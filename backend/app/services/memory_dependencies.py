"""What her working items rely on (stage 4 of docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md).

Provenance (``memory_evidence``) says where an item came from; a dependency says what must still hold for it to
be usable: "visit Nora in Lisbon in December" relies on where Nora lives. One ``memory_dependencies`` row per
item and fact, with a role and a state:

- **role**: ``required`` (it only makes sense while the fact holds), ``context`` (mentioned, or thought with,
  without relying on it), ``none`` (checked: unrelated) or ``unknown`` (an older item, not judged yet).
- **state**: ``valid`` (checked against ``checked_revision``), ``pending`` (the fact changed; a recheck owns
  it) or ``review`` (the recheck couldn't settle it: it may be out of date).

Who writes them:

- Where an item is made with what it relies on: a plan the extractor says relies on a fact, a thought that
  concluded from facts it read, her memory tools' ``relies_on`` (``link``).
- A fact's change (``memory_reconcile``): its required and unknown dependents become ``pending``; older items
  sharing a source or a person with it become ``unknown`` candidates; the recheck settles each one.
- The user's turn, when she says an item still stands (``confirm``): the only way out of ``review`` besides
  another recheck.

A hold is a required or unknown dependency that isn't valid, on an open item (``holds``). It keeps the item
from autonomous use (the memory view without a user message, her mind, thought memory, a wake of her mind without a
message); replies still see it, marked. An incidental (``context``) link never holds anything.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import MEMORY_DEPENDENCY_HOLDING_ROLES, MemoryDependency, SemanticMemory, WorkingItem

# What a reader is told about a held item, by state.
HOLD_TEXT = {
    "pending": "on hold: a fact it relies on just changed and it's being rechecked; don't act on it yet",
    "review": ("may be out of date: a fact it relies on changed and it couldn't be rechecked; check with the user "
               "before acting on it"),
}
_RANK = {"none": 0, "unknown": 1, "context": 2, "required": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fact_id_of(ref: str | uuid.UUID) -> uuid.UUID | None:
    """``fact:<uuid>``, ``[fact:<uuid>]`` or a bare uuid; None for anything else (an event, an item)."""
    if isinstance(ref, uuid.UUID):
        return ref
    text = (ref or "").strip().strip("[]")
    kind, sep, key = text.partition(":")
    if sep and kind != "fact":
        return None
    try:
        return uuid.UUID(key if sep else text)
    except ValueError:
        return None


def link(db: Session, user_id: uuid.UUID, item_id: uuid.UUID, fact: str | uuid.UUID, *, role: str = "required",
         origin: str = "derived") -> bool:
    """The item relies on (or only mentions) the fact, as of the fact's current version. Only the user's own
    active facts; an existing link only ever gets stronger (context -> required), never weaker. Returns whether
    the fact was linked."""
    fact_id = fact_id_of(fact)
    if fact_id is None:
        return False
    row = db.scalar(select(SemanticMemory).where(SemanticMemory.id == fact_id, SemanticMemory.user_id == user_id,
                                                 SemanticMemory.status == "active"))
    if row is None:
        return False
    existing = db.scalar(select(MemoryDependency).where(MemoryDependency.item_id == item_id,
                                                        MemoryDependency.fact_id == fact_id).with_for_update())
    if existing is None:
        db.execute(insert(MemoryDependency).values(
            user_id=user_id, item_id=item_id, fact_id=fact_id, role=role, state="valid", origin=origin,
            checked_revision=row.revision).on_conflict_do_nothing(constraint="uq_memory_dependencies"))
    elif _RANK[role] > _RANK[existing.role]:
        existing.role, existing.updated_at = role, _now()
    return True


def link_refs(db: Session, user_id: uuid.UUID, item_id: uuid.UUID, refs, *, role: str = "required",
              origin: str = "derived") -> tuple[list[str], list[str]]:
    """``link`` for each reference. Returns (linked, refused)."""
    linked, refused = [], []
    for ref in refs or ():
        (linked if link(db, user_id, item_id, ref, role=role, origin=origin) else refused).append(str(ref))
    return linked, refused


def holds(db: Session, user_id: uuid.UUID | None = None, item_ids=None) -> dict[uuid.UUID, str]:
    """Open items on hold now, with why: ``pending`` (being rechecked) or ``review`` (may be out of date). A
    pending row whose recheck is gone (its message was deleted) is ``review``: nothing will settle it."""
    query = (select(MemoryDependency.item_id, MemoryDependency.state, MemoryDependency.pending_revision_id)
             .join(WorkingItem, (WorkingItem.id == MemoryDependency.item_id)
                   & (WorkingItem.user_id == MemoryDependency.user_id))
             .where(MemoryDependency.role.in_(MEMORY_DEPENDENCY_HOLDING_ROLES), MemoryDependency.state != "valid",
                    WorkingItem.closed_at.is_(None)))
    if user_id is not None:
        query = query.where(MemoryDependency.user_id == user_id)
    if item_ids is not None:
        query = query.where(MemoryDependency.item_id.in_(list(item_ids)))
    out: dict[uuid.UUID, str] = {}
    for item_id, state, owner in db.execute(query):
        state = "review" if state == "review" or owner is None else "pending"
        if out.get(item_id) != "review":  # one that may be out of date says so, whatever else is pending
            out[item_id] = state
    return out


def held_ids(db: Session, user_id: uuid.UUID | None = None) -> set[uuid.UUID]:
    return set(holds(db, user_id))


def confirm(db: Session, user_id: uuid.UUID, item_id: uuid.UUID, *, note: str = "the user's turn said it still stands") -> int:
    """The user's turn settled the item as it is: every dependency not valid becomes valid at its fact's current
    version. A recheck still running for it then finds it no longer its own. Returns how many."""
    rows = list(db.scalars(select(MemoryDependency).where(
        MemoryDependency.user_id == user_id, MemoryDependency.item_id == item_id,
        MemoryDependency.state != "valid").with_for_update()))
    if not rows:
        return 0
    revisions = dict(db.execute(select(SemanticMemory.id, SemanticMemory.revision).where(
        SemanticMemory.id.in_([r.fact_id for r in rows]))).all())
    now = _now()
    for row in rows:
        row.state, row.origin, row.checked_revision = "valid", "confirmed", revisions.get(row.fact_id, 1)
        row.pending_revision_id, row.note, row.updated_at = None, note[:300], now
    return len(rows)


def for_fact(db: Session, user_id: uuid.UUID, fact_id: uuid.UUID) -> list[MemoryDependency]:
    return list(db.scalars(select(MemoryDependency).where(MemoryDependency.user_id == user_id,
                                                          MemoryDependency.fact_id == fact_id)))


def release_owned(db: Session, revision_id: uuid.UUID, *, state: str, note: str | None) -> int:
    """Every row a recheck still owns gets ``state`` (only ever ``review`` from here: a failure keeps the hold)."""
    return db.execute(update(MemoryDependency).where(
        MemoryDependency.pending_revision_id == revision_id, MemoryDependency.state == "pending",
    ).values(state=state, note=note, updated_at=_now())).rowcount


def inspect(db: Session, user_id: uuid.UUID) -> dict:
    """For ``GET /api/memory/working``: what's held and why, counted by role and state."""
    counts: dict[str, int] = {}
    for role, state in db.execute(select(MemoryDependency.role, MemoryDependency.state).where(
            MemoryDependency.user_id == user_id)):
        counts[f"{role}/{state}"] = counts.get(f"{role}/{state}", 0) + 1
    return {"counts": counts, "held": {str(k): v for k, v in holds(db, user_id).items()}}

