"""Thin orchestration for the deterministic shadow cognitive-turn spine."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import NoReturn
from uuid import uuid4

from ..action_selection_v0 import ActionSelectionEngine, ActionSelectionError
from ..appraisal.engine import AppraisalEngine
from ..appraisal.models import AppraisalRequest
from ..contracts.actions import ActionSelectionRequest, ActionSelectionResult
from ..dynamic_state.models import DynamicStateContext, UpdateRequest
from ..dynamic_state.updater import DynamicStateUpdater
from .errors import CognitiveTurnExecutionError, FailureStage
from .models import (
    CognitiveTurnRequest,
    CognitiveTurnTimings,
    CognitiveTurnTrace,
    CognitiveTurnVersions,
    ProcessingPath,
    TURN_SCHEMA_VERSION,
)


class CognitiveTurnRunner:
    def __init__(
        self,
        appraisal_engine: AppraisalEngine | None = None,
        dynamic_state_updater: DynamicStateUpdater | None = None,
        action_engine: ActionSelectionEngine | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.appraisal_engine = appraisal_engine or AppraisalEngine()
        self.dynamic_state_updater = dynamic_state_updater or DynamicStateUpdater()
        self.action_engine = action_engine or ActionSelectionEngine()
        self.id_factory = id_factory or (lambda: str(uuid4()))
        self.clock = clock or time.monotonic

    def evaluate(self, request: CognitiveTurnRequest, *, prepare_action=None, validate_action=None) -> CognitiveTurnTrace:
        if request.processing_path != ProcessingPath.FAST:
            self._raise(
                request.cognitive_turn_id,
                "validation",
                "The requested cognitive processing path is not executable in V0.",
                ValueError(f"unsupported processing path: {request.processing_path}"),
            )

        started = self._clock_value(request.cognitive_turn_id)
        try:
            appraisal_request = AppraisalRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                interpretation=request.interpretation,
                active_goals=request.active_goals,
                runtime=request.runtime,
                memories=request.memories,
                values=request.values,
                relationship=request.relationship,
                supplemental_inputs=request.supplemental_inputs,
            )
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "validation",
                "The cognitive turn input failed validation.",
                exc,
            )

        try:
            appraisal_spans = ()
            if hasattr(self.appraisal_engine, "appraise_turn"):
                appraisal_result, appraisal_spans = self.appraisal_engine.appraise_turn(appraisal_request, request)
            else:
                appraisal_result = self.appraisal_engine.appraise(appraisal_request)
            if appraisal_result.cognitive_turn_id != request.cognitive_turn_id:
                raise ValueError("appraisal returned a different cognitive-turn id")
            if (
                appraisal_result.interpretation_trace_id
                != request.interpretation.interpretation_trace_id
            ):
                raise ValueError("appraisal returned a different interpretation-trace id")
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "appraisal",
                "Appraisal could not complete for this cognitive turn.",
                exc,
            )
        appraisal_finished = self._clock_value(request.cognitive_turn_id)

        try:
            dynamic_context = DynamicStateContext(
                conversation_ref=request.conversation_ref,
                semantic_frames=request.interpretation.semantic_frames,
                objects=request.grounded_objects,
                aggregate=appraisal_result.aggregate,
            )
            update_request = UpdateRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                previous_snapshot=request.previous_state,
                personality=request.personality,
                elapsed_monotonic_ms=request.elapsed_monotonic_ms,
                appraisal_frames=appraisal_result.frames,
                outcomes=request.outcomes,
                context=dynamic_context,
                control_context=request.control_context,
            )
            if any(
                handed is not produced
                for handed, produced in zip(
                    update_request.appraisal_frames, appraisal_result.frames
                )
            ) or len(update_request.appraisal_frames) != len(appraisal_result.frames):
                raise ValueError("appraisal frame identity was not preserved")
            if update_request.context is None or (
                update_request.context.aggregate is not appraisal_result.aggregate
                or any(
                    handed is not supplied
                    for handed, supplied in zip(
                        update_request.context.semantic_frames,
                        request.interpretation.semantic_frames,
                    )
                )
                or len(update_request.context.semantic_frames)
                != len(request.interpretation.semantic_frames)
            ):
                raise ValueError("semantic-frame or aggregate identity was not preserved")
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "trace_validation",
                "The dynamic-state context changed authoritative appraisal inputs.",
                exc,
            )

        try:
            update_result = self.dynamic_state_updater.update(update_request)
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "dynamic_state",
                "Dynamic-state evaluation could not complete for this cognitive turn.",
                exc,
            )
        dynamic_state_finished = self._clock_value(request.cognitive_turn_id)

        if prepare_action is not None:
            try:
                request = prepare_action(request, appraisal_result, update_result)
            except Exception as exc:
                self._raise(request.cognitive_turn_id, "action_handoff_validation",
                            "Combined content and behavior preparation failed.", exc)

        try:
            action_request = ActionSelectionRequest(
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                core_memory_ref=request.core_memory_ref,
                interpretation=request.interpretation,
                goal_snapshot=request.goal_snapshot,
                active_goals=request.active_goals,
                appraisal=appraisal_result,
                dynamic_state=update_result.snapshot,
                runtime=request.action_runtime,
                personality=request.action_personality,
                grounded_objects=request.action_grounded_objects,
                target_descriptors=request.action_target_descriptors,
                prior_action_records=request.prior_action_records,
                prior_realization_records=request.prior_realization_records,
                observed_outcomes=request.action_observed_outcomes,
                requirement_handoff=request.requirement_handoff,
                collaboration_handoff=request.collaboration_handoff,
            )
            self._validate_action_handoff(action_request, request, appraisal_result, update_result)
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "action_handoff_validation",
                "The action-selection handoff failed validation.",
                exc,
            )

        try:
            action_result = self.action_engine.select(action_request)
            if validate_action is not None:
                validate_action(action_result)
            if ActionSelectionResult.model_validate_json(action_result.model_dump_json()) != action_result:
                raise ValueError("action result did not survive JSON round-trip")
            if action_result.cognitive_turn_id != request.cognitive_turn_id:
                raise ValueError("action selection returned a different cognitive-turn id")
        except ActionSelectionError as exc:
            self._raise(
                request.cognitive_turn_id,
                "action_selection",
                "Action selection could not complete for this cognitive turn.",
                exc,
                action_failure_phase=exc.failed_phase.value,
            )
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "action_selection",
                "Action selection could not complete for this cognitive turn.",
                exc,
            )
        finished = self._clock_value(request.cognitive_turn_id)

        try:
            emitted_versions = appraisal_result.versions
            versions = CognitiveTurnVersions(
                turn_schema=TURN_SCHEMA_VERSION,
                interpretation_schema=request.interpretation.versions.schema_id,
                appraisal_schema=emitted_versions["schema"],
                appraisal_rules=emitted_versions["rules"],
                dynamic_state_policy=update_result.snapshot.policy_version,
                personality_mapper=request.personality.mapper_version,
                memory_retrieval_policy=request.memory_retrieval_policy_version,
                interpretation_versions=request.interpretation.versions,
                goal_versions=request.goal_snapshot.versions,
                action_versions=action_result.versions,
            )
            timings = CognitiveTurnTimings(
                appraisal_spans=appraisal_spans,
                appraisal_ms=(appraisal_finished - started) * 1000,
                dynamic_state_ms=(dynamic_state_finished - appraisal_finished) * 1000,
                action_selection_ms=(finished - dynamic_state_finished) * 1000,
                total_ms=(finished - started) * 1000,
            )
            return CognitiveTurnTrace(
                trace_id=self.id_factory(),
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                input_message_ids=request.input_message_ids,
                focus_message_id=request.focus_message_id,
                processing_path=request.processing_path,
                input_snapshot=request,
                appraisal=appraisal_result,
                dynamic_state_update=update_result,
                action_selection=action_result,
                versions=versions,
                timings=timings,
            )
        except Exception as exc:
            self._raise(
                request.cognitive_turn_id,
                "trace_validation",
                "The completed cognitive turn failed invariant validation.",
                exc,
            )

    @staticmethod
    def _validate_action_handoff(action_request, request, appraisal_result, update_result) -> None:
        """The action stage receives the exact objects produced or supplied this turn."""

        exact = (
            (action_request.interpretation, request.interpretation),
            (action_request.goal_snapshot, request.goal_snapshot),
            (action_request.appraisal, appraisal_result),
            (action_request.dynamic_state, update_result.snapshot),
            (action_request.runtime, request.action_runtime),
            (action_request.personality, request.action_personality),
            (action_request.requirement_handoff, request.requirement_handoff),
            (action_request.collaboration_handoff, request.collaboration_handoff),
        )
        if any(handed is not produced for handed, produced in exact):
            raise ValueError("action handoff did not preserve an authoritative stage output")
        if len(action_request.active_goals) != len(request.active_goals) or any(
            handed is not supplied for handed, supplied in zip(action_request.active_goals, request.active_goals)
        ):
            raise ValueError("action handoff did not preserve the exact focal goals")
        if action_request.dynamic_state is request.previous_state:
            raise ValueError("action handoff received the previous dynamic state")
        if action_request.dynamic_state.supersedes_snapshot_id != request.previous_state.state_snapshot_id:
            raise ValueError("action handoff state does not supersede the previous state")

    @staticmethod
    def _raise(
        cognitive_turn_id: str,
        stage: FailureStage,
        safe_message: str,
        cause: Exception,
        *,
        action_failure_phase: str | None = None,
    ) -> NoReturn:
        raise CognitiveTurnExecutionError(
            cognitive_turn_id=cognitive_turn_id,
            failed_stage=stage,
            safe_message=safe_message,
            action_failure_phase=action_failure_phase,
        ) from cause

    def _clock_value(self, cognitive_turn_id: str) -> float:
        try:
            return self.clock()
        except Exception as exc:
            self._raise(
                cognitive_turn_id,
                "trace_validation",
                "Cognitive-turn diagnostic timing failed.",
                exc,
            )
