"""The versioned V0 feature calculators and their declarative manifests.

Every eligible candidate receives all 13 normalized features.  Each value
records its missing/inapplicable status, provenance, calculator rule, and the
manifest entries it used.  Manifests are keyed by semantic families, effects,
typed goal requirements, and typed state fields -- never by case identifiers,
expected plans, or prose.  No feature copies another feature's value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from ..contracts.actions import (
    ACTION_FEATURE_VERSION, ActionFeatureValue, ActionTargetType as T, BindingMode, FeatureKey as F,
    FeatureStatus, GroundedActionCandidate, GroundedActionMove, PartialPlanState, PlannedActionEffect,
    TransitionDomain, canonical_decimal, effect_ledger_keys,
)
from ..contracts.goals import GoalRequirementStatus, project_active_goal
from .planning_conditions import addresses_any, derive_planning_conditions
from .reference_index import ActionReferenceIndex
from .registry import ActionRegistry

FEATURE_VERSION = ACTION_FEATURE_VERSION
ZERO, ONE = Decimal(0), Decimal(1)

# -- normalization tables (implementation brief section 14) -------------------
GOAL_PRIORITY = {"low": Decimal("0.33"), "moderate": Decimal("0.67"), "high": Decimal("1.00")}
SUPPORT = {"weak": Decimal("0.25"), "moderate": Decimal("0.60"), "strong": Decimal("1.00")}
DIRECT_LINK, SUPPORTING_LINK = Decimal("1.00"), Decimal("0.50")
NON_FOCAL_UNCERTAINTY_WEIGHT = Decimal("0.50")
# Features v0.4: a typed inquiry the inquiry policy selected is itself an
# open question; its relevance comes from its declared criticality (a
# blocking question belongs to requirement resolution, never here).
INQUIRY_UNCERTAINTY_RELEVANCE = {"quality_improving": Decimal("0.50"), "optional_curiosity": Decimal("0.30")}
UNLINKED_CONTRADICTION_WEIGHT = Decimal("0.50")
CONTROL_BANDS = {"very_low": Decimal("-1"), "low": Decimal("-0.5"), "moderate": ZERO, "high": Decimal("0.5"), "very_high": ONE}
GUIDANCE_REQUIRED_SUPPORT = {"neutral_option": ZERO, "low": Decimal("0.25"), "moderate": Decimal("0.60"), "high": Decimal("1.00")}
GUIDANCE_ORDER = ("neutral_option", "low", "moderate", "high")
SUPPORT_ORDER = {"weak": 1, "moderate": 2, "strong": 3}

RESPONSE_OBLIGATION_REQUIREMENT = "response_obligation_discharged"
REQUIREMENT_TARGET_ROLES: Mapping[str, tuple[str, ...]] = {
    "supported_answer_provided": ("topic_ref",), "relevant_information_acknowledged": ("proposition_ref",),
    "shared_misunderstanding_repaired": ("proposition_ref",), "social_contact_answered": ("dialogue_scope_ref",),
    "required_argument_resolved": ("argument_ref",), "material_contradiction_resolved": ("contradiction_ref",),
    "authorized_task_completed": ("task_ref",), "requested_action_prevented_or_stopped": ("task_ref",),
    "operation_failure_explained": ("operation_ref", "failure_ref"),
    "action_request_handled": ("request_ref",),
    "conversational_deliverable_delivered": ("request_ref",),
    "inquiry_answer_incorporated": ("answer_ref",),
}

# -- goal-link manifest ---------------------------------------------------------
# A direct link is permitted only where the requirement's satisfaction policy
# accepts realization evidence; requirements satisfied only by user or world
# evidence (argument grounding, contradiction resolution, task results) accept
# at most supporting links from a conversational move.
REALIZATION_SATISFIABLE = frozenset({
    "response_obligation_discharged", "supported_answer_provided", "relevant_information_acknowledged",
    "shared_misunderstanding_repaired", "social_contact_answered", "operation_failure_explained",
    "action_request_handled", "conversational_deliverable_delivered", "inquiry_answer_incorporated",
})
ELICITATION_EFFECTS = (
    "elicitation.clarification", "elicitation.disambiguation", "elicitation.verification", "elicitation.goal",
    "elicitation.preference", "elicitation.evidence", "elicitation.reason", "elicitation.experience",
    "elicitation.understanding_check", "elicitation.response_feedback", "dialogue.repetition_request",
    "elicitation.inquiry",
)
_D, _S = "direct", "supporting"
GOAL_LINKS: Mapping[tuple[str, str], str] = {
    ("information.answer", "supported_answer_provided"): _D,
    ("information.explanation", "supported_answer_provided"): _D,
    ("information.uncertainty_qualification", "supported_answer_provided"): _S,
    ("information.correction", "supported_answer_provided"): _S,
    ("feedback.receipt", "relevant_information_acknowledged"): _D,
    ("feedback.paraphrase", "relevant_information_acknowledged"): _D,
    ("feedback.reflection", "relevant_information_acknowledged"): _D,
    ("social_support.congratulation", "relevant_information_acknowledged"): _D,
    ("social_support.experience_validation", "relevant_information_acknowledged"): _S,
    ("dialogue.repair", "shared_misunderstanding_repaired"): _D,
    ("information.correction", "shared_misunderstanding_repaired"): _D,
    ("dialogue.self_correction", "shared_misunderstanding_repaired"): _S,
    ("feedback.misunderstanding_disclosure", "shared_misunderstanding_repaired"): _S,
    ("information.example", "shared_misunderstanding_repaired"): _S,
    ("elicitation.understanding_check", "shared_misunderstanding_repaired"): _S,
    ("dialogue.opening", "social_contact_answered"): _D,
    ("dialogue.closure", "social_contact_answered"): _D,
    ("feedback.receipt", "social_contact_answered"): _S,
    **{(effect, "required_argument_resolved"): _S for effect in ELICITATION_EFFECTS},
    ("information.correction", "material_contradiction_resolved"): _S,
    ("dialogue.self_correction", "material_contradiction_resolved"): _S,
    ("elicitation.verification", "material_contradiction_resolved"): _S,
    ("coordination.next_step", "authorized_task_completed"): _S,
    ("coordination.plan_proposal", "authorized_task_completed"): _S,
    ("coordination.offer", "authorized_task_completed"): _S,
    ("choice_guidance.advice", "authorized_task_completed"): _S,
    ("social_play.joke", "authorized_task_completed"): _S,
    ("dialogue.topic_introduction", "authorized_task_completed"): _S,
    ("coordination.request_decline", "requested_action_prevented_or_stopped"): _S,
    ("coordination.suggestion_decline", "requested_action_prevented_or_stopped"): _S,
    ("information.explanation", "operation_failure_explained"): _D,
    ("information.uncertainty_qualification", "operation_failure_explained"): _S,
    # Features v0.3: the non-committing request handler is handled by a
    # truthful scoped decline; clarification and acceptance only progress it.
    ("coordination.request_decline", "action_request_handled"): _D,
    ("coordination.request_acceptance", "action_request_handled"): _S,
    **{(effect, "action_request_handled"): _S for effect in ELICITATION_EFFECTS},
    # Features v0.4: a conversational deliverable is met only by delivering the
    # validated artifact operation; clarification and inquiry support it, and
    # a truthful decline (content unavailable) only handles the turn.
    ("collaboration.artifact_delivery", "conversational_deliverable_delivered"): _D,
    ("collaboration.artifact_delivery", "inquiry_answer_incorporated"): _D,
    **{(effect, "conversational_deliverable_delivered"): _S for effect in ELICITATION_EFFECTS},
    ("coordination.request_decline", "conversational_deliverable_delivered"): _S,
    ("feedback.receipt", "inquiry_answer_incorporated"): _S,
    ("coordination.request_decline", "inquiry_answer_incorporated"): _S,
}
for (_effect, _requirement), _strength in GOAL_LINKS.items():
    if _strength == _D and _requirement not in REALIZATION_SATISFIABLE:
        raise RuntimeError("direct links require a realization-satisfiable requirement")

# -- discourse classes ------------------------------------------------------------
BACKWARD_EFFECTS = frozenset({
    "feedback.receipt", "feedback.paraphrase", "feedback.reflection", "feedback.misunderstanding_disclosure",
    "feedback.conversational_validation", "feedback.understanding_expression", "feedback.attention",
    "social_support.reassurance", "social_support.encouragement", "social_support.comfort",
    "social_support.experience_validation", "social_support.congratulation", "social_support.appreciation",
    "social_support.gratitude", "social_support.apology", "social_support.sympathy",
    "coordination.request_acceptance", "coordination.request_decline", "coordination.suggestion_acceptance",
    "coordination.suggestion_decline", "dialogue.opening", "dialogue.closure", "dialogue.topic_return",
    "dialogue.repair", "dialogue.self_correction", "information.uncertainty_qualification",
    "information.answer", "information.correction", "stance.agreement", "stance.disagreement",
    "stance.partial_agreement", "stance.challenge",
})
FLOOR_YIELDING_EFFECTS = frozenset({*ELICITATION_EFFECTS, "dialogue.closure", "coordination.offer"})
NON_EVIDENCE_EFFECTS = frozenset({
    *ELICITATION_EFFECTS, "feedback.receipt", "feedback.attention", "dialogue.opening", "dialogue.closure",
    "dialogue.topic_introduction", "social_play.joke", "social_play.playful_reframe", "social_play.light_tease",
    "social_play.banter", "social_play.play_along",
})
EXTERNALIZING_EFFECTS = frozenset({
    "feedback.paraphrase", "information.explanation", "information.answer", "information.example",
    "information.correction", "information.inform", "dialogue.self_correction",
    "collaboration.artifact_delivery",
})
# Effects whose grounding is the content they carry rather than their target.
BASIS_GROUNDED_EFFECTS: Mapping[str, tuple[str, ...]] = {
    "information.correction": ("basis_refs", "replacement_refs"),
    "dialogue.self_correction": ("replacement_refs",),
    "information.explanation": ("explanation_basis_refs",),
    "information.example": ("example_refs",),
    "information.answer": ("answer_content_refs",),
    "collaboration.artifact_delivery": ("basis_refs",),
}

# -- uptake salience manifest ---------------------------------------------------------
UPTAKE_SALIENCE_RULES: Mapping[str, Decimal] = {
    "uptake.request-decision.v1": ONE,
    "uptake.expressed-experience.v1": ONE,
    "uptake.negative-feedback.v1": ONE,
    "uptake.exposed-misunderstanding.v1": ONE,
    "uptake.social-contact.v1": ONE,
    "uptake.shared-personal-experience.v1": Decimal("0.50"),
}

# -- personality manifest (family/function -> trait, sign) ------------------------------
PERSONALITY_RELATIONS: Mapping[str, tuple[tuple[str, int], ...]] = {
    **{key: (("playfulness", 1),) for key in ("social_play.joke", "social_play.playful_reframe", "social_play.light_tease", "social_play.banter", "social_play.play_along")},
    **{key: (("curiosity", 1),) for key in ("seek_information.explore_goal", "seek_information.explore_preference", "seek_information.explore_reason", "seek_information.explore_experience", "seek_information.request_feedback", "seek_information.inquire")},
    **{key: (("warmth", 1),) for key in (
        "give_feedback.acknowledge", "give_feedback.reflect", "give_feedback.paraphrase", "give_feedback.validate",
        "give_feedback.signal_misunderstanding", "support_socially.reassure", "support_socially.validate_experience",
        "support_socially.congratulate", "support_socially.apologize", "support_socially.comfort",
        "manage_dialogue.greet", "manage_dialogue.close")},
    **{key: (("directness", 1),) for key in (
        "provide_information.answer", "provide_information.correct", "commit_or_coordinate.state_next_step",
        "commit_or_coordinate.decline_request", "commit_or_coordinate.decline_suggestion",
        "commit_or_coordinate.accept_request", "seek_information.clarify", "seek_information.disambiguate",
        "seek_information.verify", "manage_dialogue.self_correct", "advise_or_influence.advise",
        "provide_information.disclose_uncertainty", "collaborate.deliver_artifact")},
}

# -- state compatibility manifest ----------------------------------------------------------
_GOAL_DIRECT = frozenset({
    "elicitation.clarification", "elicitation.disambiguation", "elicitation.verification", "elicitation.evidence",
    "information.answer", "information.correction", "information.explanation", "collaboration.artifact_delivery",
})
_PLAY = frozenset({"social_play.joke", "social_play.playful_reframe", "social_play.light_tease", "social_play.banter", "social_play.play_along"})
APPRAISAL_RELATIONS: tuple[tuple[str, str, str, Decimal, frozenset[str]], ...] = (
    ("state.goal-relevance-high-direct.v1", "appraisal.goal_relevance", "high", ONE, _GOAL_DIRECT),
    ("state.goal-relevance-high-play.v1", "appraisal.goal_relevance", "high", Decimal("-1"), _PLAY),
    ("state.goal-relevance-low-play.v1", "appraisal.goal_relevance", "low", Decimal("0.5"), _PLAY),
    ("state.certainty-low-uncertainty-handling.v1", "appraisal.certainty", "low", ONE, frozenset({
        "elicitation.verification", "elicitation.disambiguation", "information.uncertainty_qualification",
        "social_support.reassurance", "feedback.reflection"})),
    ("state.certainty-high-correction.v1", "appraisal.certainty", "high", ONE, frozenset({"information.correction"})),
    ("state.controllability-high-coordination.v1", "appraisal.controllability", "high", ONE, frozenset({
        "coordination.offer", "coordination.plan_proposal", "coordination.next_step", "coordination.request_acceptance"})),
    ("state.norm-negative-refusal.v1", "appraisal.norm_compatibility", "negative", ONE, frozenset({
        "coordination.request_decline", "coordination.suggestion_decline"})),
    ("state.norm-negative-compliance.v1", "appraisal.norm_compatibility", "negative", Decimal("-1"), frozenset({
        "coordination.request_acceptance", "coordination.suggestion_acceptance"})),
    ("state.affiliative-positive-contact.v1", "appraisal.affiliative_value", "positive", ONE, frozenset({
        "dialogue.opening", "dialogue.closure"})),
    ("state.affiliative-negative-validation.v1", "appraisal.affiliative_value", "negative", ONE, frozenset({
        "social_support.experience_validation", "social_support.comfort", "social_support.sympathy"})),
    ("state.congruence-positive-completion.v1", "appraisal.goal_congruence", "positive", ONE, frozenset({
        "dialogue.closure", "dialogue.topic_return", "social_support.congratulation"})),
    ("state.congruence-negative-repair.v1", "appraisal.goal_congruence", "negative", ONE, frozenset({
        "social_support.apology", "dialogue.repair", "feedback.misunderstanding_disclosure"})),
    ("state.novelty-high-clarification.v1", "appraisal.novelty", "high", ONE, frozenset({"elicitation.clarification"})),
)
CONTROL_RELATIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("state.clarification-pressure.v1", "clarification_pressure", frozenset({
        "elicitation.clarification", "elicitation.disambiguation", "elicitation.verification", "elicitation.understanding_check"})),
    ("state.repair-pressure.v1", "repair_pressure", frozenset({
        "social_support.apology", "dialogue.repair", "dialogue.self_correction", "information.correction",
        "feedback.misunderstanding_disclosure"})),
    ("state.exploration-readiness.v1", "exploration_readiness", frozenset({
        "elicitation.goal", "elicitation.preference", "elicitation.reason", "elicitation.experience"})),
)

# -- response-cost defaults by definition -------------------------------------------------------
RESPONSE_COSTS: Mapping[str, str] = {
    "provide_information.answer": "0.15", "provide_information.inform": "0.15", "provide_information.explain": "0.30",
    "provide_information.elaborate": "0.30", "provide_information.summarize": "0.30", "provide_information.exemplify": "0.20",
    "provide_information.justify": "0.20", "provide_information.correct": "0.15", "provide_information.disclose_uncertainty": "0.15",
    "provide_information.warn": "0.20",
    "seek_information.clarify": "0.15", "seek_information.disambiguate": "0.20", "seek_information.verify": "0.10",
    "seek_information.explore_goal": "0.15", "seek_information.explore_preference": "0.15", "seek_information.request_evidence": "0.15",
    "seek_information.explore_reason": "0.15", "seek_information.explore_experience": "0.15", "seek_information.check_understanding": "0.10",
    "seek_information.request_feedback": "0.15",
    "give_feedback.acknowledge": "0.05", "give_feedback.confirm_understanding": "0.10", "give_feedback.signal_misunderstanding": "0.10",
    "give_feedback.paraphrase": "0.15", "give_feedback.reflect": "0.15", "give_feedback.validate": "0.10", "give_feedback.indicate_attention": "0.05",
    "express_stance.agree": "0.10", "express_stance.disagree": "0.15", "express_stance.partially_agree": "0.20", "express_stance.challenge": "0.20",
    "express_stance.evaluate": "0.20", "express_stance.express_preference": "0.15", "express_stance.express_uncertainty": "0.15",
    "express_stance.withhold_judgment": "0.15",
    "support_socially.reassure": "0.15", "support_socially.encourage": "0.15", "support_socially.comfort": "0.15",
    "support_socially.validate_experience": "0.10", "support_socially.congratulate": "0.05", "support_socially.appreciate": "0.05",
    "support_socially.thank": "0.05", "support_socially.apologize": "0.05", "support_socially.express_sympathy": "0.10",
    "advise_or_influence.advise": "0.20", "advise_or_influence.warn": "0.20", "advise_or_influence.discourage": "0.20",
    "advise_or_influence.request_user_action": "0.15", "advise_or_influence.set_boundary": "0.15",
    "commit_or_coordinate.offer": "0.15", "commit_or_coordinate.promise": "0.15", "commit_or_coordinate.accept_request": "0.05",
    "commit_or_coordinate.decline_request": "0.10", "commit_or_coordinate.accept_suggestion": "0.05",
    "commit_or_coordinate.decline_suggestion": "0.10", "commit_or_coordinate.propose_plan": "0.30",
    "commit_or_coordinate.state_next_step": "0.10", "commit_or_coordinate.defer": "0.10", "commit_or_coordinate.handoff": "0.15",
    "manage_dialogue.greet": "0.05", "manage_dialogue.close": "0.05", "manage_dialogue.introduce_topic": "0.10",
    "manage_dialogue.shift_topic": "0.10", "manage_dialogue.return_to_topic": "0.10", "manage_dialogue.pause": "0.05",
    "manage_dialogue.resume": "0.05", "manage_dialogue.repair": "0.15", "manage_dialogue.self_correct": "0.10",
    "manage_dialogue.request_repetition": "0.10", "manage_dialogue.redirect": "0.15",
    "social_play.joke": "0.20", "social_play.playful_reframe": "0.15", "social_play.light_tease": "0.15",
    "social_play.banter": "0.15", "social_play.play_along": "0.15",
    "collaborate.deliver_artifact": "0.30", "seek_information.inquire": "0.10",
}

# -- plan-contribution relations -----------------------------------------------------------
PLAN_RELATIONS = (
    ("plan.check-after-externalized-content.v1", ONE),
    ("plan.example-after-explanation.v1", ONE),
    ("plan.coordination-after-acceptance.v1", ONE),
    ("plan.elicitation-after-topic-introduction.v1", ONE),
    ("plan.inquiry-after-delivered-artifact.v1", ONE),
    ("plan.late-uptake-after-forward-move.v1", Decimal("-1")),
)

# -- soft-risk relations -------------------------------------------------------------------
SOFT_RISK_RELATIONS = (
    "risk.forward-move-before-uptake.v1", "risk.continues-after-floor-yield.v1",
    "risk.premature-closure.v1", "risk.humor-under-seriousness.v1", "risk.guidance-exceeds-basis.v1",
)

MANIFEST_VERSIONS = {
    "goal_links": "action-goal-links-v0.3", "personality": "action-personality-affinities-v0.2",
    "state": "action-state-compatibility-v0.2", "response_cost": "action-response-costs-v0.2",
    "soft_risk": "action-soft-risk-v0.1", "plan": "action-plan-relations-v0.2", "uptake": "action-uptake-salience-v0.1",
}


def _q(value: Decimal) -> Decimal:
    return Decimal(canonical_decimal(value))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(high, max(low, value))


@dataclass(frozen=True)
class GoalLink:
    coverage_key: str
    goal_ref: str
    requirement_ref: str
    strength: Decimal
    rule_id: str


@dataclass(frozen=True)
class CandidateAnalysis:
    """Everything the transition needs, computed once from the same manifests."""

    candidate: GroundedActionCandidate
    effect_key: str
    effect_version: int
    planned_state: str
    domain: TransitionDomain
    binding_mode: BindingMode
    ledger_keys: tuple[tuple[str, tuple], ...]
    goal_links: tuple[GoalLink, ...]
    unknown_refs: tuple[str, ...]
    contradiction_refs: tuple[str, ...]
    uptake_targets: tuple[str, ...]
    is_uptake: bool
    floor_yielding: bool
    features: tuple[ActionFeatureValue, ...] = field(default=())


class FeatureCalculator:
    def __init__(self, registry: ActionRegistry, index: ActionReferenceIndex) -> None:
        self.registry = registry
        self.index = index
        request = index.request
        self.request = request
        snapshot = request.goal_snapshot
        self.focal = tuple(request.active_goals)
        self.focal_ids = {goal.goal_id for goal in self.focal}
        # Features v0.3: every current-turn request goal the requirement
        # snapshot covers earns goal credit, not only the focal ones.  Goal
        # focus admits one focal goal per definition, so without this the
        # second question of a compound turn could never be answered.  The
        # snapshot covers only active user/shared goals formed this turn, so
        # historical goals never gain credit this way.
        handoff = request.requirement_handoff
        records = {record.goal_id: record for record in snapshot.records}
        extra = tuple(
            project_active_goal(records[ref]) for ref in (handoff.snapshot.goal_refs if handoff is not None else ())
            if ref not in self.focal_ids
        )
        self.linked = (*self.focal, *extra)
        # The exact frame each request goal was formed from (recovered by
        # requirement resolution).  An answer goal's topic target may be the
        # user's literal wording, which no Action object can cite; the frame
        # it was formed from can be.
        self.goal_frames: dict[str, tuple[str, ...]] = {}
        for resolution in (handoff.snapshot.resolutions if handoff is not None else ()):
            frames = self.goal_frames.setdefault(resolution.goal_ref, ())
            if resolution.frame_ref not in frames:
                self.goal_frames[resolution.goal_ref] = (*frames, resolution.frame_ref)
        self.primary_goal_ref = snapshot.focus_decision.primary_goal_ref
        open_status = {(item.goal_ref, item.requirement_ref): item.status for item in snapshot.requirement_assessments}
        self.open_requirements = {
            key for key, status in open_status.items() if status == GoalRequirementStatus.OPEN
        }
        self.non_focal_active = tuple(
            record for record in snapshot.records
            if record.goal_id in set(snapshot.active_goal_refs) and record.goal_id not in self.focal_ids
        )
        self.appraisal = self._appraisal_consensus()
        state = request.dynamic_state
        self.controls = state.derived_controls
        self.attention = {item.target_ref: Decimal(item.weight) / Decimal(100) for item in state.attention}
        self.uptake_members = index.uptake_members()
        self.uptake_salience, self.uptake_rules = self._uptake_salience()
        # A blocked current-turn user request changes what "responding" means
        # (features v0.2): only a move that addresses the blocker directly
        # discharges the response obligation.
        self.planning_conditions = derive_planning_conditions(request)

    # -- turn-level precomputation --------------------------------------------------
    def _appraisal_consensus(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        values: dict[str, set[str]] = {}
        statuses: dict[str, set[str]] = {}
        refs: dict[str, list[str]] = {}
        for frame in self.request.appraisal.frames:
            for dimension in frame.dimensions:
                statuses.setdefault(dimension.dimension_key, set()).add(dimension.status.value)
                refs.setdefault(dimension.dimension_key, []).append(f"{frame.frame_id}#{dimension.dimension_key}")
                if dimension.status.value == "known" and dimension.value is not None:
                    values.setdefault(dimension.dimension_key, set()).add(dimension.value)
        consensus = {}
        for key, found in values.items():
            if statuses.get(key) == {"known"} and len(found) == 1:
                consensus[key] = (next(iter(found)), tuple(refs[key]))
        return consensus

    def _uptake_salience(self) -> tuple[Decimal, tuple[str, ...]]:
        rules: list[str] = []
        for entry in self.index.entries:
            if not entry.focus_contribution or not entry.current:
                continue
            if entry.target_type in {T.REQUEST, T.SUGGESTION}:
                status = entry.attr("acceptance_status")
                if status in {"prohibited", "unauthorized", "unavailable", "partially_available"} or (
                    status == "acceptable" and (entry.target_type == T.SUGGESTION or entry.attr("fulfillment_mode") == "task")
                ):
                    rules.append("uptake.request-decision.v1")
            if entry.target_type in {T.EXPERIENCE, T.CONCERN, T.CLAIMED_AFFECT} and entry.attr("expression") == "explicit":
                rules.append("uptake.expressed-experience.v1")
            elif entry.target_type == T.EXPERIENCE:
                rules.append("uptake.shared-personal-experience.v1")
            if entry.target_type == T.OBSERVED_OUTCOME and entry.attr("assessment") == "negative":
                rules.append("uptake.negative-feedback.v1")
            if entry.target_type == T.INTERPRETATION_HYPOTHESIS and entry.attr("asserted_by") == "columbina" and entry.attr("epistemic_status") == "contradicted":
                rules.append("uptake.exposed-misunderstanding.v1")
            if entry.target_type == T.USER_ACT and entry.attr("disposition") == "leading" and entry.attr("support") == "strong" and entry.attr("act_key") in {
                "interpretation.act.greet", "interpretation.act.close",
            }:
                rules.append("uptake.social-contact.v1")
        unique = tuple(dict.fromkeys(rules))
        return max((UPTAKE_SALIENCE_RULES[rule] for rule in unique), default=ZERO), unique

    # -- relations ------------------------------------------------------------------
    def related(self, ref: str) -> frozenset[str]:
        related = set(self.index.related_refs(ref))
        for entry in self.index.entries_for_ref(ref):
            related.update(entry.attr("uncertainty_refs", ()) or ())
            related.update(entry.attr("contradicted_by_refs", ()) or ())
            if entry.attr("represents_ref"):
                related.add(entry.attr("represents_ref"))
        return frozenset(related)

    def relates(self, ref: str, value: str) -> bool:
        return value in self.related(ref) or ref in self.index.related_refs(value) or self.index.relates(ref, value)

    def principal_targets(self, candidate: GroundedActionCandidate) -> tuple:
        return candidate.targets_for("primary", "member", "scope", "source", "destination")

    # -- analysis ---------------------------------------------------------------------
    def analyze(self, candidate: GroundedActionCandidate, state: PartialPlanState) -> CandidateAnalysis:
        definition = self.registry.resolve(candidate.definition_ref)
        effect = definition.effect
        ledger_keys = self._ledger_keys(effect.effect_key, effect.effect_version, effect.planned_state, effect.binding_mode, candidate)
        links = self.goal_links(effect.effect_key, candidate)
        unknown_refs = self._uncertainty_refs(candidate) if (
            effect.transition_domain == TransitionDomain.UNKNOWN_HANDLING or effect.effect_key == "information.uncertainty_qualification"
        ) else ()
        contradiction_refs = self._contradiction_refs(candidate) if (
            effect.transition_domain == TransitionDomain.CONTRADICTION_HANDLING or effect.effect_key == "elicitation.verification"
        ) else ()
        all_refs = {item.ref for item in candidate.target_refs if item.role != "support"}
        all_refs |= {item.ref for item in candidate.supporting_evidence_refs if item.role == "locator"}
        uptake_targets = tuple(sorted(all_refs & self.uptake_members))
        is_uptake = effect.effect_key in BACKWARD_EFFECTS and bool(uptake_targets)
        return CandidateAnalysis(
            candidate=candidate, effect_key=effect.effect_key, effect_version=effect.effect_version,
            planned_state=effect.planned_state, domain=effect.transition_domain, binding_mode=effect.binding_mode,
            ledger_keys=ledger_keys, goal_links=links, unknown_refs=unknown_refs, contradiction_refs=contradiction_refs,
            uptake_targets=uptake_targets, is_uptake=is_uptake, floor_yielding=effect.effect_key in FLOOR_YIELDING_EFFECTS,
        )

    @staticmethod
    def _ledger_keys(effect_key: str, version: int, planned_state: str, mode: BindingMode, candidate: GroundedActionCandidate) -> tuple[tuple[str, tuple], ...]:
        return effect_ledger_keys(effect_key, version, planned_state, mode, candidate.target_refs)

    def goal_links(self, effect_key: str, candidate: GroundedActionCandidate) -> tuple[GoalLink, ...]:
        links: list[GoalLink] = []
        refs = [item.ref for item in candidate.target_refs if item.role != "support"]
        for goal in self.linked:
            for requirement in goal.requirements:
                if (goal.goal_id, requirement.requirement_id) not in self.open_requirements:
                    continue
                key = f"{goal.goal_id}#{requirement.requirement_id}"
                if requirement.requirement_key == RESPONSE_OBLIGATION_REQUIREMENT:
                    if not self.planning_conditions:
                        links.append(GoalLink(key, goal.goal_id, requirement.requirement_id, DIRECT_LINK, "link.response-obligation.any-move.v1"))
                    elif addresses_any(candidate, self.planning_conditions, self.registry, self.index):
                        links.append(GoalLink(key, goal.goal_id, requirement.requirement_id, DIRECT_LINK,
                                              "link.response-obligation.addresses-blocked-request.v1"))
                    else:
                        # A receipt or other generic move answers that a turn
                        # happened, not the blocked request: supporting only.
                        links.append(GoalLink(key, goal.goal_id, requirement.requirement_id, SUPPORTING_LINK,
                                              "link.response-obligation.blocked-request-generic-move.v1"))
                    continue
                strength = GOAL_LINKS.get((effect_key, requirement.requirement_key))
                if strength is None:
                    continue
                values = [target.ref for target in requirement.target_refs if target.role in REQUIREMENT_TARGET_ROLES.get(requirement.requirement_key, ())]
                values.extend(self.goal_frames.get(goal.goal_id, ()))
                if not any(self.relates(ref, value) for ref in refs for value in values):
                    continue
                links.append(GoalLink(key, goal.goal_id, requirement.requirement_id,
                                      DIRECT_LINK if strength == _D else SUPPORTING_LINK,
                                      f"link.{effect_key}.{requirement.requirement_key}.{strength}.v1"))
        return tuple(links)

    def _uncertainty_refs(self, candidate: GroundedActionCandidate) -> tuple[str, ...]:
        found: set[str] = set()
        uncertain_entries = [entry for entry in self.index.entries_of(T.UNKNOWN, T.SEMANTIC_ARGUMENT) if entry.attr("unresolved")]
        uncertain_entries.extend(self.index.entries_of(T.INQUIRY))
        for target in self.principal_targets(candidate):
            related = self.related(target.ref)
            for entry in uncertain_entries:
                if entry.ref == target.ref or entry.ref in related or target.ref in (entry.attr("tentative_value_refs", ()) or ()):
                    found.add(entry.attr("represents_ref") or entry.ref)
        return tuple(sorted(found))

    def _contradiction_refs(self, candidate: GroundedActionCandidate) -> tuple[str, ...]:
        found: set[str] = set()
        contradictions = [entry for entry in self.index.entries_of(T.CONTRADICTION) if entry.attr("status") == "active"]
        for target in self.principal_targets(candidate):
            related = set(self.related(target.ref))
            for hop in tuple(related):
                for entry in self.index.entries_for_ref(hop):
                    related |= set(entry.attr("contradicted_by_refs", ()) or ())
            for contradiction in contradictions:
                if contradiction.ref in related or target.ref in self.index.related_refs(contradiction.ref):
                    found.add(contradiction.ref)
        return tuple(sorted(found))

    # -- feature values ----------------------------------------------------------------
    def compute(self, analysis: CandidateAnalysis, state: PartialPlanState, plan_moves: tuple[GroundedActionMove, ...],
                plan_effects: tuple[PlannedActionEffect, ...], analyses_by_move: Mapping[str, CandidateAnalysis]) -> tuple[ActionFeatureValue, ...]:
        candidate = analysis.candidate
        coverage = state.ledger("planned_goal_coverage")
        target_effects = state.ledger("addressed_target_effects")
        handled = state.labels("planned_unknown_handling")
        repairs = state.labels("planned_contradiction_repairs")
        uptake = state.ledger("uptake_coverage")
        uptake_group_covered = max(uptake.values(), default=ZERO)
        principal = self.principal_targets(candidate)
        residual_by_ref: dict[str, Decimal] = {}
        for key, targets in analysis.ledger_keys:
            residual = max(ZERO, ONE - target_effects.get(key, ZERO))
            for target in targets:
                residual_by_ref[target.ref] = residual
        move_residual = max(residual_by_ref.values(), default=ZERO)

        # target priority -----------------------------------------------------------------
        priorities: dict[str, Decimal] = {}
        priority_refs: list[str] = []
        priority_rules: list[str] = []
        for target in principal:
            best = ZERO
            for goal in self.linked:
                band = GOAL_PRIORITY[goal.priority_band.value]
                for requirement in goal.requirements:
                    if (goal.goal_id, requirement.requirement_id) not in self.open_requirements:
                        continue
                    if requirement.requirement_key == RESPONSE_OBLIGATION_REQUIREMENT:
                        continue
                    values = [item.ref for item in requirement.target_refs if item.role in REQUIREMENT_TARGET_ROLES.get(requirement.requirement_key, ())]
                    values.extend(self.goal_frames.get(goal.goal_id, ()))
                    if any(self.relates(target.ref, value) for value in values):
                        achievable = ONE if requirement.requirement_key in REALIZATION_SATISFIABLE else SUPPORTING_LINK
                        covered = coverage.get(f"{goal.goal_id}#{requirement.requirement_id}", ZERO)
                        residual = max(ZERO, ONE - covered / achievable)
                        if band * residual > best:
                            best = band * residual
                        priority_refs.append(requirement.requirement_id)
                        priority_rules.append("priority.focal-requirement-residual.v1")
            if target.ref in self.attention:
                best = max(best, self.attention[target.ref])
                priority_refs.append(self.index.attention_ref(target.target_type.value, target.ref))
                priority_rules.append("priority.dynamic-state-attention.v1")
            if target.ref in self.uptake_members and self.uptake_salience > 0:
                best = max(best, self.uptake_salience * (ONE - uptake_group_covered))
                priority_rules.append("priority.uptake-salience.v1")
            priorities[target.ref] = best
        weighted = {ref: priorities[ref] * residual_by_ref.get(ref, move_residual) for ref in priorities}
        tp = self._weighted_mean(weighted, weighted)
        tp_status = FeatureStatus.KNOWN if priority_rules else FeatureStatus.MISSING

        # goal fit and expected progress ---------------------------------------------------------
        fit = ZERO
        progress = ZERO
        link_rules = []
        for link in analysis.goal_links:
            current = coverage.get(link.coverage_key, ZERO)
            if current < link.strength:
                fit = max(fit, link.strength)
            progress += max(ZERO, link.strength - current)
            link_rules.append(link.rule_id)
        progress = min(ONE, progress) * move_residual
        link_refs = tuple(link.requirement_ref for link in analysis.goal_links)

        # uncertainty reduction ----------------------------------------------------------------------
        ur = ZERO
        ur_status = FeatureStatus.INAPPLICABLE
        ur_refs: list[str] = []
        if analysis.unknown_refs or analysis.domain == TransitionDomain.UNKNOWN_HANDLING or analysis.effect_key == "information.uncertainty_qualification":
            ur_status = FeatureStatus.KNOWN if analysis.unknown_refs else FeatureStatus.MISSING
            qualification = analysis.effect_key == "information.uncertainty_qualification"
            for ref in analysis.unknown_refs:
                label = handled.get(ref)
                already = (label == "qualification_planned") if qualification else (label in {"elicitation_planned", "verification_planned"})
                if already:
                    continue
                ur = max(ur, self._uncertainty_relevance(ref))
                ur_refs.append(ref)
            ur = ur * move_residual

        # contradiction repair ----------------------------------------------------------------------------
        cr = ZERO
        cr_status = FeatureStatus.INAPPLICABLE
        if analysis.domain == TransitionDomain.CONTRADICTION_HANDLING or analysis.effect_key == "elicitation.verification":
            cr_status = FeatureStatus.KNOWN if analysis.contradiction_refs else FeatureStatus.MISSING
            for ref in analysis.contradiction_refs:
                if ref in repairs:
                    continue
                cr = max(cr, self._contradiction_relevance(ref))
            cr = cr * move_residual

        # evidence support ------------------------------------------------------------------------------------
        es, es_status, es_refs = self._evidence_support(analysis, weighted)

        # personality affinity ----------------------------------------------------------------------------------
        pa, pa_status, pa_rules = self._personality(candidate.definition_ref.action_key)

        # state compatibility -------------------------------------------------------------------------------------
        sc, sc_status, sc_refs, sc_rules = self._state(analysis.effect_key)

        # plan contribution ---------------------------------------------------------------------------------------
        pc, pc_status, pc_rules, pc_refs = self._plan_contribution(analysis, plan_moves, plan_effects, analyses_by_move)

        # repetition ---------------------------------------------------------------------------------------------------
        rep, rep_rules, rep_refs = self._repetition(analysis, plan_moves, analyses_by_move)

        # response cost ---------------------------------------------------------------------------------------------------
        rc = Decimal(RESPONSE_COSTS[candidate.definition_ref.action_key])

        # soft risk -------------------------------------------------------------------------------------------------------
        risk, risk_rules, risk_refs = self._soft_risk(analysis, candidate, state, plan_moves, analyses_by_move)

        def value(key: F, number: Decimal, status: FeatureStatus, refs=(), rule: str = "", manifest=()) -> ActionFeatureValue:
            number = number if status == FeatureStatus.KNOWN else ZERO
            return ActionFeatureValue(feature_key=key, value=canonical_decimal(_q(number)), status=status,
                                      provenance_refs=tuple(dict.fromkeys(ref for ref in refs if ref)),
                                      calculator_rule_id=rule, manifest_entry_ids=tuple(dict.fromkeys(manifest)))

        return (
            value(F.SEMANTIC_FIT, Decimal(candidate.binding_fit), FeatureStatus.KNOWN, candidate.source_refs, candidate.binding_fit_rule_id),
            value(F.TARGET_PRIORITY, tp, tp_status, priority_refs, "feature.target-priority.residual-weighted-mean.v1", priority_rules),
            value(F.GOAL_FIT, fit, FeatureStatus.KNOWN if analysis.goal_links else FeatureStatus.MISSING, link_refs, "feature.goal-fit.max-residual-link.v1", link_rules),
            value(F.EXPECTED_GOAL_PROGRESS, progress, FeatureStatus.KNOWN if analysis.goal_links else FeatureStatus.MISSING, link_refs, "feature.goal-progress.capped-marginal-coverage.v1", link_rules),
            value(F.UNCERTAINTY_REDUCTION, ur, ur_status, ur_refs, "feature.uncertainty-reduction.goal-relevance.v1"),
            value(F.CONTRADICTION_REPAIR, cr, cr_status, analysis.contradiction_refs, "feature.contradiction-repair.residual.v1"),
            value(F.EVIDENCE_SUPPORT, es, es_status, es_refs, "feature.evidence-support.grounding-strength.v1"),
            value(F.PERSONALITY_AFFINITY, pa, pa_status, (self.request.personality.profile_id,), "feature.personality-affinity.centered-trait.v1", pa_rules),
            value(F.STATE_COMPATIBILITY, sc, sc_status, sc_refs, "feature.state-compatibility.known-state-mean.v1", sc_rules),
            value(F.PLAN_CONTRIBUTION, pc, pc_status, pc_refs, "feature.plan-contribution.relations.v1", pc_rules),
            value(F.REPETITION_COST, rep, FeatureStatus.KNOWN, rep_refs, "feature.repetition.max-schedule.v1", rep_rules),
            value(F.RESPONSE_COST, rc, FeatureStatus.KNOWN, (candidate.definition_ref.identity,), "feature.response-cost.definition-default.v1", (MANIFEST_VERSIONS["response_cost"],)),
            value(F.SOFT_RISK_COST, risk, FeatureStatus.KNOWN, risk_refs, "feature.soft-risk.max-relation.v1", risk_rules),
        )

    @staticmethod
    def _weighted_mean(values: Mapping[str, Decimal], weights: Mapping[str, Decimal]) -> Decimal:
        if len(values) == 1:
            return next(iter(values.values()))
        total = sum(weights.values(), ZERO)
        if total == 0:
            return ZERO
        return sum((values[key] * weights[key] for key in values), ZERO) / total

    def _uncertainty_relevance(self, ref: str) -> Decimal:
        best = ZERO
        for entry in self.index.entries_for_ref(ref):
            if entry.target_type == T.INQUIRY:
                best = max(best, INQUIRY_UNCERTAINTY_RELEVANCE.get(entry.attr("inquiry_criticality"), ZERO))
        if best:
            return best
        for goal in self.focal:
            for requirement in goal.requirements:
                if requirement.requirement_key != "required_argument_resolved" or (goal.goal_id, requirement.requirement_id) not in self.open_requirements:
                    continue
                if any(target.role == "argument_ref" and (target.ref == ref or self.relates(target.ref, ref)) for target in requirement.target_refs):
                    best = max(best, GOAL_PRIORITY[goal.priority_band.value])
        if best == 0:
            for record in self.non_focal_active:
                for requirement in record.requirements:
                    if requirement.requirement_key == "required_argument_resolved" and any(
                        target.role == "argument_ref" and (target.ref == ref or self.relates(target.ref, ref)) for target in requirement.target_refs
                    ):
                        best = max(best, NON_FOCAL_UNCERTAINTY_WEIGHT * GOAL_PRIORITY[record.priority_band.value])
        return best

    def _contradiction_relevance(self, ref: str) -> Decimal:
        best = ZERO
        for goal in self.focal:
            for requirement in goal.requirements:
                if requirement.requirement_key == "material_contradiction_resolved" and (goal.goal_id, requirement.requirement_id) in self.open_requirements and any(
                    target.ref == ref for target in requirement.target_refs
                ):
                    best = max(best, GOAL_PRIORITY[goal.priority_band.value])
        return best or UNLINKED_CONTRADICTION_WEIGHT

    def _support_of(self, ref: str) -> Decimal | None:
        entries = self.index.entries_for_ref(ref)
        for entry in entries:
            status = entry.attr("epistemic_status")
            if status in {"known_false", "contradicted"} and entry.attr("asserted_by") != "columbina":
                return Decimal("-1")
            support = entry.attr("support")
            if support in SUPPORT:
                return SUPPORT[support]
            if status == "supported":
                return SUPPORT["strong"]
            if status == "tentative":
                return SUPPORT["moderate"]
            if status == "contradicted" and entry.attr("asserted_by") == "columbina":
                return SUPPORT["strong"]
            if entry.target_type in {T.PRIOR_ACTION, T.PRIOR_REALIZATION} and entry.attr("correction_status") == "explicitly_corrected":
                return SUPPORT["strong"]
            if entry.target_type in {T.OBSERVED_OUTCOME, T.CAPABILITY}:
                return SUPPORT["strong"]
            if entry.target_type in {T.EXPERIENCE, T.CONCERN, T.CLAIMED_AFFECT} and entry.attr("expression") == "explicit":
                return SUPPORT["strong"]
            if entry.target_type == T.REQUEST or entry.target_type == T.SUGGESTION:
                reasons = entry.attr("decline_reason_refs", ()) or ()
                if entry.attr("acceptance_status") in {"acceptable"}:
                    return SUPPORT["strong"]
                if reasons:
                    return SUPPORT["strong"]
            if entry.target_type == T.TOPIC and entry.attr("topic_status") == "prior_active":
                return SUPPORT["strong"]
            if entry.attr("basis_refs"):
                return SUPPORT["moderate"]
        return None

    def _evidence_support(self, analysis: CandidateAnalysis, weights: Mapping[str, Decimal]) -> tuple[Decimal, FeatureStatus, tuple[str, ...]]:
        if analysis.effect_key in NON_EVIDENCE_EFFECTS:
            return ZERO, FeatureStatus.INAPPLICABLE, ()
        candidate = analysis.candidate
        principal = self.principal_targets(candidate)
        values: dict[str, Decimal] = {}
        refs: list[str] = []
        basis_field = BASIS_GROUNDED_EFFECTS.get(analysis.effect_key)
        for target in principal:
            if basis_field is not None:
                basis = [ref for entry in self.index.entries_for_ref(target.ref) for name in basis_field
                         for ref in (entry.attr(name, ()) or ())]
                basis.extend(ref for name in ("basis_refs", "correction_content_refs") for ref in candidate.parameters.get(name, ()))
                supports = [value for value in (self._support_of(ref) for ref in dict.fromkeys(basis)) if value is not None]
                if supports:
                    values[target.ref] = max(supports)
                    refs.extend(dict.fromkeys(basis))
                continue
            support = self._support_of(target.ref)
            if support is not None:
                values[target.ref] = support
                refs.append(target.ref)
        if not values:
            return ZERO, FeatureStatus.MISSING, ()
        if candidate.definition_ref.action_key == "advise_or_influence.advise":
            strength = candidate.parameters.get("guidance_strength")
            required = GUIDANCE_REQUIRED_SUPPORT.get(strength, ZERO)
            values = {key: _clamp(value - required, Decimal("-1"), ONE) for key, value in values.items()}
        chosen_weights = {key: weights.get(key, ZERO) for key in values}
        return _clamp(self._weighted_mean(values, chosen_weights), Decimal("-1"), ONE), FeatureStatus.KNOWN, tuple(refs)

    def _personality(self, action_key: str) -> tuple[Decimal, FeatureStatus, tuple[str, ...]]:
        relations = PERSONALITY_RELATIONS.get(action_key)
        if not relations:
            return ZERO, FeatureStatus.INAPPLICABLE, ()
        profile = self.request.personality
        if profile.status.value != "known":
            return ZERO, FeatureStatus.MISSING, (f"personality.{action_key}",)
        total = ZERO
        for trait, sign in relations:
            affinity = profile.affinity(trait)
            total += Decimal(sign) * (Decimal(2) * (affinity - Decimal("0.5")))
        return _clamp(total / Decimal(len(relations)), Decimal("-1"), ONE), FeatureStatus.KNOWN, (f"personality.{action_key}",)

    def _state(self, effect_key: str) -> tuple[Decimal, FeatureStatus, tuple[str, ...], tuple[str, ...]]:
        values: list[Decimal] = []
        refs: list[str] = []
        rules: list[str] = []
        for rule_id, dimension, expected, number, effects in APPRAISAL_RELATIONS:
            if effect_key not in effects:
                continue
            known = self.appraisal.get(dimension)
            if known is not None and known[0] == expected:
                values.append(number); refs.extend(known[1]); rules.append(rule_id)
        if self.controls is not None:
            for rule_id, control, effects in CONTROL_RELATIONS:
                if effect_key in effects:
                    values.append(CONTROL_BANDS[getattr(self.controls, control).value])
                    refs.append(self.index.control_ref(control)); rules.append(rule_id)
        if not values:
            return ZERO, FeatureStatus.MISSING, (), ()
        return _clamp(sum(values, ZERO) / Decimal(len(values)), Decimal("-1"), ONE), FeatureStatus.KNOWN, tuple(refs), tuple(rules)

    def _plan_contribution(self, analysis, plan_moves, plan_effects, analyses_by_move) -> tuple[Decimal, FeatureStatus, tuple[str, ...], tuple[str, ...]]:
        if not plan_moves:
            return ZERO, FeatureStatus.INAPPLICABLE, (), ()
        found: list[tuple[str, Decimal, str]] = []
        related = set().union(*(self.related(item.ref) | {item.ref} for item in self.principal_targets(analysis.candidate)))
        for move in plan_moves:
            prior = analyses_by_move[move.move_id]
            prior_refs = set().union(*(self.related(item.ref) | {item.ref} for item in self.principal_targets(prior.candidate)))
            overlap = bool(related & prior_refs)
            if analysis.effect_key == "elicitation.understanding_check" and prior.effect_key in EXTERNALIZING_EFFECTS and overlap:
                found.append(("plan.check-after-externalized-content.v1", ONE, move.move_id))
            if analysis.effect_key == "information.example" and prior.effect_key in {"information.explanation", "information.answer"} and overlap:
                found.append(("plan.example-after-explanation.v1", ONE, move.move_id))
            if analysis.effect_key in {"coordination.next_step", "coordination.plan_proposal"} and prior.effect_key in {
                "coordination.request_acceptance", "coordination.suggestion_acceptance",
            }:
                pursued = {entry.attr("pursues_ref") for item in self.principal_targets(analysis.candidate) for entry in self.index.entries_for_ref(item.ref)}
                if pursued & {item.ref for item in prior.candidate.target_refs}:
                    found.append(("plan.coordination-after-acceptance.v1", ONE, move.move_id))
            if analysis.domain == TransitionDomain.UNKNOWN_HANDLING and prior.effect_key == "dialogue.topic_introduction" and overlap:
                found.append(("plan.elicitation-after-topic-introduction.v1", ONE, move.move_id))
            if analysis.effect_key == "elicitation.inquiry" and prior.effect_key == "collaboration.artifact_delivery":
                pursued = {entry.attr("pursues_ref") for item in self.principal_targets(analysis.candidate)
                           for entry in self.index.entries_for_ref(item.ref)}
                if pursued & {item.ref for item in prior.candidate.target_refs}:
                    found.append(("plan.inquiry-after-delivered-artifact.v1", ONE, move.move_id))
            if analysis.is_uptake and not prior.is_uptake:
                found.append(("plan.late-uptake-after-forward-move.v1", Decimal("-1"), move.move_id))
        if not found:
            return ZERO, FeatureStatus.INAPPLICABLE, (), ()
        negative = [item for item in found if item[1] < 0]
        chosen = min(negative, key=lambda item: item[1]) if negative else max(found, key=lambda item: item[1])
        return chosen[1], FeatureStatus.KNOWN, tuple(item[0] for item in found), tuple(item[2] for item in found)

    def _repetition(self, analysis, plan_moves, analyses_by_move) -> tuple[Decimal, tuple[str, ...], tuple[str, ...]]:
        candidate = analysis.candidate
        values: list[tuple[Decimal, str, str]] = []
        targets = {item.ref for item in self.principal_targets(candidate)}
        family = candidate.definition_ref.action_key.partition(".")[0]
        for move in plan_moves:
            prior = analyses_by_move[move.move_id]
            prior_targets = {item.ref for item in self.principal_targets(prior.candidate)}
            if prior.candidate.lexical_identity[0] == candidate.lexical_identity[0] and prior.candidate.target_refs == candidate.target_refs and prior.candidate.normalized_parameters == candidate.normalized_parameters:
                values.append((ONE, "repetition.exact-in-plan.v1", move.move_id))
            elif prior.effect_key == analysis.effect_key and targets & prior_targets:
                values.append((Decimal("0.75"), "repetition.same-effect-same-target.v1", move.move_id))
            elif prior.candidate.definition_ref.action_key.partition(".")[0] == family and targets & prior_targets and prior.domain == analysis.domain:
                values.append((Decimal("0.50"), "repetition.same-family-overlapping-effect.v1", move.move_id))
        for record in self.request.prior_action_records:
            if record.definition_ref.action_key == candidate.definition_ref.action_key and {item.ref for item in record.target_refs} == targets:
                values.append((Decimal("0.35"), "repetition.previous-turn-exact.v1", record.record_id))
        if not values:
            return ZERO, (), ()
        best = max(item[0] for item in values)
        return best, tuple(item[1] for item in values), tuple(item[2] for item in values)

    def _soft_risk(self, analysis, candidate, state, plan_moves, analyses_by_move) -> tuple[Decimal, tuple[str, ...], tuple[str, ...]]:
        risks: list[tuple[Decimal, str, str]] = []
        uptake = state.ledger("uptake_coverage")
        group_covered = max(uptake.values(), default=ZERO)
        if not analysis.is_uptake and self.uptake_salience > 0 and group_covered < 1:
            risks.append((self.uptake_salience * (ONE - group_covered), "risk.forward-move-before-uptake.v1", self.index.focus_message_id))
        if not analysis.floor_yielding and any(analyses_by_move[move.move_id].floor_yielding for move in plan_moves):
            risks.append((ONE, "risk.continues-after-floor-yield.v1", plan_moves[-1].move_id))
        if analysis.effect_key == "dialogue.closure":
            own = set(analysis.uptake_targets)
            pending = [ref for ref in sorted(self.uptake_members - own) if uptake.get(ref, ZERO) < 1
                       and any(entry.target_type not in {T.USER_ACT, T.MESSAGE} for entry in self.index.entries_for_ref(ref))]
            if pending:
                risks.append((ONE, "risk.premature-closure.v1", pending[0]))
        if analysis.effect_key.startswith("social_play."):
            serious = []
            relevance = self.appraisal.get("appraisal.goal_relevance")
            if relevance is not None and relevance[0] == "high":
                serious.extend(relevance[1])
            serious.extend(entry.ref for entry in self.index.entries if entry.attr("humor_declined") is True and entry.current)
            if serious:
                risks.append((ONE, "risk.humor-under-seriousness.v1", serious[0]))
        if candidate.definition_ref.action_key == "advise_or_influence.advise":
            strength = candidate.parameters.get("guidance_strength")
            supports = [self.index.entries_for_ref(item.ref) for item in candidate.targets_for("member")]
            basis = max((SUPPORT_ORDER.get(entry.attr("support"), 0) for entries in supports for entry in entries), default=0)
            excess = max(0, GUIDANCE_ORDER.index(strength) - basis) if strength in GUIDANCE_ORDER else 0
            if excess:
                risks.append((min(ONE, Decimal("0.25") * excess), "risk.guidance-exceeds-basis.v1", candidate.candidate_id))
        if not risks:
            return ZERO, (), ()
        best = max(item[0] for item in risks)
        return min(ONE, best), tuple(item[1] for item in risks), tuple(item[2] for item in risks)


__all__ = ["CandidateAnalysis", "FEATURE_VERSION", "FeatureCalculator", "GOAL_LINKS", "MANIFEST_VERSIONS",
           "PERSONALITY_RELATIONS", "RESPONSE_COSTS", "APPRAISAL_RELATIONS", "CONTROL_RELATIONS", "GoalLink"]
