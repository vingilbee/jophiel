"""Game domain intake into shared assessment. No reflection store or lesson writer.

A session's transition (paused, ended, failed, disconnected) is a review opportunity over the experience
recorded since the last stretch, ordinary and successful play included (``learning_review.game``). Code decides
only that there is new experience; the reviewer judges whether it is worth examining (post-audit 3: no count of
outcomes gates it). Ongoing play is reviewed before the session ends by the assessment sweep, and failed skills and
critical notices keep their own immediate triggers.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import DesktopSession

TRANSITIONS = {"paused", "ended", "failed", "disconnected"}


def schedule(db: Session, session: DesktopSession, to_state: str) -> None:
    if to_state not in TRANSITIONS:
        return
    try:
        with db.begin_nested():
            from app.services import learning_review
            learning_review.game(db, session, why=f"the session {to_state}: interpret the stretch of play, including "
                                                   "ordinary and successful activity")
    except Exception:
        logging.getLogger(__name__).exception("could not queue shared game assessment for %s", session.id)
