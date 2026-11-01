"""Live teacher decision and training capture, without network or database access."""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.ai.Cognitive.simple import events as E
from app.ai.state import student_two as S
from app.services import student_two as svc

TEACHER_ASK = svc._ask_teacher


@pytest.fixture
def teacher(monkeypatch, tmp_path):
    settings = svc.get_settings()
    monkeypatch.setattr(settings, "cognition_decision_provider", "teacher")
    monkeypatch.setattr(settings, "cognition_teacher_data_path", str(tmp_path / "live_teacher.jsonl"))
    monkeypatch.setattr(settings, "cognition_student_log_path", str(tmp_path / "summary.jsonl"))
    return settings


def live(**changes):
    return S.Live(**(dict(trigger="user_message", domain="chat", now=datetime.now(timezone.utc),
                         new_message="hello", lines=()) | changes))


def answer():
    return dict(S.DEFAULTS, intention="greet them", communication="respond", what_to_communicate="hello",
                move="react_briefly", about=["M"], explanation="They greeted her.",
                dials=dict(length="a_little", warmth="somewhat", playfulness="none", directness="somewhat"))


def drain():
    svc._capture_writer.submit(lambda: None).result(timeout=5)


def test_teacher_uses_one_structured_call_and_captures_the_training_pair(teacher, monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer())))])

    monkeypatch.setattr(svc, "_teacher_client", lambda *args: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(svc, "_ask_teacher", TEACHER_ASK)
    monkeypatch.setattr(svc, "manifest", lambda: pytest.fail("teacher must not need local Qwen files"))
    outcome = svc.decide(live())
    assert outcome.ok, outcome.problems
    assert svc.rendering() == "memory-v1"
    assert len(calls) == 1
    request = calls[0]
    assert request["reasoning_effort"] == "low"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "MEMORY" in request["messages"][1]["content"]
    assert "CHARACTER PAGE" in request["messages"][0]["content"]
    user, conversation, message = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    svc.log(outcome, user_id=user, conversation_id=conversation, trigger=f"message:{message}")
    drain()
    with open(teacher.cognition_teacher_data_path) as f:
        record = json.loads(f.readline())
        assert f.read() == ""
    assert record["training_eligible"] is True
    assert record["decision_id"] == outcome.decision_id
    assert record["trigger"] == f"message:{message}"
    assert record["snapshot"]["new_message"] == "hello"
    assert record["snapshot_version"] == E.SNAPSHOT_VERSION
    assert record["prompt"] == request["messages"][1]["content"]
    assert record["system"] == request["messages"][0]["content"]
    assert json.loads(record["raw_output"])["explanation"] == "They greeted her."
    assert record["decision"]["intention"] == "greet them"


@pytest.mark.parametrize("output", ["not JSON", json.dumps(dict(answer(), target="G999", work="pause"))])
def test_invalid_teacher_samples_are_saved_but_excluded(teacher, output):
    outcome = svc.decide(live(), ask=lambda model, text: output)
    assert outcome.status == "invalid"
    svc.log(outcome, user_id=uuid.uuid4(), conversation_id=None, trigger="message:test")
    drain()
    with open(teacher.cognition_teacher_data_path) as f:
        record = json.loads(f.read())
    assert not record["training_eligible"]
    assert record["raw_output"] == output
    assert record["problems"]


def test_teacher_failure_is_recorded_without_falling_back_to_qwen(teacher):
    outcome = svc.decide(live())  # conftest refuses all model calls
    assert outcome.status == "unavailable"
    assert "ConnectionRefusedError" in outcome.error
    svc.log(outcome, user_id=uuid.uuid4(), conversation_id=None, trigger="message:test")
    drain()
    with open(teacher.cognition_teacher_data_path) as f:
        record = json.loads(f.read())
    assert not record["training_eligible"]
    assert record["error"]
    assert record["prompt"]


def test_background_result_uses_teacher_and_appends_instead_of_overwriting(teacher):
    for _ in range(2):
        outcome = svc.decide(live(trigger="work_outcome", new_message=""), ask=lambda model, text: json.dumps(
            dict(S.DEFAULTS, intention="nothing to add", communication="silent")))
        assert outcome.ok, outcome.problems
        svc.log(outcome, user_id=uuid.uuid4(), conversation_id=None, trigger="work_outcome")
    drain()
    with open(teacher.cognition_teacher_data_path) as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 2
    assert records[0]["decision_id"] != records[1]["decision_id"]
    assert all(r["snapshot"]["trigger"] == "work_outcome" for r in records)
