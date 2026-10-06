from types import SimpleNamespace

from app.ai.Cognitive.system.v0.trace_summary import summarize_trace


def test_summary_keeps_diagnostics_and_excludes_conversation_content():
    record = SimpleNamespace(
        id="trace-id",
        turn_id="turn-id",
        conversation_id="conversation-id",
        terminal_status="completed",
        segment_reset_reason="predecessor_gap",
        failure_payload=None,
        skip_reason=None,
        versions={"capture_schema": "cognitive-shadow-capture-v0.2"},
        missing_predecessor_refs=["chat-message:secret-prior-message"],
        started_at=None,
        completed_at=None,
        timings={"compile_ms": 3.7},
        realization_status="provider_failed",
        realization_timings={"total_ms": 1005.0},
        realization_trace_payload={
            "attempts": [{"provider_error_code": "provider_unsupported"}],
            "final_candidate": None,
            "request": {"messages": [{"content": "secret prompt"}]},
        },
        compiled_request_payload={"messages": [{"content": "hello columbina"}]},
        pipeline_trace_payload={
            "timings": {"total_ms": 44.5, "perception_ms": 3.6},
            "perception": {
                "cue_activations": [
                    {"cue_key": "conversation.question_form", "state": "absent", "value": False},
                    {"cue_key": "conversation.topic_shift", "state": "present", "value": True},
                    {"cue_key": "conversation.repetition", "state": "unknown", "value": None},
                ],
                "overall_uncertainty": 0.125,
                "input_message_ids": ["message-1", "message-2"],
                "missing_information": ["repetition unresolved"],
            },
            "interpretation": {
                "semantic_frames": [{
                    "predicate": "unknown",
                    "arguments": {"surface_action": "hello", "object_reference": "columbina"},
                    "evidence": [{"quoted_text": "hello columbina"}],
                }],
                "hypotheses": [{
                    "dimension": "speaker_goal",
                    "hypothesis_key": "interpretation.goal.unknown",
                    "support": "strong",
                    "disposition": "leading",
                }],
                "unknowns": [{"key": "predicate"}, {"key": "meaning"}],
                "contradictions": [],
                "messages": [{"content": "hello columbina"}],
            },
            "goal_snapshot": {
                "cognitive_turn_id": "turn-id",
                "records": [{
                    "goal_id": "goal-1",
                    "definition_ref": {"definition_key": "interaction.discharge_response_obligation"},
                    "status": "active",
                    "priority_band": "moderate",
                    "owner": "system",
                    "scope": "turn",
                    "scope_ref": "turn-id",
                    "created_turn_ref": "turn-id",
                }],
                "focus_decision": {"primary_goal_ref": "goal-1"},
            },
            "cognitive_turn": {
                "appraisal": {
                    "frames": [{
                        "completeness": "partial",
                        "missing_inputs": ["interpretation-unknown"],
                        "dimensions": [{
                            "dimension_key": "appraisal.certainty",
                            "status": "unknown",
                            "missing_input_keys": ["registered_event_semantics"],
                        }],
                    }]
                },
                "dynamic_state_update": {
                    "snapshot": {
                        "values": [{
                            "state_key": "state.engagement",
                            "band": "moderate",
                            "score": 50,
                            "status": "known",
                        }],
                        "derived_controls": {"response_energy": "moderate"},
                    }
                },
                "action_selection": {
                    "plan": {
                        "moves": [{
                            "move_id": "move-1",
                            "candidate_ref": "candidate-1",
                            "family": "give_feedback",
                            "selection_score": "2.416000",
                            "definition_ref": {"action_key": "give_feedback.acknowledge"},
                        }],
                        "move_priorities": [{"move_ref": "move-1", "priority": "primary"}],
                        "termination": "stop",
                    },
                    "steps": [{
                        "step_index": 0,
                        "selected_candidate_ref": "candidate-1",
                        "constructed_candidates": [{
                            "candidate_id": "candidate-1",
                            "definition_ref": {"action_key": "give_feedback.acknowledge"},
                            "target_refs": [{"role": "turn", "target_type": "dialogue_turn",
                                             "ref": "chat-message:secret-prior-message"}],
                        }],
                        "scored_candidates": [{
                            "candidate_ref": "candidate-1",
                            "score": {"score": "2.416000"},
                        }]
                    }],
                    "shadow_only": True,
                    "safety": {"disposition": "unavailable", "restriction": "commitments"},
                },
            },
        },
    )

    summary = summarize_trace(record)

    assert summary["perception"]["important_cues"] == [
        {"key": "conversation.topic_shift", "state": "present", "value": True},
        {"key": "conversation.repetition", "state": "unknown", "value": None},
    ]
    assert summary["interpretation"]["unknown_keys"] == ["meaning", "predicate"]
    assert summary["action"]["selected_moves"][0]["action"] == "give_feedback.acknowledge"
    [candidate] = summary["action"]["candidates"]
    assert (candidate["action"], candidate["score"], candidate["in_plan"]) == (
        "give_feedback.acknowledge", "2.416000", True,
    )
    assert candidate["candidate"] == summary["action"]["selected_moves"][0]["candidate"]
    assert candidate["targets"][0]["key"].startswith("k")
    assert summary["appraisal"]["missing_inputs"] == [
        "interpretation_unknown",
        "registered_event_semantics",
    ]
    assert summary["realization"]["provider_error_codes"] == ["provider_unsupported"]
    assert summary["trace"]["missing_predecessor_count"] == 1

    rendered = str(summary)
    assert "hello columbina" not in rendered
    assert "secret prompt" not in rendered
    assert "secret-prior-message" not in rendered
    assert "quoted_text" not in rendered
    assert "surface_action" not in rendered


# ---------------------------------------------------------------------------
# SH-EVAL-009/010/011: status-first, lineage-preserving, axis-separated summaries
# ---------------------------------------------------------------------------

import uuid  # noqa: E402

import pytest  # noqa: E402

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineExecutionError  # noqa: E402
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest  # noqa: E402
from app.ai.Cognitive.system.v0.live_shadow.models import ShadowFailure  # noqa: E402
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, observe  # noqa: E402
from app.ai.Cognitive.system.v0.trace_summary import (  # noqa: E402
    LIVE_AUTHORITY, STAGES, SummaryError, latest_content_projection, reveal_final_candidate,
    validate_summary,
)
from cognitive_shadow_support import make_capture  # noqa: E402
from test_cognitive_shadow_compiler import _as_predecessor  # noqa: E402

SECRET = "My secret allergy is to walnuts"


def _observe(capture, predecessor=None, **kwargs):
    return observe(capture, predecessor, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-09-26T00:00:00+00:00",
    ), **kwargs)


def _row(record, **overrides) -> dict:
    """The persisted-row view of a pure terminal record."""

    row = {
        "id": record.terminal_id, "turn_id": record.turn_id, "conversation_id": record.conversation_ref,
        "terminal_status": record.status.value,
        "segment_reset_reason": record.segment_reset_reason.value if record.segment_reset_reason else None,
        "missing_predecessor_refs": list(record.missing_predecessor_refs),
        "skip_reason": record.skip_reason.value if record.skip_reason else None,
        "failure_payload": record.failure.model_dump(mode="json") if record.failure else None,
        "compiled_request_payload": record.compiled_request.model_dump(mode="json") if record.compiled_request else None,
        "pipeline_trace_payload": record.pipeline_trace.model_dump(mode="json") if record.pipeline_trace else None,
        "versions": record.versions.model_dump(mode="json"),
        "timings": record.worker_timings.model_dump(mode="json") if record.worker_timings else None,
        "realization_status": record.realization_status.value, "realization_trace_payload": None,
        "realization_timings": None, "started_at": record.started_at, "completed_at": record.completed_at,
    }
    row.update(overrides)
    return row


def _assert_content_free(summary) -> None:
    rendered = str(summary)
    assert SECRET not in rendered and "walnuts" not in rendered
    assert "quoted_text" not in rendered and "chat-message:" not in rendered


def _chain(*contents: str):
    user, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    history: list[tuple[str, str]] = []
    ids: list[str] = []
    records, previous = [], None
    for index, content in enumerate(contents):
        if index:
            history.append(("assistant", f"reply {index}"))
            ids.append(str(uuid.uuid4()))
        history.append(("user", content))
        ids.append(str(uuid.uuid4()))
        capture = make_capture(
            user_ref=user, conversation_ref=conversation, contents=tuple(history), message_ids=tuple(ids),
            previous_user_message_id=previous[1].focus_message_id if previous else None,
        )
        record = _observe(capture, _as_predecessor(*previous) if previous else None)
        records.append(record)
        previous = (record, capture)
    return records


def test_completed_summary_has_every_stage_ran_and_no_content() -> None:
    [record] = _chain(f"{SECRET}. What is in hummus?")
    summary = summarize_trace(_row(record))
    assert all(summary[stage]["status"] == "ran" for stage in STAGES)
    assert summary["failure"] is None and summary["skip"] is None
    assert summary["versions"]["pipeline_runner"] == record.pipeline_trace.versions.pipeline_runner
    _assert_content_free(summary)


def test_failed_pipeline_summary_exposes_the_envelope_and_not_run_stages() -> None:
    class FailingPipeline:
        def run(self, request):
            raise CognitivePipelineExecutionError(
                cognitive_turn_id=request.cognitive_turn_id, failed_stage="goal_update",
                nested_stage="focus_selection", safe_message="Goal processing could not complete for this pipeline.",
            )

    capture = make_capture(contents=(("user", SECRET),))
    record = _observe(capture, pipeline_factory=lambda turn_id: FailingPipeline())
    assert record.status.value == "failed"
    summary = summarize_trace(_row(record))
    assert summary["failure"] == {
        "stage": "pipeline.goal_update", "nested_stage": "focus_selection", "nested_phase": None,
        "code": "pipeline.goal_update.focus_selection", "retryable": False, "attempt_count": 1,
        "safe_message": "Goal processing could not complete for this pipeline.",
    }
    assert {stage: summary[stage]["status"] for stage in STAGES} == {
        "perception": "not_persisted", "interpretation": "not_persisted", "goals": "failed", "workspace": "not_run",
        "requirements": "not_run", "collaboration": "not_run", "appraisal": "not_run", "state": "not_run",
        "action": "not_run",
    }
    assert "failed:pipeline.goal_update" in summary["flags"]
    assert summary["review"]["fidelity_validation"] == "not_run"
    _assert_content_free(summary)


@pytest.mark.parametrize(("stage", "nested", "expected"), [
    ("capture", None, ("not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("compile", None, ("not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.validation", None, ("not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.perception", None, ("failed", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.handoff_validation", None, ("not_persisted", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.interpretation", None, ("not_persisted", "failed", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.goal_update", None, ("not_persisted", "not_persisted", "failed", "not_run", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.workspace_resolution", None, ("not_persisted", "not_persisted", "not_persisted", "failed", "not_run", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.requirement_resolution", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "failed", "not_run", "not_run", "not_run", "not_run")),
    ("pipeline.collaborative_content", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "failed", "not_run", "not_run", "not_run")),
    ("pipeline.goal_handoff_validation", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_run", "not_run", "not_run")),
    ("pipeline.turn_request", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_run", "not_run", "not_run")),
    ("pipeline.cognitive_turn", "appraisal", ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "failed", "not_run", "not_run")),
    ("pipeline.cognitive_turn", "dynamic_state", ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "failed", "not_run")),
    ("pipeline.cognitive_turn", "action_selection", ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "failed")),
    ("pipeline.cognitive_turn", "action_handoff_validation", ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_run")),
    ("pipeline.cognitive_turn", "trace_validation", ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted")),
    ("pipeline.cognitive_turn", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "unknown", "unknown", "unknown")),
    ("pipeline.trace_validation", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted")),
    ("persistence", None, ("not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted", "not_persisted")),
    ("worker", None, ("unknown", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown")),
])
def test_every_failure_stage_maps_to_explicit_stage_statuses(stage, nested, expected) -> None:
    [record] = _chain(SECRET)
    failure = ShadowFailure(
        stage=stage, nested_stage=nested, code=stage.replace("-", "_") + (f".{nested}" if nested else ""),
        safe_message="A safe message.", attempt_count=2, retryable=stage == "persistence",
    )
    row = _row(record, terminal_status="failed", pipeline_trace_payload=None,
               failure_payload=failure.model_dump(mode="json"))
    summary = summarize_trace(row)
    assert tuple(summary[name]["status"] for name in STAGES) == expected
    assert summary["failure"]["attempt_count"] == 2 and summary["failure"]["stage"] == stage
    assert all(set(summary[name]) == {"status"} for name in STAGES)
    _assert_content_free(summary)


def test_skipped_summary_names_the_reason_and_capture_versions() -> None:
    capture = make_capture(contents=(("user", SECRET),), language="id")
    record = _observe(capture)
    summary = summarize_trace(_row(record))
    assert summary["skip"] == {"reason": "unsupported_language"}
    assert all(summary[stage] == {"status": "not_run"} for stage in STAGES)
    assert summary["versions"]["capture_schema"] == capture.schema_version
    _assert_content_free(summary)


@pytest.mark.parametrize("overrides", [
    {"terminal_status": "completed", "pipeline_trace_payload": None},
    {"terminal_status": "completed", "pipeline_trace_payload": {"perception": {}}},
    {"terminal_status": "failed", "failure_payload": None, "pipeline_trace_payload": None},
    {"terminal_status": "failed", "failure_payload": {"stage": "compile"}, "pipeline_trace_payload": None},
    {"terminal_status": "skipped", "skip_reason": None, "pipeline_trace_payload": None},
    {"terminal_status": "skipped", "skip_reason": "unsupported_language"},
    {"terminal_status": "exploded"},
])
def test_inconsistent_rows_fail_the_summary_instead_of_inventing_values(overrides) -> None:
    [record] = _chain("hello")
    with pytest.raises(SummaryError):
        summarize_trace(_row(record, **overrides))


def test_closed_schema_rejects_drift() -> None:
    [record] = _chain("hello")
    summary = summarize_trace(_row(record))
    validate_summary(summary)
    for mutate in (
        lambda value: value.pop("review"),
        lambda value: value["goals"].pop("records"),
        lambda value: value["action"].update(extra=True),
        lambda value: value["review"].update(live_authority="granted"),
    ):
        broken = {key: (dict(item) if isinstance(item, dict) else item) for key, item in summary.items()}
        broken["goals"], broken["action"], broken["review"] = dict(summary["goals"]), dict(summary["action"]), dict(summary["review"])
        mutate(broken)
        with pytest.raises(SummaryError):
            validate_summary(broken)


def test_goal_rows_distinguish_current_from_historical_records() -> None:
    records = _chain("hi there", "How much protein is in tofu?", "And in tempeh?")
    summary = summarize_trace(_row(records[-1]))
    goals = summary["goals"]
    obligations = [row for row in goals["records"] if row["definition"] == "interaction.discharge_response_obligation"]
    assert len(obligations) == 3 and len({row["goal_id"] for row in obligations}) == 3
    current = [row for row in obligations if row["lineage"] == "current"]
    historical = [row for row in obligations if row["lineage"] == "historical"]
    assert len(current) == 1 and len(historical) == 2
    assert current[0]["scope_ownership"] == "current_turn" and current[0]["status"] == "active"
    assert current[0]["coordination_floor"] is True
    assert goals["focus"]["coordination_floor_goal_id"] == current[0]["goal_id"]
    for row in historical:
        assert row["scope_ownership"] == "other_turn" and row["status"] == "expired"
        assert row["focus_role"] == "inactive"
        assert row["last_transition"] == {
            "kind": "expire", "rule_id": "goal-lifecycle.expire-turn-scope-on-successor-turn.v1",
        }
        assert row["created_turn"] in {records[0].turn_id, records[1].turn_id}
    _assert_content_free(summary)


def test_candidates_sharing_action_and_score_remain_distinguishable() -> None:
    [record] = _chain("hello")
    row = _row(record)
    action = row["pipeline_trace_payload"]["cognitive_turn"]["action_selection"]
    action["steps"] = [{
        "step_index": 0, "selected_candidate_ref": "candidate-a",
        "constructed_candidates": [
            {"candidate_id": cid, "definition_ref": {"action_key": "request_information.clarify"},
             "target_refs": [{"role": "unknown", "target_type": "interpretation_unknown", "ref": ref}]}
            for cid, ref in (("candidate-a", "interpretation-unknown-1"), ("candidate-b", "interpretation-unknown-2"))
        ],
        "gate_results": [{"candidate_ref": "candidate-a", "status": "passed"},
                         {"candidate_ref": "candidate-b", "status": "passed"}],
        "scored_candidates": [{"candidate_ref": cid, "score": {"score": "1.000000"}} for cid in ("candidate-a", "candidate-b")],
    }]
    candidates = summarize_trace(row)["action"]["candidates"]
    assert [item["action"] for item in candidates] == ["request_information.clarify"] * 2
    assert [item["score"] for item in candidates] == ["1.000000"] * 2
    assert candidates[0]["candidate"] != candidates[1]["candidate"]
    assert candidates[0]["targets"][0]["key"] != candidates[1]["targets"][0]["key"]
    assert [item["selected_at_step"] for item in candidates] == [True, False]


def _realized(record, status: str, *, validation: str | None, text: str = "Hello there."):
    attempt = {"attempt_number": 1, "outcome": "candidate_accepted" if validation == "accepted_for_shadow_review" else "candidate_rejected",
               "validation": {"status": validation, "violations": [] if validation != "rejected" else [{"code": "move_order_violation"}]}
               if validation else None}
    payload = {"attempts": [attempt], "final_candidate": {"text": text} if status == "accepted_shadow" else None,
               "request": {"messages": [{"content": SECRET}]}}
    return _row(record, realization_status=status, realization_trace_payload=payload)


def test_review_axes_never_read_shadow_acceptance_as_quality_or_live_approval() -> None:
    [record] = _chain("hello")
    accepted = summarize_trace(_realized(record, "accepted_shadow", validation="accepted_for_shadow_review"))
    assert accepted["review"] == {
        "fidelity_validation": "passed", "behavioral_review": "not_reviewed", "live_authority": LIVE_AUTHORITY,
    }
    rejected = summarize_trace(_realized(record, "rejected", validation="rejected"))
    assert rejected["review"]["fidelity_validation"] == "failed"
    assert rejected["realization"]["validation_codes"] == ["move_order_violation"]
    provider = summarize_trace(_realized(record, "provider_failed", validation=None))
    assert provider["review"]["fidelity_validation"] == "not_run"
    reviewed = summarize_trace(_realized(record, "accepted_shadow", validation="accepted_for_shadow_review"),
                               behavioral_review="failed")
    assert reviewed["review"]["behavioral_review"] == "failed"
    assert reviewed["review"]["live_authority"] == LIVE_AUTHORITY
    with pytest.raises(SummaryError):
        summarize_trace(_realized(record, "accepted_shadow", validation="accepted_for_shadow_review"),
                        behavioral_review="approved_for_live")


def test_candidate_wording_is_only_revealed_explicitly_for_accepted_candidates() -> None:
    [record] = _chain("hello")
    row = _realized(record, "accepted_shadow", validation="accepted_for_shadow_review", text="Wording under review")
    assert "Wording under review" not in str(summarize_trace(row))
    assert SECRET not in str(summarize_trace(row))
    assert reveal_final_candidate(row) == "Wording under review"
    assert reveal_final_candidate(_realized(record, "rejected", validation="rejected")) is None


def test_latest_content_projection_returns_focus_message_and_accepted_realization() -> None:
    [record] = _chain("hello")
    row = _realized(record, "accepted_shadow", validation="accepted_for_shadow_review",
                    text="Wording under review")
    message = SimpleNamespace(id="message-1", turn_id=record.turn_id, role="user", content="hello")
    assert latest_content_projection(row, message, focus_message_id="message-1") == {
        "last_user_message": "hello",
        "last_realization_response": "Wording under review",
    }


def test_latest_content_projection_returns_null_when_no_realization_was_accepted() -> None:
    [record] = _chain("hello")
    row = _realized(record, "rejected", validation="rejected")
    message = SimpleNamespace(id="message-1", turn_id=record.turn_id, role="user", content="hello")
    assert latest_content_projection(row, message, focus_message_id="message-1") == {
        "last_user_message": "hello",
        "last_realization_response": None,
    }


@pytest.mark.parametrize("message", [
    SimpleNamespace(id="message-1", turn_id="turn-id", role="assistant", content="wrong role"),
    SimpleNamespace(id="other-message", turn_id="turn-id", role="user", content="wrong message"),
    SimpleNamespace(id="message-1", turn_id="other-turn", role="user", content="wrong turn"),
])
def test_latest_content_projection_rejects_a_message_outside_the_trace_focus(message) -> None:
    record = SimpleNamespace(
        turn_id="turn-id", realization_status="not_requested",
        realization_trace_payload=None,
    )
    with pytest.raises(SummaryError):
        latest_content_projection(record, message, focus_message_id="message-1")


def test_style_warnings_are_visible_without_becoming_rejection_or_quality_labels():
    [record] = _chain("hello")
    row = _realized(record, "accepted_shadow", validation="accepted_for_shadow_review")
    row['realization_trace_payload']['validation_policy'] = 'style_advisory-v0.1'
    row['realization_trace_payload']['attempts'][0]['validation']['warnings'] = [
        {'code': 'exclamation_used', 'subject_ref': SECRET},
    ]
    summary = summarize_trace(row)
    assert summary['realization']['validation_policy'] == 'style_advisory-v0.1'
    assert summary['realization']['warning_codes'] == ['exclamation_used']
    assert summary['realization']['validation_codes'] == []
    assert summary['review']['behavioral_review'] == 'not_reviewed'
    _assert_content_free(summary)
