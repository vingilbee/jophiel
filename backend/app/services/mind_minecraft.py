"""Minecraft as a source and a body of her general mind (docs/COLUMBINA_INNER_STATE_PLAN.md).

Everything about the game the mind needs, behind the contracts in app/ai/mind/contracts.py:

- the source: each live pack session is an activity, polled every second; its moment is the
  tick (app/ai/mind/tick.py over the pack's state, notices, plan and talk); its problems are a
  step that keeps failing (her hands' 4th and 10th failed try), a surprise (a result that says
  failed while she got something, or the gate's hard when the world changed), and something big
  (a new goal with 3+ steps, a learned move run 5+ times); a problem no longer holds when the
  game ended, the step is over or the goal changed;
- the body: her hands (the skill driver) take tips through ``desktop_commands``.

Planning stays Minecraft's own (app/ai/mind/plan.py, services/game_planning.py).
"""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from app.ai.desktop.pack_client import PackClient, PackError
from app.ai.mind.contracts import Command, CommandResult, Moment, Rhythm, Scope
from app.ai.mind.hard import HardInput, Problem
from app.ai.mind.tick import MINECRAFT, danger, news, render, worse
from app.core.config import get_settings
from app.db.models import DesktopSession
from app.services import desktop_commands, game_moment, mind_ticks

NAME = "minecraft"
BIG_STEPS = 3  # a new goal with this many steps is something big
BIG_TIMES = 5  # a learned move run this many times in a row is too
INVENTORY_KEPT_SECONDS = 180  # how far back "while it ran" can look
# Fresh tick lines that are about talk, her hands or her plan, not the world: a gate's "hard"
# on only these is not a surprise (the user talking is her mouth's to answer).
NOT_WORLD = ("the user said", "you said", "your hands:", "new goal:", "next step:", "session ", "learning:",
             "(first tick")
QUIET_TOPICS = (
    "{user}: what they want from today, and how they're doing",
    "the session so far: what you two did, and what's unfinished",
    "yourself: how it's going for you, and what you'd like to do",
)

_situation = None
_notice_cursors: dict[uuid.UUID, int] = {}


def enabled() -> bool:
    """Her mind is on in Minecraft: the mind and desktop mode both."""
    from app.services import mind

    return mind.enabled() and bool(get_settings().desktop_agent_enabled)


def situation():
    """The Minecraft situation, with her playing voice for spoken lines."""
    global _situation
    if _situation is None:
        from app.ai.prompts import _PLAYING_VOICE_PROMPT

        _situation = replace(MINECRAFT, voice=_PLAYING_VOICE_PROMPT)
    return _situation


@dataclass
class Triggers:
    """What her problems remember between ticks, per session."""

    started: bool = False
    failing_seen: set = field(default_factory=set)  # (step id, failed count) already thought about
    outcome_seen_at: float = 0.0
    inventory: deque = field(default_factory=deque)  # (at, {item: count})
    goal_seen: str | None = None
    learned_seen: tuple | None = None  # the learned move run already thought about


# --------------------------------------------------------------------------- #
# Problems
# --------------------------------------------------------------------------- #

def _counts(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def _gained(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [f"{name} +{after[name] - before.get(name, 0)}" for name in sorted(after)
            if after[name] > before.get(name, 0)]


def _inventory_at(st: Triggers, at: float) -> dict[str, int] | None:
    best = None
    for when, counts in st.inventory:
        if when <= at:
            best = counts
    return best


def _goal(plan: dict | None) -> str | None:
    return (plan or {}).get("goal")


def _step(plan: dict | None, step_id: str | None) -> dict | None:
    return next((s for s in (plan or {}).get("steps") or [] if s.get("id") == step_id), None)


def failing(st: Triggers, plan: dict | None, driver: dict) -> Problem | None:
    """Her hands' 4th and 10th failed try on one step."""
    from app.ai.desktop.skill_driver import STRUGGLING_AT, failure_summary

    log_ = driver.get("step") or {}
    failed = int(log_.get("failed") or 0)
    marks = [m for m in STRUGGLING_AT if failed >= m and (log_.get("id"), m) not in st.failing_seen]
    if not log_.get("id") or not marks:
        return None
    st.failing_seen.update((log_["id"], m) for m in marks)
    step = _step(plan, log_["id"]) or {}
    minutes = max(1, round((time.time() - float(log_.get("since") or time.time())) / 60))
    text = (f'Step: "{log_.get("do", "")}"' + (f' (done when: {step["done_when"]})' if step.get("done_when") else "")
            + f". Tried {log_.get('tries', 0)} times in about {minutes} min; {failed} failed: "
            + failure_summary(log_.get("failures") or []) + ".")
    if driver.get("notes"):
        text += f'\nYour hands\' note: "{driver["notes"]}".'
    return Problem("failing", text, topic=f"keeps failing: {log_.get('do', '')}"[:200],
                   query=f"{log_.get('do', '')} {failure_summary(log_.get('failures') or [])}"[:300],
                   anchor={"goal": _goal(plan), "step_id": log_["id"]})


def surprise_from_outcome(st: Triggers, plan: dict | None, driver: dict, state: dict) -> Problem | None:
    """A result that says it failed while she got something anyway (live: the sheep move said
    "the target is gone" three times while the mutton went up)."""
    from app.ai.desktop.skill_driver import FAILED

    found = None
    newest = st.outcome_seen_at
    for outcome in driver.get("outcomes") or []:
        at = float(outcome.get("at") or 0)
        if at <= st.outcome_seen_at:
            continue
        newest = max(newest, at)
        if outcome.get("status") not in FAILED:
            continue
        began = at - float(outcome.get("seconds") or 0) - 1.0
        before = _inventory_at(st, began)
        if before is None:
            continue
        gained = _gained(before, _counts(state))
        if gained:
            params = ", ".join(f"{k}={v}" for k, v in (outcome.get("params") or {}).items())
            found = Problem(
                "surprise",
                f"Your hands said {outcome.get('skill')}({params}) {outcome.get('status')}: "
                f"{outcome.get('reason') or 'no reason given'}. But while it ran you got: {', '.join(gained)} "
                "(counted by the game).",
                topic=f"surprise: {outcome.get('skill')} said {outcome.get('status')} but you got {gained[0]}"[:200],
                query=f"{outcome.get('skill')} {outcome.get('reason') or ''} {' '.join(gained)}"[:300],
                anchor={"goal": _goal(plan)})
    st.outcome_seen_at = newest
    return found


def surprise_from_gate(fresh: list[str], plan: dict | None, driver: dict) -> Problem | None:
    """The gate's hard, when something in the world changed (not only talk, her hands or her plan)."""
    world = [line for line in fresh if not line.startswith(NOT_WORLD)]
    if not world:
        return None
    text = "What just happened (counted by the game):\n" + "\n".join(f"- {line}" for line in world)
    step = next((s for s in (plan or {}).get("steps") or [] if s.get("state") == "active"), None)
    if step:
        text += f'\nWhat you were doing: "{step.get("do", "")}".'
    if driver.get("notes"):
        text += f'\nYour hands\' note: "{driver["notes"]}".'
    return Problem("surprise", text, topic=f"surprise: {world[0]}"[:200], query=" ".join(world)[:300],
                   anchor={"goal": _goal(plan)})


def big(st: Triggers, plan: dict | None, driver: dict, state: dict) -> Problem | None:
    """A new goal with BIG_STEPS+ steps, or a learned move run BIG_TIMES+ times in a row."""
    goal = _goal(plan)
    found = None
    if goal != st.goal_seen:
        st.goal_seen = goal
        steps = (plan or {}).get("steps") or []
        if goal and len(steps) >= BIG_STEPS:
            lines = [f'You\'re starting: "{goal}". Steps:'] + [f"  - {s.get('do', '')}" for s in steps]
            if (plan or {}).get("constraints"):
                lines.append("Rules: " + "; ".join(plan["constraints"]))
            carry = ", ".join(f"{n} x{c}" for n, c in sorted(_counts(state).items())) or "nothing"
            lines.append(f"You carry: {carry}.")
            found = Problem("big", "\n".join(lines), topic=f"before: {goal}"[:200], query=goal[:300],
                            anchor={"goal": goal})
    running = driver.get("running") or {}
    skill, times = running.get("skill") or "", (running.get("params") or {}).get("times")
    key = (skill, json.dumps(running.get("params") or {}, sort_keys=True)) if skill else None
    if key != st.learned_seen:
        st.learned_seen = key
        if skill.startswith("learned_") and isinstance(times, int) and times >= BIG_TIMES:
            found = Problem(
                "big", f"You're about to do {skill} {times} times in a row (a move you taught yourself), for "
                       f'"{goal or "your goal"}".',
                topic=f"before: {skill} x{times}"[:200], query=f"{skill} {goal or ''}"[:300], anchor={"goal": goal})
    return found


# --------------------------------------------------------------------------- #
# What she reads, and whether a thought still holds
# --------------------------------------------------------------------------- #

def _brief(state: dict | None) -> str:
    """Her state text up to the long shape-of-the-place part."""
    lines = []
    for line in (state or {}).get("text", "").splitlines():
        if line.startswith(("Ways", "Land ", "Highest ground", "Surface", "Way out", "Openings")):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _learned(db: Session, user_id: uuid.UUID) -> tuple[str, ...]:
    from app.services import desktop_game, game_learning, game_pack, game_skills

    lines = desktop_game._learned_lines(db, user_id, game_learning.game_for_profile(game_pack.PACK_PROFILE_ID))
    lines += game_skills.summary_lines(db, user_id, desktop_game.PACK_GAME)
    return tuple(line.strip() for line in lines if line.strip())


def dropped(db: Session | None, session: DesktopSession | None, problem: Problem, cancelled: str | None) -> str | None:
    """Why the thought no longer applies, or None."""
    from app.services import agent_say

    if cancelled:
        return cancelled
    if session is None or session.state in ("ended", "failed"):
        return "the game ended"
    plan = session.plan or {}
    goal_changed = problem.anchor.get("goal") not in (None, plan.get("goal"))
    if problem.trigger == "failing":
        step_ = _step(plan, problem.anchor.get("step_id"))
        if goal_changed:
            return "the goal changed"
        if step_ is None or step_.get("state") not in ("active", "pending"):
            return "that step is over"
    if problem.trigger == "big" and goal_changed:
        return "the goal changed"
    if problem.trigger == "handoff" and goal_changed and agent_say.user_spoke_since(
            db, session.owner_user_id, float(problem.anchor.get("at") or 0) + 1.0):
        return "you two moved on"
    return None


# --------------------------------------------------------------------------- #
# Source and body
# --------------------------------------------------------------------------- #

class Hands:
    """Her hands in the game (the skill driver): they take tips."""

    def controls(self) -> frozenset[str]:
        return frozenset({"tip"})

    def command(self, scope: Scope, command: Command) -> CommandResult:
        if command.control != "tip":
            return CommandResult("unsupported", f"her hands don't take {command.control}")
        kept = desktop_commands.send_hands_message(scope.activity, command.text)
        return CommandResult("accepted") if kept is not None else CommandResult("refused", "no Redis")


class MinecraftSource:
    name = NAME
    rhythm = Rhythm("poll", 1.0)
    shadow = False

    def enabled(self) -> bool:
        return enabled()

    def poll(self, db: Session) -> list[tuple[Scope, Any]] | None:
        """Each live pack session with the pack's state; None when the pack is down (no tick)."""
        sessions = mind_ticks.live_sessions(db)
        if not sessions:
            return []
        pack = PackClient.from_settings()
        try:
            state = pack.state()
        except PackError:
            return None
        return [(Scope(s.owner_user_id, NAME, s.id), (s, state, pack)) for s in sessions]

    def moment(self, db: Session | None, scope: Scope, payload: Any, previous: Any, at: float, *,
               thoughts: list[dict], thinking: str | None) -> Moment:
        session, state, pack = payload
        notices = mind_ticks.next_notices(pack, _notice_cursors, session.id)
        current = mind_ticks.build_tick(db, session, state, notices, at, thoughts=thoughts, thinking=thinking)
        before = mind_ticks.fresh_previous(previous, at)
        text, pressure, changed, why = render(current, before)
        return Moment(text, pressure, news(current, before), changed, why, can_act=session.state == "running",
                      danger=danger(current.state), talk=current.talk, plan=session.plan,
                      capture=lambda: game_moment.capture(db, session, state, at=at),
                      data={"session": session, "state": state, "tick": current})

    def previous(self, moment: Moment) -> Any:
        """What the next moment compares against."""
        return moment.data["tick"]

    def after_tick(self, scope: Scope, moment: Moment, number: int, decision: dict) -> None:
        if get_settings().mind_tick_recording_enabled:
            mind_ticks.append_tick(scope.activity, number, moment.data["tick"], moment.text, moment.pressure,
                                   moment.changed, moment.why, extra={"mind": decision})

    def gate_situation(self):
        return MINECRAFT

    def situation(self):
        return situation()

    def new_state(self) -> Triggers:
        return Triggers()

    def worse(self, now: Any, before: Any) -> bool:
        return worse(now, before)

    def problems(self, scope: Scope, st: Triggers, moment: Moment, *, gate_hard: bool) -> list[Problem]:
        at, state, plan = moment.data["tick"].at, moment.data["state"], moment.plan
        if not st.started:
            st.started, st.goal_seen = True, _goal(plan)
        st.inventory.append((at, _counts(state)))
        while st.inventory and st.inventory[0][0] < at - INVENTORY_KEPT_SECONDS:
            st.inventory.popleft()
        driver = desktop_commands.load_driver_state(scope.activity) or {}
        found = [failing(st, plan, driver), surprise_from_outcome(st, plan, driver, state),
                 surprise_from_gate(moment.fresh, plan, driver) if gate_hard else None,
                 big(st, plan, driver, state)]
        return [p for p in found if p is not None]

    def drop_waiting(self, scope: Scope, moment: Moment, problem: Problem) -> str | None:
        """The user changed the goal: a thought about the old one no longer holds."""
        plan = moment.plan or {}
        if plan.get("source") == "columbina_user_turn" and problem.anchor.get("goal") not in (None, plan.get("goal")):
            return "the goal changed"
        return None

    def quiet_topics(self, user: str) -> tuple[str, ...]:
        return tuple(topic.format(user=user) for topic in QUIET_TOPICS)

    def gather(self, db: Session, scope: Scope, problem: Problem, base: HardInput) -> tuple[HardInput | None, Any]:
        session = db.get(DesktopSession, scope.activity)
        if session is None:
            return None, None
        try:
            state = PackClient.from_settings().state()
        except PackError:
            state = None
        plan = session.plan or {}
        active = next((s for s in plan.get("steps") or [] if s.get("state") == "active"), None)
        goal = plan.get("goal") and (plan["goal"] + (f'; current step: {active["do"]}' if active else ""))
        inp = replace(base, now=_brief(state), goal=goal, learned=_learned(db, scope.user_id))
        return inp, game_moment.capture(db, session, state)

    def dropped(self, db: Session, scope: Scope, problem: Problem, cancelled: str | None) -> str | None:
        return dropped(db, db.get(DesktopSession, scope.activity), problem, cancelled)

    def outdated(self, db: Session, scope: Scope, problem: Problem, token: Any) -> list[str]:
        # A surprise is about a moment: out of date once she's paused or playing again, or a lesson
        # ended (not when she just got more loot, which is what a fight looks like).
        if problem.trigger != "surprise":
            return []
        return game_moment.since(db, token, frozenset({game_moment.STATE, game_moment.LESSON}))

    def live(self, db: Session, scope: Scope) -> bool:
        session = db.get(DesktopSession, scope.activity)
        return session is not None and session.state in mind_ticks.LIVE_STATES

    def changed_since(self, db: Session, token: Any) -> list[str]:
        return game_moment.since(db, token)

    def current_goal(self, db: Session, scope: Scope) -> str | None:
        session = db.get(DesktopSession, scope.activity)
        if session is None:
            return None
        plan_goal = (session.plan or {}).get("goal")
        from app.services import memory_manager

        goal = memory_manager.session_goal(db, session)
        if goal is not None and plan_goal and goal.text != plan_goal:
            return f"{goal.text} (the plan for it now: {plan_goal})"
        return goal.text if goal is not None else plan_goal

    def speaking(self, db: Session, scope: Scope) -> dict:
        session = db.get(DesktopSession, scope.activity)
        return {"commentary_mode": session.commentary_mode if session is not None else "quiet"}

    def body(self, scope: Scope) -> Hands:
        return Hands()

    def thought_session(self, scope: Scope) -> uuid.UUID:
        return scope.activity

    def handoff_scope(self, db: Session, user_id: uuid.UUID) -> Scope:
        from app.services import desktop_game

        session = desktop_game._live(db, user_id)  # raises when she isn't playing, as before
        return Scope(session.owner_user_id, NAME, session.id)


SOURCE = MinecraftSource()


def scope_of(session) -> Scope:
    return Scope(session.owner_user_id, NAME, session.id)
