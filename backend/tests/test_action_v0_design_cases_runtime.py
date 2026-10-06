"""All 29 conversational-action design cases through the independent adapter."""

from __future__ import annotations

import copy
import json

import pytest

from action_v0_support import canonical_case, documents, fixture_case, request_for, result_for
from app.ai.Cognitive.system.v0.action_selection_v0 import fixture_adapter as adapter
from app.ai.Cognitive.system.v0.action_selection_v0.registry import DEFINITIONS, SHADOW_KEYS

CASE_IDS = [item["case_id"] for item in json.loads(adapter.CANONICAL_PATH.read_text(encoding="utf-8"))["cases"]]


def _expected() -> dict:
    return json.loads(adapter.RESULTS_PATH.read_text(encoding="utf-8"))


def test_fixture_shape_and_complete_adapter_run() -> None:
    fixture, canonical = documents()
    assert len(fixture["cases"]) == len(canonical["cases"]) == 29
    report = adapter.run_fixture()
    assert report["passed"], report["errors"]
    assert report["cases_run"] == 29 and report["failure_variants_run"] == 7
    assert report["planning_steps"] == 81 and report["invariance_checks_run"] == 58
    assert all(item["matched_plan"] for item in report["outcomes"])


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_selects_a_declared_acceptable_plan(case_id: str) -> None:
    result = result_for(case_id)
    assert not adapter.check_translation(fixture_case(case_id), canonical_case(case_id))
    matched = adapter.matching_plan(result, canonical_case(case_id))
    assert matched is not None
    fixture_keys = {plan["plan_id"]: [move["action_key"] for move in plan["moves"]] for plan in fixture_case(case_id)["acceptable_plans"]}
    assert [move.definition_ref.action_key for move in result.plan.moves] == fixture_keys[matched]
    priorities = [move["priority"] for plan in fixture_case(case_id)["acceptable_plans"] if plan["plan_id"] == matched for move in plan["moves"]]
    primary = result.plan.primary_move_ref
    assert ["primary" if move.move_id == primary else "supporting" for move in result.plan.moves] == priorities


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_complete_decision_trace_matches_reviewed_expectations(case_id: str) -> None:
    expected = next(item for item in _expected()["cases"] if item["case_id"] == case_id)
    result = result_for(case_id)
    assert adapter.matching_plan(result, canonical_case(case_id)) == expected["matched_plan"]
    assert adapter.compare_expected(result, expected) == ()


def test_expected_results_document_covers_every_case_with_installed_versions() -> None:
    document = _expected()
    assert [item["case_id"] for item in document["cases"]] == CASE_IDS
    assert document["action_versions"] == result_for(CASE_IDS[0]).versions.model_dump(mode="json")


def test_destroying_expected_plans_does_not_change_engine_inputs_or_outputs(tmp_path) -> None:
    fixture, canonical = copy.deepcopy(documents()[0]), copy.deepcopy(documents()[1])
    for case in fixture["cases"]:
        for key in ("acceptable_plans", "disfavored_plans", "hard_invalid_plans"):
            case[key] = [{"plan_id": "destroyed", "moves": [{"move_id": "x", "action_key": "social_play.joke", "priority": "primary"}]}]
    for case in canonical["cases"]:
        case["canonical_expectations"] = {"acceptable_plans": []}
    for case_id in ("free-reflection-experience", "decline-user-suggestion", "uncertain-credit-multimove-response"):
        mutated = next(item for item in canonical["cases"] if item["case_id"] == case_id)
        assert adapter.build_request(mutated) == request_for(case_id)
        assert adapter.run_case(mutated) == result_for(case_id)


def test_diagnostic_prose_never_changes_policy_output() -> None:
    for case_id in ("repair-after-explicit-correction-stale-memory", "evidence-grounded-correction", "verify-report-upload-yes-no"):
        case = canonical_case(case_id)
        case["notes"] = ["Replaced reviewer note."]
        assert adapter.build_request(case) == request_for(case_id)
        for contradiction in case["canonical_inputs"]["interpretation"].get("contradictions", ()):
            contradiction["description"] = "Entirely different diagnostic wording."
        mutated = adapter.run_case(case)
        assert adapter.semantic_projection(mutated) == adapter.semantic_projection(result_for(case_id))


def test_schema_v1_prose_and_relationship_placeholders_are_never_consumed() -> None:
    assert adapter.V1_CONSUMED_FOR_COMPARISON == {"case_id", "acceptable_plans"}
    assert {"relationship_context", "messages", "interpretation_input", "core_action_affinities",
            "hard_runtime_context", "disfavored_plans", "hard_invalid_plans"} <= adapter.V1_UNCONSUMED_SHORTHAND
    for case in documents()[1]["cases"]:
        text = json.dumps(case["canonical_inputs"])
        assert "relationship" not in text and "rationale" not in text


def test_every_failure_variant_fails_closed_at_its_declared_phase() -> None:
    canonical = documents()[1]
    for variant in canonical["failure_variants"]:
        error = adapter.run_failure_variant(canonical, variant)
        assert error is not None, variant["variant_id"]
        assert (error.failed_phase.value, error.code) == (variant["failure_phase"], variant["code"])
        assert error.__cause__ is not None


def test_shared_signal_is_handled_by_existing_moves_without_persistence() -> None:
    assert not any("shared_signal" in item.definition_ref.action_key for item in DEFINITIONS)
    result = result_for("persistent-shared-signal-candidate")
    assert [move.definition_ref.action_key for move in result.plan.moves] == ["give_feedback.acknowledge", "seek_information.clarify"]
    accept = [
        gate for step in result.steps for candidate, gate in zip(step.constructed_candidates, step.gate_results)
        if candidate.definition_ref.action_key == "commit_or_coordinate.accept_request"
    ]
    assert accept and all("capability_unavailable" in {item.code for item in gate.violations} for gate in accept)


def test_selected_moves_are_always_shadow_definitions() -> None:
    for case_id in CASE_IDS:
        assert {move.definition_ref.action_key for move in result_for(case_id).plan.moves} <= SHADOW_KEYS
