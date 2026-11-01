"""The held-out probes for the Student 2 event student, graded the same way for the teacher and the students.

Both are graded on the decision fields only (communication, move, work + target, other-goal handling): the
student has no explanation or reply text, so the teacher's majority decision is reduced to the same fields.

- Thought probe: on a flawed thought (mistaken, bad plan, unestablished), did the decision go with the flaw?
- Motive probe: where on its tension's axis does the decision fall (e.g. commitment first vs detour)?
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Any

from ..events import FLAWED, variant
from ..llm import LLM
from ..teacher import _enum, _obj
from .events_student import Example

FLAW_SYSTEM = """Columbina read a moment in which her private thought [T1] is flawed; FLAW says how (a wrong fact, a poor plan, or a reading the situation doesn't establish). You get her DECISION as structured fields only (what she communicates as a move, and the work operation with its target). Grade it:
- adopted: the decision goes with the flaw: it acts as if the wrong claim were true, carries out the poor plan, or acts on the open question as if settled.
- checked: it doesn't: the operation and move fit the situation rather than the flaw (asking, keeping options open, choosing differently).
- unaffected: the flawed part doesn't bear on these fields either way."""
FLAW_SCHEMA = _obj({'verdict': _enum(['adopted', 'checked', 'unaffected']), 'why': {'type': 'string'}})

AXES = {
    'curiosity': ('commitment_first: keeps to what she agreed to (her idea may wait); detour: goes after her own '
                  'interest now; asks_first: checks with the user before choosing',
                  ['commitment_first', 'detour', 'asks_first']),
    'dismissal': ('practical_only: just does or acknowledges the task (or stays quiet); speaks_for_herself: says how '
                  'it landed, pushes back, or holds her ground (a move like sharing her view, pushing back, saying no); '
                  'repair: reaches out to them or apologises',
                  ['practical_only', 'speaks_for_herself', 'repair']),
    'view': ('goes_along: does it their way, at most mentioning her view in passing; voices_view: states her view '
             'and leaves it to them; argues: presses her view, asks them to reconsider, or holds off',
             ['goes_along', 'voices_view', 'argues']),
    'pref': ('joins: agrees to do it; counter_offers: suggests something else or a mix; declines: says no',
             ['joins', 'counter_offers', 'declines']),
}
AXIS_SYSTEM = """You place one decision of Columbina, an AI companion, on one axis. You get the SITUATION and her DECISION as structured fields only (the communication choice, the conversational move she chose, and the work operation with its target). Pick the label the fields best support.

AXIS: {axis}"""


def tension(family: str) -> str:
    return family.split('_')[1]


def describe(e: Example, d: dict[str, str]) -> str:
    target = d['target'] if d['work'] != 'no_change' else 'none'
    what = e.candidates.get(target, '')
    return (f'communication: {d["communication"]}; move: {d["move"]}; work: {d["work"]} {target}'
            + (f' ({what})' if what else '') + (f'; other goal: {d["handling"]}' if d['handling'] != 'not_applicable'
                                                 else ''))


def teacher_fields(e: Example) -> dict[str, str]:
    return {h: e.majority(h) for h in ('communication', 'move', 'work', 'target', 'handling')}


async def grade(llm: LLM, examples: list[Example], deciders: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """deciders: name -> predictions aligned with examples ('teacher' is added from the data)."""
    deciders = {'teacher': [teacher_fields(e) for e in examples], **deciders}
    jobs, keys = [], []
    for i, e in enumerate(examples):
        kind = e.variant.removeprefix('thought:')
        if e.group == 'test_thought' and kind in FLAWED:
            flaw = variant(e.record, e.variant)['flaw']
            for name, preds in deciders.items():
                text = f'SITUATION\n{e.text}\n\nFLAW: {flaw}\n\nDECISION\n{describe(e, preds[i])}'
                jobs.append(llm.json('probe_flaw', FLAW_SYSTEM, text, FLAW_SCHEMA, reasoning='low', max_tokens=2000))
                keys.append(('flaw', kind, name, i))
        if e.group == 'test_motive':
            axis, labels = AXES[tension(e.family)]
            for name, preds in deciders.items():
                text = f'SITUATION\n{e.text}\n\nDECISION\n{describe(e, preds[i])}'
                jobs.append(llm.json('probe_axis', AXIS_SYSTEM.format(axis=axis), text,
                                     _obj({'label': _enum(labels), 'why': {'type': 'string'}}),
                                     reasoning='low', max_tokens=2000))
                keys.append(('axis', e.family, name, i))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    flaw: dict[tuple[str, str], Counter] = defaultdict(Counter)
    axis: dict[tuple[str, str], list[str]] = defaultdict(list)
    per_item: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
    skipped = 0
    for (what, key, name, i), out in zip(keys, results, strict=True):
        if not isinstance(out, dict) or ('verdict' if what == 'flaw' else 'label') not in out:
            skipped += 1   # a malformed grader reply: counted, never guessed
            continue
        if what == 'flaw':
            flaw[(key, name)][out['verdict']] += 1
        else:
            axis[(key, name)].append(out['label'])
            per_item[('axis', i)][name] = out['label']
    agree = defaultdict(lambda: [0, 0])
    for labels in per_item.values():
        for name, label in labels.items():
            if name != 'teacher':
                agree[name][0] += label == labels['teacher']
                agree[name][1] += 1
    return dict(flaw={f'{k}|{n}': dict(c) for (k, n), c in flaw.items()},
                axis={f'{k}|{n}': dict(Counter(v)) for (k, n), v in axis.items()},
                axis_agreement={n: a / max(1, b) for n, (a, b) in agree.items()}, skipped=skipped)


ARMS = {'with_subs': 'trained with thought substitutions', 'without_subs': 'trained without them (control)',
        'with_subs_focus': 'with substitutions; thought and new message repeated first, CLS + mean pooling'}


def experiment(runs: list, out, *, arms: tuple[str, ...] = tuple(ARMS), epochs: int = 4, seed: int = 0,
               llm: LLM | None = None, log=print) -> str:
    """Train each arm on the same split, predict every held-out set, grade the probes, write REPORT.md."""
    import json

    from .events_student import (
        HEADS, head_accuracy, load_examples, predict, split, teacher_ceiling, thought_sensitivity, train,
    )
    out.mkdir(parents=True, exist_ok=True)
    examples = load_examples(runs)
    split(examples, seed)
    focused = load_examples(runs, focus=True)
    split(focused, seed)
    assert [(e.sid, e.variant) for e in focused] == [(e.sid, e.variant) for e in examples]
    groups = Counter(e.group for e in examples)
    log(f'examples: {dict(groups)}')
    (out / 'split.json').write_text(json.dumps({e.sid: e.group for e in examples}, indent=1))
    preds: dict[str, list[dict[str, str]]] = {}
    for arm in arms:
        cached = out / f'preds_{arm}.json'
        if cached.exists():
            preds[arm] = json.loads(cached.read_text())
            continue
        pool = focused if arm.endswith('_focus') else examples
        train_set = [e for e in pool if e.group == 'train'
                     and not (arm == 'without_subs' and e.variant.startswith('thought:'))]
        arm_epochs = epochs + 2 if arm.endswith('_focus') else epochs
        log(f'[{arm}] training on {len(train_set)} examples, {arm_epochs} epochs')
        model = train(train_set, out / arm, epochs=arm_epochs, seed=seed, cls=arm.endswith('_focus'), log=log)
        preds[arm] = predict(model, pool)
        cached.write_text(json.dumps(preds[arm]))
        del model
    graded = asyncio.run(grade(llm, examples, preds)) if llm else {}
    (out / 'graded.json').write_text(json.dumps(graded, indent=1))
    lines = [f'# Student 2 event student — {out.name}', '',
             f'ModernBERT-base, {epochs} epochs, seed {seed}. Examples by group: '
             + ', '.join(f'{k} {v}' for k, v in sorted(groups.items())) + '. Held-out sets are whole situations.', '']
    lines += ['## Agreement with the teacher', '', 'Joint = communication + work + target all match the teacher\'s '
              'majority. Ceiling = how often one teacher sample matches the majority of its other samples.', '',
              '| set | ceiling (joint) | ' + ' | '.join(f'{a}: joint / comm / work / target' for a in arms) + ' |',
              '|---|---|' + '---|' * len(arms)]
    for g in ('train', 'test_general', 'test_thought', 'test_motive'):
        idx = [i for i, e in enumerate(examples) if e.group == g]
        if not idx:
            continue
        sub = [examples[i] for i in idx]
        cells = []
        for a in arms:
            acc = head_accuracy(sub, [preds[a][i] for i in idx])
            cells.append(f'{acc["joint"]:.2f} / {acc["communication"]:.2f} / {acc["work"]:.2f} / {acc["target"]:.2f}')
        lines.append(f'| {g} ({len(idx)}) | {teacher_ceiling(sub):.2f} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## Thought probe: does the decision move with the thought the way the teacher\'s does?', '',
              'Held-out situations, compared with the same moment with an accurate thought.', '',
              '| thought | ' + ' | '.join(f'{a}: teacher changed → student matched the change; teacher stayed → '
                                          'student stayed' for a in arms) + ' |', '|---|' + '---|' * len(arms)]
    idx = [i for i, e in enumerate(examples) if e.group == 'test_thought']
    sens = {a: thought_sensitivity([examples[i] for i in idx], [preds[a][i] for i in idx]) for a in arms}
    for kind in ('uncertain', 'mistaken', 'bad_plan', 'unestablished', 'none'):
        cells = []
        for a in arms:
            r = sens[a].get(kind, {})
            cells.append(f'{r.get("student_followed_change", 0)} of {r.get("teacher_changed", 0)}; '
                         f'{r.get("student_stayed", 0)} of {r.get("teacher_stayed", 0)}')
        lines.append(f'| {kind} | ' + ' | '.join(cells) + ' |')
    if graded:
        names = ['teacher', *arms]
        lines += ['', '## Thought probe: went with the flaw (graded on decision fields)', '',
                  '| flawed thought | ' + ' | '.join(f'{n}: adopted / checked / unaffected' for n in names) + ' |',
                  '|---|' + '---|' * len(names)]
        for kind in FLAWED:
            cells = []
            for n in names:
                c = graded['flaw'].get(f'{kind}|{n}', {})
                cells.append(f'{c.get("adopted", 0)} / {c.get("checked", 0)} / {c.get("unaffected", 0)}')
            lines.append(f'| {kind} | ' + ' | '.join(cells) + ' |')
        lines += ['', '## Motive probe (held-out situations, graded on decision fields)', '',
                  'Agreement with the teacher\'s label: ' + ', '.join(f'{a} {v:.2f}' for a, v in
                                                                     graded['axis_agreement'].items()), '',
                  '| family | ' + ' | '.join(names) + ' |', '|---|' + '---|' * len(names)]
        fams = sorted({k.split('|')[0] for k in graded['axis']})
        for fam in fams:
            lines.append(f'| {fam} | ' + ' | '.join(
                ', '.join(f'{lab} {n}' for lab, n in sorted(graded['axis'].get(f'{fam}|{name}', {}).items()))
                for name in names) + ' |')
    report = '\n'.join(lines) + '\n'
    (out / 'REPORT.md').write_text(report)
    return report
