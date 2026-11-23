"""What each of her parts has been doing lately, for the System page's details panel (owner only).

Read-only and built on every request from the records the parts already keep; nothing is stored here. Every
feed is one user's (the owner's own activity) and newest first. Entries carry what the part really produced:
parts that reason show their reasoning (hard thoughts), parts that only decide show the decision, and nothing is
filled in where a record has none.

Learning's feed here is only what the learning API doesn't serve (capture gaps); the page reads the rest there.

``highlights`` is the page's watch log: what woke her mind and what she did, what she set to come back to, what she
thought, what memory captured and what her hands are after, newest first. ``errors`` gathers what went wrong across the parts.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CaptureGap, ChatMessage, Conversation, CoordinatorWork, DecisionAttempt, DecisionExecution, DesktopSession,
    GameExperience, MemoryEvent, MemoryJob, MemoryLesson, MemoryLessonUse, MindThought, Observation,
    ObservationSource, PhoneActivity, PhoneAffinity, PhoneContactBoundary, PhoneConversation, PhoneExchange,
    PhoneOutbound, PhonePerson, PushDelivery, SemanticMemory, WorkingItem,
)
from app.services.heard import heard_text

LIMIT = 15
PARTS = ("mouth", "mind", "hands", "memory", "observation", "learning", "phone")


def _iso(at: datetime | None) -> str | None:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _clip(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _compact(value: Any, n: int = 400) -> str | None:
    if value in (None, "", {}, []):
        return None
    if isinstance(value, str):
        return _clip(value, n)
    return _clip(json.dumps(value, ensure_ascii=False, default=str), n)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 2)


# --------------------------------------------------------------------------- #
# Per part
# --------------------------------------------------------------------------- #

def _mouth(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(
        select(ChatMessage, Conversation.title).join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc()).limit(LIMIT)).all()
    replies = []
    for msg, title in rows:
        heard = msg.heard_chars is not None
        replies.append({"id": str(msg.id), "at": _iso(msg.created_at), "conversation": title,
                        "channel": msg.source_channel, "status": msg.status,
                        "text": _clip(heard_text(msg.content, msg.heard_chars), 600),
                        "interrupted": heard})
    return {"replies": replies}


def _evaluation_out(a: DecisionAttempt) -> dict[str, Any]:
    """One evaluation of her mind as recorded: why she woke, what memory gave her, the tools she used (with what
    each returned), and what she did about speaking."""
    out, inp = a.output or {}, a.input or {}
    memory = inp.get("memory") or {}
    tools = [{"tool": t.get("tool"), "result": _clip(t.get("result"), 200)}
             for t in out.get("tools") or [] if isinstance(t, dict)][:20]
    return {"id": str(a.id), "at": _iso(a.started_at or a.captured_at), "status": a.status,
            "error": _clip(a.error, 200), "model": a.model, "turn_id": str(a.turn_id) if a.turn_id else None,
            "seconds": _seconds(a.started_at, a.produced_at), "reason": inp.get("reason") or a.trigger,
            "message": _clip(inp.get("message"), 300), "wake": _clip(inp.get("wake"), 500),
            "memory": memory.get("status"), "memory_records": len(memory.get("refs") or []),
            "lessons": len(inp.get("lessons") or []), "tools": tools,
            "silent": _clip(out.get("silent"), 200), "said": _clip(out.get("reply"), 400),
            "thinking_started": len(out.get("thinking_started") or []),
            "scheduled": len(out.get("scheduled") or []), "proposal": out.get("proposal")}


def _thought_out(t: MindThought) -> dict[str, Any]:
    from app.services.effort import judged

    return {"id": str(t.id), "at": _iso(t.created_at), "level": t.level, "kind": t.kind, "trigger": t.trigger,
            "conclusion": t.conclusion_kind, "status": t.status, "source": t.source, "topic": _clip(t.topic, 120),
            "text": _clip(t.text, 600), "thinking": _clip(t.thinking, 1500), "question": _clip(t.question, 300),
            "for_hands": _clip(t.for_hands, 300), "follows": t.follows,
            "judged": (judged(t.assessment).strip(" []") or None), "said": bool(t.said),
            "dropped": _clip(t.dropped_reason, 200)}


def _lesson_uses(db: Session, user_id: uuid.UUID, consumers: tuple[str, ...], limit: int = 10) -> list[dict[str, Any]]:
    """The lessons memory gave a decision maker lately, and what it said it did with each (its own account)."""
    rows = db.execute(
        select(MemoryLessonUse, MemoryLesson.statement, MemoryLesson.status)
        .join(MemoryLesson, MemoryLesson.id == MemoryLessonUse.lesson_id)
        .where(MemoryLessonUse.user_id == user_id, MemoryLessonUse.consumer.in_(consumers))
        .order_by(MemoryLessonUse.created_at.desc()).limit(limit)).all()
    return [{"id": u.id, "at": _iso(u.created_at), "consumer": u.consumer, "lesson": _clip(statement, 300),
             "lesson_status": status, "revision": u.revision, "received": u.received,
             "reason": _clip(u.reason, 200), "reported": u.reported, "why": _clip(u.reported_why, 300)}
            for u, statement, status in rows]


def _plan_out(a: DecisionAttempt, applied: DecisionExecution | None) -> dict[str, Any]:
    parsed = (a.output or {}).get("parsed") or {}
    steps = parsed.get("steps") or []
    executed = (applied.executed or {}) if applied else {}
    return {"id": str(a.id), "at": _iso(a.started_at or a.captured_at), "status": a.status, "error": _clip(a.error, 200),
            "seconds": _seconds(a.started_at, a.produced_at), "reasons": (a.input or {}).get("reasons"),
            "decision": parsed.get("decision"), "why": _clip(parsed.get("why"), 500),
            "progress": parsed.get("progress"), "sees": _clip(parsed.get("sees"), 400),
            "cannot_see": _clip(parsed.get("cannot_see"), 300), "goal": _clip(parsed.get("goal"), 300),
            "steps": [_clip(st.get("do"), 160) for st in steps][:8], "say": _clip(parsed.get("say"), 300),
            "ask": _clip(parsed.get("ask"), 300),
            "applied": None if applied is None else {
                "result": applied.result, "deviation": _clip(applied.deviation, 500),
                "outcome": _clip(executed.get("outcome"), 200),
                "rules": [{"rule": _clip(r.get("rule"), 200), "from": r.get("from") or []}
                          for r in ((executed.get("rules") or {}).get("applied") or [])][:8],
            }}


def _mind(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    from app.services import mind_state

    evaluations = db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                           DecisionAttempt.learner == "mind")
                             .order_by(DecisionAttempt.captured_at.desc()).limit(LIMIT)).all()
    thoughts = db.scalars(select(MindThought).where(MindThought.user_id == user_id)
                          .order_by(MindThought.created_at.desc()).limit(LIMIT * 2)).all()
    planner = db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                       DecisionAttempt.learner == "planner")
                         .order_by(DecisionAttempt.captured_at.desc()).limit(10)).all()
    applied = {e.attempt_id: e for e in db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == user_id, DecisionExecution.kind == "plan_apply",
        DecisionExecution.attempt_id.in_([a.id for a in planner] or [uuid.uuid4()])))}
    continuity = mind_state.view(db, user_id, limit=LIMIT)
    return {
        "evaluations": [_evaluation_out(a) for a in evaluations],
        "continuity": {key: [{k: (_iso(v) if isinstance(v, datetime) else v) for k, v in row.items()}
                             for row in rows] for key, rows in continuity.items()},
        "thoughts": [_thought_out(t) for t in thoughts],
        "plans": [_plan_out(a, applied.get(a.id)) for a in planner],
        "lessons": _lesson_uses(db, user_id, ("planner", "mind")),
    }


def _counts_change(before: dict | None, after: dict | None) -> str | None:
    """Her inventory before and after a run, as code counted it: "coal +3, torch -1"."""
    a, b = (before or {}).get("inventory"), (after or {}).get("inventory")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    diff = [(k, (b.get(k) or 0) - (a.get(k) or 0)) for k in sorted(set(a) | set(b))]
    bits = [f"{k} {'+' if d > 0 else ''}{d}" for k, d in diff if d]
    return ", ".join(bits[:8]) or "no change"


def _state_line(state: dict | None) -> str | None:
    if not state:
        return None
    pos = state.get("position")
    where = (f"at {', '.join(str(round(v)) for v in (pos.get(k) for k in ('x', 'y', 'z')) if v is not None)}"
             if isinstance(pos, dict) else None)
    bits = [where, state.get("health") is not None and f"health {state['health']}",
            state.get("food") is not None and f"food {state['food']}",
            state.get("held") and f"holding {state['held'] if isinstance(state['held'], str) else _compact(state['held'], 40)}"]
    return " · ".join(b for b in bits if b) or None


def _run_out(e: DecisionExecution) -> dict[str, Any]:
    x = e.executed or {}
    observed = x.get("observed") or {}
    before, after = observed.get("before"), observed.get("after")
    return {"result": e.result, "pack_status": x.get("status"), "reason": _clip(x.get("reason"), 300),
            "seconds": x.get("seconds"), "deviation": _clip(e.deviation, 300),
            "before": _state_line(before), "after": _state_line(after), "inventory": _counts_change(before, after),
            "observed_note": observed.get("note")}


def _skill_line(intended: dict | None) -> str | None:
    if not intended or not intended.get("skill"):
        return None
    params = {k: v for k, v in (intended.get("params") or {}).items() if k != "time_limit"}
    return intended["skill"] + (f" {_compact(params, 160)}" if params else "")


def _game_goal(db: Session, user_id: uuid.UUID) -> dict[str, Any] | None:
    """Her latest game session: the goal it plays for, the plan and its steps, and her effort counted by code."""
    from app.services import effort as effort_service

    session = db.scalar(select(DesktopSession).where(DesktopSession.owner_user_id == user_id)
                        .order_by(DesktopSession.started_at.desc()).limit(1))
    if session is None:
        return None
    goal = db.get(WorkingItem, session.goal_item_id) if session.goal_item_id else None
    plan = session.plan or {}
    out: dict[str, Any] = {
        "session_id": str(session.id), "state": session.state, "driver": session.driver_status,
        "started_at": _iso(session.started_at), "ended_at": _iso(session.ended_at),
        "goal": None if goal is None else {"text": _clip(goal.text, 400), "status": goal.status,
                                           "reason": _clip(goal.status_reason, 200), "owner": goal.owner},
        "plan": {"goal": _clip(plan.get("goal"), 300), "target": _clip(_compact(plan.get("target"), 200), 200),
                 "revision": session.plan_revision,
                 "steps": [{"id": st.get("id"), "do": _clip(st.get("do"), 200), "done_when": _clip(st.get("done_when"), 200),
                            "state": st.get("state")} for st in plan.get("steps") or []][:16]} if plan else None,
        "effort": None,
    }
    try:
        with db.begin_nested():
            e = effort_service.for_session(db, session)
        if e is not None:
            out["effort"] = {"active_s": round(e.active_s), "paused_s": round(e.paused_s),
                             "approach_changes": e.approach_changes, "status_note": e.status_note,
                             "rules": [{"ref": r, "rule": _clip(rule, 200), "words": _clip(w, 200)} for r, rule, w in e.rules],
                             "places": [{"name": n, "where": w} for n, w in e.places][:8],
                             "counted": list(e.counted)[:8], "spots": _clip(e.spots, 300)}
    except Exception:  # noqa: BLE001 - the goal still shows without the effort
        pass
    return out


def _desktop_out(x: DecisionExecution) -> dict[str, Any]:
    """One action of her hands on her own desktop (services/desktop_control.remember), as code recorded it."""
    intended, executed = x.intended or {}, x.executed or {}
    front = executed.get("foreground") or {}
    args = {k: v for k, v in intended.items() if k != "tool"}
    return {"id": str(x.id), "at": _iso(x.ended_at or x.captured_at), "tool": intended.get("tool"),
            "args": _compact(args, 200), "result": x.result, "status": executed.get("status"),
            "app": _clip(front.get("process"), 64), "window": _clip(front.get("title"), 160),
            "screen": _clip(executed.get("screen"), 300) if isinstance(executed.get("screen"), str) else None,
            "deviation": _clip(x.deviation, 300), "turn_id": str(x.turn_id) if x.turn_id else None}


def _hands(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    picks = db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                     DecisionAttempt.learner == "hands")
                       .order_by(DecisionAttempt.captured_at.desc()).limit(LIMIT)).all()
    runs = {e.attempt_id: e for e in db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == user_id, DecisionExecution.kind == "skill_run",
        DecisionExecution.attempt_id.in_([a.id for a in picks] or [uuid.uuid4()])))}
    checks = db.scalars(select(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                                        DecisionExecution.kind == "step_check")
                        .order_by(DecisionExecution.captured_at.desc()).limit(10)).all()
    outcomes = db.scalars(select(GameExperience).where(GameExperience.user_id == user_id)
                          .order_by(GameExperience.id.desc()).limit(LIMIT)).all()
    events = []
    for e in outcomes:
        p = e.payload or {}
        if e.kind == "skill_outcome":
            line = f"{p.get('skill')} — {p.get('status')}" + (f": {p['reason']}" if p.get("reason") else "")
        else:
            line = p.get("text") or p.get("kind") or e.kind
        events.append({"id": e.id, "at": _iso(e.occurred_at), "kind": e.kind, "game": e.game,
                       "text": _clip(line, 300), "seconds": p.get("seconds"),
                       "failed": p.get("status") not in (None, "done", "stopped") if e.kind == "skill_outcome" else False})
    decisions = []
    for a in picks:
        answer = (a.output or {}).get("output") or {}
        action = (a.output or {}).get("action")
        step = ((a.input or {}).get("plan") or {}).get("step") or {}
        run = runs.get(a.id)
        decisions.append({
            "id": str(a.id), "at": _iso(a.started_at or a.captured_at), "status": a.status, "error": _clip(a.error, 200),
            "seconds": _seconds(a.started_at, a.produced_at), "step": _clip(step.get("do"), 200),
            "claim": answer.get("status"), "note": _clip(answer.get("note"), 500),
            "action": _skill_line({"skill": action[0], "params": action[1] if len(action) > 1 else {}})
            if isinstance(action, list) and action else None,
            "weighed": [{"lesson": w.get("lesson"), "use": w.get("use"), "why": _clip(w.get("why"), 200)}
                        for w in answer.get("lessons_weighed") or [] if isinstance(w, dict)][:6],
            "run": _run_out(run) if run is not None else None,
        })
    desktop = db.scalars(select(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                                         DecisionExecution.kind == "desktop_action")
                         .order_by(DecisionExecution.captured_at.desc()).limit(LIMIT)).all()
    return {
        "goal": _game_goal(db, user_id),
        "desktop": [_desktop_out(x) for x in desktop],
        "decisions": decisions,
        "checks": [{"id": str(c.id), "at": _iso(c.ended_at or c.captured_at), "do": _clip((c.intended or {}).get("do"), 200),
                    "done_when": _clip((c.intended or {}).get("done_when"), 200),
                    "counted": _clip((c.executed or {}).get("counted"), 300)} for c in checks],
        "lessons": _lesson_uses(db, user_id, ("hands",)),
        "outcomes": events,
    }


def _memory(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    items = db.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None))
                       .order_by(WorkingItem.updated_at.desc()).limit(LIMIT)).all()
    events = db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id)
                        .order_by(MemoryEvent.recorded_at.desc()).limit(LIMIT)).all()
    facts = db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == user_id)
                       .order_by(SemanticMemory.created_at.desc()).limit(LIMIT)).all()
    lessons = db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id)
                         .order_by(MemoryLesson.updated_at.desc()).limit(LIMIT)).all()
    uses = dict(db.execute(select(MemoryLessonUse.lesson_id, func.count()).where(
        MemoryLessonUse.user_id == user_id, MemoryLessonUse.received.is_(True),
        MemoryLessonUse.lesson_id.in_([r.id for r in lessons] or [uuid.uuid4()]))
        .group_by(MemoryLessonUse.lesson_id)).all())
    pending = db.scalar(select(func.count()).select_from(MemoryJob).where(
        MemoryJob.user_id == user_id, MemoryJob.status.in_(("pending", "processing", "retry"))))
    return {
        "pending_jobs": pending or 0,
        "open": [{"id": str(i.id), "kind": i.kind, "owner": i.owner, "status": i.status, "text": _clip(i.text, 300),
                  "at": _iso(i.updated_at)} for i in items],
        "events": [{"id": e.id, "kind": e.kind, "epistemic": e.epistemic, "text": _clip(e.text, 300),
                    "at": _iso(e.recorded_at)} for e in events],
        "facts": [{"id": str(f.id), "kind": f.kind, "status": f.status, "text": _clip(f.content, 300),
                   "confidence": round(f.confidence, 2), "revision": f.revision, "at": _iso(f.created_at)}
                  for f in facts],
        "lessons": [{"id": str(r.id), "kind": r.kind, "scope": r.scope, "status": r.status, "origin": r.origin,
                     "statement": _clip(r.statement, 400), "applies_when": _clip(r.applies_when, 300),
                     "reason": _clip(r.status_reason, 200), "revision": r.revision, "used": uses.get(r.id, 0),
                     "at": _iso(r.updated_at)} for r in lessons],
    }


def _learning(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    gaps = db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id)
                      .order_by(CaptureGap.detected_at.desc()).limit(LIMIT)).all()
    return {"gaps": [{"id": g.id, "at": _iso(g.detected_at), "kind": g.record_kind, "label": g.label,
                      "reason": g.reason, "tries": g.tries, "detail": _clip(g.detail, 300)} for g in gaps]}


def _observation(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    sources = db.scalars(select(ObservationSource).where(ObservationSource.user_id == user_id)).all()
    recent = db.scalars(select(Observation).where(Observation.user_id == user_id)
                        .order_by(Observation.received_at.desc()).limit(LIMIT)).all()
    return {
        "sources": [{"source": s.source, "instance": s.source_instance, "availability": s.availability,
                     "last_received_at": _iso(s.last_received_at), "error": _clip(s.last_error, 200)}
                    for s in sources],
        "reports": [{"id": o.id, "source": o.source, "kind": o.kind, "salience": o.salience,
                     "text": _clip(o.summary, 300), "at": _iso(o.received_at)} for o in recent],
    }


def _person_name(p: PhonePerson | None) -> str | None:
    if p is None:
        return None
    return p.display_name or (p.names[0] if p.names else None)


def _phone_checks(user_id: uuid.UUID, limit: int = LIMIT) -> list[dict[str, Any]]:
    """The newest outcomes of the phone's checks for this user (services/phone_reply_check.trace: her replies to
    others, messages the user asked her to send, phone operations on their turns, grants), from the end of the log."""
    import os

    from app.core.config import get_settings

    path = get_settings().phone_check_log_path
    if not path:
        return []
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), path)
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    me, out = str(user_id), []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue  # the first line of the window may be cut
        if r.get("user") != me:
            continue
        check = r.get("check")
        ok = r.get("send") if check in ("reply", "draft") else r.get("allowed") if check == "operation" else r.get("stands")
        what = (r.get("operation") if check == "operation"
                else f"conversation {r['conversation']}" if r.get("conversation") else None)
        out.append({"at": r.get("at"), "check": check, "kind": r.get("kind"), "ok": ok, "what": _clip(what, 200),
                    "why": _clip(r.get("why"), 300), "reveals": [_clip(x, 160) for x in r.get("reveals") or []][:4]})
        if len(out) >= limit:
            break
    return out


def _phone(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    from app.services import phone_notifications, phone_state

    now = time.time()
    try:
        state = phone_state.read(user_id, now=now)
    except Exception:  # noqa: BLE001 - an unreadable Redis means the page can't tell, never a broken page
        state = None
    try:
        notes = phone_notifications.live(user_id, now=now)
    except Exception:  # noqa: BLE001
        notes = None
    sent = db.scalars(select(PhoneOutbound).where(PhoneOutbound.user_id == user_id)
                      .order_by(PhoneOutbound.created_at.desc()).limit(LIMIT)).all()
    convs = {c.conversation_id: c for c in db.scalars(select(PhoneConversation).where(
        PhoneConversation.user_id == user_id,
        PhoneConversation.conversation_id.in_([o.conversation_id for o in sent if o.conversation_id] or [""])))}
    people = db.scalars(select(PhonePerson).where(PhonePerson.user_id == user_id)
                        .order_by(PhonePerson.last_seen_at.desc()).limit(LIMIT)).all()
    exchanges = db.scalars(select(PhoneExchange).where(PhoneExchange.user_id == user_id)
                           .order_by(PhoneExchange.at.desc()).limit(LIMIT)).all()
    space = db.scalars(select(PhoneContactBoundary).where(PhoneContactBoundary.user_id == user_id,
                                                          PhoneContactBoundary.lifted_at.is_(None))
                       .order_by(PhoneContactBoundary.created_at.desc()).limit(LIMIT)).all()
    person_ids = {o.person_id for o in sent} | {e.person_id for e in exchanges} | {b.person_id for b in space}
    known = {p.id: p for p in people}
    known.update({p.id: p for p in db.scalars(select(PhonePerson).where(
        PhonePerson.id.in_([i for i in person_ids if i is not None and i not in known] or [uuid.uuid4()])))})
    affinity = {a.person_id: a for a in db.scalars(select(PhoneAffinity).where(
        PhoneAffinity.user_id == user_id, PhoneAffinity.person_id.in_([p.id for p in people] or [uuid.uuid4()])))}
    conv_titles = {c.id: c.title for c in db.scalars(select(PhoneConversation).where(
        PhoneConversation.id.in_([e.conversation_id for e in exchanges] + [b.conversation_id for b in space]
                                 or [uuid.uuid4()])))}
    actions = db.scalars(select(PhoneActivity).join(Conversation, Conversation.id == PhoneActivity.conversation_id)
                         .where(Conversation.user_id == user_id)
                         .order_by(PhoneActivity.created_at.desc()).limit(LIMIT)).all()
    pushes = db.scalars(select(PushDelivery).where(PushDelivery.user_id == user_id)
                        .order_by(PushDelivery.queued_at.desc()).limit(LIMIT)).all()
    return {
        "senses": phone_state.describe(state, now) if state is not None else [],
        "notifications": None if notes is None else [
            {"key": n.key, "app": n.app, "conversation": _clip(n.conversation or n.title, 120), "group": n.is_group,
             "can_reply": n.can_reply, "messages": len(n.messages), "at": n.posted_at} for n in notes[:LIMIT]],
        "replies": [{"id": str(o.id), "at": _iso(o.created_at), "to": _person_name(known.get(o.person_id)),
                     "conversation": (c.title if (c := convs.get(o.conversation_id)) else None),
                     "asked_by": o.asked_by, "status": o.status, "echoed": o.echo_event_id is not None,
                     "text": _clip(o.text, 600)} for o in sent],
        "checks": _phone_checks(user_id),
        "people": [{"id": str(p.id), "name": _person_name(p), "contact": p.in_contacts, "status": p.status,
                    "at": _iso(p.last_seen_at),
                    "familiarity": (a := affinity.get(p.id)) and a.familiarity, "warmth": a and a.warmth,
                    "initiative": a and a.initiative, "note": a and _clip(a.note, 300)} for p in people],
        "exchanges": [{"id": str(e.id), "at": _iso(e.at), "person": _person_name(known.get(e.person_id)),
                       "conversation": conv_titles.get(e.conversation_id), "addressed": e.addressed,
                       "participated": e.participated, "observation": _clip(e.observation, 300),
                       "their_words": _clip(e.their_words, 300), "lesson": e.lesson,
                       "lesson_why": _clip(e.lesson_why, 300)} for e in exchanges],
        "space": [{"id": str(b.id), "at": _iso(b.created_at), "person": _person_name(known.get(b.person_id)),
                   "conversation": conv_titles.get(b.conversation_id), "scope": b.scope, "topic": _clip(b.topic, 160),
                   "words": _clip(b.words, 300)} for b in space],
        "actions": [{"id": str(a.id), "at": _iso(a.created_at), "text": _clip(a.content, 300)} for a in actions],
        "pushes": [{"id": str(r.id), "at": _iso(r.finished_at or r.queued_at), "kind": r.kind, "status": r.status,
                    "attempts": r.attempts, "error": _clip(r.last_error, 200)} for r in pushes],
    }


_FEEDS = {"mouth": _mouth, "mind": _mind, "hands": _hands, "memory": _memory,
          "observation": _observation, "learning": _learning, "phone": _phone}


def feed(db: Session, user_id: uuid.UUID, part: str) -> dict[str, Any] | None:
    """One part's recent activity for ``user_id`` (None for a part with no feed here)."""
    build = _FEEDS.get(part)
    return None if build is None else build(db, user_id)


# --------------------------------------------------------------------------- #
# The watch log and the errors
# --------------------------------------------------------------------------- #

WATCH_LIMIT = 60


def highlights(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """What the owner watches most, as one log, newest first: what she thought (mind), what memory captured
    (events, facts, lessons) and her hands' goal (plan changes, steps counted done, skill runs). Real records only;
    each item says which part it came from, and its anchor on that part's page."""
    items: list[dict[str, Any]] = []

    def add(part: str, kind: str, at: str | None, title: str, text: str | None, *, anchor: str, ref: str,
            tone: str | None = None, detail: str | None = None) -> None:
        if at:
            items.append({"id": f"{part}:{kind}:{ref}", "part": part, "kind": kind, "at": at, "title": title,
                          "text": text, "detail": detail, "anchor": anchor, "tone": tone})

    for t in db.scalars(select(MindThought).where(MindThought.user_id == user_id)
                        .order_by(MindThought.created_at.desc()).limit(25)):
        o = _thought_out(t)
        what = "plan" if t.kind == "plan" else f"{t.level} thought"
        add("mind", "thought", o["at"], " · ".join(b for b in (what, o["conclusion"], o["judged"]) if b), o["text"],
            anchor="planner" if t.kind == "plan" else ("hard" if t.level == "hard" else "quick"), ref=o["id"],
            tone="muted" if t.status in ("dropped", "stale") else None,
            detail=o["thinking"])
    for a in db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                      DecisionAttempt.learner == "mind")
                        .order_by(DecisionAttempt.captured_at.desc()).limit(20)):
        o = _evaluation_out(a)
        did = ("stayed silent" if o["silent"] else "spoke" if o["said"] else "wrote nothing")
        extras = [f"started thinking" if o["thinking_started"] else None,
                  f"scheduled {o['scheduled']}" if o["scheduled"] else None,
                  f"proposal: {o['proposal']}" if o["proposal"] else None]
        add("mind", "evaluation", o["at"], " · ".join(b for b in (f"woke: {o['reason']}", did, *extras) if b),
            o["silent"] or o["said"] or o["message"], anchor="evaluations", ref=o["id"],
            tone="error" if a.status != "ok" else None,
            detail=", ".join(t["tool"] for t in o["tools"] if t["tool"]) or None)
    for w in db.scalars(select(CoordinatorWork).where(CoordinatorWork.user_id == user_id,
                                                      CoordinatorWork.kind == "wake")
                        .order_by(CoordinatorWork.created_at.desc()).limit(10)):
        add("mind", "scheduled", _iso(w.created_at), f"will come back · {w.status}",
            _clip(w.input.get("about"), 300), anchor="continuity", ref=str(w.id),
            tone="muted" if w.status in ("cancelled", "superseded") else None,
            detail=f"due {_iso(w.next_attempt_at)}")
    for e in db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id)
                        .order_by(MemoryEvent.recorded_at.desc()).limit(20)):
        add("memory", "event", _iso(e.recorded_at), f"noted · {e.kind.replace('_', ' ')} · {e.epistemic}",
            _clip(e.text, 300), anchor="events", ref=str(e.id))
    for f in db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == user_id)
                        .order_by(SemanticMemory.created_at.desc()).limit(15)):
        add("memory", "fact", _iso(f.created_at), f"fact · {f.kind}", _clip(f.content, 300), anchor="facts",
            ref=str(f.id), tone="muted" if f.status != "active" else None)
    for r in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id)
                        .order_by(MemoryLesson.updated_at.desc()).limit(10)):
        add("memory", "lesson", _iso(r.updated_at), f"lesson · {r.status} · rev {r.revision}", _clip(r.statement, 300),
            anchor="lessons", ref=f"{r.id}:{r.revision}", tone="muted" if r.status == "withdrawn" else None)
    for x in db.scalars(select(DecisionExecution).where(
            DecisionExecution.user_id == user_id,
            DecisionExecution.kind.in_(("skill_run", "step_check", "plan_apply")))
            .order_by(DecisionExecution.captured_at.desc()).limit(30)):
        at = _iso(x.ended_at or x.captured_at)
        if x.kind == "skill_run":
            run = _run_out(x)
            add("hands", "run", at, f"ran · {x.result}" + (f" · {run['seconds']} s" if run["seconds"] else ""),
                _skill_line(x.intended) or "a skill",
                anchor="decisions", ref=str(x.id), tone="error" if x.result == "failed" else None,
                detail=" · ".join(b for b in (run["reason"], run["inventory"] and f"inventory: {run['inventory']}") if b) or None)
        elif x.kind == "step_check":
            add("hands", "step", at, "step counted done", _clip((x.intended or {}).get("do"), 300), anchor="checks",
                ref=str(x.id), detail=_clip((x.executed or {}).get("counted"), 300))
        else:
            plan = ((x.executed or {}).get("plan_after") or {})
            add("hands", "plan", at, f"plan applied · {x.result}", _clip(plan.get("goal") or (x.intended or {}).get("goal"), 300),
                anchor="goal", ref=str(x.id), tone="error" if x.result == "failed" else None,
                detail=_clip(x.deviation, 300))
    for x in db.scalars(select(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                                        DecisionExecution.kind == "desktop_action")
                        .order_by(DecisionExecution.captured_at.desc()).limit(15)):
        d = _desktop_out(x)
        add("hands", "desktop", d["at"], f"desktop · {d['tool']} · {x.result}",
            " · ".join(b for b in (d["args"], d["app"] and f"in {d['app']}") if b) or None, anchor="desktop",
            ref=d["id"], tone="error" if x.result == "failed" else None, detail=d["deviation"])
    sent = db.scalars(select(PhoneOutbound).where(PhoneOutbound.user_id == user_id)
                      .order_by(PhoneOutbound.created_at.desc()).limit(10)).all()
    names = {p.id: _person_name(p) for p in db.scalars(select(PhonePerson).where(
        PhonePerson.id.in_([o.person_id for o in sent if o.person_id] or [uuid.uuid4()])))}
    for o in sent:
        add("phone", "reply", _iso(o.created_at),
            " · ".join(b for b in ("reply", names.get(o.person_id) and f"to {names[o.person_id]}",
                                   "she chose to" if o.asked_by == "her" else "you asked", o.status) if b),
            _clip(o.text, 300), anchor="replies", ref=str(o.id), tone="error" if o.status == "failed" else None)
    for n, c in enumerate(_phone_checks(user_id, 10)):
        if c["ok"] is False:
            add("phone", "check", c["at"], f"held by the {c['check']} check", c["why"], anchor="checks",
                ref=f"{c['at']}:{n}", detail=c["what"])
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"goal": _game_goal(db, user_id), "items": items[:WATCH_LIMIT]}


def errors(db: Session, user_id: uuid.UUID, status: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What went wrong lately across her parts, newest first: worker errors (heartbeats), decisions that produced
    nothing valid, executions that failed (not game skills: those are play, on the Hands page), background work
    errors, memory jobs that failed, capture gaps and sources reporting errors."""
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for part in (status or {}).get("parts", []):
        for w in part.get("workers", []):
            if w.get("error"):
                at = (datetime.fromtimestamp(time.time() - (w.get("seen_ago") or 0), timezone.utc)).isoformat()
                out.append({"id": f"worker:{w['name']}", "part": part["id"], "at": at, "what": f"worker {w['name']}",
                            "text": _clip(w["error"], 400)})
    learner_part = {"mind": "mind", "hands": "hands", "planner": "mind", "hard_thought": "mind"}
    for a in db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                      DecisionAttempt.status != "ok")
                        .order_by(DecisionAttempt.captured_at.desc()).limit(20)):
        out.append({"id": f"attempt:{a.id}", "part": learner_part.get(a.learner, "learning"),
                    "at": _iso(a.captured_at), "what": f"{a.learner.replace('_', ' ')} · {a.status}",
                    "text": _clip(a.error, 400) or "no valid answer", "turn_id": str(a.turn_id) if a.turn_id else None,
                    "attempt_id": str(a.id)})
    exec_part = {"reply": "mouth", "heard": "mouth", "channel_delivery": "mouth", "plan_apply": "mind",
                 "thought_route": "mind", "step_check": "hands",
                 "desktop_action": "hands"}
    for x in db.scalars(select(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                                        DecisionExecution.result == "failed",
                                                        DecisionExecution.kind != "skill_run")
                        .order_by(DecisionExecution.captured_at.desc()).limit(20)):
        out.append({"id": f"execution:{x.id}", "part": exec_part.get(x.kind, "mind"),
                    "at": _iso(x.captured_at), "what": f"{x.kind.replace('_', ' ')} failed",
                    "text": _clip(x.deviation, 400), "turn_id": str(x.turn_id) if x.turn_id else None})
    for w in db.scalars(select(CoordinatorWork).where(CoordinatorWork.user_id == user_id,
                                                      CoordinatorWork.error_code.is_not(None),
                                                      # her own stops and moved wakes, and moot thinking, aren't errors
                                                      CoordinatorWork.status.in_(("failed", "expired", "blocked")))
                        .order_by(CoordinatorWork.created_at.desc()).limit(10)):
        part = {"memory": "memory", "observation": "observation"}.get(w.kind, "mind")
        out.append({"id": f"work:{w.id}", "part": part, "at": _iso(w.updated_at or w.created_at),
                    "what": f"background {w.kind} · {w.status}", "text": w.error_code})
    for j in db.scalars(select(MemoryJob).where(MemoryJob.user_id == user_id, MemoryJob.last_error.is_not(None))
                        .order_by(MemoryJob.created_at.desc()).limit(10)):
        out.append({"id": f"memory-job:{j.id}", "part": "memory", "at": _iso(j.processed_at or j.created_at),
                    "what": f"memory job · {j.status} · try {j.attempts}", "text": _clip(j.last_error, 400)})
    for g in db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id)
                        .order_by(CaptureGap.detected_at.desc()).limit(10)):
        out.append({"id": f"gap:{g.id}", "part": "learning", "at": _iso(g.detected_at),
                    "what": f"capture gap · {g.label} · {g.reason.replace('_', ' ')}", "text": _clip(g.detail, 400),
                    "turn_id": str(g.turn_id) if g.turn_id else None})
    for src in db.scalars(select(ObservationSource).where(ObservationSource.user_id == user_id,
                                                          ObservationSource.last_error.is_not(None))):
        out.append({"id": f"source:{src.source}:{src.source_instance}", "part": "observation",
                    "at": _iso(src.last_received_at) or now.isoformat(), "what": f"source {src.source}",
                    "text": _clip(src.last_error, 400)})
    for r in db.scalars(select(PushDelivery).where(PushDelivery.user_id == user_id, PushDelivery.status == "failed")
                        .order_by(PushDelivery.queued_at.desc()).limit(10)):
        out.append({"id": f"push:{r.id}", "part": "phone", "at": _iso(r.finished_at or r.queued_at),
                    "what": f"push · {r.kind} failed", "text": _clip(r.last_error, 400)})
    out.sort(key=lambda e: e["at"] or "", reverse=True)
    return out[:60]


__all__ = ["PARTS", "feed", "highlights", "errors"]
