import pytest
from app.ai.Cognitive.system.v0.appraisal.models import ActiveGoal
from app.ai.Cognitive.system.v0.contracts.goals import ActiveGoalView, GoalEvidence, GoalRecord, GoalAdoptionBasis

def test_active_goal_is_canonical_alias():
    assert ActiveGoal is ActiveGoalView

def test_unregistered_adoption_rule_rejected():
    with pytest.raises(ValueError):
        GoalRecord(goal_id="g", definition_ref={"definition_key":"interaction.discharge_response_obligation","definition_version":"v0.1-draft"}, owner="system", domain="interaction", mode="achieve", scope="turn", scope_ref="t", priority_band="moderate", status="active", adoption_basis=GoalAdoptionBasis(rule_id="bad.rule"), created_turn_ref="t", last_transition_ref="x")


def test_evidence_provenance_is_typed_and_target_bound():
    evidence = {
        "evidence_id": "e",
        "evidence_kind": "trusted_runtime_fact",
        "source_type": "trusted_runtime",
        "source_ref": "runtime",
        "episode_or_turn_ref": "turn",
        "target_goal_ref": "goal",
        "target_requirement_ref": "requirement",
        "provenance": {"producer": "trusted_runtime", "review_state": "reviewed"},
        "finality": "final",
    }
    with pytest.raises(ValueError):
        GoalEvidence.model_validate({
            **evidence,
            "provenance": {**evidence["provenance"], "unknown_policy_fact": True},
        })
    with pytest.raises(ValueError):
        GoalEvidence.model_validate({
            **evidence,
            "provenance": {
                **evidence["provenance"],
                "suspend_goal_ref": "another-goal",
            },
        })
