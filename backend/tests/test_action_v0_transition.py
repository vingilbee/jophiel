"""Immutable partial-plan states and the fixed V0 transition."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from action_v0_support import documents, request_for, result_for
from app.ai.Cognitive.system.v0.action_selection_v0.features import FeatureCalculator
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFAULT_REGISTRY
from app.ai.Cognitive.system.v0.action_selection_v0.transition import apply_transition, empty_state
from app.ai.Cognitive.system.v0.contracts.actions import PartialPlanState, decimal_from


def _signature(state: PartialPlanState) -> str:
    data = state.model_dump(mode="json"); data.pop("state_signature")
    return f"plan-state-{hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]}"


def test_state_chain_is_immutable_and_signed() -> None:
    for case in documents()[1]["cases"]:
        result = result_for(case["case_id"])
        signatures = [result.steps[0].partial_state_before.state_signature]
        for step in result.steps:
            assert step.partial_state_before.state_signature == _signature(step.partial_state_before)
            assert step.partial_state_after.state_signature == _signature(step.partial_state_after)
            if step.selected_candidate_ref:
                signatures.append(step.partial_state_after.state_signature)
                assert step.partial_state_after.step_index == step.partial_state_before.step_index + 1
        assert len(signatures) == len(set(signatures))
        assert result.plan.final_partial_state_ref == result.steps[-1].partial_state_after.state_signature


def test_transition_never_mutates_the_prior_state() -> None:
    result = result_for("greet-and-introduce-topic")
    step = result.steps[0]
    before = step.partial_state_before.model_dump(mode="json")
    calculator = FeatureCalculator(DEFAULT_REGISTRY, ActionReferenceIndex(request_for("greet-and-introduce-topic")))
    candidate = next(item for item in step.constructed_candidates if item.candidate_id == step.selected_candidate_ref)
    scored = next(item for item in step.scored_candidates if item.candidate_ref == candidate.candidate_id)
    analysis = calculator.analyze(candidate, step.partial_state_before)
    after, move, effects, delta, basis = apply_transition(step.partial_state_before, analysis, scored, step_index=0,
                                                          context_ref=result.context_ref, realization_constraints=())
    assert step.partial_state_before.model_dump(mode="json") == before
    assert after == step.partial_state_after and move.planned_effect_refs == tuple(item.effect_id for item in effects)
    assert basis == step.transition_basis
    with pytest.raises(ValueError):
        apply_transition(step.partial_state_before, analysis, scored, step_index=1, context_ref=result.context_ref, realization_constraints=())
    other = next(item for item in step.scored_candidates if item.candidate_ref != candidate.candidate_id)
    with pytest.raises(ValueError):
        apply_transition(step.partial_state_before, analysis, other, step_index=0, context_ref=result.context_ref, realization_constraints=())


def test_only_planned_states_are_recorded() -> None:
    for case in documents()[1]["cases"]:
        result = result_for(case["case_id"])
        final = result.steps[-1].partial_state_after
        assert set(final.labels("planned_unknown_handling").values()) <= {"elicitation_planned", "verification_planned", "qualification_planned"}
        assert set(final.labels("planned_contradiction_repairs").values()) <= {"correction_planned", "exposure_planned"}
        for effect in result.planned_effects:
            assert effect.planned_state.endswith("_planned") and effect.actual_outcome_claim is False
        request = request_for(case["case_id"])
        assert all(record.status.value == "active" for record in request.goal_snapshot.records)
        assert all(Decimal(0) <= value <= 1 for value in final.ledger("planned_goal_coverage").values())


def test_unknowns_and_goals_remain_unresolved_after_planning() -> None:
    result = result_for("missing-recipient-open-clarification")
    final = result.steps[-1].partial_state_after
    assert final.labels("planned_unknown_handling") == {"frame-1#recipient": "elicitation_planned"}
    coverage = final.ledger("planned_goal_coverage")
    assert coverage["goal-recipient#goal-recipient:requirement"] == Decimal("0.5")
    assert coverage["goal-response#goal-response:requirement"] == Decimal("1")
    request = request_for("missing-recipient-open-clarification")
    assert [item.status.value for item in request.goal_snapshot.requirement_assessments] == ["open"] * 3
    assert request.interpretation.unknowns[0].status == "unresolved"


def test_residual_need_falls_only_for_matching_target_effects() -> None:
    result = result_for("check-understanding-paraphrase")
    first, second = result.steps[0], result.steps[1]
    after_first = first.partial_state_after.ledger("residual_target_needs")
    assert list(after_first.values()) == [Decimal(0)]
    (paraphrase_key,) = after_first
    assert paraphrase_key.startswith("feedback.paraphrase@1|paraphrase_planned|set[")
    after_second = second.partial_state_after.ledger("residual_target_needs")
    assert after_second[paraphrase_key] == 0 and len(after_second) == 2
    check_key = next(key for key in after_second if key != paraphrase_key)
    assert check_key == "elicitation.understanding_check@1|verification_planned|proposition:prop-meeting-date-june5"
    assert first.partial_state_delta.target_effect_updates[0].value == "1.000000"


def test_contradiction_handling_is_planned_never_repaired() -> None:
    result = result_for("evidence-grounded-correction")
    assert result.steps[-1].partial_state_after.labels("planned_contradiction_repairs") == {
        "contradiction-appointment-date": "correction_planned",
    }
    request = request_for("evidence-grounded-correction")
    assert request.interpretation.contradictions[0].status == "active"
    verify = result_for("verify-report-upload-yes-no").steps[-1].partial_state_after
    assert verify.labels("planned_contradiction_repairs") == {"contradiction-no-attachment": "exposure_planned"}


def test_counts_previous_move_and_accumulated_costs() -> None:
    result = result_for("repair-after-explicit-correction-stale-memory")
    final = result.steps[-1].partial_state_after
    assert final.step_index == 4 and final.previous_move_ref == result.plan.moves[-1].move_id
    assert {item.key: item.count for item in final.family_counts} == {
        "manage_dialogue": 2, "seek_information": 1, "support_socially": 1,
    }
    costs = sum((decimal_from(step.partial_state_delta.added_response_cost) for step in result.steps if step.partial_state_delta), Decimal(0))
    assert decimal_from(final.accumulated_response_cost) == costs
    assert empty_state("plan-x").step_index == 0 and empty_state("plan-x") == empty_state("plan-x")
