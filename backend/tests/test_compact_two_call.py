"""Synthetic compact protocol cases. No external model calls or behavioral-quality claims."""
import json
import pytest
from app.ai.Cognitive.system.v0.two_call.compact_contracts import COMPACT_PROTOCOL,MindProposal,ResponseProposal
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.two_call.provider import FrozenTwoCallModel,canonical,response_schema
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline,observe,ObservationContext,replay
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record,replay_realization
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from cognitive_shadow_support import make_capture
from test_two_call_orchestration import Double

class CompactDouble:
    usage={}
    provenance=Double.provenance
    def __init__(self,artifact=False,mutation=None):
        self.calls=[];self.artifact=artifact;self.mutation=mutation
    def generate(self,stage,payload,schema=None):
        self.calls.append((stage,payload))
        assert payload['protocol']==COMPACT_PROTOCOL
        owner=self.pipeline.two_call
        if stage=='call_one':
            semantic=owner.semantic_input
            if self.artifact:
                from simulated_authority_support import DinnerSemanticModel,DINNER
                output=DinnerSemanticModel(DINNER['turns']).generate(semantic)
            else:
                from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticModelOutput
                output=SemanticModelOutput(frames=[dict(predicate='social_opening',polarity='positive',arguments=[],
                    evidence_refs=[semantic.evidence_regions[0].evidence_ref])])
            observations=[];readings=[];events=[]
            for i,frame in enumerate(output.frames):
                observations.append(dict(handle=f'e{i}',predicate=frame.predicate,polarity=frame.polarity,
                    arguments=[a.model_dump(mode='json') for a in frame.arguments],
                    evidence_handles=[owner.references.reverse[r] for r in frame.evidence_refs],source_span=None,epistemic_status='expressed'))
                readings.append(dict(handle=f'q{i}',event_handles=[f'e{i}'],hypothesis_key='interpretation.pragmatic.literal',
                    candidate_meaning='Literal contribution.',support='strong',uncertainty='low',
                    evidence_handles=[],context_handles=[]))
                if frame.predicate=='social_opening':
                    readings.append(dict(handle=f's{i}',event_handles=[f'e{i}'],hypothesis_key='interpretation.goal.maintain_social_connection',
                        candidate_meaning='A social opening.',support='moderate',uncertainty='moderate',evidence_handles=[],context_handles=[]))
                events.append(dict(event_handles=[f'e{i}'],reading_handle=f'q{i}',values=[None]*8,uncertainty='high',
                                   evidence_handles=[],context_handles=[],exceptions=[]))
            raw=dict(observations=observations,readings=readings,event_appraisals=events,existing_goal_impacts=[],unknowns=[],conflicts=[])
        else:
            packet=payload['decision_context'];ops=[]
            if self.artifact:
                from simulated_authority_support import dinner_script
                from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import CollaborationModelPayload
                for i,request in enumerate(owner.prepared_content_requests):
                    output=dinner_script(CollaborationModelPayload.model_validate(request['payload']))
                    def local(value,key=''):
                        if isinstance(value,dict):return {k:local(v,k) for k,v in value.items()}
                        if isinstance(value,(tuple,list)):return [local(v,key) for v in value]
                        if isinstance(value,str) and key in {'source_refs','target_id'}:return owner.references.reverse.get(value,value)
                        return value
                    ops.append(dict(request_handle=i,output=local(output)))
                request=packet['content_requests'][0]
                text=' '.join(i['statement'] for i in ops[0]['output']['content_items'])
                moves=[dict(choice_handle=None,template_handle=request['delivery_template'],goal_handles=[request['goal_ref']],
                    target_bindings=[request['delivery_target']],segment_handles=['s0'],parameters=[])]
            else:
                choice=next(c for c in packet['choices'] if c['action_key']=='manage_dialogue.greet')
                moves=[dict(choice_handle=choice['handle'],template_handle=None,goal_handles=choice['goal_refs'],
                            target_bindings=[],segment_handles=['s0'],parameters=[])]
                text='Hello.'
            raw=dict(content_ops=ops,moves=moves,segments=[dict(handle='s0',text=text,content_handles=[],claim_bindings=[])],inquiries=[])
        if self.mutation:self.mutation(stage,raw,payload)
        return (MindProposal if stage=='call_one' else ResponseProposal).model_validate(raw)


def run_compact(model=None,text='Hi'):
    capture=make_capture(contents=(('user',text),))
    model=model or CompactDouble()
    pipeline=install(build_pipeline(capture.turn_id),model,protocol=COMPACT_PROTOCOL);model.pipeline=pipeline
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=payload_digest(capture),
        started_at='2026-10-04T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    return record,pipeline,model


def assert_replay(record):
    report=replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),compiled_request_digest=record.compiled_request_digest,
        stored_semantic_digest=record.semantic_digest,versions_payload=record.versions.model_dump(mode='json'))
    assert report.status.value=='match',report


def test_compact_social_turn_completes_both_calls_and_realization():
    record,pipeline,model=run_compact()
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    assert_replay(record)
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert record.realization_trace.final_candidate.text=='Hello.'
    assert record.pipeline_trace.interpretation.versions.bias_policy=='interpretation-bias-owner-model-v0.1'
    assert all(not h.bias_adjustments for h in record.pipeline_trace.interpretation.hypotheses)
    report=replay_realization(realization_status=record.realization_status.value,
        realization_trace_payload=record.realization_trace.model_dump(mode='json'),realization_trace_digest_value=record.realization_trace_digest,
        realization_semantic_digest_value=record.realization_semantic_digest,
        pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),compiled_request_payload=record.compiled_request.model_dump(mode='json'),
        compiled_request_digest_value=record.compiled_request_digest,language_tag='en',fallback_maximum_characters=4000)
    assert report.status.value=='match',report


def test_compact_ready_dinner_content_completes_and_replays():
    from simulated_authority_support import DINNER
    record,pipeline,model=run_compact(CompactDouble(artifact=True),text=DINNER['turns'][1]['user'])
    assert record.status.value=='completed',record.failure
    assert_replay(record)
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(model.calls)==2
    assert record.pipeline_trace.collaboration.outcomes[0].status=='proposed'
    import re
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-',canonical(model.calls[0][1])+canonical(model.calls[1][1]))


@pytest.mark.parametrize('case',['observation_evidence','reading_event','context_reference','goal_impact','duplicate_event',
                               'choice','segment','target','content_request','duplicate_segment','incompatible_overlap'])
def test_compact_foreign_and_ambiguous_references_fail_closed(case):
    import copy
    first=case in {'observation_evidence','reading_event','context_reference','goal_impact','duplicate_event'}
    def mutate(stage,raw,payload):
        if stage=='call_one':
            if case=='observation_evidence':raw['observations'][0]['evidence_handles']=['r99999']
            if case=='reading_event':raw['readings'][0]['event_handles']=['foreign-event']
            if case=='context_reference':raw['readings'][0]['context_handles']=['r99999']
            if case=='goal_impact':raw['existing_goal_impacts']=[dict(goal_handle='r99999',event_handles=['e0'],relevance=None,congruence=None,uncertainty='high')]
            if case=='duplicate_event':raw['event_appraisals'].append(copy.deepcopy(raw['event_appraisals'][0]))
        else:
            if case=='choice':raw['moves'][0]['choice_handle']='b99999'
            if case=='segment':raw['moves'][0]['segment_handles']=['foreign-segment']
            if case=='target':raw['moves'][0]['goal_handles']=['r99999']
            if case=='content_request':raw['content_ops']=[dict(request_handle=99,output=dict(title=None,operations=[],content_items=[],inquiries=[]))]
            if case=='duplicate_segment':raw['segments'].append(copy.deepcopy(raw['segments'][0]))
            if case=='incompatible_overlap':raw['moves'].append(copy.deepcopy(raw['moves'][0]))
    record,_,model=run_compact(CompactDouble(mutation=mutate))
    assert record.status.value=='failed'
    assert len(model.calls)==(1 if first else 2)
    assert 'reference_compilation' in record.failure.code


def test_compact_projection_is_single_source_and_uses_actual_updated_state():
    record,pipeline,model=run_compact()
    assert record.status.value=='completed',record.failure
    one,two=[p for _,p in model.calls];context=two['decision_context']
    for key in ('persona','biases','relationship','memories','dialogue','runtime'):
        assert context['shared_context'][key]==one['turn_context'][key]
    assert 'state_before' not in context['shared_context']
    assert context['state_after']['snapshot_handle']!=one['turn_context']['state_before']['snapshot_handle']
    assert one['turn_context']['relationship']['availability']=='unavailable'
    assert one['turn_context']['persona']['traits']['assertiveness']['availability']=='unavailable'
    assert not any('preview' in k for k in two)
    assert not any('messages' in r or 'vocabulary' in r for r in context['content_requests'])
    proposal=pipeline.two_call.captures[1].model_dump(mode='json')['proposal']
    assert set(proposal)=={'content_ops','moves','segments','inquiries'}
    assert 'excerpt' not in proposal['moves'][0] and proposal['segments'][0]['text']=='Hello.'
    assert all(r['readiness_kind'] in {'ready','needs_clarification','unavailable_capability','needs_authorization',
        'unsafe','insufficient_grounding','failed_dependency'} for r in context['readiness'])
    assert all('permission_status' in r and 'permitted_next_behaviors' in r for r in context['readiness'])
    import re
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-',canonical(one)+canonical(two))


def test_compact_provider_json_decoding_and_parameters_end_to_end():
    from types import SimpleNamespace as NS
    from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
    double=CompactDouble();calls=[]
    class Client:
        chat=NS(completions=NS())
        def create(self,**params):
            calls.append(params)
            payload=json.loads(params['messages'][1]['content'].removeprefix('INPUT_DATA\n').removesuffix('\nEND_INPUT_DATA'))
            stage='call_one' if 'turn_context' in payload else 'call_two'
            proposal=double.generate(stage,payload)
            return NS(choices=[NS(finish_reason='stop',message=NS(content=proposal.model_dump_json(),refusal=None,tool_calls=None))],
                usage=NS(prompt_tokens=100,completion_tokens=50,prompt_tokens_details=NS(cached_tokens=0),completion_tokens_details=NS(reasoning_tokens=0)))
    client=Client();client.chat.completions.create=client.create
    model=OpenAITwoCallModel(model_id='gpt-6-luna',api_key=None,client=client)
    capture=make_capture(contents=(('user','Hi'),));pipeline=install(build_pipeline(capture.turn_id),model,protocol=COMPACT_PROTOCOL)
    double.pipeline=pipeline
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=payload_digest(capture),
        started_at='2026-10-04T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    assert record.status.value=='completed',record.failure
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure
    assert len(calls)==2
    assert [p['response_format']['json_schema']['name'] for p in calls]==['call_one_compact_v0_1','call_two_compact_v0_1']
    assert all(p['reasoning_effort']=='none' and p['store'] is False and p['stream'] is False for p in calls)
    assert all(c.provenance['protocol_version']==COMPACT_PROTOCOL for c in pipeline.two_call.captures)
    spans=[s for s in pipeline.two_call.recorder.spans if s.component=='provider_attempt']
    assert all(s.input_tokens==100 and s.reasoning_tokens==0 and s.parse_ms>=0 for s in spans)
    assert_replay(record)


def test_alternative_event_appraisals_count_once_and_conflicts_stay_unknown():
    import copy
    def mutate(stage,raw,payload):
        if stage!='call_one':return
        other=copy.deepcopy(raw['readings'][0]);other['handle']='alternative';other['uncertainty']='high'
        raw['readings'].append(other)
        first=raw['event_appraisals'][0];first['values'][6]='positive';first['uncertainty']='moderate'
        second=copy.deepcopy(first);second['reading_handle']='alternative';second['values'][6]='negative'
        raw['event_appraisals'].append(second)
    record,_,_=run_compact(CompactDouble(mutation=mutate))
    assert record.status.value=='completed',record.failure
    appraisal=record.pipeline_trace.cognitive_turn.appraisal
    assert len(appraisal.model_trace.provenance['event_allocations'])==1
    assert all(d.status=='unknown' for f in appraisal.frames for d in f.dimensions if d.dimension_key=='appraisal.affiliative_value')
    assert_replay(record)


@pytest.mark.parametrize('source',['biases','persona','state_before','dialogue'])
def test_assessed_context_citation_and_unique_event_influence(source):
    def mutate(stage,raw,payload):
        if stage!='call_one':return
        event=raw['event_appraisals'][0]
        context=payload['turn_context']
        handle=context['focus_handle'] if source=='dialogue' else context[source]['snapshot_handle' if source=='state_before' else 'source_handle']
        event.update(uncertainty='moderate',context_handles=[handle])
        event['values'][6]='positive'
    record,_,_=run_compact(CompactDouble(mutation=mutate))
    assert record.status.value=='completed',record.failure
    appraisal=record.pipeline_trace.cognitive_turn.appraisal
    known=[d for f in appraisal.frames for d in f.dimensions if d.status=='known']
    assert len(known)==1
    assert len(appraisal.model_trace.provenance['event_allocations'])==1
    assert_replay(record)


def test_compact_attribution_summary_does_not_expose_packet_content():
    from app.ai.Cognitive.system.v0.trace_summary import _latency_projection
    record,_,_=run_compact()
    payload=record.pipeline_trace.model_dump(mode='json')
    payload['orchestration']['calls'][0]['attribution']['payload_sections']['private user text']=123
    summary=_latency_projection(payload,None)
    assert summary['protocol']==COMPACT_PROTOCOL and summary['model_call_count']==2
    assert summary['first_token_ms'] is None
    assert 'private user text' not in canonical(summary)
    assert summary['section_attribution'][1]['payload_sections']['decision_context.shared_context']>0


def test_model_context_conditioned_readings_reach_scenarios_without_second_bias():
    """A synthetic paired double proves wiring, not actual LLM sensitivity."""
    from app.ai.Cognitive.system.v0.live_shadow.compiler import compile_request,resolve_lineage
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineRequest
    capture=make_capture(contents=(('user','Nice one.'),))
    base=compile_request(capture,resolve_lineage(capture,None));traces=[]
    for trust in (0.1,0.9):
        biases=base.interpretation_biases.model_copy(update={'trust_prior':trust})
        request=CognitivePipelineRequest.model_validate(base.model_dump(mode='python')|dict(interpretation_biases=biases))
        def mutate(stage,raw,payload):
            if stage!='call_one':return
            reading=raw['readings'][0]
            reading['hypothesis_key']='interpretation.pragmatic.literal' if payload['turn_context']['biases']['values']['trust_prior']>0.5 else 'interpretation.pragmatic.indirect'
            reading['context_handles']=[payload['turn_context']['biases']['source_handle']]
        model=CompactDouble(mutation=mutate);pipeline=install(build_pipeline(capture.turn_id),model,protocol=COMPACT_PROTOCOL);model.pipeline=pipeline
        trace=pipeline.run(request);traces.append(trace)
        for hypothesis in trace.interpretation.hypotheses:
            assert not hypothesis.bias_adjustments
        model_hypotheses={h.hypothesis_id for h in trace.interpretation.hypotheses if h.dimension.value=='pragmatic_meaning'}
        assert model_hypotheses
        assert any(model_hypotheses & {ref for refs in s.hypothesis_refs.values() for ref in refs} for s in trace.interpretation.scenarios)
    assert traces[0].interpretation.semantic_frames==traces[1].interpretation.semantic_frames
    assert [h.hypothesis_key for h in traces[0].interpretation.hypotheses]!=[h.hypothesis_key for h in traces[1].interpretation.hypotheses]


def test_eight_turn_compact_shadow_state_continuity_and_replay():
    """Social wiring/lineage regression; not a mixed-task behavioral evaluation."""
    import uuid
    from test_cognitive_shadow_compiler import _as_predecessor
    user,conversation=str(uuid.uuid4()),str(uuid.uuid4());history=[];ids=[];previous=None
    for index in range(8):
        history.append(('user',f'Hi, turn {index+1}.'));ids.append(str(uuid.uuid4()))
        capture=make_capture(user_ref=user,conversation_ref=conversation,contents=tuple(history),message_ids=tuple(ids),
            previous_user_message_id=previous[1].focus_message_id if previous else None)
        model=CompactDouble();pipeline=install(build_pipeline(capture.turn_id),model,protocol=COMPACT_PROTOCOL);model.pipeline=pipeline
        record=observe(capture,_as_predecessor(*previous) if previous else None,
            ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=payload_digest(capture),started_at='2026-10-04T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
        assert record.status.value=='completed',record.failure
        assert_replay(record)
        if previous:
            assert record.compiled_request.previous_state==previous[0].pipeline_trace.cognitive_turn.dynamic_state_update.snapshot
        record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
        assert record.realization_status.value=='accepted_shadow',record.realization_failure
        assert len(model.calls)==2
        previous=(record,capture);history.append(('assistant','Hello.'));ids.append(str(uuid.uuid4()))


def test_authorized_dialogue_memory_is_projected_once_and_replays():
    from dialogue_memory_support import Script
    script=Script('compact-dialogue');script.exchange(1,'I prefer vegetarian dinner.','Understood.')
    capture=script.v03_capture('Hi')
    model=CompactDouble();pipeline=install(build_pipeline(capture.turn_id),model,protocol=COMPACT_PROTOCOL);model.pipeline=pipeline
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=payload_digest(capture),
        started_at='2026-10-04T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    assert record.status.value=='completed',record.failure
    assert model.calls[0][1]['turn_context']['dialogue_memory']['availability']=='available'
    assert model.calls[1][1]['decision_context']['shared_context']['dialogue_memory']==model.calls[0][1]['turn_context']['dialogue_memory']
    assert pipeline.two_call.captures[1].dialogue_context is not None
    assert_replay(record)


def test_shadow_service_factory_selects_compact_protocol(monkeypatch):
    from types import SimpleNamespace
    from app.services.cognitive_shadow import cognitive_pipeline
    from app.ai.Cognitive.system.v0.two_call import provider
    model=CompactDouble()
    monkeypatch.setattr(provider,'OpenAITwoCallModel',lambda **kwargs:model)
    settings=SimpleNamespace(cognitive_shadow_orchestration='two_call',cognitive_shadow_two_call_protocol=COMPACT_PROTOCOL,
        cognitive_shadow_semantic_parser_model='gpt-6-luna',openai_api_key=None,
        cognitive_shadow_semantic_parser_timeout_seconds=20,cognitive_shadow_orchestration_max_output_tokens=6000)
    capture=make_capture(contents=(('user','Hi'),));pipeline=cognitive_pipeline(capture.turn_id,settings);model.pipeline=pipeline
    assert pipeline.two_call.protocol==COMPACT_PROTOCOL
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=payload_digest(capture),
        started_at='2026-10-04T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2


def test_unknown_and_conflict_proposals_remain_typed_and_replayable():
    def mutate(stage,raw,payload):
        if stage!='call_one':return
        raw['unknowns']=[dict(subject_handle='q0',information='Unverified subjective intent.')]
        raw['conflicts']=[dict(subject_handle='e0',information='Two possible social readings.')]
    record,_,_=run_compact(CompactDouble(mutation=mutate))
    assert record.status.value=='completed',record.failure
    assert any(u.key=='compact_missing_information' for u in record.pipeline_trace.interpretation.unknowns)
    assert record.pipeline_trace.interpretation.contradictions
    assert_replay(record)
