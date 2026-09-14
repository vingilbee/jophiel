"""Validated output contract for the memory consolidation model."""
from typing import Literal

from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    operation: Literal["upsert", "forget"] = "upsert"
    kind: Literal["fact", "preference", "goal", "constraint", "episode"]
    normalized_key: str = Field(min_length=3, max_length=255)
    content: str = Field(min_length=3, max_length=1000)
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class MemoryExtraction(BaseModel):
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=8)
