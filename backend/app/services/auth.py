"""Auth business logic: users + server-side sessions.

Route handlers stay thin and call into here (per CLAUDE.md conventions).
"""
from datetime import timedelta
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

def get_redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url)

def create_session(user: User) -> str:
    """Create a Redis session and return the raw token for the cookie."""
    settings = get_settings()
    r = get_redis()
    token = generate_session_token()
    key = f"session:{hash_session_token(token)}"
    r.setex(
        key,
        timedelta(seconds=settings.session_lifetime_seconds),
        json.dumps({
            "user_id": str(user.id)
        })
    )
    return token


def get_user_for_token(db: Session, token: str) -> User | None:
    """Resolve a cookie token to a user, expiring stale sessions lazily."""
    r = get_redis()
    key = f"session:{hash_session_token(token)}"
    data = r.get(key)
    if data is None:
        return None
    user_id = json.loads(data)["user_id"]
    return db.get(User, user_id)


def delete_session(token: str) -> None:
    """Revoke the Redis session; missing or expired tokens are a no-op."""
    r = get_redis()
    r.delete(f"session:{hash_session_token(token)}")


def delete_user(db: Session, user: User) -> None:
    """Atomically remove account rows and enqueue persistent file cleanup."""
    from app.services import dialogue_memory, file_deletion

    conversation_ids = [conversation.id for conversation in user.conversations]
    file_deletion.schedule_deletion(db, user.id)
    db.delete(user)
    db.commit()
    file_deletion.wake_worker()
    # Snapshots, heads, and jobs went with the cascade; cached payloads must too.
    for conversation_id in conversation_ids:
        dialogue_memory.delete_cached_conversation(conversation_id)


# Precomputed hash of a throwaway password, used to equalize timing when the
# email doesn't exist (mitigates account-enumeration via response time).
_DUMMY_HASH = hash_password("dummy-password-for-timing")
