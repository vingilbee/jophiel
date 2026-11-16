"""The fixed, text-only personality shared by every mind evaluation.

The database is authoritative. Each worker loads one immutable snapshot on first use;
subsequent calls reuse it without querying. Restart workers to load a changed profile.
There is deliberately no automatic personality adaptation or file fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from sqlalchemy import select

from app.db.models import CoreMemory
from app.db.session import SessionLocal


@dataclass(frozen=True)
class Personality:
    name: str
    version: int
    text: str


_snapshot: Personality | None = None
_lock = Lock()


def current() -> Personality:
    global _snapshot
    if _snapshot is not None:
        return _snapshot
    with _lock:
        if _snapshot is None:
            with SessionLocal() as db:
                rows = db.scalars(select(CoreMemory).where(CoreMemory.is_active.is_(True)).limit(2)).all()
                if len(rows) != 1:
                    raise RuntimeError("Expected exactly one active personality in core_memory")
                row = rows[0]
                if not row.profile or not row.profile.strip():
                    raise RuntimeError("Active personality profile is empty")
                _snapshot = Personality(row.name, row.version, row.profile.strip())
        return _snapshot
