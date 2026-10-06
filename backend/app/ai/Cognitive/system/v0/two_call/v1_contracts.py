"""Cognitive two-call protocol v1: typed Interpretation/Appraisal and Action policy.

Normative sources: ``CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md`` and
``SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md``.  Both model
outputs are closed: no unrestricted strings, no model-authored durable IDs,
no target-binding graphs, no content, segments or wording.  Every value is a
registered key, a bounded enum, or a code-supplied reference.
"""
from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator

from ..contracts.interpretation import FrozenModel

PROTOCOL_V1 = 'cognitive-two-call-v1'
UNDERSTANDING_INPUT_VERSION = 'understanding-input-v1'
UNDERSTANDING_PROPOSAL_VERSION = 'understanding-proposal-v1'
POLICY_INPUT_VERSION = 'action-policy-input-v1'
POLICY_PROPOSAL_VERSION = 'action-policy-proposal-v1'
UNDERSTANDING_COMPILER_VERSION = 'understanding-compiler-v1'
POLICY_COMPILER_VERSION = 'action-policy-compiler-v1'
# Versioned derivations the handoff (§6, §20.3) leaves to registered policy.
SUPPORT_POLICY_VERSION = 'reading-support-from-uncertainty-v1'
EPISTEMIC_POLICY_VERSION = 'epistemic-status-derivation-v1'
POLICY_MODE_ROUTER_VERSION = 'policy-mode-router-v1'
SPAN_ADDRESSING_VERSION = 'focus-token-span-v1'
# v1.1 adds the ``experience_report`` predicate and its grounding projection.
# A capture records its registry version and replays under it.
# v1.2 adds ``conversation_request`` (talk, not a produced artifact) and the
# user-owned goal unknown it grounds.
ARGUMENT_KIND_REGISTRY_VERSION = 'argument-kind-registry-v1.2'
ARGUMENT_KIND_REGISTRY_VERSIONS = ('argument-kind-registry-v1', 'argument-kind-registry-v1.1', 'argument-kind-registry-v1.2')
GROUNDING_PROJECTION_VERSION = 'understanding-grounding-v1.1'
GROUNDING_PROJECTION_VERSION_V1_2 = 'understanding-grounding-v1.2'
UNKNOWN_REGISTRY_VERSION = 'interpretation-unknown-keys-v1'
CONFLICT_REGISTRY_VERSION = 'interpretation-conflict-keys-v1'
SELECTION_FACTOR_REGISTRY_VERSION = 'action-selection-factors-v1'
APPRAISAL_SCHEMA_VERSION = 'appraisal-named-dimensions-v1'
# Trace-level policies recorded on the orchestration trace.
BEHAVIOR_POLICY_V1 = 'model_policy_selection-v1'
# v1.1: conversational wording content (``two_call/v1_content.py``).
# v1.2: validation policy ``conversational-v2`` (schema + two real-world claims block).
REALIZATION_POLICY_V1 = 'separate_realization-v1.2'
REALIZATION_POLICIES_V1 = ('separate_realization-v1', 'separate_realization-v1.1', 'separate_realization-v1.2')
# The Realization validation policy each v1 realization policy words under.
VALIDATION_POLICY_BY_REALIZATION_POLICY = {
    'separate_realization-v1': 'conversational-v1', 'separate_realization-v1.1': 'conversational-v1',
    'separate_realization-v1.2': 'conversational-v2',
}
ALIGNMENT_POLICY_V1 = 'not_applicable-v1'

Uncertainty = Literal['low', 'moderate', 'high']
UNCERTAINTY_ORDER = {'low': 0, 'moderate': 1, 'high': 2}

# ---------------------------------------------------------------------------
# Closed registries (model vocabularies).  Descriptions are code-owned prompt
# text; the model returns only the keys.
# ---------------------------------------------------------------------------

UNKNOWN_KEYS: dict[str, str] = {
    'interpretation.missing_target': 'What the event refers to or acts on is not identifiable from the supplied context.',
    'interpretation.missing_detail': 'A detail the event needs is not stated anywhere supplied.',
    'interpretation.ambiguous_reference': 'More than one supplied referent fits the event.',
    'interpretation.unclear_intent': 'What the speaker wants from this event is unclear.',
    'interpretation.unclear_scope': 'Which part of the conversation the event applies to is unclear.',
    'interpretation.missing_context': 'Reading the event depends on context that is not supplied.',
}

CONFLICT_KEYS: dict[str, str] = {
    'interpretation.claim_runtime_conflict': 'A user claim contradicts a trusted runtime fact.',
    'interpretation.claim_memory_conflict': 'A user claim contradicts an authorized memory record.',
    'interpretation.claim_dialogue_conflict': 'The focus message contradicts an earlier message in the supplied dialogue.',
    'interpretation.internal_contradiction': 'Two parts of the focus message contradict each other.',
}

# Registered, optional selection factors (proposal §10, §15).  A factor names
# why a candidate was preferred; it is never a prose rationale.
SELECTION_FACTOR_KEYS: dict[str, str] = {
    'goal.direct_progress': 'Directly advances an active goal.',
    'goal.response_obligation': 'Discharges the obligation to respond to this turn.',
    'goal.blocker_resolution': 'Addresses a typed planning condition blocking a goal.',
    'goal.content_delivery': 'Delivers prepared conversational content.',
    'action.uptake': 'Takes up what the user just contributed.',
    'action.clarification': 'Resolves a registered unknown or ambiguity.',
    'action.social_maintenance': 'Maintains the social exchange.',
    'action.boundary': 'Holds a safety, capability or permission boundary.',
    'interpretation.uncertainty': 'Responds to uncertainty in the accepted understanding.',
    'interpretation.conflict_repair': 'Responds to an active typed conflict.',
    'state.curiosity': 'Shaped by the agent\'s current curiosity state.',
    'state.engagement': 'Shaped by the agent\'s current engagement state.',
    'state.interaction_strain': 'Shaped by current interaction strain.',
    'state.social_alignment': 'Shaped by current social alignment.',
    'personality.curiosity': 'Personality preference: curiosity.',
    'personality.warmth': 'Personality preference: warmth.',
    'personality.playfulness': 'Personality preference: playfulness.',
    'personality.directness': 'Personality preference: directness.',
    'relationship.context': 'Shaped by the supplied relationship state.',
}

POLICY_MODES = ('direct', 'contextual', 'deliberative')
# Whole-plan bound per deterministic policy mode (handoff §12).
MAX_MOVES_BY_MODE = {'direct': 2, 'contextual': 3, 'deliberative': 4}

ARGUMENT_VALUE_KINDS = ('enum', 'span', 'span_set', 'entity_ref', 'object_ref', 'runtime_ref', 'memory_ref',
                        'integer', 'decimal', 'boolean', 'null')

# ---------------------------------------------------------------------------
# Supplied-reference grammar.  Spans address the focus message's code-owned
# word tokens: ``span:i`` is token i, ``span:i-j`` tokens i..j inclusive.
# ---------------------------------------------------------------------------

SPAN_REF_PATTERN = r'^span:(0|[1-9][0-9]{0,3})(-(0|[1-9][0-9]{0,3}))?$'
_SPAN_RE = re.compile(SPAN_REF_PATTERN)


def parse_span_ref(ref: str) -> tuple[int, int]:
    """``span:i[-j]`` -> inclusive token bounds.  Raises on any other form."""
    match = _SPAN_RE.fullmatch(ref) if isinstance(ref, str) else None
    if match is None:
        raise ValueError('foreign span reference')
    start = int(match.group(1))
    end = int(match.group(3)) if match.group(3) is not None else start
    if end < start:
        raise ValueError('reversed span reference')
    return start, end


def ref_kind(ref: str) -> str:
    return ref.split(':', 1)[0] if isinstance(ref, str) and ':' in ref else ''


# ---------------------------------------------------------------------------
# Call 1 output: UnderstandingProposalV1
# ---------------------------------------------------------------------------

class EnumValue(FrozenModel):
    kind: Literal['enum']
    value: str = Field(min_length=1)


class SpanValue(FrozenModel):
    kind: Literal['span']
    ref: str = Field(pattern=SPAN_REF_PATTERN)


class SpanSetValue(FrozenModel):
    kind: Literal['span_set']
    refs: tuple[Annotated[str, Field(pattern=SPAN_REF_PATTERN)], ...] = Field(min_length=1)


class EntityRefValue(FrozenModel):
    kind: Literal['entity_ref']
    ref: str = Field(pattern=r'^entity:[0-9]+$')


class ObjectRefValue(FrozenModel):
    kind: Literal['object_ref']
    ref: str = Field(pattern=r'^object:[0-9]+$')


class RuntimeRefValue(FrozenModel):
    kind: Literal['runtime_ref']
    ref: str = Field(pattern=r'^runtime:[a-z_]+$')


class MemoryRefValue(FrozenModel):
    kind: Literal['memory_ref']
    ref: str = Field(pattern=r'^memory:[0-9]+$')


class IntegerValue(FrozenModel):
    kind: Literal['integer']
    value: int


class DecimalValue(FrozenModel):
    kind: Literal['decimal']
    value: float

    @model_validator(mode='after')
    def finite(self):
        if not math.isfinite(self.value):
            raise ValueError('decimal argument values must be finite')
        return self


class BooleanValue(FrozenModel):
    kind: Literal['boolean']
    value: bool


class NullValue(FrozenModel):
    kind: Literal['null']


TypedArgumentValue = Annotated[Union[EnumValue, SpanValue, SpanSetValue, EntityRefValue, ObjectRefValue,
                                     RuntimeRefValue, MemoryRefValue, IntegerValue, DecimalValue, BooleanValue,
                                     NullValue], Field(discriminator='kind')]

Ordinal = Literal['low', 'moderate', 'high', 'not_applicable']
Signed = Literal['negative', 'neutral', 'positive', 'not_applicable']
AgencyValue = Literal['user', 'columbina', 'third_party', 'environment', 'unknown', 'not_applicable']

# Named appraisal fields in canonical DIMENSIONS order.
APPRAISAL_FIELDS = ('relevance', 'congruence', 'novelty', 'certainty', 'controllability',
                    'norm_compatibility', 'affiliative_value', 'agency')


class EventAppraisal(FrozenModel):
    """Named dimensions; ``null`` = unassessed, ``not_applicable`` = does not apply."""

    uncertainty: Uncertainty
    relevance: Ordinal | None
    congruence: Signed | None
    novelty: Ordinal | None
    certainty: Ordinal | None
    controllability: Ordinal | None
    norm_compatibility: Signed | None
    affiliative_value: Signed | None
    agency: AgencyValue | None


class InterpretationReading(FrozenModel):
    hypothesis_key: str = Field(min_length=1)
    uncertainty: Uncertainty
    context_refs: tuple[str, ...]
    appraisal: EventAppraisal

    @model_validator(mode='after')
    def unique_refs(self):
        if len(set(self.context_refs)) != len(self.context_refs):
            raise ValueError('duplicate reading context refs')
        return self


class SemanticEvent(FrozenModel):
    predicate_key: str = Field(min_length=1)
    polarity: Literal['positive', 'negative', 'unknown']
    # Keyed by the predicate's registered argument names (every name present;
    # an unresolved optional value is the ``null`` kind).
    arguments: dict[str, TypedArgumentValue]
    evidence_span_refs: tuple[Annotated[str, Field(pattern=SPAN_REF_PATTERN)], ...] = Field(min_length=1)
    readings: tuple[InterpretationReading, ...]

    @model_validator(mode='after')
    def unique_evidence(self):
        if len(set(self.evidence_span_refs)) != len(self.evidence_span_refs):
            raise ValueError('duplicate event evidence spans')
        keys = [r.hypothesis_key for r in self.readings]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate readings of one event')
        return self


class ExistingGoalImpact(FrozenModel):
    goal_ref: str = Field(pattern=r'^goal:[0-9]+$')
    event_index: int = Field(ge=0)
    reading_index: int | None = Field(default=None, ge=0)
    relevance: Literal['low', 'moderate', 'high'] | None
    congruence: Literal['negative', 'neutral', 'positive'] | None
    uncertainty: Uncertainty


class CognitiveSubjectPointer(FrozenModel):
    event_index: int = Field(ge=0)
    reading_index: int | None = Field(default=None, ge=0)
    argument_key: str | None = None


class UnknownRecord(FrozenModel):
    subject: CognitiveSubjectPointer
    unknown_key: str = Field(min_length=1)
    owner: Literal['user', 'runtime', 'system', 'unknown']
    evidence_refs: tuple[str, ...]


class ConflictRecord(FrozenModel):
    left_ref: str = Field(min_length=1)
    right_ref: str = Field(min_length=1)
    conflict_key: str = Field(min_length=1)
    status: Literal['active']

    @model_validator(mode='after')
    def distinct(self):
        if self.left_ref == self.right_ref:
            raise ValueError('a conflict relates two distinct references')
        return self


class UnderstandingProposalV1(FrozenModel):
    schema_version: Literal['understanding-proposal-v1']
    events: tuple[SemanticEvent, ...] = Field(min_length=1)
    existing_goal_impacts: tuple[ExistingGoalImpact, ...]
    unknowns: tuple[UnknownRecord, ...]
    conflicts: tuple[ConflictRecord, ...]


# ---------------------------------------------------------------------------
# Call 2 output: ActionPolicyProposalV1
# ---------------------------------------------------------------------------

class CandidateSelectionFactors(FrozenModel):
    candidate_ref: str = Field(pattern=r'^candidate:[0-9]+$')
    factor_keys: tuple[str, ...] = Field(min_length=1)
    source_refs: tuple[str, ...]

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.factor_keys)) != len(self.factor_keys) or len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError('duplicate selection factors or sources')
        return self


class ActionPolicyProposalV1(FrozenModel):
    schema_version: Literal['action-policy-proposal-v1']
    selected_candidate_refs: tuple[Annotated[str, Field(pattern=r'^candidate:[0-9]+$')], ...]
    primary_index: int | None = Field(ge=0)
    stop_after_selected: bool
    selection_factors: tuple[CandidateSelectionFactors, ...]

    @model_validator(mode='after')
    def coherent(self):
        if len(set(self.selected_candidate_refs)) != len(self.selected_candidate_refs):
            raise ValueError('selected candidates must be unique')
        if (self.primary_index is None) != (not self.selected_candidate_refs):
            raise ValueError('primary_index is null exactly for an empty plan')
        if self.primary_index is not None and self.primary_index >= len(self.selected_candidate_refs):
            raise ValueError('primary_index outside the selected plan')
        refs = [item.candidate_ref for item in self.selection_factors]
        if len(set(refs)) != len(refs) or not set(refs) <= set(self.selected_candidate_refs):
            raise ValueError('selection factors describe selected candidates, once each')
        return self


# ---------------------------------------------------------------------------
# Compiled, code-owned handoffs (stored as the capture's compiled proposal).
# ---------------------------------------------------------------------------

class CompiledPolicy(FrozenModel):
    """Canonical selection: candidate identities, never model-authored bindings."""

    version: Literal['action-policy-compiler-v1'] = POLICY_COMPILER_VERSION
    policy_mode: Literal['direct', 'contextual', 'deliberative']
    selected_candidate_ids: tuple[str, ...]
    selected_identities: tuple[tuple[str, tuple[tuple[str, str, str, str], ...], str], ...]
    primary_index: int | None
    stop_after_selected: Literal[True]
    selection_factors: tuple[dict[str, Any], ...]


__all__ = [name for name in globals() if not name.startswith('_')]
