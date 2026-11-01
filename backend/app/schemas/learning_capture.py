"""Learning capture's API (docs/learning-system/01-implementation.md): chains are returned as plain JSON (the
records as saved); training examples have a fixed shape."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TrainingExampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    learner: str
    attempt_id: uuid.UUID
    label_source: str
    origin: str
    input: dict[str, Any]
    target: dict[str, Any]
    versions: dict[str, Any]
    evidence: dict[str, Any]
    review_status: str
    eligible: bool
    ineligible_reason: str | None
    decided_at: datetime
    created_at: datetime


class TrainingExampleReview(BaseModel):
    review_status: Literal["unreviewed", "accepted", "rejected"]


class AssessmentNote(BaseModel):
    """The user's correction of an assessment, in their own words."""

    text: str = Field(min_length=1, max_length=2000)
