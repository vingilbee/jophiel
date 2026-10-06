"""Typed, content-free workspace failures."""

from __future__ import annotations

from enum import StrEnum


class WorkspaceErrorCode(StrEnum):
    # Patch application (Section 7.4)
    STALE_BASE = "stale_base"
    UNKNOWN_ARTIFACT = "unknown_artifact"
    ARTIFACT_EXISTS = "artifact_exists"
    ARTIFACT_TYPE_MISMATCH = "artifact_type_mismatch"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    CONFLICTING_OPERATIONS = "conflicting_operations"
    UNKNOWN_ELEMENT = "unknown_element"
    DUPLICATE_ELEMENT = "duplicate_element"
    DUPLICATE_CONSTRAINT = "duplicate_constraint"
    CONSTRAINT_IDENTITY_CHANGED = "constraint_identity_changed"
    CONSTRAINT_REMOVAL_UNAUTHORIZED = "constraint_removal_unauthorized"
    CONSTRAINT_LOST = "constraint_lost"
    CONSTRAINT_CONFLICT = "constraint_conflict"
    UNAUTHORIZED_SOURCE = "unauthorized_source"
    PAYLOAD_INVALID = "payload_invalid"
    NO_OP_PATCH = "no_op_patch"
    # Reconciliation (Section 14)
    AUTHORITY_MODE_NOT_APPROVED = "authority_mode_not_approved"
    SHADOW_DELIVERY_IN_OBSERVATIONAL_MODE = "shadow_delivery_in_observational_mode"
    OUTCOME_LINEAGE_MISMATCH = "outcome_lineage_mismatch"
    OUTCOME_CITES_UNKNOWN_PROPOSAL = "outcome_cites_unknown_proposal"
    HEAD_CONFLICT = "head_conflict"


class WorkspaceError(ValueError):
    """A deterministic workspace failure with a stable code and no content."""

    def __init__(self, code: WorkspaceErrorCode, subject: str = "") -> None:
        super().__init__(code.value)
        self.code = code
        self.subject = subject


__all__ = ["WorkspaceError", "WorkspaceErrorCode"]
