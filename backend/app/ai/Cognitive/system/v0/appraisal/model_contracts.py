"""Provider-neutral, versioned proposal and trace contracts. No state authority."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol
from pydantic import Field, model_validator
from ..contracts.interpretation import FrozenModel

PROTOCOL_VERSION = 'appraisal-model-protocol-v0.1'
COMPILER_VERSION = 'appraisal-model-compiler-v0.1'
DIMENSIONS = {
    'appraisal.goal_relevance': ('unsigned_ordinal', 'Significance for the supplied focal goal.'),
    'appraisal.goal_congruence': ('signed_ordinal', 'Whether the event advances or obstructs the focal goal.'),
    'appraisal.novelty': ('unsigned_ordinal', 'Unexpectedness relative to supplied history; absent history is unknown.'),
    'appraisal.certainty': ('unsigned_ordinal', 'Confidence in understanding this event, not confidence in the user.'),
    'appraisal.controllability': ('unsigned_ordinal', 'Available effective response within supplied capabilities and permissions.'),
    'appraisal.norm_compatibility': ('signed_ordinal', 'Compatibility with supplied values and explicit boundaries.'),
    'appraisal.affiliative_value': ('signed_ordinal', 'Cooperation, repair or boundary conflict; disagreement is not rejection.'),
    'appraisal.agency': ('agency_category', 'Grounded causal agent; never an inferred hidden motive.'),
}
SCALES = {'unsigned_ordinal': ('low', 'moderate', 'high'),
          'signed_ordinal': ('negative', 'neutral', 'positive'),
          'agency_category': ('user', 'columbina', 'third_party', 'environment', 'unknown')}


class AppraisalModelError(RuntimeError):
    def __init__(self, code: Literal['configuration_unavailable', 'configuration_incompatible', 'timeout',
                                 'transport', 'refused', 'output_invalid', 'proposal_rejected']):
        self.code = code
        self.spans = ()
        super().__init__(code)

    @property
    def retryable(self):
        return self.code in {'timeout', 'transport'}


class ModelTrait(FrozenModel):
    key: str
    value: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    source_kind: Literal['mapped', 'explicit_policy_constant', 'unavailable']
    source_snapshot_ref: str | None
    scale_definition_ref: str
    behavioral_definition: str
    influence_boundary: str = 'Perceived significance only; never facts, intent, consent or state mutation.'


    @model_validator(mode="after")
    def source_consistency(self):
        if (self.source_kind == 'unavailable') != (self.value is None):
            raise ValueError('unavailable traits have no value; sourced traits require one')
        if self.source_kind != 'unavailable' and self.source_snapshot_ref is None:
            raise ValueError('sourced traits require a snapshot reference')
        return self


class CognitiveModelContext(FrozenModel):
    schema_version: Literal['cognitive-model-context-appraisal-v0.1'] = 'cognitive-model-context-appraisal-v0.1'
    agent_identity_ref: str | None = None
    traits: tuple[ModelTrait, ...] = ()
    dynamic_state_snapshot_ref: str | None = None
    dynamic_state_timing: Literal['pre_appraisal'] = 'pre_appraisal'
    dynamic_state: tuple[Mapping[str, Any], ...] = ()
    relationship_availability: Literal['available', 'unavailable'] = 'unavailable'
    relationship_snapshot_ref: str | None = None
    relationship: Mapping[str, Any] | None = None
    synthetic: bool = False
    dynamic_state_scale_ref: str = 'dynamic-state-schema-v0:signed-100-or-unsigned-100'
    dialogue_context: tuple[Mapping[str, Any], ...] = ()
    omitted_dialogue_refs: tuple[str, ...] = ()


class AppraisalModelPayload(FrozenModel):
    protocol_version: Literal['appraisal-model-protocol-v0.1'] = PROTOCOL_VERSION
    focus_message_ref: str
    context: CognitiveModelContext
    pairs: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]
    runtime: Mapping[str, Any]
    values: tuple[Mapping[str, Any], ...]
    supplemental_inputs: tuple[Mapping[str, Any], ...]
    dimensions: Mapping[str, Any] = DIMENSIONS
    scales: Mapping[str, Any] = SCALES


class DimensionProposal(FrozenModel):
    dimension_key: str
    status: Literal['known', 'unknown', 'disputed', 'not_applicable']
    value: str | None
    assessment_basis: Literal['observed', 'inferred', 'insufficient', 'not_applicable']
    uncertainty: Literal['low', 'moderate', 'high']
    evidence_refs: tuple[str, ...]
    evidence_summary: str = Field(min_length=1, max_length=240)
    missing_input_keys: tuple[str, ...]
    conflict_refs: tuple[str, ...]


class FrameProposal(FrozenModel):
    scenario_ref: str
    goal_ref: str
    dimensions: tuple[DimensionProposal, ...] = Field(min_length=8, max_length=8)


class AppraisalModelOutput(FrozenModel):
    frames: tuple[FrameProposal, ...]


class AppraisalModelTrace(FrozenModel):
    protocol_version: Literal['appraisal-model-protocol-v0.1'] = PROTOCOL_VERSION
    compiler_version: Literal['appraisal-model-compiler-v0.1'] = COMPILER_VERSION
    implementation: Literal['model'] = 'model'
    replay: Literal['frozen_output_only'] = 'frozen_output_only'
    prompt_version: str
    prompt_hash: str
    payload_digest: str
    provenance: Mapping[str, Any]
    payload: AppraisalModelPayload
    proposal: AppraisalModelOutput


class AppraisalCandidateModel(Protocol):
    @property
    def provenance(self) -> Mapping[str, Any]: ...
    def generate(self, payload: AppraisalModelPayload) -> AppraisalModelOutput: ...
