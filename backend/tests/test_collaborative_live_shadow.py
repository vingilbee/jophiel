"""Collaborative content through the real live-shadow path: observational isolation, replay, provider.

Hermetic: settings are constructed explicitly, model doubles are reviewed,
and the OpenAI adapter is exercised only against a fake client.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.Cognitive.system.v0.collaboration_provider import OpenAICollaborationModel, resolve_provenance
from app.ai.Cognitive.system.v0.collaborative_content_v0 import CollaborativeContentEngine
from app.ai.Cognitive.system.v0.collaborative_content_v0.doubles import ScriptedCollaborationModel
from app.ai.Cognitive.system.v0.collaborative_content_v0.model_protocol import CollaborationProviderError
from app.ai.Cognitive.system.v0.contracts.collaboration import CollaborationFailureCode as F
from app.ai.Cognitive.system.v0.contracts.workspace import ReconciliationState, WorkspaceAuthorityMode
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import ModelSemanticGrounder
from app.ai.Cognitive.system.v0.live_shadow import ObservationContext, observe, replay
from app.ai.Cognitive.system.v0.live_shadow.digests import json_payload, payload_digest
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from cognitive_shadow_support import make_capture
from simulated_authority_support import DINNER, DinnerSemanticModel, dinner_script
from test_cognitive_shadow_compiler import _as_predecessor

TURNS = DINNER["turns"]


def _factory(collaboration=None):
    def factory(turn_id: str):
        pipeline = build_pipeline(turn_id)
        pipeline.interpretation_engine = InterpretationEngine(grounder=ModelSemanticGrounder(DinnerSemanticModel(TURNS)))
        if collaboration is not None:
            pipeline.collaboration_engine = CollaborativeContentEngine(collaboration)
        return pipeline
    return factory


def _observe(capture, predecessor=None, collaboration=None):
    return observe(capture, predecessor, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-10-01T00:00:00+00:00",
    ), pipeline_factory=_factory(collaboration))


def _conversation(count: int, collaboration_factory=lambda: ScriptedCollaborationModel(dinner_script)):
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    history, ids, out, previous = [], [], [], None
    for turn in TURNS[:count]:
        history.append(("user", turn["user"]))
        ids.append(str(uuid.uuid4()))
        capture = make_capture(user_ref=user, conversation_ref=conversation, contents=tuple(history),
                               message_ids=tuple(ids),
                               previous_user_message_id=previous[1].focus_message_id if previous else None)
        record = _observe(capture, _as_predecessor(*previous) if previous else None, collaboration_factory())
        assert record.status.value == "completed", record.failure
        out.append((record, capture))
        history.append(("assistant", turn["assistant"]))
        ids.append(str(uuid.uuid4()))
        previous = (record, capture)
    return out


def test_observational_shadow_never_commits_a_shadow_proposal() -> None:
    (greeting, _), (first, _), (second, _) = _conversation(3)
    created = first.pipeline_trace.collaboration
    [outcome] = created.outcomes
    assert outcome.status == "proposed" and outcome.proposal.operation.value == "create"
    assert created.workspace.snapshot.authority_mode == WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW
    # The greeting recorded nothing to carry, so the create turn compiles exactly as before.
    assert greeting.compiled_request.workspace_inputs is None and first.compiled_request.workspace_inputs is None
    # The next turn carries exactly what was proposed, observationally, with no delivery.
    inputs = second.compiled_request.workspace_inputs
    assert inputs.authority_mode == WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW and inputs.delivered_outcomes == ()
    assert inputs.predecessor_record == created.turn_record
    workspace = second.pipeline_trace.collaboration.workspace
    [record] = workspace.reconciliations
    assert record.state == ReconciliationState.SHADOW_ONLY_NOT_DELIVERED and record.outcome_ref is None
    assert workspace.snapshot.heads == () and workspace.snapshot.revisions == ()
    # So "why does the stew fit" has no shadow-owned artifact to explain: nothing is invented.
    [target] = workspace.targets
    assert target.status.value != "resolved_artifact"


def test_a_collaborative_record_replays_from_its_recorded_candidate_without_a_provider() -> None:
    _, (record, capture) = _conversation(2)
    calls = []

    class Forbidden:
        provenance = None

        def generate(self, payload):  # pragma: no cover - must never run
            calls.append(payload)
            raise AssertionError("replay called the collaboration provider")

    report = replay(
        compiled_request_payload=json_payload(record.compiled_request),
        pipeline_trace_payload=json_payload(record.pipeline_trace),
        compiled_request_digest=record.compiled_request_digest, stored_semantic_digest=record.semantic_digest,
        versions_payload=record.versions.model_dump(mode="json"), pipeline_factory=_factory(Forbidden()),
    )
    assert (report.status.value, calls) == ("match", [])


def test_a_disabled_provider_is_a_typed_failure_that_is_declined() -> None:
    _, (record, _) = _conversation(2, collaboration_factory=lambda: None)
    trace = record.pipeline_trace
    [outcome] = trace.collaboration.outcomes
    assert (outcome.status, outcome.failure.code) == ("failed", F.PROVIDER_NOT_CONFIGURED)
    keys = [move.definition_ref.action_key for move in trace.action_selection.plan.moves]
    assert keys[0] == "commit_or_coordinate.decline_request"
    assert "collaborate.deliver_artifact" not in keys


# ---------------------------------------------------------------------------
# Settings wiring (hermetic: explicit settings objects, never the .env)
# ---------------------------------------------------------------------------

def _settings(**overrides):
    from app.core.config import Settings

    return Settings(_env_file=None, openai_api_key="sk-test", **overrides)


def test_collaboration_engine_is_disabled_by_default_and_never_falls_back() -> None:
    from app.services.cognitive_shadow import collaboration_engine

    assert collaboration_engine(_settings()).model is None
    engine = collaboration_engine(_settings(cognitive_shadow_collaboration_enabled=True))
    assert engine.model is None and engine.unavailable_code == F.PROVIDER_NOT_CONFIGURED
    engine = collaboration_engine(_settings(cognitive_shadow_collaboration_enabled=True,
                                            cognitive_shadow_collaboration_model="not-a-known-model"))
    assert engine.model is None and engine.unavailable_code == F.PROVIDER_CONFIGURATION_INCOMPATIBLE
    engine = collaboration_engine(_settings(cognitive_shadow_collaboration_enabled=True,
                                            cognitive_shadow_collaboration_model="gpt-4o-mini"))
    assert isinstance(engine.model, OpenAICollaborationModel)


# ---------------------------------------------------------------------------
# The OpenAI adapter against a fake client
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _response(content, *, finish="stop", refusal=None):
    message = SimpleNamespace(content=content, refusal=refusal, tool_calls=None, function_call=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def _payload():
    """The exact payload the engine built for the create turn of the dinner conversation."""

    payloads = []
    model = ScriptedCollaborationModel(lambda payload: payloads.append(payload) or dinner_script(payload))
    _conversation(2, collaboration_factory=lambda: model)
    return payloads[-1]


@pytest.fixture(scope="module")
def payload():
    return _payload()


def test_adapter_request_is_strict_tool_free_and_unstored(payload) -> None:
    client = FakeClient(_response(json.dumps({"title": None, "operations": [], "content_items": [], "inquiries": []})))
    model = OpenAICollaborationModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=10, client=client)
    model.generate(payload)
    [call] = client.calls
    assert call["store"] is False and call["n"] == 1 and call["stream"] is False and "tools" not in call
    assert call["response_format"]["json_schema"]["strict"] is True
    assert model.provenance.replayable is False and model.provenance.sdk_retries == 0


@pytest.mark.parametrize(("response", "code"), [
    (_response("not json"), F.PROVIDER_OUTPUT_INVALID),
    (_response(json.dumps({"unexpected": 1})), F.SCHEMA_INVALID),
    (_response(None, refusal="no"), F.PROVIDER_REFUSED),
    (_response("{}", finish="length"), F.PROVIDER_OUTPUT_INVALID),
])
def test_adapter_maps_every_bad_output_to_a_typed_failure(payload, response, code) -> None:
    model = OpenAICollaborationModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=10,
                                     client=FakeClient(response))
    with pytest.raises(CollaborationProviderError) as caught:
        model.generate(payload)
    assert caught.value.code == code


def test_adapter_timeout_is_retryable_and_configuration_is_checked_before_any_client() -> None:
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid")
    model = OpenAICollaborationModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=10,
                                     client=FakeClient(error=openai.APITimeoutError(request=request)))
    with pytest.raises(CollaborationProviderError) as caught:
        model.generate(_payload())
    assert caught.value.code == F.PROVIDER_TIMEOUT and caught.value.retryable
    for kwargs, code in (({"model_id": "", "timeout_seconds": 5}, F.PROVIDER_NOT_CONFIGURED),
                         ({"model_id": "gpt-4o-mini", "timeout_seconds": 0}, F.PROVIDER_CONFIGURATION_INCOMPATIBLE),
                         ({"model_id": "unknown-model", "timeout_seconds": 5}, F.PROVIDER_CONFIGURATION_INCOMPATIBLE)):
        with pytest.raises(CollaborationProviderError) as caught:
            resolve_provenance(**kwargs)
        assert caught.value.code == code
    with pytest.raises(CollaborationProviderError):
        OpenAICollaborationModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=5)


def test_the_trace_summary_shows_collaboration_structure_without_values() -> None:
    from app.ai.Cognitive.system.v0.trace_summary import summarize_trace
    from test_cognitive_shadow_trace_summary import _row

    _, (first, _), (second, _) = _conversation(3)
    summary = summarize_trace(_row(first))
    assert summary["workspace"]["status"] == "ran" and summary["workspace"]["authority_mode"] == "observational_shadow"
    [goal] = summary["collaboration"]["goals"]
    assert (goal["status"], goal["operation"], goal["creates_revision"], goal["item_roles"]) == (
        "proposed", "create", True, ["change"])
    rendered = str(summary)
    assert "chickpea" not in rendered and "vegetarian" not in rendered  # never artifact values
    later = summarize_trace(_row(second))
    assert later["workspace"]["reconciliation_states"] == ["shadow_only_not_delivered"]
