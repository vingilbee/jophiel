"""Canonical JSON digests for live-shadow captures, requests, and traces.

Three exact digests keep three meanings separate:

- ``capture_digest``: the exact immutable capture;
- ``compiled_request_digest``: the exact canonical pipeline request; and
- ``trace_payload_digest``: the exact stored trace, diagnostics included.

``semantic_digest`` is versioned and covers the decision-relevant trace: it
removes only the reviewed volatile fields in ``SEMANTIC_EXCLUDED_PATHS``
(generated trace identifiers and measured timings).  Stage outputs, evidence,
versions, candidates, gates, features, scores, moves, and state all remain.
Changing the exclusions requires a new ``SEMANTIC_DIGEST_VERSION``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .models import SEMANTIC_DIGEST_VERSION, TWO_CALL_SEMANTIC_DIGEST_VERSION

# Reviewed for cognitive-shadow-semantic-digest-v0.1.  Each path is a tuple of
# object keys from the root of a JSON-mode ``CognitivePipelineTrace`` dump.
SEMANTIC_EXCLUDED_PATHS: tuple[tuple[str, ...], ...] = (
    ("pipeline_trace_id",),
    ("timings",),
    ("cognitive_turn", "trace_id"),
    ("cognitive_turn", "timings"),
)


def canonical_json(value: Any) -> str:
    """Sorted keys, stable separators, no NaN/Infinity, exact Unicode."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )


def json_payload(model: BaseModel) -> dict[str, Any]:
    """The JSON-mode payload a model is persisted and digested as."""

    return model.model_dump(mode="json")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def payload_digest(payload: Mapping[str, Any] | BaseModel) -> str:
    return sha256_json(json_payload(payload) if isinstance(payload, BaseModel) else payload)


def semantic_projection(trace_payload: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    """The trace with exactly the reviewed volatile fields removed."""

    projected = copy.deepcopy(
        json_payload(trace_payload) if isinstance(trace_payload, BaseModel) else dict(trace_payload)
    )
    for path in SEMANTIC_EXCLUDED_PATHS:
        parent: Any = projected
        for key in path[:-1]:
            if not isinstance(parent, dict) or key not in parent:
                raise ValueError("trace payload does not have the reviewed semantic shape")
            parent = parent[key]
        if not isinstance(parent, dict) or path[-1] not in parent:
            raise ValueError("trace payload does not have the reviewed semantic shape")
        del parent[path[-1]]
    # New optional orchestration diagnostics follow the existing reviewed
    # timing exclusion. Captures, hashes and policy remain decision-relevant.
    if "orchestration" in projected:
        projected["orchestration"].pop("spans", None)
    return projected


def semantic_digest_version(trace_payload):
    payload = json_payload(trace_payload) if isinstance(trace_payload,BaseModel) else trace_payload
    return TWO_CALL_SEMANTIC_DIGEST_VERSION if payload.get("orchestration") is not None else SEMANTIC_DIGEST_VERSION


def semantic_digest(trace_payload: Mapping[str, Any] | BaseModel) -> str:
    return sha256_json({
        "semantic_digest_version": semantic_digest_version(trace_payload),
        "trace": semantic_projection(trace_payload),
    })


__all__ = [
    "SEMANTIC_EXCLUDED_PATHS", "canonical_json", "json_payload", "payload_digest",
    "semantic_digest", "semantic_projection", "sha256_json",
]
