"""Durable autonomous proposals; cognition authorizes, the existing chat mouth delivers.

Minecraft proposals prefer an active voice conversation. No latest-conversation fallback. Completed producer work is queued as a mind result,
not sent back through the thinker. Student 2 decides whether it is said; when it can't decide, it stays
private (blocked).

Before anything she meant to say on her own is said, ``valid`` checks again that it still holds: no newer user
message; its premise (the disagreement, item or open question it was about) still open; nothing forgotten since it
was prepared; and, in a conversation, the same thing not already said in this quiet stretch (by premise, or in
other words: ``AutonomousDelivery``, reserved in the delivery transaction by ``reserve``, so a restart or a second
worker can't say it twice). Elapsed time never reopens a stretch: only a new user message does.

Words her mouth already chose in a background turn (her ``say`` and ``offer_to_learn`` tools) are not a
proposal: cognition and the mouth already ran. ``deliver_worded`` gives them the same delivery arbitration
instead: the activity's own conversation, the restraint rules when queued and again at delivery, one chat
job per conversation behind any reply, and the follow-up announcement outbox.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from app.db.models import (
    DESKTOP_TERMINAL_STATES, AutonomousDelivery, ChatJob, ChatMessage, Conversation, CoordinatorOutput, CoordinatorWork,
    DesktopSession,
    MemoryConflict, WorkingItem,
)
from app.services import agent_say, coordinator, desktop_preview, voice_target


def _target(db, *, user_id, activity_id, source) -> tuple[Conversation | None, DesktopSession | None]:
    """The activity's own conversation, ownership checked; never "the latest conversation"."""
    session = db.get(DesktopSession, activity_id) if source == "minecraft" and activity_id else None
    target = session.conversation_id if session and session.owner_user_id == user_id else (
        activity_id if source == "conversation" else None)
    conversation = db.get(Conversation, target) if target else None
    if conversation is None or conversation.user_id != user_id:
        return None, session
    return conversation, session


PREMISE_KEYS = ("conflict", "item", "revision", "note")
SAME_WORDS = 0.6  # shared content words, of the shorter: the same thing in other words


def submit(db, *, user_id, activity_id, source, event_id, text, reasons, commentary_mode,
           question=False, ttl=120, premise=None) -> str:
    """``premise``: what it's about (its problem's anchor: a conflict, an item and its revision, an open
    question's note); checked again before it's said."""
    event = str(event_id)
    conversation, session = _target(db, user_id=user_id, activity_id=activity_id, source=source)
    voice_socket = None
    if session is not None and session.owner_user_id == user_id and session.state not in DESKTOP_TERMINAL_STATES:
        active = voice_target.current(user_id)
        if active is not None:
            socket_id, conversation_id = active
            voice_conversation = db.get(Conversation, conversation_id)
            if voice_conversation is not None and voice_conversation.user_id == user_id:
                conversation, voice_socket = voice_conversation, str(socket_id)
    if conversation is None:
        coordinator._log({"op": "autonomous_rejected", "event": event, "reason": "no_scoped_target"})
        return "Rejected: activity has no owned conversation target."
    target = conversation.id
    trigger = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == target,
                        ChatMessage.role == "user").order_by(ChatMessage.conversation_sequence.desc()).limit(1))
    request = uuid.uuid5(user_id, f"autonomous:{source}:{activity_id}:{event}")
    from app.services import system_signals
    system_signals.emit(user_id, "mind", "mouth", "question" if question else "line", event)
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext("coordinator:request-admission"))))
    if db.get(CoordinatorWork, request) is None:
        pending = db.scalar(select(func.count()).select_from(CoordinatorWork).where(
            CoordinatorWork.kind == "mind", CoordinatorWork.status.in_(coordinator.ACTIVE)))
        if pending >= getattr(coordinator.get_settings(), "coordinator_max_pending", 1000):
            db.commit()
            return "Rejected: mind queue capacity; source thought retained privately."
    versions = {"conversation": conversation.last_message_sequence}
    if session:
        versions.update(plan=session.plan_revision, execution=session.execution_generation,
                        state=session.state_revision)
    result = {"kind": "question" if question else "decision", "conclusion": text,
              "question": text if question else None}
    db.execute(insert(CoordinatorWork).values(
        id=request, request_id=request, user_id=user_id, conversation_id=target, activity_id=activity_id,
        trigger_message_id=trigger.id if trigger else None, kind="mind", status="queued",
        dependency_ids=[], input_versions=versions, deadline_at=coordinator._now() + timedelta(seconds=ttl),
        idempotency_key=f"autonomous:{request}", result=result,
        input={"schema_version": 1, "source_refs": [f"event:{event}"], "autonomous": True, "source": source, "event_id": event, "reasons": reasons,
               "commentary_mode": commentary_mode, "channel": "web", "message": "Autonomous proposal",
               "voice_socket_id": voice_socket,
               "premise": {k: str(v) for k, v in (premise or {}).items() if k in PREMISE_KEYS and v is not None}},
    ).on_conflict_do_nothing(constraint="uq_coordinator_work_idempotency"))
    db.commit()
    coordinator._log({"op": "autonomous_proposed", "request": str(request), "event": event,
                      "activity": str(activity_id), "conversation": str(target), "versions": versions})
    coordinator._wake()
    return "Proposed: queued for cognition and mouth."


def _newer_user_message(db, conversation_id, sequence) -> bool:
    return db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user",
        ChatMessage.conversation_sequence > sequence).limit(1)) is not None


def _restraint(db, user_id, reasons, mode) -> tuple[bool, str]:
    """The delivery restraint rules (agent_say), read against the current receipts. Unknown freshness keeps
    the line private."""
    pause = 8 if any(r.startswith("question:") for r in reasons) else agent_say.MIND_HUSH_SECONDS
    if (agent_say.from_mind(reasons) or any(r.startswith("question:") for r in reasons)) and agent_say.user_spoke_since(
            db, user_id, time.time() - pause):
        return False, "the user just spoke, and your reply to them answers it; keep this to yourself"
    client = desktop_preview.redis_client()
    last, fresh = None, False
    if client is not None:
        try:
            raw = client.get(agent_say._last_key(user_id))
            last = time.time() - float(raw) if raw else None
            which = agent_say.serious(reasons)
            fresh = bool(which and not client.exists(agent_say._serious_key(user_id, which)))
        except Exception:
            return False, "can't tell when you last spoke; keep it for now"
    return agent_say.allowed(reasons, mode, last, serious_fresh=fresh)


def valid(db, work) -> bool:
    if work.status != "completed" or work.deadline_at <= coordinator._now():
        return False
    conversation = db.get(Conversation, work.conversation_id)
    if conversation is None or conversation.user_id != work.user_id:
        return False
    # Assistant replies may finish while this proposal waits; only new user input invalidates it.
    if _newer_user_message(db, work.conversation_id, work.input_versions["conversation"]):
        return False
    if work.input["source"] == "minecraft":
        session = db.get(DesktopSession, work.activity_id)
        if (session is None or session.owner_user_id != work.user_id
                or session.state in DESKTOP_TERMINAL_STATES
                or any(getattr(session, attr) != work.input_versions[key] for key, attr in
                       (("plan", "plan_revision"), ("execution", "execution_generation"),
                        ("state", "state_revision")))):
            return False
        if socket_id := work.input.get("voice_socket_id"):
            return voice_target.current(work.user_id) == (uuid.UUID(socket_id), work.conversation_id)
        return session.conversation_id == work.conversation_id
    elif work.input["source"] == "conversation":
        # Everyday chat: Student 2 decides whether it is said (no game pacing rules); the fences above (deadline,
        # a newer user message) and these still hold.
        return why_not(db, work) is None
    else:
        mode = work.input["commentary_mode"]
    return _restraint(db, work.user_id, work.input["reasons"], mode)[0]


def _premise_holds(db, work) -> str | None:
    premise = work.input.get("premise") or {}
    if premise.get("conflict"):
        row = db.get(MemoryConflict, uuid.UUID(premise["conflict"]))
        if row is None or row.user_id != work.user_id or row.status != "open":
            return "the disagreement it was about is settled"
    if premise.get("item"):
        entry = db.get(WorkingItem, uuid.UUID(premise["item"]))
        if entry is None or entry.user_id != work.user_id or entry.closed_at is not None:
            return "what it was about is no longer open"
        if premise.get("revision") and str(entry.revision) != premise["revision"]:
            return "what it was about changed meanwhile"
    if premise.get("note"):
        from app.services import mind_conversation
        if not mind_conversation._note_open(work.user_id, work.conversation_id, premise["note"]):
            return "the question it was about has been answered"
    return None


def _words(work) -> list[str]:
    from app.services import memory_forget

    r = work.result or {}
    return sorted(memory_forget._bases(r.get("question") or r.get("conclusion") or ""))


def _key(work) -> str | None:
    premise = work.input.get("premise") or {}
    return f"conflict:{premise['conflict']}" if premise.get("conflict") else (
        f"item:{premise['item']}" if premise.get("item") else None)


def _stretch(db, conversation_id) -> int:
    """The quiet stretch: the conversation's latest user message (0 before any)."""
    return db.scalar(select(func.max(ChatMessage.conversation_sequence)).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user")) or 0


def _said_already(db, work) -> bool:
    """The same premise, or the same thing in other words, already said in this quiet stretch."""
    words, key = set(_words(work)), _key(work)
    for row in db.scalars(select(AutonomousDelivery).where(
            AutonomousDelivery.conversation_id == work.conversation_id,
            AutonomousDelivery.after_sequence == _stretch(db, work.conversation_id),
            AutonomousDelivery.work_id != work.id)):
        if key is not None and row.key == key:
            return True
        shared = words & set(row.words)
        if len(shared) >= 2 and len(shared) >= SAME_WORDS * min(len(words), len(row.words)):
            return True
    return False


def why_not(db, work) -> str | None:
    """Why a conversation proposal can't be said now, or None when it can."""
    from app.services import memory_forget

    if (why := _premise_holds(db, work)) is not None:
        return why
    if memory_forget.forgotten_since(db, work.user_id, work.created_at):
        return "something was forgotten since it was thought, and it may rest on it"
    if _said_already(db, work):
        return "already said in this quiet stretch"
    return None


def reserve(db, work) -> bool:
    """In the delivery transaction, under the conversation's lock: claim this quiet stretch's slot for what it's
    about. False when it was taken meanwhile (another worker, a retry): then it isn't said."""
    if work.input.get("source") != "conversation":
        return True
    if why_not(db, work) is not None:
        return False
    with db.begin_nested():
        row = db.execute(insert(AutonomousDelivery).values(
            user_id=work.user_id, conversation_id=work.conversation_id,
            after_sequence=_stretch(db, work.conversation_id), kind=(work.result or {}).get("kind") or "decision",
            key=_key(work), words=_words(work), work_id=work.id,
        ).on_conflict_do_nothing().returning(AutonomousDelivery.id)).scalar()
    return row is not None


def delivered(work):
    """Maintain the existing chatter/urgent receipt guards after actual mouth delivery."""
    agent_say.mark_said(work.user_id, work.input["reasons"])


def deliver_worded(db, *, user_id, activity_id, source, text, reasons, commentary_mode) -> str:
    """Queue words her mouth already chose for delivery. Returns what happened, for the model."""
    text = " ".join(text.split())[:400]
    if not text:
        return "Nothing to say."
    conversation, _ = _target(db, user_id=user_id, activity_id=activity_id, source=source)
    if conversation is None:
        coordinator._log({"op": "worded_rejected", "activity": str(activity_id), "reason": "no_scoped_target"})
        return "Not said: this activity has no conversation of its own to say it in."
    ok, why = _restraint(db, user_id, reasons, commentary_mode)
    if not ok:
        return f"Not said: {why}."
    request = uuid.uuid4()
    db.execute(insert(CoordinatorOutput).values(
        id=uuid.uuid4(), request_id=request, user_id=user_id, conversation_id=conversation.id, work_id=None,
        phase="follow_up", decision="say", decision_origin="mouth", status="dispatched"))
    payload = {"phase": "deliver", "content": "", "text": text, "request_id": str(request),
               "reasons": list(reasons), "commentary_mode": commentary_mode, "source": source,
               "activity_id": str(activity_id), "conversation_sequence": conversation.last_message_sequence,
               "channel": "web"}
    from app.services import chat as chat_service
    job = chat_service.enqueue_job(db, user_id=user_id, conversation=conversation, turn_id=request,
                                   payload=payload, commit=False)
    db.execute(CoordinatorOutput.__table__.update().where(CoordinatorOutput.request_id == request)
               .values(chat_job_id=job.id))
    db.commit()
    chat_service.wake_chat_worker(job.id)
    coordinator._log({"op": "worded_queued", "request": str(request), "activity": str(activity_id),
                      "conversation": str(conversation.id)})
    return ("Queued to say: it goes out as soon as any reply in progress is done, unless the user speaks "
            "first. The user will see it (and hear it if voice is on).")


def worded_still_valid(db, job: ChatJob) -> bool:
    """Checked by the chat worker at delivery, under the conversation's sequencing lock."""
    payload = job.payload
    if _newer_user_message(db, job.conversation_id, payload["conversation_sequence"]):
        return False
    conversation, session = _target(db, user_id=job.user_id, activity_id=uuid.UUID(payload["activity_id"]),
                                    source=payload["source"])
    if conversation is None or conversation.id != job.conversation_id:
        return False
    mode = session.commentary_mode if session is not None else payload["commentary_mode"]
    return _restraint(db, job.user_id, payload["reasons"], mode)[0]
