"""Her brain during play: every tick, decide how hard to think, then think (docs/COLUMBINA_MIND_DESIGN.md).

The ``mind`` worker thread calls ``run_once`` about once a second. For each
live game pack session it builds a tick (``app.ai.mind.tick``), asks the gate
how hard to think (Jev, or OpenAI Decisions as the fallback), applies the rules
code owns (urgent pressure caps hard thinking, one thought of each kind at a time, gaps),
and then:

- ``react``/``light``: a quick thought on a small pool thread (a fast model),
  saved, and its line (if any) handed to the one speaking gate, ``agent_say``;
- ``hard``: slow thinking about a problem (services/mind_hard.py): a step that keeps
  failing, a surprise, something big ahead, her mouth's hand-off, quiet time. It ends
  in one conclusion that goes to her hands, her mouth and the thoughts panel. The gate's
  ``hard`` level is only one of its triggers (a surprise); here it becomes a light thought.

Nothing here blocks her hands or her replies. Every decision goes to
``MIND_LOG_PATH`` for tuning.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.desktop.pack_client import PackClient, PackError
from app.ai.mind import gates as g
from app.ai.mind.tick import MINECRAFT, TickInput, danger, news, render, worse
from app.core.config import get_settings
from app.db.models import DesktopSession, MindThought, User
from app.db.session import SessionLocal
from app.services import agent_say, desktop_preview, game_moment, mind_hard, mind_ticks

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
THOUGHTS_IN_TICK = 5
THOUGHT_WINDOW = timedelta(minutes=10)
# What old background-turn thoughts carried from an unfinished one (before services/mind_hard.py).
CARRIED = " (unfinished from before"
HARD_TTL_SECONDS = 180  # a hard thought still "running" after this long is presumed lost
STALE_AFTER = {"react": 4.0, "light": 12.0, "quiet": 20.0}  # a quick thought landing later than this isn't said
SAME_DANGER_SECONDS = 8.0  # no new reaction to the same danger this soon, unless it got worse
USER_TURN_SECONDS = 10.0  # the user just spoke: the mouth answers, so no quiet thought
PRUNE_EVERY_SECONDS = 600
TOPIC_CHARS = 200

_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mind-quick")
_last_prune = 0.0
_gate = None
_fallback = None
_thinkers = None
_situation = None


@dataclass
class SessionMind:
    previous: TickInput | None = None
    tick_no: int = 0
    quick_busy: bool = False
    last_quick_at: float = 0.0
    last_react_at: float = 0.0
    last_danger: dict | None = None  # the danger she last reacted to (tick.danger)
    hard_local: dict = field(default_factory=dict)  # topic/since, when Redis is unavailable
    quick_since: game_moment.Moment | None = None  # the game as the running quick thought's tick saw it
    hard: mind_hard.HardState = field(default_factory=mind_hard.HardState)  # her slow thinking
    user: str | None = None  # the user's name, read once


_minds: dict[uuid.UUID, SessionMind] = {}
_notice_cursors: dict[uuid.UUID, int] = {}


def enabled() -> bool:
    settings = get_settings()
    return bool(settings.mind_enabled and settings.desktop_agent_enabled)


# --------------------------------------------------------------------------- #
# What's on her mind: thoughts, and the hard thought still running.
# --------------------------------------------------------------------------- #

def _hard_key(session_id: uuid.UUID | str) -> str:
    return f"mind:hard:{session_id}"


def thinking_about(session_id: uuid.UUID) -> str | None:
    """The hard thought still running for this session, if any."""
    client = desktop_preview.redis_client()
    if client is not None:
        try:
            raw = client.get(_hard_key(session_id))
            return raw.decode() if isinstance(raw, bytes) else raw
        except redis.RedisError:
            pass
    local = _minds.get(session_id, SessionMind()).hard_local
    if local and time.time() - local["since"] < HARD_TTL_SECONDS:
        return local["topic"]
    return None


def _set_thinking(session_id: uuid.UUID, topic: str | None) -> None:
    client = desktop_preview.redis_client()
    mind = _minds.setdefault(session_id, SessionMind())
    mind.hard_local = {"topic": topic, "since": time.time()} if topic else {}
    if client is None:
        return
    try:
        if topic:
            client.set(_hard_key(session_id), topic, ex=HARD_TTL_SECONDS)
        else:
            client.delete(_hard_key(session_id))
    except redis.RedisError:
        pass


def recent_thoughts(db: Session, session_id: uuid.UUID, now: datetime, limit: int = THOUGHTS_IN_TICK) -> list[dict]:
    rows = db.scalars(
        select(MindThought).where(MindThought.session_id == session_id, MindThought.created_at >= now - THOUGHT_WINDOW,
                                  MindThought.status != "dropped")
        .order_by(MindThought.created_at.desc()).limit(limit)).all()
    return [{"level": r.level, "text": r.text, "said": r.said, "status": r.status, "conclusion": r.conclusion_kind,
             "at": r.created_at.timestamp()} for r in reversed(rows)]


def base_topic(topic: str | None) -> str | None:
    """A hard thought's own topic, without what it carried from an unfinished one."""
    return topic.split(CARRIED, 1)[0].strip() if topic else topic


def save_thought(db: Session, *, user_id: uuid.UUID, session_id: uuid.UUID | None, level: str, text: str,
                 said: str | None = None, status: str = "done", topic: str | None = None,
                 kind: str | None = None, **hard) -> MindThought:
    """Store a thought (caller's transaction is committed here) and show it live. ``hard``: a
    hard thought's trigger, conclusion_kind, thinking, for_hands, question, dropped_reason."""
    thought = MindThought(user_id=user_id, session_id=session_id, level=level, text=text[:1000],
                          said=said, status=status, topic=(topic or None) and topic[:TOPIC_CHARS], kind=kind, **hard)
    db.add(thought)
    db.commit()
    publish(user_id, thought)
    return thought


def thought_out(thought: MindThought) -> dict:
    return {"id": str(thought.id), "level": thought.level, "text": thought.text, "said": thought.said,
            "status": thought.status, "topic": thought.topic, "kind": thought.kind,
            "trigger": thought.trigger, "conclusion_kind": thought.conclusion_kind, "thinking": thought.thinking,
            "for_hands": thought.for_hands, "question": thought.question, "dropped_reason": thought.dropped_reason,
            "session_id": str(thought.session_id) if thought.session_id else None,
            "created_at": thought.created_at.isoformat() if thought.created_at else
            datetime.now(timezone.utc).isoformat()}


def publish(user_id: uuid.UUID, thought: MindThought) -> None:
    """To every open chat socket of the user (shown in the thoughts panel, never spoken)."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.publish(agent_say.channel(user_id), json.dumps({"kind": "thought", "thought": thought_out(thought)}))
    except redis.RedisError:
        log.warning("mind: thought saved but not published for user %s", user_id)


def list_thoughts(db: Session, user_id: uuid.UUID, *, minutes: int = 120, limit: int = 200) -> list[MindThought]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = db.scalars(
        select(MindThought).where(MindThought.user_id == user_id, MindThought.created_at >= since)
        .order_by(MindThought.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def prune(db: Session) -> int:
    days = get_settings().mind_thought_retention_days
    result = db.execute(delete(MindThought).where(
        MindThought.created_at < datetime.now(timezone.utc) - timedelta(days=days)))
    db.commit()
    return result.rowcount or 0


# --------------------------------------------------------------------------- #
# The tick.
# --------------------------------------------------------------------------- #

def _gates():
    global _gate, _fallback, _thinkers
    settings = get_settings()
    if _thinkers is None:
        from app.ai.mind.think import QuickThinker

        key = settings.openai_api_key or ""
        _thinkers = {"react": QuickThinker(settings.mind_react_model, api_key=key),
                     "light": QuickThinker(settings.mind_quick_model, api_key=key)}
        _fallback = g.OpenAIDecisionsGate(api_key=settings.openai_api_key or "", timeout=5.0)
        if settings.mind_gate == "jev" and settings.typesafe_api_key:
            _gate = g.typesafe(settings.typesafe_api_key, base_url=settings.typesafe_base_url, timeout=3.0)
        else:
            _gate = _fallback
    return _gate, _fallback, _thinkers


def situation():
    """The Minecraft situation, with her playing voice for spoken lines."""
    global _situation
    if _situation is None:
        from dataclasses import replace

        from app.ai.mind.tick import MINECRAFT
        from app.ai.prompts import _PLAYING_VOICE_PROMPT

        _situation = replace(MINECRAFT, voice=_PLAYING_VOICE_PROMPT)
    return _situation


def run_once() -> None:
    global _last_prune
    if not enabled():
        return
    with SessionLocal() as db:
        if time.time() - _last_prune > PRUNE_EVERY_SECONDS:
            _last_prune = time.time()
            try:
                prune(db)
            except Exception:  # noqa: BLE001
                log.exception("mind: pruning thoughts failed")
                db.rollback()
        sessions = mind_ticks.live_sessions(db)
        if not sessions:
            _minds.clear()
            return
        pack = PackClient.from_settings()
        try:
            state = pack.state()
        except PackError:
            return
        for session in sessions:
            try:
                tick(db, pack, session, state, time.time())
                asked = mind_hard.ask_if_due(db, session, time.time())
                if asked:
                    _log({"ts": time.time(), "session": str(session.id), "question": asked})
            except Exception:  # noqa: BLE001 - the brain must never get in the way of play
                log.exception("mind tick for session %s failed", session.id)
                db.rollback()


def tick(db: Session, pack: PackClient, session: DesktopSession, state: dict, at: float) -> dict:
    """One tick for one session; returns the decision record (also logged)."""
    settings = get_settings()
    mind = _minds.setdefault(session.id, SessionMind())
    now = datetime.fromtimestamp(at, timezone.utc)
    thinking = thinking_about(session.id)
    notices = mind_ticks.next_notices(pack, _notice_cursors, session.id)
    current = mind_ticks.build_tick(db, session, state, notices, at,
                                    thoughts=recent_thoughts(db, session.id, now), thinking=thinking)
    previous = mind_ticks.fresh_previous(mind.previous, at)
    text, pressure, changed, why = render(current, previous)
    fresh = news(current, previous)
    mind.previous = current
    mind.tick_no += 1

    gate, fallback, thinkers = _gates()
    result = gate.decide(text, MINECRAFT)
    if result.error and fallback is not gate:
        result = fallback.decide(text, MINECRAFT)
    raw = g.level_from(result, settings.mind_think_threshold)
    level = g.capped(raw, pressure)
    note = None
    if level == "hard":
        level = "light"  # hard thinking starts from a problem (mind_hard; the gate's hard may be a surprise)
    if session.state != "running" and level == "react":
        level = "light"  # paused: nothing to react to with her body
    if level not in (None, "none") and not fresh:
        level, note = "none", "nothing new since the last tick"
    elif (level == "react" and mind.last_danger is not None and at - mind.last_react_at < SAME_DANGER_SECONDS
          and not worse(danger(current.state), mind.last_danger)):
        level, note = "none", "same danger, not worse"

    action = "none"
    if level in ("react", "light"):
        if mind.quick_busy:
            action = "skipped: a quick thought is running"
        elif level == "light" and at - mind.last_quick_at < settings.mind_quick_gap_seconds:
            action = "skipped: quick gap"
        else:
            mind.quick_busy = True
            mind.last_quick_at = at
            mind.quick_since = game_moment.capture(db, session, state, at=at)
            if level == "react":
                mind.last_react_at, mind.last_danger = at, danger(current.state)
            _pool.submit(_think_quick, session.id, session.owner_user_id, level, text, at,
                         thinkers and thinkers[level])
            action = level
    if action == "none" and _quiet_due(mind, current, pressure, thinking, at):
        mind.quick_busy = True
        mind.last_quick_at = at
        mind.quick_since = game_moment.capture(db, session, state, at=at)
        _pool.submit(_think_quick, session.id, session.owner_user_id, "quiet", text, at,
                     thinkers and thinkers["light"])
        action = "quiet"
    if mind.user is None:
        user = db.get(User, session.owner_user_id) if db is not None else None
        mind.user = user.name if user is not None and user.name else "the user"
    hard = mind_hard.step(session, mind.hard, state=state, plan=session.plan, talk=current.talk, fresh=fresh,
                          gate_hard=raw == "hard", pressure=pressure, at=at, user=mind.user)

    if settings.mind_tick_recording_enabled:
        mind_ticks.append_tick(session.id, mind.tick_no, current, text, pressure, changed, why,
                               extra={"mind": {"gate": result.gate, "level": level, "action": action}})
    record = {
        "ts": at, "session": str(session.id), "tick": mind.tick_no, "pressure": pressure, "why": why,
        "changed": changed[:8], "news": len(fresh), "gate": result.gate, "gate_level": result.level, "raw": raw, "level": level,
        "probabilities": result.probabilities, "gate_ms": result.latency_ms, "gate_error": result.error,
        "note": note, "action": action, "hard": hard, "thinking": thinking,
    }
    _log(record)
    return record


def _quiet_due(mind: SessionMind, current: TickInput, pressure: str, thinking: str | None, at: float) -> bool:
    """Time for a quiet thought: nothing urgent, no thought of any kind for a while,
    no hard thought running, and the user hasn't just spoken."""
    quiet = get_settings().mind_quiet_seconds
    if not quiet or pressure == "urgent" or thinking or mind.quick_busy:
        return False
    if at - max(mind.last_quick_at, mind.hard.last_started_at) < quiet:
        return False
    return not any(m.get("role") == "user" and at - m.get("at", 0) < USER_TURN_SECONDS for m in current.talk)


def _think_quick(session_id: uuid.UUID, user_id: uuid.UUID, level: str, text: str, at: float, thinker) -> None:
    mind = _minds.setdefault(session_id, SessionMind())
    try:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            thought = thinker.think(level, text, user=(user.name if user and user.name else "the user"),
                                    situation=situation())
            if not thought.text:
                _log({"ts": time.time(), "session": str(session_id), "quick": level,
                      "error": thought.error, "skipped": None if thought.error else "nothing new to think",
                      "ms": thought.latency_ms})
                return
            session = db.get(DesktopSession, session_id)
            late = time.time() - at > STALE_AFTER[level]
            live = session is not None and session.state in mind_ticks.LIVE_STATES
            status = "done" if live and not late else "stale"
            # Thought from a moment the game has left (seen live: "it slipped away" about a sheep
            # the lesson had just killed): not said, and kept as possibly out of date.
            outdated = game_moment.since(db, mind.quick_since) if status == "done" else []
            if outdated:
                status = "stale"
            said = None
            outcome = None
            if thought.say and status == "done":
                outcome = agent_say.deliver(db, user_id=user_id, text=thought.say,
                                            reasons=[f"{level}: {thought.text}"],
                                            commentary_mode=session.commentary_mode)
                said = thought.say if outcome.startswith("Said") else None
            save_thought(db, user_id=user_id, session_id=session_id, level=level, text=thought.text, said=said,
                         status=status, kind=thought.kind)
            _log({"ts": time.time(), "session": str(session_id), "quick": level, "kind": thought.kind,
                  "noticed": thought.noticed, "thought": thought.text,
                  "say": thought.say, "said": bool(said), "say_outcome": outcome, "status": status,
                  "changed": outdated or None,
                  "ms": thought.latency_ms})
    except Exception:  # noqa: BLE001
        log.exception("mind: quick thought failed for session %s", session_id)
    finally:
        mind.quick_busy = False


def hard_thought_done(user_id: uuid.UUID, session_id: uuid.UUID, topic: str | None, reply: str | None,
                      *, outdated: list[str] | None = None) -> None:
    """Called by a background turn with a ``thought:`` reason (queued before hard thinking moved to
    services/mind_hard.py): store what she thought (or that it couldn't run).
    ``outdated``: what changed in the game while she thought; then it's kept as stale, so her
    mouth reads it as possibly out of date rather than as fact."""
    topic = base_topic(topic)
    with SessionLocal() as db:
        text = " ".join((reply or "").split())
        if text:
            save_thought(db, user_id=user_id, session_id=session_id, level="hard", text=text, topic=topic,
                         status="stale" if outdated else "done")
            if outdated:
                _log({"ts": time.time(), "session": str(session_id), "hard": "stale", "thought": text[:300],
                      "changed": outdated})
        elif topic:
            save_thought(db, user_id=user_id, session_id=session_id, level="hard",
                         text=f"(didn't get to think this through: {topic})", status="unfinished", topic=topic)
    _set_thinking(session_id, None)


def log_decision(record: dict) -> None:
    """One line in MIND_LOG_PATH, for the parts of her mind outside the tick (her planner)."""
    _log(record)


def _log(record: dict) -> None:
    path = Path(get_settings().mind_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        log.warning("mind: could not write %s", path)
