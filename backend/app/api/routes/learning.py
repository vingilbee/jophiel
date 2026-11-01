"""The Learning page: what she learned from playing (``services/game_learning``).

    GET    /api/learning/lessons               her lessons (all statuses)
    PATCH  /api/learning/lessons/{id}          retire / restore to trial
    DELETE /api/learning/lessons/{id}
    GET    /api/learning/proposals             code changes she suggests
    PATCH  /api/learning/proposals/{id}        status (open/accepted/implemented/rejected), note
    GET    /api/learning/reflections           recent session reflections and what they did
    GET    /api/learning/skills                moves she taught herself (H2)
    PATCH  /api/learning/skills/{id}           switch one on or off for her hands
    DELETE /api/learning/skills/{id}

Learning capture (docs/learning-system/01-implementation.md), the user's own records only:

    GET    /api/learning/capture                       counts, recent attempts, gaps, this process's writer
    GET    /api/learning/capture/turns/{turn_id}       one conversation turn's chain, with what's missing
    GET    /api/learning/capture/attempts/{id}         one attempt's chain (her hands: the skill run it chose)
    GET    /api/learning/training-examples             derived examples (?learner=, ?eligible=)
    PATCH  /api/learning/training-examples/{id}        review (accepted / rejected / unreviewed)
    DELETE /api/learning/training-examples/{id}        delete from future training (and the export files)
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
from app.schemas.game_learning import (
    ChallengeOut,
    LearnedSkillOut,
    LearnedSkillUpdate,
    LessonOut,
    LessonUpdate,
    ProposalOut,
    ProposalUpdate,
    ReflectionOut,
)
from app.schemas.learning_capture import TrainingExampleOut, TrainingExampleReview
from app.services import capture_chains, game_challenges, game_learning, game_skills, training_examples
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


@router.get("/challenges", response_model=list[ChallengeOut])
def list_challenges(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return game_challenges.listing(db, user.id)


@router.get("/skills", response_model=list[LearnedSkillOut])
def list_skills(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return game_skills.listing(db, user.id)


@router.patch("/skills/{skill_id}", response_model=LearnedSkillOut, dependencies=mutation)
def update_skill(skill_id: uuid.UUID, data: LearnedSkillUpdate, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if not game_skills.set_enabled(db, user.id, skill_id, data.enabled):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such move"})
    return next(s for s in game_skills.listing(db, user.id) if s["id"] == str(skill_id))


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_skill(skill_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not game_skills.delete(db, user.id, skill_id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such move"})


# --------------------------------------------------------------------------- #
# Learning capture
# --------------------------------------------------------------------------- #


@router.get("/capture")
def capture_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return capture_chains.overview(db, user.id)


@router.get("/capture/turns/{turn_id}")
def capture_turn(turn_id: uuid.UUID, blobs: bool = Query(default=False), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    chain = capture_chains.turn(db, user.id, turn_id, blobs=blobs)
    if chain is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Nothing captured for it."})
    return chain


@router.get("/capture/attempts/{attempt_id}")
def capture_attempt(attempt_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chain = capture_chains.attempt(db, user.id, attempt_id)
    if chain is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such attempt."})
    return chain


@router.get("/training-examples", response_model=list[TrainingExampleOut])
def list_training_examples(learner: str | None = Query(default=None), eligible: bool | None = Query(default=None),
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.db.models import TrainingExample
    query = select(TrainingExample).where(TrainingExample.user_id == user.id)
    if learner:
        query = query.where(TrainingExample.learner == learner)
    if eligible is not None:
        query = query.where(TrainingExample.eligible.is_(eligible))
    return list(db.scalars(query.order_by(TrainingExample.decided_at.desc()).limit(200)))


@router.patch("/training-examples/{example_id}", response_model=TrainingExampleOut, dependencies=mutation)
def review_training_example(example_id: uuid.UUID, data: TrainingExampleReview,
                            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = training_examples.review(db, user.id, example_id, data.review_status)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such example."})
    return row


@router.delete("/training-examples/{example_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=mutation)
def delete_training_example(example_id: uuid.UUID, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    if not training_examples.delete_examples(db, user.id, [example_id]):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such example."})
