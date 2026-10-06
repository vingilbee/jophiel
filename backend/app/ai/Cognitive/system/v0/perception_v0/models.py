"""Immutable contracts for deterministic Perception V0."""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrozenDict(dict):
    """A JSON-serializable mapping that rejects mutation."""

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("frozen mapping cannot be mutated")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def freeze_json(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return FrozenDict({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class CueCategory(StrEnum):
    CONVERSATION = "conversation"
    LINGUISTIC = "linguistic"
    TASK = "task"
    RUNTIME = "runtime"


class CueValueType(StrEnum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    SCALAR = "scalar"
    CATEGORY = "category"
    DISTRIBUTION = "distribution"


class CueLifecycleStatus(StrEnum):
    V0_ACTIVE = "v0_active"
    SHADOW = "shadow"
    DEFINED = "defined"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class DetectorType(StrEnum):
    DETERMINISTIC = "deterministic"
    RUNTIME = "runtime"
    COMPOSITE = "composite"
    LEARNED = "learned"


class EvidenceRequirement(StrEnum):
    TEXT_SPAN = "text_span"
    MESSAGE_SEQUENCE = "message_sequence"
    RUNTIME_FACT = "runtime_fact"
    TEXT_OR_SEQUENCE = "text_or_sequence"


class DetectorSource(StrEnum):
    DETERMINISTIC = "deterministic"
    RUNTIME = "runtime"


class CueState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class PerceptionMessage(FrozenModel):
    message_id: str = Field(min_length=1)
    role: MessageRole
    content: str

    @field_validator("message_id")
    @classmethod
    def non_blank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message_id must not be blank")
        return value


class AttachmentFact(FrozenModel):
    attachment_id: str = Field(min_length=1)
    filename: str | None = None
    trusted_runtime_provenance: str = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)


class ToolEventFact(FrozenModel):
    tool: str = Field(min_length=1)
    status: str = Field(min_length=1)
    trusted_runtime: bool
    authorized: bool
    event_reference: str = Field(min_length=1)
    invocation_reference: str | None = None
    error_code: str | None = None


class PerceptionRuntimeContext(FrozenModel):
    snapshot_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    attachments_available: bool = True
    attachments: tuple[AttachmentFact, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    last_tool_event: ToolEventFact | None = None
    conversation_mode: str | None = None

    @field_validator("snapshot_id")
    @classmethod
    def non_blank_snapshot_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot_id must not be blank")
        return value

    @model_validator(mode="after")
    def unavailable_attachments_cannot_contain_facts(self) -> PerceptionRuntimeContext:
        if not self.attachments_available and self.attachments:
            raise ValueError("unavailable attachment data cannot contain attachment facts")
        return self


class PerceptionWorkingContext(FrozenModel):
    """Refs and state for cue interpretation; never prior message text.

    The ``*_refs`` and ``source_dialogue_*`` fields are the Dialogue Working
    Memory V0 projection (dialogue-working-memory-projection-v0.1).  Perception
    V0 detectors do not consume them yet; they are carried so the working
    context a turn was perceived under is exact and replayable.
    """

    active_topic_ref: str | None = None
    unresolved_refs: tuple[str, ...] = ()
    source_working_memory_ref: str | None = None
    active_topic_refs: tuple[str, ...] = ()
    focus_entity_refs: tuple[str, ...] = ()
    open_question_refs: tuple[str, ...] = ()
    source_dialogue_snapshot_ref: str | None = None
    source_dialogue_snapshot_digest: str | None = None

    @model_validator(mode="after")
    def valid_dialogue_projection(self) -> PerceptionWorkingContext:
        for label, values in (
            ("unresolved refs", self.unresolved_refs), ("active topic refs", self.active_topic_refs),
            ("focus entity refs", self.focus_entity_refs), ("open question refs", self.open_question_refs),
        ):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError(f"working-context {label} must be unique and non-blank")
        if (self.source_dialogue_snapshot_ref is None) != (self.source_dialogue_snapshot_digest is None):
            raise ValueError("a dialogue snapshot ref and its digest are supplied together")
        if self.active_topic_refs and self.active_topic_ref != self.active_topic_refs[0]:
            raise ValueError("the active topic is the most recent of the active topics")
        return self


class PerceptionRequest(FrozenModel):
    schema_version: int = Field(default=1, ge=1)
    cognitive_turn_id: str = Field(min_length=1)
    messages: tuple[PerceptionMessage, ...]
    focus_message_id: str = Field(min_length=1)
    runtime: PerceptionRuntimeContext
    working_context: PerceptionWorkingContext | None = None
    language_tag: str | None = "en"
    cue_manifest_version: str = Field(default="cue-manifest-v2", min_length=1)
    context_truncated: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> PerceptionRequest:
        if not self.cognitive_turn_id.strip() or not self.focus_message_id.strip():
            raise ValueError("cognitive turn and focus ids must not be blank")
        if len(self.messages) > 30:
            raise ValueError("Perception V0 accepts at most 30 ordered messages")
        ids = tuple(message.message_id for message in self.messages)
        if len(ids) != len(set(ids)):
            raise ValueError("message ids must be unique")
        if self.focus_message_id not in ids:
            raise ValueError("focus_message_id does not resolve")
        if ids.index(self.focus_message_id) != len(ids) - 1:
            raise ValueError("messages after the focus message are future evidence")
        return self

    @property
    def focus_message(self) -> PerceptionMessage:
        return self.messages[-1]


class CueDefinition(FrozenModel):
    cue_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: CueCategory
    value_type: CueValueType
    lifecycle_status: CueLifecycleStatus
    detector_type: DetectorType
    detector_reference: str = Field(min_length=1)
    evidence_requirement: EvidenceRequirement
    value_constraints: Mapping[str, Any] = Field(default_factory=dict)
    parent_cue_key: str | None = None
    conflicts_with: tuple[str, ...] = ()
    base_importance: float | None = None
    origin: str = "human"
    downstream_consumers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def freeze_constraints(self) -> CueDefinition:
        frozen = freeze_json(self.value_constraints)
        object.__setattr__(self, "value_constraints", frozen)
        if self.lifecycle_status == CueLifecycleStatus.V0_ACTIVE:
            if self.detector_type not in {DetectorType.DETERMINISTIC, DetectorType.RUNTIME}:
                raise ValueError("active cues require deterministic or runtime detectors")
            if not self.downstream_consumers:
                raise ValueError("active cues require a downstream consumer")
        return self


class TextSpanEvidence(FrozenModel):
    evidence_type: Literal["text_span"] = "text_span"
    message_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quoted_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_offsets(self) -> TextSpanEvidence:
        if self.start >= self.end:
            raise ValueError("text span start must precede end")
        return self


class WholeMessageEvidence(FrozenModel):
    evidence_type: Literal["whole_message"] = "whole_message"
    message_id: str = Field(min_length=1)


class MessageSequenceEvidence(FrozenModel):
    evidence_type: Literal["message_sequence"] = "message_sequence"
    message_ids: tuple[str, ...] = Field(min_length=1)


class RuntimeFactEvidence(FrozenModel):
    evidence_type: Literal["runtime_fact"] = "runtime_fact"
    runtime_snapshot_id: str = Field(min_length=1)
    json_pointer: str
    value: Any

    @field_validator("json_pointer")
    @classmethod
    def valid_pointer_syntax(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("runtime fact pointer must use RFC 6901 syntax")
        for token in value.split("/")[1:]:
            index = 0
            while index < len(token):
                if token[index] == "~":
                    if index + 1 >= len(token) or token[index + 1] not in "01":
                        raise ValueError("invalid RFC 6901 escape")
                    index += 2
                else:
                    index += 1
        return value

    @model_validator(mode="after")
    def freeze_value(self) -> RuntimeFactEvidence:
        object.__setattr__(self, "value", freeze_json(self.value))
        return self


PerceptionEvidence = Annotated[
    TextSpanEvidence | WholeMessageEvidence | MessageSequenceEvidence | RuntimeFactEvidence,
    Field(discriminator="evidence_type"),
]


class PerceptionObservation(FrozenModel):
    observation_id: str = Field(min_length=1)
    observation_key: str = Field(min_length=1)
    value_type: CueValueType
    value: Any
    evidence: tuple[PerceptionEvidence, ...]
    source: str = Field(min_length=1)
    source_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def freeze_value(self) -> PerceptionObservation:
        object.__setattr__(self, "value", freeze_json(self.value))
        return self


class CueActivation(FrozenModel):
    cue_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    value_type: CueValueType
    state: CueState
    value: bool | int | float | str | Mapping[str, float] | None
    raw_score: float | None = None
    confidence: float | None = None
    salience: float | None = None
    evidence: tuple[PerceptionEvidence, ...] = ()
    observation_refs: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    detector_source: DetectorSource
    detector_reference: str = Field(min_length=1)
    lifecycle_status: CueLifecycleStatus

    @model_validator(mode="after")
    def validate_state_value(self) -> CueActivation:
        if isinstance(self.value, Mapping) and not isinstance(self.value, FrozenDict):
            object.__setattr__(self, "value", freeze_json(self.value))
        if self.value_type == CueValueType.BOOLEAN:
            expected = {CueState.PRESENT: True, CueState.ABSENT: False}.get(self.state)
            if expected is not None and self.value is not expected:
                raise ValueError("Boolean present/absent values must be true/false")
        elif self.value_type == CueValueType.INTEGER:
            if self.state == CueState.PRESENT and (type(self.value) is not int or self.value <= 0):
                raise ValueError("integer present values must be positive integers")
            if self.state == CueState.ABSENT and (type(self.value) is not int or self.value != 0):
                raise ValueError("integer absent values must be zero")
        if self.state in {CueState.UNKNOWN, CueState.NOT_APPLICABLE} and self.value is not None:
            raise ValueError("unknown/not-applicable activations require a null value")
        if self.state == CueState.PRESENT and not self.evidence:
            raise ValueError("present activations require evidence")
        if self.state == CueState.UNKNOWN and not self.missing_information:
            raise ValueError("unknown activations require missing-information reasons")
        if self.raw_score is not None:
            raise ValueError("deterministic V0 activations cannot have raw scores")
        if self.salience is not None:
            raise ValueError("Perception V0 has no approved attention policy")
        if self.detector_source == DetectorSource.DETERMINISTIC and self.confidence is not None:
            raise ValueError("deterministic lexical detectors cannot claim calibrated confidence")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        return self


class PerceptionAttention(FrozenModel):
    cue_key: str
    salience: float


class PerceptionVersions(FrozenModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )

    schema_id: str = Field(alias="schema", min_length=1)
    cue_manifest: str = Field(min_length=1)
    deterministic_detectors: str = Field(min_length=1)
    evidence_validator: str = Field(min_length=1)
    uncertainty_policy: str = Field(min_length=1)
    attention_policy: str = Field(min_length=1)


class PerceptionInterpretationHandoff(FrozenModel):
    perception_event_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    input_message_ids: tuple[str, ...] = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    runtime_snapshot_id: str = Field(min_length=1)
    context_truncated: bool
    cue_activations: tuple[CueActivation, ...]
    versions: PerceptionVersions

    @model_validator(mode="after")
    def active_cues_only(self) -> PerceptionInterpretationHandoff:
        from .manifest import get_manifest

        expected = tuple(
            item.cue_key
            for item in get_manifest(self.versions.cue_manifest)
            if item.lifecycle_status == CueLifecycleStatus.V0_ACTIVE
        )
        actual = tuple(item.cue_key for item in self.cue_activations)
        if actual != expected or any(
            item.lifecycle_status != CueLifecycleStatus.V0_ACTIVE
            for item in self.cue_activations
        ):
            raise ValueError("interpretation handoff must contain exactly the active manifest cues")
        if len(self.input_message_ids) != len(set(self.input_message_ids)):
            raise ValueError("handoff input message ids must be unique")
        if self.focus_message_id not in self.input_message_ids or self.input_message_ids[-1] != self.focus_message_id:
            raise ValueError("handoff focus must be the final bounded message")
        return self


class PerceptionEvent(FrozenModel):
    schema_version: int = 1
    perception_event_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    input_message_ids: tuple[str, ...]
    focus_message_id: str = Field(min_length=1)
    runtime_snapshot_id: str = Field(min_length=1)
    observations: tuple[PerceptionObservation, ...]
    cue_activations: tuple[CueActivation, ...]
    attention: tuple[PerceptionAttention, ...] = ()
    overall_uncertainty: float = Field(ge=0.0, le=1.0)
    missing_information: tuple[str, ...]
    context_truncated: bool = False
    versions: PerceptionVersions

    @model_validator(mode="after")
    def validate_envelope(self) -> PerceptionEvent:
        from .manifest import get_manifest

        manifest = get_manifest(self.versions.cue_manifest)
        expected = tuple(item.cue_key for item in manifest)
        actual = tuple(item.cue_key for item in self.cue_activations)
        if actual != expected:
            raise ValueError("event activations must be complete, unique, and in manifest order")
        definitions = {item.cue_key: item for item in manifest}
        for activation in self.cue_activations:
            definition = definitions[activation.cue_key]
            if (
                activation.definition_version != definition.definition_version
                or activation.value_type != definition.value_type
                or activation.detector_reference != definition.detector_reference
                or activation.lifecycle_status != definition.lifecycle_status
            ):
                raise ValueError(f"activation conflicts with manifest: {activation.cue_key}")
        observation_ids = tuple(item.observation_id for item in self.observations)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("observation ids must be unique")
        known = set(observation_ids)
        if any(ref not in known for item in self.cue_activations for ref in item.observation_refs):
            raise ValueError("activation has an unresolved observation reference")
        if self.attention or any(item.salience is not None for item in self.cue_activations):
            raise ValueError("attention is not approved in Perception V0")
        return self

    def activations_for_interpretation(self) -> tuple[CueActivation, ...]:
        """Return only manifest-approved live cues; shadow cues can never leak."""

        from .manifest import get_manifest

        live_keys = {
            item.cue_key
            for item in get_manifest(self.versions.cue_manifest)
            if item.lifecycle_status == CueLifecycleStatus.V0_ACTIVE
        }
        return tuple(item for item in self.cue_activations if item.cue_key in live_keys)

    def interpretation_handoff(self) -> PerceptionInterpretationHandoff:
        """Build the version-preserving canonical input view for Interpretation V0."""

        return PerceptionInterpretationHandoff(
            perception_event_id=self.perception_event_id,
            cognitive_turn_id=self.cognitive_turn_id,
            input_message_ids=self.input_message_ids,
            focus_message_id=self.focus_message_id,
            runtime_snapshot_id=self.runtime_snapshot_id,
            context_truncated=self.context_truncated,
            cue_activations=self.activations_for_interpretation(),
            versions=self.versions,
        )
