"""A message that disagrees with what she remembers is kept unresolved, never applied
(services/memory_conflicts.py): the fact stays as it was, recall marks it unresolved, a later message or revision
settles it, and her mind gets it once as a problem in the conversation it came from. Real PostgreSQL
(``TEST_DATABASE_URL``); the model calls are scripted."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.core.config as config
import app.db.session as db_session
from app.ai.mind.contracts import Scope
from app.ai.recall.contracts import MemoryRef, RecallRequest
from app.db.models import MemoryConflict, SemanticMemory
from app.schemas.long_term_memory import ConflictCandidate, MemoryCandidate, MemoryExtraction
from app.services import (
    long_term_memory, memory_conflicts, memory_corrections, memory_manager, memory_reconcile, mind_conversation,
    recall,
)
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, bob, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _consolidate, _conversation, _exchange

KEY = "relationship:sister"
PORTO = "The user's sister Nora moved to Porto last month."
LISBON_SLIP = "talked to my sister in Lisbon yesterday, she says hi"


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "long_term_memory_enabled": True, "memory_corrections_enabled": True,
        "recall_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "recall_log_path": str(tmp_path / "recall.jsonl"), "redis_url": None})
    for module in (memory_manager, memory_corrections, memory_reconcile, memory_conflicts, long_term_memory,
                   recall, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


def _reads(memories=(), conflicts=(), settled=()):
    seen = {}

    def extract(user, assistant, earlier=(), known=(), **_context):
        seen["known"] = list(known)
        return MemoryExtraction(memories=list(memories), conflicts=list(conflicts), settled=list(settled))
    extract.seen = seen
    return extract


def _taught(db, user):
    """The fact as the user last stated it, from an earlier message."""
    conversation = _conversation(db, user)
    taught, job = _exchange(db, conversation, "Nora moved to Porto last month.", "Porto, lovely.")
    fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key=KEY, content=PORTO, importance=0.7,
                          confidence=0.9, source_conversation_id=conversation.id, source_message_id=taught.id)
    db.add(fact)
    from app.db.models import MemoryJob
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    db.commit()
    return conversation, fact


def _slip(**kw):
    return ConflictCandidate(known_key=KEY, stated=kw.get("stated", "sister is in Lisbon"),
                             user_words=kw.get("words", "my sister in Lisbon"))


def _conflicts(db, user):
    db.expire_all()
    return list(db.scalars(select(MemoryConflict).where(MemoryConflict.user_id == user.id)
                           .order_by(MemoryConflict.created_at)))


def test_a_disagreement_is_kept_and_the_fact_left_as_it_was(db, alice, monkeypatch):
    conversation, fact = _taught(db, alice)
    said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    # The model also offers a reworded fact for the same key: a disputed fact is never reworded in that job.
    lisbon = MemoryCandidate(kind="fact", normalized_key=KEY, content="The user's sister lives in Lisbon.",
                             importance=0.7, confidence=0.9)
    _consolidate(db, monkeypatch, job, _reads([lisbon], [_slip()]))
    db.refresh(fact)
    assert fact.content == PORTO and fact.revision == 1 and fact.status == "active"
    [row] = _conflicts(db, alice)
    assert (row.status, row.fact_id, row.fact_revision, row.source_message_id, row.conversation_id) == (
        "open", fact.id, 1, said.id, conversation.id)
    assert row.known_content == PORTO and row.user_words == "my sister in Lisbon"


@pytest.mark.parametrize("conflict, why", [
    (ConflictCandidate(known_key="relationship:brother", stated="x", user_words="my sister in Lisbon"), "not shown"),
    (ConflictCandidate(known_key=KEY, stated="in Lisbon", user_words="my sister lives in Lisbon"), "not her words"),
])
def test_a_disagreement_the_words_dont_support_is_dropped(db, alice, monkeypatch, conflict, why):
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [conflict]))
    assert _conflicts(db, alice) == [], why


def test_recall_and_consolidation_see_it_unresolved_until_settled(db, alice, monkeypatch):
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    record = recall.read(db, alice.id, MemoryRef("fact", str(fact.id))).records[0]
    assert record.content == PORTO and record.applicability == "current"
    assert 'unresolved: on ' in record.lifecycle and '"my sister in Lisbon"' in record.lifecycle
    found = recall.search(db, RecallRequest(user_id=alice.id, query="where does my sister Nora live",
                                            purpose="reply", now=datetime.now(timezone.utc), kinds=("fact",)))
    assert "unresolved" in (recall.render_legacy(found) or "")
    # The next consolidation is shown the disagreement next to the fact, and can settle it.
    _answer, job = _exchange(db, conversation, "no she's still in Porto, I was visiting Lisbon with her",
                             "Ah, a trip!")
    reads = _reads(settled=[KEY])
    _consolidate(db, monkeypatch, job, reads)
    assert any(line.startswith(f"{KEY}: {PORTO} [unresolved: on ") for line in reads.seen["known"])
    [row] = _conflicts(db, alice)
    assert row.status == "known_confirmed" and row.settled_by_message_id == _answer.id
    record = recall.read(db, alice.id, MemoryRef("fact", str(fact.id))).records[0]
    assert "unresolved" not in (record.lifecycle or "")


def test_a_later_revision_of_the_fact_settles_it(db, alice, monkeypatch):
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    moved, job = _exchange(db, conversation, "Nora moved back to Lisbon this week, actually", "Oh, back again!")
    back = MemoryCandidate(kind="fact", normalized_key=KEY, content="The user's sister Nora moved back to Lisbon.",
                           importance=0.7, confidence=0.9)
    _consolidate(db, monkeypatch, job, _reads([back]))
    [row] = _conflicts(db, alice)
    assert (row.status, row.settled_by_message_id) == ("fact_changed", moved.id)


def test_forgetting_the_fact_settles_it(db, alice, monkeypatch):
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    _asked, job = _exchange(db, conversation, "forget what I told you about my sister", "Done.")
    _consolidate(db, monkeypatch, job, _reads([MemoryCandidate(
        operation="forget", kind="fact", normalized_key=KEY, content="forget it", importance=0.1, confidence=1)]))
    assert [r.status for r in _conflicts(db, alice)] == ["forgotten"]


def test_her_mind_is_woken_once_in_its_conversation_and_a_settled_one_is_never_brought_up(db, alice, monkeypatch):
    from app.db.models import CoordinatorWork
    from app.services import autonomous_speech, coordinator

    monkeypatch.setattr(coordinator, "_wake", lambda: None)
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    [row] = _conflicts(db, alice)
    now = datetime.now(timezone.utc)
    source = mind_conversation.ConversationEvents()
    [handed] = source.sweep(db, now.timestamp())
    assert handed.startswith(f"conflict {row.id.hex[:6]}: Proposed")
    # One durable wake of her mind, in the conversation it was said in, about this disagreement; nothing said yet.
    [work] = list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.user_id == alice.id)))
    assert (work.kind, work.conversation_id, work.input["wake"], work.input["premise"]) == (
        "mind", conversation.id, "conflict", {"conflict": str(row.id)})
    assert PORTO.rstrip(".") in work.result["conclusion"] and "my sister in Lisbon" in work.result["conclusion"]
    db.expire_all()
    assert db.get(MemoryConflict, row.id).offered_at is not None
    assert source.sweep(db, now.timestamp() + 60) == []  # handed over once
    assert autonomous_speech.why_not(db, work) is None
    # Settled before she was woken (or while she worded it): the premise no longer holds, nothing is said.
    memory_conflicts.settle(db, fact, "known_confirmed", message_id=None)
    db.commit()
    assert autonomous_speech.why_not(db, work) == "the disagreement it was about is settled"


def test_a_conflict_must_name_a_fact_of_the_same_user(db, alice, bob):
    """Ownership is a key: a row pointing at someone else's fact can't be written."""
    from sqlalchemy.exc import IntegrityError

    conversation, fact = _taught(db, alice)
    theirs = _conversation(db, bob)
    said, _job = _exchange(db, theirs, LISBON_SLIP)
    db.add(MemoryConflict(user_id=bob.id, fact_id=fact.id, fact_revision=1, known_content=PORTO, stated="x",
                          user_words="my sister", source_message_id=said.id, conversation_id=theirs.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- what depends on a disagreement (plan stage 2) ----------------------------------------------------------


def test_settling_a_disagreement_closes_the_question_raised_about_it(db, alice, monkeypatch):
    from app.db.models import WorkingItem
    from app.services import thought_memory

    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    [row] = _conflicts(db, alice)
    question = memory_manager.add_item(db, alice.id, kind="question", text_="Is Nora in Porto or Lisbon now?",
                                       owner="columbina", epistemic="inferred")
    question.premise_conflict_id = row.id
    unrelated = memory_manager.add_item(db, alice.id, kind="question", text_="What is Nora's job?",
                                        owner="columbina", epistemic="inferred")
    db.commit()
    assert thought_memory._premise(db, alice.id, str(row.id)).id == row.id
    settled_by, job = _exchange(db, conversation, "no she's still in Porto, I was visiting", "Got it.")
    _consolidate(db, monkeypatch, job, _reads([], [], [KEY]))
    db.expire_all()
    closed = db.get(WorkingItem, question.id)
    assert (closed.status, closed.closed_at is not None) == ("answered", True)
    assert "settled (known confirmed)" in closed.status_reason
    assert db.get(WorkingItem, unrelated.id).closed_at is None  # only what depended on it
    # A thought about it that lands after the settlement leaves nothing open.
    assert thought_memory._premise(db, alice.id, str(row.id)) is None


def test_her_mind_reads_an_unresolved_disagreement_with_the_users_words(db, alice, monkeypatch):
    """The memory check her mind reads (the recall section of her prompt): the disputed fact marked unresolved, with
    what the user said; a failed search is said as failed, never as nothing found."""
    from app.ai.recall.contracts import RecallResult

    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads(conflicts=[_slip()]))
    now = datetime.now(timezone.utc)
    found = recall.search(db, RecallRequest(user_id=alice.id, query="where does my sister Nora live",
                                            purpose="reply", now=now, kinds=("fact",)))
    text = recall.render_bundle(found, now=now, zone="UTC")
    assert text.index("Unresolved") < text.index(PORTO.rstrip("."))  # under its own heading, with her fact
    assert 'unresolved: on ' in text and 'they said "my sister in Lisbon"' in text
    failed = recall.render_bundle(RecallResult.failed("search_failed"), now=now, zone="UTC")
    assert "fail" in failed.lower()
