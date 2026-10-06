"""Replay of stored compiled requests under the installed matching versions."""

from __future__ import annotations

import copy

from sqlalchemy import select

from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.models import GenerationPath, ShadowSourceChannel
from app.ai.Cognitive.system.v0.live_shadow.service import (
    ACCEPTED_ACTION_REGISTRY_VERSION, ObservationContext, ReplayStatus, observe, replay,
)
from app.db.models import CognitiveShadowTrace
from app.services import cognitive_shadow as service
from cognitive_shadow_support import (  # noqa: F401  (fixtures)
    add_turn, create_conversation, create_user, database, make_capture, shadow_env,
)


def _completed():
    capture = make_capture(contents=(("user", "hi"), ("assistant", "Hello!"), ("user", "Is tofu high in protein?")))
    record = observe(capture, None, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-09-26T00:00:00+00:00",
    ))
    return {
        "compiled_request_payload": record.compiled_request.model_dump(mode="json"),
        "pipeline_trace_payload": record.pipeline_trace.model_dump(mode="json"),
        "compiled_request_digest": record.compiled_request_digest,
        "stored_semantic_digest": record.semantic_digest,
        "versions_payload": record.versions.model_dump(mode="json"),
    }


def test_semantic_replay_matches_for_installed_versions() -> None:
    stored = _completed()
    report = replay(**stored)
    assert report.status == ReplayStatus.MATCH
    assert report.replayed_semantic_digest == stored["stored_semantic_digest"]
    # Diagnostic timings differ between runs; the exact payload digest may too,
    # but the semantic digest must not.
    assert replay(**stored).status == ReplayStatus.MATCH


def test_mutated_stored_inputs_or_outputs_are_rejected() -> None:
    stored = _completed()
    request = copy.deepcopy(stored["compiled_request_payload"])
    request["messages"][-1]["content"] = "Is tofu low in protein?"
    assert replay(**{**stored, "compiled_request_payload": request}).code == "stored_request_digest_mismatch"
    # Re-digesting a mutated request cannot hide it: the replayed trace differs.
    forged = replay(**{**stored, "compiled_request_payload": request,
                       "compiled_request_digest": payload_digest(request)})
    assert forged.status == ReplayStatus.MISMATCH
    trace = copy.deepcopy(stored["pipeline_trace_payload"])
    trace["action_selection"]["plan"]["moves"] = []
    assert replay(**{**stored, "pipeline_trace_payload": trace}).code == "stored_trace_digest_mismatch"


def test_version_differences_are_reported_not_replayed() -> None:
    stored = _completed()
    versions = copy.deepcopy(stored["versions_payload"])
    versions["pipeline"]["action"]["registry"] = "conversational-actions-v0.3-shadow"
    assert replay(**{**stored, "versions_payload": versions}).status == ReplayStatus.VERSION_UNAVAILABLE
    versions = copy.deepcopy(stored["versions_payload"])
    versions["compiler"] = "cognitive-shadow-compiler-v9"
    assert replay(**{**stored, "versions_payload": versions}).status == ReplayStatus.VERSION_UNAVAILABLE
    versions = copy.deepcopy(stored["versions_payload"])
    versions["pipeline"]["perception"]["cue_manifest"] = "cue-manifest-v9"
    assert replay(**{**stored, "versions_payload": versions}).code == "pipeline_versions_unavailable"
    assert ACCEPTED_ACTION_REGISTRY_VERSION == "conversational-actions-v0.2-shadow"


def test_historical_runner_or_goal_policy_is_reported_without_running() -> None:
    stored = _completed()
    called = []

    def factory(turn_id):
        called.append(turn_id)
        raise AssertionError("a historical trace must not be re-run under installed semantics")

    for path, value in (
        (("pipeline", "pipeline_runner"), "cognitive-pipeline-runner-v0.2"),
        (("pipeline", "goal", "goal_lifecycle_policy"), "goal-lifecycle-v0.1-draft"),
    ):
        versions = copy.deepcopy(stored["versions_payload"])
        target = versions
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        report = replay(**{**stored, "versions_payload": versions}, pipeline_factory=factory)
        assert (report.status, report.code) == (ReplayStatus.VERSION_UNAVAILABLE, "pipeline_versions_unavailable")
    assert called == []


def test_fixed_registry_guard_fails_closed(monkeypatch) -> None:
    from app.ai.Cognitive.system.v0.live_shadow import service as live

    monkeypatch.setattr(live, "ACCEPTED_ACTION_REGISTRY_VERSION", "some-other-registry")
    capture = make_capture()
    record = observe(capture, None, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest=payload_digest(capture),
        started_at="2026-09-26T00:00:00+00:00",
    ))
    assert record.status.value == "failed" and record.failure.code == "action_registry_version_unaccepted"
    assert record.pipeline_trace is None


def test_database_replay_is_read_only_and_user_scoped(shadow_env) -> None:
    from app.workers import cognitive_shadow as worker

    with shadow_env.db() as db:
        user = create_user(db)
        stranger = create_user(db)
        conversation = create_conversation(db, user)
        focus, job = add_turn(db, conversation, "Is tofu high in protein?")
    assert service.capture_turn(
        user_id=user.id, conversation_id=conversation.id, turn_id=focus.turn_id, user_message_id=focus.id,
        chat_job_id=job.id, generation_path=GenerationPath.CHAT_WORKER, source_channel=ShadowSourceChannel.WEB,
    )
    assert worker.run_once()
    with shadow_env.db() as db:
        row = db.scalar(select(CognitiveShadowTrace))
        before = (row.pipeline_trace_payload, row.semantic_digest, row.trace_payload_digest)
        assert service.replay_terminal(db, user_id=user.id, terminal_id=row.id).status == ReplayStatus.MATCH
        assert service.replay_terminal(db, user_id=stranger.id, terminal_id=row.id).status == ReplayStatus.INVALID_RECORD
    with shadow_env.db() as db:
        row = db.scalar(select(CognitiveShadowTrace))
        assert (row.pipeline_trace_payload, row.semantic_digest, row.trace_payload_digest) == before
