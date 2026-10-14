"""Stage C end to end: session start, lease, decisions, stops, the window guard.

Real relay over TLS, real controller loop, fake keyboard/mouse (``FakeInput``,
which keeps the game-window guard). Needs ``TEST_DATABASE_URL``; the tests that
send decisions also need ``TEST_REDIS_URL`` (a throwaway Redis), because
commands reach the relay through a Redis stream.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.db.models import DesktopSession
from app.schemas.desktop import SessionCreate
from app.services import desktop_commands
from app.services import desktop_link as link_svc
from app.services import desktop_sessions as svc
from desktop_controller import connection
from desktop_controller.fake import FakeInput
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis, real_redis  # noqa: F401  (fixtures)
from tests.test_desktop_link import _env, _wait_for, relay  # noqa: F401  (fixtures/helpers)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)

WALK = {"intent": "walk forward", "hold": ["forward"], "valid_for_ms": 3000}


class Guest:
    """The controller loop in a thread, with a fake keyboard it can inspect."""

    def __init__(self, config):
        self.input = FakeInput()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()

    def _run(self, config):
        async def main():
            task = asyncio.create_task(connection.run_once(config, input_factory=lambda profile: self.input))
            while not self._stop.is_set() and not task.done():
                await asyncio.sleep(0.05)
            task.cancel()
        try:
            asyncio.run(main())
        except BaseException:
            pass

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)


@pytest.fixture
def guest(relay, database, alice, tmp_path):
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))
    g = Guest(config)
    g.env = env
    try:
        with database() as db:
            assert _wait_for(lambda: db.get(type(env), env.id).status == "online" or (db.expire_all() or False))
        yield g
    finally:
        g.stop()


def _session(database, session_id) -> DesktopSession:
    with database() as db:
        return db.get(DesktopSession, session_id)


def _start_playing(database, alice, env) -> DesktopSession:
    with database() as db:
        session, _ = svc.start_session(db, alice.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java"))
    assert _wait_for(lambda: _session(database, session.id).state == "running", timeout=8), "controller never got ready"
    return _session(database, session.id)


def test_starting_a_session_waits_for_the_game_then_plays(guest, database, alice):
    guest.input.lost = "not_found"  # Minecraft isn't running yet
    with database() as db:
        session, _ = svc.start_session(db, alice.id, SessionCreate(environment_id=guest.env.id, profile_id="minecraft_java"))
    time.sleep(1.5)
    assert _session(database, session.id).state == "starting"
    guest.input.lost = None  # the game is up
    assert _wait_for(lambda: _session(database, session.id).state == "running", timeout=8)


def test_a_decision_presses_keys_and_pause_releases_them(real_redis, guest, database, alice):
    session = _start_playing(database, alice, guest.env)
    with database() as db:
        decision_id = svc.send_decision(db, alice.id, session.id, WALK)
    assert _wait_for(lambda: "W" in guest.input.down, timeout=5)

    started = time.monotonic()
    with database() as db:
        svc.pause_session(db, alice.id, session.id)
    assert _wait_for(lambda: not guest.input.down, timeout=3)
    assert time.monotonic() - started < 1.0  # the cancel arrives well before the lease would lapse

    assert _wait_for(lambda: any(
        r.get("decision_id") == decision_id and r.get("state") == "cancelled"
        for r in desktop_commands.recent_reports(session.id)
    ), timeout=5)


def test_a_decision_from_before_a_pause_cannot_run_after_resume(real_redis, guest, database, alice):
    session = _start_playing(database, alice, guest.env)
    old_generation = session.execution_generation
    with database() as db:
        svc.pause_session(db, alice.id, session.id)
        resumed = svc.resume_session(
            db, alice.id, session.id, expected_state_revision=_session(database, session.id).state_revision,
        ).session
    assert resumed.execution_generation > old_generation
    # A late copy of an old decision (old generation) arrives after the resume.
    real_redis.xadd(desktop_commands.stream_key(guest.env.id), {
        "kind": "decision", "session_id": str(session.id), "decision_id": "late",
        "generation": str(old_generation), "decision": '{"hold": ["forward"], "valid_for_ms": 3000}',
    })
    assert _wait_for(lambda: any(
        r.get("decision_id") == "late" and r.get("rejection_code") in ("stale_generation", "no_lease")
        for r in desktop_commands.recent_reports(session.id)
    ), timeout=5)
    assert "W" not in guest.input.down


def test_clicking_away_from_the_game_pauses_the_session(real_redis, guest, database, alice):
    session = _start_playing(database, alice, guest.env)
    with database() as db:
        svc.send_decision(db, alice.id, session.id, {"hold": ["forward"], "axes": {"look_yaw": 45}, "valid_for_ms": 3000})
    assert _wait_for(lambda: "W" in guest.input.down, timeout=5)
    guest.input.lost = "not_foreground"
    assert _wait_for(lambda: _session(database, session.id).state == "paused", timeout=5)
    assert not guest.input.down
    assert _session(database, session.id).reason_code == "not_foreground"


def test_without_the_command_channel_the_lease_still_stops_input(real_redis, guest, database, alice, monkeypatch):
    session = _start_playing(database, alice, guest.env)
    with database() as db:
        svc.send_decision(db, alice.id, session.id, WALK)
    assert _wait_for(lambda: "W" in guest.input.down, timeout=5)
    # The cancel message is lost (e.g. Redis hiccup): only the lease is left.
    monkeypatch.setattr(desktop_commands, "publish_cancel", lambda *a, **k: None)
    started = time.monotonic()
    with database() as db:
        svc.pause_session(db, alice.id, session.id)
    assert _wait_for(lambda: not guest.input.down, timeout=4)
    assert time.monotonic() - started < 2.5  # next heartbeat reply has no grant (or the lease lapses)


def test_input_works_immediately_after_resume(real_redis, guest, database, alice):
    session = _start_playing(database, alice, guest.env)
    with database() as db:
        paused = svc.pause_session(db, alice.id, session.id).session
        svc.resume_session(db, alice.id, session.id, expected_state_revision=paused.state_revision)
        decision_id = svc.send_decision(db, alice.id, session.id, WALK)  # no waiting for a heartbeat
    assert _wait_for(lambda: "W" in guest.input.down, timeout=3), desktop_commands.recent_reports(session.id)
    assert decision_id


def test_the_first_move_of_a_session_keeps_its_lease(real_redis, guest, database, alice, monkeypatch):
    """Regression: with the real idle heartbeat (2 s), the controller used to
    sleep through the start of a session and let the first lease lapse."""
    from app.ai.desktop import relay as relay_module

    monkeypatch.setattr(connection, "HEARTBEAT_SECONDS", 2.0)
    monkeypatch.setattr(relay_module, "HEARTBEAT_TIMEOUT_SECONDS", 6.0)
    time.sleep(2.5)  # the controller is now in its slow idle wait
    session = _start_playing(database, alice, guest.env)
    with database() as db:
        decision_id = svc.send_decision(db, alice.id, session.id, {"hold": ["forward"], "valid_for_ms": 2000})

    def outcome():
        return next((r for r in desktop_commands.recent_reports(session.id)
                     if r.get("decision_id") == decision_id and r.get("state") != "sent"), None)

    assert _wait_for(lambda: outcome() is not None, timeout=6)
    assert (outcome()["state"], outcome()["ended_by"]) == ("expired", "valid_for_ms")


def _env_row(database, env_id):
    from app.db.models import DesktopEnvironment

    with database() as db:
        return db.get(DesktopEnvironment, env_id)


CLICK_CENTRE = {
    "intent": "click", "events": [
        {"type": "move", "point": [320, 180]}, {"type": "click", "at_ms": 40, "button": "menu_click", "point": [320, 180]},
    ], "valid_for_ms": 400,
}


def test_clicks_land_in_menus_and_are_refused_in_play(real_redis, guest, database, alice):
    session = _start_playing(database, alice, guest.env)
    assert _wait_for(lambda: _env_row(database, guest.env.id).input_context == "playing", timeout=3)
    with database() as db:
        refused = svc.send_decision(db, alice.id, session.id, CLICK_CENTRE)
    assert _wait_for(lambda: any(
        r.get("decision_id") == refused and r.get("rejection_code") == "context_mismatch"
        for r in desktop_commands.recent_reports(session.id)
    ), timeout=3)
    assert not any(e[1] == "move_to" for e in guest.input.log)  # nothing moved the camera

    guest.input.cursor_shown = True  # e.g. the inventory is open
    assert _wait_for(lambda: _env_row(database, guest.env.id).input_context == "menu", timeout=3)
    with database() as db:
        svc.send_decision(db, alice.id, session.id, CLICK_CENTRE)
    assert _wait_for(lambda: any(e[1:] == ("up", "MOUSE_LEFT") for e in guest.input.log), timeout=3)
    pointer = [e for e in guest.input.log if e[1] == "move_to"]
    assert pointer[-1][2:] == (640, 360)  # the centre of the 1280x720 game window
    presses = [e for e in guest.input.log if e[1] in ("down", "up")]
    assert [e[1:] for e in presses] == [("down", "MOUSE_LEFT"), ("up", "MOUSE_LEFT")]
    assert presses[0][0] - pointer[0][0] >= 30  # pressed after the pointer had arrived
