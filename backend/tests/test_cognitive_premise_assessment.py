"""Grounded public-position checks; no reserved behavioral evaluation inputs."""
import jsonschema
import pytest
from pydantic import ValidationError
from test_cognitive_followup_handoff import comparison_run, followup_builder, CONTENTS, QUESTION, SOURCES
from test_cognitive_two_call_v1 import V1Double, run, select, assert_replay
from test_character_opinions import _with_character, _view
from app.ai.Cognitive.system.v0.two_call.premise import PremiseAssessment, validate_assessment
from app.ai.Cognitive.system.v0.two_call.provider import response_schema, output_type, prompt

def assessment(**updates):
    return dict(premise='The earlier position required everyone to join.', status='contradicted',
                position_refs=['prior:1'], relation='stronger_support',
                reason='The recorded statement says the chat should not be mandatory.') | updates

def proposal(payload):
    return _view('I still oppose mandatory chat; the newcomers strengthen my support for optional chat.')(payload) | dict(
        schema_version='contribution-proposal-v3', premise_assessment=assessment())

def new_run(contribution=proposal):
    model=V1Double(followup_builder(QUESTION,SOURCES),select('provide_information.answer'),contribution=contribution)
    return run(model,capture=_with_character(CONTENTS))

def test_assessment_uses_exact_older_speech_and_is_preserved_in_replay():
    record,_,_=new_run()
    assert record.pipeline_trace is not None, record.failure
    capture=record.pipeline_trace.orchestration.contributions[0]
    assert capture.payload['schema_version']=='contribution-input-v1.4'
    statements=capture.payload['premise_context']['assistant_statements']
    prior=next(s for s in statements if s['ref']=='prior:1')
    assert prior['text']==CONTENTS[1][1]
    assert prior['text'] not in [m['text'] for m in capture.payload['conversation_window']]
    assert list(capture.compiled_proposal['recorded_speech'])==[prior]
    assert capture.compiled_proposal['assessment']['status']=='contradicted'
    schema=response_schema('contribution',capture.payload)
    assert list(schema['properties']).index('premise_assessment')<list(schema['properties']).index('view')
    jsonschema.validate(capture.model_dump(mode='json')['proposal'],schema)
    assert 'BEFORE evaluating a change' in prompt('contribution',capture.payload)
    assert_replay(record)

@pytest.mark.parametrize('relation',['unchanged','stronger_support','exception','revised','uncertain'])
def test_false_premise_does_not_freeze_the_current_judgment(relation):
    # Evidence validation is deliberately independent of the current opinion.
    value=PremiseAssessment.model_validate(assessment(relation=relation))
    context={'assistant_statements':[dict(ref='prior:1',message_ref='message:1',text=CONTENTS[1][1],coverage='full_message')]}
    assert validate_assessment(value,context)['assessment']['relation']==relation

def test_missing_evidence_can_remain_uncertain_without_fabricating_a_position():
    value=PremiseAssessment.model_validate(assessment(status='insufficient_evidence',position_refs=[],relation='uncertain'))
    assert validate_assessment(value,{'assistant_statements':[]})['recorded_speech']==[]
    with pytest.raises(ValidationError):PremiseAssessment.model_validate(assessment(position_refs=[]))

def test_foreign_or_user_statement_is_not_accepted_as_assistant_position():
    record,_,_=new_run();capture=record.pipeline_trace.orchestration.contributions[0]
    bad=capture.model_dump(mode="json")["proposal"];bad['premise_assessment']['position_refs']=['prior:0']
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(bad,response_schema('contribution',capture.payload))
    value=output_type('contribution',capture.payload).model_validate(bad)
    with pytest.raises(ValueError,match='not supplied'):
        validate_assessment(value.premise_assessment,capture.payload['premise_context'])

def test_capture_rejects_rewritten_evidence_or_assessment():
    record,_,_=new_run();capture=record.pipeline_trace.orchestration.contributions[0]
    for key in ('recorded_speech','assessment'):
        data=capture.model_dump(mode='json')
        if key=='recorded_speech':data['compiled_proposal'][key][0]['text']='I required everyone to join.'
        else:data['compiled_proposal'][key]['status']='supported'
        with pytest.raises(ValidationError,match='compiled proposal differs'):
            type(capture).model_validate(data)

def test_version_1_10_keeps_its_original_schema_prompt_and_replay():
    record,_,_=run(V1Double(followup_builder(QUESTION,SOURCES),select('provide_information.answer'),contribution=_view()),
                   capture=_with_character(CONTENTS),registry='argument-kind-registry-v1.10')
    assert record.pipeline_trace is not None,record.failure
    capture=record.pipeline_trace.orchestration.contributions[0]
    assert capture.payload['schema_version']=='contribution-input-v1.3'
    assert 'premise_context' not in capture.payload
    assert 'premise_assessment' not in response_schema('contribution',capture.payload)['properties']
    assert 'Recorded-position premise assessment' not in prompt('contribution',capture.payload)
    assert_replay(record)

@pytest.mark.parametrize('status', ['no_asserted_change', 'contradicted'])
@pytest.mark.parametrize('relation', ['unchanged', 'stronger_support', 'exception', 'revised', 'uncertain'])
def test_premise_verdict_and_position_relation_are_independent(status, relation):
    value = PremiseAssessment.model_validate(assessment(status=status, relation=relation,
        premise=None if status == 'no_asserted_change' else 'The earlier position was different.'))
    context = {'assistant_statements': [dict(ref='prior:1', text=CONTENTS[1][1])]}
    assert validate_assessment(value, context)['assessment']['relation'] == relation


def test_new_guidance_is_versioned_without_changing_old_replay():
    from app.ai.Cognitive.system.v0.two_call.premise import INDEPENDENT_ASSESSMENT_VERSION
    for registry, new in [('argument-kind-registry-v1.11', False), ('argument-kind-registry-v1.12', True)]:
        record, _, _ = run(V1Double(followup_builder(QUESTION, SOURCES),
            select('provide_information.answer'), contribution=proposal),
            capture=_with_character(CONTENTS), registry=registry)
        assert record.pipeline_trace is not None, record.failure
        call = record.pipeline_trace.orchestration.contributions[0]
        assert (call.payload['premise_context']['version'] == INDEPENDENT_ASSESSMENT_VERSION) == new
        assert ('Independent assessment axes' in prompt('contribution', call.payload)) == new
        schema = response_schema('contribution', call.payload)
        relation = schema['properties']['premise_assessment']['properties']['relation']
        assert ('description' in relation) == new
        assert_replay(record)
