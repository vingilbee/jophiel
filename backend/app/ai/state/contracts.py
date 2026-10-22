"""The shared view: one turn's bounded, versioned picture of her state (docs/SHARED_STATE_REFACTOR_SPEC.md).

Pure: no database, no settings. Adapters in ``app/services/shared_state.py`` fill it from the stores that own
each part, and the view never replaces them. Every section carries a ``Projection`` header: who owns it, which
version it reflects, the records it came from, and its ``status``. ``unavailable`` and ``disabled`` are never a
negative answer: a section that wasn't read says nothing about what's true.

Step 3 of the spec fills the minimum slice (envelope, conversation, memory context, cognition, thinking
decision, work, output) plus personality and health. Inner state, activities, situation and mind are present
and explicitly ``disabled``/``unavailable`` until their adapters are wired.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

SCHEMA_VERSION = "shared-view-v0"

Status = Literal["available", "pending", "stale", "unavailable", "disabled"]
# Where a cognition field came from: the trained student, a written rule, or nobody (the field wasn't assessed).
Origin = Literal["student_1", "policy", "jev", "explicit_user_instruction", "not_assessed"]
Action = Literal["respond", "acknowledge", "ask", "defer", "silent"]
Preference = Literal["speak", "silent", "defer"]
Mode = Literal["live", "shadow", "disabled"]


@dataclass(frozen=True)
class SourceRef:
    """A record a projection came from, in its owner's own key (``message:<uuid>``, ``event:42``)."""

    kind: str
    id: str
    version: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}" + (f"@{self.version}" if self.version else "")


@dataclass(frozen=True)
class Projection:
    owner: str
    status: Status
    version: str | None = None
    updated_at: datetime | None = None
    source_refs: tuple[SourceRef, ...] = ()
    reason: str | None = None  # why it's not available, or what it covers

    @classmethod
    def off(cls, owner: str, reason: str) -> Projection:
        return cls(owner, "disabled", reason=reason)

    @classmethod
    def missing(cls, owner: str, reason: str) -> Projection:
        return cls(owner, "unavailable", reason=reason)


@dataclass(frozen=True)
class Envelope:
    schema_version: str
    user_id: uuid.UUID
    # The view is scoped to one conversation: its revision is that conversation's message sequence (a
    # database fact), and the trigger is the event the view was built for. No global per-user counter yet.
    conversation_id: uuid.UUID | None
    revision: int | None
    captured_at: datetime
    last_event_id: str | None


@dataclass(frozen=True)
class MessageRef:
    id: uuid.UUID
    role: str
    sequence: int | None
    status: str | None
    heard_chars: int | None = None  # an interrupted voice reply: how much of it was heard


@dataclass(frozen=True)
class NoteRef:
    """A derived note (not a raw message): its kind and the turn it's about. Owner: the cognitive notebook."""

    kind: str
    turn: int | None


@dataclass(frozen=True)
class ConversationView:
    meta: Projection
    conversation_id: uuid.UUID | None = None
    revision: int | None = None
    trigger_message_id: uuid.UUID | None = None
    latest_message_id: uuid.UUID | None = None
    recent: tuple[MessageRef, ...] = ()
    notes: Projection = field(default_factory=lambda: Projection.off("cognitive_live", "student off"))
    note_refs: tuple[NoteRef, ...] = ()
    summary: Projection = field(default_factory=lambda: Projection.missing("chat", "no summary owner exists"))
    commitments: Projection = field(default_factory=lambda: Projection.off("memory_manager", "not enabled"))
    # Messages committed after the trigger: the view was built for a message that's no longer the latest.
    newer_than_trigger: int = 0


@dataclass(frozen=True)
class CacheEntry:
    ref: str  # the memory's own reference, ``event:42``
    fingerprint: str  # memory's version token: a change since means a different fingerprint
    applicability: str  # memory's word: "current", "no_longer_current", ...
    corrected: bool = False
    content: str = ""  # re-read from the record now, not extracted from the mouth's rendered prompt


@dataclass(frozen=True)
class MemoryContextView:
    meta: Projection
    # What was in hand without a search: the answer before's references, re-read by memory now.
    entries: tuple[CacheEntry, ...] = ()
    # References the cache held that memory no longer has ("deleted" for one whose reference row went with it).
    gone: tuple[str, ...] = ()
    recall_status: str = "not_searched"  # this turn ran no fresh search (step 3)
    rendered: str | None = None  # bounded, for the mouth's prompt
    legacy: str | None = None  # the fact/episode lines, for the student's "about them" input


@dataclass(frozen=True)
class Interpretation:
    acts: tuple[str, ...]
    tone: str
    request: str  # no_request / talk_only / outside_ability
    probabilities: dict[str, dict[str, float]]
    model: str  # Student 1's schema and weights, the version this reading depends on


@dataclass(frozen=True)
class Assessed:
    """One routing field with where it came from. ``not_assessed`` means nobody decided it, not "no"."""

    value: Any
    origin: Origin
    reason: str | None = None


@dataclass(frozen=True)
class MissingEvidence:
    question: str
    reason: str


@dataclass(frozen=True)
class EvidenceAssessment:
    assessment: Literal["none_missing", "missing", "not_assessed"]
    origin: Origin
    missing: tuple[MissingEvidence, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class CognitionView:
    meta: Projection
    phase: Literal["user_input", "thought_result", "autonomous_event"] = "user_input"
    trigger_ref: str | None = None
    input_versions: dict[str, str] = field(default_factory=dict)
    interpretation: Interpretation | None = None
    conversational_action: Assessed = Assessed(None, "not_assessed")
    missing_evidence: EvidenceAssessment = EvidenceAssessment("not_assessed", "not_assessed")
    communication_preference: Assessed = Assessed(None, "not_assessed")
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThinkingDecision:
    meta: Projection
    trigger_ref: str | None = None
    required: bool | None = None  # None: not assessed or uncertain (never "not needed")
    depth: str | None = None
    decider: Literal["jev", "explicit_instruction", "none"] = "none"
    reason: str | None = None
    input_versions: dict[str, str] = field(default_factory=dict)
    choice: str | None = None  # the decider's own word: not_required / required / uncertain
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallDecision:
    """Whether memory must be looked up before the mouth (step 4: Jev). ``choice`` None: not assessed.
    ``not_required`` permits going on to the mouth; it never certifies that she knows the answer. Whether to
    ask what they meant or to use a tool is cognition's and the mouth's, not this decision's."""

    meta: Projection
    choice: str | None = None  # not_required / required / uncertain
    origin: Origin = "not_assessed"
    probabilities: dict[str, float] = field(default_factory=dict)
    assessment_id: str | None = None


@dataclass(frozen=True)
class WorkView:
    meta: Projection
    work_id: str | None = None
    kind: str = "reply"
    status: str | None = None
    attempt: int | None = None
    lease_expires_at: datetime | None = None
    # How a stop reaches this work: the turn's cancel key (worker) or the socket's stop event (local fallback).
    cancellation: str | None = None
    deadline_at: datetime | None = None
    result_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputView:
    meta: Projection
    channel: str = "web"
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    reply_message_id: uuid.UUID | None = None
    # What makes this the one reply for its turn: the chat job's lease plus the assistant id derived from the
    # turn (one row per turn), or the socket that owns an in-process turn.
    reservation: str | None = None
    voice: bool = False
    acknowledgement: str = "none"  # step 3 never acknowledges: there's no pending work to acknowledge


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    mode: Mode
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class SharedView:
    envelope: Envelope
    personality: Projection
    conversation: ConversationView
    memory: MemoryContextView
    cognition: CognitionView
    thinking: ThinkingDecision
    work: WorkView
    output: OutputView
    inner_state: Projection
    activities: Projection
    situation: Projection
    mind: Projection
    health: tuple[ComponentHealth, ...] = ()
    recall: RecallDecision = field(default_factory=lambda: RecallDecision(
        Projection.off("jev", "no recall assessment")))

    def dependencies(self) -> dict[str, str | None]:
        """The versions this turn's route rests on, for the trace."""
        return {
            "conversation_revision": str(self.conversation.revision) if self.conversation.revision is not None else None,
            "trigger_message": str(self.conversation.trigger_message_id) if self.conversation.trigger_message_id else None,
            "memory_cache": ",".join(f"{e.ref}#{e.fingerprint[:12]}" for e in self.memory.entries) or None,
            "personality": self.personality.version,
            **{f"cognition.{k}": v for k, v in self.cognition.input_versions.items()},
        }


def to_jsonable(value: Any) -> Any:
    """Dataclasses, uuids and datetimes to plain JSON (for the trace)."""
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    return value
