# Action Selection V0 implementation brief

Status: implementation-accepted for deterministic shadow V0 on 2026-09-26;
not behaviorally calibrated, live-approved, or live-integrated

This document instructs an implementation agent how to build Action Selection
V0 and connect it to the canonical cognitive pipeline. Read it completely before
editing code. Action Selection is not complete when only contracts, a registry,
or a standalone selector exists; completion means the exact current outputs of
Interpretation, Goal, Appraisal, and Dynamic State feed one fail-closed action
stage retained by Cognitive Turn and Pipeline traces.

The implementation is shadow-only. It must not change live chat, generate final
wording, call a model, invoke tools, write memories, persist traces, update goals
from planned effects, or claim that slow cognition or learning exists.

## Historical independent conformance audit: acceptance blockers

An independent implementation audit was completed on 2026-09-25. At that time,
the selector was implemented, connected to Cognitive Turn and Pipeline V0,
deterministic, and shadow-only. The focused action suite passed 218 tests; the
fixture adapter ran
29 cases, 81 planning steps, 7 failure variants, and 58 invariance checks with
no reported errors. The complete backend suite passed 532 tests with 5 skips,
and `git diff --check` passed.

Those results established implementation and regression coverage, but did not
yet satisfy the final independent-review requirement in section 31. Acceptance
was withheld until both blockers below were fixed and covered by regression
tests. The later remediation and successful re-audit are recorded below.

### Blocker A: required evidence is not enforced by the hard gate

Action definitions declare `evidence_requirements`, and section 13 requires the
schema/reference gate to validate evidence. The pre-remediation gate checked
that each
evidence reference supplied by a candidate resolves and has the recorded source
kind, but it does not establish that:

- every evidence requirement declared by the definition is satisfied;
- the required evidence roles and cardinalities are present;
- the cited evidence is authorized and current; or
- the evidence belongs to the current turn or an explicitly permitted prior
  context.

The audit reproduced the gap by removing every `supporting_evidence_ref` from
otherwise valid `provide_information.correct`, `give_feedback.acknowledge`, and
`support_socially.reassure` candidates. Each forged candidate remained
`shadow_only` with no gate violation, despite its definition declaring required
evidence.

Required remediation:

1. Replace or supplement descriptive evidence-requirement strings with an
   enforceable typed requirement specification containing role, accepted source
   kinds, cardinality, and applicable authorization/freshness rules.
2. Validate required evidence in the schema/reference hard-gate category; do
   not rely solely on the trusted constructor to have populated it.
3. Reject unresolved, mistyped, unauthorized, superseded, or temporally invalid
   evidence with stable violation codes.
4. Add tests that remove required evidence, change its role/source kind, cite an
   unauthorized or superseded source, and cite a source outside the permitted
   turn boundary.
5. Prove that an evidence-deficient candidate cannot be scored or selected.

### Blocker B: the application safety decision is not versioned or closed

Sections 13 and 20 require a versioned application safety decision to be
consumed and retained in the gate trace. The pre-remediation runtime projection
accepted
`safety_decision: str | None`; arbitrary or missing values are accepted, and
only a small recognized string set produces a block. `ActionGateResult` retains
a decision reference but no safety-policy or decision-schema version.

This was not a live safety exposure because all definitions remained
shadow-only, but it was a conformance failure that had to be corrected before
any action could be promoted to `approved`.

Required remediation:

1. Introduce a closed typed safety-decision contract with explicit `allow`,
   `block_commitments`, `block_response`, `unknown`, and `unavailable`
   semantics, or an equivalently reviewed vocabulary.
2. Carry the safety-policy version, decision-schema version, decision reference,
   and trusted provenance through the Pipeline runtime projection,
   `ActionRuntimeContext`, every gate result, and `ActionVersions` or another
   canonical version envelope.
3. Define and test fail-closed behavior for operational commitments when a
   required safety decision is missing, unknown, unrecognized, or produced by
   an unsupported policy version.
4. Prove that score, state, personality, goals, and prior outcomes cannot
   override any blocking decision.
5. Keep every definition shadow-only until this contract is complete and the
   independent audit is rerun.

### Remediation record (2026-09-25; independently re-audited 2026-09-26)

Blocker A — typed evidence requirements:

- `ActionEvidenceRequirement` (`contracts/actions.py`) states role, sorted
  accepted source kinds, `minimum`/`maximum`, `authorized_only`,
  `current_revision_only`, explicit turn boundaries (`current_turn`,
  `prior_record`, validated against the kinds), and which reviewed descriptive
  requirements it `satisfies`. Goal, appraisal, dynamic-state, and
  partial-plan references can never be accepted as evidence.
- `ActionDefinition.evidence_specification` is required; every definition needs
  at least one requirement with `minimum >= 1`, and every descriptive
  `evidence_requirements` name is bound to exactly one typed role. All 39
  shadow definitions have reviewed specifications
  (`registry.EVIDENCE_SPECIFICATIONS`); non-shadow definitions require target
  grounding. The constructor refuses a manifest whose recipes cite undeclared
  roles or cannot bind a required role (engine phase `registry_resolution`).
- The schema/reference gate enforces the specification itself with stable
  codes: `evidence_does_not_resolve`, `evidence_source_kind_mismatch`,
  `evidence_role_not_declared`, `evidence_source_kind_not_accepted`,
  `evidence_not_authorized`, `evidence_superseded`, `evidence_deleted`,
  `evidence_not_current` (for example a rejected hypothesis),
  `evidence_outside_turn_boundary`, `required_evidence_missing`, and
  `evidence_cardinality_exceeded`. Rejected citations never count toward a
  requirement.
- Trace validation rejects a step that records a candidate as eligible while
  its evidence violates its definition's specification.
- `tests/test_action_v0_evidence_requirements.py` reproduces the audit
  (removing all evidence from `correct`, `acknowledge`, and `reassure`) and
  covers each code, removal of each required role, a relabelled role, a
  mislabelled and an unaccepted source kind, unauthorized/superseded/deleted
  and out-of-turn sources, a prior record cited by a current-turn requirement
  and a prior record claiming the current turn, cardinality maxima, and proves
  an evidence-deficient candidate is neither scored nor selected nor recordable
  as eligible. With the correct constructor, every design-case candidate
  satisfies its specification: no gate outcome in the 29 cases changed.

Blocker B — closed, versioned application safety decision:

- `contracts/safety.py` defines `ApplicationSafetyDecision`
  (`application-safety-decision-v0.1`): `decision_id`, `policy_version`,
  closed `disposition` (`allow`, `block_commitments`, `block_response`,
  `unknown`, `unavailable`), closed `provenance`
  (`application_safety_policy`, or `runtime_unavailable` which may carry only
  `unavailable`), `producer_ref`, and `runtime_snapshot_ref`. Only the trusted
  policy can allow or block. Unrecognized dispositions, provenance, schema
  versions, and extra fields are rejected at validation.
- `safety_restriction()` is the one pure mapping: commitments are restricted
  when the decision is missing, `unknown`, `unavailable`, `block_commitments`,
  or from a policy version outside `SUPPORTED_SAFETY_POLICY_VERSIONS`;
  `block_response` restricts every move under any policy version.
- The Pipeline runtime snapshot carries the typed decision (it must belong to
  that snapshot); `to_action_runtime()` passes it unchanged into
  `ActionRuntimeContext` (same check). Interpretation, Goal, and Appraisal keep
  their string input through the deterministic `legacy_safety_label`
  projection, and Cognitive Turn rejects an action decision whose projection
  differs from the upstream string.
- Every `ActionGateResult` and the `ActionSelectionResult` carry the same
  `ActionSafetyBasis` (decision reference, schema version, policy version,
  disposition, provenance, producer, restriction, reason — restriction
  recomputed on validation). `ActionVersions` adds `safety_decision_schema` and
  `safety_policy`. Trace validation rejects a restricted candidate recorded
  without its safety violation, and a gate result citing a different decision.
- Violation codes: `application_safety_blocks_response`,
  `application_safety_blocks_operation`, `application_safety_decision_missing`,
  `application_safety_decision_unknown`,
  `application_safety_decision_unavailable`, and
  `application_safety_policy_version_unsupported`.
- The 29 schema-v2 design cases now carry an explicit trusted `allow` (the
  schema-v1 fixture records no block); the failure variant uses a typed
  `block_response`. `tests/test_action_v0_safety_decision.py` covers the closed
  vocabulary, the total restriction table, every fail-closed path, retention in
  gates/result/versions, trace forgery, Pipeline and Cognitive Turn threading,
  and proves (by spying on feature extraction and scoring with a maximal known
  personality) that no downstream influence ever reaches a blocked commitment.
- Every definition remains shadow-only.

Verification after remediation: the focused action suite passes 269 tests;
the fixture adapter runs 29 cases, 81 planning steps, 7 failure variants, and
58 invariance checks with no errors, and every case's plan and per-step
projection is unchanged (only the version envelope and trace digests changed,
so `conversational_action_design_results_v0.json` was regenerated after that
comparison); the complete backend suite passes 583 tests with 5 skips; and
`git diff --check` passes.

### Independent re-audit result (2026-09-26)

The required independent re-audit reran the focused Action suite, the fixture
adapter, the complete backend suite, and the repository diff check. It also
re-inspected the typed evidence gate, the closed application-safety decision,
the `STOP` and no-viable-plan boundaries, failure propagation, and the exact
Cognitive Turn/Pipeline handoff.

Results:

- 269 focused Action tests passed;
- all 29 design cases executed across 81 planning steps, 7 failure variants,
  and 58 invariance checks with 0 errors;
- 583 backend tests passed with 5 skips; and
- `git diff --check` passed.

Both former conformance blockers are closed. No new blocker was found for the
defined deterministic shadow boundary. Action Selection V0 is therefore
**implementation-accepted for deterministic shadow V0**. This acceptance does
not approve behavioral calibration, language realization, tool execution,
live response influence, trace persistence, outcome evaluation, or learning.

### Non-blocking follow-up before registry evolution

Trace validation currently resolves definitions through `DEFAULT_REGISTRY`
even though `ActionSelectionEngine` accepts an injected `ActionRegistry`. This
is acceptable for the fixed V0 installed registry, but the mismatch must be
removed before supporting another registry version. Use a registry-aware trace
validator or retain a canonical definition/registry digest rather than reading
process-global registry state from contract validators.

The fixed V0 registry may now be used by an observational live-shadow trace.
Behavioral calibration and live approval remain separate later decisions. The
registry-aware validation follow-up above must be completed before registry
evolution, not before fixed-registry shadow observation.

## 1. Required reading and authority order

Read these files completely before implementation:

1. `ACTION_SELECTION_V0_DESIGN.md` — final design decisions and scope;
2. `README.md` — action semantics, hard gates, feature contract, scoring formula,
   sequential policy, `STOP`, realization boundary, and system roadmap;
3. `data/CONVERSATIONAL_ACTION_REGISTRY_V0.md` — definition boundaries,
   target/parameter schemas, planned effects, binding modes, and lifecycle;
4. `data/CONVERSATIONAL_ACTION_DESIGN_CASES_README.md` — fixture semantics and
   validation requirements;
5. `data/conversational_action_design_cases.json` — 29 architecture cases;
6. `contracts/interpretation.py`, `contracts/goals.py`,
   `appraisal/models.py`, and `dynamic_state/models.py` — canonical inputs;
7. `cognitive_turn/` and `cognitive_pipeline_v0/` — integration owners; and
8. Goal V0's engine, fixture adapter, errors, and tests as patterns for
   immutable contracts, deterministic identities, failure phases, fixture
   independence, and exact handoff validation.

Authority order when prose differs:

```text
ACTION_SELECTION_V0_DESIGN.md
    > this implementation brief
    > canonical action sections of README.md
    > CONVERSATIONAL_ACTION_REGISTRY_V0.md
    > design-case authoring brief
    > fixture rationale or diagnostic prose
```

Do not silently resolve a genuine conflict by changing expected plans. Record
the conflict and fix the owning design document or contract first.

## 2. Required final pipeline

Implement this exact forward path:

```text
CognitivePipelineRequest
    -> Perception V0
    -> Interpretation V0
    -> Goal V0
       reads previous Dynamic State
    -> Appraisal
    -> Dynamic State update
       produces current-turn StateSnapshot
    -> Action Selection V0
       reads exact upstream outputs
    -> completed abstract ActionPlan
    -> CognitiveTurnTrace
    -> CognitivePipelineTrace
```

Action Selection is the final stage inside `CognitiveTurnRunner.evaluate()`.
`CognitivePipelineV0` must not invoke a second independent selector. It supplies
the action-owned runtime and personality projections to the Cognitive Turn,
then exposes the exact action result returned by that turn.

The action stage consumes the new current-turn Dynamic State snapshot, not the
previous snapshot used by Goal V0. It may not feed changes back into Goal,
Appraisal, Dynamic State, Interpretation, or Perception during the same turn.

## 3. Files to create

Create this package:

```text
system/contracts/actions.py
system/action_selection_v0/__init__.py
system/action_selection_v0/errors.py
system/action_selection_v0/models.py
system/action_selection_v0/registry.py
system/action_selection_v0/reference_index.py
system/action_selection_v0/construction.py
system/action_selection_v0/gates.py
system/action_selection_v0/features.py
system/action_selection_v0/policy.py
system/action_selection_v0/transition.py
system/action_selection_v0/engine.py
system/action_selection_v0/fixture_adapter.py
```

Small modules may be combined only when ownership remains obvious. Do not move
shared cross-stage contracts out of `contracts/actions.py`.

Create focused tests at minimum:

```text
tests/test_action_v0_contract.py
tests/test_action_v0_registry.py
tests/test_action_v0_construction.py
tests/test_action_v0_gates.py
tests/test_action_v0_features.py
tests/test_action_v0_policy.py
tests/test_action_v0_transition.py
tests/test_action_v0_engine.py
tests/test_action_v0_design_cases_runtime.py
tests/test_action_v0_cognitive_turn_integration.py
tests/test_action_v0_pipeline_integration.py
```

Modify the existing `contracts/__init__.py`, Cognitive Turn, Pipeline, tests,
and README only as required for canonical integration and factual status.

## 4. Contract conventions

All canonical action models must:

- inherit the existing canonical immutable/frozen model base;
- use `extra="forbid"` and deep-freeze nested containers;
- use tuples and immutable mappings in authoritative state;
- reject blank identifiers and duplicate identities;
- reject NaN and infinity;
- preserve deterministic collection order;
- validate ownership, lineage, revisions, and reference resolution;
- serialize and validate through JSON without semantic loss;
- contain no generated explanatory rationale as authoritative data; and
- use explicit version envelopes.

Use stable constants similar to:

```text
ACTION_CONTRACT_SCHEMA_VERSION = "action-contract-v0.1"
ACTION_REGISTRY_VERSION = "conversational-actions-v0.1-shadow"
ACTION_CONSTRUCTION_VERSION = "action-construction-v0.1"
ACTION_HARD_GATE_VERSION = "action-hard-gate-v0.1"
ACTION_FEATURE_VERSION = "action-features-v0.1"
ACTION_POLICY_VERSION = "action-policy-v0.1"
ACTION_PLANNER_VERSION = "action-planner-v0.1"
```

Version values in a result must be the versions actually used, not reconstructed
look-alikes.

## 5. Canonical enums

Define closed enums for at least:

```text
ActionLifecycle
  candidate
  reviewed
  shadow
  approved
  deprecated
  retired

ActionConstructionStatus
  uninstantiable
  instantiated

ActionGateStatus
  blocked
  shadow_only
  passed

ActionGateCategory
  registry_lifecycle
  schema_reference_integrity
  truth_evidence
  capability
  authorization_consent_privacy
  application_safety

MovePriority
  primary
  supporting

PlanTerminationKind
  stop
  no_viable_plan
  cycle_guard
  cancelled
  timeout
  failed

ActionFailurePhase
  request_validation
  registry_resolution
  candidate_construction
  hard_gate
  feature_extraction
  scoring
  conflict_resolution
  plan_transition
  plan_validation
  trace_validation
```

Operational termination values must never be treated as successful `STOP`
labels or positive training evidence.

## 6. Canonical definition contracts

Implement these shared models, with exact field names allowed to follow existing
repository naming conventions:

```text
ActionDefinitionRef
  action_key
  definition_version

ActionTargetSchema
  accepted_types
  minimum_targets
  maximum_targets

ActionParameterRule
  parameter_key
  value_type
  required
  required_when
  allowed_values
  reference_target_types

ActionEffectDefinition
  effect_key
  effect_version
  transition_domain
  binding_mode
  target_roles
  planned_state
  effect_strength
  actual_outcome_claim = false

ActionDefinition
  definition_ref
  parent_action_ref
  family
  function
  description
  target_schema
  parameter_schema
  evidence_requirements
  intended_effects
  structural_requirements
  hard_inhibitors
  policy_feature_relations
  outcome_metrics
  origin
  lifecycle
```

Validate every definition against the registry rules. `actual_outcome_claim`
must always be false in V0. Changing meaning, target schema, parameters, effects,
or gates requires a new definition version.

Production code must not parse Markdown or the design JSON to build the
registry. Encode typed static definitions in `registry.py` and test parity with
the accepted subset and effect manifest.

## 7. Installed registry

Encode all 75 definitions so candidate/deferred identities remain known, but
set lifecycle as follows:

- exactly the 39 keys listed in `ACTION_SELECTION_V0_DESIGN.md` are `shadow`;
- the 12 boundary-only keys are `candidate`;
- the 24 uncovered keys are `candidate`;
- no key is `approved` in V0.

Only `shadow` definitions may be selected by this isolated runner. Candidate,
reviewed, deprecated, and retired definitions must be non-selectable. The gate
must make lifecycle visible; it must not pretend a candidate was absent.

Test exact set equality, counts `39 + 12 + 24 = 75`, unique action keys,
definition versions, family count `9`, parent resolution, schema validity, and
effect-manifest completeness.

`candidate.manage_dialogue.establish_shared_signal` is not a registry entry.

## 8. Typed reference boundary

Define:

```text
ActionTargetRef
  role
  target_type
  ref
  revision_ref: optional

ActionEvidenceRef
  role
  ref
  source_kind
```

Build one deterministic `ActionReferenceIndex` from exact request objects. It
must index, without copying semantic meaning:

- Interpretation semantic frames, hypotheses, scenarios, unknowns, and
  contradictions;
- Goal records, requirements, assessments, and focal active projections;
- Appraisal frames and their dimensions;
- Dynamic State values, attention items, and derived controls;
- trusted runtime capability/task/policy facts;
- authorized grounded objects;
- explicitly supplied prior action, realization, and outcome records.

Every target and evidence reference must resolve, belong to the same cognitive
turn or an explicitly authorized prior record, and not point to a deleted,
superseded, unauthorized, or non-current object where current state is required.

Do not infer types or semantics from identifier spelling. Do not use case IDs,
titles, rationales, message prose, or expected plans as the reference index.

## 9. Runtime and personality projections

Define an action-owned `ActionRuntimeContext` rather than importing the Pipeline
request into the engine. It contains only trusted fields needed by the action
stage:

```text
runtime_snapshot_ref
response_obligation
response_obligation_ref
conversation_status
capability_snapshot_ref
available_capabilities
authorization
consent
safety_decision
external_export_control
policy_records
task_ref
required_task_parameters
provided_task_parameters
deadline_band
last_tool_event_ref/status, when present
```

Add `CognitivePipelineRuntimeSnapshot.to_action_runtime()` and verify that the
projection preserves exact values. No request may claim a capability,
authorization, consent, or safety status absent from the trusted Pipeline
runtime.

Define a separate `ActionPersonalityProfile`:

```text
profile_id
mapper_version
source_core_memory_ref
status: known | unknown
affinities: curiosity, warmth, playfulness, directness in [0,1]
```

Known profiles require all four finite affinities. Unknown profiles require no
affinity values and contribute numerically neutral personality influence while
remaining explicitly unknown in the trace. Do not derive action affinities from
Dynamic State's `PersonalityProfile`; it controls state updates and is not the
same contract.

`CognitivePipelineRequest` and `CognitiveTurnRequest` must require the action
profile explicitly. Tests may use a versioned unknown profile. No default may
silently construct personality values.

Relationship input is unavailable to Action V0. Do not pass the Pipeline's
relationship projection into `ActionSelectionRequest`, feature calculation, or
gates.

## 10. Action selection request

Define an immutable `ActionSelectionRequest` containing:

```text
schema_version
cognitive_turn_id
conversation_ref
interpretation: exact InterpretationEnvelope
goal_snapshot: exact GoalSnapshot
active_goals: exact focal ActiveGoalView tuple
appraisal: exact AppraisalResult
dynamic_state: exact current-turn StateSnapshot
runtime: ActionRuntimeContext
personality: ActionPersonalityProfile
grounded_objects
prior_action_records
prior_realization_records
observed_outcomes
versions or expected installed-version references
```

Validate:

- one cognitive-turn and conversation identity across inputs;
- Interpretation identity equals the Goal snapshot's interpretation reference;
- active goals exactly equal canonical projections for
  `goal_snapshot.focal_goal_refs` in order;
- appraisal cites the same Interpretation and only supplied focal goals;
- current Dynamic State cites only frames from the supplied Appraisal result;
- Dynamic State is the completed current-turn snapshot, not `previous_state`;
- runtime snapshot lineage matches the Goal and Pipeline runtime;
- personality cites the Pipeline core-memory reference and declared mapper;
- all prior records are authorized, typed, versioned, and unique; and
- installed versions match request versions.

The request must not contain expected plans, free-form relationship profiles,
caller-authored candidate lists, generated rationales, or raw fixture labels.

## 11. Grounded candidate contracts

Define at least:

```text
GroundedActionCandidate
  candidate_id
  definition_ref
  target_refs
  parameters
  supporting_evidence_refs
  proposal_rule_ids
  construction_status
  source_refs
  registry_version

UninstantiableAction
  attempt_id
  definition_ref
  candidate binding context
  missing target/parameter/evidence codes
  source_refs
```

Candidate identity is based on definition version, ordered target references,
normalized material parameters, and the current context identity. Exact
duplicates are canonicalized while retaining all proposal/evidence sources.

Parameters must satisfy the definition schema. Choice questions require
explicit grounded candidate-answer references. Scale questions require finite,
ordered bounds. Reference-bearing parameters use typed references; arbitrary
copied response content is forbidden.

## 12. Candidate construction

Construction must be deterministic and declarative:

1. enumerate installed definitions in stable `(action_key, version)` order;
2. retrieve only accepted target types from the reference index;
3. apply the definition's cardinality and binding mode;
4. bind required parameters only from explicit typed fields;
5. bind required evidence and planned effects;
6. emit an `UninstantiableAction` for each meaningful failed attempt;
7. canonicalize exact duplicate grounded candidates; and
8. return candidates in canonical identity order.

Create a versioned binding manifest in code for the 39 shadow definitions. Each
recipe declares target source types, target roles, parameter source fields,
required evidence, intended-effect bindings, and proposal rule ID. It must agree
with the registry Markdown but must not read it at runtime.

Construction may use structural support/disposition fields already present in
canonical objects. It may not use policy scores, personality, social preference,
case expectations, or natural-language rationale to decide applicability.

Question-form construction rules:

- `choice` only when two or more explicit candidate-answer refs exist;
- `scale` only when explicit minimum and maximum bounds exist;
- `check` only for a represented tentative interpretation/proposition;
- `yes_no` only for a represented proposition that can be confirmed/corrected;
- `open_set` for an unresolved value without bounded alternatives; and
- `free_reflection` only for an evidenced experience/reason target where open
  reflection is structurally appropriate.

Do not enumerate every possible question form merely to inflate recall.

## 13. Hard-gate contracts

Define:

```text
ActionGateViolation
  category
  code
  subject_ref
  evidence_refs

RealizationConstraint
  code
  subject_ref
  evidence_refs

ActionGateResult
  candidate_ref
  status
  violations
  realization_constraints
  gate_version
  definition/registry/safety versions
```

Evaluate all six categories in fixed order and retain every applicable
violation. Do not short-circuit after the first violation.

Required gate behavior:

1. **Registry/lifecycle:** unknown or wrong-version definitions are blocked;
   candidate/reviewed definitions are non-selectable; shadow definitions are
   eligible only because this runner is explicitly shadow; deprecated/retired
   definitions cannot enter new plans.
2. **Schema/reference:** validate targets, cardinality, parameters, evidence,
   turn ownership, authorization, and current/supersession state.
3. **Truth/evidence:** block only when every permitted realization necessarily
   asserts known-false, unsupported, or stronger-contradicted content. Otherwise
   attach uncertainty/evidence constraints.
4. **Capability:** block commitments, acceptance, or completion implications
   requiring unavailable capability; discussing unavailable capability remains
   possible.
5. **Authorization/consent/privacy:** block unauthorized commitments,
   executions, retrievals, or disclosures. Silence is never consent.
6. **Safety:** consume the versioned application safety decision; no score,
   personality, state, or outcome may override it.

Ordinary repetition, awkwardness, humor timing, insufficient warmth, verbosity,
or low expected utility are never gate violations by themselves.

## 14. Feature contract

For every eligible candidate emit all 13 normalized V0 features with value,
missing/inapplicable status, provenance refs, and calculator rule ID:

```text
semantic_fit             [0,1]
target_priority          [0,1]
goal_fit                 [0,1]
expected_goal_progress   [0,1]
uncertainty_reduction    [0,1]
contradiction_repair     [0,1]
evidence_support         [-1,1]
personality_affinity     [-1,1]
state_compatibility      [-1,1]
plan_contribution        [-1,1]
repetition_cost          [0,1]
response_cost            [0,1]
soft_risk_cost           [0,1]
```

Implement one versioned calculator manifest. Do not copy one salience value into
multiple features. Required V0 normalization:

- goal priority: low `0.33`, moderate `0.67`, high `1.00`;
- hypothesis/evidence support: weak `0.25`, moderate `0.60`, strong `1.00`;
- unknown/disputed/missing input contributes neutral `0` while retaining its
  missing status;
- direct effect-to-open-goal-requirement link is `1.00`; an explicit supporting
  link is `0.50`; no link is `0`;
- planned progress is capped at `1.00` per move and multiplied by residual
  matching target-effect need;
- multiple targets use the residual-priority-weighted mean; if every weight is
  zero the aggregate is zero; and
- exact repetition values are `1.00`, same effect/target `0.75`, same
  family/overlapping effect `0.50`, immediate prior-turn exact repetition
  `0.35`, otherwise `0`; use the maximum, never the sum.

For `semantic_fit`, instantiated exact type/cardinality/effect bindings begin at
`1.00`; a reviewed permitted superclass binding is `0.75`; anything weaker is
uninstantiable rather than a low-fit candidate.

Create versioned declarative manifests for:

- personality affinities by family/function;
- state/appraisal compatibility relations;
- response-cost defaults by definition; and
- soft-risk relations.

The manifests must use semantic families/effects, not case IDs or expected
choices. Personality influence is deliberately small and cannot create
remaining need. Social-play actions may respond to playfulness; information
seeking may respond to curiosity; feedback/social-support may respond to warmth;
concise goal-direct moves may respond to directness. Center `[0,1]` affinity
inputs around neutral `0.5` before producing `[-1,1]` feature values.

State compatibility may use only known current appraisal/state values and
derived controls. In particular, clarification, repair, and exploration
controls may support matching effects. Missing state is neutral, not inferred.

Store all manifest entries and rule IDs in the trace. Do not add per-case bonuses
or an unrestricted per-action bias.

## 15. Deterministic numeric representation and score

Do not compare binary floating-point values as if they were exact. Use one
documented deterministic representation throughout scoring—prefer fixed-point
integers or `Decimal` constructed from canonical decimal strings. Serialize the
canonical value used for comparison and define one versioned equality tolerance.

Implement the accepted formula exactly:

```text
remaining_need =
    0.90 * target_priority
  + 0.80 * goal_fit
  + 1.80 * expected_goal_progress
  + 1.30 * uncertainty_reduction
  + 1.40 * contradiction_repair

context_multiplier = clamp(
    0.00,
    1.75,
    1.00
      + 0.25 * evidence_support
      + 0.10 * personality_affinity
      + 0.25 * state_compatibility
      + 0.25 * plan_contribution
)

positive_marginal_value =
    semantic_fit * remaining_need * context_multiplier

score = positive_marginal_value
  - 1.80 * repetition_cost
  - 0.80 * response_cost
  - 1.20 * soft_risk_cost
  - 0.30

score(STOP) = 0
```

Trace every base value, weight, signed contribution, clamp input/output, shared
move overhead, positive marginal value, and final score.

## 16. Deterministic selection and ties

At each planning step:

```text
construct -> gate -> feature extraction -> score -> deterministic argmax
```

Select a move only when its score is strictly greater than `STOP` after applying
the versioned numeric tolerance. Resolve tied candidates in this order:

1. greater `expected_goal_progress`;
2. greater `target_priority`;
3. lower `soft_risk_cost`;
4. lower `response_cost`; and
5. ascending `(action_key, target_refs, normalized_parameters)`.

Record the complete tie set and the field that resolved it. Lexical resolution
is reproducibility metadata, never preference evidence.

## 17. Partial-plan state and transition

Define immutable successive states containing:

```text
PartialPlanState
  plan_id
  step_index
  selected_moves
  represented_effects
  addressed_target_effects
  residual_target_needs
  planned_goal_coverage
  planned_unknown_handling
  planned_contradiction_repairs
  action_counts
  family_counts
  previous_move_ref
  accumulated_response_cost
  accumulated_soft_risk
  state_signature
```

Apply the fixed transition order from `ACTION_SELECTION_V0_DESIGN.md` and the
README. Never mutate the prior state. Never mark an unknown known, a
contradiction actually repaired, a goal satisfied, a user reassured, or an
outcome achieved. Use only `*_planned` states.

Coverage uses:

```text
coverage_next = max(coverage_current, declared_effect_strength)
residual_need_next = max(0, initial_need - coverage_next)
```

Related but non-identical effects do not share coverage in V0.

Reconstruct bindings that depend on partial-plan moves, re-run gates where
needed, and recalculate every feature after each transition. A repeated canonical
state signature is an operational cycle failure, not `STOP`.

## 18. Primary and supporting moves

The engine, not the caller or fixture, assigns move priority after the plan is
complete. A non-empty plan has exactly one primary move. Select it by:

1. greatest direct coverage of the focal primary goal's open requirements;
2. greatest expected goal progress at its selection step;
3. greatest target priority at its selection step; and
4. canonical move identity order.

All other moves are supporting. Store the rule ID and compared factual fields;
do not store a generated rationale.

## 19. `STOP`, no viable plan, and guards

If the best score is not greater than zero:

- return successful `STOP` when the partial plan is non-empty;
- allow an empty successful plan only when response obligation is
  `not_required`; and
- return typed `no_viable_plan` when response is required and the partial plan
  is empty.

The current canonical response Pipeline requires a response, so its successful
traces must contain a non-empty plan.

Use a defensive configurable cycle/compute guard, but do not encode a semantic
maximum move count or question count. Guard exhaustion is an operational error
and must not become design or learning evidence for a preferred plan length.

## 20. Result and decision trace

Define at least:

```text
GroundedActionMove
PlannedActionEffect
ActionFeatureValue
ActionScoreBreakdown
ActionPlanningStep
ActionPlan
ActionVersions
ActionSelectionResult
```

Every planning step records:

- exact context and partial-state-before references;
- constructed and uninstantiable candidates;
- all gate results and constraints;
- every eligible candidate's normalized features and provenance;
- score contributions and final canonical score;
- `STOP` score;
- selected candidate or termination;
- tie set and tie-break field;
- applied declared effects;
- exact partial-state delta and after-state; and
- termination/continuation kind.

`ActionPlan` records ordered moves, primary/supporting refs, final partial-state
reference, termination kind, response obligation, and versions. It is a planned
abstract response, not wording or execution.

Validate complete internal reference resolution, ordering, uniqueness, effect
lineage, primary designation, score selection, state-signature chain, and JSON
round-trip.

## 21. Typed failure behavior

Implement `ActionSelectionError` with:

```text
cognitive_turn_id
failed_phase: ActionFailurePhase
category/code
safe_message
```

Always chain the original exception with `raise ... from exc`. Safe messages
must not contain user text, secret values, memory contents, or raw model data.

No partial plan is authoritative after failure. Action failure must prevent
successful Cognitive Turn and Pipeline traces. Future realization, execution,
outcome, reflection, and learning must not run.

## 22. Cognitive Turn integration

Extend `CognitiveTurnRequest` with:

- `action_runtime`;
- `action_personality`;
- typed authorized prior-action/prior-realization records, initially empty when
  unavailable.

After Dynamic State succeeds, construct `ActionSelectionRequest` from exact
existing objects:

```text
request.interpretation
request.goal_snapshot
request.active_goals
appraisal_result
update_result.snapshot
request.action_runtime
request.action_personality
request.grounded_objects/outcomes/prior records
```

Use identity checks before the engine call and canonical value checks after JSON
round-trip. Do not reconstruct Appraisal, goals, or state.

Extend `CognitiveTurnTrace` with required `action_selection`. Extend timings
with `action_selection_ms`; total time equals appraisal + Dynamic State + Action
Selection. Extend versions with exact `ActionVersions`.

Add Cognitive Turn failure stages `action_handoff_validation` and
`action_selection`. Preserve the nested Action failure phase on
`CognitiveTurnExecutionError` so Pipeline can report where selection failed.

Because this is a required contract addition, bump the Cognitive Turn schema
identifier and numeric schema version consistently. Do not retain a successful
compatibility path that omits Action Selection.

## 23. Pipeline integration

Extend `CognitivePipelineRequest` with required `action_personality`. Validate
its source core-memory reference and versioned unknown/known semantics.

Project `ActionRuntimeContext` from the same trusted runtime snapshot used by
Perception, Interpretation, Goal, and Appraisal. Pass it and the exact action
profile into `CognitiveTurnRequest`.

Extend `CognitivePipelineTrace` with required top-level `action_selection`. It
must be the exact in-process object returned inside `CognitiveTurnTrace`; after
serialization, canonical value equality is required. Extend Pipeline versions
with exact `ActionVersions`.

Keep `cognitive_turn_ms` inclusive of Appraisal, Dynamic State, and Action
Selection; the nested turn timing owns the stage breakdown. Do not double-count
action time in `total_ms`.

Pipeline failure remains at `cognitive_turn` externally, with nested stage and
Action phase preserved. No successful Pipeline trace may exist without a valid
non-empty action plan while response obligation is required.

Bump the Pipeline schema identifier and numeric schema version consistently.
Do not add an optional or caller-supplied selected-plan bypass.

## 24. Fixture migration

The current JSON fixture is schema-v1 authoring data and predates canonical Goal
V0. Its `goal_context`, compact appraisal/state maps, loose targets, literal
parameters, and relationship prose are not production contracts.

Either add an explicit schema-v2 canonical input section to every case or build
a test-only adapter whose mapping is fully declared in input data. Prefer
explicit schema-v2 inputs when feasible. The migration must provide:

- valid canonical Interpretation objects or explicit typed projections;
- valid `GoalSnapshot` and exact focal `ActiveGoalView` objects using the closed
  Goal V0 ontology;
- complete Appraisal dimensions with known/unknown/disputed status;
- a valid current-turn `StateSnapshot`;
- trusted runtime and explicit action-personality inputs;
- a typed target/evidence reference index;
- expected complete Action results or normalized semantic expectations; and
- typed failure/invariance variants.

The adapter must never:

- read acceptable/disfavored/invalid expected plans to construct candidates;
- infer provenance, targets, parameters, gates, or scores from rationale text;
- parse case IDs, titles, message prose, or ontology-review notes as policy;
- turn relationship placeholders into action features;
- alter expected semantics merely to make the engine pass; or
- hide unconsumed fixture fields.

Expected-output independence tests must mutate or destroy expected plans and
prove engine inputs and outputs are unchanged. Diagnostic-prose invariance tests
must replace rationales and prove policy output is unchanged.

Generated ID injection is allowed only for stable test identity. It must not
affect candidate presence, gates, features, scores, order, or termination.

## 25. Design-case comparison

Compare the complete semantic result, not a few selected fields:

- constructed/uninstantiable candidates;
- gate statuses, violations, and constraints;
- normalized features, missing flags, and provenance;
- score breakdowns;
- selected ordered moves;
- primary/supporting assignment;
- declared planned effects;
- every partial-state transition;
- ties and deterministic tie breaks;
- `STOP` or failure termination;
- final plan; and
- version envelope.

Where a design case intentionally allows multiple acceptable plans, the test
must verify that the deterministic V0 result is one declared acceptable plan and
that the scorer/trace supports the chosen ordering. Do not force all acceptable
alternatives into identical scores.

The proposed shared-signal definition must remain absent from the registry and
non-selectable. Its response uses existing acknowledgement/clarification moves
and makes no persistence claim.

## 26. Required unit tests

At minimum test:

### Contracts

- immutability and deep immutability;
- extra-field rejection;
- finite numeric validation;
- blank/duplicate identifier rejection;
- target/evidence reference resolution;
- ownership and lineage mismatches;
- version mismatches;
- JSON round-trip equality;
- invalid primary/supporting plans; and
- invalid termination combinations.

### Registry

- exact 75-key registry;
- exact 39 shadow keys and 36 candidate keys;
- all nine families;
- immutable definition versions;
- valid parent/schema/effect bindings;
- no shared-signal definition; and
- no live-approved definition.

### Construction

- every one of the 39 definitions has a binding recipe;
- missing targets/parameters/evidence become uninstantiable;
- choice/scale/check rules;
- multi-target binding and canonical order;
- duplicate canonicalization with retained provenance;
- no invented content;
- no expected-output or rationale dependency; and
- unknown/candidate definitions cannot be selected.

### Gates

- each of six gate categories independently;
- multiple violations retained together;
- uncertainty preserved by constraints;
- unsupported guarantee constrained or blocked as appropriate;
- unavailable capability versus discussion of capability;
- missing authorization/consent/privacy;
- safety cannot be overridden;
- ordinary awkwardness remains eligible; and
- shadow authority differs from live authority.

### Features and scoring

- every feature boundary and missing fallback;
- direct versus supporting goal links;
- multi-target weighted aggregation;
- all repetition bands;
- personality boundedness and explicit unknown behavior;
- state compatibility uses only known current state;
- exact score formula and clamp;
- deterministic numeric serialization;
- `STOP = 0`;
- every tie-break level; and
- lexical tie break recorded but not treated as preference evidence.

### Planning

- one-, two-, three-, and four-move plans;
- alternative acceptable order;
- early and late `STOP`;
- residual need falls only for matching effects;
- unknowns and goals remain unresolved/unsatisfied by planned effects;
- immutable state chain and state signatures;
- duplicate/repetition cost;
- cycle guard is operational failure;
- exactly one derived primary move;
- empty plan permitted only without response obligation; and
- required response with no move returns `no_viable_plan`.

### Failure boundaries

- every Action failure phase;
- original exception chaining;
- no partial authoritative result;
- Cognitive Turn stops before trace completion;
- Pipeline returns no successful trace;
- no later stage is called; and
- safe errors do not leak user content.

## 27. Required connected tests

Add tests proving:

1. current Perception and Interpretation outputs reach Goal unchanged;
2. Goal output reaches Appraisal unchanged;
3. Appraisal output reaches Dynamic State unchanged;
4. the new Dynamic State, not previous state, reaches Action Selection;
5. exact focal goals and complete Goal snapshot reach Action Selection;
6. trusted runtime projection is exact;
7. relationship data has no action-policy path;
8. exact Action result is shared by Cognitive Turn and Pipeline;
9. versions/timings include the action stage without double counting;
10. trace serialization preserves the complete action decision; and
11. any action-stage failure prevents a completed outer trace.

Use spies or recording engines where identity preservation matters, then also
run real connected-engine tests.

## 28. Regression commands

Run from `backend/` with the repository environment:

```text
UV_CACHE_DIR=/private/tmp/cognitive-action-uv \
MPLCONFIGDIR=/private/tmp/cognitive-action-mpl \
uv run --no-sync python -m pytest -q \
  tests/test_action_v0_contract.py \
  tests/test_action_v0_registry.py \
  tests/test_action_v0_construction.py \
  tests/test_action_v0_gates.py \
  tests/test_action_v0_features.py \
  tests/test_action_v0_policy.py \
  tests/test_action_v0_transition.py \
  tests/test_action_v0_engine.py \
  tests/test_action_v0_design_cases_runtime.py \
  tests/test_action_v0_cognitive_turn_integration.py \
  tests/test_action_v0_pipeline_integration.py

UV_CACHE_DIR=/private/tmp/cognitive-action-uv \
MPLCONFIGDIR=/private/tmp/cognitive-action-mpl \
uv run --no-sync python -m pytest -q

git diff --check
```

Also run the fixture adapter directly and report case, planning-step, failure,
invariance, and error counts.

## 29. Required implementation order

Use this order so failures remain local:

1. canonical action contracts and errors;
2. typed 75-definition registry and lifecycle split;
3. reference index;
4. construction recipes and uninstantiable results;
5. six hard gates;
6. feature and manifest calculators;
7. deterministic numeric scoring and tie resolution;
8. immutable partial-plan transition;
9. engine and complete trace validation;
10. fixture schema-v2 inputs or independent adapter;
11. design-case runtime tests;
12. Cognitive Turn integration;
13. Pipeline integration;
14. focused and complete regression suites;
15. independent audit; and
16. factual README status update.

Do not integrate a placeholder selector into Pipeline before the standalone
engine and fixture-independence tests are trustworthy.

## 30. Prohibited shortcuts

Do not:

- call an LLM for construction, gating, scoring, ordering, or `STOP`;
- use message text, rationale, title, or case ID as a hidden policy rule;
- load the fixture or Markdown registry in production;
- accept caller-authored selected plans or active candidates;
- treat planned effects as actual outcomes or goal satisfaction;
- use the previous Dynamic State instead of the new current state;
- pass relationship placeholders into V0 scoring;
- let personality change facts, targets, permissions, or gates;
- mark socially poor choices hard-invalid without a true invariant;
- add action-key score bonuses to make cases pass;
- add a semantic maximum move/question count;
- treat guard exhaustion as preferred `STOP`;
- compare unstable binary floats without a versioned numeric policy;
- catch action failure and continue with a placeholder plan;
- retain an optional successful Pipeline path without Action Selection;
- create a second action contract in realization or Pipeline;
- approve any action for live use;
- implement realization, tools, learning, reflection, or persistence; or
- change expected fixture outputs merely to match faulty behavior.

## 31. Completion criteria

Report Action Selection V0 complete only when:

- all canonical contracts and 75 definitions exist;
- exactly 39 definitions are selectable in shadow mode;
- construction, gates, features, score, transition, and trace are complete;
- all 29 cases execute through an independent adapter or canonical schema-v2
  inputs with complete comparisons;
- expected-output and diagnostic-prose independence tests pass;
- Cognitive Turn and Pipeline retain the exact result;
- response-required turns cannot succeed with an empty plan;
- all typed failures stop downstream work;
- focused and full backend tests pass;
- `git diff --check` passes; and
- an independent reviewer confirms design conformance.

Passing tests alone are not sufficient if the adapter derives inputs from
expected plans or diagnostic prose.

## 32. Required implementation report

The implementing agent must report:

1. every file created and modified;
2. contract and schema versions;
3. exact 39/36 lifecycle sets and registry validation;
4. construction recipes and deliberate limitations;
5. hard-gate codes and realization constraints;
6. feature manifests, numeric representation, weights, and tie policy;
7. planning/transition behavior and `STOP` handling;
8. fixture migration and independence evidence;
9. Cognitive Turn and Pipeline integration;
10. failure-phase and downstream-gating evidence;
11. focused and full test commands with pass/skip counts;
12. fixture case/step/failure/invariance/error counts;
13. remaining risks or design inconsistencies; and
14. README sections updated.

## 33. Central implementation rule

Action Selection V0 turns exact cognitive state into an auditable abstract plan.
Every selected move must be reconstructable from one immutable request, one
versioned definition, explicit typed targets and evidence, deterministic gates,
visible feature contributions, one exact score comparison, and an immutable
partial-plan transition. It must never invent desire, authority, truth,
capability, success, relationship state, or outcome evidence.

## 34. Addendum: Typed Requirement Resolution (contract / features / planner v0.3, 2026-09-30)

Normative text: [`REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md`](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md) §8. Summary of the changes:

- `ActionSelectionRequest.requirement_handoff` is optional and omitted when
  absent. It carries the sealed requirement snapshot and per-resolution
  bindings, and lineage is validated. The reference index requires every
  binding ref to resolve.
- `ActionPlanningCondition` has exactly one source: `rejection_rule_id`
  (unchanged) or `resolution_refs` (blocking resolutions of an adopted
  current-turn request goal). New kinds are `missing_capability`,
  `capability_unavailable`, `missing_consent`, `safety_prohibited`,
  `safety_unavailable`, `trusted_content_unavailable`, and
  `temporary_failure`. An unresolved `topic_ref` is now `missing_argument`.
- Resolution conditions are addressed only by the truthful move kind:
  elicitation for missing input, an answer with the explicit gap for missing
  content, and a decline with the grounded reason for capability, safety, or
  authorization. The move's principal target must be exactly a blocker.
  `feedback.receipt` addresses none of them, so acknowledgement can never
  clear a typed blocker.
- `ActionGroundedObject.capability_status` (CAPABILITY objects only) is
  `unregistered` or `unavailable`.
- Features: the handler goal is linked to decline directly and to acceptance
  and elicitation as support. Current-turn request goals in the handoff, and
  the frames they were formed from, earn goal credit, so every request in a
  compound turn reaches its own route.
