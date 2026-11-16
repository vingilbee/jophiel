# Step 1 implementation: capture and ownership

Status: built 2026-11-01 on `feature/learning-01-capture-ownership` (migration `d9b4e7a2c6f8`, not applied to the dev
database). Updated 2026-11-13: Student 1 and Student 2 (and their `effect` executions and training examples) were
retired; her mind's evaluation is the conversational learner (migration `b7e2c9f4a1d6`). Decisions: [01-capture-and-ownership.md](01-capture-and-ownership.md). This page records the
implementation details that plan left open (schema, queue and retry settings, derivation rule) and what is not covered.

## Who records what

| Owner | Records | Where |
| --- | --- | --- |
| Her mind | one evaluation (learner `mind`, since 2026-11-13): what woke her, what memory gave her (refs, status), the lessons shown, the system prompt (by hash), the tools she used and what each returned, her reply or her silence and why | `mind_turn.finish` |
| Delivery | the reply as stored and sent next to what her mind chose (speak / silent); an interrupted voice reply (`heard`); WhatsApp settlement per part and status (`channel_delivery`) | `mind_turn.finish` (both reply paths and wakes), `chat.record_heard`, `whatsapp._settled` |
| Her hands | each skill decision (system prompt and schema by hash, the step's text, output, tokens, lesson and learned-move refs) and each skill run (`unknown` while running, settled by its outcome) | `skill_driver` via `workers/desktop._hands_capture` |
| Persistence and linking | `services/learning_capture.py` | one writer thread per process |

The capture service never interprets a record. Learning reads records without rewriting them. Memory still owns every memory write.

## Identity and links

- Ids are assigned before a learner runs. In a chat job, attempt *n* of a learner is `uuid5(turn_id, "<learner>:<n>")`. A reclaimed job's attempt *n+1* is a new row with `prior_attempt_id` = attempt *n*, and capturing the same attempt again inserts nothing (`ON CONFLICT DO NOTHING`).
- A wake of her mind (no user message) runs as its own turn, `uuid5(request, "follow_up")`; her attempt there names the work that woke her (`upstream.work`), and its delivery links to that attempt.
- A turn has one `reply` record (`uuid5(turn_id, "reply")`, first wins). A skill run has one record per attempt (`uuid5(attempt, "skill_run")`): an `unknown` result can be settled once, a known one never changes.
- Links between attempts and executions are ids, not foreign keys. Records arrive asynchronously and out of order, and a missing one shows up as a gap rather than a failed insert. Raw records have real cascading foreign keys to user, conversation, message and game session.
- `absent` names what was deliberately not captured: always `prediction`, `alternatives`, `selection_probabilities`, plus `input`/`output` when they never existed. `prediction_ref` and `selection` are the optional links later plans fill.

## Schema (migration `d9b4e7a2c6f8`)

| Table | Purpose |
| --- | --- |
| `decision_attempts` | learner, trigger, provider, model, turn/conversation/message/session, prior and upstream links, versions, settings, frozen input, input refs (memory fingerprints, lesson revisions), output, absent, status, error, blob hashes, started/produced/captured/expires |
| `decision_attempt_sources` | `message:`/`fact:`/`item:`/`thought:`/`work:`/`session:` refs an attempt read (for forgetting) |
| `capture_blobs` | exact unchanging text by `(user, sha256)`: system prompts, schemas. Pruned when unreferenced for 1 h |
| `decision_executions` | kind (`reply`, `heard`, `channel_delivery`, `skill_run`, `plan_apply`, `thought_route`, `step_check`, `desktop_action`), executor, intended, executed, result (`succeeded`, `deviated`, `failed`, `unknown`, `not_executed`, `cancelled`, `handed_off`, `not_sent`), deviation, start/end |
| `capture_gaps` | content-free known losses: `queue_full`, `retries_exhausted`, `unserializable`, `source_gone`, `shutdown` |

## Persistence and failure handling

- **Freeze, then queue.** `attempt()`/`execution()` turn their arguments into JSON on the caller's thread, then append to an in-memory queue. They never block and never raise.
- **Writer.** One thread per process saves each record in its own transaction.
- **Settings (defaults).** `LEARNING_CAPTURE_QUEUE_MAX=500`, `LEARNING_CAPTURE_MAX_TRIES=6`, backoff `LEARNING_CAPTURE_RETRY_BASE_SECONDS=0.5 × 2^n`, capped at `LEARNING_CAPTURE_RETRY_MAX_SECONDS=30` (about 30 s of tries in all), `LEARNING_CAPTURE_DRAIN_SECONDS=5` at exit.
- **Gaps.** A full queue, exhausted retries, an unserializable record, a source deleted before the save (foreign key 23503, not retried) and records left at exit all become `capture_gaps`. When the database itself is down, gaps go to `LEARNING_CAPTURE_GAP_LOG_PATH` (`var/learning_capture_gaps.jsonl`).
- **Crashes.** A sudden crash loses the queue without a gap. Reconstruction then shows the missing link.
- **Limits.** No latency guarantee. The writer adds no work to the reply path beyond JSON-encoding the record.

## Reconstruction (`services/capture_chains.py`)

- **`turn(user, turn_id)`.** Attempts and executions of the turn, upstream and earlier tries followed, delivery records joined by message. `missing` lists upstream attempts never saved, an expected learner or reply with no record, and earlier tries. It also returns the turn's gaps and a `leaks` check (any source message an attempt read that was written after it started; must be empty). `complete` is true only with nothing missing and no gaps.
- **`attempt(user, id)`.** One attempt's chain. For her hands, it expects the skill run.
- **API, the user's own records only.**
  - `GET /api/learning/capture`
  - `GET /api/learning/capture/turns/{turn_id}` (`?blobs=true` resolves system texts)
  - `GET /api/learning/capture/attempts/{id}`

## Access: the owner only (decided 2026-11-01)

The user decided that training data is for the owner to train with, and that other users can only talk with
Columbina. So every route under `/api/learning` (captures, lessons, skills, assessments, trials),
`/api/mind` and `/api/memory`, plus the live `thought` socket frames, is for the owner only
(`deps.require_owner`). Any other user gets 404. `OWNER_EMAILS` (comma-separated) names the owner, falling back to
`MINECRAFT_PACK_OWNER_EMAIL`; with both empty, nobody has access. `/api/auth/me` returns `is_owner`, so the app hides
the Learning tab, the Mind panel and thoughts from other users. The routes are the real enforcement.

This changes a point of the 01 decisions:
- **"Private to their user" still holds.** The owner sees only records filed under the owner's own account. Whether
  the owner may also see and train on other users' examples is not decided yet; step 5 decides it.

## Retention and deletion

- **Raw retention.** `LEARNING_CAPTURE_RETENTION_DAYS=30` (`expires_at`). The `learning` worker thread prunes expired attempts, executions and gaps every 10 min.
- **Source deletion.** Deleting a conversation, message, game session or user cascades to its raw copies. A forget (`memory_forget._apply`) calls `learning_capture.drop_raw_for` in its transaction, with the forgotten facts, episodes, items, events and fenced messages. A message ref also removes every attempt in its conversation since that message, because a learner's window may have shown it without naming it.
- **Training examples (retired 2026-11-13).** Derived from Student 2's teacher decisions until Student 2 was retired; the tables, routes and the `LEARNING_DERIVE_EXAMPLES` setting were removed with it (migration `b7e2c9f4a1d6`).
- **Reset.** `scripts/reset_memory.py` gains the `capture` (raw) scope.
- **Privacy.** Captures are filtered by `user_id` everywhere. Nothing reads across users.

## Not covered

- **The live teacher JSONL.** `COGNITION_TEACHER_DATA_PATH` is kept as it was, outside raw retention. It is treated as an export, scrubbed on explicit deletion only.
- **The mouth's wording and the VM key-pressing driver.** Not captured as learners yet. (The planner and hard thoughts are, since post-audit 1: [POST-AUDIT-01-implementation.md](POST-AUDIT-01-implementation.md).) The mouth's words are reachable through the reply's message id.
- **Worded autonomous speech.** `say` and `deliver_worded` have no decision attempt behind them yet.
- **Gaps on file.** Gap lines written to the file are not loaded back into the database.
- **Restart recovery of a queue.** The queue is in memory by design (the accepted decision). Records lost in a crash show up only as missing links.

## Completion evidence

`backend/tests/test_learning_capture.py` (real PostgreSQL, scripted learners, no model calls):

| Test | Shows |
| --- | --- |
| conversation turn end to end | her mind's one evaluation: what woke her, the system prompt (by blob), memory refs and status, tools used, the reply; the delivery record; sources; no later message in inputs; `leaks` empty |
| failures known, unknown stays unknown | a failed memory search, a failed evaluation, silence chosen = not sent, cut-off reply = deviated, interrupted voice = heard, WhatsApp reply = unknown |
| game decision | three skill decisions: succeeded, failed with reason, still running = unknown; system prompt resolved from its blob |
| outage | replies continue with storage down; bounded retries; gaps to the gap file, content-free; nothing shown as a chain |
| full queue, deleted source | `queue_full` and `source_gone` gaps in the database |
| restart and retry | the same attempt saved twice is one row; a reclaimed job is a new attempt linked to the old one; one reply record |
| retention and forgetting | expired raw captures pruned, a forgotten fact's raw copies dropped |
| user boundary, API | another user's turn and attempt are 404; the retired training-example routes are gone |
