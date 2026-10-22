# Step 6 repair retest

October 22, 2026. Retested the current working tree at `abcbd71`, including the other agent's uncommitted coordination/transport repairs. The original failure report is retained as historical evidence.

**All four previously failing reproductions now pass.** The broader selected verification also passes: **202 backend tests, 5 Chromium tests, 21 frontend audio unit tests; zero failures or skips.** Frontend production build and `git diff --check` passed.

## Fixes verified

| Previous failure | Retest result |
|---|---|
| Late autonomous output for chat A retargeted the chat B composer | Pass: the next message remains targeted at B. |
| Active reply was lost after reconnect despite server replay | Pass: replacement-stream tokens restore the pending answer. |
| Conversational mind result committed before worker exit was orphaned | Pass: separate-process crash/restart recovers the result and dispatches its follow-up. |
| Reply committed before output update left its receipt dispatched/unannounced | Pass: receipt and pending-announcement state survive the injected crash boundary and recovery completes. |

Additional passing checks include repeated announcements with the same message ID producing one frontend bubble, replies completed while offline settling from history without resending, real WebSocket history selecting an owned pending turn rather than the latest chat, foreign-user turn isolation, announcement failure/retry without regenerating the mouth, and repair of older dispatched/terminal-job gaps.

The surrounding step-5/step-6 selection continues to pass: normal/autonomous serialization, duplicate dispatch, explicit target ownership, new-message fences, plan/execution/lifecycle revisions at three stages, quiet-mode changes during generation, scripted voice cancellation, barge-in/teardown handling, heard-text history, producer wiring and explicit unsupported cognition behavior.

## Method and limits

- Restarted only the disposable PostgreSQL cluster on port 55436. Database-backed fixtures create/remove independent schemas; both application and test database URLs in the test processes point at this isolated cluster.
- Ran the same original backend selection plus the new interaction and repair cases, using scripted cognition, Jev, mouth and TTS adapters. Crash probes use a separate interpreter or an injected process-exit boundary.
- Ran the real React application in Chromium using synthetic HTTP/WebSocket frames. These verify browser behavior; they are not a live Redis/API-to-browser transport run.
- Ran `bun run test:unit` and `bun run build` from frontend.
- Applied the complete Alembic upgrade chain successfully on the disposable cluster, ending at **a2d4f6b8c0e1**. This verifies actual upgrade execution, not just SQL generation. The cluster was stopped after testing.
- No application source/configuration was edited, no application migration was applied, and existing application processes were left untouched.

Physical microphone/STT/TTS, real Redis outages, live host restarts, WhatsApp, and trained cognition quality remain separate verification. This passing scripted-mechanics result does not claim the known cognition capabilities are implemented.

## Application rollout still pending

A read-only check found the application database at **f1c3a5e7b9d2**. It still needs **a2d4f6b8c0e1** before starting the updated backend/API/workers. The new migration adds the durable `announcement_pending` column used by the repaired code. This retest leaves the application schema unchanged.

## Evidence

- [Machine-readable summary](shared-state-step6-retest-results-2026-10-22.json)
- [Backend JUnit results](shared-state-step6-retest-backend-2026-10-22.xml) and [backend output](shared-state-step6-retest-backend-2026-10-22.log): 202 passed, 0 skipped, 54.09 seconds.
- [Chromium results](shared-state-step6-retest-browser-2026-10-22.json): 5 passed, 6.1 seconds.
- [Audio-unit output](shared-state-step6-retest-audio-2026-10-22.log): 21 passed.
- [Production build output](shared-state-step6-retest-build-2026-10-22.log)
- [Actual migration upgrade output](shared-state-step6-retest-migration-2026-10-22.log)

Backend command: the original selected files plus `tests/test_shared_state_interactions.py`, with isolated `TEST_DATABASE_URL`/`DATABASE_URL`, empty `REDIS_URL`, and normal external routing/game/inner-state flags disabled outside the scripted fixtures. Browser command: `bun run test:e2e shared-state-interactions.spec.ts --reporter=list,json`.
