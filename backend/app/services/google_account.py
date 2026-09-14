"""Persistence for a user's connected Google account.

All token ciphertext handling is contained here: callers pass and receive
plaintext, and never see the encrypted columns. Every function is scoped to a
single ``user_id`` so one user's grant can't be read or overwritten by another.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import InvalidSecret, decrypt_secret, encrypt_secret
from app.db.models import GoogleAccount


def get_for_user(db: Session, *, user_id: uuid.UUID) -> GoogleAccount | None:
    return db.scalar(
        select(GoogleAccount).where(GoogleAccount.user_id == user_id)
    )


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    refresh_token: str,
    access_token: str,
    access_token_expires_at: datetime,
    scopes: set[str],
    email: str | None,
    google_sub: str | None,
) -> GoogleAccount:
    """Create or replace the user's grant (one row per user)."""
    account = get_for_user(db, user_id=user_id)
    if account is None:
        account = GoogleAccount(user_id=user_id)
        db.add(account)

    account.encrypted_refresh_token = encrypt_secret(refresh_token)
    account.encrypted_access_token = encrypt_secret(access_token)
    account.access_token_expires_at = access_token_expires_at
    account.scopes = " ".join(sorted(scopes))
    account.email = email
    account.google_sub = google_sub

    db.commit()
    db.refresh(account)
    return account


def store_access_token(
    db: Session,
    *,
    account: GoogleAccount,
    access_token: str,
    expires_at: datetime,
) -> None:
    """Cache a freshly refreshed access token so the next turn can reuse it."""
    account.encrypted_access_token = encrypt_secret(access_token)
    account.access_token_expires_at = expires_at
    db.commit()


def read_refresh_token(account: GoogleAccount) -> str | None:
    """Decrypt the stored refresh token, or None if it's unreadable.

    Unreadable means the encryption key was rotated — the grant is effectively
    lost and the user has to reconnect, so this is a normal (if unlucky) state
    rather than an error to propagate.
    """
    try:
        return decrypt_secret(account.encrypted_refresh_token)
    except InvalidSecret:
        return None


def read_access_token(account: GoogleAccount) -> str | None:
    if not account.encrypted_access_token:
        return None
    try:
        return decrypt_secret(account.encrypted_access_token)
    except InvalidSecret:
        return None


def delete_for_user(db: Session, *, user_id: uuid.UUID) -> bool:
    """Remove the user's grant. True if there was one to remove."""
    account = get_for_user(db, user_id=user_id)
    if account is None:
        return False
    db.delete(account)
    db.commit()
    return True
