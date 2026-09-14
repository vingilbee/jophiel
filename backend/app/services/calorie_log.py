"""CRUD service for calorie (macro intake) logs.

All operations are scoped to a single user so one user can never read or
mutate another's entries. Route handlers stay thin and call into here
(per CLAUDE.md conventions).
"""
import uuid
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CalorieLog
from app.schemas.calorie_log import CalorieLogCreate, CalorieLogUpdate


def create_calorie_log(
    db: Session, *, user_id: uuid.UUID, data: CalorieLogCreate
) -> CalorieLog:
    log = CalorieLog(
        user_id=user_id,
        calories=data.calories,
        fat=data.fat,
        carbs=data.carbs,
        protein=data.protein,
        date=data.date,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_calorie_log(
    db: Session, *, user_id: uuid.UUID, log_id: uuid.UUID
) -> CalorieLog | None:
    return db.scalar(
        select(CalorieLog).where(
            CalorieLog.id == log_id, CalorieLog.user_id == user_id
        )
    )


def list_calorie_logs(
    db: Session,
    *,
    user_id: uuid.UUID,
    start_date: date_type | None = None,
    end_date: date_type | None = None,
) -> list[CalorieLog]:
    """Return the user's calorie logs, newest first, optionally date-bounded."""
    stmt = select(CalorieLog).where(CalorieLog.user_id == user_id)
    if start_date is not None:
        stmt = stmt.where(CalorieLog.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(CalorieLog.date <= end_date)
    stmt = stmt.order_by(CalorieLog.date.desc())
    return list(db.scalars(stmt))


def update_calorie_log(
    db: Session,
    *,
    user_id: uuid.UUID,
    log_id: uuid.UUID,
    data: CalorieLogUpdate,
) -> CalorieLog | None:
    """Apply a partial update; returns None if the log isn't found/owned."""
    log = get_calorie_log(db, user_id=user_id, log_id=log_id)
    if log is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(log, field, value)
    db.commit()
    db.refresh(log)
    return log


def delete_calorie_log(
    db: Session, *, user_id: uuid.UUID, log_id: uuid.UUID
) -> bool:
    """Delete a log; returns True if a row was removed, False otherwise."""
    log = get_calorie_log(db, user_id=user_id, log_id=log_id)
    if log is None:
        return False
    db.delete(log)
    db.commit()
    return True
