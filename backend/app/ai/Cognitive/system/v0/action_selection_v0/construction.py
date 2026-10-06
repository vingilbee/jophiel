"""Deterministic, declarative candidate construction.

``BINDING_MANIFEST`` holds one recipe per definition that V0 can bind.  A
recipe names accepted target types, silent eligibility filters, recorded
structural requirements, parameter and question-form binders, evidence
binders, reviewed superclass conditions, and partial-plan dependencies.  The
predicates read only typed facts from the reference index; they never consult
scores, personality, social preference, case expectations, or prose.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.actions import (
    ACTION_CONSTRUCTION_VERSION, ActionDefinition, ActionEvidenceRef, ActionTargetRef,
    ActionTargetType as T, BindingMode, GroundedActionCandidate, GroundedActionMove,
    PlannedActionEffect, UninstantiableAction,
)
from .models import IndexEntry
from .reference_index import ActionReferenceIndex
from .registry import ActionRegistry

CONSTRUCTION_VERSION = ACTION_CONSTRUCTION_VERSION
EXACT_FIT = "1.000000"
SUPERCLASS_FIT = "0.750000"
EXTERNALIZING_EFFECTS = frozenset({
    "feedback.paraphrase", "information.explanation", "information.answer", "information.example",
    "information.correction", "information.inform", "dialogue.self_correction",
    "collaboration.artifact_delivery",
})
DELIVERY_EFFECTS = frozenset({"collaboration.artifact_delivery"})
ACCEPTANCE_EFFECTS = frozenset({"coordination.request_acceptance", "coordination.suggestion_acceptance"})


@dataclass(frozen=True)
class PlanView:
    """What the current partial plan already represents, for binding dependencies."""

    moves: tuple[GroundedActionMove, ...] = ()
    effects: tuple[PlannedActionEffect, ...] = ()

    def externalized_refs(self) -> frozenset[str]:
        return frozenset(
            target.ref for effect in self.effects if effect.effect_key in EXTERNALIZING_EFFECTS
            for target in (*effect.target_refs, *effect.support_refs)
        )

    def accepted_refs(self) -> frozenset[str]:
        return frozenset(target.ref for effect in self.effects if effect.effect_key in ACCEPTANCE_EFFECTS for target in effect.target_refs)

    def delivered_refs(self) -> frozenset[str]:
        return frozenset(target.ref for effect in self.effects if effect.effect_key in DELIVERY_EFFECTS
                         for target in effect.target_refs)


@dataclass(frozen=True)
class BindingContext:
    index: ActionReferenceIndex
    plan: PlanView
    context_ref: str


Predicate = Callable[[BindingContext, IndexEntry], bool]


# ---------------------------------------------------------------------------
# Structural predicates (typed facts only)
# ---------------------------------------------------------------------------

def _focus(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.focus_contribution


def _leading(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("disposition") == "leading"


def _unrepresented(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("represents_ref") is None


def _unresolved(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("unresolved") is True


def _user_owned(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("owner") == "user"


def _kind(*kinds: str) -> Predicate:
    return lambda ctx, entry: entry.attr("unknown_kind") in kinds


def _kind_absent_or(*kinds: str) -> Predicate:
    return lambda ctx, entry: entry.attr("unknown_kind") in (None, *kinds)


def _has(name: str, minimum: int = 1) -> Predicate:
    return lambda ctx, entry: len(entry.attr(name, ()) or ()) >= minimum


def _equals(name: str, *values: Any) -> Predicate:
    return lambda ctx, entry: entry.attr(name) in values


def _not_equals(name: str, *values: Any) -> Predicate:
    return lambda ctx, entry: entry.attr(name) not in values


def _competing_or_referent(ctx: BindingContext, entry: IndexEntry) -> bool:
    return len(entry.attr("competing_candidate_refs", ())) >= 2 or entry.attr("unknown_kind") == "referent"


def _specialized_elicitation(ctx: BindingContext, entry: IndexEntry) -> bool:
    return (entry.attr("unknown_kind") in {"referent", "preference", "goal", "experience", "reason"}
            or len(entry.attr("competing_candidate_refs", ())) >= 2 or bool(entry.attr("tentative_value_refs")))


def _focus_message_without_act(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("is_focus") is True and not ctx.index.entries_of(T.USER_ACT)


def _externalized(ctx: BindingContext, entry: IndexEntry) -> bool:
    externalized = ctx.plan.externalized_refs()
    return bool(externalized & ctx.index.related_refs(entry.ref)) or entry.ref in externalized


def _acceptance_ready(ctx: BindingContext, entry: IndexEntry) -> bool:
    pursued = entry.attr("pursues_ref")
    if pursued is None:
        return True
    pursued_entries = [item for item in ctx.index.entries_for_ref(pursued) if item.target_type in {T.REQUEST, T.SUGGESTION}]
    if not pursued_entries:
        return True
    return pursued in ctx.plan.accepted_refs()


def _focus_negative_outcome_subject(ctx: BindingContext, entry: IndexEntry) -> bool:
    return any(
        item.focus_contribution and item.attr("assessment") == "negative" and item.attr("subject_ref") == entry.ref
        for item in ctx.index.entries_of(T.OBSERVED_OUTCOME)
    )


def _impact_outcome(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("assessment") == "negative" and entry.attr("dimension") in {
        "explicit_user_correction", "realization_fidelity", "social_alignment",
    }


def _columbina_subject(ctx: BindingContext, entry: IndexEntry) -> bool:
    subject = entry.attr("subject_ref")
    return any(item.attr("asserted_by") == "columbina" for item in ctx.index.entries_for_ref(subject))


def _positive_outcome(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("outcome_assessment") == "positive" or entry.attr("valence") == "positive"


def _dialogue_act(act_key: str) -> Predicate:
    def predicate(ctx: BindingContext, entry: IndexEntry) -> bool:
        return any(item.attr("act_key") == act_key and item.attr("disposition") == "leading"
                   for item in ctx.index.entries_of(T.USER_ACT))
    return predicate


def _requested_or_focus(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.focus_contribution or entry.attr("requested_by_focus") is True


def _not_requested(ctx: BindingContext, entry: IndexEntry) -> bool:
    return entry.attr("requested_by_focus") is not True


def _asserted_props_with_correction(ctx: BindingContext, entry: IndexEntry) -> bool:
    return bool(_correction_content(ctx, entry))


_CORRECTION_CONTENT_TYPES = frozenset({T.PROPOSITION, T.EVIDENCE_RECORD, T.OBSERVED_OUTCOME})


def _correction_content(ctx: BindingContext, entry: IndexEntry) -> tuple[str, ...]:
    refs: list[str] = []
    for asserted in entry.attr("asserted_refs", ()) or ():
        for item in ctx.index.entries_for_ref(asserted):
            refs.extend(item.attr("replacement_refs", ()) or ())
            refs.extend(item.attr("contradicted_by_refs", ()) or ())
    typed = [ref for ref in dict.fromkeys(refs)
             if any(candidate.target_type in _CORRECTION_CONTENT_TYPES for candidate in ctx.index.entries_for_ref(ref))]
    return tuple(typed)


def _inquiry_timing_ready(ctx: BindingContext, entry: IndexEntry) -> bool:
    """A non-blocking inquiry follows the primary contribution it belongs to (construction v0.2)."""

    if entry.attr("inquiry_timing") == "before_primary":
        return True
    return entry.attr("pursues_ref") in ctx.plan.delivered_refs()


def _unattributed_failure(ctx: BindingContext, entry: IndexEntry) -> bool:
    """Negative feedback on this object that leaves the failing part unresolved.

    An explicit user correction already attributes the failure, so it never
    calls for a feedback request.
    """

    unresolved = {item.ref for item in ctx.index.entries_of(T.UNKNOWN, T.SEMANTIC_ARGUMENT) if item.attr("unresolved")}
    return any(
        outcome.focus_contribution and outcome.attr("assessment") == "negative"
        and outcome.attr("subject_ref") == entry.ref and outcome.attr("dimension") != "explicit_user_correction"
        and set(outcome.about_refs) & unresolved
        for outcome in ctx.index.entries_of(T.OBSERVED_OUTCOME)
    )


PREDICATES: Mapping[str, Predicate] = {
    "focus_contribution": _focus, "leading_hypothesis": _leading, "unrepresented": _unrepresented,
    "unresolved": _unresolved, "user_owned": _user_owned,
    "kind_absent_or_missing_value": _kind_absent_or("missing_value", "meaning"),
    "kind_goal": _kind("goal"), "kind_preference": _kind("preference"), "kind_experience": _kind("experience"),
    "competing_or_referent": _competing_or_referent, "specialized_elicitation": _specialized_elicitation,
    "has_answer_content": _has("answer_content_refs"), "factual_or_polar_question": _equals("question_kind", "factual", "polar"),
    "has_explanation_basis": _has("explanation_basis_refs"), "has_example": _has("example_refs"),
    "has_replacement": _has("replacement_refs"), "has_contradicting_basis": _has("basis_refs"),
    "uncertain_content": _equals("epistemic_status", "unknown", "tentative"),
    "requested_content": _requested_or_focus,
    "provided_by_system": _equals("asserted_by", None, "columbina", "runtime", "memory"),
    "tentative_or_contested": _equals("epistemic_status", "tentative", "contradicted"),
    "asserted_by_other": _equals("asserted_by", "user", "memory", "third_party"),
    "asserted_by_system": _equals("asserted_by", "columbina", "runtime"),
    "asserted_by_system_or_memory": _equals("asserted_by", "columbina", "runtime", "memory"),
    "externalized_in_plan": _externalized, "missing_evidence": _equals("evidence_status", "missing"),
    "negative_feedback_subject": _focus_negative_outcome_subject,
    "unattributed_failure": _unattributed_failure,
    "focus_message_without_act": _focus_message_without_act,
    # Greetings and closings have a conventional reciprocal operator; plain
    # receipt of them is a reviewed superclass binding.
    "conventional_reciprocal_act": _equals("act_key", "interpretation.act.greet", "interpretation.act.close"),
    "columbina_interpretation_contradicted": lambda ctx, e: e.attr("asserted_by") == "columbina" and e.attr("epistemic_status") == "contradicted",
    "explicit_expression": _equals("expression", "explicit"),
    "known_valence": _equals("valence", "positive", "negative", "mixed"),
    "negative_valence": _equals("valence", "negative"),
    "has_basis": _has("basis_refs"), "positive_outcome": _positive_outcome,
    "impact_outcome": _impact_outcome, "columbina_subject": _columbina_subject,
    "task_request": _equals("fulfillment_mode", "task"),
    "acceptance_decision_needed": lambda ctx, e: e.attr("fulfillment_mode") == "task" or e.attr("acceptance_status") not in (None, "acceptable", "pending"),
    "acceptance_decided": _not_equals("acceptance_status", None, "pending"),
    "cannot_accept": _equals("acceptance_status", "prohibited", "unauthorized", "unavailable"),
    "columbina_step": _equals("owner", "columbina"), "not_requested": _not_requested,
    "has_steps": _has("step_refs", 2), "acceptance_ready": _acceptance_ready,
    "greeting_context": _dialogue_act("interpretation.act.greet"),
    "closing_context": _dialogue_act("interpretation.act.close"),
    "inactive_topic": _equals("topic_status", "inactive"), "prior_active_topic": _equals("topic_status", "prior_active"),
    "explicitly_corrected": _equals("correction_status", "explicitly_corrected"),
    "has_correction_content": _asserted_props_with_correction,
    "play_requested": lambda ctx, e: e.attr("requested_by_focus") is True,
    "has_option_basis": lambda ctx, e: bool(e.attr("basis_refs")) or e.attr("support") is not None,
    "has_risk_basis": _has("risk_basis_refs"),
    "commitment_object": lambda ctx, e: True,
    "inquiry_timing_ready": _inquiry_timing_ready,
}


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recipe:
    action_key: str
    rule_id: str
    target_types: tuple[T, ...]
    filters: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    forms: str | None = None
    parameters: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ("grounding",)
    superclass_when: tuple[str, ...] = ()
    target_set: str | None = None


def _r(key: str, types: tuple[T, ...], **values: Any) -> Recipe:
    function = key.partition(".")[2].replace("_", "-")
    return Recipe(action_key=key, rule_id=f"action-propose.{function}.v1", target_types=types, **values)


# v0.2: recipes for ``collaborate.deliver_artifact`` and ``seek_information.inquire``.
BINDING_MANIFEST_VERSION = "action-binding-manifest-v0.2"
BINDING_MANIFEST: tuple[Recipe, ...] = (
    # shadow-selectable definitions -----------------------------------------
    _r("provide_information.answer", (T.QUESTION,), requirements=("has_answer_content", "factual_or_polar_question"), evidence=("grounding", "answer_content")),
    _r("provide_information.explain", (T.PROPOSITION, T.SEMANTIC_FRAME, T.TOPIC), requirements=("has_explanation_basis",), parameters=("basis_from_explanation",), evidence=("grounding", "explanation_basis")),
    _r("provide_information.exemplify", (T.PROPOSITION, T.TOPIC), requirements=("has_example", "externalized_in_plan"), evidence=("grounding", "examples", "externalization")),
    _r("provide_information.correct", (T.PROPOSITION, T.SEMANTIC_FRAME), filters=("asserted_by_other",), requirements=("has_replacement", "has_contradicting_basis"), parameters=("basis_required",), evidence=("grounding", "basis", "replacement")),
    _r("provide_information.disclose_uncertainty", (T.PROPOSITION,), filters=("provided_by_system",), requirements=("uncertain_content", "requested_content"), evidence=("grounding", "basis")),
    _r("seek_information.clarify", (T.UNKNOWN, T.SEMANTIC_ARGUMENT), filters=("unrepresented", "unresolved"), requirements=("user_owned",), forms="clarify", superclass_when=("specialized_elicitation",)),
    _r("seek_information.disambiguate", (T.UNKNOWN, T.SEMANTIC_ARGUMENT, T.COMPETING_HYPOTHESIS_SET), filters=("unrepresented", "unresolved"), requirements=("user_owned", "competing_or_referent"), forms="disambiguate", evidence=("grounding", "candidates")),
    _r("seek_information.verify", (T.PROPOSITION,), filters=("asserted_by_other",), requirements=("tentative_or_contested",), forms="verify", evidence=("grounding", "contradiction")),
    _r("seek_information.explore_goal", (T.UNKNOWN,), filters=("unrepresented", "unresolved", "kind_goal"), requirements=("user_owned",), forms="open"),
    _r("seek_information.explore_preference", (T.UNKNOWN, T.SEMANTIC_ARGUMENT), filters=("unrepresented", "unresolved", "kind_preference"), requirements=("user_owned",), forms="preference", evidence=("grounding", "candidates")),
    _r("seek_information.request_evidence", (T.EVIDENCE_RECORD,), requirements=("missing_evidence",), forms="open"),
    _r("seek_information.explore_reason", (T.PRIOR_ACTION,), filters=("negative_feedback_subject",), requirements=("unattributed_failure",), forms="reflection", superclass_when=("negative_feedback_subject",)),
    _r("seek_information.explore_experience", (T.SEMANTIC_ARGUMENT,), filters=("unrepresented", "unresolved", "kind_experience"), requirements=("user_owned",), forms="reflection"),
    _r("seek_information.check_understanding", (T.PROPOSITION,), filters=("asserted_by_system",), requirements=("externalized_in_plan",), forms="check", evidence=("grounding", "externalization")),
    _r("seek_information.request_feedback", (T.PRIOR_ACTION, T.PRIOR_REALIZATION), filters=("negative_feedback_subject",), requirements=("unattributed_failure",), forms="feedback", evidence=("grounding", "candidates")),
    _r("give_feedback.acknowledge", (T.USER_ACT, T.MESSAGE), filters=("focus_contribution",), superclass_when=("conventional_reciprocal_act",)),
    _r("give_feedback.signal_misunderstanding", (T.INTERPRETATION_HYPOTHESIS,), filters=("columbina_interpretation_contradicted",), evidence=("grounding", "contradiction")),
    _r("give_feedback.paraphrase", (T.USER_ACT, T.PROPOSITION), target_set="paraphrase"),
    _r("give_feedback.reflect", (T.EXPERIENCE, T.CLAIMED_AFFECT), filters=("focus_contribution",), requirements=("explicit_expression",)),
    _r("support_socially.reassure", (T.CONCERN,), filters=("focus_contribution",), requirements=("explicit_expression", "has_basis"), parameters=("basis_optional",), evidence=("grounding", "basis")),
    _r("support_socially.validate_experience", (T.EXPERIENCE, T.CLAIMED_AFFECT, T.CONCERN), filters=("focus_contribution",), requirements=("explicit_expression", "known_valence")),
    _r("support_socially.congratulate", (T.OBSERVED_OUTCOME, T.EXPERIENCE), filters=("focus_contribution",), requirements=("positive_outcome",)),
    _r("support_socially.apologize", (T.OBSERVED_OUTCOME,), filters=("focus_contribution",), requirements=("impact_outcome", "columbina_subject"), evidence=("grounding", "subject")),
    _r("advise_or_influence.advise", (T.OPTION,), requirements=("has_option_basis",), forms="guidance", evidence=("grounding", "basis")),
    _r("commit_or_coordinate.offer", (T.PLAN_STEP,), filters=("columbina_step", "not_requested"), evidence=("grounding",)),
    _r("commit_or_coordinate.accept_request", (T.REQUEST,), filters=("focus_contribution", "acceptance_decision_needed"), requirements=("acceptance_decided",)),
    _r("commit_or_coordinate.decline_request", (T.REQUEST,), filters=("focus_contribution",), requirements=("cannot_accept",), parameters=("decline_reasons",), evidence=("grounding", "decline_reasons")),
    _r("commit_or_coordinate.accept_suggestion", (T.SUGGESTION,), filters=("focus_contribution",), requirements=("acceptance_decided",)),
    _r("commit_or_coordinate.decline_suggestion", (T.SUGGESTION,), filters=("focus_contribution",), requirements=("cannot_accept",), parameters=("decline_reasons",), evidence=("grounding", "decline_reasons")),
    _r("commit_or_coordinate.propose_plan", (T.PLAN,), requirements=("has_steps", "acceptance_ready"), parameters=("plan_steps",), evidence=("grounding", "steps")),
    _r("commit_or_coordinate.state_next_step", (T.PLAN_STEP,), filters=("columbina_step",), requirements=("acceptance_ready",), parameters=("next_step_scope",), evidence=("grounding",)),
    _r("manage_dialogue.greet", (T.DIALOGUE_SCOPE,), requirements=("greeting_context",), target_set="greeting"),
    _r("manage_dialogue.close", (T.DIALOGUE_SCOPE,), requirements=("closing_context",), target_set="closing"),
    _r("manage_dialogue.introduce_topic", (T.TOPIC,), requirements=("inactive_topic", "requested_content")),
    _r("manage_dialogue.return_to_topic", (T.TOPIC,), requirements=("prior_active_topic", "requested_content")),
    _r("manage_dialogue.repair", (T.PRIOR_ACTION, T.PRIOR_REALIZATION), target_set="repair", evidence=("grounding",)),
    _r("manage_dialogue.self_correct", (T.PRIOR_REALIZATION,), filters=("explicitly_corrected",), requirements=("has_correction_content",), parameters=("correction_content",), evidence=("grounding", "correction")),
    _r("collaborate.deliver_artifact", (T.ARTIFACT_REVISION,), requirements=("has_basis",),
       parameters=("artifact_operation", "basis_required"), evidence=("grounding", "basis")),
    _r("seek_information.inquire", (T.INQUIRY,), filters=("unrepresented",), requirements=("inquiry_timing_ready",),
       forms="inquiry"),
    _r("social_play.joke", (T.TOPIC,), requirements=("play_requested",)),
    _r("social_play.playful_reframe", (T.EXPERIENCE,), filters=("focus_contribution",)),
    # boundary-only candidates: constructed so the lifecycle gate stays visible
    _r("give_feedback.confirm_understanding", (T.PROPOSITION,), filters=("focus_contribution", "asserted_by_other")),
    _r("give_feedback.validate", (T.EXPERIENCE, T.CONCERN), filters=("focus_contribution",), requirements=("explicit_expression",)),
    _r("express_stance.agree", (T.PROPOSITION,), filters=("focus_contribution", "asserted_by_other")),
    _r("express_stance.disagree", (T.PROPOSITION,), filters=("focus_contribution", "asserted_by_other")),
    _r("express_stance.challenge", (T.PROPOSITION,), filters=("focus_contribution", "asserted_by_other")),
    _r("support_socially.comfort", (T.EXPERIENCE, T.CONCERN, T.CLAIMED_AFFECT), filters=("focus_contribution",), requirements=("negative_valence",)),
    _r("provide_information.inform", (T.PROPOSITION,), filters=("asserted_by_system_or_memory",)),
    _r("provide_information.elaborate", (T.PROPOSITION, T.TOPIC), requirements=("externalized_in_plan",)),
    _r("provide_information.summarize", (T.CONTENT_SOURCE,)),
    _r("commit_or_coordinate.promise", (T.COMMITMENT,), requirements=("commitment_object",)),
    _r("advise_or_influence.set_boundary", (T.BOUNDARY_SCOPE,), parameters=("boundary_scope",)),
    _r("advise_or_influence.warn", (T.CONCERN, T.OPTION, T.PROPOSITION), requirements=("has_risk_basis",), parameters=("risk_basis",)),
)
def index_manifest(manifest: tuple[Recipe, ...]) -> Mapping[str, tuple[Recipe, ...]]:
    grouped: dict[str, list[Recipe]] = {}
    for item in manifest:
        grouped.setdefault(item.action_key, []).append(item)
    for recipes in grouped.values():
        if len({item.rule_id for item in recipes}) != len(recipes):
            raise RuntimeError("binding manifest proposal rules must be unique per definition")
    return {key: tuple(value) for key, value in grouped.items()}


_RECIPES_BY_KEY: Mapping[str, tuple[Recipe, ...]] = index_manifest(BINDING_MANIFEST)


def recipes_for(action_key: str) -> tuple[Recipe, ...]:
    return _RECIPES_BY_KEY.get(action_key, ())


def recipe_for(action_key: str) -> Recipe | None:
    recipes = recipes_for(action_key)
    return recipes[0] if recipes else None


# ---------------------------------------------------------------------------
# Parameter, form, evidence, and target-set binders
# ---------------------------------------------------------------------------

def _question_forms(kind: str, entry: IndexEntry) -> tuple[dict[str, Any], ...]:
    answers = tuple(entry.attr("candidate_answer_refs", ()) or ())
    if kind == "clarify":
        return ({"question_form": "choice", "candidate_answer_refs": list(answers)},) if len(answers) >= 2 else ({"question_form": "open_set"},)
    if kind == "disambiguate":
        return ({"question_form": "choice", "candidate_answer_refs": list(answers)},) if len(answers) >= 2 else ({"question_form": "open_set"},)
    if kind == "preference":
        forms: list[dict[str, Any]] = []
        if entry.attr("scale_min") is not None and entry.attr("scale_max") is not None:
            forms.append({"question_form": "scale", "scale_min": entry.attr("scale_min"), "scale_max": entry.attr("scale_max")})
        if len(answers) >= 2:
            forms.append({"question_form": "choice", "candidate_answer_refs": list(answers)})
        return tuple(forms) or ({"question_form": "open_set"},)
    if kind == "feedback":
        return ({"question_form": "choice", "candidate_answer_refs": list(answers)},) if len(answers) >= 2 else ({"question_form": "open_set"},)
    if kind == "verify":
        return ({"question_form": "yes_no"},)
    if kind == "check":
        return ({"question_form": "check"},)
    if kind == "reflection":
        return ({"question_form": "free_reflection"},)
    if kind == "open":
        return ({"question_form": "open_set"},)
    if kind == "guidance":
        return tuple({"guidance_strength": strength, "option_refs": [entry.ref]} for strength in ("neutral_option", "low", "moderate", "high"))
    if kind == "inquiry":
        # The form, timing, and criticality were decided upstream; they are copied, never chosen here.
        form = {"question_form": entry.attr("inquiry_question_form"), "inquiry_timing": entry.attr("inquiry_timing"),
                "inquiry_criticality": entry.attr("inquiry_criticality")}
        options = tuple(entry.attr("option_refs", ()) or ())
        if form["question_form"] == "choice":
            form["candidate_answer_refs"] = list(options)
        return (form,)
    raise KeyError("unknown question-form binder")


def _parameters(names: tuple[str, ...], ctx: BindingContext, entry: IndexEntry) -> tuple[dict[str, Any], tuple[str, ...]]:
    values: dict[str, Any] = {}
    missing: list[str] = []
    for name in names:
        if name == "basis_from_explanation":
            values["basis_refs"] = list(entry.attr("explanation_basis_refs", ()))
        elif name in {"basis_required", "basis_optional"}:
            basis = list(entry.attr("basis_refs", ()) or ())
            if basis:
                values["basis_refs"] = basis
            elif name == "basis_required":
                missing.append("missing_basis_refs")
        elif name == "decline_reasons":
            reasons = list(entry.attr("decline_reason_refs", ()) or ())
            if reasons:
                values["reason_refs"] = reasons
        elif name == "plan_steps":
            values["step_refs"] = list(entry.attr("step_refs", ()))
        elif name == "next_step_scope":
            values["scope_ref"] = entry.ref
        elif name == "correction_content":
            content = list(_correction_content(ctx, entry))
            if content:
                values["correction_content_refs"] = content
            else:
                missing.append("missing_correction_content")
        elif name == "boundary_scope":
            values["scope_ref"] = entry.ref
        elif name == "risk_basis":
            values["basis_refs"] = list(entry.attr("risk_basis_refs", ()))
        elif name == "artifact_operation":
            operation = entry.attr("artifact_operation")
            if operation:
                values["artifact_operation"] = operation
            else:
                missing.append("missing_artifact_operation")
        else:
            raise KeyError("unknown parameter binder")
    return values, tuple(missing)


# The evidence role each binder cites, and the role each target-set binder cites.
EVIDENCE_BINDER_ROLES: Mapping[str, str] = {
    "grounding": "grounding", "answer_content": "basis", "explanation_basis": "basis", "examples": "example",
    "externalization": "dependency", "basis": "basis", "replacement": "replacement", "contradiction": "contradiction",
    "candidates": "candidate", "decline_reasons": "reason", "steps": "step", "subject": "subject",
    "correction": "correction",
}
TARGET_SET_EVIDENCE_ROLES: Mapping[str, str] = {
    "paraphrase": "grounding", "greeting": "locator", "closing": "locator", "repair": "grounding",
}


def recipe_evidence_roles(recipe: Recipe) -> frozenset[str]:
    if recipe.target_set is not None:
        return frozenset({TARGET_SET_EVIDENCE_ROLES[recipe.target_set]})
    return frozenset(EVIDENCE_BINDER_ROLES[name] for name in recipe.evidence)


def validate_manifest_evidence(registry: ActionRegistry, manifest: tuple[Recipe, ...]) -> None:
    """Every recipe cites only declared roles and binds every required one."""

    for recipe in manifest:
        definition = registry.by_key(recipe.action_key)
        if definition is None:
            continue
        cited = recipe_evidence_roles(recipe)
        declared = {item.role for item in definition.evidence_specification}
        required = {item.role for item in definition.evidence_specification if item.minimum >= 1}
        if not cited <= declared:
            raise ValueError(f"recipe {recipe.rule_id} cites undeclared evidence roles")
        if not required <= cited:
            raise ValueError(f"recipe {recipe.rule_id} cannot bind every required evidence role")


def _evidence(names: tuple[str, ...], ctx: BindingContext, entry: IndexEntry) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for name in names:
        if name == "grounding":
            # An anchor with no recorded sources (a message, a prior action
            # record) is itself the observation that grounds the move.
            refs.extend(("grounding", ref) for ref in (entry.source_refs or (entry.ref,)))
        elif name == "answer_content":
            refs.extend(("basis", ref) for ref in entry.attr("answer_content_refs", ()))
        elif name == "explanation_basis":
            refs.extend(("basis", ref) for ref in entry.attr("explanation_basis_refs", ()))
        elif name == "examples":
            refs.extend(("example", ref) for ref in entry.attr("example_refs", ()))
        elif name == "externalization":
            externalized = ctx.plan.externalized_refs() & (ctx.index.related_refs(entry.ref) | {entry.ref})
            refs.extend(("dependency", ref) for ref in sorted(externalized))
        elif name == "basis":
            refs.extend(("basis", ref) for ref in entry.attr("basis_refs", ()) or ())
        elif name == "replacement":
            refs.extend(("replacement", ref) for ref in entry.attr("replacement_refs", ()))
        elif name == "contradiction":
            refs.extend(("contradiction", ref) for ref in entry.attr("contradicted_by_refs", ()) or ())
        elif name == "candidates":
            refs.extend(("candidate", ref) for ref in entry.attr("competing_candidate_refs", ()) or ())
        elif name == "decline_reasons":
            refs.extend(("reason", ref) for ref in entry.attr("decline_reason_refs", ()))
        elif name == "steps":
            refs.extend(("step", ref) for ref in entry.attr("step_refs", ()))
        elif name == "subject":
            refs.append(("subject", entry.attr("subject_ref")))
        elif name == "correction":
            refs.extend(("correction", ref) for ref in _correction_content(ctx, entry))
        else:
            raise KeyError("unknown evidence binder")
    return refs


def _support_targets(definition: ActionDefinition, entry: IndexEntry, parameters: Mapping[str, Any], ctx: BindingContext) -> list[ActionTargetRef]:
    """Typed support roles for primary_with_support bindings."""

    if definition.effect.binding_mode != BindingMode.PRIMARY_WITH_SUPPORT:
        return []
    allowed = set(definition.effect.target_roles.get("support", ()))
    refs = [*parameters.get("candidate_answer_refs", ()), *parameters.get("basis_refs", ()),
            *parameters.get("reason_refs", ()), *parameters.get("step_refs", ()),
            *parameters.get("correction_content_refs", ())]
    if definition.definition_ref.action_key == "seek_information.verify":
        refs.extend(entry.attr("contradicted_by_refs", ()) or ())
    support: list[ActionTargetRef] = []
    for ref in dict.fromkeys(refs):
        for candidate in ctx.index.entries_for_ref(ref):
            if candidate.target_type in allowed:
                support.append(ActionTargetRef(role="support", target_type=candidate.target_type, ref=ref, revision_ref=candidate.revision_ref))
                break
    return support


def _target_sets(kind: str, ctx: BindingContext) -> list[tuple[list[ActionTargetRef], IndexEntry, list[tuple[str, str]]]]:
    index = ctx.index
    if kind == "paraphrase":
        acts = [item for item in index.entries_of(T.USER_ACT) if item.attr("disposition") == "leading" and item.current]
        # Construction v0.2: a proposition that is the basis of a proposed
        # artifact revision is Columbina's contribution, not a tentative
        # understanding of the user, so it is never restated as a paraphrase.
        artifact_basis = {ref for entry in index.entries_of(T.ARTIFACT_REVISION) for ref in (entry.attr("basis_refs") or ())}
        tentative = [
            item for item in index.entries_of(T.PROPOSITION)
            if item.current and item.attr("asserted_by") == "columbina" and item.attr("epistemic_status") == "tentative"
            and item.ref not in artifact_basis
            and any(index.relates(item.ref, act.ref) or set(item.about_refs) & set(act.about_refs) for act in acts)
        ]
        if not acts or not tentative:
            return []
        members = [ActionTargetRef(role="member", target_type=item.target_type, ref=item.ref, revision_ref=item.revision_ref) for item in (*acts, *tentative)]
        evidence = [("grounding", ref) for item in (*acts, *tentative) for ref in item.source_refs]
        return [(members, tentative[0], evidence)]
    if kind in {"greeting", "closing"}:
        act_key = "interpretation.act.greet" if kind == "greeting" else "interpretation.act.close"
        scope = index.entry(T.DIALOGUE_SCOPE, index.request.conversation_ref)
        acts = [item for item in index.entries_of(T.USER_ACT) if item.attr("act_key") == act_key and item.attr("disposition") == "leading"]
        if scope is None or not acts:
            return []
        targets = [ActionTargetRef(role="scope", target_type=T.DIALOGUE_SCOPE, ref=scope.ref)]
        # A greeting act is an accepted greet locator; a closing act only
        # evidences the closure context because close does not accept user acts.
        if kind == "greeting":
            targets.extend(ActionTargetRef(role="locator", target_type=T.USER_ACT, ref=item.ref) for item in acts)
        evidence = [("locator", item.ref) for item in acts]
        return [(targets, scope, evidence)]
    if kind == "repair":
        # Coordination repair binds explicitly corrected interpretation, action,
        # or memory failures.  A realization-only misstatement is Columbina's own
        # statement and is bound by self-correction instead.
        failed = [
            item for item in index.entries_of(T.PRIOR_ACTION, T.PRIOR_REALIZATION)
            if index.is_current_for_turn(item) and item.attr("correction_status") == "explicitly_corrected"
            and item.attr("failure_kind") in {"interpretation", "action", "memory"}
        ]
        if not failed:
            return []
        members = [ActionTargetRef(role="member", target_type=item.target_type, ref=item.ref, revision_ref=item.revision_ref) for item in failed]
        evidence = [("grounding", item.ref) for item in failed]
        return [(members, failed[0], evidence)]
    raise KeyError("unknown target-set binder")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _evidence_refs(ctx: BindingContext, pairs: list[tuple[str, str]]) -> tuple[ActionEvidenceRef, ...]:
    refs = {}
    for role, ref in pairs:
        if ref is None or not ctx.index.resolves(ref):
            raise ValueError("candidate evidence reference does not resolve")
        item = ActionEvidenceRef(role=role, ref=ref, source_kind=ctx.index.source_kind(ref))
        refs[item.sort_key] = item
    return tuple(refs[key] for key in sorted(refs))


class CandidateConstructor:
    def __init__(self, registry: ActionRegistry, manifest: tuple[Recipe, ...] | None = None) -> None:
        self.registry = registry
        validate_manifest_evidence(registry, BINDING_MANIFEST if manifest is None else manifest)
        self.recipes = _RECIPES_BY_KEY if manifest is None else index_manifest(manifest)

    def construct(self, index: ActionReferenceIndex, plan: PlanView, context_ref: str) -> tuple[tuple[GroundedActionCandidate, ...], tuple[UninstantiableAction, ...]]:
        ctx = BindingContext(index=index, plan=plan, context_ref=context_ref)
        built: dict[tuple, GroundedActionCandidate] = {}
        failures: dict[str, UninstantiableAction] = {}
        for definition in self.registry.definitions:
            for recipe in self.recipes.get(definition.definition_ref.action_key, ()):
                for attempt in self._attempts(recipe, definition, ctx):
                    self._record(attempt, recipe, definition, ctx, built, failures)
        candidates = tuple(built[key] for key in sorted(built))
        return candidates, tuple(failures[key] for key in sorted(failures))

    def _record(self, attempt, recipe: Recipe, definition: ActionDefinition, ctx: BindingContext,
                built: dict[tuple, GroundedActionCandidate], failures: dict[str, UninstantiableAction]) -> None:
        targets, anchor, evidence_pairs, parameters, missing = attempt
        context_ref = ctx.context_ref
        if missing:
            failure = UninstantiableAction(
                attempt_id=f"action-attempt-{_digest(context_ref, definition.definition_ref.identity, *sorted(t.ref for t in targets), *missing)}",
                definition_ref=definition.definition_ref, binding_context_refs=tuple(dict.fromkeys(t.ref for t in targets)),
                missing_codes=tuple(missing), source_refs=tuple(dict.fromkeys(anchor.source_refs)), proposal_rule_id=recipe.rule_id,
            )
            failures[failure.attempt_id] = failure
            return
        fit = SUPERCLASS_FIT if any(PREDICATES[name](ctx, anchor) for name in recipe.superclass_when) else EXACT_FIT
        candidate = GroundedActionCandidate(
            candidate_id="pending", definition_ref=definition.definition_ref,
            target_refs=tuple(sorted(targets, key=lambda item: item.sort_key)), parameters=parameters,
            supporting_evidence_refs=_evidence_refs(ctx, evidence_pairs), proposal_rule_ids=(recipe.rule_id,),
            source_refs=tuple(dict.fromkeys((anchor.ref, *anchor.source_refs))), registry_version=self.registry.version,
            binding_fit=fit,
            binding_fit_rule_id=("action-fit.reviewed-superclass.v1" if fit == SUPERCLASS_FIT else "action-fit.exact-binding.v1"),
        )
        identity = candidate.lexical_identity
        candidate = candidate.model_copy(update={
            "candidate_id": f"action-candidate-{_digest(context_ref, definition.definition_ref.identity, repr(identity[1]), identity[2])}",
        })
        previous = built.get(identity)
        if previous is not None:
            # Exact duplicates are one candidate that retains every proposal and evidence source.
            candidate = previous.model_copy(update={
                "proposal_rule_ids": tuple(dict.fromkeys((*previous.proposal_rule_ids, *candidate.proposal_rule_ids))),
                "supporting_evidence_refs": tuple(sorted(
                    {item.sort_key: item for item in (*previous.supporting_evidence_refs, *candidate.supporting_evidence_refs)}.values(),
                    key=lambda item: item.sort_key,
                )),
                "source_refs": tuple(dict.fromkeys((*previous.source_refs, *candidate.source_refs))),
            })
        built[identity] = GroundedActionCandidate.model_validate(candidate.model_dump(mode="python"))

    def _attempts(self, recipe: Recipe, definition: ActionDefinition, ctx: BindingContext):
        index = ctx.index
        if recipe.target_set is not None:
            for targets, anchor, evidence in _target_sets(recipe.target_set, ctx):
                missing = tuple(name for name in recipe.requirements if not PREDICATES[name](ctx, anchor))
                yield targets, anchor, evidence, {}, missing
            return
        for entry in index.entries_of(*recipe.target_types):
            if not index.is_current_for_turn(entry):
                continue
            if entry.target_type == T.MESSAGE and not PREDICATES["focus_message_without_act"](ctx, entry):
                continue
            if entry.target_type == T.USER_ACT and entry.attr("disposition") != "leading":
                continue
            if not all(PREDICATES[name](ctx, entry) for name in recipe.filters):
                continue
            role = "member" if definition.effect.binding_mode == BindingMode.TARGET_SET else "primary"
            primary = ActionTargetRef(role=role, target_type=entry.target_type, ref=entry.ref, revision_ref=entry.revision_ref)
            missing = tuple(name for name in recipe.requirements if not PREDICATES[name](ctx, entry))
            base_parameters, parameter_missing = _parameters(recipe.parameters, ctx, entry)
            missing = (*missing, *parameter_missing)
            evidence = [] if missing else _evidence(recipe.evidence, ctx, entry)
            if missing:
                yield [primary], entry, evidence, {}, missing
                continue
            for form in (_question_forms(recipe.forms, entry) if recipe.forms else ({},)):
                parameters = {**base_parameters, **form}
                targets = [primary, *_support_targets(definition, entry, parameters, ctx)]
                yield targets, entry, evidence, parameters, ()


__all__ = ["BINDING_MANIFEST", "BINDING_MANIFEST_VERSION", "CONSTRUCTION_VERSION", "CandidateConstructor",
           "EVIDENCE_BINDER_ROLES", "PREDICATES", "PlanView", "Recipe", "TARGET_SET_EVIDENCE_ROLES", "index_manifest",
           "recipe_evidence_roles", "recipe_for", "recipes_for", "validate_manifest_evidence"]
