"""The shared-state pipeline: user message -> scoped view -> cognition -> mouth (docs/SHARED_STATE_REFACTOR_SPEC.md,
step 3). Off unless ``SHARED_STATE_PIPELINE_ENABLED``; with it off nothing here runs.

Adapters, one per owner, over the stores that already hold the truth (the view never replaces them):

| Section | Owner (version source) |
|---|---|
| conversation | chat: ``conversations.last_message_sequence``, message ids/sequences; notes: the cognitive notebook |
| memory context | memory: ``recall.reread`` of the answer before's references, each with its fingerprint |
| cognition | Student 1 via ``cognitive_live`` (reading in every arm; the A/B arm only gates its words in the prompt) |
| thinking decision | nobody yet: ``required=None`` (Jev is step 4) |
| work / output | the chat job (id, attempt, lease, cancel key) or the socket's in-process turn |
| personality | the character page file (content hash) |
| inner state, activities, situation, mind | not projected yet: ``disabled``/``unavailable`` with the reason |

The turn runs no fresh memory search before the reply: the cache is what the last answer used, re-read now, so
a correction or deletion since is seen. Cognition always runs; when it can't, the turn is *degraded* (named to
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
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select

from app.ai.Cognitive.simple.contracts import SCHEMA_VERSION as SIMPLE_SCHEMA
from app.ai.state import assessment as assessment_contract
from app.ai.state.contracts import (
    SCHEMA_VERSION, Assessed, CacheEntry, CognitionView, ComponentHealth, ConversationView, Envelope,
    EvidenceAssessment, Interpretation, MemoryContextView, MessageRef, NoteRef, OutputView,
    Projection, RecallDecision, SharedView, SourceRef, ThinkingDecision, WorkView, to_jsonable,
)
from app.ai.state.routing import RouteDecision, route
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation
from app.db.session import SessionLocal
from app.ai.state.decisions import PendingWork
from app.services import cognitive_live, coordinator, jev_assessment, recall_references

logger = logging.getLogger(__name__)

# Message refs kept in the view: the student's window plus the current message.
RECENT_REFS = 8


def enabled() -> bool:
    return bool(get_settings().shared_state_pipeline_enabled)


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
    assessment: assessment_contract.Assessment | None = None
    # A memory search ran for this message (the recall route): its result replaces the cache in the prompt.
    searched: bool = False
    # Background work committed for this request (thinking routes), and what cognition decided the immediate
    # reply does while it runs: respond / acknowledge / partial / silent ("respond" by labelled fallback while
    # the student can't decide it).
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
        commitments=(Projection("memory_manager", "available", reason="working items, in the working-memory view")
                     if get_settings().memory_manager_enabled else
                     Projection.off("memory_manager", "MEMORY_MANAGER_ENABLED is off")),
        newer_than_trigger=newer,
    )


def _memory(user_id: uuid.UUID, conversation_id: uuid.UUID | None, trigger_id: uuid.UUID | None,
            zone: str | None) -> tuple[MemoryContextView, recall_references.TurnRecall | None]:
    if not recall_references.enabled():
        return MemoryContextView(Projection.off(
            "memory", "shared recall is off: there's no reference cache, and nothing was searched")), None
    turn = recall_references.cached_for_reply(user_id, conversation_id, trigger_id, zone)
    result = turn.result
    gone = tuple(result.coverage.get("references_gone", ()))
    gone += ("deleted",) * int(result.coverage.get("references_deleted", 0))
    entries = tuple(CacheEntry(str(r.ref), r.fingerprint, r.applicability, r.corrected, r.content)
                    for r in result.records)
    if result.status == "failed":
        meta = Projection.missing("memory", "rereading the cached references failed")
    else:
        meta = Projection("memory", "available", version=hashlib.sha256(
            ",".join(f"{e.ref}#{e.fingerprint}" for e in entries).encode()).hexdigest()[:16],
            updated_at=turn.now, source_refs=tuple(SourceRef(*e.ref.split(":", 1), e.fingerprint) for e in entries),
            reason=f"the last answer's references, re-read; {len(entries)} current, {len(gone)} gone")
    return MemoryContextView(meta, entries, gone, "not_searched", turn.rendered, turn.legacy), turn


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
    s = get_settings()
    versions = {"memory_cache": memory.meta.version or memory.meta.status}
    if s.cognitive_live_student == "off":
        return CognitionView(Projection.off("cognition", "COGNITIVE_LIVE_STUDENT is off: no reading"),
                             trigger_ref=trigger, input_versions=versions)
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
                   updated_at=datetime.now(timezone.utc), reason=f"arm {live.arm}"),
        trigger_ref=trigger, input_versions=versions, interpretation=interpretation,
        # A user message always gets an answer: a rule, not something the student predicted.
        conversational_action=Assessed("respond", "policy", "a user message is answered"),
        missing_evidence=EvidenceAssessment(
            "not_assessed", "not_assessed",
            reason="Student 1 has no output for whether older memory is needed"),
        communication_preference=Assessed("speak", "policy", "the user is waiting for a reply"),
        evidence_refs=tuple(e.ref for e in memory.entries),
    )


@lru_cache(maxsize=1)
def _personality() -> Projection:
    if not get_settings().cognitive_live_character:
        return Projection.off("character", "COGNITIVE_LIVE_CHARACTER is off")
    try:
        digest = hashlib.sha256(cognitive_live.character_page().encode()).hexdigest()[:16]
    except OSError:
        return Projection.missing("character", "character page unreadable")
    return Projection("character", "available", version=digest, source_refs=(SourceRef("file", "columbina.md"),))


def _health(cognition: CognitionView, memory: MemoryContextView) -> tuple[ComponentHealth, ...]:
    s = get_settings()
    return (
        ComponentHealth("cognition", "disabled" if cognition.meta.status == "disabled" else "live",
                        cognition.meta.status == "available", cognition.meta.reason),
        ComponentHealth("memory_cache", "disabled" if memory.meta.status == "disabled" else "live",
                        memory.meta.status == "available", memory.meta.reason),
        ComponentHealth("recall", "live" if s.recall_enabled else "disabled", s.recall_enabled,
                        "runs before the reply only on the recall route; her tools can still search"),
        ComponentHealth("mind", "live" if s.mind_enabled else "disabled", False, "not routed to (step 4)"),
        ComponentHealth("inner_state", "shadow" if s.mind_inner_state_enabled else "disabled", False,
                        "observational only, no influence"),
    )


def _work(job: ChatJob | None, turn_id: uuid.UUID) -> WorkView:
    if job is None:
        return WorkView(Projection("socket", "available", reason="in-process turn (local fallback)"),
                        work_id=str(turn_id), status="running", cancellation="socket stop event")
    return WorkView(Projection("chat_worker", "available", version=str(job.attempts)),
                    work_id=str(job.id), status=job.status, attempt=job.attempts,
                    lease_expires_at=job.lease_expires_at, cancellation=f"turn cancel key ({turn_id})")


def _assess(trigger: str, message: str, history: list[dict[str, Any]], cognition: CognitionView,
            memory: MemoryContextView,
            conversation: ConversationView) -> tuple[assessment_contract.Assessment, RecallDecision, ThinkingDecision]:
    """One Jev call for both needs; its answers as the recall and thinking sections."""
    versions = {"conversation_revision": str(conversation.revision), "memory_cache": memory.meta.version or
                memory.meta.status, **({"student_1": cognition.input_versions["student_1"]}
                                       if "student_1" in cognition.input_versions else {})}
    reading = None
    if cognition.interpretation is not None:
        i = cognition.interpretation
        reading = {"doing": list(i.acts), "tone": i.tone, "wants": i.request}
    earlier = [h for h in history if h.get("content")]
    if earlier and earlier[-1].get("role") == "user" and earlier[-1]["content"].strip() == message.strip():
        earlier = earlier[:-1]
    # The mouth's renderer puts content on an indented continuation line. Selecting its bullets
    # loses the facts, and clipping those headers can consume the budget with metadata alone.
    lines = [f"[{e.ref}] ({e.applicability}{'; corrected by the user' if e.corrected else ''}) {e.content}"
             for e in memory.entries]
    capabilities = ["conversation"] + (["memory_search"] if recall_references.enabled() else [])
    result = jev_assessment.assess(trigger_ref=trigger, input_versions=versions, state=assessment_contract.state(
        message=message, recent=earlier, interpretation=reading, evidence_lines=lines, capabilities=capabilities))
    status = {"ok": "available", "invalid": "unavailable", "error": "unavailable", "disabled": "disabled"}[result.status]
    meta = Projection("jev", status, version=f"{result.policy_version}:{result.model}" if result.model else None,
                      updated_at=datetime.now(timezone.utc), reason=result.error)
    recall = RecallDecision(meta, result.recall, "jev" if result.recall else "not_assessed",
                            result.probabilities.get("recall", {}), result.assessment_id)
    thinking = ThinkingDecision(
        meta, trigger_ref=trigger, required={"required": True, "not_required": False}.get(result.thinking),
        decider="jev" if result.thinking else "none", reason=result.error or (None if result.thinking else
                                                                               "not assessed"),
        input_versions=versions, choice=result.thinking, probabilities=result.probabilities.get("thinking", {}))
    return result, recall, thinking


def _search(user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
            message: str, zone: str | None, memory: MemoryContextView
            ) -> tuple[MemoryContextView, recall_references.TurnRecall | None]:
    """The recall route: memory's own reply search (the previous answer's references plus a search for this
    message). Its status stays as memory says it: matched, no_match, partial or failed."""
    if not recall_references.enabled():
        return replace(memory, recall_status="unavailable"), None
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
    memory, recall_turn = _memory(user_id, conversation_id, user_message_id, zone)
    at = lap("memory_cache_ms", at)
    live = None
    if cognitive_live.enabled() and conversation_id is not None:
        live = cognitive_live.prepare(user_id=user_id, conversation_id=conversation_id, history=history,
                                      message=message, long_term_memory=memory.legacy, memory_searched=False)
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
    result, recall_decision, thinking = _assess(trigger, message, history, cognition, memory, conversation)
    at = lap("assessment_ms", at)
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
        inner_state=(Projection.off("inner_state", "observational only: never steers a reply")
                     if get_settings().mind_inner_state_enabled else Projection.off("inner_state", "not enabled")),
        activities=Projection.missing("activities", "not projected yet; the game summary still reaches her "
                                                    "through its own section"),
        situation=Projection.missing("observation", "not projected yet"),
        mind=Projection.off("mind", "not routed to yet (asynchronous thinking: later in step 4)"),
        health=_health(cognition, memory),
    )
    from app.services import background_components
    view = replace(view, components=background_components.projections(user_id, conversation_id))
    decision = route(view)
    searched = False
    if decision.runs_recall:
        memory, searched_turn = _search(user_id, conversation_id, user_message_id, message, zone, view.memory)
        lap("recall_ms", at)
        if searched_turn is not None:
            recall_turn, searched = searched_turn, True
        view = replace(view, memory=memory)
        if not searched:
            decision = replace(decision, degraded=decision.degraded + ("recall_unavailable",))
    pending: tuple[PendingWork, ...] = ()
    action, origin = "respond", None
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
                        "evidence_versions": {e.ref: f"{e.fingerprint}:{e.applicability}:{int(e.corrected)}" for e in view.memory.entries}},
            input_versions={k: v for k, v in view.dependencies().items() if v is not None})
        if committed is None:
            # No work exists, so nobody may say thinking started.
            decision = replace(decision, degraded=decision.degraded + ("mind_unavailable",))
        else:
            pending = committed
            action, made = coordinator.decide_immediate(
                user_id=user_id, conversation_id=conversation_id, request_id=turn_id, trigger_ref=trigger,
                interpretation=reading, pending=pending, channel=channel, voice=voice)
            origin = made.origin if made.supported else "policy_fallback"
            view = replace(view, mind=Projection("coordinator", "pending", reason=f"{len(pending)} work item(s) "
                                                 "committed for this request"))
        lap("schedule_ms", at)
    timings["prepare_ms"] = round((time.monotonic() - started) * 1000)
    if superseded:
        timings["superseded_work"] = superseded
    return FastPathTurn(view, decision, live, recall_turn,
                        boundary_text(view, decision, searched=searched, pending=pending), started, timings, result,
                        searched, pending, action, origin)


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
    "thinking_need_uncertain": "It's unclear whether this message needs careful thinking.",
}
_DEGRADED_WORDS = {
    "cognition_unavailable": ("Your quick reading of their message didn't work this turn: take the message "
                              "only as written, and if what they want isn't clear, ask."),
    "assessment_unavailable": "The check of what this message needs didn't work this turn.",
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
            "assessment": _assessment_trace(turn.assessment),
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
            "immediate": {"action": turn.immediate_action, "origin": turn.immediate_origin} if turn.pending else None,
            "calls": {"fresh_recall_before_reply": int(turn.searched), "mind_scheduled": int(bool(turn.pending)),
                      "jev": int(turn.assessment is not None and turn.assessment.status != "disabled"),
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


def _assessment_trace(result: assessment_contract.Assessment | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {"id": result.assessment_id, "status": result.status, "recall": result.recall,
            "thinking": result.thinking, "probabilities": result.probabilities, "model": result.model,
            "policy": result.policy_version, "latency_ms": result.latency_ms, "error": result.error}


def _write(record: dict[str, Any]) -> None:
    path = get_settings().shared_state_log_path
    if not path:
        return
    if not os.path.isabs(path):
        path = os.path.join(str(cognitive_live.BACKEND), path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
