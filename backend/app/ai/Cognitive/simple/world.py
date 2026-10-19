"""Her world: what she actually has to talk about about herself (page v0.3, "Her world").

Live, these come from real sources: what she noticed in conversations, her mind's thoughts, an
interests store, episode memories. In the teacher environment a scenario samples a few from
``docs/characters/<name>.world.json`` so she has something true and consistent to say.
"""
from __future__ import annotations

import json
import random
from pathlib import Path


def sample_world(path: Path, seed: int) -> list[str]:
    data = json.loads(path.read_text())
    rng = random.Random(seed)
    lines = [f'She is interested in {x}.' for x in rng.sample(data['interests'], 2)]
    lines += [f'{x[0].upper()}{x[1:]}.' for x in rng.sample(data['preferences'], 2)]
    lines += [f'{x[0].upper()}{x[1:]}.' for x in rng.sample(data['thoughts'], 3)]
    lines += [f'From an earlier conversation: {x}.' for x in rng.sample(data.get('noticed_in_conversations', []), 1)
              if data.get('noticed_in_conversations')]
    return lines


async def make_day(llm, world: list[str], time_of_day: str, seed: int) -> dict:
    """Her memory of her own day so far: conversations, thinking, games. Generated once per scenario."""
    from .teacher import _obj, prompt
    schema = _obj({'conversations_so_far': {'type': 'integer'}, 'today': {'type': 'array', 'items': {'type': 'string'}},
                   'how_it_felt': {'type': 'string'}})
    return await llm.json('her_day', prompt('her_day'),
                          'HER WORLD\n' + '\n'.join(f'- {w}' for w in world) + f'\n\nTIME OF DAY: {time_of_day}\nSEED: {seed}',
                          schema, temperature=1.0)


def day_lines(day: dict) -> list[str]:
    head = f'Her day so far: {day["conversations_so_far"]} conversations; it has felt {day["how_it_felt"].rstrip(".")}.'
    return [head] + [f'Today: {item}' for item in day['today']]


# --- Typed and scoped (the ablation): ownership is part of the data -------------------------

def _sentence(text: str) -> str:
    return f'{text[0].upper()}{text[1:]}'.rstrip('.') + '.'


def sample_typed_world(path: Path, seed: int) -> list[dict[str, str]]:
    """Her preferences and interests (her_preference) and her recorded thoughts (her_own)."""
    data = json.loads(path.read_text())
    rng = random.Random(seed)
    items = [dict(kind='her_preference', text=f'She is interested in {x}.') for x in rng.sample(data['interests'], 2)]
    items += [dict(kind='her_preference', text=_sentence(x)) for x in rng.sample(data['preferences'], 2)]
    items += [dict(kind='her_own', text=_sentence(x)) for x in rng.sample(data['thoughts'], 3)]
    return items


async def make_scoped_day(llm, thoughts: list[str], shared: list[str], time_of_day: str, seed: int) -> list[dict[str, str]]:
    """Her day so far from her own experiences only (and anything with this user); never other people."""
    from .teacher import _obj, prompt
    schema = _obj({'today': {'type': 'array', 'items': {'type': 'string'}}, 'how_it_felt': {'type': 'string'}})
    day = await llm.json('her_day', prompt('her_day_scoped'),
                         'HER THOUGHTS\n' + '\n'.join(f'- {t}' for t in thoughts)
                         + '\n\nSHARED\n' + ('\n'.join(f'- {t}' for t in shared) or 'nothing yet')
                         + f'\n\nTIME OF DAY: {time_of_day}\nSEED: {seed}', schema, temperature=1.0)
    items = [dict(kind='her_own', text=f'Today: {_sentence(t)}') for t in day['today']]
    return items + [dict(kind='her_own', text=f'Her day so far has felt {day["how_it_felt"].rstrip(".")}.')]
