"""Her mind (``services/mind``): what she has been thinking.

    GET /api/mind/thoughts?minutes=120    her recent thoughts, oldest first
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.mind import ThoughtOut
from app.services import mind

router = APIRouter(prefix="/mind", tags=["mind"])


@router.get("/thoughts", response_model=list[ThoughtOut])
def list_thoughts(minutes: int = Query(default=120, ge=1, le=7 * 24 * 60), user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> list[ThoughtOut]:
    return [ThoughtOut.model_validate(t) for t in mind.list_thoughts(db, user.id, minutes=minutes)]
