"""Shared inputs for the Realization V0 tests.

Plans come from two real sources: the accepted Action V0 design cases (run
through the real Action engine) and real live-shadow observations of short
messages (run through the real CognitivePipelineV0 with the real Personality
Runtime Mapper projection).  Content inventories are authored per test; the
fixture adapter lives here, never in production code.
"""

from __future__ import annotations

import copy
import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from action_v0_support import request_for, result_for
from cognitive_shadow_support import COLUMBINA_CORE_GROUPS, make_capture

from app.ai.Cognitive.system.v0.contracts.actions import ActionPersonalityProfile, PersonalityStatus
from app.ai.Cognitive.system.v0.contracts.realization import (
    RealizationContentInventory, RealizationModelProvenance, RealizationRequestV0,
)
from app.ai.Cognitive.system.v0.live_shadow import ObservationContext, observe
from app.ai.Cognitive.system.v0.personality_runtime_v0 import PersonalityRuntimeMapperV0, build_source
from app.ai.Cognitive.system.v0.realization_v0 import (
    RealizationCompilerV0, RealizationEngineV0, output_from_excerpts, project_dialogue_structure,
)
from app.ai.Cognitive.system.v0.realization_v0.errors import RealizationProviderError

DATA = Path(__file__).resolve().parents[1] / "app/ai/Cognitive/system/v0/data"
DESIGN_CASES = DATA / "realization_design_cases.json"


# ---------------------------------------------------------------------------
# Plan sources
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def mapper_projection():
    groups = {key: COLUMBINA_CORE_GROUPS[key] for key in ("interpretation_biases", "perception_priors", "drives",
                                                          "action_style")}
    return PersonalityRuntimeMapperV0().compile(build_source(name="columbina", version=1, schema_version=1, groups=groups))


@lru_cache(maxsize=None)
def live_record(text: str):
    capture = make_capture(contents=(("user", text),), personality=mapper_projection(),
                           turn_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"realization-test:{text}")))
    record = observe(capture, None, ObservationContext(
        job_id=capture.capture_id, attempt=1, capture_digest="0" * 64, started_at="2026-09-26T00:00:00+00:00",
    ))
    assert record.status == "completed", record.failure
    return record


def live_inputs(text: str) -> dict[str, Any]:
    trace = live_record(text).pipeline_trace
    action = trace.action_selection
    return dict(
        action_result=action, inventory=project_dialogue_structure(action),
        personality=trace.cognitive_turn.input_snapshot.action_personality,
        state=trace.cognitive_turn.dynamic_state_update.snapshot, language_tag="en",
        current_user_utterance=text, safety_basis=action.safety,
    )


def case_inputs(case_id: str, items: list[dict] | tuple = (), *, generic: bool = True,
                personality: ActionPersonalityProfile | None = None) -> dict[str, Any]:
    """Inputs for an Action design case with an authored inventory (plus generic dialogue structure)."""

    result = result_for(case_id)
    request = request_for(case_id)
    return dict(
        action_result=result, inventory=inventory_for(result, items, generic=generic),
        personality=personality or known_personality(request.personality.source_core_memory_ref),
        state=request.dynamic_state, language_tag="en",
        current_user_utterance=f"Realization design case: {case_id}", safety_basis=result.safety,
    )


def known_personality(core_ref: str, **values: float) -> ActionPersonalityProfile:
    traits = {"curiosity": 0.88, "warmth": 0.8, "playfulness": 0.58, "directness": 0.62} | values
    return ActionPersonalityProfile(
        profile_id="test-realization-personality", mapper_version="test-realization-mapper-v1",
        source_core_memory_ref=core_ref, status=PersonalityStatus.KNOWN, **traits,
    )


def inventory_for(result, items, *, generic: bool = True) -> RealizationContentInventory:
    turn = result.cognitive_turn_id
    authored = [dict(item, cognitive_turn_id=item.get("cognitive_turn_id", turn)) for item in items]
    base = project_dialogue_structure(result) if generic else None
    payload = {
        "inventory_id": f"test-inventory:{result.result_id}",
        "cognitive_turn_id": turn,
        "producer_refs": sorted({*(base.producer_refs if base else ()), "test-authored-content"}),
        "items": [*(item.model_dump(mode="json") for item in (base.items if base else ())), *authored],
    }
    return RealizationContentInventory.model_validate(payload)


def compile_request(inputs: dict[str, Any], maximum: int = 1200):
    return RealizationCompilerV0().compile(**inputs, configured_maximum_characters=maximum)


# ---------------------------------------------------------------------------
# Authored content helpers (JSON-shaped, as a trusted producer would project)
# ---------------------------------------------------------------------------

def proposition(content_id: str, represents: tuple[str, ...], subject: str, *, predicate: str = "fact.statement",
                arguments: tuple[dict, ...] = (), source: str = "reviewed_system_knowledge",
                status: str = "grounded", attribution: str = "trusted_source", certainty: str = "exact",
                polarity: str = "positive", modality: str = "asserted", anchors: tuple[str, ...] = (), **extra) -> dict:
    return {
        "kind": "grounded_proposition", "content_id": content_id, "source_kind": source,
        "source_refs": [f"source:{content_id}"], "represents_refs": list(represents),
        "authorized_for_realization": True, "epistemic_status": status, "attribution": attribution,
        "surface_anchors": list(anchors), "subject": {"ref": f"entity:{content_id}", "surface_anchor": subject},
        "predicate": predicate, "arguments": list(arguments), "polarity": polarity, "modality": modality,
        "certainty": certainty, **extra,
    }


def reference(content_id: str, represents: tuple[str, ...], anchors: tuple[str, ...], *, kind: str = "topic",
              source: str = "trusted_runtime_observation", attribution: str = "runtime", status: str = "observed",
              **extra) -> dict:
    return {
        "kind": "dialogue_reference", "content_id": content_id, "source_kind": source,
        "source_refs": [f"source:{content_id}"], "represents_refs": list(represents),
        "authorized_for_realization": True, "epistemic_status": status, "attribution": attribution,
        "surface_anchors": list(anchors), "reference_kind": kind, "target_ref": represents[0],
        "reference_form": "anchored", **extra,
    }


def gap(content_id: str, represents: tuple[str, ...], anchor: str, *, gap_kind: str = "no_grounded_answer") -> dict:
    return {
        "kind": "knowledge_gap", "content_id": content_id, "source_kind": "retrieval_no_result",
        "source_refs": [f"retrieval:{content_id}"], "represents_refs": list(represents),
        "authorized_for_realization": True, "epistemic_status": "unknown", "attribution": "runtime",
        "topic_ref": f"topic:{content_id}", "surface_anchor": anchor, "gap_kind": gap_kind,
    }


def decimal_arg(value: str, unit: str | None = None, role: str = "value") -> dict:
    return {"role": role, "value_type": "decimal", "value": value, "unit": unit}


def entity_arg(value: str, anchor: str, role: str = "value") -> dict:
    return {"role": role, "value_type": "entity", "value": value, "surface_anchor": anchor}


# ---------------------------------------------------------------------------
# Fake models
# ---------------------------------------------------------------------------

def provenance(model_id: str = "fake-realizer-1") -> RealizationModelProvenance:
    return RealizationModelProvenance(provider="fake", model_id=model_id, temperature="0.000000",
                                      max_output_tokens=600, timeout_seconds=20)


def excerpt_output(payload, text: str, move_excerpts: dict[int, str] | None = None,
                   content_excerpts: dict[str, str] | None = None, *, whole: bool = False):
    """Provider-shaped output: each move aligned to its excerpt (1-based order) or the whole text."""

    moves = [{"move_ref": move.move_ref,
              "excerpt": text if whole or move_excerpts is None else move_excerpts[move.order]}
             for move in payload.moves]
    contents = content_excerpts
    if contents is None:
        contents = {}
        for move in payload.moves:
            excerpt = text if whole or move_excerpts is None else move_excerpts[move.order]
            for binding in move.content_refs:
                contents.setdefault(binding.content_ref, excerpt)
    data = {"text": text, "move_alignments": moves,
            "content_alignments": [{"content_ref": ref, "excerpt": value} for ref, value in contents.items()],
            "omitted_move_refs": []}
    return output_from_excerpts(data, payload)


class ScriptedModel:
    """Returns scripted provider outputs in order; records every payload it receives."""

    def __init__(self, *responses, model_id: str = "fake-realizer-1") -> None:
        self.responses = list(responses)
        self.payloads: list = []
        self._provenance = provenance(model_id)

    @property
    def provenance(self) -> RealizationModelProvenance:
        return self._provenance

    def generate(self, payload):
        self.payloads.append(payload)
        response = self.responses[min(len(self.payloads), len(self.responses)) - 1]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(payload)
        text, moves, contents = (response + (None, None))[:3] if isinstance(response, tuple) else (response, None, None)
        return excerpt_output(payload, text, moves, contents, whole=moves is None)


def schema_invalid() -> RealizationProviderError:
    return RealizationProviderError(None, schema_invalid=True)


def realize(inputs: dict[str, Any], model, *, max_attempts: int = 2, maximum: int = 1200):
    return RealizationEngineV0(model, max_attempts=max_attempts).realize(**inputs, configured_maximum_characters=maximum)


def validate_text(request: RealizationRequestV0, text: str, moves: dict[int, str] | None = None,
                  contents: dict[str, str] | None = None):
    """Validate one scripted text against a compiled request (attempt 1)."""

    from app.ai.Cognitive.system.v0.realization_v0 import RealizationValidatorV0, build_candidate, project_payload

    payload = project_payload(request)
    output = excerpt_output(payload, text, moves, contents, whole=moves is None)
    candidate = build_candidate(request, 1, output, provenance())
    return RealizationValidatorV0().validate(request, candidate)


@lru_cache(maxsize=1)
def design_document() -> dict:
    return json.loads(DESIGN_CASES.read_text(encoding="utf-8"))


def design_cases() -> list[dict]:
    return copy.deepcopy(design_document()["cases"])
