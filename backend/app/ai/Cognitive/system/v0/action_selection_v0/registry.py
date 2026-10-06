"""Typed static registry for ``conversational-actions-v0.2-shadow``.

The rows below are compiled from ``data/CONVERSATIONAL_ACTION_REGISTRY_V0.md``
by hand.  Production code never parses that Markdown or the design fixture;
parity is enforced by tests instead.  Every definition is version 1, every
effect has strength 1.0, and no definition is approved for live selection.

v0.2 (Collaborative Content and Inquiry V0) adds two shadow definitions:
``collaborate.deliver_artifact`` (one parameterized definition for every
artifact operation, carrying a validated collaborative-content proposal) and
``seek_information.inquire`` (a typed inquiry with explicit timing and
criticality).  Every earlier definition is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..contracts.actions import (
    ACTION_REGISTRY_VERSION, ActionDefinition, ActionDefinitionRef, ActionEffectDefinition,
    ActionEvidenceRequirement, ActionFamily, ActionLifecycle, ActionParameterRule, ActionTargetSchema,
    ActionTargetType as T, BindingMode as B, ParameterValueType as P, ReferenceSourceKind as S,
    TransitionDomain as D, evidence_turn_boundaries,
)

REGISTRY_VERSION = ACTION_REGISTRY_VERSION
DEFINITION_VERSION = 1

SHADOW_KEYS: frozenset[str] = frozenset({
    "advise_or_influence.advise", "commit_or_coordinate.accept_request",
    "commit_or_coordinate.accept_suggestion", "commit_or_coordinate.decline_request",
    "commit_or_coordinate.decline_suggestion", "commit_or_coordinate.offer",
    "commit_or_coordinate.propose_plan", "commit_or_coordinate.state_next_step",
    "give_feedback.acknowledge", "give_feedback.paraphrase", "give_feedback.reflect",
    "give_feedback.signal_misunderstanding", "manage_dialogue.close", "manage_dialogue.greet",
    "manage_dialogue.introduce_topic", "manage_dialogue.repair", "manage_dialogue.return_to_topic",
    "manage_dialogue.self_correct", "provide_information.answer", "provide_information.correct",
    "provide_information.disclose_uncertainty", "provide_information.exemplify",
    "provide_information.explain", "seek_information.check_understanding",
    "seek_information.clarify", "seek_information.disambiguate",
    "seek_information.explore_experience", "seek_information.explore_goal",
    "seek_information.explore_preference", "seek_information.explore_reason",
    "seek_information.request_evidence", "seek_information.request_feedback",
    "seek_information.verify", "social_play.joke", "social_play.playful_reframe",
    "support_socially.apologize", "support_socially.congratulate", "support_socially.reassure",
    "support_socially.validate_experience",
    # Registry v0.2.
    "collaborate.deliver_artifact", "seek_information.inquire",
})
BOUNDARY_ONLY_KEYS: frozenset[str] = frozenset({
    "advise_or_influence.set_boundary", "advise_or_influence.warn", "commit_or_coordinate.promise",
    "express_stance.agree", "express_stance.challenge", "express_stance.disagree",
    "give_feedback.confirm_understanding", "give_feedback.validate", "provide_information.elaborate",
    "provide_information.inform", "provide_information.summarize", "support_socially.comfort",
})
UNCOVERED_KEYS: frozenset[str] = frozenset({
    "advise_or_influence.discourage", "advise_or_influence.request_user_action",
    "commit_or_coordinate.defer", "commit_or_coordinate.handoff", "express_stance.evaluate",
    "express_stance.express_preference", "express_stance.express_uncertainty",
    "express_stance.partially_agree", "express_stance.withhold_judgment",
    "give_feedback.indicate_attention", "manage_dialogue.pause", "manage_dialogue.redirect",
    "manage_dialogue.request_repetition", "manage_dialogue.resume", "manage_dialogue.shift_topic",
    "provide_information.justify", "provide_information.warn", "social_play.banter",
    "social_play.light_tease", "social_play.play_along", "support_socially.appreciate",
    "support_socially.encourage", "support_socially.express_sympathy", "support_socially.thank",
})

FAMILY_OUTCOMES: Mapping[ActionFamily, tuple[str, ...]] = {
    ActionFamily.PROVIDE_INFORMATION: ("goal_resolution", "clarity", "confusion", "realization_fidelity"),
    ActionFamily.SEEK_INFORMATION: ("clarity", "goal_resolution", "engagement", "explicit_user_correction", "confusion"),
    ActionFamily.GIVE_FEEDBACK: ("clarity", "social_alignment", "engagement", "explicit_user_correction", "realization_fidelity"),
    ActionFamily.EXPRESS_STANCE: ("clarity", "social_alignment", "engagement", "realization_fidelity"),
    ActionFamily.SUPPORT_SOCIALLY: ("social_alignment", "engagement", "clarity", "realization_fidelity"),
    ActionFamily.ADVISE_OR_INFLUENCE: ("goal_resolution", "clarity", "social_alignment", "engagement", "realization_fidelity"),
    ActionFamily.COMMIT_OR_COORDINATE: ("goal_resolution", "clarity", "social_alignment", "realization_fidelity"),
    ActionFamily.MANAGE_DIALOGUE: ("clarity", "engagement", "social_alignment", "unnecessary_repetition", "realization_fidelity"),
    ActionFamily.SOCIAL_PLAY: ("engagement", "social_alignment", "confusion", "realization_fidelity"),
    ActionFamily.COLLABORATE: ("goal_resolution", "clarity", "explicit_user_correction", "realization_fidelity"),
}

SHARED_HARD_INHIBITORS = (
    "registry_lifecycle", "schema_reference_integrity", "truth_evidence", "capability",
    "authorization_consent_privacy", "application_safety",
)

QUESTION_FORMS = ("open_set", "yes_no", "choice", "check", "scale", "free_reflection")
_ANY_CONTENT = (T.PROPOSITION, T.SEMANTIC_FRAME, T.EVIDENCE_RECORD, T.OBSERVED_OUTCOME, T.MESSAGE)
_REASONS = (T.PROPOSITION, T.EVIDENCE_RECORD, T.OBSERVED_OUTCOME, T.CAPABILITY, T.CONCERN)


def _enum(key: str, values: tuple[str, ...], *, required: bool = False, when: Mapping[str, str] | None = None) -> ActionParameterRule:
    return ActionParameterRule(parameter_key=key, value_type=P.ENUM, allowed_values=values, required=required, required_when=when)


def _ref(key: str, types: tuple[T, ...], *, required: bool = False) -> ActionParameterRule:
    return ActionParameterRule(parameter_key=key, value_type=P.REFERENCE, reference_target_types=types, required=required)


def _refs(key: str, types: tuple[T, ...], *, required: bool = False, when: Mapping[str, str] | None = None) -> ActionParameterRule:
    return ActionParameterRule(parameter_key=key, value_type=P.REFERENCE_ARRAY, reference_target_types=types, required=required, required_when=when)


def _number(key: str, *, when: Mapping[str, str] | None = None) -> ActionParameterRule:
    return ActionParameterRule(parameter_key=key, value_type=P.NUMBER, required_when=when)


CONTENT_SCOPE = lambda: _enum("content_scope", ("minimal", "focused", "broad", "requested"))  # noqa: E731
CONDITIONALITY = lambda: _enum("conditionality", ("unconditional", "conditional", "hypothetical"))  # noqa: E731
COMMITMENT = lambda required=False: _enum("commitment_level", ("tentative", "bounded", "firm"), required=required)  # noqa: E731
SCOPE = lambda required=False: _ref("scope_ref", (  # noqa: E731
    T.TOPIC, T.DIALOGUE_SCOPE, T.SEMANTIC_FRAME, T.PROPOSITION, T.GOAL, T.PLAN, T.PLAN_STEP,
    T.BOUNDARY_SCOPE, T.OBSERVED_OUTCOME, T.PRIOR_ACTION, T.PRIOR_REALIZATION, T.EXPERIENCE, T.CONCERN,
), required=required)
BASIS = lambda required=False: _refs("basis_refs", _ANY_CONTENT, required=required)  # noqa: E731
REASONS = lambda required=False: _refs("reason_refs", _REASONS, required=required)  # noqa: E731


def _question_schema(*, forms: tuple[str, ...] = QUESTION_FORMS) -> tuple[ActionParameterRule, ...]:
    return (
        _enum("question_form", forms, required=True),
        _refs("candidate_answer_refs", (T.OPTION, T.PROPOSITION, T.TOPIC, T.OBSERVED_OUTCOME), when={"question_form": "choice"}),
        _number("scale_min", when={"question_form": "scale"}),
        _number("scale_max", when={"question_form": "scale"}),
    )


# (key, description, accepted types, min, max, parameters, evidence, structural, extra inhibitors,
#  (effect key, domain, planned state, binding mode, extra roles))
_ROWS: tuple[tuple, ...] = (
    # provide_information --------------------------------------------------
    ("provide_information.answer", "Supply content responsive to a represented question or requested proposition.",
     (T.QUESTION, T.PROPOSITION), 1, 1, (CONTENT_SCOPE(),), ("supported_answer_content",), ("represented_information_request",), (),
     ("information.answer", D.TARGET_EFFECT, "content_delivery_planned", B.PRIMARY_TARGET, {})),
    ("provide_information.inform", "Add relevant information not structurally framed as a direct answer.",
     (T.PROPOSITION, T.SEMANTIC_FRAME), 1, None, (CONTENT_SCOPE(), CONDITIONALITY()), ("content_provenance",), (), ("stronger_contradicting_evidence",),
     ("information.inform", D.TARGET_EFFECT, "content_delivery_planned", B.EACH_TARGET, {})),
    ("provide_information.explain", "Make causes, mechanisms, or relationships understandable.",
     (T.PROPOSITION, T.SEMANTIC_FRAME, T.TOPIC), 1, 1, (SCOPE(), BASIS()), ("explanation_basis",), (), (),
     ("information.explanation", D.TARGET_EFFECT, "explanation_planned", B.PRIMARY_TARGET, {})),
    ("provide_information.elaborate", "Add relevant detail to content already introduced.",
     (T.PRIOR_MOVE, T.PROPOSITION, T.TOPIC), 1, 1, (CONTENT_SCOPE(),), ("prior_content", "non_duplicate_detail"), (), (),
     ("information.elaboration", D.TARGET_EFFECT, "elaboration_planned", B.PRIMARY_TARGET, {})),
    ("provide_information.summarize", "Compress a bounded accessible source while preserving its important content.",
     (T.SEMANTIC_FRAME, T.TOPIC, T.PRIOR_ACTION, T.CONTENT_SOURCE), 1, None, (CONTENT_SCOPE(),), ("accessible_source",), (), (),
     ("information.summary", D.TARGET_EFFECT, "summary_planned", B.TARGET_SET, {})),
    ("provide_information.exemplify", "Supply a grounded example of an abstract claim or explanation.",
     (T.PROPOSITION, T.TOPIC), 1, 1, (SCOPE(),), ("grounded_example", "externalized_claim"), (), (),
     ("information.example", D.TARGET_EFFECT, "example_planned", B.PRIMARY_TARGET, {})),
    ("provide_information.justify", "Provide actual reasons or evidence supporting Columbina's claim, stance, recommendation, or action.",
     (T.PRIOR_MOVE, T.PROPOSITION, T.COMMITMENT), 1, 1, (BASIS(required=True),), ("actual_supporting_basis",), (), (),
     ("information.justification", D.TARGET_EFFECT, "justification_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("provide_information.correct", "Replace or qualify a proposition using stronger contradictory evidence.",
     (T.PROPOSITION, T.SEMANTIC_FRAME, T.PRIOR_REALIZATION), 1, 1, (BASIS(required=True), SCOPE()), ("correction_content", "contradiction_evidence"), (), (),
     ("information.correction", D.CONTRADICTION_HANDLING, "correction_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("provide_information.disclose_uncertainty", "Qualify information Columbina is providing as uncertain.",
     (T.PROPOSITION, T.SEMANTIC_FRAME, T.INTERPRETATION_HYPOTHESIS), 1, None, (CONDITIONALITY(), BASIS()), ("represented_uncertainty",), (), (),
     ("information.uncertainty_qualification", D.TARGET_EFFECT, "qualification_planned", B.EACH_TARGET, {})),
    ("provide_information.warn", "Communicate supported risk or adverse consequence.",
     (T.PROPOSITION, T.CONCERN, T.OBSERVED_OUTCOME), 1, None, (BASIS(), SCOPE()), ("supported_risk",), (), (),
     ("information.risk_disclosure", D.TARGET_EFFECT, "risk_disclosure_planned", B.EACH_TARGET, {})),
    # seek_information -----------------------------------------------------
    ("seek_information.clarify", "Resolve a missing argument, unfamiliar term, underspecified scope, or unclear content.",
     (T.UNKNOWN, T.SEMANTIC_ARGUMENT), 1, 1, _question_schema(), ("represented_unresolved_content",), (), (),
     ("elicitation.clarification", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.disambiguate", "Choose among represented competing referents or meanings.",
     (T.COMPETING_HYPOTHESIS_SET, T.UNKNOWN, T.SEMANTIC_ARGUMENT), 1, 1, _question_schema(), ("two_supported_candidates",), (), (),
     ("elicitation.disambiguation", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_WITH_SUPPORT, {"support": (T.OPTION, T.PROPOSITION, T.TOPIC, T.INTERPRETATION_HYPOTHESIS)})),
    ("seek_information.verify", "Test whether a tentative proposition or assumption is correct.",
     (T.PROPOSITION, T.INTERPRETATION_HYPOTHESIS), 1, 1, (*_question_schema(), BASIS()), ("tentative_status",), (), (),
     ("elicitation.verification", D.UNKNOWN_HANDLING, "verification_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("seek_information.explore_goal", "Learn what outcome the user wants.",
     (T.GOAL, T.UNKNOWN), 1, 1, _question_schema(), ("underspecified_goal",), (), (),
     ("elicitation.goal", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.explore_preference", "Learn a choice, ranking, or degree preferred by the user.",
     (T.UNKNOWN, T.OPTION_SET, T.SEMANTIC_ARGUMENT), 1, 1, _question_schema(), ("represented_preference_gap",), (), ("invented_preference",),
     ("elicitation.preference", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_WITH_SUPPORT, {"support": (T.OPTION, T.PROPOSITION, T.TOPIC)})),
    ("seek_information.request_evidence", "Ask for support for a claim or interpretation.",
     (T.PROPOSITION, T.INTERPRETATION_HYPOTHESIS, T.EVIDENCE_RECORD), 1, 1,
     (*_question_schema(), _enum("requested_evidence_type", ("document", "record", "observation", "source", "other"))), ("support_matters",), (), (),
     ("elicitation.evidence", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.explore_reason", "Invite reasons or reasoning behind a stance, choice, or claim.",
     (T.PROPOSITION, T.GOAL, T.OPTION, T.PRIOR_ACTION), 1, 1, _question_schema(), ("represented_stance_or_choice",), (), (),
     ("elicitation.reason", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.explore_experience", "Invite description of an event or lived experience without asserting hidden affect.",
     (T.EXPERIENCE, T.INTERPRETATION_HYPOTHESIS, T.SEMANTIC_ARGUMENT), 1, 1, _question_schema(), ("evidenced_experience",), (), ("asserted_hidden_affect",),
     ("elicitation.experience", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.check_understanding", "Ask the user to confirm or correct Columbina's tentative interpretation.",
     (T.INTERPRETATION_HYPOTHESIS, T.PROPOSITION), 1, 1, _question_schema(forms=("check",)), ("explicit_tentative_interpretation",), ("externalized_content",), (),
     ("elicitation.understanding_check", D.UNKNOWN_HANDLING, "verification_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.inquire", "Ask a typed inquiry with a declared purpose, expected use, criticality, and timing.",
     (T.INQUIRY,), 1, 1,
     (_enum("question_form", ("open_set", "yes_no", "choice"), required=True),
      _refs("candidate_answer_refs", (T.OPTION, T.PROPOSITION), when={"question_form": "choice"}),
      _enum("inquiry_timing", ("before_primary", "after_primary", "checkpoint"), required=True),
      _enum("inquiry_criticality", ("blocking", "quality_improving", "optional_curiosity"), required=True)),
     ("typed_inquiry_purpose",), (), (),
     ("elicitation.inquiry", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("seek_information.request_feedback", "Ask the user to evaluate Columbina's response, plan, or interaction.",
     (T.PRIOR_MOVE, T.PRIOR_ACTION, T.PRIOR_REALIZATION, T.PLAN), 1, 1, _question_schema(), ("actual_evaluable_object",), (), (),
     ("elicitation.response_feedback", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    # give_feedback --------------------------------------------------------
    ("give_feedback.acknowledge", "Signal receipt or attention without agreement, correctness, or full understanding.",
     (T.USER_ACT, T.MESSAGE, T.REQUEST), 1, 1, (), ("observed_contribution",), (), (),
     ("feedback.receipt", D.TARGET_EFFECT, "receipt_signal_planned", B.PRIMARY_TARGET, {})),
    ("give_feedback.confirm_understanding", "State a specific understanding as sufficiently supported.",
     (T.INTERPRETATION_HYPOTHESIS, T.PROPOSITION), 1, 1, (SCOPE(),), ("supported_interpretation",), (), ("contradicted_or_weak_interpretation",),
     ("feedback.understanding_expression", D.TARGET_EFFECT, "understanding_expression_planned", B.PRIMARY_TARGET, {})),
    ("give_feedback.signal_misunderstanding", "State that Columbina does not currently understand some content.",
     (T.UNKNOWN, T.SEMANTIC_FRAME, T.INTERPRETATION_HYPOTHESIS), 1, 1, (SCOPE(),), ("actual_unresolved_understanding",), (), (),
     ("feedback.misunderstanding_disclosure", D.TARGET_EFFECT, "misunderstanding_disclosure_planned", B.PRIMARY_TARGET, {})),
    ("give_feedback.paraphrase", "Restate understood content for alignment without adding new claims.",
     (T.USER_ACT, T.PROPOSITION, T.INTERPRETATION_HYPOTHESIS), 1, None, (SCOPE(), CONDITIONALITY()), ("preserved_epistemic_status",), (), (),
     ("feedback.paraphrase", D.TARGET_EFFECT, "paraphrase_planned", B.TARGET_SET, {})),
    ("give_feedback.reflect", "Return an observation or tentative pattern about expressed experience.",
     (T.EXPERIENCE, T.CLAIMED_AFFECT, T.USER_ACT), 1, 1, (CONDITIONALITY(),), ("expressed_experience",), (), ("asserted_hidden_affect",),
     ("feedback.reflection", D.TARGET_EFFECT, "tentative_reflection_planned", B.PRIMARY_TARGET, {})),
    ("give_feedback.validate", "Recognize a contribution, concern, or question as understandable or relevant without asserting factual correctness.",
     (T.USER_ACT, T.CONCERN, T.EXPERIENCE), 1, 1, (SCOPE(),), ("actual_contribution",), (), ("endorses_unsupported_claim",),
     ("feedback.conversational_validation", D.TARGET_EFFECT, "validation_expression_planned", B.PRIMARY_TARGET, {})),
    ("give_feedback.indicate_attention", "Signal continued attention without claiming comprehension or evaluation.",
     (T.MESSAGE, T.USER_ACT, T.EXPERIENCE, T.TOPIC, T.DYNAMIC_STATE_FIELD, T.DIALOGUE_SCOPE), 1, 1, (), ("ongoing_contribution",), (), (),
     ("feedback.attention", D.TARGET_EFFECT, "attention_signal_planned", B.PRIMARY_TARGET, {})),
    # express_stance -------------------------------------------------------
    ("express_stance.agree", "Endorse a proposition or stance as supported.",
     (T.PROPOSITION, T.PRIOR_MOVE), 1, 1, (SCOPE(), BASIS()), ("supported_agreement_basis",), (), ("implies_unsupported_fact",),
     ("stance.agreement", D.TARGET_EFFECT, "stance_expression_planned", B.PRIMARY_TARGET, {})),
    ("express_stance.disagree", "Oppose a proposition or stance without necessarily supplying a correction.",
     (T.PROPOSITION, T.PRIOR_MOVE), 1, 1, (SCOPE(), BASIS()), ("identifiable_disagreement",), (), (),
     ("stance.disagreement", D.TARGET_EFFECT, "stance_expression_planned", B.PRIMARY_TARGET, {})),
    ("express_stance.partially_agree", "Endorse one bounded part while withholding or opposing another.",
     (T.PROPOSITION, T.SEMANTIC_FRAME), 1, None, (SCOPE(required=True), BASIS()), ("separable_portions",), (), (),
     ("stance.partial_agreement", D.TARGET_EFFECT, "scoped_stance_expression_planned", B.TARGET_SET, {})),
    ("express_stance.challenge", "Request reconsideration of a proposition, assumption, or reasoning process.",
     (T.PROPOSITION, T.INTERPRETATION_HYPOTHESIS, T.PRIOR_MOVE), 1, 1, (BASIS(), SCOPE()), ("addresses_content",), (), ("personal_attack",),
     ("stance.challenge", D.TARGET_EFFECT, "reconsideration_invitation_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("express_stance.evaluate", "Assign a grounded assessment along explicit dimensions.",
     (T.OPTION, T.PLAN, T.PROPOSITION, T.PRIOR_ACTION), 1, None,
     (_refs("comparison_dimension_refs", (T.PROPOSITION, T.TOPIC), required=True), BASIS()), ("evaluation_basis",), (), (),
     ("stance.evaluation", D.TARGET_EFFECT, "assessment_expression_planned", B.TARGET_SET, {})),
    ("express_stance.express_preference", "State Columbina's bounded preference among acceptable options.",
     (T.OPTION, T.OPTION_SET), 1, None, (BASIS(), SCOPE()), ("acceptable_options",), (), (),
     ("stance.preference", D.TARGET_EFFECT, "preference_expression_planned", B.TARGET_SET, {})),
    ("express_stance.express_uncertainty", "Take an uncertain stance toward a proposition or choice.",
     (T.PROPOSITION, T.OPTION, T.INTERPRETATION_HYPOTHESIS), 1, 1, (CONDITIONALITY(), BASIS()), ("represented_uncertainty",), (), (),
     ("stance.uncertainty", D.TARGET_EFFECT, "uncertain_stance_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("express_stance.withhold_judgment", "Explicitly decline to form a stance because evidence or authority is insufficient.",
     (T.PROPOSITION, T.OPTION_SET, T.PRIOR_MOVE), 1, 1, (REASONS(), SCOPE()), ("unresolved_basis",), (), (),
     ("stance.noncommitment", D.TARGET_EFFECT, "noncommitment_expression_planned", B.PRIMARY_WITH_SUPPORT, {"support": _REASONS})),
    # support_socially -----------------------------------------------------
    ("support_socially.reassure", "Reduce concern while preserving uncertainty and avoiding unsupported promises.",
     (T.CONCERN, T.EXPERIENCE, T.PROPOSITION), 1, 1, (BASIS(), CONDITIONALITY()), ("supported_reassurance_basis",), (), ("unsupported_promise",),
     ("social_support.reassurance", D.TARGET_EFFECT, "reassurance_expression_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("support_socially.encourage", "Support continued effort, expression, or a feasible next step.",
     (T.GOAL, T.PLAN_STEP, T.EXPERIENCE, T.PRIOR_ACTION), 1, 1, (BASIS(), SCOPE()), ("feasible_effort",), (), ("guarantees_success",),
     ("social_support.encouragement", D.TARGET_EFFECT, "encouragement_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.comfort", "Offer presence or consolation in response to supported distress or hardship.",
     (T.EXPERIENCE, T.CONCERN, T.CLAIMED_AFFECT), 1, 1, (), ("supported_distress",), (), ("asserted_hidden_affect",),
     ("social_support.comfort", D.TARGET_EFFECT, "comfort_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.validate_experience", "Recognize an expressed experience or reaction as understandable without establishing external facts.",
     (T.EXPERIENCE, T.CLAIMED_AFFECT, T.CONCERN), 1, 1, (SCOPE(),), ("expressed_experience",), (), ("establishes_external_fact",),
     ("social_support.experience_validation", D.TARGET_EFFECT, "validation_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.congratulate", "Mark a supported achievement or positive milestone.",
     (T.OBSERVED_OUTCOME, T.EXPERIENCE, T.GOAL), 1, 1, (SCOPE(),), ("supported_positive_event",), (), (),
     ("social_support.congratulation", D.TARGET_EFFECT, "celebration_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.appreciate", "Express valuation of a contribution, effort, disclosure, or quality.",
     (T.USER_ACT, T.PRIOR_ACTION, T.EXPERIENCE), 1, 1, (SCOPE(),), ("actual_contribution",), (), (),
     ("social_support.appreciation", D.TARGET_EFFECT, "appreciation_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.thank", "Express gratitude for a contribution or cooperation received.",
     (T.USER_ACT, T.PRIOR_ACTION, T.MESSAGE), 1, 1, (SCOPE(),), ("identifiable_benefit",), (), (),
     ("social_support.gratitude", D.TARGET_EFFECT, "gratitude_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.apologize", "Express regret for Columbina's action, impact, or interaction failure without inventing responsibility.",
     (T.PRIOR_ACTION, T.PRIOR_REALIZATION, T.OBSERVED_OUTCOME), 1, 1, (SCOPE(), REASONS()), ("supported_involvement_or_impact",), (), ("invented_culpability",),
     ("social_support.apology", D.TARGET_EFFECT, "regret_expression_planned", B.PRIMARY_TARGET, {})),
    ("support_socially.express_sympathy", "Express concern for an evidenced hardship or loss without claiming hidden feelings.",
     (T.EXPERIENCE, T.CONCERN, T.OBSERVED_OUTCOME), 1, 1, (), ("supported_hardship",), (), ("asserted_hidden_affect",),
     ("social_support.sympathy", D.TARGET_EFFECT, "sympathy_expression_planned", B.PRIMARY_TARGET, {})),
    # advise_or_influence --------------------------------------------------
    ("advise_or_influence.advise", "Present or favor a feasible course with explicitly parameterized guidance strength.",
     (T.GOAL, T.OPTION, T.OPTION_SET, T.PLAN, T.PLAN_STEP), 1, None,
     (_refs("option_refs", (T.OPTION, T.PLAN, T.PLAN_STEP), required=True),
      _enum("guidance_strength", ("neutral_option", "low", "moderate", "high"), required=True),
      REASONS(), BASIS(), CONDITIONALITY()), ("feasible_course", "proportionate_basis"), (), (),
     ("choice_guidance.advice", D.TARGET_EFFECT, "guidance_expression_planned", B.TARGET_SET, {})),
    ("advise_or_influence.warn", "Use supported risk information to influence a decision or behavior.",
     (T.CONCERN, T.OPTION, T.PLAN, T.PROPOSITION), 1, None, (BASIS(required=True), SCOPE()), ("supported_risk", "behavioral_relevance"), (), (),
     ("choice_guidance.risk_warning", D.TARGET_EFFECT, "behavioral_warning_planned", B.TARGET_SET, {})),
    ("advise_or_influence.discourage", "Recommend against a course without necessarily asserting danger.",
     (T.OPTION, T.PLAN, T.PLAN_STEP), 1, None, (_refs("option_refs", (T.OPTION, T.PLAN, T.PLAN_STEP), required=True), REASONS()), ("grounded_disadvantage",), (), (),
     ("choice_guidance.discouragement", D.TARGET_EFFECT, "discouragement_expression_planned", B.TARGET_SET, {})),
    ("advise_or_influence.request_user_action", "Ask the user to perform a concrete action under their control.",
     (T.GOAL, T.PLAN_STEP, T.REQUEST), 1, 1, (SCOPE(required=True), REASONS(), CONDITIONALITY()), ("user_control",), (), (),
     ("choice_guidance.action_request", D.TARGET_EFFECT, "behavior_request_planned", B.PRIMARY_TARGET, {})),
    ("advise_or_influence.set_boundary", "State a persistent limit on acceptable interaction or action.",
     (T.BOUNDARY_SCOPE,), 1, 1, (SCOPE(required=True), REASONS()), ("actual_applicable_limit",), (), (),
     ("choice_guidance.boundary", D.TARGET_EFFECT, "boundary_statement_planned", B.PRIMARY_TARGET, {})),
    # commit_or_coordinate -------------------------------------------------
    ("commit_or_coordinate.offer", "Voluntarily make a feasible contribution available without claiming it has occurred.",
     (T.GOAL, T.PLAN_STEP, T.CAPABILITY), 1, 1, (CONDITIONALITY(), COMMITMENT()), ("actual_capability",), (), (),
     ("coordination.offer", D.COMMITMENT_STATE, "offer_expression_planned", B.PRIMARY_TARGET, {})),
    ("commit_or_coordinate.promise", "Commit Columbina to a feasible future conversational or authorized action.",
     (T.PLAN_STEP, T.COMMITMENT), 1, 1, (COMMITMENT(required=True), CONDITIONALITY()), ("capability_and_authority",), (), ("unsupported_guarantee",),
     ("coordination.promise", D.COMMITMENT_STATE, "commitment_creation_planned", B.PRIMARY_TARGET, {})),
    ("commit_or_coordinate.accept_request", "Accept responsibility for a specific understood request.",
     (T.REQUEST,), 1, 1, (CONDITIONALITY(),), ("satisfiable_authorized_request",), (), (),
     ("coordination.request_acceptance", D.COMMITMENT_STATE, "responsibility_acceptance_planned", B.PRIMARY_TARGET, {})),
    ("commit_or_coordinate.decline_request", "Decline one request without necessarily creating a general boundary.",
     (T.REQUEST,), 1, 1, (REASONS(), SCOPE()), ("represented_request",), (), (),
     ("coordination.request_decline", D.COMMITMENT_STATE, "refusal_expression_planned", B.PRIMARY_WITH_SUPPORT, {"support": _REASONS})),
    ("commit_or_coordinate.accept_suggestion", "Accept a user-proposed course as the current conversational plan or option.",
     (T.SUGGESTION,), 1, 1, (CONDITIONALITY(),), ("compatible_feasible_suggestion",), (), (),
     ("coordination.suggestion_acceptance", D.COMMITMENT_STATE, "course_adoption_planned", B.PRIMARY_TARGET, {})),
    ("commit_or_coordinate.decline_suggestion", "Reject adoption of a suggested course without declaring a general boundary.",
     (T.SUGGESTION,), 1, 1, (REASONS(), SCOPE()), ("represented_suggestion",), (), (),
     ("coordination.suggestion_decline", D.COMMITMENT_STATE, "course_rejection_planned", B.PRIMARY_WITH_SUPPORT, {"support": _REASONS})),
    ("commit_or_coordinate.propose_plan", "Offer an ordered or structured way to pursue an active goal.",
     (T.GOAL, T.PLAN), 1, 1, (_refs("step_refs", (T.PLAN_STEP,), required=True), CONDITIONALITY()), ("grounded_feasible_steps",), (), (),
     ("coordination.plan_proposal", D.COMMITMENT_STATE, "plan_proposal_planned", B.PRIMARY_WITH_SUPPORT, {"support": (T.PLAN_STEP,)})),
    ("commit_or_coordinate.state_next_step", "Identify the next grounded step without claiming completion.",
     (T.PLAN_STEP, T.GOAL), 1, 1, (_ref("scope_ref", (T.PLAN_STEP,), required=True),), ("represented_plan_or_goal",), (), ("claims_completion",),
     ("coordination.next_step", D.COMMITMENT_STATE, "next_step_expression_planned", B.PRIMARY_TARGET, {})),
    ("commit_or_coordinate.defer", "Intentionally postpone a request, decision, or plan step while preserving its status.",
     (T.REQUEST, T.COMMITMENT, T.PLAN_STEP, T.GOAL), 1, 1, (REASONS(required=True), CONDITIONALITY()), ("grounded_resume_condition",), (), (),
     ("coordination.deferral", D.COMMITMENT_STATE, "deferral_expression_planned", B.PRIMARY_WITH_SUPPORT, {"support": _REASONS})),
    ("commit_or_coordinate.handoff", "Transfer responsibility, or recommend transfer, to a capable authorized party or system.",
     (T.REQUEST, T.GOAL, T.PLAN_STEP), 1, 1,
     (_ref("recipient_ref", (T.CAPABILITY, T.SOCIAL_TARGET), required=True), REASONS(required=True)), ("supported_recipient_capability",), (), (),
     ("coordination.handoff", D.COMMITMENT_STATE, "transfer_proposal_planned", B.PRIMARY_WITH_SUPPORT, {"support": (T.CAPABILITY, T.SOCIAL_TARGET, *_REASONS)})),
    # manage_dialogue ------------------------------------------------------
    ("manage_dialogue.greet", "Open or reciprocate a conversational encounter.",
     (T.DIALOGUE_SCOPE, T.MESSAGE, T.USER_ACT), 1, 1, (), ("opening_context",), (), (),
     ("dialogue.opening", D.DIALOGUE_STATE, "opening_signal_planned", B.DIALOGUE_SCOPE, {})),
    ("manage_dialogue.close", "Mark the current conversation or topic as complete.",
     (T.DIALOGUE_SCOPE, T.GOAL, T.TOPIC, T.PARTIAL_PLAN), 1, 1, (SCOPE(),), ("closure_context",), (), (),
     ("dialogue.closure", D.DIALOGUE_STATE, "closure_signal_planned", B.DIALOGUE_SCOPE, {})),
    ("manage_dialogue.introduce_topic", "Establish a grounded topic not yet active.",
     (T.TOPIC,), 1, 1, (), ("grounded_inactive_topic",), (), (),
     ("dialogue.topic_introduction", D.DIALOGUE_STATE, "topic_activation_planned", B.PRIMARY_TARGET, {})),
    ("manage_dialogue.shift_topic", "Move from the current topic to another grounded topic.",
     (T.TOPIC,), 1, 1, (SCOPE(required=True),), ("typed_source_and_destination",), (), (),
     ("dialogue.topic_shift", D.DIALOGUE_STATE, "topic_shift_planned", B.SOURCE_DESTINATION, {"source": (T.TOPIC,), "destination": (T.TOPIC,)})),
    ("manage_dialogue.return_to_topic", "Resume a previously active topic after a diversion.",
     (T.TOPIC,), 1, 1, (), ("prior_active_topic",), (), (),
     ("dialogue.topic_return", D.DIALOGUE_STATE, "topic_restoration_planned", B.PRIMARY_TARGET, {})),
    ("manage_dialogue.pause", "Temporarily suspend a dialogue process or plan without closing it.",
     (T.DIALOGUE_SCOPE, T.TOPIC, T.PLAN, T.PARTIAL_PLAN), 1, 1, (REASONS(), _ref("resume_condition_ref", (T.PROPOSITION, T.PLAN_STEP))), ("pausable_process",), (), (),
     ("dialogue.pause", D.DIALOGUE_STATE, "pause_signal_planned", B.DIALOGUE_SCOPE, {})),
    ("manage_dialogue.resume", "Continue a represented paused dialogue process or plan.",
     (T.DIALOGUE_SCOPE, T.TOPIC, T.PLAN, T.PARTIAL_PLAN), 1, 1, (), ("represented_paused_state",), (), (),
     ("dialogue.resume", D.DIALOGUE_STATE, "resume_signal_planned", B.PRIMARY_TARGET, {})),
    ("manage_dialogue.repair", "Restore shared understanding after an interpretation, action, realization, or memory failure.",
     (T.PRIOR_ACTION, T.PRIOR_REALIZATION, T.INTERPRETATION_HYPOTHESIS), 1, None,
     (_enum("repair_scope", ("interpretation", "action", "realization", "memory", "mixed")), REASONS()), ("mismatch_or_correction_evidence",), (), (),
     ("dialogue.repair", D.DIALOGUE_STATE, "coordination_repair_planned", B.TARGET_SET, {})),
    ("manage_dialogue.self_correct", "Replace or qualify Columbina's own prior statement or move.",
     (T.PRIOR_MOVE, T.PRIOR_REALIZATION), 1, 1,
     (_refs("correction_content_refs", (T.PROPOSITION, T.EVIDENCE_RECORD, T.OBSERVED_OUTCOME), required=True), BASIS()), ("identifiable_error",), (), (),
     ("dialogue.self_correction", D.CONTRADICTION_HANDLING, "correction_planned", B.PRIMARY_WITH_SUPPORT, {"support": _ANY_CONTENT})),
    ("manage_dialogue.request_repetition", "Ask the user to repeat unavailable or unusable content.",
     (T.MESSAGE, T.UNKNOWN), 1, 1, (_enum("question_form", QUESTION_FORMS, required=True), SCOPE()), ("missing_or_corrupted_content",), (), (),
     ("dialogue.repetition_request", D.UNKNOWN_HANDLING, "elicitation_planned", B.PRIMARY_TARGET, {})),
    ("manage_dialogue.redirect", "Move away from an unsuitable path toward a grounded permissible goal or topic.",
     (T.GOAL, T.TOPIC, T.PLAN), 1, 1, (SCOPE(required=True), REASONS()), ("grounded_allowed_destination",), (), (),
     ("dialogue.redirect", D.DIALOGUE_STATE, "redirection_planned", B.SOURCE_DESTINATION, {"source": (T.GOAL, T.TOPIC, T.PLAN), "destination": (T.GOAL, T.TOPIC, T.PLAN)})),
    # collaborate ----------------------------------------------------------
    ("collaborate.deliver_artifact",
     "Deliver a validated collaborative-content proposal: create, revise, explain, evaluate, describe, compare, "
     "or summarize a shared conversational artifact inside the conversation.",
     (T.ARTIFACT_REVISION,), 1, 1,
     (_enum("artifact_operation", ("create", "revise", "explain", "evaluate", "describe", "compare", "summarize"),
            required=True),
      _refs("basis_refs", (T.PROPOSITION,), required=True)),
     ("validated_collaborative_content",), (), ("external_effect_claim",),
     ("collaboration.artifact_delivery", D.TARGET_EFFECT, "artifact_delivery_planned", B.PRIMARY_TARGET, {})),
    # social_play ----------------------------------------------------------
    ("social_play.joke", "Introduce a bounded humorous contribution whose primary effect is amusement.",
     (T.TOPIC, T.EXPERIENCE, T.SOCIAL_TARGET), 1, 1, (SCOPE(),), ("factual_boundary",), (), (),
     ("social_play.joke", D.TARGET_EFFECT, "amusement_invitation_planned", B.PRIMARY_TARGET, {})),
    ("social_play.playful_reframe", "Recast grounded content in a playful perspective while preserving its meaning.",
     (T.EXPERIENCE, T.PROPOSITION, T.PRIOR_MOVE), 1, 1, (SCOPE(),), ("preserved_meaning",), (), (),
     ("social_play.playful_reframe", D.TARGET_EFFECT, "playful_framing_planned", B.PRIMARY_TARGET, {})),
    ("social_play.light_tease", "Direct mild affiliative teasing toward a supported social target.",
     (T.SOCIAL_TARGET, T.PRIOR_MOVE), 1, 1, (SCOPE(),), ("appropriate_target_evidence",), (), (),
     ("social_play.light_tease", D.TARGET_EFFECT, "affiliative_tease_planned", B.PRIMARY_TARGET, {})),
    ("social_play.banter", "Continue a reciprocal sequence of playful exchange.",
     (T.USER_ACT, T.PRIOR_MOVE), 1, 1, (), ("reciprocal_play_context",), (), (),
     ("social_play.banter", D.TARGET_EFFECT, "playful_exchange_continuation_planned", B.PRIMARY_TARGET, {})),
    ("social_play.play_along", "Join the user's fictional, playful, or humorous frame without asserting it as fact.",
     (T.USER_ACT, T.PROPOSITION, T.TOPIC), 1, 1, (CONDITIONALITY(), SCOPE()), ("preserved_frame_boundary",), (), (),
     ("social_play.play_along", D.TARGET_EFFECT, "play_frame_participation_planned", B.PRIMARY_TARGET, {})),
)


# ---------------------------------------------------------------------------
# Typed evidence specifications
# ---------------------------------------------------------------------------
# Source kinds a citation may carry, by what it evidences:
#  - grounding: the observation a target was built from (the current input or a
#    trusted runtime record such as memory or a tool event);
#  - content: material a move asserts or relies on (grounded objects, the
#    interpretation, observed outcomes, trusted runtime records);
#  - prior: retained records of earlier turns, cited only by moves about them.
GROUNDING_KINDS = (S.INTERPRETATION, S.RUNTIME)
CONTENT_KINDS = (S.GROUNDED_OBJECT, S.INTERPRETATION, S.OBSERVED_OUTCOME, S.RUNTIME)
PRIOR_KINDS = (S.PRIOR_ACTION, S.PRIOR_REALIZATION)


def _ev(role: str, kinds: tuple[S, ...], minimum: int = 1, maximum: int | None = None,
        satisfies: tuple[str, ...] = ()) -> ActionEvidenceRequirement:
    ordered = tuple(sorted(set(kinds)))
    return ActionEvidenceRequirement(
        role=role, accepted_source_kinds=ordered, minimum=minimum, maximum=maximum,
        turn_boundaries=evidence_turn_boundaries(ordered), satisfies=satisfies,
    )


def _grounded(*satisfies: str, kinds: tuple[S, ...] = GROUNDING_KINDS) -> ActionEvidenceRequirement:
    return _ev("grounding", kinds, satisfies=satisfies)


# Reviewed specifications for every definition a binding recipe constructs.
# Each reviewed descriptive requirement is discharged by exactly one role.  A
# role with minimum 0 records evidence a recipe may add but never requires.
EVIDENCE_SPECIFICATIONS: Mapping[str, tuple[ActionEvidenceRequirement, ...]] = {
    # provide_information
    "provide_information.answer": (_ev("basis", CONTENT_KINDS, satisfies=("supported_answer_content",)), _grounded()),
    "provide_information.explain": (_ev("basis", CONTENT_KINDS, satisfies=("explanation_basis",)), _grounded()),
    "provide_information.exemplify": (
        _ev("dependency", (S.GROUNDED_OBJECT, S.INTERPRETATION), satisfies=("externalized_claim",)),
        _ev("example", CONTENT_KINDS, satisfies=("grounded_example",)), _grounded(),
    ),
    "provide_information.correct": (
        _ev("basis", CONTENT_KINDS, satisfies=("contradiction_evidence",)), _grounded(),
        _ev("replacement", CONTENT_KINDS, satisfies=("correction_content",)),
    ),
    "provide_information.disclose_uncertainty": (_ev("basis", CONTENT_KINDS, 0), _grounded("represented_uncertainty")),
    # seek_information
    "seek_information.clarify": (_grounded("represented_unresolved_content"),),
    "seek_information.disambiguate": (_ev("candidate", CONTENT_KINDS, 0), _grounded("two_supported_candidates")),
    "seek_information.verify": (_ev("contradiction", CONTENT_KINDS, 0), _grounded("tentative_status")),
    "seek_information.explore_goal": (_grounded("underspecified_goal"),),
    "seek_information.explore_preference": (_ev("candidate", CONTENT_KINDS, 0), _grounded("represented_preference_gap")),
    "seek_information.request_evidence": (_grounded("support_matters"),),
    "seek_information.explore_reason": (_grounded("represented_stance_or_choice", kinds=(*GROUNDING_KINDS, *PRIOR_KINDS)),),
    "seek_information.explore_experience": (_grounded("evidenced_experience"),),
    "seek_information.check_understanding": (
        _ev("dependency", (S.GROUNDED_OBJECT, S.INTERPRETATION), satisfies=("explicit_tentative_interpretation",)), _grounded(),
    ),
    "seek_information.request_feedback": (
        _ev("candidate", CONTENT_KINDS, 0), _grounded("actual_evaluable_object", kinds=(*GROUNDING_KINDS, *PRIOR_KINDS)),
    ),
    "seek_information.inquire": (
        _ev("dependency", (S.GROUNDED_OBJECT,), 0), _grounded("typed_inquiry_purpose", kinds=(S.GROUNDED_OBJECT,)),
    ),
    # collaborate: the proposal's validated content items are the basis.
    "collaborate.deliver_artifact": (
        _ev("basis", (S.GROUNDED_OBJECT,), satisfies=("validated_collaborative_content",)),
        _grounded(kinds=(S.GROUNDED_OBJECT, S.INTERPRETATION)),
    ),
    # give_feedback
    "give_feedback.acknowledge": (_grounded("observed_contribution"),),
    "give_feedback.signal_misunderstanding": (_ev("contradiction", CONTENT_KINDS, 0), _grounded("actual_unresolved_understanding")),
    "give_feedback.paraphrase": (_grounded("preserved_epistemic_status"),),
    "give_feedback.reflect": (_grounded("expressed_experience"),),
    # support_socially
    "support_socially.reassure": (_ev("basis", CONTENT_KINDS, satisfies=("supported_reassurance_basis",)), _grounded()),
    "support_socially.validate_experience": (_grounded("expressed_experience"),),
    "support_socially.congratulate": (_grounded("supported_positive_event"),),
    "support_socially.apologize": (
        _grounded(), _ev("subject", (S.GROUNDED_OBJECT, *PRIOR_KINDS), 1, 1, satisfies=("supported_involvement_or_impact",)),
    ),
    # advise_or_influence: the option target is the feasible course; its
    # proportionality is priced by the guidance-exceeds-basis soft risk.
    "advise_or_influence.advise": (_ev("basis", CONTENT_KINDS, 0), _grounded("feasible_course", "proportionate_basis")),
    # commit_or_coordinate
    "commit_or_coordinate.offer": (_grounded("actual_capability"),),
    "commit_or_coordinate.accept_request": (_grounded("satisfiable_authorized_request"),),
    "commit_or_coordinate.decline_request": (_grounded("represented_request"), _ev("reason", CONTENT_KINDS)),
    "commit_or_coordinate.accept_suggestion": (_grounded("compatible_feasible_suggestion"),),
    "commit_or_coordinate.decline_suggestion": (_grounded("represented_suggestion"), _ev("reason", CONTENT_KINDS)),
    "commit_or_coordinate.propose_plan": (_grounded(), _ev("step", (S.GROUNDED_OBJECT,), 2, satisfies=("grounded_feasible_steps",))),
    "commit_or_coordinate.state_next_step": (_grounded("represented_plan_or_goal"),),
    # manage_dialogue
    "manage_dialogue.greet": (_ev("locator", (S.INTERPRETATION,), satisfies=("opening_context",)),),
    "manage_dialogue.close": (_ev("locator", (S.INTERPRETATION,), satisfies=("closure_context",)),),
    "manage_dialogue.introduce_topic": (_grounded("grounded_inactive_topic"),),
    "manage_dialogue.return_to_topic": (_grounded("prior_active_topic"),),
    "manage_dialogue.repair": (_ev("grounding", PRIOR_KINDS, satisfies=("mismatch_or_correction_evidence",)),),
    "manage_dialogue.self_correct": (
        _ev("correction", CONTENT_KINDS, satisfies=("identifiable_error",)),
        _grounded(kinds=(*GROUNDING_KINDS, *PRIOR_KINDS)),
    ),
    # social_play
    "social_play.joke": (_grounded("factual_boundary"),),
    "social_play.playful_reframe": (_grounded("preserved_meaning"),),
}


def _evidence_specification(key: str, descriptive: tuple[str, ...]) -> tuple[ActionEvidenceRequirement, ...]:
    """Reviewed specification, or target grounding for every non-shadow definition."""

    specification = EVIDENCE_SPECIFICATIONS.get(key)
    if specification is None:
        if key in SHADOW_KEYS:
            raise RuntimeError(f"shadow definition has no reviewed evidence specification: {key}")
        specification = (_grounded(*descriptive),)
    return tuple(sorted(specification, key=lambda item: item.role))


def _lifecycle(key: str) -> ActionLifecycle:
    if key in SHADOW_KEYS:
        return ActionLifecycle.SHADOW
    if key in BOUNDARY_ONLY_KEYS or key in UNCOVERED_KEYS:
        return ActionLifecycle.CANDIDATE
    raise RuntimeError(f"registry key has no accepted lifecycle: {key}")


def _roles(mode: B, accepted: tuple[T, ...], extra: Mapping[str, tuple[T, ...]]) -> dict[str, tuple[T, ...]]:
    if mode == B.TARGET_SET:
        return {"member": accepted}
    if mode == B.DIALOGUE_SCOPE:
        return {"scope": (T.DIALOGUE_SCOPE,), "locator": tuple(item for item in accepted if item != T.DIALOGUE_SCOPE)}
    if mode == B.SOURCE_DESTINATION:
        return dict(extra)
    roles = {"primary": accepted}
    roles.update(extra)
    return roles


def _compile(row: tuple) -> ActionDefinition:
    key, description, accepted, minimum, maximum, parameters, evidence, structural, inhibitors, effect = row
    effect_key, domain, planned_state, mode, extra = effect
    family = key.partition(".")[0]
    return ActionDefinition(
        definition_ref=ActionDefinitionRef(action_key=key, definition_version=DEFINITION_VERSION),
        family=family, function=key.partition(".")[2], description=description,
        target_schema=ActionTargetSchema(accepted_types=accepted, minimum_targets=minimum, maximum_targets=maximum),
        parameter_schema=tuple(parameters), evidence_requirements=tuple(evidence),
        evidence_specification=_evidence_specification(key, tuple(evidence)),
        intended_effects=(ActionEffectDefinition(
            effect_key=effect_key, effect_version=1, transition_domain=domain, binding_mode=mode,
            target_roles=_roles(mode, accepted, extra), planned_state=planned_state,
            effect_strength="1.000000",
        ),),
        structural_requirements=tuple(structural),
        hard_inhibitors=(*SHARED_HARD_INHIBITORS, *inhibitors),
        outcome_metrics=FAMILY_OUTCOMES[ActionFamily(family)], lifecycle=_lifecycle(key),
    )


DEFINITIONS: tuple[ActionDefinition, ...] = tuple(sorted(
    (_compile(row) for row in _ROWS), key=lambda item: (item.definition_ref.action_key, item.definition_ref.definition_version),
))
_BY_KEY: Mapping[str, ActionDefinition] = {item.definition_ref.action_key: item for item in DEFINITIONS}
if len(_BY_KEY) != len(DEFINITIONS) or len(DEFINITIONS) != 77:
    raise RuntimeError("the V0 action registry must contain 77 unique definitions")
if set(_BY_KEY) != SHADOW_KEYS | BOUNDARY_ONLY_KEYS | UNCOVERED_KEYS:
    raise RuntimeError("registry keys differ from the accepted lifecycle split")


class ActionRegistry:
    """Immutable lookup over the installed definitions."""

    def __init__(self, definitions: tuple[ActionDefinition, ...] = DEFINITIONS, *, version: str = REGISTRY_VERSION) -> None:
        self.version = version
        self.definitions = tuple(sorted(definitions, key=lambda item: (item.definition_ref.action_key, item.definition_ref.definition_version)))
        self._by_ref = {item.definition_ref: item for item in self.definitions}
        if len(self._by_ref) != len(self.definitions):
            raise ValueError("registry definitions must be unique")

    def get(self, ref: ActionDefinitionRef) -> ActionDefinition | None:
        return self._by_ref.get(ref)

    def by_key(self, key: str) -> ActionDefinition | None:
        return next((item for item in self.definitions if item.definition_ref.action_key == key), None)

    def resolve(self, ref: ActionDefinitionRef) -> ActionDefinition:
        definition = self.get(ref)
        if definition is None:
            raise KeyError("unknown or unsupported action definition")
        return definition


DEFAULT_REGISTRY = ActionRegistry()


def definitions_by_lifecycle(lifecycle: ActionLifecycle) -> tuple[str, ...]:
    return tuple(item.definition_ref.action_key for item in DEFINITIONS if item.lifecycle == lifecycle)
