"""General regressions from long-dialogue failures, without topic-specific fixes."""
import pytest
from test_cognitive_premise_assessment import new_run
from test_cognitive_two_call_v1 import assert_replay
from app.ai.Cognitive.system.v0.two_call.text_surface import model_surface
from app.ai.Cognitive.system.v0.realization_v0.validator import answer_negated
from app.ai.Cognitive.system.v0.realization_v0.model_protocol import response_json_schema


def test_policy_sees_actual_answer_and_contribution_keeps_resolved_question():
    record, _, _ = new_run()
    assert record.pipeline_trace is not None, record.failure
    trace = record.pipeline_trace.orchestration
    contribution = trace.contributions[0]
    assert contribution.payload['referent_contract']['question'] == contribution.payload['question_topic']
    policy = trace.calls[-1].payload
    assert policy['policy_guidance'] == 'policy-guidance-v1.6'
    answers = [c for c in policy['eligible_candidates'] if c['action_key'] == 'provide_information.answer']
    assert answers and all(c['answer_content'] for c in answers)
    assert 'I still oppose mandatory chat' in str(answers)
    assert_replay(record)


@pytest.mark.parametrize('text,negated', [
    ('I advocated candor, not a kind lie.', False),
    ('I said candor—not a kind lie.', False),
    ('Not a kind lie, but candor.', False),
    ('I did not advocate candor.', True),
    ('Candor was not my answer.', True),
    ('I never chose candor, but I did choose tact.', True),
    ('I chose tact, not candor.', True),
])
def test_negation_belongs_to_answer_not_rejected_alternative(text, negated):
    assert answer_negated(text, ('candor',), ('candor',)) == negated


def test_model_surface_unifies_value_and_anchor_without_touching_quotes():
    assert model_surface('A\u200c clear\nanswer.') == 'A clear answer.'
    assert model_surface('café — kind, not false') == 'café — kind, not false'
    with pytest.raises(ValueError):
        model_surface('\u200c')
    with pytest.raises(ValueError):
        model_surface('bad\x00text')


def test_formatted_model_view_projects_and_replays_with_raw_proposal_preserved():
    from test_cognitive_premise_assessment import proposal
    def formatted(payload):
        value = proposal(payload)
        value['view']['text'] = 'I still\u200c prefer\noptional participation.'
        return value
    record, _, _ = new_run(contribution=formatted)
    assert record.pipeline_trace is not None, record.failure
    trace = record.pipeline_trace
    assert '\u200c' in trace.orchestration.contributions[0].proposal['view']['text']
    assert any(item.arguments[0].value == 'I still prefer optional participation.'
               for item in trace.requirement_resolution.realization_content if hasattr(item, 'arguments'))
    assert_replay(record)


def test_alignment_schema_disallows_empty_excerpts_without_weakening_older_schema():
    import jsonschema
    for name in ('move_alignments', 'content_alignments'):
        new = response_json_schema('conversational-v4')['properties'][name]['items']['properties']['excerpt']
        old = response_json_schema('conversational-v3')['properties'][name]['items']['properties']['excerpt']
        assert not jsonschema.Draft202012Validator(new).is_valid('')
        assert jsonschema.Draft202012Validator(old).is_valid('')


def test_recall_keeps_answer_but_not_user_criticism_as_assertable_subject():
    from test_cognitive_continuity_regression import recall, requirement_content_of
    from test_cognitive_two_call_v1 import V1Double, run, select
    question = 'Your judgment was poor. What was your preference?'
    contents = (('assistant', 'I prefer flexible schedules.'), ('user', question))
    for registry, expected in [('argument-kind-registry-v1.12', question),
                               ('argument-kind-registry-v1.13', 'my recorded statement')]:
        record, _, _ = run(V1Double(recall('known', 0, 'flexible schedules'),
                                   select('provide_information.answer')),
                           contents=contents, registry=registry)
        assert record.pipeline_trace is not None, record.failure
        [content] = requirement_content_of(record.pipeline_trace)
        assert content.subject.surface_anchor == expected
        assert content.arguments[0].value == 'flexible schedules'
        assert_replay(record)


@pytest.mark.parametrize('text,denied', [
    ('You mentioned hiking, not painting.', False),
    ('You did not mention hiking.', True),
    ('Hiking was not what you mentioned.', True),
])
def test_scoped_negation_through_real_validator(text, denied):
    from test_realization_answer_fidelity import A3, _scripted, _validate
    from app.ai.Cognitive.system.v0.contracts.realization import RealizationRequestV0
    request = RealizationRequestV0.model_validate(A3['request'])
    candidate = _scripted(request, text)
    codes = {v.code.value for v in _validate('conversational-v4', request, candidate).violations}
    assert ('answer_contradicted' in codes) == denied


def test_provider_schema_matches_existing_relation_evidence_requirement():
    import jsonschema
    from app.ai.Cognitive.system.v0.two_call.premise import assessment_schema
    context = dict(version='recorded-position-premise-v3', assistant_statements=[dict(ref='prior:0')])
    schema = assessment_schema(context)
    value = dict(premise=None, status='no_asserted_change', position_refs=[],
                 relation='unchanged', reason='The preference is unchanged.')
    assert not jsonschema.Draft202012Validator(schema).is_valid(value)
    assert jsonschema.Draft202012Validator(schema).is_valid(dict(value, position_refs=['prior:0']))
    assert jsonschema.Draft202012Validator(schema).is_valid(dict(value, relation='uncertain'))
    old = assessment_schema(dict(context, version='recorded-position-premise-v2'))
    assert jsonschema.Draft202012Validator(old).is_valid(value)
