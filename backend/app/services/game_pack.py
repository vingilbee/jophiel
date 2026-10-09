"""Game packs in the session system (stage G, phase 3).

A game pack is her own player in a game, driven by skills (game_packs/minecraft).
To the session service it is one more environment, ``hosting_kind="game_pack"``,
so starting, pausing, taking over, ending, plans and autonomy work exactly as
for the game VM. What the relay does for a VM, ``sync`` does for a pack, from
the desktop worker's poll:

- the environment is ``online`` while her player is in the world (pack health);
- a session waiting in ``starting`` becomes ``running`` once she's in the world;
- going offline disconnects live sessions (nothing resumes on its own);
- a skill her hands started keeps running only while its session plays: any
  stop (pause, takeover, end, disconnect) stops it. Skills someone ran by
  hand (``source: "user"``) are never touched.

The pack is one Minecraft account on this machine, so it belongs to one user:
``MINECRAFT_PACK_OWNER_EMAIL``.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession, User

log = logging.getLogger(__name__)

PACK_PROFILE_ID = "minecraft_pack"


@dataclass(frozen=True)
class PackProfile:
    """A pack's stand-in for a game profile: the session service only needs
    these fields. The pack itself holds the real rules (its skill manifest)."""

    id: str
    revision: int
    display_name: str


PACK_PROFILES = {PACK_PROFILE_ID: PackProfile(PACK_PROFILE_ID, 1, "Minecraft (her own player)")}

# Games a pack takes over from the VM: with the pack, Minecraft is played as her own player only.
# The VM path stays for every other game (and for users without the pack).
REPLACED_BY = {"minecraft_java": PACK_PROFILE_ID}


def is_pack_profile(profile_id: str | None) -> bool:
    return profile_id in PACK_PROFILES


def is_pack(env: DesktopEnvironment | None) -> bool:
    return env is not None and env.hosting_kind == "game_pack"


def has_pack(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(DesktopEnvironment.id).where(
        DesktopEnvironment.owner_user_id == user_id, DesktopEnvironment.hosting_kind == "game_pack",
        DesktopEnvironment.revoked_at.is_(None),
    )) is not None


def replacement(db: Session, user_id: uuid.UUID, profile_id: str | None) -> str | None:
    """The pack profile that replaces ``profile_id`` for this user, if any."""
    pack_id = REPLACED_BY.get(profile_id or "")
    return pack_id if pack_id and has_pack(db, user_id) else None


def ensure_environment(db: Session, user_id: uuid.UUID) -> DesktopEnvironment | None:
    """The user's Minecraft pack environment, created on first use. None if
    the pack isn't configured for this user."""
    owner = get_settings().minecraft_pack_owner_email.strip().lower()
    if not owner:
        return None
    email = db.scalar(select(User.email).where(User.id == user_id))
    if email is None or email.strip().lower() != owner:
        return None
    env = db.scalar(select(DesktopEnvironment).where(
        DesktopEnvironment.owner_user_id == user_id, DesktopEnvironment.hosting_kind == "game_pack",
        DesktopEnvironment.revoked_at.is_(None),
    ))
    if env is None:
        env = DesktopEnvironment(
            owner_user_id=user_id, display_name="Minecraft pack", hosting_kind="game_pack",
            allowed_profile_ids=[PACK_PROFILE_ID], status="offline",
            # Nothing to pair: the pack is local and authenticated by its token.
            paired_at=datetime.now(timezone.utc),
        )
        db.add(env)
        db.commit()
    return env


# --------------------------------------------------------------------------- #
# The worker's link to the pack
# --------------------------------------------------------------------------- #


def sync(db: Session, client) -> None:
    """One pass of the link (desktop worker poll, about once a second)."""
    from app.ai.desktop.pack_client import PackError
    from app.services import desktop_sessions as sessions

    envs = list(db.scalars(select(DesktopEnvironment).where(
        DesktopEnvironment.hosting_kind == "game_pack", DesktopEnvironment.revoked_at.is_(None),
    )))
    db.commit()
    if not envs:
        return
    try:
        health = client.health()
        online = bool(health.get("spawned"))
    except PackError:
        online = False

    for env in envs:
        if online and env.status != "online":
            sessions.controller_online(db, env.id, controller_generation=env.controller_generation + 1)
            log.info("game pack %s: in the world", env.id)
        elif not online and env.status == "online":
            sessions.controller_offline(db, env.id, reason="pack_offline")
            log.info("game pack %s: not in the world", env.id)

        live = list(db.scalars(select(DesktopSession).where(
            DesktopSession.environment_id == env.id, DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES),
        )))
        db.commit()
        if online:
            for session in live:
                if session.state == "starting":
                    try:
                        sessions.controller_ready(db, session.id)
                    except sessions.DesktopError:
                        db.rollback()
        playing = any(s.state == "running" and s.control_owner == "gameplay" for s in live)
        if online and not playing:
            stop_driver_skill(client)


def stop_driver_skill(client=None) -> bool:
    """Stop the running skill if her hands started it. Best effort; True if one was stopped."""
    from app.ai.desktop.pack_client import PackClient, PackError

    client = client or PackClient.from_settings()
    try:
        current = client.current()
        if current and current.get("source") == "driver":
            client.stop()
            return True
    except PackError:
        pass
    return False
