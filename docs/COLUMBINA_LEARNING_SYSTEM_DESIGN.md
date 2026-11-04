# Columbina: learning-system design and implementation roadmap

Status: **design only, revised 2026-11-01. No implementation is authorized by this document.**
The user accepted the architectural direction below: three learning loops, evidence before learning products,
multidimensional reward, attribution, and explicit prediction. Capture and ownership decisions were accepted on 2026-11-01 in
[Capture and ownership](learning-system/01-capture-and-ownership.md), which is authoritative for that scope.
Evidence and attribution decisions were accepted on 2026-11-01 in
[Evidence and attribution](learning-system/02-evidence-and-attribution.md), which is authoritative for that scope.
Other concrete schemas, algorithms, thresholds, and operational policies remain proposals.

Parent: [Memory and learning design](COLUMBINA_MEMORY_AND_LEARNING_DESIGN.md).

**Execution planning update, 2026-10-31:** This document remains the architectural reference.
Use the [step-by-step plan index](learning-system/README.md) for the smaller planning units,
their dependencies, and subsequent user decisions. Affinity and mood are excluded from V1.
The split plans do not authorize implementation. Step 1 now records accepted capture decisions and was built on request on 2026-11-01 ([implementation](learning-system/01-implementation.md)); Step 2 was built on request on 2026-11-01 ([implementation](learning-system/02-implementation.md)); Step 3 (immediate adaptation) was built on request the same day ([implementation](learning-system/03-implementation.md)); other listed open decisions remain unsettled.
This revision replaces the earlier two-speed roadmap in this file. Existing game learning and memory capabilities
are foundations to reuse; the general learning system described here is not implemented.

## 1. Goal and responsibilities

Columbina should adapt through conversation, observation, teaching, reading, thought, and action experience.
She should learn what is true, what tends to cause what, which approaches fit her goals and values, and how to
perform useful skills. These are different learning problems, not one universal reward or dataset conversion.
Human learning provides architectural inspiration, not a claim that these modules reproduce a human mind.

- **Observation** records what sources report, with provenance and timing.
- **Memory** maintains attributed knowledge, beliefs, experiences, lessons, and their revisions.
- **Cognition** interprets the current situation and owns the choice of what to do now.
- **Learning** evaluates experience and proposes changes to beliefs, lessons, skills, and trained behavior.
- **Bodies and delivery** execute decisions and report what actually happened.

Learning can affect the next decision through accepted memory and lessons. It does not independently speak,
execute actions, or compete with cognition for control. Memory owns memory writes; each learner owns the interface
through which lessons, models, or procedures affect its behavior.

## 2. The three learning loops

| Loop | What changes | Example | Completion evidence |
| --- | --- | --- | --- |
| Immediate adaptation | Current interpretation, scoped beliefs, retrieved lessons | “He wanted discussion before execution.” | The next relevant decision uses the correction without retraining |
| Deliberate learning | Hypotheses, predictions, strategies, practiced skills | “A clarifying question may distinguish these two intentions.” | A bounded trial produces evidence and revises the hypothesis or strategy |
| Consolidation | Student weights and validated reusable procedures | Recognizing related situations without retrieving the original lesson | Held-out behavior improves while prior capabilities are retained |

These are connected loops, not three isolated stores or a mandatory sequence for every event. Explicit teaching
can change a scoped belief immediately. A well-supported correction can become a training candidate without a
new experiment. An experiment may remain inconclusive and produce no lesson.

Immediate adaptation must not wait for background training. Consolidation makes useful adaptations more general
and automatic; it is not the only form of learning.

## 3. What exists and what needs connecting

Implementation observations from the 2026-10-30 inspection, with capture findings refreshed 2026-11-01; recheck paths and contracts before coding.

| Existing piece | Relevant implementation | Proposed use or gap |
| --- | --- | --- |
| Immutable observations and consumer receipts | `backend/app/services/observation.py`, `backend/app/ai/observe/registry.py` | Add a learning consumer; reuse identity, recovery, and evidence links |
| Game experience, reflection, and lessons | `backend/app/services/game_learning.py` | Generalize useful contracts; preserve game-specific outcome measurement |
| Game practice and learned skills | `backend/app/services/game_practice.py`, `backend/app/ai/desktop/practice.py` | Reuse bounded attempts, counted results, critique, and skill evaluation |
| Student 1 interpretation | `backend/app/ai/Cognitive/simple/live.py` | Persist inputs and predictions; attach interpretation-specific corrections |
| Student 2 live inference and effects | `backend/app/services/student_two.py` | Teacher path already logs detailed exact inputs, outputs, versions, and settings in background JSONL; local-path capture, reliable persistence, and execution linkage remain gaps |
| Student 2 snapshot | `backend/app/ai/state/student_two.py` | Memory input and memory-v1 rendering now exist; a compatible versioned scoped-lesson interface still needs design |
| Student 2 generation | `student_two._ask` | Temperature is 0; greedy token decoding is not an estimate of expected action reward |
| Student training/export/evaluation | `backend/app/ai/Cognitive/simple/student/events_*` | Reuse where compatible; preserve source and experience lineage for real examples |
| Hard thinking | `backend/app/ai/mind/hard.py` | Extend guesses into testable hypotheses, alternatives, and predictions |
| Working memory and thought memory | `backend/app/services/memory_manager.py`, `backend/app/services/thought_memory.py` | Reuse hypotheses and evidence; do not create a second owner for them |
| Learning page and game reports | Existing game-learning UI and reporting | Extend to evidence, attribution, predictions, lessons, and model evaluation |

Inspect legacy `learning_memories` and existing records before deciding whether to reuse or retire them.
Do not assume that a shared lifecycle means every domain uses the same confidence threshold or success test.

## 4. Evidence first, then different learning products

The shared flow is:

```text
Sources --------------------> attributed observations
Learners -------------------> decision-time records and predictions
Execution ------------------> actual actions and delivery results
                                      |
                                      v
                         linked evidence and attribution
                                      |
                           contextual outcome assessment
                                      |
               +----------------------+----------------------+
               v                      v                      v
       immediate adaptation    deliberate learning      consolidation
       beliefs and lessons     hypotheses and trials    datasets and skills
               |                      |                      |
               +------ accepted outputs at learner interfaces+
                                      |
                                future decisions
```

Raw records remain unchanged while retained; retention and source deletion follow the accepted capture plan.
Interpretation and assessment are versioned derivatives. Source references establish
where a claim came from, not whether the claim is true or causally explains an outcome.

| Experience | What it initially supports | Potential product |
| --- | --- | --- |
| “I was joking.” | Correction of an earlier interpretation | Student 1 label and scoped memory |
| “You understood me, but I wanted you to listen.” | Feedback about the conversational move | Student 2 correction or preference pair |
| A book explains why people lie | Attributed claims about human behavior | Knowledge, hypotheses, synthetic practice cases |
| A game action fails | An outcome needing diagnosis | Skill, planning, or prediction example depending on cause |
| Her thought says “Maybe I interrupted.” | A hypothesis | Investigation, not a confirmed training label |
| Independent experiences support a pattern | Evidence with conditions and exceptions | Scoped lesson and possible consolidation data |

Learning about deception must distinguish understanding behavior from choosing to perform it. A book describing
lying does not establish that a particular user lied, or that lying is desirable. A fictional example stays fictional.
Books and teaching can supply worked examples; any derived labels retain their source and review status.

Every product preserves observed/reported/inferred/simulated origin, context, uncertainty, supporting evidence,
counterevidence, and derivation lineage. Rephrasing or reflecting on one experience does not create new evidence.

### Learner-specific products

- **Student 1:** interpretation labels and uncertainty/calibration examples.
- **Student 2:** valid decision targets or supported comparisons between choices available in the same situation.
- **Prediction:** state/action-to-outcome examples with observation windows and missing-result handling.
- **Hands/planner:** procedures, preconditions, measured effects, and recovery strategies.
- **Memory/thought:** attributed knowledge, scoped lessons, hypotheses, and questions.

A source does not need to produce data for every learner. Accepting uncertainty and producing no training row is
better than assigning a confident but unsupported target.

## 5. Proposed record contracts

These are conceptual record families, not approved table definitions. Reuse existing records and introduce
new seams only where responsibilities or implementations actually vary.

| Record | Essential information |
| --- | --- |
| Learner registration | What it predicts/chooses, input/output versions, validation, adaptation interface, evaluation |
| Evidence | Source identity, actor, context, occurred/received times, payload reference, provenance, origin |
| Decision attempt | Learner/model version, exact available input, output, upstream attempt links, selected choice, input schema |
| Selection | Alternatives actually considered, selection method, exploration reason, policy version; selection probabilities when available |
| Execution | Attempt link, actual action/delivery, executor version, start/end, result, deviations or failure |
| Prediction | Author/model, creation time, target action, expected observable outcome, uncertainty, observation window, check method |
| Outcome link | Evidence, affected attempt/execution/prediction, dimension, attribution confidence, alternative explanations |
| Assessment | Evidence links, dimension values or unknowns, uncertainty, evaluator version, value-profile version |
| Hypothesis/experiment | Existing hypothesis reference, alternatives, test, preconditions, bounds, stop conditions, results |
| Lesson/knowledge | Scope, applicability, exceptions, evidence, revision, status, consumer targets |
| Training record | Learner, decision-time input, target, label source, review state, real/synthetic origin, lineage, dataset uses |
| Model/procedure candidate | Version, training/evidence lineage, evaluations, promotion decision, rollback target |

Freeze decision-time inputs when supplied and capture outputs when produced, before later results can contaminate them.
Persist asynchronously without blocking responses or actions; bounded retries and visible detected gaps replace a
guaranteed durable pre-action boundary. Store intended actions separately from executed actions.
The minimum capture can precede candidate generation: missing alternatives or predictions remain explicitly absent.
Do not reconstruct them afterward and represent them as contemporaneous.

## 6. Attribution: which part should change?

The accepted [Step 2 decisions](learning-system/02-evidence-and-attribution.md) separate feedback-target linking from
causal attribution. Learning owns retrospective assessment; code checks concrete facts and an LLM proposes grounded
explanations. V1 uses evidence-backed qualitative uncertainty, not generated confidence percentages. Assessments run
automatically for explicit corrections and execution failures, with ordinary interactions sampled as well. There is no
fixed investigation budget: stop when supported, awaiting missing evidence, or stuck without a useful next step.
Assessments can support later adaptation and training but do not automatically create either product.

Track the causal chain available to the system:

```text
Student 1 interpretation -> Student 2 choice -> mouth wording -> delivery -> user response
                                              planner -> hands -> environment outcome
```

Links identify participation; they do not prove causation. A negative response can follow a correct interpretation
and appropriate move because wording or delivery was poor. A game failure can arise from execution, stale state,
or unavailable resources rather than a bad plan.

Attribution should:

- Attach delayed feedback to the relevant attempt or sequence, not automatically the previous message.
- Distinguish observed failure from a proposed explanation of failure.
- Preserve competing explanations and allow `unclear` or `insufficient_evidence`.
- Separate choice quality given available information from the eventual outcome: luck is possible in both directions.
- Produce learner-specific corrections only where evidence supports them.
- Revisit assessments when new evidence arrives without overwriting the original observation.

The user is authoritative about their intended meaning and expressed preferences. Their external factual claims
remain attributed claims. Code-counted results support the measurement they actually make; they are not universal
proof of success. Teacher judgments remain judgments, and self-reflection remains a hypothesis until supported.

## 7. Explicit prediction and a model of consequences

Record expectations before acting so surprise can be checked rather than invented in hindsight.

Examples:

- “This question should distinguish whether he wants advice or company.”
- “This tool should break the block and increase the relevant item count within this attempt.”

A prediction includes the proposed action, expected observation, relevant conditions, uncertainty, time horizon,
and a check method. A separate predictor can supply it initially; adding fields to Student 2 is not a prerequisite.
Distinguish the predictor's expectation from what Student 2 itself represented.

Compare predictions with observations only when the observation window and coverage permit it. No observation is
not automatically failure. Predictions may resolve as supported, contradicted, partly supported, or unknown.
Track prediction calibration and error over time, separately from reward.

Begin with structured predictions and retrieved experience. A learned world model or imagined rollouts are later
options, not an initial requirement. Simulation can propose actions and synthetic practice; simulated outcomes
must not count as real confirmation. Check transfer against actual outcomes.

## 8. Multidimensional reward and personality

Keep three separate layers:

1. **Observation:** what was reported or measured.
2. **Interpretation:** what it might mean, with uncertainty.
3. **Appraisal:** how the consequences fit goals, preferences, and values.

For example, laughter is an observation, enjoyment is a possible interpretation, and valuing shared playfulness
is an appraisal. None alone proves that interrupting or repeating that joke is a good general policy.

| Accepted V1 dimension | Question |
| --- | --- |
| Goal progress | Did this advance the current intention? |
| Understanding | Did the interpretation fit the evidence? |
| Relationship | Did it respect expressed preferences and commitments? |
| Knowledge gain | Did it resolve a useful uncertainty? |
| Cost | What time, resources, interruption, damage, or recovery did it require? |
| Character fit | Was it consistent with her values and chosen manner? |

These six dimensions are accepted V1 engineering choices, not a literal biological reward function. Store each with
its evidence and uncertainty; missing dimensions are unknown, not zero. Domain adapters define measurements.
A learner may combine relevant dimensions into a scalar for an algorithm, but must preserve the original vector,
normalization, weights, and evaluator version. No universal numeric weights are settled here.

Personality affects valuation; it must not rewrite what happened. The accepted policy keeps core values fixed,
with adaptable preferences and strategies and revisable beliefs. V1 assessments need no numeric scores or overall
good/bad score. Affinity and mood are excluded from V1; their later role remains open.

Conversation has partial, ambiguous, often delayed reward signals. Silence is generally missing information,
not punishment. Immediate user approval alone can reinforce agreement or flattery; self-rated satisfaction alone
can reinforce rationalization. Use independent evidence, delayed consequences, and held-out behavioral evaluation.
A second model is a critic, not an independent source of ground truth merely because it is a different call.

## 9. Deliberate learning and purposeful exploration

Increasing temperature alone changes sampling, not the learning loop. Greedy decoding does not imply that the
student has estimated the expected utility of all actions. Explore at the level of valid semantic choices.

| Stage | Owner | Required output |
| --- | --- | --- |
| Notice uncertainty or repeated failure | Cognition/thought with learning triage | A question worth resolving |
| Propose alternatives and a test | Learning with thought/prediction | Valid candidates, predictions, costs, limits, observation window |
| Choose whether and when to try | Cognition | Selected action, selection method, exploration reason |
| Execute | Body/mouth | Actual execution and deviations |
| Observe | Source adapters | Measured or attributed outcomes |
| Assess and revise | Learning through memory/learner interfaces | Revised hypothesis, lesson, skill, or no conclusion |

Prefer informative trials with manageable costs. Curiosity should target useful uncertainty reduction, not endless
novelty. Repeated reflection or unpredictable noise must not count as repeated progress. Exploration budgets,
risk categories, candidate generation, and selection algorithms remain to be designed.

Games can support bounded routes, tools, and skill variants. Conversation can support appropriate alternatives,
clarifying questions, and expression of her own views. The allowed scope of deliberate interpersonal experiments
is still open; this plan does not enable unrestricted experimentation on users or external systems.

Record actual selection probabilities if the policy exposes them; never treat token confidence as action utility
or invent propensities for later off-policy evaluation. Untried alternatives have predicted outcomes, not observed
ones. Early comparisons require explicit supported preference labels rather than fabricated counterfactual success.

## 10. Immediate adaptation and consolidation interfaces

### Immediate adaptation

Apply clear corrections to current interpretation and scoped memory through existing owners. General lessons
include applicability conditions, exceptions, and supporting/contradicting evidence. Retrieve them where decisions
are made, and log which lesson revisions a learner saw.

Move Student 2's lesson input earlier than the previous roadmap. Design the input contract alongside the first
adaptation loop, with matching training examples and compatibility checks. Existing memory input does not establish compatibility with a new lesson contract. Do not append unfamiliar fields to an
existing model and assume it uses them correctly. Until compatible support exists, report which consumers actually
use lessons; a mouth-only change does not prove Student 2 adapted.

### Consolidation

Start with supported supervised corrections. Preference optimization such as DPO is an option where comparisons
are justified; online reinforcement learning is not required for the first milestone.

1. Select evidence-bearing candidates and a sample of ordinary successes, not only failures.
2. Attribute the issue and derive learner-specific targets or supported preferences.
3. Review ambiguous or consequential labels; retain rejected/uncertain records for inspection, not positive training.
4. Export versioned datasets with real/synthetic separation, experience lineage, and baseline rehearsal.
5. Train a candidate using the appropriate existing pipeline or a separately designed training job.
6. Evaluate adaptation, generalization, calibration, retention, and character behavior against the current baseline.
7. Promote only under the policy to be agreed; retain a rollback target and dataset/model lineage.

Dataset invariants:

- Hindsight can inform labeling, but student inputs contain only decision-time information.
- Split by related experience and synthetic derivation lineage, not individual rows; protect held-out evaluations.
- Old input versions require a faithful compatible conversion from retained decision-time state, or exclusion.
- Synthetic variants and repeated reflections do not count as independent real evidence.
- Rehearsal reduces forgetting risk but does not prove retention; evaluate it directly.
- Source deletion removes affected retrieval products and raw capture copies, but retains already-created,
  self-contained training examples. Opaque source identifiers support correction tracking without requiring the
  original source. Explicit corrections make affected examples ineligible until reviewed; explicit training-data
  deletion removes them from future training uses. Retained examples remain private to the originating user.
- Removing examples does not erase their influence from an existing model or a descendant fine-tuned from it.
  Model-level removal requires a separate strategy, such as rebuilding from an unaffected checkpoint, and
  verification; no erasure guarantee is implied by the next training run.

## 11. Changes across modules

| Module | Proposed change | Scope note |
| --- | --- | --- |
| Observation | Source reliability, coverage, timing, reading/screen sources, action/outcome links | Preserve raw reports separately from semantic assessment |
| Student 1 | Input/prediction capture and interpretation-specific corrections | Include uncertainty evaluation; do not route every negative outcome here |
| Student 2 | Durable attempt records, early lesson contract, valid alternatives and exploration | Schema/model compatibility must be evaluated |
| Thought | Testable hypotheses, alternatives, predictions, revision criteria | Better structure before broadly increasing reasoning cost |
| Memory | General scoped lessons, exceptions, counterevidence, revision and invalidation | Reuse existing memory ownership |
| Hands/planner | Preconditions, intended/executed distinction, measured effects, recovery costs | Extend existing practice and skill evaluation |
| Learning | Attribution, assessment, experiment bookkeeping, dataset construction, consolidation | Background owner of learning work, not a second action controller |
| Runtime | Leased/retryable jobs, budgets, stale-result checks, recovery | Immediate corrections must not wait for slow reflection |
| UI/evaluation | Evidence-to-change inspection, prediction accuracy, lesson use, model/procedure comparison | Make uncertain and rejected conclusions visible |

The retrospective reflector and live thought have different jobs. Thought reasons about the present and proposes
hypotheses; reflection evaluates an evidence window and may revise several products. They may share utilities,
but must not turn a live guess into its own confirming evidence.

## 12. Proposed implementation sequence

All stages remain unimplemented proposals in this roadmap. The first release should demonstrate two complete
loops, not only collect records or generate labels.

| Stage | Deliverable | Exit criterion |
| --- | --- | --- |
| L0: contracts and capture | Map existing owners; capture Student 1/2 inputs, versions, outputs, execution/delivery links | Reconstruct saved interpretation-to-outcome chains without future leakage; expose detected capture gaps while actions continue |
| L1: evidence and attribution | Learning consumer, delayed outcome links, uncertain attribution, review view, dimension assessments | Feedback reaches the right attempt; ambiguous causes remain unresolved |
| L2: immediate adaptation | Scoped lesson lifecycle and retrieval; compatible Student 2 lesson input; revisions/invalidation | A correction changes the next relevant decision without retraining and does not spread to unrelated contexts |
| L3: predictions and deliberate trials | Pre-action predictions, testable hypotheses, bounded alternatives; reuse game practice | A trial measures expectations versus results and updates a hypothesis or skill |
| L4: consolidation | Learner-specific exports, review, rehearsal, held-out evaluations, promotion/rollback design | A candidate improves on new situations while retaining prior abilities |
| L5: broader sources and autonomy | Reading/screen adapters, attributed knowledge, synthetic practice, calibrated exploration expansion | New sources use the same evidence lifecycle and transfer is tested in real use |

L0/L1 should define prediction and selection record extensions even if initial capture leaves them absent.
L2's model-format work is coordinated with the current Student 2 training effort; it does not silently change an
in-flight dataset. Reading contracts can be designed early while full source integration remains later.

### First complete loop: conversational correction

- Capture the interpretation, decision, wording, and delivery.
- Link a correction to the right stage, keeping uncertain attribution explicit.
- Update a scoped belief or lesson; show which consumer used it next time.
- Produce a consolidation candidate with only decision-time inputs.
- Evaluate a related but unseen situation, plus an exception where the lesson should not apply.

### First complete loop: game experiment

- Capture current conditions, hypothesis, alternative action, and pre-action prediction.
- Choose a bounded trial through cognition and execute through the existing body.
- Measure actual effects, including deviations, costs, and observation coverage.
- Revise the hypothesis or skill using that evidence.
- Evaluate from a changed starting condition; an inconclusive trial must remain inconclusive.

## 13. Acceptance and evaluation

- Immediate learning: the next relevant choice improves without changing weights.
- Attribution: a delivery failure does not automatically become a Student 1 or Student 2 error label.
- Delayed feedback: evidence links to the appropriate earlier action or sequence.
- Prediction: expectations predate actions; missing observation is distinguishable from contradiction.
- Evidence integrity: repeated reflection, synthetic variants, and citations do not manufacture independent support.
- Scope: lessons apply in relevant contexts and preserve exceptions; contradictory evidence can revise them.
- Reading: an attributed passage does not become an unqualified fact about the user or an endorsed action.
- Exploration: valid alternatives have recorded selection reasons, limits, actual outcomes, and costs.
- Reward: raw evidence survives evaluator changes; unknown dimensions remain unknown.
- Generalization: improvement transfers to held-out situations and changed game conditions.
- Retention: earlier capabilities and character behavior are measured alongside new learning.
- Reversibility: mistaken lessons can be withdrawn; stale workers cannot restore them; model rollback is available.
- Data integrity: source deletion removes raw copies while retained training examples remain independent; explicit
  corrections invalidate affected examples, and explicit training-data deletion excludes them from future training.
  User access boundaries and existing-model removal limitations remain explicit.
- Runtime: retries do not duplicate evidence or trials; disabled new learning stops new updates while existing
  accepted lessons/models remain distinguishable from a separate baseline mode that disables their use.

More generated lessons, higher teacher agreement, or greater immediate approval alone do not demonstrate learning.
Numeric thresholds and evaluation suites are part of the next design, not invented completion criteria here.

## 14. Next design discussion

Start by walking the two complete loops through concrete inputs, records, owners, and failure cases. Then decide:

1. **Record and ownership design:** accepted boundaries are in [Step 1](learning-system/01-capture-and-ownership.md):
   component ownership, reuse plus dedicated attempt records, exact capture, asynchronous persistence, retention,
   and derivative handling. Concrete schemas and queue/retry settings remain implementation design work.
2. **Adaptation interface:** what Student 1/2, thought, mouth, and hands actually receive; Student 2 version migration;
   lesson scope, retrieval budgets, and conflict handling.
3. **Attribution and assessment:** accepted responsibilities, qualitative uncertainty, automatic assessment, six
   dimensions, revision rules, and stopping conditions are in [Step 2](learning-system/02-evidence-and-attribution.md).
   Concrete contracts, domain measurements, sampling, stalled-reasoning detection, and broader evaluation thresholds
   remain implementation planning work. Numeric normalization and weights are not required for V1 assessment.
4. **Prediction and exploration:** initial predictor, candidate generator, observation windows, test budgets,
   allowed conversation/game trials, and what counts as useful information gain.
5. **Personality:** which values are stable, which preferences can evolve, and whether/how mood influences learning.
6. **Consolidation:** per-user adapters versus shared models, training-data permission, review requirements,
   training environment, promotion authority, rollback, forgetting strategy, and compute budgets.
7. **Evaluation:** concrete held-out scenarios, retention tests, calibration measures, and stage exit thresholds.

The architectural direction is accepted. These implementation choices have not been decided by that acceptance.

## 15. Research grounding

These sources motivate options; they do not establish that the proposed architecture learns like a human.

- [Complementary learning systems](https://web.stanford.edu/~jlmcc/papers/McCMcNaughtonOReilly95.pdf): rapid episodic
  learning and slower integration motivate adaptation plus consolidation.
- [Exploration by Random Network Distillation](https://arxiv.org/abs/1810.12894): intrinsic exploration bonuses
  motivate curiosity mechanisms; useful information gain is a separate design objective to evaluate here.
- [Dreamer / world models](https://doi.org/10.1038/s41586-025-08744-2): learned models can support planning and imagined
  experience in control tasks; social simulations still require grounding and transfer checks.
- [Direct Preference Optimization](https://arxiv.org/abs/2305.18290): an option for learning supported preferences
  without a separately trained explicit reward model; it does not solve attribution or label quality.
