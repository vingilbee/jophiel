"""decision-context-v0.2: delivery handoff, schema independence and lossless normalization.

Regression cases from live shadow failures on 2026-10-05. No external model calls.
"""
import copy
from app.ai.Cognitive.system.v0.two_call.compact import normalize_response, _dietary_value
from app.ai.Cognitive.system.v0.two_call.provider import prompt, response_schema
from app.ai.Cognitive.system.v0.realization_v0.validator import RealizationValidatorV0, SENTENCE_STARTERS, SUGGESTION_STARTERS
from test_compact_two_call import CompactDouble, run_compact


def dinner():
    from simulated_authority_support import DINNER
    return run_compact(CompactDouble(artifact=True), text=DINNER['turns'][1]['user'])


def test_v0_2_packet_hands_over_the_required_delivery_move_and_prompt():
    record, pipeline, model = dinner()
    assert record.status.value == 'completed', record.failure
    payload = model.calls[1][1]
    packet = payload['decision_context']
    assert packet['version'] == 'decision-context-v0.2'
    request = packet['content_requests'][0]
    assert request['delivery_move'] == dict(required=True, template_handle=request['delivery_template'],
        goal_handles=[request['goal_ref']], target_bindings=[request['delivery_target']])
    assert 'Delivery is mandatory' in prompt('call_two', payload)
    capture = pipeline.two_call.captures[1]
    assert capture.provenance['prompt_version'] == 'call_two-compact-v0.2'
    assert capture.provenance['context_version'] == 'decision-context-v0.2'
    assert pipeline.two_call.realization_policy == 'segmented_response-v0.2'


def test_v0_1_packets_keep_their_prompt_and_compile_unnormalized():
    _, _, model = dinner()
    payload = copy.deepcopy(model.calls[1][1])
    payload['decision_context']['version'] = 'decision-context-v0.1'
    assert 'Delivery is mandatory' not in prompt('call_two', payload)


def test_anchor_constraints_do_not_leak_into_sibling_schema_fields():
    _, _, model = dinner()
    output = response_schema('call_two', model.calls[1][1])['properties']['content_ops']['items']['properties']['output']['properties']
    assert output['content_items']['items']['properties']['anchors']['minItems'] == 1
    assert 'minItems' not in output['content_items']['items']['properties']['subject_ids']
    options = output['inquiries']['items']['properties']['answer_options']
    # An open_set/yes_no inquiry needs zero options, so options must not be forced.
    assert 'minItems' not in options
    assert options['items']['pattern'] == r'^[a-z0-9][a-z0-9_\-]*$'


def raw_response(item, inquiries=(), operations=()):
    output = dict(title=None, operations=list(operations), content_items=[item], inquiries=list(inquiries))
    return dict(content_ops=[dict(request_handle=0, output=output)], moves=[], segments=[], inquiries=[])


DISH = dict(op='set_main_dish', element_id=None, section_id=None, integer_value=None, integer_class=None, constraint=None,
            dish=dict(dish_id='quick_dinner', name='Quick dinner idea', content_class='generated_proposal', contains=[],
                      traits=[], prep_time_minutes=None), text=None, text_class=None, heading=None, source_refs=['r1'])


def item(statement, anchors):
    return dict(role='alternative', statement=statement, anchors=anchors, content_class='generated_proposal',
                claim_kind='culinary_suggestion', subject_ids=['quick_dinner'], source_refs=['r1'], numbers=[])


def test_element_id_anchor_becomes_the_spoken_label_only_when_the_statement_contains_it():
    out = normalize_response(raw_response(item('A quick dinner idea is a stir-fry.', ['quick_dinner']), operations=[DISH]))
    assert out['content_ops'][0]['output']['content_items'][0]['anchors'] == ['Quick dinner idea']
    # Nothing to map to: left for the compiler to reject rather than inventing an anchor.
    out = normalize_response(raw_response(item('Try a stir-fry.', ['quick_dinner']), operations=[DISH]))
    assert out['content_ops'][0]['output']['content_items'][0]['anchors'] == ['quick_dinner']
    out = normalize_response(raw_response(item('Try a stir-fry.', ['stir-fry', 'quick_dinner']), operations=[DISH]))
    assert out['content_ops'][0]['output']['content_items'][0]['anchors'] == ['stir-fry']


def test_inquiry_options_become_slugs_and_question_kind_follows_them():
    inquiry = dict(target_kind='artifact', target_id='x', question_kind='open_set', purpose='improve_personalization',
                   expected_use='refine_artifact', criticality='quality_improving', sensitivity='ordinary',
                   asked_about='What kind of dinner?', answer_options=['Vegetarian', 'Meat or fish', 'No preference'],
                   source_refs=['r1'])
    out = normalize_response(raw_response(item('A stir-fry.', ['stir-fry']), inquiries=[inquiry]))
    normalized = out['content_ops'][0]['output']['inquiries'][0]
    assert normalized['question_kind'] == 'choice'
    assert normalized['answer_options'] == ['vegetarian', 'meat_or_fish', 'no_preference']
    single = dict(inquiry, question_kind='choice', answer_options=['Anything'])
    out = normalize_response(raw_response(item('A stir-fry.', ['stir-fry']), inquiries=[single]))
    assert out['content_ops'][0]['output']['inquiries'][0] == dict(single, question_kind='open_set', answer_options=[])


def test_dietary_constraint_phrases_map_only_onto_closed_values():
    assert _dietary_value('without dairy') == 'dairy_free'
    assert _dietary_value('Dairy-free') == 'dairy_free'
    assert _dietary_value('No eggs') == 'egg_free'
    assert _dietary_value('no nuts') == 'nut_free'
    assert _dietary_value('low carb') == 'low carb'


def test_omitted_delivery_reports_a_closed_reason():
    def omit(stage, raw, payload):
        if stage == 'call_two':
            choice = payload['decision_context']['choices'][0]
            raw['moves'] = [dict(choice_handle=choice['handle'], template_handle=None, goal_handles=[],
                                 target_bindings=[], segment_handles=['s0'], parameters=[])]
    from simulated_authority_support import DINNER
    record, _, _ = run_compact(CompactDouble(artifact=True, mutation=omit), text=DINNER['turns'][1]['user'])
    assert record.status.value == 'failed'
    assert 'behavior_proposal_ineligible.stop_omitted_delivery' in record.failure.code


def test_v0_2_validator_policy_accepts_suggestion_openers_only_in_that_policy():
    RealizationValidatorV0(policy='segmented_response-v0.2')
    assert {'try', 'cook', "here's", 'serve'} <= SUGGESTION_STARTERS
    assert not {'try', 'cook', 'serve'} & SENTENCE_STARTERS


def test_citing_one_copy_of_a_repeated_user_requirement_cites_its_identical_siblings():
    packet = dict(content_requests=[dict(sources=[
        dict(ref='r1', kind='user_request', speaker='user', text='create meal_plan'),
        dict(ref='r2', kind='user_requirement', speaker='user', text='no meat'),
        dict(ref='r3', kind='user_requirement', speaker='user', text='No  meat'),
        dict(ref='r4', kind='user_requirement', speaker='user', text='cheap')])])
    raw = raw_response(item('A stir-fry.', ['stir-fry']), operations=[dict(DISH, source_refs=['r1', 'r2'])])
    out = normalize_response(raw, packet)
    assert out['content_ops'][0]['output']['operations'][0]['source_refs'] == ['r1', 'r2', 'r3']
