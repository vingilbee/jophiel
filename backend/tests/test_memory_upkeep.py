"""Upkeep (services/memory_upkeep.py; docs/COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5): duplicate facts merge without
losing evidence, wording, uncertainty or dependencies, and nothing that only looks alike is ever merged. Real
PostgreSQL (``TEST_DATABASE_URL``); the judge is scripted."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    MemoryConflict, MemoryDependency, MemoryEntity, MemoryEvidence, MemoryForget, MemoryUpkeepPair, RecallReceipt,
    RecallReference, SemanticMemory,
)
from app.services import memory_manager, memory_upkeep
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)
from tests.test_memory_corrections import _conversation, _exchange
from tests.test_memory_forget import on  # noqa: F401  (autouse settings fixture)

VEC = [1.0, 0.0, 0.0]
NEAR = [0.99, 0.05, 0.0]


def _fact(db, user, key, content, *, created=None, source=None, **values):
    fact = SemanticMemory(user_id=user.id, kind=values.pop("kind", "preference"), normalized_key=key, content=content,
                          importance=values.pop("importance", 0.5), confidence=values.pop("confidence", 0.9),
                          source_message_id=source, embedding=values.pop("embedding", VEC), **values)
    if created:
        fact.created_at = created
    db.add(fact)
    db.commit()
    return fact


def _says(same: bool):
    def judge(pairs):
        judge.shown = pairs
        return memory_upkeep.UpkeepJudgement(pairs=[memory_upkeep.PairVerdict(id=p["id"], same=same) for p in pairs])
    judge.shown = []
    return judge


def _two_copies(db, user):
    conversation = _conversation(db, user)
    first, _ = _exchange(db, conversation, "I can't stand cilantro")
    second, _ = _exchange(db, conversation, "seriously, no cilantro ever")
    old = _fact(db, user, "preference:no_cilantro", "The user dislikes cilantro.", source=first.id, confidence=0.95,
                created=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3), evidence_count=1)
    copy = _fact(db, user, "preference:cilantro_dislike", "The user doesn't like cilantro.", source=second.id,
                 confidence=0.7, embedding=NEAR, importance=0.8, evidence_count=1)
    return conversation, first, second, old, copy


def test_a_duplicate_merges_into_the_older_fact_keeping_everything(db, alice):
    conversation, first, second, old, copy = _two_copies(db, alice)
    item = memory_manager.add_item(db, alice.id, kind="question", text_="Ask which herbs they do like",
                                   owner="columbina", epistemic="inferred")
    db.add(MemoryDependency(user_id=alice.id, item_id=item.id, fact_id=copy.id, role="required", state="valid",
                            origin="derived", checked_revision=copy.revision))
    db.add(MemoryEvidence(user_id=alice.id, fact_id=copy.id, role="origin", message_id=second.id))
    db.add(MemoryEvidence(user_id=alice.id, fact_id=old.id, role="origin", message_id=first.id))
    receipt = RecallReceipt(user_id=alice.id, assistant_message_id=_exchange(db, conversation, "hi")[0].id,
                            conversation_id=conversation.id, schema_version="t", purpose="reply", status="matched")
    db.add(receipt)
    db.flush()
    db.add(RecallReference(receipt_id=receipt.id, user_id=alice.id, relation="supplied", fact_id=copy.id,
                           fingerprint="x", method="structured"))
    db.commit()

    result = memory_upkeep.run_user(db, alice.id, judge=_says(True))
    assert result["merged"] == 1
    db.expire_all()
    assert db.get(SemanticMemory, copy.id) is None
    kept = db.get(SemanticMemory, old.id)
    assert kept.content == "The user dislikes cilantro." and kept.revision == old.revision  # meaning unchanged
    assert kept.confidence == 0.7 and kept.importance == 0.8  # the less certain copy's doubt is kept
    assert kept.evidence_count == 2  # two separate statements, counted once each
    merged = [h for h in kept.metadata_["history"] if h["reason"] == "merged"]
    assert merged and merged[0]["content"] == "The user doesn't like cilantro."
    dep = db.scalar(select(MemoryDependency).where(MemoryDependency.item_id == item.id))
    assert dep.fact_id == old.id and dep.state == "valid" and dep.checked_revision == kept.revision
    assert db.scalar(select(RecallReference.fact_id).where(RecallReference.receipt_id == receipt.id)) == old.id
    assert {e.message_id for e in db.scalars(select(MemoryEvidence).where(MemoryEvidence.fact_id == old.id))} == \
        {first.id, second.id}


def test_two_copies_of_one_statement_are_not_more_evidence(db, alice):
    conversation = _conversation(db, alice)
    said, _ = _exchange(db, conversation, "I dislike cilantro")
    old = _fact(db, alice, "preference:a", "The user dislikes cilantro.", source=said.id, evidence_count=1,
                created=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    _fact(db, alice, "preference:b", "The user dislikes cilantro.", source=said.id, evidence_count=1, embedding=None)
    assert memory_upkeep.run_user(db, alice.id, judge=_says(True))["merged"] == 1
    db.expire_all()
    assert db.get(SemanticMemory, old.id).evidence_count == 1


def test_only_alike_is_never_the_same(db, alice):
    sister = MemoryEntity(user_id=alice.id, kind="person", name="Ana", relation="sister")
    coworker = MemoryEntity(user_id=alice.id, kind="person", name="Ana", relation="coworker")
    db.add_all([sister, coworker])
    db.commit()
    # Same name, different people.
    _fact(db, alice, "fact:ana1", "Ana lives in Porto.", kind="fact", entity_id=sister.id, attribute="lives_in",
          value="Porto")
    _fact(db, alice, "fact:ana2", "Ana lives in Porto.", kind="fact", entity_id=coworker.id, attribute="lives_in",
          value="Porto")
    # Different respect, different time, different detail.
    _fact(db, alice, "fact:q1", "Ana is vegetarian.", kind="fact", entity_id=sister.id, attribute="diet",
          value="vegetarian")
    _fact(db, alice, "fact:q2", "Ana is vegetarian at home.", kind="fact", entity_id=sister.id, attribute="diet",
          value="vegetarian", qualifier="at home")
    _fact(db, alice, "fact:t1", "Ana lived in Lisbon.", kind="fact", entity_id=sister.id, attribute="lived_in",
          value="Lisbon", historical=True)
    _fact(db, alice, "fact:t2", "Ana lives in Lisbon.", kind="fact", entity_id=sister.id, attribute="lived_in",
          value="Lisbon")
    # A claim and an older note: identity not shown, so not the same.
    _fact(db, alice, "fact:n1", "Ana has a dog.", kind="fact", entity_id=sister.id, attribute="pet", value="dog")
    _fact(db, alice, "fact:n2", "Ana has a dog.", kind="fact", entity_id=sister.id)
    assert memory_upkeep.candidate_pairs(db, alice.id) == []
    assert memory_upkeep.run_user(db, alice.id, judge=_says(True)) == {"pairs": 0}


def test_held_conflicted_or_forgetting_facts_are_left_alone(db, alice):
    conversation, first, second, old, copy = _two_copies(db, alice)
    item = memory_manager.add_item(db, alice.id, kind="question", text_="Ask about herbs", owner="columbina",
                                   epistemic="inferred")
    dep = MemoryDependency(user_id=alice.id, item_id=item.id, fact_id=copy.id, role="required", state="pending",
                           origin="derived", checked_revision=copy.revision)
    db.add(dep)
    db.commit()
    assert memory_upkeep.candidate_pairs(db, alice.id) == []  # repointing can never lift a hold
    dep.state = "valid"
    dep.checked_revision = copy.revision + 5  # valid, but checked against another version
    db.commit()
    assert memory_upkeep.candidate_pairs(db, alice.id) == []
    db.delete(dep)
    db.add(MemoryConflict(user_id=alice.id, fact_id=old.id, fact_revision=old.revision, known_content=old.content,
                          stated="I love cilantro now", user_words="I love cilantro now", source_message_id=second.id,
                          conversation_id=conversation.id, status="open"))
    db.commit()
    assert memory_upkeep.candidate_pairs(db, alice.id) == []
    db.query(MemoryConflict).delete()
    db.add(MemoryForget(user_id=alice.id, source_message_id=second.id, conversation_id=conversation.id,
                        request={"user_words": ["forget the cilantro thing"]}))
    db.commit()
    assert memory_upkeep.candidate_pairs(db, alice.id) == []
    db.query(MemoryForget).delete()
    db.commit()
    assert len(memory_upkeep.candidate_pairs(db, alice.id)) == 1


def test_a_pair_judged_different_waits_until_one_of_them_changes(db, alice):
    _conversation_, _first, _second, old, copy = _two_copies(db, alice)
    judge = _says(False)
    assert memory_upkeep.run_user(db, alice.id, judge=judge)["different"] == 1
    assert db.scalar(select(MemoryUpkeepPair.verdict)) == "different"
    assert memory_upkeep.candidate_pairs(db, alice.id) == []  # not judged again
    db.expire_all()
    copy = db.get(SemanticMemory, copy.id)
    copy.content, copy.revision = "The user really doesn't like cilantro.", copy.revision + 1
    db.commit()
    assert len(memory_upkeep.candidate_pairs(db, alice.id)) == 1  # it changed: looked at again


def test_a_fact_that_changed_after_it_was_judged_is_not_merged(db, alice):
    _conversation_, _first, _second, old, copy = _two_copies(db, alice)
    [(a, b)] = memory_upkeep.candidate_pairs(db, alice.id)
    prints = (memory_upkeep.fingerprint(a), memory_upkeep.fingerprint(b))
    db.rollback()
    fact = db.get(SemanticMemory, copy.id)
    fact.content, fact.revision = "The user now likes cilantro.", fact.revision + 1
    db.commit()
    assert memory_upkeep.merge(db, alice.id, a.id, b.id, *prints) == "changed_since_judged"
    db.expire_all()
    assert db.get(SemanticMemory, copy.id) is not None


def test_run_once_rotates_users_and_never_breaks_the_worker(db, alice, monkeypatch):
    _two_copies(db, alice)
    bob = _user(db, "bob")
    _two_copies(db, bob)
    calls = []
    monkeypatch.setattr(memory_upkeep, "_visited", {})
    monkeypatch.setattr(memory_upkeep, "_last_run", None)

    def judge(pairs):
        calls.append(len(pairs))
        raise RuntimeError("model down")
    assert memory_upkeep.run_once(judge, force=True) is False
    assert memory_upkeep.run_once(judge, force=True) is False
    assert calls == [1, 1] and len(memory_upkeep._visited) == 2
    assert uuid.UUID(str(alice.id))
