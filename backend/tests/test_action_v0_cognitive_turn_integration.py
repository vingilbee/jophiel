"""Action Selection as the final stage of the connected Cognitive Turn."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionEngine, ActionSelectionError, fixture_adapter
from app.ai.Cognitive.system.v0.appraisal.models import CapabilitySnapshot, RelationshipSnapshot, RuntimeContext
from app.ai.Cognitive.system.v0.cognitive_turn import (
    CognitiveTurnExecutionError, CognitiveTurnRequest, CognitiveTurnRunner, CognitiveTurnTrace, TURN_SCHEMA_VERSION,
)
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionFailurePhase, ActionSelectionRequest, ActionSelectionResult, ActionVersions,
)
from cognitive_action_support import safety_decision
from test_cognitive_turn import InterpretationScenario, SemanticFrame, UnknownInput, _request

CLOCK = (0.0, 0.001, 0.003, 0.004)


class RecordingActionEngine:
    def __init__(self) -> None:
        self.requests: list[ActionSelectionRequest] = []
        self.delegate = ActionSelectionEngine()

    def select(self, request: ActionSelectionRequest) -> ActionSelectionResult:
        self.requests.append(request)
        return self.delegate.select(request)


def _runner(engine=None, clock=CLOCK) -> CognitiveTurnRunner:
    return CognitiveTurnRunner(action_engine=engine, id_factory=lambda: "trace-action", clock=iter(clock).__next__)


def _clarification_request() -> CognitiveTurnRequest:
    frame = SemanticFrame(frame_id="send", predicate="send_report", arguments={"recipient": None}, unresolved_arguments=("recipient",))
    unknown = UnknownInput(unknown_id="unknown-recipient", key="recipient", evidence_refs=("evidence:send",))
    return _request(
        frames=(frame,), unknowns=(unknown,),
        scenarios=(InterpretationScenario(scenario_id="send-scenario", support="strong", disposition="leading",
                                          semantic_frame_refs=("send",), unknown_refs=("unknown-recipient",)),),
    )


def test_current_dynamic_state_and_exact_upstream_objects_reach_action_selection() -> None:
    request = _request()
    engine = RecordingActionEngine()
    trace = _runner(engine).evaluate(request)
    handed = engine.requests[0]
    assert handed.dynamic_state is trace.dynamic_state_update.snapshot
    assert handed.dynamic_state is not request.previous_state
    assert handed.dynamic_state.supersedes_snapshot_id == request.previous_state.state_snapshot_id
    assert handed.appraisal is trace.appraisal
    assert handed.interpretation is request.interpretation
    assert handed.goal_snapshot is request.goal_snapshot
    assert all(a is b for a, b in zip(handed.active_goals, request.active_goals)) and len(handed.active_goals) == len(request.active_goals)
    assert handed.runtime is request.action_runtime and handed.personality is request.action_personality
    assert trace.action_selection.request_digest
    assert trace.action_selection.plan.moves


def test_real_connected_turn_produces_a_non_empty_shadow_plan() -> None:
    trace = _runner().evaluate(_clarification_request())
    plan = trace.action_selection.plan
    assert plan.moves and plan.termination.value == "stop" and plan.response_obligation == "required"
    assert trace.action_selection.shadow_only is True
    assert trace.status == "completed"


def test_versions_and_timings_include_the_action_stage_without_double_counting() -> None:
    trace = _runner().evaluate(_request())
    assert trace.versions.turn_schema == TURN_SCHEMA_VERSION == "cognitive-turn-schema-v2"
    assert trace.versions.action_versions == ActionVersions() == trace.action_selection.versions
    timings = trace.timings
    assert (timings.appraisal_ms, timings.dynamic_state_ms, timings.action_selection_ms, timings.total_ms) == pytest.approx((1.0, 2.0, 1.0, 4.0))
    with pytest.raises(ValidationError):
        type(timings).model_validate({**timings.model_dump(), "total_ms": 3.0})


def test_trace_serialization_preserves_the_complete_action_decision() -> None:
    trace = _runner().evaluate(_clarification_request())
    restored = CognitiveTurnTrace.model_validate_json(trace.model_dump_json())
    assert restored == trace and restored.action_selection == trace.action_selection
    assert restored.action_selection.steps == trace.action_selection.steps


def test_relationship_input_has_no_action_policy_path() -> None:
    assert "relationship" not in ActionSelectionRequest.model_fields
    assert all("relationship" not in name for name in ActionSelectionRequest.model_fields)
    base = _request()
    related = CognitiveTurnRequest.model_validate({
        **base.model_dump(mode="python"),
        "relationship": RelationshipSnapshot(relationship_snapshot_id="relationship-1", status="playful", evidence="jokes welcome"),
    })
    engine = RecordingActionEngine()
    trace = _runner(engine).evaluate(related)
    handed = engine.requests[0]
    assert not any(value is related.relationship for value in handed.__dict__.values())
    plain = _runner().evaluate(base)
    assert fixture_adapter.semantic_projection(trace.action_selection)["plan"] == fixture_adapter.semantic_projection(plain.action_selection)["plan"]


class FailingActionEngine:
    def __init__(self, phase: ActionFailurePhase) -> None:
        self.phase = phase

    def select(self, request):
        try:
            raise RuntimeError("private message text")
        except RuntimeError as exc:
            raise ActionSelectionError(cognitive_turn_id=request.cognitive_turn_id, failed_phase=self.phase, code="injected") from exc


@pytest.mark.parametrize("phase", (ActionFailurePhase.CANDIDATE_CONSTRUCTION, ActionFailurePhase.PLAN_VALIDATION))
def test_action_failure_prevents_a_completed_turn(phase: ActionFailurePhase) -> None:
    with pytest.raises(CognitiveTurnExecutionError) as caught:
        _runner(FailingActionEngine(phase)).evaluate(_request())
    error = caught.value
    assert error.failed_stage == "action_selection" and error.action_failure_phase == phase.value
    assert isinstance(error.__cause__, ActionSelectionError) and error.__cause__.__cause__ is not None
    assert "private" not in error.safe_message


def test_foreign_or_empty_action_results_cannot_complete_the_turn() -> None:
    class ForeignTurn:
        def select(self, request):
            return ActionSelectionEngine().select(request).model_copy(update={"cognitive_turn_id": "foreign-turn"})

    with pytest.raises(CognitiveTurnExecutionError) as caught:
        _runner(ForeignTurn()).evaluate(_request())
    assert caught.value.failed_stage == "action_selection"

    class NotRequired:
        def select(self, request):
            request = request.model_copy(update={"runtime": request.runtime.model_copy(update={
                "response_obligation": "not_required", "response_obligation_ref": None,
                "safety_decision": safety_decision(request.runtime.runtime_snapshot_ref, "block_response")})})
            return ActionSelectionEngine().select(request)

    with pytest.raises(CognitiveTurnExecutionError) as caught:
        _runner(NotRequired()).evaluate(_request())
    assert caught.value.failed_stage == "trace_validation"


def test_turn_request_requires_explicit_action_inputs_and_trusted_lineage() -> None:
    request = _request()
    payload = request.model_dump(mode="python")
    for field in ("action_runtime", "action_personality"):
        broken = dict(payload); broken.pop(field)
        with pytest.raises(ValidationError):
            CognitiveTurnRequest.model_validate(broken)
    runtime = request.action_runtime
    for update in (
        {"action_runtime": runtime.model_copy(update={"runtime_snapshot_ref": "other-runtime"})},
        {"action_runtime": runtime.model_copy(update={"available_capabilities": ("send_email",)})},
        {"action_runtime": runtime.model_copy(update={"authorization": "granted"})},
        {"action_personality": request.action_personality.model_copy(update={"source_core_memory_ref": "other-core"})},
        {"schema_version": 1},
    ):
        with pytest.raises(ValidationError):
            CognitiveTurnRequest.model_validate({**payload, **update})
    capability = RuntimeContext(capability_snapshot=CapabilitySnapshot(snapshot_id="capability-1", available_actions=("x",)), available_actions=("x",))
    with pytest.raises(ValidationError):
        CognitiveTurnRequest.model_validate({**payload, "runtime": capability})
