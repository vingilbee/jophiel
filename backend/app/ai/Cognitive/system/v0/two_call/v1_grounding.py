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
    ReferenceForm, SensitivityClass, GroundedProposition, PropositionTerm, PropositionArgument, ArgumentValueType, Certainty,
)
from ..interpretation_v0.ontology import AFFECT_VALENCE
from .v1_contracts import (
    CONTINUITY_REGISTRIES, CONVERSATION_REGISTRIES, EXPLORATION_REGISTRIES, GROUNDING_PROJECTION_VERSION,
    GROUNDING_PROJECTION_VERSION_V1_2,
)

UNDERSTANDING_CONTENT_PRODUCER = 'realization-understanding-reference-projection-v1.1'
_GROUNDED_TYPES = frozenset({T.EXPERIENCE, T.CLAIMED_AFFECT, T.CONCERN, T.UNKNOWN})
GROUNDING_REGISTRIES = ('argument-kind-registry-v1.1', 'argument-kind-registry-v1.2', *EXPLORATION_REGISTRIES)


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


def _statement_eligible(frame) -> bool:
    return (frame.predicate == 'statement' and frame.polarity == 'positive'
            and frame.arguments.get('embedding') in (None, 'direct')
            and isinstance(frame.arguments.get('proposition'), str) and frame.arguments['proposition'].strip() != '')


def _statement_object(interpretation, *, turn_id: str):
    """v1.5: a turn that only shares something invites an optional follow-up.

    Only a turn made entirely of the speaker's own direct statements qualifies:
    a question, request, correction or reported feeling already has its own
    response path. The object is inferred (``expression='inferred'``), so the
    reflect/validate Actions, which need an explicitly expressed experience,
    stay unavailable; it carries no feeling and no unresolved requirement.
    """
    frames = interpretation.semantic_frames
    if not frames or not all(_statement_eligible(frame) for frame in frames):
        return None
    sources = tuple(frame.frame_id for frame in frames)
    return ActionGroundedObject(object_id=_id('statement', *sources), object_type=T.EXPERIENCE, valence='unknown',
                                cognitive_turn_id=turn_id, source_refs=sources, focus_contribution=True,
                                asserted_by='user', expression='inferred')


def project_understanding(interpretation, *, turn_id: str, registry: str):
    """Objects and descriptors a versioned v1 input grounds (empty before v1.1).

    v1.2: a conversation request grounds what the speaker wants from the
    conversation as a user-owned, unresolved goal unknown about the stated
    topic, so exploring it ("what matters most to you about it?") is a
    candidate instead of a forced artifact clarification.
    v1.3: explicit speaker experiences also carry optional exploration
    descriptors, without unresolved flags or additional unknown objects.
    v1.5: a turn of plain statements grounds one inferred experience with the
    same optional exploration descriptor (see ``_statement_object``).
    """
    if registry not in GROUNDING_REGISTRIES:
        return (), ()
    objects = list(project_understanding_objects(interpretation, turn_id=turn_id))
    descriptors = []
    if registry in CONVERSATION_REGISTRIES:
        statement = _statement_object(interpretation, turn_id=turn_id)
        if statement is not None:
            objects.append(statement)
    if registry in CONTINUITY_REGISTRIES:
        for frame in interpretation.semantic_frames:
            if (frame.predicate == 'contextual_perspective_request' and frame.polarity == 'positive'
                    and frame.arguments.get('embedding') in (None, 'direct')):
                objects.append(ActionGroundedObject(
                    object_id=_id('perspective', frame.frame_id), object_type=T.EXPERIENCE,
                    cognitive_turn_id=turn_id, source_refs=(frame.frame_id,), focus_contribution=True,
                    asserted_by='user', expression='explicit', epistemic_status='tentative',
                ))
    if registry in EXPLORATION_REGISTRIES:
        for obj in objects:
            if obj.object_type == T.EXPERIENCE and not obj.object_id.startswith('understanding-perspective:'):
                descriptors.append(ActionTargetDescriptor(ref=obj.object_id, target_type=T.EXPERIENCE,
                                                         owner='user', optional_exploration=True))
    if registry in ('argument-kind-registry-v1.2', *EXPLORATION_REGISTRIES):
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


def understanding_content(trace) -> tuple:
    """Anchored, user-attributed references for grounded objects the plan targets.

    The anchors are the user's exact words from the compiled frame (never
    generated text), so Realization can echo but never invent the experience.
    """
    objects = {o.object_id: o for o in trace.cognitive_turn.input_snapshot.action_grounded_objects
               if o.object_type in _GROUNDED_TYPES and o.object_id.startswith('understanding-')}
    extra = (*clarification_content(trace), *correction_acknowledgements(trace))
    if not objects:
        return extra
    frames = {f.frame_id: f for f in trace.interpretation.semantic_frames}
    targeted = {t.ref for move in trace.action_selection.plan.moves for t in move.target_refs if t.ref in objects}
    items = []
    for ref in sorted(targeted):
        item = objects[ref]
        frame = frames[item.source_refs[0]]
        arguments = frame.arguments
        if frame.predicate == 'contextual_perspective_request':
            from .continuity import resolve_pointer, trace_messages
            messages = trace_messages(trace)
            first_source, first = resolve_pointer(arguments['first_basis_ref'], messages)
            second_source, second = resolve_pointer(arguments['second_basis_ref'], messages)
            items.append(GroundedProposition(
                content_id=_id('reference', ref), cognitive_turn_id=trace.cognitive_turn_id,
                source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE,
                source_refs=tuple(dict.fromkeys((first_source, second_source, frame.frame_id))),
                represents_refs=(ref,), authorized_for_realization=True, sensitivity_class=SensitivityClass.PERSONAL,
                epistemic_status=ContentEpistemicStatus.TENTATIVE, attribution=ContentAttribution.USER,
                subject=PropositionTerm(ref=ref, surface_anchor='what you described'),
                predicate='dialogue.possible_' + arguments['relation'], certainty=Certainty.TENTATIVE,
                arguments=(PropositionArgument(role='first_basis', value_type=ArgumentValueType.STRING,
                                               value=first, surface_anchor=first),
                           PropositionArgument(role='second_basis', value_type=ArgumentValueType.STRING,
                                               value=second, surface_anchor=second)),
            ))
            continue
        status = ContentEpistemicStatus.ATTRIBUTED
        if item.object_id.startswith('understanding-statement:'):
            # What they shared, in their own words: a topic to follow up on.
            anchors = tuple(frames[source].arguments.get('proposition') for source in item.source_refs)
            kind = DialogueReferenceKind.TOPIC
        elif item.object_type == T.UNKNOWN:
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
            source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=item.source_refs,
            represents_refs=(ref,), authorized_for_realization=True, sensitivity_class=SensitivityClass.PERSONAL,
            epistemic_status=status, attribution=ContentAttribution.USER,
            reference_kind=kind, target_ref=ref, reference_form=ReferenceForm.ANCHORED, surface_anchors=anchors))
    return (*extra, *items)


def correction_acknowledgements(trace) -> tuple:
    """v1.5: acknowledging a correction names the corrected value, in the user's words.

    The acknowledged user act is the correction itself; its anchored reference
    supersedes the generic structural one (see ``realization_inventory``), so
    the receipt reads "got it, Friday" rather than an empty "I hear you".
    """
    if _registry(trace) not in CONVERSATION_REGISTRIES:
        return ()
    hypotheses = {h.hypothesis_id: h for h in trace.interpretation.hypotheses}
    frames = {f.frame_id: f for f in trace.interpretation.semantic_frames}
    out = {}
    for move in trace.action_selection.plan.moves:
        if move.definition_ref.action_key != 'give_feedback.acknowledge':
            continue
        for target in move.target_refs:
            hypothesis = hypotheses.get(target.ref)
            if hypothesis is None or hypothesis.hypothesis_key != 'interpretation.act.correct':
                continue
            corrections = [frames[ref] for ref in hypothesis.semantic_frame_refs
                           if ref in frames and frames[ref].predicate == 'correction']
            values = [f.arguments.get('corrected_value') for f in corrections]
            anchors = tuple(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
            if not anchors:
                continue
            out[target.ref] = DialogueReference(
                content_id=_id('correction-acknowledgement', target.ref), cognitive_turn_id=trace.cognitive_turn_id,
                source_kind=ContentSourceKind.CURRENT_USER_STATEMENT,
                source_refs=tuple(f.frame_id for f in corrections), represents_refs=(target.ref,),
                authorized_for_realization=True, epistemic_status=ContentEpistemicStatus.OBSERVED,
                attribution=ContentAttribution.USER, reference_kind=DialogueReferenceKind.USER_CONTRIBUTION,
                target_ref=target.ref, reference_form=ReferenceForm.ANCHORED, surface_anchors=anchors,
            )
    return tuple(out[key] for key in sorted(out))


def _registry(trace) -> str:
    return trace.orchestration.calls[0].payload['ontology']['predicate_registry_version']


def choice_clarification(target_ref: str, frame, trace, *, content_id: str) -> tuple:
    """v1.5: an ambiguous recall asks which of the user's own candidates they mean.

    One unresolved choice question plus an anchored option per candidate, each
    the user's exact words and each representing the clarified target, so all
    of them bind to the clarifying move. Replaces the v1.4 reference whose
    anchors were the bare candidates ("Who do you mean by Maya or Priya?").
    """
    from ..contracts.actions import QuestionForm
    from ..contracts.realization import GroundedQuestion
    from .continuity import resolve_pointer, trace_messages
    messages = trace_messages(trace)
    candidates = [resolve_pointer(ref, messages) for ref in frame.arguments['candidate_refs']]
    options = tuple(DialogueReference(
        content_id=_id('clarification-option', target_ref, str(index)), cognitive_turn_id=trace.cognitive_turn_id,
        source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE, source_refs=(source, frame.frame_id),
        represents_refs=(target_ref,), authorized_for_realization=True,
        epistemic_status=ContentEpistemicStatus.ATTRIBUTED, attribution=ContentAttribution.USER,
        reference_kind=DialogueReferenceKind.OPTION, target_ref=target_ref,
        reference_form=ReferenceForm.ANCHORED, surface_anchors=(text,),
    ) for index, (source, text) in enumerate(candidates))
    question = GroundedQuestion(
        content_id=content_id, cognitive_turn_id=trace.cognitive_turn_id,
        source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=(frame.frame_id,),
        represents_refs=(target_ref,), authorized_for_realization=True,
        epistemic_status=ContentEpistemicStatus.UNRESOLVED, attribution=ContentAttribution.USER,
        question_form=QuestionForm.CHOICE, option_content_refs=tuple(item.content_id for item in options),
    )
    return (question, *options)


def clarification_content(trace):
    """Specific elicitation content even when uncertainty prevented goal adoption.

    Action can legally clarify a semantic unknown independently of a request
    goal. In v1.4 that path must not fall back to a content-free generic
    reference. In v1.5 only an ambiguous recall adds content (a choice between
    the user's candidates); any other clarification keeps the generic
    reference, because the v1.4 elicitation anchor ("what you are referring
    to") was worded back as if the user had said it.
    """
    registry = _registry(trace)
    if registry not in CONTINUITY_REGISTRIES:
        return ()
    conversation = registry in CONVERSATION_REGISTRIES
    from ..requirement_resolution_v0.manifest import elicitation_anchor
    supplied = {ref for item in trace.requirement_resolution.realization_content for ref in item.represents_refs}
    unknowns = {u.unknown_id: u for u in trace.interpretation.unknowns}
    frames = trace.interpretation.semantic_frames
    out = {}
    for move in trace.action_selection.plan.moves:
        if move.definition_ref.action_key != 'seek_information.clarify':
            continue
        for target in move.target_refs:
            if target.ref in supplied:
                continue
            unknown = unknowns.get(target.ref)
            if unknown is not None:
                matches = [f for f in frames if set(f.evidence_refs) & set(unknown.evidence_refs)]
                name = unknown.key
            else:
                matches = [f for f in frames if target.ref.startswith(f.frame_id + '#')]
                name = target.ref.rsplit('#', 1)[-1]
            if len(matches) != 1:
                continue
            frame = matches[0]
            has_candidates = frame.predicate == 'dialogue_recall_request' and frame.arguments.get('candidate_refs')
            if conversation:
                if has_candidates:
                    out[target.ref] = choice_clarification(target.ref, frame, trace,
                                                           content_id=_id('clarification', target.ref))
                continue
            anchors = (elicitation_anchor(name),)
            sources = (frame.frame_id,)
            if has_candidates:
                from .continuity import resolve_pointer, trace_messages
                messages = trace_messages(trace)
                candidates = [resolve_pointer(ref, messages) for ref in frame.arguments['candidate_refs']]
                anchors = tuple(text for _, text in candidates)
                sources = tuple(dict.fromkeys((*sources, *(ref for ref, _ in candidates))))
            out[target.ref] = (DialogueReference(
                content_id=_id('clarification', target.ref), cognitive_turn_id=trace.cognitive_turn_id,
                source_kind=ContentSourceKind.CURRENT_USER_STATEMENT, source_refs=sources,
                represents_refs=(target.ref,), authorized_for_realization=True,
                epistemic_status=ContentEpistemicStatus.UNRESOLVED, attribution=ContentAttribution.USER,
                reference_kind=DialogueReferenceKind.UNRESOLVED_ELEMENT, target_ref=target.ref,
                reference_form=ReferenceForm.ANCHORED, surface_anchors=anchors,
            ),)
    return tuple(item for key in sorted(out) for item in out[key])


__all__ = ['GROUNDING_REGISTRIES', 'UNDERSTANDING_CONTENT_PRODUCER', 'project_understanding',
           'project_understanding_objects', 'understanding_content']
