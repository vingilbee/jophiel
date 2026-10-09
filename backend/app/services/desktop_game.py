"""Columbina's view of, and say over, the user's game (plan §8.4, §21; stage D2a).

Two halves, both scoped to one user:

- ``describe``: the game summary rendered into each of her turns. Trusted
  facts come from the session service (state, plan, pause reasons). What the
  driver read off the screen (notes, facts, reports) is labelled untrusted
  game content, with ages.
- The operations behind her game tools: start with a goal, replace or extend
  the plan, tell her hands how to play, pause, resume, end. They act only on the user's own environment,
  chosen here (never named by the model), and never touch the capture
  switch: letting her see the screen stays the user's decision.
"""
from __future__ import annotations

import io
import time
import uuid
from dataclasses import dataclass

from PIL import Image

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession
from app.schemas.desktop import PlanAppend, PlanIn, PlanReplace, PlanStepIn, SessionCreate
from app.services import desktop_commands, desktop_preview, game_pack
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
    "controls_silent": "your controls stopped responding: the game computer wasn't confirming any of your moves, "
                       "so you weren't actually moving",
    "pack_offline": "your Minecraft player left the world (the pack stopped, or lost the server)",
}


@dataclass(frozen=True)
class GameContext:
    """What a turn needs: the summary text, whether the game tools apply, and
    a one-line "what you're doing right now" for her sense of the moment."""

    summary: str
    tools: bool
    activity: str | None = None


def _environments(db: Session, user_id: uuid.UUID) -> list[DesktopEnvironment]:
    """The user's usable environments, oldest first: a paired game VM, and her
    Minecraft pack (stage G) when it's configured for this user."""
    game_pack.ensure_environment(db, user_id)
    return [env for env in db.scalars(
        select(DesktopEnvironment)
        .where(DesktopEnvironment.owner_user_id == user_id, DesktopEnvironment.revoked_at.is_(None))
        .order_by(DesktopEnvironment.created_at)
    ) if env.paired_at is not None or game_pack.is_pack(env)]


def _usable_environment(db: Session, user_id: uuid.UUID, game: str | None = None) -> DesktopEnvironment | None:
    """The environment for ``game`` (a profile id), else the one with the latest
    session, else her Minecraft pack, else the VM."""
    envs = _environments(db, user_id)
    if not envs:
        return None
    if game:
        return next((env for env in envs if game in env.allowed_profile_ids), None)
    latest = db.scalar(
        select(DesktopSession)
        .where(DesktopSession.owner_user_id == user_id,
               DesktopSession.environment_id.in_([env.id for env in envs]))
        .order_by(DesktopSession.started_at.desc())
    )
    if latest is not None:
        return next(env for env in envs if env.id == latest.environment_id)
    return next((env for env in envs if game_pack.is_pack(env)), envs[0])


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


def _vm_line(env: DesktopEnvironment, *, retired: set[str] = frozenset()) -> tuple[str, list[str]]:
    games = []
    for profile_id in env.allowed_profile_ids:
        if profile_id in retired:
            continue  # played through a game pack instead
        try:
            games.append(f"{sessions.get_profile(profile_id).display_name} [{profile_id}]")
        except DesktopError:
            continue
    sight = {
        "allowed": "you can see it and the user can watch",
        "blocked": "preview off: you can't see it",
        "manual_auth": "preview off while the user signs in: you can't see it",
    }[env.capture_gate]
    return (f'Your game computer "{env.display_name}": {"connected" if env.status == "online" else "not connected"}; '
            f"{sight}. Games you have: {', '.join(games) or 'none'}."), games


def _pack_line(env: DesktopEnvironment) -> str:
    where = ("you're in the world" if env.status == "online"
             else "you're not in the world (the pack isn't running, or the server is down)")
    return (f"Minecraft as your own player [{game_pack.PACK_PROFILE_ID}]: {where}. You play next to the user "
            "(they have their own player), using skills: you decide what, your hands do it. Plan steps for it: "
            "things your skills can do (go somewhere, mine, fight, explore, craft, place, eat, sleep, follow, "
            "store, take, smelt, give), each done_when something the game can confirm "
            "(e.g. \"5 oak_log in inventory\", \"a crafting table placed\"). For an ongoing request like "
            "\"follow me\": one step such as \"follow <the user's in-game name>\", done when \"the user says to stop\".")


def _who_is_who(env: DesktopEnvironment) -> str | None:
    """Her in-game name and the other players', so "follow me" never means herself."""
    if env.status != "online":
        return None
    from app.ai.desktop.pack_client import PackClient, PackError

    try:
        state = PackClient.from_settings().state()
    except PackError:
        return None
    others = state.get("players_online") or []
    you = state.get("username", "?")
    if not others:
        return f'In Minecraft you are "{you}". No one else is in the world right now.'
    user_hint = (f' The user is "{others[0]}"' if len(others) == 1
                 else " The user is one of them (ask which if it matters)")
    return (f'In Minecraft you are "{you}" (never target yourself). Other players: '
            + ", ".join(f'"{name}"' for name in others) + f".{user_hint}: when they say \"me\", that's them.")


def describe(db: Session, user_id: uuid.UUID) -> GameContext | None:
    """The game summary for one of Columbina's turns, or None when the user has
    no game environment (then she gets neither the summary nor the tools)."""
    if not get_settings().desktop_agent_enabled:
        return None
    envs = _environments(db, user_id)
    if not envs:
        db.commit()
        return None
    env = _usable_environment(db, user_id)
    lines = []
    games: list[str] = []
    retired = set(game_pack.REPLACED_BY) if any(game_pack.is_pack(e) for e in envs) else set()
    for each in envs:
        if game_pack.is_pack(each):
            lines.append(_pack_line(each))
            who = _who_is_who(each)
            if who:
                lines.append(who)
            games.append(f"Minecraft [{game_pack.PACK_PROFILE_ID}]")
        else:
            line, vm_games = _vm_line(each, retired=retired)
            lines.append(line)
            games.extend(vm_games)
    pack = game_pack.is_pack(env)
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
        "starting": ("getting ready (joining the world)" if pack
                     else "getting ready (waiting for the game window in the VM)"),
        "running": "you are playing" if session.plan else "you have the controls but no goal yet",
        "paused": "paused", "manual": "the user has the controls",
        "disconnected": "disconnected from the world" if pack else "disconnected from the VM",
        "ended": "stopped", "failed": "stopped with an error",
    }[session.state]
    game_name = "Minecraft (your own player), " if pack else ""
    lines.append(f"Game: {game_name}{state}; started {_ago(now - session.started_at.timestamp())}.")
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
    told = desktop_commands.hands_messages(session.id) if session.state not in DESKTOP_TERMINAL_STATES else []
    if told:
        lines.append("You told your hands (they follow it every move): "
                     + "; ".join(f'"{m["text"]}" ({_ago(now - m.get("at", now))})' for m in told) + ".")
    if pack:
        lines.extend(_pack_details(env, session, driver, now))
        db.commit()
        return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, [], pack=True))
    if session.state not in DESKTOP_TERMINAL_STATES:
        careful = ("you turned it on" if (desktop_commands.careful_state(session.id) or {}).get("on")
                   else "your hands reported danger" if driver.get("careful") == "you reported danger" else None)
        if careful:
            lines.append(f"Careful mode: on ({careful}): short steps, a fresh look between each.")
    play = driver.get("play") or {}
    counted = " ".join(part for part in (play.get("recent"), play.get("view")) if part)
    if counted:
        # Counted by code from what was actually sent, so not "may be wrong".
        lines.append(f"Your moves, counted: {counted}")
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


def _pack_details(env: DesktopEnvironment, session: DesktopSession, driver: dict, now: float) -> list[str]:
    """What her hands are doing and what the game says, for a pack session.
    The game state comes from the game itself (counted by code); notes are her
    hands' own words."""
    lines = []
    running = driver.get("running")
    if running and session.state == "running":
        params = ", ".join(f"{k}={v}" for k, v in (running.get("params") or {}).items() if k != "time_limit")
        lines.append(f"Your hands are doing: {running['skill']}({params}).")
    outcomes = (driver.get("outcomes") or [])[-4:]
    if outcomes:
        lines.append("Recent results: " + "; ".join(
            f"{o['skill']} -> {o['status']}{': ' + o['reason'] if o.get('reason') else ''}"
            f" ({_ago(now - o.get('at', now))})" for o in outcomes) + ".")
    if driver.get("notes"):
        lines.append(f'Your hands\' note: "{driver["notes"]}".')
    if env.status == "online":
        try:
            from app.ai.desktop.pack_client import PackClient, PackError

            state = PackClient.from_settings().state()
            lines.append("Your player right now (from the game): " + " ".join(state.get("text", "").split("\n")))
        except PackError:
            pass
    return lines


def _activity(session: DesktopSession, games: list[str], facts: list, *, pack: bool = False) -> str | None:
    game = games[0].split(" [")[0] if games else "a game"
    if pack:
        goal = session.plan["goal"].rstrip(".") if session.plan else None
        detail = f" (goal: {goal})" if goal else ""
        if session.state == "running":
            return f"Right now you are playing Minecraft as your own player{detail}, next to the user."
        if session.state == "starting":
            return f"You are joining your Minecraft world{detail}."
        if session.state in ("paused", "manual", "disconnected"):
            why = REASONS.get(session.reason_code or "", "")
            return f"You were playing Minecraft{detail}; it's paused" + (f" because {why}." if why else ".")
        return None
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


def latest_frame(db: Session, user_id: uuid.UUID, *, at: float | None = None) -> GameFrame | str:
    """Her own game, as she'd see it: the newest game-window frame (else the
    preview), under the same rules as the card's preview: capture allowed, and
    taken under the current setting by the current connection. A string says
    why there's nothing to show.

    With ``at`` (Unix seconds): the picture from that moment instead, the
    preview first since that is what the user was looking at."""
    env = _usable_environment(db, user_id)
    db.commit()
    if game_pack.is_pack(env):
        return "you play Minecraft as your own player, so there's no screen picture of it"
    if env is None or env.paired_at is None:
        return "you have no game computer"
    if env.status != "online":
        return "your game computer isn't connected"
    if env.capture_gate != "allowed":
        return "the preview is off, so you can't see your game; ask the user to press Show preview"
    kinds = ("preview", "observation") if at is not None else ("observation", "preview")
    for kind in kinds:
        frame = desktop_preview.frame_at(env.id, at, kind) if at is not None else desktop_preview.load(env.id, kind)
        if (frame is not None and frame.epoch == env.capture_epoch
                and frame.generation == env.controller_generation
                and frame.age_seconds < (desktop_preview.HISTORY_TTL_SECONDS if at is not None else 10)):
            return GameFrame(frame.jpeg, frame.age_seconds)
    return "no recent picture of your game (is it the active window in the VM?)"


# The picture attached to her turn: small (the model reads it at low detail).
TURN_PICTURE_SIZE = (512, 288)


def turn_picture(user_id: uuid.UUID, spoke_at: float) -> tuple[bytes, str] | None:
    """A small picture of her game from when the user started speaking, and a
    note saying so, for a user turn while a game session is live. None
    otherwise (no session, preview off, no picture). Never fails a turn."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            env = _usable_environment(db, user_id)
            session = _latest_session(db, user_id, env.id) if env is not None else None
            if session is None or session.state in DESKTOP_TERMINAL_STATES or game_pack.is_pack(env):
                db.commit()
                return None
            frame = latest_frame(db, user_id, at=spoke_at)
        if isinstance(frame, str):
            return None
        image = Image.open(io.BytesIO(frame.jpeg)).convert("RGB")
        image.thumbnail(TURN_PICTURE_SIZE)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=70)
    except Exception:  # pragma: no cover - the turn goes on without a picture
        import logging

        logging.getLogger(__name__).exception("desktop: could not attach the game picture")
        return None
    before = max(0.0, time.time() - spoke_at)
    note = (f"[Your game when the user started speaking, {before:.0f} s ago: what they were looking at. "
            "Small and a moment old: for details or for what is happening now, use look_at_my_game.]")
    return out.getvalue(), note


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


def pack_look(db: Session, user_id: uuid.UUID) -> str | None:
    """For look_at_my_game in Minecraft: what the game says about her player and
    surroundings, since there's no picture. None when she isn't on the pack."""
    env = _usable_environment(db, user_id)
    db.commit()
    if not game_pack.is_pack(env):
        return None
    if env.status != "online":
        return "Can't look: your Minecraft player isn't in the world."
    from app.ai.desktop.pack_client import PackClient, PackError

    try:
        state = PackClient.from_settings().state()
    except PackError as exc:
        return f"Can't look: {exc}."
    noticed = state.get("noticed") or []
    extra = ("\nNoticed lately: " + "; ".join(n["text"] for n in noticed)) if noticed else ""
    return ("What the game says about you right now (exact, from the game itself, not a picture):\n"
            + state.get("text", "") + extra
            + "\n(Tell the user in your own words, as yourself. You can't see colours or looks, only these facts.)")


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
    game = game_pack.replacement(db, user_id, game) or game  # Minecraft on the VM -> her own player
    env = _usable_environment(db, user_id, game)
    if env is None and game:
        raise DesktopError("no_environment", 409, f"you don't have the game {game!r}")
    if env is None or (env.paired_at is None and not game_pack.is_pack(env)):
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
    if game_pack.is_pack(env):
        return ("" if env.status == "online" else
                " But: your Minecraft player isn't in the world yet (the user needs to start the Minecraft "
                "server and pack); you'll start as soon as you're in.")
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
    # A new goal starts clean: tips to her hands and careful mode were about
    # the old one (cave tips once followed her onto an open hillside).
    cleared = []
    if desktop_commands.hands_messages(session.id):
        desktop_commands.send_hands_message(session.id, "", replace=True)
        cleared.append("your tips to your hands")
    if desktop_commands.careful_state(session.id):
        desktop_commands.set_careful(session.id, False)
        cleared.append("careful mode")
    note = f" (Cleared {' and '.join(cleared)} from before.)" if cleared else ""
    return f"Done; you're now on: {plan.steps[0].do}.{note}"


def add_steps(db: Session, user_id: uuid.UUID, *, steps: list[dict]) -> str:
    session = _live(db, user_id)
    if session.plan is None:
        raise DesktopError("no_plan", 409, "there is no plan yet; set one first")
    sessions.append_plan_steps(db, user_id, session.id, PlanAppend(
        expected_plan_revision=session.plan_revision, steps=[PlanStepIn(**step) for step in steps],
    ))
    return f"Added {len(steps)} step(s) after what you're doing now."


def tell_hands(db: Session, user_id: uuid.UUID, *, message: str, replace_previous: bool = False) -> str:
    session = _live(db, user_id)
    db.commit()
    told = desktop_commands.send_hands_message(session.id, message, replace=replace_previous)
    if told is None:
        raise DesktopError("unavailable", 503, "your hands can't get messages right now (no Redis)")
    if not told:
        return "Cleared what you'd told your hands."
    return ("Your hands will follow this from the next move. Now in force: "
            + "; ".join(f'"{m["text"]}"' for m in told) + ".")


def set_careful(db: Session, user_id: uuid.UUID, *, on: bool) -> str:
    session = _live(db, user_id)
    db.commit()
    if not desktop_commands.set_careful(session.id, on):
        raise DesktopError("unavailable", 503, "careful mode can't be set right now (no Redis)")
    return ("Careful mode on for the next few minutes: short steps with a fresh look between each, from your next move."
            if on else "Careful mode off: normal moves again.")


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
