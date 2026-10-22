"""Evidence expansion (docs/COLUMBINA_SOURCE_BACKED_RECALL.md §2): from the memories a search found, follow
the evidence links already stored to the sources they came from (the user's messages, what a game
reported, her thoughts), and from those sources to every other memory they started, closed or corrected.
That's how a 6 pm plan in one context reaches the 4 pm plan it replaced in another: the update message
that closed one opened the other.

The links are ``memory_evidence`` (items and events: origin, resolution, correction), a fact's
``source_message_id`` and the messages in its kept history, an episode's ``source_message_ids``, and an
event's item. Contexts are crossed on purpose: attention contexts don't separate subjects. An explicit
scope only marks what lies outside it, so history can come from anywhere but what holds there now can't.

Every step is filtered by the owner, and a row that isn't this user's (or no longer exists) never becomes a
passage; it's only counted. Sharing a source makes a record a candidate, nothing more: one message can
talk about several things, and the reader decides which passages are about the question.

Bounded: link distance, records visited, records reached from one source, text shown and time, each
reported when it cuts. A cut or a failure is never "there was no more history". Read-only.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.observe.registry import SOURCE_LABELS
from app.ai.recall.contracts import MemoryRecord, RecallRequest
from app.ai.recall.history import Expansion, NodeRef, Passage
from app.db.models import (
    ChatMessage, Conversation, DesktopSession, DesktopSessionEvent, EpisodicMemory, GameExperience, MemoryEvent,
    MemoryEvidence, MindThought, Observation, SemanticMemory, WorkingItem,
)
from app.services import heard, recall

log = logging.getLogger(__name__)

FOLLOW_ROLES = ("origin", "resolution", "correction")
_SOURCE_COLUMN = {"message": "message_id", "observation": "observation_id", "experience": "experience_id",
                  "session_event": "session_event_id", "thought": "thought_id"}


@dataclass(frozen=True)
class Budget:
    max_depth: int = 6
    max_nodes: int = 128
    max_fanout: int = 24
    max_chars: int = 16000
    passage_chars: int = 1500
    seconds: float = 3.0

    @classmethod
    def from_settings(cls, settings) -> Budget:
        return cls(settings.recall_history_max_depth, settings.recall_history_max_nodes,
                   settings.recall_history_max_fanout, settings.recall_history_max_chars,
                   settings.recall_history_passage_chars, settings.recall_history_seconds)


@dataclass
class _Walk:
    db: Session
    user_id: uuid.UUID
    budget: Budget
    started: float
    depth: dict[NodeRef, int] = field(default_factory=dict)
    edges: list[tuple[NodeRef, str, NodeRef]] = field(default_factory=list)  # memory -(role)-> source
    truncated: list[str] = field(default_factory=list)
    queries: int = 0
    stop: str = "exhausted"

    def out_of_time(self) -> bool:
        return time.monotonic() - self.started > self.budget.seconds

    def scalars(self, statement):
        self.queries += 1
        return list(self.db.scalars(statement))

    def rows(self, statement):
        self.queries += 1
        return list(self.db.execute(statement).all())

    def reach(self, found: Iterable[tuple[NodeRef, str, NodeRef]], depth: int, *, from_source: bool) -> list[NodeRef]:
        """New nodes one link away, at most ``max_fanout`` from any one node, within the node budget."""
        per_origin: dict[NodeRef, int] = {}
        new: list[NodeRef] = []
        cut_fanout = 0
        for memory, role, source in found:
            origin, target = (source, memory) if from_source else (memory, source)
            edge = (memory, role, source)
            if edge not in self.edges:
                self.edges.append(edge)
            if target in self.depth:
                continue
            if per_origin.get(origin, 0) >= self.budget.max_fanout:
                cut_fanout += 1
                continue
            if len(self.depth) >= self.budget.max_nodes:
                self.stop = "node_budget"
                break
            per_origin[origin] = per_origin.get(origin, 0) + 1
            self.depth[target] = depth
            new.append(target)
        if cut_fanout:
            self.truncated.append(f"{cut_fanout} record(s) linked to a much-used source weren't followed")
        return new


def expand(db: Session, user_id: uuid.UUID, seeds: list[MemoryRecord], *, scope: set[uuid.UUID] | None = None,
           budget: Budget | None = None, now: datetime | None = None) -> Expansion:
    """Collect passages around ``seeds`` (records a search returned for this user). Never raises: a failure
    part way is ``stop="failed"`` with what was collected so far."""
    budget = budget or Budget()
    walk = _Walk(db, user_id, budget, time.monotonic())
    for record in seeds:
        walk.depth.setdefault(NodeRef(record.ref.kind, record.ref.id), 0)
    if not walk.depth:
        return Expansion([], [], 0, 0, "no_seeds")
    frontier = list(walk.depth)
    depth = 0
    try:
        while frontier:
            if walk.out_of_time():
                walk.stop = "time_budget"
                break
            if depth >= budget.max_depth:
                walk.stop = "depth_budget"
                break
            depth += 1
            frontier = _step(walk, frontier, depth)
            if walk.stop != "exhausted":
                break
    except Exception:  # noqa: BLE001 - what was collected is still shown, marked incomplete
        log.exception("recall: history expansion failed for %s", user_id)
        walk.stop = "failed"
        db.rollback()
    try:
        passages, gaps, unavailable = _passages(walk, seeds, scope)
    except Exception:  # noqa: BLE001
        log.exception("recall: reading history passages failed for %s", user_id)
        db.rollback()
        return Expansion([r.ref for r in seeds], [], len(walk.depth), depth, "failed", walk.truncated,
                         ["the passages couldn't be read"], 0, _ms(walk.started), walk.queries)
    if walk.stop == "exhausted" and len(passages) < len(walk.depth) - unavailable:
        walk.stop = "char_budget"
    return Expansion([r.ref for r in seeds], passages, len(walk.depth), max(walk.depth.values(), default=0),
                     walk.stop, walk.truncated, gaps, unavailable, _ms(walk.started), walk.queries)  # type: ignore[arg-type]


def _step(walk: _Walk, frontier: list[NodeRef], depth: int) -> list[NodeRef]:
    """One link further from every node of the frontier, a few batched queries per kind."""
    db_user = walk.user_id
    by_kind: dict[str, list[str]] = {}
    for node in frontier:
        by_kind.setdefault(node.kind, []).append(node.id)
    found_from_memory: list[tuple[NodeRef, str, NodeRef]] = []
    found_from_source: list[tuple[NodeRef, str, NodeRef]] = []

    items = [uuid.UUID(i) for i in by_kind.get("item", [])]
    events = [int(i) for i in by_kind.get("event", [])]
    if items or events:
        links = walk.scalars(select(MemoryEvidence).where(
            MemoryEvidence.user_id == db_user, MemoryEvidence.role.in_(FOLLOW_ROLES),
            or_(MemoryEvidence.item_id.in_(items) if items else False,
                MemoryEvidence.event_id.in_(events) if events else False)).order_by(MemoryEvidence.id))
        for link in links:
            found_from_memory.append((_target(link), link.role, _source(link)))
    if events:
        # An event about an item reaches the item (its wording then, its status now).
        for event_id, item_id in walk.rows(select(MemoryEvent.id, MemoryEvent.item_id).where(
                MemoryEvent.user_id == db_user, MemoryEvent.id.in_(events), MemoryEvent.item_id.is_not(None))):
            found_from_memory.append((NodeRef("event", str(event_id)), "about", NodeRef("item", str(item_id))))
    if items:
        # And an item reaches the events that quote it: its wording before a revision, its closing.
        for event_id, item_id in walk.rows(select(MemoryEvent.id, MemoryEvent.item_id).where(
                MemoryEvent.user_id == db_user, MemoryEvent.item_id.in_(items)).order_by(MemoryEvent.id)):
            found_from_source.append((NodeRef("event", str(event_id)), "about", NodeRef("item", str(item_id))))
    facts = [uuid.UUID(i) for i in by_kind.get("fact", [])]
    if facts:
        for fact in walk.scalars(select(SemanticMemory).where(SemanticMemory.user_id == db_user,
                                                              SemanticMemory.id.in_(facts))):
            node = NodeRef("fact", str(fact.id))
            if fact.source_message_id:
                found_from_memory.append((node, "origin", NodeRef("message", str(fact.source_message_id))))
            for old in (fact.metadata_ or {}).get("history") or []:
                for key, role in (("source_message_id", "origin"), ("replaced_by_message_id", "correction")):
                    if _uuid(old.get(key)):
                        found_from_memory.append((node, role, NodeRef("message", old[key])))
    episodes = [uuid.UUID(i) for i in by_kind.get("episode", [])]
    if episodes:
        for episode in walk.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == db_user,
                                                                 EpisodicMemory.id.in_(episodes))):
            for message_id in (episode.source_message_ids or [])[: walk.budget.max_fanout]:
                if _uuid(message_id):
                    found_from_memory.append((NodeRef("episode", str(episode.id)), "origin",
                                              NodeRef("message", str(message_id))))

    sources = {kind: by_kind[kind] for kind in _SOURCE_COLUMN if by_kind.get(kind)}
    if sources:
        clauses = []
        for kind, ids in sources.items():
            column = getattr(MemoryEvidence, _SOURCE_COLUMN[kind])
            clauses.append(column.in_([int(i) for i in ids] if kind in ("observation", "experience", "session_event")
                                      else [uuid.UUID(i) for i in ids]))
        for link in walk.scalars(select(MemoryEvidence).where(
                MemoryEvidence.user_id == db_user, MemoryEvidence.role.in_(FOLLOW_ROLES), or_(*clauses))
                .order_by(MemoryEvidence.id)):
            found_from_source.append((_target(link), link.role, _source(link)))
    messages = sources.get("message", [])
    if messages:
        ids = [uuid.UUID(i) for i in messages]
        for fact in walk.scalars(select(SemanticMemory).where(
                SemanticMemory.user_id == db_user, SemanticMemory.status != "deleted",
                SemanticMemory.source_message_id.in_(ids)).order_by(SemanticMemory.created_at)):
            found_from_source.append((NodeRef("fact", str(fact.id)), "origin",
                                      NodeRef("message", str(fact.source_message_id))))
        history = [SemanticMemory.metadata_["history"].contains([{key: m}])
                   for m in messages for key in ("source_message_id", "replaced_by_message_id")]
        for fact in walk.scalars(select(SemanticMemory).where(
                SemanticMemory.user_id == db_user, SemanticMemory.status != "deleted", or_(*history))):
            for old in (fact.metadata_ or {}).get("history") or []:
                for key, role in (("source_message_id", "origin"), ("replaced_by_message_id", "correction")):
                    if old.get(key) in messages:
                        found_from_source.append((NodeRef("fact", str(fact.id)), role, NodeRef("message", old[key])))
        for episode in walk.scalars(select(EpisodicMemory).where(
                EpisodicMemory.user_id == db_user,
                or_(*[EpisodicMemory.source_message_ids.contains([m]) for m in messages]))):
            for m in episode.source_message_ids or []:
                if m in messages:
                    found_from_source.append((NodeRef("episode", str(episode.id)), "origin", NodeRef("message", m)))

    new = walk.reach(found_from_memory, depth, from_source=False)
    if walk.stop == "exhausted":
        new += walk.reach(found_from_source, depth, from_source=True)
    return new


def _target(link: MemoryEvidence) -> NodeRef:
    return NodeRef("item", str(link.item_id)) if link.item_id is not None else NodeRef("event", str(link.event_id))


def _source(link: MemoryEvidence) -> NodeRef:
    for kind, column in _SOURCE_COLUMN.items():
        value = getattr(link, column)
        if value is not None:
            return NodeRef(kind, str(value))
    raise ValueError("evidence link without a source")


def _uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Passages: what each visited node says, owner-checked
# --------------------------------------------------------------------------- #


@dataclass
class _Draft:
    node: NodeRef
    speaker: str
    text: str
    said_at: datetime | None
    order: tuple
    fingerprint: str
    context_label: str | None = None
    in_scope: bool | None = None
    status: str | None = None
    source_label: str | None = None
    record: MemoryRecord | None = None
    cut: bool = False


def _passages(walk: _Walk, seeds: list[MemoryRecord], scope: set[uuid.UUID] | None):
    db, user_id = walk.db, walk.user_id
    nodes = list(walk.depth)
    by_kind: dict[str, list[str]] = {}
    for node in nodes:
        by_kind.setdefault(node.kind, []).append(node.id)
    drafts: dict[NodeRef, _Draft] = {}
    search = recall._Search(db, RecallRequest(user_id=user_id, query="", purpose="explicit_search",
                                              now=datetime.now(timezone.utc)))
    records: list[MemoryRecord] = []
    if by_kind.get("event"):
        rows = walk.scalars(select(MemoryEvent).where(MemoryEvent.user_id == user_id,
                                                      MemoryEvent.id.in_([int(i) for i in by_kind["event"]])))
        records += [recall._event_record(row, search) for row in rows]
    if by_kind.get("item"):
        rows = walk.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id,
                                                      WorkingItem.id.in_([uuid.UUID(i) for i in by_kind["item"]])))
        records += [recall._item_record(row, search) for row in rows]
    facts: dict[str, SemanticMemory] = {}
    if by_kind.get("fact"):
        for row in walk.scalars(select(SemanticMemory).where(
                SemanticMemory.user_id == user_id, SemanticMemory.status != "deleted",
                SemanticMemory.id.in_([uuid.UUID(i) for i in by_kind["fact"]]))):
            facts[str(row.id)] = row
            records.append(recall._fact_record(row))
    if by_kind.get("episode"):
        rows = walk.scalars(select(EpisodicMemory).where(
            EpisodicMemory.user_id == user_id, EpisodicMemory.id.in_([uuid.UUID(i) for i in by_kind["episode"]])))
        records += [recall._episode_record(row) for row in rows]
    walk.queries += 2
    for record in recall._with_evidence(db, recall.annotate(db, records)):
        node = NodeRef(record.ref.kind, record.ref.id)
        text = record.content
        fact = facts.get(record.ref.id)
        for old in ((fact.metadata_ or {}).get("history") or [])[-3:] if fact is not None else []:
            text += f' [its earlier wording, replaced ({old.get("reason") or "updated"}): "{old.get("content")}"]'
        status = record.status
        if record.applicability == "no_longer_current":
            status += ", no longer current" + (f": {record.lifecycle}" if record.lifecycle else "")
        elif record.lifecycle:
            status += f", {record.lifecycle}"
        if record.corrected:
            status += ", corrected by the user since it was first noted"
        in_scope = None
        if scope is not None and record.ref.kind in ("event", "item"):
            in_scope = record.context_id in scope
        said = record.occurred_at if record.ref.kind in ("event", "episode") and record.occurred_at else record.recorded_at
        drafts[node] = _Draft(node, "memory", text, said, (said, 1, str(node)), record.fingerprint,
                              record.context_label, in_scope, status, record.source_label, record)

    if by_kind.get("message"):
        for message in walk.scalars(select(ChatMessage).join(Conversation, Conversation.id == ChatMessage.conversation_id)
                                    .where(Conversation.user_id == user_id,
                                           ChatMessage.id.in_([uuid.UUID(i) for i in by_kind["message"]]))):
            node = NodeRef("message", str(message.id))
            content = message.content or ""
            if message.role == "user":
                speaker, text = "user", content
            else:
                # Her words count only as far as the user heard them, and only as hers.
                speaker, text = "columbina", heard.heard_text(content, message.heard_chars)
            said = recall._utc(message.created_at)
            drafts[node] = _Draft(node, speaker, text, said, (said, 0, message.conversation_sequence or 0),
                                  _digest("message", message.id, content, message.heard_chars))
    if by_kind.get("observation"):
        for row in walk.scalars(select(Observation).where(
                Observation.user_id == user_id, Observation.id.in_([int(i) for i in by_kind["observation"]]))):
            said = recall._utc(row.occurred_at or row.received_at)
            drafts[NodeRef("observation", str(row.id))] = _Draft(
                NodeRef("observation", str(row.id)), "source", row.summary or row.kind, said, (said, 0, row.id),
                _digest("observation", row.id, row.summary), source_label=SOURCE_LABELS.get(row.source, row.source))
    if by_kind.get("experience"):
        for row in walk.scalars(select(GameExperience).where(
                GameExperience.user_id == user_id, GameExperience.id.in_([int(i) for i in by_kind["experience"]]))):
            payload = row.payload or {}
            text = str(payload.get("text") or payload.get("summary") or payload.get("reason") or row.kind)
            said = recall._utc(row.occurred_at)
            drafts[NodeRef("experience", str(row.id))] = _Draft(
                NodeRef("experience", str(row.id)), "source", f"{row.kind}: {text}", said, (said, 0, row.id),
                _digest("experience", row.id, text), source_label=SOURCE_LABELS.get(row.game, row.game))
    if by_kind.get("thought"):
        for row in walk.scalars(select(MindThought).where(
                MindThought.user_id == user_id, MindThought.id.in_([uuid.UUID(i) for i in by_kind["thought"]]))):
            said = recall._utc(row.created_at)
            # Her own thought: hers, like what she said.
            drafts[NodeRef("thought", str(row.id))] = _Draft(
                NodeRef("thought", str(row.id)), "columbina", row.text or "", said, (said, 0, str(row.id)),
                _digest("thought", row.id, row.text))
    if by_kind.get("session_event"):
        for row in walk.scalars(select(DesktopSessionEvent).join(DesktopSession, DesktopSession.id ==
                                                                 DesktopSessionEvent.session_id)
                                .where(DesktopSession.owner_user_id == user_id,
                                       DesktopSessionEvent.id.in_([int(i) for i in by_kind["session_event"]]))):
            payload = row.payload or {}
            text = f"{row.type}" + (f": {payload.get('goal') or payload.get('reason')}"
                                    if payload.get("goal") or payload.get("reason") else "")
            said = recall._utc(row.occurred_at)
            drafts[NodeRef("session_event", str(row.id))] = _Draft(
                NodeRef("session_event", str(row.id)), "source", text, said, (said, 0, row.id),
                _digest("session_event", row.id, text), source_label="the game session")

    unavailable = len(nodes) - len(drafts)
    # A memory with no links may never have had any: only a linked row that's gone is a known gap.
    gaps = [f"{unavailable} linked record(s) no longer exist or couldn't be read"] if unavailable else []

    # Seeds first, then nearer before further, within the text budget; then shown oldest first.
    chosen: list[_Draft] = []
    used = 0
    for node in sorted(drafts, key=lambda n: walk.depth[n]):
        draft = drafts[node]
        text = " ".join(draft.text.split())
        cut = len(text) > walk.budget.passage_chars
        text = text[: walk.budget.passage_chars].rsplit(" ", 1)[0] + " ..." if cut else text
        if used + len(text) > walk.budget.max_chars and chosen:
            continue
        used += len(text)
        chosen.append(replace(draft, text=text, cut=cut))
    chosen.sort(key=lambda d: (d.order[0] is None, d.order[0] or datetime.min.replace(tzinfo=timezone.utc),
                               d.order[1], str(d.order[2])))
    labels = {d.node: f"p{index}" for index, d in enumerate(chosen, start=1)}
    links: dict[NodeRef, list[tuple[str, str]]] = {}
    for memory, role, source in walk.edges:
        if memory in labels and source in labels:
            links.setdefault(memory, []).append((role, labels[source]))
            links.setdefault(source, []).append((role, labels[memory]))
    passages = []
    for d in chosen:
        passages.append(Passage(labels[d.node], d.node, d.speaker, d.text, d.cut, d.said_at, d.fingerprint,  # type: ignore[arg-type]
                                walk.depth[d.node], d.context_label, d.in_scope, d.status, d.source_label,
                                tuple(dict.fromkeys(links.get(d.node, []))), d.record))
    return passages, gaps, unavailable


def _digest(*parts) -> str:
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:16]


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
