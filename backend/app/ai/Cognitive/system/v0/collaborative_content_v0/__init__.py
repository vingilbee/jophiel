"""Collaborative Content Production V0 (shadow-only).

Constructive reasoning between requirement resolution and Action Selection:
for a ready conversational deliverable it proposes a typed artifact patch or
presentation content plus inquiry opportunities, through a separate provider
protocol and a deterministic compiler.  Nothing it proposes is delivered, and
a provider failure is a typed failure -- never a fallback.
"""

from .compiler import (
    COMPILER_VERSION, CollaborationCompileError, CollaborationCompilerV0, CompileContext, inquiry_id_for,
    new_artifact_ref,
)
from .doubles import RecordedCollaborationModel, ScriptedCollaborationModel
from .engine import ENGINE_VERSION, CollaborationGoalInput, CollaborativeContentEngine, curiosity_band
from .inquiry_policy import POLICY_VERSION as INQUIRY_POLICY_VERSION
from .model_protocol import (
    COLLABORATION_RESPONSE_SCHEMA_NAME, CollaborationModelOutput, CollaborationModelPayload,
    CollaborationProviderError, SourceKind, response_json_schema,
)
from .sources import build_sources, element_labels, element_ref

__all__ = [
    "COLLABORATION_RESPONSE_SCHEMA_NAME", "COMPILER_VERSION", "CollaborationCompileError", "CollaborationCompilerV0",
    "CollaborationGoalInput", "CollaborationModelOutput", "CollaborationModelPayload", "CollaborationProviderError",
    "CollaborativeContentEngine", "CompileContext", "ENGINE_VERSION", "INQUIRY_POLICY_VERSION",
    "RecordedCollaborationModel", "ScriptedCollaborationModel", "SourceKind", "build_sources", "curiosity_band",
    "element_labels", "element_ref", "inquiry_id_for", "new_artifact_ref", "response_json_schema",
]
