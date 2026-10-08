"""Columbina acting on game events (stage D2b): the queue, which moments
wake her, when she may speak, her background tools, and the driver waiting
for her next goal. Real PostgreSQL (``TEST_DATABASE_URL``); in-memory Redis."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.ai.context import GameEvent
from app.ai.tools.desktop import build_desktop_tools
from app.db.models import AgentEventJob, ChatMessage, Conversation, DesktopSession
from app.services import agent_events, agent_say, desktop_game
from app.services import desktop_sessions as svc
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_driver import WALK, Script, _run
from tests.test_desktop_game import _paired_env, enabled  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)


def _jobs(db):
    return list(db.scalars(select(AgentEventJob).order_by(AgentEventJob.created_at)))


def _playing(db, user, *, steps=2, autonomy="continue"):
    _paired_env(db, user)
    desktop_game.start(db, user.id, goal="Collect wood", steps=[
        {"do": f"step {i}", "done_when": f"done {i}"} for i in range(steps)])
    session = svc.list_sessions(db, user.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    session.autonomy = autonomy
    db.commit()
    return session


# --- the queue ------------------------------------------------------------------


def test_events_for_one_session_merge_into_one_pending_job(db, alice):
    for reason in ("plan_low", "danger: zombie", "plan_low"):
        agent_events.enqueue(db, user_id=alice.id, source="desktop", dedupe_key="s1", reason=reason)
        db.commit()
    jobs = _jobs(db)
    assert len(jobs) == 1 and agent_events.reasons_of(jobs[0]) == ["plan_low", "danger: zombie"]


def test_a_running_job_blocks_its_key_but_new_events_queue_behind_it(db, alice):
    agent_events.enqueue(db, user_id=alice.id, source="desktop", dedupe_key="s1", reason="goal_done")
    db.commit()
    first = agent_events.claim(db)
    agent_events.enqueue(db, user_id=alice.id, source="desktop", dedupe_key="s1", reason="stuck")
    db.commit()
    assert agent_events.claim(db) is None  # same session: wait for the running one
    agent_events.finish(db, first.id)
    second = agent_events.claim(db)
    assert agent_events.reasons_of(second) == ["stuck"]


def test_failures_retry_then_give_up(db, alice):
    agent_events.enqueue(db, user_id=alice.id, source="desktop", dedupe_key="s1", reason="goal_done")
    db.commit()
    job = agent_events.claim(db)
    agent_events.fail(db, job.id, "boom")
    db.refresh(job)
    assert job.status == "pending" and job.attempts == 1
    for _ in range(2):
        job.not_before = job.created_at
        db.commit()
        job = agent_events.claim(db)
        agent_events.fail(db, job.id, "boom")
    db.refresh(job)
    assert job.status == "failed"


# --- which moments wake her -----------------------------------------------------------


def test_last_step_and_goal_done_wake_her(db, alice):
    session = _playing(db, alice, steps=2)
    svc.driver_progress(db, session.id, plan_revision=1, step_id="s1", status="step_done")
    assert agent_events.reasons_of(_jobs(db)[-1]) == ["plan_low"]
    svc.driver_progress(db, session.id, plan_revision=1, step_id="s2", status="step_done")
    assert agent_events.reasons_of(_jobs(db)[-1]) == ["plan_low", "goal_done"]


def test_stuck_needs_human_and_self_pauses_wake_her(db, alice):
    session = _playing(db, alice)
    svc.driver_progress(db, session.id, plan_revision=1, step_id="s1", status="stuck", note="wall")
    assert "stuck" in agent_events.reasons_of(_jobs(db)[-1])
    svc.resume_session(db, alice.id, session.id, expected_state_revision=db.get(DesktopSession, session.id).state_revision)
    svc.controller_pause(db, session.id, reason="not_foreground")
    assert "paused:not_foreground" in agent_events.reasons_of(_jobs(db)[-1])


def test_the_users_own_pause_does_not_wake_her(db, alice):
    session = _playing(db, alice)
    svc.pause_session(db, alice.id, session.id)
    assert _jobs(db) == []


def test_danger_wakes_her_notable_only_in_commentary_mode(db, alice):
    session = _playing(db, alice)
    svc.driver_noticed(db, session.id, kind="notable", text="found iron")
    assert _jobs(db) == []
    svc.driver_noticed(db, session.id, kind="danger", text="creeper behind you")
    assert agent_events.reasons_of(_jobs(db)[-1]) == ["danger: creeper behind you"]


# --- when she may speak ---------------------------------------------------------------


@pytest.mark.parametrize(("reasons", "mode", "since", "ok"), [
    (["needs_human"], "quiet", 1, True),           # always
    (["goal_done"], "quiet", None, False),
    (["paused:not_foreground"], "quiet", None, True),
    (["goal_done"], "occasional", None, True),
    (["goal_done"], "occasional", 10, False),      # too soon after the last one
    (["plan_low"], "occasional", None, False),     # not worth interrupting for
    (["notable: iron"], "commentary", 30, True),
])
def test_occasional_means_occasional(reasons, mode, since, ok):
    assert agent_say.allowed(reasons, mode, since)[0] is ok


def test_saying_saves_the_message_and_publishes_it(db, alice, isolated_redis):
    conversation = Conversation(user_id=alice.id, title="chat")
    db.add(conversation)
    db.commit()
    result = agent_say.deliver(db, user_id=alice.id, text="Got all five logs!", reasons=["goal_done"],
                               commentary_mode="occasional")
    assert result.startswith("Said")
    saved = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)).all()
    assert [(m.role, m.content) for m in saved] == [("assistant", "Got all five logs!")]
    channel, raw = isolated_redis.published[-1]
    assert channel == agent_say.channel(alice.id) and json.loads(raw)["text"] == "Got all five logs!"
    again = agent_say.deliver(db, user_id=alice.id, text="And more!", reasons=["goal_done"],
                              commentary_mode="occasional")
    assert again.startswith("Not said")  # a minute hasn't passed


# --- her background tools -----------------------------------------------------------


def test_background_tools_plan_as_her_and_only_undo_her_own_pauses(database, alice, monkeypatch):
    from app.ai.tools.desktop import tools as tool_module

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    with database() as db:
        session = _playing(db, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id, GameEvent(("goal_done",)))}
    assert "start_game" not in tools and "say" in tools
    asyncio.run(tools["set_game_plan"].ainvoke({"goal": "Make a crafting table", "steps": [
        {"do": "craft planks", "done_when": "planks in inventory"}]}))
    with database() as db:
        s = db.get(DesktopSession, session.id)
        assert s.plan["source"] == "columbina_planning_turn" and svc.auto_goals_set(db, s.id) == 1
        svc.pause_session(db, alice.id, s.id)  # the user's pause
    refused = asyncio.run(tools["resume_game"].ainvoke({}))
    assert refused.startswith("Not done: this pause wasn't yours")


def test_she_cannot_pick_goals_forever(database, alice, monkeypatch):
    from app.ai.tools.desktop import tools as tool_module
    from app.core.config import get_settings

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    capped = get_settings().model_copy(update={"desktop_max_auto_goals": 1, "desktop_agent_enabled": True})
    monkeypatch.setattr(desktop_game, "get_settings", lambda: capped)
    with database() as db:
        _playing(db, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id, GameEvent(("goal_done",)))}
    asyncio.run(tools["set_game_plan"].ainvoke({"goal": "one"}))
    second = asyncio.run(tools["set_game_plan"].ainvoke({"goal": "two"}))
    assert second.startswith("Not done: you've picked 1 goals yourself")


# --- the driver between goals ------------------------------------------------------


def _all_done(script):
    plan = json.loads(json.dumps(script.view_state.plan))
    for st in plan["steps"]:
        st["state"] = "done"
    script.view_state = replace(script.view_state, plan=plan)


def test_playing_on_her_own_the_driver_waits_for_her_next_goal():
    script = Script([WALK])
    script.view_state = replace(script.view_state, autonomy="continue")
    _all_done(script)
    run = _run(script, planning_wait_seconds=60)
    for _ in range(5):
        assert run.step() is True     # waiting, keys released (nothing sent)
    assert script.published == [] and script.pauses == []
    plan = json.loads(json.dumps(script.view_state.plan))
    plan["steps"].append({"id": "s3", "do": "craft planks", "done_when": "planks", "state": "pending"})
    script.view_state = replace(script.view_state, plan=plan, plan_revision=2)
    assert run.step() is True and len(script.published) == 1  # her new goal: playing again


def test_no_next_goal_in_time_pauses_with_planning_timeout():
    script = Script([])
    script.view_state = replace(script.view_state, autonomy="continue")
    _all_done(script)
    run = _run(script, planning_wait_seconds=5)
    while run.step():
        pass
    assert script.pauses == ["planning_timeout"]


def test_keep_playing_off_stops_at_the_end_of_the_goal():
    script = Script([])
    _all_done(script)  # autonomy defaults to "stop" in the fake view
    assert _run(script).step() is False and script.pauses == ["plan_complete"]
