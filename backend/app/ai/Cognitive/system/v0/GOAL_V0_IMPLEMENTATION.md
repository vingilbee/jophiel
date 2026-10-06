# Goal System V0 implementation brief

Status: implemented and acceptance-verified for the shadow Goal V0 boundary on
2026-09-24

The implementation described by this brief now exists in `contracts/goals.py`,
`goal_v0/`, and the Cognitive Turn and Pipeline V0 integration layers. Its
verification baseline is 137 focused cognitive tests and 314 passing backend
tests with 5 skips. The executable design fixture covers 36 cases, 25 sequential
updates, 15 failure variants, and 2 invariance variants with no reported errors.
The brief remains normative for maintenance and regression review; completion
does not extend Goal V0 beyond its explicitly shadow-only scope.

This document instructs an implementation agent how to build Goal V0 and connect
it to the existing deterministic cognitive path. It is intentionally stricter
than a package-level feature request: Goal V0 is not complete until its canonical
contracts, engine, Appraisal migration, Cognitive Turn handoff, Cognitive
Pipeline trace, failure behavior, and tests all agree.

The implementation remains shadow-only. It must not change live chat behavior,
select actions, realize language, execute tools, persist goals to the database,
or claim that slow cognition exists.

Remediation on 2026-09-29 (SH-EVAL-013, SH-EVAL-014): formation, adoption,
evidence production, and focus precedence now follow the closed manifests and
rules in [§26](#26-remediation-v03-sh-eval-013--sh-eval-014). Where §7, §12,
or §13 below describe the original v0.1 policy, §26 is normative for the
installed versions. Status: **implemented**, not yet independently audited.

## 1. Required result

Implement one deterministic, immutable, evidence-linked Goal V0 stage in this
exact forward order:

```text
trusted CognitivePipelineRequest
    -> Perception V0
    -> Interpretation V0
    -> Goal V0
       inputs:
         exact current InterpretationEnvelope
         exact previous GoalSnapshot, if any
         trusted response/runtime context
         exact previous Dynamic State snapshot
         newly available target-linked goal evidence
       outputs:
         immutable next GoalSnapshot
         exact focal ActiveGoalView projections
         audited proposals, transitions, assessments, priority changes,
         goal conflicts, evidence conflicts, and focus decision
    -> Appraisal
    -> Dynamic State
    -> completed CognitivePipelineTrace
```

The implementation is complete only when:

1. `system/contracts/goals.py` is the single owner of every shared Goal V0
   contract;
2. the engine implements the approved definition registry and deterministic
   lifecycle/rule order;
3. callers can no longer inject arbitrary active goals into
   `CognitivePipelineV0`;
4. Appraisal receives only exact canonical projections of focal active goals;
5. the exact emitted goal snapshot is retained through Cognitive Turn and the
   outer pipeline trace;
6. all version, identity, revision, reference, and timing invariants are checked;
7. every Goal V0 failure is typed and fail-closed before Appraisal runs; and
8. the approved design cases and all existing cognitive suites pass.

Do not implement the engine in isolation and defer its connections. The package
and all migrations described here form one milestone.

## 2. Sources of truth and precedence

Read these sources completely before editing code:

1. `system/README.md`
   - `Connected cognitive trace design`;
   - `Goal System V0 canonical design`;
   - Appraisal/Dynamic State boundaries; and
   - the current implementation-status sections.
2. `system/v0/data/GOAL_DESIGN_CASES_README.md`.
3. `system/v0/data/goal_design_cases.json`.
4. `system/v0/data/OUTCOME_OBSERVATION_SCHEMA_V0.md`.
5. `system/v0/data/APPRAISAL_SCHEMA_V0.md` and the Appraisal implementation.
6. `system/v0/data/DYNAMIC_STATE_SCHEMA_V0.md` and the Dynamic State
   implementation.
7. `system/COGNITIVE_TURN_V0_IMPLEMENTATION.md` and current code.
8. `system/COGNITIVE_PIPELINE_V0_IMPLEMENTATION.md` and current code.

Precedence is:

```text
canonical README decisions
    -> approved Goal design fixture behavior
    -> this implementation brief
    -> existing legacy goal placeholder behavior
```

The fixture currently uses version names ending in `-draft`. Preserve those
exact identifiers in V0 so traces and fixtures agree. Do not silently rename
them to `final`, `v1`, or another version during implementation. The system is
still a shadow implementation and the fixture remains review provenance, not
training ground truth.

If code and the accepted design disagree, migrate the code. Do not weaken the
design to preserve the legacy `ActiveGoal` placeholder.

## 3. Decisions that must not be reopened

- A speaker-goal hypothesis is Interpretation evidence, not an adopted goal.
- A goal candidate is not an active commitment.
- Goals are desired states; they are not intentions, plans, actions, outcomes,
  constraints, personality, values, or Dynamic State.
- Safety, authorization, consent, privacy, truth, and capability are hard gates,
  not ordinary goals and not priority competitors.
- The definition registry is closed and versioned for V0.
- A response-producing pipeline turn always creates a new system-owned,
  turn-scoped `interaction.discharge_response_obligation` goal.
- Passive/no-response events do not enter `CognitivePipelineV0`.
- Goal V0 consumes the previous Dynamic State only. It cannot see the state
  produced later in the same forward pass.
- Appraisal cannot change goal status, priority, requirements, or focus.
- Planned or generated output is not satisfaction evidence.
- Terminal goal records never reopen in V0.
- Goal history is append-only and snapshots are immutable revisions.
- Focus is bounded to one primary goal and at most two compatible supporting
  goals.
- Only focal active goals reach Appraisal.
- Goal failure is fail-closed; no partial snapshot or fallback goal is allowed.
- V0 supports only turn, conversation, and task scope. No durable or
  cross-conversation goals may be added.

## 4. Current code that must be migrated

The current implementation has a temporary goal boundary:

- `appraisal.models.ActiveGoal` owns free-form `owner`, `goal_type`,
  `description`, `desired_state`, `priority_band`, and `status` fields;
- `CognitivePipelineRequest.active_goals` permits callers to inject those
  objects directly;
- `CognitiveTurnRequest.active_goals` carries them without a canonical goal
  snapshot;
- Appraisal contains rules that inspect the untyped `desired_state` mapping;
- neither `CognitivePipelineTrace` nor its timings/versions contain Goal V0.

Replace this boundary atomically. Do not leave both legacy and canonical goal
models available under different import paths.

During migration, `appraisal.models.ActiveGoal` may remain as this exact alias:

```python
from ..contracts.goals import ActiveGoalView

ActiveGoal = ActiveGoalView
```

It must not be a subclass, wrapper, conversion model, or duplicate schema.
Tests must assert:

```python
from app.ai.Cognitive.system.v0.appraisal.models import ActiveGoal
from app.ai.Cognitive.system.v0.contracts.goals import ActiveGoalView

assert ActiveGoal is ActiveGoalView
```

Remove all runtime dependence on `goal_type`, `description`, and
`desired_state`. Appraisal semantics must use registered definition references,
typed requirements, target references, and parameters.

## 5. Files to create and modify

Create:

```text
system/contracts/goals.py
system/goal_v0/__init__.py
system/goal_v0/models.py
system/goal_v0/ontology.py
system/goal_v0/lifecycle.py
system/goal_v0/rules.py
system/goal_v0/engine.py
system/goal_v0/errors.py
system/goal_v0/fixture_adapter.py       # test/design-fixture adapter only

tests/test_goal_v0_contract.py
tests/test_goal_v0_ontology.py
tests/test_goal_v0_lifecycle.py
tests/test_goal_v0_rules.py
tests/test_goal_v0_engine.py
tests/test_goal_v0_design_cases_runtime.py
tests/test_goal_v0_pipeline_integration.py
```

Modify at minimum:

```text
system/contracts/__init__.py
system/appraisal/models.py
system/appraisal/rules.py
system/appraisal/fixture_validator.py
system/appraisal/__init__.py
system/cognitive_turn/models.py
system/cognitive_turn/runner.py
system/cognitive_pipeline_v0/models.py
system/cognitive_pipeline_v0/runner.py
system/cognitive_pipeline_v0/errors.py
system/cognitive_pipeline_v0/__init__.py
system/README.md

tests/test_appraisal.py
tests/test_cognitive_turn.py
tests/test_cognitive_turn_provenance.py
tests/test_cognitive_pipeline_v0.py
tests/test_cognitive_pipeline_v0_contract.py
```

Only add another file when it has one clear responsibility. Do not build a
second generic framework, repository layer, database model, or abstract rule
language for V0.

## 6. Dependency direction

The dependency graph must remain acyclic:

```text
contracts/goals.py
        ^
        |
     goal_v0
        ^
        |
cognitive_pipeline_v0 ----> perception_v0
        |                   interpretation_v0
        |
        +----> cognitive_turn ----> appraisal
                                  -> dynamic_state
```

Rules:

- `contracts/goals.py` imports no engine, pipeline, Appraisal, or Dynamic State
  module.
- `goal_v0` may consume canonical Interpretation and Dynamic State contracts,
  but neither of those packages imports `goal_v0`.
- Appraisal imports `ActiveGoalView` from `contracts/goals.py`, never from the
  engine.
- The outer pipeline owns orchestration and exact-object handoffs.
- The fixture adapter is not imported by production code.

## 7. Version constants

Define constants once and reuse them everywhere:

```python
GOAL_CONTRACT_SCHEMA_VERSION = "goal-contract-v0.1-draft"
GOAL_ONTOLOGY_VERSION = "goal-ontology-v0.1-draft"
GOAL_LIFECYCLE_POLICY_VERSION = "goal-lifecycle-v0.1-draft"
GOAL_FORMATION_POLICY_VERSION = "goal-formation-v0.1-draft"
GOAL_PRIORITY_POLICY_VERSION = "goal-priority-v0.1-draft"
GOAL_SATISFACTION_POLICY_VERSION = "goal-satisfaction-v0.1-draft"
GOAL_ENGINE_VERSION = "goal-engine-v0.1"
```

The first six must match the top-level fixture exactly. `GOAL_ENGINE_VERSION`
is implementation provenance and must also appear in runtime traces. Reject an
unsupported snapshot version rather than attempting an implicit upgrade.

These are the original v0.1 values. The installed values, including the
independently versioned adoption and evidence policies, are listed in §26.1.

## 8. Canonical shared contracts

All shared models must be immutable Pydantic models with `extra="forbid"` and
JSON-round-trippable values. Reuse the established canonical frozen-model
behavior; nested mappings and collections must not remain mutable.

### 8.1 Enums

Use `StrEnum` and these exact values:

```text
GoalOwner: user, columbina, shared, system
GoalDomain: task, epistemic, interaction
GoalMode: achieve, maintain, avoid
GoalScope: turn, conversation, task
GoalPriorityBand: low, moderate, high

GoalStatus:
  candidate, active, suspended, satisfied, failed, abandoned,
  superseded, rejected, expired

GoalTransitionKind:
  propose, adopt, reject, expire, supersede, suspend, resume,
  satisfy, fail, abandon

GoalRequirementStatus: open, satisfied, impossible, disputed
GoalEvidenceConflictStatus: active, resolved
GoalConflictClassification:
  compatible, resource_competition, mutually_exclusive, unknown
GoalFocusRoute: continue_fast, clarify, slow
GoalUpdateTrigger:
  response_turn, realization_or_outcome, explicit_correction,
  trusted_scope_closure, trusted_runtime_change
```

Do not omit enum values merely because a particular fixture does not exercise
them. For example, `maintain`, `low`, and `candidate` are canonical even if the
approved cases rarely retain records in those states.

### 8.2 Definition and requirement contracts

Implement these canonical types:

```text
GoalDefinitionRef
  definition_key: str
  definition_version: str

GoalTargetRef
  role: str
  ref: str

GoalRequirementTemplate
  requirement_key: str
  required_target_roles: tuple[str, ...]
  allowed_parameter_keys: tuple[str, ...]
  satisfaction_policy_key: str

GoalDefinition
  definition_key: str
  definition_version: str
  lifecycle: str
  domain: GoalDomain
  mode: GoalMode
  allowed_owners: tuple[GoalOwner, ...]
  allowed_scopes: tuple[GoalScope, ...]
  default_priority_band: GoalPriorityBand
  requirement_templates: tuple[GoalRequirementTemplate, ...]

GoalRequirement
  requirement_id: str
  requirement_key: str
  target_refs: tuple[GoalTargetRef, ...]
  parameters: immutable JSON mapping
  satisfaction_policy_key: str

GoalAdoptionBasis
  rule_id: str
```

Validators must enforce:

- non-blank identifiers and references;
- unique target roles;
- definition owner/scope/domain/mode compatibility;
- the exact required target-role set from the definition;
- no unregistered parameter keys;
- the exact satisfaction policy declared by the template;
- JSON-safe, finite parameter values; and
- no duplicate requirement or target identities.

### 8.3 Goal state and audit contracts

Implement these canonical types with the exact fixture fields:

```text
GoalRecord
  goal_id
  definition_ref
  owner
  domain
  mode
  scope
  scope_ref
  priority_band
  status
  requirements[]
  adoption_basis
  created_turn_ref
  last_transition_ref
  source_refs[]

GoalProposal
  proposal_id
  goal_id
  definition_ref
  rule_id
  source_refs[]

GoalTransition
  transition_id
  goal_id
  sequence
  from_status or null
  to_status
  transition_kind
  rule_id
  cognitive_turn_id
  previous_snapshot_ref or null
  resulting_snapshot_ref
  source_refs[]
  evidence_refs[]
  outcome_refs[]
  replacement_goal_ref or null
  reason_code

GoalRequirementAssessment
  assessment_id
  goal_ref
  requirement_ref
  status
  policy_key
  evidence_refs[]
  conflict_refs[]
  previous_assessment_ref or null
  rationale

GoalEvidenceConflict
  conflict_id
  status
  left_evidence_ref
  right_evidence_ref
  resolution_ref or null
  rationale

GoalPriorityChange
  priority_change_id
  goal_ref
  old_priority_band
  new_priority_band
  rule_id
  evidence_refs[]
  rationale

GoalConflict
  conflict_id
  left_goal_ref
  right_goal_ref
  classification
  materiality
  reversibility
  rule_id
  evidence_refs[]
  resolution
  suspended_goal_ref or null
  route
  rationale

GoalFocusDecision
  focus_decision_id
  primary_goal_ref
  supporting_goal_refs[]
  non_focal_active_goal_refs[]
  rule_ids[]
  evidence_refs[]
  route
  rationale
```

Strings such as `materiality`, `reversibility`, `resolution`, `reason_code`, and
rule ids must be validated against explicit V0 registries in `goal_v0/rules.py`.
Do not accept arbitrary strings just because the Pydantic field is a `str`.

### 8.4 Goal evidence

Create a goal-specific evidence contract. Do not reuse the current Dynamic
State `OutcomeObservation`, because that type lacks target goal/requirement
ownership and finality.

```text
GoalEvidence
  evidence_id
  evidence_kind
  source_type
  source_ref
  episode_or_turn_ref
  target_goal_ref
  target_requirement_ref
  provenance
  finality
  factual_meaning
  outcome_id or null
```

Accepted evidence kinds are:

```text
realization_validation
semantic_grounding
explicit_user_confirmation
finalized_tool_result
trusted_runtime_fact
reviewed_outcome
```

`provenance` may be a small typed record if required by the accepted fixture;
it must remain structured and immutable. `factual_meaning` is diagnostic text,
not a rule input. Rules use the typed kind, target refs, finality, registered
policy, and trusted provenance fields.

Rules:

- evidence must target one existing goal and one requirement belonging to it;
- only final evidence may establish a terminal condition;
- evidence that represents an observed outcome requires a non-blank
  `outcome_id`;
- `GoalTransition.outcome_refs` and `GoalSnapshot.input_outcome_refs` use
  `outcome_id`, never `evidence_id` or `episode_or_turn_ref`;
- opposing credible evidence creates `GoalEvidenceConflict` and a `disputed`
  assessment;
- an active evidence conflict has `resolution_ref=None`;
- a resolved evidence conflict requires a valid resolution reference; and
- diagnostic wording must never be parsed to decide satisfaction.

### 8.5 Versions, snapshot, and downstream view

```text
GoalVersions
  goal_contract_schema
  goal_ontology
  goal_lifecycle_policy
  goal_formation_policy
  goal_priority_policy
  goal_satisfaction_policy
  goal_engine

GoalSnapshot
  schema_version: 1
  goal_snapshot_id
  revision
  supersedes_goal_snapshot_id or null
  cognitive_turn_id
  conversation_ref
  runtime_snapshot_ref
  interpretation_trace_ref
  previous_dynamic_state_ref
  input_outcome_refs[]
  records[]
  requirement_assessments[]
  evidence_conflicts[]
  transitions[]
  priority_changes[]
  conflicts[]
  focus_decision
  active_goal_refs[]
  focal_goal_refs[]
  versions

ActiveGoalView
  goal_id
  definition_ref
  owner
  domain
  mode
  scope
  scope_ref
  priority_band
  requirements[]
  source_refs[]
```

`ActiveGoalView` intentionally has no free-form description or desired-state
mapping and no lifecycle field. Its type and the snapshot validator establish
that it came from an active record. If Appraisal temporarily requires a
`status` property for compatibility, use a read-only property returning
`GoalStatus.ACTIVE`; do not serialize a second source of truth.

Snapshot validators must establish all of the following:

1. revision is `1` with a null predecessor, or exactly predecessor revision
   plus one with the exact predecessor id supplied to the engine;
2. conversation, turn, runtime, interpretation, and previous-state ownership
   match the update request;
3. record, requirement, transition, assessment, conflict, and audit ids are
   unique in their namespaces;
4. every definition ref resolves to the installed registry version;
5. every record duplicates the registry domain/mode exactly and uses an
   allowed owner/scope;
6. non-terminal records have a non-blank and correctly typed scope reference;
7. transition history is ordered by unique, strictly increasing sequence;
8. transition history legally reconstructs every record's status and
   `last_transition_ref`;
9. prior transition objects are byte-equivalent, not reconstructed or edited;
10. one latest requirement assessment exists for every requirement;
11. assessment goal/requirement/policy references resolve exactly;
12. disputed assessments reference same-snapshot evidence conflicts;
13. active refs equal exactly the record ids whose status is `active`;
14. focal refs are unique, ordered primary then supporting, and are a subset of
    active refs;
15. focus has exactly one primary and at most two supporting goals;
16. non-focal refs equal active refs minus focal refs;
17. every priority change, conflict, and focus ref resolves;
18. every outcome ref uses the canonical outcome-id namespace; and
19. all version fields equal installed versions.

`GoalSnapshot` retains complete transition history. It retains the latest
assessment for each requirement, linked to the preceding assessment id. Audit
records already carried from a predecessor must remain byte-equivalent.

The only allowed projection is a named function such as:

```python
def project_active_goal(record: GoalRecord) -> ActiveGoalView: ...
```

It must reject non-active records. The pipeline projects records named by
`focal_goal_refs` in that exact order and must preserve those exact view object
instances through `CognitiveTurnRequest` and `AppraisalRequest`.

## 9. Ontology registry

`goal_v0/ontology.py` owns one immutable registry containing exactly the ten
definitions in `goal_design_cases.json`:

```text
interaction.discharge_response_obligation
interaction.answer_information_request
interaction.acknowledge_relevant_information
interaction.repair_shared_understanding
interaction.respond_to_social_contact
epistemic.resolve_required_unknown
epistemic.resolve_material_contradiction
task.complete_authorized_request
task.prevent_or_stop_requested_action
interaction.explain_unavailable_or_failed_operation
```

Copy their owner/scope/default-priority/requirement declarations exactly from
the fixture. Do not hand-maintain a subtly different second table in tests.

Expose explicit lookup functions:

```python
definition(ref: GoalDefinitionRef) -> GoalDefinition
definition_by_key(key: str) -> GoalDefinition
all_definitions() -> tuple[GoalDefinition, ...]
validate_requirement(definition, requirement) -> None
```

Registry construction must fail on duplicate keys/versions, duplicate
requirement keys, unknown satisfaction policies, or invalid templates. Unknown
definitions and versions are validation errors, never generic task goals.

Do not load the runtime ontology by parsing the design fixture. Production code
must be importable without test/data files. Add a test proving the Python
registry is structurally equal to the fixture registry.

## 10. Engine-only request and result contracts

Keep orchestration-specific inputs out of the shared contract module.

### 10.1 Trusted goal runtime context

Implement an immutable `GoalRuntimeContext`:

```text
cognitive_turn_id
runtime_snapshot_ref
response_obligation: required | not_required
response_obligation_ref or null
conversation_status: open | closed
task_ref or null
available_capabilities[]
authorization
consent
safety_decision
deadline_band
```

This is a projection of the outer pipeline's one trusted runtime snapshot, not
a separately caller-authored object in the pipeline. The outer runtime snapshot
must gain the missing source fields and expose `to_goal_runtime()`.

For a `response_turn`:

- response obligation must be `required`;
- `response_obligation_ref` must be non-blank;
- conversation must be open; and
- `CognitivePipelineV0` must reject any other combination before Perception.

Direct engine calls for post-action reconciliation may use
`realization_or_outcome` without creating another response goal. They do not
turn the response pipeline into a passive-event pipeline.

### 10.2 Goal update request

```text
GoalUpdateRequest
  schema_version: 1
  trigger: GoalUpdateTrigger
  cognitive_turn_id
  conversation_ref
  interpretation: InterpretationEnvelope
  runtime: GoalRuntimeContext
  previous_goal_snapshot: GoalSnapshot or null
  previous_dynamic_state: StateSnapshot
  new_goal_evidence: tuple[GoalEvidence, ...]
```

The exact canonical `InterpretationEnvelope` and `StateSnapshot` instances must
be retained. Do not convert them to dicts and rebuild them before running rules.

Validation must reject:

- wrong conversation or turn ownership;
- a predecessor from another conversation;
- skipped or repeated revision ancestry;
- unsupported versions;
- duplicate evidence ids or outcome ids;
- goal evidence targeting missing goals/requirements, except evidence that
  targets a goal formed earlier in the same deterministic update where the rule
  explicitly allows it;
- any non-final evidence presented as completion;
- a response turn without a required trusted response obligation; and
- a direct passive update that attempts to fabricate a response obligation.

### 10.3 Goal update result

```text
GoalUpdateResult
  proposals: tuple[GoalProposal, ...]
  transitions: tuple[GoalTransition, ...]       # new transitions only
  requirement_assessments: tuple[...]          # next current assessments
  evidence_conflicts: tuple[...]                # new/current update records
  priority_changes: tuple[GoalPriorityChange, ...]
  conflicts: tuple[GoalConflict, ...]
  focus_decision: GoalFocusDecision
  snapshot: GoalSnapshot
  active_goal_views: tuple[ActiveGoalView, ...]
```

The result validator must prove that every top-level record is the exact record
stored in or represented by the snapshot, and every active view is the exact
projection of the corresponding focal record.

## 11. Lifecycle implementation

`goal_v0/lifecycle.py` must define one explicit transition table:

| From | Kind | To |
| --- | --- | --- |
| no record | `propose` | `candidate` |
| `candidate` | `adopt` | `active` |
| `candidate` | `reject` | `rejected` |
| `candidate` | `expire` | `expired` |
| `candidate` | `supersede` | `superseded` |
| `active` | `suspend` | `suspended` |
| `suspended` | `resume` | `active` |
| `active` or `suspended` | `satisfy` | `satisfied` |
| `active` or `suspended` | `fail` | `failed` |
| `active` or `suspended` | `abandon` | `abandoned` |
| `active` or `suspended` | `supersede` | `superseded` |
| `active` or `suspended` | `expire` | `expired` |

No `active -> active` transition is legal. Priority, focus, and requirement
changes have their own audit records.

Enforce:

- goal id, definition ref, owner, domain, mode, scope, scope ref, requirement
  identity, and creation turn do not mutate;
- a material desired-state change creates a replacement and supersedes the old
  record;
- supersession requires a valid replacement goal ref;
- other transition kinds require `replacement_goal_ref=None`;
- terminal states cannot transition;
- suspension retains adoption age and records a resolvable blocker/resumption
  basis in source/evidence refs;
- a failed action does not fail the goal while an approved route remains;
- `failed` requires verified impossibility;
- `abandoned` requires explicit withdrawal;
- `expired` means scope ended without another terminal result; and
- transitions created in one update have consecutive sequences after the
  predecessor's last sequence.

## 12. Deterministic rules

Rules must be named functions or immutable tables with registered ids. Do not
parse rationales, user text, goal ids, or fixture titles. Do not use an LLM.

### 12.1 Candidate formation and adoption

Implement all proposal rules listed in the design brief, including the
operation-failure rule even if its fixture coverage is indirect:

```text
goal-propose.required-response-obligation.v1
goal-propose.explicit-information-request.v1
goal-propose.relevant-information-acknowledgement.v1
goal-propose.explicit-repair-request.v1
goal-propose.explicit-social-contact.v1
goal-propose.blocking-required-unknown.v1
goal-propose.material-contradiction.v1
goal-propose.explicit-authorized-action-request.v1
goal-propose.explicit-prevent-or-stop-request.v1
goal-propose.known-operation-failure.v1
```

Formation consumes only registered Interpretation fields and trusted runtime
facts. It may inspect leading and retained-alternative hypotheses, their
evidence links, semantic frames, scenarios, unknowns, contradictions, and
modality/grounding. It must preserve scenario separation.

Use this semantic mapping (the installed, executable form of this table is
the closed formation manifest and adoption manifest of §26.4 and §26.5):

| Conditions | Candidate definition |
| --- | --- |
| trusted required response obligation | `interaction.discharge_response_obligation` |
| explicit current request-information act + obtain-information goal + grounded topic | `interaction.answer_information_request` |
| relevant inform act + seek-acknowledgement goal + grounded proposition | `interaction.acknowledge_relevant_information` |
| explicit correction/repair request + grounded proposition | `interaction.repair_shared_understanding` |
| explicit greeting/social contact + grounded dialogue scope | `interaction.respond_to_social_contact` |
| already adopted parent goal has a blocking required argument unknown | `epistemic.resolve_required_unknown` |
| material unresolved contradiction affects a supported decision/goal | `epistemic.resolve_material_contradiction` |
| explicit current action request + grounded task/effect | `task.complete_authorized_request` |
| explicit current prevent/stop request + grounded task/effect | `task.prevent_or_stop_requested_action` |
| known operation failure requiring an explanation | `interaction.explain_unavailable_or_failed_operation` |

Weak, quoted, hypothetical, reported, or insufficiently grounded requests may
produce an audited candidate when the registered formation rule is triggered,
but adoption must reject it under the exact rejection rule. They must never
silently become an active commitment.

Adoption/rejection rule ids are exactly:

```text
goal-adopt.required-system-obligation.v1
goal-adopt.explicit-grounded-user-request.v1
goal-adopt.explicit-shared-commitment.v1
goal-adopt.required-subgoal.v1
goal-reject.insufficient-interpretation-support.v1
goal-reject.quoted-or-hypothetical-request.v1
goal-reject.missing-required-grounding.v1
goal-reject.hard-gate-denial.v1
goal-reject.unregistered-definition.v1
```

Hard gates run before adoption/ranking. A denied action goal is rejected or an
existing goal is suspended with evidence; it is not adopted at low priority.

Deduplicate a non-turn candidate only when definition ref, owner, scope, scope
ref, required target refs, parameters, and material requirements exactly match
an existing non-terminal record. Never reuse a turn-scoped response obligation
across turns. Never reopen a terminal record; a recurrence gets a new identity
and provenance linking the earlier record.

### 12.2 Evidence reconciliation and satisfaction

Evaluate evidence using the requirement's registered satisfaction policy.
Implement every policy named by the ten definitions.

General rules:

- an `achieve` goal satisfies only when all requirements are `satisfied`;
- `disputed` blocks satisfaction;
- `impossible` permits failure only when no approved alternative route remains;
- `avoid` and future `maintain` goals require trusted scope closure plus adequate
  monitoring evidence;
- incomplete monitoring at closure expires rather than satisfies;
- subjective confirmation cannot override a contradictory objective result;
- unchanged requirements retain their assessment semantics and ancestry; and
- evidence never mutates the historical predecessor snapshot.

Never count these as satisfaction evidence by themselves:

```text
action selection
planned coverage
generated response
assistant claim of success
positive Appraisal
positive Dynamic State
positive sentiment
gratitude or politeness
continued use
silence or session closure
unfinalized tool event
```

### 12.3 Priority

Default priority comes from the definition. Apply only the registered priority
rules from the design brief, in their documented precedence. Emit a
`GoalPriorityChange` whenever an existing record's band changes.

Do not use owner as a universal hierarchy. Do not use personality, current
emotion, goal pressure, appraisal intensity, rationale wording, or action
scores. Previous Dynamic State may participate only in an explicitly reviewed
attention/interruption rule and can never establish adoption, truth,
satisfaction, ownership, or a priority change in V0.

### 12.4 Conflict and focus

Classify relevant active pairs using the registered compatibility rules as one
of:

```text
compatible
resource_competition
mutually_exclusive
unknown
```

Resolve in this order:

1. hard safety/authorization/consent/capability gates;
2. explicit withdrawal or correction/supersession;
3. higher priority band;
4. more specific applicable scope: turn, task, conversation;
5. older adopted commitment;
6. unresolved material/irreversible/interest-changing tie routes to clarify or
   slow cognition; and
7. stable ascending goal id only for safe, reversible, semantically equivalent
   choices.

Then select one primary goal and zero to two supporting goals. A supporting
goal must be compatible with the primary and have a requirement the current
response can advance. Preserve every other active goal as non-focal.

The response-obligation goal is a coordination floor. It normally supports the
semantic primary; it does not outrank that semantic goal merely because a
response is mandatory. Since priority policy v0.2 (§26.3) it does outrank
*older* compatible goals: a response turn's primary is always a goal of the
current turn (a blocking prerequisite, the current request, or this floor).

`GoalFocusDecision.route` is a recommendation. In the current milestone:

- `continue_fast` continues through the existing fast shadow runner;
- `clarify` continues to Appraisal using the fixture's bounded focal projection,
  usually only the response obligation where the material semantic choice is
  unresolved;
- `slow` continues to Appraisal so uncertainty and state are recorded, but no
  slow-cognition controller is invoked because it is not implemented.

Do not rewrite `CognitiveTurnRequest.processing_path` to `slow`; the existing
runner correctly rejects non-fast execution. The trace must preserve both the
executed fast path and the separate goal-route recommendation so the future
controller can use it honestly.

## 13. Engine execution order

`GoalEngine.update()` must use exactly this staged order:

```text
1. validation
2. evidence_reconciliation
3. lifecycle_update
4. candidate_formation
5. adoption
6. priority_assignment
7. conflict_resolution
8. focus_selection
9. snapshot_validation
```

Within an update:

1. validate request ownership, versions, predecessor, evidence, and bounds;
2. clone immutable working references without mutating inputs;
3. reconcile evidence and produce next requirement assessments/conflicts;
4. apply satisfaction, failure, withdrawal, supersession, and scope closure;
5. reevaluate suspension and resumption conditions;
6. form candidates from Interpretation and trusted runtime;
7. emit `propose` for every candidate, then adopt or reject it;
8. assign default/current priority and emit justified changes;
9. classify relevant active-goal pairs and resolve conflicts;
10. select bounded focus and route;
11. construct the complete next snapshot;
12. reparse it through `GoalSnapshot.model_validate()` and run cross-snapshot
    validation;
13. construct exact focal views from the validated snapshot; and
14. return only after result-level invariants pass.

Input models, predecessor snapshots, interpretation, state, evidence, and
historical records must remain byte-equivalent after execution.

Since lifecycle policy v0.3, evidence is reconciled in a canonical order
(non-satisfying observations first, then satisfying evidence, each by
evidence id), so the result is invariant under evidence order; a trusted turn
rollover (SH-EVAL-001) runs after evidence and lifecycle and before formation.

### Identity generation

Do not use iteration-order-dependent ids or wall-clock timestamps. Add an
injectable identity factory and a stable default derived from normalized
ownership inputs, record kind, turn/update identity, and local sequence. The
same normalized input must produce semantically and structurally identical
output. Tests may inject fixture ids where exact design comparison is useful.

## 14. Typed failures

Implement:

```text
GoalFailurePhase:
  validation
  evidence_reconciliation
  lifecycle_update
  candidate_formation
  adoption
  priority_assignment
  conflict_resolution
  focus_selection
  snapshot_validation

GoalExecutionError
  cognitive_turn_id
  failed_phase
  safe_message_category
  safe_message
```

The error must preserve the original exception as `__cause__`. The public
message/category must never include message contents, evidence factual meaning,
memory contents, or raw exception text.

On any failure:

- the previous snapshot remains authoritative;
- no partial result or next snapshot is returned;
- Appraisal and Dynamic State do not run;
- the caller cannot inject fallback active goals; and
- the outer pipeline reports the nested Goal phase.

Implement and test every failure variant in the approved
`fail-closed-validation-matrix`, applying its mutation to the selected local
`failure_baseline` via `baseline_key`.

## 15. Appraisal migration

Replace the legacy `ActiveGoal` definition with the exact canonical alias.
`AppraisalRequest.active_goals` may retain its field name for API stability, but
its type becomes `tuple[ActiveGoalView, ...]` and its runtime meaning is
strictly “the bounded focal active-goal views supplied by Goal V0.”

Update Appraisal validation:

- require one to three unique views;
- do not filter by an arbitrary serialized `status` field;
- frame only supplied focal views;
- retain exact view identity and ordering; and
- continue producing one frame per retained scenario/focal-goal pair.

Replace `_desired()` and all `desired_state` access with typed helpers such as:

```python
goal_has_definition(goal, definition_key)
goal_has_requirement(goal, requirement_key)
goal_target_refs(goal, role)
goal_parameter(goal, requirement_key, parameter_key)
```

For existing failure-related Appraisal behavior, determine congruence from the
registered goal definition/requirement:

- a failure event may advance
  `interaction.explain_unavailable_or_failed_operation` because its requirement
  is to explain the failure;
- the same failure obstructs an active task-completion goal;
- goal ids and natural-language descriptions never determine this difference.

Rewrite Appraisal tests to establish the same semantic contrast with canonical
requirements. Do not add old fields to `ActiveGoalView` merely to keep tests
unchanged.

The Appraisal design-fixture adapter may explicitly migrate old fixture goal
records into registered canonical test views. That adapter must reject an
unmappable desired state rather than guess.

## 16. Cognitive Turn integration

Change `CognitiveTurnRequest` to include:

```text
goal_snapshot: GoalSnapshot
active_goals: tuple[ActiveGoalView, ...]
```

The request validator must prove:

- `goal_snapshot.cognitive_turn_id`, conversation, interpretation trace, and
  previous Dynamic State ref match the request;
- `active_goals` equals the projection of `goal_snapshot.focal_goal_refs` in
  exact order and cardinality;
- every view is focal and active;
- no non-focal, suspended, candidate, or terminal goal is supplied; and
- there is at least one focal view.

`CognitiveTurnTrace.input_snapshot` already preserves the request. Add explicit
goal version provenance to `CognitiveTurnVersions`; if an explicit
`goal_snapshot` field is added to the trace for clarity, it must be the exact
same object, not a reconstruction.

`CognitiveTurnRunner` must pass the exact `active_goals` tuple into
`AppraisalRequest`. Tests must use `is`, not only equality, for the snapshot,
views, interpretation, Appraisal frames, and version objects at handoff points.

Dynamic State remains downstream. It may cite Appraisal frames scoped to goal
ids but cannot edit the goal snapshot.

## 17. Cognitive Pipeline integration

### 17.1 Request migration

Remove:

```text
CognitivePipelineRequest.active_goals
```

Add:

```text
previous_goal_snapshot: GoalSnapshot | None
new_goal_evidence: tuple[GoalEvidence, ...] = ()
```

Extend the canonical `CognitivePipelineRuntimeSnapshot` with the trusted fields
needed by `GoalRuntimeContext`, including:

```text
response_obligation
response_obligation_ref
conversation_status
task_ref
deadline_band
```

Existing authorization, consent, safety, capabilities, runtime id, and task
contract remain owned by the same runtime snapshot. `to_goal_runtime()` derives
one consistent view. Validate that `task_ref` and task contract presence do not
contradict each other where a task-scoped goal is possible.

The response pipeline accepts only `response_obligation="required"`. A passive
event or `not_required` value fails validation before Perception. The future
observer/update path is outside this task.

### 17.2 Runner sequence

After Interpretation validates and before constructing
`CognitiveTurnRequest`:

```python
goal_request = GoalUpdateRequest(
    trigger=GoalUpdateTrigger.RESPONSE_TURN,
    cognitive_turn_id=request.cognitive_turn_id,
    conversation_ref=request.conversation_ref,
    interpretation=interpretation,
    runtime=request.runtime.to_goal_runtime(),
    previous_goal_snapshot=request.previous_goal_snapshot,
    previous_dynamic_state=request.previous_state,
    new_goal_evidence=request.new_goal_evidence,
)
goal_update = goal_engine.update(goal_request)
```

Then build `CognitiveTurnRequest` with:

```text
goal_snapshot = goal_update.snapshot
active_goals = goal_update.active_goal_views
```

No caller data may replace or append to those views.

Add dependency injection for `GoalEngine` to `CognitivePipelineV0.__init__`.
The default is the real engine. Tests must use the real engine for connected
happy paths and may use spies only for explicit handoff/failure isolation.

### 17.3 Pipeline trace, versions, and timings

Add the exact emitted `GoalSnapshot` to `CognitivePipelineTrace` between
Interpretation and Cognitive Turn:

```text
perception
interpretation
goal_snapshot
cognitive_turn
```

Add Goal versions to `CognitivePipelineVersions` and `goal_update_ms` to
`CognitivePipelineTimings`.

The checked timing equation becomes:

```text
total_ms = perception_ms
         + interpretation_ms
         + goal_update_ms
         + cognitive_turn_ms
```

Trace validation must prove:

1. turn, conversation, runtime, interpretation, and previous-state ownership;
2. predecessor id and revision continuity;
3. exact GoalSnapshot object identity across pipeline/turn handoffs;
4. exact focal-view projection and object identity;
5. every Appraisal focal-goal ref belongs to snapshot focal refs;
6. no non-focal/non-active goal reached Appraisal;
7. Goal versions equal snapshot and installed versions;
8. Cognitive Turn retains the same Interpretation and goal inputs;
9. Dynamic State cites only emitted Appraisal records; and
10. stage timings are finite, non-negative, and sum correctly.

### 17.4 Pipeline failure stages

Extend `PipelineFailureStage` with:

```text
goal_update
goal_handoff_validation
```

When `GoalExecutionError` is caught:

- outer `failed_stage` is `goal_update`;
- `nested_stage` is the exact `GoalFailurePhase`;
- safe outer text contains no raw content; and
- the Cognitive Turn runner is not called.

Use `goal_handoff_validation` for a successfully returned engine result that
fails canonical projection, identity, version, or ownership checks.

## 18. Scope and revision behavior

Implement these exact rules:

| Scope | Required scope ref | Persistence |
| --- | --- | --- |
| turn | current cognitive-turn id | one response episode |
| conversation | current conversation ref | same open conversation only |
| task | trusted task ref | same task and containing conversation only |

- Each response turn gets a new response-obligation identity.
- Conversation/task records preserve original `created_turn_ref` across
  revisions.
- A same-topic new conversation never loads the prior conversation's goals.
- Trusted scope closure expires unresolved goals; it does not satisfy them.
- Explicit withdrawal abandons.
- Verified impossibility fails.
- Material replacement supersedes and names the replacement.
- Post-action evidence creates a later snapshot revision and never edits the
  pre-action snapshot retained in an earlier cognitive trace.

Persistence to a database is excluded. For V0, the caller supplies the exact
previous in-memory/serialized snapshot and stores the returned snapshot outside
this package.

## 19. Fixture adapter and runtime conformance

`goal_design_cases.json` is a design fixture, not already a production request
schema. Implement a test-only adapter that maps every case into canonical
runtime objects without losing distinctions.

The adapter must:

- validate top-level versions and registry equality;
- resolve every local id/reference;
- build real canonical Interpretation envelopes or an explicitly validated
  canonical equivalent from the fixture's interpretation input;
- build real previous GoalSnapshot and StateSnapshot objects;
- preserve sequence-step predecessor identity;
- apply new goal evidence at the declared step;
- execute the real GoalEngine;
- compare semantic output fields to expected updates;
- normalize only injected ids explicitly designated by the test;
- apply all failure mutations through JSON Pointer to the selected local
  baseline; and
- fail when a fixture field is unconsumed or unsupported.

Do not make production code read expected outputs. Do not special-case case ids
in the engine. Do not assert only that the fixture parses.

## 20. Required tests

Testing is part of this implementation and must be performed immediately at
each layer. Do not wait for Action Selection or another cognitive component.

### 20.1 Canonical contract tests

Test:

- exact enum values;
- immutability, including nested mappings/collections;
- JSON round-trip equality;
- unknown fields forbidden;
- registry/version rejection;
- owner/domain/mode/scope compatibility;
- target-role and parameter validation;
- reference uniqueness and resolution;
- active/focal projection;
- snapshot reconstruction from transitions;
- revision/predecessor rules;
- canonical alias identity in Appraisal; and
- invalid status/focus/evidence-conflict combinations.

### 20.2 Ontology tests

Test exact structural equality between the Python registry and the ten fixture
definitions. Test every definition lookup, requirement template, allowed
owner/scope, default priority, and satisfaction policy. Unknown key/version must
fail.

### 20.3 Lifecycle tests

Test every legal table edge and representative illegal edges. Test terminal
non-reopening, supersession replacement refs, consecutive transition sequence,
byte-equivalent history, suspension/resumption, expiry, abandonment, verified
failure, and new identity after recurrence.

### 20.4 Formation/adoption tests

Test every registered proposal and adoption/rejection rule with natural minimal
contrasts:

- direct versus weak inferred request;
- current versus quoted/hypothetical request;
- information versus action versus prevent/stop;
- grounded versus missing target;
- authorized versus denied/missing authorization;
- relevant versus irrelevant unknown;
- shared repair and acknowledgement;
- social contact;
- material contradiction;
- known operation failure; and
- required response obligation on every response turn.

### 20.5 Evidence and terminal tests

Test every satisfaction policy and evidence kind, plus:

- partial/non-final evidence remains open or fails validation when presented as
  terminal;
- generated/planned response is insufficient;
- explicit confirmation versus objective contradiction becomes disputed;
- response delivery satisfies only the response obligation it targets;
- finalized matching tool result may satisfy a task requirement;
- failed attempt with another route leaves the goal viable;
- avoid/maintain closure requires monitoring coverage; and
- silence, gratitude, sentiment, continued use, and positive state do nothing.

### 20.6 Priority, conflict, and focus tests

Test default priority, explicit request, deadline change, blocking subgoal,
deferral/resumption, owner non-hierarchy, compatibility, resource competition,
mutual exclusion, unknown relation, scope specificity, older commitment,
material tie routing, benign deterministic tie, bounded focus, and non-focal
retention.

Run the personality/state invariance pair and assert proposals, transitions,
assessments, priority, conflicts, focus, and snapshot records remain identical
apart from explicitly owned provenance fields.

### 20.7 Failure tests

Execute all 15 approved failure variants. For each, assert:

- exact nested phase/category;
- previous snapshot remains byte-equivalent and authoritative;
- no partial result;
- no fallback goal;
- Appraisal spy not called;
- Dynamic State spy not called;
- safe error text contains no raw user content; and
- original exception is chained.

### 20.8 Connected engine tests

Use real Perception, Interpretation, Goal, Appraisal, and Dynamic State engines
for at least:

- response-obligation-only turn;
- direct information request;
- authorized task request;
- blocking required unknown;
- material contradiction routing;
- bounded multi-goal focus;
- material conflict clarification;
- previous-snapshot persistence; and
- outcome evidence reconciled on a later revision.

Assert exact-object identity at every authoritative handoff.

### 20.9 Regression and determinism tests

Run at minimum:

```bash
cd backend
uv run --no-sync python -m pytest \
  tests/test_goal_design_cases.py \
  tests/test_goal_v0_contract.py \
  tests/test_goal_v0_ontology.py \
  tests/test_goal_v0_lifecycle.py \
  tests/test_goal_v0_rules.py \
  tests/test_goal_v0_engine.py \
  tests/test_goal_v0_design_cases_runtime.py \
  tests/test_goal_v0_pipeline_integration.py \
  tests/test_appraisal.py \
  tests/test_dynamic_state.py \
  tests/test_dynamic_state_contract.py \
  tests/test_cognitive_turn.py \
  tests/test_cognitive_turn_provenance.py \
  tests/test_cognitive_pipeline_v0.py \
  tests/test_cognitive_pipeline_v0_contract.py
```

Then run the complete backend suite. Run `git diff --check` as the final static
check.

Execute the same normalized GoalUpdateRequest twice with fresh engine instances
and assert identical snapshots/results. Also serialize and reparse the result
and assert equality.

## 21. Implementation order

Use this order so no temporary competing contract becomes entrenched:

1. add canonical enums/contracts and contract tests;
2. add exact ontology registry and registry/fixture parity tests;
3. implement lifecycle table and transition-history validation;
4. implement goal evidence, assessments, and satisfaction policies;
5. implement formation/adoption rules;
6. implement priority, conflict, and focus rules;
7. implement `GoalEngine` and typed failures;
8. make approved design cases execute against the real engine;
9. migrate Appraisal to the canonical alias and typed requirement semantics;
10. extend Cognitive Turn with the snapshot and exact focal views;
11. migrate Cognitive Pipeline request, runner, trace, timings, versions, and
    failures;
12. add real-engine end-to-end tests and adversarial handoff tests;
13. run focused and complete backend suites;
14. update `system/README.md` implementation status and diagrams to state what
    actually passed; and
15. report any deferred work without presenting it as implemented.

Keep the branch runnable after each logical layer where practical, but do not
retain a public compatibility path that lets callers bypass Goal V0.

## 22. Acceptance criteria

The task is accepted only if all statements below are true:

- there is exactly one shared goal contract package;
- no duplicate/subclass legacy `ActiveGoal` exists;
- all ten definition records and policies match the fixture;
- lifecycle state is reconstructable from legal ordered transitions;
- snapshots and nested values are immutable and JSON-round-trippable;
- the engine is deterministic and does not inspect raw text/rationales for rule
  decisions;
- response turns always create their exact turn-scoped system obligation;
- speaker-goal hypotheses do not become commitments without adoption rules;
- hard gates cannot be overridden by priority/personality/state;
- only allowlisted final target-linked evidence changes requirements;
- terminal satisfaction/failure/expiry semantics match the design;
- conflict and focus are audited, bounded, and deterministic;
- only exact focal active projections reach Appraisal;
- Appraisal no longer depends on free-form desired-state mappings;
- Goal V0 runs after exact Interpretation and before Appraisal;
- Goal V0 sees only the previous Dynamic State;
- pipeline callers cannot inject active goals;
- traces retain exact GoalSnapshot identity, versions, route, and timing;
- goal failures prevent all downstream cognitive stages;
- the design cases execute against production rules rather than only parsing;
- focused and complete test suites pass; and
- README status is updated accurately after verification.

## 23. Explicit exclusions and forbidden shortcuts

Do not:

- connect this path to live chat or add an agent/cognitive mode switch;
- persist goals to database tables;
- implement Action Selection, realization, outcome ingestion, reflection,
  learning, or slow cognition in this task;
- call an LLM for formation, adoption, priority, conflict, focus, or
  satisfaction;
- create a generic “help the user” or fabricated user goal;
- encode safety, truth, privacy, consent, authorization, personality, emotion,
  or Dynamic State as ordinary goals;
- accept caller-authored active goal views in the pipeline;
- keep `desired_state` as a hidden fallback;
- use mutable dictionaries as canonical state;
- infer semantics from ids, rationale text, descriptions, or fixture case ids;
- average competing scenarios into one conclusion;
- satisfy a goal because a response was generated;
- reopen terminal goals;
- mutate a prior snapshot or trace;
- catch a goal failure and continue with placeholder goals;
- claim `slow` route means slow cognition executed;
- make production code depend on the design JSON file; or
- update expected fixture outputs merely to make failing code pass.

## 24. Required implementation report

The implementing agent must report:

1. files created and modified;
2. canonical contracts and registry implemented;
3. rule families implemented and any deliberate limitations;
4. legacy `ActiveGoal` migration details;
5. Cognitive Turn and Pipeline integration details;
6. exact test commands and pass counts;
7. design-case runtime coverage count;
8. confirmation that no downstream stage ran in failure tests;
9. remaining risks, inconsistencies, or unimplemented exclusions; and
10. README sections updated.

Do not report Goal V0 complete if only contracts or the standalone engine exist.
Completion means the connected shadow pipeline satisfies this entire brief.

## 25. Central implementation rule

Goal V0 must turn interpreted possibilities and trusted runtime obligations into
auditable commitments without inventing desire, authority, success, or
certainty. Every downstream goal reference must be explainable from one
immutable snapshot, one registered definition, one legal lifecycle history, and
the exact evidence that justified it.

## 26. Remediation v0.3 (SH-EVAL-013 / SH-EVAL-014)

Status on 2026-09-29: **implemented** (production code plus focused and
full-suite regression tests). Not implementation-accepted until an independent
audit. Shadow-only: no model call, tool execution, persistence, or live
response behavior was added. This section is normative for the installed
versions and supersedes the original v0.1 wording it conflicts with.

It repairs four connected defects:

1. active goals had no trusted producer of reconciliation evidence, so they
   could not reach a justified terminal or suspended state;
2. an older compatible goal could hold primary focus indefinitely
   (SH-EVAL-014: a task-scoped repair goal was primary for six turns);
3. adoption rejected a candidate whenever any linked hypothesis was below
   `strong`, contradicting the ontology's reviewed `moderate` support
   (SH-EVAL-013: explicit greetings were rejected); and
4. formation tested whole-scenario hypothesis-key sets and merged arguments
   from every frame, so compound acts contaminated one another.

### 26.1 Versions

| Constant | Value | Change |
| --- | --- | --- |
| `GOAL_CONTRACT_SCHEMA_VERSION` | `goal-contract-v0.3-draft` | `GoalTransition.rejection_detail` (`GoalRejectionDetail`), omitted when absent so v0.2 payloads serialize unchanged |
| `GOAL_LIFECYCLE_POLICY_VERSION` | `goal-lifecycle-v0.3-draft` | failed/unavailable outcomes never satisfy; canonical evidence order |
| `GOAL_FORMATION_POLICY_VERSION` | `goal-formation-v0.3-draft` | closed formation manifest (`goal_v0/formation_policy.py`) |
| `GOAL_PRIORITY_POLICY_VERSION` | `goal-priority-v0.2-draft` | focus precedence tiers |
| `GOAL_SATISFACTION_POLICY_VERSION` | `goal-satisfaction-v0.2-draft` | per-policy evidence classes from the authority table |
| `GOAL_ADOPTION_POLICY_VERSION` (new) | `goal-adoption-policy-v0.1-draft` | closed adoption manifest (`goal_v0/adoption_policy.py`) |
| `GOAL_EVIDENCE_POLICY_VERSION` (new) | `goal-evidence-policy-v0.1-draft` | typed outcome events → evidence (`goal_v0/evidence_producer.py`) |
| `GOAL_ENGINE_VERSION` | `goal-engine-v0.3` | |

`GoalVersions` gained `goal_adoption_policy` and `goal_evidence_policy`. A
payload that predates them loads with the current value; its other, older
version strings identify it as history. Live-shadow lineage compares the whole
`GoalVersions`, so a v0.2 predecessor starts a new segment
(`lineage_version_change`) and stored v0.2 traces replay as
`version_unavailable`. `ontology` is unchanged (`goal-ontology-v0.1-draft`).

New registered rules: `goal-reject.parent-goal-not-adopted.v1`,
`goal-reject.active-contradiction.v1`, and
`goal-priority.current-explicit-request-over-compatible-history.v1`.

### 26.2 Update order

```text
evidence reconciliation (canonical order)
-> lifecycle transitions (withdraw, fail, suspend, resume, supersede, expire)
-> trusted turn rollover (SH-EVAL-001)
-> formation (formation manifest) -> adoption (adoption manifest)
-> priority -> conflict classification -> focus (precedence tiers)
```

Focus ranks only `active` records, so a goal that became terminal in the same
update is never focal. A lifecycle-only revision (`realization_or_outcome`,
`explicit_correction`, `trusted_runtime_change`, `trusted_scope_closure`)
never creates a response obligation.

### 26.3 Focus precedence (SH-EVAL-014)

Every active goal is placed in one tier (`GoalEngine._focus_tier`); within a
tier the definition-default semantic order and then goal id decide.

| Tier | Goals |
| --- | --- |
| 0 | a `epistemic.resolve_required_unknown` whose typed `parent_goal_ref` target is an active goal of the current turn (a prerequisite blocking the current request) |
| 1 | any other goal formed in the current turn (the current explicit request) |
| 2 | this turn's response obligation (the coordination floor) |
| 3 | compatible history: an unresolved goal of an earlier turn |
| 4 | standing social maintenance from an earlier turn |
| 5 | another turn's response obligation (lifecycle revisions only) |

An older goal is a blocker only through the typed `parent_goal_ref` relation;
shared task scope or age never makes it one. Consequences:

- in a response turn the primary goal is always a goal of the current turn;
- when the turn forms no request, its response obligation is primary and
  unresolved history is supporting or non-focal, never deleted;
- this turn's obligation is still the first supporting goal behind a current
  request, and focus stays bounded to one primary and two supporting goals;
- whenever active history was ranked below the current turn, the focus cites
  `goal-priority.current-explicit-request-over-compatible-history.v1`;
- focus evidence still comes only from the exact focal and floor records
  (SH-EVAL-002), and `coordination_floor_goal_ref` stays separate from the
  primary.

The reviewed design case `scope-persistence` (an older task plus a turn with no
new request) now expects the current obligation as primary and the older task
as supporting; its objective (the exact task persists) is unchanged.

### 26.4 Formation manifest (workstream D)

`goal_v0/formation_policy.py` is closed over the ten definitions and validated
at import. Each semantic entry names its communicative act, its speaker goal,
the registered act families of frames it may bind, how every requirement
target and parameter is derived, its owner and scope, and its runtime gates.
For each retained scenario, each retained act hypothesis of an entry, and each
eligible frame the act *and* its linked speaker goal cite, one candidate forms;
arguments are copied from that frame only. Candidates are deduplicated by
semantic identity after binding.

| Definition | Act + speaker goal | Frames | Binding / scope |
| --- | --- | --- | --- |
| `interaction.discharge_response_obligation` | trusted runtime obligation on a response turn | — | `turn_ref` = this turn; turn |
| `interaction.answer_information_request` | `act.request_information` + `goal.obtain_information` | `request_information` family | `topic_ref` = frame `topic_ref` or the frame itself; `answer_scope`; `prior_goal_ref` cited as a source; turn |
| `interaction.acknowledge_relevant_information` | `act.inform` + `goal.seek_acknowledgement` | `inform` | `proposition_ref`; conversation |
| `interaction.repair_shared_understanding` | `act.correct` + `goal.repair_shared_understanding` | `correct` | `proposition_ref`; task (trusted task or this turn) |
| `interaction.respond_to_social_contact` | `act.greet` + `goal.maintain_social_connection` | `greet`, `social_question` | `dialogue_scope_ref` = conversation; `contact_kind`; frame `contact_scope` (default turn) |
| `task.complete_authorized_request` | `act.request_action` + `goal.cause_action` | `request_action`; negative frames beside a positive one scope it | needs trusted `task_ref`; `requested_effect` (or `unresolved_effect` when `subject_ref` is unresolved, at moderate priority) |
| `task.prevent_or_stop_requested_action` | `act.request_action` + `goal.prevent_or_stop_action` | `request_action` | needs trusted `task_ref`; `prevented_effect` |
| `epistemic.resolve_required_unknown` | an exact blocking argument of a formed parent | — | `parent_goal_ref`, `argument_ref` = the one typed unknown; high priority |
| `epistemic.resolve_material_contradiction` | each active contradiction a retained scenario cites | — | `contradiction_ref` |
| `interaction.explain_unavailable_or_failed_operation` | failed/unavailable trusted tool event | — | `operation_ref`, `failure_ref`; turn |

A predicate with a declared act family binds only an entry that lists that
family; a predicate without one (legacy design predicates and unregistered
reviewed design-case predicates) binds through the act that cites it. An act that cites no frame (single-act design cases) is grounded in
its scenario's frames. An `unknown` frame is still formed so that the blocked
request reaches Action as a typed planning condition; its adoption policy
rejects it (§26.5).

Unknown arguments are never invented. A resolvable argument forms an epistemic
subgoal only for its own parent and only when exactly one typed unknown of
that name is carried by the scenario (several are narrowed by shared evidence;
ambiguity is not guessed away). The removed rule that attached the first
global unknown to the first active goal no longer exists.

### 26.5 Adoption manifest (workstream C)

`goal_v0/adoption_policy.py` gives every definition a `GoalAdoptionPolicy`:
typed `GoalHypothesisRequirement`s (role, key, minimum support, allowed
dispositions, required/optional), bound target roles, unresolved-argument
policy, ungrounded predicates, and hard gates. Only the candidate's own act
and the hypotheses that cite its frame (or cite none) are evaluated; a
hypothesis no requirement names never vetoes adoption, and optional
(corroborating) requirements never veto. Checks run in a fixed precedence:
bound targets and ungrounded predicate → unresolved arguments → authorization
→ safety → adopted parent → active contradiction → quoted/hypothetical
modality → hypothesis requirements.

| Definition | Required hypotheses (all `leading`) | Optional | Gates |
| --- | --- | --- | --- |
| response obligation | — | — | `turn_ref` bound |
| answer | `act.request_information` ≥ strong; `goal.obtain_information` ≥ strong | `modality.questioned` ≥ moderate | `unknown` frame; every unresolved argument; quoted/hypothetical |
| acknowledge | `act.inform` ≥ strong; `goal.seek_acknowledgement` ≥ strong | — | `unknown` frame; quoted/hypothetical |
| repair | `act.correct` ≥ strong; `goal.repair_shared_understanding` ≥ strong | — | `unknown` frame; quoted/hypothetical |
| social contact | `act.greet` ≥ strong; `goal.maintain_social_connection` ≥ **moderate** | — | `unknown` frame; quoted/hypothetical |
| resolve required unknown | — | — | parent adopted |
| resolve contradiction | — | — | `contradiction_ref` bound |
| complete authorized request | `act.request_action` ≥ strong; `goal.cause_action` ≥ strong | `modality.requested` ≥ moderate | `unknown` frame; unresolved arguments except `subject_ref`/`recipient` with an exact unknown; authorization not denied; safety not `blocked`; no active contradiction on its hypotheses; quoted/hypothetical |
| prevent or stop | `act.request_action` ≥ **moderate**; `goal.prevent_or_stop_action` ≥ strong | — | `unknown` frame; quoted/hypothetical (never authorization- or safety-gated: stopping only reduces effects) |
| explain failure | — | — | `operation_ref`, `failure_ref` bound |

No policy requires an explicit safety `allow` (live shadow safety is typed
`unknown`); only `blocked` denies an authorized-task goal. Every reject
transition carries a `GoalRejectionDetail` naming the policy and version, the
failed check (`hypothesis_missing`, `hypothesis_disposition`,
`hypothesis_support`, `disqualifying_modality`, `unresolved_argument`,
`authorization`, `safety`, `active_contradiction`, `parent_not_adopted`,
`ungrounded_source`), and the exact hypothesis, support bands, dispositions,
argument, runtime value, related refs, and frame refs involved.

A candidate rejected for missing grounding keeps recording
`goal-adopt.required-subgoal.v1` as its adoption basis (the rule it would have
needed), exactly as in v0.2, so Action's planning conditions are unchanged.

### 26.6 Trusted reconciliation evidence (workstream A)

`goal_v0/evidence_producer.py` is pure. Callers own provider and database
access and pass typed event projections (a discriminated union):
`response_delivered`, `user_confirmation`, `user_withdrawal`,
`goal_replacement`, `deferral`, `resumption`, `tool_result`,
`argument_grounded`, `contradiction_resolved`, `verified_impossibility`, and
`scope_closure`. Every event names its exact target goal and requirement;
`produce_goal_evidence(snapshot, events)` validates each against the
predecessor snapshot and the authority table and is all-or-nothing (any
invalid event raises `GoalEvidenceProductionError`). Output is sorted by
evidence id. `trigger_for(events)` returns the single lifecycle trigger for a
standalone revision (`realization_or_outcome` for delivery, confirmation,
tool, grounding, and resolution; `explicit_correction` for withdrawal,
replacement, and user deferral; `trusted_runtime_change` for prerequisite
deferral, resumption, and impossibility; `trusted_scope_closure` for closure)
and rejects a mixed batch.

| Goal definition | Satisfied only by |
| --- | --- |
| response obligation | `response_delivered` for its own turn (the predecessor's response episode) |
| answer / acknowledge / social contact / explain failure | `response_delivered` naming the exact requirement target (topic, proposition, dialogue scope, failure) **and** a `validated_content_ref`; delivery alone is recorded as an open observation |
| repair | `user_confirmation` naming the exact proposition; a delivered repair is an open observation (the registered policy requires shared confirmation) |
| resolve required unknown | `argument_grounded` for the exact `argument_ref` |
| resolve contradiction | `contradiction_resolved` naming the exact contradiction |
| complete authorized request / prevent or stop | a final, authorized, succeeded `tool_result` for the exact requested (prevented) effect; anything else is an open observation. An avoid-mode goal may also be satisfied at `scope_closure` by the typed `prevention_monitoring_adequate=true` verdict; `false` expires it |

A delivered response may only address the response obligation of that turn or
a goal the predecessor's focus decision contained. Withdrawal and replacement
apply only to user-requested definitions. There is no event class for
generated text, selected actions, plans, sentiment, gratitude, continued
conversation, silence, or Dynamic State, so none of them can produce evidence;
in particular an assistant claim cannot satisfy a task goal.

Terminal semantics: `satisfied` = every requirement has final satisfying
evidence; `failed` = trusted impossibility with no alternate route;
`abandoned` = explicit withdrawal of that goal; `superseded` = typed
replacement naming a valid replacement goal; `suspended` = temporary
blocker/deferral; `expired` = scope closed without success; otherwise
`active`. Every transition cites the evidence that authorized it. The engine
enforces the same per-policy evidence classes (`SATISFYING_EVIDENCE_KINDS`)
on hand-built evidence, and a failed or unavailable outcome never satisfies a
requirement (one naming the evidence it contradicts becomes a dispute).

Pipeline wiring: `CognitivePipelineRequest.new_goal_evidence` remains the typed
projection point and the runner passes it unchanged into the response-turn
update. **The live-shadow compiler does not yet emit outcome events**: the
capture carries no exact per-turn outcome binding (the dialogue-memory
utterance projection has no reply-to-turn reference and no tool outcomes are
captured), and inferring one from message order would misattribute nudges and
cross-channel replies. A future capture schema version must add typed
delivery/tool outcome projections before live evidence is produced; until then
response obligations close through the trusted turn rollover and history is
released by the §26.3 precedence tiers.

### 26.7 Dynamic State boundary

`previous_dynamic_state` provides conversation ownership and snapshot
provenance only. Valence, activation, engagement, social alignment, strain,
attention, goal pressure, personality, and positive state do not influence
formation, adoption, priority, satisfaction, or focus. The ten-turn trace is
re-run with every state value altered and yields identical snapshots apart
from `previous_dynamic_state_ref`. Any future attention/interruption rule is a
separate reviewed policy change with invariance tests.

### 26.8 Verification

New tests: `test_goal_v0_adoption_policy.py` (every definition: minimum
accepted support, one band below, required/optional roles, dispositions,
unresolved arguments, quoted/hypothetical, contradiction, authorization,
safety, parent, ordering invariance, the strong-greeting/moderate-social
case), `test_goal_v0_formation_policy.py` (information request,
correction, greeting-plus-request, grounded action, exact epistemic subgoal,
ambiguous unknown, prevent/stop and negative scope, unknown predicate and
act-family mismatch, quoted instruction, two independent acts, duplicate
scenarios), `test_goal_v0_evidence_producer.py` (authority table, delivery,
assistant claims, tool finality/effect/authorization, failure/alternate
route/impossibility, withdrawal, replacement, deferral/resumption, closure,
avoid-mode monitoring, repair confirmation, grounding, contradiction
resolution, target validation, ordering invariance), and
`test_goal_v0_multi_turn_focus.py` (the ten-turn trace below, Dynamic State
invariance, and the same ten messages through the durable live-shadow
observation path).

Ten-turn trace (task creation, constraint, correction, question, new request,
constraint, action request, summary request, topic shift, closure): with the
v0.1 focus rule the turn-1 task was primary on seven of ten turns, including
the correction and two new explicit requests; now the primary is always the
current turn's request or, on constraint/closure turns, its response
obligation. The repair is primary only on its own turn and stays active until
the explicit task-scope closure expires it (never satisfied), together with
the older tasks. Each earlier response obligation expires through the trusted
rollover.

Reviewed fixture changes (`data/goal_design_cases.json`), all version-driven or
policy-driven and verified individually: version strings; a
`rejection_detail` on the five reject transitions (weak act support, quoted,
hypothetical, missing authorization, unresolved `subject_ref`); the
`scope-persistence` focus described in §26.3; and the canonical order of
`input_outcome_refs` in `disputed-evidence`. Downstream digest-only updates:
`conversational_action_design_results_v0.json` `result_digest` values and the
Realization golden request identity change solely because request digests
record the Goal versions (restoring the old version strings reproduces the
previous digests; every Action plan and semantic projection is unchanged).

### 26.9 Remaining limitations

- No live producer of outcome events exists yet (§26.6); live-shadow evidence
  is limited to the trusted turn rollover.
- Frame-path action requests with an unresolved referent carry `moderate`
  request support, so `task.complete_authorized_request` rejects them on
  support before an epistemic subgoal can form; the subgoal path is reachable
  when the act is strong.
- An unresolved answer topic is rejected (and becomes an Action planning
  condition) rather than forming a topic-clarification subgoal.
- A social question (`how are you`) has only moderate greeting support and so
  forms no adopted social goal.
- A candidate that duplicates an existing active goal is skipped, so a
  subgoal formed for it in the same turn is rejected as having no adopted
  parent (fail closed).
- Live shadow supplies no trusted `task_ref`, so task goals and task-scoped
  closure do not occur in live shadow; repair goals there are scoped to their
  turn id under task scope.
- Policies are reviewed designs, not behavioral calibration.

### 26.10 Forbidden shortcuts

Future changes to these policies must not:

- expire every task goal at the next turn, or treat a new user message as
  proof that an older goal succeeded;
- treat generated wording, selected actions, gratitude, silence, or positive
  Dynamic State as outcome evidence;
- special-case one definition (e.g. greetings) in procedural code instead of
  its manifest entry, or tune numeric scores to hide a lifecycle defect;
- choose evidence targets or subgoal parents by "first active goal" or
  stable-id order;
- merge all compound-message frames into every candidate, invent missing
  semantic arguments, or let optional hypotheses veto adoption implicitly;
- weaken authorization, safety, capability, quotation, contradiction, or
  irreversible-action gates;
- call an LLM for formation, adoption, reconciliation, priority, conflict, or
  focus, or mutate predecessor snapshots; or
- report completion from unit tests alone without the uninterrupted trace.

## 27. Addendum: requirement-resolution handler goal (v0.4, 2026-09-30)

Normative text: [`REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md`](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md) §4.

- New definition `interaction.handle_action_request` (domain `interaction`,
  mode `achieve`). Owners are user and shared, the scope is turn, and the
  default priority band is `high`. Its single
  requirement is `action_request_handled` (target role `request_ref`,
  parameter `requested_effect`), with satisfaction policy
  `goal-satisfaction.action-request-handled.v1`. The goal is
  **non-committing**: it records that the request must be handled truthfully,
  not that it will be fulfilled.
- Formation (`v0.4`): proposal rule
  `goal-propose.explicit-action-request-handling.v1` forms it for an action
  request only when there is no trusted task ref (`requires_absent_task_ref`).
  Formation and requirement resolution share one binding helper,
  `frame_target_refs`.
- Adoption (`v0.2`): the handler is adopted on the request's own act and
  speaker goal at MODERATE support, with no gates. The answer goal's
  `deferred_arguments` (`referent_phrase`) are left to requirement
  resolution instead of rejecting the goal.
- Priority (`v0.3`), satisfaction (`v0.3`), evidence policy (`v0.2`), and
  engine (`v0.4`) include the handler. Reconciliation from outcomes
  (SH-EVAL-021) is unchanged: no goal is satisfied from a planned action.
- `data/goal_design_cases.json`: the version strings were updated and the new
  definition was appended. All 36 cases pass.
