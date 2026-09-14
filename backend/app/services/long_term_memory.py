"""Long-term memory extraction, persistence, retrieval, and job scheduling."""
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
from app.db.models import ChatMessage, LongTermMemory, MemoryJob
from app.db.session import SessionLocal
from app.schemas.long_term_memory import MemoryCandidate, MemoryExtraction

logger = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[a-z0-9]+")

_EXTRACTION_PROMPT = """You maintain long-term memory for a personal companion and general-purpose assistant.
Extract only durable, future-useful information explicitly stated or corrected by the USER.
Good memories include stable preferences, goals, constraints, explicit personal facts, important
episodes, relationships, ongoing projects, routines, and interaction preferences. Ignore greetings,
one-off questions, assistant suggestions, guesses, transient moods, and facts already represented by
an authoritative application record. Never infer diagnoses, sensitive traits, relationships, or
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
    assistant_message_id: uuid.UUID,
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
    return user_message.content, assistant.content, user_message.id


def _extract(user_text: str, assistant_text: str) -> list[MemoryCandidate]:
    response = _client().responses.parse(
        model=get_settings().memory_model,
        input=[
            {"role": "system", "content": _EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": f"USER:\n{user_text}\n\nASSISTANT:\n{assistant_text}",
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
        candidates = _extract(turn[0], turn[1]) if turn else []
        source_message_id = turn[2] if turn else None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for candidate in candidates:
            key = candidate.normalized_key.strip().lower()
            existing = db.scalar(
                select(LongTermMemory).where(
                    LongTermMemory.user_id == job.user_id,
                    LongTermMemory.normalized_key == key,
                )
            )
            if candidate.operation == "forget":
                if existing is not None:
                    existing.status = "deleted"
                    existing.last_confirmed_at = now
                continue
            vector = _embedding(candidate.content)
            if existing is None:
                existing = LongTermMemory(user_id=job.user_id, normalized_key=key)
                db.add(existing)
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
                    select(LongTermMemory).where(
                        LongTermMemory.user_id == user_id,
                        LongTermMemory.status == "active",
                        or_(LongTermMemory.expires_at.is_(None), LongTermMemory.expires_at > now),
                    )
                )
            )
            if not memories:
                return None
            query_vector = _embedding(query)
            query_words = set(_WORD_RE.findall(query.lower()))
            ranked: list[tuple[float, LongTermMemory]] = []
            for memory in memories:
                memory_words = set(_WORD_RE.findall(memory.content.lower()))
                lexical = len(query_words & memory_words) / max(1, len(query_words))
                semantic = max(0.0, _cosine(query_vector, memory.embedding))
                # Important constraints remain recallable even without a close
                # wording match; semantic and lexical relevance dominate.
                score = semantic * 0.55 + lexical * 0.25 + memory.importance * 0.15 + memory.confidence * 0.05
                if semantic >= 0.2 or lexical > 0 or memory.importance >= 0.85:
                    ranked.append((score, memory))
            selected = [item for _, item in sorted(ranked, key=lambda x: x[0], reverse=True)[:settings.memory_retrieval_limit]]
            if not selected:
                return None
            for memory in selected:
                memory.last_accessed_at = now
                memory.access_count += 1
            db.commit()
            return "\n".join(f"- [{memory.kind}] {memory.content}" for memory in selected)
    except Exception:
        logger.exception("Long-term memory retrieval failed; continuing without it")
        return None
