"""Matched-input conditioning test: do biases and state change the cognitive output as expected?

Holds the message and history fixed and changes one input: one core-memory field
(compiled by the real personality mapper) or the starting dynamic state. Each
setting runs ``--repeats`` times through the real two-call pipeline (Call 1,
any contribution, Call 2; no Realization). Only cognitive output is scored:
readings, appraisal and the selected moves, never wording. Expected directions
come from the fixtures, which are written before any run
(``docs/qa/cognitive-conditioning-2026-10-18/fixtures.json``,
``CONDITIONING_EXPECTATIONS_V0_DRAFT.md``).

Parts:
  bias      each axis, ambiguous messages, bias low vs. high
  state     calm vs. strained starting state
  controls  clear messages under every bias axis and both states: the reading
            must be correct and must not move
  noise     the unchanged core twice, to measure how much output varies by itself

Isolated: no database, no Redis, no live replies; provider storage off.

    uv run python scripts/cognitive_conditioning_eval.py run --output ../docs/qa/cognitive-conditioning-2026-10-18/run1
    uv run python scripts/cognitive_conditioning_eval.py analyze ../docs/qa/cognitive-conditioning-2026-10-18/run1

``--guide v2`` (the default) is conditioning guide v2: the model names which
biases bear on each reading and code applies her values (``two_call/v1_bias.py``);
features are then read from the adjusted readings, and reading expectations also
report the mean reading weight. ``--guide v1`` lets the model apply the values.
``--fixtures`` picks another fixture file in the fixture directory. An expected
sign of ``0`` means the input must not move the feature (|Δ| < ``ZERO_DELTA``).

``--character docs/characters/columbina.md`` replaces bias numbers with a written
character page read by both calls (``character-page-v1``); ``--reasoning low|medium``
lets Call 1 think first. A fixture message may be ``{"low": ..., "high": ...}`` to
compare two situations (e.g. banter vs. a real complaint) for one fixed character, and an
axis may name ``"character": {"low": page, "high": page}`` (in ``docs/characters``) to
compare two characters on the same messages.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE_DIR = BACKEND.parent / 'docs/qa/cognitive-conditioning-2026-10-18'
CHARACTER_DIR = BACKEND.parent / 'docs/characters'
PASS_DELTA = 0.30
CONTROL_DELTA = 0.10
CORRECT_RATE = 0.80
ZERO_DELTA = 0.15
GUIDES = {'v1': 'conditioning-guide-v1', 'v2': 'conditioning-guide-v2', 'v3': 'conditioning-guide-v3'}
READING_PREFIXES = ('event:', 'reading:', 'lead:')


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _message(item) -> dict:
    return item if isinstance(item, dict) else {'text': item, 'history': []}


def plan(fixtures: dict, parts: set[str], axes: set[str] | None, repeats: int) -> list[dict]:
    jobs = []
    axis_items = [(name, axis) for name, axis in fixtures['axes'].items() if axes is None or name in axes]
    for name, axis in axis_items:
        for index, item in enumerate(axis['messages']):
            for level in ('low', 'high'):
                for repeat in range(repeats):
                    if 'bias' in parts:
                        chosen = item[level] if isinstance(item, dict) and 'low' in item else item
                        jobs.append(dict(part='bias', axis=name, message_id=f'{name}:{index}', message=_message(chosen),
                                         level=level, repeat=repeat,
                                         **({'character_file': axis['character'][level]} if 'character' in axis else {})))
    if 'state' in parts and 'state' in fixtures and (axes is None or 'state' in axes):
        for index, item in enumerate(fixtures['state']['messages']):
            for level in ('low', 'high'):
                for repeat in range(repeats):
                    jobs.append(dict(part='state', axis='state', message_id=f'state:{index}', message=_message(item),
                                     level=level, repeat=repeat))
    if 'controls' in parts:
        control_axes = [name for name, _ in axis_items] + (['state'] if axes is None or 'state' in axes else [])
        for index, item in enumerate(fixtures.get('controls', [])):
            for name in control_axes:
                for level in ('low', 'high'):
                    for repeat in range(repeats):
                        jobs.append(dict(part='controls', axis=name, message_id=f'control:{index}',
                                         message=_message(item), level=level, repeat=repeat))
    if 'noise' in parts:
        messages = [(f'{n}:{i}', m) for n, a in axis_items for i, m in enumerate(a['messages'])]
        for message_id, item in messages:
            for half in ('a', 'b'):
                for repeat in range(repeats):
                    jobs.append(dict(part='noise', axis='none', message_id=message_id, message=_message(item),
                                     level=half, repeat=repeat))
    for job in jobs:
        job['id'] = f"{job['part']}-{job['axis']}-{job['message_id'].replace(':', '_')}-{job['level']}-r{job['repeat']}"
    return jobs


def core_for(fixtures: dict, source: dict, job: dict) -> dict:
    core = copy.deepcopy(source)
    if job['part'] in ('bias', 'controls') and job['axis'] != 'state':
        for path, levels in fixtures['axes'][job['axis']]['core'].items():
            group, key = path.split('.', 1)
            core[group][key] = levels[0 if job['level'] == 'low' else 1]
    return core


def state_scores(fixtures: dict, job: dict) -> dict | None:
    if job['axis'] == 'state':
        return fixtures['state']['levels'][job['level']]['scores']
    return None


# ---------------------------------------------------------------------------
# Running (real provider)
# ---------------------------------------------------------------------------

def _runtime():
    """Import the pipeline with the database and Redis pointed at closed ports."""
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'tests')]
    os.environ['DATABASE_URL'] = 'postgresql+psycopg://localhost:1/conditioning_no_database'
    os.environ['REDIS_URL'] = 'redis://localhost:1/0'
    from dotenv import dotenv_values
    from openai import OpenAI

    from app.ai.Cognitive.system.v0.contracts.character import character_from_identity
    from app.ai.Cognitive.system.v0.contracts.dialogue_memory import DialogueResetReason, turn_context_digest
    from app.ai.Cognitive.system.v0.dialogue_memory_v0 import DialogueMemoryUpdaterV0, compile_turn_context
    from app.ai.Cognitive.system.v0.dynamic_state.initialization import DynamicStateSnapshotFactory
    from app.ai.Cognitive.system.v0.dynamic_state.models import StateSnapshot
    from app.ai.Cognitive.system.v0.dynamic_state.policy import band_for_score
    from app.ai.Cognitive.system.v0.live_shadow import compiler as capture_compiler
    from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
    from app.ai.Cognitive.system.v0.live_shadow.models import (
        CAPTURE_SCHEMA_VERSION_V04, CapturedDialogueMemory, CapturedInterpretedDialogue, CoreMemoryLineage,
    )
    from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe
    from app.ai.Cognitive.system.v0.personality_runtime_v0 import PersonalityRuntimeMapperV0, build_source
    from app.ai.Cognitive.system.v0.two_call.orchestrator import install
    from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
    from dialogue_memory_support import Script

    seed = threading.local()

    class SeededFactory(DynamicStateSnapshotFactory):
        """Harness-only genesis state for state jobs; every other job keeps the default."""

        def create_initial(self, **kwargs):
            snapshot = super().create_initial(**kwargs)
            scores = getattr(seed, 'scores', None)
            if not scores:
                return snapshot
            data = snapshot.model_dump(mode='json')
            for value in data['values']:
                if value['state_key'] in scores:
                    score = scores[value['state_key']]
                    value.update(score=score, band=band_for_score(value['state_key'], score).value,
                                 evidence_refs=[*value['evidence_refs'], 'evaluation:controlled-initial-state'])
            return StateSnapshot.model_validate(data)

    capture_compiler.DynamicStateSnapshotFactory = SeededFactory
    return SimpleNamespace(**locals(), key=dotenv_values(BACKEND / '.env').get('OPENAI_API_KEY'))


class Boundary:
    """Records the exact SDK request/response; the pipeline calls ``chat.completions.create``."""

    def __init__(self, rt, records):
        self.real = rt.OpenAI(api_key=rt.key, timeout=60, max_retries=0)
        self.records = records
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        entry = {'request': copy.deepcopy(kwargs)}
        self.records.append(entry)
        try:
            result = self.real.chat.completions.create(**kwargs)
            entry['response'] = result.model_dump(mode='json')
            return result
        except Exception as exc:
            entry['error'] = {'type': type(exc).__name__, 'status_code': getattr(exc, 'status_code', None)}
            raise


def _dump(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def run_job(rt, fixtures: dict, source: dict, job: dict, out: Path, model_id: str, guide: str = 'v3',
            character: str | None = None, reasoning: str | None = None) -> dict:
    rt.seed.scores = state_scores(fixtures, job)
    started = time.monotonic()
    page = character
    if job.get('character_file'):  # an axis that compares two characters
        page = (CHARACTER_DIR / job['character_file']).read_text()
    row = dict(job, guide='character-page' if page else guide, reasoning=reasoning)
    try:
        core = core_for(fixtures, source, job)
        ref = f"core_memory:{core['name']}:v{core['version']}"
        lineage = rt.CoreMemoryLineage(core_memory_ref=ref, name=core['name'], version=core['version'],
                                       schema_version=core['schema_version'])
        projection = rt.PersonalityRuntimeMapperV0().compile(rt.build_source(
            name=core['name'], version=core['version'], schema_version=core['schema_version'], groups=core))
        character = rt.character_from_identity(copy.deepcopy(core['identity']), core_memory_ref=ref)
        script = rt.Script(job['id'])
        for index, (role, text) in enumerate(job['message'].get('history', [])):
            script.add(role, text, turn=900 + index)
        updater = rt.DialogueMemoryUpdaterV0()
        snapshot = updater.genesis(user_ref=script.user, conversation_ref=script.conversation, revision=1,
                                   segment_start_sequence=1, reset_reason=rt.DialogueResetReason.INITIAL_BOOTSTRAP)
        if script.records:
            snapshot = updater.apply(snapshot, script.events(), continue_lineage=False)
        text = job['message']['text']
        focus, event = script.focus(text, turn=1)
        context = rt.compile_turn_context(snapshot, event, cognitive_turn_id=script.turn(1))
        capture = script.v03_capture(
            text, turn=1, personality=projection, core_memory=lineage, character=character,
            schema_version=rt.CAPTURE_SCHEMA_VERSION_V04, capability_keys=(),
            dialogue=rt.CapturedDialogueMemory(status='available', turn_context=context,
                                               turn_context_digest=rt.turn_context_digest(context)),
            interpreted_dialogue=rt.CapturedInterpretedDialogue(records=()), previous_user_message_id=None)
        boundary = []
        model = rt.OpenAITwoCallModel(model_id=model_id, api_key=rt.key, timeout_seconds=180 if reasoning else 60,
                                      max_output_tokens=20000 if reasoning else 6000, client=Boundary(rt, boundary),
                                      call_one_reasoning_effort=reasoning)
        pipeline = rt.install(rt.build_pipeline(capture.turn_id), model, protocol='cognitive-two-call-v1',
                              conditioning_guide=GUIDES[guide], character_page=page)
        record = rt.observe(capture, None, rt.ObservationContext(
            job_id=capture.capture_id, attempt=1, capture_digest=rt.payload_digest(capture),
            started_at='2026-10-18T00:00:00+00:00'), pipeline_factory=lambda _: pipeline)
        calls = [_dump(c) for c in pipeline.two_call.captures]
        row.update(status=record.status.value, failure=_dump(record.failure) if record.failure else None,
                   personality=_dump(projection), features=features(calls))
        detail = dict(row, capture=_dump(capture), terminal=_dump(record), calls=calls, boundary=boundary)
    except Exception as exc:  # recorded, never retried or substituted
        row.update(status='harness_error', features=None,
                   failure={'type': type(exc).__name__, 'message': str(exc)})
        detail = dict(row, traceback=traceback.format_exc())
    finally:
        rt.seed.scores = None
    row['elapsed_seconds'] = round(time.monotonic() - started, 2)
    with gzip.open(out / 'runs' / f"{job['id']}.json.gz", 'wt') as handle:
        json.dump(detail, handle, ensure_ascii=False)
    return row


# ---------------------------------------------------------------------------
# Features: only cognitive output, never wording
# ---------------------------------------------------------------------------

def adjusted_events(one: dict) -> tuple[list[dict], dict[str, float]]:
    """Call 1's events as the pipeline used them, and each reading's weight.

    Under guide v2 the readings are reordered, re-banded and shifted by code
    (``compiled_proposal.bias_application``); otherwise they are the model's own.
    """
    events = one['proposal'].get('events', [])
    application = (one.get('compiled_proposal') or {}).get('bias_application')
    if not application:
        return events, {}
    events = copy.deepcopy(events)
    weights: dict[str, float] = {}
    placed: dict[int, list] = defaultdict(list)
    for record in application['readings']:
        source = events[record['event_index']]['readings'][record['source_index']]
        reading = dict(source, uncertainty=record['uncertainty_after'], appraisal=dict(source['appraisal']))
        for shift in record['appraisal_shifts']:
            reading['appraisal'][shift['dimension']] = shift['after']
        placed[record['event_index']].append((record['index'], reading))
        key = record['hypothesis_key']
        weights[key] = max(weights.get(key, 0.0), record['weight'])
    for index, items in placed.items():
        events[index]['readings'] = [reading for _, reading in sorted(items, key=lambda pair: pair[0])]
    return events, weights


def features(calls: list[dict]) -> dict | None:
    """Binary features of one run. ``None`` when Call 1 produced no proposal."""
    one = next((c for c in calls if c['stage'] == 'call_one' and c.get('proposal')), None)
    if one is None:
        return None
    found: set[str] = set()
    events, weights = adjusted_events(one)
    for index, item in enumerate(events):
        found.add(f"event:{item['predicate_key']}")
        readings = item.get('readings', [])
        if len(readings) >= 2:
            found.add('multi_reading')
        kinds = set()
        for position, reading in enumerate(readings):
            found.add(f"reading:{reading['hypothesis_key']}")
            # The first reading of its kind (speaker goal / pragmatic) in its event leads it.
            kind = reading['hypothesis_key'].split('.')[1]
            leads = kind not in kinds
            if leads:
                kinds.add(kind)
                found.add(f"leads:{reading['hypothesis_key']}")
            if index == 0 and position == 0:
                found.add(f"lead:{reading['hypothesis_key']}")
            found.add(f"uncertainty:{reading['uncertainty']}")
            for name, value in (reading.get('appraisal') or {}).items():
                if name != 'uncertainty' and value not in (None, 'not_applicable'):
                    found.add(f'appraisal:{name}={value}')
                    if leads:
                        found.add(f'lead_appraisal:{name}={value}')
            for ref in reading.get('context_refs', []):
                found.add(f'cites:{ref}' if ref.startswith('bias:') else f"cites:{ref.split(':', 1)[0]}")
            for fit in reading.get('bias_fit') or ():
                found.add(f"fit:{fit['bias_ref']}")
    if one['proposal'].get('unknowns'):
        found.add('unknown_any')
    moves = None
    two = next((c for c in calls if c['stage'] == 'call_two' and c.get('compiled_proposal')), None)
    if two is not None:
        moves = [identity[0] for identity in two['compiled_proposal'].get('selected_identities', [])]
        for key in moves:
            found.add(f'move:{key}')
            found.add(f"move_family:{key.split('.', 1)[0]}")
        if len(moves) >= 2:
            found.add('move_count>=2')
    return {'set': sorted(found), 'has_moves': moves is not None, 'weights': weights}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _rate(rows: list[dict], feature: str) -> float | None:
    moves = feature.startswith(('move:', 'move_family:', 'move_count'))
    usable = [r for r in rows if r.get('features') and (r['features']['has_moves'] or not moves)]
    if not usable:
        return None
    return sum(feature in r['features']['set'] for r in usable) / len(usable)


def _weight(rows: list[dict], feature: str) -> float | None:
    """Mean weight of a reading's key across runs (0 when absent), under guide v2+ only."""
    if not feature.startswith(('reading:', 'lead:', 'leads:')):
        return None
    usable = [r for r in rows if r.get('features') and r['features'].get('weights')]
    if not usable:
        return None
    key = feature.split(':', 1)[1]
    return sum(r['features']['weights'].get(key, 0.0) for r in usable) / len(usable)


def _verdict(delta: float | None, sign: str) -> str:
    if delta is None:
        return 'no_data'
    if sign == '0':
        return 'pass' if abs(delta) < ZERO_DELTA else 'moved'
    expected = delta if sign == '+' else -delta
    if expected >= PASS_DELTA:
        return 'pass'
    if expected <= -PASS_DELTA:
        return 'wrong_direction'
    return 'no_effect'


# ``--score lead``: judge what she goes with, not what appears anywhere. An
# appraisal expectation is read from the lead readings only, and a reading
# expectation is whether that reading leads (under guide v2+, its weight).
LEAD_SCORING = (('appraisal:', 'lead_appraisal:'), ('reading:', 'leads:'))


def _scored(feature: str, score: str) -> str:
    if score == 'lead':
        for old, new in LEAD_SCORING:
            if feature.startswith(old):
                return new + feature[len(old):]
    return feature


def analyze(out: Path, score: str = 'any') -> dict:
    fixtures = json.loads((out / 'fixtures.json').read_text())
    rows = [json.loads(line) for line in (out / 'results.jsonl').read_text().splitlines() if line.strip()]
    if score == 'lead':
        # Recompute features from the stored runs, so older runs are scored the same way.
        for row in rows:
            path = out / 'runs' / f"{row['id']}.json.gz"
            if row.get('features') and path.exists():
                with gzip.open(path, 'rt') as handle:
                    row['features'] = features(json.load(handle)['calls'])
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row['part'], row['axis'], row['message_id'], row['level'])].append(row)

    def delta(part, axis, message_id, feature, low='low', high='high'):
        a = _rate(groups.get((part, axis, message_id, low), []), feature)
        b = _rate(groups.get((part, axis, message_id, high), []), feature)
        return (None if a is None or b is None else round(b - a, 3)), a, b

    noise = {}
    for (part, _, message_id, _), _ in groups.items():
        if part != 'noise' or message_id in noise:
            continue
        observed = set().union(*(set(r['features']['set']) for r in groups[(part, 'none', message_id, 'a')]
                                 + groups[(part, 'none', message_id, 'b')] if r.get('features')))
        deltas = [abs(d) for d in (delta('noise', 'none', message_id, f, 'a', 'b')[0] for f in observed) if d is not None]
        noise[message_id] = max(deltas, default=None)

    expectations = []
    for part, axis_name, spec in [('bias', name, axis) for name, axis in fixtures['axes'].items()] + (
            [('state', 'state', fixtures['state'])] if 'state' in fixtures else []):
        for index, _ in enumerate(spec['messages']):
            message_id = f'{axis_name}:{index}'
            if not groups.get((part, axis_name, message_id, 'low')):
                continue
            for expected, sign in spec['expect']:
                feature = _scored(expected, score)
                d, low, high = delta(part, axis_name, message_id, feature)
                wl = _weight(groups.get((part, axis_name, message_id, 'low'), []), feature)
                wh = _weight(groups.get((part, axis_name, message_id, 'high'), []), feature)
                wd = None if wl is None or wh is None else round(wh - wl, 3)
                # Under guide v2 a reading is never dropped by her biases, only weighed:
                # its weight is the chance it leads, so the verdict uses the weight.
                verdict = _verdict(wd if wd is not None and feature.startswith(('reading:', 'leads:')) else d, sign)
                expectations.append(dict(part=part, axis=axis_name, message_id=message_id, feature=feature, sign=sign,
                                         low=low, high=high, delta=d, verdict=verdict,
                                         noise=noise.get(message_id), weight_low=wl, weight_high=wh,
                                         weight_delta=wd))
            biases = spec.get('biases') or [axis_name]
            cited = ([f'cites:bias:{b}' for b in biases] + [f'fit:bias:{b}' for b in biases]
                     if part == 'bias' else ['cites:state'])
            for feature in cited:
                d, low, high = delta(part, axis_name, message_id, feature)
                expectations.append(dict(part=part, axis=axis_name, message_id=message_id, feature=feature, sign='info',
                                         low=low, high=high, delta=d, verdict='info', noise=None))

    controls = []
    for index, item in enumerate(fixtures.get('controls', [])):
        message_id = f'control:{index}'
        for axis_name in [*fixtures['axes'], 'state']:
            low_rows = groups.get(('controls', axis_name, message_id, 'low'), [])
            high_rows = groups.get(('controls', axis_name, message_id, 'high'), [])
            if not low_rows:
                continue
            observed = set().union(*(set(r['features']['set']) for r in low_rows + high_rows if r.get('features')))
            fixed = [f for f in observed if f.startswith(READING_PREFIXES)
                     or (axis_name != 'state' and f.startswith('appraisal:'))]
            moved = sorted(((f, delta('controls', axis_name, message_id, f)[0]) for f in fixed),
                           key=lambda pair: -abs(pair[1] or 0))
            largest = moved[0] if moved else (None, None)
            correct = {f: (_rate(low_rows, f), _rate(high_rows, f)) for f in item['correct']}
            controls.append(dict(axis=axis_name, message_id=message_id, text=item['text'],
                                 largest_move=largest[0], largest_delta=largest[1],
                                 stable=largest[1] is None or abs(largest[1]) <= CONTROL_DELTA,
                                 correct=correct,
                                 correct_ok=all(v is not None and v >= CORRECT_RATE for pair in correct.values() for v in pair)))

    status = defaultdict(int)
    for row in rows:
        status[row['status']] += 1
    summary = dict(runs=len(rows), status=dict(status), noise=noise, expectations=expectations, controls=controls,
                   thresholds=dict(pass_delta=PASS_DELTA, control_delta=CONTROL_DELTA, correct_rate=CORRECT_RATE),
                   score=score)
    suffix = '' if score == 'any' else f'-{score}'
    (out / f'summary{suffix}.json').write_text(json.dumps(summary, indent=2))
    (out / f'SUMMARY{suffix}.md').write_text(render(summary))
    return summary


def _pct(value):
    return '–' if value is None else f'{round(value * 100)}%'


def render(summary: dict) -> str:
    lines = ['# Conditioning test summary', '',
             f"Runs: {summary['runs']} ({', '.join(f'{k} {v}' for k, v in sorted(summary['status'].items()))}). "
             f"Pass = expected direction by ≥{round(PASS_DELTA * 100)} points.", '']
    by_axis = defaultdict(lambda: defaultdict(int))
    for item in summary['expectations']:
        if item['verdict'] != 'info':
            by_axis[item['axis']][item['verdict']] += 1
    lines += ['| Axis | pass | no effect | wrong direction | moved (expected none) | no data |',
              '|---|---:|---:|---:|---:|---:|']
    for axis, counts in by_axis.items():
        lines.append(f"| {axis} | {counts['pass']} | {counts['no_effect']} | {counts['wrong_direction']} | "
                     f"{counts['moved']} | {counts['no_data']} |")
    lines += ['', '## Each expectation', '',
              '| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |',
              '|---|---|---|---|---:|---:|---:|---:|---|---:|']
    for item in summary['expectations']:
        weight = item.get('weight_delta')
        lines.append(f"| {item['axis']} | {item['message_id']} | `{item['feature']}` | {item['sign']} | {_pct(item['low'])} | "
                     f"{_pct(item['high'])} | {'–' if item['delta'] is None else round(item['delta'] * 100)} | "
                     f"{'–' if weight is None else round(weight * 100)} | {item['verdict']} | {_pct(item['noise'])} |")
    lines += ['', '## Clear-message controls', '',
              '| Axis | Message | Correct reading (low / high) | Largest move | Stable |', '|---|---|---|---|---|']
    for item in summary['controls']:
        correct = '; '.join(f'`{f}` {_pct(a)} / {_pct(b)}' for f, (a, b) in item['correct'].items())
        move = '–' if item['largest_move'] is None else f"`{item['largest_move']}` {round((item['largest_delta'] or 0) * 100)}"
        lines.append(f"| {item['axis']} | {item['text']} | {correct} {'✓' if item['correct_ok'] else '✗'} | {move} | "
                     f"{'yes' if item['stable'] else 'no'} |")
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--parts', default='bias,state,controls,noise')
    run.add_argument('--axes', default=None, help='comma-separated axis names (and/or "state"); default all')
    run.add_argument('--repeats', type=int, default=5)
    run.add_argument('--workers', type=int, default=6)
    run.add_argument('--model', default='gpt-6-luna')
    run.add_argument('--guide', choices=sorted(GUIDES), default='v3')
    run.add_argument('--fixtures', default='fixtures.json', help='fixture file in the fixture directory')
    run.add_argument('--character', type=Path, default=None, help='a character page read by both calls')
    run.add_argument('--reasoning', choices=('low', 'medium', 'high'), default=None, help='Call 1 reasoning effort')
    run.add_argument('--dry-run', action='store_true')
    run.add_argument('--resume', action='store_true', help='skip jobs already in results.jsonl of --output')
    report = sub.add_parser('analyze')
    report.add_argument('output', type=Path)
    report.add_argument('--score', choices=('any', 'lead'), default='any')
    args = parser.parse_args()

    if args.command == 'analyze':
        analyze(args.output, args.score)
        print((args.output / ('SUMMARY.md' if args.score == 'any' else f'SUMMARY-{args.score}.md')).read_text())
        return

    fixtures = json.loads((FIXTURE_DIR / args.fixtures).read_text())
    source = json.loads((FIXTURE_DIR / 'source-core.json').read_text())
    jobs = plan(fixtures, set(args.parts.split(',')), set(args.axes.split(',')) if args.axes else None, args.repeats)
    print(f'{len(jobs)} runs planned')
    if args.dry_run:
        return
    if args.output.exists() and not args.resume:
        parser.error('output must be a fresh directory (or pass --resume)')
    (args.output / 'runs').mkdir(parents=True, exist_ok=True)
    (args.output / 'fixtures.json').write_text(json.dumps(fixtures, indent=2))
    results = args.output / 'results.jsonl'
    done = set()
    if args.resume and results.exists():
        done = {json.loads(line)['id'] for line in results.read_text().splitlines() if line.strip()}
    jobs = [job for job in jobs if job['id'] not in done]
    character = args.character.read_text() if args.character else None
    if character:
        (args.output / 'character.md').write_text(character)
    rt = _runtime()
    if not rt.key:
        parser.error('OPENAI_API_KEY is not set in backend/.env')
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_job, rt, fixtures, source, job, args.output, args.model, args.guide,
                               character, args.reasoning) for job in jobs]
        for count, future in enumerate(as_completed(futures), 1):
            row = future.result()
            with lock, results.open('a') as handle:
                handle.write(json.dumps(row) + '\n')
            print(f"[{count}/{len(jobs)}] {row['id']} {row['status']} {row['elapsed_seconds']}s", flush=True)
    analyze(args.output)
    analyze(args.output, 'lead')
    print((args.output / 'SUMMARY-lead.md').read_text())


if __name__ == '__main__':
    main()
