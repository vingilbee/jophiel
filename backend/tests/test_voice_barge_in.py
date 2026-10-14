"""Barge-in fencing: a reply the user talked over must go silent at once.

Covers the server half of interruption step 1: the worker stops synthesizing
mid-clause when the turn is cancelled, audio events carry their turn id so the
client can fence them, and the route's own `stopped` is tagged with the turn
it stopped (an untagged one could settle a newer, barge-in turn).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import Mock

import pytest

from app.services import chat as chat_service
from app.services import cognitive_shadow as shadow_service
from app.workers import chat as chat_worker
from cognitive_shadow_support import create_conversation, create_user, database, shadow_env  # noqa: F401
from test_auth_sessions import auth_client, socket_client  # noqa: F401  (fixtures)

# Each token ends a clause, so every token is spoken as its own clause.
REPLY = ("Two large eggs have twelve grams of protein. ", "That is a solid breakfast. ",
         "Add toast if you want carbs. ")


@pytest.fixture
def voice_turn(shadow_env, monkeypatch):
    """Run the real worker `_generate` for a voice job with a fake sidecar."""

    published: list[dict] = []
    synthesized: list[str] = []
    closed: list[str] = []
    cancel_after_chunks: dict[str, int | None] = {"n": None}

    async def fake_stream_chat(*, messages, context, **_):
        for token in REPLY:
            yield {"type": "token", "content": token}

    async def fake_synthesize(text: str):
        synthesized.append(text)
        try:
            for _ in range(3):
                yield b"\x01\x00" * 160
        finally:
            closed.append(text)

    def cancelled(turn_id) -> bool:
        limit = cancel_after_chunks["n"]
        chunks = sum(1 for event in published if event["type"] == "audio_chunk")
        return limit is not None and chunks >= limit

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "SessionLocal", shadow_env.db)
    monkeypatch.setattr(chat_worker, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", cancelled)
    monkeypatch.setattr(chat_worker.tts_service, "synthesize", fake_synthesize)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt", lambda user_id, content: None)
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", Mock())
    monkeypatch.setattr(shadow_service, "capture_turn", lambda **kwargs: None)

    def run(*, cancel_after: int | None = None):
        published.clear()
        synthesized.clear()
        closed.clear()
        cancel_after_chunks["n"] = cancel_after
        with shadow_env.db() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            turn_id = uuid.uuid4()
            message = chat_service.add_message(db, conversation=conversation, role="user",
                                               content="Protein in eggs?", turn_id=turn_id)
            chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                     user_message_id=message.id,
                                     payload={"content": "Protein in eggs?", "voice": True}, commit=False)
            db.commit()
            job = chat_service.claim_next_job(db, lease_seconds=60)
        asyncio.run(chat_worker._generate(job))
        return {"events": list(published), "synthesized": list(synthesized),
                "closed": list(closed), "turn_id": str(turn_id)}

    return run


def test_audio_events_carry_their_turn_id(voice_turn) -> None:
    result = voice_turn()
    starts = [e for e in result["events"] if e["type"] == "audio_start"]
    ends = [e for e in result["events"] if e["type"] == "audio_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0]["turn_id"] == ends[0]["turn_id"] == result["turn_id"]
    assert result["events"][-1]["type"] == "done"


def test_cancel_stops_audio_mid_clause_and_closes_the_sidecar_stream(voice_turn) -> None:
    result = voice_turn(cancel_after=1)
    chunks = [e for e in result["events"] if e["type"] == "audio_chunk"]
    # One chunk out, then the fence: not the rest of the clause (3 chunks).
    assert len(chunks) == 1
    # The sidecar stream was closed rather than abandoned, and no later
    # clause was sent to the synthesizer after the cancel.
    assert len(result["synthesized"]) == 1
    assert result["closed"] == result["synthesized"]
    assert result["events"][-1]["type"] == "stopped"


def test_route_stop_is_tagged_with_the_stopped_turn(socket_client, monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.config import get_settings
    from app.services import auth

    client, user, chat = socket_client
    conversation = SimpleNamespace(id=uuid.uuid4())

    async def stream(**kwargs):
        yield {"type": "token", "content": "Two large eggs "}
        await asyncio.sleep(30)

    monkeypatch.setattr(chat, "stream_chat", stream)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "add_message", Mock(return_value=SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
    monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
    monkeypatch.setattr(chat.memory_service, "retrieve_for_prompt", Mock(return_value=""))
    monkeypatch.setattr(chat.memory_service, "schedule_consolidation", Mock())
    monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: False)
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}

    turn_id = str(uuid.uuid4())
    stopped: list[dict] = []
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "run", "content": "Protein in eggs?", "turn_id": turn_id})
        for _ in range(20):
            frame = ws.receive_json()
            if frame["type"] == "token":
                ws.send_json({"type": "stop", "turn_id": turn_id})
            if frame["type"] == "stopped":
                stopped.append(frame)
                if frame.get("status") == "cancelled":
                    break
    assert stopped, "no stopped frame received"
    assert all(frame.get("turn_id") == turn_id for frame in stopped)


# --- step 2: one-frame handoff, pre-roll, stop-only interruptions ---------------


def _voice_socket(socket_client, monkeypatch, transcript: str):
    """A socket whose STT records the audio it was given and returns ``transcript``."""
    from app.core.config import get_settings
    from app.services import auth

    client, user, chat = socket_client
    heard: list[tuple[bytes, int]] = []

    async def transcribe(pcm: bytes, sample_rate: int, *, minecraft: bool = False) -> str:
        heard.append((pcm, sample_rate))
        return transcript

    monkeypatch.setattr(chat.stt_service, "transcribe", transcribe)
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    return client, chat, headers, heard


def _receive_until(ws, predicate, limit: int = 40) -> dict:
    for _ in range(limit):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError("expected frame never arrived")


def test_audio_after_voice_interrupt_reaches_the_new_utterance(socket_client, monkeypatch) -> None:
    """Pre-roll sent right behind the interrupt frame belongs to the new turn,
    even while the old turn is stuck and its teardown takes the full bound."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client, chat, headers, heard = _voice_socket(socket_client, monkeypatch, transcript="")
    conversation = SimpleNamespace(id=uuid.uuid4())

    async def stuck_stream(**kwargs):
        yield {"type": "token", "content": "Two large eggs "}
        await asyncio.sleep(30)  # a provider that never observes `stop`

    cancelled: list[uuid.UUID] = []
    monkeypatch.setattr(chat.chat_worker, "request_cancel", cancelled.append)
    monkeypatch.setattr(chat, "stream_chat", stuck_stream)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "add_message", Mock(return_value=SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
    monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
    monkeypatch.setattr(chat.memory_service, "retrieve_for_prompt", Mock(return_value=""))
    monkeypatch.setattr(chat.memory_service, "schedule_consolidation", Mock())
    monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: False)

    old_turn, new_turn = str(uuid.uuid4()), str(uuid.uuid4())
    pre_roll, live = b"\x01\x00" * 4800, b"\x02\x00" * 1280
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "run", "content": "Protein in eggs?", "turn_id": old_turn})
        _receive_until(ws, lambda f: f["type"] == "token")
        ws.send_json({"type": "voice_interrupt", "cancel_turn_id": old_turn,
                      "new_turn_id": new_turn, "conversation_id": str(conversation.id),
                      "sample_rate": 16000})
        ws.send_bytes(pre_roll)
        ws.send_bytes(live)
        ws.send_json({"type": "voice_end", "turn_id": new_turn})
        _receive_until(ws, lambda f: f["type"] == "done" and f.get("turn_id") is None)

    assert cancelled == [uuid.UUID(old_turn)]
    # Exactly the bytes sent after the interrupt frame, in order, at its rate.
    assert heard == [(pre_roll + live, 16000)]


def test_stop_only_interruption_is_not_persisted(socket_client, monkeypatch) -> None:
    client, chat, headers, heard = _voice_socket(socket_client, monkeypatch, transcript="Wait!")
    add_message = Mock()
    monkeypatch.setattr(chat.chat_service, "add_message", add_message)

    new_turn = str(uuid.uuid4())
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "voice_interrupt", "cancel_turn_id": None,
                      "new_turn_id": new_turn, "sample_rate": 16000})
        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "voice_end", "turn_id": new_turn})
        done = _receive_until(ws, lambda f: f["type"] in {"done", "transcript", "error"})

    assert done == {"type": "done", "turn_id": new_turn, "status": "cancelled"}
    assert len(heard) == 1
    add_message.assert_not_called()


def test_stop_word_outside_an_interruption_is_a_normal_turn(socket_client, monkeypatch) -> None:
    """Saying "wait" to an idle assistant is something to answer, not a cancel."""
    import time

    client, chat, headers, heard = _voice_socket(socket_client, monkeypatch, transcript="Wait!")
    add_message = Mock(side_effect=RuntimeError("persisted"))  # end the task here
    monkeypatch.setattr(chat.chat_service, "add_message", add_message)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock())

    turn = str(uuid.uuid4())
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "voice_start", "turn_id": turn, "sample_rate": 16000})
        ws.send_bytes(b"\x01\x00" * 1600)
        ws.send_json({"type": "voice_end", "turn_id": turn})
        transcript = _receive_until(ws, lambda f: f["type"] in {"done", "transcript"})
        deadline = time.monotonic() + 2
        while not add_message.called and time.monotonic() < deadline:
            time.sleep(0.02)

    assert transcript["type"] == "transcript"
    assert add_message.call_args.kwargs["content"] == "Wait!"


@pytest.mark.parametrize(("transcript", "stop_only"), [
    ("Stop.", True), ("wait!", True), ("Hold on", True), ("okay, stop", True),
    ("Stop it.", True), ("Wait a second", True),
    ("Hold on, how much protein is that?", False), ("Wait, I meant tofu", False),
    ("no", False), ("", False),
])
def test_stop_only_phrases(transcript: str, stop_only: bool) -> None:
    from app.api.routes.chat import _is_stop_only

    assert _is_stop_only(transcript) is stop_only


# --- step 3: hold the reply, then resume / stop / take the turn -------------------


@pytest.fixture
def held_reply(socket_client, monkeypatch):
    """A socket with a voice reply in flight, ready to be talked over."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    def make(transcript: str):
        client, chat, headers, heard = _voice_socket(socket_client, monkeypatch, transcript)
        conversation = SimpleNamespace(id=uuid.uuid4())

        async def slow_stream(**kwargs):
            yield {"type": "token", "content": "Two large eggs "}
            await asyncio.sleep(3)
            yield {"type": "token", "content": "have twelve grams."}

        cancelled: list[uuid.UUID] = []
        add_message = Mock(return_value=SimpleNamespace(id=uuid.uuid4()))
        monkeypatch.setattr(chat.chat_worker, "request_cancel", cancelled.append)
        monkeypatch.setattr(chat, "stream_chat", slow_stream)
        monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
        monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
        monkeypatch.setattr(chat.chat_service, "add_message", add_message)
        monkeypatch.setattr(chat.chat_service, "enqueue_job", Mock())
        monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
        monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
        monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
        monkeypatch.setattr(chat.memory_service, "retrieve_for_prompt", Mock(return_value=""))
        monkeypatch.setattr(chat.memory_service, "schedule_consolidation", Mock())
        monkeypatch.setattr(chat.cognitive_shadow, "capture_enabled", lambda: False)
        return SimpleNamespace(client=client, headers=headers, heard=heard, cancelled=cancelled,
                               add_message=add_message, conversation=conversation)

    return make


def _talk_over(ws, env, *, commit: bool = False) -> tuple[str, str]:
    """Start a reply, hold it with a tentative interrupt, and end the utterance."""
    old_turn, new_turn = str(uuid.uuid4()), str(uuid.uuid4())
    ws.send_json({"type": "run", "content": "Protein in eggs?", "turn_id": old_turn})
    _receive_until(ws, lambda f: f["type"] == "token")
    ws.send_json({"type": "voice_interrupt", "tentative": True, "cancel_turn_id": old_turn,
                  "new_turn_id": new_turn, "conversation_id": str(env.conversation.id),
                  "sample_rate": 16000})
    ws.send_bytes(b"\x01\x00" * 8000)
    if commit:
        ws.send_json({"type": "voice_interrupt_commit", "turn_id": new_turn})
    ws.send_json({"type": "voice_end", "turn_id": new_turn})
    return old_turn, new_turn


def test_backchannel_resumes_the_held_reply(held_reply) -> None:
    env = held_reply("I see.")
    with env.client.websocket_connect("/api/chat/ws", headers=env.headers) as ws:
        assert ws.receive_json()["type"] == "state"
        old_turn, new_turn = _talk_over(ws, env)
        resolved = _receive_until(ws, lambda f: f["type"] == "barge_resolved")
        # The held reply was never cancelled: it carries on to the end.
        rest = _receive_until(ws, lambda f: f["type"] == "token")
        done = _receive_until(ws, lambda f: f["type"] in {"done", "stopped"})

    assert resolved == {"type": "barge_resolved", "turn_id": new_turn, "action": "resume"}
    assert rest["content"] == "have twelve grams."
    assert done["type"] == "done"
    assert env.cancelled == []
    # Only the original question was persisted, not the "I see".
    assert [c.kwargs["content"] for c in env.add_message.call_args_list if c.kwargs["role"] == "user"] \
        == ["Protein in eggs?"]


def test_stop_word_stops_the_held_reply_without_a_turn(held_reply) -> None:
    env = held_reply("Wait.")
    with env.client.websocket_connect("/api/chat/ws", headers=env.headers) as ws:
        assert ws.receive_json()["type"] == "state"
        old_turn, new_turn = _talk_over(ws, env)
        resolved = _receive_until(ws, lambda f: f["type"] == "barge_resolved")
        stopped = _receive_until(ws, lambda f: f["type"] == "stopped")

    assert resolved["action"] == "stop"
    assert stopped["turn_id"] == old_turn
    assert env.cancelled == [uuid.UUID(old_turn)]
    assert all(c.kwargs["content"] != "Wait." for c in env.add_message.call_args_list)


def test_a_real_question_takes_the_turn(held_reply) -> None:
    env = held_reply("What about rice?")
    with env.client.websocket_connect("/api/chat/ws", headers=env.headers) as ws:
        assert ws.receive_json()["type"] == "state"
        old_turn, new_turn = _talk_over(ws, env)
        resolved = _receive_until(ws, lambda f: f["type"] == "barge_resolved")
        transcript = _receive_until(ws, lambda f: f["type"] == "transcript")

    assert resolved["action"] == "turn"
    assert transcript == {"type": "transcript", "turn_id": new_turn, "content": "What about rice?"}
    assert env.cancelled == [uuid.UUID(old_turn)]
    # The held audio was transcribed once, by the decision, not again.
    assert len(env.heard) == 1


def test_commit_cancels_at_once_and_skips_the_decision(held_reply) -> None:
    env = held_reply("So what about rice, and how much of it should I eat?")
    with env.client.websocket_connect("/api/chat/ws", headers=env.headers) as ws:
        assert ws.receive_json()["type"] == "state"
        old_turn, new_turn = _talk_over(ws, env, commit=True)
        frame = _receive_until(ws, lambda f: f["type"] in {"barge_resolved", "transcript"})

    assert frame["type"] == "transcript" and frame["turn_id"] == new_turn
    assert env.cancelled == [uuid.UUID(old_turn)]


@pytest.mark.parametrize(("transcript", "action"), [
    ("I see.", "resume"), ("Mm-hm.", "resume"), ("uh huh", "resume"), ("Okay!", "resume"),
    ("Yeah, yeah.", "resume"), ("", "resume"), ("...", "resume"), ("Thank you.", "resume"),
    ("Stop.", "stop"), ("Hold on.", "stop"), ("wait a second", "stop"),
    ("I see, but what about rice?", "turn"), ("Okay so how much protein?", "turn"),
    ("No", "turn"),
    # Combinations and stretched spellings of listening noises.
    ("Hmm, I see.", "resume"), ("Hmmm... I see", "resume"), ("Oh okay, got it.", "resume"),
    ("Yeah, right.", "resume"), ("Mmmm.", "resume"), ("Ohhh, that makes sense.", "resume"),
    ("Uh-huh, yeah.", "resume"),
    ("Hmm, wait.", "stop"), ("Okay, stop.", "stop"), ("Oh, hold on.", "stop"),
    ("Hmm, I see, but what about rice?", "turn"), ("Okay, how much protein?", "turn"),
    ("Hmm, no.", "turn"),
])
def test_classify_hold(transcript: str, action: str) -> None:
    from app.api.routes.chat import _classify_hold

    assert _classify_hold(transcript) == action


# --- step 4: label each clause's audio with the reply text it speaks -----------


def test_each_clause_announces_its_span_of_the_reply(voice_turn) -> None:
    result = voice_turn()
    segments = [e for e in result["events"] if e["type"] == "audio_segment"]
    reply = "".join(REPLY)
    # The first sentence is its own clause; later ones join until a clause is
    # long enough to cover the next one's synthesis start-up.
    assert [e["segment"] for e in segments] == [0, 1]
    spans = [reply[e["text_start"]:e["text_end"]].strip() for e in segments]
    assert spans == [REPLY[0].strip(), (REPLY[1] + REPLY[2]).strip()]
    assert segments[-1]["text_end"] == len(reply.rstrip())
    # Each label precedes its clause's audio and follows audio_start.
    kinds = [e["type"] for e in result["events"] if e["type"].startswith("audio")]
    assert kinds[:3] == ["audio_start", "audio_segment", "audio_chunk"]
    assert all(e["turn_id"] == result["turn_id"] for e in segments)


def test_a_long_silent_wait_before_speaking_still_makes_a_turn(socket_client, monkeypatch) -> None:
    """Listening can stay open for minutes (e.g. watching her play). The words
    at the end must reach transcription; the oldest silence is what gets cut."""
    import time

    client, chat, headers, heard = _voice_socket(socket_client, monkeypatch, transcript="Collect some wood")
    add_message = Mock(side_effect=RuntimeError("persisted"))  # end the task here
    monkeypatch.setattr(chat.chat_service, "add_message", add_message)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock())

    turn = str(uuid.uuid4())
    silence, words = b"\x00\x00" * 16000, b"\x05\x00" * 16000  # 1 s each
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "voice_start", "turn_id": turn, "sample_rate": 16000})
        for _ in range(65):
            ws.send_bytes(silence)
        ws.send_bytes(words)
        ws.send_json({"type": "voice_end", "turn_id": turn})
        transcript = _receive_until(ws, lambda f: f["type"] in {"done", "transcript", "error"})
        deadline = time.monotonic() + 2
        while not add_message.called and time.monotonic() < deadline:
            time.sleep(0.02)

    assert transcript["type"] == "transcript"
    pcm, rate = heard[0]
    assert len(pcm) == chat.MAX_UTTERANCE_BYTES and pcm.endswith(words)
    assert add_message.call_args.kwargs["content"] == "Collect some wood"


def test_a_done_with_nothing_open_is_answered_not_ignored(socket_client, monkeypatch) -> None:
    """The client waits on every voice_end; silence from the server froze it in "thinking"."""
    client, chat, headers, _heard = _voice_socket(socket_client, monkeypatch, transcript="")
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "voice_end", "turn_id": str(uuid.uuid4())})
        frame = _receive_until(ws, lambda f: f["type"] == "error")
    assert "say it again" in frame["detail"]


def test_a_wedged_sidecar_finishes_the_turn_as_text_quickly(voice_turn, monkeypatch) -> None:
    """Seen live: the sidecar stopped answering and every turn sat on its 60 s
    HTTP timeout. Now a silent sidecar is given up on and the reply is text."""
    calls: list[str] = []

    async def wedged(text: str):
        calls.append(text)
        await asyncio.sleep(3600)
        yield b""

    monkeypatch.setattr(chat_worker.tts_service, "synthesize", wedged)
    monkeypatch.setattr(chat_worker, "TTS_FIRST_CHUNK_SECONDS", 0.4)
    started = time.monotonic()
    result = voice_turn()
    assert time.monotonic() - started < 5
    assert calls == [calls[0]]  # no further clause waits on a sidecar known to be down
    assert result["events"][-1]["type"] == "done"
    assert "".join(e["content"] for e in result["events"] if e["type"] == "token") == "".join(REPLY)


def test_a_stop_ends_a_turn_waiting_on_the_model(voice_turn, monkeypatch) -> None:
    """A barge-in while the model is in a long tool call ends the turn at once."""
    stop = {"now": False}

    async def slow_model(*, messages, context, **_):
        yield {"type": "token", "content": "One moment. "}
        stop["now"] = True
        await asyncio.sleep(3600)
        yield {"type": "token", "content": "never"}

    monkeypatch.setattr(chat_worker, "stream_chat", slow_model)
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: stop["now"])
    started = time.monotonic()
    result = voice_turn()
    assert time.monotonic() - started < 5
    assert result["events"][-1]["type"] == "stopped"
