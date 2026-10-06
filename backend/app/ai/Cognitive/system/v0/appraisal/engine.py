"""Deterministic orchestration for V0 appraisal frames and aggregates."""

from __future__ import annotations

import hashlib

from .models import (
    ActiveGoal, AppraisalAggregate, AppraisalFrame, AppraisalRequest, AppraisalResult,
    AppraisalSubject, AppraisalDimension, DimensionStatus, InterpretationScenario,
)
from .rules import RULES, RuleContext


class AppraisalEngine:
    """Derive one immutable frame for every retained scenario/focal-goal pair."""

    def appraise(self, request: AppraisalRequest) -> AppraisalResult:
        frames = tuple(
            self._frame(request, scenario, goal)
            for scenario in request.interpretation.scenarios
            if scenario.disposition in {"leading", "retained_alternative"}
            for goal in request.active_goals
            if goal.status == "active"
        )
        aggregate = self._aggregate(request, frames)
        digest = hashlib.sha256(
            f"{request.cognitive_turn_id}:{request.interpretation.interpretation_trace_id}".encode()
        ).hexdigest()[:20]
        result = AppraisalResult(
            appraisal_trace_id=f"appraisal-{digest}", cognitive_turn_id=request.cognitive_turn_id,
            interpretation_trace_id=request.interpretation.interpretation_trace_id,
            frames=frames, aggregate=aggregate,
            versions={
                "schema": "appraisal-schema-v1", "dimension_registry": "appraisal-dimensions-v1",
                "scenario_builder": request.interpretation.versions.scenario_builder,
                "rules": "appraisal-rules-v0.2",
            },
        )
        self._validate_result(request, result)
        return result

    @staticmethod
    def _validate_result(request: AppraisalRequest, result: AppraisalResult) -> None:
        retained = {
            item.scenario_id: item for item in request.interpretation.scenarios
            if item.disposition in {"leading", "retained_alternative"}
        }
        semantic_ids = {item.frame_id for item in request.interpretation.semantic_frames}
        goal_ids = {item.goal_id for item in request.active_goals if item.status == "active"}
        value_ids = {item.value_id for item in request.values}
        supplemental_ids = {item.input_id for item in request.supplemental_inputs}
        memory_ids = {item.memory_id for item in request.memories}
        evidence_ids = {item.evidence_id for item in request.interpretation.evidence}
        unknown_ids = {item.unknown_id for item in request.interpretation.unknowns}
        contradiction_ids = {
            item.contradiction_id for item in request.interpretation.contradictions
        }
        capability_id = request.runtime.capability_snapshot.snapshot_id
        relationship_id = (
            request.relationship.relationship_snapshot_id if request.relationship else None
        )
        frame_ids: list[str] = []
        for frame in result.frames:
            frame_ids.append(frame.frame_id)
            scenario = retained.get(frame.scenario_id)
            if scenario is None:
                raise ValueError("appraisal frame cites a non-retained interpretation scenario")
            if not set(frame.subject.event_refs) <= set(scenario.semantic_frame_refs):
                raise ValueError("appraisal subject event does not belong to its scenario")
            if frame.subject.focal_goal_ref not in goal_ids:
                raise ValueError("appraisal subject cites an inactive or unknown goal")
            if not set(frame.subject.value_refs) <= value_ids:
                raise ValueError("appraisal subject cites an unknown value")
            if frame.subject.capability_snapshot_ref != capability_id:
                raise ValueError("appraisal subject cites the wrong capability snapshot")
            if frame.subject.relationship_snapshot_ref != relationship_id:
                raise ValueError("appraisal subject cites the wrong relationship snapshot")
            allowed = (
                set(scenario.semantic_frame_refs) | {scenario.scenario_id, frame.subject.focal_goal_ref}
                | evidence_ids | supplemental_ids | memory_ids | value_ids | unknown_ids
                | contradiction_ids | {capability_id} | ({relationship_id} if relationship_id else set())
            )
            if result.model_trace is not None:
                model_context = result.model_trace.payload.context
                allowed |= {item["ref"] for item in model_context.dialogue_context}
                allowed |= {trait.source_snapshot_ref for trait in model_context.traits if trait.source_snapshot_ref}
                if model_context.dynamic_state_snapshot_ref:
                    allowed.add(model_context.dynamic_state_snapshot_ref)
            for dimension in frame.dimensions:
                refs = (
                    set(dimension.evidence_refs) | set(dimension.required_input_refs)
                    | set(dimension.conflict_refs)
                )
                if not refs <= allowed:
                    raise ValueError("appraisal dimension contains an unresolved reference")
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("appraisal emitted duplicate frame ids")
        if not set(result.aggregate.dominant_frame_ids) <= set(frame_ids):
            raise ValueError("appraisal aggregate cites an unknown dominant frame")

    def _frame(
        self, request: AppraisalRequest, scenario: InterpretationScenario, goal: ActiveGoal,
        dimensions: tuple[AppraisalDimension, ...] | None = None,
    ) -> AppraisalFrame:
        semantic = {item.frame_id: item for item in request.interpretation.semantic_frames}
        context = RuleContext(request, scenario, goal, tuple(semantic[ref] for ref in scenario.semantic_frame_refs))
        history = [
            item for item in request.previous_frames
            if item.scenario_id == scenario.scenario_id
            and item.subject.focal_goal_ref == goal.goal_id
        ]
        previous = max(history, key=lambda item: item.revision, default=None)
        revision = previous.revision + 1 if previous else 1
        override_key = f"{scenario.scenario_id}|{goal.goal_id}|r{revision}"
        identity = (
            f"{request.cognitive_turn_id}|{request.interpretation.interpretation_trace_id}|"
            f"{override_key}"
        )
        frame_id = request.frame_id_overrides.get(
            override_key,
            f"appraisal-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
        )
        if dimensions is None:
            dimensions = tuple(rule(context) for rule in RULES)
        unresolved = self._unresolved_inputs(context)
        unresolved_dimension = any(
            item.status in {DimensionStatus.UNKNOWN, DimensionStatus.DISPUTED}
            for item in dimensions
        )
        completeness = "complete" if not unresolved and not unresolved_dimension else "partial"
        return AppraisalFrame(
            frame_id=frame_id, revision=revision,
            supersedes_frame_id=previous.frame_id if previous else None,
            scenario_id=scenario.scenario_id, scenario_support=scenario.support,
            subject=AppraisalSubject(
                event_refs=scenario.semantic_frame_refs, focal_goal_ref=goal.goal_id,
                goal_owner=goal.owner, goal_priority_band=goal.priority_band,
                value_refs=tuple(item.value_id for item in request.values if item.status == "active"),
                capability_snapshot_ref=request.runtime.capability_snapshot.snapshot_id,
                relationship_snapshot_ref=(request.relationship.relationship_snapshot_id if request.relationship else None),
            ),
            dimensions=dimensions, missing_inputs=tuple(unresolved),
            conflicts=tuple(item.contradiction_id for item in context.contradictions),
            completeness=completeness,
        )

    @staticmethod
    def _unresolved_inputs(context: RuleContext) -> list[str]:
        resolved: set[str] = set()
        for item in context.supplemental_inputs:
            resolved.update(item.resolved_arguments)
            if item.authorization == "confirmed":
                resolved.add("authorization")
            if item.input_type == "observed_outcome" and item.outcome:
                resolved.add("goal_congruence")
        unknown_by_id = {item.unknown_id: item for item in context.request.interpretation.unknowns}
        return [
            ref for ref in context.scenario.unknown_refs
            if unknown_by_id[ref].key not in resolved
        ]

    def _aggregate(self, request: AppraisalRequest, frames: tuple[AppraisalFrame, ...]) -> AppraisalAggregate:
        leading_scenarios = {
            item.scenario_id for item in request.interpretation.scenarios if item.disposition == "leading"
        }
        dominant = [
            item.frame_id for item in frames if item.scenario_id in leading_scenarios
        ]

        ranges: dict[str, tuple[str, ...]] = {}
        for key in (
            "appraisal.goal_relevance", "appraisal.goal_congruence", "appraisal.novelty",
            "appraisal.certainty", "appraisal.controllability", "appraisal.norm_compatibility",
            "appraisal.affiliative_value",
        ):
            values = tuple(dict.fromkeys(
                dimension.value
                for frame in frames
                for dimension in (frame.dimension(key),)
                if dimension.status == DimensionStatus.KNOWN and dimension.value is not None
            ))
            if len(values) > 1:
                ranges[key] = values

        scenario_count = len({item.scenario_id for item in frames})
        disagreement = "none"
        if scenario_count > 1:
            dangerous = any(
                frame.dimension("appraisal.norm_compatibility").value == "negative"
                for frame in frames
            )
            disagreement = "high" if dangerous else "moderate"

        goals = {item.subject.focal_goal_ref for item in frames}
        congruence = {
            item.dimension("appraisal.goal_congruence").value for item in frames
            if item.dimension("appraisal.goal_congruence").status == DimensionStatus.KNOWN
        }
        goal_conflict = len(goals) > 1 and len(congruence) > 1
        referenced_missing = [ref for frame in frames for ref in frame.missing_inputs]
        framed_scenarios = {frame.scenario_id for frame in frames}
        referenced_missing.extend(
            ref for scenario in request.interpretation.scenarios
            if scenario.scenario_id not in framed_scenarios
            for ref in scenario.unknown_refs
        )
        referenced = {ref for scenario in request.interpretation.scenarios for ref in scenario.unknown_refs}
        unscoped_missing = [item.unknown_id for item in request.interpretation.unknowns if item.unknown_id not in referenced]
        declared_unknown_keys = {item.key for item in request.interpretation.unknowns}
        undeclared_missing = [
            key for frame in frames for dimension in frame.dimensions
            for key in dimension.missing_input_keys if key not in declared_unknown_keys
        ]
        missing = tuple(dict.fromkeys((*referenced_missing, *unscoped_missing, *undeclared_missing)))
        high_consequence = self._high_consequence_uncertainty(frames)
        return AppraisalAggregate(
            dominant_frame_ids=tuple(dominant), dimension_ranges=ranges,
            scenario_disagreement=disagreement, goal_conflict=goal_conflict,
            high_consequence_uncertainty=high_consequence, missing_required_inputs=missing,
        )

    @staticmethod
    def _high_consequence_uncertainty(frames: tuple[AppraisalFrame, ...]) -> bool:
        """Preserve plausible harmful branches instead of averaging them away."""
        for goal_ref in {item.subject.focal_goal_ref for item in frames}:
            alternatives = [item for item in frames if item.subject.focal_goal_ref == goal_ref]
            if len({item.scenario_id for item in alternatives}) < 2:
                continue
            negative_norm = any(
                item.dimension("appraisal.norm_compatibility").value == "negative"
                for item in alternatives
            )
            materially_different = any(
                len({item.dimension(key).value for item in alternatives}) > 1
                for key in (
                    "appraisal.goal_congruence", "appraisal.controllability",
                    "appraisal.norm_compatibility", "appraisal.affiliative_value",
                )
            )
            authorization_uncertain = any(
                any(token in missing for token in ("authorization", "consent", "ownership"))
                for item in alternatives for missing in item.missing_inputs
            )
            if negative_norm and (materially_different or authorization_uncertain):
                return True
        return False
