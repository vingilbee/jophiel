"""Reference normalization regressions; fixtures make no external requests."""
import copy
import pytest
from app.ai.Cognitive.system.v0.two_call.compact import ReferenceTable,compile_response,response_failure_code
from test_compact_two_call import CompactDouble,run_compact,assert_replay
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0


def choice_fixture():
    refs=ReferenceTable()
    goal=refs.add('canonical-goal','goal');target=refs.add('canonical-proposition','reference');source=refs.add('canonical-evidence','reference')
    choice=dict(handle='b0',action_key='provide_information.explain',goal_refs=[goal],
        targets=[dict(role='primary',target_type='proposition',ref=target,revision_ref=None)],
        parameters=dict(basis_refs=[source],recipient_scope='user'))
    payload=dict(decision_context=dict(choices=[choice],templates=[],content_requests=[]))
    raw=dict(content_ops=[],moves=[dict(choice_handle='b0',template_handle=None,goal_handles=[goal],
        target_bindings=[dict(role='primary',target_type='proposition',existing_ref=target,revision_ref=None,
            content_request_handle=None,object_kind=None,item_handle=None)],segment_handles=['s0'],
        parameters=[dict(name='basis_refs',value=[source]),dict(name='recipient_scope',value='user')])],
        segments=[dict(handle='s0',text='Explanation.',content_handles=[],claim_bindings=[])],inquiries=[])
    return refs,payload,raw


def test_supplied_local_reference_parameter_and_targets_expand_consistently():
    refs,payload,raw=choice_fixture()
    result=compile_response(raw,payload,refs.entries)
    assert result.behavior_proposal[0].goal_refs==('canonical-goal',)
    assert result.behavior_proposal[0].target_bindings[0].existing_ref=='canonical-proposition'
    assert result.response_candidate.text=='Explanation.'


def test_partial_readonly_parameter_echo_keeps_code_owned_choice():
    refs,payload,raw=choice_fixture()
    raw['moves'][0]['parameters']=raw['moves'][0]['parameters'][:1]
    echoed=compile_response(raw,payload,refs.entries)
    raw['moves'][0]['parameters']=[];raw['moves'][0]['target_bindings']=[]
    assert echoed==compile_response(raw,payload,refs.entries)


def test_reference_parameter_echo_without_target_echo_is_not_compared_to_canonical_id():
    refs,payload,raw=choice_fixture()
    raw['moves'][0]['target_bindings']=[]
    assert compile_response(raw,payload,refs.entries).behavior_proposal[0].target_bindings[0].existing_ref=='canonical-proposition'


@pytest.mark.parametrize('mutation',['foreign_parameter','changed_parameter','unknown_parameter','duplicate_parameter',
    'changed_target','changed_type','future_target','duplicate_target'])
def test_normalization_never_authorizes_parameter_or_target_changes(mutation):
    refs,payload,raw=choice_fixture();move=raw['moves'][0]
    if mutation=='foreign_parameter':move['parameters'][0]['value']=['r9999']
    if mutation=='changed_parameter':move['parameters'][0]['value']=[payload['decision_context']['choices'][0]['goal_refs'][0]]
    if mutation=='unknown_parameter':move['parameters'].append(dict(name='new_permission',value=True))
    if mutation=='duplicate_parameter':move['parameters'].append(copy.deepcopy(move['parameters'][0]))
    if mutation=='changed_target':move['target_bindings'][0]['existing_ref']=payload['decision_context']['choices'][0]['goal_refs'][0]
    if mutation=='changed_type':move['target_bindings'][0]['target_type']='goal'
    if mutation=='future_target':move['target_bindings'][0].update(existing_ref=None,content_request_handle=0,object_kind='content',item_handle=0)
    if mutation=='duplicate_target':move['target_bindings'].append(copy.deepcopy(move['target_bindings'][0]))
    with pytest.raises(ValueError):compile_response(raw,payload,refs.entries)


def test_unchanged_grounded_target_echo_completes_two_calls_realization_and_replay():
    def echo(stage,raw,payload):
        if stage!='call_two':return
        move=raw['moves'][0];choice=next(c for c in payload['decision_context']['choices'] if c['handle']==move['choice_handle'])
        move['target_bindings']=[dict(role=t['role'],target_type=t['target_type'],existing_ref=t['ref'],revision_ref=t.get('revision_ref'),
            content_request_handle=None,object_kind=None,item_handle=None) for t in choice['targets']]
        move['parameters']=[dict(name=k,value=v) for k,v in choice['parameters'].items()]
    record,_,model=run_compact(CompactDouble(mutation=echo))
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    assert_replay(record)
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure


def test_specific_failure_diagnostic_is_content_free():
    def mutate(stage,raw,payload):
        if stage=='call_two':raw['moves'][0]['goal_handles']=['r9999']
    record,_,model=run_compact(CompactDouble(mutation=mutate))
    assert record.status.value=='failed' and len(model.calls)==2
    assert '.response_reference_compilation.foreign_local_reference.' in record.failure.code
    assert 'r9999' not in record.failure.code
    assert response_failure_code(ValueError('private user content'))=='invalid_reference_contract'


def test_duplicate_and_missing_content_requests_fail_before_claim_lookup():
    refs,payload,raw=choice_fixture()
    output=dict(title=None,operations=[],content_items=[],inquiries=[])
    payload['decision_context']['content_requests']=[{}]
    with pytest.raises(ValueError,match='content request coverage mismatch'):compile_response(raw,payload,refs.entries)
    raw['content_ops']=[dict(request_handle=0,output=output)]*2
    with pytest.raises(ValueError,match='duplicate content request'):compile_response(raw,payload,refs.entries)


@pytest.mark.parametrize('field', ['content_handles', 'claim_bindings', 'inquiries'])
def test_provider_schema_disallows_generated_references_without_content_requests(field):
    from jsonschema import Draft202012Validator
    from app.ai.Cognitive.system.v0.two_call.compact_provider import compact_schema
    record,pipeline,model=run_compact()
    assert record.status.value=='completed'
    schema=compact_schema('call_two',model.calls[1][1])
    raw=pipeline.two_call.captures[1].model_dump(mode='json')['proposal']
    validator=Draft202012Validator(schema)
    validator.validate(raw)
    if field=='content_handles':raw['segments'][0][field]=['content:0:0']
    elif field=='claim_bindings':raw['segments'][0][field]=[dict(content_handle='r0',source_handles=[])]
    else:raw[field]=[dict(request_handle=0,item_handle=0,target_handle='r0',asked_about='dinner',timing='optional')]
    assert list(validator.iter_errors(raw))


@pytest.mark.parametrize('legacy_sources', [False, True])
def test_generated_claim_binding_completes_content_realization_and_replay(legacy_sources):
    from jsonschema import Draft202012Validator
    from app.ai.Cognitive.system.v0.two_call.compact_provider import compact_schema
    from simulated_authority_support import DINNER
    def bind(stage,raw,payload):
        if stage!='call_two':return
        handles=[];claims=[]
        for op in raw['content_ops']:
            for index,item in enumerate(op['output']['content_items']):
                handle=f"content:{op['request_handle']}:{index}"
                handles.append(handle)
                claims.append(dict(content_handle=handle,source_handles=item['source_refs'] if legacy_sources else []))
        assert claims
        raw['segments'][0].update(content_handles=handles,claim_bindings=claims)
        schema=compact_schema(stage,payload)
        # Exercise the specialized reference fields without requiring unrelated
        # synthetic artifact fields to satisfy the live provider vocabulary.
        binding_schema=schema['$defs']['ClaimBinding']
        for claim in claims:
            validator=Draft202012Validator(binding_schema)
            if legacy_sources:assert list(validator.iter_errors(claim))
            else:validator.validate(claim)
    record,_,model=run_compact(CompactDouble(artifact=True,mutation=bind),text=DINNER['turns'][1]['user'])
    assert record.status.value=='completed',record.failure
    assert len(model.calls)==2
    assert_replay(record)
    record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
    assert record.realization_status.value=='accepted_shadow',record.realization_failure


@pytest.mark.parametrize('handle', ['r0','content:999:0','content:0:999'])
def test_foreign_claim_is_still_rejected(handle):
    from simulated_authority_support import DINNER
    def bind(stage,raw,payload):
        if stage=='call_two':
            raw['segments'][0]['claim_bindings']=[dict(content_handle=handle,source_handles=[])]
    record,_,model=run_compact(CompactDouble(artifact=True,mutation=bind),text=DINNER['turns'][1]['user'])
    assert record.status.value=='failed' and len(model.calls)==2
    assert '.response_reference_compilation.foreign_claim_content.' in record.failure.code


def test_duplicate_source_selection_reproduction_and_single_source_owner():
    refs,payload,raw=choice_fixture()
    first=refs.add('canonical-source-one');second=refs.add('canonical-source-two')
    payload['decision_context']['content_requests']=[dict(sources=[dict(ref=first),dict(ref=second)])]
    item=dict(role='suggestion',statement='A dinner suggestion.',anchors=['dinner'],
        content_class='generated_proposal',claim_kind='uncertain_suggestion',
        subject_ids=[],source_refs=[first],numbers=[])
    raw['content_ops']=[dict(request_handle=0,output=dict(title=None,operations=[],content_items=[item],inquiries=[]))]
    claim=dict(content_handle='content:0:0',source_handles=[second])
    raw['segments'][0]['claim_bindings']=[claim]
    # Exact reported class: request permits the source, but this item does not.
    with pytest.raises(ValueError,match='claim source does not support proposed content'):
        compile_response(raw,payload,refs.entries)
    claim['source_handles']=[]
    compiled=compile_response(raw,payload,refs.entries)
    assert compiled.content_proposal[0].output.content_items[0].source_refs==('canonical-source-one',)
    claim['source_handles']=['r99999']
    with pytest.raises(ValueError,match='foreign claim source'):
        compile_response(raw,payload,refs.entries)


@pytest.mark.parametrize('mutation', ['empty_sources','foreign_sources','verified_claim'])
def test_inherited_claim_support_does_not_bypass_content_grounding(mutation):
    from simulated_authority_support import DINNER
    def bind(stage,raw,payload):
        if stage!='call_two':return
        item=raw['content_ops'][0]['output']['content_items'][0]
        raw['segments'][0]['claim_bindings']=[dict(content_handle='content:0:0',source_handles=[])]
        if mutation=='empty_sources':item['source_refs']=[]
        elif mutation=='foreign_sources':item['source_refs']=['r99999']
        else:item['claim_kind']='verified_fact'
    record,_,model=run_compact(CompactDouble(artifact=True,mutation=bind),text=DINNER['turns'][1]['user'])
    assert record.status.value=='failed' and len(model.calls)==2
    assert record.realization_status.value=='not_requested'
