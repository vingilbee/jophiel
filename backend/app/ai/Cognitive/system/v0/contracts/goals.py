"""Canonical immutable Goal V0 contracts.

This module deliberately contains contracts only.  The goal engine and its
registries live in :mod:`goal_v0` so downstream stages can depend on a single,
stable projection without importing orchestration code.
"""
from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .interpretation import FrozenModel, HypothesisDisposition, HypothesisSupport, deep_freeze

# v0.2: typed turn rollover (stale turn-scoped goals expire when a successor
# response turn begins) and exact-record focus provenance with an explicitly
# typed coordination floor.  v0.1 snapshots remain loadable for audit and
# replay reporting; they are never continued as live lineage.
# v0.3: a reject transition carries a structured ``rejection_detail`` naming
# the exact adoption-policy requirement that failed (omitted when absent, so
# v0.2 payloads keep their exact serialization).
GOAL_CONTRACT_SCHEMA_VERSION = "goal-contract-v0.3-draft"
# v0.3: adds ``interaction.fulfill_conversational_deliverable`` and
# ``interaction.incorporate_inquiry_answer`` (Collaborative Content V0).
GOAL_ONTOLOGY_VERSION = "goal-ontology-v0.3-draft"
# v0.3: a failed or unavailable outcome never satisfies a requirement, and
# evidence is reconciled in a canonical order independent of input order.
GOAL_LIFECYCLE_POLICY_VERSION = "goal-lifecycle-v0.3-draft"
# v0.2: a goal forms from, and is adopted against, the frames and hypotheses
# of its own act, so compound contributions do not contaminate each other.
# v0.3: formation is the closed manifest in ``goal_v0.formation_policy``: one
# candidate per eligible act-local frame, and an epistemic subgoal only for
# the exact blocking argument of its parent.
# v0.4: an explicit action request without a trusted task binding forms the
# non-committing request handler instead of no goal at all, so clear intent is
# never erased by missing feasibility evidence.
# v0.5: a collaborative-content frame forms the conversational-deliverable
# goal (never the external-request handler), and an inquiry-answer frame forms
# the answer-incorporation goal.
GOAL_FORMATION_POLICY_VERSION = "goal-formation-v0.5-draft"
# v0.2: focus precedence (SH-EVAL-014): a blocking prerequisite of the current
# request, then the current request, then this turn's response obligation,
# then compatible history, then standing social maintenance.
# v0.3: the request handler ranks with the other current explicit requests.
# v0.4: both new current-turn request goals rank with the other explicit
# requests; open inquiries are typed workspace records, never goals, so they
# can never capture focus.
GOAL_PRIORITY_POLICY_VERSION = "goal-priority-v0.4-draft"
# v0.2: each satisfaction policy accepts only the evidence classes of the
# reviewed authority table in ``goal_v0.evidence_producer``.
# v0.3: registers ``goal-satisfaction.action-request-handled.v1``.
# v0.4: registers the conversational-deliverable and inquiry-answer policies.
GOAL_SATISFACTION_POLICY_VERSION = "goal-satisfaction-v0.4-draft"
# The closed, definition-specific adoption manifest (``goal_v0.adoption_policy``).
# v0.2: the request handler's non-committing policy (moderate act support, no
# authorization or safety gate: those are readiness, resolved downstream).
# v0.3: the non-committing deliverable and answer-incorporation policies.
GOAL_ADOPTION_POLICY_VERSION = "goal-adoption-policy-v0.3-draft"
# Typed outcome events -> GoalEvidence (``goal_v0.evidence_producer``).
# v0.2: the request handler is satisfied by a validated delivered response.
# v0.3: a delivered-artifact event for the exact requested operation is the
# only satisfying evidence of a conversational deliverable.
GOAL_EVIDENCE_POLICY_VERSION = "goal-evidence-policy-v0.3-draft"
GOAL_ENGINE_VERSION = "goal-engine-v0.5"
LEGACY_GOAL_CONTRACT_SCHEMA_VERSIONS = frozenset({"goal-contract-v0.1-draft"})
RESPONSE_OBLIGATION_DEFINITION_KEY = "interaction.discharge_response_obligation"
TURN_ROLLOVER_PROOF_KINDS = ("successor_response_turn",)


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value


def _unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _json(value: Any) -> None:
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
            _json(item)
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _json(item)
        return
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


class GoalOwner(StrEnum):
    USER = "user"; COLUMBINA = "columbina"; SHARED = "shared"; SYSTEM = "system"


class GoalDomain(StrEnum):
    TASK = "task"; EPISTEMIC = "epistemic"; INTERACTION = "interaction"


class GoalMode(StrEnum):
    ACHIEVE = "achieve"; MAINTAIN = "maintain"; AVOID = "avoid"


class GoalScope(StrEnum):
    TURN = "turn"; CONVERSATION = "conversation"; TASK = "task"


class GoalPriorityBand(StrEnum):
    LOW = "low"; MODERATE = "moderate"; HIGH = "high"


class GoalStatus(StrEnum):
    CANDIDATE = "candidate"; ACTIVE = "active"; SUSPENDED = "suspended"
    SATISFIED = "satisfied"; FAILED = "failed"; ABANDONED = "abandoned"
    SUPERSEDED = "superseded"; REJECTED = "rejected"; EXPIRED = "expired"


class GoalTransitionKind(StrEnum):
    PROPOSE = "propose"; ADOPT = "adopt"; REJECT = "reject"; EXPIRE = "expire"
    SUPERSEDE = "supersede"; SUSPEND = "suspend"; RESUME = "resume"
    SATISFY = "satisfy"; FAIL = "fail"; ABANDON = "abandon"


class GoalRequirementStatus(StrEnum):
    OPEN = "open"; SATISFIED = "satisfied"; IMPOSSIBLE = "impossible"; DISPUTED = "disputed"


class GoalEvidenceConflictStatus(StrEnum):
    ACTIVE = "active"; RESOLVED = "resolved"


class GoalConflictClassification(StrEnum):
    COMPATIBLE = "compatible"; RESOURCE_COMPETITION = "resource_competition"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"; UNKNOWN = "unknown"


class GoalFocusRoute(StrEnum):
    CONTINUE_FAST = "continue_fast"; CLARIFY = "clarify"; SLOW = "slow"


class GoalUpdateTrigger(StrEnum):
    RESPONSE_TURN = "response_turn"; REALIZATION_OR_OUTCOME = "realization_or_outcome"
    EXPLICIT_CORRECTION = "explicit_correction"; TRUSTED_SCOPE_CLOSURE = "trusted_scope_closure"
    TRUSTED_RUNTIME_CHANGE = "trusted_runtime_change"


class GoalDefinitionRef(FrozenModel):
    definition_key: str = Field(min_length=1)
    definition_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        _nonblank(self.definition_key, "definition key"); _nonblank(self.definition_version, "definition version")
        return self


class GoalTargetRef(FrozenModel):
    role: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class GoalRequirementTemplate(FrozenModel):
    requirement_key: str = Field(min_length=1)
    required_target_roles: tuple[str, ...] = ()
    allowed_parameter_keys: tuple[str, ...] = ()
    satisfaction_policy_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        _unique(self.required_target_roles, "required target roles"); _unique(self.allowed_parameter_keys, "allowed parameter keys")
        for value in (*self.required_target_roles, *self.allowed_parameter_keys, self.satisfaction_policy_key): _nonblank(value, "template value")
        return self


class GoalDefinition(FrozenModel):
    definition_key: str = Field(min_length=1)
    definition_version: str = Field(min_length=1)
    lifecycle: str = Field(min_length=1)
    domain: GoalDomain
    mode: GoalMode
    allowed_owners: tuple[GoalOwner, ...] = Field(min_length=1)
    allowed_scopes: tuple[GoalScope, ...] = Field(min_length=1)
    default_priority_band: GoalPriorityBand
    requirement_templates: tuple[GoalRequirementTemplate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        _nonblank(self.definition_key, "definition key"); _nonblank(self.definition_version, "definition version")
        keys = tuple(item.requirement_key for item in self.requirement_templates)
        _unique(keys, "requirement keys")
        return self


class GoalRequirement(FrozenModel):
    requirement_id: str = Field(min_length=1)
    requirement_key: str = Field(min_length=1)
    target_refs: tuple[GoalTargetRef, ...] = ()
    parameters: Mapping[str, Any] = Field(default_factory=dict)
    satisfaction_policy_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        _unique(tuple(item.role for item in self.target_refs), "target roles")
        _nonblank(self.requirement_id, "requirement id"); _nonblank(self.requirement_key, "requirement key")
        _nonblank(self.satisfaction_policy_key, "satisfaction policy")
        _json(self.parameters)
        return self


class GoalAdoptionBasis(FrozenModel):
    rule_id: str = Field(min_length=1)


class GoalRecord(FrozenModel):
    goal_id: str = Field(min_length=1)
    definition_ref: GoalDefinitionRef
    owner: GoalOwner
    domain: GoalDomain
    mode: GoalMode
    scope: GoalScope
    scope_ref: str = Field(min_length=1)
    priority_band: GoalPriorityBand
    status: GoalStatus
    requirements: tuple[GoalRequirement, ...] = ()
    adoption_basis: GoalAdoptionBasis
    created_turn_ref: str = Field(min_length=1)
    last_transition_ref: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        from ..goal_v0.rules import ADOPTION_RULE_IDS, validate_rule_id
        validate_rule_id(self.adoption_basis.rule_id)
        if self.adoption_basis.rule_id not in ADOPTION_RULE_IDS:
            raise ValueError("goal adoption basis must use a registered adoption rule")
        _unique(tuple(item.requirement_id for item in self.requirements), "requirement ids")
        _unique(self.source_refs, "source refs")
        return self


class GoalProposal(FrozenModel):
    proposal_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    definition_ref: GoalDefinitionRef
    rule_id: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        from ..goal_v0.rules import PROPOSAL_RULE_IDS, validate_rule_id
        validate_rule_id(self.rule_id)
        if self.rule_id not in PROPOSAL_RULE_IDS:
            raise ValueError("goal proposal must use a proposal rule")
        return self


class GoalRejectionCheck(StrEnum):
    """The adoption-policy check a rejected candidate failed."""

    HYPOTHESIS_MISSING = "hypothesis_missing"
    HYPOTHESIS_DISPOSITION = "hypothesis_disposition"
    HYPOTHESIS_SUPPORT = "hypothesis_support"
    DISQUALIFYING_MODALITY = "disqualifying_modality"
    UNRESOLVED_ARGUMENT = "unresolved_argument"
    AUTHORIZATION = "authorization"
    SAFETY = "safety"
    ACTIVE_CONTRADICTION = "active_contradiction"
    PARENT_NOT_ADOPTED = "parent_not_adopted"
    UNGROUNDED_SOURCE = "ungrounded_source"


class GoalRejectionDetail(FrozenModel):
    """Which exact requirement of which adoption policy rejected a candidate.

    Observed values are copied from the validated Interpretation and trusted
    runtime; nothing here is free text that a later stage interprets.
    """

    policy_key: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    failed_check: GoalRejectionCheck
    hypothesis_key: str | None = None
    hypothesis_ref: str | None = None
    minimum_support: HypothesisSupport | None = None
    observed_support: HypothesisSupport | None = None
    allowed_dispositions: tuple[HypothesisDisposition, ...] = ()
    observed_disposition: HypothesisDisposition | None = None
    argument_name: str | None = None
    runtime_value: str | None = None
    related_refs: tuple[str, ...] = ()
    frame_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        _nonblank(self.policy_key, "rejection policy key"); _nonblank(self.policy_version, "rejection policy version")
        hypothesis_checks = {
            GoalRejectionCheck.HYPOTHESIS_MISSING, GoalRejectionCheck.HYPOTHESIS_DISPOSITION,
            GoalRejectionCheck.HYPOTHESIS_SUPPORT, GoalRejectionCheck.DISQUALIFYING_MODALITY,
        }
        if self.failed_check in hypothesis_checks and not self.hypothesis_key:
            raise ValueError("a hypothesis rejection names its hypothesis key")
        if self.failed_check == GoalRejectionCheck.HYPOTHESIS_SUPPORT and (
            self.minimum_support is None or self.observed_support is None
        ):
            raise ValueError("a support rejection names the minimum and observed support")
        if self.failed_check == GoalRejectionCheck.UNRESOLVED_ARGUMENT and not self.argument_name:
            raise ValueError("an unresolved-argument rejection names the argument")
        _unique(self.frame_refs, "rejection frame refs"); _unique(self.related_refs, "rejection related refs")
        return self


class GoalTransition(FrozenModel):
    transition_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    from_status: GoalStatus | None = None
    to_status: GoalStatus
    transition_kind: GoalTransitionKind
    rule_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    previous_snapshot_ref: str | None = None
    resulting_snapshot_ref: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    outcome_refs: tuple[str, ...] = ()
    replacement_goal_ref: str | None = None
    reason_code: str = Field(min_length=1)
    # Contract v0.3: set only on a reject transition formed under the adoption
    # manifest.  Omitted when absent so earlier payloads serialize unchanged.
    rejection_detail: GoalRejectionDetail | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_detail(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("rejection_detail") is None:
            data.pop("rejection_detail", None)
        return data

    @model_validator(mode="after")
    def valid(self):
        from ..goal_v0.rules import REASON_CODES, validate_rule_id
        validate_rule_id(self.rule_id)
        if self.reason_code not in REASON_CODES:
            raise ValueError("unregistered transition reason code")
        if self.transition_kind == GoalTransitionKind.SUPERSEDE and not self.replacement_goal_ref:
            raise ValueError("supersession requires a replacement goal reference")
        if self.transition_kind != GoalTransitionKind.SUPERSEDE and self.replacement_goal_ref is not None:
            raise ValueError("only supersession may carry a replacement goal reference")
        if self.rejection_detail is not None and self.transition_kind != GoalTransitionKind.REJECT:
            raise ValueError("only a reject transition may carry a rejection detail")
        return self


class GoalRequirementAssessment(FrozenModel):
    assessment_id: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    requirement_ref: str = Field(min_length=1)
    status: GoalRequirementStatus
    policy_key: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    previous_assessment_ref: str | None = None
    rationale: str = ""


class GoalEvidenceConflict(FrozenModel):
    conflict_id: str = Field(min_length=1)
    status: GoalEvidenceConflictStatus
    left_evidence_ref: str = Field(min_length=1)
    right_evidence_ref: str = Field(min_length=1)
    resolution_ref: str | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def valid(self):
        if self.status == GoalEvidenceConflictStatus.ACTIVE and self.resolution_ref is not None:
            raise ValueError("active evidence conflicts cannot have a resolution")
        if self.status == GoalEvidenceConflictStatus.RESOLVED and not self.resolution_ref:
            raise ValueError("resolved evidence conflicts require a resolution")
        return self


class GoalPriorityChange(FrozenModel):
    priority_change_id: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    old_priority_band: GoalPriorityBand
    new_priority_band: GoalPriorityBand
    rule_id: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    rationale: str = ""


class GoalConflict(FrozenModel):
    conflict_id: str = Field(min_length=1)
    left_goal_ref: str = Field(min_length=1)
    right_goal_ref: str = Field(min_length=1)
    classification: GoalConflictClassification
    materiality: str = Field(min_length=1)
    reversibility: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    resolution: str = Field(min_length=1)
    suspended_goal_ref: str | None = None
    route: GoalFocusRoute
    rationale: str = ""

    @model_validator(mode="after")
    def valid(self):
        from ..goal_v0.rules import MATERIALITY, REVERSIBILITY, RESOLUTIONS, validate_rule_id
        validate_rule_id(self.rule_id)
        if self.materiality not in MATERIALITY or self.reversibility not in REVERSIBILITY or self.resolution not in RESOLUTIONS:
            raise ValueError("unregistered conflict audit value")
        return self


class GoalFocusDecision(FrozenModel):
    focus_decision_id: str = Field(min_length=1)
    primary_goal_ref: str = Field(min_length=1)
    supporting_goal_refs: tuple[str, ...] = ()
    non_focal_active_goal_refs: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    route: GoalFocusRoute
    rationale: str = ""
    # The current turn's active response obligation when it is cited as the
    # coordination floor.  Every evidence ref must come from this exact goal or
    # from a focal goal (validated by ``GoalSnapshot``).  Omitted when absent so
    # goal-contract-v0.1 payloads keep their exact serialization.
    coordination_floor_goal_ref: str | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_floor(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("coordination_floor_goal_ref") is None:
            data.pop("coordination_floor_goal_ref", None)
        return data

    @model_validator(mode="after")
    def bounded(self):
        if len(self.supporting_goal_refs) > 2 or self.primary_goal_ref in self.supporting_goal_refs:
            raise ValueError("focus must contain one primary and at most two supporting goals")
        _unique(self.supporting_goal_refs, "supporting goal refs")
        if self.coordination_floor_goal_ref is not None:
            _nonblank(self.coordination_floor_goal_ref, "coordination floor goal ref")
        return self


class GoalTurnRollover(FrozenModel):
    """Server-owned proof that the conversation's prior response episode ended.

    It is produced only from trusted runtime facts (never from conversation
    text): the predecessor goal snapshot of the same conversation and the
    successor turn's trusted response obligation.  It authorizes exactly one
    thing: expiring non-terminal ``scope="turn"`` goals owned by a different
    turn.  Scope closure is not success, so it can never satisfy a goal.
    """

    rollover_id: str = Field(min_length=1)
    proof_kind: Literal["successor_response_turn"]
    producer: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    closed_turn_ref: str = Field(min_length=1)
    closed_goal_snapshot_ref: str = Field(min_length=1)
    closing_turn_ref: str = Field(min_length=1)
    closing_response_obligation_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self):
        for value in (
            self.rollover_id, self.producer, self.conversation_ref, self.closed_turn_ref,
            self.closed_goal_snapshot_ref, self.closing_turn_ref,
            self.closing_response_obligation_ref,
        ):
            _nonblank(value, "turn rollover field")
        if self.closed_turn_ref == self.closing_turn_ref:
            raise ValueError("a turn rollover must close a different turn than it opens")
        return self


def stale_turn_scoped_goals(snapshot: "GoalSnapshot", cognitive_turn_id: str) -> tuple["GoalRecord", ...]:
    """Non-terminal turn-scoped records owned by a turn other than ``cognitive_turn_id``."""

    return tuple(
        record for record in snapshot.records
        if record.scope == GoalScope.TURN
        and record.scope_ref != cognitive_turn_id
        and record.status in {GoalStatus.CANDIDATE, GoalStatus.ACTIVE, GoalStatus.SUSPENDED}
    )


class GoalEvidenceProvenance(FrozenModel):
    """Typed policy inputs attached by trusted evidence producers.

    Free-form diagnostic wording belongs in ``factual_meaning`` and is never
    interpreted as one of these facts.
    """

    producer: str = Field(min_length=1)
    review_state: str = Field(min_length=1)
    outcome_status: Literal["succeeded", "failed", "unavailable"] | None = None
    alternate_route_available: bool | None = None
    monitoring_adequate: bool | None = None
    non_satisfying_observation: bool = False
    contradicts_evidence_ref: str | None = None
    withdraws_goal_ref: str | None = None
    replacement_goal_ref: str | None = None
    suspend_goal_ref: str | None = None
    suspend_rule_id: str | None = None
    resume_goal_ref: str | None = None
    verified_impossible: bool = False

    @model_validator(mode="after")
    def valid(self):
        for name in (
            "contradicts_evidence_ref", "withdraws_goal_ref",
            "replacement_goal_ref", "suspend_goal_ref", "suspend_rule_id",
            "resume_goal_ref",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonblank(value, name)
        if self.suspend_rule_id is not None and self.suspend_goal_ref is None:
            raise ValueError("suspend rule requires a suspended goal reference")
        if self.alternate_route_available is not None and self.outcome_status != "failed":
            raise ValueError("alternate-route evidence requires a failed outcome")
        return self

class GoalEvidence(FrozenModel):
    evidence_id: str = Field(min_length=1)
    evidence_kind: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    episode_or_turn_ref: str = Field(min_length=1)
    target_goal_ref: str = Field(min_length=1)
    target_requirement_ref: str = Field(min_length=1)
    provenance: GoalEvidenceProvenance
    finality: str = Field(min_length=1)
    factual_meaning: str = ""
    outcome_id: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.evidence_kind not in {"realization_validation", "semantic_grounding", "explicit_user_confirmation", "finalized_tool_result", "trusted_runtime_fact", "reviewed_outcome"}:
            raise ValueError("unsupported goal evidence kind")
        if self.finality not in {"final", "non_final"}:
            raise ValueError("goal evidence finality must be final or non_final")
        if self.evidence_kind in {"finalized_tool_result", "reviewed_outcome", "realization_validation"} and not self.outcome_id:
            raise ValueError("outcome evidence requires an outcome id")
        if self.provenance.withdraws_goal_ref is not None and self.provenance.withdraws_goal_ref != self.target_goal_ref:
            raise ValueError("withdrawal provenance must target its evidence goal")
        if self.provenance.suspend_goal_ref is not None and self.provenance.suspend_goal_ref != self.target_goal_ref:
            raise ValueError("suspension provenance must target its evidence goal")
        if self.provenance.resume_goal_ref is not None and self.provenance.resume_goal_ref != self.target_goal_ref:
            raise ValueError("resumption provenance must target its evidence goal")
        if self.provenance.contradicts_evidence_ref == self.evidence_id:
            raise ValueError("evidence cannot contradict itself")
        return self


class GoalVersions(FrozenModel):
    goal_contract_schema: str = GOAL_CONTRACT_SCHEMA_VERSION
    goal_ontology: str = GOAL_ONTOLOGY_VERSION
    goal_lifecycle_policy: str = GOAL_LIFECYCLE_POLICY_VERSION
    goal_formation_policy: str = GOAL_FORMATION_POLICY_VERSION
    goal_priority_policy: str = GOAL_PRIORITY_POLICY_VERSION
    goal_satisfaction_policy: str = GOAL_SATISFACTION_POLICY_VERSION
    goal_engine: str = GOAL_ENGINE_VERSION
    # Added with contract v0.3.  A payload that predates them loads with the
    # current value; its other (older) version strings identify it as history.
    goal_adoption_policy: str = GOAL_ADOPTION_POLICY_VERSION
    goal_evidence_policy: str = GOAL_EVIDENCE_POLICY_VERSION


class GoalSnapshot(FrozenModel):
    schema_version: int = 1
    goal_snapshot_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    supersedes_goal_snapshot_id: str | None = None
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    runtime_snapshot_ref: str = Field(min_length=1)
    interpretation_trace_ref: str = Field(min_length=1)
    previous_dynamic_state_ref: str = Field(min_length=1)
    input_outcome_refs: tuple[str, ...] = ()
    records: tuple[GoalRecord, ...] = ()
    requirement_assessments: tuple[GoalRequirementAssessment, ...] = ()
    evidence_conflicts: tuple[GoalEvidenceConflict, ...] = ()
    transitions: tuple[GoalTransition, ...] = ()
    priority_changes: tuple[GoalPriorityChange, ...] = ()
    conflicts: tuple[GoalConflict, ...] = ()
    focus_decision: GoalFocusDecision
    active_goal_refs: tuple[str, ...] = ()
    focal_goal_refs: tuple[str, ...] = ()
    versions: GoalVersions = Field(default_factory=GoalVersions)

    @model_validator(mode="after")
    def valid(self):
        from ..goal_v0.rules import validate_rule_id
        from ..goal_v0.ontology import definition, validate_requirement
        from ..goal_v0.lifecycle import next_status
        records = {item.goal_id: item for item in self.records}
        if len(records) != len(self.records): raise ValueError("goal ids must be unique")
        assessments = {item.assessment_id for item in self.requirement_assessments}
        if len(assessments) != len(self.requirement_assessments): raise ValueError("assessment ids must be unique")
        requirement_keys = {(record.goal_id, req.requirement_id) for record in self.records if record.status != GoalStatus.REJECTED for req in record.requirements}
        assessment_keys = {(item.goal_ref, item.requirement_ref) for item in self.requirement_assessments if records.get(item.goal_ref) is not None and records[item.goal_ref].status != GoalStatus.REJECTED}
        if requirement_keys != assessment_keys: raise ValueError("snapshot must contain one current assessment per requirement")
        for record in self.records:
            registered = definition(record.definition_ref)
            if record.domain != registered.domain or record.mode != registered.mode:
                raise ValueError("goal record domain/mode differs from its registered definition")
            if record.owner not in registered.allowed_owners or record.scope not in registered.allowed_scopes:
                raise ValueError("goal record owner/scope is not allowed by its definition")
            for requirement in record.requirements:
                validate_requirement(registered, requirement)
        requirements_by_ref = {
            (record.goal_id, requirement.requirement_id): requirement
            for record in self.records for requirement in record.requirements
        }
        transition_ids = tuple(item.transition_id for item in self.transitions)
        if len(transition_ids) != len(set(transition_ids)): raise ValueError("transition ids must be unique")
        sequences = tuple(item.sequence for item in self.transitions)
        if sequences and (sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences))):
            raise ValueError("transition history must be strictly ordered")
        for transition in self.transitions:
            from ..goal_v0.rules import (
                ADOPTION_RULE_IDS, LIFECYCLE_RULE_IDS, PROPOSAL_RULE_IDS,
                REJECTION_RULE_IDS,
            )
            validate_rule_id(transition.rule_id)
            allowed_rules = {
                GoalTransitionKind.PROPOSE: PROPOSAL_RULE_IDS,
                GoalTransitionKind.ADOPT: ADOPTION_RULE_IDS,
                GoalTransitionKind.REJECT: REJECTION_RULE_IDS,
                GoalTransitionKind.SATISFY: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.SUSPEND: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.RESUME: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.ABANDON: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.FAIL: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.SUPERSEDE: LIFECYCLE_RULE_IDS,
                GoalTransitionKind.EXPIRE: LIFECYCLE_RULE_IDS,
            }[transition.transition_kind]
            if transition.rule_id not in allowed_rules:
                raise ValueError("transition rule is incompatible with its transition kind")
            if not transition.goal_id or transition.goal_id not in records:
                raise ValueError("transition references an unknown goal")
            if not transition.cognitive_turn_id or not transition.resulting_snapshot_ref:
                raise ValueError("transition ownership references are required")
            if transition.resulting_snapshot_ref == self.goal_snapshot_id and transition.cognitive_turn_id != self.cognitive_turn_id:
                raise ValueError("current-snapshot transition belongs to another turn")
            if transition.resulting_snapshot_ref == self.goal_snapshot_id and transition.previous_snapshot_ref != self.supersedes_goal_snapshot_id:
                raise ValueError("current transition predecessor differs from snapshot lineage")
        # Replay each goal's ordered lifecycle.  Historical transitions are
        # retained verbatim, so replay is the only safe way to validate the
        # current status and last-transition pointer.
        by_goal: dict[str, list[GoalTransition]] = {}
        for transition in self.transitions:
            by_goal.setdefault(transition.goal_id, []).append(transition)
        for goal_id, record in records.items():
            history = by_goal.get(goal_id, [])
            if not history:
                raise ValueError("goal record has no reconstructable transition history")
            status = None
            for transition in history:
                if transition.from_status != status:
                    raise ValueError("transition history predecessor status is inconsistent")
                status = next_status(status, transition.transition_kind)
                if status != transition.to_status:
                    raise ValueError("transition history result is inconsistent")
            if status != record.status:
                raise ValueError("record status cannot be reconstructed from transitions")
            if record.last_transition_ref != history[-1].transition_id:
                raise ValueError("record last_transition_ref is not the latest transition")
        for change in self.priority_changes:
            validate_rule_id(change.rule_id)
            if change.goal_ref not in records:
                raise ValueError("priority change references an unknown goal")
        for conflict in self.conflicts:
            validate_rule_id(conflict.rule_id)
            if conflict.left_goal_ref not in records or conflict.right_goal_ref not in records:
                raise ValueError("goal conflict references an unknown goal")
            if conflict.suspended_goal_ref is not None and conflict.suspended_goal_ref not in records:
                raise ValueError("goal conflict suspends an unknown goal")
        for rule_id in self.focus_decision.rule_ids:
            validate_rule_id(rule_id)
        for assessment in self.requirement_assessments:
            if assessment.goal_ref not in records or (records[assessment.goal_ref].status != GoalStatus.REJECTED and (assessment.goal_ref, assessment.requirement_ref) not in requirement_keys):
                raise ValueError("assessment references an unknown requirement")
            requirement = requirements_by_ref.get((assessment.goal_ref, assessment.requirement_ref))
            if requirement is not None and assessment.policy_key != requirement.satisfaction_policy_key:
                raise ValueError("assessment policy differs from its requirement")
        evidence_conflict_ids = tuple(item.conflict_id for item in self.evidence_conflicts)
        if len(evidence_conflict_ids) != len(set(evidence_conflict_ids)):
            raise ValueError("evidence conflict ids must be unique")
        for assessment in self.requirement_assessments:
            if not set(assessment.conflict_refs) <= set(evidence_conflict_ids):
                raise ValueError("assessment references an unknown evidence conflict")
            if assessment.status == GoalRequirementStatus.DISPUTED and not assessment.conflict_refs:
                raise ValueError("disputed assessments require an evidence conflict")
        for record in self.records:
            statuses = {item.status for item in self.requirement_assessments if item.goal_ref == record.goal_id}
            if record.status == GoalStatus.SATISFIED and statuses != {GoalRequirementStatus.SATISFIED}:
                raise ValueError("satisfied goals require all requirements satisfied")
            if record.status == GoalStatus.FAILED and GoalRequirementStatus.IMPOSSIBLE not in statuses:
                raise ValueError("failed goals require an impossible requirement")
        active = tuple(item.goal_id for item in self.records if item.status == GoalStatus.ACTIVE)
        if tuple(self.active_goal_refs) != active: raise ValueError("active goal refs do not match records")
        if len(self.focal_goal_refs) != len(set(self.focal_goal_refs)) or not set(self.focal_goal_refs) <= set(active):
            raise ValueError("focal refs must be unique active goals")
        expected = (self.focus_decision.primary_goal_ref, *self.focus_decision.supporting_goal_refs)
        if tuple(self.focal_goal_refs) != expected: raise ValueError("focus projection differs from focal refs")
        if tuple(self.focus_decision.non_focal_active_goal_refs) != tuple(x for x in active if x not in self.focal_goal_refs):
            raise ValueError("non-focal refs differ from active refs")
        self._validate_focus_provenance(records)
        if self.revision == 1 and self.supersedes_goal_snapshot_id is not None: raise ValueError("revision one cannot supersede")
        if self.revision > 1 and not self.supersedes_goal_snapshot_id: raise ValueError("later revisions require a predecessor")
        return self

    def _validate_focus_provenance(self, records: Mapping[str, GoalRecord]) -> None:
        """Every focus evidence ref must come from a focal or coordination-floor goal.

        goal-contract-v0.1 snapshots predate this ownership rule (their evidence
        could cite a historical obligation by definition-only lookup).  They are
        accepted unchanged for audit and replay reporting; they cannot declare a
        coordination floor, a field that did not exist in that contract.
        """

        focus = self.focus_decision
        floor_ref = focus.coordination_floor_goal_ref
        if self.versions.goal_contract_schema in LEGACY_GOAL_CONTRACT_SCHEMA_VERSIONS:
            if floor_ref is not None:
                raise ValueError("a legacy goal snapshot cannot declare a coordination floor")
            return
        owners = [records[ref] for ref in self.focal_goal_refs]
        if floor_ref is not None:
            floor = records.get(floor_ref)
            if floor is None:
                raise ValueError("coordination floor references an unknown goal")
            if not (
                floor.status == GoalStatus.ACTIVE
                and floor.scope == GoalScope.TURN
                and floor.scope_ref == self.cognitive_turn_id
                and floor.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY
            ):
                raise ValueError("coordination floor must be this turn's active response obligation")
            owners.append(floor)
        current_obligations = tuple(
            record.goal_id for record in self.records
            if record.status == GoalStatus.ACTIVE
            and record.scope == GoalScope.TURN
            and record.scope_ref == self.cognitive_turn_id
            and record.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY
        )
        if len(current_obligations) > 1:
            raise ValueError("a snapshot cannot hold several active current-turn response obligations")
        owned = {ref for owner in owners for ref in owner.source_refs}
        if not set(focus.evidence_refs) <= owned:
            raise ValueError("focus evidence must come from a focal or coordination-floor goal")


class ActiveGoalView(FrozenModel):
    goal_id: str = Field(min_length=1)
    definition_ref: GoalDefinitionRef
    owner: GoalOwner
    domain: GoalDomain
    mode: GoalMode
    scope: GoalScope
    scope_ref: str = Field(min_length=1)
    priority_band: GoalPriorityBand
    requirements: tuple[GoalRequirement, ...] = ()
    source_refs: tuple[str, ...] = ()

    @property
    def status(self) -> GoalStatus:
        return GoalStatus.ACTIVE


def project_active_goal(record: GoalRecord) -> ActiveGoalView:
    if record.status != GoalStatus.ACTIVE:
        raise ValueError("only active goal records can be projected")
    return ActiveGoalView(
        goal_id=record.goal_id, definition_ref=record.definition_ref, owner=record.owner,
        domain=record.domain, mode=record.mode, scope=record.scope, scope_ref=record.scope_ref,
        priority_band=record.priority_band, requirements=record.requirements,
        source_refs=record.source_refs,
    )


__all__ = [name for name in globals() if name.startswith("Goal") or name in {
    "ActiveGoalView", "project_active_goal", "stale_turn_scoped_goals",
    "LEGACY_GOAL_CONTRACT_SCHEMA_VERSIONS", "RESPONSE_OBLIGATION_DEFINITION_KEY",
    "TURN_ROLLOVER_PROOF_KINDS",
}]
