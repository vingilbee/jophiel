"""Fail-closed provenance checks for the connected cognitive turn."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.appraisal.models import (
    ActiveGoal,
    CapabilitySnapshot,
    RuntimeContext,
)
from app.ai.Cognitive.system.cognitive_turn import (
    CognitiveTurnRequest,
    CognitiveTurnRunner,
    CognitiveTurnTrace,
)
from app.ai.Cognitive.system.dynamic_state import (
    DynamicStateSnapshotFactory,
    PersonalityProfile,
)
from app.ai.Cognitive.system.interpretation_v0.engine import InterpretationEngine
from app.ai.Cognitive.system.interpretation_v0.fixture_validator import _runtime_case


FIXTURE = (
    Path(__file__).parents[1]
    / "app/ai/Cognitive/system/data/interpretation_design_cases.json"
)


def _request() -> CognitiveTurnRequest:
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][0]
    _, interpretation_request = _runtime_case(case)
    interpretation = InterpretationEngine().interpret(interpretation_request)
    personality = PersonalityProfile(
        profile_id="profile-v0",
        mapper_version="personality-runtime-v0.1",
    )
    previous = DynamicStateSnapshotFactory().create_initial(
        conversation_ref=interpretation.conversation_ref,
        cognitive_turn_id="turn-before",
        personality=personality,
    )
    return CognitiveTurnRequest(
        cognitive_turn_id=interpretation.cognitive_turn_id,
        conversation_ref=interpretation.conversation_ref,
        input_message_ids=interpretation.input_message_ids,
        focus_message_id=interpretation.focus_message_id,
        interpretation=interpretation,
        active_goals=(ActiveGoal(
            goal_id="goal-main",
            owner="columbina",
            goal_type="conversation",
            description="Answer the bounded user request",
            desired_state={"answered": True},
            priority_band="high",
        ),),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(snapshot_id="capability-main")
        ),
        previous_state=previous,
        personality=personality,
        core_memory_ref="core-v1",
        memory_retrieval_policy_version="memory-v1",
    )


def _trace() -> CognitiveTurnTrace:
    return CognitiveTurnRunner(
        id_factory=lambda: "trace-main",
        clock=iter((0.0, 0.001, 0.003)).__next__,
    ).evaluate(_request())


def test_request_rejects_state_from_another_conversation() -> None:
    request = _request()
    foreign = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="conversation-foreign",
        cognitive_turn_id="turn-foreign",
        personality=request.personality,
    )
    payload = request.model_dump(mode="python")
    payload["previous_state"] = foreign

    with pytest.raises(ValidationError, match="different conversation"):
        CognitiveTurnRequest.model_validate(payload)


@pytest.mark.parametrize("field", ("cognitive_turn_id", "conversation_ref", "input_message_ids"))
def test_request_binds_interpretation_to_the_bounded_input(field: str) -> None:
    request = _request()
    payload = request.model_dump(mode="python")
    payload["interpretation"][field] = (
        ("message-foreign",) if field == "input_message_ids" else "foreign"
    )

    with pytest.raises(ValidationError):
        CognitiveTurnRequest.model_validate(payload)


@pytest.mark.parametrize("mutation", ("scenario", "events", "mapper", "outcome", "branch"))
def test_trace_rejects_unresolved_cross_stage_references(mutation: str) -> None:
    trace = _trace()
    payload = trace.model_dump(mode="python")
    if mutation == "scenario":
        payload["appraisal"]["frames"][0]["scenario_id"] = "scenario-foreign"
    elif mutation == "events":
        payload["appraisal"]["frames"][0]["subject"]["event_refs"] = ("event-foreign",)
    elif mutation == "mapper":
        payload["dynamic_state_update"]["snapshot"]["personality_mapper_version"] = "mapper-foreign"
    elif mutation == "outcome":
        payload["dynamic_state_update"]["snapshot"]["source_outcome_ids"] = ("outcome-foreign",)
    else:
        payload["dynamic_state_update"]["snapshot"]["values"][0]["status"] = "provisional"
        payload["dynamic_state_update"]["snapshot"]["values"][0]["score"] = None
        payload["dynamic_state_update"]["snapshot"]["values"][0]["band"] = None
        payload["dynamic_state_update"]["snapshot"]["values"][0]["possible_score_range"] = (-1, 1)
        payload["dynamic_state_update"]["snapshot"]["values"][0]["branch_refs"] = (
            "branch-foreign-a",
            "branch-foreign-b",
        )

    with pytest.raises(ValidationError):
        CognitiveTurnTrace.model_validate(payload)


def test_trace_rejects_unknown_aggregate_frame() -> None:
    trace = _trace()
    payload = trace.model_dump(mode="python")
    payload["appraisal"]["aggregate"]["dominant_frame_ids"] = ("frame-foreign",)

    with pytest.raises(ValidationError, match="unknown dominant frame"):
        CognitiveTurnTrace.model_validate(payload)
