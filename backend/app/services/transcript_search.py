"""Transcript search (docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): what was actually said in the user's own
conversations, by whom, as far as it was heard. History, not memory: a passage is what was said, never a checked
fact, and finding it never makes memory learn it again.

What's searchable: the user's messages; her replies as far as the user heard them (``heard_chars``; a reply that
failed or was cancelled with nothing delivered isn't searchable at all); her unprompted messages. The message
being answered is left out. A message something was forgotten from (``memory_forgotten_sources``) is still
history the user can ask about, and says so: the record is marked ``forgotten``. A deleted conversation or
message is gone.

Matching is Postgres full text (``chat_messages.search_tsv``, English stemming) over the heard text: first every
word of the query (``websearch_to_tsquery``), then any of them, ranked by ``ts_rank_cd`` with a small recency
term; then, when words found too little, meaning (``chat_message_embeddings``, filled in the background by
``embed_run_once``; a vector whose heard text changed since is ignored), for questions worded unlike the answer
("the colours in the evening sky" for an answer about sunsets and scattering). The stored vector can end in a half-heard word, so every hit on an interrupted reply is checked again
against the heard text snapped back to a whole word (``heard.heard_cut``), and dropped if it no longer matches.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from app.ai.recall.contracts import MemoryRecord, MemoryRef, RecallInvalid, RecallResult, TimeRange
from app.db.models import ChatMessage, ChatMessageEmbedding, Conversation, MemoryForgottenSource
from app.services import heard

log = logging.getLogger(__name__)

LIMIT = 8  # passages per page
CANDIDATES = 60  # hits read per query before checking and ranking
SNIPPET = 300  # characters around the match
READ_CHARS = 1500  # a passage read by its reference
RECENCY_DAYS = 60
MEANING_MIN = 0.3  # cosine: paraphrases of an answer score ~0.45-0.5, unrelated talk under ~0.2
MEANING_READ = 3000  # newest embedded messages compared with a query
EMBED_CHARS = 2000
MAX_EMBED_ATTEMPTS = 5
_TERM = re.compile(r"[A-Za-z0-9]+")
_MARK = re.compile(r"<<|>>")
WHO = {"me": "assistant", "columbina": "assistant", "user": "user"}

# Who said it and whether it reached anyone: the user's messages, and her replies that were delivered or heard.
_ELIGIBLE = """
    c.user_id = :user_id AND m.content <> ''
    AND (m.role = 'user' OR (m.role = 'assistant'
         AND (m.status IN ('completed', 'partial') OR COALESCE(m.heard_chars, 0) > 0)))
    AND COALESCE(m.heard_chars, 1) > 0
"""


def _naive_utc(moment: datetime | None) -> datetime | None:
    """``chat_messages.created_at`` is naive UTC."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def terms(query: str) -> list[str]:
    """The query's words, as safe tsquery terms (letters and digits only)."""
    seen: list[str] = []
    for word in _TERM.findall(query or ""):
        word = word.lower()
        if len(word) > 1 and word not in seen:
            seen.append(word)
    return seen[:32]


def _tsqueries(db: Session, query: str) -> tuple[str | None, str | None]:
    """Every word (websearch syntax: quotes and -words work) and any word, as tsquery text; None when empty
    (only stop words, or nothing searchable)."""
    words = terms(query)
    if not words:
        return None, None
    every = db.scalar(text("SELECT CAST(websearch_to_tsquery('english', :q) AS text)"), {"q": query[:500]})
    some = db.scalar(text("SELECT CAST(to_tsquery('english', :q) AS text)"), {"q": " | ".join(words)})
    return (every or None), (some or None)


def _hits(db: Session, user_id: uuid.UUID, tsq: str, *, role: str | None, conversation_id: uuid.UUID | None,
          time_range: TimeRange | None, exclude: list[uuid.UUID], cap: int) -> list[tuple[uuid.UUID, float]]:
    where = [_ELIGIBLE, "m.search_tsv @@ q.q", "NOT (m.id = ANY(:exclude))"]
    params: dict = {"user_id": user_id, "tsq": tsq, "exclude": exclude, "cap": cap}
    if role:
        where.append("m.role = :role")
        params["role"] = role
    if conversation_id:
        where.append("m.conversation_id = :conversation_id")
        params["conversation_id"] = conversation_id
    if time_range is not None:
        if time_range.start is not None:
            where.append("m.created_at >= :start")
            params["start"] = _naive_utc(time_range.start)
        if time_range.end is not None:
            where.append("m.created_at < :end")
            params["end"] = _naive_utc(time_range.end)
    sql = text(f"""
        SELECT m.id, ts_rank_cd(m.search_tsv, q.q) AS rank
        FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id,
             (SELECT CAST(:tsq AS tsquery) AS q) q
        WHERE {' AND '.join(where)}
        ORDER BY rank DESC, m.created_at DESC
        LIMIT :cap
    """).bindparams(bindparam("exclude", type_=ARRAY(UUID(as_uuid=True))))
    return [(row[0], float(row[1])) for row in db.execute(sql, params)]


def _heard_body(message: ChatMessage) -> tuple[str, bool]:
    """The text that was heard (never the interruption marker), and whether it was cut off."""
    content = message.content or ""
    if message.role != "assistant":
        return content, False
    cut = heard.heard_cut(content, message.heard_chars)
    return content[:cut].rstrip(), cut < len(content)


def _checked(db: Session, tsq: str, bodies: dict[uuid.UUID, str]) -> dict[uuid.UUID, str | None]:
    """Per message: a headline fragment when the heard text really matches, else None."""
    if not bodies:
        return {}
    ids, texts = list(bodies), [bodies[i] for i in bodies]
    sql = text("""
        SELECT t.id, to_tsvector('english', t.body) @@ CAST(:tsq AS tsquery) AS ok,
               ts_headline('english', t.body, CAST(:tsq AS tsquery),
                           'MaxWords=45, MinWords=15, MaxFragments=1, StartSel=<<, StopSel=>>')
        FROM unnest(:ids, :bodies) AS t(id, body)
    """).bindparams(bindparam("ids", type_=ARRAY(UUID(as_uuid=True))), bindparam("bodies", type_=ARRAY(Text())))
    out: dict[uuid.UUID, str | None] = {}
    for message_id, ok, headline in db.execute(sql, {"tsq": tsq, "ids": ids, "bodies": texts}):
        out[message_id] = headline if ok else None
    return out


def _snippet(body: str, headline: str | None) -> str:
    """About ``SNIPPET`` characters of the heard text around the match, cut at word boundaries."""
    body = body.strip()
    if len(body) <= SNIPPET + 20:
        return body
    centre = 0
    if headline:
        marked = headline.find("<<")
        fragment = _MARK.sub("", headline).strip()
        at = body.find(fragment)
        if at >= 0:
            centre = at + max(0, marked)
        elif marked >= 0:
            word = _MARK.sub("", headline[marked:].split(">>", 1)[0])
            found = body.lower().find(word.lower())
            centre = found if found >= 0 else 0
    start = max(0, centre - SNIPPET // 3)
    end = min(len(body), start + SNIPPET)
    start = max(0, end - SNIPPET)
    if start > 0:
        space = body.find(" ", start)
        start = space + 1 if 0 <= space < start + 30 else start
    if end < len(body):
        space = body.rfind(" ", start, end)
        end = space if space > start + SNIPPET // 2 else end
    return ("…" if start > 0 else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def forgotten_ids(db: Session, user_id: uuid.UUID, message_ids) -> set[uuid.UUID]:
    ids = [m for m in message_ids if m is not None]
    if not ids:
        return set()
    return set(db.scalars(select(MemoryForgottenSource.message_id).where(
        MemoryForgottenSource.user_id == user_id, MemoryForgottenSource.message_id.in_(ids))))


def _fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:32]


def record(message: ChatMessage, title: str | None, *, content: str, cut_off: bool, forgotten: bool,
           matched: tuple = ("lexical",), score: float = 0.0) -> MemoryRecord:
    said = _aware(message.created_at)
    notes = []
    if cut_off:
        notes.append("the user stopped it here and didn't hear the rest")
    if forgotten:
        notes.append("something in it was asked to be forgotten")
    return MemoryRecord(
        ref=MemoryRef("message", str(message.id)), subkind="columbina" if message.role == "assistant" else "user",
        content=content, context_id=None, context_label=title, occurred_at=said, occurred_until=None,
        scheduled_at=None, recorded_at=said, updated_at=None, status="said", epistemic="said", evidence=None,
        fingerprint=_fingerprint("message", message.id, message.content, message.heard_chars), matched=matched,
        applicability="past", lifecycle="; ".join(notes) or None, score=score,
        conversation_id=message.conversation_id, forgotten=forgotten)


def _owned(db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[ChatMessage, str | None]]:
    if not ids:
        return {}
    rows = db.execute(select(ChatMessage, Conversation.title).join(
        Conversation, Conversation.id == ChatMessage.conversation_id).where(
        Conversation.user_id == user_id, ChatMessage.id.in_(ids))).all()
    return {message.id: (message, title) for message, title in rows}


def search(db: Session, user_id: uuid.UUID, query: str, *, now: datetime, who: str = "either",
           conversation_id: uuid.UUID | None = None, time_range: TimeRange | None = None,
           exclude: tuple[uuid.UUID, ...] = (), limit: int = LIMIT, page_token: str | None = None) -> RecallResult:
    """One bounded search of what was said. ``who``: "me" (Columbina), "user" or "either". Raises
    ``RecallInvalid`` for a request that can't be run as asked; a broken search is ``failed``."""
    started = time.monotonic()
    if len(query or "") > 2000:
        raise RecallInvalid("query is longer than 2000 characters")
    if who not in ("me", "columbina", "user", "either"):
        raise RecallInvalid("who is one of: me, user, either")
    role = WHO.get(who)
    limit = max(1, min(limit, LIMIT))
    key = _fingerprint(user_id, query, role, conversation_id, time_range)
    offset = _offset(page_token, user_id, key)
    coverage: dict = {"what": "what was said in your conversations, as far as it was heard"}
    every, some = _tsqueries(db, query)
    if every is None and some is None:
        return RecallResult("no_match", searched_kinds=("message",),
                            coverage=coverage | {"query": "no searchable words (only very common words)"})
    exclude_list = [e for e in exclude if e is not None]
    ranked: dict[uuid.UUID, float] = {}
    queries = {}
    for name, tsq, bonus in (("every", every, 1.0), ("some", some, 0.0)):
        if not tsq:
            continue
        for message_id, rank in _hits(db, user_id, tsq, role=role, conversation_id=conversation_id,
                                      time_range=time_range, exclude=exclude_list, cap=CANDIDATES):
            if message_id not in ranked:
                ranked[message_id] = rank + bonus
                queries[message_id] = tsq
        if name == "every" and len(ranked) >= offset + limit:
            break  # every word matched enough: no need for "any of them"
    meaning: dict[uuid.UUID, float] = {}
    if len(ranked) < offset + limit:
        meaning = _by_meaning(db, user_id, query, role=role, conversation_id=conversation_id, time_range=time_range,
                              exclude=exclude_list, coverage=coverage)
        for message_id, similarity in meaning.items():
            ranked.setdefault(message_id, similarity)
    rows = _owned(db, user_id, list(ranked))
    bodies, cut = {}, {}
    for message_id, (message, _title) in rows.items():
        bodies[message_id], cut[message_id] = _heard_body(message)
    checked: dict[uuid.UUID, str | None] = {}
    for tsq in set(queries.values()):
        checked.update(_checked(db, tsq, {i: bodies[i] for i in bodies if queries.get(i) == tsq}))
    for message_id in rows:
        if message_id not in queries:
            checked[message_id] = ""  # found by meaning, on its heard text (checked by fingerprint)
    dropped = sum(1 for i in rows if checked.get(i) is None)
    if dropped:
        coverage["dropped_unheard"] = dropped
    nowu = now.astimezone(timezone.utc)
    scored = []
    for message_id, (message, title) in rows.items():
        if checked.get(message_id) is None:
            continue
        age = max(0.0, (nowu - _aware(message.created_at)).total_seconds() / 86400)
        scored.append((ranked[message_id] + 0.1 * math.exp(-age / RECENCY_DAYS), message_id))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1])))
    page = scored[offset:offset + limit]
    forgotten = forgotten_ids(db, user_id, [m for _, m in page])
    records = []
    for score, message_id in page:
        message, title = rows[message_id]
        records.append(record(message, title, content=_snippet(bodies[message_id], checked[message_id] or None),
                              cut_off=cut[message_id], forgotten=message_id in forgotten, score=round(score, 4),
                              matched=("lexical",) if message_id in queries else ("semantic",)))
    if len(ranked) >= CANDIDATES:
        coverage["truncated"] = True
    more = len(scored) > offset + limit
    if more:
        coverage["more_matches"] = len(scored) - offset - limit
    coverage["ms"] = round((time.monotonic() - started) * 1000)
    scope = {"who": who, "conversation": str(conversation_id) if conversation_id else "all"}
    if time_range is not None:
        scope["time"] = {"start": time_range.start.isoformat() if time_range.start else None,
                         "end": time_range.end.isoformat() if time_range.end else None}
    _log({"op": "transcript_search", "returned": len(records), "candidates": len(ranked), "dropped": dropped,
          "who": who, "ms": coverage["ms"]})
    return RecallResult("matched" if records else "no_match", records, ("message",), scope, coverage,
                        next_page=_token(user_id, key, offset + limit) if more else None)


def _by_meaning(db: Session, user_id: uuid.UUID, query: str, *, role: str | None, conversation_id: uuid.UUID | None,
                time_range: TimeRange | None, exclude: list[uuid.UUID], coverage: dict) -> dict[uuid.UUID, float]:
    """Messages whose heard text means something close to the query, by embedding. Empty (and said in coverage)
    when the query can't be embedded or nothing is embedded yet."""
    from app.services import recall
    from app.services.long_term_memory import _cosine

    try:
        vector = recall.embed(query) if query.strip() else None
    except Exception:  # noqa: BLE001 - an outage leaves the search to words
        vector = None
    if vector is None:
        coverage["meaning"] = "not searched by meaning (no query embedding)"
        return {}
    statement = (select(ChatMessage, ChatMessageEmbedding.vector, ChatMessageEmbedding.fingerprint)
                 .join(ChatMessageEmbedding, ChatMessageEmbedding.message_id == ChatMessage.id)
                 .join(Conversation, Conversation.id == ChatMessage.conversation_id)
                 .where(Conversation.user_id == user_id, ChatMessageEmbedding.status == "done",
                        ChatMessage.content != ""))
    if role:
        statement = statement.where(ChatMessage.role == role)
    if conversation_id:
        statement = statement.where(ChatMessage.conversation_id == conversation_id)
    if time_range is not None and time_range.start is not None:
        statement = statement.where(ChatMessage.created_at >= _naive_utc(time_range.start))
    if time_range is not None and time_range.end is not None:
        statement = statement.where(ChatMessage.created_at < _naive_utc(time_range.end))
    if exclude:
        statement = statement.where(ChatMessage.id.not_in(exclude))
    rows = db.execute(statement.order_by(ChatMessage.created_at.desc()).limit(MEANING_READ)).all()
    found: dict[uuid.UUID, float] = {}
    for message, stored, fingerprint in rows:
        if not _delivered(message) or fingerprint != _embed_text(message)[1]:
            continue  # never reached them, or what was heard changed since it was embedded
        similarity = _cosine(vector, stored)
        if similarity >= MEANING_MIN:
            found[message.id] = similarity
    coverage["meaning"] = f"also searched by meaning ({len(rows)} embedded messages)"
    if len(rows) >= MEANING_READ:
        coverage["meaning_window"] = MEANING_READ
    return dict(sorted(found.items(), key=lambda pair: -pair[1])[:CANDIDATES])


def _delivered(message: ChatMessage) -> bool:
    if not message.content:
        return False
    if message.role != "assistant":
        return True
    if message.heard_chars == 0:
        return False
    return message.status in ("completed", "partial") or (message.heard_chars or 0) > 0


def _embed_text(message: ChatMessage) -> tuple[str, str]:
    body, _cut = _heard_body(message)
    body = body[:EMBED_CHARS]
    return body, _fingerprint("said-v1", body)


def embed_run_once(embed=None) -> bool:
    """One background batch of message embeddings (the recall-embeddings worker, after memory events). True when
    there was work."""
    from datetime import timedelta

    from sqlalchemy import and_, or_
    from sqlalchemy.dialects.postgresql import insert

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.services.recall_embeddings import _embed_batch

    settings = get_settings()
    embed = embed or _embed_batch
    now = datetime.now(timezone.utc)
    row = ChatMessageEmbedding
    with SessionLocal() as db:
        due = list(db.execute(
            select(ChatMessage, Conversation.user_id).join(Conversation, Conversation.id == ChatMessage.conversation_id)
            .outerjoin(row, row.message_id == ChatMessage.id)
            .where(ChatMessage.content != "",
                   or_(ChatMessage.role == "user", ChatMessage.status.in_(("completed", "partial")),
                       ChatMessage.heard_chars > 0),
                   or_(ChatMessage.heard_chars.is_(None), ChatMessage.heard_chars > 0),
                   or_(row.message_id.is_(None),
                       and_(row.status.in_(("pending", "failed")), row.next_attempt_at <= now,
                            row.attempts < MAX_EMBED_ATTEMPTS)))
            .order_by(ChatMessage.created_at.desc()).limit(settings.recall_embedding_batch)).all())
        due = [(m, u) for m, u in due if _delivered(m) and _embed_text(m)[0].strip()]
        if not due:
            return False
        texts = {m.id: _embed_text(m) for m, _ in due}
        db.execute(insert(ChatMessageEmbedding).values([
            {"message_id": m.id, "user_id": u, "model": settings.memory_embedding_model,
             "fingerprint": texts[m.id][1], "status": "pending"} for m, u in due]).on_conflict_do_nothing())
        db.commit()
    try:
        vectors = embed([texts[m.id][0] for m, _ in due])
        if len(vectors) != len(due):
            raise ValueError("embedding count doesn't match the batch")
    except Exception as exc:  # noqa: BLE001 - retried later; search works by words meanwhile
        log.warning("transcript embeddings: batch of %s failed: %s", len(due), exc)
        with SessionLocal() as db:
            for message, _ in due:
                stored = db.get(ChatMessageEmbedding, message.id)
                if stored is not None and stored.status != "done":
                    stored.status, stored.attempts = "failed", stored.attempts + 1
                    stored.next_attempt_at = now + timedelta(minutes=2 ** min(stored.attempts, 6))
            db.commit()
        return False
    with SessionLocal() as db:
        for (message, _), vector in zip(due, vectors, strict=True):
            current = db.get(ChatMessage, message.id)
            stored = db.get(ChatMessageEmbedding, message.id)
            if current is None or stored is None:
                continue  # deleted meanwhile
            text_, fingerprint = _embed_text(current)
            if fingerprint != texts[message.id][1]:
                stored.status, stored.fingerprint, stored.vector = "pending", fingerprint, None
                continue
            stored.status, stored.vector, stored.fingerprint = "done", vector, fingerprint
            stored.model = settings.memory_embedding_model
        db.commit()
    return True


def read(db: Session, user_id: uuid.UUID, message_id: uuid.UUID | str) -> MemoryRecord | None:
    """One passage by its reference, as far as it was heard; None when it's gone, not the user's, or never
    delivered (the same answer)."""
    try:
        message_id = message_id if isinstance(message_id, uuid.UUID) else uuid.UUID(str(message_id))
    except ValueError:
        return None
    row = _owned(db, user_id, [message_id]).get(message_id)
    if row is None:
        return None
    message, title = row
    if not message.content or (message.role == "assistant" and message.status not in ("completed", "partial")
                               and not (message.heard_chars or 0) > 0):
        return None
    if message.role == "assistant" and message.heard_chars == 0:
        return None
    body, cut_off = _heard_body(message)
    content = body if len(body) <= READ_CHARS else body[:READ_CHARS].rsplit(" ", 1)[0] + "…"
    return record(message, title, content=content, cut_off=cut_off,
                  forgotten=bool(forgotten_ids(db, user_id, [message.id])), matched=("direct_reference",))


def matching_ids(db: Session, user_id: uuid.UUID, words: list[str], *, before: datetime | None,
                 cap: int) -> list[uuid.UUID]:
    """Messages (either speaker, heard text) with any of ``words``, oldest first, up to ``cap``: for a forget's
    scan, which then reads each one."""
    words = terms(" ".join(words))
    if not words:
        return []
    tsq = db.scalar(text("SELECT CAST(to_tsquery('english', :q) AS text)"), {"q": " | ".join(words)})
    if not tsq:
        return []
    params: dict = {"user_id": user_id, "tsq": tsq, "cap": cap}
    bound = ""
    if before is not None:
        bound = "AND m.created_at <= :before"
        params["before"] = _naive_utc(before)
    rows = db.execute(text(f"""
        SELECT m.id FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id,
             (SELECT CAST(:tsq AS tsquery) AS q) q
        WHERE {_ELIGIBLE} AND m.search_tsv @@ q.q {bound}
        ORDER BY m.created_at, m.id LIMIT :cap
    """), params)
    return [row[0] for row in rows]


def neighbours(db: Session, user_id: uuid.UUID, message_ids: list[uuid.UUID], *, after: int,
               before: datetime | None) -> list[uuid.UUID]:
    """The ``after`` messages that follow each of these in its conversation (a reply saying "that injury"
    without its name), oldest first."""
    if not message_ids or after <= 0:
        return []
    params: dict = {"user_id": user_id, "ids": message_ids, "after": after}
    bound = ""
    if before is not None:
        bound = "AND n.created_at <= :before"
        params["before"] = _naive_utc(before)
    rows = db.execute(text(f"""
        SELECT DISTINCT n.id, n.created_at FROM chat_messages h
        JOIN conversations c ON c.id = h.conversation_id
        JOIN chat_messages n ON n.conversation_id = h.conversation_id
             AND n.conversation_sequence > h.conversation_sequence
             AND n.conversation_sequence <= h.conversation_sequence + :after
        WHERE c.user_id = :user_id AND h.id = ANY(:ids) AND n.content <> '' {bound}
        ORDER BY n.created_at, n.id
    """).bindparams(bindparam("ids", type_=ARRAY(UUID(as_uuid=True)))), params)
    return [row[0] for row in rows]


# --------------------------------------------------------------------------- #
# Pages: opaque, bound to this user and this search
# --------------------------------------------------------------------------- #


def _token(user_id: uuid.UUID, key: str, offset: int) -> str:
    raw = json.dumps({"u": str(user_id), "k": key, "o": offset, "t": "said"}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(token: str | None, user_id: uuid.UUID, key: str) -> int:
    if not token:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode()))
        offset = int(data["o"])
    except Exception as exc:  # noqa: BLE001
        raise RecallInvalid("that page token isn't valid") from exc
    if data.get("u") != str(user_id) or data.get("k") != key or data.get("t") != "said" or not 0 <= offset <= 10000:
        raise RecallInvalid("that page token belongs to a different search")
    return offset


def _log(entry: dict) -> None:
    from app.services import recall

    recall._log(entry)
