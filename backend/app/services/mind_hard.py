"""When she thinks hard, and where the conclusion goes (docs/COLUMBINA_HARD_THINKING_PLAN.md,
docs/COLUMBINA_INNER_STATE_PLAN.md).

General: no game here. Hard thinking starts on a problem, never on the last second. Her mind's
tick (services/mind.py) calls ``step`` for each activity of a polled source; a source that
produces events calls ``offer``. Problems come from:

- the source (services/mind_minecraft.py: a step that keeps failing, a surprise, something big;
  services/mind_conversation.py: a question of theirs still open after her reply, a promise she hasn't kept,
  an intention about to lapse, quiet time, and Student 2 deciding to think something over);
- her mouth's ``think_it_over`` (a hand-off: the user's words need thought);
- quiet: nothing new for MIND_HARD_QUIET_SECONDS, at most every MIND_HARD_QUIET_EVERY_SECONDS.

Attention is per user: one hard thought at a time across all her activities, none while any of
them is under urgent pressure, a gap between them (a hand-off skips it). Each activity keeps one
waiting problem (a newer one replaces it; a hand-off always wins), so a noisy source can't take
all her attention; the next to start goes by trigger priority, then the source served least
recently. Shadow sources have their own attention and never delay a live thought.

The thought (app/ai/mind/hard.py) runs on this module's own thread; it has no tools. When it lands
the source says whether it still holds (``dropped``) and whether the world moved past it
(``outdated``); otherwise it's saved and sent on: a tip to the activity's body (which accepts or
refuses it), an intention her body and mouth keep for a while, a question her mouth asks at a
pause, and always her mouth's "on your mind" and the thoughts panel. Intentions, the question and
hand-offs are kept per activity (``Scope.key``), so a thought about one game never steers another.
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
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.mind.contracts import PRIORITY, Command, CommandResult, Moment, Scope, Source
from app.ai.mind.hard import HardInput, HardThinker, HardThought, Problem, render
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, CoreMemory, MindThought, User
from app.db.session import SessionLocal
from app.services import agent_say, desktop_preview

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

EARLIER_WINDOW = timedelta(minutes=10)
EARLIER_KEPT = 3
QUESTION_PAUSE_SECONDS = 8.0  # the user quiet this long: a natural pause to ask
QUESTION_TTL_SECONDS = 180
INTENTION_TTL_SECONDS = 20 * 60
INTENTIONS_KEPT = 3
HANDOFF_TTL_SECONDS = 120
USER_TURN_SECONDS = 10.0
AGING_SECONDS = 60.0  # waiting this long improves a problem's priority by one level
EVENTS_KEPT = 200  # event ids remembered per attention, so a duplicate event is one thought

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mind-hard")
_shadow_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mind-hard-shadow")
_lock = threading.Lock()
# Attention is touched by the mind's tick, by event sources, by gap timers and by thoughts
# that land (each on its own thread): every change goes through this.
_sched_lock = threading.RLock()
_thinker: HardThinker | None = None


@dataclass
class Waiting:
    source: Source
    scope: Scope
    problem: Problem
    since: float = 0.0  # when this activity started waiting (kept when a newer problem replaces it)


@dataclass
class Attention:
    """Per user (and separately for shadow sources), in the mind's process."""

    running: Problem | None = None
    running_scope: Scope | None = None
    cancelled: str | None = None  # why the running thought will be dropped when it lands
    last_started_at: float = 0.0
    waiting: dict[str, Waiting] = field(default_factory=dict)  # one per activity (Scope.key)
    pressure: dict[str, str] = field(default_factory=dict)  # each activity's latest pressure
    served: dict[str, float] = field(default_factory=dict)  # source name -> when it last got a thought
    events: list[str] = field(default_factory=list)  # event ids already offered
    timer: threading.Timer | None = None  # wakes waiting event-source work after a gap


@dataclass
class ActivityHard:
    """Per activity: what quiet thinking needs, and the source's own memory for its problems."""

    started: bool = False  # the first tick sets what's already going on, without thinking about it
    last_news_at: float = 0.0
    quiet_topic: int = 0
    source_state: Any = None


_attention: dict[tuple[uuid.UUID, bool], Attention] = {}


def attention_for(user_id: uuid.UUID, *, shadow: bool = False) -> Attention:
    return _attention.setdefault((user_id, shadow), Attention())


def forget(scope: Scope, *, shadow: bool = False) -> None:
    """An activity that's gone: its waiting problem and pressure no longer count."""
    att = _attention.get((scope.user_id, shadow))
    if att is not None:
        att.waiting.pop(scope.key, None)
        att.pressure.pop(scope.key, None)


def reset() -> None:
    _attention.clear()


# --------------------------------------------------------------------------- #
# Problems and scheduling
# --------------------------------------------------------------------------- #

def _better(waiting: Problem | None, new: Problem) -> Problem:
    """A newer problem replaces a waiting one; the mouth's hand-off always wins."""
    if waiting is not None and waiting.trigger == "handoff" and new.trigger != "handoff":
        return waiting
    return new


def _handoff_key(key: uuid.UUID | str) -> str:
    return f"mind:handoff:{key}"


def handoff(key: uuid.UUID | str, user: str) -> Problem | None:
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.lpop(_handoff_key(key))
    except redis.RedisError:
        return None
    if not raw:
        return None
    item = json.loads(raw)
    if time.time() - float(item.get("at", 0)) > HANDOFF_TTL_SECONDS:
        return None
    what = item.get("what", "")
    return Problem("handoff", f"What you wanted to think over, from your talk with {user}: {what}",
                   topic=f"thinking over: {what}"[:200], query=what[:300],
                   anchor={"goal": item.get("goal"), "at": item.get("at")})


def quiet(source: Source, act: ActivityHard, att: Attention, moment: Moment, at: float, user: str) -> Problem | None:
    settings = get_settings()
    topics = source.quiet_topics(user)
    if not topics or not settings.mind_hard_quiet_seconds or moment.pressure == "urgent":
        return None
    if at - act.last_news_at < settings.mind_hard_quiet_seconds:
        return None
    if at - att.last_started_at < settings.mind_hard_quiet_every_seconds:
        return None
    if any(m.get("role") == "user" and at - m.get("at", 0) < USER_TURN_SECONDS for m in moment.talk):
        return None
    topic = topics[act.quiet_topic % len(topics)]
    act.quiet_topic += 1
    return Problem("quiet", f"Nothing is happening. Think about {topic}.", topic=f"quiet: {topic}"[:200],
                   query=topic[:300])


def _wait(att: Attention, source: Source, scope: Scope, problem: Problem, at: float) -> None:
    held = att.waiting.get(scope.key)
    att.waiting[scope.key] = Waiting(source, scope, _better(held.problem if held else None, problem),
                                     since=held.since if held else at)


def step(source: Source, scope: Scope, act: ActivityHard, moment: Moment, *, gate_hard: bool, at: float,
         user: str) -> str | None:
    """One tick of one activity: find its problems, start a hard thought if none is running.
    Returns what it did."""
    with _sched_lock:
        return _step(source, scope, act, moment, gate_hard=gate_hard, at=at, user=user)


def _step(source: Source, scope: Scope, act: ActivityHard, moment: Moment, *, gate_hard: bool, at: float,
          user: str) -> str | None:
    att = attention_for(scope.user_id, shadow=source.shadow)
    if not act.started:
        act.started, act.last_news_at = True, at
        act.source_state = source.new_state()
    if any(not line.startswith("(first tick") for line in moment.fresh):  # a first tick (or after a gap) isn't news
        act.last_news_at = at
    att.pressure[scope.key] = moment.pressure
    # What the problems were about is gone (the user changed the goal): a running thought is dropped
    # when it lands, a waiting one now.
    if att.running is not None and att.running_scope == scope and not att.cancelled:
        att.cancelled = source.drop_waiting(scope, moment, att.running)
    held = att.waiting.get(scope.key)
    if held is not None and source.drop_waiting(scope, moment, held.problem):
        del att.waiting[scope.key]

    found = [handoff(scope.key, user), *source.problems(scope, act.source_state, moment, gate_hard=gate_hard),
             quiet(source, act, att, moment, at, user)]
    for problem in found:
        if problem is not None:
            _wait(att, source, scope, problem, at)
    return schedule(att, at)


def offer(source: Source, scope: Scope, problem: Problem, at: float) -> str | None:
    """A problem from a source that produces events (no tick). An ``event`` anchor that was
    offered before is ignored, so a duplicate event is one thought."""
    with _sched_lock:
        return _offer(source, scope, problem, at)


def _offer(source: Source, scope: Scope, problem: Problem, at: float) -> str | None:
    att = attention_for(scope.user_id, shadow=source.shadow)
    event = problem.anchor.get("event")
    if event is not None:
        if event in att.events:
            return "hard: duplicate event"
        att.events = [*att.events, event][-EVENTS_KEPT:]
    att.pressure.setdefault(scope.key, "calm")
    _wait(att, source, scope, problem, at)
    return schedule(att, at)


def _next(att: Attention, at: float) -> Waiting:
    """The mouth's hand-off first; then trigger priority, which improves by one level per
    AGING_SECONDS waited, so a source that keeps finding urgent-looking problems can't
    starve another; then the source served least recently."""
    return min(att.waiting.values(), key=lambda w: (
        w.problem.trigger != "handoff",
        PRIORITY.get(w.problem.trigger, 5) - (at - w.since) / AGING_SECONDS,
        att.served.get(w.source.name, 0.0)))


def _event_work(att: Attention) -> bool:
    """Waiting work from a source no tick comes back for: it has to wake itself."""
    return any(w.source.rhythm.kind == "event" for w in att.waiting.values())


def schedule(att: Attention, at: float) -> str | None:
    with _sched_lock:
        return _schedule(att, at)


def _schedule(att: Attention, at: float) -> str | None:
    if not att.waiting:
        return None
    pick = _next(att, at)
    trigger = pick.problem.trigger
    if att.running is not None:
        return f"hard waiting ({trigger})"
    if "urgent" in att.pressure.values():
        return f"hard postponed ({trigger}): urgent"
    gap = get_settings().mind_hard_gap_seconds
    if trigger != "handoff" and at - att.last_started_at < gap:
        if _event_work(att) and att.timer is None:
            att.timer = threading.Timer(gap - (at - att.last_started_at) + 0.1, _wake, (att,))
            att.timer.daemon = True
            att.timer.start()
        return f"hard waiting ({trigger}): gap"
    del att.waiting[pick.scope.key]
    start(pick.source, pick.scope, att, pick.problem, at)
    return f"hard: {trigger}"


def _wake(att: Attention) -> None:
    att.timer = None
    schedule(att, time.time())


def start(source: Source, scope: Scope, att: Attention, problem: Problem, at: float) -> None:
    from app.services import mind

    att.running, att.running_scope, att.cancelled, att.last_started_at = problem, scope, None, at
    att.served[source.name] = at
    if not source.shadow:
        mind._set_thinking(scope.key, problem.topic)
    (_shadow_pool if source.shadow else _pool).submit(run, source, scope, att, problem, at)


# --------------------------------------------------------------------------- #
# The thought
# --------------------------------------------------------------------------- #

def thinker() -> HardThinker:
    global _thinker
    if _thinker is None:
        settings = get_settings()
        _thinker = HardThinker(settings.mind_hard_model, api_key=settings.openai_api_key or "",
                               reasoning_effort=settings.mind_hard_reasoning_effort)
    return _thinker


def conversation(db: Session, user_id: uuid.UUID, now: datetime,
                 conversation_id: uuid.UUID | None = None) -> tuple[dict, ...]:
    """That conversation's last messages (default: the user's latest conversation), oldest first."""
    from app.ai.mind.hard import CONVERSATION_KEPT

    if conversation_id is None:
        conversation_id = db.scalar(select(Conversation.id).where(Conversation.user_id == user_id)
                                    .order_by(Conversation.updated_at.desc()).limit(1))
    if conversation_id is None:
        return ()
    rows = db.execute(
        select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at, ChatMessage.id)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.content != "")
        .order_by(ChatMessage.created_at.desc()).limit(CONVERSATION_KEPT)).all()
    from app.services import memory_forget

    # What they asked her to forget isn't something she thinks over again (marked; a message fenced whole is left out).
    cuts = memory_forget.memory_cuts(db, user_id, [row[3] for row in rows])
    out = []
    for role, content, created, message_id in reversed(rows):
        if message_id in cuts:
            content = memory_forget.memory_view(content, None, "user", cuts[message_id])
            if content is None:
                continue
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        out.append({"role": role, "content": content[:500], "ago": (now - created).total_seconds(),
                    "id": str(message_id)})
    return tuple(out)


def _character(db: Session) -> tuple[str, ...]:
    core = db.scalar(select(CoreMemory).where(CoreMemory.is_active.is_(True)).limit(1))
    character = ((core.identity or {}).get("character") or {}) if core else {}
    return tuple(item.get("text", "") for section in ("values", "principles")
                 for item in character.get(section) or [] if item.get("text"))


def _earlier(db: Session, scope: Scope, topic: str, now: datetime) -> tuple[str, ...]:
    rows = db.scalars(
        select(MindThought.text).where(MindThought.user_id == scope.user_id,
                                       MindThought.activity_id == scope.activity, MindThought.level == "hard",
                                       MindThought.topic == topic, MindThought.status.in_(("done", "stale")),
                                       MindThought.created_at >= now - EARLIER_WINDOW)
        .order_by(MindThought.created_at.desc()).limit(EARLIER_KEPT)).all()
    return tuple(reversed(rows))


def gather(db: Session, source: Source, scope: Scope, problem: Problem) -> tuple[HardInput, Any, str]:
    """What she reads: the general part here, the source's part (right now, the goal, what she
    learned, a token to tell later whether the world moved) from the source."""
    from app.services import recall_references

    user = db.get(User, scope.user_id)
    name = user.name if user and user.name else "the user"
    now = datetime.now(timezone.utc)
    refs: list[str] = []
    base = HardInput(
        problem=problem, user=name, conversation=conversation(db, scope.user_id, now),
        memories=recall_references.for_thought(db, user_id=scope.user_id, source=scope.source,
                                               activity=scope.activity, query=problem.query, refs=refs),
        earlier=_earlier(db, scope, problem.topic, now), character=_character(db), memory_refs=tuple(refs),
    )
    inp, token = source.gather(db, scope, problem, base)
    return inp, token, name  # inp None: the activity is gone


def run(source: Source, scope: Scope, att: Attention, problem: Problem, at: float) -> None:
    from app.services import mind

    try:
        with SessionLocal() as db:
            inp, token, name = gather(db, source, scope, problem)
            # A problem that stopped holding while it waited (settled, answered, the step over) isn't thought
            # about at all: nothing to conclude, and no model call.
            stale_before = source.dropped(db, scope, problem, att.cancelled) if inp is not None else None
            db.commit()
        if inp is None:
            return
        if stale_before:
            mind._log({"ts": time.time(), "session": str(scope.activity), "source": source.name,
                       "hard_dropped_before_thinking": {"trigger": problem.trigger, "why": stale_before}})
            return
        situation = source.situation()
        brain = thinker()
        system = brain.instructions(problem.trigger, name, situation)
        thought = brain.think(inp, situation)
        if not source.shadow:
            _log_hard(scope, problem, system, render(inp), thought)
        if thought.error or thought.kind is None:
            if source.shadow:
                _log_shadow(scope, problem, system, render(inp), thought, status="nothing", why=None)
            return
        with SessionLocal() as db:
            why = source.dropped(db, scope, problem, att.cancelled)
            outdated = source.outdated(db, scope, problem, token)
            status = "dropped" if why else "stale" if outdated else "done"
            if source.shadow:
                _log_shadow(scope, problem, system, render(inp), thought, status=status, why=why)
                return
            saved = mind.save_thought(
                db, user_id=scope.user_id, session_id=source.thought_session(scope), level="hard",
                source=scope.source, activity_id=scope.activity, text=thought.conclusion, status=status, topic=problem.topic, trigger=problem.trigger,
                conclusion_kind=thought.kind, thinking=thought.thinking, for_hands=thought.for_hands,
                question=thought.question, dropped_reason=why,
                handoff=(lambda row: to_memory(db, source, scope, problem, inp, thought, row))
                if status == "done" else None)
            if status == "done":
                routed = route(source, scope, saved.id, problem, thought, goal=source.current_goal(db, scope))
                if routed:
                    mind._log({"ts": time.time(), "session": str(scope.activity), "source": source.name,
                               "routed": {k: [r.status, r.detail] for k, r in routed.items()}})
    except Exception:  # noqa: BLE001 - a hard thought that fails is just not thought
        log.exception("mind: hard thought failed for %s %s", scope.source, scope.activity)
    finally:
        with _sched_lock:
            att.running, att.running_scope = None, None
        if not source.shadow:
            mind._set_thinking(scope.key, None)
        if _event_work(att):
            schedule(att, time.time())  # no tick comes back for event-source work


def to_memory(db: Session, source: Source, scope: Scope, problem: Problem, inp: HardInput, thought: HardThought,
            saved: MindThought) -> None:
    """A thought that reached a conclusion goes to memory as an observation, in the thought's own transaction
    (docs/COLUMBINA_THOUGHT_MEMORY_SPEC.md §3); memory decides what lasts. In a savepoint: a failure here
    never loses the thought."""
    from app.services import memory_manager, mind, observation

    basis = {"messages": [m["id"] for m in inp.conversation if m.get("id")][-40:],
             "memories": list(inp.memory_refs)[:40]}
    payload = {"thought_id": str(saved.id), "conclusion_kind": thought.kind, "conclusion": thought.conclusion,
               "question": thought.question, "reason": thought.thinking, "trigger": problem.trigger,
               "topic": (mind.base_topic(problem.topic) or "")[:200] or None, "basis": basis}
    if problem.anchor.get("conflict"):
        payload["premise_conflict"] = str(problem.anchor["conflict"])
    try:
        with db.begin_nested():
            context = memory_manager.thought_context(db, scope.user_id, scope.source, scope.activity)
            observation.record(db, observation.Report(
                user_id=scope.user_id, source="mind", source_instance=f"{scope.source}:{scope.activity}",
                source_event_key=f"thought:{saved.id}:v1", kind="thought_concluded", payload=payload,
                occurred_at=datetime.now(timezone.utc), context_id=context.id, thought_id=saved.id,
                session_id=source.thought_session(scope)))
    except Exception:  # noqa: BLE001 - memory never gets in the way of thinking
        log.exception("mind: thought %s not handed to memory", saved.id)


def route(source: Source, scope: Scope, thought_id: uuid.UUID, problem: Problem, thought: HardThought, *,
          goal: str | None) -> dict[str, CommandResult]:
    """A tip to the activity's body, an intention, or a question for her mouth to ask.
    Returns what the body said to each command."""
    results: dict[str, CommandResult] = {}
    if thought.kind in ("decision", "guess") and thought.for_hands:
        body = source.body(scope)
        if body is None or "tip" not in body.controls():
            results["tip"] = CommandResult("unsupported", "no body takes tips here")
        else:
            results["tip"] = body.command(scope, Command("tip", f"(from your own thinking) {thought.for_hands}"))
            if results["tip"].status == "accepted":
                from app.services import system_signals
                system_signals.emit(scope.user_id, "mind", "hands", "tip", thought_id)
    if thought.kind == "intention":
        add_intention(scope.key, thought.for_hands or thought.conclusion)  # her hands' tip (an execution hint)
        # Memory gets it through ``to_memory``, never written here.
    if thought.conclusion or thought.question:
        from app.services import autonomous_speech
        with SessionLocal() as db:
            autonomous_speech.submit(
                db, user_id=scope.user_id, activity_id=scope.activity, source=scope.source,
                event_id=thought_id, text=thought.question or thought.conclusion,
                question=bool(thought.question), premise=problem.anchor,
                reasons=[f"question: {problem.topic}" if thought.question else f"thought: {problem.topic}"],
                **source.speaking(db, scope))
    return results


def _append(path_setting: str, record: dict) -> None:
    path = Path(path_setting)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        log.warning("mind: could not write %s", path)


def _log_hard(scope: Scope, problem: Problem, system: str, text: str, thought: HardThought) -> None:
    """Every hard thought's full input and answer, so it can be replayed (scripts/mind_hard_eval.py)."""
    _append(get_settings().mind_hard_log_path, {
        "ts": time.time(), "session": str(scope.activity), "source": scope.source, "trigger": problem.trigger,
        "topic": problem.topic, "system": system, "input": text, "kind": thought.kind,
        "conclusion": thought.conclusion, "thinking": thought.thinking, "for_hands": thought.for_hands,
        "question": thought.question, "error": thought.error, "ms": thought.latency_ms,
        "input_tokens": thought.input_tokens, "output_tokens": thought.output_tokens})


def _log_shadow(scope: Scope, problem: Problem, system: str, text: str, thought: HardThought, *,
                status: str, why: str | None) -> None:
    """A shadow source's thought: only here, never saved, said or sent to a body."""
    _append(get_settings().mind_shadow_log_path, {
        "ts": time.time(), "source": scope.source, "activity": str(scope.activity), "user": str(scope.user_id),
        "trigger": problem.trigger, "topic": problem.topic, "anchor": problem.anchor, "system": system,
        "input": text, "kind": thought.kind, "conclusion": thought.conclusion, "thinking": thought.thinking,
        "question": thought.question, "status": status, "dropped_reason": why, "error": thought.error,
        "ms": thought.latency_ms, "input_tokens": thought.input_tokens, "output_tokens": thought.output_tokens})


# --------------------------------------------------------------------------- #
# Intentions, the question, and her mouth's hand-off (Redis: shared with other processes),
# each per activity (Scope.key).
# --------------------------------------------------------------------------- #

def _intentions_key(key: uuid.UUID | str) -> str:
    return f"mind:intentions:{key}"


def add_intention(key: uuid.UUID | str, text: str) -> None:
    client = desktop_preview.redis_client()
    if client is None or not text:
        return
    name = _intentions_key(key)
    try:
        with client.pipeline() as pipe:
            pipe.rpush(name, json.dumps({"id": uuid.uuid4().hex[:8], "text": text[:300], "at": time.time()}))
            pipe.ltrim(name, -INTENTIONS_KEPT, -1)
            pipe.expire(name, INTENTION_TTL_SECONDS)
            pipe.execute()
    except redis.RedisError:
        log.warning("mind: could not keep an intention for %s", key)


def intentions(key: uuid.UUID | str) -> list[dict]:
    """Her intentions still in force, oldest first."""
    client = desktop_preview.redis_client()
    if client is None:
        return []
    try:
        items = [json.loads(raw) for raw in client.lrange(_intentions_key(key), 0, -1)]
    except redis.RedisError:
        return []
    return [i for i in items if time.time() - float(i.get("at", 0)) < INTENTION_TTL_SECONDS]


def hands_messages(session_id: uuid.UUID) -> list[dict]:
    """What her Minecraft hands follow: her mouth's tips, then her own intentions."""
    from app.services import desktop_commands

    return desktop_commands.hands_messages(session_id) + [
        {"id": f"i{i['id']}", "text": f"keep in mind (your own intention): {i['text']}", "at": i["at"]}
        for i in intentions(session_id)]


def _question_key(key: uuid.UUID | str) -> str:
    return f"mind:question:{key}"


def set_question(key: uuid.UUID | str, question: dict) -> None:
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.set(_question_key(key), json.dumps(question), ex=QUESTION_TTL_SECONDS)
    except redis.RedisError:
        log.warning("mind: could not keep a question for %s", key)


def take_question(key: uuid.UUID | str) -> dict | None:
    """Her open question, handed to her mouth's reply (so it's never also asked at a pause)."""
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.getdel(_question_key(key))
    except redis.RedisError:
        return None
    return json.loads(raw) if raw else None


def _reply_in_flight(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(ChatJob.id).where(ChatJob.user_id == user_id,
                                              ChatJob.status.in_(("pending", "processing"))).limit(1)) is not None


def ask_if_due(db: Session, source: Source, scope: Scope, at: float) -> str | None:
    """Her mouth asks the activity's open question at a natural pause: the user quiet for
    QUESTION_PAUSE_SECONDS and no reply being written. Returns what happened."""
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_question_key(scope.key))
    except redis.RedisError:
        return None
    if not raw:
        return None
    question = json.loads(raw)
    if question.get("goal") not in (None, source.current_goal(db, scope)):
        client.delete(_question_key(scope.key))
        return "question dropped: the goal changed"
    user_id = scope.user_id
    if agent_say.user_spoke_since(db, user_id, at - QUESTION_PAUSE_SECONDS) or _reply_in_flight(db, user_id):
        return None
    from app.services import autonomous_speech
    outcome = autonomous_speech.submit(
        db, user_id=user_id, activity_id=scope.activity, source=scope.source,
        event_id=question.get("thought_id") or f"question:{question.get('at')}", text=question["text"],
        question=True, reasons=[f"question: {question.get('topic', '')}"[:300]],
        **source.speaking(db, scope))
    if outcome.startswith("Proposed"):
        client.delete(_question_key(scope.key))
    return f"asked: {outcome}"


def hand_off(db: Session, user_id: uuid.UUID, *, what: str) -> str:
    """Her mouth's ``think_it_over``: think it through in the background, in the activity
    that takes hand-offs (today: her live game)."""
    from app.services import mind

    taken = next(((source, scope) for source in mind.sources() if not source.shadow
                  for scope in [source.handoff_scope(db, user_id)] if scope is not None), None)
    client = desktop_preview.redis_client()
    if taken is None or client is None:
        return "Not now: you can't think in the background at the moment. Think it through in your reply instead."
    source, scope = taken
    item = {"what": " ".join(what.split())[:300], "at": time.time(), "goal": source.current_goal(db, scope)}
    try:
        client.rpush(_handoff_key(scope.key), json.dumps(item))
        client.expire(_handoff_key(scope.key), HANDOFF_TTL_SECONDS)
    except redis.RedisError:
        return "Not now: you can't think in the background at the moment. Think it through in your reply instead."
    return ("You'll think it over in the background; your conclusion will be on your mind in a little while "
            "(and you may ask the user something from it). Answer them now as well as you can.")
