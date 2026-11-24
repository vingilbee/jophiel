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
                            "user_name": ev.user_name, **({"third_party": True} if ev.third_party else {}),
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

    waking = (ev.wake or {}).get("work_id") if (ev.wake or {}).get("kind") == "wake" else None

    @tool
    async def come_back_to(what: str, asked_by: Literal["user", "me"], in_seconds: int | None = None,
                           at: str | None = None, item: str | None = None, moves: str | None = None,
                           reason: str | None = None, their_urgency: str | None = None,
                           phone: str | None = None) -> str:
        """Choose to come back to something later without waiting for them to write: you'll be woken then, with
        the situation as it is by then, and decide what it calls for (say something, act, put it off again, or
        let it go). Use it for whatever you'd otherwise lose: a check-in they asked for, keeping a promise at a
        time, seeing how something went, picking a thought back up. This ends nothing now and holds nothing open.

        what: what to come back to and why, in your own words, enough to pick it up cold.
        asked_by: "user" when they asked you for it (a check-in or reminder they requested: a promise you agree to
        keep; it's kept as one in your working memory unless you give its item), "me" when it's your own idea
        (optional: you may let it go).
        When: exactly one of
          in_seconds: how long from now, in seconds (30 for thirty seconds; at most 60 days);
          at: an exact time: "YYYY-MM-DD HH:MM" in their local time, "HH:MM" for today, or with a UTC offset.
        The result says the exact time it was kept for: tell them that time, not a rounded one.
        item: the id in brackets of the promise, intention or question in your working memory it's about, if any:
        closing that item drops this.
        moves: to postpone or re-time something already scheduled (including the one that just woke you), its
        id in brackets ("wake:1a2b3c4d"): it stays the same intention, with its original request, and only one
        time stays live. reason: why you're moving it (required with moves).
        their_urgency: only what they said themselves about urgency or consequences, in their words.
        phone: when it's a follow-up with someone in a conversation on their phone (seeing how their audition went),
        that conversation's notification handle ("notif:1a2b3c4d"): who and where are kept, and when it comes due you
        reread it and can write to them there afresh if a reply button is still there. Never a draft: you decide
        then. Leave it out when it's about the user.
        """
        from app.ai.mind import when as W

        def do(db) -> str:
            now = datetime.now(timezone.utc)
            due = W.resolve(in_seconds=in_seconds, at=at, zone=ev.zone, now=now)
            moving = None
            if moves:
                moving = coordinator.find_wake(db, ev.user_id, moves, also=waking)
                if moving is None:
                    raise ValueError(f"nothing scheduled with the id {moves}")
                if not (reason or "").strip():
                    raise ValueError("say why you're moving it (reason)")
            entry = memory_manager.item(db, ev.user_id, item) if item else None
            if entry is not None and entry.closed_at is not None:
                raise ValueError(f"that {entry.kind} is already {entry.status}")
            kept = ""
            inherited_item = (moving.input or {}).get("item") if moving is not None else None
            if entry is None and inherited_item is None and asked_by == "user" and getattr(ev, "promises", None):
                # The promise she just noted in this evaluation is the one this keeps the time of: one promise,
                # one wake, never a second copy.
                found = db.get(memory_manager.WorkingItem, ev.promises[-1])
                if found is not None and found.closed_at is None:
                    entry = found
                    kept = f' It keeps the time of your promise [{memory_manager.ref(entry)}].'
            if entry is None and inherited_item is None and asked_by == "user":
                # What they asked for is a promise of hers: kept where promises live, so it can be fulfilled,
                # revised or cancelled like any other.
                try:
                    entry = memory_manager.note_commitment(
                        db, ev.user_id, what, owner="columbina", due_at=due,
                        source=memory_manager.Source("message", ev.trigger_message_id)
                        if ev.trigger_message_id and not ev.is_wake else None)
                    kept = f' Kept as your promise [{memory_manager.ref(entry)}].'
                except memory_manager.MemoryError as exc:
                    kept = f" (Not kept as a promise: {exc.message}.)"
            promise = entry
            if promise is None and inherited_item:
                promise = db.get(memory_manager.WorkingItem, uuid.UUID(inherited_item))
            if (promise is not None and promise.kind == "commitment" and promise.owner == "columbina"
                    and promise.closed_at is None and promise.due_at != due):
                # A promise's time is its wake's time: moving one moves the other. Words already on their way for
                # the earlier time don't keep it at the new one.
                promise.due_at = due
                memory_manager.drop_wait(promise, "its time moved")
                if promise.status == "needs_review":
                    promise.status, promise.status_reason = "open", None
            follow_up = None
            if phone:
                from app.services import phone_replies
                follow_up = phone_replies.follow_up_for(ev.user_id, phone)
                # Whether what she writes now may be shown when it comes due (kept to that conversation): only
                # when she wrote it on a turn kept to that same conversation.
                follow_up["set_on"] = ("phone" if getattr(ev, "phone_scope", None) == follow_up["conversation_id"]
                                       else "owner")
            row = coordinator.schedule_wake(
                db, user_id=ev.user_id, conversation_id=ev.conversation_id, about=what, due_at=due,
                item_id=entry.id if entry is not None else None, origin=f"turn:{ev.request_id}", zone=ev.zone,
                channel=ev.channel, channel_identity_id=ev.channel_identity_id, asked_by="user" if asked_by == "user" else "mind",
                origin_message=ev.trigger_message_id if not ev.is_wake else None,
                their_urgency=their_urgency, moves=moving, reason=reason, third_party=ev.third_party,
                phone_follow_up=follow_up)
            ev.scheduled.append(str(row.id))
            if moving is not None:
                ev.deferred.append({"from": str(moving.id), "to": str(row.id), "reason": reason})
            moved = (f" It replaces [wake:{str(moving.id)[:8]}] (same intention, revision "
                     f"{len(row.input.get('revisions') or [])})." if moving is not None else
                     " It replaces the time you had set for that item." if row.input.get("moved") else "")
            hint = ("" if moving is not None or not waking else
                    " This is a new, separate wake: to postpone the one that woke you instead, use moves.")
            return (f"Scheduled [wake:{str(row.id)[:8]}] for {W.describe(due, ev.zone)} "
                    f"({W.span((due - now).total_seconds())} from now).{moved}{kept}{hint}")
        try:
            result = await _run(do)
        except memory_manager.MemoryError as exc:
            return f"Not done: {exc.message}."
        coordinator._wake()
        return result

    @tool
    async def drop_come_back(wake: str, reason: str) -> str:
        """Drop something you had scheduled to come back to, or let go of the one that just woke you, with why.
        wake: its id in brackets ("wake:1a2b3c4d"). A promise it was about stays as it is: change that with
        update_item if it's no longer owed."""
        def do(db) -> str:
            row = coordinator.cancel_wake(db, ev.user_id, wake, reason=reason, also=waking)
            if row is None:
                return "Not done: nothing scheduled with that id."
            ev.dropped.append({"wake": str(row.id), "reason": reason})
            item = (row.input or {}).get("item")
            return "Dropped." + (f" The item [{item[:8]}] it was about is unchanged." if item else "")
        return await _run(do)

    if getattr(ev, "phone_scope", None) is not None:
        # Kept to one phone conversation: no background thinking (it would read the user's memory and come back
        # beside someone else's words); coming back to them later stays open.
        tools += [come_back_to, drop_come_back]
        return tools
    tools += [think_it_over, stop_thinking, come_back_to, drop_come_back]

    from app.services import follow_through

    if follow_through.of(ev) is not None:
        @tool
        async def settle_attempt(attempt: str, outcome: Literal["achieved", "not_achieved", "unresolved"],
                                 evidence: str) -> str:
            """Say how an attempt of yours for this promise turned out (its id in brackets under FOLLOWING THROUGH),
            only once a fresh report after the attempt is there (the device taking the command is not the result):
            achieved / not_achieved when it shows that; unresolved when it can't tell. No fresh report yet: don't
            settle, leave it pending; its check wakes you when there's something to read. The promise itself stays
            as it is. evidence: what the fresh report shows, in a few words."""
            return await asyncio.to_thread(follow_through.settle, ev, attempt, outcome, evidence)

        tools.append(settle_attempt)

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
