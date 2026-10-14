"""Protocol cognitive-two-call-v1: typed understanding, candidate policy, separate Realization.

Covers the required tests of CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md §18
with scripted doubles.  No external model calls and no behavioral-quality claims.
"""
import copy
import json
import re

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.two_call import v1 as V1
from app.ai.Cognitive.system.v0.two_call.contracts import CallCapture
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.two_call.provider import output_type, prompt, response_schema
from app.ai.Cognitive.system.v0.two_call.v1_contracts import (
    INTERPRETED_DIALOGUE_REGISTRIES, PROTOCOL_V1, WRITTEN_RECALL_REGISTRIES, ActionPolicyProposalV1, CompiledPolicy, UnderstandingProposalV1, parse_span_ref,
)
from app.ai.Cognitive.system.v0.two_call.v1_training import project_training_example
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe, replay
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
from cognitive_shadow_support import make_capture
from test_two_call_orchestration import Double

NULL = {'kind': 'null'}


def appraisal(uncertainty='low', **overrides):
    values = dict(relevance='moderate', congruence='positive', novelty='low', certainty='high', controllability='high',
                  norm_compatibility='positive', affiliative_value='positive', agency='user')
    values.update(overrides)
    return dict(uncertainty=uncertainty, **values)


def reading(key='interpretation.goal.maintain_social_connection', uncertainty='low', context=(), **appraisal_overrides):
    return dict(hypothesis_key=key, uncertainty=uncertainty, context_refs=list(context),
                appraisal=appraisal(**appraisal_overrides))


def span_of(payload, phrase):
    """The supplied token span covering an exact phrase of the focus message."""
    text = payload['focus']['text']
    start = text.lower().index(phrase.lower())
    end = start + len(phrase)
    tokens = V1.focus_tokens(text)
    inside = [i for i, (s, e, _) in enumerate(tokens) if s >= start and e <= end]
    return f'span:{inside[0]}' if inside[0] == inside[-1] else f'span:{inside[0]}-{inside[-1]}'


def understanding(*events, impacts=(), unknowns=(), conflicts=()):
    return dict(schema_version='understanding-proposal-v1', events=list(events), existing_goal_impacts=list(impacts),
                unknowns=list(unknowns), conflicts=list(conflicts))


def event(predicate, arguments, evidence, readings=()):
    return dict(predicate_key=predicate, polarity='positive', arguments=arguments, evidence_span_refs=list(evidence),
                readings=list(readings))


def social(payload):
    return understanding(event('social_opening', {'addressee': NULL}, payload['focus']['authorized_span_refs'][:1],
                               [reading(context=['bias:benign_prior'])]))


def select(*keys, primary=0, factors=None):
    def choose(payload):
        refs = []
        for key in keys:
            refs.append(next(c['candidate_ref'] for c in payload['eligible_candidates']
                             if c['action_key'] == key and c['candidate_ref'] not in refs))
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=refs,
                    primary_index=primary if refs else None, stop_after_selected=True,
                    selection_factors=factors(refs) if factors else [])
    return choose


class V1Double:
    usage = {}
    provenance = Double.provenance

    def __init__(self, one=social, two=select('manage_dialogue.greet'), mutate=None, contribution=None):
        self.calls, self.one, self.two, self.mutate = [], one, two, mutate
        # Registry v1.6 open-world stage; by default it declines, keeping the explicit gap.
        self.contribution = contribution or (lambda payload: dict(
            schema_version='contribution-proposal-v1', question_kind='unanswerable', claims=[], takes=[]))

    def generate(self, stage, payload, schema=None):
        assert payload['protocol'] == PROTOCOL_V1
        self.calls.append((stage, payload))
        json.dumps(response_schema(stage, payload))
        assert prompt(stage, payload)
        raw = {'call_one': self.one, 'call_two': self.two, 'contribution': self.contribution}[stage](payload)
        if stage == 'contribution' and payload.get('schema_version') == 'contribution-input-v1.4' and raw['schema_version'] == 'contribution-proposal-v2':
            # Legacy scripted fixtures do not model premise reasoning. The
            # dedicated premise tests supply actual assessments explicitly.
            raw = dict(raw, schema_version='contribution-proposal-v3', premise_assessment=dict(
                premise=None, status='not_applicable', position_refs=[], relation='not_applicable',
                reason='This scripted fixture does not model a comparison premise.'))
        if stage == 'call_one' and payload['ontology']['predicate_registry_version'] in ('argument-kind-registry-v1.10', 'argument-kind-registry-v1.11', 'argument-kind-registry-v1.12', 'argument-kind-registry-v1.13'):
            for item in raw['events']:
                embedding = item['arguments'].get('embedding', {}).get('value', 'direct')
                item.setdefault('act_scope', {'direct': 'current', 'hypothetical': 'imagined'}.get(embedding, embedding))
                item.setdefault('subject_mode', 'unspecified')
        if stage == 'call_one' and payload['ontology']['predicate_registry_version'] in INTERPRETED_DIALOGUE_REGISTRIES:
            raw.setdefault('dialogue_updates', [])  # registry v1.7: required, usually empty
            if payload['ontology']['predicate_registry_version'] in WRITTEN_RECALL_REGISTRIES:
                raw.setdefault('recall_answer', None)  # registry v1.8: required, null without a known recall
        if self.mutate:
            self.mutate(stage, raw, payload)
        return output_type(stage, payload).model_validate(raw)


def run(model=None, text='Hi', *, contents=None, collaboration=None, capture=None, registry=None):
    capture = capture or make_capture(contents=contents or (('user', text),))
    model = model or V1Double()
    pipeline = install(build_pipeline(capture.turn_id), model, protocol=PROTOCOL_V1)
    if registry is not None:
        pipeline.two_call.understanding_registry = registry
    if collaboration is not None:
        pipeline.collaboration_engine = collaboration
    record = observe(capture, None, ObservationContext(job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
                     started_at='2026-10-05T00:00:00+00:00'), pipeline_factory=lambda _: pipeline)
    return record, pipeline, model


def assert_replay(record):
    report = replay(compiled_request_payload=record.compiled_request.model_dump(mode='json'),
                    pipeline_trace_payload=record.pipeline_trace.model_dump(mode='json'),
                    compiled_request_digest=record.compiled_request_digest, stored_semantic_digest=record.semantic_digest,
                    versions_payload=record.versions.model_dump(mode='json'))
    assert report.status.value == 'match', report


def payload_and_entries(text):
    record, pipeline, model = run(text=text)
    plain = lambda value: json.loads(json.dumps(value))
    return plain(model.calls[0][1]), plain(pipeline.two_call.captures[0].reference_table)


# ---------------------------------------------------------------------------
# End to end, replay, versions
# ---------------------------------------------------------------------------

def test_social_turn_completes_with_model_policy_planner_and_replays():
    record, pipeline, model = run()
    assert record.status.value == 'completed', record.failure
    assert [stage for stage, _ in model.calls] == ['call_one', 'call_two']
    trace = record.pipeline_trace
    assert trace.orchestration.protocol == PROTOCOL_V1
    assert (trace.orchestration.behavior_policy, trace.orchestration.realization_policy) == (
        'model_policy_selection-v1.1', 'separate_realization-v1.6')
    assert trace.action_selection.versions.planner == 'action-planner-model-policy-v1'
    assert [m.definition_ref.action_key for m in trace.action_selection.plan.moves] == ['manage_dialogue.greet']
    assert trace.action_selection.plan.primary_designation.rule_id == 'action-primary.model-selected.v1.1'
    assert trace.interpretation.versions.hypothesis_rules == 'interpretation-context-conditioned-readings-v1'
    assert trace.interpretation.versions.semantic_parser.protocol_version == PROTOCOL_V1
    assert_replay(record)
    provenance = trace.orchestration.calls[0].provenance
    assert (provenance['context_version'], provenance['prompt_version']) == ('understanding-input-v1', 'call_one-v1.13')


def test_v1_capture_recompiles_and_rejects_a_tampered_compiled_handoff():
    record, _, _ = run()
    for call in record.pipeline_trace.orchestration.calls:
        data = call.model_dump(mode='json')
        CallCapture.model_validate(data)
        tampered = copy.deepcopy(data)
        if call.stage == 'call_two':
            tampered['compiled_proposal']['primary_index'] = None
        else:
            tampered['compiled_proposal']['interpretation']['frames'][0]['polarity'] = 'negative'
        with pytest.raises(ValidationError):
            CallCapture.model_validate(tampered)


def test_historical_moves_serialize_unchanged_and_model_policy_moves_are_marked():
    record, _, _ = run()
    move = record.pipeline_trace.action_selection.plan.moves[0]
    assert move.model_dump(mode='json')['selection_policy'] == 'model_policy'
    argmax = move.model_copy(update={'selection_policy': 'argmax'})
    assert 'selection_policy' not in argmax.model_dump(mode='json')


# ---------------------------------------------------------------------------
# Call 1: predicates, typed values, spans
# ---------------------------------------------------------------------------

FOCUS = 'Please tell me why the sky looks blue today'


def _value_for(kind, argument, payload):
    if kind == 'enum':
        return {'kind': 'enum', 'value': argument['enum_values'][0]}
    if kind == 'span':
        return {'kind': 'span', 'ref': span_of(payload, 'the sky')}
    if kind == 'span_set':
        return {'kind': 'span_set', 'refs': [span_of(payload, 'the sky'), span_of(payload, 'today')]}
    if kind == 'dialogue_span':
        return {'kind': 'dialogue_span', 'message_ref': payload['focus']['message_ref'],
                'span_ref': span_of(payload, 'the sky')}
    return NULL


# The three dependent-argument predicates have dedicated valid/invalid tests;
# blindly varying one field at a time does not produce a coherent event.
@pytest.mark.parametrize('predicate', sorted(set(V1.ARGUMENT_KINDS) - {
    'inquiry_answer', 'dialogue_recall_request', 'contextual_perspective_request'}))
def test_every_exposed_predicate_and_argument_value_kind_compiles(predicate):
    payload, entries = payload_and_entries(FOCUS)
    spec = next(p for p in payload['ontology']['predicates'] if p['predicate_key'] == predicate)
    evidence = payload['focus']['authorized_span_refs'][:1]
    for argument in spec['arguments']:
        for kind in argument['value_kinds']:
            arguments = {a['argument_key']: _value_for(a['value_kinds'][0], a, payload) for a in spec['arguments']}
            arguments[argument['argument_key']] = _value_for(kind, argument, payload)
            output = V1.compile_understanding(understanding(event(predicate, arguments, evidence)), payload, entries)
            [frame] = output.interpretation.frames
            compiled = {a.name: a.value for a in frame.arguments}
            definition = V1._MANIFEST[predicate]
            name = argument['argument_key']
            if kind == 'null' and name not in set(definition.required_arguments) | set(definition.referent_arguments):
                assert name not in compiled  # an unstated optional detail is omitted, not unresolved
                continue
            expected = {'enum': argument['enum_values'][0] if argument['enum_values'] else None, 'span': 'the sky',
                        'span_set': ('the sky', 'today'), 'dialogue_span': 'the sky', 'null': None}[kind]
            assert compiled[name] == expected


def test_entity_and_object_references_compile_to_code_owned_values():
    payload, entries = payload_and_entries(FOCUS)
    payload, entries = copy.deepcopy(payload), copy.deepcopy(entries)
    payload['dialogue_context']['entity_refs'] = [dict(ref='entity:0', kind='plan', speaker='columbina',
                                                        epistemic_status='attributed', anchor='the chickpea stew')]
    payload['dialogue_context']['open_question_refs'] = [dict(ref='object:0', purpose='discover_preference',
                                                              asked_about='spice', answer_options=['mild', 'hot'])]
    entries['entity:0'] = dict(canonical_ref='dialogue-item-1', kind='entity', anchor='the chickpea stew')
    entries['object:0'] = dict(canonical_ref='inquiry-1', kind='open_inquiry', answer_options=['mild', 'hot'])
    for spec in payload['ontology']['predicates']:
        for argument in spec['arguments']:
            if 'span' in argument['value_kinds'] and argument['argument_key'] in {'topic_ref', 'subject', 'target_ref', 'subject_ref'}:
                argument['value_kinds'].append('entity_ref')
    payload['ontology']['predicates'].append(dict(predicate_key='inquiry_answer', description='x', arguments=[
        dict(argument_key='inquiry_ref', value_kinds=['object_ref'], enum_values=[], description='x'),
        dict(argument_key='answer_kind', value_kinds=['enum'], enum_values=['option', 'free_text', 'declined'], description='x'),
        dict(argument_key='answer_option', value_kinds=['enum', 'null'], enum_values=['mild', 'hot'], description='x'),
        dict(argument_key='answer_text', value_kinds=['span', 'null'], enum_values=[], description='x')]))
    evidence = payload['focus']['authorized_span_refs'][:1]
    statement = event('statement', {'proposition': {'kind': 'span', 'ref': span_of(payload, 'the sky looks blue')},
                                    'subject': {'kind': 'entity_ref', 'ref': 'entity:0'}, 'embedding': NULL}, evidence)
    answer = event('inquiry_answer', {'inquiry_ref': {'kind': 'object_ref', 'ref': 'object:0'},
                                      'answer_kind': {'kind': 'enum', 'value': 'option'},
                                      'answer_option': {'kind': 'enum', 'value': 'hot'}, 'answer_text': NULL}, evidence)
    frames = V1.compile_understanding(understanding(statement, answer), payload, entries).interpretation.frames
    assert {a.name: a.value for a in frames[0].arguments}['subject'] == 'the chickpea stew'
    assert {a.name: a.value for a in frames[1].arguments}['inquiry_ref'] == 'inquiry-1'
    bad = copy.deepcopy(answer)
    bad['arguments']['answer_option'] = {'kind': 'enum', 'value': 'medium'}
    with pytest.raises(ValueError):
        V1.compile_understanding(understanding(statement, bad), payload, entries)
    foreign = copy.deepcopy(statement)
    foreign['arguments']['subject'] = {'kind': 'entity_ref', 'ref': 'entity:7'}
    with pytest.raises(ValueError, match='foreign local reference'):
        V1.compile_understanding(understanding(foreign), payload, entries)


@pytest.mark.parametrize('mutation,code', [
    (lambda e: e['arguments'].update(question_form={'kind': 'span', 'ref': 'span:0'}), 'argument value kind not permitted'),
    (lambda e: e['arguments'].update(question_form={'kind': 'enum', 'value': 'rhetorical'}), 'argument value outside vocabulary'),
    (lambda e: e['arguments'].pop('embedding'), 'event arguments differ from the predicate registry'),
    (lambda e: e['arguments'].update(mood={'kind': 'null'}), 'event arguments differ from the predicate registry'),
    (lambda e: e['arguments'].update(topic_ref={'kind': 'span', 'ref': 'span:40'}), 'span outside focus tokens'),
    (lambda e: e['arguments'].update(topic_ref={'kind': 'span', 'ref': 'span:4-2'}), 'reversed span reference'),
    (lambda e: e.update(predicate_key='weather_condition'), 'foreign predicate'),
    (lambda e: e['readings'].append(reading('interpretation.act.inform')), 'foreign reading key'),
    (lambda e: e['readings'].append(reading('interpretation.pragmatic.literal', context=['persona:warmth'])),
     'foreign local reference'),
    (lambda e: e['readings'].append(reading('interpretation.pragmatic.literal', context=['memory:0'])),
     'foreign local reference'),
])
def test_call_one_hard_gates_reject_out_of_contract_values(mutation, code):
    payload, entries = payload_and_entries(FOCUS)
    base = event('information_request', {'topic_ref': {'kind': 'span', 'ref': span_of(payload, 'why the sky looks blue')},
                                         'question_form': {'kind': 'enum', 'value': 'wh'}, 'embedding': NULL},
                 payload['focus']['authorized_span_refs'][:1])
    mutation(base)
    with pytest.raises(ValueError, match=code):
        V1.compile_understanding(understanding(base), payload, entries)


def test_evidence_spans_must_lie_inside_supplied_regions():
    payload, entries = payload_and_entries(FOCUS)
    entries = copy.deepcopy(entries)
    region = next(e for e in entries.values() if e['kind'] == 'evidence_region')
    region['end'] = 6  # shrink the region to "Please"
    proposal = understanding(event('social_opening', {'addressee': NULL}, ['span:2']))
    with pytest.raises(ValueError, match='span outside supplied evidence'):
        V1.compile_understanding(proposal, payload, entries)


def test_mixed_clause_local_acts_keep_exact_source_spans():
    text = 'Thanks! Can you explain why the sky is blue?'

    def one(payload):
        return understanding(
            event('express_gratitude', {'addressee': NULL}, [span_of(payload, 'Thanks')],
                  [reading('interpretation.pragmatic.literal')]),
            event('information_request', {'topic_ref': {'kind': 'span', 'ref': span_of(payload, 'why the sky is blue')},
                                          'question_form': {'kind': 'enum', 'value': 'imperative'}, 'embedding': NULL},
                  [span_of(payload, 'Can you explain why the sky is blue')],
                  [reading('interpretation.goal.obtain_information')]))
    def two(payload):
        # Closed world: the question is a knowledge gap, so only addressing candidates are selectable.
        blocking = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}
        ref = next(c['candidate_ref'] for c in payload['eligible_candidates'] if blocking <= set(c['addressed_condition_refs']))
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[ref], primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    record, _, model = run(V1Double(one=one, two=two), text)
    assert record.status.value == 'completed', record.failure
    frames = {f.predicate: f for f in record.pipeline_trace.interpretation.semantic_frames}
    assert frames['information_request'].arguments['topic_ref'] == 'why the sky is blue'
    assert set(frames['express_gratitude'].evidence_refs).isdisjoint(frames['information_request'].evidence_refs)
    assert_replay(record)


def test_negated_quoted_reported_hypothetical_and_corrected_content_compile():
    text = "Actually I meant Tuesday, and if she said 'never call' I would not."
    payload, entries = payload_and_entries(text)
    evidence = payload['focus']['authorized_span_refs'][:1]
    correction = event('correction', {'corrected_attribute': {'kind': 'span', 'ref': span_of(payload, 'I meant')},
                                      'corrected_value': {'kind': 'span', 'ref': span_of(payload, 'Tuesday')},
                                      'prior_value': NULL, 'embedding': {'kind': 'enum', 'value': 'direct'}}, evidence)
    quoted = event('statement', {'proposition': {'kind': 'span', 'ref': span_of(payload, 'never call')},
                                 'subject': NULL, 'embedding': {'kind': 'enum', 'value': 'quoted'}}, evidence)
    hypothetical = dict(event('statement', {'proposition': {'kind': 'span', 'ref': span_of(payload, 'I would not')},
                                            'subject': NULL, 'embedding': {'kind': 'enum', 'value': 'hypothetical'}}, evidence),
                        polarity='negative')
    proposal = UnderstandingProposalV1.model_validate(understanding(correction, quoted, hypothetical))
    frames = V1.compile_understanding(proposal, payload, entries).interpretation.frames
    assert [f.polarity for f in frames] == ['positive', 'positive', 'negative']
    assert [V1.derived_epistemic_status(e) for e in proposal.events] == ['user_claim', 'uncertain', 'uncertain']


def test_user_claims_and_trusted_runtime_conflicts_become_typed_contradictions():
    def one(payload):
        statement = event('statement', {'proposition': {'kind': 'span', 'ref': span_of(payload, 'I attached a photo')},
                                        'subject': NULL, 'embedding': NULL}, payload['focus']['authorized_span_refs'][:1],
                          [reading('interpretation.goal.transfer_information')])
        return understanding(statement, conflicts=[dict(left_ref=span_of(payload, 'I attached a photo'),
                                                        right_ref='runtime:attachments',
                                                        conflict_key='interpretation.claim_runtime_conflict',
                                                        status='active')])
    record, _, _ = run(V1Double(one=one, two=select('give_feedback.acknowledge')), 'I attached a photo')
    interpretation = record.pipeline_trace.interpretation if record.pipeline_trace else None
    assert record.status.value == 'completed', record.failure
    [conflict] = interpretation.contradictions
    assert conflict.description == 'interpretation.claim_runtime_conflict'
    runtime = {e.evidence_id for e in interpretation.evidence if e.source_kind.value == 'runtime_fact' and e.json_pointer == '/attachments'}
    assert runtime & set(conflict.evidence_refs)
    # Conflicts route the policy to deliberative depth.
    assert record.pipeline_trace.orchestration.calls[1].payload['policy_mode'] == 'deliberative'


def test_registered_unknowns_null_arguments_and_unregistered_keys():
    def one(payload):
        request = event('information_request', {'topic_ref': NULL, 'question_form': {'kind': 'enum', 'value': 'elliptical'},
                                                'embedding': NULL}, payload['focus']['authorized_span_refs'][:1],
                        [reading('interpretation.goal.obtain_information', uncertainty='moderate')])
        return understanding(request, unknowns=[dict(subject=dict(event_index=0, reading_index=None, argument_key='topic_ref'),
                                                     unknown_key='interpretation.missing_target', owner='user',
                                                     evidence_refs=[payload['focus']['authorized_span_refs'][0]])])
    record, _, model = run(V1Double(one=one, two=select('seek_information.clarify')), 'What about that?')
    keys = {u.key for u in record.pipeline_trace.interpretation.unknowns}
    assert {'topic_ref', 'interpretation.missing_target'} <= keys
    payload = json.loads(json.dumps(model.calls[0][1]))
    entries = json.loads(json.dumps(record.pipeline_trace.orchestration.calls[0].reference_table))
    bad = one(payload)
    bad['unknowns'][0]['unknown_key'] = 'interpretation.vibes'
    with pytest.raises(ValueError, match='foreign unknown key'):
        V1.compile_understanding(bad, payload, entries)
    bad = one(payload)
    bad['unknowns'][0]['subject']['argument_key'] = 'artifact_type'
    with pytest.raises(ValueError, match='unknown subject argument'):
        V1.compile_understanding(bad, payload, entries)


def test_named_appraisal_high_uncertainty_is_unassessed_and_scales_are_closed():
    payload, entries = payload_and_entries('Hi')
    proposal = understanding(event('social_opening', {'addressee': NULL}, ['span:0'],
                                   [reading(uncertainty='high')]))
    proposal['events'][0]['readings'][0]['appraisal']['uncertainty'] = 'high'
    mind, _ = V1.accepted_mind(proposal, payload, entries)
    assert all(value is None for value in mind.event_appraisals[0].values)
    bad = copy.deepcopy(proposal)
    bad['events'][0]['readings'][0]['appraisal']['relevance'] = 'enormous'
    with pytest.raises(ValidationError):
        UnderstandingProposalV1.model_validate(bad)
    assert 'values' not in json.dumps(response_schema('call_one', payload)['$defs']['Appraisal'])


def test_call_one_has_no_unrestricted_strings_or_model_identifiers():
    payload, _ = payload_and_entries(FOCUS)
    schema = response_schema('call_one', payload)

    def strings(node, path=''):
        if isinstance(node, dict):
            if node.get('type') == 'string':
                yield path, node
            for key, value in node.items():
                yield from strings(value, f'{path}/{key}')
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from strings(value, f'{path}/{index}')
    for path, node in strings(schema):
        if path.endswith('/resolved_question'):
            assert node['minLength'] == 1 and node['maxLength'] == 600
            continue  # v1.9 bounded interpretation, never answer text or an identifier
        assert 'enum' in node or 'pattern' in node, path
    assert 'candidate_meaning' not in json.dumps(schema) and 'handle' not in json.dumps(schema)


def test_persona_conditioning_cannot_cite_unavailable_traits_or_invent_facts():
    payload, entries = payload_and_entries('Hi')
    assert all(t['availability'] == 'unavailable' for t in payload['context']['persona_traits'])
    ok = understanding(event('social_opening', {'addressee': NULL}, ['span:0'],
                             [reading(context=['bias:trust_prior', 'state:0'])]))
    V1.compile_understanding(ok, payload, entries)
    for ref in ('persona:warmth', 'relationship:status', 'entity:0'):
        bad = understanding(event('social_opening', {'addressee': NULL}, ['span:0'], [reading(context=[ref])]))
        with pytest.raises(ValueError):
            V1.compile_understanding(bad, payload, entries)


def test_canonical_interpretation_and_appraisal_parity_with_compact_v0_1():
    from test_compact_two_call import run_compact

    def one(payload):
        literal = reading('interpretation.pragmatic.literal', uncertainty='low')
        social_reading = reading('interpretation.goal.maintain_social_connection', uncertainty='moderate')
        for item in (literal, social_reading):
            item['appraisal'] = dict(uncertainty='high', **{k: None for k in item['appraisal'] if k != 'uncertainty'})
        return understanding(event('social_opening', {'addressee': NULL}, ['span:0'], [literal, social_reading]))
    v1_record, _, _ = run(V1Double(one=one))
    v0_record, _, _ = run_compact()
    for record in (v1_record, v0_record):
        assert record.status.value == 'completed', record.failure
    view = lambda r: (
        sorted((f.predicate, f.polarity, tuple(sorted(f.arguments.items()))) for f in r.pipeline_trace.interpretation.semantic_frames),
        sorted((h.hypothesis_key, h.support.value, h.disposition.value) for h in r.pipeline_trace.interpretation.hypotheses),
        sorted((d.dimension_key, d.status, d.value) for f in r.pipeline_trace.cognitive_turn.appraisal.frames for d in f.dimensions))
    assert view(v1_record) == view(v0_record)


def test_conflicting_readings_of_different_events_keep_the_model_reading_as_an_alternative():
    text = "Actually, I don't want a schedule. I just want to talk through what matters most."

    def one(payload):
        request = lambda polarity, phrase, key: dict(event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': 'create'}, 'artifact_type': {'kind': 'enum', 'value': 'plan'},
            'target_ref': NULL, 'constraints': NULL, 'requested_form': NULL, 'outcome_certainty': {'kind': 'enum', 'value': 'clear'},
            'embedding': NULL}, [span_of(payload, phrase)], [reading(key)]), polarity=polarity)
        return understanding(request('negative', "I don't want a schedule", 'interpretation.goal.prevent_or_stop_action'),
                             request('positive', 'I just want to talk through what matters most', 'interpretation.goal.cause_action'))

    def two(payload):
        blocking = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}
        ref = next(c['candidate_ref'] for c in payload['eligible_candidates'] if blocking <= set(c['addressed_condition_refs']))
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[ref], primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    assert record.status.value == 'completed', record.failure
    hypotheses = {h.hypothesis_key: h for h in record.pipeline_trace.interpretation.hypotheses}
    assert hypotheses['interpretation.goal.cause_action'].disposition.value == 'leading'
    stop = hypotheses['interpretation.goal.prevent_or_stop_action']
    assert stop.disposition.value == 'retained_alternative'
    assert 'reading-conflict-policy-v1:retained' in stop.rule_ids
    assert_replay(record)


def test_two_requests_sharing_a_clause_region_keep_their_own_unknowns():
    text = 'Help me interpret a few possibilities, then suggest how I could talk to them.'

    def one(payload):
        request = lambda operation, phrase: event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': operation}, 'artifact_type': {'kind': 'enum', 'value': 'other'},
            'target_ref': NULL, 'constraints': NULL, 'requested_form': NULL,
            'outcome_certainty': {'kind': 'enum', 'value': 'clear'}, 'embedding': NULL},
            [span_of(payload, phrase)], [reading('interpretation.goal.cause_action')])
        return understanding(request('explain', 'Help me interpret a few possibilities'),
                             request('create', 'suggest how I could talk to them'))

    def two(payload):
        # Every open blocking condition must be addressed before the plan stops.
        open_conditions, chosen = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}, []
        for candidate in payload['eligible_candidates']:
            if set(candidate['addressed_condition_refs']) & open_conditions:
                chosen.append(candidate)
                open_conditions -= set(candidate['addressed_condition_refs'])
        # The primary is the move serving the focal goal, as the policy input marks it.
        focal = {g['ref'] for g in payload['active_goals'] if g['focal']}
        primary = next((i for i, c in enumerate(chosen) if focal & set(c['goal_refs'])), 0)
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[c['candidate_ref'] for c in chosen],
                    primary_index=primary, stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    # Previously: requirement projection raised "two resolutions describe one object differently".
    assert record.failure is None or 'requirement_resolution' not in record.failure.code, record.failure
    assert record.status.value == 'completed', record.failure
    assert_replay(record)


def test_existing_goal_impacts_are_per_event_and_merge_readings():
    payload, entries = payload_and_entries('Thanks! Can you explain that?')
    payload['context']['existing_goals'] = [dict(ref='goal:0', definition='interaction.answer_information_request',
                                                 priority='moderate')]
    entries['goal:0'] = dict(canonical_ref='goal-previous', kind='goal')
    thanks = event('express_gratitude', {'addressee': NULL}, [span_of(payload, 'Thanks')],
                   [reading('interpretation.pragmatic.literal'), reading('interpretation.goal.express_stance')])
    ask = event('elaboration_request', {'referent_phrase': NULL, 'elaboration_kind': {'kind': 'enum', 'value': 'explanation'},
                                        'embedding': NULL}, [span_of(payload, 'Can you explain that')])
    impact = lambda e, r, congruence: dict(goal_ref='goal:0', event_index=e, reading_index=r, relevance='high',
                                           congruence=congruence, uncertainty='low')
    proposal = understanding(thanks, ask, impacts=[impact(0, 0, 'positive'), impact(0, 1, 'neutral'), impact(1, None, 'positive')])
    mind, _ = V1.accepted_mind(proposal, payload, entries)
    by_event = {i.event_handles: i for i in mind.existing_goal_impacts}
    assert by_event[('e0',)].congruence is None and by_event[('e0',)].relevance == 'high'
    assert by_event[('e1',)].congruence == 'positive'
    duplicate = understanding(thanks, ask, impacts=[impact(1, None, 'positive'), impact(1, None, 'negative')])
    with pytest.raises(ValueError, match='duplicate existing goal impacts'):
        V1.compile_understanding(duplicate, payload, entries)


# ---------------------------------------------------------------------------
# Deterministic middle and Call 2 policy
# ---------------------------------------------------------------------------

def test_state_goals_and_readiness_stay_deterministic_whatever_call_two_selects():
    capture = make_capture(contents=(('user', 'Hi'),))
    greet, _, _ = run(V1Double(two=select('manage_dialogue.greet')), capture=capture)
    ack, _, _ = run(V1Double(two=select('give_feedback.acknowledge')), capture=capture)
    for record in (greet, ack):
        assert record.status.value == 'completed', record.failure
    assert greet.pipeline_trace.goal_snapshot == ack.pipeline_trace.goal_snapshot
    assert (greet.pipeline_trace.cognitive_turn.dynamic_state_update
            == ack.pipeline_trace.cognitive_turn.dynamic_state_update)
    assert greet.pipeline_trace.requirement_resolution == ack.pipeline_trace.requirement_resolution
    assert (greet.pipeline_trace.action_selection.plan.moves[0].definition_ref.action_key
            != ack.pipeline_trace.action_selection.plan.moves[0].definition_ref.action_key)


def test_call_two_input_is_fully_bound_and_output_has_no_content_or_wording():
    record, _, model = run()
    payload = model.calls[1][1]
    for candidate in payload['eligible_candidates']:
        assert set(candidate) == {'candidate_ref', 'action_key', 'goal_refs', 'target_refs', 'typed_parameters',
                                  'planned_effect_keys', 'addressed_condition_refs', 'addressed_condition_keys',
                                  'cost_band', 'risk_band', 'answer_content'}
    schema = response_schema('call_two', payload)
    assert set(schema['properties']) == {'schema_version', 'selected_candidate_refs', 'primary_index',
                                         'stop_after_selected', 'selection_factors'}
    for field in ('segments', 'content_ops', 'moves', 'text', 'rationale'):
        raw = dict(select('manage_dialogue.greet')(payload), **{field: []})
        with pytest.raises(ValidationError):
            ActionPolicyProposalV1.model_validate(raw)
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-|action-plan-|semantic-frame-', json.dumps(payload))


@pytest.mark.parametrize('mutate,code', [
    (lambda raw, p: raw.update(selected_candidate_refs=['candidate:99']), 'foreign_candidate'),
    (lambda raw, p: raw.update(stop_after_selected=False), 'iterative_planning_is_not_offered'),
    (lambda raw, p: raw.update(selected_candidate_refs=[], primary_index=None), 'empty_plan_for_a_required_response'),
    (lambda raw, p: raw.update(selection_factors=[dict(candidate_ref=raw['selected_candidate_refs'][0],
                                                       factor_keys=['personality.warmth'], source_refs=[])]),
     'personality_factor_without_available_personality'),
    (lambda raw, p: raw.update(selection_factors=[dict(candidate_ref=raw['selected_candidate_refs'][0],
                                                       factor_keys=['goal.direct_progress'], source_refs=['goal:9'])]),
     'foreign_selection_factor_source'),
])
def test_call_two_hard_gates(mutate, code):
    def two(payload):
        raw = select('manage_dialogue.greet')(payload)
        mutate(raw, payload)
        return raw
    record, _, _ = run(V1Double(two=two))
    assert record.status.value == 'failed'
    assert f'policy_compilation.{code}' in record.failure.code


def test_plans_are_bounded_by_the_routed_maximum():
    record, pipeline, model = run(V1Double(two=select('manage_dialogue.greet', 'give_feedback.acknowledge')))
    payload = json.loads(json.dumps(model.calls[1][1]))
    entries = json.loads(json.dumps(pipeline.two_call.captures[1].reference_table))
    raw = select('manage_dialogue.greet', 'give_feedback.acknowledge')(payload)
    V1.compile_policy(raw, payload, entries)
    payload['max_moves'] = 1
    with pytest.raises(ValueError, match='plan exceeds maximum moves'):
        V1.compile_policy(raw, payload, entries)
    assert response_schema('call_two', dict(payload, max_moves=1))['properties']['selected_candidate_refs']['maxItems'] == 1


def test_selection_shape_uniqueness_and_primary_bounds():
    base = dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=['candidate:0', 'candidate:0'],
                primary_index=0, stop_after_selected=True, selection_factors=[])
    with pytest.raises(ValidationError):
        ActionPolicyProposalV1.model_validate(base)
    with pytest.raises(ValidationError):
        ActionPolicyProposalV1.model_validate(dict(base, selected_candidate_refs=['candidate:0'], primary_index=1))
    with pytest.raises(ValidationError):
        ActionPolicyProposalV1.model_validate(dict(base, selected_candidate_refs=[], primary_index=0))


def test_legacy_primary_must_carry_the_greatest_focal_coverage(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'BEHAVIOR_POLICY_V1', 'model_policy_selection-v1')
    record, _, _ = run(V1Double(two=select('give_feedback.acknowledge', 'manage_dialogue.greet', primary=0)))
    assert record.status.value == 'failed'
    assert 'behavior_proposal_ineligible.primary_not_focal' in record.failure.code
    record, _, _ = run(V1Double(two=select('give_feedback.acknowledge', 'manage_dialogue.greet', primary=1)))
    assert record.status.value == 'completed', record.failure
    moves = record.pipeline_trace.action_selection.plan.moves
    assert [m.definition_ref.action_key for m in moves] == ['give_feedback.acknowledge', 'manage_dialogue.greet']
    assert record.pipeline_trace.action_selection.plan.primary_move_ref == moves[1].move_id
    monkeypatch.undo()
    assert_replay(record)


def test_model_primary_with_lower_coverage_does_not_reject_an_eligible_plan():
    record, _, _ = run(V1Double(two=select('give_feedback.acknowledge', 'manage_dialogue.greet', primary=0)))
    assert record.status.value == 'completed', record.failure
    plan = record.pipeline_trace.action_selection.plan
    assert [m.definition_ref.action_key for m in plan.moves] == ['give_feedback.acknowledge', 'manage_dialogue.greet']
    assert plan.primary_move_ref == plan.moves[0].move_id
    compared = plan.primary_designation.compared_moves
    assert float(compared[0]['direct_focal_coverage']) < float(compared[1]['direct_focal_coverage'])
    assert_replay(record)
    realized = _realized(record, ('I hear you. Hi!', {1: 'I hear you.', 2: 'Hi!'}))
    assert realized.realization_status.value == 'accepted_shadow', _final(realized)


def test_primary_rule_cannot_be_tampered_independently_of_behavior_policy():
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineTrace
    record, _, _ = run()
    payload = record.pipeline_trace.model_dump(mode='json')
    # Both duplicated action views must stay equal so this tests policy lineage.
    for selection in (payload['action_selection'], payload['cognitive_turn']['action_selection']):
        selection['plan']['primary_designation']['rule_id'] = 'action-primary.model-selected-focal-coverage.v1'
    with pytest.raises(ValidationError):
        CognitivePipelineTrace.model_validate(payload)


def _blocked(payload):
    return understanding(event('information_request', {'topic_ref': NULL, 'question_form': {'kind': 'enum', 'value': 'elliptical'},
                                                       'embedding': NULL}, payload['focus']['authorized_span_refs'][:1],
                               [reading('interpretation.goal.obtain_information', uncertainty='moderate')]))


def test_blocking_conditions_cannot_be_skipped_and_clarification_is_an_action():
    record, _, model = run(V1Double(one=_blocked, two=select('give_feedback.acknowledge')), 'What about that?')
    payload = model.calls[1][1]
    assert any(c['blocking'] for c in payload['planning_conditions'])
    assert payload['policy_mode'] == 'deliberative'
    assert record.status.value == 'failed'
    assert 'behavior_proposal_ineligible.skips_blocking_condition' in record.failure.code
    record, _, model = run(V1Double(one=_blocked, two=select('seek_information.clarify')), 'What about that?')
    assert record.status.value == 'completed', record.failure
    [move] = record.pipeline_trace.action_selection.plan.moves
    assert move.definition_ref.action_key == 'seek_information.clarify'
    # Typed target and question form; the wording belongs to Realization.
    assert [t.target_type.value for t in move.target_refs] == ['unknown']
    assert move.parameters['question_form'] == 'open_set'
    assert_replay(record)


def test_complex_multi_step_plans_are_representable():
    record, _, model = run(V1Double(two=select('manage_dialogue.greet', 'give_feedback.acknowledge')))
    assert record.status.value == 'completed', record.failure
    assert len(record.pipeline_trace.action_selection.plan.moves) == 2
    assert_replay(record)


# ---------------------------------------------------------------------------
# Content subsystem before Call 2 (artifact deliverables)
# ---------------------------------------------------------------------------

def _dinner(deliver=True):
    from simulated_authority_support import DINNER, dinner_script
    from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
    from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import ScriptedCollaborationModel
    text = DINNER['turns'][1]['user']

    def one(payload):
        return understanding(event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': 'create'}, 'artifact_type': {'kind': 'enum', 'value': 'meal_plan'},
            'target_ref': NULL, 'requested_form': NULL, 'outcome_certainty': {'kind': 'enum', 'value': 'clear'},
            'embedding': NULL, 'constraints': {'kind': 'span_set', 'refs': [
                span_of(payload, 'vegetarian'), span_of(payload, 'six people'), span_of(payload, 'under 30 minutes')]}},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.cause_action')]))

    def two(payload):
        [deliver_ref] = [ref for row in payload['goal_readiness'] for ref in row['delivery_candidate_refs']]
        refs = [deliver_ref] if deliver else [next(c['candidate_ref'] for c in payload['eligible_candidates']
                                                    if c['action_key'] == 'give_feedback.acknowledge')]
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=refs, primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    engine = CollaborativeContentEngine(ScriptedCollaborationModel(dinner_script))
    return run(V1Double(one=one, two=two), text, collaboration=engine)


def test_content_runs_before_call_two_and_its_delivery_is_a_bound_candidate():
    record, pipeline, model = _dinner()
    assert record.status.value == 'completed', record.failure
    assert record.pipeline_trace.collaboration.outcomes[0].status == 'proposed'
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == [
        'collaborate.deliver_artifact']
    components = [s.component for s in record.pipeline_trace.orchestration.spans]
    assert components.index('content_preparation') < components.index('provider_attempt', components.index('content_preparation'))
    assert 'plan_compilation' in components
    assert_replay(record)


def test_omitting_prepared_content_delivery_is_a_typed_policy_failure():
    record, _, _ = _dinner(deliver=False)
    assert record.status.value == 'failed'
    assert 'behavior_proposal_ineligible.stop_omitted_delivery' in record.failure.code


# ---------------------------------------------------------------------------
# Realization boundary, service wiring, summaries, training projection
# ---------------------------------------------------------------------------

def test_cognitive_completion_survives_realization_failure_and_never_uses_call_two_wording():
    record, _, _ = run()

    class Exploding:
        def realize(self, **kwargs):
            raise RuntimeError('provider down')
    failed = realize_record(record, engine=Exploding(), configured_maximum_characters=1200)
    assert failed.status.value == 'completed' and failed.pipeline_trace == record.pipeline_trace
    assert failed.realization_status.value == 'execution_failed'
    unavailable = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=1200)
    assert unavailable.realization_status.value == 'provider_unavailable'


def test_shadow_service_factory_selects_v1_and_installs_the_content_engine_first(monkeypatch):
    from types import SimpleNamespace
    from app.services.cognitive_shadow import cognitive_pipeline
    from app.ai.Cognitive.system.v0.two_call import provider
    from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
    model = V1Double()
    monkeypatch.setattr(provider, 'OpenAITwoCallModel', lambda **kwargs: model)
    settings = SimpleNamespace(cognitive_shadow_orchestration='two_call', cognitive_shadow_two_call_protocol=PROTOCOL_V1,
                               cognitive_shadow_semantic_parser_model='gpt-6-luna', openai_api_key=None,
                               cognitive_shadow_semantic_parser_timeout_seconds=20,
                               cognitive_shadow_orchestration_max_output_tokens=6000,
                               cognitive_shadow_collaboration_enabled=False)
    capture = make_capture(contents=(('user', 'Hi'),))
    pipeline = cognitive_pipeline(capture.turn_id, settings)
    assert pipeline.two_call.protocol == PROTOCOL_V1
    assert isinstance(pipeline.collaboration_engine, CollaborativeContentEngine)
    record = observe(capture, None, ObservationContext(job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
                     started_at='2026-10-05T00:00:00+00:00'), pipeline_factory=lambda _: pipeline)
    assert record.status.value == 'completed', record.failure


def test_trace_summary_separates_v1_components():
    from test_cognitive_shadow_trace_summary import _row
    from app.ai.Cognitive.system.v0.trace_summary import summarize_trace
    record, _, _ = _dinner()
    latency = summarize_trace(_row(record))['latency']
    assert latency['schema_version'] == 'cognitive-latency-summary-v0.4'
    assert {'call_one.provider_attempt', 'call_two.content_preparation', 'call_two.provider_attempt',
            'call_two.plan_compilation'} <= set(latency['component_ms'])
    assert latency['persistence_measurement'] == 'not_recorded_in_trace' and latency['model_call_count'] == 2


def test_training_candidates_are_unreviewed_identifier_free_and_typed_on_failure():
    record, _, _ = _dinner()
    example = project_training_example(record)
    assert example['schema_version'] == 'cognitive-training-example-v1'
    evaluation = example['evaluation']
    assert evaluation['contract_status'] == 'valid' and evaluation['promotion_status'] == 'not_promoted'
    assert {evaluation[k] for k in ('cognitive_review_status', 'behavior_review_status', 'personality_fidelity_status',
                                     'domain_label_status')} == {'unreviewed'}
    blob = json.dumps(example)
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-|semantic-frame-|goal-[0-9a-f]{8}|span:\d|candidate:\d|latency|prompt', blob)
    plan = example['target']['action_plan']
    assert plan['moves'][0]['action_key'] == 'collaborate.deliver_artifact' and plan['primary_index'] == 0
    assert example['target']['interpretation']['events'][0]['arguments']['constraints'] == [
        'vegetarian', 'six people', 'under 30 minutes']
    failed, _, _ = _dinner(deliver=False)
    negative = project_training_example(failed)
    assert negative['evaluation']['contract_status'] == 'invalid'
    assert negative['evaluation']['failure_category'] == 'policy' and negative['target'] is None


def test_span_reference_grammar():
    assert parse_span_ref('span:3') == (3, 3) and parse_span_ref('span:2-5') == (2, 5)
    for bad in ('span:', 'span:-1', 'span:03', 'r12', 'span:1-2-3'):
        with pytest.raises(ValueError):
            parse_span_ref(bad)


def test_latency_span_v0_3_components_are_versioned():
    from app.ai.Cognitive.system.v0.latency import LatencySpan
    LatencySpan(schema_version='cognitive-latency-span-v0.3', stage='call_two', span_id='call_two.plan_compilation.1',
                component='plan_compilation', status='completed', elapsed_ms=1.0)
    with pytest.raises(ValidationError):
        LatencySpan(schema_version='cognitive-latency-span-v0.2', stage='call_two', span_id='call_two.plan_compilation.1',
                    component='plan_compilation', status='completed', elapsed_ms=1.0)


def test_collaboration_provider_forwards_the_manifest_reasoning_control():
    from app.ai.Cognitive.system.v0.collaboration_provider import resolve_provenance
    provenance = resolve_provenance(model_id='gpt-6-luna', timeout_seconds=25)
    assert provenance.reasoning_effort == 'none'  # custom temperature is accepted only without reasoning
    legacy = provenance.model_copy(update={'reasoning_effort': None})
    assert 'reasoning_effort' not in legacy.model_dump(mode='json')  # earlier recorded provenance is unchanged


# ---------------------------------------------------------------------------
# Understanding grounding v1.1: experience, claimed affect, concern
# ---------------------------------------------------------------------------

SCATTERED = "I've been feeling scattered lately, and I'm worried about my exam."


def experience(payload, *, polarity='positive', experiencer='speaker', embedding=NULL, concern=True):
    return dict(event('experience_report', {
        'experience': {'kind': 'span', 'ref': span_of(payload, 'been feeling scattered lately')},
        'feeling': {'kind': 'span', 'ref': span_of(payload, 'scattered')},
        'affect': {'kind': 'enum', 'value': 'scattered'},
        'concern': {'kind': 'span', 'ref': span_of(payload, 'my exam')} if concern else NULL,
        'experiencer': {'kind': 'enum', 'value': experiencer}, 'embedding': embedding},
        payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.seek_acknowledgement')]), polarity=polarity)


def test_experience_report_grounds_objects_and_unlocks_enabled_empathy_actions():
    offered = {}

    def two(payload):
        offered.update({c['action_key']: c for c in payload['eligible_candidates']})
        return select('give_feedback.reflect')(payload)
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p)), two=two), SCATTERED)
    assert 'experience_report' in {p['predicate_key'] for p in model.calls[0][1]['ontology']['predicates']}
    assert record.status.value == 'completed', record.failure
    objects = {o.object_type.value: o for o in record.pipeline_trace.cognitive_turn.input_snapshot.action_grounded_objects
               if o.object_id.startswith('understanding-')}
    assert set(objects) == {'experience', 'claimed_affect', 'concern'}
    assert objects['experience'].asserted_by == 'user' and objects['experience'].valence == 'negative'
    assert {'give_feedback.reflect', 'support_socially.validate_experience', 'social_play.playful_reframe'} <= set(offered)
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == ['give_feedback.reflect']
    assert_replay(record)
    from app.ai.Cognitive.system.v0.live_shadow.realization import understanding_content_of
    [reference] = understanding_content_of(record.pipeline_trace)
    # Anchors are only the user's own words (feeling first for a claimed-affect target).
    assert set(reference.surface_anchors) == {'been feeling scattered lately', 'scattered'}
    assert reference.attribution.value == 'user' and reference.reference_form.value == 'anchored'
    # Realization compiles the reflection (only the provider is absent here).
    realized = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=1200)
    assert realized.realization_status.value == 'provider_unavailable'


@pytest.mark.parametrize('variant', [dict(polarity='negative'), dict(experiencer='other'),
                                     dict(embedding={'kind': 'enum', 'value': 'hypothetical'})])
def test_negated_others_and_hypothetical_experiences_ground_nothing(variant):
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p, **variant)),
                                    two=select('give_feedback.acknowledge')), SCATTERED)
    assert record.status.value == 'completed', record.failure
    assert not [o for o in record.pipeline_trace.cognitive_turn.input_snapshot.action_grounded_objects
                if o.object_id.startswith('understanding-')]
    assert 'give_feedback.reflect' not in {c['action_key'] for c in model.calls[1][1]['eligible_candidates']}


def test_v1_0_captures_keep_their_predicates_and_replay(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1')
    record, _, model = run()
    assert model.calls[0][1]['ontology']['predicate_registry_version'] == 'argument-kind-registry-v1'
    assert 'experience_report' not in {p['predicate_key'] for p in model.calls[0][1]['ontology']['predicates']}
    monkeypatch.undo()
    assert_replay(record)


def test_earlier_protocol_payloads_never_offer_the_v1_1_predicate():
    from test_compact_two_call import run_compact
    from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import predicate_specs
    assert 'experience_report' not in {p.predicate for p in predicate_specs()}
    _, _, compact = run_compact()
    assert 'experience_report' not in {p['predicate'] for p in compact.calls[0][1]['predicates']}


def test_trace_rejects_tampered_understanding_objects():
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineTrace
    record, _, _ = run(V1Double(one=lambda p: understanding(experience(p)), two=select('give_feedback.reflect')), SCATTERED)
    payload = record.pipeline_trace.model_dump(mode='json')
    objects = payload['cognitive_turn']['input_snapshot']['action_grounded_objects']
    target = next(o for o in objects if o['object_type'] == 'experience')
    target['valence'] = 'positive'
    with pytest.raises(ValidationError):
        CognitivePipelineTrace.model_validate(payload)


def test_validated_experience_wording_is_accepted_and_still_bounded():
    from realization_v0_support import compile_request, validate_text
    from app.ai.Cognitive.system.v0.live_shadow.realization import _realization_inputs
    record, _, _ = run(V1Double(one=lambda p: understanding(experience(p, concern=False)),
                                two=select('support_socially.validate_experience')), "I've been feeling scattered lately.")
    assert record.status.value == 'completed', record.failure
    # No uncertainty constraint for the speaker's own account.
    [move] = record.pipeline_trace.action_selection.plan.moves
    assert 'preserve_uncertainty' not in {c.code for c in move.realization_constraints}
    request = compile_request(_realization_inputs(record, None))
    # The live run's natural wording (previously rejected for attribution/uncertainty).
    accepted = validate_text(request, "It makes sense that you've been feeling scattered lately.")
    assert accepted.status == 'accepted_for_shadow_review' and not accepted.violations, accepted.violations
    # Still bounded: no new facts, and the user's words must be present.
    for text in ("It makes sense; scattered feelings are caused by stress hormones.", "That makes sense."):
        assert validate_text(request, text).violations, text


# ---------------------------------------------------------------------------
# v1.2: conversation, not a produced artifact
# ---------------------------------------------------------------------------

def test_optional_experience_exploration_completes_through_realization_and_replay():
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p)),
                                    two=select('seek_information.explore_experience')), SCATTERED)
    assert record.status.value == 'completed', record.failure
    policy = model.calls[1][1]
    chosen = record.pipeline_trace.orchestration.calls[1].proposal['selected_candidate_refs']
    assert set(chosen) <= set(policy['optional_exploration_candidate_refs'])
    assert not policy['planning_conditions']
    assert not policy['accepted_understanding']['unknown_refs']
    [move] = record.pipeline_trace.action_selection.plan.moves
    assert move.target_refs[0].target_type.value == 'experience'
    assert move.parameters['question_form'] == 'free_reflection'
    assert not record.pipeline_trace.collaboration.outcomes
    assert [stage for stage, _ in model.calls] == ['call_one', 'call_two']
    assert 'opportunities, not obligations' in prompt('call_two', policy)
    assert_replay(record)
    realized = _realized(record, "What has it been like for you, feeling scattered lately?")
    assert realized.realization_status.value == 'accepted_shadow', realized.realization_failure


def test_optional_exploration_can_be_ignored_or_follow_a_reflection():
    for keys in [('give_feedback.reflect',), ('give_feedback.reflect', 'seek_information.explore_experience')]:
        record, _, model = run(V1Double(one=lambda p: understanding(experience(p)), two=select(*keys)), SCATTERED)
        assert record.status.value == 'completed', record.failure
        assert model.calls[1][1]['optional_exploration_candidate_refs']
        assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == list(keys)
        assert_replay(record)


@pytest.mark.parametrize('variant', [dict(polarity='negative'), dict(experiencer='other'),
                                    dict(embedding={'kind': 'enum', 'value': 'hypothetical'}),
                                    dict(embedding={'kind': 'enum', 'value': 'reported'})])
def test_optional_exploration_never_invents_a_present_user_experience(variant):
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p, **variant)),
                                    two=select('give_feedback.acknowledge')), SCATTERED)
    assert record.status.value == 'completed', record.failure
    assert not model.calls[1][1]['optional_exploration_candidate_refs']
    assert 'seek_information.explore_experience' not in {
        c['action_key'] for c in model.calls[1][1]['eligible_candidates']}


def test_v1_2_replay_keeps_the_old_exploration_inventory(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.2')
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p)),
                                    two=select('give_feedback.reflect')), SCATTERED)
    assert record.status.value == 'completed', record.failure
    assert 'optional_exploration_candidate_refs' not in model.calls[1][1]
    assert 'seek_information.explore_experience' not in {
        c['action_key'] for c in model.calls[1][1]['eligible_candidates']}
    monkeypatch.undo()
    assert_replay(record)


def test_optional_exploration_policy_sees_recent_dialogue_without_changing_replay():
    contents = (('user', SCATTERED), ('assistant', 'What part has felt scattered?'),
                ('user', "I've been feeling scattered lately, but please don't ask more questions."))
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p, concern=False)),
                                    two=select('give_feedback.reflect')), contents=contents)
    assert record.status.value == 'completed', record.failure
    window = model.calls[1][1]['conversation_window']
    assert [m['text'] for m in window] == [text for _, text in contents]
    assert [m['current_turn'] for m in window] == [False, False, True]
    assert_replay(record)


def test_optional_exploration_descriptor_is_bound_to_the_versioned_projector():
    from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineTrace
    from app.ai.Cognitive.system.v0.contracts.actions import ActionTargetDescriptor
    record, _, _ = run(V1Double(one=lambda p: understanding(experience(p)),
                                two=select('give_feedback.reflect')), SCATTERED)
    payload = record.pipeline_trace.model_dump(mode='json')
    descriptors = payload['cognitive_turn']['input_snapshot']['action_target_descriptors']
    descriptor = next(d for d in descriptors if d.get('optional_exploration'))
    descriptor['optional_exploration'] = False
    with pytest.raises(ValidationError):
        CognitivePipelineTrace.model_validate(payload)
    with pytest.raises(ValidationError):
        ActionTargetDescriptor(ref='fake', target_type='unknown', owner='user', optional_exploration=True)


def test_optional_exploration_never_displaces_a_blocking_condition():
    def one(payload):
        return understanding(experience(payload), event('runtime_fact_request', {
            'fact_kind': {'kind': 'enum', 'value': 'current_time'}, 'embedding': NULL},
            [span_of(payload, 'what time is it')], [reading('interpretation.goal.obtain_information')]))
    record, _, model = run(V1Double(one=one, two=select('seek_information.explore_experience')),
                            SCATTERED + ' Also, what time is it?')
    assert model.calls[1][1]['optional_exploration_candidate_refs']
    assert any(c['blocking'] for c in model.calls[1][1]['planning_conditions'])
    assert record.status.value == 'failed'
    assert 'blocking' in record.failure.code, record.failure


TALK = "Actually, I don't want a schedule. I just want to talk through what matters most."


def conversation(payload, *, mode='talk_through', embedding=NULL):
    return event('conversation_request', {'topic': {'kind': 'span', 'ref': span_of(payload, 'what matters most')},
                                          'mode': {'kind': 'enum', 'value': mode}, 'embedding': embedding},
                 [span_of(payload, 'I just want to talk through what matters most')],
                 [reading('interpretation.goal.maintain_social_connection')])


def test_conversation_request_explores_instead_of_forcing_an_artifact_clarification():
    offered = {}

    def two(payload):
        offered.update({c['action_key']: c for c in payload['eligible_candidates']})
        return select('seek_information.explore_goal')(payload)
    record, _, model = run(V1Double(one=lambda p: understanding(conversation(p)), two=two), TALK)
    payload = model.calls[0][1]
    assert payload['ontology']['predicate_registry_version'] == 'argument-kind-registry-v1.13'
    assert 'conversation_request' in {p['predicate_key'] for p in payload['ontology']['predicates']}
    assert 'use conversation_request instead of collaborative_content_request' in prompt('call_one', payload)
    assert record.status.value == 'completed', record.failure
    # No artifact deliverable: no blocking target, no forced "which plan or draft" clarification.
    assert not any(c['blocking'] for c in model.calls[1][1]['planning_conditions'])
    assert 'seek_information.explore_goal' in offered
    [move] = record.pipeline_trace.action_selection.plan.moves
    assert move.definition_ref.action_key == 'seek_information.explore_goal'
    assert_replay(record)
    from app.ai.Cognitive.system.v0.live_shadow.realization import understanding_content_of
    [reference] = understanding_content_of(record.pipeline_trace)
    assert reference.surface_anchors == ('what matters most',) and reference.epistemic_status.value == 'unresolved'
    realized = realize_record(record, engine=RealizationEngineV0(None), configured_maximum_characters=1200)
    assert realized.realization_status.value == 'provider_unavailable'  # compiled; only the provider is absent


def test_quoted_or_hypothetical_conversation_requests_ground_nothing():
    record, _, model = run(V1Double(one=lambda p: understanding(conversation(p, embedding={'kind': 'enum', 'value': 'reported'})),
                                    two=select('give_feedback.acknowledge')), TALK)
    assert record.status.value == 'completed', record.failure
    assert not record.pipeline_trace.cognitive_turn.input_snapshot.action_target_descriptors or all(
        not d.ref.startswith('understanding-') for d in record.pipeline_trace.cognitive_turn.input_snapshot.action_target_descriptors)


def test_v1_1_captures_keep_their_prompt_and_predicates(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'ARGUMENT_KIND_REGISTRY_VERSION', 'argument-kind-registry-v1.1')
    record, _, model = run(V1Double(one=lambda p: understanding(experience(p)), two=select('give_feedback.reflect')), SCATTERED)
    payload = model.calls[0][1]
    assert 'conversation_request' not in {p['predicate_key'] for p in payload['ontology']['predicates']}
    assert 'use conversation_request instead of collaborative_content_request' not in prompt('call_one', payload)
    monkeypatch.undo()
    assert_replay(record)


# ---------------------------------------------------------------------------
# conversational-v1 Realization policy: style/bookkeeping advisory, honesty hard
# ---------------------------------------------------------------------------

@pytest.fixture
def conversational_v1(monkeypatch):
    """Record new turns under realization policy v1.1, whose wording validates with ``conversational-v1``."""
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'REALIZATION_POLICY_V1', 'separate_realization-v1.1')


def _realized(record, text):
    """Word one completed v1 record through the real engine with a scripted provider."""
    from realization_v0_support import ScriptedModel
    return realize_record(record, engine=RealizationEngineV0(ScriptedModel(text), max_attempts=1),
                          configured_maximum_characters=1200)


def _final(realized):
    trace = realized.realization_trace
    return realized.realization_status.value, [v.code.value for a in trace.attempts for v in a.validation.violations], \
        [w.code.value for a in trace.attempts for w in a.validation.warnings], trace.attempts[0].validation.validation_policy


def test_v1_wording_uses_the_conversational_policy_and_style_is_advisory(conversational_v1):
    record, _, _ = run()  # greet
    status, violations, warnings, policy = _final(_realized(record, "Hi! How about you?"))
    assert policy == 'conversational-v1'
    assert status == 'accepted_shadow' and not violations
    assert {'exclamation_used', 'unplanned_question'} <= set(warnings)


@pytest.mark.parametrize('text,code', [
    ("Hi! I've sent the email for you.", 'false_tool_completion_claim'),
    ("Hi! As you told me last week, you love hiking.", 'unrecorded_memory_claimed'),
    ("Hi! I'll remind you tomorrow.", 'unplanned_commitment'),
    ("Hi! I'm feeling so sad today.", 'hidden_affect_asserted'),
    ("Hi! You should quit your job.", 'unplanned_advice'),
])
def test_v1_wording_keeps_honesty_and_safety_hard(text, code, conversational_v1):
    record, _, _ = run()
    status, violations, _, _ = _final(_realized(record, text))
    assert status == 'rejected' and code in violations, violations


def test_clarification_anchor_matches_natural_question_grammar_only_when_conversational():
    from realization_v0_support import compile_request, excerpt_output, provenance
    from app.ai.Cognitive.system.v0.live_shadow.realization import _realization_inputs
    from app.ai.Cognitive.system.v0.realization_v0 import RealizationValidatorV0, build_candidate, project_payload
    text = 'Make it a little more playful.'

    def one(payload):
        return understanding(event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': 'revise'}, 'artifact_type': {'kind': 'enum', 'value': 'plan'},
            'target_ref': NULL, 'constraints': {'kind': 'span_set', 'refs': [span_of(payload, 'a little more playful')]},
            'requested_form': NULL, 'outcome_certainty': {'kind': 'enum', 'value': 'clear'}, 'embedding': NULL},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.cause_action')]))

    def two(payload):
        blocking = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}
        ref = next(c['candidate_ref'] for c in payload['eligible_candidates']
                   if c['action_key'] == 'seek_information.clarify' and blocking & set(c['addressed_condition_refs']))
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[ref], primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    assert record.status.value == 'completed', record.failure
    request = compile_request(_realization_inputs(record, None))
    payload = project_payload(request)
    candidate = build_candidate(request, 1, excerpt_output(payload, 'Which plan or draft do you mean?', whole=True), provenance())
    strict = RealizationValidatorV0().validate(request, candidate)
    conversational = RealizationValidatorV0(policy='conversational-v1').validate(request, candidate)
    assert 'content_alignment_missing' in {v.code.value for v in strict.violations}
    assert not conversational.violations, conversational.violations


def test_tolerant_excerpts_only_in_the_conversational_provider():
    from app.ai.Cognitive.system.v0.realization_v0.model_protocol import _locate
    text = "It makes sense that you’ve been  feeling scattered."
    assert _locate(text, "you've been feeling scattered") == (-1, -1)
    start, end = _locate(text, "you've been feeling scattered", tolerant=True)
    assert text[start:end] == "you’ve been  feeling scattered"


def test_instruction_variant_is_recorded_only_when_used():
    from app.ai.Cognitive.system.v0.realization_provider import OpenAIRealizationModel
    from app.ai.Cognitive.system.v0.realization_v0.model_protocol import response_json_schema, CONVERSATIONAL_TEXT_DESCRIPTION
    strict = OpenAIRealizationModel(model_id='gpt-6-luna', api_key=None, timeout_seconds=20, client=object())
    assert 'instruction_variant' not in strict.provenance.model_dump(mode='json')
    conversational = strict.with_instruction_variant('conversational-v1')
    assert conversational.provenance.instruction_variant == 'conversational-v1'
    assert response_json_schema('conversational-v1')['properties']['text']['description'] == CONVERSATIONAL_TEXT_DESCRIPTION
    assert 'Do not add greetings' in response_json_schema()['properties']['text']['description']


def _gap_turn(text='What should I do next?'):
    def one(payload):
        return understanding(event('information_request', {
            'topic_ref': {'kind': 'span', 'ref': span_of(payload, text.rstrip('?'))},
            'question_form': {'kind': 'enum', 'value': 'wh'}, 'embedding': NULL},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.obtain_information')]))
    record, _, _ = run(V1Double(one=one, two=select('provide_information.answer')), text)
    assert record.status.value == 'completed', record.failure
    return record


@pytest.mark.parametrize('text,ok', [
    ("I don't know what you should do next.", True),       # a stated gap, not advice
    ('I honestly don’t know “what should I do next” either.', True),  # quoting the user's words
    ("You should take a long walk and call a friend.", False),  # real advice stays hard
    ('I don’t know, but “experts agree” it passes.', False),  # an invented quote stays hard
])
def test_knowledge_gap_wording_precision(text, ok, conversational_v1):
    status, violations, _, _ = _final(_realized(_gap_turn(), text))
    assert (status == 'accepted_shadow') is ok, violations


def test_reflection_anchor_tolerates_fillers_but_not_unrelated_wording(conversational_v1):
    text = 'Mostly because I have too many small things on my mind.'

    def one(payload):
        return understanding(dict(event('experience_report', {
            'experience': {'kind': 'span', 'ref': span_of(payload, 'Mostly because I have too many small things on my mind')},
            'feeling': NULL, 'affect': NULL, 'concern': NULL,
            'experiencer': {'kind': 'enum', 'value': 'speaker'}, 'embedding': NULL},
            payload['focus']['authorized_span_refs'][:1])))
    record, _, _ = run(V1Double(one=one, two=select('give_feedback.reflect')), text)
    assert record.status.value == 'completed', record.failure
    assert _final(_realized(record, 'It sounds like you have too many small things on your mind.'))[0] == 'accepted_shadow'
    assert _final(_realized(record, 'It sounds like your week was great.'))[0] == 'rejected'


def test_two_elaboration_requests_missing_one_referent_keep_their_own_unknowns():
    text = 'Could you explain that without psychology jargon and give one everyday example?'

    def one(payload):
        ask = lambda kind, phrase: event('elaboration_request', {
            'referent_phrase': NULL, 'elaboration_kind': {'kind': 'enum', 'value': kind}, 'embedding': NULL},
            [span_of(payload, phrase)])
        return understanding(ask('explanation', 'Could you explain that without psychology jargon'),
                             ask('example', 'and give one everyday example'))

    def two(payload):
        open_conditions, chosen = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}, []
        for candidate in payload['eligible_candidates']:
            if set(candidate['addressed_condition_refs']) & open_conditions:
                chosen.append(candidate)
                open_conditions -= set(candidate['addressed_condition_refs'])
        focal = {g['ref'] for g in payload['active_goals'] if g['focal']}
        primary = next((i for i, c in enumerate(chosen) if focal & set(c['goal_refs'])), 0)
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[c['candidate_ref'] for c in chosen],
                    primary_index=primary, stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    # Previously (live B2): "two resolutions describe one object differently" when
    # both frames elicited the same missing referent.
    assert record.failure is None or 'requirement_resolution' not in record.failure.code, record.failure
    assert record.status.value == 'completed', record.failure
    assert_replay(record)


def test_a_reply_that_asks_first_may_leave_gap_conditions_open():
    text = 'Could you explain that without psychology jargon and give one everyday example?'

    def one(payload):
        ask = lambda kind, phrase: event('elaboration_request', {
            'referent_phrase': NULL, 'elaboration_kind': {'kind': 'enum', 'value': kind}, 'embedding': NULL},
            [span_of(payload, phrase)])
        return understanding(ask('explanation', 'Could you explain that without psychology jargon'),
                             ask('example', 'and give one everyday example'))

    def two(payload):
        # Live B2: the model asks what "that" means and stops there.
        clarify = [c for c in payload['eligible_candidates'] if c['action_key'] == 'seek_information.clarify']
        assert clarify, payload['eligible_candidates']
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[clarify[0]['candidate_ref']],
                    primary_index=0, stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    assert record.status.value == 'completed', record.failure
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == ['seek_information.clarify']
    assert_replay(record)


# ---------------------------------------------------------------------------
# Conversational wording content (separate_realization-v1.1)
# ---------------------------------------------------------------------------

def _replay_wording(realized):
    from app.ai.Cognitive.system.v0.live_shadow.realization import replay_realization
    return replay_realization(
        realization_status=realized.realization_status.value,
        realization_trace_payload=realized.realization_trace.model_dump(mode='json'),
        realization_trace_digest_value=realized.realization_trace_digest,
        realization_semantic_digest_value=realized.realization_semantic_digest,
        pipeline_trace_payload=realized.pipeline_trace.model_dump(mode='json'),
        compiled_request_payload=realized.compiled_request.model_dump(mode='json'),
        compiled_request_digest_value=realized.compiled_request_digest, language_tag='en',
        fallback_maximum_characters=1200)


def test_v1_wording_replays():
    # Previously every v1 wording replay reported realization_validation_policy_mismatch.
    record, _, _ = run()
    realized = _realized(record, 'Hi there.')
    assert realized.realization_status.value == 'accepted_shadow'
    report = _replay_wording(realized)
    assert report.status.value == 'match', report


def _content_request(operation, artifact_type, text, action_key):
    def one(payload):
        return understanding(event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': operation}, 'artifact_type': {'kind': 'enum', 'value': artifact_type},
            'target_ref': NULL, 'constraints': NULL, 'requested_form': NULL,
            'outcome_certainty': {'kind': 'enum', 'value': 'clear'}, 'embedding': NULL},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.cause_action')]))

    def two(payload):
        blocking = {c['ref'] for c in payload['planning_conditions'] if c['blocking']}
        ref = next(c['candidate_ref'] for c in payload['eligible_candidates']
                   if c['action_key'] == action_key and blocking & set(c['addressed_condition_refs']))
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=[ref], primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text)
    assert record.status.value == 'completed', record.failure
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == [action_key]
    return record


def _inventory(record):
    from app.ai.Cognitive.system.v0.live_shadow.realization import _realization_inputs
    return _realization_inputs(record, None)['inventory']


def _items(record):
    inventory = _inventory(record)
    return tuple(getattr(inventory, 'items', None) or getattr(inventory, 'content_items', None) or inventory)


BRAINSTORM = 'Help me brainstorm a name for a quiet neighborhood book club.'


def test_unproduced_content_is_declined_with_a_human_reason():
    record = _content_request('create', 'list', BRAINSTORM, 'commit_or_coordinate.decline_request')
    reasons = [i for i in _items(record) if getattr(i, 'predicate', None)]
    assert [(r.predicate, r.polarity.value, r.subject.surface_anchor) for r in reasons] == [
        ('assistant.can_provide_now', 'negative', 'that')]
    realized = _realized(record, "Sorry, I can't come up with that right now.")
    status, violations, _, _ = _final(realized)
    assert status == 'accepted_shadow' and not violations, violations
    assert _replay_wording(realized).status.value == 'match'


@pytest.mark.parametrize('text,code', [
    ("Sure, I'll come up with that later.", 'unplanned_commitment'),
    ("Here it is: The Quiet Page.", None),
])
def test_the_human_decline_still_cannot_promise_or_deliver(text, code, conversational_v1):
    record = _content_request('create', 'list', BRAINSTORM, 'commit_or_coordinate.decline_request')
    status, violations, _, _ = _final(_realized(record, text))
    assert status == 'rejected', violations
    if code:
        assert code in violations, violations


@pytest.mark.parametrize('artifact_type,anchor,question', [
    ('other', 'what you are referring to', 'What are you referring to?'),
    ('outline', 'which outline you mean', 'Which outline do you mean?'),
    ('draft_message', 'which message you mean', 'Which message do you mean?'),
])
def test_target_clarification_fits_what_was_requested(artifact_type, anchor, question):
    # Summarizing needs only the target, so the one clarification asks about it.
    record = _content_request('summarize', artifact_type, 'Can you summarize it for me?', 'seek_information.clarify')
    anchors = [a for i in _items(record) if getattr(i, 'reference_kind', None) is not None
               and i.reference_kind.value == 'unresolved_element' for a in i.surface_anchors]
    assert anchor in anchors and 'which plan or draft you mean' not in anchors, anchors
    realized = _realized(record, question)
    status, violations, _, _ = _final(realized)
    assert status == 'accepted_shadow' and not violations, violations
    assert _replay_wording(realized).status.value == 'match'


def test_v1_0_wording_records_keep_their_content_and_replay(monkeypatch):
    from app.ai.Cognitive.system.v0.two_call import v1_contracts
    monkeypatch.setattr(v1_contracts, 'REALIZATION_POLICY_V1', 'separate_realization-v1')
    record = _content_request('create', 'list', BRAINSTORM, 'commit_or_coordinate.decline_request')
    clarify = _content_request('summarize', 'other', 'Can you summarize it for me?', 'seek_information.clarify')
    monkeypatch.undo()
    assert record.pipeline_trace.orchestration.realization_policy == 'separate_realization-v1'
    assert [i.predicate for i in _items(record) if getattr(i, 'predicate', None)] == ['runtime.requested_content_produced']
    assert 'which plan or draft you mean' in [a for i in _items(clarify) if getattr(i, 'reference_kind', None) is not None
                                              and i.reference_kind.value == 'unresolved_element' for a in i.surface_anchors]
    assert_replay(record)
    realized = _realized(record, "I can't help with your request; it wasn't produced.")
    assert _replay_wording(realized).status.value == 'match'


# ---------------------------------------------------------------------------
# Collaborative-content compiler v0.2 (tolerant bookkeeping)
# ---------------------------------------------------------------------------

BOOK_CLUB = 'Help me brainstorm a name for a quiet book club. It should feel warm, not grand.'


def _brainstorm(candidate_for, compiler_version, deliver=True):
    from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
    from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import ScriptedCollaborationModel
    from app.ai.Cognitive.system.v0.collaboration_provider import resolve_provenance

    class Model(ScriptedCollaborationModel):
        provenance = resolve_provenance(model_id='gpt-6-luna', timeout_seconds=25).model_copy(
            update={'compiler_version': compiler_version})

    def one(payload):
        return understanding(event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': 'create'}, 'artifact_type': {'kind': 'enum', 'value': 'list'},
            'target_ref': NULL, 'requested_form': NULL, 'outcome_certainty': {'kind': 'enum', 'value': 'clear'},
            'embedding': NULL, 'constraints': {'kind': 'span_set', 'refs': [span_of(payload, 'warm'),
                                                                            span_of(payload, 'not grand')]}},
            payload['focus']['authorized_span_refs'][:1], [reading('interpretation.goal.cause_action')]))

    def two(payload):
        delivery = [ref for row in payload['goal_readiness'] for ref in row['delivery_candidate_refs']]
        refs = delivery[:1] if deliver and delivery else [next(
            c['candidate_ref'] for c in payload['eligible_candidates']
            if c['action_key'] == 'commit_or_coordinate.decline_request')]
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=refs, primary_index=0,
                    stop_after_selected=True, selection_factors=[])

    def script(payload):
        refs = {s.text: s.ref for s in payload.sources}
        return candidate_for(refs['create list'], refs['warm'], refs['not grand'])
    engine = CollaborativeContentEngine(Model(script))
    return run(V1Double(one=one, two=two), BOOK_CLUB, collaboration=engine)


def _live_slips(frame, warm, not_grand):
    """The two slips seen live: element ids as anchors, and "not grand" cited only by the explanation."""
    add = lambda element, name: dict(op='add_item', element_id=element, section_id='names', text=name,
                                     text_class='generated_proposal', source_refs=[frame, warm], heading=None,
                                     dish=None, constraint=None, integer_value=None, integer_class=None)
    return dict(title='Book club names', inquiries=[], operations=[
        dict(op='add_section', element_id='names', section_id=None, heading='Name ideas', text=None, text_class=None,
             source_refs=[frame], dish=None, constraint=None, integer_value=None, integer_class=None),
        add('name-1', 'The Quiet Page'), add('name-2', 'Hush & Read')],
        content_items=[dict(role='explanation', statement='These aim for a warm, curious feel without sounding grand.',
                            anchors=['name-1', 'name-2'], content_class='reasoned_inference', claim_kind='preference_fit',
                            subject_ids=['name-1', 'name-2'], source_refs=[frame, warm, not_grand], numbers=[])])


def test_compiler_v0_1_keeps_rejecting_the_live_slips():
    record, _, _ = _brainstorm(_live_slips, 'collaborative-content-compiler-v0.1', deliver=False)
    [outcome] = record.pipeline_trace.collaboration.outcomes
    assert (outcome.status, outcome.failure.code.value) == ('failed', 'user_constraint_ignored')
    assert_replay(record)


def test_compiler_v0_2_accepts_the_live_slips_and_replays():
    record, _, _ = _brainstorm(_live_slips, 'collaborative-content-compiler-v0.2')
    assert record.status.value == 'completed', record.failure
    [outcome] = record.pipeline_trace.collaboration.outcomes
    assert outcome.status == 'proposed', outcome.failure
    [item] = outcome.proposal.content_items
    assert item.anchors == ('These aim for a',)  # ids dropped; the statement's opening words remain
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == [
        'collaborate.deliver_artifact']
    assert_replay(record)


@pytest.mark.parametrize('mutate,code', [
    (lambda c: c['content_items'][0].update(claim_kind='verified_fact'), None),
    (lambda c: c['content_items'][0].update(statement='These 12 names aim for a warm feel.'), 'unsupported_number'),
    # Safety advice not tied to a stated safety constraint.
    (lambda c: c['content_items'][0].update(role='safety_guidance', claim_kind='general_practice'),
     'sensitive_content_unauthorized'),
])
def test_compiler_v0_2_keeps_claim_and_number_rules(mutate, code):
    def candidate(frame, warm, not_grand):
        c = _live_slips(frame, warm, not_grand)
        mutate(c)
        return c
    record, _, _ = _brainstorm(candidate, 'collaborative-content-compiler-v0.2', deliver=False)
    [outcome] = record.pipeline_trace.collaboration.outcomes
    assert outcome.status == 'failed'
    if code:
        assert outcome.failure.code.value == code


def _loose_items(frame, warm, not_grand):
    """Live C1: five names added with no section at all."""
    add = lambda element, name: dict(op='add_item', element_id=element, section_id=None, text=name,
                                     text_class='generated_proposal', source_refs=[warm, not_grand], heading=None,
                                     dish=None, constraint=None, integer_value=None, integer_class=None)
    return dict(title='Book club names', inquiries=[],
                operations=[add('name-1', 'The Curious Corner'), add('name-2', 'Pages & Porchlights')],
                content_items=[dict(role='explanation', statement='These names aim for a warm feel without sounding grand.',
                                    anchors=['warm feel'], content_class='reasoned_inference',
                                    claim_kind='preference_fit', subject_ids=['name-1', 'name-2'],
                                    source_refs=[warm, not_grand], numbers=[])])


def test_loose_list_items_need_a_section_only_under_compiler_v0_1():
    strict, _, _ = _brainstorm(_loose_items, 'collaborative-content-compiler-v0.1', deliver=False)
    [outcome] = strict.pipeline_trace.collaboration.outcomes
    assert outcome.failure.code.value == 'patch_rejected'
    record, _, _ = _brainstorm(_loose_items, 'collaborative-content-compiler-v0.2')
    [outcome] = record.pipeline_trace.collaboration.outcomes
    assert outcome.status == 'proposed', outcome.failure
    payload = outcome.proposal.proposed_revision.payload.model_dump(mode='json')
    assert 'The Curious Corner' in json.dumps(payload) and 'Pages & Porchlights' in json.dumps(payload)
    # The original candidate is recorded unchanged; only placement was supplied.
    assert all(op['section_id'] is None for op in outcome.candidate['operations'])
    assert_replay(record)


def test_delivering_some_prepared_content_then_stopping_is_allowed():
    from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
    from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import ScriptedCollaborationModel
    from app.ai.Cognitive.system.v0.collaboration_provider import resolve_provenance
    text = 'Help me list a few possibilities, then list how I could talk to them.'

    class Model(ScriptedCollaborationModel):
        provenance = resolve_provenance(model_id='gpt-6-luna', timeout_seconds=25)

    def script(payload):
        frame = payload.sources[0].ref
        return dict(title='Ideas', inquiries=[], operations=[
            dict(op='add_item', element_id='idea-1', section_id=None, text='One gentle possibility.',
                 text_class='generated_proposal', source_refs=[frame], heading=None, dish=None, constraint=None,
                 integer_value=None, integer_class=None)],
            content_items=[dict(role='explanation', statement='One gentle possibility to consider.',
                                anchors=['gentle possibility'], content_class='uncertain_suggestion',
                                claim_kind='preference_fit', subject_ids=['idea-1'], source_refs=[frame], numbers=[])])

    def one(payload):
        request = lambda phrase, kind: event('collaborative_content_request', {
            'operation': {'kind': 'enum', 'value': 'create'}, 'artifact_type': {'kind': 'enum', 'value': kind},
            'target_ref': NULL, 'constraints': NULL, 'requested_form': NULL,
            'outcome_certainty': {'kind': 'enum', 'value': 'clear'}, 'embedding': NULL},
            [span_of(payload, phrase)], [reading('interpretation.goal.cause_action')])
        return understanding(request('Help me list a few possibilities', 'list'),
                             request('list how I could talk to them', 'outline'))

    def two(payload):
        delivery = [ref for row in payload['goal_readiness'] for ref in row['delivery_candidate_refs']]
        assert len(delivery) == 2, payload['goal_readiness']
        return dict(schema_version='action-policy-proposal-v1', selected_candidate_refs=delivery[:1], primary_index=0,
                    stop_after_selected=True, selection_factors=[])
    record, _, _ = run(V1Double(one=one, two=two), text, collaboration=CollaborativeContentEngine(Model(script)))
    # Previously (live E1): stop_omitted_delivery rejected the whole turn.
    assert record.status.value == 'completed', record.failure
    assert [m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] == [
        'collaborate.deliver_artifact']
    assert_replay(record)


# ---------------------------------------------------------------------------
# conversational-v2 rules, current under conversational-v3 (separate_realization-v1.3): say what Columbina wants to say
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text,warning', [
    ("Hi! I'll remind you tomorrow.", 'unplanned_commitment'),
    ("Hi! I'm feeling so sad today.", 'hidden_affect_asserted'),
    ("Hi! You should quit your job.", 'unplanned_advice'),
    ("Hi! How about you?", 'unplanned_question'),
])
def test_v2_records_other_findings_as_warnings_without_blocking(text, warning):
    record, _, _ = run()
    realized = _realized(record, text)
    status, violations, warnings, policy = _final(realized)
    assert policy == 'conversational-v4'
    assert status == 'accepted_shadow' and not violations, violations
    assert warning in warnings, warnings
    assert _replay_wording(realized).status.value == 'match'


@pytest.mark.parametrize('text,code', [
    ("Hi! I've sent the email for you.", 'false_tool_completion_claim'),
    ("Hi! As you told me last week, you love hiking.", 'unrecorded_memory_claimed'),
])
def test_v2_still_blocks_false_action_and_memory_claims(text, code):
    record, _, _ = run()
    status, violations, _, policy = _final(_realized(record, text))
    assert policy == 'conversational-v4'
    assert status == 'rejected' and code in violations, violations


def test_v2_still_blocks_a_reply_over_the_configured_maximum():
    from realization_v0_support import ScriptedModel
    record, _, _ = run()
    realized = realize_record(record, engine=RealizationEngineV0(ScriptedModel('Hi! ' + 'nice to see you. ' * 20),
                                                                 max_attempts=1), configured_maximum_characters=60)
    status, violations, _, _ = _final(realized)
    assert status == 'rejected' and 'output_too_long' in violations, violations
