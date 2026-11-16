"""Executable game skills and practice challenges; learning products use shared schemas."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


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
