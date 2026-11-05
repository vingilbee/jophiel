"""Factual game evidence and profile metadata for the shared learning lifecycle.

This adapter owns no lessons, reflection jobs or independent learning policy.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import DesktopSession, DesktopSessionEvent, GameExperience, MemoryLessonUse, DecisionAttempt

log = logging.getLogger(__name__)
GAME_BY_PROFILE = {"minecraft_pack": "minecraft"}

def game_for_profile(profile_id: str | None) -> str | None:
    return GAME_BY_PROFILE.get(profile_id or "")

def _now():
    return datetime.now(timezone.utc)

def record_experience(db: Session, session_id: uuid.UUID, kind: str, payload: dict) -> int | None:
    """Persist a factual domain event and its existing observation together. Commits.

    Stable pack/attempt/trial identities make callback retries one event, not new support.
    Sources without a stable identity remain explicit unkeyed reports.
    """
    from sqlalchemy.dialects.postgresql import insert
    from app.services import observation_adapters

    session = db.get(DesktopSession, session_id)
    game = game_for_profile(session.profile_id) if session is not None else None
    if game is None:
        return None
    identity = payload.get("event_key")
    if identity is None and kind == "skill_outcome":
        identity = payload.get("run_id") or payload.get("attempt_id")
    if identity is None and kind == "notice" and payload.get("seq") is not None and payload.get("pack_at"):
        identity = f"{payload['seq']}:{payload['pack_at']}"
    event_key = str(uuid.uuid5(session_id, f"{kind}:{identity}")) if identity is not None else None
    row = db.scalar(insert(GameExperience).values(
        user_id=session.owner_user_id, session_id=session_id, game=game, kind=kind, payload=payload,
        event_key=event_key).on_conflict_do_nothing(constraint="uq_game_experiences_event").returning(GameExperience))
    if row is None:
        existing = db.scalar(select(GameExperience.id).where(
            GameExperience.user_id == session.owner_user_id, GameExperience.event_key == event_key))
        db.commit()
        return existing
    try:
        with db.begin_nested():
            observation_adapters.experience_recorded(db, session, row)
    except Exception:
        log.exception("game evidence: observation failed; preserving the event for recovery")
        try:
            with db.begin_nested():
                observation_adapters.mark_missed(db, session, row.id)
        except Exception:
            log.exception("game evidence: could not mark observation for recovery")
    db.commit()
    return row.id


def session_stats(db: Session, session: DesktopSession) -> dict:
    """Counted facts about one session, for comparing play with and without
    lessons. A stopped skill (cut short by a plan change or danger) counts
    neither way; a refused one is a failure (she chose something impossible)."""
    experiences = list(db.scalars(
        select(GameExperience).where(GameExperience.session_id == session.id).order_by(GameExperience.id)))
    events = list(db.scalars(select(DesktopSessionEvent.type).where(DesktopSessionEvent.session_id == session.id)))
    outcomes = [e.payload for e in experiences if e.kind == "skill_outcome"]
    judged = [o for o in outcomes if o.get("status") != "stopped"]
    done = sum(1 for o in judged if o.get("status") == "done")
    repeats, previous = 0, None
    for o in outcomes:
        key = (o.get("skill"), str(o.get("params")), o.get("status"), o.get("reason"))
        if o.get("status") != "done" and key == previous:
            repeats += 1
        previous = key
    supplied = list(db.scalars(select(MemoryLessonUse).join(
        DecisionAttempt, DecisionAttempt.id == MemoryLessonUse.attempt_id).where(
        DecisionAttempt.game_session_id == session.id, MemoryLessonUse.user_id == session.owner_user_id)))
    minutes = ((session.ended_at or _now()) - session.started_at).total_seconds() / 60
    return {
        "session_id": str(session.id), "started_at": session.started_at, "minutes": round(minutes, 1),
        "lessons_on": any(a.received for a in supplied) if supplied else None,
        "lessons": len({a.lesson_id for a in supplied if a.received}),
        "outcomes": len(outcomes), "success_rate": round(done / len(judged), 3) if judged else None,
        "repeated_failures": repeats,
        "deaths": sum(1 for e in experiences if e.kind == "notice" and e.payload.get("kind") == "died"),
        "steps_done": events.count("desktop.step_done"), "goals": events.count("desktop.plan_set"),
        "stuck": events.count("desktop.stuck"),
    }


def lessons_for_run(db: Session, session_id: uuid.UUID) -> list[dict]:
    from app.services import memory_lessons
    session = db.get(DesktopSession, session_id)
    if session is None:
        return []
    return [v.to_dict() for v in memory_lessons.for_game(
        db, session.owner_user_id, game_for_profile(session.profile_id), query=str(session.plan or {})).views]


def record_lesson_use(db: Session, session_id, attempt_id, lessons, output, status):
    import json
    from app.ai.learning import lessons as L
    from app.services import memory_lessons
    session = db.get(DesktopSession, session_id)
    if session is None:
        return
    views = tuple(L.LessonView(**{**v, "exceptions": tuple(v.get("exceptions", ())),
                                  "user_words": tuple(v.get("user_words", ())),
                                  "conflicts": tuple(v.get("conflicts", ())),
                                  "source_refs": tuple(v.get("source_refs", ()))}) for v in lessons)
    weighed, _ = L.parse_weighed(json.dumps(output), [v.ref for v in views])
    memory_lessons.record_uses(session.owner_user_id, attempt_id, memory_lessons.Supply(views=views),
                              consumer="hands", received=True, weighed=weighed, decision_status=status)
