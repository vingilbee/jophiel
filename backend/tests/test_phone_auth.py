"""The phone app's way in (frontend-phone/PLAN_PHASE_0-3.md, phase 1): bearer tokens on REST and the chat socket,
the origin rule, 90-day sliding sessions, sign-out, and what a phone message carries (source, written-at,
idempotent resend, the second tick)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.websockets import WebSocketDisconnect

from app.api.routes.auth import router
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.services import auth
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


class FakeRedis:
    """setex / get / delete / ttl / expire over a dict, with a clock the test moves."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.expires: dict[str, float] = {}
        self.now = 1_000_000.0

    def _live(self, key):
        if key in self.expires and self.expires[key] <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)
        return key in self.values

    def setex(self, key, ttl, value):
        seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else ttl
        self.values[key] = value
        self.expires[key] = self.now + seconds

    def get(self, key):
        return self.values[key] if self._live(key) else None

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    def ttl(self, key):
        return int(self.expires[key] - self.now) if self._live(key) else -2

    def expire(self, key, seconds):
        if self._live(key):
            self.expires[key] = self.now + seconds
            return True
        return False


@pytest.fixture
def phone(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    user = SimpleNamespace(id=uuid4(), name="Owner", email="owner@example.com",
                           created_at=datetime.now(timezone.utc))
    monkeypatch.setattr(auth, "authenticate",
                        lambda db, email, password: user if password == "right-password" else None)
    db = Mock()
    db.get.return_value = user
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    limiter.reset()
    # No Origin header: React Native's fetch sends none.
    with TestClient(app, base_url="https://testserver") as client:
        yield SimpleNamespace(client=client, user=user, redis=redis, app=app)
    limiter.reset()


def sign_in(phone, password="right-password"):
    return phone.client.post("/api/auth/token", json={
        "email": phone.user.email, "password": password, "device_name": "Pixel emulator"})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
def test_token_sign_in_returns_a_bearer_and_sets_no_cookie(phone):
    response = sign_in(phone)
    assert response.status_code == 200
    body = response.json()
    assert body["token"] and body["user"]["id"] == str(phone.user.id)
    assert "set-cookie" not in response.headers
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=89) < expires - datetime.now(timezone.utc) <= timedelta(days=90)
    record = auth.get_session(body["token"])
    assert record == {"user_id": str(phone.user.id), "client": "phone", "device_name": "Pixel emulator"}
    me = phone.client.get("/api/auth/me", headers=bearer(body["token"]))
    assert me.status_code == 200 and me.json()["id"] == str(phone.user.id)


def test_wrong_password_is_401(phone):
    response = sign_in(phone, password="wrong")
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_token_sign_in_is_rate_limited_like_signin(phone):
    codes = [sign_in(phone, password="wrong").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_bearer_passes_the_origin_check_and_signout_ends_only_that_session(phone):
    token = sign_in(phone).json()["token"]
    other = sign_in(phone).json()["token"]
    # signout requires validate_origin; the phone sends no Origin.
    assert phone.client.post("/api/auth/signout", headers=bearer(token)).status_code == 204
    assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert phone.client.get("/api/auth/me", headers=bearer(other)).status_code == 200


def test_cookie_without_origin_still_fails_the_origin_check(phone):
    cookie = auth.create_session(phone.user)
    response = phone.client.post(
        "/api/auth/signout", headers={"Cookie": f"{get_settings().session_cookie_name}={cookie}"})
    assert response.status_code == 403
    assert auth.get_session(cookie) is not None


def test_invalid_bearer_does_not_pass_the_origin_check_nor_fall_back_to_the_cookie(phone):
    cookie = auth.create_session(phone.user)
    headers = {**bearer("not-a-session"), "Cookie": f"{get_settings().session_cookie_name}={cookie}"}
    assert phone.client.post("/api/auth/signout", headers=headers).status_code == 403
    # A bearer header is the only credential looked at: the valid cookie riding along doesn't count.
    assert phone.client.get("/api/auth/me", headers=headers).status_code == 401
    assert auth.get_session(cookie) is not None


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Basic abc", "token"])
def test_malformed_authorization_is_not_authenticated(phone, header):
    assert phone.client.get("/api/auth/me", headers={"Authorization": header}).status_code == 401


def test_expired_and_revoked_tokens_are_401(phone):
    token = sign_in(phone).json()["token"]
    phone.redis.now += get_settings().phone_session_lifetime_seconds + 1
    assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    token = sign_in(phone).json()["token"]
    auth.delete_session(token)
    assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_phone_sessions_slide_and_web_sessions_do_not(phone):
    lifetime = get_settings().phone_session_lifetime_seconds
    token = sign_in(phone).json()["token"]
    key = f"session:{auth.session_key_hash(token)}"
    # Used with more than half left: not extended.
    phone.redis.now += lifetime * 0.4
    assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    assert phone.redis.ttl(key) == pytest.approx(lifetime * 0.6, abs=1)
    # Less than half left: back to the full lifetime.
    phone.redis.now += lifetime * 0.2
    assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    assert phone.redis.ttl(key) == pytest.approx(lifetime, abs=1)
    # Used now and then, it never runs out.
    for _ in range(5):
        phone.redis.now += lifetime * 0.7
        assert phone.client.get("/api/auth/me", headers=bearer(token)).status_code == 200

    web_lifetime = get_settings().session_lifetime_seconds
    cookie = auth.create_session(phone.user)
    web_key = f"session:{auth.session_key_hash(cookie)}"
    phone.redis.now += web_lifetime * 0.9
    assert auth.get_session(cookie) is not None
    assert phone.redis.ttl(web_key) == pytest.approx(web_lifetime * 0.1, abs=1)


# --------------------------------------------------------------------------- #
# The chat socket
# --------------------------------------------------------------------------- #
@pytest.fixture
def socket(phone, monkeypatch):
    from app.api.routes import chat

    db = Mock()
    db.get.return_value = phone.user
    monkeypatch.setattr(chat, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat.chat_worker, "event_bus_available", lambda: False)
    monkeypatch.setattr(chat, "SESSION_CHECK_SECONDS", 0.05)
    state = Mock()
    state.to_dict.return_value = {}
    state.describe.return_value = "Test awareness"
    monkeypatch.setattr(chat.awareness_service, "build", lambda *a, **kw: state)
    phone.app.include_router(chat.router, prefix="/api")
    return SimpleNamespace(**vars(phone), chat=chat, db=db)


def test_bearer_opens_the_socket_without_an_origin(socket):
    token = sign_in(socket).json()["token"]
    with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)) as ws:
        assert ws.receive_json()["type"] == "state"


@pytest.mark.parametrize("token", ["not-a-session", "expired"])
def test_bad_bearer_closes_the_socket_with_1008(socket, token):
    if token == "expired":
        token = sign_in(socket).json()["token"]
        socket.redis.now += get_settings().phone_session_lifetime_seconds + 1
    with pytest.raises(WebSocketDisconnect) as closed:
        with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)):
            pytest.fail("A bad bearer token was accepted")
    assert closed.value.code == 1008


def test_phone_signout_closes_its_open_socket(socket):
    token = sign_in(socket).json()["token"]
    with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)) as ws:
        assert ws.receive_json()["type"] == "state"
        assert socket.client.post("/api/auth/signout", headers=bearer(token)).status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_cookie_socket_without_origin_still_fails(socket):
    cookie = auth.create_session(socket.user)
    with pytest.raises(WebSocketDisconnect) as closed:
        with socket.client.websocket_connect(
                "/api/chat/ws", headers={"Cookie": f"{get_settings().session_cookie_name}={cookie}"}):
            pytest.fail("Cookie without origin was accepted")
    assert closed.value.code == 1008


@pytest.fixture
def turns(socket, monkeypatch):
    """The in-process reply path with the database mocked: what add_message was given."""
    chat = socket.chat
    conversation = SimpleNamespace(id=uuid4(), user_id=socket.user.id)
    saved = {}

    def add_message(db, *, conversation, role, content, turn_id=None, message_id=None, **kwargs):
        message = SimpleNamespace(id=message_id or uuid4(), turn_id=turn_id, role=role, content=content,
                                  conversation_id=conversation.id, seen_at=None, **kwargs)
        if role == "user":
            saved[turn_id] = message
        return message

    async def stream(**kwargs):
        yield {"type": "token", "content": "Hello"}

    mind = Mock()
    mind.event.return_value = {"type": "mind"}
    mind.silent = None
    monkeypatch.setattr(chat, "stream_chat", stream)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "add_message", Mock(side_effect=add_message))
    monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
    monkeypatch.setattr(chat.chat_service, "mark_seen", Mock(return_value=True))
    monkeypatch.setattr(chat.chat_service, "user_turn",
                        lambda db, *, user_id, turn_id: saved.get(turn_id))
    monkeypatch.setattr(chat.chat_service, "list_conversations", Mock(return_value=[]))
    monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
    monkeypatch.setattr(chat.mind_turn, "prepare_message", Mock(return_value=mind))
    monkeypatch.setattr(chat.mind_turn, "finish", Mock())
    monkeypatch.setattr(chat.mind_turn, "fresh_awareness", Mock(return_value=""))
    monkeypatch.setattr(chat.chat_worker, "_personality", Mock(return_value=""))
    monkeypatch.setattr(chat.memory_manager, "view_for", Mock(return_value=""))
    monkeypatch.setattr(chat.desktop_game, "summary_for", Mock(return_value=None))
    monkeypatch.setattr(chat.desktop_control, "for_turn", Mock(return_value=(None, False, False)))
    monkeypatch.setattr(chat.security_activity, "line_for", Mock(return_value=None))
    monkeypatch.setattr(chat.recall_references, "save_receipt", Mock(return_value=None))
    monkeypatch.setattr(chat.recall_references, "start_association", Mock())
    monkeypatch.setattr(chat.memory_service, "schedule_consolidation", Mock())
    return SimpleNamespace(saved=saved, add_message=chat.chat_service.add_message)


def frames_until(ws, kind, limit=20):
    seen = []
    for _ in range(limit):
        frame = ws.receive_json()
        seen.append(frame)
        if frame["type"] == kind:
            return seen
    pytest.fail(f"no {kind} frame in {[f['type'] for f in seen]}")


def test_phone_message_is_recorded_as_phone_with_its_written_time_clamped(socket, turns):
    token = sign_in(socket).json()["token"]
    turn_id = str(uuid4())
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)) as ws:
        ws.send_json({"type": "run", "content": "From the phone", "turn_id": turn_id, "written_at": future})
        frames = frames_until(ws, "done")
    types = [f["type"] for f in frames]
    assert types.index("ack") < types.index("seen") < types.index("done")
    user_calls = [c for c in turns.add_message.call_args_list if c.kwargs["role"] == "user"]
    assert len(user_calls) == 1
    assert user_calls[0].kwargs["source_channel"] == "phone"
    assert user_calls[0].kwargs["written_at"] <= datetime.now(timezone.utc)


def test_web_message_stays_web(socket, turns):
    cookie = auth.create_session(socket.user)
    headers = {"Cookie": f"{get_settings().session_cookie_name}={cookie}",
               "Origin": get_settings().cors_origins[0]}
    with socket.client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        ws.send_json({"type": "run", "content": "From the web"})
        frames_until(ws, "done")
    user_calls = [c for c in turns.add_message.call_args_list if c.kwargs["role"] == "user"]
    assert user_calls[0].kwargs["source_channel"] == "web"
    assert user_calls[0].kwargs["written_at"] is None


def test_a_resend_of_the_same_turn_is_one_message_with_the_same_ack(socket, turns):
    token = sign_in(socket).json()["token"]
    turn_id = str(uuid4())
    frame = {"type": "run", "content": "Only once", "turn_id": turn_id,
             "written_at": datetime.now(timezone.utc).isoformat()}
    with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)) as ws:
        ws.send_json(frame)
        first = next(f for f in frames_until(ws, "done") if f["type"] == "ack")
        ws.send_json(frame)
        second = frames_until(ws, "ack")[-1]
    # A reconnect (the phone's queue after a dropped socket) resends too.
    with socket.client.websocket_connect("/api/chat/ws", headers=bearer(token)) as ws:
        ws.send_json(frame)
        third = frames_until(ws, "ack")[-1]
    assert first == second == third
    user_calls = [c for c in turns.add_message.call_args_list if c.kwargs["role"] == "user"]
    assert len(user_calls) == 1


def test_the_phone_is_a_fact_in_her_awareness():
    from app.services.awareness import AwarenessSession, AwarenessState

    session = AwarenessSession(uuid4())
    session.update(client="phone", route="/chat")
    state = AwarenessState(now=datetime.now(timezone.utc), zone_name="UTC", part_of_day="morning",
                           session_seconds=10, since_last_message_seconds=None, calories_today=0,
                           entries_today=0, days_since_last_log=None, senses=session.report)
    assert "from the Jophiel app on their phone" in state.describe()
    assert state.to_dict()["senses"]["client"] == "phone"


# --------------------------------------------------------------------------- #
# Written-at in her history
# --------------------------------------------------------------------------- #
def test_her_history_says_when_a_message_waited_on_the_phone():
    from app.services import chat as chat_service

    now = datetime.now(timezone.utc)
    late = SimpleNamespace(role="user", created_at=now, written_at=now - timedelta(hours=9), turn_id=None,
                           status="completed", heard_chars=None)
    prompt = SimpleNamespace(role="user", created_at=now, written_at=now - timedelta(seconds=20), turn_id=None,
                             status="completed", heard_chars=None)
    web = SimpleNamespace(role="user", created_at=now, written_at=None, turn_id=None,
                          status="completed", heard_chars=None)
    assert "written 9 hours before it arrived" in chat_service._marker(late, "UTC", now, current=True)
    assert "written" not in chat_service._marker(prompt, "UTC", now, current=False)
    assert "written" not in chat_service._marker(web, "UTC", now, current=False)


def test_written_at_is_clamped_to_now():
    from app.services.chat import clamp_written_at

    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    assert clamp_written_at(None) is None
    assert clamp_written_at(now + timedelta(hours=1), now) == now
    assert clamp_written_at(now - timedelta(hours=1), now) == now - timedelta(hours=1)
    assert clamp_written_at(datetime(2026, 11, 20, 11), now) == datetime(2026, 11, 20, 11, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The second tick (real PostgreSQL)
# --------------------------------------------------------------------------- #
def test_claiming_the_job_marks_the_message_seen_once_and_tells_the_sockets(db_env, monkeypatch):
    from app.db.models import ChatMessage
    from app.services import chat as chat_service

    announced = []
    monkeypatch.setattr(chat_service, "announce_seen",
                        lambda user_id, message_id, conversation_id: announced.append(message_id))
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid4()
        message = chat_service.add_message(db, conversation=conversation, role="user", content="Hi",
                                           turn_id=turn_id, source_channel="phone",
                                           written_at=datetime.now(timezone.utc) - timedelta(hours=2))
        assert message.seen_at is None
        chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                 user_message_id=message.id, payload={"content": "Hi"}, commit=False)
        db.commit()
        job = chat_service.claim_next_job(db, lease_seconds=60)
        assert job is not None
        seen = db.get(ChatMessage, message.id)
        db.refresh(seen)
        assert seen.seen_at is not None and seen.source_channel == "phone" and seen.written_at is not None
        assert announced == [message.id]
        # A reclaim after a lost lease doesn't stamp it again.
        first = seen.seen_at
        assert chat_service.mark_seen(db, message.id, datetime.now(timezone.utc)) is False
        db.refresh(seen)
        assert seen.seen_at == first
        assert chat_service.user_turn(db, user_id=user.id, turn_id=turn_id).id == message.id
        assert chat_service.user_turn(db, user_id=uuid4(), turn_id=turn_id) is None
