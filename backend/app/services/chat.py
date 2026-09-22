"""Chat persistence: conversations and their messages.

All operations are scoped to a single user so one user can never read or mutate
another's conversations. Route handlers stay thin and call into here (per
CLAUDE.md conventions). The AI layer stays stateless — the route uses these
helpers to load history before streaming and to persist each turn after.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.db.models import ChatJob, ChatMessage, Conversation
from datetime import datetime, timedelta, timezone
import secrets
import redis
from app.core.config import get_settings

# A plain history turn the AI layer understands: {"role": ..., "content": ...}
ChatTurn = dict[str, str]

# Longest title we derive from a conversation's first user message.
_TITLE_MAX_LEN = 60


def _derive_title(content: str) -> str:
    title = " ".join(content.split())  # collapse whitespace/newlines
    return title[:_TITLE_MAX_LEN] if title else "New conversation"


def list_conversations(
    db: Session, *, user_id: uuid.UUID
) -> list[Conversation]:
    """Return the user's conversations, most recently updated first."""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(db.scalars(stmt))


def get_conversation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    """Return the conversation if it exists and is owned by the user."""
    return db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )


def create_conversation(
    db: Session, *, user_id: uuid.UUID, title: str | None = None,
    origin_channel: str = "web", commit: bool = True,
) -> Conversation:
    conversation = Conversation(
        user_id=user_id, title=title, origin_channel=origin_channel
    )
    db.add(conversation)
    if commit:
        db.commit()
        db.refresh(conversation)
    else:
        db.flush()
    return conversation


def get_or_create_conversation(
    db: Session,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str | None = None,
    origin_channel: str = "web",
) -> Conversation:
    """Resolve an existing owned conversation, or start a fresh one.

    A ``conversation_id`` that doesn't exist or isn't owned by the user is
    treated as "start new" rather than an error, so a stale client id can never
    leak another user's thread.
    """
    if conversation_id is not None:
        existing = get_conversation(
            db, user_id=user_id, conversation_id=conversation_id
        )
        if existing is not None:
            return existing
    return create_conversation(
        db, user_id=user_id, title=title, origin_channel=origin_channel
    )


def add_message(
    db: Session,
    *,
    conversation: Conversation,
    role: str,
    content: str,
    message_id: uuid.UUID | None = None,
    turn_id: uuid.UUID | None = None,
    status: str = "completed",
    image_ids: Sequence[str] | None = None,
    asset_ids: Sequence[str] | None = None,
    source_channel: str = "web",
    external_message_id: str | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Append a message to a conversation and bump its ``updated_at``."""
    message = ChatMessage(
        id=message_id or uuid.uuid4(),
        conversation_id=conversation.id,
        turn_id=turn_id,
        role=role,
        content=content,
        status=status,
        image_ids=list(image_ids) if image_ids else None,
        asset_ids=list(asset_ids) if asset_ids else None,
        source_channel=source_channel,
        external_message_id=external_message_id,
    )
    db.add(message)
    # Backfill a title from the first user message if we don't have one yet.
    if conversation.title is None and role == "user":
        conversation.title = _derive_title(content)
    # Touch the parent so conversation lists sort by latest activity.
    conversation.updated_at = func.now()
    if commit:
        db.commit()
        db.refresh(message)
    else:
        db.flush()
    return message


def get_history(
    db: Session, *, conversation: Conversation,
    up_to_message_id: uuid.UUID | None = None,
    up_to_job_id: uuid.UUID | None = None,
) -> list[ChatTurn]:
    """Return the conversation's messages, oldest first, as role/content dicts.

    Attachments are folded into the text as a trailing marker. The AI layer
    takes plain role/content turns and we want to keep it that way, but an
    image id has to survive into later turns — otherwise "what about the photo
    I sent earlier?" has nothing to point at.
    """
    limit = max(2, get_settings().short_term_memory_messages)
    # Fetch newest-first so Postgres can apply the limit, then restore natural
    # order for the model. Older rows remain available to the UI and worker.
    job_time = None
    if up_to_job_id is not None:
        boundary_job = db.get(ChatJob, up_to_job_id)
        if boundary_job is not None and boundary_job.conversation_id == conversation.id:
            job_time = boundary_job.created_at
    if job_time is not None:
        effective_time = func.coalesce(ChatJob.created_at, ChatMessage.created_at)
        role_order = case((ChatMessage.role == "assistant", 1), else_=0)
        stmt = (
            select(ChatMessage)
            .outerjoin(ChatJob, ChatJob.turn_id == ChatMessage.turn_id)
            .where(
                ChatMessage.conversation_id == conversation.id,
                or_(ChatJob.id.is_(None), ChatJob.created_at <= job_time),
            )
            .order_by(effective_time.desc(), role_order.desc(), ChatMessage.created_at.desc())
            .limit(limit)
        )
    else:
        stmt = select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id
        )
    if job_time is None and up_to_message_id is not None:
        boundary = db.get(ChatMessage, up_to_message_id)
        if boundary is not None and boundary.conversation_id == conversation.id:
            stmt = stmt.where(ChatMessage.created_at <= boundary.created_at)
    if job_time is None:
        stmt = stmt.order_by(ChatMessage.created_at.desc()).limit(limit)
    history: list[ChatTurn] = []
    for message in reversed(list(db.scalars(stmt))):
        # Durable cancellation metadata is not useful model context.
        if message.role == "assistant" and message.status == "cancelled" and not message.content:
            continue
        content = message.content
        if message.image_ids:
            attached = ", ".join(message.image_ids)
            content = f"{content}\n\n[Attached image ids: {attached}]"
        if message.asset_ids:
            attached = ", ".join(message.asset_ids)
            content = f"{content}\n\n[Attached Blender asset ids: {attached}]"

        # The agent can speak unprompted (see services/salience.py), producing
        # adjacent assistant messages. Merge them into one coherent history
        # turn rather than teaching every model caller about that edge case.
        if history and history[-1]["role"] == message.role:
            history[-1]["content"] += f"\n\n{content}"
            continue
        history.append({"role": message.role, "content": content})

    # A history that opens with an assistant turn is also rejected. Only
    # possible if the very first thing in a conversation was a nudge.
    while history and history[0]["role"] == "assistant":
        history.pop(0)
    return history


def delete_conversation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> bool:
    """Delete a conversation (and its messages via cascade). True if removed."""
    conversation = get_conversation(
        db, user_id=user_id, conversation_id=conversation_id
    )
    if conversation is None:
        return False
    db.delete(conversation)
    db.commit()
    return True


def enqueue_job(db: Session, *, user_id: uuid.UUID, conversation: Conversation,
                turn_id: uuid.UUID, payload: dict,
                user_message_id: uuid.UUID | None = None,
                commit: bool = True) -> ChatJob:
    job = ChatJob(turn_id=turn_id, user_id=user_id, conversation_id=conversation.id,
                  user_message_id=user_message_id, payload=payload, status="pending")
    db.add(job)
    if not commit:
        db.flush()
        return job
    db.commit()
    db.refresh(job)
    wake_chat_worker(job.id)
    return job


def wake_chat_worker(job_id: uuid.UUID | str = "wake") -> None:
    settings = get_settings()
    if settings.redis_url:
        try:
            redis.from_url(settings.redis_url).rpush(settings.chat_queue_name, str(job_id))
        except Exception:
            pass  # PostgreSQL polling is the source of truth.


def active_jobs(db: Session, *, user_id: uuid.UUID) -> list[ChatJob]:
    return list(db.scalars(select(ChatJob).where(
        ChatJob.user_id == user_id,
        ChatJob.status.in_(["pending", "processing"]),
    ).order_by(ChatJob.created_at)))


def claim_next_job(db: Session, *, lease_seconds: int) -> ChatJob | None:
    now = datetime.now(timezone.utc)
    other = aliased(ChatJob)
    live_other = exists(select(other.id).where(
        other.conversation_id == ChatJob.conversation_id,
        other.id != ChatJob.id,
        other.status == "processing",
        other.lease_expires_at > now,
    ))
    job = db.scalar(
        select(ChatJob)
        .where(
            or_(
                ChatJob.status == "pending",
                (ChatJob.status == "processing")
                & (ChatJob.lease_expires_at.is_(None) | (ChatJob.lease_expires_at <= now)),
            ),
            ~live_other,
        )
        .order_by(ChatJob.created_at, ChatJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    # The candidate row lock alone is insufficient: two workers can lock two
    # different pending jobs for the same conversation before either commits.
    # Serialize the final claim through the parent conversation, then recheck.
    db.scalar(
        select(Conversation.id)
        .where(Conversation.id == job.conversation_id)
        .with_for_update()
    )
    competing = db.scalar(
        select(ChatJob.id)
        .where(
            ChatJob.conversation_id == job.conversation_id,
            ChatJob.id != job.id,
            ChatJob.status == "processing",
            ChatJob.lease_expires_at > now,
        )
        .limit(1)
    )
    if competing is not None:
        db.rollback()
        return None
    job.status = "processing"
    job.attempts += 1
    job.claim_token = secrets.token_urlsafe(32)
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()
    db.refresh(job)
    return job


def renew_job_lease(
    db: Session, job_id: uuid.UUID, claim_token: str, *, lease_seconds: int
) -> bool:
    result = db.execute(
        update(ChatJob)
        .where(
            ChatJob.id == job_id,
            ChatJob.status == "processing",
            ChatJob.claim_token == claim_token,
        )
        .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
    )
    db.commit()
    return bool(result.rowcount)


def finish_job(
    db: Session, job_id: uuid.UUID, status: str, error: str | None = None,
    *, claim_token: str | None = None, commit: bool = True,
) -> bool:
    conditions = [ChatJob.id == job_id]
    if claim_token is not None:
        conditions.extend((
            ChatJob.status == "processing",
            ChatJob.claim_token == claim_token,
        ))
    result = db.execute(
        update(ChatJob)
        .where(*conditions)
        .values(
            status=status,
            last_error=error,
            processed_at=datetime.now(timezone.utc),
            lease_expires_at=None,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return bool(result.rowcount)
