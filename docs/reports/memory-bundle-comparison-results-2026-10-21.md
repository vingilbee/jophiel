# Fixed-retrieval comparison: 15 completed Luna calls

October 21, 2026. All 15 calls completed after explicit user approval. Model: `gpt-6-luna`; configured reasoning effort: `none`. This run used the production `complete_chat` answer entrypoint, with retrieval and mutation tools disabled. No app conversation or memory records were written. Application behavior was not changed.

The source-backed reconstruction recovered **4 → 5 → 6 in 5/5 answers**, the full bundle in **4/5**, and the selected raw passages in **0/5**. Simply reducing the evidence did not improve this case. Explicit organization is a promising next step, but five answers per condition do not establish general reliability.

## Results

The primary criterion was explicit inclusion of all three meeting times in chronological order. The latest value was assessed separately: an answer could preserve the 5→6 update while missing the older 4 pm plan.

| Condition | Complete 4→5→6 history | 6 pm retained as latest update | False claim earlier time unavailable | Courtyard mentioned |
|---|---:|---:|---:|---:|
| Full 15-record bundle | 4/5 | 5/5 | 1/5 | 4/5 |
| Three relevant verbatim passages | 0/5 | 5/5 | 5/5 | 5/5 |
| Source-backed reconstruction plus those passages | 5/5 | 5/5 | 0/5 | 5/5 |

No answer presented 4 or 5 pm as current. “6 pm retained as latest update” does not require the literal phrase “current confirmed time”; all five reconstructed answers do explicitly identify it as current or as the final change. All 15 appropriately declined to assert whether the west reading room was considered or confirmed, because that discussion is absent from every fixed evidence condition. This does not resolve the separate retention failure for the hypothetical venue.

Per-round history results:

| Round | Full bundle | Relevant passages | Reconstruction |
|---|---|---|---|
| 1 | Pass | Fail | Pass |
| 2 | Pass | Fail | Pass |
| 3 | Pass | Fail | Pass |
| 4 | Pass | Fail | Pass |
| 5 | Fail | Fail | Pass |

## What failed despite supplied evidence

The reduced condition includes event 20 verbatim. It contains both the old “at 4 pm” plan and the update “time to 5 pm.” Event 22 supplies the subsequent 5→6 change, and the fact confirms the current 6 pm meeting. These are sufficient underlying passages for this history, yet every reduced-condition answer omitted 4 pm.

Relevant-passages round 3 said:

> The recorded Lantern history I can currently see shows **5 pm first, then an update to 6 pm** … It does **not** show an earlier meeting time …

The full bundle also failed once, in round 5:

> The records I can see show a **5 pm** plan, then an update replacing it with **6 pm**; they don’t show an earlier meeting time …

Reconstruction round 1 instead said:

> … **4 pm to 5 pm, then to 6 pm**. So the earliest recorded time was **4 pm**, and **6 pm** is the current confirmed time.

The common preceding assistant reply incorrectly asserted that 5 pm was the earliest recorded time. The failed outputs repeat that error, which is consistent with anchoring, but this experiment does not prove the model's internal cause. Other plausible contributors include lifecycle wording (“Goal completed”), the General context label on event 20, source-availability annotations, ordering, and redundancy. Reconstruction retains the same raw passages and adds explicit historical/current values with source IDs and exact quotes; this run does not separate those added features from one another.

Two reduced-condition answers also volunteered that earlier book/tea statements were unsupported by the reduced evidence. That evidence condition intentionally omits most of those supporting records. This is an additional reason to scope a selected evidence view to the current question and avoid treating it as an exhaustive account of every prior topic.

## Controls and integrity

- Five independent calls per condition; condition order rotated across rounds. No generated answer was carried into another call.
- The same three conversation messages and surrounding context were used in all conditions. Only the recall evidence block changed.
- All 15 original record fingerprints were verified during preparation. The manifest hash still matches the run, and every saved system prompt exactly matches its prepared input file. All 15 condition/round pairs are unique.
- System-prompt sizes: full bundle 23,491 characters; relevant passages 19,475; reconstruction 20,105. The reconstructed condition adds no new underlying source passage or memory fact.
- Codex reviewed every complete saved answer against the stated criteria. No additional judge-model calls were made. Per-answer assessments, prompt hashes, full prompts and outputs are retained in the results JSON.

This is a controlled experiment with partially restored context, not an exact replay of the original failure. Original tool-call delivery was flattened into one recall block; historical working-memory timing was approximated; pending-observation text was unavailable; character text and prompt assembly came from the current checkout. The complete original prompt/tool trace was not saved. The model run did not perform live retrieval or test a production history-reconstruction implementation.

## Retrieval changes supported by these findings

1. **Recover the transition chain through existing evidence relationships.** The earlier diagnosis demonstrated a separate retrieval miss: old 4 pm records belonged to General while the current meeting belonged to Lantern. Starting from the current record, use bounded, owner-scoped expansion through origin/resolution evidence across attention contexts. A shared source identifies candidates; validate that they refer to the same subject and property before connecting them. Do not rely on a larger global result limit or a new database to solve this case.
2. **Return an explicit source-backed history alongside current state.** Preserve the old value, replacement value, source reference and exact supporting passage for each transition; identify 6 pm separately as current. This condition performed best here. Keep the supporting passages available so a reconstruction can be checked and contradictions can remain unresolved rather than guessed away.
3. **Do not ship relevance trimming alone as the fix.** The three selected passages contained the needed evidence but failed all five times. More concise input is not sufficient evidence of better answer use. Preserve useful historical corroboration until broader comparisons justify removing it.
4. **Check the answer against the supplied history.** If an answer denies an earlier value that the supplied history explicitly supports, flag a coverage failure. Do not invent missing transitions to force a complete chain. Keep current-value correctness and history completeness as separate acceptance criteria.

The next implementation evaluation should test these stages separately: first prove automatic retrieval reconstructs the cross-context chain without a hard-coded expected answer; then supply that output unchanged to the answer path. Cover unrelated subjects, incomplete chains, genuine conflicts, historical versus mistaken corrections, and current-only questions. This 15-call comparison supports prioritizing organization, but does not show that a production retrieval change is already working. No production retrieval changes were made in this run.

## Artifacts

- [Full outputs and per-answer assessments](memory-bundle-comparison-results-2026-10-21.json)
- [Frozen manifest](memory-bundle-comparison-manifest-2026-10-21.json)
- [Preparation and replay limits](memory-bundle-comparison-ready-2026-10-21.md)
- [Integrity checks](memory-bundle-comparison-validation-2026-10-21.json)
- [Earlier retrieval and evidence-path diagnosis](memory-failure-diagnosis-2026-10-21.md)
