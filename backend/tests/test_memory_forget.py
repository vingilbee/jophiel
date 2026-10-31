"""Forgetting what the user asks (services/memory_forget.py; plan stage 2): the knee goes from every memory
store while the peanut allergy stays, a broad wish never reaches what wasn't named, nothing comes back from
the messages it came from, and she's told when it hasn't happened yet. Real PostgreSQL (``TEST_DATABASE_URL``);
the model calls are scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

import app.core.config as config
import app.db.session as db_session
from app.ai.recall.contracts import RecallRequest
from app.db.models import (
    EpisodicMemory, MemoryEvent, MemoryForget, MemoryForgottenSource, MemoryJob, SemanticMemory, WorkingItem,
)
from app.schemas.long_term_memory import ForgetRequest, MemoryCandidate, MemoryExtraction
from app.services import (
    long_term_memory, memory_conflicts, memory_corrections, memory_forget, memory_manager, recall, recall_references,
)
from app.services.memory_manager import Source
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _consolidate, _conversation, _exchange

KNEE = "ugh and my left knee has been acting up since the half marathon in May"
FORGET = "hey, can you forget what I told you about my knee? I'd rather you didn't keep health stuff like that"


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={
        "memory_manager_enabled": True, "long_term_memory_enabled": True, "memory_corrections_enabled": True,
        "recall_enabled": True, "memory_manager_log_path": str(tmp_path / "memory.jsonl"),
        "recall_log_path": str(tmp_path / "recall.jsonl"), "redis_url": None})
    for module in (memory_manager, memory_corrections, memory_conflicts, memory_forget, long_term_memory, recall,
                   recall_references, config):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "SessionLocal", database)
    monkeypatch.setattr(recall_references, "SessionLocal", database)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(recall, "embed", lambda text: None)
    return settings


def _reads(memories=(), forget=()):
    def extract(user, assistant, earlier=(), known=(), **_context):
        extract.seen = {"earlier": list(earlier), "known": list(known)}
        return MemoryExtraction(memories=list(memories), forget=list(forget))
    extract.seen = {}
    return extract


def _sam(db, user):
    """Sam after the teaching chat: the knee as an episode, a question she raised about it, the allergy."""
    conversation = _conversation(db, user)
    knee, job = _exchange(db, conversation, KNEE, "Oh no, I hope it settles down.")
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    db.add(EpisodicMemory(user_id=user.id, conversation_id=conversation.id,
                          summary="The user's left knee has been acting up since the half marathon in May.",
                          source_message_ids=[str(knee.id)], importance=0.6, confidence=0.9))
    allergy = SemanticMemory(user_id=user.id, kind="constraint", normalized_key="constraint:peanut_allergy",
                             content="The user is allergic to peanuts.", importance=0.9, confidence=1.0,
                             source_conversation_id=conversation.id)
    db.add(allergy)
    question = memory_manager.add_item(db, user.id, kind="question", text_="How is their knee doing after the race?",
                                       owner="columbina", epistemic="inferred", source=Source("message", knee.id))
    db.commit()
    return conversation, knee, allergy, question


def _asked(db, monkeypatch, conversation, said=FORGET, forget=None, memories=None):
    asked, job = _exchange(db, conversation, said, "Of course, I'll forget that.")
    forget = [ForgetRequest(about="my knee", user_words="forget what I told you about my knee")] if forget is None else forget
    memories = [MemoryCandidate(kind="preference", normalized_key="preference:health_privacy",
                                content="The user prefers that health information not be retained.",
                                importance=0.7, confidence=0.9)] if memories is None else memories
    _consolidate(db, monkeypatch, job, _reads(memories, forget))
    return asked


def _all(_ref):
    """A judge that takes every candidate it's shown: what the code's own fences let through."""
    def judge(request, message, shown):
        judge.shown = shown
        return memory_forget.ForgetJudgement(forget=[c["ref"] for c in shown])
    judge.shown = []
    return judge


def _forget_once(db, judge):
    db.expire_all()
    due = memory_forget.next_due(db)
    db.rollback()
    assert due is not None
    return memory_forget.process(db, due, judge=judge)


def test_forgetting_the_knee_removes_it_everywhere_and_keeps_the_allergy(db, alice, monkeypatch):
    conversation, knee, allergy, question = _sam(db, alice)
    asked = _asked(db, monkeypatch, conversation)
    [row] = db.scalars(select(MemoryForget).where(MemoryForget.user_id == alice.id)).all()
    assert row.status == "pending" and row.request["user_words"] == ["forget what I told you about my knee"]
    note = memory_forget.status_note(db, alice.id)
    assert "still being forgotten" in note and "not that it's already gone" in note

    judge = _all(None)
    assert _forget_once(db, judge) == "done"
    kinds = sorted(c["kind"] for c in judge.shown)
    # Only what shares a word with the request: the allergy and the preference the asking message made aren't
    # even candidates, whatever a judge would say.
    assert all("knee" in c["text"].lower() for c in judge.shown), judge.shown
    assert "item" in kinds and "episode" in kinds
    db.expire_all()
    assert db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all() == []
    assert db.get(WorkingItem, question.id) is None
    assert db.scalar(select(MemoryEvent).where(MemoryEvent.user_id == alice.id, MemoryEvent.text.ilike("%knee%"))) is None
    facts = {f.normalized_key: f.status for f in db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == alice.id))}
    assert facts == {"constraint:peanut_allergy": "active", "preference:health_privacy": "active"}
    row = db.get(MemoryForget, row.id)
    assert row.request is None and row.removed["episode"] == 1 and row.removed["item"] == 1
    sources = set(db.scalars(select(MemoryForgottenSource.message_id).where(MemoryForgottenSource.forget_id == row.id)))
    assert {knee.id, asked.id} <= sources
    assert memory_forget.status_note(db, alice.id) is None

    # Recall finds nothing of it; the allergy still rides along.
    found = recall.search(db, RecallRequest(user_id=alice.id, query="how's my knee?", purpose="reply",
                                            now=datetime.now(timezone.utc)))
    assert [r.content for r in found.records] == ["The user is allergic to peanuts."]


def test_nothing_comes_back_from_the_messages_it_came_from(db, alice, monkeypatch):
    conversation, knee, _allergy, _question = _sam(db, alice)
    _asked(db, monkeypatch, conversation)
    _forget_once(db, _all(None))
    # The next exchange's extractor never reads the knee message as context.
    _said, job = _exchange(db, conversation, "anyway, what should I cook tonight?", "Maybe a stir fry?")
    extract = _reads()
    _consolidate(db, monkeypatch, job, extract)
    assert not any("knee" in line for line in extract.seen["earlier"]), extract.seen["earlier"]
    # A late or retried job for the knee message itself extracts nothing.
    db.execute(update(MemoryJob).where(MemoryJob.conversation_id == conversation.id,
                                       MemoryJob.assistant_message_id.in_(select(
                                           long_term_memory.ChatMessage.id).where(
                                           long_term_memory.ChatMessage.turn_id == knee.turn_id,
                                           long_term_memory.ChatMessage.role == "assistant"))).values(status="pending"))
    db.commit()
    [late] = db.scalars(select(MemoryJob).where(MemoryJob.conversation_id == conversation.id,
                                                MemoryJob.status == "pending")).all()
    called = []
    monkeypatch.setattr(long_term_memory, "_extract", lambda *a, **k: called.append(a) or MemoryExtraction(
        memories=[MemoryCandidate(kind="episode", normalized_key="episode:knee", content="knee again",
                                  importance=0.5, confidence=0.5)]))
    assert long_term_memory.process_job(db, late.id) is True
    assert called == [] and db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all() == []


def test_a_broad_wish_never_takes_what_they_didnt_name(db, alice, monkeypatch):
    conversation, _knee, allergy, _question = _sam(db, alice)
    # Only the broad wish, no "forget X": the extractor (wrongly) marks the allergy forgotten by key.
    wish = "I'd rather you didn't keep health stuff like that"
    _asked(db, monkeypatch, conversation, said=wish, forget=[], memories=[MemoryCandidate(
        operation="forget", kind="constraint", normalized_key="constraint:peanut_allergy",
        content="The user is allergic to peanuts.", importance=0.9, confidence=1.0)])
    db.expire_all()
    assert db.get(SemanticMemory, allergy.id).status == "active"  # not named in the message: not touched
    assert memory_forget.next_due(db) is None


def test_a_fact_forgotten_by_key_is_hidden_at_once_and_removed_after(db, alice, monkeypatch):
    conversation, _knee, _allergy, _question = _sam(db, alice)
    db.add(SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:gym", content="The user goes to the gym "
                          "on Mondays.", importance=0.5, confidence=0.9))
    db.commit()
    _asked(db, monkeypatch, conversation, said="please forget the gym thing", forget=[], memories=[MemoryCandidate(
        operation="forget", kind="fact", normalized_key="fact:gym", content="gym", importance=0.5, confidence=0.5)])
    db.expire_all()
    gym = db.scalar(select(SemanticMemory).where(SemanticMemory.normalized_key == "fact:gym"))
    assert gym.status == "deleted"  # recall can't see it while the request is carried out
    assert _forget_once(db, lambda request, message, shown: memory_forget.ForgetJudgement()) == "done"
    db.expire_all()
    assert db.scalar(select(SemanticMemory).where(SemanticMemory.normalized_key == "fact:gym")) is None


def test_it_waits_for_earlier_memory_jobs_and_ignores_ids_it_wasnt_shown(db, alice, monkeypatch):
    conversation, knee, _allergy, _question = _sam(db, alice)
    _asked(db, monkeypatch, conversation)
    db.execute(update(MemoryJob).where(MemoryJob.conversation_id == conversation.id).values(status="pending"))
    db.commit()
    assert _forget_once(db, _all(None)) == "pending"  # an earlier job could still write the knee
    db.execute(update(MemoryJob).where(MemoryJob.conversation_id == conversation.id).values(status="completed"))
    db.execute(update(MemoryForget).values(next_attempt_at=datetime.now(timezone.utc)))
    db.commit()
    made_up = lambda request, message, shown: memory_forget.ForgetJudgement(forget=["m99", "fact:anything"])  # noqa: E731
    assert _forget_once(db, made_up) == "nothing_found"
    db.expire_all()
    assert len(db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all()) == 1


def test_words_that_arent_in_the_message_are_not_a_request(db, alice, monkeypatch):
    conversation, _knee, _allergy, _question = _sam(db, alice)
    _asked(db, monkeypatch, conversation, said="how are you today?",
           forget=[ForgetRequest(about="my knee", user_words="forget my knee")], memories=[])
    assert memory_forget.next_due(db) is None


def test_a_forget_that_keeps_failing_says_so_and_clears_the_request(db, alice, monkeypatch):
    conversation, _knee, _allergy, _question = _sam(db, alice)
    _asked(db, monkeypatch, conversation)

    def broken(request, message, shown):
        raise RuntimeError("model down")

    for _ in range(memory_forget.MAX_ATTEMPTS):
        db.execute(update(MemoryForget).values(next_attempt_at=datetime.now(timezone.utc)))
        db.commit()
        status = _forget_once(db, broken)
    assert status == "failed"
    db.expire_all()
    [row] = db.scalars(select(MemoryForget)).all()
    assert row.request is None and row.finished_at is not None
    assert "didn't work" in memory_forget.status_note(db, alice.id)
    assert len(db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all()) == 1
