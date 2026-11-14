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
    CaptureGateIn,
    DecisionIn,
    DecisionSent,
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
    SessionOptions,
    SessionOut,
    SessionResume,
    SecurityMonitoringIn,
    SecurityStatusOut,
)
from app.ai.desktop.tls import ensure_relay_certificate
from app.services import desktop_commands, desktop_link, desktop_preview, security_activity
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
        autonomy=session.autonomy,
        explore=session.explore,
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


@router.put("/environments/{environment_id}/capture", response_model=EnvironmentOut, dependencies=mutation)
def set_capture(
    environment_id: uuid.UUID, payload: CaptureGateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _call(desktop_link.set_capture_gate, db, user.id, environment_id, payload.gate, scope=payload.scope)


@router.put("/environments/{environment_id}/security", response_model=EnvironmentOut, dependencies=mutation)
def set_security_monitoring(
    environment_id: uuid.UUID, payload: SecurityMonitoringIn, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The owner's switch for reading the VM's security logs (read only; apart from capture and input)."""
    return _call(security_activity.set_monitoring, db, user.id, environment_id, payload.enabled)


@router.get("/environments/{environment_id}/security", response_model=SecurityStatusOut)
def security_status(environment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """How monitoring is doing: which channels can be read, and why not the others."""
    return _call(security_activity.status, db, user.id, environment_id)


@router.get("/environments/{environment_id}/preview")
def get_preview(environment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The latest game frame, only while capture is allowed and the frame was
    taken under the current setting by the current connection. Never cached."""
    env = _call(service.get_environment, db, user.id, environment_id)
    frame = desktop_preview.load(env.id)
    if (
        frame is None
        or env.status != "online"
        or env.capture_gate != "allowed"
        or frame.epoch != env.capture_epoch
        or frame.generation != env.controller_generation
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "no_frame", "message": "no current picture"}
        )
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Seq": str(frame.seq),
            "X-Frame-Age-Ms": str(int(frame.age_seconds * 1000)),
        },
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


@router.patch("/sessions/{session_id}/options", response_model=SessionOut, dependencies=mutation)
def set_options(
    session_id: uuid.UUID, payload: SessionOptions, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Keep playing on her own after a goal, exploring, and how chatty she is."""
    return _session_out(_call(
        service.set_session_options, db, user.id, session_id,
        autonomy=payload.autonomy, commentary_mode=payload.commentary_mode, explore=payload.explore,
    ))


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


@router.post(
    "/sessions/{session_id}/decisions",
    response_model=DecisionSent,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=mutation,
)
@limiter.limit("120/minute")
def send_decision(
    request: Request,
    response: Response,
    session_id: uuid.UUID,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stage C test input. Accepted means sent, not done: see GET .../decisions."""
    decision_id = _call(service.send_decision, db, user.id, session_id, payload.decision, payload.decision_id)
    return DecisionSent(decision_id=decision_id)


@router.get("/sessions/{session_id}/decisions")
def list_decisions(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """What happened to recent decisions, newest first (kept about an hour)."""
    _call(service.get_session, db, user.id, session_id)
    return desktop_commands.recent_reports(session_id)


@router.get("/sessions/{session_id}/driver")
def driver_state(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The driver's notes, facts, reports and timings: untrusted game observation."""
    _call(service.get_session, db, user.id, session_id)
    return desktop_commands.load_driver_state(session_id) or {}


@router.get("/sessions/{session_id}/events", response_model=list[SessionEventOut])
def list_events(
    session_id: uuid.UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _call(service.list_events, db, user.id, session_id, after_id=after_id, limit=limit)
