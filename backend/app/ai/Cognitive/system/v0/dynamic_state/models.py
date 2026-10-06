"""Immutable typed contracts for the deterministic dynamic-state updater."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..appraisal.models import (
    AppraisalAggregate,
    AppraisalDimension,
    AppraisalFrame,
    SemanticFrame,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrozenDict(dict):
    """Serializable dictionary that rejects mutation after construction."""

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("frozen mapping cannot be mutated")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class StateKey(StrEnum):
    VALENCE = "state.valence"
    ACTIVATION = "state.activation"
    EPISTEMIC_CONFIDENCE = "state.epistemic_confidence"
    CURIOSITY = "state.curiosity"
    ENGAGEMENT = "state.engagement"
    GOAL_PRESSURE = "state.goal_pressure"
    SOCIAL_ALIGNMENT = "state.social_alignment"
    INTERACTION_STRAIN = "state.interaction_strain"


SIGNED_KEYS = frozenset({StateKey.VALENCE, StateKey.SOCIAL_ALIGNMENT})


class ScopeType(StrEnum):
    CONVERSATION = "conversation"
    PARTICIPANT = "participant"
    GOAL = "goal"
    TOPIC = "topic"
    PROPOSITION = "proposition"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"
    APPRAISAL_FRAME = "appraisal_frame"
    ACTION_CANDIDATE = "action_candidate"
    ACTION_PLAN = "action_plan"
    OUTCOME_EPISODE = "outcome_episode"


ALLOWED_SCOPES = {
    StateKey.VALENCE: frozenset({ScopeType.CONVERSATION}),
    StateKey.ACTIVATION: frozenset({ScopeType.CONVERSATION}),
    StateKey.EPISTEMIC_CONFIDENCE: frozenset(
        {
            ScopeType.PROPOSITION,
            ScopeType.HYPOTHESIS,
            ScopeType.APPRAISAL_FRAME,
            ScopeType.ACTION_CANDIDATE,
            ScopeType.ACTION_PLAN,
        }
    ),
    StateKey.CURIOSITY: frozenset(
        {ScopeType.UNKNOWN, ScopeType.TOPIC, ScopeType.HYPOTHESIS, ScopeType.PROPOSITION}
    ),
    StateKey.ENGAGEMENT: frozenset({ScopeType.CONVERSATION, ScopeType.GOAL}),
    StateKey.GOAL_PRESSURE: frozenset({ScopeType.GOAL}),
    StateKey.SOCIAL_ALIGNMENT: frozenset({ScopeType.CONVERSATION, ScopeType.PARTICIPANT}),
    StateKey.INTERACTION_STRAIN: frozenset({ScopeType.CONVERSATION, ScopeType.GOAL}),
}


class ValueStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    PROVISIONAL = "provisional"
    NOT_APPLICABLE = "not_applicable"


class Direction(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    UNCHANGED = "unchanged"
    NO_CONTRIBUTION = "no_contribution"
    BRANCH_DEPENDENT = "branch_dependent"


class StepBand(StrEnum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class SourceKind(StrEnum):
    APPRAISAL = "appraisal"
    OUTCOME = "outcome"
    ELAPSED_TIME = "elapsed_time"


class QualitativeBand(StrEnum):
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    STRONG_NEGATIVE = "strong_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    STRONG_POSITIVE = "strong_positive"


class StateValue(FrozenModel):
    state_key: StateKey
    scope_type: ScopeType
    scope_ref: str = Field(min_length=1)
    status: ValueStatus = ValueStatus.KNOWN
    score: int | None = None
    band: QualitativeBand | None = None
    baseline_score: int
    evidence_refs: tuple[str, ...] = ()
    rule_contribution_refs: tuple[str, ...] = ()
    missing_input_refs: tuple[str, ...] = ()
    branch_refs: tuple[str, ...] = ()
    possible_score_range: tuple[int, int] | None = None
    active: bool = True
    learnable: bool = True

    @model_validator(mode="after")
    def validate_score_and_status(self) -> StateValue:
        if self.scope_type not in ALLOWED_SCOPES[self.state_key]:
            raise ValueError(f"{self.scope_type} is not a valid scope for {self.state_key}")
        low, high = (-100, 100) if self.state_key in SIGNED_KEYS else (0, 100)
        for value in (self.score, self.baseline_score):
            if value is not None and not low <= value <= high:
                raise ValueError(f"{self.state_key} score must be in [{low}, {high}]")
        if self.status == ValueStatus.KNOWN and (self.score is None or self.band is None):
            raise ValueError("known state values require score and band")
        if self.status == ValueStatus.KNOWN:
            signed_bands = {
                QualitativeBand.STRONG_NEGATIVE,
                QualitativeBand.NEGATIVE,
                QualitativeBand.NEUTRAL,
                QualitativeBand.POSITIVE,
                QualitativeBand.STRONG_POSITIVE,
            }
            if (self.state_key in SIGNED_KEYS) != (self.band in signed_bands):
                raise ValueError(f"band {self.band} has the wrong domain for {self.state_key}")
            intervals = {
                QualitativeBand.VERY_LOW: (0, 14),
                QualitativeBand.LOW: (15, 29),
                QualitativeBand.MODERATE: (30, 59),
                QualitativeBand.HIGH: (60, 89),
                QualitativeBand.VERY_HIGH: (90, 100),
                QualitativeBand.STRONG_NEGATIVE: (-100, -50),
                QualitativeBand.NEGATIVE: (-49, -10),
                QualitativeBand.NEUTRAL: (-9, 9),
                QualitativeBand.POSITIVE: (10, 49),
                QualitativeBand.STRONG_POSITIVE: (50, 100),
            }
            band_low, band_high = intervals[self.band]
            assert self.score is not None
            if not band_low <= self.score <= band_high:
                raise ValueError(
                    f"score {self.score} contradicts {self.state_key} band {self.band}"
                )
        if self.status in {ValueStatus.UNKNOWN, ValueStatus.NOT_APPLICABLE} and self.score is not None:
            raise ValueError(f"{self.status} state values cannot have a score")
        if self.status == ValueStatus.PROVISIONAL:
            if self.score is not None or self.possible_score_range is None or len(self.branch_refs) < 2:
                raise ValueError("provisional values require a range and at least two branches")
            range_low, range_high = self.possible_score_range
            if range_low > range_high or range_low < low or range_high > high:
                raise ValueError(f"provisional range must be ordered within [{low}, {high}]")
        return self


class AttentionItem(FrozenModel):
    target_type: ScopeType
    target_ref: str = Field(min_length=1)
    weight: int = Field(ge=0, le=100)
    weight_band: str
    reason_refs: tuple[str, ...] = ()


class DerivedControls(FrozenModel):
    slow_cognition_pressure: QualitativeBand
    initiative_readiness: QualitativeBand
    clarification_pressure: QualitativeBand
    repair_pressure: QualitativeBand
    exploration_readiness: QualitativeBand
    response_energy: QualitativeBand


class StateSnapshot(FrozenModel):
    state_snapshot_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    revision: int = Field(ge=1)
    supersedes_snapshot_id: str | None = None
    status: str = "current"
    policy_version: str
    personality_mapper_version: str
    source_appraisal_revision_ids: tuple[str, ...] = ()
    source_outcome_ids: tuple[str, ...] = ()
    values: tuple[StateValue, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    derived_controls: DerivedControls | None = None

    @model_validator(mode="after")
    def unique_scopes(self) -> StateSnapshot:
        identities = [(value.state_key, value.scope_type, value.scope_ref) for value in self.values]
        if len(identities) != len(set(identities)):
            raise ValueError("snapshot contains duplicate state key/scope values")
        if any(
            value.scope_type == ScopeType.CONVERSATION
            and value.scope_ref != self.conversation_ref
            for value in self.values
        ):
            raise ValueError("conversation-scoped state belongs to a different conversation")
        return self


class PersonalityControl(FrozenModel):
    baseline_score: int
    persistence_band: str = "moderate"
    recovery_band: str = "moderate"
    sensitivity: str = "neutral"
    maximum_step_band: str = "moderate"


class PersonalityProfile(FrozenModel):
    profile_id: str
    mapper_version: str
    interaction_patience_band: str = "moderate"
    state_controls: Mapping[StateKey, PersonalityControl] = Field(default_factory=dict)

    @model_validator(mode="after")
    def freeze_controls(self) -> PersonalityProfile:
        if not isinstance(self.state_controls, FrozenDict):
            object.__setattr__(self, "state_controls", FrozenDict(self.state_controls))
        return self


class GroundedObject(FrozenModel):
    object_type: ScopeType
    object_ref: str
    status: str = "active"
    recoverability: str | None = None
    priority_band: str | None = None
    gate_status: str | None = None
    evidence_refs: tuple[str, ...] = ()


class OutcomeObservation(FrozenModel):
    outcome_id: str
    dimension: str
    assessment_status: str
    meaning: str
    evidence_refs: tuple[str, ...] = ()
    provenance: str


class DynamicStateContext(FrozenModel):
    conversation_ref: str
    semantic_frames: tuple[SemanticFrame, ...] = ()
    objects: tuple[GroundedObject, ...] = ()
    aggregate: AppraisalAggregate = Field(default_factory=AppraisalAggregate)

    def objects_of_type(self, object_type: ScopeType) -> tuple[GroundedObject, ...]:
        return tuple(item for item in self.objects if item.object_type == object_type)


class TransitionEvidence(FrozenModel):
    evidence_id: str
    rule_id: str
    source_kind: SourceKind
    state_key: StateKey
    scope_type: ScopeType
    scope_ref: str
    direction: Direction
    base_step_band: StepBand
    source_appraisal_frame_refs: tuple[str, ...] = ()
    source_outcome_refs: tuple[str, ...] = ()
    source_dimension_keys: tuple[str, ...] = ()
    goal_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    inhibition_refs: tuple[str, ...] = ()
    branch_refs: tuple[str, ...] = ()
    rationale: str = ""
    decay_trace: bool = False
    resolves_branches: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> TransitionEvidence:
        if self.scope_type not in ALLOWED_SCOPES[self.state_key]:
            raise ValueError(f"{self.scope_type} is not a valid scope for {self.state_key}")
        return self


class Contribution(FrozenModel):
    contribution_id: str
    rule_id: str
    state_key: StateKey
    scope_type: ScopeType
    scope_ref: str
    direction: Direction
    base_step_band: StepBand
    base_step: int
    bounded_step: int
    source_kind: SourceKind
    source_appraisal_frame_refs: tuple[str, ...] = ()
    source_outcome_refs: tuple[str, ...] = ()
    source_dimension_keys: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    inhibition_refs: tuple[str, ...] = ()
    branch_refs: tuple[str, ...] = ()
    rationale: str = ""


class AttentionSignal(FrozenModel):
    target_type: ScopeType
    target_ref: str
    reason_refs: tuple[str, ...] = ()
    goal_relevance: int = Field(default=0, ge=0, le=100)
    goal_priority: int = Field(default=0, ge=0, le=100)
    novelty: int = Field(default=0, ge=0, le=100)
    uncertainty: int = Field(default=0, ge=0, le=100)
    dispute: int = Field(default=0, ge=0, le=100)
    boundary_significance: int = Field(default=0, ge=0, le=100)
    unresolved_pressure: int = Field(default=0, ge=0, le=100)
    recency: int = Field(default=100, ge=0, le=100)


class ControlContext(FrozenModel):
    disagreement: int = Field(default=0, ge=0, le=100)
    consequence: int = Field(default=0, ge=0, le=100)
    boundary_significance: int = Field(default=0, ge=0, le=100)
    clarification_available: bool = False
    expected_information_gain: int = Field(default=0, ge=0, le=100)
    exploration_permitted: bool = True
    repair_evidence: int = Field(default=0, ge=0, le=100)
    authorization: bool = False
    capability_available: bool = False
    conversational_timing_ready: bool = False


class UpdateRequest(FrozenModel):
    cognitive_turn_id: str
    conversation_ref: str
    previous_snapshot: StateSnapshot
    personality: PersonalityProfile
    elapsed_monotonic_ms: int = Field(default=0, ge=0)
    appraisal_frames: tuple[AppraisalFrame, ...] = ()
    outcomes: tuple[OutcomeObservation, ...] = ()
    context: DynamicStateContext | None = None
    evidence: tuple[TransitionEvidence, ...] = ()
    attention_signals: tuple[AttentionSignal, ...] = ()
    control_context: ControlContext = Field(default_factory=ControlContext)

    @model_validator(mode="after")
    def validate_references(self) -> UpdateRequest:
        if self.previous_snapshot.conversation_ref != self.conversation_ref:
            raise ValueError("previous state belongs to a different conversation")
        if self.previous_snapshot.personality_mapper_version != self.personality.mapper_version:
            raise ValueError("previous state used a different personality mapper")
        if self.context is not None and self.context.conversation_ref != self.conversation_ref:
            raise ValueError("dynamic-state context belongs to a different conversation")
        frame_ids = {item.frame_id for item in self.appraisal_frames}
        outcome_ids = {item.outcome_id for item in self.outcomes}
        dimension_keys = {
            item.dimension_key for frame in self.appraisal_frames for item in frame.dimensions
        }
        scenario_ids = {item.scenario_id for item in self.appraisal_frames}
        if len(frame_ids) != len(self.appraisal_frames) or len(outcome_ids) != len(self.outcomes):
            raise ValueError("dynamic-state source ids must be unique")
        for item in self.evidence:
            if not set(item.source_appraisal_frame_refs) <= frame_ids:
                raise ValueError("transition evidence cites an unknown appraisal frame")
            if not set(item.source_outcome_refs) <= outcome_ids:
                raise ValueError("transition evidence cites an unknown outcome")
            if not set(item.source_dimension_keys) <= dimension_keys:
                raise ValueError("transition evidence cites an unknown appraisal dimension")
            if not set(item.branch_refs) <= (scenario_ids | frame_ids):
                raise ValueError("transition evidence cites an unknown branch")
        return self


class UpdateResult(FrozenModel):
    snapshot: StateSnapshot
    contributions: tuple[Contribution, ...]
