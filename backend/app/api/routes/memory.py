"""Her working memory (``services/memory_manager``), for inspection: read only.

    GET /api/memory/working       contexts, items (open first), recent events, the manager's state and the
                                  latest corrections (what each changed and left alone)
    GET /api/memory/observations  what sources reported: source health, pending work, lag, recent
                                  reports and what memory did with each (and why), failures
    GET /api/memory/recall        shared recall: event-embedding progress and her latest replies' receipts
                                  (search outcome, what each was given and what its words referred to)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_db
from app.schemas.memory import (
    ManagerStateOut, MemoryContextOut, MemoryCorrectionOut, MemoryEventOut, ObservationAdapterOut, ObservationOut, ObservationsOut,
    ObservationSourceOut, RecallOut, RecallReceiptOut, WorkingItemOut, WorkingMemoryOut,
)
from app.services import memory_corrections, memory_dependencies, memory_manager, observation, recall_references

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
