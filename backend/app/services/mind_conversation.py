"""A conversation as a source of her mind, live (docs/COLUMBINA_INNER_STATE_PLAN.md; CLAUDE.md "A general mind").

Chat only changes when someone speaks, so nothing polls it every second and there are no quick thoughts here: only
hard thoughts on real problems, in the same per-user attention as her games (one mind across everything).

What wakes it:

- after her reply (``observe``, from ``cognitive_live.after_reply``): a question of theirs still open
  (``their_open_question`` in the simple-cognition notes) is a problem worth revisiting;
- cognition (``wake``): Student 2 decided, on a finished thought, to think something over;
- the mind worker's slow ``sweep`` (every ``SWEEP_SECONDS``): a promise she made the user and hasn't kept (an open
  commitment of hers), an intention of hers about to lapse, something the user said that disagrees with what she
  remembers (an open ``memory_conflicts`` row, once: services/memory_conflicts.py), and quiet time in the user's
  latest conversation.

What she reads (``gather``): that conversation's last lines, the goals and items of her working memory, the memories
her last answer used, memories found for the problem, what's open in the conversation (notes, background work) and
how long it's been quiet. Mood is not read: the inner state has no influence yet.

Where conclusions go (``mind_hard.route``): saved (``mind_thoughts`` with source ``conversation`` and the
conversation's id), shown in the thoughts panel, an intention kept in working memory, and anything to say proposed
through ``autonomous_speech``, where Student 2 decides whether and how it is said in this conversation. There is no
body here: no tips.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.mind.contracts import Moment, Rhythm, Scope
from app.ai.mind.hard import HardInput, Problem
from app.ai.mind.situation import Situation
from app.db.models import (
    ChatMessage, Conversation, CoordinatorWork, MemoryConflict, MemoryEvidence, User, WorkingItem,
)

log = logging.getLogger(__name__)
NAME = "conversation"
CONVERSATION = Situation(NAME, "talking with {user} in chat")

SWEEP_SECONDS = 30.0
WATCH_HOURS = 24  # a conversation with a message this recent is one she still thinks about
QUIET_SECONDS = 10 * 60  # the user quiet this long in their latest conversation: time to think
PROMISE_AFTER_SECONDS = 2 * 60  # a promise this old and still open (not one she is keeping in this reply)
INTENTION_LAPSE_SECONDS = 15 * 60  # an intention lapsing within this long
CONFLICT_AFTER_SECONDS = 20  # a disagreement with her memory waits this long (her reply goes first)
QUIET_TOPICS = ("how {user} is doing, from what they've told you lately",
                "something you two left unfinished",
                "something you'd like to know about {user}")


# --------------------------------------------------------------------------- #
# Problems
# --------------------------------------------------------------------------- #

def open_problem(conversation_id: uuid.UUID, notes: list[dict]) -> Problem | None:
    """Their newest question still open after her reply, as a problem (None if there's none)."""
    open_ = [n for n in notes if n.get("kind") == "their_open_question" and n.get("text")]
    if not open_:
        return None
    note = open_[-1]
    text = " ".join(str(note["text"]).split())[:300]
    return Problem("open", f'They asked: "{text}". Your reply left it open.',
                   topic=f"still open: {text}"[:200], query=text,
                   anchor={"note": note.get("id"), "event": f"{conversation_id}:{note.get('id')}"})


def _item_problem(entry: WorkingItem) -> Problem:
    text = " ".join(entry.text.split())[:300]
    if entry.kind == "commitment":
        return Problem("promise", f'You told them you would: "{text}". It is still open.',
                       topic=f"promise: {text}"[:200], query=text,
                       anchor={"item": str(entry.id), "revision": entry.revision,
                               "event": f"item:{entry.id}:{entry.revision}"})
    due = entry.due_at.astimezone(timezone.utc).strftime("%H:%M UTC") if entry.due_at else "soon"
    return Problem("intention", f'You meant to: "{text}". It lapses at {due}.',
                   topic=f"intention: {text}"[:200], query=text,
                   anchor={"item": str(entry.id), "revision": entry.revision,
                           "event": f"item:{entry.id}:{entry.revision}"})


def _item_conversation(db: Session, entry: WorkingItem) -> uuid.UUID | None:
    """The conversation an item was said in (its first message evidence), if any: where she'd bring it up."""
    return db.scalar(select(ChatMessage.conversation_id).join(MemoryEvidence, MemoryEvidence.message_id == ChatMessage.id)
                     .where(MemoryEvidence.item_id == entry.id, MemoryEvidence.user_id == entry.user_id)
                     .order_by(MemoryEvidence.id).limit(1))


def item_problems(db: Session, now: datetime) -> list[tuple[Scope, Problem]]:
    """Her open promises to the user, and her intentions about to lapse, each in the conversation it came from."""
    rows = db.scalars(select(WorkingItem).where(
        WorkingItem.owner == "columbina", WorkingItem.closed_at.is_(None),
        ((WorkingItem.kind == "commitment") & (WorkingItem.owed_to == "user") & (WorkingItem.status == "open")
         & (WorkingItem.created_at <= now - timedelta(seconds=PROMISE_AFTER_SECONDS)))
        | ((WorkingItem.kind == "intention") & (WorkingItem.status == "active")
           & (WorkingItem.due_at <= now + timedelta(seconds=INTENTION_LAPSE_SECONDS)) & (WorkingItem.due_at > now)))
        .order_by(WorkingItem.created_at).limit(50)).all()
    from app.services import memory_reconcile

    held = memory_reconcile.held_item_ids(db)  # being rechecked after a fact changed: not hers to act on yet
    out = []
    for entry in rows:
        if entry.id in held:
            continue
        conversation_id = _item_conversation(db, entry)
        if conversation_id is not None:
            out.append((Scope(entry.user_id, NAME, conversation_id), _item_problem(entry)))
    return out


def _conflict_problem(row: MemoryConflict) -> Problem:
    since = row.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d") if row.created_at else "earlier"
    known = " ".join(row.known_content.split())[:300]
    said = " ".join(row.user_words.split())[:300]
    return Problem(
        "conflict",
        f'You remember: "{known}". On {since} they said: "{said}", which doesn\'t match it. Nothing has settled '
        "which holds: they may have misspoken or been misheard, it may have changed without them saying so, or "
        "both may be true (a visit, a trip). Your memory still says what it said.",
        topic=f"doesn't match: {row.stated}"[:200], query=f"{said} {known}"[:300],
        anchor={"conflict": str(row.id), "event": f"conflict:{row.id}"})


def conflict_problems(db: Session, now: datetime) -> list[tuple[Scope, Problem]]:
    """Things the user said that disagree with what she remembers, each in the conversation it was said in, once
    (``offered_at``), after her reply had its moment (CONFLICT_AFTER_SECONDS)."""
    from app.services import memory_conflicts

    rows = memory_conflicts.due_for_mind(db, before=now - timedelta(seconds=CONFLICT_AFTER_SECONDS),
                                         since=now - timedelta(hours=WATCH_HOURS))
    return [(Scope(row.user_id, NAME, row.conversation_id), _conflict_problem(row)) for row in rows]


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def quiet_problems(db: Session, now: datetime, turn: dict[uuid.UUID, int]) -> list[tuple[Scope, Problem]]:
    """Quiet time in each user's latest conversation: the user quiet QUIET_SECONDS, their last message within
    WATCH_HOURS. One per silence (the anchor is the conversation's last message), topics in turn."""
    latest = (select(Conversation.user_id, func.max(Conversation.updated_at).label("at"))
              .where(Conversation.updated_at >= now - timedelta(hours=WATCH_HOURS))
              .group_by(Conversation.user_id).subquery())
    rows = db.execute(select(Conversation).join(latest, (Conversation.user_id == latest.c.user_id)
                                                & (Conversation.updated_at == latest.c.at))).scalars().all()
    out = []
    for conversation in rows:
        last_user = db.scalar(select(func.max(ChatMessage.created_at)).where(
            ChatMessage.conversation_id == conversation.id, ChatMessage.role == "user"))
        if last_user is None or now - _aware(last_user) < timedelta(seconds=QUIET_SECONDS):
            continue
        if now - _aware(last_user) > timedelta(hours=WATCH_HOURS):
            continue
        user = db.get(User, conversation.user_id)
        name = user.name if user and user.name else "the user"
        n = turn.get(conversation.user_id, 0)
        turn[conversation.user_id] = n + 1
        topic = QUIET_TOPICS[n % len(QUIET_TOPICS)].format(user=name)
        out.append((Scope(conversation.user_id, NAME, conversation.id),
                    Problem("quiet", f"It's been quiet for a while. Think about {topic}.",
                            topic=f"quiet: {topic}"[:200], query=topic[:300],
                            anchor={"event": f"quiet:{conversation.id}:{conversation.last_message_sequence}"})))
    return out


def _note_open(user_id: uuid.UUID, conversation_id: uuid.UUID, note_id: str | None) -> bool:
    from app.services import cognitive_live

    notes = cognitive_live.load_state(user_id, conversation_id).get("notes") or []
    return any(n.get("id") == note_id for n in notes)


# --------------------------------------------------------------------------- #
# What she reads
# --------------------------------------------------------------------------- #

def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{int(seconds)} s" if minutes < 2 else f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"


def right_now(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime) -> str:
    """How long it's been quiet, what's still open in the conversation, and background work on it."""
    from app.services import cognitive_live

    lines = []
    last = {role: db.scalar(select(func.max(ChatMessage.created_at)).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == role)) for role in ("user", "assistant")}
    if last["user"] is not None:
        lines.append(f"Their last message: {_ago((now - _aware(last['user'])).total_seconds())} ago.")
    if last["assistant"] is not None:
        lines.append(f"Your last message: {_ago((now - _aware(last['assistant'])).total_seconds())} ago.")
    notes = cognitive_live.load_state(user_id, conversation_id).get("notes") or []
    for note in notes[-6:]:
        if note.get("text"):
            lines.append(f"Still open ({str(note.get('kind', 'note')).replace('_', ' ')}): {note['text']}")
    work = db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.conversation_id == conversation_id,
        CoordinatorWork.kind.in_(("recall", "mind")),
        CoordinatorWork.status.in_(("queued", "waiting_for_memory", "running")))).all()
    for w in work:
        about = (w.input.get("think_about") or w.input.get("message") or "").strip()[:120]
        lines.append(f"Being worked on in the background ({w.kind}, {w.status.replace('_', ' ')}): {about}")
    return "\n".join(lines)


def _focused_goal(db: Session, user_id: uuid.UUID) -> str | None:
    from app.services import memory_manager

    context = memory_manager.focused(db, user_id)
    if context is None:
        return None
    goal = db.scalar(select(WorkingItem.text).where(
        WorkingItem.user_id == user_id, WorkingItem.context_id == context.id, WorkingItem.kind == "goal",
        WorkingItem.status == "active").order_by(WorkingItem.updated_at.desc()).limit(1))
    return goal


def memories(db: Session, base: str | None, *, user_id: uuid.UUID, conversation_id: uuid.UUID,
             user_name: str, refs: list[str] | None = None) -> str | None:
    """The memories found for the problem, plus her working memory and what her last answer in this conversation
    used (re-read now). ``refs`` gets the re-read memories' references."""
    from app.services import memory_manager, recall_references

    parts = [base] if base else []
    working = memory_manager.view_for(user_id, user_name=user_name, autonomous=True)
    if working:
        parts.append(working)
    cached = recall_references.cached_for_reply(user_id, conversation_id, None, None)
    if cached.rendered and cached.result.records:
        parts.append(cached.rendered)
        if refs is not None:
            refs.extend(str(r) for r in cached.result.refs())
    return "\n\n".join(parts) or None


# --------------------------------------------------------------------------- #
# The source
# --------------------------------------------------------------------------- #

class ConversationSource:
    name = NAME
    rhythm = Rhythm("event")
    shadow = False

    def __init__(self) -> None:
        self._last_sweep = 0.0
        self._quiet_turn: dict[uuid.UUID, int] = {}

    def enabled(self) -> bool:
        return True

    def gate_situation(self) -> Situation:
        return CONVERSATION

    def situation(self) -> Situation:
        return CONVERSATION

    def new_state(self) -> None:
        return None

    def worse(self, now: Any, before: Any) -> bool:
        return False

    def problems(self, scope: Scope, state: Any, moment: Moment, *, gate_hard: bool) -> list[Problem]:
        return []

    def drop_waiting(self, scope: Scope, moment: Moment, problem: Problem) -> str | None:
        return None

    def quiet_topics(self, user: str) -> tuple[str, ...]:
        return ()  # quiet time here comes from the sweep, not a tick

    def sweep(self, db: Session, at: float) -> list[str]:
        """Every SWEEP_SECONDS: offer promises, lapsing intentions and quiet time. Returns what happened."""
        from app.services import mind_hard

        if at - self._last_sweep < SWEEP_SECONDS:
            return []
        self._last_sweep = at
        now = datetime.fromtimestamp(at, timezone.utc)
        conflicts = conflict_problems(db, now)
        found = item_problems(db, now) + conflicts + quiet_problems(db, now, self._quiet_turn)
        db.rollback()  # read only
        out = [f"{problem.trigger}: {mind_hard.offer(self, scope, problem, at)}" for scope, problem in found]
        if conflicts:
            # Offered once: one that loses its place in attention stays unresolved in memory, where recall shows it.
            from app.services import memory_conflicts

            for _scope, problem in conflicts:
                memory_conflicts.mark_offered(db, uuid.UUID(problem.anchor["conflict"]))
            db.commit()
        return out

    def gather(self, db: Session, scope: Scope, problem: Problem, base: HardInput) -> tuple[HardInput | None, Any]:
        from app.services import mind_hard

        conversation = db.get(Conversation, scope.activity)
        if conversation is None or conversation.user_id != scope.user_id:
            return None, None
        now = datetime.now(timezone.utc)
        refs = list(base.memory_refs)
        found = memories(db, base.memories, user_id=scope.user_id, conversation_id=scope.activity,
                         user_name=base.user, refs=refs)
        return replace(
            base, conversation=mind_hard.conversation(db, scope.user_id, now, scope.activity),
            now=right_now(db, scope.user_id, scope.activity, now), goal=_focused_goal(db, scope.user_id),
            memories=found, memory_refs=tuple(dict.fromkeys(refs)),
        ), conversation.last_message_sequence

    def dropped(self, db: Session, scope: Scope, problem: Problem, cancelled: str | None) -> str | None:
        if cancelled:
            return cancelled
        if problem.anchor.get("note") and not _note_open(scope.user_id, scope.activity, problem.anchor["note"]):
            return "it's been answered"
        if problem.anchor.get("conflict"):
            row = db.get(MemoryConflict, uuid.UUID(problem.anchor["conflict"]))
            if row is None or row.status != "open":
                return "it's been settled"
        if problem.anchor.get("item"):
            entry = db.get(WorkingItem, uuid.UUID(problem.anchor["item"]))
            if entry is None or entry.closed_at is not None:
                return "it's no longer open"
            if entry.revision != problem.anchor.get("revision"):
                return "it changed meanwhile"
        return None

    def outdated(self, db: Session, scope: Scope, problem: Problem, token: Any) -> list[str]:
        """They wrote again while she was thinking: the conversation moved past it."""
        if token is None:
            return []
        newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == scope.activity, ChatMessage.role == "user",
            ChatMessage.conversation_sequence > token)) or 0
        return [f"they wrote {newer} new message(s) since"] if newer else []

    def live(self, db: Session, scope: Scope) -> bool:
        conversation = db.get(Conversation, scope.activity)
        return conversation is not None and conversation.user_id == scope.user_id

    def changed_since(self, db: Session, token: Any) -> list[str]:
        return []

    def current_goal(self, db: Session, scope: Scope) -> str | None:
        return _focused_goal(db, scope.user_id)

    def speaking(self, db: Session, scope: Scope) -> dict:
        # Student 2 decides whether it is said (autonomous_speech: no game pacing rules for a conversation).
        return {"commentary_mode": "occasional"}

    def body(self, scope: Scope) -> None:
        return None

    def thought_session(self, scope: Scope) -> None:
        return None

    def handoff_scope(self, db: Session, user_id: uuid.UUID) -> None:
        return None


SOURCE = ConversationSource()


def observe(*, user_id: uuid.UUID, conversation_id: uuid.UUID, notes: list[dict]) -> str | None:
    """After a reply: offer the open question, if any, to her slow thinking. Never raises; returns what happened."""
    try:
        from app.services import mind_hard

        problem = open_problem(conversation_id, notes)
        if problem is None:
            return None
        return mind_hard.offer(SOURCE, Scope(user_id, NAME, conversation_id), problem, time.time())
    except Exception:  # noqa: BLE001 - the mind must never touch the turn
        log.exception("mind_conversation.observe failed")
        return None


def wake(*, user_id: uuid.UUID, conversation_id: uuid.UUID | None, think_about: str, decision_id: str) -> str:
    """Cognition asked the mind to think something over (Student 2's thinking decision on a follow-up)."""
    from app.services import mind_hard

    about = " ".join((think_about or "").split())[:300]
    if conversation_id is None or not about:
        return "not carried out: nothing to think about, or no conversation"
    problem = Problem("handoff", f"You decided to think this over: {about}", topic=f"thinking over: {about}"[:200],
                      query=about, anchor={"event": f"decision:{decision_id}"})
    outcome = mind_hard.offer(SOURCE, Scope(user_id, NAME, conversation_id), problem, time.time())
    return f"handed to her mind ({outcome or 'waiting'})"
