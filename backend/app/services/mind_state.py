"""Her mind's continuity as one read-only view (GET /api/mind/state): thinking under way, reconsiderations she
scheduled, the questions, promises and intentions she keeps open, and what her recent wakes came to. Everything is
read from its owner's table (coordinator work and outputs, working memory); nothing is computed or kept here."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CoordinatorOutput, CoordinatorWork, WorkingItem
from app.services import coordinator

OPEN_KINDS = ("question", "commitment", "intention")


def view(db: Session, user_id: uuid.UUID, *, limit: int = 20) -> dict[str, Any]:
    thinking = list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.kind.in_(("mind", "recall")),
        CoordinatorWork.status.in_(coordinator.ACTIVE)).order_by(CoordinatorWork.created_at).limit(limit)))
    wakes = coordinator.scheduled_wakes(db, user_id, limit=limit)
    items = list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.kind.in_(OPEN_KINDS), WorkingItem.closed_at.is_(None))
        .order_by(WorkingItem.created_at.desc()).limit(limit)))
    outputs = list(db.scalars(select(CoordinatorOutput).where(
        CoordinatorOutput.user_id == user_id, CoordinatorOutput.phase == "follow_up")
        .order_by(CoordinatorOutput.updated_at.desc()).limit(limit)))
    works = {w.id: w for w in db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.id.in_([o.work_id for o in outputs if o.work_id] or [uuid.uuid4()])))}
    return {
        "thinking": [{"id": str(w.id), "kind": w.kind, "status": w.status,
                      "about": (w.input.get("think_about") or w.input.get("message") or "")[:300],
                      "conversation_id": str(w.conversation_id) if w.conversation_id else None,
                      "created_at": w.created_at}
                     for w in thinking if not w.input.get("autonomous")],
        "scheduled": [{"id": str(w.id), "about": w.input.get("about", ""), "due_at": w.next_attempt_at,
                       "item": w.input.get("item"),
                       "conversation_id": str(w.conversation_id) if w.conversation_id else None} for w in wakes],
        "open": [{"id": str(i.id), "kind": i.kind, "owner": i.owner, "text": i.text, "status": i.status,
                  "due_at": i.due_at} for i in items],
        "recent": [{"at": o.updated_at, "reason": coordinator.wake_reason(works[o.work_id])
                    if o.work_id in works else "event", "decision": o.decision, "status": o.status,
                    "conversation_id": str(o.conversation_id) if o.conversation_id else None} for o in outputs],
    }
