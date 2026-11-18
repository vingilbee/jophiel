"""Facts learned while she read a file are linked to her reads of it (services/attachment_memory.py), and a
deleted file leaves the fact readable with that evidence said to be gone. Real PostgreSQL (``TEST_DATABASE_URL``);
the extraction is scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ai.recall.contracts import MemoryRef, RecallRequest
from app.db.models import MemoryEvidence
from app.schemas.long_term_memory import MemoryCandidate
from app.services import asset_store, attachment_memory, observation, recall
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_claims import _fact, _reads, _run, _said, on  # noqa: F401  (autouse fixture)
from tests.test_memory_corrections import _conversation

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    settings = SimpleNamespace(asset_store_dir=str(tmp_path / "assets"), asset_max_upload_mb=5)
    monkeypatch.setattr(asset_store, "get_settings", lambda: settings)
    return settings


def _read_file(db, user, conversation, message, asset_id, *, pages="120-128", mode="pages", query=None):
    """Her read of a file while answering ``message``, as read_attachment records it."""
    observation.record(db, observation.Report(
        user_id=user.id, source="conversation", source_instance=str(conversation.id),
        source_event_key=f"file_read:{uuid.uuid4().hex}", kind="file_read",
        payload={"asset_id": asset_id, "filename": "lease.pdf", "mode": mode, "pages": pages, "query": query,
                 "status": "read", "unit": "page", "total": 412},
        occurred_at=NOW, message_id=message.id))
    db.commit()


def _lease_ends():
    return _reads([MemoryCandidate(kind="fact", normalized_key="home:lease_end", content="The user's lease ends in March.",
                                   importance=0.7, confidence=0.9, operation="upsert")])


def _shown(db, user, fact):
    result = recall.read(db, user.id, MemoryRef("fact", str(fact.id)), include_evidence=True)
    return result, recall.render(result, now=NOW, zone="Europe/Lisbon")


def test_a_fact_learned_while_she_read_a_file_names_the_file_and_pages(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    asset_id = asset_store.save_bytes(alice.id, b"lease text", filename="lease.txt").asset_id
    asked, job = _said(db, conversation, "when does my lease end? it's attached", "Page 121 says it ends in March.")
    _read_file(db, alice, conversation, asked, asset_id)
    _read_file(db, alice, conversation, asked, asset_id, pages="121", mode="find", query="end date")
    _run(db, monkeypatch, job, _lease_ends())

    fact = _fact(db, alice, "home:lease_end")
    result, text = _shown(db, alice, fact)
    (record,) = result.records
    assert record.read_from == (f'"lease.pdf", pages 120-128 [file {asset_id}]',
                                f'"lease.pdf", searching for "end date" (page 121) [file {asset_id}]')
    assert [e.role for e in record.evidence] == ["origin", "basis", "basis"]
    assert "learned while you read \"lease.pdf\", pages 120-128" in text
    # The same job's links again (a retried job) add nothing.
    assert attachment_memory.link_reads(db, alice.id, asked.id, [fact.id]) == 2
    db.commit()
    assert db.scalar(select(func.count()).select_from(MemoryEvidence).where(
        MemoryEvidence.fact_id == fact.id, MemoryEvidence.role == "basis")) == 2


def test_a_deleted_file_leaves_the_fact_readable_with_its_evidence_said_gone(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    asset_id = asset_store.save_bytes(alice.id, b"lease text", filename="lease.txt").asset_id
    asked, job = _said(db, conversation, "when does my lease end?", "It ends in March (page 121).")
    _read_file(db, alice, conversation, asked, asset_id)
    _run(db, monkeypatch, job, _lease_ends())
    asset_store.delete_user_files(alice.id)

    fact = _fact(db, alice, "home:lease_end")
    result, text = _shown(db, alice, fact)
    assert result.status == "matched" and fact.content in text
    (record,) = result.records
    assert record.read_from == ('"lease.pdf", pages 120-128 (the file has since been deleted)',)
    assert "origin" in [e.role for e in record.evidence]
    # A search still finds it, the same way.
    found = recall.render_bundle(recall.search(db, RecallRequest(
        user_id=alice.id, query="lease end", purpose="reply", now=NOW)), now=NOW, zone="Europe/Lisbon")
    assert "the file has since been deleted" in found


def test_only_her_reads_in_that_exchange_count_and_a_failing_file_check_claims_nothing(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    asset_id = asset_store.save_bytes(alice.id, b"lease text", filename="lease.txt").asset_id
    earlier, _ = _said(db, conversation, "here's my lease", "Got it.")
    _read_file(db, alice, conversation, earlier, asset_id)
    bob = _user(db, "bob")
    bobs = _conversation(db, bob)
    asked, job = _said(db, conversation, "my lease ends in March", "Noted.")
    # Another user's read under the same message id can never be linked (ownership is checked).
    _read_file(db, bob, bobs, asked, asset_id)
    _run(db, monkeypatch, job, _lease_ends())
    fact = _fact(db, alice, "home:lease_end")
    result, text = _shown(db, alice, fact)
    assert result.records[0].read_from == () and "learned while" not in text

    def broken(user_id, asset_id):
        raise OSError("disk unavailable")

    assert attachment_memory.link_reads(db, alice.id, earlier.id, [fact.id]) == 1
    db.commit()
    monkeypatch.setattr(asset_store, "get_record", broken)
    result, _ = _shown(db, alice, fact)
    assert result.records[0].read_from == ('"lease.pdf", pages 120-128',)


def test_an_empty_or_fenced_reply_links_nothing(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    asset_id = asset_store.save_bytes(alice.id, b"lease text", filename="lease.txt").asset_id
    asked, job = _said(db, conversation, "my lease ends in March", " ")
    _read_file(db, alice, conversation, asked, asset_id)
    _run(db, monkeypatch, job, _lease_ends())
    assert db.scalar(select(func.count()).select_from(MemoryEvidence).where(MemoryEvidence.role == "basis")) == 0
