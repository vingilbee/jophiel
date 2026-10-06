"""Adversarial mutations: a trace validates only if it is the decision the policy made."""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from action_v0_support import documents, index_for, result_for
from app.ai.Cognitive.system.v0.contracts.actions import ActionSelectionResult, canonical_decimal

CASE = "missing-recipient-open-clarification"
MULTI = "repair-after-explicit-correction-stale-memory"


def _payload(case_id: str = CASE) -> dict:
    return copy.deepcopy(result_for(case_id).model_dump(mode="json"))


def _rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ActionSelectionResult.model_validate(payload)


def test_unmodified_traces_validate() -> None:
    for case_id in (CASE, MULTI, "preference-scale-question"):
        assert ActionSelectionResult.model_validate(_payload(case_id)) == result_for(case_id)


def test_forged_selection_of_a_lower_scoring_candidate_is_rejected() -> None:
    payload = _payload()
    step = payload["steps"][0]
    selected = step["selected_candidate_ref"]
    scores = {item["candidate_ref"]: Decimal(item["score"]["score"]) for item in step["scored_candidates"]}
    forged_ref = next(ref for ref, score in sorted(scores.items(), key=lambda item: item[1]) if ref != selected and score > 0)
    assert scores[forged_ref] < scores[selected]
    forged = next(item for item in step["constructed_candidates"] if item["candidate_id"] == forged_ref)
    step["selected_candidate_ref"] = forged_ref
    step["tie_resolution"] = None
    for move in (payload["plan"]["moves"][0], payload["moves"][0]):
        move.update({
            "candidate_ref": forged_ref, "definition_ref": forged["definition_ref"],
            "family": forged["definition_ref"]["action_key"].partition(".")[0],
            "target_refs": forged["target_refs"], "parameters": forged["parameters"],
            "supporting_evidence_refs": forged["supporting_evidence_refs"], "selection_score": canonical_decimal(scores[forged_ref]),
        })
    _rejected(payload)


def _mutate_both_move_copies(payload: dict, mutation) -> None:
    mutation(payload["plan"]["moves"][0])
    mutation(payload["moves"][0])


def test_move_family_must_equal_the_selected_definition_family() -> None:
    payload = _payload("check-understanding-paraphrase")
    _mutate_both_move_copies(payload, lambda move: move.update(family="social_play"))
    _rejected(payload)


@pytest.mark.parametrize("field", ("supporting_evidence_refs", "realization_constraints"))
@pytest.mark.parametrize("mutation", ("modify", "remove", "reorder", "add"))
def test_move_provenance_metadata_must_exactly_match_its_candidate_and_gate(field: str, mutation: str) -> None:
    payload = _payload("check-understanding-paraphrase")

    def forge(move: dict) -> None:
        values = move[field]
        assert len(values) >= 2
        if mutation == "modify":
            values[0]["ref" if field == "supporting_evidence_refs" else "subject_ref"] = "forged-ref"
        elif mutation == "remove":
            values.pop()
        elif mutation == "reorder":
            values.reverse()
        elif field == "supporting_evidence_refs":
            values.append({"role": "locator", "ref": "forged-ref", "source_kind": "interpretation"})
        else:
            values.append({"code": "forged-constraint", "subject_ref": "forged-ref", "evidence_refs": []})

    _mutate_both_move_copies(payload, forge)
    _rejected(payload)


def test_forged_state_signatures_are_rejected_throughout_the_chain() -> None:
    payload = _payload()
    signature = payload["steps"][0]["partial_state_after"]["state_signature"]
    forged = json.loads(json.dumps(payload).replace(signature, "forged-signature"))
    _rejected(forged)
    initial = payload["steps"][0]["partial_state_before"]["state_signature"]
    _rejected(json.loads(json.dumps(payload).replace(initial, "plan-state-000000000000000000000000")))


@pytest.mark.parametrize("mutation", ("remove", "resolved_by", "tie_set", "preference"))
def test_tie_records_must_equal_the_policy_tie(mutation: str) -> None:
    payload = _payload("preference-scale-question")
    step = next(item for item in payload["steps"] if item["tie_resolution"] is not None)
    tie = step["tie_resolution"]
    if mutation == "remove":
        step["tie_resolution"] = None
    elif mutation == "resolved_by":
        tie["resolved_by"] = "response_cost" if tie["resolved_by"] != "response_cost" else "target_priority"
        tie["preference_evidence"] = True
    elif mutation == "tie_set":
        extra = next(item["candidate_ref"] for item in step["scored_candidates"] if item["candidate_ref"] not in tie["tied_candidate_refs"])
        tie["tied_candidate_refs"].append(extra)
    else:
        tie["preference_evidence"] = not tie["preference_evidence"]
    _rejected(payload)


def test_a_tie_cannot_be_invented_where_none_exists() -> None:
    payload = _payload()
    step = payload["steps"][0]
    other = next(item["candidate_ref"] for item in step["scored_candidates"] if item["candidate_ref"] != step["selected_candidate_ref"])
    step["tie_resolution"] = {"tied_candidate_refs": [step["selected_candidate_ref"], other], "resolved_by": "expected_goal_progress",
                              "selected_candidate_ref": step["selected_candidate_ref"], "preference_evidence": True}
    _rejected(payload)


@pytest.mark.parametrize("field,value", (
    ("effect_key", "feedback.receipt"),
    ("planned_state", "receipt_signal_planned"),
    ("transition_domain", "target_effect"),
    ("ledger_key", "elicitation.clarification@1|elicitation_planned|unknown:elsewhere"),
    ("target_refs", [{"role": "primary", "target_type": "user_act", "ref": "hyp-1", "revision_ref": None}]),
    ("support_refs", [{"role": "support", "target_type": "user_act", "ref": "hyp-1", "revision_ref": None}]),
    ("effect_strength", "0.500000"),
))
def test_applied_effects_must_belong_to_the_selected_definition_and_targets(field: str, value) -> None:
    payload = _payload()
    for effect in (payload["steps"][0]["applied_effects"][0], payload["planned_effects"][0]):
        effect[field] = value
    _rejected(payload)


def _resign(state: dict) -> None:
    from app.ai.Cognitive.system.v0.contracts.actions import plan_state_signature
    state["state_signature"] = plan_state_signature(state)


@pytest.mark.parametrize("mutation", ("cost", "goal_update", "after_ledger", "after_counts", "uptake"))
def test_delta_must_explain_the_after_state_exactly(mutation: str) -> None:
    payload = _payload(MULTI)
    step = payload["steps"][1]
    delta, after = step["partial_state_delta"], step["partial_state_after"]
    old_signature = after["state_signature"]
    if mutation == "cost":
        delta["added_response_cost"] = "0.900000"
    elif mutation == "goal_update":
        delta["goal_coverage_updates"][0]["value"] = "0.100000"
    elif mutation == "after_ledger":
        after["planned_goal_coverage"][0]["value"] = "0.200000"
    elif mutation == "after_counts":
        # Totals stay consistent; only the delta recomputation can detect the relabelled family.
        assert after["family_counts"][0]["key"] == "manage_dialogue"
        after["family_counts"][0]["key"] = "seek_information"
    else:
        after["uptake_coverage"] = []
    _resign(after)
    text = json.dumps(payload).replace(old_signature, after["state_signature"])
    _rejected(json.loads(text))


@pytest.mark.parametrize("domain", ("goal", "unknown", "contradiction", "uptake"))
def test_semantic_state_updates_must_be_authorized_by_the_transition_basis(domain: str) -> None:
    payload = _payload(MULTI)
    step = payload["steps"][1]
    delta, after = step["partial_state_delta"], step["partial_state_after"]
    old_signature = after["state_signature"]
    if domain == "goal":
        update = {"key": "z-forged-goal#z-forged-requirement", "value": "0.500000"}
        delta["goal_coverage_updates"].append(update)
        after["planned_goal_coverage"].append(copy.deepcopy(update))
    elif domain == "unknown":
        update = {"key": "z-forged-unknown", "state": "elicitation_planned"}
        delta["unknown_handling_updates"].append(update)
        after["planned_unknown_handling"].append(copy.deepcopy(update))
    elif domain == "contradiction":
        update = {"key": "z-forged-contradiction", "state": "exposure_planned"}
        delta["contradiction_updates"].append(update)
        after["planned_contradiction_repairs"].append(copy.deepcopy(update))
    else:
        update = {"key": "z-forged-uptake", "value": "1.000000"}
        delta["uptake_updates"].append(update)
        after["uptake_coverage"].append(copy.deepcopy(update))
    _resign(after)
    forged = json.loads(json.dumps(payload).replace(old_signature, after["state_signature"]))
    _rejected(forged)


@pytest.mark.parametrize("mutation", ("swap_primary", "compared_values", "rule"))
def test_primary_designation_is_recomputed(mutation: str) -> None:
    payload = _payload(MULTI)
    plan = payload["plan"]
    designation = plan["primary_designation"]
    moves = [item["move_id"] for item in plan["moves"]]
    if mutation == "swap_primary":
        forged = next(item for item in moves if item != plan["primary_move_ref"])
        plan["primary_move_ref"] = forged
        designation["primary_move_ref"] = forged
        plan["supporting_move_refs"] = [item for item in moves if item != forged]
        plan["move_priorities"] = [{"move_ref": item, "priority": "primary" if item == forged else "supporting"} for item in moves]
    elif mutation == "compared_values":
        designation["compared_moves"][0]["direct_focal_coverage"] = "1.000000"
    else:
        designation["rule_id"] = "action-primary.first-move.v1"
    _rejected(payload)


def test_score_terms_must_use_features_and_canonical_weights() -> None:
    payload = _payload()
    scored = payload["steps"][0]["scored_candidates"][0]
    scored["features"][2]["value"] = "0.000000" if scored["features"][2]["value"] != "0.000000" else "0.500000"
    _rejected(payload)
    payload = _payload()
    score = payload["steps"][0]["scored_candidates"][0]["score"]
    term = score["need_contributions"][0]
    term["weight"] = "5.000000"
    term["signed_contribution"] = canonical_decimal(Decimal(term["base_value"]) * 5)
    _rejected(payload)


def test_every_gate_citation_resolves_in_the_reference_index() -> None:
    for case in documents()[1]["cases"]:
        index = index_for(case["case_id"])
        for step in result_for(case["case_id"]).steps:
            for gate in step.gate_results:
                assert index.resolves(gate.safety_decision_ref), gate.safety_decision_ref
                for item in (*gate.violations, *gate.realization_constraints):
                    for ref in item.evidence_refs:
                        assert index.resolves(ref), (case["case_id"], item.code, ref)
