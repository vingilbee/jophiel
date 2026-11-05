# Post-audit 1 implementation: fill missing experience capture

Status: built 2026-11-04 on `feature/experience-capture` (migration `e7c3a9f1b5d2`, not applied to the dev database:
run `uv run alembic upgrade head`). Design: [POST-AUDIT-01-fill-missing-experience-capture.md](POST-AUDIT-01-fill-missing-experience-capture.md).
Foundation: [step 1 implementation](01-implementation.md). This page records what was built, the two checks the
design left open, and what is not covered.

## The two open checks

### 1. Which damage causes the installed stack exposes (verified on a real server)

Mineflayer 4.39.0, minecraft-protocol 1.68.0, minecraft-data 3.117.0, Minecraft 26.1.2, run against a real vanilla
server (the integration harness, `test/integration/trajectory.itest.js`, plus a one-off probe).

| Source | What it gives | Seen |
| --- | --- | --- |
| `damage_event` packet | `sourceTypeId` (index into the server's `damage_type` registry), `sourceCauseId` / `sourceDirectId` (entity id + 1, 0 = none), optional `sourcePosition` | `minecraft:fall`, `minecraft:lava`, `minecraft:drown`, `minecraft:mob_attack` (cause = the zombie's entity), `minecraft:magic`, `minecraft:generic_kill` |
| `registry_data` (configuration phase, before spawn) | the 50 damage type names in id order | all 50 keys, each with a value |
| `death_combat_event` | the server's death message as a translatable component | `death.fell.accident.generic`, `death.attack.mob` + `entity.minecraft.zombie` |
| `update_health` | health, food, saturation after each change | every change |

- **Mineflayer drops the cause.** Its `entityHurt` event keeps only the source entity and discards the damage type,
  and it doesn't parse the death message. The pack listens to the raw packets itself.
- **Listen before spawn.** The registry arrives before spawn, so the recorder attaches right after `createBot`
  (`src/pack.js`). An id the registry never named is recorded with `type: null` and says so.
- **No damage amount in the packet.** The amount is in the health updates that follow, recorded as their own events.
  Pairing a hit with a health drop is left to the reader. Order alone doesn't prove the pairing.

### 2. Overhead and projected storage (measured; settings follow)

Real 26.1.2 server, flat world, six cows within 16 blocks. Numbers from `var/trajectory-measure.json`, two runs of
`trajectory.itest.js`:

| | Bytes per minute | Per hour | 30 days, connected the whole time | Tick handler (mean / max) | Encode per chunk | gzip |
| --- | --- | --- | --- | --- | --- | --- |
| Activity (walk, mine, falls, lava, a zombie, a death) | 31–32 KB | 1.9 MB | 1.3–1.4 GB | 0.065 ms / 0.7–2.3 ms | 1.9–2.0 ms | ×3.1 |
| Standing for a minute | 23 KB | 1.4 MB | 1.0 GB | 0.066–0.073 ms / 0.7–1.3 ms | 1.1–1.7 ms | ×2.9 |

- **Tick cost.** A tick is 50 ms, so the handler's mean is about 0.13% of it. One run had a single 31 ms tick
  (probably garbage collection).
- **Encoding.** It runs off the tick path, asynchronously.
- **Idle is mostly entities.** Moving entities are most of the idle bytes.
- **A survival world will cost more.** It has more entities and block changes; these numbers are a floor.
- **Measure live.** `uv run python scripts/trajectory_report.py` reports the same figures from saved chunks: the rate
  per minute of recorded time, the 30-day projection both continuous and at the observed share of time connected, and
  the pack's overhead.
- **Settings chosen from this.**
  - Pack buffer: `PACK_TRAJECTORY_BUFFER_MB=64` (about 35–45 hours of play).
  - Writer's chunk bound: `LEARNING_CAPTURE_CHUNK_QUEUE_MAX=240` (about 20 minutes behind).
  - Retention: unchanged, 30 days.

  No numeric guarantee is made. Revisit after a week of real play.

## Who records what

| Owner | Records | Where |
| --- | --- | --- |
| Her planner | `planner` attempt: exact input text, system text and schema (by hash), the plan as read (revision + content), reasons, goal, lessons supplied (id, status, revision), model, settings, raw answer, parsed decision, error. One per call; a retried job's next call names the one before (`uuid5(job, "planner:<try>")`) | `services/game_planning.run` |
| Plan application | `plan_apply` execution: the outcome, plan revision before/after, the plan after, steps applied (unlisted steps kept, steps cut by the limit), each applied rule with where it came from (`proposed`, `user_memory`, `previous_plan`, `user_goal`), rules the code carried that the planner didn't propose, rules the limit left out. `deviated` when code applied anything not proposed; `not_executed` when dropped or skipped | `game_planning._capture_apply` |
| Hard thoughts | `hard_thought` attempt: rendered input, system text and schema, the problem (trigger, topic, query, anchor), messages and memory refs read, raw and parsed answer (kind, conclusion, thinking, tip, question, `follows`, assessment). Shadow sources aren't captured | `services/mind_hard.run` |
| Thought routing | `thought_route` execution: done / stale / dropped, the thought id, where it went (tip, intention, mouth) and what the body answered. `deviated` when her hands refused the tip | `mind_hard._capture_route` |
| Her hands | as in step 1, plus: the messages she was given (ids and text), the step itself, the plan's goal. Each `skill_run` keeps the pack's report (`result_from`) apart from `observed.before` / `observed.after` (her state read by code: position, health, food, held, armor, inventory, kills, with its time) | `ai/desktop/skill_driver.py` |
| Step counter | `step_check` execution: code counting a plan step done, with what it counted and her state then. No decision behind it | `skill_driver._step_counted` |
| The pack | her trajectory (below) | `game_packs/minecraft/src/trajectory.js` |
| Collection | pulls chunks, stream headers and reported drops into the writer | `services/trajectory_capture.py`, worker thread `trajectory` (desktop mode) |
| Persistence | chunk and stream records, their own queue bound, retention, forgetting | `services/learning_capture.py` |
| Reading back | decode, expand, holes, links, measurement | `services/trajectory.py`, `services/capture_chains.session` |

Every attempt now records `versions.code`: the repository commit, with `+dirty` when there are uncommitted changes.

## Her trajectory

**Frames.** On every `physicsTick` the pack records one frame with:

- position, velocity, yaw/pitch and on-ground;
- the controls held (bitmask), water and lava;
- health, food, saturation and air;
- hotbar slot, the block being dug, whether an item is in use;
- dimension and game mode;
- every inventory slot (armor and off-hand included);
- entities within 16 blocks.

**Events.** Each event has the tick it came after and its own time:

- Damage: server type, causing and direct entity, source position, health before.
- Health: health updates, the death message, death, spawn, respawn, kicked.
- World: block changes within 6 blocks, chat and system messages, her notices (with `seq`).
- Skills: skill start and finish (run id), mode holds.
- Programs: start with the code, finish with calls, actions and log.
- Commands: every API POST, with what was asked and answered.
- Actions: low-level actions such as dig, place, attack, equip, craft and pathfinder goals, each with how it ended.
- Perceptions: `perceived` when her state was read. The shape of the place is cached, so it counts as a
  perception only then. Full content is kept once per chunk, then referred to by digest.

**Chunks.** A chunk holds at most 100 ticks or 5 s, as JSON, gzipped:

- **Self-contained.** It starts from a checkpoint (the full state before its first frame), then deltas. Unchanged
  ticks are still frames, so every tick is reconstructable and any chunk can be read or deleted alone.
- **Ids.** One stream per connection, one run per process. `seq` per stream, ids `uuid5(stream, "seq:<n>")`.
- **Integrity.** The pack's hash of the bytes is checked on collection and on read.

**Honest gaps.** Mineflayer exposes no tick while she is dead, before her position arrives, in an unloaded chunk, or
for ticks its catch-up cap drops. Each frame carries its own time, so such a stretch shows as time without frames.
Unknown values are `null`, never 0. What isn't recorded is listed in the declared `coverage`: server simulation
state, anything beyond the radii, chunk loads, sounds, particles.

**Clocks.** Chunk times are the pack's clock; attempts and executions are the backend's. Reconstruction keeps them
side by side and links them by ids (run id, program id, notice seq), never by merged time.

## Collection and failure behaviour

- **Pack side.** The pack keeps a ring of at most 64 MB / 4000 chunks. `GET /trajectory?stream=&seq=&limit=` serves
  what comes after a cursor, even when she isn't in the world. A chunk that leaves the ring unread is reported once as
  `dropped`, which the backend files as a `pack_dropped` gap with its seq and tick range.
- **Backend side.** The `trajectory` worker pulls every 2 s (at most 10 calls of 40 chunks per pass).
  - **Backpressure:** it stops pulling while the writer's chunk queue has no room, and the pack keeps buffering.
  - **Restarts:** after a restart it resumes from the newest chunk saved. A chunk saved twice is one row.
  - **Session filing:** a chunk is filed under the session live at collection, if that session had started by the
    chunk's end; otherwise under none (play outside a session is still kept).
- **Failures.** A save that fails retries, then becomes a gap, as in step 1. A full chunk queue is a `queue_full`
  gap. A hash mismatch is an `unserializable` gap. A stream whose end was never seen is listed as missing.
- **Limits.** A pack that exits loses its buffer: the stream shows no end and its last chunks are missing. A sudden
  backend crash loses queued records, which the pack still holds and are pulled again.

## Observation, memory, learning

- **Observations link to detail.** Minecraft observations name the detail they came from in `detail` (`CaptureLink`,
  ids only): `attempt_id` and `run_id` for an outcome, the pack's `pack_seq` and `pack_at` for a notice.
  `trajectory.resolve(user, at)` says whether her trajectory holds that moment (`found`), held it but it's past raw
  retention (`expired`), or doesn't (`missing`).
- **Ticks stay evidence.** Routine ticks create no observation, no memory and no model call (checked in the tests).
- **Nothing downstream changed.** Learning intake, lessons and memory are untouched (scope boundary).

## Reconstruction and API (owner only)

- `GET /api/learning/capture/sessions/{id}?since=&until=` gives a game session's experience in a window (default: the
  last hour):
  - planner proposals with what was applied (rules by origin, revision after);
  - hard thoughts with where they went;
  - each hands decision with its plan revision, the planner attempt that made it (or "unknown here"), and three
    reports kept apart: `claim` (her hands' words), `action` (the pack's report), `observed` (her state by code);
  - step checks, trajectory streams with their holes, every missing link, gaps, leaks.
- `GET /api/learning/capture/trajectory?session_id=&since=&until=` lists the streams and what they cost.
- `GET /api/learning/capture/trajectory/{stream}?from_tick=&to_tick=&since=&until=&ticks=&readable=` returns every
  tick held (at most 6000 ticks / 200 chunks per read, `truncated` past that), the events in order, what's missing
  and the coverage.
- `GET /api/learning/capture/attempts/{id}` now expects a `plan_apply` for a planner attempt and a `thought_route`
  for a concluded thought.

## Retention and deletion

- **Retention.** Chunks expire after `LEARNING_CAPTURE_RETENTION_DAYS` (30) and are pruned by the `learning` worker.
  A stream header goes once it has expired and holds no chunk. Chunks reference nothing else, so pruning leaves no
  orphan.
- **Deletion.** Deleting a game session deletes the chunks collected in it. Deleting a user deletes everything. The
  memory reset's `capture` scope and `forget_user_raw` include both tables.
- **Access.** Everything is filtered by user; another user gets 404 or nothing.

## Not covered

- Mouth wording and the VM key-pressing driver are still not captured as learners (design decision).
- Merging the pack's and the backend's clocks: deliberately not done.
- Attributing a delayed consequence to a cause: later work.
- No positive training examples are derived from trajectories.
- Gap lines written to the file are still not loaded back.
- Storage settings are a first choice, to revisit after real-play measurement.

## Completion evidence

| # | Shown by |
| --- | --- |
| 1 | `test_planner_hard_thought_and_hands_reconstruct_with_three_separate_reports`: the planner's exact input and frozen plan, a failed try linked to its retry, the applied revision with the old rule `stay dry` marked as carried by code, a hard thought and its refused tip, hands decisions linked to the planner attempt that made their revision, no leaks |
| 2 | Same test: no goal, a success, a failure, a run interrupted by danger, recovery. `test_a_missing_record_stays_visible_in_the_session` |
| 3 | `test_every_tick_is_rebuilt_from_saved_chunks_and_matches_what_the_bot_did`: the pack's own recorder (Node) → collector → PostgreSQL → reader, every tick checked against what the script set, unchanged ticks, chunk boundaries, 3 s dead without ticks. On a real server: `trajectory.itest.js` checks every tick against an independent listener |
| 4 | Same tests: fall, lava ×6 within 30 ms (all kept), an unnamed id stays unknown. Real server: fall, lava, mob attack by a zombie, a death message |
| 5 | Test 1: craft succeeded, the claim "I finished the armor set", inventory with boots only, three reports. A damage after a skill's `done` stays in order with no cause assigned |
| 6 | `test_a_full_pack_buffer_a_reconnect_and_a_restart_are_never_shown_as_continuous`, `test_storage_down_or_behind_never_blocks_and_never_fabricates_a_trajectory` |
| 7 | `test_retention_deletion_and_access_cover_the_trajectory`, `test_the_api_serves_only_the_owners_own_trajectory`. Test 3 checks no observation or memory per tick |
| 8 | The measurements above; `scripts/trajectory_report.py` for live play |

Pack tests: `bun run test` (`test/trajectory.test.js`), `bun run smoke` (now checks recording), and
`node --test test/integration/trajectory.itest.js` (real server, needs the EULA accepted).
