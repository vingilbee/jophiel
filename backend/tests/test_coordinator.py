"""Background thinking (docs/SHARED_STATE_STEP4_SPEC.md): a request that needs thinking commits work and the mouth
replies without waiting; the mind waits for its recall; the result goes through cognition before any follow-up;
a newer message supersedes it; retries never repeat a phase. Real PostgreSQL (``TEST_DATABASE_URL``). Jev, the
thinker, memory search and both cognition decisions are scripted: no model calls."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.mind.hard import HardThought
from app.ai.state.decisions import PENDING_GAP, RESULT_GAP, PendingDecision, ResultDecision
from app.db.models import ChatJob, ChatMessage, CoordinatorOutput, CoordinatorWork
from app.services import chat as chat_service
from app.services import cognition_decisions, cognitive_live, coordinator, jev_assessment, recall_references
from app.services import shared_state
from app.workers import chat as chat_worker
from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: F401

REPLY = ("Let me think ", "about that.")


@pytest.fixture
def env(shadow_env, monkeypatch, tmp_path):
    shadow_env.configure(cognitive_live_shadow_enabled=False)
    settings = SimpleNamespace(
        shared_state_pipeline_enabled=True, shared_state_log_path=str(tmp_path / "shared.jsonl"),
        cognitive_live_student="on", cognitive_live_character=False, cognitive_live_student_dir=None,
        recall_enabled=True, mind_enabled=False, mind_inner_state_enabled=False, memory_manager_enabled=False,
        shared_state_assessment_enabled=True, typesafe_api_key="k", typesafe_base_url="https://jev.test/v1",
        shared_state_assessment_model="jev-latest", shared_state_assessment_timeout_seconds=1.5,
        shared_state_work_deadline_seconds=180, coordinator_queue_name="q", redis_url=None,
        recall_tool_calls_per_reply=3)
    for module in (shared_state, coordinator, jev_assessment):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (shared_state, coordinator, chat_worker):
        monkeypatch.setattr(module, "SessionLocal", shadow_env.db)
    shared_state._personality.cache_clear()
    monkeypatch.setattr(recall_references, "enabled", lambda: True)
    empty = SimpleNamespace(result=SimpleNamespace(status="not_searched", records=[], coverage={}, refs=lambda: []),
                            now=None, rendered="Memories from your last answer: none.", legacy=None, tool_calls=0,
                            supplied={})
    monkeypatch.setattr(recall_references, "cached_for_reply", lambda *a, **k: empty)
    searched = SimpleNamespace(result=SimpleNamespace(status="no_match", records=[], coverage={}, refs=lambda: []),
                               rendered="Memory search ran and found nothing that matches.", tool_calls=0, supplied={})
    calls = {"search": 0, "think": [], "jev": ("not_required", "required")}

    def search(*a, **k):
        calls["search"] += 1
        return searched

    monkeypatch.setattr(recall_references, "for_reply", search)
    monkeypatch.setattr(recall_references, "save_receipt", lambda *a, **k: None)
    monkeypatch.setattr(cognitive_live, "enabled", lambda: True)
    monkeypatch.setattr(cognitive_live, "after_reply", lambda *a, **k: None)
    monkeypatch.setattr(cognitive_live, "prepare", lambda **k: cognitive_live.LivePrep(arm="reading", interpretation=dict(
        acts={"asking": 0.9}, tone={"friendly": 0.9}, request={"talk_only": 0.9},
        understand={"acts": {"asking": True}, "tone": "friendly", "request": "talk_only"})))
    real_assess = jev_assessment.assess

    def jev(**k):
        recall, thinking = calls["jev"]
        return real_assess(**k, post=lambda url, body: {"model": "jev-test", "answers": {
            "recall": {"choice": recall}, "thinking": {"choice": thinking}}})

    monkeypatch.setattr(jev_assessment, "assess", jev)

    def think(inp):
        calls["think"].append(inp)
        return HardThought("decision", "Stay in Berlin for a year, then decide.", thinking="private reasoning")

    monkeypatch.setattr(coordinator, "_think", think)
    # Production cognition: the student, which can't decide either (unsupported). Tests swap in scripts.
    monkeypatch.setattr(cognition_decisions, "PROVIDERS", dict(cognition_decisions.PROVIDERS))
    published, announced, inputs = [], [], []

    async def fake_stream_chat(*, messages, context, **_):
        inputs.append((messages, context))
        for token in REPLY:
            yield {"type": "token", "content": token}

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker, "_announce", lambda job, reply, mid: announced.append((job.turn_id, reply)))
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", lambda *a, **k: None)
    with shadow_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    return SimpleNamespace(db=shadow_env.db, user=user, conversation=conversation, calls=calls, published=published,
                           announced=announced, inputs=inputs, log=tmp_path / "shared.jsonl")


def _say(env, content="Should I take the Berlin job? Think it over."):
    """A user message and its chat job, run through the real chat worker."""
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        turn_id = uuid.uuid4()
        message = chat_service.add_message(db, conversation=conversation, role="user", content=content,
                                           turn_id=turn_id)
        chat_service.enqueue_job(db, user_id=env.user.id, conversation=conversation, turn_id=turn_id,
                                 user_message_id=message.id, payload={"content": content}, commit=False)
        db.commit()
    _run_chat(env)
    return turn_id, message.id


def _run_chat(env) -> bool:
    with env.db() as db:
        job = chat_service.claim_next_job(db, lease_seconds=60)
    if job is None:
        return False
    asyncio.run(chat_worker._generate(job))
    return True


def _work(env, request_id):
    with env.db() as db:
        return {w.kind: w for w in db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id == request_id))}


def _outputs(env, request_id):
    with env.db() as db:
        return {o.phase: o for o in db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.request_id == request_id))}


def _assistant(env, turn_id):
    with env.db() as db:
        return db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id, ChatMessage.role == "assistant"))


def _respond(inp):
    return ResultDecision("respond", "stub", True)


# --- immediate phase ------------------------------------------------------------------------------------


def test_thinking_commits_work_and_the_mouth_replies_without_waiting(env):
    turn, message = _say(env)
    work = _work(env, turn)
    assert set(work) == {"mind"} and work["mind"].status == "queued" and env.calls["think"] == []
    assert _assistant(env, turn).content == "".join(REPLY)  # replied before the mind ran at all
    _, context = env.inputs[-1]
    assert "working this out in the background (thinking it through: queued, not started yet)" in context.turn_boundary
    immediate = _outputs(env, turn)["immediate"]
    # The student can't decide acknowledge/silence: a labelled fallback, the gap recorded.
    assert (immediate.decision, immediate.decision_origin, immediate.status) == ("respond", "policy_fallback", "delivered")
    assert immediate.missing_capability == PENDING_GAP
    route = next(e for e in env.published if e.get("type") == "route")
    assert route["route"] == "mind" and route["degraded"] == []


def test_cognition_choosing_silence_sends_nothing_and_keeps_the_work(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "pending",
                        lambda inp: PendingDecision("silent", "stub", True))
    turn, _ = _say(env)
    assert _assistant(env, turn) is None and env.inputs == []  # the mouth never ran
    assert _outputs(env, turn)["immediate"].status == "not_sent"
    assert _work(env, turn)["mind"].status == "queued"
    assert env.published[-1] == {"type": "done", "turn_id": str(turn), "status": "silent"}


# --- the mind and result cognition ----------------------------------------------------------------------


def test_a_finished_thought_is_blocked_while_cognition_cannot_judge_it(env):
    turn, _ = _say(env)
    assert coordinator.run_once()
    work = _work(env, turn)["mind"]
    assert work.status == "completed" and work.result["conclusion"].startswith("Stay in Berlin")
    follow = _outputs(env, turn)["follow_up"]
    assert (follow.decision, follow.status, follow.missing_capability) == ("blocked", "not_sent", RESULT_GAP)
    with env.db() as db:  # nothing queued to speak, and nothing went through agent_say
        assert db.scalar(select(ChatJob).where(ChatJob.turn_id == coordinator.follow_up_turn(turn))) is None
    assert env.announced == []


def test_a_thought_cognition_says_to_respond_is_followed_up_once_through_the_mouth(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    assert coordinator.run_once()
    coordinator._conclude(_work(env, turn)["mind"].id)  # a retried conclusion dispatches nothing new
    with env.db() as db:
        jobs = list(db.scalars(select(ChatJob).where(ChatJob.turn_id == coordinator.follow_up_turn(turn))))
    assert len(jobs) == 1 and jobs[0].payload["phase"] == "follow_up"
    assert _run_chat(env)
    follow_turn = coordinator.follow_up_turn(turn)
    assert _assistant(env, follow_turn).content == "".join(REPLY)
    messages, context = env.inputs[-1]
    assert messages[-1]["content"].startswith("[INTERNAL TRIGGER") and "Stay in Berlin" in context.thought_result
    assert "private reasoning" not in context.thought_result  # her private thinking never reaches the mouth
    with env.db() as db:  # the trigger is never stored as a user message
        assert not db.scalar(select(ChatMessage).where(ChatMessage.content.like("[INTERNAL TRIGGER%")))
    assert _outputs(env, turn)["follow_up"].status == "delivered" and env.announced == [(follow_turn, "".join(REPLY))]


def test_recall_then_thinking_waits_for_memory_and_says_what_it_found(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    env.calls["jev"] = ("required", "required")
    turn, _ = _say(env)
    work = _work(env, turn)
    assert set(work) == {"recall", "mind"} and env.calls["search"] == 0  # the mouth didn't wait for the search
    assert work["mind"].dependency_ids == [str(work["recall"].id)]
    # The mind can't start first: the first claim is the recall, and the mind waits for it.
    with env.db() as db:
        claimed, _ = coordinator._claim(db, "t")
        kind = claimed.kind
        db.rollback()
    assert kind == "recall"
    assert coordinator.run_once() and coordinator.run_once()
    work = _work(env, turn)
    assert work["recall"].status == "completed" and work["recall"].result["status"] == "no_match"
    assert "found nothing" in env.calls["think"][0].memories
    assert work["mind"].result["evidence_status"] == "no_match"
    assert _run_chat(env)
    _, context = env.inputs[-1]
    assert "not proof it didn't happen" in context.thought_result


# --- interruption, staleness, deadlines, restarts --------------------------------------------------------


def test_a_new_message_supersedes_the_thought_and_it_never_answers_the_new_one(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    first, _ = _say(env)
    env.calls["jev"] = ("not_required", "not_required")
    _say(env, "actually never mind, how's your day?")
    assert _work(env, first)["mind"].status == "superseded"
    assert not coordinator.run_once()  # nothing left to run
    coordinator._conclude(_work(env, first)["mind"].id)
    assert _outputs(env, first)["follow_up"].decision == "superseded"
    with env.db() as db:
        assert db.scalar(select(ChatJob).where(ChatJob.turn_id == coordinator.follow_up_turn(first))) is None


def test_a_result_arriving_after_supersession_is_kept_private_not_accepted(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, message = _say(env)

    def think_then_interrupted(inp):
        # While the mind runs, a new message arrives and supersedes it.
        coordinator.supersede_older(env.user.id, env.conversation.id, uuid.uuid4())
        return HardThought("decision", "Too late.", thinking="x")

    monkeypatch.setattr(coordinator, "_think", think_then_interrupted)
    assert coordinator.run_once()
    work = _work(env, turn)["mind"]
    assert work.status == "superseded" and work.result["conclusion"] == "Too late."  # kept, not accepted
    assert _outputs(env, turn)["follow_up"].decision == "superseded"


def test_a_follow_up_is_rechecked_at_dispatch_and_dropped_after_new_input(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    assert coordinator.run_once()  # follow-up queued
    with env.db() as db:  # the user speaks before the follow-up job runs (no chat job: just the message)
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        chat_service.add_message(db, conversation=conversation, role="user", content="wait", turn_id=uuid.uuid4())
    assert _run_chat(env)
    assert _assistant(env, coordinator.follow_up_turn(turn)) is None
    assert _outputs(env, turn)["follow_up"].status == "cancelled"


def test_expired_work_ends_and_still_reaches_cognition(env, monkeypatch):
    seen = []
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result",
                        lambda inp: seen.append(inp) or ResultDecision("respond", "stub", True))
    turn, _ = _say(env)
    with env.db() as db:
        work = db.get(CoordinatorWork, _work(env, turn)["mind"].id)
        work.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert coordinator.run_once()
    assert _work(env, turn)["mind"].status == "expired" and env.calls["think"] == []
    assert seen[0].result.failed == "deadline"
    assert _run_chat(env)
    _, context = env.inputs[-1]
    assert "didn't reach a conclusion" in context.thought_result


def test_restart_reclaims_a_lost_lease_and_scheduling_twice_adds_nothing(env):
    turn, message = _say(env)
    mind = _work(env, turn)["mind"]
    again = coordinator.schedule(user_id=env.user.id, conversation_id=env.conversation.id, request_id=turn,
                                 trigger_message_id=message, with_recall=False, work_input={}, input_versions={})
    assert [p.work_id for p in again] == [str(mind.id)]
    with env.db() as db:  # a worker died mid-run: its lease ran out
        row = db.get(CoordinatorWork, mind.id)
        row.status, row.lease_owner = "running", "dead"
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert coordinator.run_once()
    mind = _work(env, turn)["mind"]
    assert mind.status == "completed" and mind.attempt == 1


def test_lifecycle_trace_holds_ids_and_statuses_not_text(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    coordinator.run_once()
    lines = [json.loads(x) for x in env.log.read_text().splitlines()]
    ops = [x.get("op") for x in lines if x.get("component") == "coordinator"]
    assert ops[:3] == ["scheduled", "output", "finished"] and "follow_up_dispatched" in ops
    raw = env.log.read_text()
    assert "Berlin" not in raw and "private reasoning" not in raw
