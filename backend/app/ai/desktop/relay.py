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

It carries health, preview frames, and (stage C) the session: the lease rides
every heartbeat reply, and decisions/cancels are forwarded from the API.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
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
from app.services import (
    desktop_commands, desktop_control, desktop_link, desktop_preview, desktop_sessions, security_activity,
)
from app.services.desktop_sessions import DesktopError
from app.services.desktop_link import LinkRefused
from desktop_controller.link import (
    HEARTBEAT_TIMEOUT_SECONDS,
    LEASE_MS,
    MAX_FRAME_BYTES,
    PROTOCOL,
    Cancel,
    CaptureConfig,
    ControlGrant,
    DecisionCommand,
    DecisionReport,
    DesktopAction,
    DesktopResult,
    Hello,
    HeartbeatAck,
    LinkError,
    Pair,
    Paired,
    PauseRequest,
    SecurityAck,
    SecurityBatch,
    SecurityConfig,
    SessionProblem,
    SessionReady,
    SessionStart,
    SessionStop,
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
    app = FastAPI(title="Jophiel desktop relay", docs_url=None, redoc_url=None, openapi_url=None)

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
        commands: asyncio.Task | None = None
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
            link = _SessionLink(ws, environment_id, db_call, controller_version=first.controller_version,
                                generation=generation)
            commands = asyncio.create_task(link.forward_commands())
            last_frame_at: dict[int, float] = {}
            last_heartbeat_at = time.monotonic()
            while True:
                # Timed from the last *heartbeat*: a stream of frames alone must
                # not keep a connection alive (heartbeats are where revoke is checked).
                remaining = HEARTBEAT_TIMEOUT_SECONDS - (time.monotonic() - last_heartbeat_at)
                if remaining <= 0:
                    raise TimeoutError
                message = await _receive(ws, remaining, allow_frames=True)
                if isinstance(message, bytes):
                    if _frame_kind(message) == 2:
                        # A desktop screenshot answers one action of hers: not rate limited,
                        # but only for an action this connection forwarded.
                        await link.accept_screenshot(db_call, generation, message)
                        continue
                    # Frames are best-effort: anything malformed, too fast, or
                    # taken under an outdated capture setting is dropped quietly.
                    # Rate-limited per kind: a preview and an observation arrive
                    # back to back.
                    kind = _frame_kind(message)
                    now = time.monotonic()
                    if now - last_frame_at.get(kind, 0.0) < MIN_FRAME_INTERVAL_SECONDS:
                        continue
                    last_frame_at[kind] = now
                    await _accept_frame(db_call, environment_id, generation, message)
                    continue
                if message.type != "heartbeat":
                    await link.handle(message)
                    continue
                last_heartbeat_at = time.monotonic()
                capture = await db_call(
                    desktop_link.heartbeat, environment_id, generation,
                    gate_epoch_seen=message.gate_epoch_seen, target_state=message.target_state,
                    context=message.context,
                )
                link.last_capture = capture
                if link.knows_security:
                    link.last_security = await db_call(security_activity.config_for, environment_id)
                control = await link.control()
                await link.send(link.ack(capture, control))
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
            if commands is not None:
                commands.cancel()
            if environment_id is not None and generation is not None:
                await db_call(desktop_link.disconnected, environment_id, generation, reason=reason)
                try:
                    await db_call(security_activity.mark_offline, environment_id, generation)
                except Exception:  # noqa: BLE001 - source health never blocks a disconnect
                    log.exception("desktop relay: marking security source offline failed")
                log.info("desktop relay: environment %s connection %s closed (%s)", environment_id, generation, reason)

    return app


# Profile sections a controller older than the version shown can't parse (its
# profile model forbids unknown keys, so the whole session would be refused).
# Left out for older controllers: the game still plays, just without them.
PROFILE_SECTIONS_SINCE = {"reflexes": (0, 8, 0)}
# Controllers that know security monitoring (older ones forbid the unknown field in a heartbeat reply).
SECURITY_SINCE = (0, 11, 0)
# Sections only the Mac uses (careful mode is enforced by the driver loop):
# never sent, so changing them can't break a VM's profile check.
MAC_ONLY_SECTIONS = {"careful"}


def _version(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.split(".")[:3])
    except ValueError:
        return (0,)


def profile_for(profile, controller_version: str) -> dict:
    version = _version(controller_version)
    old = {name for name, since in PROFILE_SECTIONS_SINCE.items() if version < since}
    return profile.model_dump(mode="json", exclude=old | MAC_ONLY_SECTIONS)


class _SessionLink:
    """One authenticated connection's session traffic (stage C).

    Tracks which session the controller was told to prepare, sends the lease
    with each heartbeat reply only while that session is playing, forwards
    decisions and cancels from the API, and acts on the controller's reports.
    A session id coming *from* the controller is honoured only if it is the
    one this relay started on this connection, so a controller can't act on
    another environment's session.
    """

    def __init__(self, ws: WebSocket, environment_id: uuid.UUID, db_call, controller_version: str = "",
                 generation: int = 0):
        self.ws = ws
        self.controller_version = controller_version
        self.generation = generation
        self.knows_security = _version(controller_version) >= SECURITY_SINCE
        self.last_security = SecurityConfig()
        self._tasks: set[asyncio.Task] = set()
        self.environment_id = environment_id
        self.db_call = db_call
        self.started: str | None = None
        self.ready: set[str] = set()
        self.last_capture = CaptureConfig()
        self._send_lock = asyncio.Lock()
        # Desktop actions forwarded on this connection and not answered yet (general desktop control).
        self.desktop_pending: set[str] = set()

    async def send(self, message) -> None:
        async with self._send_lock:  # heartbeat replies and forwarded commands share the socket
            await self.ws.send_text(message.model_dump_json(
                exclude={"security"} if isinstance(message, HeartbeatAck) and message.security is None else None))

    def ack(self, capture: CaptureConfig, control: ControlGrant | None) -> HeartbeatAck:
        """A heartbeat reply: capture, the lease, and (to controllers that know it) security monitoring."""
        return HeartbeatAck(capture=capture, control=control,
                            security=self.last_security if self.knows_security else None)

    async def security_batch(self, message: SecurityBatch) -> None:
        """Store one batch of her desktop's security records, from this connection only, and answer it."""
        if not self.knows_security:
            return
        try:
            status, reason = await self.db_call(security_activity.ingest, self.environment_id, self.generation,
                                                message)
        except Exception:  # noqa: BLE001 - not stored: the controller reads the same records again
            log.exception("desktop relay: security batch %s for %s failed", message.batch_id, self.environment_id)
            status, reason = "refused", "the backend couldn't store it; it will be read again"
        try:
            await self.send(SecurityAck(batch_id=message.batch_id, status=status, reason=reason))
        except Exception:  # noqa: BLE001 - the connection ended meanwhile: the controller reads them again
            pass

    async def control(self) -> ControlGrant | None:
        state = await self.db_call(desktop_sessions.control_state, self.environment_id)
        if state is None:
            if self.started is not None:
                await self.send(SessionStop(session_id=self.started))
                self.started = None
            return None
        session_id = str(state.session_id)
        if session_id not in self.ready:
            # Repeated each beat until the controller reports ready.
            profile = await asyncio.to_thread(desktop_sessions.get_profile, state.profile_id)
            self.started = session_id
            await self.send(SessionStart(session_id=session_id, profile=profile_for(profile, self.controller_version)))
            return None
        self.started = session_id
        if state.state == "running" and state.control_owner == "gameplay":
            return ControlGrant(session_id=session_id, generation=state.execution_generation, lease_ms=LEASE_MS)
        return None

    async def desktop_action(self, data: dict) -> None:
        """Forward one of her desktop actions, unless it is stale or input isn't hers to give now."""
        action_id = data.get("action_id", "")[:64]
        try:
            deliver_by = float(data.get("deliver_by") or 0)
            action = json.loads(data.get("action") or "")
        except ValueError:
            return
        if not action_id or not isinstance(action, dict):
            return
        if time.time() > deliver_by:
            return  # the caller has given up or will soon: never press late
        refusal = None
        if self.last_capture.gate != "allowed":
            refusal = "the user has your view of the desktop switched off"
        elif self.started is not None and action.get("kind") != "screenshot":
            refusal = "a game session is using the keyboard and mouse"
        if refusal is not None:
            await asyncio.to_thread(desktop_control.store_result, action_id,
                                    {"action_id": action_id, "status": "refused", "reason": refusal})
            return
        self.desktop_pending.add(action_id)
        if len(self.desktop_pending) > 64:
            self.desktop_pending.pop()
        await self.send(DesktopAction(action_id=action_id, action=action))

    async def desktop_result(self, message: DesktopResult) -> None:
        if message.action_id not in self.desktop_pending:
            return  # not an action this connection forwarded
        self.desktop_pending.discard(message.action_id)
        await asyncio.to_thread(desktop_control.store_result, message.action_id,
                                message.model_dump(mode="json", exclude={"type"}))

    async def accept_screenshot(self, db_call, generation: int, data: bytes) -> None:
        try:
            header, jpeg = unpack_frame(data)
        except ValueError:
            return
        if header.kind != "screenshot" or header.action_id not in self.desktop_pending:
            return
        if not await db_call(desktop_link.frame_allowed, self.environment_id, generation, header.gate_epoch):
            return  # taken under an older capture setting: dropped (its result then says the picture was lost)
        if not await asyncio.to_thread(desktop_preview.check_jpeg, header, jpeg):
            return
        await asyncio.to_thread(desktop_control.store_shot, header.action_id, jpeg)

    async def handle(self, message) -> None:
        if isinstance(message, DesktopResult):
            await self.desktop_result(message)
            return
        if isinstance(message, SecurityBatch):
            # Stored off the read loop, so heartbeats keep flowing while a big batch is written.
            task = asyncio.create_task(self.security_batch(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        session_id = getattr(message, "session_id", None)
        if session_id is None or session_id != self.started:
            return  # not the session this connection is serving: ignore
        if isinstance(message, SessionReady):
            self.ready.add(session_id)
            log.info("desktop relay: session %s ready (viewport %s, %s)", session_id, message.viewport, message.context)
            try:
                await self.db_call(desktop_sessions.controller_ready, uuid.UUID(session_id))
            except DesktopError:
                pass  # not "starting" any more (e.g. resumed later): nothing to do
            await self.push_control()
        elif isinstance(message, SessionProblem):
            log.info("desktop relay: session %s not ready: %s", session_id, message.reason)
            await asyncio.to_thread(desktop_commands.record_report, session_id, {"problem": message.reason})
        elif isinstance(message, DecisionReport):
            await asyncio.to_thread(
                desktop_commands.record_report, session_id, message.model_dump(exclude={"type", "session_id"}),
            )
        elif isinstance(message, PauseRequest):
            log.info("desktop relay: session %s paused by the controller (%s)", session_id, message.reason)
            await self.db_call(desktop_sessions.controller_pause, uuid.UUID(session_id), reason=message.reason)

    async def push_control(self) -> None:
        """Send the current lease now rather than at the next heartbeat, so
        input works the moment a session starts playing or is resumed."""
        await self.send(self.ack(self.last_capture, await self.control()))

    async def forward_commands(self) -> None:
        """Decisions and cancels the API publishes for this environment, sent
        on as they arrive. Only entries newer than this connection are read."""
        client = desktop_commands.async_client()
        if client is None:
            return
        key = desktop_commands.stream_key(self.environment_id)
        last = "$"
        try:
            while True:
                try:
                    result = await client.xread({key: last}, block=1000, count=20)
                except Exception:  # Redis down or restarting: retry, the lease still guards input
                    await asyncio.sleep(2)
                    continue
                for _, entries in result or []:
                    for entry_id, fields in entries:
                        last = entry_id
                        if fields.get(b"kind") == b"refresh":
                            await self.push_control()
                            continue
                        if fields.get(b"kind") == b"desktop_action":
                            await self.desktop_action({k.decode(): v.decode() for k, v in fields.items()})
                            continue
                        command = _command(fields)
                        if command is not None and command.session_id == self.started:
                            await self.send(command)
        finally:
            await client.aclose()


def _command(fields: dict) -> DecisionCommand | Cancel | None:
    data = {k.decode(): v.decode() for k, v in fields.items()}
    try:
        if data.get("kind") == "decision":
            return DecisionCommand(
                session_id=data["session_id"], decision_id=data["decision_id"],
                generation=int(data["generation"]), decision=json.loads(data["decision"]),
            )
        if data.get("kind") == "cancel":
            return Cancel(session_id=data["session_id"], reason=data.get("reason", "cancelled")[:48])
    except (KeyError, ValueError, ValidationError):
        return None
    return None


def _frame_kind(data: bytes) -> int:
    """Cheap pre-parse for rate limiting: 1 for observations, 2 for desktop screenshots, 0 otherwise."""
    head = data[:1100]
    if b'"kind":"screenshot"' in head:
        return 2
    return 1 if b'"kind":"observation"' in head else 0


async def _accept_frame(db_call, environment_id: uuid.UUID, generation: int, data: bytes) -> None:
    try:
        header, jpeg = unpack_frame(data)
    except ValueError:
        return
    if not await db_call(desktop_link.frame_allowed, environment_id, generation, header.gate_epoch):
        return
    if header.kind == "observation":
        # Only for a playing session, and only at the profile's observation size.
        if not await db_call(desktop_link.observation_wanted, environment_id, header.width, header.height):
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
