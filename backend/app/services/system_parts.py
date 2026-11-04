"""What each of her parts has been doing lately, for the System page's details panel (owner only).

Read-only and built on every request from the records the parts already keep; nothing is stored here. Every
feed is one user's (the owner's own activity) and newest first. Entries carry what the part really produced:
parts that reason show their reasoning (hard thoughts), parts that only decide show the decision, and nothing is
filled in where a record has none.

Learning has no feed here: the page reads the learning API, which already serves it whole.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChatMessage, Conversation, CoordinatorWork, DecisionAttempt, GameExperience, MemoryEvent,
    MemoryJob, MindThought, Observation, ObservationSource, WorkingItem,
)
from app.services.heard import heard_text

LIMIT = 15
PARTS = ("mouth", "cognition", "mind", "hands", "memory", "observation")


def _iso(at: datetime | None) -> str | None:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _clip(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _compact(value: Any, n: int = 400) -> str | None:
    if value in (None, "", {}, []):
        return None
    if isinstance(value, str):
        return _clip(value, n)
    return _clip(json.dumps(value, ensure_ascii=False, default=str), n)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 2)


# --------------------------------------------------------------------------- #
# Per part
# --------------------------------------------------------------------------- #

def _mouth(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(
        select(ChatMessage, Conversation.title).join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc()).limit(LIMIT)).all()
    replies = []
    for msg, title in rows:
        heard = msg.heard_chars is not None
        replies.append({"id": str(msg.id), "at": _iso(msg.created_at), "conversation": title,
                        "channel": msg.source_channel, "status": msg.status,
                        "text": _clip(heard_text(msg.content, msg.heard_chars), 600),
                        "interrupted": heard})
    return {"replies": replies}


def _decision_summary(attempt: DecisionAttempt) -> str | None:
    out = attempt.output or {}
    if attempt.learner == "student_2" and isinstance(out.get("raw"), str):
        try:
            d = json.loads(out["raw"])
        except ValueError:
            return _clip(out["raw"], 400)
        bits = [d.get("intention"), f"says: {d['communication']}" if d.get("communication") else None,
                f"move: {d['move']}" if d.get("move") not in (None, "none") else None,
                f"work: {d['work']}" if d.get("work") not in (None, "no_change") else None,
                f"thinks about: {d['think_about']}" if d.get("think_about") else None]
        return " · ".join(b for b in bits if b) or None
    return _compact(out, 400)


def _cognition(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    attempts = db.scalars(
        select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                      DecisionAttempt.learner.in_(("student_1", "student_2")))
        .order_by(DecisionAttempt.captured_at.desc()).limit(LIMIT)).all()
    work = db.scalars(select(CoordinatorWork).where(CoordinatorWork.user_id == user_id)
                      .order_by(CoordinatorWork.created_at.desc()).limit(8)).all()
    return {
        "decisions": [{"id": str(a.id), "learner": a.learner, "trigger": a.trigger, "status": a.status,
                       "error": _clip(a.error, 200), "model": a.model, "turn_id": str(a.turn_id) if a.turn_id else None,
                       "at": _iso(a.started_at or a.captured_at),
                       "seconds": _seconds(a.started_at, a.produced_at), "decided": _decision_summary(a)}
                      for a in attempts],
        "work": [{"id": str(w.id), "kind": w.kind, "status": w.status, "error": w.error_code,
                  "at": _iso(w.created_at)} for w in work],
    }


def _mind(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    from app.services import inner_state_live

    thoughts = db.scalars(select(MindThought).where(MindThought.user_id == user_id)
                          .order_by(MindThought.created_at.desc()).limit(LIMIT)).all()
    try:
        mood = inner_state_live.view(user_id)
        mood = {"words": mood.get("words"), "updated_at": mood.get("updated_at")}
    except Exception:
        mood = None
    return {
        "mood": mood,
        "thoughts": [{"id": t.id, "at": _iso(t.created_at), "level": t.level, "kind": t.kind, "trigger": t.trigger,
                      "conclusion": t.conclusion_kind, "status": t.status, "source": t.source, "text": _clip(t.text, 600),
                      "thinking": _clip(t.thinking, 1500), "question": _clip(t.question, 300),
                      "said": t.said, "dropped": _clip(t.dropped_reason, 200)} for t in thoughts],
    }


def _hands(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    picks = db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                     DecisionAttempt.learner == "hands")
                       .order_by(DecisionAttempt.captured_at.desc()).limit(8)).all()
    outcomes = db.scalars(select(GameExperience).where(GameExperience.user_id == user_id)
                          .order_by(GameExperience.id.desc()).limit(LIMIT)).all()
    events = []
    for e in outcomes:
        p = e.payload or {}
        if e.kind == "skill_outcome":
            line = f"{p.get('skill')} — {p.get('status')}" + (f": {p['reason']}" if p.get("reason") else "")
        else:
            line = p.get("text") or p.get("kind") or e.kind
        events.append({"id": e.id, "at": _iso(e.occurred_at), "kind": e.kind, "game": e.game,
                       "text": _clip(line, 300), "seconds": p.get("seconds")})
    return {"picks": [{"id": str(a.id), "at": _iso(a.started_at or a.captured_at), "status": a.status,
                       "picked": _compact((a.output or {}).get("action") or a.output, 300)} for a in picks],
            "outcomes": events}


def _memory(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    items = db.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None))
                       .order_by(WorkingItem.updated_at.desc()).limit(LIMIT)).all()
    events = db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id)
                        .order_by(MemoryEvent.recorded_at.desc()).limit(LIMIT)).all()
    pending = db.scalar(select(func.count()).select_from(MemoryJob).where(
        MemoryJob.user_id == user_id, MemoryJob.status.in_(("pending", "processing", "retry"))))
    return {
        "pending_jobs": pending or 0,
        "open": [{"id": str(i.id), "kind": i.kind, "owner": i.owner, "status": i.status, "text": _clip(i.text, 300),
                  "at": _iso(i.updated_at)} for i in items],
        "events": [{"id": e.id, "kind": e.kind, "epistemic": e.epistemic, "text": _clip(e.text, 300),
                    "at": _iso(e.recorded_at)} for e in events],
    }


def _observation(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    sources = db.scalars(select(ObservationSource).where(ObservationSource.user_id == user_id)).all()
    recent = db.scalars(select(Observation).where(Observation.user_id == user_id)
                        .order_by(Observation.received_at.desc()).limit(LIMIT)).all()
    return {
        "sources": [{"source": s.source, "instance": s.source_instance, "availability": s.availability,
                     "last_received_at": _iso(s.last_received_at), "error": _clip(s.last_error, 200)}
                    for s in sources],
        "reports": [{"id": o.id, "source": o.source, "kind": o.kind, "salience": o.salience,
                     "text": _clip(o.summary, 300), "at": _iso(o.received_at)} for o in recent],
    }


_FEEDS = {"mouth": _mouth, "cognition": _cognition, "mind": _mind, "hands": _hands, "memory": _memory,
          "observation": _observation}


def feed(db: Session, user_id: uuid.UUID, part: str) -> dict[str, Any] | None:
    """One part's recent activity for ``user_id`` (None for a part with no feed here)."""
    build = _FEEDS.get(part)
    return None if build is None else build(db, user_id)


__all__ = ["PARTS", "feed"]
