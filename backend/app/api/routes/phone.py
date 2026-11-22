"""The phone app's device: where its push notifications go, and its notification switches; and what the phone
reports about itself while the app isn't open (``PUT /api/phone/state``; services/phone_state.py).

Bearer-authenticated phone sessions only (``POST /api/auth/token``): the device row belongs to the session that
registered it and is deleted with it (sign-out, ``DELETE``). Business logic is in services/phone_devices.py.
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import bearer_token, get_current_user, validate_origin
from app.core.limiter import limiter
from app.db.models import PhoneDevice, User
from app.db.session import get_db
from app.schemas.phone import (
    DeviceOut, DeviceRegister, DeviceUpdate, PhoneActionResult, PhoneActionResultFrame, PhonePlaceOut,
    PhoneStateOut, PhoneStateReport,
)
from app.services import auth as auth_service
from app.services import google_maps, phone_actions, phone_devices, phone_state

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


@router.put("/state", response_model=PhoneStateOut, dependencies=[Depends(validate_origin)])
def report_state(payload: PhoneStateReport, session: PhoneSession = Depends(phone_session),
                 db: Session = Depends(get_db)) -> PhoneStateOut:
    """What the phone says about itself (only the fields sent change): from the background, where there's no
    socket. While the app is open the same fields ride on its ``sense`` frames."""
    changed = phone_state.report(session.user.id, payload.fields(), observed_at=payload.observed_at)
    phone_devices.mark_seen(db, session.user.id, auth_service.session_key_hash(session.token))
    return PhoneStateOut(changed=changed)


@router.post("/actions/{action_id}/result", status_code=status.HTTP_204_NO_CONTENT)
async def post_action_result(
    action_id: UUID, request: Request, session: PhoneSession = Depends(phone_session),
) -> None:
    # Validate locally so a rejected payload is never echoed in FastAPI's automatic validation response.
    # Accept the same envelope as the socket as well as the status/reason/data body.
    try:
        body = await request.json()
        if isinstance(body, dict) and ("type" in body or "action_id" in body):
            payload = PhoneActionResultFrame.model_validate(body)
            if payload.action_id != action_id:
                raise ValueError
        else:
            payload = PhoneActionResult.model_validate(body)
    except (ValidationError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone action result") from None
    try:
        accepted = await run_in_threadpool(
            phone_actions.accept_result, session.user.id, auth_service.session_key_hash(session.token),
            action_id, payload,
        )
    except phone_actions.InvalidResult:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid phone action result") from None
    except phone_actions.ResultUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Phone action result unavailable") from None
    if not accepted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Phone action not found")


@router.get("/places/search", response_model=list[PhonePlaceOut])
@limiter.limit("30/minute")
def search_places(request: Request, response: Response, q: str = Query(min_length=1, max_length=300),
                  session: PhoneSession = Depends(phone_session)) -> list[PhonePlaceOut]:
    query = q.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a place or an address")
    try:
        places = google_maps.search(query)
    except google_maps.MapsUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Maps is unavailable right now") from None
    return [PhonePlaceOut(name=place.name, address=place.address, lat=place.point[0], lng=place.point[1])
            for place in places]
