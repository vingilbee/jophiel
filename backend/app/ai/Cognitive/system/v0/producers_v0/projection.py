"""Project producer results into Action grounded objects and Realization content.

A known result becomes a factual QUESTION whose answer content is one
PROPOSITION per fact, and a closed-world ``GroundedProposition`` (or, for a
delivered claim, an attributed one) representing each proposition.  An
unavailable, stale, or unsupported result becomes the same QUESTION answered
by an EVIDENCE_RECORD of the failed lookup, represented by a ``KnowledgeGap``
that can only be worded as "no grounded answer".  Unauthorized and conflicting
results project nothing: withheld content stays inaccessible, and a conflict is
not a gap.

Object references cite the query's interpretation subject (so they resolve in
the Action reference index); the producer revision rides in ``revision_ref``.
"""

from __future__ import annotations

from ..contracts.actions import ActionGroundedObject, ActionTargetType as T
from ..contracts.interpretation import HypothesisSupport
from ..contracts.realization import (
    REALIZABLE_SENSITIVITY, ContentAttribution, ContentEpistemicStatus, ContentSourceKind, GroundedProposition,
    KnowledgeGap, PropositionArgument, PropositionTerm, RealizationContentItem,
)
from .contracts import AuthorityClass, KnowledgeGapRecord, ProducedFact, ProducerResult, ProducerStatus, SemanticQuery

_ASSERTED_BY = {"application": "columbina", "runtime": "runtime", "user": "user", "third_party": "third_party"}


def question_object_id(query: SemanticQuery) -> str:
    return f"producer-question:{query.query_id}"


def fact_object_id(fact: ProducedFact) -> str:
    return f"producer-fact:{fact.fact_id}"


def gap_object_id(gap: KnowledgeGapRecord) -> str:
    return f"producer-gap:{gap.gap_id}"


def _check(query: SemanticQuery, result: ProducerResult, gap: KnowledgeGapRecord | None) -> None:
    if result.query_ref != query.query_id or (gap is not None and gap.query_ref != query.query_id):
        raise ValueError("projection inputs answer a different query")
    if (gap is not None) != (result.status in {ProducerStatus.UNAVAILABLE, ProducerStatus.STALE, ProducerStatus.UNSUPPORTED}):
        raise ValueError("exactly an unavailable, stale, or unsupported result projects a knowledge gap")


def project_action_objects(
    query: SemanticQuery, result: ProducerResult, gap: KnowledgeGapRecord | None = None,
) -> tuple[ActionGroundedObject, ...]:
    _check(query, result, gap)
    turn, subject = query.cognitive_turn_id, query.subject_ref
    if result.status == ProducerStatus.KNOWN:
        facts = tuple(fact for fact in result.facts if fact.sensitivity in REALIZABLE_SENSITIVITY)
        answers = tuple(
            ActionGroundedObject(
                object_id=fact_object_id(fact), object_type=T.PROPOSITION, revision_ref=fact.revision,
                cognitive_turn_id=turn, source_refs=(subject,), about_refs=(subject,),
                asserted_by=_ASSERTED_BY[fact.owner],
                # A delivered claim is the user's; it is attributed, not supported.
                epistemic_status="tentative" if fact.authority_class == AuthorityClass.DELIVERED_DIALOGUE_CLAIM
                else "supported",
                support=HypothesisSupport.STRONG,
            )
            for fact in facts
        )
        if not answers:
            return ()
        answer_refs = tuple(item.object_id for item in answers)
    elif gap is not None:
        answers = (ActionGroundedObject(
            object_id=gap_object_id(gap), object_type=T.EVIDENCE_RECORD, cognitive_turn_id=turn,
            source_refs=(subject,), about_refs=(subject,), asserted_by="runtime", epistemic_status="unknown",
        ),)
        answer_refs = (answers[0].object_id,)
    else:
        return ()  # unauthorized or conflicting: nothing becomes addressable
    question = ActionGroundedObject(
        object_id=question_object_id(query), object_type=T.QUESTION, cognitive_turn_id=turn,
        source_refs=(subject,), about_refs=(subject,), focus_contribution=True, question_kind="factual",
        answer_content_refs=answer_refs,
    )
    return (question, *answers)


def project_realization_content(
    query: SemanticQuery, result: ProducerResult, gap: KnowledgeGapRecord | None = None,
) -> tuple[RealizationContentItem, ...]:
    _check(query, result, gap)
    turn = query.cognitive_turn_id
    if result.status == ProducerStatus.KNOWN:
        items: list[RealizationContentItem] = []
        for fact in result.facts:
            if fact.sensitivity not in REALIZABLE_SENSITIVITY:
                continue
            claim = fact.authority_class == AuthorityClass.DELIVERED_DIALOGUE_CLAIM
            items.append(GroundedProposition(
                content_id=f"producer-content:{fact.fact_id}", cognitive_turn_id=turn,
                source_kind=fact.source_kind, source_refs=(fact.source_ref,), represents_refs=(fact_object_id(fact),),
                authorized_for_realization=True, revision_ref=fact.revision, sensitivity_class=fact.sensitivity,
                epistemic_status=ContentEpistemicStatus.ATTRIBUTED if claim else ContentEpistemicStatus.GROUNDED,
                attribution=fact.attribution, surface_anchors=(fact.value_anchor,),
                subject=PropositionTerm(ref=query.subject_ref, surface_anchor=fact.subject_anchor),
                predicate=fact.predicate,
                arguments=(PropositionArgument(role="value", value_type=fact.value_type, value=fact.value,
                                               unit=fact.unit, surface_anchor=fact.value_anchor),),
            ))
        return tuple(items)
    if gap is not None:
        return (KnowledgeGap(
            content_id=f"producer-content:{gap.gap_id}", cognitive_turn_id=turn,
            source_kind=ContentSourceKind.RETRIEVAL_NO_RESULT, source_refs=(gap.source_ref,),
            represents_refs=(gap_object_id(gap),), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.UNKNOWN, attribution=ContentAttribution.RUNTIME,
            topic_ref=query.subject_ref, surface_anchor=query.subject_anchor, gap_kind=gap.gap_kind,
        ),)
    return ()


__all__ = [
    "fact_object_id", "gap_object_id", "project_action_objects", "project_realization_content", "question_object_id",
]
