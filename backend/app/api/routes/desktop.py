"""Desktop agent routes (plan §18). Thin: every rule lives in
``services/desktop_sessions.py``.

The whole router answers 404 while ``DESKTOP_AGENT_ENABLED`` is off, so a
disabled feature looks exactly like an absent one. Refusals carry a stable
``{"code", "message"}`` detail the UI can branch on.
"""
import uuid
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, validate_origin
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.models import DesktopSession, User
from app.db.session import get_db
from app.schemas.desktop import (
    EnvironmentCreate,
    EnvironmentOut,
    PlanAppend,
    PlanOut,
    PairingOut,
    PlanReplace,
    ProfileOut,
    SessionCommand,
    SessionCreate,
    SessionEventOut,
    SessionOut,
    SessionResume,
)
from app.ai.desktop.tls import ensure_relay_certificate
from app.services import desktop_link
from app.services import desktop_sessions as service
from desktop_controller.link import display_fingerprint
from app.services.desktop_sessions import CommandResult, DesktopError


def _require_enabled() -> None:
    if not get_settings().desktop_agent_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(prefix="/desktop", tags=["desktop"], dependencies=[Depends(_require_enabled)])
mutation = [Depends(validate_origin)]

T = TypeVar("T")


def _call(fn: Callable[..., T], *args, **kwargs) -> T:
    try:
        return fn(*args, **kwargs)
    except DesktopError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


def _plan_out(session: DesktopSession) -> PlanOut | None:
    if session.plan is None:
        return None
    return PlanOut(plan_revision=session.plan_revision, **session.plan)


def _session_out(session: DesktopSession, release_pending: bool = False) -> SessionOut:
    return SessionOut(
        id=session.id,
        environment_id=session.environment_id,
        conversation_id=session.conversation_id,
        profile_id=session.profile_id,
        profile_revision=session.profile_revision,
        state=session.state,
        state_revision=session.state_revision,
        reason_code=session.reason_code,
        control_owner=session.control_owner,
        execution_generation=session.execution_generation,
        plan_revision=session.plan_revision,
        plan=_plan_out(session),
        current_step_id=session.current_step_id,
        driver_status=session.driver_status,
        commentary_mode=session.commentary_mode,
        max_duration_seconds=session.max_duration_seconds,
        started_at=session.started_at,
        ended_at=session.ended_at,
        release_pending=release_pending,
    )


def _command_out(result: CommandResult) -> SessionOut:
    return _session_out(result.session, result.release_pending)


# --- profiles and environments ---------------------------------------------


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(_: User = Depends(get_current_user)):
    return service.list_profiles()


@router.get("/environments", response_model=list[EnvironmentOut])
def list_environments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return service.list_environments(db, user.id)


@router.post("/environments", response_model=EnvironmentOut, status_code=status.HTTP_201_CREATED, dependencies=mutation)
@limiter.limit("10/hour")
def register_environment(
    request: Request, response: Response, payload: EnvironmentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _call(service.register_environment, db, user.id, payload)


@router.post(
    "/environments/{environment_id}/pairings",
    response_model=PairingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=mutation,
)
@limiter.limit("10/hour")
def create_pairing(
    request: Request,
    response: Response,
    environment_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    created = _call(desktop_link.create_pairing, db, user.id, environment_id)
    host = settings.desktop_controller_public_host or settings.desktop_controller_bind_host
    return PairingOut(
        code=created.code,
        expires_at=created.pairing.expires_at,
        relay_address=f"{host}:{settings.desktop_controller_port}",
        relay_fingerprint=display_fingerprint(ensure_relay_certificate().fingerprint),
    )


@router.post("/environments/{environment_id}/revoke", response_model=EnvironmentOut, dependencies=mutation)
def revoke_environment(environment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(service.revoke_environment, db, user.id, environment_id)


# --- sessions -------------------------------------------------------------------


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED, dependencies=mutation)
@limiter.limit("30/hour")
def start_session(
    request: Request,
    response: Response,
    payload: SessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session, created = _call(service.start_session, db, user.id, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _session_out(session)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    live_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return [_session_out(s) for s in service.list_sessions(db, user.id, live_only=live_only, limit=limit)]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _session_out(_call(service.get_session, db, user.id, session_id))


@router.post("/sessions/{session_id}/pause", response_model=SessionOut, dependencies=mutation)
def pause_session(
    session_id: uuid.UUID,
    payload: SessionCommand | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reason = payload.reason if payload else None
    return _command_out(_call(service.pause_session, db, user.id, session_id, reason=reason))


@router.post("/sessions/{session_id}/resume", response_model=SessionOut, dependencies=mutation)
def resume_session(
    session_id: uuid.UUID, payload: SessionResume, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _command_out(_call(
        service.resume_session, db, user.id, session_id, expected_state_revision=payload.expected_state_revision
    ))


@router.post("/sessions/{session_id}/takeover", response_model=SessionOut, dependencies=mutation)
def takeover_session(
    session_id: uuid.UUID,
    payload: SessionCommand | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reason = payload.reason if payload else None
    return _command_out(_call(service.takeover_session, db, user.id, session_id, reason=reason))


@router.post("/sessions/{session_id}/end", response_model=SessionOut, dependencies=mutation)
def end_session(
    session_id: uuid.UUID,
    payload: SessionCommand | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reason = payload.reason if payload else None
    return _command_out(_call(service.end_session, db, user.id, session_id, reason=reason))


# --- plan -------------------------------------------------------------------------


@router.get("/sessions/{session_id}/plan", response_model=PlanOut | None)
def get_plan(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _plan_out(_call(service.get_session, db, user.id, session_id))


@router.put("/sessions/{session_id}/plan", response_model=SessionOut, dependencies=mutation)
def replace_plan(
    session_id: uuid.UUID, payload: PlanReplace, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _session_out(_call(service.replace_plan, db, user.id, session_id, payload))


@router.post("/sessions/{session_id}/plan/steps", response_model=SessionOut, dependencies=mutation)
def append_plan_steps(
    session_id: uuid.UUID, payload: PlanAppend, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _session_out(_call(service.append_plan_steps, db, user.id, session_id, payload))


@router.get("/sessions/{session_id}/events", response_model=list[SessionEventOut])
def list_events(
    session_id: uuid.UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _call(service.list_events, db, user.id, session_id, after_id=after_id, limit=limit)
