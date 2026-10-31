"""Generative student: Qwen3-1.7B (4-bit, LoRA) writes the whole decision, its text included.

Experiment, not architecture. Student 2 chooses among valid structured decisions and hands the text on; here a small
causal model writes one compact decision: the intention first, then the work part, then the communication and the
words she means to get across, so the words are written knowing what the decision does. Training targets are the
plain teacher's own decisions (no candidates), every kept sample of every training situation, on the same split as
Student 2 so the held-out numbers compare.

Training and generation run on a hosted GPU (Colab or Kaggle, `notebooks/qwen_student.ipynb`); this module only
writes the bundle the notebook reads and scores the predictions it sends back. The bundle holds rendered situations
and teacher decisions, never keys or user data.

Offline only.
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..events import NO_SECOND, SCHEMA_GUIDE, SPEAKING, WORK_FIELDS, EventTeacher, _QUIET, check
from ..llm import LLM
from . import events_follow as F
from . import events_meaning as M
from .events_selection import compat
from .events_student2 import baselines, core, evaluate, load_examples, split, teacher_majority
from .events_student import CONTRAST_TESTS, ORIGINAL_TESTS

DEFAULTS = dict(_QUIET) | dict(work='no_change', target='none', resolution='not_applicable', basis='none',
                               other_goal='none', other_goal_handling='not_applicable')
_SPEECH = ('communication', 'what_to_communicate', 'move', 'second_move', 'dials', 'about')
_REST = ('defer_item', 'reconsider_on', 'reconsider_ref', 'not_before', 'not_before_source', 'defer_expiry',
         'think_form', 'think_with', 'think_about', 'proposed_new_goal', 'does_not_fit')
# Output order. work_first (qwen-1): what she wants, what the decision does, what she says about it. words_first: what
# she wants, what she says, then the work that has to go with those words (qwen-1 said actions its work didn't do).
ORDERS = {'work_first': ('intention', *WORK_FIELDS, *_SPEECH, *_REST),
          'words_first': ('intention', *_SPEECH, *WORK_FIELDS, *_REST)}
FIELDS = ORDERS['work_first']
ALWAYS = ('intention', 'work', 'communication')

STUDENT_SYSTEM = ('You are Columbina\'s decision step. Read the situation and write her decision as one compact JSON '
                  'object: intention, the work part, communication, and what she means to get across. Leave out '
                  'fields at their default.')
FORMAT = f"""OUTPUT FORMAT
One JSON object, nothing else, keys in this order, leaving out any key whose value is its default:
{', '.join(FIELDS)}.
Defaults: {json.dumps({k: DEFAULTS[k] for k in FIELDS if k not in ALWAYS})}.
intention, work and communication are always present. References are written without brackets ("M", "G1")."""


def completion(d: dict[str, Any], order: str = 'work_first') -> str:
    out = {}
    for k in ORDERS[order]:
        v = d.get(k, DEFAULTS[k])
        if k in ALWAYS or v != DEFAULTS[k]:
            out[k] = v
    return json.dumps(out, ensure_ascii=False)


def parse(text: str) -> tuple[dict[str, Any], str]:
    """A generated decision filled out to the full field set, and what went wrong reading it ('' when nothing)."""
    start, end = text.find('{'), text.rfind('}')
    try:
        got = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return dict(DEFAULTS), 'not_json'
    if not isinstance(got, dict):
        return dict(DEFAULTS), 'not_json'
    d = dict(DEFAULTS) | {k: v for k, v in got.items() if k in DEFAULTS}
    if d['communication'] in SPEAKING and not d['dials']:
        d['dials'] = None
    d['second_move'] = d['second_move'] or NO_SECOND
    return d, ''


def export(runs: list[Path], out: Path, page_path: Path, *, seed: int = 0, order: str = 'work_first',
           follow: bool = False) -> dict[str, Any]:
    """bundle.zip for the notebook: train.jsonl (one row per kept teacher sample of a training situation),
    test.jsonl (one row per held-out example, and a slice of training examples to see fit), prompts.json.
    `follow`: leave out training samples confirmed in review as not carrying out their own intention (events_follow)."""
    out.mkdir(parents=True, exist_ok=True)
    examples, stats = load_examples(runs)
    split(examples, seed)
    review = F.load_review(runs) if follow else {}
    train_rows, test_rows = [], []
    for i, e in enumerate(examples):
        if e.group == 'train':
            kept = [(j, d) for j, d in enumerate(e.samples) if not F.left_out(review, e, d)]
            stats['follow_left_out'] += len(e.samples) - len(kept)
            train_rows += [dict(id=f'{i}', sample=j, prompt=e.text, completion=completion(d, order)) for j, d in kept]
    fit = [i for i, e in enumerate(examples) if e.group == 'train'][::10]
    for i, e in enumerate(examples):
        if e.group != 'train' or i in fit:
            test_rows.append(dict(id=f'{i}', group=e.group, prompt=e.text))
    prompts = dict(student_system=STUDENT_SYSTEM,
                   zero_shot_system=f'{STUDENT_SYSTEM}\n\n{SCHEMA_GUIDE}\n\n{FORMAT}\n\nCHARACTER PAGE\n{page_path.read_text()}')
    with zipfile.ZipFile(out / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('train.jsonl', '\n'.join(json.dumps(r, ensure_ascii=False) for r in train_rows))
        z.writestr('test.jsonl', '\n'.join(json.dumps(r, ensure_ascii=False) for r in test_rows))
        z.writestr('prompts.json', json.dumps(prompts, ensure_ascii=False, indent=1))
    info = dict(runs=[r.name for r in runs], seed=seed, order=order, follow=follow, stats=dict(stats), train_rows=len(train_rows),
                test_rows=len(test_rows), groups=dict(Counter(r['group'] for r in test_rows)),
                train_completion_chars=sorted(len(r['completion']) for r in train_rows)[len(train_rows) // 2],
                silent_by_trigger=silent_by_trigger(train_rows),
                rendering='memory-v1')   # exchange-line-v1 plus MEMORY after the new message (events.memory_lines)
    (out / 'export.json').write_text(json.dumps(info, indent=1))
    return info


def silent_by_trigger(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Share of training samples where she stays silent, per trigger: checked before training, since qwen-4 learned
    to acknowledge her own events from 294 added rows that were all user messages and 1% silent."""
    n, quiet = Counter(), Counter()
    for r in rows:
        trigger = r['prompt'].split('\n', 1)[0].removeprefix('TRIGGER: ')
        n[trigger] += 1
        quiet[trigger] += json.loads(r['completion'])['communication'] == 'silent'
    return {k: f'{quiet[k]}/{n[k]} ({quiet[k] / n[k]:.0%})' for k, _ in n.most_common()}


# --- Scoring ------------------------------------------------------------------------------------------

async def _speech(llm: LLM, teacher: EventTeacher, e, mine: dict[str, Any], ref: dict[str, Any],
                  other: dict[str, Any] | None) -> dict[str, Any]:
    """For a held-out example where the student speaks: its words against the teacher's (meaning units from the
    teacher's what_to_communicate), whether the words fit its own work decision, whether the mouth keeps them;
    and the same grade between two teacher samples (the ceiling)."""
    units = await M.units(llm, 'speech', e.text, ref['what_to_communicate'])
    out: dict[str, Any] = dict(same_comm=mine['communication'] == ref['communication'],
                               same_work=e.ws(mine) == e.ws(ref))
    out['vs_teacher'] = await M.grade(llm, e.text, units, mine['what_to_communicate'])
    out['compat'] = await compat(llm, e.text, mine)
    reply = await teacher.say(e.snapshot, mine)
    keep_units = await M.units(llm, 'speech', e.text, mine['what_to_communicate'])
    out['mouth'] = dict(reply=reply, **await M.grade(llm, e.text, keep_units, reply))
    if other is not None:
        out['teacher_vs_teacher'] = await M.grade(llm, e.text, units, other['what_to_communicate'])
    return out


def _rate(xs: list[bool]) -> str:
    return f'{sum(xs)}/{len(xs)}'


def slices(examples: list, preds: list[dict[str, Any]], idx: list[int], contrast: list[int]) -> dict[str, Any]:
    """What a single held-out number hides (2026-10-29). `per_situation`: talking and the joint decision right,
    averaged per situation (a held-out situation's thought variants share one answer and otherwise count up to 7
    times). `own_result_after_reply`: her own thought, work outcome, deferred item or proposal arriving when she has
    already spoken since the user's last line, where the teacher keeps quiet. Both on the main held-out set (`idx`),
    so models trained on different runs compare. `pairs`: contrast situations held out with both sides (`contrast`,
    which differ between data sets), right on speaking vs quiet for both."""
    from collections import defaultdict

    from ..events import EXCHANGE
    quiet = lambda c: c in ('silent', 'defer')  # noqa: E731
    per_sid: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    after = Counter()
    sides: dict[str, dict[str, bool]] = defaultdict(dict)
    for i in idx:
        e, p, t = examples[i], preds[i], teacher_majority(examples[i])
        per_sid[e.sid].append((p['communication'] == t['communication'],
                               (p['communication'], p['work'], p['target']) == (t['communication'], t['work'], t['target'])))
        if not e.text.startswith('TRIGGER: user message') and EXCHANGE[0] in e.text:
            after['n'] += 1
            after['teacher_quiet'] += quiet(t['communication'])
            after['student_speaks_where_teacher_quiet'] += quiet(t['communication']) and not quiet(p['communication'])
    for i in contrast:
        e, p, t = examples[i], preds[i], teacher_majority(examples[i])
        if e.variant in ('base', 'twin'):
            sides[e.sid][e.variant] = quiet(p['communication']) == quiet(t['communication'])
    both = [v for v in sides.values() if len(v) == 2]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return dict(
        per_situation=dict(situations=len(per_sid), talk_right=mean([mean([a for a, _ in v]) for v in per_sid.values()]),
                           joint_right=mean([mean([b for _, b in v]) for v in per_sid.values()])),
        own_result_after_reply=dict(after),
        pairs=dict(n=len(both), both_sides_right=sum(all(v.values()) for v in both)))


async def score(runs: list[Path], preds_path: Path, out: Path, *, llm: LLM | None, page: str, seed: int = 0,
                parallel: int = 24) -> dict[str, Any]:
    """Score one predictions file (jsonl rows {id, output, seconds}) like Student 2, then grade the speech of
    held-out examples where the student speaks (skipped without llm)."""
    examples, _ = load_examples(runs)
    split(examples, seed)
    rows = [json.loads(line) for line in preds_path.read_text().splitlines() if line.strip()]
    preds, errors = {}, Counter()
    for r in rows:
        d, err = parse(r['output'])
        errors[err or 'ok'] += 1
        preds[int(r['id'])] = d
    idx = sorted(preds)
    full = [preds.get(i, dict(DEFAULTS)) for i in range(len(examples))]
    held = [i for i in idx if examples[i].group in ORIGINAL_TESTS]
    fit = [i for i in idx if examples[i].group == 'train']
    train_idx = [i for i, e in enumerate(examples) if e.group == 'train']
    problems = Counter(p.split(':')[0] for i in held for p in check(full[i], examples[i].snapshot))
    seconds = sorted(r['seconds'] for r in rows if r.get('seconds') is not None)
    result: dict[str, Any] = dict(
        rows=len(rows), parse=dict(errors), invalid_problems=dict(problems.most_common(12)),
        held_out=evaluate(examples, full, held), fit_on_training=evaluate(examples, full, fit),
        **{g: evaluate(examples, full, [i for i in idx if examples[i].group == g])
           for g in (*ORIGINAL_TESTS, *CONTRAST_TESTS) if g in ORIGINAL_TESTS or any(examples[i].group == g for i in idx)},
        baselines=baselines(examples, train_idx, held),
        seconds=dict(median=seconds[len(seconds) // 2], p90=seconds[int(len(seconds) * .9)]) if seconds else None)
    result['slices'] = slices(examples, full, held, [i for i in idx if examples[i].group in CONTRAST_TESTS])
    # Held-out examples whose reference a review marked as a test issue: each group scored again without them.
    review = F.load_review(runs)
    issues = {i for i in idx if examples[i].group != 'train'
              and any(F.verdict(review, examples[i], d) == 'test_issue' for d in examples[i].samples)}
    result['without_test_issues'] = {
        g: dict(left_out=sum(examples[i].group == g for i in issues),
                **evaluate(examples, full, [i for i in idx if examples[i].group == g and i not in issues]))
        for g in sorted({examples[i].group for i in issues})}
    if llm is not None:
        teacher = EventTeacher(llm, page)
        gate = asyncio.Semaphore(parallel)
        speaking = [i for i in held if full[i]['communication'] in SPEAKING and not check(full[i], examples[i].snapshot)]

        async def one(i):
            e = examples[i]
            ref = teacher_majority(e)
            if ref['communication'] not in SPEAKING:
                return i, dict(teacher_speaks=False)
            others = [d for d in e.samples if d is not ref and d['communication'] in SPEAKING]
            async with gate:
                try:
                    return i, dict(teacher_speaks=True, **await _speech(llm, teacher, e, full[i], ref,
                                                                       others[0] if others else None))
                except Exception as exc:  # noqa: BLE001 - one failed grade must not lose the run
                    return i, dict(error=repr(exc))
        graded = dict(await asyncio.gather(*(one(i) for i in speaking)))
        (out / 'speech.json').write_text(json.dumps(graded, indent=1, ensure_ascii=False))
        both = [g for g in graded.values() if g.get('teacher_speaks')]
        result['speech'] = dict(
            student_speaks_valid=len(speaking), teacher_also_speaks=len(both),
            errors=sum('error' in g for g in graded.values()),
            meaning_vs_teacher=_rate([g['vs_teacher']['kept_or_weakened'] for g in both]),
            meaning_vs_teacher_same_work=_rate([g['vs_teacher']['kept_or_weakened'] for g in both if g['same_work']]),
            ceiling_teacher_vs_teacher=_rate([g['teacher_vs_teacher']['kept_or_weakened'] for g in both
                                              if 'teacher_vs_teacher' in g]),
            compatible=_rate([g['compat']['compatible'] for g in graded.values() if 'compat' in g]),
            compat_issues=dict(Counter(g['compat']['issue'] for g in graded.values() if 'compat' in g)),
            mouth_keeps=_rate([g['mouth']['kept'] for g in graded.values() if 'mouth' in g]),
            mouth_decision_changing=_rate([bool(g['mouth']['changing_additions']) for g in graded.values()
                                           if 'mouth' in g]))
    target = out / f'score-{preds_path.stem}.json'
    if llm is None and target.exists() and 'speech' in (earlier := json.loads(target.read_text())):
        result['speech'] = earlier['speech']   # a structure-only rescore keeps the graded speech (model calls)
    target.write_text(json.dumps(result, indent=1))
    return result


__all__ = ['completion', 'parse', 'export', 'score', 'core']
