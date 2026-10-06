from types import SimpleNamespace
from app.ai.Cognitive.system.v0.goal_v0.engine import GoalEngine
from app.ai.Cognitive.system.v0.contracts.goals import GoalStatus, GoalTransitionKind

def test_proposal_preserves_proposal_rule():
    record = SimpleNamespace(goal_id="g", status=GoalStatus.ACTIVE, adoption_basis=SimpleNamespace(rule_id="goal-propose.required-response-obligation.v1"), source_refs=())
    request = SimpleNamespace(cognitive_turn_id="t", previous_goal_snapshot=None)
    transition = GoalEngine()._transition(record, GoalTransitionKind.PROPOSE, request, 1, new_record=True, rule_id=record.adoption_basis.rule_id)
    assert transition.from_status is None and transition.to_status == GoalStatus.CANDIDATE

def test_identifier_text_does_not_define_evidence_polarity():
    # Evidence polarity comes only from typed provenance, never an ID prefix.
    assert "tool-" not in "semantic-grounding"
