"""Canonical Action Selection V0 contract invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from action_v0_support import request_for, result_for
from app.ai.Cognitive.system.v0.contracts.actions import (
    ACTION_CONTRACT_SCHEMA_VERSION, ACTION_REGISTRY_VERSION, ActionDefinitionRef, ActionFeatureValue,
    ActionGateResult, ActionGateStatus, ActionGateViolation, ActionGateCategory, ActionLifecycle,
    ActionPersonalityProfile, ActionPlan, ActionRuntimeContext, ActionScoreBreakdown, ActionSelectionRequest,
    ActionSelectionResult, ActionTargetRef, ActionTargetType, ActionTieResolution, ActionVersions, FeatureKey,
    GroundedActionCandidate, PlanTerminationKind, TieBreakField, canonical_decimal, decimal_from,
)

CASE = "check-understanding-paraphrase"


def test_versions_are_explicit_envelopes() -> None:
    versions = ActionVersions()
    assert versions.contract_schema == ACTION_CONTRACT_SCHEMA_VERSION == "action-contract-v0.4"
    assert versions.registry == ACTION_REGISTRY_VERSION == "conversational-actions-v0.2-shadow"
    assert versions.model_dump() == {
        "contract_schema": "action-contract-v0.4", "registry": "conversational-actions-v0.2-shadow",
        "construction": "action-construction-v0.2", "hard_gate": "action-hard-gate-v0.1",
        "features": "action-features-v0.4", "policy": "action-policy-v0.1", "planner": "action-planner-v0.4",
        "numeric_policy": "action-numeric-decimal6-v0.1",
        "safety_decision_schema": "application-safety-decision-v0.1", "safety_policy": "application-safety-policy-v0.1",
    }


def test_models_are_immutable_and_deeply_frozen() -> None:
    result = result_for(CASE)
    with pytest.raises(ValidationError):
        result.plan.moves[0].move_id = "changed"  # type: ignore[misc]
    parameters = result.plan.moves[1].parameters
    with pytest.raises(TypeError):
        parameters["question_form"] = "open_set"  # type: ignore[index]
    assert isinstance(result.steps, tuple) and isinstance(result.plan.moves, tuple)
    with pytest.raises(TypeError):
        result.plan.move_priorities[0]["priority"] = "primary"  # type: ignore[index]


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ActionDefinitionRef(action_key="give_feedback.acknowledge", definition_version=1, extra=True)  # type: ignore[call-arg]
    payload = request_for(CASE).model_dump(mode="python")
    payload["expected_plan"] = ["seek_information.check_understanding"]
    with pytest.raises(ValidationError):
        ActionSelectionRequest.model_validate(payload)


def test_canonical_decimal_policy_rejects_non_finite_and_non_canonical_values() -> None:
    assert canonical_decimal(Decimal("0.1") + Decimal("0.2")) == "0.300000"
    assert canonical_decimal(Decimal("-0.0000001")) == "0.000000"
    for value in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError):
            canonical_decimal(value)
    for text in ("0.3", "1e-6", "nan", "-0.000000", "0.30000000"):
        with pytest.raises(ValueError):
            decimal_from(text)
    with pytest.raises(ValidationError):
        ActionFeatureValue(feature_key=FeatureKey.GOAL_FIT, value="1.500000", status="known", calculator_rule_id="r")
    with pytest.raises(ValidationError):
        ActionFeatureValue(feature_key=FeatureKey.GOAL_FIT, value="0.500000", status="missing", calculator_rule_id="r")


def test_personality_known_and_unknown_semantics() -> None:
    unknown = ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref="c", status="unknown")
    assert unknown.affinity("warmth") is None
    with pytest.raises(ValidationError):
        ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref="c", status="unknown", warmth=0.5)
    with pytest.raises(ValidationError):
        ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref="c", status="known", warmth=0.5)
    for bad in (float("nan"), float("inf"), 1.5, -0.1):
        with pytest.raises(ValidationError):
            ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref="c", status="known",
                                     curiosity=0.5, warmth=bad, playfulness=0.5, directness=0.5)
    with pytest.raises(ValidationError):
        ActionPersonalityProfile(profile_id=" ", mapper_version="m", source_core_memory_ref="c", status="unknown")


def test_runtime_obligation_and_identifier_rules() -> None:
    base = {"runtime_snapshot_ref": "r", "capability_snapshot_ref": "c"}
    with pytest.raises(ValidationError):
        ActionRuntimeContext(**base, response_obligation="required")
    with pytest.raises(ValidationError):
        ActionRuntimeContext(**base, response_obligation="not_required", response_obligation_ref="x")
    with pytest.raises(ValidationError):
        ActionRuntimeContext(**base, response_obligation="not_required", available_capabilities=("a", "a"))
    with pytest.raises(ValidationError):
        ActionRuntimeContext(**base, response_obligation="not_required", last_tool_event_ref="event")


def test_target_references_reject_blank_and_duplicate_identities() -> None:
    with pytest.raises(ValidationError):
        ActionTargetRef(role="primary", target_type=ActionTargetType.UNKNOWN, ref=" ")
    candidate = result_for(CASE).steps[0].constructed_candidates[0]
    payload = candidate.model_dump(mode="python")
    payload["target_refs"] = (candidate.target_refs[0], candidate.target_refs[0])
    with pytest.raises(ValidationError):
        GroundedActionCandidate.model_validate(payload)
    payload["target_refs"] = tuple(reversed(candidate.target_refs)) if len(candidate.target_refs) > 1 else candidate.target_refs
    if len(candidate.target_refs) > 1:
        with pytest.raises(ValidationError):
            GroundedActionCandidate.model_validate(payload)


@pytest.mark.parametrize("mutation", (
    ("cognitive_turn_id", "another-turn"),
    ("conversation_ref", "another-conversation"),
    ("core_memory_ref", "another-core"),
))
def test_request_rejects_ownership_and_lineage_mismatches(mutation) -> None:
    payload = request_for(CASE).model_dump(mode="python")
    payload[mutation[0]] = mutation[1]
    with pytest.raises(ValidationError):
        ActionSelectionRequest.model_validate(payload)


def test_request_rejects_previous_state_foreign_goals_and_version_mismatch() -> None:
    request = request_for(CASE)
    previous = request.dynamic_state.model_copy(update={
        "state_snapshot_id": request.goal_snapshot.previous_dynamic_state_ref, "supersedes_snapshot_id": None, "revision": 1,
    })
    for update in (
        {"dynamic_state": previous},
        {"active_goals": tuple(reversed(request.active_goals))},
        {"versions": ActionVersions(policy="action-policy-v9")},
        {"runtime": request.runtime.model_copy(update={"runtime_snapshot_ref": "foreign-runtime"})},
    ):
        with pytest.raises(ValidationError):
            ActionSelectionRequest.model_validate({**request.model_dump(mode="python"), **update})


def test_request_rejects_unauthorized_or_same_turn_prior_records() -> None:
    request = request_for("realization-fidelity-prior-failure")
    prior = request.prior_realization_records[0]
    for replacement in (prior.model_copy(update={"authorized": False}), prior.model_copy(update={"cognitive_turn_ref": request.cognitive_turn_id})):
        with pytest.raises(ValidationError):
            ActionSelectionRequest.model_validate({**request.model_dump(mode="python"), "prior_realization_records": (replacement,)})


def test_result_json_round_trip_is_lossless() -> None:
    for case_id in (CASE, "repair-after-explicit-correction-stale-memory", "suggestion-advice-warning-force"):
        result = result_for(case_id)
        assert ActionSelectionResult.model_validate_json(result.model_dump_json()) == result
        assert ActionSelectionResult.model_validate(result.model_dump(mode="json")) == result


def test_plan_requires_exactly_one_derived_primary_and_valid_termination() -> None:
    plan = result_for(CASE).plan
    payload = plan.model_dump(mode="python")
    for update in (
        {"primary_move_ref": None},
        {"supporting_move_refs": tuple(item.move_id for item in plan.moves)},
        {"move_priorities": tuple({"move_ref": item.move_id, "priority": "primary"} for item in plan.moves)},
        {"termination": PlanTerminationKind.NO_VIABLE_PLAN},
        {"termination": PlanTerminationKind.CYCLE_GUARD},
    ):
        with pytest.raises(ValidationError):
            ActionPlan.model_validate({**payload, **update})
    with pytest.raises(ValidationError):
        ActionPlan.model_validate({**payload, "moves": (), "primary_move_ref": None, "supporting_move_refs": (),
                                   "move_priorities": (), "primary_designation": None})
    empty = ActionPlan.model_validate({**payload, "moves": (), "primary_move_ref": None, "supporting_move_refs": (),
                                       "move_priorities": (), "primary_designation": None, "response_obligation": "not_required"})
    assert empty.moves == ()


def test_result_rejects_broken_chains_and_selection_inconsistencies() -> None:
    result = result_for(CASE)
    payload = result.model_dump(mode="python")
    steps = list(result.steps)
    broken = steps[1].model_copy(update={"partial_state_before": steps[0].partial_state_before})
    with pytest.raises(ValidationError):
        ActionSelectionResult.model_validate({**payload, "steps": (steps[0], broken, *steps[2:])})
    with pytest.raises(ValidationError):
        ActionSelectionResult.model_validate({**payload, "moves": tuple(reversed(result.moves))})
    with pytest.raises(ValidationError):
        ActionSelectionResult.model_validate({**payload, "steps": tuple(steps[:-1])})


def test_gate_result_status_matches_violations_and_lifecycle() -> None:
    violation = ActionGateViolation(category=ActionGateCategory.CAPABILITY, code="capability_unavailable", subject_ref="x")
    base = {"candidate_ref": "c", "safety_decision_ref": "s", "definition_version": 1}
    with pytest.raises(ValidationError):
        ActionGateResult(**base, status=ActionGateStatus.SHADOW_ONLY, violations=(violation,), definition_lifecycle=ActionLifecycle.SHADOW)
    with pytest.raises(ValidationError):
        ActionGateResult(**base, status=ActionGateStatus.PASSED, definition_lifecycle=ActionLifecycle.SHADOW)
    with pytest.raises(ValidationError):
        ActionGateResult(**base, status=ActionGateStatus.SHADOW_ONLY, definition_lifecycle=ActionLifecycle.CANDIDATE)
    ordered = (
        ActionGateViolation(category=ActionGateCategory.CAPABILITY, code="a", subject_ref="x"),
        ActionGateViolation(category=ActionGateCategory.REGISTRY_LIFECYCLE, code="b", subject_ref="x"),
    )
    with pytest.raises(ValidationError):
        ActionGateResult(**base, status=ActionGateStatus.BLOCKED, violations=ordered, definition_lifecycle=ActionLifecycle.CANDIDATE)


def test_score_breakdown_is_self_consistent() -> None:
    scored = result_for(CASE).steps[0].scored_candidates[0]
    payload = scored.score.model_dump(mode="python")
    for key, value in (("score", "9.000000"), ("remaining_need", "0.000000"), ("context_multiplier", "1.750000"),
                       ("shared_move_overhead", "-0.200000")):
        with pytest.raises(ValidationError):
            ActionScoreBreakdown.model_validate({**payload, key: value})


def test_lexical_tie_resolution_can_never_be_preference_evidence() -> None:
    with pytest.raises(ValidationError):
        ActionTieResolution(tied_candidate_refs=("a", "b"), resolved_by=TieBreakField.LEXICAL_IDENTITY,
                            selected_candidate_ref="a", preference_evidence=True)
    with pytest.raises(ValidationError):
        ActionTieResolution(tied_candidate_refs=("a", "b"), resolved_by=TieBreakField.RESPONSE_COST,
                            selected_candidate_ref="c", preference_evidence=True)
