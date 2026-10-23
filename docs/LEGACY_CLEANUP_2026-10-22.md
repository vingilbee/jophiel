# Legacy cleanup (2026-10-22)

Branch `chore/cleanup-legacy-paths`, from `256fc61` (shared-state step 7). This removes two kinds of code:

1. **Old execution paths** that shared-state steps 1–7 replaced: duplicate reply preparation, the flag that chose
   between them, and the speech paths that bypassed common delivery.
2. **Cognitive V0**: the retired cognition stack, its live shadow, dialogue memory (its only reader was the
   shadow), the older perception/classifier and its "Cognitive test" UI, and their scripts and tests.

It changes no cognition capability. Decisions that cognition can't make yet remain known defects (listed under
"Behavior after cleanup"). No model was trained, no teacher was added, and nothing substitutes for them.

## 1. Reference audit

Every import of a removed module, outside the module itself, was classified before deleting it.

| What | Who used it | Verdict |
|---|---|---|
| `Cognitive/system/v0/*` (pipeline, two-call, Realization, Goal, Requirement Resolution, Workspace, personality mapper, appraisal, dialogue-memory contracts) | `services/cognitive_shadow.py` + its worker, `services/dialogue_memory.py` + its worker, 9 V0 scripts | Retired: shadow-only, no live reader |
| `live_shadow.models.GenerationPath` / `ShadowSourceChannel` | chat worker, WebSocket route | Only to tag shadow captures; removed with capture |
| `services/dialogue_memory.py` | scheduled from every reply path, nudges, `agent_say`, WhatsApp link + delivery statuses; cache purge on conversation/account deletion and memory reset; step 7 component projection | **Only reader was the cognitive shadow** (`turn_context`). No live consumer, so retired with its producers, worker and hooks, not moved |
| `Cognitive/system` perception/classifier + `llm.py` | `POST /api/cognitive/inspect` and the Chat page "Cognitive test" panel only | Retired (the user's choice) |
| `chat_messages.conversation_sequence` (trigger, `last_message_sequence`) | recall, observation, coordinator, autonomous speech, shared state | **Still required.** Lives in `models.py` and the migrations, not V0. Its tests moved to `tests/test_conversation_sequence.py` |
| Shadow and dialogue tables (6) | nothing after this change; no other table references them | Dropped by migration `e4c8a2f6b9d3` (follow-up, the user's decision), see section 3 |
| Historical migrations | import only `app.db`/`app.core` | Unaffected; `alembic upgrade head` from empty and the a7c4e9b2d1f3 round trip both pass |
| `recall_references.for_reply`, `long_term_memory.retrieve_for_prompt` | the `recall` route (`shared_state._search`), coordinator recall work, `mind_hard`, recall tools | Kept. Only the *pre-reply* calls were removed |
| `agent_say.deliver` | background `say` / `offer_to_learn` tools, `game_correction` | Removed; see section 2 |

Old execution paths found and removed:

- **Reply preparation**, in both reply paths (`workers/chat._generate` and the WebSocket fallback `api/routes/chat.py`): three
  branches chosen by `SHARED_STATE_PIPELINE_ENABLED` / `RECALL_ENABLED`. Only `shared_state.prepare` remains.
  `workers/chat._start_setup_tasks` (pre-reply search + shadow capture) is gone.
- **The flag** `SHARED_STATE_PIPELINE_ENABLED`: `shared_state.enabled`, `coordinator.enabled`, the
  `autonomous_speech.submit` "pipeline disabled" rejection, the mind/recall component switches, and the conditional
  coordinator thread. The coordinator thread now always starts.
- **Direct speech**: `agent_say.deliver` saved to *the user's latest conversation* and published at once, bypassing
  per-conversation arbitration.


## 2. Behavior after cleanup

| Situation | Before (flag off) | Now |
|---|---|---|
| Any reply | fresh memory search + `cognitive_live.prepare` before the mouth | shared view only: the previous answer's memories re-read, Student 1's reading, route frame on the stream |
| Memory search before a reply | every turn | only on the `recall` route, i.e. only when `SHARED_STATE_ASSESSMENT_ENABLED` (Jev) says recall is required. **It is not enabled by this cleanup**: `.env.example` and the default keep it off, and the local `.env` doesn't set it, so every turn is `unassessed` and nothing is searched before the reply. Her recall tools still work |
| Turn where cognition can't run | normal reply | visibly degraded turn (route frame, boundary text, trace), as in step 3 |
| Finished background thought / autonomous proposal | n/a | unchanged from step 5: result cognition is unsupported, so `blocked`, kept, not said |
| Game correction ("Oh wait, it just worked") | said at once via `agent_say` | proposed via `autonomous_speech.submit` → **blocked by unsupported result cognition, so silent today**. This is the existing cognition limitation, not a cleanup defect; no bypass was kept |
| Background `say` / `offer_to_learn` | saved to the latest conversation, said at once | **delivery tool, not a proposal**: the mouth already chose the words, so no second cognition/mouth pass. `autonomous_speech.deliver_worded`: the session's own conversation (no latest-conversation fallback), restraint checked when queued and again at delivery, a `deliver` chat job that waits behind any reply in that conversation (one job per conversation), dropped if the user speaks first, receipt in `coordinator_outputs`, announced through the follow-up outbox. Needs the chat worker running, like follow-ups. The tool now answers "Queued to say: ..." |
| Nudges (salience rules on the socket) | mouth writes, socket saves | unchanged (not replaced by steps 1–7; only its dialogue-memory hook went) |
| Conversation / account deletion, memory reset | also purged dialogue-memory cache keys | nothing extra (the retired tables are dropped). The optional Redis dialogue cache (off by default, TTL ≤ 1 h by default) is no longer purged and expires on its own |
| WhatsApp delivered/read/failed | scheduled a dialogue-memory "delivery settled" job | only marks the observation sweep, as before |

Known defects that remain, unchanged and not hidden: result cognition (respond/ask/defer/silent for finished
thoughts and autonomous proposals) and the immediate decision while thinking (`policy_fallback` respond) are
unsupported by the student; Jev's quality is unevaluated (thresholds still PROPOSED).


## 3. What was removed, moved and kept

**Removed** (in git history from `256fc61`): `app/ai/Cognitive/system` (294 files, incl. V0 docs and data),
`services/cognitive_shadow.py`, `services/dialogue_memory.py`, `workers/cognitive_shadow.py`,
`workers/dialogue_memory.py`, `api/routes/cognitive.py`, `schemas/cognitive.py`, the frontend
`CognitiveInspector` panel + its API call and types, 10 V0 scripts (`appraisal_comparison`, `cognitive_*` evals,
canaries, latency analyses, `cognitive_continuity_eval`), the settings `COGNITIVE_LIVE_SHADOW_*`, `COGNITIVE_SHADOW_*`,
`COGNITIVE_DIALOGUE_*`, `COGNITIVE_MODEL`, `SHARED_STATE_PIPELINE_ENABLED` (from `config.py` and `.env.example`;
`extra="ignore"`, so an `.env` that still sets them keeps working), and `agent_say.deliver`.

**Tests**: 115 V0-exclusive files deleted (the shadow, V0 stages, dialogue memory, inspector, conditioning eval).
Kept and repointed: every live test that borrowed fixtures from `cognitive_shadow_support.py` now uses
`tests/db_support.py` (`database`, `db_env`, `create_user`, `create_conversation`); `live_turn` moved to
`tests/chat_turn_support.py`; `test_voice_barge_in.py` was kept (it only patched shadow capture). Moved:
the message-order tests to `tests/test_conversation_sequence.py`. Replaced: the `agent_say.deliver` tests now
exercise common delivery (incl. new: words wait behind an in-flight reply, are dropped if the user speaks first, and
never land in another conversation). Removed: two tests of the flag itself (`flag off is unchanged`,
`_start_setup_tasks`); the no-search guarantee stays covered by `test_shared_state_worker.py`.

**Tables dropped** (follow-up the same day, the user's decision): migration `e4c8a2f6b9d3` drops
`cognitive_shadow_jobs`, `cognitive_shadow_traces`, `cognitive_dialogue_memory_snapshots`, `_heads`, `_jobs` and
`cognitive_dialogue_interpretations`, plus their 4 guard functions; their stored data is deleted for good. Its
downgrade recreates them empty from the exact schema of a database at `b3e7f9a1c5d2`, so older migrations still
downgrade through it. The ORM models are removed from `models.py`. The message-order functions
(`chat_messages_assign_sequence`, `chat_messages_position_guard`), created by the same old migration as dialogue
memory, are untouched. Apply with `uv run alembic upgrade head`.

**Kept on purpose**: all historical migrations;
`docs/qa/*` and `docs/reports/*` (dated evidence; some reference removed code and their scripts no longer run);
`MIND_CONVERSATION_SHADOW_ENABLED` (the mind's conversation source, unrelated to V0).

**Docs**: CLAUDE.md (structure, a "Message order" and a "Retired" decision entry replacing the 11 V0/shadow
entries, the shared-state entry, speech mentions), `.env.example`, `frontend/README.md`, superseded notes on the
step 3/4/5 reports and the unbuilt Cognitive Turn Lens brief, two design docs.


## 4. Verification

All runs used a disposable PostgreSQL 16 cluster (UTC, its own port, `TEST_DATABASE_URL` and `DATABASE_URL`
both pointed at it), `REDIS_URL` empty, no model calls, no application data.

| Check | Result |
|---|---|
| Backend suite before any change (`256fc61`) | 3,282 passed, 22 failed, 10 skipped. 20 failures were in V0/shadow tests (deleted here); 2 in live tests: `test_auth_sessions::test_logout_cancels_streaming_reply` (didn't expect the pipeline's `route` frame, which the local `.env` already turned on) and `test_game_moment::test_a_quick_thought_the_game_moved_past_is_not_said` (asserted a `said` field removed in step 5) |
| Backend suite after | **1,028 passed, 0 failed, 10 skipped.** Both live failures fixed as cleanup work (the route frame is now unconditional; the thought test now asserts nothing is proposed) |
| Skipped | the same 10 as before: `test_desktop_input`, `test_inner_state_live` need `TEST_REDIS_URL` (no Redis server installed here) |
| Migrations | `alembic upgrade head` from an empty database; the message-order round trip (upgrade to `a7c4e9b2d1f3`, backfill, head, downgrade, head) passes |
| Drop migration | `tests/test_retired_tables_migration.py`: at `b3e7f9a1c5d2` the 6 tables and guards exist; `e4c8a2f6b9d3` removes them and keeps the message-order functions; downgrading restores exactly the same columns, constraints, indexes and triggers; `head` again removes them |
| `alembic check` | proposes nothing for the cognitive tables at head (dropped in both the models and the database). It does report older index-name drift on WhatsApp/`chat_jobs` tables, which predates this branch |
| Startup | `uvicorn app.main:app` serves; `POST /api/cognitive/inspect` → 404; `python -m app.workers` runs every thread, including the always-on coordinator, without errors |
| Frontend | `bun run build` passes; `bun run lint` shows only the 3 existing warnings in unrelated hooks |
| Lint | no unused imports or undefined names introduced (`ruff` F401/F841/F821 counts per changed file equal or below HEAD) |

Note: `tests/test_background_system_e2e.py` rewrites two dated files in `docs/reports/` whenever it runs; they
were restored, not committed.

