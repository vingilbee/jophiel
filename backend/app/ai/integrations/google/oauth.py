"""Google OAuth 2.0 authorization-code flow — pure protocol, no database.

Deliberately implemented against the raw endpoints with ``httpx`` rather than
``google-auth-oauthlib``: the flow is four HTTP calls, and doing it directly
keeps everything async (we're inside FastAPI) and avoids pulling three Google
SDKs in for what amounts to a few form posts.

Persistence lives in ``app.services.google_account``; the "give me a usable
access token for this user" helper lives in ``credentials.py``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.ai.integrations.google import scopes as google_scopes
from app.core.config import get_settings
from app.core.crypto import sign_value, verify_value

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

_HTTP_TIMEOUT = httpx.Timeout(15.0)


class GoogleOAuthError(RuntimeError):
    """A token exchange/refresh failed, or the integration isn't configured."""


class GoogleAuthRevoked(GoogleOAuthError):
    """The stored refresh token is no longer valid — the user must reconnect.

    Google signals this with ``error=invalid_grant``: the grant was revoked in
    the user's account settings, the password changed, or it expired from
    disuse. Distinct from a transient failure because the fix is different —
    delete the stored grant and prompt for a fresh consent.
    """


@dataclass(frozen=True)
class GoogleTokens:
    """The useful parts of a Google token response."""

    access_token: str
    expires_at: datetime
    # Only present on the *first* exchange (and only because we ask for
    # `access_type=offline` + `prompt=consent`). Refreshes don't return one, so
    # the caller must keep the original.
    refresh_token: str | None
    scopes: set[str]


def is_configured() -> bool:
    """True when a client id/secret are present, so the flow can be started."""
    settings = get_settings()
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def _require_client() -> tuple[str, str]:
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise GoogleOAuthError(
            "Google OAuth is not configured — set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET in .env."
        )
    return settings.google_oauth_client_id, settings.google_oauth_client_secret


# --------------------------------------------------------------------------- #
# state (CSRF)
# --------------------------------------------------------------------------- #
def build_state(user_id: uuid.UUID) -> str:
    """An opaque, tamper-evident ``state`` bound to the user who started the flow.

    Signed rather than stored: nothing to clean up, and it survives a restart.
    The callback re-derives the user from the session cookie and checks it
    matches, so a state minted for one account can't be redeemed by another.
    """
    return sign_value(str(user_id))


def parse_state(state: str) -> uuid.UUID | None:
    """Recover the user id from a ``state``, or None if it's invalid."""
    payload = verify_value(state)
    if payload is None:
        return None
    try:
        return uuid.UUID(payload)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# flow
# --------------------------------------------------------------------------- #
def build_authorization_url(*, state: str) -> str:
    """The URL to send the user's browser to in order to grant access."""
    client_id, _ = _require_client()
    settings = get_settings()
    params = {
        "client_id": client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": google_scopes.scope_string(),
        "state": state,
        # `offline` is what makes Google issue a refresh token at all, and
        # `consent` forces a fresh one even if the user already granted access
        # (otherwise a re-connect returns no refresh token and we'd store None).
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if settings.google_account_username:
        params["login_hint"] = settings.google_account_username
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _parse_token_response(payload: dict) -> GoogleTokens:
    expires_in = int(payload.get("expires_in", 3600))
    return GoogleTokens(
        access_token=payload["access_token"],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        refresh_token=payload.get("refresh_token"),
        scopes=set(payload.get("scope", "").split()),
    )


async def _post_token(form: dict[str, str]) -> GoogleTokens:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(TOKEN_ENDPOINT, data=form)

    if response.status_code >= 400:
        try:
            error = response.json().get("error", "")
        except ValueError:
            error = response.text[:200]
        if error == "invalid_grant":
            raise GoogleAuthRevoked(
                "Google rejected the grant (invalid_grant) — it was revoked or "
                "has expired. The user needs to reconnect."
            )
        raise GoogleOAuthError(f"Google token request failed: {error or response.text[:200]}")

    return _parse_token_response(response.json())


async def exchange_code(code: str) -> GoogleTokens:
    """Trade the one-time ``code`` from the callback for tokens."""
    client_id, client_secret = _require_client()
    settings = get_settings()
    return await _post_token(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        }
    )


async def refresh_access_token(refresh_token: str) -> GoogleTokens:
    """Mint a new access token. The response carries no refresh token — reuse the old one."""
    client_id, client_secret = _require_client()
    return await _post_token(
        {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        }
    )


async def fetch_userinfo(access_token: str) -> dict:
    """Identity of the account that granted access (``sub``, ``email``, …)."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        raise GoogleOAuthError(
            f"Failed to fetch Google userinfo: {response.text[:200]}"
        )
    return response.json()


async def revoke(token: str) -> None:
    """Ask Google to invalidate a grant. Best-effort — a failure isn't fatal.

    We still delete our local copy either way; leaving a dangling grant in the
    user's Google account is better than refusing to disconnect.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            await client.post(REVOKE_ENDPOINT, data={"token": token})
    except httpx.HTTPError:
        pass
