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
    assert "stuck: wall" in agent_events.reasons_of(_jobs(db)[-1])
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


def test_with_her_mind_on_commentary_wakeups_are_left_to_it_but_control_still_wakes(db, alice, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mind_enabled", True)
    monkeypatch.setattr(settings, "desktop_agent_enabled", True)
    session = _playing(db, alice)
    svc.driver_noticed(db, session.id, kind="danger", text="creeper behind you")
    svc.driver_noticed(db, session.id, kind="warning", text="the sun is setting")
    assert _jobs(db) == []  # her mind sees these notices every tick
    svc.driver_noticed(db, session.id, kind="struggling", text="mine failed 4 times")
    assert agent_events.reasons_of(_jobs(db)[-1]) == ["struggling: mine failed 4 times"]


# --- when she may speak ---------------------------------------------------------------


@pytest.mark.parametrize(("reasons", "mode", "since", "ok"), [
    (["needs_human"], "quiet", 1, True),           # always
    (["goal_done"], "quiet", None, False),
    (["paused:not_foreground"], "quiet", None, True),
    (["goal_done"], "occasional", None, True),
    (["goal_done"], "occasional", 10, False),      # too soon after the last one
    (["plan_low"], "occasional", None, False),     # not worth interrupting for
    (["notable: iron"], "commentary", 30, True),
    (["stuck: no skill for: pillar up"], "occasional", 8, True),  # blocked: never held back by the gap
    (["struggling: 4 failed tries"], "quiet", 2, True),          # nor by quiet: she may need to ask
    (["warning: the sun is setting"], "occasional", None, True),  # a heads-up: worth a line
    (["warning: zombie 4 m from alice"], "occasional", 10, False),  # but not right after the last one
    (["warning: the sun is setting"], "quiet", None, False),
    (["warning: the sun is setting"], "occasional", 25, True),      # the gap is 20 s
    (["danger: skeleton 5 m west (behind-right)"], "occasional", 10, True),  # danger: she reacts
    (["danger: skeleton 5 m west (behind-right)"], "occasional", 5, False),  # but one line per 8 s
    (["danger: skeleton 5 m west (behind-right)"], "quiet", None, False),
])
def test_occasional_means_occasional(reasons, mode, since, ok):
    assert agent_say.allowed(reasons, mode, since)[0] is ok


@pytest.mark.parametrize(("reasons", "which"), [
    (["danger: died"], "died"),
    (["danger: died with skeleton close by; dropped where you fell (gone in 5 min): bread x4"], "died"),
    (["danger: took damage: 2.5 of 10 hearts left"], "low_health"),
    (["danger: took damage: health 5/20"], "low_health"),  # the older wording
    (["danger: took damage: 4 of 10 hearts left"], None),
    (["danger: spider 6 m south (ahead-right)"], None),
    (["goal_done"], None),
])
def test_what_counts_as_serious(reasons, which):
    assert agent_say.serious(reasons) == which


def test_a_fresh_serious_moment_skips_the_gap_but_not_quiet():
    assert agent_say.allowed(["danger: died"], "occasional", 5)[0] is False
    assert agent_say.allowed(["danger: died"], "occasional", 5, serious_fresh=True)[0] is True
    assert agent_say.allowed(["danger: died"], "quiet", 5, serious_fresh=True)[0] is False


def test_death_is_said_right_after_another_message_but_only_once(db, alice, isolated_redis):
    db.add(Conversation(user_id=alice.id, title="chat"))
    db.commit()
    say = lambda text, reasons: agent_say.deliver(db, user_id=alice.id, text=text, reasons=reasons,  # noqa: E731
                                                  commentary_mode="occasional")
    assert say("A spider!", ["danger: spider 6 m south"]).startswith("Said")
    assert say("Ouch.", ["danger: took damage: 4 of 10 hearts left"]).startswith("Not said")
    assert say("I died!", ["danger: died"]).startswith("Said")  # within the minute, still said
    assert say("Died again?", ["danger: died"]).startswith("Not said")  # the same moment twice


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


def test_published_message_says_whether_to_interrupt(db, alice, isolated_redis):
    db.add(Conversation(user_id=alice.id, title="chat"))
    db.commit()
    agent_say.deliver(db, user_id=alice.id, text="Ow, zombie!", reasons=["danger: took damage: 2 of 10 hearts left"],
                      commentary_mode="occasional")
    assert json.loads(isolated_redis.published[-1][1])["interrupt"] is True


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


def test_her_minds_own_lines_wait_while_the_user_has_just_spoken(db, alice, isolated_redis):
    """Live: almost every message got two answers, a quick thought's and the reply."""
    from datetime import datetime, timedelta

    conversation = Conversation(user_id=alice.id, title="chat")
    db.add(conversation)
    db.commit()
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content="can you climb that?"))
    db.commit()
    say = lambda reasons: agent_say.deliver(db, user_id=alice.id, text="On it!", reasons=reasons,  # noqa: E731
                                            commentary_mode="occasional")
    assert say(["light: the peak is close"]).startswith("Not said: the user just spoke")
    assert say(["thought: the user said: can you climb that?"]).startswith("Not said: the user just spoke")
    assert say(["react: creeper behind me"]).startswith("Said")  # danger still speaks
    db.execute(ChatMessage.__table__.update().where(ChatMessage.role == "user").values(
        created_at=datetime.now() - timedelta(hours=12)))  # long ago, in any clock
    db.commit()
    assert not agent_say._user_spoke_within(db, alice.id, agent_say.MIND_HUSH_SECONDS)


def test_an_unfinished_thought_is_handed_on_once_briefly_and_never_nested(db, alice, database, monkeypatch):
    """Live: unfinished topics nested inside each other, so a user message from minutes before rode along,
    and a stale "learning: try 2 of 4" made her say "still practising" after the move had worked."""
    from app.db.models import MindThought
    from app.services import mind

    session = _playing(db, alice)
    enqueued = []
    monkeypatch.setattr(mind, "SessionLocal", database)
    monkeypatch.setattr(agent_events, "enqueue", lambda db_, **kw: enqueued.append(kw["reason"]))
    mind.save_thought(db, user_id=alice.id, session_id=session.id, level="hard", status="unfinished",
                      text="(didn't get to think this through: learning: try 2 of 4)",
                      topic="learning: try 2 of 4")
    now = db.scalar(select(MindThought.created_at)).timestamp()
    mind._think_hard(db, session, "your hands: go_to -> idle", now + 5)
    assert enqueued[-1].startswith("thought: your hands: go_to -> idle (unfinished from before, 5 s ago, "
                                   "so check it still holds before acting on it: learning: try 2 of 4)")
    # handed on once: the next thought doesn't carry it again
    mind._think_hard(db, session, "news: the move worked", now + 8)
    assert enqueued[-1] == "thought: news: the move worked"
    # the carrying thought itself unfinished: only its own topic is kept
    mind.hard_thought_done(alice.id, session.id, enqueued[0].partition(":")[2].strip(), None)
    db.expire_all()
    topics = db.scalars(select(MindThought.topic).where(MindThought.status == "unfinished")).all()
    assert topics == ["your hands: go_to -> idle"]
    # and an old unfinished one isn't carried at all
    latest = db.scalar(select(MindThought.created_at).order_by(MindThought.created_at.desc()).limit(1))
    mind._think_hard(db, session, "news: dusk", latest.timestamp() + mind.UNFINISHED_FOR.total_seconds() + 5)
    assert enqueued[-1] == "thought: news: dusk"


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
