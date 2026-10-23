"""Drop the retired Cognitive V0 shadow and dialogue-memory tables (docs/LEGACY_CLEANUP_2026-10-22.md).

Nothing has read or written them since the 2026-10-22 cleanup, no other table references them, and their only
database objects are their own append-only guard triggers and functions. Upgrade deletes their stored data for
good. Downgrade recreates them empty, with the exact schema they had at b3e7f9a1c5d2 (taken from a database at
that revision), so earlier migrations can still be downgraded through.

The message-order trigger (chat_messages_assign_sequence / chat_messages_position_guard), created by the same
migration as dialogue memory, is not touched.
"""
from alembic import op

revision = "e4c8a2f6b9d3"
down_revision = "b3e7f9a1c5d2"
branch_labels = None
depends_on = None

TABLES = (
    # Dependents first: traces point at jobs, heads and jobs at snapshots.
    "cognitive_dialogue_interpretations",
    "cognitive_dialogue_memory_jobs",
    "cognitive_dialogue_memory_heads",
    "cognitive_dialogue_memory_snapshots",
    "cognitive_shadow_traces",
    "cognitive_shadow_jobs",
)
GUARDS = (
    "cognitive_dialogue_interpretations_guard",
    "cognitive_dialogue_memory_snapshots_guard",
    "cognitive_shadow_traces_guard",
    "cognitive_shadow_jobs_guard",
)


def upgrade():
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")  # its triggers go with it
    for guard in GUARDS:
        op.execute(f"DROP FUNCTION IF EXISTS {guard}()")


def downgrade():
    op.execute(_SCHEMA)


_SCHEMA = r"""
CREATE OR REPLACE FUNCTION public.cognitive_shadow_jobs_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$

;
CREATE OR REPLACE FUNCTION public.cognitive_shadow_traces_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF (to_jsonb(NEW) - 'predecessor_trace_id') IS DISTINCT FROM (to_jsonb(OLD) - 'predecessor_trace_id')
       OR (NEW.predecessor_trace_id IS DISTINCT FROM OLD.predecessor_trace_id
           AND NEW.predecessor_trace_id IS NOT NULL) THEN
        RAISE EXCEPTION 'cognitive shadow terminal records are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$

;
CREATE OR REPLACE FUNCTION public.cognitive_dialogue_memory_snapshots_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF (to_jsonb(NEW) - 'through_message_id') IS DISTINCT FROM (to_jsonb(OLD) - 'through_message_id')
       OR (NEW.through_message_id IS DISTINCT FROM OLD.through_message_id AND NEW.through_message_id IS NOT NULL) THEN
        RAISE EXCEPTION 'dialogue memory snapshots are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$

;
CREATE OR REPLACE FUNCTION public.cognitive_dialogue_interpretations_guard()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'cognitive_dialogue_interpretations rows are immutable';
END;
$function$

;

CREATE TABLE public.cognitive_dialogue_interpretations (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    source_message_id uuid NOT NULL,
    source_conversation_sequence bigint NOT NULL,
    shadow_trace_id uuid NOT NULL,
    schema_version character varying(64) NOT NULL,
    payload jsonb NOT NULL,
    record_digest character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dialogue_interpretations_digest CHECK (((record_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_dialogue_interpretations_sequence CHECK ((source_conversation_sequence >= 1))
);
CREATE TABLE public.cognitive_dialogue_memory_heads (
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    revision bigint NOT NULL,
    through_conversation_sequence bigint NOT NULL,
    semantic_digest character varying(64) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dialogue_memory_heads_revision CHECK ((revision >= 1)),
    CONSTRAINT ck_dialogue_memory_heads_semantic_digest CHECK (((semantic_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_dialogue_memory_heads_through CHECK ((through_conversation_sequence >= 0))
);
CREATE TABLE public.cognitive_dialogue_memory_jobs (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    trigger_message_id uuid NOT NULL,
    trigger_kind character varying(32) NOT NULL,
    target_conversation_sequence bigint NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claim_token character varying(64),
    lease_expires_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_safe text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    CONSTRAINT ck_dialogue_memory_jobs_attempts CHECK ((attempts >= 0)),
    CONSTRAINT ck_dialogue_memory_jobs_processing_lease CHECK ((((status)::text <> 'processing'::text) OR ((claim_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT ck_dialogue_memory_jobs_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT ck_dialogue_memory_jobs_target CHECK ((target_conversation_sequence >= 1)),
    CONSTRAINT ck_dialogue_memory_jobs_trigger_kind CHECK (((trigger_kind)::text = ANY ((ARRAY['message_committed'::character varying, 'delivery_settled'::character varying])::text[])))
);
CREATE TABLE public.cognitive_dialogue_memory_snapshots (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    revision bigint NOT NULL,
    predecessor_snapshot_id uuid,
    segment_id uuid NOT NULL,
    reset_reason character varying(64),
    through_conversation_sequence bigint NOT NULL,
    through_message_id uuid,
    source_event_count bigint NOT NULL,
    source_prefix_digest character varying(64) NOT NULL,
    snapshot_payload jsonb NOT NULL,
    semantic_digest character varying(64) NOT NULL,
    versions jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dialogue_memory_snapshots_event_count CHECK ((source_event_count >= 0)),
    CONSTRAINT ck_dialogue_memory_snapshots_prefix_digest CHECK (((source_prefix_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_dialogue_memory_snapshots_revision CHECK ((revision >= 1)),
    CONSTRAINT ck_dialogue_memory_snapshots_semantic_digest CHECK (((semantic_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_dialogue_memory_snapshots_through CHECK ((through_conversation_sequence >= 0))
);
CREATE TABLE public.cognitive_shadow_jobs (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    turn_id uuid NOT NULL,
    chat_job_id uuid,
    user_message_id uuid NOT NULL,
    generation_path character varying(32) NOT NULL,
    capture_schema_version character varying(64) NOT NULL,
    capture_payload jsonb NOT NULL,
    capture_digest character varying(64) NOT NULL,
    sampling_policy_version character varying(64) NOT NULL,
    source_channel character varying(32) NOT NULL,
    source_turn_at timestamp without time zone NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claim_token character varying(64),
    lease_expires_at timestamp with time zone,
    last_error_code text,
    last_error_safe text,
    captured_at timestamp with time zone NOT NULL,
    processed_at timestamp with time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_cognitive_shadow_jobs_attempts CHECK ((attempts >= 0)),
    CONSTRAINT ck_cognitive_shadow_jobs_capture_digest CHECK (((capture_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_cognitive_shadow_jobs_generation_path CHECK (((generation_path)::text = ANY ((ARRAY['chat_worker'::character varying, 'local_fallback'::character varying])::text[]))),
    CONSTRAINT ck_cognitive_shadow_jobs_processing_lease CHECK ((((status)::text <> 'processing'::text) OR ((claim_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT ck_cognitive_shadow_jobs_source_channel CHECK (((source_channel)::text = ANY ((ARRAY['web'::character varying, 'voice'::character varying, 'whatsapp'::character varying])::text[]))),
    CONSTRAINT ck_cognitive_shadow_jobs_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[])))
);
CREATE TABLE public.cognitive_shadow_traces (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    turn_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    predecessor_trace_id uuid,
    segment_reset_reason character varying(32),
    missing_predecessor_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    terminal_schema_version character varying(64) NOT NULL,
    terminal_status character varying(16) NOT NULL,
    skip_reason character varying(64),
    compiled_request_payload jsonb,
    pipeline_trace_payload jsonb,
    failure_payload jsonb,
    capture_digest character varying(64) NOT NULL,
    compiled_request_digest character varying(64),
    trace_payload_digest character varying(64),
    semantic_digest character varying(64),
    versions jsonb NOT NULL,
    timings jsonb,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    realization_status character varying(32) DEFAULT 'not_requested'::character varying NOT NULL,
    realization_request_payload jsonb,
    realization_request_digest character varying(64),
    realization_trace_payload jsonb,
    realization_trace_digest character varying(64),
    realization_semantic_digest character varying(64),
    realization_failure_payload jsonb,
    realization_timings jsonb,
    CONSTRAINT ck_cognitive_shadow_traces_capture_digest CHECK (((capture_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_cognitive_shadow_traces_completed CHECK ((((terminal_status)::text <> 'completed'::text) OR ((compiled_request_payload IS NOT NULL) AND (pipeline_trace_payload IS NOT NULL) AND (trace_payload_digest IS NOT NULL) AND (semantic_digest IS NOT NULL) AND (timings IS NOT NULL) AND (failure_payload IS NULL) AND (skip_reason IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_failed CHECK ((((terminal_status)::text <> 'failed'::text) OR ((failure_payload IS NOT NULL) AND (pipeline_trace_payload IS NULL) AND (trace_payload_digest IS NULL) AND (semantic_digest IS NULL) AND (skip_reason IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_after_completed CHECK ((((realization_status)::text = 'not_requested'::text) OR (((terminal_status)::text = 'completed'::text) AND (realization_timings IS NOT NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_failed CHECK ((((realization_status)::text <> 'execution_failed'::text) OR ((realization_failure_payload IS NOT NULL) AND (realization_trace_payload IS NULL) AND (realization_request_payload IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_not_requested CHECK ((((realization_status)::text <> 'not_requested'::text) OR ((realization_request_payload IS NULL) AND (realization_request_digest IS NULL) AND (realization_trace_payload IS NULL) AND (realization_trace_digest IS NULL) AND (realization_semantic_digest IS NULL) AND (realization_failure_payload IS NULL) AND (realization_timings IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_request_digest CHECK (((realization_request_digest IS NULL) OR ((realization_request_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_request_pair CHECK (((realization_request_payload IS NULL) = (realization_request_digest IS NULL))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_requested CHECK ((((realization_status)::text <> ALL ((ARRAY['accepted_shadow'::character varying, 'rejected'::character varying, 'provider_unavailable'::character varying, 'provider_failed'::character varying])::text[])) OR (realization_request_payload IS NOT NULL))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_semantic_digest CHECK (((realization_semantic_digest IS NULL) OR ((realization_semantic_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_skipped CHECK ((((realization_status)::text <> ALL ((ARRAY['skipped_missing_content'::character varying, 'skipped_unsupported_action'::character varying])::text[])) OR (realization_request_payload IS NULL))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_status CHECK (((realization_status)::text = ANY ((ARRAY['not_requested'::character varying, 'execution_failed'::character varying, 'accepted_shadow'::character varying, 'rejected'::character varying, 'skipped_missing_content'::character varying, 'skipped_unsupported_action'::character varying, 'provider_unavailable'::character varying, 'provider_failed'::character varying])::text[]))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_trace_digest CHECK (((realization_trace_digest IS NULL) OR ((realization_trace_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_trace_digests CHECK ((((realization_trace_payload IS NULL) = (realization_trace_digest IS NULL)) AND ((realization_trace_payload IS NULL) = (realization_semantic_digest IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_realization_traced CHECK ((((realization_status)::text <> ALL ((ARRAY['accepted_shadow'::character varying, 'rejected'::character varying, 'skipped_missing_content'::character varying, 'skipped_unsupported_action'::character varying, 'provider_unavailable'::character varying, 'provider_failed'::character varying])::text[])) OR ((realization_trace_payload IS NOT NULL) AND (realization_failure_payload IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_request_digest CHECK (((compiled_request_digest IS NULL) OR ((compiled_request_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_cognitive_shadow_traces_request_pair CHECK (((compiled_request_payload IS NULL) = (compiled_request_digest IS NULL))),
    CONSTRAINT ck_cognitive_shadow_traces_reset_has_no_predecessor CHECK (((segment_reset_reason IS NULL) OR (predecessor_trace_id IS NULL))),
    CONSTRAINT ck_cognitive_shadow_traces_reset_reason CHECK (((segment_reset_reason IS NULL) OR ((segment_reset_reason)::text = ANY ((ARRAY['initial_conversation'::character varying, 'predecessor_gap'::character varying, 'lineage_version_change'::character varying, 'lineage_unresolved'::character varying])::text[])))),
    CONSTRAINT ck_cognitive_shadow_traces_semantic_digest CHECK (((semantic_digest IS NULL) OR ((semantic_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_cognitive_shadow_traces_skip_reason CHECK (((skip_reason IS NULL) OR ((skip_reason)::text = ANY ((ARRAY['unsupported_language'::character varying, 'missing_personality_projection'::character varying, 'missing_required_runtime_fact'::character varying, 'unauthorized_input'::character varying, 'predecessor_unavailable'::character varying, 'capture_schema_unsupported'::character varying])::text[])))),
    CONSTRAINT ck_cognitive_shadow_traces_skipped CHECK ((((terminal_status)::text <> 'skipped'::text) OR ((skip_reason IS NOT NULL) AND (compiled_request_payload IS NULL) AND (pipeline_trace_payload IS NULL) AND (failure_payload IS NULL) AND (trace_payload_digest IS NULL) AND (semantic_digest IS NULL) AND (timings IS NULL)))),
    CONSTRAINT ck_cognitive_shadow_traces_status CHECK (((terminal_status)::text = ANY ((ARRAY['completed'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT ck_cognitive_shadow_traces_trace_digest CHECK (((trace_payload_digest IS NULL) OR ((trace_payload_digest)::text ~ '^[0-9a-f]{64}$'::text)))
);
ALTER TABLE ONLY public.cognitive_dialogue_interpretations
    ADD CONSTRAINT cognitive_dialogue_interpretations_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cognitive_dialogue_memory_heads
    ADD CONSTRAINT cognitive_dialogue_memory_heads_pkey PRIMARY KEY (conversation_id);
ALTER TABLE ONLY public.cognitive_dialogue_memory_jobs
    ADD CONSTRAINT cognitive_dialogue_memory_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT cognitive_dialogue_memory_snapshots_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_turn_id_key UNIQUE (turn_id);
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_user_message_id_key UNIQUE (user_message_id);
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_job_id_key UNIQUE (job_id);
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_turn_id_key UNIQUE (turn_id);
ALTER TABLE ONLY public.cognitive_dialogue_interpretations
    ADD CONSTRAINT uq_dialogue_interpretations_source_message UNIQUE (source_message_id);
ALTER TABLE ONLY public.cognitive_dialogue_memory_jobs
    ADD CONSTRAINT uq_dialogue_memory_jobs_trigger UNIQUE (trigger_message_id, trigger_kind);
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT uq_dialogue_memory_snapshots_position_digest UNIQUE (conversation_id, through_conversation_sequence, semantic_digest);
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT uq_dialogue_memory_snapshots_revision UNIQUE (conversation_id, revision);
CREATE INDEX ix_cognitive_dialogue_interpretations_user_id ON public.cognitive_dialogue_interpretations USING btree (user_id);
CREATE INDEX ix_cognitive_dialogue_memory_heads_user_id ON public.cognitive_dialogue_memory_heads USING btree (user_id);
CREATE INDEX ix_cognitive_dialogue_memory_jobs_user_id ON public.cognitive_dialogue_memory_jobs USING btree (user_id);
CREATE INDEX ix_cognitive_dialogue_memory_snapshots_user_id ON public.cognitive_dialogue_memory_snapshots USING btree (user_id);
CREATE INDEX ix_cognitive_shadow_jobs_captured_at ON public.cognitive_shadow_jobs USING btree (captured_at);
CREATE INDEX ix_cognitive_shadow_jobs_conversation_source_turn ON public.cognitive_shadow_jobs USING btree (conversation_id, source_turn_at, turn_id);
CREATE INDEX ix_cognitive_shadow_jobs_status_captured_at ON public.cognitive_shadow_jobs USING btree (status, captured_at);
CREATE INDEX ix_cognitive_shadow_jobs_user_id ON public.cognitive_shadow_jobs USING btree (user_id);
CREATE INDEX ix_cognitive_shadow_traces_conversation_created ON public.cognitive_shadow_traces USING btree (conversation_id, created_at);
CREATE INDEX ix_cognitive_shadow_traces_predecessor_trace_id ON public.cognitive_shadow_traces USING btree (predecessor_trace_id);
CREATE INDEX ix_cognitive_shadow_traces_segment_id ON public.cognitive_shadow_traces USING btree (segment_id);
CREATE INDEX ix_cognitive_shadow_traces_user_id ON public.cognitive_shadow_traces USING btree (user_id);
CREATE INDEX ix_dialogue_interpretations_conversation_sequence ON public.cognitive_dialogue_interpretations USING btree (conversation_id, source_conversation_sequence);
CREATE INDEX ix_dialogue_memory_jobs_conversation ON public.cognitive_dialogue_memory_jobs USING btree (conversation_id, target_conversation_sequence);
CREATE INDEX ix_dialogue_memory_jobs_status_created ON public.cognitive_dialogue_memory_jobs USING btree (status, created_at);
CREATE INDEX ix_dialogue_memory_snapshots_conversation_through ON public.cognitive_dialogue_memory_snapshots USING btree (conversation_id, through_conversation_sequence);
CREATE TRIGGER cognitive_dialogue_interpretations_immutable BEFORE UPDATE ON public.cognitive_dialogue_interpretations FOR EACH ROW EXECUTE FUNCTION public.cognitive_dialogue_interpretations_guard();
CREATE TRIGGER cognitive_dialogue_memory_snapshots_immutable BEFORE UPDATE ON public.cognitive_dialogue_memory_snapshots FOR EACH ROW EXECUTE FUNCTION public.cognitive_dialogue_memory_snapshots_guard();
CREATE TRIGGER cognitive_shadow_jobs_immutable BEFORE UPDATE ON public.cognitive_shadow_jobs FOR EACH ROW EXECUTE FUNCTION public.cognitive_shadow_jobs_guard();
CREATE TRIGGER cognitive_shadow_traces_immutable BEFORE UPDATE ON public.cognitive_shadow_traces FOR EACH ROW EXECUTE FUNCTION public.cognitive_shadow_traces_guard();
ALTER TABLE ONLY public.cognitive_dialogue_interpretations
    ADD CONSTRAINT cognitive_dialogue_interpretations_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_interpretations
    ADD CONSTRAINT cognitive_dialogue_interpretations_source_message_id_fkey FOREIGN KEY (source_message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_interpretations
    ADD CONSTRAINT cognitive_dialogue_interpretations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_heads
    ADD CONSTRAINT cognitive_dialogue_memory_heads_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_heads
    ADD CONSTRAINT cognitive_dialogue_memory_heads_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.cognitive_dialogue_memory_snapshots(id);
ALTER TABLE ONLY public.cognitive_dialogue_memory_heads
    ADD CONSTRAINT cognitive_dialogue_memory_heads_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_jobs
    ADD CONSTRAINT cognitive_dialogue_memory_jobs_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_jobs
    ADD CONSTRAINT cognitive_dialogue_memory_jobs_trigger_message_id_fkey FOREIGN KEY (trigger_message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_jobs
    ADD CONSTRAINT cognitive_dialogue_memory_jobs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT cognitive_dialogue_memory_snapshots_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT cognitive_dialogue_memory_snapshots_through_message_id_fkey FOREIGN KEY (through_message_id) REFERENCES public.chat_messages(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.cognitive_dialogue_memory_snapshots
    ADD CONSTRAINT cognitive_dialogue_memory_snapshots_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_chat_job_id_fkey FOREIGN KEY (chat_job_id) REFERENCES public.chat_jobs(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_jobs
    ADD CONSTRAINT cognitive_shadow_jobs_user_message_id_fkey FOREIGN KEY (user_message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.cognitive_shadow_jobs(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_predecessor_trace_id_fkey FOREIGN KEY (predecessor_trace_id) REFERENCES public.cognitive_shadow_traces(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.cognitive_shadow_traces
    ADD CONSTRAINT cognitive_shadow_traces_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
"""
