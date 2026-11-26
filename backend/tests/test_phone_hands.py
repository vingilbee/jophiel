"""Her hands on the phone, apart from her mind (PLAN_PHONE_HANDS.md): the job record and its one writer per field,
versions, pause / resume / stop and the phone's turn, the hands' wakes and chat line, what her mind reads, the
hands' own loop, and which turns get which tools."""
from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from sqlalchemy import select

from app.ai import chat as ai_chat
from app.ai import hands as hands_loop
from app.ai.context import AgentRuntimeContext
from app.ai.tools import hands as mind_tools
from app.ai.tools import phone_control as screen_tools
from app.core.config import get_settings
from app.db.models import CoordinatorWork, HandsJob, HandsStep
from app.services import coordinator, hands, phone_actions, phone_control, phone_state
from app.workers import hands as hands_worker
from db_support import create_conversation, create_user, database  # noqa: F401

JPEG = base64.b64encode(b"\xff\xd8 a base \xff\xd9").decode()


def screen(sid: str = "s1", app: str = "Clash of Clans") -> dict:
    return {"id": sid, "app": app, "package": "com.supercell.clashofclans", "keyboard": False,
            "image": {"jpeg_base64": JPEG, "width": 1600, "height": 720},
            "items": [{"n": 1, "role": "button", "label": "Attack!"}]}


@pytest.fixture
def phone(database, monkeypatch):
    """A user and conversation, the phone's turn recorded in memory, nothing sent to a real phone."""
    with database() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    turns: list[str] = []
    sent: list[tuple[str, dict]] = []
    phone = None
    monkeypatch.setattr(phone_control, "begin_turn", lambda user_id, turn, **kwargs: turns.append(turn))
    monkeypatch.setattr(phone_control, "superseded", lambda user_id, turn, **kwargs: bool(turns) and turns[-1] != turn)
    monkeypatch.setattr(phone_control, "_epoch", lambda *args, **kwargs: 0)
    monkeypatch.setattr(phone_state, "read", lambda *args, **kwargs: None)
    monkeypatch.setattr(hands, "_poke", lambda: None)
    monkeypatch.setattr(coordinator, "_wake", lambda: None)

    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append((action, args))
        if action in ("end_control", "control_hold"):
            if phone.refuse_holds and action == "control_hold":
                return phone_actions.Outcome("refused", uuid.uuid4(), "Not done", reason="taken_over")
            return phone_actions.Outcome("done", uuid.uuid4(), "Done")
        return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=screen(f"s{len(sent)}"))

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(hands_loop, "HOLD_SECONDS", 3600.0)  # no holds unless a test asks for them
    lines: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(phone_actions, "record_line", lambda user_id, conversation_id, line_id, text:
                        lines.append((line_id, text)))
    phone = SimpleNamespace(user=user.id, conversation=conversation.id, db=database, turns=turns, sent=sent,
                            lines=lines, refuse_holds=False)
    return phone


def wakes(db, user_id) -> list[CoordinatorWork]:
    return list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.user_id == user_id)
                           .order_by(CoordinatorWork.created_at)))


def test_a_job_starts_with_its_own_turn_and_a_new_one_replaces_it(phone):
    first = hands.start(phone.user, phone.conversation, goal="Attack this base", instructions="Duke at a corner")
    assert first.status == "running" and first.version == 1 and phone.turns == [first.turn]
    second = hands.start(phone.user, phone.conversation, goal="Collect the loot")
    assert phone.turns == [first.turn, second.turn]
    with phone.db() as db:
        assert db.get(HandsJob, first.id).outcome == "replaced"
        assert hands.open_job(db, phone.user).id == second.id
    with pytest.raises(hands.HandsError):
        hands.start(phone.user, phone.conversation, goal="  ")


def test_her_changes_bump_the_version_the_hands_check_before_acting(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack", instructions="Duke at a corner")
    token = "t1"
    assert hands.claim_next(token) == job.id
    assert hands.claim_next("t2") is None  # held, and renewed
    assert hands.check(job.id, token, 1).verdict == "go"
    told = hands.tell(phone.user, "Warden last")
    assert told.version == 2 and told.instructions == "Duke at a corner\nWarden last"
    found = hands.check(job.id, token, 1)
    assert (found.verdict, found.version, found.instructions) == ("moved", 2, "Duke at a corner\nWarden last")
    assert hands.check(job.id, token, 2).verdict == "go"
    assert hands.tell(phone.user, "Only the Duke", replace=True).instructions == "Only the Duke"
    # Not theirs: another token finds nothing to do.
    assert hands.check(job.id, "someone else", 3).verdict == "halt"


def test_a_pause_commits_then_moves_the_phones_turn_and_resume_takes_a_new_one(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")
    paused = hands.pause(phone.user, "they said wait")
    assert paused.status == "paused" and paused.paused_by == "her"
    assert phone.turns[-1].startswith("held-")  # a waiting action of the job stops waiting
    assert hands.check(job.id, "t1", 1).verdict == "halt"
    resumed = hands.resume(phone.user, "go on, Warden last")
    assert resumed.status == "running" and resumed.turn not in (job.turn,) and phone.turns[-1] == resumed.turn
    assert resumed.claim_token is None and "Warden last" in resumed.instructions
    assert hands.claim_next("t2") == job.id
    stopped = hands.stop(phone.user, "they changed their mind")
    assert stopped.status == "ended" and stopped.outcome == "stopped"
    with pytest.raises(hands.HandsError):
        hands.tell(phone.user, "anything")


def test_the_hands_events_wake_her_once_each_and_the_job_leaves_one_chat_line(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack this base")
    hands.claim_next("t1")
    hands.step(job.id, "t1", 0, "read the screen", "Phone screen s1: Clash of Clans in front", 1, app="Clash of Clans")
    hands.step(job.id, "t1", 1, "tapped [1]", None, 1)
    hands.step(job.id, "t1", 1, "tapped [1]", "Phone screen s2: Clash of Clans in front", 1)
    assert hands.report(job.id, "t1", "Two stars so far")
    assert hands.finish(job.id, "t1", "need_her", "Which base, the left or the right one?")
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        assert (row.status, row.paused_by, row.question, row.actions) == ("paused", "hands",
                                                                         "Which base, the left or the right one?", 2)
        assert row.apps == ["Clash of Clans"]
        found = wakes(db, phone.user)
        assert [w.input["origin"] for w in found] == [f"hands:{job.id}:report", f"hands:{job.id}:need_her"]
        assert found[0].input["hands"]["text"] == "Two stars so far"
        # Her moment: the job as it stands.
        text = "\n".join(hands.describe(db, row))
    assert "Job [" in text and "Attack this base" in text and "paused: your hands need you" in text
    assert "2. tapped [1] → Phone screen s2: Clash of Clans in front" in text
    assert "They need you: Which base" in text and "Two stars so far" in text
    rendered = coordinator.render_wake({"work_id": str(found[1].id), "input": found[1].input, "reason": "due"})
    assert "Your hands stopped and need you: Which base" in rendered and "stay_silent" in rendered
    # Paused, nothing more of theirs counts; resumed on a new turn, they finish.
    assert not hands.finish(job.id, "t1", "done", "late")
    hands.tell(phone.user, "The right one")
    hands.resume(phone.user)
    hands.claim_next("t2")
    assert hands.finish(job.id, "t2", "done", "Three stars on the right base")
    with phone.db() as db:
        assert db.get(HandsJob, job.id).outcome == "done"
        assert len(wakes(db, phone.user)) == 3
    assert phone.lines == [(hands.line_id(job.id), "Used your phone in Clash of Clans, 2 steps")]


def test_the_banner_stop_pauses_for_the_user_and_bounds_end_the_job(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")
    assert hands.finish(job.id, "t1", "user_stop", "they tapped Stop on the banner")
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        assert (row.status, row.paused_by) == ("paused", "user")
        assert "they tapped Stop on the banner" in "\n".join(hands.describe(db, row))
    hands.resume(phone.user)
    hands.claim_next("t2")
    with phone.db() as db:
        db.get(HandsJob, job.id).actions = hands.MAX_ACTIONS
        db.commit()
    assert hands.check(job.id, "t2", 2).verdict == "limit"
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        row.actions, row.resumed_at = 0, datetime.now(timezone.utc) - timedelta(seconds=hands.MAX_RUN_SECONDS + 5)
        db.commit()
    assert hands.check(job.id, "t2", 2).verdict == "limit"


def test_a_job_left_paused_ends_by_itself(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.pause(phone.user)
    later = datetime.now(timezone.utc) + timedelta(seconds=hands.PAUSED_MAX_SECONDS + 5)
    assert hands.expire_paused(now=later) == 1
    with phone.db() as db:
        assert db.get(HandsJob, job.id).outcome == "expired"
        assert hands.moment(phone.user, now=later) is not None  # recently ended: still in her moment
        assert hands.moment(phone.user, now=later + timedelta(seconds=hands.RECENT_SECONDS + 5)) is None


class ScriptedModel:
    """Her hands' model, scripted: each call returns the next reply; what each call read is kept."""

    def __init__(self, replies):
        self.replies, self.read = list(replies), []

    async def ainvoke(self, messages):
        self.read.append(messages)
        return self.replies.pop(0)


def call(name, args, n=1):
    return {"name": name, "args": args, "id": f"c{n}", "type": "tool_call"}


def test_the_hands_read_first_then_act_and_finish(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack this base", instructions="Duke at a corner")
    token = "t1"
    hands.claim_next(token)
    model = ScriptedModel([
        AIMessage("", tool_calls=[call("phone_tap", {"n": 1}, 1)]),
        AIMessage("", tool_calls=[call("report", {"note": "Battle started"}, 2)]),
        AIMessage("", tool_calls=[call("done", {"summary": "Attacked: two stars"}, 3)]),
    ])
    ended = asyncio.run(hands_loop.run_job(job.id, token, model_factory=lambda tools: model))
    assert ended == "done"
    # The screen was read in code before the first model call, which already had it.
    assert [a for a, _ in phone.sent] == ["read_screen", "tap", "end_control"]
    assert all(args["turn"] == job.turn for _, args in phone.sent)
    first = model.read[0]
    assert isinstance(first[0], SystemMessage) and "Attack this base" in first[0].content
    assert "Duke at a corner" in first[0].content
    assert any("Phone screen s1" in str(m.content) for m in first)
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        assert (row.status, row.outcome, row.summary, row.actions) == ("ended", "done", "Attacked: two stars", 2)
        steps = list(db.scalars(select(HandsStep).where(HandsStep.job_id == job.id).order_by(HandsStep.index)))
        assert [s.did for s in steps] == ["read the screen", "tapped [1]"]
        assert [w.input["hands"]["event"] for w in wakes(db, phone.user)] == ["report", "done"]


def test_her_pause_mid_run_ends_it_quietly(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")

    class Pausing(ScriptedModel):
        async def ainvoke(self, messages):
            if len(self.read) == 0:
                await asyncio.to_thread(hands.pause, phone.user, "wait")
            return await super().ainvoke(messages)

    model = Pausing([AIMessage("", tool_calls=[call("phone_tap", {"n": 1})])])
    assert asyncio.run(hands_loop.run_job(job.id, "t1", model_factory=lambda tools: model)) == "quiet"
    assert [a for a, _ in phone.sent if a != "end_control"] == ["read_screen"]  # the tap was never sent
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        assert (row.status, row.claim_token) == ("paused", None)
        assert wakes(db, phone.user) == []  # her own pause wakes nobody


def test_a_model_that_breaks_ends_the_job_and_she_is_told(phone):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")

    class Broken:
        async def ainvoke(self, messages):
            raise RuntimeError("boom")

    assert asyncio.run(hands_loop.run_job(job.id, "t1", model_factory=lambda tools: Broken())) == "failed"
    with phone.db() as db:
        assert db.get(HandsJob, job.id).outcome == "failed"
        assert [w.input["hands"]["event"] for w in wakes(db, phone.user)] == ["failed"]


def test_the_worker_runs_a_claimed_job_on_its_own_thread(phone, monkeypatch):
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    ran = []

    async def run_job(job_id, token):
        ran.append((job_id, token))

    monkeypatch.setattr(hands_loop, "run_job", run_job)
    assert hands_worker.run_once()
    for thread in list(hands_worker._running.values()):
        thread.join(timeout=5)
    assert ran and ran[0][0] == job.id
    assert not hands_worker.run_once()  # still claimed (its run would renew it)


def test_the_window_keeps_recent_turns_and_shortens_old_screens():
    from langchain_core.messages import ToolMessage
    window = []
    for i in range(hands_loop.WINDOW_GROUPS + 3):
        window += [AIMessage("", tool_calls=[call("phone_tap", {"n": 1}, i)]),
                   ToolMessage(f"Phone screen s{i}: Clash of Clans in front.\n[1] button", tool_call_id=f"c{i}",
                               name="phone_tap")]
    kept = hands_loop._trim(window)
    assert len(kept) == 2 * hands_loop.WINDOW_GROUPS and isinstance(kept[0], AIMessage)
    screens = [m.content for m in kept if isinstance(m, ToolMessage)]
    assert all("[1] button" in c for c in screens[-hands_loop.FULL_SCREENS:])
    assert all(c.endswith("(older screen)") for c in screens[:-hands_loop.FULL_SCREENS])


def test_her_read_while_the_hands_work_is_their_latest_look(phone):
    hands.start(phone.user, phone.conversation, goal="Attack")
    phone_control.remember(phone.user, screen("s9"), "Phone screen s9: Clash of Clans in front.\n  Loot 1200")
    named = {t.name: t for t in mind_tools.build_hands_tools(phone.user, phone.conversation, reply=True)}
    said = asyncio.run(named["read_phone_screen"].ainvoke({}))
    assert "Your hands' latest look" in said and "Loot 1200" in said
    assert phone.sent == []  # nothing taken from the job
    said = asyncio.run(named["use_phone"].ainvoke({"goal": "Collect the loot", "instructions": "then home"}))
    assert said.startswith("Started: your hands are on it")
    assert asyncio.run(named["tell_hands"].ainvoke({"instructions": "Warden last"})).startswith("Told them")
    assert asyncio.run(named["stop_hands"].ainvoke({})).startswith("Stopped")
    assert asyncio.run(named["pause_hands"].ainvoke({})) == "Not done: no job is open on their phone."


def test_hands_tools_on_her_turns_and_no_screen_tools_on_her_orchestrator(monkeypatch):
    settings = get_settings().model_copy(update={"redis_url": "redis://unused"})
    monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
    monkeypatch.setattr(ai_chat, "build_agent", lambda tools, **kwargs: {tool.name for tool in tools})
    monkeypatch.setattr(ai_chat, "get_agent", lambda *args: set())
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4(),
                                turn_id=uuid.uuid4(), phone="Notifications: shared")

    async def registered(context):
        async with ai_chat._orchestrator_for(context) as (names, _):
            return names, ai_chat._run_config(context)

    names, config = asyncio.run(registered(reply))
    assert set(mind_tools.TOOL_NAMES) <= names and config is None
    assert not {"phone_tap", "phone_tap_points", "phone_swipe", "phone_press"} & names
    # A wake (her hands' own, say): she can tell them more, pause or stop them; never start or resume a job.
    names, _ = asyncio.run(registered(replace(reply, source_message_id=None, wake="hands done")))
    assert {"tell_hands", "pause_hands", "stop_hands"} <= names
    assert not {"use_phone", "resume_hands", "read_phone_screen"} & names
    # Nothing on a turn someone else's words caused, a nudge, or without a phone.
    for other in (replace(reply, third_party=True), replace(reply, nudge="say hello"), replace(reply, phone=None)):
        names, _ = asyncio.run(registered(other))
        assert not set(mind_tools.TOOL_NAMES) & names
    assert set(screen_tools.TOOL_NAMES).isdisjoint({"use_phone", "tell_hands"})


def test_the_hands_section_is_in_her_moment():
    from app.ai.prompts import build_prompt_parts
    context = AgentRuntimeContext(uuid.uuid4(), phone="Notifications: shared",
                                  hands="Job [abc]: Attack this base\nState: running")
    _, moment = build_prompt_parts(context)
    assert "## Your hands on their phone" in moment and "Job [abc]: Attack this base" in moment
    _, moment = build_prompt_parts(replace(context, hands=None))
    assert "## Your hands on their phone" not in moment


def test_a_job_is_claimable_only_once_its_phone_turn_is_taken(phone, monkeypatch):
    """Otherwise its first read would find the phone on an older turn and be taken for the user's Stop."""
    seen = []
    monkeypatch.setattr(phone_control, "begin_turn", lambda user_id, turn, **kwargs: seen.append(
        hands.claim_next("too-early")))
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    assert seen == [None]
    assert hands.claim_next("t1") == job.id
    hands.pause(phone.user)
    seen.clear()
    hands.resume(phone.user)
    assert seen == [None]  # the resume's own turn: not claimable before it
    assert hands.claim_next("t2") == job.id


class Slow(ScriptedModel):
    """Thinks a while before each reply."""

    async def ainvoke(self, messages):
        await asyncio.sleep(0.3)
        return await super().ainvoke(messages)


def test_the_banner_is_held_up_while_the_hands_think(phone, monkeypatch):
    monkeypatch.setattr(hands_loop, "HOLD_SECONDS", 0.05)
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")
    model = Slow([AIMessage("", tool_calls=[call("done", {"summary": "ok"})])])
    assert asyncio.run(hands_loop.run_job(job.id, "t1", model_factory=lambda tools: model)) == "done"
    holds = [args for action, args in phone.sent if action == "control_hold"]
    assert holds and all(args == {"turn": job.turn} for args in holds)
    assert phone.sent[0][0] == "read_screen" and phone.sent[-1][0] == "end_control"


def test_a_hold_refused_for_the_jobs_own_turn_is_the_users_stop(phone, monkeypatch):
    monkeypatch.setattr(hands_loop, "HOLD_SECONDS", 0.05)
    phone.refuse_holds = True
    job = hands.start(phone.user, phone.conversation, goal="Attack")
    hands.claim_next("t1")
    model = Slow([AIMessage("", tool_calls=[call("phone_tap", {"n": 1})])])
    assert asyncio.run(hands_loop.run_job(job.id, "t1", model_factory=lambda tools: model)) == "user_stop"
    assert "tap" not in [action for action, _ in phone.sent]  # learned while thinking: the tap never went out
    with phone.db() as db:
        row = db.get(HandsJob, job.id)
        assert (row.status, row.paused_by) == ("paused", "user")
        assert [w.input["hands"]["event"] for w in wakes(db, phone.user)] == ["user_stop"]
