"""Evidence construction and request-relative validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import (
    MessageSequenceEvidence,
    PerceptionEvidence,
    PerceptionRequest,
    RuntimeFactEvidence,
    TextSpanEvidence,
    WholeMessageEvidence,
)

EVIDENCE_VALIDATOR_VERSION = "perception-evidence-v0.1"


def stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def text_span(message_id: str, content: str, start: int, end: int) -> TextSpanEvidence:
    return TextSpanEvidence(
        message_id=message_id,
        start=start,
        end=end,
        quoted_text=content[start:end],
    )


def whole_message(message_id: str) -> WholeMessageEvidence:
    return WholeMessageEvidence(message_id=message_id)


def message_sequence(*message_ids: str) -> MessageSequenceEvidence:
    return MessageSequenceEvidence(message_ids=message_ids)


def runtime_fact(request: PerceptionRequest, pointer: str) -> RuntimeFactEvidence:
    snapshot = request.runtime.model_dump(mode="json")
    return RuntimeFactEvidence(
        runtime_snapshot_id=request.runtime.snapshot_id,
        json_pointer=pointer,
        value=resolve_json_pointer(snapshot, pointer),
    )


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or begin with /")
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"JSON Pointer does not resolve: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if token == "-" or not token.isdigit():
                raise ValueError(f"invalid array index in JSON Pointer: {pointer}")
            index = int(token)
            if index >= len(current):
                raise ValueError(f"JSON Pointer does not resolve: {pointer}")
            current = current[index]
        else:
            raise ValueError(f"JSON Pointer traverses a scalar: {pointer}")
    return current


def validate_evidence(evidence: PerceptionEvidence, request: PerceptionRequest) -> None:
    messages = {message.message_id: message for message in request.messages}
    focus_index = tuple(messages).index(request.focus_message_id)
    if isinstance(evidence, TextSpanEvidence):
        message = messages.get(evidence.message_id)
        if message is None:
            raise ValueError(f"evidence references unknown message: {evidence.message_id}")
        if tuple(messages).index(evidence.message_id) > focus_index:
            raise ValueError("evidence references a future message")
        if evidence.end > len(message.content):
            raise ValueError("text evidence exceeds message length")
        if message.content[evidence.start:evidence.end] != evidence.quoted_text:
            raise ValueError("quoted text does not match the referenced span")
    elif isinstance(evidence, WholeMessageEvidence):
        if evidence.message_id not in messages:
            raise ValueError(f"evidence references unknown message: {evidence.message_id}")
        if tuple(messages).index(evidence.message_id) > focus_index:
            raise ValueError("evidence references a future message")
    elif isinstance(evidence, MessageSequenceEvidence):
        if len(evidence.message_ids) != len(set(evidence.message_ids)):
            raise ValueError("message-sequence evidence contains duplicate ids")
        indices = []
        for message_id in evidence.message_ids:
            if message_id not in messages:
                raise ValueError(f"evidence references unknown message: {message_id}")
            index = tuple(messages).index(message_id)
            if index > focus_index:
                raise ValueError("evidence references a future message")
            indices.append(index)
        if indices != sorted(indices):
            raise ValueError("message-sequence evidence must preserve request order")
    elif isinstance(evidence, RuntimeFactEvidence):
        if evidence.runtime_snapshot_id != request.runtime.snapshot_id:
            raise ValueError("runtime evidence belongs to a different snapshot")
        actual = resolve_json_pointer(request.runtime.model_dump(mode="json"), evidence.json_pointer)
        expected = json.loads(json.dumps(evidence.value))
        if actual != expected:
            raise ValueError("runtime evidence value does not equal the resolved fact")
    else:  # pragma: no cover - protected by the discriminated union
        raise TypeError(f"unsupported evidence type: {type(evidence).__name__}")
