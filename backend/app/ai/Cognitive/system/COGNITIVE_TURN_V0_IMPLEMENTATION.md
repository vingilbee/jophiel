# Cognitive Turn Spine V0 implementation brief

Status: ready for implementation review  
Scope: deterministic shadow execution from an existing interpretation through
appraisal and dynamic-state update  
Non-goal: this task does not create the full cognitive agent

## 1. Objective

Implement one connected, deterministic V0 cognitive turn:

```text
InterpretationEnvelope
+ active goals
+ runtime/capability context
+ retrieved memories and values
+ previous dynamic state
+ compiled personality controls
             |
             v
        AppraisalEngine
             |
             v
      DynamicStateUpdater
             |
             v
      CognitiveTurnTrace
```

This is a **shadow-only cognitive spine**. It proves that existing cognitive
components can exchange their authoritative objects without rebuilding,
flattening, or silently changing their meaning.

The runner must not alter the response sent to the user. Live chat continues to
use the existing agent until routing, action selection, realization, outcome
observation, and safety evaluation have their own reviewed contracts and tests.

## 2. Current prerequisite and ownership decisions

The appraisal-to-dynamic-state shared-contract correction is already in place:

- `AppraisalEngine` produces the canonical `AppraisalResult`, `AppraisalFrame`,
  `AppraisalDimension`, `AppraisalAggregate`, and `SemanticFrame` objects from
  `appraisal.models`.
- `dynamic_state.models` imports those definitions rather than defining reduced
  copies.
- `DynamicStateUpdater` accepts the actual appraisal frames.
- The existing integration test proves an appraisal result can pass directly to
  a dynamic-state update.

Do not reintroduce duplicate appraisal or semantic-frame models. Do not move all
existing models into a new package as part of this task. A later organizational
refactor may place cross-stage contracts under a neutral `contracts` package,
but V0 should first establish the working execution spine.

Canonical ownership remains:

| Concept | Authoritative owner |
| --- | --- |
| Interpretation scenarios and semantic frames | Interpretation contract; temporarily represented in `appraisal.models` |
| Active goals | Goal contract; temporarily represented in `appraisal.models` |
| Appraisal frames and aggregate | `appraisal.models` |
| Dynamic-state snapshot and contributions | `dynamic_state.models` |
| Cognitive-turn identity and trace | New `cognitive_turn.models` |

## 3. Scope

### Included

- immutable cognitive-turn request and trace contracts;
- one orchestration service that invokes real appraisal and dynamic-state code;
- one shared `cognitive_turn_id` across the connected stages;
- exact input and output snapshots for audit and later learning;
- explicit component/schema versions;
- per-stage diagnostic timings;
- explicit typed failure behavior;
- direct integration and invariant tests;
- JSON serialization round-trip tests;
- a truthful README status update.

### Excluded

- live-chat integration or behavior changes;
- parsing raw conversation text;
- a new perception engine;
- a new interpretation/scenario builder;
- goal formation or autonomous goal creation;
- fast/slow routing decisions;
- conversational-action selection;
- language realization;
- tool execution or authorization changes;
- outcome inference, reflection, or learning;
- memory consolidation;
- automatic core-memory/personality changes;
- LLM calls;
- new database tables or migrations.

Do not widen the task to implement an excluded subsystem. The turn spine must
expose clean extension points for later stages without pretending those stages
already exist.

## 4. Terminology

### Cognitive turn

`CognitiveTurn` is the stable identity of cognitive processing associated with
one incoming conversational event. Its identity does not change when later
evidence causes reappraisal, when a response is attached, or when an outcome is
observed.

```text
input message(s) -> cognitive_turn_id -> eventual response/outcome
```

### Cognitive-turn trace

`CognitiveTurnTrace` is one immutable revision of the calculations and evidence
associated with a cognitive turn.

The future lifecycle may be:

```text
CognitiveTurn
|- trace revision 1: fast evaluation
|- trace revision 2: slow reappraisal
|- trace revision 3: action/response attached
|- trace revision 4: outcome attached
`- trace revision 5: reflection attached
```

This task implements only revision 1: deterministic fast evaluation through
dynamic state. The schema should reserve revision identity without adding empty
action, outcome, or learning payloads.

## 5. Suggested files

```text
backend/app/ai/Cognitive/system/cognitive_turn/
|- __init__.py
|- models.py
|- runner.py
`- errors.py

backend/tests/
`- test_cognitive_turn.py
```

Dependency direction must remain:

```text
appraisal ---------+
                   +--> cognitive_turn
dynamic_state -----+
```

Neither appraisal nor dynamic state may import the cognitive-turn package. This
avoids circular ownership and lets each engine remain independently testable.

## 6. Processing-path contract

Define a string enum:

```python
class ProcessingPath(StrEnum):
    FAST = "fast"
    SLOW = "slow"
    REAPPRAISAL = "reappraisal"
```

Only `FAST` is executable in this task. `SLOW` and `REAPPRAISAL` reserve stable
contract values for later work. The V0 runner must reject an unsupported path
explicitly; it must not label ordinary fast execution as slow cognition.

## 7. Cognitive-turn request

Create an immutable, extra-forbidden Pydantic model. Reuse the existing frozen
model convention or a shared equivalent without duplicating domain contracts.

Recommended logical shape:

```python
class CognitiveTurnRequest(FrozenModel):
    schema_version: int = 1

    cognitive_turn_id: str
    conversation_ref: str
    input_message_ids: tuple[str, ...]
    processing_path: ProcessingPath = ProcessingPath.FAST

    interpretation: InterpretationEnvelope
    active_goals: tuple[ActiveGoal, ...]

    runtime: RuntimeContext
    memories: tuple[MemoryRecord, ...] = ()
    values: tuple[ValueRecord, ...] = ()
    relationship: RelationshipSnapshot | None = None
    supplemental_inputs: tuple[SupplementalInput, ...] = ()

    previous_state: StateSnapshot
    personality: PersonalityProfile

    grounded_objects: tuple[GroundedObject, ...] = ()
    control_context: ControlContext = ControlContext()
    outcomes: tuple[OutcomeObservation, ...] = ()
    elapsed_monotonic_ms: int = 0

    core_memory_ref: str
    memory_retrieval_policy_version: str
```

Exact naming may follow repository conventions, but the semantics and
invariants below are required.

### Request invariants

1. `schema_version` is exactly the supported V0 version.
2. `cognitive_turn_id`, `conversation_ref`, `core_memory_ref`, and version fields
   are non-empty.
3. At least one input message id is present.
4. Input message ids are unique.
5. At least one active goal exists for this V0 runner.
6. At least one `leading` or `retained_alternative` interpretation scenario is
   present.
7. `elapsed_monotonic_ms` is non-negative.
8. Only the `fast` processing path is accepted by this runner.
9. `previous_state` is mandatory. Conversation-state initialization stays a
   separate operation in V0.
10. Supplemental evidence retains the scope and provenance validation already
    enforced by `AppraisalRequest`.
11. The request references authoritative messages by id. It does not copy the
    full conversation transcript into every trace.
12. The runner must not silently invent goals, interpretations, memories,
    permissions, capabilities, grounded objects, or missing evidence.

## 8. Component-version contract

Create an immutable version record:

```python
class CognitiveTurnVersions(FrozenModel):
    turn_schema: str
    interpretation_schema: str
    appraisal_schema: str
    appraisal_rules: str
    dynamic_state_policy: str
    personality_mapper: str
    memory_retrieval_policy: str
```

All values must be explicit and non-empty. A trace must remain interpretable
after the implementation changes. Do not reconstruct historical version values
later from the currently installed code.

Where an engine already emits a version, copy that emitted version into the
trace and validate consistency rather than hard-coding a second conflicting
constant.

## 9. Timing contract

Create diagnostic stage timings:

```python
class CognitiveTurnTimings(FrozenModel):
    appraisal_ms: float
    dynamic_state_ms: float
    total_ms: float
```

Requirements:

- values are finite and non-negative;
- timing is diagnostic only and never affects cognition;
- inject a monotonic clock into the runner;
- tests may exclude real elapsed values from deterministic equality, or use a
  deterministic fake clock;
- do not use wall-clock timestamps for duration measurement.

## 10. Cognitive-turn trace

Recommended logical shape:

```python
class CognitiveTurnTrace(FrozenModel):
    schema_version: int = 1

    trace_id: str
    trace_revision: int
    supersedes_trace_id: str | None = None

    cognitive_turn_id: str
    conversation_ref: str
    input_message_ids: tuple[str, ...]
    processing_path: ProcessingPath
    status: str

    input_snapshot: CognitiveTurnRequest
    appraisal: AppraisalResult
    dynamic_state_update: UpdateResult

    versions: CognitiveTurnVersions
    timings: CognitiveTurnTimings
```

For the first implemented trace:

```text
trace_revision = 1
supersedes_trace_id = null
processing_path = fast
status = completed
```

The full input snapshot is retained because later reflection must know what
interpretation, goals, runtime facts, memories, values, personality mapping, and
previous state produced the outputs. Raw message contents remain in the
authoritative chat-message store and are referenced by id.

Do not add nullable placeholder fields for every future subsystem. A future
version or trace revision can add routing, actions, realization, outcomes, and
reflection under reviewed contracts.

## 11. Runner API

Create a thin orchestration service:

```python
class CognitiveTurnRunner:
    def __init__(
        self,
        appraisal_engine: AppraisalEngine | None = None,
        dynamic_state_updater: DynamicStateUpdater | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        ...

    def evaluate(self, request: CognitiveTurnRequest) -> CognitiveTurnTrace:
        ...
```

Dependencies are injectable so tests can prove ordering, failure isolation, and
determinism without global monkeypatching.

The runner is orchestration, not a cognitive inference engine. It may validate,
connect, time, and package stage outputs. It must not add new semantic meaning.

## 12. Required execution sequence

### Stage 1: construct the appraisal request

```python
appraisal_request = AppraisalRequest(
    cognitive_turn_id=request.cognitive_turn_id,
    interpretation=request.interpretation,
    active_goals=request.active_goals,
    runtime=request.runtime,
    memories=request.memories,
    values=request.values,
    relationship=request.relationship,
    supplemental_inputs=request.supplemental_inputs,
)
```

Do not flatten, serialize, copy into reduced classes, or rebuild the
interpretation objects.

### Stage 2: run appraisal

```python
appraisal_result = appraisal_engine.appraise(appraisal_request)
```

Immediately validate:

```text
appraisal_result.cognitive_turn_id
    == request.cognitive_turn_id

appraisal_result.interpretation_trace_id
    == request.interpretation.interpretation_trace_id
```

### Stage 3: construct dynamic-state context

```python
dynamic_context = DynamicStateContext(
    conversation_ref=request.conversation_ref,
    semantic_frames=request.interpretation.semantic_frames,
    objects=request.grounded_objects,
    aggregate=appraisal_result.aggregate,
)
```

Requirements:

- use the same canonical semantic-frame objects;
- use the exact aggregate produced by appraisal;
- do not calculate a substitute aggregate;
- do not convert appraisal frames into another class;
- do not infer grounded objects that were not supplied.

### Stage 4: run the dynamic-state update

```python
update_request = UpdateRequest(
    cognitive_turn_id=request.cognitive_turn_id,
    previous_snapshot=request.previous_state,
    personality=request.personality,
    elapsed_monotonic_ms=request.elapsed_monotonic_ms,
    appraisal_frames=appraisal_result.frames,
    outcomes=request.outcomes,
    context=dynamic_context,
    control_context=request.control_context,
)

update_result = dynamic_state_updater.update(update_request)
```

The exact `AppraisalFrame` instances returned by appraisal must reach the
dynamic-state request.

### Stage 5: build and validate the trace

Create the immutable trace only after both stages succeed. Include exact input
and output objects, emitted versions, diagnostic timing, and deterministic trace
identity supplied through the runner's id factory.

## 13. Cross-stage trace invariants

The completed trace must validate all of the following.

### Turn identity

```text
trace.cognitive_turn_id
= input_snapshot.cognitive_turn_id
= appraisal.cognitive_turn_id
= dynamic_state_update.snapshot.cognitive_turn_id
```

### Conversation and input identity

The trace-level conversation and input-message references equal the request
snapshot references exactly.

### Interpretation identity

```text
appraisal.interpretation_trace_id
= input_snapshot.interpretation.interpretation_trace_id
```

### State ancestry

```text
new_state.revision
= previous_state.revision + 1

new_state.supersedes_snapshot_id
= previous_state.state_snapshot_id
```

### Goal references

Every appraisal frame's `subject.focal_goal_ref` resolves to an active goal in
the request.

### Capability reference

Every appraisal frame's `subject.capability_snapshot_ref` matches the request's
runtime capability-snapshot id.

### Appraisal provenance

Every appraisal-frame id referenced by state contributions or the new snapshot
exists in `appraisal.frames`. No contribution may cite a frame from another
turn or an unknown frame.

### Aggregate preservation

The dynamic-state context receives the exact `AppraisalAggregate` emitted by
the appraisal engine.

### Version consistency

Versions recorded by the trace match the versions emitted by appraisal,
dynamic state, personality mapping, and the supplied memory-retrieval policy.

### Immutability

Execution must not mutate:

- the interpretation envelope;
- the active goals;
- the runtime snapshot;
- the previous state;
- the personality profile;
- retrieved memories or values;
- the appraisal result after construction.

## 14. Failure contract

Define a typed exception or immutable error payload:

```python
class CognitiveTurnExecutionError(RuntimeError):
    cognitive_turn_id: str
    failed_stage: str
    safe_message: str
```

Allowed failure stages:

```text
validation
appraisal
dynamic_state
trace_validation
```

Requirements:

1. If appraisal fails, dynamic state is not invoked.
2. If dynamic state fails, no new snapshot is exposed as current.
3. The previous state remains unchanged.
4. No partial state is persisted by the runner.
5. The original exception is preserved as the cause.
6. Safe messages do not expose secrets, raw database information, or provider
   details.
7. The runner does not substitute the old inspector, an LLM prediction, or a
   fabricated neutral result.

A durable failure trace can be designed later. A typed exception with stage and
turn identity is sufficient for this V0 task.

## 15. Testing policy

Test this implementation **immediately**, not after later cognitive subsystems
are built.

There are two complementary test layers:

1. **Tests required now** prove that appraisal and dynamic state are genuinely
   connected through the turn spine.
2. **Tests added later** extend the same trace through perception,
   interpretation, routing, action, realization, outcome, and reflection as
   those components become available.

Waiting for the entire system would make failures difficult to localize and
would allow incompatible contracts to accumulate again. Passing current tests
does not claim that future stages are connected; it establishes a reliable
baseline to which each later stage is added.

## 16. Tests required in this task

### 16.1 Existing regression suites

Run the appraisal, dynamic-state, memory-model, and current cognitive tests.
They must remain green.

### 16.2 Real connected happy path

Use the real `AppraisalEngine`, real `DynamicStateUpdater`, a real
`InterpretationEnvelope`, an active goal, a previous state, and a personality
profile.

Assert:

- the trace is completed;
- appraisal belongs to the requested cognitive turn;
- the state revision increments;
- the state supersedes the supplied snapshot;
- expected contributions and source references are retained.

### 16.3 Exact shared-object handoff

Use a recording updater or inspect the constructed request and assert:

```python
update_request.appraisal_frames[0] is appraisal_result.frames[0]
```

Also assert that the semantic-frame instances and appraisal aggregate are not
replaced by reduced representations.

### 16.4 Clear interpretation

```text
one leading scenario
-> complete appraisal
-> deterministic state revision
-> trace retains evidence and versions
```

### 16.5 Ambiguous interpretation

```text
leading scenario + retained alternative
-> both appraisal branches remain present
-> disagreement remains explicit
-> incompatible state effects remain provisional or branch-aware
```

### 16.6 Missing information

```text
missing consent, authorization, or required argument
-> corresponding appraisal remains unknown
-> missing reference survives in the trace
-> derived clarification/slow-cognition pressure changes only when supported
```

### 16.7 High-consequence alternative

```text
safe leading scenario + plausible harmful alternative
-> high_consequence_uncertainty is true
-> downstream consequence control reflects it
-> no averaging into a benign interpretation
```

### 16.8 Cross-goal isolation

An appraisal scoped to goal B must not update a state value scoped to goal A.

### 16.9 Determinism

With fixed id and clock factories, identical requests produce identical traces.
If real diagnostic durations are used, compare the semantic trace while
excluding only the measured duration fields.

### 16.10 Serialization round trip

```python
payload = trace.model_dump(mode="json")
restored = CognitiveTurnTrace.model_validate(payload)
assert restored == trace
```

This is mandatory before any persistence layer is added.

### 16.11 Failure isolation

With a fake appraisal engine that raises, assert:

- the dynamic-state updater is not called;
- the previous state is unchanged;
- the error identifies `appraisal` as the failed stage.

With a fake state updater that raises, assert:

- no completed trace is returned;
- the previous state is unchanged;
- the error identifies `dynamic_state` as the failed stage.

### 16.12 Contract mismatch rejection

Deliberately provide or return mismatched:

- cognitive-turn ids;
- interpretation-trace ids;
- goal references;
- capability-snapshot references;
- appraisal-frame provenance;
- state predecessor ids;
- state revision numbers.

Every mismatch must fail closed.

### 16.13 Input immutability

Capture the complete input request before execution and assert it is unchanged
after success and after each failure path.

## 17. Acceptance criteria

The implementation is complete only when all of the following are true:

- one `CognitiveTurnRunner` exists;
- the runner invokes real appraisal followed by real dynamic state;
- there is no appraisal-frame or semantic-frame conversion between stages;
- one immutable trace retains the exact input and output snapshots;
- every stage uses the same cognitive-turn identity;
- state revision ancestry is validated;
- goal, capability, evidence, and appraisal references resolve;
- emitted component versions are recorded and consistent;
- errors are explicit, stage-specific, and cannot mutate state;
- trace JSON serialization round-trips;
- existing cognitive regression suites pass;
- the new connected turn tests pass;
- live chat behavior is unchanged;
- no network or LLM call is introduced;
- no database migration is introduced;
- no action selection, response realization, reflection, or learning is added;
- the Cognitive README accurately reports the implemented shadow spine and no
  longer claims that appraisal/dynamic state are unimplemented.

## 18. Known appraisal issues outside the runner

The runner must not compensate for appraisal defects. Fix these in appraisal
itself, either immediately before or in a separate bounded change:

1. `desired_state` comparisons must inspect expected values, not only key
   presence.
2. `appraisal.controllability` must use actual registered capabilities,
   eligibility, parameters, consent, and authorization rather than defaulting
   to high control.
3. Reappraisal must choose and validate the latest matching historical frame,
   not the first matching frame.
4. Generated appraisal-frame ids must include cognitive-turn or appraisal-trace
   identity so locally repeated scenario/goal ids cannot collide across turns.
5. Supplemental evidence scope must not be mistaken for semantic support; a
   scoped memory or user message affects only dimensions justified by its typed
   claim.

Do not hide any of these issues inside orchestration logic.

## 19. Next milestone after this task

After the turn spine is implemented and tested, implement the **new designed
Perception V0 contract and runtime**, followed by the **new deterministic
Interpretation V0 and scenario builder**, and then connect those outputs to the
turn runner:

```text
conversation/message references
        |
        v
new canonical PerceptionEvent V0
        |
        v
deterministic Interpretation V0 + scenario builder
        |
        v
CognitiveTurnRunner
        |
        v
AppraisalResult + DynamicStateSnapshot
```

### Important legacy-pipeline boundary

The repository already has `system/perception.py`, `system/interpretation.py`,
`system/schemas.py`, and `CognitiveInputPipeline`. Those files implement the
older diagnostic cognitive-input experiment used by `/api/cognitive/inspect`.
They are **not** the new Perception V0 or Interpretation V0 described in the
current architecture.

The old perception output is a small flat object containing fields such as:

```text
is_question
is_request
is_directed_at_agent
novelty
ambiguity
signals
```

The new Perception V0 must instead produce the reviewed, evidence-linked,
versioned perception envelope containing:

```text
cognitive_turn_id
input_message_ids
observations
cue_activations
attention
overall_uncertainty
missing_information
component versions
```

The old interpretation output is one flat winning intent plus confidence,
alternatives, and a clarification flag. The new Interpretation V0 must produce:

```text
semantic frames
multi-dimensional hypotheses
supporting and contradicting evidence
unknowns and out-of-scope records
coherent retained scenarios
ordinal support and disposition
version provenance
```

Therefore, do not wire the current `CognitiveInputPipeline` directly into the
new appraisal engine and call the design complete. Its existing deterministic
detectors may be reviewed and reused as low-level providers for compatible V0
cues, for example question form, directive markers, explicit correction,
repetition, word count, attachment state, and runtime facts. Reused detectors
must emit the new cue contract with evidence references and detector versions.
The old flat `Perception`, `Interpretation`, and intent-classifier outputs remain
diagnostic/legacy contracts until explicitly retired.

The implementation order after the turn spine is:

1. finalize and implement the canonical Perception V0 envelope and approved
   deterministic cue registry;
2. adapt only compatible existing rule detectors into that envelope;
3. validate the reviewed perception design cases against the production V0
   engine;
4. implement deterministic Interpretation V0 and coherent scenario building;
5. validate the reviewed interpretation design cases against that engine;
6. feed the resulting canonical `InterpretationEnvelope` into the
   `CognitiveTurnRunner`;
7. add a real message-to-state integration test covering the complete connected
   path.

Only after that forward understanding/evaluation path works should V0 add:

```text
routing decision
-> small registered action plan
-> constrained language realization
-> outcome observation
-> post-turn reflection
-> candidate learning trace
```

The reflection system must transform the completed forward trace and later
evidence into **candidate** learning data. It must not treat the system's own
interpretation, appraisal, or inferred outcome as ground truth.

## 20. Central implementation rule

> Build a thin orchestration spine around existing authoritative outputs. Do
> not invent new cognitive meaning, duplicate contracts, silently discard
> uncertainty or provenance, or expand this V0 task into action selection and
> learning.
