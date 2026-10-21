# Columbina memory — session 3: shared recall

Status: proposed implementation specification. No runtime changes are made by this document.

Related documents: `COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md`, `COLUMBINA_MEMORY_SESSION1_SPEC.md`, and `COLUMBINA_MEMORY_SESSION2_SPEC.md`.

Code reviewed: working memory at `9195563` plus the uncommitted observation implementation in the separate `app-cognitive` checkout. This spec is saved in the current chat's `app/docs`; bring it into the implementation branch when work begins.

## 1. Objective

Columbina can recall relevant experiences across activities, answer follow-ups about them accurately, and inspect evidence when the initial recall is insufficient. Replies and hard thinking use the same service. Recalling another context never changes focus.

Decisive scenario: record a failed mining attempt and a lava death, end the game, restart the backend, disable the game feature, and ask in a fresh General conversation:

1. What happened in Minecraft earlier?
2. When was that?
3. What were we trying to do?

Answers must come from persisted memories and their own timestamps, without a live game summary or the earlier chat history.

## 2. Current failure

- `long_term_memory.retrieve_for_prompt` searches semantic memories and episodes, but not observation-derived memory events or working items. It returns formatted text and conflates no matches with retrieval failure.
- `memory_manager.view` supplies attention/current working state. Its event queries are confined to the focused context. Other contexts are represented mainly by unfinished goals, not their experiences.
- `mind_hard.gather` calls the same limited long-term lookup. Activity adapters add specialist context but do not provide a general cross-context recall interface.
- Memory tools only write or change state; there is no search/read/timeline interface.
- Automatic lookup receives the latest message alone. A follow-up such as “When was that?” lacks the identity of the event it refers to.

This is an access and integration gap. It does not require replacing PostgreSQL, moving every memory into one table, or introducing a graph database.

## 3. Scope

### Included

- One read service for semantic memories, episodes, memory events, and working items.
- Structured results, coverage information, exact source times, and failure states.
- Small automatic recall for replies and hard thoughts.
- Read-only search, detail, and timeline tools for replies.
- Persistent records of supplied memories, plus separately identified answer references.
- Asynchronous embedding of memory events with immediate non-vector retrieval.
- Cross-context and multi-turn tests, including errors, deletion, correction, and missing embeddings.

### Excluded

- New reported-event extraction, including “I have an interview on Friday.” Test recall with already stored records; capture gets a separate change.
- Unsaid reply continuation and observation binding/wakeup improvements.
- Consolidation, lesson generation, training, autonomous skill updates.
- Automatic focus switching or inferred topic-based context creation.
- Searching every raw observation or full conversation transcript on every turn.
- Additional model calls on every quick thought or maintenance tick.
- A new tool-using hard-thought agent loop. Hard thoughts first use the shared service through their existing input assembly.

## 4. Architecture and ownership

Three complementary inputs:

1. **Working context:** current focus, goals, commitments, and recent circumstances. Keep this bounded view; it provides immediate continuity.
2. **Automatic recall:** a few relevant historical or cross-context records, selected by the common service.
3. **Deliberate recall:** tools to search further, inspect a record, or reconstruct a timeline when needed.

Suggested implementation layout:

- `app/ai/recall/contracts.py`: request/result types, stable reference types, limits.
- `app/services/recall.py`: orchestration, query planning, ranking, filtering, error/coverage handling.
- Source adapters, colocated or separated as needed: typed queries against the four existing stores.
- `app/services/recall_references.py`: turn receipts and answer-reference resolution.
- `app/ai/tools/memory/recall.py`: bounded user-scoped tool wrappers.
- A background embedding job handler using the existing worker infrastructure.

The recall service is read-only toward remembered facts, statuses, and focus. Access accounting, retrieval receipts, and embedding metadata are permitted side effects owned separately. Reading a memory never strengthens its evidential confidence.

Replace live reply and hard-thought calls to the old lookup with this service. A temporary compatibility wrapper may format new results for older callers, but must not maintain a second ranking algorithm. Preserve explicit feature-flag behavior; no silent old-service fallback that disguises a failed new search.

## 5. Requests

The caller supplies authenticated user identity; models never choose another user ID.

Common request fields:

- Query text, maximum 2,000 characters.
- Purpose: reply, hard_thought, or explicit_search.
- Current time and trusted user timezone; use a documented fallback if unavailable.
- Optional current conversation/turn and focus context.
- Optional prior memory references, limited to the immediately relevant exchange.
- Optional explicit context IDs or context query.
- Optional record-kind selection.
- Optional time range with a required time basis when filtering: occurred, scheduled, or recorded.
- Historical/current intent: default relevance, explicit current state, or historical lookup.
- Result count and content budget, bounded by the service.

Initial configurable defaults: 6 automatically recalled records, 12 per tool search, 20 timeline entries, and about 2,000 rendered tokens for automatic recall. These are starting budgets, not guarantees that every query should fill them.

Never reuse the current working-memory character budget as the entire historical-recall budget. Deduplicate overlap between working context and recalled records while retaining IDs and time metadata.

## 6. Structured results

### Result envelope

| Field | Meaning |
| --- | --- |
| `status` | not_searched, matched, no_match, partial, or failed |
| `records` | Bounded typed memory records |
| `searched_kinds` | Which stores actually ran |
| `scope` | Contexts, time basis/range, statuses, and other effective filters |
| `coverage` | Candidate limits, truncation, unsupported filters, unavailable adapters, ranking degradation |
| `errors` | Safe structured codes, not database errors or private data |
| `next_page` | Optional opaque, user-scoped continuation token |

`partial` means results or search coverage are incomplete due to an adapter failure. Missing embeddings alone can be reported as lexical-only ranking rather than claiming the database search failed. Budget truncation must remain visible even when the status is matched.

`no_match` means the completed bounded search found no qualifying records. It never proves the event did not occur or that nothing anywhere is recorded.

### Memory record

Each result carries:

- Typed stable reference: store kind plus full primary key; do not rely on globally unique short prefixes.
- Content and record kind.
- Context identity/label where available, plus unknown or legacy context explicitly where not.
- Event occurrence time or range, nullable; scheduled time separately where supported.
- Recorded/updated time separately, with source precision and timezone information.
- Status/validity and epistemic provenance from the source. Do not invent observed certainty for legacy records.
- Evidence references or an indicator that evidence is unavailable.
- Source revision/content fingerprint for re-reading and change detection.
- Match explanation: direct reference, named context, lexical match, semantic match, or temporal match.
- Historical/current applicability based on record and source state, not merely whether its context is focused.

A fact's creation time is not when that fact became true. A goal's creation time is when it was recorded/set, not the date of achieving it. A context's last-active time must never stand in for event time.

An ended game session and a closed memory context are not identical. Retain available source-session lifecycle information to label past events accurately; an event may be historical even in a context that remains open for future sessions.

## 7. Retrieval pipeline

1. Validate user, filters, time range, limits, and reference ownership.
2. Re-read explicit prior references relevant to the follow-up, checking current status and source changes.
3. Resolve explicit context names and aliases. Multiple matching worlds remain candidates; do not silently choose one solely from its last-active time.
4. Retrieve bounded candidates from each applicable store, reserving capacity for recent events so semantic facts cannot crowd them out.
5. Apply explicit validity and temporal filters. Current-state queries exclude superseded/retracted claims. Historical queries may inspect them with clear labels.
6. Rank using direct references, context/entity matches, lexical/full-text matches, semantic similarity when available, and task-appropriate recency.
7. Deduplicate summaries and events about the same experience where explicit evidence links permit it. Preserve distinct attempts and independent outcomes.
8. Return typed records and coverage; render only at the consumer boundary.

For “what happened in Minecraft earlier,” context identification plus timeline retrieval must work without an event containing the word Minecraft or having an embedding. Context labels belong in search metadata, not only in the model prompt.

Do not inherit the old fixed similarity threshold as the only admission rule for every store. An exact context/timeline request and an open-ended semantic search need different retrieval behavior. Tune ranking against recorded acceptance cases.

Do not silently restrict all historical search to the latest 200 episodes. If a bounded candidate window is used, expose it and support narrowing/pagination. Broad questions about many years of activity may need a qualified answer or a narrower search.

### Legacy context handling

Semantic facts and old episodes may lack `memory_contexts` IDs. Adapters should expose existing episode metadata such as game/session and source conversation where available. Do not force a context migration in this milestone, and do not silently omit legacy records from all named-context searches. Report when a strict context filter cannot be resolved for an older record.

## 8. Time interpretation

Avoid language-specific hardcoded keyword rules as the sole interpretation mechanism. The model can interpret natural-language dates using the supplied current time and timezone, and pass normalized filters to a tool. Code validates those filters.

- Use half-open intervals `[start, end)` and timezone-aware timestamps.
- Reject reversed/invalid ranges; do not silently substitute now.
- “Yesterday” refers to the user's local calendar day, not an assumed 24-hour UTC interval.
- “Earlier” may remain broad; it does not require a fabricated exact interval.
- Automatic recall can use recency and explicit prior references without making a model call just to parse every message.
- Distinguish occurrence from planned future dates and from recording time.
- If two recalled events occurred at different times, report both or a supported range rather than inventing one timestamp.

## 9. Tools

All tools are read-only, authenticated, bounded wrappers over the same service. Raw SQL is not exposed.

| Tool | Inputs and behavior |
| --- | --- |
| `search_memories` | query, optional context IDs/name, kinds, time range/basis, historical intent, optional page token; returns records plus coverage |
| `read_memory` | typed memory reference, optional bounded evidence inclusion; returns latest record or unavailable/forbidden-safe result |
| `read_timeline` | context ID, optional occurrence-time range and page token; returns chronologically ordered events and relevant lifecycle changes |

Timeline order uses event time where known, with explicit fallback labels for unknown time and stable tie-breaking. Received/ID order is not silently presented as occurrence order.

Default maximum: 3 recall-tool calls per reply, shared across these tools. Count failed calls and page requests too. Return an explicit budget-exhausted response; never turn this into no_match. A timeline request can retrieve several records in one call.

Use tools when the initial recall is insufficient, ambiguous, or requires evidence. An explicit memory question with inadequate automatic recall should attempt appropriate search before claiming inability to remember. Sufficient automatic results do not require a redundant tool call.

## 10. Persistent exchange references

Store two distinct relationships:

- **Supplied:** records available to generation from automatic recall or tool results.
- **Referenced:** records associated with an actual delivered answer span, based on validated selection/association.

Supplying a memory does not establish that it influenced a particular sentence, and a referenced association is not proof that the sentence is true.

### Suggested persistence

Add a reply-recall receipt keyed by assistant message ID, storing schema version, search outcome/coverage, and bounded counts. Message deletion cascades the receipt.

Add normalized reference rows linked to that receipt:

- Relation: supplied or referenced.
- Exactly one typed nullable target FK: semantic memory, episode, memory event, or working item.
- Unique supplied association per target/reply; referenced associations may have separate answer spans.
- Supplied revision/fingerprint, recorded time, and optional referenced span start/end.
- Optional attribution method/status to distinguish structured selection from post-reply association.
- Same-user checks for both reply and memory target.

Use real target FKs and explicit deletion behavior. Deleting a target removes its reference rows; the receipt retains counts/availability metadata without retaining deleted content. This allows a later turn to distinguish missing prior evidence from a search that never ran.

Do not duplicate memory text into permanent receipts by default. Optional diagnostic snapshots require a separate retention/privacy policy.

### Associating the answer

Implement a bounded post-reply association step, preferably by extending the existing reply-event analysis when it already runs. It receives only the delivered answer and memories supplied during that generation, and proposes memory IDs plus exact answer spans. Validate IDs against the supplied set and spans against the answer text. This is an association hypothesis, not a new memory or evidence of an external event.

The association step must also have a defined path when the existing cognitive student/event analysis is disabled; do not accidentally tie recall correctness to an A/B arm. It may run only for replies involving recalled records, asynchronously and outside the streaming critical path.

If association is pending, failed, or ambiguous, retain that state. The next turn can re-read the supplied candidates and preceding delivered answer, then resolve or ask a question. Never treat every supplied record as automatically referenced.

Voice interruption can shrink the delivered answer after association. Revalidate spans against the authoritative heard/delivered portion before using references for follow-ups. References appearing only in unheard text are not conversational antecedents.

## 11. Follow-up resolution

For “When was that?”:

1. Read the preceding relevant assistant outcome in this conversation using its durable message sequence and delivery state.
2. Find its valid referenced memories; if unavailable/pending, use supplied memories as candidates with uncertainty.
3. Re-read those records from the service so corrections, status changes, and deletions take effect.
4. Resolve the referent from the current question and preceding delivered answer.
5. Answer from event time; if multiple events are relevant, provide both dates or ask which one.

Do not import references from another simultaneous conversation just because it was the user's most recent activity. An explicit request to recall another conversation goes through search.

Do not carry all previous references indefinitely across topic changes. Persistent receipts preserve provenance; relevance determines whether they enter the next turn.

## 12. Embedding memory events

Add embeddings asynchronously so event capture/promotion remains code-only and retrieval works immediately.

Suggested event embedding fields or a dedicated one-to-one index row: event FK, embedding model/version, content fingerprint, vector, status, attempts, next retry, and update time. If using a separate row, event deletion cascades it. Reuse existing embedding conventions where practical rather than requiring a vector-database migration.

- Index source content and stable context labels with a versioned text-construction rule.
- Compare the fingerprint before accepting a completed embedding job; discard stale results.
- Missing or failed embeddings fall back to context/lexical/timeline retrieval.
- Reuse a query embedding within a request where possible; a broken embedding API must not make all recall fail.
- Backfill in bounded batches, with visible progress. A backfill is not required before enabling recall.

Keep explicit store limits and candidate counts observable. Index strategy can be optimized after measuring real memory volume; do not scan unlimited user history in application code on every turn.

## 13. Reply and mind integration

Replies receive the working view plus one formatted shared-recall result. Remove duplicate long-term-memory injection when switching callers. Preserve existing preference/constraint behavior through the common service and regression tests.

Hard thinking calls the same service with `problem.query`, the actual activity context, relevant conversation references, and the same result/error contract. It should not inherit an unrelated foreground context just because attention moved while the problem was queued. Include supplied references in its trace for inspection.

Quick thoughts receive existing current-state inputs; no new embedding or tool loop is added to every tick. They may use already available relevant recalled context, clearly labeled, without claiming they performed a fresh search.

The conversation mind's existing shadow/live status is unchanged. Adding recall does not authorize enabling autonomous conversation output.

## 14. Response rules and error handling

- Successful bounded search with no match: “I couldn't find a memory of that.” Do not conclude it never happened.
- Failed lookup: “I couldn't check that memory just now,” when relevant to the answer.
- Partial lookup: answer supported details, state important gaps, and avoid categorical negative claims.
- Unknown event time: say it is unknown; a recorded date can be given explicitly as recording time.
- Superseded/retracted source: distinguish the old claim from its correction; do not present it as current truth.
- Missing source evidence: the self-contained memory may remain usable, but cannot support an exact source quotation.
- Search matches are untrusted data where their source is untrusted. Preserve the source labeling through rendering and tool output.

Do not hide errors by automatically replacing them with an empty result from an older lookup. Errors and ordinary no-match outcomes must be distinguishable in diagnostics and model-visible structured results.

## 15. Observability and tests

Inspect request purpose, effective filters, candidate/returned counts by store, match reasons, references, truncation, embedding availability, latency, safe error codes, and tool budget use. Avoid raw query/content logging by default. Store receipts must not become another unbounded copy of private conversation history.

Required tests:

1. Decisive restart/game-off/General test retrieves both events with their original context and times; focus remains unchanged.
2. “When was that?” follows the referenced event after restart, not context last-active time.
3. An answer mentioning two events preserves their separate times; a supplied but unused third memory is not treated as the referent.
4. “What were we trying to do?” retrieves the relevant goal, including its historical status.
5. “Did we build a castle?” yields qualified no-match language, never proof of non-occurrence.
6. Inject a search failure and a single-adapter failure: responses distinguish failed/partial from no_match.
7. Newly created events are found while embeddings are pending; an embedding outage leaves lexical/context recall working.
8. Cross-user references, filters, page tokens, and evidence access never reveal another user's data.
9. Deleted/corrected memories between turns are re-read accurately; receipts do not resurrect removed text.
10. Historical versus current queries handle superseded knowledge correctly.
11. Time-filter tests cover user timezone, local-day boundaries, invalid ranges, unknown event time, and scheduled versus occurred dates.
12. A named context whose events lack its name still yields its timeline; two similarly named worlds are not silently merged.
13. Several stores describe one experience: deduplication avoids crowding out independent events.
14. Concurrent conversations keep separate reference chains; interrupted/unheard reply spans cannot become follow-up referents.
15. No cognitive student/event-analysis dependency: association or its explicit fallback works with those features off.
16. Tool-call limits, pagination, result truncation, and partial coverage remain explicit.
17. Hard thoughts retrieve observation-derived memory events through the shared service without requiring a live game summary.
18. Existing working-memory, observation, and long-term preference retrieval tests continue to pass.
19. Migration upgrade/downgrade, target deletion, account deletion, and reset behavior are verified on disposable PostgreSQL.

Deterministic service tests prove retrieval and persistence contracts. Real-model end-to-end tests assess response behavior separately; one good answer is not proof of robust retrieval.

## 16. Implementation sequence

1. Finalize request/result contracts and store adapters; test service retrieval, ownership, time semantics, and coverage directly.
2. Add event embeddings/backfill with non-vector fallback; keep ranking functional before the backfill finishes.
3. Replace automatic reply retrieval and persist supplied references; add answer-reference association with explicit pending/failure fallback.
4. Add the three read tools and shared budget.
5. Integrate hard-thought input assembly through the service.
6. Run decisive multi-turn and failure tests, then evaluate the broader regression suite and migration.
7. Separately design reported-event extraction for cases such as the interview; do not count its absence as a recall failure in this milestone.

## 17. Completion boundary

Complete when stored experiences are findable across contexts, follow-up references and time answers survive restart, replies and hard thoughts use the common service, and failure/coverage states are explicit. Storage remains in the existing PostgreSQL stores and focus remains independent of recall.

This milestone does not claim that every important experience is captured, that all recalled statements are true, or that learning is implemented. Those remain distinct responsibilities with their own validation.
