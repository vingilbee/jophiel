"""Real PostgreSQL leases + scripted executors/sinks. No cognition, teacher or network model calls."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from threading import Event, Thread, Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func

from app.ai.state.background import PreparationResult, SinkReceipt
from app.core.config import get_settings
from app.db.models import CoordinatorWork, CoordinatorOutput, ChatMessage, MemoryManagerState
from app.services import background_components as components, coordinator, memory_manager, observation, observation_memory
from app.services import observation_adapters, chat as chat_service
from app.services.coordinator_runtime import Runtime
from test_coordinator import env, _say, _work  # noqa: F401
from db_support import create_user, create_conversation, database, db_env  # noqa: F401


@pytest.fixture
def bg(env, monkeypatch):
    settings = get_settings().model_copy(update={
        **vars(coordinator.get_settings()), "mind_enabled": False, "memory_manager_enabled": True,
        "observation_enabled": True, "observation_preparation_enabled": True,
        "observation_tick_budget_seconds": 0.5, "observation_log_path": str(env.log),
        "memory_manager_log_path": str(env.log), "redis_url": None})
    for module in (coordinator, components, observation, memory_manager, observation_memory):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
        if hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", env.db)
    return env


def until(check, runtime=None):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if runtime:
            runtime.tick()
        if check():
            return
        time.sleep(.01)
    raise AssertionError("background work did not settle within five seconds")


def status(bg, work_id):
    with bg.db() as db:
        return db.get(CoordinatorWork, work_id).status


def claim(bg, kinds=("mind",), token="first"):
    with bg.db() as db:
        work, ended = coordinator._claim(db, token, kinds)
        db.commit()
        if work:
            db.expunge(work)
    return work, ended


def report(bg, *, status="completed", heard=None):
    with bg.db() as db:
        conversation = db.get(type(bg.conversation), bg.conversation.id)
        message = chat_service.add_message(db, conversation=conversation, role="assistant", content="Recorded text",
                                          turn_id=uuid.uuid4(), status=status)
        message.heard_chars = heard
        db.flush()
        oid = observation.record(db, observation_adapters._message_report(message, bg.user.id))
        db.commit()
    return oid, message.id


def test_slow_mind_does_not_block_other_user_recall_memory_observation_or_deadlines(bg, monkeypatch):
    turn, _ = _say(bg)
    started, release = Event(), Event()
    def slow(work):
        started.set()
        assert release.wait(5)
        return {"kind": "decision", "conclusion": "private"}, "completed", None
    monkeypatch.setattr(coordinator, "_run_mind", slow)
    monkeypatch.setattr(coordinator, "_run_recall", lambda work: ({"refs": []}, "completed", None))
    with bg.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        memory_manager.state(db, bg.user.id)
        recall_id = components.admit(db, kind="recall", key="other-recall", user_id=user.id,
            conversation_id=conversation.id, payload={}, versions={})
        expired = components.admit(db, kind="mind", key="expired", user_id=user.id,
            conversation_id=conversation.id, payload={}, versions={})
        db.flush()
        db.get(CoordinatorWork, expired).deadline_at = coordinator._now() - timedelta(seconds=1)
        db.commit()
    report(bg)
    runtime = Runtime(1)
    try:
        runtime.tick()
        assert started.wait(2)
        def independent_done():
            with bg.db() as db:
                rows = list(db.scalars(select(CoordinatorWork)))
                return (any(w.kind == "memory" and w.status == "completed" for w in rows)
                        and any(w.kind == "observation" and w.status == "blocked" for w in rows)
                        and db.get(CoordinatorWork, recall_id).status == "completed")
        until(independent_done, runtime)
        assert status(bg, expired) == "expired"
        assert status(bg, _work(bg, turn)["mind"].id) == "running"
        coordinator.supersede_older(bg.user.id, bg.conversation.id, uuid.uuid4())
        assert status(bg, _work(bg, turn)["mind"].id) == "superseded"
    finally:
        release.set()
        runtime.close()


def test_expired_and_reclaimed_owners_cannot_write_results(bg):
    turn, _ = _say(bg)
    first, _ = claim(bg)
    with bg.db() as db:
        row = db.get(CoordinatorWork, first.id)
        row.lease_expires_at = coordinator._now() - timedelta(seconds=1)
        db.commit()
    assert not coordinator._accept(first.id, "first", 0, "completed", {"bad": 1}, None)
    second, _ = claim(bg, token="second")
    assert second.id == first.id and second.attempt == 2
    assert not coordinator._accept(first.id, "first", 0, "completed", {"bad": 2}, None)
    assert coordinator._accept(first.id, "second", 0, "completed", {"good": 1}, None)
    assert not coordinator._accept(first.id, "second", 0, "completed", {"bad": 3}, None)
    assert _work(bg, turn)["mind"].result == {"good": 1}
    with bg.db() as db:
        assert db.scalar(select(func.count()).select_from(CoordinatorWork).where(CoordinatorWork.kind == "cognition")) == 1


def test_concurrent_schedulers_serialize_user_component_and_allow_other_users(bg):
    first, _ = _say(bg)
    # Directly queue a second request without superseding the first, same user.
    coordinator.schedule(user_id=bg.user.id, conversation_id=bg.conversation.id, request_id=uuid.uuid4(),
        trigger_message_id=None, with_recall=False, work_input={}, input_versions={})
    gate, claimed = Barrier(2), []
    def contender(token):
        gate.wait()
        work, _ = claim(bg, token=token)
        claimed.append(work.id if work else None)
    threads = [Thread(target=contender, args=(str(i),)) for i in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(3)
    assert len([w for w in claimed if w]) == 1


def test_unchanged_observations_are_processed_once_and_sink_is_durable(bg, monkeypatch):
    oid, mid = report(bg, status="partial", heard=4)
    seen = []
    def prepare(evidence):
        seen.append(evidence)
        return PreparationResult(("prepared:scripted",), "scripted-v1", evidence.source_refs)
    monkeypatch.setattr(components, "prepare_observation", prepare)
    runtime = Runtime(1)
    try:
        def blocked_sink():
            with bg.db() as db:
                return db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.kind == "cognition",
                                                                CoordinatorWork.status == "blocked"))
        until(blocked_sink, runtime)
        for _ in range(5): runtime.tick()
        assert len(seen) == 1
        assert seen[0].delivery["recorded_status"] == "partial"
        assert seen[0].delivery["heard_chars"] == 4
        assert f"message:{mid}" in seen[0].source_refs
        with bg.db() as db:
            assert db.scalar(select(func.count()).select_from(CoordinatorOutput)) == 0
            assert db.scalar(select(CoordinatorWork).where(CoordinatorWork.kind == "observation")).result["output_refs"] == ["prepared:scripted"]
        # Installing a sink is not permission to replay blocked history.
        called = []
        monkeypatch.setattr(components, "cognition_sink", lambda event: called.append(event) or SinkReceipt("consumed"))
        for _ in range(5): runtime.tick()
        assert called == []
    finally:
        runtime.close()


def test_scripted_cognition_receives_typed_metadata_and_cannot_speak(bg, monkeypatch):
    report(bg)
    monkeypatch.setattr(components, "prepare_observation", lambda evidence:
                        PreparationResult(("prepared:1",), "scripted", evidence.source_refs))
    events = []
    monkeypatch.setattr(components, "cognition_sink", lambda event: events.append(event) or SinkReceipt("consumed"))
    runtime = Runtime(1)
    try:
        until(lambda: bool(events), runtime)
        event = events[0]
        assert event.kind == "learning_preparation" and event.user_id == str(bg.user.id)
        assert event.causation_id and event.correlation_id and event.input_versions and event.requires_relevance_check
        with bg.db() as db:
            assert db.scalar(select(func.count()).select_from(CoordinatorOutput)) == 0
    finally:
        runtime.close()


def test_historical_observation_survives_new_input_but_changed_delivery_is_fenced(bg):
    oid, mid = report(bg)
    components.schedule_due()
    work, _ = claim(bg, ("observation",))
    assert coordinator.supersede_older(bg.user.id, bg.conversation.id, uuid.uuid4()) == 0
    assert status(bg, work.id) == "running"
    with bg.db() as db:
        db.get(ChatMessage, mid).heard_chars = 2
        db.commit()
    assert coordinator._accept(work.id, "first", 0, "completed", {"output_refs": ["stale"]}, None)
    assert status(bg, work.id) == "superseded"
    with bg.db() as db:
        assert db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.kind == "cognition")) is None


def test_heartbeat_renews_and_deadline_still_wins(bg):
    _say(bg)
    work, _ = claim(bg)
    with bg.db() as db:
        row = db.get(CoordinatorWork, work.id)
        row.lease_expires_at = coordinator._now() + timedelta(seconds=3)
        db.commit()
    assert coordinator.renew(work.id, "first", 0)
    with bg.db() as db:
        row = db.get(CoordinatorWork, work.id)
        assert row.lease_expires_at > coordinator._now() + timedelta(seconds=30)
        row.deadline_at = coordinator._now() - timedelta(seconds=1)
        db.commit()
    assert coordinator.expire_due() == 1
    assert not coordinator.renew(work.id, "first", 0)
    assert not coordinator._accept(work.id, "first", 0, "completed", {}, None)


def test_retry_identity_stays_stable_and_is_bounded(bg):
    _say(bg)
    ids = []
    for attempt in range(3):
        work, _ = claim(bg)
        ids.append(work.id)
        coordinator._accept(work.id, "first", work.cancellation_generation, "failed", None, "scripted_error")
        if attempt < 2:
            assert claim(bg)[0] is None  # respects retry backoff
            with bg.db() as db:
                db.get(CoordinatorWork, work.id).next_attempt_at = coordinator._now() - timedelta(seconds=1)
                db.commit()
    assert len(set(ids)) == 1 and status(bg, ids[0]) == "failed"
    assert claim(bg)[0] is None


def test_shutdown_fences_running_work_and_restart_recovers(bg, monkeypatch):
    turn, _ = _say(bg)
    entered, release = Event(), Event()
    def slow(work):
        entered.set()
        release.wait(5)
        return {"late": True}, "completed", None
    monkeypatch.setattr(coordinator, "_run_mind", slow)
    runtime = Runtime(1)
    runtime.tick()
    assert entered.wait(2)
    thread = Thread(target=runtime.close)
    thread.start()
    wid = _work(bg, turn)["mind"].id
    until(lambda: status(bg, wid) == "queued")
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    with bg.db() as db:
        row = db.get(CoordinatorWork, wid)
        assert row.result is None and row.cancellation_generation == 1
    work, _ = claim(bg, token="restart")
    assert work.id == wid
    assert coordinator._accept(wid, "restart", 1, "completed", {"fresh": True}, None)


def test_memory_correction_fences_only_dependent_evidence(bg, monkeypatch):
    turn, _ = _say(bg)
    work, _ = claim(bg)
    from app.services import recall
    current = {"fact:a": "old", "fact:unrelated": "new"}
    monkeypatch.setattr(recall, "read", lambda db, user, ref: SimpleNamespace(status="matched", records=[
        SimpleNamespace(fingerprint=current[ref], applicability="current", corrected=False)]))
    evidence = {"evidence_versions": {"fact:a": "old:current:0"}}
    with bg.db() as db:
        assert coordinator.evidence_current(db, work, evidence)
    current["fact:a"] = "corrected"
    assert coordinator._accept(work.id, "first", 0, "completed", evidence, None)
    assert status(bg, work.id) == "superseded"


def test_event_wakes_idle_scheduler_without_heartbeat_delay(monkeypatch):
    from app.workers import runner
    from app.services.coordinator_runtime import wake
    from app.services import coordinator_runtime
    stop, idle, second = Event(), Event(), Event()
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(redis_url=None, memory_worker_poll_seconds=10))
    monkeypatch.setattr(coordinator_runtime, "close", lambda: None)
    calls = []
    def tick():
        calls.append(1)
        if len(calls) == 1:
            idle.set()
        else:
            second.set()
            stop.set()
        return False
    thread = Thread(target=runner._work_loop, args=("coordinator", tick, "q", stop, 10))
    thread.start()
    try:
        assert idle.wait(1)
        wake.set()
        assert second.wait(1), "event waited for the ten-second heartbeat"
    finally:
        stop.set()
        wake.set()
        thread.join(2)


def test_busy_users_do_not_hide_unrelated_ready_work_behind_scan_limit(bg):
    _say(bg)
    active, _ = claim(bg)
    with bg.db() as db:
        for i in range(110):
            components.admit(db, kind="mind", key=f"blocked:{i}", user_id=bg.user.id,
                conversation_id=bg.conversation.id, payload={}, versions={})
        user = create_user(db)
        conversation = create_conversation(db, user)
        ready = components.admit(db, kind="mind", key="ready-other-user", user_id=user.id,
            conversation_id=conversation.id, payload={}, versions={})
        db.commit()
    work, _ = claim(bg, token="another")
    assert work.id == ready


def test_memory_and_preparation_run_with_the_mind_component_unavailable(bg, monkeypatch):
    # No setting disables the mind any more (the pipeline always runs); the component seam stands in for an
    # unavailable mind, to show memory and observation don't depend on it.
    enabled = components.enabled
    monkeypatch.setattr(components, "enabled", lambda kind: kind != "mind" and enabled(kind))
    with bg.db() as db:
        memory_manager.state(db, bg.user.id)
        db.commit()
    report(bg)
    runtime = Runtime(1)
    try:
        def finished():
            views = {v.component: v for v in components.projections(bg.user.id, bg.conversation.id)}
            return views["mind"].status == "disabled" and views["observation"].status == "blocked" and bool(
                views["memory"].last_result_ref)
        until(finished, runtime)
        with bg.db() as db:
            assert db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.kind == "mind")) is None
    finally:
        runtime.close()


def test_correction_during_follow_up_generation_fences_delivery(bg, monkeypatch):
    from app.ai.state.decisions import ResultDecision
    from app.services import cognition_decisions
    from app.workers import chat as worker
    from test_coordinator import _run_chat
    turn, _ = _say(bg)
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda event: ResultDecision("respond", "scripted", True))
    assert coordinator.execute_once()
    current = {"valid": True}
    monkeypatch.setattr(coordinator, "evidence_current", lambda *a, **k: current["valid"])
    async def changed_during_generation(**kwargs):
        yield {"type": "token", "content": "stale generated text"}
        current["valid"] = False
    monkeypatch.setattr(worker, "stream_chat", changed_during_generation)
    assert _run_chat(bg)
    assert bg.announced == []
    with bg.db() as db:
        output = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.request_id == turn,
                                                          CoordinatorOutput.phase == "follow_up"))
        assert output.status == "cancelled"


def test_cognition_queue_backpressure_retains_handoff_without_enqueuing_overflow(bg, monkeypatch):
    from app.ai.state.background import CognitionEvent
    settings = components.get_settings().model_copy(update={"coordinator_max_pending": 1})
    monkeypatch.setattr(components, "get_settings", lambda: settings)
    with bg.db() as db:
        for i in range(2):
            identity = uuid.uuid4()
            event = CognitionEvent(str(identity), str(bg.user.id), None, None, "memory_changed",
                (f"event:{i}",), {}, str(i), str(i), coordinator._now().isoformat())
            components.enqueue_cognition(db, event, identity, f"test:{i}", identity)
        db.commit()
        rows = list(db.scalars(select(CoordinatorWork).order_by(CoordinatorWork.idempotency_key)))
        assert [r.status for r in rows] == ["queued", "blocked"]
        assert rows[1].error_code == "handoff_capacity"
        assert rows[1].input["source_refs"] == ["event:1"]


def test_local_slow_executor_is_not_reclaimed_into_its_own_queue(bg, monkeypatch):
    _say(bg)
    entered, release = Event(), Event()
    def slow(work):
        entered.set()
        release.wait(5)
        return {}, "completed", None
    monkeypatch.setattr(coordinator, "_run_mind", slow)
    runtime = Runtime(2)
    try:
        runtime.tick()
        assert entered.wait(2)
        wid = next(iter(runtime.active["mind"]))
        with bg.db() as db:
            db.get(CoordinatorWork, wid).lease_expires_at = coordinator._now() - timedelta(seconds=1)
            db.commit()
        runtime.tick()
        with bg.db() as db:
            assert db.get(CoordinatorWork, wid).attempt == 1
        assert len(runtime.active["mind"]) == 1
    finally:
        release.set()
        runtime.close()


def test_existing_memory_ingestion_is_projected_without_duplicate_scheduling(bg, monkeypatch):
    from app.db.models import MemoryJob
    settings = components.get_settings().model_copy(update={"long_term_memory_enabled": True})
    monkeypatch.setattr(components, "get_settings", lambda: settings)
    oid, mid = report(bg)
    with bg.db() as db:
        job = MemoryJob(user_id=bg.user.id, conversation_id=bg.conversation.id, assistant_message_id=mid,
                        status="processing", claim_token="owner", lease_expires_at=coordinator._now() + timedelta(seconds=60))
        db.add(job)
        db.commit()
    views = {v.component: v for v in components.projections(bg.user.id, bg.conversation.id)}
    assert views["memory_ingestion"].status == "running"
    assert views["memory_ingestion"].pending_refs == (f"memory_ingestion:{job.id}",)
    assert views["memory_ingestion"].conversation_id == str(bg.conversation.id)
