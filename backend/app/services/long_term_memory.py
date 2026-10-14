"""Semantic-memory extraction, persistence, retrieval, and job scheduling.

The public function names remain stable while the existing long-term fact store
is migrated to the more precise ``semantic_memories`` table.
"""
from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
import secrets

import redis
from openai import OpenAI
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatMessage, EpisodicMemory, MemoryJob, SemanticMemory
from app.db.session import SessionLocal
from app.schemas.long_term_memory import MemoryCandidate, MemoryExtraction
from app.services.heard import heard_text

logger = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[a-z0-9]+")

_EXTRACTION_PROMPT = """You maintain long-term memory for a personal companion and general-purpose assistant.
Extract only durable, future-useful information explicitly stated or corrected by the USER.
Good memories include stable preferences, goals, constraints, explicit personal facts, important
episodes, relationships, ongoing projects, routines, and interaction preferences. Ignore greetings,
one-off questions, assistant suggestions, guesses, transient moods, and facts already represented by
an authoritative application record.
Games: the assistant plays games with the user. Never store the game's state: what the assistant or the
user carries, has, or is doing in the game, where things are in a world, the time of day there. It changes
by the minute and the game records it. One-off game tasks ("collect five oak logs", "build a house", "stop
playing", "follow me") aren't the user's goals: skip them; so are tips on how to do a game action ("you can
break blocks"): the game learns those itself. But do store what lasts about playing together: games the user
plays with the assistant and likes, and standing wishes about how the assistant should be with them while
playing, especially said more than once ("tell me when you find something", "warn me about mobs",
"talk more") as `preference:` items.
The USER text is often a speech transcript and can be misheard: never store a fact from a phrase that doesn't
make sense in context (an odd brand or name in the middle of a game, a fragment), and when unsure store nothing.
EARLIER messages, when given, are context for understanding the latest exchange only: extract from the latest
USER message, and use earlier ones only to see what it refers to or that it repeats an earlier wish.
KNOWN memories, when given, are what is already stored: for the same thing, reuse its normalized_key exactly
(updated wording is fine) instead of making a new key; don't repeat one that adds nothing new.
Never infer diagnoses, sensitive traits, relationships, or
emotional conditions. A model interpretation is not a user fact. Use `forget` only when the user
explicitly retracts something or asks for it to be forgotten. Give related facts a stable lowercase
namespaced normalized_key such as `preference:communication_style`, `relationship:sister_name`,
`project:nutrition_app`, `routine:morning_workout`, or `goal:learn_japanese`. Current explicit
statements override older ones. Return no more than 8 items and return an empty list when nothing
deserves durable memory."""


def _client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else OpenAI()


def _embedding(text: str) -> list[float] | None:
    try:
        result = _client().embeddings.create(
            model=get_settings().memory_embedding_model,
            input=text,
        )
        return list(result.data[0].embedding)
    except Exception:
        logger.warning("Memory embedding failed; continuing with lexical retrieval", exc_info=True)
        return None


def schedule_consolidation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID, commit: bool = True,
) -> MemoryJob | None:
    """Create an idempotent durable job and best-effort wake the worker."""
    if not get_settings().long_term_memory_enabled:
        return None
    existing = db.scalar(
        select(MemoryJob).where(MemoryJob.assistant_message_id == assistant_message_id)
    )
    if existing is not None:
        return existing
    job = MemoryJob(
        user_id=user_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
    )
    db.add(job)
    if not commit:
        db.flush()
        return job
    db.commit()
    db.refresh(job)
    settings = get_settings()
    if settings.redis_url:
        try:
            redis.from_url(settings.redis_url).rpush(settings.memory_queue_name, str(job.id))
        except Exception:
            # Postgres is the durable queue; polling will still discover it.
            logger.warning("Could not wake memory worker through Redis", exc_info=True)
    return job


def _turn_for_job(db: Session, job: MemoryJob) -> tuple[str, str, uuid.UUID] | None:
    assistant = db.get(ChatMessage, job.assistant_message_id)
    if assistant is None:
        return None
    user_message = db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == job.conversation_id,
            ChatMessage.role == "user",
            ChatMessage.created_at <= assistant.created_at,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    if user_message is None:
        return None
    # Learn only from what the user heard: unheard text was never said to them.
    return (user_message.content, heard_text(assistant.content, assistant.heard_chars),
            user_message.id)


EARLIER_MESSAGES = 6  # before the latest exchange, so "yes, always" or a repeated wish reads right
EARLIER_CHARS = 300


def _earlier_messages(db: Session, conversation_id: uuid.UUID, before_message_id: uuid.UUID) -> list[str]:
    """The few messages before the extracted exchange, oldest first, as "USER: ..." lines."""
    before = db.get(ChatMessage, before_message_id)
    if before is None:
        return []
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.created_at < before.created_at)
        .order_by(ChatMessage.created_at.desc())
        .limit(EARLIER_MESSAGES)
    ).all()
    lines = []
    for message in reversed(rows):
        text = heard_text(message.content, message.heard_chars) if message.role == "assistant" else message.content
        text = " ".join(text.split())[:EARLIER_CHARS]
        if text:
            lines.append(f"{message.role.upper()}: {text}")
    return lines


KNOWN_SHOWN = 60  # stored memories shown to the extractor, so the same thing keeps its key


def _known(db: Session, user_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(SemanticMemory)
        .where(SemanticMemory.user_id == user_id, SemanticMemory.status == "active")
        .order_by(SemanticMemory.last_confirmed_at.desc())
        .limit(KNOWN_SHOWN)
    ).all()
    return [f"{m.normalized_key}: {' '.join(m.content.split())[:160]}" for m in rows]


def _extract(user_text: str, assistant_text: str, earlier: list[str] = (),
             known: list[str] = ()) -> list[MemoryCandidate]:
    context = ("KNOWN:\n" + "\n".join(known) + "\n\n") if known else ""
    context += ("EARLIER (context only):\n" + "\n".join(earlier) + "\n\n") if earlier else ""
    response = _client().responses.parse(
        model=get_settings().memory_model,
        input=[
            {"role": "system", "content": _EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": f"{context}USER:\n{user_text}\n\nASSISTANT:\n{assistant_text}",
            },
        ],
        text_format=MemoryExtraction,
    )
    parsed = response.output_parsed
    return parsed.memories if parsed is not None else []


def process_job(db: Session, job_id: uuid.UUID) -> bool:
    """Atomically claim and process one job, reclaiming expired leases safely."""
    now = datetime.now(timezone.utc)
    claim_token = secrets.token_urlsafe(32)
    job = db.scalar(
        select(MemoryJob).where(
            MemoryJob.id == job_id,
            or_(
                MemoryJob.status == "pending",
                (MemoryJob.status == "processing")
                & (MemoryJob.lease_expires_at.is_(None) | (MemoryJob.lease_expires_at <= now)),
            ),
        ).with_for_update(skip_locked=True)
    )
    if job is None:
        return False
    job.status = "processing"
    job.attempts += 1
    job.claim_token = claim_token
    job.lease_expires_at = now + timedelta(minutes=15)
    db.commit()
    try:
        turn = _turn_for_job(db, job)
        earlier = _earlier_messages(db, job.conversation_id, turn[2]) if turn else []
        candidates = _extract(turn[0], turn[1], earlier, _known(db, job.user_id)) if turn else []
        source_message_id = turn[2] if turn else None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for candidate in candidates:
            key = candidate.normalized_key.strip().lower()
            existing = db.scalar(
                select(SemanticMemory).where(
                    SemanticMemory.user_id == job.user_id,
                    SemanticMemory.normalized_key == key,
                )
            )
            if candidate.operation == "forget":
                if existing is not None:
                    existing.status = "deleted"
                    existing.last_confirmed_at = now
                    existing.revision += 1
                continue
            vector = _embedding(candidate.content)
            if candidate.kind == "episode":
                db.add(EpisodicMemory(
                    user_id=job.user_id,
                    conversation_id=job.conversation_id,
                    summary=candidate.content.strip(),
                    source_message_ids=[str(source_message_id)] if source_message_id else [],
                    embedding=vector,
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    context={"normalized_key": key},
                ))
                continue
            if existing is None:
                existing = SemanticMemory(user_id=job.user_id, normalized_key=key)
                db.add(existing)
            else:
                existing.evidence_count += 1
                existing.revision += 1
            existing.kind = candidate.kind
            existing.content = candidate.content.strip()
            existing.embedding = vector
            existing.importance = candidate.importance
            existing.confidence = candidate.confidence
            existing.status = "active"
            existing.source_conversation_id = job.conversation_id
            existing.source_message_id = source_message_id
            existing.last_confirmed_at = now
        # Completion is accepted only from the worker that still owns this
        # lease. Use a conditional UPDATE so a concurrent reclaim cannot be
        # defeated by a stale ORM object in this session.
        completion = db.execute(
            update(MemoryJob)
            .where(MemoryJob.id == job_id, MemoryJob.claim_token == claim_token)
            .values(
                status="completed", claim_token=None, lease_expires_at=None,
                processed_at=now, last_error=None,
            )
        )
        if completion.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        job = db.scalar(select(MemoryJob).where(
            MemoryJob.id == job_id, MemoryJob.claim_token == claim_token
        ))
        if job is not None:
            job.status = "pending" if job.attempts < 3 else "failed"
            job.claim_token = None
            job.lease_expires_at = None
            job.last_error = str(exc)[:2000]
            db.commit()
        logger.exception("Memory job %s failed", job_id)
        return False


def next_pending_job_id(db: Session) -> uuid.UUID | None:
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(MemoryJob.id)
        .where(
            or_(
                MemoryJob.status == "pending",
                (MemoryJob.status == "processing")
                & (MemoryJob.lease_expires_at.is_(None) | (MemoryJob.lease_expires_at <= now)),
            )
        )
        .order_by(MemoryJob.created_at)
        .limit(1)
    )


def _cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / norm if norm else 0.0


EPISODE_CANDIDATES = 200
# What counts as relevant, measured on real messages (text-embedding-3-small): a message about
# a memory scores 0.3+ ("remember when you died in the cave?" 0.44); small talk scores up to
# ~0.24 against any game recap ("how was your day?", "I'm so tired from work today").
MIN_SIMILARITY = 0.3
# ...or most of the message's own words appear in it (embeddings off, or a name the model blurs).
MIN_WORD_OVERLAP = 0.5
MAX_EPISODES = 3  # of memory_retrieval_limit: recaps alone never fill the block
RECENCY_DAYS = 3.0  # an episode's recency bonus halves every this many days
# Only these may come along without matching the message, and only when this important.
ALWAYS_KINDS = frozenset({"preference", "constraint"})
ALWAYS_IMPORTANCE = 0.85
_STOPWORDS = frozenset("""a an and are as at be but by can did do does for from had has have he her him his how i
if in is it its just let me my no not of oh ok okay on or our she so that the their them then there they this to
up us was we were what when where which who why will with would yeah yes you your""".split())


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _memory_line(memory: SemanticMemory | EpisodicMemory) -> str:
    if isinstance(memory, EpisodicMemory):
        source = (memory.context or {}).get("source")
        label = f"episode, {memory.event_at:%Y-%m-%d}" + (f", {source}" if source else "")
        return f"- [{label}] {memory.summary}"
    return f"- [{memory.kind}] {memory.content}"


def retrieve_for_prompt(user_id: uuid.UUID, query: str) -> str | None:
    """Return a compact ranked memory block; failures never break chat."""
    settings = get_settings()
    if not settings.long_term_memory_enabled:
        return None
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with SessionLocal() as db:
            memories = list(
                db.scalars(
                    select(SemanticMemory).where(
                        SemanticMemory.user_id == user_id,
                        SemanticMemory.status == "active",
                        or_(SemanticMemory.expires_at.is_(None), SemanticMemory.expires_at > now),
                    )
                )
            )
            # Episodes (e.g. a game session she reflected on) compete with facts
            # on the same score; only the most recent ones are considered.
            episodes = list(
                db.scalars(
                    select(EpisodicMemory)
                    .where(EpisodicMemory.user_id == user_id, EpisodicMemory.status == "active")
                    .order_by(EpisodicMemory.event_at.desc())
                    .limit(EPISODE_CANDIDATES)
                )
            )
            if not memories and not episodes:
                return None
            query_vector = _embedding(query)
            query_words = _content_words(query)
            ranked: list[tuple[float, SemanticMemory | EpisodicMemory]] = []
            for memory in [*memories, *episodes]:
                episode = isinstance(memory, EpisodicMemory)
                content = memory.summary if episode else memory.content
                lexical = len(query_words & _content_words(content)) / max(1, len(query_words))
                semantic = max(0.0, _cosine(query_vector, memory.embedding))
                # Recent episodes first among equals: "last time" means the latest one.
                recency = 0.0
                if episode:
                    event_at = memory.event_at if memory.event_at.tzinfo else memory.event_at.replace(tzinfo=timezone.utc)
                    age_days = max(0.0, (datetime.now(timezone.utc) - event_at).total_seconds() / 86400)
                    recency = 0.5 ** (age_days / RECENCY_DAYS)
                score = (semantic * 0.55 + lexical * 0.25 + memory.importance * 0.1 + recency * 0.1
                         + memory.confidence * 0.05)
                # Standing preferences and constraints stay recallable without a close match.
                always = not episode and memory.kind in ALWAYS_KINDS and memory.importance >= ALWAYS_IMPORTANCE
                if semantic >= MIN_SIMILARITY or (query_words and lexical >= MIN_WORD_OVERLAP) or always:
                    ranked.append((score, memory))
            selected: list[SemanticMemory | EpisodicMemory] = []
            shown_episodes = 0
            for _, item in sorted(ranked, key=lambda x: x[0], reverse=True):
                if isinstance(item, EpisodicMemory):
                    if shown_episodes >= MAX_EPISODES:
                        continue
                    shown_episodes += 1
                selected.append(item)
                if len(selected) >= settings.memory_retrieval_limit:
                    break
            if not selected:
                return None
            for memory in selected:
                memory.last_accessed_at = now
                memory.access_count += 1
            db.commit()
            return "\n".join(_memory_line(memory) for memory in selected)
    except Exception:
        logger.exception("Long-term memory retrieval failed; continuing without it")
        return None
