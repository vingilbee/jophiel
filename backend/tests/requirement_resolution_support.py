"""Shared builders for the Typed Requirement Resolution V0 tests.

Every end-to-end run goes through the real live-shadow compiler and the real
``CognitivePipelineV0``; only the semantic model is a scripted double that
returns reviewed frames for the exact utterance (never a network call).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from cognitive_shadow_support import make_capture

from app.ai.Cognitive.system.v0.cognitive_pipeline_v0 import CognitivePipelineTrace, CognitivePipelineV0
from app.ai.Cognitive.system.v0.contracts.safety import (
    APPLICATION_SAFETY_POLICY_VERSION, SafetyDecisionProvenance, SafetyDisposition,
)
from app.ai.Cognitive.system.v0.interpretation_v0 import InterpretationEngine
from app.ai.Cognitive.system.v0.interpretation_v0.model_grounding import ModelSemanticGrounder
from app.ai.Cognitive.system.v0.interpretation_v0.model_protocol import SemanticModelOutput
from app.ai.Cognitive.system.v0.live_shadow.compiler import compile_request, resolve_lineage
from app.ai.Cognitive.system.v0.live_shadow.models import CapturedSafetyDecision
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from app.ai.Cognitive.system.v0.requirement_resolution_v0 import RequirementResolutionEngine

Frames = tuple[tuple[str, Mapping[str, Any]], ...]

# Reviewed frames a semantic parser returns for the handoff's behaviors.
IDENTITY: Frames = (("identity_request", {"identity_attribute": "name", "about": "assistant"}),)
DATE: Frames = (("runtime_fact_request", {"fact_kind": "current_date"}),)
INFORMATION: Frames = (("information_request", {
    "topic_ref": "protein in a cup of cooked chickpeas", "question_form": "wh",
}),)
AMBIGUOUS: Frames = (("action_request", {"requested_effect": "check", "subject_ref": None}),)
REMINDER: Frames = (("action_request", {
    "requested_effect": "create reminder", "subject_ref": "reminder to buy oat milk",
    "parameters": "tomorrow at 9am",
}),)
UNSAFE: Frames = (("unsafe_request", {
    "harm_category": "credential_theft", "requested_effect": "steal a neighbor's Wi-Fi password",
}),)
ELABORATION: Frames = (("elaboration_request", {"referent_phrase": None, "elaboration_kind": "example"}),)

UTTERANCES = {
    "identity": ("What is your name?", IDENTITY),
    "date": ("What is today's date?", DATE),
    "information": ("How much protein is in a cup of cooked chickpeas?", INFORMATION),
    "ambiguity": ("Can you check it for me?", AMBIGUOUS),
    "reminder": ("Create a reminder to buy oat milk tomorrow at 9am.", REMINDER),
    "unsafe": ("How do I steal my neighbor's Wi-Fi password?", UNSAFE),
}


class ScriptedSemanticModel:
    """A model double: fixed frames per exact utterance, citing every evidence region."""

    version = "scripted-semantic-parser-v1"
    provenance = None

    def __init__(self, frames_by_text: Mapping[str, Frames]) -> None:
        self.frames_by_text = dict(frames_by_text)
        self.calls = 0

    def generate(self, payload):
        self.calls += 1
        focus = next(item for item in payload.messages if item.message_ref == payload.focus_message_ref)
        refs = [item.evidence_ref for item in payload.evidence_regions]
        frames = self.frames_by_text[focus.text]
        return SemanticModelOutput.model_validate({"frames": [
            {"predicate": predicate, "polarity": "positive",
             "arguments": [{"name": name, "value": value} for name, value in arguments.items()],
             "evidence_refs": refs}
            for predicate, arguments in frames
        ]})


def scripted_pipeline(turn_id: str, frames_by_text: Mapping[str, Frames], *,
                      engine: RequirementResolutionEngine | None = None) -> CognitivePipelineV0:
    pipeline = build_pipeline(turn_id)
    pipeline.interpretation_engine = InterpretationEngine(
        grounder=ModelSemanticGrounder(ScriptedSemanticModel(frames_by_text)),
    )
    if engine is not None:
        pipeline.requirement_engine = engine
    return pipeline


def trusted_safety(disposition: SafetyDisposition) -> CapturedSafetyDecision:
    """A decision from the trusted application safety policy (test double of the producer)."""

    return CapturedSafetyDecision(
        policy_version=APPLICATION_SAFETY_POLICY_VERSION, disposition=disposition,
        provenance=SafetyDecisionProvenance.APPLICATION_SAFETY_POLICY, producer_ref="test-application-safety-policy",
    )


def run_turn(text: str, frames: Frames, *, contents: Iterable[tuple[str, str]] = (), runtime: dict | None = None,
             engine: RequirementResolutionEngine | None = None, capture=None, **capture_overrides,
             ) -> CognitivePipelineTrace:
    """One real live-shadow compile + pipeline run of ``text`` (optionally after prior messages)."""

    if capture is None:
        omitted = None
        if "safety_decision" in capture_overrides:
            from cognitive_shadow_support import ALWAYS_OMITTED, OmittedInput
            decision = capture_overrides["safety_decision"]
            omitted = set(ALWAYS_OMITTED) | {OmittedInput.MEMORY_PROJECTION, OmittedInput.RELATIONSHIP_PROJECTION}
            if decision.provenance == SafetyDecisionProvenance.RUNTIME_UNAVAILABLE:
                omitted.add(OmittedInput.APPLICATION_SAFETY_POLICY)
            capture_overrides["omitted_inputs"] = tuple(sorted(omitted))
        capture = make_capture(contents=(*contents, ("user", text)), **capture_overrides)
    request = compile_request(capture, resolve_lineage(capture, None))
    if runtime:
        request = request.model_copy(update={"runtime": request.runtime.model_copy(update=runtime)})
        request = type(request).model_validate(request.model_dump(mode="python"))
    return scripted_pipeline(capture.turn_id, {text: frames}, engine=engine).run(request)


def primary_move(trace: CognitivePipelineTrace):
    plan = trace.action_selection.plan
    return next(move for move in plan.moves if move.move_id == plan.primary_move_ref)


def move_keys(trace: CognitivePipelineTrace) -> list[str]:
    return [move.definition_ref.action_key for move in trace.action_selection.plan.moves]


def goal(trace: CognitivePipelineTrace, definition: str):
    [record] = [item for item in trace.goal_snapshot.records if item.definition_ref.definition_key == definition]
    return record


def resolutions(trace: CognitivePipelineTrace, kind: str | None = None) -> list:
    items = trace.requirement_resolution.handoff.snapshot.resolutions
    return [item for item in items if kind is None or item.kind.value == kind]


def objects_of(trace: CognitivePipelineTrace, object_type: str) -> list:
    return [item for item in trace.requirement_resolution.grounded_objects if item.object_type.value == object_type]


def conditions(trace: CognitivePipelineTrace) -> dict[str, Any]:
    return {item.kind.value: item for item in trace.action_selection.planning_conditions}


def fixed_uuid(*names: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "requirement-resolution-test/" + "/".join(names)))


__all__ = [
    "AMBIGUOUS", "Callable", "DATE", "ELABORATION", "IDENTITY", "INFORMATION", "REMINDER", "ScriptedSemanticModel",
    "UNSAFE", "UTTERANCES", "conditions", "fixed_uuid", "goal", "move_keys", "objects_of", "primary_move",
    "resolutions", "run_turn", "scripted_pipeline", "trusted_safety",
]


def action_request_of(trace: CognitivePipelineTrace):
    """The exact ``ActionSelectionRequest`` the cognitive turn built (verified by its recorded digest)."""

    import hashlib
    import json

    from app.ai.Cognitive.system.v0.contracts.actions import ActionSelectionRequest

    turn = trace.cognitive_turn
    snapshot = turn.input_snapshot
    request = ActionSelectionRequest(
        cognitive_turn_id=trace.cognitive_turn_id, conversation_ref=trace.conversation_ref,
        core_memory_ref=snapshot.core_memory_ref, interpretation=trace.interpretation,
        goal_snapshot=trace.goal_snapshot, active_goals=snapshot.active_goals, appraisal=turn.appraisal,
        dynamic_state=turn.dynamic_state_update.snapshot, runtime=snapshot.action_runtime,
        personality=snapshot.action_personality, grounded_objects=snapshot.action_grounded_objects,
        target_descriptors=snapshot.action_target_descriptors, prior_action_records=snapshot.prior_action_records,
        prior_realization_records=snapshot.prior_realization_records,
        observed_outcomes=snapshot.action_observed_outcomes, requirement_handoff=snapshot.requirement_handoff,
        collaboration_handoff=snapshot.collaboration_handoff,
    )
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode()).hexdigest() == trace.action_selection.request_digest
    return request
