"""Her practice notes as the System page reads and edits them (api/routes/practice.py)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PracticeNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    surface: str
    place: str
    tool: str | None
    status: Literal["known", "to_try"]
    source: Literal["practice", "user"]
    text: str
    evidence: str | None
    revision: int
    pinned: bool
    created_at: datetime
    updated_at: datetime


class PracticeNoteCreate(BaseModel):
    place: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=800)
    tool: str | None = Field(None, max_length=40)
    surface: Literal["phone"] = "phone"


class PracticeNoteUpdate(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=800)
    status: Literal["known", "to_try"] | None = None
    pinned: bool | None = None  # pin or unpin: a review never changes a pinned note


class PlaceEntryOut(BaseModel):
    """One thing she knows of a place that holds next time (services/place_memory.py)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    world: str
    kind: str
    text: str
    near_id: uuid.UUID | None
    revision: int
    seen_at: datetime


class PlaceLayoutOut(BaseModel):
    """A place she uses, with what she knows of it that holds next time (services/place_memory.py)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    surface: str
    place: str
    her_layout: str
    revision: int
    created_at: datetime
    updated_at: datetime
    entries: list[PlaceEntryOut]


class PlaceLayoutUpdate(BaseModel):
    her_layout: str | None = Field(None, max_length=400)


class PlaceEntryUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=240)
