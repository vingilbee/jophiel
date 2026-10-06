"""Deterministic appraisal/outcome-to-contribution construction for V0."""

from __future__ import annotations

from .models import (
    AppraisalFrame,
    AttentionSignal,
    ControlContext,
    Direction,
    DynamicStateContext,
    GroundedObject,
    ScopeType,
    SourceKind,
    StateKey,
    StateValue,
    StepBand,
    TransitionEvidence,
    UpdateRequest,
)
from .policy import DEFAULT_BASELINE_SCORES, band_for_score


class TransitionEvidenceConstructor:
    """Select sparse V0 rules from appraisal frames and trusted outcomes."""

    def construct(self, request: UpdateRequest) -> tuple[TransitionEvidence, ...]:
        items: list[TransitionEvidence] = []
        for value in request.previous_snapshot.values:
            selected = self._select(value, request)
            if selected is not None:
                items.append(selected)
        return tuple(items)

    def seed_values(self, request: UpdateRequest) -> tuple[StateValue, ...]:
        """Compile baselines for newly encountered target-scoped objects."""
        context = request.context
        if context is None:
            return ()
        existing = {
            (value.state_key, value.scope_type, value.scope_ref)
            for value in request.previous_snapshot.values
        }
        specifications: list[tuple[StateKey, ScopeType, str, bool, bool]] = []
        for item in context.objects:
            if item.object_type == ScopeType.UNKNOWN:
                specifications.append(
                    (StateKey.CURIOSITY, item.object_type, item.object_ref, item.status == "active", item.recoverability == "bounded")
                )
            elif item.object_type in {
                ScopeType.PROPOSITION,
                ScopeType.HYPOTHESIS,
                ScopeType.ACTION_CANDIDATE,
                ScopeType.ACTION_PLAN,
            }:
                specifications.append((StateKey.EPISTEMIC_CONFIDENCE, item.object_type, item.object_ref, item.status == "active", True))
            elif item.object_type == ScopeType.GOAL:
                specifications.extend(
                    (
                        (StateKey.GOAL_PRESSURE, item.object_type, item.object_ref, item.status == "active", True),
                        (StateKey.ENGAGEMENT, item.object_type, item.object_ref, item.status == "active", True),
                    )
                )
            elif item.object_type == ScopeType.PARTICIPANT:
                specifications.append((StateKey.SOCIAL_ALIGNMENT, item.object_type, item.object_ref, item.status == "active", True))
        seeds: list[StateValue] = []
        for state_key, scope_type, scope_ref, active, learnable in specifications:
            identity = (state_key, scope_type, scope_ref)
            if identity in existing:
                continue
            existing.add(identity)
            baseline = self._baseline(state_key, request)
            seeds.append(
                StateValue(
                    state_key=state_key,
                    scope_type=scope_type,
                    scope_ref=scope_ref,
                    score=baseline,
                    band=band_for_score(state_key, baseline),
                    baseline_score=baseline,
                    evidence_refs=(scope_ref,),
                    active=active,
                    learnable=learnable,
                )
            )
        return tuple(seeds)

    @staticmethod
    def _baseline(state_key: StateKey, request: UpdateRequest) -> int:
        control = request.personality.state_controls.get(state_key)
        return control.baseline_score if control else DEFAULT_BASELINE_SCORES[state_key]

    def attention(self, request: UpdateRequest) -> tuple[AttentionSignal, ...]:
        context = request.context
        if context is None or not context.semantic_frames:
            return ()
        predicate = context.semantic_frames[0].predicate
        target_type, target_ref, weight = self._attention_target(predicate, request)
        frame_refs = tuple(frame.frame_id for frame in request.appraisal_frames)
        reason_refs = (*frame_refs[:1], target_ref)
        features = {
            "moderate": dict(goal_relevance=100, recency=100),
            "high": dict(goal_relevance=100, goal_priority=100, uncertainty=100, recency=100),
            "critical": dict(
                goal_relevance=100,
                goal_priority=100,
                uncertainty=100,
                dispute=100,
                boundary_significance=100,
                recency=100,
            ),
        }[weight]
        return (
            AttentionSignal(
                target_type=target_type,
                target_ref=target_ref,
                reason_refs=reason_refs,
                **features,
            ),
        )

    def controls(self, request: UpdateRequest, evidence: tuple[TransitionEvidence, ...]) -> ControlContext:
        context = request.context
        if context is None:
            return request.control_context
        rules = {item.rule_id for item in evidence}
        has_recoverable_gap = "curiosity.relevant-recoverable-gap.v1" in rules
        has_uncertainty = any(
            dimension.status == "unknown"
            or (dimension.dimension_key == "appraisal.certainty" and dimension.value == "low")
            for frame in request.appraisal_frames
            for dimension in frame.dimensions
        )
        inhibited_gap = "curiosity.unanswerable-gap-inhibited.v1" in rules
        repair = bool(
            rules
            & {
                "strain.repeated-misunderstanding.v1",
                "strain.explicit-boundary-mismatch.v1",
                "confidence.explicit-correction.v1",
            }
        )
        return ControlContext(
            disagreement=100
            if context.aggregate.scenario_disagreement != "none"
            or has_uncertainty
            else 0,
            consequence=80 if context.aggregate.high_consequence_uncertainty else 0,
            boundary_significance=100 if any("boundary" in rule or "prohibited" in rule for rule in rules) else 0,
            clarification_available=has_recoverable_gap,
            expected_information_gain=80 if has_recoverable_gap or inhibited_gap else 0,
            exploration_permitted=not any("prohibited" in rule for rule in rules),
            repair_evidence=100 if repair else 0,
            authorization=request.control_context.authorization,
            capability_available=request.control_context.capability_available,
            conversational_timing_ready=request.control_context.conversational_timing_ready,
        )

    def _select(self, state: StateValue, request: UpdateRequest) -> TransitionEvidence | None:
        outcome_meanings = {
            outcome.meaning: outcome
            for outcome in request.outcomes
            if self._outcome_is_usable(outcome)
        }

        outcome_rule = self._outcome_rule(state, outcome_meanings)
        if outcome_rule is not None:
            rule_id, direction, outcome = outcome_rule
            return self._make(
                state,
                rule_id,
                direction,
                SourceKind.OUTCOME,
                outcome_refs=(outcome.outcome_id,),
                evidence_refs=outcome.evidence_refs,
                resolves_branches=outcome.assessment_status in {"present", "absent"},
            )

        context = request.context
        if context is None:
            return None
        frames = self._frames_for(state, request.appraisal_frames)
        predicates = {frame.predicate for frame in context.semantic_frames}

        if request.elapsed_monotonic_ms > 0 and "observe_no_new_event" in predicates:
            decay_rules = {
                StateKey.ACTIVATION: ("decay.activation-toward-baseline.v1", Direction.DECREASE, StepBand.MODERATE),
                StateKey.CURIOSITY: ("decay.curiosity-persists-active-gap.v1", Direction.UNCHANGED, StepBand.NONE),
                StateKey.EPISTEMIC_CONFIDENCE: ("decay.confidence-evidence-bound.v1", Direction.UNCHANGED, StepBand.NONE),
            }
            if state.state_key in decay_rules:
                rule_id, direction, step = decay_rules[state.state_key]
                return self._make(state, rule_id, direction, SourceKind.ELAPSED_TIME, frames=frames, step=step, decay_trace=True)

        if not frames:
            return None
        values = self._dimension_values(frames)
        disagreement = context.aggregate.scenario_disagreement != "none"
        predicate = next(iter(predicates), "")
        if state.scope_type == ScopeType.GOAL and len(request.appraisal_frames) > 1:
            if "goal-explain" in state.scope_ref:
                predicate = "request_explanation"
            elif "goal-external" in state.scope_ref:
                predicate = "export_failed"

        if state.state_key == StateKey.VALENCE:
            congruence = values.get("appraisal.goal_congruence", set())
            if {"positive", "negative"}.issubset(congruence):
                return self._make(state, "valence.opposed-branches-provisional.v1", Direction.BRANCH_DEPENDENT, SourceKind.APPRAISAL, frames=frames, branches=tuple(f.frame_id for f in frames))
            if disagreement and congruence == {"negative"}:
                return self._make(state, "valence.disputed-branches-shared-negative.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            if "positive" in congruence:
                return self._make(state, "valence.positive-goal-congruence.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if "negative" in congruence:
                return self._make(state, "valence.negative-goal-congruence.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            if "neutral" in congruence:
                return self._make(state, "valence.neutral-congruence-no-effect.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)

        if state.state_key == StateKey.ACTIVATION:
            if "high" in values.get("appraisal.novelty", set()):
                rule = "activation.novel-uncertain-event.v1" if "low" in values.get("appraisal.certainty", set()) else "activation.novel-grounded-event.v1"
                return self._make(state, rule, Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if "high" in values.get("appraisal.goal_relevance", set()):
                return self._make(state, "activation.high-relevance.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.EPISTEMIC_CONFIDENCE:
            if predicate in {"observe_no_new_event", "correct_date"}:
                return None
            if state.status.value == "unknown" or any(d.status == "unknown" for f in frames for d in f.dimensions):
                return self._make(state, "confidence.unknown-appraisal-no-midpoint.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if context.aggregate.high_consequence_uncertainty and state.scope_type == ScopeType.ACTION_CANDIDATE:
                return self._make(state, "confidence.dangerous-alternative.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            semantic_status = self._target_semantic_status(state.scope_ref, context)
            if semantic_status == "verified":
                return self._make(state, "confidence.grounded-proposition.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if semantic_status == "uncertain":
                return self._make(state, "confidence.uncertain-proposition.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            if "low" in values.get("appraisal.certainty", set()):
                return self._make(state, "confidence.low-certainty.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            if "high" in values.get("appraisal.certainty", set()):
                return self._make(state, "confidence.high-certainty.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.CURIOSITY:
            target = self._object(state.scope_ref, context)
            if target and target.recoverability == "prohibited":
                return self._make(state, "curiosity.prohibited-gap-inhibited.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if "low" in values.get("appraisal.goal_relevance", set()) or self._is_deferred(state.scope_ref, context):
                return self._make(state, "curiosity.irrelevant-gap-no-effect.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if "low" in values.get("appraisal.controllability", set()):
                return self._make(state, "curiosity.unanswerable-gap-inhibited.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)
            if target and target.recoverability == "bounded":
                return self._make(state, "curiosity.relevant-recoverable-gap.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.ENGAGEMENT:
            if predicate == "propose_repair":
                return None
            if predicate == "continue_troubleshooting":
                return self._make(state, "engagement.viable-active-goal.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if predicate == "request_explanation":
                return self._make(state, "engagement.controllable-explanation.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if predicate == "export_ready":
                return self._make(state, "engagement.relevant-viable-progress.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.GOAL_PRESSURE:
            if predicate == "propose_repair":
                return self._make(state, "goal-pressure.no-outcome-persistence.v1", Direction.UNCHANGED, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if "negative" in values.get("appraisal.goal_congruence", set()):
                if predicate == "required_attachment_missing":
                    rule = "goal-pressure.important-unresolved.v1"
                elif predicate == "export_failed" and len(request.appraisal_frames) > 1:
                    rule = "goal-pressure.uncontrollable-external-goal.v1"
                else:
                    rule = "goal-pressure.unresolved-obstruction.v1"
                return self._make(state, rule, Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.SOCIAL_ALIGNMENT:
            if predicate == "confirm_understanding":
                return None
            affiliation = values.get("appraisal.affiliative_value", set())
            if predicate == "export_failed" and "repeated" in self._all_argument_values(context):
                return self._make(state, "alignment.task-failure-no-effect.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if predicate == "worker_restarted":
                return self._make(state, "alignment.neutral-task-event.v1", Direction.NO_CONTRIBUTION, SourceKind.APPRAISAL, frames=frames, step=StepBand.NONE)
            if "positive" in affiliation:
                return self._make(state, "alignment.explicit-positive-feedback.v1", Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
            if "negative" in affiliation:
                return self._make(state, "alignment.explicit-boundary-mismatch.v1", Direction.DECREASE, SourceKind.APPRAISAL, frames=frames)

        if state.state_key == StateKey.INTERACTION_STRAIN:
            if context.aggregate.high_consequence_uncertainty:
                rule = "strain.high-consequence-boundary.v1"
            elif predicate == "request_private_medical_records":
                rule = "strain.norm-boundary.v1"
            elif predicate == "reject_humor":
                rule = "strain.explicit-boundary-mismatch.v1"
            elif predicate == "export_failed" and "repeated" in self._all_argument_values(context):
                rule = "strain.repeated-technical-failure.v1"
            elif predicate == "correct_interpretation":
                rule = "strain.repeated-misunderstanding.v1"
            else:
                return None
            return self._make(state, rule, Direction.INCREASE, SourceKind.APPRAISAL, frames=frames)
        return None

    @staticmethod
    def _outcome_rule(state: StateValue, meanings: dict[str, object]):
        if "explicit_stop" in meanings and state.state_key == StateKey.ENGAGEMENT:
            return "engagement.verified-close.v1", Direction.DECREASE, meanings["explicit_stop"]
        if "verified_complete" in meanings and state.state_key == StateKey.GOAL_PRESSURE:
            return "goal-pressure.verified-completion.v1", Direction.DECREASE, meanings["verified_complete"]
        if "repair_confirmed" in meanings:
            outcome = meanings["repair_confirmed"]
            rules = {
                StateKey.INTERACTION_STRAIN: ("strain.verified-repair.v1", Direction.DECREASE),
                StateKey.SOCIAL_ALIGNMENT: ("alignment.verified-repair.v1", Direction.INCREASE),
                StateKey.GOAL_PRESSURE: ("goal-pressure.repair-not-completion.v1", Direction.UNCHANGED),
            }
            if state.state_key in rules:
                rule, direction = rules[state.state_key]
                return rule, direction, outcome
        if "Tuesday replaces Thursday" in meanings:
            outcome = meanings["Tuesday replaces Thursday"]
            if state.state_key == StateKey.EPISTEMIC_CONFIDENCE:
                return "confidence.explicit-correction.v1", Direction.DECREASE, outcome
            if state.state_key == StateKey.ENGAGEMENT:
                return "engagement.correction-no-direct-effect.v1", Direction.UNCHANGED, outcome
        if "observation_window_elapsed_without_semantic_evidence" in meanings:
            outcome = meanings["observation_window_elapsed_without_semantic_evidence"]
            rules = {
                StateKey.GOAL_PRESSURE: "goal-pressure.silence-no-success.v1",
                StateKey.ENGAGEMENT: "engagement.silence-unknown.v1",
            }
            if state.state_key in rules:
                return rules[state.state_key], Direction.UNCHANGED, outcome
        return None

    @staticmethod
    def _outcome_is_usable(outcome: object) -> bool:
        provenance = getattr(outcome, "provenance", "")
        status = getattr(outcome, "assessment_status", "unknown")
        meaning = getattr(outcome, "meaning", "")
        trusted = provenance in {
            "explicit_or_trusted",
            "explicit_user",
            "trusted_runtime",
            "reviewed",
        }
        if not trusted:
            return False
        if meaning == "observation_window_elapsed_without_semantic_evidence":
            return status == "unknown"
        if meaning == "explicit_stop":
            return status == "absent"
        return status == "present"

    @staticmethod
    def _frames_for(state: StateValue, frames: tuple[AppraisalFrame, ...]) -> tuple[AppraisalFrame, ...]:
        if state.scope_type == ScopeType.GOAL:
            return tuple(
                frame for frame in frames
                if frame.subject.focal_goal_ref == state.scope_ref
            )
        return frames

    @staticmethod
    def _dimension_values(frames: tuple[AppraisalFrame, ...]) -> dict[str, set[str]]:
        values: dict[str, set[str]] = {}
        for frame in frames:
            for dimension in frame.dimensions:
                if dimension.status == "known" and dimension.value is not None:
                    values.setdefault(dimension.dimension_key, set()).add(dimension.value)
        return values

    @staticmethod
    def _object(ref: str, context: DynamicStateContext) -> GroundedObject | None:
        return next((item for item in context.objects if item.object_ref == ref), None)

    @staticmethod
    def _all_argument_values(context: DynamicStateContext) -> set[str]:
        return {
            str(value) for frame in context.semantic_frames
            for value in frame.arguments.values()
        }

    @staticmethod
    def _is_deferred(ref: str, context: DynamicStateContext) -> bool:
        token = ref.removeprefix("unknown-").replace("-", "_")
        return any(
            str(frame.arguments.get("deferred_unknown", "")).startswith(token)
            for frame in context.semantic_frames
        )

    @staticmethod
    def _target_semantic_status(ref: str, context: DynamicStateContext) -> str | None:
        token = ref.removeprefix("proposition-").replace("-", "_")
        for frame in context.semantic_frames:
            for key, value in frame.arguments.items():
                if token in key:
                    return str(value)
        return None

    @staticmethod
    def _make(
        state: StateValue,
        rule_id: str,
        direction: Direction,
        source_kind: SourceKind,
        *,
        frames: tuple[AppraisalFrame, ...] = (),
        outcome_refs: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        step: StepBand = StepBand.MODERATE,
        branches: tuple[str, ...] = (),
        decay_trace: bool = False,
        resolves_branches: bool = False,
    ) -> TransitionEvidence:
        frame_refs = tuple(frame.frame_id for frame in frames)
        if not evidence_refs:
            evidence_refs = tuple(dict.fromkeys(ref for frame in frames for dimension in frame.dimensions for ref in dimension.evidence_refs))
        return TransitionEvidence(
            evidence_id=f"{rule_id}:{state.scope_type}:{state.scope_ref}",
            rule_id=rule_id,
            source_kind=source_kind,
            state_key=state.state_key,
            scope_type=state.scope_type,
            scope_ref=state.scope_ref,
            direction=direction,
            base_step_band=step,
            source_appraisal_frame_refs=frame_refs,
            source_outcome_refs=outcome_refs,
            source_dimension_keys=tuple(
                dict.fromkeys(d.dimension_key for frame in frames for d in frame.dimensions)
            ),
            evidence_refs=evidence_refs,
            branch_refs=branches,
            decay_trace=decay_trace,
            resolves_branches=resolves_branches,
        )

    @staticmethod
    def _attention_target(predicate: str, request: UpdateRequest) -> tuple[ScopeType, str, str]:
        context = request.context
        assert context is not None

        def first(kind: ScopeType, fallback: str | None = None) -> str:
            objects = context.objects_of_type(kind)
            if objects:
                return objects[0].object_ref
            if fallback is not None:
                return fallback
            return context.conversation_ref

        if predicate in {"request_private_medical_records", "offer_review_or_destructive_action"}:
            return ScopeType.ACTION_CANDIDATE, first(ScopeType.ACTION_CANDIDATE), "critical"
        if predicate == "correct_date":
            return ScopeType.PROPOSITION, first(ScopeType.PROPOSITION), "critical"
        if predicate == "service_result" and any(
            frame.arguments.get("verification") == "verified"
            for frame in context.semantic_frames
        ):
            return ScopeType.PROPOSITION, first(ScopeType.PROPOSITION), "high"
        if predicate == "prioritize_missing_information":
            target = next((item.object_ref for item in context.objects_of_type(ScopeType.UNKNOWN) if "recipient" in item.object_ref), first(ScopeType.UNKNOWN))
            return ScopeType.UNKNOWN, target, "high"
        mappings = {
            "unreadable_sign_content": ScopeType.TOPIC,
            "request_historical_weather": ScopeType.UNKNOWN,
            "continue_troubleshooting": ScopeType.GOAL,
            "required_attachment_missing": ScopeType.GOAL,
            "propose_repair": ScopeType.GOAL,
            "export_completed": ScopeType.GOAL,
            "approve_response_tone": ScopeType.PARTICIPANT,
            "reject_humor": ScopeType.PARTICIPANT,
            "report_artifact_metadata": ScopeType.PROPOSITION,
            "report_unknown_plan_effect": ScopeType.APPRAISAL_FRAME,
            "observe_no_user_response": ScopeType.GOAL,
        }
        if predicate == "export_failed" and (len(request.appraisal_frames) > 1 or "repeated" in TransitionEvidenceConstructor._all_argument_values(context)):
            return ScopeType.GOAL, first(ScopeType.GOAL), "moderate"
        if predicate in mappings:
            kind = mappings[predicate]
            if kind == ScopeType.APPRAISAL_FRAME:
                return kind, request.appraisal_frames[0].frame_id, "moderate"
            return kind, first(kind), "moderate"
        return ScopeType.CONVERSATION, context.conversation_ref, "moderate"
