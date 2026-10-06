"""Bounded generation attempts and terminal outcome assembly (``realization-engine-v0.1``).

compile (pure) -> at most two single-call attempts -> independent validation
-> one immutable ``RealizationTraceV0`` with exactly one closed status:
``accepted_shadow``, ``rejected``, ``skipped_missing_content``,
``skipped_unsupported_action``, ``provider_unavailable``, or ``provider_failed``.

A retry receives only typed validation codes and refs, never free-form
coaching.  There is no fallback model: a missing model is
``provider_unavailable``.  No outcome is a quality label, and nothing here can
reach a live response.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import ValidationError

from ..contracts.actions import ActionPersonalityProfile, ActionSafetyBasis, ActionSelectionResult
from ..contracts.realization import (
    REALIZATION_ENGINE_VERSION, REALIZATION_MAX_ATTEMPTS, AttemptOutcome, PriorValidationCode, ProviderErrorCode,
    RealizationAttemptRecord, RealizationCandidateV0, RealizationContentInventory, RealizationFailurePhase,
    ProviderUnavailableReason, RealizationModelProvenance, RealizationRequestV0, RealizationStatus,
    RealizationTimings, RealizationTraceV0, RealizationValidationResult, candidate_id_for, trace_id_for,
)
from ..dynamic_state.models import StateSnapshot
from .compiler import RealizationCompileSkip, RealizationCompilerV0
from .errors import RealizationExecutionError, RealizationProviderError
from .model_protocol import PROTOCOL_VERSION, RealizationModel, RealizationModelOutput, project_payload
from .validator import RealizationValidatorV0

ENGINE_VERSION = REALIZATION_ENGINE_VERSION
# Provider codes that are not worth a second call within the same turn.
_NON_RETRYABLE = frozenset({ProviderErrorCode.UNSUPPORTED, ProviderErrorCode.OUTPUT_INVALID, ProviderErrorCode.REFUSED})


def build_candidate(
    request: RealizationRequestV0, attempt_number: int, output: RealizationModelOutput,
    provenance: RealizationModelProvenance,
) -> RealizationCandidateV0:
    """Derive the candidate id after generation from the request digest, attempt, and canonical payload."""

    fields = dict(
        request_ref=request.request_digest, attempt_number=attempt_number, text=output.text,
        move_alignments=output.move_alignments, content_alignments=output.content_alignments,
        omitted_move_refs=output.omitted_move_refs, model_provenance=provenance,
    )
    draft = RealizationCandidateV0.model_construct(candidate_id="pending", **fields)
    candidate_id = candidate_id_for(request.request_digest, attempt_number, draft.model_dump(mode="json"))
    return RealizationCandidateV0(candidate_id=candidate_id, **fields)


class RealizationEngineV0:
    version = ENGINE_VERSION

    def __init__(
        self,
        model: RealizationModel | None,
        *,
        max_attempts: int = REALIZATION_MAX_ATTEMPTS,
        compiler: RealizationCompilerV0 | None = None,
        validator: RealizationValidatorV0 | None = None,
        clock: Callable[[], float] = time.perf_counter,
        unavailable_reason: ProviderUnavailableReason | None = None,
    ) -> None:
        if model is not None and unavailable_reason is not None:
            raise ValueError("an available model has no unavailable reason")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not (
            1 <= max_attempts <= REALIZATION_MAX_ATTEMPTS
        ):
            raise ValueError("Realization V0 allows one or two generation attempts")
        self.model = model
        self.max_attempts = max_attempts
        self.compiler = compiler or RealizationCompilerV0()
        self.validator = validator or RealizationValidatorV0()
        self.clock = clock
        self.unavailable_reason = unavailable_reason

    def realize(
        self,
        *,
        action_result: ActionSelectionResult,
        inventory: RealizationContentInventory,
        personality: ActionPersonalityProfile,
        state: StateSnapshot,
        language_tag: str | None,
        current_user_utterance: str,
        safety_basis: ActionSafetyBasis,
        configured_maximum_characters: int,
    ) -> RealizationTraceV0:
        """Return one terminal trace.  Raises ``RealizationExecutionError`` only for deterministic failures."""

        started = self.clock()
        compiled = self.compiler.compile(
            action_result=action_result, inventory=inventory, personality=personality, state=state,
            language_tag=language_tag, current_user_utterance=current_user_utterance, safety_basis=safety_basis,
            configured_maximum_characters=configured_maximum_characters,
        )
        compile_ms = (self.clock() - started) * 1000
        turn, result_ref = action_result.cognitive_turn_id, action_result.result_id

        def timings(generation_ms: float = 0.0, validation_ms: float = 0.0) -> RealizationTimings:
            return RealizationTimings(compile_ms=compile_ms, generation_ms=generation_ms, validation_ms=validation_ms,
                                      total_ms=(self.clock() - started) * 1000)

        if isinstance(compiled, RealizationCompileSkip):
            return self._checked(RealizationTraceV0(
                validation_policy=self.validator.policy,
                trace_id=trace_id_for(result_ref, None), cognitive_turn_id=turn, action_result_ref=result_ref,
                status=compiled.status, skip=compiled.skip, timings=timings(),
            ))
        request = compiled
        if self.model is None:
            return self._checked(RealizationTraceV0(
                validation_policy=self.validator.policy,
                trace_id=trace_id_for(result_ref, request.request_digest), cognitive_turn_id=turn,
                action_result_ref=result_ref, status=RealizationStatus.PROVIDER_UNAVAILABLE, request=request,
                request_digest=request.request_digest, timings=timings(), unavailable_reason=self.unavailable_reason,
            ))
        provenance = self.model.provenance
        if not isinstance(provenance, RealizationModelProvenance) or provenance.protocol_version != PROTOCOL_VERSION:
            raise RealizationExecutionError(RealizationFailurePhase.MODEL_PROTOCOL, "provider_protocol_mismatch")

        attempts: list[RealizationAttemptRecord] = []
        generation_ms = validation_ms = 0.0
        prior: tuple[PriorValidationCode, ...] = ()
        for number in range(1, self.max_attempts + 1):
            try:
                payload = project_payload(request, prior)
            except (ValueError, ValidationError) as exc:
                raise RealizationExecutionError(RealizationFailurePhase.MODEL_PROTOCOL, "payload_projection_invalid") from exc
            call_started = self.clock()
            output: RealizationModelOutput | None = None
            provider_code: ProviderErrorCode | None = None
            try:
                output = self.model.generate(payload)  # exactly one provider call per attempt
                if not isinstance(output, RealizationModelOutput):
                    output = None
            except RealizationProviderError as exc:
                provider_code = exc.code  # None means the output failed the fixed schema
            except Exception:  # noqa: BLE001 - a provider bug is a typed outcome, never a crash or a leak
                provider_code = ProviderErrorCode.INTERNAL
            latency = (self.clock() - call_started) * 1000
            generation_ms += latency

            if provider_code is not None:
                attempts.append(RealizationAttemptRecord(
                    attempt_number=number, outcome=AttemptOutcome.PROVIDER_ERROR, prior_validation_codes=prior,
                    provider_error_code=provider_code, latency_ms=latency,
                ))
                prior = ()
                if provider_code in _NON_RETRYABLE:
                    break
                continue

            validate_started = self.clock()
            candidate: RealizationCandidateV0 | None = None
            if output is not None:
                try:
                    candidate = build_candidate(request, number, output, provenance)
                except (ValueError, ValidationError):
                    candidate = None
            validation: RealizationValidationResult = (
                self.validator.validate(request, candidate) if candidate is not None
                else self.validator.schema_failure(request, number)
            )
            validation_ms += (self.clock() - validate_started) * 1000
            accepted = validation.status == "accepted_for_shadow_review"
            attempts.append(RealizationAttemptRecord(
                attempt_number=number,
                outcome=AttemptOutcome.CANDIDATE_ACCEPTED if accepted else AttemptOutcome.CANDIDATE_REJECTED,
                prior_validation_codes=prior, candidate=candidate, validation=validation, latency_ms=latency,
            ))
            if accepted:
                break
            prior = tuple(PriorValidationCode(code=item.code, subject_ref=item.subject_ref)
                          for item in validation.violations)

        last = attempts[-1]
        rejected = [item for item in attempts if item.outcome == AttemptOutcome.CANDIDATE_REJECTED]
        if last.outcome == AttemptOutcome.CANDIDATE_ACCEPTED:
            status, final = RealizationStatus.ACCEPTED_SHADOW, last
        elif rejected:
            status, final = RealizationStatus.REJECTED, rejected[-1]
        else:
            status, final = RealizationStatus.PROVIDER_FAILED, None
        return self._checked(RealizationTraceV0(
            validation_policy=self.validator.policy,
            trace_id=trace_id_for(result_ref, request.request_digest), cognitive_turn_id=turn,
            action_result_ref=result_ref, status=status, request=request, request_digest=request.request_digest,
            attempts=tuple(attempts), final_candidate=final.candidate if final else None,
            validation=final.validation if final else None, model_provenance=provenance,
            timings=timings(generation_ms, validation_ms),
        ))

    @staticmethod
    def _checked(trace: RealizationTraceV0) -> RealizationTraceV0:
        try:
            if RealizationTraceV0.model_validate(trace.model_dump(mode="json")) != trace:
                raise ValueError("trace changed across a JSON round trip")
        except ValueError as exc:
            raise RealizationExecutionError(RealizationFailurePhase.TRACE_VALIDATION, "trace_not_round_trip_safe") from exc
        return trace


__all__ = ["ENGINE_VERSION", "RealizationEngineV0", "build_candidate"]
