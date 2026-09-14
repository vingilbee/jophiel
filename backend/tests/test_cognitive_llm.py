import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.ai.Cognitive.system import llm
from app.api.routes import cognitive
from app.schemas.cognitive import CognitiveInspectRequest


def sample_state():
    return {
        "perception": {
            "observations": ["Greeting followed by a request"],
            "references": ["protein"],
            "explicit_constraints": ["One sentence"],
        },
        "interpretation": {
            "primary_intent": "request", "secondary_intents": ["greeting"],
            "resolved_goal": "Understand protein", "missing_information": [],
        },
        "action": {
            "kind": "respond", "objective": "Explain protein in one sentence",
            "capability": None, "clarification_question": None,
        },
    }


def mock_provider(monkeypatch, state=None, status="completed"):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.parse.return_value = SimpleNamespace(
        status=status, output_parsed=state,
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(llm, "OpenAI", factory)
    monkeypatch.setattr(llm, "get_settings", lambda: SimpleNamespace(
        openai_api_key="test", cognitive_model="test-model", chat_model="fallback",
    ))
    return factory, client


def test_single_call_preserves_history_and_runtime_context(monkeypatch):
    factory, client = mock_provider(monkeypatch, llm.CognitiveState(**sample_state()))
    history = ["assistant: Should I explain protein?"]
    state = llm.predict_state("Do it", history, image_count=1)
    factory.assert_called_once_with(api_key="test", max_retries=0, timeout=30.0)
    client.responses.parse.assert_called_once()
    args = client.responses.parse.call_args.kwargs
    assert args["text_format"] is llm.CognitiveState
    assert args["store"] is False
    assert args["model"] == "test-model"
    payload = json.loads(args["input"])
    assert payload["message"] == "Do it"
    assert payload["recent_messages"] == history
    assert payload["runtime_context"] == {"image_count": 1, "asset_count": 0}
    assert state.action.kind == "respond"


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_missing_or_incomplete_output_is_not_retried(monkeypatch, status):
    _, client = mock_provider(monkeypatch, status=status)
    with pytest.raises(ValueError, match="complete structured state"):
        llm.predict_state("Hello", [])
    assert client.responses.parse.call_count == 1


@pytest.mark.parametrize("capabilities, valid", [([], False), (["nutrition"], True)])
def test_delegation_requires_available_capability(monkeypatch, capabilities, valid):
    data = sample_state()
    data["action"].update(kind="delegate", capability="nutrition")
    mock_provider(monkeypatch, llm.CognitiveState(**data))
    if valid:
        assert llm.predict_state("Analyze", [], available_capabilities=capabilities).action.capability == "nutrition"
    else:
        with pytest.raises(ValueError, match="unavailable capability"):
            llm.predict_state("Analyze", [], available_capabilities=capabilities)


@pytest.mark.parametrize("changes", [
    {"kind": "clarify"},
    {"kind": "delegate"},
    {"capability": "invented"},
    {"clarification_question": "Why?"},
    {"kind": "execute"},
    {"objective": " "},
])
def test_invalid_action_is_rejected(changes):
    data = sample_state()
    data["action"].update(changes)
    with pytest.raises(ValidationError):
        llm.CognitiveState(**data)


def test_llm_inspector_returns_state_and_metrics(monkeypatch):
    predict = MagicMock(return_value=llm.CognitiveState(**sample_state()))
    monkeypatch.setattr(cognitive, "predict_state", predict)
    result = cognitive.inspect_cognition(
        CognitiveInspectRequest(mode="llm", message="Explain protein", image_count=2),
        _current_user=object(),
    )
    assert result.mode == "llm"
    assert result.image_count == 2
    assert result.metrics.online_model_calls == 1
    assert result.state.action.kind == "respond"


def test_inspector_failure_is_explicit_without_provider_details(monkeypatch):
    monkeypatch.setattr(cognitive, "predict_state", MagicMock(side_effect=ValueError("private details")))
    with pytest.raises(HTTPException) as exc:
        cognitive.inspect_cognition(CognitiveInspectRequest(mode="llm", message="Hi"), _current_user=object())
    assert exc.value.status_code == 502
    assert "private details" not in exc.value.detail
