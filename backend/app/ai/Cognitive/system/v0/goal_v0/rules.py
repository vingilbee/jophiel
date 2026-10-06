"""Registered Goal V0 rule identifiers and bounded policy registries."""
PROPOSAL_RULE_IDS = (
    "goal-propose.required-response-obligation.v1",
    "goal-propose.explicit-information-request.v1",
    "goal-propose.relevant-information-acknowledgement.v1",
    "goal-propose.explicit-repair-request.v1",
    "goal-propose.explicit-social-contact.v1",
    "goal-propose.blocking-required-unknown.v1",
    "goal-propose.material-contradiction.v1",
    "goal-propose.explicit-authorized-action-request.v1",
    "goal-propose.explicit-prevent-or-stop-request.v1",
    "goal-propose.known-operation-failure.v1",
    # Formation v0.4: an explicit action request with no trusted task binding
    # forms the non-committing request handler.
    "goal-propose.explicit-action-request-handling.v1",
    # Formation v0.5: a request for content inside the conversation, and an
    # answer to an exact open inquiry.
    "goal-propose.explicit-conversational-deliverable.v1",
    "goal-propose.inquiry-answer-incorporation.v1",
)
ADOPTION_RULE_IDS = (
    "goal-adopt.required-system-obligation.v1",
    "goal-adopt.explicit-grounded-user-request.v1",
    "goal-adopt.explicit-shared-commitment.v1",
    "goal-adopt.required-subgoal.v1",
)
REJECTION_RULE_IDS = (
    "goal-reject.insufficient-interpretation-support.v1",
    "goal-reject.quoted-or-hypothetical-request.v1",
    "goal-reject.missing-required-grounding.v1",
    "goal-reject.hard-gate-denial.v1",
    "goal-reject.unregistered-definition.v1",
    # Adoption manifest v0.1: a required subgoal whose parent was not adopted,
    # and a candidate whose own hypotheses cite an active contradiction.
    "goal-reject.parent-goal-not-adopted.v1",
    "goal-reject.active-contradiction.v1",
)
MATERIALITY = ("material", "immaterial", "unknown", "high", "moderate", "low", "none")
REVERSIBILITY = ("reversible", "irreversible", "unknown")
RESOLUTIONS = ("compatible", "defer", "suspend", "clarify", "slow", "withdraw", "supersede", "investigate", "retain_non_focal", "cofocus_or_retain", "unresolved_material_conflict", "stable_ascending_goal_identity", "prefer_higher_priority", "lexical_material_tie")
REASON_CODES = (
    "rule_conditions_met", "candidate_not_adopted", "propose", "adopt",
    "reject", "satisfy", "suspend", "resume", "abandon", "fail",
    "supersede", "expire",
)
PRIORITY_RULE_IDS = (
    "goal-priority.trusted-deadline.v1",
    "goal-priority.blocking-required-subgoal.v1",
)
FOCUS_RULE_IDS = (
    "goal-focus.bounded-compatible.v1", "goal-priority.definition-default.v1",
    "goal-priority.current-explicit-request.v1",
    # Priority policy v0.2 (SH-EVAL-014): blocking prerequisite > current
    # request > this turn's response obligation > compatible history > social
    # maintenance.  Cited whenever active history was ranked below the current turn.
    "goal-priority.current-explicit-request-over-compatible-history.v1",
)
LIFECYCLE_RULE_IDS = (
    "goal-lifecycle.satisfy-all-requirements.v1",
    "goal-lifecycle.expire-turn-scope.v1",
    "goal-lifecycle.expire-conversation-scope.v1",
    "goal-lifecycle.expire-task-scope.v1",
    # A successor response turn closed the prior turn's scope (trusted rollover).
    "goal-lifecycle.expire-turn-scope-on-successor-turn.v1",
    "goal-lifecycle.suspend-explicit-deferral.v1",
    "goal-lifecycle.suspend-missing-temporary-prerequisite.v1",
    "goal-lifecycle.resume-blocker-resolved.v1",
    "goal-lifecycle.fail-verified-impossibility.v1",
    "goal-lifecycle.abandon-explicit-withdrawal.v1",
    "goal-lifecycle.supersede-material-replacement.v1",
)
CONFLICT_RULE_IDS = (
    "goal-conflict.benign-stable-order.v1",
    "goal-conflict.compatible-effects.v1",
    "goal-conflict.higher-priority.v1",
    "goal-conflict.material-tie-route.v1",
    "goal-conflict.shared-resource.v1",
    "goal-conflict.unknown-relation.v1",
)

def validate_rule_id(rule_id: str) -> None:
    if rule_id not in PROPOSAL_RULE_IDS + ADOPTION_RULE_IDS + REJECTION_RULE_IDS + CONFLICT_RULE_IDS + PRIORITY_RULE_IDS + FOCUS_RULE_IDS + LIFECYCLE_RULE_IDS:
        raise ValueError("unregistered goal rule")
