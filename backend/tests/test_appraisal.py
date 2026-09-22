"""Black-box contract tests for deterministic V0 appraisal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.Cognitive.system.appraisal import validate_fixture
from app.ai.Cognitive.system.appraisal.engine import AppraisalEngine
from app.ai.Cognitive.system.appraisal.models import (
    ActiveGoal, AppraisalDimension, AppraisalRequest, CapabilitySnapshot, Contradiction,
    DimensionStatus, InterpretationEnvelope as CanonicalInterpretationEnvelope,
    InterpretationScenario as CanonicalInterpretationScenario, RuntimeContext,
    SemanticFrame as CanonicalSemanticFrame, SupplementalInput, UnknownInput as CanonicalUnknownInput,
)
from app.ai.Cognitive.system.contracts.interpretation import (
    InterpretationEvidence, InterpretationEvidenceKind, InterpretationVersions,
)


def SemanticFrame(**values: object) -> CanonicalSemanticFrame:
    frame_id = str(values["frame_id"])
    return CanonicalSemanticFrame(
        polarity="positive", evidence_refs=(f"evidence:{frame_id}",),
        grounding_rule_ids=("test.grounding.v1",), **values,
    )


def InterpretationScenario(**values: object) -> CanonicalInterpretationScenario:
    return CanonicalInterpretationScenario(
        hypothesis_refs={}, compatibility_rule_ids=("test.compatibility.v1",), **values,
    )


def UnknownInput(**values: object) -> CanonicalUnknownInput:
    return CanonicalUnknownInput(owner="unknown", **values)


def InterpretationEnvelope(*, cognitive_turn_id: str = "turn-test", **values: object) -> CanonicalInterpretationEnvelope:
    frames = tuple(values.get("semantic_frames", ()))
    scenarios = tuple(values.get("scenarios", ()))
    leading_seen = False
    normalized_scenarios = []
    for scenario in scenarios:
        if scenario.disposition == "leading":
            if leading_seen:
                scenario = scenario.model_copy(update={"disposition": "retained_alternative"})
            leading_seen = True
        normalized_scenarios.append(scenario)
    values["scenarios"] = tuple(normalized_scenarios)
    contradictions = tuple(values.get("contradictions", ()))
    evidence_ids = {
        ref for frame in frames for ref in frame.evidence_refs
    } | {
        ref for item in contradictions for ref in item.evidence_refs
    }
    evidence = tuple(InterpretationEvidence(
        evidence_id=ref, source_kind=InterpretationEvidenceKind.RUNTIME_FACT,
        source_ref=ref, runtime_snapshot_ref="runtime-test", json_pointer="/test", value=True,
    ) for ref in sorted(evidence_ids))
    return CanonicalInterpretationEnvelope(
        cognitive_turn_id=cognitive_turn_id, conversation_ref="conversation-test",
        perception_event_id="perception-test", input_message_ids=("message-test",),
        focus_message_id="message-test", runtime_snapshot_id="runtime-test",
        evidence=evidence, hypotheses=(),
        versions=InterpretationVersions(
            schema="interpretation-envelope-v0.1", ontology="test-ontology-v1",
            feature_manifest="test-features-v1", semantic_grounder="test-grounder-v1",
            hypothesis_rules="test-rules-v1", scenario_builder="interpretation-scenarios-v0.1",
            bias_policy="test-bias-v1", evidence_validator="test-evidence-v1",
            perception_schema="test-perception-v1", perception_cue_manifest="test-cues-v1",
            perception_detectors="test-detectors-v1",
        ), **values,
    )


def test_all_appraisal_design_cases_execute() -> None:
    report = validate_fixture()

    assert report.cases_run == 25
    assert report.reappraisal_steps_run == 4
    assert report.frames_checked >= 31
    assert report.assertions >= 900
    assert report.errors == ()


def _goal(goal_id: str = "goal", **desired_state: object) -> ActiveGoal:
    return ActiveGoal(
        goal_id=goal_id, owner="columbina", goal_type="conversation",
        description="test goal", desired_state=desired_state or {"resolved": True},
        priority_band="high",
    )


def _request(
    frames: tuple[SemanticFrame, ...], scenarios: tuple[InterpretationScenario, ...],
    *, goals: tuple[ActiveGoal, ...] | None = None,
    unknowns: tuple[UnknownInput, ...] = (),
    supplements: tuple[SupplementalInput, ...] = (),
    safety_decision: str | None = None,
    external_export_control: bool | None = None,
    previous_frames: tuple = (),
    cognitive_turn_id: str = "turn-test",
) -> AppraisalRequest:
    return AppraisalRequest(
        cognitive_turn_id=cognitive_turn_id,
        interpretation=InterpretationEnvelope(
            cognitive_turn_id=cognitive_turn_id,
            interpretation_trace_id="interpretation-test", semantic_frames=frames,
            scenarios=scenarios, unknowns=unknowns,
        ),
        active_goals=goals or (_goal(),),
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(snapshot_id="cap-test"),
            safety_decision=safety_decision,
            external_export_control=external_export_control,
        ),
        supplemental_inputs=supplements,
        previous_frames=previous_frames,
    )


def _scenario(
    scenario_id: str, frame_id: str, *, unknown_refs: tuple[str, ...] = (),
    disposition: str = "leading",
) -> InterpretationScenario:
    return InterpretationScenario(
        scenario_id=scenario_id, support="moderate", disposition=disposition,
        semantic_frame_refs=(frame_id,), unknown_refs=unknown_refs,
    )


def test_unregistered_predicate_is_conservatively_unknown() -> None:
    frame = SemanticFrame(frame_id="event-new", predicate="never_seen_before")
    result = AppraisalEngine().appraise(_request((frame,), (_scenario("scenario", frame.frame_id),)))

    assert all(item.status == DimensionStatus.UNKNOWN for item in result.frames[0].dimensions)
    assert all(
        item.missing_input_keys == ("registered_event_semantics",)
        for item in result.frames[0].dimensions
    )
    assert result.frames[0].completeness == "partial"
    assert result.aggregate.missing_required_inputs == ("registered_event_semantics",)


def test_unknowns_do_not_leak_between_scenarios() -> None:
    known = SemanticFrame(frame_id="known", predicate="explain_estimate_change")
    uncertain = SemanticFrame(frame_id="uncertain", predicate="share_photo")
    request = _request(
        (known, uncertain),
        (
            _scenario("scenario-known", "known"),
            _scenario("scenario-uncertain", "uncertain", unknown_refs=("unknown-consent",)),
        ),
        unknowns=(UnknownInput(unknown_id="unknown-consent", key="consent"),),
    )

    by_scenario = {frame.scenario_id: frame for frame in AppraisalEngine().appraise(request).frames}
    assert by_scenario["scenario-known"].dimension("appraisal.norm_compatibility").value == "positive"
    assert by_scenario["scenario-uncertain"].dimension("appraisal.norm_compatibility").status == DimensionStatus.UNKNOWN


def test_contradictions_do_not_leak_between_scenarios() -> None:
    first = SemanticFrame(frame_id="event-a", predicate="upload_failure")
    second = SemanticFrame(frame_id="event-b", predicate="upload_failure")
    contradictions = (
        Contradiction(contradiction_id="conflict-policy-allow", status="active", description="allow", evidence_refs=("evidence:conflict",)),
        Contradiction(contradiction_id="conflict-policy-block", status="active", description="block", evidence_refs=("evidence:conflict",)),
    )
    request = AppraisalRequest(
        cognitive_turn_id="turn-test",
        interpretation=InterpretationEnvelope(
            interpretation_trace_id="interpretation-test", semantic_frames=(first, second),
            scenarios=(
                _scenario("scenario-a", "event-a"),
                InterpretationScenario(
                    scenario_id="scenario-b", support="moderate",
                    disposition="retained_alternative", semantic_frame_refs=("event-b",),
                    contradiction_refs=tuple(item.contradiction_id for item in contradictions),
                ),
            ),
            contradictions=contradictions,
        ),
        active_goals=(_goal(),),
        runtime=RuntimeContext(capability_snapshot=CapabilitySnapshot(snapshot_id="cap-test")),
    )

    by_scenario = {frame.scenario_id: frame for frame in AppraisalEngine().appraise(request).frames}
    assert by_scenario["scenario-a"].dimension("appraisal.norm_compatibility").status == DimensionStatus.KNOWN
    assert by_scenario["scenario-b"].dimension("appraisal.norm_compatibility").status == DimensionStatus.DISPUTED


def test_supplemental_evidence_only_reappraises_its_scope() -> None:
    first = SemanticFrame(frame_id="event-a", predicate="export_success")
    second = SemanticFrame(frame_id="event-b", predicate="export_success")
    supplement = SupplementalInput(
        input_id="runtime-a", input_type="runtime_fact", value="failed",
        provenance="runtime", scenario_refs=("scenario-a",), event_refs=("event-a",),
    )
    request = _request(
        (first, second),
        (_scenario("scenario-a", "event-a"), _scenario("scenario-b", "event-b")),
        supplements=(supplement,),
    )

    by_scenario = {frame.scenario_id: frame for frame in AppraisalEngine().appraise(request).frames}
    assert by_scenario["scenario-a"].dimension("appraisal.goal_congruence").value == "negative"
    assert by_scenario["scenario-b"].dimension("appraisal.goal_congruence").value == "positive"
    assert "runtime-a" not in by_scenario["scenario-b"].dimension("appraisal.goal_congruence").evidence_refs


def test_ambiguous_unscoped_supplement_is_rejected() -> None:
    first = SemanticFrame(frame_id="event-a", predicate="export_success")
    second = SemanticFrame(frame_id="event-b", predicate="export_success")
    with pytest.raises(ValidationError, match="requires scope"):
        _request(
            (first, second),
            (_scenario("scenario-a", "event-a"), _scenario("scenario-b", "event-b")),
            supplements=(SupplementalInput(
                input_id="global", input_type="runtime_fact", provenance="runtime",
            ),),
        )


def test_supplemental_input_requires_provenance() -> None:
    event = SemanticFrame(frame_id="event", predicate="export_success")
    with pytest.raises(ValidationError, match="explicit provenance"):
        _request(
            (event,), (_scenario("scenario", "event"),),
            supplements=(SupplementalInput(input_id="fact", input_type="runtime_fact"),),
        )


def test_goal_effects_come_from_desired_state_not_goal_id() -> None:
    event = SemanticFrame(frame_id="failure", predicate="export_failure")
    request = _request(
        (event,), (_scenario("scenario", "failure"),),
        goals=(
            _goal("opaque-a", failure_explained=True),
            _goal("opaque-b", export_completed=True),
        ),
        external_export_control=False,
    )

    result = AppraisalEngine().appraise(request)
    by_goal = {frame.subject.focal_goal_ref: frame for frame in result.frames}
    assert by_goal["opaque-a"].dimension("appraisal.goal_congruence").value == "positive"
    assert by_goal["opaque-b"].dimension("appraisal.goal_congruence").value == "negative"
    assert by_goal["opaque-b"].dimension("appraisal.controllability").value == "low"
    assert set(result.aggregate.dominant_frame_ids) == {
        by_goal["opaque-a"].frame_id, by_goal["opaque-b"].frame_id,
    }


def test_desired_state_requires_the_expected_value() -> None:
    event = SemanticFrame(frame_id="failure", predicate="export_failure")
    request = _request(
        (event,), (_scenario("scenario", "failure"),),
        goals=(_goal("opaque", failure_explained=False),),
    )
    frame = AppraisalEngine().appraise(request).frames[0]
    assert frame.dimension("appraisal.goal_congruence").value == "negative"


def test_controllability_uses_registered_capability_and_parameters() -> None:
    event = SemanticFrame(frame_id="send", predicate="send_report")
    scenario = _scenario("scenario", "send")
    absent = AppraisalEngine().appraise(_request((event,), (scenario,))).frames[0]
    assert absent.dimension("appraisal.controllability").value == "low"

    available_request = _request((event,), (scenario,))
    available_request = AppraisalRequest(
        cognitive_turn_id=available_request.cognitive_turn_id,
        interpretation=available_request.interpretation,
        active_goals=available_request.active_goals,
        runtime=RuntimeContext(
            capability_snapshot=CapabilitySnapshot(
                snapshot_id="cap-test", available_actions=("send_report",)
            ),
            available_actions=("send_report",),
            required_parameters=("recipient",),
        ),
    )
    available = AppraisalEngine().appraise(available_request).frames[0]
    assert available.dimension("appraisal.controllability").value == "moderate"


def test_reappraisal_uses_latest_history_and_turn_scoped_ids() -> None:
    event = SemanticFrame(frame_id="event", predicate="explain_estimate_change")
    scenario = _scenario("scenario", "event")
    first_request = _request((event,), (scenario,), cognitive_turn_id="turn-one")
    first = AppraisalEngine().appraise(first_request).frames[0]
    second_request = _request(
        (event,), (scenario,), previous_frames=(first,), cognitive_turn_id="turn-two",
    )
    second = AppraisalEngine().appraise(second_request).frames[0]
    third_request = _request(
        (event,), (scenario,), previous_frames=(second, first), cognitive_turn_id="turn-three",
    )
    third = AppraisalEngine().appraise(third_request).frames[0]

    assert first.frame_id != AppraisalEngine().appraise(
        _request((event,), (scenario,), cognitive_turn_id="another-turn")
    ).frames[0].frame_id
    assert second.revision == 2 and second.supersedes_frame_id == first.frame_id
    assert third.revision == 3 and third.supersedes_frame_id == second.frame_id


def test_scoped_memory_does_not_imply_control_or_norm_support() -> None:
    event = SemanticFrame(frame_id="alert", predicate="alert_code")
    supplement = SupplementalInput(
        input_id="memory", input_type="memory_record", memory_id="memory-1",
        provenance="reviewed",
    )
    frame = AppraisalEngine().appraise(
        _request((event,), (_scenario("scenario", "alert"),), supplements=(supplement,))
    ).frames[0]
    assert frame.dimension("appraisal.controllability").value == "moderate"
    assert frame.dimension("appraisal.norm_compatibility").value == "neutral"


def test_high_consequence_scenario_difference_is_preserved() -> None:
    safe = SemanticFrame(frame_id="safe", predicate="explain_estimate_change")
    harmful = SemanticFrame(frame_id="harmful", predicate="delete_customer_records")
    request = _request(
        (safe, harmful),
        (
            _scenario("safe-scenario", "safe"),
            _scenario("harmful-scenario", "harmful", disposition="retained_alternative"),
        ),
        safety_decision="blocked",
    )

    assert AppraisalEngine().appraise(request).aggregate.high_consequence_uncertainty is True


@pytest.mark.parametrize(
    ("field", "value"),
    (("support", "certain"), ("disposition", "maybe")),
)
def test_scenario_contract_rejects_unsupported_enums(field: str, value: str) -> None:
    data = {
        "scenario_id": "scenario", "support": "strong", "disposition": "leading",
        "semantic_frame_refs": ("event",), field: value,
    }
    with pytest.raises(ValidationError):
        InterpretationScenario(**data)


def test_dimension_contract_rejects_unknown_keys_types_and_values() -> None:
    base = {
        "dimension_key": "appraisal.certainty", "status": "known",
        "value_type": "unsigned_ordinal", "value": "high",
    }
    for change in (
        {"dimension_key": "appraisal.made_up"},
        {"value_type": "signed_ordinal"},
        {"value": "nonsense"},
    ):
        with pytest.raises(ValidationError):
            AppraisalDimension(**(base | change))


def test_nested_contract_data_is_deeply_immutable() -> None:
    frame = SemanticFrame(
        frame_id="event", predicate="explain_estimate_change",
        arguments={"nested": {"answer": 1}, "items": [1, 2]},
    )

    with pytest.raises(TypeError):
        frame.arguments["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        frame.arguments["nested"]["answer"] = 2  # type: ignore[index]
    assert frame.arguments["items"] == (1, 2)
