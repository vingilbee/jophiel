"""Security monitoring end to end: fake Windows event logs -> the real controller loop -> the real relay over TLS
-> observations and coverage -> the read tool's answer. Needs ``TEST_DATABASE_URL`` (a throwaway database)."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import DesktopEnvironment, Observation, SecurityCoverage
from app.services import desktop_link as link_svc
from app.services import security_activity as S
from desktop_controller import connection
from desktop_controller.fake import FakeEventLog
from desktop_controller.security import SecurityCollector
from tests.db_support import database  # noqa: F401  (fixture)
from tests.test_desktop_link import _env, _wait_for, relay  # noqa: F401  (fixtures/helpers)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)


class Controller:
    """The controller loop in a thread, reading a fake event log."""

    def __init__(self, config, collector):
        self.collector = collector
        self.outcome: dict = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()

    def _run(self, config):
        async def main():
            task = asyncio.create_task(connection.run_once(config, security=self.collector))
            while not self._stop.is_set() and not task.done():
                await asyncio.sleep(0.05)
            if task.done() and task.exception() is not None:
                self.outcome["error"] = str(task.exception())
            task.cancel()
        try:
            asyncio.run(main())
        except BaseException:
            pass

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)


@pytest.fixture
def watched(relay, database, alice, tmp_path, monkeypatch):
    settings = S.get_settings().model_copy(update={
        "security_channels": ["Security", "System"], "security_poll_seconds": 2.0, "desktop_agent_enabled": True})
    monkeypatch.setattr(S, "get_settings", lambda: settings)
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))
    log = FakeEventLog(["Security"])  # no System channel on this machine
    log.write("Security", 4624, message="before monitoring was on")
    collector = SecurityCollector(log, tmp_path / "security_state.json", environment_id=config.environment_id)
    controller = Controller(config, collector)
    try:
        with database() as db:
            assert _wait_for(lambda: (db.expire_all() or True) and db.get(DesktopEnvironment, env.id).status == "online")
        yield {"env": env, "log": log, "controller": controller, "config": config}
    finally:
        controller.stop()


def _events(database, env):
    with database() as db:
        return [o.payload for o in db.query(Observation).filter_by(
            source=S.SOURCE, kind=S.EVENT, source_instance=str(env.id)).order_by(Observation.occurred_at)]


def test_records_arrive_while_monitoring_is_on_and_never_from_before(watched, database, alice):
    env, log = watched["env"], watched["log"]
    with database() as db:
        S.set_monitoring(db, alice.id, env.id, True)
    # The first read sets the bookmark at the log's end; what's written after it arrives.
    assert _wait_for(lambda: watched["controller"].collector.channels.get("Security") is not None, timeout=10)
    log.write("Security", 4625, message="An account failed to log on.", audit="failure",
              data={"TargetUserName": "admin"})
    log.write("Security", 4720, message="A user account was created.")
    assert _wait_for(lambda: len(_events(database, env)) == 2, timeout=12), _events(database, env)
    assert [e["event_id"] for e in _events(database, env)] == [4625, 4720]  # not the 4624 from before
    with database() as db:
        rows = db.query(SecurityCoverage).filter_by(environment_id=env.id).all()
        statuses = {(r.channel, r.status) for r in rows}
        assert ("Security", "covered") in statuses and ("System", "unavailable") in statuses
        now = datetime.now(timezone.utc)
        answer = S.render(S.activity(db, alice.id, start=now - timedelta(minutes=5), end=now, now=now), now=now)
    assert "Security 4625" in answer and "System: not seen" in answer and "isn't on this machine" in answer


def test_monitoring_off_closes_intake(watched, database, alice):
    env, log = watched["env"], watched["log"]
    with database() as db:
        S.set_monitoring(db, alice.id, env.id, True)
    assert _wait_for(lambda: watched["controller"].collector.channels.get("Security") is not None, timeout=10)
    with database() as db:
        S.set_monitoring(db, alice.id, env.id, False)
    assert _wait_for(lambda: not watched["controller"].collector.config.enabled, timeout=5)
    log.write("Security", 4698, message="while monitoring was off")
    with database() as db:
        S.set_monitoring(db, alice.id, env.id, True)
    assert _wait_for(lambda: watched["controller"].collector.epoch == 2, timeout=5)
    log.write("Security", 4699, message="after it came back")
    assert _wait_for(lambda: [e["event_id"] for e in _events(database, env)] == [4699], timeout=12), \
        _events(database, env)


def test_a_revoked_controller_supplies_nothing(watched, database, alice):
    env, log = watched["env"], watched["log"]
    from app.services import desktop_sessions

    with database() as db:
        S.set_monitoring(db, alice.id, env.id, True)
    assert _wait_for(lambda: watched["controller"].collector.channels.get("Security") is not None, timeout=10)
    with database() as db:
        desktop_sessions.revoke_environment(db, alice.id, env.id)
    log.write("Security", 4625, message="after revoke")
    # The relay drops a revoked controller at its next heartbeat; nothing it read afterwards is stored.
    assert _wait_for(lambda: not watched["controller"]._thread.is_alive(), timeout=10)
    assert "revoked" in watched["controller"].outcome.get("error", "")
    assert _events(database, env) == []
