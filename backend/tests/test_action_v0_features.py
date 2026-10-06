"""The 13-feature V0 manifest, normalization, and missing-value semantics."""

from __future__ import annotations

from decimal import Decimal

from action_v0_support import (
    candidate_by_key, documents, mutate_inputs, request_for, result_for, scored_for,
)
from app.ai.Cognitive.system.v0.action_selection_v0 import features as features_module
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.features import (
    GOAL_PRIORITY, SUPPORT, FeatureCalculator,
)
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFAULT_REGISTRY
from app.ai.Cognitive.system.v0.contracts.actions import (
    FEATURE_ORDER, FEATURE_RANGES, ActionPersonalityProfile, FeatureKey as F, FeatureStatus,
)


def _feature(case_id: str, step: int, action_key: str, key: F, *, ref: str | None = None) -> Decimal:
    trace = result_for(case_id).steps[step]
    candidates = candidate_by_key(trace, action_key)
    if ref is not None:
        candidates = [item for item in candidates if any(target.ref == ref for target in item.target_refs)]
    return scored_for(trace, candidates[0].candidate_id).feature(key)


def _status(case_id: str, step: int, action_key: str, key: F):
    trace = result_for(case_id).steps[step]
    scored = scored_for(trace, candidate_by_key(trace, action_key)[0].candidate_id)
    return next(item for item in scored.features if item.feature_key == key)


def test_every_eligible_candidate_carries_all_thirteen_bounded_features() -> None:
    assert len(FEATURE_ORDER) == 13
    for case in documents()[1]["cases"]:
        for step in result_for(case["case_id"]).steps:
            for scored in step.scored_candidates:
                assert tuple(item.feature_key for item in scored.features) == FEATURE_ORDER
                for item in scored.features:
                    low, high = FEATURE_RANGES[item.feature_key]
                    assert Decimal(low) <= Decimal(item.value) <= Decimal(high)
                    assert item.calculator_rule_id
                    if item.status != FeatureStatus.KNOWN:
                        assert Decimal(item.value) == 0


def test_required_normalization_tables() -> None:
    assert GOAL_PRIORITY == {"low": Decimal("0.33"), "moderate": Decimal("0.67"), "high": Decimal("1.00")}
    assert SUPPORT == {"weak": Decimal("0.25"), "moderate": Decimal("0.60"), "strong": Decimal("1.00")}
    assert (features_module.DIRECT_LINK, features_module.SUPPORTING_LINK) == (Decimal("1.00"), Decimal("0.50"))


def test_semantic_fit_is_exact_or_reviewed_superclass() -> None:
    assert _feature("out-of-scope-coined-command", 0, "seek_information.clarify", F.SEMANTIC_FIT, ref="frame-1#predicate") == 1
    assert _feature("out-of-scope-coined-command", 0, "seek_information.clarify", F.SEMANTIC_FIT, ref="frame-1#object_reference") == Decimal("0.75")


def test_direct_versus_supporting_goal_links() -> None:
    assert _feature("action-selection-prior-failure", 1, "provide_information.answer", F.GOAL_FIT) == 1
    assert _feature("action-selection-prior-failure", 1, "provide_information.answer", F.EXPECTED_GOAL_PROGRESS) == 1
    assert _feature("accept-summary-request", 1, "commit_or_coordinate.state_next_step", F.GOAL_FIT) == Decimal("0.5")
    assert _feature("accept-summary-request", 1, "commit_or_coordinate.state_next_step", F.EXPECTED_GOAL_PROGRESS) == Decimal("0.5")
    for effect, requirement in features_module.GOAL_LINKS:
        if features_module.GOAL_LINKS[(effect, requirement)] == "direct":
            assert requirement in features_module.REALIZATION_SATISFIABLE


def test_residual_need_reduces_later_goal_features() -> None:
    first = _feature("missing-recipient-open-clarification", 0, "give_feedback.acknowledge", F.GOAL_FIT)
    later = _feature("missing-recipient-open-clarification", 1, "give_feedback.acknowledge", F.GOAL_FIT)
    assert (first, later) == (1, 0)
    assert _feature("missing-recipient-open-clarification", 1, "give_feedback.acknowledge", F.EXPECTED_GOAL_PROGRESS) == 0


def test_multi_target_features_use_residual_priority_weighted_mean() -> None:
    assert _feature("check-understanding-paraphrase", 0, "give_feedback.paraphrase", F.TARGET_PRIORITY) == 1
    assert _feature("check-understanding-paraphrase", 0, "give_feedback.paraphrase", F.EVIDENCE_SUPPORT) == Decimal("0.6")
    calculator = FeatureCalculator(DEFAULT_REGISTRY, ActionReferenceIndex(request_for("check-understanding-paraphrase")))
    assert calculator._weighted_mean({"a": Decimal("0.4"), "b": Decimal("0.9")}, {"a": Decimal(0), "b": Decimal(0)}) == 0
    assert calculator._weighted_mean({"a": Decimal("0.5"), "b": Decimal("1")}, {"a": Decimal("1"), "b": Decimal("3")}) == Decimal("0.875")


def test_repetition_schedule_bands_use_the_maximum() -> None:
    assert _feature("realization-fidelity-prior-failure", 1, "support_socially.apologize", F.REPETITION_COST) == 1
    same_effect = [
        scored_for(result_for("preference-scale-question").steps[1], item.candidate_id).feature(F.REPETITION_COST)
        for item in candidate_by_key(result_for("preference-scale-question").steps[1], "seek_information.explore_preference")
    ]
    assert sorted(same_effect) == [Decimal("0.75"), Decimal("1")]
    assert _feature("museum-reference-choice-disambiguation", 1, "seek_information.clarify", F.REPETITION_COST) == Decimal("0.5")
    assert _feature("realization-fidelity-prior-failure", 0, "seek_information.clarify", F.REPETITION_COST) == Decimal("0.35")
    trace = result_for("realization-fidelity-prior-failure").steps[2]
    scored = scored_for(trace, candidate_by_key(trace, "seek_information.clarify")[0].candidate_id)
    repetition = next(item for item in scored.features if item.feature_key == F.REPETITION_COST)
    assert repetition.value == "0.350000" and repetition.manifest_entry_ids == ("repetition.previous-turn-exact.v1",)


def test_unknown_personality_is_explicit_and_neutral() -> None:
    case = mutate_inputs("playful-reframe-welcome-context", ("personality",), {"status": "unknown"})
    result = adapter.run_case(case)
    for step in result.steps:
        for scored in step.scored_candidates:
            feature = next(item for item in scored.features if item.feature_key == F.PERSONALITY_AFFINITY)
            assert feature.value == "0.000000" and feature.status in {FeatureStatus.MISSING, FeatureStatus.INAPPLICABLE}
    assert result.plan.moves


def test_personality_is_bounded_and_changes_neither_facts_nor_gates() -> None:
    request = request_for("playful-reframe-welcome-context")
    calculator = FeatureCalculator(DEFAULT_REGISTRY, ActionReferenceIndex(request))
    for affinity, expected in ((1.0, Decimal(1)), (0.0, Decimal(-1)), (0.5, Decimal(0)), (0.9, Decimal("0.8"))):
        profile = ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref=request.core_memory_ref,
                                           status="known", curiosity=0.5, warmth=0.5, playfulness=affinity, directness=0.5)
        calculator.request = request.model_copy(update={"personality": profile})
        value, status, _ = calculator._personality("social_play.playful_reframe")
        assert (value, status) == (expected, FeatureStatus.KNOWN)
    low = adapter.run_case(mutate_inputs("playful-reframe-welcome-context", ("personality", "playfulness"), 0.0))
    high = adapter.run_case(mutate_inputs("playful-reframe-welcome-context", ("personality", "playfulness"), 1.0))
    for a, b in zip(low.steps, high.steps):
        assert [c.lexical_identity for c in a.constructed_candidates] == [c.lexical_identity for c in b.constructed_candidates]
        assert [(g.status, g.violations) for g in a.gate_results] == [(g.status, g.violations) for g in b.gate_results]
        for x, y in zip(a.scored_candidates, b.scored_candidates):
            for fx, fy in zip(x.features, y.features):
                if fx.feature_key != F.PERSONALITY_AFFINITY:
                    assert fx.value == fy.value


def test_state_compatibility_uses_only_known_state() -> None:
    missing = _status("acknowledge-without-agreement", 0, "give_feedback.acknowledge", F.STATE_COMPATIBILITY)
    assert missing.status == FeatureStatus.MISSING and missing.value == "0.000000"
    known = _status("repair-after-explicit-correction-stale-memory", 0, "support_socially.apologize", F.STATE_COMPATIBILITY)
    assert known.status == FeatureStatus.KNOWN
    assert "state.repair-pressure.v1" in known.manifest_entry_ids
    appraisal = _status("missing-recipient-open-clarification", 0, "seek_information.clarify", F.STATE_COMPATIBILITY)
    assert appraisal.value == "1.000000" and appraisal.manifest_entry_ids == ("state.goal-relevance-high-direct.v1",)


def test_plan_contribution_is_zero_for_an_empty_plan_and_relation_based_later() -> None:
    first = _status("accept-summary-request", 0, "commit_or_coordinate.accept_request", F.PLAN_CONTRIBUTION)
    assert (first.value, first.status) == ("0.000000", FeatureStatus.INAPPLICABLE)
    later = _status("accept-summary-request", 1, "commit_or_coordinate.state_next_step", F.PLAN_CONTRIBUTION)
    assert later.value == "1.000000" and later.provenance_refs


def test_manifests_contain_no_case_identifiers_or_action_bias() -> None:
    case_ids = {item["case_id"] for item in documents()[1]["cases"]}
    for name in ("GOAL_LINKS", "PERSONALITY_RELATIONS", "APPRAISAL_RELATIONS", "CONTROL_RELATIONS", "RESPONSE_COSTS"):
        text = repr(getattr(features_module, name))
        assert not any(case_id in text for case_id in case_ids), name
    assert set(features_module.RESPONSE_COSTS) == {item.definition_ref.action_key for item in DEFAULT_REGISTRY.definitions}
