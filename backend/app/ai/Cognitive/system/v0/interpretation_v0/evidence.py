"""Build and validate the Interpretation V0 evidence registry."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..contracts.interpretation import InterpretationEvidence, InterpretationEvidenceKind
from ..perception_v0.clauses import scan_clauses
from ..perception_v0.evidence import resolve_json_pointer, stable_id
from ..perception_v0.models import TextSpanEvidence
from .models import InterpretationRequest

# v0.2 adds span evidence for each clause, clause body, and discourse-prefix
# unit of the focus message, so compositional frames cite their own regions.
EVIDENCE_VALIDATOR_VERSION = "interpretation-evidence-v0.2"


def focus_region_spans(content: str) -> tuple[tuple[int, int], ...]:
    """Every clause, clause-body, and prefix-unit span of the focus text, in order."""

    spans: list[tuple[int, int]] = []
    for clause in scan_clauses(content):
        for _, start, end in clause.prefix_units:
            spans.append((start, end))
        units = clause.prefix_units
        for index, (_, start, _) in enumerate(units):
            # Every contiguous run of prefix units, and each run through the body.
            for _, _, end in units[index:]:
                spans.append((start, end))
            spans.append((start, clause.end))
        spans.append((clause.body_start, clause.end))
        spans.append((clause.start, clause.end))
    whole = (0, len(content))
    return tuple(dict.fromkeys(span for span in spans if span != whole and span[0] < span[1]))


def _span_evidence_id(cognitive_turn_id: str, message_id: str, start: int, end: int) -> str:
    return stable_id("interpretation-evidence", cognitive_turn_id, "message", message_id, start, end)


class EvidenceRegistryBuilder:
    def build(self, request: InterpretationRequest) -> tuple[InterpretationEvidence, ...]:
        records: list[InterpretationEvidence] = []
        for activation in request.perception.cue_activations:
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id,
                    request.perception.perception_event_id, "cue", activation.cue_key,
                ),
                source_kind=InterpretationEvidenceKind.PERCEPTION_CUE,
                source_ref=f"{request.perception.perception_event_id}:{activation.cue_key}",
                cue_key=activation.cue_key,
                observation_refs=activation.observation_refs,
            ))
        for message in request.messages:
            if not message.content:
                continue
            span = TextSpanEvidence(
                message_id=message.message_id, start=0, end=len(message.content),
                quoted_text=message.content,
            )
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id,
                    "message", message.message_id, 0, len(message.content),
                ),
                source_kind=InterpretationEvidenceKind.MESSAGE_SPAN,
                source_ref=message.message_id,
                message_id=message.message_id,
                span=span,
            ))
        focus = request.focus_message
        for start, end in focus_region_spans(focus.content):
            records.append(InterpretationEvidence(
                evidence_id=_span_evidence_id(request.cognitive_turn_id, focus.message_id, start, end),
                source_kind=InterpretationEvidenceKind.MESSAGE_SPAN,
                source_ref=focus.message_id,
                message_id=focus.message_id,
                span=TextSpanEvidence(
                    message_id=focus.message_id, start=start, end=end, quoted_text=focus.content[start:end],
                ),
            ))
        if len(request.messages) > 1:
            ids = tuple(message.message_id for message in request.messages)
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id, "sequence", ids,
                ),
                source_kind=InterpretationEvidenceKind.MESSAGE_SEQUENCE,
                source_ref=request.perception.perception_event_id,
                message_ids=ids,
            ))
        runtime_json = request.runtime.model_dump(mode="json")
        for pointer in (
            "/available_capabilities", "/attachments", "/task_contract",
            "/authorization", "/safety_decision", "/last_tool_event",
        ):
            value = resolve_json_pointer(runtime_json, pointer)
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id,
                    request.runtime.snapshot_id, pointer, value,
                ),
                source_kind=InterpretationEvidenceKind.RUNTIME_FACT,
                source_ref=f"{request.runtime.snapshot_id}:{pointer}",
                runtime_snapshot_ref=request.runtime.snapshot_id,
                json_pointer=pointer,
                value=value,
            ))
        for memory in request.memories:
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id,
                    "memory", memory.memory_id, memory.status, memory.version,
                ),
                source_kind=InterpretationEvidenceKind.MEMORY_RECORD,
                source_ref=memory.memory_id,
                memory_ref=memory.memory_id,
            ))
        if request.relationship is not None:
            records.append(InterpretationEvidence(
                evidence_id=stable_id(
                    "interpretation-evidence", request.cognitive_turn_id,
                    "relationship", request.relationship.relationship_ref,
                    request.relationship.status,
                ),
                source_kind=InterpretationEvidenceKind.RELATIONSHIP_RECORD,
                source_ref=request.relationship.relationship_ref,
            ))
        return tuple(records)


def evidence_by_cue(
    records: tuple[InterpretationEvidence, ...],
) -> Mapping[str, InterpretationEvidence]:
    return {
        item.cue_key: item for item in records
        if item.source_kind == InterpretationEvidenceKind.PERCEPTION_CUE and item.cue_key
    }


def focus_message_evidence(
    records: tuple[InterpretationEvidence, ...], focus_message_id: str,
) -> InterpretationEvidence | None:
    return next((
        item for item in records
        if item.source_kind == InterpretationEvidenceKind.MESSAGE_SPAN
        and item.message_id == focus_message_id
    ), None)


def span_evidence(
    records: tuple[InterpretationEvidence, ...], message_id: str, start: int, end: int,
) -> InterpretationEvidence | None:
    """The registry record for exactly this span of ``message_id``."""

    return next((
        item for item in records
        if item.source_kind == InterpretationEvidenceKind.MESSAGE_SPAN
        and item.message_id == message_id and item.span is not None
        and (item.span.start, item.span.end) == (start, end)
    ), None)


def runtime_evidence(
    records: tuple[InterpretationEvidence, ...], pointer: str,
) -> InterpretationEvidence:
    return next(
        item for item in records
        if item.source_kind == InterpretationEvidenceKind.RUNTIME_FACT
        and item.json_pointer == pointer
    )


def validate_evidence_registry(
    records: tuple[InterpretationEvidence, ...], request: InterpretationRequest,
) -> None:
    canonical = EvidenceRegistryBuilder().build(request)
    if records != canonical:
        raise ValueError("interpretation evidence registry differs from canonical request evidence")
    ids = tuple(item.evidence_id for item in records)
    if len(ids) != len(set(ids)):
        raise ValueError("interpretation evidence ids must be unique")
    messages = {message.message_id: message for message in request.messages}
    activations = {item.cue_key: item for item in request.perception.cue_activations}
    memories = {item.memory_id: item for item in request.memories}
    runtime_json = request.runtime.model_dump(mode="json")
    for item in records:
        if item.source_kind == InterpretationEvidenceKind.MESSAGE_SPAN:
            assert item.span is not None and item.message_id is not None
            message = messages.get(item.message_id)
            if message is None or item.span.end > len(message.content):
                raise ValueError("interpretation message evidence does not resolve")
            if message.content[item.span.start:item.span.end] != item.span.quoted_text:
                raise ValueError("interpretation message span does not match immutable content")
        elif item.source_kind == InterpretationEvidenceKind.MESSAGE_SEQUENCE:
            if item.message_ids != tuple(message.message_id for message in request.messages):
                raise ValueError("interpretation message-sequence evidence changed input order")
        elif item.source_kind == InterpretationEvidenceKind.PERCEPTION_CUE:
            activation = activations.get(item.cue_key or "")
            if activation is None or activation.observation_refs != item.observation_refs:
                raise ValueError("interpretation cue evidence does not match the exact handoff")
        elif item.source_kind == InterpretationEvidenceKind.RUNTIME_FACT:
            if item.runtime_snapshot_ref != request.runtime.snapshot_id or item.json_pointer is None:
                raise ValueError("interpretation runtime evidence belongs to another snapshot")
            recorded = json.loads(json.dumps(item.value))
            if resolve_json_pointer(runtime_json, item.json_pointer) != recorded:
                raise ValueError("interpretation runtime evidence value does not resolve")
        elif item.source_kind == InterpretationEvidenceKind.MEMORY_RECORD:
            memory = memories.get(item.memory_ref or "")
            if memory is None or not memory.authorized:
                raise ValueError("interpretation memory evidence does not resolve")
        elif item.source_kind == InterpretationEvidenceKind.RELATIONSHIP_RECORD:
            if request.relationship is None or item.source_ref != request.relationship.relationship_ref:
                raise ValueError("interpretation relationship evidence does not resolve")
