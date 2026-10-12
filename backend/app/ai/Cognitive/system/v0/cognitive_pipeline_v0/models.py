"""Immutable contracts and canonical projections for Cognitive Pipeline V0."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from ..appraisal.models import (
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
from ..contracts.dialogue_interpretation import InterpretedDialogueState
from ..contracts.goals import GoalEvidence, GoalSnapshot, GoalTurnRollover, GoalVersions
from ..contracts.collaboration import (
    CollaborationActionHandoff, CollaborationGoalOutcome, CollaborationTurnRecord, CollaborationVersions,
    WorkspaceResolution, WorkspaceTurnInputs,
)
from ..contracts.realization import RealizationContentItem
from ..contracts.requirement_resolution import (
    CONTENT_STATUS_BY_PRODUCER, REQUIREMENT_ACTION_PROJECTION_VERSION, ContentProducerStatus,
    RequirementActionHandoff, RequirementResolutionInputs, RequirementResolutionVersions,
    validate_requirement_snapshot,
)
from ..contracts.safety import ApplicationSafetyDecision, legacy_safety_label
from ..producers_v0.contracts import KnowledgeGapRecord, ProducerResult, SemanticQuery
from ..contracts.actions import (
    ActionGroundedObject,
    ActionPersonalityProfile,
    ActionRuntimeContext,
    ActionSelectionResult,
    ActionTargetDescriptor,
    ActionVersions,
    ObservedOutcomeRecord,
    PriorActionRecord,
    PriorRealizationRecord,
)
from ..goal_v0.models import GoalRuntimeContext
from ..dynamic_state.models import (
    ControlContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityProfile,
    StateSnapshot,
)
from ..interpretation_v0.models import (
    InterpretationBiasProfile,
    InterpretationDialogueContext,
    InterpretationMemory,
    InterpretationRelationshipContext,
    InterpretationRuntimeContext,
    TaskContract,
)
from ..interpretation_v0.ontology import (
    FEATURE_MANIFEST_VERSION,
    ONTOLOGY_VERSION,
    registered_predicates,
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

PIPELINE_SCHEMA_VERSION = "cognitive-pipeline-schema-v0.2"
# Runner x.y.1 orders the trusted Goal turn rollover before the response-turn
# update (SH-EVAL-001).  The request schema only gains an optional field that
# is omitted when absent, so historical requests stay byte-identical.
# Runner x.y.2 runs typed requirement resolution between Goal and the
# cognitive turn and records it on the trace; the request only gains the
# optional ``requirement_inputs`` field, omitted when absent.
# Runner x.y.3 (Collaborative Content and Inquiry V0) reconciles delivered
# outcomes at the turn boundary, resolves the conversational workspace after
# Goal, runs collaborative content production after requirement resolution,
# and appends its projection to the Action inputs; the request only gains the
# optional ``workspace_inputs`` field, omitted when absent.
PIPELINE_RUNNER_VERSION = "cognitive-pipeline-runner-v0.2.3"
# v0.3 adds the Dialogue Working Memory V0 handoff.  A schema-2 request runs
# and serializes exactly as before, so stored v0.2 traces keep replaying under
# their historical versions; v0.3 semantics never run over a v0.2 payload.
PIPELINE_SCHEMA_VERSION_V03 = "cognitive-pipeline-schema-v0.3"
PIPELINE_RUNNER_VERSION_V03 = "cognitive-pipeline-runner-v0.3.3"
PIPELINE_VERSIONS_BY_SCHEMA: Mapping[int, tuple[str, str]] = {
    2: (PIPELINE_SCHEMA_VERSION, PIPELINE_RUNNER_VERSION),
    3: (PIPELINE_SCHEMA_VERSION_V03, PIPELINE_RUNNER_VERSION_V03),
}
# Runner versions whose stored traces remain valid audit records.  They load
# for summaries and replay reporting, but only the current runner (above) is
# ever emitted or continued as live-shadow lineage.  None of them ran
# requirement resolution, so their traces never carry it.
HISTORICAL_PIPELINE_RUNNER_VERSIONS: Mapping[int, frozenset[str]] = {
    2: frozenset({"cognitive-pipeline-runner-v0.2", "cognitive-pipeline-runner-v0.2.1",
                  "cognitive-pipeline-runner-v0.2.2"}),
    3: frozenset({"cognitive-pipeline-runner-v0.3", "cognitive-pipeline-runner-v0.3.1",
                  "cognitive-pipeline-runner-v0.3.2"}),
}
# Runners whose traces record requirement resolution, and collaboration.
REQUIREMENT_RESOLUTION_RUNNERS = frozenset({
    "cognitive-pipeline-runner-v0.2.2", "cognitive-pipeline-runner-v0.3.2", PIPELINE_RUNNER_VERSION,
    PIPELINE_RUNNER_VERSION_V03,
})
COLLABORATION_RUNNERS = frozenset({PIPELINE_RUNNER_VERSION, PIPELINE_RUNNER_VERSION_V03})
DIALOGUE_OBJECT_PREFIX = "dialogue:"


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
    # The closed, versioned application safety decision.  Upstream stages that
    # still take a string receive its deterministic legacy projection.
    safety_decision: ApplicationSafetyDecision | None = None
    external_export_control: bool | None = None
    policy_records: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = Field(default_factory=dict)
    last_tool_event: ToolEventFact | None = None
    conversation_mode: str | None = None
    response_obligation: Literal["required", "not_required"]
    response_obligation_ref: str | None = None
    conversation_status: Literal["open", "closed"] = "open"
    task_ref: str | None = None
    deadline_band: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> CognitivePipelineRuntimeSnapshot:
        _require_non_blank(
            "runtime identifiers",
            (self.snapshot_id, self.predicate_registry_version, self.capability_snapshot_id),
        )
        # A stored request of a historical registry stays parseable for audit
        # and replay reporting; Interpretation itself accepts only the
        # installed registry, so it is never interpreted under the new one.
        registered = registered_predicates(self.predicate_registry_version)
        if registered is None:
            raise ValueError("unsupported interpretation predicate registry version")
        if self.known_predicates != registered:
            raise ValueError("known predicates must exactly match their registry")
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
        if self.safety_decision is not None and self.safety_decision.runtime_snapshot_ref != self.snapshot_id:
            raise ValueError("the application safety decision belongs to a different runtime snapshot")
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
            safety_decision=legacy_safety_label(self.safety_decision),
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
            safety_decision=legacy_safety_label(self.safety_decision),
            external_export_control=self.external_export_control,
            policy_records=self.policy_records,
            diagnostics=self.diagnostics,
            last_tool_event=(
                self.last_tool_event.model_dump(mode="python")
                if self.last_tool_event is not None else {}
            ),
        )

    def to_action_runtime(self) -> ActionRuntimeContext:
        task = self.task_contract
        event = self.last_tool_event
        return ActionRuntimeContext(
            runtime_snapshot_ref=self.snapshot_id,
            response_obligation=self.response_obligation,
            response_obligation_ref=self.response_obligation_ref,
            conversation_status=self.conversation_status,
            capability_snapshot_ref=self.capability_snapshot_id,
            available_capabilities=self.available_capabilities,
            authorization=self.authorization,
            consent=self.consent,
            safety_decision=self.safety_decision,
            external_export_control=self.external_export_control,
            policy_records=self.policy_records,
            task_ref=self.task_ref,
            required_task_parameters=task.required_parameters if task else (),
            provided_task_parameters=task.provided_parameters if task else {},
            deadline_band=self.deadline_band,
            last_tool_event_ref=event.event_reference if event is not None else None,
            last_tool_event_status=event.status if event is not None else None,
        )

    def to_goal_runtime(self, cognitive_turn_id: str) -> GoalRuntimeContext:
        return GoalRuntimeContext(
            cognitive_turn_id=cognitive_turn_id,
            runtime_snapshot_ref=self.snapshot_id,
            response_obligation=self.response_obligation,
            response_obligation_ref=self.response_obligation_ref,
            conversation_status=self.conversation_status,
            task_ref=self.task_ref,
            available_capabilities=self.available_capabilities,
            authorization=self.authorization,
            consent=self.consent,
            safety_decision=legacy_safety_label(self.safety_decision),
            deadline_band=self.deadline_band,
            last_tool_event=(self.last_tool_event.model_dump(mode="python") if self.last_tool_event is not None else None),
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


class PipelineDialogueContext(FrozenModel):
    """The Dialogue Working Memory V0 handoff a schema-3 request carries.

    The committed snapshot itself stays in the immutable capture; the request
    carries its identity plus exactly the projections the stages consume.
    """

    turn_context_ref: str = Field(min_length=1)
    turn_context_digest: str = Field(min_length=64, max_length=64)
    snapshot_ref: str = Field(min_length=1)
    snapshot_digest: str = Field(min_length=64, max_length=64)
    snapshot_revision: int = Field(ge=0)
    focus_message_ref: str = Field(min_length=1)
    focus_conversation_sequence: int = Field(ge=1)
    projection_version: str = Field(min_length=1)
    interpretation_context: InterpretationDialogueContext
    action_object_refs: tuple[str, ...] = ()
    action_descriptor_refs: tuple[str, ...] = ()
    outside_window_refs: tuple[str, ...] = ()
    # Capture v0.4 only: relationships interpreted from earlier user messages.
    # Omitted when absent, so earlier requests keep their digests.
    interpreted: InterpretedDialogueState | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_interpreted(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("interpreted") is None:
            data.pop("interpreted", None)
        return data

    @model_validator(mode="after")
    def validate_dialogue_context(self) -> PipelineDialogueContext:
        for label, values in (("action object refs", self.action_object_refs),
                              ("action descriptor refs", self.action_descriptor_refs),
                              ("outside-window refs", self.outside_window_refs)):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"dialogue {label} must be sorted and unique")
        if any(not ref.startswith(DIALOGUE_OBJECT_PREFIX) for ref in (*self.action_object_refs,
                                                                     *self.action_descriptor_refs)):
            raise ValueError("dialogue action refs are namespaced")
        context = self.interpretation_context
        if (context.context_ref, context.source_snapshot_ref, context.source_snapshot_digest,
                context.focus_message_ref) != (self.turn_context_ref, self.snapshot_ref, self.snapshot_digest,
                                               self.focus_message_ref):
            raise ValueError("the interpretation dialogue context cites a different turn context")
        if context.projection_version != self.projection_version:
            raise ValueError("the dialogue projections were produced under different versions")
        return self


def _validate_request_dialogue(request: "CognitivePipelineRequest") -> None:
    dialogue = request.dialogue_context
    if (request.schema_version == 3) != (dialogue is not None):
        raise ValueError("a schema-3 request, and only it, carries the dialogue handoff")
    dialogue_objects = tuple(sorted(item.object_id for item in request.action_grounded_objects
                                    if item.object_id.startswith(DIALOGUE_OBJECT_PREFIX)))
    dialogue_descriptors = tuple(sorted(item.ref for item in request.action_target_descriptors
                                        if item.ref.startswith(DIALOGUE_OBJECT_PREFIX)))
    if dialogue is None:
        if dialogue_objects or dialogue_descriptors:
            raise ValueError("dialogue-derived action inputs require the dialogue handoff")
        return
    if dialogue.focus_message_ref != request.focus_message_id:
        raise ValueError("the dialogue handoff belongs to a different focus message")
    if dialogue.interpretation_context.conversation_ref != request.conversation_ref:
        raise ValueError("the dialogue handoff belongs to a different conversation")
    working = request.perception_working_context
    if working is None or (working.source_dialogue_snapshot_ref, working.source_dialogue_snapshot_digest) != (
        dialogue.snapshot_ref, dialogue.snapshot_digest,
    ):
        raise ValueError("the perception working context cites a different dialogue snapshot")
    if (dialogue_objects, dialogue_descriptors) != (dialogue.action_object_refs, dialogue.action_descriptor_refs):
        raise ValueError("dialogue-derived action inputs differ from the dialogue handoff")


class CognitivePipelineRequest(FrozenModel):
    schema_version: Literal[2, 3] = 2
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
    previous_goal_snapshot: GoalSnapshot | None = None
    new_goal_evidence: tuple[GoalEvidence, ...] = ()
    # Trusted proof the predecessor response episode ended (SH-EVAL-001).
    # Omitted from the payload when absent.
    goal_turn_rollover: GoalTurnRollover | None = None
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

    action_personality: ActionPersonalityProfile
    action_grounded_objects: tuple[ActionGroundedObject, ...] = ()
    action_target_descriptors: tuple[ActionTargetDescriptor, ...] = ()
    prior_action_records: tuple[PriorActionRecord, ...] = ()
    prior_realization_records: tuple[PriorRealizationRecord, ...] = ()
    action_observed_outcomes: tuple[ObservedOutcomeRecord, ...] = ()

    cue_manifest_version: str = Field(default=MANIFEST_VERSION, min_length=1)
    ontology_version: str = Field(default=ONTOLOGY_VERSION, min_length=1)
    feature_manifest_version: str = Field(default=FEATURE_MANIFEST_VERSION, min_length=1)
    # Schema 3 only (Dialogue Working Memory V0).  Omitted from a schema-2
    # payload entirely, so v0.2 requests stay byte-identical.
    dialogue_context: PipelineDialogueContext | None = None
    # Trusted turn-boundary facts for requirement resolution (runner x.y.2):
    # the captured turn time, user zone, and user scope.  Omitted when absent.
    requirement_inputs: RequirementResolutionInputs | None = None
    # Trusted conversational-workspace facts (runner x.y.3): the canonical
    # workspace, what the prior turn proposed, and validated deliveries since.
    # Omitted when absent (an empty observational workspace is then used).
    workspace_inputs: WorkspaceTurnInputs | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_dialogue(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for optional in ("dialogue_context", "goal_turn_rollover", "requirement_inputs", "workspace_inputs"):
                if data.get(optional) is None:
                    data.pop(optional, None)
        return data

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
        _validate_request_dialogue(self)
        if self.previous_state.conversation_ref != self.conversation_ref:
            raise ValueError("previous state belongs to a different conversation")
        if self.previous_state.personality_mapper_version != self.state_personality.mapper_version:
            raise ValueError("previous state personality mapper differs from the supplied profile")
        if self.interpretation_biases.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("interpretation bias profile belongs to a different core memory")
        if self.action_personality.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("action personality profile belongs to a different core memory")
        _require_non_blank(
            "action personality identifiers",
            (self.action_personality.profile_id, self.action_personality.mapper_version),
        )
        if any(not item.authorized for item in (
            *self.prior_action_records, *self.prior_realization_records, *self.action_observed_outcomes,
        )):
            raise ValueError("unauthorized prior action records cannot enter the pipeline")
        if any(item.cognitive_turn_id != self.cognitive_turn_id for item in self.action_grounded_objects):
            raise ValueError("action grounded objects must belong to this cognitive turn")
        if self.runtime.response_obligation != "required":
            raise ValueError("response pipeline requires a trusted response obligation")
        if any(not memory.authorized for memory in self.memories):
            raise ValueError("unauthorized memories cannot enter the pipeline")
        if self.relationship is not None and not self.relationship.authorized:
            raise ValueError("unauthorized relationship context cannot enter the pipeline")
        identity_groups: tuple[tuple[str, tuple[object, ...]], ...] = (
            ("memory ids", tuple(item.memory_id for item in self.memories)),
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
        evidence_ids = tuple(item.evidence_id for item in self.new_goal_evidence)
        _require_unique("goal evidence ids", evidence_ids)
        workspace = self.workspace_inputs
        if workspace is not None and workspace.previous_snapshot is not None and (
            workspace.previous_snapshot.conversation_ref != self.conversation_ref
        ):
            raise ValueError("the workspace inputs belong to a different conversation")
        if workspace is not None and any(item.conversation_ref != self.conversation_ref
                                         for item in workspace.delivered_outcomes):
            raise ValueError("a delivered outcome belongs to a different conversation")
        rollover = self.goal_turn_rollover
        if rollover is not None:
            previous = self.previous_goal_snapshot
            if previous is None or (
                rollover.conversation_ref != self.conversation_ref
                or rollover.closed_goal_snapshot_ref != previous.goal_snapshot_id
                or rollover.closed_turn_ref != previous.cognitive_turn_id
                or rollover.closing_turn_ref != self.cognitive_turn_id
                or rollover.closing_response_obligation_ref != self.runtime.response_obligation_ref
            ):
                raise ValueError("the goal turn rollover does not match this request's lineage")
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
    goal: GoalVersions
    action: ActionVersions
    # Runner x.y.2 onwards; omitted for historical traces.
    requirement_resolution: RequirementResolutionVersions | None = None
    # Runner x.y.3 onwards; omitted for historical traces.
    collaboration: CollaborationVersions | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_requirement_versions(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in ("requirement_resolution", "collaboration", "orchestration"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data


class CognitivePipelineTimings(FrozenModel):
    perception_ms: float
    interpretation_ms: float
    goal_update_ms: float
    cognitive_turn_ms: float
    total_ms: float
    # Runner x.y.2 onwards; a historical trace ran no resolution stage.
    requirement_resolution_ms: float = 0.0
    # Runner x.y.3 onwards (zero for historical traces).
    workspace_resolution_ms: float = 0.0
    collaborative_content_ms: float = 0.0

    @model_serializer(mode="wrap")
    def _omit_zero_collaboration_timings(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in ("workspace_resolution_ms", "collaborative_content_ms"):
                if data.get(name) == 0.0:
                    data.pop(name, None)
        return data

    @model_validator(mode="after")
    def validate_timings(self) -> CognitivePipelineTimings:
        values = (
            self.perception_ms, self.interpretation_ms, self.goal_update_ms,
            self.requirement_resolution_ms, self.cognitive_turn_ms, self.total_ms,
            self.workspace_resolution_ms, self.collaborative_content_ms,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("pipeline timings must be finite and non-negative")
        if not math.isclose(
            self.total_ms,
            self.perception_ms + self.interpretation_ms + self.goal_update_ms
            + self.requirement_resolution_ms + self.cognitive_turn_ms
            + self.workspace_resolution_ms + self.collaborative_content_ms,
            rel_tol=1e-9,
            abs_tol=1e-3,
        ):
            raise ValueError("pipeline total timing must equal its stage timings")
        return self


class PipelineRequirementContent(FrozenModel):
    """The trusted producer query, result, and explicit gap behind one content resolution."""

    resolution_ref: str = Field(min_length=1)
    query: SemanticQuery
    result: ProducerResult
    gap: KnowledgeGapRecord | None = None


class PipelineRequirementResolution(FrozenModel):
    """Requirement resolution of one turn, exactly as the cognitive turn consumed it.

    ``handoff`` is the resolution snapshot plus its action bindings;
    ``grounded_objects``/``target_descriptors`` are exactly what the
    projection appended to the turn's Action inputs; ``realization_content``
    is the closed-world content those objects may be worded from.  The trace
    validator recomputes the projection from these records, so none of it can
    be edited independently of the resolutions it came from.
    """

    versions: RequirementResolutionVersions
    handoff: RequirementActionHandoff
    content_records: tuple[PipelineRequirementContent, ...] = ()
    dialogue_refs: tuple[str, ...] = ()
    grounded_objects: tuple[ActionGroundedObject, ...] = ()
    target_descriptors: tuple[ActionTargetDescriptor, ...] = ()
    realization_content: tuple[RealizationContentItem, ...] = ()

    @model_validator(mode="after")
    def validate_resolution(self) -> PipelineRequirementResolution:
        snapshot = self.handoff.snapshot
        if (self.versions.contract, self.versions.engine, self.versions.manifest,
                self.versions.producer_manifest, self.versions.projection) != (
                snapshot.contract_version, snapshot.engine_version, snapshot.manifest_version,
                snapshot.producer_manifest_version, self.handoff.projection_version):
            raise ValueError("requirement versions differ from the snapshot and handoff they describe")
        if self.dialogue_refs != tuple(sorted(set(self.dialogue_refs))):
            raise ValueError("requirement dialogue refs must be sorted and unique")
        provenance = {item.resolution_ref: item for item in snapshot.content_queries}
        if tuple(item.resolution_ref for item in self.content_records) != tuple(provenance):
            raise ValueError("every content resolution, in order, has exactly one producer record")
        for record in self.content_records:
            expected = provenance[record.resolution_ref]
            result = record.result
            if (record.query.query_id, record.query.query_kind, result.result_id,
                    ContentProducerStatus(result.status.value), result.reason.value if result.reason else None,
                    tuple(sorted(fact.fact_id for fact in result.facts)),
                    record.gap.gap_id if record.gap is not None else None) != (
                    expected.query_id, expected.query_kind, expected.result_ref, expected.producer_status,
                    expected.producer_reason, expected.fact_refs, expected.gap_ref):
                raise ValueError("a producer record differs from the resolution's recorded provenance")
            if CONTENT_STATUS_BY_PRODUCER[ContentProducerStatus(result.status.value)] != snapshot.resolution(
                record.resolution_ref,
            ).status:
                raise ValueError("a producer record misstates its resolution")
        object_ids = tuple(item.object_id for item in self.grounded_objects)
        if object_ids != tuple(sorted(set(object_ids))):
            raise ValueError("projected objects must be unique and in canonical order")
        content_ids = {item.content_id for item in self.realization_content}
        for binding in self.handoff.bindings:
            if not set(binding.object_refs) <= set(object_ids):
                raise ValueError("a binding names an object the projection did not create")
            if not set(binding.content_refs) <= content_ids:
                raise ValueError("a binding names content the projection did not create")
        if any(item.cognitive_turn_id != snapshot.cognitive_turn_id
               for item in (*self.grounded_objects, *self.realization_content)):
            raise ValueError("projected objects and content belong to the resolution's turn")
        return self


class PipelineCollaboration(FrozenModel):
    """The workspace and collaborative-content stages of one turn, exactly as Action consumed them.

    ``workspace`` (reconciliation, canonical snapshot, targets) and
    ``outcomes`` (proposals, typed failures, skips, with each recorded
    provider candidate) stay distinguishable.  ``grounded_objects`` and
    ``realization_content`` are what the projection appended; the trace
    validator recomputes the projection from the recorded outcomes.
    """

    versions: CollaborationVersions
    workspace: WorkspaceResolution
    outcomes: tuple[CollaborationGoalOutcome, ...] = ()
    turn_record: CollaborationTurnRecord
    handoff: CollaborationActionHandoff
    grounded_objects: tuple[ActionGroundedObject, ...] = ()
    realization_content: tuple[RealizationContentItem, ...] = ()

    @model_validator(mode="after")
    def validate_collaboration(self) -> PipelineCollaboration:
        turn = self.workspace.cognitive_turn_id
        if (self.handoff.cognitive_turn_id, self.turn_record.turn_ref) != (turn, turn):
            raise ValueError("collaboration records belong to different turns")
        goals = tuple(item.goal_ref for item in self.outcomes)
        if goals != tuple(sorted(set(goals))) or goals != tuple(item.goal_ref for item in self.handoff.bindings):
            raise ValueError("one outcome and one binding per deliverable goal, in canonical order")
        targets = {item.goal_ref for item in self.workspace.targets}
        if not set(goals) <= targets:
            raise ValueError("every collaboration outcome has a workspace target")
        proposals = tuple(item.proposal for item in self.outcomes if item.proposal is not None)
        if tuple(sorted(item.content_proposal_id for item in proposals)) != tuple(
            item.content_proposal_id for item in self.turn_record.proposals
        ):
            raise ValueError("the turn record holds exactly this turn's proposals")
        if any(item.cognitive_turn_id != turn for item in (*self.grounded_objects, *self.realization_content)):
            raise ValueError("projected collaboration objects and content belong to their turn")
        return self


from ..two_call.contracts import TwoCallTrace


class CognitivePipelineTrace(FrozenModel):
    schema_version: Literal[2, 3] = 2
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
    goal_snapshot: GoalSnapshot
    cognitive_turn: CognitiveTurnTrace
    action_selection: ActionSelectionResult
    versions: CognitivePipelineVersions
    timings: CognitivePipelineTimings
    # Runner x.y.2 onwards; omitted (and absent) for historical traces.
    requirement_resolution: PipelineRequirementResolution | None = None
    # Runner x.y.3 onwards; omitted (and absent) for historical traces.
    collaboration: PipelineCollaboration | None = None
    orchestration: TwoCallTrace | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_requirement_resolution(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in ("requirement_resolution", "collaboration", "orchestration"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data

    @model_validator(mode="after")
    def validate_trace(self) -> CognitivePipelineTrace:
        turn = self.cognitive_turn
        state = turn.dynamic_state_update.snapshot
        model_choices = tuple(step.model_choice for step in self.action_selection.steps)
        model_selected = any(choice is not None for choice in model_choices)
        if model_selected and (self.orchestration is None or self.orchestration.behavior_policy not in {
                'model_grounded_selection-v0.1', 'model_policy_selection-v1', 'model_policy_selection-v1.1'}):
            raise ValueError('model action selection requires captured two-call provenance')
        if self.orchestration is not None and self.orchestration.behavior_policy in {'model_policy_selection-v1', 'model_policy_selection-v1.1'}:
            from ..two_call.provider import digest
            from ..two_call.v1 import move_identity
            from ..two_call.v1_contracts import CompiledPolicy
            from ..contracts.actions import MODEL_POLICY_PLANNER_VERSION, MODEL_PRIMARY_RULE_ID, MODEL_PRIMARY_CHOICE_RULE_ID
            source = self.orchestration.calls[1]
            compiled = CompiledPolicy.model_validate(source.compiled_proposal)
            moves = self.action_selection.plan.moves
            expected_primary_rule = (MODEL_PRIMARY_CHOICE_RULE_ID if self.orchestration.behavior_policy == 'model_policy_selection-v1.1'
                                     else MODEL_PRIMARY_RULE_ID)
            if self.action_selection.plan.primary_designation is not None and (
                    self.action_selection.plan.primary_designation.rule_id != expected_primary_rule):
                raise ValueError('primary rule differs from the captured behavior policy')
            if (self.action_selection.versions.planner != MODEL_POLICY_PLANNER_VERSION or not model_selected
                    or any(choice is None or choice.source_call_digest != digest(source.proposal) for choice in model_choices)
                    or tuple(move_identity(move) for move in moves) != tuple(compiled.selected_identities)):
                raise ValueError('accepted model actions differ from their captured proposal')
            primary = self.action_selection.plan.primary_move_ref
            if (compiled.primary_index is None) != (primary is None) or (
                    primary is not None and moves[compiled.primary_index].move_id != primary):
                raise ValueError('accepted primary move differs from the captured proposal')
        if self.orchestration is not None and self.orchestration.behavior_policy == 'model_grounded_selection-v0.1':
            from ..two_call.provider import digest
            source = self.orchestration.calls[1]
            proposals = (source.compiled_proposal or source.proposal)['behavior_proposal']
            if (not model_selected or len(proposals) != len(self.action_selection.plan.moves)
                or any(choice is None or choice.source_call_digest != digest(source.proposal) for choice in model_choices)
                or any(move.definition_ref.action_key != proposal['action_key']
                       for move,proposal in zip(self.action_selection.plan.moves, proposals))):
                raise ValueError('accepted model actions differ from their captured proposal')
        if self.orchestration is not None and self.orchestration.protocol == 'cognitive-two-call-v1':
            registry = self.orchestration.calls[0].payload['ontology']['predicate_registry_version']
            from ..two_call.v1_grounding import project_understanding
            expected, descriptors = project_understanding(self.interpretation, turn_id=self.cognitive_turn_id,
                                                          registry=registry)
            objects = turn.input_snapshot.action_grounded_objects
            tail = len(self.collaboration.grounded_objects) if self.collaboration is not None else 0
            placed = tuple(objects[len(objects) - tail - len(expected):len(objects) - tail])
            supplied = turn.input_snapshot.action_target_descriptors
            if placed != expected or (descriptors and tuple(supplied[-len(descriptors):]) != descriptors):
                raise ValueError('understanding objects differ from their deterministic projection')
        if self.orchestration is not None:
            bindings = self.orchestration.goal_bindings
            refs = tuple(binding.goal_ref for binding in bindings)
            if len(refs) != len(set(refs)):
                raise ValueError("duplicate goal formation bindings")
            goals = {g.goal_id for g in self.goal_snapshot.records}
            frames = {f.frame_id for f in self.interpretation.semantic_frames}
            if any(binding.goal_ref not in goals or not set(binding.frame_refs) <= frames for binding in bindings):
                raise ValueError("foreign goal formation binding")
            call_two = self.orchestration.calls[1]
            if self.orchestration.protocol=='cognitive-two-call-v1':
                from ..two_call.compact import ReferenceTable
                from ..two_call.v1 import policy_state_view
                expected_state=policy_state_view(state,ReferenceTable(call_two.reference_table))
                actual_state=call_two.model_dump(mode='json')['payload']['state_after']
            elif self.orchestration.protocol!='legacy-v0.1':
                from ..two_call.compact import ReferenceTable,state_view
                expected_state=ReferenceTable(call_two.reference_table).encode(state_view(state,ReferenceTable(call_two.reference_table)))
                actual_state=call_two.model_dump(mode='json')['payload']['decision_context']['state_after']
            else:
                expected_state=state.model_dump(mode="json")
                actual_state=call_two.model_dump(mode="json")["payload"].get("post_update_state")
            if actual_state != expected_state:
                raise ValueError("combined call did not use the actual post-update state")
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
        if self.goal_snapshot.cognitive_turn_id != self.cognitive_turn_id or self.goal_snapshot.conversation_ref != self.conversation_ref:
            raise ValueError("pipeline goal snapshot ownership differs")
        # Object identity is an in-process handoff invariant enforced by the
        # runner.  The persisted contract must instead require canonical value
        # equality so a valid trace can survive JSON round trips.
        if turn.input_snapshot.goal_snapshot != self.goal_snapshot:
            raise ValueError("pipeline did not retain the canonical goal snapshot")
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
        if self.versions.goal != self.goal_snapshot.versions:
            raise ValueError("pipeline goal versions differ from snapshot")
        if self.versions.cognitive_turn != turn.versions:
            raise ValueError("pipeline cognitive-turn versions were reconstructed incorrectly")
        if self.action_selection != turn.action_selection:
            raise ValueError("pipeline action selection differs from the cognitive turn's")
        if self.versions.action != self.action_selection.versions or self.versions.action != turn.versions.action_versions:
            raise ValueError("pipeline action versions were reconstructed incorrectly")
        if not self.action_selection.plan.moves:
            raise ValueError("a response pipeline cannot complete with an empty action plan")
        schema, runner = PIPELINE_VERSIONS_BY_SCHEMA[self.schema_version]
        if self.versions.pipeline_schema != schema:
            raise ValueError("pipeline schema version mismatch")
        if (
            self.versions.pipeline_runner != runner
            and self.versions.pipeline_runner not in HISTORICAL_PIPELINE_RUNNER_VERSIONS[self.schema_version]
        ):
            raise ValueError("pipeline runner version mismatch")
        self._validate_requirement_resolution(runner)
        self._validate_collaboration()
        return self

    def _validate_collaboration(self) -> None:
        """Runner x.y.3 always records the workspace and collaboration stages; earlier runners never did."""

        collaboration = self.collaboration
        turn_input = self.cognitive_turn.input_snapshot
        if (self.versions.pipeline_runner in COLLABORATION_RUNNERS) != (collaboration is not None):
            raise ValueError("exactly a collaboration-runner trace records the collaboration stages")
        if (collaboration is not None) != (self.versions.collaboration is not None):
            raise ValueError("collaboration versions are recorded exactly with the collaboration stages")
        if collaboration is None:
            if turn_input.collaboration_handoff is not None:
                raise ValueError("a historical trace carries no collaboration handoff")
            return
        if self.versions.collaboration != collaboration.versions or collaboration.versions != CollaborationVersions():
            raise ValueError("pipeline collaboration versions differ from the recorded stages")
        if turn_input.collaboration_handoff != collaboration.handoff:
            raise ValueError("the cognitive turn consumed a different collaboration handoff")
        workspace = collaboration.workspace
        if (workspace.cognitive_turn_id, workspace.conversation_ref) != (self.cognitive_turn_id, self.conversation_ref):
            raise ValueError("the workspace resolution belongs to a different turn")
        count = len(collaboration.grounded_objects)
        supplied = turn_input.action_grounded_objects
        if count and tuple(supplied[-count:]) != collaboration.grounded_objects:
            raise ValueError("the collaboration objects are not the turn's final action objects")
        from ..collaborative_content_v0.projection import CollaborationProjectorV0

        resolution = self.requirement_resolution
        projected = CollaborationProjectorV0().project(
            collaboration.outcomes, interpretation=self.interpretation, goal_snapshot=self.goal_snapshot,
            workspace=workspace, runtime=turn_input.action_runtime,
            requirement_handoff=resolution.handoff if resolution is not None else None,
            existing_objects=tuple(supplied[:len(supplied) - count]),
        )
        if (projected.grounded_objects, projected.realization_content, projected.handoff, projected.turn_record) != (
            collaboration.grounded_objects, collaboration.realization_content, collaboration.handoff,
            collaboration.turn_record,
        ):
            raise ValueError("the recorded collaboration projection is not the one its outcomes produce")

    def _validate_requirement_resolution(self, current_runner: str) -> None:
        """Runners from x.y.2 always resolve; earlier runners never did."""

        resolution = self.requirement_resolution
        turn_input = self.cognitive_turn.input_snapshot
        if (self.versions.pipeline_runner in REQUIREMENT_RESOLUTION_RUNNERS) != (resolution is not None):
            raise ValueError("exactly a resolving-runner trace records requirement resolution")
        if (resolution is not None) != (self.versions.requirement_resolution is not None):
            raise ValueError("requirement versions are recorded exactly with requirement resolution")
        if resolution is None:
            if turn_input.requirement_handoff is not None:
                raise ValueError("a historical trace carries no requirement handoff")
            return
        if self.versions.requirement_resolution != resolution.versions:
            raise ValueError("pipeline requirement versions differ from the recorded resolution")
        if turn_input.requirement_handoff != resolution.handoff:
            raise ValueError("the cognitive turn consumed a different requirement handoff")
        count = len(resolution.grounded_objects)
        supplied = turn_input.action_grounded_objects
        if self.collaboration is not None and self.collaboration.grounded_objects:
            # Collaboration objects follow the requirement projection.
            supplied = supplied[:len(supplied) - len(self.collaboration.grounded_objects)]
        if count and tuple(supplied[-count:]) != resolution.grounded_objects:
            raise ValueError("the projected objects are not the turn's final action objects")
        prefix_objects = supplied[:len(supplied) - count]
        described = len(resolution.target_descriptors)
        descriptors = turn_input.action_target_descriptors
        if described and tuple(descriptors[-described:]) != resolution.target_descriptors:
            raise ValueError("the projected descriptors are not the turn's final descriptors")
        prefix_descriptors = descriptors[:len(descriptors) - described]
        from ..requirement_resolution_v0.engine import workspace_references

        validate_requirement_snapshot(
            resolution.handoff.snapshot, interpretation=self.interpretation, goal_snapshot=self.goal_snapshot,
            runtime_refs=_runtime_refs(turn_input.action_runtime), dialogue_refs=resolution.dialogue_refs,
            workspace_refs=workspace_references(self.collaboration.workspace if self.collaboration else None),
        )
        if resolution.handoff.projection_version != REQUIREMENT_ACTION_PROJECTION_VERSION:
            # A historical projection is kept for audit; it is never recomputed
            # under the installed projector's semantics.
            return
        from ..requirement_resolution_v0.engine import ContentRecord, RequirementResolutionOutcome
        from ..requirement_resolution_v0.projection import RequirementProjectorV0

        projected = RequirementProjectorV0().project(
            RequirementResolutionOutcome(resolution.handoff.snapshot, tuple(
                ContentRecord(item.resolution_ref, item.query, item.result, item.gap)
                for item in resolution.content_records
            )),
            interpretation=self.interpretation, goal_snapshot=self.goal_snapshot,
            runtime=turn_input.action_runtime, existing_objects=tuple(prefix_objects),
            existing_descriptors=tuple(prefix_descriptors),
        )
        if (projected.grounded_objects, projected.target_descriptors, projected.realization_content,
                projected.handoff) != (resolution.grounded_objects, resolution.target_descriptors,
                                       resolution.realization_content, resolution.handoff):
            raise ValueError("the recorded requirement projection is not the one its resolutions produce")


def _runtime_refs(runtime: ActionRuntimeContext) -> tuple[str, ...]:
    """Trusted runtime references a requirement resolution of this turn may cite."""

    refs = [runtime.runtime_snapshot_ref, runtime.capability_snapshot_ref, *runtime.policy_records,
            runtime.safety_basis.decision_ref]
    if runtime.safety_decision is not None:
        refs.append(runtime.safety_decision.producer_ref)
    return tuple(sorted(set(refs)))


def project_memory(memory: InterpretationMemory) -> MemoryRecord:
    """Produce the single allowed, loss-aware Appraisal compatibility view."""

    return MemoryRecord(
        memory_id=memory.memory_id,
        memory_type=memory.memory_type,
        content=memory.content,
        provenance=memory.provenance,
        status=memory.status,
    )
