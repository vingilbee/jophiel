"""Realization policy v1.4: the wording model reads the recent conversation (comprehension only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from realization_v0_support import ScriptedModel
from test_cognitive_two_call_v1 import V1Double, _replay_wording, run

from app.ai.Cognitive.system.v0.contracts.realization import RealizationRequestV0, request_digest_for
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from app.ai.Cognitive.system.v0.realization_v0.model_protocol import response_json_schema

CONVERSATION = (('user', "I'm deciding whether to spend Saturday painting or hiking."),
                ('assistant', 'Both sound good.'),
                ('user', 'Hi'))


def _realize(record, model):
    return realize_record(record, engine=RealizationEngineV0(model, max_attempts=1), configured_maximum_characters=1200)


def test_wording_model_receives_earlier_messages_only_and_replays():
    record, _, _ = run(V1Double(), contents=CONVERSATION)
    model = ScriptedModel('Hi!')
    realized = _realize(record, model)
    context = realized.realization_trace.request.conversation_context
    assert [(m.speaker, m.text) for m in context.messages] == [
        ('user', CONVERSATION[0][1]), ('columbina', 'Both sound good.')]
    assert context.fact_authority is False and context.permitted_use == 'comprehension_and_reference_resolution_only'
    # The current message stays in linguistic_context, never duplicated as history.
    assert realized.realization_trace.request.linguistic_context.text == 'Hi'
    [payload] = model.payloads
    assert payload.conversation_context == context
    assert _replay_wording(realized).status.value == 'match'


def test_context_keeps_the_last_eight_and_leaves_out_overlong_messages():
    history = []
    for index in range(6):
        history += [('user', f'message {index}'), ('assistant', f'reply {index}')]
    history[-2] = ('user', 'x' * 2001)
    record, _, _ = run(V1Double(), contents=(*history, ('user', 'Hi')))
    context = _realize(record, ScriptedModel('Hi!')).realization_trace.request.conversation_context
    texts = [m.text for m in context.messages]
    assert len(texts) == 8 and 'x' * 2001 not in texts and texts[-1] == 'reply 5'


def test_a_first_message_has_no_context_field_at_all():
    record, _, _ = run(V1Double())
    request = _realize(record, ScriptedModel('Hi!')).realization_trace.request
    assert request.conversation_context is None
    assert 'conversation_context' not in request.model_dump(mode='json')


def test_earlier_requests_keep_their_exact_digests():
    corpus = Path(__file__).parents[1] / 'app/ai/Cognitive/system/v0/data/answer_fidelity_regression_v0.json'
    for case in json.loads(corpus.read_text())['cases']:
        request = RealizationRequestV0.model_validate(case['request'])
        assert 'conversation_context' not in request.model_dump(mode='json')
        assert request_digest_for(request) == case['request']['request_digest']


@pytest.mark.parametrize('variant', ['conversational-v1', 'conversational-v2'])
def test_instruction_variants(variant):
    text = response_json_schema(variant)['properties']['text']['description']
    assert ('conversation_context' in text) == (variant == 'conversational-v2')
    # v2 no longer tells her to default to "I don't know".
    assert ('give that answer plainly' in text) == (variant == 'conversational-v2')
