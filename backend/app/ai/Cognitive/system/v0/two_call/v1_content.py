"""Conversational wording content (realization policy ``separate_realization-v1.1``).

A pure rewrite of the *surface* of already-authorized Realization content for
protocol v1 records; meanings, ids, polarity, attribution and epistemic status
are unchanged, so every honesty check applies exactly as before.

- A failed collaborative deliverable is declined with the reason a person
  gives ("I can't come up with that right now"), not the internal fact that
  the requested content "was not produced".  It is the same negative runtime
  observation about the same request.
- A clarification about a deliverable's missing target asks with an anchor
  that fits what was requested ("which outline you mean", or "what you are
  referring to" for anything else), never always "which plan or draft".

Registry v1.5 records additionally (selected by the trace's registry):

- A dialogue fact the user never stated is a ``not_mentioned_in_conversation``
  gap ("you haven't said which company"), not an "I don't know" gap.
- A referent clarification keeps the generic reference: the elicitation
  anchor "what you are referring to" was worded back as if the user said it.
- An ambiguous recall becomes a choice between the user's own candidates.

Records worded under ``separate_realization-v1`` keep their stored content:
the rewrite is selected only by the policy recorded in the pipeline trace,
so live wording and replay rebuild the identical request.
"""
from __future__ import annotations

from ..collaborative_content_v0.projection import REQUEST_ANCHOR
from ..contracts.realization import DialogueReference, DialogueReferenceKind, GroundedProposition, Polarity, KnowledgeGap, GapKind
from ..contracts.realization import ReferenceForm
from ..requirement_resolution_v0.manifest import elicitation_anchor
from .v1_contracts import CONTINUITY_REGISTRIES, CONVERSATION_REGISTRIES

DECLINE_PREDICATE_V1_0 = 'runtime.requested_content_produced'
DECLINE_PREDICATE = 'assistant.can_provide_now'
DECLINE_SUBJECT_ANCHOR = 'that'
TARGET_ANCHOR_V1_0 = elicitation_anchor('target_ref')
OTHER_TARGET_ANCHOR = 'what you are referring to'
# Elicitation anchors that only restate "what do you mean" (v1.5 words them generically).
_REFERENT_ANCHORS = frozenset({elicitation_anchor('referent_phrase'), elicitation_anchor(None)})
_TARGET_NOUNS = {
    'meal_plan': 'meal plan', 'plan': 'plan', 'draft_message': 'message', 'outline': 'outline',
    'checklist': 'checklist', 'comparison': 'comparison', 'summary': 'summary', 'list': 'list',
}


def target_anchor(artifact_type: object) -> str:
    noun = _TARGET_NOUNS.get(artifact_type) if isinstance(artifact_type, str) else None
    return f'which {noun} you mean' if noun else OTHER_TARGET_ANCHOR


def _rebuilt(item, **updates):
    return type(item).model_validate({**item.model_dump(mode='python'), **updates})


def conversational_content(items: tuple, trace) -> tuple:
    frames = {frame.frame_id: frame for frame in trace.interpretation.semantic_frames}
    registry = trace.orchestration.calls[0].payload['ontology']['predicate_registry_version']
    continuity = registry in CONTINUITY_REGISTRIES
    conversation = registry in CONVERSATION_REGISTRIES
    out = []
    for item in items:
        if continuity and isinstance(item, DialogueReference) and item.reference_kind == DialogueReferenceKind.UNRESOLVED_ELEMENT:
            frame = next((frames[ref] for ref in item.source_refs if ref in frames), None)
            candidates = frame is not None and frame.predicate == 'dialogue_recall_request' and frame.arguments.get('candidate_refs')
            if conversation and candidates:
                from .v1_grounding import choice_clarification
                out.extend(choice_clarification(item.target_ref, frame, trace, content_id=item.content_id))
                continue
            if conversation and item.surface_anchors and set(item.surface_anchors) <= _REFERENT_ANCHORS:
                item = _rebuilt(item, surface_anchors=(), reference_form=ReferenceForm.GENERIC)
            elif candidates:
                from .continuity import resolve_pointer, trace_messages
                sources = trace_messages(trace)
                candidates = [resolve_pointer(ref, sources) for ref in frame.arguments['candidate_refs']]
                item = _rebuilt(item, surface_anchors=tuple(text for _, text in candidates),
                                source_refs=tuple(dict.fromkeys((*item.source_refs, *(ref for ref, _ in candidates)))))
        if isinstance(item, KnowledgeGap) and continuity:
            records = trace.requirement_resolution.content_records if trace.requirement_resolution else ()
            if any(record.query.query_kind == 'captured_dialogue.recall' and record.gap is not None
                   and item.content_id == f'producer-content:{record.gap.gap_id}' for record in records):
                item = _rebuilt(item, gap_kind=GapKind.NOT_MENTIONED if conversation else GapKind.NO_RECORD)
        if (isinstance(item, GroundedProposition) and item.predicate == DECLINE_PREDICATE_V1_0
                and item.polarity == Polarity.NEGATIVE):
            item = _rebuilt(item, predicate=DECLINE_PREDICATE,
                            subject={**item.subject.model_dump(mode='python'), 'surface_anchor': DECLINE_SUBJECT_ANCHOR})
        elif (isinstance(item, DialogueReference) and item.reference_kind == DialogueReferenceKind.REQUEST
              and item.content_id.startswith('collaboration-request-ref:') and item.surface_anchors == (REQUEST_ANCHOR,)):
            item = _rebuilt(item, surface_anchors=(DECLINE_SUBJECT_ANCHOR,))
        elif (isinstance(item, DialogueReference) and item.reference_kind == DialogueReferenceKind.UNRESOLVED_ELEMENT
              and item.surface_anchors == (TARGET_ANCHOR_V1_0,)):
            frame = next((frames[ref] for ref in item.source_refs if ref in frames), None)
            anchor = target_anchor(frame.arguments.get('artifact_type') if frame is not None else None)
            item = _rebuilt(item, surface_anchors=(anchor,))
        out.append(item)
    return tuple(out)


__all__ = ['DECLINE_PREDICATE', 'conversational_content', 'target_anchor']
