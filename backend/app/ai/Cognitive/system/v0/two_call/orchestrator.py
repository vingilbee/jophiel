"""Two provider calls with local references compiled into existing contracts.

Model behavior binds to actual constructed, hard-gated candidates. Legacy
captures retain strict scorer agreement; neither path rewrites supplied wording.
"""
import hashlib
from ..appraisal.context import with_dialogue, project_context
from types import SimpleNamespace
from ..appraisal.model_contracts import AppraisalModelError, AppraisalModelOutput, FrameProposal, DimensionProposal, DIMENSIONS, SCALES
from ..appraisal.model_engine import compile_proposal, payload_for, proposal_reference_scope
from ..contracts.goals import RESPONSE_OBLIGATION_DEFINITION_KEY
from ..contracts.actions import ActionSelectionRequest
from ..action_selection_v0 import ActionSelectionError
from ..realization_v0.errors import RealizationExecutionError
from ..collaborative_content_v0.engine import CollaborativeContentEngine
from ..collaborative_content_v0.model_protocol import CollaborationProviderError
from ..contracts.collaboration import CollaborationFailureCode
from ..interpretation_v0.model_grounding import ModelSemanticGrounder, canonical_candidate_arguments
from ..interpretation_v0.engine import InterpretationEngine
from ..semantic_parser_provider import resolve_provenance
from ..latency import SpanRecorder, LatencySpan
from .contracts import VERSION, CallCapture, TwoCallTrace
from .provider import digest, prompt, FrozenTwoCallModel, call_one_evidence_refs

V1 = 'cognitive-two-call-v1'


def v1_failure_code(exc):
    """Closed, content-free code for a v1 compile rejection (never model text)."""
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        return 'proposal_shape'
    token = str(exc).replace(' ', '_')
    return token if token in V1_FAILURE_CODES else 'invalid_reference_contract'


V1_FAILURE_CODES = frozenset(message.replace(' ', '_') for message in (
    'foreign predicate', 'event arguments differ from the predicate registry', 'argument value kind not permitted',
    'argument value outside vocabulary', 'foreign span reference', 'reversed span reference', 'span outside focus tokens',
    'span outside supplied evidence', 'duplicate span-set members', 'reference kind not permitted here',
    'foreign local reference', 'answer option not offered by its inquiry', 'foreign reading key',
    'duplicate existing goal impacts', 'foreign goal impact', 'goal impact event outside proposal',
    'goal impact reading outside event', 'foreign unknown key', 'unknown subject outside proposal',
    'unknown subject reading outside event', 'unknown subject argument not registered for its predicate',
    'foreign conflict key', 'duplicate conflict', 'focus tokens differ from the supplied input',
    'focus message has no addressable tokens', 'focus message too long', 'no authorized focus span',
    'unauthorized memory', 'duplicate reference handle', 'duplicate eligible candidate identity', 'foreign candidate',
    'plan exceeds maximum moves', 'iterative planning is not offered', 'empty plan for a required response',
    'foreign selection factor', 'foreign selection factor source', 'personality factor without available personality',
    'relationship factor without available relationship',
    'dialogue citation differs from supplied source', 'dialogue citation must name a supplied user message',
    'dialogue citation exceeds excerpt bound', 'duplicate dialogue candidates',
    'recall answer and knowledge status disagree', 'perspective needs distinct supporting passages',
    'unambiguous recall must not carry competing candidates'))


class TwoCallError(AppraisalModelError):
    def __init__(self, code='proposal_rejected', *, stage='call_one', subject=''):
        super().__init__(code)
        self.orchestration_stage = stage
        self.subject = subject

from ..goal_v0.engine import GoalEngine

class BindingGoalEngine(GoalEngine):
    """Versioned sidecar mirrors the actual formation basis, without rewriting goals."""
    binding_version = 'goal-formation-binding-v0.4'
    def update(self, request):
        self.formation_bindings = {}
        result = super().update(request)
        return result
    def _form(self, *args, **kwargs):
        records = super()._form(*args, **kwargs)
        # A required-unknown goal inherits only its exact canonical parent's
        # recorded formation frames, not a global matching semantic act.
        for record in records:
            parents = [t.ref for r in record.requirements for t in r.target_refs if t.role=='parent_goal_ref']
            if len(parents)==1 and parents[0] in self.formation_bindings:
                self.formation_bindings[record.goal_id] = self.formation_bindings[parents[0]]
        return records
    def _bind(self, *args, **kwargs):
        formed = super()._bind(*args, **kwargs)
        for record,basis in formed:
            refs = tuple(basis.frame_refs)
            if record.goal_id in self.formation_bindings and self.formation_bindings[record.goal_id] != refs:
                raise ValueError('ambiguous formation binding')
            self.formation_bindings[record.goal_id] = refs
        return formed

class SemanticAdapter:
    def __init__(self, owner):
        self.owner = owner
        self.version = VERSION if owner.protocol=='legacy-v0.1' else owner.protocol
        p = owner.model.provenance
        self.provenance = resolve_provenance(model_id=p['model_id'], timeout_seconds=p['timeout_seconds'], max_output_tokens=p['max_output_tokens'])
        overrides={'provider_adapter':self.version}
        if owner.protocol!='legacy-v0.1':overrides['protocol_version']=owner.protocol
        self.provenance = type(self.provenance)(**(self.provenance.model_dump(mode='python') | overrides))

    def generate(self, semantic_input):
        self.owner.recorder.stage = 'call_one'
        with self.owner.recorder.measure('payload_compile'):
            payload = self._payload(semantic_input)
        output = self.owner.call('call_one', payload)
        if self.owner.protocol==V1:
            from .v1 import accepted_mind,compile_understanding
            try:
                with self.owner.recorder.measure('response_compile'):
                    compiled=compile_understanding(output,payload,self.owner.references.entries)
                    self.owner.record_compilation(compiled)
                    self.owner.mind,self.owner.understanding=accepted_mind(output,payload,self.owner.references.entries)
            except ValueError as exc:
                error=TwoCallError(stage='call_one',subject='understanding_compilation.'+v1_failure_code(exc))
                error.spans=tuple(self.owner.recorder.spans)
                raise error from exc
            self.owner.one=compiled
            return compiled.interpretation
        if self.owner.protocol!='legacy-v0.1':
            from .compact import compile_mind
            self.owner.mind=output
            try:
                with self.owner.recorder.measure('response_compile'):
                    output=compile_mind(output,payload,self.owner.references.entries)
                    self.owner.record_compilation(output)
            except ValueError as exc:
                error=TwoCallError(stage='call_one',subject='mind_reference_compilation')
                error.spans=tuple(self.owner.recorder.spans)
                raise error from exc
        self.owner.one = output
        return output.interpretation

    def _payload(self, semantic_input):
        from ..goal_v0.ontology import all_definitions
        request = self.owner.request
        relationship = request.relationship.to_appraisal_relationship() if request.relationship else None
        context = with_dialogue(project_context(SimpleNamespace(
            action_personality=request.action_personality,relationship=relationship,
            previous_state=request.previous_state,core_memory_ref=request.core_memory_ref)),
            request.messages,request.focus_message_id)
        if self.owner.protocol==V1:
            from .v1 import build_understanding_input
            return build_understanding_input(self.owner,semantic_input)
        if self.owner.protocol!='legacy-v0.1':
            from .compact import mind_packet
            return mind_packet(self.owner,semantic_input)
        payload = dict(version=VERSION, semantic_input=semantic_input.model_dump(mode='json'),
            appraisal_context=dict(context=context.model_dump(mode='json'),
                existing_goals=request.previous_goal_snapshot.model_dump(mode='json') if request.previous_goal_snapshot else None,
                runtime=request.runtime.to_appraisal_runtime().model_dump(mode='json', exclude={'diagnostics','provided_parameters','last_tool_event'}),
                values=[v.model_dump(mode='json') for v in request.values],
                supplemental_inputs=[v.model_dump(mode='json') for v in request.supplemental_inputs]),
            goal_definitions=[d.model_dump(mode='json',exclude={'definition_version','allowed_owners','allowed_scopes','default_priority_band'}) for d in all_definitions()], dimensions=DIMENSIONS, scales=SCALES)
        return payload

class AppraisalAdapter:
    def __init__(self, owner):
        self.owner = owner

    def appraise_turn(self, request, turn):
        self.owner.recorder.stage = 'call_one'
        try:
            with self.owner.recorder.measure('response_compile'):
                return self._appraise_turn(request,turn)
        except AppraisalModelError as exc:
            exc.spans = tuple(self.owner.recorder.spans)
            raise

    def _appraise_turn(self, request, turn):
        owner = self.owner
        if owner.protocol==V1:
            from .compact_runtime import appraise_events
            from .v1_contracts import UNDERSTANDING_INPUT_VERSION
            try:
                return appraise_events(owner,request,turn,binding='understanding-event-binding-v1',
                    context_version=UNDERSTANDING_INPUT_VERSION,prompt_version='call_one-v1',
                    per_event_goal_impacts=True), ()
            except ValueError as exc:
                raise TwoCallError(stage='call_one',subject='event_appraisal_binding') from exc
        if owner.protocol!="legacy-v0.1":
            from .compact_runtime import appraise_events
            try:
                return appraise_events(owner,request,turn), ()
            except ValueError as exc:
                raise TwoCallError(stage='call_one',subject='event_appraisal_binding') from exc
        try:
            if owner.one is None:
                raise TwoCallError(subject='combined_appraisal_missing')
            # Bind local handles through compiled frames, never recompute IDs.
            mapping = {}
            for handle, candidate in enumerate(owner.one.interpretation.frames):
                args = canonical_candidate_arguments(candidate)
                matches = [frame for frame in request.interpretation.semantic_frames
                           if frame.predicate == candidate.predicate and frame.polarity == candidate.polarity
                           and dict(frame.arguments) == args]
                if len(matches) != 1:
                    raise TwoCallError(subject='frame_handle_unresolved')
                mapping[handle] = matches[0].frame_id
            goals = {g.goal_id:g for g in request.active_goals}
            bound = {}
            ambiguous = set()
            previous = owner.request.previous_goal_snapshot
            previous_ids = {g.goal_id for g in previous.records} if previous else set()
            for assessment in owner.one.appraisal:
                handles = assessment.frame_handles
                if len(set(handles)) != len(handles) or any(h not in mapping for h in handles):
                    raise TwoCallError(subject='frame_handle_foreign')
                refs = {mapping[h] for h in handles}
                if len(refs) != len(handles):
                    raise TwoCallError(subject='frame_binding_duplicate')
                if assessment.existing_goal_ref:
                    if handles or assessment.existing_goal_ref not in previous_ids:
                        raise TwoCallError(subject='existing_goal_foreign')
                    matches = [g for g in goals.values() if g.goal_id == assessment.existing_goal_ref
                               and g.definition_ref.definition_key == assessment.definition_key]
                elif assessment.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY:
                    if handles:
                        raise TwoCallError(subject='response_obligation_handles')
                    matches = [g for g in goals.values() if g.definition_ref.definition_key == assessment.definition_key]
                else:
                    if not refs:
                        raise TwoCallError(subject='outcome_source_missing')
                    matches = [g for g in goals.values() if g.definition_ref.definition_key == assessment.definition_key
                               and refs == set(owner.goal_engine.formation_bindings.get(g.goal_id, ()))]
                # An intended outcome not adopted is ignored. It never creates
                # a goal; unmatched adopted outcomes become explicit unknowns
                # under v0.2 (v0.1 frozen replay retains strict coverage).
                if not matches:
                    continue
                if len(matches) != 1 or matches[0].goal_id in bound:
                    if owner.goal_engine.binding_version == 'goal-formation-binding-v0.1':
                        raise TwoCallError(subject='outcome_binding_ambiguous')
                    ambiguous.update(g.goal_id for g in matches)
                    continue
                bound[matches[0].goal_id] = assessment
            if owner.goal_engine.binding_version == 'goal-formation-binding-v0.1' and set(bound) != set(goals):
                raise TwoCallError(subject='goal_coverage_missing')
            payload = payload_for(request, turn.appraisal_model_context)
            compiled = []
            supplied_refs = call_one_evidence_refs(owner.captures[0].model_dump(mode='json')['payload'])
            sent_refs = {e['evidence_id'] for e in payload.evidence}
            context_refs = {v['ref'] for v in payload.context.dialogue_context}
            context_refs |= {t.source_snapshot_ref for t in payload.context.traits if t.source_snapshot_ref}
            context_refs |= {payload.context.dynamic_state_snapshot_ref, payload.context.relationship_snapshot_ref}
            context_refs.add(request.runtime.capability_snapshot.snapshot_id)
            context_refs |= {v.value_id for v in request.values} | {v.input_id for v in request.supplemental_inputs}
            for scenario in request.interpretation.scenarios:
                if scenario.disposition not in {'leading','retained_alternative'}:
                    continue
                for goal in request.active_goals:
                    dims = []
                    assessment = bound.get(goal.goal_id)
                    if assessment is None or goal.goal_id in ambiguous:
                        # Call 1 cannot know which goals code will adopt. Missing
                        # or ambiguous ratings carry explicit unknowns, never a
                        # guessed score or another provider call.
                        reason = 'two_call_ambiguous_goal_assessment' if goal.goal_id in ambiguous else 'two_call_missing_goal_assessment'
                        dimensions = tuple(DimensionProposal(dimension_key=key, status='unknown', value=None,
                            assessment_basis='insufficient', uncertainty='high', evidence_refs=(),
                            evidence_summary='No uniquely bound assessment for the adopted goal.',
                            missing_input_keys=(reason,), conflict_refs=()) for key in DIMENSIONS)
                    else:
                        dimensions = assessment.dimensions
                    pair_refs = proposal_reference_scope(request, payload, scenario, goal)
                    for d in dimensions:
                        cited = set(d.evidence_refs) | set(d.conflict_refs)
                        if owner.goal_engine.binding_version == 'goal-formation-binding-v0.4':
                            if not cited <= supplied_refs:
                                raise TwoCallError(subject='evidence_foreign')
                            unbound = cited - pair_refs
                            if unbound:
                                from ..appraisal.models import AppraisalDimension
                                try:
                                    if ((d.status == 'known' and (d.assessment_basis not in {'observed', 'inferred'} or d.uncertainty == 'high'))
                                        or (d.status == 'unknown' and d.assessment_basis != 'insufficient')
                                        or (d.status == 'not_applicable' and d.assessment_basis != 'not_applicable')):
                                        raise ValueError('inconsistent epistemic basis')
                                    AppraisalDimension(dimension_key=d.dimension_key,
                                        value_type=DIMENSIONS.get(d.dimension_key, (None,))[0],
                                        status=d.status, value=d.value,
                                        missing_input_keys=d.missing_input_keys, conflict_refs=d.conflict_refs)
                                except ValueError as exc:
                                    raise TwoCallError(subject='canonical_appraisal_validation') from exc
                                d = DimensionProposal(**(d.model_dump(mode='python') | {
                                    'status':'unknown', 'value':None, 'assessment_basis':'insufficient', 'uncertainty':'high',
                                    'evidence_refs':tuple(ref for ref in d.evidence_refs if ref in pair_refs),
                                    'conflict_refs':tuple(ref for ref in d.conflict_refs if ref in pair_refs),
                                    'evidence_summary':'Supplied citations could not bind to this adopted goal and scenario.',
                                    'missing_input_keys':tuple(dict.fromkeys((*d.missing_input_keys, 'two_call_unbound_evidence'))),
                                }))
                        elif not cited <= sent_refs|context_refs:
                            raise TwoCallError(subject='evidence_foreign')
                        evidence = list(d.evidence_refs)
                        if d.status == 'known':
                            events = [f.frame_id for f in request.interpretation.semantic_frames
                                      if f.frame_id in scenario.semantic_frame_refs and set(f.evidence_refs)&set(d.evidence_refs)]
                            if not events:
                                if owner.goal_engine.binding_version not in {'goal-formation-binding-v0.3', 'goal-formation-binding-v0.4'}:
                                    raise TwoCallError(subject='accepted_event_missing')
                                # Validate the rating's shape before reducing its
                                # certainty. Missing event support is recoverable;
                                # an invalid scale/basis still fails closed.
                                kind = DIMENSIONS.get(d.dimension_key, (None,))[0]
                                if (kind is None or d.value not in SCALES[kind]
                                    or d.assessment_basis not in {'observed', 'inferred'}
                                    or d.uncertainty == 'high'):
                                    raise TwoCallError(subject='canonical_appraisal_validation')
                                d = DimensionProposal(**(d.model_dump(mode='python') | {
                                    'status':'unknown', 'value':None, 'assessment_basis':'insufficient',
                                    'uncertainty':'high',
                                    'evidence_summary':'Supplied context lacks a cited accepted event for this rating.',
                                    'missing_input_keys':tuple(dict.fromkeys((*d.missing_input_keys, 'two_call_missing_accepted_event'))),
                                }))
                            else:
                                evidence.extend((*events,goal.goal_id))
                        dims.append(type(d)(**(d.model_dump(mode='python') | {'evidence_refs':tuple(dict.fromkeys(evidence))})))
                    compiled.append(FrameProposal(scenario_ref=scenario.scenario_id, goal_ref=goal.goal_id, dimensions=tuple(dims)))
            provenance = owner.model.provenance | {'orchestration':VERSION, 'source_call':digest(owner.one)}
            if owner.goal_engine.binding_version != 'goal-formation-binding-v0.1':
                provenance['goal_binding_version'] = owner.goal_engine.binding_version
            try:
                result = compile_proposal(request,payload,AppraisalModelOutput(frames=tuple(compiled)),provenance)
            except AppraisalModelError as exc:
                raise TwoCallError(subject='canonical_appraisal_validation') from exc
            return result, ()  # Provider work belongs to call_one; never double-count it.
        except AppraisalModelError as exc:
            exc.spans = tuple(owner.recorder.spans)
            raise
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            error = TwoCallError(subject='appraisal_binding')
            error.spans = tuple(owner.recorder.spans)
            raise error from exc

class PayloadCollector:
    provenance = None
    def __init__(self):
        self.requests = []
        self.goal_ref = None
    def generate(self, payload):
        self.requests.append(dict(goal_ref=self.goal_ref,payload=payload.model_dump(mode='json')))
        raise CollaborationProviderError(CollaborationFailureCode.PROVIDER_NOT_CONFIGURED)

class CollectingEngine(CollaborativeContentEngine):
    def __init__(self, collector):
        super().__init__(collector)
    def _one(self, goal, **kwargs):
        self.model.goal_ref = goal.goal_ref
        return super()._one(goal,**kwargs)

class ContentAdapter:
    provenance = None
    def __init__(self, requests, outputs):
        self.requests = requests
        self.outputs = {item.request_handle:item.output for item in outputs}
        if len(self.outputs) != len(outputs) or set(self.outputs) != set(range(len(requests))):
            raise TwoCallError(stage='call_two',subject='content_coverage')
        self.cursor = 0
    def generate(self, payload):
        if self.cursor >= len(self.requests) or digest(payload) != digest(self.requests[self.cursor]['payload']):
            raise TwoCallError(stage='call_two',subject='content_payload')
        value = self.outputs[self.cursor]
        self.cursor += 1
        return value

class TwoCallOrchestrator:
    def __init__(self, model, protocol="legacy-v0.1"):
        self.model = model
        self.protocol = model.trace.protocol if isinstance(model,FrozenTwoCallModel) else protocol
        from .compact import ReferenceTable
        self.references=ReferenceTable()
        self.mind=None
        self.shared_context=None
        self.recorder = SpanRecorder()
        self.request = None
        self.one = None
        self.two = None
        self.captures = []
        self.contribution_captures = []
        self.dialogue_context = None
        self.context_projection = 'two-call-stage-context-v0.3'
        self.behavior_policy = 'model_grounded_selection-v0.1'
        self.realization_policy = 'segmented_response-v0.2' if self.protocol!='legacy-v0.1' else 'style_advisory-v0.1'
        self.alignment_policy = 'compiled_move_spans-v0.1'
        self.understanding = None
        self.policy = None
        self.policy_references = None
        self.collaboration = None
        self.understanding_objects = ()
        self.understanding_descriptors = ()
        from .v1_contracts import ARGUMENT_KIND_REGISTRY_VERSION
        self.understanding_registry = ARGUMENT_KIND_REGISTRY_VERSION
        if self.protocol==V1 and isinstance(model,FrozenTwoCallModel):
            # Replay rebuilds the exact input version the capture was made with.
            self.understanding_registry = model.trace.calls[0].payload['ontology']['predicate_registry_version']
        if self.protocol==V1:
            from .v1_contracts import BEHAVIOR_POLICY_V1,REALIZATION_POLICY_V1,ALIGNMENT_POLICY_V1
            self.behavior_policy,self.realization_policy,self.alignment_policy = BEHAVIOR_POLICY_V1,REALIZATION_POLICY_V1,ALIGNMENT_POLICY_V1
        if isinstance(model,FrozenTwoCallModel):
            self.behavior_policy = model.trace.behavior_policy
            self.realization_policy = model.trace.realization_policy
            self.alignment_policy = model.trace.alignment_policy
            self.context_projection = model.trace.calls[1].payload.get('context_projection', 'two-call-stage-context-v0.1')
            raw = model.trace.calls[1].dialogue_context or model.trace.calls[1].payload.get("dialogue_context")
            if raw is not None:
                from ..contracts.dialogue_memory import DialogueTurnContext
                self.dialogue_context = DialogueTurnContext.model_validate(raw)

    def begin(self, request):
        if self.request is not None:
            raise TwoCallError(subject='orchestrator_reused')
        self.request = request
        if request.dialogue_context is not None:
            context = self.dialogue_context
            if context is None or context.context_id != request.dialogue_context.turn_context_ref:
                raise TwoCallError(subject='dialogue_context_missing')
            if (context.cognitive_turn_id,context.conversation_ref,context.focus_message_ref) != (
                request.cognitive_turn_id,request.conversation_ref,request.focus_message_id):
                raise TwoCallError(subject='dialogue_context_ownership')

    def call(self, stage, payload):
        self.recorder.stage = stage
        try:
            if len(str(payload)) > 180000:
                raise TwoCallError(stage=stage,subject='payload_budget')
            with self.recorder.measure('provider_attempt', attempt_number=1, skipped=isinstance(self.model,FrozenTwoCallModel)):
                output = self.model.generate(stage,payload)
            usage = self.model.usage
            if usage:
                span = self.recorder.spans[-1]
                self.recorder.spans[-1] = LatencySpan(**(span.model_dump(mode='python')|usage))
            provenance=dict(self.model.provenance)
            table=self.policy_references if self.protocol==V1 and stage=='call_two' else self.references
            if self.protocol==V1:
                from .v1_contracts import UNDERSTANDING_INPUT_VERSION,POLICY_INPUT_VERSION
                from .contribution import contribution_input_version
                provenance.update(protocol_version=self.protocol,
                    context_version=(contribution_input_version(self) if stage=='contribution'
                                     else UNDERSTANDING_INPUT_VERSION if stage=='call_one' else POLICY_INPUT_VERSION),
                    schema_version=f'{stage}-v1',
                    prompt_version=(('contribution-v1.8' if contribution_input_version(self).endswith('v1.1') else 'contribution-v1.6')
                        if stage == 'contribution'
                        else 'call_one-v1.8' if stage == 'call_one' and self.understanding_registry == 'argument-kind-registry-v1.8'
                        else 'call_one-v1.7' if stage == 'call_one' and self.understanding_registry == 'argument-kind-registry-v1.7'
                        else 'call_one-v1.6' if stage == 'call_one' and self.understanding_registry == 'argument-kind-registry-v1.6'
                        else 'call_two-v1.5' if stage == 'call_two' and self.understanding_registry in (
                            'argument-kind-registry-v1.6', 'argument-kind-registry-v1.7', 'argument-kind-registry-v1.8')
                        else f'{stage}-v1.5' if self.understanding_registry == 'argument-kind-registry-v1.5'
                        else f'{stage}-v1.4' if stage == 'call_one' and self.understanding_registry == 'argument-kind-registry-v1.4'
                        else f'{stage}-v1.3' if stage == 'call_two' and self.understanding_registry in ('argument-kind-registry-v1.3', 'argument-kind-registry-v1.4')
                        else f'{stage}-v1.2' if self.understanding_registry in ('argument-kind-registry-v1.2', 'argument-kind-registry-v1.3')
                        else f'{stage}-v1'))
            elif self.protocol!='legacy-v0.1':
                from .provider import compact_prompt_version
                version=compact_prompt_version(stage,payload).replace('_','.')
                provenance.update(protocol_version=self.protocol,
                    context_version='turn-context-v0.1' if stage=='call_one' else payload['decision_context']['version'],
                    schema_version=stage+'-compact-v0.1',prompt_version=f'{stage}-compact-{version}')
            contribution = stage == 'contribution'
            (self.contribution_captures if contribution else self.captures).append(CallCapture(stage=stage,prompt_hash=hashlib.sha256(prompt(stage, payload).encode()).hexdigest(),
                payload_digest=digest(payload),payload=payload,proposal=output.model_dump(mode='json'),provenance=provenance,
                protocol=self.protocol,reference_table=table.entries if self.protocol!='legacy-v0.1' and not contribution else None,
                dialogue_context=self.dialogue_context.model_dump(mode='json') if self.protocol!='legacy-v0.1' and self.dialogue_context is not None else None,
                attribution=self.attribution(stage,payload,output) if self.protocol!='legacy-v0.1' else None))
            return output
        except AppraisalModelError as exc:
            exc.orchestration_stage = stage
            exc.spans = tuple(self.recorder.spans)
            raise

    def contribute(self, *, kind, frame, query):
        """Registry v1.6: one recorded contribution call compiled to a producer result."""
        from .contribution import build_payload, compile_result
        stage = self.recorder.stage
        try:
            payload = build_payload(self, kind=kind, frame=frame, query=query)
            output = self.call('contribution', payload)
            return compile_result(output, kind=kind, query=query, request=self.request,
                                  capture_digest=self.contribution_captures[-1].payload_digest)
        finally:
            self.recorder.stage = stage

    def record_compilation(self,output):
        capture=self.captures[-1]
        self.captures[-1]=CallCapture(**(capture.model_dump(mode='python') |
            dict(compiled_proposal=output.model_dump(mode='json'))))

    def attribution(self,stage,payload,output):
        from .provider import canonical,response_schema
        sections={k:len(canonical(v)) for k,v in payload.items()}
        if stage=='call_two' and 'decision_context' in payload:
            sections.update({'decision_context.'+k:len(canonical(v)) for k,v in payload['decision_context'].items()})
        return dict(unit='unicode_characters',tokenizer=None,token_estimation='characters_divided_by_4_rough_estimate',
            estimated_input_tokens=(len(canonical(payload))+len(prompt(stage,payload))+len(canonical(response_schema(stage,payload))))/4,
            estimated_output_tokens=len(canonical(output))/4,first_token_ms=None,
            payload_sections=sections,
            output_sections={k:len(canonical(v)) for k,v in output.model_dump(mode='json').items()},
            prompt_characters=len(prompt(stage,payload)),schema_characters=len(canonical(response_schema(stage,payload))))

    def prepare(self, pipeline, turn, appraisal, update, *, interpretation, goals, workspace, requirement):
        self.recorder.stage = 'call_two'
        if self.protocol==V1:
            return self._prepare_v1(pipeline,turn,appraisal,update,interpretation=interpretation,goals=goals,
                                    workspace=workspace,requirement=requirement)
        with self.recorder.measure('payload_compile'):
            payload,requests = self._prepare_inputs(pipeline,turn,appraisal,update,
                interpretation=interpretation,goals=goals,workspace=workspace,requirement=requirement)
        self.two = self.call('call_two',payload)
        if self.protocol!='legacy-v0.1':
            from .compact import compile_response,response_failure_code
            try:
                with self.recorder.measure('response_compile'):
                    self.two=compile_response(self.two,payload,self.references.entries)
                    self.record_compilation(self.two)
            except ValueError as exc:
                error=TwoCallError(stage='call_two',subject='response_reference_compilation.'+response_failure_code(exc))
                error.spans=tuple(self.recorder.spans)
                raise error from exc
        with self.recorder.measure('response_compile'):
            return self._compile_content(pipeline,turn,interpretation,goals,workspace,requirement,requests)

    def _prepare_v1(self, pipeline, turn, appraisal, update, *, interpretation, goals, workspace, requirement):
        """Content subsystem first, then Call 2 selects among fully bound candidates."""
        from ..cognitive_turn.models import CognitiveTurnRequest
        from .v1 import build_policy_input,compile_policy
        request = self.request
        # The configured collaborative-content engine (its own provider, or a
        # typed failure) runs before the cognitive call; Call 2 writes no content.
        with self.recorder.measure('content_preparation'):
            collaboration = pipeline._collaborate(request,interpretation,goals,workspace,requirement)
        self.collaboration = collaboration
        from .v1_grounding import project_understanding
        self.understanding_objects, self.understanding_descriptors = project_understanding(
            interpretation, turn_id=request.cognitive_turn_id, registry=self.understanding_registry)
        # Understanding objects sit between the requirement and the collaboration
        # projections; collaboration objects stay last (trace invariant).
        enriched = CognitiveTurnRequest(**{name:getattr(turn,name) for name in CognitiveTurnRequest.model_fields} |
            dict(action_grounded_objects=(*request.action_grounded_objects,*requirement.grounded_objects,
                                          *self.understanding_objects,*collaboration.grounded_objects),
                 action_target_descriptors=(*turn.action_target_descriptors,*self.understanding_descriptors),
                 collaboration_handoff=collaboration.handoff))
        try:
            with self.recorder.measure('payload_compile'):
                payload = build_policy_input(self,pipeline,enriched,appraisal,update,interpretation,goals,requirement,
                                             collaboration,action_request_for(enriched,appraisal,update.snapshot))
        except ValueError as exc:
            error = TwoCallError(stage='call_two',subject='policy_input.'+v1_failure_code(exc))
            error.spans = tuple(self.recorder.spans)
            raise error from exc
        raw = self.call('call_two',payload)
        try:
            with self.recorder.measure('response_compile'):
                self.policy = compile_policy(raw,payload,self.policy_references.entries)
                self.record_compilation(self.policy)
        except ValueError as exc:
            error = TwoCallError(stage='call_two',subject='policy_compilation.'+v1_failure_code(exc))
            error.spans = tuple(self.recorder.spans)
            raise error from exc
        self.two = self.policy
        return enriched,collaboration

    def select_policy_choice(self, step, candidates, eligible, restricted, plan_id, moves, analyses):
        """Bind the model's ordered selection to this step's gated candidates by identity."""
        try:
            selected = self.policy.selected_identities
            if step >= len(selected):
                from ..collaborative_content_v0.projection import artifact_object_id
                required = {artifact_object_id(o.proposal) for o in self.collaboration.outcomes if o.status == 'proposed'}
                delivered = {t.ref for m in moves if m.definition_ref.action_key == 'collaborate.deliver_artifact'
                             for t in m.target_refs if t.target_type.value == 'artifact_revision'}
                # The model may stop once it delivered some prepared content or
                # asked the user something; only ignoring all of it is rejected.
                asked = any(m.family.value == 'seek_information' for m in moves)
                if not required <= delivered and not delivered and not asked:
                    raise ValueError('model STOP omitted required content delivery')
                return None
            identity = selected[step]
            matches = [c for c in eligible if c.lexical_identity == identity]
            if not matches:
                if any(c.lexical_identity == identity for c in candidates):
                    raise ValueError('selected candidate is not eligible at its step')
                raise ValueError('selected candidate is unavailable at its step')
            if len(matches) != 1:
                raise ValueError('model behavior is ambiguous')
            if matches[0].candidate_id in set(restricted):
                raise ValueError('model behavior skips a blocking planning condition')
            return matches[0].candidate_id
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise TwoCallError(stage='call_two', subject='behavior_proposal_ineligible.' + _behavior_detail(exc, step)) from exc

    def select_policy_primary(self, moves, compared):
        from ..contracts.actions import decimal_from
        index = self.policy.primary_index
        if index is None or index >= len(moves):
            raise ValueError('model primary outside the plan')
        best = max(decimal_from(item['direct_focal_coverage']) for item in compared)
        if (self.behavior_policy == 'model_policy_selection-v1'
                and decimal_from(compared[index]['direct_focal_coverage']) != best):
            raise ValueError('model primary lacks the greatest direct focal coverage')
        return moves[index].move_id

    def validate_policy_plan(self, result):
        from .v1 import move_identity
        moves = result.plan.moves
        primary = result.plan.primary_move_ref
        if (tuple(move_identity(m) for m in moves) != tuple(self.policy.selected_identities)
                or (self.policy.primary_index is None) != (primary is None)
                or (primary is not None and moves[self.policy.primary_index].move_id != primary)):
            error = TwoCallError(stage='call_two',subject='behavior_plan_disagreement')
            error.spans = tuple(self.recorder.spans)
            raise error

    def _prepare_inputs(self, pipeline, turn, appraisal, update, *, interpretation, goals, workspace, requirement):
        request = self.request
        if self.protocol!='legacy-v0.1':
            from .compact import decision_packet
            return decision_packet(self,pipeline,turn,appraisal,update,interpretation,goals,workspace,requirement)
        collector = PayloadCollector()
        pipeline.collaboration_engine = CollectingEngine(collector)
        pipeline._collaborate(request,interpretation,goals,workspace,requirement)
        # The preview is data only. An empty preview never supplies replacement
        # behavior after the call; actual accepted plan must agree exactly.
        preview = None
        preview_result = None
        preview_realization = None
        try:
            action_request = action_request_for(turn,appraisal,update.snapshot)
            result = pipeline.cognitive_turn_runner.action_engine.select(action_request)
            preview_result = result
            preview = result.plan.model_dump(mode='json')
            from ..realization_v0.inventory import realization_inventory
            from ..realization_v0.compiler import RealizationCompilerV0, RealizationCompileSkip
            from ..realization_v0.model_protocol import project_payload
            inventory = realization_inventory(result, self.dialogue_context,
                                               requirement.realization_content)
            focus = next(m.content for m in request.messages if m.message_id==request.focus_message_id)
            compiled = RealizationCompilerV0().compile(action_result=result,inventory=inventory,
                personality=turn.action_personality,state=update.snapshot,language_tag=request.language_tag,
                current_user_utterance=focus,safety_basis=result.safety,configured_maximum_characters=4000)
            if not isinstance(compiled,RealizationCompileSkip):
                preview_realization = project_payload(compiled).model_dump(mode='json')
        except (ActionSelectionError, RealizationExecutionError):
            # A preview may have no viable move or lack required content;
            # those failures do not authorize a substituted response.
            pass
        from ..action_selection_v0.registry import DEFAULT_REGISTRY
        from ..realization_v0.action_manifest import MANIFEST
        from ..action_selection_v0.construction import BINDING_MANIFEST, recipe_for
        from dataclasses import asdict
        # Versioned stage projection: code-constructible actions for the
        # current plan, plus content-dependent recipe targets when applicable.
        allowed_keys = {c.definition_ref.action_key for step in preview_result.steps for c in step.constructed_candidates} if preview_result else set()
        compact = self.context_projection in {'two-call-stage-context-v0.2', 'two-call-stage-context-v0.3'}
        if not compact and (collector.requests or not allowed_keys):
            allowed_keys.update(r.action_key for r in BINDING_MANIFEST)
        elif compact and collector.requests:
            from ..contracts.actions import ActionTargetType as T
            # Only target types that accepted collaboration can add. Preview
            # candidates already cover all currently grounded objects.
            future_types = {T.ARTIFACT_REVISION, T.PROPOSITION, T.INQUIRY, T.OPTION, T.EVIDENCE_RECORD}
            allowed_keys.update(r.action_key for r in BINDING_MANIFEST if set(r.target_types) & future_types)
        definitions = [d for d in DEFAULT_REGISTRY.definitions if d.lifecycle.value=='shadow' and d.definition_ref.action_key in allowed_keys]
        payload = dict(version=VERSION,dialogue_context=self.dialogue_context.model_dump(mode='json') if self.dialogue_context else None,
            interpretation=interpretation.model_dump(mode='json',include={'cognitive_turn_id','interpretation_trace_id','semantic_frames','scenarios','unknowns','contradictions','versions'}),
            appraisal=dict(appraisal_trace_id=appraisal.appraisal_trace_id,
                frames=[dict(frame_id=f.frame_id,scenario_id=f.scenario_id,subject=f.subject.model_dump(mode='json'),
                             dimensions=[d.model_dump(mode='json',include={'dimension_key','status','value_type','value','evidence_refs','derivation_strength'}) for d in f.dimensions]) for f in appraisal.frames],
                aggregate=appraisal.aggregate.model_dump(mode='json')),
            post_update_state=update.snapshot.model_dump(mode='json'),
            personality=turn.action_personality.model_dump(mode='json'),
            goal_snapshot=goals.model_dump(mode='json'),workspace=workspace.model_dump(mode='json'),
            requirement=requirement.model_dump(mode='json'),content_requests=collector.requests,
            allowed_action_definitions=[d.model_dump(mode='json',include={'definition_ref','family','function','description','target_schema','parameter_schema','evidence_specification','structural_requirements','hard_inhibitors'}) for d in definitions],
            construction_recipes=[asdict(recipe_for(d.definition_ref.action_key)) for d in definitions],
            action_runtime=turn.action_runtime.model_dump(mode='json'),
            known_grounded_objects=[o.model_dump(mode='json') for o in turn.action_grounded_objects],
            deterministic_preview_plan=preview,deterministic_preview_realization=preview_realization,
            realization_manifest={k:v.model_dump(mode='json') for k,v in MANIFEST.items() if k in allowed_keys},
            context_projection=self.context_projection,
            messages=[{'ref':m.message_id,'role':m.role.value,'text':m.content} for m in request.messages],
            behavior_policy=self.behavior_policy)
        if self.alignment_policy == 'compiled_move_spans-v0.1':
            payload['alignment_policy'] = self.alignment_policy
        if self.context_projection == 'two-call-stage-context-v0.3':
            from ..action_selection_v0.reference_index import ActionReferenceIndex
            from ..action_selection_v0.features import FeatureCalculator
            grounded = []
            if preview_result:
                first = preview_result.steps[0]
                calculator = FeatureCalculator(DEFAULT_REGISTRY, ActionReferenceIndex(action_request))
                eligible = {g.candidate_ref for g in first.gate_results if g.eligible_in_shadow}
                for candidate in first.constructed_candidates:
                    if candidate.candidate_id not in eligible or candidate.candidate_id in first.condition_restricted_candidate_refs:
                        continue
                    analysis = calculator.analyze(candidate, first.partial_state_before)
                    grounded.append(dict(action_key=candidate.definition_ref.action_key,
                        goal_refs=sorted({link.goal_ref for link in analysis.goal_links}),
                        targets=[t.model_dump(mode='json') for t in candidate.target_refs],
                        parameters=dict(candidate.parameters)))
            payload['current_behavior_candidates'] = grounded
        if compact:
            payload['allowed_action_definitions'] = [d.model_dump(mode='json', include={
                'definition_ref','family','function','description','target_schema','parameter_schema'}) for d in definitions]
            # Preserve binding and wording constraints without repeating
            # the entire action / static realization registries.
            payload['realization_manifest'] = {k:v.model_dump(mode='json', include={
                'allowed_content_kinds','question_required','assertion_permission','required_alignment_kind',
                'required_bindings','generic_roles','generic_reference_kinds','minimum_content_items',
                'maximum_content_items','style_permissions','mandatory_constraint_codes','additional_constraint_codes'}) for k,v in MANIFEST.items() if k in allowed_keys}
            for entry in payload['content_requests']:
                operation = entry['payload']['operation']
                if self.context_projection == 'two-call-stage-context-v0.3':
                    entry['delivery_binding'] = dict(action_key='collaborate.deliver_artifact',
                        goal_refs=[entry['goal_ref']], target=dict(role='primary', target_type='artifact_revision',
                        existing_ref=None, revision_ref=None, content_request_handle=payload['content_requests'].index(entry),
                        object_kind='artifact', item_handle=None))
                entry['compiler_requirements'] = dict(
                    patch_required=operation in {'create','revise'}, content_required=True,
                    source_refs_required=True, inquiries_replace_content=False,
                    anchors='One to six exact terms from each content item statement; candidate wording must retain these terms.',
                    incorporate_current_user_requirements=True,
                    subjects='Existing artifact element IDs or element IDs written by this patch only.',
                    numbers='Only numbers represented by the accepted artifact.',
                )
        return payload,collector.requests

    def _compile_content(self,pipeline,turn,interpretation,goals,workspace,requirement,requests):
        request = self.request
        adapter = ContentAdapter(requests,self.two.content_proposal)
        pipeline.collaboration_engine = CollaborativeContentEngine(adapter)
        collaboration = pipeline._collaborate(request,interpretation,goals,workspace,requirement)
        failures = [o.failure for o in collaboration.outcomes if o.status=='failed']
        if adapter.cursor != len(requests) or failures:
            subject = 'content_compilation'
            if failures:
                subject += '.' + failures[0].stage + '.' + failures[0].code.value
                if failures[0].code.value=='patch_rejected':
                    from ..collaborative_content_v0.compiler import patch_rejection_detail
                    subject += '.' + patch_rejection_detail(failures[0].subject)
            else:
                subject += '.request_not_consumed'
            error = TwoCallError(stage='call_two',subject=subject)
            error.spans = tuple(self.recorder.spans)
            raise error
        self.collaboration = collaboration
        self.content_requests = requests
        from ..cognitive_turn.models import CognitiveTurnRequest
        enriched = CognitiveTurnRequest(**{name:getattr(turn,name) for name in CognitiveTurnRequest.model_fields} |
            dict(action_grounded_objects=(*request.action_grounded_objects,*requirement.grounded_objects,*collaboration.grounded_objects),
                 collaboration_handoff=collaboration.handoff))
        return enriched,collaboration

    def resolve_target(self, target, plan_ref=None):
        from ..contracts.actions import ActionTargetRef
        from ..collaborative_content_v0.projection import artifact_object_id, content_object_id
        if target.existing_ref is not None:
            if any(v is not None for v in (target.content_request_handle,target.object_kind,target.item_handle)):
                raise ValueError('mixed existing/local target')
            return ActionTargetRef(role=target.role,target_type=target.target_type,
                                   ref=target.existing_ref,revision_ref=target.revision_ref)
        if target.object_kind == 'partial_plan':
            if target.content_request_handle is not None or target.item_handle is not None or target.revision_ref is not None or plan_ref is None:
                raise ValueError('invalid local partial plan binding')
            return ActionTargetRef(role=target.role,target_type=target.target_type,ref=plan_ref)
        if target.revision_ref is not None or target.content_request_handle is None or target.object_kind is None:
            raise ValueError('unresolved local target')
        handle = target.content_request_handle
        if handle < 0 or handle >= len(self.content_requests):
            raise ValueError('foreign content request')
        goal = self.content_requests[handle]['goal_ref']
        outcome = next(o for o in self.collaboration.outcomes if o.goal_ref==goal)
        proposal = outcome.proposal
        if proposal is None:
            raise ValueError('unaccepted content')
        revision = proposal.proposed_revision
        revision_ref = revision.revision_id if revision else proposal.target_revision_ref
        if target.object_kind=='artifact':
            if target.item_handle is not None:
                raise ValueError('artifact has no item handle')
            ref = artifact_object_id(proposal)
        elif target.object_kind=='content':
            if target.item_handle is None or target.item_handle<0:
                raise ValueError('content requires local item handle')
            ref = content_object_id(proposal.content_items[target.item_handle].item_id)
            revision_ref = None
        else:
            if target.item_handle is None or target.item_handle<0:
                raise ValueError('inquiry requires local item handle')
            raw = next(p.output for p in self.two.content_proposal if p.request_handle==handle).inquiries[target.item_handle]
            matches = [i for i in proposal.inquiry_opportunities if i.target_ref==raw.target_id
                       and i.purpose.value==raw.purpose and i.asked_about==raw.asked_about]
            if len(matches)!=1:
                raise ValueError('inquiry binding ambiguous')
            ref = matches[0].inquiry_id
            revision_ref = None
        return ActionTargetRef(role=target.role,target_type=target.target_type,ref=ref,revision_ref=revision_ref)

    def matches_choice(self, candidate, proposal, plan_id, linked_goals=()):
        targets = tuple(self.resolve_target(t, plan_id) for t in proposal.target_bindings)
        if len(set(targets)) != len(targets):
            raise ValueError('duplicate model targets')
        canonical = set(candidate.target_refs)
        goals = {t.ref for t in canonical if t.target_type.value == 'goal'}
        if len(set(proposal.goal_refs)) != len(proposal.goal_refs):
            raise ValueError('duplicate model goal refs')
        if self.behavior_policy == 'strict_plan_agreement-v0.1':
            return (candidate.definition_ref.action_key == proposal.action_key
                and goals == set(proposal.goal_refs) and set(targets) == canonical)
        return (candidate.definition_ref.action_key == proposal.action_key
            and goals <= set(proposal.goal_refs)
            and set(proposal.goal_refs) <= goals | set(linked_goals)
            and {t for t in targets if t.target_type.value != 'goal'} == {t for t in canonical if t.target_type.value != 'goal'}
            and all(t in canonical or t.ref in set(linked_goals) for t in targets if t.target_type.value == 'goal'))

    def select_choice(self, step, candidates, eligible, restricted, plan_id, moves, analyses):
        try:
            if step >= len(self.two.behavior_proposal):
                from ..collaborative_content_v0.projection import artifact_object_id
                required = {artifact_object_id(o.proposal) for o in self.collaboration.outcomes if o.status == 'proposed'}
                delivered = {t.ref for m in moves if m.definition_ref.action_key == 'collaborate.deliver_artifact'
                             for t in m.target_refs if t.target_type.value == 'artifact_revision'}
                if not required <= delivered:
                    raise ValueError('model STOP omitted required content delivery')
                return None
            proposal = self.two.behavior_proposal[step]
            matches = [c for c in eligible if c.candidate_id not in set(restricted)
                       and self.matches_choice(c, proposal, plan_id, {link.goal_ref for link in analyses[c.candidate_id].goal_links})]
            if len(matches) != 1:
                if matches:
                    raise ValueError('model behavior is ambiguous')
                if any(c.candidate_id in set(restricted) and self.matches_choice(c, proposal, plan_id,
                       {link.goal_ref for link in analyses[c.candidate_id].goal_links}) for c in eligible):
                    raise ValueError('model behavior skips a blocking planning condition')
                if not any(c.definition_ref.action_key == proposal.action_key for c in eligible):
                    raise ValueError('model behavior is not eligible at this step')
                raise ValueError('model behavior goals or targets do not match')
            return matches[0].candidate_id
        except (ValueError, IndexError, KeyError, TypeError, StopIteration) as exc:
            raise TwoCallError(stage='call_two', subject='behavior_proposal_ineligible.' + _behavior_detail(exc, step)) from exc

    def validate_plan(self, result):
        if self.protocol==V1:
            return self.validate_policy_plan(result)
        try:
            moves = result.plan.moves
            proposed = self.two.behavior_proposal
            if len(moves)!=len(proposed):
                raise ValueError('move coverage')
            for step,move,proposal in zip(result.steps,moves,proposed):
                linked_goals = {link.goal_ref for link in step.transition_basis.goal_links}
                if not self.matches_choice(move, proposal, result.plan.plan_id, linked_goals):
                    raise ValueError('behavior plan disagreement')
        except (ValueError,IndexError,KeyError,TypeError,StopIteration) as exc:
            error = TwoCallError(stage='call_two',subject='behavior_plan_disagreement')
            error.spans = tuple(self.recorder.spans)
            raise error from exc

    def trace(self):
        return TwoCallTrace(protocol=self.protocol,calls=tuple(self.captures),spans=tuple(self.recorder.spans),
            contributions=tuple(self.contribution_captures),
            behavior_policy=self.behavior_policy, realization_policy=self.realization_policy,
            alignment_policy=self.alignment_policy,
            goal_binding_version=self.goal_engine.binding_version,
            goal_bindings=tuple({'goal_ref':key,'frame_refs':list(refs)} for key,refs in sorted(self.goal_engine.formation_bindings.items())))

def action_request_for(turn,appraisal,state):
    return ActionSelectionRequest(cognitive_turn_id=turn.cognitive_turn_id,conversation_ref=turn.conversation_ref,
        core_memory_ref=turn.core_memory_ref,interpretation=turn.interpretation,goal_snapshot=turn.goal_snapshot,
        active_goals=turn.active_goals,appraisal=appraisal,dynamic_state=state,runtime=turn.action_runtime,
        personality=turn.action_personality,grounded_objects=turn.action_grounded_objects,
        target_descriptors=turn.action_target_descriptors,prior_action_records=turn.prior_action_records,
        prior_realization_records=turn.prior_realization_records,observed_outcomes=turn.action_observed_outcomes,
        requirement_handoff=turn.requirement_handoff,collaboration_handoff=turn.collaboration_handoff)

# Closed, content-free reasons. Never put model text or references in the code.
_BEHAVIOR_DETAILS = {
    'model STOP omitted required content delivery': 'stop_omitted_delivery',
    'model STOP leaves an unresolved planning condition': 'stop_leaves_planning_condition',
    'model behavior is ambiguous': 'ambiguous_candidate',
    'model behavior skips a blocking planning condition': 'skips_blocking_condition',
    'model behavior is not eligible at this step': 'action_not_eligible_at_step',
    'model behavior goals or targets do not match': 'goal_or_target_mismatch',
    'model choice is not an eligible unrestricted candidate': 'choice_restricted',
    'selected candidate is not eligible at its step': 'candidate_ineligible_at_step',
    'selected candidate is unavailable at its step': 'candidate_unavailable_at_step',
    'model primary outside the plan': 'primary_outside_plan',
    'model primary lacks the greatest direct focal coverage': 'primary_not_focal',
}

def _behavior_detail(exc, step):
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        # Code-owned contract messages, matched only to pick a closed token.
        detail = 'move_without_positive_utility' if 'strictly positive score' in str(exc) else 'move_contract_invalid'
        return detail if step is None else f'{detail}.step_{step}'
    detail = _BEHAVIOR_DETAILS.get(str(exc), 'target_unresolved' if isinstance(exc, ValueError) else 'action_selection_failed')
    return detail if step is None else f'{detail}.step_{step}'

class ProposedActionEngine:
    def __init__(self, engine, owner):
        self.engine, self.owner = engine, owner
    def select(self, request):
        if self.owner.two is None or self.owner.behavior_policy == 'strict_plan_agreement-v0.1':
            return self.engine.select(request)
        if self.owner.protocol == V1:
            from ..contracts.actions import MODEL_POLICY_PLANNER_VERSION, MODEL_PRIMARY_RULE_ID, MODEL_PRIMARY_CHOICE_RULE_ID
            try:
                with self.owner.recorder.measure('plan_compilation'):
                    return self.engine.select(request, proposal_selector=self.owner.select_policy_choice,
                        primary_selector=self.owner.select_policy_primary, planner_version=MODEL_POLICY_PLANNER_VERSION,
                        primary_rule_id=(MODEL_PRIMARY_CHOICE_RULE_ID if self.owner.behavior_policy == 'model_policy_selection-v1.1'
                                         else MODEL_PRIMARY_RULE_ID),
                        source_call_digest=digest(self.owner.captures[1].proposal))
            except ActionSelectionError as exc:
                cause = exc.__cause__
                subject = cause.subject if isinstance(cause, TwoCallError) else 'behavior_proposal_ineligible.' + _behavior_detail(cause, None)
                error = TwoCallError(stage='call_two', subject=subject)
                error.spans = tuple(self.owner.recorder.spans)
                raise error from exc
        try:
            return self.engine.select(request, proposal_selector=self.owner.select_choice,
                                      source_call_digest=digest(self.owner.captures[1].proposal))
        except ActionSelectionError as exc:
            cause = exc.__cause__
            subject = cause.subject if isinstance(cause, TwoCallError) else 'behavior_proposal_ineligible.' + _behavior_detail(cause, None)
            error = TwoCallError(stage='call_two', subject=subject)
            error.spans = tuple(self.owner.recorder.spans)
            raise error from exc


def install(pipeline, model, *, protocol="legacy-v0.1"):
    owner = TwoCallOrchestrator(model,protocol)
    pipeline.goal_engine = BindingGoalEngine(identity_factory=pipeline.goal_engine.identity_factory)
    owner.goal_engine = pipeline.goal_engine
    if isinstance(model, FrozenTwoCallModel):
        owner.goal_engine.binding_version = model.trace.goal_binding_version
    pipeline.two_call = owner
    pipeline.cognitive_turn_runner.action_engine = ProposedActionEngine(pipeline.cognitive_turn_runner.action_engine, owner)
    pipeline.interpretation_engine = InterpretationEngine(grounder=ModelSemanticGrounder(SemanticAdapter(owner)))
    pipeline.cognitive_turn_runner.appraisal_engine = AppraisalAdapter(owner)
    if owner.protocol==V1:
        if owner.understanding_registry in ('argument-kind-registry-v1.4', 'argument-kind-registry-v1.5', 'argument-kind-registry-v1.6',
                                            'argument-kind-registry-v1.7', 'argument-kind-registry-v1.8'):
            from .continuity import ContinuityRequirementEngine
            pipeline.requirement_engine = ContinuityRequirementEngine(owner, pipeline.requirement_engine)
        from .compact_runtime import ContextInterpretationEngine,ModelBiasOwner,UnderstandingGrounder,UnderstandingReadingRules
        pipeline.interpretation_engine=ContextInterpretationEngine(owner,
            grounder=UnderstandingGrounder(owner,SemanticAdapter(owner)),
            rule_engine=UnderstandingReadingRules(owner),bias_policy=ModelBiasOwner())
    elif owner.protocol!="legacy-v0.1":
        from .compact_runtime import ContextInterpretationEngine,ContextReadingRules,ModelBiasOwner,ContextGrounder
        pipeline.interpretation_engine=ContextInterpretationEngine(owner,
            grounder=ContextGrounder(owner,SemanticAdapter(owner)),
            rule_engine=ContextReadingRules(owner),bias_policy=ModelBiasOwner())
    return pipeline
