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
    running = commands.add_parser("run", help="connect and stay connected (reconnects on its own)")
    running.add_argument(
        "--fake-capture", action="store_true", help="send a test picture instead of the screen (for dry runs)"
    )
    running.add_argument(
        "--fake-input", action="store_true", help="print key presses instead of sending them (for dry runs)"
    )
    running.add_argument(
        "--fake-security", action="store_true",
        help="report made-up event-log records instead of this machine's (for dry runs)",
    )
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
        from .capture import FakeCapture, default_backend
        from .security import collector_for, default_state_path

        capture = FakeCapture() if args.fake_capture else default_backend()
        saved = load_config(config_path)
        # Security monitoring reads this machine's event logs only while the owner has it switched on in the app.
        security = collector_for(saved.environment_id, default_state_path(config_path), fake=args.fake_security)
        return asyncio.run(run_forever(
            saved, capture=capture, input_factory=_input_factory(args.fake_input),
            desktop=_desktop_hands(capture, args.fake_input), security=security,
        ))
    except (LinkFailure, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def _desktop_hands(capture, fake: bool):
    """Her hands on the whole desktop (general desktop control): real input on Windows, printed with
    --fake-input, none elsewhere (actions are refused)."""
    from .desktop import DesktopHands

    if fake:
        from .fake import FakeDesktopInput

        return DesktopHands(FakeDesktopInput(echo=True), capture)
    if sys.platform != "win32":
        return DesktopHands(None, capture)
    from .input_windows import WindowsDesktopInput

    return DesktopHands(WindowsDesktopInput(), capture)


def _input_factory(fake: bool):
    """Real keyboard and mouse on Windows, bound to the profile's game window."""
    if fake:
        from .fake import FakeInput

        return lambda profile: FakeInput(echo=True)
    if sys.platform != "win32":
        return None  # sessions report "no_input" instead of pressing anything
    from .input_windows import WindowsInput

    return lambda profile: WindowsInput(profile.target.process)
