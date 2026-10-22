"""The fast path through the real chat worker (``workers/chat._generate``): no search before the reply, the
route on the turn's stream, the boundary in her context, streaming and the stored reply unchanged, one trace
line per turn. Real PostgreSQL (``TEST_DATABASE_URL``); the model and Student 1 are stubbed."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import cognitive_live, coordinator, recall_references, shared_state
from app.workers import chat as chat_worker
from cognitive_shadow_support import database, shadow_env  # noqa: F401  (fixtures)
from test_cognitive_shadow_chat_isolation import REPLY, live_turn  # noqa: F401  (fixture)


@pytest.fixture
def fast(shadow_env, monkeypatch, tmp_path):
    shadow_env.configure(cognitive_live_shadow_enabled=False)
    settings = SimpleNamespace(
        shared_state_pipeline_enabled=True, shared_state_log_path=str(tmp_path / "shared.jsonl"),
        cognitive_live_student="on", cognitive_live_character=False, cognitive_live_student_dir=None,
        recall_enabled=False, mind_enabled=False, mind_inner_state_enabled=False, memory_manager_enabled=False)
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    monkeypatch.setattr(shared_state, "SessionLocal", shadow_env.db)
    monkeypatch.setattr(coordinator, "SessionLocal", shadow_env.db)  # supersession reads the test schema only
    shared_state._personality.cache_clear()
    monkeypatch.setattr(recall_references, "enabled", lambda: False)
    monkeypatch.setattr(recall_references, "for_reply", lambda *a, **k: pytest.fail("searched before the reply"))
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt",
                        lambda *a, **k: pytest.fail("searched before the reply"))
    monkeypatch.setattr(cognitive_live, "enabled", lambda: True)
    monkeypatch.setattr(cognitive_live, "after_reply", lambda *a, **k: None)
    readings = {"ok": True}

    def prepare(**kwargs):
        assert kwargs["memory_searched"] is False
        if not readings["ok"]:
            return None  # what cognitive_live.prepare returns when Student 1 fails
        return cognitive_live.LivePrep(arm="reading", reading="- Their tone seems: friendly.", interpretation=dict(
            acts={"asking": 0.9}, tone={"friendly": 0.9}, request={"talk_only": 0.9},
            understand={"acts": {"asking": True}, "tone": "friendly", "request": "talk_only"}))

    monkeypatch.setattr(cognitive_live, "prepare", prepare)
    return SimpleNamespace(settings=settings, readings=readings, log=tmp_path / "shared.jsonl")


def test_a_pipeline_turn_streams_as_before_with_its_route_and_trace(fast, live_turn):
    turn = live_turn("hey, how's it going?")
    types = [e["type"] for e in turn["events"]]
    assert types == ["start", "route", "token", "token", "token", "done"]
    route = turn["events"][1]
    # Today nobody assesses recall/thinking needs: answered, but not claimed fast-path eligible.
    assert route["route"] == "unassessed" and route["eligibility"] == "unassessed" and route["degraded"] == []
    assert turn["assistant"] == ("".join(REPLY), "completed") and turn["job"] == ("completed", None)
    _, context = turn["agent_input"]
    assert context.cognitive_reading == "- Their tone seems: friendly."
    assert "recent conversation only" in context.turn_boundary and context.recall is None
    [line] = [json.loads(x) for x in fast.log.read_text().splitlines()]
    assert line["delivery"]["outcome"] == "completed" and line["delivery"]["reply_chars"] == len("".join(REPLY))
    assert line["cognition"]["status"] == "available" and line["cognition"]["acts"] == ["asking"]
    assert line["calls"]["fresh_recall_before_reply"] == 0 and line["dependencies"]["conversation_revision"] == "1"
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


def test_with_the_flag_off_the_turn_is_unchanged(fast, live_turn, monkeypatch):
    fast.settings.shared_state_pipeline_enabled = False
    monkeypatch.setattr(chat_worker.memory_service, "retrieve_for_prompt", lambda *a, **k: None)
    monkeypatch.setattr(cognitive_live, "enabled", lambda: False)
    turn = live_turn("hey")
    assert [e["type"] for e in turn["events"]] == ["start", "token", "token", "token", "done"]
    assert turn["agent_input"][1].turn_boundary is None and not fast.log.exists()
