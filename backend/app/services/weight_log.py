"""CRUD service for weight logs.

All operations are scoped to a single user so one user can never read or
mutate another's entries. Route handlers stay thin and call into here
(per CLAUDE.md conventions).
"""
import uuid
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import WeightLog
from app.schemas.weight_log import WeightLogCreate, WeightLogUpdate


def create_weight_log(
    db: Session, *, user_id: uuid.UUID, data: WeightLogCreate
) -> WeightLog:
    log = WeightLog(user_id=user_id, weight=data.weight, date=data.date)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_weight_log(
    db: Session, *, user_id: uuid.UUID, log_id: uuid.UUID
) -> WeightLog | None:
    return db.scalar(
        select(WeightLog).where(
            WeightLog.id == log_id, WeightLog.user_id == user_id
        )
    )


def list_weight_logs(
    db: Session,
    *,
    user_id: uuid.UUID,
    start_date: date_type | None = None,
    end_date: date_type | None = None,
) -> list[WeightLog]:
    """Return the user's weight logs, newest first, optionally date-bounded."""
    stmt = select(WeightLog).where(WeightLog.user_id == user_id)
    if start_date is not None:
        stmt = stmt.where(WeightLog.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(WeightLog.date <= end_date)
    stmt = stmt.order_by(WeightLog.date.desc())
    return list(db.scalars(stmt))


def update_weight_log(
    db: Session,
    *,
    user_id: uuid.UUID,
    log_id: uuid.UUID,
    data: WeightLogUpdate,
) -> WeightLog | None:
    """Apply a partial update; returns None if the log isn't found/owned."""
    log = get_weight_log(db, user_id=user_id, log_id=log_id)
    if log is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(log, field, value)
    db.commit()
    db.refresh(log)
    return log


def delete_weight_log(
    db: Session, *, user_id: uuid.UUID, log_id: uuid.UUID
) -> bool:
    """Delete a log; returns True if a row was removed, False otherwise."""
    log = get_weight_log(db, user_id=user_id, log_id=log_id)
    if log is None:
        return False
    db.delete(log)
    db.commit()
    return True
