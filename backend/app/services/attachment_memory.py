"""Facts learned while she read a file, linked to her reads of it.

A fact memory keeps from an exchange in which she read an attached file gets a ``basis`` evidence link to each of
her reads in that exchange (the ``conversation.file_read`` observations, recorded by ``read_attachment``). The
link is coarse on purpose: every fact kept from that exchange points to every file read in it, the user's own
statements included. It says "she was reading this when she learned it", never "the file says so".

The file can be deleted while the fact stays (the user's decision, 2026-11-18: she keeps what she learned, only the
file is gone). The read and its link stay too, as history; when it's read back, a missing file is said to be
deleted, never an error. The file's text is never copied here: the link names the file and the pages.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MemoryEvidence, Observation

log = logging.getLogger(__name__)

ROLE = "basis"
KIND = "file_read"
READS_MAX = 10  # reads linked from one exchange (a reply's reads are bounded anyway)
SHOWN_MAX = 5  # file reads named per fact when it's read back


def link_reads(db: Session, user_id: uuid.UUID, message_id: uuid.UUID | None, fact_ids: Iterable[uuid.UUID]) -> int:
    """Link each fact kept from the exchange answering ``message_id`` to her file reads in it (in the caller's
    transaction). Returns how many reads were found. Never fails the caller: memory stands without the link."""
    facts = list(dict.fromkeys(fact_ids))
    if message_id is None or not facts:
        return 0
    from app.services import memory_manager

    try:
        with db.begin_nested():  # a failure here rolls back only the links, never the facts
            reads = list(db.scalars(select(Observation.id).where(
                Observation.user_id == user_id, Observation.message_id == message_id, Observation.kind == KIND)
                .order_by(Observation.id).limit(READS_MAX)))
            for read_id in reads:
                for fact_id in facts:
                    memory_manager.link_target(db, user_id, role=ROLE,
                                               source=memory_manager.Source("observation", read_id), fact_id=fact_id)
        return len(reads)
    except Exception:  # noqa: BLE001 - a fact is kept whether or not its file link is
        log.exception("attachment memory: linking file reads for message %s failed", message_id)
        return 0


@dataclass(frozen=True)
class FileSource:
    observation_id: int
    line: str  # "lease.pdf", pages 120-128 [file 1a2b…] / ... (the file has since been deleted)


def _words(payload: dict) -> str:
    name = f'"{payload.get("filename") or "a file"}"'
    unit, pages = payload.get("unit") or "page", payload.get("pages") or ""
    units = f"{unit}s" if "," in pages or "-" in pages else unit
    mode = payload.get("mode")
    if mode == "find":
        found = f" ({units} {pages})" if pages else ""
        return f'{name}, searching for "{payload.get("query") or ""}"{found}'
    if mode == "overview":
        return f"{name} (its overview" + (f" and {units} {pages})" if pages else ")")
    return f"{name}, {units} {pages}" if pages else name


def _file_state(user_id: uuid.UUID, asset_id: str, cache: dict[str, str | None]) -> str | None:
    """``present`` / ``deleted``, or None when it couldn't be checked (then nothing is claimed about it)."""
    if asset_id not in cache:
        from app.services import asset_store

        try:
            asset_store.get_record(user_id, asset_id)
            cache[asset_id] = "present"
        except (asset_store.AssetNotFoundError, ValueError):
            cache[asset_id] = "deleted"
        except Exception:  # noqa: BLE001 - reading memory never fails on a file check
            log.exception("attachment memory: checking file %s failed", asset_id)
            cache[asset_id] = None
    return cache[asset_id]


def sources(db: Session, fact_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[FileSource]]:
    """The file reads each fact was learned in, as lines, by fact id. Facts with none are left out. A deleted file
    is said to be deleted; nothing here raises for a missing file."""
    ids = list(dict.fromkeys(fact_ids))
    if not ids:
        return {}
    rows = db.execute(select(MemoryEvidence.fact_id, Observation).join(
        Observation, Observation.id == MemoryEvidence.observation_id).where(
        MemoryEvidence.fact_id.in_(ids), MemoryEvidence.role == ROLE, Observation.kind == KIND,
        Observation.user_id == MemoryEvidence.user_id).order_by(Observation.id)).all()
    out: dict[uuid.UUID, list[FileSource]] = {}
    seen: dict[uuid.UUID, set[str]] = {}
    cache: dict[str, str | None] = {}
    for fact_id, obs in rows:
        payload = obs.payload or {}
        words = _words(payload)
        if words in seen.setdefault(fact_id, set()) or len(out.get(fact_id, [])) >= SHOWN_MAX:
            continue
        seen[fact_id].add(words)
        asset_id = str(payload.get("asset_id") or "")
        state = _file_state(obs.user_id, asset_id, cache) if asset_id else "deleted"
        tail = {"present": f" [file {asset_id}]", "deleted": " (the file has since been deleted)"}.get(state, "")
        out.setdefault(fact_id, []).append(FileSource(obs.id, words + tail))
    return out
