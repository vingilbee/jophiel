"""Explore and learn (stage H3): challenges she sets herself.

Decided with the user (2026-10-18): she may explore whenever the session plays
(the user opens the server when they want to join); she may start lessons
herself, without asking ("for now", ``GAME_PRACTICE_ASK_FIRST``); no budget yet;
the aim is flexible; the user's words always win.

The flow:
- A goal is done (``goal_done``, or she ran out of time picking one) and exploring
  is on for the session: the background turn's summary gets one challenge, picked
  by ``app/ai/desktop/curriculum.py`` from her game and her record (``propose``).
- She makes it her goal with ``set_game_plan(challenge=true)`` (``adopt``): that
  plan is the challenge. Her hands try it with their skills; where none does a
  step, she teaches herself the move (``learn_skill``, no offer needed).
- How it ends is counted on plan changes, not judged: every step done is ``done``
  (``on_goal_done``); a goal from the user is ``replaced``; another goal of her own
  is ``given_up``; a challenge in a session that ended is ``expired``. Proposals
  she didn't take are kept as ``expired`` "not taken", so she isn't offered them
  again at once. Moves learned meanwhile are kept on it (``on_learned``).
- While exploring, the cap on goals she picks herself (``DESKTOP_MAX_AUTO_GOALS``)
  doesn't apply, and a goal from the user stops a lesson she started herself
  (``game_practice.stop_own``).

Her record feeds the next pick: done challenges aren't repeated, given-up ones
are retried only with something new. Her mind sees the current challenge in its
tick (``tick_line``); the Learning page lists them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DesktopSession, GameChallenge

log = logging.getLogger(__name__)

TAKE_MINUTES = 15  # a proposal older than this isn't the one she's adopting
DONE_SHOWN = 12
FAILED_SHOWN = 8
OFFERED_SHOWN = 3
TICK_NEWS_SECONDS = 60
OPEN = ("proposed", "active")
NOT_TAKEN = "not taken"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _game(session: DesktopSession) -> str:
    from app.services.game_experience import game_for_profile

    return game_for_profile(session.profile_id) or "minecraft"


def exploring(session: DesktopSession | None) -> bool:
    """Exploring is on for this session: the switch everywhere, the session's own, her own
    player (a game pack), and keep playing (otherwise she stops after a goal anyway)."""
    from app.services import game_pack

    return (session is not None and bool(session.explore)
            and game_pack.is_pack_profile(session.profile_id) and session.autonomy == "continue")


def _active(db: Session, session_id: uuid.UUID) -> GameChallenge | None:
    return db.scalar(select(GameChallenge).where(GameChallenge.session_id == session_id,
                                                 GameChallenge.status == "active")
                     .order_by(GameChallenge.created_at.desc()).limit(1))


def _end(row: GameChallenge, status: str, result: str) -> None:
    row.status, row.result, row.ended_at = status, result[:500], _now()


def _expire_stale(db: Session, session: DesktopSession) -> None:
    """Open challenges of her earlier sessions are over; proposals here she didn't take too."""
    rows = db.scalars(select(GameChallenge).where(GameChallenge.user_id == session.owner_user_id,
                                                  GameChallenge.status.in_(OPEN)))
    for row in rows:
        if row.session_id != session.id:
            _end(row, "expired", "the session ended" if row.status == "active" else NOT_TAKEN)
        elif row.status == "proposed":
            _end(row, "expired", NOT_TAKEN)


def record(db: Session, session: DesktopSession) -> tuple[list[str], list[str], list[str]]:
    """Done, failed (with why) and just-offered challenges, newest first, for the next pick."""
    rows = list(db.scalars(select(GameChallenge).where(GameChallenge.user_id == session.owner_user_id,
                                                       GameChallenge.game == _game(session))
                           .order_by(GameChallenge.created_at.desc()).limit(100)))
    done = [r.challenge for r in rows if r.status == "done"][:DONE_SHOWN]
    failed = [f"{r.challenge} ({r.result})" for r in rows if r.status == "given_up"][:FAILED_SHOWN]
    offered = [r.challenge for r in rows if r.status == "expired" and r.result == NOT_TAKEN
               and r.session_id == session.id][:OFFERED_SHOWN]
    return done, failed, offered


def _model():
    from app.ai.desktop.driver import DriverModelError, OpenAIDriver

    settings = get_settings()
    if not settings.openai_api_key:
        raise DriverModelError("OPENAI_API_KEY is not set")
    return OpenAIDriver(settings.game_explore_model, settings.openai_api_key, timeout_seconds=60.0,
                        max_tokens=2000, image_detail="low", reasoning_effort=settings.game_explore_reasoning_effort)


def propose(db: Session, session: DesktopSession, summary: str, *, model=None) -> GameChallenge | None:
    """Pick her next challenge (one model call) when exploring and she isn't on one. Never raises."""
    from app.ai.desktop import curriculum

    if not exploring(session) or _active(db, session.id) is not None:
        return None
    _expire_stale(db, session)
    done, failed, offered = record(db, session)
    db.commit()  # don't hold anything while the model thinks
    try:
        picked = curriculum.pick(model or _model(), summary, done=done, failed=failed, offered=offered)
    except Exception as exc:  # noqa: BLE001 - without a pick she chooses her next goal as before
        log.info("no challenge for session %s: %s", session.id, exc)
        return None
    if picked is None:
        return None
    row = GameChallenge(user_id=session.owner_user_id, session_id=session.id, game=_game(session),
                        challenge=picked.challenge, why=picked.why, aim=picked.aim, check=picked.check)
    db.add(row)
    db.commit()
    log.info("challenge for session %s: %s", session.id, picked.challenge)
    return row


def proposal_lines(row: GameChallenge) -> list[str]:
    check = f" It counts as done when: {_check_text(row.check)}." if row.check else ""
    return [
        f'Your next challenge (you picked it just now, exploring): "{row.challenge}". Why: {row.why}{check}',
        "Make it your goal with set_game_plan(challenge=true): your steps, with a check on the last one when "
        "it can be counted. Your hands try it with their skills; where none does a step, teach yourself the "
        "move (learn_skill). Say what you're going for, in your own words. If something else matters more "
        "right now (the user needs you, danger), do that instead.",
    ]


def _check_text(check: dict) -> str:
    kind = check.get("kind")
    if kind in ("has", "gained", "killed"):
        return f"{kind} {check.get('count')} {check.get('what')}"
    return {"top_here": "you're on top of what's around you", "peak": "you're on the highest ground near",
            "near_player": "you're next to the user"}.get(kind, str(kind))


def on_plan_set(db: Session, session: DesktopSession, *, source: str, goal: str, challenge: bool) -> str | None:
    """A new plan was set (``session.plan_revision`` is the new one). The challenge she was on
    ends; with ``challenge`` this plan is one. The caller commits. Never raises."""
    try:
        with db.begin_nested():
            return _on_plan_set(db, session, source=source, goal=goal, challenge=challenge)
    except Exception as exc:  # noqa: BLE001 - a record never stops a plan
        log.warning("challenge record failed for session %s: %s", session.id, exc)
        return None


def _on_plan_set(db: Session, session: DesktopSession, *, source: str, goal: str, challenge: bool) -> str | None:
    current = _active(db, session.id)
    if current is not None and current.plan_revision != session.plan_revision:
        if source == "columbina_user_turn" or source == "user_api":
            _end(current, "replaced", f"the user wanted something else: {goal}")
        else:
            _end(current, "given_up", f"you set another goal: {goal}")
    if not challenge:
        return None
    since = _now() - timedelta(minutes=TAKE_MINUTES)
    row = db.scalar(select(GameChallenge).where(GameChallenge.session_id == session.id,
                                                GameChallenge.status == "proposed",
                                                GameChallenge.created_at >= since)
                    .order_by(GameChallenge.created_at.desc()).limit(1))
    if row is None:  # her own idea (or the user's "go explore"), not a proposal: still a challenge
        row = GameChallenge(user_id=session.owner_user_id, session_id=session.id, game=_game(session),
                            challenge=goal[:200], why="", aim="discover")
        db.add(row)
    row.status, row.plan_revision, row.started_at = "active", session.plan_revision, _now()
    db.flush()
    return row.challenge


def on_goal_done(db: Session, session: DesktopSession, plan_revision: int) -> None:
    """Every step of the plan is done: so is the challenge it was. The caller commits."""
    try:
        with db.begin_nested():
            row = _active(db, session.id)
            if row is not None and row.plan_revision == plan_revision:
                learned = f"; learned on the way: {', '.join(row.learned)}" if row.learned else ""
                _end(row, "done", "every step done" + learned)
    except Exception as exc:  # noqa: BLE001
        log.warning("challenge record failed for session %s: %s", session.id, exc)


def on_learned(db: Session, session_id: uuid.UUID, name: str) -> None:
    """A move learned while on a challenge belongs to it. The caller commits."""
    row = _active(db, session_id)
    if row is not None and name not in (row.learned or []):
        row.learned = [*(row.learned or []), name]


# --------------------------------------------------------------------------- #
# What she knows about it
# --------------------------------------------------------------------------- #


def summary_lines(db: Session, session: DesktopSession) -> list[str]:
    """For her game summary: whether she's exploring, the challenge she's on, what she's done."""
    if not exploring(session):
        return []
    lines = ["Exploring and learning is on: when a goal is done you pick your own next challenge, and when "
             "your hands have no skill for a step you teach yourself the move (learn_skill), saying what "
             "you're trying. The user's words always come first: what they ask replaces your challenge."]
    current = _active(db, session.id)
    if current is not None:
        why = f" ({current.why})" if current.why else ""
        lines.append(f'Your challenge now (you picked it): "{current.challenge}"{why}.')
    done, _, _ = record(db, session)
    if done:
        lines.append("Challenges you've done: " + "; ".join(done[:6]) + ".")
    return lines


def tick_line(db: Session, session_id: uuid.UUID) -> str | None:
    """For her mind's tick: the challenge she's on, or how one just ended."""
    if db is None:
        return None
    row = _active(db, session_id)
    if row is not None:
        text = f'your challenge: "{row.challenge}"'
    else:
        since = _now() - timedelta(seconds=TICK_NEWS_SECONDS)
        row = db.scalar(select(GameChallenge).where(GameChallenge.session_id == session_id,
                                                    GameChallenge.status.in_(("done", "given_up", "replaced")),
                                                    GameChallenge.ended_at >= since)
                        .order_by(GameChallenge.ended_at.desc()).limit(1))
        text = (None if row is None else
                f'your challenge "{row.challenge}": ' + {"done": "done!", "given_up": "you gave it up",
                                                         "replaced": "the user wanted something else"}[row.status])
    db.commit()
    return text


def set_explore(db: Session, user_id: uuid.UUID, *, enabled: bool) -> str:
    """Her tool: "stop exploring" / "go explore". Turning it on also turns keep playing on."""
    from app.services import game_practice

    session = game_practice._pack_session(db, user_id)
    session.explore = enabled
    if enabled:
        session.autonomy = "continue"
    db.commit()
    return ("Exploring is on: after each goal you pick your own next challenge." if enabled else
            "Exploring is off: after a goal you pick what's next as before (or stop, if keep playing is off). "
            "Your current goal carries on.")


def listing(db: Session, user_id: uuid.UUID, limit: int = 50) -> list[dict]:
    rows = db.scalars(select(GameChallenge).where(GameChallenge.user_id == user_id,
                                                  GameChallenge.status != "proposed")
                      .order_by(GameChallenge.created_at.desc()).limit(limit))
    return [{"id": str(r.id), "game": r.game, "challenge": r.challenge, "why": r.why, "aim": r.aim,
             "check": r.check, "status": r.status, "result": r.result, "learned": r.learned or [],
             "created_at": r.created_at.isoformat(),
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "ended_at": r.ended_at.isoformat() if r.ended_at else None}
            for r in rows if not (r.status == "expired" and r.result == NOT_TAKEN)]


def session_stats(db: Session, session: DesktopSession) -> dict:
    """For scripts/game_experience_report.py: what exploring did in a session, and how many model
    calls it took (no budget yet, so the count is how cost is watched)."""
    from app.db.models import GameSkillAttempt

    rows = list(db.scalars(select(GameChallenge).where(GameChallenge.session_id == session.id)))
    attempts = list(db.scalars(select(GameSkillAttempt).where(GameSkillAttempt.session_id == session.id)))
    tries = sum(len(a.tries or []) for a in attempts)
    learned = sum(1 for a in attempts if a.status == "learned")
    by = {status: sum(1 for r in rows if r.status == status) for status in ("done", "given_up", "replaced")}
    return {
        "picked": len(rows), "taken": sum(1 for r in rows if r.started_at is not None), **by,
        "lessons": len(attempts), "self_lessons": sum(1 for a in attempts if a.origin == "self"),
        "learned": learned, "tries": tries,
        # one per pick, a writer and a critic per try, one tidy per learned move
        "model_calls": len(rows) + 2 * tries + learned,
    }
