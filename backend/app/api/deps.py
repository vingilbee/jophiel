"""Shared FastAPI dependencies.

Two ways to carry a session: the web app's ``session`` cookie, and the phone app's
``Authorization: Bearer <token>`` header (same Redis session store, same hashing). A request that sends a
bearer header is judged by that header alone, never by a cookie riding along with it.
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.services import auth as auth_service


def bearer_token(headers: Headers) -> str | None:
    """The token from ``Authorization: Bearer <token>``, or None when there is no such header."""
    value = headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def session_token(request: Request) -> str | None:
    """The bearer token if the request sends one, otherwise the session cookie."""
    token = bearer_token(request.headers)
    if token is not None:
        return token
    return request.cookies.get(get_settings().session_cookie_name)


def get_current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Resolve the session to a user, or None if there isn't a valid one.

    For endpoints that must respond usefully to a signed-out visitor rather
    than 401 — notably the OAuth callback, which is a browser navigation and
    should redirect the user somewhere sensible.
    """
    token = session_token(request)
    if not token:
        return None
    return auth_service.get_user_for_token(db, token)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Resolve the session (bearer header or cookie) to the logged-in user, or raise 401."""
    token = session_token(request)
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


def require_owner(user: User = Depends(get_current_user)) -> User:
    """Her insides (memory inspection, her mind, learning, captures, training data) are the owner's only. Anyone
    else gets 404, as if the route weren't there: they can only talk with her."""
    if not get_settings().is_owner(user.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return user


def bearer_authenticated(headers: Headers) -> bool:
    """Whether the request carries a bearer token for a live session.

    A browser can't attach this header to a cross-site request without the API's CORS consent, so such a
    request can't be forged the way a cookie request can."""
    token = bearer_token(headers)
    if token is None:
        return False
    try:
        return auth_service.get_session(token) is not None
    except Exception:
        return False


def validate_origin(request: Request):
    """CSRF guard for cookie sessions: the request must come from one of the app's own origins.

    A request authenticated by a valid bearer header passes: it can't be forged cross-site. Cookie requests
    are checked exactly as before."""
    if bearer_authenticated(request.headers):
        return True
    settings = get_settings()
    if request.headers.get("origin") not in settings.cors_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid origin"
        )
    return True
