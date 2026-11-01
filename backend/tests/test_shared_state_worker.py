"""The fast path through the real chat worker (``workers/chat._generate``): the initial memory check against the
test database before cognition, the route on the turn's stream, the boundary in her context, streaming and the stored reply unchanged, one trace
line per turn. Real PostgreSQL (``TEST_DATABASE_URL``); the model and Student 1 are stubbed."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import cognitive_live, coordinator, recall_references, shared_state, student_two
from app.workers import chat as chat_worker
from db_support import database, db_env  # noqa: F401  (fixtures)
from chat_turn_support import REPLY, live_turn  # noqa: F401  (fixture)


@pytest.fixture
def fast(db_env, monkeypatch, tmp_path):
    settings = SimpleNamespace(
        shared_state_log_path=str(tmp_path / "shared.jsonl"), cognitive_live_student_dir=None,
        cognition_student_log_path=str(tmp_path / "student_two.jsonl"))
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    monkeypatch.setattr(shared_state, "SessionLocal", db_env.db)
    monkeypatch.setattr(coordinator, "SessionLocal", db_env.db)  # supersession reads the test schema only
    shared_state._personality.cache_clear()
    monkeypatch.setattr(recall_references, "SessionLocal", db_env.db)  # the real initial check, on the test schema
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt",
                        lambda *a, **k: pytest.fail("the old memory block is never built on this path"))
    monkeypatch.setattr(cognitive_live, "after_reply", lambda *a, **k: None)
    readings = {"ok": True}

    def prepare(**kwargs):
        assert kwargs["memory_searched"] is True
        if not readings["ok"]:
            return None  # what cognitive_live.prepare returns when Student 1 fails
        return cognitive_live.LivePrep(reading="- Their tone seems: friendly.", interpretation=dict(
            acts={"asking": 0.9}, tone={"friendly": 0.9}, request={"talk_only": 0.9},
            understand={"acts": {"asking": True}, "tone": "friendly", "request": "talk_only"}))

    monkeypatch.setattr(cognitive_live, "prepare", prepare)
    # Student 2, scripted: answer the message, change nothing.
    answer = {"intention": "chat back", "work": "no_change", "communication": "respond",
              "what_to_communicate": "doing well", "move": "react_briefly",
              "dials": {"length": "a_little", "warmth": "somewhat", "playfulness": "none", "directness": "somewhat"}}
    monkeypatch.setattr(student_two, "get_settings", lambda: settings)
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "test-student", "system": "sys", "rendering": None})
    real_decide = student_two.decide
    monkeypatch.setattr(student_two, "decide", lambda live, **k: real_decide(live, **k, ask=lambda m, t: json.dumps(answer)))
    return SimpleNamespace(settings=settings, readings=readings, log=tmp_path / "shared.jsonl")


def test_a_pipeline_turn_streams_as_before_with_its_route_and_trace(fast, live_turn):
    turn = live_turn("hey, how's it going?")
    types = [e["type"] for e in turn["events"]]
    assert types == ["start", "route", "token", "token", "token", "done"]
    route = turn["events"][1]
    # Student 2 decided neither recall nor thinking is needed: a confirmed fast path.
    assert route["route"] == "fast_path" and route["eligibility"] == "confirmed" and route["degraded"] == []
    assert turn["assistant"] == ("".join(REPLY), "completed") and turn["job"] == ("completed", None)
    _, context = turn["agent_input"]
    assert context.cognitive_reading == "- Their tone seems: friendly."
    # Nothing is remembered yet: the check ran, found nothing, and says so without claiming it never happened.
    assert "found nothing that matches" in context.turn_boundary and context.cached_memory is None
    assert context.recall.startswith("Memory search ran")
    assert "What to get across: doing well" in context.decision
    [line] = [json.loads(x) for x in fast.log.read_text().splitlines()]
    assert line["delivery"]["outcome"] == "completed" and line["delivery"]["reply_chars"] == len("".join(REPLY))
    assert line["cognition"]["status"] == "available" and line["cognition"]["acts"] == ["asking"]
    assert line["calls"]["fresh_recall_before_reply"] == 1 and line["dependencies"]["conversation_revision"] == "1"
    assert line["recall"] == {"ran": True, "status": "no_match", "records": 0}
    assert "how's it going" not in fast.log.read_text()


def test_a_failed_reading_is_a_visibly_degraded_turn_that_still_answers(fast, live_turn):
    fast.readings["ok"] = False
    turn = live_turn("hey")
    route = turn["events"][1]
    assert route["type"] == "route" and route["outcome"] == "degraded" and route["degraded"] == ["cognition_unavailable"]
    assert turn["assistant"] == ("".join(REPLY), "completed")
    _, context = turn["agent_input"]
    assert "didn't work this turn" in context.turn_boundary and context.cognitive_reading is None
    line = json.loads(fast.log.read_text().splitlines()[-1])
    assert line["route"]["outcome"] == "degraded" and line["cognition"]["status"] == "unavailable"
