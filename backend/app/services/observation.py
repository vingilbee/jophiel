"""Observation (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md): one immutable stream of what sources reported.

The source's own record stays authoritative (a chat message, a game experience, a delivery); an
observation is its normalized report: typed by ``app/ai/observe/registry``, with stable identity
``(user, source, source_instance, source_event_key)``, a context when the source knows it, and pointers
instead of copied text. Retrying a report is a no-op; reusing a key with different content is a
conflict that's noted, never an overwrite.

Each observation gets a processing receipt per consumer (``memory`` only, today) in the same
transaction, so a report that commits late is still processed: progress is never an id high-water mark.

- ``services/observation_adapters.py`` turns source records into reports (and recovers missed ones).
- ``services/observation_memory.py`` is the memory consumer: binding, promotion, the pending view.
- The observation worker runs capture/recovery; the coordinator schedules memory consumption.

Nothing here calls a model.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.observe import registry
from app.core.config import get_settings
from app.db.models import (
    MemoryEvidence,
    Observation,
    ObservationAdapter,
    ObservationBinding,
    ObservationProcessing,
    ObservationSource,
)

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Every consumer a receipt can name; each kind lists its own (``registry.KindSpec.consumers``).
CONSUMERS = ("memory", "thought_memory", "learning")

_log_lock = threading.Lock()
_last_sweep = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime | None) -> datetime | None:
    """Message timestamps are stored without a zone (UTC); everything else has one."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Report:
    """One thing a source reported, before it's stored."""

    user_id: uuid.UUID
    source: str
    source_instance: str
    source_event_key: str
    kind: str
    payload: dict
    occurred_at: datetime | None = None
    version: int = 1
    context_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    delivery_id: uuid.UUID | None = None
    experience_id: int | None = None
    session_id: uuid.UUID | None = None
    thought_id: uuid.UUID | None = None
    caused_by_id: int | None = None
    outcome_of_id: int | None = None
    extra: dict = field(default_factory=dict)


def record(db: Session, report: Report) -> int | None:
    """Store one report and its processing receipts in the caller's transaction (never commits). Returns
    the new id, or None when it was already recorded. Raises ``registry.ObservationInvalid``."""
    shaped = registry.normalize(report.source, report.kind, report.payload, report.version)
    entry = shaped.spec
    values = dict(
        user_id=report.user_id, context_id=report.context_id, source=report.source,
        source_instance=report.source_instance, source_event_key=report.source_event_key, kind=report.kind,
        schema_version=report.version, actor=entry.actor, epistemic=entry.epistemic, content_origin=entry.origin,
        occurred_at=as_utc(report.occurred_at), summary=shaped.summary, payload=shaped.payload,
        salience=shaped.salience, caused_by_id=report.caused_by_id, outcome_of_id=report.outcome_of_id,
        message_id=report.message_id, delivery_id=report.delivery_id, experience_id=report.experience_id,
        session_id=report.session_id, thought_id=report.thought_id,
    )
    new_id = db.execute(insert(Observation).values(**values)
                        .on_conflict_do_nothing(constraint="uq_observations_source_key")
                        .returning(Observation.id)).scalar()
    if new_id is None:
        existing = db.scalar(select(Observation).where(
            Observation.user_id == report.user_id, Observation.source == report.source,
            Observation.source_instance == report.source_instance,
            Observation.source_event_key == report.source_event_key))
        if existing is not None and (existing.kind, existing.schema_version, existing.payload) != (
                report.kind, report.version, shaped.payload):
            note_source(db, report.user_id, report.source, report.source_instance,
                        error=f"key reused with different content: {report.source_event_key}",
                        decision={"decision": "conflict", "key": report.source_event_key, "kept": existing.id})
            _log({"op": "conflict", "user": str(report.user_id), "source": report.source,
                  "key": report.source_event_key, "kept": existing.id})
        return None
    for consumer in entry.consumers:
        db.add(ObservationProcessing(observation_id=new_id, consumer=consumer, user_id=report.user_id))
    _signal(report, new_id)
    from app.services import background_components, background_wake
    if (getattr(get_settings(), "observation_preparation_enabled", False)
            and report.kind in background_components.ELIGIBLE):
        db.add(ObservationProcessing(observation_id=new_id, consumer="preparation", user_id=report.user_id))
    background_wake.mark(db)
    note_source(db, report.user_id, report.source, report.source_instance, received=True)
    return new_id


def _signal(report: Report, new_id: int) -> None:
    """The System page's pulse for a new report: which part handed it over."""
    from app.services import system_signals
    if report.source == "conversation" and report.kind != "user_message":
        system_signals.emit(report.user_id, "mouth", "observation", report.kind, new_id)
    elif report.source in ("minecraft_pack", "desktop") and report.kind != "source_availability":
        system_signals.emit(report.user_id, "hands", "observation", report.kind, new_id)
    elif report.source == "mind":
        system_signals.emit(report.user_id, "mind", "memory", "thought", report.thought_id)


# --------------------------------------------------------------------------- #
# Source state: health and progress, mutable
# --------------------------------------------------------------------------- #


def source_row(db: Session, user_id: uuid.UUID, source: str, instance: str, *, lock: bool = False,
               skip_locked: bool = False) -> ObservationSource | None:
    """The source's state row, created on first use. With ``skip_locked``, None when another worker has it."""
    db.execute(insert(ObservationSource).values(user_id=user_id, source=source, source_instance=instance)
               .on_conflict_do_nothing())
    query = select(ObservationSource).where(ObservationSource.user_id == user_id, ObservationSource.source == source,
                                            ObservationSource.source_instance == instance)
    if lock:
        query = query.with_for_update(skip_locked=skip_locked)
    return db.scalar(query)


def note_source(db: Session, user_id: uuid.UUID, source: str, instance: str, *, received: bool = False,
                availability: str | None = None, error: str | None = None, decision: dict | None = None,
                position: dict | None = None) -> None:
    """Update a source's state in the caller's transaction."""
    now = _now()
    values: dict = {"updated_at": now}
    if received:
        values["last_received_at"] = values["last_change_at"] = now
    if availability is not None:
        values["availability"] = availability
    if error is not None:
        values["last_error"] = error[:500]
    if decision is not None:
        values["last_decision"] = {"at": now.isoformat(), **decision}
    if position is not None:
        values["position"] = position
    statement = insert(ObservationSource).values(user_id=user_id, source=source, source_instance=instance, **values)
    db.execute(statement.on_conflict_do_update(
        index_elements=["user_id", "source", "source_instance"],
        set_={**values, "revision": ObservationSource.revision + 1}))


def adapter(db: Session, name: str) -> ObservationAdapter:
    """An adapter's run state; its ``started_at`` is when capture began (nothing older is imported)."""
    db.execute(insert(ObservationAdapter).values(name=name).on_conflict_do_nothing())
    return db.get(ObservationAdapter, name)


# --------------------------------------------------------------------------- #
# Retention (spec §12)
# --------------------------------------------------------------------------- #


def prune(db: Session, now: datetime | None = None, limit: int = 1000) -> int:
    """Delete routine observations past retention that every consumer has applied or skipped and that no
    memory points at. Notable and critical ones stay (there's no consolidation yet). Commits."""
    now = now or _now()
    horizon = now - timedelta(days=get_settings().observation_retention_days)
    unfinished = exists().where(ObservationProcessing.observation_id == Observation.id,
                                ObservationProcessing.status.in_(("pending", "retry", "failed")))
    cited = exists().where(MemoryEvidence.observation_id == Observation.id)
    ids = list(db.scalars(select(Observation.id).where(
        Observation.salience == "routine", Observation.received_at < horizon, ~unfinished, ~cited,
    ).order_by(Observation.id).limit(limit)))
    if ids:
        db.execute(delete(Observation).where(Observation.id.in_(ids)))
    db.commit()
    return len(ids)


def lookback(now: datetime) -> datetime:
    """How far back recovery scans source records: inside retention, so a pruned report is never
    recaptured. (A source transaction left open longer than this isn't recovered.)"""
    return now - timedelta(days=get_settings().observation_retention_days - 1)


# --------------------------------------------------------------------------- #
# Capture/recovery tick
# --------------------------------------------------------------------------- #


def tick(db: Session, now: datetime | None = None, *, consume_memory: bool = True) -> dict:
    """Capture what's new, recover what was missed (every OBSERVATION_SWEEP_SECONDS), prune, and let the
    memory consumer process what's due, all within the tick's time budget. Code only. Returns what it did."""
    global _last_sweep
    from app.services import observation_adapters, observation_memory

    settings = get_settings()
    now = now or _now()
    deadline = time.monotonic() + settings.observation_tick_budget_seconds
    done: dict = {"captured": observation_adapters.pull(db, now, deadline)}
    swept = time.time() - _last_sweep >= settings.observation_sweep_seconds
    if swept:
        _last_sweep = time.time()
        done["recovered"] = observation_adapters.sweep(db, now)
        done["pruned"] = prune(db, now)
    if swept or done["captured"]:
        run = adapter(db, observation_adapters.CONVERSATION)
        run.last_run_at = now
        run.last_decision = {"at": now.isoformat(), "decision": "apply" if any(done.values()) else "skip",
                             **{k: v for k, v in done.items() if k != "memory"}}
        db.commit()
    if consume_memory:
        # A fresh clock: what this tick just captured is due from its insert time, after ``now``.
        done["memory"] = observation_memory.process(db, max(now, _now()), deadline)
    if any(done.values()):
        _log({"op": "tick", **done})
    return done


def reset_restart(db: Session, user_ids: list[uuid.UUID] | None) -> None:
    """After an observation reset: capture starts again from now, so nothing already seen comes back."""
    from app.services import observation_adapters

    observation_adapters.restart(db, user_ids, _now())


# --------------------------------------------------------------------------- #
# Inspection (GET /api/memory/observations)
# --------------------------------------------------------------------------- #


def effective_context(observation: Observation, binding: ObservationBinding | None) -> uuid.UUID | None:
    return observation.context_id or (binding.context_id if binding is not None else None)


def inspect(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or _now()
    sources = list(db.scalars(select(ObservationSource).where(ObservationSource.user_id == user_id)
                              .order_by(ObservationSource.updated_at.desc()).limit(50)))
    counts = dict(db.execute(select(ObservationProcessing.status, func.count()).where(
        ObservationProcessing.user_id == user_id, ObservationProcessing.consumer == "memory",
    ).group_by(ObservationProcessing.status)).all())
    oldest = db.scalar(select(func.min(Observation.received_at)).join(
        ObservationProcessing, ObservationProcessing.observation_id == Observation.id).where(
        ObservationProcessing.user_id == user_id, ObservationProcessing.consumer == "memory",
        ObservationProcessing.status.in_(("pending", "retry"))))
    rows = db.execute(
        select(Observation, ObservationProcessing, ObservationBinding)
        .join(ObservationProcessing, and_(ObservationProcessing.observation_id == Observation.id,
                                          ObservationProcessing.consumer == "memory"))
        .outerjoin(ObservationBinding, ObservationBinding.observation_id == Observation.id)
        .where(Observation.user_id == user_id)
        .order_by(Observation.received_at.desc(), Observation.id.desc()).limit(50)).all()
    failures = db.execute(
        select(Observation, ObservationProcessing)
        .join(ObservationProcessing, ObservationProcessing.observation_id == Observation.id)
        .where(ObservationProcessing.user_id == user_id,
               ObservationProcessing.status.in_(("failed", "retry")))
        .order_by(ObservationProcessing.next_attempt_at.desc()).limit(10)).all()
    adapters = list(db.scalars(select(ObservationAdapter).order_by(ObservationAdapter.name)))
    db.commit()
    return {
        "counts": counts,
        "lag_seconds": (now - oldest).total_seconds() if oldest is not None else None,
        "sources": sources,
        "recent": [(o, p, effective_context(o, b), b.method if b is not None else None) for o, p, b in rows],
        "failures": failures,
        "adapters": adapters,
    }


def _log(record_: dict) -> None:
    path = Path(get_settings().observation_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **record_}, default=str) + "\n")
    except OSError:
        log.warning("observation: could not write %s", path)
