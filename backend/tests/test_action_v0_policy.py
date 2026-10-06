"""``action-policy-v0.1``: exact score formula, clamp, STOP, and tie resolution."""

from __future__ import annotations

from decimal import Decimal

import pytest

from action_v0_support import result_for
from app.ai.Cognitive.system.v0.action_selection_v0.policy import (
    COST_WEIGHTS, MULTIPLIER_WEIGHTS, NEED_WEIGHTS, SHARED_MOVE_OVERHEAD, STOP_SCORE, resolve_conflict, score_candidate,
)
from app.ai.Cognitive.system.v0.contracts.actions import (
    FEATURE_ORDER, ActionFeatureValue, FeatureKey as F, TieBreakField, canonical_decimal,
)

BASE = result_for("check-understanding-paraphrase").steps[0].constructed_candidates[0]


def _features(**values: str):
    return tuple(ActionFeatureValue(
        feature_key=key, value=canonical_decimal(Decimal(values.get(key.value, "1" if key == F.SEMANTIC_FIT else "0"))),
        status="known", calculator_rule_id="test.rule.v1",
    ) for key in FEATURE_ORDER)


def _candidate(name: str, *, key: str | None = None):
    update = {"candidate_id": f"candidate-{name}"}
    if key is not None:
        update["definition_ref"] = BASE.definition_ref.model_copy(update={"action_key": key})
    return BASE.model_copy(update=update)


def test_weights_are_the_accepted_bootstrap_vector() -> None:
    as_numbers = lambda pairs: {key.value: Decimal(value) for key, value in pairs}  # noqa: E731
    assert as_numbers(NEED_WEIGHTS) == {key: Decimal(value) for key, value in {
        "target_priority": "0.90", "goal_fit": "0.80", "expected_goal_progress": "1.80",
        "uncertainty_reduction": "1.30", "contradiction_repair": "1.40",
    }.items()}
    assert as_numbers(MULTIPLIER_WEIGHTS) == {key: Decimal(value) for key, value in {
        "evidence_support": "0.25", "personality_affinity": "0.10", "state_compatibility": "0.25", "plan_contribution": "0.25",
    }.items()}
    assert as_numbers(COST_WEIGHTS) == {key: Decimal(value) for key, value in {
        "repetition_cost": "-1.80", "response_cost": "-0.80", "soft_risk_cost": "-1.20",
    }.items()}
    assert SHARED_MOVE_OVERHEAD == "-0.300000" and STOP_SCORE == "0.000000"


def test_exact_score_formula_with_full_trace() -> None:
    features = _features(semantic_fit="0.75", target_priority="0.5", goal_fit="1", expected_goal_progress="0.5",
                         uncertainty_reduction="0.67", contradiction_repair="0.5", evidence_support="0.6",
                         personality_affinity="-0.4", state_compatibility="0.5", plan_contribution="1",
                         repetition_cost="0.35", response_cost="0.15", soft_risk_cost="0.25")
    scored = score_candidate(_candidate("formula"), features)
    need = Decimal("0.9") * Decimal("0.5") + Decimal("0.8") + Decimal("1.8") * Decimal("0.5") + Decimal("1.3") * Decimal("0.67") + Decimal("1.4") * Decimal("0.5")
    multiplier = Decimal(1) + Decimal("0.25") * Decimal("0.6") + Decimal("0.1") * Decimal("-0.4") + Decimal("0.25") * Decimal("0.5") + Decimal("0.25")
    expected = Decimal("0.75") * need * multiplier - Decimal("1.8") * Decimal("0.35") - Decimal("0.8") * Decimal("0.15") - Decimal("1.2") * Decimal("0.25") - Decimal("0.3")
    breakdown = scored.score
    assert breakdown.remaining_need == canonical_decimal(need)
    assert breakdown.context_multiplier == canonical_decimal(multiplier)
    assert breakdown.score == canonical_decimal(expected)
    assert [item.term for item in breakdown.need_contributions] == [f"need.{key.value}" for key, _ in NEED_WEIGHTS]
    assert all(item.signed_contribution.startswith("-") for item in breakdown.cost_contributions if Decimal(item.base_value) > 0)


@pytest.mark.parametrize(("values", "clamped"), (
    ({"evidence_support": "1", "state_compatibility": "1", "plan_contribution": "1", "personality_affinity": "1"}, "1.750000"),
    ({"evidence_support": "-1", "state_compatibility": "-1", "plan_contribution": "-1", "personality_affinity": "-1"}, "0.150000"),
))
def test_context_multiplier_is_clamped(values, clamped) -> None:
    scored = score_candidate(_candidate("clamp"), _features(goal_fit="1", **values))
    assert scored.score.context_multiplier == clamped
    if clamped == "1.750000":
        assert Decimal(scored.score.context_multiplier_unclamped) > Decimal("1.75")


def test_stop_is_the_zero_baseline_within_tolerance() -> None:
    zero = score_candidate(_candidate("zero"), _features(goal_fit="0.375"))
    assert zero.score.score == "0.000000"
    assert resolve_conflict((zero,), {"candidate-zero": _candidate("zero")}) == (None, None)
    tiny = score_candidate(_candidate("tiny"), _features(goal_fit="0.375002"))
    assert Decimal(tiny.score.score) > 0
    assert resolve_conflict((tiny,), {"candidate-tiny": _candidate("tiny")})[0] is tiny


def _tie(a_values: dict, b_values: dict):
    candidates = {"candidate-a": _candidate("a", key="provide_information.answer"), "candidate-b": _candidate("b", key="give_feedback.acknowledge")}
    scored = (score_candidate(candidates["candidate-a"], _features(**a_values)), score_candidate(candidates["candidate-b"], _features(**b_values)))
    assert scored[0].score.score == scored[1].score.score
    return resolve_conflict(scored, candidates)


def test_every_tie_break_level_in_order() -> None:
    # Each pair has an identical canonical score and differs first at the named field.
    winner, tie = _tie({"expected_goal_progress": "0.5"}, {"expected_goal_progress": "0.4", "target_priority": "0.2"})
    assert (winner.candidate_ref, tie.resolved_by, tie.preference_evidence) == ("candidate-a", TieBreakField.EXPECTED_GOAL_PROGRESS, True)
    winner, tie = _tie({"target_priority": "0.4", "goal_fit": "0.45"}, {"target_priority": "0.8"})
    assert (winner.candidate_ref, tie.resolved_by) == ("candidate-b", TieBreakField.TARGET_PRIORITY)
    winner, tie = _tie({"goal_fit": "1", "soft_risk_cost": "0.4"}, {"goal_fit": "1", "soft_risk_cost": "0.2", "response_cost": "0.3"})
    assert (winner.candidate_ref, tie.resolved_by) == ("candidate-b", TieBreakField.SOFT_RISK_COST)
    winner, tie = _tie({"goal_fit": "1", "response_cost": "0.3"},
                       {"goal_fit": "1", "evidence_support": "0.8", "state_compatibility": "0.4", "response_cost": "0.6"})
    assert (winner.candidate_ref, tie.resolved_by) == ("candidate-a", TieBreakField.RESPONSE_COST)
    winner, tie = _tie({"goal_fit": "1"}, {"goal_fit": "1"})
    assert (winner.candidate_ref, tie.resolved_by, tie.preference_evidence) == ("candidate-b", TieBreakField.LEXICAL_IDENTITY, False)
    assert set(tie.tied_candidate_refs) == {"candidate-a", "candidate-b"}


def test_recorded_ties_in_design_traces_resolve_deterministically() -> None:
    seen = []
    for case_id in ("preference-scale-question", "suggestion-advice-warning-force", "evidence-grounded-correction"):
        for step in result_for(case_id).steps:
            if step.tie_resolution is not None:
                seen.append(step.tie_resolution)
                assert step.tie_resolution.selected_candidate_ref == step.selected_candidate_ref
                if step.tie_resolution.resolved_by == TieBreakField.LEXICAL_IDENTITY:
                    assert step.tie_resolution.preference_evidence is False
    assert seen, "the fixture exercises at least one recorded tie"
