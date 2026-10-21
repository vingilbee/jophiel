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


class CorrectionCandidate(BaseModel):
    """An explicit correction in the latest USER message (docs/COLUMBINA_MEMORY_CORRECTIONS.md). Code checks
    every field against the words the model was shown before anything is kept."""

    subject: str = Field(min_length=1, max_length=200)  # what it's about, in the user's words ("velvet otter")
    attribute: str | None = Field(default=None, max_length=120)  # which detail of it ("marker"), if said
    previous_value: str | None = Field(default=None, max_length=300)  # what was said before, only if shown
    new_value: str = Field(min_length=1, max_length=300)  # what holds now
    # changed: the thing itself changed; mistaken: the earlier statement or record was wrong; unspecified:
    # the user didn't say which.
    kind: Literal["changed", "mistaken", "unspecified"]
    user_words: str = Field(min_length=3, max_length=500)  # the exact words of the correction


class MemoryExtraction(BaseModel):
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=8)
    corrections: list[CorrectionCandidate] = Field(default_factory=list, max_length=4)
