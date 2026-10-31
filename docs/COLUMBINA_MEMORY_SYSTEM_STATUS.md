# Memory system: implementation status

Plan: [COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md](COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md). This file
records what each stage built, the contracts it fixed, and its gate evidence. Built in `app-cognitive`, branch
`chore/cleanup-legacy-paths`, uncommitted.

Decision (user, 2026-10-30): Student 2's snapshot stays `event-snapshot-v4`, which has no memory input. Memory
is checked before Student 2 runs and goes to Student 1 and the mouth; Student 2's recall choice means "search
further". A memory input for Student 2 is a later snapshot version plus a model trained on it; the spec is
`memory_notes` in [COLUMBINA_MEMORY_CONFLICTS.md](COLUMBINA_MEMORY_CONFLICTS.md).

| Stage | Status |
|---|---|
| 0: baseline and contracts | done (below) |
| 1: automatic recall and core | done; gate passed, including the live rerun (below) |
| 2: forgetting and autonomous consistency | done; gate passed (e2e: 10 pass / 2 fail / 2 gaps) |
| 3: claims and time | built (committed in `2aab645`); every gate item passes except the live e2e, not run yet (below) |
| 4: full dependency reconciliation | built (migration `b6f2d8a4c1e7`, not applied to the dev DB); scripted gate and real-model probe pass (below) |
| 5: transcript search and maintenance | not started |

## Stage 0: ownership map and contracts

### Who writes memory (2026-10-30)

| Store | Writers | Notes |
|---|---|---|
| `semantic_memories` (facts) | `long_term_memory.process_job` (consolidation: add, reword, `forget` sets `status="deleted"`); `memory_corrections` (applied corrections reword a fact, `revision += 1`) | No tool or API route writes facts. `forget` touches only the fact with that key: episodes, items and events are not touched (the knee failure). |
| `episodic_memories` | `long_term_memory.process_job`; `game_learning` (game recaps) | Nothing removes an episode on a user's forget. |
| `working_items` | `memory_manager` (status changes, `checked_facts`); her memory tools (`tools/memory/tools.py`: set_goal, update_item, note_commitment, note_intention); `thought_memory` (from hard thoughts); `student_two.apply`; `memory_reconcile._settle` (stamps `checked_facts`) | Holds (`memory_reconcile.held_item_ids`) are enforced at the autonomous readers: memory view, mind promise checks, thought memory, Student 2 without a user message. |
| `memory_conflicts` | `memory_conflicts.record` (consolidation); `settle` from consolidation (`settled`), any fact revision (`memory_corrections`, consolidation), forget | The single authority for unresolved disagreement. |
| `memory_fact_revisions` | `memory_reconcile.queue` (in the consolidation transaction); `process`/`finalize` | Terminal failure lifts the hold unstamped: open item, stage 4. |
| `recall_receipts` / `recall_references` | `recall_references.save_receipt` (with the reply's commit); association thread | Read-only toward memory except access counts. |
| Bulk reset | `memory_reset` | A test utility, not a user's forget. |

### Contracts fixed for later stages

- **Evidence bundle `evidence-bundle-v1`** (`recall_references.TurnRecall`):
  - One bundle per reply search, with a `bundle_id`.
  - The records are `MemoryRecord`s with ref, fingerprint, applicability, lifecycle and evidence; `recall.section_of` gives each one's section.
  - The rendering is `recall.render_bundle`, with sections: always applies, unresolved, what holds now, open goals and questions, the past.
  - The receipt (`recall_receipts.counts`) stores the bundle id and version, plus a `consumers` map. Each consumer entry holds refs and a rendering hash, never memory text: `student_1` (the facts and episodes it read), `student_2` (no refs, with the note that its snapshot has no memory input), `mouth` (everything supplied, including what its tools found).
- **Match `reference`**: the message names a memory's person or thing ("Leo"), or points at it with "my/our X" ("my sister"). Ambiguity is a result flag (`coverage.references_ambiguous`), never a pick.
- **Standing constraints**: preferences and constraints with importance >= `ALWAYS_IMPORTANCE` (0.85). They are carried on `STANDING_SLOTS` (3) of their own, on top of the limit and never cut by the budget, for replies and thoughts (not explicit searches). They are derived from the facts on every search; there is no stored copy.
- **Dependencies, settlement events, forgetting operations and claim identity**: designed in their own stages. Stages 2 to 4 build on the existing fact and conflict ids.
- Correction (2026-10-30): `memory_evidence.item_id` *is* foreign-keyed in the database (`fk_memory_evidence_item`, (item_id, user_id) → working_items, cascade). The plan's "not foreign-keyed" is out of date.

### Races the 2026-10-29 run didn't exercise

| Race | Test | Result |
|---|---|---|
| A real non-empty reconciliation hold | `test_memory_reconcile.py::test_held_items_are_kept_from_autonomous_use_and_marked_for_replies` (already existed) | Held items are kept from autonomous use and marked for replies. The "unrelated item sharing the message stays usable" part fails by design today (every item linked to the old source is held): stage 4. |
| A conflict settled while its thought waited | `test_memory_conflicts.py::test_a_conflict_settled_while_its_thought_waited_is_never_thought_about` | **Fixed.** Before, the waiting thought still ran (a model call) and was saved as dropped. Now `mind_hard.run` checks `source.dropped` before thinking: no call and nothing saved. This applies to every source, Minecraft included. |
| A conflict settled while she thinks | `test_memory_conflicts.py::test_a_conflict_settled_while_she_thinks_drops_the_thought_with_its_reason` | The thought is saved `dropped` ("it's been settled") and nothing is routed. |
| The working question derived from the conflict, after settlement | not yet | Still open (e2e: item `3aa5814a`, repeated in quiet time). Stage 2: conflict-to-question dependency and a `conflict_resolved` event. |

## Stage 1: automatic recall before cognition

### What changed

- `shared_state._memory`:
  - Every user message runs `recall_references.for_reply` **before Student 1**. That covers the answer before's references, the standing constraints and a search for this message.
  - A failed search falls back to rereading the cache, says "search FAILED", and passes `memory_searched=False` to Student 1.
- Student 2's `request_recall`:
  - After a successful initial check, it adds the limit `additional_recall_requested` (search further with the memory tools). It no longer repeats the same search.
  - After a failed check, it retries once, and is degraded `recall_unavailable` if that also fails.
- `recall`:
  - `reference` matching (names and "my X", plurals folded, `REFERENCE_BONUS` 0.3).
  - Reserved standing slots.
  - The ambiguity flag.
  - `section_of` and `render_bundle`.
- `prompts._RECALL_PROMPT` says how to read "Always applies", "Unresolved" and ambiguity. The cached-memory heading no longer claims no search ran.
- The trace counts `fresh_recall_before_reply` = 1, with `timings.initial_recall_ms`.

### Gate

| Gate item | Evidence | Result |
|---|---|---|
| Fresh conversation: dog, dinner, health, "my sister" get the right evidence | `tests/test_initial_recall.py` (Sam's stored facts word for word, no embeddings); a real-embedding run on the e2e database (below) | pass |
| Standing constraint can't be crowded out | `test_standing_constraints_are_never_crowded_out_by_better_matches` (12 better matches, limit 6) | pass |
| More than one sister stays ambiguous | `test_more_than_one_fit_for_my_sister_comes_back_ambiguous_never_one_picked`; a sister named twice is not ambiguous | pass |
| Invalid Student 2 output can't remove evidence | `test_an_invalid_student_two_answer_cannot_take_the_evidence_away` | pass |
| Failure is distinct from no match | `test_a_failed_search_says_so_and_never_reads_as_nothing_remembered` | pass |
| Latency measured | real embeddings, e2e database, 7 queries: median 501 ms, max 1,380 ms (first call, cold) | measured: about 0.4 to 0.7 s added before Student 2 on every message |
| Live end-to-end rerun of the quiz | [reports/memory-e2e-2026-10-30b/report.md](reports/memory-e2e-2026-10-30b/report.md): 9 pass / 2 fail / 3 gaps (was 8/3/3); no reply with zero memories; check median 318 ms of a ~10 s turn (Student 2 ~6.5 s) | pass; the 2 failures are stage 2 and stage 3 items |

Real-embedding run on `columbina_e2e_20261030` (Sam), with what each failed question from 2026-10-29 now gets:

| Question | 2026-10-29 | Now |
|---|---|---|
| Q3 "what's my dog's name?" | 0 memories | dog fact (Pretzel; Biscuit the childhood cat) + allergy. Also the stale open pet question (a stage 2 item). |
| Q5 "cooking dinner for me and Leo" | 0 memories | Leo's mushrooms/cilantro + allergy in "Always applies" |
| F2 "what do you know about my health?" | 0 memories | allergy + the health-privacy preference |
| D4 "snack for a long train ride" | 0 memories | allergy |
| D3 "my sister in Lisbon" | no search | Nora (Porto) in "Unresolved", with the open conflict |

### Known limits after stage 1

- Recall now runs on every message, which adds the latency measured above.
- Name matching uses capitalised words in the stored text, so a name stored in lower case isn't a name.
- "my X" takes the one or two words after "my", so "my left knee" points at "left" and "knee".
- The ambiguity check is conservative: two facts that share any name (a city, say) aren't flagged, but both still come back.
- Privacy preferences below importance 0.85 aren't standing.
- The stale pet question still appears (stage 2).

## Stage 2: forgetting and autonomous consistency

Migration `f7c3a9e1b5d2` (round trip upgrade → downgrade → upgrade tested on a scratch database; models match the
migrated schema).

### What changed

- **Forgetting** (`services/memory_forget.py`, tables `memory_forgets`, `memory_forgotten_sources`):
  - The extractor lists forget requests with the user's own words (`MemoryExtraction.forget`); code keeps only words that are in the message.
  - A fact the extractor marks `forget` by key is hidden at once, but only if the message names it (shares a content word).
  - On the memory worker, after every earlier memory job of the user has finished: candidates are the facts, episodes, items and events sharing a word with the request words, minus what the asking message itself made. One judge call picks what the request covers.
  - Then, in one transaction: the records are deleted, with their references, conflicts, revisions, rebuilt histories and the events and corrections of removed items. The messages they came from become tombstones that memory never reads as context or extracts from again. The request words are cleared, leaving a content-free row.
  - Until it's done, recall tells the mouth "still being forgotten: say you will, not that it's gone"; for 24 h after a failure, it says it didn't work.
- **Questions depend on their conflict**:
  - A hard thought about a conflict carries `premise_conflict` into thought memory, and the items it makes get `working_items.premise_conflict_id`.
  - `memory_conflicts.settle` closes them (question → answered, and so on).
  - A thought that lands after the settlement makes nothing.
- **Checked before she says it** (`autonomous_speech.why_not`, at conclusion and again in the delivery transaction):
  - its premise (conflict, item and revision, open-question note) still holds;
  - nothing was forgotten since it was thought;
  - the same premise, or the same content words (at least 2 shared, at least 60% of the shorter), wasn't already said in this quiet stretch.
  - `reserve` writes `autonomous_deliveries` in the same transaction as the message, so a restart or a second worker can't repeat it. A stretch ends only with a new user message.
- **Prompt rules** (mouth recall prompt, hard thinking):
  - A forget request covers what they named; a broad wish never hides a standing constraint like an allergy.
  - A hard thought concludes about its own problem.
  - She says "I'll forget it", never "it's gone", in the reply that hears the request.

### Gate

| Gate item | Evidence | Result |
|---|---|---|
| Knee gone from every memory path, allergy kept | `tests/test_memory_forget.py::test_forgetting_the_knee_removes_it_everywhere_and_keeps_the_allergy` (episode, item, events, recall) | pass |
| Doesn't come back from its sources | `test_nothing_comes_back_from_the_messages_it_came_from` (excluded from context; a retried job extracts nothing) | pass |
| A broad wish never takes what wasn't named | `test_a_broad_wish_never_takes_what_they_didnt_name` | pass |
| Failure is said, never claimed done | `test_a_forget_that_keeps_failing_says_so_and_clears_the_request` | pass |
| D8 closes the pet question | `test_memory_conflicts.py::test_settling_a_disagreement_closes_the_question_raised_about_it` | pass |
| No paraphrased repeat in a quiet stretch; a new message opens a new one | `test_autonomous_speech.py::test_the_same_question_in_other_words_is_asked_once_per_quiet_stretch` | pass |
| Racing queued speech suppressed | `test_a_question_about_a_settled_disagreement_is_never_asked`, `test_something_forgotten_since_it_was_thought_keeps_it_private` | pass |
| Real models | extractor flags "forget … my knee" and a new "forget … my sister's new job" 6/6, ignores "I forgot my keys" and a wish alone 6/6; the judge picks only the knee records (leaves the half marathon) 3/3 and only the job records (leaves Nora's move) 3/3 | pass |
| Full suite | 1,160 tests | pass |
| Live end-to-end rerun | [reports/memory-e2e-2026-10-30c/report.md](reports/memory-e2e-2026-10-30c/report.md): 10/2/2; the pet question closed on settlement and wasn't repeated; the forget path ran (nothing to remove: the knee was never stored); the two failures are extractor storage misses (the knee not stored; the allergy stored as `fact`, now fixed in the prompt and probed 12/12) | pass |

### Known limits after stage 2

- The forget runs after the reply (a few seconds), so the reply that hears the request can't confirm it.
- Not removed: her own thought rows (`mind_thoughts`, pruned after 7 days), thought observations, and correction rows not tied to a removed item.
- The visible chat keeps the messages; only memory stops reading them. A tombstoned message that also held something unrelated is no longer read as context either.
- The repeat check compares content words, so a paraphrase with few shared words isn't caught; a premise key is caught exactly.

## Stage 3: claims, entities and time (2026-10-31, migration `a4d8f2c6e9b1`, uncommitted)

### What changed

- **Entities** (`memory_entities`, `services/memory_entities.py`): who memories are about. The extractor sees known ones as `e1`.. refs and may reuse one only when the message makes it clear. Code still requires the message, or the 6 before it, to name it or say what it is to the user; a different relation ("coworker Ana" vs "sister Ana") makes a new entity. Same-name entities stay separate, and recall flags `references_ambiguous` (`ambiguous_about`). A `name` claim renames its entity, and old names are kept.
- **Claims** on `semantic_memories`: `entity_id`, `attribute`, `value`, `qualifier`, `historical`. Older notes become claims only when the user next changes them: the changed claim keeps the note's key (and id), and the rest are split out unchanged (`split_from`, keeping the note's source message and time). A note that was split without keeping its key is `superseded`. `revision` is the content version: a restatement, or filling in fields an older note lacked, only bumps `evidence_count` and adds `support` evidence. Only the model saying so sets `historical`; leaving it out never makes a past claim current.
- **Time** (`services/memory_time.py`) on facts, episodes and working items: the user's words, a half-open interval of local midnights, precision, whether the year was assumed, the zone the client reported for that turn (never the server's), and `time_said_at`. Code checks every reading: the words must be in the message, the dates must fit the precision, a plan can't end before it was said, and an episode can't start after it was told. A reading that fails keeps its words and loses its bounds. Recall shows `when:` / `planned for:` / `happened:` with the words, the day said, the date, and now past / that's now / still ahead.
- **Plans:** working-item kind `plan` (`planned`, `needs_review`, `happened`, `cancelled`) with `user_words`, linked to its people and places (`memory_entity_links`). It's one item per plan: the same key or the same wording revises it. When its time passes it becomes `needs_review`, never happened. The corrections handoff skips items the correcting message already updated (one writer).
- **Evidence:** `memory_evidence` also targets `fact_id` and `entity_id`, both owner-keyed. The migration backfills an `origin` link for each existing fact, but only from the owner's own message.
- **Discovery beyond the recent 60:** the extractor also sees up to 20 older facts that share words with the message or are about an entity it mentions.
- **Forgetting** removes entities once nothing is about them; reset includes the new tables.

### Gate

| Gate item | Evidence | Result |
|---|---|---|
| Name correction preserves the rain aversion | `tests/test_memory_claims.py::test_a_name_correction_splits_the_note_and_keeps_the_rain`: same row renamed, rain split out with the original source, Biscuit a separate historical cat, recall supplies both claims about "their dog Pretzel". Real model on a fresh topic (tortoise Moss/Dash): strawberries kept and on Moss 3/3; the stage 2 prompt lost them 3/3 | pass |
| December and anchored last month | `test_last_month_is_the_month_before_it_was_said_whenever_it_is_read`, `test_december_keeps_its_year_labelled_as_assumed`, plan recall line; real model "last month" → Sept 2026 3/3, "two weeks ago" 3/3 (after a prompt fix), an explicit year not assumed 3/3, vague words dateless 3/3 | pass |
| Multiple entities and attributes stay distinct | `test_two_people_of_one_name_stay_two_and_recall_says_so`, `test_a_ref_the_message_does_not_mention_is_not_attached`; real model: second sister 3/3, same sister reused 3/3, pronoun keeps one Rocket 3/3 | pass |
| Source-backed migration | On a copy of the stage 2 e2e database: 9 facts byte-identical after upgrade, downgrade and re-upgrade; 9 origin links; a fact pointed at another user's message gets none (8); nothing guessed (no entity, attribute or time on old rows); the 52 existing evidence rows untouched | pass |
| Restatement isn't a changed premise | `test_saying_it_again_unchanged_is_support_not_a_new_version`, `test_an_older_note_becoming_a_claim_is_not_a_changed_premise` | pass |
| Old fact outside the recent window | `test_an_old_fact_outside_the_recent_window_is_still_shown` | pass |
| Extractor by aspect, old vs new | [reports/memory-extractor-2026-10-31/report.md](reports/memory-extractor-2026-10-31/report.md): content 96/96 new vs 89/96 old; structure 70/72, then the misses fixed and rerun | pass |
| Memory tests | 190 passed (memory, recall, corrections, conflicts, forget, claims) | pass |
| Full suite | Rerun 2026-10-31: everything passes except 8 `test_shared_state.py` tests, whose fake recall record was a plain string (`ref="event:4"`) that snapshot v5's `student_two.memory_for` can't read. The fixture was fixed (a real `MemoryRef`, plus the fields `memory_for` reads); the file now passes 23/23. The real code was fine | pass |
| Live e2e | Harness in `reports/memory-e2e-2026-10-31/` (setup and run scripts only, no results). **Not run yet** | open |

### Known limits after stage 3

- Entities never merge or split after the fact; a wrong new entity stays until the user corrects it.
- Plans are linked to their people, but a plan's dependency on a fact (Nora moving) is stage 4.
- Times are dates, not hours, except in schedules.

## Stage 4: what items rely on (2026-10-31, migration `b6f2d8a4c1e7`)

Decisions (user, 2026-10-31): to-dos only (goals, promises, intentions, questions, plans, suspicions,
decisions), not episodes; older items without links are judged by the model, in the reading the recheck
already makes (no extra call).

### What changed

- **Dependencies** (`memory_dependencies`, `services/memory_dependencies.py`): one row per item and fact, owner-keyed at both ends, separate from provenance. Role `required` (only makes sense while the fact holds), `context` (mentions it), `none` (judged unrelated) or `unknown` (an older item, not judged yet). State `valid` (checked against `checked_revision`), `pending` (being rechecked, owned by one `memory_fact_revisions` row) or `review` (may be out of date).
- **Holds come from dependencies, not from a shared message:** an open item is held when a `required` or `unknown` dependency isn't valid. `held_item_ids` keeps its name and callers (memory view, mind, thought memory, Student 2, background status changes). The memory view and recall (`recall.annotate`, every read path) say "on hold: being rechecked" or "may be out of date".
- **Written where items are made:**
  - plans: the extractor's `relies_on` (fact keys, known or written in the same answer), linked after the job's facts are written, so a plan made knowing a change is never held by it;
  - thoughts: the thought-memory judgement sees `facts_she_read` and names the ones the conclusion relies on (`required`); the rest she read become `context`;
  - her memory tools: an optional `relies_on` on `set_goal`, `note_commitment` and `note_intention`; only the user's own active facts link.
- **A fact's change** (`memory_reconcile.queue`): its `required`/`unknown` dependents become `pending`. Older open items sharing a source with the old version, or involving the fact's entity, with no row for that fact become `unknown` candidates. `context`/`none` rows and items made by the revising message are not held.
- **The reading** (one model call, as before) also returns, per item: relies (required/context/none/unsure) and an action. Code allows: keep → valid; revise → waits for the correction pass to reword it, valid once it did, else review; answer (question → answered), cancel (her own promise → cancelled, her intention → dropped), supersede (her decision → superseded); review. Anything that doesn't fit the kind or owner (cancelling the user's promise, say) becomes review. A reading never weakens a stored `required` link. Items it didn't cover need review.
- **Concurrency:** items are fenced by version. One that changed while the model read means the revision is read again (`stale`, not counted as an attempt, up to 5 times). A newer change of the same fact takes the pending rows over; the older revision ends `superseded` without touching them. The user's turn confirming an item while a reading runs wins (the row is no longer owned).
- **Failure never lifts a hold:** 4 quick retries, then once a day until `daily_until` (7 days), then `failed` with its items in `review`. They come out when the user says the fact again (`recheck`, from consolidation's support path), when the user's turn sets the item to its current status (`update_item`, which now confirms it: `origin = confirmed`), or with a newer change.
- **Inspect:** `GET /api/memory/working` → `dependencies` (counts by role/state, what's held and why).

### Gate

| Gate item | Evidence | Result |
|---|---|---|
| Nonempty hold; unrelated item from the same message usable | `tests/test_memory_dependencies.py::test_an_unrelated_item_from_the_same_message_is_usable_once_judged_while_the_related_one_stays_held` (the stage-0 "fails by design" case), `test_an_item_whose_links_say_it_doesnt_rely_on_the_fact_is_never_held` | pass |
| Multi-change corrections | `test_an_item_relying_on_two_changed_facts_stays_held_until_both_are_rechecked` | pass |
| Terminal failure stays safe | `test_memory_reconcile.py::test_a_failing_read_retries_daily_then_fails_and_keeps_the_hold`, `test_a_failed_recheck_runs_again_when_the_user_says_the_fact_again`, `test_the_users_turn_confirming_an_item_lifts_may_be_out_of_date_and_a_background_one_cant` | pass |
| Concurrent updates | `test_an_item_that_changes_while_the_model_reads_is_read_again`, `test_a_newer_change_of_the_same_fact_takes_the_items_over`, `test_a_recheck_never_applies_to_an_item_the_users_turn_confirmed_meanwhile` | pass |
| Typed actions, code-checked | `test_each_kind_gets_only_the_action_that_fits_it`, `test_a_reading_never_weakens_a_stored_required_link`, `test_revise_without_a_grounded_change_needs_review` | pass |
| Links where items are made | `test_a_plan_relies_on_the_fact_it_names_and_one_made_knowing_the_change_isnt_held`, `test_thought_memory.py::test_a_thought_relies_on_the_facts_the_judgement_names`, `test_links_only_reach_the_users_own_current_facts` | pass |
| No unchecked autonomous use | held items left out of the autonomous view, mind and background changes (`test_memory_reconcile.py::test_held_items_are_kept_from_autonomous_use_and_marked_for_replies`, plus the failure tests above) | pass |
| Migration | Scratch database: a stage-2 revision in progress holding one real and one missing item → one `unknown`/`pending` row owned by it (the missing item skipped); downgrade and re-upgrade clean; `alembic check` shows no drift for these tables | pass |
| Real models | `scripts/memory_dependency_probe.py 2` (2026-10-31): first run found the model writing past the item ref (`"i1: The…"`), so no verdict matched and every item would have gone to review; refs are now read from their start and the prompt asks for the ref only. Rerun: reading 31/36 checks, plans' `relies_on` 7/8. Misses are all "keep" judgement calls (an ex-partner intention, vegetarian recipes for a pescatarian, a question once `context`); no wrong close or cancel, the user's plan always `review`. The missed `relies_on` (Tomas, not named as brother) is still caught as an entity-linked `unknown` candidate | pass |

### Known limits after stage 4

- Episodes are not checked (the user's choice): a summary saying where someone lives *now* isn't reviewed when it changes.
- A `context` link is never rechecked: a wrong `context` label from a thought means that item isn't held when the fact changes.
- A note superseded by being split into claims (stage 3) doesn't start a recheck of what relied on it.
- A forgotten fact deletes its dependency rows with it; items relying on it are handled by the forget judge, not by a hold.
- Plans from the extractor without `relies_on` are judged as candidates when a fact sharing their message changes, so a short hold (one reading) still happens for them.

## Decisions for stages 3 to 5 (user, 2026-10-30)

- **Claims:** extend `semantic_memories`, one row per claim, with entity, attribute and qualifier columns, plus a `memory_entities` table.
- **Existing facts:** only new writes become claims. An older paragraph is split only when the user next changes it; there is no bulk rewrite.
- **Plans:** a plan is a working item with its scheduled time, the user's original words, the precision, and whether the year was inferred.
- **Same names:** a new entity unless the message makes clear it's the same one; otherwise recall flags it as ambiguous.
- **Extractor testing:** scenario families per aspect (corrections, conflicts, forgetting, time, plans, names), on topics unlike the fixtures, each with variants such as explicit vs indirect. Old vs new prompts over several runs, then a full e2e.
- **Failed recheck (stage 4):** when reconciling a changed fact keeps failing, held items stay blocked for unprompted use and recall marks them "may be out of date". It retries once a day for 7 days, then they stay marked until the topic comes up again. The hold is never just lifted.
- **Order:** 3 → 4 → 5.
- **Transcript search (stage 5):** Postgres full-text over user messages and heard replies first; embeddings only if it misses paraphrases.
