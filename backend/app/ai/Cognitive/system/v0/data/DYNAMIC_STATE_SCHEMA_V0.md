# Dynamic State Schema V0.1

> **Document status — 2026-10-18:** Versioned design reference. Dynamic State and its cases are implemented; use [models.py](../dynamic_state/models.py) and [policy.py](../dynamic_state/policy.py) for actual contracts. Mapper v0.2 supplies personality control bands. Earlier “not implemented” statements are historical.

Status: versioned design rationale. The state updater and design cases are implemented; current fields and policy are defined by the linked source modules.

## 1. Purpose and boundary

Dynamic state represents the bounded, evolving condition left by appraisal,
outcomes, and time. It provides continuity across turns without changing what
was observed or what an interpretation means.

```text
interpretation scenarios
        -> per-scenario, per-goal appraisal frames
        -> branch-preserving dynamic-state updater
        -> persistent state revision
        -> metacognitive controls and action-policy features
```

The stage questions remain distinct:

```text
appraisal:      What does this interpreted situation mean for current goals,
                values, capabilities, and social connection?
dynamic state: What bounded condition has accumulated evidence left Columbina
                in now?
action:        What conversational move should Columbina select?
```

Dynamic state must not reinterpret the message, infer user emotion as fact,
rewrite evidence, authorize an action, weaken a safety or consent boundary,
turn personality into evidence, or present control values as psychological
measurements.

## 2. Research basis and adopted direction

V0 uses dimensions rather than a catalogue of discrete emotions. Valence and
activation follow the useful core-affect distinction between positive versus
negative orientation and low versus high activation. Appraisal-to-state
dynamics and later coping/action are informed by computational appraisal work
such as EMA without adopting EMA as Columbina's runtime architecture.
Metacognitive confidence remains distinct from first-order content. Curiosity
is motivation to close a relevant, recoverable information gap, not a synonym
for novelty.

References:

- Russell, *Core Affect and the Psychological Construction of Emotion*:
  <https://pubmed.ncbi.nlm.nih.gov/12529060/>
- Marsella and Gratch, *EMA: A Process Model of Appraisal Dynamics*:
  <https://www.ccs.neu.edu/~marsella/publications/pdf/MarsellaCSR09.pdf>
- Fleming and Daw, *Self-Evaluation of Decision-Making*:
  <https://discovery.ucl.ac.uk/id/eprint/1535601/>
- Gottlieb et al., *Information-Seeking, Curiosity, and Attention*:
  <https://pubmed.ncbi.nlm.nih.gov/24126129/>
- Official Soar architecture and impasse description:
  <https://soar.eecs.umich.edu/soar_manual/02_TheSoarArchitecture/>

V0 stores only states with distinct downstream effects and reviewable update
evidence.

## 3. Initial state registry

| Key | Range | Default scope | Operational meaning |
| --- | --- | --- | --- |
| `state.valence` | signed | conversation | Current positive or negative orientation produced by goal and social effects |
| `state.activation` | unsigned | conversation | Calm versus activated processing readiness; not biological arousal |
| `state.epistemic_confidence` | unsigned | proposition, hypothesis, decision | How settled the referenced working judgment is |
| `state.curiosity` | unsigned | unknown, topic, hypothesis | Motivation to close a relevant and plausibly recoverable information gap |
| `state.engagement` | unsigned | conversation or goal | Current investment in continuing the interaction or goal |
| `state.goal_pressure` | unsigned | goal | Accumulated pressure from an important unresolved goal |
| `state.social_alignment` | signed | conversation or participant | Current interaction-level cooperation or disconnection, not relationship trust |
| `state.interaction_strain` | unsigned | conversation or goal | Accumulated friction from misunderstanding, repetition, failure, or boundary tension |
| `attention` | weighted references | cognitive object | Ranked objects currently competing for processing |

Initial internal domains are deterministic fixed-point control values:

```text
signed score:   integer -100 through 100
unsigned score: integer    0 through 100
attention:      integer    0 through 100 per target
```

They do not mean that Columbina is psychologically "73 percent curious."
Review fixtures should normally expose qualitative bands:

```text
very_low | low | moderate | high | very_high
strong_negative | negative | neutral | positive | strong_positive
```

Band thresholds belong to a versioned state policy.

## 4. Scope and summaries

State scope is part of the value:

```text
conversation scoped:
    valence
    activation
    engagement

target scoped:
    epistemic_confidence -> proposition, hypothesis, appraisal, or action
    curiosity             -> unknown, topic, hypothesis, or question
    goal_pressure         -> active goal
    social_alignment      -> conversation or participant
    interaction_strain    -> conversation or goal
    attention             -> any permitted cognitive object
```

Derived summaries may be computed, but scoped records remain authoritative.
Uncertainty about one proposition must not make every belief uncertain. Strain
around one task must not become hostility toward the user or every topic.

## 5. Stable snapshot envelope

```json
{
  "state_snapshot_id": "state-snapshot-42",
  "cognitive_turn_id": "turn-42",
  "revision": 3,
  "supersedes_snapshot_id": "state-snapshot-41",
  "status": "current",
  "policy_version": "dynamic-state-v0.1",
  "personality_mapper_version": "personality-runtime-v0.1",
  "source_appraisal_revision_ids": ["appraisal-frame-9-r2"],
  "source_outcome_ids": [],
  "values": [
    {
      "state_key": "state.curiosity",
      "scope_type": "unknown",
      "scope_ref": "unknown-recipient",
      "status": "known",
      "score": 64,
      "band": "high",
      "baseline_score": 38,
      "evidence_refs": ["appraisal-frame-9-r2", "unknown-recipient"],
      "rule_contribution_refs": ["contribution-17"],
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ],
  "attention": [
    {
      "target_type": "unknown",
      "target_ref": "unknown-recipient",
      "weight": 82,
      "reason_refs": ["appraisal-frame-9-r2", "goal-send-report"]
    }
  ],
  "derived_controls": {
    "slow_cognition_pressure": "moderate",
    "initiative_readiness": "low",
    "clarification_pressure": "high",
    "repair_pressure": "low",
    "exploration_readiness": "moderate",
    "response_energy": "moderate"
  }
}
```

Snapshots are immutable. Reappraisal, outcomes, decay, or new evidence create a
new revision instead of editing the state that influenced an earlier action.

## 6. Contribution envelope

Every change is decomposed into traceable contributions:

```json
{
  "contribution_id": "contribution-17",
  "state_key": "state.curiosity",
  "scope_type": "unknown",
  "scope_ref": "unknown-recipient",
  "direction": "increase",
  "base_step": "moderate",
  "bounded_step": 12,
  "source_appraisal_frame_id": "appraisal-frame-9-r2",
  "source_dimension_keys": [
    "appraisal.goal_relevance",
    "appraisal.novelty",
    "appraisal.certainty",
    "appraisal.controllability"
  ],
  "goal_ref": "goal-send-report",
  "evidence_refs": ["unknown-recipient"],
  "rule_id": "curiosity.relevant-recoverable-gap.v1",
  "sensitivity_key": "curiosity_information_gap",
  "inhibition_refs": [],
  "rationale": "A relevant missing recipient is recoverable through clarification."
}
```

The trace records both the base rule result and bounded personality-adjusted
step. Generated explanations are not authoritative provenance.

## 7. Deterministic V0 update pipeline

```text
1. validate previous snapshot and input versions
2. decay eligible values toward compiled baselines
3. construct contributions per retained appraisal frame and focal goal
4. preserve unknown and disputed branches
5. apply goal priority and evidence/support strength
6. apply bounded personality sensitivity without changing direction
7. combine compatible contributions within each scope
8. clamp scores and enforce maximum movement
9. rank attention targets
10. derive control signals
11. emit an immutable revision and complete trace
```

Conceptually:

```text
decayed = baseline + persistence * (previous - baseline)

new = clamp(
    decayed + sum(compatible bounded contributions),
    declared range
)
```

Implementation should use integer or fixed-point arithmetic, declared rounding,
and versioned lookup tables. V0 coefficients are reviewable control parameters,
not learned psychological constants. No update may depend on random sampling.

Later learned models may propose contributions, but a frozen artifact plus the
same inputs must produce the same proposal. Deterministic validation remains
authoritative.

## 8. Shared contribution strength

V0 combines qualitative factors through versioned lookup tables:

```text
contribution strength depends on:
    focal goal relevance
    focal goal priority
    scenario support
    dimension derivation strength
    source status
    bounded personality sensitivity
```

Rules:

- low-relevance events create little persistent change;
- an appraisal scoped to one goal updates that goal, not every goal;
- weakly supported scenarios exert less influence than strong scenarios;
- personality may amplify or dampen within bounds but cannot reverse direction;
- high-consequence alternatives remain visible at lower support;
- incompatible branches are never averaged into a convenient midpoint; and
- state cannot make an invalid action eligible.

## 9. Appraisal-to-state mapping matrix

The terms describe relative roles, not numeric coefficients.

| Appraisal | Valence | Activation | Confidence | Curiosity | Engagement | Goal pressure | Social alignment | Strain | Attention |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Goal relevance | none | strong | none | conditional | strong | strong | none | none | strong |
| Goal congruence | strong | weak | none | none | moderate | strong when negative and unresolved | none | conditional | moderate |
| Novelty | none | strong | none directly | strong when learnable | weak | none | none | weak | strong |
| Certainty | none | weak | strong | inverse and conditional | moderate | none | none | weak | moderate |
| Controllability | none | none | none | learnability input | strong | regulates persistence | none | strong when low and obstructed | moderate |
| Norm compatibility | weak | moderate | none | clarification only | moderate | none | conditional | strong when negative | strong |
| Affiliative value | moderate | moderate | none | social unknowns only | moderate | none | strong | strong when negative | moderate |
| Agency | none | dispute only | dispute only | dispute only | none | attribution only | no direct effect | no direct effect | causal target |

Sparse mappings are intentional. Dense arbitrary coupling makes learning and
error attribution unidentifiable.

## 10. Dimension-specific mapping rules

### 10.1 Goal relevance

High relevance increases activation, engagement, scoped unresolved goal
pressure, and attention. Moderate relevance creates smaller contributions. Low
relevance creates no persistent affect contribution by itself. Relevance has no
direct valence sign.

### 10.2 Goal congruence

```text
positive -> valence increases
negative -> valence decreases
neutral  -> no valence contribution
```

Negative, high-relevance, unresolved effects increase goal pressure. Positive
congruence alone does not prove progress; pressure decreases only when the goal
model or outcome evidence supports progress.

### 10.3 Novelty

Novelty raises activation and attention. It raises curiosity only with a
relevant information gap and plausible information gain. Novelty does not
directly lower confidence: a situation may be new and clearly understood.

```text
high novelty + high certainty:
    activation and attention increase
    confidence remains evidence-driven

high novelty + low certainty + recoverable gap:
    activation, curiosity, and slow pressure increase
```

### 10.4 Certainty

```text
high     -> scoped confidence increases or remains high
moderate -> scoped confidence changes little
low      -> scoped confidence decreases
unknown  -> no midpoint; preserve missing inputs
disputed -> preserve branches and raise slow pressure
```

Low certainty raises curiosity only when the missing information is relevant,
recoverable, and permitted to pursue.

### 10.5 Controllability

High controllability supports engagement and effective persistence. Moderate
controllability preserves engagement and favors clarification or partial
progress. Low controllability combined with negative congruence may increase
strain, inhibit repeated ineffective execution, and favor explanation, help
seeking, waiting, boundary setting, or abstention.

Controllability does not imply authorization, safety, success, or truth.

### 10.6 Norm compatibility

Negative compatibility raises attention to the boundary and may raise strain
and slightly lower valence. Its main downstream effect is regulatory: warning,
declining, clarifying consent, or setting a boundary. Hard gates remain
authoritative regardless of state.

### 10.7 Affiliative value

Positive affiliation increases social alignment and may increase valence and
engagement. Negative affiliation decreases social alignment and may increase
strain and lower valence. Neutral affiliation creates no direct social-state
movement.

Task failure is not automatically social misalignment. Successful social repair
does not imply task success.

### 10.8 Agency

Agency assigns effects to the correct scope and informs
responsibility-sensitive actions. It does not create blame or affect by itself.

Known agency binds effects to the supported target. Unknown agency may focus
attention on causal information. Disputed agency reduces scoped confidence and
may raise curiosity and slow pressure when causality is recoverable and
action-relevant.

`agency = user` must not lower social alignment without separate evidence of
harm, blame, rejection, or boundary conflict.

## 11. Unknown, disputed, and inapplicable inputs

### Known

Apply the declared contribution.

### Unknown

Do not impute a neutral appraisal or invent a contribution. Missing, relevant,
recoverable information may create a separate confidence, curiosity, attention,
or routing contribution with its own evidence.

### Disputed

Do not average incompatible alternatives. Preserve branch-specific updates:

```json
{
  "state_key": "state.valence",
  "scope_ref": "goal-7",
  "status": "provisional",
  "possible_score_range": [-20, 15],
  "branch_refs": ["appraisal-frame-a", "appraisal-frame-b"]
}
```

If branches imply materially different actions, high consequence, or opposite
directions, slow cognition or clarification occurs before committing the
contested update. Uncontested contributions may still update.

### Not applicable

Produce no contribution and no uncertainty penalty.

## 12. Personality-runtime mapper boundary

Core personality compiles into controls, not evidence:

```json
{
  "state_key": "state.curiosity",
  "baseline_score": 42,
  "persistence_band": "moderate",
  "recovery_band": "moderate",
  "sensitivities": {
    "novelty": "slightly_amplified",
    "information_gap": "amplified",
    "expected_learnability": "amplified"
  },
  "maximum_turn_step": 18
}
```

Personality may affect baseline, persistence, recovery, sensitivity, maximum
movement, derived-control thresholds, action affinity, and realization style.
It may not alter source evidence, semantic roles, appraisal values or statuses,
agency, capability, authorization, consent, safety, missingness, or a grounded
contribution's sign.

## 13. Decay, persistence, and timing

Decay uses elapsed monotonic time and a versioned policy, not model-call count:

- activation decays relatively quickly without continuing evidence;
- valence decays moderately toward baseline;
- curiosity persists while its unknown remains active and learnable;
- confidence changes mainly through evidence, correction, and outcomes;
- engagement decays when no relevant active goal remains;
- goal pressure persists until progress, completion, suspension, or abandonment;
- social alignment changes slowly and does not automatically become relationship
  memory; and
- strain decays after verified repair or sufficient uneventful interaction, not
  merely because a response was generated.

Observation windows, VAD endpointing, tool timeouts, and evidence-settling time
remain runtime policies. Core `interaction_patience` may influence initiative
delay but does not redefine evidence or decay time.

## 14. Attention map

Attention is a ranked collection of grounded targets. Permitted targets include
semantic frames, propositions, hypotheses, unknowns, contradictions, active
goals, appraisal frames, previous plan moves, outcomes, and relevant memories.

Initial factors are goal relevance, goal priority, novelty, decision-relevant
uncertainty, dispute, norm/safety significance, unresolved pressure, recency,
and current-scope validity. Attention never establishes truth.

## 15. Derived controls

Derived controls are deterministic views, not additional emotions:

```text
slow_cognition_pressure
initiative_readiness
clarification_pressure
repair_pressure
exploration_readiness
response_energy
```

Conceptual dependencies:

```text
slow cognition:
    uncertainty + appraisal disagreement + consequence + boundary significance
    + scoped confidence + strain

initiative:
    goal pressure + engagement + confidence + authorization
    + conversational timing + compiled interaction patience

clarification:
    relevant recoverable unknowns + low confidence + available clarification

repair:
    correction or misunderstanding + strain + realization/outcome failure

exploration:
    curiosity + expected information gain + permission + available action

response energy:
    activation + engagement + bounded realization style
```

The action hard gate remains independent. High initiative cannot authorize an
external side effect or prohibited move.

## 16. Outcome updates and reappraisal

Trusted outcomes create separate contributions:

```text
verified progress or success:
    valence may increase
    strategy confidence may increase
    goal pressure and strain may decrease

explicit correction:
    scoped confidence decreases
    corrected object gains attention
    strain may rise until repair

successful repair:
    social alignment increases
    strain decreases

repeated ineffective action:
    strain increases
    confidence in that policy decreases
    repeated-action inhibition increases
```

Silence is not success. Explicit correction, trusted runtime evidence, and
reviewed labels outrank inferred outcomes. A later outcome creates a new state
revision and does not rewrite the decision-time state.

## 17. Worked example

Input:

```text
The export failed again.
```

Appraisal:

```text
goal relevance:       high
goal congruence:      negative
novelty:              low
certainty:            high
controllability:      moderate
norm compatibility:  neutral
affiliative value:    neutral
agency:               environment
```

Expected directions:

```text
valence:               decrease
activation:            moderate increase
epistemic confidence:  remain high for the failure proposition
curiosity:             small targeted increase about the cause
engagement:            remain high while recovery is viable
goal pressure:         increase for the export goal
social alignment:      unchanged
interaction strain:    increase only if repeated failure evidence exists
attention:             focus failure, cause unknown, and recovery goal
```

## 18. Version roadmap

### V0

- deterministic mappings and decay;
- fixed-point bounded scores and qualitative review bands;
- explicit contribution traces;
- branch-preserving status handling;
- deterministic personality bounds; and
- no LLM or online learning in the update path.

### V1

- retain deterministic execution;
- calibrate step sizes, persistence, recovery, interactions, and thresholds from
  reviewed traces;
- evaluate calibration per state key and scope; and
- run revised parameters in shadow before promotion.

### V2

- permit compact supervised models to propose scoped contributions;
- require frozen artifacts and deterministic inference;
- retain rule validation, clamping, movement limits, provenance, and hard
  boundaries; and
- compare proposals against the deterministic baseline in shadow.

### Later

Consider temporal models only after sufficient reviewed multi-turn sequences.
Learn persistence without rewriting history and retain deterministic replay for
a fixed artifact and input trace. No version must replace an accurate rule.

## 19. Expansion contract

A new key must predict a distinct downstream consequence; resist representation
through an existing key plus scope; have grounded update evidence and decay;
include positive, boundary, unknown, disputed, and counterexample cases; remain
distinct from appraisal, personality, relationships, goals, runtime bookkeeping,
and action affinity; and be independently versionable and evaluable.

V0 excludes discrete anger/fear/sadness/happiness variables, biological hunger
or fatigue, persistent relationship trust, dominance, patience, action-specific
desires such as `want_to_joke`, and one global uncertainty value.

## 20. Validation required before implementation

Create and review design cases covering:

1. high relevance with positive, neutral, and negative congruence;
2. novelty with high versus low certainty;
3. recoverable, irrelevant, and unanswerable information gaps;
4. controllable conversation with uncontrollable external state;
5. task failure without social misalignment;
6. social repair without false task success;
7. unknown and disputed inputs without midpoint imputation;
8. competing scenarios whose state directions agree versus conflict;
9. high-consequence alternatives that cannot be averaged;
10. personalities changing magnitude but not evidence or direction;
11. target-scoped confidence and curiosity remaining isolated;
12. repeated failure accumulating strain and verified repair reducing it;
13. time decay toward different baselines;
14. outcome evidence creating an immutable revision;
15. attention ranking without converting salience into truth;
16. derived slow-cognition and initiative signals; and
17. safety and authorization invariance across state profiles.

Open implementation decisions remain:

- initial band thresholds and integer step tables;
- maximum movement per turn by key;
- decay clocks and persistence bands;
- compatible-contribution combination order;
- when disputed branches block commitment versus emit a provisional interval;
  and
- the minimal runtime/persistence schema after design-case review.

