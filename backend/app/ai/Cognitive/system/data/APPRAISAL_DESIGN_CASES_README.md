# Appraisal design-case authoring brief

## Objective

Create the manually reviewable appraisal fixture at:

```text
backend/app/ai/Cognitive/system/data/appraisal_design_cases.json
```

The fixture must validate that the accepted appraisal design remains connected
to coherent interpretation scenarios, semantic frames, active goals, runtime
facts, memory, values, capabilities, metacognitive routing, dynamic-state input,
action selection, and later reappraisal.

This task validates a design contract. It does not implement or evaluate the
runtime appraisal engine.

## Required references

Read these files before authoring cases:

1. `backend/app/ai/Cognitive/system/data/APPRAISAL_SCHEMA_V0.md`
   - canonical appraisal boundary, envelope, dimensions, V0 derivation,
     aggregation, reappraisal, expansion, and validation requirements.
2. `backend/app/ai/Cognitive/system/README.md`
   - perception and interpretation boundary;
   - initial interpretation ontology;
   - core-memory interpretation biases;
   - appraisal/dynamic-state connection;
   - metacognitive routing;
   - action candidate and hard-gate contracts.
3. `backend/app/ai/Cognitive/system/data/interpretation_design_cases.json`
   - existing semantic frames, hypotheses, evidence conventions, runtime facts,
     memory records, and ambiguity contrasts.
4. `backend/app/ai/Cognitive/system/data/INTERPRETATION_DESIGN_CASES_README.md`
   - accepted interpretation keys, evidence rules, support semantics, and
     ontology boundaries.
5. `backend/app/ai/Cognitive/system/data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`
   - approved conversational operators and target/effect vocabulary when a
     controllability case needs an available response.

If this brief conflicts with `APPRAISAL_SCHEMA_V0.md` or the canonical README,
report the conflict in the task summary. Do not silently invent a resolution.

## Accepted decisions: do not reopen

The fixture validates these decisions:

1. Appraisal evaluates an interpreted situation relative to Columbina's goals,
   values, capabilities, and social connection.
2. Appraisal consumes coherent interpretation scenarios, not raw messages,
   isolated cue labels, or arbitrary cross-products of hypotheses.
3. A scenario builder groups existing hypotheses and adds no new semantic
   meaning.
4. One appraisal frame is scoped to one scenario and one focal goal. Different
   effects on different goals require separate frames.
5. V0 uses qualitative ordinal or categorical values, not invented continuous
   psychological measurements.
6. All fields may remain `unknown`; conflicting credible evidence becomes
   `disputed`, not an average.
7. Fast and slow cognition use the same appraisal contract. Slow cognition adds
   evidence or refines interpretation and creates a new immutable revision.
8. Personality cannot change grounded content, runtime facts, capability,
   consent, or safety. It primarily affects later state sensitivity and action
   affinity.
9. Dynamic state does not rewrite appraisal evidence.
10. Hard safety, authorization, and consent gates remain authoritative outside
    appraisal scoring.
11. High-consequence alternatives cannot be made safe by averaging them with a
    benign interpretation.
12. V0 is deterministic and inspectable. It uses no generative LLM and performs
    no online policy or personality update.

## Strict task boundary

The delegated agent must:

- create only `appraisal_design_cases.json` unless a syntax error in this brief
  is explicitly authorized for correction;
- keep the fixture at `draft_for_human_review`;
- create manual, explainable expected scenarios and appraisal results;
- reuse existing interpretation cases where useful without modifying them;
- make every evidence reference resolve within the case;
- preserve unknown, disputed, partial, and conflicting states explicitly;
- report coverage, validation results, and open design questions.

The delegated agent must not:

- implement appraisal, scenario building, routing, state-update, action, memory,
  or learning code;
- modify database models, migrations, prompts, production schemas, or artifacts;
- edit the interpretation or action design fixtures;
- call an LLM to manufacture ground-truth labels;
- train, download, or select a machine-learning model;
- add precise appraisal floats, scenario probabilities, emotion intensities, or
  state-transition coefficients;
- add an emotion label such as angry, afraid, happy, or sarcastic as an
  appraisal dimension;
- infer user affect from Columbina's appraisal;
- invent a new appraisal key to make a case convenient;
- treat personality as source evidence;
- treat the fixture as approved training data.

## File-level schema

Use one formatted JSON document:

```json
{
  "schema_version": 1,
  "appraisal_schema_version": 1,
  "interpretation_ontology_version": 1,
  "scenario_builder_version": "interpretation-scenarios-v0.1",
  "appraisal_rules_version": "appraisal-rules-v0.1",
  "evaluation_scope": "appraisal_design",
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Appraisal Schema V0.1",
  "cases": []
}
```

Create **24–30 cases**. A natural case may cover several required contrasts, but
do not create an artificial message solely to pack unrelated fields together.

## Case-level schema

Each case uses this logical shape:

```json
{
  "case_id": "clear-direct-information-request",
  "title": "Clear request relevant to an active explanation goal",
  "objective": "Validate a single coherent scenario and a complete frame.",

  "conversation": {
    "messages": [
      {
        "message_id": "msg-1",
        "role": "user",
        "content": "Why did the estimate change?"
      }
    ],
    "focus_message_id": "msg-1"
  },

  "interpretation_input": {
    "interpretation_trace_id": "interp-case-1",
    "semantic_frames": [],
    "hypotheses": {
      "communicative_acts": [],
      "speaker_goals": [],
      "modalities": [],
      "pragmatic_meanings": []
    },
    "unknowns": [],
    "contradictions": []
  },

  "active_goals": [],
  "runtime_context": {},
  "retrieved_memories": [],
  "value_context": [],
  "relationship_context": null,

  "expected_scenarios": [],
  "expected_appraisal_frames": [],
  "expected_aggregate": {},
  "expected_route": {},
  "downstream_invariants": [],
  "forbidden_inferences": [],
  "appraisal_questions": [],

  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

Sequence/reappraisal cases add:

```json
{
  "reappraisal_steps": [
    {
      "step": 1,
      "new_inputs": [],
      "expected_supersedes_frame_ids": [],
      "expected_appraisal_frames": [],
      "expected_invariants": []
    }
  ]
}
```

Do not add `reappraisal_steps` to ordinary single-revision cases.

## Interpretation input contract

Appraisal cases begin after interpretation. Every hypothesis must use an
accepted interpretation key and include its id, support, disposition, supporting
evidence, contradicting evidence, and rationale.

Accepted communicative acts:

```text
interpretation.act.inform
interpretation.act.request_information
interpretation.act.request_action
interpretation.act.correct
interpretation.act.confirm
interpretation.act.reject_or_decline
interpretation.act.acknowledge
interpretation.act.evaluate
interpretation.act.greet
interpretation.act.close
```

Accepted speaker goals:

```text
interpretation.goal.transfer_information
interpretation.goal.obtain_information
interpretation.goal.cause_action
interpretation.goal.prevent_or_stop_action
interpretation.goal.repair_shared_understanding
interpretation.goal.express_stance
interpretation.goal.seek_acknowledgement
interpretation.goal.maintain_social_connection
interpretation.goal.unknown
```

Accepted modalities:

```text
interpretation.modality.asserted
interpretation.modality.tentative
interpretation.modality.questioned
interpretation.modality.requested
interpretation.modality.hypothetical
interpretation.modality.quoted
```

Accepted pragmatic meanings:

```text
interpretation.pragmatic.literal
interpretation.pragmatic.indirect
interpretation.pragmatic.tone_situation_mismatch
interpretation.pragmatic.unknown
```

Use only the V0 support levels `unsupported`, `weak`, `moderate`, and `strong`.
Use only `leading`, `retained_alternative`, `rejected`, and `unknown` as
dispositions. Do not invent probabilities.

Semantic frames must contain a bounded predicate, arguments, polarity, source
spans, and unresolved arguments. Use interpretation evidence conventions:

- `text_span` with exact zero-based, end-exclusive offsets;
- `cue_activation` referring to a supplied activation;
- `semantic_frame` referring to a frame id or predicate;
- `runtime_fact` using a valid JSON Pointer into `runtime_context`; and
- `memory_record` referring to a supplied memory id.

## Active-goal contract

Every appraisal frame needs a focal goal supplied by the case:

```json
{
  "goal_id": "goal-explain-current-estimate",
  "owner": "columbina",
  "goal_type": "conversation",
  "description": "Help the user understand the current estimate.",
  "desired_state": {
    "user_has_explanation": true
  },
  "priority_band": "high",
  "status": "active",
  "source_refs": ["msg-1"]
}
```

Allowed priority bands are `low`, `moderate`, and `high`. Priority is input from
the goal system, not something appraisal invents. Goal owners are `user`,
`columbina`, `shared`, or `system`. Include at least two cases where the same
scenario receives separate frames because it affects two active goals
differently.

System safety may be represented as a constraint or value reference but must not
be converted into an ordinary goal whose low priority could be traded away.

## Expected-scenario contract

Each expected scenario must reference only hypotheses and semantic frames present
in `interpretation_input`:

```json
{
  "scenario_id": "scenario-1",
  "support": "strong",
  "disposition": "leading",
  "semantic_frame_refs": ["frame-1"],
  "hypothesis_refs": {
    "communicative_acts": ["hyp-act-1"],
    "speaker_goals": ["hyp-goal-1"],
    "modalities": ["hyp-modality-1"],
    "pragmatic_meanings": ["hyp-pragmatic-1"]
  },
  "supporting_evidence_refs": ["evidence-1"],
  "contradiction_refs": [],
  "unknown_refs": [],
  "compatibility_rule_ids": ["scenario.request-question.v1"],
  "rationale": "The hypotheses describe one compatible reading."
}
```

Use scenarios to prevent invalid combinations, not to force one meaning. Include
leading and retained alternatives whenever they imply materially different
appraisal, routing, authorization, or action consequences.

Do not combine:

- mutually contradictory semantic polarities;
- quoted content with speaker-owned assertion unless both levels are represented
  explicitly;
- literal and tone-mismatch interpretations as if they were the same reading;
- rejected hypotheses with a leading scenario; or
- speaker goals belonging to different interpretations merely because they have
  similar support.

## Expected appraisal-frame contract

Each frame is scoped to exactly one `scenario_id` and one `focal_goal_ref`:

```json
{
  "frame_id": "frame-scenario-1-goal-1-r1",
  "revision": 1,
  "supersedes_frame_id": null,
  "scenario_id": "scenario-1",
  "scenario_support": "strong",
  "subject": {
    "event_refs": ["frame-1"],
    "focal_goal_ref": "goal-explain-current-estimate",
    "goal_owner": "columbina",
    "goal_priority_band": "high",
    "value_refs": ["value-helpfulness"],
    "capability_snapshot_ref": "capability-snapshot-1",
    "relationship_snapshot_ref": null
  },
  "expected_dimensions": {},
  "missing_inputs": [],
  "conflicts": [],
  "completeness": "complete"
}
```

Every frame must contain **all eight** appraisal keys. If evidence cannot support
a value, record `unknown`; do not omit the key.

## Dimension expectation contract

Use this wrapper for every field:

```json
{
  "definition_version": 1,
  "status": "known",
  "value_type": "unsigned_ordinal",
  "value": "high",
  "derivation_strength": "strong",
  "evidence_refs": ["goal-explain-current-estimate", "frame-1"],
  "rule_id": "goal-relevance.direct-effect.v1",
  "required_input_refs": ["scenario-1", "goal-explain-current-estimate"],
  "missing_input_keys": [],
  "conflict_refs": [],
  "rationale": "The question directly invokes the active explanation goal."
}
```

Allowed statuses:

```text
known
unknown
disputed
not_applicable
```

Allowed derivation strengths are `weak`, `moderate`, and `strong`; use `null`
when no derivation occurred. Strength is not `appraisal.certainty` and is not a
probability.

The exact dimension keys, types, and values are:

| Key | `value_type` | Allowed non-null values |
| --- | --- | --- |
| `appraisal.goal_relevance` | `unsigned_ordinal` | `low`, `moderate`, `high` |
| `appraisal.goal_congruence` | `signed_ordinal` | `negative`, `neutral`, `positive` |
| `appraisal.novelty` | `unsigned_ordinal` | `low`, `moderate`, `high` |
| `appraisal.certainty` | `unsigned_ordinal` | `low`, `moderate`, `high` |
| `appraisal.controllability` | `unsigned_ordinal` | `low`, `moderate`, `high` |
| `appraisal.norm_compatibility` | `signed_ordinal` | `negative`, `neutral`, `positive` |
| `appraisal.affiliative_value` | `signed_ordinal` | `negative`, `neutral`, `positive` |
| `appraisal.agency` | `agency_category` | `user`, `columbina`, `third_party`, `environment`, `unknown` |

Rules for nullability:

- `known` requires a non-null allowed value;
- `unknown` requires `value: null`, at least one `missing_input_key` or explicit
  evidence insufficiency in the rationale, and `rule_id: null` unless an
  unknown-producing rule is being tested;
- `disputed` uses `value: null`, at least two `conflict_refs`, and lists the
  supported alternatives in `rationale` or an optional `alternatives` array;
- `not_applicable` uses `value: null` and explains why the operational question
  does not apply to the scoped frame.

## Provisional V0 rule-id vocabulary

Use these identifiers where they match. If a required distinction cannot be
represented, add an `appraisal_questions` entry rather than silently changing a
rule's meaning.

```text
goal-relevance.direct-effect.v1
goal-relevance.indirect-effect.v1
goal-relevance.weak-relation.v1

goal-congruence.advances-goal.v1
goal-congruence.no-material-effect.v1
goal-congruence.obstructs-goal.v1

novelty.materially-unexpected.v1
novelty.known-type-changed-bindings.v1
novelty.expected-or-repeated.v1

certainty.grounded-unopposed.v1
certainty.leading-with-weaker-alternatives.v1
certainty.material-conflict-or-missing.v1

controllability.eligible-effective-response.v1
controllability.partial-progress-or-clarify.v1
controllability.no-effective-response.v1

norm-compatible.compatible.v1
norm-compatible.no-material-implication.v1
norm-compatible.conflict.v1

affiliative.cooperation-or-repair.v1
affiliative.no-material-social-implication.v1
affiliative.harm-or-boundary-conflict.v1

agency.semantic-runtime-grounding.v1
agency.credible-causal-conflict.v1
```

Rule ids name deterministic derivations. They do not replace evidence.

## Aggregate expectation contract

Every case must include:

```json
{
  "dominant_frame_ids": ["frame-scenario-1-goal-1-r1"],
  "dimension_ranges": {
    "appraisal.goal_congruence": ["negative", "neutral"]
  },
  "scenario_disagreement": "low",
  "goal_conflict": false,
  "high_consequence_uncertainty": false,
  "missing_required_inputs": []
}
```

Allowed disagreement values are `none`, `low`, `moderate`, and `high`.
`dimension_ranges` lists distinct supported values across retained frames; do
not invent numeric minima/maxima. Include all dimensions whose supported values
differ. Unknown and disputed values remain visible through
`missing_required_inputs` or frame conflicts.

The aggregate must not:

- drop a retained high-consequence frame;
- average negative and positive norm compatibility into neutral;
- convert unknown into a midpoint;
- choose one causal agent when agency is disputed; or
- combine appraisals across different goals without retaining goal conflict.

## Routing and downstream boundary

Allowed `expected_route.path` values are:

```text
fast
slow
clarify
abstain_or_escalate
```

Use:

- `fast` when the available frames are sufficiently complete and low-risk;
- `slow` when internal evidence retrieval, contradiction checking, or
  counterfactual simulation can resolve a decision-changing uncertainty;
- `clarify` when material information is owned by the user and cannot be
  recovered internally;
- `abstain_or_escalate` when the request is known but unsafe, unauthorized, or
  outside available capability and clarification cannot make it eligible.

`downstream_invariants` describe boundaries rather than numerical state output.
Examples:

```text
The state updater receives both retained appraisal frames.
High threat sensitivity may affect later activation magnitude but not this appraisal.
The action selector may propose clarification but receives no tool authorization.
The negative norm appraisal cannot be averaged away.
Partial controllability does not imply goal success.
```

Do not specify exact valence, activation, curiosity, control, social-alignment, or
state deltas. Those belong to the later dynamic-state design.

## Personality and relationship cases

Personality-focused cases hold the message, interpretation scenarios, goals,
runtime facts, and memory constant.

Required invariants:

1. High threat sensitivity does not change semantic roles, agency evidence, or
   norm compatibility.
2. High benign prior does not turn a negative runtime fact into positive goal
   congruence.
3. High self-attribution tendency does not resolve disputed agency without
   causal evidence.
4. High social affiliation does not create positive affiliative value without
   a compatible social interpretation.
5. High ambiguity tolerance preserves alternatives or affects routing; it does
   not manufacture appraisal certainty.
6. Personality may change later state sensitivity and action affinity, not the
   base appraisal result in these V0 cases.

Relationship context is optional and remains an input snapshot. Include at least
one case where valid relationship evidence changes an expectation about social
meaning and one where stale or conflicting relationship evidence is ignored.
Do not define the future relationship subsystem in this fixture.

## Reappraisal cases

Create at least four multi-step cases:

1. A fast initial appraisal is revised after trusted runtime evidence arrives.
2. Slow memory retrieval reduces novelty or resolves a contradiction.
3. User clarification resolves an unknown argument and changes controllability.
4. A later outcome observation revises the interpreted situation without
   rewriting the original frame.

Every later frame must:

- use a higher revision;
- reference the exact previous frame in `supersedes_frame_id`;
- identify the newly available inputs;
- rerun affected deterministic rules;
- preserve unaffected grounded fields unless new evidence bears on them; and
- leave the earlier frame immutable.

## Required coverage

The complete 24–30 case fixture must cover all of the following.

### Dimension contrasts

1. High, moderate, low, and unknown goal relevance.
2. Positive, neutral, negative, and unknown goal congruence.
3. High novelty with low relevance.
4. Low novelty with high relevance.
5. Known event type with changed bindings producing moderate novelty.
6. High certainty from grounded unopposed evidence.
7. Moderate certainty with a weaker retained alternative.
8. Low or disputed certainty with similarly supported alternatives.
9. High conversational controllability with low external controllability scoped
   to separate goals.
10. Moderate controllability through clarification or partial progress.
11. Low controllability with no effective eligible response.
12. Positive, neutral, negative, unknown, and disputed norm compatibility.
13. Positive, neutral, negative, unknown, and disputed affiliative value.
14. Every agency category plus disputed agency.

### Scenario and goal structure

15. One clear scenario and one goal.
16. Compatible multifunctional hypotheses in one scenario.
17. Literal and indirect alternatives requiring separate scenarios.
18. Literal and tone-mismatch alternatives requiring separate scenarios.
19. Quoted or hypothetical content that remains correctly scoped.
20. One scenario advancing one goal and obstructing another.
21. One event with separate conversational and external-world controllability.
22. A rejected hypothesis excluded from every retained scenario.

### Evidence and boundary cases

23. Missing active goal.
24. Missing or stale memory.
25. Trusted runtime fact contradicting memory.
26. Capability available but a required parameter missing.
27. Capability available but consent/authorization missing.
28. Known unsafe action producing negative norm compatibility while the hard
    gate remains a separate enforcement fact.
29. Positive wording with negative goal congruence.
30. Negative wording with neutral affiliative value.
31. Social repair with positive affiliative value after an explicit correction.
32. Ambiguous agency that personality cannot resolve.

### Routing and revision

33. At least two `fast` cases.
34. At least two `slow` cases with internally resolvable uncertainty.
35. At least two `clarify` cases with user-owned missing information.
36. At least one `abstain_or_escalate` case.
37. At least four reappraisal sequences as specified above.
38. At least one high-consequence alternative preserved outside the aggregate
    average.

One case may satisfy several numbered requirements.

## Forbidden inference examples

Every case must list at least one case-specific forbidden inference. Across the
fixture, include these failures:

- “The user used positive wording, so the event is goal-congruent.”
- “The speaker hedged, so Columbina's appraisal certainty must be low.”
- “The event is novel, so it is highly relevant.”
- “A tool exists, so the requested effect is controllable and authorized.”
- “The user caused the request, so the user caused the underlying failure.”
- “The user sounds negative, so affiliative value is negative.”
- “No relationship record means neutral rather than unknown social evidence.”
- “High threat sensitivity makes an ambiguous event norm-incompatible.”
- “One benign scenario makes a dangerous alternative safe.”
- “A later correction permits the original appraisal frame to be overwritten.”

## Review and validation procedure

After authoring the fixture:

1. Validate JSON syntax with a standard JSON parser.
2. Confirm unique `case_id`, message, hypothesis, semantic-frame, scenario,
   appraisal-frame, goal, memory, and evidence ids within their applicable
   scopes.
3. Verify all text spans exactly match the referenced message substring.
4. Verify every JSON Pointer resolves and the expected value matches.
5. Verify every scenario references only supplied compatible hypotheses and
   semantic frames.
6. Verify every appraisal frame references one supplied scenario and one supplied
   focal goal.
7. Verify all eight appraisal keys occur exactly once in every frame.
8. Verify statuses, value types, values, nullability, strength, and rule ids obey
   this brief.
9. Verify every evidence, required-input, missing-input, and conflict reference
   resolves.
10. Verify aggregates retain scenario disagreement, goal conflict, unknowns, and
    high-consequence alternatives.
11. Verify every later revision supersedes an existing earlier frame without
    changing it.
12. Verify route values and downstream invariants respect stage boundaries.
13. Verify personality never appears as evidence for a grounded appraisal fact.
14. Verify all required coverage items are represented and produce a coverage
    table in the task summary.
15. Run `git diff --check` and report the result.

Do not mark a case reviewed or approved. Human review occurs after fixture
creation.

## Required task summary

After creating the JSON file, report:

- output path;
- number of cases and reappraisal sequences;
- coverage by dimension, scenario contrast, status, agency category, and route;
- unresolved appraisal questions;
- any conflicts found among the source documents;
- JSON-validation and `git diff --check` results; and
- confirmation that no implementation, model, prompt, or database file changed.
