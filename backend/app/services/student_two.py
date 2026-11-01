"""Student 2 live: read her situation from the stores, ask the decision model, carry out what it decides.

The model is whatever ``COGNITION_STUDENT_DIR`` points at: a folder made by ``scripts/student_model.py prepare``,
served by ``scripts/student_model.py serve`` at ``COGNITION_STUDENT_URL``. Its ``student.json`` says how to talk to
it (system prompt, how its training situations were rendered, chat-template switches), so swapping qwen-4 for
qwen-5 is a new folder and a restart of the server, with no code change here.

- ``gather`` reads live state (read only: it never opens a context or creates a row) into ``state.student_two.Live``,
  with ``memory_for``: the memory check that already ran before cognition, as MEMORY (snapshot v5);
  ``gather_result`` does the same for a finished thought or background work (no new message: whether and how to
  bring it up is the decision).
- ``decide`` renders it, calls the model, parses and checks the answer. A server that is down, an answer that
  isn't JSON, or one ``events.check`` rejects is ``unavailable`` / ``invalid``: said as such, never read as silence.
- ``apply`` carries out the work part where an owner exists (background recall and thinking go through the
  shared-state route instead; taking something up is the mouth's, with its tools). What it couldn't carry out is
  returned and traced, never claimed.

One JSON line per decision in ``COGNITION_STUDENT_LOG_PATH`` (ids, choices, timings and problems; no message text).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.recall.contracts import MemoryRecord, RecallResult, origin_label
from app.ai.state import student_two as S
from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES, ChatMessage, CoordinatorWork, DesktopSession, MemoryConflict, MemoryContext,
    MemoryManagerState, MindThought, WorkingItem,
)

logger = logging.getLogger(__name__)
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THOUGHT_WINDOW = timedelta(minutes=15)
GOALS_SHOWN = 4


def _path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(BACKEND, path)


@lru_cache(maxsize=4)
def _manifest(path: str, mtime: float) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def manifest() -> dict[str, Any]:
    """The served model's ``student.json`` (re-read when it changes)."""
    path = os.path.join(_path(get_settings().cognition_student_dir), "student.json")
    return _manifest(path, os.path.getmtime(path))


# --------------------------------------------------------------------------- #
# Reading live state
# --------------------------------------------------------------------------- #


def rendering() -> str | None:
    """How the served model's training situations were rendered (None: unknown or no model)."""
    try:
        return manifest().get("rendering")
    except (OSError, ValueError):
        return None


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - a bad zone name is UTC, as elsewhere
        return ZoneInfo("UTC")


def live_game(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID | None) -> DesktopSession | None:
    """The game session this conversation is the talk of, while it lives."""
    if conversation_id is None:
        return None
    return db.scalar(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id, DesktopSession.conversation_id == conversation_id,
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES)).order_by(DesktopSession.started_at.desc()).limit(1))


def _lines(db: Session, conversation_id: uuid.UUID, before_sequence: int | None) -> list[S.Line]:
    from app.services.heard import heard_text

    query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id, ChatMessage.content != "")
    if before_sequence is not None:
        query = query.where(ChatMessage.conversation_sequence < before_sequence)
    rows = list(db.scalars(query.order_by(ChatMessage.conversation_sequence.desc()).limit(S.WINDOW)))
    return [S.Line("user" if r.role == "user" else "columbina",
                   heard_text(r.content, r.heard_chars) if r.role == "assistant" else r.content, _aware(r.created_at))
            for r in reversed(rows)]


def _goal_context(db: Session, user_id: uuid.UUID, game: DesktopSession | None) -> uuid.UUID | None:
    if game is not None:
        if game.goal_item_id is not None:
            goal = db.get(WorkingItem, game.goal_item_id)
            if goal is not None:
                return goal.context_id
        return db.scalar(select(MemoryContext.id).where(
            MemoryContext.user_id == user_id, MemoryContext.source == game.profile_id, MemoryContext.status != "closed")
            .order_by(MemoryContext.last_active_at.desc()).limit(1))
    state = db.get(MemoryManagerState, user_id)
    return state.focus_context_id if state is not None else None


def _goals(db: Session, user_id: uuid.UUID, context_id: uuid.UUID | None,
           game: DesktopSession | None) -> list[S.Goal]:
    if context_id is None:
        return []
    rows = list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.context_id == context_id, WorkingItem.kind == "goal",
        WorkingItem.closed_at.is_(None)).order_by(WorkingItem.created_at.desc()).limit(GOALS_SHOWN)))
    if game is not None and game.goal_item_id is not None:   # the goal the game plays for goes first
        rows.sort(key=lambda r: r.id != game.goal_item_id)
    return [S.Goal(str(r.id), r.text, "columbina" if r.owner == "columbina" else "user",
                   "active" if r.status == "active" else "on_hold",
                   r.done_when or "the user is satisfied with it", _aware(r.created_at),
                   completion="objective" if r.done_when else "subjective") for r in reversed(rows)]


_WORK_STATE = {"queued": "queued", "waiting_for_memory": "queued", "running": "running"}


def _work(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID | None,
          game: DesktopSession | None, *, exclude: uuid.UUID | None = None) -> list[S.Work]:
    out = []
    if conversation_id is not None:
        for w in db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == user_id, CoordinatorWork.conversation_id == conversation_id,
                CoordinatorWork.kind.in_(("recall", "mind")), CoordinatorWork.status.in_(tuple(_WORK_STATE)),
                CoordinatorWork.id != exclude if exclude else True)
                .order_by(CoordinatorWork.created_at)):
            subject = (w.input.get("think_about") or w.input.get("message") or "").strip()[:160]
            out.append(S.Work(f"coordinator:{w.id}", "recall" if w.kind == "recall" else "thinking",
                              subject or "their message", _WORK_STATE[w.status], _aware(w.created_at)))
    if game is not None and game.plan:
        step = next((st for st in game.plan.get("steps", []) if st.get("state") == "active"), None)
        if step is not None:
            out.append(S.Work(f"step:{game.id}:{step.get('id')}", "execution", step.get("do", "a step"),
                              "running" if game.state == "running" else "paused", _aware(game.updated_at),
                              goal_key=str(game.goal_item_id) if game.goal_item_id else None))
    return out


_THOUGHT_KIND = {"question": "question", "intention": "intention"}


def _thoughts(db: Session, activity: uuid.UUID | None, now: datetime,
              exclude: str | None = None) -> tuple[S.Thought | None, list[S.Thought]]:
    """Her own thoughts in this activity: the game session, or else the conversation."""
    if activity is None:
        return None, []
    rows = [r for r in db.scalars(select(MindThought).where(
        MindThought.activity_id == activity, MindThought.status == "done",
        MindThought.created_at >= now - THOUGHT_WINDOW).order_by(MindThought.created_at.desc()).limit(4))
        if str(r.id) != exclude][:3]
    made = [S.Thought(str(r.id), _THOUGHT_KIND.get(r.conclusion_kind or "", "conclusion"), r.question or r.text,
                      _aware(r.created_at), said=bool(r.said)) for r in rows]
    return (made[0] if made else None), list(reversed(made[1:]))


def _activity(game: DesktopSession | None) -> S.Activity | None:
    if game is None:
        return None
    plan = game.plan or {}
    step = next((st.get("do") for st in plan.get("steps", []) if st.get("state") == "active"), None)
    situation = "Playing Minecraft together" + (f"; the plan: {plan['goal']}" if plan.get("goal") else "") + (
        f"; doing now: {step}" if step else "")
    state = "running" if game.state == "running" else "paused"
    by = "none"
    if state == "paused":
        by = "user" if game.reason_code == "user_pause" else "columbina"
    return S.Activity(str(game.id), state, situation, by)


# recall.section_of -> the snapshot's sections (an unresolved fact stays "holds", with the user's words).
_SECTION = {"standing": "always", "unresolved": "holds", "current": "holds", "open": "open", "past": "past"}


def memory_for(db: Session, result: RecallResult | None, *, failed: bool = False) -> S.Memory | None:
    """The memory check before cognition as Student 2 reads it: the same records the mouth gets, with an open
    disagreement's words from ``memory_conflicts`` (the newest per fact) and whether more than one memory fits.
    ``failed``: the search itself failed (what's shown is the answer before's references, re-read)."""
    from app.services import recall

    if result is None:
        return None
    records = list(result.records)
    facts = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "fact"]
    open_rows: dict[uuid.UUID, MemoryConflict] = {}
    if facts:
        for row in db.scalars(select(MemoryConflict).where(MemoryConflict.fact_id.in_(facts),
                                                           MemoryConflict.status == "open")
                              .order_by(MemoryConflict.created_at)):
            open_rows[row.fact_id] = row
    out = []
    for r in records:
        section = recall.section_of(r)
        conflict = open_rows.get(uuid.UUID(r.ref.id)) if r.ref.kind == "fact" else None
        out.append(S.Remembered(_SECTION.get(section, "holds"), _record_text(r), _aware(r.recorded_at),
                                conflict.user_words if conflict else "",
                                _aware(conflict.created_at) if conflict and conflict.created_at else None))
    search = "failed" if failed or result.status == "failed" else "found" if out else "nothing_found"
    return S.Memory(search, tuple(out), bool(result.coverage.get("references_ambiguous")))


def _record_text(r: MemoryRecord) -> str:
    """Her own reasoning is never put as a fact: an item says whose it is ("You suspected (not confirmed): ...")."""
    label = origin_label(r) if r.ref.kind == "item" else None
    return f"{label}: {r.content}" if label else r.content


def gather(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None,
           trigger_message_id: uuid.UUID | None, message: str, zone: str | None,
           now: datetime | None = None, memory: S.Memory | None = None) -> S.Live:
    now = now or datetime.now(timezone.utc)
    trigger = db.get(ChatMessage, trigger_message_id) if trigger_message_id else None
    before = trigger.conversation_sequence if trigger is not None else None
    game = live_game(db, user_id, conversation_id)
    thought, earlier = _thoughts(db, game.id if game else conversation_id, now)
    return S.Live(
        trigger="user_message", domain="minecraft" if game is not None else "chat",
        now=now.astimezone(_zone(zone)), lines=tuple(_lines(db, conversation_id, before)) if conversation_id else (),
        new_message=message, goals=tuple(_goals(db, user_id, _goal_context(db, user_id, game), game)),
        work=tuple(_work(db, user_id, conversation_id, game)), thought=thought, earlier_thoughts=tuple(earlier),
        activity=_activity(game), memory=memory)


def _game_of(db: Session, work: CoordinatorWork) -> DesktopSession | None:
    if work.input.get("source") == "minecraft" and work.activity_id:
        game = db.get(DesktopSession, work.activity_id)
        if game is not None and game.owner_user_id == work.user_id and game.state not in DESKTOP_TERMINAL_STATES:
            return game
    return live_game(db, work.user_id, work.conversation_id)


def gather_result(db: Session, work: CoordinatorWork, *, zone: str | None = None,
                  now: datetime | None = None) -> S.Live:
    """A finished coordinator item as Student 2 reads it: her thought just finished ([T1], with the thinking
    behind it for a request), or work that ended without a conclusion. The conversation is read as it is now."""
    now = now or datetime.now(timezone.utc)
    r = work.result or {}
    game = _game_of(db, work)
    autonomous = bool(work.input.get("autonomous"))
    _, earlier = _thoughts(db, game.id if game else work.conversation_id, now,
                           exclude=str(work.input.get("event_id") or ""))
    conclusion = (r.get("conclusion") or "").strip()
    question = (r.get("question") or "").strip()
    finished = work.status == "completed" and bool(conclusion or question)
    thought = None
    if finished:
        kind = _THOUGHT_KIND.get(r.get("kind") or "", "conclusion")
        thought = S.Thought(f"coordinator:{work.id}", kind, question if kind == "question" and question else conclusion,
                            min(now, _aware(work.updated_at)) if work.updated_at else now, origin="just_finished")
    work_items = []
    if not autonomous:
        uncertain = r.get("kind") == "guess" or r.get("evidence_status") not in (None, "matched")
        unresolved = ("it is a guess" if r.get("kind") == "guess" else "") or (
            f"memory: {r['evidence_status']}" if uncertain and r.get("evidence_status") else "")
        state = "completed" if finished else "failed"
        outcome = (conclusion or question)[:300] if finished else (
            f"didn't reach a conclusion ({work.error_code or work.status})")
        subject = (work.input.get("think_about") or work.input.get("message") or "their message").strip()[:160]
        work_items.append(S.Work(f"coordinator:{work.id}", "thinking", subject, state, _aware(work.created_at),
                                 outcome=outcome, unresolved=unresolved, for_thought=finished))
    work_items += _work(db, work.user_id, work.conversation_id, game, exclude=work.id)
    return S.Live(
        trigger="thought_result" if finished else "work_outcome", domain="minecraft" if game is not None else "chat",
        now=now.astimezone(_zone(zone or work.input.get("zone"))),
        lines=tuple(_lines(db, work.conversation_id, None)) if work.conversation_id else (), new_message="",
        goals=tuple(_goals(db, work.user_id, _goal_context(db, work.user_id, game), game)), work=tuple(work_items),
        thought=thought, earlier_thoughts=tuple(earlier), activity=_activity(game))


# --------------------------------------------------------------------------- #
# Deciding
# --------------------------------------------------------------------------- #


@dataclass
class Outcome:
    status: str  # ok / invalid / unavailable
    situation: S.Situation | None = None
    decision: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    effect: str | None = None  # what ``apply`` did with the work part
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.decision is not None


def _ask(model: dict[str, Any], text: str) -> str:
    s = get_settings()
    body = {"messages": [{"role": "system", "content": model["system"]}, {"role": "user", "content": text}],
            "temperature": 0.0, "max_tokens": model.get("max_tokens", 400),
            "chat_template_kwargs": model.get("chat_template_kwargs", {})}
    request = urllib.request.Request(f"{s.cognition_student_url.rstrip('/')}/v1/chat/completions",
                                     json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=s.cognition_student_timeout_seconds) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def decide(live: S.Live, ask=None) -> Outcome:
    """One decision. Never raises: what went wrong is the outcome's status and error."""
    started = time.monotonic()
    try:
        model = manifest()
    except (OSError, ValueError) as exc:
        return Outcome("unavailable", error=f"no model: {type(exc).__name__}")
    situation = S.snapshot(live)
    text = S.render(situation, model.get("rendering"))
    try:
        output = (ask or _ask)(model, text)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
        return Outcome("unavailable", situation, model=model.get("name"), error=f"{type(exc).__name__}: {exc}"[:200],
                       latency_ms=round((time.monotonic() - started) * 1000))
    decision, unreadable = S.parse(output)
    problems = [unreadable] if unreadable else S.check(decision, situation)
    return Outcome("invalid" if problems else "ok", situation, None if unreadable else decision, problems,
                   model.get("name"), round((time.monotonic() - started) * 1000))


# --------------------------------------------------------------------------- #
# Carrying out the work part
# --------------------------------------------------------------------------- #


def apply(db: Session, outcome: Outcome, *, user_id: uuid.UUID, message_id: uuid.UUID | None,
          routed: bool = True, conversation_id: uuid.UUID | None = None) -> str:
    """The work part's effect, where an owner can carry it out now. Returns what happened, in a few words.
    ``routed``: a user turn routes background recall and thinking itself (the coordinator); on a follow-up,
    thinking wakes her mind in ``conversation_id``, and a recall alone has no owner yet."""
    from app.services import desktop_game, memory_manager, memory_reconcile

    d, refs = outcome.decision, outcome.situation.refs
    work, target = d["work"], d["target"]
    key = refs.get(target)
    source = memory_manager.Source("message", message_id) if message_id else None
    try:
        if work == "no_change":
            effect = "nothing changed"
        elif work in S.RECALL_WORK | S.E.THINKING and routed:
            effect = "background work, routed by the turn"
        elif work in S.E.THINKING:
            from app.services import mind_conversation
            effect = mind_conversation.wake(user_id=user_id, conversation_id=conversation_id,
                                            think_about=d["think_about"], decision_id=outcome.decision_id)
        elif work == "pursue":
            effect = "taken up by her reply (its tools)"
        elif target == "A1" and work == "pause":
            effect = desktop_game.pause(db, user_id, user_asked=False)
        elif target == "A1" and work == "resume":
            effect = desktop_game.resume(db, user_id)
        elif target == "A1" and work == "cancel":
            effect = desktop_game.end(db, user_id)
        elif (target.startswith("G") and work in ("pause", "resume", "resolve_goal") and message_id is None
              and key and uuid.UUID(key) in memory_reconcile.held_item_ids(db, user_id)):
            # Without a user message, a goal being rechecked after a fact changed isn't hers to change yet.
            effect = "goal on hold: being rechecked"
        elif target.startswith("G") and work in ("pause", "resume", "resolve_goal"):
            status = {"pause": "suspended", "resume": "active"}.get(work) or (
                "completed" if d["resolution"] == "achieved" else "abandoned")
            memory_manager.resolve(db, user_id, key, status=status, source=source,
                                   reason=d["intention"].strip()[:200] or work)
            db.commit()
            effect = f"goal {status}"
        elif target.startswith("W") and work == "cancel" and key and key.startswith("coordinator:"):
            row = db.get(CoordinatorWork, uuid.UUID(key.split(":", 1)[1]))
            if row is not None and row.user_id == user_id and row.status in _WORK_STATE:
                row.status, row.error_code = "cancelled", "student_two"
                row.cancellation_generation += 1
                db.commit()
            effect = "background work cancelled"
        elif target.startswith("G") and work == "request_replanning" and key:
            effect = desktop_game.rethink(db, user_id, why=d["intention"].strip()[:200] or "replan")
        else:
            effect = f"not carried out: no owner for {work} on {target} yet"
    except Exception as exc:  # noqa: BLE001 - a failed effect is said, never claimed
        db.rollback()
        logger.exception("student_two: carrying out %s %s failed", work, target)
        effect = f"failed: {type(exc).__name__}"
    outcome.effect = effect[:200]
    return outcome.effect


def log(outcome: Outcome, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None, trigger: str) -> None:
    """The decision's trace line: no message text, no words she meant to say."""
    try:
        d = outcome.decision or {}
        record = {"at": datetime.now(timezone.utc).isoformat(), "decision": outcome.decision_id,
                  "user_id": str(user_id), "conversation_id": str(conversation_id) if conversation_id else None,
                  "trigger": trigger, "status": outcome.status, "model": outcome.model,
                  "latency_ms": outcome.latency_ms, "error": outcome.error, "problems": outcome.problems,
                  "domain": outcome.situation.snapshot["domain"] if outcome.situation else None,
                  "refs": sorted(outcome.situation.refs) if outcome.situation else [],
                  "communication": d.get("communication"), "move": d.get("move"), "work": d.get("work"),
                  "target": d.get("target"), "effect": outcome.effect}
        path = _path(get_settings().cognition_student_log_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("student_two: writing the trace failed")
