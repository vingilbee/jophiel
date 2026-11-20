"""The relay as the dashboard sees it: where it listens, which network is approved, the firewall
(docs/VM_CONNECTION_PLAN.md, step 1).

The relay is its own process; it writes ``status.json`` every few seconds and reads the approval file on
every check. This module (in the API) reads that status, lists the networks the owner could approve, and
writes or forgets the approval. Owner only: the approval is the host's, not one user's.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.ai.desktop import firewall, network
from app.ai.desktop.tls import ensure_relay_certificate, relay_dir
from app.core.config import get_settings
from app.services.desktop_sessions import DesktopError
from desktop_controller.link import display_fingerprint

CANNOT_VERIFY = [
    "that your router doesn't forward the relay port to the internet (it must not)",
]


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _running(raw: dict | None) -> bool:
    at = _parse((raw or {}).get("updated_at"))
    return at is not None and (datetime.now(timezone.utc) - at).total_seconds() <= network.STATUS_STALE_SECONDS


def _firewall(kind: str | None, approval: network.Approval | None, port: int, directory: Path) -> dict:
    if kind == "vm_only":
        return {"state": "not_needed"}
    how = ("in a terminal on this computer, from backend/: `uv run python -m app.ai.desktop.firewall show` "
           "(the rule and how to load it), then `... firewall check`")
    record = firewall.read_record(directory)
    if approval is None or record is None:
        return {"state": "unverified", "how": how}
    expected = firewall.digest(firewall.rules(approval.interface, approval.network, port))
    if record.get("rule") != expected:
        return {"state": "unverified", "how": how, "checked_at": record.get("checked_at")}  # an older network
    return {"state": record.get("state", "unverified"), "checked_at": record.get("checked_at"),
            "how": None if record.get("state") == "present" else how}


def status(*, read_interfaces=network.read_interfaces, directory: Path | None = None) -> dict:
    settings = get_settings()
    directory = directory or relay_dir()
    raw = network.read_status(directory)
    approval = network.load_approval(directory)
    interfaces = read_interfaces()
    mode = "explicit" if settings.desktop_controller_bind_host else "approved"
    if _running(raw):
        out = {k: raw.get(k) for k in ("state", "reason", "detail", "address", "interface", "kind", "advertising")}
    else:
        out = {"state": "stopped", "reason": "stopped", "address": None, "advertising": False,
               "interface": approval.interface if approval else None, "kind": approval.kind if approval else None,
               "detail": "the relay isn't running: start it with `uv run python -m app.ai.desktop.relay`"}
    port = settings.desktop_controller_port
    return {
        **out,
        "mode": mode,
        "port": port,
        "fingerprint": display_fingerprint(ensure_relay_certificate(directory).fingerprint),
        "updated_at": (raw or {}).get("updated_at"),
        "approval": asdict(approval) if approval else None,
        "networks": [asdict(c) for c in network.candidates(interfaces, approval)],
        "firewall": _firewall(out.get("kind") or (approval.kind if approval else None), approval, port, directory),
        "cannot_verify": CANNOT_VERIFY,
    }


def approve(interface: str, *, read_interfaces=network.read_interfaces, directory: Path | None = None) -> dict:
    """Approve the network ``interface`` is on now. The relay picks it up within one check."""
    directory = directory or relay_dir()
    try:
        approval = network.approval_for(interface, read_interfaces())
    except ValueError as exc:
        raise DesktopError("not_approvable", 422, str(exc)) from None
    network.save_approval(directory, approval)
    return status(read_interfaces=read_interfaces, directory=directory)


def forget(*, read_interfaces=network.read_interfaces, directory: Path | None = None) -> dict:
    """No approved network: the relay closes within one check (paired machines go offline)."""
    directory = directory or relay_dir()
    network.save_approval(directory, None)
    return status(read_interfaces=read_interfaces, directory=directory)


def current_address(directory: Path | None = None) -> str | None:
    """HOST:PORT where the relay listens now (for ``--relay``), else the configured public host, else None."""
    settings = get_settings()
    raw = network.read_status(directory or relay_dir())
    if _running(raw) and raw.get("state") == "open" and raw.get("address"):
        host = raw["address"]
    else:
        host = settings.desktop_controller_public_host or settings.desktop_controller_bind_host
    if not host:
        return None
    host = f"[{host}]" if ":" in host else host
    return f"{host}:{settings.desktop_controller_port}"
