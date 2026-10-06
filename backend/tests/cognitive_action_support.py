"""Shared test inputs for the Action Selection stage of the cognitive turn.

The action runtime is always projected from the same trusted runtime the
Appraisal stage receives; the personality is the explicit, versioned unknown
profile, which contributes numerically neutral influence.
"""

from __future__ import annotations

from app.ai.Cognitive.system.v0.appraisal.models import RuntimeContext
from app.ai.Cognitive.system.v0.contracts.actions import ActionPersonalityProfile, ActionRuntimeContext
from app.ai.Cognitive.system.v0.contracts.safety import (
    APPLICATION_SAFETY_POLICY_VERSION, ApplicationSafetyDecision, SafetyDecisionProvenance, SafetyDisposition,
)

UNKNOWN_ACTION_MAPPER = "action-personality-unknown-v0"


# The typed decision each upstream legacy label stands for in these tests.  An
# absent upstream label models the ordinary case: the application evaluated
# the turn and allowed it.
_DISPOSITION_FOR_LEGACY = {
    None: SafetyDisposition.ALLOW, "blocked": SafetyDisposition.BLOCK_COMMITMENTS, "unknown": SafetyDisposition.UNKNOWN,
}


def safety_decision(
    runtime_snapshot_ref: str, disposition: SafetyDisposition | str = SafetyDisposition.ALLOW, *,
    policy_version: str = APPLICATION_SAFETY_POLICY_VERSION,
) -> ApplicationSafetyDecision:
    disposition = SafetyDisposition(disposition)
    provenance = (SafetyDecisionProvenance.RUNTIME_UNAVAILABLE if disposition == SafetyDisposition.UNAVAILABLE
                  else SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY)
    return ApplicationSafetyDecision(
        decision_id=f"{runtime_snapshot_ref}:safety-decision", policy_version=policy_version, disposition=disposition,
        provenance=provenance, producer_ref=f"{runtime_snapshot_ref}:safety-evaluation",
        runtime_snapshot_ref=runtime_snapshot_ref,
    )


def safety_decision_for_legacy(runtime_snapshot_ref: str, label: str | None) -> ApplicationSafetyDecision:
    return safety_decision(runtime_snapshot_ref, _DISPOSITION_FOR_LEGACY[label])


def action_personality(core_memory_ref: str) -> ActionPersonalityProfile:
    return ActionPersonalityProfile(
        profile_id="action-profile-unknown", mapper_version=UNKNOWN_ACTION_MAPPER,
        source_core_memory_ref=core_memory_ref, status="unknown",
    )


def action_runtime(
    runtime: RuntimeContext, *, runtime_snapshot_ref: str, response_obligation_ref: str,
) -> ActionRuntimeContext:
    return ActionRuntimeContext(
        runtime_snapshot_ref=runtime_snapshot_ref, response_obligation="required",
        response_obligation_ref=response_obligation_ref,
        capability_snapshot_ref=runtime.capability_snapshot.snapshot_id,
        available_capabilities=runtime.available_actions, authorization=runtime.authorization,
        consent=runtime.consent,
        safety_decision=safety_decision_for_legacy(runtime_snapshot_ref, runtime.safety_decision),
        external_export_control=runtime.external_export_control, policy_records=runtime.policy_records,
    )


def action_inputs(
    runtime: RuntimeContext, *, runtime_snapshot_ref: str, core_memory_ref: str,
    response_obligation_ref: str = "response-obligation:test",
) -> dict[str, object]:
    return {
        "action_runtime": action_runtime(
            runtime, runtime_snapshot_ref=runtime_snapshot_ref,
            response_obligation_ref=response_obligation_ref,
        ),
        "action_personality": action_personality(core_memory_ref),
    }


TURN_CLOCK = (0.0, 0.001, 0.003, 0.004)
