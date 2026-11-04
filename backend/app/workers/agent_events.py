"""Worker for agent event jobs: Jophiel acting on events (plan §8.2).

One job per call, in this runner thread: claim (leased, never two for the
same session at once), run the source's background turn, finish or retry.
Sources: the desktop game (a background turn) and her planner (``plan``:
one planning call, services/game_planning).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.db.session import SessionLocal
from app.services import agent_events, game_events, game_planning

log = logging.getLogger(__name__)


def run_once() -> bool:
    with SessionLocal() as db:
        job = agent_events.claim(db)
        if job is None:
            return False
        job_id, source, user_id = job.id, job.source, job.user_id
        reasons, payload = agent_events.reasons_of(job), dict(job.payload)
    try:
        if source == "desktop":
            asyncio.run(game_events.run(user_id, uuid.UUID(payload["session_id"]), reasons,
                                        plan_revision=payload.get("plan_revision")))
        elif source == game_planning.SOURCE:
            outcome = game_planning.run(user_id, uuid.UUID(payload["session_id"]), reasons, payload)
            log.info("planning for session %s (%s): %s", payload["session_id"], ", ".join(reasons), outcome)
        else:
            log.warning("agent event job %s has unknown source %r; dropping it", job_id, source)
    except Exception as exc:
        log.exception("agent event job %s failed", job_id)
        with SessionLocal() as db:
            agent_events.fail(db, job_id, f"{type(exc).__name__}: {exc}")
        return True
    with SessionLocal() as db:
        agent_events.finish(db, job_id)
    return True
