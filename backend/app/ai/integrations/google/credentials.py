"""Turn a stored Google grant into a usable access token.

This is the single entry point tool code should use: "give me a live access
token for this user, or tell me there isn't one." It transparently refreshes
expired tokens and self-heals when a grant has been revoked (deleting the dead
row so the UI reports "not connected" instead of failing forever).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.integrations.google import oauth, scopes as google_scopes
from app.services import google_account as account_service

logger = logging.getLogger(__name__)

# Refresh a little before actual expiry so a token can't die mid-request.
_EXPIRY_SKEW_SECONDS = 120


def _still_valid(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    # Rows written before a timezone-aware default would compare naive; treat
    # naive timestamps as UTC rather than raising.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    return remaining > _EXPIRY_SKEW_SECONDS


async def get_access_token(db: Session, *, user_id: uuid.UUID) -> str | None:
    """A valid Google access token for the user, or None if unavailable.

    None covers every "can't act on their behalf" case — not connected, grant
    revoked, encryption key rotated, insufficient scopes — because callers all
    react the same way: skip the integration for this turn.
    """
    account = account_service.get_for_user(db, user_id=user_id)
    if account is None:
        return None

    if not google_scopes.has_required_scopes(account.granted_scopes()):
        logger.info(
            "Google account for user %s lacks Gmail or Drive scopes; skipping.",
            user_id,
        )
        return None

    cached = account_service.read_access_token(account)
    if cached and _still_valid(account.access_token_expires_at):
        return cached

    refresh_token = account_service.read_refresh_token(account)
    if refresh_token is None:
        logger.warning(
            "Could not decrypt the Google refresh token for user %s "
            "(encryption key rotated?). Dropping the grant.",
            user_id,
        )
        account_service.delete_for_user(db, user_id=user_id)
        return None

    try:
        tokens = await oauth.refresh_access_token(refresh_token)
    except oauth.GoogleAuthRevoked:
        logger.info("Google grant for user %s was revoked; dropping it.", user_id)
        account_service.delete_for_user(db, user_id=user_id)
        return None
    except oauth.GoogleOAuthError:
        # Transient (network, Google 5xx). Keep the grant; retry next turn.
        logger.exception("Failed to refresh the Google access token for user %s", user_id)
        return None

    account_service.store_access_token(
        db,
        account=account,
        access_token=tokens.access_token,
        expires_at=tokens.expires_at,
    )
    return tokens.access_token
