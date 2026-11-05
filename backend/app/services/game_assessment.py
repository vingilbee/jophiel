"""Game domain intake into shared assessment. No reflection store or lesson writer."""
from __future__ import annotations

import logging
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DesktopSession, GameExperience, LearningAssessment
from app.services import learning_assessment as LA
from app.services.game_experience import game_for_profile


def schedule(db: Session, session: DesktopSession, to_state: str) -> None:
    try:
        with db.begin_nested():
            _schedule(db, session, to_state)
    except Exception:
        logging.getLogger(__name__).exception("could not queue shared game assessment for %s", session.id)


def _schedule(db: Session, session: DesktopSession, to_state: str) -> None:
    """Retain the old bounded stretch interpretation, including ordinary/successful play.

    Session locking serializes boundaries. The shared assessment key and evidence inserts are
    retry safe; nothing writes a second kind of learned product.
    """
    if game_for_profile(session.profile_id) is None or to_state not in {"paused", "ended", "failed", "disconnected"}:
        return
    db.scalar(select(DesktopSession).where(DesktopSession.id == session.id).with_for_update())
    previous = list(db.scalars(select(LearningAssessment).where(
        LearningAssessment.user_id == session.owner_user_id,
        LearningAssessment.game_session_id == session.id,
        LearningAssessment.trigger_key.like(f"stretch:{session.id}:%"))))
    after = max((r.trigger.get("up_to", 0) for r in previous), default=0)
    from app.services.memory_reset import learning_cutoff
    cutoff = learning_cutoff(db, session.owner_user_id)
    experiences = list(db.scalars(select(GameExperience).where(
        GameExperience.user_id == session.owner_user_id, GameExperience.session_id == session.id,
        GameExperience.id > after, GameExperience.occurred_at >= cutoff)
        .order_by(GameExperience.id)))
    needed = get_settings().learning_game_pause_outcomes if to_state == "paused" else get_settings().learning_game_min_outcomes
    if sum(e.kind == "skill_outcome" for e in experiences) < needed:
        return
    # Bound each assessment without discarding the middle of long sessions.
    for start in range(0, len(experiences), 100):
        segment = experiences[start:start + 100]
        end = segment[-1]
        LA.open_assessment(
            db, user_id=session.owner_user_id, kind="execution",
            key=f"stretch:{session.id}:{end.id}", anchor_at=end.occurred_at,
            conversation_id=session.conversation_id, game_session_id=session.id,
            trigger={"why": "interpret a meaningful stretch of play, including ordinary and successful activity",
                     "from": after, "up_to": end.id,
                     "since": LA._aware(segment[0].occurred_at).isoformat(),
                     "until": (LA._aware(end.occurred_at) + timedelta(seconds=5)).isoformat()},
            evidence=[(f"experience:{e.id}", "part of this stretch", {}) for e in segment])
        after = end.id
    from app.services import system_signals
    system_signals.emit(session.owner_user_id, "hands", "learning", "stretch", session.id)
