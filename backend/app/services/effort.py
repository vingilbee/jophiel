"""Her effort in a game session, built from what was recorded (docs/COLUMBINA_EFFORT_AND_STUCK_PLAN.md).

The outcome is the goal the session plays for (working memory); everything else is read from the records
that already exist, so nothing here can drift from what happened:

- the approach: the session's plan and its changes (``desktop.plan_set`` events);
- her conclusions and her planner's decisions: ``mind_thoughts`` of the session, with ``follows`` as she said it;
- her actions: her hands' skill runs (``decision_executions``, learning capture), repeats collapsed;
- what was said: the conversation since the outcome was set;
- pauses and resumes (``desktop.session_state``), stuck and needs-human notes;
- the user's rules and the places that matter (items and entity links under the goal).

Code orders, collapses and counts. It never says which action came from which conclusion, or whether
something is progress. Read-only.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.mind.effort import ActiveTime, Effort, Entry, collapse, render
from app.db.models import (
    ChatMessage,
    Conversation,
    DecisionExecution,
    DesktopSession,
    DesktopSessionEvent,
    MindThought,
    WorkingItem,
)

MAX_ACTIONS = 400
MAX_THOUGHTS = 120
MAX_MESSAGES = 120
SPOT_BLOCKS = 16
SAID_CHARS = 160
_WHOSE = {"user": "the user's", "columbina": "your own", "shared": "shared"}
_PLAN_BY = {"columbina_user_turn": "you, for the user", "columbina_planning_turn": "you, on your own",
            "columbina_mind": "you, revising", "user_api": "the user"}
_COUNTED = re.compile(r"([a-z0-9_]+) ([+-]\d+)")


def short_id(thought_id: uuid.UUID) -> str:
    """How a conclusion or plan of hers is named on the timeline (and in ``follows``)."""
    return "D" + thought_id.hex[:6]


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _params(params: dict) -> str:
    shown = [f"{k}={v}" for k, v in (params or {}).items() if k not in ("time_limit",) and v not in (None, "")]
    return ", ".join(shown)


def _action_entry(row: DecisionExecution) -> Entry | None:
    intended = row.intended or {}
    executed = row.executed or {}
    if row.result == "not_executed":
        return None  # decided but discarded before running: nothing happened in the world
    skill = intended.get("skill") or "?"
    params = intended.get("params") or {}
    detail = _params(params)
    if row.result == "succeeded":
        outcome = "done"
    elif row.result == "unknown":
        outcome = f"result not seen ({row.deviation})" if row.deviation else "result not seen"
    else:
        reason = executed.get("reason") or row.deviation or row.result
        outcome = f"{row.result}: {reason}"
    data = executed.get("data") if isinstance(executed.get("data"), dict) else {}
    counted = f" [{data['counted']}]" if data.get("counted") else ""
    at = _utc(row.started_at) or _utc(row.captured_at)
    # Back-to-back runs of one skill with the same result are one line; what each was given is kept as detail.
    return Entry(at=at, kind="action", text=f"hands: {skill} → {outcome}{counted}",
                 failed=row.result in ("failed", "deviated"),
                 key=f"{skill}|{row.result}|{outcome}|{counted}", until=_utc(row.ended_at),
                 details=(detail,) if detail else ())


def _counted_lines(rows: list[DecisionExecution]) -> tuple[list[str], str | None]:
    """Per skill, the item changes the game counted, side by side; and where her runs ended."""
    by_skill: dict[str, dict[str, int]] = {}
    cells: set[tuple[int, int, int]] = set()
    heights: list[int] = []
    for row in rows:
        executed = row.executed or {}
        data = executed.get("data") if isinstance(executed.get("data"), dict) else {}
        skill = (row.intended or {}).get("skill") or "?"
        for item, delta in _COUNTED.findall(str(data.get("counted") or "")):
            totals = by_skill.setdefault(skill, {})
            totals[item] = totals.get(item, 0) + int(delta)
        if all(isinstance(data.get(axis), (int, float)) for axis in ("x", "y", "z")):
            x, y, z = (int(data[axis]) for axis in ("x", "y", "z"))
            cells.add((x // SPOT_BLOCKS, y // SPOT_BLOCKS, z // SPOT_BLOCKS))
            heights.append(y)
    lines = []
    for skill, totals in by_skill.items():
        changes = [f"{item} {delta:+d}" for item, delta in totals.items() if delta]
        if changes:
            lines.append(f"{skill}: " + ", ".join(changes))
    spots = (f"Where your hands' runs ended: {len(cells)} different spots ({SPOT_BLOCKS}-block areas), "
             f"height {min(heights)} to {max(heights)}.") if heights else None
    return lines, spots


def _conversation(db: Session, user_id: uuid.UUID, since: datetime) -> list[Entry]:
    conversation_id = db.scalar(select(Conversation.id).where(Conversation.user_id == user_id)
                                .order_by(Conversation.updated_at.desc()).limit(1))
    if conversation_id is None:
        return []
    rows = db.execute(select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
                      .where(ChatMessage.conversation_id == conversation_id, ChatMessage.created_at >= since,
                             ChatMessage.content != "")
                      .order_by(ChatMessage.created_at.desc()).limit(MAX_MESSAGES)).all()
    out = []
    for role, content, created in rows:
        if role not in ("user", "assistant"):
            continue
        text = " ".join(str(content).split())[:SAID_CHARS]
        out.append(Entry(at=_utc(created), kind="user" if role == "user" else "said",
                         text=(f'the user: "{text}"' if role == "user" else f'you said: "{text}"')))
    return out


def _thoughts(db: Session, session: DesktopSession, since: datetime) -> list[Entry]:
    rows = db.scalars(select(MindThought).where(
        MindThought.user_id == session.owner_user_id, MindThought.activity_id == session.id,
        MindThought.level == "hard", MindThought.status.in_(("done", "stale")), MindThought.created_at >= since,
    ).order_by(MindThought.created_at.desc()).limit(MAX_THOUGHTS)).all()
    out = []
    for row in rows:
        stale = " (the world had moved on when it finished)" if row.status == "stale" else ""
        if row.kind == "plan":
            out.append(Entry(at=_utc(row.created_at), kind="plan", text=row.text + judged(row.assessment) + stale,
                             ref=short_id(row.id),
                             follows=row.follows))
        else:
            kind = row.conclusion_kind or "thought"
            about = f" (on: {row.topic})" if row.topic else ""
            out.append(Entry(at=_utc(row.created_at), kind="conclusion",
                             text=f"{kind}{about}{judged(row.assessment)}: {row.text}{stale}",
                             ref=short_id(row.id), follows=row.follows))
    return out


def judged(assessment: dict | None) -> str:
    """Her own judgment of the effort then, as she gave it: " [getting anywhere: no; cause: perception;
    check: go up and look]"."""
    if not assessment or not assessment.get("progress"):
        return ""
    parts = [f"getting anywhere: {assessment['progress']}"]
    if assessment.get("cause"):
        parts.append(f"cause: {assessment['cause']}")
    if assessment.get("check"):
        parts.append(f"check: {assessment['check']}")
    return " [" + "; ".join(parts) + "]"


def _session_events(db: Session, session: DesktopSession, since: datetime) -> tuple[list[Entry], ActiveTime, dict]:
    rows = db.scalars(select(DesktopSessionEvent).where(DesktopSessionEvent.session_id == session.id)
                      .order_by(DesktopSessionEvent.id)).all()
    entries: list[Entry] = []
    active = ActiveTime(since=since, running_at_start=True)
    plan = {"changes": 0, "since": None, "goal": None}
    for row in rows:
        at = _utc(row.occurred_at)
        payload = row.payload or {}
        if row.type == "desktop.session_state":
            running = payload.get("state") == "running"
            active.changes.append((at, running))
            if at >= since and payload.get("state") in ("running", "paused"):
                why = payload.get("reason")
                entries.append(Entry(at=at, kind="state", text=f"session {payload['state']}" + (f" ({why})" if why else ""),
                                     key=f"state|{payload['state']}|{why}"))
        elif row.type == "desktop.plan_set":
            goal = payload.get("goal")
            if goal != plan["goal"]:
                if at >= since and plan["goal"] is not None:
                    plan["changes"] += 1
                plan["goal"], plan["since"] = goal, at
                if at >= since:
                    by = _PLAN_BY.get(payload.get("source"), payload.get("source") or "?")
                    entries.append(Entry(at=at, kind="plan", text=f'plan now: "{goal}" (set by {by})'))
        elif row.type in ("desktop.stuck", "desktop.needs_human") and at >= since:
            word = "hands stuck" if row.type == "desktop.stuck" else "hands need the user"
            entries.append(Entry(at=at, kind="note", text=f"{word}: {payload.get('note') or ''}".strip(),
                                 failed=True))
    before = [running for when, running in active.changes if when <= since]
    if before:
        active.running_at_start = before[-1]
    elif session.started_at and _utc(session.started_at) > since:
        active.running_at_start = False  # the session started after the outcome was set
    return entries, active, plan


def for_session(db: Session, session: DesktopSession, *, now: datetime | None = None,
                senses: tuple[str, ...] = (), coverage: tuple[str, ...] = ()) -> Effort | None:
    """The effort the session plays for, or None when it has no goal in memory."""
    from app.services import memory_manager

    goal = db.get(WorkingItem, session.goal_item_id) if session.goal_item_id else None
    if goal is None:
        return None
    now = now or datetime.now(timezone.utc)
    since = _utc(goal.created_at)
    actions = list(db.scalars(select(DecisionExecution).where(
        DecisionExecution.game_session_id == session.id, DecisionExecution.kind == "skill_run",
        DecisionExecution.captured_at >= since,
    ).order_by(DecisionExecution.captured_at.desc()).limit(MAX_ACTIONS)).all())
    events, active, plan_info = _session_events(db, session, since)
    timeline = [*filter(None, (_action_entry(row) for row in actions)), *_thoughts(db, session, since),
                *_conversation(db, session.owner_user_id, since), *events]
    counted, spots = _counted_lines([row for row in actions if row.result != "not_executed"])
    active_s, paused_s = active.split(now)
    plan = session.plan or {}
    step = next((s for s in plan.get("steps") or [] if s.get("state") == "active"), None)
    approach = plan.get("goal") and (plan["goal"] + (f'; current step: {step["do"]}' if step else ""))
    rules = tuple((memory_manager.ref(entry), entry.text, entry.user_words or "")
                  for entry in memory_manager.serving(db, goal)
                  if entry.kind == "constraint" and entry.closed_at is None)
    places = tuple((entity.name or "?", (entity.metadata_ or {}).get("where"))
                   for entity in memory_manager.references(db, goal))
    status = None if goal.status == "active" else f"{goal.status}: {goal.status_reason or 'no reason given'}"
    return Effort(
        outcome=goal.text, whose=_WHOSE.get(goal.owner, goal.owner), outcome_ref=memory_manager.ref(goal),
        since=since, active_s=active_s, paused_s=paused_s, approach=approach,
        approach_since=plan_info["since"] if plan_info["goal"] == plan.get("goal") else None,
        approach_changes=plan_info["changes"], rules=rules, places=places, counted=tuple(counted), spots=spots,
        senses=senses, coverage=coverage, timeline=tuple(collapse(timeline)),
        full_since=active.window_start(now), status_note=status)


def lines_for_session(db: Session, session: DesktopSession, *, user: str = "the user",
                      senses: tuple[str, ...] = (), coverage: tuple[str, ...] = ()) -> tuple[str, ...]:
    now = datetime.now(timezone.utc)
    effort = for_session(db, session, now=now, senses=senses, coverage=coverage)
    return tuple(render(effort, now, user=user)) if effort is not None else ()
