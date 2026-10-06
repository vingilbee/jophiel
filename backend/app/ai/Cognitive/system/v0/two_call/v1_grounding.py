"""Understanding grounding (``understanding-grounding-v1.1``).

A deterministic projection of the accepted interpretation into the typed
Action objects the empathy and reflection Actions target.  Before this, no
stage produced ``experience``, ``claimed_affect`` or ``concern`` objects, so
already-enabled Actions (reflect, validate experience, playful reframe,
congratulate, ...) could never be constructed.

The objects carry the user's own account: ``asserted_by="user"``, never a
verified fact, and no wording.  Their wordable form is a user-attributed
reference anchored to the exact words the user wrote.  Both projections are
pure functions of the stored trace, so replay recomputes them exactly.
"""
from __future__ import annotations

import hashlib

from ..contracts.actions import ActionGroundedObject, ActionTargetDescriptor, ActionTargetType as T
from ..contracts.realization import (
    ContentAttribution, ContentEpistemicStatus, ContentSourceKind, DialogueReference, DialogueReferenceKind,
    ReferenceForm, SensitivityClass,
)
from ..interpretation_v0.ontology import AFFECT_VALENCE
from .v1_contracts import GROUNDING_PROJECTION_VERSION, GROUNDING_PROJECTION_VERSION_V1_2

UNDERSTANDING_CONTENT_PRODUCER = 'realization-understanding-reference-projection-v1.1'
_GROUNDED_TYPES = frozenset({T.EXPERIENCE, T.CLAIMED_AFFECT, T.CONCERN, T.UNKNOWN})
GROUNDING_REGISTRIES = ('argument-kind-registry-v1.1', 'argument-kind-registry-v1.2')


def _id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256('|'.join((GROUNDING_PROJECTION_VERSION, kind, *parts)).encode()).hexdigest()[:24]
    return f'understanding-{kind}:{digest}'


def _eligible(frame) -> bool:
    arguments = frame.arguments
    return (frame.predicate == 'experience_report' and frame.polarity == 'positive'
            and arguments.get('experiencer') == 'speaker' and arguments.get('embedding') in (None, 'direct')
            and isinstance(arguments.get('experience'), str))


def project_understanding_objects(interpretation, *, turn_id: str) -> tuple[ActionGroundedObject, ...]:
    """The speaker's own reported experience, feeling and concern, in frame order.

    Negated, quoted, reported, hypothetical and other people's experiences
    ground nothing: they are not the speaker's present experience.
    """
    out: list[ActionGroundedObject] = []
    for frame in interpretation.semantic_frames:
        if not _eligible(frame):
            continue
        arguments = frame.arguments
        affect = arguments.get('affect')
        valence = AFFECT_VALENCE.get(affect, 'unknown') if affect else 'unknown'
        # The speaker's own account: user attribution (``asserted_by``) carries
        # that it is their report; it is not an uncertain proposition to hedge.
        common = dict(cognitive_turn_id=turn_id, source_refs=(frame.frame_id,), focus_contribution=True,
                      asserted_by='user', expression='explicit')
        experience = _id('experience', frame.frame_id)
        out.append(ActionGroundedObject(object_id=experience, object_type=T.EXPERIENCE, valence=valence, **common))
        if arguments.get('feeling') or (affect and affect != 'other'):
            out.append(ActionGroundedObject(object_id=_id('affect', frame.frame_id), object_type=T.CLAIMED_AFFECT,
                                            about_refs=(experience,), valence=valence, **common))
        if isinstance(arguments.get('concern'), str):
            out.append(ActionGroundedObject(object_id=_id('concern', frame.frame_id), object_type=T.CONCERN,
                                            about_refs=(experience,), valence='negative', **common))
    return tuple(out)


def _conversation_eligible(frame) -> bool:
    arguments = frame.arguments
    return (frame.predicate == 'conversation_request' and frame.polarity == 'positive'
            and arguments.get('embedding') in (None, 'direct') and isinstance(arguments.get('topic'), str))


def project_understanding(interpretation, *, turn_id: str, registry: str):
    """Objects and descriptors a versioned v1 input grounds (empty before v1.1).

    v1.2: a conversation request grounds what the speaker wants from the
    conversation as a user-owned, unresolved goal unknown about the stated
    topic, so exploring it ("what matters most to you about it?") is a
    candidate instead of a forced artifact clarification.
    """
    if registry not in GROUNDING_REGISTRIES:
        return (), ()
    objects = list(project_understanding_objects(interpretation, turn_id=turn_id))
    descriptors = []
    if registry == 'argument-kind-registry-v1.2':
        for frame in interpretation.semantic_frames:
            if not _conversation_eligible(frame):
                continue
            ref = 'understanding-goal:' + hashlib.sha256('|'.join((GROUNDING_PROJECTION_VERSION_V1_2, 'goal',
                                                                    frame.frame_id)).encode()).hexdigest()[:24]
            objects.append(ActionGroundedObject(object_id=ref, object_type=T.UNKNOWN, cognitive_turn_id=turn_id,
                                                source_refs=(frame.frame_id,), focus_contribution=True, owner='user',
                                                unresolved=True))
            descriptors.append(ActionTargetDescriptor(ref=ref, target_type=T.UNKNOWN, owner='user', unknown_kind='goal'))
    return tuple(objects), tuple(descriptors)


def understanding_content(trace) -> tuple[DialogueReference, ...]:
    """Anchored, user-attributed references for grounded objects the plan targets.

    The anchors are the user's exact words from the compiled frame (never
    generated text), so Realization can echo but never invent the experience.
    """
    objects = {o.object_id: o for o in trace.cognitive_turn.input_snapshot.action_grounded_objects
               if o.object_type in _GROUNDED_TYPES and o.object_id.startswith('understanding-')}
    if not objects:
        return ()
    frames = {f.frame_id: f for f in trace.interpretation.semantic_frames}
    targeted = {t.ref for move in trace.action_selection.plan.moves for t in move.target_refs if t.ref in objects}
    items = []
    for ref in sorted(targeted):
        item = objects[ref]
        frame = frames[item.source_refs[0]]
        arguments = frame.arguments
        status = ContentEpistemicStatus.ATTRIBUTED
        if item.object_type == T.UNKNOWN:
            # What the speaker wants from talking about the topic: still open.
            anchors, kind, status = (arguments.get('topic'),), DialogueReferenceKind.GOAL, ContentEpistemicStatus.UNRESOLVED
        elif item.object_type == T.CONCERN:
            anchors, kind = (arguments.get('concern'),), DialogueReferenceKind.CONCERN
        elif item.object_type == T.CLAIMED_AFFECT:
            anchors, kind = (arguments.get('feeling'), arguments.get('experience')), DialogueReferenceKind.EXPERIENCE
        else:
            anchors, kind = (arguments.get('experience'), arguments.get('feeling')), DialogueReferenceKind.EXPERIENCE
        anchors = tuple(dict.fromkeys(a.strip() for a in anchors if isinstance(a, str) and a.strip()))
        items.append(DialogueReference(
            content_id=_id('reference', ref), cognitive_turn_id=trace.cognitive_turn_id,
            source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
            represents_refs=(ref,), authorized_for_realization=True, sensitivity_class=SensitivityClass.PERSONAL,
            epistemic_status=status, attribution=ContentAttribution.USER,
            reference_kind=kind, target_ref=ref, reference_form=ReferenceForm.ANCHORED, surface_anchors=anchors))
    return tuple(items)


__all__ = ['GROUNDING_REGISTRIES', 'UNDERSTANDING_CONTENT_PRODUCER', 'project_understanding',
           'project_understanding_objects', 'understanding_content']
