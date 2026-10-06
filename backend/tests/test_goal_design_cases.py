"""Acceptance invariants for the reviewed Goal System V0 design fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE = (
    Path(__file__).parents[1]
    / "app/ai/Cognitive/system/v0/data/goal_design_cases.json"
)


def _document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _case(case_id: str) -> dict[str, Any]:
    return next(case for case in _document()["cases"] if case["case_id"] == case_id)


def _updates(case: dict[str, Any]) -> list[dict[str, Any]]:
    if "expected_goal_update" in case:
        return [case["expected_goal_update"]]
    return [step["expected_goal_update"] for step in case.get("update_steps", ())]


def test_predecessor_snapshots_have_self_consistent_focus() -> None:
    for case in _document()["cases"]:
        snapshot = case.get("previous_goal_snapshot")
        if snapshot is None:
            continue
        active = {
            record["goal_id"]
            for record in snapshot["records"]
            if record["status"] == "active"
        }
        focus = snapshot["focus_decision"]
        focal = [focus["primary_goal_ref"], *focus["supporting_goal_refs"]]

        assert set(snapshot["active_goal_refs"]) == active, case["case_id"]
        assert set(focal) <= active, case["case_id"]
        assert snapshot["focal_goal_refs"] == focal, case["case_id"]
        assert set(focus["non_focal_active_goal_refs"]) == active - set(focal)


def test_interpretation_text_spans_resolve_to_exact_messages() -> None:
    for case in _document()["cases"]:
        messages = {
            message["message_id"]: message["content"]
            for message in case["conversation"]["messages"]
        }
        for evidence in case["interpretation_input"]["evidence"]:
            span = evidence.get("span")
            if span is None:
                continue
            actual = messages[span["message_id"]][span["start"]:span["end"]]
            assert actual == span["quoted_text"], (
                case["case_id"], evidence["evidence_id"]
            )


def test_active_goal_views_exactly_project_snapshot_records() -> None:
    projected_fields = (
        "goal_id", "definition_ref", "owner", "domain", "mode", "scope",
        "scope_ref", "priority_band", "requirements", "source_refs",
    )
    for case in _document()["cases"]:
        for update in _updates(case):
            records = {
                record["goal_id"]: record
                for record in update["snapshot"]["records"]
            }
            for view in update["active_goal_views"]:
                record = records[view["goal_id"]]
                assert view == {
                    field: record[field]
                    for field in projected_fields
                }, (case["case_id"], view["goal_id"])


def test_sequence_transition_history_is_byte_equivalent() -> None:
    for case in _document()["cases"]:
        previous = case.get("previous_goal_snapshot")
        for step in case.get("update_steps", ()):
            update = step["expected_goal_update"]
            stored = {
                transition["transition_id"]: transition
                for transition in update["snapshot"]["transitions"]
            }
            for transition in update["transitions"]:
                assert stored[transition["transition_id"]] == transition
            if previous is not None:
                for transition in previous["transitions"]:
                    assert stored[transition["transition_id"]] == transition
            previous = update["snapshot"]


def test_disputed_assessment_conflicts_resolve_in_same_update() -> None:
    for case in _document()["cases"]:
        for step in case.get("update_steps", ()):
            update = step["expected_goal_update"]
            conflicts = {
                conflict["conflict_id"]: conflict
                for conflict in (
                    *update.get("evidence_conflicts", ()),
                    *update.get("conflicts", ()),
                )
            }
            snapshot_conflicts = {
                conflict["conflict_id"]: conflict
                for conflict in update["snapshot"].get("evidence_conflicts", ())
            }
            assert snapshot_conflicts == {
                conflict["conflict_id"]: conflict
                for conflict in update.get("evidence_conflicts", ())
            }
            evidence_ids = {
                evidence["evidence_id"]
                for evidence in step.get("new_goal_evidence", ())
            }
            for assessment in update.get("requirement_assessments", ()):
                for conflict_ref in assessment["conflict_refs"]:
                    conflict = conflicts[conflict_ref]
                    assert snapshot_conflicts[conflict_ref] == conflict
                    if "left_evidence_ref" in conflict:
                        assert conflict["left_evidence_ref"] in evidence_ids
                        assert conflict["right_evidence_ref"] in evidence_ids


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    current = document
    tokens = pointer.split("/")[1:]
    for raw_token in tokens[:-1]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current, tokens[-1].replace("~1", "/").replace("~0", "~")


def test_failure_mutations_resolve_against_embedded_local_baselines() -> None:
    case = _case("fail-closed-validation-matrix")
    assert "failure_baseline_ref" not in case
    for variant in case["failure_variants"]:
        assert "baseline_case_ref" not in variant
        assert "local_baseline_key" not in variant
        number = int(variant["variant_id"].rsplit("-", 1)[-1])
        assert variant["baseline_key"] == (
            "sequence" if number <= 2 else "single_turn"
        )
        baseline = case["failure_baseline"][variant["baseline_key"]]
        mutation = variant["input_mutation"]
        parent, token = _pointer_parent(baseline, mutation["json_pointer"])
        if mutation["operation"] == "replace":
            if isinstance(parent, list):
                assert int(token) < len(parent)
            else:
                assert token in parent
        elif isinstance(parent, list):
            assert token == "-" or int(token) <= len(parent)
        else:
            assert token not in parent


def test_outcome_references_use_one_canonical_namespace() -> None:
    for case in _document()["cases"]:
        for step in case.get("update_steps", ()):
            outcome_ids = {
                evidence["outcome_id"]
                for evidence in step.get("new_goal_evidence", ())
                if "outcome_id" in evidence
            }
            snapshot_refs = set(
                step["expected_goal_update"]["snapshot"]["input_outcome_refs"]
            )
            assert snapshot_refs == outcome_ids
            assert all("episode" not in ref for ref in snapshot_refs)
            for transition in step["expected_goal_update"]["transitions"]:
                transition_refs = set(transition["outcome_refs"])
                assert transition_refs <= outcome_ids
                assert transition_refs <= snapshot_refs
