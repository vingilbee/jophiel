"""Who or what memories are about (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md §3-4, stage 3).

An entity is the user's sister, their dog, a pottery class: claims point at it, plans and episodes link to it.
The extractor sees the known ones as short refs (``e1``) and names one only when the message makes clear
it's that one; code still checks the message (or the few before it) mentions it by name or by what it is to
the user, and otherwise attaches nothing. A mention with no ref is a new entity, except one with the same name
and the same relation as a known one ("my sister Nora" again). Two of the same name never merge: recall says
which records are ambiguous (``ambiguous``). Renaming follows a name claim (``rename``); the old names stay.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import MemoryEntity, MemoryEntityLink, MemoryEvidence, SemanticMemory
from app.schemas.long_term_memory import EntityMention

log = logging.getLogger(__name__)

KNOWN_SHOWN = 40  # entities shown to the extractor (the ones the message mentions come first)
_WORD = re.compile(r"[a-z0-9]+")


def _base(word: str) -> str:
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def bases(text: str | None) -> set[str]:
    """Content words as bases ("sisters" -> "sister"), the same reading recall uses."""
    from app.services.long_term_memory import _STOPWORDS

    return {_base(w) for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and " ".join(a.lower().split()) == " ".join(b.lower().split())


def mentioned(entity: MemoryEntity, text: str) -> bool:
    """The text names it, or says what it is to the user ("my sister", "the dog")."""
    words = bases(text)
    return bool((bases(entity.name) & words) or (bases(entity.relation) & words))


def label(entity: MemoryEntity) -> str:
    """How she reads it: "their sister Nora", "their dog Pretzel", "Leo"."""
    if entity.relation and entity.name:
        return f"their {entity.relation} {entity.name}"
    return entity.name or f"their {entity.relation}"


def known(db: Session, user_id: uuid.UUID, text: str) -> list[MemoryEntity]:
    """The active entities shown to the extractor: the ones the message (with its context) mentions, then the
    most recently touched, up to ``KNOWN_SHOWN``."""
    rows = list(db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == user_id,
                                                      MemoryEntity.status == "active")
                           .order_by(MemoryEntity.updated_at.desc()).limit(500)))
    first = [e for e in rows if mentioned(e, text)]
    rest = [e for e in rows if e not in first]
    return (first + rest)[:KNOWN_SHOWN]


def refs(entities: list[MemoryEntity]) -> dict[str, MemoryEntity]:
    return {f"e{i}": entity for i, entity in enumerate(entities, 1)}


def lines(by_ref: dict[str, MemoryEntity]) -> list[str]:
    out = []
    for ref, e in by_ref.items():
        parts = [e.kind]
        if e.relation:
            parts.append(e.relation)
        if e.name:
            parts.append(f"named {e.name}")
        earlier = [n for n in (e.metadata_ or {}).get("names", []) if not _same(n, e.name)]
        if earlier:
            parts.append("earlier called " + ", ".join(earlier[-3:]))
        out.append(f"{ref}: " + ", ".join(parts))
    return out


_OWNER = re.compile(r"^(?:the\s+)?(?:user['’]s|users['’]|my|their|his|her|our)\s+")


def _relation(text: str | None) -> str | None:
    """What it is to the user, in plain words: "user's parrot" -> "parrot", "my grandma's parrot" -> "grandma's
    parrot"."""
    words = " ".join((text or "").lower().split())
    while True:
        stripped = _OWNER.sub("", words)
        if stripped == words:
            break
        words = stripped
    return words or None


class Resolver:
    """One extraction's entity resolution: the refs it was shown, the words it may rely on, and the new
    entities it made (one per distinct mention, however many claims name it)."""

    def __init__(self, db: Session, user_id: uuid.UUID, by_ref: dict[str, MemoryEntity], context_text: str,
                 message_id: uuid.UUID | None):
        self.db, self.user_id, self.by_ref = db, user_id, by_ref
        self.context_text, self.message_id = context_text, message_id
        self.made: dict[tuple, MemoryEntity] = {}
        self.refused: list[str] = []

    def resolve(self, mention: EntityMention | None) -> MemoryEntity | None:
        if mention is None:
            return None
        name = " ".join((mention.name or "").split()) or None
        relation = _relation(mention.relation)
        ref = (mention.ref or "").strip().lower() or None
        if ref is not None:
            entity = self.by_ref.get(ref)
            if entity is None or entity.status != "active":
                self.refused.append(f"{ref}: not a known entity")
                return None
            if relation and entity.relation and not (bases(relation) & bases(entity.relation)):
                # Said to be something else to the user ("my coworker Ines", not the sister): a different one.
                self.refused.append(f"{ref}: it's their {entity.relation}, the message says {relation}")
                return self.resolve(mention.model_copy(update={"ref": None}))
            if not (mentioned(entity, self.context_text)
                    or (name and _same(name, entity.name)) or (relation and _same(relation, entity.relation))):
                # The model says it's that one, but nothing in the words says so: attach nothing.
                self.refused.append(f"{ref}: the message doesn't mention it")
                return None
            if relation and not entity.relation:
                entity.relation = relation
            if name and not entity.name:
                self._name(entity, name)
            return entity
        if not (name or relation):
            return None
        key = (mention.kind, (name or "").lower(), relation or "")
        if key in self.made:
            return self.made[key]
        if name and relation:
            # The same name and the same relation is the same one ("my sister Nora" again): clear enough.
            same = [e for e in self.by_ref.values() if _same(e.name, name) and _same(e.relation, relation)]
            if len(same) == 1:
                self.made[key] = same[0]
                return same[0]
        entity = MemoryEntity(id=uuid.uuid4(), user_id=self.user_id, kind=mention.kind, name=name, relation=relation,
                              source_message_id=self.message_id, metadata_={"names": [name] if name else []})
        self.db.add(entity)
        self.db.flush()
        if self.message_id is not None:
            evidence(self.db, self.user_id, role="origin", message_id=self.message_id, entity_id=entity.id)
        self.made[key] = entity
        return entity

    def _name(self, entity: MemoryEntity, name: str) -> None:
        meta = dict(entity.metadata_ or {})
        names = [n for n in meta.get("names", []) if not _same(n, name)]
        meta["names"] = [*names, name][-6:]
        entity.metadata_ = meta
        entity.name = name


def rename(resolver: Resolver, entity: MemoryEntity, name: str) -> None:
    """A name claim changed what it's called: the entity follows it, and keeps the old name in its history."""
    name = " ".join(name.split())
    if name and not _same(name, entity.name):
        resolver._name(entity, name)
        entity.updated_at = datetime.now(timezone.utc)


def link(db: Session, user_id: uuid.UUID, entity: MemoryEntity, *, role: str, item_id: uuid.UUID | None = None,
         episode_id: uuid.UUID | None = None) -> None:
    db.execute(insert(MemoryEntityLink).values(user_id=user_id, entity_id=entity.id, item_id=item_id,
                                               episode_id=episode_id, role=role).on_conflict_do_nothing())


def evidence(db: Session, user_id: uuid.UUID, *, role: str, message_id: uuid.UUID, fact_id: uuid.UUID | None = None,
             entity_id: uuid.UUID | None = None) -> None:
    """A fact's or an entity's evidence link to the user's own message (never another user's: checked)."""
    from app.services import memory_manager

    memory_manager.link_target(db, user_id, role=role, source=memory_manager.Source("message", message_id),
                               fact_id=fact_id, entity_id=entity_id)


def about(db: Session, user_id: uuid.UUID, *, facts: list[uuid.UUID] = (), items: list[uuid.UUID] = (),
          episodes: list[uuid.UUID] = ()) -> dict[uuid.UUID, list[MemoryEntity]]:
    """The entities each record is about or involves, by record id."""
    out: dict[uuid.UUID, list[MemoryEntity]] = {}
    if facts:
        for fact_id, entity in db.execute(select(SemanticMemory.id, MemoryEntity).join(
                MemoryEntity, MemoryEntity.id == SemanticMemory.entity_id).where(
                SemanticMemory.id.in_(list(facts)), SemanticMemory.user_id == user_id,
                MemoryEntity.status == "active")).all():
            out.setdefault(fact_id, []).append(entity)
    targets = ([MemoryEntityLink.item_id.in_(list(items))] if items else []) + (
        [MemoryEntityLink.episode_id.in_(list(episodes))] if episodes else [])
    if targets:
        rows = db.execute(select(MemoryEntityLink, MemoryEntity).join(
            MemoryEntity, MemoryEntity.id == MemoryEntityLink.entity_id).where(
            MemoryEntityLink.user_id == user_id, MemoryEntity.status == "active", or_(*targets))
            .order_by(MemoryEntityLink.id)).all()
        for link_row, entity in rows:
            key = link_row.item_id or link_row.episode_id
            if entity not in out.setdefault(key, []):
                out[key].append(entity)
    return out


def ambiguous(db: Session, user_id: uuid.UUID, query_bases: set[str], pointers: set[str]) -> list[str]:
    """What the message refers to that fits more than one entity: a name two of them share, or "my sister"
    with two sisters known. Recall says so and never picks one."""
    if not (query_bases or pointers):
        return []
    rows = list(db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == user_id,
                                                      MemoryEntity.status == "active")))
    found = []
    for word in sorted(query_bases):
        named = [e for e in rows if word in bases(e.name)]
        if len(named) > 1:
            found.append(word)
    for pointer in sorted(pointers):
        related = [e for e in rows if pointer in bases(e.relation)]
        if len(related) > 1:
            found.append(pointer)
    return sorted(set(found))


def prune(db: Session, user_id: uuid.UUID, entity_ids: set[uuid.UUID]) -> int:
    """Remove entities nothing is about any more (after forgetting): no fact, no plan or episode link. Their
    names go with them, so a forgotten person isn't kept as a name."""
    removed = 0
    for entity_id in entity_ids:
        facts = db.scalar(select(func.count()).select_from(SemanticMemory).where(
            SemanticMemory.entity_id == entity_id, SemanticMemory.status != "deleted"))
        links = db.scalar(select(func.count()).select_from(MemoryEntityLink).where(
            MemoryEntityLink.entity_id == entity_id))
        if facts or links:
            continue
        entity = db.scalar(select(MemoryEntity).where(MemoryEntity.id == entity_id, MemoryEntity.user_id == user_id))
        if entity is not None:
            db.execute(MemoryEvidence.__table__.delete().where(MemoryEvidence.entity_id == entity_id))
            db.delete(entity)
            removed += 1
    return removed
