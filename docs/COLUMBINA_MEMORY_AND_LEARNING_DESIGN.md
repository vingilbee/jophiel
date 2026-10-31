# Columbina: general memory, observation, and learning

Status: proposed architecture for review. This document records the direction discussed with Vincent; it does not authorize or implement runtime changes. Concrete table names, thresholds, and worker deployment remain proposals.

Date: 2026-10-20

## Purpose

Columbina should maintain continuity across conversation, games, projects, and everyday activities. Minecraft is a useful test environment and one of Vincent's hobbies, not the organizing principle of her memory.

Build a shared system for recent conversation, recent events, goals, open commitments, lasting knowledge, and experiences. Learning uses these experiences to improve future behavior. New capabilities should integrate through common contracts rather than require an independent memory architecture.

General architecture does not mean one physical table for everything. Different records need different constraints, but their identity, context, evidence, and lifecycle should work together. Specialized tools may retain technical state such as terrain maps, inventory, or document files; Columbina's understanding of what happened belongs to general memory.

## Existing foundations and gaps

These findings are from source inspection, not a live database audit. Configuration on disk does not establish which settings a running process loaded.

| Existing component | What it provides | Gap to address |
| --- | --- | --- |
| Chat messages | Exact conversation history; a bounded recent window for replies | Conversation history alone does not maintain current events or persistent goals |
| Semantic memory | Facts, preferences, source references, confidence, updates, deletion, relevance-ranked retrieval | Context validity and correction propagation are incomplete |
| Episodic memory | Dated summaries, context, provenance fields | Consistent activity boundaries and links to supporting evidence are needed |
| Live conversation notes | A small set of commitments, corrections, questions, and offers | Conversation-scoped and separate from other working state |
| Working-memory table | Cognitive state plus goal/question fields | The inspected live writer does not populate the goal fields |
| Dialogue working memory | Richer structured dialogue snapshots | Shadow-only; must be assessed for reuse before introducing overlapping machinery |
| Game planning | Goal, steps, revisions, completion/wait decisions | Session-specific rather than general goal management |
| Game learning | Recorded outcomes, evidence-linked reflection, trial/confirmed/retired lessons | Generalize beyond games and improve attribution and validation |
| Mind | Thoughts, intentions, questions, activity adapters | Memory access and unfinished business are managed through separate paths |
| Learning-memory model | Situation, prediction, response, outcome, feedback, provenance | General lifecycle and integrations still need design; no database row-count claim is made here |

Relevant source files:

- `backend/app/db/models.py`
- `backend/app/services/long_term_memory.py`
- `backend/app/services/cognitive_live.py`
- `backend/app/ai/Cognitive/simple/notes.py`
- `backend/app/services/dialogue_memory.py`
- `backend/app/services/game_learning.py`
- `backend/app/services/game_planning.py`
- `backend/app/services/mind_hard.py`
- `backend/app/services/mind_conversation.py`

Observation is therefore not entirely absent: messages, tool outcomes, and game events already exist. The missing work is a shared observation contract and processing path, a coordinated memory manager, and a general learning lifecycle.

## Responsibilities and connections

| Component | Responsibility |
| --- | --- |
| Observation adapters | Capture messages, action results, environmental changes, and their provenance |
| Memory manager | Maintain current state, context, goals, commitments, corrections, consolidation, and retrieval |
| Mind / cognition | Interpret evidence, retrieve relevant memory, consider alternatives, and choose what to say or do |
| Mouth / hands | Deliver speech or execute actions; report actual delivery and execution results |
| Learning coordinator | Associate outcomes with attempts, request reflection, manage candidate lessons and validation |

The normal loop is:

1. A message or observation is recorded.
2. Relevant working state is updated or marked uncertain.
3. Cognition reads a bounded memory view and chooses an action.
4. Speech or execution reports what actually happened.
5. Later observations or user feedback attach outcomes to the attempt.
6. Reflection proposes corrections or lessons, citing evidence.
7. The memory manager applies supported memory changes; the learning coordinator tracks evaluation and any future training use.

These components should communicate through explicit records and operations. They should not independently overwrite the same state. A thought is an interpretation or proposal, never proof that an external event happened.

## Memory organization

### Recent conversation

Keep exact recent messages available for dialogue, including corrections and what the user actually heard. Preserve message IDs so derived records can be traced back to their sources.

Remove older messages from active model context according to capacity, inactivity, and consolidation boundaries. This does not require immediately deleting archived messages. Active-context eviction and permanent retention/deletion are separate policies.

### Short-term memory

Maintain a bounded representation of recent events, relevant observations, current circumstances, and uncertainties. Observations belong here when they are useful; they do not need a separate memory silo.

Keep these distinctions explicit:

- Observed: the tool reported an error.
- Reported: Vincent said the result was wrong.
- Inferred: Columbina thinks she misunderstood the request.
- Hypothesized: a missing prerequisite might explain the error.

An observation preserves what its source reported, not a guarantee that the source was correct. Record when it occurred and when it was received, since delayed reports are possible.

Repeated unchanged observations can be deduplicated or combined. Meaningful transitions, corrections, failures, and evidence supporting lessons should be retained. Avoid feeding every sensor sample into a model.

### Goals

Goals represent what Vincent, Columbina, or both are trying to achieve. Steps are subordinate to goals and must not silently replace them.

Suggested states: active, suspended, completed, abandoned. Track ownership, parent goal where applicable, relevant context, evidence, and completion conditions.

Consolidation and elapsed time must not automatically complete or delete an unfinished goal. A context switch may suspend a goal, transfer it when justified, or leave it applicable; it should not mechanically invalidate every goal.

### Open commitments

Track promises, unanswered questions, and follow-ups with an owner, target, context, source, and resolution evidence. Distinguish a promise from an offer or a prediction.

Suggested states: open, fulfilled, cancelled, needs_review. An overdue commitment remains unresolved until evidence or an explicit decision closes it. It can leave immediate attention without being forgotten.

### Long-term memory

Retain useful knowledge and summarized experiences:

- Knowledge: facts, confirmed preferences, scoped beliefs, and lessons.
- Episodes: what happened, when, with whom, in which context, and what remained unfinished.
- Procedures or skill knowledge: when a method applies, prerequisites, steps, expected result, and evidence of effectiveness.

Not every observation needs a permanent summary. Not every summary should become knowledge. Specialized executors remain responsible for executable details; knowing a procedure does not establish the ability to execute it reliably.

## Context and time

Support several concurrent contexts for a user: a conversation, project, activity, or game world. A conversation may span multiple contexts, and a project may span several conversations.

Separate changing attention from changing reality. Starting a new topic does not invalidate a project. Starting a new world can invalidate a remembered location while leaving the larger goal intact.

Memory records should distinguish event time, recorded time, last confirmation, and validity where applicable. Age influences retrieval and review, but does not alone establish relevance or truth.

World-specific observations must not be presented as current facts in another world. Historical recall remains possible with explicit historical framing. Broader lessons can remain applicable across worlds or activities when their scope supports it.

## Memory manager

The manager owns coherent memory transitions, not an unrestricted model that rewrites everything. Code enforces identity, scope, status transitions, versions, and evidence links. Models propose semantic judgments such as whether an instruction is a step or a new goal.

Required operations:

- Add or update a current observation or belief with provenance.
- Maintain goal/step relationships and open commitments.
- Apply context changes without erasing unrelated activities.
- Resolve corrections and invalidate dependent claims where needed.
- Consolidate a bounded stretch of experience.
- Retrieve a compact, context-aware view for cognition.
- Mark uncertain, stale, superseded, or retracted records explicitly.

Corrections must reach beyond a new chat note. Link a mistaken source or interpretation to derived facts, summaries, lessons, and learning records. A correction can retract, revise, or flag those dependents for review. If its target is ambiguous, retain uncertainty rather than confidently altering unrelated memory.

Retrieval should prioritize relevant active goals, commitments, current state, and recent changes, then retrieve additional history as needed. Use validity and scope as well as similarity. Return dates and uncertainty where they affect interpretation. Historical queries should still be able to retrieve inactive contexts.

## Consolidation

Trigger consolidation using a combination of capacity, inactivity, activity boundaries, and meaningful outcomes. Exact thresholds require testing; this document does not fix them.

A consolidation job should:

1. Select a bounded, versioned source range for a coherent context.
2. Identify relevant events, corrections, and unresolved business.
3. Propose an episode and any durable knowledge candidates.
4. Preserve unresolved goals and commitments explicitly.
5. Validate references and commit the derived records atomically.
6. Evict eligible detail from active context only after successful persistence.

Do not merge episodes merely because they occurred within the same evening. Preserve meaningful context boundaries. Consolidation should not repeatedly summarize summaries until original uncertainty or evidence disappears.

Example result:

> Experience: We started a new world and gathered materials toward a portal. Columbina died and lost the logs.
>
> Goal still open: Reach the Nether.
>
> Commitment still open: Collect replacement wood.
>
> Requires fresh observation: Current inventory and location.

## Persistent runtime state and ticks

The agreed direction is a stateful runtime, not managers that exist only when directly requested. Observation, memory, mind/cognition, and learning should each receive a relevant view of the current state and evaluate on each scheduled tick whether useful work is warranted. New events can request an earlier tick. Time passing can itself make work eligible even when no new message arrives.

This generalizes the sense of ongoing activity already visible in Minecraft. The existing mind Source/Body contracts and activity-specific gates are foundations to extend, not a reason to introduce another independent mind. Conversation currently has a shadow source; a general runtime must work without a live game session.

### Shared state

Maintain a durable per-user runtime state, with separate context/activity state beneath it. A shared snapshot describes what is happening; each component receives only the fields and evidence relevant to its responsibility, not the entire memory store.

Suggested snapshot fields:

- Identity, snapshot version, current time, and processed event boundary.
- Runtime lifecycle: running, paused, or stopped; current load and available processing budget.
- Active and suspended contexts, current focus, and known user presence/interaction state. Unknown presence remains unknown.
- Enabled observation sources, their last reports, freshness, connectivity, and incoming changes.
- Relevant goals, current steps, open commitments, unresolved questions, and due review times.
- Recent conversation/delivery state and meaningful recent events.
- Pending corrections, contradictions, memory capacity, and consolidation eligibility.
- Actions in flight, recent results, and execution failures.
- Learning attempts awaiting outcomes, new feedback, and candidate lessons awaiting evaluation.
- Current thinking, queued jobs, last gate decisions, cooldowns, and next eligible wake times.

Authoritative event and memory stores remain the source of truth. This snapshot is a versioned projection with references, not a second independently editable copy of every fact. Components propose changes through the existing owners. Keep processing metadata separate from memories about the world.

### Tick and activation contract

Each component evaluates a gate using its state view, changes since its last processed boundary, and elapsed time. A gate returns a structured decision such as `skip`, `defer`, `apply_light_update`, or `enqueue_work`, with a reason, priority, evidence boundary, deduplication key, and next eligible time.

| Component | Examples of reasons to activate | Examples of reasons to stay idle |
| --- | --- | --- |
| Observation | Source is due for a refresh, new input arrived, freshness changed, connection recovered | Source unchanged, refresh not due, source unavailable with retry backoff |
| Memory | Correction arrived, context changed, new event changes current state, capacity threshold reached, inactivity makes consolidation eligible | No meaningful changes and no maintenance due |
| Mind / cognition | User turn requires a reply, meaningful surprise, unresolved question worth revisiting, goal blocked, commitment needs attention | Already thinking about the same evidence, no useful next step, cooldown in force |
| Learning | Attempt received an outcome, user supplied feedback, evidence contradicts a lesson, reflection boundary reached | No new evidence, outcome still unknown, same experience already reflected on |

Most ticks should be cheap state checks and can legitimately do nothing. A tick does not imply four model calls. Use deterministic eligibility checks first; invoke semantic judgment when there is a concrete candidate question. Observation capture still persists incoming events immediately, independently of tick cadence.

Activation can lead to internal work without speech or an external action. Whether to think, speak, or act are separate decisions; user interaction preferences, interruption state, and action permissions still apply. Background thought does not require announcing every conclusion.

### Scheduler and lifecycle

A long-lived coordinator schedules bounded ticks for enabled users/contexts while the application service is running. Event wakeups reduce latency; scheduled ticks cover inactivity, freshness checks, due commitments, consolidation, and delayed outcome review. The scheduler must not require Minecraft, an open chat socket, or a fresh user request to consider due internal work.

Persist component cursors, next-due times, pending jobs, and enough state to recover after restart. In-memory state can accelerate processing but must not be the only record of unfinished work. Running state means enabled and schedulable, not that every worker must continuously consume CPU. Tick cadence may adapt to active interaction, quiet periods, source refresh needs, and resource budgets; exact intervals remain to be measured.

Explicit pause/stop controls govern activation. On restart or resume, reconcile missed events and due work without replaying every missed timer tick or flooding the user with stale follow-ups. A disconnected source supplies stale/unknown state, not invented observations. An outcome review becoming due may mark an outcome unknown or request more evidence; time alone cannot manufacture success or failure.

Use one scheduler lease or equivalent coordination per scope to prevent duplicate dispatch. Workers receive the snapshot version and evidence boundary that justified their work. Before applying results, revalidate relevant state; discard or re-evaluate conclusions made obsolete by a newer correction, context switch, or completed goal.

Prevent feedback loops: a thought or summary becoming an event must not indefinitely retrigger reflection on itself. Track causal ancestry, coalesce related work, apply cooldowns, and require new evidence or an explicit due condition for reconsideration. Prioritize current user interaction and critical state changes over background reflection, with bounded concurrency and backpressure.

### Inspectability

Expose a runtime view showing each component's current state, last tick, last processed event, last activation/skip reason, next due time, and work in flight. This makes it possible to distinguish an idle but functioning manager from a stalled one. Gate logs should explain decisions without duplicating full private conversation content.

Example without a game running:

1. Vincent corrects an interpretation. The message is recorded and wakes the runtime.
2. The memory gate sees an unresolved correction and schedules the relevant update; live cognition receives the correction immediately.
3. The learning gate sees feedback on an earlier attempt and schedules outcome association and reflection.
4. The mind gate responds to the current turn; it need not wait for long-term learning to finish.
5. Later, with no new request, elapsed inactivity makes consolidation eligible. The memory gate dispatches it while other components skip.

## Worker architecture

A worker is useful for expensive asynchronous processing, but basic observation capture should not depend on a model worker being available.

| Work | Suggested execution |
| --- | --- |
| Record user message, delivery result, or tool outcome | In the producing path, with a durable event/outbox transaction where possible |
| Normalize external observations; deduplicate and aggregate noisy inputs | Source adapter or observation-processing job |
| Apply immediate, deterministic state changes | Fast memory-manager operation |
| Interpret ambiguous updates or corrections | Model-backed job, or turn-time reasoning when needed for the immediate reply |
| Consolidate recent experience | Background memory job |
| Associate delayed outcomes and request reflection | Background learning job |
| Evaluate lessons and prepare training candidates | Separate learning/evaluation jobs |

These are logical responsibilities, not a requirement for one operating-system process per responsibility. Reuse existing durable job infrastructure where suitable.

Workers need idempotency keys, retries, leases, source ordering, and optimistic version checks. A delayed result must not overwrite a newer correction or plan. Record failures and processing lag for inspection.

Live cognition must see the latest user message and critical action results even when background processing lags. Track the processed event boundary and include or reconcile the relevant unprocessed tail. Do not block every reply on full consolidation or reflection.

## General learning architecture

Observation is input; an experience connects evidence; reflection is a process; a lesson or revised procedure is an output. Model training is a separate possible use of reviewed experiences.

### Experience-to-learning lifecycle

1. Capture the situation and relevant context.
2. Capture the interpretation, chosen action, and expected result at decision time, when available. Missing expectations remain unknown rather than being invented retrospectively.
3. Record actual speech delivery or execution.
4. Attach feedback and outcomes when evidence arrives, possibly several turns later.
5. Reflect on mismatches and alternative explanations.
6. Propose a scoped correction, lesson, or procedure change, or conclude that no generalization is warranted.
7. Apply permitted memory updates and test candidate lessons in relevant future situations.
8. Revise, strengthen, narrow, or retire conclusions based on new evidence.

Possible triggers include explicit teaching, corrections, unexpected results, repeated failures, meaningful success, and an activity ending. Do not require reflection on every event.

### Outcomes and evidence

Do not collapse everything into a single good/bad reward. Track independently:

- Result: succeeded, failed, partly succeeded, pending, or unknown.
- Feedback: what the user or environment actually reported.
- Instruction adherence: whether the action respected the request.
- Assessment: what the evidence might mean and with what uncertainty.
- Attribution: which action, claim, interpretation, or sequence the evidence concerns.
- Scope: where a conclusion is expected to apply.

A user correction is strong evidence about the user's intended meaning, but may not identify the specific error. Silence is not proof of failure; laughter is not proof of approval; disagreement is not inherently a bad conversational outcome.

Reference validation establishes that evidence exists, not that it supports a causal claim. Reflection should distinguish observed result from proposed explanation. Reusing the same evidence or repeating a thought must not increase confidence as if a new trial occurred.

### Different learning outputs

| Evidence suggests | Appropriate output |
| --- | --- |
| A stored detail is incorrect | Corrected fact with provenance and dependent-memory review |
| The user clarifies an instruction | Updated current interpretation or goal; not necessarily a lasting preference |
| The user states a standing preference | Scoped preference |
| A strategy may work under certain conditions | Trial lesson with applicability and counterevidence |
| A repeatable procedure improves | Versioned procedure candidate with prerequisites and evaluation |
| An executor malfunctioned | Diagnosed issue or improvement proposal; not a lesson blaming the agent's choice |
| A prediction was demonstrably wrong | Reviewed learning example for the relevant model, if training use is enabled |

Lessons can improve the next response through retrieval without retraining a model. Training datasets should separately track real versus synthetic origin, model versions, labels and their sources, review state, and train/evaluation separation by related experience. A teacher's judgment remains a teacher label, not observed ground truth.

Training consent, per-user versus shared learning, model promotion criteria, and executable skill modification require separate decisions. This document does not enable automatic training or code changes.

## Proposed record families

This is a conceptual schema to guide a later detailed specification. Reuse existing records where their contracts fit; do not create duplicate stores simply to adopt these names.

| Record family | Essential information |
| --- | --- |
| Context | Owner, kind, parent/related contexts, identity, lifecycle, relevant time boundaries |
| Event | Source/idempotency key, actor, context, occurred/received times, kind, payload reference, provenance |
| Working item | Context, type, content, evidence references, observed/reported/inferred distinction, validity, revision |
| Goal | Owner, desired outcome, parent/steps, status, completion conditions, context, evidence |
| Commitment | Who owes what to whom, source, context, status, optional due time, resolution evidence |
| Episode | Source range, context, summary, time span, unresolved-item references, revision/lineage |
| Knowledge / lesson | Claim, kind, applicability, confidence, status, supporting and contradicting evidence, supersession links |
| Procedure / skill knowledge | Purpose, applicability, prerequisites, method, expected outcomes, optional executor reference, version, evaluation history |
| Learning attempt | Input references/snapshot, model/version, interpretation, selected action, expected result, actual action references |
| Outcome / feedback | Target attempt(s), source events, result dimensions, attribution confidence, assessment version |
| Reflection | Source boundary, evidence references, model/version, proposed conclusions, uncertainty, application decisions |
| Training record | Linked experience, learner, input, prediction, label/source, review state, origin, dataset/model use |

Cross-cutting requirements: user isolation, stable IDs, event ordering, evidence lineage, versioned updates, deletion/retention propagation, and bounded retrieval. Keep domain payloads typed by their source without making every feature invent its own memory lifecycle.

## Suggested implementation sequence

1. Inventory live and shadow components, ownership, producers, and consumers. Decide what to reuse, extend, or retire.
2. Define the shared context/event contracts, durable observation ingestion, and persistent runtime snapshot. Integrate conversation and one action source first, with a general scheduler and inspectable per-component gates.
3. Introduce memory-manager operations for current state, persistent goals, commitments, and corrections. Connect live cognition through one bounded memory view.
4. Add consolidation with evidence lineage and unfinished-business preservation.
5. Generalize experience/outcome association and reflection, reusing the useful parts of game learning.
6. Add lesson evaluation and behavioral retrieval. Design training and executable skill changes separately.
7. Migrate existing stores and consumers incrementally, with explicit ownership and cutover checks so two systems do not independently manage the same goal or lesson.

## Acceptance scenarios

- **Goal continuity:** small instructions change steps without replacing the larger goal; asking about the goal returns the preserved goal.
- **Cross-activity continuity:** switching from a game to interview preparation preserves both contexts and their unfinished work.
- **World change:** old locations become historical; general game knowledge remains available; episode boundaries stay separate.
- **Speech correction:** a misheard preference is retracted or flagged, and summaries and lessons depending on it are reviewed.
- **Conversation consolidation:** exact recent detail leaves active context while open commitments and goals survive.
- **Explicit teaching:** “I wanted to discuss it first” corrects the current interpretation without inventing a universal permission preference.
- **Delayed outcome:** later feedback attaches to the correct earlier attempt rather than automatically to the previous message.
- **Uncertain failure:** a failed action produces an observed failure and a tentative explanation, not an unsupported confirmed cause.
- **No false reinforcement:** reflecting twice on one failure does not count as two independent examples.
- **Background lag:** a current correction reaches the next reply before a delayed worker finishes; stale worker output cannot restore the old claim.
- **Recovery:** retries do not duplicate events or lessons; failed consolidation does not discard active detail.
- **General capability:** a presentation or coding task uses the same memory and learning contracts without another feature-specific memory manager.
- **No-game runtime:** with no desktop/game session and no open chat connection, the enabled service still evaluates due internal memory and learning work.
- **Quiet ticks:** unchanged state produces explicit skip/defer decisions without repeated model calls or unsolicited messages.
- **Time-driven activation:** inactivity or a due review activates the appropriate component without a fresh user request.
- **Runtime recovery:** restart/resume restores unfinished work and cursors without duplicate jobs, replay storms, or fabricated observations.
- **Visible liveness:** runtime inspection distinguishes idle, deferred, running, paused, and failed processing with last/next tick information.

## Decisions still open

- Detailed schema constraints and mapping to existing tables and shadow dialogue memory.
- Working-memory budgets, consolidation triggers, and archived-evidence retention.
- Which semantic updates must finish before a reply and which can be asynchronous.
- Per-component tick cadence, resource budgets, lifecycle controls, and runtime-state projection ownership.
- Confidence and evaluation rules appropriate to facts, preferences, lessons, and procedures.
- Behavior for ambiguous context changes, corrections, and outcome attribution.
- Whether and how real interactions become training data, and how per-user learning remains separate from shared model behavior.

The next design step is to specify one complete conversational-correction loop and one action-outcome loop against the existing code, then finalize record contracts and worker ownership from those examples.
