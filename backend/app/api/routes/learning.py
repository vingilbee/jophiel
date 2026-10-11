"""The Learning page: what she learned from playing (``services/game_learning``).

    GET    /api/learning/lessons               her lessons (all statuses)
    PATCH  /api/learning/lessons/{id}          retire / restore to trial
    DELETE /api/learning/lessons/{id}
    GET    /api/learning/proposals             code changes she suggests
    PATCH  /api/learning/proposals/{id}        status (open/accepted/implemented/rejected), note
    GET    /api/learning/reflections           recent session reflections and what they did
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, validate_origin
from app.db.models import User
from app.db.session import get_db
from app.schemas.game_learning import LessonOut, LessonUpdate, ProposalOut, ProposalUpdate, ReflectionOut
from app.services import game_learning
from app.services.game_learning import LearningError

router = APIRouter(prefix="/learning", tags=["learning"])
mutation = [Depends(validate_origin)]

T = TypeVar("T")


def _call(fn: Callable[..., T], *args, **kwargs) -> T:
    try:
        return fn(*args, **kwargs)
    except LearningError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from None


@router.get("/lessons", response_model=list[LessonOut])
def list_lessons(game: str | None = Query(default=None), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return game_learning.list_lessons(db, user.id, game)


@router.patch("/lessons/{lesson_id}", response_model=LessonOut, dependencies=mutation)
def update_lesson(lesson_id: uuid.UUID, data: LessonUpdate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    return _call(game_learning.set_lesson_status, db, user.id, lesson_id, data.status)


@router.delete("/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_lesson(lesson_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _call(game_learning.delete_lesson, db, user.id, lesson_id)


@router.get("/proposals", response_model=list[ProposalOut])
def list_proposals(status_filter: str | None = Query(default=None, alias="status"),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return game_learning.list_proposals(db, user.id, status_filter)


@router.patch("/proposals/{proposal_id}", response_model=ProposalOut, dependencies=mutation)
def update_proposal(proposal_id: uuid.UUID, data: ProposalUpdate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return _call(game_learning.update_proposal, db, user.id, proposal_id,
                 status=data.status, user_note=data.user_note)


@router.get("/reflections", response_model=list[ReflectionOut])
def list_reflections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return game_learning.recent_reflections(db, user.id)
