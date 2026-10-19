"""The frozen teacher `teacher-2026-10-19`: the provisional teacher for the next training batch.

Arm `I-p6-r1` of the ablation (intention handoff, character page v0.6, her rhythm on), with typed
memories (explicit ownership and names), her scoped world and day, no critic, 3 samples, mood
replays on about 1 turn in 4. New scenarios and user cards, not the ten reused in the experiments.
What it's trusted to teach: conversation and character behaviour, and acts/tone/requests as ordinary
supervision. Feelings, appraisal and any exchange cue are experimental targets.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
from pathlib import Path
from typing import Any

from .ablation import Arm, prepare, run_episode
from .episode import Env
from .mood import MoodDynamics
from .notes import NOTES_VERSION
from .scenario import Scenario
from .simuser import make_card
from .teacher import PROMPTS

VERSION = 'teacher-2026-10-19'
ARM = Arm('intention', 'v0.6', True)
REPLAYS = 0.25
# Batch teacher1 recorded her notes from move labels (notes-v1); later batches from her actual reply.
PROMPT_FILES = ('intend_v4.txt', 'speak_v4.txt', 'extract_v4.txt', 'her_day_scoped.txt', 'rewrite_facts.txt',
                'user_card.txt', 'user_turn.txt')


def fingerprint(page: Path, world: Path, mood: Path) -> dict[str, str]:
    """Hashes of every input, so a later run can tell whether the teacher drifted."""
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return {'page': sha(page), 'world': sha(world), 'mood': sha(mood),
            **{name: sha(PROMPTS / name) for name in PROMPT_FILES}}


async def generate(env: Env, scenarios: list[Scenario], page: str, dynamics: MoodDynamics, world: Path,
                   *, parallel: int = 10, notes_version: str = NOTES_VERSION) -> None:
    folder = env.out / 'episodes'
    folder.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(parallel)
    started = time.monotonic()

    async def one(scenario: Scenario) -> None:
        path = folder / f'{scenario.id}.json'
        if path.exists():
            return
        async with gate:
            try:
                card = await make_card(env.llm, scenario)
                if scenario.situation == 'memory':
                    emotional = card.get('emotional_fact', -1)
                    scenario.memory_fact_index = emotional if 0 <= emotional < len(card['facts']) else 0
                source: dict[str, Any] = dict(scenario=scenario.to_dict(), card=card)
                prep = await prepare(env, source, world)
                record = await run_episode(env, ARM, {'v0.6': page}, dynamics, source, prep, replays=REPLAYS,
                                           notes_version=notes_version)
            except Exception:
                (folder / f'{scenario.id}.error.txt').write_text(traceback.format_exc())
                print(f'[{scenario.id}] failed', flush=True)
                return
            record.update(teacher_version=VERSION, card=card)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
            print(f'[{scenario.id}] {len(record["turns"])} turns ({env.llm.usage.calls} calls, '
                  f'{round(time.monotonic() - started)} s)', flush=True)

    await asyncio.gather(*(one(s) for s in scenarios))
    (env.out / f'usage-{VERSION}.json').write_text(json.dumps(env.llm.usage.to_dict(), indent=1))
