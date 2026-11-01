"""Desktop preview and the capture gate (plan §10, §23, stage B gate:
"authentication mode suppresses all capture", "unauthorized preview rejected").

Frame format and image checks need nothing; the rest needs real PostgreSQL
(``TEST_DATABASE_URL``). Redis is replaced by a small in-memory stand-in.
"""
from __future__ import annotations

import asyncio
import io
import threading
import uuid
import time

import pytest
from PIL import Image

from app.db.models import DesktopEnvironment
from app.services import desktop_link as link_svc
from app.services import desktop_preview
from app.services import desktop_sessions as svc
from desktop_controller import connection
from desktop_controller.capture import FakeCapture, encode
from desktop_controller.link import MAX_FRAME_BYTES, FrameHeader, pack_frame, unpack_frame
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_link import _env, _hello, _paired, _wait_for, relay  # noqa: F401  (fixtures/helpers)
from tests.test_desktop_sessions import _user, alice, bob, client, db  # noqa: F401  (fixtures)


@pytest.fixture
def fake_redis(isolated_redis):
    return isolated_redis


def _jpeg(width=64, height=36) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 30)).save(out, format="JPEG")
    return out.getvalue()


def _header(epoch=1, width=64, height=36, seq=1) -> FrameHeader:
    return FrameHeader(seq=seq, captured_ms=1, width=width, height=height, gate_epoch=epoch)


# --------------------------------------------------------------------------- #
# Frames as untrusted input (no database)
# --------------------------------------------------------------------------- #


def test_frames_round_trip_and_malformed_ones_are_rejected():
    header, image = unpack_frame(pack_frame(_header(), b"jpegbytes"))
    assert header.seq == 1 and image == b"jpegbytes"
    for bad in (b"", b"\x00\x00\x00\x05{}", b"\x00\x10\x00\x00" + b"x" * 20, b"x" * (MAX_FRAME_BYTES + 1)):
        with pytest.raises(ValueError):
            unpack_frame(bad)
    with pytest.raises(ValueError):  # header fields out of bounds
        unpack_frame(pack_frame(_header(width=1), b"x").replace(b'"width":1', b'"width":99999'))


def test_only_a_real_jpeg_of_the_declared_size_is_accepted():
    assert desktop_preview.check_jpeg(_header(), _jpeg())
    assert not desktop_preview.check_jpeg(_header(width=640, height=360), _jpeg())  # size lies
    assert not desktop_preview.check_jpeg(_header(), b"\x89PNG not a jpeg")
    png = io.BytesIO()
    Image.new("RGB", (64, 36)).save(png, format="PNG")
    assert not desktop_preview.check_jpeg(_header(), png.getvalue())


def test_capture_downscales_to_the_requested_size():
    big = Image.new("RGB", (1920, 1080))
    grab = encode(big, 640, 360)
    assert (grab.width, grab.height) == (640, 360)
    assert Image.open(io.BytesIO(grab.jpeg)).format == "JPEG"
    assert encode(Image.new("RGB", (320, 180)), 640, 360).width == 320  # never upscaled


# --------------------------------------------------------------------------- #
# The gate (real Postgres)
# --------------------------------------------------------------------------- #


def _connected(db, user):
    env, token = _paired(db, user)
    _, generation = link_svc.authenticate(db, _hello(env, token))
    return env, generation


def test_capture_is_off_until_the_owner_allows_it(db, alice):
    env, generation = _connected(db, alice)
    assert env.capture_gate == "blocked"
    config = link_svc.heartbeat(db, env.id, generation)
    assert (config.gate, config.target_process) == ("blocked", None)


def test_whole_screen_is_the_default_and_game_window_names_only_the_game(db, alice):
    env, generation = _connected(db, alice)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
    config = link_svc.heartbeat(db, env.id, generation)
    assert (config.gate, config.scope, config.target_process) == ("allowed", "desktop", None)
    assert (config.max_width, config.max_height) == (1280, 720)

    link_svc.set_capture_gate(db, alice.id, env.id, scope="game_window")
    config = link_svc.heartbeat(db, env.id, generation)
    assert (config.scope, config.target_process) == ("game_window", "javaw.exe")
    assert (config.max_width, config.max_height) == (640, 360)  # the profile's observation size


def test_switching_scope_fences_old_frames_and_wipes_the_picture(db, alice, fake_redis):
    env, generation = _connected(db, alice)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
    epoch = link_svc.heartbeat(db, env.id, generation).epoch
    desktop_preview.store(env.id, _header(epoch=epoch), _jpeg(), generation=generation)
    link_svc.set_capture_gate(db, alice.id, env.id, scope="game_window")
    assert desktop_preview.load(env.id) is None
    assert not link_svc.frame_allowed(db, env.id, generation, epoch)
    unchanged = link_svc.set_capture_gate(db, alice.id, env.id, scope="game_window").capture_epoch
    assert unchanged == epoch + 1  # setting the same value again changes nothing


def test_signing_in_stops_capture_and_is_confirmed_by_the_vm(db, alice, fake_redis):
    env, generation = _connected(db, alice)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
    allowed = link_svc.heartbeat(db, env.id, generation)
    desktop_preview.store(env.id, _header(epoch=allowed.epoch), _jpeg(), generation=generation)

    link_svc.set_capture_gate(db, alice.id, env.id, "manual_auth")
    assert desktop_preview.load(env.id) is None  # the last picture is wiped at once
    db.refresh(env)
    assert env.capture_confirmed is False  # requested, not yet acknowledged

    config = link_svc.heartbeat(db, env.id, generation, gate_epoch_seen=allowed.epoch)
    assert config.gate == "manual_auth" and config.target_process is None
    link_svc.heartbeat(db, env.id, generation, gate_epoch_seen=config.epoch)
    db.refresh(env)
    assert env.capture_confirmed is True


def test_frames_from_an_old_setting_or_connection_are_refused(db, alice):
    env, generation = _connected(db, alice)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
    epoch = link_svc.heartbeat(db, env.id, generation).epoch
    assert link_svc.frame_allowed(db, env.id, generation, epoch)
    assert not link_svc.frame_allowed(db, env.id, generation, epoch - 1)
    assert not link_svc.frame_allowed(db, env.id, generation - 1, epoch)
    link_svc.set_capture_gate(db, alice.id, env.id, "manual_auth")
    assert not link_svc.frame_allowed(db, env.id, generation, epoch)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")  # back on: a new epoch, old frames still refused
    assert not link_svc.frame_allowed(db, env.id, generation, epoch)


def test_only_the_owner_controls_capture(db, alice, bob):
    env, _ = _connected(db, alice)
    with pytest.raises(svc.DesktopError) as excinfo:
        link_svc.set_capture_gate(db, bob.id, env.id, "allowed")
    assert excinfo.value.status == 404


def test_revoking_turns_capture_off(db, alice, fake_redis):
    env, generation = _connected(db, alice)
    link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
    desktop_preview.store(env.id, _header(epoch=env.capture_epoch), _jpeg(), generation=generation)
    svc.revoke_environment(db, alice.id, env.id)
    db.refresh(env)
    assert env.capture_gate == "blocked" and desktop_preview.load(env.id) is None


# --------------------------------------------------------------------------- #
# Preview route
# --------------------------------------------------------------------------- #


def test_preview_route_serves_only_a_current_frame_to_the_owner(client, database, alice, bob):
    client.as_user(alice)
    env_id = client.post("/api/desktop/environments", json={
        "display_name": "VM", "allowed_profile_ids": ["minecraft_java"],
    }).json()["id"]
    url = f"/api/desktop/environments/{env_id}"
    assert client.get(f"{url}/preview").json()["detail"]["code"] == "no_frame"

    with database() as db:
        env = db.get(DesktopEnvironment, uuid.UUID(env_id))
        env.status, env.controller_generation = "online", 7
        db.commit()
    allowed = client.put(f"{url}/capture", json={"gate": "allowed"}).json()
    assert allowed["capture_gate"] == "allowed" and allowed["capture_confirmed"] is False
    with database() as db:
        epoch = db.get(DesktopEnvironment, uuid.UUID(env_id)).capture_epoch
    desktop_preview.store(uuid.UUID(env_id), _header(epoch=epoch), _jpeg(), generation=7)

    response = client.get(f"{url}/preview")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"

    client.as_user(bob)
    assert client.get(f"{url}/preview").status_code == 404
    assert client.put(f"{url}/capture", json={"gate": "blocked"}).status_code == 404

    client.as_user(alice)
    client.put(f"{url}/capture", json={"gate": "manual_auth"})
    assert client.get(f"{url}/preview").status_code == 404


# --------------------------------------------------------------------------- #
# End to end: real relay over TLS, real client loop, fake screen
# --------------------------------------------------------------------------- #


def test_preview_flows_only_while_allowed(relay, database, alice, tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "HEARTBEAT_SECONDS", 0.2)
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))
    screen = FakeCapture()
    stop = threading.Event()

    def run_client():
        async def main():
            task = asyncio.create_task(connection.run_once(config, capture=screen))
            while not stop.is_set():
                await asyncio.sleep(0.05)
            task.cancel()
        try:
            asyncio.run(main())
        except BaseException:
            pass

    thread = threading.Thread(target=run_client, daemon=True)
    thread.start()
    try:
        def env_row():
            with database() as db:
                return db.get(DesktopEnvironment, env.id)

        assert _wait_for(lambda: env_row().status == "online")
        time.sleep(0.6)
        assert screen.grabs == 0 and desktop_preview.load(env.id) is None  # off by default
        assert env_row().target_state == "capture_off"

        with database() as db:
            link_svc.set_capture_gate(db, alice.id, env.id, "allowed")
        assert _wait_for(lambda: desktop_preview.load(env.id) is not None, timeout=5)
        assert _wait_for(lambda: env_row().target_state == "ok", timeout=5)
        frame = desktop_preview.load(env.id)
        assert (frame.width, frame.height) == (1280, 720) and frame.epoch == env_row().capture_epoch

        with database() as db:
            link_svc.set_capture_gate(db, alice.id, env.id, "manual_auth")
        assert _wait_for(lambda: env_row().capture_confirmed, timeout=5)
        grabs = screen.grabs
        time.sleep(1.0)
        assert screen.grabs == grabs  # the VM stopped grabbing, not just the Mac discarding
        assert desktop_preview.load(env.id) is None
    finally:
        stop.set()
        thread.join(timeout=5)


@pytest.mark.parametrize("scope, reason", [
    ("game_window", "not_foreground"),  # e.g. the launcher's sign-in window covers the game
    ("desktop", "sensitive"),           # a Windows sign-in prompt is in front
])
def test_withheld_pictures_send_only_a_reason(relay, database, alice, tmp_path, monkeypatch, scope, reason):
    monkeypatch.setattr(connection, "HEARTBEAT_SECONDS", 0.2)
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
        link_svc.set_capture_gate(db, alice.id, env.id, "allowed", scope=scope)
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))
    screen = FakeCapture(state=reason)
    stop = threading.Event()

    def run_client():
        async def main():
            task = asyncio.create_task(connection.run_once(config, capture=screen))
            while not stop.is_set():
                await asyncio.sleep(0.05)
            task.cancel()
        try:
            asyncio.run(main())
        except BaseException:
            pass

    thread = threading.Thread(target=run_client, daemon=True)
    thread.start()
    try:
        def state():
            with database() as db:
                return db.get(DesktopEnvironment, env.id).target_state

        assert _wait_for(lambda: state() == reason, timeout=5)
        assert desktop_preview.load(env.id) is None
    finally:
        stop.set()
        thread.join(timeout=5)
