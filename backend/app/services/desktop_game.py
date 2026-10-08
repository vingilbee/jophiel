"""Columbina's view of, and say over, the user's game (plan §8.4, §21; stage D2a).

Two halves, both scoped to one user:

- ``describe``: the game summary rendered into each of her turns. Trusted
  facts come from the session service (state, plan, pause reasons). What the
  driver read off the screen (notes, facts, reports) is labelled untrusted
  game content, with ages.
- The operations behind her game tools: start with a goal, replace or extend
  the plan, pause, resume, end. They act only on the user's own environment,
  chosen here (never named by the model), and never touch the capture
  switch: letting her see the screen stays the user's decision.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession
from app.schemas.desktop import PlanAppend, PlanIn, PlanReplace, PlanStepIn, SessionCreate
from app.services import desktop_commands, desktop_preview
from app.services import desktop_sessions as sessions
from app.services.desktop_sessions import DesktopError

# How long an ended session still shows in the summary ("we just stopped").
RECENT_END_SECONDS = 15 * 60

# Written to Columbina: she is the one playing. The fast model that presses the
# keys is her hands, never someone she talks about.
REASONS = {
    "user_pause": "the user paused it", "user_takeover": "the user took over the controls",
    "user_end": "the user ended it", "plan_complete": "you finished everything you set out to do",
    "driver_stuck": "you got stuck", "needs_human": "you saw something outside the game (a login, a crash, a dialog) and need the user",
    "driver_error": "your game controls kept failing", "driver_unavailable": "your game controls are not available (model not configured)",
    "observation_stale": "you couldn't see the game for a while (is it the active window in the VM?)",
    "capture_off": "the preview is off, so you can't see the game", "budget": "you hit this session's step budget",
    "not_foreground": "the game stopped being the active window in the VM", "not_found": "the game isn't running in the VM",
    "minimized": "the game window was minimized", "watchdog": "the VM's input loop stalled and let go of every key",
    "controller_lost": "the VM disconnected", "controller_replaced": "the VM reconnected",
    "heartbeat_timeout": "the VM stopped answering", "environment_revoked": "the VM was removed",
    "limit_reached": "the time limit was reached", "plan_replaced": "you changed what you were doing",
}


@dataclass(frozen=True)
class GameContext:
    """What a turn needs: the summary text, whether the game tools apply, and
    a one-line "what you're doing right now" for her sense of the moment."""

    summary: str
    tools: bool
    activity: str | None = None


def _usable_environment(db: Session, user_id: uuid.UUID) -> DesktopEnvironment | None:
    return db.scalar(
        select(DesktopEnvironment)
        .where(DesktopEnvironment.owner_user_id == user_id, DesktopEnvironment.revoked_at.is_(None))
        .order_by(DesktopEnvironment.created_at)
    )


def _latest_session(db: Session, user_id: uuid.UUID, environment_id: uuid.UUID) -> DesktopSession | None:
    return db.scalar(
        select(DesktopSession)
        .where(DesktopSession.owner_user_id == user_id, DesktopSession.environment_id == environment_id)
        .order_by(DesktopSession.started_at.desc())
    )


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    return f"{round(seconds / 3600)} h ago"


def describe(db: Session, user_id: uuid.UUID) -> GameContext | None:
    """The game summary for one of Columbina's turns, or None when the user has
    no game VM (then she gets neither the summary nor the tools)."""
    if not get_settings().desktop_agent_enabled:
        return None
    env = _usable_environment(db, user_id)
    if env is None or env.paired_at is None:
        return None
    games = []
    for profile_id in env.allowed_profile_ids:
        try:
            games.append(f"{sessions.get_profile(profile_id).display_name} [{profile_id}]")
        except DesktopError:
            continue
    sight = {
        "allowed": "you can see it and the user can watch",
        "blocked": "preview off: you can't see it",
        "manual_auth": "preview off while the user signs in: you can't see it",
    }[env.capture_gate]
    lines = [
        f'Your game computer "{env.display_name}": {"connected" if env.status == "online" else "not connected"}; '
        f"{sight}. Games you have: {', '.join(games) or 'none'}.",
    ]
    session = _latest_session(db, user_id, env.id)
    now = time.time()
    if session is not None and session.state in DESKTOP_TERMINAL_STATES:
        ended_ago = now - session.ended_at.timestamp() if session.ended_at else RECENT_END_SECONDS + 1
        if ended_ago > RECENT_END_SECONDS:
            session = None
    if session is None:
        lines.append("You are not playing right now.")
        db.commit()
        return GameContext("\n".join(lines), tools=True)

    state = {
        "starting": "getting ready (waiting for the game window in the VM)",
        "running": "you are playing" if session.plan else "you have the controls but no goal yet",
        "paused": "paused", "manual": "the user has the controls", "disconnected": "disconnected from the VM",
        "ended": "stopped", "failed": "stopped with an error",
    }[session.state]
    lines.append(f"Game: {state}; started {_ago(now - session.started_at.timestamp())}.")
    if session.reason_code and session.state != "running":
        lines.append(f"Why: {REASONS.get(session.reason_code, session.reason_code)}.")
    if session.state not in DESKTOP_TERMINAL_STATES:
        lines.append("After each goal: " + ("you pick the next one yourself (keep playing is on)."
                                            if session.autonomy == "continue" else
                                            "you stop and tell the user (keep playing is off)."))
    if session.plan:
        steps = session.plan["steps"]
        done = [st for st in steps if st["state"] == "done"]
        active = next((st for st in steps if st["state"] == "active"), None) or next(
            (st for st in steps if st["state"] == "pending"), None
        )
        lines.append(f"Your goal: {session.plan['goal']}.")
        lines.append(
            f"Steps done: {len(done)} of {len(steps)}."
            + (f" Now: {active['do']} (done when: {active['done_when']})." if active else " None left.")
        )
    driver = desktop_commands.load_driver_state(session.id) or {}
    untrusted = []
    if driver.get("last"):
        untrusted.append(f'your last move: "{driver["last"].get("intent", "")}"')
    if driver.get("notes"):
        untrusted.append(f'your notes: "{driver["notes"]}"')
    facts = sorted((driver.get("facts") or {}).items(), key=lambda kv: -kv[1].get("at", 0))[:10]
    if facts:
        untrusted.append("facts: " + "; ".join(f"{k}={v['value']} ({_ago(now - v.get('at', now))})" for k, v in facts))
    reports = (driver.get("reports") or [])[-3:]
    if reports:
        untrusted.append("reports: " + "; ".join(f"{r['text']} ({_ago(now - r.get('at', now))})" for r in reports))
    if untrusted:
        lines.append("[what you noticed while playing: read off the screen, may be wrong] " + " | ".join(untrusted))
    lines.append("You can only use the game window: no browsing, installs, or accounts.")
    db.commit()
    return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, facts))


def _activity(session: DesktopSession, games: list[str], facts: list) -> str | None:
    game = games[0].split(" [")[0] if games else "a game"
    goal = session.plan["goal"].rstrip(".") if session.plan else None
    noticed = ", ".join(f"{k.split('.')[-1].replace('_', ' ')} {v['value']}" for k, v in facts[:2])
    detail = "; ".join(part for part in (f"goal: {goal}" if goal else None, f"so far: {noticed}" if noticed else None) if part)
    detail = f" ({detail})" if detail else ""
    if session.state == "running":
        return f"Right now you are playing {game} on your game computer{detail}. The user is watching."
    if session.state == "starting":
        return f"You are about to play {game} on your game computer{detail}."
    if session.state in ("paused", "manual", "disconnected"):
        why = REASONS.get(session.reason_code or "", "")
        return f"You were playing {game} on your game computer{detail}; it's paused" + (f" because {why}." if why else ".")
    return None


@dataclass(frozen=True)
class GameFrame:
    jpeg: bytes
    age_seconds: float


def latest_frame(db: Session, user_id: uuid.UUID) -> GameFrame | str:
    """Her own game, as she'd see it: the newest game-window frame (else the
    preview), under the same rules as the card's preview: capture allowed, and
    taken under the current setting by the current connection. A string says
    why there's nothing to show."""
    env = _usable_environment(db, user_id)
    db.commit()
    if env is None or env.paired_at is None:
        return "you have no game computer"
    if env.status != "online":
        return "your game computer isn't connected"
    if env.capture_gate != "allowed":
        return "the preview is off, so you can't see your game; ask the user to press Show preview"
    for kind in ("observation", "preview"):
        frame = desktop_preview.load(env.id, kind)
        if (frame is not None and frame.epoch == env.capture_epoch
                and frame.generation == env.controller_generation and frame.age_seconds < 10):
            return GameFrame(frame.jpeg, frame.age_seconds)
    return "no recent picture of your game (is it the active window in the VM?)"


def summary_for(user_id: uuid.UUID) -> GameContext | None:
    """``describe`` with its own DB session, for a turn being assembled (run in
    a thread). Never fails a turn: any error just means no game this turn."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            context = describe(db, user_id)
    except Exception:  # pragma: no cover - logged, then the turn goes on without the game
        import logging

        logging.getLogger(__name__).exception("desktop: could not build the game summary")
        return None
    return context


# --------------------------------------------------------------------------- #
# Operations behind Columbina's game tools (owner-scoped, environment chosen here)
# --------------------------------------------------------------------------- #


def _plan(goal: str, steps: list[dict] | None) -> PlanIn:
    items = [PlanStepIn(**step) for step in (steps or [])] or [
        PlanStepIn(do=goal[:120], done_when=f"{goal} is done"[:120])
    ]
    return PlanIn(goal=goal[:200], steps=items)


def _live(db: Session, user_id: uuid.UUID) -> DesktopSession:
    env = _usable_environment(db, user_id)
    session = _latest_session(db, user_id, env.id) if env else None
    if session is None or session.state in DESKTOP_TERMINAL_STATES:
        raise DesktopError("no_session", 409, "you are not playing right now; start first")
    return session


def start(db: Session, user_id: uuid.UUID, *, goal: str, steps: list[dict] | None = None,
          game: str | None = None) -> str:
    env = _usable_environment(db, user_id)
    if env is None or env.paired_at is None:
        raise DesktopError("no_environment", 409, "the user has no paired game VM")
    profile_id = game or (env.allowed_profile_ids[0] if env.allowed_profile_ids else "")
    existing = _latest_session(db, user_id, env.id)
    if existing is not None and existing.state not in DESKTOP_TERMINAL_STATES:
        # Already set up (e.g. from the card): just give it this goal.
        set_plan(db, user_id, goal=goal, steps=steps)
        return "Goal set; you're on it." + _blockers(env)
    session, _ = sessions.start_session(db, user_id, SessionCreate(
        environment_id=env.id, profile_id=profile_id, plan=_plan(goal, steps),
    ), plan_source="columbina_user_turn")
    return "Started; you're playing with that goal." + _blockers(env)


def _blockers(env: DesktopEnvironment) -> str:
    notes = []
    if env.status != "online":
        notes.append("the VM is not connected, so nothing happens until it is")
    if env.capture_gate != "allowed":
        notes.append("the preview is off, so you can't see the game: ask the user to press Show preview")
    return f" But: {'; '.join(notes)}." if notes else ""


def set_plan(db: Session, user_id: uuid.UUID, *, goal: str, steps: list[dict] | None = None,
             source: str = "columbina_user_turn") -> str:
    session = _live(db, user_id)
    if source == "columbina_planning_turn":
        limit = get_settings().desktop_max_auto_goals
        if sessions.auto_goals_set(db, session.id) >= limit:
            raise DesktopError("limit_reached", 409, f"you've picked {limit} goals yourself this session; "
                               "pause and tell the user instead of starting another")
    plan = _plan(goal, steps)
    sessions.replace_plan(db, user_id, session.id, PlanReplace(
        expected_plan_revision=session.plan_revision, source=source, **plan.model_dump(),
    ))
    return f"Done; you're now on: {plan.steps[0].do}."


def add_steps(db: Session, user_id: uuid.UUID, *, steps: list[dict]) -> str:
    session = _live(db, user_id)
    if session.plan is None:
        raise DesktopError("no_plan", 409, "there is no plan yet; set one first")
    sessions.append_plan_steps(db, user_id, session.id, PlanAppend(
        expected_plan_revision=session.plan_revision, steps=[PlanStepIn(**step) for step in steps],
    ))
    return f"Added {len(steps)} step(s) after what you're doing now."


def pause(db: Session, user_id: uuid.UUID) -> str:
    sessions.pause_session(db, user_id, _live(db, user_id).id, reason="user_pause")
    return "Paused; you've let go of the controls."


# Pauses Columbina may undo on her own: ones she caused. Never the user's.
SELF_RESUMABLE = ("driver_stuck", "planning_timeout", "plan_complete")


def resume(db: Session, user_id: uuid.UUID, *, only_own_pauses: bool = False) -> str:
    session = _live(db, user_id)
    if only_own_pauses and session.reason_code not in SELF_RESUMABLE:
        raise DesktopError("not_yours", 409, "this pause wasn't yours to undo; tell the user instead")
    sessions.resume_session(db, user_id, session.id, expected_state_revision=session.state_revision,
                            reason="columbina_resume" if only_own_pauses else "user_resume")
    return "Resumed; you're playing again."


def end(db: Session, user_id: uuid.UUID) -> str:
    sessions.end_session(db, user_id, _live(db, user_id).id, reason="user_end")
    return "Stopped playing."


def set_keep_playing(db: Session, user_id: uuid.UUID, *, keep_playing: bool) -> str:
    session = _live(db, user_id)
    sessions.set_session_options(db, user_id, session.id, autonomy="continue" if keep_playing else "stop")
    return ("You'll keep picking new goals yourself after each one." if keep_playing
            else "You'll stop and tell the user when this goal is done.")
