"""Hermetic combined-call contracts: doubles establish wiring, not model quality."""
import copy
import pytest
from app.ai.Cognitive.system.v0.two_call.contracts import CallOneOutput, CallTwoOutput
from app.ai.Cognitive.system.v0.two_call.provider import FrozenTwoCallModel, OpenAITwoCallModel
from app.ai.Cognitive.system.v0.two_call.orchestrator import install, TwoCallError
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineV0, CognitivePipelineExecutionError
from app.ai.Cognitive.system.v0.appraisal.model_contracts import DIMENSIONS
from app.ai.Cognitive.system.v0.live_shadow.digests import semantic_digest
from app.ai.Cognitive.system.v0.trace_summary import _latency_projection
from test_cognitive_pipeline_v0_contract import _request

class Double:
    provenance = dict(provider='offline_double',model_id='gpt-6-luna',timeout_seconds=20,max_output_tokens=6000,
                      reasoning_effort='none',provider_adapter='two-call-test')
    usage = {}
    def __init__(self, mutation=None):
        self.calls=[]
        self.mutation=mutation
    def generate(self,stage,payload,schema=None):
        self.calls.append((stage,payload))
        if stage=='call_one':
            evidence = payload['semantic_input']['evidence_regions'][0]['evidence_ref']
            dims = [dict(dimension_key=k,status='unknown',value=None,assessment_basis='insufficient',uncertainty='high',
                         evidence_refs=[],evidence_summary='Scripted absent assessment.',
                         missing_input_keys=['test_missing_assessment'],conflict_refs=[]) for k in DIMENSIONS]
            output = dict(interpretation=dict(frames=[dict(predicate='social_opening',polarity='positive',arguments=[dict(name='addressee',value=None)],evidence_refs=[evidence])]),
                appraisal=[dict(definition_key='interaction.discharge_response_obligation',existing_goal_ref=None,frame_handles=[],dimensions=dims),
                           dict(definition_key='interaction.respond_to_social_contact',existing_goal_ref=None,frame_handles=[0],dimensions=dims)])
            if self.mutation:
                self.mutation(stage,output)
            return CallOneOutput.model_validate(output)
        plan = payload['deterministic_preview_plan']
        real = payload['deterministic_preview_realization']
        assert plan is not None and real is not None, (plan, real)
        texts=['Hello.']*len(plan['moves'])
        moves=[]
        alignments=[]
        for i,(move,text) in enumerate(zip(plan['moves'],texts)):
            moves.append(dict(action_key=move['definition_ref']['action_key'],
                goal_refs=[t['ref'] for t in move['target_refs'] if t['target_type']=='goal'],
                target_bindings=[dict(role=t['role'],target_type=t['target_type'],existing_ref=t['ref'],
                    revision_ref=t.get('revision_ref'),content_request_handle=None,object_kind=None,item_handle=None)
                    for t in move['target_refs']],excerpt=text))
            alignments.extend(dict(move_handle=i,binding_handle=j,excerpt=text) for j,_ in enumerate(real['moves'][i]['content_refs']))
        output=dict(content_proposal=[],behavior_proposal=moves,
                    response_candidate=dict(text=' '.join(texts),content_alignments=alignments))
        if self.mutation:
            self.mutation(stage,output)
        return CallTwoOutput.model_validate(output)

def run(model=None):
    model=model or Double()
    pipeline=install(CognitivePipelineV0(),model)
    trace=pipeline.run(_request('informal-greeting'))
    return pipeline,model,trace

def test_exact_two_calls_post_state_and_frozen_replay():
    pipeline,model,trace=run()
    assert [s for s,_ in model.calls]==['call_one','call_two']
    assert model.calls[1][1]['post_update_state']==trace.cognitive_turn.dynamic_state_update.snapshot.model_dump(mode='json')
    assert model.calls[1][1]['post_update_state']['supersedes_snapshot_id']==trace.cognitive_turn.input_snapshot.previous_state.state_snapshot_id
    frozen=FrozenTwoCallModel(trace.orchestration)
    _,_,replayed=run(frozen)
    assert semantic_digest(replayed)==semantic_digest(trace)
    assert frozen.cursor==2
    latency=_latency_projection(trace.model_dump(mode='json'),None)
    assert latency['model_call_count']==2 and latency['coverage']=='two_call'
    assert _latency_projection(replayed.model_dump(mode='json'),None)['model_call_count']==0

def test_new_goal_binding_and_unknown_scales_remain_canonical():
    _,_,trace=run()
    goals={g.goal_id for g in trace.cognitive_turn.input_snapshot.active_goals}
    assert goals=={f.subject.focal_goal_ref for f in trace.cognitive_turn.appraisal.frames}
    assert all(d.status=='unknown' and d.value is None for f in trace.cognitive_turn.appraisal.frames for d in f.dimensions)
    assert trace.cognitive_turn.dynamic_state_update.snapshot.supersedes_snapshot_id==trace.cognitive_turn.input_snapshot.previous_state.state_snapshot_id

@pytest.mark.parametrize('case',['goal','evidence','dimensions','behavior','target','content'])
def test_invalid_combined_sections_fail_closed(case):
    def mutate(stage,output):
        if stage=='call_one':
            if case=='goal': output['appraisal'][1]['existing_goal_ref']='foreign-goal'
            if case=='evidence': output['appraisal'][1]['dimensions'][0]['evidence_refs']=['foreign-evidence']
            if case=='dimensions': output['appraisal'][1]['dimensions'][0]['dimension_key']='appraisal.certainty'
        if stage=='call_two':
            if case=='behavior': output['behavior_proposal'][0]['action_key']='provide_information.answer'
            if case=='target': output['behavior_proposal'][0]['target_bindings'][0]['existing_ref']='foreign-target'
            if case=='content': output['content_proposal']=[dict(request_handle=99,output=dict(title=None,operations=[],content_items=[],inquiries=[]))]
    model=Double(mutate)
    with pytest.raises(CognitivePipelineExecutionError): run(model)
    assert len(model.calls)==(1 if case in {'goal','evidence','dimensions'} else 2)

def test_provider_explicit_none_and_no_retries_or_extra_request():
    from types import SimpleNamespace
    class Client:
        def __init__(self):
            self.calls=[]
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self,**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(choices=[])
    client=Client()
    model=OpenAITwoCallModel(model_id='gpt-6-luna',api_key=None,client=client)
    for stage in ['call_one','call_two']:
        params=model.parameters(stage,{})
        assert params['reasoning_effort']=='none'
        assert params['store'] is False and params['stream'] is False
        with pytest.raises(Exception): model.generate(stage,{})
    assert len(client.calls)==2


def test_supplied_wording_is_validated_without_third_call():
    from app.ai.Cognitive.system.v0.two_call.realization import captured_engine
    from app.ai.Cognitive.system.v0.live_shadow.realization import realization_inventory
    _,model,trace=run()
    turn=trace.cognitive_turn
    result=captured_engine(trace.orchestration).realize(action_result=trace.action_selection,
        inventory=realization_inventory(trace.action_selection,None,trace.requirement_resolution.realization_content,
                                        trace.collaboration.realization_content),
        personality=turn.input_snapshot.action_personality,state=turn.dynamic_state_update.snapshot,
        language_tag='en',current_user_utterance='Heyy',safety_basis=trace.action_selection.safety,
        configured_maximum_characters=4000)
    assert len(model.calls)==2
    assert result.status.value=='accepted_shadow',result.model_dump(mode='json')

def observe_double(model=None, text="Hi", realization_policy=None):
    from cognitive_shadow_support import make_capture
    from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline,observe,ObservationContext
    from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
    capture=make_capture(contents=(('user',text),))
    pipeline=install(build_pipeline(capture.turn_id),model or Double())
    if realization_policy is not None:
        pipeline.two_call.realization_policy=realization_policy
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,
        capture_digest=payload_digest(capture),started_at='2026-10-02T00:00:00+00:00'),
        pipeline_factory=lambda _:pipeline)
    return record,pipeline

def test_stored_two_call_record_replays_and_cannot_invoke_separate_realization():
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record,replay_realization
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    from app.ai.Cognitive.system.v0.realization_provider import resolve_provenance
    record,pipeline=observe_double()
    assert record.status.value=='completed',record.failure
    assert record.versions.semantic_digest=='cognitive-shadow-semantic-digest-v0.2'
    stored=dict(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),
        compiled_request_digest=record.compiled_request_digest,stored_semantic_digest=record.semantic_digest,
        versions_payload=record.versions.model_dump(mode='json'))
    assert replay(**stored).status.value=='match'
    class Forbidden:
        provenance=resolve_provenance(model_id='gpt-6-luna',timeout_seconds=20)
        def generate(self,payload):
            raise AssertionError('third provider call')
    record=realize_record(record,engine=RealizationEngineV0(Forbidden()),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow'
    assert record.realization_trace.model_provenance.generation_source=='captured_two_call'
    result=replay_realization(realization_status=record.realization_status.value,
        realization_trace_payload=record.realization_trace.model_dump(mode='json'),
        realization_trace_digest_value=record.realization_trace_digest,
        realization_semantic_digest_value=record.realization_semantic_digest,
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),
        compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        compiled_request_digest_value=record.compiled_request_digest,language_tag='en',
        fallback_maximum_characters=4000)
    assert result.status.value=='match',result

@pytest.mark.parametrize('stage',['call_one','call_two'])
def test_provider_failure_preserves_spans_and_is_terminal_without_worker_retry(stage):
    from app.ai.Cognitive.system.v0.appraisal.model_contracts import AppraisalModelError
    class Timeout(Double):
        def generate(self,name,payload,schema=None):
            if name==stage:
                self.calls.append((name,payload))
                raise AppraisalModelError('timeout')
            return super().generate(name,payload,schema)
    model=Timeout()
    record,pipeline=observe_double(model)
    assert record.status.value=='failed'
    assert record.failure.retryable is False
    assert len(model.calls)==(1 if stage=='call_one' else 2)
    spans=record.failure.latency_spans
    assert spans[-1].status=='timed_out' and spans[-1].worker_attempt==1
    latency=_latency_projection({},record.failure.model_dump(mode='json'))
    assert latency['model_call_count']==len(model.calls)
    assert latency['measurement_scope']=='shadow_worker_attempt'

def test_two_call_configuration_missing_credentials_has_no_fallback(monkeypatch):
    from app.core.config import Settings
    from app.services.cognitive_shadow import cognitive_pipeline
    from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import UnavailableSemanticGrounder
    settings=Settings(_env_file=None,openai_api_key=None,cognitive_shadow_orchestration='two_call')
    pipeline=cognitive_pipeline('test-missing-key',settings)
    assert isinstance(pipeline.interpretation_engine.grounder,UnavailableSemanticGrounder)

class DinnerDouble(Double):
    """Reviewed dinner content plus actual deterministic planning inside the double.

    This verifies protocol-to-compiler equivalence, not an LLM's selection quality.
    """
    def generate(self,stage,payload,schema=None):
        from simulated_authority_support import DinnerSemanticModel,DINNER,dinner_script
        from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticModelPayload
        from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import CollaborationModelPayload
        if stage=='call_one':
            self.calls.append((stage,payload))
            semantic=DinnerSemanticModel(DINNER['turns']).generate(SemanticModelPayload.model_validate(payload['semantic_input']))
            dims=[dict(dimension_key=k,status='unknown',value=None,assessment_basis='insufficient',uncertainty='high',
                       evidence_refs=[],evidence_summary='Scripted absent assessment.',
                       missing_input_keys=['test_missing_assessment'],conflict_refs=[]) for k in DIMENSIONS]
            assessments=[dict(definition_key='interaction.discharge_response_obligation',existing_goal_ref=None,frame_handles=[],dimensions=dims)]
            for handle,_ in enumerate(semantic.frames):
                for definition in payload['goal_definitions']:
                    if definition['definition_key']=='interaction.discharge_response_obligation':continue
                    assessments.append(dict(definition_key=definition['definition_key'],existing_goal_ref=None,
                                            frame_handles=[handle],dimensions=dims))
            return CallOneOutput(interpretation=semantic,appraisal=assessments)
        self.calls.append((stage,payload))
        from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import PipelineRequirementResolution
        from app.ai.Cognitive.system.v0.contracts.collaboration import WorkspaceResolution
        from app.ai.Cognitive.system.v0.cognitive_turn import CognitiveTurnRequest
        from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
        from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import ScriptedCollaborationModel
        from app.ai.Cognitive.system.v0.two_call.orchestrator import action_request_for
        from app.ai.Cognitive.system.v0.dynamic_state.models import StateSnapshot
        from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompilerV0
        from app.ai.Cognitive.system.v0.realization_v0.model_protocol import project_payload
        from app.ai.Cognitive.system.v0.live_shadow.realization import realization_inventory
        from app.ai.Cognitive.system.v0.collaborative_content_v0.projection import artifact_object_id,content_object_id
        pipeline=self.pipeline
        request=pipeline.two_call.request
        turn=self.turn
        workspace=WorkspaceResolution.model_validate(payload['workspace'])
        requirement=PipelineRequirementResolution.model_validate(payload['requirement'])
        contents=[dict(request_handle=i,output=dinner_script(CollaborationModelPayload.model_validate(r['payload'])))
                  for i,r in enumerate(payload['content_requests'])]
        if getattr(self,'invalid_content',False):
            contents[0]['output']['content_items'][0]['source_refs']=['foreign-source']
            return CallTwoOutput(content_proposal=contents,behavior_proposal=[],
                                 response_candidate=dict(text='Must never reach realization.',content_alignments=[]))
        pipeline.collaboration_engine=CollaborativeContentEngine(ScriptedCollaborationModel(dinner_script))
        collaboration=pipeline._collaborate(request,turn.interpretation,turn.goal_snapshot,workspace,requirement)
        enriched=CognitiveTurnRequest(**{k:getattr(turn,k) for k in CognitiveTurnRequest.model_fields}|
            dict(action_grounded_objects=(*request.action_grounded_objects,*requirement.grounded_objects,*collaboration.grounded_objects),
                 collaboration_handoff=collaboration.handoff))
        state=StateSnapshot.model_validate(payload['post_update_state'])
        result=pipeline.cognitive_turn_runner.action_engine.select(action_request_for(enriched,self.appraisal,state))
        compiled=RealizationCompilerV0().compile(action_result=result,
            inventory=realization_inventory(result,None,requirement.realization_content,collaboration.realization_content),
            personality=turn.action_personality,state=state,language_tag='en',
            current_user_utterance=request.messages[-1].content,safety_basis=result.safety,configured_maximum_characters=4000)
        real=project_payload(compiled)
        items={item['content_id']:item for item in real.content_items}
        local={}
        for handle,r in enumerate(payload['content_requests']):
            proposal=next(o.proposal for o in collaboration.outcomes if o.goal_ref==r['goal_ref'])
            local[artifact_object_id(proposal)]=(handle,'artifact',None)
            local.update({content_object_id(item.item_id):(handle,'content',i) for i,item in enumerate(proposal.content_items)})
        moves=[]; alignments=[];texts=[]
        for i,(move,wording_move) in enumerate(zip(result.plan.moves,real.moves)):
            fragments=[]
            for j,binding in enumerate(wording_move.content_refs):
                item=items[binding.content_ref]
                text=item.get('meaning') or 'Hello.'
                fragments.append(text)
                alignments.append(dict(move_handle=i,binding_handle=j,excerpt=text))
            text=' '.join(dict.fromkeys(fragments)) or 'Hello.'
            texts.append(text)
            targets=[]
            for target in move.target_refs:
                fields=dict(role=target.role,target_type=target.target_type,existing_ref=target.ref,
                    revision_ref=target.revision_ref,content_request_handle=None,object_kind=None,item_handle=None)
                if target.ref in local:
                    handle,kind,index=local[target.ref]
                    fields.update(existing_ref=None,revision_ref=None,content_request_handle=handle,object_kind=kind,item_handle=index)
                elif target.target_type.value=='partial_plan':
                    fields.update(existing_ref=None,revision_ref=None,object_kind='partial_plan')
                targets.append(fields)
            moves.append(dict(action_key=move.definition_ref.action_key,
                goal_refs=[t.ref for t in move.target_refs if t.target_type.value=='goal'],target_bindings=targets,excerpt=text))
        return CallTwoOutput(content_proposal=contents,behavior_proposal=moves,
                             response_candidate=dict(text=' '.join(texts),content_alignments=alignments))

def dinner_observe(invalid=False, model=None, context_projection=None):
    from simulated_authority_support import DINNER
    from cognitive_shadow_support import make_capture
    from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline,observe,ObservationContext
    from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
    capture=make_capture(contents=(('user',DINNER['turns'][1]['user']),))
    model=model or DinnerDouble();model.invalid_content=invalid
    pipeline=install(build_pipeline(capture.turn_id),model);model.pipeline=pipeline
    if context_projection is not None:
        pipeline.two_call.context_projection=context_projection
        pipeline.two_call.behavior_policy='strict_plan_agreement-v0.1'
    original=pipeline.cognitive_turn_runner.appraisal_engine.appraise_turn
    def appraise(request,turn):
        result,spans=original(request,turn)
        model.turn=turn;model.appraisal=result
        return result,spans
    pipeline.cognitive_turn_runner.appraisal_engine.appraise_turn=appraise
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,
        capture_digest=payload_digest(capture),started_at='2026-10-02T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    return record,pipeline,model

def test_ready_content_and_future_local_targets_compile_with_two_calls():
    record,pipeline,model=dinner_observe()
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    assert record.pipeline_trace.collaboration.outcomes[0].status=='proposed'
    assert any(m.definition_ref.action_key=='collaborate.deliver_artifact' for m in record.pipeline_trace.action_selection.plan.moves)
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    result=replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest,versions_payload=record.versions.model_dump(mode='json'))
    assert result.status.value=='match',result

def test_failed_ready_content_never_produces_a_completed_plan_or_realization():
    record,pipeline,model=dinner_observe(True)
    assert record.status.value=='failed' and record.realization_status.value=='not_requested'
    assert 'content_compilation' in record.failure.code
    assert len(model.calls)==2 and record.pipeline_trace is None


@pytest.mark.parametrize('case', ['omitted', 'all_omitted', 'ambiguous', 'unadopted'])
def test_unbound_appraisal_is_unknown_and_reaches_second_call(case):
    def mutate(stage, output):
        if stage != 'call_one':
            return
        if case == 'omitted':
            output['appraisal'].pop()
        elif case == 'all_omitted':
            output['appraisal'] = []
        elif case == 'ambiguous':
            other = copy.deepcopy(output['appraisal'][1])
            other['dimensions'][0].update(status='known', value='high', assessment_basis='observed',
                uncertainty='low', evidence_refs=output['interpretation']['frames'][0]['evidence_refs'])
            output['appraisal'].append(other)
        else:
            output['appraisal'][1]['definition_key'] = 'interaction.establish_shared_understanding'
    _, model, trace = run(Double(mutate))
    assert [stage for stage, _ in model.calls] == ['call_one', 'call_two']
    assert trace.orchestration.goal_binding_version == 'goal-formation-binding-v0.4'
    unknowns = [d for f in trace.cognitive_turn.appraisal.frames for d in f.dimensions
                if any(key.startswith('two_call_') for key in d.missing_input_keys)]
    assert unknowns and all(d.status == 'unknown' and d.value is None for d in unknowns)
    _, _, replayed = run(FrozenTwoCallModel(trace.orchestration))
    assert semantic_digest(trace) == semantic_digest(replayed)


def test_known_appraisal_and_supplied_capability_reference_reach_second_call():
    class KnownDouble(Double):
        def generate(self, stage, payload, schema=None):
            if stage == 'call_one':
                def mutate(_, output):
                    evidence = output['interpretation']['frames'][0]['evidence_refs']
                    capability = payload['appraisal_context']['runtime']['capability_snapshot']['snapshot_id']
                    for assessment in output['appraisal']:
                        for dimension in assessment['dimensions']:
                            kind = DIMENSIONS[dimension['dimension_key']][0]
                            value = {'unsigned_ordinal': 'moderate', 'signed_ordinal': 'positive', 'agency_category': 'user'}[kind]
                            dimension.update(status='known', value=value, assessment_basis='inferred', uncertainty='moderate',
                                evidence_refs=evidence + [capability], missing_input_keys=[])
                self.mutation = mutate
                result = super().generate(stage, payload, schema)
                self.mutation = None
                return result
            return super().generate(stage, payload, schema)
    _, model, trace = run(KnownDouble())
    assert len(model.calls) == 2
    assert all(d.status == 'known' for f in trace.cognitive_turn.appraisal.frames for d in f.dimensions)
    record, _ = observe_double(KnownDouble())
    assert record.status.value == 'completed', record.failure
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=4000)
    assert record.realization_status.value == 'accepted_shadow', record.realization_failure


def test_deictic_normalization_binds_to_the_accepted_frame():
    def mutate(stage, output):
        if stage == 'call_one':
            output['interpretation']['frames'][0].update(predicate='information_request', arguments=[
                dict(name='topic_ref', value='that'), dict(name='question_form', value='wh')])
    _, model, trace = run(Double(mutate))
    assert len(model.calls) == 2
    assert trace.interpretation.semantic_frames[0].arguments['topic_ref'] is None


def test_foreign_binding_has_specific_content_free_failure_code():
    def mutate(stage, output):
        if stage == 'call_one':
            output['appraisal'][1]['frame_handles'] = [999]
    record, model_pipeline = observe_double(Double(mutate))
    assert record.status.value == 'failed'
    assert record.failure.code.endswith('two_call.call_one.frame_handle_foreign.proposal_rejected')
    assert len(model_pipeline.two_call.model.calls) == 1


def test_call_one_schema_enforces_obligation_and_existing_goal_bindings():
    from app.ai.Cognitive.system.v0.two_call.provider import response_schema
    _, model, _ = run()
    payload = model.calls[0][1]
    schema = response_schema('call_one', payload)
    branches = schema['$defs']['OutcomeAssessment']['anyOf']
    obligation, new = branches
    assert obligation['properties']['frame_handles']['maxItems'] == 0
    assert obligation['properties']['existing_goal_ref'] == {'type': 'null'}
    assert new['properties']['frame_handles']['minItems'] == 1
    payload = copy.deepcopy(payload)
    payload['appraisal_context']['existing_goals'] = dict(records=[dict(goal_id='existing-goal')])
    existing = response_schema('call_one', payload)['$defs']['OutcomeAssessment']['anyOf'][-1]
    assert existing['properties']['existing_goal_ref']['enum'] == ['existing-goal']
    assert existing['properties']['frame_handles']['maxItems'] == 0


def test_call_one_schema_limits_appraisal_citations_to_supplied_references():
    from app.ai.Cognitive.system.v0.two_call.provider import response_schema
    _, model, _ = run()
    payload = model.calls[0][1]
    properties = response_schema('call_one', payload)['$defs']['DimensionProposal']['properties']
    refs = properties['evidence_refs']['items']['enum']
    assert payload['semantic_input']['evidence_regions'][0]['evidence_ref'] in refs
    assert payload['appraisal_context']['runtime']['capability_snapshot']['snapshot_id'] in refs
    assert 'foreign-evidence' not in refs
    assert properties['conflict_refs']['items']['enum'] == refs


def test_previous_binding_version_retains_strict_coverage_in_frozen_replay():
    def omit(stage, output):
        if stage == 'call_one':
            output['appraisal'] = []
    _, _, trace = run(Double(omit))
    legacy = trace.orchestration.model_copy(update={'goal_binding_version':'goal-formation-binding-v0.1'})
    frozen = FrozenTwoCallModel(legacy)
    with pytest.raises(CognitivePipelineExecutionError):
        run(frozen)
    assert frozen.cursor == 1


@pytest.mark.parametrize('citation', ['capability', 'none'])
def test_context_only_known_rating_does_not_block_response(citation):
    class ContextOnlyDouble(Double):
        def generate(self, stage, payload, schema=None):
            if stage == 'call_one':
                def mutate(_, output):
                    capability = payload['appraisal_context']['runtime']['capability_snapshot']['snapshot_id']
                    for assessment in output['appraisal']:
                        d = next(d for d in assessment['dimensions'] if d['dimension_key']=='appraisal.controllability')
                        d.update(status='known', value='high', assessment_basis='inferred', uncertainty='moderate',
                            evidence_refs=[capability] if citation=='capability' else [], missing_input_keys=[])
                self.mutation = mutate
                output = super().generate(stage, payload, schema)
                self.mutation = None
                return output
            return super().generate(stage, payload, schema)
    record, pipeline = observe_double(ContextOnlyDouble())
    assert record.status.value == 'completed', record.failure
    assert [s for s, _ in pipeline.two_call.model.calls] == ['call_one', 'call_two']
    dims = [f.dimension('appraisal.controllability') for f in record.pipeline_trace.cognitive_turn.appraisal.frames]
    assert all(d.status=='unknown' and d.value is None and 'two_call_missing_accepted_event' in d.missing_input_keys for d in dims)
    assert all(not set(d.evidence_refs) & set(f.subject.event_refs) for f,d in zip(record.pipeline_trace.cognitive_turn.appraisal.frames,dims))
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    record = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=4000)
    assert record.realization_status.value == 'accepted_shadow', record.realization_failure
    result = replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'), compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest, versions_payload=record.versions.model_dump(mode='json'))
    assert result.status.value == 'match', result


@pytest.mark.parametrize('invalid', ['foreign', 'scale', 'basis', 'uncertainty'])
def test_missing_event_recovery_does_not_hide_invalid_ratings(invalid):
    class InvalidDouble(Double):
        def generate(self, stage, payload, schema=None):
            if stage == 'call_one':
                def mutate(_, output):
                    capability = payload['appraisal_context']['runtime']['capability_snapshot']['snapshot_id']
                    d = output['appraisal'][0]['dimensions'][0]
                    d.update(status='known', value='high', assessment_basis='inferred', uncertainty='moderate',
                        evidence_refs=[capability], missing_input_keys=[])
                    if invalid=='foreign': d['evidence_refs']=['foreign-evidence']
                    elif invalid=='scale': d['value']='positive'
                    elif invalid=='basis': d['assessment_basis']='insufficient'
                    else: d['uncertainty']='high'
                self.mutation=mutate
            return super().generate(stage, payload, schema)
    record, pipeline = observe_double(InvalidDouble())
    assert record.status.value=='failed'
    assert len(pipeline.two_call.model.calls)==1
    subject = 'evidence_foreign' if invalid=='foreign' else 'canonical_appraisal_validation'
    assert record.failure.code.endswith(f'call_one.{subject}.proposal_rejected')


def test_provider_json_path_with_context_only_known_rating_reaches_shadow_response():
    import json
    from types import SimpleNamespace
    class Client:
        def __init__(self):
            self.calls=[]
            self.double=Double(self.mutate)
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def mutate(self, stage, output):
            if stage=='call_one':
                for assessment in output['appraisal']:
                    for d in assessment['dimensions']:
                        kind=DIMENSIONS[d['dimension_key']][0]
                        value={'unsigned_ordinal':'moderate','signed_ordinal':'positive','agency_category':'user'}[kind]
                        d.update(status='known',value=value,assessment_basis='inferred',uncertainty='moderate',
                            evidence_refs=[self.capability],missing_input_keys=[])
        def create(self, **kwargs):
            self.calls.append(kwargs)
            stage=kwargs['response_format']['json_schema']['name'].removesuffix('_v0_1')
            payload=json.loads(kwargs['messages'][1]['content'].removeprefix('INPUT_DATA\n').removesuffix('\nEND_INPUT_DATA'))
            if stage=='call_one':
                self.capability=payload['appraisal_context']['runtime']['capability_snapshot']['snapshot_id']
            output=self.double.generate(stage,payload)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(
                content=output.model_dump_json(),refusal=None,tool_calls=None))],usage=None)
    client=Client()
    model=OpenAITwoCallModel(model_id='gpt-6-luna',api_key=None,client=client,max_output_tokens=6000)
    record, pipeline=observe_double(model)
    assert record.status.value=='completed',record.failure
    assert len(client.calls)==2
    assert all(d.status=='unknown' for f in record.pipeline_trace.cognitive_turn.appraisal.frames for d in f.dimensions)
    assert all(c['reasoning_effort']=='none' for c in client.calls)
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(client.calls)==2


@pytest.mark.parametrize('status', ['known', 'unknown', 'disputed', 'not_applicable'])
def test_schema_allowed_unused_region_is_not_foreign_and_reaches_response(status):
    class UnusedRegionDouble(Double):
        def generate(self, stage, payload, schema=None):
            if stage=='call_one':
                refs=[r['evidence_ref'] for r in payload['semantic_input']['evidence_regions']]
                assert len(refs)>1
                self.citation=refs[-1]
                assert self.citation!=refs[0]
                def mutate(_, output):
                    d=output['appraisal'][0]['dimensions'][0]
                    d.update(status=status,value='high' if status=='known' else None,
                        assessment_basis={'known':'inferred','unknown':'insufficient','disputed':'inferred','not_applicable':'not_applicable'}[status],
                        uncertainty='moderate' if status in {'known','disputed'} else 'high',
                        evidence_refs=[self.citation],missing_input_keys=['test_missing_assessment'] if status=='unknown' else [],
                        conflict_refs=[self.citation,refs[0]] if status=='disputed' else [])
                self.mutation=mutate
                output=super().generate(stage,payload,schema)
                self.mutation=None
                return output
            return super().generate(stage,payload,schema)
    model=UnusedRegionDouble()
    record,pipeline=observe_double(model,text='Hi. Can you help me make dinner?')
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    from app.ai.Cognitive.system.v0.two_call.provider import response_schema
    permitted=response_schema('call_one',model.calls[0][1])['$defs']['DimensionProposal']['properties']['evidence_refs']['items']['enum']
    assert model.citation in permitted
    dims=[d for f in record.pipeline_trace.cognitive_turn.appraisal.frames for d in f.dimensions
          if 'two_call_unbound_evidence' in d.missing_input_keys]
    assert dims and all(d.status=='unknown' and d.value is None and model.citation not in d.evidence_refs for d in dims)
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure


def test_every_schema_permitted_citation_compiles_or_becomes_explicit_unknown():
    from app.ai.Cognitive.system.v0.two_call.provider import call_one_evidence_refs, response_schema
    class AllReferencesDouble(Double):
        def generate(self,stage,payload,schema=None):
            if stage=='call_one':
                self.refs=call_one_evidence_refs(payload)
                enum=response_schema(stage,payload)['$defs']['DimensionProposal']['properties']['evidence_refs']['items']['enum']
                assert self.refs==set(enum)
                def mutate(_,output):
                    for assessment in output['appraisal']:
                        for d in assessment['dimensions']:
                            kind=DIMENSIONS[d['dimension_key']][0]
                            d.update(status='known',value={'unsigned_ordinal':'high','signed_ordinal':'positive','agency_category':'user'}[kind],
                                assessment_basis='inferred',uncertainty='moderate',evidence_refs=sorted(self.refs),missing_input_keys=[])
                self.mutation=mutate
                output=super().generate(stage,payload,schema)
                self.mutation=None
                return output
            return super().generate(stage,payload,schema)
    model=AllReferencesDouble()
    record,pipeline=observe_double(model,text='Hi. Can you help me make dinner?')
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    trace=record.pipeline_trace
    assert trace.orchestration.goal_binding_version=='goal-formation-binding-v0.4'
    assert all(d.status=='unknown' and 'two_call_unbound_evidence' in d.missing_input_keys
               for f in trace.cognitive_turn.appraisal.frames for d in f.dimensions)
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    result=replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=trace.model_dump(mode='json'),compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest,versions_payload=record.versions.model_dump(mode='json'))
    assert result.status.value=='match',result
    # The original provider proposal and its unbound citations remain captured.
    assert set(trace.orchestration.calls[0].proposal['appraisal'][0]['dimensions'][0]['evidence_refs'])==model.refs
    legacy=trace.orchestration.model_copy(update={'goal_binding_version':'goal-formation-binding-v0.3'})
    from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
    # Replaying the same owned request under the prior policy reproduces the
    # old deterministic rejection before Call 2, preserving versioned behavior.
    frozen=FrozenTwoCallModel(legacy)
    old_pipeline=install(build_pipeline(record.turn_id),frozen)
    with pytest.raises(CognitivePipelineExecutionError) as raised:
        old_pipeline.run(record.compiled_request)
    cause=raised.value
    while cause is not None and not isinstance(cause,TwoCallError):
        cause=cause.__cause__
    assert cause is not None and cause.subject=='evidence_foreign'
    assert frozen.cursor==1


def test_ready_content_schema_requires_patch_items_anchors_and_sources():
    from app.ai.Cognitive.system.v0.two_call.provider import response_schema
    record,_,model=dinner_observe()
    schema=response_schema('call_two',model.calls[1][1])
    section=schema['properties']['content_proposal']
    assert section['minItems']==section['maxItems']==1
    branch=section['items']
    assert branch['properties']['request_handle']['enum']==[0]
    properties=branch['properties']['output']['properties']
    assert properties['operations']['minItems']==1
    assert properties['content_items']['minItems']==1
    item=properties['content_items']['items']['properties']
    assert item['anchors']['minItems']==1 and item['anchors']['maxItems']==6
    for name in ('operations','content_items','inquiries'):
        assert properties[name]['items']['properties']['source_refs']['minItems']==1
    assert 'CollaborationModelOutput' not in schema['$defs']


@pytest.mark.parametrize('case,code',[('patch','missing_patch'),('items','missing_content'),('anchors','content_invalid')])
def test_content_failure_reports_the_original_compiler_code(case,code):
    class InvalidContent(DinnerDouble):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                data=output.model_dump(mode='json')
                content=data['content_proposal'][0]['output']
                if case=='patch': content['operations']=[]
                elif case=='items': content['content_items']=[]
                else: content['content_items'][0]['anchors']=[]
                return CallTwoOutput.model_validate(data)
            return output
    record,_,model=dinner_observe(model=InvalidContent())
    assert len(model.calls)==2
    assert record.status.value=='failed' and record.realization_status.value=='not_requested'
    assert record.failure.code.endswith(f'call_two.content_compilation.compiler.{code}.proposal_rejected')


def test_compact_content_payload_keeps_required_context_and_accepted_response():
    from app.ai.Cognitive.system.v0.two_call.provider import canonical,prompt
    record,pipeline,model=dinner_observe()
    payload=model.calls[1][1]
    assert payload['context_projection']=='two-call-stage-context-v0.3'
    assert len(canonical(payload))<70000
    keys={d['definition_ref']['action_key'] for d in payload['allowed_action_definitions']}
    assert 'collaborate.deliver_artifact' in keys and 'seek_information.inquire' in keys
    assert len(keys)<20
    assert payload['post_update_state']==record.pipeline_trace.cognitive_turn.dynamic_state_update.snapshot.model_dump(mode='json')
    assert payload['content_requests'][0]['compiler_requirements']['patch_required'] is True
    assert 'at least one typed patch operation' in prompt('call_two',payload)
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(model.calls)==2


def test_legacy_full_content_payload_still_frozen_replays():
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    record,_,model=dinner_observe(context_projection='two-call-stage-context-v0.1')
    assert record.status.value=='completed',record.failure
    assert len(model.calls[1][1]['allowed_action_definitions'])>20
    result=replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest,versions_payload=record.versions.model_dump(mode='json'))
    assert result.status.value=='match',result


def test_multiple_content_requests_have_separate_handle_bound_schemas():
    from app.ai.Cognitive.system.v0.two_call.provider import response_schema
    _,_,model=dinner_observe()
    payload=copy.deepcopy(model.calls[1][1])
    second=copy.deepcopy(payload['content_requests'][0])
    second['payload']['operation']='describe'
    from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import vocabulary_for
    from app.ai.Cognitive.system.v0.contracts.workspace import ArtifactOperation,ArtifactType
    second['payload']['vocabulary']=vocabulary_for(ArtifactOperation.DESCRIBE,ArtifactType.MEAL_PLAN).model_dump(mode='json')
    second['payload']['sources'][0]['ref']='second-source'
    payload['content_requests'].append(second)
    section=response_schema('call_two',payload)['properties']['content_proposal']
    assert section['minItems']==section['maxItems']==2
    branches=section['items']['anyOf']
    assert [b['properties']['request_handle']['enum'] for b in branches]==[[0],[1]]
    assert branches[0]['properties']['output']['properties']['operations']['minItems']==1
    assert branches[1]['properties']['output']['properties']['operations']['maxItems']==0
    for branch,request in zip(branches,payload['content_requests']):
        refs=branch['properties']['output']['properties']['content_items']['items']['properties']['source_refs']['items']['enum']
        assert set(refs)=={s['ref'] for s in request['payload']['sources']}


def test_content_json_provider_path_finishes_with_two_requests():
    import json
    from types import SimpleNamespace
    fixture=DinnerDouble()
    calls=[]
    class Client:
        def __init__(self):
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self,**kwargs):
            calls.append(kwargs)
            stage=kwargs['response_format']['json_schema']['name'].removesuffix('_v0_1')
            payload=json.loads(kwargs['messages'][1]['content'].removeprefix('INPUT_DATA\n').removesuffix('\nEND_INPUT_DATA'))
            fixture.pipeline=model.pipeline
            if stage=='call_two':
                fixture.turn=model.turn;fixture.appraisal=model.appraisal
            output=fixture.generate(stage,payload)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(
                content=output.model_dump_json(),refusal=None,tool_calls=None))],usage=None)
    model=OpenAITwoCallModel(model_id='gpt-6-luna',api_key=None,client=Client(),max_output_tokens=6000)
    record,_,_=dinner_observe(model=model)
    assert record.status.value=='completed',record.failure
    assert len(calls)==2
    section=calls[1]['response_format']['json_schema']['schema']['properties']['content_proposal']
    assert section['items']['properties']['output']['properties']['operations']['minItems']==1
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(calls)==2 and all(c['reasoning_effort']=='none' for c in calls)


def acknowledgement_proposal(stage,output):
    if stage=='call_two':
        move=output['behavior_proposal'][0]
        locator=next(t for t in move['target_bindings'] if t['role']=='locator')
        move['action_key']='give_feedback.acknowledge'
        move['target_bindings']=[dict(locator,role='primary')]
        move['excerpt']='Got it.'
        output['response_candidate']=dict(text='Got it.',content_alignments=[dict(move_handle=0,binding_handle=0,excerpt='Got it.')])


def test_eligible_non_argmax_model_behavior_finishes_without_rewriting_wording():
    record,pipeline=observe_double(Double(acknowledgement_proposal))
    assert record.status.value=='completed',record.failure
    trace=record.pipeline_trace
    assert trace.orchestration.behavior_policy=='model_grounded_selection-v0.1'
    result=trace.action_selection
    assert result.plan.moves[0].definition_ref.action_key=='give_feedback.acknowledge'
    from app.ai.Cognitive.system.v0.contracts.actions import deterministic_selection
    step=result.steps[0]
    winner,_,_=deterministic_selection(step.scored_candidates,{c.candidate_id:c.lexical_identity for c in step.constructed_candidates})
    assert winner!=step.selected_candidate_ref
    assert step.model_choice.candidate_ref==step.selected_candidate_ref
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert record.realization_trace.final_candidate.text=='Got it.'
    assert len(pipeline.two_call.model.calls)==2
    from app.ai.Cognitive.system.v0.live_shadow.service import replay
    result=replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=trace.model_dump(mode='json'),compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest,versions_payload=record.versions.model_dump(mode='json'))
    assert result.status.value=='match',result


def test_model_cannot_stop_without_delivering_required_compiled_content():
    class OmitDelivery(DinnerDouble):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                data=output.model_dump(mode='json')
                data['behavior_proposal']=[]
                return CallTwoOutput.model_validate(data)
            return output
    record,_,model=dinner_observe(model=OmitDelivery())
    assert record.status.value=='failed' and len(model.calls)==2
    assert 'behavior_proposal_ineligible' in record.failure.code


def test_model_goal_refs_bind_to_computed_goal_links_not_just_goal_targets():
    class LinkedGoalDouble(Double):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                data=output.model_dump(mode='json')
                move=data['behavior_proposal'][0]
                candidate=next(c for c in payload['current_behavior_candidates'] if c['action_key']==move['action_key'])
                assert candidate['goal_refs']
                move['goal_refs']=candidate['goal_refs']
                return CallTwoOutput.model_validate(data)
            return output
    record,_=observe_double(LinkedGoalDouble())
    assert record.status.value=='completed',record.failure


def test_model_policy_retains_blocker_first_and_does_not_stop_on_open_conditions():
    from test_action_v0_blocked_request import _trace
    from app.ai.Cognitive.system.v0.two_call.orchestrator import action_request_for
    from app.ai.Cognitive.system.v0.action_selection_v0.engine import ActionSelectionEngine
    from app.ai.Cognitive.system.v0.action_selection_v0.errors import ActionSelectionError
    trace=_trace('How much protein is in two eggs?')
    turn=trace.cognitive_turn
    request=action_request_for(turn.input_snapshot,turn.appraisal,turn.dynamic_state_update.snapshot)
    assert trace.action_selection.planning_conditions
    with pytest.raises(ActionSelectionError):
        ActionSelectionEngine().select(request,proposal_selector=lambda *args:None,source_call_digest='a'*64)
    def choose_restricted(step,candidates,eligible,restricted,*args):
        assert restricted
        return restricted[0]
    with pytest.raises(ActionSelectionError):
        ActionSelectionEngine().select(request,proposal_selector=choose_restricted,source_call_digest='a'*64)


def test_model_choice_provenance_cannot_be_detached_from_captured_call():
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineTrace
    _,_,trace=run(Double(acknowledgement_proposal))
    data=trace.model_dump(mode='json')
    for action in (data['action_selection'],data['cognitive_turn']['action_selection']):
        for step in action['steps']:
            step['model_choice']['source_call_digest']='0'*64
    with pytest.raises(ValueError):
        CognitivePipelineTrace.model_validate(data)


def test_supplied_delivery_binding_and_goal_links_compile_for_future_content():
    class ExplicitDelivery(DinnerDouble):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                data=output.model_dump(mode='json')
                binding=payload['content_requests'][0]['delivery_binding']
                move=next(m for m in data['behavior_proposal'] if m['action_key']==binding['action_key'])
                move['goal_refs']=binding['goal_refs']
                move['target_bindings']=[binding['target']]
                return CallTwoOutput.model_validate(data)
            return output
    record,_,_=dinner_observe(model=ExplicitDelivery())
    assert record.status.value=='completed',record.failure
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure


@pytest.mark.parametrize('policy,expected', [('style_advisory-v0.1','accepted_shadow'),('strict-v0.1','rejected')])
def test_style_tolerance_completes_two_calls_and_replays_under_recorded_policy(policy,expected):
    def warmer_ack(stage,output):
        acknowledgement_proposal(stage,output)
        if stage=='call_two':
            output['behavior_proposal'][0]['excerpt']='Got it!'
            output['response_candidate']['text']='Got it!'
            output['response_candidate']['content_alignments'][0]['excerpt']='Got it!'
    record,pipeline=observe_double(Double(warmer_ack),realization_policy=policy)
    assert record.status.value=='completed',record.failure
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record,replay_realization
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value==expected,record.realization_failure
    assert record.realization_trace.validation_policy==policy
    assert len(pipeline.two_call.model.calls)==2
    validation=record.realization_trace.validation
    if policy=='style_advisory-v0.1':
        assert {w.code.value for w in validation.warnings}=={'exclamation_used'}
        assert validation.violations==()
    else:
        assert 'exclamation_used' in validation.codes
        assert validation.warnings==()
        assert 'realization_policy' not in record.pipeline_trace.orchestration.model_dump(mode='json')
        assert 'validation_policy' not in record.realization_trace.model_dump(mode='json')
    result=replay_realization(realization_status=record.realization_status.value,
        realization_trace_payload=record.realization_trace.model_dump(mode='json'),
        realization_trace_digest_value=record.realization_trace_digest,
        realization_semantic_digest_value=record.realization_semantic_digest,
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),
        compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        compiled_request_digest_value=record.compiled_request_digest,language_tag='en',
        fallback_maximum_characters=4000)
    assert result.status.value=='match',result


@pytest.mark.parametrize('coverage',['one','none'])
def test_compiled_move_spans_complete_missing_content_alignments_and_replay(coverage):
    class SparseDinner(DinnerDouble):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                raw=output.model_dump(mode='json')
                self.original_alignment_count=len(raw['response_candidate']['content_alignments'])
                assert self.original_alignment_count>1
                raw['response_candidate']['content_alignments']=raw['response_candidate']['content_alignments'][:1] if coverage=='one' else []
                return CallTwoOutput.model_validate(raw)
            return output
    record,pipeline,_=dinner_observe(model=SparseDinner())
    assert record.status.value=='completed',record.failure
    assert record.pipeline_trace.orchestration.alignment_policy=='compiled_move_spans-v0.1'
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record,replay_realization
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(pipeline.two_call.model.calls)==2
    assert record.realization_trace.final_candidate.text==record.pipeline_trace.orchestration.calls[1].proposal['response_candidate']['text']
    result=replay_realization(realization_status=record.realization_status.value,
        realization_trace_payload=record.realization_trace.model_dump(mode='json'),
        realization_trace_digest_value=record.realization_trace_digest,
        realization_semantic_digest_value=record.realization_semantic_digest,
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),
        compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        compiled_request_digest_value=record.compiled_request_digest,language_tag='en',fallback_maximum_characters=4000)
    assert result.status.value=='match',result


def test_missing_alignment_derivation_does_not_accept_missing_required_content():
    class OmittedDinner(DinnerDouble):
        def generate(self,stage,payload,schema=None):
            output=super().generate(stage,payload,schema)
            if stage=='call_two':
                raw=output.model_dump(mode='json')
                for move in raw['behavior_proposal']:
                    if move['action_key']=='collaborate.deliver_artifact':
                        raw['response_candidate']['text']=raw['response_candidate']['text'].replace(move['excerpt'],'Here is an idea.')
                        move['excerpt']='Here is an idea.'
                raw['response_candidate']['content_alignments']=[]
                return CallTwoOutput.model_validate(raw)
            return output
    record,pipeline,_=dinner_observe(model=OmittedDinner())
    assert record.status.value=='completed',record.failure
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='rejected',record.realization_failure
    assert 'content_alignment_missing' in record.realization_trace.validation.codes
    assert len(pipeline.two_call.model.calls)==2


def test_legacy_capture_still_requires_complete_binding_coverage():
    record,pipeline=observe_double(Double(acknowledgement_proposal))
    from app.ai.Cognitive.system.v0.two_call.realization import captured_engine
    from app.ai.Cognitive.system.v0.live_shadow.realization import realization_inventory
    orchestration=record.pipeline_trace.orchestration
    raw=orchestration.model_dump(mode='json')
    raw.pop('alignment_policy')
    raw['calls'][1]['proposal']['response_candidate']['content_alignments']=[]
    from app.ai.Cognitive.system.v0.two_call.contracts import TwoCallTrace
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationCompilerV0,project_payload
    from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderError
    legacy=TwoCallTrace.model_validate(raw)
    assert legacy.alignment_policy=='complete_bindings-v0.1'
    assert 'alignment_policy' not in legacy.model_dump(mode='json')
    trace=record.pipeline_trace
    turn=trace.cognitive_turn
    compiled=RealizationCompilerV0().compile(action_result=trace.action_selection,
        inventory=realization_inventory(trace.action_selection,None,trace.requirement_resolution.realization_content,
                                        trace.collaboration.realization_content),
        personality=turn.input_snapshot.action_personality,state=turn.dynamic_state_update.snapshot,
        language_tag='en',current_user_utterance='Hi',safety_basis=trace.action_selection.safety,
        configured_maximum_characters=4000)
    with pytest.raises(RealizationProviderError):
        captured_engine(legacy).model.generate(project_payload(compiled))


@pytest.mark.parametrize('invalid',['duplicate','foreign_move','foreign_binding'])
def test_compiled_alignment_policy_still_rejects_invalid_supplied_handles(invalid):
    def mutate(stage,output):
        acknowledgement_proposal(stage,output)
        if stage=='call_two':
            alignments=output['response_candidate']['content_alignments']
            if invalid=='duplicate':alignments.append(dict(alignments[0]))
            elif invalid=='foreign_move':alignments[0]['move_handle']=999
            else:alignments[0]['binding_handle']=999
    record,pipeline=observe_double(Double(mutate))
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='provider_failed'
    assert record.realization_trace.attempts[0].provider_error_code.value=='provider_output_invalid'
    assert len(pipeline.two_call.model.calls)==2


def test_missing_bindings_across_multiple_moves_reach_normal_validation():
    from test_realization_v0_validator import SEMANTIC, VALID
    from app.ai.Cognitive.system.v0.realization_v0 import project_payload,build_candidate,RealizationValidatorV0
    from app.ai.Cognitive.system.v0.two_call.realization import CapturedRealizationModel
    request=SEMANTIC
    _,text,excerpts=VALID['semantic']
    payload=project_payload(request)
    assert len(payload.moves)>1
    record,_=observe_double()
    model=CapturedRealizationModel(record.pipeline_trace.orchestration)
    model.output=CallTwoOutput(content_proposal=[],behavior_proposal=[dict(action_key=m.action_key,
        goal_refs=[],target_bindings=[],excerpt=excerpts[m.order]) for m in payload.moves],
        response_candidate=dict(text=text,content_alignments=[dict(move_handle=0,binding_handle=0,
                                                                  excerpt=excerpts[1])]))
    output=model.generate(payload)
    assert len(output.content_alignments)>1
    candidate=build_candidate(request,1,output,model.provenance)
    result=RealizationValidatorV0().validate(request,candidate)
    assert result.status=='accepted_for_shadow_review',result.codes
