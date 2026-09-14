"""Google account integration: OAuth 2.0 connection + per-user access tokens.

This package owns *authentication only*. It knows nothing about Gmail — the
Gmail tools live in ``app.ai.tools.gmail`` and ask here for a token. Adding
Calendar or Drive later means new tools, not new auth: extend
``scopes.REQUESTED_SCOPES`` and have users reconnect.

Flow
----
1. ``GET  /api/integrations/google/authorize`` → ``build_authorization_url``
2. user consents at Google
3. ``GET  /api/integrations/google/callback``  → ``exchange_code`` → store
4. any later request → ``get_access_token`` (refreshes on demand)

Public surface:
    - get_access_token       : live access token for a user, or None
    - build_authorization_url / build_state / parse_state
    - exchange_code / refresh_access_token / fetch_userinfo / revoke
    - is_configured          : are client credentials present?
    - GoogleOAuthError / GoogleAuthRevoked
"""
from app.ai.integrations.google.credentials import get_access_token
from app.ai.integrations.google.oauth import (
    GoogleAuthRevoked,
    GoogleOAuthError,
    GoogleTokens,
    build_authorization_url,
    build_state,
    exchange_code,
    fetch_userinfo,
    is_configured,
    parse_state,
    refresh_access_token,
    revoke,
)

__all__ = [
    "GoogleAuthRevoked",
    "GoogleOAuthError",
    "GoogleTokens",
    "build_authorization_url",
    "build_state",
    "exchange_code",
    "fetch_userinfo",
    "get_access_token",
    "is_configured",
    "parse_state",
    "refresh_access_token",
    "revoke",
]
