"""Offline behavioral review stays separate from fidelity validation (SH-EVAL-008)."""

from __future__ import annotations

import pathlib

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.v0.behavioral_review_v0 import (
    AIEvaluatorProvenance, AdjudicationState, BehavioralReviewRecord, FailureAttribution, ReviewLog, ReviewerKind,
    RubricDimension, RubricLabel, RubricScore, behavioral_outcome, compare_candidates, review_report,
)
from app.ai.Cognitive.system.v0.trace_summary import LIVE_AUTHORITY

DIGEST = "a" * 64
OTHER_PLAN = "b" * 64


def _scores(**failures: RubricLabel) -> tuple[RubricScore, ...]:
    return tuple(
        RubricScore(dimension=dimension, label=failures.get(dimension.name, RubricLabel.PASS), confidence=0.8)
        for dimension in RubricDimension
    )


def _review(review_id: str, candidate: str, *, kind: ReviewerKind = ReviewerKind.HUMAN, request: str = DIGEST,
            attribution: FailureAttribution = FailureAttribution.NONE, at: str = "2026-09-28T10:00:00+00:00",
            adjudicates: tuple[str, ...] = (), **failures: RubricLabel) -> BehavioralReviewRecord:
    return BehavioralReviewRecord(
        review_id=review_id, trace_ref=f"trace:{candidate}", realization_trace_digest=DIGEST, request_digest=request,
        candidate_ref=candidate, reviewer_kind=kind, reviewer_ref=f"{kind.value}-reviewer-1",
        ai_provenance=AIEvaluatorProvenance(provider="local", model_id="judge-model", evaluator_version="judge-v1")
        if kind == ReviewerKind.AI else None,
        reviewed_at=at, scores=_scores(**failures), evaluator_uncertainty=0.2, attribution=attribution,
        adjudication=AdjudicationState.ADJUDICATED if adjudicates else AdjudicationState.INDEPENDENT,
        adjudicates_review_refs=adjudicates,
    )


# Held-out archetypes across domains.  The texts document what was reviewed;
# records never carry candidate wording.
ARCHETYPES = {
    # candidate ref: (fidelity validation, example wording, human review)
    "travel-safe-but-stiff": ("passed", "Your message has been received and noted.",
                              dict(attribution=FailureAttribution.STYLE_CONTROLS, NATURALNESS=RubricLabel.FAIL)),
    "scheduling-fluent-but-plan-inconsistent": ("failed", "Sure! I booked it and also moved your dentist.",
                                                dict(attribution=FailureAttribution.PROTOCOL_DESCRIPTIONS,
                                                     PLAN_COVERAGE=RubricLabel.FAIL)),
    "cooking-helpful-but-unsupported": ("failed", "Bake it at 180C for 25 minutes.",
                                        dict(attribution=FailureAttribution.ACTION_SELECTION,
                                             INTENT_APPROPRIATENESS=RubricLabel.FAIL)),
    "fitness-concise-and-correct": ("passed", "Which distance do you mean?", {}),
    "finance-appropriately-uncertain": ("passed", "I don't have a grounded figure for that.", {}),
}


def _archetype_reviews() -> list[BehavioralReviewRecord]:
    return [_review(f"review:{ref}", ref, **review) for ref, (_, _, review) in ARCHETYPES.items()]


def test_fidelity_and_behavioral_quality_are_reported_separately() -> None:
    report = review_report({ref: fidelity for ref, (fidelity, _, _) in ARCHETYPES.items()}, _archetype_reviews())
    assert report["fidelity_validation"] == {"failed": 2, "passed": 3}
    assert report["behavioral_review"] == {"failed": 3, "passed": 2}
    # A fidelity pass is not a quality pass: the stiff candidate passed validation and failed review.
    assert behavioral_outcome(_archetype_reviews(), candidate_ref="travel-safe-but-stiff") == "failed"
    assert report["failure_attribution"] == {
        "action_selection": 1, "protocol_descriptions": 1, "style_controls": 1,
    }
    assert report["live_authority"] == LIVE_AUTHORITY


def test_an_ai_evaluator_cannot_promote_a_candidate() -> None:
    ai_only = [_review("ai-1", "fitness-concise-and-correct", kind=ReviewerKind.AI)]
    assert behavioral_outcome(ai_only, candidate_ref="fitness-concise-and-correct") == "not_reviewed"
    with pytest.raises(ValidationError, match="model provenance"):
        BehavioralReviewRecord.model_validate({
            **ai_only[0].model_dump(mode="python"), "ai_provenance": None,
        })


def test_disagreement_needs_human_adjudication() -> None:
    candidate = "fitness-concise-and-correct"
    first = _review("h-1", candidate)
    second = _review("h-2", candidate, attribution=FailureAttribution.STYLE_CONTROLS, CONCISION=RubricLabel.FAIL)
    assert behavioral_outcome([first, second], candidate_ref=candidate) == "not_reviewed"
    resolved = _review("h-3", candidate, adjudicates=("h-1", "h-2"), at="2026-09-28T11:00:00+00:00")
    assert behavioral_outcome([first, second, resolved], candidate_ref=candidate) == "passed"
    with pytest.raises(ValidationError, match="adjudication"):
        _review("ai-adj", candidate, kind=ReviewerKind.AI, adjudicates=("h-1", "h-2"))


def test_rubric_is_complete_and_attribution_is_honest() -> None:
    record = _review("h-1", "c")
    assert {item.dimension for item in record.scores} == set(RubricDimension)
    with pytest.raises(ValidationError, match="every rubric dimension"):
        BehavioralReviewRecord.model_validate({**record.model_dump(mode="python"), "scores": record.scores[:-1]})
    with pytest.raises(ValidationError, match="attributed stage"):
        _review("h-2", "c", NATURALNESS=RubricLabel.FAIL)
    with pytest.raises(ValidationError, match="shortfall"):
        _review("h-3", "c", attribution=FailureAttribution.MODEL_CHOICE)
    partial = _review("h-4", "c", attribution=FailureAttribution.STYLE_CONTROLS, CONCISION=RubricLabel.PARTIAL)
    assert partial.verdict == "pass"


def test_candidates_of_one_plan_are_compared_offline() -> None:
    records = [
        _review("h-1", "cand-a"), _review("h-2", "cand-a"),
        _review("h-3", "cand-b", attribution=FailureAttribution.STYLE_CONTROLS, NATURALNESS=RubricLabel.FAIL),
        _review("h-4", "cand-c", request=OTHER_PLAN),
        _review("ai-1", "cand-b", kind=ReviewerKind.AI),
    ]
    assert compare_candidates(records) == {
        DIGEST: [("cand-a", 2, 0), ("cand-b", 0, 1)],
        OTHER_PLAN: [("cand-c", 1, 0)],
    }


def test_review_log_is_append_only_and_never_touches_traces(tmp_path) -> None:
    log = ReviewLog(tmp_path / "reviews.jsonl")
    first, second = _review("h-1", "c"), _review("h-2", "c", attribution=FailureAttribution.STYLE_CONTROLS,
                                                  NATURALNESS=RubricLabel.FAIL)
    log.append(first); log.append(second)
    assert log.records() == (first, second)
    with pytest.raises(ValueError, match="never edited"):
        log.append(first)
    with pytest.raises(ValueError, match="already in the log"):
        log.append(_review("h-3", "c", adjudicates=("h-1", "h-9")))
    log.append(_review("h-3", "c", adjudicates=("h-1", "h-2")))
    with pytest.raises(ValueError, match="same candidate"):
        log.append(_review("h-4", "other", adjudicates=("h-1", "h-2")))
    assert behavioral_outcome(log.records(), candidate_ref="c") == "passed"


def test_no_live_module_imports_the_review_layer() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    importers = sorted(
        str(path.relative_to(root)) for path in root.rglob("*.py")
        if path.name != "behavioral_review_v0.py" and "behavioral_review_v0" in path.read_text(encoding="utf-8")
    )
    assert importers == []
