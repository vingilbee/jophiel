"""Call 1 conditioning guides: bias meanings reach the model only for mapper v0.2 profiles.

Guide v2 (the default) gives the model the meanings only; the model names which
biases bear on each reading and code applies her values (``v1_bias``). Guide v1
gives values and meanings and lets the model apply them. Earlier profiles
(mapper v0.1 or the reviewed baseline) build exactly their original Call 1
payload and prompt, so stored captures replay unchanged.
"""
import copy
import json

from app.ai.Cognitive.system.v0.personality_runtime_v0 import PersonalityRuntimeMapperV0, build_source
from app.ai.Cognitive.system.v0.two_call import v1 as V1
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel, prompt
from app.ai.Cognitive.system.v0.two_call.v1_bias import FIT_BIASES
from app.ai.Cognitive.system.v0.two_call.v1_contracts import PROTOCOL_V1
from app.ai.Cognitive.system.v0.two_call.v1_training import project_training_example
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe
from cognitive_shadow_support import COLUMBINA_CORE_GROUPS, make_capture
from realization_v0_support import mapper_projection
from test_cognitive_two_call_v1 import NULL, V1Double, appraisal, assert_replay, event, run, understanding

GUIDE_V1_TEXT = "predict this particular agent's understanding"
GUIDE_V2_TEXT = 'code applies the agent\'s biases afterwards'
GUIDE_V3_TEXT = 'Open messages get both readings'
TEXT = 'thanks for nothing, I guess.'


def projection(**biases):
    groups = copy.deepcopy({key: COLUMBINA_CORE_GROUPS[key] for key in (
        'interpretation_biases', 'perception_priors', 'drives', 'action_style', 'traits')})
    groups['interpretation_biases'].update(biases)
    return PersonalityRuntimeMapperV0().compile(build_source(name='columbina', version=1, schema_version=1, groups=groups))


def jab_or_thanks(payload):
    """Two pragmatic readings of one event; hostile attribution bears on the jab."""
    span = payload['focus']['authorized_span_refs'][:1]
    jab = dict(hypothesis_key='interpretation.pragmatic.indirect', uncertainty='moderate', context_refs=[],
               appraisal=appraisal('moderate', affiliative_value='neutral', norm_compatibility='neutral'),
               likelihood=50, bias_fit=[dict(bias_ref='bias:hostile_attribution', direction='favours')])
    thanks = dict(hypothesis_key='interpretation.pragmatic.literal', uncertainty='moderate', context_refs=[],
                  appraisal=appraisal('moderate'), likelihood=50,
                  bias_fit=[dict(bias_ref='bias:benign_prior', direction='favours')])
    return understanding(event('social_opening', {'addressee': NULL}, span, [jab, thanks]))


def run_guided(personality, *, guide=None, one=jab_or_thanks, text=TEXT):
    capture = make_capture(contents=(('user', text),), personality=personality)
    model = V1Double(one=one)
    pipeline = install(build_pipeline(capture.turn_id), model, protocol=PROTOCOL_V1, conditioning_guide=guide)
    record = observe(capture, None, ObservationContext(job_id=capture.capture_id, attempt=1,
                     capture_digest=payload_digest(capture), started_at='2026-10-05T00:00:00+00:00'),
                     pipeline_factory=lambda _: pipeline)
    assert record.status.value == 'completed', record.failure
    call = next(c for c in pipeline.two_call.captures if c.stage == 'call_one')
    return record, json.loads(json.dumps(model.calls[0][1])), call


def test_guide_v2_gives_meanings_without_values_and_keeps_the_values_from_the_model():
    record, payload, call = run_guided(mapper_projection(), one=lambda p: understanding(event(
        'social_opening', {'addressee': NULL}, p['focus']['authorized_span_refs'][:1],
        [dict(hypothesis_key='interpretation.goal.maintain_social_connection', uncertainty='low', context_refs=[],
              appraisal=appraisal(), likelihood=90, bias_fit=[])])), text='Hi')
    context = payload['context']
    assert context['conditioning_guide'] == V1.CONDITIONING_GUIDE_V3
    assert [b['ref'] for b in context['interpretation_biases']] == [f'bias:{k}' for k in FIT_BIASES]
    assert all(set(b) == {'ref', 'meaning'} for b in context['interpretation_biases'])
    values = payload['code_only']['bias_application']['values']
    assert values['hostile_attribution'] == 0.1 and values['ambiguity_tolerance'] == 0.55
    # The model is sent the payload without its code-only inputs.
    sent = OpenAITwoCallModel(model_id='gpt-6-luna', api_key='test', client=object()).parameters('call_one', payload)
    assert 'code_only' not in sent['messages'][1]['content'] and 'hostile_attribution":0.1' not in sent['messages'][1]['content']
    assert GUIDE_V2_TEXT in prompt('call_one', payload) and GUIDE_V1_TEXT not in prompt('call_one', payload)
    assert call.provenance['prompt_version'].endswith('+conditioning-v3')
    assert GUIDE_V3_TEXT in prompt('call_one', payload)
    assert call.compiled_proposal['bias_application']['version'] == 'bias-application-v1'
    assert_replay(record)


def test_code_applies_her_bias_to_the_readings_the_model_said_it_bears_on():
    weights = {}
    for hostile in (0.1, 0.9):
        record, payload, call = run_guided(projection(hostile_attribution_bias=hostile))
        application = call.compiled_proposal['bias_application']
        weights[hostile] = {r['hypothesis_key']: r['weight'] for r in application['readings']}
        assert_replay(record)
    jab = 'interpretation.pragmatic.indirect'
    assert weights[0.9][jab] > 0.5 > weights[0.1][jab]
    # The model saw the same input at both values; only code-owned values differ.


def test_the_training_candidate_carries_the_adjusted_readings_and_their_weights():
    record, _, call = run_guided(projection(hostile_attribution_bias=0.9))
    example = project_training_example(record)
    readings = example['target']['appraisal']['readings']
    assert {r['hypothesis_key'] for r in readings} == {'interpretation.pragmatic.indirect', 'interpretation.pragmatic.literal'}
    lead = next(r for r in readings if r['lead'])
    assert readings[0] is lead and 0 < lead['weight'] <= 1 and lead['likelihood'] == 0.5
    assert {f['bias_ref'] for r in readings for f in r['bias_fit']} == {'bias:hostile_attribution', 'bias:benign_prior'}


def test_guide_v1_is_still_available_with_values_and_meanings():
    record, payload, call = run_guided(mapper_projection(), guide=V1.CONDITIONING_GUIDE_VERSION,
                                       one=lambda p: understanding(event(
                                           'social_opening', {'addressee': NULL}, p['focus']['authorized_span_refs'][:1],
                                           [dict(hypothesis_key='interpretation.goal.maintain_social_connection',
                                                 uncertainty='low', context_refs=['bias:hostile_attribution'],
                                                 appraisal=appraisal())])), text='interesting choice.')
    context = payload['context']
    assert context['conditioning_guide'] == V1.CONDITIONING_GUIDE_VERSION and 'code_only' not in payload
    views = {b['ref']: b for b in context['interpretation_biases']}
    assert set(views) == {f'bias:{key}' for key in V1.BIAS_MEANINGS_V1}
    assert views['bias:hostile_attribution']['value'] == 0.1
    assert views['bias:social_cue_sensitivity']['value'] == 0.68
    assert views['bias:confirmation_tendency']['value'] == 0.4
    assert all(view['meaning'] == V1.BIAS_MEANINGS_V1[ref.split(':', 1)[1]] for ref, view in views.items())
    assert GUIDE_V1_TEXT in prompt('call_one', payload)
    assert call.provenance['prompt_version'].endswith('+conditioning-v1')
    assert 'bias_application' not in call.compiled_proposal
    assert_replay(record)


def test_earlier_profiles_build_their_original_payload_and_prompt():
    record, pipeline, model = run(capture=make_capture(contents=(('user', 'interesting choice.'),), personality='baseline'))
    assert record.status.value == 'completed', record.failure
    payload = json.loads(json.dumps(model.calls[0][1]))
    provenance = next(c for c in pipeline.two_call.captures if c.stage == 'call_one').provenance
    context = payload['context']
    assert 'conditioning_guide' not in context and 'code_only' not in payload
    assert [b['ref'] for b in context['interpretation_biases']] == [f'bias:{key}' for key in V1.BASE_BIAS_KEYS]
    assert all(set(b) == {'ref', 'value'} for b in context['interpretation_biases'])
    assert GUIDE_V1_TEXT not in prompt('call_one', payload) and GUIDE_V2_TEXT not in prompt('call_one', payload)
    assert '+conditioning' not in provenance['prompt_version']
    assert_replay(record)


def test_a_character_page_reaches_both_calls_instead_of_bias_numbers():
    page = 'Columbina is gentle and curious.'
    capture = make_capture(contents=(('user', 'Hi'),), personality=mapper_projection())
    model = V1Double()
    pipeline = install(build_pipeline(capture.turn_id), model, protocol=PROTOCOL_V1, character_page=page)
    record = observe(capture, None, ObservationContext(job_id=capture.capture_id, attempt=1,
                     capture_digest=payload_digest(capture), started_at='2026-10-05T00:00:00+00:00'),
                     pipeline_factory=lambda _: pipeline)
    assert record.status.value == 'completed', record.failure
    payloads = dict(model.calls)
    one, two = payloads['call_one'], payloads['call_two']
    assert one['context']['character_page'] == page and one['context']['conditioning_guide'] == V1.CHARACTER_PAGE_GUIDE
    assert one['context']['interpretation_biases'] == [] and 'code_only' not in one
    assert two['character_page'] == page
    assert 'Character (context.character_page' in prompt('call_one', one)
    assert 'Character (character_page' in prompt('call_two', two)
    calls = {c.stage: c for c in pipeline.two_call.captures}
    assert calls['call_one'].provenance['prompt_version'].endswith('+character-page-v1')
    assert calls['call_two'].provenance['prompt_version'].endswith('+character-page-v1')
    assert_replay(record)
