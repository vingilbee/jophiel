# Step 7 implementation

This is a scheduling and interface refactor. It does not improve cognition decisions, recall accuracy,
personality, or learning, and adds no teacher/model calls.

## Ownership and trigger migration

- `services/coordinator.run_once` is now a cheap scheduling pass. `coordinator_runtime.Runtime` dispatches
  existing recall and hard-thinking functions to bounded executor lanes. The synchronous `execute_once`
  function is only a single-job test/script harness; the production runner never uses it.
- The old observation and memory-maintenance calls have been removed from `mind.run_once`.
- The observation worker now owns source capture, recovery and pruning. Its production tick does not run
  the memory consumer. It runs with `OBSERVATION_ENABLED`, independently of mind and the shared-state pipeline.
- Coordinator memory jobs own per-user observation consumption and due checks through the existing
  `observation_memory.process` and `memory_manager._tick_user` APIs. `MEMORY_MANAGER_ENABLED` controls them.
- Existing long-term memory ingestion/corrections, dialogue-memory, embeddings and game-reflection workers
  keep their own durable jobs and policies. They are not duplicated by the new scheduler. Existing source
  mind rhythms and hands' local execution cadence are unchanged.
- `OBSERVATION_PREPARATION_ENABLED` controls the new preparation adapter separately. Capture creates a durable
  receipt for eligible new reply, interruption, delivery-status and captured experience reports. Enabling
  preparation does not silently import historical conversations. Previously queued receipts remain recoverable.

## Processing actually available

Source capture normalizes provenance and delivery reports. The existing memory consumer binds contexts and
promotes eligible source reports under its existing rules. Memory maintenance lapses intentions, flags due
commitments and pauses idle contexts. Long-term extraction and game reflection already exist, but their outputs
are memory records and game lessons, respectively; neither is substituted for general learning preparation.

There is **no production conversation/experience-to-learning-preparation transformer**. `PreparationInput`
contains scoped source references, versions and delivery status/extent; `PreparationResult` contains derived
output references, processing version and coverage. The default adapter returns unavailable, producing a
traceable blocked work item. Scripted test adapters cover the successful path without writing memories or
training a model. Replacement adapters must be idempotent by event ID and bound their external calls.

The new `CognitionEvent`/`CognitionSink` interface accepts private-thought, preparation and memory-change events.
The default sink records blocked/unavailable. It cannot authorize speech; existing step-4/5 decisions and the
chat mouth remain the speech boundary. Blocked handoffs are not automatically replayed when a provider changes.
A later replay mechanism must explicitly check relevance using retained provenance and versions.

## Defaults and reliability

| Setting/boundary | Default |
| --- | --- |
| Idle coordinator heartbeat | 1 second, configurable |
| Executor slots | 2 per lane per process: mind, recall, memory, observation, cognition, conclusion |
| Local waiting queue | No submissions beyond free tracked slots |
| Durable admitted pending work | 1,000 per component; observation blocked work counts toward admission |
| Cognition queue overflow | Recorded blocked with source refs; no lost accepted result or unbounded runnable queue |
| Work deadline | Existing `SHARED_STATE_WORK_DEADLINE_SECONDS`, 180 seconds |
| Lease | 120 seconds, renewed on scheduling passes, never beyond deadline |
| Attempts | 3, same identity, 2/4-second retry backoff by default |
| Hard-thinking network timeout | Existing 60 seconds, no SDK retries |
| Memory observation-consumption budget | Existing 0.5 seconds per pass |
| Memory due cadence | Existing due dates, maximum check gap 10 minutes |
| Source recovery sweep | Existing 60 seconds |
| Cognition handoff deadline | 1 day |
| Routine maintenance receipt retention | 30 days, prune up to 100 once per minute |

Source reports and blocked handoffs required by consumers are retained, not pruned to conceal backlog. Existing
source retention still governs fully consumed observations. Shared projections contain at most ten pending work
references per component and one terminal result reference; authoritative history remains with its owner.

Durable database rows are the recovery source. Post-commit local/Redis notifications wake scheduling; Redis
lists are coalesced to one hint, and polling handles lost notifications. Claims use row locks plus per-user,
per-component advisory locks. SQL eligibility excludes busy users before the scan limit, so their backlog
cannot hide another user's ready job. Multiple processes can execute unrelated work; limits scale with the
number of worker processes.

Only an unexpired lease owner with the matching cancellation generation can accept a result. Expiry progresses
while executors are busy. A reclaimed/stale executor cannot populate the authoritative result. Dependencies
carry evidence fingerprints plus correction/applicability state; acceptance, follow-up dispatch and final
reply persistence recheck them. Memory corrections publish scoped version changes and typed handoffs.

New input supersedes conversation-dependent mind/recall work, but historical memory/preparation jobs survive.
Changed delivery extent invalidates stale preparation output, and the separate interruption/delivery report
supplies the new version. No generated-but-cancelled reply is relabelled as heard evidence.

Shutdown stops claims, increments ownership generations and relinquishes unfinished jobs, then drains existing
executor threads. Threads are not forcibly killed; existing provider timeouts remain necessary. Restart recovers
leases and unconsumed source receipts. Results and cognition handoffs commit together; delivery receipts remain
separate and retain their existing recovery path.

## Shared view

`SharedView.components` projects each component's scope, queued/running/blocked/failed/disabled/idle status,
pending refs, last input versions/result, version/freshness, and memory's next due time. Expired leases appear
stale; failed projection reads appear unavailable. Mechanical maintenance receipts do not invoke cognition.

## Migration and validation

Apply Alembic revision `b3e7f9a1c5d2` before restarting application/worker processes. This widens coordinator
work kinds/statuses, permits user-scoped jobs without conversations, and adds retry eligibility. Switch scheduler
ownership by restarting all old worker processes together; mixed old/new workers would restore the removed
mind-loop triggers. There is no parallel legacy scheduler switch to enable. Downgrade requires archiving/removing
new component work first; it deliberately does not destroy those records automatically.

Validation uses an isolated PostgreSQL database and scripted workers/sinks, including slow work, competing claims,
lease renewal and reclamation, shutdown/restart, cancellation, source-version deduplication, delivery freshness,
queue backpressure, scoped projections, unavailable cognition, and migration upgrade/downgrade. The existing
shared-state, autonomous delivery, memory, observation, mind and hands regression suites are included.

Existing memory-ingestion and dialogue-memory jobs also have bounded, conversation-scoped owner projections;
this does not enqueue a second copy of their processing.

Verification completed: the broader PostgreSQL regression run passed **269 tests**; the subsequent queue-bound,
local-reclaim and affected coordination/correction run passed **55 tests**. The final owner-projection/shared-state run passed **51 tests**.
`git diff --check` and Python compilation also passed. No real cognition evaluation was used.
