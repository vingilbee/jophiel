"""Action Selection inside the canonical Cognitive Pipeline V0."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionEngine, ActionSelectionError
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import (
    PIPELINE_SCHEMA_VERSION, CognitivePipelineExecutionError, CognitivePipelineRelationshipContext,
    CognitivePipelineRequest, CognitivePipelineTrace, CognitivePipelineV0,
)
from app.ai.Cognitive.system.v0.cognitive_turn import CognitiveTurnRunner
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionFailurePhase, ActionGroundedObject, ActionSelectionRequest, ActionTargetType,
)
from app.ai.Cognitive.system.v0.interpretation_v0.models import TaskContract
from app.ai.Cognitive.system.v0.perception_v0.models import ToolEventFact
from cognitive_action_support import safety_decision
from test_cognitive_pipeline_v0_contract import _request

TURN_CLOCK = (0.0, 0.001, 0.003, 0.004)
PIPELINE_CLOCK = (0.0, 0.001, 0.003, 0.004, 0.0045, 0.005, 0.0055, 0.007)


class RecordingActionEngine:
    def __init__(self) -> None:
        self.requests: list[ActionSelectionRequest] = []
        self.delegate = ActionSelectionEngine()

    def select(self, request):
        self.requests.append(request)
        return self.delegate.select(request)


def _pipeline(action_engine=None) -> CognitivePipelineV0:
    return CognitivePipelineV0(
        cognitive_turn_runner=CognitiveTurnRunner(action_engine=action_engine, id_factory=lambda: "turn-trace",
                                                  clock=iter(TURN_CLOCK).__next__),
        trace_id_factory=lambda: "pipeline-trace", clock=iter(PIPELINE_CLOCK).__next__,
    )


def test_pipeline_shares_the_exact_action_result_of_the_cognitive_turn() -> None:
    engine = RecordingActionEngine()
    trace = _pipeline(engine).run(_request("action-question-missing-recipient"))
    assert trace.action_selection is trace.cognitive_turn.action_selection
    assert trace.versions.action is trace.cognitive_turn.action_selection.versions
    assert trace.versions.action == trace.cognitive_turn.versions.action_versions
    assert trace.action_selection.plan.moves
    handed = engine.requests[0]
    assert handed.interpretation is trace.interpretation
    assert handed.goal_snapshot is trace.goal_snapshot
    assert handed.dynamic_state is trace.cognitive_turn.dynamic_state_update.snapshot
    assert handed.appraisal is trace.cognitive_turn.appraisal
    assert all(a is b for a, b in zip(handed.active_goals, trace.cognitive_turn.input_snapshot.active_goals))
    assert trace.versions.pipeline_schema == PIPELINE_SCHEMA_VERSION == "cognitive-pipeline-schema-v0.2"


def test_trusted_runtime_projection_is_exact() -> None:
    request = _request("indirect-window-request-with-attachment")
    runtime = request.runtime.model_copy(update={
        "policy_records": ("policy:privacy",), "consent": "granted", "external_export_control": False,
        "deadline_band": "high", "task_ref": "task:window", "authorization": "authorized",
        "task_contract": TaskContract(task_type="window", required_parameters=("room",), provided_parameters={"room": "kitchen"}),
        "last_tool_event": ToolEventFact(tool="window", status="failed", trusted_runtime=True, authorized=True, event_reference="event-1"),
    })
    projected = runtime.to_action_runtime()
    assert projected.runtime_snapshot_ref == runtime.snapshot_id
    assert projected.capability_snapshot_ref == runtime.capability_snapshot_id
    assert projected.available_capabilities == runtime.available_capabilities
    assert (projected.authorization, projected.consent, projected.safety_decision) == (runtime.authorization, runtime.consent, runtime.safety_decision)
    assert (projected.external_export_control, projected.policy_records, projected.task_ref, projected.deadline_band) == (False, ("policy:privacy",), "task:window", "high")
    assert (projected.required_task_parameters, dict(projected.provided_task_parameters)) == (("room",), {"room": "kitchen"})
    assert (projected.last_tool_event_ref, projected.last_tool_event_status) == ("event-1", "failed")
    assert (projected.response_obligation, projected.response_obligation_ref) == (runtime.response_obligation, runtime.response_obligation_ref)
    engine = RecordingActionEngine()
    _pipeline(engine).run(request)
    assert engine.requests[0].runtime == request.runtime.to_action_runtime()


def test_authorized_action_inputs_reach_the_action_stage_unchanged() -> None:
    request = _request("action-question-missing-recipient")
    grounded = ActionGroundedObject(
        object_id="topic-report", object_type=ActionTargetType.TOPIC, cognitive_turn_id=request.cognitive_turn_id,
        source_refs=(request.runtime.snapshot_id,), topic_status="inactive",
    )
    request = CognitivePipelineRequest.model_validate({**request.model_dump(mode="python"), "action_grounded_objects": (grounded,)})
    engine = RecordingActionEngine()
    _pipeline(engine).run(request)
    assert engine.requests[0].grounded_objects[0] is request.action_grounded_objects[0]
    assert engine.requests[0].personality is request.action_personality


def test_relationship_context_has_no_action_policy_path() -> None:
    request = CognitivePipelineRequest.model_validate({
        **_request("short-acknowledgement").model_dump(mode="python"),
        "relationship": CognitivePipelineRelationshipContext(relationship_ref="relationship-1", status="close", provenance="review"),
    })
    engine = RecordingActionEngine()
    trace = _pipeline(engine).run(request)
    handed = engine.requests[0]
    assert not any("relationship" in name for name in type(handed).model_fields)
    # Upstream stages may cite the relationship record; the action index and
    # every feature, gate, and candidate must not.
    index = ActionReferenceIndex(handed)
    assert all(entry.ref != "relationship-1" and "relationship-1" not in entry.about_refs for entry in index.entries)
    decision = trace.action_selection.model_dump_json()
    assert "relationship-1" not in decision


def test_pipeline_timings_include_action_time_once() -> None:
    trace = _pipeline().run(_request())
    turn = trace.cognitive_turn.timings
    assert turn.total_ms == pytest.approx(turn.appraisal_ms + turn.dynamic_state_ms + turn.action_selection_ms)
    pipeline = trace.timings
    assert pipeline.total_ms == pytest.approx(
        pipeline.perception_ms + pipeline.interpretation_ms + pipeline.goal_update_ms
        + pipeline.workspace_resolution_ms + pipeline.requirement_resolution_ms
        + pipeline.collaborative_content_ms + pipeline.cognitive_turn_ms
    )
    assert pipeline.total_ms == pytest.approx(7.0)


def test_trace_serialization_preserves_the_action_decision_and_rejects_divergence() -> None:
    trace = _pipeline().run(_request("action-question-missing-recipient"))
    restored = CognitivePipelineTrace.model_validate_json(trace.model_dump_json())
    assert restored == trace and restored.action_selection == restored.cognitive_turn.action_selection
    payload = trace.model_dump(mode="python")
    divergent = trace.action_selection.model_copy(update={"result_id": "another-result"})
    with pytest.raises(ValidationError):
        CognitivePipelineTrace.model_validate({**payload, "action_selection": divergent})


class FailingActionEngine:
    def select(self, request):
        try:
            raise RuntimeError("private detail")
        except RuntimeError as exc:
            raise ActionSelectionError(cognitive_turn_id=request.cognitive_turn_id, failed_phase=ActionFailurePhase.HARD_GATE, code="injected") from exc


def test_action_failure_prevents_a_completed_pipeline_and_preserves_nested_phase() -> None:
    with pytest.raises(CognitivePipelineExecutionError) as caught:
        _pipeline(FailingActionEngine()).run(_request())
    error = caught.value
    assert (error.failed_stage, error.nested_stage, error.nested_phase) == ("cognitive_turn", "action_selection", "hard_gate")
    assert "private" not in error.safe_message and error.__cause__ is not None


def test_no_viable_plan_fails_the_response_pipeline() -> None:
    request = _request()
    request = CognitivePipelineRequest.model_validate({
        **request.model_dump(mode="python"), "runtime": request.runtime.model_copy(update={"safety_decision": safety_decision(request.runtime.snapshot_id, "block_response")}),
    })
    with pytest.raises(CognitivePipelineExecutionError) as caught:
        _pipeline().run(request)
    assert (caught.value.nested_stage, caught.value.nested_phase) == ("action_selection", "plan_validation")


def test_pipeline_request_requires_the_explicit_action_personality() -> None:
    payload = _request().model_dump(mode="python")
    missing = dict(payload); missing.pop("action_personality")
    with pytest.raises(ValidationError):
        CognitivePipelineRequest.model_validate(missing)
    with pytest.raises(ValidationError):
        CognitivePipelineRequest.model_validate({**payload, "schema_version": 1})
    request = _request()
    with pytest.raises(ValidationError):
        CognitivePipelineRequest.model_validate({**payload, "action_personality": request.action_personality.model_copy(update={"source_core_memory_ref": "other-core"})})
