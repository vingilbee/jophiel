"""Her working memory (``services/memory_manager``), for inspection: read only.

    GET /api/memory/working       contexts, items (open first), recent events, the manager's state and the
                                  latest corrections (what each changed and left alone)
    GET /api/memory/observations  what sources reported: source health, pending work, lag, recent
                                  reports and what memory did with each (and why), failures
    GET /api/memory/recall        shared recall: event-embedding progress and her latest replies' receipts
                                  (search outcome, what each was given and what its words referred to)
    GET /api/memory/people        people she can tell apart on the phone, a page at a time (limit, offset, q =
                                  a name): their relationship record, shared conversations, what she sent them
                                  (services/phone_people.py)
    DELETE /api/memory/people/{id}  forget one: keys, memberships, affinity and evidence, her readings, requests
                                  for space, authorizations, phone assessments and lessons; her sent text stays
    GET /api/memory/people/{id}/review  affinity B2's shadow results for one person (services/phone_relationships.py)
    POST /api/memory/people/{id}/evidence/{evidence_id}/retract  a correction: one proposal stops counting
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_db
from app.schemas.memory import (
    ManagerStateOut, MemoryContextOut, MemoryCorrectionOut, MemoryEventOut, ObservationAdapterOut, ObservationOut, ObservationsOut,
    ObservationSourceOut, PhonePeopleOut, PhonePersonOut, PhonePersonReviewOut, RecallOut, RecallReceiptOut, WorkingItemOut, WorkingMemoryOut,
)
from app.services import (
    memory_corrections, memory_dependencies, memory_manager, observation, phone_people, phone_relationships,
    recall_references,
)

# The owner only (deps.require_owner): her insides are not for other users.
router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(require_owner)])


@router.get("/working", response_model=WorkingMemoryOut)
def working_memory(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> WorkingMemoryOut:
    found = memory_manager.inspect(db, user.id)
    pending = set(found["pending"])
    return WorkingMemoryOut(
        state=ManagerStateOut(**found["state"]),
        contexts=[MemoryContextOut.model_validate(c) for c in found["contexts"]],
        items=[WorkingItemOut.model_validate(i).model_copy(update={
            "has_source": i.id in found["sourced"], "needs_look": i.id in pending}) for i in found["items"]],
        events=[MemoryEventOut.model_validate(e) for e in found["events"]],
        corrections=[MemoryCorrectionOut.model_validate(c) for c in memory_corrections.recent(db, user.id)],
        dependencies=memory_dependencies.inspect(db, user.id),
    )


def _observation_out(row, receipt, context_id, bound_by) -> ObservationOut:
    return ObservationOut(
        id=row.id, source=row.source, source_instance=row.source_instance, kind=row.kind, actor=row.actor,
        epistemic=row.epistemic, content_origin=row.content_origin, salience=row.salience, summary=row.summary,
        occurred_at=row.occurred_at, received_at=row.received_at, context_id=context_id, bound_by=bound_by,
        memory_status=receipt.status, memory_reason=receipt.reason, attempts=receipt.attempts,
        next_attempt_at=receipt.next_attempt_at)


@router.get("/observations", response_model=ObservationsOut)
def observations(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ObservationsOut:
    found = observation.inspect(db, user.id)
    return ObservationsOut(
        counts=found["counts"], lag_seconds=found["lag_seconds"],
        sources=[ObservationSourceOut.model_validate(s) for s in found["sources"]],
        recent=[_observation_out(*entry) for entry in found["recent"]],
        failures=[_observation_out(o, p, o.context_id, None) for o, p in found["failures"]],
        adapters=[ObservationAdapterOut.model_validate(a) for a in found["adapters"]],
    )


@router.get("/recall", response_model=RecallOut)
def recall_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> RecallOut:
    found = recall_references.inspect(db, user.id)
    return RecallOut(embeddings=found["embeddings"],
                     receipts=[RecallReceiptOut.model_validate(r).model_copy(update=refs)
                               for r, refs in found["receipts"]])


@router.get("/people", response_model=PhonePeopleOut)
def people(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           q: str | None = Query(None, max_length=120),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PhonePeopleOut:
    """One page of people (``limit`` / ``offset``; ``q``: a name, shown now or before); ``next_offset`` is null on
    the last page."""
    rows, total = phone_people.inspect(db, user.id, limit=limit, offset=offset, name=q)
    after = offset + len(rows)
    return PhonePeopleOut(people=[PhonePersonOut.model_validate(p) for p in rows], total=total,
                          next_offset=after if after < total else None)


@router.delete("/people/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_person(person_id: uuid.UUID, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Response:
    if not phone_people.delete_person(db, user.id, person_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/people/{person_id}/review", response_model=PhonePersonReviewOut)
def review_person(person_id: uuid.UUID, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> PhonePersonReviewOut:
    found = phone_relationships.review(db, user.id, person_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return PhonePersonReviewOut.model_validate(found)


@router.post("/people/{person_id}/evidence/{evidence_id}/retract", status_code=status.HTTP_204_NO_CONTENT)
def retract_evidence(person_id: uuid.UUID, evidence_id: uuid.UUID, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Response:
    from app.db.models import PhoneRelationshipEvidence

    row = db.get(PhoneRelationshipEvidence, evidence_id)
    if row is None or row.person_id != person_id or not phone_relationships.retract(db, user.id, evidence_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
