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
    """Pair this machine with Jophiel's relay once, then stay connected (and start at login)."""
    import argparse
    import asyncio

    from .connection import LinkFailure, default_config_path, load_config, status_path_for

    parser = argparse.ArgumentParser(prog="desktop-controller", description=controller.__doc__)
    parser.add_argument("--config", type=Path, default=None, help="where the pairing is saved")
    commands = parser.add_subparsers(dest="command", required=True)
    pairing = commands.add_parser(
        "pair", help="pair with the one-time command from the app (no address needed)",
        description="Usual form: pair CODE --fingerprint FP (copied from the app). The older form "
                    "pair HOST:PORT CODE still works.")
    pairing.add_argument("args", nargs="+", metavar="CODE", help="the pairing code, e.g. ABCD-EFGH-JKMN")
    pairing.add_argument("--fingerprint", help="the relay's certificate fingerprint, from the app's command")
    pairing.add_argument("--relay", help="the relay's address, HOST:PORT, if it can't be found on its own")
    pairing.add_argument("--hostname", help="a DNS name for the relay, tried when discovery is blocked")
    pairing.add_argument("--no-startup", action="store_true", help="don't start the controller at login")
    pairing.add_argument("--no-start", action="store_true", help="don't start the controller now")
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
    running.add_argument("--background", action="store_true", help=argparse.SUPPRESS)  # started at login
    running.add_argument("--log", type=Path, default=None, help="write output to this file instead")
    start = commands.add_parser("startup", help="start the controller at login: on, off, or show")
    start.add_argument("action", choices=["on", "off", "show"])
    commands.add_parser("status", help="what the controller is doing, and whether it starts at login")
    args = parser.parse_args(argv)
    config_path = args.config or default_config_path()

    try:
        if args.command == "pair":
            return _pair(parser, args, config_path)
        if args.command == "startup":
            return _startup(args.action, config_path)
        if args.command == "status":
            return _status(config_path)
        if args.log is not None:
            _log_to(args.log)
        saved = load_config(config_path)
        if saved.ended:
            print(f"stopped: this pairing ended ({saved.ended}); pair again from the app", file=sys.stderr)
            return 1
        return _run(args, saved, config_path, status_path_for(config_path))
    except (LinkFailure, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


def _pair(parser, args, config_path: Path) -> int:
    import asyncio

    from . import discovery
    from .connection import pair, pair_anywhere

    if len(args.args) == 2:  # the older form: pair HOST:PORT CODE
        endpoint = discovery.parse_endpoint(args.args[0])
        fingerprint = discovery.normalize_fingerprint(args.fingerprint) if args.fingerprint else None
        config = asyncio.run(pair(endpoint.host, endpoint.port, args.args[1], config_path=config_path,
                                  fingerprint=fingerprint, hostname=args.hostname))
    elif len(args.args) == 1:
        if not args.fingerprint:
            parser.error("pair CODE needs --fingerprint (copy the whole command from the app)")
        relay = discovery.parse_endpoint(args.relay) if args.relay else None
        config = asyncio.run(pair_anywhere(args.args[0], args.fingerprint, relay=relay, hostname=args.hostname,
                                           config_path=config_path))
    else:
        parser.error("usage: pair CODE --fingerprint FP")
    from .link import display_fingerprint

    print(f"relay certificate pinned: {display_fingerprint(config.relay_fingerprint)}")
    print(f"saved to {config_path}")
    if sys.platform == "darwin":
        from .machine import mac_request_permissions

        mac_request_permissions()  # macOS asks once; the dashboard shows what's still missing
    if not args.no_startup:
        _startup("on", config_path)
    if args.no_start:
        print("Next: desktop-controller run")
        return 0
    from .startup import controller_command, start_now

    start_now(controller_command(config_path, _log_path(config_path)))
    print("started in the background; the app shows this machine as connected in a few seconds "
          "(`desktop-controller status` shows it here)")
    return 0


def _log_path(config_path: Path) -> Path:
    return config_path.with_name("controller.log")


def _startup(action: str, config_path: Path) -> int:
    from .startup import controller_command, for_this_machine

    manager = for_this_machine()
    if manager is None:
        print(f"starting at login isn't supported on {sys.platform}", file=sys.stderr)
        return 1
    if action == "on":
        state = manager.register(controller_command(config_path, _log_path(config_path)))
        print(f"starts at login ({state.method})" + (f": {state.detail}" if state.detail else ""))
    elif action == "off":
        manager.unregister()
        print("won't start at login any more (a controller running now keeps running)")
    else:
        state = manager.state()
        print(f"starts at login: {'yes (' + str(state.method) + ')' if state.registered else 'no'}")
    return 0


def _status(config_path: Path) -> int:
    from .connection import load_config, read_status, status_path_for
    from .startup import for_this_machine

    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001 - not paired, unreadable: say so
        print(f"pairing: {exc}")
        return 1
    print(f"pairing: environment {config.environment_id}" + (f" (ended: {config.ended})" if config.ended else ""))
    print(f"relay: last seen at {config.relay_host}:{config.relay_port}"
          + (f", host name {config.relay_hostname}" if config.relay_hostname else ""))
    report = read_status(status_path_for(config_path))
    if report:
        print(f"controller: {report.get('state')} at {report.get('at')}"
              + (f" — {report['detail']}" if report.get("detail") else ""))
    else:
        print("controller: no report yet (not started?)")
    manager = for_this_machine()
    if manager is not None:
        state = manager.state()
        print(f"starts at login: {'yes (' + str(state.method) + ')' if state.registered else 'no'}")
    from .machine import status as machine_status

    machine = machine_status()
    for name, capability in (("screen capture", machine.capture), ("input", machine.input)):
        print(f"{name}: {'available' if capability.available else 'unavailable'}"
              + (f" — {capability.reason}" if capability.reason else ""))
    return 0


def _log_to(path: Path) -> None:
    """Login starts have no console: output goes to a small log next to the pairing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".old.log"))
    except OSError:
        pass
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def _run(args, saved, config_path: Path, status_path: Path) -> int:
    import asyncio

    from .connection import run_forever, write_status
    from .security import collector_for, default_state_path
    from .startup import AlreadyRunning, SingleInstance, for_this_machine

    try:
        lock = SingleInstance(config_path.with_name(config_path.name + ".lock")).__enter__()
    except AlreadyRunning:
        print("a controller is already running for this pairing; not starting a second one")
        return 0
    try:
        from .capture import FakeCapture, default_backend
        from .machine import game_input_factory
        from .machine import status as machine_status

        capture = FakeCapture() if args.fake_capture else default_backend()
        manager = for_this_machine()

        def report():
            state = manager.state() if manager is not None else None
            return machine_status(
                startup="unknown" if state is None else ("registered" if state.registered else "not_registered"),
                startup_method=state.method if state else None)

        write_status(status_path, "connecting", machine=report())
        # Security monitoring reads this machine's event logs only while the owner has it switched on in the app.
        security = collector_for(saved.environment_id, default_state_path(config_path), fake=args.fake_security)
        input_factory = _input_factory(args.fake_input) if args.fake_input else game_input_factory()
        return asyncio.run(run_forever(
            saved, capture=capture, input_factory=input_factory,
            desktop=_desktop_hands(capture, args.fake_input), security=security,
            config_path=config_path, status_path=status_path, machine_status=report,
        ))
    finally:
        lock.__exit__(None, None, None)


def _desktop_hands(capture, fake: bool):
    """Her hands on the whole desktop (general desktop control): this machine's input (Windows, macOS with
    Accessibility permission, Linux X11), printed with --fake-input, none otherwise (actions are refused). Commands
    run in this machine's shell; a dry run (--fake-input) has none."""
    from .desktop import DesktopHands

    if fake:
        from .fake import FakeDesktopInput

        return DesktopHands(FakeDesktopInput(echo=True), capture)
    from .machine import desktop_input

    try:
        backend = desktop_input()
    except Exception as exc:  # noqa: BLE001 - no hands, said so in the status report
        print(f"desktop input unavailable: {exc}", file=sys.stderr)
        backend = None
    from .commands import CommandRunner
    from .machine import command_shell

    return DesktopHands(backend, capture, commands=CommandRunner(command_shell()))


def _input_factory(fake: bool):
    """Printed key presses for dry runs (real game input comes from ``machine.game_input_factory``)."""
    from .fake import FakeInput

    return lambda profile: FakeInput(echo=True)
