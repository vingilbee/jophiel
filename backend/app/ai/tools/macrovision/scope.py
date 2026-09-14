"""Short-lived, per-user scope tokens for the mounted MacroVision MCP server.

The MacroVision MCP server is mounted inside the API process (see
``mount.py``), which means its endpoint is reachable by anything that can reach
the API. It serves one user's stored photos, so it needs to know *which* user —
and must not accept an unauthenticated caller's word for it.

The token is an HMAC over ``<user_id>|<expiry>``. By default the key is
generated fresh at import, which is deliberate rather than lazy: the minting
side and the verifying side are the same process, so a process-lifetime key
needs no configuration, can't leak from a ``.env``, and makes the endpoint
useless to anyone who didn't get a token from this exact process. Tokens are
minted per chat turn, so losing them all on restart costs nothing.

That breaks in exactly one situation — several uvicorn workers, where the
worker that minted a token may not be the one that verifies it. Set
``INTERNAL_MCP_SECRET`` to share a key across them. (Multi-worker is a bad fit
here regardless: each worker loads its own copy of the 3.6GB of weights.)

External MCP clients don't use this at all — they run the server standalone
over stdio, where the user is named by ``MACROVISION_USER_ID``.
"""
from __future__ import annotations

import hmac
import secrets
import time
import uuid
from functools import lru_cache
from hashlib import sha256

from app.core.config import get_settings

# Header the client sends the token in. A bearer token rather than a custom
# header so any MCP client's standard auth handling works unchanged.
SCOPE_HEADER = "Authorization"
SCOPE_PREFIX = "Bearer "

# Env var naming the user when the server runs standalone over stdio.
USER_ID_ENV = "MACROVISION_USER_ID"

# Long enough to outlive a slow combined prediction (VLM + regressor), short
# enough that a token captured from a log is worthless by the time it's read.
DEFAULT_TTL_SECONDS = 15 * 60

_PROCESS_SECRET = secrets.token_bytes(32)


@lru_cache
def _secret() -> bytes:
    configured = get_settings().internal_mcp_secret
    return configured.encode() if configured else _PROCESS_SECRET


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), sha256).hexdigest()


def mint_scope_token(
    user_id: uuid.UUID | str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Mint a token authorizing MCP calls on behalf of ``user_id``."""
    payload = f"{user_id}|{int(time.time()) + ttl_seconds}"
    return f"{payload}|{_sign(payload)}"


def read_scope_token(token: str) -> uuid.UUID | None:
    """Recover the user id from a token, or None if it's invalid or expired."""
    user_part, _, rest = token.partition("|")
    expiry_part, _, signature = rest.partition("|")
    if not user_part or not expiry_part or not signature:
        return None

    payload = f"{user_part}|{expiry_part}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None

    try:
        if int(expiry_part) < time.time():
            return None
        return uuid.UUID(user_part)
    except ValueError:
        return None


def authorization_header(user_id: uuid.UUID | str) -> dict[str, str]:
    """The header dict a client passes to reach the mounted server as this user."""
    return {SCOPE_HEADER: f"{SCOPE_PREFIX}{mint_scope_token(user_id)}"}
