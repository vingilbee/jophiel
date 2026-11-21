"""HTTP and WebSocket logout coverage with isolated session and user storage."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.auth import router
from app.core.config import get_settings
from app.db.session import get_db
from app.services import auth


@pytest.fixture
def auth_client(monkeypatch):
    sessions = {}
    redis = Mock()
    redis.setex.side_effect = lambda key, ttl, value: sessions.update({key: value})
    redis.get.side_effect = sessions.get
    redis.delete.side_effect = lambda key: int(sessions.pop(key, None) is not None)
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    user = SimpleNamespace(
        id=uuid4(), name="Test User", email="test@example.com",
        created_at=datetime.now(timezone.utc),
    )
    db = Mock()
    db.get.return_value = user
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(
        app, base_url="https://testserver",
        headers={"Origin": get_settings().cors_origins[0]},
    ) as client:
        yield client, user


def test_signup_cookie_authenticates_subsequent_request(auth_client, monkeypatch):
    client, user = auth_client
    monkeypatch.setattr(auth, "create_user", Mock(return_value=user))
    assert client.get("/api/auth/me").status_code == 401
    response = client.post("/api/auth/signup", json={
        "name": user.name, "email": user.email, "password": "signup-test-password",
    })
    assert response.status_code == 201
    cookie_name = get_settings().session_cookie_name
    token = response.cookies.get(cookie_name)
    assert token
    assert "HttpOnly" in response.headers["set-cookie"]
    # A new client has no React state or previous browser cookies.
    with TestClient(client.app, base_url="https://testserver") as refreshed:
        me = refreshed.get("/api/auth/me", headers={"Cookie": f"{cookie_name}={token}"})
    assert me.status_code == 200
    assert me.json()["id"] == response.json()["id"] == str(user.id)
    assert client.post("/api/auth/signout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_signup_does_not_create_session(auth_client, monkeypatch):
    client, user = auth_client
    monkeypatch.setattr(auth, "create_user", Mock(side_effect=auth.EmailAlreadyExists(user.email)))
    create_session = Mock()
    monkeypatch.setattr(auth, "create_session", create_session)
    response = client.post("/api/auth/signup", json={
        "name": user.name, "email": user.email, "password": "signup-test-password",
    })
    assert response.status_code == 409
    assert "set-cookie" not in response.headers
    create_session.assert_not_called()


def test_logout_revokes_cookie_without_revoking_other_sessions(auth_client):
    client, user = auth_client
    token = auth.create_session(user)
    other_token = auth.create_session(user)
    cookie_name = get_settings().session_cookie_name
    headers = {"Cookie": f"{cookie_name}={token}"}

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    response = client.post("/api/auth/signout", headers=headers)
    assert response.status_code == 204
    assert f'{cookie_name}=""' in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get(
        "/api/auth/me", headers={"Cookie": f"{cookie_name}={other_token}"}
    ).status_code == 200
    assert client.post("/api/auth/signout", headers=headers).status_code == 204


@pytest.mark.parametrize("token", [None, "unknown-session-token"])
def test_logout_without_valid_session_is_idempotent(auth_client, token):
    client, _ = auth_client
    headers = {} if token is None else {
        "Cookie": f"{get_settings().session_cookie_name}={token}"
    }
    assert client.post("/api/auth/signout", headers=headers).status_code == 204


@pytest.fixture
def socket_client(auth_client, monkeypatch):
    from app.api.routes import chat

    client, user = auth_client
    db = Mock()
    db.get.return_value = user
    monkeypatch.setattr(chat, "SessionLocal", lambda: db)
    # These tests exercise the in-process stream path. CI starts Redis for
    # integration coverage, but no chat worker is running to publish events.
    monkeypatch.setattr(chat.chat_worker, "event_bus_available", lambda: False)
    monkeypatch.setattr(chat, "SESSION_CHECK_SECONDS", 0.05)
    # The mocked database has no earlier sends: every run is a new message, not a resend.
    monkeypatch.setattr(chat.chat_service, "user_turn", lambda *a, **kw: None)
    state = Mock()
    state.to_dict.return_value = {}
    state.describe.return_value = "Test awareness"
    monkeypatch.setattr(chat.awareness_service, "build", lambda *a, **kw: state)
    client.app.include_router(chat.router, prefix="/api")
    return client, user, chat


@pytest.mark.parametrize("frame", ["run", "binary", "idle"])
def test_open_socket_closes_after_logout(socket_client, frame):
    from starlette.websockets import WebSocketDisconnect

    client, user, chat = socket_client
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        assert client.post("/api/auth/signout", headers=headers).status_code == 204
        if frame == "run":
            ws.send_json({"type": "run", "content": "Must not run"})
        elif frame == "binary":
            ws.send_bytes(b"\x00\x00")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
        assert user.id not in chat._active_turns


@pytest.mark.parametrize("origin", ["https://hostile.example", "null", None])
def test_untrusted_origin_cannot_upload(auth_client, monkeypatch, origin):
    from app.api.routes import images

    client, user = auth_client
    client.app.include_router(images.router, prefix="/api")
    client.headers.pop("Origin")
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    if origin is not None:
        headers["Origin"] = origin
    save = Mock()
    monkeypatch.setattr(images.image_store, "save_bytes", save)
    response = client.post(
        "/api/images", headers=headers,
        files={"file": ("test.png", b"image content", "image/png")},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "invalid origin"
    save.assert_not_called()


@pytest.mark.parametrize("origin", ["https://hostile.example", "null", None])
def test_untrusted_origin_cannot_open_socket(socket_client, monkeypatch, origin):
    from starlette.websockets import WebSocketDisconnect

    client, user, chat = socket_client
    client.headers.pop("Origin")
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    if origin is not None:
        headers["Origin"] = origin
    database = Mock(side_effect=AssertionError("Origin must be checked before authentication"))
    monkeypatch.setattr(chat, "SessionLocal", database)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/chat/ws", headers=headers):
            pytest.fail("Untrusted origin was accepted")
    assert closed.value.code == 1008
    database.assert_not_called()


def test_all_mutating_http_routes_require_origin():
    from app.api.deps import validate_origin
    from app.api.routes import assets, auth, chat, dashboard, images, integrations, predict

    # Phone sign-in sets no cookie, so there's nothing for a forged cross-site request to ride on.
    no_cookie = {"/auth/token"}
    for module in (assets, auth, chat, dashboard, images, integrations, predict):
        for route in module.router.routes:
            if route.path in no_cookie:
                continue
            if getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}:
                assert any(
                    dependency.call is validate_origin
                    for dependency in route.dependant.dependencies
                ), f"Missing origin validation: {route.path}"


def test_direct_revocation_closes_only_affected_session(socket_client):
    from starlette.websockets import WebSocketDisconnect

    client, user, _ = socket_client
    token, other = auth.create_session(user), auth.create_session(user)
    cookie = get_settings().session_cookie_name
    with client.websocket_connect(
        "/api/chat/ws", headers={"Cookie": f"{cookie}={token}"}
    ) as ws, client.websocket_connect(
        "/api/chat/ws", headers={"Cookie": f"{cookie}={other}"}
    ) as other_ws:
        assert ws.receive_json()["type"] == "state"
        assert other_ws.receive_json()["type"] == "state"
        auth.delete_session(token)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
        other_ws.send_json({"type": "invalid"})
        assert other_ws.receive_json()["type"] == "error"


def test_socket_fails_closed_when_session_store_is_unavailable(socket_client, monkeypatch):
    from redis.exceptions import ConnectionError
    from starlette.websockets import WebSocketDisconnect

    client, user, _ = socket_client
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        monkeypatch.setattr(auth, "get_redis", Mock(side_effect=ConnectionError("Unavailable")))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_revoked_cookie_cannot_reconnect(socket_client):
    from starlette.websockets import WebSocketDisconnect

    client, user, _ = socket_client
    token = auth.create_session(user)
    auth.delete_session(token)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            "/api/chat/ws",
            headers={"Cookie": f"{get_settings().session_cookie_name}={token}"},
        ):
            pytest.fail("Revoked session was accepted")
    assert closed.value.code == 1008


def test_logout_cancels_streaming_reply(socket_client, monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    from starlette.websockets import WebSocketDisconnect

    client, user, chat = socket_client
    cancelled = threading.Event()

    async def stream(**kwargs):
        try:
            yield {"type": "token", "content": "Partial reply"}
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    conversation = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(chat, "stream_chat", stream)
    monkeypatch.setattr(chat.chat_service, "get_or_create_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "get_conversation", Mock(return_value=conversation))
    monkeypatch.setattr(chat.chat_service, "add_message", Mock())
    monkeypatch.setattr(chat.chat_service, "get_history", Mock(return_value=[]))
    monkeypatch.setattr(chat.google, "get_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.google_account, "get_for_user", Mock(return_value=None))
    monkeypatch.setattr(chat.memory_service, "retrieve_for_prompt", Mock(return_value=""))
    headers = {"Cookie": f"{get_settings().session_cookie_name}={auth.create_session(user)}"}
    with client.websocket_connect("/api/chat/ws", headers=headers) as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "run", "content": "Start a reply"})
        assert ws.receive_json()["type"] == "meta"
        assert ws.receive_json()["type"] == "ack"
        # Awareness updates are independent of chat streaming and may arrive
        # between the acknowledgement and the first model token, as does the
        # turn's route frame (shared-state pipeline).
        for _ in range(5):
            frame = ws.receive_json()
            if frame["type"] == "token":
                break
            assert frame["type"] in ("state", "route", "seen")
        else:
            pytest.fail("stream did not produce a token")
        assert client.post("/api/auth/signout", headers=headers).status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
        assert cancelled.is_set()
        assert user.id not in chat._active_turns
