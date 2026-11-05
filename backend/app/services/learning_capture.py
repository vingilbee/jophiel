"""Capture and ownership (docs/learning-system/01-capture-and-ownership.md; implementation:
docs/learning-system/01-implementation.md).

Each learner records its own attempt: Student 1 its reading (``cognitive_live``), Student 2 its choice
(``student_two``), her hands their skill choice (``skill_driver``). Executors record what actually happened:
an effect's owner (``student_two.apply``), delivery (the chat worker, the socket fallback, WhatsApp), her hands
running a skill. This module owns persistence and linking only; it never interprets a record, and learning reads
the records without rewriting them.

- **Ids before the call.** ``attempt_id`` is assigned before a learner is invoked and travels downstream (Student 2
  names Student 1's attempt as upstream; a reply's execution names Student 2's attempt). A chat job's attempts are
  deterministic per job attempt (``uuid5(turn, "<learner>:<n>")``), so a reclaimed job's retry is a new attempt
  linked to the one before, and re-recording the same attempt inserts nothing twice.
- **Frozen at record time.** ``attempt`` / ``execution`` turn their arguments into JSON on the caller's thread,
  before anything later can change them, then queue the frozen record. Nothing blocks: the caller never waits for
  storage. Large unchanging text (system prompts, schemas) goes in ``blobs``: stored once per user by hash, and
  the input keeps ``{"$blob": sha}``.
- **Background persistence.** One writer thread per process saves records, each in its own transaction, with
  bounded retries and exponential backoff. A full queue, exhausted retries, an unserializable record, a source
  deleted before the save (a foreign key gone) or records still queued at shutdown become ``capture_gaps`` rows
  (or lines in the gap file when the database is the thing that's down). A sudden crash loses what was queued,
  without a gap: reconstruction then shows the missing link instead of a complete chain.

Her trajectory (docs/learning-system/POST-AUDIT-01-implementation.md) goes through the same writer: chunks of every
game tick (``trajectory_chunk``), their streams (``trajectory_stream``) and what the pack let go of before we took it
(``trajectory_dropped``). Chunks wait in their own bound (``LEARNING_CAPTURE_CHUNK_QUEUE_MAX``), so a backlog of
trajectory never pushes out a decision.

Retention (``prune``, on the learning worker) and source deletion (``drop_raw_for``, from forgetting) remove raw
copies; training examples (``services/training_examples.py``) are separate and stay.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    CAPTURE_EXECUTION_KINDS, CAPTURE_LEARNERS, CAPTURE_RESULTS, CaptureBlob, CaptureGap, ChatMessage,
    DecisionAttempt, DecisionAttemptSource, DecisionExecution, TrajectoryChunk, TrajectoryStream,
)

logger = logging.getLogger(__name__)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Never captured in step 1, and said so on every attempt (filled in only by the plans that create them).
NOT_CAPTURED = ("prediction", "alternatives", "selection_probabilities")
GAP_BACKLOG = 1000
BATCH = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"not JSON: {type(value).__name__}")


def freeze(value: Any) -> Any:
    """A deep, JSON-only copy: what the record holds can't change after this call."""
    return json.loads(json.dumps(value, default=_default, ensure_ascii=False))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


_code_version: str | None = None


def code_version() -> str:
    """The code a learner ran under: the repository's commit (``+dirty`` with uncommitted changes), read once per
    process; ``unknown`` when git can't say."""
    global _code_version
    if _code_version is None:
        import subprocess

        try:
            head = subprocess.run(["git", "-C", BACKEND, "rev-parse", "--short=12", "HEAD"], capture_output=True,
                                  text=True, timeout=2, check=True).stdout.strip()
            dirty = subprocess.run(["git", "-C", BACKEND, "status", "--porcelain", "--untracked-files=no"],
                                   capture_output=True, text=True, timeout=2).stdout.strip()
            _code_version = (head + ("+dirty" if dirty else "")) or "unknown"
        except (OSError, subprocess.SubprocessError):
            _code_version = "unknown"
    return _code_version


def attempt_id_for(turn_id: uuid.UUID | None, learner: str, try_number: int | None) -> uuid.UUID:
    """The attempt's id: deterministic for a chat job's attempt (the same job attempt is the same record),
    random otherwise."""
    if turn_id is not None and try_number is not None:
        return uuid.uuid5(turn_id, f"{learner}:{try_number}")
    return uuid.uuid4()


def prior_for(turn_id: uuid.UUID | None, learner: str, try_number: int | None) -> uuid.UUID | None:
    """The attempt this one retries: the same learner on the job's previous attempt."""
    if turn_id is not None and try_number is not None and try_number > 1:
        return uuid.uuid5(turn_id, f"{learner}:{try_number - 1}")
    return None


def execution_id_for(attempt_id: uuid.UUID | str | None, kind: str, key: str | uuid.UUID | None = None) -> uuid.UUID:
    base = uuid.UUID(str(attempt_id)) if attempt_id else uuid.UUID(int=0)
    return uuid.uuid5(base, f"{kind}:{key}" if key is not None else kind)


@dataclass(frozen=True)
class Ctx:
    """Who and where an attempt belongs to, and its links: decided by the caller before the learner runs."""

    user_id: uuid.UUID
    attempt_id: uuid.UUID
    trigger: str
    conversation_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    source_message_id: uuid.UUID | None = None
    game_session_id: uuid.UUID | None = None
    prior_attempt_id: uuid.UUID | None = None
    upstream: dict[str, str] = field(default_factory=dict)  # role -> attempt id
    input_refs: tuple[dict[str, Any], ...] = ()  # memory / lesson revisions the input was built from
    sources: tuple[str, ...] = ()  # "message:<id>", "fact:<id>", ...


@dataclass
class _Record:
    kind: str  # attempt / execution / chunk / stream / gap
    id: uuid.UUID
    user_id: uuid.UUID
    label: str
    values: dict[str, Any]
    blobs: dict[str, str] = field(default_factory=dict)  # sha -> content
    sources: tuple[str, ...] = ()
    settle: bool = False  # an execution that may replace an unknown result
    turn_id: uuid.UUID | None = None
    tries: int = 0
    next_at: float = 0.0
    last_error: str | None = None


class _Permanent(Exception):
    """A save that can never succeed (its source is gone): no more tries."""


class CaptureService:
    """The background writer. ``submit`` never blocks and never raises."""

    def __init__(self, *, session_factory=None, queue_max: int | None = None, max_tries: int | None = None,
                 base_delay: float | None = None, max_delay: float | None = None, gap_path: str | None = None,
                 chunk_queue_max: int | None = None, clock=time.monotonic):
        s = get_settings()
        self._session_factory = session_factory
        self.queue_max = queue_max or s.learning_capture_queue_max
        self.chunk_queue_max = chunk_queue_max or s.learning_capture_chunk_queue_max
        self.max_tries = max_tries or s.learning_capture_max_tries
        self.base_delay = base_delay if base_delay is not None else s.learning_capture_retry_base_seconds
        self.max_delay = max_delay if max_delay is not None else s.learning_capture_retry_max_seconds
        path = gap_path or s.learning_capture_gap_log_path
        self.gap_path = path if os.path.isabs(path) else os.path.join(BACKEND, path)
        self.clock = clock
        self._queue: deque[_Record] = deque()
        self._gaps: deque[_Record] = deque()
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._busy = 0
        self._chunks_waiting = 0  # trajectory chunks in the queue (their own bound)
        self.counts = {"queued": 0, "written": 0, "retried": 0, "gaps": 0, "gaps_to_file": 0, "chunks_written": 0,
                       "chunk_bytes_written": 0}
        self.last_error: str | None = None

    # -- the caller's side ------------------------------------------------------------------------------

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory
        from app.db.session import SessionLocal
        return SessionLocal

    def submit(self, record: _Record) -> bool:
        with self._cond:
            if self._closed:
                self._gap_locked(record, "shutdown", "submitted after shutdown")
                return False
            if record.kind == "chunk" and self._chunks_waiting >= self.chunk_queue_max:
                self._gap_locked(record, "queue_full", f"{self._chunks_waiting} trajectory chunks waiting")
                return False
            if record.kind not in ("gap", "chunk") and len(self._queue) - self._chunks_waiting >= self.queue_max:
                self._gap_locked(record, "queue_full", f"{len(self._queue) - self._chunks_waiting} records waiting")
                return False
            self._chunks_waiting += record.kind == "chunk"
            (self._gaps if record.kind == "gap" else self._queue).append(record)
            self.counts["queued"] += record.kind != "gap"
            self._start_locked()
            self._cond.notify()
        return True

    def room_for_chunks(self, n: int = 1) -> bool:
        """Whether ``n`` more trajectory chunks fit in their bound now (the collector pulls only then)."""
        with self._cond:
            return not self._closed and self._chunks_waiting + n <= self.chunk_queue_max

    def gap(self, *, user_id: uuid.UUID, record_kind: str, record_id: uuid.UUID, label: str, reason: str,
            detail: str | None = None, tries: int = 0, turn_id: uuid.UUID | None = None) -> None:
        with self._cond:
            self._gap_locked(_Record(record_kind, record_id, user_id, label, {}, turn_id=turn_id, tries=tries),
                             reason, detail)
            self._start_locked()
            self._cond.notify()

    def _gap_locked(self, record: _Record, reason: str, detail: str | None) -> None:
        self.counts["gaps"] += 1
        gap = _Record("gap", record.id, record.user_id, record.label, {
            "user_id": record.user_id, "record_kind": record.kind if record.kind != "gap" else "attempt",
            "record_id": record.id, "label": record.label, "reason": reason, "tries": record.tries,
            "detail": (detail or "")[:500] or None, "turn_id": record.turn_id})
        if len(self._gaps) >= GAP_BACKLOG or self._closed:
            self._to_file(gap)
        else:
            self._gaps.append(gap)

    def _to_file(self, gap: _Record) -> None:
        self.counts["gaps_to_file"] += 1
        try:
            os.makedirs(os.path.dirname(self.gap_path), exist_ok=True)
            with open(self.gap_path, "a") as f:
                f.write(json.dumps({"at": _now().isoformat(), **gap.values}, default=str) + "\n")
        except OSError:
            logger.exception("learning_capture: the gap file can't be written either")

    def _start_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="learning-capture", daemon=True)
            self._thread.start()

    # -- the writer ---------------------------------------------------------------------------------------

    def _take(self) -> list[_Record] | None:
        """Records due now (gaps first), or None after close with nothing left."""
        with self._cond:
            while True:
                now = self.clock()
                due = [r for r in self._gaps if r.next_at <= now][:BATCH]
                due += [r for r in self._queue if r.next_at <= now][:BATCH - len(due)]
                if due:
                    for r in due:
                        (self._gaps if r.kind == "gap" else self._queue).remove(r)
                        self._chunks_waiting -= r.kind == "chunk"
                    self._busy += len(due)
                    return due
                if self._closed and not self._queue and not self._gaps:
                    return None
                waiting = [r.next_at for r in (*self._gaps, *self._queue)]
                self._cond.wait(timeout=max(0.01, min(waiting) - now) if waiting else 1.0)

    def _run(self) -> None:
        while True:
            batch = self._take()
            if batch is None:
                return
            for record in batch:
                self._save(record)
            with self._cond:
                self._busy -= len(batch)
                self._cond.notify_all()

    def _save(self, record: _Record) -> None:
        try:
            with self._sessions()() as db:
                _WRITERS[record.kind](db, record)
                db.commit()
            with self._cond:
                self.counts["written"] += record.kind != "gap"
                if record.kind == "chunk":
                    self.counts["chunks_written"] += 1
                    self.counts["chunk_bytes_written"] += record.values.get("size_bytes", 0)
            return
        except _Permanent as exc:
            outcome, why = "source_gone", str(exc)
        except (SQLAlchemyError, OSError) as exc:
            outcome, why = "retry", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        except Exception as exc:  # noqa: BLE001 - a bad record never stops the writer
            outcome, why = "unserializable", f"{type(exc).__name__}: {exc}"[:200]
        with self._cond:
            self.last_error = why
            record.tries += 1
            record.last_error = why
            if record.kind == "gap":
                if outcome == "retry" and record.tries < self.max_tries:
                    record.next_at = self.clock() + self._delay(record.tries)
                    self._gaps.append(record)
                else:
                    self._to_file(record)
            elif outcome == "retry" and record.tries < self.max_tries:
                self.counts["retried"] += 1
                record.next_at = self.clock() + self._delay(record.tries)
                self._queue.append(record)
                self._chunks_waiting += record.kind == "chunk"
            else:
                self._gap_locked(record, "retries_exhausted" if outcome == "retry" else outcome, why)
            self._cond.notify_all()

    def _delay(self, tries: int) -> float:
        return min(self.max_delay, self.base_delay * 2 ** (tries - 1))

    # -- draining -----------------------------------------------------------------------------------------

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until nothing is queued or being written (including records waiting to retry). True: drained."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._queue or self._gaps or self._busy:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(timeout=min(left, 0.05))
        return True

    def close(self, timeout: float | None = None) -> None:
        """At exit: try to save what's queued for ``timeout``; whatever's still left is a shutdown gap."""
        timeout = get_settings().learning_capture_drain_seconds if timeout is None else timeout
        with self._cond:
            for r in list(self._queue):
                r.next_at = 0.0  # no more waiting for backoff
        self.flush(timeout)
        with self._cond:
            self._closed = True
            left, self._queue = list(self._queue), deque()
            self._chunks_waiting = 0
            gaps, self._gaps = list(self._gaps), deque()
            for r in left:
                self._gap_locked(r, "shutdown", "still queued at exit")
            for g in gaps:
                self._to_file(g)
            self._cond.notify_all()

    def stats(self) -> dict[str, Any]:
        with self._cond:
            now = self.clock()
            waiting = [r for r in self._queue]
            return {**self.counts, "waiting": len(waiting), "retrying": sum(1 for r in waiting if r.tries),
                    "chunks_waiting": self._chunks_waiting, "chunk_queue_max": self.chunk_queue_max,
                    "gaps_waiting": len(self._gaps), "queue_max": self.queue_max, "max_tries": self.max_tries,
                    "oldest_retry_seconds": round(max((now - r.next_at for r in waiting if r.tries), default=0), 1),
                    "last_error": self.last_error}


# --------------------------------------------------------------------------- #
# Writing one record
# --------------------------------------------------------------------------- #


def _foreign_key_gone(exc: IntegrityError) -> bool:
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == "23503"


def _write_attempt(db: Session, record: _Record) -> None:
    try:
        for digest, content in record.blobs.items():
            statement = insert(CaptureBlob).values(user_id=record.user_id, sha256=digest, content=content)
            db.execute(statement.on_conflict_do_update(index_elements=["user_id", "sha256"],
                                                       set_={"last_used_at": func.now()}))
        inserted = db.execute(insert(DecisionAttempt).values(**record.values)
                              .on_conflict_do_nothing(index_elements=["id"]).returning(DecisionAttempt.id)).scalar()
        if inserted is not None and record.sources:
            db.execute(insert(DecisionAttemptSource).values(
                [{"attempt_id": record.id, "ref": ref[:80], "user_id": record.user_id} for ref in record.sources])
                .on_conflict_do_nothing())
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _foreign_key_gone(exc):
            raise _Permanent("its conversation, message, session or user is gone") from exc
        raise


def _write_execution(db: Session, record: _Record) -> None:
    statement = insert(DecisionExecution).values(**record.values)
    if record.settle:
        update = {k: statement.excluded[k] for k in ("executed", "result", "deviation", "ended_at")}
        statement = statement.on_conflict_do_update(index_elements=["id"], set_=update,
                                                    where=DecisionExecution.result == "unknown")
    else:
        statement = statement.on_conflict_do_nothing(index_elements=["id"])
    try:
        db.execute(statement)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _foreign_key_gone(exc):
            raise _Permanent("its conversation, message, session or user is gone") from exc
        raise


def _write_gap(db: Session, record: _Record) -> None:
    try:
        db.execute(insert(CaptureGap).values(**record.values).on_conflict_do_nothing(constraint="uq_capture_gaps_record"))
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _foreign_key_gone(exc):  # the user is gone: nothing left to say it about
            return
        raise


def _write_chunk(db: Session, record: _Record) -> None:
    try:
        db.execute(insert(TrajectoryChunk).values(**record.values).on_conflict_do_nothing(index_elements=["id"]))
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _foreign_key_gone(exc):
            raise _Permanent("its user or game session is gone") from exc
        raise


def _write_stream(db: Session, record: _Record) -> None:
    """A stream's header, seen again on every pull: what is known only grows (an end once seen stays, the highest
    seq the pack named, the newest expiry)."""
    statement = insert(TrajectoryStream).values(**record.values)
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(index_elements=["id"], set_={
        "ended_at": func.coalesce(TrajectoryStream.ended_at, excluded.ended_at),
        "end_reason": func.coalesce(TrajectoryStream.end_reason, excluded.end_reason),
        "last_seq": func.greatest(TrajectoryStream.last_seq, excluded.last_seq),
        "username": func.coalesce(excluded.username, TrajectoryStream.username),
        "version": func.coalesce(excluded.version, TrajectoryStream.version),
        "expires_at": func.greatest(TrajectoryStream.expires_at, excluded.expires_at),
        "updated_at": func.now()},
        where=TrajectoryStream.user_id == excluded.user_id)
    try:
        db.execute(statement)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _foreign_key_gone(exc):
            raise _Permanent("its user is gone") from exc
        raise


_WRITERS = {"attempt": _write_attempt, "execution": _write_execution, "chunk": _write_chunk, "stream": _write_stream,
            "gap": _write_gap}


# --------------------------------------------------------------------------- #
# The process's service
# --------------------------------------------------------------------------- #

_service: CaptureService | None = None
_service_lock = threading.Lock()


def service() -> CaptureService:
    global _service
    with _service_lock:
        if _service is None:
            _service = CaptureService()
            atexit.register(_service.close)
        return _service


def install(svc: CaptureService | None) -> CaptureService | None:
    """Replace the process's service (tests). None: captures go nowhere."""
    global _service
    with _service_lock:
        before, _service = _service, svc
    return before


class _Nowhere(CaptureService):
    def submit(self, record: _Record) -> bool:
        return False

    def room_for_chunks(self, n: int = 1) -> bool:
        return False  # captures go nowhere: nothing is pulled from the pack either

    def gap(self, **_: Any) -> None:
        return None


def disabled() -> CaptureService:
    return _Nowhere()


def _expires() -> datetime:
    return _now() + timedelta(days=get_settings().learning_capture_retention_days)


def attempt(ctx: Ctx, *, learner: str, provider: str | None, model: str | None, input: dict[str, Any],
            output: dict[str, Any] | None, status: str, started_at: datetime, produced_at: datetime | None,
            error: str | None = None, versions: dict[str, Any] | None = None,
            settings: dict[str, Any] | None = None, blobs: dict[str, str] | None = None,
            absent: Iterable[str] = NOT_CAPTURED) -> None:
    """Record one learner attempt. ``blobs``: input fields holding large unchanging text, stored by hash
    (``input[name]`` becomes ``{"$blob": sha}``). Never raises, never waits for storage."""
    assert learner in CAPTURE_LEARNERS, learner
    svc = service()
    try:
        frozen_input = freeze(input)
        stored: dict[str, str] = {}
        for name, content in (blobs or {}).items():
            digest = sha(content)
            stored[digest] = content
            frozen_input[name] = {"$blob": digest}
        values = {
            "id": ctx.attempt_id, "user_id": ctx.user_id, "learner": learner, "trigger": ctx.trigger[:32],
            "provider": provider, "model": (model or None) and model[:160], "turn_id": ctx.turn_id,
            "conversation_id": ctx.conversation_id, "source_message_id": ctx.source_message_id,
            "game_session_id": ctx.game_session_id, "prior_attempt_id": ctx.prior_attempt_id,
            "upstream": freeze(ctx.upstream), "versions": freeze(versions or {}), "settings": freeze(settings or {}),
            "input": frozen_input, "input_refs": freeze(list(ctx.input_refs)),
            "output": freeze(output) if output is not None else None, "absent": sorted(set(absent)),
            "status": status if status in ("ok", "invalid", "unavailable") else "error",
            "error": (error or None) and error[:1000], "blob_hashes": sorted(stored), "started_at": started_at,
            "produced_at": produced_at, "expires_at": _expires()}
    except Exception as exc:  # noqa: BLE001 - what can't be frozen is a gap, never a failed turn
        logger.exception("learning_capture: attempt %s can't be frozen", ctx.attempt_id)
        svc.gap(user_id=ctx.user_id, record_kind="attempt", record_id=ctx.attempt_id, label=learner,
                reason="unserializable", detail=f"{type(exc).__name__}: {exc}", turn_id=ctx.turn_id)
        return
    svc.submit(_Record("attempt", ctx.attempt_id, ctx.user_id, learner, values, stored,
                       tuple(dict.fromkeys(ctx.sources)), turn_id=ctx.turn_id))


def execution(*, user_id: uuid.UUID, kind: str, executor: str, result: str, attempt_id: uuid.UUID | str | None,
              execution_id: uuid.UUID | None = None, turn_id: uuid.UUID | None = None,
              conversation_id: uuid.UUID | None = None, assistant_message_id: uuid.UUID | None = None,
              game_session_id: uuid.UUID | None = None, intended: dict[str, Any] | None = None,
              executed: dict[str, Any] | None = None, deviation: str | None = None,
              started_at: datetime | None = None, ended_at: datetime | None = None, settle: bool = False) -> uuid.UUID:
    """Record what actually happened. ``settle=True``: this may replace an earlier ``unknown`` result for the same
    id (a skill that was running), never a known one. Returns the execution id. Never raises."""
    assert kind in CAPTURE_EXECUTION_KINDS and result in CAPTURE_RESULTS, (kind, result)
    eid = execution_id or execution_id_for(attempt_id, kind)
    svc = service()
    try:
        values = {
            "id": eid, "user_id": user_id, "attempt_id": uuid.UUID(str(attempt_id)) if attempt_id else None,
            "turn_id": turn_id, "kind": kind, "executor": executor[:40], "conversation_id": conversation_id,
            "assistant_message_id": assistant_message_id, "game_session_id": game_session_id,
            "intended": freeze(intended or {}), "executed": freeze(executed or {}), "result": result,
            "deviation": (deviation or None) and deviation[:1000], "started_at": started_at,
            "ended_at": ended_at, "expires_at": _expires()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("learning_capture: execution %s can't be frozen", eid)
        svc.gap(user_id=user_id, record_kind="execution", record_id=eid, label=kind, reason="unserializable",
                detail=f"{type(exc).__name__}: {exc}", turn_id=turn_id)
        return eid
    svc.submit(_Record("execution", eid, user_id, kind, values, settle=settle, turn_id=turn_id))
    return eid


# --------------------------------------------------------------------------- #
# Reply delivery: one place for both reply paths and follow-ups
# --------------------------------------------------------------------------- #

_REPLY_RESULT = {"completed": "succeeded", "partial": "deviated", "cancelled": "cancelled", "failed": "failed",
                 "silent": "not_sent"}


def reply(*, user_id: uuid.UUID, attempt_id: str | uuid.UUID | None, turn_id: uuid.UUID,
          conversation_id: uuid.UUID | None, outcome: str, assistant_message_id: uuid.UUID | None,
          reply_chars: int, intended: dict[str, Any] | None, channel: str, voice: bool,
          started_at: datetime | None = None) -> None:
    """The mouth's delivery of one turn: what Student 2 intended next to what was stored and sent. One record per
    turn (a turn has one assistant row; the first record wins). A reply to an outside channel (WhatsApp) is
    ``unknown`` here; its settlement is recorded by the channel."""
    result = _REPLY_RESULT.get(outcome, "unknown")
    if channel != "web" and result == "succeeded":
        result = "unknown"  # handed to the channel's outbox; settlement comes later (channel_delivery)
    deviation = {"partial": "stopped part way: only the start of the reply was kept",
                 "cancelled": "stopped before any reply was kept", "failed": "the reply failed"}.get(outcome)
    if intended and intended.get("communication") in ("silent", "defer") and outcome != "silent":
        deviation = (deviation + "; " if deviation else "") + "spoke although the decision was silence"
    execution(user_id=user_id, kind="reply", executor=f"mouth:{channel}", result=result, attempt_id=attempt_id,
              execution_id=execution_id_for(turn_id, "reply"), turn_id=turn_id,
              conversation_id=conversation_id,
              assistant_message_id=assistant_message_id if outcome != "silent" else None,
              intended=intended or {"communication": "unknown: no decision"},
              executed={"outcome": outcome, "reply_chars": reply_chars, "voice": voice, "channel": channel,
                        "stored": assistant_message_id is not None and outcome != "silent"},
              deviation=deviation, started_at=started_at, ended_at=_now())


def heard(*, user_id: uuid.UUID, message: ChatMessage, conversation_id: uuid.UUID) -> None:
    """A spoken reply interrupted: how much of it was actually heard (a deviation of the delivered reply)."""
    execution(user_id=user_id, kind="heard", executor="voice", result="deviated", attempt_id=None,
              execution_id=execution_id_for(message.id, "heard", message.heard_chars), turn_id=message.turn_id,
              conversation_id=conversation_id, assistant_message_id=message.id,
              executed={"heard_chars": message.heard_chars, "reply_chars": len(message.content or "")},
              deviation=f"interrupted: {message.heard_chars} of {len(message.content or '')} characters heard",
              ended_at=_now())


def channel_delivery(*, user_id: uuid.UUID, delivery_id: uuid.UUID, assistant_message_id: uuid.UUID,
                     channel: str, part_index: int, status: str, error: str | None = None) -> None:
    """An outside channel settling one part of a reply (provider statuses as they arrive, each its own record,
    timed when it was settled)."""
    result = {"accepted": "unknown", "sent": "unknown", "delivered": "succeeded", "read": "succeeded",
              "failed": "failed"}.get(status, "unknown")
    execution(user_id=user_id, kind="channel_delivery", executor=channel, result=result, attempt_id=None,
              execution_id=execution_id_for(delivery_id, "channel_delivery", status),
              assistant_message_id=assistant_message_id,
              executed={"status": status, "part_index": part_index, "delivery_id": str(delivery_id)},
              deviation=(error or None) and f"failed: {error}"[:500], ended_at=_now())


# --------------------------------------------------------------------------- #
# Her trajectory (docs/learning-system/POST-AUDIT-01-implementation.md)
# --------------------------------------------------------------------------- #


def chunk_id_for(stream_id: uuid.UUID | str, seq: int) -> uuid.UUID:
    """A chunk's id: the same chunk pulled twice is one row."""
    return uuid.uuid5(uuid.UUID(str(stream_id)), f"seq:{int(seq)}")


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def trajectory_chunk(*, user_id: uuid.UUID, game_session_id: uuid.UUID | None, meta: dict[str, Any],
                     data: bytes) -> uuid.UUID | None:
    """Queue one chunk as the pack encoded it (``data`` is stored as given, never decoded here). Returns its id,
    or None when ``meta`` doesn't describe a chunk (nothing queued, a gap noted). Never raises, never waits."""
    svc = service()
    try:
        cid = chunk_id_for(meta["stream"], meta["seq"])
        values = {
            "id": cid, "user_id": user_id, "stream_id": uuid.UUID(str(meta["stream"])),
            "run_id": uuid.UUID(str(meta["run"])), "game_session_id": game_session_id, "seq": int(meta["seq"]),
            "first_tick": int(meta["first_tick"]), "ticks": int(meta["ticks"]), "events": int(meta.get("events") or 0),
            "started_at": _when(meta["started_at"]), "ended_at": _when(meta["ended_at"]), "final": bool(meta.get("final")),
            "format": int(meta.get("format") or 1), "encoding": str(meta.get("encoding") or "json+gzip")[:16],
            "data": data, "size_bytes": len(data), "raw_bytes": int(meta.get("raw_bytes") or 0),
            "sha256": str(meta.get("sha256") or hashlib.sha256(data).hexdigest())[:64],
            "kinds": freeze(meta.get("kinds") or {}), "overhead": freeze(meta.get("overhead") or {}),
            "expires_at": _expires()}
    except Exception as exc:  # noqa: BLE001 - a chunk that can't be described is a gap, never a stopped pull
        logger.exception("learning_capture: a trajectory chunk can't be recorded")
        key = f"{meta.get('stream')}:{meta.get('seq')}"
        svc.gap(user_id=user_id, record_kind="chunk", record_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                label="trajectory", reason="unserializable", detail=f"{type(exc).__name__}: {exc}; chunk {key}")
        return None
    svc.submit(_Record("chunk", cid, user_id, "trajectory", values))
    return cid


def trajectory_stream(*, user_id: uuid.UUID, stream: dict[str, Any], run_id: str, game: str = "minecraft",
                      coverage: dict[str, Any] | None = None) -> None:
    """Queue a stream's header as the pack described it (seen again on every pull; what is known only grows)."""
    svc = service()
    try:
        sid = uuid.UUID(str(stream["id"]))
        values = {
            "id": sid, "user_id": user_id, "run_id": uuid.UUID(str(run_id)), "stream_index": int(stream.get("index") or 0),
            "game": game[:32], "username": (stream.get("username") or None) and str(stream["username"])[:64],
            "version": (stream.get("version") or None) and str(stream["version"])[:32],
            "started_at": _when(stream["started_at"]), "ended_at": _when(stream.get("ended_at")),
            "end_reason": (stream.get("end_reason") or None) and str(stream["end_reason"])[:500],
            "last_seq": int(stream.get("last_seq") if stream.get("last_seq") is not None else -1),
            "coverage": freeze(coverage or {}), "expires_at": _expires()}
    except Exception:  # noqa: BLE001
        logger.exception("learning_capture: a trajectory stream can't be recorded")
        return
    svc.submit(_Record("stream", sid, user_id, "trajectory_stream", values))


def trajectory_dropped(*, user_id: uuid.UUID, drop: dict[str, Any]) -> None:
    """Chunks the pack let go of before we took them (its buffer was full): a content-free gap, once per range."""
    try:
        record_id = uuid.uuid5(uuid.UUID(str(drop["stream"])), f"dropped:{int(drop['from_seq'])}")
    except Exception:  # noqa: BLE001
        record_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(drop, sort_keys=True, default=str))
    detail = (f"stream {drop.get('stream')} seq {drop.get('from_seq')}-{drop.get('to_seq')} "
              f"({drop.get('chunks')} chunks, ticks {drop.get('first_tick')}-{drop.get('last_tick')}, "
              f"{drop.get('started_at')} to {drop.get('ended_at')}): {drop.get('reason')}")
    service().gap(user_id=user_id, record_kind="chunk", record_id=record_id, label="trajectory",
                  reason="pack_dropped", detail=detail)


# --------------------------------------------------------------------------- #
# Retention and source deletion
# --------------------------------------------------------------------------- #


def drop_raw_for(db: Session, user_id: uuid.UUID, refs: Iterable[str]) -> int:
    """Raw captures made from forgotten sources go now, in the caller's transaction (training examples stay).
    A message reference also removes every attempt in its conversation since that message was written: a
    learner's window may have shown it without naming it."""
    refs = sorted(set(refs))
    if not refs:
        return 0
    ids = set(db.scalars(select(DecisionAttemptSource.attempt_id).where(
        DecisionAttemptSource.user_id == user_id, DecisionAttemptSource.ref.in_(refs))))
    messages = [uuid.UUID(r.split(":", 1)[1]) for r in refs if r.startswith("message:")]
    if messages:
        for conversation_id, at in db.execute(select(ChatMessage.conversation_id, ChatMessage.created_at)
                                              .where(ChatMessage.id.in_(messages))):
            ids |= set(db.scalars(select(DecisionAttempt.id).where(
                DecisionAttempt.user_id == user_id, DecisionAttempt.conversation_id == conversation_id,
                DecisionAttempt.started_at >= (at if at.tzinfo else at.replace(tzinfo=timezone.utc)))))
        ids |= set(db.scalars(select(DecisionAttempt.id).where(DecisionAttempt.user_id == user_id,
                                                               DecisionAttempt.source_message_id.in_(messages))))
    if not ids:
        return 0
    db.execute(delete(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                               DecisionExecution.attempt_id.in_(ids)))
    return db.execute(delete(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                    DecisionAttempt.id.in_(ids))).rowcount or 0


def prune(db: Session, now: datetime | None = None, *, batch: int = 500) -> dict[str, int]:
    """Raw captures past their retention, old gaps, and blobs nothing refers to any more."""
    now = now or _now()
    out = {}
    expired = select(DecisionAttempt.id).where(DecisionAttempt.expires_at < now).limit(batch)
    out["attempts"] = db.execute(delete(DecisionAttempt).where(DecisionAttempt.id.in_(expired))).rowcount or 0
    expired = select(DecisionExecution.id).where(DecisionExecution.expires_at < now).limit(batch)
    out["executions"] = db.execute(delete(DecisionExecution).where(DecisionExecution.id.in_(expired))).rowcount or 0
    old = now - timedelta(days=get_settings().learning_capture_retention_days)
    out["gaps"] = db.execute(delete(CaptureGap).where(CaptureGap.detected_at < old)).rowcount or 0
    referenced = select(DecisionAttempt.id).where(
        DecisionAttempt.user_id == CaptureBlob.user_id,
        DecisionAttempt.blob_hashes.op("?")(CaptureBlob.sha256)).exists()
    out["blobs"] = db.execute(delete(CaptureBlob).where(
        CaptureBlob.last_used_at < now - timedelta(hours=1), ~referenced)).rowcount or 0
    expired = select(TrajectoryChunk.id).where(TrajectoryChunk.expires_at < now).limit(batch)
    out["trajectory_chunks"] = db.execute(delete(TrajectoryChunk).where(TrajectoryChunk.id.in_(expired))).rowcount or 0
    # A stream goes once it has expired and holds no chunk (its header is what says a chunk is missing or expired).
    holds = select(TrajectoryChunk.id).where(TrajectoryChunk.stream_id == TrajectoryStream.id).exists()
    out["trajectory_streams"] = db.execute(delete(TrajectoryStream).where(
        TrajectoryStream.expires_at < now, ~holds)).rowcount or 0
    db.commit()
    return out


def forget_user_raw(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    """Every raw capture of one user (the memory reset's ``capture`` scope). Training examples stay."""
    out = {"executions": db.execute(delete(DecisionExecution).where(DecisionExecution.user_id == user_id)).rowcount,
           "attempts": db.execute(delete(DecisionAttempt).where(DecisionAttempt.user_id == user_id)).rowcount,
           "gaps": db.execute(delete(CaptureGap).where(CaptureGap.user_id == user_id)).rowcount,
           "blobs": db.execute(delete(CaptureBlob).where(CaptureBlob.user_id == user_id)).rowcount,
           "trajectory_chunks": db.execute(delete(TrajectoryChunk).where(TrajectoryChunk.user_id == user_id)).rowcount,
           "trajectory_streams": db.execute(delete(TrajectoryStream).where(TrajectoryStream.user_id == user_id)).rowcount}
    return out


def message_refs(ids: Iterable[uuid.UUID | str | None]) -> list[str]:
    return [f"message:{i}" for i in ids if i]


def any_ref(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(r for g in groups for r in g if r))


__all__ = ["Ctx", "CaptureService", "attempt", "execution", "reply", "heard", "channel_delivery", "drop_raw_for",
           "prune", "service", "install", "disabled", "attempt_id_for", "prior_for", "execution_id_for", "freeze",
           "trajectory_chunk", "trajectory_stream", "trajectory_dropped", "chunk_id_for"]
