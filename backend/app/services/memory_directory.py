"""What she knows about the user, by area (docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): the rundown behind
``what_i_know``. Built when read, from the current records, in one consistent read: nothing is stored, so it can
never be stale and never bring back what was forgotten (forgotten records are gone; transcripts are never read
here).

Areas come from who or what records are about (``memory_entities``: their sister Nora goes under Family, their dog
under Pets), then, for records about no one in particular, from what they are: a fact's kind (preferences,
standing constraints), an experience, an open plan, question or promise, or the activity or project it belongs
to. All three stores count: facts, episodes and open working items, entity links or not.

Without a topic: the areas, each with its counts and its first few records, and what wasn't shown (more records,
more areas, what isn't current), so a partial rundown is never read as complete. With a topic: that area's
records, paged.
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.ai.recall.contracts import MemoryRecord, RecallInvalid
from app.db.models import (
    WORKING_ITEM_CLOSED, EpisodicMemory, MemoryContext, MemoryEntity, SemanticMemory, WorkingItem,
)

log = logging.getLogger(__name__)

MAX_AREAS = 10  # shown without a topic
EXAMPLES = 3  # records per area without a topic
PAGE = 10  # records per page with a topic

FAMILY = frozenset("""sister brother mother mom mum father dad parent parents son daughter child children kid kids
wife husband partner spouse grandma grandmother grandpa grandfather grandparent aunt uncle cousin niece nephew
sibling siblings stepmother stepfather stepsister stepbrother fiance fiancee boyfriend girlfriend""".split())
_ENTITY_AREA = {"pet": ("pets", "Pets"), "place": ("places", "Places"),
                "organization": ("organizations", "Organizations"), "activity": ("activities", "Activities"),
                "thing": ("things", "Things they have or use"), "other": ("other", "Other people and things")}
_FACT_AREA = {"constraint": ("standing", "Standing constraints (always apply)"),
              "preference": ("preferences", "Preferences"), "goal": ("goals", "Their goals"),
              "fact": ("about", "Other things about them"), "plan": ("plans", "Plans")}
_ITEM_AREA = {"plan": ("plans", "Plans"), "question": ("questions", "Open questions"),
              "commitment": ("promises", "Promises"), "goal": ("goals", "Their goals"),
              "hypothesis": ("thoughts", "Your own open thoughts"), "decision": ("thoughts", "Your own open thoughts"),
              "intention": ("thoughts", "Your own open thoughts"),
              "constraint": ("standing", "Standing constraints (always apply)")}
STORE_WORD = {"fact": ("fact", "facts"), "episode": ("experience", "experiences"), "item": ("open item", "open items")}


@dataclass
class Area:
    key: str
    label: str
    records: list[MemoryRecord] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # who or what it's about, in words
    words: set[str] = field(default_factory=set)  # for matching a topic

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for record in self.records:
            out[record.ref.kind] = out.get(record.ref.kind, 0) + 1
        return out


@dataclass
class Directory:
    status: str  # matched, no_match, failed
    areas: list[Area]  # shown
    hidden: list[Area]  # not shown (no topic: past MAX_AREAS; topic: the other areas)
    topic: str | None
    records: list[MemoryRecord]  # the records shown, for the reply's receipt
    totals: dict[str, int]
    not_current: dict[str, int]
    more_in_area: dict[str, int] = field(default_factory=dict)
    next_page: str | None = None
    error: str | None = None


def _consistent(db: Session) -> None:
    """Counts and examples from one snapshot."""
    try:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    except Exception:  # noqa: BLE001 - the session already read something: start a fresh transaction
        db.rollback()
        try:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        except Exception:  # noqa: BLE001
            db.rollback()
            log.warning("memory directory: couldn't take a consistent snapshot", exc_info=True)


def _bases(text_: str | None) -> set[str]:
    from app.services import recall

    return {recall._base(w) for w in recall._words(text_ or "")}


def _rank(record: MemoryRecord, importance: dict[str, float]) -> tuple:
    when = record.updated_at or record.occurred_at or record.recorded_at
    return (-importance.get(str(record.ref), 0.5), -(when.timestamp() if when else 0))


def build(db: Session, user_id: uuid.UUID, *, topic: str | None = None, page_token: str | None = None,
          now: datetime | None = None) -> Directory:
    """The rundown. Raises ``RecallInvalid`` for a bad page token; a broken read is ``failed``. Reads in a
    snapshot of its own, ended before it returns (the session is free for writes after)."""
    try:
        return _build(db, user_id, topic=topic, page_token=page_token, now=now)
    finally:
        db.rollback()


def _build(db: Session, user_id: uuid.UUID, *, topic: str | None, page_token: str | None,
           now: datetime | None) -> Directory:
    from app.services import memory_entities, recall

    now = now or datetime.now(timezone.utc)
    topic = (topic or "").strip() or None
    offset = _offset(page_token, user_id, topic)
    _consistent(db)
    try:
        naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)
        facts = list(db.scalars(select(SemanticMemory).where(
            SemanticMemory.user_id == user_id, SemanticMemory.status == "active",
            or_(SemanticMemory.expires_at.is_(None), SemanticMemory.expires_at > naive_now))))
        episodes = list(db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == user_id,
                                                                EpisodicMemory.status == "active")))
        contexts = {c.id: c for c in db.scalars(select(MemoryContext).where(MemoryContext.user_id == user_id))}
        items = [i for i in db.scalars(select(WorkingItem).where(WorkingItem.user_id == user_id,
                                                                 WorkingItem.closed_at.is_(None)))
                 if not (contexts.get(i.context_id) is not None and contexts[i.context_id].status == "closed")]
        not_current = {
            "replaced or forgotten facts": db.query(SemanticMemory).filter(
                SemanticMemory.user_id == user_id, SemanticMemory.status != "active").count(),
            "finished or closed items": db.query(WorkingItem).filter(
                WorkingItem.user_id == user_id, WorkingItem.status.in_(WORKING_ITEM_CLOSED)).count(),
            "older versions of experiences": db.query(EpisodicMemory).filter(
                EpisodicMemory.user_id == user_id, EpisodicMemory.status != "active").count(),
        }
        s = recall._Search(db, recall.RecallRequest(user_id=user_id, query="", purpose="explicit_search", now=now))
        s.contexts.update(contexts)
        records = ([recall._fact_record(f) for f in facts] + [recall._episode_record(e) for e in episodes]
                   + [recall._item_record(i, s) for i in items])
        records = recall.annotate(db, records)
        importance = {f"fact:{f.id}": f.importance for f in facts} | {f"episode:{e.id}": e.importance for e in episodes}
        about = {}
        for kind, rows in (("fact", facts), ("item", items), ("episode", episodes)):
            ids = [r.id for r in rows]
            if ids:
                about.update(memory_entities.about(db, user_id, **{f"{kind}s": ids}))
        item_rows = {str(i.id): i for i in items}
        fact_rows = {str(f.id): f for f in facts}
    except Exception:  # noqa: BLE001
        log.exception("memory directory: reading failed")
        db.rollback()
        return Directory("failed", [], [], topic, [], {}, {}, error="read_failed")

    areas: dict[str, Area] = {}

    def area(key: str, label: str) -> Area:
        return areas.setdefault(key, Area(key, label, words=_bases(label)))

    for record in records:
        entities = about.get(uuid.UUID(record.ref.id), [])
        entity: MemoryEntity | None = entities[0] if entities else None
        if record.ref.kind == "fact" and fact_rows[record.ref.id].kind == "constraint":
            target = area(*_FACT_AREA["constraint"])  # always first, whoever it's about
        elif entity is not None:
            if entity.kind == "person":
                relation = _bases(entity.relation)
                target = area("family", "Family") if relation & {recall._base(w) for w in FAMILY} \
                    else area("people", "People")
            else:
                target = area(*_ENTITY_AREA.get(entity.kind, _ENTITY_AREA["other"]))
        elif record.ref.kind == "fact":
            target = area(*_FACT_AREA.get(fact_rows[record.ref.id].kind, _FACT_AREA["fact"]))
        elif record.ref.kind == "episode":
            target = area("experiences", "Experiences")
        else:
            entry = item_rows[record.ref.id]
            context = contexts.get(entry.context_id)
            if context is not None and context.kind in ("activity", "project", "world"):
                target = area(f"context:{context.id}", context.label)
            else:
                target = area(*_ITEM_AREA.get(entry.kind, ("other", "Other open items")))
        target.records.append(record)
        for entity in entities:
            name = memory_entities.label(entity)
            if name not in target.names:
                target.names.append(name)
            target.words |= _bases(entity.name) | _bases(entity.relation)
        if record.context_label:
            target.words |= _bases(record.context_label)
    for a in areas.values():
        a.records.sort(key=lambda r: _rank(r, importance))
    ordered = sorted(areas.values(), key=lambda a: (a.key != "standing", -len(a.records), a.label))
    totals = {"fact": len(facts), "episode": len(episodes), "item": len(items)}

    if topic is None:
        shown, hidden = ordered[:MAX_AREAS], ordered[MAX_AREAS:]
        examples, more = [], {}
        for a in shown:
            examples += a.records[:EXAMPLES]
            if len(a.records) > EXAMPLES:
                more[a.key] = len(a.records) - EXAMPLES
        status = "matched" if records else "no_match"
        return Directory(status, shown, hidden, None, examples, totals, not_current, more)

    wanted = _bases(topic)
    matching = [a for a in ordered if wanted & a.words] if wanted else []
    if not matching:
        return Directory("no_match", [], ordered, topic, [], totals, not_current)
    pool = [r for a in matching for r in a.records]
    page = pool[offset:offset + PAGE]
    more_left = len(pool) > offset + PAGE
    picked = {str(r.ref) for r in page}
    for a in matching:
        a.records = [r for r in a.records if str(r.ref) in picked]
    hidden = [a for a in ordered if a not in matching]
    return Directory("matched", matching, hidden, topic, page, totals, not_current,
                     {"topic": len(pool) - offset - len(page)} if more_left else {},
                     _token(user_id, topic, offset + PAGE) if more_left else None)


def _count_words(counts: dict[str, int]) -> str:
    parts = []
    for kind in ("fact", "episode", "item"):
        n = counts.get(kind, 0)
        if n:
            one, many = STORE_WORD[kind]
            parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) or "nothing"


def render(d: Directory, *, now: datetime, zone: str | None) -> str:
    """The rundown as she reads it: complete only when it says so."""
    from app.services import recall

    if d.status == "failed":
        return "Reading what you know FAILED just now: don't say nothing is recorded."
    tz = recall._zone(zone)
    head = f"What you know about them, by area (memory as it stands now: {_count_words(d.totals)})"
    if d.topic is None and d.status == "no_match":
        return head + ":\nNothing is stored about them yet."
    if d.topic is not None and d.status == "no_match":
        areas = ", ".join(f"{a.label} ({_count_words(a.counts())})" for a in d.hidden) or "none"
        return (f'{head}:\nNo area matches "{d.topic}". The areas there are: {areas}. Try one of these, or '
                "search_memories for something specific.")
    lines = [head + (f', about "{d.topic}"' if d.topic else "") + ":"]
    for a in d.areas:
        about = f" ({', '.join(a.names[:6])})" if a.names else ""
        counted = f" · {_count_words(a.counts())}" if d.topic is None else ""
        lines.append(f"## {a.label}{about}{counted}")
        lines += [recall._record_line(r, now, tz) for r in (a.records if d.topic else a.records[:EXAMPLES])]
        if d.topic is None and d.more_in_area.get(a.key):
            lines.append(f"  (+{d.more_in_area[a.key]} more here, not shown: ask what_i_know about \"{a.label}\")")
    gaps = []
    if d.topic is None and d.hidden:
        gaps.append("areas not shown: " + ", ".join(f"{a.label} ({_count_words(a.counts())})" for a in d.hidden))
    if d.topic is not None and d.more_in_area.get("topic"):
        gaps.append(f"{d.more_in_area['topic']} more records in this area on the next page")
    if d.topic is None and d.more_in_area:
        gaps.append(f"{sum(d.more_in_area.values())} more records in the areas above")
    if d.topic is not None and d.hidden:
        gaps.append("other areas: " + ", ".join(a.label for a in d.hidden))
    current = {k: v for k, v in d.not_current.items() if v}
    if current:
        gaps.append("not included, as no longer current: " + ", ".join(f"{v} {k}" for k, v in current.items()))
    if gaps:
        lines.append("Not everything is shown here (" + "; ".join(gaps) + "). Say there's more rather than "
                     "presenting this as all you know.")
    else:
        lines.append("That is everything in memory about them as it stands.")
    if d.next_page:
        lines.append(f"(More: page token {d.next_page})")
    return "\n".join(lines)


def _token(user_id: uuid.UUID, topic: str | None, offset: int) -> str:
    raw = json.dumps({"u": str(user_id), "t": topic, "o": offset, "k": "know"}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(token: str | None, user_id: uuid.UUID, topic: str | None) -> int:
    if not token:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode()))
        offset = int(data["o"])
    except Exception as exc:  # noqa: BLE001
        raise RecallInvalid("that page token isn't valid") from exc
    if data.get("u") != str(user_id) or data.get("t") != topic or data.get("k") != "know" or not 0 <= offset <= 10000:
        raise RecallInvalid("that page token belongs to a different rundown")
    return offset
