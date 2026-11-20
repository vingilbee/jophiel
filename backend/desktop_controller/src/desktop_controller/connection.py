"""The guest side of the link: pair once, then stay connected (plan §19, docs/VM_CONNECTION_PLAN.md).

Runs on the paired machine. ``pair`` trusts nothing until the relay proves it
knows the pairing code (see ``link``), then pins the relay's certificate and
saves the device token. ``run`` only ever connects to that pinned certificate,
authenticates, and heartbeats.

Where the relay is and who it is are kept apart. Identity is the pinned
certificate (and its fingerprint), fixed at pairing. The address is only the
last one that worked: when it stops working the controller looks again (the
relay's mDNS advertisement, then a DNS host name if one was given) and saves
whatever answers *with the pinned certificate*. A relay with any other
certificate is never talked to, so moving addresses can't hand the pairing to
someone else. When pairing with a fingerprint (the dashboard's command), the
controller checks it before it sends anything, so with two relays on the
network only the one named in the command is paired with.

The saved file holds a secret (the device token). It goes in the user's
profile with owner-only permissions where the OS supports them; revoking the
environment in the app makes it useless, and the controller then stops trying.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import ssl
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection, connect

from . import __version__
from . import discovery
from .capture import CaptureBackend
from .discovery import Endpoint
from .desktop import INPUT_KINDS, DesktopHands
from .profile import Profile, ProfileError, parse_profile
from .reflexes import sample_size
from .security import ACK_SECONDS, SecurityCollector
from .runtime import GuardedBackend, Sampler, SessionRuntime, TargetLost
from .link import (
    MAX_FRAME_BYTES,
    HEARTBEAT_ACTIVE_SECONDS,
    HEARTBEAT_SECONDS,
    HEARTBEAT_TIMEOUT_SECONDS,
    Cancel,
    CaptureConfig,
    ControlGrant,
    ControllerStatus,
    DecisionCommand,
    DecisionReport,
    DesktopAction,
    DesktopResult,
    FrameHeader,
    Hello,
    Heartbeat,
    HeartbeatAck,
    LinkError,
    Pair,
    Paired,
    PauseRequest,
    ReflexReport,
    SecurityAck,
    SecurityConfig,
    SessionProblem,
    SessionReady,
    SessionStart,
    SessionStop,
    TargetState,
    Welcome,
    cert_fingerprint,
    client_proof,
    code_lookup,
    display_fingerprint,
    pack_frame,
    proofs_match,
    relay_message,
    server_proof,
)

MAX_MESSAGE_BYTES = MAX_FRAME_BYTES
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
# How long a closing connection waits for a desktop action's thread to stop (it checks its stop every step).
DESKTOP_DRAIN_SECONDS = 5.0
# Refusals after which reconnecting cannot help.
FATAL = {"not_authorized", "revoked", "superseded", "unsupported_protocol"}
# Of those, the ones that mean this pairing is over: saved in the pairing file, so a login start stops at once.
ENDED = {"not_authorized", "revoked"}
# How often the machine's status (capture, input, permissions) is looked at again while connected.
STATUS_CHECK_SECONDS = 30.0
CONFIG_VERSION = 2


class LinkFailure(Exception):
    pass


@dataclass
class ControllerConfig:
    # The last address the relay answered on (with the pinned certificate). Only a hint.
    relay_host: str
    relay_port: int
    environment_id: str
    token: str
    # The relay's identity, pinned at pairing: never changed by finding the relay somewhere else.
    relay_cert_pem: str
    relay_fingerprint: str = ""
    # A DNS name to try when the saved address and mDNS both fail (networks that block multicast).
    relay_hostname: str | None = None
    # Set when the relay said this pairing is over (revoked, key no longer valid): nothing is retried.
    ended: str | None = None

    def __post_init__(self) -> None:
        if not self.relay_fingerprint and self.relay_cert_pem:
            self.relay_fingerprint = cert_fingerprint(ssl.PEM_cert_to_DER_cert(self.relay_cert_pem))

    @property
    def url(self) -> str:
        return _url(Endpoint(self.relay_host, self.relay_port))

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(self.relay_host, self.relay_port, "saved")


def _url(endpoint: Endpoint) -> str:
    host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    return f"wss://{host}:{endpoint.port}/controller"


def default_config_path() -> Path:
    override = os.environ.get("DESKTOP_CONTROLLER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    return Path(base or Path.home()) / ("Columbina" if base else ".columbina") / "controller.json"


def save_config(config: ControllerConfig, path: Path) -> None:
    """The pairing file, version 2: identity and endpoint in separate sections."""
    data = {
        "version": CONFIG_VERSION,
        "environment_id": config.environment_id,
        "token": config.token,
        "relay": {"cert_pem": config.relay_cert_pem, "fingerprint": config.relay_fingerprint},
        "endpoint": {"host": config.relay_host, "port": config.relay_port, "hostname": config.relay_hostname},
        "ended": config.ended,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)  # never a half-written pairing file


def load_config(path: Path) -> ControllerConfig:
    """Version 2, or the original flat file (``relay_host``, ``relay_port``, ``relay_cert_pem``, ...)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LinkFailure(f"not paired yet: no {path}; run `desktop-controller pair` first") from None
    if data.get("version") == CONFIG_VERSION:
        relay, endpoint = data.get("relay") or {}, data.get("endpoint") or {}
        return ControllerConfig(
            relay_host=endpoint.get("host") or "", relay_port=int(endpoint.get("port") or 0),
            environment_id=data["environment_id"], token=data["token"], relay_cert_pem=relay["cert_pem"],
            relay_fingerprint=relay.get("fingerprint") or "", relay_hostname=endpoint.get("hostname"),
            ended=data.get("ended"),
        )
    return ControllerConfig(
        relay_host=data["relay_host"], relay_port=int(data["relay_port"]), environment_id=data["environment_id"],
        token=data["token"], relay_cert_pem=data["relay_cert_pem"],
    )


# --------------------------------------------------------------------------- #
# Local status (what the controller is doing, readable while disconnected)
# --------------------------------------------------------------------------- #


def status_path_for(config_path: Path) -> Path:
    return config_path.with_name("status.json")


def write_status(path: Path | None, state: str, detail: str = "", *, relay: str | None = None,
                 machine: ControllerStatus | None = None) -> None:
    """``state``: connecting / connected / retrying / stopped. Never raises: it's only a report."""
    if path is None:
        return
    data = {"state": state, "detail": detail, "relay": relay, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "pid": os.getpid()}
    if machine is not None:
        data["machine"] = machine.model_dump(mode="json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pinned_context(cert_pem: str) -> ssl.SSLContext:
    """Trust exactly the pinned relay certificate and nothing else."""
    context = ssl.create_default_context(cadata=cert_pem)
    context.check_hostname = False  # the pin, not a name, identifies the relay
    return context


def _unverified_context() -> ssl.SSLContext:
    """Pairing only: the certificate is verified by the code proof instead."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _peer_der(ws: ClientConnection) -> bytes:
    der = ws.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
    if not der:
        raise LinkFailure("the relay presented no certificate")
    return der


async def _expect(ws: ClientConnection, timeout: float):
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout)
    except websockets.ConnectionClosed:
        raise LinkFailure("the relay closed the connection") from None
    message = relay_message.validate_json(raw)
    if isinstance(message, LinkError):
        raise LinkFailure(message.code)
    return message


# --------------------------------------------------------------------------- #
# Pair
# --------------------------------------------------------------------------- #


class WrongRelay(LinkFailure):
    """Whatever answered there has a different certificate than the one named in the pairing command."""


async def pair(host: str, port: int, code: str, *, config_path: Path | None = None,
               fingerprint: str | None = None, hostname: str | None = None) -> ControllerConfig:
    """Pair with the relay at ``host:port``. With ``fingerprint`` (the dashboard's command), nothing is sent
    unless the relay there presents exactly that certificate."""
    return await _pair_at(Endpoint(host, port), code, expected=fingerprint, config_path=config_path,
                          hostname=hostname)


async def pair_anywhere(code: str, fingerprint: str, *, relay: Endpoint | None = None, hostname: str | None = None,
                        port: int = 8765, config_path: Path | None = None, browse=discovery.browse,
                        say=print) -> ControllerConfig:
    """Pair without an address: find relays (``relay`` if given, else mDNS, then ``hostname``) and pair with
    the one whose certificate has ``fingerprint``. Others are skipped before anything is sent to them."""
    fingerprint = discovery.normalize_fingerprint(fingerprint)
    candidates: list[Endpoint] = [relay] if relay is not None else []
    if relay is None:
        say("looking for the relay on this network…")
        candidates += await asyncio.to_thread(browse, fingerprint)
        if hostname:
            candidates.append(discovery.parse_endpoint(hostname, source="hostname", default_port=port))
    if not candidates:
        raise LinkFailure(
            "couldn't find the relay on this network. Check the app says the relay is listening, that this "
            "machine is on the network you approved for it, or pass --relay HOST:PORT (shown in the app)")
    problems = []
    for candidate in candidates:
        try:
            return await _pair_at(candidate, code, expected=fingerprint, config_path=config_path, hostname=hostname)
        except WrongRelay as exc:
            problems.append(f"{candidate}: {exc}")
        except (OSError, ssl.SSLError, websockets.InvalidHandshake, websockets.ConnectionClosed, TimeoutError) as exc:
            problems.append(f"{candidate}: can't connect ({exc})")
    raise LinkFailure("no relay with the fingerprint from the app answered:\n  " + "\n  ".join(problems))


async def _pair_at(endpoint: Endpoint, code: str, *, expected: str | None, config_path: Path | None,
                   hostname: str | None) -> ControllerConfig:
    nonce = secrets.token_hex(16)
    async with connect(
        _url(endpoint), ssl=_unverified_context(), max_size=MAX_MESSAGE_BYTES, open_timeout=10
    ) as ws:
        der = _peer_der(ws)
        fingerprint = cert_fingerprint(der)
        if expected is not None and fingerprint != expected:
            # Not the relay the dashboard named: it gets neither a proof nor anything else.
            raise WrongRelay(f"a relay with a different certificate ({display_fingerprint(fingerprint)[:23]}…)")
        await ws.send(Pair(
            lookup=code_lookup(code), nonce=nonce, proof=client_proof(code, fingerprint, nonce),
            controller_version=__version__,
        ).model_dump_json())
        try:
            reply = await _expect(ws, 15)
        except LinkFailure as exc:
            if str(exc) == "pairing_invalid":
                raise LinkFailure(
                    "the relay didn't accept that code: check it, or make a new one in the app "
                    "(codes expire after a few minutes and work once)"
                ) from None
            raise
    if not isinstance(reply, Paired):
        raise LinkFailure(f"unexpected reply {reply.type!r}")
    if not proofs_match(server_proof(code, fingerprint, nonce), reply.proof):
        # Whoever answered doesn't know the code: not our relay.
        raise LinkFailure("the relay could not prove it knows the code; not pairing (is something intercepting?)")
    config = ControllerConfig(
        relay_host=endpoint.host, relay_port=endpoint.port, environment_id=reply.environment_id, token=reply.token,
        relay_cert_pem=ssl.DER_cert_to_PEM_cert(der), relay_fingerprint=fingerprint, relay_hostname=hostname,
    )
    save_config(config, config_path or default_config_path())
    print(f"paired with environment {reply.environment_id} (relay at {endpoint})")
    print(f"relay certificate pinned: {display_fingerprint(fingerprint)}")
    return config


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


class _CaptureState:
    """What the relay last allowed, and what the capture loop last saw."""

    def __init__(self) -> None:
        self.config = CaptureConfig()
        self.target_state: TargetState = "capture_off"
        self.changed = asyncio.Event()

    def apply(self, config: CaptureConfig) -> bool:
        if config == self.config:
            return False
        self.config = config
        if config.gate != "allowed":
            self.target_state = "capture_off"
        self.changed.set()
        return True

    async def wait_for_change(self, timeout: float | None = None) -> None:
        try:
            await asyncio.wait_for(self.changed.wait(), timeout)
        except TimeoutError:
            pass
        self.changed.clear()


InputFactory = Callable[[Profile], GuardedBackend]
SamplerFactory = Callable[[Profile], Sampler]


class _Play:
    """The session side of a connection: one ``SessionRuntime`` at a time.

    Everything here is a reaction to the relay. Input is only possible while
    the latest heartbeat reply carried a grant for this session; anything else
    (no grant, a cancel, a stop, the connection ending) releases everything.
    """

    def __init__(self, input_factory: InputFactory | None, sampler_factory: SamplerFactory | None = None):
        self.input_factory = input_factory
        self.sampler_factory = sampler_factory
        self.session_id: str | None = None
        self.runtime: SessionRuntime | None = None
        self.pending: SessionStart | None = None
        self._told_missing = False
        # Set when a session starts, so the heartbeat loop drops its slow idle
        # wait at once instead of letting the first lease lapse.
        self.woke = asyncio.Event()

    @property
    def active(self) -> bool:
        return self.session_id is not None

    def start(self, message: SessionStart) -> list:
        if message.session_id == self.session_id and self.runtime is not None:
            return [self._ready()]  # the relay repeats starts until it hears "ready"
        if message.session_id != self.session_id:
            self.close()
            self.session_id, self._told_missing = message.session_id, False
            self.woke.set()
        try:
            profile = parse_profile(message.profile, source="relay")
        except ProfileError:
            return [SessionProblem(session_id=message.session_id, reason="profile_invalid")]
        if self.input_factory is None:
            return [SessionProblem(session_id=message.session_id, reason="no_input")]
        try:
            sampler = self.sampler_factory(profile) if self.sampler_factory is not None else None
            self.runtime = SessionRuntime(profile, self.input_factory(profile), sampler=sampler)
        except TargetLost:
            self.pending = message
            if self._told_missing:
                return []
            self._told_missing = True
            return [SessionProblem(session_id=message.session_id, reason="target_missing")]
        self.pending = None
        print(f"session {message.session_id}: ready ({profile.display_name})")
        return [self._ready()]

    def _ready(self) -> SessionReady:
        assert self.runtime is not None and self.session_id is not None
        return SessionReady(
            session_id=self.session_id, viewport=self.runtime.scheduler.viewport_size, context=self.runtime.context(),
        )

    def context(self) -> str | None:
        return self.runtime.context() if self.runtime is not None else None

    def retry(self) -> list:
        return self.start(self.pending) if self.pending is not None else []

    def control(self, grant: ControlGrant | None) -> None:
        if self.runtime is None:
            return
        if grant is not None and grant.session_id == self.session_id:
            self.runtime.grant(grant.generation, grant.lease_ms)
        else:
            self.runtime.revoke("no_control")

    def decision(self, message: DecisionCommand) -> None:
        if self.runtime is not None and message.session_id == self.session_id:
            self.runtime.apply(message.decision_id, message.decision, message.generation)

    def cancel(self, message: Cancel) -> None:
        if self.runtime is not None and message.session_id == self.session_id:
            self.runtime.revoke(message.reason)

    def stop(self, message: SessionStop) -> None:
        if message.session_id == self.session_id:
            self.close()

    def reports(self) -> list:
        if self.runtime is None or self.session_id is None:
            return []
        out: list = []
        for report in self.runtime.drain():
            if report.kind == "pause":
                print(f"session {self.session_id}: stopped ({report.reason})")
                out.append(PauseRequest(session_id=self.session_id, reason=report.reason or "controller"))
            else:
                out.append(DecisionReport(
                    session_id=self.session_id, decision_id=report.decision_id or "", state=report.state or "",
                    ended_by=report.ended_by, ran_ms=report.ran_ms, rejection_code=report.rejection_code,
                    rejection_reason=report.rejection_reason,
                    reflexes=[ReflexReport(name=hit.name, at_ms=hit.at_ms, acted=hit.acted) for hit in report.reflexes],
                    pursuit=report.pursuit,
                ))
        return out

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()
        self.runtime = None
        self.session_id = None
        self.pending = None


async def run_once(
    config: ControllerConfig,
    *,
    capture: CaptureBackend | None = None,
    input_factory: InputFactory | None = None,
    desktop: DesktopHands | None = None,
    security: SecurityCollector | None = None,
    on_welcome=None,
    endpoint: Endpoint | None = None,
    machine_status=None,
) -> None:
    """One connection, until it ends. Raises ``LinkFailure`` with the reason.

    Three loops share the socket: the reader (relay replies, refusals), the
    heartbeat, and capture. Capture runs only while the relay says
    ``allowed``; a frame grabbed under a setting that changed meanwhile is
    discarded, so "I'm signing in" wins over a grab already in progress.
    """
    url = _url(endpoint) if endpoint is not None else config.url
    async with connect(
        url, ssl=_pinned_context(config.relay_cert_pem), max_size=MAX_MESSAGE_BYTES, open_timeout=10
    ) as ws:
        await ws.send(Hello(
            environment_id=config.environment_id, token=config.token, controller_version=__version__,
        ).model_dump_json())
        welcome = await _expect(ws, 10)
        if not isinstance(welcome, Welcome):
            raise LinkFailure(f"unexpected reply {welcome.type!r}")
        print(f"connected (generation {welcome.controller_generation})")
        if on_welcome:
            on_welcome(welcome)
        state = _CaptureState()
        # The machine's status (capture, input, permissions, startup), for relays that ask for it.
        wants_status = machine_status is not None and "status" in (welcome.features or [])
        report: dict = {"sent": None, "next": None, "checked_at": 0.0}

        async def status_due() -> ControllerStatus | None:
            """The status to send with this beat: the first time, and whenever it has changed."""
            if not wants_status:
                return None
            if time.monotonic() - report["checked_at"] >= STATUS_CHECK_SECONDS or report["next"] is None:
                report["checked_at"] = time.monotonic()
                try:
                    report["next"] = await asyncio.to_thread(machine_status)
                except Exception:  # noqa: BLE001 - a failed check is reported next time
                    return None
            if report["next"] == report["sent"]:
                return None
            report["sent"] = report["next"]
            return report["next"]

        def sampler_for(profile: Profile) -> Sampler:
            """The reflexes' picture, under the same capture switch as everything
            else: none unless capture is allowed right now."""
            size = sample_size(profile)

            def take():
                if capture is None or state.config.gate != "allowed":
                    return None
                return capture.sample(profile.target.process, size)
            return take

        play = _Play(input_factory, sampler_for)

        async def send(data) -> None:
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                # The reader is about to read the relay's reason; let it raise.
                await asyncio.Future()

        async def reply(messages: list) -> None:
            for message in messages:
                await send(message.model_dump_json())

        # Security monitoring (the owner's own switch, carried by heartbeat replies): one batch in flight.
        watch: dict = {"config": None}
        watch_changed = asyncio.Event()
        acks: dict[str, asyncio.Future] = {}

        async def security_watch() -> None:
            if security is None:
                await asyncio.Future()  # no collector on this controller: nothing to do, ever
            while True:
                config: SecurityConfig | None = watch["config"]
                if config is None or not config.enabled:
                    security.configure(config or SecurityConfig())  # off: collect() reads nothing
                    watch_changed.clear()
                    await watch_changed.wait()
                    continue
                if security.configure(config):
                    print(f"security monitoring on (epoch {config.epoch}, {len(config.channels)} channel(s))")
                try:
                    batch = await asyncio.to_thread(security.collect)
                except Exception as exc:  # noqa: BLE001 - a bad read never ends the connection
                    print(f"security: read failed: {exc}", file=sys.stderr)
                    batch = None
                if batch is None:
                    await _quiet_wait(watch_changed, config.poll_seconds)
                    continue
                answer: asyncio.Future = asyncio.get_running_loop().create_future()
                acks[batch.batch_id] = answer
                try:
                    await send(batch.model_dump_json())
                    ack: SecurityAck | None = await asyncio.wait_for(answer, ACK_SECONDS)
                except TimeoutError:
                    ack = None  # read again next time: the relay deduplicates
                finally:
                    acks.pop(batch.batch_id, None)
                stored = ack is not None and ack.status == "stored"
                if ack is not None and not stored:
                    print(f"security: batch not stored ({ack.reason or 'refused'})", file=sys.stderr)
                await asyncio.to_thread(security.acknowledge, batch.batch_id, stored=stored)
                if stored and any(read.more for read in batch.channels):
                    await asyncio.sleep(0)  # more records waiting: read on at once
                    continue
                await _quiet_wait(watch_changed, config.poll_seconds)

        # General desktop control: one action at a time, each answered exactly once.
        desktop_lock = asyncio.Lock()
        desktop_tasks: set[asyncio.Task] = set()
        # Each action's worker thread: (stop, finished). Cancelling the awaiting task doesn't stop a thread, so a
        # closing connection sets ``stop`` (no more input, held input released) and waits for ``finished``.
        desktop_threads: set[tuple[threading.Event, threading.Event]] = set()

        async def desktop_action(message: DesktopAction) -> None:
            async with desktop_lock:
                result, jpeg = await _desktop_run(message)
                if jpeg is not None and result.screen is not None and result.status == "done":
                    if state.config.gate != "allowed":  # capture switched off while taking it
                        result = result.model_copy(update={"status": "refused", "screen": None,
                                                           "reason": "the user switched your view off"})
                    else:
                        width, height = result.screen.size
                        await send(pack_frame(FrameHeader(
                            seq=0, captured_ms=int(time.monotonic() * 1000), width=width, height=height,
                            gate_epoch=state.config.epoch, kind="screenshot", action_id=message.action_id,
                        ), jpeg))
                await send(result.model_dump_json())

        async def _desktop_run(message: DesktopAction) -> tuple[DesktopResult, bytes | None]:
            kind = message.action.get("kind") if isinstance(message.action, dict) else None
            refusal = None
            if desktop is None:
                refusal = "this controller has no desktop hands"
            elif state.config.gate != "allowed":
                refusal = ("the user is signing in on the desktop" if state.config.gate == "manual_auth"
                           else "the user has your view of the desktop switched off")
            elif play.active and kind in INPUT_KINDS:
                refusal = "a game session is using the keyboard and mouse"
            if refusal is not None:
                return DesktopResult(action_id=message.action_id, status="refused", reason=refusal), None
            stop, finished = threading.Event(), threading.Event()
            entry = (stop, finished)
            desktop_threads.add(entry)

            def work() -> tuple[DesktopResult, bytes | None]:
                try:
                    return desktop.run(message.action_id, message.action, stop)
                finally:
                    finished.set()

            result = await asyncio.to_thread(work)
            desktop_threads.discard(entry)  # only once it ended: a cancelled wait leaves it for the drain
            return result

        async def reader() -> None:
            while True:
                message = await _expect(ws, HEARTBEAT_TIMEOUT_SECONDS)
                if isinstance(message, SecurityAck):
                    waiting = acks.get(message.batch_id)
                    if waiting is not None and not waiting.done():
                        waiting.set_result(message)
                    continue
                if isinstance(message, DesktopAction):
                    task = asyncio.create_task(desktop_action(message))
                    desktop_tasks.add(task)
                    task.add_done_callback(desktop_tasks.discard)
                    continue
                if isinstance(message, SessionStart):
                    await reply(play.start(message))
                    continue
                if isinstance(message, DecisionCommand):
                    play.decision(message)
                    await reply(play.reports())
                    continue
                if isinstance(message, Cancel):
                    play.cancel(message)
                    continue
                if isinstance(message, SessionStop):
                    play.stop(message)
                    continue
                if not isinstance(message, HeartbeatAck):
                    raise LinkFailure("unexpected message from the relay")
                play.control(message.control)
                if message.security != watch["config"]:
                    if watch["config"] is not None and watch["config"].enabled and not (
                            message.security and message.security.enabled):
                        print("security monitoring off")
                    watch["config"] = message.security
                    watch_changed.set()
                if state.apply(message.capture) and message.capture.gate != "allowed":
                    # Confirm a stop right away instead of at the next beat. The
                    # capture loop checks the setting before and after every
                    # grab, so nothing is captured from here on.
                    await send(Heartbeat(
                        gate_epoch_seen=message.capture.epoch, target_state="capture_off",
                    ).model_dump_json(exclude_none=True))

        async def heartbeat() -> None:
            while True:
                # exclude_none: a relay older than 0.12 forbids the "status" key, even as null.
                await send(Heartbeat(
                    gate_epoch_seen=state.config.epoch, target_state=state.target_state, context=play.context(),
                    status=await status_due(),
                ).model_dump_json(exclude_none=True))
                await reply(play.retry() + play.reports())
                # Faster beats during a session keep the lease short (plan §19).
                play.woke.clear()
                try:
                    await asyncio.wait_for(
                        play.woke.wait(), HEARTBEAT_ACTIVE_SECONDS if play.active else HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    pass

        async def capturer() -> None:
            seq = 0
            while True:
                allowed = state.config
                if allowed.gate != "allowed" or (allowed.scope == "game_window" and not allowed.target_process):
                    state.target_state = "capture_off"
                    await state.wait_for_change()
                    continue
                if capture is None:
                    state.target_state = "error"  # this machine has no capture backend
                    await state.wait_for_change()
                    continue
                started = time.monotonic()
                grab = await asyncio.to_thread(capture.grab, allowed)
                if state.config != allowed:
                    continue  # the setting changed during the grab: discard it
                state.target_state = grab.state
                if grab.jpeg is not None:
                    seq += 1
                    header = FrameHeader(
                        seq=seq, captured_ms=int(started * 1000), width=grab.width, height=grab.height,
                        gate_epoch=allowed.epoch,
                    )
                    await send(pack_frame(header, grab.jpeg))
                if allowed.observe is not None:
                    # The driver's view: the game window at exactly the decision
                    # frame size, stamped with the input mode at capture.
                    shot_at = time.monotonic()
                    context = play.context()
                    seen = await asyncio.to_thread(capture.observe, allowed.observe)
                    if state.config == allowed and seen.jpeg is not None:
                        seq += 1
                        await send(pack_frame(FrameHeader(
                            seq=seq, captured_ms=int(shot_at * 1000), width=seen.width, height=seen.height,
                            gate_epoch=allowed.epoch, kind="observation", context=context,
                        ), seen.jpeg))
                await state.wait_for_change(max(0.0, 1 / allowed.fps - (time.monotonic() - started)))

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(reader())
                group.create_task(heartbeat())
                group.create_task(capturer())
                group.create_task(security_watch())
        except BaseExceptionGroup as group_error:
            # Report the relay's reason if there is one, else the first error.
            failures = [e for e in group_error.exceptions if isinstance(e, LinkFailure)]
            raise (failures or list(group_error.exceptions))[0] from None
        finally:
            for stop, _finished in desktop_threads:
                stop.set()  # its hands send nothing more and release what they hold
            for task in desktop_tasks:
                task.cancel()  # no result can be sent any more
            play.close()  # no connection, no control: release everything now
            # No reconnect while an action's thread is still touching the desktop.
            await _drain_desktop(desktop_threads)


async def _drain_desktop(threads: set[tuple[threading.Event, threading.Event]]) -> None:
    """Wait (bounded) until every desktop action's thread has ended, after its stop was set."""
    pending = [finished for _stop, finished in threads if not finished.is_set()]
    if not pending:
        return

    def wait_all() -> bool:
        deadline = time.monotonic() + DESKTOP_DRAIN_SECONDS
        return all(f.wait(max(0.0, deadline - time.monotonic())) for f in pending)

    if not await asyncio.shield(asyncio.to_thread(wait_all)):
        print(f"desktop: an action was still running {DESKTOP_DRAIN_SECONDS:.0f} s after the connection closed",
              file=sys.stderr)


async def _quiet_wait(event: asyncio.Event, seconds: float) -> None:
    """Wait ``seconds``, or less if the setting changes (then the change is taken: the event is cleared)."""
    try:
        await asyncio.wait_for(event.wait(), seconds)
    except TimeoutError:
        pass
    event.clear()


async def run_forever(
    config: ControllerConfig, *, capture: CaptureBackend | None = None, input_factory: InputFactory | None = None,
    desktop: DesktopHands | None = None, security: SecurityCollector | None = None,
    config_path: Path | None = None, status_path: Path | None = None, machine_status=None,
    browse=discovery.browse,
) -> int:
    """Reconnect with backoff until a refusal that retrying can't fix.

    Each attempt tries the last address that worked, then looks for the relay again (mDNS, then the DNS
    host name). Only an endpoint presenting the pinned certificate is ever talked to; the one that works is
    saved as the new last address. A revoked pairing is written into the pairing file, so the next login
    start stops at once instead of retrying forever."""
    attempt = 0
    current: dict = {"endpoint": config.endpoint}

    def connected(_welcome: Welcome) -> None:
        nonlocal attempt
        attempt = 0
        endpoint = current["endpoint"]
        write_status(status_path, "connected", relay=str(endpoint))
        if (endpoint.host, endpoint.port) != (config.relay_host, config.relay_port):
            config.relay_host, config.relay_port = endpoint.host, endpoint.port
            if config_path is not None:
                save_config(config, config_path)
            print(f"the relay is now at {endpoint}; saved")

    while True:
        problems: list[str] = []
        for endpoint in await _candidates(config, browse, first=attempt == 0):
            current["endpoint"] = endpoint
            write_status(status_path, "connecting", relay=str(endpoint))
            try:
                await run_once(config, capture=capture, input_factory=input_factory, desktop=desktop,
                               security=security, on_welcome=connected, endpoint=endpoint,
                               machine_status=machine_status)
            except LinkFailure as exc:
                reason = str(exc)
                if reason in FATAL:
                    print(f"stopped: {_explain(reason)}", file=sys.stderr)
                    write_status(status_path, "stopped", _explain(reason), relay=str(endpoint))
                    if reason in ENDED:
                        config.ended = reason
                        if config_path is not None:
                            save_config(config, config_path)
                    return 1
                print(f"disconnected: {reason}", file=sys.stderr)
                problems.append(f"disconnected: {reason}")
                break  # it was the relay: retry it first, after the delay
            except ssl.SSLCertVerificationError:
                # Something else answers there now (or the relay was reset): never talk to it.
                problems.append(f"{endpoint} has a different certificate than the relay this machine paired with")
                continue
            except (OSError, ssl.SSLError, websockets.InvalidHandshake, websockets.ConnectionClosed,
                    TimeoutError) as exc:
                problems.append(f"can't reach {endpoint}: {exc}")
                continue
            else:
                break
        detail = "; ".join(problems[-3:]) or "the relay didn't answer"
        if problems and all("different certificate" in p for p in problems):
            detail += (". If the relay was reset (new certificate), pair again from the app")
        print(detail, file=sys.stderr)
        write_status(status_path, "retrying", detail, relay=str(current["endpoint"]))
        delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
        attempt += 1
        await asyncio.sleep(delay)


async def _candidates(config: ControllerConfig, browse, *, first: bool) -> list[Endpoint]:
    """Where to try, in order: the last address that worked, then the relay's advertisement (only ones
    claiming the pinned fingerprint), then the host name. On the very first attempt the saved address is
    tried alone, so a normal start doesn't wait for discovery."""
    saved = [config.endpoint] if config.relay_host and config.relay_port else []
    if first and saved:
        return saved
    found = await asyncio.to_thread(browse, config.relay_fingerprint or None)
    named = []
    if config.relay_hostname:
        try:
            named = [discovery.parse_endpoint(config.relay_hostname, source="hostname",
                                              default_port=config.relay_port or 8765)]
        except ValueError:
            named = []
    out: list[Endpoint] = []
    for endpoint in saved + found + named:
        if all((e.host, e.port) != (endpoint.host, endpoint.port) for e in out):
            out.append(endpoint)
    return out


def _explain(code: str) -> str:
    return {
        "not_authorized": "this device's key isn't valid anymore; pair again from the app",
        "revoked": "access was revoked in the app",
        "superseded": "another controller connected for this environment",
        "unsupported_protocol": "the relay speaks a different version; update this controller",
    }.get(code, code)
