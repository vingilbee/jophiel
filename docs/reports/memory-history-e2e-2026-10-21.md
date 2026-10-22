# Frontend E2E retest of source-backed memory history

October 21, 2026, approximately 10:28–10:34 a.m. America/Vancouver. **Eleven real frontend exchanges across six chats.**

The new history path recovered the original **4 → 5 → 6 pm** failure and a newly taught **2 → 3 → 7 pm** history. It also recovered the previously missed importance cue and hypothetical venue. The broader E2E still failed: an explicitly acknowledged preference correction disappeared from answerable memory, and current-only recall contradicted a correctly answered task-completion history.

## Environment and method

- Latest local checkout at `b9c665d`, branch `feature/memory-observation`, including the current uncommitted source-backed recall implementation. This identifies local code, not a deployed or remote version.
- API and frontend were initially stopped. Started current source: API PID 1039, shared worker PID 1228, frontend PID 1343. Applied migration `e8b2d6f4a1c7` for reconstruction provenance.
- Process overrides enabled memory manager, memory corrections, observation, recall, `recall_history`, mind, inner state, character and live student reading. `.env` was not changed. Answer model `gpt-6-luna`, reasoning `none`; history reader `gpt-6-luna`, reasoning `low`; consolidation model `gpt-6-luna`.
- Automatic review initially rejected the shared worker because it could consume unrelated queued data. Read-only checks showed zero pending chat, memory or correction jobs; the retry with that evidence was approved. No requested test remains blocked.
- Reused the isolated **Memory Retest** fixture user `0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9`. Lantern and cards exercise existing stored failures; Juniper pottery circle was a new fictional subject. This is not a clean-account test: unrelated prior fixture memories remained available.
- Every teaching message, distractor, update, correction and quiz was entered through the visible frontend. Used its **New chat** control to remove the preceding transcript from fresh-chat quizzes. No memories or answers were seeded through scripts. Scripts inspected and exported the test user's records only.
- All 11 chat jobs and corresponding consolidation jobs completed. Major fresh-chat quizzes followed settled consolidation. Nine service/prompt hashes matched between baseline and final exports. No application code was edited during this retest.

## Behavioral results

| Capability | Result | Observed behavior |
|---|---|---|
| Original Lantern chronology | Pass | Fresh chat returned Saturday 4→5→6 pm, with 6 pm/courtyard current. |
| Original hypothetical venue | Pass | Recovered west reading room as a rain contingency, never an agreed move. |
| Card correction and subject separation | Pass | Marble fox purple square→red crescent; Willow finch remained purple square. |
| Importance-cue provenance | Pass on this question | Identified Marble fox as the card explicitly flagged for a later test. This does not measure whether importance wording improves recall. |
| Original mistaken-versus-changed distinction | Pass | Mira always preferred chamomile; peppermint was the user's mistaken statement. |
| New Juniper chronology | Pass | After teaching and two updates across chats, fresh quiz returned Sunday 2→3→7 pm; 7 pm/north studio current. |
| New hypothetical venue | Pass | Rooftop room remained only a possibility, not an agreed move. |
| Distinct tea preferences after a new correction | **Fail** | User stayed ginger correctly, but Ada was answered as oolong rather than corrected rooibos. Broad history quiz, focused history follow-up and current-only chat all failed Ada. |
| Notebook completion through history | Pass at answer level | Fresh broad quiz said notebook had already been brought. |
| Notebook completion in current-only recall | **Fail** | Independent fresh chat said notebook still needed to be brought. |
| Unknown detail | Pass on tested probe | Did not invent a studio room number. |

## What the new tool demonstrably supplied

**Receipt 30 / reconstruction 1 — original Lantern regression.** One `recall_history` call, status `complete`, 30 passages, depth 3, 25 queries, traversal exhausted normally, zero unavailable sources. The reader took 6.77 seconds; expansion took 54 ms. It cited original user messages for the earliest 4 pm plan, both updates and the west-reading-room possibility. The final answer preserved that history and current value.

**Receipt 31 / reconstruction 2 — cards, cue and Mira.** One history call, 37 passages, depth budget reached at 6. The account was `partial`, but the raw passages still supported the tested answers. The final answer correctly used the importance cue and distinguished Mira from the user. A partial account is not proof that its supported answers failed, or that all relevant history was exhausted.

**Receipt 38 / reconstruction 6 — new Juniper quiz.** One history call, 46 passages, depth 5, traversal exhausted, zero unavailable sources, 70 ms expansion and 6.52 seconds reader time. Its checked steps were 2 pm, 2→3, and 3→7, each backed by user-message spans. The supplied passages also included the actual notebook-completion assertion. The preference-correction message was absent.

Across seven history-reader calls, recorded reader latency was **3.72–6.77 seconds**, median **3.95 seconds**, in addition to search, expansion and final answering. This is reader-call latency, not total frontend response latency. Twenty-one saved citations into original user messages were checked against their stored fingerprints and span bounds. Some paraphrased step values carried lexical warnings (“its value isn't in the quoted words”); these were not dropped. Supporting source text should remain the authority.

## Failure 1: Ada's acknowledged correction did not become reachable memory

The frontend user message `07c8bca2-e17c-43f9-93c7-5382274d59ba` said:

> she has always preferred rooibos, not oolong. My earlier statement was mistaken; her taste hasn't changed.

The immediate assistant acknowledged exactly that correction. Consolidation job `15d348c2-8214-4f13-810f-2f01888b2e37` completed on its first attempt at 17:31:57 UTC with no stored error. Replaying only its source-message selection, without another model call, confirmed it selected the correct correction message and acknowledgment.

Nevertheless, after consolidation settled:

- No retained `MemoryCorrection` row uses that source message.
- No `MemoryEvidence` link uses that message.
- The active preference fact `1640cd5f-afe7-4230-bd42-f75df0ffff2f` remains revision 1: **“Ada prefers oolong tea.”** Its source is the original teaching message.
- The correction is present in the transcript and observation 72. The observation's memory consumer marked it `skipped`, reason **“kept in the conversation history.”**
- The correction message appears in **none** of the saved reconstruction-source rows. In particular it is absent from the broad Juniper history and the focused Ada history.

The broad quiz answered oolong and denied a correction. The focused follow-up repeated the denial. Reconstruction 7 searched broadly, reached 44 passages and the depth budget, but still established oolong from older evidence. The later current-only chat also returned oolong.

This is a consolidation/retention outcome followed by missing source coverage, not demonstrated ignoring of a supplied rooibos passage. Existing-link traversal cannot recover an unlinked correction message solely by following those links. Raw extraction output was not logged, so the run cannot establish whether the model omitted a proposal, returned no parsed extraction, or another extraction decision caused the absence. No grounding rejection for this message appears in the inspected manager log. A `completed` consolidation job proves execution ended, not that the correction was preserved.

## Failure 2: history and current-only answers disagree on task completion

The user explicitly said the glaze notebook had been brought and the task was done, while the Sunday 7 pm meeting remained planned.

The live tool completed composite goal `b20bcc01-14d7-43eb-b68f-1336f077b731`, whose text bundles the meeting, both preferences and notebook obligation. Event 45 records that completion, citing bringing the notebook. There was no separate notebook commitment in the inspected working items. Another goal, `0b4e971f-b38a-458c-947a-962a3f4a528e`, remained active with stale **oolong** and **“still needs to bring their glaze notebook.”**

Receipt 38's history-enabled quiz recovered event 45 and the original completion message and correctly said the notebook was already brought. Independent fresh current-only receipt 40 used zero recall tools, supplied six events **43, 38, 39, 44, 33, 42**, and omitted event 45. It answered:

> You still need to bring your glaze notebook.

The active duplicate provides a stale obligation in the working-memory view, while the automatic recall set misses the completion. This demonstrates both coarse task representation/targeting and current-state consistency failure. A correct reconstructed answer does not repair those stored current views. The meeting fact itself remains correctly current at 7 pm.

## Observation behavior

All 11 user messages and 11 replies produced observations: **22 total**, with all 22 memory-consumer rows `skipped` under the current routine-conversation rule. Ada's correction therefore was captured as an observation, but capture did not make its semantic content searchable or link it into this history path. Observation capture, semantic retention, retrieval and answering need separate acceptance checks.

## Recommended next work

1. Trace consolidation outcomes for explicit corrections: retain proposal counts, parsed/refusal outcome, rejection field/code, source ID and applied target IDs. Use the Ada message as a regression case; do not mark correction success merely because the job completed or the immediate reply acknowledged it.
2. Add an owner-scoped way to find relevant original messages when linked evidence does not establish a requested correction. Report bounded coverage honestly; “not in these passages” does not establish “the user never corrected it.”
3. Separate obligations from composite meeting memories and reconcile duplicate active views when an obligation completes. A task-completion source should resolve the obligation without closing unrelated plan knowledge or leaving another active copy asserting it is outstanding.
4. Require current-only and history-enabled fresh-chat probes to agree on current values. Keep the original Lantern and new Juniper chronology passes as regression cases while fixing Ada and the notebook.

The new source-backed history path helps in these demonstrated cases. This run does not establish general accuracy, causal improvement versus a feature-off control, long-term retention, or human-equivalent memory. No voice or game-driving E2E was run. Application fixes were outside this retest; none were made.

## Evidence

- [All test turns, diagnosis and verified source quotes](memory-history-e2e-diagnosis-2026-10-21.json)
- [Final owner-scoped database export and source hashes](memory-history-e2e-final-2026-10-21.json)
- [Stored state before the quiz](memory-history-e2e-before-quiz-2026-10-21.json)
- [Original Lantern pass screenshot](memory-history-e2e-lantern-2026-10-21.png)
- [Card/cue/correction pass screenshot](memory-history-e2e-corrections-2026-10-21.png)
- [Juniper broad quiz screenshot](memory-history-e2e-juniper-failure-2026-10-21.png)
- [Focused Ada failure screenshot](memory-history-e2e-ada-focused-2026-10-21.png)
- [Current-only failure screenshot](memory-history-e2e-current-only-2026-10-21.png)
