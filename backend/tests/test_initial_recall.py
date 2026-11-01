"""The initial memory check before cognition (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md §7, stage 1 gate):
every user message, in a fresh conversation too, gets the evidence it needs before anyone decides anything.
Standing constraints can't be crowded out, "my sister" finds their sister without her name, more than one fit
stays ambiguous, and a failed or invalid decision can't take the evidence away.

The fixture is the 2026-10-29 end-to-end test's stored facts, word for word (docs/reports/memory-e2e-2026-10-30),
plus contrast cases with other names. Real PostgreSQL (``TEST_DATABASE_URL``); no model or embedding calls, so
this is ranking without meaning: what an embedding outage leaves.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.core.config as config
from app.ai.state import student_two as S
from app.db.models import MemoryConflict, SemanticMemory
from app.services import chat as chat_service
from app.services import cognitive_live, coordinator, recall, recall_references, shared_state, student_two
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
    turn.consumed("student_1", [r.ref for r in turn.result.records if r.ref.kind == "fact"], turn.legacy)
    turn.consumed("student_2", [], None, note=S.SNAPSHOT_MEMORY_NOTE)
    answer = chat_service.add_message(db, conversation=conversation, role="assistant", content="Pretzel!",
                                      turn_id=uuid.uuid4(), commit=False)
    receipt_id = recall_references.save_receipt(db, turn, assistant_message_id=answer.id,
                                                conversation_id=conversation.id, reply="Pretzel!")
    db.commit()
    from app.db.models import RecallReceipt
    receipt = db.get(RecallReceipt, receipt_id)
    counts = receipt.counts
    assert counts["bundle"] == {"id": str(turn.bundle_id), "version": recall_references.BUNDLE_VERSION}
    mouth, student_1 = counts["consumers"]["mouth"]["refs"], counts["consumers"]["student_1"]["refs"]
    assert set(student_1) <= set(mouth) and student_1
    assert counts["consumers"]["student_2"]["refs"] == [] and "no memory input" in counts["consumers"]["student_2"]["note"]
    assert "Pretzel" not in json.dumps(counts)  # refs and hashes, never memory text


# --- through the turn pipeline ---------------------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path, database):
    settings = SimpleNamespace(shared_state_log_path=str(tmp_path / "shared.jsonl"), cognitive_live_student_dir=None,
                               cognition_student_log_path=str(tmp_path / "student_two.jsonl"),
                               recall_tool_calls_per_reply=3)
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    monkeypatch.setattr(shared_state, "SessionLocal", database)
    monkeypatch.setattr(coordinator, "SessionLocal", database)
    shared_state._personality.cache_clear()
    seen = {}

    def read(**kwargs):
        seen["student_1"] = kwargs
        return cognitive_live.LivePrep(interpretation=dict(
            acts={"asking": 0.9}, tone={"friendly": 0.9}, request={"talk_only": 0.9},
            understand={"acts": {"asking": True}, "tone": "friendly", "request": "talk_only"}))

    monkeypatch.setattr(cognitive_live, "prepare", read)
    monkeypatch.setattr(student_two, "gather", lambda db, **k: S.Live(
        "user_message", "chat", datetime.now(timezone.utc), (), k["message"]))
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "test-student", "system": "sys", "rendering": None})
    real_decide = student_two.decide
    answer = {"text": "not json at all"}
    monkeypatch.setattr(student_two, "decide", lambda live, **k: real_decide(live, **k, ask=lambda m, t: answer["text"]))
    monkeypatch.setattr(student_two, "log", lambda *a, **k: None)
    return SimpleNamespace(seen=seen, answer=answer)


def _turn(db, user, text):
    conversation = _fresh(db, user)
    message = chat_service.add_message(db, conversation=conversation, role="user", content=text, turn_id=uuid.uuid4())
    return shared_state.prepare(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                history=[{"role": "user", "content": text}], message=text, zone="UTC",
                                turn_id=uuid.uuid4(), reply_message_id=uuid.uuid4())


def test_an_invalid_student_two_answer_cannot_take_the_evidence_away(db, alice, pipeline):
    _facts(db, alice, SAM)
    turn = _turn(db, alice, "I'm cooking dinner for me and Leo tonight. anything I should keep out of the food?")
    assert turn.student.status == "invalid" and "decision_unavailable" in turn.decision.degraded
    assert turn.immediate_origin == "operational_fallback" and turn.searched
    prompt_memory = turn.recall_rendered
    assert "allergic to peanuts" in prompt_memory and "mushrooms" in prompt_memory
    # Student 1 read the same check, searched (not "only what was in hand").
    assert pipeline.seen["student_1"]["memory_searched"] is True
    assert "allergic to peanuts" in pipeline.seen["student_1"]["long_term_memory"]
    assert turn.timings["initial_recall_ms"] >= 0


def test_a_failed_search_says_so_and_never_reads_as_nothing_remembered(db, alice, pipeline, monkeypatch):
    _facts(db, alice, SAM)

    def broken(*a, **k):
        raise RuntimeError("recall is down")

    monkeypatch.setattr(recall, "search", broken)
    turn = _turn(db, alice, "what's my dog's name?")
    assert turn.view.memory.recall_status == "failed" and not turn.searched
    assert "search FAILED" in turn.boundary and "found nothing" not in turn.boundary
    assert pipeline.seen["student_1"]["memory_searched"] is False
    assert turn.recall_turn.result.coverage["search"] == "failed"  # what the receipt will record
