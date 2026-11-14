# Mind continuity and wake context — follow-up handoff

Date: 2026-11-14 (America/Vancouver)
Status: agreed design, ready for implementation by the other agent. No application changes are made by this document.

## Scope and current baseline

The cognition removal has been implemented and the local database was migrated to `b7e2c9f4a1d6`.
Read `MIND_ARCHITECTURE.md` for the current runtime, rather than treating the original cognition
handoff's historical implementation status as current. Read `qa/mind-e2e-2026-11-13/REPORT.md`
and its timing records for observed failures. Preserve unrelated uncommitted changes.

Implement the decisions below. The aim is continuity of attention, intentions, commitments, and actual
outcomes across evaluations. Do not claim these engineering changes establish consciousness. Do not
reintroduce cognition, a separate interpretation model, numerical personality/mood, or a second behavior owner.

## 1. Give every wake the context needed to continue

Build a bounded, fresh context for every evaluation, including non-message wakes. Distinguish known
facts, missing information, and the mind's recorded intentions. Include relevant information, not an
unbounded dump of her entire state.

| Context | Required content |
| --- | --- |
| Now | Current date/time, timezone, evaluation timestamp, and elapsed time since the previous relevant evaluation. |
| Event | What changed, event ID, event time, and what actually caused this wake. |
| Purpose | Who initiated the intention, why it was created, and the original request/evidence reference. |
| Commitment | Whether she agreed to do something, what she agreed to, and whether it is fulfilled, revised, cancelled, or still open. |
| Importance | Urgency or consequences explicitly stated by the user, plus separately attributed priorities the mind chose to record. Do not invent a numerical importance score or present inferred urgency as stated fact. |
| Timing | Creation/scheduling time, actual deadline, current time, elapsed time, and lateness. A scheduling timestamp is not the deadline. |
| Continuing state | Relevant goals, current approach, intentions, questions, promises, and pending work. |
| Changes meanwhile | Relevant new messages, actions and outcomes, cancellations, and changed circumstances since the originating decision. |
| Outstanding need | Whether the task/question remains unresolved, and what evidence supports that status. Unknown is not unchanged. |

Supply the same clock reference to mind and any separate mouth/wording step. Refresh current time if
wording or execution happens later; retain original event and deadline timestamps. Store authoritative
times in UTC and render local times using the user's IANA timezone. Avoid host-local timezone assumptions.
A queued job's old awareness snapshot must not be the only clock input to a later wake.

Example rendered input (illustrative times):

```text
NOW
20:11:00, America/Vancouver.

EVENT
Your scheduled check-in is due.
It was scheduled at 20:10:00 for 20:11:00.

CONTEXT
The user asked you to ask whether Lantern Finch is ready.
You agreed to return after one minute.
This check-in has not been delivered.
No new user messages have arrived since you agreed.

CURRENT STATE
The project is still open.
No cancellation or conflicting instruction is recorded.
```

The implementation must derive these statements from actual records. Do not hardcode this example or
assert that no changes happened when they were not checked.

## 2. Replace the misleading generic wake instruction

The current wake prompt says the user is not waiting on her. That blanket statement is inappropriate
for a user-requested check-in. No new message arrived; this does not imply there is no expectation or
commitment. Express that distinction and give the specific event its context.

Use the following as the agreed prompt direction, adapting wording to the final tool names:

> You are continuing from your previous activity. The context below describes what changed, what you
> were pursuing, and what remains open.
>
> Consider what this event means for your current intentions and commitments. Decide what to do now
> using your available capabilities. You may speak, act, gather information, reconsider, wait, or
> remain silent.
>
> A due commitment is something you previously agreed to do; an optional reconsideration is something
> you wanted to think about again. Consider changed circumstances before carrying either forward.
> If you defer, revise, or abandon an intention, record that change and its reason. Distinguish what
> you intended from what actually happened.

Behavior remains prompt-driven. Do not implement a coded policy that always speaks on reminders,
forbids deferral, or automatically manufactures a reply. Avoid making optional thoughts feel like
user commitments. Preserve ownership, freshness checks, cancellation, and duplicate prevention.

## 3. Persist the intention through its lifecycle

Use a coherent lifecycle:

`intention -> event -> consideration -> action -> outcome -> updated intention`

- A fulfilled check-in requires an actual delivery outcome; model intent alone does not fulfill it.
- If she postpones, revise the same intention's time and record a concise reason. Do not leave an
  unexplained chain of unrelated reminder rows or duplicate live schedules for the same intention.
- If she drops or revises an intention, persist that decision and its reason.
- If execution fails, retain the actual failure and unresolved status for reconsideration.
- Preserve original purpose and originating evidence across deferrals and restarts.
- Keep concise decisions and reasons, not a mandatory long inner monologue or private reasoning transcript.
- Reuse existing working items and scheduler/work records where suitable; avoid a parallel notebook
  or duplicate source of truth. Audit existing identity, cancellation, and retry guarantees first.

A one-off reminder does not imply recurring permission to keep asking. The mind should have enough
context to decide whether a further attempt serves the original purpose. Mechanical safeguards must
prevent duplicate effects without deciding her social behavior.

## 4. Separate current input, history, and persistent state

The user approved this input design:

1. **Now:** fresh clock, wake reason, and clearly identified current message/event.
2. **Recent history:** separate timestamped messages and actual evaluation/delivery outcomes.
3. **Persistent state:** relevant open questions, promises, intentions, pending work, and schedules.

Preserve original user-message and turn boundaries. Do not merge adjacent user messages into one
undifferentiated instruction block. Silence means no spoken reply, not that the evaluation never
happened. Supply a clearly labelled internal outcome for that evaluation; do not fabricate assistant
speech to fill the gap. Keep such metadata distinct from user content and actual assistant messages.

For example:

```text
20:10:00 — User message A: Don't reply to this message.
20:10:02 — Evaluation A outcome: Mind chose silence; no assistant words delivered.
20:10:15 — CURRENT user message B: Reply with exactly: Ready.
```

Actual model messages need not be one JSON blob. Preserve native roles and tool-call/result matching,
with trustworthy metadata added by the application. Make the latest event unambiguous and keep
historical content separate. User text must not be able to forge authoritative application metadata.

Inspect `services/chat.py:get_history`, `ai/messages.py`, message schemas, and all consumers (chat,
voice, wakes, specialists). Timestamp/history changes must preserve attachments, heard portions of
interrupted voice replies, cancellation, durable ordering, and current recall/forgetting boundaries.
Do not leak internal outcome metadata into user-visible speech or rewrite the stored user transcript.

## 5. Fix the concrete learning-assessment reference bug

`services/learning_assessment.py:sweep` currently splits every `awaiting` entry on `:` and indexes the
second element. Producers also put explanatory text in the same list, and supported references can
point to different record types, such as attempts and executions. This is not merely corrupt data.

Separate machine-readable awaited references from explanations of missing/truncated evidence.
Validate reference type and identifier, and resolve supported types against their actual stores.
Remove a pending reference only when the expected evidence has actually arrived. Keep explanatory
notes visible without treating them as identifiers. Handle existing stored shapes deliberately;
do not erase unrelated assessments or learning data to make the sweep pass.

## 6. Related E2E findings

Also fix the hardcoded profile name/initial in `frontend/src/pages/ChatPage.tsx` using the authenticated
user. The current text renderer displays Markdown emphasis literally; align output formatting with
safe rendering of the desired basic Markdown, preserving existing file/editor links and attachment
previews. Do not enable arbitrary model HTML.

Scheduling currently exposes `in_minutes: int`. Proposed follow-up: support relative sub-minute
durations and exact local date/time requests, resolve to a UTC deadline, and return the actual
scheduled time. The user discussed this limitation but has not selected a specific tool schema;
choose a simple validated interface compatible with the agreed time/context design, without silent
rounding or invented timezone assumptions. Handle ambiguous/nonexistent local times explicitly.
Preserve one-off intent, cancellation, and late/restart behavior. Do not expand into an external
calendar integration or a new recurring-reminder product.

## Inspection starting points

- `backend/app/services/mind_turn.py` — fresh evaluation state and outcomes.
- `backend/app/services/coordinator.py` — scheduling, wake rendering, and follow-up payloads.
- `backend/app/ai/prompts.py` — shared wake instructions and context assembly.
- `backend/app/services/awareness.py` — existing local clock/session information.
- `backend/app/workers/chat.py`, `backend/app/api/routes/chat.py` — both evaluation paths.
- `backend/app/services/chat.py`, `backend/app/ai/messages.py` — history boundaries and conversion.
- `backend/app/ai/tools/mind/tools.py` — intentions, deferrals, silence, and scheduling tools.
- `backend/app/services/learning_assessment.py` — mixed awaited references and notes.
- `frontend/src/pages/ChatPage.tsx`, `frontend/src/components/DocumentAttachment.tsx` — profile/rendering.

## Verification and completion

Use meaningful regression tests and the built-in browser with real model/tool responses when available.
Use `uv` for backend work and `bun` for frontend work. Report unavailable infrastructure explicitly.

Required cases:

- A requested check-in has origin, commitment, purpose, deadline, current clock, and delivery state in
  the due wake. It produces the requested check-in in the unchanged straightforward test situation.
  Optional reconsiderations can legitimately produce silence; there is no unconditional coded reply.
- Deferral updates the same intention with a reason, without duplicate schedules. Delivery fulfillment
  is based on actual receipts. Cancellation and execution failure retain accurate state.
- Fresh clock/timezone context reaches both mind and mouth for messages and later wakes, including
  after queue delay or restart. Deadline and scheduled-at times are not confused.
- After one-message silence, a subsequent explicit reply request is a separate current message and
  is answered. Repeat across reload and model-history truncation boundaries.
- Each history item retains its timestamp/turn identity. Internal silence/outcome markers are not
  represented as things the assistant said. Tool loops and recall continue to function.
- Learning sweeps accept attempt references, execution references, explanatory notes, and unknown
  legacy shapes without crashing or incorrectly marking evidence as received.
- Non-owner profile reflects the actual signed-in user; formatting and attachment previews behave correctly.
- If sub-minute/exact-time scheduling is implemented, exercise 30 seconds, an exact local time, timezone
  conversion, cancellation, and late execution. Never silently promise a different deadline.

Retest the original E2E scenarios and save updated latency/output evidence. Do not declare success
based only on a successful scheduler dispatch: verify the resulting behavior and delivery in the UI.
