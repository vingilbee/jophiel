"""Review fixes on the phone backend: an ack only ever goes out for a message she will answer (the message and its
job are one commit; a resend repairs an orphaned message), and "spoken aloud" means the audio actually went out."""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage
from app.services import auth
from db_support import create_conversation, create_user, database, db_env  # noqa: F401
from test_phone_auth import FakeRedis


# --------------------------------------------------------------------------- #
# The message and its job: one commit (real PostgreSQL)
# --------------------------------------------------------------------------- #
@pytest.fixture
def worker_socket(db_env, monkeypatch):
    """The chat socket on the worker path (a job per message), on the test schema; no worker runs."""
    from app.api.routes import chat

    settings = get_settings().model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat.chat_worker, "event_bus_available", lambda: True)
    monkeypatch.setattr(chat.chat_worker, "_redis", lambda: None)
    monkeypatch.setattr(chat.chat_service, "wake_chat_worker", Mock())
    monkeypatch.setattr(chat, "SESSION_CHECK_SECONDS", 0.05)
    sessions = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: sessions)
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        user_id, conversation_id = user.id, conversation.id
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    token = auth.create_session(SimpleNamespace(id=user_id), client="phone")
    with TestClient(app, base_url="https://testserver") as client:
        yield SimpleNamespace(client=client, chat=chat, db=db_env.db, user_id=user_id,
                              conversation_id=conversation_id, headers={"Authorization": f"Bearer {token}"})


def _run(turn_id, conversation_id):
    return {"type": "run", "content": "Hello from the phone", "turn_id": str(turn_id),
            "conversation_id": str(conversation_id), "written_at": datetime.now(timezone.utc).isoformat()}


def _until(ws, kind, limit=10):
    for _ in range(limit):
        message = ws.receive()
        if message.get("type") == "websocket.close":
            raise WebSocketDisconnect(message.get("code", 1000))
        if message.get("text") is None:
            continue  # audio
        frame = json.loads(message["text"])
        if frame["type"] == kind:
            return frame
    pytest.fail(f"no {kind} frame")


def test_a_message_and_its_job_are_saved_together_and_then_acked(worker_socket):
    turn_id = uuid.uuid4()
    with worker_socket.client.websocket_connect("/api/chat/ws", headers=worker_socket.headers) as ws:
        ws.send_json(_run(turn_id, worker_socket.conversation_id))
        ack = _until(ws, "ack")
    with worker_socket.db() as db:
        message = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id))
        job = db.scalar(select(ChatJob).where(ChatJob.turn_id == turn_id))
    assert ack["user_message_id"] == str(message.id)
    assert job is not None and job.user_message_id == message.id
    worker_socket.chat.chat_service.wake_chat_worker.assert_called_once_with(job.id)


def test_when_the_job_cannot_be_saved_neither_is_the_message_and_no_ack_goes_out(worker_socket, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("job insert failed")

    monkeypatch.setattr(worker_socket.chat.chat_service, "enqueue_job", broken)
    turn_id = uuid.uuid4()
    frames = []
    with worker_socket.client.websocket_connect("/api/chat/ws", headers=worker_socket.headers) as ws:
        ws.send_json(_run(turn_id, worker_socket.conversation_id))
        with pytest.raises(WebSocketDisconnect):
            for _ in range(10):
                frames.append(ws.receive_json())
    assert "ack" not in [f["type"] for f in frames]
    with worker_socket.db() as db:
        assert db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id)) is None
        assert db.scalar(select(ChatJob).where(ChatJob.turn_id == turn_id)) is None


def test_a_resend_of_an_orphaned_message_creates_its_job(worker_socket):
    from app.services import chat as chat_service

    turn_id = uuid.uuid4()
    with worker_socket.db() as db:
        conversation = chat_service.get_conversation(db, user_id=worker_socket.user_id,
                                                     conversation_id=worker_socket.conversation_id)
        # Saved before the two were one commit, and the backend stopped before the job.
        orphan = chat_service.add_message(db, conversation=conversation, role="user", content="Lost?",
                                          turn_id=turn_id, source_channel="phone")
    with worker_socket.client.websocket_connect("/api/chat/ws", headers=worker_socket.headers) as ws:
        ws.send_json(_run(turn_id, worker_socket.conversation_id))
        ack = _until(ws, "ack")
        ws.send_json(_run(turn_id, worker_socket.conversation_id))
        again = _until(ws, "ack")
    assert ack["user_message_id"] == again["user_message_id"] == str(orphan.id)
    with worker_socket.db() as db:
        jobs = db.scalars(select(ChatJob).where(ChatJob.turn_id == turn_id)).all()
        assert len(jobs) == 1 and jobs[0].user_message_id == orphan.id
        assert jobs[0].payload["content"] == "Lost?"
        assert len(db.scalars(select(ChatMessage).where(ChatMessage.turn_id == turn_id)).all()) == 1


def test_two_repairs_of_one_orphan_at_once_make_one_job_and_keep_both_sockets(worker_socket, monkeypatch):
    from app.services import chat as chat_service

    turn_id = uuid.uuid4()
    with worker_socket.db() as db:
        conversation = chat_service.get_conversation(db, user_id=worker_socket.user_id,
                                                     conversation_id=worker_socket.conversation_id)
        orphan = chat_service.add_message(db, conversation=conversation, role="user", content="Lost?",
                                          turn_id=turn_id, source_channel="phone")
    real_enqueue = chat_service.enqueue_job
    raced = []

    def racing_enqueue(db, **kwargs):
        # The other socket's repair commits its job between this one's check and its insert.
        if not raced:
            raced.append(True)
            with worker_socket.db() as other:
                rival = chat_service.get_conversation(other, user_id=worker_socket.user_id,
                                                      conversation_id=worker_socket.conversation_id)
                real_enqueue(other, user_id=worker_socket.user_id, conversation=rival, turn_id=turn_id,
                             user_message_id=orphan.id, payload={"content": "Lost?"}, commit=False)
                other.commit()
        return real_enqueue(db, **kwargs)

    monkeypatch.setattr(worker_socket.chat.chat_service, "enqueue_job", racing_enqueue)
    with worker_socket.client.websocket_connect("/api/chat/ws", headers=worker_socket.headers) as first, \
            worker_socket.client.websocket_connect("/api/chat/ws", headers=worker_socket.headers) as second:
        first.send_json(_run(turn_id, worker_socket.conversation_id))
        assert _until(first, "ack")["user_message_id"] == str(orphan.id)
        second.send_json(_run(turn_id, worker_socket.conversation_id))
        assert _until(second, "ack")["user_message_id"] == str(orphan.id)
        # Neither socket closed.
        for ws in (first, second):
            ws.send_json({"type": "invalid"})
            assert _until(ws, "error")
    assert raced
    with worker_socket.db() as db:
        assert len(db.scalars(select(ChatJob).where(ChatJob.turn_id == turn_id)).all()) == 1


# --------------------------------------------------------------------------- #
# Spoken aloud = all the audio went out (real Redis, db 15)
# --------------------------------------------------------------------------- #
@pytest.fixture
def voice_socket(monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run Redis pub/sub tests")
    from app.api.routes import chat

    settings = get_settings().model_copy(update={"redis_url": url, "voice_enabled": True})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat.chat_worker, "event_bus_available", lambda: True)
    monkeypatch.setattr(chat, "SESSION_CHECK_SECONDS", 0.05)
    sessions = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: sessions)
    user = SimpleNamespace(id=uuid.uuid4(), name="Owner", email="owner@example.com",
                           created_at=datetime.now(timezone.utc))
    db = Mock()
    db.get.return_value = user
    monkeypatch.setattr(chat, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat, "_active_jobs_for_user", lambda user_id: [])
    state = Mock()
    state.to_dict.return_value = {}
    state.describe.return_value = ""
    monkeypatch.setattr(chat.awareness_service, "build", lambda *a, **kw: state)
    monkeypatch.setattr(chat.voice_target, "refresh", Mock())
    monkeypatch.setattr(chat.voice_target, "claim", Mock())
    spoken = []
    monkeypatch.setattr(chat, "mark_spoken", spoken.append)
    publisher = redis.from_url(url)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    cookie = auth.create_session(user)
    headers = {"Cookie": f"{settings.session_cookie_name}={cookie}", "Origin": settings.cors_origins[0]}

    def say(synthesize) -> list[str]:
        """Deliver one unprompted message to a live, idle voice session, with this TTS."""
        monkeypatch.setattr(chat.tts_service, "synthesize", synthesize)
        spoken.clear()
        message_id = str(uuid.uuid4())
        channel = chat.agent_say.channel(user.id)
        with TestClient(app, base_url="https://testserver") as client, \
                client.websocket_connect("/api/chat/ws", headers=headers) as ws:
            ws.send_json({"type": "sense", "hearing_enabled": True, "voice_state": "idle"})
            deadline = time.time() + 5
            while publisher.pubsub_numsub(channel)[0][1] < 1:
                assert time.time() < deadline, "delivery loop never subscribed"
                time.sleep(0.05)
            publisher.publish(channel, json.dumps({"conversation_id": str(uuid.uuid4()), "text": "Look at this.",
                                                   "reason": "follow_up", "message_id": message_id}))
            _until(ws, "nudge_end", limit=30)
        return spoken, message_id

    yield say
    publisher.close()


async def _speaks(clause, report=None):
    yield b"\x00\x00" * 8


async def _tts_fails(clause, report=None):
    raise RuntimeError("TTS sidecar down")
    yield b""  # pragma: no cover


async def _tts_silent(clause, report=None):
    # services/tts.synthesize when the sidecar is down: no error, just no audio.
    return
    yield b""  # pragma: no cover


async def _tts_drops_mid_stream(clause, report=None):
    # services/tts.synthesize when the sidecar connection breaks after the first chunk.
    yield b"\x00\x00" * 8
    if report is not None:
        report.failed = True


async def _socket_drops(clause, report=None):
    yield b"\x00\x00"
    raise WebSocketDisconnect(1006)


def test_a_message_said_in_full_is_marked_spoken(voice_socket):
    spoken, message_id = voice_socket(_speaks)
    assert spoken == [message_id]


@pytest.mark.parametrize("synthesize", [_tts_fails, _tts_silent, _tts_drops_mid_stream, _socket_drops],
                         ids=["tts-fails", "tts-silent", "tts-drops-mid-stream", "socket-drops"])
def test_speech_that_failed_or_was_cut_off_is_not_marked_spoken(voice_socket, synthesize):
    spoken, _ = voice_socket(synthesize)
    assert spoken == []


def test_synthesize_reports_a_stream_that_breaks_after_the_first_chunk(monkeypatch):
    import asyncio

    import httpx

    from app.services import tts

    class Breaks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x01\x00" * 64
            raise httpx.ReadError("sidecar went away")

    real = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Breaks())), **kw))

    async def run(report):
        return [chunk async for chunk in tts.synthesize("Hello there.", report)]

    report = tts.SpeechReport()
    chunks = asyncio.run(run(report))
    assert chunks and report.failed
    # Callers that pass no report still just get the audio that came (no error).
    assert asyncio.run(run(None))
