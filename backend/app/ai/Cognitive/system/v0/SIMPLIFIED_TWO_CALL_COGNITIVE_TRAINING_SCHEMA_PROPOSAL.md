# Simplified two-call cognitive training schema proposal

Status: design proposal for review; no runtime or training behavior is changed.

## 1. Purpose

V0 should temporarily use general LLMs to produce reviewed candidate cognitive
records that can later train a dedicated cognitive model. The trainable boundary
ends at selected Actions. Natural-language Realization is a separate problem and
is not part of the cognitive target proposed here.

The redesign must preserve the useful V0 concepts:

```text
Perception
  -> Interpretation
  -> Appraisal
  -> Dynamic State
  -> Goals and Requirements
  -> Action selection
  -> Realization (outside the cognitive training target)
```

It should remove protocol work that is not itself cognition: model-authored IDs,
temporary handle graphs, duplicated content descriptions, positional dimension
arrays, artifact patch construction in the cognitive call, and final response
wording in the action-selection output.

## 2. Core decisions

1. Keep the reviewed cue, predicate, hypothesis, appraisal, state, goal, and
   Action ontology keys.
2. Cognitive outputs contain no model-generated explanatory prose.
3. Open-world values are represented by code-owned source-span, entity, memory,
   artifact, runtime-fact, or canonical object references.
4. Code creates all durable IDs, digests, versions, and cross-stage records.
5. The model chooses typed meanings and policies; it does not reconstruct the
   compiler's internal objects.
6. Call 1 predicts Interpretation and Appraisal only.
7. Dynamic State, Goal lifecycle, Requirement resolution, safety, capability,
   and authorization remain deterministic and code-authoritative.
8. Call 2 selects an ordered Action plan from code-prepared eligible candidates.
9. Call 2 does not generate response segments, artifacts, claims, or wording.
10. Realization receives the accepted Action plan afterward and remains outside
    the cognitive training target.
11. Contract acceptance means structurally valid candidate data, not cognitive
    ground truth. Promotion to training data requires a separate quality status.

## 3. What is trainable

The eventual cognitive example has typed inputs and typed targets:

| Stage | Source in V0 | Candidate training role |
|---|---|---|
| Perception | deterministic detectors/runtime | input and optionally supervised target |
| Interpretation | Call 1 | supervised target |
| Appraisal | Call 1 | supervised target |
| Dynamic State transition | deterministic updater | derived supervised target |
| Goal transition | deterministic goal engine | derived supervised target |
| Requirement/readiness state | deterministic resolver | input to action policy and derived target |
| Action plan | Call 2 plus deterministic gates | supervised policy target after review |
| Realization wording | separate realization system | excluded from this cognitive dataset |

This separation prevents conversational prose quality from changing the meaning
of a cognition label. A separate dataset may later train Realization from an
accepted cognitive state and Action plan.

## 4. Reference policy

The model must not invent `o1`, `rd1`, `r25`, goal IDs, evidence IDs, move IDs,
or content IDs.

The input compiler exposes short, code-owned references with explicit types:

- `span:*` for exact source spans;
- `entity:*` for resolved dialogue entities;
- `memory:*` for authorized memory records;
- `runtime:*` for trusted runtime facts;
- `goal:*` for existing canonical goals;
- `object:*` for grounded workspace or dialogue objects;
- `candidate:*` for constructed Action candidates.

The model may return only supplied references. New canonical frame, appraisal,
state-transition, goal-transition, and move IDs are assigned deterministically
after validation.

Array position may identify a newly proposed frame inside one Call 1 response,
but it must not become a durable identity. Nested structures are preferred when
they remove the need for local cross-references.

## 5. Call 1: accepted input projection

Call 1 receives one bounded, versioned `UnderstandingInput`.

```json
{
  "focus": {
    "message_ref": "message:current",
    "authorized_span_refs": ["span:0", "span:1"]
  },
  "dialogue_context": {
    "message_refs": [],
    "entity_refs": [],
    "open_question_refs": [],
    "active_topic_refs": [],
    "omission_flags": []
  },
  "perception": {
    "cue_activations": [
      {
        "cue_key": "conversation.question_form",
        "state": "present",
        "value": true,
        "evidence_refs": ["span:0"]
      }
    ]
  },
  "context": {
    "persona_traits": {},
    "interpretation_biases": {},
    "relationship_state": {},
    "dynamic_state_before": {},
    "authorized_memory_refs": [],
    "existing_goals": []
  },
  "runtime": {
    "capability_refs": [],
    "trusted_fact_refs": [],
    "safety_state": null,
    "authorization_state": null
  },
  "ontology": {
    "predicate_registry_version": "...",
    "allowed_predicates": [],
    "hypothesis_registry_version": "...",
    "allowed_hypothesis_keys": [],
    "appraisal_schema_version": "..."
  }
}
```

### Call 1 input rules

- Dialogue text is exposed through bounded messages and exact span references.
- Perception supplies observations about surface form; it does not decide hidden
  meaning.
- Persona, biases, relationship, memory, and prior state may condition ambiguous
  readings and appraisal, but cannot overwrite explicit semantics.
- Existing goals are provided only for typed impact assessment. Future goals are
  not exposed and cannot be invented.
- Capability and permission may affect controllability, not the literal meaning
  of the user message.
- Unavailable context is explicit; it is never encoded as neutral.

## 6. Call 1: simplified output

Call 1 returns `UnderstandingProposalV1`.

```json
{
  "events": [
    {
      "predicate_key": "collaborative_content_request",
      "polarity": "positive",
      "arguments": {
        "operation": {"kind": "enum", "value": "create"},
        "artifact_type": {"kind": "enum", "value": "other"},
        "target_ref": {"kind": "null"},
        "constraints": {"kind": "span_set", "refs": ["span:1"]},
        "requested_form": {"kind": "null"},
        "outcome_certainty": {"kind": "enum", "value": "clear"},
        "embedding": {"kind": "enum", "value": "direct"}
      },
      "evidence_span_refs": ["span:0", "span:1"],
      "epistemic_status": "expressed",
      "readings": [
        {
          "hypothesis_key": "interpretation.goal.cause_action",
          "support": "strong",
          "uncertainty": "low",
          "context_refs": [],
          "appraisal": {
            "relevance": "high",
            "congruence": "positive",
            "novelty": "moderate",
            "certainty": "high",
            "controllability": "high",
            "norm_compatibility": "positive",
            "affiliative_value": "positive",
            "agency": "user"
          }
        }
      ]
    }
  ],
  "existing_goal_impacts": [
    {
      "goal_ref": "goal:response_obligation",
      "event_index": 0,
      "relevance": "high",
      "congruence": "positive",
      "uncertainty": "low"
    }
  ],
  "unknowns": [],
  "conflicts": []
}
```

### Call 1 output types

#### Event

- `predicate_key`: registered Interpretation predicate;
- `polarity`: `positive | negative | unknown`;
- `arguments`: exactly the registered argument names for that predicate;
- `evidence_span_refs`: supplied exact spans;
- `epistemic_status`: `expressed | user_claim | uncertain`;
- `readings`: zero or more registered interpretations of this event.

#### Typed argument value

Every predicate argument declares one allowed representation:

- `enum`: reviewed closed-vocabulary value;
- `span`: one supplied source span;
- `span_set`: one or more supplied spans;
- `entity_ref`: supplied resolved entity;
- `object_ref`: supplied grounded object;
- `runtime_ref`: supplied trusted runtime fact;
- `memory_ref`: supplied authorized memory record;
- `integer`, `decimal`, or `boolean`: only when the predicate definition permits it;
- `null`: only for an unresolved optional or explicitly nullable argument.

There is no unrestricted string argument. Code may normalize the content of a
span into a canonical value after the model has selected the span.

#### Reading

- `hypothesis_key`: registered speaker-goal or pragmatic-meaning key;
- `support`: existing reviewed support enum;
- `uncertainty`: `low | moderate | high`;
- `context_refs`: supplied typed context references that actually affected an
  ambiguous reading;
- `appraisal`: named dimensions, never an ordered array.

There is no `candidate_meaning` prose. The hypothesis key and its linked event
carry the meaning.

#### Unknown

```json
{
  "subject": {"event_index": 0, "argument_key": "target_ref"},
  "unknown_key": "interpretation.missing_target",
  "owner": "user",
  "evidence_refs": ["span:0"]
}
```

`unknown_key` is registered. There is no free-text `information` field.

#### Conflict

```json
{
  "left_ref": "span:0",
  "right_ref": "runtime:attachment_state",
  "conflict_key": "interpretation.claim_runtime_conflict",
  "status": "active"
}
```

There is no free-text conflict description.

## 7. Call 1 compilation

The deterministic compiler:

1. validates predicate and argument compatibility;
2. validates that every returned reference was supplied and authorized;
3. assigns canonical semantic-frame IDs;
4. builds Interpretation hypotheses and scenarios from registered keys;
5. creates named Appraisal dimensions and frames;
6. attaches evidence provenance;
7. produces the canonical Interpretation and Appraisal contracts already used
   by Dynamic State and Goal V0.

Compiler-generated IDs and provenance remain in durable traces, but are not
model targets.

## 8. Deterministic middle spine

The following stages remain code-owned after Call 1:

### Dynamic State

The updater consumes accepted Appraisal and produces typed transitions over the
existing keys:

- `state.valence`;
- `state.activation`;
- `state.epistemic_confidence`;
- `state.curiosity`;
- `state.engagement`;
- `state.goal_pressure`;
- `state.social_alignment`;
- `state.interaction_strain`.

The candidate training record stores compact deltas rather than the updater's
entire rule trace:

```json
{
  "state_key": "state.curiosity",
  "scope_ref": "unknown:target_ref",
  "before_band": "low",
  "after_band": "moderate",
  "direction": "increase",
  "source_refs": ["appraisal:0"]
}
```

### Goals

Goal formation, adoption, priority, satisfaction, blocking, and closure remain
deterministic. The compact candidate label records only canonical transitions:

```json
{
  "goal_definition_key": "interaction.fulfill_conversational_deliverable",
  "transition": "adopt",
  "target_refs": ["frame:0"],
  "priority": "moderate",
  "status_after": "active",
  "evidence_refs": ["frame:0"]
}
```

### Requirements and readiness

The resolver determines missing information, capability, authorization, safety,
consent, trusted content, and content-delivery readiness. These are not guessed
by Call 2. They become typed inputs to candidate construction and hard gates.

## 9. Call 2: accepted input projection

Call 2 receives `ActionPolicyInputV1` after the deterministic middle spine.

```json
{
  "accepted_understanding": {
    "frame_refs": [],
    "hypothesis_refs": [],
    "unknown_refs": [],
    "conflict_refs": []
  },
  "state_after": {},
  "active_goals": [],
  "goal_readiness": [],
  "personality": {},
  "relationship": {},
  "runtime_constraints": {
    "safety": {},
    "capabilities": [],
    "authorization": {},
    "consent": {}
  },
  "planning_conditions": [],
  "eligible_candidates": [
    {
      "candidate_ref": "candidate:0",
      "action_key": "give_feedback.acknowledge",
      "goal_refs": ["goal:0"],
      "target_refs": ["hypothesis:0"],
      "typed_parameters": {},
      "planned_effect_keys": [],
      "cost_band": "low",
      "risk_band": "low"
    }
  ],
  "max_moves": 3
}
```

### Call 2 input rules

- Code constructs candidates from the installed Action registry.
- Code binds targets and fills parameters wherever there is only one legal value.
- The model never recreates targets, goal bindings, effects, or realization
  constraints.
- Ineligible candidates are omitted or exposed only as diagnostics, never as
  selectable options.
- Candidate descriptions may be included in the prompt, but the output returns
  only candidate references and typed policy labels.
- Persona and relationship condition preference among safe eligible candidates;
  they cannot relax safety, evidence, capability, or authorization gates.

## 10. Call 2: simplified output

Call 2 returns `ActionPolicyProposalV1`.

```json
{
  "policy_mode": "contextual",
  "selected_candidate_refs": ["candidate:0", "candidate:3"],
  "primary_index": 1,
  "stop_after_selected": true,
  "selection_factors": [
    {
      "candidate_ref": "candidate:0",
      "factor_keys": ["action.uptake", "personality.warmth"],
      "source_refs": ["hypothesis:0", "persona:warmth"]
    },
    {
      "candidate_ref": "candidate:3",
      "factor_keys": ["goal.direct_progress", "personality.directness"],
      "source_refs": ["goal:0", "persona:directness"]
    }
  ]
}
```

### Call 2 output fields

- `policy_mode`: `direct | contextual | deliberative`;
- `selected_candidate_refs`: ordered subset of supplied eligible candidates;
- `primary_index`: index of the move that principally serves the focal goal;
- `stop_after_selected`: normally true; false is accepted only when the supplied
  planning contract explicitly allows another planning round;
- `selection_factors`: optional registered factor keys and supplied source refs.

There are no model-authored:

- Action keys;
- goal refs;
- target bindings;
- parameters outside supplied candidate variants;
- content operations;
- artifact patches;
- content claims or anchors;
- response segments;
- questions as prose;
- realization wording;
- rationales.

A clarification question is represented by selecting a prepared
`seek_information.*` candidate with a typed target and question-form parameter.
The Realization system later words it.

## 11. High-level reasoning without free-text reasoning traces

Higher reasoning ability comes from structured alternatives and planning, not a
long explanation field.

### Call 1 depth

- `direct`: one clear event/reading;
- `contextual`: multiple plausible readings or meaningful context conditioning;
- `deliberative`: conflicts, several retained alternatives, or consequential
  uncertainty.

The tier should normally be selected by a deterministic router from cues,
risk, ambiguity, correction state, and task complexity. It is an input policy,
not a model's unverified claim about how deeply it reasoned.

The same Call 1 schema supports all tiers. Higher tiers permit more readings,
conflicts, and goal-impact entries; they do not introduce prose.

### Call 2 depth

- `direct`: select one candidate and stop;
- `contextual`: select and order a small compatible move set;
- `deliberative`: compare multi-step eligible plans under typed goal coverage,
  cost, risk, uncertainty, personality, and relationship factors.

The same Action output remains valid at every tier. Complex reasoning increases
the number of structured alternatives considered, not the size of the target
vocabulary.

## 12. Realization boundary

Realization is explicitly separate:

```text
accepted Action plan
  + authorized content objects
  + persona voice profile
  + dialogue context
  -> natural-language candidate
  -> semantic/safety validation
```

Realization may use another LLM, a dedicated language model, or a future learned
decoder. Its text is not part of the cognition target defined here.

The cognitive record may link to the resulting response and its observed
outcome for evaluation, but those are labels about delivery and outcome, not
inputs retroactively allowed to change the cognitive decision.

## 13. Candidate training-record envelope

Provider traces should not be used directly as training examples. A separate
projection produces a stable record:

```json
{
  "schema_version": "cognitive-training-example-v1",
  "input": {
    "perception": {},
    "authorized_context": {},
    "persona": {},
    "relationship": {},
    "state_before": {},
    "goals_before": {}
  },
  "target": {
    "interpretation": {},
    "appraisal": {},
    "state_transitions": [],
    "goal_transitions": [],
    "requirements": [],
    "action_plan": {}
  },
  "evaluation": {
    "contract_status": "valid",
    "cognitive_review_status": "unreviewed",
    "behavior_review_status": "unreviewed",
    "outcome_status": "unavailable"
  },
  "provenance": {
    "ontology_versions": {},
    "model_provenance": {},
    "compiler_versions": {}
  }
}
```

The training projector removes transient provider handles, prompt text, compiler
diagnostics, retries, latency spans, persistence IDs, and Realization prose.

## 14. Training promotion rules

A record must not become a positive target merely because it compiled.

Required statuses are separate:

- `contract_status`: schema and reference validity;
- `cognitive_review_status`: correctness of Interpretation/Appraisal/State/Goals;
- `behavior_review_status`: suitability of the selected Action plan;
- `outcome_status`: observed result when available;
- `personality_fidelity_status`: whether personality influenced behavior in a
  coherent, non-caricatured way;
- `domain_label_status`: whether ontology and domain classifications are valid.

At minimum, positive training promotion requires reviewed cognitive and behavior
quality. Rejected records retain a typed failure category so schema failures,
reference failures, cognitive errors, policy errors, and realization errors are
not mixed into one negative class.

## 15. Personality and affinity

Personality is a typed conditioning input rather than a final style decoration.
It may affect:

- which ambiguous reading receives greater support;
- appraisal under genuinely ambiguous social context;
- curiosity and engagement state transitions;
- preference among several safe and goal-effective Actions;
- initiative, directness, playfulness, warmth, and disagreement choices.

It may not:

- contradict explicit user content;
- invent relationship history or affection;
- change safety, truth, authorization, or capability;
- force a question, acknowledgement, or social move on every turn.

Call 2's optional registered `selection_factors` make personality influence
inspectable without asking the model for a prose rationale.

## 16. Mapping from the current Call 1 schema

| Current field | Proposed treatment |
|---|---|
| `Observation.handle` | removed; array-local index only |
| `Observation.predicate` | retained as `predicate_key` |
| `Observation.arguments` | retained with explicit typed-value union |
| `Observation.evidence_handles` | supplied `evidence_span_refs` |
| `Observation.source_span` | represented by supplied span refs |
| `Reading.handle` | removed |
| `Reading.event_handles` | removed by nesting reading under event |
| `Reading.hypothesis_key` | retained |
| `Reading.candidate_meaning` | removed |
| `Reading.support/uncertainty` | retained |
| repeated reading evidence | inherited from event; context refs only when needed |
| eight-value appraisal vector | replaced by named fields |
| dimension exceptions by index | replaced by named nullable dimensions or per-field uncertainty if needed |
| `existing_goal_impacts` | retained with canonical supplied goal refs |
| free-text unknown information | replaced by registered unknown key and typed subject |
| free-text conflict information | replaced by registered conflict key and two typed refs |

## 17. Mapping from the current Call 2 schema

| Current field | Proposed treatment |
|---|---|
| `content_ops` | removed from cognitive call |
| collaboration patch operations | content/workspace subsystem or Realization input preparation |
| `moves.choice_handle/template_handle` | replaced by one prepared `candidate_ref` |
| model-authored goal handles | already bound inside candidate |
| model-authored target bindings | already bound inside candidate |
| `segment_handles` | removed |
| `segments[].text` | moved to Realization |
| `content_handles` and claim bindings | moved to content/Realization boundary |
| `inquiries` | represented by a selected `seek_information.*` candidate |
| duplicated response candidate | removed |
| move ordering | retained as ordered candidate refs |
| primary move | retained as `primary_index` |
| stop decision | retained explicitly |

## 18. Validation boundary

### Hard validation

- registered ontology keys;
- correct predicate arguments and value kinds;
- supplied and authorized references only;
- valid enum/numeric ranges;
- evidence-span containment;
- no contradiction of trusted runtime facts;
- Action candidate eligibility, safety, capability, authorization, consent, and
  target compatibility;
- plan ordering and maximum length;
- no duplicate incompatible Actions.

### Quality review, not hard structural rejection

- naturalness of eventual wording;
- whether an optional social move was desirable;
- warmth or playfulness strength;
- whether one of several safe plans was preferable;
- whether the answer felt sufficiently helpful;
- stylistic length absent a true product limit.

This prevents harmless conversational variation from invalidating otherwise
sound cognition.

## 19. Expected reductions

The proposal removes from model output:

- all model-authored local IDs and most cross-object handles;
- free-text candidate meanings, unknown descriptions, conflict descriptions,
  rationales, and response text;
- positional appraisal arrays;
- target-binding reconstruction;
- content/artifact patch construction;
- segment-to-move and claim-to-content graphs;
- duplicate response representations;
- Realization validation concerns from the cognitive contract.

The model retains responsibility for the decisions that are useful to learn:

- semantic predicate and typed argument selection;
- alternative interpretation selection and uncertainty;
- event appraisal;
- impact on existing goals;
- selection and ordering of eligible Actions;
- stop behavior;
- personality-conditioned preference among valid Actions.

## 20. Open review decisions

Before implementation, reviewers should decide:

1. whether Perception remains fully deterministic or becomes an additional
   supervised target for a future unified cognitive model;
2. whether appraisal needs per-dimension uncertainty or one uncertainty value
   per reading;
3. whether `policy_mode` is a deterministic input, an output label, or omitted;
4. whether Action selection needs one call for the whole ordered plan or bounded
   iterative selection using the same schema;
5. where non-linguistic content planning belongs when an Action truly requires
   an artifact;
6. what independent review process promotes candidate records to training data;
7. which existing ontology keys should be retained, merged, deprecated, or
   expanded for general conversation before any new corpus is produced.

## 21. Bottom line

The recommended simplification preserves the deterministic V0 cognitive model
but makes the LLM boundary predict only durable cognitive choices. Call 1 emits
typed Interpretation and named Appraisal. Code derives State, Goals, and
Readiness. Call 2 selects an ordered plan from grounded eligible Actions.
Realization is separate and excluded from the cognition-training target.

This design can support both simple conversation and higher-level reasoning
without treating compiler plumbing, artifact formatting, or natural-language
wording as cognition labels.
