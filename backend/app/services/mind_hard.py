"""When she thinks hard, and where the conclusion goes (docs/COLUMBINA_HARD_THINKING_PLAN.md).

Her mind's tick (services/mind.py) calls ``step`` every second. Hard thinking starts on a
problem, never on the last second:

- failing: her hands' 4th and 10th failed try on one step (their counted step log);
- surprise: a result that says failed while she got something anyway (code), or the
  gate's ``hard`` when something in the world changed (not talk, her hands or her plan);
- big: a new goal with 3+ steps, or a move she taught herself run 5+ times in a row;
- handoff: her mouth's ``think_it_over`` (a reply turn: the user's words need thought);
- quiet: nothing new for MIND_HARD_QUIET_SECONDS, at most every MIND_HARD_QUIET_EVERY_SECONDS.

One runs at a time, one waits (a newer problem replaces a waiting one; a hand-off always
wins), none starts under urgent pressure. The thought (app/ai/mind/hard.py) runs on this
module's own thread; it has no tools. It's dropped when what it was about is gone (the
step is over, the goal changed), otherwise saved and sent on: a tip to her hands, an
intention her hands and mouth keep for a while, a question her mouth asks at a pause,
and always her mouth's "on your mind" and the thoughts panel.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.mind.hard import HardInput, HardThinker, HardThought, Problem, render
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, CoreMemory, DesktopSession, MindThought, User
from app.db.session import SessionLocal
from app.services import agent_say, desktop_commands, desktop_preview, game_moment

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

BIG_STEPS = 3  # a new goal with this many steps is something big
BIG_TIMES = 5  # a learned move run this many times in a row is too
INVENTORY_KEPT_SECONDS = 180  # how far back "while it ran" can look
EARLIER_WINDOW = timedelta(minutes=10)
EARLIER_KEPT = 3
QUESTION_PAUSE_SECONDS = 8.0  # the user quiet this long: a natural pause to ask
QUESTION_TTL_SECONDS = 180
INTENTION_TTL_SECONDS = 20 * 60
INTENTIONS_KEPT = 3
HANDOFF_TTL_SECONDS = 120
USER_TURN_SECONDS = 10.0
# Fresh tick lines that are about talk, her hands or her plan, not the world: a gate's "hard"
# on only these is not a surprise (the user talking is her mouth's to answer).
NOT_WORLD = ("the user said", "you said", "your hands:", "new goal:", "next step:", "session ", "learning:",
             "(first tick")
QUIET_TOPICS = (
    "{user}: what they want from today, and how they're doing",
    "the session so far: what you two did, and what's unfinished",
    "yourself: how it's going for you, and what you'd like to do",
)

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mind-hard")
_lock = threading.Lock()
_thinker: HardThinker | None = None


@dataclass
class HardState:
    """Per session, in the mind worker's process."""

    running: Problem | None = None
    waiting: Problem | None = None
    cancelled: str | None = None  # why the running thought will be dropped when it lands
    last_started_at: float = 0.0
    last_news_at: float = 0.0
    failing_seen: set = field(default_factory=set)  # (step id, failed count) already thought about
    outcome_seen_at: float = 0.0
    inventory: deque = field(default_factory=deque)  # (at, {item: count})
    started: bool = False  # the first tick sets what's already going on, without thinking about it
    goal_seen: str | None = None
    learned_seen: tuple | None = None  # the learned move run already thought about
    quiet_topic: int = 0


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #

def _counts(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def _gained(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [f"{name} +{after[name] - before.get(name, 0)}" for name in sorted(after)
            if after[name] > before.get(name, 0)]


def _inventory_at(st: HardState, at: float) -> dict[str, int] | None:
    best = None
    for when, counts in st.inventory:
        if when <= at:
            best = counts
    return best


def _goal(plan: dict | None) -> str | None:
    return (plan or {}).get("goal")


def _step(plan: dict | None, step_id: str | None) -> dict | None:
    return next((s for s in (plan or {}).get("steps") or [] if s.get("id") == step_id), None)


def failing(st: HardState, plan: dict | None, driver: dict) -> Problem | None:
    """Her hands' 4th and 10th failed try on one step."""
    from app.ai.desktop.skill_driver import STRUGGLING_AT, failure_summary

    log_ = driver.get("step") or {}
    failed = int(log_.get("failed") or 0)
    marks = [m for m in STRUGGLING_AT if failed >= m and (log_.get("id"), m) not in st.failing_seen]
    if not log_.get("id") or not marks:
        return None
    st.failing_seen.update((log_["id"], m) for m in marks)
    step = _step(plan, log_["id"]) or {}
    minutes = max(1, round((time.time() - float(log_.get("since") or time.time())) / 60))
    text = (f'Step: "{log_.get("do", "")}"' + (f' (done when: {step["done_when"]})' if step.get("done_when") else "")
            + f". Tried {log_.get('tries', 0)} times in about {minutes} min; {failed} failed: "
            + failure_summary(log_.get("failures") or []) + ".")
    if driver.get("notes"):
        text += f'\nYour hands\' note: "{driver["notes"]}".'
    return Problem("failing", text, topic=f"keeps failing: {log_.get('do', '')}"[:200],
                   query=f"{log_.get('do', '')} {failure_summary(log_.get('failures') or [])}"[:300],
                   anchor={"goal": _goal(plan), "step_id": log_["id"]})


def surprise_from_outcome(st: HardState, plan: dict | None, driver: dict, state: dict) -> Problem | None:
    """A result that says it failed while she got something anyway (live: the sheep move said
    "the target is gone" three times while the mutton went up)."""
    from app.ai.desktop.skill_driver import FAILED

    found = None
    newest = st.outcome_seen_at
    for outcome in driver.get("outcomes") or []:
        at = float(outcome.get("at") or 0)
        if at <= st.outcome_seen_at:
            continue
        newest = max(newest, at)
        if outcome.get("status") not in FAILED:
            continue
        began = at - float(outcome.get("seconds") or 0) - 1.0
        before = _inventory_at(st, began)
        if before is None:
            continue
        gained = _gained(before, _counts(state))
        if gained:
            params = ", ".join(f"{k}={v}" for k, v in (outcome.get("params") or {}).items())
            found = Problem(
                "surprise",
                f"Your hands said {outcome.get('skill')}({params}) {outcome.get('status')}: "
                f"{outcome.get('reason') or 'no reason given'}. But while it ran you got: {', '.join(gained)} "
                "(counted by the game).",
                topic=f"surprise: {outcome.get('skill')} said {outcome.get('status')} but you got {gained[0]}"[:200],
                query=f"{outcome.get('skill')} {outcome.get('reason') or ''} {' '.join(gained)}"[:300],
                anchor={"goal": _goal(plan)})
    st.outcome_seen_at = newest
    return found


def surprise_from_gate(fresh: list[str], plan: dict | None, driver: dict) -> Problem | None:
    """The gate's hard, when something in the world changed (not only talk, her hands or her plan)."""
    world = [line for line in fresh if not line.startswith(NOT_WORLD)]
    if not world:
        return None
    text = "What just happened (counted by the game):\n" + "\n".join(f"- {line}" for line in world)
    step = next((s for s in (plan or {}).get("steps") or [] if s.get("state") == "active"), None)
    if step:
        text += f'\nWhat you were doing: "{step.get("do", "")}".'
    if driver.get("notes"):
        text += f'\nYour hands\' note: "{driver["notes"]}".'
    return Problem("surprise", text, topic=f"surprise: {world[0]}"[:200], query=" ".join(world)[:300],
                   anchor={"goal": _goal(plan)})


def big(st: HardState, plan: dict | None, driver: dict, state: dict) -> Problem | None:
    """A new goal with BIG_STEPS+ steps, or a learned move run BIG_TIMES+ times in a row."""
    goal = _goal(plan)
    found = None
    if goal != st.goal_seen:
        st.goal_seen = goal
        steps = (plan or {}).get("steps") or []
        if goal and len(steps) >= BIG_STEPS:
            lines = [f'You\'re starting: "{goal}". Steps:'] + [f"  - {s.get('do', '')}" for s in steps]
            if (plan or {}).get("constraints"):
                lines.append("Rules: " + "; ".join(plan["constraints"]))
            carry = ", ".join(f"{n} x{c}" for n, c in sorted(_counts(state).items())) or "nothing"
            lines.append(f"You carry: {carry}.")
            found = Problem("big", "\n".join(lines), topic=f"before: {goal}"[:200], query=goal[:300],
                            anchor={"goal": goal})
    running = driver.get("running") or {}
    skill, times = running.get("skill") or "", (running.get("params") or {}).get("times")
    key = (skill, json.dumps(running.get("params") or {}, sort_keys=True)) if skill else None
    if key != st.learned_seen:
        st.learned_seen = key
        if skill.startswith("learned_") and isinstance(times, int) and times >= BIG_TIMES:
            found = Problem(
                "big", f"You're about to do {skill} {times} times in a row (a move you taught yourself), for "
                       f'"{goal or "your goal"}".',
                topic=f"before: {skill} x{times}"[:200], query=f"{skill} {goal or ''}"[:300], anchor={"goal": goal})
    return found


def _handoff_key(session_id: uuid.UUID | str) -> str:
    return f"mind:handoff:{session_id}"


def handoff(session_id: uuid.UUID, user: str) -> Problem | None:
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.lpop(_handoff_key(session_id))
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


def quiet(st: HardState, talk: list[dict], pressure: str, at: float, user: str) -> Problem | None:
    settings = get_settings()
    if not settings.mind_hard_quiet_seconds or pressure == "urgent":
        return None
    if at - st.last_news_at < settings.mind_hard_quiet_seconds:
        return None
    if at - st.last_started_at < settings.mind_hard_quiet_every_seconds:
        return None
    if any(m.get("role") == "user" and at - m.get("at", 0) < USER_TURN_SECONDS for m in talk):
        return None
    topic = QUIET_TOPICS[st.quiet_topic % len(QUIET_TOPICS)].format(user=user)
    st.quiet_topic += 1
    return Problem("quiet", f"Nothing is happening. Think about {topic}.", topic=f"quiet: {topic}"[:200],
                   query=topic[:300])


def _better(waiting: Problem | None, new: Problem) -> Problem:
    """A newer problem replaces a waiting one; the mouth's hand-off always wins."""
    if waiting is not None and waiting.trigger == "handoff" and new.trigger != "handoff":
        return waiting
    return new


def step(session: DesktopSession, st: HardState, *, state: dict, plan: dict | None, talk: list[dict],
         fresh: list[str], gate_hard: bool, pressure: str, at: float, user: str) -> str | None:
    """One tick: find a problem, start a hard thought if none is running. Returns what it did."""
    if not st.started:
        st.started, st.last_news_at, st.goal_seen = True, at, _goal(plan)
    if any(not line.startswith("(first tick") for line in fresh):  # a first tick (or after a gap) isn't news
        st.last_news_at = at
    st.inventory.append((at, _counts(state)))
    while st.inventory and st.inventory[0][0] < at - INVENTORY_KEPT_SECONDS:
        st.inventory.popleft()
    goal = _goal(plan)
    # The user changed the goal: a thought about the old one is dropped when it lands, a waiting one now.
    if (plan or {}).get("source") == "columbina_user_turn":
        if st.running is not None and st.running.anchor.get("goal") not in (None, goal) and not st.cancelled:
            st.cancelled = "the goal changed"
        if st.waiting is not None and st.waiting.anchor.get("goal") not in (None, goal):
            st.waiting = None

    driver = desktop_commands.load_driver_state(session.id) or {}
    for found in (handoff(session.id, user), failing(st, plan, driver),
                  surprise_from_outcome(st, plan, driver, state),
                  surprise_from_gate(fresh, plan, driver) if gate_hard else None,
                  big(st, plan, driver, state), quiet(st, talk, pressure, at, user)):
        if found is not None:
            st.waiting = _better(st.waiting, found)
    if st.waiting is None:
        return None
    if st.running is not None:
        return f"hard waiting ({st.waiting.trigger})"
    if pressure == "urgent":
        return f"hard postponed ({st.waiting.trigger}): urgent"
    if st.waiting.trigger != "handoff" and at - st.last_started_at < get_settings().mind_hard_gap_seconds:
        return f"hard waiting ({st.waiting.trigger}): gap"
    problem, st.waiting = st.waiting, None
    start(session, st, problem, at)
    return f"hard: {problem.trigger}"


def start(session: DesktopSession, st: HardState, problem: Problem, at: float) -> None:
    from app.services import mind

    st.running, st.cancelled, st.last_started_at = problem, None, at
    mind._set_thinking(session.id, problem.topic)
    _pool.submit(run, session.id, session.owner_user_id, st, problem, at)


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


def _brief(state: dict | None) -> str:
    """Her state text up to the long shape-of-the-place part."""
    lines = []
    for line in (state or {}).get("text", "").splitlines():
        if line.startswith(("Ways", "Land ", "Highest ground", "Surface", "Way out", "Openings")):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _conversation(db: Session, user_id: uuid.UUID, now: datetime) -> tuple[dict, ...]:
    from app.ai.mind.hard import CONVERSATION_KEPT

    conversation_id = db.scalar(select(Conversation.id).where(Conversation.user_id == user_id)
                                .order_by(Conversation.updated_at.desc()).limit(1))
    if conversation_id is None:
        return ()
    rows = db.execute(
        select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.content != "")
        .order_by(ChatMessage.created_at.desc()).limit(CONVERSATION_KEPT)).all()
    out = []
    for role, content, created in reversed(rows):
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        out.append({"role": role, "content": content[:500], "ago": (now - created).total_seconds()})
    return tuple(out)


def _character(db: Session) -> tuple[str, ...]:
    core = db.scalar(select(CoreMemory).where(CoreMemory.is_active.is_(True)).limit(1))
    character = ((core.identity or {}).get("character") or {}) if core else {}
    return tuple(item.get("text", "") for section in ("values", "principles")
                 for item in character.get(section) or [] if item.get("text"))


def _earlier(db: Session, session_id: uuid.UUID, topic: str, now: datetime) -> tuple[str, ...]:
    rows = db.scalars(
        select(MindThought.text).where(MindThought.session_id == session_id, MindThought.level == "hard",
                                       MindThought.topic == topic, MindThought.status.in_(("done", "stale")),
                                       MindThought.created_at >= now - EARLIER_WINDOW)
        .order_by(MindThought.created_at.desc()).limit(EARLIER_KEPT)).all()
    return tuple(reversed(rows))


def _learned(db: Session, user_id: uuid.UUID) -> tuple[str, ...]:
    from app.services import desktop_game, game_learning, game_pack, game_skills

    lines = desktop_game._learned_lines(db, user_id, game_learning.game_for_profile(game_pack.PACK_PROFILE_ID))
    lines += game_skills.summary_lines(db, user_id, desktop_game.PACK_GAME)
    return tuple(line.strip() for line in lines if line.strip())


def gather(db: Session, session: DesktopSession, problem: Problem) -> tuple[HardInput, game_moment.Moment, str]:
    from app.ai.desktop.pack_client import PackClient, PackError
    from app.services import long_term_memory

    user = db.get(User, session.owner_user_id)
    name = user.name if user and user.name else "the user"
    try:
        state = PackClient.from_settings().state()
    except PackError:
        state = None
    now = datetime.now(timezone.utc)
    plan = session.plan or {}
    active = next((s for s in plan.get("steps") or [] if s.get("state") == "active"), None)
    goal = plan.get("goal") and (plan["goal"] + (f'; current step: {active["do"]}' if active else ""))
    inp = HardInput(
        problem=problem, user=name, now=_brief(state), goal=goal,
        conversation=_conversation(db, session.owner_user_id, now),
        memories=long_term_memory.retrieve_for_prompt(session.owner_user_id, problem.query),
        learned=_learned(db, session.owner_user_id), earlier=_earlier(db, session.id, problem.topic, now),
        character=_character(db),
    )
    return inp, game_moment.capture(db, session, state), name


def dropped(db: Session, session: DesktopSession | None, problem: Problem, cancelled: str | None) -> str | None:
    """Why the thought no longer applies, or None."""
    if cancelled:
        return cancelled
    if session is None or session.state in ("ended", "failed"):
        return "the game ended"
    plan = session.plan or {}
    goal_changed = problem.anchor.get("goal") not in (None, plan.get("goal"))
    if problem.trigger == "failing":
        step_ = _step(plan, problem.anchor.get("step_id"))
        if goal_changed:
            return "the goal changed"
        if step_ is None or step_.get("state") not in ("active", "pending"):
            return "that step is over"
    if problem.trigger == "big" and goal_changed:
        return "the goal changed"
    if problem.trigger == "handoff" and goal_changed and agent_say.user_spoke_since(
            db, session.owner_user_id, float(problem.anchor.get("at") or 0) + 1.0):
        return "you two moved on"
    return None


def run(session_id: uuid.UUID, user_id: uuid.UUID, st: HardState, problem: Problem, at: float) -> None:
    from app.services import mind

    try:
        with SessionLocal() as db:
            session = db.get(DesktopSession, session_id)
            if session is None:
                return
            inp, moment, name = gather(db, session, problem)
            db.commit()
        situation = mind.situation()
        brain = thinker()
        thought = brain.think(inp, situation)
        _log_hard(session_id, problem, brain.instructions(problem.trigger, name, situation), render(inp), thought)
        if thought.error or thought.kind is None:
            return
        with SessionLocal() as db:
            session = db.get(DesktopSession, session_id)
            why = dropped(db, session, problem, st.cancelled)
            # A surprise is about a moment: out of date once she's paused or playing again, or a lesson
            # ended (not when she just got more loot, which is what a fight looks like).
            outdated = (game_moment.since(db, moment, frozenset({game_moment.STATE, game_moment.LESSON}))
                        if problem.trigger == "surprise" else [])
            status = "dropped" if why else "stale" if outdated else "done"
            saved = mind.save_thought(
                db, user_id=user_id, session_id=session_id, level="hard", text=thought.conclusion, status=status,
                topic=problem.topic, trigger=problem.trigger, conclusion_kind=thought.kind,
                thinking=thought.thinking, for_hands=thought.for_hands, question=thought.question,
                dropped_reason=why)
            if status == "done":
                send_on(session, saved.id, problem, thought)
    except Exception:  # noqa: BLE001 - a hard thought that fails is just not thought
        log.exception("mind: hard thought failed for session %s", session_id)
    finally:
        st.running = None
        mind._set_thinking(session_id, None)


def send_on(session: DesktopSession, thought_id: uuid.UUID, problem: Problem, thought: HardThought) -> None:
    """A tip to her hands, an intention, or a question for her mouth to ask."""
    if thought.kind in ("decision", "guess") and thought.for_hands:
        desktop_commands.send_hands_message(session.id, f"(from your own thinking) {thought.for_hands}")
    if thought.kind == "intention":
        add_intention(session.id, thought.for_hands or thought.conclusion)
    if thought.kind == "question" and thought.question:
        set_question(session.id, {"text": thought.question, "at": time.time(), "thought_id": str(thought_id),
                                  "goal": (session.plan or {}).get("goal"), "topic": problem.topic})


def _log_hard(session_id: uuid.UUID, problem: Problem, system: str, text: str, thought: HardThought) -> None:
    """Every hard thought's full input and answer, so it can be replayed (scripts/mind_hard_eval.py)."""
    path = Path(get_settings().mind_hard_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    record = {"ts": time.time(), "session": str(session_id), "trigger": problem.trigger, "topic": problem.topic,
              "system": system, "input": text, "kind": thought.kind, "conclusion": thought.conclusion,
              "thinking": thought.thinking, "for_hands": thought.for_hands, "question": thought.question,
              "error": thought.error, "ms": thought.latency_ms, "input_tokens": thought.input_tokens,
              "output_tokens": thought.output_tokens}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        log.warning("mind: could not write %s", path)


# --------------------------------------------------------------------------- #
# Intentions, the question, and her mouth's hand-off (Redis: shared with other processes)
# --------------------------------------------------------------------------- #

def _intentions_key(session_id: uuid.UUID | str) -> str:
    return f"mind:intentions:{session_id}"


def add_intention(session_id: uuid.UUID, text: str) -> None:
    client = desktop_preview.redis_client()
    if client is None or not text:
        return
    key = _intentions_key(session_id)
    try:
        with client.pipeline() as pipe:
            pipe.rpush(key, json.dumps({"id": uuid.uuid4().hex[:8], "text": text[:300], "at": time.time()}))
            pipe.ltrim(key, -INTENTIONS_KEPT, -1)
            pipe.expire(key, INTENTION_TTL_SECONDS)
            pipe.execute()
    except redis.RedisError:
        log.warning("mind: could not keep an intention for session %s", session_id)


def intentions(session_id: uuid.UUID) -> list[dict]:
    """Her intentions still in force, oldest first."""
    client = desktop_preview.redis_client()
    if client is None:
        return []
    try:
        items = [json.loads(raw) for raw in client.lrange(_intentions_key(session_id), 0, -1)]
    except redis.RedisError:
        return []
    return [i for i in items if time.time() - float(i.get("at", 0)) < INTENTION_TTL_SECONDS]


def hands_messages(session_id: uuid.UUID) -> list[dict]:
    """What her hands follow: her mouth's tips, then her own intentions."""
    return desktop_commands.hands_messages(session_id) + [
        {"id": f"i{i['id']}", "text": f"keep in mind (your own intention): {i['text']}", "at": i["at"]}
        for i in intentions(session_id)]


def _question_key(session_id: uuid.UUID | str) -> str:
    return f"mind:question:{session_id}"


def set_question(session_id: uuid.UUID, question: dict) -> None:
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.set(_question_key(session_id), json.dumps(question), ex=QUESTION_TTL_SECONDS)
    except redis.RedisError:
        log.warning("mind: could not keep a question for session %s", session_id)


def take_question(session_id: uuid.UUID) -> dict | None:
    """Her open question, handed to her mouth's reply (so it's never also asked at a pause)."""
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.getdel(_question_key(session_id))
    except redis.RedisError:
        return None
    return json.loads(raw) if raw else None


def _reply_in_flight(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(ChatJob.id).where(ChatJob.user_id == user_id,
                                              ChatJob.status.in_(("pending", "processing"))).limit(1)) is not None


def ask_if_due(db: Session, session: DesktopSession, at: float) -> str | None:
    """Her mouth asks the open question at a natural pause: the user quiet for
    QUESTION_PAUSE_SECONDS and no reply being written. Returns what happened."""
    client = desktop_preview.redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_question_key(session.id))
    except redis.RedisError:
        return None
    if not raw:
        return None
    question = json.loads(raw)
    if question.get("goal") not in (None, (session.plan or {}).get("goal")):
        client.delete(_question_key(session.id))
        return "question dropped: the goal changed"
    user_id = session.owner_user_id
    if agent_say.user_spoke_since(db, user_id, at - QUESTION_PAUSE_SECONDS) or _reply_in_flight(db, user_id):
        return None
    if not client.delete(_question_key(session.id)):
        return None  # a reply took it meanwhile
    outcome = agent_say.deliver(db, user_id=user_id, text=question["text"],
                                reasons=[f"question: {question.get('topic', '')}"[:300]],
                                commentary_mode=session.commentary_mode)
    if outcome.startswith("Said") and question.get("thought_id"):
        row = db.get(MindThought, uuid.UUID(question["thought_id"]))
        if row is not None:
            row.said = question["text"]
            db.commit()
    return f"asked: {outcome}"


def hand_off(db: Session, user_id: uuid.UUID, *, what: str) -> str:
    """Her mouth's ``think_it_over``: think it through in the background."""
    from app.services import desktop_game, mind

    if not mind.enabled():
        return "Not now: your slow thinking is off. Think it through in your reply instead."
    session = desktop_game._live(db, user_id)
    client = desktop_preview.redis_client()
    if client is None:
        return "Not now: you can't think in the background at the moment. Think it through in your reply instead."
    item = {"what": " ".join(what.split())[:300], "at": time.time(), "goal": (session.plan or {}).get("goal")}
    try:
        client.rpush(_handoff_key(session.id), json.dumps(item))
        client.expire(_handoff_key(session.id), HANDOFF_TTL_SECONDS)
    except redis.RedisError:
        return "Not now: you can't think in the background at the moment. Think it through in your reply instead."
    return ("You'll think it over in the background; your conclusion will be on your mind in a little while "
            "(and you may ask the user something from it). Answer them now as well as you can.")
