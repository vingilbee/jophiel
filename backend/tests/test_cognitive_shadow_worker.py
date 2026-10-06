"""The live-shadow worker on real PostgreSQL with the real canonical pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.ai.Cognitive.system.v0.action_selection_v0 import ActionSelectionError
from app.ai.Cognitive.system.v0.action_selection_v0.engine import ActionSelectionEngine
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineRequest, CognitivePipelineTrace
from app.ai.Cognitive.system.v0.contracts.actions import ActionFailurePhase
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import ModelSemanticGrounder
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticParserError, SemanticParserErrorCode
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest, semantic_digest
from app.ai.Cognitive.system.v0.live_shadow.models import GenerationPath, ShadowSourceChannel
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from app.db.models import (
    CognitiveShadowJob, CognitiveShadowTrace, EpisodicMemory, LearningMemory, MemoryJob, SemanticMemory,
    WorkingMemory,
)
from app.services import cognitive_shadow as service
from app.workers import cognitive_shadow as worker
from cognitive_shadow_support import (  # noqa: F401  (fixtures)
    add_turn, create_conversation, create_user, database, shadow_env,
)

# Captured before the autouse fixture below replaces it for each test.
_REAL_COGNITIVE_PIPELINE = service.cognitive_pipeline


@pytest.fixture(autouse=True)
def _offline_semantic_grounder(monkeypatch):
    """Worker persistence tests never make a provider/network call.

    Model-boundary behavior is covered by test_semantic_model_grounding; this
    file keeps the historical rule grounder solely as an offline pipeline
    double while exercising PostgreSQL queue, lineage, and persistence logic.
    """

    monkeypatch.setattr(service, "cognitive_pipeline", lambda turn_id, _settings=None: build_pipeline(turn_id))


def _seed_and_capture(env, *contents: str):
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        made = [add_turn(db, conversation, content, reply=f"reply {index}") for index, content in enumerate(contents)]
    for focus, job in made:
        assert service.capture_turn(
            user_id=user.id, conversation_id=conversation.id, turn_id=focus.turn_id, user_message_id=focus.id,
            chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER, source_channel=ShadowSourceChannel.WEB,
        )
    return user, conversation, made


def _terminal(env, turn_id) -> CognitiveShadowTrace:
    with env.db() as db:
        return db.scalar(select(CognitiveShadowTrace).where(CognitiveShadowTrace.turn_id == turn_id))


def test_real_pipeline_completes_a_continuous_segment(shadow_env) -> None:
    _, conversation, made = _seed_and_capture(shadow_env, "hi there", "How much protein is in two eggs?")
    assert worker.run_once() and worker.run_once()
    assert not worker.run_once()
    first, second = (_terminal(shadow_env, focus.turn_id) for focus, _ in made)
    for row in (first, second):
        assert row.terminal_status == "completed"
        request = CognitivePipelineRequest.model_validate(row.compiled_request_payload)
        trace = CognitivePipelineTrace.model_validate(row.pipeline_trace_payload)
        assert payload_digest(row.compiled_request_payload) == row.compiled_request_digest
        assert payload_digest(row.pipeline_trace_payload) == row.trace_payload_digest
        assert semantic_digest(row.pipeline_trace_payload) == row.semantic_digest
        assert trace.cognitive_turn_id == request.cognitive_turn_id == str(row.turn_id)
        assert trace.conversation_ref == str(conversation.id)
        assert row.versions["pipeline"] == trace.versions.model_dump(mode="json")
        assert trace.action_selection.plan.moves and trace.action_selection.shadow_only
    assert first.segment_reset_reason == "initial_conversation" and first.predecessor_trace_id is None
    assert second.segment_reset_reason is None
    assert second.predecessor_trace_id == first.id and second.segment_id == first.segment_id
    first_trace = CognitivePipelineTrace.model_validate(first.pipeline_trace_payload)
    second_request = CognitivePipelineRequest.model_validate(second.compiled_request_payload)
    assert second_request.previous_state == first_trace.cognitive_turn.dynamic_state_update.snapshot
    assert second_request.previous_goal_snapshot == first_trace.goal_snapshot
    with shadow_env.db() as db:
        statuses = set(db.scalars(select(CognitiveShadowJob.status)))
    assert statuses == {"completed"}
    counts = service.metrics.snapshot()["counts"]
    assert counts["terminal_records|code=ok|status=completed"] == 2
    assert any(key.startswith("selected_action_keys|") for key in counts)


def test_chained_turns_past_the_goal_focus_boundary_stay_one_continuous_segment(shadow_env) -> None:
    # Fixed identities make the cognitive goal ids reproducible.  Before the
    # SH-EVAL-001 turn rollover these turns accumulated earlier turns' response
    # obligations as active non-focal goals; they must now stay one continuous
    # segment while each turn keeps only its own obligation active.
    namespace = uuid.UUID("00000000-0000-0000-0000-0000005ead0e")

    def fixed(*parts: str) -> uuid.UUID:
        return uuid.uuid5(namespace, "|".join(parts))

    contents = (
        "How much protein is in two eggs?", "How many calories are in a banana?", "How much fat is in avocado?",
        "How many carbs are in rice?", "How much fiber is in oats?",
    )
    with shadow_env.db() as db:
        user = create_user(db, user_id=fixed("user"))
        conversation = create_conversation(db, user, conversation_id=fixed("conversation"))
        made = [
            add_turn(
                db, conversation, content, reply=f"reply {index}", turn_id=fixed("turn", str(index)),
                message_id=fixed("message", str(index)), job_id=fixed("job", str(index)),
                reply_id=fixed("reply", str(index)),
            )
            for index, content in enumerate(contents)
        ]
    for focus, job in made:
        assert service.capture_turn(
            user_id=user.id, conversation_id=conversation.id, turn_id=focus.turn_id, user_message_id=focus.id,
            chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER, source_channel=ShadowSourceChannel.WEB,
        )
    for _ in contents:
        assert worker.run_once()
    assert not worker.run_once()

    rows = [_terminal(shadow_env, focus.turn_id) for focus, _ in made]
    assert [row.terminal_status for row in rows] == ["completed"] * len(contents)
    assert rows[0].segment_reset_reason == "initial_conversation" and rows[0].predecessor_trace_id is None
    for index, (previous, row) in enumerate(zip(rows, rows[1:]), start=1):
        assert row.segment_reset_reason is None and not row.missing_predecessor_refs
        assert row.predecessor_trace_id == previous.id and row.segment_id == rows[0].segment_id
        prior = CognitivePipelineTrace.model_validate(previous.pipeline_trace_payload)
        request = CognitivePipelineRequest.model_validate(row.compiled_request_payload)
        goals = CognitivePipelineTrace.model_validate(row.pipeline_trace_payload).goal_snapshot
        assert request.previous_goal_snapshot == prior.goal_snapshot
        assert request.goal_turn_rollover is not None
        assert request.goal_turn_rollover.closed_turn_ref == str(previous.turn_id)
        assert goals.supersedes_goal_snapshot_id == prior.goal_snapshot.goal_snapshot_id
        assert goals.revision == prior.goal_snapshot.revision + 1
        non_focal = goals.focus_decision.non_focal_active_goal_refs
        assert non_focal == tuple(ref for ref in goals.active_goal_refs if ref not in goals.focal_goal_refs)
        obligations = [
            item for item in goals.records
            if item.definition_ref.definition_key == "interaction.discharge_response_obligation"
        ]
        assert [item.scope_ref for item in obligations if item.status == "active"] == [str(row.turn_id)]
        assert [item.status for item in obligations if item.scope_ref != str(row.turn_id)] == ["expired"] * index
        floor = goals.focus_decision.coordination_floor_goal_ref
        assert floor == next(item.goal_id for item in obligations if item.status == "active")


def test_missing_personality_is_a_durable_skip_and_later_turns_record_the_gap(shadow_env) -> None:
    from sqlalchemy import update as sql_update
    from app.db.models import CoreMemory

    with shadow_env.db() as db:  # malformed core identity: runtime mapping fails closed
        db.execute(sql_update(CoreMemory).values(drives={"curiosity": 0.88}))
        db.commit()
    _, conversation, made = _seed_and_capture(shadow_env, "first")
    assert worker.run_once()
    skipped = _terminal(shadow_env, made[0][0].turn_id)
    assert skipped.terminal_status == "skipped" and skipped.skip_reason == "missing_personality_projection"
    assert skipped.compiled_request_payload is None and skipped.pipeline_trace_payload is None
    with shadow_env.db() as db:  # repaired, and re-versioned as the edit rule requires
        db.execute(sql_update(CoreMemory).values(drives={"curiosity": 0.88, "affiliation": 0.75}, version=2))
        db.commit()
    with shadow_env.db() as db:
        from app.db.models import Conversation
        focus, job = add_turn(db, db.get(Conversation, conversation.id), "second")
    assert service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=focus.turn_id,
        user_message_id=focus.id, chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert worker.run_once()
    gap = _terminal(shadow_env, focus.turn_id)
    assert gap.terminal_status == "completed"
    assert gap.segment_reset_reason == "predecessor_gap" and gap.predecessor_trace_id is None
    assert gap.missing_predecessor_refs == [f"cognitive-shadow-trace:{skipped.id}"]
    assert gap.segment_id != skipped.segment_id


def test_nested_action_failure_phase_is_retained_without_a_partial_trace(shadow_env, monkeypatch) -> None:
    def fail(self, request):
        raise ActionSelectionError(
            cognitive_turn_id=request.cognitive_turn_id, failed_phase=ActionFailurePhase.PLAN_VALIDATION,
            code="no_viable_plan",
        )

    monkeypatch.setattr(ActionSelectionEngine, "select", fail)
    _, _, made = _seed_and_capture(shadow_env, "hello")
    assert worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "failed"
    assert row.pipeline_trace_payload is None and row.semantic_digest is None
    assert row.compiled_request_payload is not None
    failure = row.failure_payload
    assert failure["stage"] == "pipeline.cognitive_turn"
    assert failure["nested_stage"] == "action_selection" and failure["nested_phase"] == "plan_validation"
    assert failure["code"] == "pipeline.cognitive_turn.action_selection.plan_validation"
    assert failure["retryable"] is False and failure["attempt_count"] == 1
    assert "hello" not in str(failure)
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowJob.status)) == "failed"


def test_transient_failures_retry_the_immutable_capture_then_complete(shadow_env, monkeypatch) -> None:
    _, _, made = _seed_and_capture(shadow_env, "hello")
    real = service.load_predecessor
    calls = {"count": 0}

    def flaky(db, job):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))
        return real(db, job)

    monkeypatch.setattr(service, "load_predecessor", flaky)
    with shadow_env.db() as db:
        before = db.scalar(select(CognitiveShadowJob.capture_payload))
    assert worker.run_once()
    with shadow_env.db() as db:
        job = db.scalar(select(CognitiveShadowJob))
        assert (job.status, job.attempts, job.last_error_code) == ("pending", 1, "transient_worker_error")
        assert job.capture_payload == before and job.claim_token is None
    assert worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "completed"
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowJob.attempts)) == 2


def test_exhausted_attempts_record_one_terminal_failure(shadow_env, monkeypatch) -> None:
    shadow_env.configure(cognitive_live_shadow_max_attempts=2)
    _, _, made = _seed_and_capture(shadow_env, "hello")

    def broken(db, job):
        raise OperationalError("SELECT 1", {}, Exception("database down"))

    monkeypatch.setattr(service, "load_predecessor", broken)
    assert worker.run_once() and worker.run_once()
    assert not worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "failed"
    assert row.failure_payload["code"] == "attempts_exhausted" and row.failure_payload["retryable"] is False
    assert row.segment_reset_reason == "lineage_unresolved"
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowTrace)) == 1
        assert db.scalar(select(CognitiveShadowJob.status)) == "failed"


class _FailingParser:
    version = "failing-parser-double"
    provenance = None

    def __init__(self, code: SemanticParserErrorCode) -> None:
        self.code = code
        self.calls = 0

    def generate(self, payload):
        self.calls += 1
        raise SemanticParserError(self.code)


def _install_parser(monkeypatch, model) -> None:
    def factory(turn_id, _settings=None):
        pipeline = build_pipeline(turn_id)
        pipeline.interpretation_engine = InterpretationEngine(grounder=ModelSemanticGrounder(model))
        return pipeline

    monkeypatch.setattr(service, "cognitive_pipeline", factory)


_PARSER_CODE = "pipeline.interpretation.semantic_grounding.semantic_parser."


def test_transient_parser_failure_retries_with_its_typed_code_then_records_it(shadow_env, monkeypatch) -> None:
    shadow_env.configure(cognitive_live_shadow_max_attempts=2)
    model = _FailingParser(SemanticParserErrorCode.TIMEOUT)
    _install_parser(monkeypatch, model)
    _, _, made = _seed_and_capture(shadow_env, "What is today's date?")
    assert worker.run_once()
    with shadow_env.db() as db:
        job = db.scalar(select(CognitiveShadowJob))
        assert (job.status, job.attempts, job.last_error_code) == ("pending", 1, _PARSER_CODE + "timeout")
    assert _terminal(shadow_env, made[0][0].turn_id) is None
    assert worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "failed" and row.pipeline_trace_payload is None
    assert row.failure_payload["code"] == _PARSER_CODE + "timeout"
    assert row.failure_payload["retryable"] is False and row.failure_payload["attempt_count"] == 2
    assert "today" not in str(row.failure_payload)
    assert model.calls == 2


def test_rejected_parser_output_is_recorded_once_without_retrying(shadow_env, monkeypatch) -> None:
    model = _FailingParser(SemanticParserErrorCode.SCHEMA_INVALID)
    _install_parser(monkeypatch, model)
    _, _, made = _seed_and_capture(shadow_env, "What is today's date?")
    assert worker.run_once()
    assert not worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "failed" and row.pipeline_trace_payload is None
    assert row.compiled_request_payload is not None
    assert row.failure_payload["code"] == _PARSER_CODE + "schema_invalid"
    assert row.failure_payload["retryable"] is False and row.failure_payload["attempt_count"] == 1
    assert model.calls == 1
    with shadow_env.db() as db:
        assert db.scalar(select(CognitiveShadowJob.status)) == "failed"


def test_unconfigured_parser_is_a_typed_failure_not_a_retry_loop(shadow_env, monkeypatch) -> None:
    shadow_env.configure(openai_api_key=None)
    monkeypatch.setattr(service, "cognitive_pipeline", _REAL_COGNITIVE_PIPELINE)
    _, _, made = _seed_and_capture(shadow_env, "hello")
    assert worker.run_once()
    row = _terminal(shadow_env, made[0][0].turn_id)
    assert row.terminal_status == "failed"
    assert row.failure_payload["code"] == _PARSER_CODE + "model_not_configured"
    assert row.failure_payload["retryable"] is False
    assert worker.check_semantic_parser_configuration(shadow_env.configure()) == "model_not_configured"


def test_unsupported_capture_schema_is_a_typed_skip(shadow_env) -> None:
    with shadow_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        focus, _ = add_turn(db, conversation, "hello")
        payload = {"schema_version": "cognitive-shadow-capture-v9"}
        from app.ai.Cognitive.system.v0.live_shadow.models import capture_id_for
        db.add(CognitiveShadowJob(
            id=uuid.UUID(capture_id_for(str(focus.turn_id))), user_id=user.id, conversation_id=conversation.id,
            turn_id=focus.turn_id, user_message_id=focus.id, generation_path="local_fallback",
            capture_schema_version="cognitive-shadow-capture-v9", capture_payload=payload,
            capture_digest=payload_digest(payload), sampling_policy_version="shadow-sampling-v0.1",
            source_channel="web", source_turn_at=focus.created_at, captured_at=datetime.now(timezone.utc),
        ))
        db.commit()
    assert worker.run_once()
    row = _terminal(shadow_env, focus.turn_id)
    assert row.terminal_status == "skipped" and row.skip_reason == "capture_schema_unsupported"
    assert row.segment_reset_reason == "lineage_unresolved"


def test_kill_switch_stops_claiming_and_retains_pending_jobs(shadow_env) -> None:
    _seed_and_capture(shadow_env, "hello")
    shadow_env.configure(cognitive_live_shadow_enabled=False)
    assert not worker.run_once()
    with shadow_env.db() as db:
        job = db.scalar(select(CognitiveShadowJob))
        assert job.status == "pending" and job.attempts == 0
    shadow_env.configure(cognitive_live_shadow_enabled=True)
    assert worker.run_once()


def test_worker_never_writes_memory_or_learning_state(shadow_env) -> None:
    _seed_and_capture(shadow_env, "hi", "I love tofu, remember that")
    assert worker.run_once() and worker.run_once()
    with shadow_env.db() as db:
        for model in (WorkingMemory, SemanticMemory, EpisodicMemory, LearningMemory, MemoryJob):
            assert db.scalar(select(text("count(*)")).select_from(model)) == 0, model.__tablename__


@pytest.mark.parametrize("enabled, shared, expected", ((True, True, True), (True, False, False), (False, True, False)))
def test_runner_starts_a_separate_shadow_thread_only_when_configured(monkeypatch, enabled, shared, expected) -> None:
    import threading
    from types import SimpleNamespace

    from app.workers import runner

    config = SimpleNamespace(
        redis_url=None, memory_worker_poll_seconds=1, memory_queue_name="q:m", file_deletion_queue_name="q:d",
        cognitive_live_shadow_enabled=enabled, cognitive_live_shadow_run_in_shared_worker=shared,
        cognitive_live_shadow_queue_name="q:s", cognitive_live_shadow_worker_poll_seconds=1,
    )
    monkeypatch.setattr(runner, "get_settings", lambda: config)
    started: list[str] = []
    stop = threading.Event()
    for name in ("chat", "memory", "file_deletion"):
        monkeypatch.setattr(getattr(runner, name), "run_once", lambda: False)

    def shadow_once() -> bool:
        started.append(threading.current_thread().name)
        stop.set()
        return False

    monkeypatch.setattr(runner.cognitive_shadow, "run_once", shadow_once)
    supervisor = threading.Thread(target=runner.run, args=(stop,))
    supervisor.start()
    if not expected:
        stop.wait(1.5)
        stop.set()
    supervisor.join(10)
    assert not supervisor.is_alive()
    assert (started == ["worker-cognitive-shadow"]) is expected


def test_worker_maintenance_purges_past_the_configured_retention_on_an_hourly_cadence(
    shadow_env, monkeypatch, caplog,
) -> None:
    """The local-canary policy: 14 days, enforced by the existing worker's hourly
    maintenance (first pass after startup included), with frozen clocks only."""

    import logging
    import time
    from datetime import timedelta
    from types import SimpleNamespace

    from app.db.models import Conversation

    shadow_env.configure(cognitive_live_shadow_retention_days=14)
    start = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    wall = {"now": start}

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall["now"]

    monkeypatch.setattr(service, "datetime", Frozen)
    # A freshly booted host (small monotonic value) must still purge on its first pass.
    mono = {"now": 10.0}
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: mono["now"], perf_counter=time.perf_counter))
    monkeypatch.setattr(worker, "_last_purge", None)
    monkeypatch.setattr(worker, "_last_stats", None)
    purges: list[int] = []
    real_purge = service.purge_expired

    def spy(db, *, retention_days):
        purges.append(retention_days)
        return real_purge(db, retention_days=retention_days)

    monkeypatch.setattr(service, "purge_expired", spy)
    caplog.set_level(logging.DEBUG)

    old_text, new_text = "canary-old-marker kiwi smoothie", "canary-new-marker lentil soup"
    _, conversation, made = _seed_and_capture(shadow_env, old_text)
    assert worker.run_once()  # first pass after startup: eligible, nothing expired yet
    assert purges == [14]
    old_focus = made[0][0]

    wall["now"] = start + timedelta(days=10)
    mono["now"] = 20.0
    with shadow_env.db() as db:
        new_focus, new_job = add_turn(db, db.get(Conversation, conversation.id), new_text, reply="later reply")
    assert service.capture_turn(
        user_id=conversation.user_id, conversation_id=conversation.id, turn_id=new_focus.turn_id,
        user_message_id=new_focus.id, chat_job_id=new_job.id, generation_path=GenerationPath.CHAT_WORKER,
        source_channel=ShadowSourceChannel.WEB,
    )
    assert worker.run_once()
    assert purges == [14]  # inside the hour: no second purge
    old_trace = _terminal(shadow_env, old_focus.turn_id)
    survivor = _terminal(shadow_env, new_focus.turn_id)
    assert survivor.predecessor_trace_id == old_trace.id

    # Past the cutoff but still inside the cadence window: nothing is purged.
    wall["now"] = start + timedelta(days=14, seconds=1)
    mono["now"] = 10.0 + 3599
    assert not worker.run_once()
    assert purges == [14]
    with shadow_env.db() as db:
        assert db.scalar(select(text("count(*)")).select_from(CognitiveShadowJob)) == 2

    # Eligible pass one second before the boundary: runs, deletes nothing.
    wall["now"] = start + timedelta(days=14, seconds=-1)
    mono["now"] = 10.0 + 3600
    assert not worker.run_once()
    assert purges == [14, 14]
    assert "retention_purged_jobs" not in service.metrics.snapshot()["counts"]

    # A later eligible pass after the boundary deletes only the expired capture.
    wall["now"] = start + timedelta(days=14, seconds=1)
    mono["now"] = 10.0 + 7200
    assert not worker.run_once()
    assert purges == [14, 14, 14]
    with shadow_env.db() as db:
        jobs = list(db.scalars(select(CognitiveShadowJob)))
        traces = list(db.scalars(select(CognitiveShadowTrace)))
    assert [job.turn_id for job in jobs] == [new_focus.turn_id]
    # The job's terminal trace went with it by cascade; the survivor keeps
    # everything except the nullable link to its purged predecessor.
    assert [trace.id for trace in traces] == [survivor.id]
    kept = traces[0]
    assert kept.predecessor_trace_id is None
    assert (kept.terminal_status, kept.segment_id, kept.semantic_digest) == (
        survivor.terminal_status, survivor.segment_id, survivor.semantic_digest,
    )
    assert kept.pipeline_trace_payload == survivor.pipeline_trace_payload
    assert service.metrics.snapshot()["counts"]["retention_purged_jobs"] == 1

    assert "retention purged 1 captures" in caplog.text
    for secret in (old_text, new_text, "later reply", "reply 0", survivor.semantic_digest):
        assert secret not in caplog.text


def test_worker_maintenance_never_purges_without_a_retention_value(shadow_env, monkeypatch) -> None:
    import time
    from types import SimpleNamespace

    _seed_and_capture(shadow_env, "hello")
    shadow_env.configure(cognitive_live_shadow_retention_days=None)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 10.0, perf_counter=time.perf_counter))
    monkeypatch.setattr(worker, "_last_purge", None)
    calls: list[int] = []
    monkeypatch.setattr(service, "purge_expired", lambda db, *, retention_days: calls.append(retention_days) or 0)
    assert worker.run_once()
    assert calls == []
