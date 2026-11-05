"""Her working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md): what we're doing, in which context,
and what's still unfinished.

General, not about any one activity: a game world, a project and the user's general life are all
contexts; a goal, a commitment owed by one party to the other, her own intention and an open question
are all working items. A goal outlives its steps: a game plan only points at the goal it serves
(``desktop_sessions.goal_item_id``), so replacing the plan never replaces the goal, and time never
closes one. A context change moves attention; closing a context suspends its goals, it never deletes
them.

Code owns every transition, the evidence links and the checks; the model only decides which tool to
call (``app/ai/tools/memory``). Nothing here calls a model. Every function works inside the caller's
transaction; the caller commits.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import String, cast, event, func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES,
    WORKING_ITEM_CLOSED,
    WORKING_ITEM_SERVES,
    WORKING_ITEM_STATUSES,
    ChatMessage,
    Conversation,
    DesktopSession,
    DesktopSessionEvent,
    GameExperience,
    MemoryContext,
    MemoryEvent,
    MemoryEntity,
    MemoryEntityLink,
    MemoryEvidence,
    MemoryManagerState,
    MindThought,
    Observation,
    WorkingItem,
)

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Who set a game plan -> who the goal belongs to, and how we know it.
USER_PLAN_SOURCES = ("columbina_user_turn", "user_api")
# A tick never sleeps longer than this, so a new due time is noticed soon enough.
MAX_CHECK_GAP = timedelta(minutes=10)
# What the memory view shows of the focused context, and of the rest.
VIEW_GOALS, VIEW_CHILDREN, VIEW_ITEMS, VIEW_EVENTS, VIEW_OTHER, VIEW_PAST = 5, 5, 5, 5, 3, 2
VIEW_CHARS = 1200
# Short labels for game contexts, by the session's profile.
GAME_LABELS = {"minecraft_pack": "Minecraft", "minecraft_java": "Minecraft"}

_log_lock = threading.Lock()


class MemoryError(Exception):
    """A refused memory change, with a plain message the model can relay."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Source:
    """Where something came from: one row of an existing table."""

    kind: str  # message | session_event | thought | experience | observation
    id: uuid.UUID | int


_SOURCE_COLUMN = {"message": "message_id", "session_event": "session_event_id", "thought": "thought_id",
                  "experience": "experience_id", "observation": "observation_id"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ref(item: WorkingItem) -> str:
    """The short id the model sees and passes back (first 8 hex digits)."""
    return item.id.hex[:8]


# --------------------------------------------------------------------------- #
# State and contexts
# --------------------------------------------------------------------------- #


def ensure_general(db: Session, user_id: uuid.UUID) -> MemoryContext:
    """The user's general context (ordinary conversation, nothing named), created on first use."""
    db.execute(insert(MemoryContext).values(id=uuid.uuid4(), user_id=user_id, kind="general", label="General")
               .on_conflict_do_nothing(index_elements=["user_id"], index_where=sql_text("kind = 'general'")))
    return db.scalar(select(MemoryContext).where(MemoryContext.user_id == user_id, MemoryContext.kind == "general"))


def state(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> MemoryManagerState:
    general = ensure_general(db, user_id)
    db.execute(insert(MemoryManagerState).values(user_id=user_id, focus_context_id=general.id)
               .on_conflict_do_nothing(index_elements=["user_id"]))
    query = select(MemoryManagerState).where(MemoryManagerState.user_id == user_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row.focus_context_id is None:  # the focused context was removed by a reset
        row.focus_context_id = general.id
    return row


def focused(db: Session, user_id: uuid.UUID) -> MemoryContext:
    current = state(db, user_id)
    return db.get(MemoryContext, current.focus_context_id)


def _context(db: Session, user_id: uuid.UUID, context_id: uuid.UUID) -> MemoryContext:
    context = db.scalar(select(MemoryContext).where(MemoryContext.id == context_id, MemoryContext.user_id == user_id))
    if context is None:
        raise MemoryError("not_found", "there's no such context")
    return context


def _touch(context: MemoryContext, now: datetime) -> None:
    context.last_active_at = now
    if context.status == "paused":
        context.status, context.status_reason = "active", None


def focus(db: Session, user_id: uuid.UUID, context_id: uuid.UUID, *, reason: str | None = None) -> MemoryContext:
    """Attend to another context. Nothing is closed: a topic change moves attention only."""
    context = _context(db, user_id, context_id)
    if context.status == "closed":
        raise MemoryError("closed", f'"{context.label}" is closed; it can only be remembered, not resumed')
    current = state(db, user_id, lock=True)
    _touch(context, _now())
    if current.focus_context_id != context.id:
        current.focus_context_id = context.id
        current.revision += 1
        record_event(db, user_id, kind="context_switched", text=f"Turned to {context.label}"
                     + (f" ({reason})" if reason else ""), epistemic="reported", context_id=context.id)
    return context


def open_context(db: Session, user_id: uuid.UUID, *, kind: str, label: str, source: str | None = None,
                 external_ref: str | None = None, parent_id: uuid.UUID | None = None,
                 focus_it: bool = True) -> MemoryContext:
    """A new context, or the existing one with the same source identity (idempotent)."""
    label = " ".join(label.split())
    if kind == "general" or not label:
        raise MemoryError("invalid", "a context needs a name, and there is only one general context")
    existing = None
    if external_ref is not None:
        existing = db.scalar(select(MemoryContext).where(
            MemoryContext.user_id == user_id, MemoryContext.source == source,
            MemoryContext.external_ref == external_ref))
    context = existing or MemoryContext(id=uuid.uuid4(), user_id=user_id, kind=kind, label=label, source=source,
                                        external_ref=external_ref, parent_id=parent_id)
    if existing is None:
        db.add(context)
        db.flush()
        record_event(db, user_id, kind="context_opened", text=f"Started: {label}", epistemic="reported",
                     context_id=context.id)
    if focus_it:
        focus(db, user_id, context.id)
    return context


def close_context(db: Session, user_id: uuid.UUID, context_id: uuid.UUID, *, reason: str) -> list[WorkingItem]:
    """Close a context: its active goals are suspended, open commitments need review, intentions lapse.
    Nothing is deleted. Returns the items changed."""
    context = _context(db, user_id, context_id)
    if context.kind == "general":
        raise MemoryError("invalid", "the general context never closes")
    if context.status == "closed":
        return []
    now = _now()
    changed = []
    open_items = select(WorkingItem).where(WorkingItem.context_id == context.id, WorkingItem.closed_at.is_(None))
    goal_ids = [entry.id for entry in db.scalars(open_items) if entry.kind == "goal"]
    # Lock order: sessions before items (spec 4.6).
    sessions = {goal_id: _linked_sessions(db, user_id, goal_id) for goal_id in goal_ids}
    for entry in db.scalars(open_items.with_for_update()).all():
        target = {"goal": "suspended", "commitment": "needs_review", "intention": "lapsed"}.get(entry.kind)
        if target and entry.status != target:
            _set_status(db, entry, target, f"{context.label} closed: {reason}", now)
            changed.append(entry)
    context.status, context.status_reason, context.closed_at = "closed", reason, now
    record_event(db, user_id, kind="context_closed", text=f"{context.label} closed: {reason}", epistemic="reported",
                 context_id=context.id)
    current = state(db, user_id, lock=True)
    if current.focus_context_id == context.id:
        general = ensure_general(db, user_id)
        current.focus_context_id = general.id
        current.revision += 1
        # Every focus change is a context_switched event: focus_at reads them back.
        record_event(db, user_id, kind="context_switched", text=f"Turned to General ({context.label} closed)",
                     epistemic="reported", context_id=general.id)
    for entry in changed:
        if entry.kind == "goal":
            _apply_goal_to_sessions(db, entry, sessions.get(entry.id, []))
    return changed


def focus_at(db: Session, user_id: uuid.UUID, at: datetime) -> MemoryContext:
    """The context in focus at a moment, from the focus changes recorded as ``context_switched`` events
    (General before any). Lets a message be bound to the context it was about after the fact."""
    moved = db.scalar(select(MemoryEvent.context_id).where(
        MemoryEvent.user_id == user_id, MemoryEvent.kind == "context_switched", MemoryEvent.occurred_at <= at,
    ).order_by(MemoryEvent.occurred_at.desc(), MemoryEvent.id.desc()).limit(1))
    context = db.get(MemoryContext, moved) if moved is not None else None
    return context or ensure_general(db, user_id)


def find_context(db: Session, user_id: uuid.UUID, label: str) -> MemoryContext | None:
    """An open context by name (case and spacing ignored)."""
    wanted = " ".join(label.split()).lower()
    return db.scalar(select(MemoryContext).where(
        MemoryContext.user_id == user_id, MemoryContext.status != "closed",
        func.lower(MemoryContext.label) == wanted).order_by(MemoryContext.last_active_at.desc()).limit(1))


def context_for_source(db: Session, user_id: uuid.UUID, source: str, *, label: str) -> MemoryContext:
    """The current open context of a source (e.g. the Minecraft world being played), opened if there's none.
    Used until the source reports its own identity (spec §7): then ``external_ref`` tells worlds apart."""
    context = db.scalar(select(MemoryContext).where(
        MemoryContext.user_id == user_id, MemoryContext.source == source, MemoryContext.status != "closed",
    ).order_by(MemoryContext.last_active_at.desc()).limit(1))
    if context is None:
        context = open_context(db, user_id, kind="world", label=label, source=source, focus_it=False)
    return context


def new_world(db: Session, user_id: uuid.UUID, source: str, *, label: str, reason: str,
              external_ref: str | None = None) -> tuple[MemoryContext, list[WorkingItem]]:
    """The source's world was replaced: close every open context of that source, open a new one, focus it.
    Returns the new context and the items closing changed."""
    _lock_live_sessions(db, user_id, source)  # lock order: sessions before items
    open_ones = db.scalars(select(MemoryContext).where(
        MemoryContext.user_id == user_id, MemoryContext.source == source, MemoryContext.status != "closed")).all()
    changed = []
    for context in open_ones:
        if external_ref is None or context.external_ref != external_ref:
            changed += close_context(db, user_id, context.id, reason=reason)
    # Live sessions keep their link to the old world's (now suspended) goal: that is what stops the
    # old plan resuming in the new world (resume_blocked); their next plan links a goal here.
    context = open_context(db, user_id, kind="world", label=label, source=source, external_ref=external_ref)
    return context, changed


def switch(db: Session, user_id: uuid.UUID, *, label: str, kind: str = "activity", new: bool = False,
           reason: str | None = None) -> tuple[MemoryContext, list[WorkingItem]]:
    """Turn to a context by name (opened if there's none). ``new`` with kind ``world``: the game being played
    moved to a different world, so the old world's context closes (its goals are suspended, not lost).
    Returns the context and what closing changed."""
    if kind == "general" or label.strip().lower() == "general":
        return focus(db, user_id, ensure_general(db, user_id).id, reason=reason), []
    if kind == "world" and new:
        live = db.scalar(select(DesktopSession).where(
            DesktopSession.owner_user_id == user_id, DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES),
        ).order_by(DesktopSession.started_at.desc()).limit(1))
        if live is not None:
            name = f"{GAME_LABELS.get(live.profile_id, live.profile_id)} world: {' '.join(label.split())}"
            return new_world(db, user_id, live.profile_id, label=name, reason=reason or "a new world")
    existing = None if new else find_context(db, user_id, label)
    if existing is not None:
        return focus(db, user_id, existing.id, reason=reason), []
    return open_context(db, user_id, kind=kind if kind in ("activity", "project", "world") else "activity",
                        label=label), []


def game_context(db: Session, session: DesktopSession) -> MemoryContext:
    label = f"{GAME_LABELS.get(session.profile_id, session.profile_id)} world"
    return context_for_source(db, session.owner_user_id, session.profile_id, label=label)


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #


def _open_status(kind: str) -> str:
    return WORKING_ITEM_STATUSES[kind][0][0]


def add_item(db: Session, user_id: uuid.UUID, *, kind: str, text_: str, owner: str, epistemic: str,
             context_id: uuid.UUID | None = None, parent_id: uuid.UUID | None = None, owed_to: str | None = None,
             done_when: str | None = None, due_at: datetime | None = None,
             source: Source | None = None, reason: str | None = None, user_words: str | None = None,
             time_values: dict | None = None) -> WorkingItem:
    """``reason``: why she concluded it, for what comes from her own thinking. ``time_values``: the ``time_*``
    fields of a checked time reading (services/memory_time.ground)."""
    words = " ".join((text_ or "").split())
    if not words:
        raise MemoryError("invalid", "say what it is")
    state(db, user_id)  # the due checks only see users with a state row
    if parent_id is not None:
        parent = item(db, user_id, parent_id)
        if kind not in WORKING_ITEM_SERVES:
            raise MemoryError("invalid", f"a {kind} doesn't go under a goal")
        if parent.kind != "goal" or parent.closed_at is not None:
            raise MemoryError("invalid", "it goes under an open goal")
        context_id = parent.context_id
    context = _context(db, user_id, context_id) if context_id else focused(db, user_id)
    if context.status == "closed":
        raise MemoryError("closed", f'"{context.label}" is closed; add it where you are now')
    now = _now()
    if kind == "intention" and due_at is None:
        due_at = now + timedelta(hours=get_settings().memory_intention_hours)
    if kind == "hypothesis" and due_at is None:  # never settled after this long: unresolved
        due_at = now + timedelta(days=get_settings().memory_hypothesis_days)
    entry = WorkingItem(id=uuid.uuid4(), user_id=user_id, context_id=context.id, kind=kind, parent_id=parent_id,
                        owner=owner, owed_to=owed_to, text=words, done_when=done_when or None,
                        status=_open_status(kind), epistemic=epistemic, due_at=due_at,
                        reason=(reason or "").strip() or None, user_words=(user_words or "").strip() or None,
                        **(time_values or {}))
    db.add(entry)
    db.flush()
    _touch(context, now)
    if source is not None:
        link(db, user_id, role="origin", source=source, item_id=entry.id)
    _schedule(db, user_id, due_at)
    record_event(db, user_id, kind="item_added", text=f"{_kind_word(entry)}: {words}", epistemic=epistemic,
                 context_id=context.id, item_id=entry.id)
    _log({"op": "add", "user": str(user_id), "item": str(entry.id), "kind": kind, "context": str(context.id)})
    return entry


def set_goal(db: Session, user_id: uuid.UUID, text_: str, *, owner: str = "user", epistemic: str = "reported",
             context_id: uuid.UUID | None = None, parent_id: uuid.UUID | None = None, done_when: str | None = None,
             source: Source | None = None) -> WorkingItem:
    """A goal (or a sub-goal). Another goal already open is never closed by this: both stand."""
    return add_item(db, user_id, kind="goal", text_=text_, owner=owner, epistemic=epistemic, context_id=context_id,
                    parent_id=parent_id, done_when=done_when, source=source)


def note_commitment(db: Session, user_id: uuid.UUID, text_: str, *, owner: str = "columbina",
                    due_at: datetime | None = None, context_id: uuid.UUID | None = None,
                    source: Source | None = None) -> WorkingItem:
    """Something one party owes the other. A promise to herself is an intention, not this."""
    if owner not in ("user", "columbina"):
        raise MemoryError("invalid", "a commitment is owed by you or by the user")
    if owner == "columbina" and fact_copy(text_):
        raise MemoryError("not_a_promise", "what the user tells you is kept in memory on its own; a promise is "
                                           "for something you still have to do")
    owed_to = "user" if owner == "columbina" else "columbina"
    return add_item(db, user_id, kind="commitment", text_=text_, owner=owner, owed_to=owed_to, epistemic="reported",
                    due_at=due_at, context_id=context_id, source=source)


_KEEP_PHRASES = ("remember", "keep in mind", "not forget", "don t forget", "do not forget", "never forget",
                 "memorize", "memorise")


def fact_copy(text_: str) -> bool:
    """A "promise" that only keeps something the user said ("Remember that Sam is allergic to peanuts"): a copy
    of a fact, not an unfinished obligation. "Remember to send the photos" is one, so it isn't this."""
    words = " ".join(re.findall(r"[a-z0-9]+", (text_ or "").lower()))
    for prefix in ("i ll ", "i will ", "i d ", "to "):
        if words.startswith(prefix):
            words = words[len(prefix):]
    for phrase in _KEEP_PHRASES:
        if words == phrase or words.startswith(phrase + " "):
            return not words[len(phrase):].strip().startswith("to ")
    return False


def note_intention(db: Session, user_id: uuid.UUID, text_: str, *, context_id: uuid.UUID | None = None,
                   epistemic: str = "inferred", source: Source | None = None) -> WorkingItem:
    """Her own plan: nobody is let down if it lapses (it does, after MEMORY_INTENTION_HOURS)."""
    return add_item(db, user_id, kind="intention", text_=text_, owner="columbina", epistemic=epistemic,
                    context_id=context_id, source=source)


def add_plan(db: Session, user_id: uuid.UUID, text_: str, *, user_words: str, time_values: dict,
             source: Source | None, owner: str = "user") -> WorkingItem:
    """Something the user means to do ("visit Nora in Lisbon in December"), with their words and the time read
    from them. Due at the end of that time: past it with no word, it needs a look (``tick_due``), and never
    becomes "happened" on its own."""
    return add_item(db, user_id, kind="plan", text_=text_, owner=owner, epistemic="reported",
                    due_at=(time_values or {}).get("time_end"), source=source, user_words=user_words,
                    time_values=time_values, context_id=ensure_general(db, user_id).id)


def revise_plan(db: Session, user_id: uuid.UUID, entry: WorkingItem, *, text_: str, user_words: str,
                time_values: dict, source: Source, role: str) -> bool:
    """The user said more about a plan already known: its wording and, when the new words give one, its time.
    The old version stays in an ``item_revised`` event. Returns whether anything changed."""
    words = " ".join((text_ or "").split())
    changes = {}
    if words and words != entry.text:
        changes["text"] = words
    if time_values and any(getattr(entry, k) != v for k, v in time_values.items() if k != "time_said_at"):
        changes.update(time_values)
        changes["due_at"] = time_values.get("time_end")
    if not changes:
        link(db, user_id, role="support", source=source, item_id=entry.id)
        return False
    before = f"{entry.text}" + (f' (when: "{entry.time_words}")' if entry.time_words else "")
    for key, value in changes.items():
        setattr(entry, key, value)
    entry.user_words = user_words or entry.user_words
    if entry.status == "needs_review" and changes.get("time_end") and changes["time_end"] > _now():
        entry.status, entry.status_reason = "planned", None
    entry.revision += 1
    entry.updated_at = _now()
    after = f"{entry.text}" + (f' (when: "{entry.time_words}")' if entry.time_words else "")
    event = record_event(db, user_id, kind="item_revised", text=f"Plan changed: {before} -> {after}",
                         epistemic="reported", context_id=entry.context_id, item_id=entry.id,
                         dedupe_key=f"plan:{entry.id}:{entry.revision}")
    link(db, user_id, role=role, source=source, item_id=entry.id)
    if event is not None:
        link(db, user_id, role=role, source=source, event_id=event.id)
    _schedule(db, user_id, entry.due_at)
    _log({"op": "revise_plan", "user": str(user_id), "item": str(entry.id), "fields": sorted(changes)})
    return True


def item(db: Session, user_id: uuid.UUID, key: uuid.UUID | str, *, lock: bool = False) -> WorkingItem:
    """An item by its id or short id (``ref``)."""
    query = select(WorkingItem).where(WorkingItem.user_id == user_id)
    if isinstance(key, uuid.UUID):
        query = query.where(WorkingItem.id == key)
    else:
        prefix = "".join(ch for ch in key.strip().lower() if ch in "0123456789abcdef-")
        if len(prefix.replace("-", "")) < 6:
            raise MemoryError("not_found", f'no item "{key}": use the id shown in brackets')
        query = query.where(cast(WorkingItem.id, String).like(prefix + "%"))
    found = db.scalars((query.with_for_update() if lock else query).limit(2)).all()
    if len(found) != 1:
        raise MemoryError("not_found", f'no single item "{key}": use the id shown in brackets')
    return found[0]


def _set_status(db: Session, entry: WorkingItem, status: str, reason: str, now: datetime) -> None:
    entry.status = status
    entry.status_reason = reason
    entry.closed_at = now if status in WORKING_ITEM_CLOSED else None
    entry.revision += 1
    entry.updated_at = now
    record_event(db, entry.user_id, kind="item_changed", text=f"{_kind_word(entry)} {status}: {entry.text}"
                 + (f" ({reason})" if reason else ""), epistemic="reported", context_id=entry.context_id,
                 item_id=entry.id)


def resolve(db: Session, user_id: uuid.UUID, key: uuid.UUID | str, *, status: str, reason: str,
            source: Source | None = None, expected_revision: int | None = None,
            background: bool = False) -> tuple[WorkingItem, list[WorkingItem]]:
    """Change an item's status. Returns it and its still-open sub-goals (closing a goal never closes them:
    the caller decides). A closed item stays closed; completing or fulfilling needs a source.
    ``background``: a turn the user didn't start may only change her own intentions."""
    entry = item(db, user_id, key)
    # Lock order: environment -> session -> working item (spec 4.6).
    sessions = _linked_sessions(db, user_id, entry.id) if entry.kind == "goal" else []
    entry = item(db, user_id, entry.id, lock=True)
    if expected_revision is not None and entry.revision != expected_revision:
        raise MemoryError("stale_revision", "it changed since you read it; read it again")
    if background and not (entry.kind == "intention" and entry.owner == "columbina"):
        raise MemoryError("not_yours", "without the user here you can only change your own intentions")
    if background and entry.id in _held(db, user_id):
        raise MemoryError("on_hold", "a fact it may depend on just changed; leave it until it's rechecked")
    opened, closed = WORKING_ITEM_STATUSES[entry.kind]
    if status not in opened + closed:
        raise MemoryError("invalid", f"a {entry.kind} can be: {', '.join(opened + closed)}")
    if entry.closed_at is not None:
        raise MemoryError("closed", f'that {entry.kind} is already {entry.status}')
    if status in ("completed", "fulfilled", "happened") and source is None:
        raise MemoryError("no_evidence", "say what shows it's done (only when the user said so or it's clearly done)")
    if status in ("supported", "disproved") and source is None:
        raise MemoryError("no_evidence", "a suspicion is only shown right or wrong by what the user said or what happened")
    if not (reason or "").strip():
        raise MemoryError("invalid", "give a short reason")
    if status == entry.status:
        if not background and source is not None:
            # The user's turn kept it as it is: if a fact it relies on changed, it now stands checked
            # (services/memory_dependencies.py; the way out of "may be out of date").
            from app.services import memory_dependencies

            if memory_dependencies.confirm(db, user_id, entry.id):
                link(db, user_id, role="support", source=source, item_id=entry.id)
                _log({"op": "confirm", "user": str(user_id), "item": str(entry.id)})
        return entry, []
    now = _now()
    _set_status(db, entry, status, " ".join(reason.split()), now)
    if source is not None:
        link(db, user_id, role="resolution" if status in closed else "support", source=source, item_id=entry.id)
    if entry.kind == "goal":
        _apply_goal_to_sessions(db, entry, sessions)
    children = list(db.scalars(select(WorkingItem).where(WorkingItem.parent_id == entry.id,
                                                         WorkingItem.closed_at.is_(None))))
    _log({"op": "resolve", "user": str(user_id), "item": str(entry.id), "status": status})
    return entry, children


def link(db: Session, user_id: uuid.UUID, *, role: str, source: Source, item_id: uuid.UUID | None = None,
         event_id: int | None = None) -> bool:
    """An evidence link, if the source exists and is this user's. Never fails the caller: a missing source
    just means no link (the memory stands on its own)."""
    return link_target(db, user_id, role=role, source=source, item_id=item_id, event_id=event_id)


def link_target(db: Session, user_id: uuid.UUID, *, role: str, source: Source, item_id: uuid.UUID | None = None,
                event_id: int | None = None, fact_id: uuid.UUID | None = None,
                entity_id: uuid.UUID | None = None) -> bool:
    """``link`` for any one target: an item, an event, a fact or an entity."""
    if not _owns_source(db, user_id, source):
        return False
    values = {"user_id": user_id, "item_id": item_id, "event_id": event_id, "fact_id": fact_id,
              "entity_id": entity_id, "role": role, _SOURCE_COLUMN[source.kind]: source.id}
    db.execute(insert(MemoryEvidence).values(**values).on_conflict_do_nothing())
    return True


def _owns_source(db: Session, user_id: uuid.UUID, source: Source) -> bool:
    if source.kind == "message":
        query = (select(ChatMessage.id).join(Conversation, Conversation.id == ChatMessage.conversation_id)
                 .where(ChatMessage.id == source.id, Conversation.user_id == user_id))
    elif source.kind == "session_event":
        query = (select(DesktopSessionEvent.id).join(DesktopSession, DesktopSession.id == DesktopSessionEvent.session_id)
                 .where(DesktopSessionEvent.id == source.id, DesktopSession.owner_user_id == user_id))
    elif source.kind == "thought":
        query = select(MindThought.id).where(MindThought.id == source.id, MindThought.user_id == user_id)
    elif source.kind == "experience":
        query = select(GameExperience.id).where(GameExperience.id == source.id, GameExperience.user_id == user_id)
    elif source.kind == "observation":
        query = select(Observation.id).where(Observation.id == source.id, Observation.user_id == user_id)
    else:
        return False
    return db.scalar(query) is not None


def record_event(db: Session, user_id: uuid.UUID, *, kind: str, text: str, epistemic: str,
                 context_id: uuid.UUID | None = None, item_id: uuid.UUID | None = None, source: Source | None = None,
                 occurred_at: datetime | None = None, dedupe_key: str | None = None) -> MemoryEvent | None:
    """One meaningful event. A repeated ``dedupe_key`` is a no-op (returns None)."""
    words = " ".join((text or "").split())
    if not words:
        return None
    if context_id is None:
        context_id = focused(db, user_id).id
    statement = insert(MemoryEvent).values(
        user_id=user_id, context_id=context_id, item_id=item_id, kind=kind, text=words, epistemic=epistemic,
        occurred_at=occurred_at or _now(), dedupe_key=dedupe_key)
    if dedupe_key is not None:
        statement = statement.on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"],
                                                     index_where=sql_text("dedupe_key IS NOT NULL"))
    event_id = db.execute(statement.returning(MemoryEvent.id)).scalar()
    if event_id is None:
        return None
    if source is not None:
        link(db, user_id, role="origin", source=source, event_id=event_id)
    return db.get(MemoryEvent, event_id)


def revise_item(db: Session, user_id: uuid.UUID, entry: WorkingItem, *, field: str, replaced: str,
                replacement: str, source: Source, dedupe_key: str) -> MemoryEvent | None:
    """A correction changes what an item says, and only that: the one span in ``text`` or ``done_when`` is
    replaced, while its id, kind, owner, status, parent, context and times stay. The caller holds the item's
    row lock and has checked its revision. The old wording stays in an ``item_revised`` event, and the
    correcting message is linked (role ``correction``) to the item and the event."""
    if field not in ("text", "done_when") or entry.user_id != user_id:
        raise MemoryError("invalid", "only an item's own wording can be corrected")
    before = getattr(entry, field) or ""
    if before.count(replaced) != 1:
        raise MemoryError("invalid", "the corrected words must appear exactly once")
    setattr(entry, field, " ".join(before.replace(replaced, replacement).split()))
    entry.revision += 1
    entry.updated_at = _now()
    where = "" if field == "text" else f" (done when: {entry.done_when})"
    event = record_event(db, user_id, kind="item_revised",
                         text=f'{_kind_word(entry)} corrected ("{replaced}" -> "{replacement}"): {entry.text}{where}',
                         epistemic="reported", context_id=entry.context_id, item_id=entry.id, dedupe_key=dedupe_key)
    link(db, user_id, role="correction", source=source, item_id=entry.id)
    if event is not None:
        link(db, user_id, role="correction", source=source, event_id=event.id)
    _log({"op": "revise", "user": str(user_id), "item": str(entry.id), "field": field})
    return event


def record_correction(db: Session, user_id: uuid.UUID, *, text_: str, context_id: uuid.UUID,
                      occurred_at: datetime, source: Source, dedupe_key: str) -> MemoryEvent:
    """The user corrected something: one ``corrected`` event (when they said it), linked to their message.
    What was recorded before stays as it was: this is added history, never a rewrite of it."""
    event = record_event(db, user_id, kind="corrected", text=text_, epistemic="reported", context_id=context_id,
                         occurred_at=occurred_at, dedupe_key=dedupe_key)
    if event is None:  # already recorded by an earlier attempt
        event = db.scalar(select(MemoryEvent).where(MemoryEvent.user_id == user_id,
                                                    MemoryEvent.dedupe_key == dedupe_key))
    link(db, user_id, role="correction", source=source, event_id=event.id)
    return event


def _kind_word(entry: WorkingItem) -> str:
    return {"goal": "Sub-goal" if entry.parent_id else "Goal", "commitment": "Commitment", "intention": "Intention",
            "question": "Question", "hypothesis": "Suspicion", "decision": "Decision", "plan": "Plan",
            "constraint": "Rule from the user"}[entry.kind]


# --------------------------------------------------------------------------- #
# Games: a session's plan serves a goal (spec 4.6, §9)
# --------------------------------------------------------------------------- #


def _linked_sessions(db: Session, user_id: uuid.UUID, goal_id: uuid.UUID) -> list[DesktopSession]:
    return list(db.scalars(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id, DesktopSession.goal_item_id == goal_id,
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES)).with_for_update()))


GOAL_PAUSE_REASONS = {"suspended": "goal_suspended", "completed": "goal_completed", "abandoned": "goal_abandoned"}


def _apply_goal_to_sessions(db: Session, goal: WorkingItem, sessions: list[DesktopSession]) -> None:
    """A suspended, completed or abandoned goal pauses the sessions playing for it (input stops once the
    transaction commits). Nothing resumes on its own when the goal is active again."""
    reason = GOAL_PAUSE_REASONS.get(goal.status)
    if reason is None:
        return
    from app.services import desktop_sessions

    stopped = []
    for session in sessions:
        if session.state == "running":
            desktop_sessions.transition(session, "paused", reason=reason, db=db)
            stopped.append(session)
    if stopped:
        def stop_input(_db_session) -> None:
            for session in stopped:
                desktop_sessions.stop_input(session, reason)
        event.listen(db, "after_commit", stop_input, once=True)


def resume_blocked(db: Session, session: DesktopSession) -> str | None:
    """Why a session can't resume because of its goal, or None."""
    if session.goal_item_id is None:
        return None
    goal = db.get(WorkingItem, session.goal_item_id)
    if goal is None:
        return None
    if db.get(MemoryContext, goal.context_id).status == "closed":
        return (f'the goal it played for ("{goal.text}") belongs to a world that is closed now; '
                "set a new plan to keep playing")
    if goal.status == "suspended":
        return f'the goal it plays for ("{goal.text}") is suspended; make it active again first'
    if goal.closed_at is not None:
        return f'the goal it played for ("{goal.text}") is {goal.status}; set a new goal to keep playing'
    return None


def on_plan_set(db: Session, session: DesktopSession, *, goal_text: str, source: str,
                plan_event: DesktopSessionEvent | None = None) -> WorkingItem | None:
    """Called whenever a session gets a plan. The plan serves a goal: the one the session already serves if
    it's still open, else the newest active goal of the game's context, else a new goal from the plan's own
    words. Replacing the plan never changes or closes that goal."""
    user_id = session.owner_user_id
    context = game_context(db, session)
    goal = db.get(WorkingItem, session.goal_item_id) if session.goal_item_id else None
    if goal is not None and (goal.closed_at is not None or goal.context_id != context.id):
        goal = None
    if goal is None:
        goal = db.scalar(select(WorkingItem).where(
            WorkingItem.user_id == user_id, WorkingItem.context_id == context.id, WorkingItem.kind == "goal",
            WorkingItem.parent_id.is_(None), WorkingItem.status == "active",
        ).order_by(WorkingItem.created_at.desc()).limit(1))
    if goal is None:
        by_user = source in USER_PLAN_SOURCES
        if plan_event is not None:
            db.flush()
        goal = set_goal(db, user_id, goal_text, owner="user" if by_user else "columbina",
                        epistemic="reported" if by_user else "inferred", context_id=context.id,
                        source=Source("session_event", plan_event.id) if plan_event is not None else None)
    elif goal.status == "suspended" and source in USER_PLAN_SOURCES:
        _set_status(db, goal, "active", "the user set a plan for it", _now())
    _link_session(session, goal)
    if session.plan is not None:
        # The goal this plan was made for. ``goal_id`` follows the session to a newer goal; this stays, so a
        # revision can tell an old goal's plan (and its rules) from the current one's (seen live 2026-11-05).
        session.plan = {**session.plan, "made_for_goal": str(goal.id)}  # reassign: JSONB doesn't track in place
    if source in USER_PLAN_SOURCES:  # her own revisions in the background don't move her attention
        focus(db, user_id, context.id)
    return goal


def begin_goal(db: Session, session: DesktopSession, text_: str, *, by_user: bool,
               source: Source | None = None) -> WorkingItem:
    """A different goal for this game (``new_goal=true``): the old one stays open, the session plays for this."""
    context = game_context(db, session)
    current = db.get(WorkingItem, session.goal_item_id) if session.goal_item_id else None
    if (current is not None and current.closed_at is None and current.context_id == context.id
            and " ".join(current.text.split()).lower() == " ".join(text_.split()).lower()):
        focus(db, session.owner_user_id, context.id)  # already this goal (a new session's plan just made it)
        return current
    goal = set_goal(db, session.owner_user_id, text_, owner="user" if by_user else "columbina",
                    epistemic="reported" if by_user else "inferred", context_id=context.id, source=source)
    _link_session(session, goal)
    focus(db, session.owner_user_id, context.id)
    return goal


def _link_session(session: DesktopSession, goal: WorkingItem) -> None:
    session.goal_item_id = goal.id
    if session.plan is not None and session.plan.get("goal_id") != str(goal.id):
        session.plan = {**session.plan, "goal_id": str(goal.id)}  # reassign: JSONB doesn't track in place


def _lock_live_sessions(db: Session, user_id: uuid.UUID, source: str) -> list[DesktopSession]:
    return list(db.scalars(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id, DesktopSession.profile_id == source,
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES)).with_for_update()))


def session_goal(db: Session, session: DesktopSession) -> WorkingItem | None:
    if session.goal_item_id is None:
        return None
    return db.get(WorkingItem, session.goal_item_id)


def goal_line(db: Session, session: DesktopSession) -> str | None:
    """The goal a session plays for, in a line for her game summary and her planner."""
    goal = session_goal(db, session)
    if goal is None:
        return None
    whose = {"user": "the user's", "columbina": "your own", "shared": "shared"}[goal.owner]
    state_note = "" if goal.status == "active" else f" ({goal.status}: {goal.status_reason or 'no reason given'})"
    return (f'The goal this plays for, in your memory ({whose}, [{ref(goal)}]): "{goal.text}"{state_note}. '
            "A new plan never replaces it; only the user ending it does (or it being clearly done).")


# --------------------------------------------------------------------------- #
# An effort: an outcome and what serves it (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md)
# --------------------------------------------------------------------------- #


def open_goal_of_session(db: Session, session_id: uuid.UUID | None) -> WorkingItem | None:
    """The open goal a game session plays for, or None. What her thoughts and the game's events in that
    session serve."""
    session = db.get(DesktopSession, session_id) if session_id else None
    goal = db.get(WorkingItem, session.goal_item_id) if session is not None and session.goal_item_id else None
    return goal if goal is not None and goal.closed_at is None else None


def serving(db: Session, goal: WorkingItem) -> list[WorkingItem]:
    """Everything that points at the goal (her approaches, the user's rules, sub-goals), oldest first."""
    return list(db.scalars(select(WorkingItem).where(WorkingItem.user_id == goal.user_id,
                                                     WorkingItem.parent_id == goal.id)
                           .order_by(WorkingItem.created_at)))


def add_constraint(db: Session, user_id: uuid.UUID, goal: WorkingItem, text_: str, *, user_words: str,
                   source: Source | None = None) -> WorkingItem:
    """A rule the user gave for this outcome, in force until lifted. ``user_words``: what they said."""
    if not " ".join((user_words or "").split()):
        raise MemoryError("invalid", "a rule from the user needs their words")
    for entry in serving(db, goal):
        if (entry.kind == "constraint" and entry.closed_at is None
                and " ".join(entry.text.split()).lower() == " ".join(text_.split()).lower()):
            return entry  # already in force
    return add_item(db, user_id, kind="constraint", text_=text_, owner="user", epistemic="reported",
                    parent_id=goal.id, source=source, user_words=user_words)


def lift_constraint(db: Session, user_id: uuid.UUID, key: uuid.UUID | str, *, reason: str,
                    source: Source | None = None) -> WorkingItem:
    entry = item(db, user_id, key)
    if entry.kind != "constraint":
        raise MemoryError("invalid", "only a rule from the user can be lifted")
    return resolve(db, user_id, entry.id, status="lifted", reason=reason, source=source)[0]


def add_reference(db: Session, user_id: uuid.UUID, goal: WorkingItem, name: str, *, where: str | None = None,
                  words: str | None = None, kind: str = "place",
                  message_id: uuid.UUID | None = None) -> MemoryEntity:
    """A place or thing that matters to this outcome ("the cave Vincent showed me", where it is)."""
    name = " ".join((name or "").split())[:200]
    if not name:
        raise MemoryError("invalid", "name the place or thing")
    for entity in references(db, goal):
        if (entity.name or "").lower() == name.lower():
            return entity
    entity = MemoryEntity(user_id=user_id, kind=kind, name=name, source_message_id=message_id,
                          metadata_={"where": " ".join((where or "").split())[:200] or None,
                                     "words": " ".join((words or "").split())[:300] or None,
                                     "for_goal": str(goal.id)})
    db.add(entity)
    db.flush()
    db.add(MemoryEntityLink(user_id=user_id, entity_id=entity.id, item_id=goal.id, role="reference"))
    db.flush()
    _log({"op": "reference", "user": str(user_id), "goal": str(goal.id), "entity": str(entity.id)})
    return entity


def references(db: Session, goal: WorkingItem) -> list[MemoryEntity]:
    return list(db.scalars(select(MemoryEntity).join(MemoryEntityLink, MemoryEntityLink.entity_id == MemoryEntity.id)
                           .where(MemoryEntityLink.item_id == goal.id, MemoryEntityLink.role == "reference",
                                  MemoryEntity.status == "active").order_by(MemoryEntity.created_at)))


# --------------------------------------------------------------------------- #
# The view: what we're doing, for her reply, her mind and her planner (spec §6)
# --------------------------------------------------------------------------- #


def _ago(then: datetime, now: datetime) -> str:
    seconds = max(0.0, (now - then).total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def _whose(owner: str, user_name: str) -> str:
    return {"user": f"{user_name}'s", "columbina": "yours", "shared": "both of yours"}[owner]


def _origin_known(db: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(db.scalars(select(MemoryEvidence.item_id).where(MemoryEvidence.item_id.in_(ids),
                                                               MemoryEvidence.role == "origin")))


def _item_line(entry: WorkingItem, now: datetime, user_name: str, sourced: set[uuid.UUID], plans: dict,
               held: dict[uuid.UUID, str] | None = None) -> str:
    tag = f"[{ref(entry)}]"
    lost = "" if entry.id in sourced or entry.epistemic == "inferred" else " (where it came from is no longer available)"
    status = "" if entry.status in ("active", "open", "planned") else f" ({entry.status}"
    if status:
        status += (f": {entry.status_reason}" if entry.status_reason else "") + ")"
    if entry.kind == "goal":
        line = f"Goal {tag} ({_whose(entry.owner, user_name)}, set {_ago(entry.created_at, now)}): {entry.text}"
        if entry.done_when:
            line += f" (done when: {entry.done_when})"
        if entry.id in plans:
            line += f'. Your game plan for it right now: "{plans[entry.id]}"'
    elif entry.kind == "commitment":
        who = "You promised" if entry.owner == "columbina" else f"{user_name} promised you"
        line = f"{who} {tag} ({_ago(entry.created_at, now)}): {entry.text}"
        if entry.status == "needs_review":
            line += " (overdue or its context closed: check whether it still stands)"
            status = ""
    elif entry.kind == "intention":
        line = f"You mean to {tag}: {entry.text}"
    elif entry.kind == "hypothesis":
        line = f"You suspect {tag} (your own guess, not confirmed): {entry.text}"
    elif entry.kind == "decision":
        line = f"You decided {tag}: {entry.text}"
    elif entry.kind == "constraint":
        line = f'{user_name}\'s rule {tag}: {entry.text} (their words: "{entry.user_words}")'
    elif entry.kind == "plan":
        from app.services import memory_time

        span = memory_time.span(entry)
        when = f" (when: {memory_time.describe(span, now)})" if span else ""
        whose = {"user": f"{user_name}'s plan", "columbina": "Your plan"}.get(entry.owner, f"Your and {user_name}'s plan")
        line = f"{whose} {tag}: {entry.text}{when}"
        if entry.status == "needs_review":
            line += " (its time has passed and nobody said whether it happened)"
            status = ""
    else:
        who = "You asked" if entry.owner == "columbina" else f"{user_name} asked"
        line = f"{who} {tag}, still open: {entry.text}"
    from app.services.memory_dependencies import HOLD_TEXT

    hold = (held or {}).get(entry.id)
    on_hold = f" ({HOLD_TEXT[hold]})" if hold else ""
    return line + status + lost + on_hold


def _held(db: Session, user_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """Items on hold, with why: a fact they rely on is being rechecked (``pending``) or couldn't be (``review``)
    (services/memory_dependencies.py)."""
    from app.services import memory_dependencies

    return memory_dependencies.holds(db, user_id)


def _top_goals(entries: list[WorkingItem]) -> list[WorkingItem]:
    """Goals to list on their own line: top-level ones, and open sub-goals whose parent has closed
    (closing a goal never closes its sub-goals, so they mustn't vanish with it)."""
    open_ids = {entry.id for entry in entries}
    return [g for g in entries if g.kind == "goal" and (g.parent_id is None or g.parent_id not in open_ids)]


def view(db: Session, user_id: uuid.UUID, *, user_name: str | None = None, now: datetime | None = None,
         autonomous: bool = False) -> str | None:
    """The memory view: the focused context in full, the other open ones in a line each, and closed ones
    only while something there is unresolved. None when there's nothing to say. Items on hold (a fact they
    may depend on is being reconciled) are marked; ``autonomous`` (a turn or thought the user didn't start)
    leaves them out, so nothing she does on her own acts on them."""
    now = now or _now()
    name = user_name or "the user"
    current = state(db, user_id)
    focus_ctx = db.get(MemoryContext, current.focus_context_id)
    open_items = list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id,
        (WorkingItem.closed_at.is_(None))).order_by(WorkingItem.created_at)))
    held = _held(db, user_id)
    if autonomous:
        open_items = [entry for entry in open_items if entry.id not in held]
    sourced = _origin_known(db, [entry.id for entry in open_items])
    plans = {row.goal_item_id: (row.plan or {}).get("goal") for row in db.scalars(select(DesktopSession).where(
        DesktopSession.owner_user_id == user_id, DesktopSession.goal_item_id.is_not(None),
        DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))) if (row.plan or {}).get("goal")}
    by_context: dict[uuid.UUID, list[WorkingItem]] = {}
    for entry in open_items:
        by_context.setdefault(entry.context_id, []).append(entry)

    mine = by_context.get(focus_ctx.id, [])
    goals = _top_goals(mine)
    goals.sort(key=lambda g: (g.status != "active", -g.created_at.timestamp()))
    children: dict[uuid.UUID, list[WorkingItem]] = {}
    shown = {g.id for g in goals}
    for entry in mine:
        if entry.kind == "goal" and entry.parent_id is not None and entry.id not in shown:
            children.setdefault(entry.parent_id, []).append(entry)
    goal_lines = []
    for goal in goals[:VIEW_GOALS]:
        goal_lines.append("  " + _item_line(goal, now, name, sourced, plans, held))
        goal_lines += ["    - " + _item_line(c, now, name, sourced, plans, held)
                       for c in children.get(goal.id, [])[:VIEW_CHILDREN]]
    commitments = ["  " + _item_line(e, now, name, sourced, plans, held)
                   for e in mine if e.kind in ("commitment", "plan")][:VIEW_ITEMS]
    questions = ["  " + _item_line(e, now, name, sourced, plans, held)
                 for e in mine if e.kind == "question"][:VIEW_ITEMS]
    intentions = ["  " + _item_line(e, now, name, sourced, plans, held) for e in mine
                  if e.kind in ("intention", "hypothesis", "decision")][:VIEW_ITEMS]
    events = list(db.scalars(select(MemoryEvent).where(
        MemoryEvent.context_id == focus_ctx.id, MemoryEvent.kind.not_in(("item_added", "item_changed", "context_switched")),
    ).order_by(MemoryEvent.id.desc()).limit(VIEW_EVENTS)))
    recent = ["  Recent: " + "; ".join(f"{_ago(e.occurred_at, now)} {e.text}" for e in reversed(events))] if events else []
    from app.services import observation_memory

    fresh = observation_memory.pending_lines(db, user_id, focus_ctx.id, now)
    just = ["  Just reported, not in memory yet: " + "; ".join(f"{_ago(at, now)} {text}" for at, text in fresh)
            ] if fresh else []

    others = []
    for context in db.scalars(select(MemoryContext).where(
            MemoryContext.user_id == user_id, MemoryContext.status != "closed", MemoryContext.id != focus_ctx.id,
    ).order_by(MemoryContext.last_active_at.desc())):
        open_goals = _top_goals(by_context.get(context.id, []))
        rest = [e for e in by_context.get(context.id, []) if e.kind != "goal"]
        if not open_goals and not rest:
            continue
        parts = [f'goal [{ref(g)}] "{g.text}"' + ("" if g.status == "active" else f" ({g.status})")
                 for g in open_goals[:2]]
        if rest:
            parts.append(f"{len(rest)} other open item{'s' if len(rest) > 1 else ''}")
        others.append(f"  {context.label} (last active {_ago(context.last_active_at, now)}): " + "; ".join(parts))
        if len(others) >= VIEW_OTHER:
            break
    past = []
    for context in db.scalars(select(MemoryContext).where(
            MemoryContext.user_id == user_id, MemoryContext.status == "closed",
    ).order_by(MemoryContext.closed_at.desc())):
        waiting = by_context.get(context.id, [])
        if not waiting:
            continue
        what = "; ".join(f'{e.kind} "{e.text}" ({e.status})' for e in waiting[:2])
        past.append(f"  {context.label}, closed {_ago(context.closed_at, now)} ({context.status_reason}): {what}. "
                    "That belonged there: don't treat it as true here.")
        if len(past) >= VIEW_PAST:
            break

    if not (goal_lines or commitments or questions or intentions or recent or just or others or past):
        return None
    header = [f"Focus: {focus_ctx.label}" + ("" if focus_ctx.kind == "general" else f" ({focus_ctx.kind})")]
    sections = [header, goal_lines or ["  No goal set here."], commitments, questions, intentions, recent, just,
                ["Also open elsewhere:", *others] if others else [], ["Earlier, closed:", *past] if past else []]

    def render() -> str:
        return "\n".join(line for section in sections for line in section)

    # Over budget: drop the least important first (events, intentions, other contexts, past, then even
    # what was just reported).
    for index in (5, 4, 7, 8, 6):
        if len(render()) <= VIEW_CHARS:
            break
        sections[index] = []
    return render()


def pending(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> list[WorkingItem]:
    """What needs a look: overdue or flagged items. A query, never stored (spec 4.4)."""
    now = now or _now()
    return list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None),
        (WorkingItem.status == "needs_review") | (WorkingItem.due_at <= now)).order_by(WorkingItem.due_at)))


# --------------------------------------------------------------------------- #
# Due maintenance (code only, scheduled independently by the coordinator)
# --------------------------------------------------------------------------- #


def _schedule(db: Session, user_id: uuid.UUID, due_at: datetime | None) -> None:
    if due_at is None:
        return
    from app.services import background_wake
    background_wake.mark(db)
    db.execute(update(MemoryManagerState).where(MemoryManagerState.user_id == user_id,
                                                MemoryManagerState.next_check_at > due_at)
               .values(next_check_at=due_at))


def tick_due(db: Session, now: datetime | None = None) -> int:
    """Every user whose check is due: lapse intentions, flag overdue commitments, pause idle contexts. No
    model, no messages. A user another worker is ticking is skipped. Returns how many users were checked."""
    now = now or _now()
    users = list(db.scalars(select(MemoryManagerState.user_id).where(MemoryManagerState.next_check_at <= now)))
    checked = 0
    for user_id in users:
        try:
            if _tick_user(db, user_id, now):
                checked += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one user's failure never stops the others
            log.exception("memory: tick failed for %s", user_id)
            db.rollback()
    return checked


def _tick_user(db: Session, user_id: uuid.UUID, now: datetime) -> bool:
    if not db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtext(f"memory:{user_id}")))):
        return False
    current = db.scalar(select(MemoryManagerState).where(MemoryManagerState.user_id == user_id).with_for_update())
    if current is None or current.next_check_at > now:
        return False
    done = []
    for entry in db.scalars(select(WorkingItem).where(
            WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None), WorkingItem.due_at <= now,
            WorkingItem.kind.in_(("intention", "commitment", "hypothesis", "plan")),
            WorkingItem.status != "needs_review").with_for_update()):
        if entry.kind == "intention":
            _set_status(db, entry, "lapsed", "its time passed", now)
        elif entry.kind == "hypothesis":
            _set_status(db, entry, "unresolved", "nothing settled it", now)
        elif entry.kind == "plan":
            _set_status(db, entry, "needs_review", "its time passed; whether it happened isn't known", now)
        else:
            _set_status(db, entry, "needs_review", "it's overdue", now)
        done.append(f"{entry.kind}:{entry.status}")
    idle = now - timedelta(hours=get_settings().memory_context_idle_hours)
    for context in db.scalars(select(MemoryContext).where(
            MemoryContext.user_id == user_id, MemoryContext.status == "active", MemoryContext.kind != "general",
            MemoryContext.last_active_at < idle)):
        context.status, context.status_reason = "paused", "nothing happened in it for a while"
        done.append("context:paused")
    upcoming = db.scalar(select(func.min(WorkingItem.due_at)).where(
        WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None), WorkingItem.due_at > now,
        WorkingItem.status != "needs_review"))
    current.next_check_at = min(upcoming, now + MAX_CHECK_GAP) if upcoming else now + MAX_CHECK_GAP
    current.last_tick_at = now
    current.last_event_id = db.scalar(select(func.coalesce(func.max(MemoryEvent.id), 0))
                                      .where(MemoryEvent.user_id == user_id))
    current.last_decision = {"at": now.isoformat(), "decision": "apply" if done else "skip",
                             "reason": ", ".join(done) or "nothing due"}
    current.revision += 1
    if done:
        _log({"op": "tick", "user": str(user_id), "changed": done})
    return True


# --------------------------------------------------------------------------- #
# Callers outside a request (own DB session; never fail theirs)
# --------------------------------------------------------------------------- #


def thought_context(db: Session, user_id: uuid.UUID, source: str, activity: uuid.UUID | None) -> MemoryContext:
    """Where a thought of hers belongs, decided when she thought it: a game session's world, else the
    context in focus then."""
    session = db.get(DesktopSession, activity) if source != "conversation" and activity else None
    if session is not None and session.owner_user_id == user_id:
        return game_context(db, session)
    return focused(db, user_id)


def restate(db: Session, user_id: uuid.UUID, entry: WorkingItem, source: Source) -> None:
    """She reached the same conclusion again: linked as a restatement, never as support. A suspicion she
    keeps returning to waits again before it counts as never settled."""
    link(db, user_id, role="restated", source=source, item_id=entry.id)
    if entry.kind == "hypothesis" and entry.closed_at is None:
        entry.due_at = _now() + timedelta(days=get_settings().memory_hypothesis_days)
        _schedule(db, user_id, entry.due_at)


def inspect(db: Session, user_id: uuid.UUID) -> dict:
    """Everything for the inspector (GET /api/memory/working)."""
    current = state(db, user_id)
    contexts = list(db.scalars(select(MemoryContext).where(MemoryContext.user_id == user_id)
                               .order_by(MemoryContext.last_active_at.desc()).limit(20)))
    items = list(db.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id)
                            .order_by(WorkingItem.closed_at.is_not(None), WorkingItem.updated_at.desc()).limit(100)))
    events = list(db.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id)
                             .order_by(MemoryEvent.id.desc()).limit(50)))
    sourced = _origin_known(db, [entry.id for entry in items])
    db.commit()
    return {
        "state": {"focus_context_id": current.focus_context_id, "last_tick_at": current.last_tick_at,
                  "next_check_at": current.next_check_at, "last_decision": current.last_decision,
                  "last_event_id": current.last_event_id},
        "contexts": contexts, "items": items, "events": events,
        "sourced": sourced, "pending": [entry.id for entry in pending(db, user_id)],
    }


def _log(record: dict) -> None:
    path = Path(get_settings().memory_manager_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **record}) + "\n")
    except OSError:
        log.warning("memory: could not write %s", path)


def view_for(user_id: uuid.UUID, *, user_name: str | None = None, autonomous: bool = False) -> str | None:
    """``view`` with its own DB session, for a turn being assembled (run in a thread). Never fails a turn:
    any error just means no memory view this turn. None when working memory is empty."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            rendered = view(db, user_id, user_name=user_name, autonomous=autonomous)
            db.commit()
            return rendered
    except Exception:  # noqa: BLE001 - logged, then the turn goes on without it
        log.exception("memory: could not build the view for %s", user_id)
        return None
