"""Teaching herself a move in a game (stage H1): who may start it, the record, and the worker.

Her hands report "no skill for: …" when a step needs a move she doesn't have.
She can then offer to learn it, and only the user can say go:

- ``offer`` (background turns, ``offer_to_learn``): she asks the user and the
  offer is remembered for a while, so a later "yes" knows what it's about;
- ``start`` (only in a turn where the user spoke, ``learn_skill``): queues an
  attempt. Which tools a turn gets is decided by code (tools/desktop), so a
  background turn, a game event or a thought can never start one;
- ``stop`` (``stop_learning``, or pausing/ending the session): ends it.

While an attempt is queued or running her hands hold (``holding``), so her body
does one thing at a time; her mind and mouth carry on. The ``game-practice``
worker thread (``run_once``) runs one attempt at a time through
``app.ai.desktop.practice``: write a program, run it in the pack's sandbox,
count what changed, let a critic judge, retry. Every try is saved as it ends
(``game_skill_attempts``), with the code, so the user can read what she wrote
(``scripts/learning_attempts.py``). How it ended becomes a ``learning`` game
event: her mind sees it in its tick (and decides whether to think about it),
or without the mind it wakes her to tell the user.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession, GameSkillAttempt
from app.db.session import SessionLocal
from app.services.desktop_sessions import DesktopError

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
TASK_MAX_CHARS = 200
OFFER_TTL_SECONDS = 20 * 60
RECENT_SECONDS = 15 * 60  # an attempt that ended this recently is still in her summary
TICK_NEWS_SECONDS = 120  # ...and in her mind's tick
STALE_RUNNING = timedelta(minutes=15)  # a running attempt nobody updated this long was cut off (a restart)


def enabled() -> bool:
    settings = get_settings()
    return settings.game_practice_enabled and settings.desktop_agent_enabled


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(seconds: float) -> str:
    from app.services.desktop_game import _ago as ago

    return ago(seconds)


# --------------------------------------------------------------------------- #
# The user's pack session
# --------------------------------------------------------------------------- #


def _pack_session(db: Session, user_id: uuid.UUID) -> DesktopSession:
    """The user's live session with her own player (a game pack)."""
    from app.services import game_pack

    session = db.scalar(
        select(DesktopSession)
        .join(DesktopEnvironment, DesktopEnvironment.id == DesktopSession.environment_id)
        .where(DesktopSession.owner_user_id == user_id, DesktopEnvironment.hosting_kind == "game_pack",
               DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))
        .order_by(DesktopSession.started_at.desc())
        .limit(1)
    )
    if session is None or not game_pack.is_pack_profile(session.profile_id):
        raise DesktopError("no_session", 409, "you're not playing as your own player right now; start first")
    return session


def _game(session: DesktopSession) -> str:
    from app.services.game_learning import game_for_profile

    return game_for_profile(session.profile_id) or "minecraft"


def _clean_task(task: str) -> str:
    task = " ".join(task.split())[:TASK_MAX_CHARS]
    if not task:
        raise DesktopError("bad_task", 400, "say what to learn, e.g. 'cross a river by placing blocks'")
    return task


# --------------------------------------------------------------------------- #
# Offers (Redis, short-lived)
# --------------------------------------------------------------------------- #


def _offer_key(session_id: uuid.UUID) -> str:
    return f"game:practice:offer:{session_id}"


def _redis():
    from app.services.desktop_preview import redis_client

    return redis_client()


def pending_offer(session_id: uuid.UUID) -> dict | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(_offer_key(session_id))
        return json.loads(raw) if raw else None
    except (redis.RedisError, ValueError):
        return None


def offer(db: Session, user_id: uuid.UUID, *, task: str) -> str:
    """Remember that she offered to learn ``task`` (she asks the user herself)."""
    if not enabled():
        raise DesktopError("unavailable", 503, "learning new moves is switched off")
    session = _pack_session(db, user_id)
    db.commit()
    task = _clean_task(task)
    if active(db, session.id) is not None:
        raise DesktopError("busy", 409, "you're already learning something")
    client = _redis()
    if client is None:
        raise DesktopError("unavailable", 503, "offers can't be kept right now (no Redis)")
    try:
        client.set(_offer_key(session.id), json.dumps({"task": task, "at": time.time()}), ex=OFFER_TTL_SECONDS)
    except redis.RedisError as exc:
        raise DesktopError("unavailable", 503, "offers can't be kept right now") from exc
    return task


def _drop_offer(session_id: uuid.UUID) -> None:
    client = _redis()
    if client is not None:
        try:
            client.delete(_offer_key(session_id))
        except redis.RedisError:
            pass


# --------------------------------------------------------------------------- #
# Start, stop, and what's going on
# --------------------------------------------------------------------------- #


def active(db: Session, session_id: uuid.UUID) -> GameSkillAttempt | None:
    return db.scalar(select(GameSkillAttempt).where(
        GameSkillAttempt.session_id == session_id, GameSkillAttempt.status.in_(ACTIVE)))


def holding(db: Session, session_id: uuid.UUID) -> bool:
    """Her hands hold while she learns (her body does one thing at a time)."""
    if not enabled():
        return False
    held = active(db, session_id) is not None
    db.commit()
    return held


def start(db: Session, user_id: uuid.UUID, *, task: str, origin: str = "user") -> str:
    """Queue an attempt. ``origin``: "user" (a turn where the user spoke) or "self" (her own
    background turn: since 2026-10-18 she may start lessons herself, unless
    GAME_PRACTICE_ASK_FIRST). A goal from the user stops a lesson of her own (``stop_own``)."""
    if not enabled():
        raise DesktopError("unavailable", 503, "learning new moves is switched off")
    task = _clean_task(task)
    session = _pack_session(db, user_id)
    current = active(db, session.id)
    if current is not None:
        db.commit()
        raise DesktopError("busy", 409, f'you\'re already learning "{current.task}"; stop_learning first')
    offered = pending_offer(session.id)
    attempt = GameSkillAttempt(user_id=user_id, session_id=session.id, game=_game(session), task=task,
                               origin="offer" if offered else origin)
    db.add(attempt)
    db.commit()
    _drop_offer(session.id)
    wake()
    settings = get_settings()
    waiting = ""
    if session.state != "running":
        from app.services.desktop_game import _unpause

        # Getting stuck ("no skill for: …") paused her: the user's yes to learning is a yes to go
        # on; learning on her own lifts only her own pauses.
        waiting = _unpause(db, user_id, "columbina_user_turn" if origin == "user" else "columbina_planning_turn")
        if "still paused" in waiting:
            waiting += " Learning starts once you're playing again."
    return (f'Started learning: "{task}". You write a program from your skills, run it, check it and fix it, '
            f"up to {settings.game_practice_max_tries} tries; your hands wait meanwhile.{waiting}")


def stop_own(db: Session, user_id: uuid.UUID) -> str:
    """The user gave her a goal: a lesson she started herself stops (one they asked for goes on).
    Returns a note for her tool result, or ""."""
    try:
        session = _pack_session(db, user_id)
    except DesktopError:
        return ""
    attempt = active(db, session.id)
    if attempt is None or attempt.origin != "self":
        db.commit()
        return ""
    task = attempt.task
    stop(db, user_id)
    return f' (Stopped teaching yourself "{task}": the user\'s goal comes first.)'


def stop(db: Session, user_id: uuid.UUID) -> str:
    session = _pack_session(db, user_id)
    attempt = active(db, session.id)
    if attempt is None:
        db.commit()
        _drop_offer(session.id)
        return "You weren't learning anything."
    if attempt.status == "queued":
        attempt.status, attempt.result, attempt.ended_at = "stopped", "stopped before it began", _now()
    else:
        attempt.stop_requested = True
    db.commit()
    from app.services import game_pack

    game_pack.stop_driver_skill()  # the program and the skill it's running stop at once
    return f'Stopped learning "{attempt.task}". Your hands carry on.'


def _recent(db: Session, session_id: uuid.UUID, within: float) -> GameSkillAttempt | None:
    return db.scalar(
        select(GameSkillAttempt)
        .where(GameSkillAttempt.session_id == session_id, GameSkillAttempt.ended_at.is_not(None),
               GameSkillAttempt.ended_at >= _now() - timedelta(seconds=within))
        .order_by(GameSkillAttempt.ended_at.desc()).limit(1)
    )


def last_result(db: Session, session_id: uuid.UUID, within: float = 120.0) -> dict | None:
    """How the lesson that last held her hands ended, for her hands: {task, status, result}."""
    attempt = _recent(db, session_id, within)
    db.commit()
    if attempt is None:
        return None
    text = _ended_text(attempt)
    if attempt.status == "learned" and attempt.result:
        text += f". What it did: {attempt.result}"
    return {"task": attempt.task, "status": attempt.status, "result": text[:600]}


def _ended_text(attempt: GameSkillAttempt) -> str:
    tries = len(attempt.tries or [])
    if attempt.status == "learned":
        from app.services.game_skills import name_for

        return (f'learned how to "{attempt.task}" ({tries} {"try" if tries == 1 else "tries"}); your hands have it '
                f'now as the skill {name_for(attempt.task)}')
    if attempt.status == "stopped":
        return f'stopped learning "{attempt.task}" ({attempt.result})'
    return f'couldn\'t work out how to "{attempt.task}": {attempt.result}'


def summary_lines(db: Session, session: DesktopSession, now: float) -> list[str]:
    """For her game summary (the mouth): an offer waiting for an answer, an attempt going on,
    or one that just ended."""
    if not enabled() or session.state in DESKTOP_TERMINAL_STATES:
        return []
    lines = []
    attempt = active(db, session.id)
    if attempt is not None:
        tries = len(attempt.tries or [])
        if attempt.status == "queued":
            lines.append(f'You\'re about to teach yourself: "{attempt.task}" (starts as soon as you\'re playing).')
        else:
            started = attempt.started_at.timestamp() if attempt.started_at else now
            lines.append(f'You\'re teaching yourself: "{attempt.task}" (try {tries + 1} of '
                         f"{get_settings().game_practice_max_tries}, started {_ago(now - started)}). Your hands wait "
                         "until it's done; stop_learning if the user wants you to stop.")
            if attempt.tries:
                lines.append(f"Last try: {attempt.tries[-1]['verdict']['reason']}")
    else:
        offered = pending_offer(session.id)
        if offered:
            lines.append(f'You offered to learn: "{offered["task"]}" ({_ago(now - offered["at"])}). '
                         "If the user says yes, start it with learn_skill.")
        recent = _recent(db, session.id, RECENT_SECONDS)
        if recent is not None:
            lines.append(f"You {_ended_text(recent)} ({_ago(now - recent.ended_at.timestamp())}).")
    db.commit()
    return lines


def tick_line(db: Session, session_id: uuid.UUID) -> str | None:
    """For her mind's tick: what she's learning (it changes each try, so each try is news),
    or how it just ended."""
    if db is None or not enabled():
        return None
    attempt = active(db, session_id)
    if attempt is not None:
        text = (f'about to learn "{attempt.task}"' if attempt.status == "queued" else
                f'learning "{attempt.task}": try {len(attempt.tries or []) + 1} of {get_settings().game_practice_max_tries}')
    else:
        recent = _recent(db, session_id, TICK_NEWS_SECONDS)
        text = f"you {_ended_text(recent)}" if recent is not None else None
    db.commit()
    return text


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #


def wake() -> None:
    url = get_settings().redis_url
    if not url:
        return
    try:
        redis.from_url(url).rpush(get_settings().game_practice_queue_name, "1")
    except redis.RedisError:
        pass  # the poll finds it anyway


def _claim(db: Session) -> GameSkillAttempt | None:
    """Tidy up, then take the oldest queued attempt whose session is playing."""
    now = _now()
    for stale in db.scalars(select(GameSkillAttempt).where(
            GameSkillAttempt.status == "running", GameSkillAttempt.updated_at < now - STALE_RUNNING)):
        stale.status, stale.result, stale.ended_at = "failed", "cut off (the worker restarted)", now
    for orphan in db.scalars(
            select(GameSkillAttempt).join(DesktopSession, DesktopSession.id == GameSkillAttempt.session_id, isouter=True)
            .where(GameSkillAttempt.status == "queued",
                   (DesktopSession.id.is_(None)) | DesktopSession.state.in_(DESKTOP_TERMINAL_STATES))):
        orphan.status, orphan.result, orphan.ended_at = "stopped", "the session ended", now
    db.commit()
    attempt = db.scalar(
        select(GameSkillAttempt)
        .join(DesktopSession, DesktopSession.id == GameSkillAttempt.session_id)
        .where(GameSkillAttempt.status == "queued", DesktopSession.state == "running",
               DesktopSession.control_owner == "gameplay")
        .order_by(GameSkillAttempt.created_at)
        .limit(1)
        .with_for_update(of=GameSkillAttempt, skip_locked=True)
    )
    if attempt is None:
        db.commit()
        return None
    attempt.status, attempt.started_at = "running", now
    db.commit()
    return attempt


def _should_stop(attempt_id: uuid.UUID, session_id: uuid.UUID) -> str | None:
    with SessionLocal() as db:
        row = db.get(GameSkillAttempt, attempt_id)
        session = db.get(DesktopSession, session_id)
        db.commit()
    if row is None or row.stop_requested:
        return "the user asked you to stop"
    if session is None or session.state != "running" or session.control_owner != "gameplay":
        return f"the session is {session.state if session else 'gone'}"
    return None


def _save_tries(attempt_id: uuid.UUID, tries: list[dict]) -> None:
    with SessionLocal() as db:
        row = db.get(GameSkillAttempt, attempt_id)
        if row is not None:
            row.tries = json.loads(json.dumps(tries, default=str))
            db.commit()


def _model():
    from app.ai.desktop.driver import DriverModelError, OpenAIDriver

    settings = get_settings()
    if not settings.openai_api_key:
        raise DriverModelError("OPENAI_API_KEY is not set")
    return OpenAIDriver(settings.game_practice_model, settings.openai_api_key,
                        timeout_seconds=120.0, max_tokens=settings.game_practice_max_tokens, image_detail="low",
                        reasoning_effort=settings.game_practice_reasoning_effort)


def run_once(*, pack=None, model=None) -> bool:
    """Run at most one attempt to its end (minutes). True if one ran."""
    if not enabled():
        return False
    with SessionLocal() as db:
        attempt = _claim(db)
        if attempt is None:
            return False
        attempt_id, session_id, task = attempt.id, attempt.session_id, attempt.task
    log.info("learning %r for session %s", task, session_id)
    from app.ai.desktop.driver import DriverModelError
    from app.ai.desktop.pack_client import PackClient
    from app.ai.desktop.practice import PracticeDeps, PracticeResult, PracticeRun

    settings = get_settings()
    try:
        model = model or _model()
        run = PracticeRun(task, PracticeDeps(
            pack=pack or PackClient.from_settings(), model=model, critic=model,
            should_stop=lambda: _should_stop(attempt_id, session_id),
            save=lambda tries: _save_tries(attempt_id, tries),
            metric=lambda record: log.info("practice %s", record),
        ), max_tries=settings.game_practice_max_tries, program_seconds=settings.game_practice_program_seconds)
        result = run.run()
    except DriverModelError as exc:
        result = PracticeResult("failed", f"couldn't think it through: {exc}")
    except Exception as exc:  # noqa: BLE001 - an attempt must never take the worker down
        log.exception("learning %r crashed", task)
        result = PracticeResult("failed", f"something broke: {type(exc).__name__}")
    _finish(attempt_id, session_id, result)
    if result.status == "learned" and settings.game_practice_tidy_enabled:
        _tidy(attempt_id, task, result, pack=pack, model=model)
    return True


def _tidy(attempt_id: uuid.UUID, task: str, result, *, pack=None, model=None) -> None:
    """After she's told the user: a tidied copy of the move she just learned (game_skills
    keeps the original until the copy has worked). Never fails the lesson."""
    from app.ai.desktop.pack_client import PackClient
    from app.ai.desktop.practice import tidy
    from app.services import game_skills

    try:
        manifest = (pack or PackClient.from_settings()).manifest()
        tidied = tidy(model or _model(), manifest, task, result.code, result.tries[-1] if result.tries else None)
    except Exception as exc:  # noqa: BLE001 - the move works as learned
        log.info("tidying %r skipped: %s", task, exc)
        return
    if tidied is None:
        log.info("tidying %r: already tidy", task)
        return
    with SessionLocal() as db:
        if game_skills.save_tidied(db, attempt_id, tidied.code, tidied.changes):
            db.commit()
            log.info("tidied %r: %s", task, tidied.changes)


def _finish(attempt_id: uuid.UUID, session_id: uuid.UUID, result) -> None:
    from app.services import desktop_sessions

    with SessionLocal() as db:
        row = db.get(GameSkillAttempt, attempt_id)
        if row is None:
            return
        row.status = result.status
        row.result = result.reason[:1000]
        row.code = result.code
        if result.tries:
            row.tries = json.loads(json.dumps(result.tries, default=str))
        row.ended_at = _now()
        if row.status == "learned":
            from app.services import game_skills

            move = game_skills.save_learned(db, row)  # one of her moves now, in any session (H2)
            if move is not None:
                from app.services import game_challenges

                game_challenges.on_learned(db, session_id, move.name)  # learned on the way to a challenge
        db.commit()
        text = _ended_text(row)
        db.commit()
        if result.status != "stopped":
            # Her mind sees it in its tick when it's on; otherwise this wakes her to tell the user.
            desktop_sessions.driver_noticed(db, session_id, kind="learning", text=text)
    log.info("learning %s: %s", result.status, result.reason)
