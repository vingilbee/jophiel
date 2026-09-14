"""Dashboard routes: CRUD for weight logs and calorie (macro intake) logs.

Every endpoint is scoped to the authenticated user via `get_current_user`,
and handlers stay thin — all DB work lives in the service layer.
"""
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, validate_origin
from app.db.models import User
from app.db.session import get_db
from app.schemas.calorie_log import (
    CalorieLogCreate,
    CalorieLogOut,
    CalorieLogUpdate,
)
from app.schemas.weight_log import (
    WeightLogCreate,
    WeightLogOut,
    WeightLogUpdate,
)
from app.services import calorie_log as calorie_service
from app.services import weight_log as weight_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# --------------------------------------------------------------------------- #
# Weight logs
# --------------------------------------------------------------------------- #
@router.post(
    "/weight-logs",
    response_model=WeightLogOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_origin)]
)
def create_weight_log(
    payload: WeightLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return weight_service.create_weight_log(
        db, user_id=current_user.id, data=payload
    )


@router.get("/weight-logs", response_model=list[WeightLogOut])
def list_weight_logs(
    start_date: date_type | None = Query(default=None),
    end_date: date_type | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return weight_service.list_weight_logs(
        db, user_id=current_user.id, start_date=start_date, end_date=end_date
    )


@router.get("/weight-logs/{log_id}", response_model=WeightLogOut)
def get_weight_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log = weight_service.get_weight_log(
        db, user_id=current_user.id, log_id=log_id
    )
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Weight log not found"
        )
    return log


@router.patch("/weight-logs/{log_id}", response_model=WeightLogOut, dependencies=[Depends(validate_origin)])
def update_weight_log(
    log_id: uuid.UUID,
    payload: WeightLogUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log = weight_service.update_weight_log(
        db, user_id=current_user.id, log_id=log_id, data=payload
    )
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Weight log not found"
        )
    return log


@router.delete(
    "/weight-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)]
)
def delete_weight_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = weight_service.delete_weight_log(
        db, user_id=current_user.id, log_id=log_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Weight log not found"
        )


# --------------------------------------------------------------------------- #
# Calorie logs
# --------------------------------------------------------------------------- #
@router.post(
    "/calorie-logs",
    response_model=CalorieLogOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(validate_origin)]
)
def create_calorie_log(
    payload: CalorieLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return calorie_service.create_calorie_log(
        db, user_id=current_user.id, data=payload
    )


@router.get("/calorie-logs", response_model=list[CalorieLogOut])
def list_calorie_logs(
    start_date: date_type | None = Query(default=None),
    end_date: date_type | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return calorie_service.list_calorie_logs(
        db, user_id=current_user.id, start_date=start_date, end_date=end_date
    )


@router.get("/calorie-logs/{log_id}", response_model=CalorieLogOut)
def get_calorie_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log = calorie_service.get_calorie_log(
        db, user_id=current_user.id, log_id=log_id
    )
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calorie log not found"
        )
    return log


@router.patch("/calorie-logs/{log_id}", response_model=CalorieLogOut, dependencies=[Depends(validate_origin)])
def update_calorie_log(
    log_id: uuid.UUID,
    payload: CalorieLogUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log = calorie_service.update_calorie_log(
        db, user_id=current_user.id, log_id=log_id, data=payload
    )
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calorie log not found"
        )
    return log


@router.delete(
    "/calorie-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)]
)
def delete_calorie_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = calorie_service.delete_calorie_log(
        db, user_id=current_user.id, log_id=log_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calorie log not found"
        )
