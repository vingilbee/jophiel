"""Canonical immutable Dialogue Working Memory V0 contracts.

Dialogue Working Memory is the short-term, conversation-scoped semantic
projection of what actually happened earlier in one conversation.  It is not
long-term, relationship, episodic, semantic, or learning memory.

    ChatMessage rows are the authoritative record of conversation events.
    Dialogue Working Memory is an immutable, reproducible semantic projection.
    Only content actually exposed to the user may be remembered as assistant speech.
    Shadow candidates are never remembered as delivered speech.

This module holds cross-boundary contracts only: source events, memory items,
snapshots, the turn-local context view, omissions, failures, versions, and the
canonical semantic digest.  It imports no database, cache, clock, network,
provider SDK, or chat agent.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator

from .interpretation import FrozenModel
from .realization import SensitivityClass, valid_text

# ---------------------------------------------------------------------------
# Versions (exact V0 identifiers)
# ---------------------------------------------------------------------------

DIALOGUE_MEMORY_CONTRACT_VERSION = "dialogue-working-memory-contract-v0.1"
DIALOGUE_MEMORY_ITEM_VERSION = "dialogue-working-memory-item-v0.1"
DIALOGUE_MESSAGE_ORDER_VERSION = "dialogue-message-order-v0.1"
DIALOGUE_MESSAGE_ELIGIBILITY_VERSION = "dialogue-message-eligibility-v0.1"
DIALOGUE_MEMORY_UPDATER_VERSION = "dialogue-working-memory-updater-v0.1"
DIALOGUE_MEMORY_PROJECTION_VERSION = "dialogue-working-memory-projection-v0.1"
DIALOGUE_MEMORY_CACHE_VERSION = "dialogue-working-memory-cache-v0.1"
DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION = "dialogue-working-memory-digest-v0.1"

# A fixed namespace: every dialogue identity is a UUID5 of versioned lineage,
# stable across processes, retries, and replays (never UUID4 or wall time).
DIALOGUE_MEMORY_NAMESPACE = uuid.UUID("3d8f2c61-7a4e-5b19-8e0c-94f1a6b2d7e5")

# Hard contract caps (section 19).  Configuration may lower, never raise, them.
MAX_SNAPSHOT_ITEMS = 200
MAX_ACTIVE_TOPICS = 8
MAX_UNRESOLVED_REFERENCES = 16
MAX_OPEN_QUESTIONS = 16
MAX_ACTIVE_REQUESTS = 16
MAX_ACTIVE_COMMITMENTS = 16
MAX_EXACT_QUOTES = 8
MAX_QUOTE_CHARACTERS = 500
MAX_SURFACE_ANCHOR_CHARACTERS = 500
MAX_OMISSION_MESSAGE_REFS = 32
MAX_ENTITY_MENTIONS = 8

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class DialogueMemoryVersions(FrozenModel):
    """Every component version a snapshot or turn context was produced under.

    The cache version is deliberately absent: a cache is never semantic.
    """

    contract: Literal["dialogue-working-memory-contract-v0.1"] = DIALOGUE_MEMORY_CONTRACT_VERSION
    item: Literal["dialogue-working-memory-item-v0.1"] = DIALOGUE_MEMORY_ITEM_VERSION
    message_order: Literal["dialogue-message-order-v0.1"] = DIALOGUE_MESSAGE_ORDER_VERSION
    message_eligibility: Literal["dialogue-message-eligibility-v0.1"] = DIALOGUE_MESSAGE_ELIGIBILITY_VERSION
    updater: Literal["dialogue-working-memory-updater-v0.1"] = DIALOGUE_MEMORY_UPDATER_VERSION
    projection: Literal["dialogue-working-memory-projection-v0.1"] = DIALOGUE_MEMORY_PROJECTION_VERSION
    semantic_digest: Literal["dialogue-working-memory-digest-v0.1"] = DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION


# ---------------------------------------------------------------------------
# Canonical JSON, digests, identities
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    """Sorted keys, stable separators, no NaN/Infinity, exact Unicode."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def dialogue_uuid(*parts: str) -> str:
    return str(uuid.uuid5(DIALOGUE_MEMORY_NAMESPACE, "\x1f".join(parts)))


def _uuid_text(value: str, label: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} must be a UUID string") from exc
    if str(parsed) != value:
        raise ValueError(f"{label} must be a canonical lowercase UUID string")


def _digest(value: str, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _nonblank(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _sorted_unique(values: tuple[str, ...], label: str) -> None:
    _unique(values, label)
    if tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted")


def _utc_text(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must be a UTC timestamp")


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

class DialogueRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class DialogueMessageStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DialogueExposureState(StrEnum):
    DURABLY_VISIBLE = "durably_visible"
    PARTIALLY_VISIBLE = "partially_visible"
    NOT_VISIBLE = "not_visible"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERY_FAILED = "delivery_failed"
    VISIBILITY_UNKNOWN = "visibility_unknown"


VISIBLE_EXPOSURES = frozenset({DialogueExposureState.DURABLY_VISIBLE, DialogueExposureState.PARTIALLY_VISIBLE})


class DialogueSpeaker(StrEnum):
    USER = "user"
    COLUMBINA = "columbina"
    RUNTIME = "runtime"


class DialogueEpistemicStatus(StrEnum):
    """``attributed``: someone said it (never verified truth).  ``observed``:
    a trusted runtime fact about the dialogue itself.  ``unresolved``: the
    dialogue left it open."""

    ATTRIBUTED = "attributed"
    OBSERVED = "observed"
    UNRESOLVED = "unresolved"


class DialogueItemStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    RESOLVED = "resolved"
    WITHDRAWN = "withdrawn"
    INACCESSIBLE = "inaccessible"


class DialogueItemType(StrEnum):
    ENTITY = "entity"
    PROPOSITION = "proposition"
    QUESTION = "question"
    REQUEST = "request"
    OPTION_SET = "option_set"
    TOPIC = "topic"
    PLAN_REFERENCE = "plan_reference"
    COMMITMENT_REFERENCE = "commitment_reference"
    UTTERANCE_REFERENCE = "utterance_reference"
    CORRECTION = "correction"
    GAP = "gap"


class DialogueGapKind(StrEnum):
    """A recorded absence.  Absence is never evidence that nothing happened."""

    DELIVERY_PENDING = "delivery_pending"
    DELIVERY_FAILED = "delivery_failed"
    RESPONSE_FAILED = "response_failed"
    VISIBILITY_UNKNOWN = "visibility_unknown"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    UNRESOLVED_CORRECTION = "unresolved_correction"
    UNRESOLVED_WITHDRAWAL = "unresolved_withdrawal"


EXPOSURE_GAP_KINDS = frozenset({
    DialogueGapKind.DELIVERY_PENDING, DialogueGapKind.DELIVERY_FAILED, DialogueGapKind.RESPONSE_FAILED,
    DialogueGapKind.VISIBILITY_UNKNOWN,
})
REFERENCE_GAP_KINDS = frozenset({
    DialogueGapKind.UNRESOLVED_REFERENCE, DialogueGapKind.UNRESOLVED_CORRECTION,
    DialogueGapKind.UNRESOLVED_WITHDRAWAL,
})


class DialogueResetReason(StrEnum):
    INITIAL_BOOTSTRAP = "initial_bootstrap"
    SOURCE_PREFIX_CHANGED = "source_prefix_changed"
    SOURCE_SEQUENCE_GAP = "source_sequence_gap"
    CONTRACT_VERSION_CHANGE = "contract_version_change"
    SNAPSHOT_INVALID = "snapshot_invalid"


class DialogueOmissionReason(StrEnum):
    OUTSIDE_BOOTSTRAP_WINDOW = "outside_bootstrap_window"
    UNSUPPORTED_HISTORICAL_SCHEMA = "unsupported_historical_schema"
    VISIBILITY_UNPROVEN = "visibility_unproven"
    SOURCE_DELETED = "source_deleted"
    PROJECTION_UNAVAILABLE = "projection_unavailable"
    HISTORICAL_TRACE_UNAVAILABLE = "historical_trace_unavailable"
    COMPACTED = "compacted"


class DialogueCueKind(StrEnum):
    """Deterministic dialogue cues found in one user utterance."""

    PRONOUN_SINGULAR = "pronoun_singular"
    PRONOUN_PLURAL = "pronoun_plural"
    DEMONSTRATIVE = "demonstrative"
    ORDINAL_OPTION = "ordinal_option"
    CONTINUATION = "continuation"
    PRIOR_USER_STATEMENT_QUERY = "prior_user_statement_query"
    PRIOR_ASSISTANT_STATEMENT_QUERY = "prior_assistant_statement_query"
    CORRECTION = "correction"
    WITHDRAWAL = "withdrawal"
    TOPIC_RETURN = "topic_return"


class DialogueResolution(StrEnum):
    """``resolved`` only when the evidence is unique; never the newest by convenience."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NO_CANDIDATE = "no_candidate"


class DialogueMemoryFailurePhase(StrEnum):
    SOURCE_LOADING = "source_loading"
    SOURCE_ORDERING = "source_ordering"
    EXPOSURE_RESOLUTION = "exposure_resolution"
    BOOTSTRAP = "bootstrap"
    TRANSITION = "transition"
    PROJECTION = "projection"
    PERSISTENCE = "persistence"
    CACHE = "cache"
    PIPELINE_HANDOFF = "pipeline_handoff"
    REALIZATION_CONTENT_PROJECTION = "realization_content_projection"
    REPLAY = "replay"


class DialogueMemoryFailureCode(StrEnum):
    CONVERSATION_NOT_OWNED = "conversation_not_owned"
    SOURCE_MESSAGE_MISSING = "source_message_missing"
    SOURCE_SEQUENCE_GAP = "source_sequence_gap"
    SOURCE_PREFIX_DIGEST_MISMATCH = "source_prefix_digest_mismatch"
    MESSAGE_ORDER_INVALID = "message_order_invalid"
    EXPOSURE_UNKNOWN = "exposure_unknown"
    DELIVERY_PENDING = "delivery_pending"
    SNAPSHOT_NOT_FOUND = "snapshot_not_found"
    SNAPSHOT_LINEAGE_MISMATCH = "snapshot_lineage_mismatch"
    SNAPSHOT_CONTRACT_INVALID = "snapshot_contract_invalid"
    PREDECESSOR_CONFLICT = "predecessor_conflict"
    PROJECTION_VERSION_UNSUPPORTED = "projection_version_unsupported"
    HISTORICAL_TRACE_UNAVAILABLE = "historical_trace_unavailable"
    AMBIGUOUS_REFERENCE_UNRESOLVED = "ambiguous_reference_unresolved"
    REQUIRED_DIALOGUE_CONTENT_MISSING = "required_dialogue_content_missing"
    FUTURE_EVIDENCE_DETECTED = "future_evidence_detected"
    CACHE_DIGEST_MISMATCH = "cache_digest_mismatch"
    CACHE_UNAVAILABLE = "cache_unavailable"
    PERSISTENCE_CONFLICT = "persistence_conflict"


# Recoverable: the database path still decides.  Everything else is a hard
# failure for the affected shadow observation.
RECOVERABLE_FAILURE_CODES = frozenset({
    DialogueMemoryFailureCode.CACHE_DIGEST_MISMATCH, DialogueMemoryFailureCode.CACHE_UNAVAILABLE,
})


class DialogueMemoryFailure(FrozenModel):
    """A content-free typed failure: no message text, payloads, or quotes."""

    phase: DialogueMemoryFailurePhase
    code: DialogueMemoryFailureCode
    safe_message: str = Field(min_length=1, max_length=300)
    retryable: bool = False


# ---------------------------------------------------------------------------
# Source event (trusted updater boundary only)
# ---------------------------------------------------------------------------

class DialogueSourceEvent(FrozenModel):
    """One persisted chat message as the updater consumes it.

    ``content`` exists only inside the trusted updater boundary; it is never
    copied wholesale into a snapshot, log, metric, cache key, or Realization
    payload.  ``exposure_state`` comes from the reviewed per-channel policy.
    """

    event_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    conversation_sequence: int = Field(ge=1)
    message_ref: str = Field(min_length=1)
    turn_ref: str | None = None
    role: DialogueRole
    status: DialogueMessageStatus
    source_channel: str = Field(min_length=1, max_length=32)
    exposure_state: DialogueExposureState
    content_digest: str
    content: str
    attachment_refs: tuple[str, ...] = ()
    created_at: str = Field(min_length=1)
    exposure_evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueSourceEvent:
        for label, value in (("conversation_ref", self.conversation_ref), ("user_ref", self.user_ref),
                             ("message_ref", self.message_ref)):
            _uuid_text(value, label)
        if self.turn_ref is not None:
            _uuid_text(self.turn_ref, "turn_ref")
        _digest(self.content_digest, "content_digest")
        if self.content_digest != sha256_text(self.content):
            raise ValueError("content_digest must be the SHA-256 of the exact content")
        _utc_text(self.created_at, "created_at")
        _unique(self.attachment_refs, "attachment refs")
        _sorted_unique(self.exposure_evidence_refs, "exposure evidence refs")
        for value in (*self.attachment_refs, *self.exposure_evidence_refs):
            _nonblank(value, "source event reference")
        if self.event_id != source_event_id_for(
            self.conversation_ref, self.message_ref, self.conversation_sequence, self.exposure_state,
        ):
            raise ValueError("event_id must be derived from the message, its position, and its exposure")
        if self.role == DialogueRole.USER:
            if self.status != DialogueMessageStatus.COMPLETED:
                raise ValueError("a persisted user message is always completed")
            if self.exposure_state not in {DialogueExposureState.DURABLY_VISIBLE, DialogueExposureState.VISIBILITY_UNKNOWN}:
                raise ValueError("a user message is either durably visible or of unknown visibility")
        if self.exposure_state in VISIBLE_EXPOSURES and self.role == DialogueRole.ASSISTANT and not self.content:
            raise ValueError("an empty assistant message cannot be visible")
        if self.exposure_state == DialogueExposureState.DURABLY_VISIBLE and self.role == DialogueRole.ASSISTANT and (
            self.status != DialogueMessageStatus.COMPLETED
        ):
            raise ValueError("only a completed assistant message is durably visible")
        if self.exposure_state == DialogueExposureState.PARTIALLY_VISIBLE and self.status not in {
            DialogueMessageStatus.PARTIAL, DialogueMessageStatus.FAILED,
        }:
            raise ValueError("only partial or failed assistant output is partially visible")
        return self

    @property
    def turn_key(self) -> str:
        return turn_key_for(self.turn_ref, self.message_ref)

    @property
    def visible(self) -> bool:
        return self.exposure_state in VISIBLE_EXPOSURES


def source_event_id_for(conversation_ref: str, message_ref: str, sequence: int, exposure: DialogueExposureState | str) -> str:
    return dialogue_uuid(DIALOGUE_MEMORY_CONTRACT_VERSION, "event", conversation_ref, message_ref, str(sequence),
                         DialogueExposureState(exposure).value)


def turn_key_for(turn_ref: str | None, message_ref: str) -> str:
    """The dialogue turn a message belongs to; an unprompted message is its own turn."""

    return f"turn:{turn_ref}" if turn_ref is not None else f"message:{message_ref}"


# ---------------------------------------------------------------------------
# Memory items (a closed discriminated union)
# ---------------------------------------------------------------------------

class DialogueSpanRef(FrozenModel):
    """An exact character span of one persisted message."""

    message_ref: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid(self) -> DialogueSpanRef:
        _uuid_text(self.message_ref, "span message_ref")
        if self.end <= self.start:
            raise ValueError("spans are non-empty")
        return self


def _anchor(value: str, label: str) -> None:
    _nonblank(value, label)
    if len(value) > MAX_SURFACE_ANCHOR_CHARACTERS:
        raise ValueError(f"{label} exceeds the anchor bound")
    if not valid_text(value) or "\n" in value:
        raise ValueError(f"{label} must be single-line valid text")


class _DialogueItemBase(FrozenModel):
    item_id: str = Field(min_length=1)
    item_version: Literal["dialogue-working-memory-item-v0.1"] = DIALOGUE_MEMORY_ITEM_VERSION
    conversation_ref: str = Field(min_length=1)
    introduced_turn_ref: str = Field(min_length=1)
    last_supported_turn_ref: str = Field(min_length=1)
    introduced_sequence: int = Field(ge=1)
    last_supported_sequence: int = Field(ge=1)
    source_message_refs: tuple[str, ...] = Field(min_length=1)
    source_span_refs: tuple[DialogueSpanRef, ...] = ()
    speaker: DialogueSpeaker
    epistemic_status: DialogueEpistemicStatus
    status: DialogueItemStatus = DialogueItemStatus.CURRENT
    sensitivity_class: SensitivityClass = SensitivityClass.ORDINARY
    # ``surface_anchors[i]`` is exactly the text at ``source_span_refs[i]``.
    surface_anchors: tuple[str, ...] = ()
    represents_refs: tuple[str, ...] = ()

    def _base_valid(self) -> None:
        _uuid_text(self.conversation_ref, "item conversation_ref")
        if self.last_supported_sequence < self.introduced_sequence:
            raise ValueError("an item cannot be supported before it was introduced")
        _unique(self.source_message_refs, "source message refs")
        for ref in self.source_message_refs:
            _uuid_text(ref, "source message ref")
        span_keys = tuple((span.message_ref, span.start, span.end) for span in self.source_span_refs)
        _unique(span_keys, "source spans")
        for span in self.source_span_refs:
            if span.message_ref not in self.source_message_refs:
                raise ValueError("a source span cites a message outside the item's sources")
        if len(self.surface_anchors) > len(self.source_span_refs):
            raise ValueError("every surface anchor has an exact source span")
        for anchor, span in zip(self.surface_anchors, self.source_span_refs):
            _anchor(anchor, "surface anchor")
            if span.end - span.start != len(anchor):
                raise ValueError("a surface anchor spans exactly its source text")
        _unique(self.represents_refs, "represented refs")
        for ref in (*self.represents_refs, self.introduced_turn_ref, self.last_supported_turn_ref):
            _nonblank(ref, "item reference")
        if self.item_id in self.represents_refs:
            raise ValueError("an item cannot represent itself")
        if self.speaker == DialogueSpeaker.RUNTIME and self.epistemic_status == DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("runtime records are observed or unresolved, never attributed speech")

    @property
    def is_current(self) -> bool:
        return self.status == DialogueItemStatus.CURRENT


class DialogueEntity(_DialogueItemBase):
    """A named thing a speaker mentioned.  Naming is not verification."""

    item_type: Literal["entity"] = "entity"
    entity_key: str = Field(min_length=1, max_length=120)
    mention_message_refs: tuple[str, ...] = Field(min_length=1, max_length=MAX_ENTITY_MENTIONS)
    last_mention_sequence: int = Field(ge=1)
    superseded_by_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> DialogueEntity:
        self._base_valid()
        if self.entity_key != self.entity_key.casefold().strip():
            raise ValueError("entity keys are normalized")
        if not set(self.mention_message_refs) <= set(self.source_message_refs):
            raise ValueError("entity mentions are item sources")
        if not self.surface_anchors:
            raise ValueError("an entity keeps its exact surface name")
        if self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("an entity is attributed to the speaker who named it")
        if (self.status == DialogueItemStatus.SUPERSEDED) != (self.superseded_by_ref is not None):
            raise ValueError("a superseded entity, and only it, names its replacement")
        return self


class DialogueProposition(_DialogueItemBase):
    """An attributed user statement.  Never a verified fact."""

    item_type: Literal["proposition"] = "proposition"
    entity_refs: tuple[str, ...] = ()
    superseded_by_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueProposition:
        self._base_valid()
        if self.speaker != DialogueSpeaker.USER:
            # Legacy assistant text is an utterance reference or quote, never a proposition.
            raise ValueError("V0 propositions are attributed user statements only")
        if self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("a user statement stays attributed; it is never promoted to fact")
        if len(self.surface_anchors) != 1:
            raise ValueError("a proposition keeps exactly its source sentence")
        _sorted_unique(self.entity_refs, "proposition entity refs")
        _sorted_unique(self.superseded_by_refs, "proposition replacements")
        if (self.status == DialogueItemStatus.SUPERSEDED) != bool(self.superseded_by_refs):
            raise ValueError("a superseded proposition, and only it, cites its correction")
        return self


QuestionShape = Literal["yes_no", "choice", "open"]


class DialogueQuestion(_DialogueItemBase):
    item_type: Literal["question"] = "question"
    question_shape: QuestionShape
    resolution_basis: Literal["completed_response_in_turn", "subsequent_user_turn"] | None = None
    resolved_by_message_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> DialogueQuestion:
        self._base_valid()
        if self.speaker == DialogueSpeaker.RUNTIME:
            raise ValueError("questions are asked by a dialogue participant")
        if self.epistemic_status != DialogueEpistemicStatus.UNRESOLVED:
            raise ValueError("a question records unresolved content")
        if len(self.surface_anchors) != 1:
            raise ValueError("a question keeps exactly its source sentence")
        resolved = self.status == DialogueItemStatus.RESOLVED
        if resolved != (self.resolution_basis is not None) or resolved != (self.resolved_by_message_ref is not None):
            raise ValueError("a resolved question, and only it, records its resolving evidence")
        if self.resolved_by_message_ref is not None:
            _uuid_text(self.resolved_by_message_ref, "resolved_by_message_ref")
        expected = {DialogueSpeaker.USER: "completed_response_in_turn", DialogueSpeaker.COLUMBINA: "subsequent_user_turn"}
        if self.resolution_basis is not None and self.resolution_basis != expected[self.speaker]:
            raise ValueError("question resolution evidence depends on who asked")
        return self


class DialogueRequest(_DialogueItemBase):
    """A user request.  A response is recorded; fulfilment is never inferred."""

    item_type: Literal["request"] = "request"
    response_message_refs: tuple[str, ...] = ()
    withdrawn_by_message_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> DialogueRequest:
        self._base_valid()
        if self.speaker != DialogueSpeaker.USER:
            raise ValueError("V0 requests are made by the user")
        if self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("a request is attributed to the user")
        if len(self.surface_anchors) != 1:
            raise ValueError("a request keeps exactly its source sentence")
        _unique(self.response_message_refs, "request responses")
        if (self.status == DialogueItemStatus.WITHDRAWN) != (self.withdrawn_by_message_ref is not None):
            raise ValueError("a withdrawn request, and only it, cites its withdrawal")
        if self.status == DialogueItemStatus.RESOLVED:
            raise ValueError("V0 never infers that a request was fulfilled")
        return self


class DialogueOption(FrozenModel):
    option_ref: str = Field(min_length=1)
    index: int = Field(ge=1)
    surface_anchor: str
    span: DialogueSpanRef

    @model_validator(mode="after")
    def valid(self) -> DialogueOption:
        _anchor(self.surface_anchor, "option anchor")
        if self.span.end - self.span.start != len(self.surface_anchor):
            raise ValueError("an option anchor spans exactly its source text")
        return self


class DialogueOptionSet(_DialogueItemBase):
    item_type: Literal["option_set"] = "option_set"
    options: tuple[DialogueOption, ...] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def valid(self) -> DialogueOptionSet:
        self._base_valid()
        if self.speaker != DialogueSpeaker.COLUMBINA:
            raise ValueError("V0 option sets are offered in delivered assistant speech")
        if tuple(item.index for item in self.options) != tuple(range(1, len(self.options) + 1)):
            raise ValueError("options keep their exact 1-based order")
        if tuple(item.option_ref for item in self.options) != tuple(
            option_ref_for(self.item_id, item.index) for item in self.options
        ):
            raise ValueError("option refs are derived from the option set and index")
        if any(item.span.message_ref not in self.source_message_refs for item in self.options):
            raise ValueError("options come from the option set's source message")
        return self


def option_ref_for(option_set_id: str, index: int) -> str:
    return f"{option_set_id}#option-{index}"


class DialogueTopic(_DialogueItemBase):
    item_type: Literal["topic"] = "topic"
    topic_key: str = Field(min_length=1, max_length=120)
    return_message_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueTopic:
        self._base_valid()
        if self.topic_key != self.topic_key.casefold().strip():
            raise ValueError("topic keys are normalized")
        if not self.surface_anchors:
            raise ValueError("a topic keeps its exact surface anchor")
        _unique(self.return_message_refs, "topic returns")
        return self


class DialoguePlanStep(FrozenModel):
    step_ref: str = Field(min_length=1)
    index: int = Field(ge=1)
    surface_anchor: str
    span: DialogueSpanRef

    @model_validator(mode="after")
    def valid(self) -> DialoguePlanStep:
        _anchor(self.surface_anchor, "plan step anchor")
        if self.span.end - self.span.start != len(self.surface_anchor):
            raise ValueError("a plan step anchor spans exactly its source text")
        return self


def plan_step_ref_for(plan_id: str, index: int) -> str:
    return f"{plan_id}#step-{index}"


class DialoguePlanReference(_DialogueItemBase):
    """A plan Columbina proposed.  V0 never records progress or completion."""

    item_type: Literal["plan_reference"] = "plan_reference"
    steps: tuple[DialoguePlanStep, ...] = Field(min_length=2, max_length=12)
    progress: Literal["unknown"] = "unknown"
    continuation_message_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialoguePlanReference:
        self._base_valid()
        if self.speaker != DialogueSpeaker.COLUMBINA:
            raise ValueError("V0 plans are proposed in delivered assistant speech")
        if tuple(item.index for item in self.steps) != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("plan steps keep their exact order")
        if tuple(item.step_ref for item in self.steps) != tuple(plan_step_ref_for(self.item_id, item.index)
                                                              for item in self.steps):
            raise ValueError("plan step refs are derived from the plan and index")
        _unique(self.continuation_message_refs, "plan continuations")
        return self


class DialogueCommitmentReference(_DialogueItemBase):
    """Something Columbina said it would do.  Fulfilment is never inferred."""

    item_type: Literal["commitment_reference"] = "commitment_reference"
    fulfillment: Literal["unknown"] = "unknown"

    @model_validator(mode="after")
    def valid(self) -> DialogueCommitmentReference:
        self._base_valid()
        if self.speaker != DialogueSpeaker.COLUMBINA or self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("a commitment is attributed Columbina speech")
        if len(self.surface_anchors) != 1:
            raise ValueError("a commitment keeps exactly its source sentence")
        return self


class DialogueQuote(FrozenModel):
    """An exact, bounded, provenance-linked prior wording."""

    text: str = Field(min_length=1, max_length=MAX_QUOTE_CHARACTERS)
    span: DialogueSpanRef
    permission: Literal["verbatim_quote_permitted"] = "verbatim_quote_permitted"

    @model_validator(mode="after")
    def valid(self) -> DialogueQuote:
        if not valid_text(self.text) or not self.text.strip():
            raise ValueError("quotes are non-empty valid text")
        if self.span.end - self.span.start != len(self.text):
            raise ValueError("a quote span covers exactly the quoted text")
        return self


class DialogueUtteranceReference(_DialogueItemBase):
    """One exposed utterance.  Partial output stays marked partial."""

    item_type: Literal["utterance_reference"] = "utterance_reference"
    message_ref: str = Field(min_length=1)
    message_sequence: int = Field(ge=1)
    terminal_status: DialogueMessageStatus
    completeness: Literal["complete", "partial"]
    exposure_state: DialogueExposureState
    source_channel: str = Field(min_length=1, max_length=32)
    content_digest: str
    character_count: int = Field(ge=0)
    attachment_count: int = Field(default=0, ge=0)
    quote: DialogueQuote | None = None
    exposure_evidence_refs: tuple[str, ...] = ()
    # Items (or option/step sub-refs) this utterance's cues uniquely resolved to.
    refers_to_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueUtteranceReference:
        self._base_valid()
        _uuid_text(self.message_ref, "utterance message_ref")
        _digest(self.content_digest, "utterance content_digest")
        _sorted_unique(self.exposure_evidence_refs, "utterance exposure evidence")
        _sorted_unique(self.refers_to_refs, "utterance references")
        if self.source_message_refs != (self.message_ref,):
            raise ValueError("an utterance reference cites exactly its message")
        if self.introduced_sequence != self.message_sequence:
            raise ValueError("an utterance is introduced at its own message position")
        if self.speaker == DialogueSpeaker.RUNTIME or self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("an utterance is attributed to its speaker")
        if self.exposure_state not in VISIBLE_EXPOSURES:
            raise ValueError("only exposed utterances are remembered")
        complete = (self.terminal_status == DialogueMessageStatus.COMPLETED
                    and self.exposure_state == DialogueExposureState.DURABLY_VISIBLE)
        if complete != (self.completeness == "complete"):
            raise ValueError("only completed, durably visible output is a complete utterance")
        if self.quote is not None:
            if self.quote.span.message_ref != self.message_ref or self.quote.span.start != 0 or (
                self.quote.span.end != self.character_count
            ):
                raise ValueError("a V0 quote is the exact whole utterance")
            if sha256_text(self.quote.text) != self.content_digest:
                raise ValueError("a quote must be exactly the persisted utterance")
        return self


class DialogueCorrection(_DialogueItemBase):
    """Append-only: cites the superseded side, the replacement side, and the correcting message."""

    item_type: Literal["correction"] = "correction"
    correcting_message_ref: str = Field(min_length=1)
    old_anchor: str
    new_anchor: str
    old_refs: tuple[str, ...] = Field(min_length=1)
    new_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> DialogueCorrection:
        self._base_valid()
        _uuid_text(self.correcting_message_ref, "correcting_message_ref")
        _anchor(self.old_anchor, "corrected anchor")
        _anchor(self.new_anchor, "replacement anchor")
        _sorted_unique(self.old_refs, "corrected refs")
        _sorted_unique(self.new_refs, "replacement refs")
        if set(self.old_refs) & set(self.new_refs):
            raise ValueError("a correction cannot replace an item with itself")
        if self.correcting_message_ref not in self.source_message_refs:
            raise ValueError("a correction cites its correcting message")
        if self.speaker != DialogueSpeaker.USER or self.epistemic_status != DialogueEpistemicStatus.ATTRIBUTED:
            raise ValueError("V0 corrections are attributed user corrections")
        return self


class DialogueGap(_DialogueItemBase):
    """A typed absence: pending/failed exposure or an unresolved reference."""

    item_type: Literal["gap"] = "gap"
    gap_kind: DialogueGapKind
    subject_message_ref: str = Field(min_length=1)
    cue_kind: DialogueCueKind | None = None
    resolution: DialogueResolution | None = None
    candidate_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueGap:
        self._base_valid()
        _uuid_text(self.subject_message_ref, "gap subject message")
        if self.subject_message_ref not in self.source_message_refs:
            raise ValueError("a gap cites its subject message")
        _sorted_unique(self.candidate_refs, "gap candidates")
        reference = self.gap_kind in REFERENCE_GAP_KINDS
        if reference != (self.cue_kind is not None) or reference != (self.resolution is not None):
            raise ValueError("reference gaps, and only they, record their cue and resolution")
        if self.resolution == DialogueResolution.RESOLVED:
            raise ValueError("a resolved reference is not a gap")
        if self.resolution == DialogueResolution.AMBIGUOUS and len(self.candidate_refs) < 2:
            raise ValueError("an ambiguous reference names its competing candidates")
        if self.resolution == DialogueResolution.NO_CANDIDATE and self.candidate_refs:
            raise ValueError("a reference without candidates names none")
        if reference:
            if self.speaker != DialogueSpeaker.USER or self.epistemic_status != DialogueEpistemicStatus.UNRESOLVED:
                raise ValueError("an unresolved user reference is unresolved user speech")
        elif self.speaker != DialogueSpeaker.RUNTIME or self.epistemic_status != DialogueEpistemicStatus.OBSERVED:
            raise ValueError("an exposure gap is an observed runtime fact")
        if self.gap_kind in EXPOSURE_GAP_KINDS and self.status not in {DialogueItemStatus.CURRENT, DialogueItemStatus.RESOLVED}:
            raise ValueError("an exposure gap is current or resolved")
        return self


DialogueMemoryItem = Annotated[
    Union[
        DialogueEntity, DialogueProposition, DialogueQuestion, DialogueRequest, DialogueOptionSet, DialogueTopic,
        DialoguePlanReference, DialogueCommitmentReference, DialogueUtteranceReference, DialogueCorrection,
        DialogueGap,
    ],
    Field(discriminator="item_type"),
]


def item_id_for(item_type: DialogueItemType | str, conversation_ref: str, identity: str, *lineage: str) -> str:
    """UUID5 over item version, conversation, normalized identity, and source lineage."""

    return dialogue_uuid(DIALOGUE_MEMORY_ITEM_VERSION, DialogueItemType(item_type).value, conversation_ref,
                         identity, *lineage)


# ---------------------------------------------------------------------------
# Omissions
# ---------------------------------------------------------------------------

class DialogueHistoryOmission(FrozenModel):
    """An explicit gap in what the snapshot covers.  Absence is not evidence."""

    reason: DialogueOmissionReason
    sequence_start: int = Field(ge=1)
    sequence_end: int = Field(ge=1)
    message_refs: tuple[str, ...] = Field(default=(), max_length=MAX_OMISSION_MESSAGE_REFS)
    item_refs: tuple[str, ...] = ()
    recoverable: bool

    @model_validator(mode="after")
    def valid(self) -> DialogueHistoryOmission:
        if self.sequence_end < self.sequence_start:
            raise ValueError("an omission range is ordered")
        _unique(self.message_refs, "omitted message refs")
        for ref in self.message_refs:
            _uuid_text(ref, "omitted message ref")
        _sorted_unique(self.item_refs, "omitted item refs")
        if self.item_refs and self.reason != DialogueOmissionReason.COMPACTED:
            raise ValueError("only a compaction names the items it removed")
        return self

    @property
    def sort_key(self) -> tuple[int, int, str, tuple[str, ...]]:
        return (self.sequence_start, self.sequence_end, self.reason.value, self.item_refs)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

_INDEX_TYPES: dict[str, frozenset[str]] = {
    "active_topic_refs": frozenset({"topic"}),
    "focus_entity_refs": frozenset({"entity"}),
    "unresolved_reference_refs": frozenset({"gap"}),
    "open_question_refs": frozenset({"question"}),
    "active_request_refs": frozenset({"request"}),
    "active_option_set_refs": frozenset({"option_set"}),
    "active_plan_refs": frozenset({"plan_reference"}),
    "active_commitment_refs": frozenset({"commitment_reference"}),
}
_INDEX_CAPS = {
    "active_topic_refs": MAX_ACTIVE_TOPICS, "unresolved_reference_refs": MAX_UNRESOLVED_REFERENCES,
    "open_question_refs": MAX_OPEN_QUESTIONS, "active_request_refs": MAX_ACTIVE_REQUESTS,
    "active_option_set_refs": 1, "active_plan_refs": 1, "active_commitment_refs": MAX_ACTIVE_COMMITMENTS,
    "focus_entity_refs": MAX_SNAPSHOT_ITEMS,
}
# Ordered (most recent first) indexes; the rest are sorted item ids.
ORDERED_INDEXES = frozenset({"active_topic_refs"})


class DialogueMemorySnapshot(FrozenModel):
    """One immutable, content-addressed state of a conversation's dialogue memory."""

    schema_version: Literal["dialogue-working-memory-contract-v0.1"] = DIALOGUE_MEMORY_CONTRACT_VERSION
    snapshot_id: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    revision: int = Field(ge=1)
    predecessor_snapshot_ref: str | None = None
    segment_id: str = Field(min_length=1)
    segment_start_sequence: int = Field(ge=1)
    reset_reason: DialogueResetReason | None = None
    through_conversation_sequence: int = Field(ge=0)
    through_message_id: str | None = None
    source_event_count: int = Field(ge=0)
    source_prefix_digest: str

    active_topic_refs: tuple[str, ...] = ()
    focus_entity_refs: tuple[str, ...] = ()
    unresolved_reference_refs: tuple[str, ...] = ()
    open_question_refs: tuple[str, ...] = ()
    active_request_refs: tuple[str, ...] = ()
    active_option_set_refs: tuple[str, ...] = ()
    active_plan_refs: tuple[str, ...] = ()
    active_commitment_refs: tuple[str, ...] = ()
    current_item_refs: tuple[str, ...] = ()

    items: tuple[DialogueMemoryItem, ...] = Field(default=(), max_length=MAX_SNAPSHOT_ITEMS)
    omitted_history: tuple[DialogueHistoryOmission, ...] = ()
    versions: DialogueMemoryVersions = Field(default_factory=DialogueMemoryVersions)
    semantic_digest: str

    @model_validator(mode="after")
    def valid(self) -> DialogueMemorySnapshot:
        for label, value in (("snapshot_id", self.snapshot_id), ("user_ref", self.user_ref),
                             ("conversation_ref", self.conversation_ref), ("segment_id", self.segment_id)):
            _uuid_text(value, label)
        if self.predecessor_snapshot_ref is not None:
            _uuid_text(self.predecessor_snapshot_ref, "predecessor_snapshot_ref")
            if self.predecessor_snapshot_ref == self.snapshot_id:
                raise ValueError("a snapshot cannot be its own predecessor")
        if (self.predecessor_snapshot_ref is None) != (self.reset_reason is not None):
            raise ValueError("a snapshot either continues its predecessor or records a typed segment reset")
        if self.revision == 1 and self.predecessor_snapshot_ref is not None:
            raise ValueError("the first revision has no predecessor")
        if self.through_message_id is not None:
            _uuid_text(self.through_message_id, "through_message_id")
        if (self.through_message_id is None) != (self.source_event_count == 0):
            raise ValueError("a snapshot names its last consumed message exactly when it consumed one")
        if self.through_conversation_sequence and self.segment_start_sequence > self.through_conversation_sequence + 1:
            raise ValueError("a segment cannot start after its watermark")
        _digest(self.source_prefix_digest, "source_prefix_digest")
        _digest(self.semantic_digest, "semantic_digest")

        ids = tuple(item.item_id for item in self.items)
        _sorted_unique(ids, "snapshot item ids")
        by_id = {item.item_id: item for item in self.items}
        for item in self.items:
            if item.conversation_ref != self.conversation_ref:
                raise ValueError("a snapshot item belongs to a different conversation")
            if item.last_supported_sequence > self.through_conversation_sequence:
                raise ValueError("a snapshot item cites evidence after the snapshot watermark")
        for name, allowed in _INDEX_TYPES.items():
            refs = getattr(self, name)
            if name in ORDERED_INDEXES:
                _unique(refs, name)
            else:
                _sorted_unique(refs, name)
            if len(refs) > _INDEX_CAPS[name]:
                raise ValueError(f"{name} exceeds its bound")
            for ref in refs:
                item = by_id.get(ref)
                if item is None or item.item_type not in allowed or not item.is_current:
                    raise ValueError(f"{name} must name current items of its type")
        if self.current_item_refs != tuple(item.item_id for item in self.items if item.is_current):
            raise ValueError("current item refs are exactly the current items")
        quotes = sum(1 for item in self.items if isinstance(item, DialogueUtteranceReference) and item.quote)
        if quotes > MAX_EXACT_QUOTES:
            raise ValueError("a snapshot holds a bounded number of exact quotes")
        # A cross-reference resolves to a present item, an option/step of one,
        # or an item a recorded compaction removed (its lineage stays in the
        # earlier immutable snapshots).
        compacted = {ref for omission in self.omitted_history for ref in omission.item_refs}
        for item in self.items:
            for ref in _cross_refs(item):
                if ref not in by_id and ref.partition("#")[0] not in compacted and not _is_sub_ref(ref, by_id):
                    raise ValueError("an item cross-reference does not resolve in the snapshot")
        omission_keys = tuple(item.sort_key for item in self.omitted_history)
        _unique(omission_keys, "omissions")
        if omission_keys != tuple(sorted(omission_keys)):
            raise ValueError("omissions must be in canonical order")
        if self.semantic_digest != snapshot_semantic_digest(self):
            raise ValueError("semantic_digest does not match the snapshot contents")
        if self.snapshot_id != snapshot_id_for(self.conversation_ref, self.semantic_digest):
            raise ValueError("snapshot_id must be derived from the conversation and semantic digest")
        return self

    def item(self, item_id: str) -> Any:
        return next((item for item in self.items if item.item_id == item_id), None)


def _is_sub_ref(ref: str, by_id: dict[str, Any]) -> bool:
    """Option and plan-step refs resolve through their parent item."""

    parent, _, suffix = ref.partition("#")
    item = by_id.get(parent)
    if item is None or not suffix:
        return False
    if isinstance(item, DialogueOptionSet):
        return any(option.option_ref == ref for option in item.options)
    if isinstance(item, DialoguePlanReference):
        return any(step.step_ref == ref for step in item.steps)
    return False


def _cross_refs(item: Any) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(item, DialogueEntity) and item.superseded_by_ref:
        refs.append(item.superseded_by_ref)
    if isinstance(item, DialogueProposition):
        refs.extend(item.superseded_by_refs)
    if isinstance(item, DialogueCorrection):
        refs.extend((*item.old_refs, *item.new_refs))
    if isinstance(item, DialogueUtteranceReference):
        refs.extend(item.refers_to_refs)
    return tuple(refs)


SNAPSHOT_DIGEST_EXCLUDED = ("snapshot_id", "semantic_digest")


def snapshot_semantic_projection(snapshot: DialogueMemorySnapshot | dict[str, Any]) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json") if isinstance(snapshot, FrozenModel) else dict(snapshot)
    return {key: value for key, value in payload.items() if key not in SNAPSHOT_DIGEST_EXCLUDED}


def snapshot_semantic_digest(snapshot: DialogueMemorySnapshot | dict[str, Any]) -> str:
    return sha256_json({
        "semantic_digest_version": DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION,
        "snapshot": snapshot_semantic_projection(snapshot),
    })


def snapshot_id_for(conversation_ref: str, semantic_digest: str) -> str:
    return dialogue_uuid(DIALOGUE_MEMORY_CONTRACT_VERSION, "snapshot", conversation_ref, semantic_digest)


def segment_id_for(conversation_ref: str, start_sequence: int, reason: DialogueResetReason, revision: int) -> str:
    return dialogue_uuid(DIALOGUE_MEMORY_CONTRACT_VERSION, "segment", conversation_ref, str(start_sequence),
                         reason.value, str(revision))


# ---------------------------------------------------------------------------
# Turn-local context view
# ---------------------------------------------------------------------------

class DialogueEventRef(FrozenModel):
    """A source event without its content (the capture already holds the text)."""

    message_ref: str = Field(min_length=1)
    conversation_sequence: int = Field(ge=1)
    turn_ref: str | None = None
    role: DialogueRole
    status: DialogueMessageStatus
    source_channel: str = Field(min_length=1, max_length=32)
    exposure_state: DialogueExposureState
    content_digest: str
    attachment_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid(self) -> DialogueEventRef:
        _uuid_text(self.message_ref, "event message_ref")
        if self.turn_ref is not None:
            _uuid_text(self.turn_ref, "event turn_ref")
        _digest(self.content_digest, "event content_digest")
        return self


class DialogueCue(FrozenModel):
    """One deterministic dialogue cue in the focus utterance and how it resolved."""

    cue_kind: DialogueCueKind
    surface_anchor: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    resolution: DialogueResolution
    resolved_refs: tuple[str, ...] = ()
    candidate_refs: tuple[str, ...] = ()
    ordinal: int | None = Field(default=None, ge=-1)

    @model_validator(mode="after")
    def valid(self) -> DialogueCue:
        _anchor(self.surface_anchor, "cue anchor")
        if self.end - self.start != len(self.surface_anchor):
            raise ValueError("a cue anchor spans exactly its focus text")
        _sorted_unique(self.resolved_refs, "cue resolutions")
        _sorted_unique(self.candidate_refs, "cue candidates")
        if (self.resolution == DialogueResolution.RESOLVED) != bool(self.resolved_refs):
            raise ValueError("a resolved cue, and only it, names what it resolved to")
        if self.resolution == DialogueResolution.AMBIGUOUS and len(self.candidate_refs) < 2:
            raise ValueError("an ambiguous cue names its competing candidates")
        if self.resolution != DialogueResolution.AMBIGUOUS and self.candidate_refs:
            raise ValueError("only an ambiguous cue carries competing candidates")
        if (self.cue_kind == DialogueCueKind.ORDINAL_OPTION) != (self.ordinal is not None):
            raise ValueError("an ordinal cue, and only it, carries its ordinal")
        return self

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (self.start, self.end, self.cue_kind.value)


class DialogueFocusEntity(FrozenModel):
    """A name mentioned in the focus utterance, with the entity id it has or will commit under."""

    entity_ref: str = Field(min_length=1)
    entity_key: str = Field(min_length=1, max_length=120)
    surface_anchor: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    committed: bool

    @model_validator(mode="after")
    def valid(self) -> DialogueFocusEntity:
        _anchor(self.surface_anchor, "focus entity anchor")
        if self.end - self.start != len(self.surface_anchor):
            raise ValueError("a focus entity anchor spans exactly its focus text")
        return self


class DialogueFocusAnalysis(FrozenModel):
    """The deterministic dialogue-layer reading of the focus utterance.

    This is not the Interpretation stage's output (which does not exist at
    capture time): it records only the cues the reviewed rules found, the
    names the focus mentions, and how each cue resolved against the committed
    snapshot plus earlier mentions in the focus itself.
    """

    is_question: bool
    is_request: bool
    focus_entities: tuple[DialogueFocusEntity, ...] = ()
    cues: tuple[DialogueCue, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> DialogueFocusAnalysis:
        keys = tuple(item.sort_key for item in self.cues)
        _unique(keys, "focus cues")
        if keys != tuple(sorted(keys)):
            raise ValueError("focus cues are in text order")
        starts = tuple(item.start for item in self.focus_entities)
        if starts != tuple(sorted(set(starts))):
            raise ValueError("focus entities are unique and in text order")
        _unique(tuple(item.entity_key for item in self.focus_entities), "focus entity keys")
        return self


class DialogueTurnContext(FrozenModel):
    """Committed snapshot + current user message, bounded by the future boundary."""

    schema_version: Literal["dialogue-working-memory-contract-v0.1"] = DIALOGUE_MEMORY_CONTRACT_VERSION
    context_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    committed_snapshot_ref: str = Field(min_length=1)
    committed_snapshot_digest: str
    committed_snapshot_revision: int = Field(ge=0)
    focus_message_ref: str = Field(min_length=1)
    focus_conversation_sequence: int = Field(ge=1)
    # The committed snapshot the context was compiled from, in full.  Bounded
    # by the snapshot caps; replay never reads the mutable head or a cache.
    prior_context_projection: DialogueMemorySnapshot
    current_user_event: DialogueEventRef
    current_interpretation_projection: DialogueFocusAnalysis
    future_boundary_sequence: int = Field(ge=1)
    versions: DialogueMemoryVersions = Field(default_factory=DialogueMemoryVersions)

    @model_validator(mode="after")
    def valid(self) -> DialogueTurnContext:
        for label, value in (("cognitive_turn_id", self.cognitive_turn_id), ("user_ref", self.user_ref),
                             ("conversation_ref", self.conversation_ref),
                             ("focus_message_ref", self.focus_message_ref)):
            _uuid_text(value, label)
        _digest(self.committed_snapshot_digest, "committed_snapshot_digest")
        snapshot = self.prior_context_projection
        if (snapshot.snapshot_id, snapshot.semantic_digest, snapshot.revision) != (
            self.committed_snapshot_ref, self.committed_snapshot_digest, self.committed_snapshot_revision,
        ):
            raise ValueError("the context cites a different committed snapshot")
        if (snapshot.user_ref, snapshot.conversation_ref) != (self.user_ref, self.conversation_ref):
            raise ValueError("the committed snapshot belongs to a different user or conversation")
        if self.future_boundary_sequence != self.focus_conversation_sequence:
            raise ValueError("the future boundary is the focus message's own position")
        if snapshot.through_conversation_sequence != self.focus_conversation_sequence - 1:
            raise ValueError("the committed snapshot must end immediately before the focus message")
        event = self.current_user_event
        if (event.message_ref, event.conversation_sequence) != (self.focus_message_ref, self.focus_conversation_sequence):
            raise ValueError("the current event is not the focus message")
        if event.role != DialogueRole.USER:
            raise ValueError("the focus of a cognitive turn is a user message")
        if event.turn_ref != self.cognitive_turn_id:
            raise ValueError("the focus message belongs to a different turn")
        if self.versions != snapshot.versions:
            raise ValueError("the context and its snapshot were produced under different versions")
        if self.context_id != turn_context_id_for(self.cognitive_turn_id, self.committed_snapshot_digest,
                                                  self.focus_message_ref):
            raise ValueError("context_id must be derived from the turn, snapshot, and focus")
        return self


def turn_context_id_for(cognitive_turn_id: str, snapshot_digest: str, focus_message_ref: str) -> str:
    return dialogue_uuid(DIALOGUE_MEMORY_CONTRACT_VERSION, "turn-context", cognitive_turn_id, snapshot_digest,
                         focus_message_ref)


def turn_context_digest(context: DialogueTurnContext | dict[str, Any]) -> str:
    payload = context.model_dump(mode="json") if isinstance(context, FrozenModel) else dict(context)
    return sha256_json({"semantic_digest_version": DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION, "context": payload})


__all__ = [
    "DIALOGUE_MEMORY_CACHE_VERSION", "DIALOGUE_MEMORY_CONTRACT_VERSION", "DIALOGUE_MEMORY_ITEM_VERSION",
    "DIALOGUE_MEMORY_NAMESPACE", "DIALOGUE_MEMORY_PROJECTION_VERSION", "DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION",
    "DIALOGUE_MEMORY_UPDATER_VERSION", "DIALOGUE_MESSAGE_ELIGIBILITY_VERSION", "DIALOGUE_MESSAGE_ORDER_VERSION",
    "EXPOSURE_GAP_KINDS", "MAX_ACTIVE_COMMITMENTS", "MAX_ACTIVE_REQUESTS", "MAX_ACTIVE_TOPICS",
    "MAX_ENTITY_MENTIONS", "MAX_EXACT_QUOTES", "MAX_OMISSION_MESSAGE_REFS", "MAX_OPEN_QUESTIONS",
    "MAX_QUOTE_CHARACTERS", "MAX_SNAPSHOT_ITEMS", "MAX_SURFACE_ANCHOR_CHARACTERS", "MAX_UNRESOLVED_REFERENCES",
    "ORDERED_INDEXES", "RECOVERABLE_FAILURE_CODES", "REFERENCE_GAP_KINDS", "SNAPSHOT_DIGEST_EXCLUDED",
    "VISIBLE_EXPOSURES", "DialogueCommitmentReference", "DialogueCorrection", "DialogueCue", "DialogueCueKind",
    "DialogueEntity", "DialogueEpistemicStatus", "DialogueEventRef", "DialogueExposureState",
    "DialogueFocusAnalysis", "DialogueFocusEntity", "DialogueGap", "DialogueGapKind", "DialogueHistoryOmission", "DialogueItemStatus",
    "DialogueItemType", "DialogueMemoryFailure", "DialogueMemoryFailureCode", "DialogueMemoryFailurePhase",
    "DialogueMemoryItem", "DialogueMemorySnapshot", "DialogueMemoryVersions", "DialogueMessageStatus",
    "DialogueOmissionReason", "DialogueOption", "DialogueOptionSet", "DialoguePlanReference", "DialoguePlanStep",
    "DialogueProposition", "DialogueQuestion", "DialogueQuote", "DialogueRequest", "DialogueResetReason",
    "DialogueResolution", "DialogueRole", "DialogueSourceEvent", "DialogueSpanRef", "DialogueSpeaker",
    "DialogueTopic", "DialogueTurnContext", "DialogueUtteranceReference", "canonical_json", "dialogue_uuid",
    "item_id_for", "option_ref_for", "plan_step_ref_for", "segment_id_for", "sha256_json", "sha256_text",
    "snapshot_id_for", "snapshot_semantic_digest", "snapshot_semantic_projection", "source_event_id_for",
    "turn_context_digest", "turn_context_id_for", "turn_key_for",
]
