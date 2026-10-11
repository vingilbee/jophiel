"""Columbina's view of, and say over, the user's game (plan §8.4, §21; stage D2a).

Two halves, both scoped to one user:

- ``describe``: the game summary rendered into each of her turns. Trusted
  facts come from the session service (state, plan, pause reasons). Her
  planning lessons and her recap of the last session (game learning) are
  here too, because this is where she sets goals. What the
  driver read off the screen (notes, facts, reports) is labelled untrusted
  game content, with ages.
- The operations behind her game tools: start with a goal, replace or extend
  the plan, tell her hands how to play, pause, resume, end. They act only on the user's own environment,
  chosen here (never named by the model), and never touch the capture
  switch: letting her see the screen stays the user's decision.
"""
from __future__ import annotations

import io
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from PIL import Image

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession, DesktopSessionEvent
from app.schemas.desktop import PlanAppend, PlanIn, PlanReplace, PlanStepIn, SessionCreate
from app.services import desktop_commands, desktop_preview, game_learning, game_pack
from app.services import desktop_sessions as sessions
from app.services.desktop_sessions import DesktopError

# How long an ended session still shows in the summary ("we just stopped").
RECENT_END_SECONDS = 15 * 60
# Her own planning turn may not set again a goal she finished this recently: an
# ongoing goal written with a done_when true on arrival ("within 2 blocks of the
# user") finished in seconds, woke her, and she set it again, five times in 35 s.
REPEAT_GOAL_SECONDS = 60

# The same goal set this often in this many minutes is shown to her as a loop
# (last session: "gather wood" five times in four minutes).
GOAL_REPEATS_SHOWN = 3
GOAL_REPEAT_MINUTES = 5
# Her skill manual comes from the pack; it only changes when the pack restarts.
MANUAL_TTL_SECONDS = 300
_manual_cache: dict = {"at": 0.0, "lines": []}

# What her own Minecraft player's skills do, in words (the pack's manifest is the truth).
PACK_SKILLS = ("go somewhere, mine, fight, explore, craft, place a block, eat, sleep, follow, store, take, "
               "smelt, give, pick up items, look at something")

# Written to Columbina: she is the one playing. The fast model that presses the
# keys is her hands, never someone she talks about.
REASONS = {
    "user_pause": "the user paused it", "user_takeover": "the user took over the controls",
    "user_end": "the user ended it", "plan_complete": "you finished everything you set out to do",
    "driver_stuck": "you got stuck", "needs_human": "you need the user (something only they can give or do)",
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
    """What a turn needs: the summary text, whether the game tools apply, a
    one-line "what you're doing right now" for her sense of the moment, and
    whether a key-pressing game VM is involved (its own prompt rules)."""

    summary: str
    tools: bool
    activity: str | None = None
    vm: bool = False


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
            f"things your skills can do ({PACK_SKILLS}; nothing else, e.g. no pillaring up or building a "
            "whole structure in one go), each done_when "
            "something the game can confirm "
            "(e.g. \"gained 5 oak_log\", \"a crafting table placed\"). An amount to get counts from the "
            "step's start (\"gained 5 oak_log\"), not what you already hold; write \"have 5 oak_log\" only "
            "when you mean the total. For an ongoing request like "
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


def _skill_manual(env: DesktopEnvironment) -> list[str]:
    """Every skill her hands have, from the pack's own manifest: so she can tell "no skill
    does this" (ask the user) from "I did it badly" (try again)."""
    if env.status != "online":
        return []
    now = time.time()
    if now - _manual_cache["at"] > MANUAL_TTL_SECONDS or not _manual_cache["lines"]:
        from app.ai.desktop.pack_client import PackClient, PackError

        try:
            manifest = PackClient.from_settings().manifest()
        except PackError:
            return []
        lines = ["Your hands' skills (all they can do, one at a time):"]
        for skill in manifest:
            params = ", ".join(name for name in skill.get("params", {}) if name != "time_limit")
            lines.append(f"- {skill['name']}({params}): {skill.get('description', '')} "
                         f"Stops when: {skill.get('stops_when', '')}.")
        lines.append("Background modes (set_my_modes), beside following, walking, exploring and mining: "
                     "guard (fight hostiles close to you or the player you're with), gather (mine listed blocks "
                     "close by on the way, e.g. iron_ore), pickup (grab drops close by). They're the only way "
                     "to do two things at once; which are on is in \"Background modes\" below.")
        _manual_cache.update(at=now, lines=lines)
    return list(_manual_cache["lines"])


def _times_set(db: Session, session: DesktopSession, goal: str) -> int:
    since = datetime.now(timezone.utc) - timedelta(minutes=GOAL_REPEAT_MINUTES)
    goals = db.scalars(
        select(DesktopSessionEvent.payload["goal"].astext)
        .where(DesktopSessionEvent.session_id == session.id, DesktopSessionEvent.type == "desktop.plan_set",
               DesktopSessionEvent.occurred_at >= since))
    return sum(1 for each in goals if each and _same_goal(each, goal))


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
    pack = game_pack.is_pack(env)
    vm = any(not game_pack.is_pack(e) for e in envs)
    now = time.time()
    lines, games = _environment_lines(db, user_id, envs)
    session = _recent_session(db, user_id, env, now)
    if session is None:
        lines.append("You are not playing right now.")
        db.commit()
        return GameContext("\n".join(lines), tools=True, vm=vm)
    lines += _session_lines(session, pack, now)
    lines += _plan_lines(db, session)
    driver = desktop_commands.load_driver_state(session.id) or {}
    lines += _commitment_lines(session, now)
    if pack:
        lines += _pack_details(env, session, driver, now)
        db.commit()
        return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, [], pack=True), vm=vm)
    vm_lines, facts = _vm_details(session, driver, now)
    lines += vm_lines
    db.commit()
    return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, facts), vm=vm)


def _environment_lines(db: Session, user_id: uuid.UUID,
                       envs: list[DesktopEnvironment]) -> tuple[list[str], list[str]]:
    """Her game environments (her own Minecraft player, game VMs) and the games
    she can play, plus what she learned playing."""
    lines = []
    games: list[str] = []
    has_pack = any(game_pack.is_pack(e) for e in envs)
    retired = set(game_pack.REPLACED_BY) if has_pack else set()
    for each in envs:
        if game_pack.is_pack(each):
            lines.append(_pack_line(each))
            who = _who_is_who(each)
            if who:
                lines.append(who)
            lines.extend(_skill_manual(each))
            games.append(f"Minecraft [{game_pack.PACK_PROFILE_ID}]")
        else:
            line, vm_games = _vm_line(each, retired=retired)
            lines.append(line)
            games.extend(vm_games)
    if has_pack:
        lines.extend(_learned_lines(db, user_id, game_learning.game_for_profile(game_pack.PACK_PROFILE_ID)))
    return lines, games


def _recent_session(db: Session, user_id: uuid.UUID, env: DesktopEnvironment, now: float) -> DesktopSession | None:
    """The session to describe: the latest one, unless it ended a while ago."""
    session = _latest_session(db, user_id, env.id)
    if session is not None and session.state in DESKTOP_TERMINAL_STATES:
        ended_ago = now - session.ended_at.timestamp() if session.ended_at else RECENT_END_SECONDS + 1
        if ended_ago > RECENT_END_SECONDS:
            return None
    return session


def _session_lines(session: DesktopSession, pack: bool, now: float) -> list[str]:
    """Whether she's playing, since when, why not, and what happens after a goal."""
    state = {
        "starting": ("getting ready (joining the world)" if pack
                     else "getting ready (waiting for the game window in the VM)"),
        "running": "you are playing" if session.plan else "you have the controls but no goal yet",
        "paused": "paused", "manual": "the user has the controls",
        "disconnected": "disconnected from the world" if pack else "disconnected from the VM",
        "ended": "stopped", "failed": "stopped with an error",
    }[session.state]
    game_name = "Minecraft (your own player), " if pack else ""
    lines = [f"Game: {game_name}{state}; started {_ago(now - session.started_at.timestamp())}."]
    if session.reason_code and session.state != "running":
        lines.append(f"Why: {REASONS.get(session.reason_code, session.reason_code)}.")
    if session.state not in DESKTOP_TERMINAL_STATES:
        lines.append("After each goal: " + ("you pick the next one yourself (keep playing is on)."
                                            if session.autonomy == "continue" else
                                            "you stop and tell the user (keep playing is off)."))
    return lines


def _plan_lines(db: Session, session: DesktopSession) -> list[str]:
    """Her goal, its rules, and where she is in its steps."""
    if not session.plan:
        return []
    steps = session.plan["steps"]
    done = [st for st in steps if st["state"] == "done"]
    active = next((st for st in steps if st["state"] == "active"), None) or next(
        (st for st in steps if st["state"] == "pending"), None
    )
    lines = [f"Your goal: {session.plan['goal']}."]
    repeats = _times_set(db, session, session.plan["goal"])
    if repeats >= GOAL_REPEATS_SHOWN:
        lines.append(f"You've set this same goal {repeats} times in the last {GOAL_REPEAT_MINUTES} minutes: "
                     "if it isn't working, don't set it again; work out what's missing, or ask the user.")
    if session.plan.get("constraints"):
        # Marked as known: the 2026-10-11 cave session's goal carried "without sleeping,
        # avoiding hostiles", and nearly every reply said it back.
        lines.append("Rules you gave your hands for it (the user knows them; don't repeat them back): "
                     + "; ".join(session.plan["constraints"]) + ".")
    lines.append(
        f"Steps done: {len(done)} of {len(steps)}."
        + (f" Now: {active['do']} (done when: {active['done_when']})." if active else " None left.")
    )
    return lines


def _commitment_lines(session: DesktopSession, now: float) -> list[str]:
    """What she promised the user and what she told her hands, while the session lives."""
    if session.state in DESKTOP_TERMINAL_STATES:
        return []
    lines = []
    promised = desktop_commands.promises(session.id)
    if promised:
        lines.append("What you promised the user (keep it, or drop it with drop_promise when it's done or moot): "
                     + "; ".join(f'{i}. "{p["text"]}" ({_ago(now - p["at"])})' for i, p in enumerate(promised, 1))
                     + ".")
    told = desktop_commands.hands_messages(session.id)
    if told:
        lines.append("You told your hands (they follow it every move): "
                     + "; ".join(f'"{m["text"]}" ({_ago(now - m.get("at", now))})' for m in told) + ".")
    return lines


def _vm_details(session: DesktopSession, driver: dict, now: float) -> tuple[list[str], list]:
    """The key-pressing VM driver's state: careful mode, counted moves, and what it
    read off the screen (untrusted). Also returns the facts, for her activity line."""
    lines = []
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
    return lines, facts


def _learned_lines(db: Session, user_id: uuid.UUID, game: str | None) -> list[str]:
    """What she learned from playing, for planning: plan lessons and her recap
    of the last session (both written by her own reflection, from code-recorded
    outcomes)."""
    if game is None:
        return []
    lines = []
    lessons = game_learning.plan_lessons(db, user_id, game)
    if lessons:
        lines.append("What you learned playing Minecraft (use it when you set goals and steps; a rule your hands "
                     "must keep the whole time goes in the plan's constraints):")
        lines += [f"- When {lesson.situation}: {lesson.advice}"
                  + (" (tentative: seen once)" if lesson.status == "trial" else "") for lesson in lessons]
    skill = game_learning.skill_lessons(db, user_id, game)
    if skill:
        lines.append("How your moves behave (what your hands learned):")
        lines += [f"- When {lesson.situation}: {lesson.advice}"
                  + (" (tentative: seen once)" if lesson.status == "trial" else "") for lesson in skill]
    gaps = game_learning.known_gaps(db, user_id, game)
    if gaps:
        lines.append("What you know you can't do yet (you asked for it to be built; not done): "
                     + "; ".join(gap.title for gap in gaps)
                     + ". If the user asks for one of these, say so and ask for help instead of trying.")
    episode = game_learning.latest_episode(db, user_id, game)
    if episode is not None:
        lines.append(f"Last session (your recap, {_ago(time.time() - episode.event_at.timestamp())}): "
                     f"{episode.summary}")
    return lines


def _pack_details(env: DesktopEnvironment, session: DesktopSession, driver: dict, now: float) -> list[str]:
    """What her hands are doing and what the game says, for a pack session.
    The game state comes from the game itself (counted by code); notes are her
    hands' own words."""
    lines = []
    running = driver.get("running")
    if running and session.state == "running":
        params = ", ".join(f"{k}={v}" for k, v in (running.get("params") or {}).items() if k != "time_limit")
        lines.append(f"Your hands are doing: {running['skill']}({params}).")
    step = driver.get("step") or {}
    current = session.current_step_id or next(
        (st["id"] for st in (session.plan or {}).get("steps", []) if st["state"] in ("active", "pending")), None)
    if step.get("id") and step["id"] == current and session.state not in DESKTOP_TERMINAL_STATES \
            and step.get("tries"):
        line = (f"This step so far (counted): {_ago(now - step['since']).removesuffix(' ago')}, "
                f"{step['tries']} tries, {step['failed']} failed")
        if step.get("failures"):
            from app.ai.desktop.skill_driver import failure_summary

            line += f": {failure_summary(step['failures'])}"
        lines.append(line + ".")
    outcomes = (driver.get("outcomes") or [])[-4:]
    if outcomes:
        lines.append("Recent results: " + "; ".join(
            f"{o['skill']} -> {o['status']}{': ' + o['reason'] if o.get('reason') else ''}"
            f" ({_ago(now - o.get('at', now))})" for o in outcomes) + ".")
    did = driver.get("modes_did") or []
    if did:
        lines.append("What your background modes did lately: "
                     + "; ".join(f"{d['text']} ({_ago(now - d.get('at', now))})" for d in did) + ".")
    if driver.get("notes"):
        lines.append(f'Your hands\' note: "{driver["notes"]}".')
    if env.status == "online":
        try:
            from app.ai.desktop.pack_client import PackClient, PackError

            state = PackClient.from_settings().state()
            lines.append("Your player right now (from the game): " + " ".join(state.get("text", "").split("\n"))
                         + " (Your hands switch to the best tool by themselves each time they mine or fight.)")
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


def playing_minecraft(db: Session, user_id: uuid.UUID) -> bool:
    """Whether the user has a live Minecraft session (any environment): speech
    is then transcribed with game words in mind."""
    if not get_settings().desktop_agent_enabled:
        return False
    profiles = db.scalars(
        select(DesktopSession.profile_id)
        .where(DesktopSession.owner_user_id == user_id,
               DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))
    ).all()
    return any("minecraft" in (p or "") for p in profiles)


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


def _plan(goal: str, steps: list[dict] | None, constraints: list[str] | None = None) -> PlanIn:
    items = [PlanStepIn(**step) for step in (steps or [])] or [
        PlanStepIn(do=goal[:120], done_when=f"{goal} is done"[:120])
    ]
    rules = [rule.strip()[:120] for rule in (constraints or []) if rule.strip()][:5]
    return PlanIn(goal=goal[:200], steps=items, constraints=rules)


def _live(db: Session, user_id: uuid.UUID) -> DesktopSession:
    env = _usable_environment(db, user_id)
    session = _latest_session(db, user_id, env.id) if env else None
    if session is None or session.state in DESKTOP_TERMINAL_STATES:
        raise DesktopError("no_session", 409, "you are not playing right now; start first")
    return session


def start(db: Session, user_id: uuid.UUID, *, goal: str, steps: list[dict] | None = None,
          game: str | None = None, constraints: list[str] | None = None) -> str:
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
        return set_plan(db, user_id, goal=goal, steps=steps, constraints=constraints) + _blockers(env)
    session, _ = sessions.start_session(db, user_id, SessionCreate(
        environment_id=env.id, profile_id=profile_id, plan=_plan(goal, steps, constraints),
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
             source: str = "columbina_user_turn", constraints: list[str] | None = None,
             since_revision: int | None = None) -> str:
    """``since_revision`` (her background turns): the plan she woke up to. If the user has
    changed it since, this is refused: she decided from what's now out of date."""
    session = _live(db, user_id)
    _refuse_if_user_replanned(db, session, since_revision)
    if source == "columbina_planning_turn":
        limit = get_settings().desktop_max_auto_goals
        if sessions.auto_goals_set(db, session.id) >= limit:
            raise DesktopError("limit_reached", 409, f"you've picked {limit} goals yourself since the user last joined in; "
                               "pause and tell the user instead of starting another")
        if _just_finished(db, session, goal):
            raise DesktopError(
                "goal_just_done", 409,
                "you finished exactly this goal moments ago. If it's meant to keep going (staying with the "
                "user, following, keeping watch), set it once more with an ongoing last step, e.g. \"stay with "
                "the user (follow_player)\", done when \"the user says to stop\". Otherwise pick a different "
                "goal, or pause and tell the user.")
    plan = _plan(goal, steps, constraints)
    if _unchanged(session, plan):
        # Set again word for word (seen live: "OK.", "Now?" and "Hello?" each re-set the same
        # goal): replacing it would cancel her move and drop her tips for nothing.
        return "You're already on exactly that; nothing changed." + _unpause(db, user_id, source)
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
    return f"Done; you're now on: {plan.steps[0].do}.{note}" + _unpause(db, user_id, source)


def _refuse_if_user_replanned(db: Session, session: DesktopSession, since_revision: int | None) -> None:
    """Seen live: the user said "go to the cave" while a background turn about her bed plan
    was thinking, and a second later it set "sleep in my bed" again. The user's plan wins."""
    if since_revision is None or session.plan_revision == since_revision:
        return
    newest = db.scalars(
        select(DesktopSessionEvent.payload)
        .where(DesktopSessionEvent.session_id == session.id, DesktopSessionEvent.type == "desktop.plan_set")
        .order_by(DesktopSessionEvent.id.desc()).limit(session.plan_revision - since_revision)
    ).all()
    by_user = next((p for p in newest if p.get("source") != "columbina_planning_turn"
                    and p.get("plan_revision", 0) > since_revision), None)
    if by_user is not None:
        raise DesktopError(
            "user_replanned", 409,
            f"while you were thinking, the user changed your plan to \"{by_user.get('goal', '')}\". "
            "That's what they want now, so leave it as it is")


def _unpause(db: Session, user_id: uuid.UUID, source: str) -> str:
    """A new goal doesn't move a paused session. After sleeping finished her
    plan she set "gather logs" twice, was told "Done; you're now on: ...", and
    said she'd started while standing still. On the user's turn her own pause
    (plan done, stuck) resumes with the new goal; any other pause is said plainly."""
    session = _live(db, user_id)
    if session.state not in ("paused", "manual", "disconnected"):
        return ""
    if source == "columbina_user_turn" and session.state == "paused" and session.reason_code in SELF_RESUMABLE:
        try:
            sessions.resume_session(db, user_id, session.id, expected_state_revision=session.state_revision,
                                    reason="columbina_resume")
            return " You'd paused after your last goal; you're playing again."
        except DesktopError:
            db.rollback()
            session = _live(db, user_id)
    why = REASONS.get(session.reason_code or "", "")
    hint = (" Call resume_game if the user wants you to do it now." if session.state == "paused"
            else "")
    return (" But you're still paused" + (f" ({why})" if why else "")
            + ": you're not moving, so don't say you've started." + hint)


def _same_goal(a: str, b: str) -> bool:
    return _words(a) == _words(b)


def _unchanged(session: DesktopSession, plan: PlanIn) -> bool:
    """The live plan already is this goal, these open steps and these rules."""
    current = session.plan or {}
    if not current or not _same_goal(current.get("goal", ""), plan.goal):
        return False
    open_steps = [st for st in current.get("steps", []) if st["state"] in ("pending", "active")]
    if len(open_steps) != len(plan.steps):
        return False
    if not all(_same_goal(old["do"], new.do) and _same_goal(old["done_when"], new.done_when)
               for old, new in zip(open_steps, plan.steps)):
        return False
    return [_words(rule) for rule in current.get("constraints") or []] == [_words(rule) for rule in plan.constraints]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _just_finished(db: Session, session: DesktopSession, goal: str) -> bool:
    """The current plan is this goal, every step is done, and that was under
    REPEAT_GOAL_SECONDS ago."""
    plan = session.plan or {}
    if not plan or not _same_goal(plan.get("goal", ""), goal):
        return False
    if any(step["state"] in ("pending", "active") for step in plan.get("steps", [])):
        return False
    finished = db.scalar(
        select(DesktopSessionEvent.occurred_at)
        .where(DesktopSessionEvent.session_id == session.id, DesktopSessionEvent.type == "desktop.steps_exhausted")
        .order_by(DesktopSessionEvent.id.desc()).limit(1))
    return finished is not None and datetime.now(timezone.utc) - finished < timedelta(seconds=REPEAT_GOAL_SECONDS)


def add_steps(db: Session, user_id: uuid.UUID, *, steps: list[dict], since_revision: int | None = None) -> str:
    session = _live(db, user_id)
    _refuse_if_user_replanned(db, session, since_revision)
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
    reply = ("Your hands will follow this from the next move. Now in force: "
             + "; ".join(f'"{m["text"]}"' for m in told) + ".")
    if game_pack.is_pack_profile(session.profile_id):
        # Told "jump and place a block below you", she passed it on and said she was
        # pillaring up: her hands have no skill for that, so nothing happened.
        reply += (f" But your hands can only use their skills ({PACK_SKILLS}): if none of them does what "
                  "this asks, it won't happen, so tell the user you can't do that yet instead of saying you are.")
    return reply


def set_modes(db: Session, user_id: uuid.UUID, *, guard: bool | None = None, gather: list[str] | None = None,
              pickup: bool | None = None) -> str:
    """Background modes beside whatever skill her hands run (her own Minecraft player only):
    guard, gather on the way, pick up drops. Changing them never touches the plan."""
    session = _live(db, user_id)
    db.commit()
    if not game_pack.is_pack_profile(session.profile_id):
        raise DesktopError("not_supported", 409, "background modes only work with your own Minecraft player")
    from app.ai.desktop.pack_client import PackClient, PackError

    change: dict = {}
    if guard is not None:
        change["guard"] = guard
    if pickup is not None:
        change["pickup"] = pickup
    if gather is not None:
        change["gather"] = [re.sub(r"[^a-z0-9_]", "", "_".join(name.lower().split()))[:64] for name in gather][:8]
    try:
        modes = PackClient.from_settings().set_modes(change) if change else PackClient.from_settings().modes()
    except PackError as exc:
        if exc.status == 400:
            raise DesktopError("bad_modes", 400, f"{exc} (use Minecraft block ids, e.g. iron_ore)") from exc
        raise DesktopError("unavailable", 503, f"you couldn't change them just now ({exc})") from exc
    return "Background modes now: " + modes_text(modes) + "."


def modes_text(modes: dict) -> str:
    parts = []
    if modes.get("guard"):
        parts.append("guard on")
    if modes.get("gather"):
        parts.append("gathering " + ", ".join(modes["gather"]) + " on the way")
    if modes.get("pickup"):
        parts.append("picking up drops")
    return "; ".join(parts) if parts else "all off"


def how_to_make(db: Session, user_id: uuid.UUID, *, item: str) -> str:
    """What an item takes, against what she carries (her own Minecraft player only)."""
    session = _live(db, user_id)
    db.commit()
    if not game_pack.is_pack_profile(session.profile_id):
        raise DesktopError("not_supported", 409, "you can only look up recipes with your own Minecraft player")
    from app.ai.desktop.pack_client import PackClient, PackError

    name = re.sub(r"[^a-z0-9_]", "", "_".join(item.lower().split()))[:64]
    if not name:
        raise DesktopError("bad_item", 400, "give a Minecraft item id, e.g. white_bed")
    try:
        found = PackClient.from_settings().recipe(name)
    except PackError as exc:
        raise DesktopError("unavailable", 503, f"you couldn't look it up just now ({exc})") from exc
    return recipe_text(found)


def recipe_text(found: dict) -> str:
    name = found.get("item", "?")
    if not found.get("known"):
        close = found.get("did_you_mean") or []
        return (f"There's no item called {name}." + (f" Close names: {', '.join(close)}." if close else
                " Nothing close either: it may be a structure or place, not an item (you can't make it)."))
    if not found.get("craftable"):
        return (f"{name} can't be crafted: it's mined, found, smelted or dropped by mobs. "
                f"You have {found.get('have', 0)}.")
    parts = [f"{name}: you have {found.get('have', 0)}."]
    for i, recipe in enumerate(found.get("recipes", []), start=1):
        needs = ", ".join(f"{n} {k}" for k, n in recipe["needs"].items())
        missing = recipe.get("missing") or {}
        line = (f"Recipe {i}: {needs} -> {recipe.get('makes', 1)}"
                + (" (needs a crafting table)" if recipe.get("needs_crafting_table") else ""))
        line += (f"; you're missing {', '.join(f'{n} {k}' for k, n in missing.items())}" if missing
                 else "; you have all of it")
        parts.append(line + ".")
    if found.get("variants", 0) > len(found.get("recipes", [])):
        parts.append(f"({found['variants']} variants in all, e.g. other wood or colours; shown: the closest to what "
                     "you have.)")
    if any(r.get("needs_crafting_table") for r in found.get("recipes", [])):
        parts.append("Crafting table: " + ("one is placed nearby." if found.get("crafting_table_nearby") else
                                           "you carry one but must place it first." if found.get(
                                               "crafting_table_carried") else "none nearby or carried."))
    return " ".join(parts)


def note_promise(db: Session, user_id: uuid.UUID, *, promise: str) -> str:
    session = _live(db, user_id)
    db.commit()
    kept = desktop_commands.add_promise(session.id, promise)
    if kept is None:
        raise DesktopError("unavailable", 503, "promises can't be kept right now (no Redis)")
    return "Noted; you'll see it each turn until you drop it. Now: " + "; ".join(
        f'{i}. "{p["text"]}"' for i, p in enumerate(kept, 1)) + "."


def drop_promise(db: Session, user_id: uuid.UUID, *, number: int | None = None) -> str:
    session = _live(db, user_id)
    db.commit()
    kept = desktop_commands.drop_promise(session.id, number)
    if kept is None:
        raise DesktopError("unavailable", 503, "promises can't be changed right now (no Redis)")
    return ("Dropped. Still promised: " + "; ".join(f'{i}. "{p["text"]}"' for i, p in enumerate(kept, 1)) + "."
            if kept else "Dropped; nothing promised now.")


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
