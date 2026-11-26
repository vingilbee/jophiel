"""Chat persistence: conversations and their messages.

All operations are scoped to a single user so one user can never read or mutate
another's conversations. Route handlers stay thin and call into here (per
CLAUDE.md conventions). The AI layer stays stateless — the route uses these
helpers to load history before streaming and to persist each turn after.
"""
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.db.models import ChatJob, ChatMessage, Conversation
from app.services.heard import heard_text
from datetime import datetime, timedelta, timezone
import secrets
import redis
from app.core.config import get_settings

# A history turn the AI layer understands: {"role": ..., "content": ...}, and with ``records``: "marker" (the app's
# own line about when it was said, shown to the model as an app record) or role "record" (an evaluation of hers
# that produced no message). Metadata never goes inside the user's or her text.
ChatTurn = dict[str, Any]

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
    written_at: datetime | None = None,
    commit: bool = True,
) -> ChatMessage:
    """Append a message to a conversation and bump its ``updated_at``."""
    message = ChatMessage(
        written_at=written_at,
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
    from app.services import background_wake
    background_wake.mark(db)
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


def clamp_written_at(value: datetime | None, now: datetime | None = None) -> datetime | None:
    """A client-reported written-at time, never later than now (a phone's clock can be ahead)."""
    if value is None:
        return None
    now = now or datetime.now(timezone.utc)
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return min(value, now)


def user_turn(db: Session, *, user_id: uuid.UUID, turn_id: uuid.UUID) -> ChatMessage | None:
    """This user's message already saved for ``turn_id``, if any: a resend of it is the same message."""
    return db.scalar(
        select(ChatMessage)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(ChatMessage.turn_id == turn_id, ChatMessage.role == "user", Conversation.user_id == user_id)
    )


def record_heard(
    db: Session, *, user_id: uuid.UUID, turn_id: uuid.UUID, heard_chars: int,
) -> bool:
    """Record how much of an interrupted spoken reply the user heard.

    Returns False when the reply isn't persisted yet (the worker may still be
    finishing it), so the caller can retry. Only ever moves the cut earlier:
    two reports for one reply keep the more conservative one.
    """
    message = db.scalar(
        select(ChatMessage)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(
            ChatMessage.turn_id == turn_id,
            ChatMessage.role == "assistant",
            Conversation.user_id == user_id,
        )
    )
    if message is None:
        return False
    if heard_chars < len(message.content) and (
        message.heard_chars is None or heard_chars < message.heard_chars
    ):
        message.heard_chars = heard_chars
        from app.services import background_wake
        background_wake.mark(db)
        db.commit()
        from app.services import learning_capture
        learning_capture.heard(user_id=user_id, message=message, conversation_id=message.conversation_id)
    return True


def _file_labels(user_id: uuid.UUID, asset_ids: Sequence[str]) -> str:
    """ "report.pdf (id 1a2b…)" per attached file, so a later turn knows what was sent and how to read it."""
    from app.services import asset_store

    labels = []
    for asset_id in asset_ids:
        try:
            labels.append(f'"{asset_store.get_record(user_id, asset_id).filename}" (id {asset_id})')
        except asset_store.AssetNotFoundError:
            labels.append(f"{asset_id} (deleted)")
    return ", ".join(labels)


def get_history(
    db: Session, *, conversation: Conversation,
    up_to_message_id: uuid.UUID | None = None,
    up_to_job_id: uuid.UUID | None = None,
    records: bool = False,
    zone: str | None = None,
    current_message_id: uuid.UUID | None = None,
) -> list[ChatTurn]:
    """Return the conversation's messages, oldest first, one turn per stored message.

    Attachments are folded into the text as a trailing marker. The AI layer
    takes plain role/content turns and we want to keep it that way, but an
    image id has to survive into later turns — otherwise "what about the photo
    I sent earlier?" has nothing to point at.

    Messages are never merged: two messages in a row from the user stay two
    turns, so an instruction scoped to one message can't bleed into the next.

    ``records`` (an evaluation of her mind): each message carries a ``marker``
    (when it was said, in ``zone``; the one ``current_message_id`` names is
    marked CURRENT), and evaluations of hers that produced no message (silence,
    nothing written, failed or cancelled before any words) appear in place as
    ``record`` turns. Both are the app's own lines, never the user's text and
    never words she said.
    """
    keep = max(2, get_settings().short_term_memory_messages)
    # At least ``keep`` messages, with the window's start held in place for HISTORY_STEP messages (see
    # _held_window), so OpenAI's prompt cache can serve the history from one turn to the next.
    limit = keep + HISTORY_STEP
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
    rows = _held_window(list(reversed(list(db.scalars(stmt)))), keep)
    history: list[ChatTurn] = []
    kept: list[tuple[datetime, ChatTurn]] = []
    now = datetime.now(timezone.utc)
    for message in rows:
        # Durable cancellation metadata is not useful model context.
        if message.role == "assistant" and message.status == "cancelled" and not message.content:
            if records:  # her mind still knows she was stopped
                turn = {"role": "record", "content": _stopped_record(message, zone, now)}
                history.append(turn)
                kept.append((_aware(message.created_at), turn, message.id))
            continue
        content = message.content
        if message.role == "assistant" and message.heard_chars is not None:
            # Interrupted voice reply: the model sees what the user heard.
            content = heard_text(content, message.heard_chars)
        if message.image_ids:
            attached = ", ".join(message.image_ids)
            content = f"{content}\n\n[Attached image ids: {attached}]"
        if message.asset_ids:
            content = f"{content}\n\n[Attached files: {_file_labels(conversation.user_id, message.asset_ids)}]"
        turn: ChatTurn = {"role": message.role, "content": content}
        if records:
            turn["marker"] = _marker(message, zone, now, current=message.id == current_message_id)
        history.append(turn)
        kept.append((_aware(message.created_at), turn, message.id))

    if records and kept:
        history = _with_outcomes(db, conversation.id, kept, zone, now)
    # A history that opens with anything but a user turn is also rejected. Only
    # possible if the very first thing in a conversation was a nudge.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


# The history window's start moves in steps of this many messages, not one exchange at a time.
HISTORY_STEP = 12


def _held_window(rows: list[ChatMessage], keep: int) -> list[ChatMessage]:
    """The newest ``keep`` to ``keep + HISTORY_STEP - 1`` messages, starting at a sequence boundary that only moves
    every HISTORY_STEP messages. A window that dropped its oldest message every turn would change the request's
    opening on every turn, and the prompt cache would never reach past the standing prompt. Falls back to the
    newest ``keep`` when sequences are missing or have gaps that would leave fewer."""
    sequences = [m.conversation_sequence for m in rows]
    if not rows or any(seq is None for seq in sequences):
        return rows[-keep:]
    base = ((max(sequences) - keep) // HISTORY_STEP) * HISTORY_STEP
    held = [m for m in rows if m.conversation_sequence > base]
    return held if len(held) >= min(keep, len(rows)) else rows[-keep:]


_RECORD = "[App record, not part of any message]"
# What an evaluation of hers that left no message did, as the app recorded it.
_OUTCOME_WORDS = {"silent": "you chose silence; no words were delivered",
                  "no_words": "you wrote nothing; no words were delivered",
                  "cancelled": "it was cancelled before any words were delivered",
                  "failed": "it failed before any words were delivered"}
_WOKEN_BY = {"user_message": "their message above", "due": "something you had scheduled coming due",
             "work_result": "thinking you started ending", "thought": "a thought of your own",
             "proposal": "a proposal from another part of you", "conflict": "memory noticing a disagreement",
             "finding": "a security finding"}
MAX_OUTCOME_RECORDS = 12


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clock(value: datetime, zone: str | None, now: datetime) -> str:
    from app.ai.mind import when as W

    same_day = W.describe(value, zone)[:10] == W.describe(now, zone)[:10]
    return W.describe(value, zone, date=not same_day)


def _stopped_record(message: ChatMessage, zone: str | None, now: datetime) -> str:
    turn = f" (turn {str(message.turn_id)[:8]})" if message.turn_id else ""
    return (f"{_RECORD} {_clock(_aware(message.created_at), zone, now)}: your reply was stopped before any words "
            f"were delivered{turn}.")


def _marker(message: ChatMessage, zone: str | None, now: datetime, *, current: bool) -> str:
    at = _clock(_aware(message.created_at), zone, now)
    turn = f" (turn {str(message.turn_id)[:8]})" if message.turn_id else ""
    if message.role == "user":
        what = ("CURRENT user message: the one this evaluation answers" if current else "user message")
        what += _written_note(message)
    else:
        what = {"partial": "your reply, stopped part way", "failed": "your reply, which failed",
                "cancelled": "your reply, stopped"}.get(message.status, "your reply")
        if message.heard_chars is not None:
            what += "; they heard only up to where it was cut off"
    return f"{_RECORD} {at}: {what}{turn}."


# A message written this long before it reached the backend (it waited on the phone) says so in her history.
WRITTEN_DELAY_NOTE_SECONDS = 120


def _written_note(message: ChatMessage) -> str:
    """"; written 9 hours before it arrived" when the phone held the message a while; otherwise nothing. A fact
    for her to read, not a rule: what the delay means is hers to judge."""
    written = getattr(message, "written_at", None)
    if written is None or message.created_at is None:
        return ""
    delay = (_aware(message.created_at) - _aware(written)).total_seconds()
    if delay < WRITTEN_DELAY_NOTE_SECONDS:
        return ""
    from app.services.awareness import _humanize
    return f"; written {_humanize(delay)} before it arrived (it waited on their phone)"


def _with_outcomes(db: Session, conversation_id: uuid.UUID, kept: list[tuple[datetime, ChatTurn, uuid.UUID]],
                   zone: str | None, now: datetime) -> list[ChatTurn]:
    """Evaluations of hers in this stretch that left no message, each in place after what preceded it.

    An evaluation answering a message is placed after that message by its id, not by comparing times: a
    message's time comes from the database's clock and an evaluation's from the worker's, which can disagree
    by more than the gap between them. Only evaluations no message here triggered (a wake) are placed by time."""
    from app.db.models import MindEvaluation

    since = kept[0][0]
    index = {message_id: i for i, (_, _, message_id) in enumerate(kept)}
    rows = list(db.scalars(select(MindEvaluation).where(
        MindEvaluation.conversation_id == conversation_id, MindEvaluation.assistant_message_id.is_(None),
        or_(MindEvaluation.finished_at >= since, MindEvaluation.trigger_message_id.in_(list(index))),
        MindEvaluation.outcome.in_(tuple(_OUTCOME_WORDS)))
        .order_by(MindEvaluation.finished_at.desc()).limit(MAX_OUTCOME_RECORDS)))
    after: dict[int, list] = {}
    for row in sorted(rows, key=lambda r: r.finished_at):
        finished = _aware(row.finished_at)
        at = index.get(row.trigger_message_id)
        if at is None:
            at = -1
            while at + 1 < len(kept) and kept[at + 1][0] <= finished:
                at += 1
        after.setdefault(at, []).append(row)
    out: list[ChatTurn] = [_record(r, zone, now) for r in after.get(-1, [])]
    for i, (_, turn, _) in enumerate(kept):
        out.append(turn)
        out.extend(_record(r, zone, now) for r in after.get(i, []))
    return out


def _record(row, zone: str | None, now: datetime) -> ChatTurn:
    woke = _WOKEN_BY.get(row.reason, row.reason)
    note = f' ("{row.silence_reason}")' if row.outcome == "silent" and row.silence_reason else ""
    return {"role": "record", "content": f"{_RECORD} {_clock(_aware(row.finished_at), zone, now)}: "
                                         f"your evaluation of {woke}: {_OUTCOME_WORDS[row.outcome]}{note}."}


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
    if user_message_id is not None:
        stop_own_wakes(db, conversation.id)
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
    newly_seen = mark_seen(db, job.user_message_id, now)
    db.commit()
    db.refresh(job)
    if newly_seen:
        announce_seen(job.user_id, job.user_message_id, job.conversation_id)
    return job


def stop_own_wakes(db: Session, conversation_id: uuid.UUID) -> int:
    """A user message arrived: a wake her background mind raised on its own (a thought, a proposal) that is being
    evaluated in this conversation stops, so the message is answered next instead of after it. Seen live
    2026-11-24 in a game: "follow me" waited 11 s and another message 14 s behind thought wakes. A wake she
    scheduled, a result she waits for, or a promise's follow-through runs on. Returns how many were stopped."""
    from app.db.models import CoordinatorWork
    from app.workers import chat as chat_worker

    running = db.scalars(select(ChatJob).where(
        ChatJob.conversation_id == conversation_id, ChatJob.status == "processing",
        ChatJob.user_message_id.is_(None), ChatJob.payload["phase"].astext == "follow_up")).all()
    stopped = 0
    for job in running:
        try:
            work = db.get(CoordinatorWork, uuid.UUID(job.payload.get("work_id", "")))
        except ValueError:
            continue
        if work is None or not (work.input or {}).get("autonomous"):
            continue
        from app.services import coordinator
        if coordinator.awaiting(db, work):
            continue
        chat_worker.request_cancel(job.turn_id)
        stopped += 1
    return stopped


def mark_seen(db: Session, message_id: uuid.UUID | None, at: datetime) -> bool:
    """Stamp a user message as picked up by her (once). True when this call set it."""
    if message_id is None:
        return False
    result = db.execute(
        update(ChatMessage)
        .where(ChatMessage.id == message_id, ChatMessage.role == "user", ChatMessage.seen_at.is_(None))
        .values(seen_at=at)
    )
    return bool(result.rowcount)


def announce_seen(user_id: uuid.UUID, message_id: uuid.UUID | None, conversation_id: uuid.UUID | None) -> None:
    """Tell this user's open sockets (web and phone) that she picked a message up: they show the second tick.
    Best effort: ``seen_at`` in history makes the tick right after a reload either way."""
    settings = get_settings()
    if not settings.redis_url or message_id is None:
        return
    import json
    from app.services import agent_say
    try:
        redis.from_url(settings.redis_url).publish(agent_say.channel(user_id), json.dumps({
            "kind": "seen", "message_id": str(message_id),
            "conversation_id": str(conversation_id) if conversation_id else None}))
    except Exception:
        pass


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
