"""Blocker B: the application safety decision is closed, versioned, and fail-closed.

The decision is one typed record carried from the Pipeline runtime snapshot
through ``ActionRuntimeContext`` into every gate result, the action result,
and the version envelope.  Operational commitments require an explicit
``allow`` from a supported policy version; nothing computed after the gate can
override a block.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from action_v0_support import canonical_case, request_for, result_for
from cognitive_action_support import safety_decision
from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionEngine, ActionSelectionError
from app.ai.Cognitive.system.v0.action_selection_v0 import engine as engine_module
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFAULT_REGISTRY
from app.ai.Cognitive.system.v0.contracts.actions import (
    COMMITMENT_EFFECT_KEYS, ActionFailurePhase, ActionGateStatus, ActionPersonalityProfile, ActionRuntimeContext,
    ActionSafetyBasis, ActionSelectionRequest, ActionSelectionResult, ActionVersions,
)
from app.ai.Cognitive.system.v0.contracts.safety import (
    APPLICATION_SAFETY_DECISION_SCHEMA_VERSION, APPLICATION_SAFETY_POLICY_VERSION, ApplicationSafetyDecision,
    SafetyDecisionProvenance, SafetyDisposition, SafetyRestriction, SafetyRestrictionReason, legacy_safety_label,
    safety_restriction,
)

ACCEPT = "accept-summary-request"
COMMITMENT_KEYS = {"commit_or_coordinate.accept_request", "commit_or_coordinate.state_next_step"}


def _is_commitment(candidate) -> bool:
    return DEFAULT_REGISTRY.resolve(candidate.definition_ref).effect.effect_key in COMMITMENT_EFFECT_KEYS


def _run(case_id: str, decision) -> ActionSelectionResult:
    request = request_for(case_id)
    runtime = request.runtime.model_copy(update={"safety_decision": decision})
    return ActionSelectionEngine().select(ActionSelectionRequest.model_validate(
        {**request.model_dump(mode="python"), "runtime": ActionRuntimeContext.model_validate(runtime.model_dump(mode="python"))}))


def _decision(disposition, **updates) -> ApplicationSafetyDecision:
    snapshot = request_for(ACCEPT).runtime.runtime_snapshot_ref
    base = safety_decision(snapshot, disposition)
    return ApplicationSafetyDecision.model_validate({**base.model_dump(mode="python"), **updates})


def _moves(result) -> set[str]:
    return {move.definition_ref.action_key for move in result.plan.moves}


# -- the closed contract --------------------------------------------------------

def test_dispositions_and_provenance_are_closed_vocabularies() -> None:
    assert {item.value for item in SafetyDisposition} == {"allow", "block_commitments", "block_response", "unknown", "unavailable"}
    base = _decision("allow").model_dump(mode="python")
    for field, value in (("disposition", "blocked"), ("disposition", "deny"), ("provenance", "user_message"),
                         ("schema_version", "application-safety-decision-v9"), ("decision_id", " ")):
        with pytest.raises(ValidationError):
            ApplicationSafetyDecision.model_validate({**base, field: value})
    with pytest.raises(ValidationError):
        ApplicationSafetyDecision.model_validate({**base, "surprise": True})


@pytest.mark.parametrize("disposition", ("allow", "block_commitments", "block_response"))
def test_only_the_trusted_policy_can_allow_or_block(disposition: str) -> None:
    base = _decision(disposition).model_dump(mode="python")
    with pytest.raises(ValidationError):
        ApplicationSafetyDecision.model_validate({**base, "provenance": "runtime_unavailable"})


def test_a_runtime_unavailable_record_carries_only_unavailable() -> None:
    assert _decision("unavailable").provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE
    base = _decision("unavailable").model_dump(mode="python")
    with pytest.raises(ValidationError):
        ApplicationSafetyDecision.model_validate({**base, "disposition": "unknown"})


def test_the_restriction_mapping_is_total_and_fails_closed() -> None:
    unsupported = "application-safety-policy-v9"
    expected = {
        (None, None): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_MISSING),
        ("allow", None): (SafetyRestriction.NONE, SafetyRestrictionReason.ALLOWED),
        ("block_commitments", None): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.BLOCKS_COMMITMENTS),
        ("block_response", None): (SafetyRestriction.RESPONSE, SafetyRestrictionReason.BLOCKS_RESPONSE),
        ("unknown", None): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_UNKNOWN),
        ("unavailable", None): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.DECISION_UNAVAILABLE),
        ("allow", unsupported): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED),
        ("unknown", unsupported): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED),
        # A block is never weakened because its producer version is unsupported.
        ("block_response", unsupported): (SafetyRestriction.RESPONSE, SafetyRestrictionReason.BLOCKS_RESPONSE),
        ("block_commitments", unsupported): (SafetyRestriction.COMMITMENTS, SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED),
    }
    for (disposition, version), outcome in expected.items():
        decision = None if disposition is None else _decision(disposition, **({"policy_version": version} if version else {}))
        assert safety_restriction(decision) == outcome, (disposition, version)


def test_the_upstream_legacy_projection_is_deterministic() -> None:
    labels = {item.value: legacy_safety_label(_decision(item)) for item in SafetyDisposition}
    assert labels == {"allow": None, "block_commitments": "blocked", "block_response": "blocked",
                      "unknown": "unknown", "unavailable": None}
    assert legacy_safety_label(None) is None


def test_a_decision_must_belong_to_the_runtime_snapshot_it_is_consumed_with() -> None:
    runtime = request_for(ACCEPT).runtime
    foreign = _decision("allow", runtime_snapshot_ref="runtime:someone-else")
    with pytest.raises(ValidationError, match="different runtime snapshot"):
        ActionRuntimeContext.model_validate({**runtime.model_dump(mode="python"), "safety_decision": foreign})


def test_versions_envelope_names_the_safety_schema_and_supported_policy() -> None:
    versions = ActionVersions()
    assert (versions.safety_decision_schema, versions.safety_policy) == (
        APPLICATION_SAFETY_DECISION_SCHEMA_VERSION, APPLICATION_SAFETY_POLICY_VERSION)


# -- every gate result and the result retain the decision -------------------------

def test_every_gate_result_retains_the_versioned_decision_and_its_provenance() -> None:
    result = result_for(ACCEPT)
    decision = request_for(ACCEPT).runtime.safety_decision
    expected = ActionSafetyBasis.from_decision(decision, request_for(ACCEPT).runtime.runtime_snapshot_ref)
    assert result.safety == expected
    assert (expected.decision_ref, expected.decision_schema_version, expected.policy_version, expected.provenance,
            expected.producer_ref, expected.restriction) == (
        decision.decision_id, APPLICATION_SAFETY_DECISION_SCHEMA_VERSION, APPLICATION_SAFETY_POLICY_VERSION,
        SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY, decision.producer_ref, SafetyRestriction.NONE)
    gates = [gate for step in result.steps for gate in step.gate_results]
    assert gates and all(gate.safety == expected for gate in gates)


def test_a_forged_restriction_is_rejected() -> None:
    basis = result_for(ACCEPT).safety.model_dump(mode="python")
    for forged in ({"restriction": "none", "disposition": "block_commitments"},
                   {"reason": "allowed", "restriction": "commitments"},
                   {"decision_present": False}):
        with pytest.raises(ValidationError):
            ActionSafetyBasis.model_validate({**basis, **forged})


def test_a_trace_cannot_swap_in_a_different_decision() -> None:
    payload = copy.deepcopy(result_for(ACCEPT).model_dump(mode="json"))
    other = ActionSafetyBasis.from_decision(None, request_for(ACCEPT).runtime.runtime_snapshot_ref).model_dump(mode="json")
    payload["safety"] = other
    with pytest.raises(ValidationError):
        ActionSelectionResult.model_validate(payload)


# -- fail-closed behaviour -----------------------------------------------------

def test_an_explicit_allow_permits_the_commitment() -> None:
    assert COMMITMENT_KEYS & _moves(result_for(ACCEPT))


@pytest.mark.parametrize("decision_factory,code", (
    (lambda: None, "application_safety_decision_missing"),
    (lambda: _decision("unknown"), "application_safety_decision_unknown"),
    (lambda: _decision("unavailable"), "application_safety_decision_unavailable"),
    (lambda: _decision("allow", policy_version="application-safety-policy-v9"), "application_safety_policy_version_unsupported"),
    (lambda: _decision("block_commitments"), "application_safety_blocks_operation"),
))
def test_commitments_fail_closed_without_a_supported_allow(decision_factory, code: str) -> None:
    result = _run(ACCEPT, decision_factory())
    assert result.safety.restriction == SafetyRestriction.COMMITMENTS
    for step in result.steps:
        for candidate, gate in zip(step.constructed_candidates, step.gate_results):
            safety_codes = {item.code for item in gate.violations if item.category.value == "application_safety"}
            if _is_commitment(candidate):
                assert gate.status == ActionGateStatus.BLOCKED and code in safety_codes
                assert gate.safety_decision_ref == result.safety.decision_ref
                assert candidate.candidate_id not in {item.candidate_ref for item in step.scored_candidates}
            else:
                assert not (safety_codes - {"application_policy_prohibits"})
    assert not COMMITMENT_KEYS & _moves(result)
    assert result.plan.moves, "a commitment restriction still permits a conversational response"


@pytest.mark.parametrize("policy_version", (APPLICATION_SAFETY_POLICY_VERSION, "application-safety-policy-v9"))
def test_a_blocked_response_blocks_every_move_under_any_policy_version(policy_version: str) -> None:
    with pytest.raises(ActionSelectionError) as caught:
        _run(ACCEPT, _decision("block_response", policy_version=policy_version))
    assert (caught.value.failed_phase, caught.value.code) == (ActionFailurePhase.PLAN_VALIDATION, "no_viable_plan")
    case = canonical_case(ACCEPT)
    case["canonical_inputs"]["runtime"].update(response_obligation="not_required",
                                               safety_decision={"disposition": "block_response", "policy_version": policy_version})
    result = adapter.run_case(case)
    assert result.plan.moves == ()
    for step in result.steps:
        assert all(gate.status == ActionGateStatus.BLOCKED and "application_safety_blocks_response" in {
            item.code for item in gate.violations} for gate in step.gate_results)
        assert step.scored_candidates == ()


# -- nothing downstream of the gate can override a block ---------------------------

def test_score_state_personality_goals_and_outcomes_cannot_override_a_block(monkeypatch) -> None:
    """Blocked candidates are never handed to features or scoring.

    Every later influence (score, partial-plan state, personality, goals,
    prior outcomes) enters through feature extraction or scoring, so a spy on
    both proves none of them ever sees a blocked commitment.
    """

    seen: list[str] = []
    real_score = engine_module.score_candidate

    def spy_score(candidate, features):
        seen.append(candidate.definition_ref.action_key)
        return real_score(candidate, features)

    monkeypatch.setattr(engine_module, "score_candidate", spy_score)
    analyzed: list[str] = []

    class SpyCalculator(engine_module.FeatureCalculator):
        def analyze(self, candidate, state):
            analyzed.append(candidate.definition_ref.action_key)
            return super().analyze(candidate, state)

    engine = ActionSelectionEngine(feature_factory=SpyCalculator)
    request = request_for(ACCEPT)
    warm = ActionPersonalityProfile(profile_id="p", mapper_version="m", source_core_memory_ref=request.core_memory_ref,
                                    status="known", curiosity=1.0, warmth=1.0, playfulness=1.0, directness=1.0)
    blocked = request.model_copy(update={
        "runtime": request.runtime.model_copy(update={"safety_decision": _decision("block_commitments")}),
        "personality": warm,
    })
    result = engine.select(ActionSelectionRequest.model_validate(blocked.model_dump(mode="python")))
    assert seen and analyzed
    assert not COMMITMENT_KEYS & (set(seen) | set(analyzed))
    assert not COMMITMENT_KEYS & _moves(result)


def test_a_trace_cannot_record_a_restricted_commitment_as_eligible() -> None:
    result = _run(ACCEPT, _decision("block_commitments"))
    payload = copy.deepcopy(result.model_dump(mode="json"))
    step = payload["steps"][0]
    candidate = next(item for item in step["constructed_candidates"] if item["definition_ref"]["action_key"] in COMMITMENT_KEYS)
    gate = next(item for item in step["gate_results"] if item["candidate_ref"] == candidate["candidate_id"])
    gate["violations"] = [item for item in gate["violations"] if item["category"] != "application_safety"]
    if not gate["violations"]:
        gate["status"] = "shadow_only"
    with pytest.raises(ValidationError, match="safety"):
        ActionSelectionResult.model_validate(payload)


def test_the_trusted_decision_reaches_the_gate_unchanged_through_cognitive_turn_and_pipeline() -> None:
    from test_action_v0_pipeline_integration import _request as pipeline_request

    request = pipeline_request()
    runtime = request.runtime
    assert isinstance(runtime.safety_decision, ApplicationSafetyDecision)
    assert runtime.to_action_runtime().safety_decision == runtime.safety_decision
    assert runtime.to_appraisal_runtime().safety_decision == legacy_safety_label(runtime.safety_decision)
    assert runtime.to_interpretation_runtime().safety_decision == legacy_safety_label(runtime.safety_decision)
    foreign = runtime.safety_decision.model_copy(update={"runtime_snapshot_ref": "runtime:someone-else"})
    with pytest.raises(ValidationError, match="different runtime snapshot"):
        type(runtime).model_validate({**runtime.model_dump(mode="python"), "safety_decision": foreign})


def test_the_cognitive_turn_rejects_action_safety_that_disagrees_with_upstream() -> None:
    from test_action_v0_cognitive_turn_integration import _request as turn_request

    request = turn_request()
    runtime = request.action_runtime
    blocked = runtime.model_copy(update={"safety_decision": safety_decision(runtime.runtime_snapshot_ref, "block_commitments")})
    with pytest.raises(ValidationError, match="authority facts"):
        type(request).model_validate({**request.model_dump(mode="python"), "action_runtime": blocked})


def test_every_design_case_consumes_an_explicit_allow() -> None:
    from action_v0_support import documents

    for case in documents()[1]["cases"]:
        assert case["canonical_inputs"]["runtime"]["safety_decision"] == {"disposition": "allow"}
        assert result_for(case["case_id"]).safety.restriction == SafetyRestriction.NONE
