"""Review-time validator for the Perception V0 design fixture.

This module is intentionally not imported by the production package surface.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import model_validator

from .engine import PerceptionEngine
from .evidence import resolve_json_pointer
from .manifest import CUE_MANIFEST
from .models import (
    AttachmentFact,
    CueState,
    CueValueType,
    FrozenDict,
    FrozenModel,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    TextSpanEvidence,
    ToolEventFact,
)

DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "perception_design_cases.json"


class CueCoverage(FrozenModel):
    cue_key: str
    expectations: int = 0
    present: int = 0
    absent: int = 0
    unknown: int = 0
    not_applicable: int = 0
    matched: int = 0
    mismatched: int = 0


class PerceptionFixtureReport(FrozenModel):
    fixture_path: str
    fixture_status: str
    cases_run: int
    expectations_checked: int
    evidence_checks: int
    coverage_by_cue: Mapping[str, CueCoverage]
    ontology_questions: tuple[str, ...]
    errors: tuple[str, ...]

    @model_validator(mode="after")
    def freeze_coverage(self) -> PerceptionFixtureReport:
        if not isinstance(self.coverage_by_cue, FrozenDict):
            object.__setattr__(self, "coverage_by_cue", FrozenDict(self.coverage_by_cue))
        return self

    @property
    def passed(self) -> bool:
        return not self.errors


def _fixture_request(case: dict[str, Any]) -> PerceptionRequest:
    runtime = case["runtime_context"]
    attachments = tuple(
        AttachmentFact(
            attachment_id=item["attachment_id"],
            filename=item.get("filename"),
            trusted_runtime_provenance="perception-design-fixture",
            evidence_reference=f"{case['case_id']}:/attachments/{index}",
        )
        for index, item in enumerate(runtime.get("attachments", ()))
    )
    raw_tool = runtime.get("last_tool_event")
    tool_event = None
    if raw_tool is not None:
        tool_event = ToolEventFact(
            tool=raw_tool["tool"],
            status=raw_tool["status"],
            trusted_runtime=True,
            authorized=True,
            event_reference=f"{case['case_id']}:/last_tool_event",
            invocation_reference=raw_tool.get("invocation_reference"),
            error_code=raw_tool.get("error_code"),
        )
    return PerceptionRequest(
        cognitive_turn_id=f"fixture:{case['case_id']}",
        messages=tuple(PerceptionMessage.model_validate(item) for item in case["messages"]),
        focus_message_id=case["focus_message_id"],
        runtime=PerceptionRuntimeContext(
            snapshot_id=f"fixture-runtime:{case['case_id']}",
            attachments=attachments,
            available_capabilities=tuple(runtime.get("available_capabilities", ())),
            last_tool_event=tool_event,
            conversation_mode=runtime.get("conversation_mode"),
        ),
    )


def _validate_expected_value(definition: Any, state: CueState, value: Any) -> None:
    if state in {CueState.UNKNOWN, CueState.NOT_APPLICABLE}:
        if value is not None:
            raise ValueError("unknown/not-applicable expectations require null values")
    elif definition.value_type == CueValueType.BOOLEAN:
        required = state == CueState.PRESENT
        if type(value) is not bool or value is not required:
            raise ValueError("Boolean expectation value conflicts with state")
    elif definition.value_type == CueValueType.INTEGER:
        if type(value) is not int:
            raise ValueError("integer expectation requires an integer value")
        if state == CueState.PRESENT and value <= 0:
            raise ValueError("present integer expectation must be positive")
        if state == CueState.ABSENT and value != 0:
            raise ValueError("absent integer expectation must be zero")


def _validate_fixture_evidence(
    raw: dict[str, Any],
    messages: dict[str, dict[str, Any]],
    runtime: dict[str, Any],
) -> None:
    evidence_type = raw.get("evidence_type")
    if evidence_type == "text_span":
        evidence = TextSpanEvidence.model_validate(raw)
        message = messages.get(evidence.message_id)
        if message is None:
            raise ValueError(f"unknown evidence message {evidence.message_id}")
        if message["content"][evidence.start:evidence.end] != evidence.quoted_text:
            raise ValueError("fixture quoted text does not equal its exact source span")
    elif evidence_type == "runtime_fact":
        pointer = raw.get("json_pointer")
        actual = resolve_json_pointer(runtime, pointer)
        if actual != raw.get("value"):
            raise ValueError("fixture runtime evidence value does not equal its JSON Pointer")
    elif evidence_type == "whole_message":
        if raw.get("message_id") not in messages:
            raise ValueError("fixture whole-message evidence does not resolve")
    elif evidence_type == "message_sequence":
        ids = raw.get("message_ids", ())
        if not ids or any(message_id not in messages for message_id in ids):
            raise ValueError("fixture message-sequence evidence does not resolve")
    else:
        raise ValueError(f"unsupported fixture evidence type: {evidence_type!r}")


def _json_plain(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(key in actual and _is_subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_is_subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def _evidence_represented(expected: dict[str, Any], actual_items: tuple[Any, ...]) -> bool:
    for actual_model in actual_items:
        actual = actual_model.model_dump(mode="json")
        if expected["evidence_type"] == "text_span":
            if actual["evidence_type"] == "whole_message" and actual["message_id"] == expected["message_id"]:
                return True
            if actual["evidence_type"] == "text_span" and all(
                actual.get(key) == expected.get(key)
                for key in ("message_id", "start", "end", "quoted_text")
            ):
                return True
        elif expected["evidence_type"] == "runtime_fact":
            if (
                actual["evidence_type"] == "runtime_fact"
                and actual["json_pointer"] == expected["json_pointer"]
                and _is_subset(expected["value"], actual["value"])
            ):
                return True
        elif _json_plain(expected) == _json_plain(actual):
            return True
    return False


def validate_fixture(path: str | Path = DEFAULT_FIXTURE) -> PerceptionFixtureReport:
    fixture_path = Path(path)
    errors: list[str] = []
    ontology_questions: list[str] = []
    expectations_checked = evidence_checks = cases_run = 0
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or raw.get("ontology_version") != 1:
        errors.append("fixture schema_version and ontology_version must both be 1")
    if raw.get("status") != "draft_for_human_review":
        errors.append("fixture status must remain draft_for_human_review")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        errors.append("fixture cases must be a list")
        cases = []
    case_ids = [case.get("case_id") for case in cases]
    if len(case_ids) != len(set(case_ids)) or any(not item for item in case_ids):
        errors.append("fixture case ids must be non-empty and unique")

    definitions = {item.cue_key: item for item in CUE_MANIFEST}
    mutable_coverage: dict[str, dict[str, int]] = {
        key: {state.value: 0 for state in CueState} | {"expectations": 0, "matched": 0, "mismatched": 0}
        for key in definitions
    }
    engine = PerceptionEngine()
    for case in cases:
        case_id = case.get("case_id", "<missing-case-id>")
        ontology_questions.extend(f"{case_id}: {item}" for item in case.get("ontology_questions", ()))
        try:
            message_ids = [item["message_id"] for item in case["messages"]]
            if len(message_ids) != len(set(message_ids)):
                raise ValueError("message ids are not unique")
            if case["focus_message_id"] not in message_ids:
                raise ValueError("focus message does not resolve")
            if message_ids.index(case["focus_message_id"]) != len(message_ids) - 1:
                raise ValueError("fixture contains messages after focus")
            messages = {item["message_id"]: item for item in case["messages"]}
            seen_expectations: set[str] = set()
            for expected in case.get("cue_expectations", ()):
                cue_key = expected["cue_key"]
                if cue_key in seen_expectations:
                    raise ValueError(f"duplicate cue expectation {cue_key}")
                seen_expectations.add(cue_key)
                definition = definitions.get(cue_key)
                if definition is None:
                    raise ValueError(f"unknown cue expectation {cue_key}")
                state = CueState(expected["expected_state"])
                _validate_expected_value(definition, state, expected.get("expected_value"))
                if state == CueState.PRESENT and not expected.get("evidence"):
                    raise ValueError(f"present expectation {cue_key} has no evidence")
                for evidence in expected.get("evidence", ()):
                    _validate_fixture_evidence(evidence, messages, case["runtime_context"])
            request = _fixture_request(case)
            event = engine.perceive(request)
            actual_by_key = {item.cue_key: item for item in event.cue_activations}
            handoff = event.interpretation_handoff()
            live_keys = {item.cue_key for item in handoff.cue_activations}
            if handoff.versions != event.versions:
                raise ValueError("interpretation handoff did not preserve event versions")
            shadow_keys = {
                item.cue_key for item in CUE_MANIFEST if item.lifecycle_status.value == "shadow"
            }
            leaked = live_keys & shadow_keys
            if leaked:
                raise ValueError(f"shadow cues leaked into interpretation handoff: {sorted(leaked)}")
            cases_run += 1
            for expected in case.get("cue_expectations", ()):
                cue_key = expected["cue_key"]
                wanted_state = CueState(expected["expected_state"])
                coverage = mutable_coverage[cue_key]
                coverage["expectations"] += 1
                coverage[wanted_state.value] += 1
                expectations_checked += 1
                actual = actual_by_key[cue_key]
                mismatch = actual.state != wanted_state or actual.value != expected.get("expected_value")
                if mismatch:
                    coverage["mismatched"] += 1
                    errors.append(
                        f"{case_id}/{cue_key}: got {actual.state.value}:{actual.value!r}, "
                        f"expected {wanted_state.value}:{expected.get('expected_value')!r}"
                    )
                    if cue_key == "runtime.tool_failure" and actual.state == CueState.NOT_APPLICABLE:
                        ontology_questions.append(
                            f"{case_id}: fixture expects absent with no tool event, but the V0 runtime "
                            "contract requires not_applicable; human design resolution is required."
                        )
                    if cue_key == "linguistic.self_reference" and case_id == "tool-failure-hypothetical-quote":
                        ontology_questions.append(
                            f"{case_id}: quoted ‘I’ is correctly excluded, but the unquoted first-person "
                            "‘we’ remains observable; the fixture's expected zero conflicts with the cue definition."
                        )
                else:
                    coverage["matched"] += 1
                for expected_evidence in expected.get("evidence", ()):
                    evidence_checks += 1
                    if not _evidence_represented(expected_evidence, actual.evidence):
                        errors.append(
                            f"{case_id}/{cue_key}: expected evidence is not represented: "
                            f"{json.dumps(expected_evidence, sort_keys=True)}"
                        )
        except Exception as exc:
            errors.append(f"{case_id}: {exc}")

    coverage = {
        key: CueCoverage(cue_key=key, **counts)
        for key, counts in mutable_coverage.items()
    }
    return PerceptionFixtureReport(
        fixture_path=str(fixture_path),
        fixture_status=raw.get("status", "<missing>"),
        cases_run=cases_run,
        expectations_checked=expectations_checked,
        evidence_checks=evidence_checks,
        coverage_by_cue=coverage,
        ontology_questions=tuple(dict.fromkeys(ontology_questions)),
        errors=tuple(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Perception V0 design fixture")
    parser.add_argument("fixture", nargs="?", default=str(DEFAULT_FIXTURE))
    args = parser.parse_args()
    report = validate_fixture(args.fixture)
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
