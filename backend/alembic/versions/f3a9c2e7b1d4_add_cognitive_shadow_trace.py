"""Add durable live-shadow cognitive capture jobs and terminal traces.

Revision ID: f3a9c2e7b1d4
Revises: e1a2b3c4d5f6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f3a9c2e7b1d4"
down_revision = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None

_DIGEST = "~ '^[0-9a-f]{64}$'"

_JOB_GUARD = """
CREATE OR REPLACE FUNCTION cognitive_shadow_jobs_guard() RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['status', 'attempts', 'claim_token', 'lease_expires_at', 'last_error_code',
                             'last_error_safe', 'processed_at', 'updated_at', 'chat_job_id'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status', 'attempts', 'claim_token', 'lease_expires_at', 'last_error_code',
                             'last_error_safe', 'processed_at', 'updated_at', 'chat_job_id']) THEN
        RAISE EXCEPTION 'cognitive shadow capture fields are immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.chat_job_id IS DISTINCT FROM OLD.chat_job_id AND NEW.chat_job_id IS NOT NULL THEN
        RAISE EXCEPTION 'cognitive shadow chat job reference is immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('completed', 'failed', 'skipped') AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'a terminal cognitive shadow job cannot change status' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_TRACE_GUARD = """
CREATE OR REPLACE FUNCTION cognitive_shadow_traces_guard() RETURNS trigger AS $$
BEGIN
    IF (to_jsonb(NEW) - 'predecessor_trace_id') IS DISTINCT FROM (to_jsonb(OLD) - 'predecessor_trace_id')
       OR (NEW.predecessor_trace_id IS DISTINCT FROM OLD.predecessor_trace_id
           AND NEW.predecessor_trace_id IS NOT NULL) THEN
        RAISE EXCEPTION 'cognitive shadow terminal records are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.create_table(
        "cognitive_shadow_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_path", sa.String(32), nullable=False),
        sa.Column("capture_schema_version", sa.String(64), nullable=False),
        sa.Column("capture_payload", postgresql.JSONB(), nullable=False),
        sa.Column("capture_digest", sa.String(64), nullable=False),
        sa.Column("sampling_policy_version", sa.String(64), nullable=False),
        sa.Column("source_channel", sa.String(32), nullable=False),
        sa.Column("source_turn_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("last_error_safe", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_job_id"], ["chat_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id"),
        sa.UniqueConstraint("user_message_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'skipped')",
            name="ck_cognitive_shadow_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_cognitive_shadow_jobs_attempts"),
        sa.CheckConstraint(
            "status <> 'processing' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_cognitive_shadow_jobs_processing_lease",
        ),
        sa.CheckConstraint(f"capture_digest {_DIGEST}", name="ck_cognitive_shadow_jobs_capture_digest"),
        sa.CheckConstraint(
            "source_channel IN ('web', 'voice', 'whatsapp')", name="ck_cognitive_shadow_jobs_source_channel"
        ),
        sa.CheckConstraint(
            "generation_path IN ('chat_worker', 'local_fallback')",
            name="ck_cognitive_shadow_jobs_generation_path",
        ),
    )
    op.create_index("ix_cognitive_shadow_jobs_user_id", "cognitive_shadow_jobs", ["user_id"])
    op.create_index("ix_cognitive_shadow_jobs_captured_at", "cognitive_shadow_jobs", ["captured_at"])
    op.create_index(
        "ix_cognitive_shadow_jobs_status_captured_at", "cognitive_shadow_jobs", ["status", "captured_at"]
    )
    op.create_index(
        "ix_cognitive_shadow_jobs_conversation_source_turn",
        "cognitive_shadow_jobs",
        ["conversation_id", "source_turn_at", "turn_id"],
    )

    op.create_table(
        "cognitive_shadow_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predecessor_trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("segment_reset_reason", sa.String(32), nullable=True),
        sa.Column(
            "missing_predecessor_refs", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("terminal_schema_version", sa.String(64), nullable=False),
        sa.Column("terminal_status", sa.String(16), nullable=False),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("compiled_request_payload", postgresql.JSONB(), nullable=True),
        sa.Column("pipeline_trace_payload", postgresql.JSONB(), nullable=True),
        sa.Column("failure_payload", postgresql.JSONB(), nullable=True),
        sa.Column("capture_digest", sa.String(64), nullable=False),
        sa.Column("compiled_request_digest", sa.String(64), nullable=True),
        sa.Column("trace_payload_digest", sa.String(64), nullable=True),
        sa.Column("semantic_digest", sa.String(64), nullable=True),
        sa.Column("versions", postgresql.JSONB(), nullable=False),
        sa.Column("timings", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["cognitive_shadow_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["predecessor_trace_id"], ["cognitive_shadow_traces.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint("turn_id"),
        sa.CheckConstraint(
            "terminal_status IN ('completed', 'failed', 'skipped')", name="ck_cognitive_shadow_traces_status"
        ),
        sa.CheckConstraint(
            "segment_reset_reason IS NULL OR segment_reset_reason IN "
            "('initial_conversation', 'predecessor_gap', 'lineage_version_change', 'lineage_unresolved')",
            name="ck_cognitive_shadow_traces_reset_reason",
        ),
        sa.CheckConstraint(
            "segment_reset_reason IS NULL OR predecessor_trace_id IS NULL",
            name="ck_cognitive_shadow_traces_reset_has_no_predecessor",
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN ('unsupported_language', 'missing_personality_projection', "
            "'missing_required_runtime_fact', 'unauthorized_input', 'predecessor_unavailable', "
            "'capture_schema_unsupported')",
            name="ck_cognitive_shadow_traces_skip_reason",
        ),
        sa.CheckConstraint(f"capture_digest {_DIGEST}", name="ck_cognitive_shadow_traces_capture_digest"),
        sa.CheckConstraint(
            f"compiled_request_digest IS NULL OR compiled_request_digest {_DIGEST}",
            name="ck_cognitive_shadow_traces_request_digest",
        ),
        sa.CheckConstraint(
            f"trace_payload_digest IS NULL OR trace_payload_digest {_DIGEST}",
            name="ck_cognitive_shadow_traces_trace_digest",
        ),
        sa.CheckConstraint(
            f"semantic_digest IS NULL OR semantic_digest {_DIGEST}",
            name="ck_cognitive_shadow_traces_semantic_digest",
        ),
        sa.CheckConstraint(
            "(compiled_request_payload IS NULL) = (compiled_request_digest IS NULL)",
            name="ck_cognitive_shadow_traces_request_pair",
        ),
        sa.CheckConstraint(
            "terminal_status <> 'completed' OR (compiled_request_payload IS NOT NULL "
            "AND pipeline_trace_payload IS NOT NULL AND trace_payload_digest IS NOT NULL "
            "AND semantic_digest IS NOT NULL AND timings IS NOT NULL "
            "AND failure_payload IS NULL AND skip_reason IS NULL)",
            name="ck_cognitive_shadow_traces_completed",
        ),
        sa.CheckConstraint(
            "terminal_status <> 'failed' OR (failure_payload IS NOT NULL AND pipeline_trace_payload IS NULL "
            "AND trace_payload_digest IS NULL AND semantic_digest IS NULL AND skip_reason IS NULL)",
            name="ck_cognitive_shadow_traces_failed",
        ),
        sa.CheckConstraint(
            "terminal_status <> 'skipped' OR (skip_reason IS NOT NULL AND compiled_request_payload IS NULL "
            "AND pipeline_trace_payload IS NULL AND failure_payload IS NULL AND trace_payload_digest IS NULL "
            "AND semantic_digest IS NULL AND timings IS NULL)",
            name="ck_cognitive_shadow_traces_skipped",
        ),
    )
    op.create_index("ix_cognitive_shadow_traces_user_id", "cognitive_shadow_traces", ["user_id"])
    op.create_index("ix_cognitive_shadow_traces_segment_id", "cognitive_shadow_traces", ["segment_id"])
    op.create_index(
        "ix_cognitive_shadow_traces_predecessor_trace_id", "cognitive_shadow_traces", ["predecessor_trace_id"]
    )
    op.create_index(
        "ix_cognitive_shadow_traces_conversation_created",
        "cognitive_shadow_traces",
        ["conversation_id", "created_at"],
    )

    op.execute(_JOB_GUARD)
    op.execute(
        "CREATE TRIGGER cognitive_shadow_jobs_immutable BEFORE UPDATE ON cognitive_shadow_jobs "
        "FOR EACH ROW EXECUTE FUNCTION cognitive_shadow_jobs_guard()"
    )
    op.execute(_TRACE_GUARD)
    op.execute(
        "CREATE TRIGGER cognitive_shadow_traces_immutable BEFORE UPDATE ON cognitive_shadow_traces "
        "FOR EACH ROW EXECUTE FUNCTION cognitive_shadow_traces_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS cognitive_shadow_traces_immutable ON cognitive_shadow_traces")
    op.execute("DROP TRIGGER IF EXISTS cognitive_shadow_jobs_immutable ON cognitive_shadow_jobs")
    op.drop_table("cognitive_shadow_traces")
    op.drop_table("cognitive_shadow_jobs")
    op.execute("DROP FUNCTION IF EXISTS cognitive_shadow_traces_guard()")
    op.execute("DROP FUNCTION IF EXISTS cognitive_shadow_jobs_guard()")
