"""The teacher: one complete chain per sample, think -> label -> code -> decide -> critic.

A rejected sample is regenerated whole (from the thought), never patched, so its feeling, mood
and decision always belong together. Each accepted sample is one training example.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from . import render
from .contracts import (
    ACTS, APPRAISALS, APPRAISALS_SIGNED, DIALS, FEELINGS, GROUP_LABELS, GROUPS, MOOD_KEYS, NO_SECOND,
    NOTE_UPDATES, REQUEST_KINDS, SCALE_WORDS, SIGNED_WORDS, TONES, Bundle, Message, Sample, scale,
)
from .llm import LLM
from .mood import MoodDynamics, band, update
from .turn import allowed_groups, flags

PROMPTS = Path(__file__).parent / 'prompts'
SAMPLES = 5
MAX_ATTEMPTS = 3          # the first try plus two retries
THINK_REASONING = 'low'


@cache
def prompt(name: str) -> str:
    return (PROMPTS / f'{name}.txt').read_text()


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def _enum(values) -> dict[str, Any]:
    return {'type': 'string', 'enum': list(values)}


def understand_schema(note_ids: list[str]) -> dict[str, Any]:
    note = _obj({'note': _enum(note_ids or ['-']), 'update': _enum(NOTE_UPDATES)})
    return _obj({
        'acts': _obj({a: {'type': 'boolean'} for a in ACTS}),
        'tone': _enum(TONES), 'request': _enum(REQUEST_KINDS),
        'about_her': _enum(SCALE_WORDS), 'sure': _enum(SCALE_WORDS),
        'appraisal': _obj({**{k: _enum(SIGNED_WORDS) for k in APPRAISALS_SIGNED},
                           **{k: _enum(SCALE_WORDS) for k in APPRAISALS}}),
        'feelings': _obj({f: _enum(SCALE_WORDS) for f in FEELINGS}),
        'note_updates': {'type': 'array', 'items': note},
    })


def decide_schema(allowed: list[str]) -> dict[str, Any]:
    return _obj({
        'main': _enum(allowed), 'second': _enum([*allowed, NO_SECOND]),
        'dials': _obj({d: _enum(SCALE_WORDS) for d in DIALS}),
        'wanted_not_available': {'type': 'array', 'items': {'type': 'string'}},
        'used_older_messages': {'type': 'array', 'items': _obj({'message': {'type': 'integer'},
                                                                 'what': {'type': 'string'}})},
    })


CRITIC_SCHEMA = _obj({'in_character': {'type': 'boolean'}, 'human_like': {'type': 'boolean'},
                      'follows_inputs': {'type': 'boolean'}, 'older_messages_complete': {'type': 'boolean'},
                      'verdict': _enum(['accept', 'reject']), 'reason': {'type': 'string'}})


def understanding_text(u: dict[str, Any]) -> str:
    acts = ', '.join(a.replace('_', ' ') for a in ACTS if u['acts'][a]) or 'nothing in particular'
    felt = ', '.join(f'{f} ({u["feelings"][f].replace("_", " ")})' for f in FEELINGS if scale(u['feelings'][f]) > 0)
    a = u['appraisal']
    return (f'She read their message as: {acts}; tone {u["tone"]}; request: {u["request"].replace("_", " ")}; '
            f'about her: {u["about_her"].replace("_", " ")}; she is {u["sure"].replace("_", " ")} sure.\n'
            f'How it lands: good for her {a["good_for_her"].replace("_", " ")}, warm toward her '
            f'{a["warm_toward_her"].replace("_", " ")}, surprising {a["surprising"].replace("_", " ")}, '
            f'important {a["important"].replace("_", " ")}.\nShe feels: {felt or "nothing much"}.')


def mood_text(mood: dict[str, float]) -> str:
    return ', '.join(f'{k} {band(k, mood[k])}' for k in MOOD_KEYS)


def flags_text(turn_flags: dict[str, bool]) -> str:
    on = [k.replace('_', ' ') for k, v in turn_flags.items() if v]
    return ', '.join(on) if on else 'nothing special'


def decision_text(decide: dict[str, Any]) -> str:
    second = '' if decide['second'] == NO_SECOND else f'; then: {GROUP_LABELS[decide["second"]]}'
    dials = ', '.join(f'{d} {decide["dials"][d].replace("_", " ")}' for d in DIALS)
    return f'Main move: {GROUP_LABELS[decide["main"]]}{second}. How: {dials}.'


@dataclass
class View:
    """What one teacher run reads. ``full`` adds the whole conversation; ``bounded`` is the student's view."""
    page: str
    bundle: Bundle
    history: list[Message]
    full: bool

    @property
    def window_start(self) -> int:
        """Message number where the student's window begins; anything numbered below it is older."""
        return self.bundle.window[0].turn if self.bundle.window else self.bundle.turn

    @property
    def has_older(self) -> bool:
        return self.full and bool(self.history) and self.history[0].turn < self.window_start

    def inputs(self) -> str:
        parts = ['CHARACTER PAGE', self.page.strip(), '', 'INPUTS', render.text(self.bundle, include_window=not self.full)]
        if self.full:
            older = (f'messages numbered below {self.window_start} are older than the recent window' if self.has_older
                     else 'every message is in the recent window: nothing is older, so used_older_messages stays empty')
            parts += ['', 'FULL CONVERSATION (numbered, before their new message)',
                      f'RECENT WINDOW START: {self.window_start} ({older})', render.conversation_text(self.history)]
        return '\n'.join(parts)


class Teacher:
    def __init__(self, llm: LLM, page: str, dynamics: MoodDynamics, *, critic: bool = True):
        self.llm = llm
        self.page = page
        self.dynamics = dynamics
        # The ablation runs every arm without a critic, so the handoff is the only difference.
        self.use_critic = critic

    async def _attempt(self, view: View, index: int, attempt: int) -> Sample:
        base = view.inputs()
        thought = await self.llm.text('think', prompt('think'), base, reasoning=THINK_REASONING, max_tokens=2500)
        note_ids = [n.id for n in view.bundle.notes]
        understand = await self.llm.json(
            'label_understand', prompt('label_understand'),
            f'{base}\n\nOPEN NOTE IDS: {", ".join(note_ids) or "none (give an empty note_updates list)"}\n\nINNER THOUGHT\n{thought}',
            understand_schema(note_ids))
        if not note_ids:
            understand['note_updates'] = []
        else:
            seen = {u['note']: u['update'] for u in understand['note_updates'] if u['note'] in note_ids}
            understand['note_updates'] = [{'note': i, 'update': seen.get(i, 'none')} for i in note_ids]
        mood_after = update(view.bundle.mood, understand, self.dynamics)
        turn_flags = flags(view.bundle.notice, understand, view.bundle.notes)
        allowed = allowed_groups(view.bundle.notice, understand, turn_flags)
        decide_input = (f'{base}\n\nINNER THOUGHT\n{thought}\n\nHER UNDERSTANDING\n{understanding_text(understand)}\n\n'
                        f'HER MOOD AFTER THIS MESSAGE: {mood_text(mood_after)}\nTHIS TURN: {flags_text(turn_flags)}\n'
                        f'ALLOWED MOVES: {", ".join(allowed)}')
        decide = await self.llm.json('label_decide', prompt('label_decide'), decide_input, decide_schema(allowed))
        # Only messages before the student's window count as older; anything else is dropped here.
        decide['used_older_messages'] = [u for u in decide['used_older_messages']
                                         if view.has_older and u['message'] < view.window_start]
        if not self.use_critic:
            return Sample(index=index, attempt=attempt, thought=thought, understand=understand, mood_after=mood_after,
                          flags=turn_flags, allowed=allowed, decide=decide, critic={'verdict': 'none (no critic)'},
                          accepted=True, view='full' if view.full else 'bounded')
        critic = await self.llm.json(
            'critic', prompt('critic'),
            f'{decide_input}\n\nHER DECISION\n{decision_text(decide)}\n'
            f'used_older_messages: {decide["used_older_messages"] if view.has_older else "(not applicable: no older messages exist)"}',
            CRITIC_SCHEMA)
        return Sample(index=index, attempt=attempt, thought=thought, understand=understand, mood_after=mood_after,
                      flags=turn_flags, allowed=allowed, decide=decide, critic=critic,
                      accepted=critic['verdict'] == 'accept', view='full' if view.full else 'bounded')

    async def sample(self, view: View, index: int) -> list[Sample]:
        """All attempts for one sample; the last one is accepted, or the sample was dropped."""
        attempts = []
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = await self._attempt(view, index, attempt)
            except Exception as exc:  # a failed call costs this attempt, not the turn
                attempts.append(Sample(index=index, attempt=attempt, thought='', understand={}, mood_after={},
                                       flags={}, allowed=[], decide={}, critic={'verdict': 'error', 'reason': repr(exc)[:300]},
                                       accepted=False, view='full' if view.full else 'bounded'))
                continue
            attempts.append(result)
            if result.accepted:
                break
        return attempts

    async def samples(self, view: View, count: int = SAMPLES) -> list[list[Sample]]:
        return list(await asyncio.gather(*(self.sample(view, i) for i in range(count))))

    async def say_it(self, view: View, chosen: Sample) -> str:
        user = (f'{view.inputs()}\n\nHER UNDERSTANDING\n{understanding_text(chosen.understand)}\n'
                f'HER MOOD NOW: {mood_text(chosen.mood_after)}\n\nHER DECISION\n{decision_text(chosen.decide)}')
        return await self.llm.text('say_it', prompt('say_it'), user, max_tokens=400)

    async def coverage(self, notes_text: str, used: list[dict[str, Any]], history: list[Message]) -> list[bool]:
        by_turn = {m.turn: m for m in history}
        items = '\n'.join(f'[{i}] message {u["message"]}: "{by_turn[u["message"]].text if u["message"] in by_turn else "?"}" '
                          f'— what mattered: {u["what"]}' for i, u in enumerate(used))
        schema = _obj({'items': {'type': 'array', 'items': _obj({'index': {'type': 'integer'},
                                                                  'covered': {'type': 'boolean'}})}})
        result = await self.llm.json('coverage', prompt('coverage'), f'OPEN NOTES\n{notes_text}\n\nUSED\n{items}', schema)
        got = {item['index']: item['covered'] for item in result['items']}
        return [bool(got.get(i, False)) for i in range(len(used))]


__all__ = ['Teacher', 'View', 'SAMPLES', 'GROUPS']
