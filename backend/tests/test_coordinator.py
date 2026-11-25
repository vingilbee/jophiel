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
    # The promise check's model (services/promise_check.py), scripted: by default what she says carries out every
    # promise it's asked about; tests of the check itself script it more strictly.
    from app.services import promise_check
    monkeypatch.setattr(promise_check, "_judge", lambda task: promise_check.Reading(promises=[
        promise_check.Verdict(item=p["id"], kept=True) for p in task["promises"]]))
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
        result = await use("come_back_to", what="ask how the interview went", asked_by="me",
                           in_seconds=3 * 24 * 3600)
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
        assert "came due: something you chose to come back to" in context.wake
        assert "ask how the interview went" in context.wake
        from app.ai.prompts import build_system_prompt
        prompt = build_system_prompt(context)
        assert "## Now" in prompt and "It was your own idea (optional)" in prompt and "## Why you're awake" in prompt
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
        await use("come_back_to", what="remind them about their mum", asked_by="user", in_seconds=7200, item=ref)
        moved = await use("come_back_to", what="remind them about their mum", asked_by="user", in_seconds=10800,
                          item=ref)
        assert "replaces the time you had set" in moved
        early = await memory["update_item"].ainvoke({"item": ref, "status": "fulfilled",
                                                     "reason": "they called her already"})
        assert early.startswith("Not done") and "hasn't come" in early  # not hers to have kept before its time
        closed = await memory["update_item"].ainvoke({"item": ref, "status": "cancelled",
                                                      "reason": "they called her already"})
        assert "dropped" in closed
        return "Noted."

    env.scripts.append(promise)
    run_job(env)
    with env.db() as db:
        statuses = sorted((w.status, w.error_code) for w in db.scalars(select(CoordinatorWork)))
        item = db.scalar(select(WorkingItem).where(WorkingItem.kind == "commitment"))
    assert statuses == [("cancelled", "item_closed"), ("cancelled", "rescheduled")]
    assert item.status == "cancelled"


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


# --------------------------------------------------------------------------- #
# Continuity (docs/MIND_CONTINUITY_FOLLOWUP_HANDOFF.md): what a wake is given, the intention's lifecycle, history
# boundaries and a fresh clock
# --------------------------------------------------------------------------- #

ZONE = "America/Vancouver"
STALE = ("- Local time: 03:00 on Monday 02 November 2026 (the middle of the night, America/Vancouver).\n"
         "- This session has been open 3 minutes; you last exchanged messages moments ago.\n"
         "- Eyes: closed. You cannot see the user's screen or anything else.")


def said(env, text, **payload) -> uuid.UUID:
    """A user message with its job's payload as the socket builds it (zone, awareness snapshot)."""
    turn = uuid.uuid4()
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        msg = chat_service.add_message(db, conversation=conversation, role="user", content=text, turn_id=turn)
        chat_service.enqueue_job(db, user_id=env.user.id, conversation=conversation, turn_id=turn,
                                 user_message_id=msg.id, payload={"content": text, "channel": "web", "zone": ZONE,
                                                                  **payload})
    return turn


def wakes(env):
    with env.db() as db:
        return list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.kind == "wake")
                               .order_by(CoordinatorWork.created_at)))


def evaluations(env):
    from app.db.models import MindEvaluation
    with env.db() as db:
        return list(db.scalars(select(MindEvaluation).order_by(MindEvaluation.finished_at)))


def _promise_tools(context):
    from app.ai.tools.memory import build_memory_tools
    return {t.name: t for t in build_memory_tools(context.user_id, wake=context.wake is not None,
                                                  evaluation=context.mind)}


def test_a_requested_check_in_wakes_her_with_its_origin_commitment_deadline_and_delivery_state(env):
    said(env, "In 30 seconds, ask me whether Lantern Finch is ready.")

    async def agree(use, context):
        result = await use("come_back_to", what="ask whether Lantern Finch is ready", asked_by="user", in_seconds=30)
        assert result.startswith("Scheduled [wake:") and ZONE in result and "(30 s from now)" in result
        assert "Kept as your promise [" in result
        return "Sure, I'll ask in 30 seconds."

    env.scripts.append(agree)
    run_job(env)
    [wake] = wakes(env)
    assert wake.input["asked_by"] == "user" and wake.input["item"] and wake.input["chain"] == str(wake.id)
    due = datetime.fromisoformat(wake.input["due_at"])
    assert 29 <= (due - datetime.fromisoformat(wake.input["scheduled_at"])).total_seconds() <= 31  # not rounded
    _due(env)
    drain(env)

    async def check_in(use, context):
        now = context.now
        assert "It is " in now and f"in {ZONE}" in now  # a fresh clock in their zone
        assert "They asked for this" in now and "something you agreed to do" in now
        assert "In 30 seconds, ask me whether Lantern Finch is ready." in now  # the original request
        assert "that was the time you chose (the deadline), not the time you set it" in now
        assert "Nothing has been delivered for it yet." in now
        assert "Their messages since: 0" in now and "still open (open)" in now
        assert "came due: a check-in they asked you for" in context.wake
        from app.ai.prompts import build_system_prompt
        prompt = build_system_prompt(context)
        assert "aren't waiting on you" not in prompt and "A due commitment is something you" in prompt
        result = await _promise_tools(context)["update_item"].ainvoke(
            {"item": wake.input["item"][:8], "status": "fulfilled", "reason": "asked them"})
        assert result.startswith("Noted:") and "once what you say now reaches them" in result
        return "Is Lantern Finch ready?"

    env.scripts.append(check_in)
    assert run_job(env)
    assert messages(env)[-1] == ("assistant", "Is Lantern Finch ready?")
    last = evaluations(env)[-1]
    assert (last.reason, last.outcome, last.delivery) == ("due", "said", "delivered")
    with env.db() as db:
        assert db.get(WorkingItem, uuid.UUID(wake.input["item"])).status == "fulfilled"  # delivered: it stands


def test_a_check_in_asked_for_on_whatsapp_is_delivered_to_the_same_person(env, monkeypatch):
    sent = []
    monkeypatch.setattr(chat_worker.whatsapp_service, "schedule_delivery",
                        lambda db, **kw: sent.append((kw["channel_identity_id"], kw["content"])))
    monkeypatch.setattr(chat_worker.whatsapp_service, "wake_worker", lambda: None)
    identity = uuid.uuid4()
    said(env, "In 30 seconds, ask me whether Lantern Finch is ready.", channel="whatsapp",
         channel_identity_id=str(identity))

    async def agree(use, context):
        await use("come_back_to", what="ask whether Lantern Finch is ready", asked_by="user", in_seconds=30)
        return "Sure."

    env.scripts.append(agree)
    run_job(env)
    [wake] = wakes(env)
    assert wake.input["channel"] == "whatsapp" and wake.input["channel_identity_id"] == str(identity)
    _due(env)
    drain(env)

    async def check_in(use, context):
        return "Is Lantern Finch ready?"

    env.scripts.append(check_in)
    assert run_job(env)  # a wake has no request of its own: the recipient comes from the wake
    assert sent == [(identity, "Sure."), (identity, "Is Lantern Finch ready?")]


def test_putting_it_off_revises_the_same_intention_with_a_reason_and_one_live_schedule(env):
    said(env, "Remind me to ask Sam about Lantern Finch in a minute.")

    async def agree(use, context):
        await use("come_back_to", what="remind them to ask Sam about Lantern Finch", asked_by="user", in_seconds=60)
        return "Will do."

    env.scripts.append(agree)
    run_job(env)
    [first] = wakes(env)
    _due(env)
    drain(env)

    async def put_off(use, context):
        refused = await use("come_back_to", what="remind them", asked_by="user", in_seconds=120,
                            moves=f"wake:{str(first.id)[:8]}")
        assert refused.startswith("Not done") and "reason" in refused
        moved = await use("come_back_to", what="remind them to ask Sam about Lantern Finch", asked_by="user",
                          in_seconds=120, moves=f"wake:{str(first.id)[:8]}", reason="they said they were driving")
        assert f"It replaces [wake:{str(first.id)[:8]}] (same intention, revision 1)" in moved
        return ""

    env.scripts.append(put_off)
    run_job(env)
    first_after, second = wakes(env)
    assert first_after.result["moved_to"] == str(second.id) and first_after.result["moved_reason"]
    assert second.status == "queued" and second.input["chain"] == str(first.id)
    assert second.input["item"] == first.input["item"]  # the same promise, not a new one
    assert second.input["origin_message"] == first.input["origin_message"]  # its original request
    assert second.input["revisions"][0]["reason"] == "they said they were driving"
    assert [w.status for w in wakes(env)].count("queued") == 1
    assert evaluations(env)[-1].outcome == "no_words"
    _due(env)
    drain(env)

    async def again(use, context):
        assert "they said they were driving" in context.now
        assert "When it came due before" in context.now and "you wrote nothing" in context.now
        assert "Remind me to ask Sam about Lantern Finch in a minute." in context.now
        return "Now's a good time to ask Sam about Lantern Finch."

    env.scripts.append(again)
    run_job(env)
    assert messages(env)[-1][1].startswith("Now's a good time")


def test_letting_go_of_the_wake_that_woke_her_keeps_why(env):
    said(env, "check on the Lantern Finch build later")

    async def agree(use, context):
        await use("come_back_to", what="check on the build", asked_by="me", in_seconds=60)
        return "Okay."

    env.scripts.append(agree)
    run_job(env)
    [wake] = wakes(env)
    _due(env)
    drain(env)

    async def let_go(use, context):
        assert "It was your own idea (optional)" in context.now
        assert await use("drop_come_back", wake=f"wake:{str(wake.id)[:8]}", reason="they already shipped it") \
            == "Dropped."
        await use("stay_silent", reason="nothing to add")
        return ""

    env.scripts.append(let_go)
    run_job(env)
    [after] = wakes(env)
    assert after.result["dropped"]["reason"] == "they already shipped it"
    assert evaluations(env)[-1].outcome == "silent"


def test_a_promise_marked_kept_whose_words_never_arrived_opens_again_and_stays_visible(env):
    said(env, "ask me in a minute if Lantern Finch is ready")

    async def agree(use, context):
        await use("come_back_to", what="ask whether Lantern Finch is ready", asked_by="user", in_seconds=60)
        return "Sure."

    env.scripts.append(agree)
    run_job(env)
    [wake] = wakes(env)
    _due(env)
    drain(env)

    async def check_in(use, context):
        await _promise_tools(context)["update_item"].ainvoke(
            {"item": wake.input["item"][:8], "status": "fulfilled", "reason": "asking now"})
        with env.db() as db:  # they write while she words it: what she says is fenced off
            conversation = chat_service.get_conversation(db, user_id=env.user.id,
                                                         conversation_id=env.conversation.id)
            chat_service.add_message(db, conversation=conversation, role="user", content="brb", turn_id=uuid.uuid4())
        return "Is Lantern Finch ready?"

    env.scripts.append(check_in)
    run_job(env)
    assert messages(env)[-1] == ("user", "brb")
    last = evaluations(env)[-1]
    assert (last.outcome, last.delivery) == ("cancelled", "cancelled")
    with env.db() as db:
        item = db.get(WorkingItem, uuid.UUID(wake.input["item"]))
    assert item.status == "open" and "wasn't delivered (cancelled)" in item.status_reason
    said(env, "ok I'm back")

    async def look(use, context):
        assert f"[wake:{str(wake.id)[:8]}] came due" in context.mind_state
        assert "it was cancelled before anything was delivered" in context.mind_state
        return "Welcome back. Is Lantern Finch ready?"

    env.scripts.append(look)
    run_job(env)


def test_after_one_message_of_silence_the_next_request_is_its_own_current_message(env, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.ai.messages import to_lc_messages

    said(env, "Don't reply to this message.")

    async def quiet(use, context):
        await use("stay_silent", reason="they asked for no reply to this message")
        return ""

    env.scripts.append(quiet)
    run_job(env)
    said(env, "Reply with exactly: Ready.")

    async def ready(use, context):
        return "Ready."

    env.scripts.append(ready)
    run_job(env)
    history = env.seen[-1].messages
    assert [t["role"] for t in history] == ["user", "record", "user"]  # never merged into one turn
    assert history[0]["content"] == "Don't reply to this message."  # the user's text, untouched
    assert "you chose silence" in history[1]["content"] and "they asked for no reply" in history[1]["content"]
    assert "CURRENT user message" in history[2]["marker"] and "CURRENT" not in history[0]["marker"]
    lc = to_lc_messages(history)
    assert [type(m) for m in lc] == [SystemMessage, HumanMessage, SystemMessage, SystemMessage, HumanMessage]
    assert lc[-1].content == "Reply with exactly: Ready." and lc[-2].content.startswith("[App record")
    assert messages(env)[-1] == ("assistant", "Ready.")
    # The same boundaries after a reload and when the window is cut to the last two messages.
    monkeypatch.setattr(get_settings(), "short_term_memory_messages", 2)
    monkeypatch.setattr(chat_service, "HISTORY_STEP", 1)  # a window cut exactly (it normally holds its start)
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        reloaded = chat_service.get_history(db, conversation=conversation, records=True, zone=ZONE)
    assert [t["role"] for t in reloaded] == ["user", "assistant"]
    assert reloaded[0]["content"] == "Reply with exactly: Ready."


def test_a_silent_evaluation_stays_after_its_message_when_the_clocks_disagree(env):
    """Messages are timed by the database's clock, evaluations by the worker's: a record is placed by the
    message it answered, so a worker clock behind the database's can't drop it or move it before the message."""
    from app.db.models import MindEvaluation

    said(env, "Don't reply to this message.")

    async def quiet(use, context):
        await use("stay_silent", reason="they asked for no reply")
        return ""

    env.scripts.append(quiet)
    run_job(env)
    with env.db() as db:
        for row in db.scalars(select(MindEvaluation)):
            row.started_at -= timedelta(minutes=5)
            row.finished_at -= timedelta(minutes=5)
        db.commit()
    said(env, "Now say hi.")
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        history = chat_service.get_history(db, conversation=conversation, records=True, zone=ZONE)
    assert [t["role"] for t in history] == ["user", "record", "user"]
    assert "you chose silence" in history[1]["content"]


def test_every_evaluation_reads_a_fresh_clock_never_a_queued_snapshot(env):
    said(env, "should I take the Berlin job?", awareness=STALE)

    async def start(use, context):
        assert "Local time" not in context.awareness and "Eyes: closed" in context.awareness
        assert "It is " in context.now and "Their message arrived at" in context.now
        await use("think_it_over", about="the Berlin job")
        return "Let me think."

    env.scripts.append(start)
    run_job(env)
    drain(env)

    async def wake(use, context):
        aw = context.awareness
        assert "Local time" not in aw and "This session" not in aw and "As their app last reported it" in aw
        local = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(ZONE))
        assert f"on {local:%A %d %B %Y} in {ZONE}" in context.now
        assert "Your previous evaluation in this conversation ended" in context.now
        return ""

    env.scripts.append(wake)
    run_job(env)


def test_a_stopped_reply_keeps_what_she_had_done_on_their_phone(env):
    """A reply cut off by the user's next message (before any words, or part way) is still in her history as the
    app's record, with its turn's line about her hands on their phone: the taps happened even though she said
    nothing about them. A plain cancelled reply with no phone use is a record too; the user's view is unchanged."""
    from app.db.models import PhoneActivity
    from app.services.phone_control import turn_line_id

    said(env, "Go to the U.")
    cancelled, partial, quiet = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        chat_service.add_message(db, conversation=conversation, role="assistant", content="", turn_id=cancelled,
                                 status="cancelled")
        chat_service.add_message(db, conversation=conversation, role="user", content="No, go back.")
        chat_service.add_message(db, conversation=conversation, role="assistant", content="I see what",
                                 turn_id=partial, status="partial")
        chat_service.add_message(db, conversation=conversation, role="user", content="The list.")
        chat_service.add_message(db, conversation=conversation, role="assistant", content="", turn_id=quiet,
                                 status="cancelled")
        chat_service.add_message(db, conversation=conversation, role="user", content="Okay.")
        db.add(PhoneActivity(id=turn_line_id(cancelled), conversation_id=env.conversation.id,
                             content="Used your phone in WhatsApp, 4 steps"))
        db.add(PhoneActivity(id=turn_line_id(partial), conversation_id=env.conversation.id,
                             content="Used your phone in WhatsApp, 2 steps (you took over)"))
        db.add(PhoneActivity(id=uuid.uuid4(), conversation_id=env.conversation.id, content="Opened WhatsApp"))
        db.commit()
        history = chat_service.get_history(db, conversation=conversation, records=True, zone=ZONE)
        # While this worker remembers the cut-off turn's steps, they come with its line, one by one.
        steps = {str(cancelled): ["tapped [3] → Phone screen s4: WhatsApp in front", "pressed back → no answer seen"]}
        import app.services.phone_control as phone_control_service
        original = phone_control_service.steps
        phone_control_service.steps = lambda user, turn, **kw: steps.get(str(turn), [])
        try:
            stepped = chat_service.get_history(db, conversation=conversation, records=True, zone=ZONE)
        finally:
            phone_control_service.steps = original
        plain = chat_service.get_history(db, conversation=conversation)
    assert [t["role"] for t in history] == ["user", "record", "user", "assistant", "user", "record", "user"]
    assert "stopped before any words" in history[1]["content"]
    assert "Used your phone in WhatsApp, 4 steps" in history[1]["content"]
    assert history[3]["content"] == "I see what"  # her words untouched; the note is the app's marker
    assert "stopped part way" in history[3]["marker"] and "2 steps (you took over)" in history[3]["marker"]
    assert "stopped before any words" in history[5]["content"] and "on their phone" not in history[5]["content"]
    assert not any("Opened WhatsApp" in t.get("content", "") + t.get("marker", "") for t in history)
    assert [t["role"] for t in plain] == ["user", "user", "assistant", "user", "user"]
    assert ("Used your phone in WhatsApp, 4 steps; step by step: 1. tapped [3] → Phone screen s4: WhatsApp in front; "
            "2. pressed back → no answer seen") in stepped[1]["content"]
    assert "step by step" not in stepped[3]["marker"]
