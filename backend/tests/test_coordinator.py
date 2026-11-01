"""Background thinking (docs/SHARED_STATE_STEP4_SPEC.md): a request that needs thinking commits work and the mouth
replies without waiting; the mind waits for its recall; the result goes through cognition before any follow-up;
a newer message supersedes it; retries never repeat a phase. Real PostgreSQL (``TEST_DATABASE_URL``). Student 2's
answers, the thinker and memory search are scripted: no model calls."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.mind.hard import HardThought
from app.ai.state.decisions import ResultDecision
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, CoordinatorOutput, CoordinatorWork
from app.services import chat as chat_service
from app.services import cognition_decisions, cognitive_live, coordinator, recall_references, student_two
from app.services import shared_state, background_components
from app.workers import chat as chat_worker
from db_support import create_conversation, create_user, database, db_env  # noqa: F401

REPLY = ("Let me think ", "about that.")


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    # Keep the production settings contract while scripting the local decision provider.
    settings = get_settings().model_copy(update=dict(
        cognition_decision_provider="student",
        shared_state_log_path=str(tmp_path / "shared.jsonl"),
        cognitive_live_student="on", cognitive_live_character=False, cognitive_live_student_dir=None,
        recall_enabled=True, mind_enabled=False, mind_inner_state_enabled=False, memory_manager_enabled=False,
        cognition_student_log_path=str(tmp_path / "student_two.jsonl"),
        shared_state_work_deadline_seconds=180, coordinator_queue_name="q", redis_url=None,
        recall_tool_calls_per_reply=3))
    for module in (shared_state, coordinator, student_two, background_components):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (shared_state, coordinator, chat_worker, background_components):
        monkeypatch.setattr(module, "SessionLocal", db_env.db)
    shared_state._personality.cache_clear()
    empty = SimpleNamespace(result=SimpleNamespace(status="not_searched", records=[], coverage={}, refs=lambda: []),
                            now=None, rendered="Memories from your last answer: none.", legacy=None, tool_calls=0,
                            supplied={})
    monkeypatch.setattr(recall_references, "cached_for_reply", lambda *a, **k: empty)
    # The reply's initial memory check is covered in test_initial_recall.py: here it found nothing, so the search
    # counted below is only the background work's own.
    from app.ai.state.contracts import MemoryContextView, Projection
    initial = recall_references.TurnRecall(None, None, "UTC", datetime.now(timezone.utc), None, SimpleNamespace(
        status="no_match", records=[], coverage={}, refs=lambda: []), "Memory search ran and found nothing.", None)
    monkeypatch.setattr(shared_state, "_memory", lambda *a, **k: (MemoryContextView(
        Projection("memory", "available"), recall_status="no_match", rendered=initial.rendered), initial))
    searched = SimpleNamespace(result=SimpleNamespace(status="no_match", records=[], coverage={}, refs=lambda: []),
                               rendered="Memory search ran and found nothing that matches.", tool_calls=0, supplied={})
    calls = {"search": 0, "think": [], "student": ("not_required", "required"), "communication": "respond",
             "result": "silent", "situations": []}

    def search(*a, **k):
        calls["search"] += 1
        return searched

    monkeypatch.setattr(recall_references, "for_reply", search)
    monkeypatch.setattr(recall_references, "save_receipt", lambda *a, **k: None)
    monkeypatch.setattr(cognitive_live, "after_reply", lambda *a, **k: None)
    monkeypatch.setattr(cognitive_live, "prepare", lambda **k: cognitive_live.LivePrep(interpretation=dict(
        acts={"asking": 0.9}, tone={"friendly": 0.9}, request={"talk_only": 0.9},
        understand={"acts": {"asking": True}, "tone": "friendly", "request": "talk_only"})))
    # Student 2, scripted: the real gather (test database), snapshot, check, routing and effects; only the
    # model's answer is given, from what the test says is needed.
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "test-student", "system": "sys", "rendering": None})
    real_decide = student_two.decide

    def answer(model, text):
        calls["situations"].append(text)
        if text.startswith("TRIGGER: thought result") or text.startswith("TRIGGER: work outcome"):
            if calls["result"] is None:
                return "not json"
            d = dict(intention="tell them what I concluded", work="no_change", communication=calls["result"])
            if calls["result"] != "silent":
                d.update(what_to_communicate="stay a year, then decide", move="react_briefly",
                         about=["T1"] if "[T1]" in text else ["W1"],
                         dials={"length": "a_little", "warmth": "somewhat", "playfulness": "none",
                                "directness": "somewhat"})
            return json.dumps(d)
        work = {("not_required", "required"): "request_thinking", ("required", "required"): "recall_then_thinking",
                ("required", "not_required"): "request_recall"}.get(calls["student"], "no_change")
        d = dict(intention="answer them", work=work, communication=calls["communication"])
        if calls["communication"] != "silent":
            d.update(what_to_communicate="let me think", move="react_briefly",
                     dials={"length": "a_little", "warmth": "somewhat", "playfulness": "none", "directness": "somewhat"})
        if work != "no_change":
            d["target"] = "M"
        if "thinking" in work:
            d.update(think_about="the Berlin job", think_form="whether")
        return json.dumps(d)

    monkeypatch.setattr(student_two, "decide", lambda live, **k: real_decide(live, **k, ask=answer))

    def think(inp):
        calls["think"].append(inp)
        return HardThought("decision", "Stay in Berlin for a year, then decide.", thinking="private reasoning")

    monkeypatch.setattr(coordinator, "_think", think)
    # Production cognition: Student 2 (scripted above) decides finished thoughts. Some tests swap in stubs.
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
    monkeypatch.setattr(chat_worker, "_announce", lambda job, reply, mid: announced.append((job.turn_id, reply)) or True)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", lambda *a, **k: None)
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    return SimpleNamespace(db=db_env.db, user=user, conversation=conversation, calls=calls, published=published,
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
    # Student 2 decided the reply and the thinking together: recorded as its decision, no gap.
    assert (immediate.decision, immediate.decision_origin, immediate.status) == ("respond", "student_2", "delivered")
    assert immediate.missing_capability is None
    assert env.calls["think"] == [] and work["mind"].input["think_about"] == "the Berlin job"
    route = next(e for e in env.published if e.get("type") == "route")
    assert route["route"] == "mind" and route["degraded"] == []


def test_cognition_choosing_silence_sends_nothing_and_keeps_the_work(env, monkeypatch):
    env.calls["communication"] = "silent"
    turn, _ = _say(env)
    assert _assistant(env, turn) is None and env.inputs == []  # the mouth never ran
    assert _outputs(env, turn)["immediate"].status == "not_sent"
    assert _work(env, turn)["mind"].status == "queued"
    assert env.published[-1] == {"type": "done", "turn_id": str(turn), "status": "silent"}


# --- the mind and result cognition ----------------------------------------------------------------------


def test_student_two_reads_the_finished_thought_and_its_silence_keeps_it_private(env):
    turn, _ = _say(env)
    assert coordinator.execute_once()
    work = _work(env, turn)["mind"]
    assert work.status == "completed" and work.result["conclusion"].startswith("Stay in Berlin")
    situation = env.calls["situations"][-1]
    assert situation.startswith("TRIGGER: thought result") and "NEW MESSAGE: none." in situation
    assert "[T1] HER PRIVATE THOUGHT (conclusion, just finished; not said to anyone): Stay in Berlin" in situation
    assert "[W1] thinking — the Berlin job (for [T1]): completed" in situation
    assert "private reasoning" not in situation  # her private thinking isn't part of the situation
    follow = _outputs(env, turn)["follow_up"]
    assert (follow.decision, follow.decision_origin, follow.status) == ("silent", "student_2", "not_sent")
    with env.db() as db:  # nothing queued to speak, and nothing went through agent_say
        assert db.scalar(select(ChatJob).where(ChatJob.turn_id == coordinator.follow_up_turn(turn))) is None
    assert env.announced == []


def test_student_two_choosing_to_say_it_reaches_the_follow_up_mouth_as_its_decision(env):
    env.calls["result"] = "respond"
    turn, _ = _say(env)
    assert coordinator.execute_once()
    follow = _outputs(env, turn)["follow_up"]
    assert (follow.decision, follow.decision_origin, follow.status) == ("respond", "student_2", "dispatched")
    assert _run_chat(env)
    _, context = env.inputs[-1]
    assert "Stay in Berlin" in context.thought_result
    assert "- What to get across: stay a year, then decide" in context.decision
    assert _outputs(env, turn)["follow_up"].status == "delivered"


def test_a_finished_thought_student_two_cannot_judge_stays_private_and_says_why(env):
    env.calls["result"] = None  # an unreadable answer
    turn, _ = _say(env)
    assert coordinator.execute_once()
    follow = _outputs(env, turn)["follow_up"]
    assert (follow.decision, follow.status) == ("blocked", "not_sent")
    assert follow.missing_capability.startswith("Student 2 invalid: not_json")
    assert env.announced == []


def test_work_that_ends_without_a_conclusion_goes_to_student_two_as_a_work_outcome(env):
    env.calls["result"] = "respond"
    turn, _ = _say(env)
    with env.db() as db:
        work = db.get(CoordinatorWork, _work(env, turn)["mind"].id)
        work.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert coordinator.execute_once()
    assert _work(env, turn)["mind"].status == "expired"
    situation = env.calls["situations"][-1]
    assert situation.startswith("TRIGGER: work outcome") and "HER PRIVATE THOUGHT: none." in situation
    assert "didn't reach a conclusion (deadline)" in situation
    assert _outputs(env, turn)["follow_up"].decision == "respond"


def test_a_thought_cognition_says_to_respond_is_followed_up_once_through_the_mouth(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    assert coordinator.execute_once()
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
    env.calls["student"] = ("required", "required")
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
    assert coordinator.execute_once() and coordinator.execute_once()
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
    env.calls["student"] = ("not_required", "not_required")
    _say(env, "actually never mind, how's your day?")
    assert _work(env, first)["mind"].status == "superseded"
    assert not coordinator.execute_once()  # nothing left to run
    coordinator._conclude(_work(env, first)["mind"].id)
    assert _outputs(env, first)["follow_up"].decision == "superseded"
    with env.db() as db:
        assert db.scalar(select(ChatJob).where(ChatJob.turn_id == coordinator.follow_up_turn(first))) is None


def test_a_result_arriving_after_supersession_cannot_populate_authoritative_result(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, message = _say(env)

    def think_then_interrupted(inp):
        # While the mind runs, a new message arrives and supersedes it.
        coordinator.supersede_older(env.user.id, env.conversation.id, uuid.uuid4())
        return HardThought("decision", "Too late.", thinking="x")

    monkeypatch.setattr(coordinator, "_think", think_then_interrupted)
    assert coordinator.execute_once()
    work = _work(env, turn)["mind"]
    assert work.status == "superseded" and work.result is None  # a stale executor cannot populate the authoritative result
    assert _outputs(env, turn)["follow_up"].decision == "superseded"


def test_a_follow_up_is_rechecked_at_dispatch_and_dropped_after_new_input(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    assert coordinator.execute_once()  # follow-up queued
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
    assert coordinator.execute_once()
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
    assert coordinator.execute_once()
    mind = _work(env, turn)["mind"]
    assert mind.status == "completed" and mind.attempt == 1


def test_lifecycle_trace_holds_ids_and_statuses_not_text(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", _respond)
    turn, _ = _say(env)
    coordinator.execute_once()
    lines = [json.loads(x) for x in env.log.read_text().splitlines()]
    ops = [x.get("op") for x in lines if x.get("component") == "coordinator"]
    assert ops[:3] == ["scheduled", "output", "finished"] and "follow_up_dispatched" in ops
    raw = env.log.read_text()
    assert "Berlin" not in raw and "private reasoning" not in raw
