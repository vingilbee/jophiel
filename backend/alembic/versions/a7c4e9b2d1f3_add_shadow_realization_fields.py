"""Add Realization V0 fields to cognitive shadow terminal traces.

Realization has its own status, payloads, and digests so a provider failure
never erases a valid deterministic Action trace and model output never enters
the core semantic digest. Existing rows migrate to ``not_requested`` with null
realization payloads (adding a column is DDL, so the immutability trigger does
not fire and no existing row is rewritten by an UPDATE).

Revision ID: a7c4e9b2d1f3
Revises: f3a9c2e7b1d4
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c4e9b2d1f3"
down_revision = "f3a9c2e7b1d4"
branch_labels = None
depends_on = None

_TABLE = "cognitive_shadow_traces"
_DIGEST = "~ '^[0-9a-f]{64}$'"
_TRACED = (
    "'accepted_shadow', 'rejected', 'skipped_missing_content', 'skipped_unsupported_action', "
    "'provider_unavailable', 'provider_failed'"
)

# Mirrors app.db.models.REALIZATION_TRACE_CHECKS exactly.
_CHECKS = (
    ("ck_cognitive_shadow_traces_realization_status",
     f"realization_status IN ('not_requested', 'execution_failed', {_TRACED})"),
    ("ck_cognitive_shadow_traces_realization_not_requested",
     "realization_status <> 'not_requested' OR (realization_request_payload IS NULL "
     "AND realization_request_digest IS NULL AND realization_trace_payload IS NULL "
     "AND realization_trace_digest IS NULL AND realization_semantic_digest IS NULL "
     "AND realization_failure_payload IS NULL AND realization_timings IS NULL)"),
    ("ck_cognitive_shadow_traces_realization_after_completed",
     "realization_status = 'not_requested' OR (terminal_status = 'completed' AND realization_timings IS NOT NULL)"),
    ("ck_cognitive_shadow_traces_realization_request_pair",
     "(realization_request_payload IS NULL) = (realization_request_digest IS NULL)"),
    ("ck_cognitive_shadow_traces_realization_trace_digests",
     "(realization_trace_payload IS NULL) = (realization_trace_digest IS NULL) "
     "AND (realization_trace_payload IS NULL) = (realization_semantic_digest IS NULL)"),
    ("ck_cognitive_shadow_traces_realization_failed",
     "realization_status <> 'execution_failed' OR (realization_failure_payload IS NOT NULL "
     "AND realization_trace_payload IS NULL AND realization_request_payload IS NULL)"),
    ("ck_cognitive_shadow_traces_realization_traced",
     f"realization_status NOT IN ({_TRACED}) OR (realization_trace_payload IS NOT NULL "
     "AND realization_failure_payload IS NULL)"),
    ("ck_cognitive_shadow_traces_realization_skipped",
     "realization_status NOT IN ('skipped_missing_content', 'skipped_unsupported_action') "
     "OR realization_request_payload IS NULL"),
    ("ck_cognitive_shadow_traces_realization_requested",
     "realization_status NOT IN ('accepted_shadow', 'rejected', 'provider_unavailable', 'provider_failed') "
     "OR realization_request_payload IS NOT NULL"),
    ("ck_cognitive_shadow_traces_realization_request_digest",
     f"realization_request_digest IS NULL OR realization_request_digest {_DIGEST}"),
    ("ck_cognitive_shadow_traces_realization_trace_digest",
     f"realization_trace_digest IS NULL OR realization_trace_digest {_DIGEST}"),
    ("ck_cognitive_shadow_traces_realization_semantic_digest",
     f"realization_semantic_digest IS NULL OR realization_semantic_digest {_DIGEST}"),
)

_COLUMNS = (
    "realization_request_payload", "realization_request_digest", "realization_trace_payload",
    "realization_trace_digest", "realization_semantic_digest", "realization_failure_payload",
    "realization_timings",
)


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("realization_status", sa.String(32), nullable=False, server_default="not_requested"),
    )
    op.add_column(_TABLE, sa.Column("realization_request_payload", postgresql.JSONB(), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_request_digest", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_trace_payload", postgresql.JSONB(), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_trace_digest", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_semantic_digest", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_failure_payload", postgresql.JSONB(), nullable=True))
    op.add_column(_TABLE, sa.Column("realization_timings", postgresql.JSONB(), nullable=True))
    for name, condition in _CHECKS:
        op.create_check_constraint(name, _TABLE, condition)


def downgrade() -> None:
    for name, _condition in reversed(_CHECKS):
        op.drop_constraint(name, _TABLE, type_="check")
    for column in reversed(_COLUMNS):
        op.drop_column(_TABLE, column)
    op.drop_column(_TABLE, "realization_status")
