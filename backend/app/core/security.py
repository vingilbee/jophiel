"""Password hashing and session-token primitives."""
import hashlib
import secrets

from pwdlib import PasswordHash

# Argon2-based hasher (FastAPI's current recommendation).
_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def generate_session_token() -> str:
    """A random, URL-safe opaque token handed to the client in a cookie."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """SHA-256 of the token — this is what we persist, never the raw token.

    A plain hash (no salt) is fine here: the input is already high-entropy
    random, so it isn't vulnerable to dictionary/rainbow-table attacks, and a
    deterministic hash lets us look the session up by equality.
    """
    return hashlib.sha256(token.encode()).hexdigest()
