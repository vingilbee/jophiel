"""The Learning page: executable game skills and learning capture (the old learner is gone, 2026-11-26).

    GET    /api/learning/skills                moves she taught herself (H2)
    PATCH  /api/learning/skills/{id}           switch one on or off for her hands
    DELETE /api/learning/skills/{id}

Learning capture (docs/learning-system/01-implementation.md), the user's own records only:

    GET    /api/learning/capture                       counts, recent attempts, gaps, this process's writer
    GET    /api/learning/capture/turns/{turn_id}       one conversation turn's chain, with what's missing
    GET    /api/learning/capture/attempts/{id}         one attempt's chain (her hands: the skill run it chose)
    GET    /api/learning/capture/sessions/{id}         a game session's experience in a window: planner, hard
                                                       thoughts, hands, what each did, trajectory, what's missing
    GET    /api/learning/capture/trajectory            her trajectory's streams and what it costs
    GET    /api/learning/capture/trajectory/{stream}   a stretch of one stream: every tick, its events, holes
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner, validate_origin
from app.db.models import User
from app.db.session import get_db
from app.schemas.game_learning import (
    ChallengeOut,
    LearnedSkillOut,
    LearnedSkillUpdate,
)
from app.services import capture_chains, game_challenges, game_skills

# The owner only (deps.require_owner): her insides are not for other users.
router = APIRouter(prefix="/learning", tags=["learning"], dependencies=[Depends(require_owner)])
mutation = [Depends(validate_origin)]

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


@router.get("/capture/sessions/{session_id}")
def capture_session(session_id: uuid.UUID, since: datetime | None = Query(default=None),
                    until: datetime | None = Query(default=None), blobs: bool = Query(default=False),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A game session's experience in a window (default the last hour): planner, hard thoughts, hands, what each
    did, her trajectory's streams, what's missing (docs/learning-system/POST-AUDIT-01-implementation.md)."""
    chain = capture_chains.session(db, user.id, session_id, since=since, until=until, blobs=blobs)
    if chain is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such session."})
    return chain


@router.get("/capture/trajectory")
def capture_trajectory_streams(session_id: uuid.UUID | None = Query(default=None),
                               since: datetime | None = Query(default=None), until: datetime | None = Query(default=None),
                               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import trajectory

    return {"streams": trajectory.streams(db, user.id, session_id=session_id, since=since, until=until),
            "measured": trajectory.measure(db, user.id, since=since)}


@router.get("/capture/trajectory/{stream_id}")
def capture_trajectory(stream_id: uuid.UUID, from_tick: int | None = Query(default=None, ge=0),
                       to_tick: int | None = Query(default=None, ge=0), since: datetime | None = Query(default=None),
                       until: datetime | None = Query(default=None), ticks: bool = Query(default=True),
                       readable: bool = Query(default=False), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """A stretch of one stream: every tick held (at most five minutes per read), its events, what's missing."""
    from app.services import trajectory

    found = trajectory.read(db, user.id, stream_id, from_tick=from_tick, to_tick=to_tick, since=since, until=until,
                            ticks=ticks)
    if found is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such stream."})
    if readable and found["ticks"]:
        found["ticks"] = [{**t, "state": trajectory.readable(t["state"])} for t in found["ticks"]]
    return found

