"""Ledger order, persistence, jobs, cache, and deletion on real PostgreSQL (24.2, 24.5, 24.6).

Set TEST_DATABASE_URL to a disposable PostgreSQL database; each test runs in
its own schema.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.ai.Cognitive.system.v0.contracts.dialogue_memory import (
    DialogueGap,
    DialogueGapKind,
    DialogueMemoryFailureCode,
    DialogueMemorySnapshot,
    DialogueUtteranceReference,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import (
    DialogueMemoryError,
    bootstrap,
    dialogue_state_digest,
)
from app.ai.Cognitive.system.v0.dialogue_memory_v0.cache_protocol import snapshot_key
from app.ai.Cognitive.system.v0.contracts.dialogue_memory import DialogueResetReason
from app.db.models import (
    ChatMessage,
    CognitiveDialogueMemoryHead,
    CognitiveDialogueMemoryJob,
    CognitiveDialogueMemorySnapshot,
    Conversation,
    MessageDelivery,
    User,
)
from app.services import chat as chat_service
from app.services import dialogue_memory as service
from app.workers import dialogue_memory as worker
from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: F401
from dialogue_memory_support import dialogue_env  # noqa: F401


def _turn(db, conversation, user_text, reply=None, status="completed", *, schedule=True):
    turn_id = uuid.uuid4()
    user_message = chat_service.add_message(db, conversation=conversation, role="user", content=user_text,
                                            turn_id=turn_id, commit=False)
    assistant = None
    if reply is not None:
        assistant = chat_service.add_message(db, conversation=conversation, role="assistant", content=reply,
                                             turn_id=turn_id, status=status, commit=False)
    if schedule:
        service.schedule_update(db, user_id=conversation.user_id, conversation_id=conversation.id,
                                trigger_message_id=(assistant or user_message).id)
    db.commit()
    return user_message, assistant


def _seed(env, *exchanges, schedule=True):
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        made = [_turn(db, conversation, *exchange, schedule=schedule) for exchange in exchanges]
        return user.id, conversation.id, [(u.id, a.id if a else None) for u, a in made]


def _drain() -> int:
    count = 0
    while worker.run_once():
        count += 1
    return count


def _head(env, conversation_id) -> DialogueMemorySnapshot | None:
    with env.db() as db:
        row = db.get(CognitiveDialogueMemoryHead, conversation_id)
        if row is None:
            return None
        return DialogueMemorySnapshot.model_validate(db.get(CognitiveDialogueMemorySnapshot, row.snapshot_id)
                                                     .snapshot_payload)


def _count(env, model, **where) -> int:
    with env.db() as db:
        statement = select(func.count()).select_from(model)
        for key, value in where.items():
            statement = statement.where(getattr(model, key) == value)
        return int(db.scalar(statement))


# ---------------------------------------------------------------------------
# Ledger order (24.2)
# ---------------------------------------------------------------------------

def test_every_append_path_allocates_contiguous_positions(dialogue_env) -> None:
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        _turn(db, conversation, "hi", "hello")
        chat_service.add_message(db, conversation=conversation, role="assistant", content="A nudge.")  # unprompted
        db.add(ChatMessage(conversation_id=conversation.id, role="user", content="raw insert",
                           source_channel="whatsapp"))  # the WhatsApp batcher's direct insert
        db.commit()
        positions = list(db.scalars(select(ChatMessage.conversation_sequence)
                                    .where(ChatMessage.conversation_id == conversation.id)
                                    .order_by(ChatMessage.conversation_sequence)))
        counter = db.scalar(select(Conversation.last_message_sequence).where(Conversation.id == conversation.id))
    assert positions == [1, 2, 3, 4] and counter == 4


def test_concurrent_appends_never_duplicate_a_position(dialogue_env) -> None:
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation_id = create_conversation(db, user).id
    errors = []

    def append(index: int) -> None:
        try:
            for step in range(10):
                with dialogue_env.db() as db:
                    db.add(ChatMessage(conversation_id=conversation_id, role="user", content=f"{index}-{step}"))
                    db.commit()
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with dialogue_env.db() as db:
        positions = sorted(db.scalars(select(ChatMessage.conversation_sequence)
                                      .where(ChatMessage.conversation_id == conversation_id)))
    assert positions == list(range(1, 81))


def test_positions_cannot_be_supplied_or_changed(dialogue_env) -> None:
    _, conversation_id, made = _seed(dialogue_env, ("hi", "hello"), schedule=False)
    with dialogue_env.db() as db:
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(text("INSERT INTO chat_messages (id, conversation_id, role, content, status, source_channel, "
                            "conversation_sequence, created_at) VALUES (:id, :c, 'user', 'x', 'completed', 'web', "
                            "99, now())"), {"id": uuid.uuid4(), "c": conversation_id})
            db.commit()
        db.rollback()
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(update(ChatMessage).where(ChatMessage.id == made[0][0]).values(conversation_sequence=7))
            db.commit()


# ---------------------------------------------------------------------------
# Scheduling atomicity and isolation (24.5)
# ---------------------------------------------------------------------------

def test_message_and_update_job_commit_or_roll_back_together(dialogue_env) -> None:
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        conversation_id = conversation.id
        message = chat_service.add_message(db, conversation=conversation, role="user", content="kept",
                                           turn_id=uuid.uuid4(), commit=False)
        assert service.schedule_update(db, user_id=user.id, conversation_id=conversation.id,
                                       trigger_message_id=message.id)
        db.commit()
        doomed = chat_service.add_message(db, conversation=conversation, role="user", content="rolled back",
                                          turn_id=uuid.uuid4(), commit=False)
        service.schedule_update(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=doomed.id)
        db.rollback()
    assert _count(dialogue_env, ChatMessage, conversation_id=conversation_id) == 1
    assert _count(dialogue_env, CognitiveDialogueMemoryJob, conversation_id=conversation_id) == 1


def test_a_scheduling_failure_never_fails_the_chat_transaction(dialogue_env, monkeypatch) -> None:
    def broken_insert(*args, **kwargs):
        raise RuntimeError("queue table unavailable")

    monkeypatch.setattr(service, "insert", broken_insert)
    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        conversation_id = conversation.id
        message = chat_service.add_message(db, conversation=conversation, role="user", content="still saved",
                                           turn_id=uuid.uuid4(), commit=False)
        assert service.schedule_update(db, user_id=user.id, conversation_id=conversation.id,
                                       trigger_message_id=message.id) is False
        db.commit()
    assert _count(dialogue_env, ChatMessage, conversation_id=conversation_id) == 1
    assert service.metrics.snapshot()["schedule_failures|error=RuntimeError"] == 1


def test_disabled_dialogue_memory_schedules_nothing(dialogue_env) -> None:
    dialogue_env.configure(cognitive_dialogue_memory_enabled=False)
    _seed(dialogue_env, ("hi", "hello"))
    assert _count(dialogue_env, CognitiveDialogueMemoryJob) == 0
    assert worker.run_once() is False


# ---------------------------------------------------------------------------
# Worker, idempotency, concurrency (24.5)
# ---------------------------------------------------------------------------

def test_worker_advances_the_head_per_turn_and_retries_are_idempotent(dialogue_env) -> None:
    user_id, conversation_id, _ = _seed(dialogue_env, ("I have a dog named Max.", "Nice!"), ("What about food?",
                                                                                           "Kibble works."))
    assert _drain() == 2
    head = _head(dialogue_env, conversation_id)
    assert head.through_conversation_sequence == 4 and head.revision == 2
    snapshots = _count(dialogue_env, CognitiveDialogueMemorySnapshot, conversation_id=conversation_id)
    with dialogue_env.db() as db:  # re-arm every job: a retry must not create a different successor
        db.execute(update(CognitiveDialogueMemoryJob).values(status="pending", claim_token=None, attempts=0))
        db.commit()
    assert _drain() == 2
    assert _count(dialogue_env, CognitiveDialogueMemorySnapshot, conversation_id=conversation_id) == snapshots
    assert _head(dialogue_env, conversation_id) == head


def test_a_stale_worker_token_writes_nothing(dialogue_env) -> None:
    _, conversation_id, _ = _seed(dialogue_env, ("hi", "hello"))
    with dialogue_env.db() as db:
        job = service.claim_next_job(db, lease_seconds=60)
    with dialogue_env.db() as db:  # the lease expired and another worker reclaimed it
        db.execute(update(CognitiveDialogueMemoryJob).values(claim_token="someone-else"))
        db.commit()
    with dialogue_env.db() as db:
        assert service.process_job(db, job, claim_token=job.claim_token) == "stale"
    assert _count(dialogue_env, CognitiveDialogueMemorySnapshot) == 0
    assert _head(dialogue_env, conversation_id) is None


def test_conversation_serialization_prevents_lost_updates(dialogue_env) -> None:
    _, conversation_id, _ = _seed(dialogue_env, *[(f"Message {index}.", f"Reply {index}.") for index in range(6)])
    results, errors = [], []

    def work() -> None:
        try:
            while worker.run_once():
                results.append(1)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with dialogue_env.db() as db:
        rows = list(db.scalars(select(CognitiveDialogueMemorySnapshot)
                               .where(CognitiveDialogueMemorySnapshot.conversation_id == conversation_id)
                               .order_by(CognitiveDialogueMemorySnapshot.revision)))
        statuses = set(db.scalars(select(CognitiveDialogueMemoryJob.status)))
    assert statuses == {"completed"}
    assert [row.revision for row in rows] == list(range(1, len(rows) + 1))
    for previous, current in zip(rows, rows[1:]):
        assert current.predecessor_snapshot_id == previous.id  # one unbroken chain, nothing lost
    assert rows[-1].through_conversation_sequence == 12


def test_user_only_terminal_turn_advances_over_the_user_message(dialogue_env) -> None:
    _, conversation_id, made = _seed(dialogue_env, ("Are you there?",))
    _drain()
    head = _head(dialogue_env, conversation_id)
    assert head.through_conversation_sequence == 1
    assert [item.message_ref for item in head.items if isinstance(item, DialogueUtteranceReference)] == [
        str(made[0][0])]


def test_partial_and_failed_outcomes_use_the_exact_policy(dialogue_env) -> None:
    _, conversation_id, made = _seed(dialogue_env, ("First?", "Partial ans", "partial"), ("Second?", "", "failed"),
                                     ("Third?", "", "cancelled"))
    _drain()
    head = _head(dialogue_env, conversation_id)
    partial = next(item for item in head.items if isinstance(item, DialogueUtteranceReference)
                   and item.message_ref == str(made[0][1]))
    assert partial.completeness == "partial"
    failed = [item for item in head.items if isinstance(item, DialogueGap)
              and item.gap_kind == DialogueGapKind.RESPONSE_FAILED]
    assert [item.subject_message_ref for item in failed] == [str(made[1][1])]
    assert not any(str(made[2][1]) in item.source_message_refs for item in head.items)


def test_whatsapp_delivery_settles_a_pending_utterance_later(dialogue_env) -> None:
    from app.services import whatsapp as whatsapp_service

    with dialogue_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        from app.db.models import ChannelIdentity

        identity = ChannelIdentity(provider="whatsapp", provider_account_id="phone", external_subject="wa-user",
                                   user_id=user.id)
        db.add(identity)
        db.flush()
        turn_id = uuid.uuid4()
        chat_service.add_message(db, conversation=conversation, role="user", content="Hi", turn_id=turn_id,
                                 source_channel="whatsapp", commit=False)
        reply = chat_service.add_message(db, conversation=conversation, role="assistant", content="Hello!",
                                         turn_id=turn_id, source_channel="whatsapp", commit=False)
        whatsapp_service.schedule_delivery(db, assistant_message_id=reply.id, channel_identity_id=identity.id,
                                           content="Hello!")
        service.schedule_update(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=reply.id)
        db.commit()
        reply_id, conversation_id = reply.id, conversation.id
    _drain()
    pending = _head(dialogue_env, conversation_id)
    assert [item.gap_kind for item in pending.items if isinstance(item, DialogueGap)] == [
        DialogueGapKind.DELIVERY_PENDING]
    with dialogue_env.db() as db:  # the provider reports delivery through the webhook path
        db.execute(update(MessageDelivery).values(provider_message_id="wamid.1", status="sent"))
        assert whatsapp_service.apply_delivery_status(db, provider_message_id="wamid.1", status="delivered")
        db.commit()
    _drain()
    settled = _head(dialogue_env, conversation_id)
    assert settled.revision == pending.revision + 1
    assert settled.through_conversation_sequence == pending.through_conversation_sequence
    utterance = next(item for item in settled.items if isinstance(item, DialogueUtteranceReference)
                     and item.message_ref == str(reply_id))
    assert utterance.completeness == "complete" and utterance.exposure_evidence_refs


# ---------------------------------------------------------------------------
# Lazy catch-up, crash recovery, CAS, immutability (24.5)
# ---------------------------------------------------------------------------

def _records_state(env, conversation_id, user_id, through):
    from app.services.dialogue_memory import _events, _range

    with env.db() as db:
        records = _range(db, conversation_id, after=0, through=through)
        events = _events(records, user_id=user_id)
    return dialogue_state_digest(bootstrap(user_ref=str(user_id), conversation_ref=str(conversation_id),
                                           events=events, window_start=1, revision=1,
                                           reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP))


def test_lazy_catch_up_equals_eager_updates_after_lost_jobs(dialogue_env) -> None:
    user_id, conversation_id, _ = _seed(dialogue_env, ("I have a cat named Luna.", "Cute!"),
                                        ("Suggest toys.", "Options:\n1. Ball\n2. Feather"),
                                        ("The first one.", "Great."))
    with dialogue_env.db() as db:  # a crash lost every wake-up and job
        db.execute(CognitiveDialogueMemoryJob.__table__.delete())
        db.commit()
        focus = chat_service.add_message(db, conversation=db.get(Conversation, conversation_id), role="user",
                                         content="What did I just tell you?", turn_id=uuid.uuid4())
        context = service.turn_context(db, user_id=user_id, conversation_id=conversation_id,
                                       turn_id=focus.turn_id, focus_message_id=focus.id)
    head = _head(dialogue_env, conversation_id)
    assert head.through_conversation_sequence == 6 == context.focus_conversation_sequence - 1
    assert context.committed_snapshot_ref == head.snapshot_id
    assert dialogue_state_digest(head) == _records_state(dialogue_env, conversation_id, user_id, 6)


def test_eager_chain_and_one_shot_catch_up_remember_the_same_state(dialogue_env) -> None:
    user_id, conversation_id, _ = _seed(dialogue_env, ("My dog is Rex.", "Hi Rex!"), ("Let's talk about walks.",
                                                                                      "Sure."))
    _drain()
    eager = _head(dialogue_env, conversation_id)
    assert eager.revision == 2
    assert dialogue_state_digest(eager) == _records_state(dialogue_env, conversation_id, user_id, 4)


def test_head_compare_and_set_rejects_a_wrong_predecessor(dialogue_env) -> None:
    user_id, conversation_id, _ = _seed(dialogue_env, ("hi", "hello"))
    _drain()
    head = _head(dialogue_env, conversation_id)
    from app.services.dialogue_memory import _persist

    with dialogue_env.db() as db:
        with pytest.raises(DialogueMemoryError) as error:
            _persist(db, head, expected_head=str(uuid.uuid4()))
        assert error.value.code == DialogueMemoryFailureCode.PREDECESSOR_CONFLICT
        db.rollback()


def test_snapshot_rows_are_immutable_but_cascade_and_fk_clearing_work(dialogue_env) -> None:
    user_id, conversation_id, made = _seed(dialogue_env, ("hi", "hello"))
    _drain()
    with dialogue_env.db() as db:
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(update(CognitiveDialogueMemorySnapshot).values(semantic_digest="0" * 64))
            db.commit()
        db.rollback()
        with pytest.raises((IntegrityError, DBAPIError)):
            db.execute(update(CognitiveDialogueMemorySnapshot).values(snapshot_payload={}))
            db.commit()
        db.rollback()
        # Deleting the consumed message clears only the provenance pointer.
        db.execute(ChatMessage.__table__.delete().where(ChatMessage.id == made[0][1]))
        db.commit()
        row = db.scalar(select(CognitiveDialogueMemorySnapshot))
        assert row.through_message_id is None and row.snapshot_payload["through_message_id"] == str(made[0][1])


def test_deleted_consumed_message_starts_a_typed_reset_segment(dialogue_env) -> None:
    user_id, conversation_id, made = _seed(dialogue_env, ("one", "two"), ("three", "four"))
    _drain()
    with dialogue_env.db() as db:
        db.execute(ChatMessage.__table__.delete().where(ChatMessage.id == made[0][1]))
        db.commit()
        conversation = db.get(Conversation, conversation_id)
        _turn(db, conversation, "five", "six")
    _drain()
    head = _head(dialogue_env, conversation_id)
    assert head.reset_reason.value == "source_prefix_changed" and head.predecessor_snapshot_ref is None
    assert any(item.reason.value == "source_deleted" for item in head.omitted_history)


def test_conversation_and_account_deletion_cascade_every_row_and_cache_key(dialogue_env, monkeypatch) -> None:
    from app.services import auth as auth_service
    from app.services import file_deletion

    purged = []
    monkeypatch.setattr(service, "delete_cached_conversation", lambda conversation_id: purged.append(conversation_id))
    monkeypatch.setattr(file_deletion, "wake_worker", lambda: None)
    user_id, conversation_id, _ = _seed(dialogue_env, ("hi", "hello"))
    _drain()
    with dialogue_env.db() as db:
        assert chat_service.delete_conversation(db, user_id=user_id, conversation_id=conversation_id)
    assert purged == [conversation_id]
    for model in (CognitiveDialogueMemorySnapshot, CognitiveDialogueMemoryHead, CognitiveDialogueMemoryJob):
        assert _count(dialogue_env, model) == 0

    user_id, conversation_id, _ = _seed(dialogue_env, ("again", "hello"))
    _drain()
    with dialogue_env.db() as db:
        auth_service.delete_user(db, db.get(User, user_id))
    assert purged[-1] == conversation_id
    for model in (CognitiveDialogueMemorySnapshot, CognitiveDialogueMemoryHead, CognitiveDialogueMemoryJob):
        assert _count(dialogue_env, model) == 0


def test_foreign_user_cannot_read_or_advance_a_conversation(dialogue_env) -> None:
    _, conversation_id, _ = _seed(dialogue_env, ("hi", "hello"))
    with dialogue_env.db() as db:
        with pytest.raises(DialogueMemoryError) as error:
            service.advance(db, user_id=uuid.uuid4(), conversation_id=conversation_id, target=2)
    assert error.value.code == DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED


# ---------------------------------------------------------------------------
# Cache (24.6)
# ---------------------------------------------------------------------------

class FakeCache:
    def __init__(self) -> None:
        self.entries: dict[str, bytes] = {}
        self.fail = False
        self.puts = 0

    def get(self, conversation_ref, revision, digest):
        if self.fail:
            raise ConnectionError("cache down")
        return self.entries.get(snapshot_key(conversation_ref, revision, digest))

    def put(self, snapshot, ttl_seconds):
        if self.fail:
            raise ConnectionError("cache down")
        from app.ai.Cognitive.system.v0.dialogue_memory_v0.cache_protocol import encode

        self.puts += 1
        self.entries[snapshot_key(snapshot.conversation_ref, snapshot.revision, snapshot.semantic_digest)] = encode(snapshot)

    def delete_conversation(self, conversation_ref):
        self.entries = {key: value for key, value in self.entries.items() if conversation_ref not in key}


def _context(env, conversation_id, user_id, focus):
    with env.db() as db:
        return service.turn_context(db, user_id=user_id, conversation_id=conversation_id, turn_id=focus.turn_id,
                                    focus_message_id=focus.id)


def test_every_cache_mode_yields_the_identical_turn_context(dialogue_env, monkeypatch) -> None:
    user_id, conversation_id, _ = _seed(dialogue_env, ("My dog is Rex.", "Hi Rex!"))
    with dialogue_env.db() as db:
        focus = chat_service.add_message(db, conversation=db.get(Conversation, conversation_id), role="user",
                                         content="Feed him what?", turn_id=uuid.uuid4())
    monkeypatch.setattr(service, "_cache_override", None)
    disabled = _context(dialogue_env, conversation_id, user_id, focus)  # also persists the head (catch-up)

    cache = FakeCache()
    monkeypatch.setattr(service, "_cache_override", cache)
    miss = _context(dialogue_env, conversation_id, user_id, focus)
    head = _head(dialogue_env, conversation_id)
    cache.put(head, 60)
    hit = _context(dialogue_env, conversation_id, user_id, focus)
    assert service.metrics.snapshot().get("cache_hits") == 1
    for key in cache.entries:  # corruption is discarded, never consumed
        cache.entries[key] = cache.entries[key].replace(b'"revision":1', b'"revision":2')
    corrupt = _context(dialogue_env, conversation_id, user_id, focus)
    assert service.metrics.snapshot().get("cache_mismatches") == 1 and cache.entries == {}
    cache.fail = True
    unavailable = _context(dialogue_env, conversation_id, user_id, focus)
    assert disabled == miss == hit == corrupt == unavailable


def test_cache_is_published_only_after_commit_and_keys_carry_no_content(dialogue_env, monkeypatch) -> None:
    cache = FakeCache()
    monkeypatch.setattr(service, "_cache_override", cache)
    _, conversation_id, _ = _seed(dialogue_env, ("My secret is pineapple.", "Noted."))
    real_finish = service._finish

    def lose_lease(db, job, claim_token, **kwargs):
        return False  # the commit never happens

    monkeypatch.setattr(service, "_finish", lose_lease)
    _drain()
    assert cache.puts == 0 and _count(dialogue_env, CognitiveDialogueMemorySnapshot) == 0
    monkeypatch.setattr(service, "_finish", real_finish)
    with dialogue_env.db() as db:
        db.execute(update(CognitiveDialogueMemoryJob).values(status="pending", claim_token=None))
        db.commit()
    _drain()
    assert cache.puts == 1
    assert all("pineapple" not in key and "secret" not in key for key in cache.entries)
    with dialogue_env.db() as db:
        chat_service.delete_conversation(db, user_id=db.get(Conversation, conversation_id).user_id,
                                         conversation_id=conversation_id)
    assert cache.entries == {}


def test_cache_failure_never_rolls_back_a_valid_update(dialogue_env, monkeypatch) -> None:
    cache = FakeCache()
    cache.fail = True
    monkeypatch.setattr(service, "_cache_override", cache)
    _, conversation_id, _ = _seed(dialogue_env, ("hi", "hello"))
    _drain()
    assert _head(dialogue_env, conversation_id).through_conversation_sequence == 2
    assert service.metrics.snapshot()["cache_failures|error=ConnectionError|operation=put"] == 1


# ---------------------------------------------------------------------------
# Migration (24.2)
# ---------------------------------------------------------------------------

def test_migration_backfills_positions_and_round_trips_in_an_isolated_database() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the migration round trip")
    name = "dialogue_migration_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, check=True, capture_output=True,
                       env={**os.environ, "DATABASE_URL": target, "PYTHONPATH": str(backend)})

    engine = create_engine(target)
    try:
        alembic("upgrade", "a7c4e9b2d1f3")
        user, conversation, first, second = (uuid.uuid4() for _ in range(4))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, name, email, hashed_password, created_at) "
                                    "VALUES (:u, 'x', :e, 'h', now())"), {"u": user, "e": f"{user}@x"})
            connection.execute(text("INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                                    "VALUES (:c, :u, 't', now(), now())"), {"c": conversation, "u": user})
            rows = [  # (turn, role, created_at): the second turn's rows share one timestamp
                (first, "user", "2026-01-01 10:00:00"), (first, "assistant", "2026-01-01 10:00:30"),
                (None, "assistant", "2026-01-01 10:01:00"),
                (second, "assistant", "2026-01-01 10:02:00"), (second, "user", "2026-01-01 10:02:00"),
            ]
            ids = []
            for turn, role, created in rows:
                message = uuid.uuid4()
                ids.append((role, turn))
                connection.execute(text("INSERT INTO chat_messages (id, turn_id, conversation_id, role, content, "
                                        "created_at) VALUES (:i, :t, :c, :r, 'x', :at)"),
                                   {"i": message, "t": turn, "c": conversation, "r": role, "at": created})
            for turn, created in ((first, "2026-01-01 10:00:00"), (second, "2026-01-01 10:02:00")):
                connection.execute(text("INSERT INTO chat_jobs (id, turn_id, user_id, conversation_id, payload, "
                                        "status, attempts, created_at, updated_at) VALUES (:i, :t, :u, :c, '{}', "
                                        "'completed', 1, :at, now())"),
                                   {"i": uuid.uuid4(), "t": turn, "u": user, "c": conversation, "at": created})
        alembic("upgrade", "head")
        with engine.begin() as connection:
            ordered = connection.execute(text(
                "SELECT role, turn_id, conversation_sequence FROM chat_messages ORDER BY conversation_sequence"
            )).all()
            counter = connection.execute(text("SELECT last_message_sequence FROM conversations")).scalar_one()
        assert [(row.role, row.turn_id) for row in ordered] == [
            ("user", first), ("assistant", first), ("assistant", None), ("user", second), ("assistant", second)]
        assert [row.conversation_sequence for row in ordered] == [1, 2, 3, 4, 5] and counter == 5
        engine.dispose()
        alembic("downgrade", "a7c4e9b2d1f3")
        with engine.begin() as connection:
            tables = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE tablename LIKE 'cognitive_dialogue%'")).scalars())
            columns = set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'chat_messages'")).scalars())
        assert tables == set() and "conversation_sequence" not in columns
        engine.dispose()
        alembic("upgrade", "head")
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
