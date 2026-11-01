"""A message that disagrees with what she remembers, kept unresolved (docs/COLUMBINA_MEMORY_CONFLICTS.md).

"My sister in Lisbon", after the user said Nora moved to Porto: a slip, a mishearing, a trip, or a move nobody
mentioned. Memory can't tell, so it doesn't decide. Consolidation names the disagreement (``conflicts`` in the
extraction); code keeps it only when the key is a fact the model was shown and the words are the user's own,
and then leaves the fact as it was: a contradicted fact is never reworded in the same job.

From then on the disagreement is visible, not applied:

- recall marks the fact "unresolved" with the user's words, on every read path (``notes``);
- consolidation sees it next to the fact (``known_suffix``), so a later message can settle it (``settled``);
- her mind gets it as a problem in the conversation it came from (``services/mind_conversation.py``), and
  cognition (Student 2) chooses whether to ask, think it over or leave it.

It closes as ``known_confirmed`` (the user settled it and the memory holds), ``fact_changed`` (the fact was
revised since: the revision is the answer) or ``forgotten``. It never closes on its own with time.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MemoryConflict, MemoryJob, SemanticMemory
from app.schemas.long_term_memory import ConflictCandidate
from app.services.memory_corrections import _contains, _norm

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

SHOWN = 2  # unresolved disagreements shown next to one fact


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Recording (in the consolidation job's transaction)
# --------------------------------------------------------------------------- #


def record(db: Session, *, job: MemoryJob, message_id: uuid.UUID, user_text: str,
           shown: dict[str, SemanticMemory], conflicts: list[ConflictCandidate]) -> set[str]:
    """Keep each disagreement the words support: its key is a fact the model was shown (``shown``, by key),
    its words are in the user's message, and the fact didn't come from this same message. Returns the keys
    kept, which the caller must not reword in this job."""
    said = _norm(user_text)
    kept: set[str] = set()
    for candidate in conflicts:
        key = candidate.known_key.strip().lower()
        fact = shown.get(key)
        words = " ".join(candidate.user_words.split())
        if fact is None or fact.status != "active":
            _log({"op": "dropped", "user": str(job.user_id), "reason": "not a fact it was shown", "key": key})
            continue
        if not _contains(said, _norm(words)):
            _log({"op": "dropped", "user": str(job.user_id), "reason": "not the user's words", "key": key})
            continue
        if fact.source_message_id == message_id:
            continue  # the same message can't disagree with itself
        row_id = db.execute(insert(MemoryConflict).values(
            id=uuid.uuid4(), user_id=job.user_id, fact_id=fact.id, fact_revision=fact.revision,
            known_content=fact.content, stated=" ".join(candidate.stated.split()), user_words=words,
            source_message_id=message_id, conversation_id=job.conversation_id, memory_job_id=job.id,
        ).on_conflict_do_nothing(constraint="uq_memory_conflicts_once").returning(MemoryConflict.id)).scalar()
        kept.add(key)
        if row_id is not None:
            _log({"op": "recorded", "user": str(job.user_id), "conflict": str(row_id), "fact": str(fact.id),
                  "revision": fact.revision})
    return kept


# What settling a disagreement closes an open item that depends on it as, per kind.
_CLOSES = {"question": "answered", "intention": "dropped", "decision": "withdrawn", "hypothesis": "unresolved",
           "commitment": "cancelled", "goal": "abandoned"}


def settle(db: Session, fact: SemanticMemory, status: str, *, message_id: uuid.UUID | None) -> int:
    """Close the fact's open disagreements (``known_confirmed``, ``fact_changed`` or ``forgotten``), and the open
    items that depend on them (a question her mind raised about it): the settlement is their answer. Anything
    she meant to say about it is checked against this at delivery (``autonomous_speech.valid``)."""
    settled = list(db.scalars(update(MemoryConflict).where(
        MemoryConflict.fact_id == fact.id, MemoryConflict.user_id == fact.user_id, MemoryConflict.status == "open",
    ).values(status=status, settled_at=_now(), settled_by_message_id=message_id).returning(MemoryConflict.id)))
    closed = 0
    if settled:
        from app.db.models import WorkingItem
        from app.services import memory_manager

        source = memory_manager.Source("message", message_id) if message_id else None
        for entry in db.scalars(select(WorkingItem).where(
                WorkingItem.user_id == fact.user_id, WorkingItem.premise_conflict_id.in_(settled),
                WorkingItem.closed_at.is_(None))).all():
            try:
                memory_manager.resolve(db, fact.user_id, entry.id, status=_CLOSES[entry.kind], source=source,
                                       reason=f"the disagreement it was about was settled ({status.replace('_', ' ')})")
                closed += 1
            except memory_manager.MemoryError as exc:  # never fails the settlement; logged
                _log({"op": "item_not_closed", "user": str(fact.user_id), "item": str(entry.id), "why": str(exc)})
        _log({"op": "settled", "user": str(fact.user_id), "fact": str(fact.id), "status": status,
              "count": len(settled), "items_closed": closed})
    return len(settled)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def open_for(db: Session, user_id: uuid.UUID, fact_ids) -> dict[uuid.UUID, list[MemoryConflict]]:
    """Open disagreements per fact, oldest first."""
    ids = list(fact_ids)
    if not ids:
        return {}
    out: dict[uuid.UUID, list[MemoryConflict]] = {}
    for row in db.scalars(select(MemoryConflict).where(
            MemoryConflict.user_id == user_id, MemoryConflict.fact_id.in_(ids), MemoryConflict.status == "open")
            .order_by(MemoryConflict.created_at)):
        out.setdefault(row.fact_id, []).append(row)
    return out


def _said(row: MemoryConflict) -> str:
    at = row.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d") if row.created_at else "later"
    return f'on {at} they said "{row.user_words}"'


def note(rows: list[MemoryConflict]) -> str | None:
    """How recall marks a fact with open disagreements (None without any)."""
    if not rows:
        return None
    said = " / ".join(_said(r) for r in rows[-SHOWN:])
    return f"unresolved: {said}, which doesn't match this, and it isn't settled which holds"


def known_suffix(rows: list[MemoryConflict]) -> str:
    """What consolidation sees after the fact, so a later message can settle it."""
    return f" [unresolved: {' / '.join(_said(r) for r in rows[-SHOWN:])}]" if rows else ""


def due_for_mind(db: Session, *, before: datetime, since: datetime, limit: int = 20) -> list[MemoryConflict]:
    """Open disagreements not yet offered to her mind, recorded between ``since`` and ``before``."""
    return list(db.scalars(select(MemoryConflict).where(
        MemoryConflict.status == "open", MemoryConflict.offered_at.is_(None),
        MemoryConflict.created_at <= before, MemoryConflict.created_at >= since,
    ).order_by(MemoryConflict.created_at).limit(limit)))


def mark_offered(db: Session, conflict_id: uuid.UUID) -> None:
    db.execute(update(MemoryConflict).where(MemoryConflict.id == conflict_id, MemoryConflict.offered_at.is_(None))
               .values(offered_at=_now()))


def _log(record_: dict) -> None:
    path = Path(get_settings().memory_manager_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), "area": "conflict", **record_}) + "\n")
    except OSError:
        log.warning("memory: could not write %s", path)
