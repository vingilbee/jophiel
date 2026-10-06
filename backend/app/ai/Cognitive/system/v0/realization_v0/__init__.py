"""Realization V0: shadow-only wording of an already selected Action plan.

The cognitive system decides what may be communicated; the realization model
decides only how that authorized meaning is worded.  Candidates are persisted
for inspection only; they never reach a user, a tool, or memory.

This package imports no provider SDK, database, chat agent, prompt, or tool.
"""

from .action_manifest import MANIFEST, MANIFEST_VERSION, ActionRealizationEntry, entry_for
from .compiler import COMPILER_VERSION, RealizationCompileSkip, RealizationCompilerV0
from .content import DIALOGUE_STRUCTURE_PRODUCER, ContentIndex, project_dialogue_structure
from .engine import ENGINE_VERSION, RealizationEngineV0, build_candidate
from .errors import RealizationExecutionError, RealizationProviderError
from .model_protocol import (
    PROTOCOL_VERSION, RESPONSE_JSON_SCHEMA, RealizationModel, RealizationModelOutput, RealizationModelPayload,
    canonical_payload_json, output_from_excerpts, project_payload,
)
from .style_manifest import STYLE_MANIFEST_VERSION, band_for
from .style_mapper import compile_delivery_profile
from .validator import CONSTRAINT_ENFORCEMENT, RealizationValidatorV0

__all__ = [
    "ActionRealizationEntry", "COMPILER_VERSION", "CONSTRAINT_ENFORCEMENT", "ContentIndex",
    "DIALOGUE_STRUCTURE_PRODUCER", "ENGINE_VERSION", "MANIFEST", "MANIFEST_VERSION", "PROTOCOL_VERSION",
    "RESPONSE_JSON_SCHEMA", "RealizationCompileSkip", "RealizationCompilerV0", "RealizationEngineV0",
    "RealizationExecutionError", "RealizationModel", "RealizationModelOutput", "RealizationModelPayload",
    "RealizationProviderError", "RealizationValidatorV0", "STYLE_MANIFEST_VERSION", "band_for", "build_candidate",
    "canonical_payload_json", "compile_delivery_profile", "entry_for", "output_from_excerpts",
    "project_dialogue_structure", "project_payload",
]
