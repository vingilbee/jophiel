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
from dataclasses import asdict, dataclass
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection, connect

from . import __version__
from .capture import CaptureBackend
from .link import (
    MAX_FRAME_BYTES,
    HEARTBEAT_SECONDS,
    HEARTBEAT_TIMEOUT_SECONDS,
    CaptureConfig,
    FrameHeader,
    Hello,
    Heartbeat,
    HeartbeatAck,
    LinkError,
    Pair,
    Paired,
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


async def run_once(config: ControllerConfig, *, capture: CaptureBackend | None = None, on_welcome=None) -> None:
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

        async def send(data) -> None:
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                # The reader is about to read the relay's reason; let it raise.
                await asyncio.Future()

        async def reader() -> None:
            while True:
                message = await _expect(ws, HEARTBEAT_TIMEOUT_SECONDS)
                if not isinstance(message, HeartbeatAck):
                    raise LinkFailure("unexpected message from the relay")
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
                    gate_epoch_seen=state.config.epoch, target_state=state.target_state,
                ).model_dump_json())
                await asyncio.sleep(HEARTBEAT_SECONDS)

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
                await state.wait_for_change(max(0.0, 1 / allowed.fps - (time.monotonic() - started)))

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(reader())
                group.create_task(heartbeat())
                group.create_task(capturer())
        except BaseExceptionGroup as group_error:
            # Report the relay's reason if there is one, else the first error.
            failures = [e for e in group_error.exceptions if isinstance(e, LinkFailure)]
            raise (failures or list(group_error.exceptions))[0] from None


async def run_forever(config: ControllerConfig, *, capture: CaptureBackend | None = None) -> int:
    """Reconnect with backoff until a refusal that retrying can't fix."""
    attempt = 0

    def connected(_welcome: Welcome) -> None:
        nonlocal attempt
        attempt = 0

    while True:
        try:
            await run_once(config, capture=capture, on_welcome=connected)
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
