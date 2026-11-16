"""System probe: the real runner's worker loops on a real database, with thinking she started held open. Her other
conversations, memory upkeep and capture preparation go on meanwhile; once the thinking finishes it wakes her mind in
its own conversation, and what she says there is delivered once. Her mind's runs and the slow thinker are scripted."""
from __future__ import annotations

import time
import uuid
from datetime import timedelta
from threading import Event, Thread

import pytest
from sqlalchemy import func, select

from app.db.models import ChatJob, ChatMessage, CoordinatorOutput, CoordinatorWork, Observation, WorkingItem
from app.services import background_components as components, chat as chat_service, coordinator, coordinator_runtime
from app.services import memory_manager, observation, observation_adapters
from app.services.background_components import PreparationResult
from app.workers import chat as chat_worker, observation as observation_worker, runner
from test_background_coordination import bg  # noqa: F401
from test_coordinator import env, says  # noqa: F401
from db_support import create_conversation, database, db_env  # noqa: F401


def wait_for(check, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("Integrated worker progress did not arrive before timeout")


@pytest.mark.parametrize("adapters", ["scripted_success", "unavailable"])
def test_real_worker_loops_progress_while_her_thinking_is_held_and_it_wakes_her_after(bg, monkeypatch, adapters):
    settings = components.get_settings().model_copy(update={
        "memory_manager_enabled": True, "observation_enabled": True, "observation_preparation_enabled": True,
        "desktop_agent_enabled": False, "whatsapp_enabled": False, "coordinator_executor_slots": 1,
        "coordinator_heartbeat_seconds": 1, "redis_url": None})
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    from app.db import session as database_sessions
    monkeypatch.setattr(database_sessions, "SessionLocal", bg.db)
    monkeypatch.setattr(observation_worker, "SessionLocal", bg.db)
    monkeypatch.setattr(runner.memory, "run_once", lambda: False)  # separate ingestion has no jobs in this fixture
    monkeypatch.setattr(runner.file_deletion, "run_once", lambda: False)
    monkeypatch.setattr(chat_worker.desktop_game, "summary_for", lambda *a, **k: None)
    monkeypatch.setattr(chat_worker.game_correction, "start", lambda *a, **k: None)
    runtime = coordinator_runtime.Runtime(1)
    monkeypatch.setattr(coordinator_runtime, "_instance", runtime)
    started, release, stop = Event(), Event(), Event()

    def slow(work):
        started.set()
        assert release.wait(25), "the harness did not release its scripted thinking"
        return {"kind": "decision", "conclusion": "Private scripted outcome"}, "completed", None

    monkeypatch.setattr(coordinator, "_run_mind", slow)
    if adapters == "scripted_success":
        monkeypatch.setattr(components, "prepare_observation", lambda evidence: PreparationResult(
            output_refs=(f"prepared:{evidence.event_id}",), processing_version="system-test-v1",
            coverage=evidence.source_refs))
    else:
        monkeypatch.setattr(components, "prepare_observation", components.unavailable_preparation)
    with bg.db() as db:
        observation.adapter(db, observation_adapters.CONVERSATION)
        db.commit()

    def enqueue(conversation_id, message):
        with bg.db() as db:
            c = db.get(type(bg.conversation), conversation_id)
            tid = uuid.uuid4()
            m = chat_service.add_message(db, conversation=c, role="user", content=message, turn_id=tid)
            job = chat_service.enqueue_job(db, user_id=bg.user.id, conversation=c, turn_id=tid,
                                           user_message_id=m.id, payload={"content": message})
            db.commit()
            return job.id, tid

    def job_done(job_id):
        with bg.db() as db:
            return db.get(ChatJob, job_id).status == "completed"

    async def start(use, context):
        await use("think_it_over", about="something worth thinking through")
        return "Let me think about that."

    async def wake(use, context):
        assert "Private scripted outcome" in context.wake
        return "Here's where I landed."

    bg.scripts.extend([start, says("Sure, happy to chat."), wake])
    first, turn = enqueue(bg.conversation.id, "A request whose thinking is held open")
    errors = []

    def run():
        try:
            runner.run(stop)
        except BaseException as exc:  # noqa: BLE001
            errors.append(repr(exc))

    thread = Thread(target=run, name="system-supervisor")
    thread.start()
    try:
        assert started.wait(8)
        wait_for(lambda: job_done(first))
        with bg.db() as db:
            other = create_conversation(db, bg.user)
            item = memory_manager.note_commitment(db, bg.user.id, "Synthetic overdue fixture", owner="user",
                                                  due_at=coordinator._now() - timedelta(minutes=1))
            item_id = item.id
            db.commit()
        second, _ = enqueue(other.id, "A normal chat while another conversation is thinking")
        wait_for(lambda: job_done(second))

        def independent_done():
            with bg.db() as db:
                rows = list(db.scalars(select(CoordinatorWork)))
                wanted = "completed" if adapters == "scripted_success" else "blocked"
                return (db.get(WorkingItem, item_id).status == "needs_review"
                        and any(w.kind == "memory" and w.status == "completed" for w in rows)
                        and any(w.kind == "observation" and w.status == wanted for w in rows)
                        and db.scalar(select(func.count()).select_from(Observation)) >= 4)

        wait_for(independent_done)
        with bg.db() as db:
            mind = db.scalar(select(CoordinatorWork).where(CoordinatorWork.request_id == turn,
                                                          CoordinatorWork.kind == "mind"))
            assert mind.status == "running" and not release.is_set()
        release.set()

        def woke_and_said():
            with bg.db() as db:
                output = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.request_id == turn,
                                                                  CoordinatorOutput.phase == "follow_up"))
                return output is not None and output.status == "delivered" and output

        output = wait_for(woke_and_said)
        assert output.decision == "said"
        with bg.db() as db:
            said = [m.content for m in db.scalars(select(ChatMessage).where(
                ChatMessage.conversation_id == bg.conversation.id, ChatMessage.role == "assistant")
                .order_by(ChatMessage.conversation_sequence))]
        assert said == ["Let me think about that.", "Here's where I landed."]  # in its own conversation, once

        def counts():
            with bg.db() as db:
                return (db.scalar(select(func.count()).select_from(Observation)),
                        db.scalar(select(func.count()).select_from(CoordinatorWork).where(
                            CoordinatorWork.kind == "observation")))

        def settled():  # her last reply is observed and prepared once; then heartbeats add nothing
            before = counts()
            time.sleep(1.2)
            return counts() == before

        wait_for(settled, timeout=20)
        before = counts()
        time.sleep(1.2)
        assert counts() == before  # unchanged heartbeats add no source work
    finally:
        release.set()
        stop.set()
        coordinator_runtime.wake.set()
        coordinator_runtime.capture_wake.set()
        thread.join(10)
    assert not thread.is_alive() and not errors, errors
