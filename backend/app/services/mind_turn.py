"""One evaluation of her mind (docs/MIND_ARCHITECTURE.md): what a wake is given, and the record of what she did.

Every wake is one tool-loop evaluation of the same mind (``app/ai/chat.py``): a user's message, thinking she started
that finished, a thought or proposal from another part of her, or a reconsideration she scheduled. Nothing sits
between the wake and her: no interpretation stage, no decision model, no gate on what she may say. This module
assembles the bounded state she reads and keeps the record; she decides with her prompt and tools.

- ``prepare_message`` / ``prepare_wake`` (blocking; call in a thread): unfinished thinking a new message makes
  moot is superseded; memory is checked once for this moment (``recall_references.for_reply``: the answer before's
  references, standing constraints, a search for the message or for what the wake is about), kept as the turn's
  recall so her memory tools continue from it in the same loop; the lessons memory holds for this user; thinking
  still under way and reconsiderations she scheduled. A failed search is said as failed, never as nothing found.
- ``Evaluation``: the per-wake record her tools write into (``app/ai/tools/mind``): silence chosen, thinking
  started, reconsiderations scheduled, a proposal decided.
- ``finish``: one trace line in ``MIND_TURN_LOG_PATH`` (ids, labels, counts, timings: no message or memory text),
  her attempt and its delivery in learning capture (learner ``mind``), which lessons she had.

The database is the record between wakes. Nothing here survives in process memory.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation
from app.db.session import SessionLocal
from app.services import coordinator, learning_capture, recall_references

logger = logging.getLogger(__name__)
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA_VERSION = 1


@dataclass
class Evaluation:
    """One wake of her mind: what it was given (read by the prompt) and what she did (written by her tools)."""

    user_id: uuid.UUID
    conversation_id: uuid.UUID | None
    request_id: uuid.UUID  # the turn this evaluation runs as
    reason: str  # user_message / work_result / thought / proposal / conflict / finding / due
    trigger_message_id: uuid.UUID | None = None  # the user's message it answers, or the latest one (a wake)
    message: str = ""
    channel: str = "web"
    voice: bool = False
    zone: str | None = None
    user_name: str | None = None
    wake: dict[str, Any] | None = None  # coordinator.follow_up_still_valid's info, for a wake
    # Memory, checked once for this moment.
    recall_turn: recall_references.TurnRecall | None = None
    memory_status: str = "not_searched"  # matched / partial / no_match / failed / not_searched
    searched: bool = False  # a search ran and worked (else: the answer before's references, re-read)
    memory_refs: tuple[dict[str, Any], ...] = ()
    conversation_sequence: int | None = None  # the conversation's latest message when this began
    lessons: Any = None  # memory_lessons.Supply
    lessons_note: str | None = None
    superseded: int = 0
    live_trial: bool = False  # a bounded experiment of hers is under way (she can pause or stop it)
    capture: learning_capture.Ctx | None = None
    started: float = field(default_factory=time.monotonic)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timings: dict[str, int] = field(default_factory=dict)
    # Filled while she runs.
    prompt: str | None = None  # the system prompt she read (set by app/ai/chat.py)
    model: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)  # every tool result, in order
    silent: str | None = None  # her reason, when she chose to say nothing
    pending: tuple[coordinator.PendingWork, ...] = ()  # thinking she started in this evaluation
    scheduled: list[str] = field(default_factory=list)  # reconsiderations she scheduled (work ids)
    proposal_decision: str | None = None  # accept / decline / defer, on a proposal wake

    # --- what the prompt reads -------------------------------------------------------------- #

    @property
    def is_wake(self) -> bool:
        return self.reason != "user_message"

    @property
    def recall_rendered(self) -> str | None:
        return self.recall_turn.rendered if self.searched and self.recall_turn is not None else None

    @property
    def cached_memory(self) -> str | None:
        if self.searched or self.recall_turn is None or self.recall_turn.result.status == "failed":
            return None
        return self.recall_turn.rendered

    @property
    def long_term_memory(self) -> str | None:
        return self.recall_turn.legacy if self.recall_turn is not None else None

    def boundary(self) -> str:
        """What this evaluation had from memory, in her words (the rules for it are in ``prompts.py``)."""
        lines = []
        entries = len(self.memory_refs)
        if self.searched:
            lines.append(f"- {_SEARCH_WORDS.get(self.memory_status, _SEARCH_WORDS['failed'])}")
        elif self.memory_status == "failed":
            lines.append(f"- {_SEARCH_WORDS['failed']}")
            if self.recall_turn is not None and self.recall_turn.result.status != "failed":
                have = (f"{entries} memories from your last answer, re-read now" if entries
                        else "no memories from your last answer")
                lines.append(f"- You have the recent conversation and {have}.")
        else:
            lines.append("- Memory was not searched for this moment. You have the recent conversation only.")
        return "\n".join(lines)

    def state(self) -> str | None:
        """What is under way and what she set to come back to (read fresh; never from process memory)."""
        try:
            with SessionLocal() as db:
                return _state(db, self)
        except Exception:  # noqa: BLE001 - the evaluation goes on without it, and says nothing false
            logger.exception("mind_turn: reading her pending state failed")
            return "(What's under way couldn't be read just now: don't assume nothing is.)"

    def event(self) -> dict[str, Any]:
        """The ``route`` frame for the turn stream: status only, so a client can show what memory gave."""
        return {"type": "route", "route": "mind", "recall": self.memory_status,
                "degraded": ["recall_unavailable"] if self.memory_status == "failed" else []}


_SEARCH_WORDS = {
    "matched": "Memory was searched for this moment: what it found is under \"What you recall\".",
    "partial": "Memory was searched for this moment, but part of it couldn't be read: what's there is incomplete.",
    "no_match": ("Memory was searched for this moment and found nothing that matches. That isn't proof it "
                 "didn't happen: say you can't find a memory of it."),
    "failed": ("Memory was searched for this moment and the search FAILED: don't say nothing is recorded. Your "
               "memory tools may still work if you need them."),
}
_WORK_WORDS = {"queued": "queued, not started yet", "waiting_for_memory": "waiting for a memory lookup",
               "running": "under way"}


def _ago(seconds: float) -> str:
    seconds = abs(seconds)
    minutes = int(seconds // 60)
    return f"{int(seconds)} s" if minutes < 2 else f"{minutes} min" if minutes < 120 else (
        f"{minutes // 60} h" if minutes < 48 * 60 else f"{minutes // 1440} days")


def _state(db, ev: Evaluation) -> str | None:
    now = datetime.now(timezone.utc)
    lines = []
    for work in coordinator.thinking_for(db, ev.user_id, ev.conversation_id):
        if work.kind != "mind":
            continue
        about = (work.input.get("think_about") or work.input.get("message") or "").strip()[:160]
        lines.append(f"- Thinking you started [work:{str(work.id)[:8]}], {_WORK_WORDS.get(work.status, work.status)}: "
                     f"{about}. You haven't reached a conclusion on it: you'll be woken when it ends.")
    for wake in coordinator.scheduled_wakes(db, ev.user_id):
        due = wake.next_attempt_at
        due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        when = f"in {_ago((due - now).total_seconds())}" if due > now else "now due"
        lines.append(f"- You'll come back to [wake:{str(wake.id)[:8]}] {when}: {wake.input.get('about', '')[:200]}")
    return "\n".join(lines) or None


# --------------------------------------------------------------------------- #
# Assembling a wake
# --------------------------------------------------------------------------- #


def _memory(ev: Evaluation, query: str) -> None:
    """Memory's own search for this moment. When it fails, the answer before's references are re-read instead and
    the evaluation says the search failed (never "nothing found")."""
    turn = recall_references.for_reply(ev.user_id, ev.conversation_id, ev.trigger_message_id if not ev.is_wake
                                       else None, query, ev.zone)
    status = turn.result.status
    searched = status != "failed"
    if not searched:
        turn = recall_references.cached_for_reply(ev.user_id, ev.conversation_id,
                                                  ev.trigger_message_id if not ev.is_wake else None, ev.zone)
        turn.result.coverage["search"] = "failed"  # the receipt says the search failed, not what the cache held
    ev.recall_turn, ev.memory_status, ev.searched = turn, status, searched
    ev.memory_refs = tuple({"ref": str(r.ref), "fingerprint": r.fingerprint, "applicability": r.applicability,
                            "corrected": bool(r.corrected)} for r in turn.result.records)
    turn.consumed("mind", [str(r.ref) for r in turn.result.records], turn.legacy)


def _lessons(ev: Evaluation, query: str) -> None:
    """The lessons memory holds for this user, for this moment. Read only; a failure is no lessons, said."""
    from app.services import memory_lessons

    if not get_settings().lessons_supply_enabled:
        ev.lessons_note = "lesson supply is switched off (baseline)"
        return
    try:
        with SessionLocal() as db:
            # Tentative inferences too, labelled as guesses: they can inform how she reads and answers now; they
            # are never asserted.
            supply = memory_lessons.for_decision(db, ev.user_id, query, include_tentative=True)
        why = None
    except Exception as exc:  # noqa: BLE001 - this evaluation goes without lessons
        logger.exception("mind_turn: reading lessons failed")
        ev.lessons_note = f"reading lessons failed: {type(exc).__name__}"
        return
    ev.lessons = supply
    ev.lessons_note = why or (None if supply.views or supply.left_out else "no lessons with live support")


def lessons_text(ev: Evaluation) -> str | None:
    from app.ai.learning import lessons as L

    if ev.lessons is None or not ev.lessons.views:
        return None
    return "\n".join(L.decision_lines(ev.lessons.views, cut=len(ev.lessons.left_out)))


def _capture(ev: Evaluation, tries: int | None) -> None:
    lesson_refs = tuple({"ref": f"lesson:{v.id}", "revision": v.revision, "shown": True}
                        for v in (ev.lessons.views if ev.lessons is not None else ()))
    sources = tuple(dict.fromkeys(filter(None, (
        f"message:{ev.trigger_message_id}" if ev.trigger_message_id else "",
        *(r["ref"] for r in ev.memory_refs), *(r["ref"] for r in lesson_refs),
        f"work:{ev.wake['work_id']}" if ev.wake else ""))))
    upstream = {"work": ev.wake["work_id"]} if ev.wake else {}
    activity = (ev.wake or {}).get("activity_id") if (ev.wake or {}).get("input", {}).get("source") == "minecraft" \
        else None
    ev.capture = learning_capture.Ctx(
        user_id=ev.user_id, attempt_id=learning_capture.attempt_id_for(ev.request_id, "mind", tries),
        prior_attempt_id=learning_capture.prior_for(ev.request_id, "mind", tries), trigger=ev.reason,
        conversation_id=ev.conversation_id, turn_id=ev.request_id,
        source_message_id=ev.trigger_message_id if not ev.is_wake else None,
        game_session_id=uuid.UUID(activity) if activity else None, upstream=upstream,
        input_refs=ev.memory_refs + lesson_refs, sources=sources)


def _sequence(conversation_id: uuid.UUID | None) -> int | None:
    if conversation_id is None:
        return None
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        return conversation.last_message_sequence if conversation is not None else None


def prepare_message(*, user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
                    message: str, zone: str | None, turn_id: uuid.UUID, channel: str = "web", voice: bool = False,
                    job: ChatJob | None = None, user_name: str | None = None) -> Evaluation:
    """A user's message wakes her. Blocking (call it in a thread). Never raises: what couldn't be read is said to
    her as such."""
    ev = Evaluation(user_id=user_id, conversation_id=conversation_id, request_id=turn_id, reason="user_message",
                    trigger_message_id=user_message_id, message=message, channel=channel, voice=voice, zone=zone,
                    user_name=user_name)
    at = ev.started
    # A new message: unfinished thinking for earlier ones in this conversation can't answer it.
    ev.superseded = coordinator.supersede_older(user_id, conversation_id, user_message_id)
    at = _lap(ev, "supersede_ms", at)
    _gather(ev, message, job.attempts if job is not None else None, at)
    return ev


def prepare_wake(*, job: ChatJob, info: dict[str, Any], user_name: str | None = None) -> Evaluation:
    """Something other than a message wakes her (``info``: ``coordinator.follow_up_still_valid``). Blocking."""
    payload = job.payload or {}
    with SessionLocal() as db:
        latest = db.scalar(select(ChatMessage.id).where(
            ChatMessage.conversation_id == job.conversation_id, ChatMessage.role == "user")
            .order_by(ChatMessage.conversation_sequence.desc()).limit(1))
    ev = Evaluation(user_id=job.user_id, conversation_id=job.conversation_id, request_id=job.turn_id,
                    reason=info.get("reason") or "work_result", trigger_message_id=latest,
                    channel=payload.get("channel", "web"), voice=False, zone=payload.get("zone"),
                    user_name=user_name, wake=info)
    r, inp = info.get("result") or {}, info.get("input") or {}
    about = " ".join(filter(None, (inp.get("about"), inp.get("think_about"), r.get("question"),
                                   r.get("conclusion"))))[:600]
    _gather(ev, about, job.attempts, ev.started)
    return ev


def _lap(ev: Evaluation, name: str, since: float) -> float:
    now = time.monotonic()
    ev.timings[name] = round((now - since) * 1000)
    return now


def _gather(ev: Evaluation, query: str, tries: int | None, at: float) -> None:
    ev.conversation_sequence = _safely(lambda: _sequence(ev.conversation_id), None)
    try:
        _memory(ev, query)
    except Exception:  # noqa: BLE001 - both the search and the re-read failed: said to her, never an empty memory
        logger.exception("mind_turn: the memory check failed")
        ev.recall_turn, ev.memory_status, ev.searched = None, "failed", False
    at = _lap(ev, "memory_ms", at)
    if ev.memory_status != "failed":
        from app.services import system_signals
        system_signals.emit(ev.user_id, "memory", "mind", "memory_check", ev.trigger_message_id)
    _lessons(ev, query)
    at = _lap(ev, "lessons_ms", at)
    if get_settings().desktop_agent_enabled:
        ev.live_trial = _safely(lambda: _has_live_trial(ev.user_id), False)
    _capture(ev, tries)
    ev.timings["prepare_ms"] = round((time.monotonic() - ev.started) * 1000)


def _has_live_trial(user_id: uuid.UUID) -> bool:
    from app.db.models import LearningTrial

    with SessionLocal() as db:
        return db.scalar(select(LearningTrial.id).where(
            LearningTrial.user_id == user_id,
            LearningTrial.status.in_(("selected", "preparing", "executing", "paused"))).limit(1)) is not None


def _safely(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.exception("mind_turn: a read failed")
        return default


def moved_on(db, ev: Evaluation) -> bool:
    """Whether the user wrote in this conversation since the evaluation began (checked under the conversation's
    lock, right before anything a wake produced is stored): what she'd say was worded without their message."""
    if ev.conversation_id is None or ev.conversation_sequence is None:
        return False
    return db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == ev.conversation_id, ChatMessage.role == "user",
        ChatMessage.conversation_sequence > ev.conversation_sequence).limit(1)) is not None


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def finish(ev: Evaluation | None, *, outcome: str, reply: str, assistant_message_id: uuid.UUID | None,
           heard_chars: int | None = None, error: str | None = None) -> None:
    """What she did and what was delivered: the trace line, her attempt, its delivery. Never raises.
    ``outcome``: completed / partial / cancelled / failed / silent."""
    if ev is None:
        return
    spoke = bool(reply) and outcome in ("completed", "partial")
    if ev.pending and ev.conversation_id is not None:
        _safely(lambda: coordinator.record_immediate(
            user_id=ev.user_id, conversation_id=ev.conversation_id, request_id=ev.request_id, spoke=spoke,
            pending=ev.pending), None)
        coordinator.immediate_outcome(ev.request_id, status={
            "completed": "delivered", "silent": "not_sent"}.get(outcome, outcome),
            assistant_message_id=assistant_message_id if spoke else None)
    _safely(lambda: _record(ev, outcome=outcome, reply=reply, assistant_message_id=assistant_message_id,
                            error=error), None)
    try:
        turn = ev.recall_turn
        record = {
            "at": datetime.now(timezone.utc).isoformat(), "schema": SCHEMA_VERSION, "component": "mind",
            "user_id": str(ev.user_id), "conversation_id": str(ev.conversation_id) if ev.conversation_id else None,
            "turn": str(ev.request_id), "reason": ev.reason,
            "attempt": str(ev.capture.attempt_id) if ev.capture else None,
            "wake": {"work": ev.wake.get("work_id"), "kind": ev.wake.get("kind"), "status": ev.wake.get("status"),
                     "late": ev.wake.get("late")} if ev.wake else None,
            "memory": {"status": ev.memory_status, "searched": ev.searched, "records": len(ev.memory_refs),
                       "version": hashlib.sha256(",".join(f"{r['ref']}#{r['fingerprint']}" for r in ev.memory_refs)
                                                 .encode()).hexdigest()[:16],
                       "tool_calls": turn.tool_calls if turn is not None else 0},
            "lessons": {"offered": len(ev.lessons.views) if ev.lessons is not None else 0,
                        "left_out": len(ev.lessons.left_out) if ev.lessons is not None else 0,
                        "note": ev.lessons_note},
            "tools": [a.get("tool") for a in ev.actions],
            "did": {"silent": ev.silent is not None, "spoke": spoke,
                    "thinking_started": [p.work_id for p in ev.pending], "scheduled": list(ev.scheduled),
                    "proposal": ev.proposal_decision},
            "superseded_work": ev.superseded,
            "delivery": {"outcome": outcome, "assistant_message_id": str(assistant_message_id)
                         if assistant_message_id else None, "reply_chars": len(reply or ""),
                         "heard_chars": heard_chars, "error": error},
            "model": ev.model,
            "timings": ev.timings | {"total_ms": round((time.monotonic() - ev.started) * 1000)},
        }
        _write(record)
    except Exception:  # noqa: BLE001
        logger.exception("mind_turn: writing the trace failed")


def _record(ev: Evaluation, *, outcome: str, reply: str, assistant_message_id: uuid.UUID | None,
            error: str | None) -> None:
    """Learning capture: her attempt as it happened (what she read, what she did), and its delivery."""
    ctx = ev.capture
    if ctx is None:
        return
    failed = outcome == "failed"
    blobs = {"system": ev.prompt} if ev.prompt else {}
    absent = list(learning_capture.NOT_CAPTURED) + ([] if ev.prompt else ["input"])
    learning_capture.attempt(
        ctx, learner="mind", provider="openai", model=ev.model,
        input={"reason": ev.reason, "message": ev.message or None, "system": None,
               "wake": coordinator.render_wake(ev.wake) if ev.wake else None,
               "memory": {"status": ev.memory_status, "searched": ev.searched, "refs": list(ev.memory_refs)},
               "lessons": [v.to_dict() for v in ev.lessons.views] if ev.lessons is not None else [],
               "channel": ev.channel, "voice": ev.voice},
        output=None if failed and not reply and not ev.actions else {
            "reply": reply, "silent": ev.silent, "tools": ev.actions[:60],
            "thinking_started": [p.work_id for p in ev.pending], "scheduled": list(ev.scheduled),
            "proposal": ev.proposal_decision},
        status="error" if failed else "ok", error=error, started_at=ev.started_at,
        produced_at=datetime.now(timezone.utc), versions={"schema": SCHEMA_VERSION},
        settings={"channel": ev.channel, "voice": ev.voice}, blobs=blobs, absent=absent)
    learning_capture.reply(
        user_id=ev.user_id, attempt_id=ctx.attempt_id, turn_id=ev.request_id, conversation_id=ev.conversation_id,
        outcome=outcome, assistant_message_id=assistant_message_id, reply_chars=len(reply or ""),
        intended={"communication": "silent" if ev.silent is not None else "speak", "origin": "mind",
                  "reason": ev.reason}, channel=ev.channel, voice=ev.voice,
        started_at=datetime.now(timezone.utc) - timedelta(seconds=time.monotonic() - ev.started))
    if ev.lessons is not None:
        from app.services import memory_lessons
        memory_lessons.record_uses(ev.user_id, ctx.attempt_id, ev.lessons, consumer="mind",
                                   received=bool(ev.prompt), reason=None if ev.prompt else (
                                       ev.lessons_note or "the evaluation failed before its prompt was built"),
                                   decision_status="error" if failed else "ok")


def _write(record: dict[str, Any]) -> None:
    path = get_settings().mind_turn_log_path
    if not path:
        return
    if not os.path.isabs(path):
        path = os.path.join(BACKEND, path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
