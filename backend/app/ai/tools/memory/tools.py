"""The working-memory tools: keep a goal, close or change an item, note a promise, an intention or an open
question, turn to another context. Her mind owns what is noted and when it's closed; memory persists it. Each calls ``app.services.memory_manager`` for this user only. Evidence is
the user's message this reply answers, filled in here, never by the model. Refusals come back as
plain text the model can relay.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

from langchain_core.tools import BaseTool, tool

from app.db.session import SessionLocal
from app.services import memory_manager
from app.services.memory_manager import MemoryError, Source

ItemStatus = Literal["active", "suspended", "completed", "abandoned", "open", "fulfilled", "cancelled",
                     "done", "dropped", "asked", "answered", "stale", "supported", "disproved", "unresolved",
                     "superseded", "withdrawn"]


async def _run(fn: Callable[..., str], user_id: uuid.UUID, kind: str) -> str:
    """Run one working-memory change in its own transaction; a kept change is the System page's mind -> memory
    pulse (``kind``: what she noted or changed)."""
    def call() -> str:
        with SessionLocal() as db:
            try:
                result = fn(db)
                db.commit()
            except MemoryError as exc:
                db.rollback()
                return f"Not done: {exc.message}."
        from app.services import system_signals
        system_signals.emit(user_id, "mind", "memory", kind)
        return result
    return await asyncio.to_thread(call)


def build_memory_tools(user_id: uuid.UUID, *, message_id: uuid.UUID | None = None,
                       background: bool = False, wake: bool = False, evaluation=None) -> list[BaseTool]:
    """Memory tools for one evaluation. ``message_id``: the user's message being answered (the evidence
    for what these keep). ``background``: a game-event turn: only her own intentions. ``wake``: her mind woke
    without a user message: a promise of hers she keeps in this evaluation can be marked fulfilled. ``evaluation``:
    the evaluation of her mind (``mind_turn.Evaluation``): a promise of hers marked fulfilled is noted on it and
    closed only with her delivered words (workers/chat.py); a timed promise's wake is kept in its conversation."""
    source = Source("message", message_id) if message_id is not None else None

    def relying(db, entry, relies_on: list[str] | None) -> str:
        """What the item relies on (services/memory_dependencies.py), and what couldn't be linked."""
        if not relies_on:
            return ""
        from app.services import memory_dependencies

        linked, refused = memory_dependencies.link_refs(db, user_id, entry.id, relies_on[:6], role="required")
        note = f" Relies on {len(linked)} fact(s)." if linked else ""
        if refused:
            note += f" Not linked (not one of the user's current facts): {', '.join(refused)}."
        return note

    @tool
    async def set_goal(goal: str, parent: str | None = None, done_when: str | None = None,
                       whose: Literal["user", "columbina", "shared"] = "user",
                       relies_on: list[str] | None = None) -> str:
        """Keep a goal in your memory: something the user (or you, or both) is trying to achieve that
        outlives this conversation ("prepare for Friday's interview", "plan the Japan trip").

        Only when the user names an end they want, not for a step toward a goal you already have:
        small instructions serve the current goal and need no new one. Another open goal is never
        closed by this: both stand. A goal in a game is set with the game tools, which keep it here too.
        parent: the id in brackets of the goal this is a sub-goal of, if it is one.
        done_when: what will show it's done, if the user said.
        relies_on: the ids in brackets of facts from your memory (fact:...) it only makes sense while they
        hold ("plan the trip to Nora's in Lisbon" relies on where Nora lives). If one changes, it's rechecked.
        """
        def do(db) -> str:
            parent_id = memory_manager.item(db, user_id, parent).id if parent else None
            entry = memory_manager.set_goal(db, user_id, goal, owner=whose, done_when=done_when,
                                            parent_id=parent_id, source=source,
                                            epistemic="reported" if whose != "columbina" else "inferred")
            return f"Kept: goal [{memory_manager.ref(entry)}] \"{entry.text}\"." + relying(db, entry, relies_on)
        return await _run(do, user_id, "goal")

    def when_of(at: str | None, in_seconds: int | None) -> datetime | None:
        if at is None and in_seconds is None:
            return None
        from app.ai.mind import when as W
        try:
            return W.resolve(in_seconds=in_seconds, at=at, zone=getattr(evaluation, "zone", None),
                             now=datetime.now(timezone.utc))
        except ValueError as exc:
            raise MemoryError("invalid", str(exc)) from None

    def condition(db, when: str | None, awaits: str | None) -> dict | None:
        """``when``: what it waits for, in words; ``awaits``: thinking of hers it waits for ("work:1a2b3c4d")."""
        if not awaits:
            return {"kind": "event", "text": when} if (when or "").strip() else None
        from app.db.models import CoordinatorWork
        from sqlalchemy import select

        key = awaits.removeprefix("work:").strip().lower()
        rows = [w for w in db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.user_id == user_id, CoordinatorWork.kind.in_(("mind", "recall"))))
            if str(w.id).startswith(key)] if len(key) >= 6 else []
        if len(rows) != 1:
            raise MemoryError("not_found", f'no thinking of yours "{awaits}": use the id shown in brackets')
        return {"kind": "work", "work_id": str(rows[0].id), "request_id": str(rows[0].request_id),
                "text": (when or "").strip() or "when that thinking ends"}

    def timed(db, entry, *, moving: bool) -> str:
        """A timed promise of hers gets its durable wake in the same transaction; a time without one is said."""
        from app.ai.mind import when as W
        from app.services import coordinator

        if entry.due_at is None or entry.owner != "columbina":
            return ""
        try:
            row = coordinator.promise_wake(db, evaluation, entry, reason="the promise's time changed" if moving else None)
        except ValueError as exc:
            return f" No wake could be kept for its time ({exc}): don't tell them you'll come back on your own."
        if evaluation is not None:
            evaluation.scheduled.append(str(row.id))
        zone = getattr(evaluation, "zone", None)
        moved = " (its earlier time is replaced)" if row.input.get("moved") else ""
        return (f" You'll be woken for it at {W.describe(entry.due_at, zone)} [wake:{str(row.id)[:8]}]{moved}; it "
                "counts kept once what you promised reaches them then.")

    @tool
    async def update_item(item: str, status: ItemStatus, reason: str) -> str:
        """Change something in your memory: complete, suspend, abandon or reactivate a goal; mark a
        promise fulfilled or cancelled; mark your intention done or dropped; a question of yours asked, or a
        question answered, stale (no longer matters) or dropped; a suspicion of yours supported or disproved
        (only by what the user said or what happened, never by thinking it again) or unresolved; a decision of
        yours superseded or withdrawn.

        Only when the user says so, or it's clearly done: never because time passed or the topic moved
        on. A promise of yours is fulfilled only by doing what you promised: mark it fulfilled in the reply that
        does it (it counts once those words reach them), never when you agree to it or they acknowledge it. One
        owed at a time can't be fulfilled before then; to change it, use change_promise. Completing a goal leaves
        its sub-goals open (you're told which). item: its id in brackets. One marked "may be out of date" that the
        user says still stands: set it to the status it has now.
        reason: a few words ("the user said we're done", "we changed our mind").
        """
        def do(db) -> str:
            entry = memory_manager.item(db, user_id, item)
            if (status == "fulfilled" and evaluation is not None and entry.kind == "commitment"
                    and entry.owner == "columbina" and entry.closed_at is None):
                # Her own promise is kept by what she delivers: noted on this evaluation, closed with her words in
                # the transaction that stores them (workers/chat.py), never before.
                why = memory_manager.unkeepable(entry)
                if why is not None:
                    raise MemoryError("not_due", why)
                if entry.id not in evaluation.fulfilled:
                    evaluation.fulfilled.append(entry.id)
                    evaluation.keeping[entry.id] = " ".join(reason.split())[:300] or "kept"
                return (f'Noted: [{memory_manager.ref(entry)}] "{entry.text}" counts as kept once what you say now '
                        "reaches them; if it doesn't, it stays open.")
            entry, children = memory_manager.resolve(db, user_id, item, status=status, reason=reason,
                                                     source=source, background=background, own_word=wake)
            note = ""
            if entry.closed_at is not None:
                # What she set to come back to about it has nothing left to come back to.
                from app.services import coordinator

                if coordinator.close_item_wakes(db, user_id, entry.id):
                    note = " What you'd scheduled to come back to about it is dropped."
            if children:
                note += " Still open under it: " + "; ".join(
                    f'[{memory_manager.ref(c)}] "{c.text}"' for c in children) + "."
            return f'Done: [{memory_manager.ref(entry)}] "{entry.text}" is {entry.status}.{note}'
        return await _run(do, user_id, "item_update")

    @tool
    async def note_commitment(what: str, who_owes: Literal["columbina", "user"] = "columbina",
                              at: str | None = None, in_seconds: int | None = None, when: str | None = None,
                              awaits: str | None = None, done_when: str | None = None,
                              relies_on: list[str] | None = None) -> str:
        """Keep a promise in your memory, once: something you agreed to do for the user ("I'll remind you about
        the dentist at 3"), or something the user promised you ("I'll show you the base tomorrow"). Not for your
        own plans to yourself: those are intentions. Not for remembering what the user told you ("remember that
        Leo hates cilantro"): that's kept on its own. A promise already kept that they clarify or move: change
        it with change_promise, never a second one.

        When it's owed, only what was said:
          at: an exact time ("YYYY-MM-DD HH:MM" their local time, "HH:MM" today, or with a UTC offset), or
          in_seconds: from now. A timed promise of yours gets its wake: you'll be woken then to keep it
          (no come_back_to needed).
          when: the condition, in words, when it's owed on something happening ("when the report finishes").
          awaits: when that's thinking of yours under way, its id ("work:1a2b3c4d"): you're woken when it ends.
        done_when: what counts as done ("the summary reaches them in chat"), if it isn't plain.
        relies_on: the ids in brackets of facts (fact:...) it only makes sense while they hold.
        """
        def do(db) -> str:
            due = when_of(at, in_seconds)
            entry = memory_manager.note_commitment(db, user_id, what, owner=who_owes, due_at=due, source=source,
                                                   done_when=done_when, trigger=condition(db, when, awaits))
            if evaluation is not None and who_owes == "columbina":
                evaluation.promises.append(entry.id)
            return (f'Kept: promise [{memory_manager.ref(entry)}] "{entry.text}".' + timed(db, entry, moving=False)
                    + relying(db, entry, relies_on))
        return await _run(do, user_id, "commitment")

    @tool
    async def change_promise(item: str, reason: str, what: str | None = None, at: str | None = None,
                             in_seconds: int | None = None, when: str | None = None,
                             done_when: str | None = None) -> str:
        """Change a promise that's still open, when they clarify or change what they want: the same promise,
        with only what you give changed; everything else (its time included) stays as it was. A new time moves
        its wake (only one time stays live). How it reaches them ("just message me") is a clarification of the
        same promise, not a new one, and not it being kept.
        item: its id in brackets. reason: what they said, in a few words.
        what: its new wording. at / in_seconds: its new time. when: its new condition. done_when: what counts as
        done now.
        """
        def do(db) -> str:
            due = when_of(at, in_seconds)
            trigger = {"kind": "event", "text": when} if (when or "").strip() else None
            entry, changed = memory_manager.revise_commitment(db, user_id, item, reason=reason, text_=what,
                                                              done_when=done_when, due_at=due, trigger=trigger,
                                                              source=source)
            if not changed:
                return f'Unchanged: [{memory_manager.ref(entry)}] "{entry.text}" already says that.'
            note = timed(db, entry, moving=True) if "when" in changed else ""
            if not note and entry.due_at is not None and entry.owner == "columbina":
                from app.ai.mind import when as W
                note = f" Still owed at {W.describe(entry.due_at, getattr(evaluation, 'zone', None))}."
            return (f'Changed ({", ".join(changed)}): [{memory_manager.ref(entry)}] "{entry.text}".{note}')
        return await _run(do, user_id, "commitment")

    @tool
    async def note_intention(what: str, relies_on: list[str] | None = None) -> str:
        """Keep something you mean to do yourself, not promised to anyone ("look into that cave
        later", "ask how the interview went"). It lapses on its own after a while if you don't.
        relies_on: the ids in brackets of facts (fact:...) it only makes sense while they hold."""
        def do(db) -> str:
            entry = memory_manager.note_intention(db, user_id, what, source=source)
            return f'Kept: intention [{memory_manager.ref(entry)}] "{entry.text}".' + relying(db, entry, relies_on)
        return await _run(do, user_id, "intention")

    @tool
    async def note_question(question: str, asked_by: Literal["user", "columbina"] = "user",
                            asking_now: bool = False) -> str:
        """Keep a question that is still open after this exchange: one of theirs you haven't answered (or only
        in part), or one of yours they haven't answered. It stays in your working memory until it's answered, stale
        or dropped (update_item). Only for a question worth coming back to, not every "?". One already in your
        working memory is already kept: don't note it again.
        asked_by: "user" for theirs, "columbina" for yours.
        asking_now: true when you ask it in what you say now (it then waits on their answer)."""
        def do(db) -> str:
            entry = memory_manager.add_item(db, user_id, kind="question", text_=question, owner=asked_by,
                                            epistemic="reported" if asked_by == "user" else "inferred",
                                            source=source)
            if asked_by == "columbina" and asking_now and evaluation is not None:
                # Asked in what she says now: it waits on their answer once those words are delivered, and isn't
                # handed back to her as a follow-up. One saved for later stays eligible for one (open_questions).
                entry.offered_at = entry.created_at or datetime.now(timezone.utc)
                evaluation.asking.append(entry.id)
            return f'Kept: open question [{memory_manager.ref(entry)}] "{entry.text}".'
        return await _run(do, user_id, "question")

    @tool
    async def switch_context(name: str, kind: Literal["activity", "project", "world", "general"] = "activity",
                             new: bool = False) -> str:
        """Turn your attention to another context, so what's kept from now on belongs there: a project
        ("Interview prep"), an activity, a game world, or "general" for ordinary talk. Turning away
        closes nothing: you can come back.

        new: true only when it replaces the one you're in for good, e.g. the user started a new
        Minecraft world (kind "world"): the old world's goals are put on hold and remembered as the
        past, not deleted, and its places and things are no longer true here.
        """
        def do(db) -> str:
            context, changed = memory_manager.switch(db, user_id, label=name, kind=kind, new=new)
            note = ""
            if changed:
                note = " Put on hold from before: " + "; ".join(f'"{c.text}" ({c.status})' for c in changed[:5]) + "."
            return f"Now in: {context.label}.{note}"
        return await _run(do, user_id, "context_switch")

    if background:
        return [note_intention, update_item]
    return [set_goal, update_item, note_commitment, change_promise, note_intention, note_question, switch_context]
