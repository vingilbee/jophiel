"""Versioned immutable contracts for Durable Live-Shadow Trace V0.

A capture is the exact, turn-time set of server-owned facts from which one
canonical ``CognitivePipelineRequest`` is later compiled.  A terminal record is
the one immutable outcome of observing that capture: ``completed``, ``failed``,
or ``skipped``.  Neither contract carries authority over the live response.

This module holds contracts only.  It imports no database, Redis, chat-agent,
prompt, tool, or fixture code.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from ..cognitive_pipeline_v0.models import (
    CognitivePipelineRelationshipContext,
    CognitivePipelineRequest,
    CognitivePipelineTrace,
    CognitivePipelineVersions,
)
from ..contracts.actions import ActionPersonalityProfile
from ..contracts.dialogue_memory import (
    DialogueMemoryFailure,
    DialogueMemoryVersions,
    DialogueTurnContext,
    sha256_text,
    turn_context_digest,
)
from ..contracts.interpretation import FrozenModel
from ..contracts.realization import (
    RealizationFailure,
    RealizationRequestV0,
    RealizationStatus,
    RealizationTimings,
    RealizationTraceV0,
    realization_semantic_digest,
    sha256_json,
)
from ..contracts.safety import (
    APPLICATION_SAFETY_DECISION_SCHEMA_VERSION,
    SafetyDecisionProvenance,
    SafetyDisposition,
)
from ..dynamic_state.models import PersonalityProfile
from ..interpretation_v0.models import InterpretationBiasProfile, InterpretationMemory

# v0.2 records the explicit ``realization_content_inventory`` omission.  A v0.1
# capture is skipped as ``capture_schema_unsupported``, never reinterpreted.
CAPTURE_SCHEMA_VERSION = "cognitive-shadow-capture-v0.2"
# v0.3 adds the Dialogue Working Memory V0 turn context and bounds the message
# window by conversation position.  It is produced only while dialogue memory
# is enabled; v0.2 captures are produced, stored, and replayed unchanged.
CAPTURE_SCHEMA_VERSION_V03 = "cognitive-shadow-capture-v0.3"
SUPPORTED_CAPTURE_SCHEMAS = frozenset({CAPTURE_SCHEMA_VERSION, CAPTURE_SCHEMA_VERSION_V03})
TERMINAL_SCHEMA_VERSION = "cognitive-shadow-terminal-v0.1"
COMPILER_VERSION = "cognitive-shadow-compiler-v0.1"
SEMANTIC_DIGEST_VERSION = "cognitive-shadow-semantic-digest-v0.1"
TWO_CALL_SEMANTIC_DIGEST_VERSION = "cognitive-shadow-semantic-digest-v0.2"
WORKER_VERSION = "cognitive-shadow-worker-v0.1"
CAPTURE_PRODUCER_VERSION = "cognitive-shadow-capture-producer-v0.1"
CAPABILITY_MAP_VERSION = "cognitive-shadow-capability-map-v0.1"
# No structured, authorized memory projection exists yet.  The request field is
# required, so the absence of retrieval is itself named and versioned.
MEMORY_RETRIEVAL_POLICY_VERSION = "cognitive-shadow-no-structured-memory-v0.1"

# The documented bounded-history limit, including the focus message.  It is a
# capture-schema constant rather than a setting so every retry of a capture and
# every capture under this schema version applies the same bound.
CAPTURE_MESSAGE_LIMIT = 24

# The one registered, versioned mapping from server-owned facts to capability
# keys.  Keys outside this set can never enter ``available_capabilities``.
REGISTERED_CAPABILITY_KEYS: frozenset[str] = frozenset({
    "nutrition_image_estimation",  # settings.macrovision_tools_enabled
    "email_mailbox_access",        # gmail tools enabled + granted gmail.modify scope
    "drive_document_access",       # drive tools enabled + granted drive scope
    "blender_asset_tools",         # settings.blender_tools_enabled
})

# A fixed namespace so every identity derived from a durable turn is stable
# across processes, retries, and replays.
SHADOW_NAMESPACE = uuid.UUID("6f1c7a52-4a3e-5b9e-9d0a-2f6c1e8b7d34")

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def capture_id_for(turn_id: str) -> str:
    """The idempotency key of a capture: one per durable turn.

    The namespace label is the v0.2 constant for every capture version: a
    turn has exactly one capture whichever schema produced it.
    """

    return str(uuid.uuid5(SHADOW_NAMESPACE, f"{CAPTURE_SCHEMA_VERSION}:capture:{turn_id}"))


def terminal_id_for(job_id: str) -> str:
    return str(uuid.uuid5(SHADOW_NAMESPACE, f"{TERMINAL_SCHEMA_VERSION}:terminal:{job_id}"))


def segment_id_for(turn_id: str) -> str:
    """A segment is named by the turn that began it."""

    return str(uuid.uuid5(SHADOW_NAMESPACE, f"{COMPILER_VERSION}:segment:{turn_id}"))


def _uuid_text(value: str, label: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{label} must be a UUID string") from exc
    if str(parsed) != value:
        raise ValueError(f"{label} must be a canonical lowercase UUID string")


def _utc_text(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{label} must be a UTC timestamp")


def _digest(value: str | None, label: str, *, required: bool) -> None:
    if value is None:
        if required:
            raise ValueError(f"{label} is required")
        return
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

class ShadowSourceChannel(StrEnum):
    WEB = "web"
    VOICE = "voice"
    WHATSAPP = "whatsapp"


class GenerationPath(StrEnum):
    CHAT_WORKER = "chat_worker"
    LOCAL_FALLBACK = "local_fallback"


class OmittedInput(StrEnum):
    """Canonical inputs with no accepted live producer.  Empty is not evidence."""

    MEMORY_PROJECTION = "memory_projection"
    RELATIONSHIP_PROJECTION = "relationship_projection"
    TASK_CONTRACT = "task_contract"
    LAST_TOOL_EVENT = "last_tool_event"
    AUTHORIZATION = "authorization"
    CONSENT = "consent"
    EXTERNAL_EXPORT_CONTROL = "external_export_control"
    APPLICATION_SAFETY_POLICY = "application_safety_policy"
    PERSONALITY_PROJECTION = "personality_projection"
    PERCEPTION_WORKING_CONTEXT = "perception_working_context"
    VALUES = "values"
    SUPPLEMENTAL_INPUTS = "supplemental_inputs"
    GOAL_EVIDENCE = "goal_evidence"
    STATE_GROUNDED_OBJECTS = "state_grounded_objects"
    STATE_OUTCOMES = "state_outcomes"
    # Canonical defaults are used for these; no live producer supplies them.
    STATE_CONTROL_CONTEXT = "state_control_context"
    ELAPSED_MONOTONIC_TIME = "elapsed_monotonic_time"
    ACTION_GROUNDED_OBJECTS = "action_grounded_objects"
    ACTION_TARGET_DESCRIPTORS = "action_target_descriptors"
    PRIOR_ACTION_RECORDS = "prior_action_records"
    PRIOR_REALIZATION_RECORDS = "prior_realization_records"
    ACTION_OBSERVED_OUTCOMES = "action_observed_outcomes"
    # No trusted producer projects grounded realization content (memory, tool,
    # reviewed knowledge) yet; only content-free dialogue structure exists.
    REALIZATION_CONTENT_INVENTORY = "realization_content_inventory"
    # Capture v0.3 only: the dialogue turn context could not be produced.
    DIALOGUE_WORKING_MEMORY = "dialogue_working_memory"


# Inputs no V0 producer can ever supply; every capture omits them explicitly.
ALWAYS_OMITTED: frozenset[OmittedInput] = frozenset({
    OmittedInput.TASK_CONTRACT,
    OmittedInput.LAST_TOOL_EVENT,
    OmittedInput.AUTHORIZATION,
    OmittedInput.CONSENT,
    OmittedInput.EXTERNAL_EXPORT_CONTROL,
    OmittedInput.PERCEPTION_WORKING_CONTEXT,
    OmittedInput.VALUES,
    OmittedInput.SUPPLEMENTAL_INPUTS,
    OmittedInput.GOAL_EVIDENCE,
    OmittedInput.STATE_GROUNDED_OBJECTS,
    OmittedInput.STATE_OUTCOMES,
    OmittedInput.STATE_CONTROL_CONTEXT,
    OmittedInput.ELAPSED_MONOTONIC_TIME,
    OmittedInput.ACTION_GROUNDED_OBJECTS,
    OmittedInput.ACTION_TARGET_DESCRIPTORS,
    OmittedInput.PRIOR_ACTION_RECORDS,
    OmittedInput.PRIOR_REALIZATION_RECORDS,
    OmittedInput.ACTION_OBSERVED_OUTCOMES,
    OmittedInput.REALIZATION_CONTENT_INVENTORY,
})

# Capture v0.3: inputs the dialogue producer supplies whenever the turn
# context is available (and that are omitted exactly when it is not).
DIALOGUE_SUPPLIED_INPUTS: frozenset[OmittedInput] = frozenset({
    OmittedInput.PERCEPTION_WORKING_CONTEXT,
    OmittedInput.ACTION_GROUNDED_OBJECTS,
    OmittedInput.ACTION_TARGET_DESCRIPTORS,
    OmittedInput.REALIZATION_CONTENT_INVENTORY,
})
ALWAYS_OMITTED_V03: frozenset[OmittedInput] = ALWAYS_OMITTED - DIALOGUE_SUPPLIED_INPUTS


class PersonalityMissingReason(StrEnum):
    NO_ACCEPTED_SOURCE = "no_accepted_mapper_or_reviewed_baseline"
    BASELINE_INVALID = "reviewed_baseline_invalid"
    CORE_MEMORY_UNAVAILABLE = "core_memory_unavailable"
    CORE_MEMORY_MISMATCH = "baseline_core_memory_mismatch"
    CORE_MEMORY_INACTIVE = "core_memory_inactive"
    RUNTIME_MAPPER_SCHEMA_UNSUPPORTED = "runtime_mapper_schema_unsupported"
    RUNTIME_MAPPER_INPUT_INVALID = "runtime_mapper_input_invalid"
    RUNTIME_MAPPER_PROJECTION_INVALID = "runtime_mapper_projection_invalid"


class TerminalStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SkipReason(StrEnum):
    # ``feature_disabled`` and ``not_sampled`` are counted as content-free
    # metrics only: no capture (and so no terminal row) is created for them.
    FEATURE_DISABLED = "feature_disabled"
    NOT_SAMPLED = "not_sampled"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    MISSING_PERSONALITY_PROJECTION = "missing_personality_projection"
    MISSING_REQUIRED_RUNTIME_FACT = "missing_required_runtime_fact"
    UNAUTHORIZED_INPUT = "unauthorized_input"
    # Reserved for the "skip until recovery" lineage policy.  V0 instead begins
    # a new segment with ``predecessor_gap`` so observation can continue.
    PREDECESSOR_UNAVAILABLE = "predecessor_unavailable"
    CAPTURE_SCHEMA_UNSUPPORTED = "capture_schema_unsupported"


class SegmentResetReason(StrEnum):
    INITIAL_CONVERSATION = "initial_conversation"
    PREDECESSOR_GAP = "predecessor_gap"
    LINEAGE_VERSION_CHANGE = "lineage_version_change"
    # The job terminated before lineage could be resolved (an unreadable
    # capture, or exhausted attempts).  Such a record is never completed.
    LINEAGE_UNRESOLVED = "lineage_unresolved"


RESETS_CITING_PREDECESSORS = frozenset({
    SegmentResetReason.PREDECESSOR_GAP, SegmentResetReason.LINEAGE_VERSION_CHANGE,
})


ShadowFailureStage = Literal[
    "capture",
    "compile",
    "pipeline.validation",
    "pipeline.perception",
    "pipeline.handoff_validation",
    "pipeline.interpretation",
    "pipeline.goal_update",
    "pipeline.workspace_resolution",
    "pipeline.requirement_resolution",
    "pipeline.collaborative_content",
    "pipeline.goal_handoff_validation",
    "pipeline.turn_request",
    "pipeline.cognitive_turn",
    "pipeline.trace_validation",
    "persistence",
    "worker",
]


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class CapturedMessage(FrozenModel):
    """One exact durable chat record; never a merged history turn."""

    message_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str
    # Capture v0.3 only: the message's durable conversation position.
    conversation_sequence: int | None = Field(default=None, ge=1)

    @model_serializer(mode="wrap")
    def _omit_absent_position(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("conversation_sequence") is None:
            data.pop("conversation_sequence", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> CapturedMessage:
        _uuid_text(self.message_id, "message_id")
        return self


class ConfiguredLanguage(FrozenModel):
    """The configured application locale.  No language detector ran."""

    tag: str = Field(min_length=1)
    provenance: Literal["configured_application_locale"] = "configured_application_locale"
    source_ref: Literal["settings.cognitive_live_shadow_language_tag"] = (
        "settings.cognitive_live_shadow_language_tag"
    )


class CapturedAttachment(FrozenModel):
    """An attachment of the captured turn verified against the user's store."""

    attachment_id: str = Field(min_length=1)
    store: Literal["image_store", "asset_store"]
    provenance: Literal["server_store_record"] = "server_store_record"


class CapturedCapabilities(FrozenModel):
    mapping_version: str = Field(min_length=1)
    capability_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> CapturedCapabilities:
        if len(set(self.capability_keys)) != len(self.capability_keys):
            raise ValueError("capability keys must be unique")
        if tuple(sorted(self.capability_keys)) != self.capability_keys:
            raise ValueError("capability keys must be sorted")
        if self.mapping_version == CAPABILITY_MAP_VERSION and not set(self.capability_keys) <= REGISTERED_CAPABILITY_KEYS:
            raise ValueError("capability keys must come from the registered capability mapping")
        return self


class CapturedSafetyDecision(FrozenModel):
    """The application safety decision as captured, before snapshot binding.

    The compiler binds it to the compiled runtime snapshot id without changing
    its schema version, policy version, disposition, provenance, or producer.
    """

    schema_version: Literal["application-safety-decision-v0.1"] = APPLICATION_SAFETY_DECISION_SCHEMA_VERSION
    policy_version: str = Field(min_length=1)
    disposition: SafetyDisposition
    provenance: SafetyDecisionProvenance
    producer_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> CapturedSafetyDecision:
        if (
            self.disposition in {SafetyDisposition.ALLOW, SafetyDisposition.BLOCK_COMMITMENTS, SafetyDisposition.BLOCK_RESPONSE}
            and self.provenance != SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY
        ):
            raise ValueError("only the trusted application safety policy can allow or block")
        if self.provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE and self.disposition != SafetyDisposition.UNAVAILABLE:
            raise ValueError("a runtime-unavailable record can only carry the unavailable disposition")
        return self


class CapturedResponseObligation(FrozenModel):
    obligation: Literal["required"] = "required"
    evidence_ref: str = Field(min_length=1)


class CoreMemoryLineage(FrozenModel):
    core_memory_ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    schema_version: int = Field(ge=1)

    @model_validator(mode="after")
    def valid(self) -> CoreMemoryLineage:
        if self.core_memory_ref != core_memory_ref_for(self.name, self.version):
            raise ValueError("core memory ref must name the captured core identity version")
        return self


def core_memory_ref_for(name: str, version: int) -> str:
    return f"core_memory:{name}:v{version}"


class ShadowPersonalityProjection(FrozenModel):
    """All three compiled personality inputs from one core-memory lineage.

    ``reviewed_baseline`` is a separately reviewed, explicitly configured
    application baseline; it is never learned or user-specific.
    ``runtime_mapper`` is reserved for an accepted Personality Runtime Mapper.
    """

    projection_kind: Literal["reviewed_baseline", "runtime_mapper"]
    projection_id: str = Field(min_length=1)
    projection_version: str = Field(min_length=1)
    scope: Literal["application_baseline", "core_identity"]
    review_ref: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    core_memory_ref: str = Field(min_length=1)
    interpretation_biases: InterpretationBiasProfile
    state_personality: PersonalityProfile
    action_personality: ActionPersonalityProfile

    @model_validator(mode="after")
    def valid(self) -> ShadowPersonalityProjection:
        if self.projection_kind == "reviewed_baseline" and self.scope != "application_baseline":
            raise ValueError("a reviewed baseline is an application baseline, not a user-specific projection")
        if self.interpretation_biases.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("interpretation bias profile belongs to a different core memory")
        if self.action_personality.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("action personality profile belongs to a different core memory")
        for value in (self.projection_id, self.projection_version, self.review_ref, self.provenance):
            if not value.strip():
                raise ValueError("personality projection provenance must not be blank")
        if self.projection_kind == "runtime_mapper":
            # One mapper call produces all three profiles: they share one
            # version and deterministic identity, and Action is fully known.
            if self.scope != "core_identity":
                raise ValueError("a runtime-mapper projection is scoped to the core identity")
            if self.projection_id != f"personality-runtime:{self.core_memory_ref}:{self.projection_version}":
                raise ValueError("runtime-mapper projection id must name its lineage and mapper version")
            for profile, version, suffix in (
                (self.interpretation_biases, self.interpretation_biases.compiler_version, "interpretation"),
                (self.state_personality, self.state_personality.mapper_version, "state"),
                (self.action_personality, self.action_personality.mapper_version, "action"),
            ):
                if version != self.projection_version or profile.profile_id != f"{self.projection_id}:{suffix}":
                    raise ValueError("runtime-mapper profiles must share one mapper version and identity")
            if self.action_personality.status != "known":
                raise ValueError("a runtime-mapper projection carries a known action personality")
        return self


class CapturedDialogueMemory(FrozenModel):
    """The dialogue turn context exactly as captured, or the typed reason it is absent.

    Self-contained for replay: it holds the committed snapshot the context was
    compiled from, so neither the mutable head nor a cache is ever re-read.
    """

    status: Literal["available", "unavailable"]
    turn_context: DialogueTurnContext | None = None
    turn_context_digest: str | None = None
    failure: DialogueMemoryFailure | None = None

    @model_validator(mode="after")
    def valid(self) -> CapturedDialogueMemory:
        available = self.status == "available"
        if available != (self.turn_context is not None) or available != (self.turn_context_digest is not None):
            raise ValueError("an available dialogue capture, and only it, holds a turn context and its digest")
        if available == (self.failure is not None):
            raise ValueError("an unavailable dialogue capture, and only it, records its typed failure")
        if self.turn_context is not None and self.turn_context_digest != turn_context_digest(self.turn_context):
            raise ValueError("the dialogue turn context digest does not match")
        return self


class CognitiveShadowCapture(FrozenModel):
    capture_id: str = Field(min_length=1)
    schema_version: Literal["cognitive-shadow-capture-v0.2", "cognitive-shadow-capture-v0.3"] = CAPTURE_SCHEMA_VERSION
    producer_version: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    chat_job_ref: str | None = None
    generation_path: GenerationPath
    focus_message_id: str = Field(min_length=1)
    previous_user_message_id: str | None = None
    messages: tuple[CapturedMessage, ...] = Field(min_length=1, max_length=CAPTURE_MESSAGE_LIMIT)
    message_limit: Literal[24] = CAPTURE_MESSAGE_LIMIT
    context_truncated: bool
    source_channel: ShadowSourceChannel
    language: ConfiguredLanguage

    attachments_available: bool
    attachments: tuple[CapturedAttachment, ...] = ()
    capabilities: CapturedCapabilities
    authorization: None = None
    consent: None = None
    external_export_control: None = None
    safety_decision: CapturedSafetyDecision
    response_obligation: CapturedResponseObligation
    conversation_status: Literal["open"] = "open"

    memories: tuple[InterpretationMemory, ...] = ()
    memory_retrieval_policy_version: str = Field(min_length=1)
    relationship: CognitivePipelineRelationshipContext | None = None
    core_memory: CoreMemoryLineage | None = None
    personality: ShadowPersonalityProjection | None = None
    personality_missing_reason: PersonalityMissingReason | None = None
    omitted_inputs: tuple[OmittedInput, ...]

    sampling_policy_version: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    # Capture v0.3 only; omitted from a v0.2 payload so its digest never changes.
    dialogue: CapturedDialogueMemory | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_dialogue(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("dialogue") is None:
            data.pop("dialogue", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> CognitiveShadowCapture:
        for label, value in (
            ("user_ref", self.user_ref), ("conversation_ref", self.conversation_ref),
            ("turn_id", self.turn_id), ("focus_message_id", self.focus_message_id),
        ):
            _uuid_text(value, label)
        if self.capture_id != capture_id_for(self.turn_id):
            raise ValueError("capture_id must be derived from the durable turn and capture version")
        if self.chat_job_ref is not None:
            _uuid_text(self.chat_job_ref, "chat_job_ref")
        if (self.generation_path == GenerationPath.CHAT_WORKER) != (self.chat_job_ref is not None):
            raise ValueError("a chat-worker capture cites its ChatJob; a local-fallback capture cites none")
        if self.previous_user_message_id is not None:
            _uuid_text(self.previous_user_message_id, "previous_user_message_id")
            if self.previous_user_message_id == self.focus_message_id:
                raise ValueError("the previous user message cannot be the focus message")
        _utc_text(self.captured_at, "captured_at")

        message_ids = tuple(message.message_id for message in self.messages)
        if len(set(message_ids)) != len(message_ids):
            raise ValueError("captured message ids must be unique")
        if message_ids[-1] != self.focus_message_id or message_ids.count(self.focus_message_id) != 1:
            raise ValueError("the focus message must be the final bounded message")
        if self.messages[-1].role != "user":
            raise ValueError("the focus message must be a user message")

        attachment_ids = tuple((item.store, item.attachment_id) for item in self.attachments)
        if len(set(attachment_ids)) != len(attachment_ids):
            raise ValueError("captured attachments must be unique")
        if not self.attachments_available and self.attachments:
            raise ValueError("an unavailable attachment subsystem cannot report attachments")

        if any(not memory.authorized for memory in self.memories):
            raise ValueError("unauthorized memories cannot be captured")
        if self.relationship is not None and not self.relationship.authorized:
            raise ValueError("unauthorized relationship context cannot be captured")

        if (self.personality is None) == (self.personality_missing_reason is None):
            raise ValueError("capture either a personality projection or an explicit missing reason")
        if self.personality is not None:
            if self.core_memory is None or self.personality.core_memory_ref != self.core_memory.core_memory_ref:
                raise ValueError("the personality projection must share the captured core-memory lineage")

        omitted = set(self.omitted_inputs)
        if len(omitted) != len(self.omitted_inputs):
            raise ValueError("omitted inputs must be unique")
        if tuple(sorted(self.omitted_inputs)) != self.omitted_inputs:
            raise ValueError("omitted inputs must be sorted")
        v03 = self.schema_version == CAPTURE_SCHEMA_VERSION_V03
        if not (ALWAYS_OMITTED_V03 if v03 else ALWAYS_OMITTED) <= omitted:
            raise ValueError("inputs without a V0 producer must be recorded as omitted")
        self._validate_dialogue(omitted)
        expectations = (
            (OmittedInput.MEMORY_PROJECTION, not self.memories),
            (OmittedInput.RELATIONSHIP_PROJECTION, self.relationship is None),
            (OmittedInput.PERSONALITY_PROJECTION, self.personality is None),
            (
                OmittedInput.APPLICATION_SAFETY_POLICY,
                self.safety_decision.provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE,
            ),
        )
        for item, absent in expectations:
            if (item in omitted) != absent:
                raise ValueError(f"omission of {item.value} must match the captured input")
        return self

    def _validate_dialogue(self, omitted: set[OmittedInput]) -> None:
        positions = tuple(message.conversation_sequence for message in self.messages)
        if self.schema_version != CAPTURE_SCHEMA_VERSION_V03:
            if self.dialogue is not None or any(item is not None for item in positions):
                raise ValueError("only a v0.3 capture carries dialogue memory and message positions")
            if OmittedInput.DIALOGUE_WORKING_MEMORY in omitted:
                raise ValueError("only a v0.3 capture can omit dialogue memory")
            return
        if self.dialogue is None:
            raise ValueError("a v0.3 capture records its dialogue memory or why it is absent")
        if any(item is None for item in positions) or list(positions) != sorted(set(positions)):
            raise ValueError("a v0.3 capture window is in strictly increasing conversation position")
        unavailable = self.dialogue.status == "unavailable"
        for item in (*DIALOGUE_SUPPLIED_INPUTS, OmittedInput.DIALOGUE_WORKING_MEMORY):
            if (item in omitted) != unavailable:
                raise ValueError(f"omission of {item.value} must match the dialogue capture")
        context = self.dialogue.turn_context
        if context is None:
            return
        focus = self.messages[-1]
        if (context.cognitive_turn_id, context.user_ref, context.conversation_ref, context.focus_message_ref) != (
            self.turn_id, self.user_ref, self.conversation_ref, self.focus_message_id,
        ):
            raise ValueError("the dialogue turn context belongs to a different turn, user, or conversation")
        if focus.conversation_sequence != context.focus_conversation_sequence:
            raise ValueError("the dialogue turn context was compiled for a different focus position")
        if sha256_text(focus.content) != context.current_user_event.content_digest:
            raise ValueError("the dialogue turn context was compiled from different focus text")


# ---------------------------------------------------------------------------
# Terminal record
# ---------------------------------------------------------------------------

from ..latency import LatencySpan


class ShadowFailure(FrozenModel):
    """A content-free failure envelope: no raw exception text, no user content."""

    latency_spans: tuple[LatencySpan, ...] = ()

    @model_serializer(mode="wrap")
    def omit_legacy_latency(self, handler):
        data = handler(self)
        if not self.latency_spans:
            data.pop("latency_spans", None)
        return data

    stage: ShadowFailureStage
    nested_stage: str | None = None
    nested_phase: str | None = None
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_.]+$")
    safe_message: str = Field(min_length=1, max_length=300)
    attempt_count: int = Field(ge=0)
    retryable: bool

    @property
    def failure_path(self) -> str:
        return ".".join(part for part in (self.stage, self.nested_stage, self.nested_phase) if part)


class ShadowComponentVersions(FrozenModel):
    capture_schema: str = Field(min_length=1)
    terminal_schema: Literal["cognitive-shadow-terminal-v0.1"] = TERMINAL_SCHEMA_VERSION
    compiler: Literal["cognitive-shadow-compiler-v0.1"] = COMPILER_VERSION
    worker: Literal["cognitive-shadow-worker-v0.1"] = WORKER_VERSION
    semantic_digest: Literal["cognitive-shadow-semantic-digest-v0.1", "cognitive-shadow-semantic-digest-v0.2"] = SEMANTIC_DIGEST_VERSION
    capability_map: str | None = None
    memory_retrieval_policy: str | None = None
    personality_projection: str | None = None
    pipeline: CognitivePipelineVersions | None = None
    # Capture v0.3 only; omitted from v0.2 records so their payloads never change.
    dialogue_memory: DialogueMemoryVersions | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_dialogue(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("dialogue_memory") is None:
            data.pop("dialogue_memory", None)
        return data


class ShadowWorkerTimings(FrozenModel):
    """Diagnostic measurements only; excluded from every semantic digest."""

    compile_ms: float = Field(ge=0)
    pipeline_wall_ms: float = Field(ge=0)
    pipeline_cpu_ms: float = Field(ge=0)
    total_wall_ms: float = Field(ge=0)


class ShadowRealizationStatus(StrEnum):
    """Realization's own terminal status on a shadow record.

    ``not_requested``: realization disabled, the core observation did not
    complete, or the completed plan has no move to word.  ``execution_failed``:
    a deterministic realization failure (typed envelope, no trace).  The other
    six are the closed ``RealizationTraceV0`` statuses.  A provider failure
    never erases a valid Action trace, and a rejected candidate is never a
    completed utterance.
    """

    NOT_REQUESTED = "not_requested"
    ACCEPTED_SHADOW = "accepted_shadow"
    REJECTED = "rejected"
    SKIPPED_MISSING_CONTENT = "skipped_missing_content"
    SKIPPED_UNSUPPORTED_ACTION = "skipped_unsupported_action"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_FAILED = "provider_failed"
    EXECUTION_FAILED = "execution_failed"


TRACED_REALIZATION_STATUSES = frozenset(ShadowRealizationStatus(item.value) for item in RealizationStatus)


def realization_trace_digest(trace: RealizationTraceV0) -> str:
    return sha256_json(trace.model_dump(mode="json"))


class CognitiveShadowTerminalRecord(FrozenModel):
    terminal_schema_version: Literal["cognitive-shadow-terminal-v0.1"] = TERMINAL_SCHEMA_VERSION
    terminal_id: str = Field(min_length=1)
    status: TerminalStatus
    capture_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    user_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    predecessor_trace_id: str | None = None
    segment_reset_reason: SegmentResetReason | None = None
    missing_predecessor_refs: tuple[str, ...] = ()
    versions: ShadowComponentVersions

    capture_digest: str
    compiled_request_digest: str | None = None
    trace_payload_digest: str | None = None
    semantic_digest: str | None = None

    compiled_request: CognitivePipelineRequest | None = None
    pipeline_trace: CognitivePipelineTrace | None = None
    failure: ShadowFailure | None = None
    skip_reason: SkipReason | None = None
    worker_timings: ShadowWorkerTimings | None = None

    # Realization V0: separately versioned (inside its trace) and digested; the
    # core semantic digest above never covers model output.
    realization_status: ShadowRealizationStatus = ShadowRealizationStatus.NOT_REQUESTED
    realization_request: RealizationRequestV0 | None = None
    realization_request_digest: str | None = None
    realization_trace: RealizationTraceV0 | None = None
    realization_trace_digest: str | None = None
    realization_semantic_digest: str | None = None
    realization_failure: RealizationFailure | None = None
    realization_timings: RealizationTimings | None = None

    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> CognitiveShadowTerminalRecord:
        for label, value in (
            ("terminal_id", self.terminal_id), ("capture_id", self.capture_id),
            ("job_id", self.job_id), ("turn_id", self.turn_id), ("user_ref", self.user_ref),
            ("conversation_ref", self.conversation_ref), ("focus_message_id", self.focus_message_id),
            ("segment_id", self.segment_id),
        ):
            _uuid_text(value, label)
        if self.terminal_id != terminal_id_for(self.job_id):
            raise ValueError("terminal_id must be derived from the job")
        if self.capture_id != capture_id_for(self.turn_id):
            raise ValueError("terminal record cites a capture of a different turn")
        _utc_text(self.started_at, "started_at")
        _utc_text(self.completed_at, "completed_at")
        if datetime.fromisoformat(self.completed_at) < datetime.fromisoformat(self.started_at):
            raise ValueError("a terminal record cannot complete before it started")

        # Segment lineage.
        if self.predecessor_trace_id is not None:
            _uuid_text(self.predecessor_trace_id, "predecessor_trace_id")
        if self.segment_reset_reason is None:
            if self.predecessor_trace_id is None:
                raise ValueError("a continuous segment turn must cite its predecessor trace")
            if self.missing_predecessor_refs:
                raise ValueError("a continuous segment turn has no missing predecessors")
        else:
            if self.predecessor_trace_id is not None:
                raise ValueError("a segment reset has no continuous predecessor")
            if self.segment_id != segment_id_for(self.turn_id):
                raise ValueError("a reset segment is named by the turn that begins it")
            if (self.segment_reset_reason in RESETS_CITING_PREDECESSORS) != bool(self.missing_predecessor_refs):
                raise ValueError("a gap or version reset, and only those, records its missing predecessor refs")
            if self.segment_reset_reason == SegmentResetReason.LINEAGE_UNRESOLVED and (
                self.status == TerminalStatus.COMPLETED or self.compiled_request is not None
            ):
                raise ValueError("an unresolved lineage cannot carry a compiled request or a completed trace")

        _digest(self.capture_digest, "capture_digest", required=True)
        _digest(self.compiled_request_digest, "compiled_request_digest", required=False)
        _digest(self.trace_payload_digest, "trace_payload_digest", required=False)
        _digest(self.semantic_digest, "semantic_digest", required=False)

        if (self.compiled_request is None) != (self.compiled_request_digest is None):
            raise ValueError("a compiled request and its digest are recorded together")
        if self.compiled_request is not None:
            self._validate_request_ownership(self.compiled_request)

        if self.status == TerminalStatus.COMPLETED:
            if self.pipeline_trace is None or self.compiled_request is None:
                raise ValueError("a completed record requires the exact request and trace")
            if None in (self.trace_payload_digest, self.semantic_digest, self.worker_timings):
                raise ValueError("a completed record requires every digest and its timings")
            if self.failure is not None or self.skip_reason is not None:
                raise ValueError("a completed record carries no failure or skip reason")
            if self.versions.pipeline != self.pipeline_trace.versions:
                raise ValueError("terminal versions must equal the trace's canonical versions")
            self._validate_trace_ownership(self.pipeline_trace, self.compiled_request)
        else:
            if self.pipeline_trace is not None or self.trace_payload_digest is not None or self.semantic_digest is not None:
                raise ValueError("only a completed record may carry a pipeline trace")
            if self.versions.pipeline is not None:
                raise ValueError("only a completed record carries canonical pipeline versions")
            if self.status == TerminalStatus.FAILED:
                if self.failure is None or self.skip_reason is not None:
                    raise ValueError("a failed record requires exactly a failure envelope")
            else:
                if self.skip_reason is None or self.failure is not None:
                    raise ValueError("a skipped record requires exactly a skip reason")
                if self.skip_reason in {SkipReason.FEATURE_DISABLED, SkipReason.NOT_SAMPLED}:
                    raise ValueError("disabled or unsampled turns are metrics, never terminal rows")
                if self.compiled_request is not None or self.worker_timings is not None:
                    raise ValueError("a skipped record cannot claim a compilation or execution that did not occur")
        self._validate_realization()
        return self

    def _validate_realization(self) -> None:
        status = self.realization_status
        payloads = (
            self.realization_request, self.realization_request_digest, self.realization_trace,
            self.realization_trace_digest, self.realization_semantic_digest, self.realization_failure,
            self.realization_timings,
        )
        for label, value in (
            ("realization_request_digest", self.realization_request_digest),
            ("realization_trace_digest", self.realization_trace_digest),
            ("realization_semantic_digest", self.realization_semantic_digest),
        ):
            _digest(value, label, required=False)
        if status == ShadowRealizationStatus.NOT_REQUESTED:
            if any(value is not None for value in payloads):
                raise ValueError("an unrequested realization carries no realization payload")
            return
        if self.status != TerminalStatus.COMPLETED or self.pipeline_trace is None:
            raise ValueError("realization runs only after a completed deterministic observation")
        if self.realization_timings is None:
            raise ValueError("a requested realization records its timings")
        if (self.realization_request is None) != (self.realization_request_digest is None):
            raise ValueError("a realization request and its digest are recorded together")
        if status == ShadowRealizationStatus.EXECUTION_FAILED:
            if self.realization_failure is None or any(value is not None for value in (
                self.realization_request, self.realization_trace, self.realization_trace_digest,
                self.realization_semantic_digest,
            )):
                raise ValueError("a failed realization records exactly its typed failure")
            return
        trace = self.realization_trace
        if trace is None or self.realization_failure is not None:
            raise ValueError("a traced realization records its trace and no failure")
        if ShadowRealizationStatus(trace.status.value) != status:
            raise ValueError("the realization status is the trace's status")
        if (trace.cognitive_turn_id, trace.action_result_ref) != (
            self.turn_id, self.pipeline_trace.action_selection.result_id,
        ):
            raise ValueError("the realization trace belongs to a different turn or action result")
        if (self.realization_request, self.realization_request_digest) != (trace.request, trace.request_digest):
            raise ValueError("the recorded realization request is the trace's request")
        if self.realization_timings != trace.timings:
            raise ValueError("the recorded realization timings are the trace's timings")
        if self.realization_trace_digest != realization_trace_digest(trace):
            raise ValueError("the realization trace digest does not match the trace")
        if self.realization_semantic_digest != realization_semantic_digest(trace):
            raise ValueError("the realization semantic digest does not match the trace")

    def _validate_request_ownership(self, request: CognitivePipelineRequest) -> None:
        if (
            request.cognitive_turn_id != self.turn_id
            or request.conversation_ref != self.conversation_ref
            or request.focus_message_id != self.focus_message_id
        ):
            raise ValueError("compiled request ownership differs from the capture")

    def _validate_trace_ownership(
        self, trace: CognitivePipelineTrace, request: CognitivePipelineRequest
    ) -> None:
        if (
            trace.cognitive_turn_id != request.cognitive_turn_id
            or trace.conversation_ref != request.conversation_ref
            or trace.focus_message_id != request.focus_message_id
            or trace.input_message_ids != tuple(message.message_id for message in request.messages)
            or trace.runtime_snapshot_id != request.runtime.snapshot_id
        ):
            raise ValueError("pipeline trace ownership differs from the compiled request")


__all__ = [
    "ALWAYS_OMITTED", "ALWAYS_OMITTED_V03", "CAPABILITY_MAP_VERSION", "CAPTURE_MESSAGE_LIMIT",
    "CAPTURE_PRODUCER_VERSION", "CAPTURE_SCHEMA_VERSION", "CAPTURE_SCHEMA_VERSION_V03", "CapturedDialogueMemory",
    "DIALOGUE_SUPPLIED_INPUTS", "SUPPORTED_CAPTURE_SCHEMAS", "COMPILER_VERSION", "MEMORY_RETRIEVAL_POLICY_VERSION",
    "REGISTERED_CAPABILITY_KEYS", "RESETS_CITING_PREDECESSORS", "SEMANTIC_DIGEST_VERSION", "SHADOW_NAMESPACE", "TERMINAL_SCHEMA_VERSION",
    "TRACED_REALIZATION_STATUSES", "ShadowRealizationStatus", "realization_trace_digest",
    "WORKER_VERSION", "CapturedAttachment", "CapturedCapabilities", "CapturedMessage",
    "CapturedResponseObligation", "CapturedSafetyDecision", "CognitiveShadowCapture",
    "CognitiveShadowTerminalRecord", "ConfiguredLanguage", "CoreMemoryLineage", "GenerationPath",
    "OmittedInput", "PersonalityMissingReason", "SegmentResetReason", "ShadowComponentVersions",
    "ShadowFailure", "ShadowFailureStage", "ShadowPersonalityProjection", "ShadowSourceChannel",
    "ShadowWorkerTimings", "SkipReason", "TerminalStatus", "capture_id_for", "core_memory_ref_for",
    "segment_id_for", "terminal_id_for",
]
