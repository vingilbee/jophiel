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
from app.db.models import (DESKTOP_TERMINAL_STATES, ChatMessage, Conversation, DesktopEnvironment, DesktopSession,
                           DesktopSessionEvent, GameExperience)
from app.ai.desktop import step_check
from app.schemas.desktop import PlanAppend, PlanIn, PlanReplace, PlanStepIn, SessionCreate
from app.services import (agent_events, desktop_commands, desktop_preview, game_learning, game_moment,
                          game_pack, game_planning, game_players)
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
# "What happened lately" in her summary: this far back, at most this many lines.
LATELY_SECONDS = 5 * 60
LATELY_LINES = 14
# Her hands' moves that finished as asked aren't news (the timeline would be all go_to).
QUIET_SKILLS = {"go_to", "follow_player", "explore", "look_at"}
# Her skill manual comes from the pack; it only changes when the pack restarts.
MANUAL_TTL_SECONDS = 300
_manual_cache: dict = {"at": 0.0, "lines": []}

# What her own Minecraft player's skills do, in words (the pack's manifest is the truth).
PACK_SKILLS = ("go somewhere, mine, fight, explore, craft, place a block, eat, sleep, follow, store, take, "
               "smelt, give, pick up items, look at something")

# Written to Columbina: she is the one playing. The fast model that presses the
# keys is her hands, never someone she talks about.
REASONS = {
    "user_pause": "the user paused it", "columbina_pause": "you paused it yourself", "user_takeover": "the user took over the controls",
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
    "goal_suspended": "the goal you were playing for was put on hold",
    "goal_completed": "the goal you were playing for is done", "goal_abandoned": "the goal you were playing for was dropped",
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
    # Her planner writes the plan (services/game_planning): replies set goals, not steps.
    planner: bool = False
    # The game as this summary saw it, so what changed while she wrote can be told (game_moment).
    moment: game_moment.Moment | None = None


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


PACK_GAME = "minecraft"  # game_learning.GAME_BY_PROFILE[game_pack.PACK_PROFILE_ID]


def _who_is_who(db: Session, user_id: uuid.UUID, env: DesktopEnvironment) -> str | None:
    """Her in-game name and the other players', so "follow me" never means herself, and which
    one is the user: linked in the database when they've said so, else a guess."""
    if env.status != "online":
        return None
    from app.ai.desktop.pack_client import PackClient, PackError

    try:
        state = PackClient.from_settings().state()
    except PackError:
        return None
    others = state.get("players_online") or []
    you = state.get("username", "?")
    name = game_players.user_name(db, user_id) or "the user"
    player = game_players.user_player(db, user_id, PACK_GAME)
    if player:
        where = "online" if player in others else "not online right now"
        user_hint = f' {name} plays as "{player}" ({where}): when they say "me", that\'s them.'
    elif len(others) == 1:
        user_hint = (f' {name} is probably "{others[0]}" (the only other player; not confirmed: when they say '
                     f'which player is them, save it with this_is_me).')
    elif others:
        user_hint = f" {name} is one of them (ask which, then save it with this_is_me)."
    else:
        user_hint = ""
    if not others:
        return f'In Minecraft you are "{you}". No one else is in the world right now.{user_hint}'
    return (f'In Minecraft you are "{you}" (never target yourself). Other players: '
            + ", ".join(f'"{n}"' for n in others) + f".{user_hint}")


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
        lines.append("Before learning a move (or offering to): if you can name the skill above that does it, with "
                     "its parameters, plan that skill instead. If you can't, learn it: a step no skill does just "
                     "gets stuck again.")
        _manual_cache.update(at=now, lines=lines)
    return list(_manual_cache["lines"])


def _times_set(db: Session, session: DesktopSession, goal: str) -> int:
    since = datetime.now(timezone.utc) - timedelta(minutes=GOAL_REPEAT_MINUTES)
    goals = db.scalars(
        select(DesktopSessionEvent.payload["goal"].astext)
        .where(DesktopSessionEvent.session_id == session.id, DesktopSessionEvent.type == "desktop.plan_set",
               DesktopSessionEvent.occurred_at >= since))
    return sum(1 for each in goals if each and _same_goal(each, goal))


def describe(db: Session, user_id: uuid.UUID, *, for_reply: bool = False) -> GameContext | None:
    """The game summary for one of Columbina's turns, or None when the user has
    no game environment (then she gets neither the summary nor the tools).
    ``for_reply``: a reply to the user, which takes her open question (to ask it)."""
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
    planner = pack and game_planning.handles(session)
    if planner and session.state not in DESKTOP_TERMINAL_STATES:
        working_out = game_planning.planning_now(db, session.id)
        if working_out:
            lines.append(f"You're working out {working_out}: your plan follows in a few seconds. Until it does, "
                         "say you're figuring it out, not what your steps are.")
    driver = desktop_commands.load_driver_state(session.id) or {}
    lines += _commitment_lines(session, now)
    if pack:
        from app.services import game_challenges

        if session.state not in DESKTOP_TERMINAL_STATES:
            lines += game_challenges.summary_lines(db, session)
        details, state = _pack_details(db, env, session, driver, now, for_reply=for_reply)
        lines += details
        moment = game_moment.capture(db, session, state, at=now)
        db.commit()
        return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, [], pack=True), vm=vm,
                           planner=planner, moment=moment)
    vm_lines, facts = _vm_details(session, driver, now)
    lines += vm_lines
    moment = game_moment.capture(db, session, None, at=now)
    db.commit()
    return GameContext("\n".join(lines), tools=True, activity=_activity(session, games, facts), vm=vm,
                       moment=moment)


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
            who = _who_is_who(db, user_id, each)
            if who:
                lines.append(who)
            lines.extend(_skill_manual(each))
            from app.services import game_skills

            lines.extend(game_skills.summary_lines(db, user_id, PACK_GAME))
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
        from app.services import game_challenges

        lines.append("After each goal: " + ("you pick your next challenge yourself (exploring is on)."
                                            if game_challenges.exploring(session) else
                                            "you pick the next one yourself (keep playing is on)."
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
    from app.services import memory_manager

    goal_line = memory_manager.goal_line(db, session)
    if goal_line:
        lines = [goal_line, f"Your current plan for it: {session.plan['goal']}."]
    else:
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
        + (f" Now: {active['do']} (done when: {active['done_when']}"
           + (f"; counted: {step_check.describe(active['check'])}" if active.get("check") else "") + ")."
           if active else " None left.")
    )
    return lines


def _commitment_lines(session: DesktopSession, now: float) -> list[str]:
    """What she told her hands, while the session lives (her promises are in her working memory)."""
    if session.state in DESKTOP_TERMINAL_STATES:
        return []
    lines = []
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


def _pack_details(db: Session, env: DesktopEnvironment, session: DesktopSession, driver: dict,
                  now: float, *, for_reply: bool = False) -> tuple[list[str], dict | None]:
    """What her hands are doing and what the game says, for a pack session, and the
    game's state (None if unreachable). The game state comes from the game itself
    (counted by code); notes are her hands' own words."""
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
    lines += _mind_lines(db, session, now, for_reply=for_reply)
    from app.services import game_practice

    lines += game_practice.summary_lines(db, session, now)
    from app.services import learning_trials

    lines += learning_trials.summary_lines(db, session)
    lately = _lately_lines(db, session, now)
    if lately:
        lines += lately
    else:  # nothing recorded (game learning off): her hands' own short lists
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
    state = None
    if env.status == "online":
        try:
            from app.ai.desktop.pack_client import PackClient, PackError

            state = PackClient.from_settings().state()
            lines.append("Your player right now (from the game): " + " ".join(state.get("text", "").split("\n"))
                         + " (Your hands switch to the best tool by themselves each time they mine or fight; "
                         "to hold something else, or arrange the hotbar, your hands have hold and put_in_hotbar.)")
        except PackError:
            state = None
    return lines, state


def planning_facts(db: Session, session: DesktopSession) -> dict[str, list[str]]:
    """What her planner reads (services/game_planning), by section, from the same sources as
    her summary: what is happening (her hands, her player, lately, her thoughts), her skills,
    how steps are written, what she learned, and who is who."""
    from app.services import game_skills

    env = db.get(DesktopEnvironment, session.environment_id)
    now = time.time()
    driver = desktop_commands.load_driver_state(session.id) or {}
    who = _who_is_who(db, session.owner_user_id, env)
    return {
        "now": _pack_details(db, env, session, driver, now)[0],
        "skills": _skill_manual(env) + game_skills.summary_lines(db, session.owner_user_id, PACK_GAME),
        "steps": [_pack_line(env)],
        "learned": _learned_lines(db, session.owner_user_id, game_learning.game_for_profile(session.profile_id)),
        "who": [who] if who else [],
    }


def _thought_line(t: dict, now: float) -> str:
    # A stale thought was written before the game moved on (or landed too late): it may be wrong now.
    notes = [f"thought over: a {t['conclusion']}"] if t.get("conclusion") else []
    if t.get("said"):
        notes.append(f'you said "{t["said"]}"')
    notes.append(_ago(now - t["at"]))
    if t["status"] == "stale":
        notes.append("out of date: the game changed after you thought it, so check it against now")
    return f'"{t["text"]}" ({"; ".join(notes)})'


def _mind_lines(db: Session, session: DesktopSession, now: float, *, for_reply: bool = False) -> list[str]:
    """Her own recent thoughts during play (services/mind.py), what she means to do and
    the question she wanted to ask (services/mind_hard.py), so a reply fits what she's
    been noticing and thinking over. Empty when her mind is off."""
    from app.services import mind, mind_hard, mind_minecraft

    if not mind_minecraft.enabled():
        return []
    at = datetime.fromtimestamp(now, timezone.utc)
    thoughts = [t for t in mind.recent_thoughts(db, session.id, at) if t["status"] != "unfinished"]
    lines = []
    if thoughts:
        lines.append("On your mind (your own thoughts while playing, newest last): "
                     + "; ".join(_thought_line(t, now) for t in thoughts) + ".")
    question = mind_hard.take_question(session.id) if for_reply else None
    if question:
        lines.append(f'A question you wanted to ask the user, from thinking it over: "{question["text"]}". '
                     "Ask it in this reply if it still fits, in your own words; otherwise let it go.")
    thinking = mind.thinking_about(session.id)
    if thinking:
        lines.append(f"You're still thinking over: {thinking}.")
    return lines


def _lately_text(experience: GameExperience) -> str | None:
    """One thing that happened, in plain words, or None when it isn't news."""
    p = experience.payload or {}
    if experience.kind == "notice":
        text = p.get("text") or ""
        if p.get("kind") == "mode":  # "gather: mined 2 iron_ore on the way" -> what she did
            text = text.split(": ", 1)[-1]
    elif experience.kind == "skill_outcome":
        skill, status = p.get("skill", "?"), p.get("status", "?")
        if skill in QUIET_SKILLS and status in ("done", "stopped"):
            return None
        if status == "stopped" and p.get("reason") == "the plan changed":
            return None
        collected = (p.get("data") or {}).get("collected")
        got = f" (got {', '.join(f'{k} x{v}' for k, v in collected.items())})" if isinstance(collected, dict) and collected else ""
        text = f"{skill} {status}{': ' + p['reason'] if p.get('reason') else ''}{got}"
    else:
        return None
    return text.replace("_", " ") if text else None


def _last_said_at(db: Session, user_id: uuid.UUID) -> datetime | None:
    """When she last said anything to the user (a reply or something she said on her own)."""
    at = db.scalar(
        select(ChatMessage.created_at).join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.role == "assistant", ChatMessage.content != "")
        .order_by(ChatMessage.created_at.desc()).limit(1))
    if at is not None and at.tzinfo is None:  # TimestampMixin columns are naive UTC
        at = at.replace(tzinfo=timezone.utc)
    return at


def _lately_lines(db: Session, session: DesktopSession, now: float) -> list[str]:
    """The last few minutes of her game, oldest first, from what code recorded
    (deaths and what she dropped, damage, mobs, what her modes did, failures),
    with a mark where she last spoke: what she can react to and talk about."""
    since = datetime.fromtimestamp(now - LATELY_SECONDS, timezone.utc)
    rows = db.scalars(
        select(GameExperience)
        .where(GameExperience.session_id == session.id, GameExperience.occurred_at >= since,
               GameExperience.occurred_at <= datetime.fromtimestamp(now, timezone.utc),
               GameExperience.kind != "lessons_applied")
        .order_by(GameExperience.id.desc()).limit(LATELY_LINES * 3)).all()[::-1]
    entries: list[list] = []  # [text, at, times]
    for row in rows:
        text = _lately_text(row)
        if not text:
            continue
        at = row.occurred_at.timestamp()
        if entries and entries[-1][0] == text:
            entries[-1][1], entries[-1][2] = at, entries[-1][2] + 1
        else:
            entries.append([text, at, 1])
    entries = entries[-LATELY_LINES:]
    if not entries:
        return []
    said = _last_said_at(db, session.owner_user_id)
    said_at = said.timestamp() if said is not None else None
    out = ["What happened in your game lately (from the game itself, oldest first):"]
    marked = False
    for text, at, times in entries:
        if not marked and said_at is not None and at > said_at:
            out.append(f"  -- you last spoke to the user here ({_ago(now - said_at)}); below is new to them --")
            marked = True
        out.append(f"  - {text}{f' (x{times})' if times > 1 else ''} ({_ago(now - at)})")
    if not marked:
        out.append("  -- you've spoken since all of this --")
    return out


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


def summary_for(user_id: uuid.UUID, *, for_reply: bool = False) -> GameContext | None:
    """``describe`` with its own DB session, for a turn being assembled (run in
    a thread). Never fails a turn: any error just means no game this turn."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            context = describe(db, user_id, for_reply=for_reply)
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


def _plan(goal: str, steps: list[dict] | None, constraints: list[str] | None = None,
          target: str | None = None) -> PlanIn:
    items = [PlanStepIn(**step) for step in (steps or [])] or [
        PlanStepIn(do=goal[:120], done_when=f"{goal} is done"[:120])
    ]
    rules = [rule.strip()[:120] for rule in (constraints or []) if rule.strip()][:5]
    return PlanIn(goal=goal[:200], steps=items, constraints=rules, target=_target(target))


def _target(text: str | None) -> str | None:
    """A game id from the model's words ("Iron Ore" -> iron_ore), or None."""
    name = re.sub(r"[^a-z0-9_]", "", "_".join((text or "").lower().split()))[:64]
    return name or None


def _live(db: Session, user_id: uuid.UUID) -> DesktopSession:
    env = _usable_environment(db, user_id)
    session = _latest_session(db, user_id, env.id) if env else None
    if session is None or session.state in DESKTOP_TERMINAL_STATES:
        raise DesktopError("no_session", 409, "you are not playing right now; start first")
    return session


def start(db: Session, user_id: uuid.UUID, *, goal: str, steps: list[dict] | None = None,
          game: str | None = None, constraints: list[str] | None = None, target: str | None = None,
          new_goal: bool = False, message_id: uuid.UUID | None = None) -> str:
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
        if game_planning.handles(existing) and len(steps or []) != 1:
            return set_goal(db, user_id, goal=goal, target=target, rules=constraints, new_goal=new_goal,
                            message_id=message_id) + _blockers(env)
        return set_plan(db, user_id, goal=goal, steps=steps, constraints=constraints, new_goal=new_goal,
                        message_id=message_id) + _blockers(env)
    session, _ = sessions.start_session(db, user_id, SessionCreate(
        environment_id=env.id, profile_id=profile_id, plan=_plan(goal, steps, constraints, target),
    ), plan_source="columbina_user_turn")
    if new_goal:
        _begin_goal(db, user_id, goal, message_id)
        db.commit()
    if game_planning.handles(session) and len(steps or []) != 1:
        # A one-step command ("follow me") needs no planning; anything else gets her planner's steps.
        game_planning.request(db, session, "goal: a new session", goal=_goal(goal, target, constraints))
        db.commit()
        agent_events.wake()
        return "Started; you're working out how to do it (your plan follows in a few seconds)." + _blockers(env)
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
             since_revision: int | None = None, challenge: bool = False, new_goal: bool = False,
             message_id: uuid.UUID | None = None) -> str:
    """``since_revision`` (her background turns): the plan she woke up to. If the user has
    changed it since, this is refused: she decided from what's now out of date.
    ``challenge``: this goal is a challenge she set herself while exploring (stage H3)."""
    from app.services import game_challenges

    session = _live(db, user_id)
    _refuse_if_user_replanned(db, session, since_revision)
    if source == "columbina_planning_turn" and not game_challenges.exploring(session):
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
    if new_goal:
        _begin_goal(db, user_id, goal, message_id, by_user=source == "columbina_user_turn")
        db.commit()
    if _unchanged(session, plan):
        # Set again word for word (seen live: "OK.", "Now?" and "Hello?" each re-set the same
        # goal): replacing it would cancel her move and drop her tips for nothing.
        return "You're already on exactly that; nothing changed." + _unpause(db, user_id, source)
    if source == "columbina_planning_turn" and _same_goal_still_open(session, plan):
        # Her own turn re-setting the goal she's on, steps reworded (seen live: "stay close to
        # tearDrop12" re-set after every bit of news, 80 plans in 25 min, each one clearing the
        # user's "careful" and tips to her hands). The user's own turns may still reshape it.
        return ("That's already your goal and it isn't finished; nothing changed. To line up more, "
                "add_game_steps; to change how you play it, tell_my_hands.")
    sessions.replace_plan(db, user_id, session.id, PlanReplace(
        expected_plan_revision=session.plan_revision, source=source, **plan.model_dump(),
    ))
    adopted = game_challenges.on_plan_set(db, session, source=source, goal=plan.goal, challenge=challenge)
    db.commit()
    stopped = _stop_own_lesson(db, user_id) if source == "columbina_user_turn" else ""
    note = _fresh_start(session)
    if adopted:
        note += f' This is your challenge now: "{adopted}".'
    return f"Done; you're now on: {plan.steps[0].do}.{note}{stopped}" + _unpause(db, user_id, source)


def _begin_goal(db: Session, user_id: uuid.UUID, text: str, message_id: uuid.UUID | None, *,
                by_user: bool = True) -> None:
    """A different goal (``new_goal``): kept in her working memory next to the old one, which stays open;
    the session plays for this one from now on."""
    from app.services import memory_manager

    session = db.scalar(select(DesktopSession).where(DesktopSession.id == _live(db, user_id).id).with_for_update())
    memory_manager.begin_goal(db, session, text, by_user=by_user,
                              source=memory_manager.Source("message", message_id) if message_id else None)


def _stop_own_lesson(db: Session, user_id: uuid.UUID) -> str:
    from app.services import game_practice

    return game_practice.stop_own(db, user_id)  # the user's goal comes before a lesson of her own


def _fresh_start(session: DesktopSession) -> str:
    """A new goal starts clean: tips to her hands and careful mode were about the old one
    (cave tips once followed her onto an open hillside). Returns a note saying what went."""
    cleared = []
    if desktop_commands.hands_messages(session.id):
        desktop_commands.send_hands_message(session.id, "", replace=True)
        cleared.append("your tips to your hands")
    if desktop_commands.careful_state(session.id):
        desktop_commands.set_careful(session.id, False)
        cleared.append("careful mode")
    return f" (Cleared {' and '.join(cleared)} from before.)" if cleared else ""


def _goal(text: str, target: str | None, rules: list[str] | None) -> dict:
    return {"text": " ".join(text.split())[:200], "target": _target(target),
            "rules": [" ".join(rule.split())[:120] for rule in (rules or []) if rule.strip()][:5]}


def set_goal(db: Session, user_id: uuid.UUID, *, goal: str, target: str | None = None,
             rules: list[str] | None = None, new_goal: bool = False, message_id: uuid.UUID | None = None) -> str:
    """What the user wants now, for her planner (services/game_planning) to plan: it writes
    the steps. Without the planner (not her own Minecraft player, or it's off), the goal is
    set as a one-step plan as before."""
    session = _live(db, user_id)
    if not game_planning.handles(session):
        return set_plan(db, user_id, goal=goal, constraints=rules, new_goal=new_goal, message_id=message_id)
    if new_goal:
        _begin_goal(db, user_id, goal, message_id)
    game_planning.request(db, session, "goal: the user asked", goal=_goal(goal, target, rules))
    db.commit()
    agent_events.wake()
    stopped = _stop_own_lesson(db, user_id)
    if session.state == "running":
        return ("Got it: you're working out how (your plan follows in a few seconds; your hands carry on "
                "with what they were doing until then). Say you're on it, not what your steps will be." + stopped)
    why = REASONS.get(session.reason_code or "", "")
    return ("Got it: you're working out how; your plan follows in a few seconds. You're paused right now"
            + (f" ({why})" if why else "") + ": not moving yet." + stopped)


def do_now(db: Session, user_id: uuid.UUID, *, do: str, done_when: str, rules: list[str] | None = None) -> str:
    """A one-step command, set at once with no planning (follow me, come here, go up the hill).
    A goal still waiting for her planner is out of date, so it's dropped."""
    session = _live(db, user_id)
    game_planning.cancel_waiting(db, session.id)
    db.commit()
    return set_plan(db, user_id, goal=do, steps=[{"do": do, "done_when": done_when}], constraints=rules)


def replace_from_planner(db: Session, user_id: uuid.UUID, *, read_revision: int, goal: str, steps: list[dict],
                         constraints: list[str], target: str | None, source: str, new_goal: bool) -> str:
    """Her planner's plan (services/game_planning), only if the plan is still the one it read.
    A goal she picked herself counts against her limit of own goals."""
    from app.services import game_challenges

    session = _live(db, user_id)
    if session.plan_revision != read_revision:
        raise DesktopError("stale_plan_revision", 409, "the plan changed while you were planning")
    if source == "columbina_planning_turn" and not game_challenges.exploring(session):
        limit = get_settings().desktop_max_auto_goals
        if sessions.auto_goals_set(db, session.id) >= limit:
            raise DesktopError("limit_reached", 409, f"you've picked {limit} goals yourself since the user last "
                               "joined in")
    plan = _plan(goal, steps, constraints, target)
    if new_goal:  # before the plan, so the plan links to the new goal, not the open old one
        _begin_goal(db, user_id, goal, None, by_user=source == "columbina_user_turn")
    sessions.replace_plan(db, user_id, session.id, PlanReplace(
        expected_plan_revision=read_revision, source=source, **plan.model_dump(),
    ))
    note = ""
    if new_goal:
        game_challenges.on_plan_set(db, session, source=source, goal=plan.goal, challenge=False)
        db.commit()
        note = _fresh_start(session)
    return f"set ({'new goal' if new_goal else 'revised'}): now on {plan.steps[0].do}{note}" + _unpause(
        db, user_id, source)


def rethink(db: Session, user_id: uuid.UUID, *, why: str) -> str:
    """A thought of hers concluded the plan may not fit: her planner looks at it again."""
    session = _live(db, user_id)
    if not game_planning.handles(session):
        raise DesktopError("not_supported", 409, "your plan is yours to change here: set_game_plan")
    game_planning.request(db, session, f"rethink: {' '.join(why.split())[:300]}")
    db.commit()
    agent_events.wake()
    return "Your mind will look at the plan again in a few seconds."


def unpause_after_planning(db: Session, user_id: uuid.UUID, source: str) -> str:
    """Her planner kept the plan: a pause she made or got stuck in is lifted (try again)."""
    return _unpause(db, user_id, source)


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
    by_user = next((p for p in newest if p.get("source") not in ("columbina_planning_turn", "columbina_mind")
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
    (plan done, stuck, paused herself) resumes with the new goal. On her own turn
    only a pause she made or got stuck in does: a finished plan with keep playing
    off stays stopped. The user's pause is said plainly."""
    session = _live(db, user_id)
    if session.state not in ("paused", "manual", "disconnected"):
        return ""
    own = ((*SELF_RESUMABLE, *GOAL_PAUSES) if source == "columbina_user_turn"
           else ("columbina_pause", "driver_stuck"))
    if session.state == "paused" and session.reason_code in own:
        try:
            sessions.resume_session(db, user_id, session.id, expected_state_revision=session.state_revision,
                                    reason="columbina_resume")
            return " You'd paused yourself; you're playing again."
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


def _same_goal_still_open(session: DesktopSession, plan: PlanIn) -> bool:
    current = session.plan or {}
    return bool(current) and _same_goal(current.get("goal", ""), plan.goal) and any(
        st["state"] in ("pending", "active") for st in current.get("steps", []))


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


def this_is_me(db: Session, user_id: uuid.UUID, *, player: str) -> str:
    """The user said which Minecraft player is them: save it for good."""
    env = next((e for e in _environments(db, user_id) if game_pack.is_pack(e)), None)
    if env is None:
        return "Not saved: you don't have your own Minecraft player."
    from app.ai.desktop.pack_client import PackClient, PackError

    try:
        if PackClient.from_settings().state().get("username", "").lower() == player.strip().lower():
            return f'Not saved: "{player}" is you, not the user.'
    except PackError:
        pass  # offline: she can't check her own name
    try:
        saved = game_players.link(db, user_id, PACK_GAME, player)
    except ValueError as exc:
        return f"Not saved: {exc}."
    db.commit()
    return f'Saved: the user is "{saved}" in Minecraft, from now on.'


def note_promise(db: Session, user_id: uuid.UUID, *, promise: str, message_id: uuid.UUID | None = None) -> str:
    session = _live(db, user_id)
    from app.services import memory_manager

    context = memory_manager.game_context(db, session)
    try:
        memory_manager.note_commitment(db, user_id, promise, owner="columbina", context_id=context.id,
                                       source=memory_manager.Source("message", message_id) if message_id else None)
    except memory_manager.MemoryError as exc:
        db.rollback()
        return f"Not noted: {exc.message}."
    db.commit()
    return "Noted in your memory; you'll see it until it's kept or dropped. " + _promise_list(db, user_id, session)


def drop_promise(db: Session, user_id: uuid.UUID, *, number: int | None = None, kept: bool = True,
                 message_id: uuid.UUID | None = None) -> str:
    session = _live(db, user_id)
    from app.services import memory_manager

    promised = _promises_in_memory(db, user_id, session)
    if not promised:
        return "Nothing promised now."
    if number is not None and not 1 <= number <= len(promised):
        raise DesktopError("not_found", 404, f"there's no promise {number}; there are {len(promised)}")
    source = memory_manager.Source("message", message_id) if message_id else None
    status = "fulfilled" if kept and source is not None else "cancelled"
    try:
        for entry in promised if number is None else [promised[number - 1]]:
            memory_manager.resolve(db, user_id, entry.id, status=status, source=source,
                                   reason="kept" if status == "fulfilled" else "dropped")
    except memory_manager.MemoryError as exc:
        raise DesktopError(exc.code, 409, exc.message) from None
    db.commit()
    return "Dropped. " + _promise_list(db, user_id, session)


def _promises_in_memory(db: Session, user_id: uuid.UUID, session: DesktopSession) -> list:
    from app.db.models import WorkingItem
    from app.services import memory_manager

    context = memory_manager.game_context(db, session)
    return list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.context_id == context.id, WorkingItem.kind == "commitment",
        WorkingItem.owner == "columbina", WorkingItem.closed_at.is_(None)).order_by(WorkingItem.created_at)))


def _promise_list(db: Session, user_id: uuid.UUID, session: DesktopSession) -> str:
    promised = _promises_in_memory(db, user_id, session)
    return ("Still promised: " + "; ".join(f'{i}. "{p.text}"' for i, p in enumerate(promised, 1)) + "."
            if promised else "Nothing promised now.")


def set_careful(db: Session, user_id: uuid.UUID, *, on: bool) -> str:
    session = _live(db, user_id)
    db.commit()
    if not desktop_commands.set_careful(session.id, on):
        raise DesktopError("unavailable", 503, "careful mode can't be set right now (no Redis)")
    return ("Careful mode on for the next few minutes: short steps with a fresh look between each, from your next move."
            if on else "Careful mode off: normal moves again.")


def pause(db: Session, user_id: uuid.UUID, *, user_asked: bool) -> str:
    """``user_asked``: the user wants the pause, and only they lift it. Otherwise
    it's hers. Before this (2026-10-15) every pause she made was stamped as the user's, so
    she couldn't undo them and asked the user to say "go ahead" four times."""
    reason = "user_pause" if user_asked else "columbina_pause"
    sessions.pause_session(db, user_id, _live(db, user_id).id, reason=reason)
    if user_asked:
        return "Paused; you've let go of the controls. Only the user can lift this pause."
    return "Paused; you've let go of the controls. It's your pause: resume_game when you know what to do next."


# Pauses Columbina may undo on her own: ones she caused. Never the user's.
SELF_RESUMABLE = ("driver_stuck", "planning_timeout", "plan_complete", "columbina_pause")
# Paused because her working memory closed or suspended the goal: a new plan from the user's turn lifts it.
GOAL_PAUSES = ("goal_suspended", "goal_completed", "goal_abandoned")


def resume(db: Session, user_id: uuid.UUID, *, only_own_pauses: bool = False) -> str:
    session = _live(db, user_id)
    # Running is nothing to undo. After her own resume the reason stays "columbina_resume", which
    # isn't hers to lift, so a background turn was told "this pause wasn't yours" while playing and
    # told the user she was paused (seen live 2026-10-19, twice in one session).
    if session.state == "running":
        return "You're already playing; there's no pause to lift."
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
