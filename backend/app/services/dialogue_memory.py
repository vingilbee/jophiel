"""PostgreSQL ownership, locking, persistence, and scheduling for Dialogue Working Memory V0.

Observational (shadow-only) and never read by the live chat path. PostgreSQL
is the source of truth: chat messages and their delivery records are the
authoritative events, snapshots are immutable, and the head pointer moves only
by compare-and-set under a per-conversation transaction lock. Redis only wakes
the worker and (optionally) caches verified snapshot payloads.

Two paths reach the same state:

- eager: the chat paths schedule a durable job in the transaction that commits
  the terminal assistant outcome (or its delivery transition); the worker
  advances the head to exactly that job's target;
- lazy: a live-shadow capture catches the head up to the message immediately
  before its focus, so a crash, lost wake-up, expired lease, or cache
  eviction never loses a turn.

Logs and metrics carry ids, counts, statuses, and stable codes only -- never
message text, snapshot payloads, or quotes.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import redis
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueExposureState,
    DialogueGap,
    DialogueGapKind,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueMemorySnapshot,
    DialogueMemoryVersions,
    DialogueResetReason,
    DialogueTurnContext,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.bootstrap import bootstrap, select_window
from app.ai.Cognitive.system.v0.dialogue_memory_v0.cache_protocol import (
    NullDialogueMemoryCache,
    conversation_key_pattern,
    decode_verified,
    encode,
    head_key,
    snapshot_key,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.digests import prefix_digest_for
from app.ai.Cognitive.system.v0.dialogue_memory_v0.eligibility import source_event
from app.ai.Cognitive.system.v0.dialogue_memory_v0.errors import DialogueMemoryError, fail
from app.ai.Cognitive.system.v0.dialogue_memory_v0.models import (
    DeliveryFact,
    DialogueMemoryLimits,
    DialogueMessageRecord,
    SourceFingerprint,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.ordering import validate_records
from app.ai.Cognitive.system.v0.dialogue_memory_v0.projector import compile_turn_context
from app.ai.Cognitive.system.v0.dialogue_memory_v0.updater import DialogueMemoryUpdaterV0
from app.core.config import get_settings
from app.db.models import (
    ChatMessage,
    CognitiveDialogueMemoryHead,
    CognitiveDialogueMemoryJob,
    CognitiveDialogueMemorySnapshot,
    Conversation,
    MessageDelivery,
)

logger = logging.getLogger(__name__)

TRIGGER_MESSAGE_COMMITTED = "message_committed"
TRIGGER_DELIVERY_SETTLED = "delivery_settled"


# ---------------------------------------------------------------------------
# Content-free, in-process metrics
# ---------------------------------------------------------------------------

class DialogueMemoryMetrics:
    """Counts and statuses only.  No label ever carries content."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        key = name + "".join(f"|{key}={labels[key]}" for key in sorted(labels))
        with self._lock:
            self._counts[key] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


metrics = DialogueMemoryMetrics()


def enabled() -> bool:
    return bool(get_settings().cognitive_dialogue_memory_enabled)


def limits() -> DialogueMemoryLimits:
    settings = get_settings()
    return DialogueMemoryLimits(
        bootstrap_messages=settings.cognitive_dialogue_memory_bootstrap_messages,
        max_items=settings.cognitive_dialogue_memory_max_items,
    )


# ---------------------------------------------------------------------------
# Optional cache (Redis), never authoritative
# ---------------------------------------------------------------------------

class RedisDialogueMemoryCache:
    """``DialogueMemoryCache`` over Redis.  Every failure is a recoverable miss."""

    def __init__(self, url: str) -> None:
        self._client = redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)

    def get(self, conversation_ref: str, revision: int, digest: str) -> bytes | None:
        return self._client.get(snapshot_key(conversation_ref, revision, digest))

    def put(self, snapshot: DialogueMemorySnapshot, ttl_seconds: int) -> None:
        pipe = self._client.pipeline()
        pipe.set(snapshot_key(snapshot.conversation_ref, snapshot.revision, snapshot.semantic_digest),
                 encode(snapshot), ex=ttl_seconds)
        pipe.set(head_key(snapshot.conversation_ref), f"{snapshot.revision}:{snapshot.semantic_digest}", ex=ttl_seconds)
        pipe.execute()

    def delete_conversation(self, conversation_ref: str) -> None:
        keys = list(self._client.scan_iter(match=conversation_key_pattern(conversation_ref), count=200))
        keys.append(head_key(conversation_ref))
        self._client.delete(*keys)


_cache_override = None


def cache():
    """The configured cache, or the null cache when disabled or unconfigured."""

    if _cache_override is not None:
        return _cache_override
    settings = get_settings()
    if not settings.cognitive_dialogue_memory_cache_enabled or not settings.redis_url:
        return NullDialogueMemoryCache()
    return RedisDialogueMemoryCache(settings.redis_url)


def _publish(snapshot: DialogueMemorySnapshot) -> None:
    """After commit only.  A cache failure never undoes a valid database update."""

    try:
        cache().put(snapshot, get_settings().cognitive_dialogue_memory_cache_ttl_seconds)
    except Exception as exc:  # noqa: BLE001 - the cache is disposable
        metrics.increment("cache_failures", operation="put", error=type(exc).__name__)


def delete_cached_conversation(conversation_id: uuid.UUID | str) -> None:
    try:
        cache().delete_conversation(str(conversation_id))
    except Exception as exc:  # noqa: BLE001
        metrics.increment("cache_failures", operation="delete", error=type(exc).__name__)
        logger.warning("Dialogue memory cache purge failed for conversation %s (%s)", conversation_id,
                       type(exc).__name__)


# ---------------------------------------------------------------------------
# Locking and source loading
# ---------------------------------------------------------------------------

def _lock_key(conversation_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"dialogue-memory:{conversation_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_conversation(db: Session, conversation_id: uuid.UUID) -> None:
    """Serialize head transitions of one conversation (never the chat row lock)."""

    db.execute(select(func.pg_advisory_xact_lock(_lock_key(conversation_id))))


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _owner(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    owner = db.scalar(select(Conversation.user_id).where(Conversation.id == conversation_id))
    if owner is None or owner != user_id:
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                   "The conversation does not exist or belongs to a different user.")


def _to_records(db: Session, rows: list[ChatMessage]) -> list[DialogueMessageRecord]:
    deliveries: dict[uuid.UUID, list[DeliveryFact]] = {}
    ids = [row.id for row in rows if row.role == "assistant"]
    if ids:
        for delivery in db.scalars(select(MessageDelivery).where(MessageDelivery.assistant_message_id.in_(ids))):
            deliveries.setdefault(delivery.assistant_message_id, []).append(DeliveryFact(
                delivery_ref=str(delivery.id), channel=delivery.channel, part_index=delivery.part_index,
                status=delivery.status,
            ))
    return [
        DialogueMessageRecord(
            message_ref=str(row.id), conversation_ref=str(row.conversation_id),
            conversation_sequence=row.conversation_sequence, turn_ref=str(row.turn_id) if row.turn_id else None,
            role=row.role, status=row.status, source_channel=row.source_channel, content=row.content,
            created_at=_utc_text(row.created_at),
            attachment_refs=tuple([*(f"image_store:{item}" for item in row.image_ids or ()),
                                   *(f"asset_store:{item}" for item in row.asset_ids or ())]),
            deliveries=tuple(sorted(deliveries.get(row.id, ()), key=lambda item: item.part_index)),
        )
        for row in rows
    ]


def _range(db: Session, conversation_id: uuid.UUID, *, after: int, through: int) -> list[DialogueMessageRecord]:
    rows = list(db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.conversation_sequence > after,
               ChatMessage.conversation_sequence <= through)
        .order_by(ChatMessage.conversation_sequence)
    ))
    return _to_records(db, rows)


def _tail(db: Session, conversation_id: uuid.UUID, *, boundary: int, limit: int) -> list[DialogueMessageRecord]:
    rows = list(db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.conversation_sequence <= boundary)
        .order_by(ChatMessage.conversation_sequence.desc())
        .limit(limit + 1)
    ))
    rows.reverse()
    return _to_records(db, rows)


def _message(db: Session, conversation_id: uuid.UUID, message_ref: str) -> DialogueMessageRecord | None:
    row = db.scalar(select(ChatMessage).where(ChatMessage.id == uuid.UUID(message_ref),
                                              ChatMessage.conversation_id == conversation_id))
    return _to_records(db, [row])[0] if row is not None else None


def _fingerprints(db: Session, conversation_id: uuid.UUID, *, start: int, through: int) -> list[SourceFingerprint]:
    rows = db.execute(text(
        "SELECT conversation_sequence, id, role, status, turn_id, source_channel, "
        "encode(sha256(convert_to(content, 'UTF8')), 'hex') AS content_digest "
        "FROM chat_messages WHERE conversation_id = :conversation AND conversation_sequence BETWEEN :start AND :through "
        "ORDER BY conversation_sequence"
    ), {"conversation": conversation_id, "start": start, "through": through}).all()
    return [
        SourceFingerprint(conversation_sequence=row.conversation_sequence, message_ref=str(row.id), role=row.role,
                          status=row.status, turn_ref=str(row.turn_id) if row.turn_id else None,
                          source_channel=row.source_channel, content_digest=row.content_digest)
        for row in rows
    ]


def prefix_intact(db: Session, snapshot: DialogueMemorySnapshot) -> bool:
    """Whether every consumed source event is still exactly as it was consumed."""

    start, through = snapshot.segment_start_sequence, snapshot.through_conversation_sequence
    fingerprints = _fingerprints(db, uuid.UUID(snapshot.conversation_ref), start=start, through=through)
    if [item.conversation_sequence for item in fingerprints] != list(range(start, through + 1)):
        return False
    if len(fingerprints) != snapshot.source_event_count:
        return False
    return prefix_digest_for(snapshot.conversation_ref, start, fingerprints) == snapshot.source_prefix_digest


# ---------------------------------------------------------------------------
# Snapshot rows
# ---------------------------------------------------------------------------

def _from_row(row: CognitiveDialogueMemorySnapshot, *, user_id: uuid.UUID) -> DialogueMemorySnapshot:
    if row.user_id != user_id:
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                   "A dialogue snapshot belongs to a different user.")
    try:
        snapshot = DialogueMemorySnapshot.model_validate(row.snapshot_payload)
    except (ValidationError, ValueError) as exc:
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.SNAPSHOT_CONTRACT_INVALID,
                   "A stored dialogue snapshot failed contract validation.") from exc
    if (snapshot.snapshot_id, snapshot.conversation_ref, snapshot.user_ref, snapshot.revision,
            snapshot.semantic_digest, snapshot.through_conversation_sequence) != (
        str(row.id), str(row.conversation_id), str(row.user_id), row.revision, row.semantic_digest,
        row.through_conversation_sequence,
    ):
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "A stored dialogue snapshot differs from its indexed columns.")
    return snapshot


def _head(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, use_cache: bool = False,
          ) -> DialogueMemorySnapshot | None:
    row = db.get(CognitiveDialogueMemoryHead, conversation_id)
    if row is None:
        return None
    if row.user_id != user_id:
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                   "The dialogue memory head belongs to a different user.")
    if use_cache:
        cached = _cached(user_id=user_id, conversation_id=conversation_id, revision=row.revision,
                         snapshot_id=row.snapshot_id, digest=row.semantic_digest)
        if cached is not None:
            return cached
    snapshot_row = db.get(CognitiveDialogueMemorySnapshot, row.snapshot_id)
    if snapshot_row is None:
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.SNAPSHOT_NOT_FOUND,
                   "The dialogue memory head names a missing snapshot.")
    snapshot = _from_row(snapshot_row, user_id=user_id)
    if (snapshot.revision, snapshot.semantic_digest) != (row.revision, row.semantic_digest):
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                   "The dialogue memory head differs from its snapshot.")
    return snapshot


def _cached(*, user_id: uuid.UUID, conversation_id: uuid.UUID, revision: int, snapshot_id: uuid.UUID, digest: str,
            ) -> DialogueMemorySnapshot | None:
    backend = cache()
    try:
        raw = backend.get(str(conversation_id), revision, digest)
    except Exception as exc:  # noqa: BLE001 - unavailable cache == miss
        metrics.increment("cache_failures", operation="get", error=type(exc).__name__)
        return None
    if raw is None:
        metrics.increment("cache_misses")
        return None
    try:
        snapshot = decode_verified(raw, user_ref=str(user_id), conversation_ref=str(conversation_id),
                                   revision=revision, snapshot_id=str(snapshot_id), digest=digest)
    except DialogueMemoryError:
        # A corrupt or foreign entry is discarded, never consumed.
        metrics.increment("cache_mismatches")
        delete_cached_conversation(conversation_id)
        return None
    metrics.increment("cache_hits")
    return snapshot


def _persist(db: Session, snapshot: DialogueMemorySnapshot, *, expected_head: str | None) -> None:
    """Insert the immutable snapshot and move the head by compare-and-set (no commit)."""

    conversation_id = uuid.UUID(snapshot.conversation_ref)
    user_id = uuid.UUID(snapshot.user_ref)
    try:
        with db.begin_nested():
            db.execute(insert(CognitiveDialogueMemorySnapshot).values(
                id=uuid.UUID(snapshot.snapshot_id), user_id=user_id, conversation_id=conversation_id,
                revision=snapshot.revision,
                predecessor_snapshot_id=(uuid.UUID(snapshot.predecessor_snapshot_ref)
                                         if snapshot.predecessor_snapshot_ref else None),
                segment_id=uuid.UUID(snapshot.segment_id),
                reset_reason=snapshot.reset_reason.value if snapshot.reset_reason else None,
                through_conversation_sequence=snapshot.through_conversation_sequence,
                through_message_id=uuid.UUID(snapshot.through_message_id) if snapshot.through_message_id else None,
                source_event_count=snapshot.source_event_count, source_prefix_digest=snapshot.source_prefix_digest,
                snapshot_payload=snapshot.model_dump(mode="json"), semantic_digest=snapshot.semantic_digest,
                versions=snapshot.versions.model_dump(mode="json"),
            ).on_conflict_do_nothing(index_elements=["id"]))
            values = dict(snapshot_id=uuid.UUID(snapshot.snapshot_id), revision=snapshot.revision,
                          through_conversation_sequence=snapshot.through_conversation_sequence,
                          semantic_digest=snapshot.semantic_digest, updated_at=datetime.now(timezone.utc))
            if expected_head is None:
                moved = db.execute(insert(CognitiveDialogueMemoryHead).values(
                    conversation_id=conversation_id, user_id=user_id, **values,
                ).on_conflict_do_nothing(index_elements=["conversation_id"]).returning(
                    CognitiveDialogueMemoryHead.conversation_id)).scalar_one_or_none()
            else:
                moved = db.execute(update(CognitiveDialogueMemoryHead).where(
                    CognitiveDialogueMemoryHead.conversation_id == conversation_id,
                    CognitiveDialogueMemoryHead.user_id == user_id,
                    CognitiveDialogueMemoryHead.snapshot_id == uuid.UUID(expected_head),
                ).values(**values).returning(CognitiveDialogueMemoryHead.conversation_id)).scalar_one_or_none()
            if moved is None:
                raise fail(DialogueMemoryFailurePhase.PERSISTENCE, DialogueMemoryFailureCode.PREDECESSOR_CONFLICT,
                           "The dialogue memory head moved; the predecessor is no longer current.", retryable=True)
    except IntegrityError as exc:
        raise fail(DialogueMemoryFailurePhase.PERSISTENCE, DialogueMemoryFailureCode.PERSISTENCE_CONFLICT,
                   "A conflicting dialogue snapshot already holds this revision.", retryable=True) from exc


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def _events(records: list[DialogueMessageRecord], *, user_id: uuid.UUID):
    return [source_event(record, user_ref=str(user_id)) for record in records]


def _bootstrap_at(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, boundary: int, revision: int,
                  reason: DialogueResetReason) -> DialogueMemorySnapshot:
    bounds = limits()
    window = select_window(_tail(db, conversation_id, boundary=boundary, limit=bounds.bootstrap_messages),
                           boundary=boundary, limit=bounds.bootstrap_messages)
    metrics.increment("bootstraps", reason=reason.value)
    return bootstrap(
        user_ref=str(user_id), conversation_ref=str(conversation_id), events=_events(list(window.records),
                                                                                     user_id=user_id),
        window_start=window.window_start, revision=revision, reset_reason=reason, omissions=window.omissions,
        limits=bounds,
    )


def _settled_exposures(db: Session, snapshot: DialogueMemorySnapshot, *, user_id: uuid.UUID):
    """Source events for consumed messages whose pending delivery has since settled."""

    updates = []
    conversation_id = uuid.UUID(snapshot.conversation_ref)
    for item in snapshot.items:
        if not (isinstance(item, DialogueGap) and item.gap_kind == DialogueGapKind.DELIVERY_PENDING and item.is_current):
            continue
        record = _message(db, conversation_id, item.subject_message_ref)
        if record is None:
            continue  # a deleted source is caught by the prefix check
        event = source_event(record, user_ref=str(user_id))
        if event.exposure_state != DialogueExposureState.DELIVERY_PENDING:
            updates.append(event)
    return updates


def _successor(db: Session, current: DialogueMemorySnapshot | None, *, user_id: uuid.UUID,
               conversation_id: uuid.UUID, target: int) -> DialogueMemorySnapshot | None:
    """The committed state through ``target`` (caller holds the lock).  None when nothing changes."""

    if current is None:
        return _bootstrap_at(db, user_id=user_id, conversation_id=conversation_id, boundary=target, revision=1,
                             reason=DialogueResetReason.INITIAL_BOOTSTRAP)
    reset: DialogueResetReason | None = None
    if current.versions != DialogueMemoryVersions():
        reset = DialogueResetReason.CONTRACT_VERSION_CHANGE
    elif not prefix_intact(db, current):
        metrics.increment("prefix_mismatches")
        reset = DialogueResetReason.SOURCE_PREFIX_CHANGED
    if reset is None:
        records = _range(db, conversation_id, after=current.through_conversation_sequence,
                         through=max(target, current.through_conversation_sequence))
        try:
            validate_records(records, after_sequence=current.through_conversation_sequence,
                             conversation_ref=str(conversation_id))
        except DialogueMemoryError as exc:
            if exc.code != DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP:
                raise
            reset = DialogueResetReason.SOURCE_SEQUENCE_GAP
    if reset is not None:
        # Discontinuous lineage is never spliced: a new, explicit segment.
        boundary = max(target, current.through_conversation_sequence)
        return _bootstrap_at(db, user_id=user_id, conversation_id=conversation_id, boundary=boundary,
                             revision=current.revision + 1, reason=reset)
    updater = DialogueMemoryUpdaterV0(limits())
    successor = updater.apply(current, _events(records, user_id=user_id),
                              exposure_updates=_settled_exposures(db, current, user_id=user_id))
    return None if successor is current else successor


def _advance_locked(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, target: int,
                    ) -> DialogueMemorySnapshot | None:
    current = _head(db, user_id=user_id, conversation_id=conversation_id)
    successor = _successor(db, current, user_id=user_id, conversation_id=conversation_id, target=target)
    if successor is None:
        return current
    _persist(db, successor, expected_head=current.snapshot_id if current is not None else None)
    metrics.increment("snapshots_persisted", reset=successor.reset_reason.value if successor.reset_reason else "none")
    return successor


def advance(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, target: int) -> DialogueMemorySnapshot | None:
    """Advance the head through ``target`` and commit; publish the cache after commit."""

    _owner(db, user_id=user_id, conversation_id=conversation_id)
    lock_conversation(db, conversation_id)
    before = db.scalar(select(CognitiveDialogueMemoryHead.snapshot_id).where(
        CognitiveDialogueMemoryHead.conversation_id == conversation_id))
    snapshot = _advance_locked(db, user_id=user_id, conversation_id=conversation_id, target=target)
    db.commit()
    if snapshot is not None and str(before) != snapshot.snapshot_id:
        _publish(snapshot)
    return snapshot


def _historical(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, boundary: int,
                ) -> DialogueMemorySnapshot:
    """The committed state exactly through ``boundary`` when the head has moved past it.

    Rebuilt in memory from the newest persisted snapshot at or before the
    boundary (never the current head, which already holds future evidence).
    """

    row = db.scalar(
        select(CognitiveDialogueMemorySnapshot)
        .where(CognitiveDialogueMemorySnapshot.conversation_id == conversation_id,
               CognitiveDialogueMemorySnapshot.through_conversation_sequence <= boundary)
        .order_by(CognitiveDialogueMemorySnapshot.through_conversation_sequence.desc(),
                  CognitiveDialogueMemorySnapshot.revision.desc())
        .limit(1)
    )
    base = _from_row(row, user_id=user_id) if row is not None else None
    if base is not None and base.versions == DialogueMemoryVersions() and prefix_intact(db, base):
        records = _range(db, conversation_id, after=base.through_conversation_sequence, through=boundary)
        try:
            validate_records(records, after_sequence=base.through_conversation_sequence,
                             conversation_ref=str(conversation_id))
            return DialogueMemoryUpdaterV0(limits()).apply(
                base, _events(records, user_id=user_id),
                exposure_updates=_settled_exposures(db, base, user_id=user_id),
            )
        except DialogueMemoryError as exc:
            if exc.code != DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP:
                raise
    head = db.get(CognitiveDialogueMemoryHead, conversation_id)
    return _bootstrap_at(db, user_id=user_id, conversation_id=conversation_id, boundary=boundary,
                         revision=(head.revision + 1) if head is not None else 1,
                         reason=DialogueResetReason.INITIAL_BOOTSTRAP)


def turn_context(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, turn_id: uuid.UUID, focus_message_id: uuid.UUID,
) -> DialogueTurnContext:
    """The turn-local view for a capture: committed state through the message before the focus.

    Catches the head up (and persists it) when it is behind; never reads a
    newer head's content.  Raises ``DialogueMemoryError`` with a typed code.
    """

    _owner(db, user_id=user_id, conversation_id=conversation_id)
    focus = _message(db, conversation_id, str(focus_message_id))
    if focus is None or focus.role != "user" or focus.turn_ref != str(turn_id):
        raise fail(DialogueMemoryFailurePhase.SOURCE_LOADING, DialogueMemoryFailureCode.SOURCE_MESSAGE_MISSING,
                   "The focus message is missing or is not this turn's user message.")
    boundary = focus.conversation_sequence - 1
    lock_conversation(db, conversation_id)
    published = None
    head_row = db.get(CognitiveDialogueMemoryHead, conversation_id)
    if head_row is not None and head_row.through_conversation_sequence > boundary:
        snapshot = _historical(db, user_id=user_id, conversation_id=conversation_id, boundary=boundary)
        metrics.increment("context_sources", source="historical")
    elif head_row is not None and head_row.through_conversation_sequence == boundary:
        snapshot = _head(db, user_id=user_id, conversation_id=conversation_id, use_cache=True)
        if not prefix_intact(db, snapshot):
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING,
                       DialogueMemoryFailureCode.SOURCE_PREFIX_DIGEST_MISMATCH,
                       "A message the committed dialogue memory consumed has changed or been deleted.")
        metrics.increment("context_sources", source="head")
    elif boundary == 0 and head_row is None:
        # The first message of a conversation: nothing precedes it.
        snapshot = bootstrap(user_ref=str(user_id), conversation_ref=str(conversation_id), events=(),
                             window_start=1, revision=1, reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP)
        metrics.increment("context_sources", source="empty")
    else:
        snapshot = _advance_locked(db, user_id=user_id, conversation_id=conversation_id, target=boundary)
        if snapshot is None or snapshot.through_conversation_sequence != boundary:
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING, DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
                       "Dialogue memory could not be brought exactly to the message before the focus.")
        if head_row is None or str(head_row.snapshot_id) != snapshot.snapshot_id:
            published = snapshot
        metrics.increment("context_sources", source="catch_up")
    db.commit()
    if published is not None:
        _publish(published)
    return compile_turn_context(snapshot, source_event(focus, user_ref=str(user_id)), cognitive_turn_id=str(turn_id))


# ---------------------------------------------------------------------------
# Durable update jobs
# ---------------------------------------------------------------------------

def schedule_update(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, trigger_message_id: uuid.UUID,
    trigger_kind: str = TRIGGER_MESSAGE_COMMITTED,
) -> bool:
    """Add an update job inside the caller's transaction (the caller commits).

    Runs in a SAVEPOINT: if it cannot be written, only the savepoint rolls
    back, so the chat transaction it rides in always commits unchanged.  A
    lost job is recovered by the next capture's lazy catch-up.  A settled
    delivery re-arms its finished job rather than adding a second row.
    """

    if not enabled():
        return False
    # The caller's own rows flush outside the guard: their errors are the caller's.
    db.flush()
    try:
        with db.begin_nested():
            target = db.scalar(select(ChatMessage.conversation_sequence).where(
                ChatMessage.id == trigger_message_id, ChatMessage.conversation_id == conversation_id))
            if target is None:
                raise fail(DialogueMemoryFailurePhase.PERSISTENCE, DialogueMemoryFailureCode.SOURCE_MESSAGE_MISSING,
                           "The trigger message is not part of this conversation.")
            statement = insert(CognitiveDialogueMemoryJob).values(
                id=uuid.uuid4(), user_id=user_id, conversation_id=conversation_id,
                trigger_message_id=trigger_message_id, trigger_kind=trigger_kind,
                target_conversation_sequence=target, status="pending", attempts=0,
            )
            if trigger_kind == TRIGGER_DELIVERY_SETTLED:
                statement = statement.on_conflict_do_update(
                    constraint="uq_dialogue_memory_jobs_trigger",
                    set_=dict(status="pending", attempts=0, claim_token=None, lease_expires_at=None,
                              last_error_code=None, last_error_safe=None, processed_at=None),
                    where=CognitiveDialogueMemoryJob.status.in_(("completed", "failed", "skipped")),
                )
            else:
                statement = statement.on_conflict_do_nothing(constraint="uq_dialogue_memory_jobs_trigger")
            db.execute(statement)
        metrics.increment("jobs_scheduled", trigger=trigger_kind)
        return True
    except Exception as exc:  # noqa: BLE001 - never fail the live turn
        metrics.increment("schedule_failures", error=type(exc).__name__)
        logger.warning("Dialogue memory update not scheduled for conversation %s (%s)", conversation_id,
                       type(exc).__name__)
        return False


def wake_worker() -> None:
    settings = get_settings()
    if settings.redis_url:
        try:
            redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2).rpush(
                settings.cognitive_dialogue_memory_queue_name, "wake")
        except Exception:  # noqa: BLE001 - PostgreSQL polling is the source of truth
            pass


def claim_next_job(db: Session, *, lease_seconds: int) -> CognitiveDialogueMemoryJob | None:
    now = datetime.now(timezone.utc)
    job = db.scalar(
        select(CognitiveDialogueMemoryJob)
        .where((CognitiveDialogueMemoryJob.status == "pending") | (
            (CognitiveDialogueMemoryJob.status == "processing") & (CognitiveDialogueMemoryJob.lease_expires_at <= now)))
        .order_by(CognitiveDialogueMemoryJob.created_at, CognitiveDialogueMemoryJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.rollback()
        return None
    job.status = "processing"
    job.attempts += 1
    job.claim_token = secrets.token_urlsafe(32)
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()
    db.refresh(job)
    return job


def _finish(db: Session, job: CognitiveDialogueMemoryJob, claim_token: str, *, status: str,
            code: str | None = None, safe: str | None = None) -> bool:
    result = db.execute(
        update(CognitiveDialogueMemoryJob)
        .where(CognitiveDialogueMemoryJob.id == job.id, CognitiveDialogueMemoryJob.status == "processing",
               CognitiveDialogueMemoryJob.claim_token == claim_token)
        .values(status=status, processed_at=datetime.now(timezone.utc), lease_expires_at=None,
                last_error_code=code, last_error_safe=safe)
    )
    return bool(result.rowcount)


def release_for_retry(db: Session, job: CognitiveDialogueMemoryJob, claim_token: str, *, code: str) -> bool:
    result = db.execute(
        update(CognitiveDialogueMemoryJob)
        .where(CognitiveDialogueMemoryJob.id == job.id, CognitiveDialogueMemoryJob.status == "processing",
               CognitiveDialogueMemoryJob.claim_token == claim_token)
        .values(status="pending", claim_token=None, lease_expires_at=None, last_error_code=code,
                last_error_safe="A transient dialogue memory failure occurred; the update will be retried.")
    )
    db.commit()
    return bool(result.rowcount)


def process_job(db: Session, job: CognitiveDialogueMemoryJob, *, claim_token: str) -> str:
    """Advance to the job's captured target and finish the job in one transaction.

    Returns ``completed``, ``stale`` (the lease was lost: nothing written), or
    the typed failure code.  A retry reuses the captured target, never a later one.
    """

    owned = db.scalar(select(CognitiveDialogueMemoryJob.id).where(
        CognitiveDialogueMemoryJob.id == job.id, CognitiveDialogueMemoryJob.status == "processing",
        CognitiveDialogueMemoryJob.claim_token == claim_token,
    ).with_for_update())
    if owned is None:
        db.rollback()
        return "stale"
    _owner(db, user_id=job.user_id, conversation_id=job.conversation_id)
    lock_conversation(db, job.conversation_id)
    before = db.scalar(select(CognitiveDialogueMemoryHead.snapshot_id).where(
        CognitiveDialogueMemoryHead.conversation_id == job.conversation_id))
    snapshot = _advance_locked(db, user_id=job.user_id, conversation_id=job.conversation_id,
                               target=job.target_conversation_sequence)
    if snapshot is None or snapshot.through_conversation_sequence < job.target_conversation_sequence:
        raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
                   "The dialogue memory head could not reach the job's target.")
    if not _finish(db, job, claim_token, status="completed"):
        db.rollback()
        return "stale"
    db.commit()
    if str(before) != snapshot.snapshot_id:
        _publish(snapshot)
    return "completed"


def fail_job(db: Session, job: CognitiveDialogueMemoryJob, claim_token: str, *, code: str, safe: str) -> bool:
    finished = _finish(db, job, claim_token, status="failed", code=code, safe=safe)
    db.commit()
    return finished


def queue_depth(db: Session) -> int:
    return int(db.scalar(select(func.count(CognitiveDialogueMemoryJob.id)).where(
        CognitiveDialogueMemoryJob.status.in_(("pending", "processing")))) or 0)


__all__ = [
    "DialogueMemoryMetrics", "RedisDialogueMemoryCache", "TRIGGER_DELIVERY_SETTLED", "TRIGGER_MESSAGE_COMMITTED",
    "advance", "cache", "claim_next_job", "delete_cached_conversation", "enabled", "fail_job", "limits",
    "lock_conversation", "metrics", "prefix_intact", "process_job", "queue_depth", "release_for_retry",
    "schedule_update", "turn_context", "wake_worker",
]
