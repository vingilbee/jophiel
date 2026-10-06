"""Typed Requirement Resolution V0 (shadow-only).

Deterministic and closed-world: for each active current-turn request goal it
resolves the declared prerequisites (arguments, referents, trusted content,
capability, authorization, consent, safety) from typed snapshots, then
projects them into existing Action inputs and closed-world Realization
content.  No model call, no text parsing, no I/O.
"""

from .engine import (
    ContentRecord, RequirementResolutionEngine, RequirementResolutionError, RequirementResolutionOutcome,
    RequirementResolutionRequest, default_producers, goal_frames, linked_referent_cues, runtime_date_record,
)
from .manifest import CAPABILITY_MAP_VERSION, MANIFEST_VERSION, REQUEST_GOAL_DEFINITIONS
from .projection import (
    REQUIREMENT_CONTENT_PRODUCER, RequirementProjection, RequirementProjectorV0, request_object_id,
)

__all__ = [
    "CAPABILITY_MAP_VERSION", "ContentRecord", "MANIFEST_VERSION", "REQUEST_GOAL_DEFINITIONS",
    "REQUIREMENT_CONTENT_PRODUCER", "RequirementProjection", "RequirementProjectorV0", "RequirementResolutionEngine",
    "RequirementResolutionError", "RequirementResolutionOutcome", "RequirementResolutionRequest",
    "default_producers", "goal_frames", "linked_referent_cues", "request_object_id", "runtime_date_record",
]
