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
from app.core.config import get_settings
from app.db.models import AgentEventJob, ChatMessage, Conversation, DesktopSession
from app.services import agent_events, agent_say, desktop_game
from app.services import desktop_sessions as svc
from tests.db_support import database  # noqa: F401  (fixture)
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
    assert "stuck: wall" in agent_events.reasons_of(_jobs(db)[-1])
    svc.resume_session(db, alice.id, session.id, expected_state_revision=db.get(DesktopSession, session.id).state_revision)
    svc.controller_pause(db, session.id, reason="not_foreground")
    assert "paused:not_foreground" in agent_events.reasons_of(_jobs(db)[-1])


def test_the_users_own_pause_does_not_wake_her(db, alice):
    session = _playing(db, alice)
    svc.pause_session(db, alice.id, session.id)
    assert _jobs(db) == []


def test_with_her_mind_on_commentary_wakeups_are_left_to_it_but_control_still_wakes(db, alice, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "desktop_agent_enabled", True)
    session = _playing(db, alice)
    svc.driver_noticed(db, session.id, kind="danger", text="creeper behind you")
    svc.driver_noticed(db, session.id, kind="warning", text="the sun is setting")
    assert _jobs(db) == []  # her mind sees these notices every tick
    svc.driver_noticed(db, session.id, kind="struggling", text="mine failed 4 times")
    assert _jobs(db) == []  # a step that keeps failing is her slow thinking's (services/mind_hard.py)
    svc.driver_noticed(db, session.id, kind="stuck", text="no skill for: crossing water")
    assert agent_events.reasons_of(_jobs(db)[-1]) == ["stuck: no skill for: crossing water"]


# --- the user's commentary setting (pace is hers to judge) -------------------------------------------


@pytest.mark.parametrize(("reasons", "mode", "ok"), [
    (["needs_human"], "quiet", True),              # always
    (["goal_done"], "quiet", False),
    (["paused:not_foreground"], "quiet", True),    # she stopped on her own: they should know
    (["goal_done"], "occasional", True),
    (["plan_low"], "occasional", False),           # not a kind this mode speaks about
    (["notable: iron"], "commentary", True),
    (["stuck: no skill for: pillar up"], "occasional", True),
    (["struggling: 4 failed tries"], "quiet", True),  # she may need to ask
    (["warning: the sun is setting"], "occasional", True),
    (["warning: the sun is setting"], "quiet", False),
    (["danger: skeleton 5 m west (behind-right)"], "occasional", True),
    (["danger: skeleton 5 m west (behind-right)"], "quiet", False),
])
def test_the_commentary_setting_is_the_users_rule_and_has_no_pace_rule(reasons, mode, ok):
    assert agent_say.allowed(reasons, mode)[0] is ok


@pytest.fixture
def speaking(database, db, alice, isolated_redis, monkeypatch):
    """Her own words through common delivery: queued for the session's conversation, then delivered by the
    chat worker (``autonomous_speech.deliver_worded`` -> ``workers/chat._deliver_worded``)."""
    from app.services import autonomous_speech, chat as chat_service, coordinator
    from app.workers import chat as chat_worker

    monkeypatch.setattr(chat_worker, "SessionLocal", database)
    monkeypatch.setattr(coordinator, "SessionLocal", database)
    monkeypatch.setattr(chat_worker, "_redis", lambda: isolated_redis)
    session = _playing(db, alice)
    conversation = Conversation(user_id=alice.id, title="chat")
    db.add(conversation)
    db.commit()
    session.conversation_id = conversation.id
    db.commit()

    def say(text, reasons, mode="occasional"):
        result = autonomous_speech.deliver_worded(db, user_id=alice.id, activity_id=session.id, source="minecraft",
                                                  text=text, reasons=reasons, commentary_mode=mode)
        with database() as other:
            job = chat_service.claim_next_job(other, lease_seconds=60)
        if job is not None:
            chat_worker._deliver_worded(job)
        return result

    def saved():
        db.expire_all()
        return [(m.role, m.content) for m in db.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.conversation_sequence))]
    return type("Speaking", (), {"say": staticmethod(say), "saved": staticmethod(saved),
                                 "conversation": conversation, "session": session})


def test_no_time_gap_holds_her_words_back_only_the_users_setting_does(speaking):
    assert speaking.say("A spider!", ["danger: spider 6 m south"]).startswith("Queued")
    assert speaking.say("Ouch.", ["danger: took damage: 4 of 10 hearts left"]).startswith("Queued")
    assert speaking.say("Shh.", ["goal_done"], mode="quiet").startswith("Not said")  # their rule
    assert speaking.saved() == [("assistant", "A spider!"), ("assistant", "Ouch.")]


@pytest.mark.parametrize(("reasons", "cut"), [
    (["danger: zombie 2 m ahead"], True),
    (["danger: died"], True),
    (["needs_human"], True),
    (["goal_done"], False),
    (["discovery: a village 70 m north"], False),
    (["paused:plan_complete"], False),
])
def test_urgent_messages_cut_her_off(reasons, cut):
    assert agent_say.interrupts(reasons) is cut


def test_published_message_says_whether_to_interrupt(speaking, isolated_redis):
    speaking.say("Ow, zombie!", ["danger: took damage: 2 of 10 hearts left"])
    assert json.loads(isolated_redis.published[-1][1])["interrupt"] is True


def test_saying_saves_the_message_in_the_sessions_conversation_and_publishes_it(speaking, db, alice,
                                                                                 isolated_redis):
    newer = Conversation(user_id=alice.id, title="a newer chat")  # never "the latest conversation"
    db.add(newer)
    db.commit()
    result = speaking.say("Got all five logs!", ["goal_done"])
    assert result.startswith("Queued")
    assert speaking.saved() == [("assistant", "Got all five logs!")]
    assert db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == newer.id)).all() == []
    channel, raw = isolated_redis.published[-1]
    assert channel == agent_say.channel(alice.id) and json.loads(raw)["text"] == "Got all five logs!"
    again = speaking.say("And more!", ["goal_done"])
    assert again.startswith("Queued")  # how often she speaks is hers to judge, not a timer's


def test_queued_words_wait_behind_a_reply_and_drop_if_the_user_speaks_first(speaking, database, db, alice):
    from app.services import autonomous_speech, chat as chat_service
    from app.workers import chat as chat_worker

    reply_turn = uuid.uuid4()
    chat_service.enqueue_job(db, user_id=alice.id, conversation=speaking.conversation, turn_id=reply_turn,
                             payload={"content": "hi"}, commit=True)
    with database() as other:
        replying = chat_service.claim_next_job(other, lease_seconds=60)  # her reply is in flight
    assert replying.turn_id == reply_turn
    assert autonomous_speech.deliver_worded(db, user_id=alice.id, activity_id=speaking.session.id,
                                            source="minecraft", text="Got the logs!", reasons=["goal_done"],
                                            commentary_mode="occasional").startswith("Queued")
    with database() as other:
        assert chat_service.claim_next_job(other, lease_seconds=60) is None  # one job per conversation
        chat_service.finish_job(other, replying.id, "completed", claim_token=replying.claim_token)
    chat_service.add_message(db, conversation=speaking.conversation, role="user", content="wait, stop")
    with database() as other:
        queued = chat_service.claim_next_job(other, lease_seconds=60)
    chat_worker._deliver_worded(queued)
    assert speaking.saved() == [("user", "wait, stop")]  # the user spoke after it was queued: not said


def test_she_is_told_how_recently_she_and_they_spoke_as_facts_not_rules(speaking, db):
    """What her mind reads before speaking unasked: the user's setting and the recent pace, as facts."""
    from app.db.models import CoordinatorWork
    from app.services import autonomous_speech

    db.add(ChatMessage(conversation_id=speaking.conversation.id, role="user", content="can you climb that?"))
    db.commit()
    work = CoordinatorWork(user_id=speaking.session.owner_user_id, conversation_id=speaking.conversation.id,
                           input={"source": "minecraft", "commentary_mode": "quiet"})
    facts = autonomous_speech.facts(db, work)
    assert any("commentary setting for this game: quiet" in f and "only when you need them" in f for f in facts)
    assert any(f.startswith("Their last message here was") for f in facts)


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


def test_news_about_a_plan_that_is_gone_is_dropped(db, alice):
    """Seen live: "on the last step" turns about her bed plan ran after the user said
    "go to the cave", and set the bed plan back."""
    from app.services import game_events

    session = _playing(db, alice, steps=2)
    svc.driver_progress(db, session.id, plan_revision=1, step_id="s1", status="step_done")
    assert _jobs(db)[-1].payload["plan_revision"] == 1
    reasons = ["plan_low", "danger: creeper 8 m north"]
    assert game_events.current_reasons(reasons, 1, 1) == reasons
    assert game_events.current_reasons(reasons, 1, 2) == ["danger: creeper 8 m north"]
    assert game_events.current_reasons(["plan_low", "goal_done"], 1, 2) == []


def test_her_background_plan_never_overrides_the_users_newer_one(database, alice, monkeypatch):
    from app.ai.tools.desktop import tools as tool_module

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    with database() as db:
        session = _playing(db, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id, GameEvent(("stuck",), plan_revision=1))}
    # Her own changes during the turn don't block her next ones.
    assert asyncio.run(tools["set_game_plan"].ainvoke({"goal": "Place my bed"})).startswith("Done")
    assert asyncio.run(tools["add_game_steps"].ainvoke({"steps": [
        {"do": "sleep", "done_when": "it's morning"}]})).startswith("Added")
    with database() as db:
        desktop_game.set_plan(db, alice.id, goal="Go to the cave")  # the user, meanwhile
    refused = asyncio.run(tools["set_game_plan"].ainvoke({"goal": "Sleep in my bed"}))
    assert refused.startswith("Not done: while you were thinking, the user changed your plan to \"Go to the cave\"")
    assert asyncio.run(tools["add_game_steps"].ainvoke({"steps": [
        {"do": "sleep", "done_when": "it's morning"}]})).startswith("Not done: while you were thinking")
    with database() as db:
        assert db.get(DesktopSession, session.id).plan["goal"] == "Go to the cave"


def test_on_the_last_step_she_adds_steps_but_never_replaces_the_plan(database, alice, monkeypatch):
    from app.ai.tools.desktop import tools as tool_module

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    with database() as db:
        _playing(db, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id, GameEvent(("plan_low",), plan_revision=1))}
    assert "add_game_steps" in asyncio.run(tools["set_game_plan"].ainvoke({"goal": "Something else"}))
    assert asyncio.run(tools["add_game_steps"].ainvoke({"steps": [
        {"do": "craft planks", "done_when": "planks in inventory"}]})).startswith("Added")


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


def test_the_goal_cap_restarts_when_the_user_joins_in(database, alice, monkeypatch):
    """Seen live: counted over a whole evening, the cap ran out and every finished
    goal after it paused, while the user was right there playing."""
    from app.ai.tools.desktop import tools as tool_module
    from app.core.config import get_settings

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    capped = get_settings().model_copy(update={"desktop_max_auto_goals": 1, "desktop_agent_enabled": True})
    monkeypatch.setattr(desktop_game, "get_settings", lambda: capped)
    with database() as db:
        session = _playing(db, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id, GameEvent(("goal_done",)))}
    asyncio.run(tools["set_game_plan"].ainvoke({"goal": "one"}))
    with database() as db:
        assert svc.auto_goals_set(db, session.id) == 1
        desktop_game.set_plan(db, alice.id, goal="what the user asked for")  # their turn
        assert svc.auto_goals_set(db, session.id) == 0
    assert asyncio.run(tools["set_game_plan"].ainvoke({"goal": "two"})).startswith("Done")
    with database() as db:
        s = svc.pause_session(db, alice.id, session.id).session
        assert svc.auto_goals_set(db, s.id) == 1
        svc.resume_session(db, alice.id, s.id, expected_state_revision=s.state_revision)  # their resume
        assert svc.auto_goals_set(db, s.id) == 0


def test_a_finished_plan_cannot_be_resumed(db, alice):
    """Seen live: Resume on a finished plan paused again within a second, 7 times."""
    session = _playing(db, alice, steps=1)
    step = session.plan["steps"][0]["id"]
    svc.driver_progress(db, session.id, plan_revision=session.plan_revision, step_id=step, status="step_done")
    paused = svc.pause_session(db, alice.id, session.id, reason="plan_complete").session
    with pytest.raises(svc.DesktopError) as excinfo:
        svc.resume_session(db, alice.id, session.id, expected_state_revision=paused.state_revision)
    assert excinfo.value.code == "nothing_to_do"
    desktop_game.set_plan(db, alice.id, goal="chop more oak", steps=[
        {"do": "chop oak", "done_when": "gained 10 oak_log"}])  # a new goal resumes her own pause
    db.refresh(paused)
    assert paused.state == "running"


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


def test_her_reply_sees_her_conclusions_but_never_a_dropped_one(db, alice, monkeypatch):
    """Hard thinking (services/mind_hard.py) reaches her reply: conclusions with their kind, never a dropped one.
    A question from it reaches her as a wake of her mind (autonomous_speech), not as a line in the summary."""
    import time as time_

    from app.services import mind, mind_hard

    session = _playing(db, alice)
    monkeypatch.setattr(mind, "publish", lambda user_id, thought: None)
    mind.save_thought(db, user_id=alice.id, session_id=session.id, level="hard", text="The move kills them.",
                      topic="surprise: x", trigger="surprise", conclusion_kind="guess", thinking="it said failed",
                      for_hands="count the mutton")
    mind.save_thought(db, user_id=alice.id, session_id=session.id, level="hard", text="Dig straight up.",
                      status="dropped", topic="keeps failing: y", trigger="failing", conclusion_kind="decision",
                      dropped_reason="that step is over")
    mind_hard.add_intention(session.id, "keep 5 planks to get back down")
    lines = "\n".join(desktop_game._mind_lines(db, session, time_.time() + 1, for_reply=True))
    assert '"The move kills them." (thought over: a guess;' in lines
    assert "Dig straight up" not in lines
    assert "keep 5 planks" not in lines  # her intentions are in her working memory's view, not here
    assert "A question you wanted" not in lines


def test_a_counted_check_in_her_plan_is_kept_and_shown_back_to_her(db, alice):
    session = _playing(db, alice)
    desktop_game.set_plan(db, alice.id, goal="reach the top of the ice pillar", source="columbina_user_turn", steps=[
        {"do": "build up beside the pillar", "done_when": "on top of the pillar", "check": {"kind": "top_here"}},
        {"do": "wave", "done_when": "waved"}])
    db.refresh(session)
    steps = session.plan["steps"]
    assert steps[0]["check"] == {"kind": "top_here"} and "check" not in steps[1]
    lines = "\n".join(desktop_game._plan_lines(db, session))
    assert "(done when: on top of the pillar; counted: nothing next to you higher than where you stand)" in lines
