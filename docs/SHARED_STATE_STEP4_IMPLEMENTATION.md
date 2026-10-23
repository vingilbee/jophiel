# Shared-state refactor: step 4 implementation report

> **Superseded 2026-10-22** by the legacy cleanup (`LEGACY_CLEANUP_2026-10-22.md`): the shared-state pipeline is the only reply preparation, `SHARED_STATE_PIPELINE_ENABLED` and the old preparation branch are gone, and Cognitive V0, its shadow and dialogue memory are retired. Test tables below name V0 test files that no longer exist. Kept as the record of what was built.

Update 2026-10-22: live frontend findings are recorded in
`reports/shared-state-step4-e2e-2026-10-22.md`. The cached-memory input defect found there is fixed:
cache entries carry the re-read record content directly into Jev's request, alongside reference,
applicability and correction flags, rather than selecting metadata bullets from the mouth's rendering.
Evidence is bounded to eight entries of 1,500 characters each. Regression tests cover all four memory
kinds, the isolated Cobalt reading-circle case, clipping, corrections and empty caches. Focused validation:
31 passed, 3 worker integration tests skipped without `TEST_DATABASE_URL`. Jev is mocked in these tests;
live routing has not been rerun after this fix. The other live findings remain open.

Status: 2026-10-21. Spec: `SHARED_STATE_STEP4_SPEC.md`. **Every part of step 4 is built and connected as
designed, and tested with scripted (stubbed) decisions.** Two things are not done, and they are why step 4
isn't production-ready:

- **Cognition can't yet make the two decisions around thinking.** The trained student has no inputs or outputs
  for them (table below). Both go through explicit cognition interfaces, which today answer "unsupported". With
  the real student, that means the immediate reply takes a labelled fallback, and **finished thoughts are
  `blocked`, not spoken**. Adding the capability is a cognition change, outside this session.
- **Jev's decision quality is unevaluated** (a validation gate, not a build prerequisite).

Behind `SHARED_STATE_PIPELINE_ENABLED` (plus `SHARED_STATE_ASSESSMENT_ENABLED` for Jev). Not audited, not tried
live, no real model calls in tests. No teacher or extra model is used anywhere.

## The flow as connected

```text
user message → shared view → Student 1 (interpretation) → Jev (recall? thinking?)
  │
  ├─ neither          → mouth                                   (fast_path, confirmed)
  ├─ recall only      → memory search → mouth                   (recall)
  └─ thinking (± recall)
       ├─ coordinator commits work: [recall] → mind              (coordinator_work)
       ├─ cognition: acknowledge / partial / silent?  ── student: unsupported → fallback "respond"
       │    respond → mouth now, told the work is queued and unfinished
       │    silent  → no message, recorded as not sent           (coordinator_outputs, phase immediate)
       └─ coordinator worker: recall → hard.py mind → result
            ├─ newer user message?  → superseded, kept private
            └─ cognition: respond / ask / defer / silent?  ── student: unsupported → blocked
                 respond/ask → follow-up chat job → mouth → delivered (phase follow_up)
```

## Cognition decisions: what the student supports

Both decisions are explicit interfaces (`app/ai/state/decisions.py`, providers in
`app/services/cognition_decisions.py`). Only cognition answers them, and the coordinator never picks a
social behaviour itself.

| Need | Student 1 (live) | Student 2 (trained, `student_two.pt`, not loaded live) |
|---|---|---|
| Input: the user message with window, notes, memories, notice, mood | yes | yes |
| Input: "a thought of hers is pending" | **missing** | **missing** |
| Input: a finished thought (conclusion, uncertainty, evidence) | **missing** (the bundle's `message` is always the user's) | **missing** |
| Output: acts, tone, request kind | yes | reads them |
| Output: one main move among 20 groups (react_briefly, ask_about_it, …) | no | yes |
| Output: acknowledge / partial / silent | **missing** | **missing** (no silence or hold group) |
| Output: respond / ask / defer / silent for a thought | **missing** | **missing** |

Mapping an existing move (say `react_briefly` → acknowledge) would be a guess about semantics the student was
never trained on, so it isn't done. What happens today with the real student:

| Decision | Interface | Today | Recorded as |
|---|---|---|---|
| Immediate reply while thinking | `decide_pending` | unsupported → labelled fallback `respond`: the mouth replies, told the work is queued and has no conclusion yet. Silence is never chosen by fallback (it could orphan a request) | `coordinator_outputs` phase `immediate`, origin `policy_fallback`, `missing_capability` set |
| Finished thought | `decide_result` | unsupported → `blocked`: kept, not said | phase `follow_up`, decision `blocked`, `missing_capability` set |

Tests swap in scripted providers (`silent`, `respond`) to drive every branch end to end. Production has no
other provider. When the student gains these capabilities, these two provider functions are the only code that
changes.

## What's built

| Spec item | Built | Where |
|---|---|---|
| Jev: recall and thinking only | Policy v1: two questions, each `not_required` / `required` / `uncertain`; no clarification or tool choices. One call (verified: ~60 ms). Fixed policy, message and memory text sent as data, bounded input, 1.5 s timeout, no retry, never raises | `app/ai/state/assessment.py`, `services/jev_assessment.py` |
| Routes | `fast_path` (confirmed: valid cognition, both not required), `recall` (search before the mouth), `mind` / `recall_then_mind` (background), `unassessed` (unknown or uncertain, never read as "no") | `app/ai/state/routing.py` |
| Durable work | `coordinator_work`: kind, dependencies, statuses (queued, waiting_for_memory, running, completed, failed, cancelled, expired, superseded), cancellation generation, lease, deadline, idempotency key `request:kind`, result | migration `f1c3a5e7b9d2`, `services/coordinator.py` |
| Immediate notification | Work is committed and cognition consulted inside the turn's preparation, before the mouth. The mouth gets the real status ("queued, not started yet" / "waiting for a memory lookup"), never a claim of active thinking or a conclusion. It never waits for recall or the mind | `coordinator.schedule`, `decide_immediate`, `shared_state.boundary_text` |
| Explicit silence | A `coordinator_outputs` row (`not_sent`), no assistant message. The user's message still advances dialogue memory; the work stays tracked; WhatsApp gets nothing (no fabricated acknowledgement) | `workers/chat._finish_silent`; fallback path too |
| Recall dependency | The recall item runs memory's own reply search, and its status is kept (matched / partial / no_match / failed). The mind can't be claimed until the recall is terminal. Failed or expired evidence reaches the mind as such, never as "nothing recorded" | `coordinator._claim`, `_run_recall`, `_run_mind` |
| Mind | `app/ai/mind/hard.py` (`HardThinker`, `MIND_HARD_MODEL`), trigger `handoff`, with the message, Student 1's reading, up to 16 earlier messages and the evidence. Its private `thinking` is stored, never shown to the mouth | `coordinator._run_mind`, `_think` |
| Result acceptance | Accepted only if the worker still holds the lease and the cancellation generation is unchanged. A late result is kept on the row as a private outcome; its status stays `superseded` | `coordinator._accept` |
| Result cognition → follow-up | `decide_result`. respond/ask queue one follow-up chat job (`uuid5(request, "follow_up")`): same conversation and channel as the request, never "the latest". The output row and the job commit together; a second dispatch stops at the unique row | `coordinator._conclude`, `_dispatch_follow_up` |
| Follow-up delivery | The chat worker re-checks validity right before the mouth (no newer user message, not superseded). The mouth gets the thought as "You finished thinking" plus an internal trigger that is never stored. The stored reply is announced to the user's sockets (shown, and spoken on an idle live voice session), going through the mouth, not `agent_say`'s own path. A failed follow-up says nothing | `workers/chat._generate`, `_announce`, `prompts._THOUGHT_RESULT_PROMPT` |
| Serialization | Chat jobs already run one at a time per conversation, so a follow-up can't overtake or interleave with the immediate reply, or with another follow-up | `chat_service.claim_next_job` (existing) |
| Supersession | A new user message supersedes every unfinished item in its conversation (status plus generation); its outcome is recorded `superseded` and never sent | `coordinator.supersede_older`, `_conclude` |
| Deadlines and failures | `SHARED_STATE_WORK_DEADLINE_SECONDS` (180) ends work as `expired`; errors end it as `failed`. Both still reach result cognition with the failure, so a waiting user isn't left with silence nobody decided on | `coordinator.run_once` |
| Restart | Expired leases are reclaimed; scheduling is idempotent; phases are unique per request; the follow-up job's turn id and assistant id are deterministic | tested |
| Trace | Per turn: route, assessment, background work ids and kinds, immediate decision and origin. Per lifecycle: scheduled / output / finished / superseded / expired / follow_up_dispatched. Ids and statuses only, no message or thought text | `var/shared_state.jsonl` |

Data flow: with assessment on, each message, up to 6 clipped recent turns and the cached memory lines go to
TypeSafe.

## Not done, or knowingly limited

- **Cognition capability for both decisions** (above). Until then: fallback `respond` immediately, `blocked`
  follow-ups.
- **Uncertain thinking** stays `unassessed` and doesn't schedule thinking. The spec's fallback ("default to
  deliberate work when a coherent problem can be formed") needs a judgement of coherence that no component
  makes yet.
- **Mind asks for more evidence:** not built. `hard.py` returns one conclusion and has no evidence-request
  output. The budget (one recall round per request) is fixed by construction.
- **Re-assessment after recall** (e.g. contradictory sources): not built.
- **Defer:** recorded and kept private. There's no later re-offer yet (that's step 5's autonomous result
  route).
- **Voice "user is speaking" check at dispatch:** the socket's existing rule (speak a follow-up only on an
  idle live voice session) applies. There's no server-side speaking check before the follow-up job runs.
- **Local fallback path (no Redis):** routes, schedules and records the immediate phase. Follow-ups need the
  external chat worker and coordinator thread.
- **Frontend:** a silent turn ends with `done`/`silent` and no tokens, and there's no UI for it yet. Follow-ups
  appear like other unprompted messages.
- **Observation events:** lifecycle goes to the trace only. No existing observation kind has a consumer for it
  (spec: add kinds only for a concrete consumer).
- **Channels:** only the mechanics are tested (web path through the real chat worker). Voice, fallback and
  WhatsApp have not been exercised live.

## Tests

| File | What | Count |
|---|---|---|
| `tests/test_coordinator.py` (real Postgres) | work committed and mouth replies before the mind runs; scripted silence; blocked result with the real student; scripted respond → exactly one follow-up through the mouth (trigger not stored, private thinking not shown); recall → mind dependency and no_match evidence; new message supersedes; late result kept private; follow-up re-checked at dispatch; expiry reaches cognition; lost lease reclaimed and scheduling idempotent; trace has no text | 11 |
| `tests/test_shared_state.py` | routes for every Jev answer, failures, request shape, recall statuses, trace | 26 |
| `tests/test_shared_state_cache.py`, `tests/test_shared_state_worker.py` | cache re-read; real `_generate` | 5 |

`tests/test_cognitive_shadow_chat_isolation.py` now pins the old preparation with recall off, so a local `.env`
with `SHARED_STATE_PIPELINE_ENABLED` or `RECALL_ENABLED` can't change what it compares. Its word-ban test still
fails as before this work.

Full suite (2026-10-21, local `.env` has `SHARED_STATE_PIPELINE_ENABLED=true` and `RECALL_ENABLED=true`):
**3223 passed, 21 failed, 10 skipped.**

| Failing | Count | Cause |
|---|---|---|
| `test_realization_v0_shadow.py` (replay/outcome tests), `test_cognitive_shadow_replay.py`, `test_dialogue_memory_pipeline.py`, `test_personality_runtime_v0_shadow_integration.py` | 15 | The legacy V0 shadow baseline: they fail the same way on a clean copy of the commit before this work |
| `test_cognitive_shadow_chat_isolation.py` word ban | 1 | Baseline (the context has had `cognitive_reading` since before this work) |
| `test_auth_sessions.py::test_logout_cancels_streaming_reply`, `test_dialogue_memory_isolation.py::…byte_identical`, `test_realization_v0_shadow.py::test_realization_cannot_alter_chat_tokens…` (×3) | 5 | **The local `.env` flags:** all 5 pass with `SHARED_STATE_PIPELINE_ENABLED=false RECALL_ENABLED=false`. With the pipeline on, the turn stream gains a `route` frame where the auth test expects `state`; with recall on, the context's `recall_turn` carries its own time, so "identical turn" comparisons can't match. Not pinned (left for the user to decide, like the isolation file) |

The `test_game_planning.py` flake seen earlier didn't recur.

## Decisions (2026-10-21)

| # | Decision | Status |
|---|---|---|
| 1 | Background work in a new table | Decided (the user) |
| 2 | Acknowledge / partial / silent while thinking is cognition's decision, through the student, not a design rule | Decided (the user). Interface built; the student lacks the capability (table above) |
| 3 | The mind reuses `app/ai/mind/hard.py` | Decided (the user) |
| 4 | Thought-result cognition goes through the student's interface; no teacher calls (not approved) | Decided (the user). Interface built; the student lacks the capability, so results are `blocked` |
| 5 | Evaluation thresholds and labels | Proposed (`docs/qa/shared-state-assessment/`), awaiting the user's agreement before the run |

Steps 5 and 6 of the refactor and the later observation/maintenance integration remain outstanding.
