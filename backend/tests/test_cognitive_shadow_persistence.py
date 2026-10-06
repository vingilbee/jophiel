"""Capture, queue, constraints, and deletion on real PostgreSQL.

Set TEST_DATABASE_URL to a disposable PostgreSQL database; each test runs in
its own schema.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import (
    CAPTURE_MESSAGE_LIMIT, CognitiveShadowCapture, GenerationPath, ShadowSourceChannel, capture_id_for,
)
from app.db.models import ChatMessage, CognitiveShadowJob, CognitiveShadowTrace, Conversation, GoogleAccount, User
from app.services import cognitive_shadow as service
from app.services import image_store
from cognitive_shadow_support import (  # noqa: F401  (fixtures)
    add_turn, create_conversation, create_user, database, no_wakeups, shadow_env,
)
from PIL import Image


def _capture(env, conversation, message, job, **kwargs) -> bool:
    return service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=message.turn_id,
        user_message_id=message.id, chat_job_id=job.id if job is not None else None,
        generation_path=GenerationPath.CHAT_WORKER if job is not None else GenerationPath.LOCAL_FALLBACK,
        source_channel=kwargs.pop("source_channel", ShadowSourceChannel.WEB), **kwargs,
    )


def _job(env, turn_id) -> CognitiveShadowJob:
    with env.db() as db:
        return db.scalar(select(CognitiveShadowJob).where(CognitiveShadowJob.turn_id == turn_id))


def _seed(env, *turns: str):
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        made = [add_turn(db, conversation, content, reply=f"reply {index}") for index, content in enumerate(turns)]
    return user, conversation, made


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def test_capture_preserves_exact_durable_messages_and_focus(shadow_env, monkeypatch) -> None:
    with no_wakeups(monkeypatch) as wakes:
        with shadow_env.db() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            first, _ = add_turn(db, conversation, "hi", reply="Hello!")
            # A nudge produces two adjacent assistant messages; they stay separate.
            db.add(ChatMessage(conversation_id=conversation.id, role="assistant", content="A nudge.",
                               created_at=datetime(2026, 9, 1, 12, 0, 12)))
            db.add(ChatMessage(conversation_id=conversation.id, turn_id=uuid.uuid4(), role="assistant",
                               content="", status="cancelled", created_at=datetime(2026, 9, 1, 12, 0, 13)))
            db.commit()
            focus, job = add_turn(db, conversation, "How much protein is in tofu?")
        assert _capture(shadow_env, conversation, focus, job)
    row = _job(shadow_env, focus.turn_id)
    capture = CognitiveShadowCapture.model_validate(row.capture_payload)
    assert [(m.role, m.content) for m in capture.messages] == [
        ("user", "hi"), ("assistant", "Hello!"), ("assistant", "A nudge."), ("user", "How much protein is in tofu?"),
    ]
    assert capture.focus_message_id == str(focus.id) == capture.messages[-1].message_id
    assert capture.messages[0].message_id == str(first.id)
    assert capture.previous_user_message_id == str(first.id)
    assert capture.context_truncated is False
    assert capture.chat_job_ref == str(job.id) and capture.generation_path == GenerationPath.CHAT_WORKER
    assert row.capture_digest == payload_digest(row.capture_payload)
    assert row.id == uuid.UUID(capture_id_for(str(focus.turn_id)))
    assert row.status == "pending" and row.attempts == 0
    assert wakes == [str(row.id)]


def test_bounded_history_marks_truncation(shadow_env) -> None:
    turns = [f"message {index}" for index in range(CAPTURE_MESSAGE_LIMIT)]
    _, conversation, made = _seed(shadow_env, *turns)
    focus, job = made[-1]
    assert _capture(shadow_env, conversation, focus, job)
    capture = CognitiveShadowCapture.model_validate(_job(shadow_env, focus.turn_id).capture_payload)
    assert len(capture.messages) == CAPTURE_MESSAGE_LIMIT
    assert capture.context_truncated is True
    assert capture.messages[-1].message_id == str(focus.id)
    short_focus, short_job = made[1]
    assert _capture(shadow_env, conversation, short_focus, short_job)
    short = CognitiveShadowCapture.model_validate(_job(shadow_env, short_focus.turn_id).capture_payload)
    assert [m.content for m in short.messages] == ["message 0", "reply 0", "message 1"]
    assert short.context_truncated is False


@pytest.mark.parametrize("payload, channel", (
    ({"content": "x", "voice": True}, ShadowSourceChannel.VOICE),
    ({"content": "x", "channel": "whatsapp"}, ShadowSourceChannel.WHATSAPP),
    ({"content": "x"}, ShadowSourceChannel.WEB),
))
def test_channels_and_local_fallback(shadow_env, payload, channel) -> None:
    assert service.source_channel_for(payload) == channel
    with shadow_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        focus, job = add_turn(db, conversation, "hello", payload=payload)
        fallback, _ = add_turn(db, conversation, "typed without a worker", with_job=False)
    assert _capture(shadow_env, conversation, focus, job, source_channel=service.source_channel_for(payload))
    assert _job(shadow_env, focus.turn_id).source_channel == channel.value
    assert _capture(shadow_env, conversation, fallback, None)
    row = _job(shadow_env, fallback.turn_id)
    capture = CognitiveShadowCapture.model_validate(row.capture_payload)
    assert row.generation_path == "local_fallback" and row.chat_job_id is None
    assert capture.generation_path == GenerationPath.LOCAL_FALLBACK and capture.chat_job_ref is None


def test_attachments_are_server_verified(shadow_env) -> None:
    with shadow_env.db() as db:
        user = create_user(db)
        other = create_user(db)
        conversation = create_conversation(db, user)
    mine = image_store.save_pil(user.id, Image.new("RGB", (8, 8))).image_id
    theirs = image_store.save_pil(other.id, Image.new("RGB", (8, 8))).image_id
    with shadow_env.db() as db:
        conversation = db.get(Conversation, conversation.id)
        focus, job = add_turn(db, conversation, "what is this?", image_ids=[mine, theirs, "forged0000"])
    assert _capture(shadow_env, conversation, focus, job)
    capture = CognitiveShadowCapture.model_validate(_job(shadow_env, focus.turn_id).capture_payload)
    assert capture.attachments_available is True
    assert [(a.store, a.attachment_id) for a in capture.attachments] == [("image_store", mine)]


def test_capture_contains_no_tokens_secrets_or_awareness(shadow_env) -> None:
    with shadow_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        db.add(GoogleAccount(
            user_id=user.id, encrypted_refresh_token="SECRET-REFRESH", encrypted_access_token="SECRET-ACCESS",
            scopes="https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/drive",
        ))
        db.commit()
        focus, job = add_turn(db, conversation, "hi", payload={
            "content": "hi", "awareness": "AWARENESS-PROSE", "screen_image_id": "SCREEN",
        })
    assert _capture(shadow_env, conversation, focus, job)
    text_payload = str(_job(shadow_env, focus.turn_id).capture_payload)
    for secret in ("SECRET-REFRESH", "SECRET-ACCESS", "AWARENESS-PROSE", "SCREEN", "googleapis"):
        assert secret not in text_payload
    capture = CognitiveShadowCapture.model_validate(_job(shadow_env, focus.turn_id).capture_payload)
    assert set(capture.capabilities.capability_keys) >= {"email_mailbox_access", "drive_document_access"}


def test_sampling_is_deterministic_across_processes_and_retries() -> None:
    turn = uuid.UUID("11111111-2222-4333-8444-555555555555")
    decisions = {service.is_sampled(turn, policy_version="shadow-sampling-v0.1", rate=0.37) for _ in range(5)}
    assert len(decisions) == 1
    assert not service.is_sampled(turn, policy_version="p", rate=0.0)
    assert service.is_sampled(turn, policy_version="p", rate=1.0)
    turns = [uuid.uuid5(uuid.NAMESPACE_URL, str(index)) for index in range(2000)]
    selected = sum(service.is_sampled(item, policy_version="shadow-sampling-v0.1", rate=0.25) for item in turns)
    assert 400 < selected < 600
    # The hash covers only the policy version and turn id (a pinned vector).
    import hashlib
    digest = hashlib.sha256(f"shadow-sampling-v0.1:{turn}".encode()).digest()
    assert service.is_sampled(turn, policy_version="shadow-sampling-v0.1", rate=0.5) == (
        int.from_bytes(digest[:8], "big") / 2**64 < 0.5
    )


def test_kill_switch_unsampled_and_unreviewed_retention_create_nothing(shadow_env) -> None:
    _, conversation, made = _seed(shadow_env, "one", "two", "three")
    shadow_env.configure(cognitive_live_shadow_enabled=False)
    assert not _capture(shadow_env, conversation, *made[0])
    shadow_env.configure(cognitive_live_shadow_enabled=True, cognitive_live_shadow_sample_rate=0.0)
    assert not _capture(shadow_env, conversation, *made[1])
    shadow_env.configure(cognitive_live_shadow_sample_rate=1.0, cognitive_live_shadow_retention_days=None)
    assert not _capture(shadow_env, conversation, *made[2])
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowJob.id)) is None
    counts = service.metrics.snapshot()["counts"]
    assert counts["unsampled_turns"] == 1
    assert counts["capture_failures|code=retention_unreviewed"] == 1


def test_capture_is_idempotent_and_never_rewritten(shadow_env) -> None:
    _, conversation, made = _seed(shadow_env, "first")
    focus, job = made[0]
    assert _capture(shadow_env, conversation, focus, job)
    original = _job(shadow_env, focus.turn_id).capture_payload
    # Later database changes cannot alter an existing capture.
    with shadow_env.db() as db:
        db.execute(update(ChatMessage).where(ChatMessage.id == focus.id).values(content="edited later"))
        db.commit()
    assert not _capture(shadow_env, conversation, focus, job)
    row = _job(shadow_env, focus.turn_id)
    assert row.capture_payload == original
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 1
    assert service.metrics.snapshot()["counts"]["capture_duplicates"] == 1


def test_capture_rejects_foreign_or_mismatched_records(shadow_env) -> None:
    _, conversation, made = _seed(shadow_env, "mine")
    focus, job = made[0]
    with shadow_env.db() as db:
        stranger = create_user(db)
    assert not service.capture_turn(
        user_id=stranger.id, conversation_id=conversation.id, turn_id=focus.turn_id,
        user_message_id=focus.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert not service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=uuid.uuid4(),
        user_message_id=focus.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    counts = service.metrics.snapshot()["counts"]
    assert counts["capture_failures|code=conversation_not_owned"] == 1
    assert counts["capture_failures|code=focus_message_mismatch"] == 1


# ---------------------------------------------------------------------------
# Constraints, immutability, and deletion
# ---------------------------------------------------------------------------

def _captured_job(env) -> CognitiveShadowJob:
    _, conversation, made = _seed(env, "hello")
    focus, job = made[0]
    assert _capture(env, conversation, focus, job)
    return _job(env, focus.turn_id)


def test_capture_payload_is_immutable_and_only_queue_fields_mutate(shadow_env) -> None:
    row = _captured_job(shadow_env)
    for values in (
        {"capture_payload": {"tampered": True}}, {"capture_digest": "0" * 64},
        {"source_turn_at": datetime(2000, 1, 1)}, {"turn_id": uuid.uuid4()},
    ):
        with shadow_env.db() as db, pytest.raises(DBAPIError, match="immutable"):
            db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(**values))
            db.commit()
    with shadow_env.db() as db:
        db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(
            last_error_code="transient_worker_error", attempts=2,
        ))
        db.commit()
        db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(status="skipped"))
        db.commit()
    with shadow_env.db() as db, pytest.raises(DBAPIError, match="cannot change status"):
        db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(status="pending"))
        db.commit()


def test_job_constraints(shadow_env) -> None:
    row = _captured_job(shadow_env)
    for values, name in (
        ({"status": "processing"}, "processing_lease"),
        ({"status": "exploded"}, "status"),
        ({"attempts": -1}, "attempts"),
    ):
        with shadow_env.db() as db, pytest.raises(IntegrityError, match=name):
            db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(**values))
            db.commit()


def test_one_terminal_record_per_job_and_terminal_constraints(shadow_env) -> None:
    from app.workers import cognitive_shadow as worker

    row = _captured_job(shadow_env)
    assert worker.run_once()
    with shadow_env.db() as db:
        terminal = db.scalar(select(CognitiveShadowTrace))
        assert terminal.job_id == row.id and terminal.terminal_status == "completed"
        duplicate = {c.name: getattr(terminal, c.key) for c in CognitiveShadowTrace.__table__.columns}
        duplicate["id"] = uuid.uuid4()
        with pytest.raises(IntegrityError):
            db.execute(CognitiveShadowTrace.__table__.insert().values(**duplicate))
            db.commit()
        db.rollback()
        for values in ({"semantic_digest": "f" * 64}, {"pipeline_trace_payload": {}}, {"terminal_status": "failed"}):
            with pytest.raises(DBAPIError, match="immutable"):
                db.execute(update(CognitiveShadowTrace).where(CognitiveShadowTrace.id == terminal.id).values(**values))
                db.commit()
            db.rollback()
        bad = {**duplicate, "id": uuid.uuid4(), "job_id": uuid.uuid4(), "turn_id": uuid.uuid4(),
               "failure_payload": {"code": "x"}}
        with pytest.raises(IntegrityError):
            db.execute(CognitiveShadowTrace.__table__.insert().values(**bad))
            db.commit()


def test_conversation_and_account_deletion_remove_shadow_data(shadow_env) -> None:
    from app.workers import cognitive_shadow as worker

    user, conversation, made = _seed(shadow_env, "one", "two")
    for focus, job in made:
        assert _capture(shadow_env, conversation, focus, job)
    assert worker.run_once() and worker.run_once()
    with shadow_env.db() as db:
        other_conversation = create_conversation(db, db.get(User, user.id))
        focus, job = add_turn(db, other_conversation, "elsewhere")
    assert _capture(shadow_env, other_conversation, focus, job)
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowTrace)) == 2
        db.delete(db.get(Conversation, conversation.id))
        db.commit()
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowTrace)) == 0
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 1
        db.delete(db.get(User, user.id))
        db.commit()
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 0


def test_retention_purge_removes_expired_captures_and_clears_predecessors(shadow_env, monkeypatch) -> None:
    from app.workers import cognitive_shadow as worker

    _, conversation, made = _seed(shadow_env, "one")
    assert _capture(shadow_env, conversation, *made[0])
    assert worker.run_once()
    captured_at = _job(shadow_env, made[0][0].turn_id).captured_at

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return captured_at + timedelta(days=2, seconds=1)

    # A second turn captured "later" continues the first one's segment.
    with shadow_env.db() as db:
        focus, job = add_turn(db, db.get(Conversation, conversation.id), "two")
    monkeypatch.setattr(service, "datetime", Later)
    assert _capture(shadow_env, conversation, focus, job)
    assert worker.run_once()
    with shadow_env.db() as db:
        survivor = db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.turn_id == focus.turn_id))
        assert survivor.predecessor_trace_id is not None and survivor.segment_reset_reason is None
        assert service.purge_expired(db, retention_days=3) == 0
        assert service.purge_expired(db, retention_days=2) == 1
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 1
        survivor = db.scalar(select(CognitiveShadowTrace))
        # The foreign key clears the purged predecessor; nothing else changes.
        assert survivor.turn_id == focus.turn_id and survivor.predecessor_trace_id is None
        assert survivor.segment_reset_reason is None


# ---------------------------------------------------------------------------
# Claiming, leases, ordering, and atomicity
# ---------------------------------------------------------------------------

def test_claim_token_lease_and_expired_reclaim(shadow_env) -> None:
    row = _captured_job(shadow_env)
    with shadow_env.db() as db:
        claimed = service.claim_next_job(db, lease_seconds=60)
    assert claimed.id == row.id and claimed.status == "processing" and claimed.attempts == 1
    assert claimed.claim_token and claimed.lease_expires_at > datetime.now(timezone.utc)
    with shadow_env.db() as db:
        assert service.claim_next_job(db, lease_seconds=60) is None
        db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()
    with shadow_env.db() as db:
        reclaimed = service.claim_next_job(db, lease_seconds=60)
    assert reclaimed.id == row.id and reclaimed.attempts == 2
    assert reclaimed.claim_token != claimed.claim_token


def test_stale_worker_cannot_complete_a_reclaimed_job(shadow_env) -> None:
    from app.workers.cognitive_shadow import build_terminal

    row = _captured_job(shadow_env)
    with shadow_env.db() as db:
        stale = service.claim_next_job(db, lease_seconds=60)
        db.execute(update(CognitiveShadowJob).where(CognitiveShadowJob.id == row.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()
    with shadow_env.db() as db:
        fresh = service.claim_next_job(db, lease_seconds=60)
    record = build_terminal(stale, max_attempts=3)
    with shadow_env.db() as db:
        assert service.persist_terminal(db, job=stale, claim_token=stale.claim_token, record=record) is False
        assert db.scalar(select(CognitiveShadowTrace.id)) is None
    with shadow_env.db() as db:
        assert service.persist_terminal(db, job=fresh, claim_token=fresh.claim_token, record=record) is True
        assert db.get(CognitiveShadowJob, row.id).status == "completed"


def test_long_failure_code_and_terminal_trace_are_persisted_together(shadow_env) -> None:
    from app.ai.Cognitive.system.v0.live_shadow.service import shadow_failure, unresolved_record

    row = _captured_job(shadow_env)
    with shadow_env.db() as db:
        job = service.claim_next_job(db, lease_seconds=60)
    assert job.id == row.id
    code = (
        "cognitive_pipeline.response_preparation.response_proposal.two_call.call_two."
        "response_reference_compilation.content_request_ref_outside_current_allowed_requests.proposal_rejected"
    )
    assert len(code) > 128
    record = unresolved_record(
        job_id=str(job.id), turn_id=str(job.turn_id), user_ref=str(job.user_id),
        conversation_ref=str(job.conversation_id), focus_message_id=str(job.user_message_id),
        capture_schema_version=job.capture_schema_version, capture_digest=job.capture_digest,
        started_at=datetime.now(timezone.utc).isoformat(),
        failure=shadow_failure("pipeline.cognitive_turn", code, "The response proposal was rejected.", attempt=job.attempts),
    )
    with shadow_env.db() as db:
        assert service.persist_terminal(db, job=job, claim_token=job.claim_token, record=record)
    with shadow_env.db() as db:
        saved = db.get(CognitiveShadowJob, job.id)
        assert saved.status == "failed"
        assert saved.last_error_code == code
        trace = db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.job_id == job.id))
        assert trace is not None
        assert trace.failure_payload["code"] == code


def test_terminal_insert_and_job_completion_are_atomic(shadow_env, monkeypatch) -> None:
    from app.workers.cognitive_shadow import build_terminal

    row = _captured_job(shadow_env)
    with shadow_env.db() as db:
        job = service.claim_next_job(db, lease_seconds=60)
    record = build_terminal(job, max_attempts=3)
    real_row = service.terminal_row

    def broken_row(record, *, job):
        built = real_row(record, job=job)
        built.capture_digest = "not-a-digest"  # violates a CHECK at flush time
        return built

    monkeypatch.setattr(service, "terminal_row", broken_row)
    with shadow_env.db() as db, pytest.raises(IntegrityError):
        service.persist_terminal(db, job=job, claim_token=job.claim_token, record=record)
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowTrace.id)) is None
        assert db.get(CognitiveShadowJob, row.id).status == "processing"


def test_per_conversation_source_order_and_cross_conversation_concurrency(shadow_env) -> None:
    _, conversation, made = _seed(shadow_env, "first", "second")
    _, other_conversation, other_made = _seed(shadow_env, "other")
    # Capture out of order: the later turn is captured first.
    for focus, job in (made[1], made[0], other_made[0]):
        assert _capture(shadow_env, conversation if focus.conversation_id == conversation.id else other_conversation,
                        focus, job)
    claims: list = []
    barrier = threading.Barrier(3)

    def claim() -> None:
        barrier.wait()
        with shadow_env.db() as db:
            claims.append(service.claim_next_job(db, lease_seconds=60))

    threads = [threading.Thread(target=claim) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    claimed = [job for job in claims if job is not None]
    by_conversation: dict = {}
    for job in claimed:
        by_conversation.setdefault(job.conversation_id, []).append(job)
    # Never two turns of one conversation at once; the earlier source turn first.
    assert all(len(jobs) == 1 for jobs in by_conversation.values())
    assert by_conversation[conversation.id][0].turn_id == made[0][0].turn_id
    assert other_conversation.id in by_conversation  # different conversations run concurrently


def test_postgres_polling_recovers_a_lost_wakeup(shadow_env, monkeypatch) -> None:
    from app.workers import cognitive_shadow as worker

    monkeypatch.setattr(service, "wake_worker", lambda job_id="wake": None)  # notification lost
    row = _captured_job(shadow_env)
    assert worker.run_once()
    with shadow_env.db() as db:
        assert db.get(CognitiveShadowJob, row.id).status == "completed"


def test_migration_upgrade_downgrade_in_an_isolated_database() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the migration round trip")
    name = "shadow_migration_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=backend, check=True, capture_output=True,
            env={**os.environ, "DATABASE_URL": target, "PYTHONPATH": str(backend)},
        )

    def tables() -> set[str]:
        engine = create_engine(target)
        try:
            with engine.connect() as connection:
                return set(connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE tablename LIKE 'cognitive_shadow%' "
                    "UNION SELECT tgname FROM pg_trigger WHERE tgname LIKE 'cognitive_shadow%' "
                    "UNION SELECT proname FROM pg_proc WHERE proname LIKE 'cognitive_shadow%'"
                )).scalars())
        finally:
            engine.dispose()

    try:
        alembic("upgrade", "head")
        expected = {
            "cognitive_shadow_jobs", "cognitive_shadow_traces", "cognitive_shadow_jobs_immutable",
            "cognitive_shadow_traces_immutable", "cognitive_shadow_jobs_guard", "cognitive_shadow_traces_guard",
        }
        assert tables() == expected
        alembic("downgrade", "e1a2b3c4d5f6")
        assert tables() == set()
        alembic("upgrade", "head")
        assert tables() == expected
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
