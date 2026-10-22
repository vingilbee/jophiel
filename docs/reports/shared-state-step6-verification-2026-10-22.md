# Step 6 interaction verification

October 22, 2026. Current checkout: `abcbd71`, branch `feature/source-backed-recall`, plus the existing uncommitted step-5 changes.

**Not a pass yet: four reproducible coordination/transport failures, independent of cognition quality.** Backend: **196 passed, 2 failed**. Chromium: **1 passed, 2 failed**. Frontend audio utilities: **21 passed**. No skipped backend tests in the final selected run.

## Confirmed failures

### 1. A late autonomous message changes the frontend's active conversation

Reproduction: complete a turn in chat A; select New chat and complete a turn in B; receive an autonomous announcement targeting A; send the next message while still looking at B's transcript.

The third outgoing WebSocket `run` targets **A**, although the user was in **B**. `ChatPage.onNudgeStart` unconditionally calls `setConversationId(conversationId)` and appends the proposal to the current transcript. Correct backend target binding therefore does not protect the frontend's selected conversation.

Source: `frontend/src/pages/ChatPage.tsx:221`. Reproduction: `frontend/e2e/shared-state-interactions.spec.ts`, `a late autonomous reply for another conversation cannot retarget the composer`. Expected ID ends `002`; received ID ends `001`.

This is a same-user cross-conversation routing failure, not cross-user access. The affected frontend handler predates the current step-5 diff; this verifies the current interaction, not that step 5 introduced it.

### 2. Active normal replies are not restored by WebSocket replay after reconnect

Reproduction: start a normal turn, receive a partial answer, close the socket with a service-restart code, reconnect, bootstrap history containing the user message, then replay the turn's text and terminal event.

The replacement connection is established and history is displayed, but the recovered answer never appears. `useChatSocket` rejects and clears `pendingRef` on close; subsequent replayed tokens return at `if (!pending) return`. The page retains “Connection closed.” rather than showing the replayed answer.

Source: `frontend/src/hooks/useChatSocket.ts:303` and `:334`. Reproduction: `a socket restart can resume an active reply from server replay`. This test controls server frames; it does not restart the production API or test real Redis replay. It demonstrates that the current browser cannot consume the expected replacement stream. A later history reload after completion may recover stored text; continuous recovery failed here.

### 3. A committed conversational mind result can be orphaned by worker exit

Reproduction: submit a normal request requiring mind; in a separate Python process claim its work and commit its completed result; terminate that process with `os._exit(17)` before conclusion dispatch; start a new coordinator process with scripted result cognition authorizing `respond`.

The work remains completed, but no follow-up output/job is created. Restart recovery in `coordinator.run_once` explicitly scans only `input.autonomous == true`. Completed conversational work is no longer claimable by `_claim`, so this gap has no recovery path.

Source: `backend/app/services/coordinator.py:182`. Reproduction: `test_conversation_result_survives_real_worker_process_exit`. The equivalent autonomous proposal survives the same separate-process crash and delivers once, so the failure is source-specific rather than a general harness/database problem.

### 4. Crash after reply commit can strand the output receipt and announcement

Reproduction: authorize an autonomous proposal, run its real mouth worker with scripted text, then inject process termination at `coordinator.follow_up_outcome`, immediately after the worker commits the assistant row and marks its ChatJob completed. Start a fresh coordinator process.

The assistant message exists, but its output remains **`dispatched`**, with no assistant receipt ID, and no announcement occurs. The chat job is already terminal, so normal job recovery will not reclaim it. Coordinator orphan recovery also skips it because an output row exists. The existing-assistant early-return path does not reconcile this output either.

Source: `backend/app/workers/chat.py:505` and `:520`. Reproduction: `test_restart_repairs_receipt_after_reply_commit_before_output_update`. The process-exit boundary is injected in the real worker coroutine; the recovery process is a separate interpreter. This exposes a durable consistency window; it does not claim exactly-once external delivery is attainable.

## Mechanics that passed

- Autonomous output waits behind a leased normal reply, then uses the same per-conversation job arbitration.
- Concurrent repeated conclusion dispatch creates one reservation/job and one delivery.
- Duplicate proposals, repeat processing after completed delivery, and ordinary lease reclaim do not repeat the mouth output in the exercised cases.
- An autonomous result accepted immediately before a real coordinator-process exit is recovered by a new process and delivered once.
- Backend proposals retain their explicit conversation target even when another conversation exists; another user's conversation is rejected.
- New messages fence queued proposals and proposals whose mouth is generating. Late thinking results remain private after supersession.
- Plan, execution and lifecycle revisions each fence proposals before cognition, before mouth, and during mouth generation: **nine combinations passed**.
- Quiet mode changed during mouth generation cancels final autonomous persistence/announcement.
- A scripted voice cancellation stops synthesis after one audio chunk, records a partial outcome and does not announce the interrupted reply as a completed autonomous message.
- A new persisted user message during scripted follow-up audio prevents final autonomous persistence and announcement. This is a stale-result fence, not a claim that a database message alone stops every already-in-flight audio chunk.
- Existing barge-in tests passed: tagged audio/stop events, sidecar closure on cancellation, hold/resume/commit behavior, stop-only interruption handling and teardown races.
- Heard-text/history tests passed, along with the frontend's VAD, PCM buffer, capture-mute and heard-text unit checks.
- Normal and autonomous text can occupy separate frontend bubbles without an autonomous event completing the pending normal turn.
- Production unsupported result cognition remains explicitly `blocked/not_sent`; scripted respond/ask/defer/silent branches are verified separately.

The initial broad run had one auth-test failure from receiving an extra `state` frame where it expected `error`. Its isolated rerun and the final combined run passed. It is not included among the four confirmed failures.

## Test isolation and limits

Used a newly initialized disposable PostgreSQL cluster at `/tmp/cognitive-step6-20261022`, port 55436. Each database-backed test creates and removes its own schema. The final run sets both application and test database URLs to that temporary cluster, disables Redis connections, and disables unrelated game/inner-state behavior outside explicit fixtures. The new race probes script cognition, Jev, mouth text and TTS. Existing producer tests independently cover quick/hard mind and planner submission wiring. No application account data or real model calls were used in this step-6 run.

The browser probes run the real React application in Chromium against Vite on port 4173, with synthetic HTTP/WebSocket responses. They are browser transport tests, not a single live backend-to-browser pipeline run. The HTTP interception was corrected before the reported run to avoid intercepting Vite's own `/src/api/` modules; initial blank pages were a harness error, not a product failure.

Physical microphone capture, actual speech recognition/TTS services, real Redis reconnect replay, host/network outages, WhatsApp delivery and real cognition decision quality remain outside this run. Tests passing under scripted cognition do not establish the missing trained capabilities. Prior step-4 cache/input and immediate-thinking-quality findings were not re-evaluated here.

The temporary PostgreSQL cluster was stopped after verification. Existing API/frontend/workers and the application database were not restarted, migrated or modified. No product source was edited. Only two new test files and the report/evidence were added. Existing uncommitted step-5 changes were preserved. The new regression tests intentionally remain failing where they expose the four defects.

## Evidence and rerun

- [Machine-readable results](shared-state-step6-results-2026-10-22.json)
- [Final backend JUnit results](shared-state-step6-final-2026-10-22.xml) and [backend output](shared-state-step6-backend-2026-10-22.log)
- [Frontend audio output](shared-state-step6-audio-2026-10-22.log)
- [Wrong-target screenshot](shared-state-step6-wrong-target-2026-10-22.png) and [assertion/context](shared-state-step6-wrong-target-context-2026-10-22.md)
- [Reconnect screenshot](shared-state-step6-reconnect-2026-10-22.png) and [assertion/context](shared-state-step6-reconnect-context-2026-10-22.md)
- New backend probes: `backend/tests/test_shared_state_interactions.py` (20 parameterized cases; 18 passed, 2 failed).
- New browser probes: `frontend/e2e/shared-state-interactions.spec.ts` (3 cases; 1 passed, 2 failed).

To rerun backend probes, provide an isolated PostgreSQL URL as both `TEST_DATABASE_URL` and `DATABASE_URL`, set `REDIS_URL=''`, and run from backend:

```sh
SHARED_STATE_PIPELINE_ENABLED=false SHARED_STATE_ASSESSMENT_ENABLED=false RECALL_ENABLED=false COGNITIVE_LIVE_STUDENT=off DESKTOP_AGENT_ENABLED=false MIND_INNER_STATE_ENABLED=false uv run pytest tests/test_shared_state_interactions.py -q
```

The fixture explicitly enables the new pipeline with scripted cognition for these tests. To rerun the browser probes, from frontend:

```sh
bun run test:e2e shared-state-interactions.spec.ts
bun run test:unit
```

Step 6 should remain open until the four reproductions pass after repair, followed by a live transport/voice run. Cognition capability and quality remain a separate track.
