# Durable Live-Shadow Trace V0 implementation brief

Status: ready for implementation; observational shadow only

This brief defines how the already implemented canonical cognitive pipeline is
invoked on real conversation turns and how its exact inputs, outputs, failures,
versions, and timings are stored durably without changing anything the user
sees or anything the assistant executes.

It does **not** approve cognitive output for live response control. Action
Selection V0 is implementation-accepted for deterministic shadow execution,
but it is not behaviorally calibrated or live-approved.

## 1. Objective

For an eligible real user turn, create one durable observational record that
answers:

```text
What exact bounded input and trusted runtime facts were captured?
What did each canonical cognitive stage produce?
What abstract action plan would Action Selection V0 have selected?
Which versions, evidence, gates, scores, state transitions, and failures
produced that result?
```

The implementation must connect:

```text
durable user turn + immutable turn-time capture
                       |
                       v
              durable shadow job
                       |
                       v
             CognitivePipelineV0.run(...)
                       |
                       v
      immutable completed / failed / skipped trace record
```

The existing chat agent remains the sole producer of the actual response:

```text
real user turn
    |
    +--> existing chat agent ----------------> user-visible response
    |
    `--> CognitivePipelineV0 live shadow ----> internal trace only
```

There must be no arrow from the shadow result back into live generation,
prompt construction, tool execution, memory, delivery, or chat-job status.

## 2. Purpose

Durable live-shadow traces provide evidence needed to:

- measure how often the canonical pipeline can execute on real inputs;
- expose missing upstream typed-object producers without fabricating inputs;
- inspect Perception, Interpretation, Goal, Appraisal, Dynamic State, and
  Action decisions together;
- evaluate candidate construction, gate behavior, action ordering, and `STOP`;
- distinguish implementation failures from behavioral-calibration questions;
- reproduce decisions against exact versioned inputs;
- establish latency and storage costs before any live response influence;
- prepare later realization, outcome observation, review, and offline learning;
  and
- provide regression evidence for future version promotion.

A shadow trace is observational evidence. It is not a training label, reward,
reviewed preference, successful outcome, memory, or permission to act.

## 3. Definitions

- **live input**: an actual persisted user conversation turn.
- **shadow execution**: cognition runs on that input, but its result has zero
  authority over the user-visible response or any side effect.
- **capture**: the immutable, turn-time facts needed to compile a canonical
  pipeline request later.
- **compiled request**: one validated `CognitivePipelineRequest` created from a
  capture plus the preceding shadow state/goal lineage.
- **trace**: the exact validated `CognitivePipelineTrace` returned by the
  canonical runner.
- **segment**: a contiguous per-conversation chain of successful shadow traces.
  A recorded reset begins a new segment after a predecessor gap.
- **semantic digest**: a stable digest of decision-relevant content excluding
  inherently variable diagnostics such as wall-clock timing and generated
  outer trace identifiers.
- **live-approved**: permitted to affect user-visible behavior. Nothing in this
  brief grants that status.

## 4. Current repository facts

The implementation agent must inspect the current repository before editing.
At the time of this brief:

- `CognitivePipelineV0` already connects Perception V0, Interpretation V0,
  Goal V0, Appraisal, Dynamic State, and Action Selection V0 in process.
- The canonical action-extended pipeline is deterministic apart from generated
  outer trace identifiers and measured timings.
- Action Selection is implementation-accepted for deterministic shadow V0.
- No canonical cognitive stage is invoked by the live chat response path.
- `backend/app/workers/chat.py` owns durable production chat generation through
  `ChatJob`; web, voice, and WhatsApp converge there in configured production.
- A local WebSocket fallback can generate without the external worker and must
  not become an invisible coverage gap.
- Chat messages and jobs are durable, but no canonical cognitive trace table or
  worker exists.
- `LearningMemory` exists as older storage scaffolding but is not the canonical
  live-shadow trace and must not be written by this task.
- `OUTCOME_OBSERVATION_SCHEMA_V0.md` is a later outcome design. This task does
  not infer or persist outcome evidence.
- The canonical Pipeline requires already compiled personality inputs. The
  compiler must not silently invent an Interpretation bias profile.
- There is no live producer for the complete set of typed Action grounded
  objects. Empty explicit inputs are valid; fixture-derived or prose-derived
  objects are forbidden.

## 5. Authority order

Use these sources in this order:

1. this live-shadow implementation brief for capture, persistence, queueing,
   isolation, privacy, and rollout;
2. `ACTION_SELECTION_V0_DESIGN.md` for accepted Action architecture;
3. `ACTION_SELECTION_V0_IMPLEMENTATION.md` for Action contracts and invariants;
4. `COGNITIVE_PIPELINE_V0_IMPLEMENTATION.md` and current pipeline contracts for
   canonical request/trace behavior;
5. stage implementation briefs for each stage's invariants; and
6. the main `README.md` status section for current completion state.

This brief adds an observational integration boundary. It does not weaken any
stage invariant or supersede stage-owned contracts.

## 6. Scope

### Included

- a versioned immutable shadow-capture contract;
- durable PostgreSQL capture/job and terminal trace records;
- idempotent enqueueing for real chat turns;
- per-conversation ordered job claiming and retry leases;
- a deterministic compiler from captured facts to
  `CognitivePipelineRequest`;
- explicit first-turn state initialization and segment-reset semantics;
- execution of the existing `CognitivePipelineV0` without changing it into a
  persistence service;
- exact JSON persistence of the validated compiled request and completed trace;
- typed failed and skipped terminal records;
- stable request, trace, and semantic digests;
- feature flags, deterministic sampling, kill switch, metrics, and rollout;
- privacy, authorization, deletion, and retention behavior;
- tests proving zero influence on user-visible behavior; and
- documentation of coverage gaps and required input producers.

### Excluded

- using the selected plan to generate or modify response wording;
- adding the shadow plan to the existing agent prompt;
- invoking tools or external actions from cognitive output;
- changing chat, delivery, memory, goal, or user state because of the result;
- treating planned effects as actual effects or goal satisfaction;
- language realization or realization-fidelity evaluation;
- outcome inference, reward, credit assignment, reflection, or consolidation;
- writing `LearningMemory`;
- online learning or per-turn weight changes;
- model training, dataset promotion, or automatic review labels;
- action-definition discovery or lifecycle promotion;
- normalizing the complete trace into many stage-specific database tables;
- a user-facing or public trace API; and
- live approval of any action.

## 7. Non-negotiable invariants

### 7.1 Zero behavioral influence

Shadow state and results must never be read by:

- `stream_chat`, the agent graph, prompts, or model selection;
- tool registries, authorization checks, or tool executors;
- TTS, WhatsApp delivery, or WebSocket event publication;
- response cancellation or chat-job completion logic;
- long-term memory extraction, retrieval, or consolidation; or
- awareness, salience, initiative, or notification systems.

Enabling shadow mode may add an internal capture row and queue wake-up only.
Shadow failure must never fail, cancel, delay completion of, or replace a live
chat response.

### 7.2 No invented canonical inputs

The compiler may project only captured trusted facts and accepted canonical
defaults with explicit provenance. It must not infer structured objects from:

- fixture expectations;
- fixture adapters;
- design-case titles, notes, or rationales;
- the live agent's generated response;
- free-form awareness prose;
- raw core-memory JSON in place of a compiled personality projection;
- untrusted client claims about capability, authorization, consent, or safety;
  or
- later database state that was not part of the turn-time capture.

Missing required inputs produce a typed skipped or failed trace. They do not
produce guessed values.

### 7.3 Shadow is not outcome evidence

`PlannedActionEffect`, goal coverage, Action score, `STOP`, continued
conversation, or successful trace completion must not be recorded as an actual
outcome. No row created by this task is a reward or reviewed label.

### 7.4 Historical immutability

Capture payloads and terminal trace payloads are append-only. Corrections create
new revisions or review records in a later system; they never rewrite what the
pipeline originally received or produced.

Mutable queue fields such as lease, attempt count, and processing status belong
to the job record, not the immutable terminal trace.

### 7.5 Fail closed without failing chat

The cognitive computation fails closed: it returns no partial successful trace
after a pipeline failure. The surrounding observational service fails open with
respect to chat: it records the safe failure and leaves the live response path
untouched.

## 8. Versioned contracts

Create a small package such as:

```text
backend/app/ai/Cognitive/system/v0/live_shadow/
    __init__.py
    models.py
    compiler.py
    digests.py
    service.py
```

Initial versions:

```text
cognitive-shadow-capture-v0.1
cognitive-shadow-terminal-v0.1
cognitive-shadow-compiler-v0.1
cognitive-shadow-semantic-digest-v0.1
cognitive-shadow-worker-v0.1
```

Every Pydantic contract must be immutable, `extra="forbid"`, JSON round-trip
safe, and explicit about schema version.

### 8.1 Capture envelope

Define one logical `CognitiveShadowCapture` containing at least:

```text
capture_id
schema_version
user_ref
conversation_ref
turn_id
chat_job_ref or explicit local-fallback marker
focus_message_id
ordered bounded messages with exact message id, role, and content
context_truncated
source channel
configured language tag and provenance
trusted attachment facts
trusted capability snapshot
authorization / consent / export-control facts
typed application-safety decision
response obligation and evidence reference
conversation status
task contract when one actually exists
last finalized tool event when one actually exists
authorized structured memory projections and retrieval versions
authorized relationship projection when one actually exists
compiled personality projection or explicit missing marker
explicit optional-input omissions
turn-time capture timestamp
capture producer/version
```

Do not store provider access tokens, cookies, API keys, raw audio, Redis data,
or unredacted tool credentials.

The capture must preserve exact message IDs. Do not use `get_history()`'s
merged role/content view because adjacent messages may be combined and their
identity lost. Add a bounded-message query that returns ordered durable message
records as of the source turn/job boundary.

### 8.2 Terminal envelope

Define one `CognitiveShadowTerminalRecord` with a closed status:

```text
completed
failed
skipped
```

Common fields:

```text
terminal_schema_version
capture_id / job_id / turn_id / conversation_ref
segment_id and predecessor_trace_id
segment_reset_reason or null
compiler_version and worker_version
capture_digest
compiled_request_digest or null
semantic_digest or null
all canonical component versions available at termination
started_at / completed_at
```

`completed` requires the exact validated `CognitivePipelineRequest` and
`CognitivePipelineTrace` JSON payloads.

`failed` requires a typed failure envelope with stage, nested stage/phase when
available, stable code, safe message, attempt count, and retryability. It must
not contain raw exception text or user content.

`skipped` requires a stable reason such as:

```text
feature_disabled
not_sampled
unsupported_language
missing_personality_projection
missing_required_runtime_fact
unauthorized_input
predecessor_unavailable
capture_schema_unsupported
```

Disabled or unsampled turns may be counted without storing conversation
content. Decide explicitly whether they need rows; do not create full captures
for unsampled turns merely to record that sampling excluded them.

## 9. Turn-time capture boundary

Capture must occur after the user message and source `ChatJob` are durable and
before the live model begins producing the assistant response.

Use one shared capture service for web, voice, and WhatsApp. The production
worker path and local fallback must call the same compiler/service or record an
explicit unsupported-path skip. Do not duplicate slightly different capture
logic inside several route handlers.

The recommended sequence is:

```text
persist user message and ChatJob
    -> resolve deterministic sampling decision
    -> construct trusted capture from server-owned facts
    -> insert shadow job idempotently
    -> commit capture/job independently of cognitive execution
    -> wake shadow worker best-effort
    -> start/continue existing live generation unchanged
```

If capture fails after the chat turn is durable:

- log a content-free error and increment a metric;
- do not change the `ChatJob` status;
- do not suppress or replace the live response; and
- never retry capture by reading a later moving conversation snapshot as if it
  were the original turn-time state.

Use PostgreSQL as the durable queue source of truth. Redis may wake the worker,
but loss of a Redis notification must not lose a job.

## 10. Deterministic sampling

The feature must default off. When enabled below 100%, sample deterministically
from a stable hash of `(sampling_policy_version, turn_id)`.

Do not use process-local randomness. Retries and multiple application instances
must make the same selection for the same turn.

Record the sampling policy version and selected/not-selected result without
using user content. Sampling must not target sensitive content unless a later
reviewed policy explicitly authorizes that use.

## 11. Canonical input compilation

The compiler consumes only the immutable capture and the previous successful
shadow lineage. It returns either one validated `CognitivePipelineRequest` or a
typed skip/failure.

### 11.1 Identity

- Use the existing durable chat `turn_id` as `cognitive_turn_id`.
- Use the durable conversation UUID string as `conversation_ref`.
- Use the persisted user-message UUID as `focus_message_id`.
- The focus message must be the final bounded message.
- Runtime and capability snapshot IDs must be deterministic functions of the
  turn and compiler version.
- Generated pipeline/turn trace IDs should use injected deterministic factories
  when reproducibility requires stable semantic output. Actual measured timings
  remain diagnostic and are excluded from the semantic digest.

### 11.2 Messages and language

- Project exact persisted `ChatMessage` records into `PerceptionMessage`.
- Preserve roles and contents without merging.
- Apply the same documented bounded-history limit on every retry.
- Set `context_truncated=True` when older eligible messages were omitted.
- V0 accepts only `language_tag="en"`.
- The language value must come from a configured application/conversation
  locale with provenance. Do not claim that content was language-detected when
  no detector ran.
- Unsupported configured languages produce a typed skip, not silent English
  processing.

### 11.3 Attachments

Create `AttachmentFact` only from server-verified image/asset records available
to the user and the captured turn. Client-supplied IDs alone are not trusted
existence or authorization evidence.

An absent attachment is different from an unavailable attachment subsystem.
Preserve that distinction through `attachments_available`.

### 11.4 Capabilities, authorization, and consent

Map only registered server-owned capability facts. OAuth tokens and scopes may
support the projection but must never enter the capture payload.

Capability keys require a versioned mapping. Do not place arbitrary tool names
or prompt text into `available_capabilities`.

Authorization and consent remain explicit. Missing values remain `None`; they
must not become granted merely because a tool is technically available.

### 11.5 Application safety

The safety decision must be created by the trusted application safety boundary,
not by cognition, the client, a prompt, or the live LLM.

Until that policy producer exists, create the canonical typed
`unavailable` decision with `runtime_unavailable` provenance. Do **not** create a
synthetic `allow`. This intentionally permits observation of fail-closed
commitment behavior.

The safety decision must cite the same captured runtime snapshot. Its policy and
schema versions must survive unchanged into every Action gate and terminal
trace.

### 11.6 Personality projections

The pipeline requires three related but distinct inputs:

- `InterpretationBiasProfile`;
- Dynamic State `PersonalityProfile`; and
- `ActionPersonalityProfile`.

The Action contract already supports an explicit versioned `unknown` profile.
Interpretation does not permit the compiler to silently create a neutral
profile. Therefore live-shadow enablement requires one of:

1. an accepted deterministic Personality Runtime Mapper that produces all
   three projections from authorized core identity; or
2. a separately reviewed, explicitly configured baseline projection whose
   values, provenance, scope, and compiler version are recorded as a baseline,
   not misrepresented as learned or user-specific.

If neither exists, persist `skipped: missing_personality_projection`. Do not
copy fixture profiles or invent `0.5` values inside the live-shadow compiler.

All three projections must share the captured core-memory/version lineage. The
previous Dynamic State snapshot's mapper version must equal the current state
personality mapper version.

### 11.7 Memory and relationship inputs

Only structured, authorized, versioned projections may enter the canonical
request. Do not parse the current long-term-memory prompt string back into
cognitive records.

If no approved structured projection exists, pass an empty tuple/`None` and
record the omission in capture metadata. Empty input must not be described as
evidence that the user has no memories or relationship history.

### 11.8 Goal and Dynamic State lineage

Process shadow jobs in source-turn order per conversation.

For the first eligible turn in a segment:

- create a deterministic initial Dynamic State snapshot using
  `DynamicStateSnapshotFactory`;
- use a distinct deterministic bootstrap cognitive-turn ID;
- set `previous_goal_snapshot=None`; and
- record `segment_reset_reason` as `initial_conversation` or the typed reset
  reason.

For a continuous later turn:

- take the exact final Dynamic State snapshot from the previous completed
  shadow trace;
- take the exact previous Goal snapshot from that trace;
- verify conversation, revision, predecessor, mapper, and version lineage; and
- cite the preceding trace as `predecessor_trace_id`.

If an earlier captured turn in the same conversation failed or was skipped,
never pretend the state chain is continuous. Either:

- skip later turns until an explicit recovery policy is applied; or
- begin a new segment with deterministic initialization and record
  `segment_reset_reason="predecessor_gap"` plus the missing predecessor refs.

V0 should use the second option so observation can continue while the gap stays
visible. Segment resets are diagnostic facts, not conversational state changes.

Do not write the shadow state into production `WorkingMemory`. The durable
completed trace is the authoritative shadow predecessor for this task.

### 11.9 Action-specific typed inputs

Use only real typed producers for:

- `action_grounded_objects`;
- `action_target_descriptors`;
- `prior_action_records`;
- `prior_realization_records`; and
- `action_observed_outcomes`.

Until such producers exist, pass empty tuples and record producer coverage as
missing. Never translate design fixtures, live-response text, or expected plans
into those objects.

This limitation means early shadow Action output will often be acknowledgement,
clarification, or greeting. That is valid coverage evidence, not permission to
fabricate richer inputs.

### 11.10 Outcomes

Pass no `OutcomeObservation` or `ObservedOutcomeRecord` unless a later accepted
outcome runtime created it with the required provenance. The next user message
alone is not a positive or negative outcome.

## 12. Persistence design

Use two tables for V0: one mutable durable job/capture row and one immutable
terminal row. Do not create a table per cognitive stage yet; canonical JSON
contracts are still evolving and the complete validated payload is the unit of
replay.

### 12.1 `cognitive_shadow_jobs`

Recommended fields:

```text
id UUID primary key
user_id UUID foreign key users(id) on delete cascade
conversation_id UUID foreign key conversations(id) on delete cascade
turn_id UUID not null
chat_job_id UUID nullable foreign key chat_jobs(id) on delete set null
user_message_id UUID foreign key chat_messages(id) on delete cascade
capture_schema_version varchar not null
capture_payload jsonb not null
capture_digest char(64) not null
sampling_policy_version varchar not null
source_channel varchar not null
status varchar: pending | processing | completed | failed | skipped
attempts integer not null
claim_token varchar nullable
lease_expires_at timestamptz nullable
last_error_code varchar nullable
last_error_safe text nullable
captured_at timestamptz not null
processed_at timestamptz nullable
created_at / updated_at
```

Constraints and indexes:

- unique `(turn_id)` for V0 idempotency;
- unique `user_message_id` where appropriate;
- index `(status, captured_at)` for claiming;
- index `(conversation_id, captured_at)` for ordered serialization;
- capture digest format validation;
- closed status check;
- attempt count non-negative; and
- processing rows require a claim token and lease.

The capture payload is immutable after insert. Only queue-owned fields may
change.

### 12.2 `cognitive_shadow_traces`

Recommended fields:

```text
id UUID primary key
job_id UUID unique foreign key cognitive_shadow_jobs(id) on delete cascade
user_id UUID foreign key users(id) on delete cascade
conversation_id UUID foreign key conversations(id) on delete cascade
turn_id UUID unique not null
segment_id UUID not null
predecessor_trace_id UUID nullable self-reference on delete set null
segment_reset_reason varchar nullable
terminal_schema_version varchar not null
terminal_status varchar: completed | failed | skipped
compiled_request_payload jsonb nullable
pipeline_trace_payload jsonb nullable
failure_payload jsonb nullable
capture_digest char(64) not null
compiled_request_digest char(64) nullable
trace_payload_digest char(64) nullable
semantic_digest char(64) nullable
versions jsonb not null
timings jsonb nullable
started_at / completed_at
created_at
```

Terminal invariants:

- exactly one terminal row per job and turn;
- `completed` requires request, trace, all digests, versions, and timings and
  forbids a failure payload;
- `failed` requires a failure payload and forbids a successful trace;
- `skipped` requires a skip reason and forbids request/trace claims that did not
  occur;
- stored request and trace must validate through their canonical Pydantic
  contracts before insert;
- `trace.cognitive_turn_id`, conversation, focus message, and versions must
  agree with the capture and compiled request; and
- no update path exists for terminal semantic payloads.

### 12.3 Why this is not `LearningMemory`

The trace stores what happened inside the shadow computation. It contains no
reviewed target, feedback judgment, reward, or accepted update. A later review
system may reference a trace; it must not convert the trace itself into ground
truth.

## 13. Queue, claiming, and idempotency

Follow the existing durable worker pattern, including claim tokens and expiring
leases, but preserve per-conversation source order.

A job is claimable only when:

- it is pending or its processing lease expired;
- no earlier pending/processing job exists for the same conversation; and
- no other live shadow job for the conversation holds a lease.

Lock the conversation or use an equivalent advisory/row-lock strategy before
the final claim so two workers cannot process different turns of the same
conversation concurrently.

Use conditional updates containing `(job_id, claim_token, status=processing)`
for completion. A stale worker must never overwrite a reclaimed job.

Retries:

- reuse the immutable capture;
- never recapture moving DB state;
- never create a second terminal row;
- classify validation/deterministic contract failures as non-retryable;
- retry transient database/worker failures within a bounded attempt policy;
- record exhausted attempts as one terminal failure; and
- use an idempotency key based on the durable turn and capture version.

## 14. Worker execution

Create a dedicated worker module, for example:

```text
backend/app/workers/cognitive_shadow.py
backend/app/services/cognitive_shadow.py
```

Do not run the full cognitive pipeline synchronously inside the chat worker or
WebSocket request. Even deterministic local work can add user-visible latency.

Worker sequence:

1. claim the earliest eligible job with a lease;
2. load and validate the immutable capture;
3. resolve the preceding terminal trace/segment under a conversation lock;
4. compile one canonical `CognitivePipelineRequest`;
5. validate and digest the request;
6. execute the existing `CognitivePipelineV0`;
7. JSON-round-trip validate the resulting trace;
8. recompute cross-record ownership and version invariants;
9. compute raw-payload and semantic digests;
10. insert one immutable terminal record;
11. conditionally complete the owned job; and
12. commit terminal insert and job completion atomically.

The worker must not import the fixture adapter, chat agent, prompt system,
realizer, tool executor, long-term-memory writer, or outcome worker.

The worker runner may add a separately named thread/queue. For production
rollout, isolate its concurrency and resource budget so shadow CPU work cannot
materially increase chat latency.

## 15. Digests and replay

Compute SHA-256 over canonical JSON with sorted keys and stable separators.

Keep three meanings separate:

- `capture_digest`: exact immutable capture;
- `compiled_request_digest`: exact canonical pipeline request; and
- `trace_payload_digest`: exact stored trace including diagnostic IDs/timings.

Also compute a versioned `semantic_digest` over decision-relevant trace content
with only reviewed volatile fields removed. At minimum, outer generated trace
IDs and measured timing values may be excluded. Do not exclude stage outputs,
evidence, versions, candidates, gates, features, scores, moves, or state.

The normalization function must be explicit, tested, and versioned. Never
silently change normalization under the same digest version.

Replay tooling should:

- load and validate the stored compiled request;
- run the installed matching versions only;
- compare the semantic projection/digest;
- report version unavailable rather than replaying with a different policy;
  and
- never rewrite the historical trace after a mismatch.

## 16. Failure and skip contract

Preserve nested canonical failure ownership:

```text
capture
compile
pipeline.validation
pipeline.perception
pipeline.handoff_validation
pipeline.interpretation
pipeline.goal_update
pipeline.cognitive_turn
cognitive_turn.appraisal
cognitive_turn.dynamic_state
cognitive_turn.action_handoff_validation
cognitive_turn.action_selection.<action phase>
cognitive_turn.trace_validation
persistence
```

Store stable codes and safe messages only. Raw exceptions may be logged through
the protected server logging policy but must not enter user-facing events or
the terminal safe envelope.

A cognitive failure must never publish a WebSocket/Redis error event for the
live turn and must never modify the assistant message or `ChatJob` outcome.

## 17. Privacy, access, deletion, and retention

The capture and trace may contain conversation text, evidence spans, inferred
hypotheses, and motivational state. Treat them as sensitive user conversation
data.

Required protections:

- user and conversation foreign keys with cascade deletion;
- no cross-user query path;
- no public API or frontend serialization in V0;
- application logs contain IDs/codes, not payloads or message text;
- no credentials, tokens, raw audio, provider payloads, or secrets;
- the same or stricter retention class as the source conversation;
- conversation deletion removes jobs and traces;
- account deletion removes all shadow data;
- backup/deletion behavior is documented consistently with conversation data;
- deterministic digests are provenance, not permission to retain content; and
- any future research export requires a separate authorization and
  de-identification design.

Before broad production sampling, set an explicit retention duration and
review whether full compiled requests are necessary for that duration. Do not
retain indefinitely by accident.

## 18. Configuration and kill switch

Add explicit settings with conservative defaults, for example:

```text
cognitive_live_shadow_enabled = false
cognitive_live_shadow_sample_rate = 0.0
cognitive_live_shadow_sampling_policy_version = "shadow-sampling-v0.1"
cognitive_live_shadow_queue_name = "macrovision:cognitive-shadow:wakeups"
cognitive_live_shadow_worker_lease_seconds = 300
cognitive_live_shadow_worker_poll_seconds = 2
cognitive_live_shadow_max_attempts = 3
cognitive_live_shadow_retention_days = <reviewed value>
```

Validate sample rate in `[0, 1]`, positive leases/attempts, and a reviewed
retention value before enabling capture.

Disabling the feature must stop new captures immediately without deleting or
corrupting existing records. Already captured jobs may be drained or moved to a
typed skipped status according to an explicit shutdown policy.

## 19. Observability

Publish aggregate internal metrics without message content:

```text
eligible turns
sampled turns
capture successes/failures
pending and oldest-job age
completed/failed/skipped by stable phase/code
pipeline stage latency distributions
total worker CPU/wall latency
trace payload sizes
segment resets and predecessor gaps
action plan length
selected action-key counts
no-viable-plan and STOP counts
missing input-producer counts
chat latency with shadow disabled vs enabled sampling
```

Action-key aggregates must be access-controlled and evaluated for privacy risk
before external telemetry export.

No metric may classify continued conversation or silence as success.

## 20. Recommended files

The implementation should normally add or modify:

```text
backend/app/ai/Cognitive/system/v0/live_shadow/__init__.py
backend/app/ai/Cognitive/system/v0/live_shadow/models.py
backend/app/ai/Cognitive/system/v0/live_shadow/compiler.py
backend/app/ai/Cognitive/system/v0/live_shadow/digests.py
backend/app/services/cognitive_shadow.py
backend/app/workers/cognitive_shadow.py
backend/app/workers/runner.py
backend/app/core/config.py
backend/app/db/models.py
backend/alembic/versions/<revision>_add_cognitive_shadow_trace.py
backend/app/workers/chat.py or a shared pre-generation capture owner
backend/tests/test_cognitive_shadow_contract.py
backend/tests/test_cognitive_shadow_compiler.py
backend/tests/test_cognitive_shadow_persistence.py
backend/tests/test_cognitive_shadow_worker.py
backend/tests/test_cognitive_shadow_chat_isolation.py
backend/tests/test_cognitive_shadow_replay.py
```

Exact filenames may vary, but ownership must stay separated:

- Cognitive contracts/compiler under `Cognitive/system/v0/live_shadow`;
- database queue operations under `services`;
- polling/execution under `workers`; and
- canonical cognition inside the existing pipeline/stage packages.

Do not place SQLAlchemy, Redis, or chat-agent imports inside the canonical stage
engines.

## 21. Required tests

### 21.1 Contract tests

- immutable, extra-forbid, versioned capture and terminal contracts;
- closed statuses, failure phases, skip reasons, and reset reasons;
- exact JSON round trips;
- canonical digest stability and mutation sensitivity;
- semantic digest excludes only reviewed volatile fields; and
- invalid ownership/version combinations fail.

### 21.2 Capture tests

- exact durable message IDs, roles, contents, order, and focus;
- bounded history and `context_truncated` behavior;
- web, voice, WhatsApp, and local-fallback handling;
- server-verified attachment projection;
- no access tokens, cookies, secrets, raw audio, or awareness prose;
- deterministic sampling across processes/retries;
- capture idempotency for duplicate enqueue; and
- post-turn database changes cannot alter an existing capture.

### 21.3 Compiler tests

- exact identity and runtime-snapshot lineage;
- unsupported language skips;
- missing personality projection skips rather than inventing neutrality;
- explicit reviewed baseline profile, when configured, retains provenance;
- unavailable safety produces typed fail-closed safety input, never `allow`;
- empty optional memory/relationship/action inputs remain explicit omissions;
- first-turn deterministic state initialization;
- exact previous Goal/Dynamic State handoff for continuous turns;
- predecessor failure begins an explicitly marked new segment;
- cross-user, cross-conversation, cross-turn, stale, or unauthorized inputs are
  rejected; and
- compiled requests validate through `CognitivePipelineRequest`.

### 21.4 Persistence and queue tests

- database constraints and indexes;
- user/conversation deletion cascades;
- one job and one terminal record per turn;
- capture payload cannot be changed after insert;
- only queue-owned fields mutate;
- claim token and lease ownership;
- expired lease reclaim;
- stale worker cannot complete a reclaimed job;
- per-conversation source ordering under concurrent workers;
- different conversations may run concurrently;
- Redis wake-up loss recovers through PostgreSQL polling; and
- terminal insert and job completion are atomic.

### 21.5 Pipeline and replay tests

- real `CognitivePipelineV0` executes from a compiled captured turn;
- complete request and trace survive JSON round trip;
- exact ownership and versions persist;
- semantic replay matches for the installed versions;
- mutated stored inputs or outputs change/reject the digest;
- nested Action failure phase is retained;
- no partial trace is stored as completed; and
- fixed-registry `DEFAULT_REGISTRY` limitation is documented and guarded.

### 21.6 Behavioral-isolation tests

With the same live input and mocked deterministic live agent:

- shadow disabled and enabled emit the same user-visible events and text;
- shadow selection is never added to prompts or model inputs;
- shadow failure does not alter chat-job or assistant-message status;
- shadow output never invokes a tool;
- shadow output never writes Working Memory, Semantic/Episodic Memory, or
  `LearningMemory`;
- shadow output never schedules memory consolidation;
- no frontend/Redis event exposes the trace;
- cancellation behavior is unchanged; and
- capture/queue wake-up does not hold the streaming loop open.

### 21.7 Regression and migration tests

- migration upgrade and downgrade in an isolated test database;
- existing chat, WhatsApp, memory, Cognitive Pipeline, Goal, and Action tests;
- full backend test suite; and
- `git diff --check`.

## 22. Acceptance criteria

Durable Live-Shadow Trace V0 is implementation-accepted only when:

1. the feature is disabled by default and has a tested kill switch;
2. eligible sampled real turns create idempotent durable captures;
3. capture uses exact turn-time server-owned facts and never moving later state;
4. the worker is durable, retry-safe, lease-safe, and ordered per conversation;
5. compiled requests pass the canonical Pipeline contract without fixture or
   prose inference;
6. missing personality input skips explicitly or uses a separately reviewed
   baseline projection with provenance;
7. absent safety policy never becomes synthetic `allow`;
8. first-turn and predecessor-gap state lineage is explicit;
9. completed traces retain exact canonical requests, outputs, versions, and
   decision-relevant digests;
10. failed/skipped runs are durable and typed rather than disappearing;
11. no trace is written to `LearningMemory` or treated as outcome/reward;
12. conversation/account deletion removes associated shadow data;
13. tests prove no user-visible, tool, memory, delivery, or prompt influence;
14. canary operation shows bounded storage and no material live-chat latency
    regression at the reviewed sampling rate;
15. focused and complete regression suites pass; and
16. an independent audit confirms the isolation boundary before sampling is
    increased.

Passing unit tests alone is not sufficient if capture uses later moving state,
if missing inputs are fabricated, or if shadow computation can influence live
response behavior.

## 23. Rollout

Use staged rollout:

```text
Stage 0: tests only, feature disabled
Stage 1: local/staging, explicit test accounts, 100% capture
Stage 2: production canary, very small deterministic sample
Stage 3: increase sample only after latency, privacy, failure, and storage review
Stage 4: behavioral review dataset references selected traces
```

At every stage, Action remains shadow-only. A later live-approval brief must
define realization, behavioral evaluation, promotion thresholds, rollback, and
the exact action subset permitted to influence responses.

## 24. Implementation order

Implement in this order:

1. finalize capture and terminal contracts;
2. review the initial personality-projection source or accept explicit skips;
3. add database models and migration;
4. implement deterministic digests;
5. implement idempotent capture persistence and sampling;
6. implement exact bounded-message and trusted-runtime capture;
7. implement per-conversation job claiming and leases;
8. implement first-turn/segment lineage compilation;
9. execute the real canonical Pipeline in the worker;
10. persist completed/failed/skipped terminal records atomically;
11. add worker-runner integration and kill switch;
12. add isolation, privacy, replay, concurrency, and migration tests;
13. run focused and full regression suites;
14. perform an independent isolation/conformance audit;
15. update README status factually; and
16. enable only the reviewed staging/canary configuration.

Do not begin by calling `CognitivePipelineV0` synchronously in the WebSocket or
by creating a public trace endpoint.

## 25. Prohibited shortcuts

Do not:

- feed shadow output to the live agent;
- run cognition inline on the latency-critical streaming path;
- let shadow failure fail the chat turn;
- use fixture adapters or design cases as live input producers;
- construct expected actions and pass them to the engine;
- parse prose into trusted runtime, personality, memory, or action objects;
- synthesize a safety `allow` decision;
- silently create a neutral Interpretation bias profile;
- read future messages or post-response state into a turn-time capture;
- treat planned effects, continued engagement, or silence as outcomes;
- write `LearningMemory`, semantic memory, episodic memory, or Working Memory;
- store credentials, raw audio, or provider tokens;
- expose traces through existing chat/history APIs;
- overwrite historical capture or trace payloads;
- process two turns from the same conversation concurrently;
- use completion time instead of source-turn order for predecessor selection;
- drop failed/skipped jobs without a terminal record;
- automatically approve or promote any action; or
- claim behavioral calibration from trace volume alone.

## 26. Required implementation-agent report

The implementing agent must report:

1. every file and migration created or modified;
2. capture, terminal, compiler, digest, and worker versions;
3. exact capture timing and every supported channel/path;
4. sampling and kill-switch behavior;
5. input-producer coverage and every explicit omission;
6. personality and safety provenance decisions;
7. first-turn, predecessor, gap, and segment-reset behavior;
8. database constraints, indexes, deletion, and retention behavior;
9. job idempotency, claim, lease, retry, and concurrency evidence;
10. replay and digest evidence;
11. proof of zero live-response/tool/memory influence;
12. focused and full test commands with counts;
13. canary latency/storage/failure measurements when available;
14. remaining risks and missing producers; and
15. the exact README status change, if acceptance passes.

## 27. Central rule

A durable live-shadow trace records what the accepted deterministic cognitive
pipeline would have done on an exact real turn. It observes; it does not speak,
act, remember, reward, learn, or silently become truth.

## 28. Addendum: requirement resolution in live shadow (2026-09-30)

Normative text: [`REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md`](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md) §9. The compiler derives `requirement_inputs` only from
the capture: turn time is `captured_at`, no user zone is captured (so the date
is stated in UTC), and the user ref comes from the capture. Cross-record validation checks these
inputs and the dialogue refs against the capture. A resolution failure is a
typed `pipeline.requirement_resolution` failure. The capture schema is
unchanged. The runner version bump starts a new lineage segment, and earlier traces
replay as `version_unavailable`. Nothing is added to prompts, memory, or
responses.
