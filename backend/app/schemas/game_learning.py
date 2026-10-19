"""Game learning: the reflection model's output contract and the Learning page's API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Reflection model output
# --------------------------------------------------------------------------- #


class ReflectedLesson(BaseModel):
    key: str = Field(description="stable lowercase key like `crafting:place_table_first`; an existing key to update it")
    verdict: Literal["new", "supports", "contradicts"]
    level: Literal["skill", "plan"] = Field(
        description='"skill": how an action behaves (for your hands); "plan": what to do, in what order, when '
                    "(for you, when you set goals and steps)")
    situation: str = Field(description="when this applies, in a few words")
    advice: str = Field(description="what to do, in one sentence")
    evidence: list[str] = Field(description='timeline refs that show it, e.g. ["e4", "e7-e9"]')


class ReflectedProposal(BaseModel):
    key: str = Field(description="stable lowercase key like `skill:mine_gives_up_on_gravel`; reuse an existing one")
    title: str
    area: str = Field(description='the skill name, "new_skill", "runner", "noticing" or "other"')
    problem: str = Field(description="what you wanted vs what happened, citing the outcomes")
    suggestion: str = Field(description="what a programmer should change")
    evidence: list[str]


class Reflection(BaseModel):
    episode: str = Field(description="1-3 sentences, first person, past tense; empty if nothing happened")
    episode_importance: float = Field(description="0 to 1: how worth remembering in conversation")
    lessons: list[ReflectedLesson] = Field(max_length=6)
    proposals: list[ReflectedProposal] = Field(max_length=3)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class LessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    game: str
    normalized_key: str
    situation: str
    advice: str
    level: str
    status: str
    support_count: int
    contradict_count: int
    evidence: list
    created_at: datetime
    updated_at: datetime


class LessonUpdate(BaseModel):
    status: Literal["trial", "retired"]


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    game: str
    normalized_key: str
    title: str
    area: str
    problem: str
    suggestion: str
    occurrences: int
    evidence: list
    status: str
    user_note: str | None
    created_at: datetime
    updated_at: datetime


class ProposalUpdate(BaseModel):
    status: Literal["open", "accepted", "implemented", "rejected"] | None = None
    user_note: str | None = Field(default=None, max_length=2000)


class ReflectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    game: str
    status: str
    attempts: int
    last_error: str | None
    result: dict | None
    created_at: datetime
    processed_at: datetime | None


class LearnedSkillOut(BaseModel):
    """A move she taught herself (stage H2): her hands have it as a skill while enabled."""

    id: uuid.UUID
    game: str
    name: str
    task: str
    code: str
    enabled: bool
    uses: int
    successes: int
    misses_in_row: int
    off_reason: str | None  # set when it switched itself off
    original_code: str | None  # the program as learned, while ``code`` is a tidied copy
    tidy_note: str | None
    tidy_proven: bool
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChallengeOut(BaseModel):
    """A challenge she set herself while exploring (stage H3)."""

    id: uuid.UUID
    game: str
    challenge: str
    why: str
    aim: Literal["discover", "for_us"]
    check: dict | None
    status: Literal["active", "done", "given_up", "replaced", "expired"]
    result: str | None
    learned: list[str]
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class LearnedSkillUpdate(BaseModel):
    enabled: bool
