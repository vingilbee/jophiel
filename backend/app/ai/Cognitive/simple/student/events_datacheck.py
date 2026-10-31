"""Is a training set ready? Checks on what the whole set teaches, run before any training. Offline, no model calls.

Why (2026-10-29): qwen-5's data passed every per-row check (valid decisions, teacher expectations met, judge flags
reviewed) and still taught the wrong habit. qwen-5 learned "this family -> speak" and "stop -> acknowledge"
because the set showed one side only: 9 of 32 "when to talk" pairs survived, one family's training rows all said
"she has not spoken" while its test rows all said "she has spoken", and 64 of 67 new "stop" rows were
acknowledgements. No per-row check could see that. These checks look at the set:

1. Pairs: per contrast family, how many planned situations became a usable pair (base and twin both accepted).
2. Expectations: of the usable pairs, how many the teacher's majority met on both sides.
3. Both sides: per family and kind of variant (base, twin, thought), every combination of trigger and exchange line
   in its held-out rows also appears in its training rows; a held-out combination never trained on whose answer
   differs from that kind's training habit must be taught by pairs elsewhere (the after-reply families), else it is
   flagged.
4. Taught by pairs: training pairs whose base and twin differ in speaking vs staying quiet, per family set.
5. Label mix: per trigger, the share of each way of talking in the training rows, against a baseline set; shifts
   over 5 points are flagged (a new set that tips one trigger toward acknowledging).
6. Teacher agreement: per trigger, the share of training situations whose 3 samples disagree on speaking at all.
7. Unique situations (2026-10-29): no two base situations of a family share NEAR_COPY or more of their conversation's
   content words, and no topic repeats within a family. A near-copy teaches a topic instead of a reason, and one
   split across training and test hands the test its answer. Twins and thought variants share their base's scenario
   on purpose and are not compared.

`check` returns the numbers and `GATES` says what passes. A failed gate means: fix the data or say why not, before
training. Passing is necessary, not sufficient; the trained student is still scored against qwen-3.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from ..events import CONTRAST_SETS, EVAL_ONLY, EXCHANGE, FAMILY_BY_ID
from .events_student2 import load_examples, split, teacher_majority

QUIET = frozenset({'silent', 'defer'})
MIN_SIDE = 3
MIN_SIDE_SITUATIONS = 2   # a gap shown by one held-out situation is listed, not gated
GATES = dict(pair_yield=0.7, expectations=0.9, label_shift=0.05, disagreement=0.3, pairs_taught_after_reply=20)
NEAR_COPY = 0.4   # word overlap (Jaccard) of two conversations; the old maker's near-copies were 0.4-0.65
_STOP = frozenset('the and you for that this with are was not but can have just what about from they them your '
                  'would could should there here then now out get like some will its all one his her she him'.split())


def _speaks(c: str) -> bool:
    return c not in QUIET


def _trigger(e: Any) -> str:
    return e.text.split('\n', 1)[0].removeprefix('TRIGGER: ')


def _line(e: Any) -> str:
    return next((x for x in EXCHANGE if x in e.text), 'none')


def _situations(run: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((run / 'situations').glob('*.json'))]


def pairs(runs: list[Path]) -> dict[str, dict[str, Any]]:
    """Per contrast family with a twin: planned, usable (both accepted), and met (teacher majority meets every
    expectation of both sides)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for run in runs:
        for r in _situations(run):
            family = FAMILY_BY_ID.get(r['plan']['family'])
            if family is None or family.set not in CONTRAST_SETS or family.twist is None or not r['plan'].get('twin'):
                continue
            c = out[family.id]
            c['planned'] += 1
            v = {x['name']: x for x in r['variants']}
            if not (v.get('base', {}).get('accepted') and v.get('twin', {}).get('accepted')):
                continue
            c['usable'] += 1
            met = all(sum(bool(test(d, v[side]['snapshot'])) for d in v[side]['samples']) * 2 > len(v[side]['samples'])
                      for side, _, test in family.expect if side in v and v[side]['samples'])
            c['met'] += met
    return {f: dict(c, yield_=round(c['usable'] / c['planned'], 2) if c['planned'] else 0.0) for f, c in out.items()}


def _kind(e: Any) -> str:
    return e.variant.split(':', 1)[0]   # base, twin or thought


def unseen_sides(train: list, held: list) -> dict[str, set[str]]:
    """3. Both sides: held-out situations whose answer the family's training never shows for that kind of variant,
    trigger and line (fewer than MIN_SIDE training rows, against that kind's habit in the family), or shows the
    other way. Compared like with like (2026-10-29): a twin is made to answer the other way from its base, so
    against the family's overall habit every twin looked untrained. Counted per situation: a held-out situation's
    thought variants share its answer and would count it up to 7 times."""
    combo: dict[tuple, Counter] = defaultdict(Counter)
    habit: dict[tuple, Counter] = defaultdict(Counter)
    for e in train:
        said = _speaks(teacher_majority(e)['communication'])
        combo[(e.family, _kind(e), _trigger(e), _line(e))][said] += 1
        habit[(e.family, _kind(e))][said] += 1
    unseen: dict[str, set] = defaultdict(set)
    for e in held:
        h = habit[(e.family, _kind(e))] or sum((c for (f, _), c in habit.items() if f == e.family), Counter())
        if e.family in EVAL_ONLY or not h:
            continue
        said, c = _speaks(teacher_majority(e)['communication']), combo[(e.family, _kind(e), _trigger(e), _line(e))]
        where = f'{e.family} | {_kind(e)} | {_trigger(e)} | {_line(e)}'
        if sum(c.values()) < MIN_SIDE:
            if said != h.most_common(1)[0][0]:
                unseen[f'{where} | rarely trained ({sum(c.values())} rows)'].add(e.sid)
        elif said != c.most_common(1)[0][0]:
            unseen[f'{where} | trained the other way'].add(e.sid)
    return unseen


def _conversation_words(r: dict[str, Any]) -> set[str] | None:
    base = next((v for v in r['variants'] if v['name'] == 'base' and v.get('accepted')), None)
    if base is None:
        return None
    w = base['world']
    text = ' '.join([m['text'] for m in w.get('messages', [])] + [w.get('new_message') or ''])
    return {x for x in re.findall(r'[a-z]{3,}', text.lower()) if x not in _STOP}


def uniqueness(runs: list[Path], in_training: dict[str, bool]) -> dict[str, Any]:
    """Near-copies within each family (base situations only), topics repeated within a family, and how long the
    conversations are. `in_training`: situation id -> whether it trains (to show near-copies across the split)."""
    words: dict[str, dict[str, set[str]]] = defaultdict(dict)
    topics: dict[str, Counter] = defaultdict(Counter)
    lengths: Counter = Counter()
    for run in runs:
        for r in _situations(run):
            ws = _conversation_words(r)
            if ws is None:
                continue
            family, sid = r['plan']['family'], r['plan']['id']
            words[family][sid] = ws
            if r['plan'].get('topic'):
                topics[family][r['plan']['topic']] += 1
            base = next(v for v in r['variants'] if v['name'] == 'base')
            lengths[len(base['world'].get('messages', []))] += 1
    near = []
    for family, by in words.items():
        for a, b in combinations(sorted(by), 2):
            score = len(by[a] & by[b]) / max(1, len(by[a] | by[b]))
            if score >= NEAR_COPY:
                near.append(dict(family=family, a=a, b=b, overlap=round(score, 2),
                                 across_split=in_training.get(a) != in_training.get(b)))
    return dict(near_copies=sorted(near, key=lambda x: -x['overlap']),
                topic_repeats={f: {t: n for t, n in c.items() if n > 1} for f, c in topics.items()
                               if any(n > 1 for n in c.values())},
                lengths=dict(sorted(lengths.items())))


def _mix(examples: list, trigger_of=_trigger) -> dict[str, dict[str, float]]:
    by: dict[str, Counter] = defaultdict(Counter)
    for e in examples:
        for d in e.samples:
            by[trigger_of(e)][d['communication']] += 1
    return {t: {k: round(n / sum(c.values()), 3) for k, n in sorted(c.items())} for t, c in by.items()}


def _talk_by_family(examples: list) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for e in examples:
        out[e.family][teacher_majority(e)['communication']] += 1
    return out


def check(runs: list[Path], baseline: list[Path] | None = None, seed: int = 0) -> dict[str, Any]:
    examples, _ = load_examples(runs)
    split(examples, seed)
    train = [e for e in examples if e.group == 'train']
    held = [e for e in examples if e.group != 'train']
    result: dict[str, Any] = dict(situations=len({e.sid for e in examples}), train_rows=sum(len(e.samples) for e in train))

    # 1-2. Pairs.
    p = pairs(runs)
    trained = {f: c for f, c in p.items() if f not in EVAL_ONLY}
    result['pairs'] = p
    # Only pairs made since the baseline can still be fixed; older runs are reported, not gated.
    new = set(pairs([r for r in runs if r not in (baseline or [])])) if baseline else set(p)
    result['pairs_low_yield'] = sorted(f for f, c in p.items() if f in new and c['yield_'] < GATES['pair_yield'])
    usable = sum(c['usable'] for c in trained.values())
    result['expectations_met'] = round(sum(c['met'] for c in trained.values()) / usable, 3) if usable else None

    unseen = unseen_sides(train, held)
    result['held_out_side_never_trained'] = {k: len(v) for k, v in sorted(unseen.items(), key=lambda kv: -len(kv[1]))}
    result['held_out_situations'] = len({e.sid for e in held})

    # One-sided families: one way of talking in 90% or more of the family's training situations. Not wrong in
    # itself (a "stop" is acknowledged), but the student learns it as a habit unless pairs teach the other side.
    one_sided = {}
    for fam, c in _talk_by_family(train).items():
        top, n = c.most_common(1)[0]
        if n / sum(c.values()) >= 0.9 and sum(c.values()) >= 8:
            one_sided[fam] = f'{top} {n}/{sum(c.values())}'
    result['one_sided_families'] = one_sided

    # 4. Pairs that teach speaking vs quiet.
    by_sid: dict[str, dict[str, Any]] = defaultdict(dict)
    for e in train:
        by_sid[e.sid][e.variant] = e
    flips: Counter = Counter()
    for sid, v in by_sid.items():
        if 'base' in v and 'twin' in v:
            a, b = (_speaks(teacher_majority(v[k])['communication']) for k in ('base', 'twin'))
            if a != b:
                family = FAMILY_BY_ID.get(v['base'].family)
                flips[family.set if family else 'other'] += 1
                if v['base'].family.endswith(('_after_reply', '_after_ack')):
                    flips['after_reply'] += 1
    result['pairs_flipping_speech'] = dict(flips)

    # 5. Label mix against the baseline.
    mix = _mix(train)
    result['label_mix'] = mix
    if baseline:
        base_examples, _ = load_examples(baseline)
        split(base_examples, seed)
        base_mix = _mix([e for e in base_examples if e.group == 'train'])
        result['label_shift'] = {
            t: {k: round(mix[t].get(k, 0) - base_mix.get(t, {}).get(k, 0), 3) for k in set(mix[t]) | set(base_mix.get(t, {}))
                if abs(mix[t].get(k, 0) - base_mix.get(t, {}).get(k, 0)) > GATES['label_shift']}
            for t in mix}
        result['label_shift'] = {t: v for t, v in result['label_shift'].items() if v}

    # 6. Teacher agreement on speaking at all.
    split_n: dict[str, Counter] = defaultdict(Counter)
    for e in train:
        split_n[_trigger(e)]['n'] += 1
        split_n[_trigger(e)]['split'] += len({_speaks(d['communication']) for d in e.samples}) > 1
    result['teacher_disagrees'] = {t: round(c['split'] / c['n'], 3) for t, c in split_n.items()}

    # 7. Unique situations.
    result['uniqueness'] = uniqueness(runs, {e.sid: e.group == 'train' for e in examples})

    result['gates'] = dict(
        pair_yield=not result['pairs_low_yield'],
        expectations=result['expectations_met'] is None or result['expectations_met'] >= GATES['expectations'],
        both_sides=not any(len(v) >= MIN_SIDE_SITUATIONS for v in unseen.values()),
        taught_after_reply=flips['after_reply'] >= GATES['pairs_taught_after_reply'],
        label_shift=not result.get('label_shift'),
        disagreement=all(v <= GATES['disagreement'] for v in result['teacher_disagrees'].values()),
        unique=not result['uniqueness']['near_copies'] and not result['uniqueness']['topic_repeats'])
    return result


def page(result: dict[str, Any]) -> str:
    """The same numbers as a short page: gates first, then what failed."""
    lines = ['# Data check', '', f'{result["situations"]} situations, {result["train_rows"]} training rows.', '',
             '| gate | passes |', '|---|---|']
    lines += [f'| {k} | {"yes" if v else "**no**"} |' for k, v in result['gates'].items()]
    lines += ['', '## Pairs (usable / planned, teacher met)', '', '| family | planned | usable | met | yield |', '|---|---|---|---|---|']
    lines += [f'| {f} | {c.get("planned", 0)} | {c.get("usable", 0)} | {c.get("met", 0)} | {c["yield_"]:.0%} |'
              for f, c in sorted(result['pairs'].items())]
    lines += ['', 'Low yield (gated, made since the baseline): ' + (', '.join(result['pairs_low_yield']) or 'none')]
    lines += ['', f'Teacher met expectations on {result["expectations_met"]} of usable trained pairs.', '',
              '## Held-out sides never trained (family | kind | trigger | line: held-out situations)', '']
    lines += [f'- {k}: {n}' for k, n in result['held_out_side_never_trained'].items()] or ['- none']
    lines += ['', '## One-sided families (one way of talking in 90%+ of training situations)', '']
    lines += [f'- {f}: {v}' for f, v in sorted(result['one_sided_families'].items())] or ['- none']
    lines += ['', f'## Pairs that flip speaking vs quiet: {result["pairs_flipping_speech"]}', '',
              '## Label shifts against the baseline (over 5 points)', '']
    lines += [f'- {t}: {v}' for t, v in result.get('label_shift', {}).items()] or ['- none']
    lines += ['', '## Teacher samples disagree on speaking at all', '']
    lines += [f'- {t}: {v:.0%}' for t, v in result['teacher_disagrees'].items()]
    u = result['uniqueness']
    lines += ['', f'## Unique situations (near-copy: {NEAR_COPY:.0%}+ shared words within a family)', '']
    lines += [f'- {n["family"]}: {n["a"]} ~ {n["b"]} ({n["overlap"]:.0%}{", across train/test" if n["across_split"] else ""})'
              for n in u['near_copies']] or ['- no near-copies']
    lines += [f'- topic repeated in {f}: {t}' for f, t in u['topic_repeats'].items()]
    lines += ['', f'Earlier lines per situation: {u["lengths"]}']
    return '\n'.join(lines) + '\n'


__all__ = ['GATES', 'check', 'page', 'pairs']
