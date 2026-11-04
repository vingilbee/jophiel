"""Read the user's Hands and integrations for cognition without invoking them."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.integrations import registry
from app.ai.state.student_two import Capability
from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession
from app.services import game_pack


def for_user(db: Session, user_id: uuid.UUID) -> tuple[Capability, ...]:
    capabilities = []
    for provider in registry.all_providers():
        connection = provider.get_connection(db, user_id)
        capabilities.append(Capability(
            provider.name, provider.capabilities, provider.is_configured(),
            "account linked" if connection else "account not linked",
        ))

    environments = db.scalars(select(DesktopEnvironment).where(
        DesktopEnvironment.owner_user_id == user_id,
        DesktopEnvironment.revoked_at.is_(None),
    ).order_by(DesktopEnvironment.created_at)).all()
    sessions = db.scalars(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id,
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES),
    ).order_by(DesktopSession.started_at.desc())).all() if environments else ()
    enabled = get_settings().desktop_agent_enabled
    for environment in environments:
        pack = game_pack.is_pack(environment)
        if not pack and environment.paired_at is None:
            continue
        session = next((s for s in sessions if s.environment_id == environment.id), None)
        actions = ("Move, follow players, and use game skills",) if pack else (
            "View the desktop when preview is allowed and control supported games",
        )
        connection = {"online": "connected", "offline": "disconnected"}.get(environment.status, "unknown")
        capabilities.append(Capability(environment.display_name, actions, enabled, connection,
                                       session.state if session else "no active session"))
    return tuple(capabilities)
