"""Embeddings for memory events (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §12), in the background so
writing an event stays code-only and recall works before they land (by context, words and time).

Each pass takes a bounded batch of events with no embedding yet (or a failed one that's due again),
embeds "<context label>: <text>" in one call, and keeps a vector only if the event and its label still
read the same when it comes back (the fingerprint), so a stale result is discarded. A backfill is the
same pass, run until nothing is left; ``progress`` says how far it got.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MemoryContext, MemoryEvent, MemoryEventEmbedding
from app.db.session import SessionLocal
from app.services import recall

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _embed_batch(texts: list[str]) -> list[list[float]]:
    from app.services.long_term_memory import _client

    result = _client().embeddings.create(model=get_settings().memory_embedding_model, input=texts)
    return [list(item.embedding) for item in sorted(result.data, key=lambda d: d.index)]


def _due(db: Session, now: datetime, limit: int) -> list[tuple[MemoryEvent, str | None]]:
    row = MemoryEventEmbedding
    query = (select(MemoryEvent, MemoryContext.label)
             .join(MemoryContext, MemoryContext.id == MemoryEvent.context_id)
             .outerjoin(row, row.event_id == MemoryEvent.id)
             .where(or_(row.event_id.is_(None),
                        and_(row.status.in_(("pending", "failed")), row.next_attempt_at <= now,
                             row.attempts < MAX_ATTEMPTS)))
             .order_by(MemoryEvent.id.desc()).limit(limit))
    return list(db.execute(query).all())


def _text(event: MemoryEvent, label: str | None) -> tuple[str, str]:
    text = recall.event_embedding_text(label, event.text)
    return text, recall._fingerprint(recall.EMBED_TEXT_VERSION, text)


def run_once(embed=None) -> bool:
    """One batch. True when there was work (so the worker loops again at once)."""
    settings = get_settings()
    if not settings.recall_enabled:
        return False
    embed = embed or _embed_batch
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = _due(db, now, settings.recall_embedding_batch)
        if not due:
            return False
        texts = {event.id: _text(event, label) for event, label in due}
        db.execute(insert(MemoryEventEmbedding).values([
            {"event_id": event.id, "user_id": event.user_id, "model": settings.memory_embedding_model,
             "text_version": recall.EMBED_TEXT_VERSION, "fingerprint": texts[event.id][1], "status": "pending"}
            for event, _ in due]).on_conflict_do_nothing())
        db.commit()
    try:
        vectors = embed([texts[event.id][0] for event, _ in due])
        if len(vectors) != len(due):
            raise ValueError("embedding count doesn't match the batch")
    except Exception as exc:  # noqa: BLE001 - retried later; recall works without vectors meanwhile
        log.warning("recall embeddings: batch of %s failed: %s", len(due), exc)
        with SessionLocal() as db:
            for event, _ in due:
                row = db.get(MemoryEventEmbedding, event.id)
                if row is not None and row.status != "done":
                    row.status, row.attempts = "failed", row.attempts + 1
                    row.next_attempt_at = now + timedelta(minutes=2 ** min(row.attempts, 6))
            db.commit()
        return False
    kept = stale = 0
    with SessionLocal() as db:
        for (event, _), vector in zip(due, vectors, strict=True):
            current = db.execute(select(MemoryEvent, MemoryContext.label)
                                 .join(MemoryContext, MemoryContext.id == MemoryEvent.context_id)
                                 .where(MemoryEvent.id == event.id)).first()
            row = db.get(MemoryEventEmbedding, event.id)
            if current is None or row is None:
                continue  # the event was deleted meanwhile (its row cascades)
            text, fingerprint = _text(*current)
            if fingerprint != texts[event.id][1]:
                row.status, row.fingerprint, row.vector = "pending", fingerprint, None
                stale += 1
                continue
            row.status, row.vector, row.fingerprint = "done", vector, fingerprint
            row.model, row.text_version = settings.memory_embedding_model, recall.EMBED_TEXT_VERSION
            kept += 1
        db.commit()
    recall._log({"op": "embed_events", "kept": kept, "stale": stale})
    return True


def progress(db: Session, user_id) -> dict:
    """How many of this user's events are embedded, waiting, or failing (for inspection)."""
    total = db.scalar(select(func.count()).select_from(MemoryEvent).where(MemoryEvent.user_id == user_id)) or 0
    by_status = dict(db.execute(select(MemoryEventEmbedding.status, func.count())
                                .where(MemoryEventEmbedding.user_id == user_id)
                                .group_by(MemoryEventEmbedding.status)).all())
    return {"events": total, "done": by_status.get("done", 0), "pending": by_status.get("pending", 0),
            "failed": by_status.get("failed", 0),
            "not_started": max(0, total - sum(by_status.values()))}
