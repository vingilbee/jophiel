"""Phase 7 action delivery, privacy, and late outcomes using the real backend entry points."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import chat as ai_chat
from app.ai.context import AgentRuntimeContext
from app.ai.tools import phone as tools
from app.api.routes import chat, phone
from app.core.config import get_settings
from app.services import auth, fcm, google_maps, mind_phone, phone_actions, phone_devices, presence
from db_support import create_conversation, create_user, database  # noqa: F401


@pytest.fixture
def channel(database, monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to exercise real Redis actions")
    settings = get_settings().model_copy(update={"redis_url": url, "google_maps_api_key": "test-key"})
    with redis.from_url(url) as bus:
        for module in (chat, phone_actions, google_maps, tools):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        monkeypatch.setattr(auth, "get_redis", lambda: bus)
        monkeypatch.setattr(presence, "_client", lambda: bus)
        monkeypatch.setattr(chat, "_active_jobs_for_user", lambda user_id: [])
        monkeypatch.setattr(chat, "AWARENESS_HEARTBEAT_SECONDS", 60)
        monkeypatch.setattr(chat.awareness_service, "build", lambda *args, **kwargs: SimpleNamespace(
            to_dict=lambda: {}))
        with database() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            token = auth.create_session(user, client=auth.PHONE_CLIENT)
            other_phone = auth.create_session(user, client=auth.PHONE_CLIENT)
            web = auth.create_session(user)
            phone_devices.register(db, user_id=user.id, token=token, fcm_token="older-device", device_name="old")
            phone_devices.register(db, user_id=user.id, token=other_phone, fcm_token="newer-device", device_name="new")
        app = FastAPI()
        app.include_router(chat.router, prefix="/api")
        app.include_router(phone.router, prefix="/api")
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, user=user.id, conversation=conversation.id, token=token,
                                  other_phone=other_phone, web=web, settings=settings, bus=bus, db=database)
        for value in (token, other_phone, web):
            auth.delete_session(value)


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def receive(ws, frame_type):
    for _ in range(12):
        frame = ws.receive_json()
        if frame["type"] == frame_type:
            return frame
    pytest.fail(f"No {frame_type} frame")


def wait_for_phone(user):
    deadline = time.monotonic() + 3
    while (target := presence.action_socket(user)) is None:
        assert time.monotonic() < deadline, "Phone never subscribed to actions"
        time.sleep(0.01)
    return target


def result_url(action_id):
    return f"/api/phone/actions/{action_id}/result"


def test_socket_targets_live_phone_and_unblocks_navigation_tool(channel, monkeypatch):
    monkeypatch.setattr(google_maps, "search", lambda *_args, **_kwargs: [
        google_maps.Place("Museum", "10 High Street", (49.15, -123.75), "museum-id")
    ])
    monkeypatch.setattr(fcm, "send", lambda *args, **kwargs: pytest.fail("The action reached a live socket"))
    navigate, *_ = tools.build_phone_tools(channel.user, channel.conversation)
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        assert wait_for_phone(channel.user)[0] == auth.session_key_hash(channel.token)
        with ThreadPoolExecutor() as executor:
            result = executor.submit(asyncio.run, navigate.ainvoke({"action": "navigate_to", "destination": "Museum"}))
            frame = receive(ws, "phone_action")
            assert frame["args"] == {"name": "Museum", "lat": 49.15, "lng": -123.75, "query": None,
                                     "mode": "drive", "place_id": "museum-id"}
            action_id = frame["action_id"]
            url = result_url(action_id)
            for wrong_token, status in ((channel.other_phone, 404), (channel.web, 403)):
                assert channel.client.post(url, headers=headers(wrong_token), json={"status": "done"}).status_code == status
            ws.send_json({"type": "phone_action_result", "action_id": action_id, "status": "done"})
            assert result.result(timeout=5) == "Opened Maps → Museum"
        activity = receive(ws, "phone_activity")
        assert activity["message_id"] == action_id
        assert channel.client.post(url, headers=headers(channel.token), json={
            "status": "failed", "reason": "maps_missing"
        }).status_code == 204
    path = f"/api/chat/conversations/{channel.conversation}"
    phone_messages = channel.client.get(path, headers=headers(channel.token)).json()["messages"]
    assert [(entry["id"], entry["kind"], entry["content"]) for entry in phone_messages] == [
        (action_id, "phone_activity", "Opened Maps → Museum")
    ]
    assert channel.client.get(path, headers=headers(channel.web)).json()["messages"] == []
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        assert receive(ws, "history")["messages"][0]["kind"] == "phone_activity"


def test_push_timeout_and_late_result_reach_phone_mind_once(channel, monkeypatch):
    monkeypatch.setitem(phone_actions.WAIT_SECONDS, "navigate_to", 0.15)
    sent = []

    def push(token, data, *, client, ttl_seconds):
        sent.append((token, data, ttl_seconds))
        return fcm.Result("sent")

    monkeypatch.setattr(fcm, "send", push)
    outcome = phone_actions.act(channel.user, channel.conversation, "navigate_to", {"name": "Museum"})
    assert outcome.status == "unconfirmed"
    assert len(sent) == 1
    token, frame, ttl = sent[0]
    assert token == "newer-device"
    assert json.loads(frame["args"]) == {"name": "Museum"}
    assert 0 < ttl <= 60
    url = result_url(outcome.action_id)
    for _ in range(2):
        assert channel.client.post(url, headers=headers(channel.other_phone), json={
            "status": "notified", "reason": "locked"
        }).status_code == 204
    source = mind_phone.PhoneSource()
    with channel.db() as db:
        assert len(phone_actions.history(db, channel.conversation)) == 2
        [(scope, payload)] = source.poll(db)
        moment = source.moment(db, scope, payload, None, time.time(), thoughts=[], thinking=None)
        assert moment.fresh == ["Couldn't open Maps (phone locked), sent a notification"]
        source.after_tick(scope, moment, 1, {})
        assert phone_actions.pending_news(db) == {}


def test_position_reply_is_encrypted_and_only_activity_is_persisted(channel, monkeypatch):
    point = (21.127331, 34.872212)
    room = channel.conversation
    monkeypatch.setattr(google_maps, "address_at", lambda coords: "Main Street" if coords == point else None)
    _, _, _, where = tools.build_phone_tools(channel.user, room)
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        wait_for_phone(channel.user)
        with ThreadPoolExecutor() as executor:
            result = executor.submit(asyncio.run, where.ainvoke({}))
            frame = receive(ws, "phone_action")
            action_id = uuid.UUID(frame["action_id"])
            assert frame["action"] == "get_position" and frame["args"] == {}
            with channel.bus.pubsub() as listener:
                listener.subscribe(phone_actions._reply_channel(action_id))
                assert listener.get_message(timeout=2)["type"] == "subscribe"
                url = result_url(action_id)
                auth_headers = headers(channel.token)
                rejected = channel.client.post(url, headers=auth_headers, json={
                    "status": "done", "data": {"lat": 91.12345, "lng": 34.872212}
                })
                assert rejected.status_code == 422 and "91.12345" not in rejected.text
                assert channel.client.post(url, headers=auth_headers, json={"status": "done", "data": {
                    "lat": point[0], "lng": point[1], "accuracy_m": 4, "fix_at": "2026-11-22T02:00:00Z",
                }}).status_code == 204
                relayed = listener.get_message(timeout=2)["data"].decode()
                assert "point" in relayed and all(str(coord) not in relayed for coord in point)
            assert result.result(timeout=5) == "Main Street"
        assert receive(ws, "phone_activity")["text"] == "Used your location once"
    with channel.db() as db:
        [activity] = phone_actions.history(db, room)
        assert activity.content == "Used your location once"
    prefix = phone_actions._pending_key(action_id)
    for key in channel.bus.scan_iter(prefix + "*"):
        metadata = channel.bus.get(key).decode()
        assert all(str(coord) not in metadata for coord in point)


def test_settings_search_uses_basic_fields(channel, monkeypatch):
    def post(url, *, json, headers, timeout):
        assert url == google_maps.PLACES_URL
        assert json["textQuery"] == "Museum"
        assert "OpeningHours" not in headers["X-Goog-FieldMask"]
        return httpx.Response(200, json={"places": [{"displayName": {"text": "Museum"},
                                                     "formattedAddress": "10 High Street",
                                                     "location": {"latitude": 49.15, "longitude": -123.75}}]})

    monkeypatch.setattr(google_maps.httpx, "post", post)
    response = channel.client.get("/api/phone/places/search", params={"q": "Museum"}, headers=headers(channel.token))
    assert response.json() == [{"name": "Museum", "address": "10 High Street", "lat": 49.15, "lng": -123.75}]


def test_saved_destination_is_resolved_on_the_phone_that_opens_maps(channel):
    navigate, *_ = tools.build_phone_tools(channel.user, channel.conversation)
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        wait_for_phone(channel.user)
        with ThreadPoolExecutor() as executor:
            result = executor.submit(asyncio.run, navigate.ainvoke({
                "action": "navigate_to", "destination": "Work", "saved_place": True,
            }))
            resolve = receive(ws, "phone_action")
            assert resolve["action"] == "resolve_saved_place" and resolve["args"] == {"name": "Work"}
            ws.send_json({"type": "phone_action_result", "action_id": resolve["action_id"], "status": "done",
                          "data": {"lat": 49.15, "lng": -123.75}})
            frame = receive(ws, "phone_action")
            assert frame["action"] == "navigate_to" and frame["args"]["lat"] == 49.15
            ws.send_json({"type": "phone_action_result", "action_id": frame["action_id"], "status": "done"})
            assert result.result(timeout=5) == "Opened Maps → Work"


def test_maps_tools_are_registered_only_on_direct_replies(monkeypatch):
    settings = get_settings().model_copy(update={"redis_url": "redis://unused"})
    monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
    monkeypatch.setattr(ai_chat, "build_agent", lambda tools, **kwargs: {tool.name for tool in tools})
    monkeypatch.setattr(ai_chat, "get_agent", lambda *args: set())
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4())

    async def registered(context):
        async with ai_chat._orchestrator_for(context) as (names, _):
            return names

    assert asyncio.run(registered(reply)) == {"phone_action", "get_route", "search_places", "where_am_i"}
    assert asyncio.run(registered(replace(reply, wake="late result"))) == set()
    assert asyncio.run(registered(replace(reply, nudge="say hello"))) == set()


def test_route_and_nearby_return_only_human_readable_location_data(monkeypatch):
    settings = get_settings().model_copy(update={"google_maps_api_key": "test-key"})
    monkeypatch.setattr(tools, "get_settings", lambda: settings)
    monkeypatch.setattr(google_maps, "get_settings", lambda: settings)
    position = (21.127331, 34.872212)
    calls = []

    def act(user, conversation, action, args, **kwargs):
        assert action == "get_position"
        return phone_actions.Outcome("done", uuid.uuid4(), "Phone returned a location", position)

    def post(url, *, json, headers, timeout):
        calls.append(json)
        if url == google_maps.ROUTES_URL:
            assert json["origin"]["location"]["latLng"] == {"latitude": position[0], "longitude": position[1]}
            assert json["routingPreference"] == "TRAFFIC_AWARE"
            if json["travelMode"] == "TWO_WHEELER":
                return httpx.Response(400)
            return httpx.Response(200, json={"routes": [{"distanceMeters": 8200, "duration": "1500s"}]})
        assert json["locationBias"]["circle"]["center"] == {"latitude": position[0], "longitude": position[1]}
        assert "currentOpeningHours.openNow" in headers["X-Goog-FieldMask"]
        return httpx.Response(200, json={"places": [{
            "id": "pharmacy-id", "displayName": {"text": "Pharmacy"}, "formattedAddress": "High Street",
            "location": {"latitude": 21.13, "longitude": 34.87}, "currentOpeningHours": {"openNow": False},
            "regularOpeningHours": {"weekdayDescriptions": ["Monday: 9 AM–5 PM"]},
        }]})

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_actions, "record_position_use", lambda *_: None)
    monkeypatch.setattr(google_maps.httpx, "post", post)
    _, route, nearby, _ = tools.build_phone_tools(uuid.uuid4(), uuid.uuid4())
    estimate = asyncio.run(route.ainvoke({"destination": "Office", "mode": "two_wheeler"}))
    assert estimate == "Two-wheeler routing is unavailable here. Using driving instead. About 25 min by car, 8.2 km with live traffic."
    places = json.loads(asyncio.run(nearby.ainvoke({"query": "pharmacy", "nearby": True})))
    assert places == [{"name": "Pharmacy", "address": "High Street", "open_now": False,
                       "opening_hours": ["Monday: 9 AM–5 PM"], "place_id": "pharmacy-id",
                       "approx_distance_m": google_maps.distance_m(position, (21.13, 34.87))}]
    assert [request["travelMode"] for request in calls[:2]] == ["TWO_WHEELER", "DRIVE"]
