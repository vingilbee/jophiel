# Fixed-retrieval evidence comparison — prepared October 21, 2026

Status: completed after explicit user approval to call Luna with the prepared inputs. All 15 calls and semantic assessments are saved in `memory-bundle-comparison-results-2026-10-21.json`; interpretation is in `memory-bundle-comparison-results-2026-10-21.md`. Earlier execution attempts were rejected before calls began; the approved run completed successfully.

## Conditions

All conditions use the same original question, preceding user question and incorrect assistant reply, character prompt, saved awareness, reconstructed cognitive reading and approximate historical working-memory view. Retrieval and mutation tools are disabled. Only the recall evidence block changes.

| Condition | Evidence | Recall-block characters | Complete system-prompt characters |
|---|---|---:|---:|
| Larger bundle | Original union of 15 supplied records from receipt 26 | 5,194 | 23,491 |
| Relevant passages | Three verbatim passages from that same union: event 20, event 22, current 6 pm fact | 1,178 | 19,475 |
| Source-backed reconstruction | Structured 4→5→6 history and current confirmation, with exact quotes/reference IDs, plus the same three supporting passages | 1,808 | 20,105 |

The reduced and reconstructed conditions add no new underlying memory fact or external source. The original surrounding history includes the assistant's mistaken statement that the earliest known time was 5 pm; it is deliberately held fixed across conditions.

Planned execution: five independent calls per condition, fifteen total, rotating condition order by round. The production `complete_chat` entrypoint uses configured model `gpt-6-luna` with reasoning effort `none`. Each invocation starts with the frozen messages, with no generated answer carried to another invocation. No app chats or memory records are written.

## Restored versus approximate inputs

- All fifteen evidence records verified against original stored receipt fingerprints. Historical item text/status/revisions were restored from the pre-quiz snapshot where later updates changed them.
- Three original conversation messages restored exactly: earlier user question, incorrect assistant answer, target follow-up.
- Saved awareness restored from the original chat job payload.
- Original cognitive log confirms the character setting, reading arm and empty notes. Reading is decoded from logged rounded probabilities using the production threshold/argmax rules; values are away from decoding boundaries.
- Character text and system-prompt assembly are rebuilt from the current checkout, not an archived original full prompt.
- Historical working-memory view is approximated from frozen item states and production formatting. Per-context last-active timestamps are inferred from preceding records; historical pending-observation text was not saved.
- The original automatic recall plus three tool-result bundles are flattened into a single fixed block. Original per-call response ordering and full tool-message trace were not persisted.

Consequently this is a controlled comparison with restored context, not a byte-exact replay of the historical model invocation.

## Offline checks completed

- Fifteen of fifteen original evidence fingerprints match after restoration.
- The reduced condition contains only passages present in the larger condition.
- Every reconstructed claim has an exact supporting quote in those passages.
- The reconstruction is continuous: 4→5, 5→6; the stored active fact confirms 6.
- Non-evidence prompt inputs are shared. Replacing the evidence block with a placeholder gives common prompt SHA-256 `e8d80b66a5f61e2e3d6e11cb7c1c99cd7eb7b9601271ef1d9bed76e007d7bd5f`.
- Script compilation and CLI help checks pass.

## Assessment after approval

Review each complete saved answer for:

1. Inclusion of 4, 5 and 6 in the correct historical order.
2. Identification of 6 pm as the current confirmed time, with the courtyard venue retained.
3. No claim that 4 pm evidence or earlier history is unavailable.
4. No acceptance of the old 4/5 pm values as current.
5. Appropriate uncertainty about the hypothetical west-reading-room discussion, which is not supported by these fixed memory passages.

Also report prompt size, omissions, contradictions and any tendency to repeat the previous assistant's incorrect claim. Five runs per condition are exploratory; they cannot establish a general accuracy improvement or uniquely attribute differences to length, distraction, redundancy, ordering or the added structured scaffold.

## Exact prepared model inputs

These local files are reviewable copies of what each condition would send: system prompt plus the three original conversation messages. The data came from the assistant-generated isolated Memory Retest fixture, including fictional card, tea and club details; they are not newly fetched real-user personal records.

- [Larger bundle](/Users/vincentgilbert/Desktop/NutritionDetection/app-cognitive/docs/reports/memory-bundle-comparison-input-larger_bundle-2026-10-21.json)
- [Relevant passages](/Users/vincentgilbert/Desktop/NutritionDetection/app-cognitive/docs/reports/memory-bundle-comparison-input-relevant_passages-2026-10-21.json)
- [Source-backed reconstruction](/Users/vincentgilbert/Desktop/NutritionDetection/app-cognitive/docs/reports/memory-bundle-comparison-input-source_backed_reconstruction-2026-10-21.json)

Manifest: `memory-bundle-comparison-manifest-2026-10-21.json`. Validation: `memory-bundle-comparison-validation-2026-10-21.json`. Harness: `backend/scripts/memory_bundle_comparison.py`.

## Ready command

From backend:

```sh
.venv/bin/python scripts/memory_bundle_comparison.py run --manifest ../docs/reports/memory-bundle-comparison-manifest-2026-10-21.json --output ../docs/reports/memory-bundle-comparison-results-2026-10-21.json --repeats 5
```

Run only after the required authorization is obtained. It refuses to overwrite an existing output file and checkpoints every completed answer.
