from ..contracts.goals import GoalStatus, GoalTransitionKind

TRANSITIONS = {
 (None, GoalTransitionKind.PROPOSE): GoalStatus.CANDIDATE,
 (GoalStatus.CANDIDATE, GoalTransitionKind.ADOPT): GoalStatus.ACTIVE,
 (GoalStatus.CANDIDATE, GoalTransitionKind.REJECT): GoalStatus.REJECTED,
 (GoalStatus.CANDIDATE, GoalTransitionKind.EXPIRE): GoalStatus.EXPIRED,
 (GoalStatus.CANDIDATE, GoalTransitionKind.SUPERSEDE): GoalStatus.SUPERSEDED,
 (GoalStatus.ACTIVE, GoalTransitionKind.SUSPEND): GoalStatus.SUSPENDED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.RESUME): GoalStatus.ACTIVE,
 (GoalStatus.ACTIVE, GoalTransitionKind.SATISFY): GoalStatus.SATISFIED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.SATISFY): GoalStatus.SATISFIED,
 (GoalStatus.ACTIVE, GoalTransitionKind.FAIL): GoalStatus.FAILED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.FAIL): GoalStatus.FAILED,
 (GoalStatus.ACTIVE, GoalTransitionKind.ABANDON): GoalStatus.ABANDONED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.ABANDON): GoalStatus.ABANDONED,
 (GoalStatus.ACTIVE, GoalTransitionKind.SUPERSEDE): GoalStatus.SUPERSEDED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.SUPERSEDE): GoalStatus.SUPERSEDED,
 (GoalStatus.ACTIVE, GoalTransitionKind.EXPIRE): GoalStatus.EXPIRED,
 (GoalStatus.SUSPENDED, GoalTransitionKind.EXPIRE): GoalStatus.EXPIRED,
}
def next_status(current, kind):
    key=(current,kind)
    if key not in TRANSITIONS: raise ValueError("illegal goal transition")
    return TRANSITIONS[key]
