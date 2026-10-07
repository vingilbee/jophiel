"""``desktop-profile-check``: validate profile files (a plan §16 developer tool)."""

from __future__ import annotations

import sys
from pathlib import Path

from .profile import ProfileError, load_profile


def profile_check(argv: list[str] | None = None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    if not paths:
        print("usage: desktop-profile-check PROFILE.yaml [...]", file=sys.stderr)
        return 2
    failed = 0
    for path in paths:
        try:
            profile = load_profile(Path(path))
        except ProfileError as exc:
            failed += 1
            print(f"FAIL {path}")
            for issue in exc.issues:
                print(f"  - {issue}")
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"FAIL {path}\n  - {exc}")
        else:
            print(f"ok   {path}  ({profile.id} r{profile.revision}: "
                  f"{len(profile.buttons)} buttons, {len(profile.axes)} axes, "
                  f"contexts {sorted(profile.contexts.modes)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(profile_check())


def controller(argv: list[str] | None = None) -> int:
    """Pair this machine with the Columbina relay, then stay connected."""
    import argparse
    import asyncio

    from .connection import LinkFailure, default_config_path, load_config, pair, run_forever

    parser = argparse.ArgumentParser(prog="desktop-controller", description=controller.__doc__)
    parser.add_argument("--config", type=Path, default=None, help="where the pairing is saved")
    commands = parser.add_subparsers(dest="command", required=True)
    pairing = commands.add_parser("pair", help="pair with a code from the app's Game session card")
    pairing.add_argument("relay", help="the relay address shown with the code, e.g. 192.168.1.76:8765")
    pairing.add_argument("code", help="the pairing code, e.g. ABCD-EFGH-JKMN")
    commands.add_parser("run", help="connect and stay connected (reconnects on its own)")
    args = parser.parse_args(argv)
    config_path = args.config or default_config_path()

    try:
        if args.command == "pair":
            host, _, port = args.relay.rpartition(":")
            if not host or not port.isdigit():
                parser.error("relay must look like HOST:PORT, e.g. 192.168.1.76:8765")
            asyncio.run(pair(host, int(port), args.code, config_path=config_path))
            print(f"saved to {config_path}. Next: desktop-controller run")
            return 0
        return asyncio.run(run_forever(load_config(config_path)))
    except (LinkFailure, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
