"""Typed, content-safe Action Selection V0 failures."""

from __future__ import annotations

from ..contracts.actions import ActionFailurePhase

SAFE_CATEGORIES: dict[ActionFailurePhase, str] = {
    ActionFailurePhase.REQUEST_VALIDATION: "invalid_action_request",
    ActionFailurePhase.REGISTRY_RESOLUTION: "unresolvable_action_registry",
    ActionFailurePhase.CANDIDATE_CONSTRUCTION: "invalid_action_candidate",
    ActionFailurePhase.HARD_GATE: "invalid_action_gate",
    ActionFailurePhase.FEATURE_EXTRACTION: "invalid_action_feature",
    ActionFailurePhase.SCORING: "invalid_action_score",
    ActionFailurePhase.CONFLICT_RESOLUTION: "invalid_action_conflict",
    ActionFailurePhase.PLAN_TRANSITION: "invalid_action_transition",
    ActionFailurePhase.PLAN_VALIDATION: "invalid_action_plan",
    ActionFailurePhase.TRACE_VALIDATION: "invalid_action_trace",
}

SAFE_MESSAGES: dict[ActionFailurePhase, str] = {
    ActionFailurePhase.REQUEST_VALIDATION: "The action-selection request failed validation.",
    ActionFailurePhase.REGISTRY_RESOLUTION: "The installed action registry could not be resolved.",
    ActionFailurePhase.CANDIDATE_CONSTRUCTION: "Action candidates could not be constructed safely.",
    ActionFailurePhase.HARD_GATE: "The action hard gate could not complete.",
    ActionFailurePhase.FEATURE_EXTRACTION: "Action features could not be extracted.",
    ActionFailurePhase.SCORING: "Action scores could not be computed.",
    ActionFailurePhase.CONFLICT_RESOLUTION: "The action selection conflict could not be resolved.",
    ActionFailurePhase.PLAN_TRANSITION: "The partial action plan could not be advanced.",
    ActionFailurePhase.PLAN_VALIDATION: "No valid action plan could be produced.",
    ActionFailurePhase.TRACE_VALIDATION: "The action decision trace failed validation.",
}


class ActionSelectionError(RuntimeError):
    """Fail-closed action failure.  No partial plan is ever attached."""

    def __init__(
        self,
        *,
        cognitive_turn_id: str,
        failed_phase: ActionFailurePhase,
        code: str,
        category: str | None = None,
        safe_message: str | None = None,
    ) -> None:
        message = safe_message or SAFE_MESSAGES[failed_phase]
        super().__init__(message)
        self.cognitive_turn_id = cognitive_turn_id
        self.failed_phase = failed_phase
        self.category = category or SAFE_CATEGORIES[failed_phase]
        self.code = code
        self.safe_message = message


class NoViablePlanError(ValueError):
    """A required response has no positively valued eligible move."""


class CycleGuardError(RuntimeError):
    """The defensive compute guard fired; this is never a preferred STOP."""
