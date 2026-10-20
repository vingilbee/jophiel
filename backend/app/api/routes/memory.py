"""Her working memory (``services/memory_manager``), for inspection: read only.

    GET /api/memory/working    contexts, items (open first), recent events and the manager's state
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.memory import (
    ManagerStateOut, MemoryContextOut, MemoryEventOut, WorkingItemOut, WorkingMemoryOut,
)
from app.services import memory_manager

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/working", response_model=WorkingMemoryOut)
def working_memory(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> WorkingMemoryOut:
    if not memory_manager.enabled():
        return WorkingMemoryOut(enabled=False)
    found = memory_manager.inspect(db, user.id)
    pending = set(found["pending"])
    return WorkingMemoryOut(
        enabled=True,
        state=ManagerStateOut(**found["state"]),
        contexts=[MemoryContextOut.model_validate(c) for c in found["contexts"]],
        items=[WorkingItemOut.model_validate(i).model_copy(update={
            "has_source": i.id in found["sourced"], "needs_look": i.id in pending}) for i in found["items"]],
        events=[MemoryEventOut.model_validate(e) for e in found["events"]],
    )
