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
