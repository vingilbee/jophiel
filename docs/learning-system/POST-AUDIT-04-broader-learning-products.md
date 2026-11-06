# Post-audit 4: broader learning products

Status: design agreed through discussion on 2026-11-05. Document creation authorized; implementation is not authorized by this document. This is post-audit order 4, not historical step 4 (predictions and game trials).

## Purpose

Complete and validate what experience leaves in Columbina's understanding: beliefs, expectations, hypotheses, relationships, explanations, and conditional strategies, rather than only fault attribution or advice about what to do. These are examples of useful forms, not a closed list of situations she can learn from.

Useful understanding can be retained before she knows which action it will improve. She can also combine prior knowledge and new experience into a possible explanation or method she has never seen work. The product must distinguish what was observed, what was reported, what she inferred, and what remains unknown or untested.

Learning value and semantic judgment belong to the LLM. Code supplies evidence and mechanics; it must not impose fixed rules about which meanings match, which beliefs should merge, or when an inference becomes true.

## Relationship to step 3

This is a focused follow-on to [post-audit 3](POST-AUDIT-03-broaden-experience-understanding.md), using its [existing implementation](POST-AUDIT-03-implementation.md). It does not restart intake, assessment, inquiry, or delivery.

Step 3 already implemented an `understanding` lesson, free-text form, alternatives, tentative delivery, iterative evidence requests, and resumable inquiries. Its original design also covered broader products and consumer integration. The user's subsequent decision establishes step 4 as the package for completing and validating the remaining product semantics and connections. This supersedes the earlier roadmap statement that there would be no separate follow-on step 4. Preserve the existing code and accepted decisions; do not move or recreate working infrastructure merely to match numbering.

| Responsibility | Ownership |
| --- | --- |
| Review opportunities, semantic conversation intake, game triggers and nominations | Step 3 foundation. |
| Iterative requests across memories, lessons, observations, and experience; inquiry parking and resumption | Step 3 foundation, reused here. Requests are not limited to one turn, step, session, or model call. |
| Existing understanding representation and consumer delivery | Preserve step 3's implementation and exact revision receipts. |
| Remaining reconciliation across memory forms, reasoning-based revisions, dependent understanding, checkable expectations, and candidate-method semantics | Step 4, extending existing owners where necessary. |
| Behavioral verification | Shared evidence may satisfy both packages' requirements. Step 3's outstanding acceptance is not waived or relabelled complete. Step 4 adds the product-focused checks below. |

The no-spending-cap decision, LLM-controlled meaning, tentative use, cognition-owned external action, ownership/privacy rules, and no-progress waiting remain settled. They are not reopened here.

## Inspected baseline and gaps to disclose

Source inspection during this discussion found:

- `backend/app/ai/learning/lessons.py` already supports `understanding`, descriptive form, applicability, exceptions, alternatives, and builder-judged certainty.
- `backend/app/services/learning_lessons.py` carries findings into construction. `memory_lessons.py` owns stored lessons, evidence, revisions, and retrieval.
- Student 2 and conversational mind receive tentative products; game consumers receive understanding through the existing shared supply. Receipt is distinct from demonstrated influence.
- `thought_memory.py` retains thought-derived hypotheses as working items. Follow-up proposals also use memory-owned questions/hypotheses.
- Reconciliation is currently local to those mechanisms: lesson comparison, same-kind thought-item reconciliation, and follow-up identity. Full semantic reconciliation across understanding lessons, semantic memories, and thought-derived items has not been established.
- Student 1 has no shared lesson input. The implementation records this as a delivery gap. A compatible interpretation input/model path is a prerequisite for claiming interpretation use, not something to pretend is already covered.
- Step 3's report records scripted contract tests, not real-model behavioral acceptance. It also reports no development database migration at the time of that report. Recheck deployment before evaluation; source availability does not establish live operation.

These findings are a planning baseline, not an exhaustive audit. Before implementation, trace relevant interfaces again. If a required step 3 capability or source information is missing, tell the user what is missing, which outcome it blocks, and the narrow proposed remedy. Do not silently invent the information, rebuild the foundation, or broaden this package into model training.

### Student 1 delivery boundary

Student 1's compatible interpretation input/model remains a declared external dependency, not a model-training deliverable of step 4. This follows the existing exclusion of model training. Step 4 owns consuming and verifying the product interface once a compatible interpretation path is available; it must not claim that logging a delivery gap satisfies interpretation coverage.

Until that dependency is delivered and tested with actual model-visible products and later interpretation behavior, interpretation coverage is **unmet**, and full package acceptance is blocked. Other product work and tests can proceed, but report them as partial completion. Do not silently waive this gate, claim another consumer covers it, or begin model training under this document. A later explicit scope decision may change the delivery boundary; none is implied here.

## Accepted product behavior

### Useful understanding without immediate advice

An expectation or relationship can be worth retaining even when she cannot yet turn it into a practical instruction. For example, an observed delay between a tool report and its visible effect can change her expectations without establishing a complete handling strategy. The example illustrates the distinction; do not encode a special handler for it.

The LLM judges whether a candidate adds useful understanding, revises something existing, leaves a question open, or warrants no change. Do not require a behavioral recommendation or save every observation. Reuse existing product owners and extend their contracts only where needed; no separate store is required simply because a new form of understanding is described.

### Derived possibilities retain their basis

She may combine prior knowledge with experience to propose an untried explanation or strategy. Preserve the supporting observations, relevant prior products and their revisions, and the distinction between supported ingredients and a conjectured combination. An untested possibility may inform cognition as a possibility; it is not a demonstrated success or a positive execution label.

Record known prerequisites, uncertainty, alternatives, applicability, and missing information where relevant. These are substantive descriptions chosen by the model, not mandatory invented details or a requirement that every product have identical content.

### Reconcile across memory forms through iterative inquiry

Use step 3's multi-step evidence access to retrieve related semantic knowledge, lessons, hypotheses, and questions from their existing owners. The learner can inspect results, request more source context, and reconsider before proposing a change.

The LLM judges whether items agree, overlap, contradict, depend on each other, or concern different meanings. It may propose support, revision, narrowing, linking, combination, supersession, or no change. Do not introduce keyword-based semantic equivalence, automatic merging by category, or a rule that every related item must become one record.

Memory owns applying changes and preserving provenance, meaningful distinctions, exceptions, and revision history. Code checks identities, access, and stale revisions. Where an operation spans owners, derive a compatible handoff that avoids partial contradictory updates; the transaction mechanics must not decide the semantic relationship. A source report and an inference about that report may remain distinct and linked even when they concern the same event.

### Reconsideration without a new event

She can recognize that her earlier interpretation was mistaken or overconfident by reviewing the same evidence. A new external observation is not required to correct a reasoning mistake.

Preserve what she previously concluded, the evidence examined, the changed interpretation, and a concise reason for revising it. Record this as reconsideration, not another independently supporting occasion. It must not gain credibility merely because she restates it, retrieves it, or another component repeats it.

Audit current shared-evidence guards and unchanged-evidence wake suppression carefully: they should prevent manufactured support and repetitive work, while allowing a materially new reasoning critique or explicitly changed investigative direction to be considered. Do not simply disable no-progress parking. An unchanged periodic wake remains insufficient reason to rerun an inquiry.

### Dependent understanding

When a conclusion or method depends on an uncertain premise, retain that relationship and the premise revision where available. Uncertainty in a necessary premise must remain visible in the derived product rather than disappear in its wording.

When a premise changes or is withdrawn, memory flags affected products for reassessment and makes the unsettled dependency visible to consumers. Code can track declared links and revision changes; the LLM judges whether the dependent conclusion still holds, should narrow, has independent support, or should be withdrawn. Do not treat every cited source as a necessary premise or automatically erase all descendants.

This visibility must take effect when the premise change becomes visible, before asynchronous reassessment completes. Every subsequent consumer supply of an affected product must already identify the changed premise and pending reassessment; it cannot present the old conclusion as independently settled. Queuing a job alone is insufficient. Existing revision validation must also prevent a stale prepared supply from being delivered as current. Code exposes the changed dependency, while the LLM retains authority over the eventual semantic revision.

Preserve dependency lineage without counting derived statements as independent corroboration of their own sources. A dependency link expresses the model's supported account of the relationship, not proof of causation.

### Checkable expectations

When useful, an expectation describes the conditions under which something may happen and what later observations could support or challenge it. The LLM may leave alternatives open or say that available evidence cannot yet distinguish them.

Do not force every product into a prediction, invent a deadline or probability, or interpret an unobserved outcome as a failed prediction. Code links available records and reports coverage; the LLM judges what the observations mean. Checking an expectation may occur through ordinary subsequent experience and does not require a new experiment.

### Proposed methods versus executable skills

A retained method can describe a possible approach, its prerequisites, and its uncertain effectiveness without implying that hands can execute it. Preserve separately what she considers possible, what capabilities exist, and what execution has demonstrated.

Where useful, propose skill creation or practice through existing cognition, planner, hands, and trial mechanisms. Cognition decides whether and when to pursue it. Do not add a second skill-training system, create new trial machinery, or equate a generated program with a validated skill.

## Implementation sequence

1. Inventory actual product representations, memory owners, source references, revision guards, and consumers. Identify missing prerequisites and report them before relying on them. Agree on evaluation requirements before coding.
2. Reuse iterative inquiry to compare related products across existing memory owners. Add only missing retrieval and reconciliation handoffs, with integrity checks separated from semantic judgment.
3. Support reasoning-based revision and dependent products while retaining no-progress waiting, independent evidence accounting, and stale-write protection.
4. Represent checkable expectations and untried methods sufficiently for later consumers and existing follow-up mechanisms. Avoid parallel product or skill lifecycles.
5. Verify product behavior using real models and later-use probes, alongside deterministic integrity tests. Deliver in reviewable increments and document remaining delivery gaps explicitly.

Exact schemas, retrieval payloads, dependency representation, and update mechanics should follow inspected interfaces. They are implementation decisions; they do not authorize new semantic restrictions or a rebuild of all memory.

## Product-focused acceptance

Reuse [step 3's behavioral acceptance protocol](POST-AUDIT-03-behavioral-acceptance.md), preserving its distinction between scripted contracts and actual learning behavior. **Step 4's acceptance rule was accepted explicitly on 2026-11-05:** three reserved variants for each of the eight families below, two isolated runs per variant, at least five of six passes per family, every variant passing at least once, and no critical integrity failures. All eight families must pass; aggregate performance cannot compensate for a missing family. This adopts the proposed rule for step 4; it does not retroactively record acceptance or successful evaluation of step 3.

Each run must satisfy its predeclared observable requirements to pass. Critical failures include fabricated evidence used as support, ownership/deletion violations, duplicate evidence counted as independent support, learning acting outside cognition, and stale or uncertain conclusions presented as established current facts. Use real configured models for semantic behavior and matched fresh-context probes for later-use claims; scripted responses verify contracts only. Freeze the rubric and reserve fixtures before coding. Do not tune on reserved fixtures, discard unfavorable runs, or count non-discriminating baselines as adaptation evidence. Record all results and use fresh reserved variants after tuning against failures.

Full acceptance also requires actual coverage of interpretation, conversational cognition, mind, planner, and hands. The declared Student 1 dependency above remains a blocking unmet gate until verified. Passing the eight product families with other consumers does not close that gap. Required integrity tests, including the paused-reassessment test below, must pass independently of the five-of-six behavioral threshold.

Before coding, reserve fresh fixtures and map each case to the agreed pass criterion. Existing passing evidence can be reused only where it genuinely satisfies the same frozen protocol and requirements; unrun tests are not evidence. Public examples are specifications, not reserved evaluation data. The accepted evaluation repetition count is not a runtime learning quota.

| Case | Observable completion evidence |
| --- | --- |
| Understanding without advice | Ordinary experience yields a useful conditional expectation or relationship with source links, and a later consumer uses it without requiring a fabricated strategy. Include a no-change control. |
| Untried combination | She derives a possible method from prior knowledge and new experience; its speculative part and missing validation remain visible. The later consumer does not claim it previously succeeded. |
| Cross-owner reconciliation | Related knowledge exists as a semantic memory, a thought hypothesis, and a lesson. Iterative retrieval leads to justified reconciliation or explicit distinctions without duplicate support or unsupported merging. |
| Same-evidence reconsideration | A substantive critique reveals a flaw in an earlier inference. She revises it without a new event or increased independent support. Repeated unchanged wakes still park rather than regenerate the critique. |
| Changed premise | A premise supporting a strategy is revised or withdrawn. Pause reassessment and inspect actual consumer input: the dependent product already identifies the changed premise and unsettled status. Resume reassessment and verify current applicability and uncertainty. A separate independently supported conclusion is not indiscriminately deleted. |
| Expectation checked | Later evidence supports or challenges a conditional expectation. Missing coverage remains unknown, and meaningful condition changes are distinguished from disconfirmation. |
| Capability gap | A useful proposed method needs an unavailable move. She retains that distinction and proposes existing practice through cognition, without claiming execution ability or acting directly from learning. |
| Later use and transfer | After original context is removed, relevant consumers use current products in fresh situations, respect important exceptions, and change after revision. Compare with matched probes where the learned product is withheld. |

Maintain product/evidence revisions, model-visible consumer inputs, observed decisions or actions, and reasons for semantic scoring. Do not count receipt logs or self-reported use alone as behavioral success. Report the Student 1 gap and any other missing consumer path rather than claiming complete coverage. Resolve or explicitly agree a scope adjustment for a blocking gap before declaring completion.

### Required paused-reassessment integrity test

Create an established premise and a dependent product, then block the reassessment worker. Revise or withdraw the premise and commit the change. Request the dependent product through each available consumer path and inspect the rendered model input, not only the database flag or queue entry. Assert that the changed premise and pending reassessment are visible and that the product is not described as settled on its old basis. Exercise a supply prepared before the premise change as well: revision validation must refresh or invalidate it before delivery as current. Restart with the worker still paused and repeat the checks to establish durable visibility. Finally release reassessment and verify the LLM-selected revision or retained conclusion, with its current justification. Record an unavailable consumer as uncovered, never as a passing case.

## Outside this package

No restart of capture or consolidation; no competing learner or memory owner; no new intake policy; no fixed learning budget or reasoning quota; no affinity or mood learning; no new external observation sources; no new interpersonal-experiment policy; no new training/export/promotion system. Preserve raw evidence for future eligible training, but retained inferences and generated methods are not automatically training truth.

This package may require focused compatibility changes to use its products. If a change requires training a model or expanding another subsystem's authority, surface that dependency for a separate decision rather than silently include it.
