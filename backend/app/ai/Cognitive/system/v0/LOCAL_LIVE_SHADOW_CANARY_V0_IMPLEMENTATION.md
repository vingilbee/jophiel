# Local Live-Shadow Canary V0 implementation

## 1. Status and purpose

This document is the implementation handoff for enabling and verifying the
already implemented Cognitive V0 live-shadow mechanism on a developer's local
machine.

The reviewed local retention period is:

```text
14 days
```

This approval is limited to local development data. It does not authorize
production sampling, production retention, use as training data, research
export, or any influence on the live assistant response.

The goal is to collect real local conversational traces for inspection while
the canonical cognitive pipeline remains observational. The live response must
remain exactly the response produced by the existing chat path.

## 2. Do not rebuild the trace system

The following mechanisms already exist and must be reused:

- immutable capture and terminal trace persistence;
- deterministic per-turn sampling;
- a separate cognitive-shadow worker thread or isolated worker process;
- `app.services.cognitive_shadow.purge_expired()`;
- hourly retention maintenance in
  `app.workers.cognitive_shadow._maintenance()`;
- cascade deletion from `CognitiveShadowJob` to `CognitiveShadowTrace`;
- account and conversation deletion cascades;
- typed completed, failed, and skipped terminal records;
- queue, failure, latency, payload-size, and retention-purge metrics;
- personality projection through Personality Runtime Mapper V0; and
- PostgreSQL retention and deletion tests.

Do not create a second trace table, duplicate queue, second long-running
retention worker, alternate mapper, or local-only cognitive pipeline.

## 3. Safety boundary

Local live shadow means:

```text
real local turn
    -> normal live response path (authoritative)
    -> immutable shadow capture (observational)
    -> separate shadow worker
    -> persisted cognitive trace for local inspection
```

It never means:

```text
shadow Action result -> response wording, tool execution, memory write,
authorization decision, or chat-job outcome
```

The implementation must preserve these invariants:

- Shadow failure cannot fail, delay, replace, or modify the live answer.
- Shadow output is not inserted into prompts.
- Shadow output is not published as a chat event.
- Shadow output cannot execute a tool.
- Shadow output cannot write WorkingMemory, LearningMemory, SemanticMemory, or
  EpisodicMemory.
- No trace inspection API or frontend exposure is added in this task.
- Logs contain identifiers and stable codes, not conversation or trace
  payloads.
- The default repository configuration remains disabled and fail-closed.

## 4. Reviewed local configuration

Use this local-only profile:

```env
COGNITIVE_LIVE_SHADOW_ENABLED=true
COGNITIVE_LIVE_SHADOW_SAMPLE_RATE=1.0
COGNITIVE_LIVE_SHADOW_SAMPLING_POLICY_VERSION=shadow-sampling-v0.1
COGNITIVE_LIVE_SHADOW_RETENTION_DAYS=14
COGNITIVE_LIVE_SHADOW_PERSONALITY_SOURCE=runtime_mapper
COGNITIVE_LIVE_SHADOW_LANGUAGE_TAG=en
COGNITIVE_LIVE_SHADOW_CORE_MEMORY_NAME=columbina
COGNITIVE_LIVE_SHADOW_RUN_IN_SHARED_WORKER=true
```

`SAMPLE_RATE=1.0` is acceptable here only because the scope is a deliberate
local test. Do not copy that value into staging or production configuration.

Keep the following code defaults unchanged:

```text
cognitive_live_shadow_enabled = false
cognitive_live_shadow_sample_rate = 0.0
cognitive_live_shadow_retention_days = None
```

Those defaults ensure that a fresh deployment neither captures data nor
silently invents a retention decision.

Place the active values in the developer's uncommitted local environment file.
Do not commit an enabled `.env`. The committed `.env.example` may document the
reviewed local example, but it must remain commented out and must say that 14
days is approved only for the local canary.

## 5. Worker ownership

No new long-running worker is required.

For local development, the normal shared worker process is the owner:

```text
python -m app.workers
```

When `COGNITIVE_LIVE_SHADOW_ENABLED=true` and
`COGNITIVE_LIVE_SHADOW_RUN_IN_SHARED_WORKER=true`, the runner starts a separate
`cognitive-shadow` thread with its own queue. It does not execute shadow
cognition on the chat thread.

The shadow worker calls maintenance at a bounded cadence. Retention maintenance
uses the configured 14-day value and, at most once per hour while the worker is
running, deletes capture jobs whose `captured_at` is older than the cutoff.
Their terminal traces are deleted by foreign-key cascade.

The first worker maintenance pass after process startup is eligible to purge,
so an expired local record is cleaned when the enabled worker next starts.

### Disabled-worker limitation

The current shared runner does not start the cognitive-shadow thread while the
feature is disabled. Consequently, automatic retention is enforced while the
local shadow worker is running and again when it is next enabled; it cannot run
while the laptop or worker process is stopped.

Document this honestly. Do not claim wall-clock deletion at the exact 14-day
instant. For the local canary, the policy is:

```text
Delete records older than 14 days on the next hourly maintenance pass of the
running local shadow worker, including the first eligible pass after restart.
```

Before sharing, archiving, or copying the local database, expired shadow data
must be purged and all remaining shadow contents must be treated as sensitive
conversation data.

If a manual purge entry point is added, it must be a thin invocation of the
existing `purge_expired()` service with the configured retention value. It must
not use ad-hoc SQL, accept zero/negative retention, bypass configuration, or
become a second daemon. A manual command is optional for this local V0; the
existing hourly worker maintenance is the automatic owner.

## 6. Implementation tasks

### 6.1 Configuration documentation

Update `backend/.env.example` so it:

- gives `14` as the local-canary example;
- keeps every live-shadow setting commented out;
- labels `1.0` sampling as local-only;
- warns that production requires a separate rollout decision; and
- retains `runtime_mapper` as the normal personality source.

Do not change the safe defaults in `app/core/config.py`.

### 6.2 Canonical status documentation

Update the canonical Cognitive README to record:

- the reviewed local retention value is 14 days;
- this removes the retention-decision blocker only for local testing;
- production rollout remains unapproved;
- cleanup is hourly while the enabled worker runs and after the next eligible
  restart;
- account and conversation deletion remain immediate through cascades; and
- staging/canary privacy, latency, failure-rate, and storage measurements are
  still required before any broader rollout.

Do not mark the cognitive pipeline live-integrated. Shadow capture observes a
live turn, but its result still has no authority over the response.

### 6.3 Maintenance regression coverage

The service-level expiration test already proves that old jobs and their traces
are deleted. Add worker-level coverage if it does not already exist that proves:

1. maintenance uses `cognitive_live_shadow_retention_days`;
2. a record newer than 14 days survives;
3. a record older than 14 days is deleted;
4. deleting a job cascades to its terminal trace;
5. a surviving trace whose predecessor was purged has only its nullable
   `predecessor_trace_id` cleared;
6. maintenance does not run more than once inside the one-hour cadence;
7. a later eligible maintenance call runs again;
8. the purge metric records only the number actually deleted; and
9. no trace payload or message content is logged.

Prefer injected/frozen time or monkeypatched clocks. Do not sleep for an hour
and do not make tests depend on the machine's current date.

### 6.4 Local activation runbook

Document the exact local sequence:

1. Start local PostgreSQL and apply migrations.
2. Confirm the seeded active `columbina` CoreMemory exists.
3. Add the reviewed settings to the uncommitted local environment.
4. Start the backend API.
5. Start the normal worker process so its cognitive-shadow thread is running.
6. Send a small set of deliberate local conversations.
7. Confirm each sampled turn creates one capture job and exactly one terminal
   record: completed, failed, or skipped.
8. Confirm the live response is unchanged when shadow capture succeeds,
   fails, or is disabled.
9. Inspect only through a local database session or an explicitly test-only
   script; do not add a public endpoint.
10. Stop the local canary by setting `COGNITIVE_LIVE_SHADOW_ENABLED=false` and
    `COGNITIVE_LIVE_SHADOW_SAMPLE_RATE=0.0`.

Redis wakeups are optional locally if the existing PostgreSQL polling fallback
is operating. PostgreSQL remains the source of truth.

## 7. Local verification queries

Use read-only queries for manual verification. Do not print payload columns in
ordinary logs or paste them into shared artifacts.

Counts and statuses are sufficient for the first check:

```sql
SELECT status, count(*)
FROM cognitive_shadow_jobs
GROUP BY status
ORDER BY status;

SELECT terminal_status, count(*)
FROM cognitive_shadow_traces
GROUP BY terminal_status
ORDER BY terminal_status;

SELECT min(captured_at) AS oldest_capture,
       max(captured_at) AS newest_capture,
       count(*) AS captures
FROM cognitive_shadow_jobs;
```

To evaluate retention without waiting 14 days, use automated tests with frozen
time or test-owned backdated rows. Do not backdate genuine conversational rows
and do not lower the approved policy merely to make manual testing faster.

## 8. Required tests

Run at minimum:

```text
tests/test_cognitive_shadow_persistence.py
tests/test_cognitive_shadow_worker.py
tests/test_cognitive_shadow_chat_isolation.py
tests/test_personality_runtime_v0_shadow_integration.py
```

The focused database-backed run must include:

- capture refusal when retention is `None`;
- 14-day purge boundary behavior;
- conversation and account cascade deletion;
- predecessor clearing after expiration;
- worker maintenance cadence;
- completed/failed/skipped terminal persistence;
- capture and worker failure isolation from the live response; and
- runtime personality mapping at capture time.

Then run the complete backend suite with `TEST_DATABASE_URL` configured and
run `git diff --check`.

## 9. Acceptance criteria

The local live-shadow canary mechanism is ready only when:

1. the repository defaults remain disabled, unsampled, and retention-unset;
2. the uncommitted local profile explicitly sets 14 days;
3. capture still refuses to run with no retention value;
4. the existing shadow worker, not a duplicate daemon, owns automatic purge;
5. records older than the cutoff are deleted with their traces;
6. newer records remain intact;
7. account and conversation deletion still remove shadow data immediately;
8. retention behavior is deterministic and tested without wall-clock sleeps;
9. trace content is absent from routine logs and public APIs;
10. live responses are invariant under shadow success, failure, and disablement;
11. focused and complete database-backed suites pass;
12. the canonical README records exact test counts and the local-only scope;
13. production sampling remains disabled and unapproved; and
14. `git diff --check` passes.

## 10. Explicit non-goals

Do not add any of the following in this task:

- production or staging enablement;
- a new long-running cleanup worker;
- a public trace browsing endpoint;
- frontend trace rendering;
- research export;
- learning-dataset conversion;
- trace-derived model training;
- personality calibration;
- language realization from selected shadow actions;
- tool execution from shadow plans;
- raw payload logging; or
- a change to the live assistant response path.

## 11. Handoff result

When implementation is complete, report:

- configuration and documentation files changed;
- whether worker-level retention coverage was added;
- focused pass/skip counts;
- complete backend pass/skip counts;
- confirmation that repository defaults remain off;
- confirmation that the active local value is 14 days without exposing other
  environment contents;
- confirmation that no public API or live-response dependency was added; and
- any remaining blocker before starting the local trace review.
