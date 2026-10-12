"""Extractive, turn-scoped dialogue grounding for argument registry v1.4.

The model chooses source spans, never writes remembered facts. Code checks
speaker, bounds, and capture membership. This is a shadow conversation bridge,
not a new long-term memory store or a general-knowledge provider. A quoted
user claim remains attributed, not independently verified.
"""
from __future__ import annotations

import hashlib

from ..contracts.realization import ArgumentValueType, ContentAttribution, ContentSourceKind
from ..contracts.requirement_resolution import RequirementReason as R, RequirementResolutionStatus as S
from ..producers_v0.contracts import AuthorityClass, ProducedFact, ProducerReason, ProducerResult, ProducerStatus
from ..producers_v0.manifest import QUERY_MANIFEST_VERSION
from ..requirement_resolution_v0.engine import RequirementResolutionEngine
from .v1_contracts import CONVERSATION_REGISTRIES, OPEN_WORLD_REGISTRIES, WRITTEN_RECALL_REGISTRIES

VERSION = 'captured-user-dialogue-v1'
MAX_EXCERPT_CHARACTERS = 600
# Registry v1.8: the cited span is the answer to the current question, worked out
# from the user's own words, not a statement to quote back ("you said Tofu").
RECALL_ANSWER_PREDICATE = 'dialogue.recall_answer'
MAX_QUESTION_ANCHOR_CHARACTERS = 200


def _key(*parts):
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]


def dialogue_span(value, entries):
    from .v1 import focus_tokens, span_bounds
    entry = entries.get(value.message_ref)
    if entry is None or entry.get('kind') != 'message' or not (entry.get('speaker') == 'user' or entry.get('citable')):
        raise ValueError('dialogue citation must name a supplied user message (or, v1.8, her own)')
    text = entry['text']
    start, end = span_bounds(value.span_ref, focus_tokens(text))
    if end - start > MAX_EXCERPT_CHARACTERS:
        raise ValueError('dialogue citation exceeds excerpt bound')
    return start, end, text[start:end]


def dialogue_pointer(value, entries):
    start, end, _ = dialogue_span(value, entries)
    return f"{entries[value.message_ref]['canonical_ref']}#dialogue-span:{start}:{end}"


def resolve_pointer(pointer, messages):
    """Resolve only against this capture's authorized messages.

    Which speakers a pointer may name is decided when it is compiled
    (``dialogue_span``): user messages, and from registry v1.8 her own replies.
    """
    if not isinstance(pointer, str) or '#dialogue-span:' not in pointer:
        raise ValueError('invalid dialogue pointer')
    message_ref, bounds = pointer.rsplit('#dialogue-span:', 1)
    parts = bounds.split(':')
    if len(parts) != 2 or not all(p.isdecimal() for p in parts):
        raise ValueError('invalid dialogue pointer bounds')
    start, end = map(int, parts)
    message = messages.get(message_ref)
    if message is None or message['speaker'] not in ('user', 'assistant'):
        raise ValueError('dialogue source unavailable')
    text = message['text']
    if not (0 <= start < end <= len(text)) or end - start > MAX_EXCERPT_CHARACTERS:
        raise ValueError('dialogue pointer outside source')
    return message_ref, text[start:end]


def trace_messages(trace):
    call = trace.orchestration.calls[0]
    return {call.reference_table[m['ref']]['canonical_ref']: m
            for m in call.payload['dialogue_context']['messages']}


class ContinuityRequirementEngine(RequirementResolutionEngine):
    """Registries v1.4+: accepted uncertainty wins; explicit recall is sourced."""

    def __init__(self, owner, previous):
        super().__init__(producer_factory=previous.producer_factory,
                         allow_review_classes=previous.allow_review_classes,
                         preserve_accepted_uncertainty=True)
        self.owner = owner

    def _argument(self, request, goal, frame, declaration):
        if (frame.predicate == 'dialogue_recall_request'
                and frame.arguments.get('knowledge_status') == 'ambiguous_reference'):
            return self._record(request, goal, frame, declaration, S.MISSING_USER_INPUT, R.REFERENT_AMBIGUOUS,
                                argument=declaration.argument, subject=(frame.frame_id,),
                                evidence=frame.evidence_refs), (), None
        if (self.owner.understanding_registry in CONVERSATION_REGISTRIES
                and frame.predicate == 'dialogue_recall_request'
                and frame.arguments.get('knowledge_status') == 'known'
                and declaration.argument == 'referent_phrase' and frame.arguments.get('answer_ref')):
            # v1.5: the cited answer resolves what the question refers to. A bare
            # "that" in "which cat did that?" is nulled by grounding, and the
            # deterministic cue must not then block a question already answered.
            return self._record(request, goal, frame, declaration, S.SATISFIED, R.REFERENT_STATED,
                                argument=declaration.argument, subject=(frame.frame_id,),
                                values=(f'{frame.frame_id}#answer_ref',), evidence=frame.evidence_refs), (), None
        return super()._argument(request, goal, frame, declaration)

    def _content_result(self, request, registry, frame, query):
        if frame.predicate != 'dialogue_recall_request':
            result = super()._content_result(request, registry, frame, query)
            return self._open_world(result, frame, query)
        result_id = f'captured-dialogue-result:{_key(query.query_id)}'
        common = dict(result_id=result_id, query_ref=query.query_id,
                      authority_class=AuthorityClass.DELIVERED_DIALOGUE_CLAIM,
                      producer_id=VERSION, producer_version=VERSION, manifest_version=QUERY_MANIFEST_VERSION)
        if frame.arguments.get('knowledge_status') != 'known':
            return ProducerResult(**common, status=ProducerStatus.UNAVAILABLE,
                                  reason=ProducerReason.NO_MATCHING_RECORD)
        messages = {m.message_id: dict(speaker=m.role.value, text=m.content) for m in self.owner.request.messages}
        source, text = resolve_pointer(frame.arguments.get('answer_ref'), messages)
        hers = messages[source]['speaker'] == 'assistant'
        # The request and messages were ownership/exposure checked by the live
        # capture compiler. No retrieval, other conversation, or model knowledge
        # enters this path. The fact is what the user said, not that it is true.
        predicate, subject = 'dialogue.user_statement', 'you'
        if self.owner.understanding_registry in WRITTEN_RECALL_REGISTRIES:
            # The question is the subject, so the wording states the answer to it.
            question = next(m.content for m in self.owner.request.messages
                            if m.message_id == self.owner.request.focus_message_id).strip()
            predicate = RECALL_ANSWER_PREDICATE
            subject = question if len(question) <= MAX_QUESTION_ANCHOR_CHARACTERS else query.subject_anchor
        fact = ProducedFact(
            fact_id=f'captured-dialogue-fact:{_key(query.query_id, source, text)}', query_ref=query.query_id,
            authority_class=AuthorityClass.DELIVERED_DIALOGUE_CLAIM, producer_id=VERSION, producer_version=VERSION,
            predicate=predicate, subject_anchor=subject, value=text,
            value_type=ArgumentValueType.STRING, value_anchor=text, owner='application' if hers else 'user', owner_ref=None if hers else query.user_ref,
            source_kind=(ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE if hers
                         else ContentSourceKind.DELIVERED_USER_UTTERANCE), source_ref=source,
            revision=f'captured-source:{_key(source, messages[source]["text"])}', attribution=ContentAttribution.COLUMBINA if hers else ContentAttribution.USER,
        )
        return ProducerResult(**common, status=ProducerStatus.KNOWN, facts=(fact,))

    def _open_world(self, result, frame, query):
        """Registry v1.6: when no closed-world producer answered, ask the recorded contribution stage.

        Recall stays closed-world (what the user said is never model knowledge).
        A contribution that declines (about the user, current events, or
        unanswerable) keeps the explicit knowledge gap.
        """
        from .contribution import KNOWLEDGE_QUERY_KINDS, OPINION_QUERY_KIND
        if self.owner.understanding_registry not in OPEN_WORLD_REGISTRIES or result.status == ProducerStatus.KNOWN:
            return result
        if query.query_kind == OPINION_QUERY_KIND:
            return self.owner.contribute(kind='opinion', frame=frame, query=query)
        if query.query_kind in KNOWLEDGE_QUERY_KINDS:
            return self.owner.contribute(kind='knowledge', frame=frame, query=query)
        return result
