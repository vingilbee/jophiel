# Columbina memory — session 2: observation

Status: proposed implementation specification, following review of the supplied observation proposal. No runtime changes are implemented by this document.

Companion design: `COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md`.

Baseline reviewed: `9195563` on `feature/general-memory`, in the separate `app-cognitive` checkout. This document is saved in the current chat's `app/docs` workspace; it must be brought into the implementation branch when work starts.

## 1. Outcome

Meaningful observations from conversation and actions become available to general working memory without a manual memory-tool call. Capture is durable, retries do not duplicate memories, context does not change while an observation waits for processing, and background ticks continue without a game or chat connection.

Observation records what a source reported. It does not decide whether a user statement is universally true, invent an explanation for a failed action, or independently create lessons.

## 2. Review of the supplied proposal

Keep the shared observation stream, typed source adapters, separate capture flag, evidence links, code-based promotion, and reuse of the existing mind worker.

Correct these details before implementation:

1. A BIGSERIAL ID is not commit order. Transaction A can allocate 10, transaction B commit 11, and a worker advance past 11 before A commits. A simple `id > cursor` consumer would permanently miss 10. Use durable per-observation processing state for this session, not an ID high-water mark as the correctness mechanism.
2. An append-only stream cannot also update `repeat_count` and `last_seen_at` in existing observations. Aggregate noisy samples before emission, or use a separate mutable source-state projection. Published observations stay immutable except for explicit retention/privacy deletion.
3. Delivery information is not absent from the application. `MessageDelivery` and `ChatMessage.heard_chars` already exist. Adapt their authoritative updates rather than introducing a second definition of delivered/heard.
4. A subagent returning text is not proof that an external action succeeded. Capture actual tool results when available; label delegation completion separately.
5. Savepoint-based failure isolation alone does not make capture durable: if an observation fails but its source commits, the event may be lost. Provide reconciliation from durable sources or an outbox/retry mechanism.
6. Do not move game-learning reflection to the new stream in this session. Mirror selected events and verify parity first.
7. Do not duplicate exact message bodies or external document contents into observation summaries and diagnostic logs by default. Preserve references and structured facts needed by the consumer.

Some lines in the pasted proposal were truncated. The decisions below are explicit proposals rather than reconstructions of missing text.

## 3. Scope

### Required first vertical slice

- Conversation adapter: user-message references and authoritative assistant delivery/interruption updates.
- One action adapter: existing Minecraft skill outcomes and selected notices, using existing durable game experiences as sources.
- General observation storage and registry; no game-specific lifecycle rules in the storage service.
- Per-source identity, freshness, availability, and durable ingestion progress where needed.
- Memory-consumer processing with bounded batches and atomic promotion.
- General runtime inspection: source health, pending work, processing lag, last tick, and skip/apply/failure reasons.
- A no-game conversation case that exercises the complete path.

### Follow-on adapters within or after this milestone

Add direct tool results only after one concrete tool has a reliable invocation/result hook. Screen observation, Gmail, WhatsApp, desktop transitions, and additional channels can use the same contract, but are not all required for the first slice. Do not describe the milestone as covering every tool if only delegation wrappers are instrumented.

### Deferred

- Learning reflection, automatic skill changes, model training.
- Consolidation into episodes and cross-memory correction propagation.
- Automatic topic inference and world identity discovery.
- Migrating or deleting `game_experiences`.
- Generalized scheduling for all future components.
- Raw screen frames, sensor samples, and every mind tick.

## 4. Data ownership

| Record | Owner and purpose |
| --- | --- |
| Existing messages, deliveries, game experiences | Authoritative source records |
| Observations | Immutable, typed reports of source events with stable identity and context |
| Observation source state | Mutable health, freshness, retry state, and adapter progress |
| Observation processing | Per-consumer delivery status and durable retries |
| Memory events | Selected observations rendered as useful, evidence-linked recent memories |
| Memory manager state | Existing focus and maintenance state; retain it rather than moving fields prematurely |

Keep the original source record authoritative. The observation is its normalized report and common interface. Memory-event promotion does not convert an uncertain report into an established external fact.

## 5. Proposed schema contracts

These are implementation targets. Migration names and exact SQL should be reviewed alongside the existing models before coding.

### `observations`

| Field | Proposed contract |
| --- | --- |
| `id` | BIGSERIAL primary key; stable identity, not a commit-order guarantee |
| `user_id` | Required UUID, user FK with account-deletion cascade |
| `context_id` | Nullable UUID; composite FK with user ownership; referenced contexts cannot be routinely deleted |
| `source` | Required VARCHAR(64), registered adapter name |
| `source_instance` | Required VARCHAR(160), stable conversation/session/source identity |
| `source_event_key` | Required VARCHAR(255); adapter-assigned identity including revision when appropriate |
| `kind` | Required VARCHAR(64), validated by the registry |
| `schema_version` | Required integer, default 1, positive |
| `actor` | Required enum-like string: user, columbina, environment, tool, system |
| `epistemic` | Required: observed or reported; no inferred external claims |
| `content_origin` | Required: user, external, system, or model; identifies provenance, not instruction authority |
| `occurred_at` | Nullable timestamptz when the source cannot establish event time |
| `received_at` | Required timestamptz, server default now |
| `summary` | Required bounded text, 1–500 characters; source-specific safe renderer, not unrestricted model output |
| `payload` | Required JSONB, default `{}`; typed and versioned by registry; proposed 16 KiB application limit |
| `salience` | Required: routine, notable, critical; source-defined default, not a confidence score |
| `caused_by_id` | Optional same-user observation FK; descriptive provenance, not proof of causation |
| `outcome_of_id` | Optional same-user observation FK pointing to a known action attempt; no invented attribution |
| Source references | Nullable typed FKs for message, delivery, experience, session event, and thought as adapters require |

Unique key: `(user_id, source, source_instance, source_event_key)`. Retrying the same report is a no-op. Reusing a key with different content produces an inspectable conflict; it must not silently overwrite an observation.

Indexes: `(user_id, received_at DESC, id DESC)`, `(context_id, received_at DESC, id DESC)`, and source-reference indexes needed for reconciliation and deletion. Composite unique `(id, user_id)` supports ownership-checked relationships.

Source adapters validate ownership of all source references. A delivery may reference both a delivery record and its message; do not impose an inappropriate exactly-one-source rule on observations. The existing memory-evidence table still requires exactly one evidence source per link.

Self-referential observation links must not cascade deletion through later observations. Remove only the link when its source observation expires. Context and account deletion paths need explicit integration tests.

### `observation_processing`

Use one row per `(observation_id, consumer)`; initially `consumer = memory` only.

- Required status: pending, retry, applied, skipped, failed; default pending.
- Required attempt count, default zero; nonnegative.
- Required `next_attempt_at`, default now.
- Optional processed timestamp and compact safe error code/reason.
- Optional promotion-rule version when processed.
- Observation deletion cascades processing rows.
- Index pending/retry work by `(consumer, next_attempt_at, observation_id)`.

For code-only promotion, claim work using a row lock with `SKIP LOCKED`, perform promotion, update status, and commit in one short transaction. A crash rolls back both the memory write and processing completion. No long-running lease or model call is needed inside this transaction.

A future model-backed consumer needs its own lease/revalidation policy. Do not hold database locks across model calls.

### `observation_sources`

Primary identity: `(user_id, source, source_instance)`.

Maintain enabled state, explicit availability (unknown, online, offline), last received time, last meaningful-change time, next check time, source-specific durable ingestion position, and last decision/error. Define what freshness means per adapter.

Silence in conversation does not mean the user disconnected. A quiet game sensor can be healthy. Keep freshness, availability, and meaningful change separate.

### Existing tables

- Add `observation_id` to `memory_evidence`, extend its exactly-one-source constraint, and index it. Deleting an observation deletes only its evidence links, not working items or memory events.
- Keep `memory_manager_state`; do not repurpose `last_event_id` as an observation cursor. It currently identifies memory-event progress, not observation delivery.
- Use existing memory-event deduplication with a stable key such as `observation:<id>:<rule-name>`. Evidence and event creation commit atomically with processing status.
- Do not add `context_id` to all long-term stores as part of this milestone.

## 6. Capture and ordering

Prefer observation insertion plus its processing row in the transaction that persists the source. Preserve the caller's transaction boundary; the capture helper must not commit it.

If capture is isolated by a savepoint so the user operation can succeed independently, reconciliation is mandatory for supported durable sources. Record a safe diagnostic and retry from the source later. A consumed source marker advances only when capture is known to have succeeded or to be a genuine duplicate.

Reconciliation must itself avoid ID/commit-order gaps. Source-specific transaction-serialized sequences can be used where their guarantee is verified; otherwise use pending source/outbox rows or an anti-join scan of committed source records. A finite lookback window alone cannot guarantee recovery from arbitrarily late commits.

For external actions, the database and remote service cannot share a transaction. Record a durable invocation identity before dispatch where practical, then persist the actual result. On timeout, the outcome is unknown unless evidence establishes failure. Recovery must not resend a potentially completed external action merely to reconstruct an observation.

Ordering has three meanings:

- Source order: message position, delivery revision, or action sequence when available.
- Event time: when the reported event happened, possibly unknown or delayed.
- Processing order: the order committed observations are consumed.

Do not claim these are interchangeable. Memory promotion must tolerate delayed observations and label their age. Processing all committed pending rows avoids lost late commits even if their numeric IDs are lower than previously processed IDs.

## 7. Context binding

Resolve context at capture from explicit source binding when available. An action result inherits the context of its invocation/session at the time of the attempt, not whichever context is focused when the result arrives.

A user message can arrive before the reply model recognizes a context switch. Record known context or leave it unresolved; use an explicit context-binding operation/projection after interpretation. Do not mutate immutable observation content or silently assign the worker's current focus. Reconciliation needs persisted binding information; a live lookup of current focus is not enough for historical messages.

An observation with unresolved context may be stored, but context-dependent promotion waits for binding. Do not route an unresolved game result into General just to move the queue forward. Expose deferred binding in inspection.

## 8. Promotion rules

The registry maps `(source, kind, schema_version)` to a payload validator, renderer, default salience, and code-based promotion policy. The registry can grow without a database migration for every event kind.

| Observation | Initial memory treatment |
| --- | --- |
| User message recorded | Retain reference; do not duplicate every message into the memory prompt |
| Reply delivered/interrupted | Preserve authoritative exposure state; optionally render a concise interruption event, not unheard text |
| Skill attempt failed | Promote a concise failure with actual reason and source; do not infer its cause |
| Death notice | Promote death; assert lost items only when separately supported by outcome/state evidence |
| Ordinary successful repeated action | Usually skip; retain normalized source reference for later consumers |
| Source explicitly offline/recovered | Promote only meaningful transitions; no repeated identical warnings |
| Model thought | If supported later, record that the model produced a thought; do not promote its content as observed reality |

Salience is an input to promotion, not a universal command to store everything permanently. Promotions preserve epistemic status. If the current memory view strips relevant provenance or trust labels, extend the renderer before admitting free-form external content.

Source text is data. A tool can be authentic while its result contains untrusted instructions. Do not use a trusted/untrusted flag to make external text authoritative in system prompts. Code-rendered summaries should avoid unnecessary verbatim external material; retain origin labels and inspectable source references.

## 9. Repeats and coalescing

Transport retry: identical event key, no new observation.

Repeated independent attempt: distinct event key, separate observation, even if the error text matches. Learning may need the independent outcomes later.

Unchanged sensor state: update source freshness/projection without appending another semantic event. If aggregation is useful, publish an immutable window report containing count, start, and end once the window closes. Any later report is a new observation; a consumer never needs to rediscover a silently modified old row.

## 10. Ticks, wakeups, and cognition

Reuse the existing worker loop. Separate capture from consumption flags:

- `OBSERVATION_ENABLED`: adapters may record and reconcile observations.
- `MEMORY_MANAGER_ENABLED`: memory promotion and existing working-memory maintenance may run.
- Existing mind lifecycle controls govern worker scheduling for this milestone; document this dependency clearly.

Capture can run while memory promotion is disabled. Enabling promotion later processes the retained backlog once. Do not create rows for future learning consumers until their backlog/retention contract is defined.

New observations request an early check through durable due state; optional wake signals are only an optimization. Each tick examines pending work, due source checks, current load, and bounded processing capacity. Record skip, defer, apply, or failure with reasons.

Use configurable batch/time budgets. A broken report gets bounded retries and visible failure, without blocking all other observations for the user. Successful application means effectively-once memory effects under retries, not a claim of globally exactly-once external execution.

Live replies continue to receive the latest user message and immediate action results through their existing paths. Background promotion must not delay them. For critical pending observations, add a bounded pending-event view or a small deterministic pre-reply drain, with explicit deduplication against already delivered history/results. Select and test one approach before implementation; otherwise the design has not met its live-freshness requirement.

A memory event appearing does not automatically trigger speech or action. This session exposes observations and pending state to cognition; broader decisions to reflect, learn, or proactively speak remain separate gates.

## 11. Delivery and corrections

Message creation, reply generation, delivery, and acknowledgement are different events. Capture delivery transitions at the authoritative update sites. Voice interruption reports may arrive later; append a revision/event and derive the current exposure state using existing delivery rules.

Do not record the full generated reply as spoken when only a prefix was heard. Retried delivery acknowledgements must be idempotent. One user message may have several delivery-state observations for the corresponding reply; message identity alone is not a sufficient deduplication key for those transitions.

Record a correction as the user's actual message. Semantic recognition remains with the existing reply/cognition path for now. This milestone does not fix durable fact retraction by itself, and should not be described as doing so.

## 12. Retention, deletion, and reset

Proposed default: routine observations become eligible for deletion after 14 days, only after all enabled consumers have applied/skipped them and no active review requires the evidence. Pending, retrying, or failed work must not silently disappear under retention.

Notable/critical observations remain until an explicit supported retention decision; consolidation is not implemented yet. Report growth rather than pretending an absent consolidation process will eventually clean them up.

Message and delivery pointer observations follow deletion of their authoritative source. Avoid copying message bodies into payload/summary, since cascading the pointer alone would leave duplicated content behind.

Observation deletion removes evidence links. Goals and commitments remain self-contained, as in session one. Privacy deletion and memory reset are stronger operations: derived event text containing the deleted content must be purged/redacted according to the requested scope, not left behind simply because its evidence FK was removed.

Add an explicit observation-reset scope for stream, processing rows, and source state. Working-memory reset should not unexpectedly replay every old observation into newly cleared memory: preserve processing receipts when retaining observations, or explicitly mark an eligible boundary as skipped. Define and test the chosen reset combination. Account deletion removes all user-owned rows.

## 13. Implementation order

1. Finalize schema details, live pending-event handling, and context binding against actual source hooks.
2. Add migration, registry, capture, processing state, source health, and observation evidence links.
3. Integrate conversation and authoritative delivery hooks, including recovery from missed capture.
4. Integrate selected game outcomes using stable existing experience identities; keep game learning untouched.
5. Connect bounded promotion and freshness checks to the existing loop; add inspection and reset behavior.
6. Run isolated PostgreSQL concurrency tests, upgrade/downgrade tests, and real worker tests with no game/socket.
7. Add further adapters only after the first vertical slice passes.

## 14. Acceptance tests

1. A meaningful action outcome creates one observation and one promoted memory event with evidence and the correct context.
2. Retrying capture and promotion does not duplicate either record.
3. Transaction A allocates a lower observation ID but commits after B is processed; A is still processed later.
4. Crash before commit leaves promotion pending; crash after commit does not duplicate it.
5. Source capture fails inside a savepoint, source commits, and reconciliation recovers it exactly once.
6. An action started in context A completes after focus switches to B; its outcome remains in A.
7. A user context-switch message captured before interpretation receives a correct explicit binding rather than the worker's current focus.
8. Generated but unheard reply text does not become a remembered delivered statement; delayed interruption updates the exposure projection.
9. Unchanged samples update freshness without producing duplicate memory events; two genuine failed attempts remain distinct.
10. External text resembling instructions remains labeled data and cannot become an instruction through promotion.
11. A no-game worker tick consumes a pending conversation/delivery observation; an unchanged tick skips without a model call.
12. An offline source remains explicitly stale/unknown; silence in chat does not infer absence or disengagement.
13. A failed row cannot block unrelated observations indefinitely; inspection exposes retries and failures.
14. User isolation, nullable source deletion, causal-link deletion, reset combinations, and account deletion preserve their contracts.
15. Routine retention does not delete pending work; working-memory reset does not unexpectedly resurrect cleared events.
16. Capture enabled with promotion disabled retains work; enabling promotion processes it once.
17. Existing memory tests and learning tests continue to pass without switching game reflection to observations.

## 15. Decisions and remaining implementation choices

Accepted direction for the draft: common stream with source pointers; separate capture flag; source freshness included; game-learning migration deferred; 14-day routine retention as configurable eligibility, not unconditional deletion; immutable published observations; durable per-row consumption rather than naive ID cursors.

Before implementation, resolve the bounded pending-event view versus pre-reply drain, exact source hooks and their recovery mechanisms, context-binding persistence, schema validation and deletion constraints, and worker batch/time budgets. These are focused engineering choices within this scope, not a request to redesign memory or learning.
