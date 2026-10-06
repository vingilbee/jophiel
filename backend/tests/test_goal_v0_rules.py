from app.ai.Cognitive.system.v0.goal_v0.rules import validate_rule_id

def test_registered_rule_ids_are_checked():
    validate_rule_id("goal-propose.required-response-obligation.v1")
