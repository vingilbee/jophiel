"""The memory check each evaluation of her mind starts from (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md
§7, stage 1 gate): every user message, in a fresh conversation too, brings her the evidence it needs. Standing
constraints can't be crowded out, "my sister" finds their sister without her name, more than one fit stays
ambiguous, and a failed search is said as failed, never as nothing remembered.

The fixture is the 2026-10-29 end-to-end test's stored facts, word for word (docs/reports/memory-e2e-2026-10-30),
plus contrast cases with other names. Real PostgreSQL (``TEST_DATABASE_URL``); no model or embedding calls, so
this is ranking without meaning: what an embedding outage leaves.
"""
from __future__ import annotations

import json
import uuid

import pytest

import app.core.config as config
from app.db.models import MemoryConflict, SemanticMemory
from app.services import chat as chat_service
from app.services import coordinator, mind_turn, recall, recall_references
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)

SAM = [  # kind, importance, content: as the memory worker stored them
    ("constraint", 0.9, "The user is allergic to peanuts."),
    ("fact", 0.7, "The user's dog is named Pretzel. Biscuit was their childhood cat."),
    ("preference", 0.8, "The user prefers that health information they share not be kept in memory."),
    ("preference", 0.6, "Leo can’t stand mushrooms and doesn’t like cilantro."),
    ("fact", 0.7, "The user's sister Nora moved to Porto last month and lives there."),
    ("fact", 0.6, "The user’s pottery class meets Thursdays at 7pm."),
]


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path, database):
    settings = config.get_settings().model_copy(update={"recall_log_path": str(tmp_path / "recall.jsonl")})
    for module in (config, recall, recall_references):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(recall_references, "SessionLocal", database)
    monkeypatch.setattr(recall, "embed", lambda text: None)  # no network: ranking without meaning
    return settings


def _facts(db, user, rows):
    facts = []
    for i, (kind, importance, content) in enumerate(rows):
        fact = SemanticMemory(user_id=user.id, kind=kind, normalized_key=f"{kind}:{i}:{uuid.uuid4().hex[:6]}",
                              content=content, importance=importance, confidence=1.0, status="active")
        db.add(fact)
        facts.append(fact)
    db.commit()
    return facts


def _fresh(db, user):
    """A new conversation with one user message: nothing from an earlier answer to lean on."""
    conversation = chat_service.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
    return conversation


def _check(db, user, text):
    conversation = _fresh(db, user)
    message = chat_service.add_message(db, conversation=conversation, role="user", content=text,
                                       turn_id=uuid.uuid4())
    return recall_references.for_reply(user.id, conversation.id, message.id, text, "UTC")


def _contents(turn, section=None):
    return [r.content for r in turn.result.records if section is None or recall.section_of(r) == section]


@pytest.mark.parametrize("message,expected", [
    ("what's my dog's name?", "Pretzel"),
    ("I'm cooking dinner for me and Leo tonight. anything I should keep out of the food?", "mushrooms"),
    ("talked to my sister in Lisbon yesterday, she says hi", "Nora"),
    ("did I ever have a pet called Biscuit?", "Biscuit"),
    ("when's my pottery class?", "7pm"),
])
def test_a_fresh_conversation_gets_the_memory_its_message_is_about(db, alice, message, expected):
    _facts(db, alice, SAM)
    turn = _check(db, alice, message)
    assert turn.result.status == "matched"
    assert any(expected in c for c in _contents(turn)), _contents(turn)
    # The allergy rides along whatever the topic, in its own section.
    assert _contents(turn, "standing") == ["The user is allergic to peanuts."]
    assert "Always applies" in turn.rendered


def test_a_message_about_nothing_remembered_still_carries_the_standing_constraints(db, alice):
    _facts(db, alice, SAM)
    turn = _check(db, alice, "what's a good snack for a long train ride?")
    assert _contents(turn, "standing") == ["The user is allergic to peanuts."]
    health = _check(db, alice, "what do you know about my health?")
    assert "The user is allergic to peanuts." in _contents(health)
    assert any("health information" in c for c in _contents(health))


def test_standing_constraints_are_never_crowded_out_by_better_matches(db, alice):
    _facts(db, alice, SAM[:1] + [("fact", 0.9, f"The user's garden has tomato bed number {i}.") for i in range(12)])
    turn = _check(db, alice, "how is my garden tomato bed doing?")
    garden = [c for c in _contents(turn) if "garden" in c]
    assert len(garden) == 6  # the limit, all of it used by matches
    assert "The user is allergic to peanuts." in _contents(turn, "standing")
    assert turn.result.coverage["standing"] == 1 and turn.result.coverage.get("more_matches")


def test_more_than_one_fit_for_my_sister_comes_back_ambiguous_never_one_picked(db, alice):
    _facts(db, alice, [("fact", 0.7, "The user's sister Ada is a nurse in Leeds."),
                       ("fact", 0.7, "The user's sister Mina studies music in Osaka.")])
    turn = _check(db, alice, "my sister called me today")
    assert sorted(c.split()[3] for c in _contents(turn)) == ["Ada", "Mina"]
    assert turn.result.coverage["references_ambiguous"] is True and "don't pick one" in turn.rendered
    # One sister named twice is not ambiguous.
    other = _user(db, "solo")
    _facts(db, other, [("fact", 0.7, "The user's brother Tomas lives in Bergen."),
                       ("fact", 0.6, "Tomas, the user's brother, works as a pilot.")])
    single = _check(db, other, "my brother is visiting")
    assert len(_contents(single)) == 2 and "references_ambiguous" not in single.result.coverage


def test_a_name_finds_its_memories_without_other_shared_words(db, alice):
    _facts(db, alice, [("fact", 0.6, "Priya is the user's coworker and hates olives."),
                       ("fact", 0.6, "The user's coworker Dev bikes to work.")])
    turn = _check(db, alice, "having lunch with priya, any ideas")
    assert _contents(turn) == ["Priya is the user's coworker and hates olives."]
    assert "reference" in turn.result.records[0].matched


def test_an_unresolved_disagreement_is_its_own_section(db, alice):
    [nora] = _facts(db, alice, [("fact", 0.7, "The user's sister Nora moved to Porto last month and lives there.")])
    conversation = _fresh(db, alice)
    said = chat_service.add_message(db, conversation=conversation, role="user",
                                    content="talked to my sister in Lisbon yesterday", turn_id=uuid.uuid4())
    db.add(MemoryConflict(user_id=alice.id, fact_id=nora.id, fact_revision=nora.revision, known_content=nora.content,
                          stated="sister is in Lisbon", user_words="my sister in Lisbon", source_message_id=said.id,
                          conversation_id=conversation.id, status="open"))
    db.commit()
    turn = _check(db, alice, "is my sister still around?")
    assert _contents(turn, "unresolved") == [nora.content]
    assert "Unresolved" in turn.rendered and "my sister in Lisbon" in turn.rendered


def test_the_receipt_says_what_each_consumer_saw(db, alice):
    _facts(db, alice, SAM)
    conversation = _fresh(db, alice)
    message = chat_service.add_message(db, conversation=conversation, role="user", content="what's my dog's name?",
                                       turn_id=uuid.uuid4())
    turn = recall_references.for_reply(alice.id, conversation.id, message.id, message.content, "UTC")
    turn.consumed("mind", [str(r.ref) for r in turn.result.records], turn.legacy)
    answer = chat_service.add_message(db, conversation=conversation, role="assistant", content="Pretzel!",
                                      turn_id=uuid.uuid4(), commit=False)
    receipt_id = recall_references.save_receipt(db, turn, assistant_message_id=answer.id,
                                                conversation_id=conversation.id, reply="Pretzel!")
    db.commit()
    from app.db.models import RecallReceipt
    receipt = db.get(RecallReceipt, receipt_id)
    counts = receipt.counts
    assert counts["bundle"] == {"id": str(turn.bundle_id), "version": recall_references.BUNDLE_VERSION}
    assert set(counts["consumers"]) >= {"mind"} and counts["consumers"]["mind"]["refs"]
    assert not {"student_1", "student_2"} & set(counts["consumers"])  # one consumer: her mind
    assert "Pretzel" not in json.dumps(counts)  # refs and hashes, never memory text


# --- through the turn pipeline ---------------------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path, database):
    settings = mind_turn.get_settings()
    for key, value in {"mind_turn_log_path": str(tmp_path / "turns.jsonl"),
                       "desktop_agent_enabled": False}.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(coordinator, "SessionLocal", database)
    monkeypatch.setattr(mind_turn, "SessionLocal", database)
    return settings


def _turn(db, user, text):
    conversation = _fresh(db, user)
    message = chat_service.add_message(db, conversation=conversation, role="user", content=text, turn_id=uuid.uuid4())
    return mind_turn.prepare_message(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                     message=text, zone="UTC", turn_id=uuid.uuid4())


def test_her_evaluation_starts_from_the_memory_check_with_no_stage_between(db, alice, pipeline):
    _facts(db, alice, SAM)
    ev = _turn(db, alice, "I'm cooking dinner for me and Leo tonight. anything I should keep out of the food?")
    assert ev.searched and ev.memory_status in ("matched", "partial")
    prompt_memory = ev.recall_rendered
    assert "allergic to peanuts" in prompt_memory and "mushrooms" in prompt_memory
    assert {r["ref"] for r in ev.memory_refs} <= set(ev.capture.sources)  # what she read is her attempt's sources
    assert ev.recall_turn.consumers["mind"]["refs"] and ev.timings["memory_ms"] >= 0


def test_a_failed_search_says_so_and_never_reads_as_nothing_remembered(db, alice, pipeline, monkeypatch):
    _facts(db, alice, SAM)

    def broken(*a, **k):
        raise RuntimeError("recall is down")

    monkeypatch.setattr(recall, "search", broken)
    ev = _turn(db, alice, "what's my dog's name?")
    assert ev.memory_status == "failed" and not ev.searched
    assert "search FAILED" in ev.boundary() and "found nothing" not in ev.boundary()
    assert ev.recall_turn.result.coverage["search"] == "failed"  # what the receipt will record
    assert ev.event()["degraded"] == ["recall_unavailable"]
