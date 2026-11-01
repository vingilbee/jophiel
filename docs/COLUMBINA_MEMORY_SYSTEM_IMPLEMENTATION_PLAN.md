# Columbina memory system: connected implementation plan

Date: 2026-10-30. Stage-5 design aligned with user decisions on 2026-10-31; built the same day (see the status file, stage 5). For built stages and remaining gates, see [implementation status](COLUMBINA_MEMORY_SYSTEM_STATUS.md). Stage-5 details and superseding policy decisions are in the stage-5 spec (COLUMBINA_MEMORY_SYSTEM_STATUS.md, stage 5).

This plan extends the current PostgreSQL, worker, recall and cognition architecture. It incorporates the small-index design and the completed memory test in [the test report](reports/memory-e2e-2026-10-30/report.md). It does not require a graph database, Markdown memory store, or wholesale replacement of the existing fact table.

The central change is automatic, bounded recall before Student 2 decides how to respond. Student 2 decides whether that initial evidence is sufficient and whether more work is needed. Memory owns persistence, uncertainty and dependency validity; cognition owns whether and how to speak.

## 1. Baseline and requirements

The completed test scored 8/14 quiz passes, with three failures and three declared known gaps. Canonical corrections persisted. Both intended conflicts were recorded; the dog conflict settled. Forgetting did not remove the knee episode. Some replies received zero memories despite the correct facts existing. A working pet question remained open after its conflict settled and resurfaced in quiet-time speech.

The test did not exercise a nonempty reconciliation hold or settlement while a conflict thought was still waiting. These need targeted acceptance tests, not assumed passes.

Required outcomes:

- Corrected claims retain unrelated details and source-backed history.
- Every user message receives an initial bounded memory check before response decisions.
- Standing constraints cannot disappear merely because relevance ranking chose other records.
- Explicit changes, unresolved conflicts, and historical statements remain distinct.
- Settling a conflict resolves or invalidates its dependent questions and speech proposals even when the fact wording is unchanged.
- Forgetting executes a scoped operation and prevents the forgotten material from returning through derived records or source reprocessing.
- Autonomous delivery rechecks its premises and avoids repeated questions with different wording.
- Evidence traces distinguish what existed, what was selected, what each model actually received, and what was delivered.

## 2. Current components and intended changes

Paths below are relative to the repository root. These are integration points, not a claim that the proposed capabilities already exist.

| Current component | Keep | Extend or change |
| --- | --- | --- |
| `backend/app/db/models.py` | Semantic memories, episodes, working items, evidence, conflicts, revision jobs, recall receipts | Entity/attribute identity, temporal fields, constrained dependencies, index and invalidation state |
| `backend/app/services/long_term_memory.py` | Background consolidation and fact history | Atomic claim extraction, entity matching, partial updates, wider candidate discovery, typed change events |
| `backend/app/services/recall.py` | Shared search/read/render and conflict annotations | Entity and relationship matching, core inclusion, temporal filtering, coverage-oriented recall |
| `backend/app/services/recall_references.py` | TurnRecall, receipts, supplied references and history receipts | One initial evidence bundle, per-consumer receipts, version-aware reuse and later-search deltas |
| `backend/app/services/recall_expand.py` and `recall_history.py` | Bounded source/history expansion | New entity, claim and dependency relationships; preserve ownership limits |
| `backend/app/services/shared_state.py` | Turn orchestration, explicit degraded states, coordinator routing | Move bounded search before cognition; supply the same versioned evidence to Student 2 and the mouth |
| `backend/app/services/student_two.py`, `backend/app/ai/state/student_two.py`, `backend/app/ai/Cognitive/simple/events.py` | Decision ownership, typed snapshots, validation and rendering | Versioned memory input, evidence references, additional-recall semantics and compatible trained model evaluation |
| `backend/app/services/memory_reconcile.py` and `memory_corrections.py` | Durable jobs, grounding, holds and correction processing | Typed dependent operations, content versions, conservative failure handling |
| `backend/app/services/memory_conflicts.py` | Explicit unresolved state and settlement | Settlement events linked to questions, thoughts and queued speech |
| `backend/app/services/thought_memory.py` | Typed persistence of thought conclusions | Preserve conflict/claim dependencies when deriving working questions or intentions |
| `backend/app/services/mind_conversation.py`, `mind_hard.py`, `autonomous_speech.py` | Conversation thoughts and Student 2 speech decision | Quiet-episode pacing, premise checks, question resolution, semantic repetition suppression |
| `backend/app/services/coordinator.py`, `background_components.py`, `backend/app/workers/chat.py` | Existing durable work and delivery machinery | Evidence-version propagation, final validity checks, faithful execution of the communication brief |
| `backend/app/ai/tools/memory/`, `backend/app/api/routes/memory.py` | Existing memory read/write entry points | Route applicable operations through the same mutation/invalidation contracts |

`recall_references.for_reply()` already performs a bounded search and can be adapted. Calling it earlier alone is insufficient: `student_two.gather()` currently builds a Live object without a dedicated memory bundle. Both the orchestration and snapshot contract must change.

The existing `memory_reset.py` is a broad testing/reset utility. It is not the implementation of a user's scoped “forget my knee” request. Existing `core_memory` concerns Columbina's identity; do not repurpose it as the user's standing-memory core.

## 3. Memory organization: core, index, detailed records

### User memory core

Create a compact, derived view named distinctly from `core_memory`, for example `user_memory_core`. It contains references to current canonical records, not another independently editable copy of their facts.

Include standing constraints such as the peanut allergy. Explicit privacy preferences guide conversational judgment; they do not add special search authorization gates or automatic sensitive-topic hiding. Use a deterministic, indexed lookup for these records; semantic-search failure must not remove them. Include a bounded relationship directory or selected relationship pointers so “my sister” can resolve to the correct entity. Multiple sisters or ambiguous roles must remain ambiguous rather than arbitrarily selecting Nora.

Constraint status, scope and uncertainty must remain visible. A broad privacy preference is not automatic authorization to erase every health fact; execute the scope requested by the user. For the regression fixture, forgetting the knee must preserve the allergy.

### Small topic directory (built on read)

Stage-5 decision: build an owner-scoped directory from current eligible records when read, rather than storing a separately rebuilt index. It covers facts, episodes (including standalone episodes) and working items. Example entries:

- Family: sister Nora; residence history and visit plan available → entity and record references.
- Pets: dog Pretzel; childhood cat Biscuit → entities and independent claims.
- Activities: pottery class; schedule history available → class, schedule and source references.

An entry has a topic/entity key, short routing description, referenced record IDs and current content versions. Counts and examples come from a consistent read, with omitted domains, pages and stores stated explicitly. It helps choose records and supports broad-rundown coverage. It is never the only copy of a detail and never silently outranks the underlying fact.

Keep the prompt-visible index small. The complete searchable directory can be larger. Select relevant entries plus a bounded topic inventory for broad requests; do not place an unbounded list of people or facts in every prompt.

There is no stored topic index or background rebuild in stage 5. Construct the directory from current eligible records, respecting forget fences and dependency state. Entity/relationship routing for initial recall remains required; the full rundown is available through a tool rather than injected into every prompt. Forgetting excludes the affected active records before acknowledging completion.

### Detailed stores

| Store | Meaning |
| --- | --- |
| Claims | Independently updateable assertions about an entity and attribute |
| Episodes | Events or experiences with original context and source evidence |
| Plans | Future intentions, participants, destination and scheduled interval; initially a typed working item if that fits existing lifecycle semantics |
| Working items | Unfinished questions, commitments, decisions, hypotheses and intentions |
| Conflicts | Unresolved disagreement with a claim/version, including the user's actual words |
| Transcripts | User messages and delivered/heard assistant text; searchable evidence, not automatically factual knowledge |

Choose one canonical representation for a plan. If a plan is a working item, any episode about its creation is historical evidence, not a second editable current plan.

## 4. Claims, entities and time

Extend the current fact model incrementally with stable entity identity, attribute identity and qualifiers. A claim must be independently updateable; do not make subject+attribute uniqueness so rigid that it prohibits multiple siblings, preferences, homes or qualified assertions.

Example for a stable dog entity:

- `name = Pretzel`
- `dislikes = rain`

The correction changes the name claim and establishes Biscuit as a different childhood-cat entity. It cannot discard the rain claim. Entity merges and splits require source support; a model's similar wording is not enough to merge people or pets.

Keep original wording, source evidence, current status and history. Separate a semantic/content version from confirmation count, evidence updates and access metadata. During our test, D8 advanced the dog fact counter from 2 to 3 with identical content. Dependencies should not treat that as a changed premise.

For temporal records preserve:

- Original expression and source message timestamp/timezone.
- Event, scheduled, and recorded time as distinct concepts.
- Resolved interval, precision, interpretation confidence and ambiguity where needed.
- For schedules: local weekday/time, timezone and recurrence; do not infer a timezone solely from current server location.

“December” may resolve to a December interval with an inferred year, but that inference must remain labeled. “Last month” is anchored to the source timestamp, never to the date of a later recall. If more than one interpretation is plausible, preserve alternatives or unknown bounds rather than inventing certainty.

The extractor is a major part of this migration. It must propose entity/attribute matches, preserve unrelated claims, handle two corrections in one message, and distinguish a historical statement from a current assertion. Test extraction separately with unfamiliar names and paraphrases, then test it end-to-end.

## 5. Provenance and dependencies

Provenance answers “where did this come from?” Dependency answers “what premise must still hold for this record to be usable?” Keep these separate relationships.

Extend MemoryEvidence with valid claim/fact targets and enforce target ownership and referential integrity. Its current item target is not foreign-keyed; audit and repair or quarantine invalid legacy endpoints before adding constraints. Preserve source roles such as origin, correction and resolution. Do not create a competing evidence store.

Add explicit versioned dependencies for working items, plans, summary statements, and affected episode interpretations. Use concrete constrained endpoint columns or an owner-scoped record registry; avoid unconstrained `(type, id)` pairs. Select the schema through a migration spike that proves foreign-key and delete behavior for every supported endpoint.

Each dependency records the premise, meaningful version checked, relation/role, dependent record and validity state. Index both premise→dependents and dependent→premises. Distinguish required premises from contextual references so an incidental link does not block unrelated work.

Use `supersedes` for replacement and the existing conflict record as the authority for unresolved disagreement. A graph-like conflicts_with edge, if exposed, is a projection of that record, not another separately mutable conflict status.

Shared source messages remain candidate-discovery clues for legacy records. They do not prove dependency. For new records, create explicit dependency links when the record is derived. For legacy records, bounded matching may propose links; uncertainty is retained rather than stamped as checked.

Historical truth remains intact: “visited Lisbon last year” does not become false when Nora moves. A future visit or a summary asserting current Lisbon residence can need review. Track those distinctions at the relevant claim or summary span rather than flagging every mention of Lisbon.

## 6. Write, settlement and reconciliation flow

1. Ingest a source with stable message/observation identity and ownership.
2. Discover candidate entities and claims using entity/attribute, lexical and semantic retrieval. Do not rely only on the most recent 60 facts.
3. Ground the proposed change in source evidence and compare against the expected content version.
4. In a transaction, accept a supported change, record an unresolved conflict, or record confirmation. Persist the corresponding durable invalidation/reconciliation event in that transaction.
5. Make affected dependencies visibly pending before they can be used autonomously; invalidate core/index/summary projections.
6. Reconcile dependent records through typed operations: retain as checked, revise, resolve a question, cancel an obligation, supersede a decision, or flag for review.
7. Commit results only if the source and dependent versions still match. Retry on concurrent change using existing worker lease/idempotency conventions.
8. Rebuild derived projections; publish completion with evidence references.

Use the existing revision/coordinator machinery where its semantics fit. If settlement or forgetting needs a separate durable event kind, introduce it explicitly; do not manufacture a content change just to trigger reconciliation. All mutation entry points, including tools and API edits, must use the same lifecycle contract.

A `conflict_resolved` event is mandatory. It carries conflict ID, outcome, settling message and related dependency references. It closes a linked clarification question or marks it for explicit review, invalidates queued speech about that question, and drops still-waiting conflict thoughts. Completed thought history remains historical; its derived working question must still be resolved.

Terminal reconciliation failure releases leases, not semantic holds. Leave affected required dependencies unchecked/needs_review, prevent their autonomous execution, expose the failure to readers, and provide bounded retry/recovery. Unrelated records remain usable. The model proposes operations; code validates source support, scope, versions, ownership and permitted state transitions.

## 7. Automatic initial recall and deeper recall

### User-message pipeline

Persist the user message → check scoped memory operations → read the current user core → resolve relevant entity/relationship/index references → bounded initial search and expansion → build one evidence bundle → supply it to cognition and the mouth → decide/respond or request additional work.

The initial recall happens even if Student 2 might later choose silence. It must not depend on Student 2 first deciding that recall is necessary.

Reuse recall.search/read and TurnRecall. The prior-answer cache becomes one candidate source, not a substitute for relevant retrieval. Fresh conversations must work without cached references. Carry any applicable user constraint alongside topic evidence without pretending it answers the topic question.

Start with existing `recall_auto_limit`, history-depth and character budgets. Measure latency and coverage before changing them. Reserve space for standing constraints so ordinary ranking cannot crowd them out. An embedding timeout should fall back to indexed entity/lexical retrieval; total failure is explicit unavailable/partial, never “you never told me.” Bound latency and cancellation, avoid holding a database transaction while models run, and keep recall read-only except for existing access/receipt bookkeeping.

### Evidence bundle contract

Introduce a versioned typed bundle shared by the turn services:

- Owner, trigger/turn ID, as-of time and bundle ID.
- Search status: matched, no_match, partial, unavailable; searched stores and omitted coverage.
- Current constraints and claims, historical claims, future plans, unresolved conflicts and relevant open items in distinct sections.
- Stable record references, content versions, evidence/source references, temporal precision and uncertainty.
- Directory as-of state, record versions and dependency validity; stale and unchecked records clearly marked.
- Bounded rendered text plus machine-readable records, available expansions and budget usage.

Student 1's memory-related reading, Student 2 and the mouth must receive consistent evidence versions. They can receive different sized renderings, but consumer-specific receipts must identify exactly what each saw. Never silently replace the evidence after a decision without recording the delta and reconsidering a decision whose premises changed.

### Student 2 integration

Extend Live, snapshot rendering, reference mapping, validation and the served model manifest together. Add stable memory references in the contract; do not repurpose goal/thought references as memories. Treat this as a new snapshot version, with explicit model compatibility checks.

Student 2 decides whether to answer, ask, stay silent, think, or request more evidence. Its recall decision now means **additional recall**. Preserve existing output labels only if they express that meaning unambiguously; otherwise version the decision contract too.

Prepare evaluation/training situations with useful, absent, conflicting and unavailable evidence. Test that the current model actually uses the new inputs; do not assume adding prompt fields makes a trained model compatible. An incompatible or invalid model output is a labeled failure with a bounded operational fallback that retains available evidence and performs no invented work. Do not let fallback erase constraints or report unsupported deletion success.

Additional searches use the existing tools/coordinator with an explicit query, purpose and remaining budget. Feed new evidence back to cognition when it could change the chosen action, especially on conflict or missing information. Prevent unlimited search/redecision loops and duplicate retrieval.

### Retrieval strategies

| Request | Retrieval strategy and expansion |
| --- | --- |
| Dog name | Resolve pet entity; current name claim; identity corrections when needed |
| “My sister in Lisbon” | Resolve relationship without requiring “Nora”; fetch residence and open conflicts |
| Dinner/snack | Standing allergy constraints plus participants' food preferences |
| Pottery current time/history | Current schedule, superseded schedule and supporting messages |
| Visit date | Future plan, scheduled interval, original time expression and uncertainty |
| Earlier rivers/oceans answer | Tool-only owner-scoped delivered/heard transcript search; exact and differently worded questions must pass |
| Quick rundown | Read-time directory tool covering facts, episodes and working items; consistent counts and explicit omissions |

For same-turn discrepancies, retrieving a disagreeing fact permits an uncertain clarification, not a persistent overwrite. Background consolidation remains the durable conflict writer unless a later design explicitly moves that operation. Distinguish a provisional comparison from a persisted conflict so the two paths cannot create duplicates or disagree about settlement.

## 8. Scoped forgetting

Create a dedicated user-facing operation service, separate from memory_reset. Detect a plausible forget request before ordinary response routing; use a typed intent resolver where language is ambiguous. Validate and resolve its target scope from the user's own words and relevant records. Ask only when the scope is genuinely unclear, not when an explicit target is already known.

First fence the target against active-memory recall and autonomous use with a versioned deletion/invalidation state. Then remove or redact the applicable claims, episodes, working items, summaries, index entries, embeddings, pending proposals and retained trace payloads. Purge or invalidate replicas/caches and prevent jobs based on older source versions from reinserting it. Completion is idempotent and observable.

Stage-5 policy: “forget this detail” removes active memory and stops unprompted use, while the original conversation and forget request remain searchable when the user asks to revisit history. Historical evidence must not automatically recreate active knowledge, including through background extraction or processing a retrieved transcript. Preserve unrelated source spans in multi-topic messages; if separation is uncertain, temporarily exclude the whole source from ordinary recall/extraction, retain unrelated independently stored facts and report partial completion. This fallback does not block explicit historical transcript access. Scan relevant history resumably: 30 messages is a batch size, not total coverage. Do not claim completion if scoped cleanup or source fencing is unfinished. “Delete this conversation” has broader semantics, and actual deletions remain excluded from search and receipt rereads. See the stage-5 spec §4.

Keep minimal content-free tombstones/receipts where needed to prevent resurrection. Do not create an audit copy of the forgotten text; retained original transcripts follow the explicit historical-access policy above. Acknowledge completed forgetting only after its required operations have succeeded; if delayed or failed, report that state accurately. In the regression case, remove the knee information while retaining peanuts.

## 9. Autonomous speech and quiet-time behavior

Carry premise versions, conflict/question IDs, originating thought and communication intent through mind → coordinator → Student 2 → chat job → delivery.

Revalidate before proposal acceptance and immediately before delivery:

- Owner and conversation are still valid.
- No later user message, forgetting fence or content/dependency change invalidates the proposal.
- Its question is still open and its conflict still unresolved when those are required premises.
- Equivalent intent/question has not already been delivered in this quiet episode.
- Required dependencies are checked; failed reconciliation cannot be bypassed.

Use the existing delivery lease/idempotency mechanism to reserve a question/intent delivery key transactionally. Base repetition keys on question/conflict IDs and intent, not only generated sentence text. Legacy/free-text proposals need a bounded conservative equivalence check. On rejection, store a reason rather than generating another paraphrase of the same question.

Define a quiet episode around external user activity. Assistant speech must not create a new opportunity to ask the same unanswered question every sweep. Persist cooldown/consumption state so worker restart cannot reset it. Genuine new evidence or a new user turn can justify reconsideration; elapsed time alone cannot reopen a settled conflict.

Preserve the communication brief's meaning through wording. Pin known entity-role assignments and the selected question intent in a typed speech plan. Validate the delivery path with contrast cases before deciding whether an additional model checker is necessary. Do not add an unconditional judge call to every reply. If a check detects unsupported entity swaps, withhold/recompute the proposal under bounded retries.

A correctness fence can suppress stale speech; it does not make memory choose to speak. Student 2 remains the communication decision-maker within valid proposals.

## 10. Observability and privacy

Extend existing recall/shared-state/Student 2 traces rather than creating an unrelated tracing system. Capture initial and additional recall, candidates/selection reasons where practical, supplied references and versions, the exact bounded renderings each consumer received, decision and delivery identifiers, and explicit degraded states.

Exact payload retention should be configurable and time-bounded, owner-scoped, and subject to forgetting. In normal operation retain references/hashes plus the minimum needed diagnostics; detailed payload capture can be enabled for isolated tests. Include the directory as-of state, core versions and temporal interpretation so later replay does not substitute today's state for answer-time evidence.

Record mutation events, dependency invalidations, reconciliation attempts and terminal failures, conflict settlement, question closure, delivery suppression and duplicate prevention. Provide an inspection view connecting a source message to changed records, dependent work, recall receipts and actual speech.

Measure factual coverage, constraint inclusion, correction preservation, successful forgetting, conflict accuracy, repeated-question rate, invalid Student 2 decisions, and latency. Keep deterministic replay of stored decisions separate from fresh model reruns; neither is automatically evidence of the other.

## 11. Implementation sequence and release gates

| Stage | Work | Required gate before moving on |
| --- | --- | --- |
| 0: Baseline and contract design | Preserve completed test evidence; specify bundle, event, dependency and model compatibility contracts; inspect existing memory mutation entry points | Written ownership map and tests for the two unexercised hold/settlement races; no need to rerun the entire unchanged baseline merely to begin |
| 1: Automatic recall and core | Reuse search before cognition; add derived core/index routing; wire one bundle into Student 1, Student 2 and mouth; validate compatible Student 2 snapshot/model | Fresh-conversation dog, dinner, health and sister-reference tests receive the correct evidence; invalid Student 2 output cannot remove it; measure added latency |
| 2: Forgetting and autonomous consistency | Add scoped forgetting; explicit conflict→question dependencies; settlement events; final delivery fences; quiet-episode deduplication | Knee gone from all eligible memory paths without losing allergy; D8 closes pet question; no repeat during 15-minute idle; racing queued speech suppressed |
| 3: Claims and time | Add stable entities/attributes, independent claims and time fields; update extractor; expand dependency/evidence endpoint schema | Name correction preserves rain aversion; December and anchored last-month recall work; multiple entities/attributes stay distinct; source-backed migration validated |
| 4: Full dependency reconciliation | Extend stage-2 links to plans, decisions, summaries and relevant episode interpretations; replace legacy shared-source assumptions | Nonempty holds, multi-change corrections, terminal failures and concurrent updates preserve validity; no unchecked autonomous use |
| 5: Transcript search and maintenance | Tool-only delivered/heard transcript search; read-time rundown directory; bounded duplicate-fact merging; selective active-memory forgetting and relearning fences. No summary refresh or automatic entity merging | Exact/paraphrased rivers/oceans recall with correct attribution; rundown covers all stores and domains with omissions explicit; active forgetting survives extraction/upkeep while explicit historical access works; stored/supplied/said evidence over repeated conversational runs |

Stages share the contracts from stage 0. Stage 2 uses existing fact/conflict IDs, so it need not wait for an atomic-claim migration. Stage 3 must retain or explicitly map those references. Each stage includes storage, supplied-evidence and delivered-answer checks; passing a database assertion alone is insufficient.

## 12. Migration and rollout safeguards

- Start on the isolated memtest database. Implement additive migrations and restart compatible workers only after migration succeeds.
- Preserve existing UUIDs where semantics remain the same. Splitting a composite fact creates mapped claims with provenance; keep a compatibility reader for old references until dependencies, receipts and caches are accounted for.
- Backfill in bounded, restartable batches with source/version checks. Never guess an entity identity or exact date merely to fill a non-null field; unresolved legacy records remain supported.
- Audit orphan and cross-owner endpoints before adding constraints. Quarantine invalid legacy links instead of silently granting them validity.
- Avoid independent competing writers during transition. Route old and new APIs through the same mutation service; derived compatibility views must not become another source of truth.
- Feature-gate the new orchestration and snapshot/model version as a pair. Shadow comparison may measure retrieval, but only one path may mutate memory or deliver speech.
- On rollback, stop new work, drain or fence incompatible jobs, and return to a compatible reader/model. Do not roll back deletion tombstones or resurrect forgotten data. Prefer forward schema compatibility over destructive down-migrations.
- Evaluate chat, voice, resumed jobs, background follow-ups and game-related paths that share Student 2/recall. Cancellation, user ownership and conversation routing remain enforced across all entry points.

## 13. Acceptance matrix

| Scenario | Stored | Supplied | Said / acted |
| --- | --- | --- | --- |
| Dog correction | Pretzel name and rain aversion both retained; Biscuit distinct childhood cat | Correct entity/claims in initial bundle | Correct dog answer in a fresh conversation |
| Two preferences in one correction | Mushroom/cilantro states and history consistent | Current preference plus allergy | Dinner/snack honors both; no stale promise |
| Sister without proper name | Relationship resolves appropriately; ambiguity preserved for multiple sisters | Porto and open disagreement before decision | Can clarify uncertainty without confidently overwriting or inventing a visit |
| Conflict settlement with unchanged wording | known_confirmed plus dependent question resolved | No obsolete open question or unresolved annotation for the settled dog | No delayed or quiet-time repeat; waiting thought dropped with reason |
| Real nonempty dependency hold | Related item held; unrelated item sharing the message remains usable | Pending/failed status visible | Autonomous action blocked until valid; terminal retry failure stays safe |
| Concurrent edit or delete during thought | Expected-version write/fence rejects stale results | Fresh versions or explicit unavailability | Stale prepared speech does not deliver |
| Forget knee | Named active records removed or excluded; old sources and retrieved history cannot re-extract it; unrelated allergy remains | No knee in active recall, rundown or cache; original mentions and forget request available as labelled history when asked; actual deletion still enforced | No unprompted knee recall; truthful completion/partial state; health answer retains allergy; historical access does not restore active memory |
| December and last month | Original words plus uncertain/precise anchored intervals | Plan time distinguished from recorded time | No “this month” drift or invented precise date |
| Historical Lisbon visit | Historical episode remains true; future destination reviewed separately | Past/current/future clearly distinguished | Does not rewrite the past |
| Old fact outside recent extraction window | Correct older claim found and matched | Relevant old record supplied | Correct correction/conflict behavior |
| Original assistant wording | Delivered/heard transcript searchable and attributed, including paraphrased queries | Actual river/ocean answer, not reconstructed guess; cross-user/deletion/heard checks enforced | Actual tool use and faithful answer over repeated conversational runs; uncertainty if missing |
| Broad rundown | Read-time directory and canonical facts, episodes and working items consistent | Counts/examples agree; domain, store and page omissions explicit | Useful rundown without forgotten active facts; privacy handled by conversational judgment, without automatic count-only hiding |
| Idle for 15 minutes / worker restart | Quiet episode and consumed question state survive | Settled/pending states current | No paraphrased repeats; speech remains Student 2's choice |
| Student 2 invalid/incompatible / recall timeout | No unsupported mutations | Constraints retained; failure distinct from empty results | Honest bounded fallback; no invented success |
| Cross-user references and deletion races | Database and service ownership enforced; actual deletion distinct from active-memory forget fences | No foreign or deleted content; forgotten sources available only for requested historical access | No leakage or automatic resurrection of forgotten active memory |

Use unfamiliar contrast cases as well as Nora/Pretzel/Leo so the extractor and decision model cannot pass by memorizing the regression script. Record repetitions and variation in model-based results. Keep the original 14-question score comparable, and score forgetting, dependency holds, concurrency and autonomous repetition separately.

## 14. Definition of done

The system is complete when the index, core, claims, dependencies, recall bundle, Student 2 input, reply generation and autonomous delivery share one version-aware lifecycle; all acceptance gates pass on the isolated database; migrations and model-contract compatibility are documented; and the report demonstrates stored, supplied and said evidence for the failures being closed.

No stage is complete merely because a migration succeeds or an assistant agrees that it remembered, corrected, or forgot something.
