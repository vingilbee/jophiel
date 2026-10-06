"""Conversational Workspace V0 (shadow-only).

The versioned work product a conversation develops together: typed artifact
payloads, immutable revisions, deterministic patches, delivered-outcome
reconciliation, open inquiries, and per-goal target resolution.  Pure: no
database, clock, model, or I/O; durable persistence lives in
``app.services.conversation_workspace``.
"""

from ..contracts.workspace import ArtifactType as _ArtifactType, ArtifactOperation as _ArtifactOperation
from ..interpretation_v0.ontology import ARTIFACT_TYPES as _ARTIFACT_TYPES, COLLABORATIVE_OPERATIONS as _OPERATIONS
from .errors import WorkspaceError, WorkspaceErrorCode
from .patching import PATCH_ENGINE_VERSION, PatchAuthority, PatchResult, apply_patch, constraint_violations
from .reconciliation import RECONCILIATION_VERSION, ReconciliationResult, reconcile
from .resolution import ANSWER_GOAL, DELIVERABLE_GOAL, RESOLUTION_VERSION, WORKSPACE_GOALS, resolve_workspace

# The parser's closed vocabularies are exactly the workspace registry.
if tuple(item.value for item in _ArtifactType) != _ARTIFACT_TYPES:
    raise RuntimeError("the interpretation artifact types differ from the workspace registry")
if tuple(item.value for item in _ArtifactOperation) != _OPERATIONS:
    raise RuntimeError("the interpretation operations differ from the workspace operations")

__all__ = [
    "ANSWER_GOAL", "DELIVERABLE_GOAL", "PATCH_ENGINE_VERSION", "PatchAuthority", "PatchResult",
    "RECONCILIATION_VERSION", "RESOLUTION_VERSION", "ReconciliationResult", "WORKSPACE_GOALS", "WorkspaceError",
    "WorkspaceErrorCode", "apply_patch", "constraint_violations", "reconcile", "resolve_workspace",
]
