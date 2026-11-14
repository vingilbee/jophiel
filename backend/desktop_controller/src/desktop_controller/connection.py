"""The guest side of the link: pair once, then stay connected (plan §19).

Runs inside the game VM. ``pair`` trusts nothing until the relay proves it
knows the pairing code (see ``link``), then pins the relay's certificate and
saves the device token. ``run`` only ever connects to that pinned certificate,
authenticates, and heartbeats. In stage B it sends no input at all.

The saved file holds a secret (the device token). It goes in the user's
profile with owner-only permissions where the OS supports them; revoking the
environment in the app makes it useless.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import ssl
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection, connect

from . import __version__
from .capture import CaptureBackend
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
# Refusals after which reconnecting cannot help.
FATAL = {"not_authorized", "revoked", "superseded", "unsupported_protocol"}


class LinkFailure(Exception):
    pass


@dataclass
class ControllerConfig:
    relay_host: str
    relay_port: int
    environment_id: str
    token: str
    relay_cert_pem: str

    @property
    def url(self) -> str:
        return f"wss://{self.relay_host}:{self.relay_port}/controller"


def default_config_path() -> Path:
    override = os.environ.get("DESKTOP_CONTROLLER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    return Path(base or Path.home()) / ("Columbina" if base else ".columbina") / "controller.json"


def save_config(config: ControllerConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)


def load_config(path: Path) -> ControllerConfig:
    try:
        return ControllerConfig(**json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise LinkFailure(f"not paired yet: no {path}; run `desktop-controller pair` first") from None


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


async def pair(host: str, port: int, code: str, *, config_path: Path | None = None) -> ControllerConfig:
    nonce = secrets.token_hex(16)
    async with connect(
        f"wss://{host}:{port}/controller", ssl=_unverified_context(), max_size=MAX_MESSAGE_BYTES, open_timeout=10
    ) as ws:
        der = _peer_der(ws)
        fingerprint = cert_fingerprint(der)
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
        relay_host=host, relay_port=port, environment_id=reply.environment_id, token=reply.token,
        relay_cert_pem=ssl.DER_cert_to_PEM_cert(der),
    )
    save_config(config, config_path or default_config_path())
    print(f"paired with environment {reply.environment_id}")
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
) -> None:
    """One connection, until it ends. Raises ``LinkFailure`` with the reason.

    Three loops share the socket: the reader (relay replies, refusals), the
    heartbeat, and capture. Capture runs only while the relay says
    ``allowed``; a frame grabbed under a setting that changed meanwhile is
    discarded, so "I'm signing in" wins over a grab already in progress.
    """
    async with connect(
        config.url, ssl=_pinned_context(config.relay_cert_pem), max_size=MAX_MESSAGE_BYTES, open_timeout=10
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
            return await asyncio.to_thread(desktop.run, message.action_id, message.action)

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
                    ).model_dump_json())

        async def heartbeat() -> None:
            while True:
                await send(Heartbeat(
                    gate_epoch_seen=state.config.epoch, target_state=state.target_state, context=play.context(),
                ).model_dump_json())
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
            for task in desktop_tasks:
                task.cancel()  # its hands release what they hold; no result can be sent any more
            play.close()  # no connection, no control: release everything now


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
) -> int:
    """Reconnect with backoff until a refusal that retrying can't fix."""
    attempt = 0

    def connected(_welcome: Welcome) -> None:
        nonlocal attempt
        attempt = 0

    while True:
        try:
            await run_once(config, capture=capture, input_factory=input_factory, desktop=desktop,
                           security=security, on_welcome=connected)
        except LinkFailure as exc:
            reason = str(exc)
            if reason in FATAL:
                print(f"stopped: {_explain(reason)}", file=sys.stderr)
                return 1
            print(f"disconnected: {reason}", file=sys.stderr)
        except (OSError, ssl.SSLError, websockets.InvalidHandshake, websockets.ConnectionClosed, TimeoutError) as exc:
            if isinstance(exc, ssl.SSLCertVerificationError):
                print("stopped: the relay's certificate changed; pair again", file=sys.stderr)
                return 1
            print(f"can't reach the relay: {exc}", file=sys.stderr)
        delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
        attempt += 1
        await asyncio.sleep(delay)


def _explain(code: str) -> str:
    return {
        "not_authorized": "this device's key isn't valid anymore; pair again",
        "revoked": "access was revoked in the app",
        "superseded": "another controller connected for this environment",
        "unsupported_protocol": "the relay speaks a different version; update this controller",
    }.get(code, code)
