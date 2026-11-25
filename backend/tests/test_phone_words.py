"""The user's words while her hands are on their phone (services/phone_words.py): they reach her under the next
screen and never stop the turn; what she didn't read is answered next, as its own turn."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.tools import phone_control as control_tools
from app.api.routes import chat as chat_route
from app.db.models import ChatJob, ChatMessage
from app.services import chat as chat_service
from app.services import phone_actions, phone_control, phone_words
from app.workers import chat as chat_worker
from db_support import database  # noqa: F401
from test_phone_actions import channel, headers, receive  # noqa: F401
from test_phone_phase9 import screen, tools_by_name


def _offline_tools(monkeypatch, outcome=None):
    monkeypatch.setattr(phone_actions, "act", outcome or (lambda *args, **kwargs: phone_actions.Outcome(
        "done", uuid.uuid4(), "Done", screen=screen(image=False))))
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "remember", lambda *args, **kwargs: None)


def test_their_words_come_back_under_the_next_screen(monkeypatch):
    _offline_tools(monkeypatch)
    calls = []
    said = [phone_words.Words(job_id=str(uuid.uuid4()), message_id=str(uuid.uuid4()), turn_id=str(uuid.uuid4()),
                              text="pop the warden now", at=0.0)]
    monkeypatch.setattr(phone_words, "hands_on", lambda *args, **kwargs: calls.append("on"))
    batches = iter([said, []])
    monkeypatch.setattr(phone_words, "take", lambda *args, **kwargs: next(batches))
    monkeypatch.setattr(phone_words, "hands_off", lambda *args, **kwargs: calls.append("off"))
    monkeypatch.setattr(phone_words.time, "time", lambda: 3.0)
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    first = asyncio.run(named["read_phone_screen"].ainvoke({}))
    assert first.startswith("Phone screen s1")
    assert 'The user said this while you were acting' in first
    assert '- 3 s ago: "pop the warden now"' in first
    # Read once: the next step doesn't repeat them.
    second = asyncio.run(named["phone_tap"].ainvoke({"n": 1}))
    assert "pop the warden" not in second
    assert calls.count("on") == 2 and "off" not in calls


def test_stop_on_the_banner_takes_her_hands_off(monkeypatch):
    def act(user_id, conversation_id, action, args, **kwargs):
        if action == "read_screen":
            return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=screen(image=False))
        return phone_actions.Outcome("refused", uuid.uuid4(), "Not done", reason="taken_over")

    _offline_tools(monkeypatch, act)
    released = []
    monkeypatch.setattr(phone_words, "hands_on", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_words, "take", lambda *args, **kwargs: [])
    monkeypatch.setattr(phone_words, "hands_off", lambda user_id, turn: released.append(turn))
    turn = uuid.uuid4()
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=turn)
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    stopped = asyncio.run(named["phone_tap"].ainvoke({"n": 1}))
    assert stopped.startswith("Not done: the user took over their phone this turn (they tapped Stop on the banner)")
    assert released == [str(turn)]


@pytest.fixture
def words(channel, monkeypatch):
    """The socket and the phone_words keys on the test Redis; the chat worker's streams there too."""
    monkeypatch.setattr(chat_worker, "_redis", lambda: channel.bus)
    monkeypatch.setattr(chat_service, "wake_chat_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_route.awareness_service, "build", lambda *args, **kwargs: SimpleNamespace(
        to_dict=lambda: {}, describe=lambda: "", zone_name=None,
        senses=SimpleNamespace(vision_enabled=False, screen_image_id=None)))
    with channel.db() as db:
        working = uuid.uuid4()
        db.add(ChatJob(turn_id=working, user_id=channel.user, conversation_id=channel.conversation, payload={},
                       status="processing"))
        db.commit()
    yield SimpleNamespace(channel=channel, working=str(working))
    for key in channel.bus.scan_iter("phone:control:*"):
        channel.bus.delete(key)


def _stream(bus, turn):
    return [json.loads(fields[b"event"]) for _, fields in bus.xrange(chat_worker.stream_key(turn))]


def test_a_message_while_her_hands_are_busy_joins_her_turn(words):
    channel, working = words.channel, words.working
    phone_words.hands_on(channel.user, channel.conversation, working)
    assert _stream(channel.bus, working) == [{"type": "hands", "turn_id": working, "open": True}]
    joined = uuid.uuid4()
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        ws.send_json({"type": "run", "conversation_id": str(channel.conversation), "content": "pop the warden now",
                      "turn_id": str(joined)})
        ack = receive(ws, "ack")
        assert ack["joins"] == working
        # The turn she's working in was not told to stop.
        assert not channel.bus.exists(chat_worker.cancel_key(working))
        with channel.db() as db:
            job = db.scalar(select(ChatJob).where(ChatJob.turn_id == joined))
            assert job.status == "pending"  # waits behind hers
        # Her next step reads it: its job is answered in her turn, and its own stream says so.
        taken = phone_words.take(channel.user, channel.conversation, working)
        assert [w.text for w in taken] == ["pop the warden now"]
        assert receive(ws, "done") == {"type": "done", "turn_id": str(joined), "status": "completed",
                                       "joined": working}
    with channel.db() as db:
        job = db.scalar(select(ChatJob).where(ChatJob.turn_id == joined))
        assert job.status == "completed"
        message = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == joined))
        assert message.seen_at is not None
    assert phone_words.take(channel.user, channel.conversation, working) == []


def test_words_she_never_read_are_answered_next(words):
    channel, working = words.channel, words.working
    phone_words.hands_on(channel.user, channel.conversation, working)
    joined = uuid.uuid4()
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        ws.send_json({"type": "run", "conversation_id": str(channel.conversation), "content": "how's it going?",
                      "turn_id": str(joined)})
        assert receive(ws, "ack")["joins"] == working
    # The turn ends before another step.
    phone_words.hands_off(channel.user, working)
    assert _stream(channel.bus, working)[-1] == {"type": "hands", "turn_id": working, "open": False}
    assert phone_words.hands(channel.user) is None
    assert phone_words.take(channel.user, channel.conversation, working) == []
    with channel.db() as db:
        assert db.scalar(select(ChatJob.status).where(ChatJob.turn_id == joined)) == "pending"


def test_without_her_hands_on_the_phone_a_message_is_its_own_turn(words, monkeypatch):
    channel, working = words.channel, words.working
    stopped = []
    monkeypatch.setattr(chat_route, "_stop_active_turn", lambda user_id: _record(stopped, user_id))
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        ws.send_json({"type": "run", "conversation_id": str(channel.conversation), "content": "hello",
                      "turn_id": str(uuid.uuid4())})
        assert "joins" not in receive(ws, "ack")
    assert stopped == [channel.user]
    # A newer turn holding the phone isn't released by an older one ending.
    phone_words.hands_on(channel.user, channel.conversation, "newer")
    phone_words.hands_off(channel.user, working)
    assert phone_words.hands(channel.user).turn == "newer"


async def _record(calls, user_id):
    calls.append(user_id)
