"""Interpreted dialogue relationships: the fold, Call 1 registry v1.7 updates, and v1.8 recall support."""

from __future__ import annotations

import pytest
from test_cognitive_two_call_v1 import V1Double, assert_replay, run, social

from app.ai.Cognitive.system.v0.contracts.dialogue_interpretation import (
    DialogueInterpretationRecord, InterpretedSpan, InterpretedUpdate,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.interpreted import fold
from app.ai.Cognitive.system.v0.two_call import v1 as V1

# ---------------------------------------------------------------------------
# Fold
# ---------------------------------------------------------------------------


def _span(message: str, text: str, source: str) -> InterpretedSpan:
    start = source.index(text)
    return InterpretedSpan(message_ref=message, start=start, end=start + len(text), text=text)


A1 = "Saturday is painting or hiking."
A2 = "I keep leaning toward the first one."
A4 = "Actually, I meant hiking, not painting."


def _records():
    painting, hiking = _span('m1', 'painting', A1), _span('m1', 'hiking', A1)
    return [
        DialogueInterpretationRecord(source_message_ref='m4', source_conversation_sequence=7, updates=(
            InterpretedUpdate(kind='correction', spans=(painting, _span('m4', 'hiking', A4))),)),
        DialogueInterpretationRecord(source_message_ref='m1', source_conversation_sequence=1, updates=(
            InterpretedUpdate(kind='option_set', spans=(painting, hiking)),)),
        DialogueInterpretationRecord(source_message_ref='m2', source_conversation_sequence=3, updates=(
            InterpretedUpdate(kind='same_referent', spans=(_span('m2', 'the first one', A2), painting)),)),
    ]


def test_fold_orders_by_conversation_and_marks_corrected_values_superseded():
    state = fold(_records(), before_sequence=9)
    assert [option.span.text for option in state.option_sets[0].options] == ['painting', 'hiking']
    assert [option.superseded for option in state.option_sets[0].options] == [True, False]
    link = state.referent_links[0]
    assert (link.mention.span.text, link.referent.span.text, link.referent.superseded) == ('the first one', 'painting', True)
    assert (state.corrections[0].replaced.text, state.corrections[0].replacement.text) == ('painting', 'hiking')


def test_fold_never_sees_the_focus_or_later_and_is_order_independent():
    before = fold(_records(), before_sequence=7)
    assert not before.corrections and not any(o.superseded for o in before.option_sets[0].options)
    assert fold(reversed(_records()), before_sequence=9) == fold(_records(), before_sequence=9)


def test_records_reject_updates_that_do_not_cite_their_own_message():
    with pytest.raises(ValueError):
        DialogueInterpretationRecord(source_message_ref='m2', source_conversation_sequence=3, updates=(
            InterpretedUpdate(kind='option_set', spans=(_span('m1', 'painting', A1), _span('m1', 'hiking', A1))),))
    with pytest.raises(ValueError):
        InterpretedSpan(message_ref='m1', start=0, end=3, text='painting')


# ---------------------------------------------------------------------------
# Call 1 registry v1.7
# ---------------------------------------------------------------------------

CONVERSATION = (('user', "I'm deciding whether to spend Saturday painting or hiking."),
                ('assistant', 'Both sound good.'),
                ('user', 'I keep leaning toward the first one.'))


def dspan(payload, message_ref, phrase):
    message = next(m for m in payload['dialogue_context']['messages'] if m['ref'] == message_ref)
    start = message['text'].index(phrase)
    tokens = V1.focus_tokens(message['text'])
    inside = [i for i, (s, e, _) in enumerate(tokens) if s >= start and e <= start + len(phrase)]
    ref = f'span:{inside[0]}' if inside[0] == inside[-1] else f'span:{inside[0]}-{inside[-1]}'
    return dict(kind='dialogue_span', message_ref=message_ref, span_ref=ref)


def _run(updates):
    def one(payload):
        raw = social(payload)
        raw['dialogue_updates'] = updates(payload)
        return raw
    return run(V1Double(one=one), contents=CONVERSATION)


def _compiled(pipeline):
    return pipeline.two_call.captures[0].compiled_proposal


def test_valid_updates_compile_to_exact_user_text_and_replay():
    record, pipeline, model = _run(lambda p: [
        dict(kind='same_referent', spans=[dspan(p, 'message:2', 'the first one'), dspan(p, 'message:0', 'painting')]),
    ])
    assert record.status.value == 'completed', record.failure
    payload = model.calls[0][1]
    assert payload['dialogue_context']['interpreted_links'] == []
    compiled = _compiled(pipeline)
    assert compiled['dialogue_updates_dropped'] == 0
    [update] = compiled['dialogue_updates']
    assert update['kind'] == 'same_referent'
    assert [span['text'] for span in update['spans']] == ['the first one', 'painting']
    assert_replay(record)


def test_invalid_updates_are_dropped_and_counted_without_failing_the_turn():
    record, pipeline, _ = _run(lambda p: [
        # Cites no span of the current message.
        dict(kind='option_set', spans=[dspan(p, 'message:0', 'painting'), dspan(p, 'message:0', 'hiking')]),
        # Cites the assistant's message.
        dict(kind='same_referent', spans=[dspan(p, 'message:2', 'the first one'),
                                          dict(kind='dialogue_span', message_ref='message:1', span_ref='span:0')]),
        # A correction relates exactly two spans.
        dict(kind='correction', spans=[dspan(p, 'message:2', 'the first one'), dspan(p, 'message:0', 'painting'),
                                       dspan(p, 'message:0', 'hiking')]),
    ])
    assert record.status.value == 'completed', record.failure
    compiled = _compiled(pipeline)
    assert not compiled['dialogue_updates'] and compiled['dialogue_updates_dropped'] == 3
    assert_replay(record)


# ---------------------------------------------------------------------------
# Call 1 registry v1.8: the recall answer is written, and must be the user's words
# ---------------------------------------------------------------------------

NEIGHBORS = (('user', 'My two neighbors are Ana and Bea. Ana is the gardener and Bea runs every morning.'),
             ('user', 'Actually, the runner gave me the tomatoes.'),
             ('user', 'So who gave me the tomatoes?'))


def _recall(answer, contents=NEIGHBORS):
    from test_cognitive_two_call_v1 import NULL, event, reading, select, span_of, understanding

    def one(payload):
        raw = understanding(event('dialogue_recall_request', {
            'referent_phrase': {'kind': 'span', 'ref': span_of(payload, 'who')},
            'answer_ref': dspan(payload, 'message:1', 'the runner'), 'candidate_refs': NULL,
            'knowledge_status': {'kind': 'enum', 'value': 'known'}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
        raw['recall_answer'] = answer
        return raw
    return run(V1Double(one, select('provide_information.answer')), contents=contents)


def test_a_written_answer_found_in_the_users_words_is_the_answer():
    from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of
    record, pipeline, _ = _recall('Bea.')
    assert record.status.value == 'completed', record.failure
    assert _compiled(pipeline)['recall_answer_source'] == 'written'
    [content] = requirement_content_of(record.pipeline_trace)
    assert content.arguments[0].value == 'Bea'
    # The answer to the question, not "you said Bea".
    assert (content.predicate, content.subject.surface_anchor) == ('dialogue.recall_answer', 'So who gave me the tomatoes?')
    assert_replay(record)


@pytest.mark.parametrize('answer', ['Carla', 'Be', None])
def test_a_name_the_user_never_wrote_falls_back_to_the_cited_passage(answer):
    from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of
    record, pipeline, _ = _recall(answer)
    assert record.status.value == 'completed', record.failure
    assert _compiled(pipeline)['recall_answer_source'] == 'cited'
    [content] = requirement_content_of(record.pipeline_trace)
    assert content.arguments[0].value == 'the runner'
    assert_replay(record)


SEASON = (('user', 'Do you have a favorite season?'),
          ('assistant', 'I think autumn is the loveliest season.'),
          ('user', 'What did you say your favorite was?'))


def test_she_can_recall_her_own_earlier_words():
    from test_cognitive_two_call_v1 import NULL, event, reading, select, span_of, understanding
    from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of

    def one(payload):
        hers = next(m for m in payload['dialogue_context']['messages'] if m['speaker'] == 'assistant')
        assert hers['indexed_tokens']  # v1.8: her replies are citable
        raw = understanding(event('dialogue_recall_request', {
            'referent_phrase': {'kind': 'span', 'ref': span_of(payload, 'favorite')},
            'answer_ref': dspan(payload, hers['ref'], 'autumn'), 'candidate_refs': NULL,
            'knowledge_status': {'kind': 'enum', 'value': 'known'}, 'embedding': NULL,
        }, payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
        raw['recall_answer'] = 'autumn'
        return raw
    record, pipeline, _ = run(V1Double(one, select('provide_information.answer')), contents=SEASON)
    assert record.status.value == 'completed', record.failure
    assert _compiled(pipeline)['recall_answer_source'] == 'written'
    [content] = requirement_content_of(record.pipeline_trace)
    assert content.arguments[0].value == 'autumn'
    # What she said, attributed to her: never a fact.
    assert (content.source_kind.value, content.attribution.value, content.epistemic_status.value) == (
        'delivered_assistant_utterance', 'columbina', 'attributed')
    assert_replay(record)


def test_dialogue_updates_still_cite_only_user_messages():
    def updates(p):
        hers = next(m for m in p['dialogue_context']['messages'] if m['speaker'] == 'assistant')
        return [dict(kind='same_referent', spans=[dspan(p, 'message:2', 'favorite'), dspan(p, hers['ref'], 'autumn')])]

    def one(payload):
        raw = social(payload)
        raw['dialogue_updates'] = updates(payload)
        return raw
    record, pipeline, _ = run(V1Double(one=one), contents=SEASON)
    assert record.status.value == 'completed', record.failure
    assert not _compiled(pipeline)['dialogue_updates'] and _compiled(pipeline)['dialogue_updates_dropped'] == 1


def test_recall_answer_is_rejected_before_registry_v1_8(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.7')
    record, _, model = _recall('Bea')
    assert model.calls[0][1]['ontology']['predicate_registry_version'] == 'argument-kind-registry-v1.7'
    assert record.status.value != 'completed'


# ---------------------------------------------------------------------------
# End to end on real PostgreSQL: store, capture v0.4, fold, pending
# ---------------------------------------------------------------------------

from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: E402,F401
from dialogue_memory_support import dialogue_env  # noqa: E402,F401
from test_dialogue_memory_pipeline import _capture, _finish_reply, _live_turn, _shadow_rows  # noqa: E402
from test_dialogue_memory_persistence import _drain  # noqa: E402


@pytest.fixture
def interpretation_env(dialogue_env, monkeypatch):
    from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
    from app.ai.Cognitive.system.v0.two_call.orchestrator import install
    from app.ai.Cognitive.system.v0.two_call.v1_contracts import PROTOCOL_V1
    from app.services import cognitive_shadow as service

    dialogue_env.configure(cognitive_dialogue_interpretation_enabled=True)
    dialogue_env.script = {}
    dialogue_env.payloads = []

    def factory(turn_id, _settings=None):
        def one(payload):
            dialogue_env.payloads.append(payload)
            raw = social(payload)
            raw['dialogue_updates'] = dialogue_env.script.get(payload['focus']['text'], lambda p: [])(payload)
            return raw
        return install(build_pipeline(turn_id), V1Double(one=one), protocol=PROTOCOL_V1)

    monkeypatch.setattr(service, 'cognitive_pipeline', factory)
    return dialogue_env


def _turn(env, conversation_id, user_id, text, reply):
    message_id, turn_id, job_id = _live_turn(env, conversation_id, text)
    assert _capture(env, conversation_id, user_id, message_id, turn_id, job_id)
    _finish_reply(env, conversation_id, turn_id, reply)
    _drain()
    return message_id, turn_id


def _rows(env, conversation_id):
    from sqlalchemy import select

    from app.db.models import CognitiveDialogueInterpretation
    with env.db() as db:
        return db.scalars(select(CognitiveDialogueInterpretation)
                          .where(CognitiveDialogueInterpretation.conversation_id == conversation_id)
                          .order_by(CognitiveDialogueInterpretation.source_conversation_sequence)).all()


T1 = "I'm deciding whether to spend Saturday painting or hiking."
T2 = 'I keep leaning toward the first one.'
T3 = 'What was the other option?'


def test_updates_are_stored_and_reach_the_next_turn_through_capture_v04(interpretation_env) -> None:
    from app.ai.Cognitive.system.v0.live_shadow.models import CAPTURE_SCHEMA_VERSION_V04, CognitiveShadowCapture
    from app.workers import cognitive_shadow as shadow_worker

    env = interpretation_env
    env.script[T1] = lambda p: [dict(kind='option_set', spans=[
        dspan(p, p['focus']['message_ref'], 'painting'), dspan(p, p['focus']['message_ref'], 'hiking')])]
    env.script[T2] = lambda p: [dict(kind='same_referent', spans=[
        dspan(p, p['focus']['message_ref'], 'the first one'), dspan(p, 'message:0', 'painting')])]
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        user_id, conversation_id = user.id, conversation.id

    first, first_turn = _turn(env, conversation_id, user_id, T1, 'Both sound lovely.')
    assert shadow_worker.run_once()
    [row] = _rows(env, conversation_id)
    assert row.source_message_id == first and row.payload['updates'][0]['kind'] == 'option_set'
    assert [s['text'] for s in row.payload['updates'][0]['spans']] == ['painting', 'hiking']

    second, second_turn = _turn(env, conversation_id, user_id, T2, 'Making things is a good pull.')
    # Captured while turn 2's reading is still queued: it is pending, not silently missing.
    third, third_turn = _turn(env, conversation_id, user_id, T3, 'Hiking.')
    job3, _ = _shadow_rows(env, third_turn)
    capture3 = CognitiveShadowCapture.model_validate(job3.capture_payload)
    assert capture3.schema_version == CAPTURE_SCHEMA_VERSION_V04
    assert capture3.interpreted_dialogue.pending_message_refs == (str(second),)
    assert [r.source_message_ref for r in capture3.interpreted_dialogue.records] == [str(first)]

    assert shadow_worker.run_once()  # turn 2
    payload2 = env.payloads[-1]
    [options] = payload2['dialogue_context']['interpreted_links']
    assert options['kind'] == 'option_set' and [o['text'] for o in options['options']] == ['painting', 'hiking']
    assert all(o['message_ref'] is not None and not o['superseded'] for o in options['options'])
    assert [r.source_message_id for r in _rows(env, conversation_id)] == [first, second]

    assert shadow_worker.run_once()  # turn 3: its capture was frozen with turn 2 pending
    payload3 = env.payloads[-1]
    assert payload3['dialogue_context']['interpreted_links_incomplete'] is True
    _, trace3 = _shadow_rows(env, third_turn)
    assert trace3.terminal_status == 'completed', trace3.failure_payload
    # Exact replay from the stored capture-derived request and trace, with no model call.
    from app.ai.Cognitive.system.v0.live_shadow import replay
    report = replay(compiled_request_payload=trace3.compiled_request_payload,
                    pipeline_trace_payload=trace3.pipeline_trace_payload,
                    compiled_request_digest=trace3.compiled_request_digest,
                    stored_semantic_digest=trace3.semantic_digest, versions_payload=trace3.versions)
    assert report.status.value == 'match', report
    request = trace3.compiled_request_payload
    assert request['dialogue_context']['interpreted']['option_sets'][0]['options'][0]['span']['text'] == 'painting'


def test_interpretation_rows_are_immutable(interpretation_env) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.workers import cognitive_shadow as shadow_worker

    env = interpretation_env
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        user_id, conversation_id = user.id, conversation.id
    _turn(env, conversation_id, user_id, T1, 'Both sound lovely.')
    assert shadow_worker.run_once()
    [row] = _rows(env, conversation_id)
    assert row.payload['updates'] == []  # read, and established nothing
    with env.db() as db, pytest.raises(DBAPIError):
        db.execute(text('UPDATE cognitive_dialogue_interpretations SET record_digest = record_digest'))
        db.commit()
