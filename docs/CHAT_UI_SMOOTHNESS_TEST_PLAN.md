# Chat UI Smoothness and Streaming Edge-Case Test Plan

## 1. Purpose

Evaluate whether the chat interface remains responsive and visually stable during ordinary use, token streaming, cancellation, errors, reconnects, navigation, and rapid user actions.

This plan covers both:

- Perceived smoothness: typing, scrolling, animation, and response feedback.
- Correctness under asynchronous activity: stale events, duplicate messages, stuck states, and persistence errors.

## 2. Scope

### In scope

- Composer typing and keyboard handling.
- Send, streaming, stop, retry, and completion states.
- Rendering incremental assistant output.
- Auto-scroll behavior during and after streaming.
- Multiple sends and concurrent generations.
- WebSocket disconnect/reconnect behavior.
- Navigation and refresh during an active response.
- Long conversations and long messages.
- Error, timeout, empty-input, and malformed-response states.
- User content resembling prompt injection.

### Out of scope

- Model quality or nutrition accuracy.
- Backend load testing beyond the UI impact of controlled latency.
- Full accessibility certification, except keyboard behavior relevant to this plan.

## 3. Test environments

Run the plan in at least these configurations:

| Environment | Viewport | Purpose |
| --- | ---: | --- |
| Desktop Chromium | 1440×900 | Baseline profiling and regression testing |
| Small mobile viewport | 390×844 | Layout, keyboard, and touch-like interaction |
| Firefox/WebKit | 1440×900 | Cross-browser behavior |
| CPU-throttled Chromium | 4× slowdown | Detect main-thread and render bottlenecks |
| Network-throttled Chromium | Fast 3G or equivalent | Streaming, timeout, and recovery behavior |

Use deterministic streamed responses for repeatable UI tests. Run a smaller set against the real provider to validate transport and timing assumptions.

## 4. Performance metrics and targets

| Metric | Target / acceptance threshold |
| --- | --- |
| Frame rate during typing, scrolling, and streaming | Approximately 60 FPS; investigate sustained drops below 50 FPS |
| Main-thread long tasks | No avoidable tasks over 50 ms during ordinary interaction |
| Keypress-to-render latency | Usually under 50 ms; no visible input lag |
| Send/Stop visual feedback | Control state changes within 100 ms |
| Stop-to-final-token latency | Under 200 ms in deterministic tests |
| Tokens rendered after Stop | 0 |
| Duplicate user or assistant messages | 0 |
| Stuck loading/Stop state | 0 occurrences |
| Unexpected layout shift | No composer/header movement; investigate measurable CLS |
| Scroll behavior | No jumps; preserve user position when reading older content |
| Memory and DOM growth | Stable after repeated send/stop/refresh cycles |
| Socket/request cleanup | No orphaned active requests or sockets after completion, stop, or navigation |

Record median and p95 timings for automated runs. Do not judge smoothness from average frame rate alone; inspect janky intervals and long tasks.

## 5. Functional state model

The UI should behave consistently across these states:

```text
idle → sending → streaming → completed
                    ├──────→ stopped
                    ├──────→ failed
                    └──────→ disconnected/reconnecting
```

For every transition, verify:

- The correct controls are visible and enabled.
- The composer draft is preserved or cleared intentionally.
- Only the active request can update the active assistant message.
- Late events from an old request are ignored.
- The user can recover without refreshing the page.
- Persisted history matches what was visibly rendered.

## 6. Test cases

### A. Baseline interaction and smoothness

| ID | Test | Expected result |
| --- | --- | --- |
| SM-01 | Type continuously in an empty composer | Characters appear without lag, dropped input, or layout movement |
| SM-02 | Type while a response is streaming | Composer remains responsive; streaming does not block input |
| SM-03 | Send short, medium, and very long messages | Send feedback is immediate; layout remains stable |
| SM-04 | Press Enter once | Exactly one message is sent and draft is cleared intentionally |
| SM-05 | Press Enter or click Send rapidly | No duplicate request or message is created |
| SM-06 | Submit empty/whitespace input | Submission is prevented or a clear validation state appears |
| SM-07 | Scroll a long transcript | Scrolling is smooth and does not trigger excessive rerenders |
| SM-08 | Load 20, 100, and 500 messages | Initial render and scrolling remain usable; no runaway DOM/memory growth |

### B. Streaming and auto-scroll

| ID | Test | Expected result |
| --- | --- | --- |
| ST-01 | Stream a short response token-by-token | Incremental text appears smoothly and ends in a completed state |
| ST-02 | Stream a long response | No visible stutter, duplicated text, or excessive layout thrashing |
| ST-03 | Start at the bottom during streaming | View follows new content smoothly |
| ST-04 | Scroll upward during streaming | User position is preserved; no forced jump to the bottom |
| ST-05 | Return to the bottom during streaming | Follow mode resumes intentionally |
| ST-06 | Stream markdown/code blocks | Partial content remains readable and final formatting is correct |
| ST-07 | Receive an empty or malformed stream event | UI remains recoverable and does not enter a stuck state |

### C. Stop, retry, and concurrency

| ID | Test | Expected result |
| --- | --- | --- |
| CT-01 | Stop before the first token | Request stops cleanly; no assistant text appears afterward |
| CT-02 | Stop halfway through a response | Partial response remains clearly stopped or incomplete; Stop disappears |
| CT-03 | Stop and immediately send a new message | New response is isolated from the stopped response |
| CT-04 | Send a second message while the first streams | Product policy is enforced: queue, cancel, or reject; never interleave replies |
| CT-05 | Double-click Send | One request and one user message only |
| CT-06 | Stop after completion begins | No error or stuck state; completed response remains intact |
| CT-07 | Retry a failed or stopped response | Exactly one new generation is created with correct ordering |
| CT-08 | Deliver late events from a cancelled request | Late events are ignored and cannot mutate newer messages |

### D. Network, lifecycle, and persistence

| ID | Test | Expected result |
| --- | --- | --- |
| LC-01 | Disconnect during initial send | Clear error/reconnect state; controls become usable again |
| LC-02 | Disconnect during streaming | Partial content is handled intentionally; no infinite spinner |
| LC-03 | Reconnect after disconnect | Socket/request recovery is bounded and does not duplicate content |
| LC-04 | Refresh during streaming | Transcript is consistent after reload; no duplicate assistant message |
| LC-05 | Navigate away during streaming | Requests/sockets are cleaned up or explicitly supported as background work |
| LC-06 | Return to chat after navigation | Correct conversation and active state are restored |
| LC-07 | Provider/server error after partial output | Partial output is preserved and visibly marked failed |
| LC-08 | Repeat send → stop → refresh 20 times | No memory, DOM, listener, or socket growth beyond expected bounds |

### E. Input safety and unusual content

| ID | Test | Expected result |
| --- | --- | --- |
| IN-01 | Send text containing “ignore previous instructions” | It remains ordinary user content; it does not change system behavior |
| IN-02 | Send HTML/script-like text | Content is rendered safely as text or sanitized markdown |
| IN-03 | Send very long unbroken text/URLs | Wrapping and horizontal layout remain controlled |
| IN-04 | Paste multiline text | Composer handles it according to product policy without freezing |
| IN-05 | Include Unicode, emoji, RTL text, and unusual whitespace | No crashes, truncation, or incorrect message ordering |

## 7. Instrumentation

Use:

- Chrome DevTools Performance panel for frame rate, scripting, layout, paint, and long tasks.
- Chrome Performance Monitor for CPU, JS heap, DOM nodes, and event listeners.
- React DevTools Profiler to identify components rerendering on every token.
- Playwright for repeatable typing, streaming, stop, reconnect, and persistence tests.
- Browser console and WebSocket logs to detect uncaught errors, duplicate frames, and stale events.

Capture these timestamps for each streamed turn:

```text
send_clicked
request_started
first_token_rendered
stop_clicked (if applicable)
last_token_rendered
terminal_state_rendered
persisted_to_server
```

## 8. Execution order

1. Run baseline interaction tests with deterministic responses.
2. Profile typing, scrolling, and token streaming.
3. Run stop/cancel and rapid-action tests.
4. Run disconnect, reconnect, refresh, and navigation tests.
5. Repeat the most important cases on mobile and throttled CPU/network.
6. Run long-transcript and repeated-cycle memory checks.
7. Run real-provider smoke tests to validate that deterministic results reflect production transport behavior.

## 9. Evidence to collect

- Test result JSON with pass/fail and timings.
- Performance traces for baseline, long streaming, stop, and reconnect cases.
- Screenshots or recordings of visible failures.
- Console and WebSocket logs for lifecycle failures.
- Memory/DOM snapshots before and after repeated cycles.
- Browser, viewport, CPU/network throttling, commit, and test data identifiers.

## 10. Exit criteria

The chat UI is ready for normal use when:

- No P0/P1 lifecycle or message-ordering failures remain.
- Stop, retry, disconnect, and refresh flows are deterministic and recoverable.
- No duplicate, interleaved, or stale assistant output is observed.
- Typing and scrolling remain responsive under CPU/network throttling.
- No sustained jank or avoidable long tasks occur during ordinary interaction.
- Memory, DOM, event listeners, and sockets remain stable over repeated cycles.
- Mobile layouts preserve the composer and primary controls without overflow.
