"""Bring decisions made under the v3 event contract to v4 without re-deciding them.

v4 adds fields a student can select (docs/STUDENT2_STUDENT_OUTPUT_DESIGN.md): `about` for speech, `think_form` /
`think_with` for thinking, a structured deferral (`reconsider_on`, `reconsider_ref`, `not_before`) and, in the
snapshot, which message lifted a restriction. For older runs a model labels them after the fact, in
`relabel_v4.json` next to the run (provenance kept); the teacher's own fields are never changed. Code then checks
every decision against the v4 contract: one whose restriction lift or assessment no longer holds is left out,
with its reason counted, never repaired.

Older snapshots have no clock, so a deferral's stated time ("after 8:30") cannot become `not_before`; its words are
kept as `time_named` and counted.

Offline only.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..events import (RECONSIDER, SPEAKING, THINK_FORMS, THINKING, _enum, _list, _obj, _str, candidates, check, load,
                      render)
from ..llm import LLM
from ..teacher import prompt

RELABEL_FILE = 'relabel_v4.json'
MODEL = 'gpt-6-luna'


def _lift_schema(refs: list[str], count: int) -> dict[str, Any]:
    msgs = [r for r in refs if r == 'M' or r.startswith('U')]
    return _obj({'restrictions': _list(_obj({'number': {'type': 'integer'}, 'ended_by': _enum([*msgs, 'none'])}))})


def _fields_schema(refs: list[str]) -> dict[str, Any]:
    return _obj({'decisions': _list(_obj({'number': {'type': 'integer'}, 'about': _list(_enum(refs)),
                                          'think_form': _enum(THINK_FORMS), 'think_with': _list(_enum(refs)),
                                          'reconsider_on': _enum(RECONSIDER),
                                          'reconsider_ref': _enum([*refs, 'none']), 'time_named': _str()}))})


def _needs_fields(s: dict[str, Any]) -> bool:
    return s['communication'] in SPEAKING or s['communication'] == 'defer' or s['work'] in THINKING


def _listing(samples: list[dict[str, Any]]) -> str:
    out = []
    for i, s in enumerate(samples, 1):
        line = f'DECISION {i}: {s["communication"]} / {s["work"]} {s["target"]}'
        if s['communication'] in SPEAKING:
            line += f'\nmeant to get across: {s["what_to_communicate"]}\nsays: {s.get("reply") or "(not spoken)"}'
        if s['work'] in THINKING:
            line += f'\nthink about: {s["think_about"]}'
        if s['communication'] == 'defer':
            line += f'\ndefers {s["defer_item"]} until: {s["reconsider_when"]} (expires {s["defer_expiry"]})'
        out.append(line)
    return '\n\n'.join(out)


def _lift_key(snapshot: dict[str, Any]) -> str:
    part = {k: snapshot[k] for k in ('messages', 'new_message', 'restrictions')}
    return hashlib.sha256(json.dumps(part, sort_keys=True).encode()).hexdigest()[:16]


async def relabel(llm: LLM, out: Path, *, parallel: int = 40) -> dict[str, Any]:
    """Labels for every accepted v3 variant of a run, kept in relabel_v4.json (resumable)."""
    path = out / RELABEL_FILE
    done: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {
        '_meta': dict(model=llm.model if hasattr(llm, 'model') else MODEL, by='ai',
                      prompts={f: hashlib.sha256(prompt(f).encode()).hexdigest()[:12]
                               for f in ('event_relabel_lift', 'event_relabel_fields')})}
    lifts: dict[str, Any] = done.setdefault('_lifts', {})
    gate = asyncio.Semaphore(parallel)

    async def lift(snapshot: dict[str, Any]) -> None:
        key = _lift_key(snapshot)
        if key in lifts or not snapshot.get('restrictions'):
            return
        lifts[key] = None   # claimed
        refs = [r for r, _ in candidates(snapshot)]
        async with gate:
            made = await llm.json('event_relabel_lift', prompt('event_relabel_lift'), render(snapshot),
                                  _lift_schema(refs, len(snapshot['restrictions'])), reasoning='low', max_tokens=1500)
        ended = {d['number']: d['ended_by'] for d in made['restrictions']}
        lifts[key] = [ended.get(i, 'none') for i in range(1, len(snapshot['restrictions']) + 1)]

    async def fields(key: str, v: dict[str, Any]) -> None:
        refs = [r for r, _ in candidates(v['snapshot'])]
        async with gate:
            made = await llm.json('event_relabel_fields', prompt('event_relabel_fields'),
                                  f'SITUATION\n{v["rendered"]}\n\n{_listing(v["samples"])}', _fields_schema(refs),
                                  reasoning='low', max_tokens=3000)
        done[key] = {str(d['number']): {k: x for k, x in d.items() if k != 'number'} for d in made['decisions']}

    records = load(out)
    todo = [(f'{r["plan"]["id"]}|{v["name"]}', v) for r in records for v in r['variants'] if v['accepted'] and v['samples']]
    await asyncio.gather(*(lift(v['snapshot']) for _, v in todo), return_exceptions=True)
    results = await asyncio.gather(*(fields(k, v) for k, v in todo
                                     if k not in done and any(_needs_fields(s) for s in v['samples'])),
                                   return_exceptions=True)
    done['_failures'] = sum(isinstance(r, Exception) for r in results)
    path.write_text(json.dumps(done, indent=1))
    return done


EMPTY_V4 = dict(about=[], reconsider_on='none', reconsider_ref='none', not_before='', not_before_source='none',
                think_form='none', think_with=[])


def upgrade(variant: dict[str, Any], labels: dict[str, Any], key: str) -> tuple[dict[str, Any], str,
                                                                                 list[dict[str, Any]]]:
    """A v3 variant as v4: the snapshot with its restriction lifts, the re-rendered text, and its samples with
    the labelled fields and the v4 contract's problems (validator-rejected v3 samples keep their problems)."""
    s = copy.deepcopy(variant['snapshot'])
    ended = (labels.get('_lifts') or {}).get(_lift_key(s)) or []
    index = {ref: int(i) for i, ref in s['user_refs'].items()}
    for r, by in zip(s.get('restrictions', []), ended, strict=False):
        r['lifted_by'] = -1 if by == 'M' else index.get(by, 0) if by != 'none' and index.get(by, 0) > r['message'] else 0
    for r in s.get('restrictions', []):
        r.setdefault('lifted_by', 0)
    s.setdefault('clock', '')
    mine = labels.get(key, {})
    samples = []
    for i, old in enumerate(variant.get('samples') or [], 1):
        d = {k: x for k, x in old.items() if k != 'reconsider_when'} | EMPTY_V4
        lab = mine.get(str(i), {})
        notes = []
        if old['communication'] in SPEAKING:
            d['about'] = list(dict.fromkeys(lab.get('about', [])))[:4]
        if old['work'] in THINKING:
            d['think_form'] = lab.get('think_form', 'none')
            d['think_with'] = [w for w in dict.fromkeys(lab.get('think_with', [])) if w != old['target']][:2]
        if old['communication'] == 'defer':
            d['reconsider_on'] = lab.get('reconsider_on', 'none')
            d['reconsider_ref'] = lab.get('reconsider_ref', 'none')
            if lab.get('time_named'):
                notes.append(f'time named but no clock: {lab["time_named"]}')
                if d['reconsider_on'] == 'time_reached':   # only a time: nothing left to wait on without a clock
                    d['reconsider_on'] = 'none'
        d['problems'] = old['problems'] or check(d, s)
        d['v3_problems'] = old['problems']
        d['relabel_notes'] = notes
        samples.append(d)
    return s, render(s), samples


def summary(runs: list[Path]) -> dict[str, Any]:
    """What the upgrade changes: decisions newly rejected by the v4 contract, by reason."""
    newly: Counter = Counter()
    total = kept = 0
    notes: Counter = Counter()
    for run in runs:
        labels = json.loads((run / RELABEL_FILE).read_text())
        for r in load(run):
            for v in r['variants']:
                if not v['accepted'] or not v['samples']:
                    continue
                _, _, samples = upgrade(v, labels, f'{r["plan"]["id"]}|{v["name"]}')
                for d in samples:
                    if d['v3_problems']:
                        continue
                    total += 1
                    kept += not d['problems']
                    for p in d['problems']:
                        newly[p.split(' [')[0].split(' which')[0][:70]] += 1
                    for n in d['relabel_notes']:
                        notes[n.split(':')[0]] += 1
    return dict(v3_valid=total, still_valid=kept, newly_rejected=dict(newly.most_common()), notes=dict(notes))
