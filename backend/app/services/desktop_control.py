"""General desktop control: her hands on the paired Windows VM as an ordinary desktop
(docs/GENERAL_DESKTOP_CONTROL_DESIGN.md).

The orchestrator decides every action; this module carries one at a time to the VM and back:

    tool (app/ai/tools/desktop_control) -> act() -> Redis stream desktop:commands:<env>
        -> relay (app/ai/desktop/relay.py) -> controller (desktop_controller.desktop) -> DesktopResult
        -> relay -> Redis desktop:act:<id>:result (+ :shot for a screenshot) -> act() returns

Nothing here is a session: the VM stays connected while its controller is online, and the tools are there
whenever desktop access is (``access_for``). An action that doesn't come back in time is ``unconfirmed``:
it may or may not have happened, and she is told so. Commands are never replayed: one sent while the relay
is away is simply not delivered (and comes back unconfirmed).

Every action is also experience (``remember``): a learning capture record (``desktop_action`` execution,
on the turn's chain, so a retrospective assessment of the exchange sees what her hands did) and an
observation (source ``desktop``: what she did and what she saw in front), which reaches working memory and
learning like any other source's report.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DesktopEnvironment
from app.services import desktop_commands, desktop_preview

log = logging.getLogger(__name__)

SOURCE = "desktop"
RESULT_TTL_SECONDS = 120
# How long an action may sit before the relay drops it unsent (no late input).
DELIVER_WITHIN_SECONDS = 4.0
LAST_SEEN_TTL_SECONDS = 6 * 3600


def result_key(action_id: str) -> str:
    return f"desktop:act:{action_id}:result"


def shot_key(action_id: str) -> str:
    return f"desktop:act:{action_id}:shot"


def _last_seen_key(user_id: uuid.UUID) -> str:
    return f"desktop:seen:{user_id}"


# Her hands' recent actions, for her background mind (services/mind_desktop.py): per user, newest first, short-lived.
TRAIL_KEY = "desktop:trail:{user_id}"
TRAIL_USERS_KEY = "desktop:trail:users"  # sorted set: user id -> when their hands last acted (or a command ended)
LAST_USED_KEY = "desktop:hands:last:{user_id}"  # when her hands last acted (services/coordinator: a wake's reset)
TRAIL_KEPT = 40
TRAIL_TTL_SECONDS = 3600
TYPED_SHOWN = 160
OUTPUT_SHOWN = 600  # of a command's output, the end her background mind reads on the trail
CAPTURE_OUTPUT_CHARS = 4000  # of a command's output, the end kept in the (owner-only) learning capture


# --------------------------------------------------------------------------- #
# Access: is there a desktop for her hands right now?
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DesktopAccess:
    environment_id: uuid.UUID
    display_name: str
    online: bool
    gate: str  # allowed | blocked | manual_auth
    busy: bool  # a game session on the VM holds the keyboard and mouse
    os_name: str | None = None  # what the controller says it runs on ("Linux 7.1.5+kali-arm64"), if it said
    commands: bool = False  # its controller can run commands (0.13.0+, and a shell found there)
    jobs: bool = False  # its controller keeps background commands and says when they end (0.14.0+)

    @property
    def usable(self) -> bool:
        return self.online and self.gate == "allowed" and not self.busy

    def why_not(self) -> str | None:
        if not self.online:
            return f'your desktop "{self.display_name}" isn\'t connected (its controller is offline)'
        if self.gate == "manual_auth":
            return "the user is signing in on your desktop; wait until they're done"
        if self.gate != "allowed":
            return "the user has your view of the desktop switched off"
        if self.busy:
            return "a game session is using your desktop's keyboard and mouse"
        return None

    def prompt_line(self) -> str:
        why = self.why_not()
        state = "connected, and you can see and use it" if why is None else f"not usable right now: {why}"
        what = f"a computer of your own running {self.os_name}" if self.os_name else "a computer of your own"
        return f'Your desktop "{self.display_name}" ({what}): {state}.'


def environment_for(db: Session, user_id: uuid.UUID) -> DesktopEnvironment | None:
    """The user's paired VM (not her Minecraft pack), newest pairing first."""
    return db.scalar(select(DesktopEnvironment).where(
        DesktopEnvironment.owner_user_id == user_id, DesktopEnvironment.revoked_at.is_(None),
        DesktopEnvironment.paired_at.is_not(None), DesktopEnvironment.hosting_kind != "game_pack",
    ).order_by(DesktopEnvironment.paired_at.desc()).limit(1))


def access_for(db: Session, user_id: uuid.UUID) -> DesktopAccess | None:
    """None when desktop access is off or the user has no paired desktop: then no tools and no prompt."""
    if not get_settings().desktop_agent_enabled:
        return None
    env = environment_for(db, user_id)
    if env is None:
        return None
    from app.services import desktop_sessions

    busy = desktop_sessions.control_state(db, env.id) is not None
    reported = env.controller_status if isinstance(env.controller_status, dict) else {}
    commands = reported.get("commands") if isinstance(reported.get("commands"), dict) else {}
    return DesktopAccess(env.id, env.display_name, env.status == "online", env.capture_gate, busy,
                         os_name=(str(reported.get("os") or "").strip() or None) and str(reported["os"])[:80],
                         commands=commands.get("available") is True,
                         jobs=_version(env.controller_version) >= JOBS_SINCE)


JOBS_SINCE = (0, 14, 0)


def _version(value: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value or "").split("+")[0].split(".")[:3])
    except ValueError:
        return ()


# --------------------------------------------------------------------------- #
# One action
# --------------------------------------------------------------------------- #


@dataclass
class Outcome:
    """What became of one action. ``status``: done | failed | refused | unconfirmed."""

    action_id: str
    status: str
    reason: str | None = None
    ran_ms: int = 0
    screen: dict | None = None  # ScreenInfo, for a screenshot
    jpeg: bytes | None = None
    foreground_process: str | None = None
    foreground_title: str | None = None
    exit_code: int | None = None  # a command's (None: it didn't finish, or not a command)
    output: str | None = None  # a command's output, as text
    output_cut: bool = False
    # A background command (0.14.0+): its job id, and whether it's still running (``output``: what it printed so far).
    job: str | None = None
    running: bool | None = None
    environment_id: uuid.UUID | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None


def wait_seconds(action: dict) -> float:
    """How long to wait for an action's result: its own bounded running time plus the round trip."""
    kind = action.get("kind")
    extra = 0.0
    if kind == "keyboard":
        extra = len(action.get("text") or "") * 0.006 + len(action.get("keys") or []) * int(action.get("repeat") or 1) * 0.06
    elif kind == "drag":
        extra = int(action.get("duration_ms") or 400) / 1000
    elif kind == "move":
        extra = int(action.get("hover_ms") or 0) / 1000
    elif kind == "scroll":
        extra = (abs(int(action.get("down") or 0)) + abs(int(action.get("right") or 0))) * 0.02
    elif kind == "run_command":
        extra = float(action.get("timeout_s") or 60) + 5  # the command's own limit, and stopping it
    return 8.0 + extra


JOB_KINDS = ("command_status", "stop_command")


def act(db: Session, user_id: uuid.UUID, action: dict) -> Outcome:
    """Send one action to the user's desktop and wait for what became of it. Never raises."""
    action_id = uuid.uuid4().hex
    access = access_for(db, user_id)
    db.commit()  # don't hold a transaction open while waiting on the VM
    if access is None:
        return Outcome(action_id, "refused", "you have no desktop connected")
    why = access.why_not()
    if why is not None:
        return Outcome(action_id, "refused", why)
    if (action.get("background") or action.get("kind") in JOB_KINDS) and not access.jobs:
        return Outcome(action_id, "refused", "your desktop's controller is too old for background commands "
                                             "(it needs 0.14.0): run it without background")
    client = desktop_preview.redis_client()
    if client is None:
        return Outcome(action_id, "refused", "the desktop link isn't set up here (no Redis)")
    outcome = Outcome(action_id, "unconfirmed", environment_id=access.environment_id)
    try:
        client.xadd(desktop_commands.stream_key(access.environment_id), {
            "kind": "desktop_action", "action_id": action_id, "action": json.dumps(action),
            "deliver_by": str(time.time() + DELIVER_WITHIN_SECONDS),
        }, maxlen=desktop_commands.STREAM_MAXLEN, approximate=True)
        popped = client.blpop([result_key(action_id)], timeout=wait_seconds(action))
    except redis.RedisError as exc:
        log.warning("desktop control: action %s: %s", action_id, exc)
        outcome.reason = "the desktop link failed while sending it"
        outcome.ended_at = datetime.now(timezone.utc)
        return outcome
    outcome.ended_at = datetime.now(timezone.utc)
    if popped is None:
        outcome.reason = "no answer from the desktop in time: it may or may not have happened"
        return outcome
    data = json.loads(popped[1])
    outcome.status = data.get("status") or "unconfirmed"
    outcome.reason = data.get("reason")
    outcome.ran_ms = int(data.get("ran_ms") or 0)
    outcome.screen = data.get("screen")
    outcome.foreground_process = data.get("foreground_process")
    outcome.foreground_title = data.get("foreground_title")
    outcome.exit_code = data.get("exit_code") if isinstance(data.get("exit_code"), int) else None
    outcome.output = data.get("output") if isinstance(data.get("output"), str) else None
    outcome.output_cut = bool(data.get("output_cut"))
    outcome.job = str(data["job"])[:16] if data.get("job") else None
    outcome.running = data.get("running") if isinstance(data.get("running"), bool) else None
    if outcome.status == "done" and action.get("kind") == "screenshot":
        try:
            outcome.jpeg = client.get(shot_key(action_id))
        except redis.RedisError:
            outcome.jpeg = None
        if outcome.jpeg is None or not outcome.screen:
            outcome.status, outcome.reason = "failed", "the picture was lost on the way"
    return outcome


# --- the relay's side --------------------------------------------------------


def store_result(action_id: str, result: dict) -> None:
    client = desktop_preview.redis_client()
    if client is None:
        return
    with client.pipeline() as pipe:
        pipe.rpush(result_key(action_id), json.dumps(result))
        pipe.expire(result_key(action_id), RESULT_TTL_SECONDS)
        pipe.execute()


def store_shot(action_id: str, jpeg: bytes) -> None:
    client = desktop_preview.redis_client()
    if client is not None:
        client.set(shot_key(action_id), jpeg, ex=RESULT_TTL_SECONDS)


# --------------------------------------------------------------------------- #
# Experience: learning capture + observation
# --------------------------------------------------------------------------- #

_CAPTURE_RESULT = {"done": "succeeded", "failed": "failed", "refused": "not_executed", "unconfirmed": "unknown"}


@dataclass(frozen=True)
class TurnRef:
    """The turn an action belongs to (for linking its records), all optional."""

    turn_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None


def _app_changed(user_id: uuid.UUID, process: str | None, title: str | None) -> bool:
    """Whether what's in front differs from what she last saw there (kept a few hours)."""
    if not process and not title:
        return False
    client = desktop_preview.redis_client()
    if client is None:
        return True
    now = f"{process or ''}|{title or ''}"
    try:
        before = client.get(_last_seen_key(user_id))
        client.set(_last_seen_key(user_id), now, ex=LAST_SEEN_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - unknown is "changed": better one memory too many than none
        return True
    return (before.decode() if isinstance(before, bytes) else before) != now


def remember(db: Session, user_id: uuid.UUID, *, tool: str, intended: dict, outcome: Outcome,
             turn: TurnRef, step: int) -> None:
    """Record one action as experience. Never raises (her hands don't fail because memory did)."""
    from app.services import learning_capture, observation

    intended = {"tool": tool, **intended}
    executed = {"status": outcome.status, "ran_ms": outcome.ran_ms, "step": step,
                "foreground": {"process": outcome.foreground_process, "title": outcome.foreground_title}}
    if outcome.screen:
        executed["screen"] = outcome.screen
    if tool in ("run_command", "check_command", "stop_command"):
        executed.update(exit_code=outcome.exit_code, output_cut=outcome.output_cut,
                        output=(outcome.output or "")[-CAPTURE_OUTPUT_CHARS:] or None,
                        job=outcome.job, running=outcome.running)
    if tool == "run_command" and outcome.job and outcome.running:
        _keep_job(user_id, outcome, intended, turn)
    try:
        learning_capture.execution(
            user_id=user_id, kind="desktop_action", executor="hands:desktop",
            result=_CAPTURE_RESULT.get(outcome.status, "unknown"), attempt_id=None,
            execution_id=uuid.uuid5(uuid.NAMESPACE_URL, f"desktop-action:{outcome.action_id}"),
            turn_id=turn.turn_id, conversation_id=turn.conversation_id, intended=intended, executed=executed,
            deviation=outcome.reason if outcome.status != "done" else None,
            started_at=outcome.started_at, ended_at=outcome.ended_at)
    except Exception:  # noqa: BLE001
        log.exception("desktop control: capture of action %s failed", outcome.action_id)
    process, title = outcome.foreground_process, outcome.foreground_title
    payload = {
        "action_id": outcome.action_id, "tool": tool, "status": outcome.status,
        "reason": (outcome.reason or None) and outcome.reason[:300],
        "summary": (_intended_words(tool, intended) + (f" (exit code {outcome.exit_code})"
                    if tool == "run_command" and outcome.exit_code is not None else ""))[:200],
        "foreground_process": (process or None) and process[:64],
        "foreground_title": (title or None) and title[:200],
        "app_changed": _app_changed(user_id, process, title) if outcome.status == "done" else False,
        "turn_id": str(turn.turn_id) if turn.turn_id else None, "step": step,
    }
    _add_to_trail(user_id, tool=tool, intended=intended, outcome=outcome, turn=turn, step=step)
    try:
        with db.begin_nested():
            observation.record(db, observation.Report(
                user_id=user_id, source=SOURCE, source_instance=str(user_id),
                source_event_key=f"action:{outcome.action_id}", kind="desktop_action", payload=payload,
                occurred_at=outcome.ended_at or outcome.started_at, message_id=turn.message_id))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("desktop control: observation of action %s failed", outcome.action_id)


def _add_to_trail(user_id: uuid.UUID, *, tool: str, intended: dict, outcome: Outcome, turn: TurnRef,
                  step: int) -> None:
    """One action on her hands' trail, which her background mind reads while (and just after) they work. Unlike
    memory's line, it carries what she typed (shortened): watching the work means knowing the commands. Never
    raises; no Redis, no trail (her mind simply doesn't see the desktop)."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    at = (outcome.ended_at or outcome.started_at).timestamp()
    entry = {"id": outcome.action_id, "at": at, "tool": tool, "what": trail_words(tool, intended),
             "status": outcome.status, "reason": (outcome.reason or None) and outcome.reason[:200],
             "process": (outcome.foreground_process or None) and outcome.foreground_process[:64],
             "title": (outcome.foreground_title or None) and outcome.foreground_title[:120], "step": step,
             "exit_code": outcome.exit_code, "job": outcome.job, "running": outcome.running,
             "output": ((outcome.output or "").strip()[-OUTPUT_SHOWN:] or None
                        if tool in ("run_command", "check_command", "stop_command") else None),
             "turn": str(turn.turn_id) if turn.turn_id else None,
             "conversation": str(turn.conversation_id) if turn.conversation_id else None,
             "message": str(turn.message_id) if turn.message_id else None}
    key = TRAIL_KEY.format(user_id=user_id)
    try:
        with client.pipeline() as pipe:
            pipe.lpush(key, json.dumps(entry))
            pipe.ltrim(key, 0, TRAIL_KEPT - 1)
            pipe.expire(key, TRAIL_TTL_SECONDS)
            pipe.zadd(TRAIL_USERS_KEY, {str(user_id): at})
            pipe.zremrangebyscore(TRAIL_USERS_KEY, "-inf", at - TRAIL_TTL_SECONDS)
            pipe.set(LAST_USED_KEY.format(user_id=user_id), json.dumps(at), ex=TRAIL_TTL_SECONDS * 24)
            pipe.execute()
    except Exception:  # noqa: BLE001 - her hands don't fail because her mind can't watch
        log.warning("desktop control: trail of action %s not kept", outcome.action_id)


def last_used(user_id: uuid.UUID) -> float | None:
    """When her hands last acted on this user's desktop (an epoch), or None (never, or unreadable). A background
    command ending isn't her using it."""
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.get(LAST_USED_KEY.format(user_id=user_id))
        return float(json.loads(raw)) if raw else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Background commands (0.14.0+): which turn started each, and its end onto the trail
# --------------------------------------------------------------------------- #

JOB_KEY = "desktop:job:{environment_id}:{job}"
JOB_TTL_SECONDS = 6 * 3600  # longer than a job may run (the controller stops it at 4 h)


def job_handle(job: str | None) -> str:
    return f"cmd:{job}"


def _keep_job(user_id: uuid.UUID, outcome: Outcome, intended: dict, turn: TurnRef) -> None:
    client = desktop_preview.redis_client()
    if client is None or outcome.environment_id is None:
        return
    record = {"user": str(user_id), "command": str(intended.get("command") or "")[:TYPED_SHOWN],
              "turn": str(turn.turn_id) if turn.turn_id else None,
              "conversation": str(turn.conversation_id) if turn.conversation_id else None,
              "message": str(turn.message_id) if turn.message_id else None,
              "started": (outcome.started_at).timestamp()}
    try:
        client.set(JOB_KEY.format(environment_id=outcome.environment_id, job=outcome.job), json.dumps(record),
                   ex=JOB_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - its end then reaches her mind without the turn it belongs to
        log.warning("desktop control: background command %s not kept", outcome.job)


def command_finished(db: Session, environment_id: uuid.UUID, done: dict) -> bool:
    """The relay's side: a background command ended on this desktop (``CommandDone``). Its end goes onto her hands'
    trail, as part of the turn that started it, so her background mind sees it (services/mind_desktop.py) and can
    wake her to follow up. Once per job. Returns whether it was recorded."""
    client = desktop_preview.redis_client()
    job = str(done.get("job") or "")[:16]
    if client is None or not job:
        return False
    key = JOB_KEY.format(environment_id=environment_id, job=job)
    raw = client.get(key)
    record = json.loads(raw) if raw else {}
    user_id = _uuid_or_none(record.get("user"))
    if user_id is None:
        env = db.get(DesktopEnvironment, environment_id)
        user_id = env.owner_user_id if env is not None else None
    if user_id is None:
        return False
    if not client.set(f"{key}:ended", "1", nx=True, ex=JOB_TTL_SECONDS):
        return True  # already recorded (a resend after a reconnect)
    code = done.get("exit_code") if isinstance(done.get("exit_code"), int) else None
    ran = int(done.get("ran_s") or 0)
    command = record.get("command") or " ".join(str(done.get("command") or "").split())[:TYPED_SHOWN]
    at = time.time()
    entry = {"id": f"ended:{job}", "at": at, "tool": "command_ended",
             "what": f"background command [{job_handle(job)}] `{command}` ended after {_minutes(ran)}",
             "status": "done" if code is not None else "stopped",
             "reason": (done.get("why") or None) and str(done["why"])[:200], "exit_code": code, "job": job,
             "running": False, "output": (str(done.get("output") or "")).strip()[-OUTPUT_SHOWN:] or None,
             "turn": record.get("turn"), "conversation": record.get("conversation"), "message": record.get("message")}
    trail_key = TRAIL_KEY.format(user_id=user_id)
    with client.pipeline() as pipe:
        pipe.lpush(trail_key, json.dumps(entry))
        pipe.ltrim(trail_key, 0, TRAIL_KEPT - 1)
        pipe.expire(trail_key, TRAIL_TTL_SECONDS)
        pipe.zadd(TRAIL_USERS_KEY, {str(user_id): at})
        pipe.execute()
    from app.services import system_signals
    system_signals.emit(user_id, "hands", "mind", "command_ended")
    return True


def _minutes(seconds: int) -> str:
    return f"{seconds} s" if seconds < 90 else f"{seconds // 60} min"


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def trail_users(since: float) -> list[uuid.UUID]:
    """Users whose hands acted on their desktop since ``since`` (an epoch). Empty without Redis."""
    client = desktop_preview.redis_client()
    if client is None:
        return []
    try:
        raw = client.zrangebyscore(TRAIL_USERS_KEY, since, "+inf")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for value in raw:
        try:
            out.append(uuid.UUID(value.decode() if isinstance(value, bytes) else value))
        except ValueError:
            continue
    return out


def trail(user_id: uuid.UUID) -> list[dict]:
    """Her hands' recent actions on this user's desktop, oldest first."""
    client = desktop_preview.redis_client()
    if client is None:
        return []
    try:
        raw = client.lrange(TRAIL_KEY.format(user_id=user_id), 0, TRAIL_KEPT - 1)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for value in reversed(raw):
        try:
            out.append(json.loads(value))
        except (TypeError, ValueError):
            continue
    return out


def trail_words(tool: str, intended: dict) -> str:
    """What she did, for her own mind watching: memory's words, with what she typed."""
    if tool == "keyboard" and intended.get("text") is not None:
        text = " ".join(str(intended["text"]).split())
        return f'typed "{text[:TYPED_SHOWN]}{"..." if len(text) > TYPED_SHOWN else ""}"'
    if tool == "run_command":
        text = " ".join(str(intended.get("command") or "").split())
        return f'ran `{text[:TYPED_SHOWN]}{"..." if len(text) > TYPED_SHOWN else ""}`'
    return _intended_words(tool, intended)


def _intended_words(tool: str, intended: dict) -> str:
    """A short code-written line of what she meant to do (for her memory; never model text but hers)."""
    if tool == "screenshot":
        return "looked at part of the screen" if intended.get("crop") else "looked at the screen"
    if tool == "run_command":
        return "ran a command" + (" in the background" if intended.get("background") else "")
    if tool in ("check_command", "stop_command"):
        verb = "checked on" if tool == "check_command" else "stopped"
        return f"{verb} background command [cmd:{intended.get('job')}]"
    if tool == "keyboard":
        if intended.get("text") is not None:
            return f"typed {len(intended['text'])} characters"
        return "pressed " + ", ".join(intended.get("keys") or [])
    if tool == "click":
        times = {1: "", 2: "double-", 3: "triple-"}.get(int(intended.get("count") or 1), "")
        return f"{times}clicked ({intended.get('button') or 'left'})"
    return {"move_mouse": "moved the pointer", "drag": "dragged", "scroll": "scrolled"}.get(tool, tool)


def line_for(user_id: uuid.UUID) -> str | None:
    """Her desktop's line for a reply turn's context, or None (no desktop: no tools, no section)."""
    return for_turn(user_id)[0]


def for_turn(user_id: uuid.UUID) -> tuple[str | None, bool, bool]:
    """Her desktop's line for a reply turn's context, whether she can use it now, and whether it can run commands.
    Not usable: the line says why, and she gets neither the desktop tools nor how to use them. No desktop:
    ``(None, False, False)``."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            access = access_for(db, user_id)
            db.commit()
    except Exception:  # noqa: BLE001 - a turn never fails because the desktop check did
        log.exception("desktop control: access check for %s failed", user_id)
        return None, False, False
    if access is None:
        return None, False, False
    return access.prompt_line(), access.usable, access.usable and access.commands
