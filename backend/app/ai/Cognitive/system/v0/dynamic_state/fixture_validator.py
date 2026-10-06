"""Independently execute the dynamic-state design fixture from case inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .models import (
    AppraisalAggregate,
    AppraisalDimension,
    AppraisalFrame,
    DynamicStateContext,
    GroundedObject,
    OutcomeObservation,
    PersonalityControl,
    PersonalityProfile,
    ScopeType,
    SemanticFrame,
    StateKey,
    StateSnapshot,
    StateValue,
    UpdateRequest,
    UpdateResult,
    ValueStatus,
)
from ..appraisal.models import AppraisalSubject
from ..appraisal.rules import DIMENSION_TYPES
from .policy import band_for_score, score_for_band
from .updater import DynamicStateUpdater

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "dynamic_state_design_cases.json"
ELAPSED_MS = {"none": 0, "short": 30_000, "medium": 300_000, "long": 3_600_000}


class FixtureValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    fixture_path: str
    cases_run: int
    profiles_run: int
    transition_steps_run: int
    assertions: int
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _profile(raw: dict[str, Any]) -> PersonalityProfile:
    controls = {}
    for key, item in raw["state_controls"].items():
        state_key = StateKey(key)
        controls[state_key] = PersonalityControl(
            baseline_score=score_for_band(state_key, item["baseline_band"]),
            persistence_band=item["persistence_band"],
            recovery_band=item["recovery_band"],
            sensitivity=item["sensitivity"],
            maximum_step_band=item["maximum_step_band"],
        )
    return PersonalityProfile(
        profile_id=raw["profile_id"], mapper_version=raw["mapper_version"],
        interaction_patience_band=raw.get("interaction_patience_band", "moderate"),
        state_controls=controls,
    )


def _previous_snapshot(case: dict[str, Any], profile: PersonalityProfile) -> StateSnapshot:
    values = []
    for raw in case["previous_state"]["values"]:
        state_key = StateKey(raw["state_key"])
        status = ValueStatus(raw["status"])
        control = profile.state_controls.get(state_key)
        baseline = control.baseline_score if control else score_for_band(state_key, raw["baseline_band"])
        score = score_for_band(state_key, raw["band"]) if raw.get("band") is not None else None
        values.append(StateValue(
            state_key=state_key, scope_type=ScopeType(raw["scope_type"]), scope_ref=raw["scope_ref"],
            status=status, score=score, band=raw.get("band"), baseline_score=baseline,
            evidence_refs=tuple(raw.get("evidence_refs", ())),
            missing_input_refs=tuple(raw.get("missing_input_refs", ())),
            branch_refs=tuple(raw.get("branch_refs", ())),
            possible_score_range=tuple(raw["possible_score_range"]) if raw.get("possible_score_range") else None,
            active=raw.get("lifecycle_status", "active") == "active", learnable=True,
        ))
    raw_snapshot = case["previous_state"]
    return StateSnapshot(
        state_snapshot_id=raw_snapshot["snapshot_id"], cognitive_turn_id=f"{case['case_id']}:before",
        conversation_ref=case["conversation"]["conversation_id"],
        revision=raw_snapshot["revision"], policy_version=raw_snapshot["policy_version"],
        personality_mapper_version=profile.mapper_version, values=tuple(values),
    )


def _appraisal_frames(case: dict[str, Any]) -> tuple[AppraisalFrame, ...]:
    frames = []
    goals = {item["goal_id"]: item for item in case["cognitive_objects"].get("goals", ())}
    semantic_refs = tuple(
        item["semantic_frame_id"]
        for item in case["cognitive_objects"].get("semantic_frames", ())
    )
    for raw in case["appraisal_input"]["frames"]:
        dimensions = tuple(AppraisalDimension(
            dimension_key=key, status=value["status"], value_type=DIMENSION_TYPES[key],
            value=value.get("value"),
            derivation_strength=value.get("derivation_strength"),
            evidence_refs=tuple(value.get("evidence_refs", ())),
            missing_input_keys=tuple(value.get("missing_input_refs", ())),
            conflict_refs=tuple(value.get("conflict_refs", ())),
        ) for key, value in raw["dimensions"].items())
        goal = goals.get(raw["focal_goal_ref"], {})
        frames.append(AppraisalFrame(
            frame_id=raw["frame_id"], revision=raw["revision"], scenario_id=raw["scenario_id"],
            scenario_support=raw["scenario_support"],
            subject=AppraisalSubject(
                event_refs=semantic_refs, focal_goal_ref=raw["focal_goal_ref"],
                goal_owner=goal.get("owner", "unknown"),
                goal_priority_band=raw["goal_priority_band"], value_refs=(),
                capability_snapshot_ref="dynamic-state-fixture",
            ),
            dimensions=dimensions,
            missing_inputs=tuple(raw.get("missing_inputs", ())),
            conflicts=tuple(raw.get("conflicts", ())),
            completeness=raw.get("completeness", "complete"),
        ))
    return tuple(frames)


def _context(case: dict[str, Any]) -> DynamicStateContext:
    objects: list[GroundedObject] = []
    collections = {
        "unknowns": (ScopeType.UNKNOWN, "unknown_id"), "goals": (ScopeType.GOAL, "goal_id"),
        "propositions": (ScopeType.PROPOSITION, "proposition_id"), "topics": (ScopeType.TOPIC, "topic_id"),
        "participants": (ScopeType.PARTICIPANT, "participant_id"),
        "action_candidates": (ScopeType.ACTION_CANDIDATE, "candidate_id"),
        "action_plans": (ScopeType.ACTION_PLAN, "action_plan_id"),
        "hypotheses": (ScopeType.HYPOTHESIS, "hypothesis_id"),
    }
    cognitive = case["cognitive_objects"]
    for collection, (object_type, id_key) in collections.items():
        for raw in cognitive.get(collection, ()):
            objects.append(GroundedObject(
                object_type=object_type, object_ref=raw[id_key], status=raw.get("status", "active"),
                recoverability=raw.get("recoverability"), priority_band=raw.get("priority_band"),
                gate_status=raw.get("gate_status"), evidence_refs=tuple(raw.get("evidence_refs", ())),
            ))
    semantic = tuple(SemanticFrame(
        frame_id=raw["semantic_frame_id"], predicate=raw["predicate"],
        arguments=raw.get("arguments", {}), polarity=raw.get("polarity", "positive"),
        evidence_refs=tuple(raw.get("source_span_refs", ())) or (f"fixture:{raw['semantic_frame_id']}",),
        unresolved_arguments=tuple(raw.get("unresolved_arguments", ())),
        grounding_rule_ids=("fixture.migration.v1",),
    ) for raw in cognitive.get("semantic_frames", ()))
    aggregate = case["appraisal_input"]["aggregate"]
    return DynamicStateContext(
        conversation_ref=case["conversation"]["conversation_id"], semantic_frames=semantic,
        objects=tuple(objects), aggregate=AppraisalAggregate(
            dominant_frame_ids=tuple(aggregate.get("dominant_frame_ids", ())),
            dimension_ranges={
                key: tuple(values)
                for key, values in aggregate.get("dimension_ranges", {}).items()
            },
            scenario_disagreement=aggregate.get("scenario_disagreement", "none"),
            goal_conflict=aggregate.get("goal_conflict", False),
            high_consequence_uncertainty=aggregate.get("high_consequence_uncertainty", False),
            missing_required_inputs=tuple(aggregate.get("missing_required_inputs", ())),
        ),
    )


def _outcomes(case: dict[str, Any]) -> tuple[OutcomeObservation, ...]:
    return tuple(OutcomeObservation(
        outcome_id=raw["outcome_id"], dimension=raw["dimension"],
        assessment_status=raw["assessment_status"], meaning=raw["meaning"],
        evidence_refs=tuple(raw.get("evidence_refs", ())), provenance=raw["provenance"],
    ) for raw in case["cognitive_objects"].get("outcomes", ()))


def _assert_snapshot(label: str, result: UpdateResult, expected_state: dict[str, Any], errors: list[str]) -> int:
    assertions = 0
    actual_values = {(v.state_key.value, v.scope_type.value, v.scope_ref): v for v in result.snapshot.values}
    for wanted in expected_state["values"]:
        assertions += 2
        identity = (wanted["state_key"], wanted["scope_type"], wanted["scope_ref"])
        actual = actual_values.get(identity)
        if actual is None:
            errors.append(f"{label}: missing state value {identity}")
            continue
        if actual.status.value != wanted["status"]:
            errors.append(f"{label}: {identity} status {actual.status.value}, expected {wanted['status']}")
        permitted = wanted["expected_resulting_bands"]
        if permitted and actual.status == ValueStatus.PROVISIONAL:
            assert actual.possible_score_range is not None
            possible = {band_for_score(actual.state_key, score).value for score in actual.possible_score_range}
            if not set(permitted).issubset(possible):
                errors.append(f"{label}: {identity} provisional bands {possible}, expected {permitted}")
        elif permitted and (actual.band is None or actual.band.value not in permitted):
            errors.append(f"{label}: {identity} band {actual.band}, expected one of {permitted}")
        elif not permitted and actual.band is not None:
            errors.append(f"{label}: {identity} imputed forbidden band {actual.band}")
    return assertions


def _assert_final(label: str, result: UpdateResult, expected: dict[str, Any], errors: list[str]) -> int:
    assertions = 1 + _assert_snapshot(label, result, expected["expected_state"], errors)
    if result.snapshot.revision != expected["expected_state"]["snapshot_revision"]:
        errors.append(
            f"{label}: revision {result.snapshot.revision}, expected "
            f"{expected['expected_state']['snapshot_revision']}"
        )
    actual_contributions = {(item.rule_id, item.state_key.value, item.scope_type.value, item.scope_ref, item.direction.value) for item in result.contributions}
    for wanted in expected["expected_contributions"]:
        assertions += 1
        identity = (wanted["rule_id"], wanted["state_key"], wanted["scope_type"], wanted["scope_ref"], wanted["direction"])
        if identity not in actual_contributions:
            errors.append(f"{label}: missing constructed contribution {identity}")
    actual_attention = {(a.target_type.value, a.target_ref): a for a in result.snapshot.attention}
    for wanted in expected["expected_state"]["attention"]:
        assertions += 1
        actual = actual_attention.get((wanted["target_type"], wanted["target_ref"]))
        if actual is None or actual.weight_band != wanted["weight_band"]:
            errors.append(f"{label}: attention mismatch for {wanted['target_ref']}")
    controls = result.snapshot.derived_controls
    assert controls is not None
    for wanted in expected["expected_state"]["derived_controls"]:
        assertions += 1
        actual = getattr(controls, wanted["control_key"]).value
        if actual != wanted["band"]:
            errors.append(f"{label}: {wanted['control_key']}={actual}, expected {wanted['band']}")
    return assertions


def _run_case_profile(case: dict[str, Any], profile: PersonalityProfile, updater: DynamicStateUpdater, errors: list[str]) -> tuple[UpdateResult, int, int]:
    previous = _previous_snapshot(case, profile)
    frames, outcomes, context = _appraisal_frames(case), _outcomes(case), _context(case)
    assertions = steps_run = 0
    steps = case.get("transition_steps", ())
    if steps:
        result: UpdateResult | None = None
        for step in steps:
            steps_run += 1
            trigger = step["trigger_type"]
            result = updater.update(UpdateRequest(
                cognitive_turn_id=f"{case['case_id']}:step-{step['step']}", previous_snapshot=previous,
                conversation_ref=case["conversation"]["conversation_id"],
                personality=profile,
                elapsed_monotonic_ms=ELAPSED_MS[case["elapsed_time"]["duration_band"]] if trigger == "elapsed_time" else 0,
                appraisal_frames=frames if trigger in {"appraisal", "reappraisal"} else (),
                outcomes=outcomes if trigger in {"observed_outcome", "explicit_correction", "elapsed_time"} else (),
                context=context,
            ))
            if result.snapshot.revision != previous.revision + 1:
                errors.append(f"{case['case_id']}/{profile.profile_id}/step-{step['step']}: non-sequential revision")
            if result.snapshot.supersedes_snapshot_id != previous.state_snapshot_id:
                errors.append(f"{case['case_id']}/{profile.profile_id}/step-{step['step']}: broken supersession chain")
            assertions += 2 + _assert_snapshot(f"{case['case_id']}/{profile.profile_id}/step-{step['step']}", result, step["expected_snapshot"], errors)
            previous = result.snapshot
        assert result is not None
        return result, assertions, steps_run
    result = updater.update(UpdateRequest(
        cognitive_turn_id=case["case_id"], previous_snapshot=previous, personality=profile,
        conversation_ref=case["conversation"]["conversation_id"],
        elapsed_monotonic_ms=ELAPSED_MS[case["elapsed_time"]["duration_band"]],
        appraisal_frames=frames, outcomes=outcomes, context=context,
    ))
    return result, assertions, steps_run


def validate_fixture(path: Path = DEFAULT_FIXTURE, *, raise_on_error: bool = False) -> FixtureValidationReport:
    document = json.loads(path.read_text())
    errors: list[str] = []
    assertions = profiles_run = transition_steps_run = 0
    updater = DynamicStateUpdater()
    if len(document["cases"]) != 30:
        errors.append(f"fixture has {len(document['cases'])} cases; expected 30")
    case_ids = [case["case_id"] for case in document["cases"]]
    if len(case_ids) != len(set(case_ids)):
        errors.append("fixture contains duplicate case ids")
    for case in document["cases"]:
        expected_by_profile = {item["profile_id"]: item for item in case["expected_by_profile"]}
        for raw_profile in case["personality_profiles"]:
            profile = _profile(raw_profile)
            profiles_run += 1
            expected = expected_by_profile[profile.profile_id]
            label = f"{case['case_id']}/{expected['profile_id']}"
            try:
                result, step_assertions, steps = _run_case_profile(case, profile, updater, errors)
                assertions += step_assertions + _assert_final(label, result, expected, errors)
                transition_steps_run += steps
            except (AssertionError, KeyError, TypeError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
    report = FixtureValidationReport(
        fixture_path=str(path), cases_run=len(document["cases"]), profiles_run=profiles_run,
        transition_steps_run=transition_steps_run, assertions=assertions, errors=tuple(errors),
    )
    if raise_on_error and errors:
        raise AssertionError("\n".join(errors))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    report = validate_fixture(args.fixture)
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
