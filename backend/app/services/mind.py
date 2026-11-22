"""Her mind: every tick of a source, decide how hard to think, then think (docs/COLUMBINA_MIND_DESIGN.md,
docs/MIND_ARCHITECTURE.md).

General: no game here. The ``mind`` worker thread calls ``run_once`` about once a second. Each
source (app/ai/mind/contracts.py; Minecraft in services/mind_minecraft.py) gives its live
activities; for each one its moment goes to the gate (Jev, or OpenAI Decisions as the fallback),
the rules code owns apply (urgent pressure caps hard thinking, one quick thought at a time, gaps),
and then:

- ``react``/``light``: a quick thought on a small pool thread (a fast model),
  saved, and its proposed line (if any) handed to her mind as a wake (autonomous_speech), which decides whether it's said;
- ``hard``: slow thinking about a problem (services/mind_hard.py), with attention shared per
  user across her activities. The gate's ``hard`` level is only one of its triggers (a
  surprise); here it becomes a light thought.

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
from typing import Any, Callable

import redis
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.ai.mind import gates as g
from app.ai.mind.contracts import Moment, Scope, Source
from app.core.config import get_settings
from app.db.models import MemoryEvidence, MindThought, RecallReconstructionSource, User
from app.db.session import SessionLocal
from app.services import agent_say, desktop_preview, mind_hard

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
_sources: list | None = None


@dataclass
class ActivityMind:
    """Per activity (a game session, a conversation), in the mind worker's process."""

    previous: Any = None  # what the source's next moment compares against
    tick_no: int = 0
    quick_busy: bool = False
    last_quick_at: float = 0.0
    last_react_at: float = 0.0
    last_danger: Any = None  # the danger she last reacted to (the source's own)
    hard_local: dict = field(default_factory=dict)  # topic/since, when Redis is unavailable
    quick_since: Any = None  # the source as the running quick thought's moment saw it
    hard: mind_hard.ActivityHard = field(default_factory=mind_hard.ActivityHard)
    user: str | None = None  # the user's name, read once


_minds: dict[uuid.UUID, ActivityMind] = {}


def sources() -> list:
    """Every source her mind reads (polled and event ones, live and shadow)."""
    global _sources
    if _sources is None:
        from app.services import mind_conversation, mind_minecraft, mind_phone, mind_security

        _sources = [mind_minecraft.SOURCE, mind_conversation.SOURCE, mind_security.SOURCE, mind_phone.SOURCE]
    return _sources


# --------------------------------------------------------------------------- #
# What's on her mind: thoughts, and the hard thought still running.
# --------------------------------------------------------------------------- #

def _hard_key(session_id: uuid.UUID | str) -> str:
    return f"mind:hard:{session_id}"


def _activity(key: uuid.UUID | str) -> uuid.UUID:
    return key if isinstance(key, uuid.UUID) else uuid.UUID(str(key))


def thinking_about(session_id: uuid.UUID | str) -> str | None:
    """The hard thought still running for this session, if any."""
    client = desktop_preview.redis_client()
    if client is not None:
        try:
            raw = client.get(_hard_key(session_id))
            return raw.decode() if isinstance(raw, bytes) else raw
        except redis.RedisError:
            pass
    local = _minds.get(_activity(session_id), ActivityMind()).hard_local
    if local and time.time() - local["since"] < HARD_TTL_SECONDS:
        return local["topic"]
    return None


def _set_thinking(session_id: uuid.UUID | str, topic: str | None) -> None:
    client = desktop_preview.redis_client()
    mind = _minds.setdefault(_activity(session_id), ActivityMind())
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


def activity_thoughts(db: Session, scope: Scope, now: datetime, limit: int = THOUGHTS_IN_TICK) -> list[dict]:
    """Her recent thoughts about an activity that isn't a game session (her phone): by source and activity."""
    rows = db.scalars(
        select(MindThought).where(MindThought.source == scope.source, MindThought.activity_id == scope.activity,
                                  MindThought.created_at >= now - THOUGHT_WINDOW, MindThought.status != "dropped")
        .order_by(MindThought.created_at.desc()).limit(limit)).all()
    return [{"level": r.level, "text": r.text, "said": r.said, "status": r.status, "conclusion": r.conclusion_kind,
             "at": r.created_at.timestamp()} for r in reversed(rows)]


def base_topic(topic: str | None) -> str | None:
    """A hard thought's own topic, without what it carried from an unfinished one."""
    return topic.split(CARRIED, 1)[0].strip() if topic else topic


def save_thought(db: Session, *, user_id: uuid.UUID, session_id: uuid.UUID | None, level: str, text: str,
                 said: str | None = None, status: str = "done", topic: str | None = None,
                 kind: str | None = None, source: str | None = None, activity_id: uuid.UUID | None = None,
                 handoff: Callable[[MindThought], None] | None = None, **hard) -> MindThought:
    """Store a thought (caller's transaction is committed here) and show it live. ``source``/``activity_id``:
    what it is about (a game thought defaults to its session). ``handoff``: run in the same transaction once
    the thought has its id (a finished hard thought handed to memory). ``hard``: a hard thought's trigger,
    conclusion_kind, thinking, for_hands, question, dropped_reason."""
    if session_id is not None:
        source, activity_id = source or "minecraft", activity_id or session_id
    thought = MindThought(user_id=user_id, session_id=session_id, level=level, text=text[:1000],
                          said=said, status=status, topic=(topic or None) and topic[:TOPIC_CHARS], kind=kind,
                          source=source, activity_id=activity_id, **hard)
    db.add(thought)
    if handoff is not None:
        db.flush()
        handoff(thought)
    db.commit()
    publish(user_id, thought)
    return thought


def thought_out(thought: MindThought) -> dict:
    return {"id": str(thought.id), "level": thought.level, "text": thought.text, "said": thought.said,
            "status": thought.status, "topic": thought.topic, "kind": thought.kind,
            "trigger": thought.trigger, "conclusion_kind": thought.conclusion_kind, "thinking": thought.thinking,
            "for_hands": thought.for_hands, "question": thought.question, "dropped_reason": thought.dropped_reason,
            "assessment": thought.assessment,
            "session_id": str(thought.session_id) if thought.session_id else None,
            "source": thought.source, "activity_id": str(thought.activity_id) if thought.activity_id else None,
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
    """Old thoughts go, except the ones a memory or a reconstruction points to (where something she remembers
    came from)."""
    days = get_settings().mind_thought_retention_days
    cited = (exists().where(MemoryEvidence.thought_id == MindThought.id)
             | exists().where(RecallReconstructionSource.thought_id == MindThought.id))
    result = db.execute(delete(MindThought).where(
        MindThought.created_at < datetime.now(timezone.utc) - timedelta(days=days), ~cited))
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


def run_once() -> None:
    global _last_prune
    with SessionLocal() as db:
        if time.time() - _last_prune > PRUNE_EVERY_SECONDS:
            _last_prune = time.time()
            try:
                prune(db)
            except Exception:  # noqa: BLE001
                log.exception("mind: pruning thoughts failed")
                db.rollback()
        for source in sources():
            if source.rhythm.kind == "event" and hasattr(source, "sweep") and source.enabled():
                try:  # an event source's slow look for problems nothing announces (promises, quiet time)
                    offered = source.sweep(db, time.time())
                    if offered:
                        _log({"ts": time.time(), "source": source.name, "sweep": offered})
                except Exception:  # noqa: BLE001
                    log.exception("mind: sweeping %s failed", source.name)
                    db.rollback()
            if source.rhythm.kind != "poll" or not source.enabled():
                continue
            polled = source.poll(db)
            if polled is None:
                continue  # the source is unreachable: no tick, and the next one starts fresh
            # A source that ticks only what's due (her phone) says which activities are still live; otherwise
            # what it polled is what's live.
            live = source.live_activities() if hasattr(source, "live_activities") else None
            _forget_gone(source, live if live is not None else {scope.activity for scope, _ in polled})
            for scope, payload in polled:
                try:
                    tick(db, source, scope, payload, time.time())
                except Exception:  # noqa: BLE001 - the brain must never get in the way of play
                    log.exception("mind tick for %s %s failed", scope.source, scope.activity)
                    db.rollback()


_activity_source: dict[uuid.UUID, str] = {}


def _forget_gone(source: Source, live: set[uuid.UUID]) -> None:
    """Activities of this source that ended: their mind and their place in attention go."""
    for activity, name in list(_activity_source.items()):
        if name == source.name and activity not in live:
            _activity_source.pop(activity, None)
            mind = _minds.pop(activity, None)
            if mind is not None:
                for att in mind_hard._attention.values():
                    att.waiting.pop(str(activity), None)
                    att.pressure.pop(str(activity), None)
    for activity in live:
        _activity_source[activity] = source.name


def tick(db: Session | None, source: Source, scope: Scope, payload: Any, at: float) -> dict:
    """One tick for one activity; returns the decision record (also logged)."""
    settings = get_settings()
    mind = _minds.setdefault(scope.activity, ActivityMind())
    now = datetime.fromtimestamp(at, timezone.utc)
    thinking = thinking_about(scope.key)
    if source.thought_session(scope):
        thoughts = recent_thoughts(db, source.thought_session(scope), now)
    else:
        thoughts = activity_thoughts(db, scope, now) if db is not None else []
    moment = source.moment(db, scope, payload, mind.previous, at, thoughts=thoughts, thinking=thinking)
    mind.previous = source.previous(moment)
    mind.tick_no += 1

    gate, fallback, thinkers = _gates()
    if getattr(source, "gate_needs_news", False) and not moment.fresh:
        # A source that sits unchanged for hours (her phone): nothing new isn't worth a model call.
        result = g.GateResult("skipped", "none", reason="nothing new")
    else:
        result = gate.decide(moment.text, source.gate_situation())
        if result.error and fallback is not gate:
            result = fallback.decide(moment.text, source.gate_situation())
    raw = g.level_from(result, settings.mind_think_threshold)
    level = g.capped(raw, moment.pressure)
    note = None
    if level == "hard":
        level = "light"  # hard thinking starts from a problem (mind_hard; the gate's hard may be a surprise)
    if not moment.can_act and level == "react":
        level = "light"  # paused: nothing to react to with her body
    if level not in (None, "none") and not moment.fresh:
        level, note = "none", "nothing new since the last tick"
    elif (level == "react" and mind.last_danger is not None and at - mind.last_react_at < SAME_DANGER_SECONDS
          and not source.worse(moment.danger, mind.last_danger)):
        level, note = "none", "same danger, not worse"

    action = "none"
    if level in ("react", "light"):
        if mind.quick_busy:
            action = "skipped: a quick thought is running"
        elif level == "light" and at - mind.last_quick_at < settings.mind_quick_gap_seconds:
            action = "skipped: quick gap"
        else:
            _start_quick(mind, source, scope, level, moment, at, thinkers and thinkers[level])
            if level == "react":
                mind.last_react_at, mind.last_danger = at, moment.danger
            action = level
    if action == "none" and source.thinks_when_quiet() and _quiet_due(mind, scope, moment, thinking, at):
        _start_quick(mind, source, scope, "quiet", moment, at, thinkers and thinkers["light"])
        action = "quiet"
    if mind.user is None:
        user = db.get(User, scope.user_id) if db is not None else None
        mind.user = user.name if user is not None and user.name else "the user"
    hard = mind_hard.step(source, scope, mind.hard, moment, gate_hard=raw == "hard", at=at, user=mind.user)

    source.after_tick(scope, moment, mind.tick_no, {"gate": result.gate, "level": level, "action": action})
    record = {
        "ts": at, "session": str(scope.activity), "source": source.name, "tick": mind.tick_no,
        "pressure": moment.pressure, "why": moment.why, "changed": moment.changed[:8], "news": len(moment.fresh),
        "gate": result.gate, "gate_level": result.level, "raw": raw, "level": level,
        "probabilities": result.probabilities, "gate_ms": result.latency_ms, "gate_error": result.error,
        "note": note, "action": action, "hard": hard, "thinking": thinking,
    }
    _log(record)
    return record


def _start_quick(mind: ActivityMind, source: Source, scope: Scope, level: str, moment: Moment, at: float,
                 thinker) -> None:
    mind.quick_busy = True
    mind.last_quick_at = at
    mind.quick_since = moment.capture() if moment.capture else None
    _pool.submit(_think_quick, source, scope, level, moment.text, at, thinker)


def _quiet_due(mind: ActivityMind, scope: Scope, moment: Moment, thinking: str | None, at: float) -> bool:
    """Time for a quiet thought: nothing urgent, no thought of any kind for a while,
    no hard thought running, and the user hasn't just spoken."""
    quiet = get_settings().mind_quiet_seconds
    if not quiet or moment.pressure == "urgent" or thinking or mind.quick_busy:
        return False
    last_hard = mind_hard.attention_for(scope.user_id).last_started_at
    if at - max(mind.last_quick_at, last_hard) < quiet:
        return False
    return not any(m.get("role") == "user" and at - m.get("at", 0) < USER_TURN_SECONDS for m in moment.talk)


def _think_quick(source: Source, scope: Scope, level: str, text: str, at: float, thinker) -> None:
    mind = _minds.setdefault(scope.activity, ActivityMind())
    try:
        with SessionLocal() as db:
            user = db.get(User, scope.user_id)
            from app.services.personality import current
            text = f"PERSONALITY\n{current().text}\n\nCURRENT MOMENT\n{text}"
            thought = thinker.think(level, text, user=(user.name if user and user.name else "the user"),
                                    situation=source.situation())
            if not thought.text:
                _log({"ts": time.time(), "session": str(scope.activity), "quick": level,
                      "error": thought.error, "skipped": None if thought.error else "nothing new to think",
                      "ms": thought.latency_ms})
                return
            late = time.time() - at > STALE_AFTER[level]
            status = "done" if source.live(db, scope) and not late else "stale"
            # Thought from a moment the source has left (seen live: "it slipped away" about a sheep
            # the lesson had just killed): not said, and kept as possibly out of date.
            outdated = source.changed_since(db, mind.quick_since) if status == "done" else []
            if outdated:
                status = "stale"
            said = None
            outcome = None
            saved = save_thought(db, user_id=scope.user_id, session_id=source.thought_session(scope), level=level,
                                 text=thought.text, status=status, kind=thought.kind, source=scope.source,
                                 activity_id=scope.activity)
            if thought.say and status == "done":
                from app.services import autonomous_speech
                outcome = autonomous_speech.submit(
                    db, user_id=scope.user_id, activity_id=scope.activity, source=scope.source,
                    event_id=saved.id, text=thought.say, reasons=[f"{level}: {thought.text}"],
                    ttl=source.say_ttl() if hasattr(source, "say_ttl") else STALE_AFTER[level],
                    **source.speaking(db, scope))
            _log({"ts": time.time(), "session": str(scope.activity), "quick": level, "kind": thought.kind,
                  "noticed": thought.noticed, "thought": thought.text,
                  "say": thought.say, "said": bool(said), "say_outcome": outcome, "status": status,
                  "changed": outdated or None,
                  "ms": thought.latency_ms})
    except Exception:  # noqa: BLE001
        log.exception("mind: quick thought failed for %s %s", scope.source, scope.activity)
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
