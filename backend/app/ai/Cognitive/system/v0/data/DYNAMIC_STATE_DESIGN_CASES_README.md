# Dynamic-state design-case authoring brief

## Objective

Create the manually reviewable dynamic-state fixture at:

```text
backend/app/ai/Cognitive/system/v0/data/dynamic_state_design_cases.json
```

The fixture must validate that the accepted dynamic-state design remains
connected to per-scenario appraisal, focal goals, compiled personality controls,
elapsed time, trusted outcomes, attention, metacognitive routing, initiative,
and action selection without rewriting any upstream evidence.

This task validates a design contract. It does not implement or evaluate the
runtime state updater.

## Required references

Read these files completely before authoring cases:

1. `backend/app/ai/Cognitive/system/v0/data/DYNAMIC_STATE_SCHEMA_V0.md`
   - canonical state registry, scope, envelopes, deterministic pipeline,
     appraisal mappings, status handling, personality boundary, decay,
     derived controls, roadmap, and validation requirements.
2. `backend/app/ai/Cognitive/system/v0/data/APPRAISAL_SCHEMA_V0.md`
   - appraisal dimensions, statuses, per-scenario/per-goal scope, aggregation,
     reappraisal, and downstream boundaries.
3. `backend/app/ai/Cognitive/system/v0/data/appraisal_design_cases.json`
   - current appraisal-frame shapes, evidence conventions, scenario contrasts,
     focal goals, personality profiles, and immutable reappraisal examples.
4. `backend/app/ai/Cognitive/system/v0/data/OUTCOME_OBSERVATION_SCHEMA_V0.md`
   - observation provenance, outcome episodes, silence handling, worker
     triggers, and deterministic outcome dimensions.
5. `backend/app/ai/Cognitive/system/v0/README.md`
   - full cognitive trace, personality mapper direction, fast/slow routing,
     action scoring, realization, outcome, and learning boundaries.
6. `backend/app/ai/Cognitive/system/v0/data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`
   - accepted action targets, policy features, gates, transition effects, and
     action/state separation.
7. `backend/app/ai/Cognitive/system/v0/data/interpretation_design_cases.json`
   - existing qualitative dynamic-state directions and the upstream evidence
     they must not overwrite.

If this brief conflicts with `DYNAMIC_STATE_SCHEMA_V0.md`,
`APPRAISAL_SCHEMA_V0.md`, or the canonical README, report the conflict in the
task summary. Do not silently invent a resolution.

## Accepted decisions: do not reopen

The fixture validates these decisions:

1. Appraisal evaluates a situation; dynamic state records the bounded condition
   left by appraisal, outcomes, and time.
2. V0 state keys are valence, activation, epistemic confidence, curiosity,
   engagement, goal pressure, social alignment, interaction strain, and a
   structured attention map.
3. State is scoped. Confidence, curiosity, goal pressure, alignment, strain,
   and attention must retain their target references.
4. V0 updates are deterministic, inspectable, replayable, and versioned.
5. Design cases use qualitative bands and transition strength. They do not
   invent exact unreviewed coefficients, decay constants, or psychological
   measurements.
6. Unknown appraisal values do not become neutral state contributions.
7. Disputed or incompatible appraisal branches remain explicit and are not
   averaged into a midpoint.
8. High-consequence alternatives cannot be neutralized by a more benign branch.
9. Personality changes baseline, persistence, recovery, bounded sensitivity,
   and thresholds. It cannot change evidence, appraisal status, contribution
   direction, safety, consent, authorization, or causal agency.
10. Outcomes create later state revisions. They do not rewrite the state that
    existed when an action was selected.
11. Silence is not success, repair is not task completion, task failure is not
    social rejection, novelty is not uncertainty, and attention is not truth.
12. Dynamic state informs action preference and metacognitive routing but never
    bypasses the deterministic action hard gate.
13. Relationship trust is not a V0 dynamic-state key. `social_alignment`
    describes the current interaction only.
14. Appraisal controllability is not duplicated as a persistent `control`
    state.
15. Discrete emotions such as anger, fear, sadness, happiness, or sarcasm are
    not V0 state keys.

## Strict task boundary

The delegated agent must:

- create only `dynamic_state_design_cases.json` unless a syntax error in this
  brief is explicitly authorized for correction;
- keep the fixture at `draft_for_human_review`;
- create manual, explainable expected transitions and revisions;
- reuse or adapt reviewed-shaped appraisal cases where useful without modifying
  their source fixtures;
- make every local reference resolve within its case;
- preserve scope, unknowns, disputes, branches, and provenance explicitly;
- keep personality profiles as controls rather than evidence;
- list unresolved policy questions instead of inventing precise constants; and
- report coverage, validation results, and open questions.

The delegated agent must not:

- implement state, appraisal, routing, action, memory, outcome, or learning
  code;
- modify database models, migrations, prompts, production schemas, or model
  artifacts;
- edit existing perception, interpretation, appraisal, action, or outcome
  fixtures and documents;
- call an LLM to manufacture ground-truth labels;
- train, download, or select a machine-learning model;
- invent exact transition coefficients, decay half-lives, or personality
  multipliers;
- infer a user's internal emotion from Columbina's state;
- add an unapproved state key merely to make a case convenient;
- treat personality, wording sentiment, or action preference as factual
  evidence;
- let a state transition authorize an external side effect; or
- treat the fixture as approved training data.

## File-level schema

Use one formatted JSON document:

```json
{
  "schema_version": 1,
  "dynamic_state_schema_version": 1,
  "appraisal_schema_version": 1,
  "outcome_observation_schema_version": 1,
  "state_policy_version": "dynamic-state-rules-v0.1-draft",
  "evaluation_scope": "dynamic_state_design",
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Dynamic State Schema V0.1",
  "cases": []
}
```

Create **24–30 cases**. Prefer natural minimal contrasts. Do not pack unrelated
state transitions into one artificial conversation merely to reduce case count.

## Case-level schema

Each ordinary case uses this logical shape:

```json
{
  "case_id": "relevant-recoverable-information-gap",
  "title": "Relevant missing recipient raises scoped curiosity",
  "objective": "Separate curiosity from novelty and global uncertainty.",
  "coverage_requirements": [5, 11, 15],

  "conversation": {
    "messages": [],
    "focus_message_id": "m1"
  },

  "cognitive_objects": {
    "semantic_frames": [],
    "hypotheses": [],
    "unknowns": [],
    "contradictions": [],
    "goals": [],
    "outcomes": []
  },

  "appraisal_input": {
    "frames": [],
    "aggregate": {}
  },

  "previous_state": {
    "snapshot_id": "state-before",
    "revision": 1,
    "policy_version": "dynamic-state-rules-v0.1-draft",
    "values": [],
    "attention": []
  },

  "elapsed_time": {
    "duration_band": "none",
    "clock_source": "monotonic_runtime",
    "evidence_ref": null
  },

  "personality_profiles": [],
  "expected_by_profile": [],
  "forbidden_updates": [],
  "downstream_expectations": [],
  "dynamic_state_questions": [],

  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

Sequence cases add `transition_steps`. Do not add it to ordinary one-step cases.

## Accepted state keys and scopes

Use only these keys:

```text
state.valence
state.activation
state.epistemic_confidence
state.curiosity
state.engagement
state.goal_pressure
state.social_alignment
state.interaction_strain
```

Accepted scope types:

```text
conversation
participant
goal
topic
proposition
hypothesis
unknown
appraisal_frame
action_candidate
action_plan
outcome_episode
```

Default scope rules:

| State | Required scope |
| --- | --- |
| `state.valence` | conversation |
| `state.activation` | conversation |
| `state.epistemic_confidence` | proposition, hypothesis, appraisal frame, action candidate, or action plan |
| `state.curiosity` | unknown, topic, hypothesis, or proposition |
| `state.engagement` | conversation or goal |
| `state.goal_pressure` | goal |
| `state.social_alignment` | conversation or participant |
| `state.interaction_strain` | conversation or goal |

An exception requires a non-empty `dynamic_state_questions` entry. Do not use a
global scope merely because the correct target is inconvenient to represent.

## Qualitative state-value contract

Design cases do not settle the open numeric policy. A previous or expected state
value uses:

```json
{
  "state_value_id": "state-curiosity-recipient-before",
  "state_key": "state.curiosity",
  "scope_type": "unknown",
  "scope_ref": "unknown-recipient",
  "status": "known",
  "band": "moderate",
  "baseline_band": "low",
  "evidence_refs": ["prior-state-evidence"],
  "policy_version": "dynamic-state-rules-v0.1-draft"
}
```

Unsigned bands:

```text
very_low | low | moderate | high | very_high
```

Signed bands:

```text
strong_negative | negative | neutral | positive | strong_positive
```

Accepted value statuses:

```text
known | unknown | provisional | not_applicable
```

Rules:

- `known` requires one allowed band and supporting references;
- `unknown` uses `band: null` and explicit missing inputs;
- `provisional` uses `band: null`, two or more branch references, and an
  `expected_band_range` or explicit alternatives;
- `not_applicable` uses `band: null` and explains why the state question does
  not apply to this scope; and
- no case uses arbitrary decimals or exact scores.

## Appraisal-input contract

Each appraisal frame must retain:

```json
{
  "frame_id": "appraisal-frame-1",
  "revision": 1,
  "scenario_id": "scenario-1",
  "scenario_support": "strong",
  "focal_goal_ref": "goal-1",
  "goal_priority_band": "high",
  "dimensions": {
    "appraisal.goal_relevance": {},
    "appraisal.goal_congruence": {},
    "appraisal.novelty": {},
    "appraisal.certainty": {},
    "appraisal.controllability": {},
    "appraisal.norm_compatibility": {},
    "appraisal.affiliative_value": {},
    "appraisal.agency": {}
  }
}
```

Every dimension includes its status, qualitative value, evidence references,
conflict or missing references, and derivation strength where applicable. A
case may abbreviate unrelated evidence objects, but it must never omit a field
whose status changes the expected state transition.

The aggregate must preserve dominant frames, dimension ranges, scenario
disagreement, goal conflict, high-consequence uncertainty, and missing required
inputs. The updater consumes individual frames; the aggregate is routing and
summary input, not permission to average incompatible branches.

## Previous-state contract

Every case supplies the minimal previous state necessary to test the transition.
Do not assume an invisible neutral state.

Previous values must specify:

- state key and scope;
- status and band;
- baseline band;
- policy version;
- evidence or initialization source; and
- whether the value remains active, resolved, superseded, or decaying.

An omitted key means the case does not assert an expected transition for that
key. It does not mean a zero value.

## Personality-profile contract

Every case includes a neutral compiled profile:

```json
{
  "profile_id": "neutral",
  "mapper_version": "personality-runtime-v0.1-draft",
  "state_controls": {
    "state.curiosity": {
      "baseline_band": "moderate",
      "persistence_band": "moderate",
      "recovery_band": "moderate",
      "sensitivity": "neutral",
      "maximum_step_band": "moderate"
    }
  }
}
```

Personality contrast cases include at least two profiles while holding the
conversation, cognitive objects, appraisal, prior state, elapsed time, and
outcomes constant.

Accepted qualitative sensitivity values:

```text
strongly_dampened | dampened | neutral | amplified | strongly_amplified
```

Accepted persistence and recovery bands:

```text
low | moderate | high
```

The expected contribution direction must remain invariant across profiles.
Profiles may change magnitude, persistence, recovery, or a derived threshold
only within the schema boundary.

## Expected contribution contract

Each expected transition is explained through one or more contributions:

```json
{
  "contribution_id": "contribution-curiosity-gap",
  "state_key": "state.curiosity",
  "scope_type": "unknown",
  "scope_ref": "unknown-recipient",
  "direction": "increase",
  "base_step_band": "moderate",
  "expected_profile_effect": "amplified",
  "source_appraisal_frame_refs": ["appraisal-frame-1"],
  "source_dimension_keys": [
    "appraisal.goal_relevance",
    "appraisal.novelty",
    "appraisal.certainty",
    "appraisal.controllability"
  ],
  "goal_ref": "goal-send-report",
  "evidence_refs": ["unknown-recipient"],
  "rule_id": "curiosity.relevant-recoverable-gap.v1",
  "inhibition_refs": [],
  "rationale": "The relevant recipient gap is recoverable by asking."
}
```

Accepted directions:

```text
increase | decrease | unchanged | no_contribution | branch_dependent
```

Accepted base-step bands:

```text
none | weak | moderate | strong
```

`unchanged` means the update ran and retained the existing band.
`no_contribution` means the cited appraisal dimension must not affect that state.
Do not use them interchangeably.

Every contribution must identify the rule, affected scope, source appraisal or
outcome, and evidence. Personality references belong in
`expected_profile_effect`, not `evidence_refs`.

## Expected profile result

```json
{
  "profile_id": "neutral",
  "expected_decay": [],
  "expected_contributions": [],
  "expected_state": {
    "snapshot_revision": 2,
    "supersedes_snapshot_id": "state-before",
    "values": [],
    "attention": [],
    "derived_controls": {}
  }
}
```

Every expected state value records:

- state key and scope;
- status;
- previous band;
- expected direction;
- expected resulting band or permitted band range;
- contribution references;
- invariant references; and
- rationale.

The fixture may leave `expected_resulting_band` as a constrained set when the
open threshold policy prevents an exact answer. It must still assert direction,
scope, status, and forbidden results.

## Decay expectations

Time-based cases use qualitative elapsed-time bands:

```text
none | short | medium | long
```

An expected decay record uses:

```json
{
  "state_key": "state.activation",
  "scope_type": "conversation",
  "scope_ref": "conversation-1",
  "baseline_band": "low",
  "previous_band": "high",
  "elapsed_time_band": "medium",
  "expected_direction": "decrease",
  "expected_resulting_bands": ["moderate", "low"],
  "rule_id": "decay.activation-toward-baseline.v1",
  "rationale": "No continuing activating evidence is present."
}
```

Decay must use elapsed monotonic time rather than message count or number of
model calls. Curiosity, confidence, goal pressure, alignment, and strain require
their schema-specific persistence conditions; they do not all decay like
activation.

## Attention expectation contract

Attention items use qualitative weights:

```json
{
  "target_type": "unknown",
  "target_ref": "unknown-recipient",
  "weight_band": "high",
  "reason_refs": ["appraisal-frame-1", "goal-send-report"],
  "must_rank_above_refs": ["topic-small-talk"],
  "rationale": "The missing recipient blocks a high-priority goal."
}
```

Accepted weight bands:

```text
low | moderate | high | critical
```

`critical` is a processing-priority label, not proof that the target is true.
Cases must preserve unsupported, contradicted, and disputed target status.

## Derived-control expectations

Use only these derived controls:

```text
slow_cognition_pressure
initiative_readiness
clarification_pressure
repair_pressure
exploration_readiness
response_energy
```

Values are:

```text
very_low | low | moderate | high | very_high
```

Every control includes reason references and a rationale. Controls are derived
views rather than persisted emotions or action authorization.

Required boundaries:

- high slow pressure may route to slow cognition but does not prove an
  interpretation;
- high initiative readiness remains subject to conversational timing,
  authorization, and the action hard gate;
- high clarification pressure does not require asking if trusted internal
  evidence can resolve the unknown;
- high repair pressure does not imply Columbina caused the original problem;
- high exploration readiness does not authorize private or unavailable data
  access; and
- response energy affects bounded realization style, not action semantics.

## Unknown and disputed expectations

Unknown appraisal input produces no ordinary signed or unsigned midpoint
contribution. A separate contribution may affect scoped confidence, curiosity,
attention, or slow routing only when its missing-input reference and
recoverability are explicit.

Disputed cases must include:

- every live appraisal branch;
- branch-specific proposed contributions;
- whether their directions agree or conflict;
- whether uncontested contributions may commit;
- whether the contested state remains provisional;
- the permitted state interval or band alternatives; and
- the slow/clarify/continue routing consequence.

Never average `positive` and `negative` into `neutral`, or `high` and `low` into
`moderate`, merely because both branches exist.

## Outcome and sequence contract

Sequence cases use:

```json
{
  "transition_steps": [
    {
      "step": 1,
      "trigger_type": "appraisal",
      "new_input_refs": ["appraisal-frame-1"],
      "previous_snapshot_ref": "state-before",
      "expected_snapshot": {},
      "expected_invariants": []
    },
    {
      "step": 2,
      "trigger_type": "observed_outcome",
      "new_input_refs": ["outcome-repair-confirmed"],
      "previous_snapshot_ref": "state-after-step-1",
      "expected_snapshot": {},
      "expected_invariants": []
    }
  ]
}
```

Accepted triggers:

```text
appraisal | reappraisal | elapsed_time | observed_outcome | explicit_correction
```

Each step creates a new immutable revision and supersedes exactly one supplied
prior snapshot. Unaffected values remain unchanged and must not cite the new
input as evidence merely because they appear in the later snapshot.

Silence, disconnection, expiration, and missing voice capture remain unknown
outcomes. They must not produce success contributions.

## Forbidden-update contract

Every case lists at least one case-specific forbidden update. Examples:

```text
The user caused an event, therefore social alignment decreases.
The task failed, therefore the user rejected Columbina.
Positive wording makes negative goal congruence positive.
High attention makes the attended hypothesis true.
Unknown novelty becomes moderate curiosity.
High curiosity authorizes an external search.
Low controllability erases the active goal.
Successful apology marks the task complete.
Personality changes a negative contribution into a positive one.
Silence lowers goal pressure as if completion were observed.
```

## Downstream expectation contract

Downstream expectations may identify action-policy or routing consequences, but
must preserve separation:

```json
{
  "consumer": "action_selector",
  "expected_effect": "increase_affinity",
  "target": "seek_information.clarify",
  "reason_refs": ["control-clarification-pressure"],
  "invariants": [
    "The state does not make the action eligible.",
    "The hard gate and registry lifecycle remain authoritative."
  ]
}
```

Do not provide final wording, execute tools, or invent an action outside the
registry.

## Required coverage

Every numbered requirement must appear in at least one case's
`coverage_requirements`.

### State dimensions and scope

1. valence increase from positive goal congruence;
2. valence decrease from negative goal congruence;
3. high relevance with neutral congruence does not create a valence sign;
4. activation from relevance and novelty without inventing hazard;
5. confidence scoped to one proposition while another remains stable;
6. curiosity for a relevant recoverable information gap;
7. irrelevant uncertainty produces little or no curiosity;
8. an unanswerable or prohibited gap does not produce unrestricted exploration;
9. engagement sustained by relevant viable progress;
10. engagement reduced after a verified close or lack of active relevant goals;
11. goal pressure increases for an important unresolved goal;
12. positive congruence without outcome evidence does not falsely complete a
    goal or reduce its pressure;
13. verified progress or completion reduces scoped goal pressure;
14. positive, negative, and neutral social-alignment transitions;
15. repeated misunderstanding or failure accumulates scoped strain;
16. verified repair reduces strain without asserting task success;
17. two targets retain different confidence, curiosity, pressure, or strain;
18. one event affects two goals differently without cross-scope contamination.

### Appraisal mapping and ambiguity

19. novelty with high certainty versus novelty with low certainty;
20. controllable conversation with uncontrollable external state;
21. task failure without social misalignment;
22. social repair without false task completion;
23. unknown appraisal produces no midpoint state value;
24. disputed branches with the same state direction may commit an uncontested
    bounded update;
25. disputed branches with opposite directions preserve a provisional range;
26. a high-consequence alternative cannot be averaged away;
27. agency attribution assigns scope without automatically creating blame;
28. negative norm compatibility raises boundary attention while hard gates stay
    independent.

### Personality, time, outcomes, and control

29. two personality profiles change update magnitude but not direction or fact;
30. personality-specific baseline and persistence affect decay without changing
    the triggering appraisal;
31. activation decays toward baseline with elapsed time;
32. curiosity persists only while its referenced gap remains active and
    learnable;
33. confidence changes through new evidence, correction, or outcome rather than
    arbitrary time alone;
34. social alignment does not automatically become relationship memory;
35. attention ranking prioritizes a relevant object without turning salience
    into truth;
36. slow-cognition pressure rises for decision-changing disagreement;
37. initiative readiness combines pressure, engagement, confidence, timing,
    patience, and authorization without bypassing gates;
38. explicit correction creates a later revision and leaves history immutable;
39. silence does not count as success;
40. unaffected state dimensions do not cite later evidence;
41. outcome evidence changes only supported dimensions;
42. safety, consent, authorization, and capability remain invariant under every
    personality and state profile.

One natural case may cover several requirements. Do not add meaningless fields
solely to claim coverage.

## Recommended case set

A coherent 26–29 case suite can include:

1. positive progress on a high-priority goal;
2. negative obstruction of a high-priority goal;
3. highly relevant but neutral information;
4. novel and clearly understood event;
5. novel recoverable uncertainty;
6. irrelevant uncertainty;
7. unanswerable information gap;
8. prohibited information gap;
9. viable ongoing goal sustaining engagement;
10. verified conversation close;
11. unresolved goal pressure before outcome;
12. verified goal completion;
13. positive social acknowledgement;
14. explicit social rejection or boundary mismatch;
15. neutral technical report;
16. repeated technical failure without social rejection;
17. repeated misunderstanding accumulating strain;
18. successful repair without task completion;
19. two propositions with different confidence;
20. two unknowns with different curiosity;
21. controllable clarification around uncontrollable external state;
22. unknown appraisal input;
23. disputed branches agreeing in direction;
24. disputed branches opposing in direction;
25. high-consequence destructive alternative;
26. personality-sensitivity contrast;
27. elapsed-time decay sequence;
28. explicit-correction or trusted-outcome revision sequence; and
29. autonomous initiative with patience and authorization boundaries.

This is guidance, not permission to skip the numbered coverage table.

## Review and validation procedure

After authoring the fixture:

1. Validate JSON syntax with a standard parser.
2. Confirm 24–30 cases and unique case, message, cognitive-object, appraisal,
   snapshot, state-value, contribution, attention, outcome, and evidence ids
   within their applicable scopes.
3. Verify all text spans exactly match their source messages.
4. Verify every runtime JSON Pointer resolves and its value matches.
5. Verify every appraisal frame has one scenario and focal goal and contains all
   eight appraisal dimensions exactly once.
6. Verify every state key and scope type is accepted.
7. Verify state bands, statuses, nullability, direction, and step bands obey this
   brief.
8. Verify every evidence, goal, scenario, branch, contribution, scope, reason,
   missing-input, conflict, outcome, and supersession reference resolves.
9. Verify each contribution cites sufficient appraisal or outcome input and the
   proper scoped target.
10. Verify no unknown value receives a midpoint contribution.
11. Verify disputed branches are preserved and incompatible directions are not
    averaged.
12. Verify profile contrasts hold all non-personality input constant and never
    change contribution direction or grounded facts.
13. Verify decay uses elapsed-time evidence and moves toward the declared
    baseline.
14. Verify every sequence revision supersedes an existing immutable snapshot.
15. Verify new evidence is cited only by dimensions it can affect.
16. Verify attention weight never changes the epistemic status of its target.
17. Verify derived controls have reason references and do not authorize actions.
18. Verify downstream expectations preserve hard gates, lifecycle, consent,
    safety, and capability.
19. Verify all 42 coverage requirements are represented and produce a coverage
    table in the task summary.
20. List every non-empty `dynamic_state_questions` entry without silently
    resolving it.
21. Run `git diff --check` and report the result.

Do not mark any case reviewed or approved. Human review occurs after fixture
creation.

## Completion criteria

The delegated task is complete only when:

- one valid JSON fixture exists at the required path;
- it contains 24–30 manual cases;
- all state keys, scopes, mappings, statuses, profiles, controls, and sequences
  obey this brief;
- all 42 required contrasts are covered;
- every reference resolves;
- ambiguity, missingness, and branch conflict remain visible;
- no implementation or unrelated file changed;
- every case remains pending human review; and
- the task summary reports validation and unresolved policy questions.

## Required task summary

Report:

- output path and case count;
- sequence-case and personality-contrast counts;
- coverage by state key, scope type, appraisal dimension, status, direction,
  attention target, derived control, outcome trigger, and route consequence;
- the numbered 1–42 coverage table;
- every unresolved dynamic-state question;
- conflicts among source documents, if any;
- JSON/reference validation and `git diff --check` results; and
- confirmation that no runtime implementation, database, prompt, or model file
  changed.

## Suggested delegated-agent prompt

```text
Read DYNAMIC_STATE_DESIGN_CASES_README.md and every required reference it names.
Create only backend/app/ai/Cognitive/system/v0/data/dynamic_state_design_cases.json.
Follow the schema and strict boundary exactly, keep all cases pending human
review, validate every reference and required contrast, and report open design
questions rather than inventing numeric transition policy.
```

