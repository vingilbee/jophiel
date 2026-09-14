"""Stable contracts shared by cognitive-system stages."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

SignalValue: TypeAlias = bool | int | float | str | None


class PerceptionSignal(BaseModel):
    """An optional, independently-produced observation.

    New signal names may be added without changing the stable perception fields.
    """

    model_config = ConfigDict(extra="forbid")

    value: SignalValue
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1)


class Perception(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    is_question: bool
    is_request: bool
    is_directed_at_agent: bool
    novelty: float = Field(ge=0.0, le=1.0)
    ambiguity: float = Field(ge=0.0, le=1.0)
    signals: dict[str, PerceptionSignal] = Field(default_factory=dict)


class Intent(StrEnum):
    QUESTION = "question"
    REQUEST = "request"
    INFORM = "inform"
    CLARIFY = "clarify"
    FEEDBACK = "feedback"
    GREETING = "greeting"
    UNKNOWN = "unknown"


class IntentScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[IntentScore] = Field(default_factory=list)
    needs_clarification: bool
    source: str


class CognitiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perception: Perception
    interpretation: Interpretation
