"""Her character (core memory v2): opinions reasoned from values, principles and beliefs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from cognitive_shadow_support import make_capture
from test_cognitive_continuity_regression import _opinion
from test_cognitive_two_call_v1 import V1Double, assert_replay, run, select

from app.ai.Cognitive.system.v0.contracts.character import Character
from app.ai.Cognitive.system.v0.contracts.realization import GroundedProposition
from app.ai.Cognitive.system.v0.live_shadow.models import CognitiveShadowCapture
from app.ai.Cognitive.system.v0.live_shadow.realization import requirement_content_of
from app.ai.Cognitive.system.v0.two_call.contribution import ContributionProposalV2, basis_check
from app.ai.Cognitive.system.v0.two_call.provider import prompt, response_schema

_MIGRATION = Path(__file__).parents[1] / 'alembic/versions/e3a9c7b1d5f2_columbina_character.py'


def _seeded_character() -> dict:
    spec = importlib.util.spec_from_file_location('character_migration', _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CHARACTER


def _with_character(contents, character=None):
    capture = make_capture(contents=contents)
    character = character or _seeded_character()
    payload = capture.model_dump(mode='json') | {
        'character': character | {'core_memory_ref': capture.core_memory.core_memory_ref}}
    return CognitiveShadowCapture.model_validate(payload)


LIE = 'Is it okay to lie to a friend to spare their feelings?'


def _view(text='I lean toward the truth, said gently.', basis=None, tensions=None):
    return lambda payload: dict(
        schema_version='contribution-proposal-v2', question_kind='opinion', claims=[],
        view=dict(text=text, confidence='medium',
                  basis=basis if basis is not None else [dict(kind='value', ref='honesty'),
                                                         dict(kind='belief', ref='deception_costs_trust')],
                  tensions=tensions if tensions is not None else [
                      dict(between=['honesty', 'compassion'], resolution='The truth, chosen kindly and well timed.')]))


def test_the_seeded_character_is_valid_and_a_capture_without_one_omits_it():
    character = Character.model_validate(_seeded_character() | {'core_memory_ref': 'core_memory:columbina:v2'})
    assert [item.id for item in character.values][0] == 'honesty' and len(character.beliefs) == 3
    assert 'character' not in make_capture().model_dump(mode='json')


def test_an_opinion_is_one_view_reasoned_from_her_character():
    model = V1Double(_opinion('lie to a friend'), select('provide_information.answer'), contribution=_view())
    record, pipeline, _ = run(model, capture=_with_character((('user', LIE),)))
    assert record.pipeline_trace is not None, record.failure
    assert record.compiled_request.character is not None
    contribution = record.pipeline_trace.orchestration.contributions[0]
    payload = contribution.payload
    assert payload['schema_version'] == 'contribution-input-v1.2'
    assert contribution.provenance['prompt_version'] == 'contribution-v1.9'
    assert [item['id'] for item in payload['character']['beliefs']] == [
        'deception_costs_trust', 'people_make_sense', 'more_than_it_looks']
    assert 'mood' in payload and payload['personality']
    assert 'Her character (v1.9)' in prompt('contribution', payload)
    # Cited ids are limited to her character by the schema.
    ref = response_schema('contribution', payload)['properties']['view']['anyOf'][0]['properties']['basis']['items']
    assert 'honesty' in ref['properties']['ref']['anyOf'][0]['enum']
    [content] = [i for i in requirement_content_of(record.pipeline_trace) if isinstance(i, GroundedProposition)]
    assert (content.predicate, content.arguments[0].value) == ('columbina.view.reasoned', 'I lean toward the truth, said gently.')
    assert content.source_kind.value == 'columbina_own_view' and content.requires_uncertainty
    assert_replay(record)


def test_a_view_may_rest_on_plain_reasoning_and_unknown_ids_never_fail_the_turn():
    basis = [dict(kind='reasoning', ref=None), dict(kind='value', ref='loyalty'), dict(kind='belief', ref='honesty')]
    model = V1Double(_opinion('lie to a friend'), select('provide_information.answer'),
                     contribution=_view(basis=basis, tensions=[]))
    capture = _with_character((('user', LIE),))
    record, _, _ = run(model, capture=capture)
    assert record.pipeline_trace is not None, record.failure
    proposal = ContributionProposalV2.model_validate(record.pipeline_trace.orchestration.contributions[0].proposal)
    # "honesty" is a value, not a belief: cited under the wrong section, it does not count.
    assert basis_check(proposal.view, capture.character) == dict(known=[], unknown=['loyalty', 'honesty'])


def test_without_a_character_opinions_keep_the_previous_path():
    record, _, model = run(V1Double(_opinion('small talk is pointless'), select('provide_information.answer'),
                                    contribution=lambda p: dict(
                                        schema_version='contribution-proposal-v1', question_kind='opinion', claims=[],
                                        takes=[dict(stance='unsure', tone='serious', confidence='low', text='I am not sure.'),
                                               dict(stance='own_position', tone='serious', confidence='medium',
                                                    text='I think it has a purpose.')])),
                           text='Honestly I think small talk is pointless.')
    assert record.pipeline_trace is not None, record.failure
    assert record.pipeline_trace.orchestration.contributions[0].payload['schema_version'] == 'contribution-input-v1.1'


def test_a_question_routed_as_factual_still_gets_her_view():
    """'Should I tell them?' may reach the contribution as a knowledge request; her view is kept, not dropped."""
    from test_cognitive_continuity_regression import _question
    model = V1Double(_question('tell them what I really think'), select('provide_information.answer'),
                     contribution=_view(text='I think honest, specific feedback, asked for first, is kinder.'))
    record, _, _ = run(model, capture=_with_character(
        (('user', 'My friend wrote a bad novel. Should I tell them what I really think?'),)))
    assert record.pipeline_trace is not None, record.failure
    assert record.pipeline_trace.orchestration.contributions[0].payload['requested'] == 'knowledge'
    [content] = [i for i in requirement_content_of(record.pipeline_trace) if isinstance(i, GroundedProposition)]
    assert content.predicate == 'columbina.view.reasoned'
    assert_replay(record)
