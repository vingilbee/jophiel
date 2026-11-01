"""Forgetting, stage 5 (services/memory_forget.py; docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): every mention is
found however many there are, mixed messages keep what isn't about it, the conversation stays searchable history,
and nothing relearns it. Real PostgreSQL (``TEST_DATABASE_URL``); the model calls are scripted."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.ai.recall.contracts import MemoryRef
from app.db.models import (
    ChatMessage, EpisodicMemory, MemoryForget, MemoryForgottenSource, MemoryJob, SemanticMemory,
)
from app.schemas.long_term_memory import ForgetRequest, MemoryCandidate, MemoryExtraction
from app.services import (
    long_term_memory, memory_forget, recall, recall_expand, recall_references, transcript_search,
)
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _consolidate, _conversation, _exchange
from tests.test_memory_forget import FORGET, _reads, on  # noqa: F401  (``on``: autouse settings fixture)

NOW = datetime.now(timezone.utc)
MIXED = "My knee has been sore since the race. Also, I'm allergic to peanuts, so no satay please."
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def scripted(words=("knee", "injury"), *, separable=True, fail_on=None):
    """A judge that finds the mentions by word: quotes their sentences, or says whole (``separable=False``)."""
    def judge(request, message, shown):
        judge.calls.append("messages" if shown and shown[0]["kind"] == "message" else "records")
        if fail_on is not None and len(judge.calls) in fail_on:
            raise RuntimeError("model down")
        if shown and shown[0]["kind"] == "message":
            cuts = []
            for item in shown:
                sentences = [s for s in _SENTENCE.split(item["text"]) if s.strip()]
                about = [s for s in sentences if any(w in s.lower() for w in words)]
                if about:
                    cuts.append(memory_forget.MessageCut(id=item["ref"], quotes=about if separable else [],
                                                         whole=not separable,
                                                         other_topics=len(about) < len(sentences)))
            return memory_forget.ForgetJudgement(messages=cuts)
        return memory_forget.ForgetJudgement(forget=[c["ref"] for c in shown
                                                     if any(w in c["text"].lower() for w in words)])
    judge.calls = []
    return judge


def _ask(db, monkeypatch, conversation, said=FORGET):
    asked, job = _exchange(db, conversation, said, "Of course, I'll forget that.")
    _consolidate(db, monkeypatch, job, _reads([], [ForgetRequest(about="my knee",
                                                                  user_words="forget what I told you about my knee")]))
    return asked


def _run(db, judge, limit=20):
    """Carry the forget out to its end (it yields between scan batches). Returns the statuses seen."""
    seen = []
    for _ in range(limit):
        db.expire_all()
        db.execute(update(MemoryForget).where(MemoryForget.status == "pending")
                   .values(next_attempt_at=datetime.now(timezone.utc)))
        db.commit()
        due = memory_forget.next_due(db)
        db.rollback()
        if due is None:
            break
        seen.append(memory_forget.process(db, due, judge=judge))
    return seen


def _fences(db, user):
    return dict(db.execute(select(MemoryForgottenSource.message_id, MemoryForgottenSource.spans).where(
        MemoryForgottenSource.user_id == user.id)).all())


def _complete(db, job):
    db.execute(update(MemoryJob).where(MemoryJob.id == job.id).values(status="completed"))
    db.commit()


def test_every_mention_is_found_past_one_batch_and_indirect_ones_too(db, alice, monkeypatch):
    monkeypatch.setattr(memory_forget, "SCAN_BATCHES_PER_RUN", 1)
    replies = []
    for c in range(2):
        conversation = _conversation(db, alice)
        for i in range(20):
            said, job = _exchange(db, conversation, f"day {c}-{i}: my knee is acting up again after the run.",
                                  "I'm sorry, I hope that injury settles soon.")
            _complete(db, job)
            replies.append(db.scalar(select(ChatMessage).where(ChatMessage.turn_id == said.turn_id,
                                                               ChatMessage.role == "assistant")))
    db.add(EpisodicMemory(user_id=alice.id, conversation_id=conversation.id, summary="The user's knee acts up "
                          "after runs.", source_message_ids=[str(said.id)], importance=0.6, confidence=0.9))
    db.commit()
    _ask(db, monkeypatch, conversation)
    judge = scripted()
    seen = _run(db, judge)
    assert seen[0] == "pending" and seen[-1] == "done", seen  # it yielded between batches, and finished
    assert judge.calls.count("messages") >= 3 and judge.calls[-1] == "records"
    db.expire_all()
    fences = _fences(db, alice)
    knee_messages = [m for m in db.scalars(select(ChatMessage).where(ChatMessage.content.ilike("%knee is acting%")))]
    assert len(knee_messages) == 40 and all(m.id in fences for m in knee_messages)
    # Her replies never say "knee": found as the messages after each mention, fenced by span.
    assert all(fences.get(r.id) for r in replies)
    assert db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all() == []
    row = db.scalar(select(MemoryForget))
    assert row.status == "done" and row.removed["messages"] >= 80 and row.fence and row.request is None


def test_while_it_scans_she_is_told_it_isnt_done(db, alice, monkeypatch):
    monkeypatch.setattr(memory_forget, "SCAN_BATCHES_PER_RUN", 1)
    monkeypatch.setattr(memory_forget, "SCAN_BATCH", 2)
    conversation = _conversation(db, alice)
    for i in range(5):
        _said, job = _exchange(db, conversation, f"my knee hurts {i}", "Oh no.")
        _complete(db, job)
    _ask(db, monkeypatch, conversation)
    assert _run(db, scripted(), limit=1) == ["pending"]
    assert "still being forgotten" in memory_forget.status_note(db, alice.id)
    assert _run(db, scripted())[-1] == "done"
    assert memory_forget.status_note(db, alice.id) is None


def test_a_mixed_message_keeps_what_isnt_about_it(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    mixed, job = _exchange(db, conversation, MIXED, "Noted, no peanuts.")
    _complete(db, job)
    allergy = SemanticMemory(user_id=alice.id, kind="constraint", normalized_key="constraint:peanut_allergy",
                             content="The user is allergic to peanuts.", importance=0.9, confidence=1.0,
                             source_message_id=mixed.id, source_conversation_id=conversation.id)
    db.add(allergy)
    db.add(EpisodicMemory(user_id=alice.id, conversation_id=conversation.id, summary="The user's knee has been "
                          "sore since a race.", source_message_ids=[str(mixed.id)], importance=0.6, confidence=0.9))
    db.commit()
    _ask(db, monkeypatch, conversation)
    assert _run(db, scripted())[-1] == "done"
    db.expire_all()
    assert db.get(SemanticMemory, allergy.id).status == "active"
    spans = _fences(db, alice)[mixed.id]
    assert spans and MIXED[spans[0][0]:spans[0][1]] == "My knee has been sore since the race."
    view = memory_forget.memory_view(MIXED, None, "user", memory_forget.memory_cuts(db, alice.id, [mixed.id])[mixed.id])
    assert "knee" not in view and "allergic to peanuts" in view and memory_forget.FORGOTTEN_MARK in view
    # The next extraction reads the rest of it as context, never the knee.
    _said, later = _exchange(db, conversation, "what should I cook tonight?", "Maybe a stir fry, no peanuts.")
    extract = _reads()
    _consolidate(db, monkeypatch, later, extract)
    earlier = " ".join(extract.seen["earlier"])
    assert "allergic to peanuts" in earlier and "knee" not in earlier.lower()
    # Nor does a memory's source bring it back: the allergy's passage is marked.
    record = recall.read(db, alice.id, MemoryRef("fact", str(allergy.id))).records[0]
    passages = [p for p in recall_expand.expand(db, alice.id, [record]).passages if p.node.kind == "message"]
    assert passages and all("knee" not in p.text.lower() and "peanuts" in p.text for p in passages)


def test_when_it_cant_be_separated_the_whole_message_is_set_aside_and_that_is_said(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    mixed, job = _exchange(db, conversation, MIXED, "Noted, no peanuts.")
    _complete(db, job)
    allergy = SemanticMemory(user_id=alice.id, kind="constraint", normalized_key="constraint:peanut_allergy",
                             content="The user is allergic to peanuts.", importance=0.9, confidence=1.0,
                             source_message_id=mixed.id)
    db.add(allergy)
    db.commit()
    _ask(db, monkeypatch, conversation)
    assert _run(db, scripted(separable=False))[-1] == "partial"
    db.expire_all()
    assert _fences(db, alice)[mixed.id] is None
    assert db.get(SemanticMemory, allergy.id).status == "active"  # stored on its own: kept
    assert "set aside" in memory_forget.status_note(db, alice.id)
    found = recall.search(db, recall.RecallRequest(user_id=alice.id, query="what can I eat?", purpose="reply",
                                                   now=NOW))
    assert "The user is allergic to peanuts." in [r.content for r in found.records]
    assert memory_forget.forgotten_since(db, alice.id, NOW.replace(year=2000))


def test_the_conversation_and_the_request_stay_searchable_history(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    knee, job = _exchange(db, conversation, "my knee has been sore since the race", "Oh no, rest it.")
    _complete(db, job)
    asked = _ask(db, monkeypatch, conversation)
    _run(db, scripted())
    found = transcript_search.search(db, alice.id, "knee", now=NOW, who="user")
    ids = {r.ref.id: r for r in found.records}
    assert str(knee.id) in ids and str(asked.id) in ids
    assert ids[str(knee.id)].forgotten and ids[str(asked.id)].forgotten
    # The request itself is fenced only on the user's own request words.
    spans = _fences(db, alice)[asked.id]
    assert spans and FORGET[spans[0][0]:spans[0][1]].lower() == "forget what i told you about my knee"


def test_going_back_over_forgotten_history_never_relearns_it(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    knee, job = _exchange(db, conversation, "my knee has been sore since the race", "Oh no, rest it.")
    _complete(db, job)
    _ask(db, monkeypatch, conversation)
    _run(db, scripted())
    # Later they ask what they said; her reply was given the passage and tells them.
    question, later = _exchange(db, conversation, "what did I tell you back then about my knee? also I moved to Paris",
                                "You said your knee had been sore since the race. And Paris, how exciting!")
    reply = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == question.turn_id, ChatMessage.role == "assistant"))
    found = transcript_search.search(db, alice.id, "knee", now=NOW, exclude=(question.id,))
    turn = recall_references.TurnRecall(alice.id, conversation.id, "UTC", NOW, None, found, "", None)
    turn.supply(found.records)
    recall_references.save_receipt(db, turn, assistant_message_id=reply.id, conversation_id=conversation.id,
                                   reply=reply.content)
    db.commit()
    extract = _reads([MemoryCandidate(kind="episode", normalized_key="episode:sore_knee",
                                      content="The user's knee was sore after a race.", importance=0.5, confidence=0.8),
                      MemoryCandidate(kind="fact", normalized_key="fact:lives_in", content="The user moved to Paris.",
                                      importance=0.6, confidence=0.9)])
    seen = {}

    def reads(user, assistant, earlier=(), known=(), **context):
        seen["assistant"] = assistant
        return extract(user, assistant, earlier, known, **context)
    _consolidate(db, monkeypatch, later, reads)
    assert seen["assistant"] == ""  # her retelling of forgotten history isn't something to learn from
    assert db.scalars(select(EpisodicMemory).where(EpisodicMemory.user_id == alice.id)).all() == []
    facts = [f.content for f in db.scalars(select(SemanticMemory).where(SemanticMemory.user_id == alice.id))]
    assert "The user moved to Paris." in facts
    # The user can still ask her to remember it again later: a plain new message isn't fenced.
    _said, again = _exchange(db, conversation, "actually, please remember my knee is weak", "Okay, I'll remember.")
    _consolidate(db, monkeypatch, again, _reads([MemoryCandidate(
        kind="fact", normalized_key="fact:weak_knee", content="The user's knee is weak.", importance=0.5,
        confidence=0.8)]))
    assert db.scalar(select(SemanticMemory).where(SemanticMemory.normalized_key == "fact:weak_knee")) is not None


def test_a_scan_that_fails_part_way_resumes_and_never_claims_done(db, alice, monkeypatch):
    monkeypatch.setattr(memory_forget, "SCAN_BATCHES_PER_RUN", 1)
    monkeypatch.setattr(memory_forget, "SCAN_BATCH", 2)
    conversation = _conversation(db, alice)
    for i in range(5):
        _said, job = _exchange(db, conversation, f"my knee hurts {i}", "Oh no.")
        _complete(db, job)
    _ask(db, monkeypatch, conversation)
    judge = scripted(fail_on={2})
    assert _run(db, judge, limit=1) == ["pending"]  # one batch read, then it yields
    assert _run(db, judge, limit=1) == ["pending"]  # the second batch's call failed: retried later
    row = db.scalar(select(MemoryForget))
    assert row.status == "pending" and row.last_error and row.request["scan"]["next"] == 2
    assert "still being forgotten" in memory_forget.status_note(db, alice.id)
    assert _run(db, judge)[-1] == "done"
    # Failing every time ends as failed, said so, nothing claimed.
    other = _conversation(db, alice)
    _said, job = _exchange(db, other, "my knee again", "Hm.")
    _complete(db, job)
    _ask(db, monkeypatch, other)
    statuses = _run(db, scripted(fail_on=set(range(1, 99))))
    assert statuses[-1] == "failed"
    assert "didn't work" in memory_forget.status_note(db, alice.id)


def test_later_memory_jobs_wait_for_a_forget_in_progress(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _said, job = _exchange(db, conversation, "my knee is sore", "Oh no.")
    _complete(db, job)
    _ask(db, monkeypatch, conversation)
    _said, later = _exchange(db, conversation, "anyway my knee is the worst", "Rest it.")
    assert long_term_memory.next_pending_job_id(db) is None  # it waits: it can't write what's being forgotten
    _run(db, scripted())
    assert long_term_memory.next_pending_job_id(db) == later.id
    # It runs after, and reads the earlier mention only as marked.
    extract = _reads()
    _consolidate(db, monkeypatch, later, extract)
    assert not any("knee is sore" in line for line in extract.seen["earlier"])


def test_nothing_about_it_is_nothing_found(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    _said, job = _exchange(db, conversation, "I like hiking", "Nice!")
    _complete(db, job)
    _ask(db, monkeypatch, conversation)
    assert _run(db, scripted())[-1] == "nothing_found"
    assert uuid.UUID(str(db.scalar(select(MemoryForget.id))))  # still recorded, content-free


def test_the_asking_message_never_rewrites_or_restates_what_it_asks_to_forget(db, alice, monkeypatch):
    conversation = _conversation(db, alice)
    knee, job = _exchange(db, conversation, "my knee has been sore since the race", "Oh no.")
    _complete(db, job)
    old = SemanticMemory(user_id=alice.id, kind="fact", normalized_key="fact:knee", content="The user's knee is sore.",
                         importance=0.5, confidence=0.9, source_message_id=knee.id)
    db.add(old)
    db.commit()
    asked, ask_job = _exchange(db, conversation, FORGET, "Of course.")
    _consolidate(db, monkeypatch, ask_job, _reads(
        [MemoryCandidate(kind="fact", normalized_key="fact:knee", content="The user's knee was sore after a race.",
                         importance=0.5, confidence=0.9),
         MemoryCandidate(kind="preference", normalized_key="preference:knee_private",
                         content="The user prefers that information about their knee not be retained.",
                         importance=0.6, confidence=0.9),
         MemoryCandidate(kind="preference", normalized_key="preference:health_privacy",
                         content="The user prefers that health information not be retained.",
                         importance=0.6, confidence=0.9)],
        [ForgetRequest(about="my knee", user_words="forget what I told you about my knee")]))
    db.expire_all()
    kept = db.get(SemanticMemory, old.id)
    assert kept.content == "The user's knee is sore." and kept.source_message_id == knee.id  # not rewritten
    keys = set(db.scalars(select(SemanticMemory.normalized_key).where(SemanticMemory.user_id == alice.id)))
    assert "preference:knee_private" not in keys and "preference:health_privacy" in keys
    assert _run(db, scripted())[-1] == "done"
    db.expire_all()
    assert db.get(SemanticMemory, old.id) is None
