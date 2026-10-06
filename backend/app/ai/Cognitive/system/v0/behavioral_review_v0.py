"""Offline behavioral review of shadow Realization candidates (SH-EVAL-008).

Deterministic safety/fidelity validation answers "did the candidate stay
inside the authorized plan?".  It never answers whether the wording was
natural, useful, or appropriate to what the user wanted.  This module holds
that separate, reviewed evaluation layer:

* a review record references the immutable trace and the exact candidate
  digest and never rewrites either;
* rubric labels are stored apart from validator violations;
* reviewer identity, review time, rubric version, per-dimension labels,
  confidence, and adjudication state are explicit;
* an AI evaluator must record its model provenance and can never promote a
  candidate by itself -- only human review (adjudicated when reviewers
  disagree) yields a behavioral pass or fail;
* failures are attributed to the stage that should change (action
  selection, style controls, protocol descriptions, or model choice), never
  "teach the realizer to deviate from the plan".

It is offline tooling: nothing here is imported by a live module, calls a
model, or reaches chat, tools, memory, or delivery.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts.interpretation import FrozenModel

REVIEW_RECORD_VERSION = "behavioral-review-record-v0.1"
RUBRIC_VERSION = "realization-behavioral-rubric-v0.1"


class RubricDimension(StrEnum):
    INTENT_APPROPRIATENESS = "action_to_intent_appropriateness"
    PLAN_COVERAGE = "plan_coverage"
    NATURALNESS = "naturalness_and_register"
    META_LANGUAGE = "no_unnecessary_meta_language"
    CONCISION = "concision"
    TASK_SUCCESS = "task_success_or_useful_recovery"


class RubricLabel(StrEnum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class ReviewerKind(StrEnum):
    HUMAN = "human"
    AI = "ai"


class AdjudicationState(StrEnum):
    INDEPENDENT = "independent"     # one reviewer's own judgement
    ADJUDICATED = "adjudicated"     # the resolution of a recorded disagreement


class FailureAttribution(StrEnum):
    """Which upstream stage a behavioral failure should change."""

    NONE = "none"
    ACTION_SELECTION = "action_selection"      # the plan itself was inappropriate
    STYLE_CONTROLS = "style_controls"          # register, warmth, directness, compression
    PROTOCOL_DESCRIPTIONS = "protocol_descriptions"
    MODEL_CHOICE = "model_choice"
    UNKNOWN = "unknown"


BehavioralOutcome = Literal["passed", "failed", "not_reviewed"]


class RubricScore(FrozenModel):
    dimension: RubricDimension
    label: RubricLabel
    confidence: float = Field(ge=0.0, le=1.0)


class AIEvaluatorProvenance(FrozenModel):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)


class BehavioralReviewRecord(FrozenModel):
    """One reviewer's rubric judgement of one exact shadow candidate."""

    record_version: Literal["behavioral-review-record-v0.1"] = REVIEW_RECORD_VERSION
    review_id: str = Field(min_length=1)
    rubric_version: Literal["realization-behavioral-rubric-v0.1"] = RUBRIC_VERSION
    trace_ref: str = Field(min_length=1)                 # the immutable terminal trace
    realization_trace_digest: str = Field(min_length=64, max_length=64)
    request_digest: str = Field(min_length=64, max_length=64)  # the authorized plan's realization request
    candidate_ref: str = Field(min_length=1)             # the exact candidate id
    reviewer_kind: ReviewerKind
    reviewer_ref: str = Field(min_length=1)
    ai_provenance: AIEvaluatorProvenance | None = None
    reviewed_at: str = Field(min_length=1)
    scores: tuple[RubricScore, ...] = Field(min_length=1)
    evaluator_uncertainty: float = Field(ge=0.0, le=1.0)
    attribution: FailureAttribution
    adjudication: AdjudicationState = AdjudicationState.INDEPENDENT
    adjudicates_review_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> BehavioralReviewRecord:
        stamp = datetime.fromisoformat(self.reviewed_at)
        if stamp.tzinfo is None:
            raise ValueError("review time must carry a timezone")
        dimensions = tuple(item.dimension for item in self.scores)
        if set(dimensions) != set(RubricDimension) or len(dimensions) != len(set(dimensions)):
            raise ValueError("a review labels every rubric dimension exactly once")
        if (self.reviewer_kind == ReviewerKind.AI) != (self.ai_provenance is not None):
            raise ValueError("exactly an AI evaluator records its model provenance")
        if self.adjudication == AdjudicationState.ADJUDICATED:
            if self.reviewer_kind != ReviewerKind.HUMAN or len(self.adjudicates_review_refs) < 2:
                raise ValueError("adjudication is a human resolution of at least two recorded reviews")
        elif self.adjudicates_review_refs:
            raise ValueError("only an adjudication cites the reviews it resolves")
        failed = any(item.label == RubricLabel.FAIL for item in self.scores)
        imperfect = failed or any(item.label == RubricLabel.PARTIAL for item in self.scores)
        if failed and self.attribution == FailureAttribution.NONE:
            raise ValueError("a failed dimension needs an attributed stage")
        if not imperfect and self.attribution != FailureAttribution.NONE:
            raise ValueError("an attribution names the stage behind a shortfall")
        return self

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "fail" if any(item.label == RubricLabel.FAIL for item in self.scores) else "pass"


def behavioral_outcome(records: Iterable[BehavioralReviewRecord], *, candidate_ref: str) -> BehavioralOutcome:
    """The behavioral review axis for one candidate.

    AI reviews never decide it.  One human verdict decides; disagreeing human
    verdicts need a human adjudication, whose verdict decides.
    """

    human = [item for item in records if item.candidate_ref == candidate_ref and item.reviewer_kind == ReviewerKind.HUMAN]
    adjudications = [item for item in human if item.adjudication == AdjudicationState.ADJUDICATED]
    if adjudications:
        latest = max(adjudications, key=lambda item: datetime.fromisoformat(item.reviewed_at))
        return "passed" if latest.verdict == "pass" else "failed"
    verdicts = {item.verdict for item in human}
    if len(verdicts) != 1:
        return "not_reviewed"  # no human review yet, or an unresolved disagreement
    return "passed" if verdicts == {"pass"} else "failed"


def review_report(
    candidates: Mapping[str, Literal["passed", "failed", "not_run"]], records: Iterable[BehavioralReviewRecord],
) -> dict[str, object]:
    """Fidelity validation and behavioral quality, counted separately per candidate."""

    records = tuple(records)
    fidelity = Counter(candidates.values())
    quality = Counter(behavioral_outcome(records, candidate_ref=ref) for ref in candidates)
    attributions = Counter(item.attribution.value for item in records if item.verdict == "fail")
    return {
        "fidelity_validation": dict(sorted(fidelity.items())),
        "behavioral_review": dict(sorted(quality.items())),
        "failure_attribution": dict(sorted(attributions.items())),
        "live_authority": "never_granted_by_shadow",
    }


def compare_candidates(records: Iterable[BehavioralReviewRecord]) -> dict[str, list[tuple[str, int, int]]]:
    """Per authorized plan (request digest), each candidate's human pass/fail counts.

    For offline wording calibration among candidates of one identical plan.
    """

    grouped: dict[str, dict[str, list[int]]] = {}
    for item in records:
        if item.reviewer_kind != ReviewerKind.HUMAN:
            continue
        counts = grouped.setdefault(item.request_digest, {}).setdefault(item.candidate_ref, [0, 0])
        counts[0 if item.verdict == "pass" else 1] += 1
    return {
        digest: sorted(((ref, passed, failed) for ref, (passed, failed) in by_candidate.items()),
                       key=lambda row: (-row[1], row[2], row[0]))
        for digest, by_candidate in sorted(grouped.items())
    }


class ReviewLog:
    """An append-only local JSONL log of review records (never a trace)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> tuple[BehavioralReviewRecord, ...]:
        if not self.path.exists():
            return ()
        return tuple(
            BehavioralReviewRecord.model_validate(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        )

    def append(self, record: BehavioralReviewRecord) -> None:
        existing = self.records()
        if any(item.review_id == record.review_id for item in existing):
            raise ValueError("review ids are unique; a review is never edited in place")
        known = {item.review_id for item in existing}
        if not set(record.adjudicates_review_refs) <= known:
            raise ValueError("an adjudication resolves reviews already in the log")
        if any(
            item.review_id in record.adjudicates_review_refs and item.candidate_ref != record.candidate_ref
            for item in existing
        ):
            raise ValueError("an adjudication resolves reviews of the same candidate")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")


__all__ = [
    "AIEvaluatorProvenance", "AdjudicationState", "BehavioralReviewRecord", "FailureAttribution", "RUBRIC_VERSION",
    "REVIEW_RECORD_VERSION", "ReviewLog", "ReviewerKind", "RubricDimension", "RubricLabel", "RubricScore",
    "behavioral_outcome", "compare_candidates", "review_report",
]
