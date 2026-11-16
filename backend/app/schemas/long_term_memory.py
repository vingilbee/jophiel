"""Validated output contract for the memory consolidation model."""
from typing import Literal

from pydantic import BaseModel, Field


_DATE = r"^\d{4}-\d{2}-\d{2}$"
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class TimeReading(BaseModel):
    """When a memory is about, as the model read the user's words (services/memory_time.py checks it: the
    words must be the user's, the dates must fit the precision, and what can't be placed has no bounds)."""

    words: str = Field(min_length=1, max_length=200)  # copied exactly from the latest USER message
    start: str | None = Field(default=None, pattern=_DATE)  # first day, on the user's calendar
    end: str | None = Field(default=None, pattern=_DATE)  # last day, inclusive
    precision: Literal["day", "days", "week", "month", "season", "year", "unknown", "repeating"]
    year_inferred: bool = False  # the year wasn't said: you chose it ("in December")
    repeats: Literal["daily", "weekly", "monthly"] | None = None  # a schedule ("Thursdays at 7pm")
    weekdays: list[Weekday] = Field(default_factory=list, max_length=7)
    local_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class EntityMention(BaseModel):
    """Who or what a memory is about. ``ref`` is a KNOWN ENTITIES id ("e2") only when the message makes clear
    it's that one; otherwise null, and code keeps it a new one (services/memory_entities.py)."""

    ref: str | None = Field(default=None, max_length=8)
    kind: Literal["person", "pet", "place", "organization", "activity", "thing", "other"]
    name: str | None = Field(default=None, max_length=120)  # "Nora", if the user named them
    relation: str | None = Field(default=None, max_length=120)  # what it is to the user: "sister", "dog"


class MemoryCandidate(BaseModel):
    operation: Literal["upsert", "forget"] = "upsert"
    kind: Literal["fact", "preference", "goal", "constraint", "episode", "plan"]
    normalized_key: str = Field(min_length=3, max_length=255)
    content: str = Field(min_length=3, max_length=1000)
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    # One claim (stage 3): who or what it's about, which detail, its value, and in what respect.
    entity: EntityMention | None = None
    attribute: str | None = Field(default=None, max_length=120)  # "name", "lives_in", "dislikes"
    value: str | None = Field(default=None, max_length=300)  # "Pretzel"
    qualifier: str | None = Field(default=None, max_length=200)  # "as a child", "at work"
    historical: bool = False  # how things were, not how they are now
    time: TimeReading | None = None
    # The KNOWN key of an older note this claim was taken out of, unchanged, when that note was split.
    split_from: str | None = Field(default=None, max_length=255)
    # A plan or an episode: who else it involves, and where.
    participants: list[EntityMention] = Field(default_factory=list, max_length=4)
    # A plan: its words, copied exactly from the latest USER message.
    user_words: str | None = Field(default=None, max_length=500)
    # A plan already KNOWN (its key reused): what became of it.
    plan_status: Literal["planned", "happened", "cancelled"] = "planned"
    # A plan: the keys of facts (KNOWN, or written in this same answer) it only makes sense while they hold
    # ("visit Nora in Lisbon" relies on where Nora lives). Code keeps only keys of the user's active facts.
    relies_on: list[str] = Field(default_factory=list, max_length=4)


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


class ConflictCandidate(BaseModel):
    """The latest USER message disagrees with a KNOWN memory without saying it changed or was wrong
    (docs/COLUMBINA_MEMORY_CONFLICTS.md). Kept unresolved, never applied: her mind decides what to do."""

    known_key: str = Field(min_length=3, max_length=255)  # the KNOWN memory's key, exactly
    stated: str = Field(min_length=1, max_length=300)  # what the message says about it, in a few words
    user_words: str = Field(min_length=3, max_length=500)  # copied exactly from the latest USER message


class ForgetRequest(BaseModel):
    """The latest USER message asks her to forget something (services/memory_forget.py). Code finds what it
    covers among the stored memories; this only says what was asked."""

    about: str = Field(min_length=1, max_length=200)  # what to forget, in a few words ("my knee")
    user_words: str = Field(min_length=3, max_length=500)  # the request, copied exactly from the USER message


class MemoryExtraction(BaseModel):
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=12)
    corrections: list[CorrectionCandidate] = Field(default_factory=list, max_length=4)
    conflicts: list[ConflictCandidate] = Field(default_factory=list, max_length=4)
    # keys of KNOWN memories whose [unresolved: ...] disagreement the latest USER message settles
    settled: list[str] = Field(default_factory=list, max_length=4)
    forget: list[ForgetRequest] = Field(default_factory=list, max_length=3)
