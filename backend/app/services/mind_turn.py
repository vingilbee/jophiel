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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, CoordinatorWork, MindEvaluation, WorkingItem
from app.db.session import SessionLocal
from app.services import coordinator, learning_capture, recall, recall_references

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
    channel_identity_id: str | None = None  # who to deliver to off the web (a WhatsApp identity)
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
    deferred: list[dict[str, Any]] = field(default_factory=list)  # wakes she moved: from, to, reason
    dropped: list[dict[str, Any]] = field(default_factory=list)  # wakes she dropped or let go, with why
    fulfilled: list[uuid.UUID] = field(default_factory=list)  # promises she marked fulfilled (count once delivered)

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

    def situation(self) -> str:
        """The clock and what caused this evaluation, read now (never from a queued job's snapshot): the "Now"
        section of her prompt. Never raises: what couldn't be read is said as such."""
        now = datetime.now(timezone.utc)
        try:
            with SessionLocal() as db:
                return _situation(db, self, now)
        except Exception:  # noqa: BLE001
            logger.exception("mind_turn: reading the situation failed")
            from app.ai.mind import when as W
            return (f"- It is {W.describe(now, self.zone)}.\n- What happened since your last evaluation couldn't be "
                    "read just now: don't assume nothing changed.")

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
    from app.ai.mind import when as W

    now = datetime.now(timezone.utc)
    lines = []
    for work in coordinator.thinking_for(db, ev.user_id, ev.conversation_id):
        if work.kind != "mind":
            continue
        about = (work.input.get("think_about") or work.input.get("message") or "").strip()[:160]
        lines.append(f"- Thinking you started [work:{str(work.id)[:8]}], {_WORK_WORDS.get(work.status, work.status)}: "
                     f"{about}. You haven't reached a conclusion on it: you'll be woken when it ends.")
    for wake in coordinator.scheduled_wakes(db, ev.user_id):
        due = _utc(wake.next_attempt_at)
        when = (f"at {W.describe(due, ev.zone)} (in {W.span((due - now).total_seconds())})" if due > now
                else "now due")
        whose = {"user": " (they asked for it)", "mind": " (your own idea)"}.get((wake.input or {}).get("asked_by"), "")
        lines.append(f"- You'll come back to [wake:{str(wake.id)[:8]}] {when}{whose}: "
                     f"{wake.input.get('about', '')[:200]}")
    lines += _unsettled(db, ev, now)
    return "\n".join(lines) or None


def _unsettled(db, ev: Evaluation, now: datetime) -> list[str]:
    """Wakes of the last day that came due and weren't carried through (her words not delivered, silence on a
    check-in they asked for), with nothing scheduled after them and not let go: what actually happened, so it can
    be reconsidered. Read from records; at most four."""
    rows = list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == ev.user_id, CoordinatorWork.kind == "wake",
        CoordinatorWork.status.in_(("completed", "expired")),
        CoordinatorWork.updated_at >= now - timedelta(days=1)).order_by(CoordinatorWork.updated_at.desc()).limit(12)))
    out = []
    for work in rows:
        result = work.result or {}
        if result.get("moved_to") or result.get("dropped"):
            continue
        attempt = db.scalar(select(MindEvaluation).where(MindEvaluation.work_id == work.id).limit(1))
        if attempt is None or attempt.delivery in ("delivered", "partial"):
            continue
        if attempt.outcome in ("silent", "no_words") and (work.input or {}).get("asked_by") != "user":
            continue  # her own optional idea, which she chose to let pass
        item = db.get(WorkingItem, uuid.UUID(work.input["item"])) if (work.input or {}).get("item") else None
        if item is not None and item.closed_at is not None:
            continue
        out.append(f"- [wake:{str(work.id)[:8]}] came due {_when(attempt.finished_at, ev.zone, now)}: "
                   f"{_did(attempt)}. Nothing is scheduled for it now: {(work.input or {}).get('about', '')[:160]}")
        if len(out) == 4:
            break
    return out


# --------------------------------------------------------------------------- #
# Now: the clock and what caused this evaluation, from records
# --------------------------------------------------------------------------- #

_REASON_WORDS = {"user_message": "their message", "work_result": "thinking you started ending",
                 "thought": "a thought from your background thinking", "proposal": "a proposal from another part of you",
                 "conflict": "memory noticing a disagreement", "finding": "a security finding",
                 "due": "something you scheduled coming due"}
_DID_WORDS = {"said": "you spoke", "partial": "you spoke, but were stopped part way",
              "silent": "you chose silence; no words were delivered",
              "no_words": "you wrote nothing; no words were delivered",
              "cancelled": "it was cancelled before anything was delivered",
              "failed": "it failed; nothing was delivered"}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _when(value: datetime | None, zone: str | None, now: datetime) -> str:
    from app.ai.mind import when as W

    value = _utc(value)
    if value is None:
        return "an unrecorded time"
    same_day = W.describe(value, zone)[:10] == W.describe(now, zone)[:10]
    text = W.describe(value, zone, date=not same_day)
    delta = (now - value).total_seconds()
    return f"{text} ({W.span(delta)} {'ago' if delta >= 0 else 'from now'})"


def _did(row: MindEvaluation) -> str:
    return _DID_WORDS.get(row.outcome, row.outcome) + (
        f' ("{row.silence_reason}")' if row.outcome == "silent" and row.silence_reason else "")


def _situation(db, ev: Evaluation, now: datetime) -> str:
    from app.ai.mind import when as W

    zone = ev.zone if W.zone_of(ev.zone) is not None else None
    local = now.astimezone(W.zone_of(zone)) if zone else now
    lines = [f"- It is {local:%H:%M:%S} on {local:%A %d %B %Y}"
             + (f" in {zone} ({now:%H:%M:%S} UTC)." if zone else " UTC. Their time zone isn't known: times here are UTC.")]
    began = (now - ev.started_at).total_seconds()
    if began >= 2:
        lines.append(f"- This evaluation began {W.span(began)} ago (its preparation took that long).")
    if ev.conversation_id is not None:
        previous = db.scalar(select(MindEvaluation).where(
            MindEvaluation.conversation_id == ev.conversation_id, MindEvaluation.turn_id != ev.request_id)
            .order_by(MindEvaluation.finished_at.desc()).limit(1))
        if previous is not None:
            lines.append(f"- Your previous evaluation in this conversation ended {_when(previous.finished_at, zone, now)}"
                         f", woken by {_REASON_WORDS.get(previous.reason, previous.reason)}: {_did(previous)}.")
        else:
            lines.append("- There's no earlier evaluation of yours on record in this conversation.")
        last = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == ev.conversation_id,
                                                   ChatMessage.role == "user")
                         .order_by(ChatMessage.conversation_sequence.desc()).limit(1))
        if last is not None and (ev.is_wake or last.id != ev.trigger_message_id):
            lines.append(f"- Their latest message was at {_when(last.created_at, zone, now)}.")
    lines.append("")
    lines.append("EVENT")
    if not ev.is_wake:
        message = db.get(ChatMessage, ev.trigger_message_id) if ev.trigger_message_id else None
        if message is not None:
            lines.append(f"- Their message arrived at {_when(message.created_at, zone, now)}. It is the CURRENT "
                         "message, the last one in the conversation: this evaluation answers it.")
        else:
            lines.append("- Their message (the last one in the conversation) woke you.")
        return "\n".join(lines)
    info = ev.wake or {}
    work = db.get(CoordinatorWork, uuid.UUID(info["work_id"])) if info.get("work_id") else None
    if work is None:
        lines.append(f"- {_REASON_WORDS.get(ev.reason, ev.reason)} woke you (its record couldn't be read).")
        return "\n".join(lines)
    if work.kind == "wake":
        lines += _due(db, ev, work, zone, now)
    else:
        lines.append(f"- {_REASON_WORDS.get(ev.reason, ev.reason).capitalize()} [work:{str(work.id)[:8]}], "
                     f"recorded at {_when(work.updated_at, zone, now)}. What it says is under \"Why you're awake\".")
        changed = _since(db, ev, _utc(work.created_at), zone, now)
        if changed:
            lines += ["", f"CHANGES SINCE IT STARTED ({_when(work.created_at, zone, now)})", *changed]
    return "\n".join(lines)


def _due(db, ev: Evaluation, work: CoordinatorWork, zone: str | None, now: datetime) -> list[str]:
    """A wake she scheduled, come due: who wanted it and why, its deadline against now, what was promised and how
    it stands, what was attempted for it, and what changed since. Every line is read from a record; what isn't
    recorded is said to be unknown."""
    inp = work.input or {}
    due = _parse(inp.get("due_at")) or _utc(work.next_attempt_at)
    scheduled = _parse(inp.get("scheduled_at"))
    first = _parse(inp.get("first_scheduled_at")) or scheduled
    late = (now - due).total_seconds() if due else None
    out = [f"- What came due: [wake:{str(work.id)[:8]}]: {inp.get('about') or ''}",
           f"- It was set at {_when(scheduled, zone, now)} for {_when(due, zone, now)}: that was the time you chose "
           "(the deadline), not the time you set it."]
    if late is not None:
        out.append("- It is on time." if late < 60 else
                   f"- It is {_span(late)} late: you couldn't be woken at the time. Check whether it still matters.")
    asked = inp.get("asked_by")
    out += ["", "PURPOSE"]
    if asked == "user":
        out.append("- They asked for this: it's something you agreed to do for them, not an idea of your own.")
    elif asked == "mind":
        out.append("- It was your own idea (optional): nobody asked you for it.")
    else:
        out.append("- Who asked for it isn't recorded (it was scheduled before that was kept).")
    if inp.get("purpose") and inp.get("purpose") != inp.get("about"):
        out.append(f"- What it was for when you first set it: {inp['purpose']}")
    origin = db.get(ChatMessage, uuid.UUID(inp["origin_message"])) if inp.get("origin_message") else None
    if origin is not None and origin.conversation_id == work.conversation_id:
        text = " ".join((origin.content or "").split())
        out.append(f"- Their message when it was set ({_when(origin.created_at, zone, now)}): "
                   f"\"{text[:300]}{'…' if len(text) > 300 else ''}\"")
    elif inp.get("origin_message"):
        out.append("- The message it came from is no longer available.")
    if inp.get("their_urgency"):
        out.append(f"- What they said about urgency or consequences: \"{inp['their_urgency']}\"")
    else:
        out.append("- Nothing they said about urgency or consequences is recorded.")
    out += ["", "COMMITMENT"]
    entry = None
    if inp.get("item"):
        try:
            entry = db.get(WorkingItem, uuid.UUID(inp["item"]))
        except ValueError:
            entry = None
    if entry is not None and entry.user_id == ev.user_id:
        state = entry.status + (f": {entry.status_reason}" if entry.status_reason else "")
        out.append(f"- Your working memory has it as {entry.kind} [{str(entry.id)[:8]}] \"{entry.text}\" ({state}).")
    elif inp.get("item"):
        out.append("- The working-memory item it was about can no longer be read.")
    else:
        out.append("- No promise or intention in your working memory tracks it.")
    for rev in (inp.get("revisions") or [])[-4:]:
        out.append(f"- Moved {_when(_parse(rev.get('at')), zone, now)} from {_when(_parse(rev.get('from_due')), zone, now)}"
                   f" to {_when(_parse(rev.get('to_due')), zone, now)}: \"{rev.get('reason') or 'no reason kept'}\"")
    chain = [uuid.UUID(r["from_wake"]) for r in inp.get("revisions") or [] if r.get("from_wake")]
    attempts = list(db.scalars(select(MindEvaluation).where(MindEvaluation.work_id.in_(chain or [uuid.uuid4()]))
                               .order_by(MindEvaluation.finished_at))) if chain else []
    delivered = [a for a in attempts if a.delivery in ("delivered", "partial")]
    for a in attempts[-4:]:
        out.append(f"- When it came due before ({_when(a.finished_at, zone, now)}): {_did(a)}.")
    out.append(f"- You spoke for it {_when(delivered[-1].finished_at, zone, now)} and it was delivered." if delivered
               else "- Nothing has been delivered for it yet.")
    changed = _since(db, ev, first, zone, now)
    out += ["", f"CHANGES SINCE IT WAS FIRST SET ({_when(first, zone, now)})", *(changed or ["- (none to report)"])]
    out += ["", "OUTSTANDING"]
    if entry is not None and entry.closed_at is None:
        out.append(f"- By your working memory it is still open ({entry.status}).")
    elif entry is not None:
        out.append(f"- By your working memory it is closed ({entry.status}).")
    else:
        out.append("- Whether it is still needed isn't recorded anywhere: judge from the conversation, and don't "
                   "take unknown as unchanged.")
    return out


def _since(db, ev: Evaluation, since: datetime | None, zone: str | None, now: datetime) -> list[str]:
    """What happened in this conversation since ``since``: their messages, your messages, your evaluations."""
    if since is None or ev.conversation_id is None:
        return []
    out = []
    naive = since.astimezone(timezone.utc).replace(tzinfo=None)  # chat_messages.created_at is naive UTC
    rows = list(db.execute(select(ChatMessage.role, func.count(), func.max(ChatMessage.created_at)).where(
        ChatMessage.conversation_id == ev.conversation_id, ChatMessage.created_at > naive)
        .group_by(ChatMessage.role)))
    counts = {role: (n, at) for role, n, at in rows}
    n, at = counts.get("user", (0, None))
    out.append(f"- Their messages since: {n}" + (f", the latest {_when(at, zone, now)} (in the conversation above)."
                                                 if n else ". They haven't written since."))
    n, at = counts.get("assistant", (0, None))
    out.append(f"- Your messages since: {n}" + (f", the latest {_when(at, zone, now)}." if n else "."))
    evals = list(db.scalars(select(MindEvaluation).where(
        MindEvaluation.conversation_id == ev.conversation_id, MindEvaluation.finished_at > since,
        MindEvaluation.turn_id != ev.request_id).order_by(MindEvaluation.finished_at.desc()).limit(4)))
    for e in reversed(evals):
        out.append(f"- Evaluation {_when(e.finished_at, zone, now)}, woken by "
                   f"{_REASON_WORDS.get(e.reason, e.reason)}: {_did(e)}.")
    return out


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _span(seconds: float) -> str:
    from app.ai.mind import when as W
    return W.span(seconds)


def fresh_awareness(awareness: str | None, *, wake: bool, reported_at: str | None = None) -> str | None:
    """Their app's sense report for this evaluation, without its clock: the "Now" section is the clock, read when
    she runs. For a wake the report came with an earlier request, so it is labelled as such and its "since"
    figures (as of then) are left out."""
    if not awareness:
        return awareness
    drop = ("- Local time:",) + (("- This session",) if wake else ())
    lines = [line for line in awareness.split("\n") if not line.startswith(drop)]
    if wake:
        when = f" at {reported_at[:19].replace('T', ' ')} UTC" if reported_at else ""
        lines.insert(0, f"- (As their app last reported it{when}, with an earlier request: it may have changed.)")
    return "\n".join(lines)


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
                    job: ChatJob | None = None, user_name: str | None = None,
                    channel_identity_id: str | None = None) -> Evaluation:
    """A user's message wakes her. Blocking (call it in a thread). Never raises: what couldn't be read is said to
    her as such."""
    ev = Evaluation(user_id=user_id, conversation_id=conversation_id, request_id=turn_id, reason="user_message",
                    trigger_message_id=user_message_id, message=message, channel=channel, voice=voice, zone=zone,
                    user_name=user_name, channel_identity_id=channel_identity_id)
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
                    channel_identity_id=payload.get("channel_identity_id"), user_name=user_name, wake=info)
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
    # Memory's search and the lessons read rank by the same message: it is embedded once for both.
    with recall.shared_embeddings():
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
    _safely(lambda: _keep(ev, outcome=outcome, spoke=spoke, assistant_message_id=assistant_message_id), None)
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
                    "proposal": ev.proposal_decision, "moved": [d["from"] for d in ev.deferred],
                    "dropped": [d["wake"] for d in ev.dropped], "fulfilled": [str(i) for i in ev.fulfilled]},
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


_DELIVERY = {"completed": "delivered", "partial": "partial", "cancelled": "cancelled", "failed": "failed",
             "silent": "not_sent"}


def _keep(ev: Evaluation, *, outcome: str, spoke: bool, assistant_message_id: uuid.UUID | None) -> None:
    """The evaluation's durable record (``mind_evaluations``), and promises she marked fulfilled in it opened
    again when her words weren't delivered: intending to keep a promise isn't keeping it."""
    did = ("said" if spoke and outcome == "completed" else "partial" if spoke else
           ("silent" if ev.silent is not None else "no_words") if outcome == "silent" else
           outcome if outcome in ("cancelled", "failed") else "no_words")
    delivery = _DELIVERY.get(outcome, "failed")
    with SessionLocal() as db:
        db.execute(insert(MindEvaluation).values(
            id=uuid.uuid4(), user_id=ev.user_id, conversation_id=ev.conversation_id, turn_id=ev.request_id,
            reason=ev.reason[:24], trigger_message_id=ev.trigger_message_id if not ev.is_wake else None,
            work_id=uuid.UUID(ev.wake["work_id"]) if ev.wake and ev.wake.get("work_id") else None,
            outcome=did, delivery=delivery, assistant_message_id=assistant_message_id,
            silence_reason=ev.silent, tools=[a.get("tool") for a in ev.actions][:60],
            started_at=ev.started_at, finished_at=datetime.now(timezone.utc),
        ).on_conflict_do_nothing(constraint="uq_mind_evaluations_turn"))
        if ev.fulfilled and delivery not in ("delivered", "partial"):
            from app.services import memory_manager
            for item_id in dict.fromkeys(ev.fulfilled):
                memory_manager.reopen_undelivered(db, ev.user_id, item_id, delivery=delivery)
        db.commit()


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
            "proposal": ev.proposal_decision, "moved": ev.deferred, "dropped": ev.dropped,
            "fulfilled": [str(i) for i in ev.fulfilled]},
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
