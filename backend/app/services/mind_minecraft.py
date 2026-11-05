"""Minecraft as a source and a body of her general mind (docs/COLUMBINA_INNER_STATE_PLAN.md).

Everything about the game the mind needs, behind the contracts in app/ai/mind/contracts.py:

- the source: each live pack session is an activity, polled every second; its moment is the
  tick (app/ai/mind/tick.py over the pack's state, notices, plan and talk); its problems are a
  look back over her effort when a step of her plan ends (done, given up by her hands, or left
  when the plan moved on after her hands had tried it), a surprise (a result that says failed
  while she got something, or the gate's hard when the world changed), and something big (a new
  goal with 3+ steps, a learned move run 5+ times); a problem no longer holds when the game
  ended, the outcome or the goal changed;
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
    """Her mind reaches Minecraft: desktop mode is on."""
    return bool(get_settings().desktop_agent_enabled)


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
    step_seen: dict | None = None  # the plan step that was active last tick, with her hands' tries on it
    hands_stuck_seen: tuple | None = None  # her hands' last stuck report already looked back on
    outcome_seen_at: float = 0.0
    inventory: deque = field(default_factory=deque)  # (at, {item: count})
    goal_seen: str | None = None
    learned_seen: tuple | None = None  # the learned move run already thought about
    place_surprise_at: float = 0.0  # when going in or out last made her think hard


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


def _active(session: DesktopSession, plan: dict | None, driver: dict) -> dict | None:
    """The step active now, keyed by plan revision (step ids repeat across plans), with her hands' tries."""
    step = next((s for s in (plan or {}).get("steps") or [] if s.get("state") == "active"), None)
    if step is None:
        return None
    log_ = driver.get("step") or {}
    mine = log_.get("id") == step.get("id")
    return {"revision": getattr(session, "plan_revision", None), "id": step.get("id"), "do": step.get("do", ""),
            "tries": int(log_.get("tries") or 0) if mine else 0, "failed": int(log_.get("failed") or 0) if mine else 0,
            "failures": (log_.get("failures") or []) if mine else []}


def step_ended(st: Triggers, session: DesktopSession, plan: dict | None, driver: dict) -> Problem | None:
    """A step of her plan ended: a moment to look back over the whole effort (stage 2 of
    docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md). Whether it's getting anywhere is her mind's to judge from the
    record; code only notices that a part of it ended. No time limit or count of tries is involved.

    Ended means: her hands finished it; her hands gave up on it (stuck); or the plan moved on while it was
    unfinished after her hands had tried it (one replaced before anything was tried isn't a part that ended)."""
    from app.ai.desktop.skill_driver import failure_summary

    if session is None:
        return None
    revision = getattr(session, "plan_revision", None)
    before, now = st.step_seen, _active(session, plan, driver)
    st.step_seen = now
    stuck = (revision, getattr(session, "current_step_id", None), (driver.get("notes") or "")[:200]) \
        if getattr(session, "driver_status", None) in ("stuck", "needs_human") else None
    gave_up = stuck is not None and stuck != st.hands_stuck_seen
    if stuck is not None:
        st.hands_stuck_seen = stuck
    if before is None or (now is not None and now["revision"] == before["revision"] and now["id"] == before["id"]
                          and not gave_up):
        return None
    same_plan = before["revision"] == revision
    old = _step(plan, before["id"]) if same_plan else None
    if gave_up:
        how = "your hands gave up on it" + (f': "{driver["notes"]}"' if driver.get("notes") else "")
    elif old is not None and old.get("state") == "done":
        how = "done"
    elif before["tries"] > 0:
        how = "the plan moved on before it was done"
    else:
        return None
    text = f'A step of your plan just ended: "{before["do"]}" ({how}).'
    if before["tries"]:
        text += f" Your hands tried it {before['tries']} times; {before['failed']} failed"
        text += (": " + failure_summary(before["failures"]) + "." if before["failed"] and before["failures"] else ".")
    text += ("\nLook back over your whole effort below, not just this step: is it getting anywhere, "
             "and if not, why?")
    return Problem("review", text, topic=f"looking back: {before['do']}"[:200],
                   query=f"{(plan or {}).get('goal') or ''} {before['do']}"[:300],
                   anchor={"outcome": str(session.goal_item_id) if getattr(session, "goal_item_id", None) else None})


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


# Going in or out of a cave or under a roof, and a mouth coming into view: news, but walking along a cave
# edge flips them every few seconds. Live 2026-11-04 each flip started a hard thought that changed her
# route, which flipped them again: 20 route changes in 15 minutes. At most one such thought per gap.
PLACE_FLIPS = ("you went underground", "you're outdoors again", "you went under a roof",
               "a cave or tunnel mouth came into view")
PLACE_SURPRISE_GAP_S = 120.0


def surprise_from_gate(fresh: list[str], plan: dict | None, driver: dict, st: Triggers | None = None,
                       now: float | None = None) -> Problem | None:
    """The gate's hard, when something in the world changed (not only talk, her hands or her plan)."""
    world = [line for line in fresh if not line.startswith(NOT_WORLD)]
    if not world:
        return None
    if st is not None and all(line.startswith(PLACE_FLIPS) for line in world):
        now = time.time() if now is None else now
        if now - st.place_surprise_at < PLACE_SURPRISE_GAP_S:
            return None
        st.place_surprise_at = now
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

SHAPE_LINES = ("Ways", "Land ", "Highest ground", "Surface", "Way out", "Openings", "Open space", "Where it goes on")
# The shape lines her mind reads (all but the long "Land out to" survey): which way the ground rises or
# falls, cave mouths, the way out. Before 2026-11-04 her mind got none of them, so "which branch goes
# deeper?" had no answer in what it read.
# The open space she is in and where it goes on (2026-11-04) are kept too: the passages underground.
SHAPE_KEPT = ("Ways", "Highest ground", "Surface", "Way out", "Openings", "Open space", "Where it goes on")

# How she perceives in Minecraft, checked against the pack's code (game_packs/minecraft/src/sight.js,
# surroundings.js, terrain.js) on 2026-11-04. Fixed: what the code can and can't tell her.
SENSES = (
    "Blocks: you know a block once a face of it has been in your line of sight, or you saw it earlier (about "
    "4000 remembered). Solid blocks block sight; darkness doesn't. Searches reach their radius (go_to: 64) and "
    "check at most 128 candidates, so many hidden ones nearby can crowd out a visible one further off.",
    "Ways: in 8 directions you walk the ground up to 24 blocks (up 1, down at most 3) and learn how far it rises "
    "or falls and what stops you. It stops at the first wall.",
    "Openings (cave or tunnel mouths): detected only while you're under open sky, along 16 directions.",
    "Overhead: open sky, trees, a roof, or underground with how far up the rock starts.",
    "Open space: the air connected to where you stand, filled side by side and never through rock, out to 12 "
    "blocks sideways, 4 above your head and 8 below your feet: its size, the floor in it, water or lava at its "
    "edge, and the passages where it goes on past that. It doesn't say what the place is (a cave, a tunnel, a hole "
    "you dug): that's yours to judge. Nothing tells you what's behind rock.",
    "Height: yours now, a minute ago and five minutes ago, from your own position.",
)


def _brief(state: dict | None) -> str:
    """Her state text up to the long shape-of-the-place part."""
    lines = []
    for line in (state or {}).get("text", "").splitlines():
        if line.startswith(SHAPE_LINES):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _shape(state: dict | None) -> str:
    """The shape of the place, short: the ways (one line each), openings, peak, surface and way out."""
    lines, keep = [], False
    for line in (state or {}).get("text", "").splitlines():
        if line.startswith(SHAPE_LINES):
            keep = line.startswith(SHAPE_KEPT)
        if keep and line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def coverage(state: dict | None) -> tuple[str, ...]:
    """What this view does and doesn't cover, from the view itself (code, no judgment)."""
    text = (state or {}).get("text", "")
    around = next((line for line in text.splitlines() if line.startswith("Around you:")), "")
    out = []
    if not text:
        return ("Nothing: the game's state couldn't be read just now.",)
    # Facts only. The first wording ("cave mouths aren't detected from here") read as advice to go back out,
    # and she left caves for open sky four times in ten minutes (2026-11-04).
    if "underground" in around or "under a roof" in around:
        out.append("Under rock or a roof: the openings list is empty here by design (it's only filled under "
                   "open sky); the ways say where walkable ground rises or falls within 24 blocks; the open "
                   "space says where the air around you goes on past 12 blocks, not where it leads after that.")
    else:
        out.append("Under open sky: openings are listed along 16 directions from here.")
    if "Ways" not in text:
        out.append("No ways were reported in this view.")
    if "Open space" not in text:
        out.append("No open space was reported in this view.")
    return tuple(out)



def dropped(db: Session | None, session: DesktopSession | None, problem: Problem, cancelled: str | None) -> str | None:
    """Why the thought no longer applies, or None."""
    from app.services import agent_say

    if cancelled:
        return cancelled
    if session is None or session.state in ("ended", "failed"):
        return "the game ended"
    plan = session.plan or {}
    goal_changed = problem.anchor.get("goal") not in (None, plan.get("goal"))
    goal_item = getattr(session, "goal_item_id", None)
    if problem.trigger == "review" and problem.anchor.get("outcome") not in (None, str(goal_item) if goal_item else None):
        return "the outcome changed"
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
        found = [step_ended(st, moment.data.get("session"), plan, driver), surprise_from_outcome(st, plan, driver, state),
                 surprise_from_gate(moment.fresh, plan, driver, st) if gate_hard else None,
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
        from app.services import effort

        # Her effort: the outcome from memory and the story of it (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md).
        # The plan's own goal is only her current approach, shown inside it.
        story = effort.lines_for_session(db, session, user=base.user, senses=SENSES, coverage=coverage(state))
        from app.services import memory_lessons, desktop_game, game_skills
        supply = memory_lessons.for_game(db, scope.user_id, "minecraft", query=problem.text)
        learned = desktop_game._learned_lines(db, scope.user_id, "minecraft", supply=supply)
        learned += game_skills.summary_lines(db, scope.user_id, "minecraft")
        now = _brief(state)
        shape = _shape(state)
        inp = replace(base, now=now + (f"\n{shape}" if shape else ""), goal=None if story else goal,
                      effort=story, learned=tuple(learned), lesson_supply=supply)
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
