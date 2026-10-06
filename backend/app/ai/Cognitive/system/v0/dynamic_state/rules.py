"""Transition-rule registry connecting grounded evidence to state changes."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Direction, SourceKind, StateKey, TransitionEvidence, UpdateRequest


@dataclass(frozen=True)
class TransitionRule:
    rule_id: str
    state_key: StateKey
    direction: Direction
    source_kind: SourceKind

    def validate(self, evidence: TransitionEvidence) -> None:
        actual = (evidence.state_key, evidence.direction, evidence.source_kind)
        expected = (self.state_key, self.direction, self.source_kind)
        if actual != expected:
            raise ValueError(f"evidence {evidence.evidence_id!r} does not satisfy {self.rule_id}: {actual} != {expected}")
        if evidence.source_kind == SourceKind.APPRAISAL and not evidence.source_appraisal_frame_refs:
            raise ValueError(f"appraisal rule {self.rule_id} requires an appraisal frame reference")
        if evidence.source_kind == SourceKind.OUTCOME and not evidence.source_outcome_refs:
            raise ValueError(f"outcome rule {self.rule_id} requires an outcome reference")
        if evidence.direction == Direction.BRANCH_DEPENDENT and len(evidence.branch_refs) < 2:
            raise ValueError(f"branch-dependent rule {self.rule_id} requires at least two branches")


class TransitionRuleRegistry:
    def __init__(self, rules: tuple[TransitionRule, ...] = ()) -> None:
        self._rules: dict[str, TransitionRule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: TransitionRule) -> None:
        if rule.rule_id in self._rules:
            raise ValueError(f"duplicate transition rule: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def resolve(self, evidence: TransitionEvidence) -> TransitionRule:
        try:
            rule = self._rules[evidence.rule_id]
        except KeyError as exc:
            raise ValueError(f"unknown transition rule: {evidence.rule_id}") from exc
        rule.validate(evidence)
        return rule

    def construct(
        self,
        request: UpdateRequest,
        constructor: object | None = None,
    ) -> tuple[TransitionEvidence, ...]:
        """Construct and validate transitions from appraisal/outcome evidence."""
        if constructor is None:
            from .construction import TransitionEvidenceConstructor

            constructor = TransitionEvidenceConstructor()
        evidence = constructor.construct(request)  # type: ignore[attr-defined]
        for item in evidence:
            self.resolve(item)
        return evidence

    @property
    def rule_ids(self) -> frozenset[str]:
        return frozenset(self._rules)


_RULE_ROWS = (
    ("activation.high-relevance.v1", StateKey.ACTIVATION, Direction.INCREASE, SourceKind.APPRAISAL),
    ("activation.novel-grounded-event.v1", StateKey.ACTIVATION, Direction.INCREASE, SourceKind.APPRAISAL),
    ("activation.novel-uncertain-event.v1", StateKey.ACTIVATION, Direction.INCREASE, SourceKind.APPRAISAL),
    ("alignment.explicit-boundary-mismatch.v1", StateKey.SOCIAL_ALIGNMENT, Direction.DECREASE, SourceKind.APPRAISAL),
    ("alignment.explicit-positive-feedback.v1", StateKey.SOCIAL_ALIGNMENT, Direction.INCREASE, SourceKind.APPRAISAL),
    ("alignment.neutral-task-event.v1", StateKey.SOCIAL_ALIGNMENT, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("alignment.task-failure-no-effect.v1", StateKey.SOCIAL_ALIGNMENT, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("alignment.verified-repair.v1", StateKey.SOCIAL_ALIGNMENT, Direction.INCREASE, SourceKind.OUTCOME),
    ("confidence.dangerous-alternative.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.DECREASE, SourceKind.APPRAISAL),
    ("confidence.explicit-correction.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.DECREASE, SourceKind.OUTCOME),
    ("confidence.grounded-proposition.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.INCREASE, SourceKind.APPRAISAL),
    ("confidence.high-certainty.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.INCREASE, SourceKind.APPRAISAL),
    ("confidence.low-certainty.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.DECREASE, SourceKind.APPRAISAL),
    ("confidence.uncertain-proposition.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.DECREASE, SourceKind.APPRAISAL),
    ("confidence.unknown-appraisal-no-midpoint.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("curiosity.irrelevant-gap-no-effect.v1", StateKey.CURIOSITY, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("curiosity.prohibited-gap-inhibited.v1", StateKey.CURIOSITY, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("curiosity.relevant-recoverable-gap.v1", StateKey.CURIOSITY, Direction.INCREASE, SourceKind.APPRAISAL),
    ("curiosity.unanswerable-gap-inhibited.v1", StateKey.CURIOSITY, Direction.DECREASE, SourceKind.APPRAISAL),
    ("decay.activation-toward-baseline.v1", StateKey.ACTIVATION, Direction.DECREASE, SourceKind.ELAPSED_TIME),
    ("decay.confidence-evidence-bound.v1", StateKey.EPISTEMIC_CONFIDENCE, Direction.UNCHANGED, SourceKind.ELAPSED_TIME),
    ("decay.curiosity-persists-active-gap.v1", StateKey.CURIOSITY, Direction.UNCHANGED, SourceKind.ELAPSED_TIME),
    ("engagement.controllable-explanation.v1", StateKey.ENGAGEMENT, Direction.INCREASE, SourceKind.APPRAISAL),
    ("engagement.correction-no-direct-effect.v1", StateKey.ENGAGEMENT, Direction.UNCHANGED, SourceKind.OUTCOME),
    ("engagement.relevant-viable-progress.v1", StateKey.ENGAGEMENT, Direction.INCREASE, SourceKind.APPRAISAL),
    ("engagement.silence-unknown.v1", StateKey.ENGAGEMENT, Direction.UNCHANGED, SourceKind.OUTCOME),
    ("engagement.verified-close.v1", StateKey.ENGAGEMENT, Direction.DECREASE, SourceKind.OUTCOME),
    ("engagement.viable-active-goal.v1", StateKey.ENGAGEMENT, Direction.INCREASE, SourceKind.APPRAISAL),
    ("goal-pressure.important-unresolved.v1", StateKey.GOAL_PRESSURE, Direction.INCREASE, SourceKind.APPRAISAL),
    ("goal-pressure.no-outcome-persistence.v1", StateKey.GOAL_PRESSURE, Direction.UNCHANGED, SourceKind.APPRAISAL),
    ("goal-pressure.repair-not-completion.v1", StateKey.GOAL_PRESSURE, Direction.UNCHANGED, SourceKind.OUTCOME),
    ("goal-pressure.silence-no-success.v1", StateKey.GOAL_PRESSURE, Direction.UNCHANGED, SourceKind.OUTCOME),
    ("goal-pressure.uncontrollable-external-goal.v1", StateKey.GOAL_PRESSURE, Direction.INCREASE, SourceKind.APPRAISAL),
    ("goal-pressure.unresolved-obstruction.v1", StateKey.GOAL_PRESSURE, Direction.INCREASE, SourceKind.APPRAISAL),
    ("goal-pressure.verified-completion.v1", StateKey.GOAL_PRESSURE, Direction.DECREASE, SourceKind.OUTCOME),
    ("strain.explicit-boundary-mismatch.v1", StateKey.INTERACTION_STRAIN, Direction.INCREASE, SourceKind.APPRAISAL),
    ("strain.high-consequence-boundary.v1", StateKey.INTERACTION_STRAIN, Direction.INCREASE, SourceKind.APPRAISAL),
    ("strain.norm-boundary.v1", StateKey.INTERACTION_STRAIN, Direction.INCREASE, SourceKind.APPRAISAL),
    ("strain.repeated-misunderstanding.v1", StateKey.INTERACTION_STRAIN, Direction.INCREASE, SourceKind.APPRAISAL),
    ("strain.repeated-technical-failure.v1", StateKey.INTERACTION_STRAIN, Direction.INCREASE, SourceKind.APPRAISAL),
    ("strain.verified-repair.v1", StateKey.INTERACTION_STRAIN, Direction.DECREASE, SourceKind.OUTCOME),
    ("valence.disputed-branches-shared-negative.v1", StateKey.VALENCE, Direction.DECREASE, SourceKind.APPRAISAL),
    ("valence.negative-goal-congruence.v1", StateKey.VALENCE, Direction.DECREASE, SourceKind.APPRAISAL),
    ("valence.neutral-congruence-no-effect.v1", StateKey.VALENCE, Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL),
    ("valence.opposed-branches-provisional.v1", StateKey.VALENCE, Direction.BRANCH_DEPENDENT, SourceKind.APPRAISAL),
    ("valence.positive-goal-congruence.v1", StateKey.VALENCE, Direction.INCREASE, SourceKind.APPRAISAL),
)

DEFAULT_RULE_REGISTRY = TransitionRuleRegistry(tuple(TransitionRule(*row) for row in _RULE_ROWS))
