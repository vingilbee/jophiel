# Chat UI Smoothness Test Results

Date: 2026-09-09

## Scope

Real authenticated browser tests against the local backend, database, Redis, Vite proxy, and WebSocket. The backend used its deterministic QA response provider so UI and transport timing could be measured repeatably without live model variability.

## 1. Smoothness and responsiveness

Status: PASS for the tested baseline.

| Metric | Result |
| --- | ---: |
| Estimated frame rate | 60.4 FPS |
| Frames over 20 ms | 0 |
| Frames over 33 ms | 0 |
| Worst frame | 17.7 ms |
| Long tasks over 50 ms | 0 |
| P95 typing latency | 15.7 ms |
| Maximum typing latency | 15.9 ms |
| Layout shift | 0.00034 |
| Uncaught page errors | 0 |

The real flow completed successfully: signup → signin → authenticated chat → real WebSocket → backend streaming.

## 2. Interruption and concurrency

### Passed

- Stopping midway preserved partial output.
- No later tokens appeared after Stop.
- A follow-up after Stop rendered one clean response without stale-token contamination.
- Rapid follow-up messages appeared once each in the UI.
- No uncaught page errors occurred.

### Suspicious

- Rapid follow-up persistence produced consecutive user messages and did not persist an assistant response for every rapid user turn. Example persisted role sequence:

  `user → assistant(partial) → user → assistant(complete) → user → user → assistant(complete)`

This may be intentional cancellation behavior, but the transcript contains user turns without matching assistant results and needs an explicit product/server policy.

## 3. Network and lifecycle

### Passed

- Disconnect/reconnect eventually recovered to `Live`.
- Refresh during streaming restored the user message.
- No uncaught page errors occurred.

### Suspicious

- Navigating away during streaming and returning to chat did not restore the active user message.
- Rapid follow-up persistence stored both user messages but no assistant responses for those turns in the inspected transcript.
- Repeated Stop → refresh cycles left the chat unusable in all five cycles: `Live` was absent and Send was disabled.
- Disconnect did not show a clear visible `Offline` or `Connecting` state during the offline interval.
- Repeated WebSocket initialization warnings occurred: connections closed before establishment, plus `401` and `404` responses. The socket eventually recovered, but the initialization path is noisy and potentially fragile.

## 4. Recommended follow-up

1. Fix the Stop → refresh state so the chat returns to a usable authenticated socket and enabled composer.
2. Define and test the persistence contract for cancelled and queued turns.
3. Prevent active user messages from disappearing after navigation.
4. Trace the WebSocket `401`/`404` initialization requests and add an explicit offline/reconnecting UI state.
5. Rerun the lifecycle suite after fixes.
6. Then test mobile viewports, long transcripts, and memory/socket growth.

## Test artifacts

- [Test plan](./CHAT_UI_SMOOTHNESS_TEST_PLAN.md)
- [Smoothness audit](./smoothness_audit.mjs)
- [Interruption audit](./chat_interruption_audit.mjs)
- [Lifecycle audit](./chat_lifecycle_audit.mjs)

## Regression rerun: 2026-09-10

The updated backend/client changes were tested against the real authenticated stack after restarting the QA backend and running the browser audits serially.

### Smoothness

Status: PASS.

- Estimated frame rate: 60.3 FPS.
- Frames over 20 ms: 0.
- Frames over 33 ms: 0.
- Worst frame: 17.7 ms.
- Long tasks over 50 ms: 0.
- P95 input latency: 16.0 ms.
- Maximum input latency: 16.3 ms.
- Layout shift: 0.
- Uncaught page errors: 0.

### Interruption and persistence

Status: PASS.

- Stop midway preserved partial output and stopped later tokens.
- Follow-up after Stop had no stale-token contamination.
- Rapid follow-up messages rendered once each.
- Persisted turns now have explicit assistant outcomes, including an empty cancelled assistant record for the interrupted turn.
- No uncaught page errors occurred.

### Network and lifecycle

Status: PASS for the tested scenarios.

- Disconnect/reconnect recovered to `Live`.
- Refresh during streaming restored the user turn.
- Navigation away and back restored the active user message.
- Rapid follow-up persistence passed: the cancelled and completed outcomes were both represented.
- Five Stop → refresh cycles all returned to `Live` with Send enabled.

### Remaining observation

The browser still logs occasional `401 Unauthorized` responses and repeated WebSocket connections that close before establishment during initialization/reconnect. The application recovers and all lifecycle assertions pass, but this transport noise remains worth tracing.

The first parallel rerun was invalid because the shared Vite proxy dropped under concurrent audits. The final smoothness and interruption results above were run serially with the proxy restarted.

## Voice-turn protocol test: 2026-09-10

The voice QA server used the real authenticated `/api/chat/ws`, real database, Redis, binary PCM frames, and deterministic STT/TTS fixtures.

### Passed

- A voice turn completed over the shared WebSocket path.
- Three binary audio frames were received.
- One stable `turn_id` was observed across the completed voice turn's protocol events.
- No uncaught browser errors occurred.
- The voice code no longer opens a separate legacy WebSocket path.

### Suspicious

- Barge-in cancellation did not stop the second voice turn: after sending `stop` during generation, the terminal event was `done` with status `completed` rather than `stopped`.
- The voice persistence audit could inspect only the newest conversation in this run; it should be expanded to aggregate all conversations before treating persistence as fully verified.

Follow-up priority: trace voice `stop` handling from the client frame through `_stop_active_turn` and `run_voice_turn`, then rerun with a guaranteed mid-token stop. Add an assertion that cancelled voice turns persist `partial` or `cancelled`, never `completed`.

### Voice cancellation root cause and fix

Investigation confirmed why the voice barge-in test can return `done/completed` after Stop:

1. `run_voice_turn` now creates a durable Redis-backed chat job using the voice `turn_id` and then subscribes to that turn's Redis stream.
2. `register_turn` stores only the asyncio task, stop event, and conversation ID; it does not store the durable voice `turn_id`.
3. `_stop_active_turn` calls `chat_worker.request_cancel(active.task.get_name().removeprefix("chat-turn:"))`.
4. Voice tasks are created without the `chat-turn:<turn_id>` task name, so the Redis cancellation key is generated from an asyncio task name rather than the actual job turn ID.
5. The chat worker therefore does not see the cancellation key, completes the job, and publishes `done/completed`.

The fix added `turn_id` to `_ActiveTurn`/`register_turn` and passes that exact ID to `request_cancel`. The Stop frame also carries the pending turn ID. A real rerun now emits `stopped` with status `cancelled` or `partial`, and the matching partial assistant record is persisted.

### Voice regression rerun: 2026-09-11

- Real voice WebSocket completion: PASS; three binary audio frames received.
- Stable turn identity: PASS.
- Voice barge-in cancellation: PASS; terminal event was `stopped` with status `partial`.
- Voice persistence: PASS; both completed and partial assistant outcomes persisted with matching turn IDs.
- Browser runtime errors: PASS; none observed.

## Extended coverage: 2026-09-10

Against the real authenticated backend and WebSocket:

- Unusual and unsafe inputs (`IN-01`–`IN-05`): PASS. Prompt-injection-like text, HTML/script-like text, long URLs, multiline text, Unicode, emoji, RTL text, and unusual whitespace remained data; no script executed and no page errors occurred.
- Duplicate Enter submission (`CT-05`): PASS. One rendered user message was produced.
- Long transcript scrolling (`SM-07`/`SM-08`): PASS. A real transcript reached 2,509px in a 737px scroller and moved correctly from top to bottom.
- Mobile responsiveness: PASS at 390×844. No horizontal overflow, estimated 60.0 FPS, worst frame 17.8ms, maximum input latency 14.9ms, and no page errors.

## Remaining real-backend coverage: 2026-09-11

The remaining scenarios were rerun against the authenticated application, real database, Redis-backed chat queue, durable worker, and shared WebSocket path. Only the generated token provider was deterministic so edge cases could be reproduced.

### Passed

- Markdown/code streaming remained readable, including fenced code.
- An unknown provider event was ignored safely with no page errors.
- A controlled generation failure rendered the expected error state.
- A subsequent retry succeeded after the failed turn.
- A 30-turn transcript remained scrollable and renderable: 4,653px scroll height in a 737px viewport, with no page errors.
- Under 4× CPU throttling and 150ms network latency, responsiveness remained bounded: 59.7 FPS, no frames over 50ms, worst frame 33.3ms, and 88.4ms input completion.

### Suspicious observations

- No new application-level suspicious behavior was found in this rerun.
- The broader lifecycle suite still records occasional WebSocket initialization/reconnect noise (`401 Unauthorized` and connections closing before establishment). The UI recovers and the lifecycle assertions pass, but this transport behavior remains worth tracing.

The prior retry warning was a QA-fixture false positive: production history merges adjacent user turns, and the fixture incorrectly matched the earlier `QA-ERROR` text while evaluating the retry. The fixture now evaluates the newest merged segment, and the retry passed.

## Audit qualification / correction: 2026-09-12

The earlier broad conclusion that all uncritical behavior had passed is withdrawn. The disposable chat QA environment explicitly set `MACROVISION_TOOLS_ENABLED=false` and did not exercise real agent tool delegation, so its successful chat-stream assertions did not prove MacroVision, Drive/Slides/PPTX, or other MCP tools were usable.

The prior UI tests also did not assert the exact barge-in state transition while text was present in the composer, nor did they treat a refresh during an in-flight turn as a required uninterrupted UI state. Those cases require a separate live test against the normal `mprocs` configuration.

Current status: transport/token tests are not sufficient evidence that the real agent tools work; mid-stream injection, Stop behavior, and refresh recovery are open regressions until reproduced and rerun against the normal frontend (`5173`) and backend (`8000`) stack.

## Real normal-stack injection rerun: 2026-09-12

The normal local stack was started with Postgres, Redis, the combined worker, backend `8000`, and frontend `5173`. A browser run used the actual model-backed worker and persistent WebSocket.

- First response showed `Stop`.
- After submitting a second message mid-stream, the UI showed `Stopping` and no `Stop` button at 0ms and 300ms.
- At 1.5s, the second response was active and `Stop` was visible again.
- Clicking `Stop` during that second response worked and rendered `Response stopped.`
- No page errors occurred.

This confirms a real handoff-state defect: the newly submitted turn is temporarily not stoppable while the first turn is being torn down and the queued turn is starting. The Stop implementation does work after the new turn becomes active, but the UI should expose a stable control/state during the transition instead of leaving the user with a disabled `Stopping` control.
