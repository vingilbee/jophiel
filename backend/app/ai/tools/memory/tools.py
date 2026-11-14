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
                     "done", "dropped", "answered", "supported", "disproved", "unresolved", "superseded",
                     "withdrawn"]


async def _run(fn: Callable[..., str], **kwargs) -> str:
    def call() -> str:
        with SessionLocal() as db:
            try:
                result = fn(db, **kwargs)
                db.commit()
                return result
            except MemoryError as exc:
                db.rollback()
                return f"Not done: {exc.message}."
    return await asyncio.to_thread(call)


def build_memory_tools(user_id: uuid.UUID, *, message_id: uuid.UUID | None = None,
                       background: bool = False, wake: bool = False) -> list[BaseTool]:
    """Memory tools for one evaluation. ``message_id``: the user's message being answered (the evidence
    for what these keep). ``background``: a game-event turn: only her own intentions. ``wake``: her mind woke
    without a user message: a promise of hers she keeps in this evaluation can be marked fulfilled."""
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
        return await _run(do)

    @tool
    async def update_item(item: str, status: ItemStatus, reason: str) -> str:
        """Change something in your memory: complete, suspend, abandon or reactivate a goal; mark a
        promise fulfilled or cancelled; mark your intention done or dropped; a question answered; a
        suspicion of yours supported or disproved (only by what the user said or what happened, never
        by thinking it again) or unresolved; a decision of yours superseded or withdrawn.

        Only when the user says so, or it's clearly done: never because time passed or the topic moved
        on. Completing a goal leaves its sub-goals open (you're told which). item: its id in brackets.
        One marked "may be out of date" that the user says still stands: set it to the status it has now.
        reason: a few words ("the user said we're done", "we changed our mind").
        """
        def do(db) -> str:
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
        return await _run(do)

    @tool
    async def note_commitment(what: str, who_owes: Literal["columbina", "user"] = "columbina",
                              due_in_minutes: int | None = None, relies_on: list[str] | None = None) -> str:
        """Keep a promise in your memory: something you told the user you'd do or tell them later
        ("I'll remind you about the dentist"), or something the user promised you ("I'll show you the
        base tomorrow"). Not for your own plans to yourself: those are intentions. Not for remembering
        what the user told you ("remember that Leo hates cilantro"): that's kept on its own.
        due_in_minutes: only when a time was said.
        relies_on: the ids in brackets of facts (fact:...) it only makes sense while they hold.
        """
        def do(db) -> str:
            due = (datetime.now(timezone.utc) + timedelta(minutes=due_in_minutes)) if due_in_minutes else None
            entry = memory_manager.note_commitment(db, user_id, what, owner=who_owes, due_at=due, source=source)
            return f'Kept: promise [{memory_manager.ref(entry)}] "{entry.text}".' + relying(db, entry, relies_on)
        return await _run(do)

    @tool
    async def note_intention(what: str, relies_on: list[str] | None = None) -> str:
        """Keep something you mean to do yourself, not promised to anyone ("look into that cave
        later", "ask how the interview went"). It lapses on its own after a while if you don't.
        relies_on: the ids in brackets of facts (fact:...) it only makes sense while they hold."""
        def do(db) -> str:
            entry = memory_manager.note_intention(db, user_id, what, source=source)
            return f'Kept: intention [{memory_manager.ref(entry)}] "{entry.text}".' + relying(db, entry, relies_on)
        return await _run(do)

    @tool
    async def note_question(question: str, asked_by: Literal["user", "columbina"] = "user") -> str:
        """Keep a question that is still open after this exchange: one of theirs you haven't answered (or only
        in part), or one of yours they haven't answered. It stays in your working memory until you mark it
        answered or dropped (update_item). Only for a question worth coming back to, not every "?".
        asked_by: "user" for theirs, "columbina" for yours."""
        def do(db) -> str:
            entry = memory_manager.add_item(db, user_id, kind="question", text_=question, owner=asked_by,
                                            epistemic="reported" if asked_by == "user" else "inferred",
                                            source=source)
            return f'Kept: open question [{memory_manager.ref(entry)}] "{entry.text}".'
        return await _run(do)

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
        return await _run(do)

    if background:
        return [note_intention, update_item]
    return [set_goal, update_item, note_commitment, note_intention, note_question, switch_context]
