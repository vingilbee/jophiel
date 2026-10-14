"""October 12 closed-loop failures: routing and source-linked stage handoff.

Scripted model outputs verify plumbing, not live-model comprehension quality.
"""
import copy

import jsonschema
import pytest

from test_character_opinions import _with_character, _view
from test_cognitive_continuity_regression import citation, unresolved_followup
from test_cognitive_two_call_v1 import (
    NULL, V1Double, assert_replay, event, reading, run, select, understanding,
)
from app.ai.Cognitive.system.v0.two_call import v1
from app.ai.Cognitive.system.v0.two_call.provider import prompt, response_schema


def followup_builder(question, sources, *, operation='compare_positions', predicate='elaboration_request'):
    def build(payload):
        args = ({'topic': NULL, 'embedding': NULL} if predicate == 'opinion_request' else
                {'referent_phrase': NULL, 'embedding': NULL,
                 'elaboration_kind': {'kind': 'enum', 'value': 'explanation'}})
        item = event(predicate, args, payload['focus']['authorized_span_refs'][:1],
                     [reading('interpretation.goal.obtain_information')])
        item['followup'] = dict(operation=operation, resolved_question=question,
                                sources=[dict(role=role, citation=citation(payload, index, phrase))
                                         for role, index, phrase in sources])
        return understanding(item)
    return build


CONTENTS = (
    ('user', 'Should the book club start with ten minutes of chat?'),
    ('assistant', 'Chat can welcome people, but it should not be mandatory.'),
    ('user', 'Would you make a rule?'),
    ('assistant', 'No, people should have a choice.'),
    ('user', 'Some prefer discussing the book immediately.'),
    ('assistant', 'That preference matters too.'),
    ('user', 'The newcomers say chatting helps them join in.'),
    ('assistant', 'That gives it a concrete purpose.'),
    ('user', 'The group wants an optional chat.'),
    ('assistant', 'I support optional chats for this group.'),
    ('user', 'Explain what changed and what stayed the same in your position.'),
)
QUESTION = 'Explain the earlier non-mandatory chat position versus support for optional chats after newcomers described their benefit.'
SOURCES = [('earlier_position', 1, 'Chat can welcome people, but it should not be mandatory'),
           ('recent_position', 9, 'I support optional chats for this group'),
           ('new_information', 6, 'The newcomers say chatting helps them join in')]


def comparison_run(builder=None):
    model = V1Double(builder or followup_builder(QUESTION, SOURCES), select('provide_information.answer'),
                     contribution=_view('I still oppose mandatory chat; the newcomers give optional chat a clearer purpose.'))
    return run(model, capture=_with_character(CONTENTS))


def test_old_position_is_forwarded_outside_recent_window_and_replays():
    record, _, model = comparison_run()
    assert record.pipeline_trace is not None, record.failure
    frame = record.pipeline_trace.interpretation.semantic_frames[0]
    assert frame.predicate == 'elaboration_request'
    assert frame.arguments['referent_phrase'] == QUESTION
    capture = record.pipeline_trace.orchestration.contributions[0]
    payload = capture.payload
    assert CONTENTS[1][1] not in [m['text'] for m in payload['conversation_window']]
    assert payload['question_topic'] == QUESTION
    context = payload['followup_context']
    assert context['operation'] == 'compare_positions'
    assert [s['text'] for s in context['sources']] == [s[2] for s in SOURCES]
    assert context['sources'][0]['speaker'] == 'assistant'
    assert context['sources'][0]['message_ref'] == 'message:1'
    assert 'followup_context' in prompt('contribution', payload)
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == ['provide_information.answer']
    # Strict provider schema and accepted-understanding observation agree.
    one = record.pipeline_trace.orchestration.calls[0]
    raw = v1.plain_json(one.proposal)
    raw.setdefault('recall_answer', None)
    jsonschema.validate(raw, response_schema('call_one', one.payload))
    mind, _ = v1.accepted_mind(raw, one.payload, one.reference_table)
    assert any(a.value == QUESTION for a in mind.observations[0].arguments)
    from app.ai.Cognitive.system.v0.two_call.v1_training import project_training_example
    projected = project_training_example(record)
    assert 'compare_positions' in str(projected)
    assert 'Chat can welcome people' in str(projected)
    assert_replay(record)


def test_exception_question_keeps_meaning_and_does_not_require_artifact():
    contents = (('user', 'Do you like grand names?'), ('assistant', 'I prefer simple names.'),
                ('user', 'I like the grand name because it makes me laugh. Is that a good reason to make an exception?'))
    question = 'Does amusement justify an exception to the preference for simple names?'
    builder = followup_builder(question, [('recent_position', 1, 'I prefer simple names'),
                                          ('new_information', 2, 'it makes me laugh')],
                               operation='continue_opinion', predicate='opinion_request')
    record, _, _ = run(V1Double(builder, select('provide_information.answer'), contribution=_view()),
                       capture=_with_character(contents))
    assert record.pipeline_trace is not None, record.failure
    payload = record.pipeline_trace.orchestration.contributions[0].payload
    assert payload['question_topic'] == question
    assert payload['followup_context']['operation'] == 'continue_opinion'
    assert_replay(record)


@pytest.mark.parametrize('mutation', ['foreign', 'out_of_bounds', 'user_position', 'duplicate', 'artifact', 'blank'])
def test_invalid_followup_sources_are_rejected(mutation):
    record, _, _ = comparison_run()
    one = record.pipeline_trace.orchestration.calls[0]
    raw = v1.plain_json(one.proposal)
    event = raw['events'][0]
    followup = event['followup']
    if mutation == 'foreign':
        followup['sources'][0]['citation']['message_ref'] = 'message:999'
    elif mutation == 'out_of_bounds':
        followup['sources'][0]['citation']['span_ref'] = 'span:999'
    elif mutation == 'user_position':
        followup['sources'][0]['citation'] = citation(one.payload, 0, 'Should the book club')
    elif mutation == 'duplicate':
        followup['sources'].append(copy.deepcopy(followup['sources'][0]))
    elif mutation == 'artifact':
        event['predicate_key'] = 'collaborative_content_request'
    else:
        followup['resolved_question'] = ' '
    with pytest.raises(ValueError):
        v1.validate_understanding(raw, one.payload, one.reference_table)


def test_genuine_ambiguity_still_clarifies_without_followup_context():
    record, _, _ = run(V1Double(unresolved_followup, select('seek_information.clarify')),
                       text='What about that?')
    assert record.pipeline_trace is not None, record.failure
    assert not record.pipeline_trace.orchestration.contributions
    assert_replay(record)


def test_old_registry_does_not_offer_or_accept_new_handoff(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.8')
    record, _, _ = run()
    assert_replay(record)
    one = record.pipeline_trace.orchestration.calls[0]
    assert 'resolved_question' not in str(response_schema('call_one', one.payload))
    assert 'Source-linked follow-ups' not in prompt('call_one', one.payload)
    invalid, _, _ = comparison_run()
    assert invalid.pipeline_trace is None


def test_non_followup_events_use_json_null_not_unavailable_sentinel():
    record, _, _ = run()
    one = record.pipeline_trace.orchestration.calls[0]
    schema = response_schema('call_one', one.payload)
    branches = schema['properties']['events']['items']['anyOf']
    for branch in branches:
        predicate = branch['properties']['predicate_key']['enum'][0]
        if predicate not in ('opinion_request', 'elaboration_request'):
            assert branch['properties']['followup'] == {'type': 'null'}


def test_context_does_not_leak_into_unrelated_event():
    from types import SimpleNamespace
    from app.ai.Cognitive.system.v0.two_call.followup import contribution_context
    record, pipeline, _ = comparison_run()
    frame = SimpleNamespace(predicate='opinion_request', arguments={'topic': 'An unrelated taste question'})
    assert contribution_context(pipeline.two_call, frame)['followup_context'] is None


def test_future_message_cannot_be_used_as_support():
    record, _, _ = comparison_run()
    one = record.pipeline_trace.orchestration.calls[0]
    raw, payload, entries = map(v1.plain_json, (one.proposal, one.payload, one.reference_table))
    future = dict(ref='message:99', speaker='assistant', text='Future opinion', authority='context_only')
    payload['dialogue_context']['messages'].append(future)
    entries['message:99'] = dict(kind='message', canonical_ref='future', speaker='assistant',
                                  text='Future opinion', citable=True)
    raw['events'][0]['followup']['sources'][0]['citation'] = dict(
        kind='dialogue_span', message_ref='message:99', span_ref='span:0-1')
    with pytest.raises(ValueError, match='differs from supplied source'):
        v1.validate_understanding(raw, payload, entries)
