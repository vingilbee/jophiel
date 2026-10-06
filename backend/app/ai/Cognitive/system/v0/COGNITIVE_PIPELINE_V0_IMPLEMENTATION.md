# Cognitive Pipeline V0 implementation brief

Status: implemented and acceptance-hardened for shadow V0  
Scope: deterministic, shadow-only orchestration from bounded messages through
Perception, Interpretation, Appraisal, and Dynamic State  
Prerequisites: acceptance-hardened Perception V0 and Interpretation V0, plus the
implemented `CognitiveTurnRunner`  
Non-goal: this task does not create the complete cognitive agent or change live
chat behavior

## 1. Objective

Implement one canonical shadow pipeline:

```text
bounded messages
+ one trusted runtime snapshot
+ retrieved context
+ compiled personality inputs
+ active goals
+ previous dynamic state
                 |
                 v
          PerceptionEngine
                 |
                 v
       exact active-cue handoff
                 |
                 v
        InterpretationEngine
                 |
                 v
        CognitiveTurnRunner
          |- AppraisalEngine
          `- DynamicStateUpdater
                 |
                 v
       CognitivePipelineTrace
```

The pipeline validates, derives exact stage-specific views, calls the real
engines, preserves authoritative objects at handoffs, and packages a connected
trace. It performs no new cognitive inference.

Completion requires one `CognitivePipelineRequest` to produce a trace containing
the exact emitted `PerceptionEvent`, `InterpretationEnvelope`, and
`CognitiveTurnTrace`, with ownership, provenance, versions, references, and
conversation boundaries validated.

## 2. Create a new package

Do not expand `CognitiveTurnRunner`. Its correct responsibility is:

```text
existing InterpretationEnvelope
  -> AppraisalEngine
  -> DynamicStateUpdater
  -> CognitiveTurnTrace
```

The new pipeline begins earlier:

```text
source inputs
  -> PerceptionEngine
  -> InterpretationEngine
  -> CognitiveTurnRunner
  -> CognitivePipelineTrace
```

Recommended files:

```text
backend/app/ai/Cognitive/system/v0/cognitive_pipeline_v0/
|- __init__.py
|- models.py
|- runner.py
`- errors.py

backend/tests/
|- test_cognitive_pipeline_v0.py
`- test_cognitive_pipeline_v0_contract.py
```

Do not reuse `system/pipeline.py`; that is the legacy diagnostic inspector.

## 3. Existing contracts that must be reused

- `PerceptionEngine.perceive(PerceptionRequest) -> PerceptionEvent`
- `PerceptionEvent.interpretation_handoff()` returns the only accepted live
  handoff. The pipeline must not rebuild it.
- `InterpretationEngine.interpret(InterpretationRequest) ->
  InterpretationEnvelope`
- `CognitiveTurnRunner.evaluate(CognitiveTurnRequest) -> CognitiveTurnTrace`
- `CognitiveTurnRunner` already owns the real Appraisal and Dynamic State calls.
- Interpretation requires an explicit compiled `InterpretationBiasProfile`.
- Cognitive Turn requires explicit active goals, previous state, Appraisal
  context, and `dynamic_state.PersonalityProfile`.

Goal formation, personality compilation, retrieval, persistence, routing,
actions, realization, outcomes, and learning are not supplied by this task.

## 4. Central decisions

### 4.1 One source for shared facts

The caller must not submit separate Perception, Interpretation, and Appraisal
copies of the same runtime fact. Create one immutable
`CognitivePipelineRuntimeSnapshot` and derive the existing narrow views:

```text
CognitivePipelineRuntimeSnapshot
|- PerceptionRuntimeContext
|- InterpretationRuntimeContext
`- appraisal RuntimeContext
```

Use one retrieved-memory collection and one relationship context, with explicit
downstream projections where current stage contracts differ.

### 4.2 Exact stage outputs cross handoffs

```text
PerceptionEvent.interpretation_handoff()
  -> exact InterpretationRequest.perception

InterpretationEnvelope
  -> exact CognitiveTurnRequest.interpretation

CognitiveTurnRequest.interpretation
  -> exact CognitiveTurnTrace.input_snapshot.interpretation
```

Do not serialize, flatten, reconstruct, or reduce these objects.

### 4.3 No fabricated cognition

Reject missing inputs rather than inventing a neutral bias, personality,
generic goal, initial state, memory, relationship, authorization, capability,
or fallback interpretation. First-turn state initialization remains an explicit
external call to `DynamicStateSnapshotFactory.create_initial()`.

### 4.4 Shadow only

Do not connect this pipeline to live chat or user-visible response behavior in
this task.

## 5. Dependency direction

```text
perception_v0 ---------+
interpretation_v0 -----+
cognitive_turn --------+--> cognitive_pipeline_v0
contracts -------------+
```

No orchestrated stage may import `cognitive_pipeline_v0`.

## 6. Pipeline request

Recommended logical shape:

```python
class CognitivePipelineRequest(FrozenModel):
    schema_version: Literal[1] = 1
    cognitive_turn_id: str
    conversation_ref: str
    messages: tuple[PerceptionMessage, ...]
    focus_message_id: str
    language_tag: str | None = "en"
    context_truncated: bool = False
    perception_working_context: PerceptionWorkingContext | None = None

    runtime: CognitivePipelineRuntimeSnapshot
    memories: tuple[InterpretationMemory, ...] = ()
    relationship: CognitivePipelineRelationshipContext | None = None

    interpretation_biases: InterpretationBiasProfile
    active_goals: tuple[ActiveGoal, ...]
    values: tuple[ValueRecord, ...] = ()
    supplemental_inputs: tuple[SupplementalInput, ...] = ()

    previous_state: StateSnapshot
    state_personality: PersonalityProfile
    grounded_objects: tuple[GroundedObject, ...] = ()
    control_context: ControlContext = Field(default_factory=ControlContext)
    outcomes: tuple[OutcomeObservation, ...] = ()
    elapsed_monotonic_ms: int = 0

    core_memory_ref: str
    memory_retrieval_policy_version: str
    processing_path: Literal[ProcessingPath.FAST] = ProcessingPath.FAST

    cue_manifest_version: str = MANIFEST_VERSION
    ontology_version: str = ONTOLOGY_VERSION
    feature_manifest_version: str = FEATURE_MANIFEST_VERSION
```

Imported version constants may be construction defaults. Completed traces must
copy versions actually emitted by engines, never reconstruct them later.

### 6.1 Request invariants

Validate before any engine call:

1. All ownership, source, and version ids are non-blank.
2. Messages are non-empty, ordered, unique by id, and at most 30.
3. Focus occurs exactly once and is the final message.
4. Previous state belongs to `conversation_ref`.
5. Previous state mapper equals `state_personality.mapper_version`.
6. `interpretation_biases.source_core_memory_ref == core_memory_ref`.
7. At least one goal has `status == "active"`.
8. Goal, memory, value, outcome, supplemental-input, attachment, and grounded
   object identities are unique in their applicable namespaces.
9. Elapsed monotonic milliseconds are non-negative.
10. Only `ProcessingPath.FAST` is accepted.
11. Current V0 requires `language_tag == "en"`.
12. Nested mappings are deeply immutable and JSON-compatible.
13. Raw core-memory JSON cannot substitute for compiled profiles.

## 7. Canonical runtime snapshot

Recommended shape:

```python
class CognitivePipelineRuntimeSnapshot(FrozenModel):
    snapshot_id: str
    schema_version: Literal[1] = 1
    predicate_registry_version: str
    known_predicates: tuple[str, ...]
    known_entities: tuple[str, ...] = ()

    attachments_available: bool = True
    attachments: tuple[AttachmentFact, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    capability_snapshot_id: str
    task_contract: TaskContract | None = None

    authorization: str | None = None
    consent: str | None = None
    safety_decision: str | None = None
    external_export_control: bool | None = None
    policy_records: tuple[str, ...] = ()
    diagnostics: Mapping[str, JsonValue] = Field(default_factory=dict)
    last_tool_event: ToolEventFact | None = None
    conversation_mode: str | None = None
```

Require non-blank snapshot/registry/capability ids, unique predicates/entities/
capabilities/attachments/policies, the installed predicate registry and
version, no attachments when `attachments_available` is false, trusted
attachment provenance, JSON-safe diagnostics, and immutable facts.

Diagnostics never create evidence, authorization, capabilities, semantic
arguments, hypotheses, goals, or state contributions.

### 7.1 Derived Perception view

```python
PerceptionRuntimeContext(
    snapshot_id=runtime.snapshot_id,
    schema_version=runtime.schema_version,
    attachments_available=runtime.attachments_available,
    attachments=runtime.attachments,
    available_capabilities=runtime.available_capabilities,
    last_tool_event=runtime.last_tool_event,
    conversation_mode=runtime.conversation_mode,
)
```

### 7.2 Derived Interpretation view

```python
InterpretationRuntimeContext(
    snapshot_id=runtime.snapshot_id,
    predicate_registry_version=runtime.predicate_registry_version,
    known_predicates=runtime.known_predicates,
    known_entities=runtime.known_entities,
    attachments=runtime.attachments,
    available_capabilities=runtime.available_capabilities,
    task_contract=runtime.task_contract,
    authorization=runtime.authorization,
    safety_decision=runtime.safety_decision,
    last_tool_event=runtime.last_tool_event,
)
```

### 7.3 Derived Appraisal view

For V0, capability and action keys use the same normalized registry. Do not ask
the caller for a second list.

```python
RuntimeContext(
    capability_snapshot=CapabilitySnapshot(
        snapshot_id=runtime.capability_snapshot_id,
        available_actions=runtime.available_capabilities,
    ),
    available_actions=runtime.available_capabilities,
    required_parameters=(runtime.task_contract.required_parameters
                         if runtime.task_contract else ()),
    provided_parameters=(runtime.task_contract.provided_parameters
                         if runtime.task_contract else {}),
    authorization=runtime.authorization,
    consent=runtime.consent,
    safety_decision=runtime.safety_decision,
    external_export_control=runtime.external_export_control,
    policy_records=runtime.policy_records,
    diagnostics=runtime.diagnostics,
    last_tool_event=(runtime.last_tool_event.model_dump(mode="python")
                     if runtime.last_tool_event else {}),
)
```

The appraisal tool-event mapping is a derived compatibility view and must equal
the typed source exactly.

Tests must prove snapshot ids, attachments, capabilities/actions, task
parameters, authorization, safety, and tool events cannot diverge across views.

## 8. Memory projection

Use `InterpretationMemory` as the authoritative V0 retrieval record. It carries
the strictest current metadata: id, type, content, provenance, status,
retrieval score, version, and authorization.

Requirements:

1. Reject unauthorized records before content inspection.
2. Reject duplicate ids and non-finite retrieval scores.
3. Pass exact instances to `InterpretationRequest` in exact order.
4. Derive Appraisal `MemoryRecord` from only id, type, content, provenance, and
   status, one-to-one and in order.
5. Retain retrieval score/version in the canonical request and Interpretation
   evidence even though Appraisal does not consume them.
6. Stale memory cannot override trusted current runtime facts.
7. Perform no retrieval inside the pipeline.

This is a temporary explicit projection, not a third memory schema.

## 9. Relationship projection

Recommended canonical superset:

```python
class CognitivePipelineRelationshipContext(FrozenModel):
    relationship_ref: str
    status: str
    provenance: str
    authorized: bool = True
    evidence: str | None = None
    records: tuple[str, ...] = ()
```

Derive both current views with the same id and status:

```text
InterpretationRelationshipContext.relationship_ref
  = relationship_ref

RelationshipSnapshot.relationship_snapshot_id
  = relationship_ref
```

Reject unauthorized input before content inspection. If omitted, both stage
relationships must be `None`; a relationship cannot exist in only one stage.

## 10. Personality and core-memory ownership

Keep two typed compiled outputs because they serve different purposes:

- `InterpretationBiasProfile`: ambiguity-gated interpretation priors.
- `dynamic_state.PersonalityProfile`: state baselines and transition controls.

Validate:

```text
interpretation_biases.source_core_memory_ref == core_memory_ref
previous_state.personality_mapper_version == state_personality.mapper_version
```

Compiler and mapper versions may differ and must both remain explicit. Do not
compile either profile from raw memory in this task.

## 11. Runner API and exact sequence

```python
class CognitivePipelineV0:
    def __init__(
        self,
        *,
        perception_engine: PerceptionEngine | None = None,
        interpretation_engine: InterpretationEngine | None = None,
        cognitive_turn_runner: CognitiveTurnRunner | None = None,
        trace_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None: ...

    def run(self, request: CognitivePipelineRequest) -> CognitivePipelineTrace:
        ...
```

Use `run`; keep `CognitiveTurnRunner.evaluate` unchanged.

### Stage 0: trust-boundary validation

Revalidate even an already constructed Pydantic request from its Python dump.
Reject unauthorized memory/relationship before serializing their content.

### Stage 1: Perception

Construct one `PerceptionRequest` from top-level ownership/messages and the
derived Perception runtime. Run `perception_engine.perceive()` and revalidate
the returned event from its Python dump to defeat `model_construct()` bypasses.

Validate turn id, ordered message ids, focus id, runtime snapshot id, context
truncation, and requested manifest version against the pipeline request.

### Stage 2: exact handoff

Call `perception.interpretation_handoff()` exactly once. Do not reconstruct it.
Assert ownership/versions equal the event, shadow cues are absent, and every
handoff activation is the exact event activation instance.

### Stage 3: Interpretation

Construct `InterpretationRequest` with the exact handoff and messages, derived
runtime/relationship, exact authoritative memories and bias profile, and
explicit versions. Run the real engine and revalidate its complete envelope.

Validate turn, conversation, perception event, messages, focus, runtime,
truncation, and copied Perception versions.

### Stage 4: Cognitive Turn request

Construct `CognitiveTurnRequest` with the exact emitted Interpretation object,
derived Appraisal runtime/memory/relationship, and explicitly supplied goals,
values, state, personality, objects, controls, outcomes, core-memory ref, and
retrieval-policy version.

### Stage 5: Cognitive Turn

Call only `cognitive_turn_runner.evaluate(turn_request)`. Do not bypass it to
call Appraisal or Dynamic State directly.

Validate turn/conversation/messages/focus, exact input request and
Interpretation identity, exact Interpretation versions, Appraisal trace
ownership, and resulting state conversation ownership.

### Stage 6: completed trace

Only after all stages succeed may a completed `CognitivePipelineTrace` exist.

## 12. Pipeline trace

```python
class CognitivePipelineVersions(FrozenModel):
    pipeline_schema: str
    pipeline_runner: str
    perception: PerceptionVersions
    interpretation: InterpretationVersions
    cognitive_turn: CognitiveTurnVersions

class CognitivePipelineTimings(FrozenModel):
    perception_ms: float
    interpretation_ms: float
    cognitive_turn_ms: float
    total_ms: float

class CognitivePipelineTrace(FrozenModel):
    schema_version: Literal[1] = 1
    pipeline_trace_id: str
    trace_revision: Literal[1] = 1
    supersedes_pipeline_trace_id: None = None
    cognitive_turn_id: str
    conversation_ref: str
    input_message_ids: tuple[str, ...]
    focus_message_id: str
    runtime_snapshot_id: str
    processing_path: Literal[ProcessingPath.FAST]
    status: Literal["completed"] = "completed"
    perception: PerceptionEvent
    interpretation: InterpretationEnvelope
    cognitive_turn: CognitiveTurnTrace
    versions: CognitivePipelineVersions
    timings: CognitivePipelineTimings
```

Do not embed the raw pipeline request in the completed trace. Raw messages stay
in the authoritative message store and are referenced by id. A future durable
design may store a request digest and source references.

### 12.1 Trace invariants

Enforce:

1. one turn id across every stage;
2. one conversation across Interpretation, Turn, and State;
3. exact ordered message/focus ownership;
4. exact Perception-to-Interpretation runtime lineage;
5. Interpretation cites the emitted Perception event;
6. copied Perception versions equal emitted versions;
7. Turn input holds the exact Interpretation object;
8. Turn versions hold the exact Interpretation versions object;
9. Appraisal cites retained Interpretation scenarios;
10. State cites emitted appraisal frames and advances exactly one revision;
11. state conversation and personality mapper remain correct;
12. all models are immutable and JSON-round-trip equal;
13. versions are emitted values, not reconstructed constants; and
14. timings are finite, non-negative, and sum to total.

In-memory tests must use `is` for authoritative handoffs. JSON round trips
preserve equality and id references, not Python object identity.

## 13. Determinism

Perception and Interpretation semantics are deterministic. Default Turn trace
ids/timings use UUID/time, so a default complete operational trace is not
byte-identical. Require injectable factories/clocks and equal traces under fixed
test factories.

Operational ids/timings must never influence perception, hypotheses,
appraisals, or state. Suggested versions:

```text
cognitive-pipeline-schema-v0.1
cognitive-pipeline-runner-v0.1
```

## 14. Failure contract

```python
PipelineFailureStage = Literal[
    "validation", "perception", "handoff_validation", "interpretation",
    "turn_request", "cognitive_turn", "trace_validation",
]

class CognitivePipelineExecutionError(RuntimeError):
    cognitive_turn_id: str
    failed_stage: PipelineFailureStage
    nested_stage: str | None
    safe_message: str
```

Preserve the original exception as `__cause__`. Copy Interpretation
`failed_phase` or Cognitive Turn `failed_stage` into `nested_stage`. Perception
ordinary errors become pipeline `perception` failures.

Safe messages expose no message/memory/relationship content, raw tool error,
secret, provider, or database detail. Never continue after failure, return a
partial successful trace, fall back to legacy/LLM logic, fabricate defaults,
or persist partial output.

## 15. Required tests

### 15.1 Contracts

Test frozen/extra-forbidden models, deep immutability, JSON safety, blank and
duplicate ids, focus ordering, 30-message bound, language/path rejection,
core-memory ownership, state conversation/mapper mismatch, missing active goal,
runtime registry/version validation, attachment availability, round trips, and
explicit versions.

### 15.2 Projections

Prove one runtime produces consistent stage snapshot ids, exact attachment/tool
objects, capability/action keys, task parameters, authorization/safety, and an
exact typed-to-mapping tool view. Prove diagnostics have no cognitive effect and
divergent caller stage contexts are impossible.

### 15.3 Memory and relationship

Test exact Interpretation memory instances, one-to-one Appraisal projection,
retained retrieval metadata, stale-memory conflict, rejection before content
inspection, no internal retrieval, relationship absent/present consistency,
shared id/status, and no manufactured evidence.

### 15.4 Ordering and identity

With instrumented engines prove exact order:

```text
perception -> handoff -> interpretation -> cognitive_turn -> trace validation
```

Each engine runs once; exact messages, handoff, Interpretation, versions, and
outputs cross boundaries; inputs remain unchanged; legacy code is never used.

### 15.5 Adversarial boundaries

Reject foreign/malformed Perception ownership, `model_construct()` activation
bypasses, shadow handoffs, Interpretation for another event, forged evidence,
split/incompatible scenarios, Turn traces with another Interpretation,
Appraisal references to non-retained scenarios, State references to unknown
frames, cross-conversation/mapping state, reconstructed versions, and success
after any stage failure.

### 15.6 Failure phases

Inject every phase. Assert correct outer and nested phase, preserved cause, safe
message, no later calls, no completed trace, and unchanged inputs/outputs.

### 15.7 Real-engine connected cases

Run at least:

1. factual information request;
2. action request with missing user-owned argument;
3. correction with negation scope;
4. decline distinct from correction;
5. short acknowledgement;
6. multifunctional confirmation;
7. quoted hypothetical;
8. indirect request with one coherent alternative;
9. runtime conflict with stale memory;
10. unknown/out-of-scope predicate;
11. attachment grounding;
12. irreversible request with unresolved authorization;
13. 30-message truncated context; and
14. cross-conversation state rejection.

Assert ownership, versions, identity, references, state revision, and JSON round
trip for every successful case.

### 15.8 Determinism and regressions

With fixed factories/clocks, identical input yields equal trace. Ownership
changes alter owned ids; bias changes only gated ambiguity; operational metadata
does not change cognition; ordering is stable.

Run all Perception, Interpretation, Appraisal, Dynamic State, Cognitive Turn,
Pipeline, API, legacy inspector, and full backend suites. Live behavior must not
change.

## 16. Acceptance criteria

Complete only when:

- one public `CognitivePipelineV0` exists;
- one immutable request is the source of shared facts;
- one runtime produces all stage runtime views;
- one memory set and relationship feed applicable stages;
- exact bounded input reaches Perception;
- exact active handoff reaches Interpretation with zero shadow influence;
- exact Interpretation reaches `CognitiveTurnRunner`;
- the existing runner still owns Appraisal and State;
- ownership is checked before and after each call;
- callers cannot submit divergent runtime copies;
- nothing missing is fabricated;
- the trace contains exact outputs and emitted versions;
- identity/reference invariants pass;
- typed failures stop execution safely;
- required real-engine cases and full regression pass;
- no network, LLM, database, persistence, action, tool execution, routing,
  response generation, or live-chat behavior is added; and
- README is updated only after acceptance passes.

## 17. Implementation order

1. Inspect current contracts/tests.
2. Add pipeline errors and immutable models.
3. Add canonical runtime and pure projections.
4. Add memory/relationship projections.
5. Add request ownership validation.
6. Add Perception invocation/output validation.
7. Add exact handoff validation.
8. Add Interpretation invocation/output validation.
9. Construct the Turn request.
10. Reuse `CognitiveTurnRunner`.
11. Add trace/version/timing validation.
12. Add contract/projection/order/identity/failure tests.
13. Move manual connected cases through the public pipeline while retaining
    useful lower-level tests.
14. Run focused and full suites plus `git diff --check`.
15. Update README truthfully.

## 18. Excluded and forbidden shortcuts

Excluded: live integration, automatic goals/retrieval/personality compilation,
state persistence/decay, routing, slow cognition, reappraisal, action/tool
selection or execution, response realization, outcome inference, reflection,
learning/training, migrations, and legacy removal.

Do not add parsing to `CognitiveTurnRunner`, bypass it, accept separate stage
runtime contexts, rebuild handoffs, duplicate canonical outputs, fabricate
defaults, inspect unauthorized content, treat diagnostics/personality/timing as
evidence, use shadow cues, call an LLM fallback, use the legacy pipeline, emit
partial success, hard-code fixture outputs, add placeholder future fields, or
claim complete human-like cognition.

## 19. Required implementation-agent report

Report every changed file; final dependency graph; public request/runner/trace/
failure types; runtime projection table; memory/relationship/personality
connections; exact-object handoff confirmation; `CognitiveTurnRunner` reuse;
all real connected cases; focused/full test results; `git diff --check`; every
remaining V0 limitation; zero shadow influence; no fabricated inputs; and no
live-chat/network/LLM/database/persistence/action/routing/tool/response changes.

## 20. Central rule

> Cognitive Pipeline V0 connects authoritative stages; it does not replace
> them. One validated source produces exact stage views, exact stage outputs
> cross every handoff, and one immutable trace proves the path from observable
> signals to interpretation, appraisal, and dynamic state without inventing
> missing cognition.

## 21. Addendum: requirement-resolution stage (runner v0.2.2 / v0.3.2, 2026-09-30)

Normative text: [`REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md`](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md) §9. Summary:

- The request gains optional `requirement_inputs` (turn time with zone, an optional
  IANA zone, and a user ref). It is omitted when absent, and schema versions are unchanged.
- A `requirement_resolution` stage runs between Goal and the Cognitive Turn.
  Its projected objects and descriptors join the Action inputs, and its
  handoff is passed to Action Selection. Its failures are typed with stage
  `requirement_resolution`.
- The trace gains `requirement_resolution` (versions, handoff, content
  records, dialogue refs, projected objects/descriptors/content) and
  `timings.requirement_resolution_ms`, which is counted in the total. Trace validation
  recomputes the projection from the recorded snapshot and requires the
  section exactly for current runner versions. Historical `v0.2.1`/`v0.3.1`
  traces stay loadable without it.
