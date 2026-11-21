"""Auth business logic: users + server-side sessions.

Route handlers stay thin and call into here (per CLAUDE.md conventions).
"""
from datetime import timedelta
from functools import lru_cache
import redis
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.db.models import User


class EmailAlreadyExists(Exception):
    """Raised when signing up with an email that's already registered."""


def create_user(db: Session, *, name: str, email: str, password: str) -> User:
    email = email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyExists(email)

    user = User(name=name, email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    """Return the user if credentials are valid, else None."""
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        # Hash anyway to keep timing roughly constant against user enumeration.
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user

@lru_cache(maxsize=4)
def _redis_for(url: str) -> redis.Redis:
    # Shared per process: every session check reuses a pooled connection
    # instead of opening a new one.
    return redis.from_url(url)


def get_redis():
    return _redis_for(get_settings().redis_url)

PHONE_CLIENT = "phone"


def _session_key(token: str) -> str:
    return f"session:{hash_session_token(token)}"


def session_key_hash(token: str) -> str:
    """The hash a session is stored under: what other records (a phone's device row) point at."""
    return hash_session_token(token)


def create_session(user: User, *, client: str = "web", device_name: str | None = None) -> str:
    """Create a Redis session and return the raw token (a cookie on the web, a bearer token on the phone).

    Phone sessions last ``phone_session_lifetime_seconds`` and slide (``get_session``); web sessions keep the
    cookie's fixed lifetime."""
    r = get_redis()
    token = generate_session_token()
    record: dict = {"user_id": str(user.id)}
    if client == PHONE_CLIENT:
        record.update(client=PHONE_CLIENT, device_name=device_name)
    r.setex(_session_key(token), timedelta(seconds=session_lifetime(client)), json.dumps(record))
    return token


def session_lifetime(client: str | None) -> int:
    settings = get_settings()
    return settings.phone_session_lifetime_seconds if client == PHONE_CLIENT else settings.session_lifetime_seconds


def get_session(token: str) -> dict | None:
    """The session record for a token (``user_id``, and ``client`` / ``device_name`` for the phone), or None.

    A phone session is renewed to its full lifetime when less than half of it is left, so a phone used now and
    then stays signed in; web sessions are never extended here."""
    r = get_redis()
    key = _session_key(token)
    data = r.get(key)
    if data is None:
        return None
    record = json.loads(data)
    if record.get("client") == PHONE_CLIENT:
        lifetime = session_lifetime(PHONE_CLIENT)
        left = r.ttl(key)
        if isinstance(left, int) and 0 <= left < lifetime / 2:
            r.expire(key, lifetime)
    return record


def session_expires_in(token: str) -> int | None:
    """Seconds until the session ends, or None if it doesn't exist."""
    left = get_redis().ttl(_session_key(token))
    return left if isinstance(left, int) and left >= 0 else None


def session_alive(session_hash: str) -> bool:
    """Whether the session stored under this hash still exists (a phone device dies with its session)."""
    return bool(get_redis().exists(f"session:{session_hash}"))


def get_user_for_token(db: Session, token: str) -> User | None:
    """Resolve a session token to a user, expiring stale sessions lazily."""
    record = get_session(token)
    if record is None:
        return None
    return db.get(User, record["user_id"])


def delete_session(token: str) -> None:
    """Revoke the Redis session; missing or expired tokens are a no-op."""
    r = get_redis()
    r.delete(_session_key(token))


def delete_user(db: Session, user: User) -> None:
    """Atomically remove account rows and enqueue persistent file cleanup."""
    from app.services import file_deletion

    file_deletion.schedule_deletion(db, user.id)
    db.delete(user)
    db.commit()
    file_deletion.wake_worker()


# Precomputed hash of a throwaway password, used to equalize timing when the
# email doesn't exist (mitigates account-enumeration via response time).
_DUMMY_HASH = hash_password("dummy-password-for-timing")
