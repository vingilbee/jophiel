"""Ablation: separate the effects of the intention handoff, the character page and her rhythm.

A 2 x 2 x 2 design over the same scenarios and user cards:
- handoff: ``labels`` (think -> labels -> code -> decide -> wording from labels; the pilot chain) or
  ``intention`` (intention -> reply from the thought -> labels after; frozen ``*_v4`` prompts);
- page: ``v0.2`` (habits as rules; the pilot's) or ``v0.6`` (motives, listening, her world);
- rhythm: her own recent replies counted and shown (``rhythm.py``), or not.

Everything else is held constant across arms: typed memories with explicit ownership and names, her
scoped world and day (her own experiences only, prepared once per scenario), the simulated user,
3 samples per turn, no critic in any arm, the same seeded continuation choice. Measurement is
offline (``rate``); nothing is gated.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import SCHEMA_VERSION, SIGNED, WINDOW, Bundle, Message
from .episode import Env
from .intention import IntentionTeacher, PROMPT_SUFFIX, scenario_from
from .mood import REPLAY_SWAP, START_MOODS, MoodDynamics
from .notes import NOTES_VERSION, NoteBook, events_from_reply
from .notice import notice
from .retrieval import MemoryItem, MemoryPool
from .rhythm import own_rhythm
from .simuser import DELAY_SECONDS, next_turn
from .teacher import Teacher, View, _obj, prompt
from .world import make_scoped_day, sample_typed_world

SAMPLES = 3
PROMPT_SUFFIX['intention-ablation'] = '_v4'   # the intention arms use the frozen round-4 prompts


@dataclass(frozen=True)
class Arm:
    handoff: str   # 'labels' | 'intention'
    page: str      # 'v0.2' | 'v0.6'
    rhythm: bool

    @property
    def name(self) -> str:
        return f'{self.handoff[0].upper()}-p{self.page[-1]}-r{int(self.rhythm)}'


ARMS = [Arm(h, p, r) for h in ('labels', 'intention') for p in ('v0.2', 'v0.6') for r in (False, True)]
BASELINE = Arm('labels', 'v0.2', False)


# --- preparation, shared by every arm ----------------------------------------------------------

async def prepare(env: Env, record: dict[str, Any], world_path: Path) -> dict[str, Any]:
    card = record['card']
    schema = _obj({'facts': {'type': 'array', 'items': {'type': 'string'}}})
    rewritten = await env.llm.json('rewrite_facts', prompt('rewrite_facts'),
                                   f'NAME: {card["name"]}\nFACTS\n' + '\n'.join(f'- {f}' for f in card['facts']), schema)
    facts = rewritten['facts'] if len(rewritten['facts']) == len(card['facts']) else card['facts']
    scenario = scenario_from(record)
    world = sample_typed_world(world_path, scenario.seed)
    thoughts = [w['text'] for w in world if w['kind'] == 'her_own']
    day = await make_scoped_day(env.llm, thoughts, [], scenario.time_of_day, scenario.seed)
    return dict(facts=facts, original_facts=card['facts'], world=world, day=day, shared=[])


# --- one arm, one conversation ----------------------------------------------------------------

async def run_episode(env: Env, arm: Arm, pages: dict[str, str], dynamics: MoodDynamics, record: dict[str, Any],
                      prep: dict[str, Any], *, replays: float = 0.0, notes_version: str = 'notes-v1') -> dict[str, Any]:
    scenario = scenario_from(record)
    card = record['card']
    rng = random.Random(scenario.seed)
    # Mood replays use their own generator, so continuation choices match a run without them.
    replay_rng = random.Random(scenario.seed + 1)
    llm = env.llm
    page = pages[arm.page]
    labels = Teacher(llm, page, dynamics, critic=False)
    intention = IntentionTeacher(llm, page, dynamics, variant='intention-ablation')
    emotional = card.get('emotional_fact', -1)
    pool = MemoryPool([MemoryItem(content=f, importance=0.85 if i == emotional else 0.6)
                       for i, f in enumerate(prep['facts'])] if scenario.memories else [], llm, env.embedding_model)
    await pool.prepare()
    hers = prep['world'] + prep['day']
    her_texts = [m['text'] for m in hers]
    history: list[Message] = []
    notes = NoteBook()
    mood = dict(START_MOODS[scenario.mood])
    previous = None
    seconds = None
    out: dict[str, Any] = dict(schema=SCHEMA_VERSION, notes_version=notes_version, arm=arm.name, handoff=arm.handoff, page=arm.page,
                               rhythm=arm.rhythm, scenario=scenario.to_dict(), prepared=prep, turns=[])
    count = 0
    for turn_index in range(scenario.turns):
        user = await next_turn(llm, card, scenario, history, turn_index=turn_index,
                               last_turn=turn_index == scenario.turns - 1)
        message = user['message'].strip()
        if turn_index:
            seconds = DELAY_SECONDS[user['delay']]
        count += 1
        about = await pool.retrieve(message)
        typed = ([dict(kind='about_them', text=t) for t in about] + [dict(kind='shared', text=t) for t in prep['shared']]
                 + hers)
        bundle = Bundle(turn=count, message=message, window=history[-WINDOW:], notes=list(notes.notes), memories=about,
                        notice=notice(message, seconds_since_reply=seconds, first_user_turn=turn_index == 0),
                        mood=dict(mood), affinity=0.0, time_of_day=scenario.time_of_day, previous=previous,
                        rhythm=own_rhythm(history, her_texts) if arm.rhythm else {}, typed_memories=typed)
        view = View(page, bundle, history, full=True)
        if arm.handoff == 'labels':
            samples = [attempts[-1] for attempts in await labels.samples(view, SAMPLES) if attempts[-1].accepted]
        else:
            samples = await intention.samples(view, SAMPLES)
        if not samples:
            out['stopped'] = 'no sample'
            break
        chosen = rng.choice(samples)
        reply = (await labels.say_it(view, chosen)) if arm.handoff == 'labels' else chosen.extra['reply']
        turn = dict(index=turn_index, user=user, situation_here=turn_index == scenario.situation_turn,
                    bundle=bundle.to_dict(), samples=[[s.to_dict()] for s in samples], noisy=False,
                    coverage={}, continued=chosen.index, reply=reply)
        if replays and arm.handoff == 'intention' and replay_rng.random() < replays:
            # Same message and history, contrasting mood: shows the student what mood does.
            swap = REPLAY_SWAP[scenario.mood]
            swapped = Bundle.from_dict(bundle.to_dict() | {'mood': dict(START_MOODS[swap])})
            replay_samples = await intention.samples(View(page, swapped, history, full=True), SAMPLES)
            turn['replay'] = dict(mood=swap, bundle=swapped.to_dict(), samples=[[s.to_dict()] for s in replay_samples])
        out['turns'].append(turn)
        previous_reply = history[-1].text if history and history[-1].speaker == 'columbina' else None
        notes.apply_message(turn=count, message=message, understand=chosen.understand, previous_reply=previous_reply)
        history.append(Message('user', message, count))
        count += 1
        if notes_version == NOTES_VERSION:
            events = events_from_reply(reply, chosen.decide,
                                       source='description' if arm.handoff == 'intention' else 'plan')
            notes.apply_reply(turn=count, message=message, understand=chosen.understand, reply=reply, events=events)
            turn['events'] = events.to_dict()
        else:
            notes.apply_decision(turn=count, message=message, decide=chosen.decide, understand=chosen.understand,
                                 reply=reply)
        history.append(Message('columbina', reply, count))
        mood = dict(chosen.mood_after)
        previous = dict(understand=chosen.understand, decide={k: chosen.decide[k] for k in ('main', 'second', 'dials')})
        if arm.handoff == 'intention':
            previous['intention'] = chosen.extra['intention']
        if user['ending'] and turn_index >= 3:
            break
    out['history'] = [m.to_dict() for m in history]
    return out


async def run(env: Env, source: Path, ids: list[str], pages: dict[str, str], dynamics: MoodDynamics, world_path: Path,
              *, arms: list[Arm] = ARMS, parallel: int = 12) -> None:
    prepared_dir = env.out / 'prepared'
    prepared_dir.mkdir(parents=True, exist_ok=True)
    records = {i: json.loads((source / 'episodes' / f'{i}.json').read_text()) for i in ids}

    async def prep(i: str) -> dict[str, Any]:
        path = prepared_dir / f'{i}.json'
        if path.exists():
            return json.loads(path.read_text())
        data = await prepare(env, records[i], world_path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        return data

    prepared = dict(zip(ids, await asyncio.gather(*(prep(i) for i in ids)), strict=True))
    gate = asyncio.Semaphore(parallel)
    started = time.monotonic()

    async def one(arm: Arm, i: str) -> None:
        folder = env.out / 'arms' / arm.name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{i}.json'
        if path.exists():
            return
        async with gate:
            try:
                record = await run_episode(env, arm, pages, dynamics, records[i], prepared[i])
            except Exception:
                (folder / f'{i}.error.txt').write_text(traceback.format_exc())
                print(f'[{arm.name} {i}] failed', flush=True)
                return
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            print(f'[{arm.name} {i}] {len(record["turns"])} turns ({env.llm.usage.calls} calls, '
                  f'{round(time.monotonic() - started)} s)', flush=True)

    await asyncio.gather(*(one(a, i) for a in arms for i in ids))
    (env.out / 'usage.json').write_text(json.dumps(env.llm.usage.to_dict(), indent=1))


# --- offline measurement ----------------------------------------------------------------------

RATE_FLAGS = ('borrowed_memory', 'invented_self', 'ignored_correction', 'ignored_topic_change',
              'asked_already_answered', 'dodged_question', 'repeated_herself')
RATE_SCHEMA = _obj({**{f: {'type': 'boolean'} for f in RATE_FLAGS}, 'note': {'type': 'string'}})


def _memory_block(bundle: dict[str, Any]) -> str:
    from .render import lines
    # Untyped records (rounds 1-4, pilot) fall back to their memory and world blocks.
    return '\n'.join(line for field, line in lines(Bundle.from_dict(bundle))
                     if field in ('typed_memories', 'memories', 'world'))


async def rate(env: Env, run_dir: Path, *, parallel: int = 40) -> None:
    gate = asyncio.Semaphore(parallel)

    async def one(path: Path) -> None:
        out = run_dir / 'ratings' / path.parent.name / path.name
        if out.exists():
            return
        record = json.loads(path.read_text())
        convo: list[str] = []
        jobs = []
        for t in record['turns']:
            if t.get('reply') is None:
                break
            convo.append(f'Them: {t["bundle"]["message"]}')
            text = (f'INPUTS\n{_memory_block(t["bundle"])}\n\nCONVERSATION\n' + '\n'.join(convo[:-1])
                    + f'\n\nTHEIR LATEST MESSAGE\n{t["bundle"]["message"]}\n\nREPLY\n{t["reply"]}')
            convo.append(f'Columbina: {t["reply"]}')

            async def call(text=text):
                async with gate:
                    return await env.llm.json('rate', prompt('rate'), text, RATE_SCHEMA)
            jobs.append(call())
        ratings = await asyncio.gather(*jobs)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ratings, ensure_ascii=False, indent=1))

    await asyncio.gather(*(one(p) for p in sorted((run_dir / 'arms').glob('*/*.json'))))


def report(run_dir: Path) -> dict[str, Any]:
    per_arm: dict[str, dict[str, Any]] = {}
    for folder in sorted((run_dir / 'arms').iterdir()):
        rows: list[dict[str, float]] = []
        streaks = []
        for path in sorted(folder.glob('*.json')):
            record = json.loads(path.read_text())
            rating_path = run_dir / 'ratings' / folder.name / path.name
            ratings = json.loads(rating_path.read_text()) if rating_path.exists() else []
            run, longest = 0, 0
            turns = record['turns']
            turns = [t for t in turns if t.get('reply') is not None]
            for k, t in enumerate(turns):
                reply = t['reply']
                asked = '?' in reply
                run = run + 1 if asked else 0
                longest = max(longest, run)
                row = dict(question=float(asked), words=float(len(reply.split())))
                # The user's next message carries their hidden reaction to this reply.
                if k + 1 < len(turns):
                    row['reaction'] = SIGNED[turns[k + 1]['user']['reaction']]
                if k < len(ratings):
                    row.update({f: float(ratings[k][f]) for f in RATE_FLAGS})
                rows.append(row)
            streaks.append(longest)
        keys = sorted({k for r in rows for k in r})
        per_arm[folder.name] = dict(replies=len(rows), longest_question_run=streaks,
                                    **{k: round(sum(r[k] for r in rows if k in r) / max(1, sum(k in r for r in rows)), 3)
                                       for k in keys})
    effects: dict[str, dict[str, float]] = defaultdict(dict)
    metrics = [k for k in next(iter(per_arm.values()), {}) if k not in ('replies', 'longest_question_run')]
    for factor, (a, b) in {'intention_vs_labels': ('I', 'L'), 'page_v06_vs_v02': ('p6', 'p2'),
                           'rhythm_on_vs_off': ('r1', 'r0')}.items():
        for m in metrics:
            hi = [v[m] for n, v in per_arm.items() if a in n.split('-')]
            lo = [v[m] for n, v in per_arm.items() if b in n.split('-')]
            if hi and lo:
                effects[factor][m] = round(sum(hi) / len(hi) - sum(lo) / len(lo), 3)
    return dict(arms=per_arm, main_effects=dict(effects), baseline=BASELINE.name)
