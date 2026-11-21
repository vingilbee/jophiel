"""The phone app's device: where its push notifications go, and its notification switches.

Bearer-authenticated phone sessions only (``POST /api/auth/token``): the device row belongs to the session that
registered it and is deleted with it (sign-out, ``DELETE``). Business logic is in services/phone_devices.py.
"""
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import bearer_token, get_current_user, validate_origin
from app.db.models import PhoneDevice, User
from app.db.session import get_db
from app.schemas.phone import DeviceOut, DeviceRegister, DeviceUpdate
from app.services import auth as auth_service
from app.services import phone_devices

router = APIRouter(prefix="/phone", tags=["phone"])


@dataclass(frozen=True)
class PhoneSession:
    user: User
    token: str


def phone_session(request: Request, user: User = Depends(get_current_user)) -> PhoneSession:
    """The caller's phone session: a bearer token whose session is a phone's. A web cookie gets 403."""
    token = bearer_token(request.headers)
    record = auth_service.get_session(token) if token else None
    if record is None or record.get("client") != auth_service.PHONE_CLIENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Phone sessions only")
    return PhoneSession(user=user, token=token)


def _device(db: Session, session: PhoneSession) -> PhoneDevice:
    device = phone_devices.for_session(db, session.token)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No device registered for this session")
    return device


@router.get("/device", response_model=DeviceOut)
def get_device(db: Session = Depends(get_db), session: PhoneSession = Depends(phone_session)) -> PhoneDevice:
    return _device(db, session)


@router.put("/device", response_model=DeviceOut, dependencies=[Depends(validate_origin)])
def register_device(payload: DeviceRegister, db: Session = Depends(get_db),
                    session: PhoneSession = Depends(phone_session)) -> PhoneDevice:
    """Register or refresh this session's device: after sign-in, and whenever the FCM token changes."""
    name = payload.device_name
    if name is None:
        name = (auth_service.get_session(session.token) or {}).get("device_name")
    return phone_devices.register(db, user_id=session.user.id, token=session.token,
                                  fcm_token=payload.fcm_token, device_name=name)


@router.patch("/device", response_model=DeviceOut, dependencies=[Depends(validate_origin)])
def update_device(payload: DeviceUpdate, db: Session = Depends(get_db),
                  session: PhoneSession = Depends(phone_session)) -> PhoneDevice:
    return phone_devices.update(db, _device(db, session), notifications_enabled=payload.notifications_enabled,
                                show_text=payload.show_text)


@router.delete("/device", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)])
def delete_device(db: Session = Depends(get_db), session: PhoneSession = Depends(phone_session)) -> None:
    phone_devices.forget_session(db, session.token)
