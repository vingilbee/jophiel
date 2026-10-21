# Memory failure diagnosis: stored 4 pm plan and related retest failures

October 21, 2026. Read-only diagnosis of the October 20 frontend retest. No application code, database records or schema were changed. The four service hashes still match the tested source. Existing exported receipts were compared with live records; ranking controls were diagnostic replays, not new chat tests.

## Main finding: the 4 pm failure has two stages

The initial broad quiz missed 4 pm because its supplied memory set omitted the old records. The focused follow-up was supplied two intact passages containing 4 pm and still denied an earlier time. Therefore the follow-up is not explained by a retrieval miss alone. A design that retrieves more records without checking how the answer uses them does not address the full demonstrated failure.

| Stage | Broad quiz, receipt 25 | Focused follow-up, receipt 26 |
|---|---|---|
| 4 pm exists in storage | Yes | Yes |
| 4 pm supplied during generation | No | Yes: events 20 and 21 |
| Final answer includes 4 pm | No | No |
| Failure classification | Retrieval/selection miss | Failure to use supplied historical evidence |

Both answers said the earliest verifiable time was 5 pm. The complete sequence in the user's original messages is 4 pm → 5 pm → 6 pm. We cannot infer the model's internal reason for ignoring the supplied evidence; the trace proves availability and the contradictory output, not its hidden reasoning.

## 1. Storage and provenance

The original 4 pm plan is stored in:

- Item `01ec7533-ee81-4f80-8318-0654e16b47b2`: remember Lantern meets Saturdays at 4 pm, courtyard, alongside tea preferences.
- Item `9653302f-c2c2-4b9f-8c70-b25a4afe6e70`: another goal containing the same 4 pm detail.
- Creation events 13 and 14; subsequent status-change events 20 and 21.

These belong to context `50ae6b2b-a485-467e-bed9-f5c98fb31ad3`, **General**. The later 5 pm and 6 pm plans belong to context `f6f70a0a-8f57-4570-8f83-f212cd0e6a26`, **Lantern book club**.

The old items were marked completed when the plan changed. Their content and history were retained. This is not deletion, a missing embedding for the original event, or absence of a historical record. Merely closing an old plan should not make its former time unknowable.

## 2. Broad quiz: where the evidence dropped out

Receipt 25 supplied fourteen unique records across automatic recall and tools. None was event 13, 14, 20, 21 or either old 4 pm item. The result was `matched`, not a search failure. Three recall calls were used; additional attempts returned `budget_exhausted`.

The source provides two mechanisms:

1. Context matching uses context labels/source names, not all subjects mentioned in their contents. “Lantern” names the later Lantern context, while General has no matching name. `recall._rank` adds **+0.5** for events/items in the named context. Closed historical items receive a small ranking penalty and have no stored semantic vector in the item adapter.
2. An explicit context parameter becomes a hard filter for event/item records; `read_timeline` is likewise confined to one context. An earlier statement made before the subject received a named context can therefore fall outside the requested timeline.

### Controlled ranking diagnostics

`memory-failure-ranking-trace-2026-10-21.json` records controls using the original broad question and original time, the settled snapshot's candidate IDs/item states, and a fresh embedding for the same text. No memory updates were committed. This is not a byte-for-byte replay of historical tool arguments: those arguments were not persisted.

- Automatic six-result lookup reproduced the same six leading IDs as the broad answer's initial supplied prefix. The four 4 pm events ranked **13, 14, 15 and 16**, below the six-result cutoff.
- An unconstrained twelve-result historical tool search selected all four old events after item-echo deduplication. The evidence is retrievable without naming the old value in the query.
- The same query with explicit `context='Lantern'` admitted **none** of those four events: they belong to General.
- Disabling only the named-context bonus in a diagnostic control moved the 4 pm events to ranks **1, 2, 4 and 5**.
- An event-only twelve-result historical search also returned all four.

This demonstrates ranking/context sensitivity in the current code. It does not prove which exact context argument the model used in every historical tool call, because the saved receipts aggregate records and counts, and the logs omit query/filter payloads. Increasing limits globally or removing context scoping is not established as the correct general fix by these controls.

## 3. Focused follow-up: evidence arrived, answer still failed

Receipt 26 supplied fifteen records, including these fingerprint-matched passages:

**Event 20**

> Goal completed: Remember the book club Lantern meets Saturdays at 4 pm in the library courtyard; the user prefers jasmine tea when studying and friend Mira prefers peppermint tea (User updated the Lantern meeting time to 5 pm; same place, people, and tea preferences.)

**Event 21** also explicitly states that Lantern meets Saturdays at 4 pm in the old goal text.

These two passages' fingerprints match their receipt references, so this finding does not rely on text that was edited after generation. The focused answer nevertheless said:

> The earliest meeting time I can verify is 5 pm; it was later updated to 6 pm.

The answer also claimed earlier history was unavailable. That is inconsistent with event 20, which supplies both the earlier 4 pm value and its replacement by 5 pm. The evidence was embedded in prose about goal lifecycle rather than exposed as a structured meeting-time transition. That packaging is a plausible usability problem for the model, not proof of its internal cause.

The design needs an answer-level historical coverage check as well as retrieval: when supplied evidence contains a prior value and replacement, an answer must account for them or explicitly explain a genuine conflict. It must not label the prior value unavailable merely because it is no longer current.

## 4. Existing relationships already reach the old plan

A read-only owner-scoped traversal found this path:

`6 pm item d53d2827…`

→ origin evidence → `5→6 update message 929b0098…`

→ resolution evidence → `5 pm item 17201ff3…`

→ origin evidence → `4→5 update message 8b722942…`

→ resolution evidence → `4 pm item 01ec7533…`

The update messages themselves explicitly say “from 5 pm to 6 pm” and “from 4 pm to 5 pm.” This is four existing evidence joins across two update messages; the same memory subject persists across a change of attention context. Full IDs, role directions and source text are in `memory-failure-evidence-path-2026-10-21.json`.

Current recall does not perform this reverse-source expansion. `_with_evidence` attaches source IDs to returned records; it does not search other targets sharing those sources. The relationship-based selection logic mostly deduplicates item-echo events, and previous-answer references anchor later searches. Neither assembles this plan history.

This demonstrates a concrete use for existing relationships without adding a graph database. Generalizing it still requires bounded traversal and subject/property validation: records sharing a source are candidates, not automatically the same claim.

## 5. Other demonstrated failures

### A. Importance-cue provenance was lost from the answerable representation

The original teaching message explicitly flagged Marble fox for a later test. The stored fact became a broad “may test me on the cards” statement, and the remember intention retained the card/marker but not that exact explicit cue. Receipt 19 contained the broad statement and card memories, yet the answer could not identify the explicitly flagged card.

The original message remains reachable through origin evidence. But `read_memory(include_evidence=True)` attaches evidence objects and then calls `recall.render`; `_record_line` does not render those source IDs, roles or source text as a usable source-reading interface. It mainly renders lost-source/correction annotations. Source provenance exists in storage but is not sufficient for this question through the present memory tools. No measured cue-on/off recall benefit is established.

### B. Hypothetical venue: not retained in the recall stores inspected

The user's message said west reading room was only a possibility and courtyard remained confirmed. No fact, working item or memory event in the final export contains west reading room. A live check found zero episodic rows for this test user. The exact discussion remains in the old transcript; observations record routine message metadata and skip promotion to semantic events. Current memory tools do not fetch the earlier conversation transcript as a fallback.

The fresh answers safely avoided declaring the room confirmed, but could not recall it had been considered. This is a representation/retention gap in the searched memory stores, not demonstrated bad ranking of an existing hypothetical-venue memory. Extraction proposals were not captured, so whether the extractor omitted it or another decision discarded it is unknown.

### C. Completion: wrong target accepted, then source provenance weakened

The actual completion assertion is message `aee53ed8-6e3f-4c4d-bbdb-b98788cb0184`: the user had brought the poem copy and the task was done. Event 28 instead marked the **6 pm meeting-plan goal** completed, citing bringing the poem. The poem commitment `8244a151…` stayed open.

Receipt 27 supplied no poem-commitment record and used zero recall read calls. The cross-context working view shows up to two goals by ID but summarizes other open items as a count; this can make the wrong goal actionable while concealing the intended commitment's ID. The exact historical working-view prompt was not saved, so that contribution remains an inference. The wrong database target and the stated reason are confirmed.

The manager checks owner, lifecycle and presence of a source, but does not validate that the cited source supports completion of that particular target. A source-owned user message is not automatically semantic evidence for every memory operation.

The final fresh follow-up corrected the actual commitment to fulfilled. Its resolution evidence points to question `ec4b4e9f…` (“Do I still need to bring… or did I already do that?”), not the earlier completion assertion. The tools bind evidence to the current message unconditionally. This is a target-selection problem followed by a provenance problem; the final answer being correct does not erase either.

### D. Historical correction annotations differ across APIs

For old marker event 12, `search` applies `_corrected_after` and marks it `no_longer_current`, pointing to the revised item. `read` reaches `_event_record` directly and returns generic `past`. `timeline` also calls `_event_record` without that correction pass. Any new lifecycle logic should be shared by all three read paths, including prior-reference rereads.

### E. Schedule correction rejection lacks enough diagnostics

Only two correction rows were retained: Marble fox (`changed`) and Mira (`mistaken`). The 5→6 schedule update logged `dropped: not grounded` and produced no correction row; the 4→5 update also has no retained row. The final schedule was handled by ordinary chat tools and fact extraction. Existing logs do not retain the proposed correction or exact failed grounding field, so the cause of this rejection cannot be traced further from this run. Do not describe the final correct schedule as proof that sequential correction-ledger ordering succeeded.

### F. Duplicate semantic identities

The extractor created both prefixed and unprefixed keys for the same card, such as `fact:cpsc310_marble_fox_card` and `cpsc310_marble_fox_card`. The semantic upsert uses exact normalized-key equality, so both coexist. The correction pass revised the tested old copy successfully. Duplicate identities are confirmed; their precise contribution to the missed 4 pm answer is not established.

## 6. Concrete acceptance requirements for the new design

1. **Cross-context history:** starting from current 6 pm, recover 4→5→6 through existing owner-scoped evidence relationships even though the original statement was made in General. Preserve dates, source IDs and historical/current distinctions.
2. **Supplied-evidence utilization:** give the answer path only events 20 and 21 plus the 6 pm fact. It must include 4 pm in the history and must not say earlier history is unavailable. This isolates answer synthesis from retrieval.
3. **Current-state separation:** the same history must not make 4 or 5 pm the current meeting time.
4. **Task targeting:** the poem completion must fulfill the poem commitment while leaving the unrelated meeting-time memory valid. Ambiguous target selection should retrieve the candidate rather than resolving a visible but unrelated goal.
5. **Evidence integrity:** the resolution must cite the actual earlier completion assertion when recovered during a follow-up, not turn the follow-up question into evidence that the action happened.
6. **Cue and hypothetical provenance:** recover who explicitly requested later recall, and distinguish a considered venue from a confirmed move using owner-checked source access. Do not promote hypothetical content to current fact merely to retain it.
7. **Uniform reads:** search, direct reads, timeline and prior-reference rereads agree on correction lifecycle annotations.
8. **Traceability:** capture per-call query, filters, returned IDs/scores/cutoff reasons, automatic versus tool stage, and rejected grounding field/code, with bounded retention. Current aggregate receipts are useful but cannot explain all decision boundaries.

These are proposed acceptance cases, not newly passing tests. They do not require a database migration or another graph to demonstrate the first history expansion. Application changes and behavioral reruns remain future work.

## Evidence and reproducibility limits

- Source inspected is unchanged from the October 20 retest. No fresh frontend conversations were sent for this diagnosis.
- Receipt IDs 25/26 and unchanged event fingerprints establish the two 4 pm failure classes. Receipts are supplied-set evidence, not hidden model reasoning.
- `memory-failure-ranking-trace-2026-10-21.json`: controlled ranking variants. Snapshot candidate IDs/item states were restored in memory, not in the DB. Facts/contexts were read live with source hashes checked; query embedding was obtained fresh. This is not an exact historical replay.
- `memory-failure-evidence-path-2026-10-21.json`: actual existing evidence joins and the exact 4 pm passages supplied to the follow-up.
- Original snapshots: `memory-retest-final.json`, `memory-retest-settled.json`, `memory-retest-injection-evidence.json`. Nine overall supplied-reference records changed after their receipts; the two crucial 4 pm events did not.
- No application mutations were made. Diagnostic DB transactions were rolled back.
