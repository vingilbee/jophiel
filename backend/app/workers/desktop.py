"""Gameplay worker: one driver per playing session (plan §8.1).

``run_once`` is the runner's poll: it finds sessions that are playing with a
plan and starts a driver thread for any that has none. Each thread holds a
PostgreSQL advisory lock on its session for as long as it drives, so a second
worker process can never drive the same session twice. A driver thread ends
when its session stops playing; stopping is always the session service's
call (pause/end) or the driver pausing it, never the thread just vanishing
with input held: the controller's lease covers that case.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import text

from app.ai.desktop.driver import DriverModelError, build_driver_model
from app.ai.desktop.driver_loop import DriverDeps, DriverRun
from app.ai.desktop.pack_client import PackClient
from app.ai.desktop.skill_driver import SkillDriverDeps, SkillDriverRun
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.services import (desktop_commands, desktop_preview, game_experience, game_pack, game_practice, game_skills,
                          mind_hard)
from app.services import desktop_sessions as sessions

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

_threads: dict[uuid.UUID, threading.Thread] = {}
_metrics_lock = threading.Lock()


def run_once() -> bool:
    if not get_settings().desktop_agent_enabled:
        return False
    with SessionLocal() as db:
        try:
            game_pack.sync(db, PackClient.from_settings())
        except Exception:  # pragma: no cover - the link retries next poll
            log.exception("game pack link failed")
            db.rollback()
        candidates = sessions.driver_candidates(db)
    for session_id in candidates:
        thread = _threads.get(session_id)
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=drive, args=(session_id,), name=f"driver-{session_id.hex[:8]}", daemon=True)
            _threads[session_id] = thread
            thread.start()
    for session_id in [sid for sid, t in _threads.items() if not t.is_alive()]:
        _threads.pop(session_id, None)
    return False


def _lock_key(session_id: uuid.UUID) -> int:
    return int.from_bytes(session_id.bytes[:8], "big", signed=True)


def _db(fn):
    def call(*args, **kwargs):
        with SessionLocal() as db:
            return fn(db, *args, **kwargs)
    return call


def write_metric(record: dict) -> None:
    path = Path(get_settings().desktop_driver_metrics_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": time.time(), **record})
    with _metrics_lock, open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _debug_writer(session_id: uuid.UUID):
    """Saves each step's frame, prompt and reply, only if DESKTOP_DRIVER_DEBUG_DIR is set."""
    configured = get_settings().desktop_driver_debug_dir
    if not configured:
        return None
    folder = Path(configured)
    folder = (folder if folder.is_absolute() else BACKEND_ROOT / folder) / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)

    def write(step: int, prompt: str, output: dict, jpeg: bytes) -> None:
        (folder / f"{step:05d}.jpg").write_bytes(jpeg)
        (folder / f"{step:05d}.json").write_text(json.dumps({"prompt": prompt, "reply": output}, indent=2))

    return write


def _hands_capture(session_id: uuid.UUID):
    """Her hands' records for the learning capture, in this session's owner's name (services/learning_capture.py)."""
    from datetime import datetime, timezone

    from app.db.models import DesktopSession
    from app.services import learning_capture

    owner = _db(lambda db, sid: getattr(db.get(DesktopSession, sid), "owner_user_id", None))(session_id)

    def at(seconds: float | None):
        return datetime.fromtimestamp(seconds, timezone.utc) if seconds is not None else None

    def capture(kind: str, **r) -> None:
        if owner is None:
            return
        if kind == "attempt":
            ctx = learning_capture.Ctx(user_id=owner, attempt_id=r["attempt_id"], trigger="hands_step",
                                       game_session_id=session_id, input_refs=tuple(r.get("input_refs") or ()),
                                       sources=(f"session:{session_id}", *(ref["ref"] for ref in r.get("input_refs") or ()
                                                                          if ref.get("ref", "").startswith("lesson:"))))
            learning_capture.attempt(
                ctx, learner="hands", provider="openai", model=r.get("model"),
                input=r["input"], output=None if r.get("output") is None else {**r["output"], "usage": r.get("usage")},
                status=r["status"], error=r.get("error"), started_at=at(r["asked_at"]),
                produced_at=at(r.get("answered_at")),
                versions={**(r.get("versions") or {}), "code": learning_capture.code_version()}, settings=r.get("settings"),
                blobs=r.get("blobs"))
        else:
            kind = r.get("execution_kind", "skill_run")
            # A record with no decision behind it (code counting a step done) is keyed by what it is about.
            eid = (learning_capture.execution_id_for(None, kind, r["key"]) if r.get("attempt_id") is None and r.get("key")
                   else None)
            learning_capture.execution(
                user_id=owner, kind=kind, executor=r.get("executor", "minecraft_pack"), result=r["result"],
                attempt_id=r["attempt_id"], execution_id=eid, game_session_id=session_id, intended=r.get("intended"),
                executed=r.get("executed"), deviation=r.get("deviation"), started_at=at(r.get("started")),
                ended_at=at(r.get("ended")), settle=r.get("settle", False))

    return capture


def _drive_skills(session_id: uuid.UUID, model) -> None:
    """A game pack session: her hands pick skills (stage G, phase 3)."""
    settings = get_settings()
    deps = SkillDriverDeps(
        view=_db(sessions.driver_view),
        progress=_db(sessions.driver_progress),
        pause=lambda sid, reason: _db(sessions.driver_pause)(sid, reason=reason),
        pack=PackClient.from_settings(),
        model=model,
        metric=write_metric,
        save_state=desktop_commands.save_driver_state,
        notice=lambda sid, kind, text: _db(sessions.driver_noticed)(sid, kind=kind, text=text),
        auto_goals=_db(sessions.auto_goals_set),
        messages=mind_hard.hands_messages,  # her mouth's tips and her own intentions
        experience=_db(game_experience.record_experience),
        learning=_db(game_practice.holding),
        learned=_db(game_skills.for_session),
        learned_used=_db(game_skills.record_use),
        lesson_result=_db(game_practice.last_result),
        capture=_hands_capture(session_id),
    )
    run = SkillDriverRun(session_id, deps, max_decisions=settings.minecraft_pack_max_decisions,
                         planning_wait_seconds=settings.desktop_planning_wait_seconds,
                         max_auto_goals=settings.desktop_max_auto_goals)
    log.info("skill driver started for session %s (%s)", session_id, settings.desktop_driver_model)
    while run.step():
        pass
    log.info("skill driver stopped for session %s after %d decisions", session_id, run.decisions)


def drive(session_id: uuid.UUID) -> None:
    settings = get_settings()
    with engine.connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(session_id)}).scalar():
            return  # another worker drives this session
        lock_conn.commit()
        try:
            try:
                model = build_driver_model()
            except DriverModelError as exc:
                log.warning("desktop driver unavailable for %s: %s", session_id, exc)
                _db(sessions.driver_pause)(session_id, reason="driver_unavailable")
                return
            view = _db(sessions.driver_view)(session_id)
            if view is not None and game_pack.is_pack_profile(view.profile_id):
                _drive_skills(session_id, model)
                return
            deps = DriverDeps(
                view=_db(sessions.driver_view),
                progress=_db(sessions.driver_progress),
                pause=lambda sid, reason: _db(sessions.driver_pause)(sid, reason=reason),
                observation=lambda env_id: desktop_preview.load(env_id, "observation"),
                publish=desktop_commands.publish_decision,
                outcomes=desktop_commands.recent_reports,
                save_state=desktop_commands.save_driver_state,
                metric=write_metric,
                profile=sessions.get_profile,
                model=model,
                notice=lambda sid, kind, text: _db(sessions.driver_noticed)(sid, kind=kind, text=text),
                auto_goals=_db(sessions.auto_goals_set),
                messages=desktop_commands.hands_messages,
                careful=desktop_commands.careful_state,
                history=lambda env_id, at: desktop_preview.frame_at(env_id, at, "observation"),
            )
            run = DriverRun(session_id, deps, freshness_ms=settings.desktop_driver_freshness_ms,
                            max_steps=settings.desktop_driver_max_steps, debug=_debug_writer(session_id),
                            planning_wait_seconds=settings.desktop_planning_wait_seconds,
                            max_auto_goals=settings.desktop_max_auto_goals)
            log.info("desktop driver started for session %s (%s)", session_id, settings.desktop_driver_model)
            while run.step():
                pass
            log.info("desktop driver stopped for session %s after %d steps", session_id, run.steps)
        except Exception:
            log.exception("desktop driver crashed for session %s", session_id)
            _db(sessions.driver_pause)(session_id, reason="driver_error")
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(session_id)})
            lock_conn.commit()
