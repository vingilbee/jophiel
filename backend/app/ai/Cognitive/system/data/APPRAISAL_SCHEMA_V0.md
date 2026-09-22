# Appraisal Schema V0.1

Status: architectural direction accepted. The schema, deterministic rules, and
qualitative aggregation require design-case validation before runtime or table
implementation.

## 1. Purpose and boundary

Appraisal evaluates what an interpreted situation means for Columbina's current
goals, values, capabilities, and social connection. It does not re-detect cues,
decide what the user literally said, infer user emotion as fact, select an
action, or directly mutate dynamic state.

```text
perception
    -> interpretation hypotheses and semantic frames
    -> coherent interpretation scenarios
    -> per-scenario appraisal frames
    -> aggregate preserving disagreement
       +-> metacognitive routing
       +-> bounded dynamic-state update
       +-> action candidate construction and scoring
```

Appraisal is agent-relative but evidence-bound. The same external event can be
congruent with one active goal and incongruent with another. Core values and
goals may legitimately define what matters to Columbina, but personality cannot
rewrite the interpreted event, runtime capability, consent, or safety facts.

## 2. Research basis and adopted direction

The design borrows mechanisms rather than importing a complete cognitive
architecture.

The Soar/Scherer implementation organizes appraisal around relevance,
implication, coping potential, and normative significance. It derives appraisal
incrementally during normal cognitive processing and includes such dimensions as
novelty, goal relevance, causal attribution, outcome probability, discrepancy
from expectation, conduciveness, urgency, control, power, and standards
compatibility:

<https://public.websites.umich.edu/~rickl/pubs/marinier-laird-lewis-2008-cogsys.pdf>

EMA appraises the agent's current causal interpretation and explains fast and
slow changes through evolving perception and inference rather than requiring
two unrelated appraisal engines:

<https://www.ccs.neu.edu/~marsella/publications/pdf/MarsellaCSR09.pdf>

Soar's broader state/operator architecture is useful for keeping represented
state, operator selection, working memory, and long-term memory distinct:

<https://soar.eecs.umich.edu/soar_manual/02_TheSoarArchitecture/>

ACT-R remains useful inspiration for inspectable productions, declarative
chunks, bounded processing, and learned utility. Standard ACT-R does not provide
the appraisal contract used here; affective systems are normally extensions:

<https://act-r.psy.cmu.edu/>

Columbina therefore adopts:

- appraisal frames tied to the current interpretation;
- incremental computation when required information becomes available;
- a partial order based on data dependencies;
- continuous reappraisal through immutable revisions;
- appraisal as input to action/coping and dynamic state; and
- deterministic, traceable V0 rules.

Columbina does not adopt:

- Soar or ACT-R as its runtime engine;
- a fixed human emotion-label taxonomy as cognitive truth;
- one active interpretation that discards meaningful alternatives;
- an unvalidated emotion-intensity equation; or
- online reinforcement of appraisal rules from ordinary conversation.

## 3. Interpretation-scenario dependency

The interpretation envelope currently retains hypotheses in separate
dimensions. Appraisal requires compatible combinations of those hypotheses.
Otherwise the system could combine the literal meaning from one alternative
with the speaker goal or pragmatic force from a contradictory alternative.

The scenario builder adds no new meaning. It groups existing evidence-linked
hypotheses into a coherent candidate situation:

```json
{
  "scenario_id": "interpretation-scenario-uuid",
  "interpretation_trace_id": "interpretation-uuid",
  "support": "strong",
  "disposition": "leading",
  "semantic_frame_refs": ["semantic-frame-1"],
  "hypothesis_refs": {
    "communicative_acts": ["hyp-act-1"],
    "speaker_goals": ["hyp-goal-1"],
    "modalities": ["hyp-modality-1"],
    "pragmatic_meanings": ["hyp-pragmatic-1"]
  },
  "supporting_evidence_refs": ["cue-activation-1", "message-span-1"],
  "contradiction_refs": [],
  "unknown_refs": [],
  "compatibility_rule_ids": ["scenario.request-question.v1"]
}
```

Scenario support remains `unsupported`, `weak`, `moderate`, or `strong` in V0.
It is not a probability. The scenario builder must preserve at least every
materially different retained interpretation that could change an appraisal,
route, authorization question, or action. Runtime search budgets may bound
construction but must be recorded separately from semantics.

## 4. Stable appraisal envelope

An appraisal frame is scoped to one interpretation scenario and one focal goal.
If a scenario affects several goals differently, it produces separate frames.
This avoids hiding conflict through premature averaging.

```json
{
  "schema_version": 1,
  "appraisal_trace_id": "appraisal-trace-uuid",
  "cognitive_turn_id": "turn-uuid",
  "interpretation_trace_id": "interpretation-uuid",

  "frames": [
    {
      "frame_id": "appraisal-frame-uuid",
      "revision": 1,
      "supersedes_frame_id": null,
      "scenario_id": "interpretation-scenario-uuid",
      "scenario_support": "strong",

      "subject": {
        "event_refs": ["semantic-frame-1"],
        "focal_goal_ref": "goal-help-user",
        "goal_owner": "columbina",
        "goal_priority_band": "high",
        "value_refs": ["value-honesty"],
        "capability_snapshot_ref": "capabilities-uuid",
        "relationship_snapshot_ref": null
      },

      "dimensions": {},
      "missing_inputs": [],
      "conflicts": [],
      "completeness": "complete",
      "created_at": "2026-09-18T18:03:12.800Z"
    }
  ],

  "aggregate": {
    "dominant_frame_ids": ["appraisal-frame-uuid"],
    "dimension_ranges": {},
    "scenario_disagreement": "moderate",
    "goal_conflict": false,
    "high_consequence_uncertainty": false,
    "missing_required_inputs": []
  },

  "versions": {
    "schema": "appraisal-schema-v1",
    "dimension_registry": "appraisal-dimensions-v1",
    "scenario_builder": "interpretation-scenarios-v0.1",
    "rules": "appraisal-rules-v0.1",
    "goal_snapshot": "goals-version-ref",
    "personality_mapper": "personality-appraisal-v1"
  }
}
```

Historical frames are immutable. New inference, memory, runtime evidence, or
counterfactual action simulation creates a new revision and links it through
`supersedes_frame_id`.

## 5. Dimension-value contract

Every dimension uses the same wrapper:

```json
{
  "dimension_key": "appraisal.goal_congruence",
  "definition_version": 1,
  "status": "known",
  "value_type": "signed_ordinal",
  "value": "negative",
  "derivation_strength": "strong",
  "evidence_refs": ["runtime-export-failure", "goal-help-export"],
  "rule_id": "goal-congruence.blocked-goal.v1",
  "required_input_refs": ["scenario-1", "goal-help-export"],
  "missing_input_keys": [],
  "conflict_refs": []
}
```

Allowed statuses are `known`, `unknown`, `disputed`, and `not_applicable`.
Unknown is not neutral, zero, or false. `derivation_strength` describes support
for the appraisal derivation; it is distinct from the value of
`appraisal.certainty`.

Initial value types are:

```text
unsigned_ordinal: low | moderate | high
signed_ordinal:   negative | neutral | positive
agency_category:  user | columbina | third_party | environment | unknown
```

V0 does not expose uncalibrated continuous numbers as if they were psychological
measurements.

## 6. Initial dimension registry

| Dimension | Type | Operational question |
| --- | --- | --- |
| `appraisal.goal_relevance` | unsigned ordinal | How materially does this scenario affect the focal active goal? |
| `appraisal.goal_congruence` | signed ordinal | Does the expected situation advance or obstruct that goal? |
| `appraisal.novelty` | unsigned ordinal | How different is the grounded situation from current expectations and relevant memory? |
| `appraisal.certainty` | unsigned ordinal | How well supported is this interpreted situation and its material implications? |
| `appraisal.controllability` | unsigned ordinal | Can an available, eligible response materially affect the goal or situation? |
| `appraisal.norm_compatibility` | signed ordinal | Is the interpreted situation/effect compatible with applicable values, consent, boundaries, and rules? |
| `appraisal.affiliative_value` | signed ordinal | What does the scenario imply for cooperation, connection, or interpersonal harm? |
| `appraisal.agency` | category | Which agent or environment is causally implicated by available evidence? |

## 7. Deterministic V0 derivation

### 7.1 Shared rules

All derivations must:

1. consume a coherent scenario, never a raw cue alone;
2. retain source interpretation, semantic-frame, goal, runtime, capability,
   memory, and value references;
3. refuse to infer a value when a required input is missing;
4. emit `disputed` when credible inputs conflict;
5. keep authorization and safety enforcement outside appraisal scoring;
6. keep user emotion separate from Columbina's appraisal; and
7. avoid using current dynamic state to rewrite grounded facts.

### 7.2 Goal relevance

```text
high:
  the scenario directly advances, blocks, threatens, or invokes a high-priority
  active goal or obligation

moderate:
  it indirectly affects an active goal or establishes a plausible required
  subgoal

low:
  it is weakly related and has no material expected consequence

unknown:
  the focal goal, event reference, or relationship between them is unresolved
```

A direct request may be highly relevant without being authorized or achievable.

### 7.3 Goal congruence

Compare the scenario's grounded or predicted effects with the focal goal's
desired state:

```text
positive: advances or satisfies the goal
neutral:  has no material effect
negative: blocks, reverses, or threatens the goal
unknown:  effect or desired state is unresolved
```

This corresponds broadly to conduciveness in the Soar/Scherer work.

### 7.4 Novelty

Compare the grounded event signature against current predictions, working
memory, bounded recent events, and relevant retrieved memory:

```text
high:     materially outside current expectations and known matching patterns
moderate: known event type with unexpected arguments, timing, or outcome
low:      expected, recently repeated, or strongly matched in relevant memory
unknown:  required expectation/memory comparison was unavailable
```

Novelty and importance remain separate.

### 7.5 Certainty

Certainty concerns Columbina's support for the scenario and its implications,
not whether the speaker used tentative language by itself:

```text
high:
  strong scenario support, grounded arguments, reliable source/runtime evidence,
  and no material competitor

moderate:
  a reasonable leading scenario with unresolved but weaker alternatives

low:
  major missing arguments, contradictions, weak source grounding, or a similarly
  supported competing scenario

unknown:
  the evidence required to assess certainty is unavailable
```

### 7.6 Controllability

Check registered conversational operators, runtime capabilities, required
parameters, consent/authorization state, and predicted effects:

```text
high:     at least one safe eligible response can materially affect the goal
moderate: progress is possible through clarification, retry, partial help, or escalation
low:      no available eligible response can materially affect the relevant goal
unknown:  capabilities, parameters, authorization, or effects are unresolved
```

Controllability is goal-scoped. Columbina may not control an external failure but
may retain high control over explaining it. Hard gates remain authoritative and
cannot be averaged away by a high appraisal value.

### 7.7 Norm compatibility

Compare the interpreted situation or requested effect with safety, consent,
authorization, core values, user boundaries, and application constraints:

```text
positive: compatible with applicable norms
neutral:  no material normative implication
negative: conflicts with at least one applicable norm or boundary
unknown:  ownership, consent, scope, or applicable norm is unresolved
```

The appraisal describes compatibility. The hard-gate system enforces it.

### 7.8 Affiliative value

```text
positive: cooperation, support, repair, appreciation, or compatible bonding
neutral:  task content with no material social implication
negative: rejection, hostility, pressure, boundary violation, or relational harm
unknown:  social meaning remains ambiguous
```

Positive or negative wording alone cannot determine affiliative value.

### 7.9 Agency

Agency follows semantic roles, action history, tool/runtime provenance, and
causal evidence:

```text
user | columbina | third_party | environment | unknown
```

When several causes remain plausible, the dimension is `disputed` and preserves
the alternatives. V0 does not infer motive or intentionality from agency.

## 8. Partial ordering and incremental appraisal

Appraisal fields become available when their required inputs exist. The V0
dependency order is:

```text
interpreted scenario + active goal
    -> goal relevance

grounded event + expectations/memory
    -> novelty

scenario alternatives + evidence quality
    -> certainty

goal model + event/predicted effect
    -> goal congruence

semantic roles + runtime provenance
    -> agency

capabilities + candidates + parameters + authorization
    -> controllability

values + constraints + consent/boundaries
    -> norm compatibility

pragmatic meaning + social goal + relationship evidence
    -> affiliative value
```

This is a dependency graph rather than a mandatory serial latency pipeline.
Independent rules may execute together once their inputs are ready. Missing later
fields remain unknown rather than blocking every low-risk response.

## 9. Aggregate without erasing ambiguity

The aggregate keeps each frame and reports:

- dominant frames;
- per-dimension possible ranges;
- disagreement among scenarios;
- conflict among active goals;
- high-consequence alternatives; and
- required missing inputs.

For bounded V0 state aggregation only, scenario support may map to:

```text
unsupported = 0
weak        = 1
moderate    = 2
strong      = 3
```

Weights normalize only across compatible retained scenarios and are not called
probabilities. Dynamic-state mappings must preserve incompatible branches and
follow [`DYNAMIC_STATE_SCHEMA_V0.md`](DYNAMIC_STATE_SCHEMA_V0.md); they must not
use this aggregate to average away action-changing or high-consequence
disagreement.

Action selection must inspect high-consequence alternatives and cannot rely only
on an average. A harmless scenario and a plausible destructive scenario do not
average into a moderately safe request.

## 10. Fast, slow, and reappraisal

Columbina uses one appraisal contract:

```text
fast cognition:
  available interpretation + active goals + runtime facts
  -> deterministic appraisal revision

slow cognition:
  deeper inference + memory retrieval + contradiction checking
  + counterfactual action/effect simulation
  -> revised interpretation scenario
  -> new appraisal revision under the same contract
```

Slow cognition does not freely edit an appraisal. It must add or correct a
referenced input and run the registered derivation again. Historical frames
remain available for learning and error attribution.

Triggers for slow refinement include:

- similarly supported scenarios with materially different appraisals;
- disputed high-relevance or high-consequence fields;
- unknown consent, ownership, agency, or controllability needed for action;
- contradiction between memory and trusted runtime facts;
- novel event outside the registered rule coverage; and
- counterfactual effect simulation required for consequential action.

## 11. Personality, relationship, and state boundary

Appraisal is inherently relative to the agent's goals and values, but influence
must remain explicit:

- core values may supply `value_refs` and bounded goal priorities;
- affiliation values may affect which social goals matter;
- personality interpretation biases act before appraisal through scenario
  support and cannot reappear as duplicate evidence;
- personality sensitivities, regulation, baselines, and recovery primarily act
  in the later dynamic-state updater;
- current state may change attention or slow-path pressure but cannot change
  capability, consent, semantic roles, or source evidence; and
- future relationship evidence may influence social expectation only through a
  versioned snapshot with provenance.

For example, high threat sensitivity may increase the size of an activation update
after a negative high-relevance appraisal. It must not manufacture a negative
norm appraisal from neutral evidence.

## 12. Connections downstream

### Dynamic state

The state updater consumes frame values, scenario support, goal relevance,
disagreement, and personality sensitivity. It records exact appraisal-frame
references in every state delta. Appraisal never directly overwrites state.

### Metacognitive router

The router consumes uncertainty, disagreement, missing required inputs,
consequence, novelty, and reversibility. It may request slow refinement,
clarification, abstention, or ordinary fast processing.

### Action selection

Action candidate construction and scoring may use appraisal fields such as goal
relevance, controllability, norm compatibility, and affiliative value. Appraisal
does not grant tool authorization and cannot bypass hard gates.

### Outcome and reappraisal

Later outcome observations do not rewrite the original frame. They may revise
the interpretation of what happened and create a new appraisal revision. Error
attribution can then distinguish incorrect interpretation, incorrect appraisal,
poor state mapping, poor action selection, and realization failure.

## 13. Expansion contract

The outer envelope remains fixed while dimensions live in a versioned registry.
Each definition requires:

```text
dimension key and immutable definition version
operational question
value type and allowed values
required and optional input types
deterministic or learned derivation reference
evidence and conflict requirements
downstream consumers
parent or decomposition relationship
origin and lifecycle status
```

Lifecycle:

```text
candidate -> reviewed -> shadow -> approved -> deprecated -> retired
```

Likely future decompositions are:

| V0 field | Possible later fields |
| --- | --- |
| `novelty` | suddenness, unfamiliarity, unpredictability, expectation discrepancy |
| `certainty` | epistemic certainty, source reliability, outcome likelihood |
| `controllability` | direct control, power/resources, adjustment potential |
| `norm_compatibility` | internal values, external rules, relationship boundaries |
| `agency` | causal agent, motive, intentionality, negligence, accident |
| `goal_congruence` | immediate effect, future expected effect, reversibility |
| New field | urgency/time pressure |

A new dimension is justified only when it predicts a distinct state, route, or
action consequence; cannot be represented by an existing field; has reviewable
evidence; and improves downstream behavior in shadow evaluation.

## 14. Model roadmap

### V0

- deterministic scenario compatibility rules;
- deterministic appraisal derivations;
- ordinal values and support;
- explicit unknown/disputed handling;
- immutable traces and revisions; and
- no LLM or online learning in the fast path.

### V1

After reviewed appraisal cases exist, compact supervised heads may predict
specific appraisal dimensions from the accepted scenario representation. A
symbolic layer still enforces types, evidence, goal scope, runtime facts, hard
boundaries, and unknown behavior. Learned outputs run in shadow before promotion.

### Later

- calibrated continuous values where data supports them;
- learned scenario compatibility;
- action-effect and outcome-likelihood models;
- counterfactual appraisal through a world/action model;
- relationship-conditioned residuals with privacy and minimum-data guards; and
- offline learning from reviewed outcome/error-attribution records.

No maturity milestone requires replacing an accurate deterministic rule with a
neural model.

## 15. Validation required before implementation

The delegated authoring contract for this fixture is
[`APPRAISAL_DESIGN_CASES_README.md`](APPRAISAL_DESIGN_CASES_README.md).

Create appraisal design cases that cover:

1. one clear interpretation and one active goal;
2. competing literal, indirect, and tone-mismatch scenarios;
3. one event that advances one goal and obstructs another;
4. novelty without importance and importance without novelty;
5. speaker uncertainty versus Columbina's appraisal certainty;
6. controllable conversation but uncontrollable external state;
7. available capability with missing consent or parameters;
8. ambiguous and disputed agency;
9. norm incompatibility that remains blocked regardless of personality;
10. affiliative meaning that disagrees with lexical sentiment;
11. missing memory, goal, capability, and relationship inputs;
12. fast appraisal followed by slow evidence-based reappraisal;
13. personality changing state sensitivity without changing facts; and
14. later outcome evidence revising appraisal without rewriting history.

Only after these cases pass should the logical schema be mapped to PostgreSQL or
the state-transition matrix be assigned numerical coefficients.
