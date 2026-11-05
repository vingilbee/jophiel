# Step 1 implementation: capture and ownership

Status: built 2026-11-01 on `feature/learning-01-capture-ownership` (migration `d9b4e7a2c6f8`, not applied to the dev
database). Decisions: [01-capture-and-ownership.md](01-capture-and-ownership.md). This page records the
implementation details that plan left open (schema, queue and retry settings, derivation rule) and what is not covered.

## Who records what

| Owner | Records | Where |
| --- | --- | --- |
| Student 1 | its attempt: the exact `Bundle` it read, its soft outputs, or its failure | `cognitive_live._capture` |
| Student 2 | its attempt: exact prompt, system text and schema (by hash), snapshot, generation settings, raw output, parsed decision, problems | `student_two.decide(capture=...)`, before `apply` |
| Effect owner | what the work part did next to what was intended | `student_two.apply` → `effect` execution |
| Delivery | the reply as stored and sent next to Student 2's intention; an interrupted voice reply (`heard`); WhatsApp settlement per part and status (`channel_delivery`) | `shared_state.finish`, chat worker follow-ups, `chat.record_heard`, `whatsapp._settled` |
| Her hands | each skill decision (system prompt and schema by hash, the step's text, output, tokens, lesson and learned-move refs) and each skill run (`unknown` while running, settled by its outcome) | `skill_driver` via `workers/desktop._hands_capture` |
| Persistence and linking | `services/learning_capture.py` | one writer thread per process |
| Learning (derivatives) | training examples | `services/training_examples.py`, `learning` worker thread |

The capture service never interprets a record. Learning reads records and adds derivatives without rewriting them. Memory still owns every memory write.

## Identity and links

- Ids are assigned before a learner runs. In a chat job, attempt *n* of a learner is `uuid5(turn_id, "<learner>:<n>")`. A reclaimed job's attempt *n+1* is a new row with `prior_attempt_id` = attempt *n*, and capturing the same attempt again inserts nothing (`ON CONFLICT DO NOTHING`).
- Student 2's `upstream.student_1` names Student 1's attempt. When Student 1 didn't run or failed, the link still names it, with `student_1_status`.
- A finished thought's decision is `uuid5(work_id, "student_2:result")` on the follow-up turn. The follow-up job carries `decision_attempt_id`, so its delivery links to it.
- A turn has one `reply` record (`uuid5(turn_id, "reply")`, first wins). A skill run has one record per attempt (`uuid5(attempt, "skill_run")`): an `unknown` result can be settled once, a known one never changes.
- Links between attempts and executions are ids, not foreign keys. Records arrive asynchronously and out of order, and a missing one shows up as a gap rather than a failed insert. Raw records have real cascading foreign keys to user, conversation, message and game session.
- `absent` names what was deliberately not captured: always `prediction`, `alternatives`, `selection_probabilities`, plus `input`/`output` when they never existed. `prediction_ref` and `selection` are the optional links later plans fill.

## Schema (migration `d9b4e7a2c6f8`)

| Table | Purpose |
| --- | --- |
| `decision_attempts` | learner, trigger, provider, model, turn/conversation/message/session, prior and upstream links, versions, settings, frozen input, input refs (memory fingerprints, lesson revisions), output, absent, status, error, blob hashes, started/produced/captured/expires |
| `decision_attempt_sources` | `message:`/`fact:`/`item:`/`thought:`/`work:`/`session:` refs an attempt read (for forgetting) |
| `capture_blobs` | exact unchanging text by `(user, sha256)`: system prompts, schemas. Pruned when unreferenced for 1 h |
| `decision_executions` | kind (`effect`, `reply`, `heard`, `channel_delivery`, `skill_run`), executor, intended, executed, result (`succeeded`, `deviated`, `failed`, `unknown`, `not_executed`, `cancelled`, `handed_off`, `not_sent`), deviation, start/end |
| `capture_gaps` | content-free known losses: `queue_full`, `retries_exhausted`, `unserializable`, `source_gone`, `shutdown` |
| `training_examples` (+ `_sources`, `_deletions`) | learning's derivatives; opaque source refs; tombstones |

## Persistence and failure handling

- **Freeze, then queue.** `attempt()`/`execution()` turn their arguments into JSON on the caller's thread, then append to an in-memory queue. They never block and never raise.
- **Writer.** One thread per process saves each record in its own transaction.
- **Settings (defaults).** `LEARNING_CAPTURE_QUEUE_MAX=500`, `LEARNING_CAPTURE_MAX_TRIES=6`, backoff `LEARNING_CAPTURE_RETRY_BASE_SECONDS=0.5 × 2^n`, capped at `LEARNING_CAPTURE_RETRY_MAX_SECONDS=30` (about 30 s of tries in all), `LEARNING_CAPTURE_DRAIN_SECONDS=5` at exit.
- **Gaps.** A full queue, exhausted retries, an unserializable record, a source deleted before the save (foreign key 23503, not retried) and records left at exit all become `capture_gaps`. When the database itself is down, gaps go to `LEARNING_CAPTURE_GAP_LOG_PATH` (`var/learning_capture_gaps.jsonl`).
- **Crashes.** A sudden crash loses the queue without a gap. Reconstruction then shows the missing link.
- **Limits.** No latency guarantee. The writer adds no work to the reply path beyond JSON-encoding the record.

## Reconstruction (`services/capture_chains.py`)

- **`turn(user, turn_id)`.** Attempts and executions of the turn, upstream and earlier tries followed, delivery records joined by message. `missing` lists upstream attempts never saved, an expected learner or reply with no record, and earlier tries. It also returns the turn's gaps, a `leaks` check (any source message an attempt read that was written after it started; must be empty), and the training examples made from it. `complete` is true only with nothing missing and no gaps.
- **`attempt(user, id)`.** One attempt's chain. For her hands, it expects the skill run.
- **API, the user's own records only.**
  - `GET /api/learning/capture`
  - `GET /api/learning/capture/turns/{turn_id}` (`?blobs=true` resolves system texts)
  - `GET /api/learning/capture/attempts/{id}`
  - `GET|PATCH|DELETE /api/learning/training-examples[/{id}]`

## Access: the owner only (decided 2026-11-01)

The user decided that training data is for the owner to train with, and that other users can only talk with
Columbina. So every route under `/api/learning` (captures, training examples, review, deletion, lessons, skills),
`/api/mind` and `/api/memory`, plus the live `thought` socket frames, is for the owner only
(`deps.require_owner`). Any other user gets 404. `OWNER_EMAILS` (comma-separated) names the owner, falling back to
`MINECRAFT_PACK_OWNER_EMAIL`; with both empty, nobody has access. `/api/auth/me` returns `is_owner`, so the app hides
the Learning tab, the Mind panel and thoughts from other users. The routes are the real enforcement.

This changes two points of the 01 decisions:
- **Deleting training data is now the owner's operation, not the user's.**
- **"Private to their user" still holds.** The owner sees only records filed under the owner's own account. Whether
  the owner may also see and train on other users' examples is not decided yet; step 5 decides it.

## Retention, deletion and derivatives

- **Raw retention.** `LEARNING_CAPTURE_RETENTION_DAYS=30` (`expires_at`). The `learning` worker thread prunes expired attempts, executions and gaps every 10 min.
- **Source deletion.** Deleting a conversation, message, game session or user cascades to its raw copies. A forget (`memory_forget._apply`) calls `learning_capture.drop_raw_for` in its transaction, with the forgotten facts, episodes, items, events and fenced messages. A message ref also removes every attempt in its conversation since that message, because a learner's window may have shown it without naming it.
- **Derivation.** While `LEARNING_DERIVE_EXAMPLES` (the user's learning setting, default on) is on, the `learning` worker derives one kind of example: a Student 2 teacher decision that was valid when made becomes an unreviewed `teacher_decision` example (exact rendered prompt and snapshot → the decision). An attempt missing its input or output is never derived. Labels from feedback, attribution and review policy belong to steps 2 and 5.
- **Examples stay.** Source deletion and raw retention never touch examples. Their `training_example_sources` refs are opaque and still match after the source is gone.
- **Corrections.** `memory_corrections._apply` calls `training_examples.invalidate_for_sources` with the revised facts and items and the messages that stated the old claim. The examples become `eligible=false`, `needs_review`, and are kept. A review `accepted` makes one eligible again; `rejected` keeps it out.
- **Explicit deletion.** `DELETE /api/learning/training-examples/{id}` removes the example and leaves a tombstone of ids only, so the attempt is never derived again. It also removes the user's line for that attempt from each export file (`training_examples.exports()`: today the live teacher JSONL). It does not undo influence on models already trained.
- **Reset.** `scripts/reset_memory.py` gains the `capture` (raw) and `training` scopes.
- **Privacy.** Captures and examples are filtered by `user_id` everywhere. Nothing reads across users.

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
| conversation turn end to end | exact Student 1 bundle and Student 2 prompt/system/settings, upstream link, effect and reply records, sources, no later message in inputs, `leaks` empty |
| failures known, unknown stays unknown | Student 1 error, Student 2 invalid output, failed reply, cut-off reply = deviated, interrupted voice = heard, WhatsApp reply = unknown |
| game decision | three skill decisions: succeeded, failed with reason, still running = unknown; system prompt resolved from its blob |
| outage | replies continue with storage down; bounded retries; gaps to the gap file, content-free; nothing shown as a chain |
| full queue, deleted source | `queue_full` and `source_gone` gaps in the database |
| restart and retry | the same attempt saved twice is one row; a reclaimed job is a new attempt linked to the old one; one reply record |
| derived examples | one example per attempt, kept after the conversation (and its raw capture) is deleted; none with the setting off |
| retention and forgetting | expired raw captures pruned, a forgotten fact's raw copies dropped, examples kept |
| correction and deletion | ineligible and `needs_review` after a correction (source already gone), other users can't review or delete, deletion scrubs only that user's export line, never derived again |
| user boundary, API | another user's turn, attempt and examples are 404 or empty; review and delete through the routes |
