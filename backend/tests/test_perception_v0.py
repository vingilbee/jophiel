"""Black-box behavior tests for deterministic Perception V0."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.ai.Cognitive.system.perception_v0 import (
    AttachmentFact,
    CueState,
    PerceptionMessage,
    PerceptionRequest,
    PerceptionRuntimeContext,
    ToolEventFact,
    perceive,
)
from app.ai.Cognitive.system.perception_v0.fixture_validator import validate_fixture


FIXTURE = Path(__file__).parents[1] / "app/ai/Cognitive/system/data/perception_design_cases.json"


def _event(
    content: str,
    *,
    prior: tuple[PerceptionMessage, ...] = (),
    runtime: PerceptionRuntimeContext | None = None,
    language_tag: str | None = "en",
):
    focus = PerceptionMessage(message_id="focus", role="user", content=content)
    return perceive(PerceptionRequest(
        cognitive_turn_id="turn-test",
        messages=(*prior, focus),
        focus_message_id="focus",
        runtime=runtime or PerceptionRuntimeContext(snapshot_id="runtime-test"),
        language_tag=language_tag,
    ))


def _cue(event, key: str):
    return next(item for item in event.cue_activations if item.cue_key == key)


def test_all_twenty_fixture_cases_execute_and_report_only_contract_conflicts() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = validate_fixture(FIXTURE)

    assert fixture["status"] == "draft_for_human_review"
    assert len(fixture["cases"]) == 20
    assert report.cases_run == 20
    assert report.expectations_checked == 49
    assert report.evidence_checks == 36
    assert len(report.errors) == 2
    assert any("tool_failure" in item and "not_applicable" in item for item in report.errors)
    assert any("self_reference" in item and "present:1" in item for item in report.errors)
    assert any("human design resolution" in item for item in report.ontology_questions)
    assert any("unquoted first-person" in item for item in report.ontology_questions)


def test_factual_question_and_action_request_question_are_distinct() -> None:
    factual = _event("What time does the museum close?")
    action = _event("Could you send the report?")

    assert _cue(factual, "conversation.question_form").state == CueState.PRESENT
    assert _cue(factual, "conversation.explicit_action_request").state == CueState.ABSENT
    assert _cue(action, "conversation.question_form").state == CueState.PRESENT
    assert _cue(action, "conversation.explicit_action_request").state == CueState.PRESENT


def test_correction_refusal_repetition_and_lexical_minimal_pairs() -> None:
    correction = _event(
        "No, I said Tuesday, not Thursday.",
        prior=(PerceptionMessage(message_id="prior", role="assistant", content="Your appointment is Thursday."),),
    )
    refusal = _event(
        "No thanks.",
        prior=(PerceptionMessage(message_id="prior", role="assistant", content="Would you like dessert?"),),
    )
    first = _event("pls send the budget draft")
    repeated = _event(
        "pls send the budget draft",
        prior=(PerceptionMessage(message_id="prior", role="user", content="pls send the budget draft"),),
    )

    assert _cue(correction, "conversation.explicit_correction").state == CueState.PRESENT
    assert _cue(refusal, "conversation.explicit_correction").state == CueState.ABSENT
    assert _cue(first, "conversation.repetition").value == 0
    assert _cue(repeated, "conversation.repetition").value == 1
    assert _cue(_event("I think the train leaves at six."), "linguistic.uncertainty_marker").state == CueState.PRESENT
    assert _cue(_event("The train leaves at six."), "linguistic.uncertainty_marker").state == CueState.ABSENT
    assert _cue(_event("I did not approve it."), "linguistic.negation").state == CueState.PRESENT
    assert _cue(_event("I did approve it."), "linguistic.negation").state == CueState.ABSENT


def test_runtime_trust_boundaries_ignore_user_claims_and_capability_lists() -> None:
    claim = _event("I attached it and the export tool failed.")
    attachment = _event(
        "Here it is.",
        runtime=PerceptionRuntimeContext(
            snapshot_id="runtime-attachment",
            attachments=(AttachmentFact(
                attachment_id="att-1", filename="scan.pdf",
                trusted_runtime_provenance="upload", evidence_reference="upload-1",
            ),),
        ),
    )
    capability = _event(
        "Please generate a PDF.",
        runtime=PerceptionRuntimeContext(
            snapshot_id="runtime-capability", available_capabilities=("summarize_text",)
        ),
    )
    unavailable = _event(
        "I attached it.",
        runtime=PerceptionRuntimeContext(
            snapshot_id="runtime-attachments-unavailable", attachments_available=False
        ),
    )

    assert _cue(claim, "runtime.attachment_present").state == CueState.ABSENT
    assert _cue(claim, "runtime.tool_failure").state == CueState.NOT_APPLICABLE
    assert _cue(attachment, "runtime.attachment_present").state == CueState.PRESENT
    assert _cue(unavailable, "runtime.attachment_present").state == CueState.UNKNOWN
    assert all(item.cue_key != "runtime.capability_unavailable" for item in capability.cue_activations)


@pytest.mark.parametrize(("trusted", "authorized"), [(False, True), (True, False), (False, False)])
def test_untrusted_or_unauthorized_tool_failures_remain_unknown(
    trusted: bool, authorized: bool
) -> None:
    event = _event(
        "The export failed.",
        runtime=PerceptionRuntimeContext(
            snapshot_id="runtime-tool",
            last_tool_event=ToolEventFact(
                tool="export", status="failed", trusted_runtime=trusted,
                authorized=authorized, event_reference="event-1",
            ),
        ),
    )

    activation = _cue(event, "runtime.tool_failure")
    assert activation.state == CueState.UNKNOWN
    assert activation.value is None
    assert activation.missing_information


def test_trusted_authorized_failure_and_success_are_exact_runtime_observations() -> None:
    def runtime(status: str) -> PerceptionRuntimeContext:
        return PerceptionRuntimeContext(
            snapshot_id=f"runtime-{status}",
            last_tool_event=ToolEventFact(
                tool="export", status=status, trusted_runtime=True,
                authorized=True, event_reference=f"event-{status}",
            ),
        )

    failure = _cue(_event("status", runtime=runtime("failed")), "runtime.tool_failure")
    success = _cue(_event("status", runtime=runtime("succeeded")), "runtime.tool_failure")
    unrecognized = _cue(_event("status", runtime=runtime("mysterious")), "runtime.tool_failure")
    assert failure.state == CueState.PRESENT
    assert failure.evidence[0].json_pointer == "/last_tool_event"
    assert success.state == CueState.ABSENT
    assert unrecognized.state == CueState.UNKNOWN


def test_quote_ownership_excludes_owned_markers_and_preserves_ambiguity() -> None:
    introduced = _event('The tool said "I failed and I hate it."')
    ambiguous = _event('"I hate it"')
    unmatched = _event('She wrote "I hate it')
    reported_request = _event('She said "Could you send it?"')

    assert _cue(introduced, "linguistic.self_reference").state == CueState.ABSENT
    assert _cue(introduced, "linguistic.negative_wording").state == CueState.ABSENT
    assert _cue(ambiguous, "linguistic.self_reference").state == CueState.UNKNOWN
    assert _cue(ambiguous, "linguistic.negative_wording").state == CueState.UNKNOWN
    assert _cue(unmatched, "linguistic.self_reference").state == CueState.UNKNOWN
    assert _cue(reported_request, "conversation.question_form").state == CueState.ABSENT
    assert _cue(reported_request, "conversation.explicit_action_request").state == CueState.ABSENT


def test_explicit_urgency_is_not_emotional_intensity_and_mixed_wording_coexists() -> None:
    deadline = _event("This is awful—please fix it by 3 p.m.")
    intensity = _event("Awful!")
    mixed = _event("Great, another broken export.")

    assert _cue(deadline, "task.urgency").state == CueState.PRESENT
    assert _cue(intensity, "task.urgency").state == CueState.ABSENT
    assert _cue(mixed, "linguistic.positive_wording").state == CueState.PRESENT
    assert _cue(mixed, "linguistic.negative_wording").state == CueState.PRESENT


def test_unsupported_language_preserves_runtime_evaluation_and_lexical_unknowns() -> None:
    event = _event("No lo hagas.", language_tag="es")

    assert _cue(event, "linguistic.negation").state == CueState.UNKNOWN
    assert _cue(event, "conversation.explicit_action_request").state == CueState.UNKNOWN
    assert _cue(event, "runtime.attachment_present").state == CueState.ABSENT
    assert event.overall_uncertainty > 0


def test_shadow_cues_are_visible_but_cannot_enter_interpretation_handoff() -> None:
    event = _event("Great, another broken export. Fix it by 3 p.m.")
    full = {item.cue_key for item in event.cue_activations}
    handoff_view = event.interpretation_handoff()
    handoff = {item.cue_key for item in handoff_view.cue_activations}

    assert {"linguistic.positive_wording", "linguistic.negative_wording", "task.urgency"} <= full
    assert "linguistic.positive_wording" not in handoff
    assert "linguistic.negative_wording" not in handoff
    assert "task.urgency" not in handoff
    assert len(handoff) == 9
    assert handoff_view.versions == event.versions
    assert handoff_view.perception_event_id == event.perception_event_id


def test_determinism_input_immutability_and_local_performance() -> None:
    request = PerceptionRequest(
        cognitive_turn_id="turn-deterministic",
        messages=(PerceptionMessage(message_id="m1", role="user", content="Could you fix it now?"),),
        focus_message_id="m1",
        runtime=PerceptionRuntimeContext(snapshot_id="runtime-deterministic"),
    )
    before = request.model_dump(mode="json")
    started = time.perf_counter()
    events = [perceive(request) for _ in range(100)]
    elapsed = time.perf_counter() - started

    assert all(item == events[0] for item in events)
    assert request.model_dump(mode="json") == before
    assert elapsed < 5.0


def test_v0_isolated_from_legacy_contracts() -> None:
    event = _event("Hello")

    assert event.__class__.__module__.endswith("perception_v0.models")
    assert not hasattr(event, "is_question")
    assert not hasattr(event, "intent")
