"""Where the relay may listen: the network the owner approved (docs/VM_CONNECTION_PLAN.md, step 1).

The owner approves one boundary, once:

- **vm_only**: a host-only / virtual-switch interface (UTM, Parallels or VMware Fusion's ``bridge1xx`` /
  ``vmnet*`` on macOS, ``virbr*`` on Linux, VirtualBox's ``vboxnet*``). Only this host and its guests reach
  it, so no firewall rule is needed. Identified by interface name and its subnet.
- **lan**: a trusted home network on an ordinary interface. Identified by interface name, subnet *and the
  gateway's hardware address*: another network with the same 192.168.1.0/24 (a café, a friend's flat) has a
  different router, so the relay stays closed there.

The relay binds to that interface's *current* address, so DHCP moving the Mac within the approved network
needs no new approval. Anywhere else it neither listens nor advertises, and says why (``Decision``).
A wildcard address is never a bind target.

The interface's own hardware address is recorded but not matched: macOS's private Wi-Fi addresses change
it per network and over time; the gateway is what identifies the network.

The decision is pure (``decide``); reading the machine's interfaces and gateway is ``read_interfaces``.
The approval is a small JSON file next to the relay's certificate, written by the API (owner only) and
read by the relay on every check.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

APPROVAL_NAME = "network.json"
STATUS_NAME = "status.json"
# How often the relay looks at the network (and refreshes status.json); older than STATUS_STALE_SECONDS
# means the relay isn't running.
CHECK_SECONDS = 5.0
STATUS_STALE_SECONDS = 3 * CHECK_SECONDS

Kind = Literal["vm_only", "lan"]

# Interfaces only this host and its guests can reach (by name). macOS: bridge100+ are made by the
# virtualization framework / vmnet for VMs (bridge0 is the Thunderbolt bridge, a real network).
_VM_ONLY = re.compile(r"^(bridge1\d\d|vmnet\d+|vboxnet\d+|virbr\d+|vnic\d+)$")
# Never candidates: loopback, tunnels/VPNs, AirDrop/peer-to-peer, docker's own bridges.
_NEVER = re.compile(r"^(lo\d*|utun\d+|ipsec\d+|tun\d+|tap\d+|wg\d+|awdl\d+|llw\d+|anpi\d+|ap\d+|gif\d+|stf\d+"
                    r"|docker\d+|br-[0-9a-f]+|veth.*|tailscale\d*|zt.*)$")


@dataclass(frozen=True)
class Interface:
    """One IPv4 address on one interface, as the machine has it now."""

    name: str
    address: str
    network: str  # CIDR, e.g. 192.168.1.0/24
    mac: str | None = None
    up: bool = True
    # The router this interface reaches the rest of the world through (only the default route's interface).
    gateway_ip: str | None = None
    gateway_mac: str | None = None

    @property
    def vm_only(self) -> bool:
        return bool(_VM_ONLY.match(self.name))


@dataclass(frozen=True)
class Approval:
    kind: Kind
    interface: str
    network: str
    gateway_ip: str | None = None
    gateway_mac: str | None = None
    interface_mac: str | None = None  # recorded, not matched (see module docstring)
    approved_at: str = ""


@dataclass(frozen=True)
class Candidate:
    """An interface the owner could approve, as the dashboard lists it."""

    interface: str
    address: str
    network: str
    kind: Kind
    recommended: bool
    approvable: bool
    # Why not approvable, or what approving it means.
    note: str
    gateway_ip: str | None = None
    gateway_mac: str | None = None
    current: bool = False  # the one the approval names, and it matches


Reason = Literal[
    "open",            # listening on the approved network
    "explicit",        # listening on the address set in .env
    "not_approved",    # nothing approved yet
    "interface_gone",  # the approved interface is missing or down
    "other_network",   # the interface is on a different network than the approved one
    "unidentified",    # can't tell which network this is (gateway unknown)
]


@dataclass(frozen=True)
class Decision:
    address: str | None
    reason: Reason
    detail: str
    interface: str | None = None
    kind: Kind | None = None
    network: str | None = None

    @property
    def open(self) -> bool:
        return self.address is not None


def _private(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def _norm_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    parts = re.split(r"[:-]", mac.strip().lower())
    if len(parts) != 6:
        return None
    try:
        return ":".join(f"{int(p, 16):02x}" for p in parts)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Pure decisions
# --------------------------------------------------------------------------- #


def candidates(interfaces: list[Interface], approval: Approval | None = None) -> list[Candidate]:
    """What the owner may approve: private IPv4 networks on real or VM-only interfaces. A VM-only one is
    recommended when there is one; otherwise the LAN carrying the default route."""
    out: list[Candidate] = []
    for iface in interfaces:
        if not iface.up or _NEVER.match(iface.name) or not _private(iface.address):
            continue
        if iface.vm_only:
            out.append(Candidate(
                iface.name, iface.address, iface.network, "vm_only", False, True,
                "VM-only: only virtual machines running on this computer can reach it (not another computer)",
            ))
        elif iface.gateway_mac:
            out.append(Candidate(
                iface.name, iface.address, iface.network, "lan", False, True,
                "this network (identified by its router); other devices on it can reach the relay port, "
                "so a firewall rule should narrow it", iface.gateway_ip, iface.gateway_mac,
            ))
        else:
            out.append(Candidate(
                iface.name, iface.address, iface.network, "lan", False, False,
                "can't identify this network (no router seen on it), so it can't be approved",
            ))
    recommended = next((c for c in out if c.kind == "vm_only"), None) or next(
        (c for c in out if c.approvable), None)
    current = decide(approval, interfaces) if approval else None
    return [
        Candidate(**{**asdict(c), "recommended": c is recommended,
                     "current": bool(current and current.open and current.interface == c.interface)})
        for c in out
    ]


def approval_for(candidate_name: str, interfaces: list[Interface], *, now: datetime | None = None) -> Approval:
    """The approval for the interface named, as it is now. Raises ValueError if it can't be approved."""
    match = next((c for c in candidates(interfaces) if c.interface == candidate_name), None)
    if match is None:
        raise ValueError(f"{candidate_name} isn't a network the relay can listen on")
    if not match.approvable:
        raise ValueError(match.note)
    iface = next(i for i in interfaces if i.name == candidate_name and i.address == match.address)
    return Approval(
        kind=match.kind, interface=match.interface, network=match.network,
        gateway_ip=match.gateway_ip if match.kind == "lan" else None,
        gateway_mac=match.gateway_mac if match.kind == "lan" else None,
        interface_mac=iface.mac, approved_at=(now or datetime.now(timezone.utc)).isoformat(),
    )


def decide(approval: Approval | None, interfaces: list[Interface],
           gateway_mac_for: Callable[[str, str], str | None] | None = None) -> Decision:
    """Where to listen now, or why not. ``gateway_mac_for(interface, gateway_ip)`` looks the approved
    router up when the interface doesn't carry the default route (a VPN took it, say)."""
    if approval is None:
        return Decision(None, "not_approved", "no network approved for the relay yet; approve one in the app")
    on_iface = [i for i in interfaces if i.name == approval.interface and i.up]
    if not on_iface:
        return Decision(None, "interface_gone", f"{approval.interface} is off or missing",
                        approval.interface, approval.kind, approval.network)
    approved_net = ipaddress.ip_network(approval.network, strict=False)
    inside = [i for i in on_iface if ipaddress.ip_address(i.address) in approved_net and _private(i.address)]
    if not inside:
        nets = ", ".join(sorted({i.network for i in on_iface})) or "no address"
        return Decision(None, "other_network",
                        f"{approval.interface} is on {nets}, not the approved {approval.network}",
                        approval.interface, approval.kind, approval.network)
    iface = inside[0]
    if approval.kind == "lan":
        seen = _norm_mac(iface.gateway_mac)
        if seen is None and gateway_mac_for and approval.gateway_ip:
            seen = _norm_mac(gateway_mac_for(iface.name, approval.gateway_ip))
        if seen is None:
            return Decision(None, "unidentified",
                            f"can't see the router on {approval.interface}, so can't tell this is the approved "
                            "network", approval.interface, approval.kind, approval.network)
        if seen != _norm_mac(approval.gateway_mac):
            return Decision(None, "other_network",
                            f"{approval.interface} is on a different network with the same addresses "
                            f"({approval.network}, another router)", approval.interface, approval.kind,
                            approval.network)
    return Decision(iface.address, "open", f"listening on {approval.interface} ({iface.address})",
                    approval.interface, approval.kind, approval.network)


def decide_explicit(host: str, interfaces: list[Interface]) -> Decision:
    """``DESKTOP_CONTROLLER_BIND_HOST`` set in .env: that address, as before (never a wildcard)."""
    if not host or (_is_ip(host) and ipaddress.ip_address(host).is_unspecified):
        raise SystemExit(f"refusing to bind the desktop relay to {host!r}: never a wildcard address")
    iface = next((i for i in interfaces if i.address == host), None)
    return Decision(host, "explicit", f"listening on {host} (DESKTOP_CONTROLLER_BIND_HOST in .env)",
                    iface.name if iface else None, None, iface.network if iface else None)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# The approval file
# --------------------------------------------------------------------------- #


def load_approval(directory: Path) -> Approval | None:
    try:
        data = json.loads((directory / APPROVAL_NAME).read_text(encoding="utf-8"))
        return Approval(**data)
    except (OSError, ValueError, TypeError):
        return None


def save_approval(directory: Path, approval: Approval | None) -> None:
    path = directory / APPROVAL_NAME
    if approval is None:
        path.unlink(missing_ok=True)
        return
    _write_json(path, asdict(approval))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)


def write_status(directory: Path, data: dict) -> None:
    try:
        _write_json(directory / STATUS_NAME, data)
    except OSError:
        pass


def read_status(directory: Path) -> dict | None:
    try:
        return json.loads((directory / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Reading the machine
# --------------------------------------------------------------------------- #


def read_interfaces() -> list[Interface]:
    """This machine's IPv4 addresses, with the default route's gateway (and its hardware address) on the
    interface that carries it. Never raises: an unreadable piece is just missing."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - a backend dependency
        return []
    gateway_ip, gateway_iface = default_gateway()
    gateway_mac = neighbor_mac(gateway_iface, gateway_ip) if gateway_ip and gateway_iface else None
    stats = psutil.net_if_stats()
    out: list[Interface] = []
    for name, addrs in psutil.net_if_addrs().items():
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), None)
        stat = stats.get(name)
        for a in addrs:
            if a.family != socket.AF_INET or not a.netmask:
                continue
            try:
                network = str(ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False))
            except ValueError:
                continue
            on_route = name == gateway_iface
            out.append(Interface(
                name=name, address=a.address, network=network, mac=_norm_mac(mac), up=bool(stat and stat.isup),
                gateway_ip=gateway_ip if on_route else None, gateway_mac=gateway_mac if on_route else None,
            ))
    return out


def _run(*argv: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def default_gateway() -> tuple[str | None, str | None]:
    """(gateway IPv4, interface) of the default route."""
    if sys.platform == "darwin":
        text = _run("/sbin/route", "-n", "get", "default")
        gw = re.search(r"gateway:\s*(\S+)", text)
        iface = re.search(r"interface:\s*(\S+)", text)
        ip = gw.group(1) if gw else None
        return (ip if ip and _is_ip(ip) else None), (iface.group(1) if iface else None)
    if sys.platform.startswith("linux"):
        match = re.search(r"default via (\S+) dev (\S+)", _run("ip", "-4", "route", "show", "default"))
        return (match.group(1), match.group(2)) if match else (None, None)
    return None, None


def neighbor_mac(interface: str, ip: str) -> str | None:
    """The hardware address of ``ip`` on ``interface`` from the ARP / neighbour table, pinging it once if
    it isn't there yet."""
    for attempt in range(2):
        if sys.platform == "darwin":
            match = re.search(r" at ([0-9a-fA-F:]{11,17}) on (\S+)", _run("/usr/sbin/arp", "-n", ip))
            if match and match.group(2) == interface:
                return _norm_mac(match.group(1))
        elif sys.platform.startswith("linux"):
            match = re.search(r"lladdr ([0-9a-fA-F:]{17})", _run("ip", "neigh", "show", ip, "dev", interface))
            if match:
                return _norm_mac(match.group(1))
        else:
            return None
        if attempt == 0:
            flag = "-t" if sys.platform == "darwin" else "-W"
            _run("ping", "-c", "1", flag, "1", ip)
    return None
