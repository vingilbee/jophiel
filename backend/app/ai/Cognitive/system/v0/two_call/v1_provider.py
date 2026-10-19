"""Strict, payload-narrowed JSON Schemas for protocol v1 (OpenAI strict mode shape).

Each predicate is its own branch with exactly its registered arguments; every
reference field is an enum of supplied refs or the focus-span grammar.  No
property accepts an unrestricted string.
"""
from __future__ import annotations

from ..contracts.dialogue_interpretation import DIALOGUE_UPDATE_KINDS, MAX_DIALOGUE_UPDATES
from .v1_contracts import (
    APPRAISAL_FIELDS, INTERPRETED_DIALOGUE_REGISTRIES, WRITTEN_RECALL_REGISTRIES, FOLLOWUP_REGISTRIES, SCOPED_SOURCE_REGISTRIES, POLICY_PROPOSAL_VERSION, SELECTION_FACTOR_KEYS, SPAN_REF_PATTERN,
    UNDERSTANDING_PROPOSAL_VERSION,
)


def _obj(properties: dict) -> dict:
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def _enum(values) -> dict:
    return {'type': 'string', 'enum': list(values)}


def _nullable(schema: dict) -> dict:
    return {'anyOf': [schema, {'type': 'null'}]}


# A closed placeholder for an empty vocabulary; its arrays carry maxItems 0.
_NONE = _enum(['unavailable'])


def _ref_list(values) -> dict:
    values = list(values)
    return {'type': 'array', 'items': _enum(values)} if values else {'type': 'array', 'maxItems': 0, 'items': _NONE}


_SPAN = {'$ref': '#/$defs/SpanRef'}
_ORDINAL = ('low', 'moderate', 'high')
_SIGNED = ('negative', 'neutral', 'positive')
_AGENCY = ('user', 'columbina', 'third_party', 'environment', 'unknown')
_APPRAISAL_SCALES = {'relevance': _ORDINAL, 'congruence': _SIGNED, 'novelty': _ORDINAL, 'certainty': _ORDINAL,
                     'controllability': _ORDINAL, 'norm_compatibility': _SIGNED, 'affiliative_value': _SIGNED,
                     'agency': _AGENCY}


def _value_schema(argument: dict, refs: dict) -> dict:
    branches = []
    for kind in argument['value_kinds']:
        if kind == 'enum':
            branches.append(_obj({'kind': _enum(['enum']), 'value': _enum(argument['enum_values'])}))
        elif kind == 'span':
            branches.append({'$ref': '#/$defs/SpanValue'})
        elif kind == 'span_set':
            branches.append({'$ref': '#/$defs/SpanSetValue'})
        elif kind == 'dialogue_span':
            branches.append(refs.get('citation_schema') or _obj({'kind': _enum(['dialogue_span']),
                                  'message_ref': _enum(refs['citable_messages']), 'span_ref': _SPAN}))
        elif kind == 'dialogue_span_set':
            branches.append(_obj({'kind': _enum(['dialogue_span_set']), 'spans': {
                'type': 'array', 'minItems': 2, 'maxItems': 4,
                'items': (refs.get('citation_schema') if argument['argument_key'] != 'candidate_refs' else None) or _obj({'kind': _enum(['dialogue_span']),
                               'message_ref': _enum(refs['citable_messages']), 'span_ref': _SPAN})}}))
        elif kind in {'entity_ref', 'object_ref'}:
            if refs['entity' if kind == 'entity_ref' else 'object']:
                branches.append({'$ref': '#/$defs/EntityValue' if kind == 'entity_ref' else '#/$defs/ObjectValue'})
        elif kind == 'null':
            branches.append({'$ref': '#/$defs/NullValue'})
        else:  # pragma: no cover - no registered argument offers the remaining kinds yet
            raise ValueError(f'unsupported argument kind in schema: {kind}')
    if not branches:
        raise ValueError('an argument has no representable value kind')
    return branches[0] if len(branches) == 1 else {'anyOf': branches}


def understanding_schema(payload: dict) -> dict:
    dialogue = payload['dialogue_context']
    context = payload['context']
    refs = {
        'entity': [e['ref'] for e in dialogue['entity_refs']],
        'object': [o['ref'] for o in dialogue['open_question_refs']],
        'user_messages': [m['ref'] for m in dialogue['messages'] if m['speaker'] == 'user'],
        # v1.8: her own replies carry indexed tokens and may be cited as well.
        'citable_messages': [m['ref'] for m in dialogue['messages']
                             if m['speaker'] == 'user' or 'indexed_tokens' in m],
    }
    context_refs = ([t['ref'] for t in context['persona_traits'] if t['availability'] == 'available']
                    + [b['ref'] for b in context['interpretation_biases']]
                    + [d['ref'] for d in context['relationship_state']['dimensions']]
                    + [s['ref'] for s in context['dynamic_state_before']['values']]
                    + [m['ref'] for m in dialogue['messages']]
                    + [m['ref'] for m in context['authorized_memory_refs']]
                    + [v['ref'] for v in context['agent_values']])
    evidence_refs = ([m['ref'] for m in dialogue['messages']] + [f['ref'] for f in payload['runtime']['trusted_fact_refs']]
                     + [m['ref'] for m in context['authorized_memory_refs']])
    goals = [g['ref'] for g in context['existing_goals']]
    ontology = payload['ontology']
    appraisal = _obj({'uncertainty': _enum(_ORDINAL), **{
        name: _nullable(_enum((*_APPRAISAL_SCALES[name], 'not_applicable'))) for name in APPRAISAL_FIELDS}})
    reading = _obj({'hypothesis_key': _enum(ontology['hypothesis_keys']), 'uncertainty': _enum(_ORDINAL),
                    'context_refs': _ref_list(context_refs), 'appraisal': {'$ref': '#/$defs/Appraisal'}})
    if context.get('conditioning_guide') in ('conditioning-guide-v2', 'conditioning-guide-v3'):
        # Guide v2+: the reading's likelihood from the words, and the biases that bear on it here.
        reading['properties'].update(
            likelihood={'type': 'integer', 'minimum': 1, 'maximum': 100},
            bias_fit={'type': 'array', 'items': _obj({
                'bias_ref': _enum([b['ref'] for b in context['interpretation_biases']]),
                'direction': _enum(['favours', 'disfavours'])})})
        reading['required'].extend(['likelihood', 'bias_fit'])
    branches = []
    argument_names = set()
    shared: dict[str, str] = {}
    definitions: dict[str, dict] = {}
    scoped = ontology['predicate_registry_version'] in SCOPED_SOURCE_REGISTRIES
    assistant_sources = False
    if scoped:
        from .source_passages import install_source_schema
        assistant_sources = install_source_schema(payload, definitions)
        refs['citation_schema'] = {'$ref': '#/$defs/DialogueCitation'}

    def share(schema: dict) -> dict:
        # Identical value shapes (e.g. every ``embedding``) are defined once.
        if '$ref' in schema:
            return schema
        import json
        key = json.dumps(schema, sort_keys=True)
        if key not in shared:
            shared[key] = f'Value{len(shared)}'
            definitions[shared[key]] = schema
        return {'$ref': f'#/$defs/{shared[key]}'}

    for predicate in ontology['predicates']:
        arguments = {a['argument_key']: share(_value_schema(a, refs)) for a in predicate['arguments']}
        argument_names.update(arguments)
        branches.append(_obj({
            'predicate_key': _enum([predicate['predicate_key']]),
            'polarity': {'$ref': '#/$defs/Polarity'},
            'arguments': _obj(arguments),
            'evidence_span_refs': {'$ref': '#/$defs/EvidenceSpans'},
            'readings': {'$ref': '#/$defs/Readings'},
        }))
        if scoped:
            branches[-1]['properties'].update(
                act_scope=_enum(['current', 'quoted', 'reported', 'imagined']),
                subject_mode=_enum(['actual', 'hypothetical', 'unspecified']))
            branches[-1]['required'].extend(['act_scope', 'subject_mode'])
        if ontology['predicate_registry_version'] in FOLLOWUP_REGISTRIES:
            citation = _obj({'kind': _enum(['dialogue_span']),
                             'message_ref': _enum([m['ref'] for m in dialogue['messages']
                                                   if m['speaker'] in ('user', 'assistant')]),
                             'span_ref': _SPAN})
            followup = _obj({
                'operation': _enum(['continue_opinion', 'explain_position', 'compare_positions']),
                'resolved_question': {'type': 'string', 'minLength': 1, 'maxLength': 600},
                'sources': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': _obj({
                    'role': _enum(['earlier_position', 'recent_position', 'new_information', 'topic']),
                    'citation': citation})}})
            if scoped:
                source_branches = [_obj({'role': _enum(['new_information', 'topic']),
                                         'citation': {'$ref': '#/$defs/DialogueCitation'}})]
                if assistant_sources:
                    source_branches.append(_obj({'role': _enum(['earlier_position', 'recent_position']),
                                                 'citation': {'$ref': '#/$defs/AssistantCitation'}}))
                followup['properties']['sources']['minItems'] = 0
                followup['properties']['sources']['items'] = (source_branches[0] if len(source_branches) == 1
                                                              else {'anyOf': source_branches})
            branches[-1]['properties']['followup'] = (share(_nullable(followup))
                if predicate['predicate_key'] in ('opinion_request', 'elaboration_request') else {'type': 'null'})
            branches[-1]['required'].append('followup')
    evidence_ref = {'anyOf': [_SPAN, _enum(evidence_refs)]}
    impact = _obj({'goal_ref': _enum(goals) if goals else _NONE,
                   'event_index': {'type': 'integer', 'minimum': 0},
                   'reading_index': _nullable({'type': 'integer', 'minimum': 0}),
                   'relevance': _nullable(_enum(_ORDINAL)), 'congruence': _nullable(_enum(_SIGNED)),
                   'uncertainty': _enum(_ORDINAL)})
    unknown = _obj({
        'subject': _obj({'event_index': {'type': 'integer', 'minimum': 0},
                         'reading_index': _nullable({'type': 'integer', 'minimum': 0}),
                         'argument_key': _nullable(_enum(sorted(argument_names)))}),
        'unknown_key': _enum([u['key'] for u in ontology['unknown_keys']]),
        'owner': _enum(['user', 'runtime', 'system', 'unknown']),
        'evidence_refs': {'type': 'array', 'items': evidence_ref},
    })
    conflict = _obj({'left_ref': evidence_ref, 'right_ref': evidence_ref,
                     'conflict_key': _enum([c['key'] for c in ontology['conflict_keys']]), 'status': _enum(['active'])})
    schema = _obj({
        'schema_version': _enum([UNDERSTANDING_PROPOSAL_VERSION]),
        'events': {'type': 'array', 'minItems': 1, 'items': branches[0] if len(branches) == 1 else {'anyOf': branches}},
        'existing_goal_impacts': {'type': 'array', 'items': impact} if goals else {'type': 'array', 'maxItems': 0, 'items': impact},
        'unknowns': {'type': 'array', 'items': unknown},
        'conflicts': {'type': 'array', 'items': conflict},
    })
    if ontology['predicate_registry_version'] in INTERPRETED_DIALOGUE_REGISTRIES:
        dialogue_span = _obj({'kind': _enum(['dialogue_span']), 'message_ref': _enum(refs['user_messages']),
                              'span_ref': _SPAN})
        schema['properties']['dialogue_updates'] = {'type': 'array', 'maxItems': MAX_DIALOGUE_UPDATES, 'items': _obj({
            'kind': _enum(list(DIALOGUE_UPDATE_KINDS)),
            'spans': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': dialogue_span}})}
        schema['required'] = [*schema['required'], 'dialogue_updates']
        if ontology['predicate_registry_version'] in WRITTEN_RECALL_REGISTRIES:
            schema['properties']['recall_answer'] = {'type': ['string', 'null']}
            schema['required'] = [*schema['required'], 'recall_answer']
    definitions.update({'SpanRef': {'type': 'string', 'pattern': SPAN_REF_PATTERN}, 'Reading': reading, 'Appraisal': appraisal,
                   'Polarity': _enum(['positive', 'negative', 'unknown']),
                   'EvidenceSpans': {'type': 'array', 'minItems': 1, 'items': _SPAN},
                   'Readings': {'type': 'array', 'items': {'$ref': '#/$defs/Reading'}},
                   'SpanValue': _obj({'kind': _enum(['span']), 'ref': _SPAN}),
                   'SpanSetValue': _obj({'kind': _enum(['span_set']), 'refs': {'type': 'array', 'minItems': 1, 'items': _SPAN}}),
                   'NullValue': _obj({'kind': _enum(['null'])})})
    if refs['entity']:
        definitions['EntityValue'] = _obj({'kind': _enum(['entity_ref']), 'ref': _enum(refs['entity'])})
    if refs['object']:
        definitions['ObjectValue'] = _obj({'kind': _enum(['object_ref']), 'ref': _enum(refs['object'])})
    schema['$defs'] = definitions
    return schema


def policy_schema(payload: dict) -> dict:
    candidates = [c['candidate_ref'] for c in payload['eligible_candidates']]
    maximum = payload['max_moves']
    from .v1 import _policy_sources
    sources = sorted(_policy_sources(payload))
    available = {t['ref'].split(':', 1)[1] for t in payload['personality'] if t['availability'] == 'available'}
    factors = [key for key in SELECTION_FACTOR_KEYS
               if not (key.startswith('personality.') and key.split('.', 1)[1] not in available)
               and not (key == 'relationship.context' and not payload['relationship']['dimensions'])]
    selected = ({'type': 'array', 'maxItems': maximum, 'items': _enum(candidates)} if candidates
                else {'type': 'array', 'maxItems': 0, 'items': _NONE})
    factor = _obj({'candidate_ref': _enum(candidates) if candidates else _NONE,
                   'factor_keys': {'type': 'array', 'minItems': 1, 'items': _enum(factors)},
                   'source_refs': _ref_list(sources)})
    return _obj({
        'schema_version': _enum([POLICY_PROPOSAL_VERSION]),
        'selected_candidate_refs': selected,
        'primary_index': _nullable({'type': 'integer', 'minimum': 0, 'maximum': max(maximum - 1, 0)}),
        # No bounded iterative-planning contract is offered in v1.
        'stop_after_selected': {'type': 'boolean', 'enum': [True]} if not payload['iterative_planning'] else {'type': 'boolean'},
        'selection_factors': ({'type': 'array', 'maxItems': maximum, 'items': factor} if candidates
                              else {'type': 'array', 'maxItems': 0, 'items': factor}),
    })


def v1_schema(stage: str, payload: dict) -> dict:
    return understanding_schema(payload) if stage == 'call_one' else policy_schema(payload)


__all__ = ['policy_schema', 'understanding_schema', 'v1_schema']
