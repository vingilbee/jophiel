"""Shared FastAPI dependencies."""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.services import auth as auth_service


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Resolve the session cookie to a user, or None if there isn't a valid one.

    For endpoints that must respond usefully to a signed-out visitor rather
    than 401 — notably the OAuth callback, which is a browser navigation and
    should redirect the user somewhere sensible.
    """
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    return auth_service.get_user_for_token(db, token)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Resolve the session cookie to the logged-in user, or raise 401."""
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user = auth_service.get_user_for_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
    return user

def validate_origin(request: Request):
    settings = get_settings()
    cors = settings.cors_origins
    headers = request.headers.get("origin")

    if headers not in cors:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid origin"
        )
    return True

