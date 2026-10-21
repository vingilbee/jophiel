# Columbina memory — corrections

Status: built, flag-gated (`MEMORY_CORRECTIONS_ENABLED`, off by default), migration `d3f7b1e9a5c2`. Not independently audited. Tests: `backend/tests/test_memory_corrections.py` (real PostgreSQL, scripted models). Code: `backend/app/services/memory_corrections.py`.

Related: `COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md` ("Corrections must reach beyond a new chat note"), the session 1–3 specs, `reports/memory-lab-2026-10-20.md` and `reports/memory-links-trace-2026-10-20.md` (the failure this fixes).

## 1. The problem

In the memory lab, the user said: "I changed the velvet otter marker from a purple square to a red crescent." Semantic consolidation stored a red-crescent fact. Two active working items (a study sub-goal and a "remember" intention) still said purple square, and a fresh chat answered with the old marker.

Each store updated only itself. Nothing carried the correction to the other memories that stated the old value.

## 2. Who owns what

| Step | Owner | Model or code |
|---|---|---|
| Notice that a message is a correction, and read it (subject, detail, old value, new value, kind) | Semantic consolidation (`long_term_memory.process_job`), in the call it already makes | Model |
| Keep the reading only as far as the shown words support it | `memory_corrections.ground` | Code |
| Hand it to the memory manager | `memory_corrections.record`, same transaction as the consolidated facts and the job's completion | Code |
| Decide which current memories state the corrected detail, and which words change | One judging call (`_judge`) over a bounded candidate list | Model proposes |
| Accept or refuse each proposed edit; write it; keep history; link evidence | `check`, `_apply`, `memory_manager.revise_item`, `memory_manager.record_correction` | Code |

The model interprets meaning. Code owns persistence, ownership, revisions, ordering and evidence integrity. There are no keyword rules for spotting a correction: the extraction prompt (`_CORRECTIONS_PROMPT`, added only while the flag is on) asks for explicit corrections, and code checks what the model returned.

## 3. The flow

1. **Reading.** The consolidation model returns `corrections` alongside `memories` (`MemoryExtraction`, `CorrectionCandidate`). `kind` is `changed` (the thing itself changed), `mistaken` (the earlier statement or record was wrong) or `unspecified` (the user didn't say which).
2. **Grounding** (`ground`). The model's reading is dropped unless `user_words` appears in the user's message, the new value's words are in it, and the subject's words were shown (the message, the earlier messages, or the known facts). An `attribute` or `previous_value` the shown words don't support is cleared, never guessed.
3. **Handoff** (`record`). One `memory_corrections` row per correction, in the consolidation job's transaction. It is unique per (user, message, fingerprint), so a re-run job adds nothing. A database trigger requires the source to be a user message in one of the owner's own conversations.
4. **Order** (`next_due`). Corrections apply one at a time per user, in the order the user said them (`said_at`). A correction also waits while any earlier consolidation job of that user is unfinished, because that job may still hold an older correction. The memory worker runs a correction only when no consolidation job is waiting.
5. **Candidates** (`gather`), user-scoped and capped at 24:
   - current facts and open working items whose words include the subject (without the detail's own word: "velvet otter marker" finds "velvet otter … marked with") or the old value;
   - whatever shares their sources through the existing evidence links (other items and facts from the same message, a fact's earlier wordings' sources);
   - their parent goal and sub-goals.

   Closed items, items in closed contexts, and memories that came from the correcting message itself are left out. **Sharing a source only makes a memory a candidate.** It never changes anything by itself.
6. **Judging** (`_judge`, `MEMORY_CORRECTION_MODEL`, else `MEMORY_MODEL`). For each candidate the model says `affected` (with the field, the exact words to replace and their replacement), `unaffected` or `unsure`. Items are given as data, not instructions.
7. **Checking** (`check`). An edit is refused unless all of these hold:
   - the words to replace appear exactly once in that field;
   - the replacement adds nothing but the new value (and small filler words);
   - it removes something, and only the old value when that is known; when the old value is unknown, at most 6 words.

   `unsure`, `unaffected` and refused edits change nothing, and each is kept in the row's `result` with its reason.
8. **Applying** (`_apply`), one transaction under the user's memory advisory lock:
   - each target row is locked and re-read fresh, and its revision must equal the one judged; otherwise the whole correction goes back to pending and is judged again (`_Stale`);
   - an item closed since, or already revised by a later correction, is skipped;
   - items: `memory_manager.revise_item` replaces only that span. Id, kind, owner, status, parent, context and times stay. It writes an `item_revised` event that quotes the swap, plus `correction` evidence links from the user's message to the item and the event;
   - facts: the old wording goes into `metadata.history` (reason `correction`, with the correction id), and the content and embedding are updated;
   - one `corrected` memory event (`describe`), dated when the user said it and linked to their message. The row is marked `applied` with its `event_id`.

A failure releases the row with backoff (30 s, doubling) and becomes `failed` after 4 attempts. A worker whose lease ran out can't commit (claim token check).

## 4. What stays as it was

- **Original events.** `item_added`/`item_changed` events keep their old text. Recall marks an item-wording event recorded before the item's latest `correction` link as `no_longer_current` ("the user corrected it later; it now reads as in [item:…]"), so the past stays findable as the past.
- **Facts' past wordings.** Consolidation now keeps a fact's replaced wording in `metadata.history` too, with reason `correction` when the turn's correction explains the change, else `restated`. It keeps the last 10.
- **Late jobs.** A consolidation job that runs late never overwrites a fact the user stated in a later message (`_said_after`).
- **Unrelated words.** Only the old value's span changes. In the lab case, "Factory and REST", the intention and the broad study goal (which never stated a marker) stay intact.

## 5. Recall

- "What is the current marker?": facts and items carry the new wording. A corrected fact's line says `corrected by the user; it said before: "…"`, and a revised item's says `corrected by the user since it was first noted`.
- "What did I correct?": the `corrected` event ("The user changed the velvet otter marker from "purple square" to "red crescent".") is an ordinary memory event with `correction` evidence to the source message, so search, timeline and `read_memory` return it with its time and source.

No ranking, decay or learning changed.

## 6. Inspection, reset, flags

- `GET /api/memory/working` returns `corrections`: each one's reading, status, attempts, last error, `result` (candidate count, what was revised, what was left alone and why) and its event.
- Decisions are logged to `MEMORY_MANAGER_LOG_PATH` with `"area": "correction"`.
- Reset scope `memory` deletes corrections. Scope `working` deletes their events (the row keeps `event_id = NULL`). Deleting the source message deletes the correction (cascade). Its memory event stays and loses only the evidence link.
- `MEMORY_CORRECTIONS_ENABLED` works only with `MEMORY_MANAGER_ENABLED` on too (the manager owns the events a correction writes). Off: corrections are neither read nor applied, and facts are consolidated as before. History keeping and the late-job guard apply either way.

## 7. Evidence (2026-10-20)

- `test_memory_corrections.py`: 16 passed. The earlier memory suites (`test_memory_manager`, `test_observation`, `test_recall`, `test_memory_review_regressions`) still give 69 passed. Migration upgrade → downgrade → upgrade was clean on a throwaway database.
- Real models, isolated users, db `e2e`, throwaway Redis, flags from env overrides:
  - **Run 1 (natural).** The chat model created no working items this time, so only the fact and the event were exercised. Consolidation rewrote the fact (history reason `correction`). The correction found 0 candidates (the fact came from the correcting message itself) and recorded the `corrected` event. Fresh chats answered red crescent and "purple square → red crescent".
  - **Run 2 (the lab's three working items seeded from the real teaching message).** The real judge revised the sub-goal and the intention, left the broad goal `unaffected` ("does not state the velvet otter marker"), and the creation events kept purple square. Four fresh chats answered correctly: the broad quiz (both cards, red crescent), "current marker", "what did I correct about velvet otter" and "what did I correct recently".

## 8. Limits

- Only corrections in the user's chat messages (anything that schedules a consolidation job) are read. A correction of a game observation, a lesson or an episode recap isn't handled.
- Candidates are found by words, then by shared sources. A memory that states the old value in other words ("the violet box") is found only if it shares a source with a direct match.
- Episodes (`episodic_memories`) and `learning_memories` aren't revised.
- Without event embeddings, "What did I correct?" matches the `corrected` event only when the question names the subject, because lexical recall doesn't stem correct/corrected. In the live run, the small store and recent-event slots found it anyway. That isn't a fix.
- The live runs show the model's reading and judging are good on this case. They're two runs, not a measured accuracy.
