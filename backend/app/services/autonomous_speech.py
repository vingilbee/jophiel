"""Durable autonomous proposals; cognition authorizes, the existing chat mouth delivers.

No latest-conversation fallback. Completed producer work is queued as a mind result,
not sent back through the thinker. Unsupported result cognition remains blocked.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from app.db.models import ChatMessage, Conversation, CoordinatorWork, DesktopSession
from app.services import agent_say, coordinator, desktop_preview


def submit(db, *, user_id, activity_id, source, event_id, text, reasons, commentary_mode,
           question=False, ttl=120) -> str:
    event = str(event_id)
    if not coordinator.enabled():
        coordinator._log({"op": "autonomous_rejected", "event": event, "reason": "pipeline_disabled"})
        return "Rejected: shared-state pipeline disabled."
    session = db.get(DesktopSession, activity_id) if source == "minecraft" else None
    target = session.conversation_id if session and session.owner_user_id == user_id else (
        activity_id if source == "conversation" else None)
    conversation = db.get(Conversation, target) if target else None
    if conversation is None or conversation.user_id != user_id:
        coordinator._log({"op": "autonomous_rejected", "event": event, "reason": "no_scoped_target"})
        return "Rejected: activity has no owned conversation target."
    trigger = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == target,
                        ChatMessage.role == "user").order_by(ChatMessage.conversation_sequence.desc()).limit(1))
    request = uuid.uuid5(user_id, f"autonomous:{source}:{activity_id}:{event}")
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
               "commentary_mode": commentary_mode, "channel": "web", "message": "Autonomous proposal"},
    ).on_conflict_do_nothing(constraint="uq_coordinator_work_idempotency"))
    db.commit()
    coordinator._log({"op": "autonomous_proposed", "request": str(request), "event": event,
                      "activity": str(activity_id), "conversation": str(target), "versions": versions})
    coordinator._wake()
    return "Proposed: queued for cognition and mouth."


def valid(db, work) -> bool:
    if work.status != "completed" or work.deadline_at <= coordinator._now():
        return False
    conversation = db.get(Conversation, work.conversation_id)
    if conversation is None or conversation.user_id != work.user_id:
        return False
    # Assistant replies may finish while this proposal waits; only new user input invalidates it.
    newer = db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
        ChatMessage.conversation_sequence > work.input_versions["conversation"]).limit(1))
    if newer:
        return False
    if work.input["source"] == "minecraft":
        session = db.get(DesktopSession, work.activity_id)
        if (session is None or session.owner_user_id != work.user_id
                or session.conversation_id != work.conversation_id
                or any(getattr(session, attr) != work.input_versions[key] for key, attr in
                       (("plan", "plan_revision"), ("execution", "execution_generation"),
                        ("state", "state_revision")))):
            return False
        mode = session.commentary_mode
    else:
        mode = work.input["commentary_mode"]
    reasons = work.input["reasons"]
    pause = 8 if any(r.startswith("question:") for r in reasons) else agent_say.MIND_HUSH_SECONDS
    if (agent_say.from_mind(reasons) or any(r.startswith("question:") for r in reasons)) and agent_say.user_spoke_since(
            db, work.user_id, time.time() - pause):
        return False
    client = desktop_preview.redis_client()
    last, fresh = None, False
    if client is not None:
        try:
            raw = client.get(agent_say._last_key(work.user_id))
            last = time.time() - float(raw) if raw else None
            which = agent_say.serious(reasons)
            fresh = bool(which and not client.exists(agent_say._serious_key(work.user_id, which)))
        except Exception:
            return False  # Cannot establish freshness; retain privately.
    return agent_say.allowed(reasons, mode, last, serious_fresh=fresh)[0]


def delivered(work):
    """Maintain the existing chatter/urgent receipt guards after actual mouth delivery."""
    client = desktop_preview.redis_client()
    if client is not None:
        try:
            client.set(agent_say._last_key(work.user_id), str(time.time()), ex=86400)
            which = agent_say.serious(work.input["reasons"])
            if which:
                client.set(agent_say._serious_key(work.user_id, which), "1", ex=agent_say.SERIOUS_TTL[which])
        except Exception:
            coordinator.logger.warning("autonomous delivery receipt unavailable: %s", work.id)
