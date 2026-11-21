"""Phone push notifications (frontend-phone/PLAN_PHASE_0-3.md, phase 2): presence, the device endpoints, and the
push outbox deciding, a few seconds later, whether her message still needs to reach the phone."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.services import auth, fcm, presence, push
from db_support import create_conversation, create_user, database, db_env  # noqa: F401
from test_phone_auth import FakeRedis


class FakeHashRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hdel(self, key, *fields):
        for field in fields:
            self.hashes.get(key, {}).pop(field, None)

    def expire(self, key, seconds):
        return True


@pytest.fixture
def hashes(monkeypatch):
    fake = FakeHashRedis()
    monkeypatch.setattr(presence, "_client", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# Presence
# --------------------------------------------------------------------------- #
def test_web_counts_only_when_visible_and_used_in_the_last_two_minutes(hashes):
    user, now = uuid4(), time.time()
    presence.report(user, "tab", client="web", visible=True, last_active_at=now - 30, voice_live=False, now=now)
    assert presence.present(user, now=now)
    # Same tab, nobody touched it for over two minutes.
    assert not presence.present(user, now=now + 100)
    presence.report(user, "tab", client="web", visible=True, last_active_at=now - 200, voice_live=False, now=now)
    assert not presence.present(user, now=now)
    presence.report(user, "tab", client="web", visible=False, last_active_at=now, voice_live=False, now=now)
    assert not presence.present(user, now=now)


def test_phone_counts_when_visible_and_entries_die_without_a_refresh(hashes):
    user, now = uuid4(), time.time()
    assert presence.report(user, "phone", client="phone", visible=True, last_active_at=None,
                           voice_live=False, now=now) is True
    assert presence.present(user, now=now)
    assert not presence.present(user, now=now + presence.ENTRY_SECONDS + 1)
    presence.leave(user, "phone")
    assert not presence.present(user, now=now)


def test_report_says_when_the_user_became_present(hashes):
    user, now = uuid4(), time.time()
    assert presence.report(user, "tab", client="web", visible=False, last_active_at=now,
                           voice_live=False, now=now) is False
    assert presence.report(user, "tab", client="web", visible=True, last_active_at=now,
                           voice_live=False, now=now) is True
    # Already present: a second visible app isn't a new arrival.
    assert presence.report(user, "phone", client="phone", visible=True, last_active_at=None,
                           voice_live=False, now=now) is False


def test_no_redis_means_not_present():
    assert presence.present(uuid4()) is False


# --------------------------------------------------------------------------- #
# The outbox (real PostgreSQL)
# --------------------------------------------------------------------------- #
class FakeFCM:
    """Google's token endpoint and FCM's send endpoint, recording what was sent."""

    def __init__(self):
        self.sent: list[dict] = []
        self.reply = (200, {"name": "projects/p/messages/1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append(body["message"])
        status, payload = self.reply
        return httpx.Response(status, json=payload)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def outbox(db_env, monkeypatch, hashes):
    from app.services import chat as chat_service

    sessions = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: sessions)
    sessions.exists = lambda key: int(sessions.get(key) is not None)
    monkeypatch.setattr(fcm, "configured", lambda: True)
    monkeypatch.setattr(fcm._credentials, "access_token", lambda client: "access")
    monkeypatch.setattr(fcm._credentials, "project_id", lambda: "jophiel-test")
    monkeypatch.setattr(chat_service, "announce_seen", lambda *a: None)
    fake = FakeFCM()

    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        user_id, conversation_id = user.id, conversation.id

    def phone(*, show_text=False, enabled=True, fcm_token=None):
        from app.services import phone_devices

        token = auth.create_session(SimpleNamespace(id=user_id), client="phone", device_name="Pixel")
        with db_env.db() as db:
            device = phone_devices.register(db, user_id=user_id, token=token,
                                            fcm_token=fcm_token or f"fcm-{uuid4()}", device_name="Pixel")
            phone_devices.update(db, device, show_text=show_text, notifications_enabled=enabled)
            return SimpleNamespace(token=token, id=device.id)

    def say(text="She says something.", *, spoken=False):
        with db_env.db() as db:
            conversation = chat_service.get_conversation(db, user_id=user_id, conversation_id=conversation_id)
            message = chat_service.add_message(db, conversation=conversation, role="assistant", content=text,
                                               commit=False)
            if spoken:
                message.spoken_at = datetime.now(timezone.utc)
            queued = push.schedule_for_message(db, user_id=user_id, conversation_id=conversation_id,
                                               message_id=message.id)
            db.commit()
            return SimpleNamespace(id=message.id, queued=queued)

    def due():
        """Skip the few seconds' wait."""
        from app.db.models import PushDelivery
        with db_env.db() as db:
            for row in db.scalars(select(PushDelivery).where(PushDelivery.status == "pending")):
                row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

    def drain():
        due()
        client = fake.client()
        while push.run_once(client=client):
            pass

    def rows():
        from app.db.models import PushDelivery
        with db_env.db() as db:
            return list(db.scalars(select(PushDelivery).order_by(PushDelivery.queued_at)))

    def device(device_id):
        from app.db.models import PhoneDevice
        with db_env.db() as db:
            return db.get(PhoneDevice, device_id)

    return SimpleNamespace(user_id=user_id, conversation_id=conversation_id, fcm=fake, phone=phone, say=say,
                           due=due, drain=drain, rows=rows, device=device, db=db_env.db, sessions=sessions)


def test_pushed_when_nobody_is_present_with_no_text_by_default(outbox):
    phone = outbox.phone()
    message = outbox.say("Something private.")
    assert message.queued == 1
    # Not before its few seconds are up.
    assert push.run_once(client=outbox.fcm.client()) is False
    outbox.drain()
    assert len(outbox.fcm.sent) == 1
    sent = outbox.fcm.sent[0]
    assert sent["data"] == {"type": "message", "conversation_id": str(outbox.conversation_id),
                            "message_id": str(message.id)}
    assert sent["android"] == {"priority": "HIGH"} and "notification" not in sent
    assert [r.status for r in outbox.rows()] == ["sent"]
    assert outbox.device(phone.id).needs_clear is True


def test_show_text_carries_her_words_trimmed(outbox):
    outbox.phone(show_text=True)
    outbox.say("word " * 100)
    outbox.drain()
    text = outbox.fcm.sent[0]["data"]["text"]
    assert len(text) <= push.TEXT_LIMIT and text.endswith("…")


def test_web_visible_and_in_use_means_no_push_but_idle_web_does(outbox):
    outbox.phone()
    presence.report(outbox.user_id, "tab", client="web", visible=True, last_active_at=time.time(),
                    voice_live=False)
    outbox.say()
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].last_error == "user present"
    presence.report(outbox.user_id, "tab", client="web", visible=True, last_active_at=time.time() - 300,
                    voice_live=False)
    outbox.say()
    outbox.drain()
    assert len(outbox.fcm.sent) == 1


def test_phone_open_means_no_push(outbox):
    outbox.phone()
    presence.report(outbox.user_id, "phone-socket", client="phone", visible=True, last_active_at=None,
                    voice_live=False)
    outbox.say()
    outbox.drain()
    assert outbox.fcm.sent == []


def test_spoken_aloud_is_not_pushed(outbox):
    outbox.phone()
    outbox.say(spoken=True)
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].last_error == "spoken aloud"


def test_presence_appearing_in_the_wait_drops_the_push(outbox):
    outbox.phone()
    outbox.say()
    # The user opens the web app during the few seconds before it's due.
    presence.report(outbox.user_id, "tab", client="web", visible=True, last_active_at=time.time(),
                    voice_live=False)
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].status == "dropped"


def test_a_live_voice_session_gets_a_moment_to_say_it_aloud(outbox):
    outbox.phone()
    presence.report(outbox.user_id, "tab", client="web", visible=False, last_active_at=None, voice_live=True)
    message = outbox.say()
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].status == "pending"
    with outbox.db() as db:
        push.mark_spoken(db, message.id)
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].last_error == "spoken aloud"


def test_notifications_off_and_other_conversations_get_nothing(outbox):
    from app.services import chat as chat_service

    outbox.phone(enabled=False)
    assert outbox.say().queued == 0
    outbox.phone()
    with outbox.db() as db:
        other = create_conversation(db, SimpleNamespace(id=outbox.user_id))
        from app.db.models import UserChatState
        db.add(UserChatState(user_id=outbox.user_id, primary_conversation_id=outbox.conversation_id))
        db.commit()
        message = chat_service.add_message(db, conversation=other, role="assistant", content="Elsewhere",
                                           commit=False)
        assert push.schedule_for_message(db, user_id=outbox.user_id, conversation_id=other.id,
                                         message_id=message.id) == 0
        db.commit()


def test_one_push_per_message_and_device(outbox):
    phone = outbox.phone()
    message = outbox.say()
    with outbox.db() as db:
        assert push.schedule_for_message(db, user_id=outbox.user_id, conversation_id=outbox.conversation_id,
                                         message_id=message.id) == 0
        db.commit()
    outbox.drain()
    outbox.drain()
    assert len(outbox.fcm.sent) == 1
    assert [r.device_id for r in outbox.rows()] == [phone.id]


def test_a_dead_token_forgets_the_device(outbox):
    phone = outbox.phone()
    outbox.say()
    outbox.fcm.reply = (404, {"error": {"status": "NOT_FOUND", "message": "Requested entity was not found.",
                                        "details": [{"errorCode": "UNREGISTERED"}]}})
    outbox.drain()
    assert outbox.device(phone.id) is None
    assert outbox.rows() == []


def test_a_transient_failure_retries_and_sends_late(outbox):
    outbox.phone()
    outbox.say()
    outbox.fcm.reply = (503, {"error": {"status": "UNAVAILABLE"}})
    outbox.drain()
    row = outbox.rows()[0]
    assert row.status == "pending" and row.attempts == 1
    outbox.fcm.reply = (200, {})
    outbox.drain()
    assert outbox.rows()[0].status == "sent"


def test_a_device_dies_with_its_session(outbox):
    phone = outbox.phone()
    outbox.say()
    auth.delete_session(phone.token)
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.device(phone.id) is None


def test_clear_goes_to_phones_that_got_notifications_when_the_user_is_back(outbox):
    notified = outbox.phone()
    quiet = outbox.phone()
    with outbox.db() as db:
        from app.db.models import PhoneDevice
        db.get(PhoneDevice, quiet.id).notifications_enabled = False
        db.commit()
    outbox.say()
    outbox.drain()
    assert len(outbox.fcm.sent) == 1
    with outbox.db() as db:
        assert push.schedule_clear(db, user_id=outbox.user_id) == 1
        # Nothing new since: no second clear.
        assert push.schedule_clear(db, user_id=outbox.user_id) == 0
    outbox.drain()
    assert [m["data"]["type"] for m in outbox.fcm.sent] == ["message", "clear"]
    assert outbox.device(notified.id).needs_clear is False


def test_without_a_service_account_nothing_is_sent(outbox, monkeypatch):
    monkeypatch.setattr(fcm, "configured", lambda: False)
    outbox.phone()
    outbox.say()
    outbox.drain()
    assert outbox.fcm.sent == [] and outbox.rows()[0].last_error == "push not configured"


# --------------------------------------------------------------------------- #
# The device endpoints
# --------------------------------------------------------------------------- #
@pytest.fixture
def api(outbox):
    from app.api.routes import auth as auth_routes
    from app.api.routes import phone as phone_routes
    from app.db.session import get_db

    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(phone_routes.router, prefix="/api")

    def db():
        with outbox.db() as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_register_update_and_signout(api, outbox):
    token = auth.create_session(SimpleNamespace(id=outbox.user_id), client="phone", device_name="Pixel")
    headers = {"Authorization": f"Bearer {token}"}
    assert api.get("/api/phone/device", headers=headers).status_code == 404
    response = api.put("/api/phone/device", headers=headers, json={"fcm_token": "fcm-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["device_name"] == "Pixel" and body["notifications_enabled"] and not body["show_text"]
    # A refreshed FCM token updates the same device.
    again = api.put("/api/phone/device", headers=headers, json={"fcm_token": "fcm-2"}).json()
    assert again["id"] == body["id"]
    patched = api.patch("/api/phone/device", headers=headers, json={"show_text": True}).json()
    assert patched["show_text"] and patched["notifications_enabled"]
    assert api.post("/api/auth/signout", headers=headers).status_code == 204
    from app.db.models import PhoneDevice
    with outbox.db() as db:
        assert db.scalars(select(PhoneDevice)).all() == []


def test_signing_in_again_on_the_same_phone_replaces_the_old_device(api, outbox):
    old = auth.create_session(SimpleNamespace(id=outbox.user_id), client="phone")
    new = auth.create_session(SimpleNamespace(id=outbox.user_id), client="phone")
    api.put("/api/phone/device", headers={"Authorization": f"Bearer {old}"}, json={"fcm_token": "same"})
    api.put("/api/phone/device", headers={"Authorization": f"Bearer {new}"}, json={"fcm_token": "same"})
    from app.db.models import PhoneDevice
    with outbox.db() as db:
        devices = db.scalars(select(PhoneDevice)).all()
    assert len(devices) == 1 and devices[0].session_hash == auth.session_key_hash(new)


def test_web_sessions_cannot_register_a_device(api, outbox):
    cookie = auth.create_session(SimpleNamespace(id=outbox.user_id))
    response = api.put("/api/phone/device", json={"fcm_token": "x"}, headers={
        "Cookie": f"{get_settings().session_cookie_name}={cookie}", "Origin": get_settings().cors_origins[0]})
    assert response.status_code == 403
    # No credentials at all: turned away (by the origin check, before authentication).
    assert api.put("/api/phone/device", json={"fcm_token": "x"}).status_code == 403
    assert api.get("/api/phone/device").status_code == 401


def test_the_socket_reports_presence_from_sense_frames_and_leaves_on_close(hashes, monkeypatch):
    from app.api.routes import chat

    sessions = FakeRedis()
    monkeypatch.setattr(auth, "get_redis", lambda: sessions)
    user = SimpleNamespace(id=uuid4(), name="Owner", email="owner@example.com",
                           created_at=datetime.now(timezone.utc))
    from unittest.mock import Mock
    db = Mock()
    db.get.return_value = user
    monkeypatch.setattr(chat, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat.chat_service, "user_turn", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "SESSION_CHECK_SECONDS", 0.05)
    state = Mock()
    state.to_dict.return_value = {}
    state.describe.return_value = ""
    monkeypatch.setattr(chat.awareness_service, "build", lambda *a, **kw: state)
    monkeypatch.setattr(chat.push, "schedule_clear", Mock(return_value=0))
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    token = auth.create_session(user, client="phone")
    with TestClient(app, base_url="https://testserver") as client:
        with client.websocket_connect("/api/chat/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
            assert ws.receive_json()["type"] == "state"
            ws.send_json({"type": "sense", "client": "phone", "visible": True})
            for _ in range(5):
                if ws.receive_json()["type"] == "state":
                    break
            assert presence.present(user.id)
            chat.push.schedule_clear.assert_called_once()
            ws.send_json({"type": "sense", "client": "phone", "visible": False})
            for _ in range(5):
                if ws.receive_json()["type"] == "state":
                    break
            assert not presence.present(user.id)
        assert hashes.hgetall(f"presence:{user.id}") == {}


@pytest.mark.parametrize("reply", [
    (404, {"error": {"status": "NOT_FOUND", "message": "Requested entity was not found."}}),
    (400, {"error": {"status": "INVALID_ARGUMENT", "message": "Invalid value at 'message.data[0].value'",
                     "details": [{"fieldViolations": [{"field": "message.data[0].value"}]}]}}),
])
def test_a_wrong_project_or_bad_payload_never_deletes_the_phone(outbox, reply):
    phone = outbox.phone()
    outbox.say()
    outbox.fcm.reply = reply
    outbox.drain()
    assert outbox.device(phone.id) is not None
    row = outbox.rows()[0]
    assert row.status == "failed"
    if reply[0] == 404:
        assert "check the project ID" in row.last_error


def test_fcm_naming_the_token_invalid_forgets_the_device(outbox):
    phone = outbox.phone()
    outbox.say()
    outbox.fcm.reply = (400, {"error": {"status": "INVALID_ARGUMENT", "message": "The registration token is not valid",
                                        "details": [{"fieldViolations": [{"field": "message.token"}]}]}})
    outbox.drain()
    assert outbox.device(phone.id) is None
