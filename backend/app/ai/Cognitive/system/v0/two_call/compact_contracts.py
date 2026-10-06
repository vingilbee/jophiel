"""Compact provider contracts. Canonical state and references remain code-owned."""
from typing import Any, Literal
from pydantic import Field, model_validator
from ..contracts.interpretation import FrozenModel, HypothesisSupport
from ..interpretation_v0.model_protocol import SemanticArgumentCandidate
from ..appraisal.model_contracts import DIMENSIONS, SCALES
from .contracts import ContentProposal, LocalTarget

COMPACT_PROTOCOL = 'context-conditioned-two-call-v0.1'

class TurnContext(FrozenModel):
    version: Literal['turn-context-v0.1'] = 'turn-context-v0.1'
    dialogue: tuple[dict[str, Any], ...]
    dialogue_memory: dict[str, Any]
    focus_handle: str
    persona: dict[str, Any]
    biases: dict[str, Any]
    relationship: dict[str, Any]
    state_before: dict[str, Any]
    memories: tuple[dict[str, Any], ...]
    runtime: dict[str, Any]
    previous_goals: tuple[dict[str, Any], ...]
    open_inquiries: tuple[dict[str, Any], ...]
    perception: tuple[dict[str, Any], ...]
    omissions: tuple[dict[str, Any], ...] = ()

class SourceSpan(FrozenModel):
    message_handle: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)

class Observation(FrozenModel):
    handle: str
    predicate: str
    polarity: Literal['positive', 'negative', 'unknown']
    arguments: tuple[SemanticArgumentCandidate, ...]
    evidence_handles: tuple[str, ...] = Field(min_length=1)
    source_span: SourceSpan | None = None
    epistemic_status: Literal['expressed', 'user_claim', 'uncertain']

class Reading(FrozenModel):
    handle: str
    event_handles: tuple[str, ...] = Field(min_length=1)
    hypothesis_key: str
    candidate_meaning: str = Field(min_length=1, max_length=160)
    support: HypothesisSupport
    uncertainty: Literal['low', 'moderate', 'high']
    evidence_handles: tuple[str, ...]
    context_handles: tuple[str, ...]

class DimensionException(FrozenModel):
    index: int = Field(ge=0, le=7)
    uncertainty: Literal['low', 'moderate', 'high']
    evidence_handles: tuple[str, ...]
    context_handles: tuple[str, ...]

class EventAppraisal(FrozenModel):
    event_handles: tuple[str, ...] = Field(min_length=1)
    reading_handle: str | None = None
    # Ordered DIMENSIONS: null = unknown, 'not_applicable' = not applicable.
    values: tuple[str | None, ...] = Field(min_length=8, max_length=8)
    uncertainty: Literal['low', 'moderate', 'high']
    evidence_handles: tuple[str, ...]
    context_handles: tuple[str, ...]
    exceptions: tuple[DimensionException, ...] = ()

    @model_validator(mode='after')
    def scales(self):
        if len({e.index for e in self.exceptions}) != len(self.exceptions):
            raise ValueError('duplicate dimension exceptions')
        for i,(key,(kind,_)) in enumerate(DIMENSIONS.items()):
            value=self.values[i]
            if value is not None and value != 'not_applicable' and value not in SCALES[kind]:
                raise ValueError('invalid compact dimension scale')
            certainty=next((e.uncertainty for e in self.exceptions if e.index==i),self.uncertainty)
            if value not in {None,'not_applicable'} and certainty=='high':
                raise ValueError('assessed dimensions require bounded uncertainty')
        return self

class GoalImpact(FrozenModel):
    goal_handle: str
    event_handles: tuple[str, ...] = Field(min_length=1)
    relevance: Literal['low','moderate','high'] | None
    congruence: Literal['negative','neutral','positive'] | None
    uncertainty: Literal['low','moderate','high']

    @model_validator(mode='after')
    def bounded_assessment(self):
        if self.uncertainty=='high' and (self.relevance is not None or self.congruence is not None):
            raise ValueError('assessed goal impacts require bounded uncertainty')
        if len(set(self.event_handles))!=len(self.event_handles):raise ValueError('duplicate goal impact events')
        return self

class Unresolved(FrozenModel):
    subject_handle: str
    information: str = Field(min_length=1, max_length=160)

class MindProposal(FrozenModel):
    observations: tuple[Observation, ...] = Field(min_length=1)
    readings: tuple[Reading, ...]
    event_appraisals: tuple[EventAppraisal, ...]
    existing_goal_impacts: tuple[GoalImpact, ...]
    unknowns: tuple[Unresolved, ...]
    conflicts: tuple[Unresolved, ...]

class ClaimBinding(FrozenModel):
    content_handle: str
    # Empty in new provider output: support comes from the referenced item's
    # source_refs. Retained to validate/replay prior captured proposals.
    source_handles: tuple[str, ...]

class Segment(FrozenModel):
    handle: str
    text: str = Field(min_length=1)
    content_handles: tuple[str, ...]
    claim_bindings: tuple[ClaimBinding, ...]

class ResponseMove(FrozenModel):
    choice_handle: str | None
    template_handle: str | None
    goal_handles: tuple[str, ...]
    target_bindings: tuple[LocalTarget, ...]
    segment_handles: tuple[str, ...] = Field(min_length=1)
    # Only code-defined typed parameters of the selected choice are permitted.
    parameters: tuple[SemanticArgumentCandidate, ...] = ()

    @model_validator(mode='after')
    def one_selection(self):
        if (self.choice_handle is None)==(self.template_handle is None):
            raise ValueError('select exactly one grounded choice or construction template')
        return self

class ResponseInquiry(FrozenModel):
    request_handle: int = Field(ge=0)
    item_handle: int = Field(ge=0)
    target_handle: str
    asked_about: str
    # Blocking request-field clarification uses a grounded requirement choice.
    # Collaboration candidates currently support optional inquiries only.
    timing: Literal['optional']

class ResponseProposal(FrozenModel):
    content_ops: tuple[ContentProposal, ...]
    moves: tuple[ResponseMove, ...]
    segments: tuple[Segment, ...]
    inquiries: tuple[ResponseInquiry, ...]

DECISION_CONTEXT_VERSION = 'decision-context-v0.2'

class DecisionContext(FrozenModel):
    # v0.2 adds each content request's required delivery_move and selects the
    # v0.2 Call 2 prompt plus lossless response normalization. v0.1 captures
    # keep their own prompt and compile exactly as before.
    version: Literal['decision-context-v0.1', 'decision-context-v0.2'] = DECISION_CONTEXT_VERSION
    shared_context: dict[str, Any]
    understanding: dict[str, Any]
    state_after: dict[str, Any]
    goals: tuple[dict[str, Any], ...]
    readiness: tuple[dict[str, Any], ...]
    content_requests: tuple[dict[str, Any], ...]
    answer_permissions: tuple[dict[str, Any], ...]
    choices: tuple[dict[str, Any], ...]
    templates: tuple[dict[str, Any], ...]
