"""Protocol and trust-boundary tests for optional model semantic grounding."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai.Cognitive.system.v0.semantic_parser_provider import OpenAISemanticCandidateModel
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import (
    ModelSemanticGrounder, UnavailableSemanticGrounder,
)
from app.ai.Cognitive.system.v0.interpretation_v0.semantic_grounding import SemanticGrounder
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticModelOutput
from app.ai.Cognitive.system.v0.interpretation_v0.fixture_validator import DEFAULT_FIXTURE, _runtime_case
from app.services.cognitive_shadow import cognitive_pipeline


def _request(case_id: str):
    document = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = next(item for item in document["cases"] if item["case_id"] == case_id)
    return _runtime_case(case)[1]


class _CapturingModel:
    version = "fake-semantic-parser-v1"

    def __init__(self, *, foreign_ref: bool = False) -> None:
        self.payload = None
        self.foreign_ref = foreign_ref

    def generate(self, payload):
        self.payload = payload
        evidence_ref = "foreign-evidence" if self.foreign_ref else payload.evidence_regions[0].evidence_ref
        return SemanticModelOutput.model_validate({
            "frames": [{
                "predicate": "information_request",
                "polarity": "positive",
                "arguments": [
                    {"name": "topic_ref", "value": None},
                    {"name": "question_form", "value": "wh"},
                ],
                "evidence_refs": [evidence_ref],
            }]
        })


class _StringNullModel(_CapturingModel):
    def generate(self, payload):
        output = super().generate(payload)
        raw = output.model_dump(mode="python")
        raw["frames"][0]["arguments"][0]["value"] = "null"
        return SemanticModelOutput.model_validate(raw)


def test_model_payload_excludes_runtime_memory_relationship_bias_and_goal_state() -> None:
    request = _request("runtime-capability-memory-conflict")
    model = _CapturingModel()

    envelope = InterpretationEngine(grounder=ModelSemanticGrounder(model)).interpret(request)

    assert model.payload is not None
    assert set(model.payload.model_dump()) == {
        "protocol_version", "focus_message_ref", "messages", "evidence_regions", "predicates",
    }
    serialized = model.payload.model_dump_json()
    for forbidden in (
        request.biases.profile_id,
        request.biases.source_core_memory_ref,
        request.runtime.snapshot_id,
        *(item.memory_id for item in request.memories),
    ):
        assert forbidden not in serialized
    if request.relationship is not None:
        assert request.relationship.relationship_ref not in serialized
    assert envelope.versions.semantic_grounder.endswith("fake-semantic-parser-v1")


def test_model_candidate_cannot_cite_evidence_outside_focus_registry() -> None:
    request = _request("factual-information-question")
    with pytest.raises(Exception) as caught:
        InterpretationEngine(grounder=ModelSemanticGrounder(_CapturingModel(foreign_ref=True))).interpret(request)
    assert getattr(caught.value, "failed_phase", None) == "semantic_grounding"


def test_exact_string_null_is_canonicalized_to_an_unresolved_argument() -> None:
    envelope = InterpretationEngine(
        grounder=ModelSemanticGrounder(_StringNullModel())
    ).interpret(_request("factual-information-question"))
    frame = envelope.semantic_frames[0]
    assert frame.arguments["topic_ref"] is None
    assert frame.unresolved_arguments == ("topic_ref",)


class _FakeCompletions:
    def create(self, **kwargs):  # pragma: no cover - request construction only
        raise AssertionError(kwargs)


class _FakeClient:
    class _Chat:
        completions = _FakeCompletions()

    chat = _Chat()


def test_openai_request_is_tool_free_and_carries_messages_only_inside_bounded_payload() -> None:
    request = _request("factual-information-question")
    model = _CapturingModel()
    grounder = ModelSemanticGrounder(model)
    evidence = InterpretationEngine().evidence_builder.build(request)
    payload = grounder._payload(request, evidence)
    provider = OpenAISemanticCandidateModel(
        model_id="gpt-4o-mini", api_key=None, timeout_seconds=20, client=_FakeClient(),
    )

    parameters = provider.request_parameters(payload)

    assert parameters["model"] == "gpt-4o-mini"
    assert parameters["stream"] is False and parameters["store"] is False
    assert "tools" not in parameters and "functions" not in parameters
    assert [item["role"] for item in parameters["messages"]] == ["system", "user"]
    assert request.focus_message.content not in parameters["messages"][0]["content"]
    assert request.focus_message.content in parameters["messages"][1]["content"]
    schema = parameters["response_format"]["json_schema"]["schema"]
    predicate_enum = schema["properties"]["frames"]["items"]["properties"]["predicate"]["enum"]
    assert "information_request" in predicate_enum


def test_shadow_pipeline_has_no_deterministic_grounder_fallback() -> None:
    settings = SimpleNamespace(
        cognitive_shadow_semantic_parser_model="gpt-4o-mini",
        cognitive_shadow_semantic_parser_timeout_seconds=20,
        openai_api_key=None,
    )
    pipeline = cognitive_pipeline("turn-without-provider", settings)
    # A missing key installs a grounder that fails every turn with a typed
    # configuration code; it is never the lexical rule grounder.
    assert isinstance(pipeline.interpretation_engine.grounder, UnavailableSemanticGrounder)
    assert not isinstance(pipeline.interpretation_engine.grounder, SemanticGrounder)
