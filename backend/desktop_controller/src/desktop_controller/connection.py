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
from dataclasses import asdict, dataclass
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection, connect

from . import __version__
from .link import (
    HEARTBEAT_SECONDS,
    HEARTBEAT_TIMEOUT_SECONDS,
    Hello,
    Heartbeat,
    LinkError,
    Pair,
    Paired,
    Welcome,
    cert_fingerprint,
    client_proof,
    code_lookup,
    display_fingerprint,
    proofs_match,
    relay_message,
    server_proof,
)

MAX_MESSAGE_BYTES = 64 * 1024
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


async def run_once(config: ControllerConfig, *, on_welcome=None) -> None:
    """One connection, until it ends. Raises ``LinkFailure`` with the reason."""
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
        while True:
            try:
                await ws.send(Heartbeat().model_dump_json())
            except websockets.ConnectionClosed:
                await _expect(ws, 1)  # read the refusal the relay sent before closing
                raise
            await _expect(ws, HEARTBEAT_TIMEOUT_SECONDS)
            # Wait for the next beat while listening, so a refusal (revoked,
            # superseded) is acted on at once rather than on the next send.
            try:
                await _expect(ws, HEARTBEAT_SECONDS)
            except TimeoutError:
                continue
            raise LinkFailure("unexpected message from the relay")


async def run_forever(config: ControllerConfig) -> int:
    """Reconnect with backoff until a refusal that retrying can't fix."""
    attempt = 0

    def connected(_welcome: Welcome) -> None:
        nonlocal attempt
        attempt = 0

    while True:
        try:
            await run_once(config, on_welcome=connected)
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
