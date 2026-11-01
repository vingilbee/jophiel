"""Recall's contracts (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §5, §6): what a caller asks, what
comes back, and the stable reference to a memory. Pure: no database, no model.

A result is records plus what the search actually covered, never pre-rendered text: whoever shows it
renders it. ``no_match`` means a bounded search that ran found nothing; ``failed`` means it couldn't
run; ``partial`` means some stores couldn't be read; ``not_searched`` means nobody looked.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

Kind = Literal["fact", "episode", "event", "item", "message"]
# The stores a memory search reads. ``message`` (a transcript passage: what was said, not a memory) is its own
# search (services/transcript_search.py) and only ever a reference here.
KINDS: tuple[Kind, ...] = ("fact", "episode", "event", "item")
REF_KINDS: tuple[Kind, ...] = (*KINDS, "message")
Purpose = Literal["reply", "hard_thought", "explicit_search"]
Intent = Literal["relevance", "current", "historical"]
Basis = Literal["occurred", "scheduled", "recorded"]
Status = Literal["not_searched", "matched", "no_match", "partial", "failed"]
# How a record was found.
# ``reference``: the message names it ("Leo") or points at it ("my sister" for a fact about their sister).
Match = Literal["direct_reference", "named_context", "lexical", "semantic", "temporal", "standing", "reference"]
# Whether it describes now: a past event, or something that no longer holds.
Applicability = Literal["current", "past", "no_longer_current", "unknown"]

QUERY_LIMIT = 2000
AUTO_LIMIT = 6
TOOL_LIMIT = 12
TIMELINE_LIMIT = 20
AUTO_BUDGET_CHARS = 8000  # about 2,000 tokens
TOOL_CALLS_PER_REPLY = 3
PRIOR_REFS = 4  # references carried from the exchange before


class RecallInvalid(ValueError):
    """A request that can't be run as asked (bad range, unknown reference shape): never silently fixed."""


@dataclass(frozen=True)
class MemoryRef:
    """A memory's full key: its store and primary key. Rendered ``event:123``, ``fact:<uuid>``."""

    kind: Kind
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"

    @classmethod
    def parse(cls, text: str) -> MemoryRef:
        kind, _, key = (text or "").strip().strip("[]").partition(":")
        if kind not in REF_KINDS or not key:
            raise RecallInvalid(f"not a memory reference: {text[:60]!r}")
        if kind == "event":
            if not key.isdigit():
                raise RecallInvalid(f"not a memory reference: {text[:60]!r}")
        else:
            try:
                key = str(uuid.UUID(key))
            except ValueError as exc:
                raise RecallInvalid(f"not a memory reference: {text[:60]!r}") from exc
        return cls(kind, key)  # type: ignore[arg-type]


@dataclass(frozen=True)
class TimeRange:
    """Half-open ``[start, end)``, timezone-aware, on one time basis. Either end may be open."""

    basis: Basis
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        for moment in (self.start, self.end):
            if moment is not None and moment.tzinfo is None:
                raise RecallInvalid("time range bounds need a time zone")
        if self.start is None and self.end is None:
            raise RecallInvalid("a time range needs a start or an end")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise RecallInvalid("time range ends before it starts")

    def contains(self, moment: datetime | None) -> bool | None:
        """None when the moment is unknown: the record can't be placed in the range."""
        if moment is None:
            return None
        return (self.start is None or moment >= self.start) and (self.end is None or moment < self.end)


@dataclass(frozen=True)
class RecallRequest:
    user_id: uuid.UUID
    query: str
    purpose: Purpose
    now: datetime
    zone: str = "UTC"  # the user's IANA zone; UTC when the client didn't say
    conversation_id: uuid.UUID | None = None
    focus_context_id: uuid.UUID | None = None
    prior_refs: tuple[MemoryRef, ...] = ()
    context_ids: tuple[uuid.UUID, ...] = ()
    context_query: str | None = None
    kinds: tuple[Kind, ...] = KINDS
    time_range: TimeRange | None = None
    intent: Intent = "relevance"
    limit: int | None = None  # None: AUTO_LIMIT for replies and thoughts, TOOL_LIMIT for a search
    budget_chars: int = AUTO_BUDGET_CHARS
    page_token: str | None = None
    # Open items of this context are already in her working view: not repeated.
    skip_open_items_of: uuid.UUID | None = None

    def bounded(self) -> RecallRequest:
        if len(self.query or "") > QUERY_LIMIT:
            raise RecallInvalid(f"query is longer than {QUERY_LIMIT} characters")
        if self.now.tzinfo is None:
            raise RecallInvalid("now needs a time zone")
        kinds = tuple(k for k in KINDS if k in self.kinds)
        if not kinds:
            raise RecallInvalid("no memory kinds to search")
        cap = TOOL_LIMIT if self.purpose == "explicit_search" else AUTO_LIMIT
        return replace(self, kinds=kinds, limit=max(1, min(self.limit or cap, cap)),
                       budget_chars=max(500, min(self.budget_chars, AUTO_BUDGET_CHARS * 2)),
                       prior_refs=self.prior_refs[:PRIOR_REFS])


@dataclass(frozen=True)
class Evidence:
    role: str
    source: str  # message, observation, experience, session_event, thought, conversation, game_session
    source_id: str


@dataclass(frozen=True)
class TimeSpan:
    """When a memory is about, as stored (services/memory_time.py): the user's words, the interval read from them
    when they were said (half-open, or none when it couldn't be placed), and how sure that reading is."""

    words: str
    precision: str  # day, days, week, month, season, year, unknown, repeating
    start: datetime | None
    end: datetime | None
    inferred: bool  # part of the date (usually the year) wasn't said
    zone: str | None  # None: the user's zone wasn't known (read as UTC)
    said_at: datetime | None
    repeats: str | None = None  # a schedule in words: "weekly on Thursday at 19:00"


@dataclass(frozen=True)
class MemoryRecord:
    ref: MemoryRef
    subkind: str  # a fact's kind ("preference"), an event's kind ("died"), an item's kind ("goal")
    content: str
    context_id: uuid.UUID | None
    context_label: str | None  # None: unknown, or an older record kept before contexts existed
    occurred_at: datetime | None  # when it happened (or began); None when unknown
    occurred_until: datetime | None  # end of the period, when it was one
    scheduled_at: datetime | None  # a due or planned time, never an occurrence
    recorded_at: datetime  # when it was written down
    updated_at: datetime | None
    status: str
    epistemic: str  # observed, reported, inferred, or "unknown" for older records
    evidence: tuple[Evidence, ...] | None  # None: not available for this record
    fingerprint: str
    matched: tuple[Match, ...]
    applicability: Applicability
    lifecycle: str | None = None  # e.g. "its game session has ended", "its context is closed"
    source_label: str | None = None  # the source whose words it quotes, e.g. "Minecraft"
    corrected: bool = False  # an item a user's correction revised since it was first noted
    score: float = 0.0
    owner: str | None = None  # an item's: whose it is (user, columbina, shared)
    reason: str | None = None  # an item from her own thinking: why she concluded it
    time: TimeSpan | None = None  # what the user said about when (stage 3)
    # Who or what it's about or involves, in words ("their sister Nora"), and those entities' ids.
    about: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    user_words: str | None = None  # a plan's own words, as the user said them
    # A transcript passage (kind ``message``): its conversation, and whether something in it was asked to be
    # forgotten (it's still history the user can ask about; it's never remembered again from it).
    conversation_id: uuid.UUID | None = None
    forgotten: bool = False


_SUSPECTED = {"open": "You suspected (not confirmed)", "supported": "You suspected, later confirmed",
              "disproved": "You suspected, later shown wrong", "unresolved": "You suspected, never settled"}


def origin_label(record: MemoryRecord) -> str | None:
    """Where a memory came from and how sure it is, in her words: what the user told her, what she
    observed, what she suspected or decided. Her own reasoning is never put as a fact."""
    if record.ref.kind == "item":
        if record.subkind == "hypothesis":
            return _SUSPECTED.get(record.status, "You suspected")
        if record.subkind == "decision":
            return "You decided" if record.status == "active" else "You had decided (since changed)"
        if record.subkind == "plan":
            return "The user plans" if record.owner == "user" else "You and the user plan"
        if record.owner == "columbina" and record.subkind == "intention":
            return "You meant to"
        if record.owner == "columbina" and record.subkind == "question":
            return "You wondered"
        if record.subkind == "commitment":
            return "You promised" if record.owner == "columbina" else "The user promised you"
        if record.epistemic == "reported":
            return "The user told you"
        return "You concluded" if record.epistemic == "inferred" else None
    if record.ref.kind == "message":
        # Unmistakable either way: she reads these and quotes them back ("I said..." / "you said...").
        return ("Your own words (you, Columbina, said this to them: quote it as \"I said\")"
                if record.subkind == "columbina" else
                "The user's own words (they wrote this to you: quote it as \"you said\")")
    if record.ref.kind == "fact":
        return "The user told you"
    if record.ref.kind == "event":
        if record.epistemic == "observed":
            return "You observed"
        if record.epistemic == "reported" and record.source_label:
            return f"{record.source_label} reported"
        return "Your own thinking" if record.epistemic == "inferred" else None
    return None


@dataclass
class RecallResult:
    status: Status
    records: list[MemoryRecord] = field(default_factory=list)
    searched_kinds: tuple[Kind, ...] = ()
    scope: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    next_page: str | None = None

    @classmethod
    def not_searched(cls, reason: str) -> RecallResult:
        return cls("not_searched", coverage={"reason": reason})

    @classmethod
    def failed(cls, code: str, kinds: tuple[Kind, ...] = ()) -> RecallResult:
        return cls("failed", searched_kinds=(), errors=[code], coverage={"attempted": list(kinds)})

    def refs(self) -> list[MemoryRef]:
        return [record.ref for record in self.records]
