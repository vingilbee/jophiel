# Shared-state step 3: live frontend E2E

October 21, 2026, approximately 5:59–6:02 p.m. America/Vancouver.

**Step 3 worked in this live text-chat sample: 12 real frontend exchanges across five conversations.** Greetings, recent-context follow-ups, a changed choice, topic switching, cached memory, both student wording modes, and a disabled-student degraded turn completed. One known frontend gap remains: degraded status is sent but has no visible indicator.

## Method and scope

- Ran the current local source including existing uncommitted changes. No application code or `.env` edits were made.
- Started FastAPI on port 8000, Vite on 5173 and the real chat worker loop. No mocked APIs, model outputs, WebSockets, memories or answers. The test drove Chromium through the signup form, chat composer, Send and New chat controls.
- Normal settings: `SHARED_STATE_PIPELINE_ENABLED=true`, `COGNITIVE_LIVE_STUDENT=ab`, character and recall enabled, mind disabled. Temporary worker process overrides exercised `COGNITIVE_LIVE_STUDENT=on` and `off`; original settings were restored afterward.
- Created isolated user `5891ec9a-3e52-4daa-b2e9-3eaaa00fa15a` for normal, reading and degraded tests. The cache test reused the existing fictional **Memory Retest** fixture, `0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9`, with a scoped test session. No memory records were seeded or modified by scripts.
- Verified each turn against observed browser WebSocket frames, rendered text, authenticated history, owner-scoped Postgres jobs/messages/receipts and `var/shared_state.jsonl`. All 12 jobs completed on attempt 1, each with one user row, one assistant row, one `ack`, one `done` and one matching trace. No browser JavaScript exceptions were observed.
- Only the chat worker ran; background consolidation and correction workers were not started. This tests conversation continuity and existing cache integration, not durable learning of the newly introduced lighthouse choice.

## Results

| Probe | Observed result |
|---|---|
| Greeting | Relevant streamed reply; cognition available even in `page_only` arm. |
| Recent-context follow-up | Correctly repeated **Silver Wren**, chosen because it sounded livelier. |
| Changed choice | Acknowledged **Quiet Harbor**, then answered the follow-up with the updated choice. This was a same-conversation update, not a stored-memory correction test. |
| Topic change | Followed the switch to rain on windows without carrying the lighthouse discussion into the response. |
| New chat | New conversation ID and fresh greeting; no cached references from the other conversation appeared. |
| Existing memory tool | One real mouth memory-tool call. The prompt intentionally called Lantern an astronomy club; the response identified the saved **book club**, Saturday **6 pm**, **library courtyard**, and did not endorse astronomy as the stored subject. |
| Cached follow-ups | Two subsequent turns returned **6 pm / library courtyard**, then **Saturday**. Both receipts show `source: cache`, four supplied event references, zero tool calls. The same reference fingerprints appear in both state dependency traces. |
| Reading arm | With worker student mode `on`, trace records `arm reading`, available cognition, a streamed response and completed persistence. Normal A/B conversations happened to select `page_only`; this override checks reading mode, not random-assignment distribution. |
| Disabled student | With worker student mode `off`, stream and trace record `outcome: degraded`, `cognition_unavailable`, cognition disabled. Mouth returned a constrained ordinary greeting and the turn completed. |
| Streaming and persistence | Captured multiple token frames per turn; saved reply text exactly matches the assembled streamed output. Sticker markers rendered as images on the sampled sticker replies. |

Every normal/reading/cache turn was honestly labelled **`unassessed`**, never confirmed `fast_path`. All traces record zero fresh recall before reply and zero mind calls. The one mouth-initiated search is recorded separately as `memory_tool_calls_by_mouth: 1` and `mouth_escalated: ["searched_memory"]`. This is existing tool use, not step 4 conditional recall routing.

## Latency

Across the seven normal turns, browser-measured first-token latency was **1.43–3.86 s**, median **1.89 s**; completion median **2.32 s**. The first turn included about **2.28 s** of cognition preparation; later normal cognition preparation took tens of milliseconds. The single reading-mode turn immediately after restarting the worker took **6.62 s** to first token and **7.36 s** total, including **2.54 s** cognition preparation. The degraded turn took **2.16 s** to first token.

This small text-only sample is not a latency regression comparison: there was no flag-off control, spoken-turn measurement or statistically useful p95 sample.

## Confirmed gap and limits

The frontend ignores the `route` event. The disabled-student test emitted the correct degraded status, but the screenshot shows an ordinary answer with no degraded indicator. This agrees with the implementation report's outstanding UI item. Protocol and trace visibility work; user-visible degraded status remains incomplete.

Step 4 remains untested live: evidence-need assessment, conditional recall routing, Jev/mind decisions and result return, acknowledgement/silence and bounded rerouting. No live claim is made for confirmed fast-path eligibility, cache invalidation after correction/deletion, retrieval outage, runtime student exception, voice/barge-in, Redis-unavailable WebSocket fallback, WhatsApp, restart recovery, supersession or duplicate-job replay. Both cached follow-ups also had the relevant answer in recent transcript: the receipts prove cache supply and reread, not exclusive causal dependence on the cache.

The live Student output and invocation were verified; this sample does not evaluate its classifier accuracy. Optional inner-state/activity/situation/mind integrations were not enabled by this test.

## Evidence and rerun

- [Verified jobs, receipts, dependency traces and checks](shared-state-e2e-verification-2026-10-21.json)
- [Normal frontend turns and frames](shared-state-e2e-normal-2026-10-21.json)
- [Memory tool and cache follow-ups](shared-state-e2e-cache-2026-10-21.json)
- [Reading-mode turn](shared-state-e2e-reading-2026-10-21.json)
- [Degraded-mode turn](shared-state-e2e-degraded-2026-10-21.json)
- [Normal screenshot](shared-state-e2e-normal-2026-10-21.png), [cache screenshot](shared-state-e2e-cache-2026-10-21.png), [reading screenshot](shared-state-e2e-reading-2026-10-21.png), [degraded screenshot](shared-state-e2e-degraded-2026-10-21.png)
- [Explicit live browser runner](shared-state-e2e-runner-2026-10-21.mjs): from repository root, `node docs/reports/shared-state-e2e-runner-2026-10-21.mjs`, with local services running. It creates a fresh fixture account and runs the seven normal turns. Optional `E2E_MODE=reading|degraded|cache` uses a pre-existing test cookie file and requires the matching worker configuration; sessions are deliberately excluded from artifacts.
- [Read-only verification script](shared-state-e2e-verify-2026-10-21.py): from backend, `uv run python < ../docs/reports/shared-state-e2e-verify-2026-10-21.py`. It checks saved runs against live database rows and traces.

The original browser probe's exact raw-reply selector did not match replies containing sticker markers; the subsequent rendered-text check removes those markers, verifies the text captured from the page and is authoritative in the verification export. No product rendering failure was observed.

Local API, frontend and chat worker were left running with the original `.env` settings. Temporary test browser sessions were revoked after evidence collection.
