"""Symmetric encryption and value signing for third-party integration secrets.

Unlike our own session tokens (which we only ever store hashed — see
``security.py``), OAuth refresh tokens must be *recoverable*: we need the
original value to mint new access tokens. So they're encrypted at rest with
Fernet (AES-128-CBC + HMAC) rather than hashed.

The same key backs :func:`sign_value` / :func:`verify_value`, which we use for
the OAuth ``state`` parameter — a tamper-evident round-trip through the
provider that needs no server-side storage.
"""
from __future__ import annotations

import hmac
import secrets
from functools import lru_cache
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class EncryptionKeyMissing(RuntimeError):
    """Raised when an integration needs the key but it isn't configured."""

    def __init__(self) -> None:
        super().__init__(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is not set. Generate one with:\n"
            '  uv run python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"\n'
            "then add it to your .env."
        )


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().google_token_encryption_key
    if not key:
        raise EncryptionKeyMissing
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:  # malformed / wrong-length key
        raise ValueError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key "
            "(expected 32 url-safe base64-encoded bytes)."
        ) from exc


def encrypt_secret(value: str) -> str:
    """Encrypt a secret for storage in the database."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    Raises ``cryptography.fernet.InvalidToken`` if the ciphertext was written
    with a different key — i.e. rotating the key invalidates stored tokens and
    users must reconnect.
    """
    return _fernet().decrypt(value.encode()).decode()


# Re-exported so callers can handle a key rotation without importing cryptography.
InvalidSecret = InvalidToken


def _signature(payload: str) -> str:
    settings = get_settings()
    if not settings.google_token_encryption_key:
        raise EncryptionKeyMissing
    return hmac.new(
        settings.google_token_encryption_key.encode(),
        payload.encode(),
        sha256,
    ).hexdigest()


def sign_value(payload: str) -> str:
    """Return ``payload`` with an HMAC tag appended, plus a random nonce.

    The nonce makes each signed value unique, so an OAuth ``state`` can't be
    replayed from a previously observed URL.
    """
    body = f"{payload}:{secrets.token_urlsafe(16)}"
    return f"{body}:{_signature(body)}"


def verify_value(signed: str) -> str | None:
    """Recover the payload from :func:`sign_value`, or None if it's been tampered with."""
    payload, _, rest = signed.partition(":")
    nonce, _, signature = rest.partition(":")
    if not payload or not nonce or not signature:
        return None
    body = f"{payload}:{nonce}"
    if not hmac.compare_digest(_signature(body), signature):
        return None
    return payload
