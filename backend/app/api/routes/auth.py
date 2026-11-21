"""Authentication routes: signup, signin, phone token, signout, current user, delete account.

The web app signs in with a cookie (``/signin``); the phone app gets a bearer token (``/token``), which sets no
cookie and so needs no Origin check. Both are the same Redis sessions (``services/auth.py``).
"""
from datetime import datetime, timedelta, timezone

from app.api.deps import bearer_token, validate_origin
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from app.core.limiter import limiter
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.schemas.user import TokenOut, TokenRequest, UserCreate, UserLogin, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.session_cookie_name, path="/")


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_origin)])
@limiter.limit("3/hour")
def signup(request: Request, payload: UserCreate, response: Response, db: Session = Depends(get_db)) -> User:
    try:
        user = auth_service.create_user(
            db, name=payload.name, email=payload.email, password=payload.password
        )
    except auth_service.EmailAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )
    token = auth_service.create_session(user)
    _set_session_cookie(response, token)
    return user


@router.post("/signin", response_model=UserOut, dependencies=[Depends(validate_origin)])
@limiter.limit("5/minute")
def signin(
    request: Request, payload: UserLogin, response: Response, db: Session = Depends(get_db)
) -> User:
    user = auth_service.authenticate(
        db, email=payload.email, password=payload.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = auth_service.create_session(user)
    _set_session_cookie(response, token)
    return user


@router.post("/token", response_model=TokenOut)
@limiter.limit("5/minute")
def token(request: Request, payload: TokenRequest, response: Response,
          db: Session = Depends(get_db)) -> TokenOut:
    """Phone sign-in: returns a bearer token for a sliding 90-day session. Sets no cookie."""
    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    session = auth_service.create_session(
        user, client=auth_service.PHONE_CLIENT, device_name=payload.device_name.strip())
    lifetime = auth_service.session_lifetime(auth_service.PHONE_CLIENT)
    return TokenOut(token=session, user=UserOut.model_validate(user),
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime))


@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)])
def signout(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> None:
    """End this session: the bearer token's (the phone, and its device row) or the cookie's."""
    settings = get_settings()
    phone = bearer_token(request.headers)
    if phone:
        from app.services import phone_devices
        auth_service.delete_session(phone)
        # No more notifications for this phone (best effort: the push worker also drops a dead session's device).
        try:
            phone_devices.forget_session(db, phone)
        except Exception:
            db.rollback()
        return
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        auth_service.delete_session(token)
    _clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(validate_origin)])
def delete_account(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    auth_service.delete_user(db, current_user)
    _clear_session_cookie(response)
    return {"status": "deletion_pending"}
