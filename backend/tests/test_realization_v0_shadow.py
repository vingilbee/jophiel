"""Realization V0 shadow persistence, replay, retention, and isolation on real PostgreSQL (section 19.8).

Set TEST_DATABASE_URL to a disposable PostgreSQL database; each test runs in
its own schema.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.ai.Cognitive.system.v0.contracts.realization import (
    ProviderErrorCode, RealizationContentInventory, RealizationTraceV0, realization_semantic_digest, sha256_json,
)
from app.ai.Cognitive.system.v0.live_shadow import (
    ObservationContext, ReplayStatus, load_capture, observe, replay_realization,
)
from app.ai.Cognitive.system.v0.live_shadow.models import GenerationPath, ShadowSourceChannel
from app.ai.Cognitive.system.v0.live_shadow import realization as live_realization
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0, RealizationExecutionError
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderError
from app.db.models import ChatMessage, CognitiveShadowJob, CognitiveShadowTrace, Conversation, User
from app.services import cognitive_shadow as service
from cognitive_shadow_support import add_turn, create_conversation, create_user, database, shadow_env  # noqa: F401
from realization_v0_support import ScriptedModel
from test_cognitive_shadow_chat_isolation import REPLY, live_turn  # noqa: F401  (fixture)

@pytest.fixture(autouse=True)
def _restore_shared_turn_clock():
    """``add_turn`` advances a module-level clock that other suites' fixed timestamps assume; restore it."""

    import cognitive_shadow_support

    saved = cognitive_shadow_support._clock["now"]
    yield
    cognitive_shadow_support._clock["now"] = saved


GREETING = "Hello there."
CANDIDATE_MARKER = "Hello there, lovely friend"
TIMEOUT = RealizationProviderError(ProviderErrorCode.TIMEOUT)
REALIZATION_COLUMNS = (
    "realization_request_payload", "realization_request_digest", "realization_trace_payload",
    "realization_trace_digest", "realization_semantic_digest", "realization_failure_payload", "realization_timings",
)


def _use_model(monkeypatch, *responses) -> ScriptedModel:
    model = ScriptedModel(*responses)
    monkeypatch.setattr(service, "realization_engine", lambda settings=None: RealizationEngineV0(model))
    return model


def _enable(env, **updates) -> None:
    env.configure(cognitive_shadow_realization_enabled=True, **updates)


def _observe_turn(env, content: str = "hi"):
    """Capture one real turn and let the real shadow worker persist it."""

    from app.workers import cognitive_shadow as worker

    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message, job = add_turn(db, conversation, content)
    assert service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=message.turn_id,
        user_message_id=message.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert worker.run_once()
    with env.db() as db:
        row = db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.turn_id == message.turn_id))
        job_row = db.scalar(select(CognitiveShadowJob).where(CognitiveShadowJob.turn_id == message.turn_id))
    return row, job_row, conversation


def _pure_core(job_row: CognitiveShadowJob):
    """The deterministic observation recomputed from the stored capture alone."""

    capture = load_capture(job_row.capture_payload)
    return observe(capture, None, ObservationContext(
        job_id=str(job_row.id), attempt=1, capture_digest=job_row.capture_digest,
        started_at="2026-09-26T00:00:00+00:00",
    ))


def _assert_core_intact(row: CognitiveShadowTrace, job_row: CognitiveShadowJob) -> None:
    pure = _pure_core(job_row)
    assert row.terminal_status == "completed"
    assert row.compiled_request_payload == pure.compiled_request.model_dump(mode="json")
    assert row.compiled_request_digest == pure.compiled_request_digest
    assert row.semantic_digest == pure.semantic_digest
    # Generated trace ids and timings are the only volatile core fields.
    assert payload_digest(row.pipeline_trace_payload) == row.trace_payload_digest
    assert row.pipeline_trace_payload["action_selection"] == pure.pipeline_trace.model_dump(mode="json")["action_selection"]


# ---------------------------------------------------------------------------
# Disabled realization leaves the deterministic trace unchanged
# ---------------------------------------------------------------------------

def test_disabled_realization_leaves_the_trace_unchanged(shadow_env, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(service, "realization_engine", lambda settings=None: called.append(1))
    row, job_row, _ = _observe_turn(shadow_env)
    assert called == []
    assert row.realization_status == "not_requested"
    assert all(getattr(row, column) is None for column in REALIZATION_COLUMNS)
    _assert_core_intact(row, job_row)
    assert "realization" not in json.dumps(row.versions)


def test_realization_never_enables_live_shadow_by_itself(shadow_env, monkeypatch) -> None:
    from app.workers import cognitive_shadow as worker

    shadow_env.configure(cognitive_live_shadow_enabled=False, cognitive_shadow_realization_enabled=True)
    _use_model(monkeypatch, GREETING)
    with shadow_env.db() as db:
        conversation = create_conversation(db, create_user(db))
        message, job = add_turn(db, conversation, "hi")
    assert not service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=message.turn_id,
        user_message_id=message.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert not worker.run_once()
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowJob)) is None


# ---------------------------------------------------------------------------
# Every terminal realization outcome persists with a valid combination
# ---------------------------------------------------------------------------

def test_accepted_outcome_persists_both_components_atomically(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    model = _use_model(monkeypatch, GREETING)
    row, job_row, _ = _observe_turn(shadow_env)
    assert len(model.payloads) == 1
    assert (row.terminal_status, row.realization_status) == ("completed", "accepted_shadow")
    trace = RealizationTraceV0.model_validate(row.realization_trace_payload)
    assert trace.final_candidate.text == GREETING
    assert row.realization_trace_digest == sha256_json(row.realization_trace_payload)
    assert row.realization_semantic_digest == realization_semantic_digest(trace)
    assert row.realization_request_payload == row.realization_trace_payload["request"]
    assert row.realization_request_digest == trace.request_digest
    assert row.realization_failure_payload is None and row.realization_timings == trace.timings.model_dump(mode="json")
    assert trace.action_result_ref == row.pipeline_trace_payload["action_selection"]["result_id"]
    _assert_core_intact(row, job_row)
    # The core semantic digest never covers model output.
    assert GREETING not in json.dumps(row.pipeline_trace_payload)
    assert service.metrics.snapshot()["counts"]["realization_outcomes|status=accepted_shadow"] == 1


@pytest.mark.parametrize(("responses", "status"), [
    ((f"{GREETING} How are you?", f"{GREETING} Anything new?"), "rejected"),
    ((TIMEOUT, TIMEOUT), "provider_failed"),
])
def test_rejected_and_provider_failed_outcomes_preserve_the_action_trace(shadow_env, monkeypatch, responses,
                                                                         status) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, *responses)
    row, job_row, _ = _observe_turn(shadow_env)
    assert (row.terminal_status, row.realization_status) == ("completed", status)
    trace = RealizationTraceV0.model_validate(row.realization_trace_payload)
    assert len(trace.attempts) == 2
    if status == "rejected":
        assert "unplanned_question" in trace.validation.codes and trace.final_candidate is not None
    else:
        assert trace.final_candidate is None
        assert [item.provider_error_code for item in trace.attempts] == ["provider_timeout", "provider_timeout"]
    _assert_core_intact(row, job_row)


def test_provider_unavailable_without_an_explicit_model(shadow_env) -> None:
    _enable(shadow_env, cognitive_shadow_realization_model=None, openai_api_key="sk-test", chat_model="gpt-4.1")
    row, job_row, _ = _observe_turn(shadow_env)
    assert row.realization_status == "provider_unavailable"
    trace = RealizationTraceV0.model_validate(row.realization_trace_payload)
    assert trace.attempts == () and trace.model_provenance is None and trace.request is not None
    _assert_core_intact(row, job_row)


def test_skipped_outcomes_persist_without_a_request(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    model = _use_model(monkeypatch, GREETING)
    real = live_realization.project_dialogue_structure

    def empty(result):
        inventory = real(result)
        return RealizationContentInventory.model_validate({**inventory.model_dump(mode="json"), "items": []})

    monkeypatch.setattr(live_realization, "project_dialogue_structure", empty)
    row, job_row, _ = _observe_turn(shadow_env, "thanks, got it")
    assert row.realization_status == "skipped_missing_content"
    assert row.realization_request_payload is None and row.realization_request_digest is None
    assert model.payloads == []
    _assert_core_intact(row, job_row)


def test_deterministic_realization_failure_keeps_the_core_trace(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)

    class Exploding(RealizationEngineV0):
        def realize(self, **kwargs):
            raise RealizationExecutionError("request_compilation", "request_contract_invalid")

    monkeypatch.setattr(service, "realization_engine", lambda settings=None: Exploding(None))
    row, job_row, _ = _observe_turn(shadow_env)
    assert row.realization_status == "execution_failed"
    assert row.realization_failure_payload["code"] == "request_contract_invalid"
    assert row.realization_trace_payload is None and row.realization_request_payload is None
    assert row.realization_timings is not None
    _assert_core_intact(row, job_row)


def test_database_rejects_partial_or_invalid_realization_payloads(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, GREETING)
    row, _, _ = _observe_turn(shadow_env)
    with shadow_env.db() as db:
        base = {column.name: getattr(db.get(CognitiveShadowTrace, row.id), column.key)
                for column in CognitiveShadowTrace.__table__.columns}
    invalid = (
        {"realization_status": "accepted_shadow", "realization_trace_payload": None,
         "realization_trace_digest": None, "realization_semantic_digest": None},
        {"realization_status": "not_requested"},
        {"realization_status": "execution_failed", "realization_failure_payload": {"code": "x"}},
        {"realization_request_digest": None},
        {"realization_semantic_digest": None},
        {"realization_trace_digest": "not-a-digest"},
        {"realization_status": "skipped_missing_content"},
        {"realization_status": "invented_status"},
        {"realization_timings": None},
        {"terminal_status": "failed", "pipeline_trace_payload": None, "trace_payload_digest": None,
         "semantic_digest": None, "failure_payload": {"code": "x"}},
    )
    with shadow_env.db() as db:
        for overrides in invalid:
            values = {**base, "id": uuid.uuid4(), "job_id": row.job_id, "turn_id": uuid.uuid4(), **overrides}
            db.execute(delete(CognitiveShadowTrace).where(CognitiveShadowTrace.id == row.id))
            with pytest.raises(IntegrityError, match="violates check constraint"):
                db.execute(CognitiveShadowTrace.__table__.insert().values(**values))
            db.rollback()
        # The unmodified row is itself valid (so each failure above is the named combination).
        db.execute(delete(CognitiveShadowTrace).where(CognitiveShadowTrace.id == row.id))
        db.execute(CognitiveShadowTrace.__table__.insert().values(**{**base, "id": uuid.uuid4()}))
        db.rollback()
    with shadow_env.db() as db:
        for values in ({"realization_status": "rejected"}, {"realization_trace_payload": {}}):
            with pytest.raises(DBAPIError, match="immutable"):
                db.execute(update(CognitiveShadowTrace).where(CognitiveShadowTrace.id == row.id).values(**values))
                db.commit()
            db.rollback()


def test_retries_never_alter_the_captured_input_or_core_trace(shadow_env, monkeypatch) -> None:
    from app.workers import cognitive_shadow as worker

    _enable(shadow_env)
    _use_model(monkeypatch, GREETING)
    real_persist = service.persist_terminal
    failures = []

    def flaky(db, **kwargs):
        if not failures:
            failures.append(1)
            raise RuntimeError("transient database failure")
        return real_persist(db, **kwargs)

    monkeypatch.setattr(service, "persist_terminal", flaky)
    with shadow_env.db() as db:
        conversation = create_conversation(db, create_user(db))
        message, job = add_turn(db, conversation, "hi")
    service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=message.turn_id,
        user_message_id=message.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    with shadow_env.db() as db:
        before = db.scalar(select(CognitiveShadowJob))
        captured = (json.dumps(before.capture_payload, sort_keys=True), before.capture_digest)
    assert worker.run_once() and worker.run_once()
    with shadow_env.db() as db:
        job_row = db.scalar(select(CognitiveShadowJob))
        row = db.scalar(select(CognitiveShadowTrace))
    assert (json.dumps(job_row.capture_payload, sort_keys=True), job_row.capture_digest) == captured
    assert job_row.attempts == 2 and row.realization_status == "accepted_shadow"
    _assert_core_intact(row, job_row)


# ---------------------------------------------------------------------------
# Replay without generation
# ---------------------------------------------------------------------------

class _Forbidden:
    provenance = None

    def generate(self, payload):  # pragma: no cover - reaching this is the failure
        raise AssertionError("replay must never call a model")


@pytest.mark.parametrize("responses", [(GREETING,), (f"{GREETING} How are you?",) * 2, (TIMEOUT, TIMEOUT)])
def test_replay_recompiles_and_revalidates_without_a_provider_call(shadow_env, monkeypatch, responses) -> None:
    _enable(shadow_env, cognitive_shadow_realization_model="fake-realizer-1")
    _use_model(monkeypatch, *responses)
    row, _, conversation = _observe_turn(shadow_env)
    monkeypatch.setattr(service, "realization_engine", lambda settings=None: RealizationEngineV0(_Forbidden()))
    with shadow_env.db() as db:
        report = service.replay_realization_terminal(db, user_id=conversation.user_id, terminal_id=row.id)
        other_user = service.replay_realization_terminal(db, user_id=uuid.uuid4(), terminal_id=row.id)
        core = service.replay_terminal(db, user_id=conversation.user_id, terminal_id=row.id)
    assert (report.status, report.code) == (ReplayStatus.MATCH, "realization_semantic_digest_match")
    assert report.replayed_semantic_digest == row.realization_semantic_digest
    assert other_user.status == ReplayStatus.INVALID_RECORD
    assert core.status == ReplayStatus.MATCH  # the deterministic core replay is unaffected


def test_replay_reports_tampering_and_version_changes_without_rewriting(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, GREETING)
    row, _, _ = _observe_turn(shadow_env)
    common = dict(realization_status=row.realization_status, realization_trace_digest_value=row.realization_trace_digest,
                  realization_semantic_digest_value=row.realization_semantic_digest,
                  pipeline_trace_payload=row.pipeline_trace_payload,
                  compiled_request_payload=row.compiled_request_payload,
                  compiled_request_digest_value=row.compiled_request_digest,
                  language_tag="en", fallback_maximum_characters=1200)
    assert replay_realization(realization_trace_payload=row.realization_trace_payload, **common).status == ReplayStatus.MATCH
    tampered = json.loads(json.dumps(row.realization_trace_payload))
    tampered["final_candidate"]["text"] = "Hello friend."
    assert replay_realization(realization_trace_payload=tampered, **common).status == ReplayStatus.MISMATCH
    newer = json.loads(json.dumps(row.realization_trace_payload))
    newer["versions"]["validator"] = "realization-validator-v0.3"
    report = replay_realization(realization_trace_payload=newer, **common)
    assert (report.status, report.code) == (ReplayStatus.VERSION_UNAVAILABLE, "realization_versions_unavailable")
    report = replay_realization(realization_trace_payload=row.realization_trace_payload, expected_model_id="other-model",
                                **common)
    assert (report.status, report.code) == (ReplayStatus.VERSION_UNAVAILABLE, "realization_model_unavailable")
    # Same model id, different provider semantics (SH-EVAL-007): reported, not replayed as equivalent.
    from app.ai.Cognitive.system.v0.realization_provider import resolve_provenance

    stored = RealizationTraceV0.model_validate(row.realization_trace_payload).model_provenance
    installed = resolve_provenance(model_id="gpt-4.1-mini", timeout_seconds=stored.timeout_seconds)
    report = replay_realization(realization_trace_payload=row.realization_trace_payload,
                                expected_provenance=installed, **common)
    assert (report.status, report.code) == (
        ReplayStatus.VERSION_UNAVAILABLE, "realization_provider_configuration_unavailable",
    )
    assert replay_realization(realization_trace_payload=row.realization_trace_payload, expected_provenance=stored,
                              **common).status == ReplayStatus.MATCH
    assert replay_realization(realization_trace_payload=None, **{**common, "realization_status": "not_requested"}
                              ).status == ReplayStatus.INVALID_RECORD
    with shadow_env.db() as db:
        unchanged = db.get(CognitiveShadowTrace, row.id)
        assert unchanged.realization_trace_payload == row.realization_trace_payload


# ---------------------------------------------------------------------------
# Retention, deletion, privacy, and live-chat isolation
# ---------------------------------------------------------------------------

def test_conversation_and_account_deletion_remove_realization_data(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, GREETING, GREETING)
    first, _, conversation = _observe_turn(shadow_env)
    second, _, other = _observe_turn(shadow_env)
    with shadow_env.db() as db:
        db.execute(delete(Conversation).where(Conversation.id == conversation.id))
        db.execute(delete(User).where(User.id == other.user_id))
        db.commit()
        assert db.scalar(select(CognitiveShadowTrace)) is None
        assert db.scalar(select(CognitiveShadowJob)) is None
    assert first.realization_status == second.realization_status == "accepted_shadow"


def test_retention_purge_removes_realization_data(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, GREETING)
    row, job_row, _ = _observe_turn(shadow_env)
    with shadow_env.db() as db:
        # captured_at is immutable, so the clock moves forward instead of the row moving back.
        assert service.purge_expired(db, retention_days=14) == 0
        assert db.get(CognitiveShadowTrace, row.id) is not None
    monkeypatch.setattr(service, "datetime", type("FrozenClock", (), {
        "now": staticmethod(lambda tz=None: datetime.now(timezone.utc) + timedelta(days=15)),
        "fromisoformat": staticmethod(datetime.fromisoformat),
    }))
    with shadow_env.db() as db:
        assert service.purge_expired(db, retention_days=14) == 1
        assert db.get(CognitiveShadowTrace, row.id) is None
        assert db.get(CognitiveShadowJob, job_row.id) is None


def test_candidate_text_never_appears_in_logs_or_metrics(shadow_env, monkeypatch, caplog) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, f"{CANDIDATE_MARKER}! How are you?", f"{CANDIDATE_MARKER}! How are you?")
    caplog.set_level(logging.DEBUG)
    row, _, _ = _observe_turn(shadow_env)
    assert row.realization_status == "rejected"
    assert CANDIDATE_MARKER not in caplog.text
    assert CANDIDATE_MARKER not in json.dumps(service.metrics.snapshot())


@pytest.mark.parametrize("responses", [(GREETING,), (f"{GREETING} How are you?",) * 2, (TIMEOUT, TIMEOUT)])
def test_realization_cannot_alter_chat_tokens_reply_job_tools_or_memory(shadow_env, live_turn, monkeypatch,
                                                                        responses) -> None:
    from app.workers import cognitive_shadow as worker

    shadow_env.configure(cognitive_live_shadow_enabled=False)
    baseline = live_turn("hi")
    _enable(shadow_env, cognitive_live_shadow_enabled=True)
    _use_model(monkeypatch, *responses)
    first = live_turn("hi")
    assert worker.run_once()
    second = live_turn("hi")
    for run in (first, second):
        assert run["events"] == baseline["events"]
        assert run["assistant"] == baseline["assistant"] == ("".join(REPLY), "completed")
        assert run["job"] == baseline["job"] == ("completed", None)
        assert run["agent_input"][0] == baseline["agent_input"][0]
        assert run["agent_input"][1] == baseline["agent_input"][1]
    rendered = str(second["agent_input"]) + str(second["events"])
    for marker in (GREETING, "realization", "candidate", "accepted_shadow"):
        assert marker not in rendered
    with shadow_env.db() as db:
        row = db.scalar(select(CognitiveShadowTrace))
        assistant_texts = set(db.scalars(select(ChatMessage.content).where(ChatMessage.role == "assistant")))
    assert row.realization_status != "not_requested"
    assert assistant_texts == {"".join(REPLY)}
    # One consolidation per live turn, exactly as without realization.
    assert second["consolidations"] == 3


def test_migration_adds_realization_fields_and_existing_rows_become_not_requested() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the realization migration round trip")
    name = "realization_migration_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, check=True, capture_output=True,
                       env={**os.environ, "DATABASE_URL": target, "PYTHONPATH": str(backend)})

    def columns(engine) -> set[str]:
        with engine.connect() as connection:
            return set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'cognitive_shadow_traces' "
                "AND column_name LIKE 'realization%'")).scalars())

    def checks(engine) -> set[str]:
        with engine.connect() as connection:
            return set(connection.execute(text(
                "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_cognitive_shadow_traces_realization%'"
            )).scalars())

    engine = None
    try:
        alembic("upgrade", "f3a9c2e7b1d4")
        engine = create_engine(target)
        assert columns(engine) == set()
        ids = {key: uuid.uuid4() for key in ("user", "conversation", "message", "turn", "job", "trace")}
        digest = "a" * 64
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, name, email, hashed_password) VALUES (:u, 'n', :e, 'h')"),
                               {"u": ids["user"], "e": f"{ids['user']}@example.com"})
            connection.execute(text("INSERT INTO conversations (id, user_id, updated_at) VALUES (:c, :u, now())"),
                               {"c": ids["conversation"], "u": ids["user"]})
            connection.execute(text(
                "INSERT INTO chat_messages (id, conversation_id, turn_id, role, content) "
                "VALUES (:m, :c, :t, 'user', 'hi')"), {"m": ids["message"], "c": ids["conversation"], "t": ids["turn"]})
            connection.execute(text(
                "INSERT INTO cognitive_shadow_jobs (id, user_id, conversation_id, turn_id, user_message_id, "
                "generation_path, capture_schema_version, capture_payload, capture_digest, sampling_policy_version, "
                "source_channel, source_turn_at, status, captured_at) VALUES (:j, :u, :c, :t, :m, 'local_fallback', "
                "'cognitive-shadow-capture-v0.1', '{}'::jsonb, :d, 's', 'web', now(), 'skipped', now())"),
                {"j": ids["job"], "u": ids["user"], "c": ids["conversation"], "t": ids["turn"], "m": ids["message"],
                 "d": digest})
            connection.execute(text(
                "INSERT INTO cognitive_shadow_traces (id, job_id, user_id, conversation_id, turn_id, segment_id, "
                "segment_reset_reason, terminal_schema_version, terminal_status, skip_reason, capture_digest, "
                "versions, started_at, completed_at) VALUES (:x, :j, :u, :c, :t, :t, 'lineage_unresolved', "
                "'cognitive-shadow-terminal-v0.1', 'skipped', 'capture_schema_unsupported', :d, '{}'::jsonb, now(), now())"),
                {"x": ids["trace"], "j": ids["job"], "u": ids["user"], "c": ids["conversation"], "t": ids["turn"],
                 "d": digest})
        engine.dispose()
        alembic("upgrade", "head")
        engine = create_engine(target)
        assert columns(engine) == {"realization_status", *REALIZATION_COLUMNS}
        assert len(checks(engine)) == 12
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT realization_status, realization_request_payload, realization_trace_payload, "
                "realization_failure_payload, realization_timings FROM cognitive_shadow_traces")).one()
            assert tuple(row) == ("not_requested", None, None, None, None)
        engine.dispose()
        alembic("downgrade", "f3a9c2e7b1d4")
        engine = create_engine(target)
        assert columns(engine) == set() and checks(engine) == set()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM cognitive_shadow_traces")).scalar_one() == 1
        engine.dispose()
        alembic("upgrade", "head")
        engine = create_engine(target)
        assert columns(engine) == {"realization_status", *REALIZATION_COLUMNS}
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_realization_replay_reports_historical_pipeline_versions_unavailable(shadow_env, monkeypatch) -> None:
    _enable(shadow_env)
    _use_model(monkeypatch, GREETING)
    row, _, _ = _observe_turn(shadow_env)
    common = dict(realization_status=row.realization_status, realization_trace_payload=row.realization_trace_payload,
                  realization_trace_digest_value=row.realization_trace_digest,
                  realization_semantic_digest_value=row.realization_semantic_digest,
                  compiled_request_payload=row.compiled_request_payload,
                  compiled_request_digest_value=row.compiled_request_digest,
                  language_tag="en", fallback_maximum_characters=1200)
    for path, value in ((("action", "planner"), "action-planner-v0.1"),
                        (("goal", "goal_lifecycle_policy"), "goal-lifecycle-v0.1-draft"),
                        (("pipeline_runner",), "cognitive-pipeline-runner-v0.2")):
        payload = json.loads(json.dumps(row.pipeline_trace_payload))
        target = payload["versions"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        report = replay_realization(pipeline_trace_payload=payload, **common)
        assert (report.status, report.code) == (ReplayStatus.VERSION_UNAVAILABLE, "pipeline_versions_unavailable")
