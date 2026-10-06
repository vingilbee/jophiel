"""Interpretation V0 semantic-parser remediation (brief section 2.2).

Provider outcomes map to stable content-free codes; the compiler rejects with
typed codes; provenance is typed and round-trips; configuration failure is a
typed failure, never the rule grounder; empty input is a bounded unknown;
validated model frames dominate shallow cues; and the regression corpus
covers every required family offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.Cognitive.system.v0.semantic_parser_provider import (
    OpenAISemanticCandidateModel, _INSTRUCTION, resolve_provenance,
)
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineExecutionError
from app.ai.Cognitive.system.v0.contracts.interpretation import InterpretationVersions, SemanticParserProvenance
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import (
    EMPTY_FOCUS_RULE, MODEL_GROUNDING_COMPILER_VERSION, ModelSemanticGrounder, UnavailableSemanticGrounder,
)
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import (
    SemanticModelOutput, SemanticParserError, SemanticParserErrorCode as E,
)
from app.ai.Cognitive.system.v0.interpretation_v0.ontology import (
    CONDITIONAL_MODEL_PREDICATES,
    COMPOSITIONAL_PREDICATES, MODEL_PREDICATE_MANIFEST, PREDICATE_MANIFEST,
)
from app.ai.Cognitive.system.v0.interpretation_v0.regression import (
    DEFAULT_CORPUS, FrozenCandidateModel, RegressionTurn, iter_turns, load_corpus, request_for, run_turn,
)
from app.ai.Cognitive.system.v0.live_shadow.service import pipeline_failure
from app.ai.Cognitive.system.v0.interpretation_v0.models import InterpretationExecutionError
from app.services.cognitive_shadow import cognitive_pipeline, semantic_parser_configuration_code

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _turn(*messages: str, frames: list[dict] | None = None) -> RegressionTurn:
    roles = ["user", "assistant"]
    history = tuple({"role": roles[index % 2], "content": text} for index, text in enumerate(messages))
    return RegressionTurn("t", "test", history, {"frames": frames or []}, {})


def _interpret(turn: RegressionTurn, model):
    return InterpretationEngine(grounder=ModelSemanticGrounder(model)).interpret(request_for(turn))


def _frame(name, evidence_text, polarity="positive", **arguments):
    return {"predicate": name, "polarity": polarity, "arguments": arguments, "evidence_text": evidence_text}


def _parser_code(exc: BaseException) -> E | None:
    while exc is not None:
        if isinstance(exc, SemanticParserError):
            return exc.code
        exc = exc.__cause__
    return None


# ---------------------------------------------------------------------------
# Provider outcomes
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _response(content="{\"frames\": []}", finish_reason="stop", *, refusal=None, tool_calls=None, choices=1):
    message = SimpleNamespace(content=content, refusal=refusal, tool_calls=tool_calls, function_call=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)] * choices)


def _status(cls, code: int):
    return cls("provider body", response=httpx.Response(code, request=_REQUEST), body=None)


def _payload():
    turn = _turn("What is today's date?")
    request = request_for(turn)
    grounder = ModelSemanticGrounder(FrozenCandidateModel({"frames": []}))
    return grounder._payload(request, InterpretationEngine().evidence_builder.build(request))


@pytest.mark.parametrize(("outcome", "code"), [
    (openai.APITimeoutError(request=_REQUEST), E.TIMEOUT),
    (openai.APIConnectionError(request=_REQUEST), E.TRANSPORT),
    (_status(openai.RateLimitError, 429), E.TRANSPORT),
    (_status(openai.InternalServerError, 500), E.TRANSPORT),
    (_status(openai.BadRequestError, 400), E.UNSUPPORTED_REQUEST),
    (_status(openai.AuthenticationError, 401), E.CONFIGURATION_INCOMPATIBLE),
    (_response(refusal="no"), E.REFUSED),
    (_response(finish_reason="content_filter"), E.REFUSED),
    (_response(finish_reason="length"), E.OUTPUT_TRUNCATED),
    (_response(tool_calls=[object()]), E.OUTPUT_INVALID),
    (_response(choices=2), E.OUTPUT_INVALID),
    (_response(content=None), E.OUTPUT_INVALID),
    (_response(content="{not json"), E.MALFORMED_JSON),
    (_response(content="{\"frames\": [{\"predicate\": \"x\"}]}"), E.SCHEMA_INVALID),
])
def test_every_provider_outcome_maps_to_a_stable_code(outcome, code) -> None:
    model = OpenAISemanticCandidateModel(
        model_id="gpt-4o-mini", api_key=None, timeout_seconds=20, client=_Client(outcome),
    )
    with pytest.raises(SemanticParserError) as caught:
        model.generate(_payload())
    assert caught.value.code == code
    assert "provider body" not in str(caught.value) and "not json" not in str(caught.value)
    assert caught.value.retryable is (code in {E.TIMEOUT, E.TRANSPORT})


@pytest.mark.parametrize(("model_id", "timeout", "code"), [
    ("", 20, E.MODEL_NOT_CONFIGURED),
    ("not-a-known-model", 20, E.CONFIGURATION_INCOMPATIBLE),
    ("o3-mini", 20, E.CONFIGURATION_INCOMPATIBLE),  # accepts only its default temperature
    ("gpt-4o-mini", 0, E.CONFIGURATION_INCOMPATIBLE),
])
def test_configuration_is_validated_before_any_client_exists(model_id, timeout, code) -> None:
    with pytest.raises(SemanticParserError) as caught:
        resolve_provenance(model_id=model_id, timeout_seconds=timeout)
    assert caught.value.code == code and not caught.value.retryable


def test_missing_api_key_is_model_not_configured() -> None:
    with pytest.raises(SemanticParserError) as caught:
        OpenAISemanticCandidateModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=20)
    assert caught.value.code == E.MODEL_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Typed provenance
# ---------------------------------------------------------------------------

def test_request_parameters_are_exactly_the_recorded_provenance() -> None:
    client = _Client(_response())
    model = OpenAISemanticCandidateModel(model_id="gpt-4o-mini", api_key=None, timeout_seconds=17, client=client)
    provenance = model.provenance
    parameters = model.request_parameters(_payload())
    assert parameters["model"] == provenance.model_id == "gpt-4o-mini"
    assert parameters["temperature"] == float(provenance.temperature)
    assert parameters[provenance.token_limit_parameter] == provenance.max_output_tokens
    assert parameters["timeout"] == provenance.timeout_seconds == 17
    assert parameters["n"] == provenance.candidate_count == 1
    assert parameters["stream"] is provenance.streaming is False and parameters["store"] is False
    assert parameters["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in parameters and "functions" not in parameters
    assert (provenance.sdk_retries, provenance.replayable, provenance.endpoint) == (0, False, "chat.completions")
    assert provenance.compiler_version == MODEL_GROUNDING_COMPILER_VERSION


class _ProvenancedDouble(FrozenCandidateModel):
    provenance = resolve_provenance(model_id="gpt-4o-mini", timeout_seconds=20)


def test_envelope_carries_typed_provenance_that_round_trips() -> None:
    turn = _turn("Hello there!")
    envelope = _interpret(turn, _ProvenancedDouble({"frames": [_frame("social_opening", "Hello")]}))
    assert envelope.versions.semantic_parser == _ProvenancedDouble.provenance
    payload = json.loads(envelope.versions.model_dump_json())
    assert payload["semantic_parser"]["model_id"] == "gpt-4o-mini"
    assert InterpretationVersions.model_validate(payload) == envelope.versions
    assert SemanticParserProvenance.model_validate(payload["semantic_parser"]).replayable is False


def test_rule_grounded_versions_serialize_without_a_parser_field() -> None:
    envelope = InterpretationEngine().interpret(request_for(_turn("Hello there!")))
    assert envelope.versions.semantic_parser is None
    assert "semantic_parser" not in envelope.versions.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Compiler rejections and deterministic normalizations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("frame", "code"), [
    (_frame("museum_closing_time", "When", museum="x"), E.UNREGISTERED_PREDICATE),  # offline-only predicate
    (_frame("runtime_fact_request", "When", fact_kind="current_date", extra="x"), E.DISALLOWED_ARGUMENT),
    (_frame("runtime_fact_request", "When"), E.MISSING_REQUIRED_ARGUMENT),
    (_frame("runtime_fact_request", "When", fact_kind="tomorrow"), E.ARGUMENT_VALUE_OUTSIDE_VOCABULARY),
])
def test_compiler_rejections_are_typed(frame, code) -> None:
    with pytest.raises(InterpretationExecutionError) as caught:
        _interpret(_turn("When does it close?"), FrozenCandidateModel({"frames": [frame]}))
    assert caught.value.failed_phase == "semantic_grounding"
    assert _parser_code(caught.value) == code


def test_foreign_evidence_and_empty_output_are_typed() -> None:
    class Foreign(FrozenCandidateModel):
        def generate(self, payload):
            return SemanticModelOutput.model_validate({"frames": [{
                "predicate": "social_opening", "polarity": "positive", "arguments": [],
                "evidence_refs": ["not-a-focus-region"],
            }]})

    for model, code in ((Foreign({}), E.FOREIGN_EVIDENCE), (FrozenCandidateModel({"frames": []}), E.NO_FRAMES)):
        with pytest.raises(InterpretationExecutionError) as caught:
            _interpret(_turn("Hello there!"), model)
        assert _parser_code(caught.value) == code


def test_a_bare_deictic_referent_compiles_to_an_explicit_unknown() -> None:
    envelope = _interpret(_turn("Can you check it for me?"), FrozenCandidateModel({"frames": [
        _frame("action_request", "check it", requested_effect="check", subject_ref="it"),
    ]}))
    [frame] = envelope.semantic_frames
    assert frame.arguments["subject_ref"] is None and frame.unresolved_arguments == ("subject_ref",)
    assert [item.key for item in envelope.unknowns] == ["subject_ref"]


def test_a_named_referent_is_never_rewritten() -> None:
    envelope = _interpret(_turn("Can you check the report for me?"), FrozenCandidateModel({"frames": [
        _frame("action_request", "check the report", requested_effect="check", subject_ref="the report"),
    ]}))
    assert envelope.semantic_frames[0].arguments["subject_ref"] == "the report"
    assert not envelope.unknowns


def test_a_repeated_identical_act_keeps_only_the_smallest_span() -> None:
    envelope = _interpret(_turn("Thanks so much!"), FrozenCandidateModel({"frames": [
        _frame("express_gratitude", "Thanks so much!"), _frame("express_gratitude", "Thanks"),
    ]}))
    [frame] = envelope.semantic_frames
    by_id = {item.evidence_id: item for item in envelope.evidence}
    assert [by_id[ref].span.quoted_text for ref in frame.evidence_refs] == ["Thanks"]


def test_distinct_acts_with_different_content_are_never_merged() -> None:
    envelope = _interpret(
        _turn("What's a good source of iron, and how much should I eat per day?"),
        FrozenCandidateModel({"frames": [
            _frame("information_request", "What's a good source", topic_ref="iron sources", question_form="wh"),
            _frame("information_request", "how much should I eat", topic_ref="iron intake", question_form="wh"),
        ]}),
    )
    assert len(envelope.semantic_frames) == 2


def test_unknown_frames_always_record_an_unresolved_meaning() -> None:
    envelope = _interpret(_turn("hmm"), FrozenCandidateModel({"frames": [
        _frame("unknown", "hmm", "unknown", predicate="filler"),
    ]}))
    assert [item.key for item in envelope.unknowns] == ["meaning"]


# ---------------------------------------------------------------------------
# Degraded behavior: semantic unknown versus parser unavailable
# ---------------------------------------------------------------------------

class _MustNotCall:
    version = "must-not-call"
    provenance = None

    def generate(self, payload):  # pragma: no cover - asserting it is never reached
        raise AssertionError("no parser call is made for an empty focus message")


@pytest.mark.parametrize("content", ["", "   "])
def test_empty_focus_is_a_bounded_unknown_without_a_call(content) -> None:
    envelope = _interpret(_turn(content), _MustNotCall())
    [frame] = envelope.semantic_frames
    assert frame.predicate == "unknown" and EMPTY_FOCUS_RULE in frame.grounding_rule_ids
    assert {item.key for item in envelope.unknowns} == {"predicate", "meaning"}


def test_unavailable_parser_is_a_hard_typed_failure_not_a_rule_fallback() -> None:
    settings = SimpleNamespace(
        cognitive_shadow_semantic_parser_model="gpt-4o-mini",
        cognitive_shadow_semantic_parser_timeout_seconds=20, openai_api_key=None,
    )
    pipeline = cognitive_pipeline("turn-without-provider", settings)
    assert isinstance(pipeline.interpretation_engine.grounder, UnavailableSemanticGrounder)
    assert semantic_parser_configuration_code(settings) == "model_not_configured"
    with pytest.raises(InterpretationExecutionError) as caught:
        pipeline.interpretation_engine.interpret(request_for(_turn("Hello there!")))
    assert _parser_code(caught.value) == E.MODEL_NOT_CONFIGURED


@pytest.mark.parametrize(("code", "retryable"), [(E.TIMEOUT, True), (E.TRANSPORT, True), (E.SCHEMA_INVALID, False)])
def test_shadow_failure_codes_carry_the_parser_code_and_retryability(code, retryable) -> None:
    try:
        try:
            try:
                raise SemanticParserError(code)
            except SemanticParserError as parser:
                raise InterpretationExecutionError(
                    cognitive_turn_id="t", failed_phase="semantic_grounding", safe_message="grounding failed",
                ) from parser
        except InterpretationExecutionError as interpretation:
            raise CognitivePipelineExecutionError(
                cognitive_turn_id="t", failed_stage="interpretation", safe_message="interpretation failed",
                nested_stage="semantic_grounding",
            ) from interpretation
    except CognitivePipelineExecutionError as exc:
        failure = pipeline_failure(exc, attempt=1)
    assert failure.code == f"pipeline.interpretation.semantic_grounding.semantic_parser.{code.value}"
    assert failure.retryable is retryable


# ---------------------------------------------------------------------------
# Frame dominance (cue versus validated frame)
# ---------------------------------------------------------------------------

def _hypotheses(envelope) -> dict[str, object]:
    return {item.hypothesis_key.rsplit(".", 1)[-1]: item for item in envelope.hypotheses}


def test_an_information_frame_outranks_an_action_cue_and_retains_the_cue_reading() -> None:
    envelope = _interpret(
        _turn("What's a good source of iron, and how much should I eat per day?"),
        FrozenCandidateModel({"frames": [
            _frame("information_request", "What's a good source", topic_ref="iron sources", question_form="wh"),
        ]}),
    )
    hypotheses = _hypotheses(envelope)
    assert hypotheses["request_information"].disposition.value == "leading"
    action = hypotheses["request_action"]
    assert (action.support.value, action.disposition.value) == ("weak", "retained_alternative")
    cue_ids = {item.evidence_id for item in envelope.evidence if item.cue_key == "conversation.explicit_action_request"}
    assert cue_ids & set(action.supporting_evidence_refs)


def test_rule_grounded_frames_keep_cue_driven_rules() -> None:
    # The lexical grounder reads the same surface form as the cues, so its
    # frames are not independent act evidence (frame dominance is model-only).
    envelope = InterpretationEngine().interpret(request_for(_turn("Can you send it to him?")))
    assert _hypotheses(envelope)["request_action"].disposition.value == "leading"


def test_a_quoted_instruction_is_never_the_speakers_request() -> None:
    envelope = _interpret(_turn('My coworker said "delete the whole database" as a joke.'), FrozenCandidateModel({
        "frames": [_frame(
            "action_request", "delete the whole database", requested_effect="delete database",
            subject_ref="the whole database", embedding="quoted",
        )],
    }))
    hypotheses = _hypotheses(envelope)
    assert hypotheses["request_action"].disposition.value == "rejected"
    assert hypotheses["inform"].disposition.value == "leading"
    assert hypotheses["quoted"].disposition.value == "leading"
    assert "cause_action" not in hypotheses


# ---------------------------------------------------------------------------
# Manifest exposure and the regression corpus
# ---------------------------------------------------------------------------

def test_the_model_is_offered_only_reviewed_generic_predicates() -> None:
    offered = {item.predicate for item in MODEL_PREDICATE_MANIFEST}
    assert set(COMPOSITIONAL_PREDICATES) <= offered
    assert {"information_request", "unknown", "social_opening"} <= offered
    assert not offered & {"museum_closing_time", "send_report", "delete_database", "evaluate_meal"}
    assert MODEL_PREDICATE_MANIFEST[-1].predicate == "unknown"
    payload = _payload()
    # Conditional predicates (inquiry_answer) are offered only while open
    # inquiries are supplied; two-call v1.1 predicates only to v1.1 inputs.
    from app.ai.Cognitive.system.v0.interpretation_v0.ontology import V1_ONLY_MODEL_PREDICATES
    from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import predicate_specs
    assert {item.predicate for item in payload.predicates} == offered - CONDITIONAL_MODEL_PREDICATES - V1_ONLY_MODEL_PREDICATES
    assert V1_ONLY_MODEL_PREDICATES <= {item.predicate for item in predicate_specs(v1_predicates=V1_ONLY_MODEL_PREDICATES)}
    assert all(item.description and not item.description.startswith("Reviewed design-case")
               for item in MODEL_PREDICATE_MANIFEST)
    assert len(PREDICATE_MANIFEST) > len(MODEL_PREDICATE_MANIFEST)


_REQUIRED_FAMILIES = {
    "greeting", "compound_act", "identity", "runtime_fact", "information_request", "action_request",
    "ambiguity", "correction", "follow_up", "social_question", "unsupported_factual_claim", "unsafe_request",
    "quoted_action", "mixed_polarity", "hypothetical", "uninterrupted_trace",
}


def test_corpus_covers_every_required_family_and_compositional_predicate() -> None:
    corpus = load_corpus()
    turns = list(iter_turns(corpus))
    assert corpus["status"] == "draft_for_human_review"
    assert _REQUIRED_FAMILIES <= {item.family for item in turns}
    used = {frame["predicate"] for item in turns for frame in item.frozen_candidate["frames"]}
    assert set(COMPOSITIONAL_PREDICATES) <= used
    traces = [case for case in corpus["cases"] if case["kind"] == "trace"]
    assert traces and all(5 <= len(trace["turns"]) <= 10 for trace in traces)


def test_corpus_text_never_reaches_prompts_or_the_manifest() -> None:
    corpus_messages = {
        message["content"] for item in iter_turns(load_corpus()) for message in item.messages
        if len(message["content"]) > 12
    }
    surfaces = [_INSTRUCTION] + [
        text for item in PREDICATE_MANIFEST
        for text in (item.description, *item.argument_descriptions.values())
    ]
    assert not any(message in surface for message in corpus_messages for surface in surfaces)


@pytest.mark.parametrize("turn", list(iter_turns(load_corpus(DEFAULT_CORPUS))), ids=lambda item: item.case_id)
def test_frozen_candidates_meet_every_corpus_expectation(turn) -> None:
    result = run_turn(turn, FrozenCandidateModel(turn.frozen_candidate))
    assert result.passed, result.mismatches
