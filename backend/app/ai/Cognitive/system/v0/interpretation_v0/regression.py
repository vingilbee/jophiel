"""Structured regression scoring for the model-backed semantic parser.

Pure: no provider SDK, database, or network.  A corpus case holds bounded
messages, a reviewed *frozen candidate* (what a correct parser returns, with
evidence cited by span text), and expected semantics plus downstream
hypotheses.  ``FrozenCandidateModel`` replays the frozen candidate through the
real compiler and rules offline; any ``SemanticCandidateModel`` (including the
live provider) can be scored against the same expectations.

Corpus text lives only in ``data/``; it is never embedded in a prompt, the
predicate manifest, or a runtime branch.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contracts.interpretation import HypothesisDimension, HypothesisDisposition, InterpretationEnvelope
from .engine import InterpretationEngine
from .fixture_validator import _runtime_case
from .model_grounding import ModelSemanticGrounder
from .model_protocol import SemanticModelOutput, SemanticModelPayload, SemanticParserError
from .models import InterpretationOpenInquiry
from .ontology import requested_outcome

# v0.2 scores the closed requested-outcome class of each frame and supplies a
# turn's reviewed ``open_inquiries`` (protocol v0.3) to the parser.
REGRESSION_SCORER_VERSION = "semantic-parser-regression-scorer-v0.2"
DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "data" / "semantic_parser_regression_corpus_v0.json"
DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "data" / "semantic_parser_regression_results_v0.jsonl"
_NEUTRAL_BIASES = {key: 0.5 for key in (
    "benign_prior", "trust_prior", "threat_sensitivity", "confirmation_tendency",
    "self_attribution_tendency", "ambiguity_tolerance", "social_affiliation_prior",
)}


@dataclass(frozen=True)
class RegressionTurn:
    """One scored focus message and the bounded messages before it."""

    case_id: str
    family: str
    messages: tuple[dict[str, str], ...]
    frozen_candidate: dict[str, Any]
    expected: dict[str, Any]
    open_inquiries: tuple[dict[str, Any], ...] = ()


def load_corpus(path: Path | str = DEFAULT_CORPUS) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_turns(corpus: dict[str, Any]) -> Iterator[RegressionTurn]:
    """Flatten single-turn cases and uninterrupted traces into scored turns."""

    for case in corpus["cases"]:
        if case["kind"] == "single":
            yield RegressionTurn(
                case["case_id"], case["family"], tuple(case["messages"]),
                case["frozen_candidate"], case["expected"], tuple(case.get("open_inquiries", ())),
            )
            continue
        history: list[dict[str, str]] = []
        for index, turn in enumerate(case["turns"], start=1):
            history.append({"role": "user", "content": turn["user"]})
            yield RegressionTurn(
                f"{case['case_id']}#{index}", case["family"], tuple(history),
                turn["frozen_candidate"], turn["expected"], tuple(turn.get("open_inquiries", ())),
            )
            history.append({"role": "assistant", "content": turn["assistant"]})


def request_for(turn: RegressionTurn):
    messages = [
        {"message_id": f"m{index}", "role": item["role"], "content": item["content"]}
        for index, item in enumerate(turn.messages)
    ]
    case = {
        "case_id": turn.case_id, "messages": messages, "focus_message_id": messages[-1]["message_id"],
        "runtime_context": {"attachments": [], "available_capabilities": [], "last_tool_event": None,
                            "task_contract": None},
        "bias_scenarios": [{"profile_id": "neutral", "compiled_biases": _NEUTRAL_BIASES}],
    }
    request = _runtime_case(case)[1]
    if not turn.open_inquiries:
        return request
    return request.model_copy(update={"open_inquiries": tuple(
        InterpretationOpenInquiry.model_validate(item) for item in turn.open_inquiries
    )})


class FrozenCandidateModel:
    """Offline double: returns a reviewed candidate, citing regions by text.

    Each frozen frame names ``evidence_text``; the double cites the smallest
    supplied focus region containing it, exactly as a parser told to cite the
    smallest owning region should.
    """

    version = "frozen-candidate-double-v0.1"
    provenance = None

    def __init__(self, candidate: dict[str, Any]) -> None:
        self.candidate = candidate

    def generate(self, payload: SemanticModelPayload) -> SemanticModelOutput:
        frames = []
        for frame in self.candidate["frames"]:
            owning = [
                region for region in payload.evidence_regions
                if frame["evidence_text"].casefold() in region.text.casefold()
            ]
            if not owning:
                raise ValueError(f"frozen evidence text is not a focus region: {frame['evidence_text']!r}")
            region = min(owning, key=lambda item: (item.end - item.start, item.start))
            frames.append({
                "predicate": frame["predicate"], "polarity": frame.get("polarity", "positive"),
                "arguments": [{"name": key, "value": value} for key, value in frame["arguments"].items()],
                "evidence_refs": [region.evidence_ref],
            })
        return SemanticModelOutput.model_validate({"frames": frames})


def _keys(envelope: InterpretationEnvelope, dimension: HypothesisDimension, disposition: HypothesisDisposition):
    return sorted(
        item.hypothesis_key.rsplit(".", 1)[-1] for item in envelope.hypotheses
        if item.dimension == dimension and item.disposition == disposition
    )


@dataclass
class TurnScore:
    case_id: str
    family: str
    passed: bool
    failure_category: str | None
    mismatches: list[str] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "family": self.family, "passed": self.passed,
            "failure_category": self.failure_category, "mismatches": self.mismatches, "observed": self.observed,
        }


def observe(envelope: InterpretationEnvelope) -> dict[str, Any]:
    frames = envelope.semantic_frames
    A, G = HypothesisDimension.COMMUNICATIVE_ACT, HypothesisDimension.SPEAKER_GOAL
    return {
        "predicates": sorted(frame.predicate for frame in frames),
        "polarities": sorted(f"{frame.predicate}:{frame.polarity}" for frame in frames),
        "unresolved": sorted(f"{frame.predicate}.{name}" for frame in frames for name in frame.unresolved_arguments),
        "embedded": sorted(
            f"{frame.predicate}:{frame.arguments['embedding']}" for frame in frames
            if frame.arguments.get("embedding") not in (None, "direct")
        ),
        "arguments": {
            frame.predicate: {key: value for key, value in frame.arguments.items() if value is not None}
            for frame in frames
        },
        "requested_outcomes": sorted(
            f"{frame.predicate}:{requested_outcome(frame.predicate).value}" for frame in frames
            if requested_outcome(frame.predicate) is not None
        ),
        "leading_acts": _keys(envelope, A, HypothesisDisposition.LEADING),
        "retained_acts": _keys(envelope, A, HypothesisDisposition.RETAINED_ALTERNATIVE),
        "rejected_acts": _keys(envelope, A, HypothesisDisposition.REJECTED),
        "leading_goals": _keys(envelope, G, HypothesisDisposition.LEADING),
    }


_SEMANTIC_FIELDS = ("predicates", "polarities", "unresolved", "embedded", "requested_outcomes")
_DOWNSTREAM_FIELDS = ("leading_acts", "retained_acts", "rejected_acts", "leading_goals")


def _options(expected) -> list:
    """An expectation, or its reviewed alternatives (``{"any_of": [...]}``)."""

    return list(expected["any_of"]) if isinstance(expected, dict) and "any_of" in expected else [expected]


def _list_matches(expected, observed: list) -> bool:
    return any(sorted(option) == observed for option in _options(expected))


def score(turn: RegressionTurn, envelope: InterpretationEnvelope | None, error: Exception | None) -> TurnScore:
    """Exact comparison of every expectation the case states.

    Unstated fields are not scored.  A field may list reviewed alternative
    parses as ``{"any_of": [...]}``; one must match exactly.
    ``required_arguments`` asserts only that a named argument is present and
    non-null (its wording is the parser's).
    """

    if envelope is None:
        parser_error = error
        while parser_error is not None and not isinstance(parser_error, SemanticParserError):
            parser_error = parser_error.__cause__
        category = f"parser_error.{parser_error.code.value}" if parser_error is not None else "pipeline_error"
        return TurnScore(turn.case_id, turn.family, False, category, [category])
    observed = observe(envelope)
    expected = turn.expected
    semantic: list[str] = []
    downstream: list[str] = []
    for name in _SEMANTIC_FIELDS:
        if name in expected and not _list_matches(expected[name], observed[name]):
            semantic.append(f"{name}: expected {expected[name]}, got {observed[name]}")
    for qualified in expected.get("required_arguments", ()):
        predicate, argument = qualified.split(".", 1)
        if argument not in observed["arguments"].get(predicate, {}):
            semantic.append(f"required argument {qualified} missing or null")
    for predicate, values in expected.get("argument_values", {}).items():
        for argument, value in values.items():
            actual = observed["arguments"].get(predicate, {}).get(argument)
            if actual not in _options(value):
                semantic.append(f"{predicate}.{argument}: expected {value!r}, got {actual!r}")
    for name in _DOWNSTREAM_FIELDS:
        if name in expected and not _list_matches(expected[name], observed[name]):
            downstream.append(f"{name}: expected {expected[name]}, got {observed[name]}")
    category = "semantic_mismatch" if semantic else "downstream_mismatch" if downstream else None
    return TurnScore(turn.case_id, turn.family, category is None, category, semantic + downstream, observed)


def run_turn(turn: RegressionTurn, model) -> TurnScore:
    engine = InterpretationEngine(grounder=ModelSemanticGrounder(model))
    try:
        envelope = engine.interpret(request_for(turn))
    except Exception as exc:  # scored, never raised: the category records it
        return score(turn, None, exc)
    return score(turn, envelope, None)


def summarize(scores: list[TurnScore]) -> dict[str, Any]:
    by_family: dict[str, Counter] = {}
    for item in scores:
        by_family.setdefault(item.family, Counter())["pass" if item.passed else "fail"] += 1
    return {
        "turns": len(scores), "passed": sum(item.passed for item in scores),
        "failure_categories": dict(Counter(item.failure_category for item in scores if not item.passed)),
        "by_family": {family: dict(counts) for family, counts in sorted(by_family.items())},
    }


__all__ = [
    "DEFAULT_CORPUS", "DEFAULT_RESULTS", "FrozenCandidateModel", "REGRESSION_SCORER_VERSION", "RegressionTurn",
    "TurnScore", "iter_turns", "load_corpus", "observe", "request_for", "run_turn", "score", "summarize",
]
