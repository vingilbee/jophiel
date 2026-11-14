"""Her mind (``services/mind``, ``services/mind_state``): what she has been thinking, and what she keeps going.

    GET /api/mind/thoughts?minutes=120    her recent thoughts, oldest first
    GET /api/mind/state                   thinking under way, what she scheduled to come back to, what she keeps
                                          open, and what her recent wakes came to
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_db
from app.schemas.mind import MindStateOut, ThoughtOut
from app.services import mind, mind_state

# The owner only (deps.require_owner): her insides are not for other users.
router = APIRouter(prefix="/mind", tags=["mind"], dependencies=[Depends(require_owner)])


@router.get("/thoughts", response_model=list[ThoughtOut])
def list_thoughts(minutes: int = Query(default=120, ge=1, le=7 * 24 * 60), user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> list[ThoughtOut]:
    return [ThoughtOut.model_validate(t) for t in mind.list_thoughts(db, user.id, minutes=minutes)]


@router.get("/state", response_model=MindStateOut)
def state(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MindStateOut:
    return MindStateOut.model_validate(mind_state.view(db, user.id))
