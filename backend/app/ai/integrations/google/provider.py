"""Registry entry for the Google integration.

Adapts the Google-specific OAuth/storage modules to the generic
``IntegrationProvider`` shape so the integrations API can list and manage it
without knowing anything about Google.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.integrations.google import oauth
from app.ai.integrations.registry import (
    Connection,
    IntegrationProvider,
    register,
)
from app.services import google_account as account_service


def _get_connection(db: Session, user_id: uuid.UUID) -> Connection | None:
    account = account_service.get_for_user(db, user_id=user_id)
    if account is None:
        return None
    return Connection(
        account_label=account.email,
        connected_at=account.created_at,
        scopes=tuple(sorted(account.granted_scopes())),
    )


def _authorize_url(user_id: uuid.UUID) -> str:
    return oauth.build_authorization_url(state=oauth.build_state(user_id))


async def _disconnect(db: Session, user_id: uuid.UUID) -> None:
    account = account_service.get_for_user(db, user_id=user_id)
    if account is None:
        return
    refresh_token = account_service.read_refresh_token(account)
    if refresh_token:
        # Best-effort: we drop our copy either way (see oauth.revoke).
        await oauth.revoke(refresh_token)
    account_service.delete_for_user(db, user_id=user_id)


GOOGLE = register(
    IntegrationProvider(
        id="google",
        name="Google",
        description=(
            "Connect Gmail, Drive, Docs, and Slides. Create documents and "
            "presentations, organize files, and download PDFs in chat."
        ),
        category="Workspace",
        capabilities=(
            "Create and edit Google Docs and Slides",
            "Find, upload, copy, move, rename, and trash Drive files",
            "Manage folders and file sharing",
            "Export PDF, Word, and PowerPoint files to chat",
            "Search and read your mail",
            "Draft replies and new emails",
            "Send email after you approve it",
            "Organize with labels, archive, and trash",
        ),
        is_configured=oauth.is_configured,
        get_connection=_get_connection,
        build_authorize_url=_authorize_url,
        disconnect=_disconnect,
    )
)
