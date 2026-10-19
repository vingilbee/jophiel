"""Focused probe: does tension alone change the frozen teacher's reading, feelings and reply?

Identical inputs except one mood value: each turn's own bundle (message, history, memories, notes,
previous labels, rhythm, and every other mood value) is run with tension low and tension high.
Messages come in two classes, blunt (the teacher read them as cold or hostile toward her, or as a
complaint/tease about her) and neutral (friendly or neutral, no complaint, tease or correction), so
"tense makes blunt remarks land harder" can be told apart from "tense makes everything land harder".
Offline measurement only.
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

import numpy as np

from .ablation import SAMPLES
from .contracts import Bundle, Message, decide_targets, understand_targets
from .intention import IntentionTeacher
from .teacher import View

LOW, HIGH = 0.1, 0.65
CUES = ('hurt', 'annoyed', 'unsure', 'happy', 'amused', 'good_for_her', 'warm_toward_her', 'dial_playfulness',
        'dial_warmth', 'dial_length', 'play_move', 'asks')


def _classify(turn: dict[str, Any]) -> str | None:
    samples = [a[-1] for a in turn['samples'] if a[-1].get('accepted')]
    if not samples:
        return None
    blunt = sum(1 for s in samples
                if s['understand']['appraisal']['warm_toward_her'] in ('bad', 'very_bad')
                or s['understand']['tone'] in ('cold',)
                or ((s['understand']['acts']['complaining'] or s['understand']['acts']['teasing'])
                    and s['understand']['about_her'] in ('strong', 'very')))
    neutral = sum(1 for s in samples
                  if s['understand']['tone'] in ('friendly', 'neutral')
                  and s['understand']['appraisal']['warm_toward_her'] in ('neutral', 'good', 'very_good')
                  and not any(s['understand']['acts'][a] for a in ('complaining', 'teasing', 'correcting_her')))
    if blunt * 2 > len(samples):
        return 'blunt'
    if neutral == len(samples):
        return 'neutral'
    return None


def pick(paths: list[Path], per_class: int, seed: int) -> list[tuple[str, dict[str, Any], list[Message]]]:
    pools: dict[str, list] = {'blunt': [], 'neutral': []}
    for path in paths:
        record = json.loads(path.read_text())
        history: list[Message] = []
        for t in record['turns']:
            kind = _classify(t)
            if kind and t['bundle'].get('typed_memories'):
                pools[kind].append((kind, t['bundle'], list(history)))
            history.append(Message('user', t['bundle']['message'], t['bundle']['turn']))
            if t.get('reply') is not None:
                history.append(Message('columbina', t['reply'], t['bundle']['turn'] + 1))
    rng = random.Random(seed)
    chosen = []
    for kind in ('blunt', 'neutral'):
        rng.shuffle(pools[kind])
        chosen += pools[kind][:per_class]
    return chosen


def _vector(sample) -> list[float]:
    u, d = understand_targets(sample.understand), decide_targets(sample.decide)
    feelings = dict(zip(('happy', 'amused', 'curious', 'touched', 'unsure', 'hurt', 'annoyed', 'tired'), u['feelings']))
    return [feelings['hurt'], feelings['annoyed'], feelings['unsure'], feelings['happy'], feelings['amused'],
            u['appraisal'][0], u['appraisal'][1], d['dials'][2], d['dials'][1], d['dials'][0],
            float('play' in (sample.decide['main'], sample.decide['second'])), float('?' in sample.extra['reply'])]


async def run(teacher: IntentionTeacher, items, out: Path) -> dict[str, Any]:
    async def one(index: int, kind: str, bundle: dict[str, Any], history: list[Message]) -> dict[str, Any]:
        result = dict(index=index, kind=kind, message=bundle['message'])
        for name, value in (('low', LOW), ('high', HIGH)):
            b = Bundle.from_dict(bundle | {'mood': bundle['mood'] | {'tension': value}})
            samples = await teacher.samples(View(teacher.page, b, history, full=True), SAMPLES)
            result[name] = dict(vectors=[_vector(s) for s in samples], replies=[s.extra['reply'] for s in samples],
                                samples=[s.to_dict() for s in samples])
        return result

    results = await asyncio.gather(*(one(i, *item) for i, item in enumerate(items)))
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return summarise(results)


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for kind in ('blunt', 'neutral'):
        rows = [r for r in results if r['kind'] == kind and r['low']['vectors'] and r['high']['vectors']]
        if not rows:
            continue
        deltas = np.array([np.mean(r['high']['vectors'], 0) - np.mean(r['low']['vectors'], 0) for r in rows])
        summary[kind] = dict(
            turns=len(rows),
            mean_shift_high_minus_low={c: round(float(deltas[:, i].mean()), 3) for i, c in enumerate(CUES)},
            turns_up={c: int((deltas[:, i] > 0.05).sum()) for i, c in enumerate(CUES)},
            turns_down={c: int((deltas[:, i] < -0.05).sum()) for i, c in enumerate(CUES)})
    return summary
