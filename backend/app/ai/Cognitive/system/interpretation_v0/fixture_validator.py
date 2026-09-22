"""Structural and live-runtime validation for interpretation design cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import model_validator

from ..contracts.interpretation import (
    HYPOTHESIS_DIMENSION_BY_KEY,
    FrozenDict,
    FrozenModel,
    HypothesisDisposition,
    HypothesisSupport,
)
from ..perception_v0 import (
    AttachmentFact,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    ToolEventFact,
    perceive,
)
from ..perception_v0.evidence import resolve_json_pointer
from ..perception_v0.manifest import CUE_MANIFEST
from .engine import InterpretationEngine
from .models import (
    InterpretationBiasProfile,
    InterpretationMemory,
    InterpretationRequest,
    InterpretationRuntimeContext,
    TaskContract,
)
from .ontology import PREDICATES, PREDICATE_REGISTRY_VERSION

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "interpretation_design_cases.json"
_APPRAISAL_SHAPES = {
    "appraisal.goal_relevance": ("expected_band", {"low", "moderate", "high", "unknown"}),
    "appraisal.novelty": ("expected_band", {"low", "moderate", "high", "unknown"}),
    "appraisal.certainty": ("expected_band", {"low", "moderate", "high", "unknown"}),
    "appraisal.controllability": ("expected_band", {"low", "moderate", "high", "unknown"}),
    "appraisal.goal_congruence": ("expected_tone", {"negative", "neutral", "positive", "unknown"}),
    "appraisal.norm_compatibility": ("expected_tone", {"negative", "neutral", "positive", "unknown"}),
    "appraisal.affiliative_value": ("expected_tone", {"negative", "neutral", "positive", "unknown"}),
    "appraisal.agency": ("expected_category", {"user", "columbina", "third_party", "environment", "unknown"}),
}
_STATE_KEYS = {
    "valence", "activation", "epistemic_confidence", "curiosity", "engagement",
    "goal_pressure", "social_alignment", "interaction_strain",
}
_STATE_DIRECTIONS = {
    "increase", "decrease", "unchanged", "increase_or_unchanged",
    "decrease_or_unchanged", "unknown",
}
_ROUTES = {"fast", "slow", "clarify", "abstain_or_escalate"}


class RuntimeEligibility(StrEnum):
    LIVE_V0_ELIGIBLE = "live_v0_eligible"
    ONTOLOGY_ONLY_SHADOW_DEPENDENCY = "ontology_only_shadow_dependency"
    BLOCKED_BY_UNIMPLEMENTED_EXTERNAL_CONTEXT = "blocked_by_unimplemented_external_context"


class InterpretationFixtureReport(FrozenModel):
    fixture_path: str
    fixture_status: str
    cases_validated: int
    cases_executed: int
    expectations_checked: int
    eligibility_by_case: dict[str, RuntimeEligibility]
    eligibility_counts: dict[str, int]
    ontology_coverage: dict[str, int]
    predicate_coverage: dict[str, int]
    deferred_cases: tuple[str, ...]
    ontology_questions: tuple[str, ...]
    errors: tuple[str, ...]

    @model_validator(mode="after")
    def freeze_mappings(self) -> InterpretationFixtureReport:
        for name in (
            "eligibility_by_case", "eligibility_counts", "ontology_coverage", "predicate_coverage",
        ):
            object.__setattr__(self, name, FrozenDict(getattr(self, name)))
        return self

    @property
    def passed(self) -> bool:
        return not self.errors


def _eligibility(case: dict[str, Any]) -> RuntimeEligibility:
    expected_keys = {
        item["hypothesis_key"]
        for values in case["base_hypothesis_expectations"].values()
        for item in values
    }
    if "interpretation.pragmatic.tone_situation_mismatch" in expected_keys:
        return RuntimeEligibility.ONTOLOGY_ONLY_SHADOW_DEPENDENCY
    return RuntimeEligibility.LIVE_V0_ELIGIBLE


def _facts(case: dict[str, Any]) -> tuple[
    tuple[AttachmentFact, ...], ToolEventFact | None, TaskContract | None
]:
    raw = case["runtime_context"]
    attachments = tuple(
        AttachmentFact(
            attachment_id=item["attachment_id"], filename=item.get("filename"),
            trusted_runtime_provenance="interpretation-design-fixture",
            evidence_reference=f"{case['case_id']}:/attachments/{index}",
        )
        for index, item in enumerate(raw.get("attachments", ()))
    )
    tool = None
    if raw.get("last_tool_event") is not None:
        event = raw["last_tool_event"]
        tool = ToolEventFact(
            tool=event["tool"], status=event["status"], trusted_runtime=True,
            authorized=True, event_reference=f"{case['case_id']}:/last_tool_event",
            error_code=event.get("error_code"),
        )
    task = TaskContract.model_validate(raw["task_contract"]) if raw.get("task_contract") else None
    return attachments, tool, task


def _neutral_bias(case: dict[str, Any]) -> InterpretationBiasProfile:
    scenario = next(
        (item for item in case["bias_scenarios"] if item["profile_id"] == "neutral"),
        case["bias_scenarios"][0],
    )
    return InterpretationBiasProfile(
        profile_id=scenario["profile_id"],
        compiler_version="interpretation-fixture-bias-compiler-v1",
        source_core_memory_ref=f"fixture-core:{scenario['profile_id']}",
        **scenario["compiled_biases"],
    )


def _runtime_case(case: dict[str, Any]):
    messages = tuple(PerceptionMessage.model_validate(item) for item in case["messages"])
    attachments, tool, task = _facts(case)
    snapshot_id = f"fixture-runtime:{case['case_id']}"
    perception = perceive(PerceptionRequest(
        cognitive_turn_id=f"fixture:{case['case_id']}", messages=messages,
        focus_message_id=case["focus_message_id"],
        runtime=PerceptionRuntimeContext(
            snapshot_id=snapshot_id, attachments=attachments,
            available_capabilities=tuple(case["runtime_context"].get("available_capabilities", ())),
            last_tool_event=tool,
        ),
    ))
    memories = tuple(
        InterpretationMemory(**item) for item in case.get("retrieved_memories", ())
    )
    request = InterpretationRequest(
        cognitive_turn_id=perception.cognitive_turn_id,
        conversation_ref=f"fixture-conversation:{case['case_id']}",
        perception=perception.interpretation_handoff(), messages=messages,
        runtime=InterpretationRuntimeContext(
            snapshot_id=snapshot_id,
            predicate_registry_version=PREDICATE_REGISTRY_VERSION,
            known_predicates=PREDICATES,
            attachments=attachments,
            available_capabilities=tuple(case["runtime_context"].get("available_capabilities", ())),
            task_contract=task,
            authorization=case["runtime_context"].get("authorization"),
            safety_decision=case["runtime_context"].get("safety_decision"),
            last_tool_event=tool,
        ),
        memories=memories, biases=_neutral_bias(case),
    )
    return perception, request


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value))


def validate_fixture(path: str | Path = DEFAULT_FIXTURE) -> InterpretationFixtureReport:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    questions: list[str] = []
    cases = raw.get("cases", [])
    if raw.get("status") != "draft_for_human_review":
        errors.append("interpretation fixture must remain draft_for_human_review")
    if raw.get("evaluation_scope") != "ontology_design":
        errors.append("interpretation fixture must retain ontology_design scope")
    ids = [case.get("case_id") for case in cases]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("interpretation fixture case ids must be non-empty and unique")

    eligibility_by_case: dict[str, RuntimeEligibility] = {}
    ontology_coverage: Counter[str] = Counter()
    predicate_coverage: Counter[str] = Counter()
    appraisal_coverage: Counter[str] = Counter()
    route_coverage: Counter[str] = Counter()
    expectations_checked = cases_executed = 0
    deferred: list[str] = []
    for case in cases:
        case_id = case["case_id"]
        eligibility = _eligibility(case)
        eligibility_by_case[case_id] = eligibility
        try:
            message_ids = [item["message_id"] for item in case["messages"]]
            if len(message_ids) != len(set(message_ids)) or case["focus_message_id"] != message_ids[-1]:
                raise ValueError("message ids must be unique and focus must be final")
            messages = {item["message_id"]: item["content"] for item in case["messages"]}
            cue_keys = {item["cue_key"] for item in case["perception_input"]["cue_activations"]}
            semantic_predicates = {
                item["predicate"] for item in case["semantic_frame_expectations"]
            }
            memory_ids = {item["memory_id"] for item in case.get("retrieved_memories", ())}

            def validate_design_evidence(item: dict[str, Any]) -> None:
                kind = item["evidence_type"]
                if kind == "text_span":
                    if messages[item["message_id"]][item["start"]:item["end"]] != item["quoted_text"]:
                        raise ValueError("evidence text span does not match source")
                elif kind == "cue_activation":
                    if item["cue_key"] not in cue_keys:
                        raise ValueError("evidence references an unavailable fixture cue")
                elif kind == "semantic_frame":
                    if item["predicate"] not in semantic_predicates:
                        raise ValueError("evidence references an unavailable semantic predicate")
                elif kind == "runtime_fact":
                    if resolve_json_pointer(case["runtime_context"], item["json_pointer"]) != item["value"]:
                        raise ValueError("fixture runtime JSON Pointer does not resolve")
                elif kind == "memory_record":
                    if item["memory_id"] not in memory_ids:
                        raise ValueError("evidence references an unavailable memory")
                else:
                    raise ValueError(f"unsupported design evidence type {kind}")

            accepted_cues = {item.cue_key for item in CUE_MANIFEST}
            if not cue_keys <= accepted_cues:
                raise ValueError(f"unknown cue keys: {sorted(cue_keys - accepted_cues)}")
            for activation in case["perception_input"]["cue_activations"]:
                state, value = activation["state"], activation["value"]
                if state in {"present", "absent"} and type(value) not in {bool, int}:
                    raise ValueError(f"invalid cue value for {activation['cue_key']}")
                for item in activation.get("evidence", ()):
                    if item["evidence_type"] == "text_span":
                        if messages[item["message_id"]][item["start"]:item["end"]] != item["quoted_text"]:
                            raise ValueError("fixture text span does not match source")
            for frame in case["semantic_frame_expectations"]:
                predicate_coverage[frame["predicate"]] += 1
                for item in frame.get("evidence", ()):
                    if item["evidence_type"] == "text_span" and messages[item["message_id"]][item["start"]:item["end"]] != item["quoted_text"]:
                        raise ValueError("semantic-frame text span does not match source")
            for dimension, values in case["base_hypothesis_expectations"].items():
                for item in values:
                    key = item["hypothesis_key"]
                    if key not in HYPOTHESIS_DIMENSION_BY_KEY:
                        raise ValueError(f"unknown hypothesis key {key}")
                    HypothesisSupport(item["support"])
                    HypothesisDisposition(item["disposition"])
                    ontology_coverage[key] += 1
                    for evidence_item in (*item.get("supporting_evidence", ()), *item.get("contradicting_evidence", ())):
                        validate_design_evidence(evidence_item)
            for scenario in case["bias_scenarios"]:
                for value in scenario["compiled_biases"].values():
                    if not 0 <= value <= 1:
                        raise ValueError("fixture bias value is outside [0, 1]")
                for adjustment in scenario.get("expected_adjustments", ()):
                    if adjustment["hypothesis_key"] not in HYPOTHESIS_DIMENSION_BY_KEY:
                        raise ValueError("bias adjustment references an unknown hypothesis")
                    if adjustment["direction"] not in {"increase", "decrease", "unchanged"}:
                        raise ValueError("invalid bias-adjustment direction")
                    if adjustment["maximum_ordinal_shift"] not in {0, 1}:
                        raise ValueError("fixture bias shift exceeds the V0 bound")
            for item in case["appraisal_expectations"]:
                key = item["appraisal_key"]
                if key not in _APPRAISAL_SHAPES:
                    raise ValueError(f"unknown appraisal key {key}")
                field, allowed = _APPRAISAL_SHAPES[key]
                if item.get(field) not in allowed:
                    raise ValueError(f"invalid {field} for {key}")
                appraisal_coverage[key] += 1
            for item in case["dynamic_state_expectations"]:
                if item["state_key"] not in _STATE_KEYS:
                    raise ValueError(f"unknown dynamic-state key {item['state_key']}")
                if item["expected_direction"] not in _STATE_DIRECTIONS:
                    raise ValueError("invalid dynamic-state direction")
            route = case["routing_expectation"]["path"]
            if route not in _ROUTES:
                raise ValueError(f"invalid routing path {route}")
            route_coverage[route] += 1
            if case.get("review", {}).get("status") != "pending":
                raise ValueError("fixture cases must remain pending human review")
            for item in case.get("post_interpretation_expectations", ()):
                for evidence_item in item.get("evidence", ()):
                    validate_design_evidence(evidence_item)

            perception, request = _runtime_case(case)
            handoff_keys = {item.cue_key for item in perception.interpretation_handoff().cue_activations}
            shadow_keys = {
                "conversation.topic_shift", "linguistic.positive_wording",
                "linguistic.negative_wording", "task.urgency",
            }
            if handoff_keys & shadow_keys:
                raise ValueError("shadow cue entered the live interpretation handoff")
            if eligibility == RuntimeEligibility.ONTOLOGY_ONLY_SHADOW_DEPENDENCY:
                deferred.append(case_id)
                continue
            envelope = InterpretationEngine().interpret(request)
            cases_executed += 1
            frames = {item.predicate: item for item in envelope.semantic_frames}
            for expected in case["semantic_frame_expectations"]:
                expectations_checked += 1
                actual = frames.get(expected["predicate"])
                if actual is None:
                    errors.append(f"{case_id}: missing semantic frame {expected['predicate']}")
                    continue
                if _plain(actual.arguments) != expected["arguments"]:
                    errors.append(f"{case_id}/{expected['predicate']}: argument mismatch")
                if actual.polarity != expected["polarity"]:
                    if expected["polarity"] == "mixed":
                        questions.append(
                            f"{case_id}: fixture polarity 'mixed' conflicts with the canonical "
                            "positive|negative|unknown contract."
                        )
                    else:
                        errors.append(
                            f"{case_id}/{expected['predicate']}: canonical polarity {actual.polarity!r} "
                            f"differs from fixture {expected['polarity']!r}"
                        )
                if tuple(expected["unresolved_arguments"]) != actual.unresolved_arguments:
                    if case_id != "unknown-out-of-scope-command":
                        errors.append(f"{case_id}/{expected['predicate']}: unresolved arguments mismatch")
            actual_keys = tuple(item.hypothesis_key for item in envelope.hypotheses)
            if len(actual_keys) != len(set(actual_keys)):
                errors.append(f"{case_id}: duplicate hypothesis keys in live output")
            actual_hypotheses = {item.hypothesis_key: item for item in envelope.hypotheses}
            for values in case["base_hypothesis_expectations"].values():
                for expected in values:
                    expectations_checked += 1
                    actual = actual_hypotheses.get(expected["hypothesis_key"])
                    if actual is None:
                        errors.append(f"{case_id}: missing hypothesis {expected['hypothesis_key']}")
                    elif actual.base_support.value != expected["support"] or actual.disposition.value != expected["disposition"]:
                        errors.append(
                            f"{case_id}/{expected['hypothesis_key']}: got "
                            f"{actual.base_support.value}:{actual.disposition.value}, expected "
                            f"{expected['support']}:{expected['disposition']}"
                        )
            if envelope.out_of_scope != case["expected_out_of_scope"]:
                errors.append(f"{case_id}: out-of-scope status mismatch")
        except Exception as exc:
            errors.append(f"{case_id}: {exc}")

    expected_hypotheses = set(HYPOTHESIS_DIMENSION_BY_KEY)
    if set(ontology_coverage) != expected_hypotheses:
        errors.append(
            "fixture hypothesis coverage differs from the accepted ontology: "
            f"{sorted(expected_hypotheses - set(ontology_coverage))} missing"
        )
    if set(predicate_coverage) != set(PREDICATES):
        errors.append("fixture predicate coverage differs from the code-owned registry")
    if set(appraisal_coverage) != set(_APPRAISAL_SHAPES):
        errors.append("fixture does not cover every accepted appraisal key")
    if set(route_coverage) != _ROUTES:
        errors.append("fixture does not cover every required routing path")

    counts = Counter(item.value for item in eligibility_by_case.values())
    return InterpretationFixtureReport(
        fixture_path=str(fixture_path), fixture_status=raw.get("status", "<missing>"),
        cases_validated=len(cases), cases_executed=cases_executed,
        expectations_checked=expectations_checked,
        eligibility_by_case=eligibility_by_case,
        eligibility_counts=dict(counts), ontology_coverage=dict(ontology_coverage),
        predicate_coverage=dict(predicate_coverage), deferred_cases=tuple(deferred),
        ontology_questions=tuple(dict.fromkeys(questions)), errors=tuple(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Interpretation V0 design cases")
    parser.add_argument("fixture", nargs="?", default=str(DEFAULT_FIXTURE))
    args = parser.parse_args()
    report = validate_fixture(args.fixture)
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
