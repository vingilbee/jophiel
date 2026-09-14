"""Third-party integration routes.

Integrations are exposed as a list, driven by the provider registry
(``app.ai.integrations.registry``). Listing, starting a connection, and
disconnecting are all provider-agnostic — adding Slack or Notion later needs no
change here:

    GET    /api/integrations                    → every provider + this user's status
    GET    /api/integrations/{provider}         → one of them
    GET    /api/integrations/{provider}/authorize → consent URL (JSON, so `fetch` can read it)
    DELETE /api/integrations/{provider}         → revoke upstream + forget locally

OAuth *callbacks* stay provider-specific, because each provider's response
differs. Google's is ``GET /api/integrations/google/callback``.

That callback is a browser navigation, so the ``SameSite=lax`` session cookie
rides along and identifies the user. It's authenticated twice over: the signed
``state`` carries the user id that started the flow and must match the session,
which is what stops a forged callback attaching an attacker's mailbox to
someone else's account.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.ai.integrations import google, registry
from app.ai.integrations.google import scopes as google_scopes
from app.api.deps import get_current_user, get_current_user_optional, validate_origin
from app.core.config import get_settings
from app.core.crypto import EncryptionKeyMissing
from app.db.models import User
from app.db.session import get_db
from app.schemas.integrations import AuthorizeOut, IntegrationOut
from app.services import google_account as account_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _to_out(
    provider: registry.IntegrationProvider,
    db: Session,
    user_id: uuid.UUID,
) -> IntegrationOut:
    connection = provider.get_connection(db, user_id)
    return IntegrationOut(
        id=provider.id,
        name=provider.name,
        description=provider.description,
        category=provider.category,
        capabilities=list(provider.capabilities),
        configured=provider.is_configured(),
        connected=connection is not None,
        account_label=connection.account_label if connection else None,
        connected_at=connection.connected_at if connection else None,
        scopes=list(connection.scopes) if connection else [],
    )


def _require_provider(provider_id: str) -> registry.IntegrationProvider:
    provider = registry.get_provider(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown integration: {provider_id}",
        )
    return provider


def _frontend_redirect(
    provider_id: str, outcome: str, *, reason: str | None = None
) -> RedirectResponse:
    """Bounce the browser back to the integrations page with the flow's result."""
    settings = get_settings()
    query = f"provider={provider_id}&status={outcome}"
    if reason:
        query += f"&reason={reason}"
    return RedirectResponse(
        url=f"{settings.frontend_base_url.rstrip('/')}/integrations?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --------------------------------------------------------------------------- #
# Provider-agnostic
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[IntegrationOut])
def list_integrations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[IntegrationOut]:
    """Every available integration and whether this user has connected it."""
    return [
        _to_out(provider, db, current_user.id)
        for provider in registry.all_providers()
    ]


# Declared before the `/{provider_id}/…` routes so the literal path always wins.
@router.get("/google/callback")
async def google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> RedirectResponse:
    """Finish Google's flow: exchange the code and store the grant.

    Always redirects (never returns JSON) — the browser lands here directly
    from Google, so the user must end up back in the app either way. That's
    also why auth is optional here: a session that expired mid-flow should send
    them back to sign in, not dump a 401 body in the browser.
    """
    if current_user is None:
        return _frontend_redirect("google", "error", reason="not_signed_in")

    if error:
        # Usually the user clicking "Cancel" on the consent screen.
        logger.info("Google OAuth callback returned an error: %s", error)
        return _frontend_redirect("google", "error", reason=error)

    if not code or not state:
        return _frontend_redirect("google", "error", reason="missing_code")

    state_user_id = google.parse_state(state)
    if state_user_id is None or state_user_id != current_user.id:
        # Either the state was tampered with, or it was minted for a different
        # account than the one currently signed in.
        logger.warning(
            "Rejecting Google callback: state/session mismatch for user %s",
            current_user.id,
        )
        return _frontend_redirect("google", "error", reason="state_mismatch")

    try:
        tokens = await google.exchange_code(code)
    except google.GoogleOAuthError:
        logger.exception("Google code exchange failed for user %s", current_user.id)
        return _frontend_redirect("google", "error", reason="exchange_failed")

    if not tokens.refresh_token:
        # We ask for `access_type=offline` + `prompt=consent`, so this shouldn't
        # happen — but without a refresh token the grant dies in an hour and is
        # worse than useless, so refuse to store it.
        logger.error("Google returned no refresh token for user %s", current_user.id)
        return _frontend_redirect("google", "error", reason="no_refresh_token")

    if not google_scopes.has_required_scopes(tokens.scopes):
        logger.info(
            "User %s granted neither Gmail nor Drive access: %s",
            current_user.id,
            sorted(tokens.scopes),
        )
        return _frontend_redirect("google", "error", reason="insufficient_scope")

    # Best-effort identity: failing here shouldn't lose a valid grant, it just
    # means the UI can't name the connected mailbox.
    email: str | None = None
    google_sub: str | None = None
    try:
        userinfo = await google.fetch_userinfo(tokens.access_token)
        email = userinfo.get("email")
        google_sub = userinfo.get("sub")
    except google.GoogleOAuthError:
        logger.warning("Could not fetch Google userinfo for user %s", current_user.id)

    try:
        account_service.upsert(
            db,
            user_id=current_user.id,
            refresh_token=tokens.refresh_token,
            access_token=tokens.access_token,
            access_token_expires_at=tokens.expires_at,
            scopes=tokens.scopes,
            email=email,
            google_sub=google_sub,
        )
    except EncryptionKeyMissing:
        logger.exception("Cannot store the Google grant — encryption key missing.")
        return _frontend_redirect("google", "error", reason="server_misconfigured")

    return _frontend_redirect("google", "connected")


@router.get("/{provider_id}", response_model=IntegrationOut)
def get_integration(
    provider_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationOut:
    return _to_out(_require_provider(provider_id), db, current_user.id)


@router.get("/{provider_id}/authorize", response_model=AuthorizeOut)
def authorize_integration(
    provider_id: str,
    current_user: User = Depends(get_current_user),
) -> AuthorizeOut:
    """Mint a consent URL for this user to start the provider's OAuth flow."""
    provider = _require_provider(provider_id)
    if not provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The {provider.name} integration is not configured on this server.",
        )
    try:
        return AuthorizeOut(
            authorization_url=provider.build_authorize_url(current_user.id)
        )
    except EncryptionKeyMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)])
async def disconnect_integration(
    provider_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Revoke the grant upstream and delete our copy.

    Idempotent — disconnecting something that isn't connected is a no-op, not
    a 404.
    """
    provider = _require_provider(provider_id)
    await provider.disconnect(db, current_user.id)
