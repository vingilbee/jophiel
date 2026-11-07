"""Student 2 live: read her situation from the stores, ask the decision model, carry out what it decides.

The default provider is EventTeacher's single structured decision call. Full training examples go to
qwen-6/live_teacher.jsonl on a background writer when the caller logs the decision. Pending writes drain on
normal process exit; an abrupt process termination can lose queued examples. No training conversion runs here.
Set COGNITION_DECISION_PROVIDER=student to use the local model described below instead.

The model is whatever ``COGNITION_STUDENT_DIR`` points at: a folder made by ``scripts/student_model.py prepare``,
served by ``scripts/student_model.py serve`` at ``COGNITION_STUDENT_URL``. Its ``student.json`` says how to talk to
it (system prompt, how its training situations were rendered, chat-template switches), so swapping qwen-4 for
qwen-5 is a new folder and a restart of the server, with no code change here.

- ``gather`` reads live state (read only: it never opens a context or creates a row) into ``state.student_two.Live``,
  with ``memory_for``: the memory check that already ran before cognition, as MEMORY (snapshot v5);
  ``gather_result`` does the same for a finished thought or background work (no new message: whether and how to
  bring it up is the decision).
- ``decide`` renders it, calls the model, parses and checks the answer. A server that is down, an answer that
  isn't JSON, or one ``events.check`` rejects is ``unavailable`` / ``invalid``: said as such, never read as silence.
- ``apply`` carries out the work part where an owner exists (background recall and thinking go through the
  shared-state route instead; taking something up is the mouth's, with its tools). What it couldn't carry out is
  returned and traced, never claimed.

Lessons (docs/learning-system/03-implementation.md): ``gather`` / ``gather_result`` add a bounded selection of the
user's accepted lessons (``memory_lessons.for_decision``). The teacher reads them (rendering ``lessons-v1``, with
``lessons.GUIDE`` and a ``lessons_weighed`` field for its own account); without lessons its input is exactly
``memory-v1``. A local model reads them only if its manifest's rendering shows them. Every decision with a capture
records which lesson revisions it had and whether it received them (``memory_lessons.record_uses``).

One JSON line per decision in ``COGNITION_STUDENT_LOG_PATH`` (ids, choices, timings and problems; no message text).
"""
from __future__ import annotations

import json
import fcntl
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.learning import lessons as LS
from app.ai.recall.contracts import MemoryRecord, RecallResult, origin_label
from app.ai.state import student_two as S
from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES, ChatMessage, CoordinatorWork, DesktopSession, MemoryConflict, MemoryContext,
    MemoryManagerState, MindThought, WorkingItem,
)

logger = logging.getLogger(__name__)
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THOUGHT_WINDOW = timedelta(minutes=15)
GOALS_SHOWN = 4
_capture_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teacher-data")


def _path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(BACKEND, path)


@lru_cache(maxsize=4)
def _manifest(path: str, mtime: float) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def manifest() -> dict[str, Any]:
    """The served model's ``student.json`` (re-read when it changes)."""
    path = os.path.join(_path(get_settings().cognition_student_dir), "student.json")
    return _manifest(path, os.path.getmtime(path))


# --------------------------------------------------------------------------- #
# Reading live state
# --------------------------------------------------------------------------- #


def rendering() -> str | None:
    """How the served model's training situations were rendered (None: unknown or no model). For the teacher, the
    base rendering: a decision given lessons is rendered ``lessons-v1`` (memory-v1 plus LESSONS)."""
    if get_settings().cognition_decision_provider == "teacher":
        return "memory-v1"
    try:
        return manifest().get("rendering")
    except (OSError, ValueError):
        return None


def lessons_supported() -> bool:
    """Whether the decision model's input shows lessons: the teacher's does; a local model's only if its manifest's
    rendering was trained with them."""
    if get_settings().cognition_decision_provider == "teacher":
        return True
    try:
        return manifest().get("rendering") in S.LESSON_RENDERINGS
    except (OSError, ValueError):
        return False


def _lessons(db: Session, user_id: uuid.UUID, query: str) -> dict[str, Any]:
    """The lessons offered to one decision, as ``Live`` fields. Read only; a failure is no lessons, said."""
    from app.services import memory_lessons

    if not get_settings().lessons_supply_enabled:
        return {"lessons_note": "lesson supply is switched off (baseline)"}
    try:
        with db.begin_nested():
            # Tentative inferences too, labelled as guesses (post-audit 3): they can inform how she reads and
            # answers now; they are never asserted.
            supply = memory_lessons.for_decision(db, user_id, query, include_tentative=True)
    except Exception as exc:  # noqa: BLE001 - this decision goes without lessons
        logger.exception("student_two: reading lessons failed")
        return {"lessons_note": f"reading lessons failed: {type(exc).__name__}"}
    if supply.views:
        from app.services import system_signals
        system_signals.emit(user_id, "memory", "cognition", "lessons")
    return {"lessons": supply.views, "lessons_left_out": supply.left_out,
            "lessons_note": None if supply.views or supply.left_out else "no lessons with live support"}


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - a bad zone name is UTC, as elsewhere
        return ZoneInfo("UTC")


def live_game(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID | None) -> DesktopSession | None:
    """The game session this conversation is the talk of, while it lives."""
    if conversation_id is None:
        return None
    return db.scalar(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id, DesktopSession.conversation_id == conversation_id,
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES)).order_by(DesktopSession.started_at.desc()).limit(1))


def _turn_game(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID | None) -> DesktopSession | None:
    """Read the current game even when this conversation is not its commentary target."""
    linked = live_game(db, user_id, conversation_id)
    if linked is not None or not get_settings().desktop_agent_enabled:
        return linked
    return db.scalar(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id,
        DesktopSession.profile_id.in_(("minecraft_pack", "minecraft_java")),
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES),
    ).order_by(DesktopSession.started_at.desc()).limit(1))


def _hands_now(game: DesktopSession | None) -> str | None:
    if game is None or game.state != "running":
        return None
    from redis import RedisError
    from app.services import desktop_commands

    try:
        driver = desktop_commands.load_driver_state(game.id) or {}
    except (RedisError, ValueError):
        return None
    running = driver.get("running") or {}
    if not running.get("skill"):
        return None
    params = ", ".join(f"{key}={value}" for key, value in (running.get("params") or {}).items()
                       if key != "time_limit")
    return f"{running['skill']}({params})"


def _lines(db: Session, conversation_id: uuid.UUID, before_sequence: int | None,
           seen: list[str] | None = None) -> list[S.Line]:
    """``seen``: collects the shown messages' refs (the capture's sources)."""
    from app.services.heard import heard_text

    query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id, ChatMessage.content != "")
    if before_sequence is not None:
        query = query.where(ChatMessage.conversation_sequence < before_sequence)
    rows = list(db.scalars(query.order_by(ChatMessage.conversation_sequence.desc()).limit(S.WINDOW)))
    if seen is not None:
        seen.extend(f"message:{r.id}" for r in reversed(rows))
    return [S.Line("user" if r.role == "user" else "columbina",
                   heard_text(r.content, r.heard_chars) if r.role == "assistant" else r.content, _aware(r.created_at))
            for r in reversed(rows)]


def _goal_context(db: Session, user_id: uuid.UUID, game: DesktopSession | None) -> uuid.UUID | None:
    if game is not None:
        if game.goal_item_id is not None:
            goal = db.get(WorkingItem, game.goal_item_id)
            if goal is not None:
                return goal.context_id
        return db.scalar(select(MemoryContext.id).where(
            MemoryContext.user_id == user_id, MemoryContext.source == game.profile_id, MemoryContext.status != "closed")
            .order_by(MemoryContext.last_active_at.desc()).limit(1))
    state = db.get(MemoryManagerState, user_id)
    return state.focus_context_id if state is not None else None


def _goals(db: Session, user_id: uuid.UUID, context_id: uuid.UUID | None,
           game: DesktopSession | None) -> list[S.Goal]:
    if context_id is None:
        return []
    rows = list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.context_id == context_id, WorkingItem.kind == "goal",
        WorkingItem.closed_at.is_(None)).order_by(WorkingItem.created_at.desc()).limit(GOALS_SHOWN)))
    if game is not None and game.goal_item_id is not None:   # the goal the game plays for goes first
        rows.sort(key=lambda r: r.id != game.goal_item_id)
    return [S.Goal(str(r.id), r.text, "columbina" if r.owner == "columbina" else "user",
                   "active" if r.status == "active" else "on_hold",
                   r.done_when or "the user is satisfied with it", _aware(r.created_at),
                   completion="objective" if r.done_when else "subjective") for r in reversed(rows)]


_WORK_STATE = {"queued": "queued", "waiting_for_memory": "queued", "running": "running"}


def _work(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID | None,
          game: DesktopSession | None, *, exclude: uuid.UUID | None = None) -> list[S.Work]:
    out = []
    if conversation_id is not None:
        for w in db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == user_id, CoordinatorWork.conversation_id == conversation_id,
                CoordinatorWork.kind.in_(("recall", "mind")), CoordinatorWork.status.in_(tuple(_WORK_STATE)),
                CoordinatorWork.id != exclude if exclude else True)
                .order_by(CoordinatorWork.created_at)):
            subject = (w.input.get("think_about") or w.input.get("message") or "").strip()[:160]
            out.append(S.Work(f"coordinator:{w.id}", "recall" if w.kind == "recall" else "thinking",
                              subject or "their message", _WORK_STATE[w.status], _aware(w.created_at)))
    if game is not None and game.plan:
        step = next((st for st in game.plan.get("steps", []) if st.get("state") == "active"), None)
        if step is not None:
            out.append(S.Work(f"step:{game.id}:{step.get('id')}", "execution", step.get("do", "a step"),
                              "running" if game.state == "running" else "paused", _aware(game.updated_at),
                              goal_key=str(game.goal_item_id) if game.goal_item_id else None))
    if game is not None:
        out += _trial_work(db, game)
    return out


_TRIAL_STATE = {"selected": "queued", "preparing": "queued", "executing": "running", "paused": "paused"}


def _trial_work(db: Session, game: DesktopSession) -> list[S.Work]:
    """A bounded experiment cognition chose, while it's under way: work she can pause, resume or stop."""
    from app.ai.learning import trials as T
    from app.db.models import LearningTrial
    from app.services import learning_trials

    trial = db.scalar(select(LearningTrial).where(LearningTrial.session_id == game.id,
                                                  LearningTrial.status.in_(tuple(_TRIAL_STATE))))
    if trial is None:
        return []
    proposal = learning_trials.current_revision(db, trial, trial.selected_revision).proposal
    alt = T.alternative(proposal, trial.selected_alternative) or {}
    return [S.Work(f"trial:{trial.id}", "execution", f"experiment: {alt.get('action', 'a trial')}"[:160],
                   _TRIAL_STATE[trial.status], _aware(trial.selected_at or trial.created_at))]


_THOUGHT_KIND = {"question": "question", "intention": "intention"}


def _thoughts(db: Session, activity: uuid.UUID | None, now: datetime,
              exclude: str | None = None) -> tuple[S.Thought | None, list[S.Thought]]:
    """Her own thoughts in this activity: the game session, or else the conversation."""
    if activity is None:
        return None, []
    rows = [r for r in db.scalars(select(MindThought).where(
        MindThought.activity_id == activity, MindThought.status == "done",
        MindThought.created_at >= now - THOUGHT_WINDOW).order_by(MindThought.created_at.desc()).limit(4))
        if str(r.id) != exclude][:3]
    made = [S.Thought(str(r.id), _THOUGHT_KIND.get(r.conclusion_kind or "", "conclusion"), r.question or r.text,
                      _aware(r.created_at), said=bool(r.said)) for r in rows]
    return (made[0] if made else None), list(reversed(made[1:]))


def _activity(game: DesktopSession | None, *, goal: str | None = None) -> S.Activity | None:
    if game is None:
        return None
    plan = game.plan or {}
    step = next((st.get("do") for st in plan.get("steps", []) if st.get("state") == "active"), None)
    situation = "Playing Minecraft together"
    if goal:
        situation += f"; goal: {goal}"
    if plan.get("goal") and plan["goal"] != goal:
        situation += f"; the plan: {plan['goal']}"
    if step:
        situation += f"; current step: {step}"
    state = "running" if game.state == "running" else "paused"
    by = "none"
    if state == "paused":
        by = "user" if game.reason_code == "user_pause" else "columbina"
    return S.Activity(str(game.id), state, situation, by)


# recall.section_of -> the snapshot's sections (an unresolved fact stays "holds", with the user's words).
_SECTION = {"standing": "always", "unresolved": "holds", "current": "holds", "open": "open", "past": "past"}


def memory_for(db: Session, result: RecallResult | None, *, failed: bool = False) -> S.Memory | None:
    """The memory check before cognition as Student 2 reads it: the same records the mouth gets, with an open
    disagreement's words from ``memory_conflicts`` (the newest per fact) and whether more than one memory fits.
    ``failed``: the search itself failed (what's shown is the answer before's references, re-read)."""
    from app.services import recall

    if result is None:
        return None
    records = list(result.records)
    facts = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "fact"]
    open_rows: dict[uuid.UUID, MemoryConflict] = {}
    if facts:
        for row in db.scalars(select(MemoryConflict).where(MemoryConflict.fact_id.in_(facts),
                                                           MemoryConflict.status == "open")
                              .order_by(MemoryConflict.created_at)):
            open_rows[row.fact_id] = row
    out = []
    for r in records:
        section = recall.section_of(r)
        conflict = open_rows.get(uuid.UUID(r.ref.id)) if r.ref.kind == "fact" else None
        out.append(S.Remembered(_SECTION.get(section, "holds"), _record_text(r), _aware(r.recorded_at),
                                conflict.user_words if conflict else "",
                                _aware(conflict.created_at) if conflict and conflict.created_at else None))
    search = "failed" if failed or result.status == "failed" else "found" if out else "nothing_found"
    return S.Memory(search, tuple(out), bool(result.coverage.get("references_ambiguous")))


def _record_text(r: MemoryRecord) -> str:
    """Her own reasoning is never put as a fact: an item says whose it is ("You suspected (not confirmed): ...")."""
    label = origin_label(r) if r.ref.kind == "item" else None
    return f"{label}: {r.content}" if label else r.content


def _sources(seen: list[str], goals, work, thoughts, *extra: str) -> tuple[str, ...]:
    refs = list(seen) + [f"item:{g.key}" for g in goals]
    refs += [f"work:{w.key.split(':', 1)[1]}" for w in work if w.key.startswith("coordinator:")]
    refs += [f"thought:{t.key}" for t in thoughts if t is not None and not t.key.startswith("coordinator:")]
    return tuple(dict.fromkeys([*refs, *(e for e in extra if e)]))


def gather(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None,
           trigger_message_id: uuid.UUID | None, message: str, zone: str | None,
           now: datetime | None = None, memory: S.Memory | None = None,
           memory_refs: tuple[str, ...] = ()) -> S.Live:
    """``memory_refs``: the records behind ``memory`` (``fact:<id>``, ...), kept only as the capture's sources."""
    now = now or datetime.now(timezone.utc)
    trigger = db.get(ChatMessage, trigger_message_id) if trigger_message_id else None
    before = trigger.conversation_sequence if trigger is not None else None
    from app.services import capability_registry, memory_manager

    game = _turn_game(db, user_id, conversation_id)
    thought, earlier = _thoughts(db, game.id if game else conversation_id, now)
    goal = memory_manager.session_goal(db, game) if game else None
    seen: list[str] = []
    lines = tuple(_lines(db, conversation_id, before, seen)) if conversation_id else ()
    goals = tuple(_goals(db, user_id, _goal_context(db, user_id, game), game))
    work = tuple(_work(db, user_id, conversation_id, game))
    lessons = _lessons(db, user_id, message)
    return S.Live(
        trigger="user_message", domain="minecraft" if game is not None else "chat",
        now=now.astimezone(_zone(zone)), lines=lines,
        new_message=message, goals=goals, work=work, thought=thought, earlier_thoughts=tuple(earlier),
        activity=_activity(game, goal=goal.text if goal else None), memory=memory,
        capabilities=capability_registry.for_user(db, user_id),
        hands_now=_hands_now(game), activity_state=game.state if game else None,
        sources=_sources(seen, goals, work, (thought, *earlier),
                         f"message:{trigger_message_id}" if trigger_message_id else "", *memory_refs), **lessons)


def gather_trial(db: Session, trial, proposal: dict, game: DesktopSession, *, zone: str | None = None,
                 now: datetime | None = None) -> S.Live:
    """A trial learning proposes, as Student 2 reads it (trigger component_proposal): each alternative one
    proposal [P*] with its prediction, cost, uncertainty and whether learning recommends it, competing with the
    goal the game plays for (her hands do one thing at a time). The game and its talk are read as they are now."""
    from app.ai.learning import trials as T

    now = now or datetime.now(timezone.utc)
    thought, earlier = _thoughts(db, game.id, now)
    seen: list[str] = []
    lines = tuple(_lines(db, game.conversation_id, None, seen)) if game.conversation_id else ()
    goals = tuple(_goals(db, trial.user_id, _goal_context(db, trial.user_id, game), game))
    work = tuple(_work(db, trial.user_id, game.conversation_id, game))
    competes = str(game.goal_item_id) if game.goal_item_id and any(g.key == str(game.goal_item_id) for g in goals) \
        else None
    because = [d.get("reason") or "" for d in trial.deferrals or []]
    proposals = tuple(S.Proposal(f"trial:{trial.id}:{trial.revision}:{v['key']}", "curriculum", v["goal"],
                                 v["purpose"], competes) for v in T.proposal_views(proposal, deferred_because=because))
    lessons = _lessons(db, trial.user_id, proposal.get("hypothesis") or "")
    earlier_all = tuple(t for t in (thought, *earlier) if t is not None)
    return S.Live(
        trigger="component_proposal", domain="minecraft", now=now.astimezone(_zone(zone)), lines=lines,
        new_message="", goals=goals, work=work, thought=None, earlier_thoughts=earlier_all, activity=_activity(game),
        proposals=proposals,
        sources=_sources(seen, goals, work, earlier_all, f"trial:{trial.id}"), **lessons)


def _game_of(db: Session, work: CoordinatorWork) -> DesktopSession | None:
    if work.input.get("source") == "minecraft" and work.activity_id:
        game = db.get(DesktopSession, work.activity_id)
        if game is not None and game.owner_user_id == work.user_id and game.state not in DESKTOP_TERMINAL_STATES:
            return game
    return live_game(db, work.user_id, work.conversation_id)


def gather_result(db: Session, work: CoordinatorWork, *, zone: str | None = None,
                  now: datetime | None = None) -> S.Live:
    """A finished coordinator item as Student 2 reads it: her thought just finished ([T1], with the thinking
    behind it for a request), or work that ended without a conclusion. The conversation is read as it is now."""
    now = now or datetime.now(timezone.utc)
    r = work.result or {}
    game = _game_of(db, work)
    autonomous = bool(work.input.get("autonomous"))
    _, earlier = _thoughts(db, game.id if game else work.conversation_id, now,
                           exclude=str(work.input.get("event_id") or ""))
    conclusion = (r.get("conclusion") or "").strip()
    question = (r.get("question") or "").strip()
    finished = work.status == "completed" and bool(conclusion or question)
    thought = None
    if finished:
        kind = _THOUGHT_KIND.get(r.get("kind") or "", "conclusion")
        thought = S.Thought(f"coordinator:{work.id}", kind, question if kind == "question" and question else conclusion,
                            min(now, _aware(work.updated_at)) if work.updated_at else now, origin="just_finished")
    work_items = []
    if not autonomous:
        uncertain = r.get("kind") == "guess" or r.get("evidence_status") not in (None, "matched")
        unresolved = ("it is a guess" if r.get("kind") == "guess" else "") or (
            f"memory: {r['evidence_status']}" if uncertain and r.get("evidence_status") else "")
        state = "completed" if finished else "failed"
        outcome = (conclusion or question)[:300] if finished else (
            f"didn't reach a conclusion ({work.error_code or work.status})")
        subject = (work.input.get("think_about") or work.input.get("message") or "their message").strip()[:160]
        work_items.append(S.Work(f"coordinator:{work.id}", "thinking", subject, state, _aware(work.created_at),
                                 outcome=outcome, unresolved=unresolved, for_thought=finished))
    work_items += _work(db, work.user_id, work.conversation_id, game, exclude=work.id)
    seen: list[str] = []
    lines = tuple(_lines(db, work.conversation_id, None, seen)) if work.conversation_id else ()
    goals = tuple(_goals(db, work.user_id, _goal_context(db, work.user_id, game), game))
    query = " ".join(filter(None, (conclusion or question, lines[-1].text if lines else "")))
    lessons = _lessons(db, work.user_id, query)
    activity = _activity(game)
    if autonomous and activity is not None:
        from dataclasses import replace

        activity = replace(activity, situation=(
            f"{activity.situation}. User's game commentary setting: {game.commentary_mode}. "
            "Decide whether this thought adds something useful to the recent conversation. "
            "Stay silent if the reply already covered it or it would duplicate a reply in progress."
        ))
    return S.Live(
        trigger="thought_result" if finished else "work_outcome", domain="minecraft" if game is not None else "chat",
        now=now.astimezone(_zone(zone or work.input.get("zone"))),
        lines=lines, new_message="", goals=goals, work=tuple(work_items),
        thought=thought, earlier_thoughts=tuple(earlier), activity=activity,
        sources=_sources(seen, goals, work_items, earlier, f"work:{work.id}",
                         *(f"{k}" for k in (r.get("evidence_refs") or ()) if isinstance(k, str))), **lessons)


# --------------------------------------------------------------------------- #
# Deciding
# --------------------------------------------------------------------------- #


@dataclass
class Outcome:
    status: str  # ok / invalid / unavailable
    situation: S.Situation | None = None
    decision: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    effect: str | None = None  # what ``apply`` did with the work part
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    teacher_capture: dict[str, Any] | None = field(default=None, repr=False)
    # The learning capture this decision was recorded under (``decision_id`` is its attempt id), or None.
    capture: Any = field(default=None, repr=False)
    # Lessons: whether its input showed the lessons offered, why not, and its own account of each.
    lessons_received: bool = False
    lessons_reason: str | None = None
    lessons_weighed: list[dict[str, Any]] = field(default_factory=list)
    lesson_problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.decision is not None


def _ask(model: dict[str, Any], text: str) -> str:
    s = get_settings()
    body = {"messages": [{"role": "system", "content": model["system"]}, {"role": "user", "content": text}],
            "temperature": 0.0, "max_tokens": model.get("max_tokens", 400),
            "chat_template_kwargs": model.get("chat_template_kwargs", {})}
    request = urllib.request.Request(f"{s.cognition_student_url.rstrip('/')}/v1/chat/completions",
                                     json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=s.cognition_student_timeout_seconds) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def decide(live: S.Live, ask=None, *, capture=None) -> Outcome:
    """One decision. Never raises: what went wrong is the outcome's status and error.
    ``capture``: a ``learning_capture.Ctx`` (its ``attempt_id`` becomes the decision id); the attempt is then
    recorded here, input and output exactly as they were, before anything carries the decision out."""
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    exact: dict[str, Any] = {}
    live = _current_lessons(live)
    if get_settings().cognition_decision_provider == "teacher":
        outcome = _teacher_decide(live, started, ask=ask, exact=exact)
    else:
        outcome = _local_decide(live, started, ask=ask, exact=exact)
    _settle_lessons(outcome, live)
    if capture is not None:
        if live.lessons:
            # The lesson revisions offered are part of what the input was built from (shown or not: see input).
            from dataclasses import replace
            # As sources too: forgetting what a lesson rests on removes the raw copies that showed it.
            capture = replace(capture, input_refs=tuple(capture.input_refs) + tuple(
                {"ref": f"lesson:{v.id}", "revision": v.revision, "shown": outcome.lessons_received}
                for v in live.lessons), sources=tuple(dict.fromkeys(
                    (*capture.sources, *(f"lesson:{v.id}" for v in live.lessons)))))
        outcome.decision_id = str(capture.attempt_id)
        outcome.capture = capture
        try:
            _record_attempt(outcome, live, capture, exact, started_at)
        except Exception:  # noqa: BLE001 - capture never changes the decision
            logger.exception("student_two: capturing the attempt failed")
        _record_lessons(outcome, live, capture.user_id, upstream=capture.upstream)
    return outcome


def _current_lessons(live: S.Live) -> S.Live:
    """The lessons gathered for this decision, made current before its input is built (post-audit 4): one revised,
    withdrawn or unsettled since it was gathered goes in as it is now, or not at all, said."""
    from dataclasses import replace

    from app.services import memory_lessons

    if not live.lessons:
        return live
    supply, why = memory_lessons.current(memory_lessons.Supply(tuple(live.lessons), tuple(live.lessons_left_out)))
    if supply.views == tuple(live.lessons) and supply.left_out == tuple(live.lessons_left_out) and why is None:
        return live
    return replace(live, lessons=supply.views, lessons_left_out=supply.left_out,
                   lessons_note=why or live.lessons_note or (None if supply.views else
                                                             "its lessons changed since they were gathered"))


def _settle_lessons(outcome: Outcome, live: S.Live) -> None:
    """Whether the lessons offered reached the model's input, and if not, why (said, never assumed)."""
    if not live.lessons:
        outcome.lessons_reason = live.lessons_note
        return
    situation = outcome.situation
    if situation is not None and situation.lessons_shown:
        outcome.lessons_received = True
        return
    if situation is None or not situation.text:
        outcome.lessons_reason = "the decision failed before its input was built"
    else:
        outcome.lessons_reason = "this decision model's input has no lessons (its rendering doesn't show them)"


STUDENT_1_GAP = ("Student 1's trained input has no place for lessons (a delivery gap, post-audit 3): the message was "
                 "interpreted without them; Student 2 read them with its reading")


def _record_lessons(outcome: Outcome, live: S.Live, user_id: uuid.UUID, *, upstream: dict | None = None) -> None:
    from app.services import memory_lessons

    supply = memory_lessons.Supply(tuple(live.lessons), tuple(live.lessons_left_out))
    memory_lessons.record_uses(user_id, outcome.decision_id, supply, consumer="student_2",
                               received=outcome.lessons_received, reason=outcome.lessons_reason,
                               weighed=outcome.lessons_weighed, decision_status=outcome.status)
    # Interpretation (Student 1) had the same lessons available and couldn't receive them: recorded as such, so a
    # missing delivery is never counted as interpretation having them.
    reading = (upstream or {}).get("student_1")
    if reading and (upstream or {}).get("student_1_status") != "not_run":
        memory_lessons.record_uses(user_id, reading, supply, consumer="student_1", received=False,
                                   reason=STUDENT_1_GAP)


def _local_decide(live: S.Live, started: float, *, ask=None, exact: dict[str, Any]) -> Outcome:
    exact["provider"] = "student"
    try:
        model = manifest()
    except (OSError, ValueError) as exc:
        return Outcome("unavailable", error=f"no model: {type(exc).__name__}")
    situation = S.snapshot(live)
    text = S.render(situation, model.get("rendering"))
    exact.update(system=model.get("system") or "", prompt=text, snapshot=situation.snapshot, refs=situation.refs,
                 lessons=_lesson_input(situation),
                 rendering=model.get("rendering"), settings={
                     "temperature": 0.0, "max_tokens": model.get("max_tokens", 400),
                     "chat_template_kwargs": model.get("chat_template_kwargs", {}),
                     "url": get_settings().cognition_student_url},
                 versions={"snapshot_version": S.E.SNAPSHOT_VERSION, "decision_version": S.E.DECISION_VERSION,
                           "rendering": model.get("rendering"), "manifest": _manifest_digest(model)})
    try:
        output = (ask or _ask)(model, text)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
        return Outcome("unavailable", situation, model=model.get("name"), error=f"{type(exc).__name__}: {exc}"[:200],
                       latency_ms=round((time.monotonic() - started) * 1000))
    exact["raw_output"] = output
    decision, unreadable = S.parse(output)
    problems = [unreadable] if unreadable else S.check(decision, situation)
    return Outcome("invalid" if problems else "ok", situation, None if unreadable else decision, problems,
                   model.get("name"), round((time.monotonic() - started) * 1000))


def _manifest_digest(model: dict[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _record_attempt(outcome: Outcome, live: S.Live, capture, exact: dict[str, Any], started_at: datetime) -> None:
    """Student 2's attempt as it happened: what was missing stays absent (no prompt when the snapshot itself
    failed, no output when the model never answered)."""
    from app.services import learning_capture

    blobs = {}
    if exact.get("system"):
        blobs["system"] = exact["system"]
    if exact.get("schema") is not None:
        blobs["schema"] = json.dumps(exact["schema"], sort_keys=True)
    absent = list(learning_capture.NOT_CAPTURED)
    if "prompt" not in exact:
        absent.append("input")
    if "raw_output" not in exact:
        absent.append("output")
    learning_capture.attempt(
        capture, learner="student_2", provider=exact.get("provider"), model=outcome.model,
        input={"prompt": exact.get("prompt"), "snapshot": exact.get("snapshot"), "refs": exact.get("refs"),
               "rendering": exact.get("rendering"), "system": None, "schema": None,
               "lessons": exact.get("lessons")},
        output=None if "raw_output" not in exact else {
            "raw": exact["raw_output"], "decision": outcome.decision, "problems": outcome.problems,
            "lessons_weighed": outcome.lessons_weighed, "lesson_problems": outcome.lesson_problems},
        status=outcome.status, error=outcome.error, started_at=started_at,
        produced_at=datetime.now(timezone.utc) if "raw_output" in exact else None,
        versions=exact.get("versions") or {}, settings=exact.get("settings") or {}, blobs=blobs, absent=absent)


@lru_cache(maxsize=1)
def _teacher_client(api_key: str, timeout: float):
    from openai import OpenAI

    return OpenAI(api_key=api_key, timeout=timeout, max_retries=0)


def _ask_teacher(model: dict[str, Any], text: str) -> str:
    s = get_settings()
    result = _teacher_client(s.openai_api_key, s.cognition_teacher_timeout_seconds).chat.completions.create(
        model=model["name"], store=False, reasoning_effort="low", max_completion_tokens=4000,
        messages=[{"role": "system", "content": model["system"]}, {"role": "user", "content": text}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "event_decide", "strict": True, "schema": model["schema"]}})
    return result.choices[0].message.content or ""


def _lesson_input(situation: S.Situation) -> dict[str, Any] | None:
    """What the capture keeps of the lessons offered: each one (id, revision, as shown), whether the input showed
    them, how many the budget left out."""
    if not situation.lessons:
        return None
    return {"interface": LS.INTERFACE_VERSION, "shown": situation.lessons_shown, "cut": situation.lessons_cut,
            "lessons": [v.to_dict() for v in situation.lessons]}


def teacher_model(situation: S.Situation) -> tuple[str, dict[str, Any]]:
    """The teacher's rendering and model call for a situation: ``memory-v1`` exactly when there are no lessons;
    with lessons, ``lessons-v1``: the LESSONS section, the guide on weighing them, and the ``lessons_weighed``
    field for its own account (outside the decision contract: ``parse`` never reads it)."""
    from app.ai.Cognitive.simple import events as E
    from app.services.cognitive_live import character_page

    s = get_settings()
    rendering = LS.RENDERING if situation.lessons else "memory-v1"
    text = S.render(situation, rendering)
    system = f'{E.prompt("decide_events_v1")}\n\n{E.SCHEMA_GUIDE}\n\nCHARACTER PAGE\n{character_page()}'
    schema = E.decision_schema([ref for ref, _ in E.candidates(situation.snapshot)])
    if situation.lessons_shown:
        system += f"\n\n{LS.GUIDE}"
        schema = dict(schema, properties=dict(schema["properties"],
                                              lessons_weighed=LS.weighed_schema(situation.lesson_refs)),
                      required=[*schema["required"], "lessons_weighed"])
    return text, {"name": s.cognition_teacher_model, "system": system, "schema": schema, "rendering": rendering}


def _teacher_decide(live: S.Live, started: float, *, ask=None, exact: dict[str, Any] | None = None) -> Outcome:
    """Only EventTeacher's decision call: no sample reply, critic, or additional training samples."""
    from app.ai.Cognitive.simple import events as E

    s = get_settings()
    outcome = Outcome("unavailable", model=s.cognition_teacher_model)
    capture = {"format_version": 1, "at": datetime.now(timezone.utc).isoformat(),
               "snapshot_version": E.SNAPSHOT_VERSION, "decision_version": E.DECISION_VERSION,
               "rendering": "memory-v1", "reasoning_effort": "low", "max_completion_tokens": 4000}
    outcome.teacher_capture = capture
    try:
        situation = S.snapshot(live)
        outcome.situation = situation
        text, model = teacher_model(situation)
        rendering = model["rendering"]
        versions = {"snapshot_version": E.SNAPSHOT_VERSION, "decision_version": E.DECISION_VERSION,
                    "rendering": rendering, "prompt": "decide_events_v1"}
        if situation.lessons_shown:
            versions["lessons"] = LS.INTERFACE_VERSION
        capture.update(snapshot=situation.snapshot, refs=situation.refs, prompt=text, rendering=rendering,
                       system=model["system"], schema=model["schema"], lessons=_lesson_input(situation))
        if exact is not None:
            exact.update(provider="teacher", system=model["system"], schema=model["schema"], prompt=text,
                         snapshot=situation.snapshot, refs=situation.refs, rendering=rendering,
                         lessons=_lesson_input(situation),
                         settings={"reasoning_effort": "low", "max_completion_tokens": 4000, "store": False,
                                   "response_format": "json_schema strict"},
                         versions=versions)
        output = (ask or _ask_teacher)(model, text)
        capture["raw_output"] = output
        if exact is not None:
            exact["raw_output"] = output
        decision, unreadable = S.parse(output)
        outcome.decision = None if unreadable else decision
        outcome.problems = [unreadable] if unreadable else S.check(decision, situation)
        outcome.status = "invalid" if outcome.problems else "ok"
        if situation.lessons_shown and not unreadable:
            outcome.lessons_weighed, outcome.lesson_problems = LS.parse_weighed(output, situation.lesson_refs)
            capture["lessons_weighed"] = outcome.lessons_weighed
    except Exception as exc:  # a provider failure is degraded cognition, never a Qwen fallback
        logger.exception("student_two: teacher decision failed")
        outcome.error = f"{type(exc).__name__}: {exc}"[:200]
    outcome.latency_ms = round((time.monotonic() - started) * 1000)
    return outcome


def _append_capture(path: str, line: str) -> None:
    """One writer per process, with a file lock for API/worker processes sharing the capture file."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        logger.exception("student_two: teacher training capture failed (%s)", path)


# --------------------------------------------------------------------------- #
# Carrying out the work part
# --------------------------------------------------------------------------- #


def apply(db: Session, outcome: Outcome, *, user_id: uuid.UUID, message_id: uuid.UUID | None,
          routed: bool = True, conversation_id: uuid.UUID | None = None) -> str:
    """The work part's effect, where an owner can carry it out now. Returns what happened, in a few words.
    ``routed``: a user turn routes background recall and thinking itself (the coordinator); on a follow-up,
    thinking wakes her mind in ``conversation_id``, and a recall alone has no owner yet."""
    from app.services import desktop_game, memory_manager, memory_reconcile

    d, refs = outcome.decision, outcome.situation.refs
    work, target = d["work"], d["target"]
    key = refs.get(target)
    source = memory_manager.Source("message", message_id) if message_id else None
    try:
        if work == "no_change":
            effect = "nothing changed"
        elif work in S.RECALL_WORK | S.E.THINKING and routed:
            effect = "background work, routed by the turn"
        elif target.startswith("P") and work in ("pursue", "decline_proposal", "defer_proposal"):
            from app.services import learning_trials

            if work == "pursue":
                effect = learning_trials.on_pursue(db, user_id, key, decision=d, decision_id=outcome.decision_id,
                                                   goal_handling=d.get("other_goal_handling"))
            elif work == "decline_proposal":
                effect = learning_trials.on_decline(db, user_id, key, decision=d, decision_id=outcome.decision_id)
            else:
                effect = learning_trials.on_defer(db, user_id, key, decision=d, decision_id=outcome.decision_id,
                                                  situation=outcome.situation)
        elif target.startswith("W") and key and key.startswith("trial:") and work in ("pause", "resume", "cancel"):
            from app.services import learning_trials

            effect = learning_trials.on_work(db, user_id, key, work, reason=d["intention"].strip()[:200] or work)
        elif work in S.E.THINKING:
            from app.services import mind_conversation
            effect = mind_conversation.wake(user_id=user_id, conversation_id=conversation_id,
                                            think_about=d["think_about"], decision_id=outcome.decision_id)
        elif work == "pursue":
            effect = "taken up by her reply (its tools)"
        elif target == "A1" and work == "pause":
            effect = desktop_game.pause(db, user_id, user_asked=False)
        elif target == "A1" and work == "resume":
            effect = desktop_game.resume(db, user_id)
        elif target == "A1" and work == "cancel":
            effect = desktop_game.end(db, user_id)
        elif (target.startswith("G") and work in ("pause", "resume", "resolve_goal") and message_id is None
              and key and uuid.UUID(key) in memory_reconcile.held_item_ids(db, user_id)):
            # Without a user message, a goal being rechecked after a fact changed isn't hers to change yet.
            effect = "goal on hold: being rechecked"
        elif target.startswith("G") and work in ("pause", "resume", "resolve_goal"):
            status = {"pause": "suspended", "resume": "active"}.get(work) or (
                "completed" if d["resolution"] == "achieved" else "abandoned")
            memory_manager.resolve(db, user_id, key, status=status, source=source,
                                   reason=d["intention"].strip()[:200] or work)
            db.commit()
            effect = f"goal {status}"
        elif target.startswith("W") and work == "cancel" and key and key.startswith("coordinator:"):
            row = db.get(CoordinatorWork, uuid.UUID(key.split(":", 1)[1]))
            if row is not None and row.user_id == user_id and row.status in _WORK_STATE:
                row.status, row.error_code = "cancelled", "student_two"
                row.cancellation_generation += 1
                db.commit()
            effect = "background work cancelled"
        elif target.startswith("G") and work == "request_replanning" and key:
            effect = desktop_game.rethink(db, user_id, why=d["intention"].strip()[:200] or "replan")
        else:
            effect = f"not carried out: no owner for {work} on {target} yet"
    except Exception as exc:  # noqa: BLE001 - a failed effect is said, never claimed
        db.rollback()
        logger.exception("student_two: carrying out %s %s failed", work, target)
        effect = f"failed: {type(exc).__name__}"
    outcome.effect = effect[:200]
    try:
        _record_effect(outcome, user_id=user_id, work=work, target=target, routed=routed)
    except Exception:  # noqa: BLE001 - capture never changes what was done
        logger.exception("student_two: capturing the effect failed")
    return outcome.effect


def _effect_result(effect: str, work: str, routed: bool) -> str:
    if effect.startswith("failed"):
        return "failed"
    if effect.startswith("not carried out"):
        return "not_executed"
    if effect == "goal on hold: being rechecked":
        return "not_executed"
    if effect.startswith("experiment selected"):
        return "succeeded"
    if (work in S.RECALL_WORK | S.E.THINKING and routed) or work == "pursue":
        return "handed_off"
    return "succeeded"


def _record_effect(outcome: Outcome, *, user_id: uuid.UUID, work: str, target: str, routed: bool) -> None:
    """The work part as its owner carried it out: intended (Student 2's work and target) next to what happened."""
    ctx = outcome.capture
    if ctx is None:
        return
    from app.services import learning_capture

    d = outcome.decision or {}
    learning_capture.execution(
        user_id=user_id, kind="effect", executor="student_two.apply", attempt_id=outcome.decision_id,
        result=_effect_result(outcome.effect or "", work, routed), turn_id=ctx.turn_id,
        conversation_id=ctx.conversation_id, game_session_id=ctx.game_session_id,
        intended={"work": work, "target": target, "target_key": (outcome.situation.refs.get(target)
                                                                 if outcome.situation else None),
                  "resolution": d.get("resolution"), "think_about": d.get("think_about")},
        executed={"effect": outcome.effect}, deviation=None if _effect_result(outcome.effect or "", work, routed)
        in ("succeeded", "handed_off") else outcome.effect, ended_at=datetime.now(timezone.utc))


def log(outcome: Outcome, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None, trigger: str) -> None:
    """Summary trace plus, for the teacher, a full training example on the background writer."""
    if outcome.teacher_capture is not None:
        try:
            capture = outcome.teacher_capture | {
                "decision_id": outcome.decision_id, "user_id": str(user_id),
                "conversation_id": str(conversation_id) if conversation_id else None, "trigger": trigger,
                "model": outcome.model, "status": outcome.status, "latency_ms": outcome.latency_ms,
                "error": outcome.error, "problems": outcome.problems, "decision": outcome.decision,
                "effect": outcome.effect, "training_eligible": outcome.ok, "review_status": "unreviewed"}
            # Freeze the record before handing it to another thread; later state changes cannot alter it.
            line = json.dumps(capture, ensure_ascii=False) + "\n"
            _capture_writer.submit(_append_capture, _path(get_settings().cognition_teacher_data_path), line)
        except Exception:
            logger.exception("student_two: queuing teacher training capture failed")
    try:
        d = outcome.decision or {}
        record = {"at": datetime.now(timezone.utc).isoformat(), "decision": outcome.decision_id,
                  "user_id": str(user_id), "conversation_id": str(conversation_id) if conversation_id else None,
                  "trigger": trigger, "status": outcome.status, "model": outcome.model,
                  "latency_ms": outcome.latency_ms, "error": outcome.error, "problems": outcome.problems,
                  "domain": outcome.situation.snapshot["domain"] if outcome.situation else None,
                  "refs": sorted(outcome.situation.refs) if outcome.situation else [],
                  "communication": d.get("communication"), "move": d.get("move"), "work": d.get("work"),
                  "target": d.get("target"), "effect": outcome.effect,
                  "lessons": {"offered": len(outcome.situation.lessons) if outcome.situation else 0,
                              "received": outcome.lessons_received,
                              "weighed": [w["use"] for w in outcome.lessons_weighed]}}
        path = _path(get_settings().cognition_student_log_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("student_two: writing the trace failed")
