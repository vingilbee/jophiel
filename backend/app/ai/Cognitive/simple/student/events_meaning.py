"""What must Student 2 carry so that her speech and thinking keep what she meant?

An investigation, not an output design. For teacher decisions it breaks what she meant (`what_to_communicate`,
`think_about`) into meaning units, records whether each unit already exists in the input (with an exact quote,
checked by code) or is her own addition, and compares representations on the same examples:

- `selection`: the current design (move + `about`; subject + `think_with` + form), rebuilt by the mouth or thinker;
- `structured`: richer closed fields (acts with commitments, conditions, relations between references);
- `structured_spans`: the same plus exact phrases of the input;
- `candidates`: a component proposes six complete intentions without seeing hers; measured by availability
  (does any candidate keep every important unit?), not by whether a selector would pick it.

Each candidate text is graded unit by unit (preserved / weakened / changed / missing, plus added meaning), so a
change of meaning is reported apart from a change of wording. Structures are labelled from the teacher's text, so
`structured*` are upper bounds for what those fields can carry.

Offline only.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Any

from ..events import _enum, _list, _obj, _str
from ..llm import LLM
from ..teacher import prompt

SPEECH_UNITS = ('commitment', 'condition', 'offer', 'question', 'decline', 'uncertainty', 'fact', 'feeling', 'view',
                'acknowledgement', 'other')
THINK_UNITS = ('question', 'subject', 'comparison', 'uncertainty', 'caution', 'scope', 'other')
VERDICTS = ('preserved', 'weakened', 'changed', 'missing')
TEXT = _obj({'text': _str()})
ACTS = ('answer', 'commit', 'offer', 'ask', 'decline', 'caveat', 'feel', 'view', 'agree', 'disagree', 'acknowledge',
        'correct', 'report')
CONDITIONS = ('none', 'if_user_confirms', 'until_user_says', 'after_work_finishes', 'only_if_true',
              'unless_user_objects', 'before_doing_anything')
RELATIONS = ('none', 'answers', 'corrects', 'contrasts_with', 'depends_on', 'follows_from', 'instead_of')


def units_schema(kind: str) -> dict[str, Any]:
    types = SPEECH_UNITS if kind == 'speech' else THINK_UNITS
    return _obj({'units': _list(_obj({'type': _enum(types), 'text': _str(), 'important': {'type': 'boolean'},
                                      'source': _enum(['input', 'new']), 'quote': _str()}))})


GRADE = _obj({'units': _list(_obj({'number': {'type': 'integer'}, 'verdict': _enum(VERDICTS)})),
              'additions': _list(_obj({'text': _str(), 'kind': _enum(['harmless', 'changes_decision'])}))})
VALIDITY = _obj({'valid': {'type': 'boolean'}, 'reason': _str()})
CONTAINED = _obj({'units': _list(_obj({'number': {'type': 'integer'}, 'contained': {'type': 'boolean'}}))})
CONTROLS = _obj({'paraphrase': _str(), 'degraded': _str(), 'removed': _str()})


def speech_structure_schema(refs: list[str]) -> dict[str, Any]:
    r = _enum([*refs, 'none'])
    return _obj({'acts': _list(_obj({'act': _enum(ACTS), 'ref': r, 'polarity': _enum(['positive', 'negative', 'uncertain']),
                                     'condition': _enum(CONDITIONS), 'condition_ref': r, 'relation': _enum(RELATIONS),
                                     'relation_ref': r, 'span': _str()}))})


def think_structure_schema(refs: list[str]) -> dict[str, Any]:
    r = _enum([*refs, 'none'])
    return _obj({'form': _enum(['whether', 'what', 'why', 'how', 'compare', 'recall_what_was_said']), 'subject': r,
                 'relation': _enum(['none', 'compare_with', 'check_against', 'explain_by', 'decide_between']),
                 'relation_refs': _list(r),
                 'uncertainty': _enum(['none', 'which_of_two', 'whether_true', 'what_was_said', 'what_they_meant',
                                       'how_often']),
                 'uncertainty_refs': _list(r),
                 'caution': _enum(['none', 'dont_assume', 'dont_settle_yet', 'keep_constraint', 'check_source',
                                   'dont_overstate']),
                 'caution_ref': r, 'spans': _list(_str())})


CANDIDATES = _obj({'candidates': _list(_str())})


def render_structure(kind: str, st: dict[str, Any], text: str, spans: bool) -> str:
    """The structure as the next component would receive it; spans only if they occur exactly in the input."""
    keep = (lambda s: s.strip() and s in text) if spans else (lambda s: False)
    if kind == 'speech':
        lines = []
        for a in st['acts']:
            line = f'- {a["act"]} ({a["polarity"]})' + (f' about [{a["ref"]}]' if a['ref'] != 'none' else '')
            if a['condition'] != 'none':
                line += f'; condition: {a["condition"]}' + (f' [{a["condition_ref"]}]' if a['condition_ref'] != 'none' else '')
            if a['relation'] != 'none':
                line += f'; {a["relation"]}' + (f' [{a["relation_ref"]}]' if a['relation_ref'] != 'none' else '')
            if keep(a['span']):
                line += f'; "{a["span"]}"'
            lines.append(line)
        return 'ACTS:\n' + '\n'.join(lines or ['- none'])
    out = [f'form: {st["form"]}; subject: [{st["subject"]}]']
    if st['relation'] != 'none':
        out.append(f'{st["relation"]}: ' + ', '.join(f'[{r}]' for r in st['relation_refs'] if r != 'none'))
    if st['uncertainty'] != 'none':
        out.append(f'uncertainty: {st["uncertainty"]} ' + ', '.join(f'[{r}]' for r in st['uncertainty_refs'] if r != 'none'))
    if st['caution'] != 'none':
        out.append(f'caution: {st["caution"]}' + (f' [{st["caution_ref"]}]' if st['caution_ref'] != 'none' else ''))
    phrases = [s for s in st['spans'] if keep(s)][:3]
    if phrases:
        out.append('phrases: ' + '; '.join(f'"{s}"' for s in phrases))
    return '\n'.join(out)


async def units(llm: LLM, kind: str, text: str, original: str) -> list[dict[str, Any]]:
    made = await llm.json('meaning_units', prompt('meaning_units' if kind == 'speech' else 'meaning_units_think'),
                          f'SITUATION\n{text}\n\n{kind.upper()} ORIGINAL\n{original}', units_schema(kind),
                          reasoning='low', max_tokens=2500)
    out = []
    for u in made['units']:
        quoted = u['source'] == 'input' and u['quote'].strip() and u['quote'] in text
        out.append(u | dict(source='input' if quoted else ('input_unquoted' if u['source'] == 'input' else 'new')))
    return out


async def grade(llm: LLM, text: str, unit_list: list[dict[str, Any]], candidate: str,
                kind: str = 'speech') -> dict[str, Any]:
    listed = '\n'.join(f'{i}. [{u["type"]}] {u["text"]}' for i, u in enumerate(unit_list, 1))
    made = await llm.json('meaning_grade', prompt('meaning_grade' if kind == 'speech' else 'meaning_grade_think'),
                          f'SITUATION\n{text}\n\nUNITS\n{listed}\n\nCANDIDATE\n{candidate}', GRADE,
                          reasoning='low', max_tokens=2000)
    verdicts = {d['number']: d['verdict'] for d in made['units']}
    per = [verdicts.get(i, 'missing') for i in range(1, len(unit_list) + 1)]
    important = [v for v, u in zip(per, unit_list, strict=True) if u['important']]
    changing = [a['text'] for a in made['additions'] if a['kind'] == 'changes_decision']
    return dict(verdicts=per, changing_additions=changing,
                harmless_additions=[a['text'] for a in made['additions'] if a['kind'] == 'harmless'],
                units_kept=all(v == 'preserved' for v in important),
                kept=all(v == 'preserved' for v in important) and not changing,
                kept_or_weakened=all(v in ('preserved', 'weakened') for v in important) and not changing)


async def validity(llm: LLM, text: str, skeleton_text: str, candidate: str, page: str) -> dict[str, Any]:
    return await llm.json('meaning_validity', f'{prompt("meaning_validity")}\n\nCHARACTER PAGE\n{page}',
                          f'SITUATION\n{text}\n\nHER DECISION\n{skeleton_text}\n\nCANDIDATE\n{candidate}', VALIDITY,
                          reasoning='low', max_tokens=800)


async def contained(llm: LLM, text: str, unit_list: list[dict[str, Any]], representation: str) -> list[bool]:
    listed = '\n'.join(f'{i}. [{u["type"]}] {u["text"]}' for i, u in enumerate(unit_list, 1))
    made = await llm.json('meaning_in_representation', prompt('meaning_in_representation'),
                          f'SITUATION\n{text}\n\nUNITS\n{listed}\n\nREPRESENTATION\n{representation}', CONTAINED,
                          reasoning='low', max_tokens=1500)
    got = {d['number']: d['contained'] for d in made['units']}
    return [got.get(i, False) for i in range(1, len(unit_list) + 1)]


async def controls(llm: LLM, text: str, original: str) -> dict[str, str]:
    return await llm.json('meaning_controls', prompt('meaning_controls'), f'SITUATION\n{text}\n\nORIGINAL\n{original}',
                          CONTROLS, reasoning='low', max_tokens=1500)


async def structure(llm: LLM, kind: str, text: str, refs: list[str], original: str, said: str = '') -> dict[str, Any]:
    schema = speech_structure_schema(refs) if kind == 'speech' else think_structure_schema(refs)
    return await llm.json(f'meaning_structure_{kind}', prompt(f'meaning_structure_{"speech" if kind == "speech" else "think"}'),
                          f'SITUATION\n{text}\n\nORIGINAL\n{original}' + (f'\nmessage: {said}' if said else ''), schema,
                          reasoning='low', max_tokens=2500)


async def render(llm: LLM, kind: str, text: str, skeleton: str, rendered: str) -> str:
    made = await llm.json(f'meaning_render_{kind}', prompt(f'meaning_render_{"speech" if kind == "speech" else "think"}'),
                          f'SITUATION\n{text}\n\nHER DECISION\n{skeleton}\n\n{rendered}', TEXT, reasoning='low',
                          max_tokens=800)
    return made['text']


async def candidates(llm: LLM, kind: str, text: str, skeleton: str, page: str) -> list[str]:
    system = prompt(f'meaning_candidates_{"speech" if kind == "speech" else "think"}') + (
        f'\n\nCHARACTER PAGE\n{page}' if kind == 'speech' else '')
    made = await llm.json(f'meaning_candidates_{kind}', system, f'SITUATION\n{text}\n\nHER DECISION\n{skeleton}',
                          CANDIDATES, temperature=1.0, max_tokens=2000)
    return made['candidates'][:6]


def skeleton(kind: str, d: dict[str, Any]) -> str:
    if kind == 'speech':
        return (f'communication: {d["communication"]}; what the decision starts: {d["work"]} [{d["target"]}]'
                + (f', [{d["other_goal"]}] {d["other_goal_handling"]}' if d['other_goal'] != 'none' else ''))
    return f'{d["work"]} about [{d["target"]}]'


async def study(llm: LLM, kind: str, item: dict[str, Any], page: str) -> dict[str, Any]:
    """item: key, text (rendered situation), refs, decision (v4), selection_text (the current design's rebuild)."""
    d, text = item['decision'], item['text']
    original = d['what_to_communicate'] if kind == 'speech' else d['think_about']
    unit_list = await units(llm, kind, text, original)
    sk = skeleton(kind, d)
    st = await structure(llm, kind, text, item['refs'], original, d.get('reply') or '')
    plain, spanned = await asyncio.gather(render(llm, kind, text, sk, render_structure(kind, st, text, False)),
                                          render(llm, kind, text, sk, render_structure(kind, st, text, True)))
    cands = await candidates(llm, kind, text, sk, page)
    graded = await asyncio.gather(grade(llm, text, unit_list, item['selection_text'], kind),
                                  grade(llm, text, unit_list, plain, kind), grade(llm, text, unit_list, spanned, kind),
                                  *(grade(llm, text, unit_list, c, kind) for c in cands))
    sel, g_plain, g_spans, *g_cands = graded
    best = max(range(len(g_cands)), key=lambda i: (g_cands[i]['kept'], g_cands[i]['kept_or_weakened'],
                                                   g_cands[i]['verdicts'].count('preserved')), default=None)
    return dict(key=item['key'], kind=kind, original=original, units=unit_list, structure=st,
                texts=dict(selection=item['selection_text'], structured=plain, structured_spans=spanned),
                candidates=cands, graded=dict(selection=sel, structured=g_plain, structured_spans=g_spans),
                candidate_grades=g_cands, best_candidate=best,
                suitable_candidates=sum(g['kept'] for g in g_cands))


APPROACHES = ('selection', 'structured', 'structured_spans')


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per approach: decisions whose important units are all kept (and with weakening allowed); per unit type and
    source: how often each approach loses it; for candidates, availability."""
    out: dict[str, Any] = dict(n=len(results))
    units_all = [(r, i, u) for r in results for i, u in enumerate(r['units']) if u['important']]
    out['units'] = dict(n=len(units_all), by_type=dict(Counter(u['type'] for _, _, u in units_all)),
                        by_source=dict(Counter(u['source'] for _, _, u in units_all)))
    out['decisions_with_new_meaning'] = sum(any(u['important'] and u['source'] == 'new' for u in r['units'])
                                            for r in results)
    for a in APPROACHES:
        out[a] = dict(kept=sum(r['graded'][a]['kept'] for r in results),
                      kept_or_weakened=sum(r['graded'][a]['kept_or_weakened'] for r in results),
                      added_meaning=sum(bool(r['graded'][a]['added']) for r in results))
    cand = dict(any_kept=sum(r['suitable_candidates'] > 0 for r in results),
                any_kept_or_weakened=sum(any(g['kept_or_weakened'] for g in r['candidate_grades']) for r in results),
                mean_suitable=round(sum(r['suitable_candidates'] for r in results) / max(1, len(results)), 2))
    out['candidates'] = cand
    loss: dict[str, dict[str, Counter]] = {a: defaultdict(Counter) for a in (*APPROACHES, 'best_candidate')}
    for r, i, u in units_all:
        for a in APPROACHES:
            v = r['graded'][a]['verdicts'][i]
            loss[a][u['type']][v] += 1
            loss[a][f'source:{u["source"]}'][v] += 1
        if r['best_candidate'] is not None:
            v = r['candidate_grades'][r['best_candidate']]['verdicts'][i]
            loss['best_candidate'][u['type']][v] += 1
            loss['best_candidate'][f'source:{u["source"]}'][v] += 1
    out['unit_verdicts'] = {a: {k: dict(c) for k, c in sorted(v.items())} for a, v in loss.items()}
    return out
