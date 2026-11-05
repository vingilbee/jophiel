"""Reading her trajectory back (docs/learning-system/POST-AUDIT-01-implementation.md, "Reconstruction").

The pack recorded every client tick she was in the world (``game_packs/minecraft/src/trajectory.js``); the backend
saved its chunks as encoded (``learning_capture.trajectory_chunk``). Here they are read back, never rewritten:

- ``decode`` checks a chunk's bytes against the hash the pack gave and opens it;
- ``expand`` rebuilds each tick's full state from the chunk's checkpoint and frames (unchanged ticks included);
- ``read`` gives a stretch of one stream: its ticks, its events in order, what's missing (seq holes, gaps the pack
  or the writer reported, a stream whose end was never seen) and the coverage the pack declared;
- ``resolve`` says what a time in a session points at: a chunk that holds it, one that expired, or nothing;
- ``measure`` reports what it costs (bytes per minute and hour, a 30-day projection, the pack's tick overhead).

What isn't in a chunk is not invented: a stretch without frames is a stretch the client exposed no ticks for (she
was dead, or the process lagged), and an event's order is the pack's own; the backend's clock is never merged in.
Read only, and only one user's records.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import CaptureGap, TrajectoryChunk, TrajectoryStream

MAPS = ("inv", "ent")
MAX_TICKS = 6000  # one read: five minutes of ticks
MAX_CHUNKS = 200  # one read opens at most this many chunks (about 16 minutes)
CONTROLS = ("forward", "back", "left", "right", "jump", "sprint", "sneak")


class ChunkCorrupt(ValueError):
    """A chunk whose bytes don't match the hash it was saved with, or that can't be opened."""


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _iso(at: datetime | None) -> str | None:
    return _aware(at).isoformat() if at is not None else None


def decode(row: TrajectoryChunk) -> dict[str, Any]:
    data = bytes(row.data)
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise ChunkCorrupt(f"chunk {row.stream_id}:{row.seq}: bytes don't match their hash")
    if row.encoding != "json+gzip":
        raise ChunkCorrupt(f"chunk {row.stream_id}:{row.seq}: unknown encoding {row.encoding!r}")
    try:
        return json.loads(gzip.decompress(data))
    except (OSError, ValueError) as exc:
        raise ChunkCorrupt(f"chunk {row.stream_id}:{row.seq}: {exc}") from exc


def apply_delta(state: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any]:
    """The pack's ``applyDelta``: scalars and arrays replaced, the maps (inventory slots, entities) by key, a key
    set to null removed."""
    if not delta:
        return state
    out = dict(state)
    for key, value in delta.items():
        if key in MAPS:
            merged = dict(state.get(key) or {})
            for k, v in (value or {}).items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            out[key] = merged
        else:
            out[key] = value
    return out


def expand(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tick of a decoded chunk: ``{tick, at_ms, state}``, unchanged ticks included."""
    state = chunk.get("checkpoint") or {}
    out = []
    for i, frame in enumerate(chunk.get("frames") or []):
        state = apply_delta(state, frame[1] if len(frame) > 1 else None)
        out.append({"tick": chunk["first_tick"] + i, "at_ms": chunk["started_ms"] + frame[0], "state": state})
    return out


def readable(state: dict[str, Any]) -> dict[str, Any]:
    """A tick's compact state with its fields named (for people reading it; ``state`` keeps the stored form)."""
    controls = state.get("c")
    medium = state.get("m")
    return {
        "position": state.get("p"), "velocity": state.get("v"),
        "yaw_pitch": state.get("r"), "on_ground": state.get("g"),
        "controls": None if controls is None else [n for i, n in enumerate(CONTROLS) if controls & (1 << i)],
        "in_water": None if medium is None else bool(medium & 1), "in_lava": None if medium is None else bool(medium & 2),
        "health": state.get("h"), "food": state.get("f"), "saturation": state.get("s"), "air": state.get("o"),
        "hotbar_slot": state.get("q"), "digging": state.get("d"), "using_item": bool(state.get("u")),
        "dimension": state.get("dim"), "game_mode": state.get("gm"),
        "inventory": state.get("inv") or {}, "entities": state.get("ent") or {},
    }


def holes(seqs: list[int], last_seq: int | None = None) -> list[dict[str, int]]:
    """Missing seq ranges: between the chunks held, and after the last one held up to what the pack said it made."""
    out = []
    held = sorted(set(seqs))
    expect = 0
    for seq in held:
        if seq > expect:
            out.append({"from_seq": expect, "to_seq": seq - 1})
        expect = seq + 1
    if last_seq is not None and last_seq >= expect:
        out.append({"from_seq": expect, "to_seq": last_seq})
    return out


def _stream_out(db: Session, stream: TrajectoryStream) -> dict[str, Any]:
    rows = db.execute(select(TrajectoryChunk.seq, TrajectoryChunk.ticks, TrajectoryChunk.size_bytes,
                             TrajectoryChunk.started_at, TrajectoryChunk.ended_at, TrajectoryChunk.final)
                      .where(TrajectoryChunk.stream_id == stream.id, TrajectoryChunk.user_id == stream.user_id)
                      .order_by(TrajectoryChunk.seq)).all()
    seqs = [r.seq for r in rows]
    retention = timedelta(days=get_settings().learning_capture_retention_days)
    missing = holes(seqs, stream.last_seq)
    expired_before = None
    if rows and rows[0].seq > 0 and _aware(stream.started_at) < datetime.now(timezone.utc) - retention:
        expired_before = rows[0].seq  # the stream's start is past retention: what's missing there expired
    return {
        "id": str(stream.id), "run_id": str(stream.run_id), "index": stream.stream_index, "game": stream.game,
        "username": stream.username, "version": stream.version, "started_at": _iso(stream.started_at),
        "ended_at": _iso(stream.ended_at), "end_reason": stream.end_reason,
        "end_seen": stream.ended_at is not None or any(r.final for r in rows),
        "chunks": len(rows), "ticks": sum(r.ticks for r in rows), "bytes": sum(r.size_bytes for r in rows),
        "first_seq": seqs[0] if seqs else None, "last_seq_held": seqs[-1] if seqs else None,
        "last_seq_reported": stream.last_seq, "missing": missing, "expired_before_seq": expired_before,
        "from": _iso(rows[0].started_at) if rows else None, "to": _iso(rows[-1].ended_at) if rows else None,
        "coverage": stream.coverage,
    }


def streams(db: Session, user_id: uuid.UUID, *, session_id: uuid.UUID | None = None, since: datetime | None = None,
            until: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Her streams (newest first), each with what is held of it and what isn't. With a session, the streams that
    have chunks collected during it."""
    query = select(TrajectoryStream).where(TrajectoryStream.user_id == user_id)
    if session_id is not None:
        query = query.where(TrajectoryStream.id.in_(select(TrajectoryChunk.stream_id).where(
            TrajectoryChunk.user_id == user_id, TrajectoryChunk.game_session_id == session_id)))
    if since is not None:
        query = query.where((TrajectoryStream.ended_at.is_(None)) | (TrajectoryStream.ended_at >= since))
    if until is not None:
        query = query.where(TrajectoryStream.started_at <= until)
    rows = db.scalars(query.order_by(TrajectoryStream.started_at.desc()).limit(limit)).all()
    return [_stream_out(db, s) for s in rows]


def read(db: Session, user_id: uuid.UUID, stream_id: uuid.UUID, *, from_tick: int | None = None,
         to_tick: int | None = None, since: datetime | None = None, until: datetime | None = None,
         ticks: bool = True, max_ticks: int = MAX_TICKS, max_chunks: int = MAX_CHUNKS) -> dict[str, Any] | None:
    """A stretch of one stream: every tick held in it (``ticks=False``: events only), its events in order, and what
    is missing. None: no such stream of this user's."""
    stream = db.scalar(select(TrajectoryStream).where(TrajectoryStream.id == stream_id,
                                                      TrajectoryStream.user_id == user_id))
    held_query = select(TrajectoryChunk).where(TrajectoryChunk.stream_id == stream_id, TrajectoryChunk.user_id == user_id)
    if stream is None and db.scalar(select(func.count()).select_from(held_query.subquery())) == 0:
        return None
    query = held_query
    if from_tick is not None:
        query = query.where(TrajectoryChunk.first_tick + TrajectoryChunk.ticks > from_tick)
    if to_tick is not None:
        query = query.where(TrajectoryChunk.first_tick <= to_tick)
    if since is not None:
        query = query.where(TrajectoryChunk.ended_at >= since)
    if until is not None:
        query = query.where(TrajectoryChunk.started_at <= until)
    rows = list(db.scalars(query.order_by(TrajectoryChunk.seq).limit(max_chunks + 1)))
    truncated = len(rows) > max_chunks  # more than one read opens: narrow it by tick or time
    rows = rows[:max_chunks]
    out_ticks: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    corrupt: list[dict[str, Any]] = []
    coverage = None
    for row in rows:
        try:
            chunk = decode(row)
        except ChunkCorrupt as exc:
            corrupt.append({"seq": row.seq, "why": str(exc)})
            continue
        coverage = coverage or chunk.get("coverage")
        for kind_tick, offset, kind, data in chunk.get("events") or []:
            at_ms = chunk["started_ms"] + offset
            if since is not None and at_ms < since.timestamp() * 1000:
                continue
            if until is not None and at_ms > until.timestamp() * 1000:
                continue
            if from_tick is not None and kind_tick < from_tick - 1:
                continue
            if to_tick is not None and kind_tick > to_tick:
                continue
            events.append({"seq": row.seq, "tick": kind_tick, "at": _ms_iso(at_ms), "kind": kind, "data": data})
        if not ticks:
            continue
        for t in expand(chunk):
            if (from_tick is not None and t["tick"] < from_tick) or (to_tick is not None and t["tick"] > to_tick):
                continue
            if since is not None and t["at_ms"] < since.timestamp() * 1000:
                continue
            if until is not None and t["at_ms"] > until.timestamp() * 1000:
                continue
            if len(out_ticks) >= max_ticks:
                truncated = True
                break
            out_ticks.append({"tick": t["tick"], "at": _ms_iso(t["at_ms"]), "state": t["state"]})
    summary = _stream_out(db, stream) if stream is not None else {"id": str(stream_id), "header": "never saved"}
    seqs = [r.seq for r in rows]
    window_missing = [h for h in (summary.get("missing") or [])
                      if not seqs or (h["to_seq"] >= seqs[0] - 1 and h["from_seq"] <= seqs[-1] + 1)]
    # Gaps for this stream: ranges the pack dropped (named in their detail), and chunks the writer couldn't save
    # (filed under the chunk's own id).
    from app.services.learning_capture import chunk_id_for

    hole_ids = [chunk_id_for(stream_id, n) for h in window_missing if "from_seq" in h
                for n in range(h["from_seq"], min(h["to_seq"], h["from_seq"] + 2000) + 1)]
    gaps = list(db.scalars(select(CaptureGap).where(
        CaptureGap.user_id == user_id, CaptureGap.label == "trajectory",
        CaptureGap.detail.like(f"%{stream_id}%") | CaptureGap.record_id.in_(hole_ids or [uuid.uuid4()]))))
    missing = list(window_missing)
    if stream is None:
        missing.append({"what": "stream header", "note": "never saved"})
    elif not summary["end_seen"]:
        missing.append({"what": "stream end", "note": "never seen: still connected, or the pack stopped without saying"})
    missing += [{"what": "chunk unreadable", **c} for c in corrupt]
    return {
        "stream": summary, "coverage": coverage or (stream.coverage if stream else None), "chunks": seqs,
        "ticks": out_ticks if ticks else None, "truncated": truncated, "events": events, "missing": missing,
        "gaps": [{"reason": g.reason, "detail": g.detail, "detected_at": _iso(g.detected_at)} for g in gaps],
        "complete": not missing and not gaps and not truncated,
    }


def _ms_iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds")


def resolve(db: Session, user_id: uuid.UUID, at: datetime, *, session_id: uuid.UUID | None = None) -> dict[str, Any]:
    """What her trajectory holds for a moment (a notice, an outcome): ``found`` (the chunk and tick nearest before
    it), ``expired`` (older than raw retention: the detail is gone, the link remains), or ``missing`` (within
    retention, but nothing held: a hole, a gap, or no recording then)."""
    at = _aware(at)
    query = select(TrajectoryChunk).where(TrajectoryChunk.user_id == user_id, TrajectoryChunk.started_at <= at,
                                          TrajectoryChunk.ended_at >= at)
    if session_id is not None:
        query = query.where(TrajectoryChunk.game_session_id == session_id)
    row = db.scalar(query.order_by(TrajectoryChunk.started_at.desc()).limit(1))
    if row is not None:
        return {"status": "found", "stream": str(row.stream_id), "seq": row.seq, "first_tick": row.first_tick,
                "ticks": row.ticks}
    retention = timedelta(days=get_settings().learning_capture_retention_days)
    if at < datetime.now(timezone.utc) - retention:
        return {"status": "expired", "note": f"older than the {retention.days}-day raw retention: the detail is gone"}
    return {"status": "missing", "note": "nothing held for that moment (a hole, a reported gap, or no recording)"}


def measure(db: Session, user_id: uuid.UUID, *, since: datetime | None = None) -> dict[str, Any]:
    """What her trajectory costs, from what was saved: bytes per minute and hour of recorded time, a 30-day
    projection at that rate (continuous, and at the share of time she was actually recorded), and the pack's own
    overhead per tick and per chunk."""
    query = select(TrajectoryChunk.size_bytes, TrajectoryChunk.raw_bytes, TrajectoryChunk.ticks,
                   TrajectoryChunk.started_at, TrajectoryChunk.ended_at, TrajectoryChunk.overhead,
                   TrajectoryChunk.events).where(TrajectoryChunk.user_id == user_id)
    if since is not None:
        query = query.where(TrajectoryChunk.started_at >= since)
    rows = db.execute(query).all()
    if not rows:
        return {"chunks": 0, "note": "nothing recorded yet"}
    stored = sum(r.size_bytes for r in rows)
    raw = sum(r.raw_bytes for r in rows)
    ticks = sum(r.ticks for r in rows)
    seconds = sum(max(0.0, (_aware(r.ended_at) - _aware(r.started_at)).total_seconds()) for r in rows)
    first = min(_aware(r.started_at) for r in rows)
    last = max(_aware(r.ended_at) for r in rows)
    span = max(1.0, (last - first).total_seconds())
    tick_ms = sum(float((r.overhead or {}).get("tick_ms_total") or 0) for r in rows)
    tick_max = max(float((r.overhead or {}).get("tick_ms_max") or 0) for r in rows)
    encode = [float((r.overhead or {}).get("encode_ms") or 0) for r in rows]
    per_minute = stored / max(seconds / 60, 1e-9)
    days = get_settings().learning_capture_retention_days
    return {
        "chunks": len(rows), "ticks": ticks, "events": sum(r.events for r in rows), "bytes": stored, "raw_bytes": raw,
        "compression": round(raw / stored, 2) if stored else None,
        "recorded_seconds": round(seconds, 1), "span_seconds": round(span, 1),
        "recorded_share": round(min(1.0, seconds / span), 3),
        "bytes_per_minute": round(per_minute), "bytes_per_hour": round(per_minute * 60),
        f"projected_{days}_days_continuous_bytes": round(per_minute * 60 * 24 * days),
        f"projected_{days}_days_at_observed_share_bytes": round(per_minute * 60 * 24 * days * min(1.0, seconds / span)),
        "tick_handler_ms_mean": round(tick_ms / ticks, 4) if ticks else None,
        "tick_handler_ms_max": round(tick_max, 3),
        "encode_ms_mean_per_chunk": round(sum(encode) / len(encode), 3),
        "encode_ms_max_per_chunk": round(max(encode), 3),
    }


__all__ = ["decode", "expand", "apply_delta", "readable", "holes", "streams", "read", "resolve", "measure",
           "ChunkCorrupt"]
