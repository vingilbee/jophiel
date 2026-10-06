"""The six ordered hard-gate categories and realization constraints."""

from __future__ import annotations

from action_v0_support import (
    candidate_by_key, gate_for, index_for, mutate_inputs, result_for, scored_for,
)
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.gates import HardGate, is_live_selectable
from app.ai.Cognitive.system.v0.action_selection_v0.reference_index import ActionReferenceIndex
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFAULT_REGISTRY, DEFINITIONS, ActionRegistry
from app.ai.Cognitive.system.v0.contracts.actions import (
    ActionDefinitionRef, ActionGateCategory as C, ActionGateStatus, ActionLifecycle, ActionTargetRef, ActionTargetType,
    FeatureKey,
)

GATE = HardGate(DEFAULT_REGISTRY)


def _codes(result) -> list[tuple[str, str]]:
    return [(item.category.value, item.code) for item in result.violations]


def _gate(case_id: str, action_key: str, step: int = 0, *, ref: str | None = None):
    step_trace = result_for(case_id).steps[step]
    candidates = candidate_by_key(step_trace, action_key)
    if ref is not None:
        candidates = [item for item in candidates if any(target.ref == ref for target in item.target_refs)]
    assert candidates, (case_id, action_key)
    return candidates[0], gate_for(step_trace, candidates[0].candidate_id)


def _evaluate(case_id: str, candidate, **updates):
    return GATE.evaluate(candidate.model_copy(update=updates), index_for(case_id))


def test_registry_lifecycle_gate() -> None:
    _candidate, gate = _gate("verify-report-upload-yes-no", "give_feedback.confirm_understanding")
    assert gate.status == ActionGateStatus.BLOCKED and gate.definition_lifecycle == ActionLifecycle.CANDIDATE
    assert _codes(gate)[0] == ("registry_lifecycle", "lifecycle_candidate_not_selectable")
    clarify, _ = _gate("missing-recipient-open-clarification", "seek_information.clarify")
    unknown = _evaluate("missing-recipient-open-clarification", clarify,
                        definition_ref=ActionDefinitionRef(action_key="seek_information.clarify", definition_version=2))
    assert ("registry_lifecycle", "unknown_definition_or_version") in _codes(unknown)
    mismatch = _evaluate("missing-recipient-open-clarification", clarify, registry_version="conversational-actions-v9")
    assert ("registry_lifecycle", "registry_version_mismatch") in _codes(mismatch)
    retired = ActionRegistry(tuple(
        item.model_copy(update={"lifecycle": ActionLifecycle.RETIRED}) if item.definition_ref.action_key == "seek_information.clarify" else item
        for item in DEFINITIONS
    ))
    result = HardGate(retired).evaluate(clarify, index_for("missing-recipient-open-clarification"))
    assert ("registry_lifecycle", "lifecycle_retired_cannot_enter_plans") in _codes(result)


def test_shadow_authority_differs_from_live_authority() -> None:
    clarify, gate = _gate("missing-recipient-open-clarification", "seek_information.clarify")
    assert gate.status == ActionGateStatus.SHADOW_ONLY and gate.eligible_in_shadow and not is_live_selectable(gate)
    approved = ActionRegistry(tuple(
        item.model_copy(update={"lifecycle": ActionLifecycle.APPROVED}) if item.definition_ref.action_key == "seek_information.clarify" else item
        for item in DEFINITIONS
    ))
    live = HardGate(approved).evaluate(clarify, index_for("missing-recipient-open-clarification"))
    assert live.status == ActionGateStatus.PASSED and is_live_selectable(live)


def test_schema_and_reference_integrity_gate() -> None:
    clarify, _ = _gate("missing-recipient-open-clarification", "seek_information.clarify")
    case = "missing-recipient-open-clarification"
    checks = {
        "unknown_parameter": {"parameters": {**clarify.parameters, "tone": "warm"}},
        "missing_required_parameter": {"parameters": {}},
        "parameter_value_not_allowed": {"parameters": {"question_form": "essay"}},
        "choice_requires_two_candidate_answers": {"parameters": {"question_form": "choice", "candidate_answer_refs": ["request-send-report"]}},
        "target_type_not_accepted": {"target_refs": (ActionTargetRef(role="primary", target_type=ActionTargetType.MESSAGE, ref="m1"),)},
        "target_does_not_resolve": {"target_refs": (ActionTargetRef(role="primary", target_type=ActionTargetType.SEMANTIC_ARGUMENT, ref="frame-1#missing"),)},
    }
    for code, update in checks.items():
        assert code in {item.code for item in _evaluate(case, clarify, **update).violations}, code
    scale, _ = _gate("preference-scale-question", "seek_information.explore_preference")
    reversed_scale = _evaluate("preference-scale-question", scale, parameters={"question_form": "scale", "scale_min": 5, "scale_max": 0})
    assert "scale_requires_ordered_finite_bounds" in {item.code for item in reversed_scale.violations}


def test_superseded_and_unauthorized_targets_are_blocked() -> None:
    case = mutate_inputs("decline-request-not-boundary", ("grounded_objects", 1, "status"), "superseded")
    index = ActionReferenceIndex(adapter.build_request(case))
    decline, _ = _gate("decline-request-not-boundary", "commit_or_coordinate.decline_request")
    assert "target_superseded" in {item.code for item in GATE.evaluate(decline, index).violations}
    case = mutate_inputs("decline-request-not-boundary", ("grounded_objects", 1, "authorized"), False)
    index = ActionReferenceIndex(adapter.build_request(case))
    assert "target_not_authorized" in {item.code for item in GATE.evaluate(decline, index).violations}


def test_truth_and_evidence_gate() -> None:
    _, confirm = _gate("verify-report-upload-yes-no", "give_feedback.confirm_understanding")
    assert ("truth_evidence", "confirms_unsupported_interpretation") in _codes(confirm)
    _, promise = _gate("reassure-without-promise", "commit_or_coordinate.promise")
    assert ("truth_evidence", "unsupported_outcome_guarantee") in _codes(promise)
    _, inform = _gate("unauthorized-email-and-unsupported-claim", "provide_information.inform", ref="prop-email-sent")
    assert ("truth_evidence", "asserts_contradicted_content") in _codes(inform)
    _, accept = _gate("decline-request-not-boundary", "commit_or_coordinate.accept_request")
    assert ("truth_evidence", "commits_to_false_assertion") in _codes(accept)
    case = mutate_inputs("validate-without-factual-endorsement", ("grounded_objects", 0, "expression"), "inferred")
    comfort = next(item for item in adapter.run_case(case).steps[0].constructed_candidates
                   if item.definition_ref.action_key == "support_socially.comfort")
    result = GATE.evaluate(comfort, ActionReferenceIndex(adapter.build_request(case)))
    assert ("truth_evidence", "asserts_hidden_affect") in _codes(result)


def test_uncertainty_is_preserved_by_constraints_not_blocking() -> None:
    _, paraphrase = _gate("check-understanding-paraphrase", "give_feedback.paraphrase")
    assert paraphrase.status == ActionGateStatus.SHADOW_ONLY
    assert "preserve_uncertainty" in {item.code for item in paraphrase.realization_constraints}
    _, reassure = _gate("reassure-without-promise", "support_socially.reassure")
    assert reassure.status == ActionGateStatus.SHADOW_ONLY
    assert "forbid_unsupported_guarantee" in {item.code for item in reassure.realization_constraints}
    _, reflect = _gate("free-reflection-experience", "give_feedback.reflect")
    assert "forbid_hidden_affect_assertion" in {item.code for item in reflect.realization_constraints}
    _, acknowledge = _gate("acknowledge-without-agreement", "give_feedback.acknowledge")
    assert "forbid_factual_endorsement" in {item.code for item in acknowledge.realization_constraints}
    move = result_for("reassure-without-promise").plan.moves[0]
    assert "forbid_unsupported_guarantee" in {item.code for item in move.realization_constraints}


def test_capability_gate_blocks_commitment_but_not_discussion() -> None:
    _, accept = _gate("unauthorized-email-and-unsupported-claim", "commit_or_coordinate.accept_request")
    assert ("capability", "capability_unavailable") in _codes(accept)
    _, decline = _gate("unauthorized-email-and-unsupported-claim", "commit_or_coordinate.decline_request")
    assert decline.status == ActionGateStatus.SHADOW_ONLY
    _, persistence = _gate("persistent-shared-signal-candidate", "commit_or_coordinate.accept_request")
    assert ("capability", "capability_unavailable") in _codes(persistence)


def test_authorization_consent_and_privacy_gate() -> None:
    _, accept = _gate("unauthorized-email-and-unsupported-claim", "commit_or_coordinate.accept_request")
    codes = {item.code for item in accept.violations if item.category == C.AUTHORIZATION_CONSENT_PRIVACY}
    assert {"authorization_missing", "private_disclosure_not_permitted"} <= codes
    _, suggestion = _gate("decline-user-suggestion", "commit_or_coordinate.accept_suggestion")
    assert ("authorization_consent_privacy", "consent_absent") in _codes(suggestion)
    _, stale = _gate("repair-after-explicit-correction-stale-memory", "provide_information.inform", ref="prop-pine-street-current")
    assert ("authorization_consent_privacy", "private_disclosure_not_permitted") in _codes(stale)


def test_application_safety_cannot_be_overridden() -> None:
    _, accept = _gate("decline-request-not-boundary", "commit_or_coordinate.accept_request")
    assert ("application_safety", "application_policy_prohibits") in _codes(accept)
    blocked = adapter.run_case(mutate_inputs("accept-summary-request", ("runtime", "safety_decision"), {"disposition": "block_commitments"}))
    step = blocked.steps[0]
    for candidate, gate in zip(step.constructed_candidates, step.gate_results):
        effect = DEFAULT_REGISTRY.resolve(candidate.definition_ref).effect.effect_key
        if effect in {"coordination.request_acceptance", "coordination.next_step"}:
            assert ("application_safety", "application_safety_blocks_operation") in _codes(gate)
    assert not {"commit_or_coordinate.accept_request", "commit_or_coordinate.state_next_step"} & {
        move.definition_ref.action_key for move in blocked.plan.moves
    }
    assert blocked.plan.moves, "a blocked operation still permits a conversational response"


def test_every_applicable_violation_is_retained_in_category_order() -> None:
    _, accept = _gate("unauthorized-email-and-unsupported-claim", "commit_or_coordinate.accept_request")
    categories = [item.category for item in accept.violations]
    assert set(categories) == {C.CAPABILITY, C.AUTHORIZATION_CONSENT_PRIVACY, C.APPLICATION_SAFETY}
    order = list(C)
    assert categories == sorted(categories, key=order.index)
    assert accept.categories_evaluated == tuple(C)


def test_ordinary_awkwardness_is_never_a_hard_violation() -> None:
    step = result_for("playful-reframe-serious-repeated-pressure").steps[0]
    humor = candidate_by_key(step, "social_play.playful_reframe")[0]
    gate = gate_for(step, humor.candidate_id)
    assert gate.status == ActionGateStatus.SHADOW_ONLY and not gate.violations
    assert scored_for(step, humor.candidate_id).feature(FeatureKey.SOFT_RISK_COST) == 1
    advice = result_for("suggestion-advice-warning-force").steps[0]
    strong = next(item for item in candidate_by_key(advice, "advise_or_influence.advise") if item.parameters["guidance_strength"] == "high")
    assert gate_for(advice, strong.candidate_id).status == ActionGateStatus.SHADOW_ONLY
    assert scored_for(advice, strong.candidate_id).feature(FeatureKey.SOFT_RISK_COST) > 0
