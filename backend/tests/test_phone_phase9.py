"""Phase 9, phone control (frontend-phone/PLAN_PHASE_9.md): a fake phone round-trips screens over the real socket
and result endpoint; turns, takeovers, limits, the owner's say-so, short-term screen memory and the pictures."""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.ai import chat as ai_chat
from app.ai.context import AgentRuntimeContext
from app.ai.tools import phone_control as control_tools
from app.ai.tools.desktop_control import screenshot_hook
from app.core.config import get_settings
from app.schemas.phone import PhoneActionResult
from app.services import fcm, phone_actions, phone_control, phone_state
from db_support import database  # noqa: F401
from test_phone_actions import channel, headers, receive, result_url, wait_for_phone  # noqa: F401

JPEG = base64.b64encode(b"\xff\xd8 a picture of Settings \xff\xd9").decode()


def screen(sid: str = "s1", app: str = "Settings", *, image: bool = True, items: list | None = None) -> dict:
    return {
        "id": sid, "app": app, "package": "com.android.settings", "keyboard": False,
        "image": {"jpeg_base64": JPEG, "width": 720, "height": 1600} if image else None,
        "image_missing": None if image else "secure",
        "items": items if items is not None else [
            {"n": 1, "role": "text_field", "text": "", "hint": "Search settings", "focused": True,
             "box": [24, 180, 672, 96]},
            {"n": 2, "role": "switch", "label": "Hotspot", "checked": False, "box": [600, 900, 96, 96]},
            {"role": "text", "text": "Secret recent search"},
        ],
    }


def tools_by_name(tools):
    return {t.name: t for t in tools}


def run_tool(executor, tool, args):
    return executor.submit(asyncio.run, tool.ainvoke({"type": "tool_call", "id": "c1", "name": tool.name,
                                                      "args": args}))


def test_a_fake_phone_round_trips_screens_and_the_turn_leaves_one_line(channel, monkeypatch):
    monkeypatch.setattr(fcm, "send", lambda *args, **kwargs: pytest.fail("The action reached a live socket"))
    turn = uuid.uuid4()
    tools, control = control_tools.build_phone_control_tools(channel.user, channel.conversation, turn_id=turn)
    named = tools_by_name(tools)
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        wait_for_phone(channel.user)
        with ThreadPoolExecutor() as executor:
            # Acting before reading the screen (and so before the user's say-so) is refused without sending.
            early = asyncio.run(named["phone_tap"].ainvoke({"n": 1}))
            assert early.startswith("Not done: start with read_phone_screen")

            pending = run_tool(executor, named["read_phone_screen"], {"wait_ms": 9000})
            frame = receive(ws, "phone_action")
            assert frame["action"] == "read_screen"
            assert frame["args"] == {"wait_ms": 5000, "turn": str(turn)}
            with channel.bus.pubsub() as listener:
                listener.subscribe(phone_actions._reply_channel(uuid.UUID(frame["action_id"])))
                assert listener.get_message(timeout=2)["type"] == "subscribe"
                ws.send_json({"type": "phone_action_result", "action_id": frame["action_id"], "status": "done",
                              "data": {"screen": screen()}})
                relayed = listener.get_message(timeout=2)["data"].decode()
                # Sealed to the waiting worker: nothing of the screen is readable on the bus.
                assert "Secret recent search" not in relayed and "Hotspot" not in relayed and JPEG not in relayed
            message = pending.result(timeout=10)
            assert message.content.startswith("Phone screen s1: Settings (com.android.settings) in front.")
            assert '[2] switch "Hotspot" (unchecked)' in message.content
            assert "Secret recent search" in message.content
            assert message.artifact == {"phone_screen": {"id": "s1", "jpeg_b64": JPEG, "label": "Phone screen s1"}}

            pending = run_tool(executor, named["phone_tap"], {"n": 2})
            frame = receive(ws, "phone_action")
            assert frame["action"] == "tap" and frame["args"] == {"screen": "s1", "n": 2, "turn": str(turn)}
            after = screen("s2", items=[{"n": 1, "role": "switch", "label": "Hotspot", "checked": True}])
            # A large screen (the phone's picture can be ~1 MB) through the result endpoint.
            after["image"]["jpeg_base64"] = "A" * 1_000_000
            response = channel.client.post(result_url(frame["action_id"]), headers=headers(channel.token),
                                           json={"status": "done", "data": {"screen": after}})
            assert response.status_code == 204
            message = pending.result(timeout=10)
            assert '[1] switch "Hotspot" (checked)' in message.content
            assert len(message.artifact["phone_screen"]["jpeg_b64"]) == 1_000_000

            # The turn ends with control on: end_control goes out, and the turn's one line is written.
            control.finish()
            frame = receive(ws, "phone_action")
            assert frame["action"] == "end_control" and frame["args"] == {"turn": str(turn)}
            ws.send_json({"type": "phone_action_result", "action_id": frame["action_id"], "status": "done"})
            activity = receive(ws, "phone_activity")
            assert activity["text"] == "Used your phone in Settings, 2 steps"
    with channel.db() as db:
        assert [m.content for m in phone_actions.history(db, channel.conversation)] == [
            "Used your phone in Settings, 2 steps"]
    # Nothing of a screen stays in Redis.
    for key in channel.bus.scan_iter("phone:*"):
        if channel.bus.type(key) == b"string":
            assert b"Hotspot" not in channel.bus.get(key)


def test_results_must_fit_control_actions(channel):
    for action, body in (("read_screen", {"status": "done"}),
                         ("open_app", {"status": "done", "data": {"screen": screen()}}),
                         ("tap", {"status": "notified"}),
                         ("tap", {"status": "failed", "reason": "stale_screen", "data": {"screen": screen()}})):
        with pytest.raises(phone_actions.InvalidResult):
            channel.bus.set(phone_actions._pending_key(uuid.UUID(int=1)), json.dumps({
                "user": str(channel.user), "session": "s", "conversation": str(channel.conversation),
                "action": action, "deadline": time.time() + 5, "view": {}, "name": "x", "public_key": None}))
            phone_actions.accept_result(channel.user, "s", uuid.UUID(int=1), PhoneActionResult.model_validate(body))
    assert phone_actions._valid_result("end_control", PhoneActionResult(status="done"))
    assert phone_actions._valid_result("tap", PhoneActionResult(status="refused", reason="taken_over"))


def test_new_words_end_the_old_turns_control(channel, monkeypatch):
    monkeypatch.setattr(fcm, "send", lambda *args, **kwargs: pytest.fail("The action reached a live socket"))
    old, new = uuid.uuid4(), uuid.uuid4()
    tools, control = control_tools.build_phone_control_tools(channel.user, channel.conversation, turn_id=old)
    named = tools_by_name(tools)
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        wait_for_phone(channel.user)
        with ThreadPoolExecutor() as executor:
            pending = run_tool(executor, named["read_phone_screen"], {})
            frame = receive(ws, "phone_action")
            ws.send_json({"type": "phone_action_result", "action_id": frame["action_id"], "status": "done",
                          "data": {"screen": screen()}})
            pending.result(timeout=10)
            # The old turn waits on a tap; the user's new words start another turn.
            pending = run_tool(executor, named["phone_tap"], {"n": 1})
            receive(ws, "phone_action")
            started = time.monotonic()
            control_tools.build_phone_control_tools(channel.user, channel.conversation, turn_id=new)
            message = pending.result(timeout=10)
            assert time.monotonic() - started < 3  # it stopped waiting, it didn't time out
            assert message.content.startswith("Not done: the user took over their phone")
            assert control.taken_over
            # Nothing more of the old turn is sent.
            assert asyncio.run(named["phone_press"].ainvoke({"key": "back"})).startswith("Not done: the user took")
    outcome = phone_actions.act(channel.user, channel.conversation, "tap", {"screen": "s1", "n": 1, "turn": str(old)})
    assert (outcome.status, outcome.reason) == ("refused", "taken_over")


def test_the_users_touch_stops_her_for_the_turn(monkeypatch):
    sent = []

    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append(action)
        if action == "read_screen":
            return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=screen(image=False))
        return phone_actions.Outcome("refused", uuid.uuid4(), "Not done (taken_over)", reason="taken_over")

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "remember", lambda *args, **kwargs: None)
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    first = asyncio.run(named["read_phone_screen"].ainvoke({}))
    assert "no picture: this app forbids screenshots" in first
    assert asyncio.run(named["phone_tap"].ainvoke({"n": 2})).startswith("Not done: the user took over")
    assert asyncio.run(named["phone_scroll"].ainvoke({"direction": "down"})).startswith("Not done: the user took")
    assert sent == ["read_screen", "tap"]
    assert phone_control.turn_line(apps=control.apps, steps=control.steps, acted=control.acted,
                                   taken_over=control.taken_over) == "Used your phone in Settings, 2 steps (you took over)"


def test_eighty_actions_a_turn_then_she_says_how_far_she_got(monkeypatch):
    monkeypatch.setattr(phone_actions, "act", lambda *args, **kwargs: phone_actions.Outcome(
        "done", uuid.uuid4(), "Done", screen=screen(image=False)))
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "remember", lambda *args, **kwargs: None)
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    for _ in range(control_tools.MAX_ACTIONS_PER_TURN - 1):
        asyncio.run(named["phone_scroll"].ainvoke({"direction": "down"}))
    assert control.steps == control_tools.MAX_ACTIONS_PER_TURN
    limit = asyncio.run(named["phone_scroll"].ainvoke({"direction": "down"}))
    assert limit.startswith("Not done: that's 80 phone actions this turn. Stop and tell the user how far")
    assert control.steps == control_tools.MAX_ACTIONS_PER_TURN


def test_phone_reasons_are_said_plainly(monkeypatch):
    reasons = iter(["own_app", "stale_screen", "locked"])
    monkeypatch.setattr(phone_actions, "act", lambda *args, **kwargs: phone_actions.Outcome(
        "refused" if args[2] != "tap" else "failed", uuid.uuid4(), "x", reason=next(reasons)))
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    assert "you can read it but not act in it" in asyncio.run(named["read_phone_screen"].ainvoke({}))
    assert "Call read_phone_screen and act on the new numbers" in asyncio.run(named["phone_tap"].ainvoke({"n": 3}))
    assert "Ask them to unlock it" in asyncio.run(named["phone_press"].ainvoke({"key": "back"}))


def test_an_action_that_happened_before_the_takeover_is_said_to_have_happened(monkeypatch):
    sent = []

    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append(action)
        if action == "read_screen":
            return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=screen("s1"))
        return phone_actions.Outcome("refused", uuid.uuid4(), "Not done (taken_over_after)", reason="taken_over_after")

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    said = asyncio.run(named["phone_tap"].ainvoke({"n": 2}))
    assert said.startswith("Done: that action did happen on the phone. Then the user took over")
    assert control.taken_over
    # Nothing more of this turn reaches the phone.
    assert asyncio.run(named["phone_tap"].ainvoke({"n": 3})).startswith("Not done: the user took over")
    assert sent == ["read_screen", "tap"]


def test_pinch_and_gesture_send_several_fingers_on_the_picture(monkeypatch):
    sent = []

    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append((action, args))
        shown = {**screen("s1"), "image": {"jpeg_base64": "AAAA", "width": 720, "height": 1600}}
        return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=shown)

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    asyncio.run(named["phone_pinch"].ainvoke({"x": 360, "y": 800, "zoom": "out"}))
    action, args = sent[-1]
    assert action == "gesture" and args["screen"] == "s1" and len(args["fingers"]) == 2
    first, second = args["fingers"]
    # Zooming out brings the fingers together, each staying on the picture.
    assert abs(first[2] - second[2]) < abs(first[0] - second[0])
    assert all(0 <= v[0] < 720 and 0 <= v[1] < 1600 for f in args["fingers"] for v in (f[:2], f[2:]))
    asyncio.run(named["phone_pinch"].ainvoke({"x": 20, "y": 800, "zoom": "in", "spread": 400}))
    assert all(v >= 0 for f in sent[-1][1]["fingers"] for v in f)

    asyncio.run(named["phone_gesture"].ainvoke({"fingers": [[100, 900, 100, 500], [300, 900, 300, 500]],
                                                "ms": 5000}))
    assert sent[-1] == ("gesture", {"screen": "s1", "fingers": [[100, 900, 100, 500], [300, 900, 300, 500]],
                                    "ms": 2000, "turn": sent[-1][1]["turn"]})
    count = len(sent)
    assert asyncio.run(named["phone_gesture"].ainvoke({"fingers": [[1, 2, 3]]})).startswith("Not done: give 1 to 5")
    assert asyncio.run(named["phone_gesture"].ainvoke({"fingers": [[1, 1, 1, 1]] * 6})).startswith("Not done")
    assert len(sent) == count


@pytest.fixture
def state_user(channel, monkeypatch):
    """A user whose phone reports state, removed from the shared test Redis afterwards (other tests poll every
    user that reported)."""
    monkeypatch.setattr(phone_state, "_client", lambda: channel.bus)
    user = uuid.uuid4()
    yield user
    channel.bus.hdel(phone_state.USERS_KEY, str(user))
    channel.bus.delete(phone_state._key(user), phone_control._wipe_key(user))


def test_short_term_screen_memory_is_text_only_bounded_and_wiped(channel, state_user):
    user = state_user
    now = time.time()
    for i in range(12):
        phone_control.remember(user, screen(f"s{i}"), f"Phone screen s{i}: text", now=now - 60 * (12 - i))
    kept = phone_control.recent(user, now=now)
    assert [e.id for e in kept] == [f"s{i}" for i in range(2, 12)]
    assert all("jpeg" not in e.text.lower() for e in kept)
    # 30 minutes each.
    assert [e.id for e in phone_control.recent(user, now=now + 30 * 60 - 61)] == ["s11"]
    phone_control.remember(user, screen("s20"), "Phone screen s20: text", now=now)
    # The phone reports the master switch or Phone control off: gone at once, in every process (the epoch).
    phone_state.report(user, {"control_on": True})
    assert phone_control.recent(user, now=now)
    phone_state.report(user, {"control_on": False})
    assert phone_control.recent(user, now=now) == []
    phone_state.report(user, {"control_on": True})
    phone_control.remember(user, screen("s21"), "Phone screen s21: text", now=now)
    assert [e.id for e in phone_control.recent(user, now=now)] == ["s21"]
    # Another process's memory from before a wipe is dropped too.
    phone_control._memory[str(user)].append(phone_control.Remembered("s0", "Settings", "old", now, epoch=0))
    assert [e.id for e in phone_control.recent(user, now=now)] == ["s21"]


def test_remembered_screens_are_recall_only(monkeypatch):
    user = uuid.uuid4()
    monkeypatch.setattr(phone_control, "_epoch", lambda *args, **kwargs: 0)
    monkeypatch.setattr(phone_state, "read", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    phone_control.remember(user, screen("s4", "Spotify"), "Phone screen s4: Spotify in front.\n  Song A")
    tools, _ = control_tools.build_phone_control_tools(user, uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    listing = asyncio.run(named["recent_phone_screens"].ainvoke({}))
    assert "- s4: Spotify, 1 min ago" in listing and "not actionable" in listing
    assert "Song A" in asyncio.run(named["recent_phone_screens"].ainvoke({"screen": "s4"}))


def test_a_screen_reads_as_numbered_items_and_text():
    text = phone_control.render(screen(items=[
        {"n": 1, "role": "text_field", "text": "", "hint": "Password", "password": True},
        {"n": 2, "role": "button", "label": "Play"},
        {"role": "image", "label": "Album cover"},
        {"role": "text", "text": "Recent searches"},
    ]), picture=True)
    lines = text.split("\n")
    assert lines[0] == "Phone screen s1: Settings (com.android.settings) in front."
    assert "its picture (720x1600, numbered boxes drawn on) follows" in lines[1]
    assert lines[3:] == ["[1] text_field (empty) hint: Password (password: its text is hidden)",
                         '[2] button "Play"', "  (image: Album cover)", "  Recent searches"]
    assert phone_control.record_line(text) == lines[0]


def test_phone_pictures_reach_her_like_desktop_screenshots():
    messages = [HumanMessage("turn on my hotspot")]
    for i in range(1, 5):
        messages += [AIMessage("", tool_calls=[{"name": "phone_tap", "args": {}, "id": f"c{i}"}]),
                     ToolMessage(f"Phone screen s{i}: Settings in front.", tool_call_id=f"c{i}",
                                 artifact={"phone_screen": {"id": f"s{i}", "jpeg_b64": JPEG,
                                                            "label": f"Phone screen s{i}"}})]
    shown = screenshot_hook({"messages": messages})["llm_input_messages"]
    pictures = [m for m in shown if isinstance(m, HumanMessage) and isinstance(m.content, list)]
    assert [p.content[1]["text"] for p in pictures] == ["Phone screen s2:", "Phone screen s3:", "Phone screen s4:"]
    assert "the user's phone screen" in pictures[0].content[0]["text"]


def test_control_tools_only_on_reply_turns_with_a_phone(monkeypatch):
    settings = get_settings().model_copy(update={"redis_url": "redis://unused"})
    monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
    built = {}

    def agent(tools, **kwargs):
        built["hook"] = kwargs.get("pre_model_hook")
        return {tool.name for tool in tools}

    monkeypatch.setattr(ai_chat, "build_agent", agent)
    monkeypatch.setattr(ai_chat, "get_agent", lambda *args: set())
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    finished = []
    monkeypatch.setattr(control_tools.PhoneControl, "finish", lambda self: finished.append(self.turn))
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4(),
                                turn_id=uuid.uuid4(), phone="Notifications: shared")

    async def registered(context):
        async with ai_chat._orchestrator_for(context) as (names, _):
            return names, ai_chat._run_config(context)

    names, config = asyncio.run(registered(reply))
    assert set(control_tools.TOOL_NAMES) <= names
    assert built["hook"] is screenshot_hook and config == {"recursion_limit": ai_chat.DESKTOP_RECURSION_LIMIT}
    assert finished == [str(reply.turn_id)]
    # Never on her own turns, a turn someone else's words caused, a nudge, or without a phone.
    for other in (replace(reply, source_message_id=None, wake="late result", phone_control=None),
                  replace(reply, third_party=True, phone_control=None),
                  replace(reply, nudge="say hello", phone_control=None),
                  replace(reply, phone=None, phone_control=None)):
        names, config = asyncio.run(registered(other))
        assert not set(control_tools.TOOL_NAMES) & names and config is None
