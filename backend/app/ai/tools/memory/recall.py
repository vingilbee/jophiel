"""Her read tools (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §9): search what she remembers, read one
memory with its evidence, or walk a context's timeline. Read-only wrappers over ``app.services.recall``
for this user only; no SQL. They share one budget per reply (failed calls and pages count), and what they
find joins what the reply was given (its receipt). With RECALL_HISTORY_ENABLED, ``recall_history`` rebuilds
how one thing changed from the sources behind what search finds (``services/recall_history.py``).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool

from app.ai.recall.contracts import TOOL_LIMIT, MemoryRef, RecallInvalid, RecallRequest, RecallResult, TimeRange
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

KindName = Literal["facts", "episodes", "events", "goals_and_promises"]
_KIND = {"facts": "fact", "episodes": "episode", "events": "event", "goals_and_promises": "item"}


def _moment(text: str, zone: ZoneInfo, *, end: bool) -> datetime:
    """An ISO date or time; without a zone it's the user's local time. A bare date is that local day's
    start (or, as an end, the start of the next day: ranges are [start, end))."""
    text = text.strip()
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text).date()
            if end:
                day += timedelta(days=1)
            return datetime.combine(day, time(0), zone)
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecallInvalid(f"not an ISO date or time: {text[:40]!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=zone)


def _range(time_from: str | None, time_to: str | None, basis: str, zone: ZoneInfo) -> TimeRange | None:
    if not time_from and not time_to:
        return None
    return TimeRange(basis=basis,  # type: ignore[arg-type]
                     start=_moment(time_from, zone, end=False) if time_from else None,
                     end=_moment(time_to, zone, end=True) if time_to else None)


def build_recall_tools(turn) -> list[BaseTool]:
    """The read tools for one reply. ``turn``: its ``recall_references.TurnRecall``."""
    from app.services import recall, recall_references

    zone = ZoneInfo(turn.zone) if turn.zone else ZoneInfo("UTC")

    def budgeted(run, show=None) -> str:
        if turn.tool_calls >= turn.tool_budget:
            turn.tool_statuses.append("budget_exhausted")
            return (f"Memory tools used up for this reply ({turn.tool_budget} calls). Answer from what you have, "
                    "and say what you couldn't check; this is not a sign nothing is recorded.")
        turn.tool_calls += 1
        try:
            with SessionLocal() as db:
                result = run(db)
                db.commit()
        except RecallInvalid as exc:
            turn.tool_statuses.append("invalid")
            return f"Not searched: {exc}. Fix the request and try again if it matters."
        except Exception:  # noqa: BLE001
            log.exception("recall: a memory tool failed")
            turn.tool_statuses.append("failed")
            return "Memory search FAILED: you couldn't check just now. Don't say nothing is recorded."
        if show is not None:
            return show(result)
        turn.tool_statuses.append(result.status)
        turn.supply(result.records)
        return recall.render(result, now=turn.now, zone=turn.zone)

    @tool
    async def search_memories(query: str, context: str | None = None, kinds: list[KindName] | None = None,
                              time_from: str | None = None, time_to: str | None = None,
                              time_basis: Literal["occurred", "scheduled", "recorded"] = "occurred",
                              historical: bool = False, page: str | None = None) -> str:
        """Search everything you remember, across every context (games, projects, ordinary talk), without
        changing what you're focused on. Use it when what you recalled for this reply isn't enough to
        answer a question about the past, or is ambiguous.

        query: what you're looking for, in plain words. context: a context's name ("Minecraft") or its id.
        kinds: limit to facts, episodes, events or goals_and_promises. time_from / time_to: ISO dates or
        times (a bare date is that whole day in the user's time zone; ranges include the start, not the
        end), worked out from the current time you know. time_basis: "occurred" (when it happened),
        "scheduled" (when it's due) or "recorded" (when it was written down). historical: true to include
        what no longer holds (finished goals, replaced facts). page: the token from a previous result.
        """
        def run(db):
            context_ids: tuple[uuid.UUID, ...] = ()
            context_query = None
            if context:
                try:
                    context_ids = (uuid.UUID(context.strip("[] ")),)
                except ValueError:
                    context_query = context
            req = RecallRequest(
                user_id=turn.user_id, query=query, purpose="explicit_search", now=turn.now, zone=turn.zone,
                conversation_id=turn.conversation_id, context_ids=context_ids, context_query=context_query,
                kinds=tuple(_KIND[k] for k in kinds) if kinds else recall_references._kinds(),
                time_range=_range(time_from, time_to, time_basis, zone),
                intent="historical" if historical else "relevance", limit=TOOL_LIMIT, page_token=page)
            return recall.search(db, req)
        return await asyncio.to_thread(budgeted, run)

    @tool
    async def read_memory(memory: str, include_evidence: bool = False) -> str:
        """Read one memory again by its id in brackets (e.g. "event:42"): its latest text, status, times, and
        with include_evidence where it came from. For checking a detail before you state it."""
        def run(db):
            return recall.read(db, turn.user_id, MemoryRef.parse(memory), include_evidence=include_evidence)
        return await asyncio.to_thread(budgeted, run)

    @tool
    async def read_timeline(context: str, time_from: str | None = None, time_to: str | None = None,
                            page: str | None = None) -> str:
        """What happened in one context, in the order it happened, with its goals: "what did we do in
        Minecraft?". context: its name or id; if several contexts have that name you get their names and
        ids to choose from, never a guess. time_from / time_to: as in search_memories (when it happened)."""
        def run(db):
            from sqlalchemy import select

            from app.db.models import MemoryContext

            try:
                context_id = uuid.UUID(context.strip("[] "))
            except ValueError:
                mine = list(db.scalars(select(MemoryContext).where(MemoryContext.user_id == turn.user_id)))
                found = recall._matching_contexts(mine, context)
                if not found:
                    return RecallResult("no_match", searched_kinds=("event",), errors=["no_such_context"],
                                        coverage={"context_query": "no context has that name"})
                if len(found) > 1:
                    raise RecallInvalid("several contexts have that name: " + "; ".join(
                        f"{c.label} [{c.id}] ({c.status}, last active {c.last_active_at:%Y-%m-%d})" for c in found[:6])
                        + ". Ask again with one id")
                context_id = found[0].id
            return recall.timeline(db, turn.user_id, context_id, time_range=_range(time_from, time_to, "occurred", zone),
                                   page_token=page)
        return await asyncio.to_thread(budgeted, run)

    tools = [search_memories, read_memory, read_timeline]
    from app.services import recall_history as histories

    if not histories.enabled():
        return tools

    def shown(run) -> str:
        try:
            run = histories.finish(run)  # the reading, after the session is closed
        except Exception:  # noqa: BLE001
            log.exception("recall: rebuilding a history failed")
            turn.tool_statuses.append("history_failed")
            return "Rebuilding the history FAILED: you couldn't check just now. Don't say nothing is recorded."
        turn.tool_statuses.append(f"history_{run.account.status}")
        turn.supply(run.records)
        turn.histories.append(run)
        return run.rendered

    @tool
    async def recall_history(question: str, about: str | None = None, context: str | None = None) -> str:
        """How something changed over time, rebuilt from the user's own words and what was recorded: what it
        was first, each change or correction in the order they told you, and what holds now, each with the
        exact words it rests on, plus those passages. Use it when the user asks about the past of something
        that changed ("what was it before?", "what order did I change it in?", "what did I first say?"), or
        when what you recall shows only the latest value of something that was updated and the earlier ones
        matter. Not needed for what holds now alone. Counts as one memory tool call.

        question: the user's question, in their words. about: the thing and which part of it, in a few words
        ("the book club's meeting time"). context: only when the user limited it to one context (a game
        world, a project): its name or id; history from elsewhere is still shown, marked outside it.
        """
        def run(db):
            context_ids: tuple[uuid.UUID, ...] = ()
            context_query = None
            if context:
                try:
                    context_ids = (uuid.UUID(context.strip("[] ")),)
                except ValueError:
                    context_query = context
            return histories.collect(db, user_id=turn.user_id, question=question, about=about,
                                        context_ids=context_ids, context_query=context_query, now=turn.now,
                                        zone=turn.zone, conversation_id=turn.conversation_id)
        return await asyncio.to_thread(budgeted, run, shown)

    return [*tools, recall_history]
