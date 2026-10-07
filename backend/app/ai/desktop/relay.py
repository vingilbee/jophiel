"""The controller relay: where the game VM's controller connects (plan §5.1, §19).

A separate process on its own port, so the main API (port 8000) can stay on
127.0.0.1 while only this listens on the LAN::

    DESKTOP_AGENT_ENABLED=true DESKTOP_CONTROLLER_BIND_HOST=192.168.1.76 \\
        uv run python -m app.ai.desktop.relay

It speaks TLS only (a plain connection fails the handshake) and nothing but
``desktop_controller.link`` messages. A connection must pair or authenticate
with its first message, within a few seconds, or it is closed. Afterwards it
must heartbeat; each heartbeat re-checks the database, so a revoke, a re-pair
or a newer connection ends this one within one heartbeat.

Stage B carries health and preview frames. Leases and decisions arrive in stage C.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import uuid
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.desktop.tls import ensure_relay_certificate
from app.core.config import get_settings
from app.services import desktop_link, desktop_preview
from app.services.desktop_link import LinkRefused
from desktop_controller.link import (
    HEARTBEAT_TIMEOUT_SECONDS,
    MAX_FRAME_BYTES,
    PROTOCOL,
    Hello,
    HeartbeatAck,
    LinkError,
    Pair,
    Paired,
    Welcome,
    controller_message,
    unpack_frame,
)

log = logging.getLogger(__name__)

FIRST_MESSAGE_SECONDS = 10.0
# Text (control) messages; frames are binary and bounded by MAX_FRAME_BYTES.
MAX_MESSAGE_BYTES = 64 * 1024
# At most 4 frames a second per connection, whatever the controller sends.
MIN_FRAME_INTERVAL_SECONDS = 0.25

SessionFactory = Callable[[], Session]


def create_relay_app(session_factory: SessionFactory, fingerprint: str) -> FastAPI:
    app = FastAPI(title="Columbina desktop relay", docs_url=None, redoc_url=None, openapi_url=None)

    async def db_call(fn, *args, **kwargs):
        def run():
            with session_factory() as db:
                return fn(db, *args, **kwargs)
        return await asyncio.to_thread(run)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "protocol": PROTOCOL}

    @app.websocket("/controller")
    async def controller(ws: WebSocket) -> None:
        await ws.accept()
        environment_id: uuid.UUID | None = None
        generation: int | None = None
        reason = "controller_lost"
        try:
            first = await _receive(ws, FIRST_MESSAGE_SECONDS)
            if isinstance(first, Pair):
                redeemed = await db_call(desktop_link.redeem_pairing, first, relay_fingerprint=fingerprint)
                await ws.send_text(Paired(
                    environment_id=str(redeemed.environment_id), token=redeemed.token, proof=redeemed.proof,
                ).model_dump_json())
                log.info("desktop relay: paired environment %s", redeemed.environment_id)
                await ws.close()
                return
            if not isinstance(first, Hello):
                raise LinkRefused("bad_message")
            environment_id, generation = await db_call(desktop_link.authenticate, first)
            log.info("desktop relay: environment %s online (generation %s)", environment_id, generation)
            await ws.send_text(Welcome(controller_generation=generation).model_dump_json())
            last_frame_at = 0.0
            last_heartbeat_at = time.monotonic()
            while True:
                # Timed from the last *heartbeat*: a stream of frames alone must
                # not keep a connection alive (heartbeats are where revoke is checked).
                remaining = HEARTBEAT_TIMEOUT_SECONDS - (time.monotonic() - last_heartbeat_at)
                if remaining <= 0:
                    raise TimeoutError
                message = await _receive(ws, remaining, allow_frames=True)
                if isinstance(message, bytes):
                    # Frames are best-effort: anything malformed, too fast, or
                    # taken under an outdated capture setting is dropped quietly.
                    now = time.monotonic()
                    if now - last_frame_at < MIN_FRAME_INTERVAL_SECONDS:
                        continue
                    last_frame_at = now
                    await _accept_frame(db_call, environment_id, generation, message)
                    continue
                if message.type != "heartbeat":
                    raise LinkRefused("bad_message")
                last_heartbeat_at = time.monotonic()
                capture = await db_call(
                    desktop_link.heartbeat, environment_id, generation,
                    gate_epoch_seen=message.gate_epoch_seen, target_state=message.target_state,
                )
                await ws.send_text(HeartbeatAck(capture=capture).model_dump_json())
        except LinkRefused as refused:
            reason = {"superseded": "controller_replaced", "revoked": "environment_revoked"}.get(
                refused.code, "controller_lost"
            )
            await _refuse(ws, refused.code)
        except TimeoutError:
            reason = "heartbeat_timeout"
            await _refuse(ws, "heartbeat_timeout" if generation is not None else "bad_message")
        except WebSocketDisconnect:
            pass
        finally:
            if environment_id is not None and generation is not None:
                await db_call(desktop_link.disconnected, environment_id, generation, reason=reason)
                log.info("desktop relay: environment %s connection %s closed (%s)", environment_id, generation, reason)

    return app


async def _accept_frame(db_call, environment_id: uuid.UUID, generation: int, data: bytes) -> None:
    try:
        header, jpeg = unpack_frame(data)
    except ValueError:
        return
    if not await db_call(desktop_link.frame_allowed, environment_id, generation, header.gate_epoch):
        return
    if not await asyncio.to_thread(desktop_preview.check_jpeg, header, jpeg):
        return
    await asyncio.to_thread(desktop_preview.store, environment_id, header, jpeg, generation=generation)


async def _receive(ws: WebSocket, timeout: float, *, allow_frames: bool = False):
    message = await asyncio.wait_for(ws.receive(), timeout)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    if message.get("bytes") is not None:
        if not allow_frames:
            raise LinkRefused("bad_message")
        return message["bytes"]
    raw = message.get("text") or ""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise LinkRefused("bad_message")
    try:
        message = controller_message.validate_json(raw)
    except ValidationError:
        protocol_mismatch = '"protocol"' in raw and PROTOCOL not in raw
        raise LinkRefused("unsupported_protocol" if protocol_mismatch else "bad_message") from None
    return message


async def _refuse(ws: WebSocket, code: str) -> None:
    try:
        await ws.send_text(LinkError(code=code).model_dump_json())
        await ws.close(code=1008)
    except Exception:  # the socket may already be gone
        pass


def check_bind_host(host: str) -> None:
    """Refuse wildcard binds: the relay belongs on one LAN address (plan §5.1)."""
    try:
        if ipaddress.ip_address(host).is_unspecified:
            raise SystemExit(
                f"refusing to bind the desktop relay to {host}: set DESKTOP_CONTROLLER_BIND_HOST "
                "to this Mac's LAN address (e.g. 192.168.1.76), or leave it at 127.0.0.1"
            )
    except ValueError:
        pass  # a host name; uvicorn resolves it


def main() -> None:
    from app.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.desktop_agent_enabled:
        raise SystemExit("the desktop agent is off; set DESKTOP_AGENT_ENABLED=true to run the relay")
    check_bind_host(settings.desktop_controller_bind_host)
    cert = ensure_relay_certificate()
    with SessionLocal() as db:
        stale = desktop_link.mark_all_offline(db)
    if stale:
        log.info("desktop relay: marked %d environment(s) offline from a previous run", stale)
    log.info("desktop relay: certificate fingerprint sha256 %s", cert.fingerprint)
    uvicorn.run(
        create_relay_app(SessionLocal, cert.fingerprint),
        host=settings.desktop_controller_bind_host,
        port=settings.desktop_controller_port,
        ssl_certfile=str(cert.cert_path),
        ssl_keyfile=str(cert.key_path),
        ws_max_size=MAX_FRAME_BYTES,
        proxy_headers=False,
        server_header=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
