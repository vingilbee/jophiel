"""Jophiel starting sessions herself on a paired machine (docs/VM_CONNECTION_PLAN.md, step 3).

The owner's per-machine switch is off for every pairing until turned on; her start also needs the machine
online; the owner's own starts need neither; each session records who started it. Real PostgreSQL
(``TEST_DATABASE_URL``).
"""
from __future__ import annotations

import pytest

from app.db.models import DesktopSession
from app.schemas.desktop import SessionCreate
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _env, _user, alice, bob, client, db  # noqa: F401  (fixtures)


def _her_start(db, user, env):
    session, _ = svc.start_session(db, user.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java"),
                                   started_by="jophiel")
    return session


def _online(db, env):
    svc.controller_online(db, env.id, controller_generation=1)
    db.refresh(env)


def test_the_switch_is_off_for_a_new_pairing_and_her_start_is_refused(db, alice):
    env = _env(db, alice)
    _online(db, env)
    assert env.auto_start_allowed is False
    with pytest.raises(DesktopError) as refused:
        _her_start(db, alice, env)
    assert refused.value.code == "auto_start_off" and "Allow Jophiel to start sessions" in refused.value.message
    assert db.query(DesktopSession).filter_by(environment_id=env.id).count() == 0


def test_with_the_switch_on_she_starts_only_on_an_online_machine(db, alice):
    env = _env(db, alice)
    svc.set_auto_start(db, alice.id, env.id, True)
    with pytest.raises(DesktopError) as offline:
        _her_start(db, alice, env)
    assert offline.value.code == "offline"
    _online(db, env)
    session = _her_start(db, alice, env)
    assert session.started_by == "jophiel" and session.state == "starting"
    # A second start while that one is live is refused by the one-live-session rule, as before.
    with pytest.raises(DesktopError) as twice:
        _her_start(db, alice, env)
    assert twice.value.code == "already_owned"


def test_the_owners_own_start_needs_neither_and_is_recorded_as_theirs(db, alice):
    env = _env(db, alice)  # offline, switch off
    session, _ = svc.start_session(db, alice.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java"))
    assert session.started_by == "user"


def test_turning_the_switch_off_again_stops_future_starts(db, alice):
    env = _env(db, alice)
    _online(db, env)
    svc.set_auto_start(db, alice.id, env.id, True)
    changed = svc.set_auto_start(db, alice.id, env.id, False)
    assert changed.auto_start_allowed is False and changed.auto_start_changed_at is not None
    with pytest.raises(DesktopError):
        _her_start(db, alice, env)


def test_only_the_owner_can_switch_it(db, alice, bob):
    env = _env(db, alice)
    with pytest.raises(DesktopError) as foreign:
        svc.set_auto_start(db, bob.id, env.id, True)
    assert foreign.value.status == 404


def test_a_revoked_machine_cant_be_switched_on(db, alice):
    env = _env(db, alice)
    svc.revoke_environment(db, alice.id, env.id)
    with pytest.raises(DesktopError) as revoked:
        svc.set_auto_start(db, alice.id, env.id, True)
    assert revoked.value.code == "not_authorized"


def test_her_minecraft_pack_isnt_a_paired_machine_and_needs_no_switch(db, alice):
    env = _env(db, alice)
    env.hosting_kind = "game_pack"
    db.commit()
    assert svc.jophiel_may_start(env) is None


def test_the_switch_and_who_started_through_the_api(client, database, alice, bob):
    client.as_user(alice)
    env = client.post("/api/desktop/environments", json={
        "display_name": "Legion VM", "allowed_profile_ids": ["minecraft_java"]}).json()
    assert env["auto_start_allowed"] is False and env["controller_status"] is None
    on = client.put(f"/api/desktop/environments/{env['id']}/auto-start", json={"allowed": True})
    assert on.status_code == 200 and on.json()["auto_start_allowed"] is True
    started = client.post("/api/desktop/sessions", json={"environment_id": env["id"], "profile_id": "minecraft_java"})
    assert started.json()["started_by"] == "user"
    client.as_user(bob)
    assert client.put(f"/api/desktop/environments/{env['id']}/auto-start", json={"allowed": False}).status_code == 404
    assert client.get("/api/desktop/relay").status_code == 404  # the relay is the owner's (OWNER_EMAILS) only


def test_the_pairing_command_names_the_relay_by_fingerprint_not_address(client, alice, tmp_path, monkeypatch):
    from app.ai.desktop import tls
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "desktop_relay_dir", str(tmp_path / "relay"))
    monkeypatch.setattr(get_settings(), "desktop_controller_bind_host", None)
    monkeypatch.setattr(get_settings(), "desktop_controller_public_host", None)
    client.as_user(alice)
    env = client.post("/api/desktop/environments", json={
        "display_name": "Legion VM", "allowed_profile_ids": ["minecraft_java"]}).json()
    pairing = client.post(f"/api/desktop/environments/{env['id']}/pairings").json()
    fingerprint = tls.ensure_relay_certificate(tmp_path / "relay").fingerprint
    assert pairing["pair_args"] == f"pair {pairing['code']} --fingerprint {fingerprint}"
    assert pairing["relay_address"] is None  # the relay isn't running in this test
