"""Her parts as one system (the System page; owner only).

    GET /api/system/status          every part's state, from its workers' heartbeats
    GET /api/system/parts/{part}    one part's recent activity (the owner's own)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_db
from app.services import system_parts, system_status

# The owner only (deps.require_owner): her insides are not for other users.
router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_owner)])


@router.get("/status")
def get_status() -> dict[str, Any]:
    return system_status.status()


@router.get("/parts/{part}")
def get_part(part: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    feed = system_parts.feed(db, user.id, part)
    if feed is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"No feed for part {part!r}."})
    return feed
