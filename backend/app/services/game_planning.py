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
line becomes a wake of her mind, which decides whether it's said. A decision made on a plan that
changed meanwhile, or while a newer goal from the user waits, is dropped: the newer job
plans again.

Only for her own Minecraft player (game packs), in desktop mode.

Captured for learning (docs/learning-system/POST-AUDIT-01-implementation.md): each call is a ``planner`` attempt
(the exact input and system text, frozen before the call, and the raw and parsed answer), and what code did with
it is a ``plan_apply`` execution: the plan revision it made, the steps and rules as applied next to the ones
proposed, and where each carried rule came from. A proposal and what was applied stay separate records.
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
# "trial": an experiment her mind chose needs something arranged first (services/learning_trials.py).
PLAN_KINDS = frozenset({"stuck", "needs_human", "struggling", "plan_low", "goal_done", "rethink", "trial"})
PLAN_PAUSES = frozenset({"paused:plan_complete", "paused:planning_timeout"})
TALK_MESSAGES = 6
GOAL_CHARS = 200

_planner = None


def enabled() -> bool:
    from app.services import mind_minecraft

    return mind_minecraft.enabled()


def handles(session: DesktopSession) -> bool:
    """Whether her planner writes this session's plan."""
    return enabled() and game_pack.is_pack_profile(session.profile_id)


def is_plan_news(reason: str) -> bool:
    return kind_of(reason) in PLAN_KINDS or reason in PLAN_PAUSES


# --------------------------------------------------------------------------- #
# Asking for a plan
# --------------------------------------------------------------------------- #

# A thought asking to rethink the plan waits until the plan has stood this long. Live 2026-11-04 every
# thought asked for a rethink: 44 revisions in 24 minutes, each changing her route before the last had
# a chance to work. The user's goals, her hands stuck or struggling, and a goal done never wait.
RETHINK_GAP_S = 60.0


def _last_plan_change(db: Session, session: DesktopSession):
    from app.db.models import DesktopSessionEvent

    return db.scalar(select(DesktopSessionEvent.occurred_at).where(
        DesktopSessionEvent.session_id == session.id, DesktopSessionEvent.type == "desktop.plan_set",
    ).order_by(DesktopSessionEvent.id.desc()).limit(1))


def request(db: Session, session: DesktopSession, reason: str, *, goal: dict | None = None) -> None:
    """Queue planning for this session, in the caller's transaction (the caller commits, then
    calls ``agent_events.wake``). ``goal``: what the user wants now ({text, target, rules});
    a newer one replaces an older one still waiting. A rethink soon after the plan changed waits
    (``RETHINK_GAP_S``); anything else makes the waiting job due now."""
    from datetime import datetime, timedelta, timezone

    payload = {"session_id": str(session.id), "plan_revision": session.plan_revision}
    if goal is not None:
        payload["goal"] = {**goal, "at": time.time()}
    agent_events.enqueue(db, user_id=session.owner_user_id, source=SOURCE, dedupe_key=str(session.id),
                         reason=reason[:400], payload=payload)
    now = datetime.now(timezone.utc)
    due = now
    if goal is None and reason.startswith("rethink"):
        changed = _last_plan_change(db, session)
        if changed is not None:
            changed = changed if changed.tzinfo else changed.replace(tzinfo=timezone.utc)
            due = max(now, changed + timedelta(seconds=RETHINK_GAP_S))
    pending = (AgentEventJob.source == SOURCE, AgentEventJob.dedupe_key == str(session.id),
               AgentEventJob.status == "pending")
    if due > now:
        # Only ever later for a job that holds nothing but rethinks.
        job = db.scalar(select(AgentEventJob).where(*pending))
        if job is not None and all(r.startswith("rethink") for r in job.reasons):
            job.not_before = max(job.not_before, due) if job.not_before else due
    else:
        db.execute(update(AgentEventJob).where(*pending, AgentEventJob.not_before > now).values(not_before=now))


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


def goal_waiting(db: Session, session_id: uuid.UUID) -> bool:
    """A goal from the user waits for her planner or is being planned. Her hands hold until it lands: seen live
    2026-11-24, "make a bed" then resume_game ran the old plan's step ("scan the network") into three stuck pauses
    in 30 seconds while the bed was still being planned."""
    found = any(job.payload.get("goal") for job in _jobs(db, session_id, ("pending", "running")))
    db.commit()
    return found


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
        if made_for_other_goal(plan):
            lines.append("  (These rules came with a plan made for an earlier goal. A revision keeps only the "
                         "ones you list again.)")
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


def _talk(db: Session, user_id: uuid.UUID, read: dict | None = None) -> list[str]:
    """The last few turns of her latest conversation. ``read``: gets the conversation's id, for the capture."""
    from app.services import chat as chat_service

    conversations = chat_service.list_conversations(db, user_id=user_id)
    if not conversations:
        return []
    if read is not None:
        read["conversation_id"] = conversations[0].id
    history = chat_service.get_history(db, conversation=conversations[0])[-TALK_MESSAGES:]
    return [f"  {'the user' if turn['role'] == 'user' else 'you'}: {' '.join(str(turn['content']).split())[:300]}"
            for turn in history]


def _user_name(db: Session, user_id: uuid.UUID) -> str:
    user = db.get(User, user_id)
    return user.name if user and user.name else "the user"


def _coverage() -> tuple[str, ...]:
    """What her current view covers, from the game's state now (nothing when it can't be read)."""
    from app.ai.desktop.pack_client import PackClient, PackError
    from app.services import mind_minecraft

    try:
        return mind_minecraft.coverage(PackClient.from_settings().state())
    except PackError:
        return ()


def build_input(db: Session, session: DesktopSession, reasons: list[str], goal: dict | None,
                read: dict | None = None) -> str:
    """The planning input, most important first: why now, the goal, the plan, what it takes,
    what is happening, what she can do, what she learned, who is who, the talk. ``read``: filled with what it
    was built from (the conversation, the goal item, the lessons supplied), for the capture."""
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
    from app.services import memory_manager

    served = memory_manager.goal_line(db, session)
    sections.append(("The goal and the plan", [*([served] if served else []), *plan_lines(session.plan), keep_playing,
                                               f"Her session: {session.state}"
                                               + (f" ({desktop_game.REASONS.get(session.reason_code, session.reason_code)})"
                                                  if session.state != "running" and session.reason_code else "")
                                               + "."]))
    from app.services import effort, mind_minecraft

    story = effort.lines_for_session(db, session, user=_user_name(db, session.owner_user_id),
                                     senses=mind_minecraft.SENSES, coverage=_coverage())
    if story:
        sections.append(("Her effort (recorded by code; judging it is yours)", list(story)))
    target = (goal or {}).get("target") or (session.plan or {}).get("target")
    needs = _needs(target)
    if needs:
        sections.append(("What it takes", [needs]))
    from app.services import learning_trials

    trial = learning_trials.planning_lines(db, session)
    if trial:
        sections.append(("An experiment to get ready for", trial))
    facts = desktop_game.planning_facts(db, session)
    sections.append(("What is happening", facts["now"]))
    sections.append(("What she can do", facts["skills"]))
    sections.append(("How steps are written here", facts["steps"]))
    if facts["learned"]:
        sections.append(("What she learned playing", facts["learned"]))
    if facts["who"]:
        sections.append(("Who is who", facts["who"]))
    talk = _talk(db, session.owner_user_id, read)
    if read is not None:
        read["goal_item_id"] = getattr(memory_manager.open_goal_of_session(db, session.id), "id", None)
        read["lesson_supply"] = facts["lesson_supply"]
        read["lessons"] = [{"ref": f"lesson:{v.id}", "status": v.status, "revision": v.revision}
                           for v in facts["lesson_supply"].views]
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


def made_for_other_goal(plan: dict | None) -> bool:
    """The plan was made for a goal the session no longer plays for (the user gave a new one, and no new plan
    has been made since). Unknown (an older plan with no record) counts as the same goal."""
    plan = plan or {}
    made_for, now = plan.get("made_for_goal"), plan.get("goal_id")
    return bool(made_for and now and made_for != now)


def _rule_key(rule) -> str:
    """One rule however it's written: spacing, case and end punctuation don't make it another rule (seen live
    2026-11-05: the planner's copy with a full stop read as new, so the old one was 'carried by code')."""
    return " ".join(str(rule).split()).rstrip(" .!;,").lower()


MAX_RULES = 5


def _rules(*groups, limit: int | None = MAX_RULES) -> list[str]:
    out: list[str] = []
    for group in groups:
        for rule in group or []:
            rule = " ".join(str(rule).split())
            if rule and _rule_key(rule) not in {_rule_key(r) for r in out}:
                out.append(rule)
    return out[:limit] if limit is not None else out


def _revised_rules(held, previous, user_goal, proposed) -> list[str]:
    """A revision's rules: when more than fit, the user's come first, then the planner's, then the old plan's
    (so the planner can let an old rule go by not listing it); they read in the plan's order."""
    kept = {_rule_key(r) for r in _rules(held, user_goal, proposed, previous)}
    return [r for r in _rules(held, previous, user_goal, proposed, limit=None) if _rule_key(r) in kept]


def _users_rules(db: Session, session: DesktopSession) -> list[str]:
    """The user's rules in force for the outcome (memory): her hands keep them in every plan for it."""
    from app.services import memory_manager

    goal = memory_manager.open_goal_of_session(db, session.id)
    if goal is None:
        return []
    return [entry.text for entry in memory_manager.serving(db, goal)
            if entry.kind == "constraint" and entry.closed_at is None]


def _rule_origins(applied: list[str], *, proposed=None, user_memory=None, previous_plan=None,
                  user_goal=None, ended_with_old_goal=None) -> dict:
    """The rules as applied, each with where it came from (the planner's proposal, the user's rules in memory, the
    plan before, the user's words for this goal), and the ones the limit left out. Code's carrying of rules is
    shown, never hidden (the old-rules problem of the audit)."""
    key = _rule_key
    groups = {"proposed": proposed or [], "user_memory": user_memory or [], "previous_plan": previous_plan or [],
              "user_goal": user_goal or []}
    applied_keys = {key(r) for r in applied}
    every = {key(r): r for g in groups.values() for r in g}
    return {
        "applied": [{"rule": r, "from": [name for name, g in groups.items() if key(r) in {key(x) for x in g}]}
                    for r in applied],
        "proposed": list(groups["proposed"]),
        "carried_by_code": [r for r in applied if key(r) not in {key(x) for x in groups["proposed"]}],
        "left_out_by_limit": [r for k, r in every.items() if k not in applied_keys],
        # The old plan's rules not carried because it was made for an earlier goal (none listed them again).
        "ended_with_old_goal": [r for r in ended_with_old_goal or [] if key(r) not in applied_keys],
    }



def apply(db: Session, session: DesktopSession, decision, *, read_revision: int, goal: dict | None,
          report: dict | None = None) -> str:
    """Apply one decision to the session (owner-scoped through desktop_game). Returns what
    happened, for the log. ``report``: filled with what code applied next to what was proposed (the steps,
    the rules and where each came from), for the capture; it never changes what is applied."""
    report = report if report is not None else {}
    from app.services import desktop_game
    from app.services.desktop_sessions import DesktopError

    if session.plan_revision != read_revision:
        return "dropped: the plan changed while planning"
    if _newer_goal_waiting(db, session.id):
        return "dropped: a newer goal from the user is waiting"
    user_id = session.owner_user_id
    plan = session.plan or {}
    max_steps = get_settings().desktop_plan_max_steps
    held = _users_rules(db, session)
    try:
        if decision.decision == "keep":
            return "kept" + desktop_game.unpause_after_planning(db, user_id, "columbina_mind")
        if decision.decision == "revise":
            merged = merged_steps(plan.get("steps", []), decision.steps, decision.drop)
            steps = merged[:max_steps]
            # Seen live 2026-11-05: the user said "make a bed, you don't have to follow me"; the plan, still the
            # old goal's, was revised into the bed, and "Follow the user deeper into the cave" came with it.
            # The old plan's rules come along only for the same goal, and after the user's and the planner's.
            old_goal = made_for_other_goal(plan)
            previous = [] if old_goal else plan.get("constraints")
            constraints = _revised_rules(held, previous, (goal or {}).get("rules"), decision.rules)
            listed = {entry.keep for entry in decision.steps if entry.keep}
            report.update(steps={
                "applied": steps, "cut_by_limit": merged[max_steps:],
                "kept_unlisted": [step["id"] for step in plan.get("steps", [])
                                  if step["state"] in ("pending", "active") and step["id"] not in listed
                                  and step["id"] not in decision.drop]},
                rules=_rule_origins(constraints, proposed=decision.rules, user_memory=held,
                                    previous_plan=previous, user_goal=(goal or {}).get("rules"),
                                    ended_with_old_goal=plan.get("constraints") if old_goal else []))
            if not steps:
                return "nothing to do: a revision with no steps"
            if (_same_steps(plan.get("steps", []), steps) and (decision.goal or plan.get("goal")) == plan.get("goal")
                    and not old_goal):  # an old goal's plan is re-made, so its rules end even with the same steps
                return "kept (the revision changed nothing)" + desktop_game.unpause_after_planning(
                    db, user_id, "columbina_mind")
            # The plan's goal is her approach: a revision may change it (seen live 2026-11-04: "follow tearDrop12"
            # outlived ~40 revisions that each said to stop following, because only a new goal could replace it).
            return desktop_game.replace_from_planner(
                db, user_id, read_revision=read_revision, goal=decision.goal or plan.get("goal") or "",
                steps=steps, constraints=constraints,
                target=decision.target or plan.get("target"), source="columbina_mind", new_goal=False)
        if decision.decision == "new_goal":
            proposed = [{"do": s.do[:120], "done_when": (s.done_when or f"{s.do} is done")[:120]}
                        for s in decision.steps if s.do]
            steps = proposed[:max_steps]
            text = decision.goal or (goal or {}).get("text")
            constraints = _rules((goal or {}).get("rules"), decision.rules)  # a new outcome: its own rules
            report.update(steps={"applied": steps, "cut_by_limit": proposed[max_steps:], "kept_unlisted": []},
                          rules=_rule_origins(constraints, proposed=decision.rules, user_goal=(goal or {}).get("rules")))
            if not text or not steps:
                return "nothing to do: a new goal needs a goal and steps"
            return desktop_game.replace_from_planner(
                db, user_id, read_revision=read_revision, goal=text, steps=steps,
                constraints=constraints,
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


def run(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str], payload: dict, *, planner=None,
        job_id: uuid.UUID | None = None, try_number: int | None = None) -> str:
    """One planning job (the agent-events worker). Raises when the call fails, so the job is
    retried; returns what happened otherwise. ``job_id``/``try_number``: the job and its attempt, so a retried
    job's call is a new attempt linked to the one before."""
    from datetime import datetime, timezone

    from app.services import autonomous_speech, mind, mind_minecraft

    goal = payload.get("goal")
    attempt_id = _attempt_id(job_id, try_number)
    started_at = datetime.now(timezone.utc)
    read: dict = {}
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.owner_user_id != user_id or session.state in DESKTOP_TERMINAL_STATES:
            return "skipped: no live session"
        text = build_input(db, session, reasons, goal, read)
        read_revision = session.plan_revision
        plan_before = _frozen(session.plan)
        user = db.get(User, user_id)
        name = user.name if user and user.name else "the user"
        commentary_mode = session.commentary_mode
        db.commit()
    topic = (f"planning: {goal['text']}" if goal else "planning: " + "; ".join(reasons))[:GOAL_CHARS]
    from app.services.personality import current
    text = f"PERSONALITY\n{current().text}\n\nPLANNING SITUATION\n{text}"
    decision = (planner or _get_planner()).plan(
        text, user=name, situation=mind_minecraft.situation(), max_steps=get_settings().desktop_plan_max_steps)
    from app.services import memory_lessons
    memory_lessons.record_uses(user_id, attempt_id, read["lesson_supply"], consumer="planner",
                              received=True, decision_status="ok" if decision.decision else "invalid")
    _capture_attempt(attempt_id, user_id=user_id, session_id=session_id, job_id=job_id, try_number=try_number,
                     text=text, reasons=reasons, goal=goal, read=read, read_revision=read_revision,
                     plan_before=plan_before, decision=decision, started_at=started_at)
    if decision.decision is None:
        mind.log_decision({"ts": time.time(), "session": str(session_id), "plan": "error", "reasons": reasons,
                  "error": decision.error, "ms": decision.latency_ms})
        raise RuntimeError(f"planning failed: {decision.error}")
    said = None
    applied_at = datetime.now(timezone.utc)
    report: dict = {}
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.state in DESKTOP_TERMINAL_STATES:
            _capture_apply(attempt_id, user_id=user_id, session_id=session_id, decision=decision,
                           outcome="skipped: the session ended while planning", read_revision=read_revision,
                           session=None, report=report, started_at=applied_at)
            return "skipped: the session ended while planning"
        outcome = apply(db, session, decision, read_revision=read_revision, goal=goal, report=report)
        db.commit()
        applied = not outcome.startswith(("dropped", "refused", "nothing"))
        if applied and not outcome.startswith("kept"):
            from app.services import system_signals
            system_signals.emit(session.owner_user_id, "mind", "hands", "plan", session_id)
        line = decision.ask or decision.say
        if line and applied:
            kind = "ask" if decision.ask else "plan"
            result = autonomous_speech.submit(
                db, user_id=user_id, activity_id=session_id, source="minecraft",
                event_id=f"plan:{read_revision}:{session.plan_revision}:{kind}", text=line,
                reasons=[f"{kind}: {decision.why or ''}"], commentary_mode=commentary_mode,
                question=bool(decision.ask))
            said = line if result.startswith("Said") else None
        remembered = remember(db, session, decision)
        thought = None
        if decision.why:
            thought = mind.save_thought(db, user_id=user_id, session_id=session_id, level="hard", kind="plan",
                                        text=decision.why, said=said, status="done" if applied else "stale",
                                        topic=topic, follows=decision.follows,
                                        assessment={"progress": decision.progress} if decision.progress else None)
        else:
            db.commit()
        _capture_apply(attempt_id, user_id=user_id, session_id=session_id, decision=decision, outcome=outcome,
                       read_revision=read_revision, session=session, report=report, started_at=applied_at,
                       said=said, remembered=remembered, thought_id=getattr(thought, "id", None))
    mind.log_decision({"ts": time.time(), "session": str(session_id), "plan": decision.decision, "outcome": outcome,
              "reasons": reasons, "goal": goal, "why": decision.why, "steps": [vars(s) for s in decision.steps],
              "drop": decision.drop, "rules": decision.rules, "target": decision.target, "ask": decision.ask,
              "say": decision.say, "said": bool(said), "follows": decision.follows, "remembered": remembered,
              "sees": decision.sees, "cannot_see": decision.cannot_see, "progress": decision.progress,
              "ms": decision.latency_ms, "input_tokens": decision.input_tokens})
    return outcome


# --------------------------------------------------------------------------- #
# Learning capture (docs/learning-system/POST-AUDIT-01-implementation.md)
# --------------------------------------------------------------------------- #

def _attempt_id(job_id: uuid.UUID | None, try_number: int | None) -> uuid.UUID:
    from app.services import learning_capture

    return learning_capture.attempt_id_for(job_id, "planner", try_number)


def _frozen(value):
    import copy

    return copy.deepcopy(value)


def _decision_out(decision) -> dict:
    keep = ("decision", "why", "sees", "cannot_see", "progress", "goal", "target", "drop", "rules", "ask", "say",
            "follows", "user_rules", "lift_rules", "places")
    return {"steps": [vars(step) for step in decision.steps], **{k: getattr(decision, k) for k in keep}}


def _capture_attempt(attempt_id: uuid.UUID, *, user_id, session_id, job_id, try_number, text, reasons, goal, read,
                     read_revision, plan_before, decision, started_at) -> None:
    """The planner's call as asked (frozen before it) and as answered. Never raises: capture never stops a plan."""
    import json
    from datetime import datetime, timezone

    from app.ai.mind.plan import SCHEMA
    from app.services import learning_capture

    try:
        refs = [f"session:{session_id}", *(r["ref"] for r in read.get("lessons") or () if r.get("ref"))]
        if read.get("goal_item_id"):
            refs.append(f"item:{read['goal_item_id']}")
        ctx = learning_capture.Ctx(
            user_id=user_id, attempt_id=attempt_id, trigger="plan_job", game_session_id=session_id,
            conversation_id=read.get("conversation_id"),
            prior_attempt_id=learning_capture.prior_for(job_id, "planner", try_number),
            input_refs=tuple(read.get("lessons") or ()),
            sources=tuple(refs))
        answered = decision.raw is not None
        status = "ok" if decision.decision is not None else "invalid" if answered else "unavailable"
        learning_capture.attempt(
            ctx, learner="planner", provider="openai", model=decision.model,
            input={"system": None, "schema": None, "text": text, "reasons": list(reasons), "goal": goal,
                   "plan": {"revision": read_revision, "before": plan_before},
                   "job": {"id": str(job_id) if job_id else None, "try": try_number},
                   "goal_item_id": str(read["goal_item_id"]) if read.get("goal_item_id") else None},
            blobs={"system": decision.system or "", "schema": json.dumps(SCHEMA, sort_keys=True)},
            output={"raw": decision.raw, "parsed": _decision_out(decision) if decision.decision else None,
                    "usage": {"latency_ms": decision.latency_ms, "input_tokens": decision.input_tokens,
                              "output_tokens": decision.output_tokens}} if answered else None,
            status=status, error=decision.error, started_at=started_at,
            produced_at=datetime.now(timezone.utc) if answered else None,
            settings=dict(decision.settings or {}),
            versions={"schema": "plan_decision", "code": learning_capture.code_version()})
    except Exception:  # noqa: BLE001
        log.exception("planning: the planner's attempt was not captured")


_APPLY_RESULT = (("dropped", "not_executed"), ("skipped", "not_executed"), ("nothing", "not_executed"),
                 ("refused", "failed"))


def _capture_apply(attempt_id: uuid.UUID, *, user_id, session_id, decision, outcome: str, read_revision: int,
                   session, report: dict, started_at, said=None, remembered=None, thought_id=None) -> None:
    """What code did with the decision: the plan revision it made, the steps and rules applied next to the ones
    proposed. ``deviated`` when code applied something the planner didn't propose (rules carried from the plan
    before or from memory, unlisted steps kept, steps over the limit cut). Never raises."""
    from datetime import datetime, timezone

    from app.services import learning_capture

    try:
        result = next((r for prefix, r in _APPLY_RESULT if outcome.startswith(prefix)), "succeeded")
        plan_after = _frozen(session.plan) if session is not None else None
        revision_after = session.plan_revision if session is not None else None
        rules = report.get("rules") or {}
        steps = report.get("steps") or {}
        deviation = []
        if result == "succeeded":
            if rules.get("carried_by_code"):
                deviation.append("rules applied that the planner didn't propose: "
                                 + "; ".join(f"{r['rule']} (from {', '.join(r['from']) or 'unknown'})"
                                             for r in rules.get("applied", []) if r["rule"] in rules["carried_by_code"]))
            if rules.get("left_out_by_limit"):
                deviation.append("rules left out by the limit: " + "; ".join(rules["left_out_by_limit"]))
            if steps.get("kept_unlisted"):
                deviation.append("open steps the planner didn't list kept at the end: "
                                 + ", ".join(steps["kept_unlisted"]))
            if steps.get("cut_by_limit"):
                deviation.append(f"{len(steps['cut_by_limit'])} steps cut by the plan's step limit")
            if deviation:
                result = "deviated"
        else:
            deviation.append(outcome)
        learning_capture.execution(
            user_id=user_id, kind="plan_apply", executor="game_planning.apply", result=result, attempt_id=attempt_id,
            game_session_id=session_id,
            intended={"decision": decision.decision, "goal": decision.goal, "target": decision.target,
                      "steps": [vars(step) for step in decision.steps], "drop": decision.drop,
                      "rules": decision.rules, "read_revision": read_revision},
            executed={"outcome": outcome, "plan_revision_before": read_revision, "plan_revision_after": revision_after,
                      "plan_after": plan_after, "steps": steps or None, "rules": rules or None, "said": said,
                      "remembered": remembered, "thought_id": str(thought_id) if thought_id else None},
            deviation="; ".join(deviation)[:1000] or None, started_at=started_at, ended_at=datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        log.exception("planning: what was applied was not captured")


def _said_by_user(db: Session, user_id: uuid.UUID, words: str, since) -> uuid.UUID | None:
    """The user's message these words come from (said since the outcome was set), or None: a rule or place
    is kept as the user's only when they really said it."""
    from app.db.models import ChatMessage, Conversation

    wanted = " ".join(words.split()).lower().strip(' "\'.')
    if len(wanted) < 3:
        return None
    rows = db.execute(select(ChatMessage.id, ChatMessage.content).join(
        Conversation, Conversation.id == ChatMessage.conversation_id).where(
        Conversation.user_id == user_id, ChatMessage.role == "user", ChatMessage.created_at >= since,
    ).order_by(ChatMessage.created_at.desc()).limit(200)).all()
    for message_id, content in rows:
        if wanted in " ".join(str(content).split()).lower():
            return message_id
    return None


def remember(db: Session, session: DesktopSession, decision) -> list[str]:
    """The user's rules and places the planner named, kept under the outcome (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md).
    Which layer a correction changes is the planner's call; code only checks the user really said it."""
    from app.services import memory_manager

    goal = memory_manager.open_goal_of_session(db, session.id)
    if goal is None or not (decision.user_rules or decision.lift_rules or decision.places):
        return []
    done: list[str] = []
    try:
        with db.begin_nested():
            for rule, words in decision.user_rules:
                message_id = _said_by_user(db, session.owner_user_id, words, goal.created_at)
                if message_id is None:
                    done.append(f"rule not kept (words not found): {rule}"[:200])
                    continue
                entry = memory_manager.add_constraint(
                    db, session.owner_user_id, goal, rule, user_words=words,
                    source=memory_manager.Source("message", message_id))
                done.append(f"rule {memory_manager.ref(entry)}: {rule}"[:200])
            for ref in decision.lift_rules:
                try:
                    memory_manager.lift_constraint(db, session.owner_user_id, ref,
                                                   reason=decision.why or "the user took it back")
                    done.append(f"lifted {ref}")
                except memory_manager.MemoryError as exc:
                    done.append(f"not lifted {ref}: {exc.message}"[:200])
            for name, where, words in decision.places:
                message_id = _said_by_user(db, session.owner_user_id, words, goal.created_at) if words else None
                memory_manager.add_reference(db, session.owner_user_id, goal, name, where=where, words=words,
                                             message_id=message_id)
                done.append(f"place: {name}"[:200])
    except Exception:  # noqa: BLE001 - memory never gets in the way of the plan
        log.exception("planning: rules/places not kept for session %s", session.id)
        return ["failed"]
    return done
