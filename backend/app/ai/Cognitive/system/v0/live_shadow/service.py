"""Pure observation and replay of one captured turn.

``observe`` resolves lineage, compiles the canonical request, executes the
existing ``CognitivePipelineV0`` unchanged, validates the result through its
canonical contracts, digests it, and returns exactly one immutable terminal
record.  It performs no I/O: the database queue lives in
``app.services.cognitive_shadow`` and polling in ``app.workers.cognitive_shadow``.

Nothing here imports the chat agent, prompts, tools, realizers, memory writers,
outcome workers, or any fixture adapter.
"""

from __future__ import annotations
from ..contracts.actions import action_versions_supported

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from ..cognitive_pipeline_v0 import (
    PIPELINE_VERSIONS_BY_SCHEMA,
    CognitivePipelineExecutionError,
    CognitivePipelineRequest,
    CognitivePipelineTrace,
    CognitivePipelineV0,
)
from ..appraisal.model_contracts import AppraisalModelError
from ..cognitive_turn import CognitiveTurnRunner
from ..collaborative_content_v0 import CollaborativeContentEngine
from ..collaborative_content_v0.doubles import recorded_model_for
from ..interpretation_v0.model_protocol import SemanticParserError
from ..contracts.actions import ACTION_REGISTRY_VERSION, ActionVersions
from ..contracts.goals import GoalVersions
from .compiler import (
    CompileError,
    CompileSkip,
    PredecessorRecord,
    ShadowLineage,
    compile_request,
    resolve_lineage,
    trace_ref,
)
from .digests import canonical_json, json_payload, payload_digest, semantic_digest
from .models import (
    COMPILER_VERSION,
    SEMANTIC_DIGEST_VERSION,
    SUPPORTED_CAPTURE_SCHEMAS,
    SHADOW_NAMESPACE,
    WORKER_VERSION,
    CognitiveShadowCapture,
    CognitiveShadowTerminalRecord,
    SegmentResetReason,
    ShadowComponentVersions,
    ShadowFailure,
    ShadowWorkerTimings,
    SkipReason,
    TerminalStatus,
    segment_id_for,
    terminal_id_for,
)

# Action trace validation resolves definitions through the process-global
# ``DEFAULT_REGISTRY``.  That is accepted only for this fixed V0 registry;
# observing under any other installed registry fails closed until a
# registry-aware validator exists (ACTION_SELECTION_V0_IMPLEMENTATION.md).
# v0.2 adds two shadow definitions and leaves every v0.1 definition
# unchanged, so a v0.1 trace still resolves every definition it cites.
ACCEPTED_ACTION_REGISTRY_VERSION = "conversational-actions-v0.2-shadow"


def pipeline_trace_id_for(turn_id: str) -> str:
    return str(uuid.uuid5(SHADOW_NAMESPACE, f"{WORKER_VERSION}:pipeline-trace:{turn_id}"))


def turn_trace_id_for(turn_id: str) -> str:
    return str(uuid.uuid5(SHADOW_NAMESPACE, f"{WORKER_VERSION}:turn-trace:{turn_id}"))


def build_pipeline(turn_id: str) -> CognitivePipelineV0:
    """The canonical pipeline with deterministic generated identifiers."""

    return CognitivePipelineV0(
        cognitive_turn_runner=CognitiveTurnRunner(id_factory=lambda: turn_trace_id_for(turn_id)),
        trace_id_factory=lambda: pipeline_trace_id_for(turn_id),
    )


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def shadow_failure(
    stage: str, code: str, safe_message: str, *, attempt: int, retryable: bool = False,
    nested_stage: str | None = None, nested_phase: str | None = None,
    latency_spans=(),
) -> ShadowFailure:
    return ShadowFailure(
        stage=stage, nested_stage=nested_stage, nested_phase=nested_phase, code=code,
        safe_message=safe_message, attempt_count=attempt, retryable=retryable, latency_spans=latency_spans,
    )


def _semantic_parser_error(exc: BaseException) -> SemanticParserError | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, SemanticParserError):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


def pipeline_failure(exc: CognitivePipelineExecutionError, *, attempt: int) -> ShadowFailure:
    """Preserve nested canonical failure ownership with a stable code.

    A semantic-parser failure anywhere in the cause chain appends its typed
    code (``...semantic_grounding.semantic_parser.<code>``) and carries its
    retryability, so a provider timeout is distinguishable from a rejected
    candidate without reading exception text.
    """

    stage = f"pipeline.{exc.failed_stage}"
    parser_error = _semantic_parser_error(exc)
    appraisal_error = None
    current = exc
    while current is not None:
        if isinstance(current, AppraisalModelError):
            appraisal_error = current
            break
        current = current.__cause__
    parts = (stage, exc.nested_stage, exc.nested_phase)
    if parser_error is not None:
        parts += ("semantic_parser", parser_error.code.value)
    if appraisal_error is not None:
        if hasattr(appraisal_error, "orchestration_stage"):
            parts += ("two_call", appraisal_error.orchestration_stage,
                      getattr(appraisal_error, "subject", ""), appraisal_error.code)
        else:
            parts += ("model_appraisal", appraisal_error.code)
    code = ".".join(part for part in parts if part)
    return shadow_failure(
        stage, code, exc.safe_message, attempt=attempt,
        nested_stage=exc.nested_stage, nested_phase=exc.nested_phase,
        retryable=(parser_error is not None and parser_error.retryable) or (
            appraisal_error is not None and appraisal_error.retryable),
        latency_spans=appraisal_error.spans if appraisal_error is not None else (),
    )


def load_capture(payload: Any) -> CognitiveShadowCapture | SkipReason:
    """Validate a stored capture payload, or report its schema as unsupported."""

    if not isinstance(payload, dict) or payload.get("schema_version") not in SUPPORTED_CAPTURE_SCHEMAS:
        return SkipReason.CAPTURE_SCHEMA_UNSUPPORTED
    return CognitiveShadowCapture.model_validate(payload)


@dataclass(frozen=True)
class ObservationContext:
    job_id: str
    attempt: int
    capture_digest: str
    started_at: str


def _base_record(
    capture: CognitiveShadowCapture, context: ObservationContext, lineage, **values: Any
) -> CognitiveShadowTerminalRecord:
    personality = capture.personality
    from .digests import semantic_digest_version
    versions = ShadowComponentVersions(
        semantic_digest=semantic_digest_version(values.get("pipeline_trace") or {}),
        capture_schema=capture.schema_version,
        capability_map=capture.capabilities.mapping_version,
        memory_retrieval_policy=capture.memory_retrieval_policy_version,
        personality_projection=(
            f"{personality.projection_kind}:{personality.projection_id}:{personality.projection_version}"
            if personality is not None else None
        ),
        pipeline=values.pop("pipeline_versions", None),
        dialogue_memory=(
            capture.dialogue.turn_context.versions
            if capture.dialogue is not None and capture.dialogue.turn_context is not None else None
        ),
    )
    return CognitiveShadowTerminalRecord(
        terminal_id=terminal_id_for(context.job_id),
        capture_id=capture.capture_id,
        job_id=context.job_id,
        turn_id=capture.turn_id,
        user_ref=capture.user_ref,
        conversation_ref=capture.conversation_ref,
        focus_message_id=capture.focus_message_id,
        segment_id=lineage.segment_id,
        predecessor_trace_id=lineage.predecessor_trace_id,
        segment_reset_reason=lineage.reset_reason,
        missing_predecessor_refs=lineage.missing_predecessor_refs,
        versions=versions,
        capture_digest=context.capture_digest,
        started_at=context.started_at,
        completed_at=utc_now_text(),
        **values,
    )


def unresolved_record(
    *,
    job_id: str,
    turn_id: str,
    user_ref: str,
    conversation_ref: str,
    focus_message_id: str,
    capture_schema_version: str,
    capture_digest: str,
    started_at: str,
    failure: ShadowFailure | None = None,
    skip_reason: SkipReason | None = None,
) -> CognitiveShadowTerminalRecord:
    """A terminal record for a job that ended before lineage was resolved.

    Built only from the job's own durable identity columns, never from an
    unreadable capture payload.
    """

    return CognitiveShadowTerminalRecord(
        terminal_id=terminal_id_for(job_id),
        status=TerminalStatus.FAILED if failure is not None else TerminalStatus.SKIPPED,
        capture_id=job_id,
        job_id=job_id,
        turn_id=turn_id,
        user_ref=user_ref,
        conversation_ref=conversation_ref,
        focus_message_id=focus_message_id,
        segment_id=segment_id_for(turn_id),
        segment_reset_reason=SegmentResetReason.LINEAGE_UNRESOLVED,
        versions=ShadowComponentVersions(capture_schema=capture_schema_version),
        capture_digest=capture_digest,
        failure=failure,
        skip_reason=skip_reason,
        started_at=started_at,
        completed_at=utc_now_text(),
    )


def observe(
    capture: CognitiveShadowCapture,
    predecessor: PredecessorRecord | None,
    context: ObservationContext,
    *,
    pipeline_factory: Callable[[str], CognitivePipelineV0] = build_pipeline,
) -> CognitiveShadowTerminalRecord:
    """Observe one capture and return its one terminal record.

    Deterministic failures become a ``failed`` record.  Exceptions that are
    not deterministic cognition failures propagate so the queue can retry.
    """

    total_started = time.perf_counter()
    try:
        lineage = resolve_lineage(capture, predecessor)
    except CompileError as exc:
        # An integrity-violating predecessor is never continued: the turn
        # begins its own segment, cites the rejected predecessor, and fails.
        rejected = ShadowLineage(
            segment_id=segment_id_for(capture.turn_id), predecessor_trace_id=None,
            reset_reason=SegmentResetReason.PREDECESSOR_GAP,
            missing_predecessor_refs=(trace_ref(predecessor.terminal_id),) if predecessor else (),
            previous_state=None, previous_goal_snapshot=None,
        )
        return _base_record(
            capture, context, rejected, status=TerminalStatus.FAILED,
            failure=shadow_failure("compile", exc.code, exc.safe_message, attempt=context.attempt),
        )

    compile_started = time.perf_counter()
    try:
        compiled = compile_request(capture, lineage)
    except CompileError as exc:
        return _base_record(
            capture, context, lineage, status=TerminalStatus.FAILED,
            failure=shadow_failure("compile", exc.code, exc.safe_message, attempt=context.attempt),
        )
    compile_ms = (time.perf_counter() - compile_started) * 1000
    if isinstance(compiled, CompileSkip):
        return _base_record(capture, context, lineage, status=TerminalStatus.SKIPPED, skip_reason=compiled.reason)

    request = compiled
    request_payload = json_payload(request)
    try:
        # The exact persisted request must survive a JSON round trip unchanged.
        if CognitivePipelineRequest.model_validate(request_payload) != request:
            raise ValueError("compiled request changed across a JSON round trip")
    except ValueError:
        return _base_record(
            capture, context, lineage, status=TerminalStatus.FAILED,
            failure=shadow_failure(
                "compile", "compiled_request_not_round_trip_safe",
                "The compiled request is not JSON round-trip safe.", attempt=context.attempt,
            ),
        )
    request_digest = payload_digest(request_payload)

    def failed(failure: ShadowFailure) -> CognitiveShadowTerminalRecord:
        return _base_record(
            capture, context, lineage, status=TerminalStatus.FAILED, failure=failure,
            compiled_request=request, compiled_request_digest=request_digest,
            worker_timings=ShadowWorkerTimings(
                compile_ms=compile_ms, pipeline_wall_ms=0.0, pipeline_cpu_ms=0.0,
                total_wall_ms=(time.perf_counter() - total_started) * 1000,
            ),
        )

    if ActionVersions().registry != ACCEPTED_ACTION_REGISTRY_VERSION or ACTION_REGISTRY_VERSION != ACCEPTED_ACTION_REGISTRY_VERSION:
        return failed(shadow_failure(
            "worker", "action_registry_version_unaccepted",
            "The installed action registry is not accepted for live-shadow observation.",
            attempt=context.attempt,
        ))

    pipeline = None
    pipeline_wall_started = time.perf_counter()
    pipeline_cpu_started = time.thread_time()
    try:
        pipeline = pipeline_factory(capture.turn_id)
        if getattr(pipeline, "two_call", None) is not None:
            pipeline.two_call.dialogue_context = capture.dialogue.turn_context if capture.dialogue else None
            pipeline.two_call.recorder.worker_attempt = context.attempt
        trace = pipeline.run(request)
    except CognitivePipelineExecutionError as exc:
        failure = pipeline_failure(exc, attempt=context.attempt)
        if getattr(pipeline, "two_call", None) is not None:
            failure = ShadowFailure(**(failure.model_dump(mode="python") |
                {"latency_spans":tuple(pipeline.two_call.recorder.spans), "retryable":False}))
        return failed(failure)
    pipeline_wall_ms = (time.perf_counter() - pipeline_wall_started) * 1000
    pipeline_cpu_ms = (time.thread_time() - pipeline_cpu_started) * 1000

    trace_payload = json_payload(trace)
    try:
        revalidated = CognitivePipelineTrace.model_validate(trace_payload)
        if revalidated != trace or canonical_json(json_payload(revalidated)) != canonical_json(trace_payload):
            raise ValueError("pipeline trace changed across a JSON round trip")
        _validate_cross_record(capture, request, trace)
    except ValueError:
        # Fail closed: a trace that does not survive validation is never stored
        # as completed, and no partial trace is kept.
        return failed(shadow_failure(
            "pipeline.trace_validation", "pipeline.trace_validation.persisted_contract",
            "The completed pipeline trace failed persisted-contract validation.",
            attempt=context.attempt,
        ))

    return _base_record(
        capture, context, lineage, status=TerminalStatus.COMPLETED,
        compiled_request=request, compiled_request_digest=request_digest,
        pipeline_trace=trace, trace_payload_digest=payload_digest(trace_payload),
        semantic_digest=semantic_digest(trace_payload), pipeline_versions=trace.versions,
        worker_timings=ShadowWorkerTimings(
            compile_ms=compile_ms, pipeline_wall_ms=pipeline_wall_ms,
            pipeline_cpu_ms=pipeline_cpu_ms,
            total_wall_ms=(time.perf_counter() - total_started) * 1000,
        ),
    )


def _validate_cross_record(
    capture: CognitiveShadowCapture, request: CognitivePipelineRequest, trace: CognitivePipelineTrace
) -> None:
    """Recompute ownership and version invariants across capture, request, and trace."""

    if (
        trace.cognitive_turn_id != capture.turn_id
        or trace.conversation_ref != capture.conversation_ref
        or trace.focus_message_id != capture.focus_message_id
        or trace.input_message_ids != tuple(message.message_id for message in capture.messages)
        or trace.runtime_snapshot_id != request.runtime.snapshot_id
    ):
        raise ValueError("trace ownership differs from the capture")
    if trace.pipeline_trace_id != pipeline_trace_id_for(capture.turn_id):
        raise ValueError("trace identity was not generated deterministically")
    if not action_versions_supported(trace.versions.action):
        raise ValueError("trace action versions differ from the installed versions")
    safety = trace.action_selection.safety
    if (
        trace.cognitive_turn.input_snapshot.action_runtime.safety_decision != request.runtime.safety_decision
        or safety.policy_version != capture.safety_decision.policy_version
        or safety.decision_schema_version != capture.safety_decision.schema_version
    ):
        raise ValueError("the captured safety decision did not survive into the action gates")
    if not trace.action_selection.shadow_only:
        raise ValueError("a live-shadow trace must remain shadow-only")
    resolution = trace.requirement_resolution
    if resolution is not None:
        # Requirement resolution consumed exactly the captured turn-boundary
        # facts and the captured dialogue context of this turn.
        from ..requirement_resolution_v0.engine import dialogue_references
        from .compiler import requirement_inputs_for

        if request.requirement_inputs != requirement_inputs_for(capture):
            raise ValueError("requirement inputs differ from the captured turn boundary")
        dialogue = request.dialogue_context
        expected = dialogue_references(
            dialogue.interpretation_context if dialogue is not None else None,
            dialogue.action_object_refs if dialogue is not None else (),
        )
        if resolution.dialogue_refs != expected:
            raise ValueError("requirement resolution cited a dialogue context other than the captured one")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class ReplayStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    VERSION_UNAVAILABLE = "version_unavailable"
    INVALID_RECORD = "invalid_record"


@dataclass(frozen=True)
class ReplayReport:
    status: ReplayStatus
    code: str
    stored_semantic_digest: str | None
    replayed_semantic_digest: str | None = None


def replay(
    *,
    compiled_request_payload: Any,
    pipeline_trace_payload: Any,
    compiled_request_digest: str,
    stored_semantic_digest: str,
    versions_payload: Any,
    pipeline_factory: Callable[[str], CognitivePipelineV0] = build_pipeline,
) -> ReplayReport:
    """Re-run a stored compiled request under the installed matching versions.

    Read-only: a mismatch is reported, never written back.  Any version
    difference is reported as unavailable rather than replayed under a
    different policy.
    """

    def report(status: ReplayStatus, code: str, replayed: str | None = None) -> ReplayReport:
        return ReplayReport(status, code, stored_semantic_digest, replayed)

    try:
        versions = ShadowComponentVersions.model_validate(versions_payload)
    except ValidationError:
        return report(ReplayStatus.VERSION_UNAVAILABLE, "component_versions_unsupported")
    if versions.compiler != COMPILER_VERSION or versions.semantic_digest not in {SEMANTIC_DIGEST_VERSION, "cognitive-shadow-semantic-digest-v0.2"}:
        return report(ReplayStatus.VERSION_UNAVAILABLE, "component_versions_unsupported")
    from .digests import semantic_digest_version
    if versions.semantic_digest != semantic_digest_version(pipeline_trace_payload or {}):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "semantic_digest_policy_unavailable")
    if versions.pipeline is None:
        return report(ReplayStatus.INVALID_RECORD, "not_a_completed_record")
    if versions.pipeline.action.registry != ACCEPTED_ACTION_REGISTRY_VERSION or not action_versions_supported(versions.pipeline.action):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "action_versions_unavailable")

    if payload_digest(compiled_request_payload) != compiled_request_digest:
        return report(ReplayStatus.MISMATCH, "stored_request_digest_mismatch")
    try:
        stored_digest = semantic_digest(pipeline_trace_payload)
    except (ValueError, TypeError):
        return report(ReplayStatus.INVALID_RECORD, "stored_trace_shape_invalid")
    if stored_digest != stored_semantic_digest:
        return report(ReplayStatus.MISMATCH, "stored_trace_digest_mismatch")
    model_trace = pipeline_trace_payload.get("cognitive_turn", {}).get("appraisal", {}).get("model_trace")
    try:
        request = CognitivePipelineRequest.model_validate(compiled_request_payload)
    except ValidationError:
        return report(ReplayStatus.INVALID_RECORD, "stored_request_invalid")
    # A trace produced by a historical runner or Goal policy is never re-run
    # under the installed semantics: its outcome would not be comparable.
    if (
        (versions.pipeline.pipeline_schema, versions.pipeline.pipeline_runner)
        != PIPELINE_VERSIONS_BY_SCHEMA[request.schema_version]
        or versions.pipeline.goal != GoalVersions()
    ):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "pipeline_versions_unavailable")

    # Collaborative content is model-backed: replay re-runs the deterministic
    # compiler on the recorded candidates (and recorded provider failures),
    # never the provider.
    try:
        stored_trace = CognitivePipelineTrace.model_validate(pipeline_trace_payload)
        recorded = recorded_model_for(getattr(stored_trace, "collaboration", None))
    except (ValidationError, ValueError):
        return report(ReplayStatus.INVALID_RECORD, "stored_trace_invalid")
    try:
        pipeline = pipeline_factory(request.cognitive_turn_id)
        if stored_trace.orchestration is not None:
            from ..two_call.orchestrator import install
            from ..two_call.provider import FrozenTwoCallModel
            pipeline = install(pipeline, FrozenTwoCallModel(stored_trace.orchestration))
        elif model_trace is not None:
            from ..appraisal.model_engine import ModelAppraisalEngine, FrozenAppraisalModel
            pipeline.cognitive_turn_runner.appraisal_engine = ModelAppraisalEngine(
                FrozenAppraisalModel(stored_trace.cognitive_turn.appraisal.model_trace))
        # v1 runs the content subsystem itself (before Call 2), so it replays
        # from its recorded candidates like a staged trace.
        if recorded is not None and (stored_trace.orchestration is None
                                     or stored_trace.orchestration.protocol == 'cognitive-two-call-v1'):
            pipeline.collaboration_engine = CollaborativeContentEngine(recorded)
        trace = pipeline.run(request)
    except CognitivePipelineExecutionError as exc:
        return report(ReplayStatus.MISMATCH, pipeline_failure(exc, attempt=0).code)
    except (AppraisalModelError, SemanticParserError, ValueError, KeyError):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "two_call_configuration_unavailable")
    if trace.versions != versions.pipeline:
        return report(ReplayStatus.VERSION_UNAVAILABLE, "pipeline_versions_unavailable")
    replayed = semantic_digest(json_payload(trace))
    if replayed != stored_semantic_digest:
        return report(ReplayStatus.MISMATCH, "semantic_digest_mismatch", replayed)
    return report(ReplayStatus.MATCH, "semantic_digest_match", replayed)


__all__ = [
    "ACCEPTED_ACTION_REGISTRY_VERSION", "ObservationContext", "ReplayReport", "ReplayStatus",
    "build_pipeline", "load_capture", "observe", "pipeline_failure", "shadow_failure",
    "pipeline_trace_id_for", "replay", "turn_trace_id_for", "unresolved_record", "utc_now_text",
]
