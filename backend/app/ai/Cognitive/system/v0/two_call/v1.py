"""Cognitive two-call protocol v1: input compilers and deterministic output compilers.

Call 1 (``UnderstandingInputV1`` -> ``UnderstandingProposalV1``) predicts typed
Interpretation and named Appraisal.  Call 2 (``ActionPolicyInputV1`` ->
``ActionPolicyProposalV1``) selects an ordered plan from code-constructed,
hard-gated, fully bound candidates.  Code owns every durable identifier,
reference, derivation and gate; the model returns supplied references only.

Every compiler here is a pure function of ``(raw proposal, payload, reference
table)`` so a capture can be recompiled and digest-checked on replay.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..appraisal.model_contracts import DIMENSIONS
from ..contracts.interpretation import HYPOTHESIS_KEYS_BY_DIMENSION, HypothesisDimension, HypothesisSupport
from ..interpretation_v0.model_protocol import SemanticArgumentCandidate, SemanticFrameCandidate, SemanticModelOutput
from ..interpretation_v0.ontology import EMBEDDING_VALUES, MODEL_PREDICATE_MANIFEST
from .contracts import CallOneOutput
from .v1_contracts import (
    APPRAISAL_FIELDS, APPRAISAL_SCHEMA_VERSION, ARGUMENT_KIND_REGISTRY_VERSION, CONFLICT_KEYS, CONFLICT_REGISTRY_VERSION,
    EPISTEMIC_POLICY_VERSION, MAX_MOVES_BY_MODE, POLICY_INPUT_VERSION, POLICY_MODE_ROUTER_VERSION, PROTOCOL_V1,
    SELECTION_FACTOR_KEYS, SELECTION_FACTOR_REGISTRY_VERSION, SPAN_ADDRESSING_VERSION, SUPPORT_POLICY_VERSION,
    UNDERSTANDING_INPUT_VERSION, UNKNOWN_KEYS, UNKNOWN_REGISTRY_VERSION, ActionPolicyProposalV1, CompiledPolicy,
    UnderstandingProposalV1, parse_span_ref, ref_kind,
)

# ---------------------------------------------------------------------------
# Focus tokens (span addressing, ``focus-token-span-v1``)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
MAX_FOCUS_TOKENS = 4000


def focus_tokens(text: str) -> tuple[tuple[int, int, str], ...]:
    """Deterministic word tokens of the focus message: ``(start, end, text)``."""
    return tuple((m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text))


def span_bounds(ref: str, tokens) -> tuple[int, int]:
    """Character bounds of a supplied token span in the focus message."""
    first, last = parse_span_ref(ref)
    if last >= len(tokens):
        raise ValueError('span outside focus tokens')
    return tokens[first][0], tokens[last][1]


# ---------------------------------------------------------------------------
# Argument-kind registry (``argument-kind-registry-v1``)
#
# Per model-exposed predicate and argument: the allowed typed value kinds.
# Open-world text is a focus span (or a resolved dialogue entity), never an
# unrestricted string.  ``null`` is allowed only where an unresolved value is
# an explicit, downstream-handled unknown.
# ---------------------------------------------------------------------------

_REF = ('span', 'entity_ref', 'null')          # referent: exact span, resolved entity, or unresolved
_SPAN = ('span',)                               # required open-world value
_OPT_SPAN = ('span', 'null')
_OPT_ENUM = ('enum', 'null')
_ENUM = ('enum',)
_LIST = ('span_set', 'null')

ARGUMENT_KINDS: dict[str, dict[str, tuple[str, ...]]] = {
    'unsafe_request': {'harm_category': _ENUM, 'requested_effect': _SPAN, 'embedding': _OPT_ENUM},
    'social_question': {'topic_ref': _SPAN, 'addressee': _OPT_SPAN},
    'identity_request': {'identity_attribute': _ENUM, 'about': _ENUM, 'embedding': _OPT_ENUM},
    'runtime_fact_request': {'fact_kind': _ENUM, 'embedding': _OPT_ENUM},
    'elaboration_request': {'referent_phrase': _REF, 'elaboration_kind': _ENUM, 'embedding': _OPT_ENUM},
    'collaborative_content_request': {
        'operation': _OPT_ENUM, 'artifact_type': _OPT_ENUM, 'target_ref': _REF, 'constraints': _LIST,
        'requested_form': _OPT_ENUM, 'outcome_certainty': _OPT_ENUM, 'embedding': _OPT_ENUM},
    'action_request': {'requested_effect': _SPAN, 'subject_ref': _REF, 'parameters': _LIST, 'embedding': _OPT_ENUM},
    'correction': {'corrected_attribute': _SPAN, 'corrected_value': _SPAN, 'prior_value': _OPT_SPAN,
                   'embedding': _OPT_ENUM},
    'statement': {'proposition': _SPAN, 'subject': ('span', 'entity_ref', 'null'), 'embedding': _OPT_ENUM},
    'constraint_statement': {'constraint': _SPAN, 'constraint_kind': _ENUM, 'applies_to': ('span', 'entity_ref', 'null'),
                             'embedding': _OPT_ENUM},
    'express_gratitude': {'addressee': _OPT_SPAN},
    'inquiry_answer': {'inquiry_ref': ('object_ref',), 'answer_kind': _ENUM, 'answer_option': _OPT_ENUM,
                       'answer_text': _OPT_SPAN},
    # The acknowledged message is not a typed ref in this protocol version.
    'acknowledge_message': {'message_id': ('null',)},
    'social_opening': {'addressee': _OPT_SPAN},
    'social_closing': {'future_contact': _OPT_SPAN},
    'information_request': {'topic_ref': _REF, 'question_form': _ENUM, 'embedding': _OPT_ENUM},
    # v1.1: the speaker's own experience; grounds experience/affect/concern objects.
    'experience_report': {'experience': _SPAN, 'feeling': _OPT_SPAN, 'affect': _OPT_ENUM, 'concern': _OPT_SPAN,
                          'experiencer': _ENUM, 'embedding': _OPT_ENUM},
    # v1.2: wanting to talk rather than receive produced content.
    'conversation_request': {'topic': _REF, 'mode': _ENUM, 'embedding': _OPT_ENUM},
    # An unregistered meaning has no free-text label in v1.
    'unknown': {'surface_action': _OPT_SPAN, 'object_reference': _OPT_SPAN, 'predicate': ('null',)},
}

_MANIFEST = {item.predicate: item for item in MODEL_PREDICATE_MANIFEST}

# v1 descriptions for span/ref-valued arguments.  The v0 manifest asks for
# paraphrases, which v1 forbids; enum arguments keep their reviewed text.
_REFERENT = ' Null when it is only a pronoun or deictic word with no resolved referent.'
ARGUMENT_DESCRIPTIONS_V1: dict[tuple[str, str], str] = {
    ('unsafe_request', 'requested_effect'): 'Span of what is requested.',
    ('social_question', 'topic_ref'): "Span naming what is asked about, e.g. 'your day'.",
    ('social_question', 'addressee'): 'Optional span naming who is addressed.',
    ('social_opening', 'addressee'): 'Optional span naming who the greeting addresses.',
    ('express_gratitude', 'addressee'): 'Optional span naming who is thanked.',
    ('social_closing', 'future_contact'): "Optional span of stated future contact, e.g. 'tomorrow'.",
    ('elaboration_request', 'referent_phrase'): 'Span or resolved entity_ref of what should be elaborated.' + _REFERENT,
    ('collaborative_content_request', 'target_ref'): (
        'Existing shared content the operation applies to: a span or resolved entity_ref. Null for create.' + _REFERENT),
    ('collaborative_content_request', 'constraints'): 'One span per requirement the speaker states for this content.',
    ('action_request', 'requested_effect'): "Span of the requested effect, e.g. 'create a reminder'.",
    ('action_request', 'subject_ref'): 'Span or resolved entity_ref of what the action applies to.' + _REFERENT,
    ('action_request', 'parameters'): 'One span per stated detail (time, quantity, constraint).',
    ('correction', 'corrected_attribute'): 'Span naming what is being corrected.',
    ('correction', 'corrected_value'): 'Span of the new value.',
    ('correction', 'prior_value'): 'Optional span of the previous value when the focus message states it.',
    ('statement', 'proposition'): 'Span of what is stated.',
    ('statement', 'subject'): 'Optional span or resolved entity_ref the statement is about.',
    ('constraint_statement', 'constraint'): 'Span of the stated constraint.',
    ('constraint_statement', 'applies_to'): 'Optional span or resolved entity_ref the constraint applies to.',
    ('inquiry_answer', 'inquiry_ref'): 'The supplied open question being answered.',
    ('inquiry_answer', 'answer_text'): 'For answer_kind free_text: span of the answer.',
    ('information_request', 'topic_ref'): 'Span or resolved entity_ref naming what is asked about.' + _REFERENT,
    ('unknown', 'surface_action'): 'Optional span of the main verb phrase.',
    ('unknown', 'object_reference'): 'Optional span of the object phrase.',
    ('unknown', 'predicate'): 'Always null in this protocol.',
    ('acknowledge_message', 'message_id'): 'Always null in this protocol.',
    ('experience_report', 'experience'): 'Span of what the speaker is going through, e.g. "been feeling scattered lately".',
    ('experience_report', 'feeling'): "Optional span of the exact feeling words, e.g. 'scattered'.",
    ('experience_report', 'concern'): 'Optional span of what worries or burdens the speaker, as stated.',
    ('conversation_request', 'topic'): 'Span or resolved entity_ref of what they want to talk about.' + _REFERENT,
}


def _validate_registry() -> None:
    for (predicate, argument), _ in ARGUMENT_DESCRIPTIONS_V1.items():
        if argument not in ARGUMENT_KINDS.get(predicate, {}):
            raise RuntimeError(f'v1 argument description names an unregistered argument: {predicate}.{argument}')
    for predicate, arguments in ARGUMENT_KINDS.items():
        for name, kinds in arguments.items():
            if set(kinds) - {'enum', 'null'} and (predicate, name) not in ARGUMENT_DESCRIPTIONS_V1:
                raise RuntimeError(f'span/ref argument requires a v1 description: {predicate}.{name}')
        definition = _MANIFEST.get(predicate)
        if definition is None:
            raise RuntimeError(f'argument-kind registry names an unexposed predicate: {predicate}')
        if set(arguments) != set(definition.allowed_arguments):
            raise RuntimeError(f'argument-kind registry differs from the predicate manifest: {predicate}')
    if set(ARGUMENT_KINDS) != set(_MANIFEST):
        raise RuntimeError('every model-exposed predicate requires an argument-kind entry')


_validate_registry()

READING_HYPOTHESIS_KEYS = (HYPOTHESIS_KEYS_BY_DIMENSION[HypothesisDimension.SPEAKER_GOAL]
                           + HYPOTHESIS_KEYS_BY_DIMENSION[HypothesisDimension.PRAGMATIC_MEANING])
CONTEXT_REF_KINDS = frozenset({'persona', 'bias', 'relationship', 'state', 'message', 'memory', 'value'})
EVIDENCE_REF_KINDS = frozenset({'span', 'message', 'runtime', 'memory'})
RUNTIME_FACT_POINTERS = ('available_capabilities', 'attachments', 'task_contract', 'authorization',
                         'safety_decision', 'last_tool_event')


def argument_enum_values(predicate: str, argument: str, payload_inquiries=()) -> tuple[str, ...]:
    if predicate == 'inquiry_answer' and argument == 'answer_option':
        return tuple(dict.fromkeys(option for inquiry in payload_inquiries for option in inquiry['answer_options']))
    return tuple(_MANIFEST[predicate].argument_values.get(argument, ()))


# ---------------------------------------------------------------------------
# Call 1 input: UnderstandingInputV1
# ---------------------------------------------------------------------------

def _json_default(value):
    from enum import Enum
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    raise TypeError(f'not JSON payload data: {type(value).__name__}')


def plain_json(value):
    """Payloads are plain JSON so digests and replay comparisons are exact."""
    return json.loads(json.dumps(value, default=_json_default, ensure_ascii=False))


def _canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def build_understanding_input(owner, semantic) -> dict:
    """Bounded, versioned Call 1 input.  Canonical refs live only in the table."""
    from ..perception_v0.evidence import resolve_json_pointer
    request = owner.request
    interpretation_request = owner.interpretation_request
    table = owner.references
    focus = next(m for m in request.messages if m.message_id == request.focus_message_id)
    tokens = focus_tokens(focus.content)
    if not tokens or len(tokens) > MAX_FOCUS_TOKENS:
        raise ValueError('focus message has no addressable tokens' if not tokens else 'focus message too long')
    owner.focus_tokens = tokens

    messages = []
    for index, message in enumerate(request.messages):
        handle = table.bind(f'message:{index}', message.message_id, 'message')
        messages.append(dict(ref=handle, speaker=message.role.value, text=message.content,
                             authority='current_user_task' if message.message_id == request.focus_message_id else 'context_only'))
    focus_ref = table.reverse[focus.message_id]

    # Authorized spans: the code-owned evidence regions, as token ranges.
    authorized = []
    for index, region in enumerate(semantic.evidence_regions):
        if region.message_ref != focus.message_id:
            continue
        inside = [i for i, (start, end, _) in enumerate(tokens) if region.start <= start and end <= region.end]
        table.bind(f'region:{index}', region.evidence_ref, 'evidence_region', start=region.start, end=region.end)
        if inside:
            authorized.append(f'span:{inside[0]}' if inside[0] == inside[-1] else f'span:{inside[0]}-{inside[-1]}')
    if not authorized:
        raise ValueError('no authorized focus span')

    entities, topics = [], []
    if request.dialogue_context is not None:
        context = request.dialogue_context.interpretation_context
        for item in context.items:
            if not item.surface_anchor:
                continue
            handle = table.bind(f'entity:{len(entities)}', item.item_ref, 'entity', anchor=item.surface_anchor)
            entities.append(dict(ref=handle, kind=item.item_kind, speaker=item.speaker,
                                 epistemic_status=item.epistemic_status, anchor=item.surface_anchor))
            if item.item_kind == 'topic':
                topics.append(handle)
    open_questions = []
    for index, inquiry in enumerate(semantic.open_inquiries):
        handle = table.bind(f'object:{index}', inquiry.inquiry_ref, 'open_inquiry',
                            answer_options=list(inquiry.answer_options))
        open_questions.append(dict(ref=handle, purpose=inquiry.purpose, asked_about=inquiry.asked_about,
                                   answer_options=list(inquiry.answer_options)))
    omissions = ['upstream_capture_window_truncated'] if request.context_truncated else []

    personality = request.action_personality
    traits = []
    for key in ('curiosity', 'warmth', 'playfulness', 'directness'):
        value = getattr(personality, key, None)
        if value is not None:
            table.bind(f'persona:{key}', personality.profile_id, 'persona')
        traits.append(dict(ref=f'persona:{key}', value=value, availability='unavailable' if value is None else 'available'))
    biases = request.interpretation_biases
    bias_views = []
    for key in ('benign_prior', 'trust_prior', 'threat_sensitivity', 'confirmation_tendency',
                'self_attribution_tendency', 'ambiguity_tolerance', 'social_affiliation_prior'):
        table.bind(f'bias:{key}', biases.profile_id, 'bias')
        bias_views.append(dict(ref=f'bias:{key}', value=getattr(biases, key)))
    relationship = []
    if request.relationship is not None:
        snapshot = request.relationship.to_appraisal_relationship()
        table.bind('relationship:status', snapshot.relationship_snapshot_id, 'relationship')
        table.bind('relationship:records', snapshot.relationship_snapshot_id, 'relationship')
        relationship = [dict(ref='relationship:status', value=snapshot.status),
                        dict(ref='relationship:records', value=list(snapshot.records))]
    state = []
    for index, item in enumerate(v for v in request.previous_state.values if v.active):
        table.bind(f'state:{index}', request.previous_state.state_snapshot_id, 'state')
        plain = item.model_dump(mode='json')
        state.append(dict(ref=f'state:{index}', **{k: plain[k] for k in ('state_key', 'scope_type', 'status', 'band', 'score')}))
    memories = []
    for index, memory in enumerate(request.memories):
        if not memory.authorized:
            raise ValueError('unauthorized memory')
        table.bind(f'memory:{index}', memory.memory_id, 'memory')
        memories.append(dict(ref=f'memory:{index}', text=memory.content, epistemic_status=memory.status,
                             source=memory.provenance))
    values = []
    for index, value in enumerate(request.values):
        table.bind(f'value:{index}', value.value_id, 'value')
        values.append(dict(ref=f'value:{index}', **{k: v for k, v in value.model_dump(mode='json').items() if k != 'value_id'}))
    goals = []
    if request.previous_goal_snapshot is not None:
        for record in request.previous_goal_snapshot.records:
            if record.status.value != 'active':
                continue
            handle = table.bind(f'goal:{len(goals)}', record.goal_id, 'goal')
            goals.append(dict(ref=handle, definition=record.definition_ref.definition_key,
                              priority=record.priority_band.value))

    runtime_json = interpretation_request.runtime.model_dump(mode='json')
    facts = []
    for name in RUNTIME_FACT_POINTERS:
        # The same source ref the interpretation evidence registry records.
        table.bind(f'runtime:{name}', f'{interpretation_request.runtime.snapshot_id}:/{name}', 'runtime',
                   pointer=f'/{name}')
        facts.append(dict(ref=f'runtime:{name}', value=resolve_json_pointer(runtime_json, f'/{name}')))
    capability_refs = ['runtime:available_capabilities']

    predicates = []
    for spec in semantic.predicates:
        kinds = ARGUMENT_KINDS[spec.predicate]
        arguments = []
        for argument in spec.arguments:
            allowed = tuple(kind for kind in kinds[argument.name]
                            if not (kind == 'entity_ref' and not entities) and not (kind == 'object_ref' and not open_questions))
            arguments.append(dict(argument_key=argument.name, value_kinds=list(allowed),
                                  enum_values=list(argument_enum_values(spec.predicate, argument.name, open_questions)),
                                  description=ARGUMENT_DESCRIPTIONS_V1.get((spec.predicate, argument.name), argument.description)))
        predicates.append(dict(predicate_key=spec.predicate, description=spec.description, arguments=arguments))

    return plain_json(dict(
        schema_version=UNDERSTANDING_INPUT_VERSION, protocol=PROTOCOL_V1,
        focus=dict(message_ref=focus_ref, text=focus.content,
                   indexed_tokens=' '.join(f'[{i}]{t}' for i, (_, _, t) in enumerate(tokens)),
                   token_count=len(tokens), authorized_span_refs=authorized),
        dialogue_context=dict(messages=messages, message_refs=[m['ref'] for m in messages], entity_refs=entities,
                              open_question_refs=open_questions, active_topic_refs=topics, omission_flags=omissions),
        perception=dict(cue_activations=[dict(cue_key=c.cue_key, state=c.state.value)
                                         for c in interpretation_request.perception.cue_activations]),
        context=dict(persona_traits=traits, interpretation_biases=bias_views,
                     relationship_state=dict(availability='available' if relationship else 'unavailable',
                                             dimensions=relationship),
                     dynamic_state_before=dict(availability='available', values=state),
                     authorized_memory_refs=memories, agent_values=values, existing_goals=goals),
        runtime=dict(capability_refs=capability_refs, trusted_fact_refs=facts,
                     safety_state=runtime_json.get('safety_decision'), authorization_state=runtime_json.get('authorization')),
        ontology=dict(predicate_registry_version=getattr(owner, 'understanding_registry', ARGUMENT_KIND_REGISTRY_VERSION),
                      predicates=predicates,
                      span_addressing=SPAN_ADDRESSING_VERSION,
                      hypothesis_registry_version='interpretation-hypotheses-v0',
                      hypothesis_keys=list(READING_HYPOTHESIS_KEYS),
                      unknown_registry_version=UNKNOWN_REGISTRY_VERSION,
                      unknown_keys=[dict(key=k, description=v) for k, v in UNKNOWN_KEYS.items()],
                      conflict_registry_version=CONFLICT_REGISTRY_VERSION,
                      conflict_keys=[dict(key=k, description=v) for k, v in CONFLICT_KEYS.items()],
                      appraisal_schema_version=APPRAISAL_SCHEMA_VERSION),
    ))


# ---------------------------------------------------------------------------
# Call 1 compiler
# ---------------------------------------------------------------------------

def _supplied(payload) -> dict:
    """Every reference the payload supplied, by kind."""
    context = payload['context']
    refs = {
        'message': {m['ref'] for m in payload['dialogue_context']['messages']},
        'entity': {e['ref'] for e in payload['dialogue_context']['entity_refs']},
        'object': {o['ref'] for o in payload['dialogue_context']['open_question_refs']},
        'persona': {t['ref'] for t in context['persona_traits'] if t['availability'] == 'available'},
        'bias': {b['ref'] for b in context['interpretation_biases']},
        'relationship': {d['ref'] for d in context['relationship_state']['dimensions']},
        'state': {s['ref'] for s in context['dynamic_state_before']['values']},
        'memory': {m['ref'] for m in context['authorized_memory_refs']},
        'value': {v['ref'] for v in context['agent_values']},
        'goal': {g['ref'] for g in context['existing_goals']},
        'runtime': {f['ref'] for f in payload['runtime']['trusted_fact_refs']},
    }
    return refs


def _check_span(ref, tokens):
    span_bounds(ref, tokens)
    return ref


def _check_ref(ref, kinds, supplied, tokens):
    kind = ref_kind(ref)
    if kind not in kinds:
        raise ValueError('reference kind not permitted here')
    if kind == 'span':
        return _check_span(ref, tokens)
    if ref not in supplied.get(kind, set()):
        raise ValueError('foreign local reference')
    return ref


def _normalize_appraisal(appraisal) -> tuple:
    """Values in DIMENSIONS order.  High uncertainty means unassessed (versioned)."""
    values = tuple(getattr(appraisal, name) for name in APPRAISAL_FIELDS)
    if appraisal.uncertainty == 'high':
        values = tuple(v if v in {None, 'not_applicable'} else None for v in values)
    return values


def _compiled_value(value, *, tokens, text, entries):
    kind = value.kind
    if kind == 'null':
        return None
    if kind == 'enum':
        return value.value
    if kind == 'span':
        start, end = span_bounds(value.ref, tokens)
        return text[start:end]
    if kind == 'span_set':
        out = []
        for ref in value.refs:
            start, end = span_bounds(ref, tokens)
            out.append(text[start:end])
        if len(set(out)) != len(out):
            raise ValueError('duplicate span-set members')
        return tuple(out)
    if kind == 'entity_ref':
        return entries[value.ref]['anchor']
    if kind == 'object_ref':
        return entries[value.ref]['canonical_ref']
    if kind in {'integer', 'decimal', 'boolean'}:
        return value.value
    raise ValueError('argument value kind not compilable')


def compiled_arguments(event, *, tokens, text, entries) -> list[SemanticArgumentCandidate]:
    """Canonical arguments, sorted by name.

    ``null`` on a required or referent argument is an unresolved value (an
    explicit downstream unknown); on any other optional argument it means
    "not stated" and the argument is omitted -- an absent optional detail is
    not ambiguity (``argument-kind-registry-v1``).
    """
    definition = _MANIFEST[event.predicate_key]
    keep_null = set(definition.required_arguments) | set(definition.referent_arguments)
    out = []
    for name, value in sorted(event.arguments.items()):
        if value.kind == 'null' and name not in keep_null:
            continue
        out.append(SemanticArgumentCandidate(name=name, value=_compiled_value(value, tokens=tokens, text=text, entries=entries)))
    return out


def _region_evidence(refs, tokens, entries) -> tuple[str, ...]:
    """Map token spans to the code-owned regions that contain them (containment gate)."""
    regions = [(entry['start'], entry['end'], entry['canonical_ref']) for handle, entry in entries.items()
               if entry['kind'] == 'evidence_region']
    out = []
    for ref in refs:
        first, last = parse_span_ref(ref)
        if last >= len(tokens):
            raise ValueError('span outside focus tokens')
        for index in range(first, last + 1):
            start, end, _ = tokens[index]
            covering = [region for region in regions if region[0] <= start and end <= region[1]]
            if not covering:
                raise ValueError('span outside supplied evidence')
            # The most specific (smallest) supplied region is the token's evidence.
            out.append(min(covering, key=lambda region: (region[1] - region[0], region[0]))[2])
    return tuple(dict.fromkeys(out))


def validate_understanding(raw, payload, entries):
    """Hard gates for Call 1.  Returns the validated proposal and focus tokens."""
    proposal = UnderstandingProposalV1.model_validate(raw)
    text = payload['focus']['text']
    tokens = focus_tokens(text)
    if len(tokens) != payload['focus']['token_count']:
        raise ValueError('focus tokens differ from the supplied input')
    supplied = _supplied(payload)
    predicates = {p['predicate_key']: p for p in payload['ontology']['predicates']}
    hypotheses = set(payload['ontology']['hypothesis_keys'])
    unknown_keys = {item['key'] for item in payload['ontology']['unknown_keys']}
    conflict_keys = {item['key'] for item in payload['ontology']['conflict_keys']}
    for event in proposal.events:
        spec = predicates.get(event.predicate_key)
        if spec is None:
            raise ValueError('foreign predicate')
        arguments = {a['argument_key']: a for a in spec['arguments']}
        if set(event.arguments) != set(arguments):
            raise ValueError('event arguments differ from the predicate registry')
        for name, value in event.arguments.items():
            allowed = arguments[name]
            if value.kind not in allowed['value_kinds']:
                raise ValueError('argument value kind not permitted')
            if value.kind == 'enum' and value.value not in allowed['enum_values']:
                raise ValueError('argument value outside vocabulary')
            if value.kind == 'span':
                _check_span(value.ref, tokens)
            elif value.kind == 'span_set':
                for ref in value.refs:
                    _check_span(ref, tokens)
                if len(set(value.refs)) != len(value.refs):
                    raise ValueError('duplicate span-set members')
            elif value.kind in {'entity_ref', 'object_ref', 'runtime_ref', 'memory_ref'}:
                _check_ref(value.ref, {value.kind.removesuffix('_ref')}, supplied, tokens)
        if event.predicate_key == 'inquiry_answer':
            inquiry = event.arguments['inquiry_ref']
            option = event.arguments['answer_option']
            if option.kind == 'enum' and option.value not in entries[inquiry.ref]['answer_options']:
                raise ValueError('answer option not offered by its inquiry')
        _region_evidence(event.evidence_span_refs, tokens, entries)
        for reading in event.readings:
            if reading.hypothesis_key not in hypotheses:
                raise ValueError('foreign reading key')
            for ref in reading.context_refs:
                _check_ref(ref, CONTEXT_REF_KINDS, supplied, tokens)
    keys = [(impact.goal_ref, impact.event_index, impact.reading_index) for impact in proposal.existing_goal_impacts]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate existing goal impacts')
    for impact in proposal.existing_goal_impacts:
        if impact.goal_ref not in supplied['goal']:
            raise ValueError('foreign goal impact')
        if impact.event_index >= len(proposal.events):
            raise ValueError('goal impact event outside proposal')
        if impact.reading_index is not None and impact.reading_index >= len(proposal.events[impact.event_index].readings):
            raise ValueError('goal impact reading outside event')
    for unknown in proposal.unknowns:
        if unknown.unknown_key not in unknown_keys:
            raise ValueError('foreign unknown key')
        subject = unknown.subject
        if subject.event_index >= len(proposal.events):
            raise ValueError('unknown subject outside proposal')
        event = proposal.events[subject.event_index]
        if subject.reading_index is not None and subject.reading_index >= len(event.readings):
            raise ValueError('unknown subject reading outside event')
        if subject.argument_key is not None and subject.argument_key not in event.arguments:
            raise ValueError('unknown subject argument not registered for its predicate')
        for ref in unknown.evidence_refs:
            _check_ref(ref, EVIDENCE_REF_KINDS, supplied, tokens)
    signatures = set()
    for conflict in proposal.conflicts:
        if conflict.conflict_key not in conflict_keys:
            raise ValueError('foreign conflict key')
        for ref in (conflict.left_ref, conflict.right_ref):
            _check_ref(ref, EVIDENCE_REF_KINDS, supplied, tokens)
        signature = (conflict.conflict_key, *sorted((conflict.left_ref, conflict.right_ref)))
        if signature in signatures:
            raise ValueError('duplicate conflict')
        signatures.add(signature)
    return proposal, tokens


def compile_understanding(raw, payload, entries) -> CallOneOutput:
    """Canonical semantic candidates for the existing interpretation compiler."""
    proposal, tokens = validate_understanding(raw, payload, entries)
    text = payload['focus']['text']
    frames = []
    for event in proposal.events:
        arguments = compiled_arguments(event, tokens=tokens, text=text, entries=entries)
        frames.append(SemanticFrameCandidate(predicate=event.predicate_key, polarity=event.polarity, arguments=arguments,
                                             evidence_refs=_region_evidence(event.evidence_span_refs, tokens, entries)))
    return CallOneOutput(interpretation=SemanticModelOutput(frames=frames), appraisal=())


def derived_support(uncertainty: str) -> HypothesisSupport:
    """``reading-support-from-uncertainty-v1``: evidence coverage is guaranteed by
    the event's required spans, so support follows the reading's uncertainty."""
    return {'low': HypothesisSupport.STRONG, 'moderate': HypothesisSupport.MODERATE,
            'high': HypothesisSupport.WEAK}[uncertainty]


_ASSERTIVE = frozenset({'statement', 'constraint_statement', 'correction', 'inquiry_answer'})


def derived_epistemic_status(event) -> str:
    """``epistemic-status-derivation-v1`` (views and training projection only)."""
    embedding = event.arguments.get('embedding')
    embedded = embedding is not None and embedding.kind == 'enum' and embedding.value != 'direct'
    if event.predicate_key == 'unknown' or event.polarity == 'unknown' or embedded:
        return 'uncertain'
    return 'user_claim' if event.predicate_key in _ASSERTIVE else 'expressed'


def accepted_mind(raw, payload, entries):
    """The accepted understanding in the shape the canonical adapters consume.

    Handles are code-owned (``e0``, ``q0_1``); context handles are the supplied
    typed refs, resolvable through the same reference table.  There is no
    model prose: a reading's ``candidate_meaning`` is its registered key.
    """
    from .compact_contracts import EventAppraisal, GoalImpact, MindProposal, Observation, Reading
    proposal, tokens = validate_understanding(raw, payload, entries)
    text = payload['focus']['text']
    regions = {entry['canonical_ref']: handle for handle, entry in entries.items() if entry['kind'] == 'evidence_region'}
    observations, readings, appraisals = [], [], []
    for i, event in enumerate(proposal.events):
        evidence = _region_evidence(event.evidence_span_refs, tokens, entries)
        observations.append(Observation(
            handle=f'e{i}', predicate=event.predicate_key, polarity=event.polarity,
            arguments=compiled_arguments(event, tokens=tokens, text=text, entries=entries),
            evidence_handles=tuple(regions[ref] for ref in evidence), source_span=None,
            epistemic_status=derived_epistemic_status(event)))
        for j, reading in enumerate(event.readings):
            handle = f'q{i}_{j}'
            readings.append(Reading(handle=handle, event_handles=(f'e{i}',), hypothesis_key=reading.hypothesis_key,
                                    candidate_meaning=reading.hypothesis_key, support=derived_support(reading.uncertainty),
                                    uncertainty=reading.uncertainty, evidence_handles=(), context_handles=reading.context_refs))
            appraisals.append(EventAppraisal(event_handles=(f'e{i}',), reading_handle=handle,
                                             values=_normalize_appraisal(reading.appraisal),
                                             uncertainty=reading.appraisal.uncertainty, evidence_handles=(),
                                             context_handles=reading.context_refs, exceptions=()))
    # One impact per (goal, event): readings of one event merge (agreeing
    # values kept, disagreement unknown, the highest uncertainty wins).
    grouped = {}
    for impact in proposal.existing_goal_impacts:
        grouped.setdefault((impact.goal_ref, impact.event_index), []).append(impact)
    impacts = []
    for (goal, index), items in grouped.items():
        uncertainty = max((i.uncertainty for i in items), key=lambda u: {'low': 0, 'moderate': 1, 'high': 2}[u])
        assessed = uncertainty != 'high'
        agree = lambda field: getattr(items[0], field) if len({getattr(i, field) for i in items}) == 1 else None
        impacts.append(GoalImpact(goal_handle=goal, event_handles=(f'e{index}',),
                                  relevance=agree('relevance') if assessed else None,
                                  congruence=agree('congruence') if assessed else None, uncertainty=uncertainty))
    mind = MindProposal(observations=observations, readings=readings, event_appraisals=appraisals,
                        existing_goal_impacts=impacts, unknowns=(), conflicts=())
    return mind, proposal


def typed_evidence(refs, *, entries, tokens, evidence, turn, messages) -> tuple[str, ...]:
    """Typed refs -> canonical interpretation evidence IDs (never invented)."""
    from ..contracts.interpretation import InterpretationEvidenceKind as K
    from ..perception_v0.evidence import stable_id
    ids = {item.evidence_id for item in evidence}
    out = []
    for ref in refs:
        kind = ref_kind(ref)
        if kind == 'span':
            out.extend(_region_evidence((ref,), tokens, entries))
            continue
        entry = entries[ref]
        if kind == 'message':
            # The registry's whole-message evidence record, by its exact identity.
            message = entry['canonical_ref']
            matches = [stable_id('interpretation-evidence', turn, 'message', message, 0, len(messages[message]))]
        elif kind == 'runtime':
            matches = [e.evidence_id for e in evidence if e.source_kind == K.RUNTIME_FACT and e.json_pointer == entry['pointer']]
        elif kind == 'memory':
            matches = [e.evidence_id for e in evidence if e.source_kind == K.MEMORY_RECORD and e.memory_ref == entry['canonical_ref']]
        else:
            raise ValueError('reference kind is not evidence')
        if len(matches) != 1 or matches[0] not in ids:
            raise ValueError('typed evidence reference does not bind uniquely')
        out.append(matches[0])
    if not out:
        raise ValueError('typed evidence reference does not bind uniquely')
    return tuple(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Call 2 input: ActionPolicyInputV1
# ---------------------------------------------------------------------------

def route_policy_mode(interpretation, conditions, requests) -> str:
    """``policy-mode-router-v1``: deterministic depth, never a model claim."""
    contradictions = [c for c in interpretation.contradictions if c.status == 'active']
    blockers = [c for c in conditions if c.blocker_refs]
    unsafe = any(f.predicate == 'unsafe_request' for f in interpretation.semantic_frames)
    if contradictions or blockers or unsafe:
        return 'deliberative'
    alternatives = any(h.disposition.value == 'retained_alternative' for h in interpretation.hypotheses)
    # An absent optional detail is not ambiguity: count only unresolved required
    # arguments, unregistered meaning, and model-registered unknowns.
    gaps = any(set(frame.unresolved_arguments) & set(_MANIFEST[frame.predicate].required_arguments)
               for frame in interpretation.semantic_frames if frame.predicate in _MANIFEST)
    gaps = gaps or any(item.key in UNKNOWN_KEYS or item.key == 'meaning' for item in interpretation.unknowns)
    if len(interpretation.semantic_frames) > 1 or gaps or alternatives or requests:
        return 'contextual'
    return 'direct'


def _band(feature) -> str:
    from decimal import Decimal
    if feature is None or feature.status.value != 'known':
        return 'low'  # missing/inapplicable features contribute a neutral zero
    value = Decimal(feature.value)
    return 'low' if value < Decimal('0.34') else ('moderate' if value < Decimal('0.67') else 'high')


_SCOPE_KINDS = frozenset({'frame', 'hypothesis', 'unknown', 'conflict', 'goal'})


def policy_state_view(snapshot, table) -> dict:
    """Post-update state exposed to Call 2; recomputed by the trace validator.

    A scope resolves only to an understanding/goal handle (bound before this
    view), so later candidate/object bindings can never change it.
    """
    scopes = {}
    for handle, entry in table.entries.items():
        if entry['kind'] in _SCOPE_KINDS:
            scopes.setdefault(entry['canonical_ref'], handle)
    values = []
    for index, entry in enumerate(v for v in snapshot.values if v.active):
        plain = entry.model_dump(mode='json')
        values.append(dict(ref=f'state:{index}', state_key=plain['state_key'], scope_type=plain['scope_type'],
                           scope_ref=scopes.get(entry.scope_ref), status=plain['status'], band=plain['band'],
                           score=plain['score']))
    return dict(availability='available', values=values)


def build_policy_input(owner, pipeline, turn, appraisal, update, interpretation, goals, requirement,
                       collaboration, action_request) -> dict:
    """Code-constructed, hard-gated, fully bound candidates plus typed policy context."""
    from ..action_selection_v0.construction import PlanView
    from ..action_selection_v0.features import FeatureCalculator
    from ..action_selection_v0.planning_conditions import addresses, derive_planning_conditions
    from ..action_selection_v0.reference_index import ActionReferenceIndex
    from ..action_selection_v0.transition import empty_state
    from ..contracts.actions import FeatureKey
    from .compact import ReferenceTable
    table = ReferenceTable()
    owner.policy_references = table
    engine = pipeline.cognitive_turn_runner.action_engine.engine
    canonical = json.dumps(action_request.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
    context_ref = 'action-context-' + hashlib.sha256(canonical.encode()).hexdigest()[:20]
    plan_ref = 'action-plan-' + hashlib.sha256(context_ref.encode()).hexdigest()[:20]
    index = ActionReferenceIndex(action_request)
    state = empty_state(plan_ref)
    candidates, _ = engine.constructor_factory(engine.registry).construct(index, PlanView((), ()), context_ref)
    gate = engine.gate_factory(engine.registry)
    gates = {c.candidate_id: gate.evaluate(c, index) for c in candidates}
    eligible = [c for c in candidates if gates[c.candidate_id].eligible_in_shadow]
    conditions = derive_planning_conditions(action_request)
    calculator = engine.feature_factory(engine.registry, index)

    # Accepted understanding.
    frames = []
    for i, frame in enumerate(interpretation.semantic_frames):
        table.bind(f'frame:{i}', frame.frame_id, 'frame')
        frames.append(dict(ref=f'frame:{i}', predicate=frame.predicate, polarity=frame.polarity,
                           arguments={k: v for k, v in sorted(frame.arguments.items())},
                           unresolved_arguments=list(frame.unresolved_arguments)))
    hypotheses = []
    for i, item in enumerate(interpretation.hypotheses):
        table.bind(f'hypothesis:{i}', item.hypothesis_id, 'hypothesis')
        hypotheses.append(dict(ref=f'hypothesis:{i}', key=item.hypothesis_key, dimension=item.dimension.value,
                               disposition=item.disposition.value, support=item.support.value,
                               frame_refs=[table.reverse[f] for f in item.semantic_frame_refs if f in table.reverse]))
    unknowns = []
    for i, item in enumerate(interpretation.unknowns):
        table.bind(f'unknown:{i}', item.unknown_id, 'unknown')
        unknowns.append(dict(ref=f'unknown:{i}', key=item.key, owner=item.owner))
    conflicts = []
    for i, item in enumerate(interpretation.contradictions):
        table.bind(f'conflict:{i}', item.contradiction_id, 'conflict')
        conflicts.append(dict(ref=f'conflict:{i}', key=item.description, status=item.status))

    focal = goals.focus_decision.primary_goal_ref if goals.focus_decision else None
    active = []
    for i, goal in enumerate(turn.active_goals):
        table.bind(f'goal:{i}', goal.goal_id, 'goal')
    for goal in turn.active_goals:
        active.append(dict(ref=table.reverse[goal.goal_id], definition=goal.definition_ref.definition_key,
                           priority=goal.priority_band.value if hasattr(goal.priority_band, 'value') else goal.priority_band,
                           status=goal.status.value if hasattr(goal.status, 'value') else goal.status,
                           focal=goal.goal_id == focal))

    def handle_for(ref):
        if ref in table.reverse:
            return table.reverse[ref]
        return table.bind(f'object:{sum(1 for e in table.entries.values() if e["kind"] == "object")}', ref, 'object')

    state_after = policy_state_view(update.snapshot, table)
    for item in state_after['values']:
        table.bind(item['ref'], update.snapshot.state_snapshot_id, 'state', state_key=item['state_key'])

    personality = []
    profile = turn.action_personality
    for key in ('curiosity', 'warmth', 'playfulness', 'directness'):
        value = getattr(profile, key, None)
        if value is not None:
            table.bind(f'persona:{key}', profile.profile_id, 'persona')
        personality.append(dict(ref=f'persona:{key}', value=value, availability='unavailable' if value is None else 'available'))
    relationship = []
    if owner.request.relationship is not None:
        snapshot = owner.request.relationship.to_appraisal_relationship()
        table.bind('relationship:status', snapshot.relationship_snapshot_id, 'relationship')
        relationship = [dict(ref='relationship:status', value=snapshot.status)]

    planning = []
    for i, condition in enumerate(conditions):
        table.bind(f'condition:{i}', condition.condition_id, 'condition')
        planning.append(dict(ref=f'condition:{i}', kind=condition.kind.value,
                             blocked_goal_ref=handle_for(condition.blocked_goal_ref),
                             blocking=bool(condition.blocker_refs),
                             collaboration_failure=condition.collaboration_failure))

    # Readiness, per goal, from the deterministic requirement snapshot.
    readiness = {}
    for resolution in requirement.handoff.snapshot.resolutions:
        row = readiness.setdefault(resolution.goal_ref, dict(goal_ref=handle_for(resolution.goal_ref), ready=True,
                                                               requirements=[]))
        row['requirements'].append(dict(kind=resolution.kind.value, status=resolution.status.value,
                                        blocking=resolution.blocking, reason=resolution.reason_code.value))
        if resolution.blocking and resolution.status.value != 'satisfied':
            row['ready'] = False

    runtime = turn.action_runtime
    views, identities = [], {}
    for candidate in eligible:
        analysis = calculator.analyze(candidate, state)
        features = {f.feature_key: f for f in calculator.compute(analysis, state, (), (), {})}
        handle = f'candidate:{len(views)}'
        identity = candidate.lexical_identity
        if identity in identities:
            raise ValueError('duplicate eligible candidate identity')
        identities[identity] = handle
        table.bind(handle, candidate.candidate_id, 'candidate',
                   identity=[identity[0], [list(t) for t in identity[1]], identity[2]])
        definition = engine.registry.resolve(candidate.definition_ref)
        views.append(dict(
            candidate_ref=handle, action_key=candidate.definition_ref.action_key,
            goal_refs=sorted({handle_for(link.goal_ref) for link in analysis.goal_links}),
            target_refs=[dict(role=t.role, target_type=t.target_type.value, ref=handle_for(t.ref)) for t in candidate.target_refs],
            typed_parameters={k: v for k, v in sorted(candidate.parameters.items())},
            planned_effect_keys=[definition.effect.effect_key],
            addressed_condition_refs=[f'condition:{i}' for i, c in enumerate(conditions)
                                      if addresses(candidate, c, engine.registry, index)],
            addressed_condition_keys=sorted({c.kind.value for c in conditions
                                             if addresses(candidate, c, engine.registry, index)}),
            cost_band=_band(features.get(FeatureKey.RESPONSE_COST)),
            risk_band=_band(features.get(FeatureKey.SOFT_RISK_COST))))
    # Grounded objects candidates target, so targets are meaningful without IDs.
    objects = []
    by_id = {o.object_id: o for o in action_request.grounded_objects}
    for handle, entry in list(table.entries.items()):
        if entry['kind'] != 'object' or entry['canonical_ref'] not in by_id:
            continue
        item = by_id[entry['canonical_ref']]
        objects.append(dict(ref=handle, object_type=item.object_type.value,
                            about_refs=[table.reverse[r] for r in item.about_refs if r in table.reverse],
                            asserted_by=getattr(item, 'asserted_by', None),
                            epistemic_status=getattr(item, 'epistemic_status', None)))
    deliveries = {}
    from ..collaborative_content_v0.projection import artifact_object_id
    required = {artifact_object_id(o.proposal): o.goal_ref for o in collaboration.outcomes if o.status == 'proposed'}
    for view, candidate in zip(views, eligible):
        if candidate.definition_ref.action_key == 'collaborate.deliver_artifact' and any(
                t.ref in required for t in candidate.target_refs):
            deliveries.setdefault(handle_for(next(required[t.ref] for t in candidate.target_refs if t.ref in required)),
                                  []).append(view['candidate_ref'])
    goal_readiness = []
    for row in readiness.values():
        row['delivery_candidate_refs'] = deliveries.get(row['goal_ref'], [])
        goal_readiness.append(row)
    for goal_handle, refs in deliveries.items():
        if not any(r['goal_ref'] == goal_handle for r in goal_readiness):
            goal_readiness.append(dict(goal_ref=goal_handle, ready=True, requirements=[], delivery_candidate_refs=refs))

    mode = route_policy_mode(interpretation, conditions, collaboration.outcomes)
    safety = runtime.safety_basis
    return plain_json(dict(
        schema_version=POLICY_INPUT_VERSION, protocol=PROTOCOL_V1,
        accepted_understanding=dict(frame_refs=frames, hypothesis_refs=hypotheses, unknown_refs=unknowns,
                                    conflict_refs=conflicts),
        state_after=state_after, active_goals=active, goal_readiness=goal_readiness,
        personality=personality,
        relationship=dict(availability='available' if relationship else 'unavailable', dimensions=relationship),
        runtime_constraints=dict(
            response_obligation=runtime.response_obligation,
            # Content-free safety view: canonical decision/snapshot refs stay code-owned.
            safety={k: v for k, v in safety.model_dump(mode='json').items()
                    if not k.endswith(('_ref', '_refs', '_id', '_ids'))} if hasattr(safety, 'model_dump') else None,
            capabilities=list(runtime.available_capabilities), authorization=runtime.authorization,
            consent=runtime.consent),
        planning_conditions=planning, grounded_objects=objects, eligible_candidates=views,
        policy_mode=mode, policy_mode_router=POLICY_MODE_ROUTER_VERSION, max_moves=MAX_MOVES_BY_MODE[mode],
        iterative_planning=False,
        **({'policy_guidance': 'policy-guidance-v1.2'}
           if getattr(owner, 'understanding_registry', None) == 'argument-kind-registry-v1.2' else {}),
        selection_factor_registry=dict(version=SELECTION_FACTOR_REGISTRY_VERSION,
                                       keys=[dict(key=k, description=v) for k, v in SELECTION_FACTOR_KEYS.items()]),
    ))


def _policy_sources(payload) -> set[str]:
    refs = set()
    understanding = payload['accepted_understanding']
    for section in ('frame_refs', 'hypothesis_refs', 'unknown_refs', 'conflict_refs'):
        refs.update(item['ref'] for item in understanding[section])
    refs.update(item['ref'] for item in payload['state_after']['values'])
    refs.update(item['ref'] for item in payload['active_goals'])
    refs.update(item['ref'] for item in payload['personality'] if item['availability'] == 'available')
    refs.update(item['ref'] for item in payload['relationship']['dimensions'])
    refs.update(item['ref'] for item in payload['planning_conditions'])
    return refs


def compile_policy(raw, payload, entries) -> CompiledPolicy:
    """Hard gates for Call 2; candidate refs -> code-owned candidate identities."""
    proposal = ActionPolicyProposalV1.model_validate(raw)
    supplied = {item['candidate_ref'] for item in payload['eligible_candidates']}
    if not set(proposal.selected_candidate_refs) <= supplied:
        raise ValueError('foreign candidate')
    if len(proposal.selected_candidate_refs) > payload['max_moves']:
        raise ValueError('plan exceeds maximum moves')
    if not proposal.stop_after_selected and not payload['iterative_planning']:
        raise ValueError('iterative planning is not offered')
    if not proposal.selected_candidate_refs and payload['runtime_constraints']['response_obligation'] == 'required':
        raise ValueError('empty plan for a required response')
    sources = _policy_sources(payload)
    available_traits = {item['ref'].split(':', 1)[1] for item in payload['personality'] if item['availability'] == 'available'}
    factors = []
    for item in proposal.selection_factors:
        if not set(item.factor_keys) <= set(SELECTION_FACTOR_KEYS):
            raise ValueError('foreign selection factor')
        if not set(item.source_refs) <= sources:
            raise ValueError('foreign selection factor source')
        for key in item.factor_keys:
            if key.startswith('personality.') and key.split('.', 1)[1] not in available_traits:
                raise ValueError('personality factor without available personality')
            if key == 'relationship.context' and not payload['relationship']['dimensions']:
                raise ValueError('relationship factor without available relationship')
        factors.append(dict(candidate_id=entries[item.candidate_ref]['canonical_ref'], factor_keys=list(item.factor_keys),
                            source_refs=[entries[ref]['canonical_ref'] for ref in item.source_refs]))
    identities = []
    for ref in proposal.selected_candidate_refs:
        action, targets, parameters = entries[ref]['identity']
        identities.append((action, tuple(tuple(t) for t in targets), parameters))
    return CompiledPolicy(policy_mode=payload['policy_mode'],
                          selected_candidate_ids=tuple(entries[ref]['canonical_ref'] for ref in proposal.selected_candidate_refs),
                          selected_identities=tuple(identities), primary_index=proposal.primary_index,
                          stop_after_selected=True, selection_factors=tuple(factors))


def move_identity(move) -> tuple:
    """The lexical identity of a plan move, comparable to a candidate's."""
    from ..contracts.actions import _canonical_json
    return (move.definition_ref.action_key, tuple(t.sort_key for t in move.target_refs),
            json.dumps(_canonical_json(move.parameters), sort_keys=True, separators=(',', ':')))


__all__ = [
    'ARGUMENT_KINDS', 'accepted_mind', 'build_policy_input', 'build_understanding_input', 'compile_policy',
    'compile_understanding', 'derived_epistemic_status', 'derived_support', 'focus_tokens', 'move_identity',
    'policy_state_view', 'route_policy_mode', 'typed_evidence', 'validate_understanding',
]
