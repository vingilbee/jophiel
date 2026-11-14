# Post-audit 3: broaden experience understanding

> **2026-11-13:** Student 1, Student 2, the shared-state pipeline, appraisal and mood were retired; one mind now decides every turn and wake. Where this document describes them, it records the system as it was. Current design: [MIND_ARCHITECTURE.md](../MIND_ARCHITECTURE.md).

Status: design agreed through discussion on 2026-11-05. Implementation requested and built the same day ([implementation](POST-AUDIT-03-implementation.md)); behavioral acceptance not yet run. This is the third post-audit work package, not historical step 3 (immediate adaptation).

## Goal

Columbina builds an evolving understanding from available observations, interactions, instruction, and action outcomes. Experience can change what she notices, believes, predicts, considers possible, and chooses to do. She can discover useful learning opportunities without someone explicitly teaching or correcting her.

Learn from meaningful stretches of activity, successes, ordinary observations, surprises, and conversations. A trigger opens an inquiry; it does not define the boundaries of the experience. Learning is not limited to one turn, action, plan step, or session, and can begin while activity continues.

The user's examples of discovering a better route in a game or inferring someone's conversational tendencies illustrate this general capacity. They are not prescribed strategies, special learning handlers, or an exhaustive list of supported situations. Solving a problem through current reasoning is distinct from retaining experience that improves later understanding or behavior.

Completion requires evidence that retained understanding influences later interpretation or behavior after the original context is gone, transfers where appropriate, and changes when evidence warrants revision. Creating memories or logging retrieval alone is insufficient.

## Foundation and precedence

Build on completed [post-audit 1 capture](POST-AUDIT-01-implementation.md) and [post-audit 2 consolidation](POST-AUDIT-02-implementation.md), using the [audit](AUDIT-2026-11-04-learning-from-experience.md) as historical gap context. Preserve the single shared learning lifecycle and completed consumer connections. Do not recreate the old game learner.

The broader architectural reference is [General experience-driven learning](../../../app-cognitive/docs/learning-system/GENERAL_EXPERIENCE_LEARNING_DESIGN.md) in the sibling checkout. This package makes its broader intake, understanding, and later-use goals concrete. The accepted decisions here supersede earlier proposals for a learning spending cap, a fixed reasoning-round limit, or code-defined semantic admission rules. Existing ownership, privacy, retention, deletion, and fixed core-value decisions continue to apply.

## Current implementation baseline

The following was inspected in source during planning. It is not a deployment audit or a claim of successful live-model evaluation. Recheck callers and contracts before implementation.

| Area | Existing behavior and relevant extension |
| --- | --- |
| Shared intake | `backend/app/services/learning_assessment.py` admits corrections, complaints, sampled ordinary messages, interrupted/failed replies, failed effects, failed game skills, and critical game notices. Broader conversation admission needs semantic judgment. |
| Game stretches | `backend/app/services/game_assessment.py` schedules ordinary and successful stretches on session transitions, subject to outcome counts, and partitions long stretches. Retain this capability while allowing meaningful inquiry before session completion and LLM nominations beyond coded events. |
| Evidence and revision | Shared assessment already gathers evidence, requests further records, checks references, and revises conclusions. Extend this lifecycle rather than introducing another independent controller or assessment store. |
| Lesson construction | `backend/app/services/learning_lessons.py` and `backend/app/ai/learning/lessons.py` still center products on `preference` and `strategy`, with restricted eligibility for construction. Broader understanding must not be forced into behavioral advice or causal blame. |
| Memory owners | Semantic and episodic memory, memory-owned lessons, and `WorkingItem` hypotheses/questions already exist. `thought_memory.py` retains selected hard-thought conclusions with reasoning provenance; restatement is not support. Reuse compatible owners and reconcile overlapping products. |
| Interpretation | `cognitive_live.py` supplies Student 1 a limited selection of recalled memories, with no shared lesson supply. A broadening of product creation alone will not establish interpretation use. |
| Conversational cognition | `student_two.py` supplies recalled knowledge, including labelled hypotheses, and accepted lessons. Actual lesson delivery depends on supported input rendering and is separately recorded. |
| Mind | Conversation hard thinking reads recalled and working memory but lacks the explicit lesson supply used by Minecraft hard thinking. Preserve the distinction between an existing hypothesis route and broader shared-product delivery. |
| Game consumers | Hands, planner, and Minecraft mind already receive shared lessons, including supported scoped tentative lessons, and record supplied revisions. Extend only what broader products require. |
| Practice | Trials and executable skill development have existing owners. `learning_trials.py` retains bounded game trial machinery; declarative understanding is not itself an executable skill. |

## Component responsibilities

| Component | Responsibility in this package |
| --- | --- |
| Observation and capture | Preserve source reports, timing, coverage, available inputs, decisions, execution, and measured outcomes. Make existing evidence accessible without inventing absent facts. |
| Interpretation / Student 1 | Interpret the current message using relevant retained understanding, including explicitly tentative possibilities. |
| Cognition / Student 2 | Select current behavior and decide whether and when to pursue learning-generated proposals. |
| Mind | Reason about current situations, produce conclusions and hypotheses, and nominate potentially useful experience. It is not learning's sole gatekeeper. |
| Planner | Develop detailed steps within the authorized goal, informed by relevant understanding. |
| Hands | Select and execute available actions within authorized scope, reporting progress and outcomes. |
| Learning | Independently discover opportunities, examine experience, retrieve related evidence and products, and propose lasting changes or useful follow-up. It does not independently speak or act. |
| Memory | Own persistence, retrieval, evidence links, reconciliation, and revisions of retained products. Learning requests memory rather than becoming a second memory owner. |
| Scheduling and infrastructure | Arrange review opportunities and safe concurrent work; enforce access and record integrity. Do not decide semantic importance or truth. |

## Accepted design

### 1. Code supplies evidence; the LLM judges meaning

The LLM judges learning value, meaningful experience boundaries, relevance, applicability, explanations, uncertainty, and whether understanding should change. Code must not encode situation-specific lessons, causal conclusions, or automatic truth promotion based on an observation count.

Code still enforces ownership, deletion requests, valid source references, retry deduplication, and revision consistency. It can establish that a referenced record exists or that two references describe the same event. Reference validity does not establish that an inference is true or supported. Protection against stale writes must not become a semantic acceptance rule.

Existing count-based acceptance gates and restrictive product eligibility must be audited where they would block this design. Do not replace them with an unapproved universal confidence formula. Evidence integrity remains enforceable even when substantive judgment belongs to the model.

### 2. Different entry points, one learning path

**Conversation:** the existing background learning component owns semantic review. Code schedules opportunities after a completed exchange, with a fallback when the user pauses or a reply does not complete. The reviewer sees the conversation and available interpretation/mind outputs, and judges whether to examine a meaningful stretch, wait for more context, or leave it alone.

Corrections and complaints become evidence signals rather than code deciding that they deserve assessment. Replace deterministic random sampling as the main admission route for ordinary conversation. Failed delivery and interruption remain execution facts; neither automatically establishes a poor conversational choice. A scheduling opportunity need not create a substantive assessment or memory product. New activity arriving during review remains available for a subsequent pass.

**Games:** retain useful existing code triggers, including failures, critical notices, and completed stretches. Also accept LLM nominations for discoveries, emerging patterns, ordinary activity, and surprises that coded events miss. The mind can nominate experience, but background learning can independently examine available activity. It must not require a new semantic handler for every learnable situation.

Both routes enter the existing shared lifecycle. Reconcile overlapping nominations, active inquiries, and assessments so retries or multiple entry points do not create duplicate lessons or independent evidence.

### 3. Ordinary activity and ongoing experience

Ordinary observations and successes receive deliberate review opportunities even when no explicit correction, failure, or surprise is present. Consequential events can take priority, but must not permanently crowd out ordinary activity. This is a coverage requirement, not a numerical spending allocation.

Code may page and bound evidence transport to fit context and execution constraints. Those pages are not semantic episode boundaries. The LLM can select related events across pages, turns, steps, and sessions, and can keep an inquiry open while outcomes develop. Observation-only experience needs no fabricated action, decision, or reward.

### 4. Iterative inquiry through existing components

Learning can request relevant memories, existing lessons, observations, execution records, and related experiences from their owners, inspect the results, then request more and reconsider. It is not a fixed evidence window followed by one judgment.

Responses preserve source identity, uncertainty, revision, and available coverage. Missing, expired, omitted, or inaccessible material is explicit; absence must not be interpreted as evidence that something never happened. Reuse available retrieval interfaces and extend them only where the inquiry requires it.

Compare new experience with existing lessons before proposing another lesson. The outcome may support, revise, narrow, combine, supersede, or leave existing understanding unchanged. Any combination must preserve source lineage, distinctions, exceptions, and revision dependencies rather than erase them. Cross-context comparison is permitted when meaningful; relevance and transfer must be justified, not inferred solely from superficial similarity.

### 5. Broader products and uncertainty

Experience may change attributed knowledge, expectations, relationships, beliefs, explanations, strategies, understanding of her own capabilities, or open questions. These describe forms of understanding, not an exhaustive semantic taxonomy. Not every result must say what to do next. No change is a valid conclusion.

Reuse existing memory owners where compatible and extend representation and retrieval contracts as necessary. Keep reported observations distinct from her inferences and from executable procedures. Preserve applicability, exceptions, supporting and conflicting evidence, alternatives, and revisions.

Relevant uncertain inferences may inform interpretation and conversational cognition immediately, as they already can inform cautious game decisions. They remain guesses. Retrieving a guess, repeating it, acting on it, or having multiple components restate it does not make it factual or provide independent support. A supported observation can justify a narrow hypothesis without establishing its cause or a global claim about a person.

Conflicting evidence may indicate an exception, noise, changed conditions, or an incorrect belief. The LLM may retain competing explanations or revise promptly when evidence warrants it. Strong contradictory evidence need not wait for a fixed number of repetitions. Preserve dependencies between observations from the same event, user, or environment when assessing recurrence and generalization.

### 6. Persistent questions and stopping

An unresolved inquiry can persist and resume when relevant evidence arrives. Retain its question, examined evidence and revisions, concise provisional conclusions, alternatives, and what remains unknown. Preserve enough state to continue without treating each wake as a new independent inquiry; hidden reasoning traces are not required.

The LLM decides whether there is a useful next evidence request, a conclusion, or a need to wait. There is no fixed reasoning-round limit or spending cap. Infrastructure exposes when a request yields no new evidence and prevents duplicate concurrent jobs. Unchanged records cannot manufacture support. Waiting for future evidence does not imply that the hypothesis is false or that the inquiry is resolved.

**No useful progress means resumable waiting.** When retrieval returns unchanged evidence and the learner has no materially different useful request, park the inquiry rather than immediately repeating retrieval or restating its conclusions. Record what is awaited, the relevant sources/questions, the last examined evidence revisions, and any proposed follow-up. A parked inquiry is unfinished, not failed or disproven. It resumes when relevant new or revised evidence arrives, a follow-up produces information, or an explicit reconsideration supplies a new question or investigative direction. Unrelated traffic and periodic worker wakes do not alone justify rerunning that inquiry.

Infrastructure reports unchanged source identities/revisions and suppresses duplicate work; the LLM judges whether a different direction could help. Do not convert parking into a fixed retry count, spending cap, automatic belief withdrawal, or permanent abandonment. No-progress detection manages continuation rather than limiting learning.

### 7. Follow-up stays under cognition's authority

Reading existing records is within learning's role. If records cannot answer a question, preserve that uncertainty and propose a useful new observation, question, or practice opportunity through existing cognition mechanisms. Explain what uncertainty the proposal could resolve and which alternatives remain.

Cognition decides whether and when to pursue the proposal. Reuse existing trial and practice machinery where applicable. This package does not add new experiment execution machinery or resolve the separate interpersonal-experiment policy.

### 8. Later use and verification

Supply relevant retained understanding to the consumers that can use it: interpretation, cognition, mind, planner, and hands. Reuse existing delivery where it suffices; add the connections needed for broader products, with scope and uncertainty intact. A local model's unsupported input contract must be recorded as a delivery gap rather than counted as successful receipt.

Record the exact product revisions supplied, distinguish supply from reported influence, and inspect resulting interpretation or behavior separately. Later experience can support, challenge, narrow, or withdraw understanding. Neither a consumer saying it applied a lesson nor a good outcome alone proves that the lesson caused the outcome.

### 9. Resource behavior

The user explicitly rejected a learning spending cap or quota. Do not impose one or make it a prerequisite for ordinary review. There is also no fixed maximum number of reasoning rounds.

Keep live conversation and action responsive through background scheduling and sound concurrency. Context limits, paging, request timeouts, cancellation, worker leases, and failure recovery are mechanical necessities, not semantic limits on what can be learned. Preserve progress across such boundaries. Record actual latency, usage, pending work, and coverage for diagnosis without turning measurement into an unapproved learning cutoff.

## Implementation sequence

1. Trace current observation intake, assessment, memory product owners, retrieval, inquiry continuation, and consumer input contracts. Record which pieces are reused and which narrowly require extension. Check deployed model interfaces before claiming delivery support.
2. Extend the shared lifecycle for semantic conversation review and game LLM nominations. Preserve useful game triggers, ongoing review, ordinary-activity coverage, and retry-safe reconciliation of overlapping entries.
3. Connect iterative requests to existing evidence and memory owners, including retrieval of existing lessons. Add persistent continuation only where the current lifecycle lacks it; keep historical evidence access distinct from requests for new external action.
4. Extend assessment and memory contracts to represent broader understanding and LLM-judged uncertainty without forcing every result into a strategy. Reconcile dependencies and existing products under memory ownership.
5. Connect relevant products to interpretation and other consumers that currently lack suitable delivery. Preserve exact supplied revisions and support follow-up proposals through cognition.
6. Verify discovery, iterative investigation, later use, transfer, revision, and operational recovery. Update implementation documentation with observed limits and distinguish scripted contract tests from live-model behavioral evidence.

Deliver this sequence in reviewable increments, each with its relevant checks, rather than one large coding change. The later agreed [post-audit 4 plan](POST-AUDIT-04-broader-learning-products.md) completes remaining product semantics and validation on this infrastructure. It supersedes the earlier decision to absorb all broader-product work into this package. Preserve implemented capabilities and this package’s outstanding acceptance; the [remaining roadmap](README.md#remaining-roadmap-and-ownership) assigns the follow-on work without duplication.

Concrete schema names, tool payloads, pagination sizes, wake debounce intervals, and migration details are implementation choices to derive from current interfaces. They are not additional semantic policies and must not silently reintroduce caps, hardcoded lesson categories, or count-based truth rules.

## Completion evidence

Define evaluation cases before implementation. Examples are held-out tests of general capability, not branches in production logic.

The [behavioral acceptance specification](POST-AUDIT-03-behavioral-acceptance.md) translates the table below into concrete case families, evidence requirements, and a proposed pass rule. The pass rule is pending user agreement. Freeze the agreed rubric and reserve evaluation fixtures before coding; infrastructure tests alone cannot close this package.

| Capability | Required evidence |
| --- | --- |
| Autonomous discovery | Ordinary observation, success, and conversation can initiate useful learning without explicit teaching or a failed action. Also demonstrate a legitimate no-change result. |
| Semantic conversation intake | Context distinguishes potentially meaningful experience from superficial correction/complaint labels; a scheduling wake alone does not force an assessment or write. |
| Meaningful stretches | An inquiry relates evidence across multiple turns or steps, can begin before activity ends, and can resume across sessions. |
| Iterative evidence access | The learner requests relevant memory or observation, inspects it, and follows up when needed. Missing evidence is visible and never invented. |
| Existing understanding | Retrieval finds relevant prior lessons; new evidence revises or supports them without redundant products or double-counted sources. |
| Broader understanding | At least one useful change concerns an expectation, relationship, or explanation rather than only a behavioral instruction. Observation-only cases need no invented action. |
| Tentative use | An uncertain inference reaches a relevant consumer as a guess and affects consideration or interpretation without becoming an asserted fact. |
| Persistence and transfer | A later decision after the original context is absent uses retained understanding. Irrelevant surface changes preserve useful transfer; important contextual changes prompt reconsideration. |
| Revision | Contrary evidence leads to justified narrowing, competing explanations, revision, or withdrawal, and consumers stop receiving obsolete revisions as current. |
| Cognition authority | A request for new evidence becomes a proposal, not an autonomous utterance or action by learning. |
| Reliability | Retries, late evidence, concurrent revisions, deletion, and restart preserve ownership and continuity without duplicate writes or resurrecting forgotten evidence. |
| Responsiveness | Background inquiry does not make the current response wait; latency and coverage are measured without a spending cap. |

Use scripted tests for lifecycle and integrity, plus real-model evaluations for semantic discovery, evidence interpretation, and behavioral transfer. Do not present scripted model outputs as proof of general learning. Numerical evaluation thresholds are not settled by this design; report failures and limitations explicitly.

## Scope boundaries

Included: broader learning intake and inquiry, comparison across experiences, memory-mediated understanding and revision, necessary consumer connections, existing-mechanism follow-up proposals, and evidence of later use and transfer.

Excluded: rebuilding completed capture or consolidation, another parallel learner, new experiment machinery, interpersonal-experiment policy, affinity or mood learning, unrelated planner changes, new observation sources solely to expand coverage, model fine-tuning, training exports, and model promotion. Preserve existing structured experience for future training; a memory summary must not replace the source record, and an inference must not silently become a training label.

This plan requires useful generalization from the evidence available to her. It does not promise that every experience yields learning or that any inferred explanation is correct.
