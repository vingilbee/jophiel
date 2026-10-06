"""Closed, definition-specific Goal V0 adoption manifest (SH-EVAL-013).

Adoption used to reject a candidate whenever *any* linked hypothesis was below
``strong``.  The Interpretation ontology deliberately assigns ``moderate``
support to ordinary speaker goals (an explicit greeting grounds a strong
greeting act and a moderate social-connection goal), so that rule rejected
valid goals systematically.

Each installed Goal definition now declares exactly which hypotheses gate its
adoption, at what minimum support and disposition, and which hard gates apply.
Only the candidate's own act, and the hypotheses that share that act's frames,
are evaluated; a hypothesis that no requirement names never vetoes a
candidate.  The manifest is immutable, validated at import, and versioned
independently (``GOAL_ADOPTION_POLICY_VERSION``).  Nothing here reads Dynamic
State, personality, or model output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.goals import (
    GOAL_ADOPTION_POLICY_VERSION,
    GoalRejectionCheck,
    GoalRejectionDetail,
)
from ..contracts.interpretation import (
    HYPOTHESIS_DIMENSION_BY_KEY,
    FrozenModel,
    HypothesisDimension,
    HypothesisDisposition,
    HypothesisSupport,
    InterpretationHypothesis,
)
from .ontology import all_definitions
from .rules import ADOPTION_RULE_IDS

SUPPORT_RANK: Mapping[HypothesisSupport, int] = {
    HypothesisSupport.UNSUPPORTED: 0,
    HypothesisSupport.WEAK: 1,
    HypothesisSupport.MODERATE: 2,
    HypothesisSupport.STRONG: 3,
}
RETAINED = (HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE)
LEADING = (HypothesisDisposition.LEADING,)
UNGROUNDED_PREDICATES = ("unknown",)
QUOTED_OR_HYPOTHETICAL = ("interpretation.modality.quoted", "interpretation.modality.hypothetical")
# Runtime authorization values that do not deny an authorized-task goal.  An
# absent value is not a denial: authorization is enforced again at action time.
AUTHORIZATION_NOT_DENIED = frozenset({None, "not_required", "authorized", "confirmed", "allowed", "granted"})
SAFETY_BLOCKED = "blocked"

RULE_MISSING_GROUNDING = "goal-reject.missing-required-grounding.v1"
RULE_HARD_GATE = "goal-reject.hard-gate-denial.v1"
RULE_QUOTED = "goal-reject.quoted-or-hypothetical-request.v1"
RULE_SUPPORT = "goal-reject.insufficient-interpretation-support.v1"
RULE_PARENT = "goal-reject.parent-goal-not-adopted.v1"
RULE_CONTRADICTION = "goal-reject.active-contradiction.v1"

_CHECK_RULES: Mapping[GoalRejectionCheck, str] = {
    GoalRejectionCheck.UNRESOLVED_ARGUMENT: RULE_MISSING_GROUNDING,
    GoalRejectionCheck.AUTHORIZATION: RULE_HARD_GATE,
    GoalRejectionCheck.SAFETY: RULE_HARD_GATE,
    GoalRejectionCheck.PARENT_NOT_ADOPTED: RULE_PARENT,
    GoalRejectionCheck.ACTIVE_CONTRADICTION: RULE_CONTRADICTION,
    GoalRejectionCheck.DISQUALIFYING_MODALITY: RULE_QUOTED,
    GoalRejectionCheck.HYPOTHESIS_MISSING: RULE_SUPPORT,
    GoalRejectionCheck.HYPOTHESIS_DISPOSITION: RULE_SUPPORT,
    GoalRejectionCheck.HYPOTHESIS_SUPPORT: RULE_SUPPORT,
    GoalRejectionCheck.UNGROUNDED_SOURCE: RULE_SUPPORT,
}


class GoalHypothesisRequirement(FrozenModel):
    """One hypothesis a candidate's own act must (or may) carry."""

    role: Literal["act", "speaker_goal", "corroboration"]
    dimension: HypothesisDimension
    hypothesis_key: str = Field(min_length=1)
    minimum_support: HypothesisSupport
    allowed_dispositions: tuple[HypothesisDisposition, ...] = Field(min_length=1)
    # Optional requirements are evaluated and never veto adoption.
    required: bool = True

    @model_validator(mode="after")
    def valid(self) -> GoalHypothesisRequirement:
        if HYPOTHESIS_DIMENSION_BY_KEY.get(self.hypothesis_key) != self.dimension:
            raise ValueError("hypothesis requirement key and dimension disagree")
        if self.minimum_support == HypothesisSupport.UNSUPPORTED:
            raise ValueError("a hypothesis requirement cannot accept unsupported hypotheses")
        if not set(self.allowed_dispositions) <= set(RETAINED):
            raise ValueError("only leading or retained hypotheses can satisfy a requirement")
        if len(set(self.allowed_dispositions)) != len(self.allowed_dispositions):
            raise ValueError("allowed dispositions must be unique")
        return self


class GoalAdoptionPolicy(FrozenModel):
    goal_definition_key: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    # The adoption rule an adopted record cites.
    adoption_rule_id: str = Field(min_length=1)
    hypothesis_requirements: tuple[GoalHypothesisRequirement, ...] = ()
    # Requirement target roles that must be bound to a non-blank reference.
    required_bound_targets: tuple[str, ...] = ()
    # ``reject_unless_resolvable``: every unresolved argument of the
    # candidate's own frames rejects it, except a declared resolvable argument
    # that has an exact typed unknown (it forms a parent-linked epistemic
    # subgoal instead).  ``ignore``: the requirement binds the frame itself.
    unresolved_arguments: Literal["ignore", "reject_unless_resolvable"] = "ignore"
    resolvable_arguments: tuple[str, ...] = ()
    # Adoption v0.2: referent arguments whose unresolved value is readiness,
    # not a rejection ground.  Requirement resolution settles them after Goal
    # (from Dialogue Working Memory, or by asking the user); no epistemic
    # subgoal forms for them, and the user's request is never erased.
    deferred_arguments: tuple[str, ...] = ()
    disqualifying_modalities: tuple[str, ...] = ()
    # A candidate bound to a frame of one of these predicates has no
    # recoverable meaning to commit to; it is rejected as ungrounded.
    ungrounded_predicates: tuple[str, ...] = ()
    reject_on_active_contradiction: bool = False
    requires_authorization: bool = False
    requires_safety_allow: bool = False
    reject_on_safety_block: bool = False
    requires_adopted_parent: bool = False

    @model_validator(mode="after")
    def valid(self) -> GoalAdoptionPolicy:
        if self.adoption_rule_id not in ADOPTION_RULE_IDS:
            raise ValueError("adoption policy cites an unregistered adoption rule")
        keys = tuple((item.role, item.hypothesis_key) for item in self.hypothesis_requirements)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate hypothesis requirement")
        if sum(1 for item in self.hypothesis_requirements if item.role == "act") > 1:
            raise ValueError("an adoption policy names at most one act requirement")
        if any(HYPOTHESIS_DIMENSION_BY_KEY.get(key) != HypothesisDimension.MODALITY for key in self.disqualifying_modalities):
            raise ValueError("disqualifying modalities must be modality hypotheses")
        if self.resolvable_arguments and self.unresolved_arguments != "reject_unless_resolvable":
            raise ValueError("resolvable arguments require the reject_unless_resolvable policy")
        if len(set(self.resolvable_arguments)) != len(self.resolvable_arguments):
            raise ValueError("resolvable arguments must be unique")
        if self.deferred_arguments and self.unresolved_arguments != "reject_unless_resolvable":
            raise ValueError("deferred arguments require the reject_unless_resolvable policy")
        if len(set(self.deferred_arguments)) != len(self.deferred_arguments):
            raise ValueError("deferred arguments must be unique")
        if set(self.deferred_arguments) & set(self.resolvable_arguments):
            raise ValueError("an argument is either resolvable by a subgoal or deferred, never both")
        return self


def _req(role, key, minimum, dispositions=LEADING, *, required=True) -> GoalHypothesisRequirement:
    return GoalHypothesisRequirement(
        role=role, dimension=HYPOTHESIS_DIMENSION_BY_KEY[key], hypothesis_key=key,
        minimum_support=minimum, allowed_dispositions=dispositions, required=required,
    )


S = HypothesisSupport
_POLICIES = (
    GoalAdoptionPolicy(
        goal_definition_key="interaction.discharge_response_obligation",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        adoption_rule_id="goal-adopt.required-system-obligation.v1",
        required_bound_targets=("turn_ref",),
    ),
    GoalAdoptionPolicy(
        goal_definition_key="interaction.answer_information_request",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.request_information", S.STRONG),
            _req("speaker_goal", "interpretation.goal.obtain_information", S.STRONG),
            _req("corroboration", "interpretation.modality.questioned", S.MODERATE, RETAINED, required=False),
        ),
        required_bound_targets=("topic_ref",),
        unresolved_arguments="reject_unless_resolvable",
        # A follow-up's pronoun referent ("expand on that") is resolved from
        # Dialogue Working Memory or asked for by requirement resolution.
        deferred_arguments=("referent_phrase",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="interaction.acknowledge_relevant_information",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.inform", S.STRONG),
            _req("speaker_goal", "interpretation.goal.seek_acknowledgement", S.STRONG),
        ),
        required_bound_targets=("proposition_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="interaction.repair_shared_understanding",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-shared-commitment.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.correct", S.STRONG),
            _req("speaker_goal", "interpretation.goal.repair_shared_understanding", S.STRONG),
        ),
        required_bound_targets=("proposition_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    # The SH-EVAL-013 case: a strong greeting act with the reviewed moderate
    # social-connection goal is a valid social-contact commitment.
    GoalAdoptionPolicy(
        goal_definition_key="interaction.respond_to_social_contact",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-shared-commitment.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.greet", S.STRONG),
            _req("speaker_goal", "interpretation.goal.maintain_social_connection", S.MODERATE),
        ),
        required_bound_targets=("dialogue_scope_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="epistemic.resolve_required_unknown",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        adoption_rule_id="goal-adopt.required-subgoal.v1",
        required_bound_targets=("parent_goal_ref", "argument_ref"),
        requires_adopted_parent=True,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="epistemic.resolve_material_contradiction",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        adoption_rule_id="goal-adopt.required-subgoal.v1",
        required_bound_targets=("contradiction_ref",),
    ),
    # Moderate is never enough here: this is the goal that can lead to an
    # external, possibly irreversible, effect.
    GoalAdoptionPolicy(
        goal_definition_key="task.complete_authorized_request",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.request_action", S.STRONG),
            _req("speaker_goal", "interpretation.goal.cause_action", S.STRONG),
            _req("corroboration", "interpretation.modality.requested", S.MODERATE, RETAINED, required=False),
        ),
        required_bound_targets=("task_ref",),
        unresolved_arguments="reject_unless_resolvable",
        resolvable_arguments=("subject_ref", "recipient"),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
        reject_on_active_contradiction=True,
        requires_authorization=True,
        reject_on_safety_block=True,
    ),
    # Stopping or preventing an action only reduces external effects, so a
    # moderately supported request act (e.g. one weakened by an unresolved
    # referent) still commits to it; the prevent goal itself must be strong.
    GoalAdoptionPolicy(
        goal_definition_key="task.prevent_or_stop_requested_action",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.request_action", S.MODERATE),
            _req("speaker_goal", "interpretation.goal.prevent_or_stop_action", S.STRONG),
        ),
        required_bound_targets=("task_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    # Adoption v0.2: the non-committing request handler.  It commits only to
    # handling the request (fulfil, clarify, or truthfully decline), so a
    # moderately supported request act -- one weakened by an unresolved
    # referent or an unsafe reading -- still preserves the user's intent.
    # Missing arguments, capability, authorization, consent, and safety are
    # readiness, resolved after Goal; none of them may erase the request here.
    GoalAdoptionPolicy(
        goal_definition_key="interaction.handle_action_request",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.request_action", S.MODERATE),
            _req("speaker_goal", "interpretation.goal.cause_action", S.MODERATE),
            _req("corroboration", "interpretation.modality.requested", S.MODERATE, RETAINED, required=False),
        ),
        required_bound_targets=("request_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    # Adoption v0.3: producing content inside the conversation commits to no
    # external effect, so -- like the request handler -- a moderately
    # supported request act preserves the user's intent.  Missing arguments
    # and the artifact target are readiness, resolved after Goal.
    GoalAdoptionPolicy(
        goal_definition_key="interaction.fulfill_conversational_deliverable",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.request_action", S.MODERATE),
            _req("speaker_goal", "interpretation.goal.cause_action", S.MODERATE),
            _req("corroboration", "interpretation.modality.requested", S.MODERATE, RETAINED, required=False),
        ),
        required_bound_targets=("request_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="interaction.incorporate_inquiry_answer",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        ungrounded_predicates=UNGROUNDED_PREDICATES,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        hypothesis_requirements=(
            _req("act", "interpretation.act.inform", S.MODERATE),
            _req("speaker_goal", "interpretation.goal.transfer_information", S.MODERATE),
        ),
        required_bound_targets=("answer_ref",),
        disqualifying_modalities=QUOTED_OR_HYPOTHETICAL,
    ),
    GoalAdoptionPolicy(
        goal_definition_key="interaction.explain_unavailable_or_failed_operation",
        policy_version=GOAL_ADOPTION_POLICY_VERSION,
        adoption_rule_id="goal-adopt.explicit-grounded-user-request.v1",
        required_bound_targets=("operation_ref", "failure_ref"),
    ),
)


def validate_adoption_manifest(policies: tuple[GoalAdoptionPolicy, ...] = _POLICIES) -> tuple[GoalAdoptionPolicy, ...]:
    """The manifest is closed over the installed Goal definitions."""

    definitions = {item.definition_key: item for item in all_definitions()}
    keys = tuple(item.goal_definition_key for item in policies)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate goal adoption policy")
    if set(keys) != set(definitions):
        raise ValueError("adoption manifest must cover exactly the installed goal definitions")
    for policy in policies:
        if policy.policy_version != GOAL_ADOPTION_POLICY_VERSION:
            raise ValueError("adoption policy version differs from the manifest version")
        roles = {role for template in definitions[policy.goal_definition_key].requirement_templates for role in template.required_target_roles}
        if not set(policy.required_bound_targets) <= roles:
            raise ValueError("adoption policy binds a target role its definition does not declare")
    return policies


ADOPTION_POLICIES: Mapping[str, GoalAdoptionPolicy] = {
    item.goal_definition_key: item for item in validate_adoption_manifest()
}


def adoption_policy(definition_key: str) -> GoalAdoptionPolicy:
    try:
        return ADOPTION_POLICIES[definition_key]
    except KeyError as exc:
        raise ValueError("goal definition has no adoption policy") from exc


@dataclass(frozen=True)
class UnresolvedArgument:
    """One unresolved argument of a candidate's own frame.

    ``unknown_ref`` is the exact typed unknown for it, when exactly one exists.
    """

    argument_name: str
    frame_ref: str
    unknown_ref: str | None


@dataclass(frozen=True)
class AdoptionContext:
    """Everything adoption may consult about one candidate, and nothing else."""

    definition_key: str
    is_system_owned: bool
    has_source_refs: bool
    bound_targets: Mapping[str, str]
    act_hypothesis: InterpretationHypothesis | None = None
    linked_hypotheses: tuple[InterpretationHypothesis, ...] = ()
    frame_refs: tuple[str, ...] = ()
    frame_predicates: tuple[str, ...] = ()
    unresolved: tuple[UnresolvedArgument, ...] = ()
    active_contradiction_refs: frozenset[str] = frozenset()
    authorization: str | None = None
    safety_decision: str | None = None
    parent_adopted: bool | None = None


@dataclass(frozen=True)
class AdoptionDecision:
    adopted: bool
    rejection_rule_id: str | None = None
    detail: GoalRejectionDetail | None = None


def _reject(policy: GoalAdoptionPolicy, context: AdoptionContext, check: GoalRejectionCheck, **fields) -> AdoptionDecision:
    return AdoptionDecision(False, _CHECK_RULES[check], GoalRejectionDetail(
        policy_key=policy.goal_definition_key, policy_version=policy.policy_version,
        failed_check=check, frame_refs=tuple(sorted(set(context.frame_refs))), **fields,
    ))


def _ranked(hypotheses) -> list[InterpretationHypothesis]:
    # Independent of hypothesis ordering: best support first, then identity.
    return sorted(hypotheses, key=lambda item: (-SUPPORT_RANK[item.support], item.hypothesis_id))


def _check_requirement(policy, context, requirement: GoalHypothesisRequirement) -> AdoptionDecision | None:
    if requirement.role == "act" and context.act_hypothesis is not None and (
        context.act_hypothesis.hypothesis_key == requirement.hypothesis_key
    ):
        # The act is the exact hypothesis the candidate was formed from.
        matches = [context.act_hypothesis]
    else:
        matches = _ranked(item for item in context.linked_hypotheses if item.hypothesis_key == requirement.hypothesis_key)
    if not matches:
        return _reject(policy, context, GoalRejectionCheck.HYPOTHESIS_MISSING,
                       hypothesis_key=requirement.hypothesis_key, minimum_support=requirement.minimum_support,
                       allowed_dispositions=requirement.allowed_dispositions)
    eligible = [item for item in matches if item.disposition in requirement.allowed_dispositions]
    if not eligible:
        observed = matches[0]
        return _reject(policy, context, GoalRejectionCheck.HYPOTHESIS_DISPOSITION,
                       hypothesis_key=requirement.hypothesis_key, hypothesis_ref=observed.hypothesis_id,
                       allowed_dispositions=requirement.allowed_dispositions,
                       observed_disposition=observed.disposition, observed_support=observed.support)
    best = eligible[0]
    if SUPPORT_RANK[best.support] < SUPPORT_RANK[requirement.minimum_support]:
        return _reject(policy, context, GoalRejectionCheck.HYPOTHESIS_SUPPORT,
                       hypothesis_key=requirement.hypothesis_key, hypothesis_ref=best.hypothesis_id,
                       minimum_support=requirement.minimum_support, observed_support=best.support,
                       allowed_dispositions=requirement.allowed_dispositions,
                       observed_disposition=best.disposition)
    return None


def evaluate_adoption(policy: GoalAdoptionPolicy, context: AdoptionContext) -> AdoptionDecision:
    """Apply one policy.  Checks run in a fixed precedence order."""

    if policy.goal_definition_key != context.definition_key:
        raise ValueError("adoption policy evaluated for another definition")
    for role in policy.required_bound_targets:
        if not str(context.bound_targets.get(role) or "").strip():
            return _reject(policy, context, GoalRejectionCheck.UNRESOLVED_ARGUMENT, argument_name=role)
    if set(context.frame_predicates) & set(policy.ungrounded_predicates):
        return _reject(policy, context, GoalRejectionCheck.UNRESOLVED_ARGUMENT, argument_name="predicate")
    if policy.unresolved_arguments == "reject_unless_resolvable":
        for item in sorted(context.unresolved, key=lambda value: (value.frame_ref, value.argument_name)):
            if item.argument_name in policy.resolvable_arguments and item.unknown_ref is not None:
                continue
            if item.argument_name in policy.deferred_arguments:
                continue
            return _reject(policy, context, GoalRejectionCheck.UNRESOLVED_ARGUMENT,
                           argument_name=item.argument_name,
                           related_refs=(item.unknown_ref,) if item.unknown_ref else ())
    if policy.requires_authorization and context.authorization not in AUTHORIZATION_NOT_DENIED:
        return _reject(policy, context, GoalRejectionCheck.AUTHORIZATION, runtime_value=context.authorization)
    if policy.reject_on_safety_block and context.safety_decision == SAFETY_BLOCKED:
        return _reject(policy, context, GoalRejectionCheck.SAFETY, runtime_value=context.safety_decision)
    if policy.requires_safety_allow and context.safety_decision is not None:
        return _reject(policy, context, GoalRejectionCheck.SAFETY, runtime_value=context.safety_decision)
    if policy.requires_adopted_parent and context.parent_adopted is not True:
        return _reject(policy, context, GoalRejectionCheck.PARENT_NOT_ADOPTED,
                       related_refs=(context.bound_targets["parent_goal_ref"],) if context.bound_targets.get("parent_goal_ref") else ())
    if policy.reject_on_active_contradiction:
        cited = sorted({
            ref for item in context.linked_hypotheses for ref in item.contradiction_refs
            if ref in context.active_contradiction_refs
        })
        if cited:
            return _reject(policy, context, GoalRejectionCheck.ACTIVE_CONTRADICTION, related_refs=tuple(cited))
    for modality in policy.disqualifying_modalities:
        present = _ranked(
            item for item in context.linked_hypotheses
            if item.hypothesis_key == modality and item.disposition in RETAINED
            and item.support != HypothesisSupport.UNSUPPORTED
        )
        if present:
            return _reject(policy, context, GoalRejectionCheck.DISQUALIFYING_MODALITY,
                           hypothesis_key=modality, hypothesis_ref=present[0].hypothesis_id,
                           observed_support=present[0].support, observed_disposition=present[0].disposition)
    for requirement in policy.hypothesis_requirements:
        if not requirement.required:
            continue
        failure = _check_requirement(policy, context, requirement)
        if failure is not None:
            return failure
    if not (context.is_system_owned or context.has_source_refs):
        return _reject(policy, context, GoalRejectionCheck.UNGROUNDED_SOURCE)
    return AdoptionDecision(True)


__all__ = [
    "ADOPTION_POLICIES", "AdoptionContext", "AdoptionDecision", "GoalAdoptionPolicy",
    "GoalHypothesisRequirement", "SUPPORT_RANK", "UnresolvedArgument", "adoption_policy",
    "evaluate_adoption", "validate_adoption_manifest",
]
