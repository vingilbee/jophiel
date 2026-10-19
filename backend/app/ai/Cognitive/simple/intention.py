"""Teacher variant `intention-v1`: understand -> intention in plain words -> reply -> labels after.

The reply is spoken from her own intention, not from move labels; the labels are extracted
afterwards and describe what she did, so they no longer constrain how she thinks. No critic and no
reply check: this variant exists to see her raw behaviour (docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md,
"Experiment: intention teacher").
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    DIALS, GROUPS, NO_SECOND, SCALE_WORDS, SCHEMA_VERSION, WINDOW, Bundle, Message, Sample,
)
from .episode import Env
from .mood import START_MOODS, update
from .notes import NoteBook
from .notice import notice
from .retrieval import MemoryItem, MemoryPool
from .rhythm import own_rhythm
from .scenario import Scenario
from .simuser import DELAY_SECONDS, next_turn
from .teacher import View, _enum, _obj, mood_text, prompt, understand_schema
from .turn import allowed_groups, flags

VARIANT = 'intention-v4'
# Prompt files per variant; intention-v1 kept for replaying the first experiment.
PROMPT_SUFFIX = {'intention-v1': '_v1', 'intention-v2': '_v2', 'intention-v3': '_v3', 'intention-v4': ''}
SAMPLES = 3


def extract_schema(note_ids: list[str]) -> dict[str, Any]:
    base = understand_schema(note_ids)
    did = _obj({'main': _enum(GROUPS), 'second': _enum([*GROUPS, NO_SECOND]),
                'dials': _obj({d: _enum(SCALE_WORDS) for d in DIALS}), 'did_not_fit': {'type': 'string'}})
    return _obj({'understand': base, 'did': did})


def split_thought(thought: str) -> tuple[str, str]:
    upper = thought.upper()
    at = upper.find('INTENTION')
    if at < 0:
        return thought.strip(), ''
    moment = thought[:at].replace('MOMENT', '', 1).replace('Moment', '', 1).strip(' :*#\n')
    intention = thought[at + len('INTENTION'):].strip(' :*#\n')
    return moment, intention


class IntentionTeacher:
    def __init__(self, llm, page: str, dynamics, variant: str = VARIANT):
        self.llm = llm
        self.page = page
        self.dynamics = dynamics
        self.variant = variant
        self.suffix = PROMPT_SUFFIX[variant]

    async def sample(self, view: View, index: int) -> Sample:
        base = view.inputs()
        thought = await self.llm.text('intend', prompt('intend' + self.suffix), base, reasoning='low', max_tokens=2500)
        _, intention = split_thought(thought)
        reply = await self.llm.text('speak', prompt('speak' + self.suffix),
                                    f'{base}\n\nHER MOOD: {mood_text(view.bundle.mood)}\n\nHER THOUGHT\n{thought}', max_tokens=500)
        note_ids = [n.id for n in view.bundle.notes]
        labels = await self.llm.json(
            'extract', prompt('extract' + self.suffix),
            f'{base}\n\nOPEN NOTE IDS: {", ".join(note_ids) or "none (give an empty note_updates list)"}\n\n'
            f'HER THOUGHT\n{thought}\n\nHER REPLY\n{reply}', extract_schema(note_ids))
        understand = labels['understand']
        seen = {u['note']: u['update'] for u in understand['note_updates'] if u['note'] in note_ids}
        understand['note_updates'] = [{'note': i, 'update': seen.get(i, 'none')} for i in note_ids]
        mood_after = update(view.bundle.mood, understand, self.dynamics)
        turn_flags = flags(view.bundle.notice, understand, view.bundle.notes)
        decide = {k: labels['did'][k] for k in ('main', 'second', 'dials')}
        return Sample(index=index, attempt=0, thought=thought, understand=understand, mood_after=mood_after,
                      flags=turn_flags, allowed=allowed_groups(view.bundle.notice, understand, turn_flags),
                      decide=decide, critic={'verdict': 'none (intention variants have no critic)'}, accepted=True,
                      extra=dict(intention=intention, reply=reply, did_not_fit=labels['did']['did_not_fit']))

    async def samples(self, view: View, count: int = SAMPLES) -> list[Sample]:
        results = await asyncio.gather(*(self.sample(view, i) for i in range(count)), return_exceptions=True)
        return [r for r in results if isinstance(r, Sample)]


def scenario_from(record: dict[str, Any]) -> Scenario:
    data = dict(record['scenario'])
    data.pop('family', None)
    data.setdefault('want', 'just to chat and kill time')
    return Scenario(**{k: data[k] for k in Scenario.__dataclass_fields__})


async def run_episode(env: Env, teacher: IntentionTeacher, scenario: Scenario, card: dict[str, Any],
                      world: list[str]) -> dict[str, Any]:
    rng = random.Random(scenario.seed)
    llm = env.llm
    emotional = card.get('emotional_fact', -1)
    pool = MemoryPool([MemoryItem(content=f, importance=0.85 if i == emotional else 0.6)
                       for i, f in enumerate(card['facts'])] if scenario.memories else [], llm, env.embedding_model)
    await pool.prepare()
    history: list[Message] = []
    notes = NoteBook()
    mood = dict(START_MOODS[scenario.mood])
    previous = None
    seconds = None
    record: dict[str, Any] = dict(schema=SCHEMA_VERSION, variant=teacher.variant, scenario=scenario.to_dict(), card=card,
                                  world=world, turns=[])
    count = 0
    for turn_index in range(scenario.turns):
        user = await next_turn(llm, card, scenario, history, turn_index=turn_index,
                               last_turn=turn_index == scenario.turns - 1)
        message = user['message'].strip()
        if turn_index:
            seconds = DELAY_SECONDS[user['delay']]
        count += 1
        bundle = Bundle(turn=count, message=message, window=history[-WINDOW:], notes=list(notes.notes),
                        memories=await pool.retrieve(message),
                        notice=notice(message, seconds_since_reply=seconds, first_user_turn=turn_index == 0),
                        mood=dict(mood), affinity=0.0, time_of_day=scenario.time_of_day, previous=previous, world=world,
                        rhythm=own_rhythm(history, world) if teacher.variant in ('intention-v3', 'intention-v4') else {})
        samples = await teacher.samples(View(teacher.page, bundle, history, full=True))
        if not samples:
            record['stopped'] = 'no sample'
            break
        chosen = rng.choice(samples)
        reply = chosen.extra['reply']
        record['turns'].append(dict(index=turn_index, user=user, situation_here=turn_index == scenario.situation_turn,
                                    bundle=bundle.to_dict(), samples=[[s.to_dict()] for s in samples],
                                    noisy=False, coverage={}, continued=chosen.index, reply=reply))
        previous_reply = history[-1].text if history and history[-1].speaker == 'columbina' else None
        notes.apply_message(turn=count, message=message, understand=chosen.understand, previous_reply=previous_reply)
        history.append(Message('user', message, count))
        count += 1
        notes.apply_decision(turn=count, message=message, decide=chosen.decide, understand=chosen.understand, reply=reply)
        history.append(Message('columbina', reply, count))
        mood = dict(chosen.mood_after)
        previous = dict(understand=chosen.understand, decide=chosen.decide, intention=chosen.extra['intention'])
        if user['ending'] and turn_index >= 3:
            break
    record['history'] = [m.to_dict() for m in history]
    return record


async def run_experiment(env: Env, teacher: IntentionTeacher, source: Path, ids: list[str], world_path: Path,
                         *, parallel: int = 10) -> None:
    from .world import day_lines, make_day, sample_world
    episodes = env.out / 'episodes'
    episodes.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(parallel)

    async def one(episode_id: str) -> None:
        path = episodes / f'{episode_id}.json'
        if path.exists():
            return
        original = json.loads((source / 'episodes' / f'{episode_id}.json').read_text())
        scenario = scenario_from(original)
        async with gate:
            started = time.monotonic()
            try:
                world = sample_world(world_path, scenario.seed)
                day = None
                if teacher.variant == 'intention-v4':
                    # Her own memories of today, so "what did you do?" has a simple true answer.
                    day = await make_day(env.llm, world, scenario.time_of_day, scenario.seed)
                    world = world + day_lines(day)
                record = await run_episode(env, teacher, scenario, original['card'], world)
                record['her_day'] = day
            except Exception:
                (episodes / f'{episode_id}.error.txt').write_text(traceback.format_exc())
                print(f'[{episode_id}] failed', flush=True)
                return
            record['seconds'] = round(time.monotonic() - started, 1)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            print(f'[{episode_id}] {len(record["turns"])} turns in {record["seconds"]} s ({env.llm.usage.calls} calls)', flush=True)

    await asyncio.gather(*(one(i) for i in ids))
    (env.out / 'usage.json').write_text(json.dumps(env.llm.usage.to_dict(), indent=1))
