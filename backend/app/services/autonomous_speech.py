"""Wake events from the other parts of her (a thought of her own, a proposal, something memory noticed), and the
delivery fences for what she says without being asked. The mind decides; this only carries and checks.

``submit`` keeps the event as durable coordinator work in the activity's own conversation (Minecraft events prefer
an active voice conversation; never "the latest conversation"). The coordinator turns it into one wake of the mind
(``coordinator._conclude``): in that evaluation she chooses to say it, ask, note it, start work, or stay silent.

Execution facts checked before the wake and again before anything is stored (``valid``): its deadline; no newer
user message; its premise (the disagreement, item or open question it was about) still open; nothing forgotten
since it was prepared; the game it came from unchanged; and, in a conversation, the same thing not already said in
this quiet stretch (by premise, or in other words: ``AutonomousDelivery``, reserved in the delivery transaction by
``reserve``, so a restart or a second worker can't say it twice). Elapsed time never reopens a stretch: only a new
user message does. How recently she spoke and the user's commentary setting are given to her as facts (``facts``):
whether now is a good moment is hers to judge.

Words she already chose in a background game turn (her ``say`` and ``offer_to_learn`` tools) are not an event: she
already decided. ``deliver_worded`` gives them delivery arbitration: the activity's own conversation, the user's
commentary setting, one chat job per conversation behind any reply, and the follow-up announcement outbox.
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
    MemoryConflict, SecurityInvestigation, WorkingItem,
)

# Sources whose events belong to a conversation rather than a game (premise and repeat fences).
CONVERSATIONAL = ("conversation", "security", "phone", "desktop")
from app.services import agent_say, coordinator, desktop_preview, voice_target


def _target(db, *, user_id, activity_id, source) -> tuple[Conversation | None, DesktopSession | None]:
    """The activity's own conversation, ownership checked; never "the latest conversation". A security
    investigation's own conversation is the one it was opened in (``security_investigations.conversation_id``)."""
    session = db.get(DesktopSession, activity_id) if source == "minecraft" and activity_id else None
    target = session.conversation_id if session and session.owner_user_id == user_id else (
        activity_id if source == "conversation" else None)
    if source == "security" and activity_id:
        investigation = db.get(SecurityInvestigation, activity_id)
        target = investigation.conversation_id if investigation and investigation.user_id == user_id else None
    if source == "phone":
        target = _phone_conversation(db, user_id)
    if source == "desktop":
        from app.services import mind_desktop
        target = mind_desktop.conversation_of(user_id)  # the turn her hands worked in (ownership checked below)
    conversation = db.get(Conversation, target) if target else None
    if conversation is None or conversation.user_id != user_id:
        return None, session
    return conversation, session


def _phone_conversation(db, user_id) -> uuid.UUID | None:
    """Where a thought about their phone goes: the conversation the phone app is in (it shows the latest one;
    services/phone_state.py keeps the one its socket last used), else their latest conversation."""
    from app.services import chat as chat_service, phone_state

    state = phone_state.read(user_id)
    if state is not None and state.conversation_id is not None:
        return state.conversation_id
    conversations = chat_service.list_conversations(db, user_id=user_id)
    return conversations[0].id if conversations else None


PREMISE_KEYS = ("conflict", "item", "revision", "investigation")
SAME_WORDS = 0.6  # shared content words, of the shorter: the same thing in other words


def submit(db, *, user_id, activity_id, source, event_id, text, reasons, commentary_mode,
           question=False, ttl=120, premise=None, wake="thought", extra=None, third_party=False,
           arrived=None, reply_targets=None, admitted=None) -> str:
    """``premise``: what it's about (its problem's anchor: a conflict, an item and its revision); checked again
    before the mind is woken and before anything is stored. ``wake``: why it wakes her (``thought``, ``proposal``,
    ``conflict``, ``finding``). ``extra``: what the producer needs back in that evaluation (a proposal's keys). ``third_party``: it came
    from someone else's words (a phone notification): the evaluation it wakes gets no device action, nor does
    anything it schedules (app/ai/chat.py). ``arrived``: the handles of the notifications that just arrived and
    prompted it (no text): the wake names them, so she reads it as news, not her own guess. ``reply_targets``
    (affinity B3): those of them she may answer on that wake, each bound to its own triggering message (ids only,
    services/phone_replies.py); never inherited by anything the wake starts or schedules. ``admitted(db)``: the
    producer's own bookkeeping, run in the transaction that admits the wake (never on a refusal), so the two commit
    together."""
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
    if wake != "follow_through":  # a follow-through is her acting, not a line for her mouth
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
        input={"schema_version": 1, "source_refs": [f"event:{event}"], "autonomous": True, "wake": wake,
               "extra": extra or {}, "source": source, "event_id": event, "reasons": reasons,
               "commentary_mode": commentary_mode, "channel": "web", "message": "Autonomous proposal",
               "voice_socket_id": voice_socket, **({"third_party": True} if third_party else {}),
               **({"arrived": list(arrived)[:8]} if arrived else {}),
               **({"reply_targets": list(reply_targets)[:8]} if reply_targets and third_party else {}),
               "premise": {k: str(v) for k, v in (premise or {}).items() if k in PREMISE_KEYS and v is not None}},
    ).on_conflict_do_nothing(constraint="uq_coordinator_work_idempotency"))
    if admitted is not None:
        admitted(db)
    db.commit()
    coordinator._log({"op": "autonomous_proposed", "request": str(request), "event": event,
                      "activity": str(activity_id), "conversation": str(target), "versions": versions})
    coordinator._wake()
    return "Proposed: queued to wake her mind."


def _newer_user_message(db, conversation_id, sequence) -> bool:
    return db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user",
        ChatMessage.conversation_sequence > sequence).limit(1)) is not None


def _setting(db, user_id, reasons, mode) -> tuple[bool, str]:
    """The user's commentary setting for this game (their rule, not hers): which events may be said in which
    mode. Pace is not checked here."""
    return agent_say.allowed(reasons, mode)


def facts(db, work) -> list[str]:
    """What she should know before speaking unasked: the user's commentary setting, how long since she last spoke
    on her own and since the user last wrote. Facts for her judgment, never a rule."""
    out = []
    mode = (work.input or {}).get("commentary_mode")
    if (work.input or {}).get("source") not in CONVERSATIONAL and mode:
        out.append(f"The user's commentary setting for this game: {mode}"
                   + (" (they asked for quiet: speak only when you need them)" if mode == agent_say.QUIET else ""))
    client = desktop_preview.redis_client()
    if client is not None:
        try:
            raw = client.get(agent_say._last_key(work.user_id))
            if raw:
                out.append(f"You last spoke without being asked {int(time.time() - float(raw))} s ago.")
        except Exception:  # noqa: BLE001 - unknown is simply not said
            pass
    last_user = db.scalar(select(func.max(ChatMessage.created_at)).where(
        ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user"))
    if last_user is not None:
        now = db.scalar(select(func.localtimestamp())) if last_user.tzinfo is None else coordinator._now()
        out.append(f"Their last message here was {max(0, int((now - last_user).total_seconds()))} s ago.")
    return out


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
    if work.input["source"] in CONVERSATIONAL:
        # Everyday chat (and what her mind found in her desktop's security records, in its own conversation).
        return why_not(db, work) is None
    return True


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
    if premise.get("investigation"):
        # What she found must still be the current view: new evidence or a gap since makes it an older one.
        investigation = db.get(SecurityInvestigation, uuid.UUID(premise["investigation"]))
        if investigation is None or investigation.user_id != work.user_id or investigation.status == "closed":
            return "the investigation it was about is closed"
        if premise.get("revision") and str(investigation.revision) != premise["revision"]:
            return "the security records it was about changed since (new evidence or a gap)"
    return None


def _words(work) -> list[str]:
    from app.services import memory_forget

    r = work.result or {}
    return sorted(memory_forget._bases(r.get("question") or r.get("conclusion") or ""))


def _key(work) -> str | None:
    if work.input.get("wake") == "follow_through":
        # Acting on a change, not a thing to say: each change is its own (services/follow_through.py dedupes them).
        return f"follow:{work.id}"
    premise = work.input.get("premise") or {}
    if premise.get("investigation"):
        return f"security:{premise['investigation']}:{premise.get('revision')}"
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
    if work.input.get("wake") != "follow_through" and _said_already(db, work):
        return "already said in this quiet stretch"
    return None


def reserve(db, work) -> bool:
    """In the delivery transaction, under the conversation's lock: claim this quiet stretch's slot for what it's
    about. False when it was taken meanwhile (another worker, a retry): then it isn't said."""
    if work.input.get("source") not in CONVERSATIONAL:
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
    """After actual delivery: when she last spoke on her own, and a serious moment said (the receipts ``facts``
    and the user's commentary setting read)."""
    agent_say.mark_said(work.user_id, work.input.get("reasons") or [])


def deliver_worded(db, *, user_id, activity_id, source, text, reasons, commentary_mode) -> str:
    """Queue words her mouth already chose for delivery. Returns what happened, for the model."""
    text = " ".join(text.split())[:400]
    if not text:
        return "Nothing to say."
    conversation, _ = _target(db, user_id=user_id, activity_id=activity_id, source=source)
    if conversation is None:
        coordinator._log({"op": "worded_rejected", "activity": str(activity_id), "reason": "no_scoped_target"})
        return "Not said: this activity has no conversation of its own to say it in."
    ok, why = _setting(db, user_id, reasons, commentary_mode)
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
    """Checked by the chat worker at delivery, under the conversation's sequencing lock: no newer user message, the
    same conversation, and the user's commentary setting as it is now."""
    payload = job.payload
    if _newer_user_message(db, job.conversation_id, payload["conversation_sequence"]):
        return False
    conversation, session = _target(db, user_id=job.user_id, activity_id=uuid.UUID(payload["activity_id"]),
                                    source=payload["source"])
    if conversation is None or conversation.id != job.conversation_id:
        return False
    mode = session.commentary_mode if session is not None else payload["commentary_mode"]
    return _setting(db, job.user_id, payload["reasons"], mode)[0]
