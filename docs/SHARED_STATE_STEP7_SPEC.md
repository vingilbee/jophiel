# Step 7: background coordination — implementation handoff

Planning specification, 2026-10-22. No runtime changes accompany this document.

## User clarification and scope

Observation means background processing that turns conversations and experiences into material for learning. It is not the name of the game sensor/change detector. Existing source-report capture may supply its inputs, but must not be confused with this intended processing role.

The user will revamp cognition separately. This step implements coordination, work scheduling and component interfaces without training cognition, adding a teacher, implementing personality decisions, or requiring the current student to support new events. Future learning itself is also outside scope. Preserve an explicit interface through which meaningful results can later reach cognition.

Goal: background mind work, memory management and observation processing share a reliable scheduling model and publish their state/results into the shared view. Slow work must not stop scheduling, recovery or cancellation.

## Simple flow

```text
new message / completed conversation exchange / experience / work result / due timer
    → durable event and relevant shared-state update
    → coordinator checks which work is due
    → independent executor performs work
    → durable result and updated component projection
    → optional cognition-input event, retained for the future cognition implementation
```

Tick means a cheap scheduling pass, not a model call or a broadcast requiring all components to run. Use a configurable idle heartbeat, initially about one second. Relevant events wake scheduling immediately; the heartbeat recovers missed notifications and handles due work.

## Component roles

| Component | Responsibility in this step |
|---|---|
| Coordinator | Ingest work/events, enforce dependencies and concurrency limits, dispatch due jobs, process results, deadlines, retries and cancellation |
| Mind executor | Run already-authorized background thinking using existing thinking code; publish private results |
| Memory executor/manager | Run memory-owned ingestion, organization, maintenance and retrieval through existing APIs; publish changed references/versions |
| Observation executor | Process eligible conversations/experiences into learning-preparation records through a defined adapter; publish processing status and output references |
| Shared view | Expose owned projections and work status with scope, version and freshness |
| Cognition interface | Accept typed meaningful events for later interpretation; no behavioral implementation in this step |

The intended observation processing may exceed what `services/observation.py` currently implements. Inventory existing capture, dialogue, experience, reflection and memory processing before assigning responsibility. Reuse working stages where semantics match. If learning-preparation transformation is absent, define its input/output boundary and report the capability as unavailable; do not substitute game telemetry or invent a new learning algorithm and call it complete.

## Required changes

### 1. Separate the scheduler from slow work

Currently coordinator `run_once` can run recall/thinking synchronously. Refactor this so expensive calls execute outside the scheduling pass. Use existing durable jobs/leases and suitable existing worker infrastructure where possible. Do not create a second coordinator that competes with the old one.

Bound executor concurrency and queue sizes. Serialize conflicting work by component/user/activity as appropriate; unrelated users and components should not wait behind one slow thought. Do not hold a DB transaction or row lock during a model/network call. Long work needs lease renewal and ownership fencing; expired ownership must not permit duplicate result acceptance.

### 2. Add component scheduling rules

Each adapter declares supported event types, scope, relevant input versions, due cadence if any, concurrency key, timeout/retry policy, and result type.

Examples:

- Mind: an existing thinking request, or an explicitly configured reflection deadline. Do not invent personality-driven reflection decisions.
- Memory: eligible completed exchange/experience, correction/invalidation, or existing maintenance deadline.
- Observation: a completed eligible exchange or experience, an explicit batch/coverage boundary, or retry/recovery deadline. Avoid reprocessing unchanged history each tick.

Reuse existing timer semantics and feature switches. The heartbeat checks whether work is due; each component retains its own rate. Incremental jobs carry source IDs/versions or coverage ranges to avoid duplicate processing.

### 3. Publish scoped state/results

Use the existing shared-state contracts where possible. Add component status (`idle`, `queued`, `running`, `blocked`, `failed`, `disabled`), last processed input/version, pending work references, last result references and next due time where applicable.

Authoritative records stay with their existing owner. Shared state contains bounded projections and references, not full historical conversations or copied memory stores. Represent unavailable/stale data explicitly.

Events/results carry stable ID, user scope, conversation/activity where applicable, source refs, input versions, causation/correlation IDs, creation time, and payload schema version. Work carries deadline, lease, retry state, deduplication identity and cancellation generation.

Memory corrections publish invalidation/version changes. Pending work based on changed evidence must be revalidated before accepting dependent effects. An unrelated state change should not invalidate all work.

### 4. Decouple observation/memory maintenance from mind

Move relevant scheduling out of the old mind loop. Mind disabled must not disable observation processing/recovery or memory maintenance. Preserve each component's own enable/disable policy.

Switch each responsibility to its replacement scheduler atomically/configurationally; remove the old trigger once its replacement is active. Do not run both triggers and rely solely on deduplication to hide duplicate scheduling.

Keep hands' local execution cadence unchanged. This step does not relocate Gmail/document tools or rebuild game execution.

### 5. Keep cognition replaceable

Define a typed result/event sink for the future cognition implementation. Relevant examples: completed private thought, completed observation/learning-preparation record, significant memory result. Mechanical maintenance receipts do not all need cognitive evaluation.

Use scripted sinks in tests. With real cognition unavailable, record the pending/blocked handoff explicitly and retain the source result. Do not call a teacher, turn results into fake user messages, send speech directly, or claim personality behavior is implemented.

For later cognition replay, retain deduplication and freshness metadata; do not automatically replay every historical blocked event into speech when cognition becomes available. Re-evaluate relevance first.

Existing step-4/5 speech boundaries remain intact. This step neither bypasses them nor requires solving their known cognition capability gaps.

## Observation and future learning boundary

Observation input is eligible conversation/experience evidence with provenance and delivery status. Distinguish what the user actually saw/heard from generated-but-cancelled text. Observation outputs are derived learning-preparation records, never silently promoted to factual memories or model updates.

Keep source evidence, derived interpretation, processing version and coverage separate. If the actual transformation is not implemented, adapter contract and scheduling can be built/tested with scripted outputs, while production processing remains explicitly unavailable. Report that limitation in the completion notes.

## Reliability requirements

- Idle heartbeat continues during slow executor work.
- Durable events/work are the recovery source; Redis notifications are an acceleration, not the only record.
- Multiple scheduler processes cannot dispatch conflicting duplicate work.
- Retries use stable identities; accepted work/results and external delivery remain separately recorded.
- New user input cancels/supersedes conversation-dependent work according to existing policy. Independent memory/observation processing of historical facts may continue; do not cancel it merely because a newer message arrived.
- Shutdown stops claims and drains or safely relinquishes work. Restart recovers work and pending results.
- Prioritize cancellation, deadlines and ready results so bulk maintenance cannot starve interactive work.
- Bound retention and event backlog. Coalesce replaceable projections; do not discard source events or delivery receipts required by memory/observation consumers.

## Implementation sequence

1. Inventory current scheduler/executor calls and observation capabilities. List which existing workers are reused and which duplicate triggers will be removed.
2. Extract expensive coordinator execution into bounded executors using durable claims and result acceptance.
3. Add event wake-ups, heartbeat scheduling and scoped eligibility adapters.
4. Migrate memory and observation scheduling independently from mind, preserving existing owner APIs and concurrent work.
5. Project status/results into shared state and wire the cognition sink with an explicit unavailable implementation.
6. Verify behavior with scripted slow workers, results and cognition. Document actual implemented processing versus adapter-only capabilities.

## Acceptance tests

- Hold mind work open: scheduler still processes deadlines, cancellation, another user's ready recall, and observation/memory jobs within configured limits.
- Run many unchanged ticks: no repeated model processing or re-observation of identical source versions.
- Publish an event during idle wait: processing wakes without waiting for the heartbeat interval.
- Disable mind: enabled memory and observation jobs still run.
- Retry/restart/multiple schedulers: one accepted result per idempotency identity; ownership fencing prevents late duplicate acceptance.
- Correct memory during dependent work: stale evidence cannot silently authorize the dependent effect.
- Receive a new user message: obsolete conversational output is fenced while valid historical maintenance can finish.
- Observation covers a completed exchange once; partial/cancelled delivery remains distinguishable.
- Cognition unavailable: private results and handoffs remain traceable, with no direct speech fallback.
- Existing steps 3–5 and hands regression tests pass; no duplicate old/new scheduler triggers.

## Completion definition

Complete means background scheduling remains responsive, component work is scoped/durable/independent, existing memory and observation responsibilities have one scheduler owner, and the result-to-cognition interface exists. It does not mean cognition is intelligent enough, personality behavior is validated, an absent observation transformation was invented, or the future learning system exists.

Report changed triggers, executor limits, cadence/timeout defaults, recovery behavior, test evidence, existing processing reused, unavailable capabilities and remaining migration switches. No real cognition evaluation is required to verify this infrastructure.
