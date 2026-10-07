"""Desktop agent sessions: ownership, lifecycle state machine, and the plan.

Plan §6 and §17-18. The rules this module owns:

- **One owner per environment.** Start inserts a live session; the partial
  unique index ``uq_desktop_sessions_active_environment`` makes the second of
  two concurrent starts fail, which becomes ``already_owned``. No check-then-
  insert race to get wrong.
- **Every transition is row-locked** (``SELECT ... FOR UPDATE``), bumps
  ``state_revision``, and writes a ``DesktopSessionEvent`` in the same
  transaction, so the persisted history and the row never disagree and a
  client can always recover from a snapshot.
- **Anything that stops input bumps ``execution_generation``** (pause,
  takeover, end, disconnect, plan replace). The controller drops any decision
  carrying an older generation, so a late model response cannot act.
- **Nothing resumes on its own.** Resume needs the caller's
  ``expected_state_revision`` and an online environment; reconnecting a
  controller never moves a session back to ``running``.
- **Ownership is a filter, not a check.** Every user-facing query includes the
  owner, so another user's ids are simply "not found" (404), never "forbidden".

Functions without an ``owner_user_id`` (``controller_*``, ``expire_overdue``)
are for the trusted relay/worker side (stage B+); no route exposes them.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES,
    Conversation,
    DesktopEnvironment,
    DesktopPairing,
    DesktopSession,
    DesktopSessionEvent,
)
from app.schemas.desktop import (
    EnvironmentCreate,
    PlanAppend,
    PlanIn,
    PlanReplace,
    ProfileOut,
    SessionCreate,
)
from app.services import desktop_preview
from desktop_controller import Profile, ProfileError, Rejection, load_profile

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Which states each command may leave from. Anything else is a conflict.
_PAUSABLE = {"starting", "running", "manual", "disconnected"}
_TAKEOVER_FROM = {"starting", "running", "paused", "disconnected"}
_RESUMABLE = {"paused", "manual", "disconnected"}
_DISCONNECTABLE = {"starting", "running"}
# Step states that still count against the plan's size bound.
_OPEN_STEP_STATES = {"pending", "active"}


class DesktopError(Exception):
    """A refusal with a stable code (plan §18) and an HTTP status."""

    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def _not_found(what: str) -> DesktopError:
    return DesktopError("not_found", 404, f"{what} not found")


@dataclass
class CommandResult:
    session: DesktopSession
    # True when this command took input away from gameplay and the controller
    # has not (yet) confirmed releasing it.
    release_pending: bool = False


# --------------------------------------------------------------------------- #
# Profiles (trusted files, never user- or model-supplied content)
# --------------------------------------------------------------------------- #


def _profiles_dir() -> Path:
    path = Path(get_settings().desktop_profiles_dir)
    return path if path.is_absolute() else BACKEND_ROOT / path


def get_profile(profile_id: str) -> Profile:
    """Load and fully check one trusted profile. The file name is the id."""
    path = _profiles_dir() / f"{profile_id}.yaml"
    if not path.is_file():
        raise DesktopError("profile_not_found", 422, f"no profile named {profile_id!r}")
    try:
        profile = _load_cached(str(path), path.stat().st_mtime_ns)
    except ProfileError as exc:
        raise DesktopError("profile_invalid", 422, f"profile {profile_id!r} failed its checks: {exc.issues[0]}")
    if profile.id != profile_id:
        raise DesktopError("profile_invalid", 422, f"profile file {path.name} declares id {profile.id!r}")
    return profile


@lru_cache(maxsize=32)
def _load_cached(path: str, mtime_ns: int) -> Profile:
    """Profiles are read on every relay heartbeat; reload only when the file changes."""
    return load_profile(path)


def list_profiles() -> list[ProfileOut]:
    """Profiles that pass every check. Templates and broken files are left out."""
    found = []
    for path in sorted(_profiles_dir().glob("*.yaml")):
        try:
            profile = get_profile(path.stem)
        except DesktopError:
            continue
        found.append(ProfileOut(
            id=profile.id, revision=profile.revision, display_name=profile.display_name,
            observation_size=profile.observation.size,
        ))
    return found


# --------------------------------------------------------------------------- #
# Environments
# --------------------------------------------------------------------------- #


def list_environments(db: Session, owner_user_id: uuid.UUID) -> list[DesktopEnvironment]:
    return list(
        db.scalars(
            select(DesktopEnvironment)
            .where(DesktopEnvironment.owner_user_id == owner_user_id)
            .order_by(DesktopEnvironment.created_at)
        )
    )


def get_environment(db: Session, owner_user_id: uuid.UUID, environment_id: uuid.UUID) -> DesktopEnvironment:
    return _owned_environment(db, owner_user_id, environment_id)


def _owned_environment(
    db: Session, owner_user_id: uuid.UUID, environment_id: uuid.UUID, *, lock: bool = False
) -> DesktopEnvironment:
    stmt = select(DesktopEnvironment).where(
        DesktopEnvironment.id == environment_id, DesktopEnvironment.owner_user_id == owner_user_id
    )
    env = db.scalar(stmt.with_for_update() if lock else stmt)
    if env is None:
        raise _not_found("environment")
    return env


def register_environment(db: Session, owner_user_id: uuid.UUID, data: EnvironmentCreate) -> DesktopEnvironment:
    """Stage A only: the owner registers an environment directly. Stage B
    replaces this with device pairing, which also issues the controller's
    credential."""
    allowed = list(dict.fromkeys(data.allowed_profile_ids))
    for profile_id in allowed:
        get_profile(profile_id)
    env = DesktopEnvironment(
        owner_user_id=owner_user_id,
        display_name=data.display_name,
        guest_os=data.guest_os,
        allowed_profile_ids=allowed,
    )
    db.add(env)
    db.commit()
    return env


def revoke_environment(db: Session, owner_user_id: uuid.UUID, environment_id: uuid.UUID) -> DesktopEnvironment:
    """Cut the environment off and end every live session on it. Idempotent."""
    env = _owned_environment(db, owner_user_id, environment_id, lock=True)
    if env.revoked_at is None:
        env.revoked_at = _now()
    env.status = "offline"
    # The device key stops working at once, and the relay drops a connected
    # controller at its next heartbeat (it re-reads this row every time).
    env.controller_token_hash = None
    env.controller_generation += 1
    env.capture_gate = "blocked"
    env.capture_epoch += 1
    for pairing in db.scalars(
        select(DesktopPairing).where(DesktopPairing.environment_id == env.id, DesktopPairing.consumed_at.is_(None))
    ):
        pairing.code = None
        pairing.expires_at = _now()
    ended = []
    for session in _live_sessions_locked(db, env.id):
        _transition(session, "ended", reason="environment_revoked", db=db)
        ended.append(session)
    db.commit()
    desktop_preview.clear(env.id)
    for session in ended:
        _stop_input(session)
    return env


# --------------------------------------------------------------------------- #
# Session reads
# --------------------------------------------------------------------------- #


def get_session(db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID) -> DesktopSession:
    session = db.scalar(
        select(DesktopSession).where(DesktopSession.id == session_id, DesktopSession.owner_user_id == owner_user_id)
    )
    if session is None:
        raise _not_found("session")
    return session


def list_sessions(
    db: Session, owner_user_id: uuid.UUID, *, live_only: bool = False, limit: int = 20
) -> list[DesktopSession]:
    stmt = select(DesktopSession).where(DesktopSession.owner_user_id == owner_user_id)
    if live_only:
        stmt = stmt.where(DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))
    return list(db.scalars(stmt.order_by(DesktopSession.started_at.desc()).limit(limit)))


def list_events(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, *, after_id: int = 0, limit: int = 50
) -> list[DesktopSessionEvent]:
    get_session(db, owner_user_id, session_id)
    return list(
        db.scalars(
            select(DesktopSessionEvent)
            .where(DesktopSessionEvent.session_id == session_id, DesktopSessionEvent.id > after_id)
            .order_by(DesktopSessionEvent.id)
            .limit(limit)
        )
    )


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #


def start_session(db: Session, owner_user_id: uuid.UUID, data: SessionCreate) -> tuple[DesktopSession, bool]:
    """Acquire the environment and create a session in ``starting``.

    Returns ``(session, created)``; ``created`` is False when an idempotency
    key matched an earlier identical request. The session waits in
    ``starting`` until the controller reports ready (``controller_ready``).
    """
    settings = get_settings()
    request_hash = hashlib.sha256(data.model_dump_json(exclude={"idempotency_key"}).encode()).hexdigest()
    if data.idempotency_key:
        existing = _by_idempotency_key(db, owner_user_id, data.idempotency_key)
        if existing is not None:
            return _replayed(existing, request_hash), False

    env = _owned_environment(db, owner_user_id, data.environment_id)
    if env.revoked_at is not None:
        raise DesktopError("not_authorized", 403, "this environment's access was revoked")
    if data.profile_id not in env.allowed_profile_ids:
        raise DesktopError("not_authorized", 403, f"profile {data.profile_id!r} is not allowed on this environment")
    profile = get_profile(data.profile_id)
    if data.conversation_id is not None:
        owned = db.scalar(
            select(Conversation.id).where(
                Conversation.id == data.conversation_id, Conversation.user_id == owner_user_id
            )
        )
        if owned is None:
            raise _not_found("conversation")
    max_seconds = data.max_duration_seconds or settings.desktop_max_session_seconds
    if max_seconds > settings.desktop_max_session_seconds:
        raise DesktopError(
            "limit_reached", 422, f"sessions can last at most {settings.desktop_max_session_seconds} seconds"
        )

    session = DesktopSession(
        id=uuid.uuid4(),
        owner_user_id=owner_user_id,
        environment_id=env.id,
        conversation_id=data.conversation_id,
        profile_id=profile.id,
        profile_revision=profile.revision,
        state="starting",
        state_revision=1,
        control_owner="none",
        execution_generation=1,
        plan_revision=0,
        next_step_number=1,
        commentary_mode=data.commentary_mode,
        max_duration_seconds=max_seconds,
        idempotency_key=data.idempotency_key,
        request_hash=request_hash,
    )
    db.add(session)
    _record(db, session, "desktop.session_state", {"state": "starting", "previous_state": None, "reason": None})
    if data.plan is not None:
        _set_plan(db, session, data.plan, source="user_api")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if data.idempotency_key:
            # A concurrent retry with the same key won the insert. Checked for
            # either constraint: Postgres may report the environment index
            # first even when the key is what collided.
            existing = _by_idempotency_key(db, owner_user_id, data.idempotency_key)
            if existing is not None:
                return _replayed(existing, request_hash), False
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_desktop_sessions_active_environment":
            raise DesktopError("already_owned", 409, "this environment already has a live session") from None
        raise
    return session, True


def _by_idempotency_key(db: Session, owner_user_id: uuid.UUID, key: str) -> DesktopSession | None:
    return db.scalar(
        select(DesktopSession).where(
            DesktopSession.owner_user_id == owner_user_id, DesktopSession.idempotency_key == key
        )
    )


def _replayed(existing: DesktopSession, request_hash: str) -> DesktopSession:
    if existing.request_hash != request_hash:
        raise DesktopError(
            "idempotency_conflict", 409, "this idempotency key was already used for a different request"
        )
    return existing


# --------------------------------------------------------------------------- #
# User lifecycle commands
# --------------------------------------------------------------------------- #


def pause_session(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, *, reason: str | None = None
) -> CommandResult:
    """Stop input and hold. Idempotent: pausing a paused session changes nothing."""
    session = _locked(db, owner_user_id, session_id)
    if session.state == "paused":
        db.commit()
        return CommandResult(session)
    _require_live(session)
    if session.state not in _PAUSABLE:
        raise _conflict(session, "pause")
    had_control = session.control_owner == "gameplay"
    _transition(session, "paused", reason=reason or "user_pause", db=db)
    db.commit()
    _stop_input(session)
    return CommandResult(session, release_pending=had_control)


def takeover_session(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, *, reason: str | None = None
) -> CommandResult:
    """Hand input to the user. Gameplay is fenced off (new generation) before
    manual control is recorded, so automation can't act after the handover."""
    session = _locked(db, owner_user_id, session_id)
    if session.state == "manual":
        db.commit()
        return CommandResult(session)
    _require_live(session)
    if session.state not in _TAKEOVER_FROM:
        raise _conflict(session, "take over")
    had_control = session.control_owner == "gameplay"
    _transition(session, "manual", reason=reason or "user_takeover", db=db)
    db.commit()
    _stop_input(session)
    return CommandResult(session, release_pending=had_control)


def resume_session(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, *, expected_state_revision: int
) -> CommandResult:
    """Explicitly give control back to gameplay.

    The expected revision is what makes "End session" win a race: an end that
    commits first bumps the revision, so a resume built on the old snapshot
    fails instead of reviving the session.
    """
    session = _locked(db, owner_user_id, session_id)
    _require_live(session)
    if session.state_revision != expected_state_revision:
        raise DesktopError(
            "stale_revision", 409, "the session changed since you last looked; refresh and try again"
        )
    if session.state == "running":
        db.commit()
        return CommandResult(session)
    if session.state not in _RESUMABLE:
        raise _conflict(session, "resume")
    # Not locked: lock order is always environment -> session (revoke, offline),
    # and either of those committing after this read still waits on the
    # session row and then ends/disconnects it.
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == session.environment_id))
    if env is None or env.revoked_at is not None:
        raise DesktopError("not_authorized", 403, "this environment's access was revoked")
    if env.status != "online":
        raise DesktopError("environment_offline", 409, "the game VM's controller is not connected")
    _transition(session, "running", reason="user_resume", db=db)
    db.commit()
    from app.services import desktop_commands

    desktop_commands.publish_refresh(session.environment_id)
    return CommandResult(session)


def end_session(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, *, reason: str | None = None
) -> CommandResult:
    """Terminal stop. Idempotent, and nothing can resume an ended session."""
    session = _locked(db, owner_user_id, session_id)
    if session.state in DESKTOP_TERMINAL_STATES:
        db.commit()
        return CommandResult(session)
    had_control = session.control_owner == "gameplay"
    _transition(session, "ended", reason=reason or "user_end", db=db)
    db.commit()
    _stop_input(session)
    return CommandResult(session, release_pending=had_control)


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


def replace_plan(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, data: PlanReplace
) -> DesktopSession:
    """New goal or stuck recovery: bump ``plan_revision`` and fence input, so
    decisions made for the old plan cannot run."""
    session = _locked(db, owner_user_id, session_id)
    _require_live(session)
    _check_plan_revision(session, data.expected_plan_revision)
    _set_plan(db, session, data, source=data.source)
    db.commit()
    _stop_input(session, "plan_replaced")
    return session


def append_plan_steps(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, data: PlanAppend
) -> DesktopSession:
    """Plan ahead without disturbing the driver: no revision bump, no fence."""
    session = _locked(db, owner_user_id, session_id)
    _require_live(session)
    if session.plan is None:
        raise DesktopError("no_plan", 409, "this session has no plan to append to")
    _check_plan_revision(session, data.expected_plan_revision)
    plan = dict(session.plan)
    open_steps = sum(1 for step in plan["steps"] if step["state"] in _OPEN_STEP_STATES)
    _check_step_count(open_steps + len(data.steps))
    new_steps = _number_steps(session, data.steps)
    plan["steps"] = [*plan["steps"], *new_steps]
    session.plan = plan  # reassign: JSONB columns don't track in-place mutation
    _record(db, session, "desktop.plan_appended", {
        "plan_revision": session.plan_revision, "step_ids": [step["id"] for step in new_steps],
    })
    db.commit()
    return session


def _set_plan(db: Session, session: DesktopSession, data: PlanIn, *, source: str) -> None:
    _check_step_count(len(data.steps))
    abandoned = 0
    if session.plan is not None:
        abandoned = sum(1 for step in session.plan["steps"] if step["state"] in _OPEN_STEP_STATES)
    session.plan = {
        "goal": data.goal,
        "steps": _number_steps(session, data.steps),
        "if_stuck": data.if_stuck,
        "constraints": list(data.constraints),
        "source": source,
    }
    session.plan_revision += 1
    session.current_step_id = None
    if session.plan_revision > 1:
        # Replacing (not the first plan) invalidates in-flight decisions.
        session.execution_generation += 1
    _record(db, session, "desktop.plan_set", {
        "plan_revision": session.plan_revision, "abandoned_steps": abandoned, "source": source,
    })


def _number_steps(session: DesktopSession, steps) -> list[dict]:
    """Step ids are the service's, never the model's: ``s1``, ``s2``, ... and
    never reused within a session, so an append can't collide."""
    numbered = []
    for step in steps:
        numbered.append({"id": f"s{session.next_step_number}", "do": step.do,
                         "done_when": step.done_when, "state": "pending"})
        session.next_step_number += 1
    return numbered


def _check_plan_revision(session: DesktopSession, expected: int) -> None:
    if session.plan_revision != expected:
        raise DesktopError(
            "stale_plan_revision", 409,
            f"the plan is at revision {session.plan_revision}, not {expected}; it was replaced meanwhile",
        )


def _check_step_count(count: int) -> None:
    limit = get_settings().desktop_plan_max_steps
    if count > limit:
        raise DesktopError("plan_too_long", 422, f"a plan can have at most {limit} open steps")


# --------------------------------------------------------------------------- #
# Trusted controller/worker side (no route exposes these)
# --------------------------------------------------------------------------- #


def controller_online(db: Session, environment_id: uuid.UUID, *, controller_generation: int) -> DesktopEnvironment:
    """The controller connected and authenticated. Marks health only: no
    session moves because of this (reconnect never auto-resumes)."""
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == environment_id).with_for_update())
    if env is None or env.revoked_at is not None:
        raise DesktopError("not_authorized", 403, "unknown or revoked environment")
    env.status = "online"
    env.controller_generation = controller_generation
    env.last_heartbeat_at = _now()
    db.commit()
    return env


def controller_offline(
    db: Session,
    environment_id: uuid.UUID,
    *,
    reason: str = "controller_lost",
    controller_generation: int | None = None,
) -> bool:
    """Connection or lease lost: sessions that had (or were about to get)
    gameplay control become ``disconnected`` and stay there.

    With ``controller_generation``, only that connection may mark the
    environment offline: an old socket closing after a newer one connected
    must not knock the new one offline. Returns whether anything changed.
    """
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == environment_id).with_for_update())
    if env is None or (controller_generation is not None and env.controller_generation != controller_generation):
        db.commit()
        return False
    env.status = "offline"
    disconnect_sessions_locked(db, env.id, reason=reason, states=_DISCONNECTABLE)
    db.commit()
    return True


def disconnect_sessions_locked(db: Session, environment_id: uuid.UUID, *, reason: str, states=_DISCONNECTABLE) -> None:
    """Move this environment's live sessions in ``states`` to ``disconnected``.
    The caller holds the environment row lock and commits."""
    for session in _live_sessions_locked(db, environment_id):
        if session.state in states:
            _transition(session, "disconnected", reason=reason, db=db)


def controller_ready(db: Session, session_id: uuid.UUID) -> DesktopSession:
    """The controller confirmed the target and accepted a lease: ``starting``
    becomes ``running``. Only the first start uses this; later returns to
    ``running`` go through an explicit user resume."""
    session = db.scalar(select(DesktopSession).where(DesktopSession.id == session_id).with_for_update())
    if session is None:
        raise _not_found("session")
    if session.state != "starting":
        raise _conflict(session, "mark ready")
    env = db.scalar(select(DesktopEnvironment).where(DesktopEnvironment.id == session.environment_id))
    if env is None or env.status != "online" or env.revoked_at is not None:
        raise DesktopError("environment_offline", 409, "the game VM's controller is not connected")
    _transition(session, "running", reason="controller_ready", db=db)
    db.commit()
    return session


def send_decision(
    db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID, decision: dict, decision_id: str | None = None
) -> str:
    """Stage C: a hand-written decision for the controller (no model yet).

    Only while the session is playing; the controller does the full check
    (profile, current context, frame) and reports the outcome. Here only the
    shape is checked, for a quick, readable refusal.
    """
    from app.services import desktop_commands
    from desktop_controller.validate import parse_decision

    session = get_session(db, owner_user_id, session_id)
    db.commit()
    if session.state != "running" or session.control_owner != "gameplay":
        raise DesktopError("invalid_state", 409, "start or resume the session before sending input")
    parsed = parse_decision(decision)
    if isinstance(parsed, Rejection):
        raise DesktopError("invalid_decision", 422, f"{parsed.field}: {parsed.reason}")
    if not desktop_commands.available():
        raise DesktopError("unavailable", 503, "sending input needs Redis (REDIS_URL)")
    decision_id = decision_id or uuid.uuid4().hex[:16]
    desktop_commands.publish_decision(
        session.environment_id, session.id, decision_id, session.execution_generation, decision,
    )
    return decision_id


def controller_pause(db: Session, session_id: uuid.UUID, *, reason: str) -> DesktopSession | None:
    """The controller stopped on its own (game window lost, watchdog): a
    playing session pauses, and stays paused until the user resumes."""
    session = db.scalar(select(DesktopSession).where(DesktopSession.id == session_id).with_for_update())
    if session is None or session.state != "running":
        db.commit()
        return session
    _transition(session, "paused", reason=reason, db=db)
    db.commit()
    return session


@dataclass(frozen=True)
class ControlState:
    """What the relay needs about an environment's live session."""

    session_id: uuid.UUID
    state: str
    execution_generation: int
    control_owner: str
    profile_id: str


def control_state(db: Session, environment_id: uuid.UUID) -> ControlState | None:
    session = db.scalar(
        select(DesktopSession).where(
            DesktopSession.environment_id == environment_id, DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES)
        )
    )
    db.commit()
    if session is None:
        return None
    return ControlState(
        session.id, session.state, session.execution_generation, session.control_owner, session.profile_id
    )


def expire_overdue(db: Session, *, now: datetime | None = None) -> int:
    """End live sessions past their ``max_duration_seconds``. Returns how many."""
    now = now or _now()
    candidates = db.scalars(
        select(DesktopSession)
        .where(DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))
        .with_for_update(skip_locked=True)
    ).all()
    ended = 0
    for session in candidates:
        if session.started_at + timedelta(seconds=session.max_duration_seconds) <= now:
            _transition(session, "ended", reason="limit_reached", db=db)
            ended += 1
    db.commit()
    return ended


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _stop_input(session: DesktopSession, reason: str | None = None) -> None:
    """Tell the controller to let go now, after the stop is committed. Best
    effort: the next heartbeat reply carries no lease anyway."""
    from app.services import desktop_commands

    desktop_commands.publish_cancel(session.environment_id, session.id, reason or session.reason_code or "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _locked(db: Session, owner_user_id: uuid.UUID, session_id: uuid.UUID) -> DesktopSession:
    session = db.scalar(
        select(DesktopSession)
        .where(DesktopSession.id == session_id, DesktopSession.owner_user_id == owner_user_id)
        .with_for_update()
    )
    if session is None:
        raise _not_found("session")
    return session


def _live_sessions_locked(db: Session, environment_id: uuid.UUID) -> list[DesktopSession]:
    return list(
        db.scalars(
            select(DesktopSession)
            .where(
                DesktopSession.environment_id == environment_id,
                DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES),
            )
            .with_for_update()
        )
    )


def _require_live(session: DesktopSession) -> None:
    if session.state in DESKTOP_TERMINAL_STATES:
        raise DesktopError("session_ended", 409, "this session has ended; start a new one")


def _conflict(session: DesktopSession, verb: str) -> DesktopError:
    return DesktopError("invalid_state", 409, f"can't {verb} a session that is {session.state}")


_OWNER_BY_STATE = {"running": "gameplay", "manual": "manual"}


def _transition(session: DesktopSession, to_state: str, *, reason: str, db: Session) -> None:
    previous = session.state
    session.state = to_state
    session.reason_code = reason
    session.state_revision += 1
    session.control_owner = _OWNER_BY_STATE.get(to_state, "none")
    if to_state != "running":
        # Every move away from gameplay fences off decisions already in flight.
        session.execution_generation += 1
    if to_state in DESKTOP_TERMINAL_STATES:
        session.ended_at = _now()
    _record(db, session, "desktop.session_state", {"state": to_state, "previous_state": previous, "reason": reason})


def _record(db: Session, session: DesktopSession, event_type: str, payload: dict) -> None:
    db.add(DesktopSessionEvent(
        session_id=session.id,
        type=event_type,
        state_revision=session.state_revision,
        payload={**payload, "execution_generation": session.execution_generation},
    ))
