# Deterministic Interpretation V0 implementation brief

Status: ready for implementation review  
Target: shadow-only deterministic runtime  
Upstream: implemented Perception V0  
Downstream: implemented appraisal, dynamic state, and cognitive-turn spine

## 1. Objective

Implement the new deterministic Interpretation V0 runtime described by the
Cognitive system design:

```text
bounded messages + trusted runtime + retrieved evidence
                         |
                         v
                 Perception V0 event
                         |
                         v
              active-cue handoff only
                         |
                         v
             deterministic Interpretation V0
              |- bounded semantic grounding
              |- multidimensional hypotheses
              |- explicit unknowns/contradictions
              |- bounded personality adjustment
              `- coherent scenario construction
                         |
                         v
              canonical InterpretationEnvelope
                         |
                         v
        CognitiveTurnRunner -> appraisal -> dynamic state
```

Interpretation V0 converts observable upstream evidence into explicit possible
meanings. It must preserve alternatives and uncertainty. It must not guess a
single hidden intent, infer a user's private mental state as fact, or silently
turn personality and memory into evidence.

The implementation is complete only when a real `PerceptionEvent` can flow
through Interpretation V0 into the existing real appraisal and dynamic-state
engines under one connected cognitive-turn identity.

## 2. Current repository facts

The implementation agent must inspect the current code before changing it.
Important current facts are:

- `system/perception_v0/` is the new canonical Perception V0 runtime.
- `PerceptionEvent.interpretation_handoff()` exposes only `v0_active` cues and
  preserves perception version provenance.
- `system/perception.py`, `system/interpretation.py`, `system/schemas.py`, and
  `CognitiveInputPipeline` are the legacy diagnostic inspector. They are not
  the runtime being implemented here.
- `appraisal.models.InterpretationEnvelope`, `SemanticFrame`,
  `InterpretationScenario`, `UnknownInput`, and `Contradiction` are currently
  the minimal canonical objects consumed by appraisal.
- That current interpretation envelope is insufficient as a complete upstream
  contract because it does not contain cognitive-turn ownership, message
  ownership, perception provenance, full hypotheses, evidence records,
  out-of-scope records, or intrinsic interpretation versions.
- `CognitiveTurnRunner` already consumes an `InterpretationEnvelope`; it must
  consume the enriched canonical object directly, without conversion.
- `data/interpretation_design_cases.json` contains 23 manually authored design
  cases and remains `draft_for_human_review`. It is design evidence, not
  training truth.

Do not build a parallel interpretation result class and then reduce it into the
old envelope. Enrich and relocate the canonical shared contract atomically.

## 2.1 Design inconsistencies resolved by this brief

The existing documents and contracts contain several inconsistencies. Treat the
decisions below as the implementation resolution; do not silently choose a
different interpretation.

| Inconsistency | Resolution for V0 |
| --- | --- |
| The README's early proposed interpretation envelope contains `appraisals`, although the accepted architecture says appraisal is a separate downstream stage. | The Interpretation V0 envelope contains no appraisal payload. Appraisal consumes retained interpretation scenarios afterward. |
| The proposed README envelope omits coherent scenarios, while the implemented appraisal engine requires them. | Scenarios are mandatory canonical Interpretation V0 output. |
| The current appraisal-owned `InterpretationEnvelope` contains scenarios but not the actual hypotheses, evidence registry, source ownership, or interpretation versions. | Replace it atomically with the enriched shared canonical envelope; keep compatibility imports only. |
| The design fixture may contain shadow cues, while implemented Perception V0 correctly filters them from its interpretation handoff. | Split ontology-design validation from live-runtime conformance. Shadow-dependent expectations remain deferred and have zero live influence. |
| Perception's bounded message list includes context, but the focus message is the incoming event that owns the current turn. Cognitive Turn currently records only `input_message_ids`. | Preserve both the ordered bounded input ids and an explicit `focus_message_id` through Perception, Interpretation, Cognitive Turn, and the trace. |
| The Perception handoff lacks its full message-id list and runtime-snapshot identity even though downstream evidence needs both. | Extend the handoff with `input_message_ids`, `runtime_snapshot_id`, and `context_truncated`; copy them exactly from the event. |
| The design expects personality bias, but the Personality Runtime Mapper is not implemented. | Interpretation accepts an explicit compiled, versioned bias profile. It never reads raw core memory or silently creates a neutral profile. |
| The fixture contains routing expectations, but the metacognitive router is not implemented and routing is outside interpretation. | Validate that Interpretation exposes the evidence/unknowns needed by the expected route; do not emit the route from Interpretation V0. |
| Existing semantic frames use weak string `source_spans`, while Perception V0 has typed evidence with exact offsets and runtime provenance. | Canonical semantic frames cite typed, resolvable evidence records. A compatibility view may be derived temporarily. |
| The fixture remains `draft_for_human_review`, but runtime implementation is now requested. | It guides deterministic coverage and contrasts, but is not training truth or fully approved semantics. Report every runtime/deferred discrepancy for human review. |
| The README still describes perception/interpretation as design in some status sections. | Update status only after the real connected tests pass; keep future learned models explicitly unimplemented. |

Two deliberately separate concepts remain:

```text
input_message_ids = ordered bounded messages actually available as evidence
focus_message_id  = the incoming message/event that owns this cognitive turn
```

Do not redefine one to mean the other during implementation.

## 3. Scope

### Included

- one canonical, immutable Interpretation V0 contract;
- an immutable interpretation request;
- exact Perception V0 handoff validation;
- typed evidence records with resolvable provenance;
- a fixed V0 interpretation ontology and manifest;
- deterministic, bounded semantic grounding;
- deterministic multidimensional hypothesis rules;
- explicit unknown, contradiction, and out-of-scope records;
- bounded personality-bias adjustment behind an ambiguity gate;
- deterministic coherent-scenario construction;
- ordinal support and disposition, not probabilities;
- stable deterministic identifiers;
- explicit component versions;
- typed failure phases;
- design-fixture validation and runtime-eligibility reporting;
- direct Perception -> Interpretation integration tests;
- direct Interpretation -> Appraisal -> Dynamic State integration tests;
- direct Perception -> Interpretation -> CognitiveTurnRunner connected tests;
- the small cognitive-turn contract changes required to validate the new
  interpretation ownership and provenance; and
- README status updates.

### Excluded

- a generative LLM call;
- a learned interpretation classifier;
- numerical probability or confidence claims;
- online learning or per-turn weight updates;
- broad open-domain semantic parsing;
- automatic personality compilation from raw core memory;
- goal formation;
- appraisal logic inside interpretation;
- dynamic-state mutation;
- metacognitive route selection;
- conversational action selection;
- response generation or tool execution;
- persistence or database migrations;
- live-chat routing; and
- retiring the legacy inspector in the same task.

The design fixture contains expected appraisal, state, and routing behavior so
humans can review the end-to-end meaning. Interpretation V0 must not implement
those downstream systems or copy their expected outputs into its envelope.

## 4. Central boundaries

### 4.1 Perception versus interpretation

Perception reports observable signals. Interpretation proposes meanings.

```text
question punctuation                       -> perception cue
request_information                        -> interpretation hypothesis
explicit correction wording                -> perception cue
repair_shared_understanding                 -> interpretation hypothesis
negation token and span                     -> perception cue
negative semantic-frame polarity            -> interpretation result
trusted tool failure                        -> perception/runtime fact
failure is attributable to a specific actor -> interpretation hypothesis
```

Interpretation must not rewrite cue states, detector provenance, observations,
or source spans.

### 4.2 Interpretation versus appraisal

Interpretation answers:

> What coherent meanings are supported by the evidence?

Appraisal answers:

> What does each retained meaning imply for Columbina's goals, values,
> capability, relationship, and situation?

Interpretation must not emit goal congruence, controllability, valence, arousal,
or another appraisal/state value. It supplies the semantic frames, hypotheses,
unknowns, contradictions, and coherent scenarios that appraisal evaluates.

### 4.3 Interpretation versus routing

Interpretation may expose facts needed by a later router, such as unresolved
user-owned information, conflicting retained alternatives, out-of-scope
predicates, and high-consequence semantic content. It must not choose `fast`,
`slow`, `clarify`, or `abstain_or_escalate` in this task.

### 4.4 Evidence versus priors

Current message evidence and trusted runtime facts outrank retrieved memory,
relationship priors, and personality biases. A prior may adjust an
underdetermined hypothesis. It is never recorded as observed evidence.

## 5. Canonical contract ownership

Create one shared contract module for objects crossing Interpretation,
Appraisal, Dynamic State, and Cognitive Turn. Recommended files:

```text
backend/app/ai/Cognitive/system/contracts/
|- __init__.py
`- interpretation.py
```

Move or define these canonical classes there:

```text
SemanticFrame
InterpretationHypothesis
InterpretationScenario
InterpretationEvidence
UnknownInput
Contradiction
OutOfScopeRecord
InterpretationVersions
InterpretationEnvelope
```

Then update:

```text
interpretation_v0 -> imports and emits contracts.interpretation
appraisal         -> imports contracts.interpretation
dynamic_state     -> imports canonical SemanticFrame transitively or directly
cognitive_turn    -> imports canonical InterpretationEnvelope
```

Temporary re-exports from `appraisal.models` are permitted to avoid breaking
existing imports during the atomic migration, but there must be only one class
definition for every shared concept. Tests must assert object identity, not
only structural equality:

```python
from app.ai.Cognitive.system.appraisal.models import SemanticFrame as LegacyImport
from app.ai.Cognitive.system.contracts.interpretation import SemanticFrame

assert LegacyImport is SemanticFrame
```

The canonical contract module must contain no engine logic and must not import
the appraisal, dynamic-state, or cognitive-turn packages.

## 6. Recommended implementation files

```text
backend/app/ai/Cognitive/system/contracts/
|- __init__.py
`- interpretation.py

backend/app/ai/Cognitive/system/interpretation_v0/
|- __init__.py
|- models.py
|- ontology.py
|- evidence.py
|- semantic_grounding.py
|- rules.py
|- bias.py
|- scenarios.py
|- engine.py
`- fixture_validator.py

backend/tests/
|- test_interpretation_v0_contract.py
|- test_interpretation_v0.py
`- test_cognitive_pipeline_v0.py
```

Exact filenames may follow repository conventions, but responsibilities and
dependency direction must remain clear:

```text
perception_v0.models
        |
        +--> contracts.interpretation <-- appraisal.models
        |              ^                       |
        v              |                       v
interpretation_v0 -----+                dynamic_state
        |
        v
cognitive_turn
```

No downstream package may import an Interpretation V0 engine implementation.

## 7. Processing contract

Interpretation V0 is deterministic and shadow-only:

```python
class InterpretationEngine:
    def interpret(self, request: InterpretationRequest) -> InterpretationEnvelope:
        ...
```

Given identical validated inputs and component versions, it must return an
equal envelope, including equal identifiers and ordering. It may not read the
clock, generate UUIDs, inspect global conversation state, call the network, or
consult an LLM.

Stable identifiers must be derived from canonical input and version data using
the same deterministic hash strategy used by Perception V0. Every identifier
must include `cognitive_turn_id` or the owning interpretation trace identity so
locally repeated message text cannot collide across turns.

## 8. Upstream Perception V0 handoff

Interpretation must accept the exact object produced by:

```python
handoff = perception_event.interpretation_handoff()
```

Do not serialize, flatten, or reconstruct its cue activations.

Before implementing Interpretation, extend
`PerceptionInterpretationHandoff` with the ownership fields needed for exact
validation:

```python
class PerceptionInterpretationHandoff(FrozenModel):
    perception_event_id: str
    cognitive_turn_id: str
    input_message_ids: tuple[str, ...]
    focus_message_id: str
    runtime_snapshot_id: str
    context_truncated: bool
    cue_activations: tuple[CueActivation, ...]
    versions: PerceptionVersions
```

`PerceptionEvent` should also retain `runtime_snapshot_id`, copied from the
validated `PerceptionRequest`. Existing Perception tests must be updated.

Handoff invariants:

1. cognitive-turn, perception-event, message, focus, runtime, and version ids
   are non-empty;
2. `focus_message_id` belongs to `input_message_ids`;
3. only `v0_active` cue activations are present;
4. there is exactly one activation for every active cue in manifest order;
5. every activation retains its original object identity from the event;
6. the handoff versions exactly equal the originating event versions;
7. shadow, defined, deprecated, and retired cues cannot enter the handoff; and
8. JSON serialization round-trips.

The 23-case ontology-design fixture may mention shadow cues. This does not
authorize Interpretation V0 to read them. Do not duplicate a shadow detector in
Interpretation merely to satisfy a design-only expected result.

## 9. Interpretation request

Recommended logical shape:

```python
class InterpretationRequest(FrozenModel):
    schema_version: Literal[1] = 1
    cognitive_turn_id: str
    conversation_ref: str
    perception: PerceptionInterpretationHandoff
    messages: tuple[PerceptionMessage, ...]
    runtime: InterpretationRuntimeContext
    memories: tuple[InterpretationMemory, ...] = ()
    relationship: InterpretationRelationshipContext | None = None
    biases: InterpretationBiasProfile
    ontology_version: str = "interpretation-ontology-v1"
    feature_manifest_version: str = "interpretation-features-v1"
```

The request contains bounded message text because deterministic semantic
grounding requires it. The resulting envelope retains message references and
evidence spans, not a second raw transcript.

### 9.1 Runtime context

Recommended minimum shape:

```python
class TaskContract(FrozenModel):
    task_type: str
    required_parameters: tuple[str, ...] = ()
    provided_parameters: Mapping[str, JsonValue] = Field(default_factory=dict)


class InterpretationRuntimeContext(FrozenModel):
    snapshot_id: str
    known_predicates: tuple[str, ...]
    known_entities: tuple[str, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    task_contract: TaskContract | None = None
    authorization: str | None = None
    safety_decision: str | None = None
    last_tool_event: ToolEventFact | None = None
```

This is trusted runtime input, not a copy of user assertions. The runtime
snapshot id must equal the perception handoff's runtime snapshot id.

The known-predicate registry should normally be code-owned and versioned. If it
is represented in the request, the request must record the registry version and
must not accept arbitrary user-provided entries as trusted known predicates.

### 9.2 Retrieved evidence

Interpretation memory records must retain, at minimum:

```python
class InterpretationMemory(FrozenModel):
    memory_id: str
    memory_type: str
    content: str
    provenance: str
    status: str
    retrieval_score: float | None = None
    version: int | None = None
```

Only active, authorized records may influence hypotheses. Retrieval score is a
retrieval fact, not truth confidence. A stale or contradicted memory must remain
visible as conflicting or ignored evidence rather than silently winning.

### 9.3 Request invariants

The request validator must reject:

1. blank identities or version strings;
2. a request turn id different from the perception turn id;
3. messages whose ordered ids differ from
   `perception.input_message_ids`;
4. a focus id that is missing, duplicated, or not the last supplied message;
5. any message after the focus message;
6. a runtime snapshot id different from the perception runtime snapshot id;
7. duplicate memory ids;
8. non-finite bias or retrieval values;
9. raw core-memory JSON in place of a compiled bias profile;
10. unsupported language-dependent execution without an explicit unknown path;
11. a shadow cue activation manually inserted into the handoff; and
12. mutable nested mappings or non-JSON values.

The runner must not silently create messages, cues, memories, runtime facts, or
personality values that were not supplied.

## 10. Fixed Interpretation V0 ontology

Create a static, versioned ontology manifest. Runtime code must not accept
arbitrary hypothesis keys.

### 10.1 Communicative acts

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

### 10.2 Speaker goals

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

### 10.3 Modalities

```text
interpretation.modality.asserted
interpretation.modality.tentative
interpretation.modality.questioned
interpretation.modality.requested
interpretation.modality.hypothetical
interpretation.modality.quoted
```

### 10.4 Pragmatic meanings

```text
interpretation.pragmatic.literal
interpretation.pragmatic.indirect
interpretation.pragmatic.tone_situation_mismatch
interpretation.pragmatic.unknown
```

Do not add `sarcastic`, `angry`, `hostile`, `deceptive`, user-emotion labels, or
another unreviewed key. Such possibilities may appear only in an unknown or
out-of-scope note with explicit uncertainty.

### 10.5 Ontology definitions

Each manifest entry requires:

```python
class HypothesisDefinition(FrozenModel):
    hypothesis_key: str
    definition_version: int
    dimension: HypothesisDimension
    description: str
    compatible_with: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    rule_references: tuple[str, ...]
    lifecycle_status: Literal["v0_active"] = "v0_active"
```

The manifest validator must reject duplicate keys, missing rule
implementations, cross-dimension mistakes, self-conflicts, unknown compatibility
references, and definitions without operational descriptions.

## 11. Evidence contract

Interpretation evidence must be explicit and resolvable. Recommended registry:

```python
class InterpretationEvidence(FrozenModel):
    evidence_id: str
    source_kind: InterpretationEvidenceKind
    source_ref: str
    message_id: str | None = None
    span: TextSpanEvidence | None = None
    cue_key: str | None = None
    observation_refs: tuple[str, ...] = ()
    runtime_snapshot_ref: str | None = None
    memory_ref: str | None = None
    semantic_frame_ref: str | None = None
```

Allowed source kinds:

```text
perception_cue
message_span
message_sequence
runtime_fact
memory_record
relationship_record
semantic_frame
```

Rules and biases are derivation provenance, not source evidence. Record them in
`rule_ids` and `bias_adjustment`, never as if they were observed facts.

Evidence validation must prove:

- message spans exactly match the supplied immutable message content;
- cue evidence refers to an activation in the exact perception handoff;
- cue observation references remain unchanged;
- runtime facts resolve against the supplied runtime snapshot;
- memory references resolve and retain status/provenance;
- semantic-frame references resolve;
- evidence ids are unique and stable;
- a source owned by another turn cannot be cited; and
- future messages cannot be cited.

Copying a small immutable evidence record into the interpretation envelope is
allowed. Copying or paraphrasing raw evidence without its source reference is
not.

## 12. Canonical semantic frame

Enrich the existing canonical `SemanticFrame` rather than creating a second
frame class:

```python
class SemanticFrame(FrozenModel):
    frame_id: str
    predicate: str
    arguments: Mapping[str, JsonValue]
    polarity: Literal["positive", "negative", "unknown"]
    evidence_refs: tuple[str, ...]
    unresolved_arguments: tuple[str, ...] = ()
    grounding_rule_ids: tuple[str, ...] = ()
```

If `source_spans` must remain temporarily for compatibility, it must be a
derived compatibility view over typed evidence references, not a second source
of truth.

Frame invariants:

1. ids, predicates, evidence refs, and rule ids are non-empty where required;
2. frame ids are unique inside an envelope;
3. all evidence references resolve;
4. unresolved arguments exist as keys with a null value;
5. resolved arguments cannot also be listed unresolved;
6. negation changes only the grounded proposition or argument in its validated
   scope;
7. quotation ownership is retained rather than attributed to the current
   speaker;
8. unknown predicates are represented explicitly and never added to the known
   predicate registry at runtime; and
9. argument mappings are deeply immutable and JSON-round-trippable.

## 13. Bounded semantic grounding

V0 is not a general semantic parser. Implement a versioned registry of narrow,
inspectable grounding rules.

Each predicate definition should include:

```python
class PredicateDefinition(FrozenModel):
    predicate: str
    definition_version: int
    lexical_or_structural_patterns: tuple[str, ...]
    allowed_arguments: tuple[str, ...]
    required_arguments: tuple[str, ...]
    consequence_band: str
    reversibility: str
    capability_key: str | None = None
```

The initial registry must cover the reviewed design-case predicates that are
safe and meaningful as V0 examples, including information questions, report
sending, appointment correction, offer acceptance/decline, acknowledgement,
evaluation, greeting/closing, train-time assertions, quoted/hypothetical
content, timer stopping, window closing, tool/capability failure, attachment
summarization, and consequential deletion.

Rules:

- Ground only values supported by exact text, trusted runtime facts, or an
  authorized uncontradicted memory.
- Missing required arguments remain null and appear in
  `unresolved_arguments`.
- User-owned missing information is never filled from personality.
- A retrieved memory may resolve an ambiguous alias only when the current
  message does not contradict it; cite the memory and retain the ambiguity when
  evidence remains weak.
- Known but unavailable/unauthorized actions remain known predicates. Capability
  failure is not `out_of_scope`.
- Unknown user-defined words or referents produce an `OutOfScopeRecord` and an
  explicit unknown frame instead of inventing a definition.
- Extremely short or empty input must return a valid bounded unknown result, not
  crash and not manufacture an act.
- Do not reimplement Perception shadow cue detectors inside grounding to bypass
  their lifecycle state.
- Stable rule ordering and explicit tie-breaking are mandatory.

## 14. Hypothesis contract

Use ordinal support only:

```python
class HypothesisSupport(StrEnum):
    UNSUPPORTED = "unsupported"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class HypothesisDisposition(StrEnum):
    LEADING = "leading"
    RETAINED_ALTERNATIVE = "retained_alternative"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
```

Recommended record:

```python
class BiasAdjustment(FrozenModel):
    bias_key: str
    direction: Literal["increase", "decrease", "unchanged"]
    ordinal_shift: Literal[-1, 0, 1]
    ambiguity_gate: float
    policy_version: str


class InterpretationHypothesis(FrozenModel):
    hypothesis_id: str
    hypothesis_key: str
    definition_version: int
    dimension: HypothesisDimension
    base_support: HypothesisSupport
    support: HypothesisSupport
    disposition: HypothesisDisposition
    semantic_frame_refs: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    rule_ids: tuple[str, ...]
    bias_adjustments: tuple[BiasAdjustment, ...] = ()
```

Hypothesis invariants:

- the key exists in the ontology and matches its dimension/version;
- every reference resolves;
- supported hypotheses contain admissible supporting evidence;
- `unsupported` hypotheses cannot be leading or retained;
- `unknown` disposition has an explicit unknown reference;
- a rejected hypothesis remains available for audit only when a rule actually
  evaluated or contradicted it;
- `base_support` is calculated before personality adjustment;
- final support differs by at most one ordinal level from base support in V0;
- an ambiguity gate of zero requires no effective adjustment;
- no hypothesis contains a numeric probability or uncalibrated confidence; and
- ordering is deterministic: dimension order, then descending support, then
  ontology order, then stable id.

## 15. Deterministic hypothesis rules

Rules must be code-owned, named, versioned, and independently testable. At
minimum implement the accepted high-level behavior:

1. question form without an explicit action request supports
   `act.request_information`, `goal.obtain_information`, and questioned
   modality;
2. an explicit action request supports `act.request_action`,
   `goal.cause_action`, and requested modality even when grammatical form is a
   question;
3. explicit correction supports `act.correct` and
   `goal.repair_shared_understanding`;
4. an explicit acceptance/confirmation is distinct from acknowledgement;
5. `No thanks` or equivalent refusal is rejection/decline, not correction;
6. an uncertainty marker supports tentative modality for the speaker's
   proposition; it does not make model support uncertain automatically;
7. negation changes semantic scope and is not an act by itself;
8. quotation and hypothetical ownership remain explicit and may be multi-label;
9. a complete low-consequence factual assertion supports inform/asserted/literal;
10. conventional indirect conversational action may receive at most moderate
    support while the literal interpretation remains retained;
11. an indirect hypothesis never authorizes external tool execution;
12. positive/negative tone mismatch may be produced only from currently
    approved evidence; V0 must not consume shadow cues;
13. repeated unresolved requests preserve the original act and add evidence of
    repetition; repetition does not increase authorization;
14. a trusted runtime conflict outranks a user assumption and stale memory;
15. unknown goals use `interpretation.goal.unknown` instead of an invented hidden
    motive; and
16. unknown or out-of-scope semantic content remains unknown rather than being
    forced to the nearest known act.

No rule may directly emit an appraisal dimension, dynamic-state contribution,
route, action, or response.

## 16. Unknown, contradiction, and out-of-scope records

Use separate concepts:

```python
class UnknownInput(FrozenModel):
    unknown_id: str
    key: str
    owner: Literal["user", "runtime", "system", "unknown"]
    status: Literal["unresolved"] = "unresolved"
    evidence_refs: tuple[str, ...] = ()


class Contradiction(FrozenModel):
    contradiction_id: str
    status: Literal["active", "resolved"]
    description: str
    evidence_refs: tuple[str, ...]
    resolution_ref: str | None = None


class OutOfScopeRecord(FrozenModel):
    out_of_scope_id: str
    surface_form: str
    reason: str
    evidence_refs: tuple[str, ...]
    recoverable_by_user: bool
```

Rules:

- missing argument is an unknown, not automatically out of scope;
- a known predicate with unavailable capability is not out of scope;
- contradictory sources are retained with provenance;
- only a real resolution may mark a contradiction resolved;
- absence of evidence is not contradiction;
- an empty tuple of out-of-scope records means `out_of_scope == false`;
- if a compatibility Boolean is retained, derive it from the records; and
- ids are deterministic and unique.

## 17. Compiled personality-bias input

Interpretation must consume a compiled bounded profile, not raw core-memory
JSON:

```python
class InterpretationBiasProfile(FrozenModel):
    profile_id: str
    compiler_version: str
    source_core_memory_ref: str
    benign_prior: float
    trust_prior: float
    threat_sensitivity: float
    confirmation_tendency: float
    self_attribution_tendency: float
    ambiguity_tolerance: float
    social_affiliation_prior: float
```

All values are finite and in `[0, 1]`; `0.5` is neutral.

Because the full Personality Runtime Mapper is not yet implemented, tests may
construct explicit compiled profiles. Runtime code must not silently fabricate a
neutral profile. A caller that wants neutral behavior must supply an explicit
versioned neutral profile.

### 17.1 Ambiguity gate

Compute base hypotheses first. Personality adjustment is allowed only when
evidence genuinely underdetermines meaning:

```text
final_support(h) =
    clamp_ordinal(
        base_support(h)
        + ambiguity_gate(h) * bounded_adjustment(h)
    )
```

V0 may implement the product using deterministic thresholds, but must record
the gate, direction, and final integer ordinal shift. Maximum shift is one
ordinal level.

The ambiguity gate must be zero for:

- trusted runtime facts;
- explicit requests and corrections;
- validated negation scope;
- quotation ownership;
- grounded arguments;
- explicit current-message rejection;
- safety, authorization, and capability facts; and
- evidence contradicted by a current explicit correction.

Biases may weakly reorder ambiguous goals and acts, moderately adjust pragmatic
alternatives, and preserve alternatives longer through ambiguity tolerance.
They may not remove a supported alternative, manufacture evidence, fill a
semantic argument, or convert unknown agency into Columbina agency.

## 18. Coherent scenario builder

Appraisal evaluates coherent scenarios, not independent labels and not a
Cartesian product of every hypothesis.

Retain the canonical downstream-compatible shape, enriched where necessary:

```python
class InterpretationScenario(FrozenModel):
    scenario_id: str
    support: ScenarioSupport
    disposition: ScenarioDisposition
    semantic_frame_refs: tuple[str, ...]
    hypothesis_refs: Mapping[str, tuple[str, ...]]
    supporting_evidence_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    compatibility_rule_ids: tuple[str, ...] = ()
```

### 18.1 Builder rules

1. Include only mutually compatible hypotheses.
2. Never combine explicit `request_action` with a rejected
   `request_information` interpretation merely because question form exists.
3. Multi-label modalities such as quoted plus hypothetical may coexist.
4. Hypotheses in a scenario must address the included semantic frame or the
   same discourse event.
5. A contradiction that materially changes meaning creates separate retained
   branches; it is not averaged away.
6. Unsupported/rejected hypotheses cannot enter retained scenarios.
7. Weak hypotheses enter a scenario only when required to represent an explicit
   unresolved alternative; normally retained alternatives require moderate or
   strong support.
8. At least one leading or retained scenario must always exist, including an
   explicit bounded unknown scenario for empty/out-of-scope input.
9. The leading scenario is selected by deterministic support, evidence quality,
   contradiction burden, ontology order, and stable-id tie-breakers.
10. High-consequence alternatives remain retained when plausible even if a
    benign alternative leads.
11. Scenario support is derived from member support using one versioned ordinal
    mapping; it is not called a probability.
12. Scenario ids include turn, perception, frame, and hypothesis ownership.

### 18.2 Scenario validation

Every scenario reference must resolve. Additionally:

- frame ids, hypothesis ids, unknown ids, contradiction ids, and evidence ids
  are unique;
- `hypothesis_refs` keys are accepted dimensions;
- every referenced hypothesis has the matching dimension;
- every appraisal-facing event reference belongs to a frame in that scenario;
- a leading scenario cannot have `unsupported` support;
- only one scenario should be leading unless a documented exact tie policy
  intentionally permits more; and
- retained branches must not contain mutually contradictory claims without an
  explicit branch/contradiction representation.

## 19. Interpretation versions

The output must record emitted versions at creation time:

```python
class InterpretationVersions(FrozenModel):
    schema: str
    ontology: str
    feature_manifest: str
    semantic_grounder: str
    hypothesis_rules: str
    scenario_builder: str
    bias_policy: str
    evidence_validator: str
    perception_schema: str
    perception_cue_manifest: str
    perception_detectors: str
```

All fields are explicit and non-empty. Perception versions must be copied from
the exact handoff rather than reconstructed from installed constants.

`feature_manifest` records which perception cues and other features each
interpretation rule version is allowed to consume. The engine must reject a
feature manifest that expects a cue unavailable in the supplied perception
manifest.

## 20. Canonical InterpretationEnvelope

Recommended complete shape:

```python
class InterpretationEnvelope(FrozenModel):
    schema_version: Literal[1] = 1
    interpretation_trace_id: str
    cognitive_turn_id: str
    conversation_ref: str
    perception_event_id: str
    input_message_ids: tuple[str, ...]
    focus_message_id: str
    runtime_snapshot_id: str

    evidence: tuple[InterpretationEvidence, ...]
    semantic_frames: tuple[SemanticFrame, ...]
    hypotheses: tuple[InterpretationHypothesis, ...]
    scenarios: tuple[InterpretationScenario, ...]
    unknowns: tuple[UnknownInput, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    out_of_scope_records: tuple[OutOfScopeRecord, ...] = ()

    context_truncated: bool = False
    versions: InterpretationVersions
```

Optional convenience views such as `hypotheses_by_dimension`,
`hypothesis_keys`, or `out_of_scope` must be derived properties. Do not store a
second conflicting representation.

### 20.1 Envelope invariants

The canonical model validator must enforce:

1. exact ownership by cognitive turn, conversation, perception event, messages,
   focus message, and runtime snapshot;
2. non-empty explicit versions;
3. deterministic unique ids for every contained record;
4. complete resolution of every internal reference;
5. hypothesis key/dimension/version consistency with the ontology;
6. scenario coherence and at least one retained scenario;
7. no shadow perception cue reference;
8. no future-message evidence;
9. no ungrounded resolved semantic argument;
10. no unsupported leading or retained hypothesis;
11. no personality adjustment beyond the approved boundary;
12. out-of-scope status derived from actual records;
13. deep immutability; and
14. JSON serialization round-trip equality.

This validator is a trust boundary. It must reject logically inconsistent
objects returned by injected or future stage implementations even when those
objects are already Pydantic model instances.

## 21. Required execution sequence

The engine must execute these phases in order:

```text
1. validate request and exact perception handoff
2. build the evidence registry
3. ground semantic frames
4. record unknowns, contradictions, and out-of-scope content
5. compute unbiased base hypotheses
6. apply bounded ambiguity-gated personality adjustments
7. assign final dispositions deterministically
8. build coherent scenarios
9. construct and validate the canonical envelope
```

No phase may mutate an earlier phase's result. A failure returns no completed
envelope.

## 22. Failure contract

Define a typed exception:

```python
InterpretationFailurePhase = Literal[
    "validation",
    "evidence",
    "semantic_grounding",
    "hypothesis_rules",
    "bias_adjustment",
    "scenario_building",
    "envelope_validation",
]


class InterpretationExecutionError(RuntimeError):
    cognitive_turn_id: str
    failed_phase: InterpretationFailurePhase
    safe_message: str
```

Requirements:

- preserve the original exception as `__cause__`;
- do not expose message content, memory content, database details, secrets, or
  provider internals in the safe message;
- do not return partial frames or hypotheses as a successful envelope;
- do not mutate the perception event, messages, runtime, memories, relationship,
  or bias profile;
- do not substitute legacy interpretation, an LLM answer, or a fabricated
  neutral interpretation; and
- do not persist partial output.

A durable failure trace remains future work.

## 23. Cognitive-turn and appraisal integration changes

The enriched envelope must be consumed directly by appraisal. No adapter may
discard hypotheses, evidence, alternatives, or ownership fields.

### 23.1 Appraisal request validation

Require:

```text
AppraisalRequest.cognitive_turn_id
    = InterpretationEnvelope.cognitive_turn_id
```

Every appraisal frame must also satisfy:

- `scenario_id` resolves to a retained interpretation scenario;
- `subject.event_refs` are semantic frames belonging to that scenario;
- `focal_goal_ref`, value refs, relationship ref, and capability ref resolve;
- dimension evidence/unknown/conflict refs resolve against the interpretation
  and appraisal request; and
- aggregate dominant-frame ids resolve to emitted appraisal frames.

### 23.2 Appraisal version provenance

Appraisal must copy `interpretation.versions.scenario_builder` into its emitted
version record. It must not hard-code a second scenario-builder version that can
disagree with the interpretation envelope.

### 23.3 CognitiveTurnRequest validation

Require:

```text
request.cognitive_turn_id = interpretation.cognitive_turn_id
request.conversation_ref  = interpretation.conversation_ref
request.input_message_ids = interpretation.input_message_ids
request.focus_message_id  = interpretation.focus_message_id
```

Add `focus_message_id` to `CognitiveTurnRequest` and `CognitiveTurnTrace` so the
incoming event that owns the turn remains distinguishable from older bounded
context messages. Require it to resolve exactly once in `input_message_ids` and
to match the interpretation focus. Existing callers that pass only one incoming
message use that message as the focus.

The cognitive-turn trace must record interpretation versions from the envelope,
not infer them only from appraisal.

### 23.4 Existing turn-audit hardening

Before claiming connected end-to-end acceptance, also close the previously
identified state ownership gap:

- add `conversation_ref` to `StateSnapshot`;
- require previous and new snapshots to match the cognitive-turn conversation;
- require `snapshot.personality_mapper_version` to match the supplied profile;
- validate outcome, branch, dimension, aggregate, value, and relationship
  references; and
- add adversarial fail-closed tests.

These fixes are not interpretation inference. They are necessary contract
validation for the connected test to mean what it claims.

## 24. Design-fixture policy

The authoritative design files are:

```text
data/INTERPRETATION_DESIGN_CASES_README.md
data/interpretation_design_cases.json
```

The fixture has two roles and they must not be confused.

### 24.1 Structural/ontology validation

Validate all 23 cases for:

- allowed cue and hypothesis keys;
- support and disposition vocabulary;
- exact text spans;
- resolvable runtime JSON pointers;
- unique ids;
- valid appraisal expectation shapes;
- valid routing and state-direction vocabulary;
- expected-out-of-scope presence;
- bias bounds and maximum ordinal shifts;
- pending review status; and
- required coverage.

### 24.2 Live runtime conformance

Classify each case explicitly as:

```text
live_v0_eligible
ontology_only_shadow_dependency
blocked_by_unimplemented_external_context
```

For `live_v0_eligible` cases, create actual `PerceptionRequest` objects, run the
real Perception V0 engine, pass the exact handoff to the real Interpretation V0
engine, and compare the runtime output with the applicable expected semantic
frames and hypotheses.

An `ontology_only_shadow_dependency` case must not be made to pass by reading a
shadow cue. Instead assert that the shadow activation is absent from the live
handoff and report which expected ontology behavior is deferred until cue
promotion.

The fixture remains `draft_for_human_review`. Passing a deterministic rule test
does not convert the fixture into reviewed training data.

## 25. Tests required immediately

Do not wait for action selection, routing, persistence, or learning. Test this
stage during the same implementation task.

### 25.1 Contract tests

Test:

- frozen/extra-forbidden models;
- deep mapping immutability;
- invalid enum/key/version rejection;
- duplicate-id rejection;
- unresolved-reference rejection;
- message-span validation;
- JSON round trips;
- stable deterministic ids; and
- canonical-class identity across compatibility imports.

### 25.2 Perception handoff tests

Test:

- exact active cue instances reach Interpretation;
- shadow cues cannot enter;
- message, focus, turn, runtime, and perception ids match;
- perception versions are copied exactly; and
- a forged handoff fails closed.

### 25.3 Semantic-grounding tests

At minimum cover:

- factual question;
- action request phrased as a question;
- informative statement about the same action;
- correction with negation scope;
- explicit decline;
- complete and incomplete action frames;
- self-reference and agency roles;
- quoted plus hypothetical content;
- known but unavailable capability;
- trusted tool failure;
- multi-turn reference evidence;
- unknown predicate/referent;
- empty or extremely short input;
- attachment grounding; and
- consequential irreversible action content.

### 25.4 Hypothesis-rule tests

Cover every ontology key with a positive case and every dimension with a
contrast. Specifically verify:

- question does not override explicit action request;
- correction differs from decline;
- confirmation differs from acknowledgement;
- speaker uncertainty differs from model uncertainty;
- quoted/hypothetical modalities can coexist;
- indirect request preserves literal alternative;
- unknown goal remains unknown;
- repetition does not create authorization; and
- runtime fact overrides stale memory.

### 25.5 Bias-boundary tests

Hold evidence constant and vary only the bias profile. Prove:

- explicit request/correction and grounded arguments do not change;
- negation and quotation ownership do not change;
- runtime conflicts do not change;
- ambiguous pragmatic alternatives shift by no more than one level;
- competing supported alternatives remain present;
- high self-attribution cannot manufacture Columbina agency; and
- high ambiguity tolerance preserves alternatives rather than increasing
  certainty.

### 25.6 Scenario tests

Test:

- coherent combinations only;
- no accidental Cartesian product;
- deterministic leading selection;
- retained incompatible branches;
- high-consequence alternative retention;
- unknown/out-of-scope scenario generation;
- complete reference resolution; and
- stable ordering and ids.

### 25.7 Failure and immutability tests

Inject a failure in every phase and assert:

- the typed phase is correct;
- the original cause is preserved;
- no downstream phase runs;
- no complete envelope is returned; and
- every input dump remains byte-for-byte/structurally unchanged.

### 25.8 Connected real-engine tests

Use real engines rather than mocks for at least these paths:

```text
message -> PerceptionEngine -> InterpretationEngine
        -> AppraisalEngine -> DynamicStateUpdater
```

and:

```text
message -> PerceptionEngine -> InterpretationEngine
        -> CognitiveTurnRunner -> AppraisalEngine -> DynamicStateUpdater
```

Assert exact object identity for the canonical interpretation envelope,
semantic frames, scenarios, and appraisal-to-state objects at every handoff.

Required connected cases:

1. clear factual information request;
2. explicit action request with a missing user-owned argument;
3. explicit correction;
4. ambiguous indirect meaning with a retained literal alternative;
5. trusted runtime conflict;
6. unknown/out-of-scope predicate;
7. consequential irreversible request; and
8. cross-conversation state rejection.

### 25.9 Regression suites

All existing Perception, Appraisal, Dynamic State, Cognitive Turn, API, and
legacy inspector tests must remain green. Live behavior must not change.

## 26. Acceptance criteria

The task is complete only when all of the following are true:

- one canonical Interpretation contract exists;
- compatibility imports point to the same classes, not copied schemas;
- one deterministic `InterpretationEngine` exists;
- it consumes the exact Perception V0 active-cue handoff;
- shadow cues have zero live influence;
- request ownership matches turn, conversation, messages, focus, perception,
  and runtime identities;
- semantic frames are bounded, evidence-linked, and immutable;
- every supported hypothesis has resolvable evidence and rule provenance;
- base support is separated from bounded personality adjustment;
- no uncalibrated probabilities are emitted;
- unknowns, contradictions, and out-of-scope content remain explicit;
- coherent retained scenarios are built without a Cartesian product;
- at least one retained scenario exists for every valid request;
- emitted versions are explicit and consistent;
- output ids and ordering are deterministic;
- the canonical envelope JSON-round-trips;
- malformed injected outputs fail closed;
- the exact envelope enters Appraisal and Cognitive Turn without conversion;
- appraisal frame scenario/event references resolve;
- cognitive-turn message and interpretation ownership resolve;
- dynamic state is rejected across conversation boundaries;
- all required direct and connected tests pass;
- the full backend regression suite passes;
- no network, LLM, database, persistence, action, or live-chat behavior is added;
  and
- the Cognitive README accurately distinguishes implemented Interpretation V0
  from the legacy inspector and future learned interpretation.

## 27. Required implementation order

Implement in this order so failures remain local:

1. review the 23-case fixture and produce the runtime-eligibility classification;
2. create/migrate the canonical shared interpretation contracts;
3. extend the Perception handoff ownership fields and tests;
4. implement the ontology, predicate, rule, and feature manifests;
5. implement evidence validation;
6. implement bounded semantic grounding;
7. implement unbiased hypothesis rules;
8. implement bounded personality adjustment;
9. implement coherent scenario construction;
10. implement the engine and typed failures;
11. update Appraisal to consume and validate the enriched envelope;
12. harden Cognitive Turn and state conversation ownership;
13. add direct and connected integration tests;
14. run the complete regression suite; and
15. update README implementation status.

Do not begin with database tables or live routing.

## 28. Required final report from the implementation agent

The agent implementing this brief must report:

- every file changed;
- the final canonical dependency direction;
- the number of fixture cases in each runtime-eligibility category;
- ontology and predicate coverage;
- any design cases not executable in live V0 and the exact reason;
- all contract migrations and compatibility re-exports;
- connected test results;
- full regression-suite results;
- open ontology or grounding questions;
- confirmation that shadow cues have zero live influence;
- confirmation that no live-chat, database, network, or LLM behavior changed;
  and
- `git diff --check` result.

## 29. Explicitly forbidden shortcuts

Do not:

- return the legacy `IntentInterpreter` result;
- create a second incompatible `InterpretationEnvelope`;
- collapse all dimensions into one winning intent;
- map a winning intent directly to user emotion;
- pass shadow cues into live rules;
- re-detect a shadow cue inside Interpretation to bypass its lifecycle;
- use personality or memory as observed evidence;
- fill missing arguments from plausibility alone;
- call an LLM for semantic parsing;
- emit fabricated confidence/probability values;
- combine every hypothesis by Cartesian product;
- discard a consequential alternative because a benign reading ranks first;
- hard-code fixture outputs by case id or exact full-message lookup;
- reconstruct historical versions from current constants;
- mutate Perception/Appraisal/Dynamic State inputs; or
- claim the whole cognitive system is complete when only the forward shadow
  interpretation path is implemented.

## 30. Central implementation rule

> Interpretation V0 proposes bounded, evidence-linked possible meanings. It
> preserves uncertainty and alternatives, emits one canonical contract, and
> connects directly to appraisal without inventing facts, probabilities,
> personality evidence, authorization, or downstream behavior.
