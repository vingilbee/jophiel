"""The mind's tools for one evaluation. Each carries out a choice she made and returns what actually happened, in
plain words she can act on; refusals are execution facts (nothing to stop, too far ahead), never a judgment of
whether she should. What she did is also written to the evaluation's record (``services/mind_turn.Evaluation``).
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

from langchain_core.tools import BaseTool, tool

from app.db.session import SessionLocal

MAX_MINUTES = 60 * 24 * 60


async def _run(fn: Callable[..., str]) -> str:
    def call() -> str:
        with SessionLocal() as db:
            try:
                result = fn(db)
                db.commit()
                return result
            except ValueError as exc:
                db.rollback()
                return f"Not done: {exc}."
    return await asyncio.to_thread(call)


def build_mind_tools(ev) -> list[BaseTool]:
    """``ev``: this evaluation (``mind_turn.Evaluation``). Tools that need a conversation are left out without one;
    ``decide_proposal`` is there only when a proposal woke her."""
    from app.services import coordinator, memory_manager

    @tool(return_direct=True)
    async def stay_silent(reason: str) -> str:
        """Say nothing now. Call this instead of writing a reply when silence is what you choose: it needs no
        answer, they're mid-thought, you have nothing worth adding, or what woke you isn't worth saying. Nothing
        you noted or scheduled is lost. reason: a few words, for yourself (never shown to them)."""
        ev.silent = " ".join((reason or "").split())[:200] or "chose silence"
        return "Silent."

    tools: list[BaseTool] = [stay_silent]
    if ev.conversation_id is None:
        return tools

    @tool
    async def think_it_over(about: str, look_up_memory_first: bool = False) -> str:
        """Start thinking something through in the background, when it needs more than this moment: it runs
        without holding this conversation, and you're woken with your conclusion when it ends (then you decide
        whether it's worth saying). Until then you have no conclusion: answer what you can stand behind now, say
        you're thinking about the rest, or stay silent; never present a conclusion you haven't reached.

        about: what to think over, in your own words, complete enough to think from alone.
        look_up_memory_first: true when it turns on what you remember beyond what you have here.
        """
        words = " ".join((about or "").split())[:600]
        if not words:
            return "Not done: say what to think over."

        def start() -> str:
            pending = coordinator.schedule(
                user_id=ev.user_id, conversation_id=ev.conversation_id, request_id=ev.request_id,
                trigger_message_id=ev.trigger_message_id, with_recall=bool(look_up_memory_first),
                work_input={"message": ev.message, "think_about": words, "zone": ev.zone, "channel": ev.channel,
                            "user_name": ev.user_name,
                            "cached_memory": ev.recall_turn.rendered if ev.recall_turn is not None else None,
                            "evidence_versions": {r["ref"]: f"{r['fingerprint']}:{r['applicability']}:"
                                                            f"{int(r['corrected'])}" for r in ev.memory_refs}},
                input_versions={})
            if pending is None:
                return ("Not started: background thinking couldn't be started just now. Nothing is being thought "
                        "over: don't say it is.")
            ev.pending = pending
            mind = next((p for p in pending if p.kind == "mind"), pending[-1])
            return (f"Started [work:{mind.work_id[:8]}] ({mind.status}). You'll be woken with the conclusion; a new "
                    "message from them before then makes it moot.")
        return await asyncio.to_thread(start)

    @tool
    async def stop_thinking(work: str, reason: str) -> str:
        """Stop background thinking you started that no longer matters. work: its id in brackets
        ("work:1a2b3c4d"). reason: a few words."""
        def do(db) -> str:
            stopped = coordinator.cancel_thinking(db, ev.user_id, work, reason=reason)
            return "Stopped." if stopped else "Not done: no thinking under way with that id."
        return await _run(do)

    @tool
    async def come_back_to(what: str, in_minutes: int, item: str | None = None) -> str:
        """Choose to come back to something later without waiting for them to write: you'll be woken then, with
        the situation as it is by then, and decide what it calls for (say something, act, put it off again, or
        let it go). Use it for whatever you'd otherwise lose: checking in after a while, keeping a promise at a
        time, seeing how something went, picking a thought back up. This ends nothing now and holds nothing open.

        what: what to come back to and why, in your own words, enough to pick it up cold.
        in_minutes: how long from now (when is your judgment; at most 60 days).
        item: the id in brackets of the promise, intention or question in your working memory it's about, if any:
        closing that item drops this, and scheduling again for the same item moves it.
        """
        def do(db) -> str:
            if in_minutes is None or in_minutes < 0 or in_minutes > MAX_MINUTES:
                raise ValueError("in_minutes must be between 0 and 86400 (60 days)")
            entry = memory_manager.item(db, ev.user_id, item) if item else None
            if entry is not None and entry.closed_at is not None:
                raise ValueError(f"that {entry.kind} is already {entry.status}")
            row = coordinator.schedule_wake(
                db, user_id=ev.user_id, conversation_id=ev.conversation_id, about=what,
                due_at=datetime.now(timezone.utc) + timedelta(minutes=in_minutes),
                item_id=entry.id if entry is not None else None, origin=f"turn:{ev.request_id}", zone=ev.zone,
                channel=ev.channel)
            ev.scheduled.append(str(row.id))
            moved = " (it replaces the time you had set for that item)" if row.input.get("moved") else ""
            return f"Scheduled [wake:{str(row.id)[:8]}]: you'll be woken in {in_minutes} min{moved}."
        try:
            result = await _run(do)
        except memory_manager.MemoryError as exc:
            return f"Not done: {exc.message}."
        coordinator._wake()
        return result

    @tool
    async def drop_come_back(wake: str, reason: str) -> str:
        """Drop something you had scheduled to come back to. wake: its id in brackets ("wake:1a2b3c4d")."""
        def do(db) -> str:
            row = coordinator.cancel_wake(db, ev.user_id, wake, reason=reason)
            return "Dropped." if row is not None else "Not done: nothing scheduled with that id."
        return await _run(do)

    tools += [think_it_over, stop_thinking, come_back_to, drop_come_back]

    extra = ((ev.wake or {}).get("input") or {}).get("extra") or {}
    if ev.reason == "proposal" and extra.get("trial"):
        options = list(extra.get("options") or [])

        @tool
        async def decide_proposal(choice: Literal["accept", "decline", "defer"], reason: str,
                                  option: str | None = None, come_back_in_minutes: int | None = None,
                                  when_they_write_again: bool = False) -> str:
            """Decide the proposal that woke you. It is yours to take up, turn down or put off; nobody asked the
            user, and nothing happens unless you accept.

            choice: accept (run one option now: your hands do one thing at a time, so it takes the place of what
            they're doing), decline (drop it), defer (not now).
            option: for accept, which option (its key, shown in brackets).
            reason: why, in a sentence (kept with the proposal).
            For defer, when to look again: come_back_in_minutes, or when_they_write_again; with neither, it comes
            back when what it competes with is finished.
            """
            from app.services import learning_trials

            decision_id = str(ev.capture.attempt_id) if ev.capture is not None else None
            why = " ".join((reason or "").split())[:300]

            def do(db) -> str:
                if choice == "accept":
                    if option not in options:
                        raise ValueError(f"choose one of the options: {', '.join(options)}")
                    key = f"trial:{extra['trial']}:{extra['revision']}:{option}"
                    return learning_trials.on_pursue(db, ev.user_id, key, reason=why, decision_id=decision_id)
                key = f"trial:{extra['trial']}:{extra['revision']}:{options[0] if options else 'a'}"
                if choice == "decline":
                    return learning_trials.on_decline(db, ev.user_id, key, reason=why, decision_id=decision_id)
                condition = None
                if come_back_in_minutes is not None and 0 <= come_back_in_minutes <= MAX_MINUTES:
                    at = datetime.now(timezone.utc) + timedelta(minutes=come_back_in_minutes)
                    condition = {"kind": "time_reached", "at": at.isoformat(), "by": "mind"}
                elif when_they_write_again:
                    condition = {"kind": "user_returns", "by": "mind"}
                return learning_trials.on_defer(db, ev.user_id, key, reason=why, decision_id=decision_id,
                                                condition=condition)
            result = await _run(do)
            if not result.startswith("Not done") and not result.startswith("not carried out"):
                ev.proposal_decision = choice
            return result

        tools.append(decide_proposal)

    if ev.live_trial:
        @tool
        async def experiment(action: Literal["pause", "resume", "stop"], reason: str) -> str:
            """Pause, resume or stop the experiment you chose to run in your game (shown in your game summary).
            reason: a few words."""
            from app.services import learning_trials

            def do(db) -> str:
                trial_id = _live_trial(ev.user_id, db)
                if trial_id is None:
                    raise ValueError("no experiment is under way")
                return learning_trials.on_work(db, ev.user_id, f"trial:{trial_id}",
                                               {"stop": "cancel"}.get(action, action),
                                               reason=" ".join((reason or "").split())[:200] or action)
            return await _run(do)

        tools.append(experiment)
    return tools


def _live_trial(user_id: uuid.UUID, db) -> uuid.UUID | None:
    """A bounded experiment of hers that is under way (selected, preparing, executing or paused)."""
    from sqlalchemy import select

    from app.db.models import LearningTrial

    return db.scalar(select(LearningTrial.id).where(
        LearningTrial.user_id == user_id,
        LearningTrial.status.in_(("selected", "preparing", "executing", "paused"))).limit(1))
