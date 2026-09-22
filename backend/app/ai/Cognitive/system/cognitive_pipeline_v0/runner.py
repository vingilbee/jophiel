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
from .errors import CognitivePipelineExecutionError, PipelineFailureStage
from .models import (
    PIPELINE_RUNNER_VERSION,
    PIPELINE_SCHEMA_VERSION,
    CognitivePipelineRequest,
    CognitivePipelineTimings,
    CognitivePipelineTrace,
    CognitivePipelineVersions,
    project_memory,
)


class CognitivePipelineV0:
    def __init__(
        self,
        *,
        perception_engine: PerceptionEngine | None = None,
        interpretation_engine: InterpretationEngine | None = None,
        cognitive_turn_runner: CognitiveTurnRunner | None = None,
        trace_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.perception_engine = perception_engine or PerceptionEngine()
        self.interpretation_engine = interpretation_engine or InterpretationEngine()
        self.cognitive_turn_runner = cognitive_turn_runner or CognitiveTurnRunner()
        self.trace_id_factory = trace_id_factory or (lambda: str(uuid4()))
        self.clock = clock or time.monotonic

    def run(self, request: CognitivePipelineRequest) -> CognitivePipelineTrace:
        turn_id = getattr(request, "cognitive_turn_id", "unknown-turn")

        # Authorization is checked without dumping caller-controlled content.
        try:
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
            appraisal_memories = tuple(project_memory(memory) for memory in request.memories)
            turn_request = CognitiveTurnRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                input_message_ids=tuple(message.message_id for message in request.messages),
                focus_message_id=request.focus_message_id,
                processing_path=request.processing_path,
                interpretation=interpretation,
                active_goals=request.active_goals,
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
            )
            if turn_request.interpretation is not interpretation:
                raise ValueError("interpretation identity was not preserved in turn request")
            self._validate_appraisal_projections(turn_request, request)
        except Exception as exc:
            self._raise(
                turn_id,
                "turn_request",
                "The cognitive-turn request could not be constructed.",
                exc,
            )

        try:
            emitted_turn = self.cognitive_turn_runner.evaluate(turn_request)
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
            self._raise(
                turn_id,
                "cognitive_turn",
                "The cognitive turn could not complete for this pipeline.",
                exc,
                nested_stage=nested,
            )

        try:
            versions = CognitivePipelineVersions(
                pipeline_schema=PIPELINE_SCHEMA_VERSION,
                pipeline_runner=PIPELINE_RUNNER_VERSION,
                perception=perception.versions,
                interpretation=interpretation.versions,
                cognitive_turn=emitted_turn.versions,
            )
            timings = CognitivePipelineTimings(
                perception_ms=(perception_finished - started) * 1000,
                interpretation_ms=(interpretation_finished - perception_finished) * 1000,
                cognitive_turn_ms=(cognitive_turn_finished - interpretation_finished) * 1000,
                total_ms=(cognitive_turn_finished - started) * 1000,
            )
            trace = CognitivePipelineTrace(
                pipeline_trace_id=self.trace_id_factory(),
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                input_message_ids=tuple(message.message_id for message in request.messages),
                focus_message_id=request.focus_message_id,
                runtime_snapshot_id=request.runtime.snapshot_id,
                processing_path=request.processing_path,
                perception=perception,
                interpretation=interpretation,
                cognitive_turn=emitted_turn,
                versions=versions,
                timings=timings,
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
    ) -> NoReturn:
        raise CognitivePipelineExecutionError(
            cognitive_turn_id=cognitive_turn_id,
            failed_stage=stage,
            nested_stage=nested_stage,
            safe_message=safe_message,
        ) from cause
