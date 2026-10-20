"""Her planner at work during play (docs/COLUMBINA_MIND_DESIGN.md "Planning"; the call is
``app/ai/mind/plan.py``).

One author for the plan. Anything that may need the plan to change becomes a planning
job (``agent_events``, source ``plan``, one per session, merged while pending, never two
at once): a goal from her reply to the user (``set_goal``), her hands stuck, struggling or
on their last step, a goal done, or a hard thought asking for a rethink. The job builds the
planning input from facts (the goal, the plan with step ids, what it takes from the game's
own data, what is happening, her skills and lessons, her thoughts, the talk), makes one
call, and applies its decision here:

- keep: nothing changes (a pause her hands got stuck in is lifted: try again);
- revise: open steps merged by id, so a step is only lost when the planner drops it;
- new_goal / done / wait_for_user: a new plan, nothing, or a pause with a question.

The planner's "why" is saved as her thought (level hard, kind plan), and a question or
line goes through the one speaking gate (``agent_say``). A decision made on a plan that
changed meanwhile, or while a newer goal from the user waits, is dropped: the newer job
plans again.

Only for her own Minecraft player (game packs), and only with ``MIND_PLANNER_ENABLED``.
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, AgentEventJob, DesktopSession, User
from app.db.session import SessionLocal
from app.services import agent_events, game_pack
from app.services.game_event_kinds import kind_of

log = logging.getLogger(__name__)

SOURCE = "plan"
# Reasons that are news about the plan, which the planner answers instead of a background turn.
PLAN_KINDS = frozenset({"stuck", "needs_human", "struggling", "plan_low", "goal_done", "rethink"})
PLAN_PAUSES = frozenset({"paused:plan_complete", "paused:planning_timeout"})
TALK_MESSAGES = 6
GOAL_CHARS = 200

_planner = None


def enabled() -> bool:
    from app.services import mind

    return bool(get_settings().mind_planner_enabled and mind.enabled())


def handles(session: DesktopSession) -> bool:
    """Whether her planner writes this session's plan."""
    return enabled() and game_pack.is_pack_profile(session.profile_id)


def is_plan_news(reason: str) -> bool:
    return kind_of(reason) in PLAN_KINDS or reason in PLAN_PAUSES


# --------------------------------------------------------------------------- #
# Asking for a plan
# --------------------------------------------------------------------------- #

def request(db: Session, session: DesktopSession, reason: str, *, goal: dict | None = None) -> None:
    """Queue planning for this session, in the caller's transaction (the caller commits, then
    calls ``agent_events.wake``). ``goal``: what the user wants now ({text, target, rules});
    a newer one replaces an older one still waiting."""
    payload = {"session_id": str(session.id), "plan_revision": session.plan_revision}
    if goal is not None:
        payload["goal"] = {**goal, "at": time.time()}
    agent_events.enqueue(db, user_id=session.owner_user_id, source=SOURCE, dedupe_key=str(session.id),
                         reason=reason[:400], payload=payload)


def cancel_waiting(db: Session, session_id: uuid.UUID) -> None:
    """Drop planning that hasn't started (the user just set the plan directly: a goal that was
    waiting is out of date). A job already running finds the plan changed and drops its answer."""
    db.execute(update(AgentEventJob).where(
        AgentEventJob.source == SOURCE, AgentEventJob.dedupe_key == str(session_id),
        AgentEventJob.status == "pending",
    ).values(status="done", last_error="superseded: the plan was set directly"))


def _jobs(db: Session, session_id: uuid.UUID, statuses: tuple[str, ...]) -> list[AgentEventJob]:
    return list(db.scalars(select(AgentEventJob).where(
        AgentEventJob.source == SOURCE, AgentEventJob.dedupe_key == str(session_id),
        AgentEventJob.status.in_(statuses),
    ).order_by(AgentEventJob.created_at)))


def planning_now(db: Session, session_id: uuid.UUID) -> str | None:
    """What she's working out a plan for, while a planning job waits or runs: for her summary,
    so a reply says "figuring it out" instead of claiming a plan she doesn't have yet."""
    jobs = _jobs(db, session_id, ("pending", "running"))
    if not jobs:
        return None
    goal = next((job.payload.get("goal") for job in reversed(jobs) if job.payload.get("goal")), None)
    if goal:
        return f'how to do what the user asked: "{goal.get("text", "")}"'
    return "whether your plan still fits (" + "; ".join(agent_events.reasons_of(jobs[-1]))[:200] + ")"


def _newer_goal_waiting(db: Session, session_id: uuid.UUID) -> bool:
    return any(job.payload.get("goal") for job in _jobs(db, session_id, ("pending",)))


# --------------------------------------------------------------------------- #
# The planning input
# --------------------------------------------------------------------------- #

def plan_lines(plan: dict | None) -> list[str]:
    """The plan as the planner reads it: goal, rules, every step with its id and state."""
    if not plan:
        return ["The plan: none yet."]
    lines = [f"The plan: goal \"{plan['goal']}\"" + (f" (about: {plan['target']})" if plan.get("target") else "")
             + f", set by {_SOURCES.get(plan.get('source'), plan.get('source'))}."]
    if plan.get("constraints"):
        lines.append("Rules in force: " + "; ".join(plan["constraints"]) + ".")
    for step in plan["steps"]:
        lines.append(f"  [{step['id']}] {step['state']}: {step['do']} (done when: {step['done_when']})")
    if not any(step["state"] in ("pending", "active") for step in plan["steps"]):
        lines.append("  (every step is done)")
    return lines


_SOURCES = {"columbina_user_turn": "you, for the user", "columbina_planning_turn": "you, on your own",
            "columbina_mind": "you, revising", "user_api": "the user"}


def needs_text(found: dict) -> str:
    """What the pack worked out it takes (pack /needs), in words."""
    target = found.get("target", "?")
    if not found.get("known"):
        close = found.get("did_you_mean") or []
        return f"There's no item or block called {target}." + (f" Close names: {', '.join(close)}." if close else "")
    doing = "mine" if found.get("kind") == "block" else "get"
    if found.get("ready"):
        return f"What it takes to {doing} {target}: nothing missing, she can do it now."
    lines = [f"What it takes to {doing} {target} from what she has now (worked out by code from the game's data; "
             "trust it), in order:"]
    for i, step in enumerate(found.get("steps") or [], start=1):
        lines.append(f"  {i}. {_step_words(step)}")
    for gap in found.get("gaps") or []:
        lines.append(f"  Not worked out: {gap.get('item')} x{gap.get('count')} ({gap.get('why')}).")
    return "\n".join(lines)


def _step_words(step: dict) -> str:
    action, item, count = step.get("action"), step.get("item"), step.get("count", 1)
    if action == "mine":
        tool = step.get("tool")
        how = (f"needs a {tool}" if step.get("tool_needed") else f"with her {tool}") if tool else "by hand"
        return f"mine {step.get('from')} for {item} x{count} ({how})"
    if action == "craft":
        return f"craft {item} x{count}" + (" (at a crafting table)" if step.get("table") else "")
    if action == "place":
        return f"place a {item}"
    if action == "smelt":
        return f"smelt {step.get('from')} into {item} x{count} (in a furnace, with fuel)"
    if action == "hunt":
        return f"get {item} x{count} from a {step.get('from')}"
    return f"{action} {item} x{count}"


def _needs(target: str | None) -> str | None:
    if not target:
        return None
    from app.ai.desktop.pack_client import PackClient, PackError

    try:
        return needs_text(PackClient.from_settings().needs(target))
    except PackError as exc:
        return f"What it takes to get {target}: couldn't be worked out just now ({exc})."


def _talk(db: Session, user_id: uuid.UUID) -> list[str]:
    from app.services import chat as chat_service

    conversations = chat_service.list_conversations(db, user_id=user_id)
    if not conversations:
        return []
    history = chat_service.get_history(db, conversation=conversations[0])[-TALK_MESSAGES:]
    return [f"  {'the user' if turn['role'] == 'user' else 'you'}: {' '.join(str(turn['content']).split())[:300]}"
            for turn in history]


def build_input(db: Session, session: DesktopSession, reasons: list[str], goal: dict | None) -> str:
    """The planning input, most important first: why now, the goal, the plan, what it takes,
    what is happening, what she can do, what she learned, who is who, the talk."""
    from app.services import desktop_game
    from app.services.game_events import describe_reason

    why = [describe_reason(reason) for reason in reasons if not reason.startswith("goal:")]
    if goal:
        why.insert(0, f'The user wants something: "{goal.get("text", "")}".'
                   + (f" (About: {goal['target']}.)" if goal.get("target") else "")
                   + (f" Their rules for it: {'; '.join(goal['rules'])}." if goal.get("rules") else ""))
    sections = [("Why you're planning now", [f"- {line}" for line in why] or ["- (nothing specific: check the plan)"])]
    keep_playing = ("Keep playing is on: after a goal, she picks the next one herself."
                    if session.autonomy == "continue" else
                    "Keep playing is off: after this goal, she stops and tells the user.")
    sections.append(("The goal and the plan", [*plan_lines(session.plan), keep_playing,
                                               f"Her session: {session.state}"
                                               + (f" ({desktop_game.REASONS.get(session.reason_code, session.reason_code)})"
                                                  if session.state != "running" and session.reason_code else "")
                                               + "."]))
    target = (goal or {}).get("target") or (session.plan or {}).get("target")
    needs = _needs(target)
    if needs:
        sections.append(("What it takes", [needs]))
    facts = desktop_game.planning_facts(db, session)
    sections.append(("What is happening", facts["now"]))
    sections.append(("What she can do", facts["skills"]))
    sections.append(("How steps are written here", facts["steps"]))
    if facts["learned"]:
        sections.append(("What she learned playing", facts["learned"]))
    if facts["who"]:
        sections.append(("Who is who", facts["who"]))
    talk = _talk(db, session.owner_user_id)
    if talk:
        sections.append(("Recent talk (oldest first)", talk))
    return "\n\n".join(f"## {title}\n" + "\n".join(lines) for title, lines in sections)


# --------------------------------------------------------------------------- #
# Applying a decision
# --------------------------------------------------------------------------- #

def merged_steps(current: list[dict], decision_steps: list, drop: dict[str, str]) -> list[dict]:
    """The new open steps: the planner's order, existing steps by id, new ones where it put
    them; open steps it didn't mention stay, at the end, unless it dropped them."""
    open_by_id = {step["id"]: step for step in current if step["state"] in ("pending", "active")}
    out, used = [], set()
    for entry in decision_steps:
        if entry.keep:
            step = open_by_id.get(entry.keep)
            if step is not None and entry.keep not in used and entry.keep not in drop:
                out.append({"do": step["do"], "done_when": step["done_when"]})
                used.add(entry.keep)
        elif entry.do:
            out.append({"do": entry.do[:120], "done_when": (entry.done_when or f"{entry.do} is done")[:120]})
    for step_id, step in open_by_id.items():
        if step_id not in used and step_id not in drop:
            out.append({"do": step["do"], "done_when": step["done_when"]})
    return out


def _same_steps(current: list[dict], steps: list[dict]) -> bool:
    open_steps = [(s["do"], s["done_when"]) for s in current if s["state"] in ("pending", "active")]
    return open_steps == [(s["do"], s["done_when"]) for s in steps]


def _rules(*groups) -> list[str]:
    out: list[str] = []
    for group in groups:
        for rule in group or []:
            rule = " ".join(str(rule).split())[:120]
            if rule and rule.lower() not in (r.lower() for r in out):
                out.append(rule)
    return out[:5]


def apply(db: Session, session: DesktopSession, decision, *, read_revision: int, goal: dict | None) -> str:
    """Apply one decision to the session (owner-scoped through desktop_game). Returns what
    happened, for the log."""
    from app.services import desktop_game
    from app.services.desktop_sessions import DesktopError

    if session.plan_revision != read_revision:
        return "dropped: the plan changed while planning"
    if _newer_goal_waiting(db, session.id):
        return "dropped: a newer goal from the user is waiting"
    user_id = session.owner_user_id
    plan = session.plan or {}
    max_steps = get_settings().desktop_plan_max_steps
    try:
        if decision.decision == "keep":
            return "kept" + desktop_game.unpause_after_planning(db, user_id, "columbina_mind")
        if decision.decision == "revise":
            steps = merged_steps(plan.get("steps", []), decision.steps, decision.drop)[:max_steps]
            if not steps:
                return "nothing to do: a revision with no steps"
            if _same_steps(plan.get("steps", []), steps):
                return "kept (the revision changed nothing)" + desktop_game.unpause_after_planning(
                    db, user_id, "columbina_mind")
            return desktop_game.replace_from_planner(
                db, user_id, read_revision=read_revision, goal=plan.get("goal") or decision.goal or "",
                steps=steps, constraints=_rules(plan.get("constraints"), (goal or {}).get("rules"), decision.rules),
                target=decision.target or plan.get("target"), source="columbina_mind", new_goal=False)
        if decision.decision == "new_goal":
            steps = [{"do": s.do[:120], "done_when": (s.done_when or f"{s.do} is done")[:120]}
                     for s in decision.steps if s.do][:max_steps]
            text = decision.goal or (goal or {}).get("text")
            if not text or not steps:
                return "nothing to do: a new goal needs a goal and steps"
            return desktop_game.replace_from_planner(
                db, user_id, read_revision=read_revision, goal=text, steps=steps,
                constraints=_rules((goal or {}).get("rules"), decision.rules),
                target=decision.target or (goal or {}).get("target"),
                # The user's own goal counts as them taking part (it resets her own-goal limit).
                source="columbina_user_turn" if goal else "columbina_planning_turn", new_goal=True)
        if decision.decision == "wait_for_user":
            if session.state == "running":
                desktop_game.pause(db, user_id, user_asked=False)
                return "paused to wait for the user"
            return "waiting for the user"
        return "done"
    except DesktopError as exc:
        return f"refused: {exc.message}"


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def _get_planner():
    global _planner
    if _planner is None:
        from app.ai.mind.plan import Planner

        settings = get_settings()
        _planner = Planner(settings.mind_plan_model, api_key=settings.openai_api_key or "",
                           reasoning_effort=settings.mind_plan_reasoning_effort)
    return _planner


def run(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str], payload: dict, *, planner=None) -> str:
    """One planning job (the agent-events worker). Raises when the call fails, so the job is
    retried; returns what happened otherwise."""
    from app.services import agent_say, mind, mind_minecraft

    goal = payload.get("goal")
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.owner_user_id != user_id or session.state in DESKTOP_TERMINAL_STATES:
            return "skipped: no live session"
        text = build_input(db, session, reasons, goal)
        read_revision = session.plan_revision
        user = db.get(User, user_id)
        name = user.name if user and user.name else "the user"
        commentary_mode = session.commentary_mode
        db.commit()
    topic = (f"planning: {goal['text']}" if goal else "planning: " + "; ".join(reasons))[:GOAL_CHARS]
    decision = (planner or _get_planner()).plan(
        text, user=name, situation=mind_minecraft.situation(), max_steps=get_settings().desktop_plan_max_steps)
    if decision.decision is None:
        mind.log_decision({"ts": time.time(), "session": str(session_id), "plan": "error", "reasons": reasons,
                  "error": decision.error, "ms": decision.latency_ms})
        raise RuntimeError(f"planning failed: {decision.error}")
    said = None
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.state in DESKTOP_TERMINAL_STATES:
            return "skipped: the session ended while planning"
        outcome = apply(db, session, decision, read_revision=read_revision, goal=goal)
        db.commit()
        applied = not outcome.startswith(("dropped", "refused", "nothing"))
        line = decision.ask or decision.say
        if line and applied:
            kind = "ask" if decision.ask else "plan"
            result = agent_say.deliver(db, user_id=user_id, text=line, reasons=[f"{kind}: {decision.why or ''}"],
                                       commentary_mode=commentary_mode)
            said = line if result.startswith("Said") else None
        if decision.why:
            mind.save_thought(db, user_id=user_id, session_id=session_id, level="hard", kind="plan",
                              text=decision.why, said=said, status="done" if applied else "stale", topic=topic)
    mind.log_decision({"ts": time.time(), "session": str(session_id), "plan": decision.decision, "outcome": outcome,
              "reasons": reasons, "goal": goal, "why": decision.why, "steps": [vars(s) for s in decision.steps],
              "drop": decision.drop, "rules": decision.rules, "target": decision.target, "ask": decision.ask,
              "say": decision.say, "said": bool(said), "ms": decision.latency_ms,
              "input_tokens": decision.input_tokens})
    return outcome
