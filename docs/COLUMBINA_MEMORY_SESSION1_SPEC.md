# Memory session 1: general working memory (spec)

Status: spec for review. Nothing is built. Parent design: `docs/COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md`.
Date: 2026-10-20

## 1. The problem this session fixes

Live Minecraft session `9e42d19d`, 2026-10-20, 18:14–18:22 UTC:

- **The goal was lost.** At 18:15 Vincent said "the current task is to go to the Nether". Each later
  instruction replaced the whole plan. There were 14 plan sets in 7 minutes: "go to the Nether" → "gather
  wood" → "materials toward a Nether portal" → "mine cobblestone" → "reach dry land near the stone".
  At 18:22 he asked "do you still remember our goal?" and she answered "iron and a sheep".
- **The old world leaked into the new one.** Before and after the map change she kept talking about
  "the cave furnace" and "smelting iron" from the previous world.

The cause: the game plan is the only place a goal lives, it belongs to one game session, and nothing
records which world or activity something belongs to.

## 2. Scope

**In:**
- explicit contexts
- general goals (with sub-goals outside games)
- typed intentions and commitments
- recent meaningful events, linked to their sources
- inspectable manager state
- a cheap due-work check in the existing mind loop
- the game session linked to a goal through a real column (`desktop_sessions.goal_item_id`)
- the bounded memory view, read by the reply, the mind and the planner

**Out (later sessions):**
- consolidation into episodes
- observation contracts beyond what's listed here
- learning and outcomes
- the voice fact-saver fix (§11 sketches it separately)
- any change to live notes or shadow dialogue memory

**Preserved unchanged:**
- live notes (`working_memories.cognitive_state["simple_live"]`, `notes-v2`): a pinned student input
- the `working_memories` table and every consumer of it
- shadow dialogue memory (§10 covers what we borrow from its contracts)

Everything new sits behind `MEMORY_MANAGER_ENABLED` (default false). With the flag off, behaviour is the same as today.

## 3. Ownership: one writer per fact

| Fact | Owner after session 1 | Today |
| --- | --- | --- |
| What we're trying to achieve (goal) | **Memory manager** (`working_items`, kind `goal`) | Game plan `goal` text, replaced every turn |
| How the hands get there in a game (steps) | Game planner / `set_game_plan` (unchanged); the session points at the goal (`goal_item_id`) | Same, but no link |
| Sub-goals of a non-game goal | Memory manager (`goal` with `parent_id`) | Nowhere |
| Promises she made | Memory manager (kind `commitment`) | Redis `note_promise`, per game session, 3 kept, 20 min |
| Things she means to do, not promised | Memory manager (kind `intention`) | Redis `mind:intentions`, 3 kept, 20 min |
| Tips to her hands (execution hints) | Unchanged: Redis hands channel | Same |
| Questions waiting for an answer | Memory manager (kind `question`) | Hard-thought question hand-off; live notes |
| Which world/activity/project | Memory manager (`memory_contexts`) | Nowhere |
| Conversation notes for the student | Unchanged (`cognitive_live`) | Same |

Rule: when a writer moves, its readers move in the same change. While the flag is on, `note_promise` and
`drop_promise` write to the manager **instead of** Redis, never to both. Hard-thought intentions are
written to the manager, and the hands keep receiving them through their existing tips channel (the
manager forwards them).

Intention vs commitment (from review): a **commitment** is owed to someone ("I said I'd get replacement
wood"). An **intention** is her own plan ("I should check that cave"), and it can lapse without anyone
being let down. An **execution hint** ("turn left at the wall") isn't memory at all and stays in the
hands channel.

## 4. Schema (migration-level)

### 4.0 Conventions

- Postgres 16. Every id is `uuid` (app-side `uuid4`) except append-only rows (`memory_events`,
  `memory_evidence`), which use `bigint generated always as identity` for ordering.
- Every time is `timestamptz`, `not null default now()` unless stated otherwise.
- Code columns (kind, status, owner, …) are `varchar(16)`/`varchar(32)` with a `CHECK`. Free text is
  `text` with **no length limit**. The only text rule is "not blank" where text is required.
- Every row carries `user_id → users.id ON DELETE CASCADE`, so deleting an account removes everything.
- References between memory rows use **composite foreign keys that include `user_id`** (or
  `context_id`), so a row can never point at another user's or another context's row.
- Those references are `ON DELETE NO ACTION` (checked at the end of the statement), **not RESTRICT**.
  Deleting a referenced context or goal on its own fails, while the account cascade and the reset
  (one statement per table, in order) still work.
- **Memories stand on their own.** An item keeps its full description, status, context and times.
  Evidence links (4.5) explain where it came from. They don't decide whether it exists, and losing a
  link never deletes the memory.

### 4.1 `memory_contexts`

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | uuid4 | pk |
| `user_id` | uuid | no | | fk users cascade |
| `kind` | varchar(16) | no | | `general` \| `activity` \| `project` \| `world` |
| `label` | text | no | | not blank |
| `source` | varchar(32) | yes | | who can recognise it again (`minecraft_pack`, …) |
| `external_ref` | text | yes | | the source's identity for it |
| `parent_id` | uuid | yes | | composite fk `(parent_id, user_id) → memory_contexts(id, user_id)` no action |
| `status` | varchar(16) | no | `'active'` | `active` \| `paused` \| `closed` |
| `status_reason` | text | yes | | |
| `created_at` | timestamptz | no | now() | |
| `last_active_at` | timestamptz | no | now() | |
| `closed_at` | timestamptz | yes | | |

Constraints:
- `ck_memory_contexts_kind`, `ck_memory_contexts_status`: the code lists above
- `ck_memory_contexts_label`: `btrim(label) <> ''`
- `ck_memory_contexts_closed`: `(status = 'closed') = (closed_at IS NOT NULL)`
- `ck_memory_contexts_general`: `kind <> 'general' OR (source IS NULL AND external_ref IS NULL AND parent_id IS NULL AND status <> 'closed')`.
  The general context is never closed.
- `ck_memory_contexts_ref`: `external_ref IS NULL OR source IS NOT NULL`
- `ck_memory_contexts_parent_self`: `parent_id IS NULL OR parent_id <> id`
- `uq_memory_contexts_id_user`: unique `(id, user_id)` (the target of composite fks)
- `uq_memory_contexts_general`: unique `(user_id)` where `kind = 'general'`
- `uq_memory_contexts_ref`: unique `(user_id, source, external_ref)` where `external_ref IS NOT NULL`
- trigger `memory_contexts_parent_check` (before insert/update of `parent_id`): rejects cycles by walking up the parents

Indexes: `(user_id, status, last_active_at DESC)`.

Deletion: normal use only closes a context. Deleting one fails while items, events or children
reference it. Only the account cascade and the reset (4.8) remove contexts.

### 4.2 `working_items`

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | uuid4 | pk; also the stable goal id the game session points at |
| `user_id` | uuid | no | | fk users cascade |
| `context_id` | uuid | no | | composite fk `(context_id, user_id) → memory_contexts(id, user_id)` no action |
| `kind` | varchar(16) | no | | `goal` \| `intention` \| `commitment` \| `question` |
| `parent_id` | uuid | yes | | composite fk `(parent_id, context_id) → working_items(id, context_id)` no action |
| `owner` | varchar(16) | no | | `user` \| `columbina` \| `shared`: who wants it / who owes it / who asked |
| `owed_to` | varchar(16) | yes | | commitments only: `user` \| `columbina` |
| `text` | text | no | | not blank |
| `done_when` | text | yes | | |
| `status` | varchar(16) | no | | per kind, below |
| `status_reason` | text | yes | | |
| `epistemic` | varchar(16) | no | | `reported` (the user said it) \| `inferred` (she concluded it) |
| `due_at` | timestamptz | yes | | |
| `closed_at` | timestamptz | yes | | |
| `revision` | integer | no | 1 | bumped on every update; updates are compare-and-set |
| `created_at` | timestamptz | no | now() | |
| `updated_at` | timestamptz | no | now() | |

Statuses (`ck_working_items_status`):

| Kind | Open statuses | Closed statuses |
| --- | --- | --- |
| goal | `active`, `suspended` | `completed`, `abandoned` |
| commitment | `open`, `needs_review` | `fulfilled`, `cancelled` |
| intention | `active` | `done`, `dropped`, `lapsed` |
| question | `open` | `answered`, `dropped` |

Constraints:
- `ck_working_items_kind`, `ck_working_items_owner`, `ck_working_items_epistemic`: the code lists above
- `ck_working_items_text`: `btrim(text) <> ''`
- `ck_working_items_closed`: `(closed_at IS NOT NULL) = (status IN (<all closed statuses>))`
- `ck_working_items_parent`: `parent_id IS NULL OR (kind = 'goal' AND parent_id <> id)`. Only goals have parents.
- `ck_working_items_owed_to`: `(kind = 'commitment') = (owed_to IS NOT NULL)`. Required for
  commitments, forbidden otherwise.
- `ck_working_items_commitment_parties`: `kind <> 'commitment' OR (owner IN ('user','columbina') AND owner <> owed_to)`.
  A commitment is owed by one party to the other: Columbina to Vincent ("I'll get replacement wood"),
  or Vincent to Columbina ("I'll show you the base later"). **A self-commitment is not a commitment**:
  "I'll remember to check the cave" is an intention.
- `ck_working_items_intention_due`: `kind <> 'intention' OR due_at IS NOT NULL` (an intention always has a lapse time)
- `uq_working_items_id_user`: unique `(id, user_id)`
- `uq_working_items_id_context`: unique `(id, context_id)` (targets of composite fks)
- trigger `working_items_parent_check` (before insert/update of `parent_id`, `kind`, `context_id`):
  - the parent's `kind` is `goal`
  - no cycle (walk up the parents)
  - a goal that is a parent can't change kind

  The composite fk already guarantees the same context (so the same user).

Indexes:
- `(user_id, status)`
- `(context_id, kind, status)`
- `(parent_id)` where `parent_id IS NOT NULL`
- `ix_working_items_due`: `(due_at)` where `due_at IS NOT NULL AND closed_at IS NULL` (the tick's query)

### 4.3 `memory_events`

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | bigint identity | no | | pk; ordering |
| `user_id` | uuid | no | | fk users cascade |
| `context_id` | uuid | no | | composite fk `(context_id, user_id) → memory_contexts(id, user_id)` no action |
| `item_id` | uuid | yes | | the item this is about; composite fk `(item_id, user_id) → working_items(id, user_id)` `ON DELETE SET NULL (item_id)` |
| `kind` | varchar(32) | no | | open list (`item_changed`, `context_switched`, `death`, `world_changed`, …); no check |
| `text` | text | no | | not blank, one line |
| `epistemic` | varchar(16) | no | | `observed` \| `reported` \| `inferred` |
| `occurred_at` | timestamptz | no | | when it happened |
| `recorded_at` | timestamptz | no | now() | when we learned it |
| `dedupe_key` | text | yes | | |

Constraints:
- `ck_memory_events_epistemic`
- `ck_memory_events_text`
- `uq_memory_events_dedupe`: unique `(user_id, dedupe_key)` where `dedupe_key IS NOT NULL`
- `uq_memory_events_id_user`: unique `(id, user_id)`

Indexes: `(context_id, id DESC)`, `(user_id, id)` (the processed boundary).

Where an event came from is an evidence link (4.5), not a column.

### 4.4 `memory_manager_state`

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `user_id` | uuid | no | | pk; fk users cascade |
| `focus_context_id` | uuid | yes | | composite fk `(focus_context_id, user_id) → memory_contexts(id, user_id)` `ON DELETE SET NULL (focus_context_id)` |
| `last_event_id` | bigint | no | 0 | processed boundary |
| `last_tick_at` | timestamptz | yes | | |
| `next_check_at` | timestamptz | no | now() | |
| `last_decision` | jsonb | no | `'{}'` | `{at, decision: skip\|defer\|apply, reason}`. Inspection only, never read as state |
| `revision` | integer | no | 1 | compare-and-set |
| `updated_at` | timestamptz | no | now() | |

Index: `(next_check_at)`.

Pending work is **not stored**: it's a query, either `working_items` with status `needs_review`, or
`due_at <= now()` and still open. One source of truth, no id lists in JSON.

### 4.5 `memory_evidence`

Where a memory came from. Each row is a real foreign key, so deleting the source deletes the link and
nothing else.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | bigint identity | no | | pk |
| `user_id` | uuid | no | | fk users cascade |
| `item_id` | uuid | yes | | composite fk `(item_id, user_id) → working_items(id, user_id)` `ON DELETE CASCADE` |
| `event_id` | bigint | yes | | composite fk `(event_id, user_id) → memory_events(id, user_id)` `ON DELETE CASCADE` |
| `role` | varchar(16) | no | | `origin` \| `support` \| `resolution` \| `correction` |
| `message_id` | uuid | yes | | fk `chat_messages.id` `ON DELETE CASCADE` |
| `session_event_id` | bigint | yes | | fk `desktop_session_events.id` `ON DELETE CASCADE` |
| `thought_id` | uuid | yes | | fk `mind_thoughts.id` `ON DELETE CASCADE` (thoughts are pruned after 7 days: the link goes, the memory stays) |
| `experience_id` | bigint | yes | | fk `game_experiences.id` `ON DELETE CASCADE` |
| `created_at` | timestamptz | no | now() | |

Constraints:
- `ck_memory_evidence_target`: `num_nonnulls(item_id, event_id) = 1`
- `ck_memory_evidence_source`: `num_nonnulls(message_id, session_event_id, thought_id, experience_id) = 1`
- `ck_memory_evidence_role`: role list. `resolution` only with `item_id`.
- `uq_memory_evidence`: `UNIQUE NULLS NOT DISTINCT (item_id, event_id, role, message_id, session_event_id, thought_id, experience_id)`

Indexes: one per source column (so cascades from those tables stay fast), plus `(item_id)` and `(event_id)`.

Sources have no `user_id` of their own (a message belongs to a conversation), so the manager checks that
a source belongs to the user before linking it.

Rules:
- Evidence is **optional** in the schema. The manager adds an `origin` link whenever a source exists
  (tools always pass the current message), but a memory with no links left is still valid.
- `completed`/`fulfilled` require a `resolution` link **at the moment of the transition** (the user's
  message, or a session event). The manager enforces this, not the database, because the link may
  legitimately disappear later.
- In the view, an item with no `origin` link left reads "source: no longer available". She must not
  claim she can quote the original instruction.
- Consolidation (session 3) adds summaries as **new memories**. It does not relink old evidence to them.

### 4.6 `desktop_sessions.goal_item_id` (new column on an existing table)

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `goal_item_id` | uuid | yes | composite fk `(goal_item_id, owner_user_id) → working_items(id, user_id)` `ON DELETE SET NULL (goal_item_id)` |

Index `(goal_item_id)` where not null. The plan JSON may carry a copy (`plan.goal_id`) for readers that
only see the plan. The column is the truth, and the copy is rewritten whenever the column changes.

Effects of the goal on play (applied by the manager in the same transaction as the status change):

| Goal becomes | Linked session that's running or paused |
| --- | --- |
| `suspended` | paused with reason `goal_suspended`. Resuming needs the goal `active` again plus the usual explicit resume |
| `completed` | paused with reason `goal_completed` and the link kept. Resume is refused until a new plan is set |
| `abandoned` | same as completed, with reason `goal_abandoned` |
| `active` again | nothing resumes on its own (the existing rule) |

Locking order: environment → session → working item (it extends the existing environment → session
order). The manager locks the item last.

### 4.7 Defaults the manager fills (not the database)

- intention `due_at` = now + 2 h
- commitment `due_at`: only when a time was said
- `epistemic`: `reported` for tool calls made on a user message, `inferred` for her own (background turns, thoughts)
- `owner`: `user` for goals set from the user's words, `columbina` for her own

### 4.8 Reset (`services/memory_reset.py`, `scripts/reset_memory.py`)

A new scope `working` deletes, in this order:
1. `memory_evidence`
2. `memory_events`
3. `UPDATE desktop_sessions SET goal_item_id = NULL` (sessions themselves are untouched)
4. `working_items` (one statement; the self-reference is checked at statement end)
5. `memory_manager_state`
6. `memory_contexts`

What each scope removes after this change:

| Scope | Removes | Working memory |
| --- | --- | --- |
| `conversations` | conversations, messages, … | **stays**. Message evidence links go with the messages |
| `memory` | semantic, episodic, learning memories, memory jobs | stays |
| `game` | game experiences, lessons, skills, … | stays. Experience evidence links go with the experiences |
| `working` (new) | everything above, in that order | **removed** |
| no scope given (all) | all of the above | removed |

Account deletion cascades through `user_id` as for every other table.

### 4.9 Later sessions: integration requirements only (no schema yet)

- Episodes and knowledge will need a nullable `context_id` (same composite-fk pattern), so recall can
  filter by context, and historical contexts remain retrievable.
- `memory_evidence` will gain targets (`episode_id`, `knowledge_id`) through the same "exactly one
  target" check, not a second evidence table.
- Consolidation records will reference a `memory_events` id range per context.
- Learning attempts and outcomes will link to `memory_events` and `working_items` by real fks.

Their migrations are deferred until their sessions. Nothing here fixes their final shape.

## 5. Manager operations (`services/memory_manager.py`)

Plain functions on a db session. Every write checks ownership (user), bumps `revision`, writes a
`memory_events` row, adds evidence links when a source is known, and runs in the caller's transaction.

| Operation | Rules |
| --- | --- |
| `ensure_general(user)` | lazily creates the general context + state row |
| `open_context(user, kind, label, source?, external_ref?, parent?)` | idempotent on (`source`, `external_ref`); sets focus |
| `focus(user, context_id)` | changes focus only; nothing is closed |
| `close_context(user, context_id, reason)` | its active goals → `suspended` (reason "context closed: …"), open commitments → `needs_review`; nothing is deleted |
| `set_goal(user, text, owner, context?, parent?, done_when?, source?)` | a goal with no parent in a context that already has an active top-level goal is **allowed** (two goals can coexist); it never closes the other one |
| `add_subgoal(user, parent_id, text, …)` | parent must be an active goal in the same context |
| `resolve(user, item_id, status, reason, source?, expected_revision?)` | legal transitions only; `completed`/`fulfilled` need a source (it becomes the `resolution` link); a goal's change applies its effect on a linked game session (4.6); closing a goal does **not** close its children automatically, it lists them in the result so the caller decides |
| `note_commitment` / `note_intention` / `note_question` | `owner`/`owed_to` required; intentions get `due_at` = now + 2 h |
| `record_event(user, kind, text, source, epistemic, context?, dedupe_key?)` | duplicate `dedupe_key` is a no-op |
| `view(user, *, for_=reply\|mind\|planner, context?)` | the bounded memory view (§6) |
| `tick_due(db, now)` | §8 |

Nothing in the manager calls a model in session 1. The semantic judgement ("is this a new goal or part of
the current one?") is made by the reply model choosing a tool (§9). Code enforces the consequences.

## 6. The memory view

The view is one short text block, built from the database, with ids, so tools can refer to items.

```
What we're doing (memory, newest first):
Focus: Minecraft, world started today 18:14
  Goal g1 (Vincent's, since 18:15): reach the Nether
    - current game plan serves it: "mine cobblestone"
  You promised (c1, 18:16): kill a sheep for wool if you see one
  You meant to (i1): find iron
  Recent: 18:19 died to a drowned, lost the logs · 18:21 reached dry land
Also open elsewhere:
  Interview prep: goal g2 "prepare answers for Friday" (suspended since yesterday)
Earlier world (closed 18:14): goal "smelt the raw iron" suspended. That was on the old map: don't treat it as here.
```

Budget: focus context in full (goals ≤ 5 with ≤ 5 sub-goals each, open commitments/intentions/questions
≤ 5 each, last 5 events), other active contexts as one line each (≤ 3), closed contexts only when
something in them is still unresolved (≤ 2 lines, labelled as past). It's capped at about 1,200 characters, and
items are dropped lowest-priority first: events, then intentions, then other contexts.

Readers:
- **reply turn:** the orchestrator prompt gets it as a new `## What we're doing` block next to awareness, every user turn
- **game summary** (`desktop_game.describe`): when the flag is on, it gets the goal line in place of "Goal: <plan goal>"
- **mind tick:** a goal/commitment line in the Minecraft situation; the conversation source gets the open items
- **planner input** (`game_planning.build_input`): "The goal (from memory): … · this plan serves it"

## 7. Contexts in session 1 (explicit only)

| How a context opens or changes | Example |
| --- | --- |
| The user names one, and the reply model calls `switch_context` | "let's work on my interview", "new map" |
| A source reports a new identity | the pack's world identity changes |
| Default | everything else goes to the general context |

There's no automatic topic-based splitting.

**Minecraft world identity.** This still needs checking. The pack doesn't report one today. The server always uses
`level-name = columbina`, and "a new map" means the folder was replaced, so the level name can't tell
worlds apart. The proposal: the pack's `/state` gains `world: {id}`, read from a small `columbina-world-id`
file the pack writes into the world folder the first time it sees that world (a random uuid). A
replaced folder has no file, so it's a new world. This is reliable and doesn't depend on what the
protocol exposes (mineflayer's login packet handling shows `worldName`/`dimension` but no seed in the
code inspected). Fallback while that's not built: the user's "new map" → `switch_context(new=true)`.

On a world change, `close_context(old)`:
- goals in it become `suspended` ("that world was replaced")
- an intention like "check the cave furnace" lapses
- general knowledge isn't touched, because it isn't in working memory

**Unchanged:** the session's goal `"Reach the Nether"`, if it was set in the new world.

## 8. The tick (code only, in the mind loop)

`mind.run_once()` calls `memory_manager.tick_due(db, now)` first, before sources. It runs whenever
`MIND_ENABLED` and `MEMORY_MANAGER_ENABLED` are on, with or without a game session or open socket.

For each user with `next_check_at <= now` (small indexed query):
- intentions past `due_at` → `lapsed`
- commitments past `due_at` → `needs_review`
- an active context with no event for 2 h → `paused` (attention only; goals untouched)
- write `last_tick_at` and `last_decision`
- set `next_check_at` to the nearest upcoming due time, capped at 10 min

No model calls, no messages. Items needing review reach the mouth through the view ("needs a look: c1, due
an hour ago"), not as an unprompted message: speaking first stays the job of the existing nudge and
`agent_say` rules.

Restart: everything lives in Postgres. On boot the first tick picks up every overdue user once
(no replay of missed ticks). Concurrency: the tick takes `pg_try_advisory_xact_lock(user)` per user, and
item updates are compare-and-set on `revision`.

## 9. Tools (reply turn, general, not game-specific)

| Tool | Does |
| --- | --- |
| `set_goal(text, owner="user", parent_id=None, done_when=None, context="focus")` | new goal or sub-goal |
| `update_item(id, status, reason)` | complete / suspend / abandon / fulfil / cancel / drop |
| `note_commitment(text, due_in_minutes=None)` | "I'll …" said to the user |
| `note_intention(text)` | her own plan |
| `switch_context(label, kind="activity", new=False)` | focus another context, or open a new one (`new=true` closes the focused one of the same source when it's a world) |

Evidence for every call is the current user message id, filled by code, never by the model.

Prompt rule (short): "Small instructions serve the current goal: don't replace the goal for them. Only
`set_goal` when Vincent wants a different end, and only `update_item` to close something when he says
so or it's clearly done."

**Game link.** With the flag on:
- `set_game_plan`, `set_game_goal`, `start_game` and `do_now` set `desktop_sessions.goal_item_id` (and
  the plan's `goal_id` copy). It's the focused context's newest active top-level goal, unless the call
  passes `new_goal=true`, which first calls `set_goal` with that text.
- Replacing the plan **never touches** the goal.
- The planner's `done` decision completes the plan, not the goal. It records a `goal_done`-style event
  and the view shows "plan done; is the goal done?"
- The planner's `new_goal` (one she picked) becomes an intention with owner `columbina`, linked to the plan.

Background turns (`game_events`) get `note_intention` and `update_item` (their own intentions only), not `set_goal`.

## 10. Borrowed from shadow dialogue memory (contracts, not code)

- `DialogueCommitmentReference`: "fulfilment is never inferred", and the source sentence is kept. This
  maps to: a commitment's `origin` link = the message it came from, `fulfilled` only with a `resolution` link.
- Request/plan items with explicit evidence anchors and an epistemic status. This maps to `epistemic` +
  `memory_evidence` here.

Nothing from the shadow pipeline runs live. A later session can decide whether it reads `working_items`.

## 11. Separate small item: provisional facts from speech (not session 1)

Recorded here so it isn't lost (from review, 2026-10-20):
- New fact candidates are `provisional` for a bounded delay, about 60 s, instead of active at once.
- A correction from the user inside the delay cancels or revises the candidate.
- After the delay it's saved, whatever happens next: a candidate never waits forever.
- Language mixing alone is **not** treated as garbling. Use transcription uncertainty where the STT
  gives it, plus contextual ambiguity (she asked "did you mean…?").
- A later correction still needs a retract path on saved facts (`semantic_memories.status =
  retracted` + the correcting message as evidence).

It needs its own short spec covering timeout and correction behaviour before building.

## 12. Acceptance tests

Automated (real Postgres, as the desktop tests do; model calls scripted):

1. **Goal continuity:**
   - "Let's build a Nether portal" → `set_goal`
   - then "chop wood", "if you see a sheep, kill it", "mine cobblestone", "get to dry land"
   - plan replaced 4×; goal `g1` still `active`, the session's `goal_item_id = g1` throughout, and the sheep is a commitment
   - the view's first goal line is the portal
2. **Context isolation:** world A goal "smelt iron" → new world → A closed, goal `suspended`. In the
   view it appears only under "Earlier world", and the world B goal is unaffected.
3. **Topic switch and return:** while the game goal is active, `switch_context("Interview prep", project)`
   + `set_goal("prepare answers")`. Both are active; focus is on the interview. Switching back shows the
   portal as focus and the interview on one line.
4. **Restart:** state persists, and `tick_due` after a simulated restart lapses an overdue intention once
   and sets `next_check_at`. A second tick does nothing (`skip`).
5. **No game, no socket:** with the mind on and no sessions, the tick still runs and records `skip`.
6. **Stale write refused:** `resolve` with an old `revision` fails, and a background turn can't close a
   user-owned goal.
7. **Flag off:** existing game-tool tests pass unchanged.
8. **Memories stand on their own:** deleting the source message removes its evidence link; the goal stays
   and the view says "source: no longer available".
9. **Schema rules:** a non-goal parent, a parent in another context, a cycle, `owed_to` on a non-commitment,
   and a self-owed commitment are all rejected by the database.
10. **Goal effects:** completing/abandoning a linked goal pauses the session and refuses resume until a
    new plan; suspending pauses it.
11. **Reset:** scope `working` removes working memory in order; scope `conversations` leaves goals in place.

Live check (by Vincent, plain steps):

| Do | Expect |
| --- | --- |
| Start a world, say "our goal is to reach the Nether" | Thoughts/inspector shows goal g1 |
| Give 4–5 small instructions | The game plan changes each time; the goal line stays |
| Ask "what's our goal?" | "Reach the Nether", with what she's doing for it now |
| Say "new map", then ask about the furnace | It was on the old map |
| Restart the backend, ask again | Same answer |

## 13. Inspectability

- `GET /api/memory/working`: contexts, items, recent events, manager state (last tick, last decision,
  next check), items needing review
- a small "Memory" panel in the chat page's debug tools, next to the thoughts panel
- every manager decision appended to `var/memory_manager.jsonl` (ids and reasons, no message text)

## 14. Decisions taken by default (change them if you disagree)

| Decision | Default | Why |
| --- | --- | --- |
| Goals/intentions/commitments/questions in one table with `kind` | One table `working_items` | They share lifecycle, evidence and "what's unfinished?" queries; per-kind rules are constraints. Separate tables remain possible |
| Who judges goal vs step | The reply model through tools (option A), code enforces | No lag before the next turn; generalises `set_game_plan`; no extra call |
| Plan links to which goal by default | Focused context's newest active top-level goal | Today's failure was replacing; the safe default is to keep. Override with `new_goal=true` |
| Intention lapse time | 2 h | Longer than today's 20 min, so unfinished business survives a pause; tune later |
| Context inactivity → paused | 2 h | Attention only, nothing closes |

## 15. Open questions

1. World identity file (§7): is it OK for the pack to write a small file into the world folder?
2. Should user goals in the general context ever lapse, or only ever close explicitly? (The spec: only explicitly.)
3. Should the inspector panel let Vincent edit or close items by hand in session 1, or stay read-only?

## 16. Implementation notes (2026-10-20, branch `feature/general-memory`)

Built as specified, except for the following:

| Spec | As built | Why |
| --- | --- | --- |
| `bigint generated always as identity` for events and evidence | `BIGSERIAL` | Same as the codebase's other append-only tables (`desktop_session_events`, `game_experiences`) |
| Hard-thought intentions: the manager forwards them to the hands | The thought is written to both: the Redis list (her hands' tip, an execution hint) and `working_items` (memory). The mouth reads only memory while the flag is on | The hands' channel already exists and stays the hint path; there's still one memory owner |
| Planner `new_goal` becomes an intention | A planner revision links to the open goal like any other plan; a planner `new_goal` starts its own goal (owned by `columbina`, `inferred`; the old one stays open) | A planner goal is a goal, not a promise to herself; owner and epistemic status keep it apart from the user's |
| New world: live sessions are unlinked from the old goals | They keep the link to the old (now suspended) goal, and resume is refused while that goal's world is closed; the next plan links a goal in the new world | Unlinking left the old plan resumable in the new world (review 2026-10-20) |
| Plan link set in `_set_plan` | Also deferred for a session being created: `start_session` links it right after its insert commits | Flushing earlier would turn the `already_owned` race into a different error |
| Minecraft world id file (§7) | Not built | A new world opens only when the user says so (`switch_context(new=true, kind="world")`). Open question §15.1 still stands |
| Inspector panel (§13) | API only (`GET /api/memory/working`) | The chat page's debug panels are being reworked on another branch; the panel follows once that lands |
| `drop_promise(number)` | `number` omitted drops all (as before); `kept=false` cancels; `fulfilled` needs the user's message as evidence, otherwise it's `cancelled` | Same behaviour the tool had, plus the evidence rule |

Tests: `backend/tests/test_memory_manager.py` covers the acceptance list in §12 (24 tests, real Postgres); `test_memory_review_regressions.py` holds the four review regressions (2026-10-20).
