"""Source-backed history for one question (docs/COLUMBINA_SOURCE_BACKED_RECALL.md): what her
``recall_history`` tool runs.

1. **Start from ordinary search**: one historical search (what no longer holds included) for the
   question. Under an explicit scope, search is limited to it, as any tool search is.
2. **Expand** (``recall_expand``): follow the evidence links from those records to their sources and on to
   the other memories the sources touched, across contexts, within budgets.
3. **Read** (``app.ai.recall.history``): one structured call gets the question, the scope and the
   passages, and proposes the history; code keeps what it can trace and settles what depends on it.
4. **Show** both: the rebuilt history, labelled as a reading for this question, and the passages
   themselves. What to say with it is hers.
5. **Keep provenance** (``save``, with the reply's receipt): the question, the checked account
   (citations as spans), coverage, the reader's model and input digest, and one row per passage with its
   version. No source text is copied.

Read-only toward memory: nothing here completes, revises or creates a memory, and the account is never
stored as a fact. A failure at any step is said as a failure, never as "no history".
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.recall import history
from app.ai.recall.contracts import TOOL_LIMIT, RecallInvalid, RecallRequest, RecallResult
from app.ai.recall.history import Expansion, HistoryAccount, HistoryReader, ReaderAnswer
from app.core.config import get_settings
from app.db.models import RecallReconstruction, RecallReconstructionSource
from app.services import recall, recall_expand

log = logging.getLogger(__name__)

SCHEMA_VERSION = "recall-history-v1"
_TARGET = {"fact": "fact_id", "episode": "episode_id", "event": "event_id", "item": "item_id",
           "message": "message_id", "observation": "observation_id", "experience": "experience_id",
           "session_event": "session_event_id", "thought": "thought_id"}
_INT_TARGETS = ("event", "observation", "experience", "session_event")


def enabled() -> bool:
    settings = get_settings()
    return settings.recall_enabled and settings.recall_history_enabled


@dataclass
class HistoryRun:
    question: str
    about: str | None
    scope: dict  # {"context_ids": [...], "labels": [...]}, empty when none was asked
    search_status: str
    expansion: Expansion
    account: HistoryAccount
    rendered: str
    model: str | None = None
    reasoning_effort: str | None = None
    input_digest: str | None = None
    answer: ReaderAnswer | None = None
    records: list = field(default_factory=list)  # the memory records among the passages, for the receipt
    now: datetime | None = None
    zone: str = "UTC"


def _reader() -> Callable[[str], ReaderAnswer]:
    settings = get_settings()
    return HistoryReader(settings.recall_history_model, api_key=settings.openai_api_key,
                         reasoning_effort=settings.recall_history_reasoning_effort).read


def _scope(db: Session, user_id: uuid.UUID, context_ids: tuple[uuid.UUID, ...],
           context_query: str | None) -> tuple[set[uuid.UUID] | None, dict]:
    if not context_ids and not context_query:
        return None, {}
    from sqlalchemy import select

    from app.db.models import MemoryContext

    mine = list(db.scalars(select(MemoryContext).where(MemoryContext.user_id == user_id)))
    chosen = [c for c in mine if c.id in context_ids]
    if len(chosen) < len(context_ids):
        raise RecallInvalid("unknown context")
    if context_query:
        found = recall._matching_contexts(mine, context_query)
        if not found:
            raise RecallInvalid("no context has that name")
        chosen += [c for c in found if c not in chosen]
    return {c.id for c in chosen}, {"context_ids": [str(c.id) for c in chosen], "labels": [c.label for c in chosen]}


def build(db: Session, *, user_id: uuid.UUID, question: str, about: str | None = None,
          context_ids: tuple[uuid.UUID, ...] = (), context_query: str | None = None, now: datetime, zone: str,
          conversation_id: uuid.UUID | None = None, reader: Callable[[str], ReaderAnswer] | None = None) -> HistoryRun:
    """``collect`` then ``finish``: for callers that don't mind holding the session through the reading."""
    return finish(collect(db, user_id=user_id, question=question, about=about, context_ids=context_ids,
                          context_query=context_query, now=now, zone=zone, conversation_id=conversation_id),
                  reader=reader)


def collect(db: Session, *, user_id: uuid.UUID, question: str, about: str | None = None,
            context_ids: tuple[uuid.UUID, ...] = (), context_query: str | None = None, now: datetime, zone: str,
            conversation_id: uuid.UUID | None = None) -> HistoryRun:
    """The database half: search, then expand. Raises ``RecallInvalid`` for a request that can't run as
    asked (no question, an unknown context); every other failure is in the result."""
    settings = get_settings()
    question = " ".join((question or "").split())[: history.QUESTION_LIMIT]
    about = " ".join((about or "").split())[:300] or None
    if not question:
        raise RecallInvalid("say the question")
    scope_ids, scope = _scope(db, user_id, context_ids, context_query)
    req = RecallRequest(user_id=user_id, query=f"{about}. {question}" if about else question,
                        purpose="explicit_search", now=now, zone=zone, conversation_id=conversation_id,
                        context_ids=tuple(scope_ids or ()), intent="historical", limit=TOOL_LIMIT)
    found: RecallResult = recall.search(db, req)
    seeds = found.records if found.status in ("matched", "partial") else []
    expansion = recall_expand.expand(db, user_id, seeds, scope=scope_ids,
                                     budget=recall_expand.Budget.from_settings(settings))
    if found.status == "failed":
        expansion.gaps.append("the search to start from failed")
    elif found.status == "partial":
        expansion.gaps.append("some memory couldn't be searched")
    run = HistoryRun(question, about, scope, found.status, expansion, HistoryAccount("no_history"), "",
                     records=[p.record for p in expansion.passages if p.record is not None])
    run.now, run.zone = now, zone
    return run


def finish(run: HistoryRun, *, reader: Callable[[str], ReaderAnswer] | None = None) -> HistoryRun:
    """The reading half, with no database session: one call, the check, the rendering."""
    settings = get_settings()
    expansion = run.expansion
    if expansion.passages:
        text = history.reader_input(run.question, run.about, run.scope.get("labels"), expansion,
                                    now=run.now, zone=run.zone)
        run.model, run.reasoning_effort = settings.recall_history_model, settings.recall_history_reasoning_effort
        run.input_digest = hashlib.sha256(
            (history.PROMPT_VERSION + "\n" + history.INSTRUCTIONS + "\n" + text).encode()).hexdigest()
        run.answer = (reader or _reader())(text)
        if run.answer.raw is None:
            run.account = HistoryAccount("failed", error=run.answer.error or "no answer")
        else:
            try:
                run.account = history.check(run.answer.raw, expansion, scoped=bool(run.scope))
            except Exception as exc:  # noqa: BLE001 - a malformed answer is a failed reading
                log.warning("recall: checking a history answer failed: %s", exc)
                run.account = HistoryAccount("failed", error=f"unreadable answer ({type(exc).__name__})")
    elif run.search_status == "failed" or expansion.stop == "failed":
        run.account = HistoryAccount("failed", error="couldn't collect anything to read")
    run.rendered = history.render(run.account, expansion, zone=run.zone)
    account = run.account
    recall._log({"op": "history", "status": account.status, "search": run.search_status,
                 "seeds": len(expansion.seeds), "visited": expansion.visited, "passages": len(expansion.passages),
                 "stop": expansion.stop, "depth": expansion.depth_reached, "queries": expansion.queries,
                 "expand_ms": expansion.ms, "steps": len(account.steps), "dropped": len(account.dropped),
                 "current": account.current.state if account.current else None,
                 "read_ms": run.answer.latency_ms if run.answer else None,
                 "tokens": [run.answer.input_tokens, run.answer.output_tokens] if run.answer else None,
                 "error": account.error})
    return run


# --------------------------------------------------------------------------- #
# Provenance, with the reply's receipt
# --------------------------------------------------------------------------- #


def _cites(cites) -> list[dict]:
    return [{"passage": c.passage, "start": c.start, "end": c.end} for c in cites]


def account_json(account: HistoryAccount) -> dict:
    """The checked account with citations as spans into the passages: no copied source text."""
    current = account.current
    return {
        "subject": account.subject, "property": account.property, "same_thing": account.same_thing,
        "identity_note": account.identity_note, "reordered": account.reordered,
        "steps": [{"kind": s.kind, "before": s.before, "after": s.after, "support": s.support,
                   "listed_as": s.listed_as, "warnings": list(s.warnings), "cites": _cites(s.cites)}
                  for s in account.steps],
        "current": {"state": current.state, "value": current.value, "note": current.note,
                    "cites": _cites(current.cites)} if current else None,
        "conflicts": [{"note": note, "cites": _cites(group)} for note, group in account.conflicts],
        "gaps": account.gaps, "dropped": account.dropped, "error": account.error,
    }


def save(db: Session, *, receipt_id: int, user_id: uuid.UUID, run: HistoryRun) -> int:
    """One reconstruction and its passages, in the caller's transaction."""
    expansion = run.expansion
    answer = run.answer
    row = RecallReconstruction(
        receipt_id=receipt_id, user_id=user_id, schema_version=SCHEMA_VERSION, prompt_version=history.PROMPT_VERSION,
        status=run.account.status, question=run.question, about=run.about, scope=run.scope, model=run.model,
        reasoning_effort=run.reasoning_effort, input_digest=run.input_digest, account=account_json(run.account),
        coverage=json.loads(json.dumps({
            "search": run.search_status, "seeds": [str(r) for r in expansion.seeds], "visited": expansion.visited,
            "passages": len(expansion.passages), "depth": expansion.depth_reached, "stop": expansion.stop,
            "truncated": expansion.truncated, "gaps": expansion.gaps, "unavailable": expansion.unavailable,
            "expand_ms": expansion.ms, "queries": expansion.queries,
            "read_ms": answer.latency_ms if answer else None,
            "input_tokens": answer.input_tokens if answer else None,
            "output_tokens": answer.output_tokens if answer else None}, default=str)))
    db.add(row)
    db.flush()
    cited = run.account.cited_labels()
    for p in expansion.passages:
        key = int(p.node.id) if p.node.kind in _INT_TARGETS else uuid.UUID(p.node.id)
        db.add(RecallReconstructionSource(
            reconstruction_id=row.id, user_id=user_id, label=p.label, speaker=p.speaker, depth=p.depth,
            cited=p.label in cited, in_scope=p.in_scope, truncated=p.truncated, fingerprint=p.fingerprint,
            said_at=p.said_at, **{_TARGET[p.node.kind]: key}))
    db.flush()
    return row.id
