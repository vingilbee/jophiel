"""One conversation episode in the teacher environment, and the run that collects many.

Per turn: build the bundle -> 5 full-view samples (each a complete, critic-reviewed chain) ->
continue from one accepted sample (its reply goes to the simulated user; its mood, notes and labels
become the next turn's state). Some turns also get mood replays (same message, contrasting mood)
and a bounded-teacher run (only the student's bundle), the gap measurement.
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

from . import render
from .contracts import SCHEMA_VERSION, WINDOW, Bundle, Message, Sample
from .llm import LLM
from .mood import REPLAY_SWAP, START_MOODS, MoodDynamics
from .notes import NoteBook
from .notice import notice
from .retrieval import MemoryItem, MemoryPool
from .scenario import Scenario
from .simuser import DELAY_SECONDS, make_card, next_turn
from .teacher import SAMPLES, Teacher, View

REPLAY_EVERY = 4        # a mood replay on about 1 turn in 4
REPLAY_SAMPLES = 3
BOUNDED_EVERY = 5       # the bounded-teacher comparison on about 1 turn in 5
BOUNDED_SAMPLES = 2


@dataclass
class Env:
    llm: LLM
    teacher: Teacher
    embedding_model: str
    out: Path


def _final(attempts: list[Sample]) -> Sample | None:
    last = attempts[-1]
    return last if last.accepted else None


def _turn_record(bundle: Bundle, samples: list[list[Sample]]) -> dict[str, Any]:
    return dict(bundle=bundle.to_dict(), samples=[[a.to_dict() for a in attempts] for attempts in samples])


async def run_episode(env: Env, scenario: Scenario) -> dict[str, Any]:
    rng = random.Random(scenario.seed)
    llm, teacher = env.llm, env.teacher
    card = await make_card(llm, scenario)
    emotional = card.get('emotional_fact', -1)
    if scenario.situation == 'memory':
        scenario.memory_fact_index = emotional if 0 <= emotional < len(card['facts']) else 0
    pool = MemoryPool([MemoryItem(content=f, importance=0.85 if i == emotional else 0.6)
                       for i, f in enumerate(card['facts'])] if scenario.memories else [], llm, env.embedding_model)
    await pool.prepare()

    history: list[Message] = []
    notes = NoteBook()
    mood = dict(START_MOODS[scenario.mood])
    previous: dict[str, Any] | None = None
    seconds_since_reply: float | None = None
    record: dict[str, Any] = dict(schema=SCHEMA_VERSION, scenario=scenario.to_dict(), card=card, turns=[])
    count = 0

    for turn_index in range(scenario.turns):
        last_turn = turn_index == scenario.turns - 1
        user = await next_turn(llm, card, scenario, history, turn_index=turn_index, last_turn=last_turn)
        message = user['message'].strip()
        if turn_index:
            seconds_since_reply = DELAY_SECONDS[user['delay']]
        count += 1
        bundle = Bundle(
            turn=count, message=message, window=history[-WINDOW:], notes=list(notes.notes),
            memories=await pool.retrieve(message), notice=notice(message, seconds_since_reply=seconds_since_reply,
                                                                 first_user_turn=turn_index == 0),
            mood=dict(mood), affinity=0.0, time_of_day=scenario.time_of_day, previous=previous)
        full = View(teacher.page, bundle, history, full=True)
        samples = await teacher.samples(full)
        accepted = [s for s in (_final(a) for a in samples) if s]
        turn: dict[str, Any] = dict(index=turn_index, user=user, situation_here=turn_index == scenario.situation_turn,
                                    **_turn_record(bundle, samples))
        turn['noisy'] = len(accepted) < 3

        extras = []
        if turn_index and rng.random() < 1 / BOUNDED_EVERY:
            extras.append(('bounded', None))
        if rng.random() < 1 / REPLAY_EVERY:
            extras.append(('replay', REPLAY_SWAP[scenario.mood]))

        async def bounded() -> list[list[Sample]]:
            return await teacher.samples(View(teacher.page, bundle, history, full=False), BOUNDED_SAMPLES)

        async def replay(mood_name: str) -> tuple[Bundle, list[list[Sample]]]:
            swapped = Bundle.from_dict(bundle.to_dict() | {'mood': dict(START_MOODS[mood_name])})
            return swapped, await teacher.samples(View(teacher.page, swapped, history, full=True), REPLAY_SAMPLES)

        jobs = [bounded() if kind == 'bounded' else replay(arg) for kind, arg in extras]
        if not accepted:
            turn['continued'] = None
            record['turns'].append(turn)
            record['stopped'] = 'no accepted sample'
            break
        chosen = rng.choice(accepted)
        reply_job = teacher.say_it(full, chosen)
        results = await asyncio.gather(reply_job, *jobs)
        reply = results[0]
        for (kind, arg), result in zip(extras, results[1:], strict=True):
            if kind == 'bounded':
                turn['bounded'] = [[a.to_dict() for a in attempts] for attempts in result]
            else:
                swapped, replay_samples = result
                turn['replay'] = dict(mood=arg, **_turn_record(swapped, replay_samples))

        # The gap check: did the notes carry what older messages contributed?
        coverage = {}
        notes_text = '\n'.join(line for field, line in render.lines(bundle) if field == 'notes')
        for s in accepted:
            used = s.decide.get('used_older_messages') or []
            if used:
                coverage[str(s.index)] = await teacher.coverage(notes_text, used, history)
        turn['coverage'] = coverage
        turn['continued'] = chosen.index
        turn['reply'] = reply

        previous_reply = history[-1].text if history and history[-1].speaker == 'columbina' else None
        notes.apply_message(turn=count, message=message, understand=chosen.understand, previous_reply=previous_reply)
        history.append(Message('user', message, count))
        count += 1
        notes.apply_decision(turn=count, message=message, decide=chosen.decide, understand=chosen.understand,
                             reply=reply)
        history.append(Message('columbina', reply, count))
        mood = dict(chosen.mood_after)
        previous = dict(understand=chosen.understand, decide={k: chosen.decide[k] for k in ('main', 'second', 'dials')})
        turn['notes_after'] = [n.to_dict() for n in notes.notes]
        record['turns'].append(turn)
        if user['ending'] and turn_index >= 3:
            break
    record['history'] = [m.to_dict() for m in history]
    return record


async def run(env: Env, scenarios: list[Scenario], *, parallel: int = 6, usage_name: str = 'usage.json') -> None:
    episodes = env.out / 'episodes'
    episodes.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(parallel)
    started = time.monotonic()

    async def one(scenario: Scenario) -> None:
        path = episodes / f'{scenario.id}.json'
        if path.exists():
            return
        async with gate:
            t0 = time.monotonic()
            try:
                record = await run_episode(env, scenario)
            except Exception:
                (episodes / f'{scenario.id}.error.txt').write_text(traceback.format_exc())
                print(f'[{scenario.id}] failed', flush=True)
                return
            record['seconds'] = round(time.monotonic() - t0, 1)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            print(f'[{scenario.id}] {len(record["turns"])} turns in {record["seconds"]} s '
                  f'({env.llm.usage.calls} calls so far, {round(time.monotonic() - started)} s)', flush=True)

    await asyncio.gather(*(one(s) for s in scenarios))
    (env.out / usage_name).write_text(json.dumps(env.llm.usage.to_dict(), indent=1))


__all__ = ['Env', 'run', 'run_episode', 'SAMPLES']
