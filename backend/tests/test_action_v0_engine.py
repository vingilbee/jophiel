"""Sequential planning, STOP, primary designation, and fail-closed phases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from action_v0_support import action_keys, fixture_case, mutate_inputs, request_for, result_for
from cognitive_action_support import safety_decision
from app.ai.Cognitive.system.v0.action_selection_v0 import engine as engine_module
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.engine import ActionSelectionEngine
from app.ai.Cognitive.system.v0.action_selection_v0.errors import ActionSelectionError
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFINITIONS, ActionRegistry
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionFailurePhase, ActionSelectionResult, FeatureKey as F, PlanTerminationKind,
)


@pytest.mark.parametrize(("case_id", "expected"), (
    ("missing-recipient-open-clarification", ["seek_information.clarify"]),
    ("check-understanding-paraphrase", ["give_feedback.paraphrase", "seek_information.check_understanding"]),
    ("greet-and-introduce-topic", ["manage_dialogue.greet", "manage_dialogue.introduce_topic", "seek_information.explore_goal"]),
    ("repair-after-explicit-correction-stale-memory", [
        "support_socially.apologize", "manage_dialogue.repair", "manage_dialogue.self_correct", "seek_information.clarify",
    ]),
))
def test_one_to_four_move_plans(case_id: str, expected: list[str]) -> None:
    result = result_for(case_id)
    assert action_keys(result) == expected
    assert result.plan.termination == PlanTerminationKind.STOP
    assert len(result.steps) == len(expected) + 1


def test_alternative_acceptable_order_is_supported_by_the_scores() -> None:
    result = result_for("four-move-plan-order-contrast")
    assert action_keys(result) == [
        "provide_information.explain", "provide_information.exemplify", "give_feedback.acknowledge",
        "seek_information.check_understanding",
    ]
    first = result.steps[0]
    by_key = {candidate.definition_ref.action_key: scored for candidate in first.constructed_candidates
              for scored in first.scored_candidates if scored.candidate_ref == candidate.candidate_id}
    assert Decimal(by_key["provide_information.explain"].score.score) > Decimal(by_key["give_feedback.acknowledge"].score.score)
    plans = {plan["plan_id"]: [move["action_key"] for move in plan["moves"]] for plan in fixture_case("four-move-plan-order-contrast")["acceptable_plans"]}
    assert action_keys(result) == plans["plan-b"] and sorted(plans["plan-a"]) == sorted(plans["plan-b"])


def test_stop_is_selected_only_when_nothing_is_positive() -> None:
    early = result_for("missing-recipient-open-clarification").steps[-1]
    assert early.termination == PlanTerminationKind.STOP and early.stop_score == "0.000000"
    assert all(Decimal(item.score.score) <= 0 for item in early.scored_candidates)
    late = result_for("repair-after-explicit-correction-stale-memory").steps[-1]
    assert late.termination == PlanTerminationKind.STOP and late.step_index == 4


def test_late_continuations_lose_to_stop_through_costs_not_masks() -> None:
    final = result_for("return-to-topic-and-close").steps[-1]
    assert final.termination == PlanTerminationKind.STOP
    for scored in final.scored_candidates:
        assert Decimal(scored.score.score) <= 0
    repeated = result_for("persistent-shared-signal-candidate").steps[-1]
    clarify = [item for item in repeated.constructed_candidates if item.definition_ref.action_key == "seek_information.clarify"]
    assert clarify and all(
        next(s for s in repeated.scored_candidates if s.candidate_ref == item.candidate_id).feature(F.REPETITION_COST) == 1 for item in clarify
    )


def test_exactly_one_primary_is_derived_from_focal_goal_coverage() -> None:
    result = result_for("realization-fidelity-prior-failure")
    plan = result.plan
    assert plan.primary_move_ref == plan.moves[2].move_id
    assert plan.supporting_move_refs == (plan.moves[0].move_id, plan.moves[1].move_id)
    designation = plan.primary_designation
    assert designation.focal_primary_goal_ref == request_for("realization-fidelity-prior-failure").goal_snapshot.focus_decision.primary_goal_ref
    coverage = {item["move_ref"]: Decimal(item["direct_focal_coverage"]) for item in designation.compared_moves}
    assert coverage[plan.primary_move_ref] == Decimal("0.5") and max(coverage.values()) == Decimal("0.5")


def test_empty_plan_is_permitted_only_without_a_response_obligation() -> None:
    blocked = mutate_inputs("primary-social-joke", ("runtime", "safety_decision"), {"disposition": "block_response"})
    with pytest.raises(ActionSelectionError) as caught:
        adapter.run_case(blocked)
    assert (caught.value.failed_phase, caught.value.code) == (ActionFailurePhase.PLAN_VALIDATION, "no_viable_plan")
    blocked["canonical_inputs"]["runtime"]["response_obligation"] = "not_required"
    result = adapter.run_case(blocked)
    assert result.plan.moves == () and result.plan.termination == PlanTerminationKind.STOP
    assert result.plan.response_obligation == "not_required"


def test_selection_is_deterministic_and_independent_of_registry_input_order() -> None:
    request = request_for("reassure-without-promise")
    first = ActionSelectionEngine().select(request)
    second = ActionSelectionEngine(registry=ActionRegistry(tuple(reversed(DEFINITIONS)))).select(request)
    assert first == second == result_for("reassure-without-promise")


def test_cycle_guard_is_an_operational_failure_never_stop() -> None:
    with pytest.raises(ActionSelectionError) as caught:
        ActionSelectionEngine(max_steps=2).select(request_for("repair-after-explicit-correction-stale-memory"))
    assert (caught.value.failed_phase, caught.value.code) == (ActionFailurePhase.PLAN_TRANSITION, "cycle_guard")
    with pytest.raises(ValueError):
        ActionSelectionEngine(max_steps=0)


class _Boom(RuntimeError):
    pass


def _raise(*_args, **_kwargs):
    raise _Boom("private text: Could you send the report?")


@pytest.mark.parametrize("phase", list(ActionFailurePhase))
def test_every_failure_phase_is_typed_chained_and_content_safe(phase: ActionFailurePhase, monkeypatch) -> None:
    request = request_for("check-understanding-paraphrase")
    engine = ActionSelectionEngine()
    if phase == ActionFailurePhase.REQUEST_VALIDATION:
        request = request.model_copy(update={"grounded_objects": (
            request.grounded_objects[0].model_copy(update={"about_refs": ("frame-1", "not-indexed")}), *request.grounded_objects[1:],
        )})
    elif phase == ActionFailurePhase.REGISTRY_RESOLUTION:
        engine = ActionSelectionEngine(registry=ActionRegistry(tuple(item for item in DEFINITIONS if item.definition_ref.action_key != "seek_information.clarify")))
    elif phase == ActionFailurePhase.CANDIDATE_CONSTRUCTION:
        monkeypatch.setattr(engine_module.CandidateConstructor, "construct", _raise)
    elif phase == ActionFailurePhase.HARD_GATE:
        monkeypatch.setattr(engine_module.HardGate, "evaluate", _raise)
    elif phase == ActionFailurePhase.FEATURE_EXTRACTION:
        monkeypatch.setattr(engine_module.FeatureCalculator, "compute", _raise)
    elif phase == ActionFailurePhase.SCORING:
        monkeypatch.setattr(engine_module, "score_candidate", _raise)
    elif phase == ActionFailurePhase.CONFLICT_RESOLUTION:
        monkeypatch.setattr(engine_module, "resolve_conflict", _raise)
    elif phase == ActionFailurePhase.PLAN_TRANSITION:
        monkeypatch.setattr(engine_module, "apply_transition", _raise)
    elif phase == ActionFailurePhase.PLAN_VALIDATION:
        request = request.model_copy(update={"runtime": request.runtime.model_copy(update={"safety_decision": safety_decision(request.runtime.runtime_snapshot_ref, "block_response")})})
    elif phase == ActionFailurePhase.TRACE_VALIDATION:
        monkeypatch.setattr(engine_module.ActionSelectionResult, "model_validate_json", classmethod(lambda cls, *a, **k: None))
    with pytest.raises(ActionSelectionError) as caught:
        engine.select(request)
    error = caught.value
    assert error.failed_phase == phase
    assert error.cognitive_turn_id == request.cognitive_turn_id
    assert error.__cause__ is not None
    assert error.category and error.code and error.safe_message
    for secret in ("private", "Could you", "report?", "prop-meeting-date-june5"):
        assert secret not in error.safe_message and secret not in str(error)


def test_failures_return_no_partial_result() -> None:
    engine = ActionSelectionEngine(max_steps=1)
    request = request_for("check-understanding-paraphrase")
    returned = None
    with pytest.raises(ActionSelectionError):
        returned = engine.select(request)
    assert returned is None
    assert isinstance(ActionSelectionEngine().select(request), ActionSelectionResult)
