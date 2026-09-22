# Goal V0 design-case authoring brief

## Objective

Create the manually reviewable Goal V0 design fixture at:

```text
backend/app/ai/Cognitive/system/data/goal_design_cases.json
```

The fixture must validate the accepted Goal System V0 design across canonical
contract ownership, ontology, candidate formation, adoption, lifecycle,
priority, conflicts, bounded focus, scope persistence, satisfaction evidence,
pipeline placement, tracing, and failure behavior.

This task creates design evidence for later implementation. It does not
implement or evaluate a goal engine, modify the cognitive pipeline, or establish
training ground truth.

## Required references

Read these files completely before authoring cases:

1. `backend/app/ai/Cognitive/system/README.md`
   - `Goal System V0 canonical design`;
   - connected cognitive trace design;
   - interpretation ontology and scenario contract;
   - appraisal and dynamic-state boundaries;
   - action planning and planned-goal-coverage boundary; and
   - outcome observation and learning boundaries.
2. `backend/app/ai/Cognitive/system/contracts/interpretation.py`
   - canonical hypothesis, scenario, unknown, contradiction, semantic-frame,
     evidence, and version contracts.
3. `backend/app/ai/Cognitive/system/data/INTERPRETATION_DESIGN_CASES_README.md`
   - accepted interpretation keys, support/disposition semantics, and evidence
     rules.
4. `backend/app/ai/Cognitive/system/data/APPRAISAL_DESIGN_CASES_README.md`
   - current active-goal compatibility shape and one-frame-per-scenario/goal
     boundary.
5. `backend/app/ai/Cognitive/system/data/OUTCOME_OBSERVATION_SCHEMA_V0.md`
   - target-linked outcome evidence, silence handling, goal resolution, tool
     results, and immutable episode/revision semantics.
6. `backend/app/ai/Cognitive/system/data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`
   - planned effects, target/effect coverage, authorization gates, `STOP`, and
     the rule that action selection never establishes an actual outcome.
7. `backend/app/ai/Cognitive/system/data/DYNAMIC_STATE_SCHEMA_V0.md`
   - previous-state ownership, scoped goal pressure, and the boundary that
     dynamic state cannot rewrite goals.
8. Existing design fixtures in this directory for identifier, review,
   provenance, sequence, and JSON-format conventions.

If this brief conflicts with the canonical README, report the conflict in the
task summary and do not silently invent a resolution. The canonical README wins
unless the user explicitly approves a design change.

## Accepted decisions: do not reopen

The fixture validates these decisions:

1. `system/contracts/goals.py` will be the single owner of shared Goal V0
   contracts. Appraisal, Cognitive Turn, and Cognitive Pipeline must not define
   competing goal classes.
2. A speaker-goal hypothesis is not a candidate goal, and a candidate is not an
   adopted commitment.
3. Goals describe desired states. Intentions, plans, actions, realized effects,
   outcomes, safety constraints, personality, values, and dynamic state remain
   separate.
4. Runtime goal semantics come only from a small, versioned definition
   registry. Cases may not invent a new definition key.
5. V0 owners are `user`, `columbina`, `shared`, and `system`; domains are
   `task`, `epistemic`, and `interaction`; modes are `achieve`, `maintain`, and
   `avoid`; scopes are `turn`, `conversation`, and `task`; priorities are
   `low`, `moderate`, and `high`.
6. Safety, authorization, consent, privacy, truth, and capability are gates,
   not low-priority goals or learnable tradeoffs.
7. V0 lifecycle statuses are `candidate`, `active`, `suspended`, `satisfied`,
   `failed`, `abandoned`, `superseded`, `rejected`, and `expired`.
8. `satisfied`, `failed`, `abandoned`, `superseded`, `rejected`, and `expired`
   are terminal. V0 never reopens a terminal record.
9. Priority and focus changes are audited separately from lifecycle status
   transitions.
10. A response-producing turn always adopts the registered system-owned,
    turn-scoped `interaction.discharge_response_obligation` goal. Passive and
    no-response events do not enter `CognitivePipelineV0`.
11. Focus contains one primary goal and at most two compatible supporting goals.
    Non-focal active goals remain retained but do not enter appraisal or action
    scoring for that turn.
12. Material unresolved conflicts route to clarification or slow cognition.
    Stable lexical ordering is allowed only for safe, reversible, semantically
    equivalent choices.
13. Owner alone is not a universal priority hierarchy. Personality, appraisal,
    current emotion, generated rationale, and action score cannot set goal
    priority.
14. V0 has no cross-conversation or durable goals. Task goals remain bounded by
    both their trusted task reference and containing conversation.
15. A generated or selected response does not automatically satisfy a goal.
    Satisfaction requires allowlisted, target-linked evidence under the goal
    definition's reviewed satisfaction policy.
16. Silence, gratitude, sentiment, continued use, planned goal coverage,
    assistant claims, positive appraisal, and unfinalized tool events are not
    satisfaction evidence.
17. A failed action attempt does not fail its goal while another approved route
    may remain possible.
18. Goal V0 runs after Interpretation and before Appraisal. It consumes the
    previous goal snapshot and previous dynamic state, never the appraisal or
    new dynamic state produced later in the same forward pass.
19. Goal updates are immutable and revisioned. Post-action evidence produces a
    later goal-snapshot revision and never rewrites the pre-action trace.
20. A Goal V0 execution failure is fail-closed. No partial next snapshot,
    caller-injected fallback goal, or downstream Appraisal run is permitted.

## Strict task boundary

The delegated agent must:

- create only `goal_design_cases.json` unless correcting a syntax error in this
  brief is explicitly authorized;
- keep the fixture at `draft_for_human_review`;
- create manual, natural, explainable cases rather than generated label noise;
- use only the provisional definition and rule vocabularies in this brief;
- preserve ambiguity, alternative interpretations, unknowns, conflicts,
  disputed evidence, suspension conditions, and provenance;
- make every local identifier and reference resolve;
- use immutable sequence steps for lifecycle and outcome cases;
- report coverage, validation results, conflicts, and open questions after
  creating the file.

The delegated agent must not:

- implement Goal V0 or modify Perception, Interpretation, Appraisal, Dynamic
  State, Cognitive Turn, Cognitive Pipeline, action, outcome, memory, routing,
  or learning code;
- modify existing fixtures, schemas, prompts, database models, migrations,
  services, or model artifacts;
- call an LLM to manufacture ground-truth goal labels;
- train, download, or select a model;
- add a new goal definition, owner, domain, mode, scope, priority, status,
  transition, evidence kind, or rule id to make a case convenient;
- treat a speaker-goal hypothesis as automatically adopted;
- use personality or dynamic state as evidence that a goal is true, owned,
  authorized, satisfied, or higher priority;
- turn safety or authorization into an ordinary competing goal;
- mark planned coverage as actual progress or satisfaction;
- invent calibrated probabilities, continuous priority scores, goal utility,
  completion percentages, or psychological measurements;
- mark a case reviewed or approved; or
- treat the fixture as approved training data.

## File-level schema

Use one formatted JSON document:

```json
{
  "schema_version": 1,
  "goal_contract_schema_version": "goal-contract-v0.1-draft",
  "goal_ontology_version": "goal-ontology-v0.1-draft",
  "goal_lifecycle_policy_version": "goal-lifecycle-v0.1-draft",
  "goal_formation_policy_version": "goal-formation-v0.1-draft",
  "goal_priority_policy_version": "goal-priority-v0.1-draft",
  "goal_satisfaction_policy_version": "goal-satisfaction-v0.1-draft",
  "interpretation_ontology_version": 1,
  "outcome_observation_schema_version": 1,
  "evaluation_scope": "goal_system_design",
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Goal System V0",
  "goal_definitions": [],
  "cases": []
}
```

Create **30–36 cases**. Prefer minimal natural contrasts. One case may cover
several requirements, but do not combine unrelated phenomena merely to reduce
the case count.

## Provisional Goal V0 definition registry

Copy the following definitions into top-level `goal_definitions` using
structured records. Their lifecycle is `provisional_for_human_review`; inclusion
does not make them approved production definitions.

| Definition key | Domain | Mode | Allowed owners | Allowed scopes | Default priority |
| --- | --- | --- | --- | --- | --- |
| `interaction.discharge_response_obligation` | `interaction` | `achieve` | `system` | `turn` | `moderate` |
| `interaction.answer_information_request` | `interaction` | `achieve` | `user`, `shared` | `turn`, `task` | `high` |
| `interaction.acknowledge_relevant_information` | `interaction` | `achieve` | `user`, `shared` | `turn`, `conversation` | `moderate` |
| `interaction.repair_shared_understanding` | `interaction` | `achieve` | `shared` | `turn`, `task` | `high` |
| `interaction.respond_to_social_contact` | `interaction` | `achieve` | `shared` | `turn`, `conversation` | `moderate` |
| `epistemic.resolve_required_unknown` | `epistemic` | `achieve` | `columbina`, `shared` | `turn`, `task` | `moderate` |
| `epistemic.resolve_material_contradiction` | `epistemic` | `achieve` | `columbina`, `shared` | `turn`, `task` | `high` |
| `task.complete_authorized_request` | `task` | `achieve` | `user`, `shared` | `task` | `high` |
| `task.prevent_or_stop_requested_action` | `task` | `avoid` | `user`, `shared` | `turn`, `task` | `high` |
| `interaction.explain_unavailable_or_failed_operation` | `interaction` | `achieve` | `user`, `shared` | `turn`, `task` | `high` |

Each definition record uses:

```json
{
  "definition_key": "interaction.answer_information_request",
  "definition_version": "v0.1-draft",
  "lifecycle": "provisional_for_human_review",
  "domain": "interaction",
  "mode": "achieve",
  "allowed_owners": ["user", "shared"],
  "allowed_scopes": ["turn", "task"],
  "default_priority_band": "high",
  "requirement_templates": [
    {
      "requirement_key": "supported_answer_provided",
      "required_target_roles": ["topic_ref"],
      "allowed_parameter_keys": ["answer_scope"],
      "satisfaction_policy_key": "goal-satisfaction.supported-answer.v1"
    }
  ]
}
```

Use these requirement keys only:

```text
response_obligation_discharged
supported_answer_provided
relevant_information_acknowledged
shared_misunderstanding_repaired
social_contact_answered
required_argument_resolved
material_contradiction_resolved
authorized_task_completed
requested_action_prevented_or_stopped
operation_failure_explained
```

Required definition-to-requirement mapping:

| Definition | Required requirement | Required target roles | Allowed parameters | Satisfaction policy |
| --- | --- | --- | --- | --- |
| `interaction.discharge_response_obligation` | `response_obligation_discharged` | `turn_ref` | none | `goal-satisfaction.response-delivered.v1` |
| `interaction.answer_information_request` | `supported_answer_provided` | `topic_ref` | `answer_scope` | `goal-satisfaction.supported-answer.v1` |
| `interaction.acknowledge_relevant_information` | `relevant_information_acknowledged` | `proposition_ref` | none | `goal-satisfaction.relevant-acknowledgement.v1` |
| `interaction.repair_shared_understanding` | `shared_misunderstanding_repaired` | `proposition_ref` | none | `goal-satisfaction.shared-repair-confirmed.v1` |
| `interaction.respond_to_social_contact` | `social_contact_answered` | `dialogue_scope_ref` | `contact_kind` | `goal-satisfaction.social-contact-answered.v1` |
| `epistemic.resolve_required_unknown` | `required_argument_resolved` | `parent_goal_ref`, `argument_ref` | `argument_name` | `goal-satisfaction.required-argument-grounded.v1` |
| `epistemic.resolve_material_contradiction` | `material_contradiction_resolved` | `contradiction_ref` | none | `goal-satisfaction.material-contradiction-resolved.v1` |
| `task.complete_authorized_request` | `authorized_task_completed` | `task_ref` | `requested_effect` | `goal-satisfaction.authorized-task-result.v1` |
| `task.prevent_or_stop_requested_action` | `requested_action_prevented_or_stopped` | `task_ref` | `prevented_effect` | `goal-satisfaction.requested-action-stopped.v1` |
| `interaction.explain_unavailable_or_failed_operation` | `operation_failure_explained` | `operation_ref`, `failure_ref` | none | `goal-satisfaction.operation-failure-explained.v1` |

Do not add optional secondary requirements merely to make a case more detailed.
If a natural case exposes a genuinely missing goal definition or requirement,
record it in `goal_questions`; do not extend the registry.

## Case-level schema

Each ordinary case uses this logical shape:

```json
{
  "case_id": "clear-information-request-adopted",
  "title": "Explicit information request creates an adopted answer goal",
  "objective": "Separate interpreted speaker intent from deterministic goal adoption.",
  "coverage_requirements": [1, 2, 6],

  "conversation": {
    "conversation_ref": "conversation-1",
    "messages": [
      {
        "message_id": "message-1",
        "role": "user",
        "content": "Why did the estimate change?"
      }
    ],
    "focus_message_id": "message-1"
  },

  "turn_context": {
    "cognitive_turn_id": "turn-1",
    "runtime_snapshot_ref": "runtime-1",
    "response_obligation": "required",
    "conversation_status": "open",
    "task_ref": null,
    "available_capabilities": [],
    "authorization": "not_required",
    "consent": "not_required",
    "safety_decision": "allow",
    "deadline_band": "none"
  },

  "interpretation_input": {
    "interpretation_trace_ref": "interpretation-1",
    "semantic_frames": [],
    "scenarios": [],
    "hypotheses": [],
    "unknowns": [],
    "contradictions": [],
    "evidence": []
  },

  "previous_goal_snapshot": null,
  "previous_dynamic_state": {
    "snapshot_ref": "state-before-1",
    "relevant_values": []
  },
  "new_goal_evidence": [],

  "expected_goal_update": {
    "proposals": [],
    "transitions": [],
    "requirement_assessments": [],
    "priority_changes": [],
    "conflicts": [],
    "focus_decision": {},
    "snapshot": {},
    "active_goal_views": []
  },

  "expected_pipeline_effect": {
    "route": "continue_fast",
    "appraisal_goal_refs": [],
    "must_not_run_downstream": false,
    "failure": null
  },

  "forbidden_inferences": [],
  "goal_questions": [],
  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

Sequence cases replace the single expected update with:

```json
{
  "update_steps": [
    {
      "step": 1,
      "trigger": "response_turn",
      "previous_snapshot_ref": null,
      "new_input_refs": ["interpretation-1", "response-obligation-1"],
      "expected_goal_update": {}
    },
    {
      "step": 2,
      "trigger": "realization_or_outcome",
      "previous_snapshot_ref": "goal-snapshot-1",
      "new_input_refs": ["realization-evidence-1"],
      "expected_goal_update": {}
    }
  ]
}
```

Accepted step triggers are:

```text
response_turn
realization_or_outcome
explicit_correction
trusted_scope_closure
trusted_runtime_change
```

Every step creates a new immutable snapshot revision. Do not mutate the prior
step's record in place.

## Interpretation-input boundary

Use only accepted Interpretation V0 keys. Every hypothesis includes:

```text
hypothesis_id
dimension
hypothesis_key
support
disposition
supporting_evidence_refs[]
contradicting_evidence_refs[]
rationale
```

Goal formation may consider only `leading` and `retained_alternative`
hypotheses. A hypothesis still never authorizes direct adoption by itself.
Formation requires the exact combination named by a rule, such as an explicit
request act, requested modality, grounded target, and compatible trusted
runtime state.

Cases must distinguish:

- explicit direct requests from weak or ambiguous inferred preferences;
- literal current speech from quoted, hypothetical, or reported requests;
- request for information from request for an external action;
- request to cause an action from request to prevent or stop it;
- user-provided facts from authorization, consent, and capability facts;
- unknown required arguments from out-of-scope or unknowable content; and
- competing scenarios from one consolidated conclusion.

Do not convert `interpretation.goal.unknown` into a generic clarification goal
unless an unresolved argument is decision-relevant to an already adopted goal.

## Previous-snapshot contract

An existing `previous_goal_snapshot` includes:

```text
goal_snapshot_id
revision
supersedes_goal_snapshot_id
conversation_ref
records[]
requirement_assessments[]
active_goal_refs[]
focal_goal_refs[]
versions
```

Each existing goal record includes at least:

```text
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
```

Cases with an initial turn use `previous_goal_snapshot: null`. Cases with an
existing conversation or task must provide the exact predecessor. Never copy a
goal from another conversation or silently reset its creation time.

## Provisional formation and adoption rule vocabulary

Use only these proposal rule ids:

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

Use only these adoption/rejection rule ids:

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

The fixture may reveal that a rule is too broad or too narrow, but it must not
invent another rule. Record the issue for human review.

Every accepted response turn emits ordered `propose` and `adopt` transitions
for its response-obligation goal, even if other semantic goals already exist.
Deduplication may reuse an existing non-turn goal only when its definition,
owner, scope, scope reference, targets, parameters, and material requirements
match exactly. Turn-scoped response obligations are never reused across turns.

## Lifecycle transition contract

Use only these status-changing transitions:

| From | Transition | To |
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

Every transition includes:

```text
transition_id
goal_id
sequence
from_status
to_status
transition_kind
rule_id
cognitive_turn_id
previous_snapshot_ref
resulting_snapshot_ref
source_refs[]
evidence_refs[]
outcome_refs[]
replacement_goal_ref or null
reason_code
```

`evidence_refs` and `outcome_refs` are separate namespaces. Trusted runtime
facts, semantic evidence, corrections, deferrals, and scope-closure facts stay
in `evidence_refs` only. Evidence that represents an actual observed outcome
also carries a canonical `outcome_id`; that same identifier, never the evidence
id or `episode_or_turn_ref`, is used by both `GoalTransition.outcome_refs` and
`GoalSnapshot.input_outcome_refs`. `episode_or_turn_ref` remains provenance.

Use these lifecycle rule ids:

```text
goal-lifecycle.suspend-higher-incompatible-goal.v1
goal-lifecycle.suspend-missing-temporary-prerequisite.v1
goal-lifecycle.suspend-explicit-deferral.v1
goal-lifecycle.resume-blocker-resolved.v1
goal-lifecycle.satisfy-all-requirements.v1
goal-lifecycle.fail-verified-impossibility.v1
goal-lifecycle.abandon-explicit-withdrawal.v1
goal-lifecycle.supersede-material-replacement.v1
goal-lifecycle.expire-turn-scope.v1
goal-lifecycle.expire-conversation-scope.v1
goal-lifecycle.expire-task-scope.v1
```

Priority and focus changes are not represented as `active -> active`
transitions. Requirement assessment changes also remain separate immutable
records.

## Requirement assessment and evidence contract

Accepted requirement statuses are:

```text
open
satisfied
impossible
disputed
```

Every assessment includes:

```text
assessment_id
goal_ref
requirement_ref
status
policy_key
evidence_refs[]
conflict_refs[]
previous_assessment_ref or null
rationale
```

When an assessment is `disputed`, every `conflict_refs` entry resolves to a
typed evidence-conflict record in the same update:

```text
conflict_id
status (`active` or `resolved`)
left_evidence_ref
right_evidence_ref
resolution_ref or null
rationale
```

Both evidence references must resolve in that update. An active conflict has a
null resolution; a resolved conflict has a non-null resolution reference.

Use only these evidence kinds:

```text
realization_validation
semantic_grounding
explicit_user_confirmation
finalized_tool_result
trusted_runtime_fact
reviewed_outcome
```

Every evidence item includes source type, source reference, episode or turn
reference, target goal and requirement references, provenance, finality, and a
short factual meaning. Evidence without the exact goal/requirement link cannot
change that requirement.

Use these satisfaction policy keys:

```text
goal-satisfaction.response-delivered.v1
goal-satisfaction.supported-answer.v1
goal-satisfaction.relevant-acknowledgement.v1
goal-satisfaction.shared-repair-confirmed.v1
goal-satisfaction.social-contact-answered.v1
goal-satisfaction.required-argument-grounded.v1
goal-satisfaction.material-contradiction-resolved.v1
goal-satisfaction.authorized-task-result.v1
goal-satisfaction.requested-action-stopped.v1
goal-satisfaction.operation-failure-explained.v1
```

An `achieve` goal is satisfied only when every required requirement is
satisfied. An impossible requirement fails the goal only when no approved
alternative route remains. A disputed requirement blocks satisfaction.

An `avoid` or future `maintain` goal requires trusted scope closure and adequate
monitoring evidence. No observed violation is insufficient by itself. If
monitoring coverage is incomplete at closure, expect `expired`, not
`satisfied`.

## Priority contract

Accepted bands are `low`, `moderate`, and `high`. Use only these priority rule
ids:

```text
goal-priority.definition-default.v1
goal-priority.current-explicit-request.v1
goal-priority.accepted-shared-commitment.v1
goal-priority.required-response-obligation.v1
goal-priority.trusted-deadline.v1
goal-priority.blocking-required-subgoal.v1
goal-priority.explicit-deferral.v1
goal-priority.explicit-resumption.v1
goal-priority.inherit-parent-band.v1
```

Every non-default change emits:

```text
priority_change_id
goal_ref
old_priority_band
new_priority_band
rule_id
evidence_refs[]
rationale
```

Do not infer a priority change from emotional intensity, goal pressure,
personality, verbose wording, action utility, or ownership alone. A previous
dynamic-state value may appear only in a reviewed attention/interruption case
and may not change priority, ownership, adoption, or satisfaction.

## Conflict and focus contract

Accepted pair classifications are:

```text
compatible
resource_competition
mutually_exclusive
unknown
```

Each assessed pair emits:

```text
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
```

Use only these conflict rule ids:

```text
goal-conflict.compatible-effects.v1
goal-conflict.shared-resource.v1
goal-conflict.opposed-required-effects.v1
goal-conflict.unknown-relation.v1
goal-conflict.explicit-supersession.v1
goal-conflict.higher-priority.v1
goal-conflict.more-specific-scope.v1
goal-conflict.preserve-older-commitment.v1
goal-conflict.material-tie-route.v1
goal-conflict.benign-stable-order.v1
```

Conflict resolution follows the canonical README order. Hard gates are applied
before ranking. A material or irreversible tie routes to `clarify` or `slow`;
it is not resolved by goal id. Stable goal-id ordering may appear only in a
case that explicitly establishes safe, reversible, semantically equivalent
alternatives.

Every successful update emits one `GoalFocusDecision`:

```text
focus_decision_id
primary_goal_ref
supporting_goal_refs[]
non_focal_active_goal_refs[]
rule_ids[]
evidence_refs[]
route
rationale
```

There is exactly one primary goal and zero to two supporting goals. Supporting
goals must be compatible with the primary and have a requirement that the
current response can advance. Only primary and supporting goals are projected
as `ActiveGoalView` objects for Appraisal.

## Scope and persistence contract

Every non-terminal goal has exactly one scope and non-empty scope reference:

```text
turn         -> cognitive_turn_id
conversation -> conversation_ref
task         -> trusted task_ref
```

Test that:

- a turn-scoped response obligation receives a new identity every turn;
- a conversation-scoped goal retains identity and creation provenance across
  revisions in the same conversation;
- a task-scoped goal retains identity only while the exact task and containing
  conversation remain valid;
- a same-topic message in a new conversation does not load the old goal;
- explicit withdrawal produces `abandoned`, scope closure produces `expired`,
  verified impossibility produces `failed`, and replacement produces
  `superseded`; and
- later recurrence after any terminal status creates a new goal linked by
  provenance rather than reopening the old record.

## Previous dynamic-state boundary

Supply a minimal previous-state reference in every case. Add scoped values only
when needed for a required contrast. Goal V0 may use previous state for a
reviewed interruption or attention decision, but cases must keep these
invariants:

- state never proposes an unsupported goal;
- goal pressure does not increase priority;
- curiosity does not create an epistemic goal without a blocking registered
  requirement;
- social alignment does not create a shared goal;
- strain does not establish user hostility, withdrawal, or conflict;
- engagement does not adopt a goal; and
- the dynamic state produced later in the same turn is unavailable to Goal V0.

At least one paired case must hold every non-personality input constant while
varying a personality profile upstream. Goal proposals, transitions, priority,
conflicts, focus, and satisfaction must remain identical.

## Expected snapshot and downstream contract

Each expected snapshot includes:

```text
schema_version
goal_snapshot_id
revision
supersedes_goal_snapshot_id
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
```

The initial snapshot uses revision `1` and a null predecessor. Every later
snapshot increments its exact predecessor by one. Identical normalized inputs
must produce semantically identical output; design ids may be fixture-stable
instead of prescribing a future UUID algorithm.

Every evidence conflict referenced by a snapshot requirement assessment is
stored in that same snapshot. Snapshot history copies evidence-conflict records
without rewriting them, using the same rule as lifecycle transitions.

`active_goal_views` contain only canonical projections of the focal active
records. Each view includes:

```text
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

Appraisal goal references must equal the focus decision. Candidates,
suspended goals, terminal goals, and non-focal active goals must not appear in
the downstream projection.

## Failure-case contract

Accepted Goal V0 failure phases are:

```text
validation
evidence_reconciliation
lifecycle_update
candidate_formation
adoption
priority_assignment
conflict_resolution
focus_selection
snapshot_validation
```

A failure expectation uses:

```json
{
  "failure_phase": "validation",
  "pipeline_failed_stage": "goal_update",
  "safe_message_category": "invalid_goal_input",
  "previous_snapshot_remains_authoritative": true,
  "partial_snapshot_returned": false,
  "downstream_appraisal_runs": false,
  "caller_fallback_goals_allowed": false
}
```

Failure matrices embed complete baselines locally. Each variant selects one by
`baseline_key`, and its mutation pointer is resolved against that local
object before the mutation is applied. Referencing another fixture case as a
baseline is forbidden.

Required failure cases include wrong-conversation predecessor, skipped revision,
unknown definition/version, malformed scope reference, invalid transition,
unresolved evidence reference, duplicate goal identity, non-final tool evidence
presented as completion, invalid focus projection, and engine-emitted snapshot
that fails revalidation.

Do not include raw user content in a safe error expectation.

## Forbidden-inference examples

Every case lists at least one case-specific forbidden inference. Examples:

```text
The user may want information, therefore Columbina adopted an answer goal.
The user quoted a command, therefore an action goal is active.
The user is enthusiastic, therefore the goal priority is high.
Columbina is curious, therefore an epistemic goal exists.
The plan includes an answer, therefore the user understands it.
The assistant said the task succeeded, therefore the task goal is satisfied.
The tool attempt failed, therefore the task goal is failed.
The user said thanks, therefore every active goal is satisfied.
No violation was observed, therefore an avoidance goal is satisfied.
A shared goal exists because the conversation is friendly.
The user-owned goal bypasses authorization because it has high priority.
The system coordination goal is a safety policy.
A non-focal goal disappears from persistence.
A material tie is resolved lexically.
The new dynamic state changes priority during the same forward turn.
Conversation closure silently counts as goal success.
```

## Required coverage

Every numbered requirement must appear in at least one case's
`coverage_requirements`.

### Formation, adoption, and ontology

1. required response obligation produces a system-owned turn candidate and
   ordered adoption;
2. an explicit grounded information request adopts an answer goal;
3. an explicit authorized action request adopts a task goal;
4. an explicit prevent/stop request adopts an avoidance task goal;
5. relevant supplied information may create an acknowledgement goal without
   creating a task goal;
6. weak inferred speaker desire remains a hypothesis and is not adopted;
7. quoted request is rejected as a current commitment;
8. hypothetical request is rejected as a current commitment;
9. missing target grounding prevents semantic-goal adoption while the response
   obligation remains valid;
10. a required unknown creates a parent-linked epistemic subgoal;
11. an irrelevant unknown creates no epistemic goal;
12. a material contradiction creates a bounded resolution goal;
13. friendly tone alone does not create a shared goal;
14. explicit mutual commitment permits a shared goal;
15. an unregistered definition produces a typed failure rather than an ad-hoc
   goal.

### Lifecycle and persistence

16. proposal and adoption occur as separate ordered transitions;
17. temporary missing authorization or prerequisite suspends an active goal;
18. verified blocker resolution resumes the same goal without resetting age;
19. explicit user withdrawal abandons the matching goal;
20. a materially revised requested outcome supersedes the earlier goal and
   references the replacement;
21. verified impossibility fails a goal only when no approved route remains;
22. one failed action attempt leaves the goal active when another route exists;
23. turn-scope closure expires an unresolved turn goal;
24. conversation closure expires unresolved conversation and task goals;
25. conversation-scoped identity persists across turns in one conversation;
26. task-scoped identity remains tied to the exact trusted task reference;
27. the same topic in a new conversation does not restore a V0 goal;
28. recurrence after a terminal state creates a new linked goal rather than
   reopening history.

### Priority, conflict, and focus

29. an explicit current task takes focus over a lower standing interaction
   goal while both remain retained;
30. owner alone does not determine priority;
31. personality variation leaves priority and focus unchanged;
32. previous goal pressure or curiosity cannot promote a goal band;
33. a trusted deadline changes priority with an audited rule and evidence;
34. explicit deferral lowers eligibility through suspension rather than silent
   deletion;
35. compatible goals may occupy primary and supporting focus together;
36. resource competition preserves the non-focal active goal;
37. higher priority resolves a reversible incompatible pair;
38. equal-priority material conflict routes to clarification or slow cognition;
39. safe equivalent alternatives may use the recorded stable-order rule;
40. focus contains one primary and no more than two supporting goals;
41. a fourth compatible active goal remains non-focal and is excluded from
   Appraisal projection;
42. hard safety, consent, authorization, privacy, truth, and capability gates
   cannot be defeated by goal ranking.

### Requirement evidence and terminal decisions

43. validated delivered answer satisfies `supported_answer_provided` without
   claiming user understanding;
44. selected or planned answer alone does not satisfy the requirement;
45. explicit repair confirmation satisfies a shared repair requirement;
46. delivery of a repair attempt without confirmation leaves repair open;
47. grounded required argument satisfies only the linked epistemic requirement;
48. finalized authorized tool success satisfies the exact task requirement;
49. unfinalized, mismatched, or unauthorized tool result does not satisfy it;
50. explicit user confirmation cannot override contradictory objective evidence
   and produces `disputed`;
51. gratitude, positive sentiment, continued use, and silence remain
   non-satisfaction evidence;
52. all required subrequirements must be satisfied before the goal transitions;
53. one satisfied subrequirement represents partial progress without a numeric
   completion percentage;
54. avoidance satisfaction requires trusted scope closure and adequate
   monitoring;
55. incomplete avoidance monitoring expires rather than satisfies the goal;
56. response obligation is satisfied only by validated realization and
   delivery.

### Engine, trace, and failure boundaries

57. Goal V0 consumes previous state and cannot consume same-turn new state;
58. evidence reconciliation happens before new candidate formation;
59. post-action evidence creates a later snapshot and does not rewrite the
   pre-action snapshot;
60. snapshot revision and predecessor identity are exact;
61. every transition, assessment, priority change, conflict, and focus decision
   cites resolving rules and evidence;
62. only focal active records produce canonical `ActiveGoalView` projections;
63. Appraisal references only projected focal goals;
64. no-response input is rejected from the response-producing pipeline;
65. wrong-conversation or skipped-revision predecessor fails validation;
66. invalid lifecycle transition fails without a partial snapshot;
67. unresolved or cross-case evidence reference fails validation;
68. goal update failure prevents Appraisal and Dynamic State execution;
69. previous snapshot remains authoritative after failure; and
70. safe failure output contains no raw conversation content.

One natural case may cover several requirements. Do not add meaningless fields
solely to claim coverage.

## Recommended case set

A coherent 32–35 case suite can include:

1. response obligation only;
2. direct information request;
3. weak indirect information desire;
4. quoted action request;
5. hypothetical action request;
6. authorized grounded task request;
7. task request missing authorization;
8. request with missing required argument and derived epistemic subgoal;
9. irrelevant unknown contrast;
10. explicit stop/prevent request;
11. relevant information acknowledgement;
12. social greeting/contact without invented durable relationship goal;
13. explicit shared repair commitment;
14. material contradiction resolution;
15. temporary suspension and later resumption sequence;
16. explicit withdrawal and abandonment;
17. corrected target and supersession sequence;
18. failed attempt with alternate route;
19. verified impossibility;
20. turn and conversation scope-expiration contrast;
21. cross-turn conversation persistence;
22. same-topic new-conversation isolation;
23. standing-versus-task focus;
24. compatible primary plus two supporting goals and one non-focal goal;
25. reversible priority-resolved conflict;
26. material equal-priority conflict routed to clarification;
27. benign stable-order tie;
28. personality/state invariance pair;
29. delivered answer satisfaction sequence;
30. repair attempt versus confirmed repair contrast;
31. finalized tool success versus pending/mismatched tool result;
32. disputed subjective confirmation versus objective result;
33. avoidance monitoring and closure sequence;
34. silence/gratitude non-satisfaction contrast; and
35. grouped validation/fail-closed cases, split further if necessary for clear
    phase ownership.

This list is guidance, not permission to skip any numbered coverage item.

## Review and validation procedure

After authoring the fixture:

1. Validate JSON syntax with a standard JSON parser.
2. Confirm 30–36 cases and unique case, definition, goal, requirement,
   transition, assessment, priority-change, conflict, focus-decision, snapshot,
   interpretation, evidence, outcome, message, runtime, and state identifiers
   within their applicable scopes.
3. Verify every goal definition matches the provisional registry exactly.
4. Verify every goal uses an allowed definition, owner, domain, mode, scope,
   scope reference, priority, requirement, and satisfaction policy.
5. Verify every interpretation key, support, disposition, scenario, evidence,
   semantic frame, unknown, and contradiction follows Interpretation V0.
6. Verify every response-producing success case contains one new turn-scoped
   response-obligation goal with ordered propose/adopt transitions.
7. Verify every lifecycle transition is legal and its from-status matches the
   prior record.
8. Verify terminal records never transition again.
9. Verify priority changes use only accepted rules and do not cite personality,
   appraisal, generated rationale, or current-turn new state.
10. Verify every conflict pair and resolution follows the canonical decision
    order.
11. Verify every successful focus has exactly one primary, at most two
    supporting goals, and only compatible supporting goals.
12. Verify non-focal active goals remain in the snapshot but not in
    `active_goal_views` or Appraisal references.
13. Verify every requirement assessment cites an allowlisted evidence kind and
    the exact goal and requirement.
14. Verify planned coverage, silence, gratitude, sentiment, and unfinalized tool
    events never satisfy a goal.
15. Verify sequence steps increment the exact predecessor revision and never
    mutate an earlier snapshot.
16. Verify turn, conversation, and task scope references and closure behavior.
17. Verify no case introduces cross-conversation persistence.
18. Verify every failure uses an accepted phase, exposes only a safe category,
    returns no partial snapshot, and prevents downstream execution.
19. Verify all references resolve within the case or to its explicitly supplied
    predecessor. Cross-case references are forbidden.
20. Verify all 70 coverage requirements are represented and produce a coverage
    table in the task summary.
21. List every non-empty `goal_questions` entry without silently resolving it.
22. Run `git diff --check` and report the result.

Do not mark a case or definition reviewed or approved. Human review happens
after fixture creation.

## Completion criteria

The delegated task is complete only when:

- one valid `goal_design_cases.json` fixture exists at the required path;
- it contains 30–36 manual cases;
- the provisional registry is reproduced without extension;
- all 70 required contrasts are covered;
- every reference resolves and every sequence preserves immutable history;
- lifecycle, priority, conflict, focus, scope, satisfaction, and failure
  expectations obey the canonical README;
- ambiguity, disputes, non-focal goals, and missing evidence remain explicit;
- no implementation or unrelated file changed;
- every case and definition remains pending human review; and
- the task summary reports validation, coverage, conflicts, and open questions.

## Required task summary

Report:

- output path and case count;
- sequence-case, conflict-case, satisfaction-case, scope-case, personality/state
  invariance, and failure-case counts;
- coverage by definition, owner, domain, mode, scope, priority, lifecycle status,
  transition kind, requirement status, evidence kind, conflict class, route, and
  failure phase;
- a numbered 1–70 coverage table mapping every requirement to case ids;
- all unresolved `goal_questions`;
- any conflicts found among the required source documents;
- JSON parsing and reference-validation results;
- `git diff --check` result; and
- confirmation that only `goal_design_cases.json` was created or modified.

## Suggested delegated-agent prompt

```text
Read backend/app/ai/Cognitive/system/data/GOAL_DESIGN_CASES_README.md completely,
then read every required reference it names. Create only
backend/app/ai/Cognitive/system/data/goal_design_cases.json. Follow the brief
exactly, keep all definitions and cases draft/pending human review, validate JSON
and every reference, cover requirements 1 through 70, run git diff --check, and
report the required summary. Do not implement Goal V0 or modify any other file.
```
