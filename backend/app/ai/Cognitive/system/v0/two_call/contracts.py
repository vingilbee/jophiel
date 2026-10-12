"""Versioned shadow orchestration proposals; never authoritative consumer contracts."""
import hashlib
import json
from typing import Any, Literal
from pydantic import model_serializer, Field, model_validator
from ..contracts.dialogue_interpretation import InterpretedUpdate
from ..contracts.interpretation import FrozenModel
from ..interpretation_v0.model_protocol import SemanticModelOutput
from ..appraisal.model_contracts import DimensionProposal
from ..collaborative_content_v0.model_protocol import CollaborationModelOutput
from ..latency import LatencySpan
from ..contracts.actions import ActionTargetType

VERSION = 'cognitive-two-call-v0.1'

class OutcomeAssessment(FrozenModel):
    definition_key: str
    existing_goal_ref: str | None
    # Local semantic handles are indices in interpretation.frames. Empty is
    # permitted only for an exact existing goal or the code-owned obligation.
    frame_handles: tuple[int, ...]
    dimensions: tuple[DimensionProposal, ...] = Field(min_length=8, max_length=8)

class CallOneOutput(FrozenModel):
    interpretation: SemanticModelOutput
    appraisal: tuple[OutcomeAssessment, ...]
    # Registry v1.7 only (omitted otherwise, so earlier digests never change):
    # the validated relationships, and how many proposed ones failed validation.
    dialogue_updates: tuple[InterpretedUpdate, ...] | None = None
    dialogue_updates_dropped: int | None = Field(default=None, ge=0)
    # Registry v1.8 only, when there is a known recall: where its answer came
    # from. "written" is the model's own answer, found in a user message;
    # "cited" means it fell back to the cited passage.
    recall_answer_source: Literal['written', 'cited'] | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_updates(self, handler):
        data = handler(self)
        if isinstance(data, dict):
            for key in ('dialogue_updates', 'dialogue_updates_dropped', 'recall_answer_source'):
                if data.get(key) is None:
                    data.pop(key, None)
        return data

class ContentProposal(FrozenModel):
    # Index in the ordered, code-prepared content requests, not a future ID.
    request_handle: int = Field(ge=0)
    output: CollaborationModelOutput

class LocalTarget(FrozenModel):
    role: str
    target_type: ActionTargetType
    existing_ref: str | None
    revision_ref: str | None
    content_request_handle: int | None
    object_kind: Literal['artifact', 'content', 'inquiry', 'partial_plan'] | None
    item_handle: int | None

class BehaviorMove(FrozenModel):
    action_key: str
    # Selection can only name the definition/targets already known at call 2.
    # No future candidate ID is accepted from a model.
    goal_refs: tuple[str, ...]
    target_bindings: tuple[LocalTarget, ...]
    excerpt: str = Field(min_length=1)

class ContentAlignment(FrozenModel):
    move_handle: int = Field(ge=0)
    # Optional legacy hint: index in this move's final compiled bindings.
    # New runs derive missing spans after compilation; the model need not guess it.
    binding_handle: int = Field(ge=0)
    excerpt: str = Field(min_length=1)

class ResponseCandidate(FrozenModel):
    text: str = Field(min_length=1)
    content_alignments: tuple[ContentAlignment, ...]

class CallTwoOutput(FrozenModel):
    content_proposal: tuple[ContentProposal, ...]
    behavior_proposal: tuple[BehaviorMove, ...]
    response_candidate: ResponseCandidate

class CallCapture(FrozenModel):
    # ``contribution`` (registry v1.6) is recorded in ``TwoCallTrace.contributions``.
    stage: Literal['call_one', 'call_two', 'contribution']
    prompt_hash: str
    payload_digest: str
    payload: dict[str, Any]
    proposal: dict[str, Any]
    provenance: dict[str, Any]
    protocol: Literal['legacy-v0.1','context-conditioned-two-call-v0.1','cognitive-two-call-v1'] = 'legacy-v0.1'
    reference_table: dict[str, Any] | None = None
    compiled_proposal: dict[str, Any] | None = None
    attribution: dict[str, Any] | None = None
    # Code-only authorized snapshot for replay/Realization, excluded from packets.
    dialogue_context: dict[str, Any] | None = None

    @model_serializer(mode='wrap')
    def omit_legacy_capture_fields(self,handler):
        data=handler(self)
        if self.protocol=='legacy-v0.1':data.pop('protocol',None)
        for key in ('reference_table','compiled_proposal','attribution','dialogue_context'):
            if data.get(key) is None:data.pop(key,None)
        return data

    @model_validator(mode='after')
    def payload_bound(self):
        canonical = json.dumps(self.payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if hashlib.sha256(canonical.encode()).hexdigest()!=self.payload_digest:
            raise ValueError('captured payload digest mismatch')
        if self.protocol != 'legacy-v0.1' and self.compiled_proposal is not None:
            if self.protocol == 'cognitive-two-call-v1':
                from .v1 import compile_understanding,compile_policy
                compiler=compile_understanding if self.stage=='call_one' else compile_policy
            else:
                from .compact import compile_mind,compile_response
                compiler=compile_mind if self.stage=='call_one' else compile_response
            from .provider import digest
            if digest(compiler(self.proposal,self.payload,self.reference_table)) != digest(self.compiled_proposal):
                raise ValueError('compiled proposal differs from captured compact source')
        if len(self.prompt_hash)!=64:
            raise ValueError('invalid captured prompt hash')
        if not isinstance(self.provenance.get('model_id'),str) or not self.provenance['model_id'].strip():
            raise ValueError('captured model identity missing')
        for key in ('timeout_seconds','max_output_tokens'):
            value = self.provenance.get(key)
            if isinstance(value,bool) or not isinstance(value,int) or value<1:
                raise ValueError('captured provider configuration missing')
        return self

class FormationBinding(FrozenModel):
    goal_ref: str
    frame_refs: tuple[str, ...]

class TwoCallTrace(FrozenModel):
    version: Literal['cognitive-two-call-v0.1'] = VERSION
    protocol: Literal['legacy-v0.1','context-conditioned-two-call-v0.1','cognitive-two-call-v1'] = 'legacy-v0.1'
    # Legacy captures require scorer agreement. New captures compile model
    # choices to grounded candidates; gates and transitions remain authoritative.
    # v1 selects an ordered plan from candidate refs; Realization is separate.
    behavior_policy: Literal['strict_plan_agreement-v0.1', 'model_grounded_selection-v0.1', 'model_policy_selection-v1', 'model_policy_selection-v1.1'] = 'strict_plan_agreement-v0.1'
    realization_policy: Literal['strict-v0.1', 'style_advisory-v0.1', 'segmented_response-v0.1', 'segmented_response-v0.2', 'separate_realization-v1', 'separate_realization-v1.1', 'separate_realization-v1.2', 'separate_realization-v1.3', 'separate_realization-v1.4'] = 'strict-v0.1'
    alignment_policy: Literal['complete_bindings-v0.1', 'compiled_move_spans-v0.1', 'not_applicable-v1'] = 'complete_bindings-v0.1'
    calls: tuple[CallCapture, ...]
    # Registry v1.6 open-world contribution calls, in order; empty (and omitted
    # from the serialized trace) for every earlier capture.
    contributions: tuple[CallCapture, ...] = ()
    goal_binding_version: Literal['goal-formation-binding-v0.1', 'goal-formation-binding-v0.2', 'goal-formation-binding-v0.3', 'goal-formation-binding-v0.4'] = 'goal-formation-binding-v0.1'
    goal_bindings: tuple[FormationBinding, ...] = ()
    spans: tuple[LatencySpan, ...] = ()

    @model_serializer(mode='wrap')
    def omit_legacy_realization_policy(self, handler):
        data = handler(self)
        if self.protocol=='legacy-v0.1':data.pop('protocol',None)
        if self.alignment_policy == 'complete_bindings-v0.1':
            data.pop('alignment_policy', None)
        if self.realization_policy == 'strict-v0.1':
            data.pop('realization_policy', None)
        if not self.contributions:
            data.pop('contributions', None)
        return data

    @model_validator(mode='after')
    def ordered_calls(self):
        if any(c.protocol!=self.protocol for c in self.calls):
            raise ValueError('capture protocol differs from orchestration')
        if self.protocol!='legacy-v0.1' and any(c.compiled_proposal is None for c in self.calls):
            raise ValueError('compact captures require compiled accepted handoffs')
        if tuple(c.stage for c in self.calls) != ('call_one', 'call_two'):
            raise ValueError('a completed two-call trace has exactly the two ordered captures')
        if any(c.stage != 'contribution' or c.protocol != 'cognitive-two-call-v1' for c in self.contributions):
            raise ValueError('contribution captures are protocol v1 contribution calls')
        v1_realization = self.realization_policy in ('separate_realization-v1', 'separate_realization-v1.1',
                                                     'separate_realization-v1.2', 'separate_realization-v1.3',
                                                     'separate_realization-v1.4')
        v1_behavior = self.behavior_policy in ('model_policy_selection-v1', 'model_policy_selection-v1.1')
        v1_policies = v1_behavior and self.alignment_policy == 'not_applicable-v1' and v1_realization
        if (self.protocol == 'cognitive-two-call-v1') != v1_policies or (not v1_policies and (
                v1_behavior or v1_realization
                or 'not_applicable-v1' == self.alignment_policy)):
            raise ValueError('protocol v1 and its selection/realization policies occur together')
        return self
