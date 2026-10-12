"""Deterministic, declarative candidate construction."""

from __future__ import annotations

import pytest

from action_v0_support import (
    canonical_case, candidate_by_key, documents, index_for, request_for, result_for,
)
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.construction import (
    BINDING_MANIFEST, CandidateConstructor, PlanView, Recipe, recipe_for,
)
from app.ai.Cognitive.system.v0.action_selection_v0.registry import (
    BOUNDARY_ONLY_KEYS, DEFAULT_REGISTRY, SHADOW_KEYS, UNCOVERED_KEYS,
)
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionLifecycle, ActionTargetDescriptor, ActionTargetType, ParameterValueType,
)
from pydantic import ValidationError


def _construct(case_id: str, plan: PlanView = PlanView()):
    return CandidateConstructor(DEFAULT_REGISTRY).construct(index_for(case_id), plan, "context-test")


def test_every_shadow_definition_has_a_recipe_bound_to_accepted_types() -> None:
    for key in SHADOW_KEYS | BOUNDARY_ONLY_KEYS:
        recipe = recipe_for(key)
        assert recipe is not None, key
        definition = DEFAULT_REGISTRY.by_key(key)
        assert set(recipe.target_types) <= set(definition.target_schema.accepted_types), key
        assert recipe.rule_id.startswith("action-propose.") and recipe.rule_id.endswith(".v1")
    assert all(recipe_for(key) is None for key in UNCOVERED_KEYS)
    assert len({item.action_key for item in BINDING_MANIFEST}) == 53
    assert len(BINDING_MANIFEST) == 55  # two additional, version-gated optional-exploration recipes (v1.3, v1.5)
    for recipe in BINDING_MANIFEST:
        assert set(recipe.target_types) <= set(DEFAULT_REGISTRY.by_key(recipe.action_key).target_schema.accepted_types)


def test_construction_is_deterministic_and_canonically_ordered() -> None:
    first = _construct("repair-after-explicit-correction-stale-memory")
    second = _construct("repair-after-explicit-correction-stale-memory")
    assert first == second
    identities = [item.lexical_identity for item in first[0]]
    assert identities == sorted(identities)


def test_missing_targets_parameters_and_evidence_become_uninstantiable() -> None:
    _candidates, failures = _construct("missing-recipient-open-clarification")
    codes = {item.definition_ref.action_key: set(item.missing_codes) for item in failures}
    assert codes["commit_or_coordinate.accept_request"] == {"acceptance_decided"}
    assert codes["commit_or_coordinate.decline_request"] == {"cannot_accept"}
    _candidates, failures = _construct("free-reflection-experience")
    codes = {item.definition_ref.action_key: set(item.missing_codes) for item in failures}
    assert codes["support_socially.validate_experience"] == {"known_valence"}
    assert codes["support_socially.comfort"] == {"negative_valence"}
    candidates, failures = _construct("check-understanding-paraphrase")
    assert not candidate_by_key(type("S", (), {"constructed_candidates": candidates}), "seek_information.check_understanding")
    assert {"externalized_in_plan"} <= {code for item in failures for code in item.missing_codes}
    for item in failures:
        assert item.missing_codes and item.proposal_rule_id


def test_question_forms_follow_explicit_structure_only() -> None:
    step = result_for("preference-scale-question").steps[0]
    forms = {item.parameters["question_form"]: item for item in candidate_by_key(step, "seek_information.explore_preference")}
    assert set(forms) == {"scale", "choice"}
    assert (forms["scale"].parameters["scale_min"], forms["scale"].parameters["scale_max"]) == (0, 5)
    assert len(forms["choice"].parameters["candidate_answer_refs"]) == 3
    choice = candidate_by_key(result_for("museum-reference-choice-disambiguation").steps[0], "seek_information.disambiguate")
    assert [item.parameters["question_form"] for item in choice] == ["choice"]
    open_referent = candidate_by_key(result_for("out-of-scope-coined-command").steps[0], "seek_information.disambiguate")
    assert [item.parameters["question_form"] for item in open_referent] == ["open_set"]
    verify = candidate_by_key(result_for("verify-report-upload-yes-no").steps[0], "seek_information.verify")
    assert [item.parameters["question_form"] for item in verify] == ["yes_no"]
    check = candidate_by_key(result_for("check-understanding-paraphrase").steps[1], "seek_information.check_understanding")
    assert [item.parameters["question_form"] for item in check] == ["check"]


def test_choice_requires_two_grounded_alternatives_and_scale_requires_ordered_bounds() -> None:
    with pytest.raises(ValidationError):
        ActionTargetDescriptor(ref="x", target_type=ActionTargetType.SEMANTIC_ARGUMENT, candidate_answer_refs=["only-one"])
    for bounds in ((5, 5), (5, 0)):
        with pytest.raises(ValidationError):
            ActionTargetDescriptor(ref="x", target_type=ActionTargetType.SEMANTIC_ARGUMENT, scale_min=bounds[0], scale_max=bounds[1])
    with pytest.raises(ValidationError):
        ActionTargetDescriptor(ref="x", target_type=ActionTargetType.SEMANTIC_ARGUMENT, scale_min=0)


def test_multi_target_binding_is_a_canonical_target_set() -> None:
    paraphrase = candidate_by_key(result_for("check-understanding-paraphrase").steps[0], "give_feedback.paraphrase")
    assert len(paraphrase) == 1
    members = paraphrase[0].target_refs
    assert [item.role for item in members] == ["member", "member"]
    assert list(members) == sorted(members, key=lambda item: item.sort_key)
    assert {item.ref for item in members} == {"hyp-1", "prop-meeting-date-june5"}


def test_exact_duplicates_are_canonicalized_with_retained_provenance() -> None:
    duplicate = Recipe(action_key="give_feedback.acknowledge", rule_id="action-propose.acknowledge-alternate-rule.v1",
                       target_types=(ActionTargetType.USER_ACT,), filters=("focus_contribution",), evidence=("grounding",))
    index = index_for("acknowledge-without-agreement")
    baseline, _ = CandidateConstructor(DEFAULT_REGISTRY).construct(index, PlanView(), "ctx")
    merged, _ = CandidateConstructor(DEFAULT_REGISTRY, (*BINDING_MANIFEST, duplicate)).construct(index, PlanView(), "ctx")
    assert [item.lexical_identity for item in merged] == [item.lexical_identity for item in baseline]
    acknowledgement = next(item for item in merged if item.definition_ref.action_key == "give_feedback.acknowledge")
    assert acknowledgement.proposal_rule_ids == ("action-propose.acknowledge.v1", "action-propose.acknowledge-alternate-rule.v1")
    original = next(item for item in baseline if item.definition_ref.action_key == "give_feedback.acknowledge")
    assert acknowledgement.candidate_id == original.candidate_id
    assert acknowledgement.target_refs == original.target_refs


def test_constructed_parameters_are_typed_and_never_invented() -> None:
    for case_id in ("check-understanding-paraphrase", "evidence-grounded-correction", "decline-user-suggestion",
                    "repair-after-explicit-correction-stale-memory", "uncertain-credit-multimove-response"):
        index = index_for(case_id)
        for step in result_for(case_id).steps:
            for candidate in step.constructed_candidates:
                definition = DEFAULT_REGISTRY.resolve(candidate.definition_ref)
                for key, value in candidate.parameters.items():
                    rule = definition.parameter_rule(key)
                    assert rule is not None, (case_id, key)
                    if rule.value_type == ParameterValueType.ENUM:
                        assert value in rule.allowed_values
                    elif rule.value_type in {ParameterValueType.REFERENCE, ParameterValueType.REFERENCE_ARRAY}:
                        refs = [value] if isinstance(value, str) else list(value)
                        assert all(index.resolves(ref) for ref in refs)
                    else:
                        assert isinstance(value, int | float)
                for evidence in candidate.supporting_evidence_refs:
                    assert index.resolves(evidence.ref)


def test_construction_ignores_expected_plans_rationales_and_notes() -> None:
    case = canonical_case("four-move-plan-order-contrast")
    case["canonical_expectations"] = {"acceptable_plans": []}
    case["notes"] = ["An unrelated diagnostic note that must not matter."]
    request = adapter.build_request(case)
    assert request == request_for("four-move-plan-order-contrast")


def test_candidate_lifecycle_definitions_are_constructed_but_never_selected() -> None:
    constructed_candidates = set()
    for case in documents()[1]["cases"]:
        result = result_for(case["case_id"])
        for step in result.steps:
            for candidate, gate in zip(step.constructed_candidates, step.gate_results):
                definition = DEFAULT_REGISTRY.resolve(candidate.definition_ref)
                if definition.lifecycle == ActionLifecycle.CANDIDATE:
                    constructed_candidates.add(definition.definition_ref.action_key)
                    assert gate.status.value == "blocked"
                    assert gate.violations[0].code == "lifecycle_candidate_not_selectable"
        assert all(key in SHADOW_KEYS for key in (move.definition_ref.action_key for move in result.plan.moves))
    assert constructed_candidates >= {
        "give_feedback.confirm_understanding", "support_socially.comfort", "provide_information.inform",
        "commit_or_coordinate.promise", "express_stance.agree", "advise_or_influence.set_boundary",
    }


def test_reviewed_superclass_bindings_are_recorded() -> None:
    step = result_for("out-of-scope-coined-command").steps[0]
    clarify = {item.target_refs[0].ref: item for item in candidate_by_key(step, "seek_information.clarify")}
    assert clarify["frame-1#predicate"].binding_fit == "1.000000"
    assert clarify["frame-1#object_reference"].binding_fit == "0.750000"
    assert clarify["frame-1#object_reference"].binding_fit_rule_id == "action-fit.reviewed-superclass.v1"
    greeting = candidate_by_key(result_for("greet-and-introduce-topic").steps[0], "give_feedback.acknowledge")
    assert {item.target_refs[0].ref: item.binding_fit for item in greeting}["hyp-1"] == "0.750000"
