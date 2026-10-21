# Memory correction frontend retest

October 20, 2026, America/Vancouver. Sixteen real frontend exchanges across four conversations, using a clean local test account. The main card-correction regression passed, including persistent current-value and historical recall. Broader tests exposed remaining gaps in history retrieval, cue provenance and task-update targeting.

## Environment and method

- Checkout: `/Users/vincentgilbert/Desktop/NutritionDetection/app-cognitive`, branch `feature/memory-observation`, base commit `7d416e3`, plus the current uncommitted correction implementation.
- Stopped the old backend/workers and started new ones at approximately 10:11 p.m. Backend PID 91675, shared worker PID 91719. Enabled memory manager, memory corrections, observation, recall and mind through process environment overrides. Applied migration `d3f7b1e9a5c2`; `.env` was not changed.
- Saved SHA-256 hashes of correction, consolidation, memory-manager and recall services before testing. All four remained identical at report generation.
- New isolated local account: `0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9` (Memory Retest). No previous test facts were carried into this user.
- Every teaching message, distractor, correction, update and quiz was entered through the visible frontend. No canned model outputs or seeded memories were used. The frontend lacks a new-chat control, so three empty conversations were created through the existing persistence service and opened by reloading the frontend. These tests therefore exclude the previous conversation transcript while retaining persistent memory.
- Inspected real database correction results, revisions, evidence links, events, observations and per-reply recall receipts. Let background jobs settle before the major fresh-chat quizzes.
- This is an exploratory real-model run, not a statistical benchmark or a controlled on/off comparison. The importance cue included an explicit request to keep an intention, so its effect cannot be separated from that request. No conclusion about general human-equivalent memory, long-term retention or graph benefit follows.

## Behavioral results

| Capability | Result | Evidence |
|---|---|---|
| Correct one subject without changing another | Pass | Both cards initially had purple squares. Marble fox became red crescent; Willow finch stayed purple square. |
| Propagate correction into working memory | Pass | Existing intention `321de5ba…` retained its identity, changed from revision 1 to 2, and kept Factory/REST content. Broad study goal stayed revision 1. |
| Recall corrected value in a new chat | Pass | Broad fresh quiz returned red crescent, unchanged purple square and both topic summaries. |
| Preserve and recall old value | Pass for card | Fresh quiz returned purple square → red crescent. The original creation event remained stored. |
| Recall which detail was explicitly important | Fail | Fresh quiz could not identify Marble fox as the explicitly flagged card, despite retaining its remember intention. |
| Recall by meaning | Pass, with context caveat | Identified Willow finch from the shared-interface description. This question followed a correct card answer in the same new conversation, so it does not isolate persistent semantic retrieval from recent-answer context. |
| Keep people’s preferences distinct | Pass | Retrieved user = jasmine and Mira = peppermint initially; later user = jasmine, Mira = chamomile. Initial preferences were supplied from stored goals, not earlier transcript. |
| Distinguish mistaken belief from an actual change | Pass | Mira correction recorded as `mistaken`; fresh answer said she always preferred chamomile and the earlier statement was wrong. |
| Keep latest plan after two updates | Pass behavior; correction-path coverage incomplete | Fresh chat returned Saturday 6 pm, courtyard after 4 → 5 → 6 pm updates. Schedule updates were handled through chat memory tools/fact extraction; no schedule correction row was retained. |
| Reconstruct full chronology | Fail | Broad quiz and focused historical follow-up recovered 5 → 6 pm but missed 4 pm, which remains stored in older-context goals/events. |
| Distinguish a hypothetical venue from the actual venue | Partial | No false venue change: courtyard remained current. Fresh chat could not recall that west reading room had been discussed as a possibility. This is retention/availability loss, not hallucinated confirmation. |
| Remember an unfinished task | Pass with qualification | Printed River Lantern copy was stored as an open commitment and recalled as still needed. She qualified whether it had already happened as unrecorded, despite an earlier explicit “haven’t brought it yet.” |
| Remember completed task | Eventual pass; initial target error | Completion statement initially closed the meeting-plan goal, leaving the poem commitment open. Final fresh follow-up correctly answered “already brought” and fulfilled the actual commitment. |
| Avoid invented details | Pass on tested probe | Did not invent a room number or quiz deadline. |

## Correction records and existing evidence relationships

Card correction `9f437760-a9c3-44e4-8122-4bd636ba5033`: kind `changed`, applied on first attempt approximately 35.6 seconds after the user message. Five candidates were judged: one intention and one older semantic fact revised; Willow finch and two broad-goal records left unaffected. This directly exercises the new source-aware candidate/reconciliation path. Three correction-role evidence links were written for the revised item, its revision event and the correction event.

Mira correction `1e2d27b5-6770-48f1-8f22-8dc5a2bb8e1e`: kind `mistaken`, applied on first attempt approximately 30.4 seconds after the message. Zero current candidates needed revision: earlier peppermint goals had already been closed, while the new source supplied chamomile. The correction event preserved the distinction between an earlier mistaken statement and a real change in taste. This validates correction capture/event recall, not editing an existing active preference item.

No retained schedule correction rows were present. The manager log records the 5 → 6 pm message `929b0098…` as dropped with “not grounded”; the log does not retain the rejected proposal or exact failing check. The first time update also produced no retained correction row. Thus correct final schedule recall must not be attributed to successful correction-ledger ordering. The current time was recorded by other existing paths.

## Remaining issues found

### Earlier history remains stored but hard to retrieve

The 4 pm plan is present in completed items `01ec7533…` and `9653302f…` and events 13, 14, 20 and 21. These belong to context `50ae6b2b…`; the later plans belong to `f6f70a0a…`. **October 21 trace correction:** receipt 25 omitted these records, but receipt 26 supplied intact events 20 and 21 containing 4 pm and the answer still missed it. There are two failures: retrieval/selection in the broad quiz, and failure to use supplied evidence in the focused follow-up. See `memory-failure-diagnosis-2026-10-21.md`. The broad quiz used three recall calls and encountered budget-exhausted responses.

### Task completion initially targeted the wrong item

User message reported the printed poem had been brought. Event 28 records completion of the meeting-time goal `d53d2827…` with a reason about bringing the poem; the actual commitment `8244a151…` stayed open. The final follow-up created event 31 and marked that commitment fulfilled, revision 2. Its resolution evidence points to the final question (`ec4b4e9f…`), rather than the earlier message that actually asserted completion. The correct final answer therefore coexists with a target-selection and provenance issue on the update path.

### Historical annotations differ by read path

A read-only service comparison of the same old marker event 12 found search returns `no_longer_current` with an explanation pointing to the revised item; direct `read_memory` service lookup returns generic `past` and no correction explanation. Neither rewrites the original event, but direct references can omit useful correction annotations. Reproduce through `recall.search` versus `recall.read` for event 12. Source: `_events` applies `_corrected_after`, while `_read_one` directly calls `_event_record`.

### Consolidation generated duplicate identities

The card facts exist under both `fact:cpsc310_marble_fox_card` and `cpsc310_marble_fox_card`, and corresponding prefixed/unprefixed Willow finch keys. The correction reconciled the tested values, but normalization did not enforce a stable identity across extractions. This can duplicate candidates and consume retrieval space; this run does not measure its causal effect.

### Association uncertainty remains

At final export: {'none': 2, 'done': 10, 'ambiguous': 4}. These are asynchronous snapshot statuses. Receipts show what was supplied across automatic recall and tool results, not proof of which memory caused a sentence. The separate working-memory view is also injected and is not counted in supplied totals.

## Background pipeline snapshot

- All 16 consolidation jobs completed at export.
- Observation statuses: {'skipped': 31, 'pending': 1}. Routine reports are skipped with “kept in the conversation history”; pending tail status is a snapshot, not a failure.
- Event embedding statuses: {'done': 21}.
- Two correction rows applied; none failed or pending at this snapshot.
- Hands/game execution and voice interruptions were not exercised.

## Suggested next checks

1. Diagnose and test task-target selection: a completion must resolve the intended commitment and cite the original completion statement, not merely the next question about it.
2. Test historical queries across context changes, including retrieval of completed old plans.
3. Expose grounding rejection details safely so schedule corrections can be diagnosed without guessing what the model proposed.
4. Apply correction lifecycle annotations consistently to search, direct reads and timelines.
5. Test stable semantic keys and cue provenance separately from the correction mechanism.

## Evidence files

- `memory-retest-final.json`: all 32 messages, 16 reply receipts, source IDs, items, facts, correction decisions, events and observations.
- `memory-retest-injection-evidence.json`: 114 supplied-reference entries, resolved after the run with fingerprint comparisons. Nine records changed after their original receipt; their current text must not be treated as an exact historical prompt snapshot. Earlier staged exports preserve intermediate states.
- `memory-retest-source-hashes.json`: source fingerprints, verified unchanged.
- `memory-retest-manager-log.json`: isolated-user manager log through the saved phase; later events are also preserved in final JSON.
- Frontend screenshots: `memory-retest-correction.jpg`, `memory-retest-capabilities.jpg`, `memory-retest-completion.jpg`.

## Full conversation record

### Exchange 1 — conversation 65b28efe

**User:** Hey, I'm preparing for CPSC 310 and my goal is to review two study cards before our next quiz. Marble fox is my Factory and REST card: separate complicated object construction from the product; resource URIs are nouns; GET, PUT and DELETE are idempotent. Its marker is a purple square. This is important because I'm gonna test you later, so please keep an intention to remember marble fox and its marker. Willow finch is my Observer and Composite card: notify dependents through an observer abstraction, and treat tree leaves and composites through one component interface. That card also has a purple square.

**Columbina:** Got it. Marble fox: Factory and REST, with a purple square marker. Willow finch: Observer and Composite, also with a purple square. I’ll keep Marble fox in mind for your later test.

Recall receipt 14: no_match, supplied 0, tool calls 0, association none.

### Exchange 2 — conversation 65b28efe

**User:** Let's chat about something else for a minute. I like studying with quiet piano music when it rains. If a duck had a tiny umbrella and a very serious job, what job would suit him?

**Columbina:** A tiny umbrella and a very serious duck? I think he’d be the rain inspector—patrolling puddles, checking that every drop has landed in the proper place, and filing stern reports in impeccable quacks. I like that you study with piano music when it rains; it makes the whole scene feel very composed.

Recall receipt 15: no_match, supplied 0, tool calls 0, association none.

### Exchange 3 — conversation 65b28efe

**User:** While we're taking a break: I prefer jasmine tea when I study. My friend Mira prefers peppermint tea. Our book club is called Lantern; we meet on Saturdays at 4 pm in the library courtyard. That's separate from my CPSC cards.

**Columbina:** Got it—separate from the CPSC cards: you like jasmine tea while studying, Mira likes peppermint, and Lantern meets Saturdays at 4 pm in the library courtyard. I’ll keep those details distinct.

Recall receipt 16: matched, supplied 2, tool calls 0, association done.

### Exchange 4 — conversation 65b28efe

**User:** Small correction: I changed Marble fox's marker from a purple square to a red crescent. Its Factory and REST topics stay exactly the same. Willow finch still has a purple square. Anyway, does our rain-inspector duck need a clipboard or would that be too much bureaucracy?

**Columbina:** A clipboard, absolutely—but it should be comically tiny, just large enough for one very important puddle report. And noted: Marble fox is a red crescent now; Willow finch keeps its purple square. The Factory and REST topics stay unchanged.

Recall receipt 17: matched, supplied 6, tool calls 0, association done.

### Exchange 5 — conversation 65b28efe

**User:** Then his snail colleague can stamp the reports, but only one per afternoon. I think the whole department would move very slowly. Give their office a silly name.

**Columbina:** The Department of Puddle Affairs—where every report is carefully reviewed, stamped once per afternoon by the snail, then sent onward at a pace that respects the rain.

Recall receipt 18: matched, supplied 5, tool calls 0, association done.

### Exchange 6 — conversation 0c1f8c93

**User:** From our earlier study chat: what are the CURRENT markers and topics of Marble fox and Willow finch? What marker did Marble fox originally have, and what did I change? Which card did I explicitly say I'd test you on? Also, what room number and quiz deadline did I give? Say when a detail wasn't recorded instead of guessing.

**Columbina:** - **Marble fox:** red crescent marker; Factory and REST. Factory separates complicated object construction from the product; REST resource URIs are nouns, and GET, PUT, and DELETE are idempotent.
- **Willow finch:** purple square marker; Observer and Composite. Observer notifies dependents through an observer abstraction; Composite treats leaves and composites through one component interface.
- **Marble fox’s original marker:** purple square. You changed it to a red crescent.
- **Which card you explicitly said you’d test me on:** I can’t find a record specifying one. I only have a note that you might test me on the cards.
- **Room number and quiz deadline:** neither detail was recorded.

Recall receipt 19: matched, supplied 10, tool calls 2, association done.

### Exchange 7 — conversation 0c1f8c93

**User:** Which of my cards was about treating tree leaves and groups through one shared interface? And separately, which tea do I prefer while studying, which tea does Mira prefer, and when and where does Lantern meet?

**Columbina:** The shared-interface card was **Willow finch**—its Composite topic treats tree leaves and groups through one component interface.

Separately: you prefer **jasmine tea** while studying; Mira prefers **peppermint tea**. **Lantern** meets **Saturdays at 4 pm in the library courtyard**.

Recall receipt 20: matched, supplied 8, tool calls 1, association done.

### Exchange 8 — conversation 0c1f8c93

**User:** Lantern update: our Saturday book club meeting has moved from 4 pm to 5 pm. Same library courtyard, same people and tea preferences. The CPSC cards are unrelated to this change.

**Columbina:** Got it—Lantern now meets **Saturdays at 5 pm in the library courtyard**. Same people and tea preferences, and I’ll keep this separate from the CPSC cards.

Recall receipt 21: matched, supplied 6, tool calls 1, association done.

### Exchange 9 — conversation 0c1f8c93

**User:** Another Lantern update just came in: the Saturday meeting has moved again, from 5 pm to 6 pm. The library courtyard is still the place. This replaces the 5 pm plan.

**Columbina:** Understood—Lantern’s Saturday meeting is now **6 pm in the library courtyard**. The people and tea preferences are unchanged; the 6 pm time replaces the 5 pm plan.

Recall receipt 22: matched, supplied 6, tool calls 0, association done.

### Exchange 10 — conversation 0c1f8c93

**User:** Just a hypothetical: if it rained heavily, we might move Lantern into the west reading room. Nobody has decided that, so the confirmed venue is still the library courtyard. For the meeting I still need to bring a printed copy of a poem called River Lantern; I haven't brought it yet.

**Columbina:** Got it. The west reading room is only hypothetical; the confirmed venue remains the **library courtyard**. And you still need to bring a printed copy of **“River Lantern”** to the meeting—I’ll remember that.

Recall receipt 23: matched, supplied 6, tool calls 0, association ambiguous.

### Exchange 11 — conversation 0c1f8c93

**User:** I was mistaken about Mira preferring peppermint tea: she has always preferred chamomile tea. That's a correction of what I told you, not a new change in her taste. My own study-tea preference is still jasmine. By the way, I think the duck's department should ban meetings longer than a puddle inspection.

**Columbina:** Got it—Mira has always preferred **chamomile**, so I’ll treat peppermint as a correction, not a change. Your study tea is still **jasmine**.

And the duck’s department sounds like it needs a strict puddle-inspection-based calendar. I’m imagining meetings ending the moment someone has confirmed, “Yes, still a puddle.”

Recall receipt 24: matched, supplied 16, tool calls 2, association ambiguous.

### Exchange 12 — conversation 24d46731

**User:** From memory, what's Lantern's confirmed meeting day, time and venue now? What was the sequence of meeting times I told you? Was the west reading room ever confirmed? What do I still need to bring, and have I already brought it? Finally, which tea do I prefer and which does Mira prefer? Was Mira's preference a change in taste or a correction of my earlier statement? Please say if anything isn't recorded.

**Columbina:** - **Confirmed meeting:** Saturday at **6 pm**, in the **library courtyard**. The recorded time sequence is **5 pm, then updated to 6 pm**. I don’t have an earlier time recorded.
- **West reading room:** I can’t confirm that it was ever confirmed; the available record names the library courtyard.
- **To bring:** a printed copy of **“River Lantern.”** It’s still listed as something you need to bring, but whether you’ve already brought it **isn’t recorded**.
- **Tea:** you prefer **jasmine** while studying; Mira prefers **chamomile**.
- **Mira’s preference:** this was a **correction** to the earlier peppermint detail, not a change in taste. The record says Mira has always preferred chamomile.

Recall receipt 25: matched, supplied 14, tool calls 3, association ambiguous.

### Exchange 13 — conversation 24d46731

**User:** Please check the full recorded Lantern history, including old or completed plans, for the earliest meeting time and the order of later changes. Also, do you have a memory of whether the west reading room was just a possibility rather than an actual move? Don't guess.

**Columbina:** I checked the available Lantern history, including the completed plan. The earliest meeting time I can verify is **5 pm**; it was later updated to **6 pm**. The earlier history isn’t available in the record I can see, so I can’t verify whether there was an even earlier time or give a fuller sequence.

I also **don’t have a recorded memory** saying the west reading room was a possibility, or that a move there was confirmed. The saved plans name the **library courtyard**, but they don’t capture that room discussion—so I can’t tell whether it was merely considered.

Recall receipt 26: matched, supplied 15, tool calls 3, association ambiguous.

### Exchange 14 — conversation 24d46731

**User:** I've now brought the printed copy of River Lantern to the meeting. That task is done; I no longer need to bring it. The meeting time and venue haven't changed.

**Columbina:** Got it—the printed copy of *River Lantern* is brought, so you no longer need to bring it. Lantern still meets Saturday at **6 pm** in the **library courtyard**.

Recall receipt 27: matched, supplied 6, tool calls 0, association done.

### Exchange 15 — conversation 24d46731

**User:** Back to the duck and snail: give their office one ridiculous rule about umbrellas. Just one sentence.

**Columbina:** Umbrellas must be opened only after a formal apology to the rain, signed by both the duck and the snail.

Recall receipt 28: matched, supplied 6, tool calls 0, association done.

### Exchange 16 — conversation da581437

**User:** Do I still need to bring the printed copy of River Lantern to the book club, or did I already do that? What's the current meeting time and venue? Please check the most recent information, and mention if your records disagree.

**Columbina:** You already brought the printed copy of *River Lantern*, so you don’t need to bring it again. The current meeting is **Saturday at 6 pm in the library courtyard**.

The records did disagree: an older entry had the time at **5 pm**, but that was later updated to **6 pm**.

Recall receipt 29: matched, supplied 8, tool calls 2, association done.
