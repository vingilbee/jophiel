"""General desktop control end to end: tool layer -> Redis -> real relay over TLS -> real controller loop ->
fake Windows desktop -> back. Needs ``TEST_DATABASE_URL`` and ``TEST_REDIS_URL`` (throwaway ones)."""
from __future__ import annotations

import asyncio
import io
import threading
import time

import pytest
from PIL import Image

from app.services import desktop_control
from app.services import desktop_link as link_svc
from desktop_controller import connection
from desktop_controller.capture import FakeCapture
from desktop_controller.desktop import DesktopHands
from desktop_controller.fake import FakeDesktopInput
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import real_redis  # noqa: F401  (fixture)
from tests.test_desktop_link import _env, _wait_for, relay  # noqa: F401  (fixtures/helpers)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)


class Desktop:
    """The controller loop in a thread with fake desktop hands it can inspect."""

    def __init__(self, config):
        self.input = FakeDesktopInput()
        self.hands = DesktopHands(self.input, FakeCapture(), sleep=lambda _s: None)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()

    def _run(self, config):
        async def main():
            task = asyncio.create_task(connection.run_once(config, capture=FakeCapture(), desktop=self.hands))
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
def desktop(relay, real_redis, database, alice, tmp_path, monkeypatch):
    settings = desktop_control.get_settings().model_copy(update={"desktop_agent_enabled": True})
    monkeypatch.setattr(desktop_control, "get_settings", lambda: settings)
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))
    d = Desktop(config)
    d.env = env
    try:
        with database() as db:
            assert _wait_for(lambda: db.get(type(env), env.id).status == "online" or (db.expire_all() or False))
        yield d
    finally:
        d.stop()


def _allow(database, alice, env, gate="allowed"):
    with database() as db:
        link_svc.set_capture_gate(db, alice.id, env.id, gate)
    time.sleep(2.5)  # the next heartbeat carries it to the relay and the controller


def _act(database, alice, action):
    with database() as db:
        return desktop_control.act(db, alice.id, action)


def test_with_the_view_off_nothing_is_pressed(desktop, database, alice):
    outcome = _act(database, alice, {"kind": "click", "point": [10, 10]})
    assert outcome.status == "refused" and "switched off" in outcome.reason
    assert desktop.input.events == []


def test_a_screenshot_and_a_click_go_there_and_back(desktop, database, alice):
    _allow(database, alice, desktop.env)
    shot = _act(database, alice, {"kind": "screenshot"})
    assert shot.status == "done", shot.reason
    assert Image.open(io.BytesIO(shot.jpeg)).size == tuple(shot.screen["size"])
    assert shot.screen["region"] == [0, 0, 1920, 1080]
    assert shot.foreground_process == "explorer.exe"

    click = _act(database, alice, {"kind": "click", "point": [960, 540], "count": 2})
    assert click.status == "done", click.reason
    assert desktop.input.events[:3] == [("move", 960, 540), ("button_down", "left"), ("button_up", "left")]

    typed = _act(database, alice, {"kind": "keyboard", "keys": ["ctrl+s"]})
    assert typed.status == "done" and ("key_down", "ctrl") in desktop.input.events
    assert desktop.input.held == set()


def test_signing_in_stops_her_hands(desktop, database, alice):
    _allow(database, alice, desktop.env)
    _allow(database, alice, desktop.env, "manual_auth")
    outcome = _act(database, alice, {"kind": "keyboard", "text": "hello"})
    assert outcome.status == "refused"
    assert desktop.input.events == []
