"""Shared recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §4-8): the one read service over what she
remembers, used by replies, hard thoughts and her read tools.

Four stores, each read by its own adapter: facts (``semantic_memories``), episodes
(``episodic_memories``), memory events (``memory_events``: what happened, in a context) and working
items (``working_items``: goals, promises, intentions, questions). A request becomes typed records plus
what the search covered; rendering is the caller's business (``render``).

How a record is found, in order of strength: it's one the exchange before referred to; it belongs to a
context the request names (by label or source: "our Minecraft game" finds the Minecraft world, whether
or not its events say "Minecraft"); the message names it or points at it (``reference``: "Leo" finds the
memories that name Leo, "my sister" the ones about their sister, without her name); its words; its meaning
(embeddings, when there are any: an event's comes from a background job, and recall works before it lands);
its time, for an explicit range. Facts and episodes keep the scoring the old lookup used
(``long_term_memory.retrieve_for_prompt`` is now a wrapper over this), so standing preferences still come
along unmatched.

The user's core: standing constraints (important preferences and constraints, the peanut allergy) ride along
with every reply and hard thought on slots of their own (``STANDING_SLOTS``), so ranking other records higher
can never crowd them out. It's read from the facts on every search, never kept as a copy. When more than one
memory fits what the message points at ("my sister" with two sisters), all of them come back and the result
says so: recall never picks one.

Read-only toward what's remembered: the only writes are access counts on facts and episodes (as before).
Reading a memory never makes it more certain. An adapter that fails makes the result ``partial``, all of
them ``failed``; neither is ever turned into ``no_match``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.observe.registry import SOURCE_LABELS
from app.ai.recall.contracts import (
    TIMELINE_LIMIT, Evidence, Kind, MemoryRecord, MemoryRef, RecallInvalid, RecallRequest, RecallResult, TimeRange,
    origin_label,
)
from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES, DesktopSession, DesktopSessionEvent, EpisodicMemory, GameExperience, MemoryContext,
    MemoryConflict, MemoryEvent, MemoryEventEmbedding, MemoryEvidence, Observation, SemanticMemory, WorkingItem,
)

log = logging.getLogger(__name__)

EMBED_TEXT_VERSION = "event-v1"
# Words that name a kind of context, not a particular one: "world" alone doesn't pick a world.
_GENERIC = frozenset({"world", "general", "activity", "project", "context", "life", "stuff", "thing", "things"})
# A context from one of these sources is also "the game".
_GAME_SOURCES = frozenset({"minecraft_pack"})
# Events that only restate a working item are dropped when the item itself is in the result.
_ITEM_ECHO = frozenset({"item_added", "item_changed"})
# Events that quote an item's wording at the time: history once a correction revises the item.
_ITEM_WORDING = frozenset({"item_added", "item_changed", "item_revised"})
_QUIET_EVENT_KINDS = frozenset({"item_added", "item_changed", "context_switched", "context_opened",
                                "source_online"})
MIN_EVENT_SLOTS = 2  # recent events facts can't push out, when any were found
STANDING_SLOTS = 3  # standing constraints carried on top of the limit (replies and thoughts, not searches)
REFERENCE_BONUS = 0.3  # the message names or points at it
_POSSESSIVE_RE = re.compile(r"\b(?:my|our)\s+([a-z]+)(?:['’]s|s['’])?(?:\s+([a-z]+))?", re.IGNORECASE)
_NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]+")
_WORD = re.compile(r"[A-Za-z]+")
KIND_LABEL = {"fact": "facts", "episode": "episodes", "event": "events", "item": "goals, promises and your own conclusions",
              "message": "what was said in your conversations"}


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _fingerprint(*parts) -> str:
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:16]


def embed(text: str) -> list[float] | None:
    """The query's embedding (None when unavailable). Separate so tests and outages can replace it."""
    from app.services.long_term_memory import _embedding

    return _embedding(text)


def event_embedding_text(label: str | None, text: str) -> str:
    return f"{label}: {text}" if label else text


# --------------------------------------------------------------------------- #
# Contexts
# --------------------------------------------------------------------------- #


def _words(text: str | None) -> set[str]:
    from app.services.long_term_memory import _content_words

    return _content_words(text or "")


def _base(word: str) -> str:
    """A word's base for reference matching: lower case, a plural's ``s`` off ("sisters" -> "sister")."""
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _pointers(query: str) -> set[str]:
    """What "my ..." points at, as bases: "my sister in Lisbon" -> {sister}, "my dog's name" -> {dog, name}."""
    from app.services.long_term_memory import _STOPWORDS

    found: set[str] = set()
    for match in _POSSESSIVE_RE.finditer(query or ""):
        found |= {_base(w) for w in match.groups() if w and len(w) > 2 and w.lower() not in _STOPWORDS}
    return found


def _names(text: str) -> set[str]:
    """The capitalised words of a memory (people, pets, places), as bases."""
    from app.services.long_term_memory import _STOPWORDS

    return {_base(w) for w in _NAME_RE.findall(text or "") if w.lower() not in _STOPWORDS and w.lower() != "user"}


def _refers(record: MemoryRecord, s: _Search) -> bool:
    """The message names this memory's person or thing ("Leo"), or points at what it's about ("my sister"),
    in its words or through the entities it's about ("their sister Nora" for a plan to visit Lisbon)."""
    content = " ".join([record.content or "", *record.about])
    bases = {_base(w) for w in _WORD.findall(content)}
    return bool((s.pointers & bases) or (s.query_bases & _names(content)))


def _context_names(context: MemoryContext) -> set[str]:
    names = _words(context.label) - _GENERIC
    if context.source:
        names |= _words(SOURCE_LABELS.get(context.source, context.source.replace("_", " "))) - {"pack"}
        if context.source in _GAME_SOURCES:
            names.add("game")
    if context.kind == "world":
        names.add("game")
    return names


def _matching_contexts(contexts: list[MemoryContext], query: str) -> list[MemoryContext]:
    wanted = _words(query)
    return [c for c in contexts if wanted & _context_names(c)] if wanted else []


# --------------------------------------------------------------------------- #
# The search
# --------------------------------------------------------------------------- #


@dataclass
class _Candidate:
    record: MemoryRecord
    vector: list[float] | None = None
    importance: float = 0.5
    confidence: float = 1.0
    echo_of: uuid.UUID | None = None  # an event that restates this item
    admitted: bool = False


class _Search:
    def __init__(self, db: Session, req: RecallRequest):
        self.db, self.req = db, req
        self.query_words = _words(req.query)
        self.query_bases = {_base(w) for w in self.query_words}
        self.pointers = _pointers(req.query)
        self.contexts = {c.id: c for c in db.scalars(select(MemoryContext).where(
            MemoryContext.user_id == req.user_id).order_by(MemoryContext.last_active_at.desc()).limit(500))}
        self.coverage: dict = {}
        self.errors: list[str] = []
        self._vector: list[float] | None = None
        self._vector_tried = False

    # -- query embedding, lazily: only when some candidate has a vector to compare with
    def query_vector(self) -> list[float] | None:
        if not self._vector_tried:
            self._vector_tried = True
            try:
                self._vector = embed(self.req.query) if self.req.query.strip() else None
            except Exception:  # noqa: BLE001 - an embedding outage never stops recall
                log.warning("recall: query embedding failed; ranking without meaning", exc_info=True)
                self._vector = None
            if self._vector is None:
                self.coverage["ranking"] = "without meaning (no query embedding)"
        return self._vector

    def label(self, context_id: uuid.UUID | None) -> tuple[str | None, MemoryContext | None]:
        context = self.contexts.get(context_id) if context_id else None
        return (context.label if context else None), context


def _named_contexts(req: RecallRequest, search: _Search) -> set[uuid.UUID]:
    """Contexts the request names: by id, by a context query, or by the words of the query itself."""
    named: set[uuid.UUID] = set()
    for context_id in req.context_ids:
        if context_id not in search.contexts:
            raise RecallInvalid("unknown context")
        named.add(context_id)
    if req.context_query:
        found = _matching_contexts(list(search.contexts.values()), req.context_query)
        if not found:
            search.coverage["context_query"] = "no context has that name"
        named |= {c.id for c in found}
    if not req.context_ids and not req.context_query:
        named |= {c.id for c in _matching_contexts(list(search.contexts.values()), req.query)}
    if len(named) > 1:
        search.coverage["contexts_matched"] = sorted(search.contexts[c].label for c in named)
    return named


def search(db: Session, req: RecallRequest) -> RecallResult:
    """Run one bounded search. Never raises for an adapter failure: that's ``partial`` / ``failed``.
    Raises ``RecallInvalid`` for a request that can't be run as asked."""
    started = time.monotonic()
    req = req.bounded()
    s = _Search(db, req)
    named = _named_contexts(req, s)
    # A context the caller asked for by name or id is a filter for events and items (facts and episodes
    # have no context recorded: they're searched anyway, and the coverage says so).
    strict = bool(req.context_ids or req.context_query) and req.purpose == "explicit_search"
    if strict:
        s.coverage["context_filter"] = ("events and goals limited to the named context; facts and episodes "
                                        "have no context recorded and were searched without it")
    direct: dict[MemoryRef, MemoryRecord] = {}
    for ref in req.prior_refs:
        record = _read_one(db, req.user_id, ref, s)
        if record is not None and record.forgotten:
            # History she was shown when asked; never carried on as memory (docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5).
            s.coverage.setdefault("references_set_aside", []).append(str(ref))
            continue
        if record is None:
            s.coverage.setdefault("references_gone", []).append(str(ref))
        else:
            direct[ref] = replace(record, matched=("direct_reference",))
    # Where the exchange before was: a weaker pull, for follow-ups that don't say where.
    anchors = {r.context_id for r in direct.values() if r.context_id}
    candidates: list[_Candidate] = []
    ran: list[Kind] = []
    adapters: dict[Kind, Callable[[], list[_Candidate]]] = {
        "event": lambda: _events(db, req, s, named | anchors),
        "item": lambda: _items(db, req, s, named | anchors),
        "fact": lambda: _facts(db, req, s),
        "episode": lambda: _episodes(db, req, s),
    }
    for kind in req.kinds:
        try:
            with db.begin_nested():
                candidates += adapters[kind]()
            ran.append(kind)
        except Exception:  # noqa: BLE001 - one store failing never hides the others
            log.exception("recall: %s adapter failed", kind)
            s.errors.append(f"{kind}_unavailable")
    if not ran:
        return RecallResult.failed("search_failed", req.kinds)

    ranked = _rank(req, s, candidates, named, anchors, direct, strict)
    chosen, more = _choose(req, s, ranked, direct)
    if req.purpose != "explicit_search":
        _count_access(db, chosen)
    status = ("partial" if s.errors else "matched") if chosen else ("partial" if s.errors else "no_match")
    scope = {"contexts": sorted(s.contexts[c].label for c in named) if named else "all",
             "intent": req.intent, "kinds": list(ran)}
    if req.time_range:
        tr = req.time_range
        scope["time"] = {"basis": tr.basis, "start": tr.start.isoformat() if tr.start else None,
                         "end": tr.end.isoformat() if tr.end else None}
    s.coverage["candidates"] = len(candidates)
    s.coverage["ms"] = round((time.monotonic() - started) * 1000)
    next_page = _page_token(req, (_offset(req) + len(chosen))) if more and req.purpose == "explicit_search" else None
    result = RecallResult(status, chosen, tuple(ran), scope, s.coverage, s.errors, next_page)
    _log({"op": "search", "purpose": req.purpose, "status": status, "returned": len(chosen),
          "by_kind": {k: sum(1 for r in chosen if r.ref.kind == k) for k in ran}, "named": len(named),
          "direct": len(direct), "errors": s.errors, "ms": s.coverage["ms"]})
    return result


# --------------------------------------------------------------------------- #
# Adapters: bounded candidates from each store
# --------------------------------------------------------------------------- #


def _event_vectors(db: Session, events: list[MemoryEvent], s: _Search) -> dict[int, list[float]]:
    if not events:
        return {}
    rows = db.scalars(select(MemoryEventEmbedding).where(
        MemoryEventEmbedding.event_id.in_([e.id for e in events]), MemoryEventEmbedding.status == "done"))
    by_id = {row.event_id: row for row in rows}
    vectors = {}
    for event in events:
        row = by_id.get(event.id)
        label, _ = s.label(event.context_id)
        if row and row.fingerprint == _fingerprint(EMBED_TEXT_VERSION, event_embedding_text(label, event.text)):
            vectors[event.id] = row.vector
    missing = len(events) - len(vectors)
    if missing:
        s.coverage["events_without_embedding"] = missing
    return vectors


def _event_record(event: MemoryEvent, s: _Search) -> MemoryRecord:
    label, context = s.label(event.context_id)
    closed = context is not None and context.status == "closed"
    return MemoryRecord(
        ref=MemoryRef("event", str(event.id)), subkind=event.kind, content=event.text, context_id=event.context_id,
        context_label=label, occurred_at=_utc(event.occurred_at), occurred_until=None, scheduled_at=None,
        recorded_at=_utc(event.recorded_at), updated_at=None, status="recorded", epistemic=event.epistemic,
        evidence=None, fingerprint=_fingerprint("event", event.id, event.text, event.context_id), matched=(),
        applicability="past", lifecycle="its context is closed" if closed else None,
        source_label=SOURCE_LABELS.get(context.source) if context is not None and context.source else None)


def _item_record(entry: WorkingItem, s: _Search) -> MemoryRecord:
    label, context = s.label(entry.context_id)
    closed_context = context is not None and context.status == "closed"
    lifecycle = None
    if entry.closed_at is not None:
        lifecycle = entry.status + (f": {entry.status_reason}" if entry.status_reason else "")
    elif closed_context:
        lifecycle = "its context is closed"
    current = entry.closed_at is None and not closed_context
    from app.services import memory_time

    span = memory_time.span(entry)
    if entry.kind == "plan" and entry.status == "needs_review":
        lifecycle = "its time has passed and nobody said whether it happened"
    return MemoryRecord(
        ref=MemoryRef("item", str(entry.id)), subkind=entry.kind,
        content=entry.text + (f" (done when: {entry.done_when})" if entry.done_when else ""),
        context_id=entry.context_id, context_label=label, occurred_at=None, occurred_until=None,
        # A plan's time is when it's meant to happen; its due time is only when to check on it.
        scheduled_at=_utc(entry.time_start) if entry.kind == "plan" else _utc(entry.due_at),
        recorded_at=_utc(entry.created_at), updated_at=_utc(entry.updated_at),
        status=entry.status, epistemic=entry.epistemic, evidence=None,
        fingerprint=_fingerprint("item", entry.id, entry.revision, entry.status, entry.text), matched=(),
        applicability="current" if current else "no_longer_current", lifecycle=lifecycle, owner=entry.owner,
        reason=entry.reason, time=span, user_words=entry.user_words)


def _fact_record(fact: SemanticMemory) -> MemoryRecord:
    from app.services import memory_time

    history = (fact.metadata_ or {}).get("history") or []
    corrected = next((h for h in reversed(history) if h.get("reason") == "correction"), None)
    note = f'corrected by the user; it said before: "{corrected["content"]}"' if corrected else None
    if fact.status == "active" and fact.historical:
        note = "; ".join(x for x in ("how things were, not how they are now", note) if x)
    applicability = ("past" if fact.historical else "current") if fact.status == "active" else "no_longer_current"
    return MemoryRecord(
        ref=MemoryRef("fact", str(fact.id)), subkind=fact.kind, content=fact.content, context_id=None,
        context_label=None, occurred_at=None, occurred_until=None, scheduled_at=None,
        recorded_at=_utc(fact.created_at), updated_at=_utc(fact.last_confirmed_at), status=fact.status,
        epistemic="reported", evidence=None,
        fingerprint=_fingerprint("fact", fact.id, fact.revision, fact.status, fact.content, fact.entity_id,
                                 fact.attribute, fact.value, fact.historical, fact.time_words, fact.time_start),
        matched=(), applicability=applicability,
        lifecycle=note if fact.status == "active" else "replaced by a later fact", time=memory_time.span(fact))


def _episode_record(episode: EpisodicMemory) -> MemoryRecord:
    from app.services import memory_time

    ctx = episode.context or {}
    source = ctx.get("source")
    return MemoryRecord(
        ref=MemoryRef("episode", str(episode.id)), subkind=str(source or "episode"), content=episode.summary,
        context_id=None, context_label=SOURCE_LABELS.get(ctx.get("game") or "") if ctx.get("game") else None,
        occurred_at=_utc(episode.event_at), occurred_until=None, scheduled_at=None,
        recorded_at=_utc(episode.created_at), updated_at=None, status=episode.status,
        epistemic="inferred" if source == "game" else "unknown", evidence=None,
        fingerprint=_fingerprint("episode", episode.id, episode.status, episode.summary), matched=(),
        applicability="past" if episode.status == "active" else "no_longer_current",
        lifecycle=None if episode.status == "active" else episode.status, time=memory_time.span(episode))


def _events(db: Session, req: RecallRequest, s: _Search, pulled: set[uuid.UUID]) -> list[_Candidate]:
    settings = get_settings()
    base = select(MemoryEvent).where(MemoryEvent.user_id == req.user_id)
    rows: dict[int, MemoryEvent] = {}
    recent = list(db.scalars(base.order_by(MemoryEvent.id.desc()).limit(settings.recall_event_candidates)))
    rows.update({e.id: e for e in recent})
    if len(recent) >= settings.recall_event_candidates:
        s.coverage["event_window"] = settings.recall_event_candidates
    if pulled:
        rows.update({e.id: e for e in db.scalars(base.where(MemoryEvent.context_id.in_(pulled))
                                                 .order_by(MemoryEvent.occurred_at.desc()).limit(200))})
    tr = req.time_range
    if tr and tr.basis in ("occurred", "recorded"):
        column = MemoryEvent.occurred_at if tr.basis == "occurred" else MemoryEvent.recorded_at
        ranged = base
        if tr.start:
            ranged = ranged.where(column >= tr.start)
        if tr.end:
            ranged = ranged.where(column < tr.end)
        rows.update({e.id: e for e in db.scalars(ranged.order_by(column.desc()).limit(200))})
    events = list(rows.values())
    vectors = _event_vectors(db, events, s)
    records = annotate(db, [_event_record(e, s) for e in events])
    return [_Candidate(record, vectors.get(e.id), echo_of=e.item_id if e.kind in _ITEM_ECHO else None)
            for e, record in zip(events, records)]


def _items(db: Session, req: RecallRequest, s: _Search, pulled: set[uuid.UUID]) -> list[_Candidate]:
    base = select(WorkingItem).where(WorkingItem.user_id == req.user_id)
    if req.intent == "current":
        base = base.where(WorkingItem.closed_at.is_(None))
    rows = {i.id: i for i in db.scalars(base.order_by(WorkingItem.updated_at.desc()).limit(200))}
    if pulled:
        rows.update({i.id: i for i in db.scalars(base.where(WorkingItem.context_id.in_(pulled)).limit(100))})
    records = annotate(db, [_item_record(entry, s) for entry in rows.values()
                            # Open items of the focused context are already in her working view, with their ids.
                            if not (req.skip_open_items_of and entry.context_id == req.skip_open_items_of
                                    and entry.closed_at is None)])
    return [_Candidate(record) for record in records]


def _facts(db: Session, req: RecallRequest, s: _Search) -> list[_Candidate]:
    now = req.now.astimezone(timezone.utc).replace(tzinfo=None)
    statuses = ("active", "superseded") if req.intent == "historical" else ("active",)
    rows = db.scalars(select(SemanticMemory).where(
        SemanticMemory.user_id == req.user_id, SemanticMemory.status.in_(statuses),
        or_(SemanticMemory.expires_at.is_(None), SemanticMemory.expires_at > now)))
    rows = list(rows)
    records = annotate(db, [_fact_record(f) for f in rows])
    return [_Candidate(r, f.embedding, f.importance, f.confidence) for r, f in zip(records, rows)]


def _episodes(db: Session, req: RecallRequest, s: _Search) -> list[_Candidate]:
    settings = get_settings()
    statuses = ("active", "superseded", "consolidated") if req.intent == "historical" else ("active",)
    query = select(EpisodicMemory).where(EpisodicMemory.user_id == req.user_id, EpisodicMemory.status.in_(statuses))
    tr = req.time_range
    if tr and tr.basis == "occurred":
        if tr.start:
            query = query.where(EpisodicMemory.event_at >= tr.start)
        if tr.end:
            query = query.where(EpisodicMemory.event_at < tr.end)
    rows = list(db.scalars(query.order_by(EpisodicMemory.event_at.desc()).limit(settings.recall_episode_candidates)))
    if len(rows) >= settings.recall_episode_candidates:
        s.coverage["episode_window"] = settings.recall_episode_candidates
    records = annotate(db, [_episode_record(e) for e in rows])
    return [_Candidate(r, e.embedding, e.importance, e.confidence) for r, e in zip(records, rows)]


# --------------------------------------------------------------------------- #
# Ranking and choosing
# --------------------------------------------------------------------------- #


def _rank(req: RecallRequest, s: _Search, candidates: list[_Candidate], named: set[uuid.UUID],
          anchors: set[uuid.UUID], direct: dict[MemoryRef, MemoryRecord], strict: bool = False) -> list[_Candidate]:
    from app.services.long_term_memory import MIN_SIMILARITY, MIN_WORD_OVERLAP, RECENCY_DAYS, _cosine

    tr = req.time_range
    words = s.query_words
    ranked = []
    for c in candidates:
        r = c.record
        if r.ref in direct:
            continue  # comes back as the direct reference itself
        if strict and r.ref.kind in ("event", "item") and r.context_id not in named:
            continue
        # An explicit time range filters; a record whose time is unknown can't be placed in it.
        if tr is not None:
            moment = {"occurred": r.occurred_at, "scheduled": r.scheduled_at, "recorded": r.recorded_at}[tr.basis]
            inside = tr.contains(moment)
            if inside is None:
                s.coverage.setdefault("unplaceable", {}).setdefault(r.ref.kind, 0)
                s.coverage["unplaceable"][r.ref.kind] += 1
                continue
            if not inside:
                continue
        matched: list[str] = []
        semantic = 0.0
        if c.vector is not None:
            vector = s.query_vector()
            semantic = max(0.0, _cosine(vector, c.vector)) if vector else 0.0
        if r.ref.kind in ("fact", "episode"):
            # The scoring the old lookup used, unchanged (long_term_memory.retrieve_for_prompt).
            lexical = len(words & _words(r.content)) / max(1, len(words))
            recency = 0.0
            if r.ref.kind == "episode" and r.occurred_at:
                age_days = max(0.0, (req.now - r.occurred_at).total_seconds() / 86400)
                recency = 0.5 ** (age_days / RECENCY_DAYS)
            score = semantic * 0.55 + lexical * 0.25 + c.importance * 0.1 + recency * 0.1 + c.confidence * 0.05
            if semantic >= MIN_SIMILARITY:
                matched.append("semantic")
            if words and lexical >= MIN_WORD_OVERLAP:
                matched.append("lexical")
            if _standing(c):
                matched.append("standing")
        else:
            lexical = len(words & (_words(r.content) | _words(r.context_label))) / max(1, len(words))
            moment = r.occurred_at or r.updated_at or r.recorded_at
            age_days = max(0.0, (req.now - moment).total_seconds() / 86400) if moment else 30.0
            recency = 0.5 ** (age_days / RECENCY_DAYS)
            in_named = r.context_id in named
            in_anchor = r.context_id in anchors
            score = (semantic * 0.55 + lexical * 0.25 + recency * 0.15 + (0.5 if in_named else 0.0)
                     + (0.3 if in_anchor and not in_named else 0.0)
                     + (0.1 if req.focus_context_id and r.context_id == req.focus_context_id else 0.0))
            if r.ref.kind == "event" and r.subkind in _QUIET_EVENT_KINDS:
                score -= 0.1
            if r.ref.kind == "item" and r.applicability == "current":
                score += 0.05
            if in_named or in_anchor:
                matched.append("named_context")
            if semantic >= MIN_SIMILARITY:
                matched.append("semantic")
            if words and lexical >= MIN_WORD_OVERLAP:
                matched.append("lexical")
        if _refers(r, s):
            matched.append("reference")
            score += REFERENCE_BONUS
        if tr is not None and not matched and not words:
            matched.append("temporal")  # a range with nothing else to match: everything in it
        if not matched:
            continue
        c.record = replace(r, matched=tuple(matched), score=round(score, 4))
        c.admitted = True
        ranked.append(c)
    # Historical by default for a relevance search: what no longer holds ranks below what does.
    if req.intent == "current":
        ranked = [c for c in ranked if c.record.applicability != "no_longer_current"]
    ranked.sort(key=lambda c: (c.record.score - (0.05 if c.record.applicability == "no_longer_current" else 0)),
                reverse=True)
    return ranked


def _standing(c: _Candidate) -> bool:
    """A standing constraint: an important preference or constraint (an allergy), which applies whatever the
    message is about."""
    from app.services.long_term_memory import ALWAYS_IMPORTANCE, ALWAYS_KINDS

    return c.record.ref.kind == "fact" and c.record.subkind in ALWAYS_KINDS and c.importance >= ALWAYS_IMPORTANCE


def _ambiguous(s: _Search, records: list[MemoryRecord]) -> bool:
    """More than one memory fits what "my ..." points at, and they name different people or things: two facts
    about "my sister", one naming Nora and one Ada; or a name or relation the message uses fits more than one
    known entity (two Noras, two sisters). Recall returns both and says so; it never picks one."""
    from app.services import memory_entities

    which = memory_entities.ambiguous(s.db, s.req.user_id, s.query_bases & _names(s.req.query), s.pointers)
    if which:
        s.coverage["ambiguous_about"] = which
        return True
    for pointer in s.pointers:
        named = [_names(r.content) for r in records
                 if r.ref.kind == "fact" and pointer in {_base(w) for w in _WORD.findall(r.content or "")}]
        named = [n for n in named if n]
        if any(not (a & b) for i, a in enumerate(named) for b in named[i + 1:]):
            return True
    return False


def _rendered_size(record: MemoryRecord) -> int:
    extra = sum(len(a) + 2 for a in record.about) + len(record.user_words or "")
    if record.time is not None:
        extra += len(record.time.words) + 110
    return len(record.content) + len(record.context_label or "") + 140 + extra


def _choose(req: RecallRequest, s: _Search, ranked: list[_Candidate],
            direct: dict[MemoryRef, MemoryRecord]) -> tuple[list[MemoryRecord], bool]:
    from app.services.long_term_memory import MAX_EPISODES

    chosen: list[MemoryRecord] = list(direct.values())
    items_in = {uuid.UUID(c.record.ref.id) for c in ranked if c.record.ref.kind == "item"}
    items_in |= {uuid.UUID(r.ref.id) for r in chosen if r.ref.kind == "item"}
    seen_text = {r.content.strip().lower() for r in chosen}
    pool, standing = [], []
    for c in ranked:
        # The same thing said twice: an event restating an item that's here, or identical text.
        if c.echo_of is not None and c.echo_of in items_in:
            continue
        text = c.record.content.strip().lower()
        if text in seen_text:
            continue
        seen_text.add(text)
        if req.purpose != "explicit_search" and _standing(c) and len(standing) < STANDING_SLOTS:
            standing.append(c.record)  # their own slots: ranking can't crowd them out
        else:
            pool.append(c.record)
    if _ambiguous(s, chosen + pool):
        s.coverage["references_ambiguous"] = True
    offset = _offset(req)
    pool = pool[offset:] if offset else pool
    room = req.limit - len(chosen)
    picked: list[MemoryRecord] = []
    episodes = 0
    for record in pool:
        if len(picked) >= room:
            break
        if record.ref.kind == "episode" and req.purpose != "explicit_search":
            if episodes >= MAX_EPISODES:
                continue
            episodes += 1
        picked.append(record)
    # Recent events facts can't crowd out: up to MIN_EVENT_SLOTS of them, replacing the weakest others.
    events = [r for r in pool if r.ref.kind == "event"]
    want = min(MIN_EVENT_SLOTS, len(events))
    have = sum(1 for r in picked if r.ref.kind == "event")
    for event in [e for e in events if e not in picked][: max(0, want - have)]:
        weakest = next((r for r in reversed(picked) if r.ref.kind != "event"), None)
        if weakest is None:
            break
        picked[picked.index(weakest)] = event
    picked.sort(key=lambda r: r.score, reverse=True)
    if standing:
        s.coverage["standing"] = len(standing)
    kept = chosen + standing
    result = kept + picked
    # The content budget: visible when it cuts. What the exchange before used and the standing constraints are
    # never cut.
    used = sum(_rendered_size(r) for r in kept)
    for record in picked:
        used += _rendered_size(record)
        if used > req.budget_chars and kept:
            s.coverage["truncated"] = "content budget"
            break
        kept.append(record)
    more = len(pool) > len(picked) or len(kept) < len(result)
    if more and len(pool) > len(picked):
        s.coverage["more_matches"] = len(pool) - len(picked)
    return _with_evidence(s.db, kept), more


def _count_access(db: Session, records: list[MemoryRecord]) -> None:
    now = datetime.now(timezone.utc)
    facts = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "fact"]
    episodes = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "episode"]
    for model, ids, stamp in ((SemanticMemory, facts, now.replace(tzinfo=None)), (EpisodicMemory, episodes, now)):
        for row in (db.scalars(select(model).where(model.id.in_(ids))) if ids else []):
            row.last_accessed_at = stamp
            row.access_count = (row.access_count or 0) + 1


# --------------------------------------------------------------------------- #
# Evidence and lifecycle (only for what's returned)
# --------------------------------------------------------------------------- #


def _unresolved(db: Session, records: list[MemoryRecord]) -> list[MemoryRecord]:
    """A current fact a message disagreed with, not settled which holds (services/memory_conflicts.py): still
    what memory says, marked unresolved with the user's words, so no reader takes it as settled."""
    facts = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "fact" and r.applicability == "current"]
    if not facts:
        return records
    from app.services import memory_conflicts

    by_fact: dict[uuid.UUID, list[MemoryConflict]] = {}
    for row in db.scalars(select(MemoryConflict).where(MemoryConflict.fact_id.in_(facts),
                                                       MemoryConflict.status == "open")
                          .order_by(MemoryConflict.created_at)):
        by_fact.setdefault(row.fact_id, []).append(row)
    if not by_fact:
        return records
    out = []
    for record in records:
        note = memory_conflicts.note(by_fact.get(uuid.UUID(record.ref.id), [])) if record.ref.kind == "fact" else None
        if note:
            record = replace(record, lifecycle="; ".join(x for x in (record.lifecycle, note) if x))
        out.append(record)
    return out


def _held(db: Session, records: list[MemoryRecord]) -> list[MemoryRecord]:
    """An open item a fact it relies on changed under (services/memory_dependencies.py): being rechecked, or
    may be out of date. Said on every read path, so no reader takes it as checked."""
    items = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "item"]
    if not items:
        return records
    from app.services import memory_dependencies

    held = memory_dependencies.holds(db, item_ids=items)
    if not held:
        return records
    out = []
    for record in records:
        hold = held.get(uuid.UUID(record.ref.id)) if record.ref.kind == "item" else None
        if hold:
            record = replace(record, lifecycle="; ".join(x for x in (record.lifecycle, memory_dependencies.HOLD_TEXT[hold]) if x))
        out.append(record)
    return out


def _about(db: Session, records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Who or what each record is about or involves (services/memory_entities.py), in words and by id."""
    ids = {kind: [uuid.UUID(r.ref.id) for r in records if r.ref.kind == kind] for kind in ("fact", "item", "episode")}
    if not records or not any(ids.values()):
        return records
    from app.services import memory_entities

    found = {}
    # Records carry no owner: each row's own owner, so an entity is only ever another record's of the same user.
    for user_id in _owners(db, ids):
        found.update(memory_entities.about(db, user_id, facts=ids["fact"], items=ids["item"],
                                           episodes=ids["episode"]))
    out = []
    for record in records:
        entities = found.get(uuid.UUID(record.ref.id)) if record.ref.kind in ids else None
        if entities:
            record = replace(record, about=tuple(memory_entities.label(e) for e in entities),
                             entity_ids=tuple(str(e.id) for e in entities))
        out.append(record)
    return out


def _owners(db: Session, ids: dict[str, list[uuid.UUID]]) -> set[uuid.UUID]:
    owners: set[uuid.UUID] = set()
    for kind, model in (("fact", SemanticMemory), ("item", WorkingItem), ("episode", EpisodicMemory)):
        if ids[kind]:
            owners |= set(db.scalars(select(model.user_id).where(model.id.in_(ids[kind])).distinct()))
    return owners


def annotate(db: Session, records: list[MemoryRecord]) -> list[MemoryRecord]:
    """What a correction changed, the same on every read path (search, ``read``, ``timeline``, the reread of
    an answer's references, history expansion): an event quoting an item's wording from before a correction
    revised it is history, no longer what the item says (docs/COLUMBINA_MEMORY_CORRECTIONS.md); an item a
    correction revised says so; a fact a message disagreed with says it's unresolved. Never applied twice: it
    reads only what's stored."""
    records = _held(db, _about(db, _unresolved(db, records)))
    wording = [int(r.ref.id) for r in records if r.ref.kind == "event" and r.subkind in _ITEM_WORDING]
    items = {uuid.UUID(r.ref.id) for r in records if r.ref.kind == "item"}
    about = dict(db.execute(select(MemoryEvent.id, MemoryEvent.item_id).where(
        MemoryEvent.id.in_(wording), MemoryEvent.item_id.is_not(None))).all()) if wording else {}
    targets = items | set(about.values())
    if not targets:
        return records
    latest = dict(db.execute(select(MemoryEvidence.item_id, func.max(MemoryEvidence.created_at)).where(
        MemoryEvidence.item_id.in_(targets), MemoryEvidence.role == "correction")
        .group_by(MemoryEvidence.item_id)).all())
    if not latest:
        return records
    out = []
    for record in records:
        if record.ref.kind == "item" and uuid.UUID(record.ref.id) in latest:
            record = replace(record, corrected=True)
        elif record.ref.kind == "event" and record.subkind in _ITEM_WORDING:
            item_id = about.get(int(record.ref.id))
            if item_id in latest and record.recorded_at < _utc(latest[item_id]):
                # What the item said then: true history, but no longer what it says.
                record = replace(record, applicability="no_longer_current",
                                 lifecycle=f"the user corrected it later; it now reads as in [item:{item_id}]")
        out.append(record)
    return out


def _with_evidence(db: Session, records: list[MemoryRecord]) -> list[MemoryRecord]:
    events = [int(r.ref.id) for r in records if r.ref.kind == "event"]
    items = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "item"]
    links = list(db.scalars(select(MemoryEvidence).where(or_(
        MemoryEvidence.event_id.in_(events) if events else False,
        MemoryEvidence.item_id.in_(items) if items else False)))) if (events or items) else []
    by_target: dict[str, list[MemoryEvidence]] = {}
    for link in links:
        key = f"event:{link.event_id}" if link.event_id is not None else f"item:{link.item_id}"
        by_target.setdefault(key, []).append(link)
    ended = _ended_sessions(db, links)
    from app.services import attachment_memory

    facts = [uuid.UUID(r.ref.id) for r in records if r.ref.kind == "fact"]
    try:
        files = attachment_memory.sources(db, facts)
    except Exception:  # noqa: BLE001 - a fact reads without its file sources rather than not at all
        log.exception("recall: reading file sources failed")
        files = {}
    out = []
    for record in records:
        if record.ref.kind in ("event", "item"):
            mine = by_target.get(str(record.ref), [])
            evidence = tuple(_evidence(link) for link in mine)
            lifecycle = record.lifecycle
            if record.ref.kind == "event" and lifecycle is None and any(ended.get(link.id) for link in mine):
                lifecycle = "its game session has ended"
            out.append(replace(record, evidence=evidence, lifecycle=lifecycle))
        elif record.ref.kind == "message":
            out.append(record)
        elif record.ref.kind == "fact":
            fact = db.get(SemanticMemory, uuid.UUID(record.ref.id))
            evidence = (Evidence("origin", "message", str(fact.source_message_id)),) if fact and fact.source_message_id else ()
            read = files.get(uuid.UUID(record.ref.id), [])
            evidence += tuple(Evidence(attachment_memory.ROLE, "observation", str(f.observation_id)) for f in read)
            out.append(replace(record, evidence=evidence, read_from=tuple(f.line for f in read)))
        else:
            episode = db.get(EpisodicMemory, uuid.UUID(record.ref.id))
            ids = (episode.source_message_ids or []) if episode else []
            evidence = tuple(Evidence("origin", "message", str(i)) for i in ids[:5])
            if episode and (episode.context or {}).get("session_id"):
                evidence += (Evidence("origin", "game_session", str(episode.context["session_id"])),)
            out.append(replace(record, evidence=evidence))
    return out


def _evidence(link: MemoryEvidence) -> Evidence:
    for column, name in (("message_id", "message"), ("observation_id", "observation"),
                         ("experience_id", "experience"), ("session_event_id", "session_event"),
                         ("thought_id", "thought")):
        value = getattr(link, column)
        if value is not None:
            return Evidence(link.role, name, str(value))
    return Evidence(link.role, "unknown", "")


def _ended_sessions(db: Session, links: list[MemoryEvidence]) -> dict[int, bool]:
    """Per evidence link: whether the game session it came from has ended."""
    sessions: dict[int, uuid.UUID] = {}
    obs = {l.observation_id: l.id for l in links if l.observation_id is not None}
    for oid, session_id in (db.execute(select(Observation.id, Observation.session_id).where(
            Observation.id.in_(list(obs)))).all() if obs else []):
        if session_id is not None:
            sessions[obs[oid]] = session_id
    exp = {l.experience_id: l.id for l in links if l.experience_id is not None}
    for eid, session_id in (db.execute(select(GameExperience.id, GameExperience.session_id).where(
            GameExperience.id.in_(list(exp)))).all() if exp else []):
        sessions[exp[eid]] = session_id
    sev = {l.session_event_id: l.id for l in links if l.session_event_id is not None}
    for sid, session_id in (db.execute(select(DesktopSessionEvent.id, DesktopSessionEvent.session_id).where(
            DesktopSessionEvent.id.in_(list(sev)))).all() if sev else []):
        sessions[sev[sid]] = session_id
    if not sessions:
        return {}
    states = dict(db.execute(select(DesktopSession.id, DesktopSession.state).where(
        DesktopSession.id.in_(set(sessions.values())))).all())
    return {link_id: states.get(session_id) in DESKTOP_TERMINAL_STATES for link_id, session_id in sessions.items()}


# --------------------------------------------------------------------------- #
# One record, and a context's timeline
# --------------------------------------------------------------------------- #


def _read_one(db: Session, user_id: uuid.UUID, ref: MemoryRef, s: _Search | None = None) -> MemoryRecord | None:
    """The latest version of one memory, or None when it's gone or not this user's (the same answer)."""
    if ref.kind == "message":
        from app.services import transcript_search

        return transcript_search.read(db, user_id, ref.id)
    model = {"event": MemoryEvent, "item": WorkingItem, "fact": SemanticMemory, "episode": EpisodicMemory}[ref.kind]
    row = db.get(model, int(ref.id) if ref.kind == "event" else uuid.UUID(ref.id))
    if row is None or row.user_id != user_id or getattr(row, "status", None) == "deleted":
        return None
    if ref.kind in ("fact", "episode"):
        return annotate(db, [_fact_record(row) if ref.kind == "fact" else _episode_record(row)])[0]
    if s is None:
        s = _Search(db, RecallRequest(user_id=user_id, query="", purpose="explicit_search",
                                      now=datetime.now(timezone.utc)))
    if row.context_id not in s.contexts:
        s.contexts[row.context_id] = db.get(MemoryContext, row.context_id)
    return annotate(db, [_event_record(row, s) if ref.kind == "event" else _item_record(row, s)])[0]


def read(db: Session, user_id: uuid.UUID, ref: MemoryRef | str, *, include_evidence: bool = False) -> RecallResult:
    """One memory by its reference. Missing and not-yours look the same: ``no_match`` with ``unavailable``."""
    try:
        ref = ref if isinstance(ref, MemoryRef) else MemoryRef.parse(ref)
    except RecallInvalid:
        return RecallResult("no_match", errors=["unavailable"])
    try:
        record = _read_one(db, user_id, ref)
    except Exception:  # noqa: BLE001
        log.exception("recall: reading %s failed", ref)
        return RecallResult.failed("read_failed", (ref.kind,))
    if record is None:
        return RecallResult("no_match", searched_kinds=(ref.kind,), errors=["unavailable"])
    record = replace(record, matched=("direct_reference",))
    if include_evidence:
        record = _with_evidence(db, [record])[0]
    return RecallResult("matched", [record], (ref.kind,))


def reread(db: Session, user_id: uuid.UUID, refs: list[MemoryRef]) -> RecallResult:
    """Memories already in hand (an answer's references), each at its latest version, without a search: the
    cache check. Gone or not-yours drop out and are counted (``references_gone``); a correction since shows as
    on every read path. ``matched`` with the records, else ``not_searched`` (a cache is never "no match")."""
    try:
        s = _Search(db, RecallRequest(user_id=user_id, query="", purpose="explicit_search",
                                      now=datetime.now(timezone.utc)))
        records, gone, aside = [], [], []
        for ref in refs:
            record = _read_one(db, user_id, ref, s)
            if record is not None and record.forgotten:
                aside.append(str(ref))  # history she was shown when asked: not carried on as memory
            elif record is None:
                gone.append(str(ref))
            else:
                records.append(replace(record, matched=("direct_reference",)))
    except Exception:  # noqa: BLE001
        log.exception("recall: rereading %d references failed", len(refs))
        return RecallResult.failed("read_failed", tuple(sorted({r.kind for r in refs})))
    coverage = ({"source": "cache"} | ({"references_gone": gone} if gone else {})
                | ({"references_set_aside": aside} if aside else {}))
    kinds = tuple(sorted({r.kind for r in refs}))
    if not records:
        return RecallResult("not_searched", searched_kinds=kinds, coverage=coverage | {"reason": "nothing cached"})
    return RecallResult("matched", records, kinds, coverage=coverage)


def render_records(records: list[MemoryRecord], *, now: datetime, zone: str | None) -> list[str]:
    """One line per record, as ``render`` writes them, without its search status line."""
    tz = _zone(zone)
    return [_record_line(record, now, tz) for record in records]


def timeline(db: Session, user_id: uuid.UUID, context_id: uuid.UUID, *, time_range: TimeRange | None = None,
             page_token: str | None = None, limit: int = TIMELINE_LIMIT) -> RecallResult:
    """What happened in one context, oldest first by when it happened (ties by when it was written), with
    its goals and promises. Unknown or foreign contexts are ``no_match`` / ``unavailable``."""
    context = db.get(MemoryContext, context_id)
    if context is None or context.user_id != user_id:
        return RecallResult("no_match", searched_kinds=("event",), errors=["unavailable"])
    if time_range is not None and time_range.basis != "occurred":
        raise RecallInvalid("a timeline is ordered by when things happened: use the occurred basis")
    req = RecallRequest(user_id=user_id, query="", purpose="explicit_search", now=datetime.now(timezone.utc),
                        context_ids=(context_id,), intent="historical", limit=limit, page_token=page_token)
    offset = _offset(req)
    try:
        s = _Search(db, req)
        query = select(MemoryEvent).where(MemoryEvent.user_id == user_id, MemoryEvent.context_id == context_id)
        if time_range and time_range.start:
            query = query.where(MemoryEvent.occurred_at >= time_range.start)
        if time_range and time_range.end:
            query = query.where(MemoryEvent.occurred_at < time_range.end)
        rows = list(db.scalars(query.order_by(MemoryEvent.occurred_at, MemoryEvent.id)
                               .offset(offset).limit(min(limit, TIMELINE_LIMIT) + 1)))
        more = len(rows) > min(limit, TIMELINE_LIMIT)
        rows = rows[:min(limit, TIMELINE_LIMIT)]
        records = [replace(_event_record(row, s), matched=("named_context",)) for row in rows]
        if offset == 0:
            items = db.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id,
                                                         WorkingItem.context_id == context_id)
                               .order_by(WorkingItem.created_at).limit(10))
            records += [replace(_item_record(entry, s), matched=("named_context",)) for entry in items]
        records = _with_evidence(db, annotate(db, records))
    except Exception:  # noqa: BLE001
        log.exception("recall: timeline of %s failed", context_id)
        return RecallResult.failed("timeline_failed", ("event", "item"))
    coverage = {"order": "by when it happened; ties by when it was written", "context_status": context.status}
    return RecallResult("matched" if records else "no_match", records, ("event", "item"),
                        {"contexts": [context.label]}, coverage,
                        next_page=_page_token(req, offset + len(rows)) if more else None)


# --------------------------------------------------------------------------- #
# Pages: opaque, bound to this user and this search
# --------------------------------------------------------------------------- #


def _search_key(req: RecallRequest) -> str:
    tr = req.time_range
    return _fingerprint(req.query, sorted(str(c) for c in req.context_ids), req.context_query, req.kinds, req.intent,
                        tr.basis if tr else None, tr.start if tr else None, tr.end if tr else None)


def _page_token(req: RecallRequest, offset: int) -> str:
    raw = json.dumps({"u": str(req.user_id), "k": _search_key(req), "o": offset}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(req: RecallRequest) -> int:
    if not req.page_token:
        return 0
    try:
        padded = req.page_token + "=" * (-len(req.page_token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        offset = int(data["o"])
    except Exception as exc:  # noqa: BLE001
        raise RecallInvalid("that page token isn't valid") from exc
    if data.get("u") != str(req.user_id) or data.get("k") != _search_key(req) or not 0 <= offset <= 10000:
        raise RecallInvalid("that page token belongs to a different search")
    return offset


# --------------------------------------------------------------------------- #
# Rendering, at the consumer
# --------------------------------------------------------------------------- #


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _ago(then: datetime, now: datetime) -> str:
    from app.services.memory_manager import _ago as ago

    return ago(then, now)


def _when(moment: datetime, now: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return f"{local:%a %d %b %Y, %H:%M} ({zone.key}; {_ago(moment, now)})"


_WHOSE = {"fact": "something they told you", "episode": "an episode", "event": "an event", "item": ""}


def _message_line(record: MemoryRecord, now: datetime, zone: ZoneInfo) -> str:
    """A transcript passage: who said it, when, where, and that it's what was said, not a checked fact."""
    where = f' in "{record.context_label}"' if record.context_label else ""
    parts = [f"{origin_label(record)}, {_when(record.occurred_at, now, zone)}{where}",
             "conversation history, not a checked fact"]
    if record.lifecycle and "stopped it here" in record.lifecycle:
        parts.append("the user stopped this reply here and didn't hear the rest")
    if record.forgotten:
        parts.append("they asked you to forget something in it: you may tell them what was said if they ask about "
                     "that conversation, but it isn't something you remember, and don't bring it up yourself")
    return f"- [{record.ref}] " + " · ".join(parts) + f'\n  "{record.content}"'


def _record_line(record: MemoryRecord, now: datetime, zone: ZoneInfo) -> str:
    if record.ref.kind == "message":
        return _message_line(record, now, zone)
    where = record.context_label or ("no context recorded (older memory)" if record.ref.kind in ("fact", "episode")
                                     else "unknown context")
    head = f"- [{record.ref}] {record.subkind}"
    if record.ref.kind == "item":
        head = f"- [{record.ref}] {record.subkind} ({record.status})"
    origin = origin_label(record)
    parts = ([origin] if origin else []) + [f"in: {where}"]
    if record.about:
        parts.append("about: " + ", ".join(record.about))
    if record.time is not None:
        from app.services import memory_time

        what = {"item": "planned for", "episode": "happened", "event": "happened"}.get(record.ref.kind, "when")
        parts.append(f"{what}: {memory_time.describe(record.time, now)}")
    elif record.ref.kind in ("event", "episode"):
        parts.append(f"happened {_when(record.occurred_at, now, zone)}" if record.occurred_at
                     else "when it happened is unknown")
    if record.scheduled_at and record.subkind != "plan":
        parts.append(f"due {_when(record.scheduled_at, now, zone)}")
    if record.ref.kind in ("fact", "item"):
        parts.append(f"recorded {_when(record.recorded_at, now, zone)} (when it was written down, not when it "
                     "happened)")
    parts.append(record.epistemic)
    if record.applicability == "no_longer_current":
        parts.append("no longer current" + (f": {record.lifecycle}" if record.lifecycle else ""))
    elif record.lifecycle:
        parts.append(record.lifecycle)
    if record.corrected:
        parts.append("corrected by the user since it was first noted")
    if "direct_reference" in record.matched:
        parts.append("from your last answer")
    if record.evidence == ():
        parts.append("its source is no longer available")
    if record.read_from:
        parts.append("learned while you read " + "; ".join(record.read_from))
    why = f"\n  Why you thought so: {record.reason}" if record.reason else ""
    words = f'\n  Their words: "{record.user_words}"' if record.user_words else ""
    return f"{head} · " + " · ".join(parts) + f"\n  {record.content}{words}{why}"


def status_line(result: RecallResult) -> str:
    looked = ", ".join(KIND_LABEL[k] for k in result.searched_kinds) or "nothing"
    if result.status == "not_searched":
        return "Memory search: not run for this."
    if result.status == "failed":
        return "Memory search FAILED: you couldn't check your memory just now. Don't say nothing is recorded."
    notes = []
    if result.status == "partial":
        missing = ", ".join(e.replace("_unavailable", "") for e in result.errors)
        notes.append(f"some memory couldn't be read ({missing}): what's here is incomplete")
    if result.coverage.get("truncated") or result.coverage.get("more_matches"):
        notes.append("more matched than is shown")
    if result.coverage.get("references_gone"):
        notes.append("something your last answer used has since been removed or changed")
    if result.coverage.get("references_set_aside"):
        notes.append("conversation history your last answer quoted was about something they asked you to forget: "
                     "not carried on")
    if result.coverage.get("dropped_unheard"):
        notes.append("some matches were in words the user never heard and are left out")
    if result.coverage.get("contexts_matched"):
        notes.append("several contexts match: " + ", ".join(result.coverage["contexts_matched"]))
    tail = (" (" + "; ".join(notes) + ")") if notes else ""
    if result.status == "no_match":
        return f"Memory search ran (looked in: {looked}) and found nothing that matches{tail}."
    return f"Memory search ran (looked in: {looked}){tail}:"


def render(result: RecallResult, *, now: datetime, zone: str | None) -> str:
    """The result as she reads it: a status line, then one entry per record with its id, context, times,
    how it's known, and whether it still holds."""
    tz = _zone(zone)
    lines = [status_line(result)]
    lines += [_record_line(record, now, tz) for record in result.records]
    if result.next_page:
        lines.append(f"(More: page token {result.next_page})")
    return "\n".join(lines)


BUNDLE_SECTIONS = (
    ("standing", "Always applies (a standing constraint: it holds whatever the topic, and doesn't answer the "
                 "question by itself)"),
    ("unresolved", "Unresolved (something they said doesn't match this, and nothing has settled which holds: "
                   "don't settle it yourself)"),
    ("current", "What holds now"),
    ("open", "Open plans, goals, promises and questions"),
    ("past", "The past (things that happened, or no longer hold)"),
)


def section_of(record: MemoryRecord) -> str:
    """Where a record goes in a reply's evidence: standing constraints, unresolved disagreements, what holds
    now, open work, the past. Each record goes in exactly one, the first that fits."""
    if record.ref.kind == "message":
        return "past"
    if "standing" in record.matched and record.applicability != "no_longer_current":
        return "standing"
    if any(part.startswith("unresolved:") for part in (record.lifecycle or "").split("; ")):
        return "unresolved"
    if record.applicability in ("past", "no_longer_current"):
        return "past"
    if record.ref.kind == "item":
        return "open"
    return "current"


def render_bundle(result: RecallResult, *, now: datetime, zone: str | None) -> str:
    """A reply's evidence: the status line, then its records in sections (``BUNDLE_SECTIONS``), each record in
    one. The same records ``render`` shows, grouped."""
    tz = _zone(zone)
    lines = [status_line(result)]
    if result.coverage.get("references_ambiguous"):
        lines.append("(More than one memory fits someone or something they mentioned: don't pick one. If it "
                     "matters which they mean, ask.)")
    for key, title in BUNDLE_SECTIONS:
        records = [r for r in result.records if section_of(r) == key]
        if records:
            lines.append(f"{title}:")
            lines += [_record_line(r, now, tz) for r in records]
    if result.next_page:
        lines.append(f"(More: page token {result.next_page})")
    return "\n".join(lines)


def render_legacy(result: RecallResult) -> str | None:
    """The old long-term block (facts and episodes only), for callers that read it as text: the same lines
    ``retrieve_for_prompt`` always made, now ranked by this service."""
    lines = []
    for record in result.records:
        if record.ref.kind == "episode":
            source = record.subkind if record.subkind != "episode" else None
            label = f"episode, {record.occurred_at:%Y-%m-%d}" + (f", {source}" if source else "")
            lines.append(f"- [{label}] {record.content}")
        elif record.ref.kind == "fact":
            unresolved = next((part for part in (record.lifecycle or "").split("; ") if part.startswith("unresolved:")),
                              None)
            lines.append(f"- [{record.subkind}] {record.content}" + (f" ({unresolved})" if unresolved else ""))
        else:
            continue
        if record.time is not None:
            from app.services import memory_time

            lines[-1] += f" (when: {memory_time.describe(record.time, datetime.now(timezone.utc))})"
    return "\n".join(lines) or None


def _log(entry: dict) -> None:
    path = get_settings().recall_log_path
    if not path:
        return
    try:
        import os

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **entry}, default=str) + "\n")
    except OSError:
        pass
