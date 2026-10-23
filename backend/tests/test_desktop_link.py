"""Desktop relay link: pairing, controller auth, heartbeats (plan §10, §19, stage B).

The proof tests need nothing. The rest need real PostgreSQL
(``TEST_DATABASE_URL``, see ``test_desktop_sessions.py``); the relay tests run
the real relay over real TLS on 127.0.0.1 with the real guest client.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import threading
import time
import uuid
from datetime import timedelta

import pytest
import uvicorn
import websockets
from websockets.asyncio.client import connect

from app.ai.desktop import relay as relay_module
from app.ai.desktop.tls import ensure_relay_certificate
from app.db.models import DesktopEnvironment, DesktopPairing
from app.schemas.desktop import EnvironmentCreate, SessionCreate
from app.services import desktop_link as link_svc
from app.services import desktop_sessions as svc
from app.services.desktop_link import LinkRefused
from app.services.desktop_sessions import DesktopError
from desktop_controller import connection
from desktop_controller.link import (
    Hello,
    Pair,
    client_proof,
    code_lookup,
    new_pairing_code,
    normalize_code,
    server_proof,
)
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)

FP = "a" * 64          # the real relay's certificate fingerprint
MITM_FP = "b" * 64     # what a machine in the middle would present


# --------------------------------------------------------------------------- #
# Proof math (no database)
# --------------------------------------------------------------------------- #


def test_codes_are_readable_and_typos_in_lookalikes_are_forgiven():
    code = new_pairing_code()
    assert len(code) == 14 and code.count("-") == 2
    assert normalize_code(code.lower().replace("-", " ")) == code.replace("-", "")
    assert normalize_code("0000-1111-2222") == normalize_code("oooo-iiii-2222")
    with pytest.raises(ValueError):
        normalize_code("short")


def test_a_proof_only_matches_the_certificate_it_was_made_for():
    code, nonce = new_pairing_code(), "0" * 32
    assert client_proof(code, FP, nonce) != client_proof(code, MITM_FP, nonce)
    assert client_proof(code, FP, nonce) != server_proof(code, FP, nonce)
    assert client_proof(code, FP, nonce) != client_proof(new_pairing_code(), FP, nonce)


# --------------------------------------------------------------------------- #
# Service (real Postgres)
# --------------------------------------------------------------------------- #


def _env(db, user) -> DesktopEnvironment:
    return svc.register_environment(db, user.id, EnvironmentCreate(
        display_name="Legion VM", allowed_profile_ids=["minecraft_java"],
    ))


def _pair_msg(code: str, fingerprint: str = FP, nonce: str = "1" * 32) -> Pair:
    return Pair(lookup=code_lookup(code), nonce=nonce, proof=client_proof(code, fingerprint, nonce),
                controller_version="test")


def _paired(db, user):
    env = _env(db, user)
    code = link_svc.create_pairing(db, user.id, env.id).code
    redeemed = link_svc.redeem_pairing(db, _pair_msg(code), relay_fingerprint=FP)
    return env, redeemed.token


def _hello(env, token) -> Hello:
    return Hello(environment_id=str(env.id), token=token, controller_version="test")


def test_redeeming_a_code_issues_a_token_and_proves_the_relay(db, alice):
    env = _env(db, alice)
    code = link_svc.create_pairing(db, alice.id, env.id).code
    redeemed = link_svc.redeem_pairing(db, _pair_msg(code), relay_fingerprint=FP)
    assert redeemed.environment_id == env.id
    assert redeemed.proof == server_proof(code, FP, "1" * 32)
    db.refresh(env)
    assert env.paired_at is not None and env.controller_token_hash not in (None, redeemed.token)
    pairing = db.query(DesktopPairing).one()
    assert pairing.consumed_at is not None and pairing.code is None  # the stored code is gone


def test_a_code_works_once(db, alice):
    env = _env(db, alice)
    code = link_svc.create_pairing(db, alice.id, env.id).code
    link_svc.redeem_pairing(db, _pair_msg(code), relay_fingerprint=FP)
    with pytest.raises(LinkRefused, match="pairing_invalid"):
        link_svc.redeem_pairing(db, _pair_msg(code, nonce="2" * 32), relay_fingerprint=FP)


def test_a_proof_relayed_through_another_certificate_fails(db, alice):
    env = _env(db, alice)
    code = link_svc.create_pairing(db, alice.id, env.id).code
    # The controller saw the middle machine's certificate and proved against it.
    with pytest.raises(LinkRefused, match="pairing_invalid"):
        link_svc.redeem_pairing(db, _pair_msg(code, fingerprint=MITM_FP), relay_fingerprint=FP)
    db.refresh(env)
    assert env.controller_token_hash is None


def test_wrong_proofs_use_up_the_code(db, alice):
    env = _env(db, alice)
    code = link_svc.create_pairing(db, alice.id, env.id).code
    wrong = _pair_msg(code, fingerprint=MITM_FP)
    for _ in range(5):
        with pytest.raises(LinkRefused):
            link_svc.redeem_pairing(db, wrong, relay_fingerprint=FP)
    with pytest.raises(LinkRefused):  # even the right proof is refused now
        link_svc.redeem_pairing(db, _pair_msg(code), relay_fingerprint=FP)


def test_expired_and_replaced_codes_are_refused(db, alice):
    env = _env(db, alice)
    first = link_svc.create_pairing(db, alice.id, env.id).code
    second = link_svc.create_pairing(db, alice.id, env.id)
    with pytest.raises(LinkRefused):
        link_svc.redeem_pairing(db, _pair_msg(first), relay_fingerprint=FP)
    second.pairing.expires_at = second.pairing.expires_at - timedelta(hours=1)
    db.commit()
    with pytest.raises(LinkRefused):
        link_svc.redeem_pairing(db, _pair_msg(second.code), relay_fingerprint=FP)


def test_only_the_owner_can_make_a_code(db, alice, bob):
    env = _env(db, alice)
    with pytest.raises(DesktopError) as excinfo:
        link_svc.create_pairing(db, bob.id, env.id)
    assert excinfo.value.status == 404


def test_authentication_needs_the_current_token(db, alice):
    env, token = _paired(db, alice)
    with pytest.raises(LinkRefused, match="not_authorized"):
        link_svc.authenticate(db, _hello(env, "x" * 43))
    _, generation = link_svc.authenticate(db, _hello(env, token))
    db.refresh(env)
    assert (env.status, env.controller_generation) == ("online", generation)


def test_repairing_locks_out_the_old_device(db, alice):
    env, old_token = _paired(db, alice)
    code = link_svc.create_pairing(db, alice.id, env.id).code
    link_svc.redeem_pairing(db, _pair_msg(code, nonce="3" * 32), relay_fingerprint=FP)
    with pytest.raises(LinkRefused, match="not_authorized"):
        link_svc.authenticate(db, _hello(env, old_token))


def test_a_newer_connection_supersedes_the_old_one(db, alice):
    env, token = _paired(db, alice)
    _, old = link_svc.authenticate(db, _hello(env, token))
    _, new = link_svc.authenticate(db, _hello(env, token))
    link_svc.heartbeat(db, env.id, new)
    with pytest.raises(LinkRefused, match="superseded"):
        link_svc.heartbeat(db, env.id, old)
    # The old socket closing afterwards must not knock the new one offline.
    link_svc.disconnected(db, env.id, old, reason="controller_lost")
    db.refresh(env)
    assert env.status == "online"
    link_svc.disconnected(db, env.id, new, reason="controller_lost")
    db.refresh(env)
    assert env.status == "offline"


def test_reconnecting_never_resumes_a_running_session(db, alice):
    env, token = _paired(db, alice)
    link_svc.authenticate(db, _hello(env, token))
    session, _ = svc.start_session(db, alice.id, SessionCreate(environment_id=env.id, profile_id="minecraft_java"))
    svc.controller_ready(db, session.id)
    link_svc.authenticate(db, _hello(env, token))  # e.g. the VM's network blipped
    db.refresh(session)
    assert (session.state, session.reason_code) == ("disconnected", "controller_replaced")


def test_revoking_cuts_off_heartbeats_tokens_and_codes(db, alice):
    env, token = _paired(db, alice)
    _, generation = link_svc.authenticate(db, _hello(env, token))
    pending = link_svc.create_pairing(db, alice.id, env.id).code
    svc.revoke_environment(db, alice.id, env.id)
    with pytest.raises(LinkRefused, match="revoked"):
        link_svc.heartbeat(db, env.id, generation)
    with pytest.raises(LinkRefused, match="not_authorized"):
        link_svc.authenticate(db, _hello(env, token))
    with pytest.raises(LinkRefused):
        link_svc.redeem_pairing(db, _pair_msg(pending), relay_fingerprint=FP)
    with pytest.raises(DesktopError):
        link_svc.create_pairing(db, alice.id, env.id)


# --------------------------------------------------------------------------- #
# The real relay over TLS, with the real guest client
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay(database, tmp_path, monkeypatch):
    # Faster beats than production (2 s / 6 s) so the tests stay quick.
    monkeypatch.setattr(relay_module, "HEARTBEAT_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(connection, "HEARTBEAT_SECONDS", 0.3)
    cert = ensure_relay_certificate(tmp_path / "relay")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        relay_module.create_relay_app(database, cert.fingerprint), host="127.0.0.1", port=port,
        ssl_certfile=str(cert.cert_path), ssl_keyfile=str(cert.key_path), log_level="warning",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "relay did not start"
        time.sleep(0.05)
    yield {"port": port, "cert": cert}
    server.should_exit = True
    thread.join(timeout=10)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _status(database, env_id):
    with database() as db:
        return db.get(DesktopEnvironment, env_id).status


def test_pair_connect_and_revoke_end_to_end(relay, database, alice, tmp_path):
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config_path = tmp_path / "controller.json"
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=config_path))
    assert config.environment_id == str(env.id)
    assert connection.load_config(config_path).token == config.token

    outcome: dict[str, str] = {}

    def run_client():
        try:
            asyncio.run(connection.run_once(config))
        except connection.LinkFailure as exc:
            outcome["reason"] = str(exc)

    client = threading.Thread(target=run_client, daemon=True)
    client.start()
    assert _wait_for(lambda: _status(database, env.id) == "online")

    with database() as db:
        svc.revoke_environment(db, alice.id, env.id)
    client.join(timeout=10)
    assert outcome.get("reason") == "revoked"
    assert _status(database, env.id) == "offline"


def test_a_silent_controller_is_dropped_and_marked_offline(relay, database, alice, tmp_path):
    with database() as db:
        env = _env(db, alice)
        code = link_svc.create_pairing(db, alice.id, env.id).code
    config = asyncio.run(connection.pair("127.0.0.1", relay["port"], code, config_path=tmp_path / "c.json"))

    async def hello_then_silence():
        async with connect(config.url, ssl=connection._pinned_context(config.relay_cert_pem)) as ws:
            await ws.send(_hello(env, config.token).model_dump_json())
            await ws.recv()  # welcome
            with pytest.raises(websockets.ConnectionClosed):
                while True:
                    await ws.recv()  # the timeout error, then the close

    asyncio.run(hello_then_silence())
    assert _wait_for(lambda: _status(database, env.id) == "offline")


def test_the_relay_refuses_plain_connections(relay):
    async def plain():
        async with connect(f"ws://127.0.0.1:{relay['port']}/controller", open_timeout=3):
            pass

    with pytest.raises((websockets.InvalidMessage, websockets.InvalidHandshake, ConnectionError, OSError, TimeoutError)):
        asyncio.run(plain())


def test_a_controller_refuses_a_relay_with_a_different_certificate(relay, database, alice, tmp_path):
    other = ensure_relay_certificate(tmp_path / "impostor")
    config = connection.ControllerConfig(
        relay_host="127.0.0.1", relay_port=relay["port"], environment_id=str(uuid.uuid4()),
        token="t" * 43, relay_cert_pem=other.cert_path.read_text(),
    )
    with pytest.raises(ssl.SSLCertVerificationError):
        asyncio.run(connection.run_once(config))


def test_pairing_with_a_wrong_code_fails_cleanly(relay, database, alice, tmp_path):
    with database() as db:
        env = _env(db, alice)
        link_svc.create_pairing(db, alice.id, env.id)
    with pytest.raises(connection.LinkFailure, match="didn't accept that code"):
        asyncio.run(connection.pair("127.0.0.1", relay["port"], new_pairing_code(), config_path=tmp_path / "c.json"))
    assert not (tmp_path / "c.json").exists()


def test_the_relay_refuses_to_bind_every_interface():
    with pytest.raises(SystemExit):
        relay_module.check_bind_host("0.0.0.0")
    relay_module.check_bind_host("192.168.1.76")
