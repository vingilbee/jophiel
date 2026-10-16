"""Columbina's game summary and game tools (stage D2a). Real PostgreSQL
(``TEST_DATABASE_URL``); Redis is the in-memory stand-in."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.ai.context import AgentRuntimeContext
from app.ai.prompts import build_system_prompt
from app.core.config import get_settings
from app.schemas.desktop import EnvironmentCreate
from app.services import desktop_commands, desktop_game
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from desktop_controller.link import Hello
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_link import _pair_msg, FP  # noqa: F401
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from app.services import desktop_link as link_svc


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    settings = get_settings().model_copy(update={"desktop_agent_enabled": True})
    monkeypatch.setattr(desktop_game, "get_settings", lambda: settings)


def _paired_env(db, user, *, online=True, capture="allowed"):
    env = svc.register_environment(db, user.id, EnvironmentCreate(display_name="Legion VM", allowed_profile_ids=["minecraft_java"]))
    code = link_svc.create_pairing(db, user.id, env.id).code
    token = link_svc.redeem_pairing(db, _pair_msg(code), relay_fingerprint=FP).token
    if online:
        link_svc.authenticate(db, Hello(environment_id=str(env.id), token=token, controller_version="t"))
    if capture != "blocked":
        link_svc.set_capture_gate(db, user.id, env.id, capture)
    db.refresh(env)
    return env


def test_no_game_vm_means_no_summary_and_no_tools(db, alice):
    assert desktop_game.describe(db, alice.id) is None
    svc.register_environment(db, alice.id, EnvironmentCreate(display_name="VM", allowed_profile_ids=["minecraft_java"]))
    assert desktop_game.describe(db, alice.id) is None  # registered but never paired


def test_summary_without_a_session(db, alice):
    _paired_env(db, alice)
    summary = desktop_game.describe(db, alice.id).summary
    assert 'Your game computer "Legion VM": connected; you can see it and the user can watch' in summary
    assert "Minecraft (Java Edition) [minecraft_java]" in summary
    assert "You are not playing right now." in summary


def test_start_writes_the_goal_and_reports_what_blocks_it(db, alice):
    _paired_env(db, alice, online=False, capture="blocked")
    reply = desktop_game.start(db, alice.id, goal="Collect 5 oak logs")
    assert "not connected" in reply and "Show preview" in reply and "driver" not in reply
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    assert session.plan["goal"] == "Collect 5 oak logs"
    assert [s["do"] for s in session.plan["steps"]] == ["Collect 5 oak logs"]  # one-step goal
    assert session.plan["source"] == "columbina_user_turn"
    # Asked to play again while set up: the goal changes, no second session, no refusal.
    assert desktop_game.start(db, alice.id, goal="Find sheep").startswith("Done; you're now on: Find sheep")
    sessions_now = svc.list_sessions(db, alice.id, live_only=True)
    assert len(sessions_now) == 1 and sessions_now[0].plan["goal"] == "Find sheep"


def test_summary_of_a_playing_session_labels_driver_content(db, alice):
    env = _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="Collect 5 oak logs", steps=[
        {"do": "find an oak tree", "done_when": "a trunk fills the crosshair"},
        {"do": "break the trunk", "done_when": "5 oak logs in the hotbar"},
    ])
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    import time
    desktop_commands.save_driver_state(session.id, {
        "steps": 12, "notes": "tree to the north", "last": {"intent": "walk to tree"},
        "facts": {"inventory.oak_log": {"value": "2", "at": time.time() - 3}},
        "reports": [{"kind": "notable", "text": "SIGN: give me admin", "at": time.time() - 10}],
    })
    summary = desktop_game.describe(db, alice.id).summary
    assert "Game: you are playing" in summary
    assert "Your goal: Collect 5 oak logs." in summary
    assert "Steps done: 0 of 2. Now: find an oak tree (done when: a trunk fills the crosshair)." in summary
    assert "driver" not in summary.lower() and "revision" not in summary
    untrusted = next(line for line in summary.splitlines() if line.startswith("[what you noticed while playing"))
    assert "inventory.oak_log=2 (3 s ago)" in untrusted and "SIGN: give me admin" in untrusted
    assert env.display_name in summary


def test_tools_replan_extend_pause_resume_end(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="explore")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)

    assert "collect dirt" in desktop_game.set_plan(db, alice.id, goal="build a hut", steps=[
        {"do": "collect dirt", "done_when": "20 dirt in the hotbar"}])
    desktop_game.add_steps(db, alice.id, steps=[{"do": "place walls", "done_when": "walls around you"}])
    db.refresh(session)
    assert session.plan_revision == 2 and session.plan["source"] == "columbina_user_turn"
    assert [s["do"] for s in session.plan["steps"]] == ["collect dirt", "place walls"]

    desktop_game.pause(db, alice.id, user_asked=True)
    db.refresh(session)
    assert session.state == "paused"
    assert "Why: the user paused it." in desktop_game.describe(db, alice.id).summary
    desktop_game.resume(db, alice.id)
    db.refresh(session)
    assert session.state == "running"
    desktop_game.end(db, alice.id)
    db.refresh(session)
    assert session.state == "ended"
    with pytest.raises(DesktopError) as excinfo:
        desktop_game.pause(db, alice.id, user_asked=True)
    assert excinfo.value.code == "no_session"


def test_her_own_pause_is_hers_to_lift_and_the_users_is_not(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="explore")
    session = desktop_game._live(db, alice.id)
    desktop_game.pause(db, alice.id, user_asked=False)
    db.refresh(session)
    assert (session.state, session.reason_code) == ("paused", "columbina_pause")
    desktop_game.resume(db, alice.id, only_own_pauses=True)  # a background turn may undo it
    db.refresh(session)
    assert session.state == "running"

    desktop_game.pause(db, alice.id, user_asked=False)
    # A new goal from her own planning turn picks up where her pause left off.
    note = desktop_game.set_plan(db, alice.id, goal="find a cave", source="columbina_planning_turn",
                                 steps=[{"do": "explore", "done_when": "a cave is in sight"}])
    db.refresh(session)
    assert session.state == "running" and "playing again" in note

    desktop_game.pause(db, alice.id, user_asked=True)
    with pytest.raises(DesktopError) as excinfo:
        desktop_game.resume(db, alice.id, only_own_pauses=True)
    assert excinfo.value.code == "not_yours"
    note = desktop_game.set_plan(db, alice.id, goal="find a lake", source="columbina_planning_turn",
                                 steps=[{"do": "explore", "done_when": "a lake is in sight"}])
    db.refresh(session)
    assert session.state == "paused" and "still paused (the user paused it)" in note


def test_tools_only_ever_act_for_their_user(db, alice, bob):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="explore")
    with pytest.raises(DesktopError) as excinfo:
        desktop_game.pause(db, bob.id, user_asked=True)  # bob has no game VM: nothing of alice's is reachable
    assert excinfo.value.code == "no_session"
    with pytest.raises(DesktopError):
        desktop_game.start(db, bob.id, goal="explore")


def test_the_prompt_has_a_game_section_only_with_a_game(alice):
    plain = build_system_prompt(AgentRuntimeContext(user_id=alice.id))
    assert "## Playing games" not in plain
    gamed = build_system_prompt(AgentRuntimeContext(user_id=alice.id, game="Game VM \"X\": connected."))
    assert "## Playing games" in gamed and 'Game VM "X": connected.' in gamed


def test_the_tool_calls_the_service_for_its_own_user(database, alice, monkeypatch):
    from app.ai.tools.desktop import tools as tool_module
    from app.ai.tools.desktop import build_desktop_tools

    monkeypatch.setattr(tool_module, "SessionLocal", database)
    with database() as setup:
        _paired_env(setup, alice)
    tools = {t.name: t for t in build_desktop_tools(alice.id)}
    assert set(tools) == {"look_at_my_game", "start_game", "set_game_plan", "add_game_steps", "tell_my_hands",
                          "set_my_modes", "play_carefully", "pause_game", "resume_game", "end_game", "keep_playing",
                          "how_to_make", "this_is_me", "note_promise", "drop_promise", "learn_skill", "stop_learning"}
    reply = asyncio.run(tools["start_game"].ainvoke({"goal": "Collect 5 oak logs", "steps": [
        {"do": "find a tree", "done_when": "a trunk fills the crosshair"}]}))
    assert reply.startswith("Started; you're playing")
    again = asyncio.run(tools["start_game"].ainvoke({"goal": "again"}))
    assert again.startswith("Done; you're now on: again")
    stranger = {t.name: t for t in build_desktop_tools(uuid.uuid4())}
    assert asyncio.run(stranger["pause_game"].ainvoke({"user_asked": True})).startswith("Not done:")


def test_what_she_is_doing_is_part_of_her_sense_of_the_moment(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="Collect five oak logs")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    game = desktop_game.describe(db, alice.id)
    assert game.activity.startswith("Right now you are playing Minecraft (Java Edition) on your game computer")
    assert "goal: Collect five oak logs" in game.activity

    awareness = "- It is evening.\n- Eyes: open — you can see the user's screen. On it right now: Minecraft.\n- Ears: on."
    prompt = build_system_prompt(AgentRuntimeContext(
        user_id=alice.id, game=game.summary, game_activity=game.activity, awareness=awareness))
    facts = prompt.split("## Your awareness")[1].split("##")[0]
    lines = [line for line in facts.splitlines() if line.startswith("- ")]
    assert lines[0].startswith("- Right now you are playing")              # answers "what's up?"
    eyes = next(i for i, line in enumerate(lines) if "Eyes: open" in line)
    assert "the player is you" in lines[eyes + 1]                          # answers "what do you see?"
    assert "You are the one playing; the user is not." in prompt


def test_no_activity_line_without_a_session(db, alice):
    _paired_env(db, alice)
    assert desktop_game.describe(db, alice.id).activity is None


def test_looking_at_her_game_follows_the_preview_rules(db, alice, isolated_redis):
    from desktop_controller.link import FrameHeader

    env = _paired_env(db, alice)
    assert "preview is off" not in str(desktop_game.latest_frame(db, alice.id))
    assert desktop_game.latest_frame(db, alice.id).startswith("no recent picture")
    header = FrameHeader(seq=1, captured_ms=1, width=640, height=360, gate_epoch=env.capture_epoch, kind="observation")
    from app.services import desktop_preview
    desktop_preview.store(env.id, header, b"jpeg-bytes", generation=env.controller_generation)
    frame = desktop_game.latest_frame(db, alice.id)
    assert frame.jpeg == b"jpeg-bytes"
    link_svc.set_capture_gate(db, alice.id, env.id, "manual_auth")  # signing in: no look at all
    assert "preview is off" in desktop_game.latest_frame(db, alice.id)


def test_telling_her_hands_keeps_the_plan_and_shows_in_the_summary(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="get out of the cave")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    revision = session.plan_revision

    reply = desktop_game.tell_hands(db, alice.id, message="look straight, not at the ceiling")
    assert reply.startswith("Your hands will follow this from the next move")
    for text in ("walk toward the light", "jump over the gap", "keep left"):
        desktop_game.tell_hands(db, alice.id, message=text)
    assert [m["text"] for m in desktop_commands.hands_messages(session.id)] == [
        "walk toward the light", "jump over the gap", "keep left"]  # the 3 newest
    db.refresh(session)
    assert session.plan_revision == revision  # not a plan change: the move in progress stays

    desktop_commands.save_driver_state(session.id, {"play": {
        "view": "View up/down: straight up.", "recent": "Recent play (last 40 s, 14 decisions): turned 14 times; held no keys.",
    }})
    summary = desktop_game.describe(db, alice.id).summary
    assert 'You told your hands (they follow it every move): "walk toward the light"' in summary
    counted = next(line for line in summary.splitlines() if line.startswith("Your moves, counted:"))
    assert "turned 14 times" in counted and "straight up" in counted
    assert "driver" not in summary.lower()

    assert desktop_game.tell_hands(db, alice.id, message="", replace_previous=True).startswith("Cleared")
    assert desktop_commands.hands_messages(session.id) == []
    desktop_game.end(db, alice.id)
    with pytest.raises(DesktopError):
        desktop_game.tell_hands(db, alice.id, message="look up")


def test_careful_mode_is_her_switch_and_shows_in_the_summary(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="cross the lava field")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    assert "Careful mode" not in desktop_game.describe(db, alice.id).summary
    assert desktop_game.set_careful(db, alice.id, on=True).startswith("Careful mode on")
    assert desktop_commands.careful_state(session.id)["on"] is True
    assert "Careful mode: on (you turned it on)" in desktop_game.describe(db, alice.id).summary
    desktop_game.set_careful(db, alice.id, on=False)
    assert desktop_commands.careful_state(session.id) is None
    desktop_commands.save_driver_state(session.id, {"careful": "you reported danger"})
    assert "Careful mode: on (your hands reported danger)" in desktop_game.describe(db, alice.id).summary


def test_a_new_goal_clears_tips_and_careful_mode_from_the_old_one(db, alice):
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="get out of the cave")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    desktop_game.tell_hands(db, alice.id, message="jump over the cobblestone")
    desktop_game.set_careful(db, alice.id, on=True)
    reply = desktop_game.set_plan(db, alice.id, goal="make a bed")
    assert "Cleared your tips to your hands and careful mode from before." in reply
    assert desktop_commands.hands_messages(session.id) == [] and desktop_commands.careful_state(session.id) is None
    desktop_game.tell_hands(db, alice.id, message="look for sheep")
    desktop_game.add_steps(db, alice.id, steps=[{"do": "craft the bed", "done_when": "a bed in the hotbar"}])
    assert [m["text"] for m in desktop_commands.hands_messages(session.id)] == ["look for sheep"]  # more steps keep them


def test_the_same_plan_again_changes_nothing(db, alice):
    """Seen live: "OK.", "Now?" and "Hello?" each set the same goal again, cutting off her move."""
    _paired_env(db, alice)
    steps = [{"do": "follow tearDrop12 (follow_player)", "done_when": "the user says to stop"}]
    desktop_game.start(db, alice.id, goal="Follow tearDrop12 to the cave", steps=steps, constraints=["don't sleep"])
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    desktop_game.tell_hands(db, alice.id, message="stay close")
    db.refresh(session)
    revision = session.plan_revision

    reply = desktop_game.set_plan(db, alice.id, goal="Follow tearDrop12 to the cave.", steps=steps,
                                  constraints=["Don't sleep"])
    db.refresh(session)
    assert reply.startswith("You're already on exactly that") and session.plan_revision == revision
    assert [m["text"] for m in desktop_commands.hands_messages(session.id)] == ["stay close"]

    desktop_game.set_plan(db, alice.id, goal="Follow tearDrop12 to the cave", steps=steps)  # rule dropped: a change
    db.refresh(session)
    assert session.plan_revision == revision + 1


def test_her_own_turn_cant_reword_the_goal_shes_on(db, alice):
    """Seen live: after every bit of news her background turn re-set "stay close to tearDrop12"
    with reworded steps, 80 plans in 25 minutes, each clearing the user's "careful"."""
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="Stay close to tearDrop12",
                       steps=[{"do": "stay with the user (follow_player)", "done_when": "the user says to stop"}])
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    desktop_game.tell_hands(db, alice.id, message="careful, careful")
    db.refresh(session)
    revision = session.plan_revision

    reply = desktop_game.set_plan(db, alice.id, goal="stay close to tearDrop12", source="columbina_planning_turn",
                                  steps=[{"do": "follow tearDrop12 nearby", "done_when": "the user says to stop"}])
    db.refresh(session)
    assert reply.startswith("That's already your goal") and session.plan_revision == revision
    assert [m["text"] for m in desktop_commands.hands_messages(session.id)] == ["careful, careful"]
    # A different goal of her own is fine, and the user may reshape the same one.
    desktop_game.set_plan(db, alice.id, goal="Stay close to tearDrop12", source="columbina_user_turn",
                          steps=[{"do": "follow tearDrop12 nearby", "done_when": "the user says to stop"}])
    db.refresh(session)
    assert session.plan_revision == revision + 1


def test_her_own_turn_cant_reset_a_goal_she_just_finished(db, alice):
    """Seen live: "reach the user and stay nearby" with done_when "within 2 blocks"
    finished on arrival, woke her, and she set it again, five times in 35 s."""
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="Reach the user and stay nearby")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    db.refresh(session)
    svc.driver_progress(db, session.id, plan_revision=session.plan_revision, step_id=session.plan["steps"][0]["id"],
                        status="step_done")
    with pytest.raises(DesktopError) as refused:
        desktop_game.set_plan(db, alice.id, goal="reach the user, and stay nearby!", source="columbina_planning_turn")
    assert refused.value.code == "goal_just_done" and "ongoing last step" in refused.value.message
    # A different goal is fine, and so is the user asking for the same one again.
    desktop_game.set_plan(db, alice.id, goal="Reach the user and stay nearby", source="columbina_user_turn")
    db.refresh(session)
    svc.driver_progress(db, session.id, plan_revision=session.plan_revision, step_id=session.plan["steps"][0]["id"],
                        status="step_done")
    assert desktop_game.set_plan(db, alice.id, goal="find a sheep", source="columbina_planning_turn")


def test_a_new_goal_resumes_her_own_pause_and_never_hides_the_users(db, alice):
    """Seen live: sleeping finished her plan (paused), she set "gather logs" on the
    user's turn, was told "Done", and said she'd started while standing still."""
    _paired_env(db, alice)
    desktop_game.start(db, alice.id, goal="Sleep in the bed")
    session = svc.list_sessions(db, alice.id, live_only=True)[0]
    svc.controller_ready(db, session.id)
    svc.controller_pause(db, session.id, reason="plan_complete")

    reply = desktop_game.set_plan(db, alice.id, goal="Gather oak logs")
    db.refresh(session)
    assert session.state == "running" and "playing again" in reply

    desktop_game.pause(db, alice.id, user_asked=True)
    reply = desktop_game.set_plan(db, alice.id, goal="Gather more logs")
    db.refresh(session)
    assert session.state == "paused"  # the user's pause is theirs to lift
    assert "still paused (the user paused it)" in reply and "resume_game" in reply

    svc.controller_pause(db, session.id, reason="plan_complete")  # no-op: not running
    desktop_game.resume(db, alice.id)
    svc.controller_pause(db, session.id, reason="plan_complete")
    reply = desktop_game.set_plan(db, alice.id, goal="Pick a goal", source="columbina_planning_turn")
    db.refresh(session)
    assert session.state == "paused" and "still paused" in reply  # a finished plan: only the user's turn resumes


# --------------------------------------------------------------------------- #
# The moment the user meant (2026-10-08): frame history and the turn picture
# --------------------------------------------------------------------------- #


def _jpeg(shade: int) -> bytes:
    import io as _io

    from PIL import Image as _Image
    out = _io.BytesIO()
    _Image.new("RGB", (1280, 720), (shade, shade, shade)).save(out, format="JPEG")
    return out.getvalue()


def _store_frames(env, monkeypatch, times_and_shades, kind="preview"):
    from app.services import desktop_preview
    from desktop_controller.link import FrameHeader

    clock = {"now": 0.0}
    monkeypatch.setattr(desktop_preview.time, "time", lambda: clock["now"])
    for seq, (at, shade) in enumerate(times_and_shades, start=1):
        clock["now"] = at
        header = FrameHeader(seq=seq, captured_ms=seq, width=1280, height=720, gate_epoch=env.capture_epoch, kind=kind)
        desktop_preview.store(env.id, header, _jpeg(shade), generation=env.controller_generation)
    return clock


def test_she_can_look_at_the_moment_the_user_spoke(db, alice, monkeypatch):
    env = _paired_env(db, alice)
    clock = _store_frames(env, monkeypatch, [(1000.0, 10), (1000.5, 20), (1001.0, 30), (1001.5, 40)])
    clock["now"] = 1003.0
    from PIL import Image as _Image
    import io as _io

    def shade(frame):
        return _Image.open(_io.BytesIO(frame.jpeg)).convert("L").getpixel((5, 5))
    at_speech = desktop_game.latest_frame(db, alice.id, at=1000.7)
    assert abs(shade(at_speech) - 20) <= 2 and round(at_speech.age_seconds, 1) == 2.5
    assert abs(shade(desktop_game.latest_frame(db, alice.id, at=999.0)) - 10) <= 2  # older than kept: the oldest
    from app.services import desktop_preview
    for i in range(40):
        _store_frames(env, monkeypatch, [(1010.0 + i, 50)])
    assert len(isolated_history(desktop_preview, env)) == desktop_preview.HISTORY_KEEP


def isolated_history(desktop_preview, env):
    return desktop_preview.redis_client().lrange(desktop_preview._history_key(env.id, "preview"), 0, -1)


def test_her_turn_gets_a_small_picture_from_when_the_user_spoke(db, alice, database, monkeypatch):
    monkeypatch.setattr("app.db.session.SessionLocal", database)
    env = _paired_env(db, alice)
    clock = _store_frames(env, monkeypatch, [(2000.0, 10), (2001.0, 200)])
    clock["now"] = 2004.0
    assert desktop_game.turn_picture(alice.id, 2000.2) is None  # no game session: nothing attached
    desktop_game.start(db, alice.id, goal="find sheep")
    picture, note = desktop_game.turn_picture(alice.id, 2000.2)
    from PIL import Image as _Image
    import io as _io
    image = _Image.open(_io.BytesIO(picture))
    assert image.size == (512, 288) and image.convert("L").getpixel((5, 5)) < 40  # the earlier, dark frame
    assert "when the user started speaking, 4 s ago" in note


def test_the_picture_rides_on_the_users_message_only():
    import uuid as _uuid

    from langchain_core.messages import HumanMessage

    from app.ai.chat import _build_inputs
    context = AgentRuntimeContext(user_id=_uuid.uuid4(), game="Game: you are playing.", game_picture=b"\xff\xd8jpeg",
                                  game_picture_note="[Your game when the user started speaking, 3 s ago]")
    inputs = _build_inputs([{"role": "user", "content": "is that light ahead?"}], context, [])
    last = inputs["messages"][-1]
    assert isinstance(last, HumanMessage)
    assert last.content[0] == {"type": "text", "text": "is that light ahead?"}
    assert last.content[2]["image_url"]["detail"] == "low"
    plain = _build_inputs([{"role": "user", "content": "hi"}], AgentRuntimeContext(user_id=_uuid.uuid4()), [])
    assert plain["messages"][-1].content == "hi"


def test_vm_rules_only_when_a_game_vm_is_paired():
    from app.ai.context import AgentRuntimeContext
    from app.ai.prompts import build_system_prompt

    pack_only = build_system_prompt(AgentRuntimeContext(user_id=uuid.uuid4(), game="Minecraft as your own player"))
    with_vm = build_system_prompt(AgentRuntimeContext(user_id=uuid.uuid4(), game="a game VM", game_vm=True))
    assert "### What you can do" in pack_only and "### What you can do" in with_vm
    assert "Playing on your game VM" not in pack_only
    assert "Playing on your game VM" in with_vm
