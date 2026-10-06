"""Real Perception -> Interpretation -> Appraisal -> Dynamic State acceptance."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cognitive_action_support import action_inputs
from app.ai.Cognitive.system.v0.appraisal.engine import AppraisalEngine
from app.ai.Cognitive.system.v0.appraisal.models import CapabilitySnapshot, RuntimeContext
from app.ai.Cognitive.system.v0.contracts.goals import GoalUpdateTrigger
from app.ai.Cognitive.system.v0.goal_v0 import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.models import GoalRuntimeContext, GoalUpdateRequest
from app.ai.Cognitive.system.v0.cognitive_turn import CognitiveTurnRequest, CognitiveTurnRunner
from app.ai.Cognitive.system.v0.dynamic_state import DynamicStateSnapshotFactory, PersonalityProfile
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.fixture_validator import (
    DEFAULT_FIXTURE,
    _runtime_case,
)


@pytest.mark.parametrize("case_id", (
    "factual-information-question",
    "action-question-missing-recipient",
    "explicit-correction-tuesday",
    "decline-no-thanks",
    "short-acknowledgement",
    "neutral-weather-information",
    "informal-greeting",
    "indirect-window-request-with-attachment",
    "runtime-capability-memory-conflict",
    "unknown-out-of-scope-command",
    "consequential-delete-first-request",
))
def test_real_pipeline_preserves_ownership_identity_and_version_provenance(
    case_id: str,
) -> None:
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = next(item for item in document["cases"] if item["case_id"] == case_id)
    perception, interpretation_request = _runtime_case(case)
    interpretation = InterpretationEngine().interpret(interpretation_request)
    personality = PersonalityProfile(
        profile_id="pipeline-profile", mapper_version="personality-runtime-v0.1"
    )
    previous = DynamicStateSnapshotFactory().create_initial(
        conversation_ref=interpretation.conversation_ref,
        cognitive_turn_id="pipeline-before", personality=personality,
    )
    goal_update = GoalEngine().update(GoalUpdateRequest(
        trigger=GoalUpdateTrigger.RESPONSE_TURN,
        cognitive_turn_id=interpretation.cognitive_turn_id,
        conversation_ref=interpretation.conversation_ref,
        interpretation=interpretation,
        runtime=GoalRuntimeContext(
            cognitive_turn_id=interpretation.cognitive_turn_id,
            runtime_snapshot_ref=interpretation.runtime_snapshot_id,
            response_obligation="required",
            response_obligation_ref=f"response-obligation:{case_id}",
        ),
        previous_dynamic_state=previous,
    ))
    runtime = RuntimeContext(capability_snapshot=CapabilitySnapshot(snapshot_id="appraisal-capability-1"))
    request = CognitiveTurnRequest(
        cognitive_turn_id=interpretation.cognitive_turn_id,
        conversation_ref=interpretation.conversation_ref,
        input_message_ids=interpretation.input_message_ids,
        focus_message_id=interpretation.focus_message_id,
        interpretation=interpretation,
        goal_snapshot=goal_update.snapshot,
        active_goals=goal_update.active_goal_views,
        runtime=runtime,
        previous_state=previous, personality=personality,
        core_memory_ref="core-columbina-v1",
        memory_retrieval_policy_version="memory-retrieval-v1",
        **action_inputs(
            runtime, runtime_snapshot_ref=interpretation.runtime_snapshot_id,
            core_memory_ref="core-columbina-v1", response_obligation_ref=f"response-obligation:{case_id}",
        ),
    )
    trace = CognitiveTurnRunner(
        appraisal_engine=AppraisalEngine(), id_factory=lambda: "pipeline-trace-1",
        clock=iter((0.0, 0.001, 0.003, 0.004)).__next__,
    ).evaluate(request)

    assert request.interpretation is interpretation
    assert perception.cognitive_turn_id == trace.cognitive_turn_id
    assert perception.perception_event_id == interpretation.perception_event_id
    assert trace.focus_message_id == interpretation.focus_message_id
    assert trace.dynamic_state_update.snapshot.conversation_ref == interpretation.conversation_ref
    assert trace.versions.interpretation_versions is interpretation.versions
    assert trace.appraisal.versions["scenario_builder"] == interpretation.versions.scenario_builder
    assert set(trace.dynamic_state_update.snapshot.source_appraisal_revision_ids) <= {
        frame.frame_id for frame in trace.appraisal.frames
    }


def test_connected_pipeline_rejects_state_from_another_conversation() -> None:
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = next(
        item for item in document["cases"]
        if item["case_id"] == "factual-information-question"
    )
    _, interpretation_request = _runtime_case(case)
    interpretation = InterpretationEngine().interpret(interpretation_request)
    personality = PersonalityProfile(
        profile_id="pipeline-profile", mapper_version="personality-runtime-v0.1"
    )
    foreign_state = DynamicStateSnapshotFactory().create_initial(
        conversation_ref="foreign-conversation",
        cognitive_turn_id="foreign-before",
        personality=personality,
    )

    valid_state = DynamicStateSnapshotFactory().create_initial(
        conversation_ref=interpretation.conversation_ref,
        cognitive_turn_id="pipeline-before", personality=personality,
    )
    goal_update = GoalEngine().update(GoalUpdateRequest(
        trigger=GoalUpdateTrigger.RESPONSE_TURN,
        cognitive_turn_id=interpretation.cognitive_turn_id,
        conversation_ref=interpretation.conversation_ref,
        interpretation=interpretation,
        runtime=GoalRuntimeContext(
            cognitive_turn_id=interpretation.cognitive_turn_id,
            runtime_snapshot_ref=interpretation.runtime_snapshot_id,
            response_obligation="required",
            response_obligation_ref="response-obligation:foreign-state",
        ),
        previous_dynamic_state=valid_state,
    ))

    with pytest.raises(ValidationError, match="different conversation"):
        CognitiveTurnRequest(
            cognitive_turn_id=interpretation.cognitive_turn_id,
            conversation_ref=interpretation.conversation_ref,
            input_message_ids=interpretation.input_message_ids,
            focus_message_id=interpretation.focus_message_id,
            interpretation=interpretation,
            goal_snapshot=goal_update.snapshot,
            active_goals=goal_update.active_goal_views,
            runtime=RuntimeContext(
                capability_snapshot=CapabilitySnapshot(snapshot_id="appraisal-capability-1")
            ),
            previous_state=foreign_state,
            personality=personality,
            core_memory_ref="core-columbina-v1",
            memory_retrieval_policy_version="memory-retrieval-v1",
            **action_inputs(
                RuntimeContext(capability_snapshot=CapabilitySnapshot(snapshot_id="appraisal-capability-1")),
                runtime_snapshot_ref=interpretation.runtime_snapshot_id, core_memory_ref="core-columbina-v1",
            ),
        )
