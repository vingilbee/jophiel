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


def test_her_mind_gets_it_once_in_its_conversation_and_drops_it_when_settled(db, alice, monkeypatch):
    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    [row] = _conflicts(db, alice)
    now = datetime.now(timezone.utc)
    assert mind_conversation.conflict_problems(db, now) == []  # her reply goes first
    db.execute(update(MemoryConflict).where(MemoryConflict.id == row.id)
               .values(created_at=now - timedelta(minutes=1)))
    db.commit()
    [(scope, problem)] = mind_conversation.conflict_problems(db, now)
    assert scope == Scope(alice.id, "conversation", conversation.id) and problem.trigger == "conflict"
    assert PORTO in problem.text and "my sister in Lisbon" in problem.text
    assert problem.anchor == {"conflict": str(row.id), "event": f"conflict:{row.id}"}

    offered = []
    from app.services import mind_hard
    monkeypatch.setattr(mind_hard, "offer", lambda source, s, p, at: offered.append((s, p.trigger)) or "hard: started")
    source = mind_conversation.ConversationSource()
    source.sweep(db, now.timestamp())
    assert (scope, "conflict") in offered
    db.expire_all()
    assert db.get(MemoryConflict, row.id).offered_at is not None
    assert mind_conversation.conflict_problems(db, now) == []  # once

    assert source.dropped(db, scope, problem, None) is None
    memory_conflicts.settle(db, fact, "known_confirmed", message_id=None)
    db.commit()
    assert source.dropped(db, scope, problem, None) == "it's been settled"


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


# --- settlement racing her thought (plan stage 0: the two cases the 2026-10-29 run never exercised) ---------


class _Thinker:
    """A scripted hard thinker: ``during`` runs while she thinks (the user settling it meanwhile)."""

    def __init__(self, during=None):
        self.during, self.calls = during, 0

    def instructions(self, trigger, name, situation, **kw):
        return "system"

    def think(self, inp, situation):
        from app.ai.mind.hard import HardThought

        self.calls += 1
        if self.during:
            self.during()
        return HardThought("question", "I should check where Nora lives.", question="Is Nora in Porto or Lisbon?")


def _offered_problem(db, monkeypatch, user):
    conversation, fact = _taught(db, user)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads([], [_slip()]))
    [row] = _conflicts(db, user)
    now = datetime.now(timezone.utc)
    db.execute(update(MemoryConflict).where(MemoryConflict.id == row.id).values(created_at=now - timedelta(minutes=1)))
    db.commit()
    [(scope, problem)] = mind_conversation.conflict_problems(db, now)
    return fact, scope, problem


def _run(monkeypatch, database, scope, problem, thinker):
    from app.services import mind, mind_hard

    from app.services import cognitive_live, recall_references

    for module in (mind_hard, recall_references, cognitive_live):
        monkeypatch.setattr(module, "SessionLocal", database)
    monkeypatch.setattr(mind_hard, "thinker", lambda: thinker)
    routed, logged = [], []
    monkeypatch.setattr(mind_hard, "route", lambda *a, **k: routed.append(a) or {})
    monkeypatch.setattr(mind, "_log", logged.append)
    monkeypatch.setattr(mind, "_set_thinking", lambda *a: None)
    monkeypatch.setattr(mind, "publish_thought", lambda *a, **k: None, raising=False)
    att = mind_hard.attention_for(scope.user_id)
    att.running, att.running_scope = problem, scope
    mind_hard.run(mind_conversation.ConversationSource(), scope, att, problem, datetime.now(timezone.utc).timestamp())
    return routed, logged


def test_a_conflict_settled_while_its_thought_waited_is_never_thought_about(db, alice, monkeypatch, database):
    fact, scope, problem = _offered_problem(db, monkeypatch, alice)
    memory_conflicts.settle(db, fact, "known_confirmed", message_id=None)  # the user answered meanwhile
    db.commit()
    thinker = _Thinker()
    routed, logged = _run(monkeypatch, database, scope, problem, thinker)
    assert thinker.calls == 0 and routed == []
    assert logged[-1]["hard_dropped_before_thinking"] == {"trigger": "conflict", "why": "it's been settled"}
    from app.db.models import MindThought
    db.expire_all()
    assert list(db.scalars(select(MindThought).where(MindThought.user_id == alice.id))) == []


def test_a_conflict_settled_while_she_thinks_drops_the_thought_with_its_reason(db, alice, monkeypatch, database):
    fact, scope, problem = _offered_problem(db, monkeypatch, alice)

    def settle_meanwhile():
        with database() as other:
            memory_conflicts.settle(other, other.get(SemanticMemory, fact.id), "known_confirmed", message_id=None)
            other.commit()

    thinker = _Thinker(settle_meanwhile)
    routed, _ = _run(monkeypatch, database, scope, problem, thinker)
    assert thinker.calls == 1 and routed == []  # nothing reaches her mouth or her working memory
    from app.db.models import MindThought
    db.expire_all()
    [thought] = list(db.scalars(select(MindThought).where(MindThought.user_id == alice.id)))
    assert thought.status == "dropped" and thought.dropped_reason == "it's been settled"


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


def test_student_two_reads_an_unresolved_disagreement_with_the_users_words(db, alice, monkeypatch):
    """Snapshot v5: the memory check before cognition reaches Student 2 as MEMORY, the disputed fact as unresolved
    with what the user said; a model trained before v5 gets the situation without the section."""
    from app.ai.state import student_two as S
    from app.services import student_two

    conversation, fact = _taught(db, alice)
    _said, job = _exchange(db, conversation, LISBON_SLIP, "Say hi back!")
    _consolidate(db, monkeypatch, job, _reads(conflicts=[_slip()]))
    now = datetime.now(timezone.utc)
    found = recall.search(db, RecallRequest(user_id=alice.id, query="where does my sister Nora live",
                                            purpose="reply", now=now, kinds=("fact",)))
    memory = student_two.memory_for(db, found)
    [record] = [r for r in memory.records if r.text == PORTO]
    assert memory.search == "found" and record.section == "holds" and record.disagreed_words == "my sister in Lisbon"
    live = S.Live("user_message", "chat", now, (), "is Nora around this weekend?", memory=memory)
    situation = S.snapshot(live)
    text = S.render(situation, "memory-v1")
    assert f'- unresolved: {PORTO.rstrip(".")} (recorded today); today they said "my sister in Lisbon"' in text
    assert "MEMORY" not in S.render(situation, "exchange-line-v1")
    assert student_two.memory_for(db, found, failed=True).search == "failed"
    assert student_two.memory_for(db, None) is None
