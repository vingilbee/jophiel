# Frontend end-to-end test — mind migration

Tested on November 13, 2026 (America/Vancouver), using the Codex built-in browser against
http://127.0.0.1:5173, local FastAPI, PostgreSQL, Redis, and the shared workers. Real model:
`gpt-6-luna`; no mocked chat or memory responses. An isolated local test account named
`Mind E2E` was created. Test messages used a fictional project, Lantern Finch.

## Migration

Successfully applied the pending migrations from `b4e8d2a6f1c3` through:

- `c5f9a2d7e3b1`: desktop action capture.
- `d8b3e6f1a4c7`: security activity.
- `a6d2f8c4e9b1`: text personality.
- `b7e2c9f4a1d6`: cognition retirement and transfer.

A database read confirmed `alembic_version = b7e2c9f4a1d6`.

## Latency and output

Backend times below are the mind evaluation's `timings.total_ms`, including preparation and
its model/tool chain. They are not provider-only latency or browser end-to-end latency, and
exclude any preceding queue wait. Browser figures were measured around clicking Send and
waiting for visible text / disappearance of Stop; they include automation observation overhead.
Only selected turns have reliable browser timings. The initial recall timing attempt had an
incorrect locator and is excluded from browser measurements.

| Scenario | Backend evaluation | Result |
| --- | ---: | --- |
| Short greeting/personality response | 1.664 s | “Hi! I’m curious why people choose the names they do for their pets.” |
| Remember fictional project fact | 3.400 s | Acknowledged Lantern Finch; called `set_goal`. |
| Recall fact in a fresh conversation | 4.365 s | Called `read_memory`; answered “Your fictional test project is called **Lantern Finch**.” |
| Schedule follow-up | 2.658 s | Called `come_back_to`; disclosed rounding 30 seconds to 1 minute. |
| First scheduled wake | 3.461 s | Failed requested behavior: rescheduled silently instead of asking the question. |
| Second scheduled wake | 1.804 s | Chose silence; no further wake scheduled. Cancellation was being requested around this time. |
| Cancel reminder and remain silent | 2.625 s | Called `drop_come_back` and `stay_silent`; UI displayed the silent status. |
| New “Reply with exactly: Ready.” after silence | 3.452 s | Failed: called the previous cancellation/silence tools again and remained silent. |
| Same minimal reply request in a fresh chat | 1.354 s | Correct output: “Ready.” |

| Browser measurement | First visible text | Completed |
| --- | ---: | ---: |
| Scheduling reply | 2.613 s | 2.830 s |
| Cancellation / explicit silence | Not applicable | 2.901 s |
| Minimal reply in a fresh chat | 1.439 s | 1.551 s |

This is a small functional sample, not a load test or percentile benchmark. Per-evaluation
records and browser observations are saved in `timings.json`.

## Confirmed working

- Sign-up and authenticated chat, with real streaming responses.
- Personality-aligned short output and exact short-output compliance in a clean conversation.
- A fictional fact persisted across a new chat; `read_memory` was executed by the mind before
  the correct answer, confirming an actual tool chain rather than only recent-chat recall.
- Conversation history survived browser reload.
- Scheduled wake persisted through browser reload and was processed without a new user message.
- Explicit silence produced a visible status without a normal assistant answer.
- At the final reminder check, both test wake records were completed and none remained queued.
- No browser warnings/errors were captured in the inspected console windows.

## Findings

### High: a requested follow-up is deferred again when its wake becomes due

The user requested asking whether Lantern Finch was ready. The tool accepted a one-minute wake,
and the browser was reloaded before it came due. The wake was due at `04:07:12.895633Z`; its
work row completed at `04:07:13.329391Z`, approximately 0.434 seconds later. The infrastructure
therefore dispatched promptly.

However, the mind evaluation invoked `come_back_to` again and produced no words. The resulting
wake was scheduled for `04:08:15.871412Z`. The original question never appeared in the browser.
The first wake's capture confirms an empty reply and a successful rescheduling tool result.

This is an observed behavior failure, not evidence of a broken scheduler or delivery transport.
The generic wake prompt allows reconsideration, silence, and deferral. Review how the due event,
original request, and current obligation are expressed to the mind. Preserve prompt-driven behavior;
do not repair this with an unconditional coded “always speak on a wake” policy.

### High: a one-message silence request carries into the next request

Reproduction:

1. Ask the mind to cancel reminders and stay silent for this message.
2. After that turn completes, send `Reply with exactly: Ready.`
3. Observe a second “She chose not to reply.” instead of Ready.

The later job payload correctly contained the new request, but `chat.get_history` supplied one
combined user turn containing the previous cancellation/silence instruction followed by the new
request. `backend/app/services/chat.py` merges adjacent messages with the same role. Since silence
stores no assistant message, consecutive user messages lose their separate turn boundary.

The model then called `drop_come_back` and `stay_silent` again; its capture reason referred to the
previous instruction. Starting a fresh chat and submitting the exact same Ready request worked
in 1.551 seconds in the browser. Review silent-turn representation and preservation of user-message
boundaries in model history. The prompt must be able to distinguish an old instruction scoped to
one message from the current request.

Screenshot: `chat-results.jpg`.

### High: learning-assessment worker repeatedly fails

Worker output repeatedly reported `IndexError: list index out of range` at
`backend/app/services/learning_assessment.py:368` during the assessment sweep. The code accesses
`r.split(":", 1)[1]` for every `awaiting` entry without guarding its format. The preceding attempt-ID
selection filters entries, but the final list comprehension does not.

The observed stack is consistent with a malformed or differently shaped awaiting reference;
its exact originating record was not inspected. Chat continued, but the assessment worker retried
with the same error during the run. Inspect supported reference shapes and legacy migrated data;
do not clear unrelated learning data to hide the error.

Startup also emitted embedding/import errors, including `KeyError: 'openai.resources'`, with lexical
fallback. Recall later succeeded. The startup import issue was not reproduced in isolation.

### Medium: profile shows the wrong person's name

The newly created account was named Mind E2E, but Chat displayed Vincent and the initial V.
`frontend/src/pages/ChatPage.tsx:556` and `:558` hardcode that name instead of using the signed-in user.
This is display behavior; no cross-account memory exposure was established by this test.

### Low: model Markdown appears literally

The recall answer showed `**Lantern Finch**` with asterisks in the bubble.
`frontend/src/components/DocumentAttachment.tsx` renders plain text plus selected attachment links,
so Markdown emphasis is not rendered. Align model formatting with the renderer or support the
intended safe formatting. Screenshot: `chat-before-wake.jpg`.

### Limitation: scheduling granularity is whole minutes

`come_back_to(what, in_minutes: int, ...)` cannot express 30 seconds. The response correctly
explained the change to one minute. Decide whether sub-minute timing is desired before treating
this as a defect.

## Boundaries

This was a text frontend smoke test with seven submitted messages and two autonomous wake evaluations.
Voice/audio, desktop/game execution, owner-only System/Mind panels, process-restart recovery,
concurrent workers, and load/performance percentiles were not tested. Browser reload is not equivalent
to restarting workers. No product source fixes were made in this test session; changes consist of
applying migrations and saving test evidence. Local API, frontend, and worker processes were started
for this run.
