# Source-backed recall: findings and implementation handoff

Date: 2026-10-21 (America/Vancouver)

Status: proposed implementation direction, supported by exploratory evaluations. This document does not implement or claim a production recall fix.

## Purpose and location

Improve recall of changing information by recovering related source evidence and presenting a supported history alongside current state. Start with existing relationships; no new graph database is justified by the demonstrated failure.

This handoff is saved in the `app` checkout. The newer implementation, test records, and evaluation scripts are in `/Users/vincentgilbert/Desktop/NutritionDetection/app-cognitive`. Inspect that checkout's current code and instructions before implementing, and preserve unrelated uncommitted work.

## Read first

Architecture documents in `app-cognitive/docs`:

- `COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md`
- `COLUMBINA_MEMORY_SESSION1_SPEC.md`
- `COLUMBINA_MEMORY_SESSION2_SPEC.md`
- `COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md`

Evidence reports in `app-cognitive/docs/reports`:

- `memory-lab-2026-10-20.md`
- `memory-links-trace-2026-10-20.md`
- `memory-correction-retest-2026-10-20.md`
- `memory-failure-diagnosis-2026-10-21.md`
- `memory-two-stage-eval-2026-10-21.md`
- `memory-bundle-comparison-ready-2026-10-21.md`
- `memory-bundle-comparison-results-2026-10-21.md`

Use their linked JSON artifacts for exact IDs, source passages, fingerprints, prepared prompts, and results. Reports distinguish real frontend behavior, diagnostic service calls, and isolated model evaluations; retain those distinctions.

## What we discovered

### Correction consistency was the first problem

Originally, a corrected marker reached semantic memory while active working items still contained the old marker. Existing evidence links could locate candidates, but no reconciliation connected the stores.

The implemented correction path passed the original frontend regression: it revised the affected fact and intention, preserved another card's identical old marker, and recalled both current and original values in a fresh chat. Corrections must preserve what changed and its source, not simply overwrite the past.

This is a narrow successful regression, not proof that all updates work. Schedule changes in the broader retest were handled by other paths; the correction ledger did not retain those schedule transitions. Wrong task targeting also remains a separate issue.

### The meeting-history failure has two stages

The user changed Lantern's meeting from 4 pm to 5 pm and then to 6 pm. Current venue remained the library courtyard.

| Stage | Evidence available to generation | Observed answer |
| --- | --- | --- |
| Broad fresh-chat quiz | Old 4 pm records absent | Reported only 5 → 6 |
| Focused historical follow-up | Events 20 and 21 included intact 4 pm passages | Still denied an earlier recorded time |

Therefore better retrieval alone is insufficient: the answer must also correctly use supplied historical evidence. The model's internal reason for ignoring it is unknown.

The old records were in General, while later plans were in Lantern book club. Diagnostic controls demonstrated sensitivity to the named-context bonus, result cutoff, and explicit context filtering. These controls do not recover every original tool argument, which was not persisted.

### Existing links already recover the history

An owner-scoped evidence path exists:

```text
6 pm item
  → origin evidence → user's 5→6 update message
  → reverse resolution evidence → previous 5 pm item
  → origin evidence → user's 4→5 update message
  → reverse resolution evidence → previous 4 pm item
```

Attention-context boundaries do not necessarily separate subjects. Following this path reaches the same meeting across General and Lantern contexts.

The evaluation harness reconstructed 4 → 5 → 6 and matched 6 pm against an active fact. It visited 21 nodes with a six-edge depth bound and 128-node maximum. Those are harness settings, not established production defaults.

Important limitation: the harness uses a case-specific time-transition parser. It demonstrates reachable evidence, not a general production interpretation mechanism. Do not ship that parser as the general solution.

### Isolated answer evaluations

An initial minimal synthetic evidence test passed six of six runs, including reversed evidence order. Both lifecycle prose and explicit source statements worked without surrounding conversation or working memory.

A later comparison restored fixed surrounding context and disabled retrieval and mutation tools. Five independent calls per condition produced:

| Evidence condition | Complete 4 → 5 → 6 history | Retained 6 pm as latest |
| --- | --- | --- |
| Full 15-record bundle | 4/5 | 5/5 |
| Three relevant verbatim passages | 0/5 | 5/5 |
| Source-backed reconstruction plus those passages | 5/5 | 5/5 |

All 15 historical record fingerprints matched during preparation. Reconstruction added explicit organization, source IDs, and supporting quotes; it introduced no new underlying source passage or fact.

The shared preceding assistant response incorrectly said 5 pm was earliest. Repetition of that error is consistent with anchoring, but does not prove it. Context labels, lifecycle wording, ordering, redundancy, and source annotations could also contribute. The experiment does not isolate each feature added by reconstruction.

This was partially restored context, not an exact replay: original tool results were flattened, historical working-view timing was approximated, and some original prompt state was unavailable. Five calls per condition are exploratory. Production retrieval remains unchanged by these evaluations.

Conclusion: prioritize source-backed reconstruction. Simply trimming results or globally increasing limits is not an established fix.

## Proposed implementation

### Ownership

- Observation preserves reports and source identity.
- Memory manager owns persistent memory transitions, corrections, and lifecycle updates.
- Recall finds and organizes evidence for the present question.
- Cognition/mind decides what to say or do with that evidence.

The proposed reconstruction is initially read-only toward memory content. It must not complete goals, revise facts, or save generated interpretations as authoritative memories. Existing retrieval accounting and bounded diagnostics can remain separate side effects.

### 1. Start from ordinary retrieval

Keep current search as the entry point. For a history question, retrieved current or historical records become starting points for evidence expansion. Do not seed production code with expected values, fixture IDs, or the old context name.

Use semantic interpretation to identify the subject, requested property, and need for historical detail. Avoid a collection of English keyword rules. Reuse existing model work where practical; do not automatically add an expensive interpretation call to every unrelated turn or mind tick.

### 2. Expand through existing sources

Follow relevant origin, resolution, and correction evidence to source records and other memory targets connected to those sources. Permit crossing attention contexts while maintaining user ownership and any genuinely strict scope constraints.

Provide bounded, owner-checked access to source passages. Source IDs alone are insufficient if the reader cannot inspect what was actually asserted.

Track visited records, deduplicate repeated paths, and enforce node, depth, content, and time budgets. Stop when adequate evidence is found, expansion yields no useful information, or a budget is reached. Report truncation, unavailable sources, and failures explicitly.

Sharing evidence only makes a record a candidate. It does not establish that every claim in a multi-topic message concerns the same subject or property.

### 3. Interpret the collected evidence

Use general semantic interpretation to propose a question-scoped account from the bounded candidate set. Code should enforce reference integrity and valid output structure; it should not pretend that structural validation proves semantic truth.

An illustrative output contract includes:

- Subject and property under discussion.
- Supported prior and replacement values.
- Transition kind: actual change, correction of a mistaken statement, or unresolved.
- Source references and supporting passages for each assertion.
- Source sequence and available event/effective dates, distinguished from recording dates.
- Current state and its supporting evidence, when established.
- Gaps, conflicting statements, uncertain identity, and search coverage.

Do not equate latest recording time with latest real-world state. Do not turn a hypothetical alternative into an actual transition. Do not infer that an item marked completed means its embedded historical value never held.

If source order is all that is known, describe the order in which the user supplied updates rather than inventing effective dates. If two records cannot be connected reliably, preserve separate candidates or a gap instead of manufacturing a complete sequence.

### 4. Render the history with its evidence

Supply a compact connected account alongside the relevant original passages, clearly distinguished from source text. For the fixture, the result should communicate:

```text
Lantern meeting time:
- Originally recorded as Saturday, 4 pm.
- User updated 4 pm → 5 pm. [supporting source]
- User later updated 5 pm → 6 pm. [supporting source]
Current recorded plan: Saturday, 6 pm, library courtyard. [supporting source]
```

This is an example of supported content, not a mandatory answer template. Keep the raw supporting passages available: the best tested condition contained both reconstruction and passages.

Label the view as scoped to the question. Omission of unrelated tea preferences or other topics must not imply that those facts are unsupported everywhere in memory.

### 5. Preserve cognition's freedom

Recall returns evidence, ambiguity, gaps, attempted cues, and stop reasons. Cognition chooses whether to answer, acknowledge partial recall, say she does not remember, request another cue, or move on.

Do not force a conversational action with a prompt rule such as “no match means ask a question.” Do not introduce an automatic answer-verifier loop as an assumed requirement. Historical coverage checks below are evaluation criteria; any proposed production verification mechanism is a separate design decision.

### 6. Make lifecycle interpretation consistent

Search, direct reads, timelines, and prior-reference rereads must share the same correction/current-state annotations. The diagnosis found `_events` applying correction annotations while direct reads and timelines bypassed that pass. Inspect current code before editing; centralize the policy rather than duplicating it again.

## Related findings and scope boundaries

| Finding | Treatment |
| --- | --- |
| Explicit importance cue survived only in source wording | Source expansion should permit answering who explicitly flagged what. Preserve the distinction from an inferred importance score. Evaluate durable cue retention separately. |
| Hypothetical west reading room absent from recall stores | Better ranking alone cannot recover it. A bounded source read can recover it when reachable; wider transcript retrieval or retention changes require an explicit scope decision. Never store it as confirmed merely to retain it. |
| Wrong goal completed for bringing a poem | Separate write-targeting fix. Resolve the intended commitment and cite the actual completion assertion, including when recovered during a later question. |
| Duplicate prefixed/unprefixed fact keys | Separate identity normalization concern; deduplicate output where supported without assuming similar records are identical. |
| Schedule correction rejected as “not grounded” | Improve bounded diagnostic specificity; do not claim the schedule validated correction-ledger ordering. |

Entity grouping and iterative cue search remain longer-term ideas. Memories may relate to multiple entities; entity identity resolution, merge/split repair, and ambiguous matches require care. This historical-recall slice does not require a new entity schema or graph service.

Decay remains separate: it affects accessibility, not validity. Repeated retrieval must not make a corrected false claim authoritative. No decay, learning, or autonomous behavior changes belong in this slice.

## Acceptance and evaluation

### Deterministic service and integrity tests

1. Starting from current records, recover earlier evidence across contexts without fixture-specific lookup rules.
2. Enforce ownership at every traversal/source-read step; another user's source never becomes a candidate.
3. Respect cycles, duplicate paths, deletion, missing sources, budgets, and partial failure without reporting them as proof that history never existed.
4. Make correction annotations agree across search, direct read, timeline, and prior-reference reread.
5. Validate that every reconstructed source reference belongs to the supplied evidence set. Preserve exact supporting excerpts where used.
6. Confirm recall does not mutate remembered facts, working-item statuses, or goals.

### Semantic and answer tests

1. Recover 4 → 5 → 6 from the stored Lantern case and identify 6 pm as current.
2. Pass the automatically reconstructed output unchanged into the answer path. Do not manually repair it between stages.
3. Retain the full/relevant/reconstructed fixed-evidence comparison as an answer regression, including the misleading preceding assistant answer.
4. Test actual changes versus mistaken statements, hypothetical alternatives, incomplete chains, real conflicts, reversed evidence order, unrelated assertions sharing a source, and similarly named subjects.
5. Test changes outside meeting times, such as a marker, project location, or preference, without specialized parsers.
6. Evaluate current-value accuracy and historical completeness separately. A correct 6 pm answer does not pass a question requesting the full history if it omits 4 pm.
7. Test source-level importance attribution and hypothetical status without conflating these with general history reconstruction success.

### Real frontend validation

Use isolated test accounts and fresh conversations. Exercise broad history, focused follow-ups, current-only questions, distractors, and ambiguous references. Report supplied evidence separately from final behavior. Do not treat isolated backend results as a completed frontend regression.

Capture sufficient bounded diagnostics to distinguish candidate collection, expansion, interpretation, rendering, and answer failures. Include effective filters, per-stage IDs, cutoff/stop reasons, source versions, and reconstruction evidence references. Keep private content retention explicit; avoid permanently duplicating complete transcripts in receipts.

## Suggested implementation order

1. Read the current code and evaluation artifacts; reproduce the six-result miss and existing traversal.
2. Define the bounded expansion and reconstruction result contracts.
3. Implement source access and evidence expansion with ownership and coverage tests.
4. Add general semantic reconstruction and shared lifecycle annotations.
5. Wire reconstruction into the appropriate recall path while retaining supporting passages and explicit failure behavior.
6. Run service tests, semantic/answer evaluations, and then frontend retests.
7. Document what improved, what remains unsupported, latency/cost, and any regressions. Keep task-write repair separately identifiable.

The target is a general source-backed recall capability, not a hardcoded passing answer for Lantern. The evidence supports this direction; its reliability and operational cost still need to be established in implementation.
