from app.ai.Cognitive.system.v0.goal_v0.lifecycle import next_status
from app.ai.Cognitive.system.v0.contracts.goals import GoalStatus, GoalTransitionKind

def test_first_transition_is_proposal():
    assert next_status(None, GoalTransitionKind.PROPOSE) == GoalStatus.CANDIDATE
    assert next_status(GoalStatus.CANDIDATE, GoalTransitionKind.ADOPT) == GoalStatus.ACTIVE
