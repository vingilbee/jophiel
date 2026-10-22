# Step 4 live frontend E2E: routing works, three integration gaps remain

October 22, 2026, approximately 6:50–6:57 a.m. America/Vancouver. **20 real frontend exchanges, one newly created synthetic QA account.**

Jev selected and executed all four routes: **12 fast_path, 5 recall, 2 mind, 1 recall_then_mind**. All 20 immediate replies streamed and persisted exactly once. This is not an overall E2E pass: cache content is dropped from Jev's input, immediate mouth replies bypass the pending-thinking boundary, and completed thoughts cannot pass the current result-cognition provider.

## Findings

### 1. Cached fact content never reaches Jev

`services/shared_state.py:266` splits the rendered cache and keeps only lines beginning `- `. Memory renders a record as a metadata header followed by an indented content line (`services/recall.py`, `_record_line`). The filter retains the header and discards the actual fact.

The diagnostic frontend test isolated this from recent-conversation evidence:

1. In a fresh conversation, request a 180-word fictional harbor scene, then the saved Cobalt meeting details only at the end. Jev chose recall; the answer contained the correct details after character 600.
2. Ask the day/time/place again. The shared state still had the valid fact reference and fingerprint. Jev's recent-conversation projection clips the preceding assistant message at 600 characters, excluding those details.
3. Jev chose **recall required** (probability 0.72), causing another matched search. The answer was correct, but cache sufficiency was not recognized.

A read-only reconstruction of the current adapter confirms: one cached record, full cache contains **Thursday / 7 pm / Glass Pavilion**, selected `memories_supplied` contains none of those answer values, and the clipped conversation contains none either. This is a deterministic input-shaping defect, not evidence that Jev ignored a fact it was given. The reconstruction is not a captured HTTP request; it applies the same production shaping to the recorded trigger and stored receipt, omitting the irrelevant quick-reading field.

An ordinary immediate follow-up did skip recall, but the prior answer was also visible in its recent context. That success alone would have hidden this defect.

Evidence: [cache-isolation turns](shared-state-step4-cacheprobe-2026-10-22.json), [cache and shaped-input reconstruction](shared-state-step4-cache-input-2026-10-22.json).

### 2. Mouth answers the delegated problem before mind completes

All three thinking requests received substantive final answers in the immediate phase:

- Logic puzzle: mouth gave the complete box-identification solution.
- Venue choice: mouth recommended B, weighed tradeoffs and listed booking checks.
- Recall plus thinking: mouth retrieved memories through two of its own tool calls and supplied a complete preparation plan, alongside the coordinator's separate recall dependency.

The state boundary explicitly tells mouth that work is unfinished and not to replace the pending conclusion with its own quick answer (`shared_state.boundary_text`). Actual delivery still precedes accepted mind completion:

| Request | Immediate output recorded delivered (UTC) | Mind completed (UTC) |
|---|---|---|
| Logic puzzle | 13:50:50.539 | 13:50:52.632 |
| Venue choice | 13:53:23.431 | 13:53:24.289 |
| Recall + thinking | 13:54:39.555 | 13:54:40.962 |

These are coordinator database timestamps. The immediate outputs use `decision_origin: policy_fallback`, because pending-thought cognition is unsupported. The background work is real and asynchronous; the user-visible answer bypasses its result.

### 3. Every completed thought is blocked from delivery

All three mind jobs completed on attempt 1, without a model error, using the real `HardThinker`. Each then produced:

```text
phase: follow_up
decision: blocked
decision_origin: unsupported
status: not_sent
chat_job_id: null
```

`cognition_decisions.student_result` unconditionally reports unsupported; `coordinator._conclude` therefore records blocked rather than dispatching a follow-up. This confirms the implementation report's disclosed capability gap. There is no live mind → supported cognition → mouth completion today. No teacher or scripted cognition provider was substituted to make this appear to pass.

## Working paths

| Probe | Live behavior |
|---|---|
| Greeting, general knowledge | One successful Jev assessment, confirmed fast path, no fresh recall or mind work. |
| Recent-context follow-up and changed choice | Correctly remembered Silver Wren, then Quiet Harbor after the change; no recall. |
| Ambiguous “send it to her” | No arbitrary memory lookup or thinking work; mouth asked what to send and to whom. No external message was sent. |
| Uncached saved fact | Recall route searched before mouth, matched the synthetic fact, returned Thursday / 7 pm / Glass Pavilion. |
| Immediate cached follow-up | Confirmed fast path, supplied cached reference, zero fresh searches. Recent conversation also supported the answer. |
| Cache expires after unrelated exchanges | An earlier planned cache probe encountered an empty cache after topic changes. `_prior` intentionally drops references when the preceding reply was associated with none. Jev correctly recalled again; this was an invalid cache-retention assumption in the provisional test label, not a classifier error. |
| Unknown personal history | Recall `no_match`; reply said it could not find the name and would not guess. Mouth also made one additional memory-tool call. |
| Explicit and implicit reasoning | Both chose mind; durable jobs were committed and really completed. |
| Recall + reasoning | Recall dependency completed as `matched`, then mind consumed that evidence. Snapshot shows recall completed while mind was running; mind result records `evidence_status: matched` and the matching dependency ID. |
| Transport and persistence | All 20 jobs completed on attempt 1, with one assistant row matching streamed text, one terminal `done`, and one matching per-turn trace. No browser JavaScript exceptions. |

Jev was `jev-1.14.0`, policy `conversation-assessment-policy-v1`. All 20 assessments succeeded; median provider latency **82.95 ms**, range **47.3–359.2 ms**. Browser first-token median was **1.31 s**, range **0.94–5.76 s**, including startup and tool-bearing turns. These are a small text-chat sample, not a flag-off comparison or a voice latency evaluation.

Pre-call provisional labels matched 18/20 decisions. The exceptions are the two cache probes above: one lost its cache under the existing reference policy, one exposed the content-stripping defect. These numbers are diagnostic, not a formal Jev accuracy score. The separate draft evaluation cases and thresholds were not changed or run.

## Environment and data

- Local API/frontend/workers were initially stopped. `.env` had the shared-state pipeline on, but **assessment off**; the TypeSafe key was configured.
- Applied the existing additive migration **e8b2d6f4a1c7 → f1c3a5e7b9d2**, creating coordinator work/output tables. This database change remains applied.
- Started API, frontend, real chat worker and real coordinator worker. Enabled `SHARED_STATE_ASSESSMENT_ENABLED=true` in process environments only; `.env` remains unchanged. `COGNITIVE_LIVE_STUDENT=ab`, recall enabled, `MIND_ENABLED=false` for the independent autonomous/game loop. The step-4 coordinator nonetheless invokes the configured hard thinker for routed conversation work.
- Test user: **Step Four E2E**, `be1b4884-9fc7-4120-bf88-5bc9ff6ebff6`, created through frontend signup. All chat messages were entered using the real composer/Send/New chat controls.
- Taught explicitly fictional Cobalt reading-circle details through the frontend. Ran only that turn's actual memory consolidation job through `long_term_memory.process_job`, producing one fact with original-message provenance. No memory rows or model answers were seeded manually; unrelated pending memory jobs were not consumed.
- Automatic approval review rejected reuse of the older fixture because sending its conversations/cache to Jev lacked established data authorization. That fixture was not used; fresh synthetic data supplied the required recall coverage. No requested test remains blocked. Temporary sessions for both the unused fixture and new test account were revoked and cookie files removed afterward.
- A test-observer parser initially assumed every trace's `work` field was a dictionary; coordinator lifecycle lines use an ID string. Fixed the report helper, recovered the already-completed puzzle evidence without replaying the turn, and continued in separate runs. The clean JSON retains this as `harnessInterruption`; it was not an application failure.
- No application source or configuration files were edited. API/frontend/chat/coordinator processes were left running with the assessment override. Restarting from the unchanged `.env` disables Jev assessment again. General background workers were not started.

Not exercised: voice, WhatsApp, Redis-unavailable fallback, failure injection, cancellation during mind, restart/retry recovery, memory correction/deletion during work, or statistical routing evaluation. Private mind thinking text is excluded from the saved observation exports.

## Evidence and next fixes

- [Verification summary and per-turn decisions](shared-state-step4-verification-2026-10-22.json)
- [Initial eight turns](shared-state-step4-clean-2026-10-22.json), [venue and synthetic teaching](shared-state-step4-remaining-2026-10-22.json), [eight recall/cache/combined turns](shared-state-step4-synthetic-2026-10-22.json), [two isolated cache probes](shared-state-step4-cacheprobe-2026-10-22.json)
- [Stored synthetic fact and source](shared-state-step4-synthetic-memory-2026-10-22.json)
- [Combined-route screenshot](shared-state-step4-synthetic-recall_and_thinking-2026-10-22.png), [venue screenshot](shared-state-step4-remaining-decision_thinking-2026-10-22.png), [cache-probe screenshot](shared-state-step4-cacheprobe-final-2026-10-22.png)
- [Browser runner](shared-state-step4-runner-2026-10-22.mjs), [read-only observer](shared-state-step4-observe-2026-10-22.py), [read-only verifier](shared-state-step4-verify-2026-10-22.py). The verifier runs from backend: `uv run python ../docs/reports/shared-state-step4-verify-2026-10-22.py`. Test sessions are excluded and have been revoked; fixture continuation requires preparing a new session for the synthetic account.

Next fixes: pass bounded semantic memory content to Jev rather than parsing metadata-only lines; make pending-thinking behavior hold the delegated conclusion; implement the approved cognition capability for result evaluation before claiming end-to-end thinking delivery works. Re-run the isolated cache probe and the three thinking requests after those changes.
