# Context-conditioned two-call pipeline redesign

Date: 2026-10-05  
Status: proposed implementation handoff; not implemented or benchmarked  
Scope: Cognitive V0 shadow only; no live-response, tool, memory-write, or
training authority

This document supersedes the earlier version of this file. The complete
normative contracts, mappings, training envelope, and design rationale are in
[`SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md`](SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md).
Implementers must treat that document and this handoff as one specification.

## 1. Required architecture

```text
deterministic Perception
  -> Call 1: typed Interpretation and named Appraisal
  -> deterministic Dynamic State, Goals, and Requirements
  -> Call 2: grounded Action-policy selection
  -> deterministic Action gates and plan compilation
  -> separate Realization
```

The trainable cognitive boundary ends at selected Actions. Natural-language
Realization is a separate system and is not part of the cognitive training
target.

## 2. Non-negotiable decisions

1. Preserve reviewed cue, predicate, hypothesis, Appraisal, state, Goal,
   Requirement, and Action ontology keys.
2. Cognitive model outputs contain no model-generated explanatory prose.
3. Open-world values use exact source spans or supplied canonical references.
4. Code owns durable IDs, versions, digests, provenance, and canonical records.
5. Call 1 predicts Interpretation and Appraisal only.
6. Dynamic State, Goal lifecycle, Requirement resolution, safety, capability,
   authorization, and consent remain deterministic and authoritative.
7. Call 2 selects and orders code-prepared eligible Action candidates.
8. Call 2 does not generate artifact patches, content claims, response segments,
   questions as prose, or final wording.
9. Realization is separate and excluded from the cognition-training target.
10. Structural acceptance is not a positive training label.
11. Shadow isolation remains enabled through implementation and evaluation.

## 3. Reference policy

The model must not invent observation, reading, evidence, Goal, content, move,
or artifact handles. Input compilers expose short code-owned references:

- `span:*` — exact authorized message span;
- `entity:*` — resolved dialogue entity;
- `memory:*` — authorized memory record;
- `runtime:*` — trusted runtime fact;
- `goal:*` — existing canonical Goal;
- `object:*` — grounded dialogue/workspace object;
- `candidate:*` — code-constructed Action candidate;
- `persona:*` and `relationship:*` — supplied conditioning dimensions.

The model may return only supplied references. Code assigns canonical IDs after
validation.

## 4. Call 1 responsibility

Call 1 answers:

```text
What semantically happened?
What registered readings are plausible?
How does this agent appraise those readings?
How do they affect Goals that already exist?
What registered unknowns or conflicts are present?
```

It does not create state, Goals, Requirements, Actions, content, or wording.

## 5. Call 1 input

`UnderstandingInputV1` contains:

```ts
type UnderstandingInputV1 = {
  schema_version: "understanding-input-v1";
  focus: {
    message_ref: SuppliedMessageRef;
    authorized_span_refs: SuppliedSpanRef[];
  };
  dialogue_context: {
    message_refs: SuppliedMessageRef[];
    entity_refs: SuppliedEntityRef[];
    open_question_refs: SuppliedObjectRef[];
    active_topic_refs: SuppliedObjectRef[];
    omission_flags: RegisteredOmissionKey[];
  };
  perception: { cue_activations: CueActivationView[] };
  context: {
    persona_traits: PersonaTraitView[];
    interpretation_biases: InterpretationBiasView[];
    relationship_state: RelationshipDimensionView[];
    dynamic_state_before: DynamicStateView[];
    authorized_memory_refs: SuppliedMemoryRef[];
    existing_goals: ExistingGoalView[];
  };
  runtime: {
    capability_refs: SuppliedCapabilityRef[];
    trusted_fact_refs: SuppliedRuntimeFactRef[];
    safety_state: SafetyStateView | null;
    authorization_state: AuthorizationStateView | null;
  };
  ontology: {
    predicate_registry_version: string;
    predicates: PredicateDefinitionView[];
    hypothesis_registry_version: string;
    hypothesis_keys: RegisteredHypothesisKey[];
    unknown_keys: RegisteredUnknownKey[];
    conflict_keys: RegisteredConflictKey[];
    appraisal_schema_version: string;
  };
};
```

Persona, relationship, memory, biases, and prior state may condition ambiguous
readings and Appraisal but cannot overwrite explicit semantics. Capability and
permission may affect controllability, not literal meaning.

## 6. Call 1 output

```ts
type UnderstandingProposalV1 = {
  schema_version: "understanding-proposal-v1";
  events: SemanticEvent[]; // min 1
  existing_goal_impacts: ExistingGoalImpact[];
  unknowns: UnknownRecord[];
  conflicts: ConflictRecord[];
};

type SemanticEvent = {
  predicate_key: RegisteredPredicateKey;
  polarity: "positive" | "negative" | "unknown";
  arguments: ArgumentBinding[];
  evidence_span_refs: SuppliedSpanRef[]; // min 1, unique
  readings: InterpretationReading[];
};

type ArgumentBinding = {
  argument_key: RegisteredArgumentKey;
  value: TypedArgumentValue;
};

type TypedArgumentValue =
  | { kind: "enum"; value: RegisteredArgumentValue }
  | { kind: "span"; ref: SuppliedSpanRef }
  | { kind: "span_set"; refs: SuppliedSpanRef[] }
  | { kind: "entity_ref"; ref: SuppliedEntityRef }
  | { kind: "object_ref"; ref: SuppliedObjectRef }
  | { kind: "runtime_ref"; ref: SuppliedRuntimeFactRef }
  | { kind: "memory_ref"; ref: SuppliedMemoryRef }
  | { kind: "integer"; value: number }
  | { kind: "decimal"; value: number }
  | { kind: "boolean"; value: boolean }
  | { kind: "null" };

type InterpretationReading = {
  hypothesis_key: RegisteredHypothesisKey;
  uncertainty: "low" | "moderate" | "high";
  context_refs: SuppliedContextRef[];
  appraisal: EventAppraisal;
};

type EventAppraisal = {
  uncertainty: "low" | "moderate" | "high";
  relevance: "low" | "moderate" | "high" | "not_applicable" | null;
  congruence: "negative" | "neutral" | "positive" | "not_applicable" | null;
  novelty: "low" | "moderate" | "high" | "not_applicable" | null;
  certainty: "low" | "moderate" | "high" | "not_applicable" | null;
  controllability: "low" | "moderate" | "high" | "not_applicable" | null;
  norm_compatibility: "negative" | "neutral" | "positive" | "not_applicable" | null;
  affiliative_value: "negative" | "neutral" | "positive" | "not_applicable" | null;
  agency: "user" | "columbina" | "third_party" | "environment" |
          "unknown" | "not_applicable" | null;
};

type ExistingGoalImpact = {
  goal_ref: SuppliedExistingGoalRef;
  event_index: number;
  reading_index: number | null;
  relevance: "low" | "moderate" | "high" | null;
  congruence: "negative" | "neutral" | "positive" | null;
  uncertainty: "low" | "moderate" | "high";
};

type CognitiveSubjectPointer = {
  event_index: number;
  reading_index: number | null;
  argument_key: RegisteredArgumentKey | null;
};

type UnknownRecord = {
  subject: CognitiveSubjectPointer;
  unknown_key: RegisteredUnknownKey;
  owner: "user" | "runtime" | "system" | "unknown";
  evidence_refs: SuppliedEvidenceRef[];
};

type ConflictRecord = {
  left_ref: SuppliedEvidenceRef;
  right_ref: SuppliedEvidenceRef;
  conflict_key: RegisteredConflictKey;
  status: "active";
};
```

The provider output has no unrestricted string values. Predicates dynamically
narrow legal argument keys, value kinds, enum values, and nullability.

The compiler derives common epistemic status from predicate class,
modality/embedding, source, and uncertainty. It derives canonical hypothesis
support from uncertainty, evidence coverage, and registered policy. A new field
requires a new schema version if evaluation proves either derivation inadequate.

`null` Appraisal means unassessed; `not_applicable` means the dimension does not
apply. Named dimensions replace the positional array and numeric exceptions.

## 7. Call 1 compiler

The deterministic compiler:

1. validates installed predicates and hypothesis keys;
2. validates predicate-specific arguments and value kinds;
3. validates supplied/authorized references and evidence-span containment;
4. derives epistemic status and canonical support under versioned policy;
5. assigns canonical Semantic Frame and Interpretation Hypothesis IDs;
6. creates named Appraisal dimensions and frames;
7. emits existing canonical Interpretation/Appraisal contracts.

Compiler IDs and diagnostics are durable traces, not model targets.

## 8. Deterministic middle spine

Keep the existing Dynamic State keys and updater, Goal lifecycle, Requirement
resolution, safety, capability, authorization, consent, and readiness. Project
compact state and Goal transitions for candidate training data, but do not ask
Call 1 or Call 2 to author them.

```ts
type StateTransitionLabel = {
  state_key: RegisteredStateKey;
  scope_ref: CanonicalCognitiveRef;
  before_band: RegisteredStateBand;
  after_band: RegisteredStateBand;
  direction: "increase" | "decrease" | "unchanged" |
             "no_contribution" | "branch_dependent";
  source_refs: CanonicalCognitiveRef[];
};

type GoalTransitionLabel = {
  goal_definition_key: RegisteredGoalDefinitionKey;
  transition: RegisteredGoalTransitionKind;
  target_refs: CanonicalCognitiveRef[];
  priority: RegisteredGoalPriorityBand;
  status_after: RegisteredGoalStatus;
  evidence_refs: CanonicalCognitiveRef[];
};
```

## 9. Call 2 responsibility

Call 2 chooses which grounded eligible Actions to take, their order, which is
primary, and when to stop. It does not create Goals, targets, parameters,
artifacts, content, or language.

## 10. Call 2 input

```ts
type ActionPolicyInputV1 = {
  schema_version: "action-policy-input-v1";
  accepted_understanding: {
    frame_refs: CanonicalFrameRef[];
    hypothesis_refs: CanonicalHypothesisRef[];
    unknown_refs: CanonicalUnknownRef[];
    conflict_refs: CanonicalConflictRef[];
  };
  state_after: DynamicStateView[];
  active_goals: ActiveGoalView[];
  goal_readiness: GoalReadinessView[];
  personality: PersonaTraitView[];
  relationship: RelationshipDimensionView[];
  runtime_constraints: {
    safety: SafetyStateView;
    capabilities: CapabilityStateView[];
    authorization: AuthorizationStateView;
    consent: ConsentStateView;
  };
  planning_conditions: PlanningConditionView[];
  eligible_candidates: EligibleActionCandidate[];
  policy_mode: "direct" | "contextual" | "deliberative";
  max_moves: number;
};

type EligibleActionCandidate = {
  candidate_ref: SuppliedCandidateRef;
  action_key: RegisteredActionKey;
  goal_refs: CanonicalGoalRef[];
  target_refs: CanonicalTargetRef[];
  typed_parameters: TypedActionParameter[];
  planned_effect_keys: RegisteredEffectKey[];
  addressed_condition_keys: RegisteredPlanningConditionKey[];
  cost_band: "low" | "moderate" | "high";
  risk_band: "low" | "moderate" | "high";
};
```

Code constructs candidates, binds targets, and fills parameters wherever only
one legal value exists. Persona and relationship may change preference among
valid candidates, never hard eligibility.

## 11. Call 2 output

```ts
type ActionPolicyProposalV1 = {
  schema_version: "action-policy-proposal-v1";
  selected_candidate_refs: SuppliedCandidateRef[];
  primary_index: number | null;
  stop_after_selected: boolean;
  selection_factors: CandidateSelectionFactors[];
};

type CandidateSelectionFactors = {
  candidate_ref: SuppliedCandidateRef;
  factor_keys: RegisteredSelectionFactorKey[];
  source_refs: SuppliedPolicySourceRef[];
};
```

Selections are unique, ordered, and bounded by `max_moves`. `primary_index` is
null exactly for an empty legal plan. Selection factors are registered and
optional, never prose rationales. `stop_after_selected=false` is allowed only
under an explicitly bounded iterative-planning contract.

A clarification is a prepared `seek_information.*` candidate with typed target
and question-form parameters. Realization later words it.

## 12. Reasoning depth

Do not add a free-text reasoning trace. A deterministic router supplies:

- `direct` for one clear reading or obvious Action;
- `contextual` for meaningful contextual ambiguity or a short move sequence;
- `deliberative` for conflicts, consequential uncertainty, or competing plans.

The same schemas apply at every depth. Higher modes permit more structured
alternatives and plan combinations, not new prose fields.

## 13. Realization boundary

```text
accepted Action plan
  + authorized content objects
  + persona voice profile
  + dialogue context
  -> natural-language candidate
  -> semantic and safety validation
```

Realization may use another LLM, a dedicated language model, or a future
decoder. Its text is excluded from this cognition-training target. Cognitive
completion remains durable if Realization rejects or fails.

## 14. Candidate training record

Raw provider captures are never direct training examples. Project canonical
records into:

```ts
type CognitiveTrainingExampleV1 = {
  schema_version: "cognitive-training-example-v1";
  input: {
    perception: PerceptionTrainingView;
    authorized_context: AuthorizedContextTrainingView;
    persona: PersonaTrainingView;
    relationship: RelationshipTrainingView;
    state_before: DynamicStateTrainingView;
    goals_before: GoalTrainingView;
  };
  target: {
    interpretation: InterpretationTrainingTarget;
    appraisal: AppraisalTrainingTarget;
    state_transitions: StateTransitionLabel[];
    goal_transitions: GoalTransitionLabel[];
    requirements: RequirementTrainingLabel[];
    action_plan: ActionPolicyTrainingTarget;
  };
  evaluation: {
    contract_status: "valid" | "invalid";
    cognitive_review_status: ReviewStatus;
    behavior_review_status: ReviewStatus;
    personality_fidelity_status: ReviewStatus;
    domain_label_status: ReviewStatus;
    outcome_status: OutcomeReviewStatus;
  };
  provenance: {
    ontology_versions: Record<string, string>;
    model_provenance: ModelProvenance;
    compiler_versions: Record<string, string>;
  };
};
```

The projector removes prompt text, transient references, retries, latency spans,
diagnostics, persistence IDs, and Realization prose.

Contract validity is necessary but insufficient. Positive promotion requires
independent review of cognition, Action quality, personality fidelity, domain
labels, and outcomes where available. Multiple acceptable readings/plans must
be representable.

## 15. Validation boundary

Hard gates remain for ontology membership, typed arguments, authorized refs,
numeric/enum ranges, span containment, trusted-runtime contradictions, Action
eligibility, safety, evidence, capability, authorization, consent, target
compatibility, plan order, and maximum length.

Naturalness, optional social moves, warmth/playfulness strength, preference
among several safe plans, and stylistic length are quality-review or Realization
concerns, not cognitive structural failures.

## 16. Implementation sequence

### Phase A — baseline

Keep `context-conditioned-two-call-v0.1` replayable. Freeze the current general-
conversation corpus and baseline input/output tokens, provider latency, compile
time, failure stage, acceptance, and wording quality.

### Phase B — versioned contracts

Add the four V1 contracts and dynamically narrowed strict JSON Schemas. Do not
mutate frozen replay behavior in place.

### Phase C — adapters

Compile Call 1 into existing canonical Interpretation/Appraisal, reuse the
deterministic middle, construct fully bound eligible candidates, and compile
Call 2 selections into existing Action plans with authoritative gates.

### Phase D — Realization separation

Remove segments/wording from Call 2 and invoke the separately versioned
Realization path after accepted Action planning.

### Phase E — training projection

Add the candidate projection and separate review statuses. Do not enable
training, export, or extended retention.

### Phase F — real shadow evaluation

Use the real WebSocket -> persistence -> fresh worker -> shadow path. Rerun the
13-turn general-conversation corpus plus an uninterrupted 8–10-turn dialogue.
Review actual labels, Actions, wording, latency, retries, failures, and outcomes.

## 17. Likely implementation surfaces

```text
backend/app/ai/Cognitive/system/v0/two_call/compact_contracts.py
backend/app/ai/Cognitive/system/v0/two_call/compact_provider.py
backend/app/ai/Cognitive/system/v0/two_call/compact.py
backend/app/ai/Cognitive/system/v0/two_call/compact_runtime.py
backend/app/ai/Cognitive/system/v0/two_call/orchestrator.py
backend/app/ai/Cognitive/system/v0/two_call/provider.py
backend/app/ai/Cognitive/system/v0/two_call/realization.py
backend/app/ai/Cognitive/system/v0/live_shadow/realization.py
backend/app/ai/Cognitive/system/v0/trace_summary.py
backend/app/core/config.py
backend/app/services/cognitive_shadow.py
backend/app/workers/cognitive_shadow.py
```

Implementers must confirm the inventory before editing. New versioned modules
are preferred over mutating old trace semantics.

## 18. Required tests

- every exposed predicate, hypothesis, and argument value kind;
- mixed clause-local acts and exact source spans;
- negated, quoted, reported, hypothetical, corrected, and conflicting content;
- user claims versus trusted runtime facts;
- persona/relationship conditioning without invented facts or affinity;
- null arguments, registered unknowns, and typed conflicts;
- named Appraisal and uncertainty validation;
- canonical Interpretation/Appraisal adapter parity;
- deterministic state, Goal, and readiness authority;
- only supplied eligible Action candidates selectable;
- candidate ordering, uniqueness, primary, maximum moves, and stop behavior;
- clarification represented as Action rather than prose;
- hard gates cannot be overridden by personality or model choice;
- no content, artifact, segment, wording, or prose rationale in Call 2;
- complex multi-step plans remain representable;
- cognitive completion survives Realization failure;
- old and new trace replay by exact version;
- training candidate status never implies review or promotion.

## 19. Acceptance criteria

Implementation is complete only when:

1. both new model outputs contain no unrestricted generated prose;
2. no model-authored durable IDs or target-binding graphs remain;
3. named Appraisal replaces positional output on the new route;
4. deterministic State, Goals, Requirements, and hard Action gates remain
   authoritative;
5. Call 2 returns only candidate selection/order/primary/stop plus registered
   selection factors;
6. Realization is separate and independently measured;
7. old traces remain replayable;
8. trace summaries separate every provider/compiler/middle/gate/Realization/
   persistence duration;
9. the general-conversation corpus improves cognitive completion and Action
   quality without increasing unsafe or ungrounded behavior;
10. candidate training records remain unreviewed until independent promotion.

## 20. Open decisions

1. Whether Perception is only input or later a supervised target.
2. Whether Appraisal eventually needs per-dimension uncertainty.
3. Exact uncertainty/evidence-to-support derivation policy.
4. Whole-plan versus bounded iterative Action selection.
5. Where true structured artifact content planning belongs.
6. Ontology keys to expand, merge, or retire before collection.
7. Independent adjudication and training-promotion process.

## 21. Bottom line

Call 1 predicts typed Interpretation and named Appraisal. Deterministic code
derives State, Goals, and Readiness. Call 2 selects an ordered plan from fully
grounded eligible Actions. Realization is separate and excluded from the
cognitive training target.

This preserves V0's cognitive concepts while removing protocol plumbing that
currently consumes latency, creates brittle failures, and would contaminate
future cognitive-model training data.
