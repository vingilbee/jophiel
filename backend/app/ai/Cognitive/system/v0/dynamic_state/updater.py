"""Deterministic, replayable dynamic-state transition engine."""

from __future__ import annotations

from collections import defaultdict

from .construction import TransitionEvidenceConstructor
from .models import (
    AttentionItem,
    AttentionSignal,
    Contribution,
    DerivedControls,
    Direction,
    PersonalityControl,
    QualitativeBand,
    StateKey,
    StateSnapshot,
    StateValue,
    UpdateRequest,
    UpdateResult,
    ValueStatus,
)
from .policy import (
    POLICY_VERSION,
    RECOVERY_RULE_IDS,
    RECOVERY_RATIOS,
    RULE_MAGNITUDE_RATIOS,
    MAXIMUM_STEPS,
    STEP_SCORES,
    attention_band,
    band_for_score,
    bounded_step,
    clamp,
    decayed_score,
    unsigned_control_band,
)
from .rules import DEFAULT_RULE_REGISTRY, TransitionRuleRegistry


class DynamicStateUpdater:
    def __init__(
        self,
        registry: TransitionRuleRegistry = DEFAULT_RULE_REGISTRY,
        constructor: TransitionEvidenceConstructor | None = None,
    ) -> None:
        self.registry = registry
        self.constructor = constructor or TransitionEvidenceConstructor()

    def update(self, request: UpdateRequest) -> UpdateResult:
        previous = request.previous_snapshot
        if previous.conversation_ref != request.conversation_ref:
            raise ValueError("previous state belongs to a different conversation")
        if previous.personality_mapper_version != request.personality.mapper_version:
            raise ValueError("previous state personality mapper does not match the supplied profile")
        if previous.policy_version not in {POLICY_VERSION, "dynamic-state-rules-v0.1-draft"}:
            raise ValueError(f"unsupported previous policy: {previous.policy_version}")

        seeded_values = self.constructor.seed_values(request)
        working_previous = previous.model_copy(
            update={"values": (*previous.values, *seeded_values)}
        )
        construction_request = request.model_copy(
            update={"previous_snapshot": working_previous}
        )
        prior_by_scope = {
            (v.state_key, v.scope_type, v.scope_ref): v
            for v in working_previous.values
        }
        constructed = self.registry.construct(construction_request, self.constructor)
        all_evidence = (*constructed, *request.evidence)
        identities = [(item.rule_id, item.state_key, item.scope_type, item.scope_ref) for item in all_evidence]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate constructed/explicit transition evidence")
        generated_attention = self.constructor.attention(construction_request)
        attention_signals = (*generated_attention, *request.attention_signals)
        effective_request = request.model_copy(
            update={"control_context": self.constructor.controls(construction_request, all_evidence)}
        )
        contributions: list[Contribution] = []
        grouped: dict[tuple[StateKey, object, str], list[Contribution]] = defaultdict(list)
        appraisal_refs: set[str] = set()
        outcome_refs: set[str] = set()

        for evidence in all_evidence:
            self.registry.resolve(evidence)
            identity = (evidence.state_key, evidence.scope_type, evidence.scope_ref)
            prior = prior_by_scope.get(identity)
            control = self._control_for(evidence.state_key, effective_request, fallback=prior.baseline_score if prior else 0)
            magnitude = bounded_step(evidence.base_step_band, control.sensitivity, control.maximum_step_band)
            ratio_num, ratio_den = RULE_MAGNITUDE_RATIOS.get(evidence.rule_id, (1, 1))
            magnitude = (magnitude * ratio_num + ratio_den // 2) // ratio_den
            if prior is not None and prior.score is not None:
                recovering = (
                    evidence.direction == Direction.INCREASE and prior.score < control.baseline_score
                ) or (
                    evidence.direction == Direction.DECREASE and prior.score > control.baseline_score
                )
                if recovering and evidence.rule_id in RECOVERY_RULE_IDS:
                    recovery_num, recovery_den = RECOVERY_RATIOS[control.recovery_band]
                    magnitude = min(
                        MAXIMUM_STEPS[control.maximum_step_band],
                        (magnitude * recovery_num + recovery_den // 2) // recovery_den,
                    )
            contribution = Contribution(
                contribution_id=evidence.evidence_id,
                rule_id=evidence.rule_id,
                state_key=evidence.state_key,
                scope_type=evidence.scope_type,
                scope_ref=evidence.scope_ref,
                direction=evidence.direction,
                base_step_band=evidence.base_step_band,
                base_step=STEP_SCORES[evidence.base_step_band],
                bounded_step=magnitude,
                source_kind=evidence.source_kind,
                source_appraisal_frame_refs=evidence.source_appraisal_frame_refs,
                source_outcome_refs=evidence.source_outcome_refs,
                source_dimension_keys=evidence.source_dimension_keys,
                evidence_refs=evidence.evidence_refs,
                inhibition_refs=evidence.inhibition_refs,
                branch_refs=evidence.branch_refs,
                rationale=evidence.rationale,
            )
            contributions.append(contribution)
            grouped[(evidence.state_key, evidence.scope_type, evidence.scope_ref)].append(contribution)
            appraisal_refs.update(evidence.source_appraisal_frame_refs)
            outcome_refs.update(evidence.source_outcome_refs)

        values: list[StateValue] = []
        all_scopes = set(prior_by_scope) | set(grouped)
        evidence_by_id = {e.evidence_id: e for e in all_evidence}
        for identity in sorted(all_scopes, key=lambda item: tuple(str(part) for part in item)):
            state_key, scope_type, scope_ref = identity
            prior = prior_by_scope.get(identity)
            if prior is None:
                control = self._control_for(state_key, effective_request)
                prior = StateValue(
                    state_key=state_key,
                    scope_type=scope_type,
                    scope_ref=scope_ref,
                    score=control.baseline_score,
                    band=band_for_score(state_key, control.baseline_score),
                    baseline_score=control.baseline_score,
                )
            control = self._control_for(state_key, effective_request, fallback=prior.baseline_score)
            scope_contributions = grouped.get(identity, [])
            values.append(
                self._apply_scope(
                    prior,
                    scope_contributions,
                    evidence_by_id,
                    request.elapsed_monotonic_ms,
                    control,
                )
            )

        attention = self._derive_attention(attention_signals)
        controls = self._derive_controls(tuple(values), tuple(contributions), effective_request, attention)
        snapshot = StateSnapshot(
            state_snapshot_id=f"{request.cognitive_turn_id}:state:r{previous.revision + 1}",
            cognitive_turn_id=request.cognitive_turn_id,
            conversation_ref=request.conversation_ref,
            revision=previous.revision + 1,
            supersedes_snapshot_id=previous.state_snapshot_id,
            policy_version=POLICY_VERSION,
            personality_mapper_version=request.personality.mapper_version,
            source_appraisal_revision_ids=tuple(sorted(appraisal_refs)),
            source_outcome_ids=tuple(sorted(outcome_refs)),
            values=tuple(values),
            attention=attention,
            derived_controls=controls,
        )
        return UpdateResult(snapshot=snapshot, contributions=tuple(contributions))

    @staticmethod
    def _control_for(
        state_key: StateKey, request: UpdateRequest, fallback: int = 0
    ) -> PersonalityControl:
        return request.personality.state_controls.get(state_key) or PersonalityControl(baseline_score=fallback)

    @staticmethod
    def _apply_scope(
        prior: StateValue,
        contributions: list[Contribution],
        evidence_by_id: dict[str, object],
        elapsed_ms: int,
        control: PersonalityControl,
    ) -> StateValue:
        if prior.status in {ValueStatus.UNKNOWN, ValueStatus.NOT_APPLICABLE}:
            return prior.model_copy(
                update={"rule_contribution_refs": tuple(c.contribution_id for c in contributions)}
            )

        branch_contributions = [c for c in contributions if c.direction == Direction.BRANCH_DEPENDENT]
        directional = {c.direction for c in contributions if c.direction in {Direction.INCREASE, Direction.DECREASE}}
        if branch_contributions or directional == {Direction.INCREASE, Direction.DECREASE}:
            branches = tuple(dict.fromkeys(ref for c in contributions for ref in c.branch_refs))
            if len(branches) < 2:
                branches = tuple(c.contribution_id for c in contributions[:2])
            magnitude = max((c.bounded_step for c in contributions), default=0)
            if prior.status == ValueStatus.PROVISIONAL:
                assert prior.possible_score_range is not None
                lower, upper = prior.possible_score_range
            else:
                assert prior.score is not None
                lower = upper = prior.score
            return prior.model_copy(
                update={
                    "status": ValueStatus.PROVISIONAL,
                    "score": None,
                    "band": None,
                    "possible_score_range": (
                        clamp(prior.state_key, lower - magnitude),
                        clamp(prior.state_key, upper + magnitude),
                    ),
                    "branch_refs": branches,
                    "rule_contribution_refs": tuple(c.contribution_id for c in contributions),
                }
            )

        if prior.status == ValueStatus.PROVISIONAL:
            assert prior.possible_score_range is not None
            lower, upper = prior.possible_score_range
            resolving = [
                c
                for c in contributions
                if c.direction in {Direction.INCREASE, Direction.DECREASE}
                and getattr(evidence_by_id[c.contribution_id], "resolves_branches", False)
            ]
            if resolving:
                directions = {c.direction for c in resolving}
                if len(directions) != 1:
                    raise ValueError("resolving evidence has incompatible directions")
                direction = next(iter(directions))
                magnitude = min(
                    MAXIMUM_STEPS[control.maximum_step_band],
                    sum(c.bounded_step for c in resolving),
                )
                anchor = upper if direction == Direction.INCREASE else lower
                score = clamp(
                    prior.state_key,
                    anchor + (magnitude if direction == Direction.INCREASE else -magnitude),
                )
                new_refs = tuple(
                    dict.fromkeys(
                        (*prior.evidence_refs, *(ref for c in resolving for ref in c.evidence_refs))
                    )
                )
                return prior.model_copy(
                    update={
                        "status": ValueStatus.KNOWN,
                        "score": score,
                        "band": band_for_score(prior.state_key, score),
                        "possible_score_range": None,
                        "branch_refs": (),
                        "evidence_refs": new_refs,
                        "rule_contribution_refs": tuple(c.contribution_id for c in contributions),
                    }
                )
            delta = sum(
                c.bounded_step if c.direction == Direction.INCREASE else -c.bounded_step
                for c in contributions
                if c.direction in {Direction.INCREASE, Direction.DECREASE}
                and not getattr(evidence_by_id[c.contribution_id], "decay_trace", False)
            )
            maximum = MAXIMUM_STEPS[control.maximum_step_band]
            delta = max(-maximum, min(maximum, delta))
            return prior.model_copy(
                update={
                    "possible_score_range": (
                        clamp(prior.state_key, lower + delta),
                        clamp(prior.state_key, upper + delta),
                    ),
                    "rule_contribution_refs": tuple(c.contribution_id for c in contributions),
                }
            )

        assert prior.score is not None
        decayed = decayed_score(
            prior.state_key,
            prior.score,
            control.baseline_score,
            elapsed_ms,
            control.persistence_band,
            active=prior.active,
            learnable=prior.learnable,
        )
        delta = 0
        for contribution in contributions:
            evidence = evidence_by_id[contribution.contribution_id]
            if getattr(evidence, "decay_trace", False):
                continue
            if contribution.direction == Direction.INCREASE:
                delta += contribution.bounded_step
            elif contribution.direction == Direction.DECREASE:
                delta -= contribution.bounded_step
        maximum = MAXIMUM_STEPS[control.maximum_step_band]
        if maximum:
            delta = max(-maximum, min(maximum, delta))
        score = clamp(prior.state_key, decayed + delta)
        effective = [
            c
            for c in contributions
            if c.direction in {Direction.INCREASE, Direction.DECREASE}
            and not getattr(evidence_by_id[c.contribution_id], "decay_trace", False)
        ]
        new_refs = tuple(
            dict.fromkeys(
                (*prior.evidence_refs, *(ref for c in effective for ref in c.evidence_refs))
            )
        )
        return prior.model_copy(
            update={
                "score": score,
                "band": band_for_score(prior.state_key, score),
                "baseline_score": control.baseline_score,
                "evidence_refs": new_refs,
                "rule_contribution_refs": tuple(c.contribution_id for c in contributions),
            }
        )

    @staticmethod
    def _derive_attention(signals: tuple[AttentionSignal, ...]) -> tuple[AttentionItem, ...]:
        items = []
        for signal in signals:
            weight = min(
                100,
                (
                    signal.goal_relevance * 20
                    + signal.goal_priority * 15
                    + signal.novelty * 10
                    + signal.uncertainty * 15
                    + signal.dispute * 20
                    + signal.boundary_significance * 25
                    + signal.unresolved_pressure * 15
                    + signal.recency * 10
                    + 50
                )
                // 100,
            )
            items.append(
                AttentionItem(
                    target_type=signal.target_type,
                    target_ref=signal.target_ref,
                    weight=weight,
                    weight_band=attention_band(weight),
                    reason_refs=signal.reason_refs,
                )
            )
        return tuple(sorted(items, key=lambda item: (-item.weight, item.target_type, item.target_ref)))

    @staticmethod
    def _derive_controls(
        values: tuple[StateValue, ...],
        contributions: tuple[Contribution, ...],
        request: UpdateRequest,
        attention: tuple[AttentionItem, ...],
    ) -> DerivedControls:
        context = request.control_context
        known = {value.state_key: value.score for value in values if value.status == ValueStatus.KNOWN}
        confidence_values = [v.score for v in values if v.state_key == StateKey.EPISTEMIC_CONFIDENCE and v.score is not None]
        confidence = min(confidence_values, default=50)
        curiosity = max((v.score or 0 for v in values if v.state_key == StateKey.CURIOSITY), default=0)
        strain = max((v.score or 0 for v in values if v.state_key == StateKey.INTERACTION_STRAIN), default=0)
        pressure = max((v.score or 0 for v in values if v.state_key == StateKey.GOAL_PRESSURE), default=0)
        engagement = max((v.score or 0 for v in values if v.state_key == StateKey.ENGAGEMENT), default=known.get(StateKey.ENGAGEMENT, 50) or 0)
        activation = known.get(StateKey.ACTIVATION, 50) or 0
        provisional = any(v.status in {ValueStatus.UNKNOWN, ValueStatus.PROVISIONAL} for v in values)

        slow = min(89, (100 - confidence) // 2 + context.disagreement // 2 + context.consequence // 3 + context.boundary_significance // 2 + strain // 4 + (25 if provisional else 0))
        clarification = min(89, (100 - confidence) // 2 + curiosity // 2 + (20 if context.clarification_available else 0))
        repair = min(89, strain * 2 // 3 + context.repair_evidence * 2 // 3)
        exploration = min(59, curiosity // 2 + context.expected_information_gain // 2) if context.exploration_permitted else 0
        initiative = (pressure + engagement + confidence) // 3
        if not (context.authorization and context.capability_available and context.conversational_timing_ready):
            initiative = min(initiative, 55)
        patience_adjustment = {"low": 25, "moderate": 0, "high": -25}.get(request.personality.interaction_patience_band, 0)
        initiative = max(0, min(100, initiative + patience_adjustment))
        # V0 keeps realization energy conservative unless a later caller
        # supplies an explicit style policy; state alone cannot force intensity.
        response_energy = max(40, min(59, (activation + engagement) // 2))
        return DerivedControls(
            slow_cognition_pressure=unsigned_control_band(slow),
            initiative_readiness=unsigned_control_band(initiative),
            clarification_pressure=unsigned_control_band(clarification),
            repair_pressure=unsigned_control_band(repair),
            exploration_readiness=unsigned_control_band(exploration),
            response_energy=unsigned_control_band(response_energy),
        )
