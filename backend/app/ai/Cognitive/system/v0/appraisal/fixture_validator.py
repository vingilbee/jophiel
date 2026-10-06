"""Execute the reviewed appraisal design cases against the production engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .engine import AppraisalEngine
from .models import (
    ActiveGoal, AppraisalFrame, AppraisalRequest, CapabilitySnapshot, Contradiction,
    InterpretationEnvelope, InterpretationScenario, MemoryRecord, RelationshipSnapshot,
    RuntimeContext, SemanticFrame, SupplementalInput, UnknownInput, ValueRecord,
)
from ..contracts.interpretation import (
    HYPOTHESIS_DIMENSION_BY_KEY,
    InterpretationEvidence,
    InterpretationEvidenceKind,
    InterpretationHypothesis,
    InterpretationVersions,
)
from ..perception_v0.models import TextSpanEvidence
from ..goal_v0.ontology import definition_by_key

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "appraisal_design_cases.json"

def _canonical_fixture_goal(item: dict[str, Any]) -> ActiveGoal:
    """Translate the pre-Goal-V0 appraisal fixture shape at the boundary."""
    if "definition_ref" in item:
        return ActiveGoal.model_validate(item)
    desired = item.get("desired_state") or {}
    marker, key = next(((marker, candidate) for marker, candidate in (
        ("failure_explained", "interaction.explain_unavailable_or_failed_operation"),
        ("invoice_export_recovered", "task.complete_authorized_request"),
        ("export_completed", "task.complete_authorized_request"),
        ("user_has_explanation", "interaction.answer_information_request"),
    ) if desired.get(marker) is not None), ("answer_requested", "interaction.answer_information_request"))
    definition = definition_by_key(key)
    template = definition.requirement_templates[0]
    goal_id = item["goal_id"]
    return ActiveGoal(
        goal_id=goal_id,
        definition_ref={"definition_key": key, "definition_version": definition.definition_version},
        owner=item.get("owner", "columbina"), domain=definition.domain, mode=definition.mode,
        scope="conversation", scope_ref=goal_id, priority_band=item.get("priority_band", "moderate"),
        requirements=({"requirement_id": f"{goal_id}-requirement", "requirement_key": template.requirement_key,
                       "target_refs": ({"role": "legacy_target_ref", "ref": goal_id},),
                       "parameters": {"requested_effect": marker},
                       "satisfaction_policy_key": template.satisfaction_policy_key},),
        source_refs=tuple(item.get("source_refs", ())),
    )


class FixtureValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    fixture_path: str
    cases_run: int
    frames_checked: int
    reappraisal_steps_run: int
    assertions: int
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _evidence_record(item: dict[str, Any], runtime_snapshot_id: str) -> InterpretationEvidence:
    if item["evidence_type"] == "text_span":
        return InterpretationEvidence(
            evidence_id=item["evidence_id"], source_kind=InterpretationEvidenceKind.MESSAGE_SPAN,
            source_ref=item["evidence_id"], message_id=item["message_id"],
            span=TextSpanEvidence(
                message_id=item["message_id"], start=item["start"], end=item["end"],
                quoted_text=item["quoted_text"],
            ),
        )
    return InterpretationEvidence(
        evidence_id=item["evidence_id"], source_kind=InterpretationEvidenceKind.RUNTIME_FACT,
        source_ref=item["evidence_id"], runtime_snapshot_ref=runtime_snapshot_id,
        json_pointer=item["json_pointer"], value=item.get("value"),
    )


def _request(
    case: dict[str, Any], *, supplemental: tuple[SupplementalInput, ...] = (),
    previous: tuple[AppraisalFrame, ...] = (), expected_frames: list[dict[str, Any]] | None = None,
) -> AppraisalRequest:
    interpretation = case["interpretation_input"]
    runtime_snapshot_id = f"runtime:{case['case_id']}"
    evidence = tuple(
        _evidence_record(item, runtime_snapshot_id)
        for item in interpretation["evidence_records"]
    )
    evidence_ids = tuple(item.evidence_id for item in evidence)
    hypotheses = tuple(InterpretationHypothesis(
        hypothesis_id=item["hypothesis_id"], hypothesis_key=item["hypothesis_key"],
        definition_version=1, dimension=HYPOTHESIS_DIMENSION_BY_KEY[item["hypothesis_key"]],
        base_support=item["support"], support=item["support"], disposition=item["disposition"],
        semantic_frame_refs=tuple(frame["frame_id"] for frame in interpretation["semantic_frames"]),
        supporting_evidence_refs=tuple(item.get("supporting_evidence_refs", ())),
        contradicting_evidence_refs=tuple(item.get("contradicting_evidence_refs", ())),
        rule_ids=("fixture.migration.v1",),
    ) for group in interpretation["hypotheses"].values() for item in group)
    scenarios = tuple(InterpretationScenario(
        scenario_id=item["scenario_id"], support=item["support"], disposition=item["disposition"],
        semantic_frame_refs=tuple(item["semantic_frame_refs"]),
        hypothesis_refs={
            {
                "communicative_acts": "communicative_act",
                "speaker_goals": "speaker_goal",
                "modalities": "modality",
                "pragmatic_meanings": "pragmatic_meaning",
            }.get(key, key): tuple(value)
            for key, value in item["hypothesis_refs"].items()
        },
        supporting_evidence_refs=tuple(item.get("supporting_evidence_refs", ())),
        contradiction_refs=tuple(item.get("contradiction_refs", ())),
        unknown_refs=tuple(item.get("unknown_refs", ())),
        compatibility_rule_ids=tuple(item.get("compatibility_rule_ids", ())),
    ) for item in case["expected_scenarios"])
    envelope = InterpretationEnvelope(
        interpretation_trace_id=interpretation["interpretation_trace_id"],
        cognitive_turn_id=case["case_id"], conversation_ref=f"fixture:{case['case_id']}",
        perception_event_id=f"perception:{case['case_id']}",
        input_message_ids=tuple(dict.fromkeys(item.message_id for item in evidence if item.message_id)),
        focus_message_id=next(item.message_id for item in reversed(evidence) if item.message_id),
        runtime_snapshot_id=runtime_snapshot_id, evidence=evidence,
        semantic_frames=tuple(SemanticFrame(
            frame_id=item["frame_id"], predicate=item["predicate"], arguments=item.get("arguments", {}),
            polarity=item.get("polarity", "positive"),
            evidence_refs=tuple(item.get("source_spans", ())) or evidence_ids[:1],
            unresolved_arguments=tuple(item.get("unresolved_arguments", ())),
            grounding_rule_ids=("fixture.migration.v1",),
        ) for item in interpretation["semantic_frames"]),
        hypotheses=hypotheses, scenarios=scenarios,
        unknowns=tuple(UnknownInput(owner="unknown", **item) for item in interpretation.get("unknowns", ())),
        contradictions=tuple(Contradiction(
            evidence_refs=evidence_ids[:1], **item,
        ) for item in interpretation.get("contradictions", ())),
        versions=InterpretationVersions(
            schema="interpretation-envelope-v0.1", ontology="fixture-ontology-v1",
            feature_manifest="fixture-features-v1", semantic_grounder="fixture-grounder-v1",
            hypothesis_rules="fixture-rules-v1", scenario_builder="interpretation-scenarios-v0.1",
            bias_policy="fixture-bias-v1", evidence_validator="fixture-evidence-v1",
            perception_schema="fixture-perception-v1", perception_cue_manifest="fixture-cues-v1",
            perception_detectors="fixture-detectors-v1",
        ),
    )
    runtime_raw = case["runtime_context"]
    capability = CapabilitySnapshot(**runtime_raw["capability_snapshot"])
    runtime = RuntimeContext(capability_snapshot=capability, **{
        key: value for key, value in runtime_raw.items() if key != "capability_snapshot"
    })
    expected_frames = expected_frames if expected_frames is not None else case["expected_appraisal_frames"]
    overrides = {
        f"{item['scenario_id']}|{item['subject']['focal_goal_ref']}|r{item['revision']}": item["frame_id"]
        for item in expected_frames
    }
    return AppraisalRequest(
        cognitive_turn_id=case["case_id"], interpretation=envelope,
        active_goals=tuple(_canonical_fixture_goal(item) for item in case["active_goals"]),
        runtime=runtime, memories=tuple(MemoryRecord(**item) for item in case["retrieved_memories"]),
        values=tuple(ValueRecord(**item) for item in case["value_context"]),
        relationship=(RelationshipSnapshot(**case["relationship_context"]) if case["relationship_context"] else None),
        supplemental_inputs=supplemental, previous_frames=previous, frame_id_overrides=overrides,
    )


def _check_frames(label: str, actual: tuple[AppraisalFrame, ...], expected: list[dict[str, Any]], errors: list[str]) -> int:
    assertions = 1
    if len(actual) != len(expected):
        errors.append(f"{label}: produced {len(actual)} frames, expected {len(expected)}")
    by_id = {item.frame_id: item for item in actual}
    for wanted in expected:
        frame = by_id.get(wanted["frame_id"])
        assertions += 5
        if frame is None:
            errors.append(f"{label}: missing frame {wanted['frame_id']}")
            continue
        for field in ("revision", "supersedes_frame_id", "scenario_id", "scenario_support", "completeness"):
            if getattr(frame, field) != wanted[field]:
                errors.append(f"{label}/{frame.frame_id}: {field}={getattr(frame, field)!r}, expected {wanted[field]!r}")
        expected_dimensions = wanted["expected_dimensions"]
        for key, dimension_wanted in expected_dimensions.items():
            assertions += 4
            dimension = frame.dimension(key)
            for field in ("status", "value", "rule_id"):
                actual_value = getattr(dimension, field)
                if hasattr(actual_value, "value"):
                    actual_value = actual_value.value
                if actual_value != dimension_wanted[field]:
                    errors.append(f"{label}/{frame.frame_id}/{key}: {field}={actual_value!r}, expected {dimension_wanted[field]!r}")
            if tuple(dimension.conflict_refs) != tuple(dimension_wanted["conflict_refs"]):
                errors.append(f"{label}/{frame.frame_id}/{key}: conflict refs differ")
    return assertions


def _check_aggregate(label: str, actual: object, expected: dict[str, Any], errors: list[str]) -> int:
    assertions = 6
    dumped = actual.model_dump(mode="json")  # type: ignore[attr-defined]
    if dumped != expected:
        for key in expected:
            if dumped[key] != expected[key]:
                errors.append(f"{label}/aggregate: {key}={dumped[key]!r}, expected {expected[key]!r}")
    return assertions


def validate_fixture(path: Path = DEFAULT_FIXTURE, *, raise_on_error: bool = False) -> FixtureValidationReport:
    document = json.loads(path.read_text(encoding="utf-8"))
    engine, errors = AppraisalEngine(), []
    assertions = frames_checked = steps_run = 0
    if document.get("status") != "draft_for_human_review":
        errors.append("fixture status must remain draft_for_human_review")
    for case in document["cases"]:
        label = case["case_id"]
        result = engine.appraise(_request(case))
        assertions += _check_frames(label, result.frames, case["expected_appraisal_frames"], errors)
        frames_checked += len(case["expected_appraisal_frames"])
        final_result = result
        supplemental: list[SupplementalInput] = []
        previous = result.frames
        for step in case.get("reappraisal_steps", ()):
            steps_run += 1
            supplemental.extend(SupplementalInput(**item) for item in step["new_inputs"])
            final_result = engine.appraise(_request(
                case, supplemental=tuple(supplemental), previous=previous,
                expected_frames=step["expected_appraisal_frames"],
            ))
            assertions += _check_frames(
                f"{label}/step-{step['step']}", final_result.frames,
                step["expected_appraisal_frames"], errors,
            )
            frames_checked += len(step["expected_appraisal_frames"])
            previous = final_result.frames
        assertions += _check_aggregate(label, final_result.aggregate, case["expected_aggregate"], errors)
    report = FixtureValidationReport(
        fixture_path=str(path), cases_run=len(document["cases"]), frames_checked=frames_checked,
        reappraisal_steps_run=steps_run, assertions=assertions, errors=tuple(errors),
    )
    if raise_on_error and errors:
        raise AssertionError("\n".join(errors))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    report = validate_fixture(args.path)
    print(report.model_dump_json(indent=2))
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
