"""Durable account cleanup consumed by the shared memory/deletion worker."""
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import FileDeletionJob
from app.services import asset_store, auth, image_store

logger = logging.getLogger(__name__)


def schedule_deletion(db: Session, user_id: uuid.UUID) -> FileDeletionJob:
    """Enqueue in the caller's account-deletion transaction. Do not commit."""
    job = FileDeletionJob(user_id=user_id)
    db.add(job)
    return job


def wake_worker() -> None:
    """Best effort only: PostgreSQL polling is the source of truth."""
    try:
        auth.get_redis().rpush(get_settings().file_deletion_queue_name, "wake")
    except Exception:
        logger.warning("Cleanup wake-up failed; worker will poll PostgreSQL")


def delete_user_sessions(user_id: uuid.UUID) -> None:
    """Remove existing sessions, including tokens created before this feature."""
    redis = auth.get_redis()
    for key in redis.scan_iter(match="session:*", count=200):
        raw = redis.get(key)
        if raw is None:
            continue
        try:
            owner = json.loads(raw).get("user_id")
        except (ValueError, AttributeError):
            continue
        if owner == str(user_id):
            redis.delete(key)


def process_next_job(db: Session) -> bool:
    """Claim and process one due job, returning whether work was attempted.

    Keep the row lock through local cleanup. SKIP LOCKED prevents competing
    workers from processing it; a crash releases the lock and rolls back the
    claim, so no durable `processing` state can become stranded.
    """
    now = datetime.now(timezone.utc)
    job = db.scalar(
        select(FileDeletionJob)
        .where(FileDeletionJob.status == "pending", FileDeletionJob.next_attempt_at <= now)
        .order_by(FileDeletionJob.next_attempt_at, FileDeletionJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.rollback()
        return False
    job.attempts += 1
    try:
        image_store.delete_user_files(job.user_id)
        asset_store.delete_user_files(job.user_id)
        delete_user_sessions(job.user_id)
    except Exception as exc:
        # Retain the same row lock until the retry state is committed.
        job.last_error = type(exc).__name__
        delay = min(3600, 5 * 2 ** min(job.attempts - 1, 10))
        job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        logger.warning("Account cleanup job %s failed (%s); retry scheduled", job.id, job.last_error)
    else:
        job.status = "completed"
        job.processed_at = datetime.now(timezone.utc)
        job.last_error = None
    db.commit()
    return True
