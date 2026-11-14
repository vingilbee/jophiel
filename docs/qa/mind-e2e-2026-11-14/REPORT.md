# Mind continuity retest — November 14, 2026

## Result

Most fixes passed. The original reply-after-silence behavior is still not reliable: a simple
one-message silence followed by Ready passed, but the original cancellation-and-silence wording
followed by reload and Ready failed twice. Both requested reminders were delivered successfully.
No application source changes were made during this retest.

## Environment and setup

- Built-in browser at http://127.0.0.1:5173/chat; the existing isolated Mind E2E account was reused.
- Real `gpt-6-luna` responses; no mocked chat, scheduling, or memory responses.
- Stopped the API/worker processes from the prior test, applied pending migration
  `c8e4a1f7b3d2`, and restarted on the updated code.
- Confirmed the database revision is `c8e4a1f7b3d2`.
- Tested the desktop profile at 1280 x 800 and restored the viewport afterward.
- Local API and shared workers remained running after the test.

## Passing checks

### Thirty-second reminder, including browser reload

Used the original request to ask whether Lantern Finch was ready, in 30 seconds.
The mind called `note_commitment` and `come_back_to`. The stored schedule was:

- Scheduled: `2026-11-14T17:50:22.074471Z`.
- Due: `2026-11-14T17:50:52.070256Z` (30 seconds, without rounding to a minute).
- Wake work completed: `2026-11-14T17:50:53.094215Z` (1.024 seconds after due).
- Mind evaluation/delivery finished: `2026-11-14T17:50:55.406579Z` (3.336 seconds after due).

After a browser reload and without another user message, the UI displayed:

> Is Lantern Finch ready?

The database recorded the wake as completed and the associated promise as fulfilled. Its capture
shows `update_item`; fulfillment was backed by delivered speech. No repeat schedule was created.
Screenshot: `reminder-delivered.jpg`.

### Exact local-time scheduling

Asked for a check-in at `2026-11-14 10:53:00 America/Vancouver`.
The mind acknowledged that exact local time. The wake work completed 0.250 seconds after due,
and the UI displayed the requested message:

> Exact-time check complete?

The associated commitment was fulfilled. This verifies the supplied local time was resolved using
the configured IANA zone and executed, rather than merely acknowledged. Screenshot:
`exact-time-delivered.jpg`. Both test wake rows were completed; no active test reminders remained.

### Simple silence boundary

In a fresh conversation:

1. Sent `Do not reply to this message. Use stay_silent for this message only.`
2. Observed the silent outcome.
3. Sent `Reply with exactly: Ready.`
4. Received `Ready.`

This simple case passed. It did not establish that the original fuller failure was fixed;
see the remaining issue below.

### Current time and formatting

Asked for the local date, time including seconds, and timezone, plus bold project text and inline code.
The answer was:

> November 14, 2026, 10:51:48 AM America/Vancouver

The date/time matched the new evaluation context. The DOM contained a `strong` element for Lantern
Finch and a `code` element for Ready, rather than literal Markdown markers. User message text stayed
literal. Screenshot: `time-and-formatting.jpg`.

### Profile

The sidebar now displays `Mind E2E` and the initial M, matching the signed-in test account,
instead of the hardcoded Vincent/V. The profile was verified visibly at desktop width.

### Learning assessment

The restarted worker's captured output had no recurrence of the previous `IndexError`, and no
`ERROR`, `Traceback`, or `KeyError` entries were observed in the new log during this run.

The local database contains migrated `awaiting_notes`. The dedicated regression covering attempt
references, execution references, explanatory notes, malformed/unknown shapes, and repeated sweeps
also passed against disposable PostgreSQL schemas; see validation below. This is stronger evidence
than simply observing a quiet worker log.

## Remaining high-priority failure: silence still overrides new requests

Reproduction using the original wording:

1. Send `Cancel every scheduled follow-up about Lantern Finch. Use the cancellation tool, then use
   stay_silent: I do not want a reply to this message.`
2. Wait for silence, then reload the page.
3. Send `Reply with exactly: Ready.`
4. Observe `She chose not to reply.`
5. Send the exact Ready request again.
6. Observe a second silent outcome.

The new history code is no longer merging these user messages. A post-run history reconstruction
shows the cancellation message, a separate internal silence-outcome record, and a separate Ready
message explicitly labelled CURRENT, each with timestamps/turn identity. The worker calls
`get_history(records=True, current_message_id=...)` and the conversion preserves separate messages.
The reconstruction also contains the later failed evaluation's own outcome record; that final record
was created afterward and is not evidence that it existed in the model's initial request.

The first failed Ready evaluation called `stay_silent` and recorded this reason:

> The immediately previous request explicitly asked for silence for that message only; it does not
> apply to this new request.

Despite recording that distinction, it selected the silence tool and delivered no reply. A second
Ready request also called `stay_silent`. This is still an end-to-end failure, but the evidence does
not support attributing it to the old history-merge bug. Investigate actual tool selection, system
instructions, historical outcome metadata, and the prompt supplied in this richer conversation.
Do not hide it by forcing all user messages to receive an answer: deliberate silence remains valid.

Turn IDs:

- First failed Ready: `dffe1216-cce1-420d-b2c4-98ed6096182a`.
- The repeated failure is recorded in `backend-evidence.json` and `browser-observations.json`.

Screenshot: `remaining-silence-failure.jpg`.

## Timings

Browser values measure click Send to observed disappearance of Stop, including automation observation
and polling overhead. They are not provider-only latency or precise first-token measurements.
Backend values are the mind evaluation's `timings.total_ms`; queue wait before evaluation is excluded.

| Scenario | Browser observed completion | Backend evaluation | Result |
| --- | ---: | ---: | --- |
| Simple one-message silence | 3.937 s | 3.474 s | Passed |
| Ready after simple silence | 3.130 s | 2.700 s | Passed |
| Schedule thirty-second check-in | 4.157 s | 4.018 s | Passed |
| Thirty-second due wake | Not instrumented | 2.262 s | Delivered |
| Fresh clock and Markdown | 2.069 s | 1.915 s | Passed |
| Schedule exact local-time check-in | 2.687 s | 2.507 s | Passed |
| Exact-time due wake | Not instrumented | 3.027 s | Delivered |
| Original cancellation and silence | 2.671 s | 2.071 s | Deliberate silence |
| Ready after original wording and reload | 2.269 s | 2.014 s | Failed: silent |
| Repeat Ready | 2.664 s | 2.391 s | Failed: silent |

This is a small functional sample, not a load test or a reliability percentage.

## Regression validation

- Backend focused run (`test_mind_when`, `test_mind_runtime`, `test_learning_assessment`):
  **34 passed, 18 skipped** without TEST_DATABASE_URL.
- Re-ran `test_learning_assessment` and `test_mind_runtime` with TEST_DATABASE_URL pointing to local
  PostgreSQL; their fixtures created and removed disposable schemas: **39 passed**, 102.21 seconds.
  Includes the mixed-reference/notes sweep regression. These runs overlap; do not add their pass counts.
- Frontend `inlineMarkdown.test.tsx`: **5 passed**, including safe HTML escaping, link compatibility,
  emphasis/code rendering, literal user text, and ordinary asterisks/underscores.
- No browser warnings/errors were returned in the inspected console window.
- `git diff --check` passed.

## Scope limits and evidence

Eight typed messages and two autonomous wakes were exercised. Voice/audio, desktop/game actions,
owner-only panels, worker-restart recovery during a pending reminder, concurrency, and load were
not tested. Browser reload is not worker restart. Independent mouth generation was not exercised
as a separate long-delayed stage.

Evidence files:

- `browser-observations.json`: prompts' observed UI states and completion timings.
- `backend-evidence.json`: scoped test-account captures, evaluation receipts, schedules, and timing traces.
- Four screenshots named above.

No unrelated conversation/memory data was deleted or exported. The test account's records were retained.
