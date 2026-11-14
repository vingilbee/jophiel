"""Her mind's wakes, end to end through the real chat worker, coordinator and her mind's tools
(docs/MIND_ARCHITECTURE.md; acceptance scenarios in docs/COGNITION_TO_MIND_HANDOFF.md). Real PostgreSQL
(``TEST_DATABASE_URL``). Only the model's run is scripted: each script stands in for one evaluation and uses her real
tools (``build_mind_tools`` on the evaluation the worker built), so what she chooses is the script's, and everything
that carries it out, persists it and wakes her again is the real code.

- A message runs one evaluation: no student, appraisal or mood stage; silence is hers and stores nothing.
- Thinking she starts holds nothing open: it runs, then wakes her with its conclusion, and she decides what to say.
- A reconsideration she schedules wakes her later without a new message, survives a restart, and isn't superseded
  by a new message; closing the item it was about drops it.
- A new message makes unfinished thinking moot; duplicate dispatches and retries never repeat an effect.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.ai.mind.hard import HardThought
from app.ai.tools.mind import build_mind_tools
from app.core.config import get_settings
from app.db.models import ChatMessage, CoordinatorOutput, CoordinatorWork, DecisionAttempt, WorkingItem
from app.services import chat as chat_service
from app.services import coordinator, memory_manager, mind_turn, recall_references
from app.workers import chat as chat_worker
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    settings = get_settings()
    for key, value in {"redis_url": None, "mind_turn_log_path": str(tmp_path / "turns.jsonl"),
                       "mind_work_deadline_seconds": 180, "lessons_supply_enabled": False,
                       "desktop_agent_enabled": False}.items():
        monkeypatch.setattr(settings, key, value)

    def no_match(user_id, conversation_id, user_message_id, query, zone, **_):
        from app.ai.recall.contracts import RecallResult
        return recall_references.TurnRecall(user_id, conversation_id, "UTC", datetime.now(timezone.utc), None,
                                            RecallResult("no_match"), "Memory search ran and found nothing.", None,
                                            user_message_id=user_message_id)

    monkeypatch.setattr(recall_references, "for_reply", no_match)
    monkeypatch.setattr(recall_references, "save_receipt", lambda *a, **k: None)
    thoughts = []

    def think(inp):
        thoughts.append(inp)
        return HardThought("decision", "Stay in Berlin for a year, then decide.", thinking="private reasoning")

    monkeypatch.setattr(coordinator, "_think", think)
    scripts, seen, published, announced = [], [], [], []

    async def stream(*, messages, context, **_):
        """One evaluation: the next script decides, with her real tools; its return value is what she writes."""
        seen.append(SimpleNamespace(messages=messages, context=context))
        tools = {t.name: t for t in build_mind_tools(context.mind)}

        async def use(name, **args):
            result = await tools[name].ainvoke(args)
            context.mind.actions.append({"tool": name, "status": "success", "result": str(result)[:300]})
            return result

        words = await scripts.pop(0)(use, context)
        for token in ([words] if words else []):
            yield {"type": "token", "content": token}

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "stream_chat", stream)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker, "_announce", lambda job, reply, mid: announced.append((job.turn_id, reply)) or True)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", lambda *a, **k: None)
    monkeypatch.setattr(coordinator, "_wake", lambda: None)
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    return SimpleNamespace(db=db_env.db, user=user, conversation=conversation, scripts=scripts, seen=seen,
                           published=published, announced=announced, thoughts=thoughts,
                           trace=tmp_path / "turns.jsonl")


def says(text):
    async def script(use, context):
        return text
    return script


def message(env, text) -> uuid.UUID:
    """A user message and its chat job, as the socket enqueues them. Returns the turn id."""
    turn = uuid.uuid4()
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        msg = chat_service.add_message(db, conversation=conversation, role="user", content=text, turn_id=turn)
        chat_service.enqueue_job(db, user_id=env.user.id, conversation=conversation, turn_id=turn,
                                 user_message_id=msg.id, payload={"content": text, "channel": "web"})
    return turn


def run_job(env) -> bool:
    with env.db() as db:
        job = chat_service.claim_next_job(db, lease_seconds=60)
        if job is not None:
            db.expunge(job)
    if job is None:
        return False
    asyncio.run(chat_worker._generate(job))
    return True


def drain(env, limit=10):
    for _ in range(limit):
        if not coordinator.execute_once():
            return


def messages(env):
    with env.db() as db:
        return [(m.role, m.content) for m in db.scalars(select(ChatMessage).where(
            ChatMessage.conversation_id == env.conversation.id).order_by(ChatMessage.conversation_sequence))]


def outputs(env, phase="follow_up"):
    with env.db() as db:
        return list(db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.user_id == env.user.id,
                                                               CoordinatorOutput.phase == phase)))


# --------------------------------------------------------------------------- #
# One evaluation per message
# --------------------------------------------------------------------------- #


def test_a_message_is_one_evaluation_of_her_mind_with_no_other_stage(env):
    message(env, "how was your day?")
    env.scripts.append(says("Quiet, mostly. Yours?"))
    assert run_job(env)
    assert messages(env) == [("user", "how was your day?"), ("assistant", "Quiet, mostly. Yours?")]
    ctx = env.seen[0].context
    # What she read: her personality, her mind's section, memory's check; no reading, notes or decision brief.
    assert ctx.mind is not None and ctx.personality and ctx.wake is None
    assert "Memory search ran and found nothing" in (ctx.recall or "")
    assert not hasattr(ctx, "cognitive_reading") and not hasattr(ctx, "decision")
    route = next(e for e in env.published if e.get("type") == "route")
    assert (route["route"], route["recall"]) == ("mind", "no_match")
    [line] = [json.loads(x) for x in env.trace.read_text().splitlines() if '"component": "mind"' in x]
    assert (line["reason"], line["did"]["spoke"], line["memory"]["status"]) == ("user_message", True, "no_match")


def test_silence_is_her_choice_and_stores_nothing(env):
    turn = message(env, "ok")

    async def quiet(use, context):
        assert await use("stay_silent", reason="nothing to add") == "Silent."
        return ""

    env.scripts.append(quiet)
    run_job(env)
    assert messages(env) == [("user", "ok")]
    assert env.published[-1] == {"type": "done", "turn_id": str(turn), "message_id": str(uuid.uuid5(turn, "assistant")),
                                 "status": "silent"}
    with env.db() as db:
        assert db.scalar(select(func.count()).select_from(chat_service.ChatJob).where(
            chat_service.ChatJob.status == "completed")) == 1


def test_a_failed_memory_search_is_said_as_failed_never_as_nothing_found(env, monkeypatch):
    from app.ai.recall.contracts import RecallResult

    def failed(user_id, conversation_id, user_message_id, query, zone, **_):
        return recall_references.TurnRecall(user_id, conversation_id, "UTC", datetime.now(timezone.utc), None,
                                            RecallResult.failed("search_failed"), "", None)

    monkeypatch.setattr(recall_references, "for_reply", failed)
    monkeypatch.setattr(recall_references, "cached_for_reply", failed)
    message(env, "what did I tell you about Nora?")
    env.scripts.append(says("I can't check my memory right now."))
    run_job(env)
    ctx = env.seen[0].context
    assert "search FAILED" in ctx.turn_boundary and "nothing that matches" not in ctx.turn_boundary
    assert next(e for e in env.published if e.get("type") == "route")["degraded"] == ["recall_unavailable"]


# --------------------------------------------------------------------------- #
# Thinking she starts
# --------------------------------------------------------------------------- #


def test_thinking_she_starts_wakes_her_with_its_conclusion_and_she_decides_what_to_say(env):
    message(env, "should I take the Berlin job?")

    async def start(use, context):
        result = await use("think_it_over", about="whether the Berlin job suits them", look_up_memory_first=True)
        assert result.startswith("Started [work:")
        return "Let me think that through properly."

    env.scripts.append(start)
    run_job(env)
    assert messages(env)[-1] == ("assistant", "Let me think that through properly.")
    with env.db() as db:
        kinds = sorted(w.kind for w in db.scalars(select(CoordinatorWork)))
    assert kinds == ["mind", "recall"]  # the lookup first, then the thinking: nothing held open meanwhile
    [immediate] = outputs(env, "immediate")
    assert (immediate.decision, immediate.status) == ("respond", "delivered")

    drain(env)  # recall, then the thinker, then the wake is dispatched
    assert "whether the Berlin job suits them" in env.thoughts[0].problem.text
    assert env.thoughts[0].character and env.thoughts[0].character[0].startswith("Jophiel")  # her personality
    [wake] = outputs(env)
    assert (wake.decision, wake.status) == ("wake", "dispatched")

    async def say_it(use, context):
        assert "The thinking you started has ended" in context.wake
        assert "Stay in Berlin for a year, then decide." in context.wake and "private reasoning" not in context.wake
        return "Having thought about it: stay a year, then decide."

    env.scripts.append(say_it)
    assert run_job(env)
    assert env.seen[-1].messages[-1]["content"].startswith("[INTERNAL TRIGGER")
    assert messages(env)[-1] == ("assistant", "Having thought about it: stay a year, then decide.")
    [wake] = outputs(env)
    assert (wake.decision, wake.status) == ("said", "delivered") and env.announced
    # Dispatching again (a retry, a second worker) wakes her no second time.
    drain(env)
    coordinator._conclude(wake.work_id)
    assert len(outputs(env)) == 1 and not run_job(env)


def test_woken_with_a_conclusion_she_may_keep_it_to_herself(env):
    message(env, "should I take the Berlin job?")

    async def start(use, context):
        await use("think_it_over", about="the Berlin job")
        return ""  # acts without speaking

    env.scripts.append(start)
    run_job(env)
    assert messages(env) == [("user", "should I take the Berlin job?")]
    drain(env)

    async def keep(use, context):
        await use("stay_silent", reason="not worth interrupting with")
        return ""

    env.scripts.append(keep)
    run_job(env)
    assert messages(env) == [("user", "should I take the Berlin job?")]
    [wake] = outputs(env)
    assert (wake.decision, wake.status) == ("silent", "not_sent") and env.announced == []


def test_a_new_message_makes_unfinished_thinking_moot_and_nothing_stale_is_said(env):
    message(env, "should I take the Berlin job?")

    async def start(use, context):
        await use("think_it_over", about="the Berlin job")
        return "Thinking."

    env.scripts.append(start)
    run_job(env)
    message(env, "actually, never mind, what's for dinner?")
    env.scripts.append(says("Pasta?"))
    run_job(env)
    with env.db() as db:
        assert {w.status for w in db.scalars(select(CoordinatorWork))} == {"superseded"}
    drain(env)
    assert not run_job(env) and env.thoughts == [] and messages(env)[-1] == ("assistant", "Pasta?")


# --------------------------------------------------------------------------- #
# Coming back later, without a message
# --------------------------------------------------------------------------- #


def _due(env):
    with env.db() as db:
        db.execute(update(CoordinatorWork).where(CoordinatorWork.kind == "wake").values(
            next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()


def test_she_schedules_coming_back_and_is_woken_later_without_a_message(env):
    message(env, "my interview is on Friday")

    async def note(use, context):
        result = await use("come_back_to", what="ask how the interview went", in_minutes=60 * 24 * 3)
        assert result.startswith("Scheduled [wake:")
        return "Good luck on Friday!"

    env.scripts.append(note)
    run_job(env)
    drain(env)
    assert outputs(env) == []  # not due: nothing runs, the model isn't called
    # A worker restart: nothing lives in process memory; the row is the record.
    from app.services import coordinator_runtime
    coordinator_runtime._instance = None
    # A new message meanwhile doesn't make it moot (it's not thinking about that message).
    message(env, "thanks!")
    env.scripts.append(says("Anytime."))
    run_job(env)
    with env.db() as db:
        [wake] = list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.kind == "wake")))
    assert wake.status == "queued"
    _due(env)
    drain(env)

    async def check_in(use, context):
        assert context.wake.startswith("You chose to come back to this") and "ask how the interview went" in context.wake
        from app.ai.prompts import build_system_prompt
        assert "Nobody just wrote to you. You chose to come back to this" in build_system_prompt(context)
        return "How did the interview go?"

    env.scripts.append(check_in)
    assert run_job(env)
    assert messages(env)[-1] == ("assistant", "How did the interview go?")
    with env.db() as db:
        attempts = list(db.scalars(select(DecisionAttempt)))
    assert attempts == []  # capture is off in tests; the wake ran as one evaluation (traced below)
    reasons = [json.loads(x)["reason"] for x in env.trace.read_text().splitlines() if '"component": "mind"' in x]
    assert reasons == ["user_message", "user_message", "due"]


def test_a_reconsideration_about_an_item_goes_when_the_item_closes_and_moves_when_rescheduled(env):
    message(env, "remind me to call my mum")

    async def promise(use, context):
        from app.ai.tools.memory import build_memory_tools
        memory = {t.name: t for t in build_memory_tools(context.user_id, message_id=context.source_message_id)}
        kept = await memory["note_commitment"].ainvoke({"what": "remind them to call their mum"})
        ref = kept.split("[")[1].split("]")[0]
        await use("come_back_to", what="remind them about their mum", in_minutes=120, item=ref)
        moved = await use("come_back_to", what="remind them about their mum", in_minutes=180, item=ref)
        assert "replaces the time you had set" in moved
        closed = await memory["update_item"].ainvoke({"item": ref, "status": "fulfilled",
                                                      "reason": "they called her already"})
        assert "dropped" in closed
        return "Noted."

    env.scripts.append(promise)
    run_job(env)
    with env.db() as db:
        statuses = sorted((w.status, w.error_code) for w in db.scalars(select(CoordinatorWork)))
        item = db.scalar(select(WorkingItem).where(WorkingItem.kind == "commitment"))
    assert statuses == [("cancelled", "item_closed"), ("cancelled", "rescheduled")]
    assert item.status == "fulfilled"


def test_a_promise_she_keeps_on_her_own_can_be_closed_in_that_wake(env):
    with env.db() as db:
        entry = memory_manager.note_commitment(db, env.user.id, "send them the recipe")
        db.commit()
        ref = memory_manager.ref(entry)
        coordinator.schedule_wake(db, user_id=env.user.id, conversation_id=env.conversation.id,
                                  about="send the recipe", due_at=datetime.now(timezone.utc), item_id=entry.id)
        db.commit()
    drain(env)

    async def keep(use, context):
        from app.ai.tools.memory import build_memory_tools
        memory = {t.name: t for t in build_memory_tools(context.user_id, wake=True)}
        result = await memory["update_item"].ainvoke({"item": ref, "status": "fulfilled", "reason": "sent it now"})
        assert result.startswith("Done:")
        return "Here's that recipe: ..."

    env.scripts.append(keep)
    run_job(env)
    with env.db() as db:
        assert db.get(WorkingItem, entry.id).status == "fulfilled"


# --------------------------------------------------------------------------- #
# Helpers other test files use (tests/test_background_coordination.py)
# --------------------------------------------------------------------------- #


def _say(env, content="Should I take the Berlin job? Think it over."):
    """A user message run through the real chat worker, in which she starts thinking it over (her own tool)."""
    turn = message(env, content)

    async def start(use, context):
        await use("think_it_over", about=content)
        return "Let me think about that."

    env.scripts.append(start)
    run_job(env)
    with env.db() as db:
        msg = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn, ChatMessage.role == "user"))
    return turn, msg.id


def _work(env, request_id):
    with env.db() as db:
        return {w.kind: w for w in db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id == request_id))}
