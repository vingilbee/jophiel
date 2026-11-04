"""The event queue that wakes Jophiel without a user message (plan §8.2).

A *source* (the desktop game, today) calls ``enqueue`` inside its own
transaction when something happens that she should know about. Events for the
same ``(source, dedupe_key)`` merge into one pending job, so a burst becomes
one background turn with several reasons. The worker (``workers/agent_events``)
claims jobs with a lease, never two for the same key at once.

Postgres is authoritative; ``wake`` only shortens the worker's poll.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.db.models import AgentEventJob

log = logging.getLogger(__name__)

QUEUE = "macrovision:agent-events:wakeups"
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
MAX_REASONS = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, *, user_id: uuid.UUID, source: str, dedupe_key: str, reason: str,
            payload: dict | None = None) -> None:
    """Record an event in the caller's transaction (the caller commits).

    Merges into the pending job for this key if there is one: the reason is
    appended (once) and the payload updated.
    """
    stmt = insert(AgentEventJob).values(
        id=uuid.uuid4(), user_id=user_id, source=source, dedupe_key=dedupe_key,
        reasons=[reason], payload=payload or {}, status="pending",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentEventJob.source, AgentEventJob.dedupe_key],
        index_where=AgentEventJob.status == "pending",
        set_={
            # Appended here; de-duplicated and capped where it's read (reasons_of).
            "reasons": AgentEventJob.reasons.op("||")(stmt.excluded.reasons),
            "payload": AgentEventJob.payload.op("||")(stmt.excluded.payload),
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)


def reasons_of(job: AgentEventJob) -> list[str]:
    """The job's reasons, oldest first, each once, the newest few only."""
    return list(dict.fromkeys(job.reasons))[-MAX_REASONS:]


def wake() -> None:
    url = get_settings().redis_url
    if not url:
        return
    try:
        redis.from_url(url).rpush(QUEUE, "1")
    except redis.RedisError:
        pass  # the worker's poll finds it anyway


def claim(db: Session) -> AgentEventJob | None:
    """Lease the oldest due job whose key has no other job running."""
    now = _now()
    other = aliased(AgentEventJob)
    busy = exists().where(and_(
        other.source == AgentEventJob.source, other.dedupe_key == AgentEventJob.dedupe_key,
        other.id != AgentEventJob.id, other.status == "running", other.lease_expires_at > now,
    ))
    job = db.scalar(
        select(AgentEventJob)
        .where(
            ((AgentEventJob.status == "pending") & (AgentEventJob.not_before <= now))
            | ((AgentEventJob.status == "running") & (AgentEventJob.lease_expires_at <= now)),
            ~busy,
        )
        .order_by(AgentEventJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.commit()
        return None
    job.status = "running"
    job.attempts += 1
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return job


def finish(db: Session, job_id: uuid.UUID) -> None:
    db.execute(update(AgentEventJob).where(AgentEventJob.id == job_id).values(status="done", lease_expires_at=None))
    db.commit()


def fail(db: Session, job_id: uuid.UUID, error: str) -> None:
    """Retry later, unless out of attempts. A retry that would collide with a
    newer pending job for the same key is folded into that job instead."""
    job = db.get(AgentEventJob, job_id)
    if job is None:
        return
    job.last_error = error[:2000]
    job.lease_expires_at = None
    if job.attempts >= MAX_ATTEMPTS:
        job.status = "failed"
        db.commit()
        return
    newer = db.scalar(select(AgentEventJob).where(
        AgentEventJob.source == job.source, AgentEventJob.dedupe_key == job.dedupe_key,
        AgentEventJob.status == "pending",
    ))
    if newer is not None:
        newer.reasons = [*job.reasons, *newer.reasons]
        job.status = "failed"
    else:
        job.status = "pending"
        job.not_before = _now() + timedelta(seconds=20 * job.attempts)
    db.commit()
