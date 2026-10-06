"""Canonical immutable Action Selection V0 contracts.

This module owns every cross-stage action type: definitions, typed target and
evidence references, the trusted runtime and personality projections, the
selection request, candidates, gate results, features, score breakdowns,
partial-plan states, planning steps, and the completed plan.  It contains
contracts only; the registry, construction, gates, scoring, and planner live in
``action_selection_v0``.

Numeric policy: every value that participates in a score comparison is a
canonical decimal string with exactly six fractional digits (``"0.670000"``).
Scoring uses :class:`decimal.Decimal` constructed from these strings, never
binary floats, and two scores are equal when their difference is at most
``ACTION_SCORE_TOLERANCE``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .goals import ActiveGoalView, GoalOwner, GoalSnapshot, GoalStatus, project_active_goal
from .interpretation import FrozenModel, HypothesisSupport, InterpretationEnvelope
from .collaboration import CollaborationActionHandoff
from .requirement_resolution import RequirementActionHandoff
from .safety import (
    APPLICATION_SAFETY_DECISION_SCHEMA_VERSION, APPLICATION_SAFETY_POLICY_VERSION, ApplicationSafetyDecision,
    SafetyDecisionProvenance, SafetyDisposition, SafetyRestriction, SafetyRestrictionReason, safety_restriction,
)
from ..appraisal.models import AppraisalResult
from ..dynamic_state.models import StateSnapshot

# contract v0.2 / features v0.2 / planner v0.2 (SH-EVAL-006): typed planning
# conditions for blocked user goals, blocker-first selection eligibility, and
# response-obligation coverage that a generic move cannot fully earn while a
# user request is blocked.  Additive fields are omitted when empty.
# contract v0.3 / features v0.3 / planner v0.3 (typed requirement resolution,
# SH-EVAL-020..024): the request carries the turn's requirement handoff;
# planning conditions are also derived from blocking resolutions on active
# current-turn request goals and are addressed only by the exact move kind that
# truthfully resolves them; a grounded CAPABILITY object may carry a typed
# ``capability_status``.  Additive fields are omitted when empty.
# v0.4 (Collaborative Content and Inquiry V0): ARTIFACT_REVISION and INQUIRY
# targets, the ``collaborate`` family, typed artifact-operation and inquiry
# fields on grounded objects, an explicit ``unresolved`` flag for projected
# user-owned unknowns, the ``content_unavailable`` planning condition, and the
# request's collaboration handoff.  Additive fields are omitted when empty.
ACTION_CONTRACT_SCHEMA_VERSION = "action-contract-v0.4"
ACTION_REGISTRY_VERSION = "conversational-actions-v0.2-shadow"
ACTION_CONSTRUCTION_VERSION = "action-construction-v0.2"
ACTION_HARD_GATE_VERSION = "action-hard-gate-v0.1"
ACTION_FEATURE_VERSION = "action-features-v0.4"
ACTION_POLICY_VERSION = "action-policy-v0.1"
ACTION_PLANNER_VERSION = "action-planner-v0.4"
ACTION_NUMERIC_POLICY_VERSION = "action-numeric-decimal6-v0.1"

ACTION_DECIMAL_PLACES = 6
ACTION_QUANTUM = Decimal("0.000001")
ACTION_SCORE_TOLERANCE = Decimal("0.000001")
STOP_ACTION_KEY = "STOP"
PRIMARY_RULE_ID = "action-primary.focal-goal-direct-coverage.v1"
# Planner action-planner-model-policy-v1: the model names the primary move,
# which must carry the plan's greatest direct focal-goal coverage.
MODEL_PRIMARY_RULE_ID = "action-primary.model-selected-focal-coverage.v1"

_DECIMAL_PATTERN = re.compile(r"^-?\d+\.\d{6}$")


# ---------------------------------------------------------------------------
# Small shared validators and numeric helpers
# ---------------------------------------------------------------------------

def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _json_value(value: Any) -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _json_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _json_value(item)
        return
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def canonical_decimal(value: Decimal | int | str) -> str:
    """Return the one canonical six-place decimal string for ``value``."""

    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("value is not a decimal number") from exc
    if not number.is_finite():
        raise ValueError("decimal values must be finite")
    text = str(number.quantize(ACTION_QUANTUM, rounding=ROUND_HALF_EVEN))
    return "0.000000" if text == "-0.000000" else text


def decimal_from(text: str) -> Decimal:
    """Parse a canonical decimal string; anything else is rejected."""

    if not isinstance(text, str) or not _DECIMAL_PATTERN.match(text):
        raise ValueError("numeric values must be canonical six-place decimal strings")
    if text == "-0.000000":
        raise ValueError("negative zero is not canonical")
    return Decimal(text)


def _bounded(text: str, low: str, high: str, label: str) -> None:
    number = decimal_from(text)
    if number < Decimal(low) or number > Decimal(high):
        raise ValueError(f"{label} must be within [{low}, {high}]")


def unit_to_decimal(value: float, label: str) -> Decimal:
    """Convert a validated finite float in ``[0, 1]`` through its decimal text."""

    if type(value) not in {float, int} or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    number = Decimal(repr(float(value)))
    if number < 0 or number > 1:
        raise ValueError(f"{label} must be within [0, 1]")
    return number


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------

class ActionLifecycle(StrEnum):
    CANDIDATE = "candidate"; REVIEWED = "reviewed"; SHADOW = "shadow"
    APPROVED = "approved"; DEPRECATED = "deprecated"; RETIRED = "retired"


class ActionConstructionStatus(StrEnum):
    UNINSTANTIABLE = "uninstantiable"; INSTANTIATED = "instantiated"


class ActionGateStatus(StrEnum):
    BLOCKED = "blocked"; SHADOW_ONLY = "shadow_only"; PASSED = "passed"


class ActionGateCategory(StrEnum):
    REGISTRY_LIFECYCLE = "registry_lifecycle"
    SCHEMA_REFERENCE_INTEGRITY = "schema_reference_integrity"
    TRUTH_EVIDENCE = "truth_evidence"
    CAPABILITY = "capability"
    AUTHORIZATION_CONSENT_PRIVACY = "authorization_consent_privacy"
    APPLICATION_SAFETY = "application_safety"


GATE_CATEGORY_ORDER: tuple[ActionGateCategory, ...] = tuple(ActionGateCategory)


class MovePriority(StrEnum):
    PRIMARY = "primary"; SUPPORTING = "supporting"


class PlanTerminationKind(StrEnum):
    STOP = "stop"; NO_VIABLE_PLAN = "no_viable_plan"; CYCLE_GUARD = "cycle_guard"
    CANCELLED = "cancelled"; TIMEOUT = "timeout"; FAILED = "failed"


OPERATIONAL_TERMINATIONS = frozenset({
    PlanTerminationKind.NO_VIABLE_PLAN, PlanTerminationKind.CYCLE_GUARD,
    PlanTerminationKind.CANCELLED, PlanTerminationKind.TIMEOUT, PlanTerminationKind.FAILED,
})


class ActionFailurePhase(StrEnum):
    REQUEST_VALIDATION = "request_validation"
    REGISTRY_RESOLUTION = "registry_resolution"
    CANDIDATE_CONSTRUCTION = "candidate_construction"
    HARD_GATE = "hard_gate"
    FEATURE_EXTRACTION = "feature_extraction"
    SCORING = "scoring"
    CONFLICT_RESOLUTION = "conflict_resolution"
    PLAN_TRANSITION = "plan_transition"
    PLAN_VALIDATION = "plan_validation"
    TRACE_VALIDATION = "trace_validation"


class ActionFamily(StrEnum):
    PROVIDE_INFORMATION = "provide_information"
    SEEK_INFORMATION = "seek_information"
    GIVE_FEEDBACK = "give_feedback"
    EXPRESS_STANCE = "express_stance"
    SUPPORT_SOCIALLY = "support_socially"
    ADVISE_OR_INFLUENCE = "advise_or_influence"
    COMMIT_OR_COORDINATE = "commit_or_coordinate"
    MANAGE_DIALOGUE = "manage_dialogue"
    SOCIAL_PLAY = "social_play"
    # Registry v0.2: shared work delivered inside the conversation.
    COLLABORATE = "collaborate"


class ActionTargetType(StrEnum):
    MESSAGE = "message"; USER_ACT = "user_act"; ASSISTANT_ACT = "assistant_act"
    DIALOGUE_ACT = "dialogue_act"; SEMANTIC_FRAME = "semantic_frame"
    SEMANTIC_ARGUMENT = "semantic_argument"; PROPOSITION = "proposition"
    QUESTION = "question"; TOPIC = "topic"; OPTION = "option"; OPTION_SET = "option_set"
    INTERPRETATION_HYPOTHESIS = "interpretation_hypothesis"
    COMPETING_HYPOTHESIS_SET = "competing_hypothesis_set"; UNKNOWN = "unknown"
    CONTRADICTION = "contradiction"; GOAL = "goal"; PLAN = "plan"; PLAN_STEP = "plan_step"
    REQUEST = "request"; SUGGESTION = "suggestion"; COMMITMENT = "commitment"
    EXPERIENCE = "experience"; CONCERN = "concern"; CLAIMED_AFFECT = "claimed_affect"
    APPRAISAL_FIELD = "appraisal_field"; DYNAMIC_STATE_FIELD = "dynamic_state_field"
    PARTIAL_PLAN = "partial_plan"; PRIOR_MOVE = "prior_move"; PRIOR_ACTION = "prior_action"
    PRIOR_REALIZATION = "prior_realization"; OBSERVED_OUTCOME = "observed_outcome"
    EVIDENCE_RECORD = "evidence_record"; CONTENT_SOURCE = "content_source"
    CAPABILITY = "capability"; BOUNDARY_SCOPE = "boundary_scope"
    SOCIAL_TARGET = "social_target"; DIALOGUE_SCOPE = "dialogue_scope"
    # Contract v0.4: a validated collaborative-content proposal (a proposed or
    # presented artifact revision) and an inquiry opportunity.
    ARTIFACT_REVISION = "artifact_revision"; INQUIRY = "inquiry"


class TransitionDomain(StrEnum):
    TARGET_EFFECT = "target_effect"; UNKNOWN_HANDLING = "unknown_handling"
    CONTRADICTION_HANDLING = "contradiction_handling"; COMMITMENT_STATE = "commitment_state"
    DIALOGUE_STATE = "dialogue_state"


class BindingMode(StrEnum):
    EACH_TARGET = "each_target"; TARGET_SET = "target_set"; PRIMARY_TARGET = "primary_target"
    PRIMARY_WITH_SUPPORT = "primary_with_support"; SOURCE_DESTINATION = "source_destination"
    DIALOGUE_SCOPE = "dialogue_scope"


class QuestionForm(StrEnum):
    OPEN_SET = "open_set"; YES_NO = "yes_no"; CHOICE = "choice"; CHECK = "check"
    SCALE = "scale"; FREE_REFLECTION = "free_reflection"


class ParameterValueType(StrEnum):
    ENUM = "enum"; REFERENCE = "reference"; REFERENCE_ARRAY = "reference_array"; NUMBER = "number"


class FeatureKey(StrEnum):
    SEMANTIC_FIT = "semantic_fit"; TARGET_PRIORITY = "target_priority"; GOAL_FIT = "goal_fit"
    EXPECTED_GOAL_PROGRESS = "expected_goal_progress"
    UNCERTAINTY_REDUCTION = "uncertainty_reduction"; CONTRADICTION_REPAIR = "contradiction_repair"
    EVIDENCE_SUPPORT = "evidence_support"; PERSONALITY_AFFINITY = "personality_affinity"
    STATE_COMPATIBILITY = "state_compatibility"; PLAN_CONTRIBUTION = "plan_contribution"
    REPETITION_COST = "repetition_cost"; RESPONSE_COST = "response_cost"
    SOFT_RISK_COST = "soft_risk_cost"


FEATURE_ORDER: tuple[FeatureKey, ...] = tuple(FeatureKey)
FEATURE_RANGES: Mapping[FeatureKey, tuple[str, str]] = {
    FeatureKey.SEMANTIC_FIT: ("0", "1"), FeatureKey.TARGET_PRIORITY: ("0", "1"),
    FeatureKey.GOAL_FIT: ("0", "1"), FeatureKey.EXPECTED_GOAL_PROGRESS: ("0", "1"),
    FeatureKey.UNCERTAINTY_REDUCTION: ("0", "1"), FeatureKey.CONTRADICTION_REPAIR: ("0", "1"),
    FeatureKey.EVIDENCE_SUPPORT: ("-1", "1"), FeatureKey.PERSONALITY_AFFINITY: ("-1", "1"),
    FeatureKey.STATE_COMPATIBILITY: ("-1", "1"), FeatureKey.PLAN_CONTRIBUTION: ("-1", "1"),
    FeatureKey.REPETITION_COST: ("0", "1"), FeatureKey.RESPONSE_COST: ("0", "1"),
    FeatureKey.SOFT_RISK_COST: ("0", "1"),
}


class FeatureStatus(StrEnum):
    KNOWN = "known"; MISSING = "missing"; INAPPLICABLE = "inapplicable"


class PersonalityStatus(StrEnum):
    KNOWN = "known"; UNKNOWN = "unknown"


class ReferenceSourceKind(StrEnum):
    INTERPRETATION = "interpretation"; GOAL = "goal"; APPRAISAL = "appraisal"
    DYNAMIC_STATE = "dynamic_state"; RUNTIME = "runtime"; GROUNDED_OBJECT = "grounded_object"
    PRIOR_ACTION = "prior_action"; PRIOR_REALIZATION = "prior_realization"
    OBSERVED_OUTCOME = "observed_outcome"; PARTIAL_PLAN = "partial_plan"


class ObjectStatus(StrEnum):
    CURRENT = "current"; SUPERSEDED = "superseded"; DELETED = "deleted"


class TieBreakField(StrEnum):
    EXPECTED_GOAL_PROGRESS = "expected_goal_progress"; TARGET_PRIORITY = "target_priority"
    SOFT_RISK_COST = "soft_risk_cost"; RESPONSE_COST = "response_cost"
    LEXICAL_IDENTITY = "lexical_identity"


# ---------------------------------------------------------------------------
# Definition contracts
# ---------------------------------------------------------------------------

class ActionDefinitionRef(FrozenModel):
    action_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)

    @model_validator(mode="after")
    def valid(self) -> ActionDefinitionRef:
        _nonblank(self.action_key, "action key")
        return self

    @property
    def identity(self) -> str:
        return f"{self.action_key}@{self.definition_version}"


class ActionTargetSchema(FrozenModel):
    accepted_types: tuple[ActionTargetType, ...] = Field(min_length=1)
    minimum_targets: int = Field(ge=0)
    maximum_targets: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid(self) -> ActionTargetSchema:
        _unique(self.accepted_types, "accepted target types")
        if self.maximum_targets is not None and self.maximum_targets < self.minimum_targets:
            raise ValueError("maximum targets must not be below minimum targets")
        return self


class ActionParameterRule(FrozenModel):
    parameter_key: str = Field(min_length=1)
    value_type: ParameterValueType
    required: bool = False
    required_when: Mapping[str, str] | None = None
    allowed_values: tuple[str, ...] = ()
    reference_target_types: tuple[ActionTargetType, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> ActionParameterRule:
        _nonblank(self.parameter_key, "parameter key")
        _unique(self.allowed_values, "allowed values")
        if self.value_type == ParameterValueType.ENUM and not self.allowed_values:
            raise ValueError("enum parameters require allowed values")
        if self.value_type != ParameterValueType.ENUM and self.allowed_values:
            raise ValueError("only enum parameters declare allowed values")
        is_reference = self.value_type in {ParameterValueType.REFERENCE, ParameterValueType.REFERENCE_ARRAY}
        if is_reference != bool(self.reference_target_types):
            raise ValueError("reference parameters, and only they, declare target types")
        if self.required and self.required_when:
            raise ValueError("a parameter is either always or conditionally required")
        return self


class ActionEffectDefinition(FrozenModel):
    effect_key: str = Field(min_length=1)
    effect_version: int = Field(ge=1)
    transition_domain: TransitionDomain
    binding_mode: BindingMode
    target_roles: Mapping[str, tuple[ActionTargetType, ...]]
    planned_state: str = Field(min_length=1)
    effect_strength: str
    actual_outcome_claim: Literal[False] = False

    @model_validator(mode="after")
    def valid(self) -> ActionEffectDefinition:
        _nonblank(self.effect_key, "effect key")
        if not self.planned_state.endswith("_planned"):
            raise ValueError("V0 effects may only declare planned states")
        _bounded(self.effect_strength, "0", "1", "effect strength")
        if not self.target_roles:
            raise ValueError("effects require target roles")
        required_roles = {
            BindingMode.EACH_TARGET: {"primary"}, BindingMode.TARGET_SET: {"member"},
            BindingMode.PRIMARY_TARGET: {"primary"}, BindingMode.PRIMARY_WITH_SUPPORT: {"primary", "support"},
            BindingMode.SOURCE_DESTINATION: {"source", "destination"},
            BindingMode.DIALOGUE_SCOPE: {"scope"},
        }[self.binding_mode]
        if not required_roles <= set(self.target_roles):
            raise ValueError("effect target roles do not match its binding mode")
        return self


class EvidenceTurnBoundary(StrEnum):
    """Where cited evidence may come from, relative to the current turn.

    ``current_turn`` evidence belongs to this cognitive turn's own inputs.
    ``prior_record`` evidence is a retained record of an earlier turn (a prior
    action or realization) and must not claim the current turn.
    """

    CURRENT_TURN = "current_turn"; PRIOR_RECORD = "prior_record"


PRIOR_RECORD_SOURCE_KINDS = frozenset({ReferenceSourceKind.PRIOR_ACTION, ReferenceSourceKind.PRIOR_REALIZATION})
# Stage and planner bookkeeping is never evidence for a conversational move.
NON_EVIDENCE_SOURCE_KINDS = frozenset({
    ReferenceSourceKind.GOAL, ReferenceSourceKind.APPRAISAL, ReferenceSourceKind.DYNAMIC_STATE,
    ReferenceSourceKind.PARTIAL_PLAN,
})


def evidence_turn_boundaries(kinds: tuple[ReferenceSourceKind, ...]) -> tuple[EvidenceTurnBoundary, ...]:
    present = set(kinds)
    out = []
    if present - PRIOR_RECORD_SOURCE_KINDS:
        out.append(EvidenceTurnBoundary.CURRENT_TURN)
    if present & PRIOR_RECORD_SOURCE_KINDS:
        out.append(EvidenceTurnBoundary.PRIOR_RECORD)
    return tuple(out)


class ActionEvidenceRequirement(FrozenModel):
    """One enforceable evidence requirement of a definition.

    ``role`` is the evidence role a candidate cites; ``accepted_source_kinds``
    the index source kinds that role may cite; ``minimum``/``maximum`` the
    cardinality of valid citations.  Evidence is always required to be
    authorized and at its current revision; ``turn_boundaries`` is derived
    from the accepted kinds and stated explicitly so a reviewer sees it.
    ``satisfies`` names the reviewed descriptive requirements this role
    discharges.
    """

    role: str = Field(min_length=1)
    accepted_source_kinds: tuple[ReferenceSourceKind, ...] = Field(min_length=1)
    minimum: int = Field(ge=0)
    maximum: int | None = Field(default=None, ge=1)
    turn_boundaries: tuple[EvidenceTurnBoundary, ...] = Field(min_length=1)
    authorization: Literal["authorized_only"] = "authorized_only"
    freshness: Literal["current_revision_only"] = "current_revision_only"
    satisfies: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> ActionEvidenceRequirement:
        _nonblank(self.role, "evidence role")
        _unique(self.accepted_source_kinds, "accepted evidence source kinds")
        if tuple(sorted(self.accepted_source_kinds)) != self.accepted_source_kinds:
            raise ValueError("accepted evidence source kinds must be sorted")
        if set(self.accepted_source_kinds) & NON_EVIDENCE_SOURCE_KINDS:
            raise ValueError("stage bookkeeping cannot be accepted as evidence")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("evidence maximum must not be below its minimum")
        if self.turn_boundaries != evidence_turn_boundaries(self.accepted_source_kinds):
            raise ValueError("evidence turn boundaries must match the accepted source kinds")
        _unique(self.satisfies, "satisfied evidence requirements")
        for name in self.satisfies:
            _nonblank(name, "satisfied evidence requirement")
        return self


def evidence_specification_violations(
    specification: tuple[ActionEvidenceRequirement, ...], evidence_refs: tuple[ActionEvidenceRef, ...],
    subject: str, rejected: frozenset[tuple[str, str, str]] = frozenset(),
) -> tuple[tuple[str, str], ...]:
    """Structural evidence check shared by the hard gate and trace validation.

    Returns ``(code, subject_ref)`` pairs.  ``rejected`` holds the sort keys of
    citations the reference index already rejected; they never count toward
    a requirement's cardinality.
    """

    by_role = {item.role: item for item in specification}
    counts: dict[str, int] = {}
    out: list[tuple[str, str]] = []
    for evidence in evidence_refs:
        requirement = by_role.get(evidence.role)
        if requirement is None:
            out.append(("evidence_role_not_declared", evidence.ref))
        elif evidence.source_kind not in requirement.accepted_source_kinds:
            out.append(("evidence_source_kind_not_accepted", evidence.ref))
        elif evidence.sort_key not in rejected:
            counts[evidence.role] = counts.get(evidence.role, 0) + 1
    for requirement in specification:
        count = counts.get(requirement.role, 0)
        if count < requirement.minimum:
            out.append(("required_evidence_missing", f"{subject}#evidence:{requirement.role}"))
        if requirement.maximum is not None and count > requirement.maximum:
            out.append(("evidence_cardinality_exceeded", f"{subject}#evidence:{requirement.role}"))
    return tuple(out)


class ActionDefinition(FrozenModel):
    definition_ref: ActionDefinitionRef
    parent_action_ref: ActionDefinitionRef | None = None
    family: ActionFamily
    function: str = Field(min_length=1)
    description: str = Field(min_length=1)
    target_schema: ActionTargetSchema
    parameter_schema: tuple[ActionParameterRule, ...] = ()
    evidence_requirements: tuple[str, ...] = Field(min_length=1)
    evidence_specification: tuple[ActionEvidenceRequirement, ...] = Field(min_length=1)
    intended_effects: tuple[ActionEffectDefinition, ...] = Field(min_length=1, max_length=1)
    structural_requirements: tuple[str, ...] = ()
    hard_inhibitors: tuple[str, ...] = ()
    policy_feature_relations: tuple[FeatureKey, ...] = FEATURE_ORDER
    outcome_metrics: tuple[str, ...] = Field(min_length=1)
    origin: Literal["human", "system", "reflected"] = "human"
    lifecycle: ActionLifecycle

    @model_validator(mode="after")
    def valid(self) -> ActionDefinition:
        family, _, function = self.definition_ref.action_key.partition(".")
        if family != self.family.value or function != self.function:
            raise ValueError("action key must equal family.function")
        _unique(tuple(item.parameter_key for item in self.parameter_schema), "parameter keys")
        for rule in self.parameter_schema:
            for key in (rule.required_when or {}):
                if key not in {item.parameter_key for item in self.parameter_schema}:
                    raise ValueError("conditional requirement names an unknown parameter")
        _unique(self.policy_feature_relations, "policy feature relations")
        _unique(self.outcome_metrics, "outcome metrics")
        _unique(self.evidence_requirements, "evidence requirements")
        roles = tuple(item.role for item in self.evidence_specification)
        _unique(roles, "evidence requirement roles")
        if roles != tuple(sorted(roles)):
            raise ValueError("evidence requirements must be sorted by role")
        if not any(item.minimum >= 1 for item in self.evidence_specification):
            raise ValueError("every definition requires at least one piece of evidence")
        satisfied = [name for item in self.evidence_specification for name in item.satisfies]
        _unique(tuple(satisfied), "satisfied evidence requirements")
        if set(satisfied) != set(self.evidence_requirements):
            raise ValueError("every reviewed evidence requirement is bound to exactly one typed requirement")
        allowed_outcomes = {
            "goal_resolution", "clarity", "explicit_user_correction", "engagement",
            "social_alignment", "unnecessary_repetition", "confusion", "realization_fidelity",
        }
        if not set(self.outcome_metrics) <= allowed_outcomes:
            raise ValueError("outcome metrics must use the shared outcome vocabulary")
        effect = self.intended_effects[0]
        declared = {kind for kinds in effect.target_roles.values() for kind in kinds}
        if effect.binding_mode != BindingMode.DIALOGUE_SCOPE and not (
            set(self.target_schema.accepted_types) <= declared
        ):
            raise ValueError("effect roles must cover every accepted target type")
        return self

    @property
    def effect(self) -> ActionEffectDefinition:
        return self.intended_effects[0]

    def parameter_rule(self, key: str) -> ActionParameterRule | None:
        return next((item for item in self.parameter_schema if item.parameter_key == key), None)

    def evidence_requirement(self, role: str) -> ActionEvidenceRequirement | None:
        return next((item for item in self.evidence_specification if item.role == role), None)


# ---------------------------------------------------------------------------
# Typed references and authorized inputs
# ---------------------------------------------------------------------------

class ActionTargetRef(FrozenModel):
    role: str = Field(min_length=1)
    target_type: ActionTargetType
    ref: str = Field(min_length=1)
    revision_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> ActionTargetRef:
        _nonblank(self.role, "target role"); _nonblank(self.ref, "target ref")
        if self.revision_ref is not None:
            _nonblank(self.revision_ref, "target revision")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.role, self.target_type.value, self.ref, self.revision_ref or "")


class ActionEvidenceRef(FrozenModel):
    role: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    source_kind: ReferenceSourceKind

    @model_validator(mode="after")
    def valid(self) -> ActionEvidenceRef:
        _nonblank(self.role, "evidence role"); _nonblank(self.ref, "evidence ref")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.role, self.source_kind.value, self.ref)


class ActionGroundedObject(FrozenModel):
    """An authorized, typed object the action stage may address.

    Objects never carry response wording.  Every reference field names another
    indexed object and is resolved by the reference index.
    """

    object_id: str = Field(min_length=1)
    object_type: ActionTargetType
    revision_ref: str | None = None
    status: ObjectStatus = ObjectStatus.CURRENT
    authorized: bool = True
    cognitive_turn_id: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    about_refs: tuple[str, ...] = ()
    focus_contribution: bool = False
    asserted_by: Literal["user", "columbina", "runtime", "memory", "third_party"] | None = None
    epistemic_status: Literal["supported", "tentative", "contradicted", "known_false", "unsupported", "unknown"] | None = None
    support: HypothesisSupport | None = None
    basis_refs: tuple[str, ...] = ()
    contradicted_by_refs: tuple[str, ...] = ()
    replacement_refs: tuple[str, ...] = ()
    explanation_basis_refs: tuple[str, ...] = ()
    example_refs: tuple[str, ...] = ()
    answer_content_refs: tuple[str, ...] = ()
    question_kind: Literal["factual", "causal", "polar"] | None = None
    expression: Literal["explicit", "inferred"] | None = None
    valence: Literal["positive", "negative", "mixed", "unknown"] | None = None
    fulfillment_mode: Literal["conversational", "task"] | None = None
    acceptance_status: Literal[
        "acceptable", "pending", "partially_available", "unavailable", "unauthorized", "prohibited",
    ] | None = None
    decline_reason_refs: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    requires_authorization: bool = False
    requires_consent_refs: tuple[str, ...] = ()
    safety_policy_refs: tuple[str, ...] = ()
    guarantees_external_outcome: bool = False
    requires_false_assertion: bool = False
    discloses_private_data: bool = False
    owner: Literal["user", "columbina", "shared"] | None = None
    requested_by_focus: bool = False
    pursues_ref: str | None = None
    step_refs: tuple[str, ...] = ()
    option_refs: tuple[str, ...] = ()
    topic_status: Literal["inactive", "active", "prior_active"] | None = None
    dialogue_phase: Literal["opening", "open", "closing", "closed"] | None = None
    evidence_status: Literal["available", "missing"] | None = None
    candidate_answer_refs: tuple[str, ...] = ()
    failure_kind: Literal["interpretation", "action", "realization", "memory"] | None = None
    correction_status: Literal["none", "explicitly_corrected"] | None = None
    outcome_assessment: Literal["positive", "negative", "neutral", "unknown"] | None = None
    humor_declined: bool = False
    risk_basis_refs: tuple[str, ...] = ()
    # Contract v0.3: the typed state of a CAPABILITY object cited as a decline
    # reason.  ``unregistered``: no registered capability performs the
    # requested effect; ``unavailable``: it is registered but not available
    # this turn.  Omitted when absent, so earlier payloads keep their bytes.
    capability_status: Literal["available", "unavailable", "unregistered"] | None = None
    # Contract v0.4 (all omitted when absent, so earlier payloads keep their bytes).
    # ``unresolved``: a projected user-owned UNKNOWN the user must supply.
    unresolved: bool = False
    # ARTIFACT_REVISION objects: the artifact operation the delivery performs.
    artifact_operation: Literal[
        "create", "revise", "explain", "evaluate", "describe", "compare", "summarize",
    ] | None = None
    # INQUIRY objects: the question form, timing, and criticality decided upstream.
    inquiry_question_form: Literal["open_set", "yes_no", "choice"] | None = None
    inquiry_timing: Literal["before_primary", "after_primary", "checkpoint"] | None = None
    inquiry_criticality: Literal["blocking", "quality_improving", "optional_curiosity"] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_capability_status(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in ("capability_status", "artifact_operation", "inquiry_question_form", "inquiry_timing",
                         "inquiry_criticality"):
                if data.get(name) is None:
                    data.pop(name, None)
            if data.get("unresolved") is False:
                data.pop("unresolved", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> ActionGroundedObject:
        _nonblank(self.object_id, "grounded object id")
        if self.capability_status is not None and self.object_type != ActionTargetType.CAPABILITY:
            raise ValueError("only a capability object carries a capability status")
        if self.unresolved and (self.object_type != ActionTargetType.UNKNOWN or self.owner != "user"):
            raise ValueError("only a user-owned UNKNOWN object is flagged unresolved")
        if (self.artifact_operation is not None) != (self.object_type == ActionTargetType.ARTIFACT_REVISION):
            raise ValueError("an artifact-revision object, and only it, names its artifact operation")
        inquiry_fields = (self.inquiry_question_form, self.inquiry_timing, self.inquiry_criticality)
        if (self.object_type == ActionTargetType.INQUIRY) != all(item is not None for item in inquiry_fields) or (
            self.object_type != ActionTargetType.INQUIRY and any(item is not None for item in inquiry_fields)
        ):
            raise ValueError("an inquiry object, and only it, names its question form, timing, and criticality")
        reference_fields = (
            self.source_refs, self.about_refs, self.basis_refs, self.contradicted_by_refs,
            self.replacement_refs, self.explanation_basis_refs, self.example_refs,
            self.answer_content_refs, self.decline_reason_refs, self.requires_consent_refs,
            self.safety_policy_refs, self.step_refs, self.option_refs,
            self.candidate_answer_refs, self.risk_basis_refs,
        )
        for values in reference_fields:
            _unique(values, "grounded object references")
            for value in values:
                _nonblank(value, "grounded object reference")
                if value == self.object_id:
                    raise ValueError("grounded objects cannot reference themselves")
        _unique(self.required_capabilities, "required capabilities")
        return self


class ActionTargetDescriptor(FrozenModel):
    """Typed structural facts about an upstream object (unknown, argument, frame, hypothesis)."""

    ref: str = Field(min_length=1)
    target_type: ActionTargetType
    owner: Literal["user", "runtime", "system", "unknown"] | None = None
    unknown_kind: Literal["missing_value", "referent", "meaning", "preference", "goal", "experience", "reason"] | None = None
    represents_ref: str | None = None
    competing_candidate_refs: tuple[str, ...] = ()
    candidate_answer_refs: tuple[str, ...] = ()
    scale_min: int | None = None
    scale_max: int | None = None
    tentative_value_refs: tuple[str, ...] = ()
    explanation_basis_refs: tuple[str, ...] = ()
    about_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> ActionTargetDescriptor:
        _nonblank(self.ref, "descriptor ref")
        for values in (self.competing_candidate_refs, self.candidate_answer_refs,
                       self.tentative_value_refs, self.explanation_basis_refs, self.about_refs):
            _unique(values, "descriptor references")
        if (self.scale_min is None) != (self.scale_max is None):
            raise ValueError("scale bounds must be supplied together")
        if self.scale_min is not None and self.scale_min >= self.scale_max:
            raise ValueError("scale bounds must be finite and ordered")
        if self.candidate_answer_refs and len(self.candidate_answer_refs) < 2:
            raise ValueError("candidate answers require at least two alternatives")
        if self.competing_candidate_refs and len(self.competing_candidate_refs) < 2:
            raise ValueError("competing candidates require at least two alternatives")
        return self


class PriorActionRecord(FrozenModel):
    record_id: str = Field(min_length=1)
    cognitive_turn_ref: str = Field(min_length=1)
    definition_ref: ActionDefinitionRef
    target_refs: tuple[ActionTargetRef, ...] = Field(min_length=1)
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    about_refs: tuple[str, ...] = ()
    authorized: bool = True
    status: ObjectStatus = ObjectStatus.CURRENT
    failure_kind: Literal["interpretation", "action", "realization", "memory"] | None = None
    correction_status: Literal["none", "explicitly_corrected"] = "none"

    @model_validator(mode="after")
    def valid(self) -> PriorActionRecord:
        _json_value(self.parameters)
        _unique(tuple(item.sort_key for item in self.target_refs), "prior action targets")
        return self


class PriorRealizationRecord(FrozenModel):
    record_id: str = Field(min_length=1)
    cognitive_turn_ref: str = Field(min_length=1)
    prior_action_ref: str | None = None
    message_ref: str = Field(min_length=1)
    fidelity: Literal["faithful", "unfaithful", "unknown"] = "unknown"
    asserted_refs: tuple[str, ...] = ()
    about_refs: tuple[str, ...] = ()
    correction_status: Literal["none", "explicitly_corrected"] = "none"
    authorized: bool = True
    status: ObjectStatus = ObjectStatus.CURRENT


class ObservedOutcomeRecord(FrozenModel):
    outcome_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    dimension: Literal[
        "goal_resolution", "clarity", "explicit_user_correction", "engagement",
        "social_alignment", "unnecessary_repetition", "confusion", "realization_fidelity",
    ]
    assessment: Literal["positive", "negative", "neutral", "unknown"]
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    provenance: str = Field(min_length=1)
    focus_contribution: bool = False
    about_refs: tuple[str, ...] = ()
    authorized: bool = True


# ---------------------------------------------------------------------------
# Runtime and personality projections
# ---------------------------------------------------------------------------

# Effects that accept, promise, offer, or propose to carry out an operation.
# Application safety fails closed for exactly these.
COMMITMENT_EFFECT_KEYS = frozenset({
    "coordination.request_acceptance", "coordination.suggestion_acceptance", "coordination.promise",
    "coordination.offer", "coordination.next_step", "coordination.plan_proposal",
})

SAFETY_VIOLATION_CODES: Mapping[SafetyRestrictionReason, str] = {
    SafetyRestrictionReason.BLOCKS_RESPONSE: "application_safety_blocks_response",
    SafetyRestrictionReason.BLOCKS_COMMITMENTS: "application_safety_blocks_operation",
    SafetyRestrictionReason.DECISION_MISSING: "application_safety_decision_missing",
    SafetyRestrictionReason.DECISION_UNKNOWN: "application_safety_decision_unknown",
    SafetyRestrictionReason.DECISION_UNAVAILABLE: "application_safety_decision_unavailable",
    SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED: "application_safety_policy_version_unsupported",
}


def missing_safety_decision_ref(runtime_snapshot_ref: str) -> str:
    return f"{runtime_snapshot_ref}#safety_decision:missing"


class ActionSafetyBasis(FrozenModel):
    """The safety decision one action turn consumed, and the restriction it produced.

    Retained on the result and on every gate result so a trace shows which
    decision, schema, policy version, and producer every eligibility rests on.
    The restriction is recomputed from the recorded decision on validation.
    """

    decision_ref: str = Field(min_length=1)
    runtime_snapshot_ref: str = Field(min_length=1)
    decision_present: bool
    decision_schema_version: str | None = None
    policy_version: str | None = None
    disposition: SafetyDisposition | None = None
    provenance: SafetyDecisionProvenance | None = None
    producer_ref: str | None = None
    restriction: SafetyRestriction
    reason: SafetyRestrictionReason

    @classmethod
    def from_decision(cls, decision: ApplicationSafetyDecision | None, runtime_snapshot_ref: str) -> ActionSafetyBasis:
        restriction, reason = safety_restriction(decision)
        if decision is None:
            return cls(decision_ref=missing_safety_decision_ref(runtime_snapshot_ref), runtime_snapshot_ref=runtime_snapshot_ref,
                       decision_present=False, restriction=restriction, reason=reason)
        return cls(
            decision_ref=decision.decision_id, runtime_snapshot_ref=runtime_snapshot_ref, decision_present=True,
            decision_schema_version=decision.schema_version, policy_version=decision.policy_version,
            disposition=decision.disposition, provenance=decision.provenance, producer_ref=decision.producer_ref,
            restriction=restriction, reason=reason,
        )

    def decision(self) -> ApplicationSafetyDecision | None:
        if not self.decision_present:
            return None
        return ApplicationSafetyDecision(
            schema_version=self.decision_schema_version, decision_id=self.decision_ref, policy_version=self.policy_version,
            disposition=self.disposition, provenance=self.provenance, producer_ref=self.producer_ref,
            runtime_snapshot_ref=self.runtime_snapshot_ref,
        )

    @model_validator(mode="after")
    def valid(self) -> ActionSafetyBasis:
        details = (self.decision_schema_version, self.policy_version, self.disposition, self.provenance, self.producer_ref)
        if not self.decision_present:
            if any(item is not None for item in details) or self.decision_ref != missing_safety_decision_ref(self.runtime_snapshot_ref):
                raise ValueError("a missing safety decision carries no decision details")
        elif any(item is None for item in details):
            raise ValueError("a present safety decision records its schema, policy, disposition, and provenance")
        if (self.restriction, self.reason) != safety_restriction(self.decision()):
            raise ValueError("the recorded safety restriction is not the one the decision produces")
        return self

    def violation_code(self, effect_key: str | None) -> str | None:
        """The application-safety violation this basis imposes on an effect, if any."""

        if self.restriction == SafetyRestriction.RESPONSE:
            return SAFETY_VIOLATION_CODES[self.reason]
        if self.restriction == SafetyRestriction.COMMITMENTS and effect_key in COMMITMENT_EFFECT_KEYS:
            return SAFETY_VIOLATION_CODES[self.reason]
        return None


class ActionRuntimeContext(FrozenModel):
    runtime_snapshot_ref: str = Field(min_length=1)
    response_obligation: Literal["required", "not_required"]
    response_obligation_ref: str | None = None
    conversation_status: Literal["open", "closed"] = "open"
    capability_snapshot_ref: str = Field(min_length=1)
    available_capabilities: tuple[str, ...] = ()
    authorization: str | None = None
    consent: str | None = None
    safety_decision: ApplicationSafetyDecision | None = None
    external_export_control: bool | None = None
    policy_records: tuple[str, ...] = ()
    task_ref: str | None = None
    required_task_parameters: tuple[str, ...] = ()
    provided_task_parameters: Mapping[str, Any] = Field(default_factory=dict)
    deadline_band: str | None = None
    last_tool_event_ref: str | None = None
    last_tool_event_status: str | None = None

    @model_validator(mode="after")
    def valid(self) -> ActionRuntimeContext:
        if self.response_obligation == "required" and not self.response_obligation_ref:
            raise ValueError("a required response obligation needs a reference")
        if self.response_obligation == "not_required" and self.response_obligation_ref is not None:
            raise ValueError("a not-required response cannot carry an obligation reference")
        for label, values in (("available capabilities", self.available_capabilities),
                              ("policy records", self.policy_records),
                              ("required task parameters", self.required_task_parameters)):
            _unique(values, label)
            for value in values:
                _nonblank(value, label)
        if (self.last_tool_event_ref is None) != (self.last_tool_event_status is None):
            raise ValueError("tool-event reference and status are supplied together")
        _json_value(self.provided_task_parameters)
        if self.safety_decision is not None and self.safety_decision.runtime_snapshot_ref != self.runtime_snapshot_ref:
            raise ValueError("the application safety decision belongs to a different runtime snapshot")
        return self

    @property
    def safety_basis(self) -> ActionSafetyBasis:
        return ActionSafetyBasis.from_decision(self.safety_decision, self.runtime_snapshot_ref)


PERSONALITY_TRAITS = ("curiosity", "warmth", "playfulness", "directness")


class ActionPersonalityProfile(FrozenModel):
    profile_id: str = Field(min_length=1)
    mapper_version: str = Field(min_length=1)
    source_core_memory_ref: str = Field(min_length=1)
    status: PersonalityStatus
    curiosity: float | None = None
    warmth: float | None = None
    playfulness: float | None = None
    directness: float | None = None

    @model_validator(mode="after")
    def valid(self) -> ActionPersonalityProfile:
        for value in (self.profile_id, self.mapper_version, self.source_core_memory_ref):
            _nonblank(value, "personality identifier")
        values = tuple(getattr(self, trait) for trait in PERSONALITY_TRAITS)
        if self.status == PersonalityStatus.KNOWN:
            if any(value is None for value in values):
                raise ValueError("known action personality requires all four affinities")
            for trait, value in zip(PERSONALITY_TRAITS, values):
                unit_to_decimal(value, trait)
        elif any(value is not None for value in values):
            raise ValueError("unknown action personality cannot carry affinity values")
        return self

    def affinity(self, trait: str) -> Decimal | None:
        if trait not in PERSONALITY_TRAITS:
            raise ValueError("unknown personality trait")
        value = getattr(self, trait)
        return None if value is None else unit_to_decimal(value, trait)


# ---------------------------------------------------------------------------
# Versions and request
# ---------------------------------------------------------------------------

class ActionVersions(FrozenModel):
    contract_schema: str = ACTION_CONTRACT_SCHEMA_VERSION
    registry: str = ACTION_REGISTRY_VERSION
    construction: str = ACTION_CONSTRUCTION_VERSION
    hard_gate: str = ACTION_HARD_GATE_VERSION
    features: str = ACTION_FEATURE_VERSION
    policy: str = ACTION_POLICY_VERSION
    planner: str = ACTION_PLANNER_VERSION
    numeric_policy: str = ACTION_NUMERIC_POLICY_VERSION
    safety_decision_schema: str = APPLICATION_SAFETY_DECISION_SCHEMA_VERSION
    safety_policy: str = APPLICATION_SAFETY_POLICY_VERSION


class PlanningConditionKind(StrEnum):
    """Why a current-turn user goal is blocked (closed, typed)."""

    MISSING_ARGUMENT = "missing_argument"            # a user-suppliable argument is unresolved
    MISSING_TRUSTED_FACT = "missing_trusted_fact"    # the trusted producer holds no (fresh) fact for a known topic
    MISSING_AUTHORIZATION = "missing_authorization"  # authorization was denied or is absent
    CONFLICTING_FACT = "conflicting_fact"            # active contradiction on the goal's content
    UNSUPPORTED_DOMAIN = "unsupported_domain"        # the request is outside registered predicates
    # Contract v0.3 (typed requirement resolution).
    MISSING_CAPABILITY = "missing_capability"        # no registered capability performs the requested effect
    CAPABILITY_UNAVAILABLE = "capability_unavailable"  # the registered capability is unavailable this turn
    MISSING_CONSENT = "missing_consent"              # the user's explicit consent is not recorded
    SAFETY_PROHIBITED = "safety_prohibited"          # a trusted application safety decision prohibits it
    SAFETY_UNAVAILABLE = "safety_unavailable"        # no safety decision can authorize it (fails closed)
    TRUSTED_CONTENT_UNAVAILABLE = "trusted_content_unavailable"  # no producer can answer this query
    TEMPORARY_FAILURE = "temporary_failure"          # a trusted operation failed this turn
    # Contract v0.4: collaborative content could not be produced for a ready deliverable.
    CONTENT_UNAVAILABLE = "content_unavailable"


class ActionPlanningCondition(FrozenModel):
    """A typed, evidence-linked reason a current-turn user goal is blocked.

    Derived deterministically from one of two typed sources: a Goal rejection
    (``rejection_rule_id``: the goal was not adopted) or, since contract v0.3,
    blocking requirement resolutions of an *adopted* request goal
    (``resolution_refs``).  ``blocker_refs`` are the exact index references a
    move must target to address the blocker.
    """

    condition_id: str = Field(min_length=1)
    kind: PlanningConditionKind
    blocked_goal_ref: str = Field(min_length=1)
    blocked_goal_definition: str = Field(min_length=1)
    rejection_rule_id: str | None = None
    blocker_refs: tuple[str, ...] = ()
    frame_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    resolution_refs: tuple[str, ...] = ()
    # Contract v0.4: a failed collaborative proposal (its typed failure code).
    collaboration_failure: str | None = None

    @model_serializer(mode="wrap")
    def _omit_resolution_source(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            if data.get("rejection_rule_id") is None:
                data.pop("rejection_rule_id", None)
            if not data.get("resolution_refs"):
                data.pop("resolution_refs", None)
            if data.get("collaboration_failure") is None:
                data.pop("collaboration_failure", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> ActionPlanningCondition:
        for label, values in (("blocker refs", self.blocker_refs), ("frame refs", self.frame_refs),
                              ("evidence refs", self.evidence_refs), ("resolution refs", self.resolution_refs)):
            _unique(values, label)
            if values != tuple(sorted(values)):
                raise ValueError(f"planning-condition {label} must be in canonical order")
        sources = sum((self.rejection_rule_id is not None, bool(self.resolution_refs),
                       self.collaboration_failure is not None))
        if sources != 1:
            raise ValueError("a planning condition derives from exactly one rejection, resolution set, or failure")
        if self.rejection_rule_id is not None:
            _nonblank(self.rejection_rule_id, "rejection rule")
        return self

    @property
    def from_resolution(self) -> bool:
        # A failed collaborative proposal is addressed as strictly as a resolution.
        return bool(self.resolution_refs) or self.collaboration_failure is not None


class ActionSelectionRequest(FrozenModel):
    schema_version: Literal["action-contract-v0.4"] = ACTION_CONTRACT_SCHEMA_VERSION
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    core_memory_ref: str = Field(min_length=1)
    interpretation: InterpretationEnvelope
    goal_snapshot: GoalSnapshot
    active_goals: tuple[ActiveGoalView, ...]
    appraisal: AppraisalResult
    dynamic_state: StateSnapshot
    runtime: ActionRuntimeContext
    personality: ActionPersonalityProfile
    grounded_objects: tuple[ActionGroundedObject, ...] = ()
    target_descriptors: tuple[ActionTargetDescriptor, ...] = ()
    prior_action_records: tuple[PriorActionRecord, ...] = ()
    prior_realization_records: tuple[PriorRealizationRecord, ...] = ()
    observed_outcomes: tuple[ObservedOutcomeRecord, ...] = ()
    versions: ActionVersions = Field(default_factory=ActionVersions)
    # Contract v0.3: the typed readiness of this turn's request goals.  Omitted
    # when absent (a request built without requirement resolution).
    requirement_handoff: RequirementActionHandoff | None = None
    # Contract v0.4: what collaborative content production produced for this
    # turn's deliverable goals.  Omitted when absent.
    collaboration_handoff: CollaborationActionHandoff | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_handoff(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict):
            for name in ("requirement_handoff", "collaboration_handoff"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data

    @model_validator(mode="after")
    def valid(self) -> ActionSelectionRequest:
        turn, conversation = self.cognitive_turn_id, self.conversation_ref
        interpretation, goals = self.interpretation, self.goal_snapshot
        if not (turn == interpretation.cognitive_turn_id == goals.cognitive_turn_id
                == self.appraisal.cognitive_turn_id == self.dynamic_state.cognitive_turn_id):
            raise ValueError("action inputs belong to different cognitive turns")
        if not (conversation == interpretation.conversation_ref == goals.conversation_ref
                == self.dynamic_state.conversation_ref):
            raise ValueError("action inputs belong to different conversations")
        if goals.interpretation_trace_ref != interpretation.interpretation_trace_id:
            raise ValueError("goal snapshot cites a different interpretation")
        records = {item.goal_id: item for item in goals.records}
        if tuple(item.goal_id for item in self.active_goals) != tuple(goals.focal_goal_refs):
            raise ValueError("active goals must be the exact focal projection")
        for view in self.active_goals:
            record = records.get(view.goal_id)
            if record is None or record.status != GoalStatus.ACTIVE or view != project_active_goal(record):
                raise ValueError("active goals differ from canonical goal projections")
        if self.appraisal.interpretation_trace_id != interpretation.interpretation_trace_id:
            raise ValueError("appraisal cites a different interpretation")
        focal = set(goals.focal_goal_refs)
        if any(frame.subject.focal_goal_ref not in focal for frame in self.appraisal.frames):
            raise ValueError("appraisal cites a goal outside the supplied focal goals")
        frame_ids = {frame.frame_id for frame in self.appraisal.frames}
        if not set(self.dynamic_state.source_appraisal_revision_ids) <= frame_ids:
            raise ValueError("dynamic state cites frames outside the supplied appraisal")
        if self.dynamic_state.supersedes_snapshot_id != goals.previous_dynamic_state_ref:
            raise ValueError("action stage requires the completed current-turn dynamic state")
        if self.dynamic_state.state_snapshot_id == goals.previous_dynamic_state_ref:
            raise ValueError("action stage cannot consume the previous dynamic state")
        if not (self.runtime.runtime_snapshot_ref == goals.runtime_snapshot_ref
                == interpretation.runtime_snapshot_id):
            raise ValueError("runtime lineage differs across action inputs")
        if self.personality.source_core_memory_ref != self.core_memory_ref:
            raise ValueError("action personality cites a different core memory")
        if self.versions != ActionVersions():
            raise ValueError("request versions differ from the installed action versions")
        identities = (
            *(item.object_id for item in self.grounded_objects),
            *(item.record_id for item in self.prior_action_records),
            *(item.record_id for item in self.prior_realization_records),
            *(item.outcome_id for item in self.observed_outcomes),
        )
        _unique(identities, "grounded object and prior record identities")
        _unique(tuple((item.target_type, item.ref) for item in self.target_descriptors), "target descriptors")
        for item in (*self.prior_action_records, *self.prior_realization_records, *self.observed_outcomes):
            if not item.authorized:
                raise ValueError("unauthorized prior records cannot enter action selection")
        for item in (*self.prior_action_records, *self.prior_realization_records):
            if item.cognitive_turn_ref == turn:
                raise ValueError("prior records must belong to an earlier cognitive turn")
        for item in self.grounded_objects:
            if item.cognitive_turn_id != turn:
                raise ValueError("grounded objects must belong to the current cognitive turn")
        handoff = self.requirement_handoff
        if handoff is not None:
            snapshot = handoff.snapshot
            if (snapshot.cognitive_turn_id, snapshot.conversation_ref, snapshot.interpretation_trace_ref,
                    snapshot.goal_snapshot_ref, snapshot.runtime_snapshot_ref) != (
                    turn, conversation, interpretation.interpretation_trace_id, goals.goal_snapshot_id,
                    self.runtime.runtime_snapshot_ref):
                raise ValueError("the requirement handoff belongs to a different turn, goal, or runtime snapshot")
            for goal_ref in snapshot.goal_refs:
                record = records.get(goal_ref)
                if (record is None or record.status != GoalStatus.ACTIVE or record.created_turn_ref != turn
                        or record.owner not in {GoalOwner.USER, GoalOwner.SHARED}):
                    raise ValueError("requirement resolutions cover only active current-turn request goals")
        collaboration = self.collaboration_handoff
        if collaboration is not None:
            if collaboration.cognitive_turn_id != turn:
                raise ValueError("the collaboration handoff belongs to a different turn")
            for binding in collaboration.bindings:
                record = records.get(binding.goal_ref)
                if record is None or record.status != GoalStatus.ACTIVE or record.created_turn_ref != turn:
                    raise ValueError("collaboration bindings cover only active current-turn goals")
        return self


# ---------------------------------------------------------------------------
# Candidates and gates
# ---------------------------------------------------------------------------

def _canonical_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    return value


class GroundedActionCandidate(FrozenModel):
    candidate_id: str = Field(min_length=1)
    definition_ref: ActionDefinitionRef
    target_refs: tuple[ActionTargetRef, ...] = Field(min_length=1)
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    supporting_evidence_refs: tuple[ActionEvidenceRef, ...] = ()
    proposal_rule_ids: tuple[str, ...] = Field(min_length=1)
    construction_status: Literal[ActionConstructionStatus.INSTANTIATED] = ActionConstructionStatus.INSTANTIATED
    source_refs: tuple[str, ...] = Field(min_length=1)
    registry_version: str = Field(min_length=1)
    binding_fit: str
    binding_fit_rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> GroundedActionCandidate:
        _json_value(self.parameters)
        _unique(tuple(item.sort_key for item in self.target_refs), "candidate targets")
        if tuple(sorted(self.target_refs, key=lambda item: item.sort_key)) != self.target_refs:
            raise ValueError("candidate targets must be in canonical order")
        _unique(tuple(item.sort_key for item in self.supporting_evidence_refs), "candidate evidence")
        if tuple(sorted(self.supporting_evidence_refs, key=lambda item: item.sort_key)) != self.supporting_evidence_refs:
            raise ValueError("candidate evidence must be in canonical order")
        _unique(self.proposal_rule_ids, "proposal rules"); _unique(self.source_refs, "candidate sources")
        if self.binding_fit not in {"1.000000", "0.750000"}:
            raise ValueError("binding fit is exact (1.00) or a reviewed superclass (0.75)")
        return self

    @property
    def normalized_parameters(self) -> str:
        import json
        return json.dumps(_canonical_json(self.parameters), sort_keys=True, separators=(",", ":"))

    @property
    def lexical_identity(self) -> tuple[str, tuple[tuple[str, str, str, str], ...], str]:
        return (self.definition_ref.action_key, tuple(item.sort_key for item in self.target_refs),
                self.normalized_parameters)

    def targets_for(self, *roles: str) -> tuple[ActionTargetRef, ...]:
        return tuple(item for item in self.target_refs if item.role in roles)


class UninstantiableAction(FrozenModel):
    attempt_id: str = Field(min_length=1)
    definition_ref: ActionDefinitionRef
    binding_context_refs: tuple[str, ...] = ()
    missing_codes: tuple[str, ...] = Field(min_length=1)
    source_refs: tuple[str, ...] = ()
    proposal_rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> UninstantiableAction:
        _unique(self.missing_codes, "missing codes")
        return self


class ActionGateViolation(FrozenModel):
    category: ActionGateCategory
    code: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class RealizationConstraint(FrozenModel):
    code: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ActionGateResult(FrozenModel):
    candidate_ref: str = Field(min_length=1)
    status: ActionGateStatus
    violations: tuple[ActionGateViolation, ...] = ()
    realization_constraints: tuple[RealizationConstraint, ...] = ()
    categories_evaluated: tuple[ActionGateCategory, ...] = GATE_CATEGORY_ORDER
    gate_version: str = ACTION_HARD_GATE_VERSION
    definition_version: int | None = None
    definition_lifecycle: ActionLifecycle | None = None
    registry_version: str = ACTION_REGISTRY_VERSION
    safety: ActionSafetyBasis

    @model_validator(mode="after")
    def valid(self) -> ActionGateResult:
        if self.categories_evaluated != GATE_CATEGORY_ORDER:
            raise ValueError("every gate category is evaluated in fixed order")
        order = {category: index for index, category in enumerate(GATE_CATEGORY_ORDER)}
        indexes = tuple(order[item.category] for item in self.violations)
        if indexes != tuple(sorted(indexes)):
            raise ValueError("gate violations must follow category order")
        if (self.status == ActionGateStatus.BLOCKED) != bool(self.violations):
            raise ValueError("a gate result is blocked exactly when violations exist")
        if self.status == ActionGateStatus.PASSED and self.definition_lifecycle != ActionLifecycle.APPROVED:
            raise ValueError("only approved definitions can pass the live gate")
        if self.status == ActionGateStatus.SHADOW_ONLY and self.definition_lifecycle != ActionLifecycle.SHADOW:
            raise ValueError("shadow-only status requires a shadow definition")
        return self

    @property
    def safety_decision_ref(self) -> str:
        return self.safety.decision_ref

    @property
    def eligible_in_shadow(self) -> bool:
        return self.status in {ActionGateStatus.SHADOW_ONLY, ActionGateStatus.PASSED}

    @property
    def eligible_live(self) -> bool:
        return self.status == ActionGateStatus.PASSED


# ---------------------------------------------------------------------------
# Features, scores, effects, moves, and partial-plan states
# ---------------------------------------------------------------------------

class ActionFeatureValue(FrozenModel):
    feature_key: FeatureKey
    value: str
    status: FeatureStatus
    provenance_refs: tuple[str, ...] = ()
    calculator_rule_id: str = Field(min_length=1)
    manifest_entry_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> ActionFeatureValue:
        low, high = FEATURE_RANGES[self.feature_key]
        _bounded(self.value, low, high, self.feature_key.value)
        if self.status != FeatureStatus.KNOWN and decimal_from(self.value) != 0:
            raise ValueError("missing or inapplicable features contribute neutral zero")
        return self


class ActionScoreContribution(FrozenModel):
    term: str = Field(min_length=1)
    feature_key: FeatureKey | None = None
    base_value: str
    weight: str
    signed_contribution: str

    @model_validator(mode="after")
    def valid(self) -> ActionScoreContribution:
        product = decimal_from(self.base_value) * decimal_from(self.weight)
        if canonical_decimal(product) != self.signed_contribution:
            raise ValueError("score contribution differs from base times weight")
        return self


class ActionScoreBreakdown(FrozenModel):
    candidate_ref: str = Field(min_length=1)
    policy_version: str = ACTION_POLICY_VERSION
    need_contributions: tuple[ActionScoreContribution, ...]
    remaining_need: str
    multiplier_contributions: tuple[ActionScoreContribution, ...]
    context_multiplier_unclamped: str
    context_multiplier: str
    semantic_fit: str
    positive_marginal_value: str
    cost_contributions: tuple[ActionScoreContribution, ...]
    shared_move_overhead: str
    score: str

    @model_validator(mode="after")
    def valid(self) -> ActionScoreBreakdown:
        need = sum((decimal_from(item.signed_contribution) for item in self.need_contributions), Decimal(0))
        if canonical_decimal(need) != self.remaining_need:
            raise ValueError("remaining need differs from its contributions")
        raw = Decimal(1) + sum((decimal_from(item.signed_contribution) for item in self.multiplier_contributions), Decimal(0))
        if canonical_decimal(raw) != self.context_multiplier_unclamped:
            raise ValueError("context multiplier differs from its contributions")
        clamped = min(Decimal("1.75"), max(Decimal(0), raw))
        if canonical_decimal(clamped) != self.context_multiplier:
            raise ValueError("context multiplier clamp is inconsistent")
        positive = decimal_from(self.semantic_fit) * decimal_from(self.remaining_need) * decimal_from(self.context_multiplier)
        if canonical_decimal(positive) != self.positive_marginal_value:
            raise ValueError("positive marginal value is inconsistent")
        costs = sum((decimal_from(item.signed_contribution) for item in self.cost_contributions), Decimal(0))
        total = decimal_from(self.positive_marginal_value) + costs + decimal_from(self.shared_move_overhead)
        if canonical_decimal(total) != self.score:
            raise ValueError("final score is inconsistent with its breakdown")
        if self.shared_move_overhead != "-0.300000":
            raise ValueError("shared move overhead is fixed by the policy version")
        return self


# ---------------------------------------------------------------------------
# Canonical policy rules shared by the engine and every trace validator
# ---------------------------------------------------------------------------

NEED_WEIGHTS: tuple[tuple[FeatureKey, str], ...] = (
    (FeatureKey.TARGET_PRIORITY, "0.900000"), (FeatureKey.GOAL_FIT, "0.800000"),
    (FeatureKey.EXPECTED_GOAL_PROGRESS, "1.800000"), (FeatureKey.UNCERTAINTY_REDUCTION, "1.300000"),
    (FeatureKey.CONTRADICTION_REPAIR, "1.400000"),
)
MULTIPLIER_WEIGHTS: tuple[tuple[FeatureKey, str], ...] = (
    (FeatureKey.EVIDENCE_SUPPORT, "0.250000"), (FeatureKey.PERSONALITY_AFFINITY, "0.100000"),
    (FeatureKey.STATE_COMPATIBILITY, "0.250000"), (FeatureKey.PLAN_CONTRIBUTION, "0.250000"),
)
COST_WEIGHTS: tuple[tuple[FeatureKey, str], ...] = (
    (FeatureKey.REPETITION_COST, "-1.800000"), (FeatureKey.RESPONSE_COST, "-0.800000"),
    (FeatureKey.SOFT_RISK_COST, "-1.200000"),
)
SHARED_MOVE_OVERHEAD = "-0.300000"


def deterministic_selection(
    scored: tuple[Any, ...], lexical_identities: Mapping[str, Any],
) -> tuple[str | None, tuple[str, ...], TieBreakField | None]:
    """The one V0 argmax: STOP unless strictly positive, then the ordered tie policy.

    Returns the winning candidate ref (or ``None`` for STOP), the complete tie
    set (empty when there is no tie), and the field that resolved the tie.
    """

    if not scored:
        return None, (), None
    best = max(decimal_from(item.score.score) for item in scored)
    if best - ACTION_SCORE_TOLERANCE <= 0:
        return None, (), None
    tied = [item for item in scored if best - decimal_from(item.score.score) <= ACTION_SCORE_TOLERANCE]
    if len(tied) == 1:
        return tied[0].candidate_ref, (), None
    orderings = (
        (TieBreakField.EXPECTED_GOAL_PROGRESS, lambda item: -item.feature(FeatureKey.EXPECTED_GOAL_PROGRESS)),
        (TieBreakField.TARGET_PRIORITY, lambda item: -item.feature(FeatureKey.TARGET_PRIORITY)),
        (TieBreakField.SOFT_RISK_COST, lambda item: item.feature(FeatureKey.SOFT_RISK_COST)),
        (TieBreakField.RESPONSE_COST, lambda item: item.feature(FeatureKey.RESPONSE_COST)),
    )
    tie_set = tuple(item.candidate_ref for item in tied)
    remaining = list(tied)
    for field, key in orderings:
        lowest = min(key(item) for item in remaining)
        remaining = [item for item in remaining if key(item) == lowest]
        if len(remaining) == 1:
            return remaining[0].candidate_ref, tie_set, field
    winner = min(remaining, key=lambda item: lexical_identities[item.candidate_ref])
    return winner.candidate_ref, tie_set, TieBreakField.LEXICAL_IDENTITY


def effect_ledger_keys(
    effect_key: str, effect_version: int, planned_state: str, binding_mode: BindingMode,
    target_refs: tuple[ActionTargetRef, ...],
) -> tuple[tuple[str, tuple[ActionTargetRef, ...]], ...]:
    """Canonical target-effect ledger identities for one bound effect."""

    prefix = f"{effect_key}@{effect_version}|{planned_state}|"
    by_role = lambda *roles: tuple(item for item in target_refs if item.role in roles)  # noqa: E731
    if binding_mode == BindingMode.EACH_TARGET:
        return tuple((prefix + f"{item.target_type.value}:{item.ref}", (item,)) for item in by_role("primary"))
    if binding_mode == BindingMode.TARGET_SET:
        members = by_role("member")
        identity = ",".join(sorted(f"{item.target_type.value}:{item.ref}" for item in members))
        return ((prefix + f"set[{identity}]", members),)
    if binding_mode == BindingMode.DIALOGUE_SCOPE:
        scope = by_role("scope")
        return ((prefix + f"{scope[0].target_type.value}:{scope[0].ref}", scope),)
    if binding_mode == BindingMode.SOURCE_DESTINATION:
        source, destination = by_role("source"), by_role("destination")
        return ((prefix + f"{source[0].ref}->{destination[0].ref}", (*source, *destination)),)
    primary = by_role("primary")
    return ((prefix + f"{primary[0].target_type.value}:{primary[0].ref}", primary),)


def plan_state_signature(fields: Mapping[str, Any]) -> str:
    """Canonical digest of every partial-plan field except the signature itself."""

    import hashlib
    import json

    payload = json.dumps({key: value for key, value in fields.items() if key != "state_signature"},
                         sort_keys=True, separators=(",", ":"))
    return f"plan-state-{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


class ScoredCandidate(FrozenModel):
    candidate_ref: str = Field(min_length=1)
    features: tuple[ActionFeatureValue, ...]
    score: ActionScoreBreakdown

    @model_validator(mode="after")
    def valid(self) -> ScoredCandidate:
        if tuple(item.feature_key for item in self.features) != FEATURE_ORDER:
            raise ValueError("every scored candidate carries all 13 features in manifest order")
        if self.score.candidate_ref != self.candidate_ref:
            raise ValueError("score breakdown cites a different candidate")
        values = {item.feature_key: item.value for item in self.features}
        for terms, weights in (
            (self.score.need_contributions, NEED_WEIGHTS), (self.score.multiplier_contributions, MULTIPLIER_WEIGHTS),
            (self.score.cost_contributions, COST_WEIGHTS),
        ):
            if tuple((item.feature_key, item.weight, item.base_value) for item in terms) != tuple(
                (key, weight, values[key]) for key, weight in weights
            ):
                raise ValueError("score terms must use this candidate's features and the canonical policy weights")
        if self.score.semantic_fit != values[FeatureKey.SEMANTIC_FIT]:
            raise ValueError("score semantic fit differs from the feature")
        if self.score.policy_version != ACTION_POLICY_VERSION:
            raise ValueError("score was computed by a different policy version")
        return self

    def feature(self, key: FeatureKey) -> Decimal:
        return decimal_from(next(item for item in self.features if item.feature_key == key).value)


class PlannedActionEffect(FrozenModel):
    effect_id: str = Field(min_length=1)
    move_ref: str = Field(min_length=1)
    effect_key: str = Field(min_length=1)
    effect_version: int = Field(ge=1)
    transition_domain: TransitionDomain
    planned_state: str = Field(min_length=1)
    binding_mode: BindingMode
    target_refs: tuple[ActionTargetRef, ...] = Field(min_length=1)
    support_refs: tuple[ActionTargetRef, ...] = ()
    effect_strength: str
    ledger_key: str = Field(min_length=1)
    actual_outcome_claim: Literal[False] = False

    @model_validator(mode="after")
    def valid(self) -> PlannedActionEffect:
        if not self.planned_state.endswith("_planned"):
            raise ValueError("planned effects record planned states only")
        _bounded(self.effect_strength, "0", "1", "effect strength")
        return self


class GroundedActionMove(FrozenModel):
    move_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    candidate_ref: str = Field(min_length=1)
    definition_ref: ActionDefinitionRef
    family: ActionFamily
    target_refs: tuple[ActionTargetRef, ...] = Field(min_length=1)
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    supporting_evidence_refs: tuple[ActionEvidenceRef, ...] = ()
    realization_constraints: tuple[RealizationConstraint, ...] = ()
    planned_effect_refs: tuple[str, ...] = Field(min_length=1)
    selection_score: str
    # ``model_policy`` (planner model-policy-v1): the model chose this eligible
    # move; its deterministic score is recorded but is a preference, not a
    # gate.  Omitted for the historical argmax policy.
    selection_policy: Literal["argmax", "model_policy"] = "argmax"

    @model_serializer(mode="wrap")
    def _omit_default_policy(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("selection_policy") == "argmax":
            data.pop("selection_policy", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> GroundedActionMove:
        _json_value(self.parameters)
        decimal_from(self.selection_score)
        if self.selection_policy == "argmax" and decimal_from(self.selection_score) <= 0:
            raise ValueError("a selected move must have strictly positive score")
        return self


class LedgerEntry(FrozenModel):
    key: str = Field(min_length=1)
    value: str

    @model_validator(mode="after")
    def valid(self) -> LedgerEntry:
        _bounded(self.value, "0", "1", "ledger value")
        return self


class StateLabel(FrozenModel):
    key: str = Field(min_length=1)
    state: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> StateLabel:
        if not self.state.endswith("_planned"):
            raise ValueError("partial-plan labels record planned states only")
        return self


class CountEntry(FrozenModel):
    key: str = Field(min_length=1)
    count: int = Field(ge=1)


def _sorted_keys(values: tuple[Any, ...], label: str) -> None:
    keys = tuple(item.key for item in values)
    _unique(keys, label)
    if keys != tuple(sorted(keys)):
        raise ValueError(f"{label} must be in canonical key order")


class PartialPlanState(FrozenModel):
    plan_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    selected_moves: tuple[str, ...] = ()
    represented_effects: tuple[str, ...] = ()
    addressed_target_effects: tuple[LedgerEntry, ...] = ()
    residual_target_needs: tuple[LedgerEntry, ...] = ()
    planned_goal_coverage: tuple[LedgerEntry, ...] = ()
    planned_unknown_handling: tuple[StateLabel, ...] = ()
    planned_contradiction_repairs: tuple[StateLabel, ...] = ()
    uptake_coverage: tuple[LedgerEntry, ...] = ()
    action_counts: tuple[CountEntry, ...] = ()
    family_counts: tuple[CountEntry, ...] = ()
    previous_move_ref: str | None = None
    accumulated_response_cost: str = "0.000000"
    accumulated_soft_risk: str = "0.000000"
    state_signature: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> PartialPlanState:
        for label, values in (
            ("addressed target effects", self.addressed_target_effects),
            ("residual target needs", self.residual_target_needs),
            ("planned goal coverage", self.planned_goal_coverage),
            ("planned unknown handling", self.planned_unknown_handling),
            ("planned contradiction repairs", self.planned_contradiction_repairs),
            ("uptake coverage", self.uptake_coverage),
            ("action counts", self.action_counts), ("family counts", self.family_counts),
        ):
            _sorted_keys(values, label)
        _unique(self.selected_moves, "selected moves"); _unique(self.represented_effects, "represented effects")
        if len(self.selected_moves) != self.step_index:
            raise ValueError("partial-plan step index equals the number of selected moves")
        if (self.previous_move_ref is None) != (not self.selected_moves):
            raise ValueError("previous move reference must name the last selected move")
        if self.selected_moves and self.previous_move_ref != self.selected_moves[-1]:
            raise ValueError("previous move reference must name the last selected move")
        if sum(item.count for item in self.action_counts) != self.step_index:
            raise ValueError("action counts must equal the number of selected moves")
        if sum(item.count for item in self.family_counts) != self.step_index:
            raise ValueError("family counts must equal the number of selected moves")
        decimal_from(self.accumulated_response_cost); decimal_from(self.accumulated_soft_risk)
        if self.state_signature != plan_state_signature(self.model_dump(mode="json")):
            raise ValueError("partial-plan state signature does not match its contents")
        return self

    @classmethod
    def signed(cls, **fields: Any) -> PartialPlanState:
        """Build a state whose signature is computed from its canonical contents."""

        draft = cls.model_construct(**{**fields, "state_signature": "unsigned"})
        return cls(**{**fields, "state_signature": plan_state_signature(draft.model_dump(mode="json"))})

    def ledger(self, name: str) -> dict[str, Decimal]:
        return {item.key: decimal_from(item.value) for item in getattr(self, name)}

    def labels(self, name: str) -> dict[str, str]:
        return {item.key: item.state for item in getattr(self, name)}


class PartialPlanDelta(FrozenModel):
    added_move_ref: str = Field(min_length=1)
    added_effect_refs: tuple[str, ...] = Field(min_length=1)
    target_effect_updates: tuple[LedgerEntry, ...] = ()
    goal_coverage_updates: tuple[LedgerEntry, ...] = ()
    unknown_handling_updates: tuple[StateLabel, ...] = ()
    contradiction_updates: tuple[StateLabel, ...] = ()
    uptake_updates: tuple[LedgerEntry, ...] = ()
    added_response_cost: str
    added_soft_risk: str


class TransitionGoalLink(FrozenModel):
    """Typed authorization for one goal-ledger transition."""

    coverage_key: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    requirement_ref: str = Field(min_length=1)
    strength: str
    rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> TransitionGoalLink:
        if self.coverage_key != f"{self.goal_ref}#{self.requirement_ref}":
            raise ValueError("transition goal coverage key must name its goal and requirement")
        _bounded(self.strength, "0", "1", "transition goal-link strength")
        if decimal_from(self.strength) <= 0:
            raise ValueError("transition goal-link strength must be positive")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.coverage_key, self.rule_id, self.strength)


class TransitionBasis(FrozenModel):
    """The typed analysis facts that authorize semantic partial-state updates."""

    candidate_ref: str = Field(min_length=1)
    goal_links: tuple[TransitionGoalLink, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    uptake_targets: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> TransitionBasis:
        for values, label in (
            (self.unknown_refs, "transition unknown refs"),
            (self.contradiction_refs, "transition contradiction refs"),
            (self.uptake_targets, "transition uptake targets"),
        ):
            _unique(values, label)
            if values != tuple(sorted(values)):
                raise ValueError(f"{label} must be in canonical order")
        keys = tuple(item.sort_key for item in self.goal_links)
        _unique(keys, "transition goal links")
        if keys != tuple(sorted(keys)):
            raise ValueError("transition goal links must be in canonical order")
        _unique(tuple(item.coverage_key for item in self.goal_links), "transition goal coverage keys")
        return self


class ActionTieResolution(FrozenModel):
    tied_candidate_refs: tuple[str, ...] = Field(min_length=2)
    resolved_by: TieBreakField
    selected_candidate_ref: str = Field(min_length=1)
    preference_evidence: bool

    @model_validator(mode="after")
    def valid(self) -> ActionTieResolution:
        _unique(self.tied_candidate_refs, "tied candidates")
        if self.selected_candidate_ref not in self.tied_candidate_refs:
            raise ValueError("tie selection must be one of the tied candidates")
        if self.resolved_by == TieBreakField.LEXICAL_IDENTITY and self.preference_evidence:
            raise ValueError("lexical tie resolution is never preference evidence")
        return self


MODEL_ACTION_PLANNER_VERSION = 'action-planner-model-grounded-v0.1'
MODEL_POLICY_PLANNER_VERSION = 'action-planner-model-policy-v1'
MODEL_PLANNER_VERSIONS = frozenset({MODEL_ACTION_PLANNER_VERSION, MODEL_POLICY_PLANNER_VERSION})

def action_versions_supported(versions):
    if isinstance(versions, Mapping):
        if set(versions) != set(ActionVersions.model_fields):
            return False
        try:
            versions = ActionVersions.model_validate(versions)
        except (ValueError, TypeError):
            return False
    return versions in (ActionVersions(), ActionVersions(planner=MODEL_ACTION_PLANNER_VERSION),
                        ActionVersions(planner=MODEL_POLICY_PLANNER_VERSION))


class ModelActionChoice(FrozenModel):
    source_call_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    proposal_index: int = Field(ge=0)
    candidate_ref: str | None


class ActionPlanningStep(FrozenModel):
    step_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    context_ref: str = Field(min_length=1)
    partial_state_before: PartialPlanState
    constructed_candidates: tuple[GroundedActionCandidate, ...] = ()
    uninstantiable: tuple[UninstantiableAction, ...] = ()
    gate_results: tuple[ActionGateResult, ...] = ()
    scored_candidates: tuple[ScoredCandidate, ...] = ()
    stop_score: str = "0.000000"
    model_choice: ModelActionChoice | None = None
    selected_candidate_ref: str | None = None
    tie_resolution: ActionTieResolution | None = None
    applied_effects: tuple[PlannedActionEffect, ...] = ()
    transition_basis: TransitionBasis | None = None
    partial_state_delta: PartialPlanDelta | None = None
    partial_state_after: PartialPlanState
    termination: PlanTerminationKind | None = None
    # Eligible candidates withheld from selection at this step because a
    # blocking planning condition was still unaddressed and at least one
    # eligible candidate addressed it (planner v0.2).  Omitted when empty.
    condition_restricted_candidate_refs: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_empty_restriction(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("condition_restricted_candidate_refs"):
            data.pop("condition_restricted_candidate_refs", None)
        if isinstance(data, dict) and data.get('model_choice') is None:
            data.pop('model_choice', None)
        return data

    @model_validator(mode="after")
    def valid(self) -> ActionPlanningStep:
        if self.stop_score != "0.000000":
            raise ValueError("STOP has zero marginal value")
        candidates = {item.candidate_id for item in self.constructed_candidates}
        _unique(tuple(item.candidate_id for item in self.constructed_candidates), "constructed candidates")
        if tuple(item.candidate_ref for item in self.gate_results) != tuple(item.candidate_id for item in self.constructed_candidates):
            raise ValueError("every constructed candidate has exactly one gate result in order")
        self._validate_gate_invariants()
        eligible = {item.candidate_ref for item in self.gate_results if item.eligible_in_shadow}
        if tuple(item.candidate_ref for item in self.scored_candidates) != tuple(
            item.candidate_id for item in self.constructed_candidates if item.candidate_id in eligible
        ):
            raise ValueError("every eligible candidate, and only those, is scored in order")
        restricted = self.condition_restricted_candidate_refs
        _unique(restricted, "condition-restricted candidates")
        if restricted != tuple(item.candidate_ref for item in self.scored_candidates if item.candidate_ref in set(restricted)):
            raise ValueError("condition-restricted candidates must be scored candidates in order")
        if restricted and len(restricted) == len(self.scored_candidates):
            raise ValueError("a restriction must leave at least one blocker-addressing candidate selectable")
        selected = self.selected_candidate_ref
        identities = {item.candidate_id: item.lexical_identity for item in self.constructed_candidates}
        winner, tie_set, resolved_by = deterministic_selection(
            tuple(item for item in self.scored_candidates if item.candidate_ref not in set(restricted)), identities,
        )
        if self.model_choice is not None:
            if self.model_choice.candidate_ref != selected or self.model_choice.proposal_index != self.step_index:
                raise ValueError('model choice does not bind this step')
            if selected in set(restricted) or self.tie_resolution is not None:
                raise ValueError('model selection violates a blocker restriction or claims an argmax tie')
        elif selected != winner:
            raise ValueError("the recorded selection is not the deterministic policy argmax")
        if self.model_choice is not None:
            pass
        elif tie_set:
            expected_tie = ActionTieResolution(
                tied_candidate_refs=tie_set, resolved_by=resolved_by, selected_candidate_ref=winner,
                preference_evidence=resolved_by != TieBreakField.LEXICAL_IDENTITY,
            )
            if self.tie_resolution != expected_tie:
                raise ValueError("the recorded tie set, tie-break field, or winner differs from the policy")
        elif self.tie_resolution is not None:
            raise ValueError("a tie resolution was recorded where no tie exists")
        if selected is not None:
            if selected not in eligible or selected not in candidates:
                raise ValueError("the selected candidate must be eligible")
            if (self.termination is not None or self.transition_basis is None
                    or self.partial_state_delta is None or not self.applied_effects):
                raise ValueError("a selection step records its transition basis, applies effects, and continues")
            candidate = next(item for item in self.constructed_candidates if item.candidate_id == selected)
            scored = next(item for item in self.scored_candidates if item.candidate_ref == selected)
            if self.transition_basis.candidate_ref != selected:
                raise ValueError("the transition basis belongs to a different candidate")
            self._validate_effects(candidate)
            self._validate_delta(candidate, scored)
        else:
            if (self.termination is None or self.applied_effects or self.transition_basis is not None
                    or self.partial_state_delta is not None):
                raise ValueError("a terminal step records termination and no effects")
            if self.partial_state_after != self.partial_state_before:
                raise ValueError("a terminal step cannot change the partial plan")
        return self

    def _validate_gate_invariants(self) -> None:
        """Safety blocks and evidence deficiencies recorded as eligible are forgeries."""

        from ..action_selection_v0.registry import DEFAULT_REGISTRY

        if any(item.safety != self.gate_results[0].safety for item in self.gate_results):
            raise ValueError("every gate result in a step consumes the same safety decision")
        for candidate, gate in zip(self.constructed_candidates, self.gate_results):
            definition = DEFAULT_REGISTRY.get(candidate.definition_ref)
            codes = {(item.category, item.code) for item in gate.violations}
            safety_code = gate.safety.violation_code(definition.effect.effect_key if definition else None)
            if safety_code is not None and (ActionGateCategory.APPLICATION_SAFETY, safety_code) not in codes:
                raise ValueError("a candidate the safety decision restricts is recorded without its safety violation")
            if definition is not None and gate.status != ActionGateStatus.BLOCKED and evidence_specification_violations(
                definition.evidence_specification, candidate.supporting_evidence_refs, candidate.candidate_id,
            ):
                raise ValueError("an evidence-deficient candidate is recorded as eligible")

    def _validate_effects(self, candidate: GroundedActionCandidate) -> None:
        from ..action_selection_v0.registry import DEFAULT_REGISTRY

        definition = DEFAULT_REGISTRY.get(candidate.definition_ref)
        if definition is None:
            raise ValueError("applied effects cite an unregistered definition")
        declared = definition.effect
        bindings = effect_ledger_keys(declared.effect_key, declared.effect_version, declared.planned_state,
                                      declared.binding_mode, candidate.target_refs)
        support = candidate.targets_for("support")
        if len(bindings) != len(self.applied_effects):
            raise ValueError("applied effects do not match the selected definition's bindings")
        for effect, (ledger_key, targets) in zip(self.applied_effects, bindings):
            if (effect.effect_key, effect.effect_version, effect.transition_domain, effect.planned_state,
                    effect.binding_mode, effect.effect_strength) != (
                    declared.effect_key, declared.effect_version, declared.transition_domain, declared.planned_state,
                    declared.binding_mode, declared.effect_strength):
                raise ValueError("an applied effect is not the selected definition's declared effect")
            if effect.target_refs != targets or effect.support_refs != support or effect.ledger_key != ledger_key:
                raise ValueError("an applied effect is not bound to the selected candidate's targets")
            if effect.move_ref != self.partial_state_delta.added_move_ref:
                raise ValueError("an applied effect belongs to a different move")

    def _validate_delta(self, candidate: GroundedActionCandidate, scored: ScoredCandidate) -> None:
        before, after, delta = self.partial_state_before, self.partial_state_after, self.partial_state_delta
        if delta.added_effect_refs != tuple(item.effect_id for item in self.applied_effects):
            raise ValueError("the delta cites different effects than those applied")
        if (delta.added_response_cost, delta.added_soft_risk) != (
            canonical_decimal(scored.feature(FeatureKey.RESPONSE_COST)), canonical_decimal(scored.feature(FeatureKey.SOFT_RISK_COST)),
        ):
            raise ValueError("the delta's costs differ from the selected candidate's features")
        addressed = before.ledger("addressed_target_effects")
        residual = before.ledger("residual_target_needs")
        target_updates = {item.key: decimal_from(item.value) for item in delta.target_effect_updates}
        expected_targets = {}
        for effect in self.applied_effects:
            expected_targets[effect.ledger_key] = max(addressed.get(effect.ledger_key, Decimal(0)), decimal_from(effect.effect_strength))
        if target_updates != expected_targets:
            raise ValueError("target-effect coverage updates differ from the applied effects")
        for key, coverage in target_updates.items():
            addressed[key] = coverage
            residual[key] = max(Decimal(0), Decimal(1) - coverage)
        goals = before.ledger("planned_goal_coverage")
        expected_goal_updates: dict[str, Decimal] = {}
        for link in self.transition_basis.goal_links:
            updated = max(goals.get(link.coverage_key, Decimal(0)), decimal_from(link.strength))
            if updated != goals.get(link.coverage_key):
                expected_goal_updates[link.coverage_key] = updated
            goals[link.coverage_key] = updated
        recorded_goal_updates = {item.key: decimal_from(item.value) for item in delta.goal_coverage_updates}
        if recorded_goal_updates != expected_goal_updates:
            raise ValueError("goal coverage updates differ from the transition basis")
        unknowns = before.labels("planned_unknown_handling")
        effect = self.applied_effects[0]
        unknown_label = ("qualification_planned" if effect.effect_key == "information.uncertainty_qualification"
                         else effect.planned_state)
        if self.transition_basis.unknown_refs and unknown_label not in {
            "elicitation_planned", "verification_planned", "qualification_planned",
        }:
            raise ValueError("the transition basis cannot authorize this unknown-handling state")
        expected_unknown_updates: dict[str, str] = {}
        for ref in self.transition_basis.unknown_refs:
            if unknowns.get(ref) in {"elicitation_planned", "verification_planned"} and unknown_label == "qualification_planned":
                continue
            unknowns[ref] = unknown_label
            expected_unknown_updates[ref] = unknown_label
        recorded_unknown_updates = {item.key: item.state for item in delta.unknown_handling_updates}
        if recorded_unknown_updates != expected_unknown_updates:
            raise ValueError("unknown-handling updates differ from the transition basis")
        repairs = before.labels("planned_contradiction_repairs")
        repair_label = ("correction_planned" if effect.transition_domain == TransitionDomain.CONTRADICTION_HANDLING
                        else "exposure_planned")
        expected_repair_updates = {ref: repair_label for ref in self.transition_basis.contradiction_refs}
        repairs.update(expected_repair_updates)
        recorded_repair_updates = {item.key: item.state for item in delta.contradiction_updates}
        if recorded_repair_updates != expected_repair_updates:
            raise ValueError("contradiction updates differ from the transition basis")
        uptake = before.ledger("uptake_coverage")
        expected_uptake_updates = {ref: Decimal(1) for ref in self.transition_basis.uptake_targets}
        uptake.update(expected_uptake_updates)
        recorded_uptake_updates = {item.key: decimal_from(item.value) for item in delta.uptake_updates}
        if recorded_uptake_updates != expected_uptake_updates:
            raise ValueError("uptake updates differ from the transition basis")
        actions = {item.key: item.count for item in before.action_counts}
        families = {item.key: item.count for item in before.family_counts}
        key = candidate.definition_ref.action_key
        actions[key] = actions.get(key, 0) + 1
        families[key.partition(".")[0]] = families.get(key.partition(".")[0], 0) + 1
        ledger = lambda values: tuple(LedgerEntry(key=k, value=canonical_decimal(values[k])) for k in sorted(values))  # noqa: E731
        labels = lambda values: tuple(StateLabel(key=k, state=values[k]) for k in sorted(values))  # noqa: E731
        expected = PartialPlanState.signed(
            plan_id=before.plan_id, step_index=before.step_index + 1,
            selected_moves=(*before.selected_moves, delta.added_move_ref),
            represented_effects=(*before.represented_effects, *delta.added_effect_refs),
            addressed_target_effects=ledger(addressed), residual_target_needs=ledger(residual),
            planned_goal_coverage=ledger(goals), planned_unknown_handling=labels(unknowns),
            planned_contradiction_repairs=labels(repairs), uptake_coverage=ledger(uptake),
            action_counts=tuple(CountEntry(key=k, count=actions[k]) for k in sorted(actions)),
            family_counts=tuple(CountEntry(key=k, count=families[k]) for k in sorted(families)),
            previous_move_ref=delta.added_move_ref,
            accumulated_response_cost=canonical_decimal(decimal_from(before.accumulated_response_cost) + decimal_from(delta.added_response_cost)),
            accumulated_soft_risk=canonical_decimal(decimal_from(before.accumulated_soft_risk) + decimal_from(delta.added_soft_risk)),
        )
        if after != expected:
            raise ValueError("the after-state is not the before-state with the recorded delta applied")


def primary_comparison(step: ActionPlanningStep, move_ref: str, focal_primary_goal_ref: str) -> dict[str, str]:
    """The factual fields the primary-designation rule compares for one move."""

    import json

    prefix = f"{focal_primary_goal_ref}#"
    before = step.partial_state_before.ledger("planned_goal_coverage")
    coverage = sum(
        (decimal_from(item.value) - before.get(item.key, Decimal(0))
         for item in step.partial_state_delta.goal_coverage_updates if item.key.startswith(prefix)),
        Decimal(0),
    )
    scored = next(item for item in step.scored_candidates if item.candidate_ref == step.selected_candidate_ref)
    candidate = next(item for item in step.constructed_candidates if item.candidate_id == step.selected_candidate_ref)
    identity = candidate.lexical_identity
    return {
        "move_ref": move_ref, "direct_focal_coverage": canonical_decimal(coverage),
        "expected_goal_progress": canonical_decimal(scored.feature(FeatureKey.EXPECTED_GOAL_PROGRESS)),
        "target_priority": canonical_decimal(scored.feature(FeatureKey.TARGET_PRIORITY)),
        "identity": json.dumps([identity[0], [list(item) for item in identity[1]], identity[2]]),
    }


def designate_primary(compared: list[Mapping[str, str]]) -> str:
    """Greatest direct focal coverage, then progress, then priority, then identity."""

    return min(compared, key=lambda item: (
        -decimal_from(item["direct_focal_coverage"]), -decimal_from(item["expected_goal_progress"]),
        -decimal_from(item["target_priority"]), item["identity"],
    ))["move_ref"]


class PrimaryDesignation(FrozenModel):
    rule_id: str = Field(min_length=1)
    focal_primary_goal_ref: str = Field(min_length=1)
    primary_move_ref: str = Field(min_length=1)
    compared_moves: tuple[Mapping[str, str], ...] = Field(min_length=1)


class ActionPlan(FrozenModel):
    plan_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    moves: tuple[GroundedActionMove, ...] = ()
    primary_move_ref: str | None = None
    supporting_move_refs: tuple[str, ...] = ()
    move_priorities: tuple[Mapping[str, str], ...] = ()
    primary_designation: PrimaryDesignation | None = None
    final_partial_state_ref: str = Field(min_length=1)
    termination: PlanTerminationKind
    response_obligation: Literal["required", "not_required"]
    versions: ActionVersions = Field(default_factory=ActionVersions)

    @model_validator(mode="after")
    def valid(self) -> ActionPlan:
        move_ids = tuple(item.move_id for item in self.moves)
        _unique(move_ids, "plan moves")
        if tuple(item.step_index for item in self.moves) != tuple(range(len(self.moves))):
            raise ValueError("plan moves must be in contiguous selection order")
        if self.termination != PlanTerminationKind.STOP:
            raise ValueError("a completed plan terminates with STOP")
        if not self.moves:
            if self.response_obligation == "required":
                raise ValueError("a required response cannot complete with an empty plan")
            if self.primary_move_ref is not None or self.supporting_move_refs or self.primary_designation:
                raise ValueError("an empty plan has no primary or supporting moves")
            return self
        if self.primary_move_ref not in move_ids or self.primary_designation is None:
            raise ValueError("a non-empty plan has exactly one derived primary move")
        if self.primary_designation.primary_move_ref != self.primary_move_ref:
            raise ValueError("primary designation disagrees with the plan")
        if self.supporting_move_refs != tuple(item for item in move_ids if item != self.primary_move_ref):
            raise ValueError("every non-primary move is supporting, in plan order")
        expected = tuple(
            {"move_ref": item, "priority": MovePriority.PRIMARY.value if item == self.primary_move_ref else MovePriority.SUPPORTING.value}
            for item in move_ids
        )
        if tuple(dict(item) for item in self.move_priorities) != expected:
            raise ValueError("move priorities differ from the derived designation")
        return self


class ActionSelectionResult(FrozenModel):
    result_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    request_digest: str = Field(min_length=1)
    context_ref: str = Field(min_length=1)
    plan: ActionPlan
    steps: tuple[ActionPlanningStep, ...] = Field(min_length=1)
    moves: tuple[GroundedActionMove, ...] = ()
    planned_effects: tuple[PlannedActionEffect, ...] = ()
    shadow_only: Literal[True] = True
    safety: ActionSafetyBasis
    versions: ActionVersions = Field(default_factory=ActionVersions)
    # Typed reasons current-turn user goals were blocked (planner v0.2).
    planning_conditions: tuple[ActionPlanningCondition, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_empty_conditions(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("planning_conditions"):
            data.pop("planning_conditions", None)
        return data

    @model_validator(mode="after")
    def valid(self) -> ActionSelectionResult:
        if self.plan.cognitive_turn_id != self.cognitive_turn_id:
            raise ValueError("plan belongs to a different cognitive turn")
        _unique(tuple(item.condition_id for item in self.planning_conditions), "planning conditions")
        if any(step.condition_restricted_candidate_refs for step in self.steps) and not self.planning_conditions:
            raise ValueError("a selection restriction requires a recorded planning condition")
        if any(gate.safety != self.safety for step in self.steps for gate in step.gate_results):
            raise ValueError("every gate result consumes the turn's one safety decision")
        if self.moves != self.plan.moves:
            raise ValueError("result moves must equal the plan moves")
        model_selected = self.versions.planner in MODEL_PLANNER_VERSIONS
        model_policy = self.versions.planner == MODEL_POLICY_PLANNER_VERSION
        installed = ActionVersions(planner=self.versions.planner) if model_selected else ActionVersions()
        if self.versions != self.plan.versions or self.versions != installed:
            raise ValueError("result versions must be the installed action versions")
        if any((step.model_choice is not None) != model_selected for step in self.steps):
            raise ValueError('every step must use the recorded selection policy')
        if model_selected and len({step.model_choice.source_call_digest for step in self.steps}) != 1:
            raise ValueError('model choices must belong to one captured proposal')
        if tuple(item.step_index for item in self.steps) != tuple(range(len(self.steps))):
            raise ValueError("planning steps must be contiguous")
        if any(item.context_ref != self.context_ref for item in self.steps):
            raise ValueError("every planning step cites the same immutable context")
        for previous, current in zip(self.steps, self.steps[1:]):
            if current.partial_state_before != previous.partial_state_after:
                raise ValueError("partial-plan states must form an unbroken chain")
        signatures = [self.steps[0].partial_state_before.state_signature] + [
            item.partial_state_after.state_signature for item in self.steps if item.selected_candidate_ref
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError("partial-plan state signatures must not repeat")
        selecting = self.steps[:-1]
        if any(item.selected_candidate_ref is None for item in selecting):
            raise ValueError("only the final planning step may terminate")
        final = self.steps[-1]
        if final.termination != PlanTerminationKind.STOP:
            raise ValueError("a successful result terminates with STOP")
        if len(selecting) != len(self.plan.moves):
            raise ValueError("each selection step produces exactly one plan move")
        effects_by_id = {item.effect_id: item for item in self.planned_effects}
        _unique(tuple(item.effect_id for item in self.planned_effects), "planned effects")
        for step, move in zip(selecting, self.plan.moves):
            if move.candidate_ref != step.selected_candidate_ref or move.step_index != step.step_index:
                raise ValueError("plan move differs from its selection step")
            candidate = next(item for item in step.constructed_candidates if item.candidate_id == move.candidate_ref)
            gate = next(item for item in step.gate_results if item.candidate_ref == move.candidate_ref)
            from ..action_selection_v0.registry import DEFAULT_REGISTRY
            definition = DEFAULT_REGISTRY.get(candidate.definition_ref)
            if definition is None:
                raise ValueError("plan move cites an unregistered definition")
            if move.selection_policy != ("model_policy" if model_policy else "argmax"):
                raise ValueError("plan move selection policy differs from the installed planner")
            if (candidate.definition_ref, definition.family, candidate.target_refs, candidate.parameters,
                    candidate.supporting_evidence_refs, gate.realization_constraints) != (
                move.definition_ref, move.family, move.target_refs, move.parameters,
                move.supporting_evidence_refs, move.realization_constraints,
            ):
                raise ValueError("plan move metadata differs from its selected candidate, definition, or gate")
            scored = next(item for item in step.scored_candidates if item.candidate_ref == move.candidate_ref)
            if scored.score.score != move.selection_score:
                raise ValueError("plan move score differs from its scoring step")
            if tuple(item.effect_id for item in step.applied_effects) != move.planned_effect_refs:
                raise ValueError("move effects differ from the applied effects")
            for effect_ref in move.planned_effect_refs:
                effect = effects_by_id.get(effect_ref)
                if effect is None or effect.move_ref != move.move_id:
                    raise ValueError("planned effect lineage does not resolve")
        designation = self.plan.primary_designation
        if designation is not None:
            compared = [
                primary_comparison(step, move.move_id, designation.focal_primary_goal_ref)
                for step, move in zip(selecting, self.plan.moves)
            ]
            if [dict(item) for item in designation.compared_moves] != compared:
                raise ValueError("primary designation compared fields differ from the recorded steps")
            if model_policy:
                best = max(decimal_from(item["direct_focal_coverage"]) for item in compared)
                chosen = next((item for item in compared if item["move_ref"] == designation.primary_move_ref), None)
                if (designation.rule_id != MODEL_PRIMARY_RULE_ID or chosen is None
                        or decimal_from(chosen["direct_focal_coverage"]) != best):
                    raise ValueError("the model primary move lacks the plan's greatest direct focal coverage")
            elif designation.primary_move_ref != designate_primary(compared) or designation.rule_id != PRIMARY_RULE_ID:
                raise ValueError("the primary move is not the one the designation rule derives")
        if self.plan.final_partial_state_ref != final.partial_state_after.state_signature:
            raise ValueError("plan cites a different final partial state")
        if tuple(effects_by_id) != tuple(ref for move in self.plan.moves for ref in move.planned_effect_refs):
            raise ValueError("planned effects must be the effects of the plan moves in order")
        return self


__all__ = [name for name in globals() if not name.startswith("_") and name not in {
    "annotations", "math", "re", "Mapping", "Any", "Literal", "Field", "model_validator", "model_serializer",
    "SerializerFunctionWrapHandler",
    "ROUND_HALF_EVEN", "Decimal", "InvalidOperation", "StrEnum",
}]
