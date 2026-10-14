"""General scope/source/authority regressions; no reserved evaluation fixtures."""
import copy
import jsonschema
import pytest

from test_cognitive_two_call_v1 import V1Double, run, event, reading, understanding, NULL, select, assert_replay
from test_character_opinions import _with_character, _view
from test_cognitive_followup_handoff import comparison_run
from app.ai.Cognitive.system.v0.two_call import v1
from app.ai.Cognitive.system.v0.two_call.provider import response_schema, prompt
from app.ai.Cognitive.system.v0.two_call.source_passages import passages


def scoped_opinion(scope):
    def build(payload):
        item = event('opinion_request', {'topic': {'kind': 'span', 'ref': payload['focus']['authorized_span_refs'][0]},
            'embedding': {'kind': 'enum', 'value': 'hypothetical'}},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')])
        item.update(act_scope=scope, subject_mode='hypothetical')
        return understanding(item)
    return build


@pytest.mark.parametrize('scope,answers', [('current', True), ('quoted', False), ('reported', False), ('imagined', False)])
def test_real_request_and_imagined_subject_are_independent(scope, answers):
    model = V1Double(scoped_opinion(scope), select('provide_information.answer') if answers else select('give_feedback.acknowledge'),
                     contribution=_view())
    record, _, _ = run(model, capture=_with_character((('user', 'What would you prefer in an imaginary reading room?'),)))
    assert record.pipeline_trace is not None, record.failure
    assert bool(record.pipeline_trace.orchestration.contributions) == answers
    [frame] = record.pipeline_trace.interpretation.semantic_frames
    assert frame.arguments['embedding'] == {'current': 'direct', 'imagined': 'hypothetical'}.get(scope, scope)
    assert_replay(record)


def test_every_menu_passage_is_in_bounds_and_bounded_without_quadratic_growth():
    text = ('A long clause without punctuation ' * 140) + 'but participation is never mandatory.'
    tokens = v1.focus_tokens(text)
    choices = passages(text)
    assert len(choices) < len(tokens)
    assert any('never mandatory' in p['text'] for p in choices)
    for choice in choices:
        start, end = v1.span_bounds(choice['span_ref'], tokens)
        assert choice['text'] == text[start:end]
        assert len(choice['text']) <= 600


def test_long_source_keeps_coverage_without_inventing_missing_context():
    from test_cognitive_followup_handoff import followup_builder
    conclusion = 'Participation remains optional, never compulsory'
    contents = (('user', 'What is your view?'),
                ('assistant', ('An extended explanation. ' * 110) + conclusion + '.'),
                ('user', 'Explain your final qualification.'))
    builder = followup_builder('Explain the final qualification about participation.',
                               [('recent_position', 1, conclusion)], operation='explain_position')
    record, _, _ = run(V1Double(builder, select('provide_information.answer'), contribution=_view()),
                       capture=_with_character(contents))
    source = record.pipeline_trace.orchestration.contributions[0].payload['followup_context']['sources'][0]
    assert source['source_message_characters'] == len(contents[1][1])
    assert source['has_omitted_prefix'] is True
    assert source['has_omitted_suffix'] is False
    assert 'source_message_text' not in source
    assert source['text'] == conclusion
    assert_replay(record)


def test_schema_rejects_numeric_overrun_and_speaker_laundering():
    record, _, _ = comparison_run()
    call = record.pipeline_trace.orchestration.calls[0]
    schema = response_schema('call_one', call.payload)
    raw = v1.plain_json(call.proposal)
    raw.setdefault('recall_answer', None)
    jsonschema.validate(raw, schema)
    bad = copy.deepcopy(raw)
    bad['events'][0]['followup']['sources'][0]['citation']['span_ref'] = 'span:0-999'
    assert not jsonschema.Draft202012Validator(schema).is_valid(bad)
    bad = copy.deepcopy(raw)
    source = bad['events'][0]['followup']['sources'][0]
    source['citation'] = dict(kind='dialogue_span', message_ref='message:0',
        span_ref=call.payload['dialogue_context']['messages'][0]['citation_passages'][0]['span_ref'])
    assert not jsonschema.Draft202012Validator(schema).is_valid(bad)
    # Runtime checks remain necessary even if an adapter bypasses strict output.
    with pytest.raises(ValueError, match='assistant speech'):
        v1.validate_understanding(bad, call.payload, call.reference_table)


def test_dialogue_reasoning_is_available_without_claiming_external_knowledge():
    def contribution(payload):
        result = _view('My recommendation is unchanged; the survey strengthens my support.')(payload)
        result['question_kind'] = 'dialogue_reasoning'
        return result
    from test_cognitive_followup_handoff import followup_builder, CONTENTS, QUESTION, SOURCES
    record, _, _ = run(V1Double(followup_builder(QUESTION, SOURCES), select('provide_information.answer'),
        contribution=contribution), capture=_with_character(CONTENTS))
    assert record.pipeline_trace is not None, record.failure
    call = record.pipeline_trace.orchestration.contributions[0]
    assert 'dialogue_reasoning' in response_schema('contribution', call.payload)['properties']['question_kind']['enum']
    assert 'contribution_v1_10' not in prompt('contribution', call.payload)  # prompt text, not filename
    from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of
    assert any(getattr(item, 'predicate', '') == 'dialogue.reasoned_account'
               for item in requirement_content_of(record.pipeline_trace))
    assert_replay(record)


def test_old_scope_and_schema_are_not_reinterpreted(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.9')
    record, _, _ = run()
    call = record.pipeline_trace.orchestration.calls[0]
    assert 'act_scope' not in str(response_schema('call_one', call.payload))
    assert all('citation_passages' not in m for m in call.payload['dialogue_context']['messages'])
    assert_replay(record)
