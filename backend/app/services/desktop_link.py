"""Pairing a game VM and authenticating its controller (plan §10, §18-19).

Two callers with different powers, kept apart on purpose:

- **The user** (cookie-authenticated API) can create a pairing code for an
  environment they own. That is all: they can't act as a controller.
- **The relay** (on behalf of a controller holding no user cookie) can redeem
  a code, authenticate with a device token, and report heartbeats or a lost
  connection. A controller can never list conversations, start sessions,
  pick another environment, or grant itself anything.

Every connection gets a fresh ``controller_generation``. The relay re-checks
it on each heartbeat, which is how a revoke, a re-pair, or a newer connection
cuts an old socket off within one heartbeat, without the API and the relay
having to talk to each other directly.
"""
from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DesktopEnvironment, DesktopPairing
from app.services.desktop_sessions import (
    DesktopError,
    _owned_environment,
    controller_offline,
    disconnect_sessions_locked,
)
from desktop_controller.link import (
    Hello,
    Pair,
    client_proof,
    code_lookup,
    new_pairing_code,
    proofs_match,
    server_proof,
    token_hash,
)


class LinkRefused(Exception):
    """The relay sends ``code`` to the controller and closes the connection."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NewPairing:
    pairing: DesktopPairing
    code: str  # shown to the user once; never returned again


@dataclass(frozen=True)
class Redeemed:
    environment_id: uuid.UUID
    token: str
    proof: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# User side
# --------------------------------------------------------------------------- #


def create_pairing(db: Session, owner_user_id: uuid.UUID, environment_id: uuid.UUID) -> NewPairing:
    """A fresh single-use code. Any earlier unused code for this environment dies."""
    settings = get_settings()
    env = _owned_environment(db, owner_user_id, environment_id, lock=True)
    if env.revoked_at is not None:
        raise DesktopError("not_authorized", 403, "this environment's access was revoked; add a new one")
    for old in db.scalars(
        select(DesktopPairing).where(DesktopPairing.environment_id == env.id, DesktopPairing.consumed_at.is_(None))
    ):
        old.code = None
        old.expires_at = _now()
    code = new_pairing_code()
    pairing = DesktopPairing(
        owner_user_id=owner_user_id,
        environment_id=env.id,
        code_lookup=code_lookup(code),
        code=code.replace("-", ""),
        expires_at=_now() + timedelta(seconds=settings.desktop_pairing_ttl_seconds),
    )
    db.add(pairing)
    db.commit()
    return NewPairing(pairing, code)


# --------------------------------------------------------------------------- #
# Relay side
# --------------------------------------------------------------------------- #


def redeem_pairing(db: Session, message: Pair, *, relay_fingerprint: str) -> Redeemed:
    """Check the controller's proof and issue a device token.

    Every refusal looks the same to the controller (``pairing_invalid``), so it
    can't tell an unknown code from an expired or used one. A wrong proof
    counts against the code; enough of them kill it.
    """
    settings = get_settings()
    found = db.scalar(select(DesktopPairing).where(DesktopPairing.code_lookup == message.lookup))
    if found is None:
        raise LinkRefused("pairing_invalid")
    # Lock order: environment, then pairing (as revoke and create do).
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == found.environment_id).with_for_update())
    pairing = db.scalar(select(DesktopPairing).where(DesktopPairing.id == found.id).with_for_update())
    usable = (
        env is not None
        and pairing is not None
        and env.revoked_at is None
        and pairing.consumed_at is None
        and pairing.code is not None
        and pairing.expires_at > _now()
        and pairing.failed_attempts < settings.desktop_pairing_max_attempts
    )
    if not usable:
        db.commit()
        raise LinkRefused("pairing_invalid")
    if not proofs_match(client_proof(pairing.code, relay_fingerprint, message.nonce), message.proof):
        # Most likely a typo, or a machine in the middle relaying the proof
        # under its own certificate.
        pairing.failed_attempts += 1
        if pairing.failed_attempts >= settings.desktop_pairing_max_attempts:
            pairing.code = None
        db.commit()
        raise LinkRefused("pairing_invalid")

    proof = server_proof(pairing.code, relay_fingerprint, message.nonce)
    token = secrets.token_urlsafe(32)
    pairing.consumed_at = _now()
    pairing.code = None
    env.controller_token_hash = token_hash(token)
    env.paired_at = _now()
    env.controller_version = message.controller_version
    # A re-pair locks out the previous controller (its next heartbeat fails).
    env.controller_generation += 1
    if env.status == "online":
        env.status = "offline"
        disconnect_sessions_locked(db, env.id, reason="controller_replaced", states={"running"})
    db.commit()
    return Redeemed(env.id, token, proof)


def authenticate(db: Session, message: Hello) -> tuple[uuid.UUID, int]:
    """Accept a paired controller's connection: a new generation, online.

    A newer connection replaces an older one. Sessions that were running
    under the old one become ``disconnected``: control belonged to that
    connection, and nothing resumes on its own.
    """
    try:
        environment_id = uuid.UUID(message.environment_id)
    except ValueError:
        raise LinkRefused("not_authorized") from None
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == environment_id).with_for_update())
    stored = env.controller_token_hash if env is not None else None
    if (
        env is None
        or env.revoked_at is not None
        or stored is None
        or not hmac.compare_digest(stored, token_hash(message.token))
    ):
        db.commit()
        raise LinkRefused("not_authorized")
    if env.status == "online":
        disconnect_sessions_locked(db, env.id, reason="controller_replaced", states={"running"})
    env.controller_generation += 1
    env.status = "online"
    env.last_heartbeat_at = _now()
    env.controller_version = message.controller_version
    db.commit()
    return env.id, env.controller_generation


def heartbeat(db: Session, environment_id: uuid.UUID, controller_generation: int) -> None:
    """Record a heartbeat, or refuse if this connection is no longer the
    environment's current one (revoked, re-paired, or replaced)."""
    result = db.execute(
        update(DesktopEnvironment)
        .where(
            DesktopEnvironment.id == environment_id,
            DesktopEnvironment.controller_generation == controller_generation,
            DesktopEnvironment.revoked_at.is_(None),
        )
        .values(last_heartbeat_at=_now(), status="online")
    )
    db.commit()
    if result.rowcount == 1:
        return
    revoked = db.scalar(select(DesktopEnvironment.revoked_at).where(DesktopEnvironment.id == environment_id))
    raise LinkRefused("revoked" if revoked is not None else "superseded")


def disconnected(db: Session, environment_id: uuid.UUID, controller_generation: int, *, reason: str) -> None:
    """The connection ended. Only the current connection can mark the
    environment offline."""
    controller_offline(db, environment_id, reason=reason, controller_generation=controller_generation)


def mark_all_offline(db: Session) -> int:
    """Relay startup: nothing can be connected to a relay that just started."""
    ids = list(db.scalars(select(DesktopEnvironment.id).where(DesktopEnvironment.status == "online")))
    for environment_id in ids:
        controller_offline(db, environment_id, reason="relay_restarted")
    return len(ids)
