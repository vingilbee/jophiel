"""Coherent, non-Cartesian scenario construction."""

from __future__ import annotations

from ..contracts.interpretation import (
    HYPOTHESIS_CONFLICTS,
    HYPOTHESIS_COMPATIBILITIES,
    HypothesisDimension,
    HypothesisDisposition,
    HypothesisSupport,
    InterpretationHypothesis,
    InterpretationScenario,
    ScenarioDisposition,
    ScenarioSupport,
    SemanticFrame,
    UnknownInput,
)
from ..perception_v0.evidence import stable_id

# v0.2 records ``scenario.compositional-acts.v1`` when the leading scenario
# holds several communicative acts grounded in distinct frames.
SCENARIO_BUILDER_VERSION = "interpretation-scenarios-v0.2"
COMPOSITIONAL_ACTS_RULE = "scenario.compositional-acts.v1"
_LEVELS = tuple(HypothesisSupport)


class ScenarioBuilder:
    def build(
        self,
        *,
        cognitive_turn_id: str,
        perception_event_id: str,
        frames: tuple[SemanticFrame, ...],
        hypotheses: tuple[InterpretationHypothesis, ...],
        unknowns: tuple[UnknownInput, ...],
        contradiction_ids: tuple[str, ...],
    ) -> tuple[InterpretationScenario, ...]:
        leading = tuple(
            item for item in hypotheses
            if item.disposition == HypothesisDisposition.LEADING
            and item.support != HypothesisSupport.UNSUPPORTED
        )
        alternatives = tuple(
            item for item in hypotheses
            if item.disposition == HypothesisDisposition.RETAINED_ALTERNATIVE
            and item.support != HypothesisSupport.UNSUPPORTED
        )
        if not leading:
            raise ValueError("scenario construction requires a leading hypothesis")

        def group(items: tuple[InterpretationHypothesis, ...]) -> dict[str, tuple[str, ...]]:
            return {
                dimension.value: tuple(item.hypothesis_id for item in items if item.dimension == dimension)
                for dimension in HypothesisDimension
                if any(item.dimension == dimension for item in items)
            }

        def support(items: tuple[InterpretationHypothesis, ...]) -> ScenarioSupport:
            retained = [item.support for item in items if item.support != HypothesisSupport.UNSUPPORTED]
            return min(retained, key=_LEVELS.index) if retained else ScenarioSupport.WEAK

        frame_refs = tuple(frame.frame_id for frame in frames)
        evidence_refs = tuple(dict.fromkeys(
            ref for item in leading for ref in item.supporting_evidence_refs
        ))
        primary = InterpretationScenario(
            scenario_id=stable_id(
                "interpretation-scenario", cognitive_turn_id, perception_event_id,
                frame_refs, tuple(item.hypothesis_id for item in leading), "leading",
            ),
            support=support(leading),
            disposition=ScenarioDisposition.LEADING,
            semantic_frame_refs=frame_refs,
            hypothesis_refs=group(leading),
            supporting_evidence_refs=evidence_refs,
            contradiction_refs=contradiction_ids,
            unknown_refs=tuple(item.unknown_id for item in unknowns),
            compatibility_rule_ids=(
                ("scenario.leading-compatible-dimensions.v1", COMPOSITIONAL_ACTS_RULE)
                if len({
                    item.semantic_frame_refs for item in leading
                    if item.dimension == HypothesisDimension.COMMUNICATIVE_ACT
                }) > 1
                else ("scenario.leading-compatible-dimensions.v1",)
            ),
        )
        scenarios = [primary]
        remaining = list(alternatives)
        alternative_groups: list[tuple[InterpretationHypothesis, ...]] = []
        while remaining:
            component = [remaining.pop(0)]
            changed = True
            while changed:
                changed = False
                group_keys = {item.hypothesis_key for item in component}
                for candidate in tuple(remaining):
                    candidate_compatible = set(
                        HYPOTHESIS_COMPATIBILITIES.get(candidate.hypothesis_key, ())
                    )
                    if candidate_compatible & group_keys or any(
                        candidate.hypothesis_key
                        in HYPOTHESIS_COMPATIBILITIES.get(item.hypothesis_key, ())
                        for item in component
                    ):
                        component.append(candidate)
                        remaining.remove(candidate)
                        changed = True
            alternative_groups.append(tuple(component))

        for alternative_group in alternative_groups:
            alternative_dimensions = {item.dimension for item in alternative_group}
            conflicts = {
                conflict
                for item in alternative_group
                for conflict in HYPOTHESIS_CONFLICTS.get(item.hypothesis_key, ())
            }
            branch = (*alternative_group, *(
                item for item in leading
                if item.dimension not in alternative_dimensions
                and item.hypothesis_key not in conflicts
            ))
            scenarios.append(InterpretationScenario(
                scenario_id=stable_id(
                    "interpretation-scenario", cognitive_turn_id, perception_event_id,
                    frame_refs, tuple(item.hypothesis_id for item in branch),
                    tuple(item.hypothesis_key for item in alternative_group),
                ),
                support=support(branch),
                disposition=ScenarioDisposition.RETAINED_ALTERNATIVE,
                semantic_frame_refs=frame_refs,
                hypothesis_refs=group(branch),
                supporting_evidence_refs=tuple(dict.fromkeys(
                    ref for item in branch for ref in item.supporting_evidence_refs
                )),
                contradiction_refs=contradiction_ids,
                unknown_refs=tuple(item.unknown_id for item in unknowns),
                compatibility_rule_ids=("scenario.conflict-separated-alternative.v1",),
            ))
        high_consequence = any(
            frame.predicate == "delete_database" and "authorization" in frame.unresolved_arguments
            for frame in frames
        )
        if high_consequence and len(scenarios) == 1:
            scenarios.append(InterpretationScenario(
                scenario_id=stable_id(
                    "interpretation-scenario", cognitive_turn_id, perception_event_id,
                    frame_refs, "high-consequence-unresolved-authorization",
                ),
                support=ScenarioSupport.MODERATE,
                disposition=ScenarioDisposition.RETAINED_ALTERNATIVE,
                semantic_frame_refs=frame_refs,
                hypothesis_refs=group(leading),
                supporting_evidence_refs=evidence_refs,
                contradiction_refs=contradiction_ids,
                unknown_refs=tuple(item.unknown_id for item in unknowns),
                compatibility_rule_ids=("scenario.high-consequence-alternative-retention.v1",),
            ))
        return tuple(scenarios)
