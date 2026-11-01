"""The shared-state pipeline: user message -> scoped view -> cognition -> mouth (docs/SHARED_STATE_REFACTOR_SPEC.md,
step 3). The only reply preparation: both reply paths (chat worker, WebSocket fallback) call ``prepare``.

Adapters, one per owner, over the stores that already hold the truth (the view never replaces them):

| Section | Owner (version source) |
|---|---|
| conversation | chat: ``conversations.last_message_sequence``, message ids/sequences; notes: the cognitive notebook |
| memory context | memory: the initial check (``recall_references.for_reply``: the answer before's references, the user's standing constraints and a search for this message), each record with its fingerprint |
| cognition | Student 1 via ``cognitive_live`` (reading in every arm; the A/B arm only gates its words in the prompt) |
| thinking decision | nobody yet: ``required=None`` (Jev is step 4) |
| work / output | the chat job (id, attempt, lease, cancel key) or the socket's in-process turn |
| personality | the character page file (content hash) |
| inner state, activities, situation, mind | not projected yet: ``disabled``/``unavailable`` with the reason |

Every user message gets an initial memory check before cognition (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md
§7), whether or not anyone later decides memory is needed: Student 1 and the mouth read the same evidence bundle,
and the receipt records what each consumer saw. Student 2's snapshot has no memory input yet, so its recall choice
means searching further (the mouth's tools). If the search fails, the answer before's references are re-read
instead and the turn says the search failed. Cognition always runs; when it can't, the turn is *degraded* (named to
the mouth, published on the turn stream, traced), never a silent normal reply. What the student can't assess
(whether older memory or careful thinking is needed) is said to the mouth as a limit of this turn, and such a
turn is ``unassessed``: answered by the mouth, never reported as confirmed fast-path eligible.

One trace line per turn in ``SHARED_STATE_LOG_PATH``: event -> dependencies -> cognition -> route and reasons ->
work -> outcome and delivery. Ids, labels, counts and timings only: no message text, no memory content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select

from app.ai.Cognitive.simple.contracts import SCHEMA_VERSION as SIMPLE_SCHEMA
from app.ai.recall.contracts import RecallResult
from app.ai.state import student_two as student_contract
from app.ai.state.contracts import (
    SCHEMA_VERSION, Assessed, CacheEntry, CognitionView, ComponentHealth, ConversationView, Envelope,
    EvidenceAssessment, Interpretation, MemoryContextView, MessageRef, NoteRef, OutputView,
    Projection, RecallDecision, SharedView, SourceRef, ThinkingDecision, WorkView, to_jsonable,
)
from app.ai.state.routing import RouteDecision, route
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation
from app.db.session import SessionLocal
from app.ai.state.decisions import PendingDecision, PendingWork
from app.services import cognitive_live, coordinator, learning_capture, recall_references, student_two

logger = logging.getLogger(__name__)

# Message refs kept in the view: the student's window plus the current message.
RECENT_REFS = 8


@dataclass
class FastPathTurn:
    """One reply's prepared state: the view, its route, and what the mouth's context is built from."""

    view: SharedView
    decision: RouteDecision
    live: cognitive_live.LivePrep | None
    recall_turn: recall_references.TurnRecall | None
    boundary: str
    started: float = field(default_factory=time.monotonic)
    timings: dict[str, int] = field(default_factory=dict)
    # Student 2's decision for this message (None: it didn't run), and its words for the mouth.
    student: student_two.Outcome | None = None
    decision_brief: str | None = None
    # A memory search ran for this message (the initial check, or the recall route's retry after it failed): its
    # result replaces the cache in the prompt.
    searched: bool = False
    # Background work committed for this request (thinking routes), and what cognition decided the reply does:
    # respond / acknowledge / partial / ask / silent ("respond" by labelled operational fallback when Student 2
    # couldn't decide).
    pending: tuple[PendingWork, ...] = ()
    immediate_action: str = "respond"
    immediate_origin: str | None = None

    @property
    def silent(self) -> bool:
        return self.immediate_action == "silent"

    @property
    def recall_rendered(self) -> str | None:
        return self.recall_turn.rendered if self.searched and self.recall_turn is not None else None

    @property
    def long_term_memory(self) -> str | None:
        return self.view.memory.legacy

    @property
    def cached_memory(self) -> str | None:
        return None if self.searched else self.view.memory.rendered

    def event(self) -> dict[str, Any]:
        """The ``route`` frame for the turn stream: status only, so a client can show a degraded turn."""
        return {"type": "route", "route": self.decision.route, "outcome": self.decision.outcome,
                "eligibility": self.decision.eligibility, "recall": self.decision.recall,
                "thinking": self.decision.thinking,
                "degraded": list(self.decision.degraded), "limits": list(self.decision.limits)}


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #


def _conversation(user_id: uuid.UUID, conversation_id: uuid.UUID | None,
                  trigger_id: uuid.UUID | None) -> ConversationView:
    if conversation_id is None:
        return ConversationView(Projection.missing("chat", "no conversation"))
    try:
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                return ConversationView(Projection.missing("chat", "conversation not found"))
            trigger = db.get(ChatMessage, trigger_id) if trigger_id else None
            if trigger is not None and trigger.conversation_id != conversation_id:
                trigger = None
            query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            newer = 0
            if trigger is not None and trigger.conversation_sequence is not None:
                newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.conversation_sequence > trigger.conversation_sequence)) or 0
                query = query.where(ChatMessage.conversation_sequence <= trigger.conversation_sequence)
            rows = list(db.scalars(query.order_by(ChatMessage.conversation_sequence.desc()).limit(RECENT_REFS)))
            revision = conversation.last_message_sequence
            updated = conversation.updated_at
    except Exception:  # noqa: BLE001 - the view says it couldn't read, the turn goes on
        logger.exception("shared_state: reading the conversation failed")
        return ConversationView(Projection.missing("chat", "read failed"))
    recent = tuple(MessageRef(r.id, r.role, r.conversation_sequence, r.status, r.heard_chars)
                   for r in reversed(rows))
    return ConversationView(
        Projection("chat", "available", version=str(revision), updated_at=updated,
                   source_refs=tuple(SourceRef("message", str(m.id), str(m.sequence)) for m in recent)),
        conversation_id=conversation_id, revision=revision,
        trigger_message_id=trigger.id if trigger is not None else None,
        latest_message_id=recent[-1].id if recent else None, recent=recent,
        commitments=Projection("memory_manager", "available", reason="working items, in the working-memory view"),
        newer_than_trigger=newer,
    )


def _memory(user_id: uuid.UUID, conversation_id: uuid.UUID | None, trigger_id: uuid.UUID | None,
            message: str, zone: str | None) -> tuple[MemoryContextView, recall_references.TurnRecall | None]:
    """The initial memory check, before cognition: memory's own reply search (the answer before's references,
    the user's standing constraints, and a search for this message). It doesn't wait for anyone to decide it's
    needed. When the search fails, the answer before's references are re-read instead, and the view says the
    search failed (never "nothing found")."""
    turn = recall_references.for_reply(user_id, conversation_id, trigger_id, message, zone)
    status = turn.result.status
    if status == "failed":
        turn = recall_references.cached_for_reply(user_id, conversation_id, trigger_id, zone)
        turn.result.coverage["search"] = "failed"  # the receipt says the search failed, not what the cache held
    result = turn.result
    gone = tuple(result.coverage.get("references_gone", ()))
    gone += ("deleted",) * int(result.coverage.get("references_deleted", 0))
    entries = tuple(CacheEntry(str(r.ref), r.fingerprint, r.applicability, r.corrected, r.content)
                    for r in result.records)
    if result.status == "failed":
        meta = Projection.missing("memory", "the search and rereading the cached references both failed")
    else:
        what = (f"searched for this message; {len(entries)} found" if status != "failed" else
                f"the search failed; the last answer's references re-read, {len(entries)} current")
        meta = Projection("memory", "available", version=hashlib.sha256(
            ",".join(f"{e.ref}#{e.fingerprint}" for e in entries).encode()).hexdigest()[:16],
            updated_at=turn.now, source_refs=tuple(SourceRef(*e.ref.split(":", 1), e.fingerprint) for e in entries),
            reason=f"{what}, {len(gone)} gone")
    return MemoryContextView(meta, entries, gone, status, turn.rendered, turn.legacy), turn


@lru_cache(maxsize=1)
def _student_version() -> str:
    s = get_settings()
    path = s.cognitive_live_student_dir or str(cognitive_live.DEFAULT_STUDENT_DIR)
    try:
        stamp = int(os.path.getmtime(os.path.join(path, "student_one.pt")))
    except OSError:
        stamp = 0
    return f"{SIMPLE_SCHEMA}:{os.path.basename(path.rstrip('/'))}:{stamp}"


def _cognition(live: cognitive_live.LivePrep | None, trigger: str | None,
               memory: MemoryContextView) -> CognitionView:
    versions = {"memory_cache": memory.meta.version or memory.meta.status}
    if live is None or live.interpretation is None:
        return CognitionView(Projection.missing("cognition", "Student 1 couldn't read the message (see "
                                                "cognitive_live log)"), trigger_ref=trigger, input_versions=versions)
    data = live.interpretation
    understand = data["understand"]
    versions["student_1"] = _student_version()
    interpretation = Interpretation(
        acts=tuple(a for a, on in understand["acts"].items() if on), tone=understand["tone"],
        request=understand["request"],
        probabilities={"acts": data["acts"], "tone": data["tone"], "request": data["request"]},
        model=versions["student_1"])
    return CognitionView(
        Projection("cognition", "available", version=versions["student_1"],
                   updated_at=datetime.now(timezone.utc), reason="Student 1"),
        trigger_ref=trigger, input_versions=versions, interpretation=interpretation,
        missing_evidence=EvidenceAssessment(
            "not_assessed", "not_assessed",
            reason="Student 1 has no output for whether older memory is needed"),
        evidence_refs=tuple(e.ref for e in memory.entries),
    )


@lru_cache(maxsize=1)
def _personality() -> Projection:
    try:
        digest = hashlib.sha256(cognitive_live.character_page().encode()).hexdigest()[:16]
    except OSError:
        return Projection.missing("character", "character page unreadable")
    return Projection("character", "available", version=digest, source_refs=(SourceRef("file", "columbina.md"),))


def _health(cognition: CognitionView, memory: MemoryContextView) -> tuple[ComponentHealth, ...]:
    return (
        ComponentHealth("cognition", "disabled" if cognition.meta.status == "disabled" else "live",
                        cognition.meta.status == "available", cognition.meta.reason),
        ComponentHealth("memory_cache", "disabled" if memory.meta.status == "disabled" else "live",
                        memory.meta.status == "available", memory.meta.reason),
        ComponentHealth("recall", "live", memory.recall_status != "failed",
                        "runs before cognition on every message; her tools can search further"),
        ComponentHealth("mind", "live", False, "background thinking on the thinking routes"),
        ComponentHealth("inner_state", "shadow", False, "observational only, no influence"),
    )


def _work(job: ChatJob | None, turn_id: uuid.UUID) -> WorkView:
    if job is None:
        return WorkView(Projection("socket", "available", reason="in-process turn (local fallback)"),
                        work_id=str(turn_id), status="running", cancellation="socket stop event")
    return WorkView(Projection("chat_worker", "available", version=str(job.attempts)),
                    work_id=str(job.id), status=job.status, attempt=job.attempts,
                    lease_expires_at=job.lease_expires_at, cancellation=f"turn cancel key ({turn_id})")


def _decide(trigger: str, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None,
            user_message_id: uuid.UUID | None, message: str, zone: str | None,
            recall_result: RecallResult | None = None, recall_failed: bool = False,
            capture: learning_capture.Ctx | None = None,
            ) -> tuple[student_two.Outcome, RecallDecision, ThinkingDecision]:
    """Student 2 decides the reply and its work from the live situation; recall and thinking follow from its
    work part. When it can't decide (server down, unreadable or invalid answer) both stay unassessed.
    ``capture``: its attempt's id and links, decided before it runs (the sources come from what it read)."""
    try:
        memory_refs = tuple(str(r.ref) for r in recall_result.records) if recall_result is not None else ()
        with SessionLocal() as db:
            live = student_two.gather(db, user_id=user_id, conversation_id=conversation_id,
                                      trigger_message_id=user_message_id, message=message, zone=zone,
                                      memory=student_two.memory_for(db, recall_result, failed=recall_failed),
                                      memory_refs=memory_refs)
        if capture is not None:
            capture = replace(capture, game_session_id=_session_of(live), sources=tuple(dict.fromkeys(
                (*live.sources, *(r["ref"] for r in capture.input_refs)))))
        outcome = student_two.decide(live, capture=capture)
    except Exception as exc:  # noqa: BLE001 - the view says it couldn't decide, the turn goes on
        logger.exception("shared_state: Student 2 failed")
        outcome = student_two.Outcome("unavailable", error=f"{type(exc).__name__}"[:200])
        if capture is not None:
            # Known failure before or around the call: an attempt with nothing captured but the failure.
            outcome.decision_id, outcome.capture = str(capture.attempt_id), capture
            learning_capture.attempt(capture, learner="student_2", provider=None, model=None, input={},
                                     output=None, status="error", error=outcome.error,
                                     started_at=datetime.now(timezone.utc), produced_at=None,
                                     absent=learning_capture.NOT_CAPTURED + ("input", "output"))
    status = {"ok": "available"}.get(outcome.status, "unavailable")
    reason = outcome.error or ("; ".join(outcome.problems)[:300] if outcome.problems else None)
    meta = Projection("student_2", status, version=outcome.model, updated_at=datetime.now(timezone.utc),
                      reason=reason)
    if not outcome.ok:
        return outcome, RecallDecision(meta), ThinkingDecision(meta, trigger_ref=trigger, reason=reason or "no decision")
    d = outcome.decision
    recall = RecallDecision(meta, student_contract.recall_need(d), "student_2", assessment_id=outcome.decision_id)
    thinking_choice = student_contract.thinking_need(d)
    thinking = ThinkingDecision(meta, trigger_ref=trigger, required=thinking_choice == "required",
                                decider="student_2", choice=thinking_choice)
    return outcome, recall, thinking


def _session_of(live) -> uuid.UUID | None:
    try:
        return uuid.UUID(live.activity.key) if live.activity is not None else None
    except ValueError:
        return None


def _captures(*, user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
              turn_id: uuid.UUID, job: ChatJob | None, memory: MemoryContextView
              ) -> tuple[learning_capture.Ctx, learning_capture.Ctx]:
    """Student 1's and Student 2's attempt ids for this turn, before either runs. A chat job's attempt n is the
    same record however often it's saved; attempt n+1 (a reclaimed job) is a new attempt linked to it."""
    tries = job.attempts if job is not None else None
    refs = tuple({"ref": e.ref, "fingerprint": e.fingerprint, "applicability": e.applicability,
                  "corrected": bool(e.corrected)} for e in memory.entries)
    trigger_ref = (f"message:{user_message_id}",) if user_message_id else ()

    def ctx(learner: str, **extra) -> learning_capture.Ctx:
        return learning_capture.Ctx(
            user_id=user_id, attempt_id=learning_capture.attempt_id_for(turn_id, learner, tries),
            prior_attempt_id=learning_capture.prior_for(turn_id, learner, tries), trigger="user_message",
            conversation_id=conversation_id, turn_id=turn_id, source_message_id=user_message_id, **extra)

    s1_refs = tuple(r for r in refs if r["ref"].split(":", 1)[0] in ("fact", "episode"))
    s1 = ctx("student_1", input_refs=s1_refs, sources=trigger_ref + tuple(r["ref"] for r in s1_refs))
    s2 = ctx("student_2", input_refs=refs, upstream={"student_1": str(s1.attempt_id)})
    return s1, s2


def _search(user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
            message: str, zone: str | None, memory: MemoryContextView
            ) -> tuple[MemoryContextView, recall_references.TurnRecall | None]:
    """The recall route: memory's own reply search (the previous answer's references plus a search for this
    message). Its status stays as memory says it: matched, no_match, partial or failed."""
    turn = recall_references.for_reply(user_id, conversation_id, user_message_id, message, zone)
    return replace(memory, recall_status=turn.result.status), turn


# --------------------------------------------------------------------------- #
# The turn
# --------------------------------------------------------------------------- #


def prepare(*, user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
            history: list[dict[str, Any]], message: str, zone: str | None, turn_id: uuid.UUID,
            reply_message_id: uuid.UUID | None, channel: str = "web", voice: bool = False,
            job: ChatJob | None = None, user_name: str | None = None) -> FastPathTurn:
    """Build the view, run cognition, route. Blocking (call it in a thread). Never raises: any part that
    fails is ``unavailable`` in the view, and the route says what that costs."""
    started = time.monotonic()
    timings: dict[str, int] = {}

    def lap(name: str, since: float) -> float:
        now = time.monotonic()
        timings[name] = round((now - since) * 1000)
        return now

    at = started
    # A new message: unfinished work for earlier ones in this conversation can't answer it.
    superseded = coordinator.supersede_older(user_id, conversation_id, user_message_id)
    conversation = _conversation(user_id, conversation_id, user_message_id)
    at = lap("conversation_ms", at)
    memory, recall_turn = _memory(user_id, conversation_id, user_message_id, message, zone)
    at = lap("initial_recall_ms", at)
    searched = memory.recall_status != "failed"
    capture_1, capture_2 = _captures(user_id=user_id, conversation_id=conversation_id,
                                     user_message_id=user_message_id, turn_id=turn_id, job=job, memory=memory)
    live = None
    if conversation_id is not None:
        live = cognitive_live.prepare(user_id=user_id, conversation_id=conversation_id, history=history,
                                      message=message, long_term_memory=memory.legacy, memory_searched=searched,
                                      capture=capture_1, student_version=_student_version())
        if recall_turn is not None:
            recall_turn.consumed("student_1", [e.ref for e in memory.entries if e.ref.split(":", 1)[0]
                                               in ("fact", "episode")], memory.legacy)
    if live is None or live.interpretation is None:
        # Student 2 still names the reading it would have had: the missing link is visible, never invented.
        capture_2 = replace(capture_2, upstream={"student_1": str(capture_1.attempt_id), "student_1_status": (
            "not_run" if conversation_id is None else "failed")})
    if recall_turn is not None:
        shown = student_two.rendering() in student_contract.MEMORY_RENDERINGS
        recall_turn.consumed("student_2", [e.ref for e in memory.entries] if shown else [], None,
                             note=None if shown else student_contract.SNAPSHOT_MEMORY_NOTE)
    lap("cognition_ms", at)
    trigger = f"message:{user_message_id}" if user_message_id else f"turn:{turn_id}"
    cognition = _cognition(live, trigger, memory)
    if live is not None and live.state.get("notes") is not None:
        notes = Projection("cognitive_live", "available", reason="derived notes, not messages")
        note_refs = tuple(NoteRef(n["kind"], n.get("opened_turn")) for n in live.state["notes"])
    else:
        notes, note_refs = Projection.off("cognitive_live", "no notebook this turn"), ()
    conversation = replace(conversation, notes=notes, note_refs=note_refs)
    at = time.monotonic()
    student, recall_decision, thinking = _decide(trigger, user_id=user_id, conversation_id=conversation_id,
                                                 user_message_id=user_message_id, message=message, zone=zone,
                                                 recall_result=recall_turn.result if recall_turn else None,
                                                 recall_failed=memory.recall_status == "failed",
                                                 capture=capture_2)
    at = lap("student_2_ms", at)
    d = student.decision if student.ok else None
    if d is not None:
        cognition = replace(cognition, conversational_action=Assessed(d["communication"], "student_2",
                                                                      d["intention"].strip()[:200] or None),
                            communication_preference=Assessed("speak" if student_contract.speaks(d) else "hold",
                                                              "student_2"))
    view = SharedView(
        envelope=Envelope(SCHEMA_VERSION, user_id, conversation_id, conversation.revision,
                          datetime.now(timezone.utc), trigger),
        personality=_personality(),
        conversation=conversation,
        memory=memory,
        cognition=cognition,
        thinking=thinking,
        recall=recall_decision,
        work=_work(job, turn_id),
        output=OutputView(Projection("delivery", "available"), channel=channel, conversation_id=conversation_id,
                          turn_id=turn_id, reply_message_id=reply_message_id,
                          reservation="chat job lease + one assistant row per turn" if job is not None
                          else "the socket that owns the turn", voice=voice),
        inner_state=Projection.off("inner_state", "observational only: never steers a reply"),
        activities=Projection.missing("activities", "not projected yet; the game summary still reaches her "
                                                    "through its own section"),
        situation=Projection.missing("observation", "not projected yet"),
        mind=Projection.off("mind", "not routed to yet (asynchronous thinking: later in step 4)"),
        health=_health(cognition, memory),
    )
    from app.services import background_components
    view = replace(view, components=background_components.projections(user_id, conversation_id))
    decision = route(view)
    # What she does now: Student 2's choice, silence included; "respond" only as a labelled operational fallback
    # when it couldn't decide (a failure is never read as silence).
    if d is not None:
        action, origin = ("silent" if d["communication"] in ("silent", "defer") else d["communication"]), "student_2"
    else:
        action, origin = "respond", "operational_fallback"
    effect = None
    if d is not None:
        with SessionLocal() as db:
            effect = student_two.apply(db, student, user_id=user_id, message_id=user_message_id)
        lap("student_2_effect_ms", at)
    student_two.log(student, user_id=user_id, conversation_id=conversation_id, trigger=trigger)
    if decision.runs_recall and action != "silent":
        if searched:
            # The initial check already searched for this message: Student 2 asking for recall means more than
            # it found, which is the mouth's memory tools (said to it as a limit), not the same search again.
            decision = replace(decision, limits=decision.limits + ("additional_recall_requested",))
        else:
            memory, searched_turn = _search(user_id, conversation_id, user_message_id, message, zone, view.memory)
            lap("recall_ms", at)
            if searched_turn is not None and memory.recall_status != "failed":
                recall_turn, searched = searched_turn, True
                view = replace(view, memory=memory)
            else:
                decision = replace(decision, degraded=decision.degraded + ("recall_unavailable",))
    pending: tuple[PendingWork, ...] = ()
    if decision.schedules_mind and conversation_id is not None:
        reading = None
        if cognition.interpretation is not None:
            i = cognition.interpretation
            reading = {"doing": list(i.acts), "tone": i.tone, "wants": i.request}
        committed = coordinator.schedule(
            user_id=user_id, conversation_id=conversation_id, request_id=turn_id, trigger_message_id=user_message_id,
            with_recall=decision.route == "recall_then_mind",
            work_input={"message": message, "interpretation": reading, "zone": zone, "channel": channel,
                        "user_name": user_name, "cached_memory": view.memory.rendered,
                        "think_about": d["think_about"] if d is not None else "",
                        "evidence_versions": {e.ref: f"{e.fingerprint}:{e.applicability}:{int(e.corrected)}" for e in view.memory.entries}},
            input_versions={k: v for k, v in view.dependencies().items() if v is not None})
        if committed is None:
            # No work exists, so nobody may say thinking started.
            decision = replace(decision, degraded=decision.degraded + ("mind_unavailable",))
        else:
            pending = committed
            coordinator.decide_immediate(
                user_id=user_id, conversation_id=conversation_id, request_id=turn_id, trigger_ref=trigger,
                interpretation=reading, pending=pending, channel=channel, voice=voice,
                decided=PendingDecision(action, "student_2", True))
            view = replace(view, mind=Projection("coordinator", "pending", reason=f"{len(pending)} work item(s) "
                                                 "committed for this request"))
        lap("schedule_ms", at)
    timings["prepare_ms"] = round((time.monotonic() - started) * 1000)
    if superseded:
        timings["superseded_work"] = superseded
    brief = student_contract.brief(d, student.situation, effect=effect) if d is not None and action != "silent" else None
    return FastPathTurn(view, decision, live, recall_turn,
                        boundary_text(view, decision, searched=searched, pending=pending), started, timings,
                        student=student, decision_brief=brief, searched=searched, pending=pending,
                        immediate_action=action, immediate_origin=origin)


_LIMIT_WORDS = {
    # Two independent unknowns, each said on its own: one may be decided while the other isn't.
    "recall_need_not_assessed": "Nobody checked whether this message needs anything older than what you have.",
    "thinking_need_not_assessed": "Nobody checked whether this message needs careful thinking.",
    "request_outside_talking": ("They seem to want something beyond talking: use your tools or specialists if "
                                "one fits, and don't promise what none of them can do."),
    "memory_cache_unavailable": ("The memories from your last answer couldn't be read just now: don't say "
                                 "nothing is recorded."),
    "memory_cache_changed": "Something your last answer used has since been removed or changed.",
    "recall_need_uncertain": "It's unclear whether this message needs anything older than what you have.",
    "additional_recall_requested": ("Your decision step judged this message needs more from memory than the "
                                    "check above: if what you recall doesn't cover it, search with your memory "
                                    "tools before answering."),
    "thinking_need_uncertain": "It's unclear whether this message needs careful thinking.",
}
_DEGRADED_WORDS = {
    "cognition_unavailable": ("Your quick reading of their message didn't work this turn: take the message "
                              "only as written, and if what they want isn't clear, ask."),
    "decision_unavailable": ("Your decision step didn't work this turn: answer from the conversation, and don't "
                             "start or promise anything you haven't decided."),
    "recall_unavailable": ("This message needs memory, and searching it isn't available here: say you can't "
                           "check that right now, and answer only what the conversation supports."),
    "mind_unavailable": ("This message needs careful thinking, and it couldn't be started: say you haven't "
                         "thought it through, and give only what you can stand behind now."),
}

_PENDING_STATUS = {"queued": "queued, not started yet", "waiting_for_memory": "waiting for a memory lookup",
                   "running": "under way"}

_SEARCH_WORDS = {
    "matched": "Memory was searched for this message: what it found is under \"What you recall\".",
    "partial": "Memory was searched for this message, but part of it couldn't be read: what's there is incomplete.",
    "no_match": ("Memory was searched for this message and found nothing that matches. That isn't proof it "
                 "didn't happen: say you can't find a memory of it."),
    "failed": "Memory was searched for this message and the search FAILED: don't say nothing is recorded.",
    "not_searched": "Memory search didn't run.",
}


def boundary_text(view: SharedView, decision: RouteDecision, *, searched: bool = False,
                  pending: tuple[PendingWork, ...] = ()) -> str:
    """What this turn did and didn't have, for the mouth (the rules for it are in ``prompts.py``)."""
    lines = []
    for code in decision.degraded:
        lines.append(f"- {_DEGRADED_WORDS.get(code, code)}")
    if pending:
        steps = "; ".join(f"{'a memory lookup' if p.kind == 'recall' else 'thinking it through'}: "
                          f"{_PENDING_STATUS.get(p.status, p.status)}" for p in pending)
        lines.append(f"- You're working this out in the background ({steps}). You haven't reached a conclusion: "
                     "don't present one as settled or replace it with a quick answer of your own. You can answer "
                     "a part that doesn't depend on it. When it's done you may come back to them.")
    memory = view.memory
    if searched:
        lines.append(f"- {_SEARCH_WORDS.get(memory.recall_status, _SEARCH_WORDS['failed'])}")
    elif memory.recall_status == "failed":
        lines.append(f"- {_SEARCH_WORDS['failed']}")
        if memory.meta.status == "available":
            have = (f"{len(memory.entries)} memories from your last answer, re-read now" if memory.entries
                    else "no memories from your last answer")
            lines.append(f"- You have the recent conversation and {have}.")
    elif memory.meta.status == "available":
        have = f"{len(memory.entries)} memories from your last answer, re-read now" if memory.entries else (
            "no memories from your last answer")
        lines.append(f"- Memory was not searched for this message. You have the recent conversation and {have}.")
    else:
        lines.append("- Memory was not searched for this message. You have the recent conversation only.")
    for code in decision.limits:
        if _LIMIT_WORDS.get(code):
            lines.append(f"- {_LIMIT_WORDS[code]}")
    return "\n".join(lines)


def finish(turn: FastPathTurn | None, *, outcome: str, reply: str, assistant_message_id: uuid.UUID | None,
           heard_chars: int | None = None) -> None:
    """The turn's trace line: what it rested on, how it was routed, what was delivered. Never raises."""
    if turn is None:
        return
    _record_reply(turn, outcome=outcome, reply=reply, assistant_message_id=assistant_message_id)
    try:
        view, decision = turn.view, turn.decision
        recall_turn = turn.recall_turn
        searched = recall_turn.tool_calls if recall_turn is not None else 0
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "schema": SCHEMA_VERSION,
            "user_id": str(view.envelope.user_id),
            "conversation_id": str(view.envelope.conversation_id) if view.envelope.conversation_id else None,
            "event": view.envelope.last_event_id,
            "revision": view.envelope.revision,
            "newer_than_trigger": view.conversation.newer_than_trigger,
            "dependencies": view.dependencies(),
            "cognition": {
                "status": view.cognition.meta.status, "reason": view.cognition.meta.reason,
                "acts": list(view.cognition.interpretation.acts) if view.cognition.interpretation else None,
                "tone": view.cognition.interpretation.tone if view.cognition.interpretation else None,
                "request": view.cognition.interpretation.request if view.cognition.interpretation else None,
                "action": to_jsonable(view.cognition.conversational_action),
                "missing_evidence": view.cognition.missing_evidence.assessment,
            },
            "thinking": {"required": view.thinking.required, "decider": view.thinking.decider,
                         "choice": view.thinking.choice},
            "student_2": _student_trace(turn.student),
            "recall": {"ran": turn.searched, "status": view.memory.recall_status,
                       "records": len(recall_turn.result.records) if turn.searched and recall_turn else 0},
            "route": {"route": decision.route, "eligibility": decision.eligibility,
                      "supported": decision.supported, "outcome": decision.outcome,
                      "degraded": list(decision.degraded), "limits": list(decision.limits),
                      "reasons": list(decision.reasons)},
            "sections": {name: getattr(view, name).status if isinstance(getattr(view, name), Projection)
                         else getattr(view, name).meta.status
                         for name in ("personality", "conversation", "memory", "cognition", "thinking", "work",
                                      "output", "inner_state", "activities", "situation", "mind")},
            "work": {"id": view.work.work_id, "attempt": view.work.attempt,
                     "background": [{"id": p.work_id, "kind": p.kind, "status": p.status} for p in turn.pending]},
            "immediate": {"action": turn.immediate_action, "origin": turn.immediate_origin},
            "calls": {"fresh_recall_before_reply": int(turn.searched), "mind_scheduled": int(bool(turn.pending)),
                      "student_2": int(turn.student is not None and turn.student.status != "unavailable"),
                      "memory_tool_calls_by_mouth": searched},
            # Her own search during the reply: the memory in hand wasn't enough for this message.
            "mouth_escalated": ["searched_memory"] if searched else [],
            "delivery": {"outcome": outcome, "assistant_message_id": str(assistant_message_id)
                         if assistant_message_id else None, "reply_chars": len(reply or ""),
                         "heard_chars": heard_chars},
            "timings": turn.timings | {"total_ms": round((time.monotonic() - turn.started) * 1000)},
        }
        _write(record)
    except Exception:  # noqa: BLE001
        logger.exception("shared_state: writing the trace failed")


def _record_reply(turn: FastPathTurn, *, outcome: str, reply: str, assistant_message_id: uuid.UUID | None) -> None:
    """Delivery's record of this turn: what Student 2 intended next to what the mouth stored. Never raises."""
    try:
        view, student = turn.view, turn.student
        d = student.decision if student is not None and student.ok else None
        intended = {"communication": d["communication"] if d else None, "move": d.get("move") if d else None,
                    "immediate_action": turn.immediate_action, "origin": turn.immediate_origin,
                    "decision_status": student.status if student is not None else "not_run"}
        learning_capture.reply(
            user_id=view.envelope.user_id, attempt_id=student.decision_id if student is not None and student.capture
            else None, turn_id=view.output.turn_id, conversation_id=view.envelope.conversation_id, outcome=outcome,
            assistant_message_id=assistant_message_id, reply_chars=len(reply or ""), intended=intended,
            channel=view.output.channel, voice=view.output.voice,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=time.monotonic() - turn.started))
    except Exception:  # noqa: BLE001
        logger.exception("shared_state: recording the delivery failed")


def _student_trace(outcome: student_two.Outcome | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    d = outcome.decision or {}
    return {"id": outcome.decision_id, "status": outcome.status, "model": outcome.model,
            "latency_ms": outcome.latency_ms, "error": outcome.error, "problems": outcome.problems,
            "communication": d.get("communication"), "work": d.get("work"), "target": d.get("target"),
            "effect": outcome.effect}


def _write(record: dict[str, Any]) -> None:
    path = get_settings().shared_state_log_path
    if not path:
        return
    if not os.path.isabs(path):
        path = os.path.join(str(cognitive_live.BACKEND), path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
