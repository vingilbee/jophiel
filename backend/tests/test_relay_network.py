"""Where the relay listens (docs/VM_CONNECTION_PLAN.md, step 1): the approved network, following the
address within it, closed elsewhere; the firewall rule; the budget for connections that haven't paired.

No database: the supervisor runs with a stand-in server and advertiser; one test binds a real socket on
127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.desktop import firewall, network
from app.ai.desktop import relay as relay_module
from app.ai.desktop.network import Approval, Interface
from app.ai.desktop.tls import ensure_relay_certificate
from app.services import desktop_relay

HOME_ROUTER = "60:98:49:cd:d0:11"
CAFE_ROUTER = "aa:bb:cc:dd:ee:ff"


def _lan(address="192.168.1.76", router=HOME_ROUTER, name="en0", net="192.168.1.0/24", up=True) -> Interface:
    return Interface(name, address, net, "96:62:56:84:bc:df", up, "192.168.1.1", router)


def _vm(address="192.168.139.3") -> Interface:
    return Interface("bridge100", address, "192.168.138.0/23", "3e:06:30:25:e6:64")


def _vpn() -> Interface:
    return Interface("utun3", "10.8.0.2", "10.8.0.0/24")


def _loop() -> Interface:
    return Interface("lo0", "127.0.0.1", "127.0.0.0/8")


# --------------------------------------------------------------------------- #
# What can be approved
# --------------------------------------------------------------------------- #


def test_a_vm_only_interface_is_recommended_over_the_lan():
    found = network.candidates([_loop(), _lan(), _vm(), _vpn()])
    assert [(c.interface, c.kind, c.recommended) for c in found] == [
        ("en0", "lan", False), ("bridge100", "vm_only", True)]  # never loopback or a VPN


def test_without_a_vm_interface_the_lan_is_recommended():
    (only,) = network.candidates([_lan()])
    assert only.recommended and only.approvable and only.gateway_mac == HOME_ROUTER


def test_a_network_whose_router_cant_be_seen_cant_be_approved():
    (unknown,) = network.candidates([_lan(router=None)])
    assert not unknown.approvable and not unknown.recommended
    with pytest.raises(ValueError, match="can't identify"):
        network.approval_for("en0", [_lan(router=None)])


def test_approving_records_the_router_for_a_lan_and_nothing_for_a_vm_interface():
    lan = network.approval_for("en0", [_lan()])
    assert (lan.kind, lan.network, lan.gateway_mac) == ("lan", "192.168.1.0/24", HOME_ROUTER)
    vm = network.approval_for("bridge100", [_vm()])
    assert (vm.kind, vm.gateway_mac) == ("vm_only", None)
    with pytest.raises(ValueError):
        network.approval_for("utun3", [_vpn()])


# --------------------------------------------------------------------------- #
# Where to listen now
# --------------------------------------------------------------------------- #


def test_nothing_approved_means_closed():
    decision = network.decide(None, [_lan()])
    assert not decision.open and decision.reason == "not_approved"


def test_the_relay_follows_a_dhcp_move_inside_the_approved_network():
    approval = network.approval_for("en0", [_lan()])
    assert network.decide(approval, [_lan()]).address == "192.168.1.76"
    moved = network.decide(approval, [_lan(address="192.168.1.120")])
    assert (moved.address, moved.reason) == ("192.168.1.120", "open")


def test_the_same_addresses_behind_another_router_stay_closed():
    approval = network.approval_for("en0", [_lan()])
    cafe = network.decide(approval, [_lan(router=CAFE_ROUTER)])
    assert not cafe.open and cafe.reason == "other_network" and "another router" in cafe.detail


def test_another_subnet_or_a_missing_interface_stays_closed():
    approval = network.approval_for("en0", [_lan()])
    hotspot = network.decide(approval, [_lan(address="172.20.10.4", net="172.20.10.0/28")])
    assert (hotspot.open, hotspot.reason) == (False, "other_network")
    assert network.decide(approval, [_lan(up=False)]).reason == "interface_gone"
    assert network.decide(approval, [_vm()]).reason == "interface_gone"


def test_when_a_vpn_takes_the_default_route_the_router_is_looked_up_directly():
    approval = network.approval_for("en0", [_lan()])
    behind_vpn = _lan(router=None)
    asked = []

    def lookup(interface, ip):
        asked.append((interface, ip))
        return HOME_ROUTER.upper()

    assert network.decide(approval, [behind_vpn, _vpn()], lookup).address == "192.168.1.76"
    assert asked == [("en0", "192.168.1.1")]
    assert network.decide(approval, [behind_vpn], lambda *_: None).reason == "unidentified"


def test_a_vm_only_interface_needs_no_router():
    approval = network.approval_for("bridge100", [_vm()])
    assert network.decide(approval, [_vm(address="192.168.139.9")]).address == "192.168.139.9"


def test_an_explicit_address_is_used_as_is_but_never_a_wildcard():
    assert network.decide_explicit("192.168.1.76", [_lan()]).interface == "en0"
    for wildcard in ("0.0.0.0", "::", ""):
        with pytest.raises(SystemExit):
            network.decide_explicit(wildcard, [_lan()])


def test_the_approval_file_round_trips(tmp_path):
    approval = network.approval_for("en0", [_lan()])
    network.save_approval(tmp_path, approval)
    assert network.load_approval(tmp_path) == approval
    network.save_approval(tmp_path, None)
    assert network.load_approval(tmp_path) is None
    (tmp_path / network.APPROVAL_NAME).write_text("{not json")
    assert network.load_approval(tmp_path) is None  # unreadable = not approved (closed)


# --------------------------------------------------------------------------- #
# The firewall rule
# --------------------------------------------------------------------------- #


def test_the_pf_rule_admits_only_the_approved_subnet():
    text = firewall.rules("en0", "192.168.1.0/24", 8765, system="macos")
    assert "pass in quick on en0 proto tcp from 192.168.1.0/24 to (en0) port 8765" in text
    assert "block drop in quick proto tcp to any port 8765" in text
    loaded = ("pass in quick on en0 inet proto tcp from 192.168.1.0/24 to (en0) port = 8765 flags S/SA keep state\n"
              "block drop in quick proto tcp from any to any port = 8765\n")
    assert firewall._matches(loaded, "en0", "192.168.1.0/24", 8765)
    assert not firewall._matches(loaded, "en0", "10.0.0.0/24", 8765)
    assert not firewall._matches("", "en0", "192.168.1.0/24", 8765)


def test_the_nft_rule_and_its_check():
    text = firewall.rules("eth0", "192.168.1.0/24", 8765, system="linux")
    assert 'iifname "eth0" ip saddr 192.168.1.0/24 tcp dport 8765 accept' in text
    assert firewall._matches(text, "eth0", "192.168.1.0/24", 8765)


# --------------------------------------------------------------------------- #
# Connections that haven't said who they are
# --------------------------------------------------------------------------- #


def test_unpaired_connections_are_limited_in_all_and_per_address():
    gate = relay_module.UnpairedGate(total=5, per_host=2)
    assert gate.enter("10.0.0.9") and gate.enter("10.0.0.9")
    assert not gate.enter("10.0.0.9")  # a third from one address
    assert gate.enter("10.0.0.2") and gate.enter("10.0.0.3") and gate.enter("10.0.0.4")
    assert not gate.enter("10.0.0.5")  # five in all
    gate.leave("10.0.0.9")
    assert gate.enter("10.0.0.5")
    for host in ("10.0.0.9", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"):
        gate.leave(host)
    assert not gate.by_host


# --------------------------------------------------------------------------- #
# The supervisor
# --------------------------------------------------------------------------- #


class _Ads:
    def __init__(self):
        self.events = []

    def __call__(self, address, port, fingerprint):
        ads = self

        class _One:
            async def start(self):
                ads.events.append(("start", address))
                return True

            async def stop(self):
                ads.events.append(("stop", address))

        return _One()


class _World:
    """The machine as the supervisor sees it, changed by the test."""

    def __init__(self, tmp_path, interfaces):
        self.interfaces = interfaces
        self.bound = []
        self.serving = []
        self.ads = _Ads()
        self.directory = tmp_path / "relay"
        self.cert = ensure_relay_certificate(self.directory)

    def bind(self, address, port):
        self.bound.append(address)
        return object()

    def serve(self, sock):
        world = self

        class _Server:
            should_exit = False
            force_exit = False

        server = _Server()

        async def run():
            while not server.should_exit:
                await asyncio.sleep(0.01)
            world.serving.remove(server)

        world.serving.append(server)
        return server, asyncio.create_task(run())

    def supervisor(self, explicit=None):
        return relay_module.RelaySupervisor(
            app=None, cert=self.cert, port=8765, directory=self.directory, explicit_host=explicit,
            read_interfaces=lambda: self.interfaces, gateway_mac_for=lambda *_: None,
            advertiser=self.ads, bind=self.bind, serve=self.serve,
        )

    def status(self):
        return json.loads((self.directory / network.STATUS_NAME).read_text())


def test_the_relay_opens_moves_and_closes_with_the_network(tmp_path):
    world = _World(tmp_path, [_lan()])
    sup = world.supervisor()

    async def scenario():
        await sup.check()
        assert world.status()["state"] == "closed" and world.status()["reason"] == "not_approved"
        assert world.bound == [] and world.ads.events == []

        network.save_approval(world.directory, network.approval_for("en0", world.interfaces))
        await sup.check()
        assert world.bound == ["192.168.1.76"] and len(world.serving) == 1
        assert world.ads.events == [("start", "192.168.1.76")]
        status = world.status()
        assert (status["state"], status["address"], status["advertising"]) == ("open", "192.168.1.76", True)
        assert status["fingerprint"] == world.cert.fingerprint

        await sup.check()  # nothing changed: nothing rebinds
        assert world.bound == ["192.168.1.76"]

        world.interfaces = [_lan(address="192.168.1.120")]  # DHCP moved the Mac
        await sup.check()
        assert world.bound == ["192.168.1.76", "192.168.1.120"] and len(world.serving) == 1
        assert world.ads.events[-2:] == [("stop", "192.168.1.76"), ("start", "192.168.1.120")]

        world.interfaces = [_lan(address="192.168.1.120", router=CAFE_ROUTER)]  # another network
        await sup.check()
        assert world.serving == [] and world.ads.events[-1] == ("stop", "192.168.1.120")
        status = world.status()
        assert (status["state"], status["reason"], status["address"], status["advertising"]) == (
            "closed", "other_network", None, False)

        world.interfaces = [_lan(address="192.168.1.33")]  # back home: no new approval needed
        await sup.check()
        assert world.status()["address"] == "192.168.1.33"

        stop = asyncio.Event()
        stop.set()
        await sup.run(stop)
        assert world.serving == [] and world.status()["state"] == "stopped"

    asyncio.run(scenario())


def test_a_bind_failure_is_reported_and_retried(tmp_path):
    world = _World(tmp_path, [_lan()])
    network.save_approval(world.directory, network.approval_for("en0", world.interfaces))
    fail = {"on": True}

    def bind(address, port):
        if fail["on"]:
            raise OSError(49, "Can't assign requested address")
        return object()

    sup = world.supervisor()
    sup.bind = bind

    async def scenario():
        await sup.check()
        status = world.status()
        assert (status["state"], status["reason"]) == ("error", "cant_listen")
        assert "Can't assign requested address" in status["detail"]
        fail["on"] = False
        await sup.check()
        assert world.status()["state"] == "open"
        await sup.close()

    asyncio.run(scenario())


def test_a_server_that_died_is_started_again(tmp_path):
    world = _World(tmp_path, [_lan()])
    network.save_approval(world.directory, network.approval_for("en0", world.interfaces))
    sup = world.supervisor()

    async def scenario():
        await sup.check()
        sup.server.should_exit = True
        await asyncio.sleep(0.05)
        await sup.check()
        assert world.bound == ["192.168.1.76", "192.168.1.76"] and len(world.serving) == 1
        await sup.close()

    asyncio.run(scenario())


def test_an_explicit_address_ignores_the_approval_and_loopback_isnt_advertised(tmp_path):
    world = _World(tmp_path, [_lan(), _loop()])
    sup = world.supervisor(explicit="127.0.0.1")

    async def scenario():
        await sup.check()
        status = world.status()
        assert (status["state"], status["reason"], status["mode"]) == ("open", "explicit", "explicit")
        assert world.ads.events == []
        await sup.close()

    asyncio.run(scenario())


def test_the_real_listener_binds_only_the_address_given(tmp_path):
    sock = relay_module._bind("127.0.0.1", 0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
        assert sock.type == socket.SOCK_STREAM
    finally:
        sock.close()
    with pytest.raises(SystemExit):
        relay_module._bind("0.0.0.0", 0)


# --------------------------------------------------------------------------- #
# What the dashboard reads
# --------------------------------------------------------------------------- #


@pytest.fixture
def approved_mode(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "desktop_controller_bind_host", None)
    monkeypatch.setattr(get_settings(), "desktop_controller_public_host", None)


def test_a_relay_that_stopped_writing_is_shown_as_not_running(tmp_path, approved_mode):
    directory = tmp_path / "relay"
    ensure_relay_certificate(directory)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    network.write_status(directory, {"state": "open", "address": "192.168.1.76", "updated_at": old})
    status = desktop_relay.status(read_interfaces=lambda: [_lan()], directory=directory)
    assert status["state"] == "stopped" and "isn't running" in status["detail"]
    assert desktop_relay.current_address(directory) is None


def test_approving_from_the_dashboard_and_the_firewall_state(tmp_path, approved_mode):
    directory = tmp_path / "relay"
    ensure_relay_certificate(directory)
    status = desktop_relay.approve("en0", read_interfaces=lambda: [_lan(), _vm()], directory=directory)
    assert status["approval"]["interface"] == "en0" and status["mode"] == "approved"
    assert status["firewall"]["state"] == "unverified" and "firewall show" in status["firewall"]["how"]
    assert any("router" in item for item in status["cannot_verify"])

    # A check that found the rule for this very network.
    network._write_json(directory / firewall.RECORD_NAME, {
        "state": "present", "checked_at": datetime.now(timezone.utc).isoformat(),
        "rule": firewall.digest(firewall.rules("en0", "192.168.1.0/24", 8765)),
    })
    assert desktop_relay.status(read_interfaces=lambda: [_lan()], directory=directory)["firewall"]["state"] == "present"

    status = desktop_relay.approve("bridge100", read_interfaces=lambda: [_lan(), _vm()], directory=directory)
    assert status["firewall"]["state"] == "not_needed"

    from app.services.desktop_sessions import DesktopError

    with pytest.raises(DesktopError):
        desktop_relay.approve("utun3", read_interfaces=lambda: [_vpn()], directory=directory)
    assert desktop_relay.forget(read_interfaces=lambda: [_lan()], directory=directory)["approval"] is None


def test_the_pairing_address_is_where_the_relay_listens_now(tmp_path, approved_mode):
    directory = tmp_path / "relay"
    network.write_status(directory, {"state": "open", "address": "192.168.1.120",
                                     "updated_at": datetime.now(timezone.utc).isoformat()})
    assert desktop_relay.current_address(directory) == "192.168.1.120:8765"


def test_the_supervisor_really_serves_tls_on_the_address_and_stops(tmp_path, monkeypatch):
    """The real path: our own bound socket, uvicorn over TLS, the real relay app; loopback, so never advertised."""
    import ssl
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    directory = tmp_path / "relay"
    cert = ensure_relay_certificate(directory)
    ads = _Ads()
    sup = relay_module.RelaySupervisor(
        relay_module.create_relay_app(lambda: None, cert.fingerprint), cert, port=port, directory=directory,
        explicit_host="127.0.0.1", read_interfaces=lambda: [_loop()], advertiser=ads,
    )

    def health() -> dict:
        context = ssl.create_default_context(cafile=str(cert.cert_path))
        context.check_hostname = False
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/health", context=context, timeout=5) as response:
            return json.loads(response.read())

    async def scenario():
        await sup.check()
        for _ in range(100):
            if sup.server is not None and sup.server.started:
                break
            await asyncio.sleep(0.05)
        assert (await asyncio.to_thread(health))["status"] == "ok"
        assert ads.events == []  # loopback: never announced
        stop = asyncio.Event()
        stop.set()
        await sup.run(stop)
        with pytest.raises(OSError):
            await asyncio.to_thread(health)

    asyncio.run(scenario())
    assert json.loads((directory / network.STATUS_NAME).read_text())["state"] == "stopped"
