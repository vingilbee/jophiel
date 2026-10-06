# Action Selection V0 accepted design

Status: accepted for deterministic shadow implementation on 2026-09-25

This document records the planner audit and closes the design decisions required
to implement Action Selection V0. It is normative where older action prose,
registry notes, or fixture shorthand are ambiguous. The detailed scoring model
and action semantics remain in `README.md` and
`data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`.

The implementation handoff is `ACTION_SELECTION_V0_IMPLEMENTATION.md`.

Acceptance here means that the architecture, contracts, policy boundaries,
initial registry subset, failure behavior, and test obligations are fixed well
enough for a shadow implementation. It does not approve live behavior, create
training truth, calibrate human conversational preferences, implement language
realization, or authorize external actions.

## 1. Audited artifacts

The audit covered:

- the canonical action design in `README.md`;
- all 75 definitions and the transition-effect manifest in
  `data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`;
- the authoring and validation contract in
  `data/CONVERSATIONAL_ACTION_DESIGN_CASES_README.md`;
- all 29 cases in `data/conversational_action_design_cases.json`;
- the current canonical Interpretation, Goal, Appraisal, Dynamic State,
  Cognitive Turn, and Pipeline V0 contracts.

The fixture contains 29 unique cases, all nine families, all six question forms,
one- through four-move plans, alternate acceptable plans and orders, early and
late `STOP` contrasts, missing-state handling, hard-gate cases, realization and
selection attribution cases, and uncertain credit assignment. It references 51
registered keys: 39 occur in at least one acceptable plan and 12 occur only as
disfavored or hard-invalid contrasts. The remaining 24 keys are uncovered.

## 2. Canonical stage placement

Action Selection V0 runs after the current-turn Dynamic State update:

```text
trusted CognitivePipelineRequest
    -> Perception V0
    -> Interpretation V0
    -> Goal V0
       consumes previous Dynamic State
    -> Appraisal
    -> current-turn Dynamic State
    -> Action Selection V0
    -> selected abstract ActionPlan
```

Action Selection may use the current-turn appraisal and Dynamic State because
both are complete before planning starts. It must not mutate or feed values back
into any earlier stage during the same turn. A later metacognitive controller
may route to slow cognition before realization, but slow cognition is not part
of Action Selection V0.

Language realization, tool execution, outcome observation, reflection, memory
writing, and learning remain later stages. Selecting a move records an intended
and planned effect only; it never establishes that wording was produced, a tool
ran, a user was helped, an unknown was resolved, or a goal was satisfied.

## 3. Canonical ownership

One new shared contract module must own all cross-stage action types:

```text
system/contracts/actions.py
```

The engine, registry compiler, construction rules, gates, feature extraction,
scoring, transitions, and fixture adapter belong under:

```text
system/action_selection_v0/
```

No duplicate action contract may live in Appraisal, Cognitive Turn, Pipeline,
the registry loader, or a prompt model. Downstream realization must import the
canonical selected-plan projection rather than defining another action shape.

The JSON design fixture is not a production dependency. Production code must
compile a versioned registry from owned definitions or static typed data; it
must not load expected plans or rationales from the fixture.

## 4. Canonical request

`ActionSelectionRequest` must be immutable, extra-forbid, JSON-round-trippable,
and contain exact authoritative values or references for:

- `cognitive_turn_id` and `conversation_ref`;
- the exact current `InterpretationEnvelope`;
- the exact current `GoalSnapshot`;
- the exact focal `ActiveGoalView` projections produced by Goal V0;
- the exact current appraisal frames and aggregate;
- the exact current `StateSnapshot`;
- one trusted runtime projection containing response obligation, capability,
  authorization, consent, privacy/export control, safety decision, policy
  records, task contract, and current tool-event facts;
- an explicitly versioned, bounded action-personality projection or an explicit
  unknown/neutral projection;
- authorized prior-action, prior-realization, and observed-outcome references,
  when available;
- registry, construction, gate, feature, scorer, and planner versions.

The request must not accept caller-authored active goals, free-form relationship
profiles, inferred permissions, raw hidden memory, expected plans, generated
rationales, or unversioned personality numbers. Relationship input is explicitly
unknown in V0 because no canonical relationship subsystem has been accepted.

The Pipeline is the composition owner. It must derive the action request from
the exact stage outputs already stored in its trace. Callers may not substitute
look-alike copies with different identifiers, revisions, or provenance.

## 5. Typed targets and parameters

Every grounded candidate uses typed references:

```text
ActionTargetRef
  role
  target_type
  ref
  revision_ref, when the target is revisioned
```

Targets resolve against the current Interpretation, Goal, Appraisal, State,
runtime, prior-action, or authorized-memory reference index. Literal fixture
labels such as `topic`, `reason`, `option`, `steps`, `answer`, or `next_step`
are authoring shorthand, not canonical runtime references. The implementation
fixture adapter must translate only explicit input fields into typed objects; it
must not infer semantics from case IDs, prose rationales, expected plans, or
review notes.

Semantic parameters are validated JSON values whose schema is owned by the
versioned action definition. Reference-bearing values use explicit `*_ref` or
`*_refs` fields. `question_form`, bounded scale endpoints, `content_scope`,
`conditionality`, `commitment_level`, `repair_scope`, and reviewed enumerations
may be literal typed values. Response wording, tone, verbosity, and vocabulary
belong to realization constraints rather than semantic action parameters.

## 6. Definition and lifecycle contract

Each immutable `ActionDefinition` contains:

- `action_key` and `definition_version`;
- family, function, description, and optional parent definition;
- target schema and cardinality;
- parameter schema and conditional requirements;
- evidence and structural requirements;
- declared planned effects and target-binding rules;
- hard inhibitors;
- allowed policy-feature relations;
- outcome dimensions;
- origin and lifecycle status.

Lifecycle values are:

```text
candidate -> reviewed -> shadow -> approved -> deprecated -> retired
```

Only a new immutable definition version may change semantics or gates.
`candidate` and `reviewed` definitions may be validated but not selected.
`shadow` definitions may be selected only by the isolated shadow pipeline.
`approved` is reserved for a later live-promotion decision. Deprecated and
retired definitions cannot enter new plans, although historical traces remain
readable.

For the initial implementation, the 39-key subset below is `shadow`. Every
other registry key remains `candidate`.

## 7. Initial shadow-selectable subset

The following keys have at least one positively supported plan in the audited
fixture and are accepted for shadow construction, scoring, and selection:

```text
advise_or_influence.advise
commit_or_coordinate.accept_request
commit_or_coordinate.accept_suggestion
commit_or_coordinate.decline_request
commit_or_coordinate.decline_suggestion
commit_or_coordinate.offer
commit_or_coordinate.propose_plan
commit_or_coordinate.state_next_step
give_feedback.acknowledge
give_feedback.paraphrase
give_feedback.reflect
give_feedback.signal_misunderstanding
manage_dialogue.close
manage_dialogue.greet
manage_dialogue.introduce_topic
manage_dialogue.repair
manage_dialogue.return_to_topic
manage_dialogue.self_correct
provide_information.answer
provide_information.correct
provide_information.disclose_uncertainty
provide_information.exemplify
provide_information.explain
seek_information.check_understanding
seek_information.clarify
seek_information.disambiguate
seek_information.explore_experience
seek_information.explore_goal
seek_information.explore_preference
seek_information.explore_reason
seek_information.request_evidence
seek_information.request_feedback
seek_information.verify
social_play.joke
social_play.playful_reframe
support_socially.apologize
support_socially.congratulate
support_socially.reassure
support_socially.validate_experience
```

Registry v0.2 (Collaborative Content and Inquiry V0) adds two shadow-selectable
definitions beyond the audited fixture; see
`data/CONVERSATIONAL_ACTION_REGISTRY_V0.md` ("Collaborative Content V0
additions"):

```text
collaborate.deliver_artifact
seek_information.inquire
```

These 12 keys occur only as a disfavored or hard-invalid contrast and therefore
remain non-selectable candidates until they receive a positive case:

```text
advise_or_influence.set_boundary
advise_or_influence.warn
commit_or_coordinate.promise
express_stance.agree
express_stance.challenge
express_stance.disagree
give_feedback.confirm_understanding
give_feedback.validate
provide_information.elaborate
provide_information.inform
provide_information.summarize
support_socially.comfort
```

The 24 keys listed as uncovered in the registry also remain candidates. This
keeps V0 bounded without deleting potentially useful distinctions.

## 8. Candidate construction

Construction enumerates only installed definitions, binds current typed targets
and required parameters, attaches source provenance, canonicalizes exact
duplicates, and records every attempted result. It favors semantic recall over
preference but cannot invent targets or parameters.

Construction results are:

```text
uninstantiable  required target, parameter, or evidence could not be bound
instantiated    complete grounded candidate ready for hard-gate validation
```

The constructor must not use action scores, personality preference, case
expectations, wording, or policy rationale to decide semantic applicability.
One definition bound to distinct target sets creates distinct candidates.

## 9. Deterministic hard gate

Every instantiated candidate passes through all six ordered, versioned gate
categories:

1. registry and lifecycle;
2. schema and reference integrity;
3. truth and evidence;
4. capability;
5. authorization, consent, and privacy; and
6. application safety policy.

The result records every violation and every mandatory realization constraint.
A gate may preserve a truthful action by attaching constraints such as
`preserve_uncertainty` or `forbid_unsupported_guarantee`; it may not silently
change the action. Ordinary awkwardness, repetition, humor timing, warmth,
verbosity, or low utility are policy disadvantages rather than hard failures.

Gate results are `blocked`, `shadow_only`, or `passed`. In the isolated V0
runner, an installed `shadow` definition is eligible and its trace records that
selection authority is shadow-only. The same definition must be non-selectable
at a live boundary until separately promoted to `approved`.

## 10. Sequential planning and `STOP`

Planning starts from an immutable turn context and an empty immutable
`PartialPlanState`. Each step reconstructs applicable candidates, gates them,
computes features, scores them, selects one move or `STOP`, and produces a new
partial state. Earlier partial states are never mutated.

The transition records planned effects only. It updates exact target-effect
coverage, residual needs, planned goal coverage, unknown-handling intent,
contradiction-handling intent, action/family counts, accumulated costs, and a
canonical state signature.

The accepted `action-policy-v0.1` feature manifest, marginal-utility formula,
shared move overhead, repetition schedule, and zero-valued `STOP` baseline in
`README.md` are normative. Numerical weights are bootstrap policy constants,
not psychological claims or learned truth.

The selector continues only when the best candidate has score greater than
zero. Deterministic ties resolve by:

1. greater expected goal progress;
2. greater target priority;
3. lower soft-risk cost;
4. lower response cost; and
5. ascending canonical `(action_key, target_refs, normalized_parameters)`.

A non-empty plan contains exactly one primary move; all other moves are
supporting. Primary designation is derived from highest direct focal-goal
coverage, then the same deterministic tie rules. It is not supplied by an
untrusted caller.

An empty plan plus `STOP` is valid only when no response is required. When a
response is required and no candidate has positive marginal utility, the engine
returns typed `no_viable_plan`; it must not label the empty plan conversationally
preferred. Timeout, cancellation, repeated state signature, and defensive cycle
limits are operational failures, never successful `STOP` evidence.

## 11. Canonical outputs and trace

`ActionSelectionResult` contains:

- the immutable completed `ActionPlan`;
- exact selected `GroundedActionMove` instances;
- the primary and supporting move references;
- every planning-step decision record;
- construction and gate outcomes for considered candidates;
- normalized features, missing-value markers, provenance, signed
  contributions, scores, ties, and tie-break fields;
- partial-state before/after references and deltas;
- termination kind;
- registry, construction, gate, feature, scorer, and planner versions.

The trace records computations and references, not generated explanations. A
human-readable diagnostic may be derived separately but is not authoritative.
All collections have stable ordering and all identifiers/revisions are unique.
The result must survive JSON round-trip validation without losing enum,
provenance, ordering, or immutability semantics.

Pipeline and Cognitive Turn traces must retain the exact action result and
versions. Appraisal, Dynamic State, or Goal contracts must not be modified to
embed action-owned fields.

## 12. Failure contract

Failures are typed and fail closed. Required phases are:

```text
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

An `ActionSelectionError` records cognitive turn, phase, stable category/code,
safe message, and chained cause. No partial plan becomes authoritative after a
failure. Realization, tool execution, outcome handling, and learning must not
run. Operational fallback, if later added, occurs outside the successful
cognitive trace and must be labeled as fallback.

## 13. Personality, state, and relationship boundaries

Personality affects only bounded policy affinity after construction and hard
gating. It cannot create facts, goals, targets, permissions, or eligibility. If
the deterministic personality mapper is unavailable, V0 uses a versioned
explicit-unknown projection whose numerical contribution is neutral and whose
missing status remains visible in the trace.

Appraisal and Dynamic State values retain known, unknown, disputed,
provisional, and not-applicable semantics. Numerical neutral fallback is used
only for scoring and must not overwrite provenance or status.

Relationship context is excluded from V0 scoring. Existing fixture labels such
as `supportive`, `collaborative`, or relevant-history prose are scenario
descriptions only. They cannot change pass/fail results or scores until a
canonical relationship contract exists.

## 14. Shared-signal decision

`candidate.manage_dialogue.establish_shared_signal` is rejected as a
conversational-action definition. Establishing a persistent phrase-to-behavior
mapping changes stored interaction protocol or relationship memory; it is not
the intended response move itself.

V0 may acknowledge the request, clarify scope/duration, and state that durable
persistence is unavailable. A future consented interaction-protocol or
relationship-memory subsystem may own the convention record. The action system
may later reference that record but must not own, infer, or silently persist it.

## 15. Fixture authority and migration

The 29-case fixture is accepted as architecture and regression design evidence.
Its ordinal plan preferences remain pending behavioral calibration and are not
training labels. One case may have several acceptable plans.

Because the fixture predates canonical Goal V0, some fields are authoring
shorthand: `goal_context`, compact appraisal/state maps, loose targets, and
literal semantic parameters. The implementation must create an explicit test
adapter or a schema-v2 fixture projection that constructs canonical upstream
objects. That migration must obey all of these rules:

- expected plans never become engine inputs;
- rationales, case IDs, titles, and diagnostic prose never determine policy;
- all goal inputs come from valid `GoalSnapshot` and `ActiveGoalView` objects;
- all appraisal and state inputs preserve their canonical statuses;
- every target/support reference resolves through an explicit typed index;
- relationship placeholders have no policy effect;
- generated IDs may be injected only for deterministic test identity, never
  semantic choice;
- complete plans, steps, candidates, gates, features, and traces are compared;
- failure variants execute real phase boundaries; and
- changing expected output cannot change engine output.

The shorthand fixture itself must not force duplicate production contracts.

## 16. Implementation acceptance gates

Action Selection V0 is not complete until tests prove:

1. canonical contracts are immutable, extra-forbid, versioned, and round-trip;
2. the installed registry exactly matches the accepted 39-key shadow subset;
3. every other provisional key is non-selectable;
4. construction binds only explicit typed inputs;
5. all six hard-gate categories and realization constraints are exercised;
6. every design case executes without expected-output or prose feedback;
7. one-, multi-, alternate-order, early-STOP, late-STOP, and `no_viable_plan`
   behavior is deterministic;
8. missing appraisal, state, personality, and relationship input remains
   explicit and neutral;
9. personality can change bounded ordering but never facts or gates;
10. score components, partial-state transitions, ties, and `STOP` are exactly
    traceable;
11. a required-response failure cannot become an empty successful plan;
12. no downstream realization or execution runs after an action failure;
13. Pipeline and Cognitive Turn preserve exact action identities and versions;
14. complete backend regression tests pass; and
15. the main README is updated from design-accepted to implementation-accepted
    only after an independent audit.

## 17. Explicit V0 exclusions

Action Selection V0 does not include:

- live-chat routing or live behavior;
- language generation or realization validation;
- tool invocation or side effects;
- persistent action traces or registry tables;
- a learned selector, online learning, reinforcement learning, or exploration;
- relationship-state inference;
- slow cognition or metacognitive recursion;
- outcome evaluation, credit assignment, reflection, or memory writes;
- automatic action-definition discovery or promotion; or
- claims that deterministic V0 reproduces human decision making.

These exclusions keep the next implementation bounded while preserving the
interfaces needed for later realization, outcome observation, and learning.
