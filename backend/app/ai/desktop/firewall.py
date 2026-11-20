"""The host firewall rule that narrows the relay port on a shared LAN (docs/VM_CONNECTION_PLAN.md, step 1).

On a VM-only interface nothing else can reach the port, so no rule is needed. On a home LAN every device on
it can, so the port should admit only the approved subnet. This module writes that rule for the platform's
firewall (pf on macOS, nftables on Linux) and checks whether it is loaded.

Loading or reading firewall rules needs administrator rights, which neither the API nor the relay has. So
the owner runs this helper in a terminal; ``check`` asks for the password through ``sudo`` and records what
it found in ``firewall.json`` for the dashboard. The dashboard never claims more than that record says, and
says what it can't see at all (the router: port forwarding must stay off).

    uv run python -m app.ai.desktop.firewall show    # the rule and how to load it
    uv run python -m app.ai.desktop.firewall check   # is it loaded? (sudo)

pf on macOS forgets runtime rules at restart: run ``check`` after a restart, or load the rule from
``/etc/pf.conf`` (the commands printed by ``show`` say how).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.ai.desktop import network

ANCHOR = "com.apple/jophiel-relay"  # inside macOS's own anchor, which the stock pf.conf already evaluates
NFT_TABLE = "jophiel_relay"
RECORD_NAME = "firewall.json"


def platform() -> str:
    return "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "other"


def rules(interface: str, subnet: str, port: int, *, system: str | None = None) -> str:
    """The rule text: the relay port only from ``subnet`` on ``interface``, dropped from everywhere else."""
    system = system or platform()
    if system == "macos":
        return (f"pass in quick on {interface} proto tcp from {subnet} to ({interface}) port {port}\n"
                f"block drop in quick proto tcp to any port {port}\n")
    if system == "linux":
        return (f"table inet {NFT_TABLE} {{\n"
                f"  chain input {{\n"
                f"    type filter hook input priority -10; policy accept;\n"
                f'    iifname "{interface}" ip saddr {subnet} tcp dport {port} accept\n'
                f"    tcp dport {port} drop\n"
                f"  }}\n"
                f"}}\n")
    raise SystemExit("no firewall helper for this system; narrow the relay port by hand")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_commands(rule_path: Path, *, system: str | None = None) -> list[str]:
    system = system or platform()
    if system == "macos":
        return [f"sudo pfctl -a {ANCHOR} -f {rule_path}", "sudo pfctl -E"]
    return [f"sudo nft -f {rule_path}"]


def _loaded(system: str) -> tuple[bool, str]:
    """(read it, text) of the loaded rules, asking for the password if sudo needs it."""
    argv = (["sudo", "pfctl", "-a", ANCHOR, "-sr"] if system == "macos"
            else ["sudo", "nft", "list", "table", "inet", NFT_TABLE])
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return done.returncode == 0, done.stdout


def _matches(loaded: str, interface: str, subnet: str, port: int) -> bool:
    """pf prints ``... port = 8765 ...`` and ``block drop``; nft prints ``tcp dport 8765 ...`` and ``drop``."""
    text = " ".join(loaded.split())
    port_seen = f"port = {port}" in text or f"dport {port}" in text
    return interface in text and subnet in text and port_seen and "drop" in text


def check(directory: Path, interface: str, subnet: str, port: int) -> dict:
    system = platform()
    read, loaded = _loaded(system)
    record = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "platform": system, "interface": interface, "subnet": subnet, "port": port,
        "rule": digest(rules(interface, subnet, port, system=system)),
        "state": ("present" if _matches(loaded, interface, subnet, port) else "missing") if read else "unverified",
    }
    network._write_json(directory / RECORD_NAME, record)
    return record


def read_record(directory: Path) -> dict | None:
    try:
        return json.loads((directory / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _target(directory: Path) -> tuple[str, str]:
    approval = network.load_approval(directory)
    if approval is None:
        raise SystemExit("approve a network for the relay in the app first")
    if approval.kind == "vm_only":
        raise SystemExit(f"{approval.interface} is VM-only: nothing else can reach the relay port, no rule needed")
    return approval.interface, approval.network


def main(argv: list[str] | None = None) -> int:
    from app.ai.desktop.tls import relay_dir
    from app.core.config import get_settings

    parser = argparse.ArgumentParser(prog="firewall", description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["show", "check"])
    args = parser.parse_args(argv)
    directory, port = relay_dir(), get_settings().desktop_controller_port
    interface, subnet = _target(directory)
    text = rules(interface, subnet, port)
    rule_path = directory / ("relay.pf" if platform() == "macos" else "relay.nft")
    rule_path.write_text(text, encoding="utf-8")
    if args.action == "show":
        print(f"Rule ({rule_path}):\n\n{text}\nLoad it:\n  " + "\n  ".join(load_commands(rule_path)))
        if platform() == "macos":
            print(f'\nTo keep it after a restart, add to /etc/pf.conf:\n  load anchor "{ANCHOR}" from "{rule_path}"')
        print("\nAlso: never forward the relay port on your router (this Mac can't check that).")
        return 0
    record = check(directory, interface, subnet, port)
    print({"present": "the rule is loaded", "missing": "the rule isn't loaded: run `show` and load it",
           "unverified": "couldn't read the firewall (sudo refused?)"}[record["state"]])
    return 0 if record["state"] == "present" else 1


if __name__ == "__main__":
    raise SystemExit(main())
