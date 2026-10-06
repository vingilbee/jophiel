"""Realization V0 domain-general design cases (section 19.7), run through the real stages."""

from __future__ import annotations

import pytest

from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompileSkip
from realization_v0_support import (
    case_inputs, compile_request, design_cases, design_document, known_personality, live_inputs, validate_text,
)

CASES = design_cases()


def _inputs(case: dict, personality=None) -> dict:
    source = case["plan_source"]
    if "live_message" in source:
        return live_inputs(source["live_message"])
    return case_inputs(source["action_case"], case["content_items"], personality=personality)


def _moves(spec):
    return {int(key): value for key, value in spec.items()} if spec else None


def test_fixture_is_a_reviewed_draft_covering_every_required_item() -> None:
    document = design_document()
    assert document["status"] == "draft_for_human_review"
    assert document["schema_version"] == "realization-design-cases-v0.1"
    covered = {item for case in CASES for item in case["coverage"]}
    assert covered == set(range(1, 20)) == {int(key) for key in document["required_coverage"]}
    assert {case["domain"] for case in CASES} == {"personal", "practical", "social", "factual"}
    assert len({case["case_id"] for case in CASES}) == len(CASES)
    for case in CASES:
        # No case depends on the nutrition domain for its meaning.
        for item in case["content_items"]:
            # Upstream ref names come from the Action fixtures; the worded meaning must not.
            meaning = str([item.get("surface_anchors"), item.get("subject"), item.get("arguments"),
                           item.get("surface_anchor"), item.get("quote_text")]).lower()
            assert "nutrition" not in meaning and "protein" not in meaning


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_design_case(case: dict) -> None:
    inputs = _inputs(case)
    expected = case["expected"]
    if "action_keys" in expected:
        assert [move.definition_ref.action_key for move in inputs["action_result"].plan.moves] == expected["action_keys"]
    request = compile_request(inputs)
    if expected["compile"] != "request":
        assert isinstance(request, RealizationCompileSkip) and request.status.value == expected["compile"]
        return
    assert not isinstance(request, RealizationCompileSkip), getattr(request, "skip", None)
    codes = {item.code for item in request.constraints}
    assert set(expected.get("constraints_include", ())) <= codes
    candidate = case["candidate"]
    result = validate_text(request, candidate["text"], _moves(candidate.get("move_excerpts")),
                           candidate.get("content_excerpts"))
    assert result.status == expected["status"], [(v.code.value, v.subject_ref) for v in result.violations]
    for mutation in case["mutations"]:
        mutated = validate_text(request, mutation["text"], _moves(mutation.get("move_excerpts")),
                                mutation.get("content_excerpts"))
        found = {item.code.value for item in mutated.violations}
        assert mutated.status == "rejected", mutation["mutation_id"]
        assert set(mutation["expected_codes"]) <= found, (mutation["mutation_id"], sorted(found))


def test_meaning_is_identical_under_personality_extremes() -> None:
    case = next(item for item in CASES if "personality_variants" in item)
    requests = []
    for variant in case["personality_variants"]:
        base = case_inputs(case["plan_source"]["action_case"], case["content_items"])
        personality = known_personality(base["personality"].source_core_memory_ref, **variant)
        requests.append(compile_request(case_inputs(case["plan_source"]["action_case"], case["content_items"],
                                                    personality=personality)))
    invariant = [
        (request.action_plan, request.content_items, request.move_bindings, request.constraints,
         request.discourse_controls.move_order)
        for request in requests
    ]
    assert all(item == invariant[0] for item in invariant)
    assert len({request.delivery_profile.controls for request in requests}) == len(requests)
    for request in requests:
        result = validate_text(request, case["candidate"]["text"], _moves(case["candidate"]["move_excerpts"]))
        assert result.status == "accepted_for_shadow_review"
