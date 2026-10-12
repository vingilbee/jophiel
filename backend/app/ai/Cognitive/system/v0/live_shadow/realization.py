"""Realization V0 as a downstream extension of a completed shadow observation.

    stored capture -> deterministic CognitivePipelineV0 (``observe``, unchanged)
                   -> deterministic RealizationRequestV0 compilation
                   -> provider call -> validation
                   -> one atomic terminal persistence containing both components

The pure ``CognitivePipelineV0.run()`` and the core semantic digest are
untouched: realization reads only the completed trace, and its outcome is
recorded in separate, separately digested fields.  A realization failure can
never erase the valid Action trace, and nothing here reaches the live chat
response, a tool, or memory.

Replay never calls a model: it recompiles the request under matching
versions, revalidates every stored candidate, and compares digests.
"""

from __future__ import annotations
from ..contracts.actions import action_versions_supported

import copy
import time
from typing import Any

from pydantic import ValidationError

from ..cognitive_pipeline_v0 import PIPELINE_VERSIONS_BY_SCHEMA, CognitivePipelineRequest, CognitivePipelineTrace
from ..contracts.actions import ActionVersions
from ..contracts.goals import GoalVersions
from ..contracts.dialogue_memory import DialogueTurnContext
from ..contracts.realization import (
    RealizationFailure, RealizationFailurePhase,
    RealizationModelProvenance, RealizationTimings, RealizationTraceV0, RealizationVersions, realization_semantic_digest,
    sha256_json,
)
from ..realization_v0.inventory import realization_inventory
from ..realization_v0.errors import RealizationProviderError
from ..realization_v0 import (
    RealizationCompileSkip, RealizationCompilerV0, RealizationEngineV0, RealizationExecutionError,
    RealizationValidatorV0,
)
from .models import (
    CognitiveShadowTerminalRecord,
    ShadowRealizationStatus,
    TerminalStatus,
    realization_trace_digest,
)
from .service import ReplayReport, ReplayStatus, utc_now_text

_INTERNAL_FAILURE = RealizationFailure(
    phase=RealizationFailurePhase.TRACE_VALIDATION, code="realization_internal_error",
    safe_message="Realization failed unexpectedly; the deterministic observation is unaffected.",
)



def requirement_content_of(trace: CognitivePipelineTrace) -> tuple:
    resolution = trace.requirement_resolution
    return resolution.realization_content if resolution is not None else ()


def collaboration_content_of(trace: CognitivePipelineTrace) -> tuple:
    collaboration = trace.collaboration
    return collaboration.realization_content if collaboration is not None else ()


def wording_content_of(trace: CognitivePipelineTrace) -> tuple[tuple, tuple]:
    """Requirement and collaboration content as worded under the trace's recorded policy."""
    requirement, collaboration = requirement_content_of(trace), collaboration_content_of(trace)
    if trace.orchestration is not None and trace.orchestration.realization_policy in (
            'separate_realization-v1.1', 'separate_realization-v1.2', 'separate_realization-v1.3',
            'separate_realization-v1.4'):
        from ..two_call.v1_content import conversational_content
        requirement = conversational_content(requirement, trace)
        collaboration = conversational_content(collaboration, trace)
    return requirement, collaboration


def understanding_content_of(trace: CognitivePipelineTrace) -> tuple:
    """Protocol v1.1 anchored references to the user's own reported experience."""
    if trace.orchestration is None or trace.orchestration.protocol != 'cognitive-two-call-v1':
        return ()
    from ..two_call.v1_grounding import understanding_content
    return understanding_content(trace)


def _realization_inputs(record: CognitiveShadowTerminalRecord, dialogue_context: DialogueTurnContext | None,
                        ) -> dict[str, Any]:
    trace = record.pipeline_trace
    action = trace.action_selection
    return dict(
        action_result=action,
        inventory=realization_inventory(action, dialogue_context, *wording_content_of(trace),
                                        understanding_content_of(trace)),
        personality=trace.cognitive_turn.input_snapshot.action_personality,
        state=trace.cognitive_turn.dynamic_state_update.snapshot,
        language_tag=record.compiled_request.language_tag,
        current_user_utterance=_focus_user_utterance(record.compiled_request),
        safety_basis=action.safety,
        conversation_context=conversation_context_of(trace, record.compiled_request),
    )


def conversation_context_of(trace: CognitivePipelineTrace, request: CognitivePipelineRequest,
                            ) -> tuple[tuple[str, str], ...]:
    """Realization policy v1.4+: up to 8 messages before the focus, from the immutable compiled request."""
    from ..contracts.realization import MAX_CONVERSATION_CONTEXT_CHARACTERS, MAX_CONVERSATION_CONTEXT_MESSAGES
    from ..two_call.v1_contracts import CONVERSATION_CONTEXT_REALIZATION_POLICIES
    orchestration = trace.orchestration
    if orchestration is None or orchestration.realization_policy not in CONVERSATION_CONTEXT_REALIZATION_POLICIES:
        return ()
    earlier = []
    for message in request.messages:
        if message.message_id == request.focus_message_id:
            break
        # An over-long message is left out whole rather than cut mid-meaning.
        if message.content.strip() and len(message.content) <= MAX_CONVERSATION_CONTEXT_CHARACTERS:
            earlier.append(('user' if message.role.value == 'user' else 'columbina', message.content))
    return tuple(earlier[-MAX_CONVERSATION_CONTEXT_MESSAGES:])


def _focus_user_utterance(request: CognitivePipelineRequest) -> str:
    """Read the exact immutable focus utterance; never consult mutable chat state."""

    matches = tuple(message for message in request.messages if message.message_id == request.focus_message_id)
    if len(matches) != 1 or matches[0].role.value != "user":
        raise ValueError("the compiled request has no unique user focus utterance")
    return matches[0].content


CONVERSATIONAL_POLICY = "conversational-v1"
INSTRUCTION_VARIANT = "conversational-v1"


def validation_policy_for(orchestration) -> str:
    """The validation policy a separately worded v1 record uses (recorded in its trace)."""
    from ..two_call.v1_contracts import VALIDATION_POLICY_BY_REALIZATION_POLICY
    return VALIDATION_POLICY_BY_REALIZATION_POLICY[orchestration.realization_policy]


def instruction_variant_for(orchestration) -> str:
    """The wording instructions a v1 record uses (selected by its recorded realization policy)."""
    from ..two_call.v1_contracts import INSTRUCTION_VARIANT_BY_REALIZATION_POLICY
    return INSTRUCTION_VARIANT_BY_REALIZATION_POLICY[orchestration.realization_policy]


def conversational_engine(engine: RealizationEngineV0, policy: str = CONVERSATIONAL_POLICY,
                          variant: str = INSTRUCTION_VARIANT) -> RealizationEngineV0:
    """Protocol v1: natural-voice instructions plus a conversational validation policy.

    ``conversational-v1`` makes style and alignment bookkeeping warnings;
    ``conversational-v2`` blocks only malformed output and false action or
    memory claims.  A model without instruction variants (offline doubles)
    keeps its own instructions.
    """
    from ..realization_v0.validator import RealizationValidatorV0
    model = engine.model
    if model is not None and hasattr(model, "with_instruction_variant"):
        model = model.with_instruction_variant(variant)
    return RealizationEngineV0(model, max_attempts=engine.max_attempts, compiler=engine.compiler,
                               validator=RealizationValidatorV0(policy=policy), clock=engine.clock,
                               unavailable_reason=engine.unavailable_reason)


def _captured_wording(orchestration) -> bool:
    """Earlier two-call protocols word inside Call 2; v1 uses separate Realization."""
    from ..two_call.v1_contracts import REALIZATION_POLICIES_V1
    return orchestration is not None and orchestration.realization_policy not in REALIZATION_POLICIES_V1


def _with_realization(record: CognitiveShadowTerminalRecord, **updates: Any) -> CognitiveShadowTerminalRecord:
    fields = {name: getattr(record, name) for name in CognitiveShadowTerminalRecord.model_fields}
    fields.update(updates, completed_at=utc_now_text())
    return CognitiveShadowTerminalRecord(**fields)


def realize_record(
    record: CognitiveShadowTerminalRecord, *, engine: RealizationEngineV0, configured_maximum_characters: int,
    dialogue_context: DialogueTurnContext | None = None,
) -> CognitiveShadowTerminalRecord:
    """Attach exactly one realization outcome to a completed record.  Never raises.

    ``dialogue_context`` is the captured v0.3 turn context: only the dialogue
    objects the selected moves reference become authorized content.  The
    resulting candidate is shadow-only and never re-enters dialogue memory.
    """

    if record.status != TerminalStatus.COMPLETED or record.pipeline_trace is None:
        return record
    if not record.pipeline_trace.action_selection.plan.moves:
        return record  # nothing to word
    started = time.perf_counter()
    try:
        if _captured_wording(record.pipeline_trace.orchestration):
            from ..two_call.realization import captured_engine
            engine = captured_engine(record.pipeline_trace.orchestration)
        elif record.pipeline_trace.orchestration is not None:
            orchestration = record.pipeline_trace.orchestration
            engine = conversational_engine(engine, validation_policy_for(orchestration),
                                           instruction_variant_for(orchestration))
        trace = engine.realize(**_realization_inputs(record, dialogue_context),
                               configured_maximum_characters=configured_maximum_characters)
    except RealizationExecutionError as exc:
        return _failed(record, exc.failure(), started)
    except Exception:  # noqa: BLE001 - realization must never cost the deterministic trace
        return _failed(record, _INTERNAL_FAILURE, started)
    try:
        return _with_realization(
            record,
            realization_status=ShadowRealizationStatus(trace.status.value),
            realization_request=trace.request,
            realization_request_digest=trace.request_digest,
            realization_trace=trace,
            realization_trace_digest=realization_trace_digest(trace),
            realization_semantic_digest=realization_semantic_digest(trace),
            realization_timings=trace.timings,
        )
    except (ValueError, ValidationError):
        return _failed(record, RealizationFailure(
            phase=RealizationFailurePhase.TRACE_VALIDATION, code="realization_record_invalid",
            safe_message="The realization outcome failed terminal-record validation.",
        ), started)


def _failed(record: CognitiveShadowTerminalRecord, failure: RealizationFailure, started: float) -> CognitiveShadowTerminalRecord:
    total = (time.perf_counter() - started) * 1000
    return _with_realization(
        record, realization_status=ShadowRealizationStatus.EXECUTION_FAILED, realization_failure=failure,
        realization_timings=RealizationTimings(compile_ms=total, generation_ms=0.0, validation_ms=0.0, total_ms=total),
    )


# ---------------------------------------------------------------------------
# Replay (no generation)
# ---------------------------------------------------------------------------

def replay_realization(
    *,
    realization_status: str,
    realization_trace_payload: Any,
    realization_trace_digest_value: str | None,
    realization_semantic_digest_value: str | None,
    pipeline_trace_payload: Any,
    compiled_request_payload: Any,
    compiled_request_digest_value: str,
    language_tag: str | None,
    fallback_maximum_characters: int,
    expected_model_id: str | None = None,
    expected_provenance: RealizationModelProvenance | None = None,
    dialogue_context: DialogueTurnContext | None = None,
) -> ReplayReport:
    """Recompile, revalidate, and compare digests.  Read-only and never calls a model.

    For a v0.3 record, ``dialogue_context`` is the captured turn context: the
    dialogue-derived inventory is rebuilt from it, never from current state.
    """

    def report(status: ReplayStatus, code: str, replayed: str | None = None) -> ReplayReport:
        return ReplayReport(status, code, realization_semantic_digest_value, replayed)

    if realization_status not in {item.value for item in ShadowRealizationStatus}:
        return report(ReplayStatus.INVALID_RECORD, "realization_status_unknown")
    if realization_status == ShadowRealizationStatus.NOT_REQUESTED.value:
        return report(ReplayStatus.INVALID_RECORD, "realization_not_requested")
    if realization_status == ShadowRealizationStatus.EXECUTION_FAILED.value or not isinstance(
        realization_trace_payload, dict
    ):
        return report(ReplayStatus.INVALID_RECORD, "no_realization_trace")
    if realization_trace_payload.get("versions") != RealizationVersions().model_dump(mode="json"):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "realization_versions_unavailable")
    if sha256_json(realization_trace_payload) != realization_trace_digest_value:
        return report(ReplayStatus.MISMATCH, "stored_realization_trace_digest_mismatch")
    try:
        stored = RealizationTraceV0.model_validate(realization_trace_payload)
    except ValidationError:
        return report(ReplayStatus.INVALID_RECORD, "stored_realization_trace_invalid")
    if realization_semantic_digest(stored) != realization_semantic_digest_value:
        return report(ReplayStatus.MISMATCH, "stored_realization_semantic_digest_mismatch")
    if stored.status.value != realization_status:
        return report(ReplayStatus.INVALID_RECORD, "realization_status_mismatch")
    if expected_model_id is not None and stored.model_provenance is not None and (
        stored.model_provenance.model_id != expected_model_id
    ):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "realization_model_unavailable")
    # A candidate produced under different provider semantics (another
    # capability manifest, resolved option, or legacy unrecorded options) is
    # reported, never presented as a replay under the installed configuration.
    if expected_provenance is not None and stored.model_provenance is not None and (
        stored.model_provenance != expected_provenance
    ):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "realization_provider_configuration_unavailable")
    # The Action plan a realization worded was produced by specific pipeline,
    # Goal, and Action versions; under different installed versions it is
    # reported unavailable rather than re-validated as if current.
    stored_versions = pipeline_trace_payload.get("versions") if isinstance(pipeline_trace_payload, dict) else None
    if isinstance(stored_versions, dict) and (
        not action_versions_supported(stored_versions.get("action"))
        or stored_versions.get("goal") != GoalVersions().model_dump(mode="json")
        or (stored_versions.get("pipeline_schema"), stored_versions.get("pipeline_runner"))
        not in set(PIPELINE_VERSIONS_BY_SCHEMA.values())
    ):
        return report(ReplayStatus.VERSION_UNAVAILABLE, "pipeline_versions_unavailable")
    if sha256_json(compiled_request_payload) != compiled_request_digest_value:
        return report(ReplayStatus.MISMATCH, "stored_compiled_request_digest_mismatch")
    try:
        pipeline = CognitivePipelineTrace.model_validate(pipeline_trace_payload)
    except ValidationError:
        return report(ReplayStatus.INVALID_RECORD, "stored_pipeline_trace_invalid")
    try:
        pipeline_request = CognitivePipelineRequest.model_validate(compiled_request_payload)
    except ValidationError:
        return report(ReplayStatus.INVALID_RECORD, "stored_compiled_request_invalid")
    if (
        pipeline_request.cognitive_turn_id != pipeline.cognitive_turn_id
        or pipeline_request.focus_message_id != pipeline.focus_message_id
    ):
        return report(ReplayStatus.INVALID_RECORD, "stored_compiled_request_mismatch")

    if pipeline.orchestration is None:
        expected_policy = "strict-v0.1"
    elif _captured_wording(pipeline.orchestration):
        expected_policy = pipeline.orchestration.realization_policy
    else:
        expected_policy = validation_policy_for(pipeline.orchestration)  # v1: the separate conversational engine
    if stored.validation_policy != expected_policy:
        return report(ReplayStatus.MISMATCH, "realization_validation_policy_mismatch")
    maximum = (stored.request.provenance.configured_maximum_characters if stored.request is not None
               else fallback_maximum_characters)
    action = pipeline.action_selection
    try:
        compiled = RealizationCompilerV0().compile(
            action_result=action,
            inventory=realization_inventory(action, dialogue_context, *wording_content_of(pipeline),
                                            understanding_content_of(pipeline)),
            personality=pipeline.cognitive_turn.input_snapshot.action_personality,
            state=pipeline.cognitive_turn.dynamic_state_update.snapshot, language_tag=language_tag,
            current_user_utterance=_focus_user_utterance(pipeline_request),
            safety_basis=action.safety, configured_maximum_characters=maximum,
            conversation_context=conversation_context_of(pipeline, pipeline_request),
        )
    except RealizationExecutionError as exc:
        return report(ReplayStatus.MISMATCH, f"recompile_failed.{exc.code}")

    if isinstance(compiled, RealizationCompileSkip):
        if (compiled.status, compiled.skip) != (stored.status, stored.skip):
            return report(ReplayStatus.MISMATCH, "realization_skip_mismatch")
        replayed = stored
    else:
        if stored.request is None or compiled != stored.request:
            return report(ReplayStatus.MISMATCH, "realization_request_digest_mismatch")
        if _captured_wording(pipeline.orchestration):
            # The text must actually originate in the second captured proposal,
            # in addition to surviving the ordinary deterministic validator.
            from ..two_call.realization import CapturedRealizationModel
            from ..realization_v0.model_protocol import project_payload
            from ..realization_v0.engine import build_candidate
            try:
                captured = CapturedRealizationModel(pipeline.orchestration)
                if stored.model_provenance != captured.provenance:
                    return report(ReplayStatus.MISMATCH, "two_call_wording_provenance_mismatch")
                for attempt in stored.attempts:
                    if attempt.candidate is None:
                        continue
                    output = captured.generate(project_payload(compiled))
                    expected = build_candidate(compiled,attempt.attempt_number,output,captured.provenance)
                    if expected != attempt.candidate:
                        return report(ReplayStatus.MISMATCH, "two_call_wording_capture_mismatch")
            except (ValueError, RealizationProviderError):
                return report(ReplayStatus.MISMATCH, "two_call_wording_capture_invalid")
        validator = RealizationValidatorV0(policy=stored.validation_policy)
        attempts = []
        for attempt in stored.attempts:
            if attempt.validation is None:
                attempts.append(attempt)
                continue
            revalidated = (validator.validate(compiled, attempt.candidate) if attempt.candidate is not None
                           else validator.schema_failure(compiled, attempt.attempt_number))
            if revalidated != attempt.validation:
                return report(ReplayStatus.MISMATCH, "realization_revalidation_mismatch")
            attempts.append(attempt.model_copy(update={"validation": revalidated}))
        payload = copy.deepcopy(stored.model_dump(mode="json"))
        payload["request"] = compiled.model_dump(mode="json")
        payload["attempts"] = [item.model_dump(mode="json") for item in attempts]
        try:
            replayed = RealizationTraceV0.model_validate(payload)
        except ValidationError:
            return report(ReplayStatus.MISMATCH, "replayed_realization_trace_invalid")
    digest = realization_semantic_digest(replayed)
    if digest != realization_semantic_digest_value:
        return report(ReplayStatus.MISMATCH, "realization_semantic_digest_mismatch", digest)
    return report(ReplayStatus.MATCH, "realization_semantic_digest_match", digest)


__all__ = ["collaboration_content_of", "realization_inventory", "realize_record", "replay_realization",
           "requirement_content_of"]
