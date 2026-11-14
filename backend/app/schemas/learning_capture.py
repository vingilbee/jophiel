"""Learning capture's API (docs/learning-system/01-implementation.md): chains are returned as plain JSON (the
records as saved)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AssessmentNote(BaseModel):
    """The user's correction of an assessment, in their own words."""

    text: str = Field(min_length=1, max_length=2000)
