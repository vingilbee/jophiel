# Shared-state refactor: step 3 implementation report

> **Superseded 2026-10-22** by the legacy cleanup (`LEGACY_CLEANUP_2026-10-22.md`): the shared-state pipeline is the only reply preparation, `SHARED_STATE_PIPELINE_ENABLED` and the old preparation branch are gone, and Cognitive V0, its shadow and dialogue memory are retired. The cutover criteria and removal plan below were explicitly superseded by that cleanup, not met. Kept as the record of what was built.

Status: implemented 2026-10-21 behind `SHARED_STATE_PIPELINE_ENABLED` (off by default). Not audited, not tried
live. Spec: `SHARED_STATE_REFACTOR_SPEC.md`. This report covers step 3 (with the step-2 contracts and adapters
it needs) and nothing beyond it. **Steps 4, 5 and 6 are outstanding.**

The target is **one** shared-state preparation pipeline feeding the existing mouth. The flag is a temporary
migration and rollback switch, not a second architecture. The old preparation branch is removed once the
cutover criteria below pass, and not before.

## What a turn does with the flag on

```text
user message (persisted, ChatJob queued: unchanged)
  -> scoped view   app/services/shared_state.prepare
       conversation: revision, message refs, derived notes
       memory: the last answer's references, re-read now   (no search before the reply)
  -> cognition     Student 1 reads the message, in every A/B arm
  -> route         app/ai/state/routing.route (pure)
  -> mouth         the same orchestrator, tools and streaming; her prompt gets the cached
                   memories and "What you had for this message"
  -> trace         var/shared_state.jsonl: one line per turn
```

The flag is checked in two places, the only two reply-preparation points: the chat worker
(`workers/chat._generate`) and the local WebSocket fallback (`api/routes/chat.run_turn`). Everything after
preparation is shared and unchanged. With the flag off, a turn runs the old preparation. A test checks that the
same events stream, that no new prompt section appears, and that no trace is written.

## What is complete in step 3

| Piece | Complete? | Where | Evidence |
|---|---|---|---|
| Per-turn state assembly | Yes, for the minimum slice (envelope, conversation, memory context, cognition, thinking decision, work, output) plus personality and health. Inner state, activities, situation and mind are explicit `disabled`/`unavailable` sections | `ai/state/contracts.py`, `services/shared_state.prepare` | `test_shared_state.py` (assembly with stub adapters) |
| Cached-memory access | Yes. The last answer's references are re-read through memory's own read path: corrections and completions show, deletions are counted, another user's rows are never read. The receipt supplies them again, so follow-ups keep working | `recall.reread`, `recall_references.cached_for_reply` | `test_shared_state_cache.py` (real Postgres) |
| Student interpretation | Yes, for what Student 1 outputs (acts, tone, request kind), in every A/B arm. Its absence or failure is a degraded turn | `cognitive_live.prepare` (`interpretation`), `shared_state._cognition` | `test_shared_state.py`, `test_shared_state_worker.py` |
| Integration with the existing mouth | Yes. Same orchestrator, tools (recall tools included), streaming, TTS, barge-in, persistence and receipts. Only its context differs. A `route` frame goes on the turn stream | `workers/chat.py`, `api/routes/chat.py`, `prompts.py` | `test_shared_state_worker.py` (real `_generate`, stubbed model) |
| Routing table | Partly. The table and its outcomes are implemented and tested, but no component produces "evidence missing" or "thinking required" yet | `ai/state/routing.py` | `test_shared_state.py` |
| Trace | Yes. One line per turn, linking event, dependencies, cognition, route, eligibility, work and delivery; ids, labels, counts and timings only | `shared_state.finish` | trace test checks that no message or memory text appears |

## Unassessed vs confirmed fast path

Student 1 has no output for "needs older memory" or "needs careful thinking", and there is no thinking decider
yet. Those two requirements are therefore **unknown**, and the router keeps unknown apart from "no":

| Route | Eligibility | When | Executed in step 3 |
|---|---|---|---|
| `fast_path` | `confirmed` | Both requirements were assessed and found absent | Mouth on the context in hand |
| `unassessed` | `unassessed` | No known requirement, at least one not assessed | Mouth on the context in hand; the limits are told to her and traced |
| `recall` / `mind` / `recall_then_mind` | `not_eligible` | A requirement is known | Not wired: degraded (`<route>_not_wired`); she's told she can't do that properly right now |

On today's components **every turn is `unassessed`**, and no turn is reported as fast-path eligible. The trace,
the `route` frame and `RouteDecision` all carry `route`, `eligibility` and `outcome`, and `outcome` is
`fast_path` only for a confirmed route. A turn whose cognition is unavailable is `degraded` whatever its route.

What reaches the mouth on an unassessed turn: "Memory was not searched for this message", then one sentence per
unknown: "Nobody checked whether this message needs anything older than what you have." and/or "Nobody checked
whether this message needs careful thinking." The two are independent. If one is decided (for example
thinking assessed as not needed), only the other is said, so the text never contradicts a decision (tested in
both mixed cases). She also gets the rule to say what she couldn't check rather than filling the gap. The only after-the-fact signal that the context in hand was not enough is her
calling a memory tool: `mouth_escalated: ["searched_memory"]`.

**Training requirement (reported, not built):** confirming eligibility needs an evidence-need output, either on
Student 1 (new teacher labels and retraining) or from a separate assessor, plus Jev or an explicit rule for
thinking. That's step 4. Until then, the `fast_path` route can only occur in tests.

**A/B isolation:** with the flag on, the student reads in both arms, and its interpretation always reaches the
route. The arm decides only whether the reading's words reach the prompt (`cognitive_reading`). The boundary
section is identical in both arms.

## Contracts and owners (step 2)

`app/ai/state/contracts.py` is pure. Each section carries a `Projection` header: `owner`, `status`,
`version`, `updated_at`, `source_refs`, `reason`.

| Section | Status in step 3 | Owner, version source |
|---|---|---|
| Envelope | filled | `conversations.last_message_sequence` is the view's revision; the trigger is `message:<id>` |
| Conversation | filled | chat: message ids and sequences, newer-than-trigger count; notes: cognitive notebook (derived, kept apart from messages); summary `unavailable` (no owner exists); commitments point at memory_manager |
| Memory context | filled | memory: `recall.reread` gives each record's fingerprint, applicability and corrected flag; `gone` counts deletions |
| Cognition | filled | Student 1 (`simple-cognition-v1`, weights dir + mtime). Action `respond` and preference `speak` are policy, labelled `origin: policy` |
| Thinking decision | `disabled`, `required=None` | Jev is step 4 |
| Work / Output | filled | chat job id, attempt, lease, cancel key; or the socket's in-process turn |
| Personality | filled when `COGNITIVE_LIVE_CHARACTER` | the character page's content hash |
| Health | filled | settings plus section status |
| Inner state, activities, situation, mind | `disabled` / `unavailable`, with the reason | later steps |

There is no global per-user revision counter yet. A view is identified by its conversation revision plus the
input versions it records (`SharedView.dependencies()`).

## Degraded behaviour

| Cause | What happens |
|---|---|
| `COGNITIVE_LIVE_STUDENT=off`, or Student 1 fails | `cognition_unavailable`: the turn still answers. She's told her reading failed and to ask if the message is unclear; the `route` frame says `outcome: degraded`; the trace records it |
| Cached references can't be read | memory `unavailable`; she's told not to say nothing is recorded |
| A cached memory deleted or changed since | `memory_cache_changed`; changed records are re-read fresh, deleted ones are counted from the receipt |
| `RECALL_ENABLED` off | memory `disabled`; she's told she has the recent conversation only; typed memory says "not looked up for this message", never "nothing relevant" |

The frontend ignores the `route` frame: there's no UI for a degraded or unassessed turn yet.

## Guarantees (restart, scope, cancellation)

- **Restart / duplicates:** no new durable state. The reply's ownership is still the chat job lease plus the
  one assistant row per turn (`uuid5(turn, "assistant")`). A reclaimed job rebuilds the view from the stores.
  Re-reading memory is idempotent.
- **Scope:** every adapter filters by the job's user and conversation. Cached references come only from this
  conversation's previous answer, and memory's `_read_one` rejects other users' rows (tested).
- **Cancellation / interruption:** unchanged. Barge-in, the cancel key, heard spans and the lease are the
  existing code. The trace records the delivered outcome (`completed` / `partial` / `cancelled` / `failed`).
- **Supersession:** recorded, not acted on. `newer_than_trigger` counts messages committed after the trigger.
  Cancelling superseded work is step 6.

## Cutover criteria (the old preparation branch is removed only when all hold)

The old branch runs a fresh search before every reply. The new one doesn't, and today it can't tell which
messages need one. Removing the old branch now would mean that questions about the past depend on her choosing
to use a tool. So:

1. **Evidence need is assessed and conditional recall is wired (step 4). Required, no substitute.** A trained
   or evaluated signal decides, per message, whether recall is required, and the `recall` route fetches that
   evidence before the mouth answers. A keyword rule does not count. Evaluating her tool use (whether, on
   unassessed turns, her own memory tool calls match pre-reply search on the memory question sets in
   `docs/reports/memory-history-e2e-*`: Lantern, Juniper, corrections) can support an **interim experiment**,
   such as running the pipeline with the flag on in dev. It does not qualify for final cutover, because a turn
   whose memory need is unknown can never be a confirmed fast path.
2. **Thinking need is assessed or explicitly ruled.** Jev or an explicit, tested rule is wired. With criterion 1
   that makes confirmed `fast_path` turns possible: `unassessed` becomes the exception, its rate reported from
   the trace and agreed before cutover.
3. **Conversation quality holds.** `docs/COLUMBINA_CONVERSATIONAL_TEST_PLAN.md`, run with the flag off and on,
   shows no regression in relevance, naturalness or continuity (greetings, topic changes, corrections,
   follow-ups).
4. **Latency holds.** `scripts/voice_latency_report.py`: first-token p50/p95 on spoken turns no worse than the
   old branch.
5. **Degraded turns are rare and visible.** Student 1 is on in every environment (it's required, not
   optional), `cognition_unavailable` stays under an agreed rate, and the UI shows degraded turns.
6. **Every channel is covered.** Web text, voice (with barge-in), the WebSocket fallback and WhatsApp have each
   been run with the flag on, with no lost or duplicated replies and no trace write failures.
7. **Spec acceptance rows for steps 3–4 pass:** greeting, valid cached memory, personal history with absent
   evidence (no results vs unavailable), cognition or recall failure, memory correction after cache creation.

## Removal plan

1. When the criteria pass, change the flag's default to on. Keep the flag for one release as the rollback.
2. Delete the old branch in `workers/chat._start_setup_tasks` / `_generate` and in `api/routes/chat.run_turn`:
   the pre-reply `recall_references.for_reply` / `memory_service.retrieve_for_prompt` calls and the legacy
   `cognitive_live.prepare` call. Keep both functions: `mind_hard` still uses them, and `for_reply` becomes
   the `recall` route's implementation in step 4.
3. Remove `context.recall` as a pre-reply block if step 4's recall route renders its own.
4. Remove the flag and its `enabled()` checks. Note in CLAUDE.md that the pipeline is the only preparation.

Until then, both branches stay, and changes to reply preparation go into the new one.

## Test results (final run, 2026-10-21)

Full backend suite against a throwaway Postgres (`TEST_DATABASE_URL`; `TEST_REDIS_URL` unset, so those tests
skip): **3200 passed, 19 failed, 10 skipped** (14 min 40 s). The new tests and every memory/recall/correction/
observation suite pass. The 19 failures are not caused by this change:

| Failing | Count | Why it's unresolved, not caused here |
|---|---|---|
| `test_realization_v0_shadow.py` | 12 | Fails the same way on a clean export of the last commit (legacy V0 shadow; stale replay digests, likely from the local `.env` shadow provider settings CLAUDE.md warns about) |
| `test_cognitive_shadow_replay.py::test_database_replay_is_read_only_and_user_scoped` | 1 | Same: fails on the clean export |
| `test_dialogue_memory_pipeline.py::test_v03_capture_end_to_end_…`, `test_personality_runtime_v0_shadow_integration.py::test_turn_time_projection_…` | 2 | Same `version_unavailable` replay error as the row above (V0 shadow); not rerun on the clean export |
| `test_cognitive_shadow_chat_isolation.py::test_shadow_output_never_reaches_events_prompts_or_tools` | 1 | Bans the word "cognitive" from the context's repr, which has held `cognitive_reading` since before this change |
| `test_game_planning.py` (keep / wait_for_user / failed call) | 3 | Order-dependent flake: passed in the first full run and twice in isolation (14/14); none touch this code |

None of these are fixed here: V0 is out of scope per the spec, and the flake needs its own investigation.

New tests: `tests/test_shared_state.py` (routing, eligibility, prompt, assembly, trace),
`tests/test_shared_state_cache.py` (cache re-read against real memory),
`tests/test_shared_state_worker.py` (real `_generate`: an unassessed turn, a degraded turn, flag off).

## Memory work preserved

Built on the uncommitted source-backed recall changes, which are untouched apart from additions:
`recall.reread` and `recall.render_records` (new public reads), `recall_references.cached_for_reply` (new), and
`prior_references` split into `_prior`, which also returns the deleted count. Its result and callers are
unchanged.

## Outstanding

- **Step 4:** evidence-need and thinking assessment; conditional recall and Jev/mind routing; acknowledgement
  and silence; bounded rerouting.
- **Step 5:** thought-result cognition and shared speech arbitration. Quick thoughts, hard questions, planner
  speech and `agent_say` still deliver directly.
- **Step 6:** cancelling superseded reply work; interruption, restart, duplicate-delivery and stale-result tests
  across the new routes; architecture map updated to shipped behaviour.
- Not in any step yet: nudges and background game turns using the view; a UI for the `route` frame.
