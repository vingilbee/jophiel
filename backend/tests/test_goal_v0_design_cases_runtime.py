from copy import deepcopy

from app.ai.Cognitive.system.v0.goal_v0.engine import GoalEngine
from app.ai.Cognitive.system.v0.goal_v0.fixture_adapter import (
    _fixture_identity,
    build_interpretation,
    build_request,
    fixture_definitions_equal,
    load_design_fixture,
    run_fixture,
)

def test_fixture_registry_parity():
    fixture = load_design_fixture("app/ai/Cognitive/system/v0/data/goal_design_cases.json")
    assert len(fixture["cases"]) == 36
    assert fixture_definitions_equal(fixture)

def test_all_design_cases_execute_and_match():
    report = run_fixture("app/ai/Cognitive/system/v0/data/goal_design_cases.json")
    assert report["cases_run"] == 36
    assert report["update_steps_run"] == 25
    assert report["failure_variants_run"] == 15
    assert report["invariance_variants_run"] == 2
    assert report["errors"] == ()
    assert report["passed"] is True


def test_fixture_adapter_does_not_invent_interpretation_records():
    fixture = load_design_fixture("app/ai/Cognitive/system/v0/data/goal_design_cases.json")
    for case in fixture["cases"]:
        built = build_interpretation(case)
        raw = case["interpretation_input"]
        assert tuple(item.frame_id for item in built.semantic_frames) == tuple(
            item["frame_id"] for item in raw["semantic_frames"]
        )
        assert tuple(item.hypothesis_id for item in built.hypotheses) == tuple(
            item["hypothesis_id"] for item in raw["hypotheses"]
        )
        assert tuple(item.scenario_id for item in built.scenarios) == tuple(
            item["scenario_id"] for item in raw["scenarios"]
        )


def test_expected_outputs_never_become_engine_inputs():
    fixture = load_design_fixture("app/ai/Cognitive/system/v0/data/goal_design_cases.json")
    case = deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "suspend-resume"))
    step = case["update_steps"][0]
    original = build_request(case, step=step)
    step["expected_goal_update"] = {"deliberately": "unusable"}
    assert build_request(case, step=step) == original


def test_diagnostic_evidence_text_never_changes_policy_output():
    fixture = load_design_fixture("app/ai/Cognitive/system/v0/data/goal_design_cases.json")
    case = deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "alternate-route"))
    step = case["update_steps"][0]
    identity = lambda kind, parts: _fixture_identity(case, kind, parts)
    original = GoalEngine(identity_factory=identity).update(build_request(case, step=step))
    step["new_goal_evidence"][0]["factual_meaning"] = "Arbitrary diagnostic prose with opposite wording."
    changed = GoalEngine(identity_factory=identity).update(build_request(case, step=step))
    assert changed == original
