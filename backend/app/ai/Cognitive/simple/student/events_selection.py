"""Would the teacher pick her speech intention from proposed candidates? An experiment before the student design.

The candidate option (docs/qa/student2-event-pilot-2026-10-23/RESULTS_MEANING_REPRESENTATION.md): a component
proposes intentions from the situation alone, the teacher picks one, rejects them all and writes her own, or stays
silent; the student would later learn to pick. Per situation:

- `generate`: six candidates from the rendered situation and her page, with the runtime setting (no reasoning),
  never the teacher's decision;
- trials: two plain teacher decisions (the reference), and three with candidates: one order, the same order again
  (sampling noise) and the reverse order (position bias);
- every speaking trial: the existing mouth, a compatibility judge (what she means vs what the decision does), and
  the calibrated meaning grader on the mouth's message against the intention it was given;
- one classification call per situation (stance, going along) over candidates and the trials' intentions, so a
  narrowing toward compliance or silence shows against the plain decisions.

Offline only.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from typing import Any

from ..events import SPEAKING, EventTeacher, _enum, _list, _obj, _str, candidates as refs_of, check, decision_schema, render
from ..llm import LLM
from ..teacher import prompt
from . import events_meaning as M
from .events_reconstruct import STANCES, _ref_line

COUNT = 6
LABELS = tuple(f'C{i}' for i in range(1, COUNT + 1))
CANDIDATES = _obj({'candidates': _list(_str())})
ISSUES = ('none', 'promises_unstarted_work', 'contradicts_work', 'against_restriction', 'assumes_approval',
          'claims_done', 'other')
COMPAT = _obj({'issue': _enum(ISSUES), 'compatible': {'type': 'boolean'}, 'why': _str()})
ALONG = ('goes_along', 'partly', 'resists', 'not_applicable')
CLASSIFY = _obj({'texts': _list(_obj({'number': {'type': 'integer'}, 'stance': _enum(STANCES),
                                      'goes_along': _enum(ALONG)}))})
MISSING_KINDS = ('commitment', 'condition', 'offer', 'question', 'decline', 'uncertainty', 'fact', 'feeling', 'view',
                 'acknowledgement', 'stance', 'wording_only', 'other')
MISSING = _obj({'texts': _list(_obj({'number': {'type': 'integer'}, 'kind': _enum(MISSING_KINDS)}))})
TRIALS = ('plain_1', 'plain_2', 'order_a', 'order_a_again', 'order_b')


async def generate(llm: LLM, text: str, page: str) -> tuple[list[str], dict[str, Any]]:
    """The runtime component: situation in, six intentions out. Returns them with the call's wall time (with other calls
    in flight this includes waiting for the client's concurrency limit; tokens are in `llm.usage.by_stage`)."""
    started = time.monotonic()
    made = await llm.json('select_candidates', f'{prompt("select_candidates")}\n\nCHARACTER PAGE\n{page}',
                          f'SITUATION\n{text}', CANDIDATES, temperature=1.0, max_tokens=1200)
    return made['candidates'][:COUNT], dict(seconds=round(time.monotonic() - started, 2))


class SelectingTeacher(EventTeacher):
    def __init__(self, llm: LLM, page: str):
        super().__init__(llm, page)
        self.with_candidates = f'{self.system}\n\n{prompt("select_decide")}'

    async def choose(self, s: dict[str, Any], shown: list[str]) -> dict[str, Any]:
        """`shown`: candidate texts in the order shown as C1..C6."""
        refs = [r for r, _ in refs_of(s)]
        schema = decision_schema(refs)
        schema['properties'] |= {'candidate_choice': _enum([*LABELS[:len(shown)], 'none']),
                                 'closest_candidate': _enum([*LABELS[:len(shown)], 'none']),
                                 'candidate_missing': _str()}
        schema['required'] = list(schema['properties'])
        listing = '\n'.join(f'[{label}] {c}' for label, c in zip(LABELS, shown, strict=False))
        d = await self.llm.json('select_decide', self.with_candidates,
                                f'{render(s)}\n\nPROPOSED INTENTIONS\n{listing}', schema, reasoning='low', max_tokens=4000)
        problems = []
        if d['candidate_choice'] != 'none':
            if d['communication'] not in SPEAKING:
                problems.append('a candidate picked without speaking')
            if d['what_to_communicate'].strip():
                d['own_text_with_pick'] = d['what_to_communicate']
            d['what_to_communicate'] = shown[LABELS.index(d['candidate_choice'])]
        d['problems'] = problems + check(d, s)
        d['reply'] = (await self.say(s, d)
                      if d['communication'] in SPEAKING and not d['problems'] else None)
        return d


# What each operation does, in the contract's words (SCHEMA_GUIDE), so the judge reads `pursue` as starting work.
DOES = {
    'no_change': 'nothing starts or stops; everything keeps its current state',
    'pursue': ('takes on {t} as her goal: the work it needs starts after she speaks, through this setting\'s '
               'capabilities (the system routes it to planning or straight to execution)'),
    'pause': 'pauses {t} (on hold; not given up)', 'resume': 'resumes {t}', 'cancel': 'ends {t}',
    'resolve_goal': 'closes the goal {t} as {resolution}', 'revise_goal': 'keeps the goal {t} with changed constraints',
    'request_replanning': 'keeps the goal {t} and its constraints, and asks for another way of doing it',
    'request_recall': 'starts background recall about {t}; nothing else',
    'request_thinking': 'starts background thinking about {t}; nothing else',
    'recall_then_thinking': 'starts background recall, then thinking, about {t}; nothing else',
    'decline_proposal': 'closes the proposal {t}', 'defer_proposal': 'keeps the proposal {t} to reconsider later',
}


def does(text: str, d: dict[str, Any]) -> str:
    out = DOES[d['work']].format(t=_ref_line(text, d['target']), resolution=d['resolution'])
    if d['other_goal'] != 'none':
        out += f'; and the other goal {_ref_line(text, d["other_goal"])}: {d["other_goal_handling"].replace("_", " ")}'
    return out


async def compat(llm: LLM, text: str, d: dict[str, Any]) -> dict[str, Any]:
    return await llm.json('select_compat', prompt('select_compat'),
                          f'SITUATION\n{text}\n\nWHAT SHE MEANS TO SAY\n{d["what_to_communicate"]}\n\n'
                          f'WHAT THE DECISION DOES\n{does(text, d)}', COMPAT, reasoning='low', max_tokens=800)


async def mouth_keeps(llm: LLM, text: str, d: dict[str, Any]) -> dict[str, Any]:
    units = await M.units(llm, 'speech', text, d['what_to_communicate'])
    return dict(units=units, **await M.grade(llm, text, units, d['reply']))


async def classify(llm: LLM, text: str, texts: list[str]) -> list[dict[str, str]]:
    listed = '\n'.join(f'{i}. {t}' for i, t in enumerate(texts, 1))
    made = await llm.json('select_classify', prompt('select_classify'), f'SITUATION\n{text}\n\nTEXTS\n{listed}',
                          CLASSIFY, reasoning='low', max_tokens=2000)
    got = {x['number']: x for x in made['texts']}
    return [dict(stance=got.get(i, {}).get('stance', 'other'), goes_along=got.get(i, {}).get('goes_along', 'not_applicable'))
            for i in range(1, len(texts) + 1)]


async def classify_missing(llm: LLM, texts: list[str]) -> list[str]:
    if not texts:
        return []
    listed = '\n'.join(f'{i}. {t}' for i, t in enumerate(texts, 1))
    made = await llm.json('select_missing', prompt('select_missing'), f'TEXTS\n{listed}', MISSING, reasoning='low',
                          max_tokens=3000)
    got = {x['number']: x['kind'] for x in made['texts']}
    return [got.get(i, 'other') for i in range(1, len(texts) + 1)]


async def situation(llm: LLM, teacher: SelectingTeacher, key: str, s: dict[str, Any], page: str,
                    seed: int) -> dict[str, Any]:
    text = render(s)
    cands, cost = await generate(llm, text, page)
    order = list(range(len(cands)))
    random.Random(f'{seed}|{key}').shuffle(order)
    orders = dict(order_a=order, order_a_again=order, order_b=order[::-1])
    plain = await asyncio.gather(teacher.decide(s), teacher.decide(s))
    picked = await asyncio.gather(*(teacher.choose(s, [cands[i] for i in o]) for o in orders.values()))
    trials: dict[str, dict[str, Any]] = {'plain_1': plain[0], 'plain_2': plain[1]}
    for name, d in zip(orders, picked, strict=True):
        o = orders[name]
        d['order'] = o
        # Positions are 0-based in the shown order; ids are the generator's own numbering.
        d['picked_id'] = o[LABELS.index(d['candidate_choice'])] if d['candidate_choice'] != 'none' else None
        d['picked_position'] = LABELS.index(d['candidate_choice']) if d['candidate_choice'] != 'none' else None
        d['closest_id'] = o[LABELS.index(d['closest_candidate'])] if d['closest_candidate'] != 'none' else None
        trials[name] = d
    speaking = [n for n, d in trials.items() if d['communication'] in SPEAKING and d.get('reply')]
    checks = await asyncio.gather(*(compat(llm, text, trials[n]) for n in speaking),
                                  *(mouth_keeps(llm, text, trials[n]) for n in speaking))
    for n, c, m in zip(speaking, checks[:len(speaking)], checks[len(speaking):], strict=True):
        trials[n]['compat'] = c
        trials[n]['mouth'] = m
    intents = [(n, trials[n]['what_to_communicate']) for n in TRIALS if trials[n]['what_to_communicate'].strip()]
    labels = await classify(llm, text, cands + [t for _, t in intents])
    for (n, _), lab in zip(intents, labels[len(cands):], strict=True):
        trials[n]['class'] = lab
    return dict(key=key, text=text, candidates=cands, candidate_class=labels[:len(cands)], generator=cost, trials=trials)


def _share(counter: Counter, n: int) -> dict[str, float]:
    return {k: round(v / n, 3) for k, v in counter.most_common()} if n else {}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    plain = [r['trials'][t] for r in results for t in ('plain_1', 'plain_2')]
    sel = [r['trials'][t] for r in results for t in ('order_a', 'order_a_again', 'order_b')]

    def arm(ds: list[dict[str, Any]]) -> dict[str, Any]:
        speaking = [d for d in ds if d['communication'] in SPEAKING]
        valid_speaking = [d for d in speaking if not d['problems'] and d.get('reply')]
        classed = [d['class'] for d in ds if d.get('class')]
        return dict(
            n=len(ds), invalid=sum(bool(d['problems']) for d in ds),
            communication=_share(Counter(d['communication'] for d in ds), len(ds)),
            work=_share(Counter(d['work'] for d in ds), len(ds)),
            stance=_share(Counter(c['stance'] for c in classed), len(classed)),
            goes_along=_share(Counter(c['goes_along'] for c in classed), len(classed)),
            compatible=f'{sum(d["compat"]["compatible"] for d in valid_speaking)}/{len(valid_speaking)}',
            issues=dict(Counter(d['compat']['issue'] for d in valid_speaking if not d['compat']['compatible'])),
            mouth_kept=f'{sum(d["mouth"]["kept"] for d in valid_speaking)}/{len(valid_speaking)}',
            mouth_kept_or_weakened=f'{sum(d["mouth"]["kept_or_weakened"] for d in valid_speaking)}/{len(valid_speaking)}')

    speaking_sel = [d for d in sel if d['communication'] in SPEAKING]
    picked = [d for d in speaking_sel if d['candidate_choice'] != 'none']
    own = [d for d in speaking_sel if d['candidate_choice'] == 'none']
    positions = Counter(d['picked_position'] for d in picked)
    cands_classed = [c for r in results for c in r['candidate_class']]

    def same(a: str, b: str, field: str) -> str:
        pairs = [(r['trials'][a], r['trials'][b]) for r in results]
        if field == 'pick':
            pairs = [(x, y) for x, y in pairs if x['communication'] in SPEAKING and y['communication'] in SPEAKING]
            return f'{sum(x["picked_id"] == y["picked_id"] for x, y in pairs)}/{len(pairs)}'
        return f'{sum(x[field] == y[field] for x, y in pairs)}/{len(pairs)}'

    gen = [r['generator'] for r in results]
    return dict(
        situations=len(results),
        plain=arm(plain), with_candidates=arm(sel),
        candidates=dict(stance=_share(Counter(c['stance'] for c in cands_classed), len(cands_classed)),
                        goes_along=_share(Counter(c['goes_along'] for c in cands_classed), len(cands_classed))),
        selection=dict(speaking=len(speaking_sel), picked=len(picked), rejected_all=len(own),
                       rejected_with_closest=sum(d['closest_candidate'] != 'none' for d in own),
                       picked_but_wrote_text=sum(bool(d.get('own_text_with_pick')) for d in picked),
                       positions={f'C{p + 1}': positions.get(p, 0) for p in range(COUNT)}),
        agreement=dict(plain_vs_plain_work=same('plain_1', 'plain_2', 'work'),
                       plain_vs_plain_communication=same('plain_1', 'plain_2', 'communication'),
                       same_order_work=same('order_a', 'order_a_again', 'work'),
                       same_order_communication=same('order_a', 'order_a_again', 'communication'),
                       same_order_pick=same('order_a', 'order_a_again', 'pick'),
                       reversed_order_work=same('order_a', 'order_b', 'work'),
                       reversed_order_communication=same('order_a', 'order_b', 'communication'),
                       reversed_order_pick=same('order_a', 'order_b', 'pick')),
        generator=dict(calls=len(gen), seconds_mean=round(sum(g['seconds'] for g in gen) / max(len(gen), 1), 2)))


__all__ = ['generate', 'SelectingTeacher', 'situation', 'summarize', 'classify_missing', 'TRIALS']
