# Step 7 worker-system E2E verification

October 22, 2026. Tested checkout `045aa5d` plus the existing uncommitted step-7 changes. Scope: scheduler, worker independence, ownership, progress, recovery and coordination. Component intelligence, recall quality and learning transformation quality are excluded.

## Result

**Passed within scope: 289 backend tests, 5 Chromium transport tests and 21 frontend audio unit tests; zero failures or skips in the final run.** Both new integrated worker-loop scenarios passed. No coordination failure was found after correcting the disposable test environment. Component capability/quality remains explicitly excluded.

## Integrated system exercise

Added `backend/tests/test_background_system_e2e.py`, with two complete worker-loop scenarios. These run the production `runner.run` supervisor, real chat/coordinator/observation loops, the real asynchronous Runtime and executor lanes, PostgreSQL claims/receipts, source capture, observation consumption, memory due checks and scoped progress projections.

Each scenario does the following:

1. Submit a synthetic user message through the durable chat service and let the chat worker produce the immediate reply.
2. Hold its mind executor open using a scripted barrier.
3. While that barrier is still held, enqueue another normal reply in another conversation for the **same user**, and create an overdue commitment through the real memory-manager API.
4. Require the second chat to finish, the commitment to change to `needs_review`, memory work to complete, four conversation-source observations to be captured, and preparation work to settle **before releasing mind**.
5. Inspect shared component projections while mind is still `running`, including the other components' last-result and pending-work references.
6. Release mind, require its result and typed cognition handoff to settle, then run another unchanged heartbeat and check that source observations/preparation work were not duplicated.
7. Stop and drain the real runner/executors cleanly.

In the final run, mind started at approximately 0.1 seconds; its immediate chat response completed at approximately 0.13 seconds. The independent second reply and memory/capture checks completed by approximately **5.3 seconds while mind remained held**. Mind was released only after those checks passed. The delay includes the ordinary chat polling cadence; these scripted measurements establish independence, not production model latency.

The two adapter conditions were:

| Condition | Result expected and observed |
|---|---|
| Scripted preparation and cognition return valid typed results | Preparation and handoffs complete; memory/chat progress independently. |
| Production preparation/cognition seams report unavailable | Work remains explicitly `blocked`, with source provenance; memory and chat still progress. |

Blocked/unavailable component capability is not counted as a coordination failure. No replacement intelligence or learning algorithm was introduced. The independent source-mind loop was disabled throughout (`MIND_ENABLED=false`), while conversation-authorized mind work still ran in its own executor lane.

## Additional coordination coverage

The regression selection exercises:

- Slow mind alongside another user's recall, memory and observation, including deadline expiry and supersession.
- Bounded per-lane capacity, user/component claim serialization, fairness behind busy-user backlogs and durable queue backpressure.
- Lease heartbeat/renewal, expired/reclaimed-owner rejection, bounded retry identity/backoff and shutdown/restart fencing.
- Historical maintenance surviving new conversation input, changed delivery extent invalidating stale preparation and memory corrections invalidating dependent evidence.
- Observation/preparation work and memory upkeep with the entire shared-state mind pipeline disabled.
- Typed result metadata, scoped/bounded status projections, durable blocked handoffs and no automatic replay merely because a new sink is installed.
- Existing memory-ingestion projections without duplicate scheduling; quick/hard thought and planner wiring; unchanged normal/autonomous target, retry, restart and voice-cancellation safeguards.
- Actual step-7 migration upgrade/downgrade against disposable schemas.

The five Chromium transport regressions cover normal/autonomous bubble separation, preserving the selected conversation, reconnect replay, duplicate announcement IDs and completion while offline. They use the real React application with synthetic transport frames and are separate from the worker-loop system exercise.

## Isolation, harness corrections and limits

The disposable PostgreSQL cluster on port 55436 was reused; both test/application database URLs in test processes point there. Every database fixture uses an independent schema. The initial cluster inherited America/Vancouver time from `initdb`; observation treats timestamp-without-zone message timestamps as UTC. That mismatch caused seven missing-capture assertions in the initial broad run. Restarting **only the disposable cluster** with `timezone=UTC` corrected the fixture contract. The first version of the new harness also required routing a late-imported memory-view DB session to the fixture schema; that test-session routing was corrected without changing production code.

The real memory maintenance and source-capture functions were not replaced. Model interpretation, Jev choices, mouth text and the intentionally slow mind call were scripted. Preparation/cognition were tested through explicit success/unavailable seams. Unrelated legacy extraction/file-deletion loops were idle stand-ins in the integrated fixture; their content processing is not claimed as part of that system run.

There were no real model calls, application accounts or user data. No product source or configuration was edited; the only added executable is the new test harness. Existing step-7 edits were preserved. Existing application processes were not started, restarted or migrated.

The full migration to **b3e7f9a1c5d2** was actually applied on the disposable cluster. Deployment still requires that revision and restarting old workers together, as the implementation report specifies. This retest does not assert deployment has happened. The temporary cluster is stopped after final verification.

This is an integrated worker-system test plus separate browser transport regressions. It is not one continuous frontend → real Redis → API → real model session. Physical voice, live Redis/network outages, host crashes and component intelligence quality remain outside this run.

## Evidence

- [Machine-readable summary](shared-state-step7-results-2026-10-22.json)
- [Final backend log](shared-state-step7-final-2026-10-22.log)
- [Migration log](shared-state-step7-migration-2026-10-22.log)
- [Final combined backend results](shared-state-step7-final-2026-10-22.xml)
- [Scripted-success system timeline and durable state](shared-state-step7-system-scripted_success-2026-10-22.json)
- [Unavailable-adapter system timeline and durable state](shared-state-step7-system-unavailable-2026-10-22.json)
- [Chromium transport results](shared-state-step7-browser-2026-10-22.json)
- [System/observation focused run](shared-state-step7-system-2026-10-22.xml)

Rerun the integrated cases from backend with isolated `TEST_DATABASE_URL` and `DATABASE_URL`, empty `REDIS_URL`, and UTC database time: `uv run pytest tests/test_background_system_e2e.py -q`. The fixtures configure the production runner with the selected adapters. The full selected command and logs are recorded with the final summary artifact.
