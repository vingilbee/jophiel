"""Canonical shadow orchestration across all implemented cognitive V0 stages."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import NoReturn
from uuid import uuid4

from ..cognitive_turn import (
    CognitiveTurnExecutionError,
    CognitiveTurnRequest,
    CognitiveTurnRunner,
    CognitiveTurnTrace,
)
from ..contracts.interpretation import InterpretationEnvelope
from ..interpretation_v0 import (
    InterpretationEngine,
    InterpretationExecutionError,
    InterpretationRequest,
)
from ..interpretation_v0.evidence import validate_evidence_registry
from ..perception_v0 import PerceptionEngine, PerceptionEvent, PerceptionRequest
from ..perception_v0.evidence import validate_evidence
from ..goal_v0 import GoalEngine, GoalExecutionError
from ..goal_v0.models import GoalUpdateRequest
from ..contracts.goals import GoalUpdateTrigger
from ..contracts.requirement_resolution import RequirementResolutionVersions
from ..requirement_resolution_v0 import (
    RequirementProjectorV0,
    RequirementResolutionEngine,
    RequirementResolutionRequest,
)
from ..collaborative_content_v0 import CollaborationGoalInput, CollaborativeContentEngine
from ..collaborative_content_v0.projection import CollaborationProjectorV0
from ..contracts.collaboration import CollaborationVersions, WorkspaceTurnInputs
from ..contracts.workspace import OpenInquiryStatus, WorkspaceAuthorityMode, empty_workspace
from ..conversation_workspace_v0 import WORKSPACE_GOALS, reconcile, resolve_workspace
from ..interpretation_v0.models import InterpretationOpenInquiry
from .errors import CognitivePipelineExecutionError, PipelineFailureStage
from .models import (
    PIPELINE_VERSIONS_BY_SCHEMA,
    CognitivePipelineRequest,
    CognitivePipelineTimings,
    CognitivePipelineTrace,
    CognitivePipelineVersions,
    PipelineCollaboration,
    PipelineRequirementContent,
    PipelineRequirementResolution,
    project_memory,
)


class CognitivePipelineV0:
    def __init__(
        self,
        *,
        perception_engine: PerceptionEngine | None = None,
        interpretation_engine: InterpretationEngine | None = None,
        cognitive_turn_runner: CognitiveTurnRunner | None = None,
        goal_engine: GoalEngine | None = None,
        requirement_engine: RequirementResolutionEngine | None = None,
        collaboration_engine: CollaborativeContentEngine | None = None,
        trace_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.perception_engine = perception_engine or PerceptionEngine()
        self.interpretation_engine = interpretation_engine or InterpretationEngine()
        self.cognitive_turn_runner = cognitive_turn_runner or CognitiveTurnRunner()
        self.goal_engine = goal_engine or GoalEngine()
        self.requirement_engine = requirement_engine or RequirementResolutionEngine()
        self.requirement_projector = RequirementProjectorV0()
        # No provider by default: every ready deliverable then records a typed
        # ``provider_not_configured`` failure -- never content from elsewhere.
        self.collaboration_engine = collaboration_engine or CollaborativeContentEngine()
        self.collaboration_projector = CollaborationProjectorV0()
        self.trace_id_factory = trace_id_factory or (lambda: str(uuid4()))
        self.clock = clock or time.monotonic

    def run(self, request: CognitivePipelineRequest) -> CognitivePipelineTrace:
        turn_id = getattr(request, "cognitive_turn_id", "unknown-turn")
        two_call = getattr(self, "two_call", None)

        # Authorization is checked without dumping caller-controlled content.
        try:
            if two_call is not None:
                two_call.begin(request)
            if any(not memory.authorized for memory in request.memories):
                raise ValueError("unauthorized memory")
            if request.relationship is not None and not request.relationship.authorized:
                raise ValueError("unauthorized relationship")
            validated_request = CognitivePipelineRequest.model_validate(
                request.model_dump(mode="python")
            )
            if validated_request != request:
                raise ValueError("pipeline request changed during validation")
            started = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            self._raise(
                turn_id, "validation", "The cognitive pipeline input failed validation.", exc
            )

        try:
            perception_request = PerceptionRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                messages=request.messages,
                focus_message_id=request.focus_message_id,
                runtime=request.runtime.to_perception_runtime(),
                working_context=request.perception_working_context,
                language_tag=request.language_tag,
                cue_manifest_version=request.cue_manifest_version,
                context_truncated=request.context_truncated,
            )
            emitted_perception = self.perception_engine.perceive(perception_request)
            validated_perception = PerceptionEvent.model_validate(
                emitted_perception.model_dump(mode="python")
            )
            if validated_perception != emitted_perception:
                raise ValueError("perception output changed during validation")
            self._validate_perception(emitted_perception, request, perception_request)
            perception = emitted_perception
            perception_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            self._raise(
                turn_id, "perception", "Perception could not complete for this pipeline.", exc
            )

        try:
            handoff = perception.interpretation_handoff()
            if handoff.cue_activations != perception.activations_for_interpretation():
                raise ValueError("perception handoff contains different active cues")
            if any(
                handed is not emitted
                for handed, emitted in zip(
                    handoff.cue_activations, perception.activations_for_interpretation()
                )
            ):
                raise ValueError("perception activation identity was not preserved")
            if handoff.versions is not perception.versions:
                raise ValueError("perception version identity was not preserved")
            expected_handoff_ownership = (
                perception.perception_event_id,
                perception.cognitive_turn_id,
                perception.input_message_ids,
                perception.focus_message_id,
                perception.runtime_snapshot_id,
                perception.context_truncated,
            )
            actual_handoff_ownership = (
                handoff.perception_event_id,
                handoff.cognitive_turn_id,
                handoff.input_message_ids,
                handoff.focus_message_id,
                handoff.runtime_snapshot_id,
                handoff.context_truncated,
            )
            if actual_handoff_ownership != expected_handoff_ownership:
                raise ValueError("perception handoff ownership differs from its event")
        except Exception as exc:
            self._raise(
                turn_id,
                "handoff_validation",
                "The Perception-to-Interpretation handoff failed validation.",
                exc,
            )

        try:
            workspace_inputs = self._workspace_inputs(request)
            open_inquiries = self._open_inquiries(request, workspace_inputs)
        except Exception as exc:
            self._raise(
                turn_id, "workspace_resolution",
                "The conversational workspace could not be reconciled for this pipeline.", exc,
            )

        try:
            interpretation_request = InterpretationRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                perception=handoff,
                messages=request.messages,
                runtime=request.runtime.to_interpretation_runtime(),
                memories=request.memories,
                relationship=(
                    request.relationship.to_interpretation_relationship()
                    if request.relationship is not None else None
                ),
                biases=request.interpretation_biases,
                language_tag=request.language_tag,
                ontology_version=request.ontology_version,
                feature_manifest_version=request.feature_manifest_version,
                dialogue_context=(
                    request.dialogue_context.interpretation_context
                    if request.dialogue_context is not None else None
                ),
                open_inquiries=open_inquiries,
            )
            self._validate_interpretation_projection(interpretation_request, request, handoff)
            emitted_interpretation = self.interpretation_engine.interpret(interpretation_request)
            validated_interpretation = InterpretationEnvelope.model_validate(
                emitted_interpretation.model_dump(mode="python")
            )
            if validated_interpretation != emitted_interpretation:
                raise ValueError("interpretation output changed during validation")
            validate_evidence_registry(
                emitted_interpretation.evidence, interpretation_request
            )
            self._validate_interpretation(
                emitted_interpretation, request, perception, handoff
            )
            interpretation = emitted_interpretation
            interpretation_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            nested = exc.failed_phase if isinstance(exc, InterpretationExecutionError) else None
            self._raise(
                turn_id,
                "interpretation",
                "Interpretation could not complete for this pipeline.",
                exc,
                nested_stage=nested,
            )

        try:
            goal_update = self.goal_engine.update(GoalUpdateRequest(
                trigger=GoalUpdateTrigger.RESPONSE_TURN,
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                interpretation=interpretation,
                runtime=request.runtime.to_goal_runtime(request.cognitive_turn_id),
                previous_goal_snapshot=request.previous_goal_snapshot,
                previous_dynamic_state=request.previous_state,
                new_goal_evidence=request.new_goal_evidence,
                turn_rollover=request.goal_turn_rollover,
            ))
            goal_snapshot = goal_update.snapshot
            goal_finished = self._clock_value(turn_id)
        except GoalExecutionError as exc:
            self._raise(turn_id, "goal_update", "Goal processing could not complete for this pipeline.", exc, nested_stage=exc.failed_phase)
        except Exception as exc:
            self._raise(turn_id, "goal_update", "Goal processing could not complete for this pipeline.", exc)

        try:
            workspace = resolve_workspace(
                cognitive_turn_id=request.cognitive_turn_id, conversation_ref=request.conversation_ref,
                inputs=workspace_inputs, interpretation=interpretation, goal_snapshot=goal_snapshot,
                dialogue_context=(request.dialogue_context.interpretation_context
                                  if request.dialogue_context is not None else None),
            )
            workspace_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            self._raise(
                turn_id, "workspace_resolution",
                "The conversational workspace could not be resolved for this pipeline.", exc,
            )

        try:
            requirement = self._resolve_requirements(request, interpretation, goal_snapshot, workspace)
            requirement_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            self._raise(
                turn_id, "requirement_resolution",
                "Requirement resolution could not complete for this pipeline.", exc,
            )

        try:
            if two_call is None:
                collaboration = self._collaborate(request, interpretation, goal_snapshot, workspace, requirement)
            else:
                # No content provider runs before Appraisal/state. This empty
                # projection is replaced by compiled second-call content.
                projection = self.collaboration_projector.project((), interpretation=interpretation,
                    goal_snapshot=goal_snapshot, workspace=workspace, runtime=request.runtime.to_action_runtime(),
                    requirement_handoff=requirement.handoff,
                    existing_objects=(*request.action_grounded_objects, *requirement.grounded_objects))
                collaboration = PipelineCollaboration(versions=CollaborationVersions(), workspace=workspace,
                    outcomes=(), turn_record=projection.turn_record, handoff=projection.handoff,
                    grounded_objects=projection.grounded_objects, realization_content=projection.realization_content)
            collaboration_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            self._raise(
                turn_id, "collaborative_content",
                "Collaborative content production could not complete for this pipeline.", exc,
            )

        try:
            appraisal_memories = tuple(project_memory(memory) for memory in request.memories)
            turn_request = CognitiveTurnRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                input_message_ids=tuple(message.message_id for message in request.messages),
                focus_message_id=request.focus_message_id,
                processing_path=request.processing_path,
                interpretation=interpretation,
                goal_snapshot=goal_snapshot,
                active_goals=goal_update.active_goal_views,
                runtime=request.runtime.to_appraisal_runtime(),
                memories=appraisal_memories,
                values=request.values,
                relationship=(
                    request.relationship.to_appraisal_relationship()
                    if request.relationship is not None else None
                ),
                supplemental_inputs=request.supplemental_inputs,
                previous_state=request.previous_state,
                personality=request.state_personality,
                grounded_objects=request.grounded_objects,
                control_context=request.control_context,
                outcomes=request.outcomes,
                elapsed_monotonic_ms=request.elapsed_monotonic_ms,
                core_memory_ref=request.core_memory_ref,
                memory_retrieval_policy_version=request.memory_retrieval_policy_version,
                action_runtime=request.runtime.to_action_runtime(),
                action_personality=request.action_personality,
                action_grounded_objects=(
                    *request.action_grounded_objects, *requirement.grounded_objects, *collaboration.grounded_objects,
                ),
                action_target_descriptors=(
                    *request.action_target_descriptors, *requirement.target_descriptors,
                ),
                prior_action_records=request.prior_action_records,
                prior_realization_records=request.prior_realization_records,
                action_observed_outcomes=request.action_observed_outcomes,
                requirement_handoff=requirement.handoff,
                collaboration_handoff=collaboration.handoff,
            )
            if turn_request.interpretation is not interpretation:
                raise ValueError("interpretation identity was not preserved in turn request")
            if turn_request.action_personality is not request.action_personality:
                raise ValueError("action personality identity was not preserved in turn request")
            self._validate_appraisal_projections(turn_request, request)
            self._validate_action_projection(turn_request, request, requirement, collaboration)
        except Exception as exc:
            self._raise(
                turn_id,
                "goal_handoff_validation",
                "The cognitive-turn request could not be constructed.",
                exc,
            )

        try:
            if hasattr(getattr(self.cognitive_turn_runner, "appraisal_engine", None), "appraise_turn"):
                from ..appraisal.context import project_context, with_dialogue
                context = with_dialogue(project_context(turn_request), request.messages, request.focus_message_id)
                turn_request = CognitiveTurnRequest(**{name: getattr(turn_request, name)
                    for name in CognitiveTurnRequest.model_fields} | {"appraisal_model_context": context})
            if two_call is None:
                emitted_turn = self.cognitive_turn_runner.evaluate(turn_request)
            else:
                def prepare_action(turn, appraisal, update):
                    nonlocal turn_request, collaboration
                    turn_request, collaboration = two_call.prepare(self, turn, appraisal, update,
                        interpretation=interpretation, goals=goal_snapshot, workspace=workspace, requirement=requirement)
                    self._validate_action_projection(turn_request, request, requirement, collaboration,
                                                     understanding=two_call.understanding_objects,
                                                     understanding_descriptors=two_call.understanding_descriptors)
                    return turn_request
                emitted_turn = self.cognitive_turn_runner.evaluate(turn_request,
                    prepare_action=prepare_action, validate_action=two_call.validate_plan)
            validated_turn = CognitiveTurnTrace.model_validate(
                emitted_turn.model_dump(mode="python")
            )
            if validated_turn != emitted_turn:
                raise ValueError("cognitive-turn output changed during validation")
            self._validate_turn(emitted_turn, turn_request, interpretation)
            cognitive_turn_finished = self._clock_value(turn_id)
        except CognitivePipelineExecutionError:
            raise
        except Exception as exc:
            nested = exc.failed_stage if isinstance(exc, CognitiveTurnExecutionError) else None
            nested_phase = exc.action_failure_phase if isinstance(exc, CognitiveTurnExecutionError) else None
            self._raise(
                turn_id,
                "cognitive_turn",
                "The cognitive turn could not complete for this pipeline.",
                exc,
                nested_stage=nested,
                nested_phase=nested_phase,
            )

        try:
            pipeline_schema, pipeline_runner = PIPELINE_VERSIONS_BY_SCHEMA[request.schema_version]
            versions = CognitivePipelineVersions(
                pipeline_schema=pipeline_schema,
                pipeline_runner=pipeline_runner,
                perception=perception.versions,
                interpretation=interpretation.versions,
                cognitive_turn=emitted_turn.versions,
                goal=goal_snapshot.versions,
                action=emitted_turn.action_selection.versions,
                requirement_resolution=requirement.versions,
                collaboration=collaboration.versions,
            )
            timings = CognitivePipelineTimings(
                perception_ms=(perception_finished - started) * 1000,
                interpretation_ms=(interpretation_finished - perception_finished) * 1000,
                goal_update_ms=(goal_finished - interpretation_finished) * 1000,
                workspace_resolution_ms=(workspace_finished - goal_finished) * 1000,
                requirement_resolution_ms=(requirement_finished - workspace_finished) * 1000,
                collaborative_content_ms=(collaboration_finished - requirement_finished) * 1000,
                cognitive_turn_ms=(cognitive_turn_finished - collaboration_finished) * 1000,
                total_ms=(cognitive_turn_finished - started) * 1000,
            )
            trace = CognitivePipelineTrace(
                schema_version=request.schema_version,
                pipeline_trace_id=self.trace_id_factory(),
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                input_message_ids=tuple(message.message_id for message in request.messages),
                focus_message_id=request.focus_message_id,
                runtime_snapshot_id=request.runtime.snapshot_id,
                processing_path=request.processing_path,
                perception=perception,
                interpretation=interpretation,
                goal_snapshot=goal_snapshot,
                cognitive_turn=emitted_turn,
                action_selection=emitted_turn.action_selection,
                versions=versions,
                timings=timings,
                requirement_resolution=requirement,
                collaboration=collaboration,
                orchestration=two_call.trace() if two_call is not None else None,
            )
            self._validate_trace_identity(trace, perception, interpretation, emitted_turn)
            return trace
        except Exception as exc:
            self._raise(
                turn_id,
                "trace_validation",
                "The completed cognitive pipeline failed invariant validation.",
                exc,
            )

    # -- conversational workspace and collaboration (runner x.y.3) ------------------------
    @staticmethod
    def _workspace_inputs(request: CognitivePipelineRequest) -> WorkspaceTurnInputs:
        """The request's workspace facts, or an empty observational workspace."""

        if request.workspace_inputs is not None:
            return request.workspace_inputs
        return WorkspaceTurnInputs(
            authority_mode=WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW,
            previous_snapshot=empty_workspace(request.conversation_ref, WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW),
        )

    @staticmethod
    def _open_inquiries(request, inputs: WorkspaceTurnInputs) -> tuple[InterpretationOpenInquiry, ...]:
        """Inquiries open at the turn boundary (after delivered outcomes reconcile) -- the parser's only answer targets."""

        previous = inputs.previous_snapshot or empty_workspace(request.conversation_ref, inputs.authority_mode)
        reconciled = reconcile(previous, record=inputs.predecessor_record, outcomes=inputs.delivered_outcomes,
                               turn_ref=request.cognitive_turn_id)
        return tuple(
            InterpretationOpenInquiry(inquiry_ref=item.inquiry_id, purpose=item.purpose.value,
                                      asked_about=item.asked_about, answer_options=item.answer_options)
            for item in reconciled.inquiries if item.status == OpenInquiryStatus.OPEN
        )

    def _collaborate(self, request, interpretation, goal_snapshot, workspace, requirement) -> PipelineCollaboration:
        """Content production for every ready deliverable goal, then its projection into Action inputs."""

        snapshot = requirement.handoff.snapshot
        readiness = {item.goal_ref: item.readiness for item in snapshot.goal_readiness}
        frames = {frame.frame_id: frame for frame in interpretation.semantic_frames}
        answers = {item.goal_ref: item for item in workspace.answer_bindings if item.goal_ref}
        previous_inquiries = {item.inquiry_id: item for item in workspace.snapshot.inquiries}
        goals = []
        for target in workspace.targets:
            record = next(item for item in goal_snapshot.records if item.goal_id == target.goal_ref)
            if record.definition_ref.definition_key not in WORKSPACE_GOALS:
                continue  # pragma: no cover - the workspace targets only these goals
            answer = answers.get(target.goal_ref)
            goals.append(CollaborationGoalInput(
                goal_ref=target.goal_ref, frame=frames[target.frame_ref], target=target,
                blocked=readiness.get(target.goal_ref) != "ready", answer=answer,
                inquiry=previous_inquiries.get(answer.inquiry_ref) if answer is not None else None,
            ))
        outcomes = self.collaboration_engine.produce(
            cognitive_turn_id=request.cognitive_turn_id, conversation_ref=request.conversation_ref,
            goals=tuple(goals), workspace=workspace, interpretation=interpretation,
            dialogue_context=request.dialogue_context.interpretation_context if request.dialogue_context else None,
            messages=request.messages, personality=request.action_personality,
        )
        projection = self.collaboration_projector.project(
            outcomes, interpretation=interpretation, goal_snapshot=goal_snapshot, workspace=workspace,
            runtime=request.runtime.to_action_runtime(), requirement_handoff=requirement.handoff,
            existing_objects=(*request.action_grounded_objects, *requirement.grounded_objects),
        )
        return PipelineCollaboration(
            versions=CollaborationVersions(), workspace=workspace, outcomes=outcomes,
            turn_record=projection.turn_record, handoff=projection.handoff,
            grounded_objects=projection.grounded_objects, realization_content=projection.realization_content,
        )

    def _resolve_requirements(self, request, interpretation, goal_snapshot, workspace=None) -> PipelineRequirementResolution:
        """Typed readiness of this turn's request goals, projected into Action inputs.

        Reads only the typed Interpretation and Goal outputs, the one trusted
        runtime snapshot, the authorized dialogue context, and the captured
        turn-boundary inputs.  Never text, never a model.
        """

        runtime = request.runtime.to_action_runtime()
        dialogue = request.dialogue_context
        resolution_request = RequirementResolutionRequest(
            cognitive_turn_id=request.cognitive_turn_id, conversation_ref=request.conversation_ref,
            interpretation=interpretation, goal_snapshot=goal_snapshot, runtime=runtime,
            dialogue_context=dialogue.interpretation_context if dialogue is not None else None,
            dialogue_action_refs=dialogue.action_object_refs if dialogue is not None else (),
            inputs=request.requirement_inputs, workspace=workspace,
        )
        outcome = self.requirement_engine.resolve(resolution_request)
        projection = self.requirement_projector.project(
            outcome, interpretation=interpretation, goal_snapshot=goal_snapshot, runtime=runtime,
            existing_objects=request.action_grounded_objects,
            existing_descriptors=request.action_target_descriptors,
        )
        snapshot = outcome.snapshot
        return PipelineRequirementResolution(
            versions=RequirementResolutionVersions(producer_manifest=snapshot.producer_manifest_version),
            handoff=projection.handoff,
            content_records=tuple(
                PipelineRequirementContent(resolution_ref=item.resolution_ref, query=item.query,
                                           result=item.result, gap=item.gap)
                for item in outcome.content_records
            ),
            dialogue_refs=resolution_request.dialogue_refs(),
            grounded_objects=projection.grounded_objects,
            target_descriptors=projection.target_descriptors,
            realization_content=projection.realization_content,
        )

    @staticmethod
    def _validate_perception(
        event: PerceptionEvent,
        request: CognitivePipelineRequest,
        perception_request: PerceptionRequest,
    ) -> None:
        expected = (
            request.cognitive_turn_id,
            tuple(message.message_id for message in request.messages),
            request.focus_message_id,
            request.runtime.snapshot_id,
            request.context_truncated,
            request.cue_manifest_version,
        )
        actual = (
            event.cognitive_turn_id,
            event.input_message_ids,
            event.focus_message_id,
            event.runtime_snapshot_id,
            event.context_truncated,
            event.versions.cue_manifest,
        )
        if actual != expected:
            raise ValueError("perception output ownership differs from the pipeline request")
        observations = {
            observation.observation_id: observation
            for observation in event.observations
        }
        for observation in event.observations:
            for evidence in observation.evidence:
                validate_evidence(evidence, perception_request)
        for activation in event.cue_activations:
            for evidence in activation.evidence:
                validate_evidence(evidence, perception_request)
            referenced_evidence = tuple(
                evidence
                for observation_ref in activation.observation_refs
                for evidence in observations[observation_ref].evidence
            )
            if activation.evidence != referenced_evidence:
                raise ValueError(
                    "activation evidence differs from its referenced observations"
                )

    @staticmethod
    def _validate_interpretation_projection(request, pipeline_request, handoff) -> None:
        if request.perception is not handoff:
            raise ValueError("exact perception handoff was not preserved")
        dialogue = pipeline_request.dialogue_context
        expected = dialogue.interpretation_context if dialogue is not None else None
        if request.dialogue_context is not expected:
            raise ValueError("the exact dialogue context was not handed to interpretation")
        if any(
            supplied is not authoritative
            for supplied, authoritative in zip(request.memories, pipeline_request.memories)
        ) or len(request.memories) != len(pipeline_request.memories):
            raise ValueError("authoritative memory instances or ordering changed")

    @staticmethod
    def _validate_interpretation(envelope, request, perception, handoff) -> None:
        expected = (
            request.cognitive_turn_id,
            request.conversation_ref,
            perception.perception_event_id,
            tuple(message.message_id for message in request.messages),
            request.focus_message_id,
            request.runtime.snapshot_id,
            request.context_truncated,
        )
        actual = (
            envelope.cognitive_turn_id,
            envelope.conversation_ref,
            envelope.perception_event_id,
            envelope.input_message_ids,
            envelope.focus_message_id,
            envelope.runtime_snapshot_id,
            envelope.context_truncated,
        )
        if actual != expected:
            raise ValueError("interpretation ownership differs from the pipeline request")
        versions = envelope.versions
        if (
            versions.perception_schema != handoff.versions.schema_id
            or versions.perception_cue_manifest != handoff.versions.cue_manifest
            or versions.perception_detectors != handoff.versions.deterministic_detectors
        ):
            raise ValueError("interpretation did not retain emitted perception versions")

    @staticmethod
    def _validate_appraisal_projections(turn_request, pipeline_request) -> None:
        runtime = pipeline_request.runtime
        projected = turn_request.runtime
        if (
            projected.capability_snapshot.snapshot_id != runtime.capability_snapshot_id
            or projected.capability_snapshot.available_actions != runtime.available_capabilities
            or projected.available_actions != runtime.available_capabilities
        ):
            raise ValueError("appraisal capability projection diverged")
        expected_tool = (
            runtime.last_tool_event.model_dump(mode="python")
            if runtime.last_tool_event is not None else {}
        )
        if dict(projected.last_tool_event) != expected_tool:
            raise ValueError("appraisal tool-event projection diverged")
        memories = turn_request.memories
        for source, target in zip(pipeline_request.memories, memories):
            if target.model_dump(mode="python") != {
                "memory_id": source.memory_id,
                "memory_type": source.memory_type,
                "content": source.content,
                "provenance": source.provenance,
                "status": source.status,
            }:
                raise ValueError("appraisal memory projection diverged")
        if len(memories) != len(pipeline_request.memories):
            raise ValueError("appraisal memory projection changed cardinality")

    @staticmethod
    def _validate_action_projection(turn_request, pipeline_request, requirement, collaboration=None, *,
                                    understanding=(), understanding_descriptors=()) -> None:
        """The action runtime is an exact projection of the one trusted runtime snapshot.

        Authorized action inputs supplied with the request are handed over
        unchanged and in order; the requirement projection is appended after
        them, exactly as recorded.
        """

        projected = turn_request.action_runtime
        if projected != pipeline_request.runtime.to_action_runtime():
            raise ValueError("action runtime projection diverged")
        runtime = pipeline_request.runtime
        if (
            projected.runtime_snapshot_ref != runtime.snapshot_id
            or projected.capability_snapshot_ref != runtime.capability_snapshot_id
            or projected.available_capabilities != runtime.available_capabilities
            or projected.authorization != runtime.authorization
            or projected.consent != runtime.consent
            or projected.safety_decision != runtime.safety_decision
            or projected.response_obligation != runtime.response_obligation
        ):
            raise ValueError("action runtime projection changed a trusted fact")
        for name in ("prior_action_records", "prior_realization_records", "action_observed_outcomes"):
            supplied, handed = getattr(pipeline_request, name), getattr(turn_request, name)
            if len(supplied) != len(handed) or any(a is not b for a, b in zip(supplied, handed)):
                raise ValueError("authorized action inputs were not preserved")
        for name, appended in (
            ("action_grounded_objects", (*requirement.grounded_objects, *understanding,
                                         *(collaboration.grounded_objects if collaboration is not None else ()))),
            ("action_target_descriptors", (*requirement.target_descriptors, *understanding_descriptors)),
        ):
            supplied, handed = getattr(pipeline_request, name), getattr(turn_request, name)
            expected = (*supplied, *appended)
            if len(expected) != len(handed) or any(a is not b for a, b in zip(expected, handed)):
                raise ValueError("authorized action inputs or the requirement projection were not preserved")
        if turn_request.requirement_handoff is not requirement.handoff:
            raise ValueError("the requirement handoff was not preserved")
        if collaboration is not None and turn_request.collaboration_handoff is not collaboration.handoff:
            raise ValueError("the collaboration handoff was not preserved")

    @staticmethod
    def _validate_turn(trace, request, interpretation) -> None:
        if trace.input_snapshot is not request:
            raise ValueError("turn trace did not retain its exact request")
        if trace.input_snapshot.interpretation is not interpretation:
            raise ValueError("turn trace did not retain exact interpretation")
        if trace.versions.interpretation_versions is not interpretation.versions:
            raise ValueError("turn trace did not retain exact interpretation versions")
        if (
            trace.cognitive_turn_id != request.cognitive_turn_id
            or trace.conversation_ref != request.conversation_ref
            or trace.input_message_ids != request.input_message_ids
            or trace.focus_message_id != request.focus_message_id
            or trace.appraisal.cognitive_turn_id != request.cognitive_turn_id
            or trace.dynamic_state_update.snapshot.conversation_ref != request.conversation_ref
        ):
            raise ValueError("cognitive-turn output ownership differs from its request")

    @staticmethod
    def _validate_trace_identity(trace, perception, interpretation, turn) -> None:
        if (
            trace.perception is not perception
            or trace.interpretation is not interpretation
            or trace.cognitive_turn is not turn
            or trace.versions.perception is not perception.versions
            or trace.versions.interpretation is not interpretation.versions
            or trace.versions.cognitive_turn is not turn.versions
            or trace.action_selection is not turn.action_selection
            or trace.versions.action is not turn.action_selection.versions
        ):
            raise ValueError("pipeline trace reconstructed an authoritative stage output")

    def _clock_value(self, cognitive_turn_id: str) -> float:
        try:
            value = self.clock()
            if not math.isfinite(value):
                raise ValueError("pipeline clock returned a non-finite value")
            return value
        except Exception as exc:
            self._raise(
                cognitive_turn_id,
                "trace_validation",
                "Cognitive-pipeline diagnostic timing failed.",
                exc,
            )

    @staticmethod
    def _raise(
        cognitive_turn_id: str,
        stage: PipelineFailureStage,
        safe_message: str,
        cause: Exception,
        *,
        nested_stage: str | None = None,
        nested_phase: str | None = None,
    ) -> NoReturn:
        raise CognitivePipelineExecutionError(
            cognitive_turn_id=cognitive_turn_id,
            failed_stage=stage,
            nested_stage=nested_stage,
            nested_phase=nested_phase,
            safe_message=safe_message,
        ) from cause
