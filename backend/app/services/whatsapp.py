"""WhatsApp identity linking, durable webhook ingestion, and turn batching."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    ChannelIdentity,
    ChannelInboundMessage,
    ChannelLinkCode,
    ChatJob,
    ChatMessage,
    Conversation,
    ConversationTurnState,
    MessageDelivery,
    UserChatState,
)
from app.services import chat as chat_service

logger = logging.getLogger(__name__)
_CODE_ALPHABET = string.ascii_uppercase + string.digits
_LINK_RE = re.compile(r"^\s*(?:link\s+)?([A-Z0-9]{8})\s*$", re.IGNORECASE)


def split_text(content: str, limit: int = 4000) -> list[str]:
    """Split without exceeding WhatsApp's text body limit."""
    remaining = content.strip()
    if not remaining:
        return [""]
    chunks: list[str] = []
    while len(remaining) > limit:
        cut = max(remaining.rfind("\n", 0, limit + 1), remaining.rfind(" ", 0, limit + 1))
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def schedule_delivery(
    db: Session, *, assistant_message_id: uuid.UUID,
    channel_identity_id: uuid.UUID, content: str,
) -> list[MessageDelivery]:
    deliveries = [
        MessageDelivery(
            assistant_message_id=assistant_message_id,
            channel="whatsapp",
            channel_identity_id=channel_identity_id,
            content=chunk,
            part_index=index,
        )
        for index, chunk in enumerate(split_text(content))
    ]
    db.add_all(deliveries)
    return deliveries


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.upper().encode()).hexdigest()


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = get_settings().whatsapp_app_secret
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def create_link_code(db: Session, *, user_id: uuid.UUID) -> tuple[str, datetime]:
    settings = get_settings()
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    expires_at = _now() + timedelta(seconds=settings.whatsapp_link_code_ttl_seconds)
    db.execute(delete(ChannelLinkCode).where(ChannelLinkCode.user_id == user_id))
    db.add(ChannelLinkCode(
        user_id=user_id, provider="whatsapp", code_hash=_code_hash(code),
        expires_at=expires_at,
    ))
    db.commit()
    return code, expires_at


def linked_identity(db: Session, *, user_id: uuid.UUID) -> ChannelIdentity | None:
    return db.scalar(select(ChannelIdentity).where(
        ChannelIdentity.provider == "whatsapp",
        ChannelIdentity.user_id == user_id,
    ))


def unlink(db: Session, *, user_id: uuid.UUID) -> bool:
    identity = linked_identity(db, user_id=user_id)
    if identity is None:
        return False
    db.delete(identity)
    db.commit()
    return True


def _primary_conversation(db: Session, user_id: uuid.UUID) -> Conversation:
    state = db.get(UserChatState, user_id)
    if state is not None:
        conversation = db.get(Conversation, state.primary_conversation_id)
        if conversation is not None:
            return conversation
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .limit(1)
    )
    if conversation is None:
        conversation = Conversation(
            user_id=user_id, title="WhatsApp conversation", origin_channel="whatsapp"
        )
        db.add(conversation)
        db.flush()
    if state is None:
        db.add(UserChatState(
            user_id=user_id, primary_conversation_id=conversation.id
        ))
    else:
        state.primary_conversation_id = conversation.id
    db.flush()
    return conversation


def _consume_link_code(
    db: Session, *, code: str, provider_account_id: str,
    external_subject: str, display_name: str | None,
) -> ChannelIdentity | None:
    link = db.scalar(
        select(ChannelLinkCode)
        .where(
            ChannelLinkCode.provider == "whatsapp",
            ChannelLinkCode.code_hash == _code_hash(code),
            ChannelLinkCode.expires_at > _now(),
        )
        .with_for_update()
    )
    if link is None:
        return None
    # One WhatsApp identity per account and one account per WhatsApp identity.
    subject_identity = db.scalar(select(ChannelIdentity).where(
        ChannelIdentity.provider == "whatsapp",
        ChannelIdentity.provider_account_id == provider_account_id,
        ChannelIdentity.external_subject == external_subject,
    ).with_for_update())
    if subject_identity is not None and subject_identity.user_id != link.user_id:
        return None
    user_identity = db.scalar(select(ChannelIdentity).where(
        ChannelIdentity.provider == "whatsapp",
        ChannelIdentity.user_id == link.user_id,
    ).with_for_update())
    if user_identity is not None:
        user_identity.provider_account_id = provider_account_id
        user_identity.external_subject = external_subject
        user_identity.display_name = display_name or user_identity.display_name
        identity = user_identity
    elif subject_identity is not None:
        identity = subject_identity
    else:
        identity = ChannelIdentity(
            provider="whatsapp", provider_account_id=provider_account_id,
            external_subject=external_subject, user_id=link.user_id,
            display_name=display_name,
        )
        db.add(identity)
        db.flush()
    conversation = _primary_conversation(db, link.user_id)
    db.delete(link)
    # Confirmation is durable and goes through the same delivery outbox.
    assistant = ChatMessage(
        conversation_id=conversation.id, role="assistant",
        content="WhatsApp is linked. You can continue our conversation here.",
        status="completed", source_channel="system",
    )
    db.add(assistant)
    db.flush()
    schedule_delivery(
        db, assistant_message_id=assistant.id,
        channel_identity_id=identity.id, content=assistant.content,
    )
    from app.services import dialogue_memory

    # Delivered only through the outbox: dialogue memory records it as pending
    # until a settled delivery status arrives.
    dialogue_memory.schedule_update(
        db, user_id=link.user_id, conversation_id=conversation.id, trigger_message_id=assistant.id,
    )
    return identity


def wake_worker() -> None:
    settings = get_settings()
    if not settings.redis_url:
        return
    try:
        redis.from_url(
            settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
        ).rpush(settings.whatsapp_queue_name, "wake")
    except Exception:
        logger.warning("WhatsApp wake-up failed; worker will poll PostgreSQL", exc_info=True)


def ingest_message(
    db: Session, *, provider_account_id: str, provider_message_id: str,
    external_subject: str, content: str, provider_timestamp: datetime | None,
    display_name: str | None = None,
) -> str:
    """Persist one inbound message. Returns accepted, duplicate, or unlinked."""
    identity = db.scalar(select(ChannelIdentity).where(
        ChannelIdentity.provider == "whatsapp",
        ChannelIdentity.provider_account_id == provider_account_id,
        ChannelIdentity.external_subject == external_subject,
    ))
    if identity is None:
        match = _LINK_RE.match(content)
        if match is None:
            return "unlinked"
        identity = _consume_link_code(
            db, code=match.group(1), provider_account_id=provider_account_id,
            external_subject=external_subject, display_name=display_name,
        )
        if identity is None:
            return "unlinked"
        conversation = _primary_conversation(db, identity.user_id)
        db.execute(
            insert(ChannelInboundMessage)
            .values(
                id=uuid.uuid4(),
                channel_identity_id=identity.id,
                conversation_id=conversation.id,
                provider_account_id=provider_account_id,
                provider_message_id=provider_message_id,
                provider_timestamp=provider_timestamp,
                content=content,
                status="linked",
            )
            .on_conflict_do_nothing(
                constraint="uq_channel_inbound_provider_message"
            )
        )
        db.commit()
        wake_worker()
        return "linked"

    conversation = _primary_conversation(db, identity.user_id)
    inbound_id = uuid.uuid4()
    inserted = db.scalar(
        insert(ChannelInboundMessage)
        .values(
            id=inbound_id,
            channel_identity_id=identity.id,
            conversation_id=conversation.id,
            provider_account_id=provider_account_id,
            provider_message_id=provider_message_id,
            provider_timestamp=provider_timestamp,
            content=content,
            status="pending",
        )
        .on_conflict_do_nothing(
            constraint="uq_channel_inbound_provider_message"
        )
        .returning(ChannelInboundMessage.id)
    )
    if inserted is None:
        return "duplicate"

    settings = get_settings()
    now = _now()
    state = db.scalar(
        select(ConversationTurnState)
        .where(ConversationTurnState.conversation_id == conversation.id)
        .with_for_update()
    )
    if state is None:
        first = now
        state = ConversationTurnState(
            conversation_id=conversation.id,
            first_pending_at=first,
            last_pending_at=now,
            dispatch_after=now + timedelta(seconds=settings.whatsapp_debounce_seconds),
        )
        db.add(state)
    else:
        state.last_pending_at = now
        state.dispatch_after = min(
            now + timedelta(seconds=settings.whatsapp_debounce_seconds),
            state.first_pending_at + timedelta(seconds=settings.whatsapp_max_batch_seconds),
        )
    db.commit()
    wake_worker()
    return "accepted"


def apply_delivery_status(
    db: Session, *, provider_message_id: str, status: str, error: str | None = None
) -> bool:
    """Apply one provider status; True when a dialogue memory update was scheduled."""
    delivery = db.scalar(select(MessageDelivery).where(
        MessageDelivery.provider_message_id == provider_message_id
    ))
    if delivery is None:
        return False
    allowed = {"sent", "delivered", "read", "failed"}
    if status not in allowed:
        return False
    rank = {"pending": 0, "retry": 0, "sending": 0, "accepted": 1,
            "sent": 2, "delivered": 3, "read": 4}
    if status != "failed" and rank.get(status, 0) < rank.get(delivery.status, 0):
        return False
    if status == "failed" and delivery.status in {"delivered", "read"}:
        return False
    delivery.status = status
    delivery.last_error = error if status == "failed" else None
    if status in {"delivered", "read", "failed"}:
        from app.services import background_wake
        background_wake.mark(db)
        return schedule_exposure_update(db, assistant_message_id=delivery.assistant_message_id)
    return False


def schedule_exposure_update(db: Session, *, assistant_message_id: uuid.UUID) -> bool:
    """A delivery reached a settled state: let dialogue memory re-check its exposure."""
    from app.services import dialogue_memory

    if not dialogue_memory.enabled():
        return False
    owner = db.execute(
        select(Conversation.user_id, Conversation.id)
        .join(ChatMessage, ChatMessage.conversation_id == Conversation.id)
        .where(ChatMessage.id == assistant_message_id)
    ).one_or_none()
    if owner is None:
        return False
    return dialogue_memory.schedule_update(
        db, user_id=owner.user_id, conversation_id=owner.id, trigger_message_id=assistant_message_id,
        trigger_kind=dialogue_memory.TRIGGER_DELIVERY_SETTLED,
    )


def dispatch_due_batch(db: Session) -> ChatJob | None:
    """Atomically assemble one due WhatsApp batch into one user turn/job."""
    now = _now()
    state = db.scalar(
        select(ConversationTurnState)
        .where(ConversationTurnState.dispatch_after <= now)
        .order_by(ConversationTurnState.dispatch_after)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if state is None:
        db.rollback()
        return None
    active = db.scalar(select(ChatJob.id).where(
        ChatJob.conversation_id == state.conversation_id,
        ChatJob.status.in_(("pending", "processing")),
    ).limit(1))
    if active is not None:
        db.rollback()
        return None
    inbound = list(db.scalars(
        select(ChannelInboundMessage)
        .where(
            ChannelInboundMessage.conversation_id == state.conversation_id,
            ChannelInboundMessage.status == "pending",
        )
        .order_by(
            ChannelInboundMessage.provider_timestamp,
            ChannelInboundMessage.created_at,
            ChannelInboundMessage.id,
        )
        .with_for_update()
    ))
    if not inbound:
        db.delete(state)
        db.commit()
        return None
    identity = db.get(ChannelIdentity, inbound[0].channel_identity_id)
    conversation = db.get(Conversation, state.conversation_id)
    if identity is None or conversation is None:
        db.delete(state)
        db.commit()
        return None
    content = "\n".join(item.content for item in inbound)
    turn_id = uuid.uuid4()
    message = ChatMessage(
        conversation_id=conversation.id, turn_id=turn_id, role="user",
        content=content, status="completed", source_channel="whatsapp",
        external_message_id=(inbound[0].provider_message_id if len(inbound) == 1 else None),
    )
    db.add(message)
    conversation.updated_at = now
    db.flush()
    job = ChatJob(
        turn_id=turn_id, user_id=identity.user_id,
        conversation_id=conversation.id, user_message_id=message.id,
        payload={
            "content": content,
            "channel": "whatsapp",
            "channel_identity_id": str(identity.id),
            "image_ids": [], "asset_ids": [], "voice": False,
            "awareness": "", "vision_enabled": False,
            "screen_image_id": None,
        },
        status="pending",
    )
    db.add(job)
    batch_id = uuid.uuid4()
    for item in inbound:
        item.status = "batched"
        item.batch_id = batch_id
        item.chat_message_id = message.id
    db.delete(state)
    db.commit()
    chat_service.wake_chat_worker(job.id)
    return job
