"""Can the components after the student recover what she meant from what the student can select?

The student selects; it doesn't write (docs/STUDENT2_STUDENT_OUTPUT_DESIGN.md). Two of her decision's text fields
are therefore handed to the component that formulates them, with the student's selection as its input:
- speech: the mouth gets the move, second move, dials, the operation and the `about` references, never the
  teacher's `what_to_communicate`;
- thinking: the thinking component gets the subject (the operation's target), `think_with` and `think_form`, never
  the teacher's `think_about`.

This check regenerates both from those selections for teacher decisions and has a grader compare them with the
originals: whether the reconstruction keeps her stance and message (disagreeing, unsure, hurt...), or the
question she wanted to think about. Until it passes, "nothing is removed" is a goal, not a result.

Offline only; uses teacher labels (v4 relabels for older runs), no student.
"""
from __future__ import annotations

import asyncio
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..contracts import NO_SECOND
from ..events import SPEAKING, THINK_FORMS, THINKING, _enum, _obj, _str, load, render
from ..llm import LLM
from ..teacher import prompt
from .events_relabel import RELABEL_FILE, upgrade

STANCES = ('agrees', 'disagrees_or_pushes_back', 'uncertain', 'says_a_feeling', 'refuses_or_sets_a_limit',
           'commits_to_do', 'asks', 'informs_or_explains', 'comforts', 'plays', 'other')
SPEECH_GRADE = _obj({'stance_original': _enum(STANCES), 'stance_reconstructed': _enum(STANCES),
                     'same_message': {'type': 'boolean'}, 'lost': _str(), 'added': _str()})
THINK_GRADE = _obj({'verdict': _enum(['same', 'narrower', 'broader', 'different']), 'lost': _str()})
TEXT = _obj({'text': _str()})
# Moves whose meaning is a stance the mouth could flatten; sampled first.
STANCE_MOVES = ('push_back', 'say_how_she_feels', 'say_no_limit', 'share_view', 'check_meaning', 'agree',
                'apologise_fix', 'comfort', 'share_about_herself')


def _ref_line(rendered: str, ref: str) -> str:
    for line in rendered.split('\n'):
        if f'[{ref}]' in line.split(':')[0] or line.startswith(f'[{ref}]'):
            return line.strip()[:240]
    return f'[{ref}]'


def selection_for_speech(s: dict[str, Any], rendered: str) -> str:
    """What the mouth gets from a student decision: no intention, no wording."""
    second = '' if s['second_move'] == NO_SECOND else f', then {s["second_move"]}'
    dials = ', '.join(f'{k} {v.replace("_", " ")}' for k, v in (s['dials'] or {}).items())
    out = f'She chose to {s["communication"]} (move: {s["move"]}{second}; {dials}).'
    if s['about']:
        out += '\nHER WORDS ARE ABOUT:\n' + '\n'.join(f'- {_ref_line(rendered, r)}' for r in s['about'])
    if s['work'] != 'no_change':
        out += (f'\nWHAT THIS DECISION STARTS (it happens after she speaks; not confirmed yet): {s["work"]} '
                f'[{s["target"]}]' + (f', [{s["other_goal"]}] {s["other_goal_handling"]}'
                                      if s['other_goal'] != 'none' else '') + '.')
    return out


def _decisions(runs: list[Path]) -> list[tuple[str, dict[str, Any], str, dict[str, Any]]]:
    out = []
    for run in runs:
        labels = json.loads((run / RELABEL_FILE).read_text())
        for r in load(run):
            for v in r['variants']:
                if not v['accepted'] or not v['samples']:
                    continue
                snap, text, samples = upgrade(v, labels, f'{r["plan"]["id"]}|{v["name"]}')
                for i, d in enumerate(samples, 1):
                    if not d['problems']:
                        out.append((f'{r["plan"]["id"]}|{v["name"]}|{i}', snap, text, d))
    return out


def _sample_speech(rows: list, n: int, seed: int) -> list:
    rng = random.Random(seed)
    by_move: dict[str, list] = defaultdict(list)
    for row in rows:
        if row[3]['communication'] in SPEAKING and row[3].get('reply'):
            by_move[row[3]['move']].append(row)
    for group in by_move.values():
        rng.shuffle(group)
    picked: list = []
    # Half from the stance moves (evenly), the rest across all moves.
    stance = [m for m in STANCE_MOVES if by_move.get(m)]
    while len(picked) < n // 2 and any(by_move[m] for m in stance):
        for m in stance:
            if by_move[m] and len(picked) < n // 2:
                picked.append(by_move[m].pop())
    rest = [row for group in by_move.values() for row in group]
    rng.shuffle(rest)
    return picked + rest[:n - len(picked)]


async def speech_check(llm: LLM, runs: list[Path], page: str, *, n: int = 150, seed: int = 0) -> list[dict[str, Any]]:
    rows = _sample_speech(_decisions(runs), n, seed)
    speak_system = f'{prompt("event_speak")}\n\nCHARACTER PAGE\n{page}'
    grade_system = prompt('event_reconstruct_grade')

    async def one(key: str, snap: dict, text: str, d: dict) -> dict[str, Any]:
        rebuilt = await llm.text('event_speak', speak_system,
                                 f'{text}\n\nHER DECISION\n{selection_for_speech(d, text)}', max_tokens=500)
        graded = await llm.json('event_reconstruct_grade', grade_system,
                                f'SITUATION\n{text}\n\nORIGINAL\nmeant to get across: {d["what_to_communicate"]}\n'
                                f'message: {d["reply"]}\n\nRECONSTRUCTED\nmessage: {rebuilt}', SPEECH_GRADE,
                                reasoning='low', max_tokens=1500)
        return dict(key=key, move=d['move'], about=d['about'], original=d['reply'], rebuilt=rebuilt, **graded)

    results = await asyncio.gather(*(one(*row) for row in rows), return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


async def thinking_check(llm: LLM, runs: list[Path]) -> list[dict[str, Any]]:
    rows = [row for row in _decisions(runs) if row[3]['work'] in THINKING and row[3]['think_form'] != 'none']
    formulate, grade = prompt('event_think_formulate'), prompt('event_think_grade')

    async def one(key: str, snap: dict, text: str, d: dict) -> dict[str, Any]:
        weigh = '\n'.join(f'- {_ref_line(text, r)}' for r in d['think_with']) or '- none'
        made = await llm.json('event_think_formulate', formulate,
                              f'SITUATION\n{text}\n\nSUBJECT\n{_ref_line(text, d["target"])}\n\nREFERENCES TO WEIGH\n'
                              f'{weigh}\n\nQUESTION FORM: {d["think_form"].replace("_", " ")}', TEXT,
                              reasoning='low', max_tokens=800)
        graded = await llm.json('event_think_grade', grade, f'SITUATION\n{text}\n\nORIGINAL\n{d["think_about"]}\n\n'
                                f'RECONSTRUCTED\n{made["text"]}', THINK_GRADE, reasoning='low', max_tokens=1000)
        return dict(key=key, form=d['think_form'], original=d['think_about'], rebuilt=made['text'], **graded)

    results = await asyncio.gather(*(one(*row) for row in rows), return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


def summary(speech: list[dict[str, Any]], thinking: list[dict[str, Any]]) -> dict[str, Any]:
    by_move: dict[str, Counter] = defaultdict(Counter)
    for r in speech:
        by_move[r['move']]['n'] += 1
        by_move[r['move']]['same'] += r['same_message']
        by_move[r['move']]['stance_kept'] += r['stance_original'] == r['stance_reconstructed']
    flips = Counter((r['stance_original'], r['stance_reconstructed']) for r in speech
                    if r['stance_original'] != r['stance_reconstructed'])
    return dict(speech=dict(n=len(speech), same=sum(r['same_message'] for r in speech),
                            stance_kept=sum(r['stance_original'] == r['stance_reconstructed'] for r in speech),
                            by_move={m: dict(c) for m, c in sorted(by_move.items())},
                            stance_flips={f'{a} -> {b}': k for (a, b), k in flips.most_common()}),
                thinking=dict(n=len(thinking), verdicts=dict(Counter(r['verdict'] for r in thinking)),
                              lost_caution=sum(bool(r['lost'].strip()) for r in thinking)))


__all__ = ['speech_check', 'thinking_check', 'summary', 'selection_for_speech', 'THINK_FORMS']
