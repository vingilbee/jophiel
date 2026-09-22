"""Immutable contracts and canonical projections for Cognitive Pipeline V0."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..appraisal.models import (
    ActiveGoal,
    CapabilitySnapshot,
    MemoryRecord,
    RelationshipSnapshot,
    RuntimeContext,
    SupplementalInput,
    ValueRecord,
)
from ..cognitive_turn.models import (
    CognitiveTurnTrace,
    CognitiveTurnVersions,
    ProcessingPath,
)
from ..contracts.interpretation import (
    FrozenModel,
    InterpretationEnvelope,
    InterpretationVersions,
)
from ..dynamic_state.models import (
    ControlContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityProfile,
    StateSnapshot,
)
from ..interpretation_v0.models import (
    InterpretationBiasProfile,
    InterpretationMemory,
    InterpretationRelationshipContext,
    InterpretationRuntimeContext,
    TaskContract,
)
from ..interpretation_v0.ontology import (
    FEATURE_MANIFEST_VERSION,
    ONTOLOGY_VERSION,
    PREDICATES,
    PREDICATE_REGISTRY_VERSION,
)
from ..perception_v0.manifest import MANIFEST_VERSION
from ..perception_v0.models import (
    AttachmentFact,
    PerceptionEvent,
    PerceptionMessage,
    PerceptionRuntimeContext,
    PerceptionVersions,
    PerceptionWorkingContext,
    ToolEventFact,
)

PIPELINE_SCHEMA_VERSION = "cognitive-pipeline-schema-v0.1"
PIPELINE_RUNNER_VERSION = "cognitive-pipeline-runner-v0.1"


def _require_non_blank(label: str, values: tuple[str, ...]) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{label} must not contain blank values")


def _require_unique(label: str, values: tuple[object, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _validate_json(value: object) -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _validate_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json(item)
        return
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


class CognitivePipelineRuntimeSnapshot(FrozenModel):
    snapshot_id: str = Field(min_length=1)
    schema_version: Literal[1] = 1
    predicate_registry_version: str = Field(min_length=1)
    known_predicates: tuple[str, ...]
    known_entities: tuple[str, ...] = ()

    attachments_available: bool = True
    attachments: tuple[AttachmentFact, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    capability_snapshot_id: str = Field(min_length=1)
    task_contract: TaskContract | None = None

    authorization: str | None = None
    consent: str | None = None
    safety_decision: str | None = None
    external_export_control: bool | None = None
    policy_records: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = Field(default_factory=dict)
    last_tool_event: ToolEventFact | None = None
    conversation_mode: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> CognitivePipelineRuntimeSnapshot:
        _require_non_blank(
            "runtime identifiers",
            (self.snapshot_id, self.predicate_registry_version, self.capability_snapshot_id),
        )
        if self.predicate_registry_version != PREDICATE_REGISTRY_VERSION:
            raise ValueError("unsupported interpretation predicate registry version")
        if self.known_predicates != PREDICATES:
            raise ValueError("known predicates must exactly match the installed registry")
        for label, values in (
            ("known predicates", self.known_predicates),
            ("known entities", self.known_entities),
            ("available capabilities", self.available_capabilities),
            ("policy records", self.policy_records),
        ):
            _require_non_blank(label, values)
            _require_unique(label, values)
        attachment_ids = tuple(item.attachment_id for item in self.attachments)
        _require_unique("attachment ids", attachment_ids)
        for attachment in self.attachments:
            _require_non_blank(
                "attachment provenance",
                (attachment.attachment_id, attachment.trusted_runtime_provenance,
                 attachment.evidence_reference),
            )
        if not self.attachments_available and self.attachments:
            raise ValueError("unavailable attachments cannot contain attachment facts")
        _validate_json(self.diagnostics)
        if self.task_contract is not None:
            _require_non_blank(
                "required task parameters", self.task_contract.required_parameters
            )
            _require_unique(
                "required task parameters", self.task_contract.required_parameters
            )
            _validate_json(self.task_contract.provided_parameters)
        if self.last_tool_event is not None:
            event = self.last_tool_event
            required = (event.tool, event.status, event.event_reference)
            if event.invocation_reference is not None:
                required += (event.invocation_reference,)
            _require_non_blank("tool-event fields", required)
        return self

    def to_perception_runtime(self) -> PerceptionRuntimeContext:
        return PerceptionRuntimeContext(
            snapshot_id=self.snapshot_id,
            schema_version=self.schema_version,
            attachments_available=self.attachments_available,
            attachments=self.attachments,
            available_capabilities=self.available_capabilities,
            last_tool_event=self.last_tool_event,
            conversation_mode=self.conversation_mode,
        )

    def to_interpretation_runtime(self) -> InterpretationRuntimeContext:
        return InterpretationRuntimeContext(
            snapshot_id=self.snapshot_id,
            predicate_registry_version=self.predicate_registry_version,
            known_predicates=self.known_predicates,
            known_entities=self.known_entities,
            attachments=self.attachments,
            available_capabilities=self.available_capabilities,
            task_contract=self.task_contract,
            authorization=self.authorization,
            safety_decision=self.safety_decision,
            last_tool_event=self.last_tool_event,
        )

    def to_appraisal_runtime(self) -> RuntimeContext:
        task = self.task_contract
        return RuntimeContext(
            capability_snapshot=CapabilitySnapshot(
                snapshot_id=self.capability_snapshot_id,
                available_actions=self.available_capabilities,
            ),
            available_actions=self.available_capabilities,
            required_parameters=task.required_parameters if task else (),
            provided_parameters=task.provided_parameters if task else {},
            authorization=self.authorization,
            consent=self.consent,
            safety_decision=self.safety_decision,
            external_export_control=self.external_export_control,
            policy_records=self.policy_records,
            diagnostics=self.diagnostics,
            last_tool_event=(
                self.last_tool_event.model_dump(mode="python")
                if self.last_tool_event is not None else {}
            ),
        )


class CognitivePipelineRelationshipContext(FrozenModel):
    relationship_ref: str = Field(min_length=1)
    status: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    authorized: bool = True
    evidence: str | None = None
    records: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_relationship(self) -> CognitivePipelineRelationshipContext:
        _require_non_blank(
            "relationship fields", (self.relationship_ref, self.status, self.provenance)
        )
        _require_unique("relationship records", self.records)
        _require_non_blank("relationship records", self.records)
        if not self.authorized:
            raise ValueError("unauthorized relationship context cannot enter the pipeline")
        return self

    def to_interpretation_relationship(self) -> InterpretationRelationshipContext:
        return InterpretationRelationshipContext(
            relationship_ref=self.relationship_ref,
            status=self.status,
            provenance=self.provenance,
            authorized=self.authorized,
        )

    def to_appraisal_relationship(self) -> RelationshipSnapshot:
        return RelationshipSnapshot(
            relationship_snapshot_id=self.relationship_ref,
            status=self.status,
            evidence=self.evidence,
            records=self.records,
        )


class CognitivePipelineRequest(FrozenModel):
    schema_version: Literal[1] = 1
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    messages: tuple[PerceptionMessage, ...] = Field(min_length=1, max_length=30)
    focus_message_id: str = Field(min_length=1)
    language_tag: str | None = "en"
    context_truncated: bool = False
    perception_working_context: PerceptionWorkingContext | None = None

    runtime: CognitivePipelineRuntimeSnapshot
    memories: tuple[InterpretationMemory, ...] = ()
    relationship: CognitivePipelineRelationshipContext | None = None

    interpretation_biases: InterpretationBiasProfile
    active_goals: tuple[ActiveGoal, ...]
    values: tuple[ValueRecord, ...] = ()
    supplemental_inputs: tuple[SupplementalInput, ...] = ()

    previous_state: StateSnapshot
    state_personality: PersonalityProfile
    grounded_objects: tuple[GroundedObject, ...] = ()
    control_context: ControlContext = Field(default_factory=ControlContext)
    outcomes: tuple[OutcomeObservation, ...] = ()
    elapsed_monotonic_ms: int = Field(default=0, ge=0)

    core_memory_ref: str = Field(min_length=1)
    memory_retrieval_policy_version: str = Field(min_length=1)
    processing_path: Literal[ProcessingPath.FAST] = ProcessingPath.FAST

    cue_manifest_version: str = Field(default=MANIFEST_VERSION, min_length=1)
    ontology_version: str = Field(default=ONTOLOGY_VERSION, min_length=1)
    feature_manifest_version: str = Field(default=FEATURE_MANIFEST_VERSION, min_length=1)

    @field_validator("language_tag")
    @classmethod
    def only_english(cls, value: str | None) -> str:
        if value != "en":
            raise ValueError("Cognitive Pipeline V0 supports only language_tag='en'")
        return value

    @model_validator(mode="after")
    def validate_request(self) -> CognitivePipelineRequest:
        _require_non_blank(
            "pipeline ownership/version identifiers",
            (
                self.cognitive_turn_id, self.conversation_ref, self.focus_message_id,
                self.core_memory_ref, self.memory_retrieval_policy_version,
                self.cue_manifest_version, self.ontology_version,
                self.feature_manifest_version,
            ),
        )
        message_ids = tuple(message.message_id for message in self.messages)
        _require_unique("message ids", message_ids)
        if message_ids.count(self.focus_message_id) != 1:
            raise ValueError("focus message must occur exactly once")
        if message_ids[-1] != self.focus_message_id:
            raise ValueError("focus message must be the final bounded message")
        if self.previous_state.conversation_ref != self.conversation_ref:
            raise ValueError("previous state belongs to a different conversation")
        if self.previous_state.personality_mapper_version != self.state_personality.mapper_version:
            raise ValueError("previous state personality mapper differs from the supplied profile")
        if self.interpretation_biases.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("interpretation bias profile belongs to a different core memory")
        if not any(goal.status == "active" for goal in self.active_goals):
            raise ValueError("Cognitive Pipeline V0 requires at least one active goal")
        if any(not memory.authorized for memory in self.memories):
            raise ValueError("unauthorized memories cannot enter the pipeline")
        if self.relationship is not None and not self.relationship.authorized:
            raise ValueError("unauthorized relationship context cannot enter the pipeline")
        identity_groups: tuple[tuple[str, tuple[object, ...]], ...] = (
            ("memory ids", tuple(item.memory_id for item in self.memories)),
            ("goal ids", tuple(item.goal_id for item in self.active_goals)),
            ("value ids", tuple(item.value_id for item in self.values)),
            ("supplemental input ids", tuple(item.input_id for item in self.supplemental_inputs)),
            ("outcome ids", tuple(item.outcome_id for item in self.outcomes)),
            ("grounded object identities", tuple(
                (item.object_type, item.object_ref) for item in self.grounded_objects
            )),
        )
        for label, identifiers in identity_groups:
            _require_unique(label, identifiers)
        for memory in self.memories:
            _require_non_blank(
                "memory identifiers and provenance",
                (memory.memory_id, memory.memory_type, memory.provenance, memory.status),
            )
        _require_non_blank(
            "compiled interpretation profile identifiers",
            (
                self.interpretation_biases.profile_id,
                self.interpretation_biases.compiler_version,
                self.interpretation_biases.source_core_memory_ref,
            ),
        )
        _require_non_blank(
            "state personality identifiers",
            (self.state_personality.profile_id, self.state_personality.mapper_version),
        )
        _require_non_blank(
            "previous-state identifiers",
            (
                self.previous_state.state_snapshot_id,
                self.previous_state.cognitive_turn_id,
                self.previous_state.conversation_ref,
                self.previous_state.policy_version,
                self.previous_state.personality_mapper_version,
            ),
        )
        for goal in self.active_goals:
            _require_non_blank("goal identifiers", (goal.goal_id, goal.owner))
            _validate_json(goal.desired_state)
        for supplemental in self.supplemental_inputs:
            _require_non_blank("supplemental input ids", (supplemental.input_id,))
            _validate_json(supplemental.value)
            _validate_json(supplemental.resolved_arguments)
        for outcome in self.outcomes:
            _require_non_blank(
                "outcome identifiers and provenance", (outcome.outcome_id, outcome.provenance)
            )
        for grounded in self.grounded_objects:
            _require_non_blank("grounded object references", (grounded.object_ref,))
        return self


class CognitivePipelineVersions(FrozenModel):
    pipeline_schema: str = Field(min_length=1)
    pipeline_runner: str = Field(min_length=1)
    perception: PerceptionVersions
    interpretation: InterpretationVersions
    cognitive_turn: CognitiveTurnVersions


class CognitivePipelineTimings(FrozenModel):
    perception_ms: float
    interpretation_ms: float
    cognitive_turn_ms: float
    total_ms: float

    @model_validator(mode="after")
    def validate_timings(self) -> CognitivePipelineTimings:
        values = (
            self.perception_ms, self.interpretation_ms,
            self.cognitive_turn_ms, self.total_ms,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("pipeline timings must be finite and non-negative")
        if not math.isclose(
            self.total_ms,
            self.perception_ms + self.interpretation_ms + self.cognitive_turn_ms,
            rel_tol=1e-9,
            abs_tol=1e-3,
        ):
            raise ValueError("pipeline total timing must equal its stage timings")
        return self


class CognitivePipelineTrace(FrozenModel):
    schema_version: Literal[1] = 1
    pipeline_trace_id: str = Field(min_length=1)
    trace_revision: Literal[1] = 1
    supersedes_pipeline_trace_id: None = None
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    input_message_ids: tuple[str, ...] = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    runtime_snapshot_id: str = Field(min_length=1)
    processing_path: Literal[ProcessingPath.FAST]
    status: Literal["completed"] = "completed"
    perception: PerceptionEvent
    interpretation: InterpretationEnvelope
    cognitive_turn: CognitiveTurnTrace
    versions: CognitivePipelineVersions
    timings: CognitivePipelineTimings

    @model_validator(mode="after")
    def validate_trace(self) -> CognitivePipelineTrace:
        turn = self.cognitive_turn
        state = turn.dynamic_state_update.snapshot
        if not (
            self.cognitive_turn_id
            == self.perception.cognitive_turn_id
            == self.interpretation.cognitive_turn_id
            == turn.cognitive_turn_id
            == state.cognitive_turn_id
        ):
            raise ValueError("pipeline cognitive-turn ownership differs across stages")
        if not (
            self.conversation_ref
            == self.interpretation.conversation_ref
            == turn.conversation_ref
            == state.conversation_ref
        ):
            raise ValueError("pipeline conversation ownership differs across stages")
        if not (
            self.input_message_ids
            == self.perception.input_message_ids
            == self.interpretation.input_message_ids
            == turn.input_message_ids
        ):
            raise ValueError("pipeline message ownership differs across stages")
        if not (
            self.focus_message_id
            == self.perception.focus_message_id
            == self.interpretation.focus_message_id
            == turn.focus_message_id
        ):
            raise ValueError("pipeline focus ownership differs across stages")
        if not (
            self.runtime_snapshot_id
            == self.perception.runtime_snapshot_id
            == self.interpretation.runtime_snapshot_id
        ):
            raise ValueError("pipeline runtime lineage differs across stages")
        if self.interpretation.perception_event_id != self.perception.perception_event_id:
            raise ValueError("interpretation cites a different perception event")
        if turn.input_snapshot.interpretation != self.interpretation:
            raise ValueError("cognitive turn contains a different interpretation")
        if turn.versions.interpretation_versions != self.interpretation.versions:
            raise ValueError("cognitive turn contains different interpretation versions")
        if self.processing_path != turn.processing_path:
            raise ValueError("pipeline processing paths differ")
        if self.versions.perception != self.perception.versions:
            raise ValueError("pipeline perception versions were reconstructed incorrectly")
        if self.versions.interpretation != self.interpretation.versions:
            raise ValueError("pipeline interpretation versions were reconstructed incorrectly")
        if self.versions.cognitive_turn != turn.versions:
            raise ValueError("pipeline cognitive-turn versions were reconstructed incorrectly")
        if self.versions.pipeline_schema != PIPELINE_SCHEMA_VERSION:
            raise ValueError("pipeline schema version mismatch")
        if self.versions.pipeline_runner != PIPELINE_RUNNER_VERSION:
            raise ValueError("pipeline runner version mismatch")
        return self


def project_memory(memory: InterpretationMemory) -> MemoryRecord:
    """Produce the single allowed, loss-aware Appraisal compatibility view."""

    return MemoryRecord(
        memory_id=memory.memory_id,
        memory_type=memory.memory_type,
        content=memory.content,
        provenance=memory.provenance,
        status=memory.status,
    )
