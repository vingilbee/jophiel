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

Records worded under ``separate_realization-v1`` keep their stored content:
the rewrite is selected only by the policy recorded in the pipeline trace,
so live wording and replay rebuild the identical request.
"""
from __future__ import annotations

from ..collaborative_content_v0.projection import REQUEST_ANCHOR
from ..contracts.realization import DialogueReference, DialogueReferenceKind, GroundedProposition, Polarity
from ..requirement_resolution_v0.manifest import elicitation_anchor

DECLINE_PREDICATE_V1_0 = 'runtime.requested_content_produced'
DECLINE_PREDICATE = 'assistant.can_provide_now'
DECLINE_SUBJECT_ANCHOR = 'that'
TARGET_ANCHOR_V1_0 = elicitation_anchor('target_ref')
OTHER_TARGET_ANCHOR = 'what you are referring to'
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
    out = []
    for item in items:
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
