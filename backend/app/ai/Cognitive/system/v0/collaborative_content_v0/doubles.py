"""Offline structured doubles for the collaborative-content protocol.

``ScriptedCollaborationModel`` answers each payload through a reviewed
function (tests and the frozen evaluation script); ``RecordedCollaborationModel``
returns exactly the candidate a stored trace recorded for each request frame,
so replay re-runs the deterministic compiler without any model call.  Neither
is ever a fallback for a failed provider.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .model_protocol import (
    CollaborationModelOutput, CollaborationModelPayload, CollaborationProviderError, SourceKind,
)


def request_ref_of(payload: CollaborationModelPayload) -> str:
    """The current-turn request or answer frame a payload is for."""

    refs = [item.ref for item in payload.sources if item.current_turn and item.kind in {
        SourceKind.USER_REQUEST, SourceKind.USER_ANSWER,
    }]
    if len(refs) != 1:
        raise ValueError("a collaboration payload names exactly one request frame")
    return refs[0]


class ScriptedCollaborationModel:
    version = "scripted-collaboration-double-v0.1"
    provenance = None

    def __init__(self, script: Callable[[CollaborationModelPayload], Mapping[str, Any] | CollaborationProviderError]):
        self.script = script
        self.payloads: list[CollaborationModelPayload] = []

    def generate(self, payload: CollaborationModelPayload) -> CollaborationModelOutput:
        self.payloads.append(payload)
        result = self.script(payload)
        if isinstance(result, CollaborationProviderError):
            raise result
        return CollaborationModelOutput.model_validate(dict(result))


class RecordedCollaborationModel:
    """Replays recorded candidates keyed by request frame ref; never calls a provider.

    ``failures`` replays a recorded provider failure code for a frame; the
    recorded ``provenance`` (one provider per stored trace) is reported
    unchanged so a replayed outcome is byte-comparable with the stored one.
    """

    version = "recorded-collaboration-candidate-v0.1"

    def __init__(self, candidates: Mapping[str, Mapping[str, Any]], *, failures: Mapping[str, Any] | None = None,
                 provenance=None) -> None:
        self.candidates = dict(candidates)
        self.failures = dict(failures or {})
        self.provenance = provenance

    def generate(self, payload: CollaborationModelPayload) -> CollaborationModelOutput:
        from ..contracts.collaboration import CollaborationFailureCode

        ref = request_ref_of(payload)
        if ref in self.failures:
            raise CollaborationProviderError(CollaborationFailureCode(self.failures[ref]))
        candidate = self.candidates.get(ref)
        if candidate is None:
            raise CollaborationProviderError(CollaborationFailureCode.PROVIDER_NOT_CONFIGURED)
        return CollaborationModelOutput.model_validate(dict(candidate))


def recorded_model_for(collaboration) -> RecordedCollaborationModel | None:
    """The replay double for a stored trace's collaboration stage (``None`` if it made no provider call)."""

    if collaboration is None:
        return None
    frames = {item.goal_ref: item.frame_ref for item in collaboration.workspace.targets}
    candidates, failures, provenances = {}, {}, set()
    for outcome in collaboration.outcomes:
        if outcome.status == "skipped":
            continue
        frame = frames[outcome.goal_ref]
        if outcome.provenance is not None:
            provenances.add(outcome.provenance.model_dump_json())
        if outcome.candidate is not None:
            candidates[frame] = dict(outcome.candidate)
        elif outcome.failure is not None and outcome.failure.stage == "provider":
            failures[frame] = outcome.failure.code.value
    if not candidates and not failures:
        return None
    if len(provenances) > 1:
        raise ValueError("a stored trace records one collaboration provider")
    provenance = None
    if provenances:
        from ..contracts.collaboration import CollaborationProviderProvenance

        provenance = CollaborationProviderProvenance.model_validate_json(provenances.pop())
    return RecordedCollaborationModel(candidates, failures=failures, provenance=provenance)


def empty_output(**overrides) -> dict:
    return {"title": None, "operations": [], "content_items": [], "inquiries": [], **overrides}


def operation(op: str, *, source_refs, element_id=None, section_id=None, integer_value=None, integer_class=None,
              constraint=None, dish=None, text=None, text_class=None, heading=None) -> dict:
    return {"op": op, "element_id": element_id, "section_id": section_id, "integer_value": integer_value,
            "integer_class": integer_class, "constraint": constraint, "dish": dish, "text": text,
            "text_class": text_class, "heading": heading, "source_refs": list(source_refs)}


def item(role: str, statement: str, anchors, *, content_class="generated_proposal", claim_kind="artifact_structure",
         subject_ids=(), source_refs, numbers=()) -> dict:
    return {"role": role, "statement": statement, "anchors": list(anchors), "content_class": content_class,
            "claim_kind": claim_kind, "subject_ids": list(subject_ids), "source_refs": list(source_refs),
            "numbers": list(numbers)}


def inquiry(target_id: str, *, asked_about: str, source_refs, options=(), purpose="discover_preference",
            expected_use="set_constraint_reason", target_kind="artifact_constraint",
            criticality="quality_improving", sensitivity="ordinary") -> dict:
    return {"target_kind": target_kind, "target_id": target_id,
            "question_kind": "choice" if len(options) >= 2 else "open_set", "purpose": purpose,
            "expected_use": expected_use, "criticality": criticality, "sensitivity": sensitivity,
            "asked_about": asked_about, "answer_options": list(options), "source_refs": list(source_refs)}


__all__ = [
    "RecordedCollaborationModel", "ScriptedCollaborationModel", "empty_output", "inquiry", "item", "operation",
    "recorded_model_for", "request_ref_of",
]
