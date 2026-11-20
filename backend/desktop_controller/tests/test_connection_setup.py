"""Finding the relay, the pairing file, reconnecting, login start (docs/VM_CONNECTION_PLAN.md).

No relay and no database here: the real-relay checks are in backend/tests/test_desktop_link.py.
"""
from __future__ import annotations

import asyncio
import json
import plistlib
import subprocess

import pytest

from desktop_controller import connection, discovery, machine, startup
from desktop_controller.discovery import Endpoint
from desktop_controller.link import Heartbeat, Welcome, cert_fingerprint

@pytest.fixture
def cert_pem(tmp_path) -> str:
    """A freshly made self-signed certificate."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test relay")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _config(cert_pem: str, **extra) -> connection.ControllerConfig:
    return connection.ControllerConfig(
        relay_host="192.168.1.76", relay_port=8765, environment_id="e" * 8, token="t" * 43,
        relay_cert_pem=cert_pem, **extra,
    )


# --------------------------------------------------------------------------- #
# Addresses and fingerprints
# --------------------------------------------------------------------------- #


def test_fingerprints_are_accepted_as_the_app_shows_them():
    hexed = "ab" * 32
    shown = ":".join(hexed[i:i + 2].upper() for i in range(0, 64, 2))
    assert discovery.normalize_fingerprint(shown) == hexed
    assert discovery.normalize_fingerprint(hexed.upper()) == hexed
    with pytest.raises(ValueError):
        discovery.normalize_fingerprint("ab" * 31)


def test_relay_addresses_parse_with_and_without_a_port():
    assert discovery.parse_endpoint("relay.local:9000") == Endpoint("relay.local", 9000)
    assert discovery.parse_endpoint("[fe80::1]:8765").host == "fe80::1"
    assert discovery.parse_endpoint("relay.local", default_port=8765).port == 8765
    assert str(Endpoint("fe80::1", 8765)) == "[fe80::1]:8765"
    for bad in ("relay.local", "relay.local:0", ":8765", "relay.local:http"):
        with pytest.raises(ValueError):
            discovery.parse_endpoint(bad)


def test_without_zeroconf_discovery_finds_nothing_instead_of_failing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_zeroconf(name, *args, **kwargs):
        if name.startswith("zeroconf"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zeroconf)
    assert discovery.browse("a" * 64, timeout=0.1) == []


# --------------------------------------------------------------------------- #
# The pairing file
# --------------------------------------------------------------------------- #


def test_an_original_pairing_file_still_loads(tmp_path, cert_pem):
    path = tmp_path / "controller.json"
    path.write_text(json.dumps({
        "relay_host": "192.168.1.76", "relay_port": 8765, "environment_id": "env", "token": "tok",
        "relay_cert_pem": cert_pem,
    }))
    config = connection.load_config(path)
    assert (config.relay_host, config.relay_port, config.token) == ("192.168.1.76", 8765, "tok")
    # The fingerprint is worked out from the pinned certificate.
    import ssl

    assert config.relay_fingerprint == cert_fingerprint(ssl.PEM_cert_to_DER_cert(cert_pem))
    assert config.ended is None


def test_the_new_file_keeps_identity_and_address_apart(tmp_path, cert_pem):
    path = tmp_path / "controller.json"
    connection.save_config(_config(cert_pem, relay_hostname="relay.example.lan", ended="revoked"), path)
    data = json.loads(path.read_text())
    assert data["version"] == 2
    assert set(data["relay"]) == {"cert_pem", "fingerprint"}
    assert data["endpoint"] == {"host": "192.168.1.76", "port": 8765, "hostname": "relay.example.lan"}
    again = connection.load_config(path)
    assert (again.relay_hostname, again.ended) == ("relay.example.lan", "revoked")
    assert again.relay_fingerprint == _config(cert_pem).relay_fingerprint
    assert not list(tmp_path.glob("*.tmp"))  # written whole, then moved into place


def test_not_paired_says_how_to_pair(tmp_path):
    with pytest.raises(connection.LinkFailure, match="pair"):
        connection.load_config(tmp_path / "missing.json")


# --------------------------------------------------------------------------- #
# Where to try
# --------------------------------------------------------------------------- #


def test_the_first_try_is_the_last_address_alone(cert_pem):
    calls = []

    def browse(fp):
        calls.append(fp)
        return [Endpoint("192.168.1.80", 8765, "mdns", fp)]

    found = asyncio.run(connection._candidates(_config(cert_pem), browse, first=True))
    assert found == [Endpoint("192.168.1.76", 8765, "saved")]
    assert calls == []  # a normal start doesn't wait for discovery


def test_later_tries_add_the_advertisement_and_the_host_name_without_repeats(cert_pem):
    config = _config(cert_pem, relay_hostname="relay.example.lan")
    seen = []

    def browse(fp):
        seen.append(fp)
        return [Endpoint("192.168.1.76", 8765, "mdns", fp), Endpoint("192.168.1.80", 8765, "mdns", fp)]

    found = asyncio.run(connection._candidates(config, browse, first=False))
    assert [(e.host, e.source) for e in found] == [
        ("192.168.1.76", "saved"), ("192.168.1.80", "mdns"), ("relay.example.lan", "hostname")]
    assert seen == [config.relay_fingerprint]  # only relays claiming our certificate


# --------------------------------------------------------------------------- #
# Reconnecting
# --------------------------------------------------------------------------- #


def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(connection.asyncio, "sleep", instant)


def test_a_relay_that_moved_is_found_and_its_new_address_saved(monkeypatch, tmp_path, cert_pem):
    _no_sleep(monkeypatch)
    path = tmp_path / "controller.json"
    config = _config(cert_pem)
    connection.save_config(config, path)
    tried = []

    async def fake_run_once(config, *, endpoint, on_welcome, **_):
        tried.append(endpoint.host)
        if endpoint.host == "192.168.1.76":
            raise OSError("no route to host")
        on_welcome(Welcome(controller_generation=1))
        raise connection.LinkFailure("revoked")  # end the test's run

    monkeypatch.setattr(connection, "run_once", fake_run_once)
    browse = lambda fp: [Endpoint("192.168.1.90", 8765, "mdns", fp)]  # noqa: E731
    code = asyncio.run(connection.run_forever(config, config_path=path, status_path=tmp_path / "status.json",
                                              browse=browse))
    assert code == 1
    assert tried == ["192.168.1.76", "192.168.1.76", "192.168.1.90"]
    saved = connection.load_config(path)
    assert (saved.relay_host, saved.ended) == ("192.168.1.90", "revoked")
    assert saved.relay_cert_pem == cert_pem  # moving never changes who the relay is
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "stopped"


def test_a_different_certificate_is_skipped_and_retried_not_trusted(monkeypatch, tmp_path, cert_pem):
    _no_sleep(monkeypatch)
    import ssl

    attempts = {"n": 0}

    async def fake_run_once(config, *, endpoint, on_welcome, **_):
        attempts["n"] += 1
        if attempts["n"] < 4:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        raise connection.LinkFailure("not_authorized")

    monkeypatch.setattr(connection, "run_once", fake_run_once)
    status_path = tmp_path / "status.json"
    states = []
    real_write = connection.write_status
    monkeypatch.setattr(connection, "write_status",
                        lambda p, state, detail="", **kw: (states.append((state, detail)), real_write(p, state, detail, **kw)))
    code = asyncio.run(connection.run_forever(_config(cert_pem), config_path=tmp_path / "c.json",
                                              status_path=status_path, browse=lambda fp: []))
    assert code == 1
    retrying = [d for s, d in states if s == "retrying"]
    assert retrying and "different certificate" in retrying[0] and "pair again" in retrying[0]
    assert connection.load_config(tmp_path / "c.json").ended == "not_authorized"


def test_a_lost_connection_is_retried_but_superseded_is_not_saved_as_ended(monkeypatch, tmp_path, cert_pem):
    _no_sleep(monkeypatch)
    reasons = iter(["heartbeat_timeout", "superseded"])

    async def fake_run_once(config, **_):
        raise connection.LinkFailure(next(reasons))

    monkeypatch.setattr(connection, "run_once", fake_run_once)
    path = tmp_path / "c.json"
    code = asyncio.run(connection.run_forever(_config(cert_pem), config_path=path, browse=lambda fp: []))
    assert code == 1
    assert not path.exists()  # superseded: another copy runs; this pairing is still fine


# --------------------------------------------------------------------------- #
# Talking to old relays
# --------------------------------------------------------------------------- #


def test_heartbeats_leave_out_status_unless_asked():
    beat = Heartbeat(gate_epoch_seen=1, target_state="capture_off")
    assert "status" not in json.loads(beat.model_dump_json(exclude_none=True))


def test_a_welcome_from_an_old_relay_still_parses():
    assert Welcome.model_validate_json('{"type": "welcome", "controller_generation": 3}').features is None
    assert Welcome.model_validate_json(
        '{"type": "welcome", "controller_generation": 3, "features": ["status"]}').features == ["status"]


# --------------------------------------------------------------------------- #
# The machine's status
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("env, expected", [
    ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
    ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, "x11"),
    ({"DISPLAY": ":0"}, "x11"),
    ({}, None),
])
def test_linux_display_server_is_told_apart(env, expected):
    assert machine.linux_display(env) == expected


def test_the_status_report_validates():
    report = machine.status(startup="registered", startup_method="launch_agent")
    assert report.machine and report.platform in ("windows", "macos", "linux", "other")
    assert isinstance(report.capture.available, bool) and isinstance(report.input.available, bool)
    if not report.capture.available:
        assert report.capture.reason


# --------------------------------------------------------------------------- #
# Starting at login
# --------------------------------------------------------------------------- #


def _done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class _FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[str, str] = {}

    class _Key:
        def __init__(self, reg):
            self.reg = reg

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKey(self, root, path):  # noqa: N802 - winreg's names
        return self._Key(self)

    def OpenKey(self, root, path, *args):  # noqa: N802
        return self._Key(self)

    def SetValueEx(self, key, name, _reserved, _kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise OSError(name)
        del self.values[name]

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise OSError(name)
        return self.values[name], 1


def test_windows_uses_a_logon_task_and_falls_back_to_the_run_key():
    command = [r"C:\Python\pythonw.exe", "-m", "desktop_controller", "--config", r"C:\Users\o'neil\c.json", "run"]
    script = startup.windows_task_script(command)
    assert "-AtLogOn" in script and "-RunLevel Limited" in script and "IgnoreNew" in script
    assert "o''neil" in script  # quoted for PowerShell

    registry = _FakeRegistry()
    ok = startup.WindowsStartup(run=lambda argv: _done(0), registry=registry)
    registry.values[startup.RUN_VALUE] = "old"
    assert ok.register(command).method == "scheduled_task"
    assert startup.RUN_VALUE not in registry.values  # one way of starting, not two

    refused = startup.WindowsStartup(run=lambda argv: _done(1, err="Access is denied."), registry=registry)
    state = refused.register(command)
    assert (state.registered, state.method) == (True, "run_key")
    assert "Access is denied" in state.detail
    assert registry.values[startup.RUN_VALUE] == subprocess.list2cmdline(command)
    assert refused.state().method == "run_key"
    refused.unregister()
    assert refused.state().registered is False


def test_macos_launch_agent_starts_at_login_and_is_never_restarted_by_launchd(tmp_path):
    calls = []
    mac = startup.MacStartup(run=lambda argv: (calls.append(argv), _done(0))[1], home=tmp_path, uid=501)
    command = ["/usr/bin/python3", "-m", "desktop_controller", "run", "--background"]
    assert mac.register(command).registered
    plist = plistlib.loads(mac.path.read_bytes())
    assert plist["ProgramArguments"] == command and plist["RunAtLoad"] is True and plist["KeepAlive"] is False
    assert ["launchctl", "bootstrap", "gui/501", str(mac.path)] in calls
    mac.unregister()
    assert not mac.state().registered


def test_linux_autostart_entry(tmp_path):
    linux = startup.LinuxStartup(config_home=tmp_path)
    linux.register(["/usr/bin/python3", "-m", "desktop_controller", "--config", "/home/o p/c.json", "run"])
    text = linux.path.read_text()
    assert "Exec=/usr/bin/python3 -m desktop_controller --config '/home/o p/c.json' run" in text
    linux.unregister()
    assert not linux.state().registered


def test_only_one_controller_runs_per_pairing(tmp_path):
    lock = tmp_path / "controller.json.lock"
    with startup.SingleInstance(lock):
        with pytest.raises(startup.AlreadyRunning):
            with startup.SingleInstance(lock):
                pass
    with startup.SingleInstance(lock):  # released when the first one ended
        pass
