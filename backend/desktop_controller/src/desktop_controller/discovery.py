"""Finding the relay without an IP address (docs/VM_CONNECTION_PLAN.md, step 1).

The relay advertises one DNS-SD service over mDNS, only on the network the owner approved:
``_jophiel-relay._tcp.local.``, with its certificate fingerprint in the TXT record (``fp``).

Discovery only *suggests* where the relay is. Identity is always the certificate: pairing checks the
fingerprint from the dashboard's command before it sends anything, and a paired controller trusts exactly
its pinned certificate. So a second relay (or anything else) advertising the same service name gets
nothing: the controller skips a candidate whose fingerprint differs, and the TLS handshake fails against a
certificate that isn't the pinned one.

``zeroconf`` is imported lazily: without it (or with multicast blocked) a controller still connects to its
last address, a DNS host name, or an explicit ``--relay``.
"""
from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass

SERVICE_TYPE = "_jophiel-relay._tcp.local."
# TXT keys: fp = sha256 of the relay's DER certificate (lowercase hex); proto = the link protocol.
TXT_FINGERPRINT = "fp"
TXT_PROTOCOL = "proto"
BROWSE_SECONDS = 3.0

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    # Where this candidate came from: "saved", "mdns", "hostname" or "explicit".
    source: str = "explicit"
    # The fingerprint the advertisement claims (mDNS only). A claim, never trusted: checked on connect.
    fingerprint: str | None = None

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


def normalize_fingerprint(text: str) -> str:
    """``AB:12:...`` or ``ab12...`` -> 64 lowercase hex characters. Raises ValueError otherwise."""
    cleaned = re.sub(r"[\s:]", "", text or "").lower()
    if not _HEX64.match(cleaned):
        raise ValueError("a relay fingerprint is 64 hex characters (colons allowed), as shown in the app")
    return cleaned


def parse_endpoint(text: str, *, source: str = "explicit", default_port: int | None = None) -> Endpoint:
    """``HOST:PORT`` (``[v6]:PORT`` for IPv6), or ``HOST`` alone when ``default_port`` is given."""
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, sep, port = text.rpartition(":")
        if not sep:
            host, port = text, ""
    if not port and default_port is not None:
        port = str(default_port)
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("a relay address looks like HOST:PORT, e.g. jophiel-relay.local:8765")
    return Endpoint(host, int(port), source)


def browse(fingerprint: str | None = None, *, timeout: float = BROWSE_SECONDS) -> list[Endpoint]:
    """Relays advertising on the local network, the one claiming ``fingerprint`` first.

    Empty when zeroconf isn't installed, multicast is blocked, or nothing answers in ``timeout`` seconds.
    Never raises: discovery failing just means trying the other candidates."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []

    found: dict[str, Endpoint] = {}

    class _Listener(ServiceListener):
        def add_service(self, zc, type_, name):  # noqa: ANN001 - zeroconf's signature
            self._resolve(zc, type_, name)

        def update_service(self, zc, type_, name):  # noqa: ANN001
            self._resolve(zc, type_, name)

        def remove_service(self, zc, type_, name):  # noqa: ANN001
            found.pop(name, None)

        @staticmethod
        def _resolve(zc, type_, name) -> None:  # noqa: ANN001
            info = zc.get_service_info(type_, name, timeout=1500)
            if info is None or not info.port:
                return
            props = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                     for k, v in (info.properties or {}).items() if v is not None}
            claimed = props.get(TXT_FINGERPRINT)
            try:
                claimed = normalize_fingerprint(claimed) if claimed else None
            except ValueError:
                claimed = None
            for address in info.parsed_addresses():
                found.setdefault(name, Endpoint(address, int(info.port), "mdns", claimed))
                break

    try:
        zc = Zeroconf()
    except OSError:
        return []
    try:
        ServiceBrowser(zc, SERVICE_TYPE, _Listener())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fingerprint and any(e.fingerprint == fingerprint for e in found.values()):
                break  # the one we want answered: no need to wait out the rest
            time.sleep(0.1)
    except OSError:
        return []
    finally:
        try:
            zc.close()
        except Exception:  # noqa: BLE001 - closing a half-open socket set
            pass
    endpoints = list(found.values())
    if fingerprint:
        # Only relays claiming our fingerprint are worth a connection; the claim is checked on connect.
        endpoints = [e for e in endpoints if e.fingerprint == fingerprint]
    return endpoints


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True
