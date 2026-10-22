# Step 6 coordination repairs

2026-10-22. Repairs to the four scripted-cognition failures in `shared-state-step6-verification-2026-10-22.md`. The original report remains the pre-repair evidence.

## Changes

1. **Conversation targeting:** an autonomous announcement never selects a conversation. The page accepts its bubble only for the selected target and ignores the other target's tokens. Normal reply metadata updates the selected conversation synchronously. Announcement message IDs are deduplicated in the socket relay and browser; history also supplies IDs already displayed. Ignored announcements do not play unprompted browser audio.
2. **Reconnect:** keep the pending turn identity, promise, and callbacks across reconnect/offline events. Restore its callback-target bubble from history and clear partial text before full stream replay. An answer completed offline settles directly from stored history, without another `run`. A reconnect supplies `resume_turn_id`; the server resolves its history under the authenticated user's ownership, avoiding latest-chat retargeting. A policy/authentication close ends the pending turn explicitly.
3. **Conversational result recovery:** scan completed, failed and expired mind work from both sources for missing **follow-up** decisions. An immediate-phase output no longer hides a committed mind result. Existing phase uniqueness protects repeat dispatch.
4. **Receipt and announcement recovery:** save the output status, assistant receipt and `announcement_pending` marker in the same transaction as the assistant message and terminal ChatJob. The coordinator drains pending announcements, clearing the marker only after successful publication. Transport failure retains the marker; retry never calls the mouth again. Older `dispatched` outputs whose jobs already ended are reconciled from their stored messages. The existing-assistant worker path also repairs receipts.

Publication is at least once: a crash after publication but before clearing the marker may repeat the same message ID. Consumers suppress duplicates; this does not claim exactly-once external delivery or guaranteed receipt by a disconnected browser. History remains authoritative.

## Validation

- Full original step-6 backend selection plus announcement retry/legacy-receipt tests: **200 passed**, no skips.
- Additional real WebSocket scoped-history tests: **2 passed**, covering owned pending-turn selection and foreign-user isolation.
- Chromium transport probes: **5 passed**, including both original browser failures, duplicate IDs, and completion while offline.
- Frontend audio unit tests: **21 passed**.
- Frontend production build and lint passed; lint reported three pre-existing Fast Refresh warnings in unrelated hooks.
- Migration upgrade SQL generated successfully; whitespace checks passed.

The backend runs used a disposable PostgreSQL cluster and independent schemas. Crash tests use a separate interpreter or an injected process-exit boundary. Cognition, mouth and TTS are scripted. Browser tests use the real React app with synthetic HTTP/WebSocket frames. No application data or live models were used.

## Rollout

Apply Alembic revision `a2d4f6b8c0e1` (parent `f1c3a5e7b9d2`) **before starting the updated backend workers/API**. It adds `coordinator_outputs.announcement_pending BOOLEAN NOT NULL DEFAULT false`. Existing historical delivered outputs are not indiscriminately reannounced; recoverable dispatched/terminal-job gaps are reconciled by the worker.

The migration has not been applied to the application database. No application services or feature flags were changed. Physical voice, real Redis outage/reconnect and live transport validation remain follow-up verification; cognition quality remains a separate track.
