# Interpretation V0 implementation brief

Status: model-backed semantic parser implemented; 2026-09-29 remediation implemented (P0 + P1, P2 partial); not independently audited
Target: shadow-only hybrid runtime (model parsing, deterministic validation and policy)
Upstream: implemented Perception V0
Downstream: implemented appraisal, dynamic state, and cognitive-turn spine

Revision authority: the 2026-09-29 model-backed revision in this document
supersedes older requirements for deterministic semantic parsing. The former
rule grounder may remain only as offline fixture/reference infrastructure. It
must not be selected by the live-shadow worker and must never be a provider
failure fallback.

## 1. Objective

Implement and harden the model-backed Interpretation V0 runtime described by
the Cognitive system design:

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
                 Interpretation V0
              |- GPT-4o-mini semantic candidates
              |- deterministic candidate validation
              |- deterministic multidimensional hypotheses
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

The semantic model is a parser, not the conversational assistant. Its request
contains only bounded role-labelled messages, the focus-message reference,
code-owned focus evidence regions, and the code-owned predicate/argument
manifest. It receives no Core Memory profile, personality bias, retrieved
memory, relationship variables, runtime facts, goal state, action state,
tools, or realization instructions. Those omissions are structural and must be
covered by serialization tests.

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
- `contracts/interpretation.py` owns the canonical immutable envelope, frames,
  hypotheses, scenarios, evidence, unknowns, contradictions, out-of-scope
  records, and version contracts. Appraisal compatibility imports point to the
  same classes.
- `InterpretationEnvelope` carries cognitive-turn/message/focus/runtime
  ownership, Perception provenance, full hypotheses, evidence records,
  out-of-scope records, and intrinsic component versions.
- `CognitiveTurnRunner` consumes that canonical envelope directly without a
  reduced compatibility conversion.
- `interpretation_v0/model_protocol.py` and `model_grounding.py` implement the
  provider-neutral candidate boundary and deterministic compiler.
- `ai/Cognitive/system/v0/semantic_parser_provider.py` is the only semantic-parser module
  allowed to import the OpenAI SDK or make the provider call.
- `services/cognitive_shadow.cognitive_pipeline()` installs the model-backed
  grounder for every live-shadow observation. `live_shadow.build_pipeline()`
  remains a pure offline/reference constructor and is not the worker path.
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
| The original appraisal-owned `InterpretationEnvelope` contained scenarios but not the actual hypotheses, evidence registry, source ownership, or interpretation versions. | It has been replaced atomically by the enriched shared canonical envelope; compatibility imports point to the same classes. |
| The design fixture may contain shadow cues, while implemented Perception V0 correctly filters them from its interpretation handoff. | Split ontology-design validation from live-runtime conformance. Shadow-dependent expectations remain deferred and have zero live influence. |
| Perception's bounded message list includes context, but the focus message is the incoming event that owns the current turn. Cognitive Turn currently records only `input_message_ids`. | Preserve both the ordered bounded input ids and an explicit `focus_message_id` through Perception, Interpretation, Cognitive Turn, and the trace. |
| The original Perception handoff lacked its full message-id list and runtime-snapshot identity even though downstream evidence needs both. | The handoff now carries `input_message_ids`, `runtime_snapshot_id`, and `context_truncated`, copied exactly from the event. |
| The original design expected personality bias before the Personality Runtime Mapper existed. | Interpretation now accepts an explicit compiled, versioned bias profile. It never reads raw core memory or silently creates a neutral profile, and the profile is not projected into the semantic-model payload. |
| The fixture contains routing expectations, but the metacognitive router is not implemented and routing is outside interpretation. | Validate that Interpretation exposes the evidence/unknowns needed by the expected route; do not emit the route from Interpretation V0. |
| Existing semantic frames use weak string `source_spans`, while Perception V0 has typed evidence with exact offsets and runtime provenance. | Canonical semantic frames cite typed, resolvable evidence records. A compatibility view may be derived temporarily. |
| The fixture remains `draft_for_human_review`, but runtime implementation is now requested. | It guides deterministic coverage and contrasts, but is not training truth or fully approved semantics. Report every runtime/deferred discrepancy for human review. |
| The README still describes perception/interpretation as design in some status sections. | Keep README status aligned with the hybrid runtime; do not claim future trained, adaptive, or fine-tuned parser variants are implemented. |
| The original brief forbids an LLM and requires deterministic semantic rules, while live shadow now uses GPT-4o mini. | GPT-4o mini is the only live-shadow semantic parser. Deterministic code owns schema restriction, evidence ownership, normalization, frame construction, downstream hypothesis policy, bias application, and envelope validation. There is no rule-parser fallback. |
| Interpretation receives memory, relationship, bias, dialogue, and runtime fields, which could accidentally leak into the semantic-model request. | The provider payload is a separate closed contract. Only bounded messages, focus identity/evidence regions, and the predicate manifest may cross it. Request fields not explicitly projected are forbidden. |
| Sampled model output cannot reproduce a historical semantic digest reliably. | Record exact parser/compiler/model provenance and mark model-grounded traces non-replayable. Never describe temperature zero as determinism. |

Two deliberately separate concepts remain:

```text
input_message_ids = ordered bounded messages actually available as evidence
focus_message_id  = the incoming message/event that owns this cognitive turn
```

Do not redefine one to mean the other during implementation.

## 2.2 Current implementation and required fixes

Implemented on 2026-09-29 (initial wiring):

- `model_protocol.py` defines the closed provider-neutral payload and candidate
  contracts;
- `semantic_parser_provider.py` calls explicit `gpt-4o-mini` with strict JSON
  Schema, one candidate, temperature zero, no tools, no streaming, no provider
  storage, and no SDK retries;
- `ModelSemanticGrounder` compiles candidates only after checking the
  code-owned predicate registry, allowed/required arguments, focus-message
  evidence ownership, unresolved nulls, and canonical frame invariants;
- the live-shadow worker always installs the model-backed grounder and has no
  deterministic semantic fallback; and
- the parser payload excludes memory, relationship, personality/bias, runtime,
  goals, actions, and tools.

### 2.2.1 Remediation status (2026-09-29, second pass)

The fixes below were required, in priority order. Each row states what is now
implemented and its completion evidence. Nothing here is behaviorally accepted
or rollout-approved until an independent audit and a fresh end-to-end shadow
evaluation.

| Priority | Required fix | Status and evidence |
| --- | --- | --- |
| P0 | **Expand the predicate ontology.** | **Implemented.** `interpretation-predicates-v0.3` adds `social_question`, `identity_request`, `runtime_fact_request`, `elaboration_request`, `action_request`, `unsafe_request`, `correction`, `statement`, `constraint_statement`, and `express_gratitude`; `social_opening`/`acknowledge_message` arguments became optional and `unknown`'s label optional. Every predicate has a model-facing `description`, per-argument descriptions, closed vocabularies (`argument_values`), and a declared `act_family`. Only reviewed predicates (`model_exposed`) are offered to the model, specific predicates first and `unknown` last; the narrow design-case predicates stay offline-only because they lured the parser (for example `send_report` for an unrelated request). An optional `embedding` argument (`direct`/`quoted`/`reported`/`hypothetical`) keeps relayed content from becoming the speaker's request. Predicates still assert only attributed meaning. Evidence: `tests/test_semantic_parser_hardening.py` (manifest exposure, vocabulary, and corpus coverage of every compositional predicate). |
| P0 | **Model-specific structured regression corpus.** | **Implemented.** `data/semantic_parser_regression_corpus_v0.json` (`draft_for_human_review`): 24 single-turn cases covering every named family plus quoted-action, negated, mixed-polarity, hypothetical, greeting+request, correction+request, two-question, constraint, gratitude, closing, and degraded contrasts, and an uninterrupted 8-turn meal-planning trace. Each case has a reviewed frozen candidate and exact expectations (predicate multiset, polarity, unresolved arguments, embedding, required/closed argument values, leading/retained/rejected acts, leading goals); defensible alternative parses are listed as `any_of`. `interpretation_v0/regression.py` scores it (categories `parser_error.<code>`, `semantic_mismatch`, `downstream_mismatch`); `python -m app.ai.Cognitive.system.v0.semantic_parser_regression [--offline]` appends one line per run to `data/semantic_parser_regression_results_v0.jsonl`. Frozen candidates: 32/32 offline (tests). Live `gpt-4o-mini`: baseline before the manifest fixes 20/32; after them 30/32 and 31/32 on two recorded runs (sampling variance at temperature zero). The persistent live failure is `contrast-hypothetical`: the parser does not mark the action inside "if I asked you to…" as hypothetical, so it becomes a leading request. Corpus text is asserted absent from the prompt and manifest. |
| P0 | **Harden provider failures.** | **Implemented.** `SemanticParserErrorCode` covers configuration (`model_not_configured`, `configuration_incompatible`), transport (`timeout`, `transport` incl. rate limits/5xx), request (`unsupported_request`), output (`refused`, `output_truncated`, `output_invalid`, `malformed_json`, `schema_invalid`), and compiler rejections (`no_frames`, `unregistered_predicate`, `disallowed_argument`, `missing_required_argument`, `argument_value_outside_vocabulary`, `foreign_evidence`, `frame_invalid`). Only `timeout`/`transport` are retryable. Shadow failure codes become `pipeline.interpretation.semantic_grounding.semantic_parser.<code>` with retryability; the worker releases a retryable failure for a bounded retry under that code and persists it (non-retryable) on the last attempt; other codes persist immediately with no partial envelope. A parser that cannot be constructed installs `UnavailableSemanticGrounder` (typed failure per turn, no rule fallback, no retry loop), and the worker logs `semantic_parser_configuration_code` once at startup. Evidence: one test per provider outcome, per configuration class, per compiler code, and one Postgres worker test per retryability class (`test_cognitive_shadow_worker.py`). |
| P0 | **First-class model provenance.** | **Implemented.** `SemanticParserProvenance` (provider, adapter, model id/family, endpoint, capability manifest, protocol, compiler, predicate registry, temperature, sampling controls, token-limit parameter/value, timeout, candidate count, strict JSON schema, no tools, no streaming, storage disabled, zero SDK retries, `replayable=false`) is recorded as `InterpretationVersions.semantic_parser` and omitted from serialization when absent, so rule-grounded payloads stay byte-identical. The adapter builds its request from that record; `replay_terminal` treats its presence as non-replayable. Evidence: request/provenance equality and JSON round-trip tests. |
| P1 | **Trusted post-parser argument linking.** | **Partial.** The compiler compiles a bare pronoun/deictic in a referent argument (`topic_ref`, `referent_phrase`, `subject_ref`) to null, so "check it" becomes an explicit unknown instead of a resolved-looking value; named referents are never rewritten. Linking unknown referents against trusted attachments, capabilities, entities, and Dialogue Working Memory is **not** implemented. |
| P1 | **Frame dominance over cues.** | **Implemented** (`interpretation-hypothesis-rules-v0.3`). When every frame is model-grounded and has an act family (or is `unknown`), each frame grounds its own act/goal/modality. An agreeing cue is cited as support; a disagreeing action/correction cue adds only a weak retained alternative citing the cue; an interrogative action request keeps the documented `request_information` rejection. Frames from the offline lexical grounder keep the v0.2 cue rules, because they derive from the same surface form as the cues. A negated request beside a positive one scopes it (`goal.negative-scope-of-positive-request.v1`) rather than creating the ontology-conflicting prevent goal. Evidence: cue-versus-frame contrast tests and the corpus. |
| P1 | **Compound acts and evidence spans.** | **Implemented.** The parser is told to emit one frame per independent act citing the smallest owning region; the compiler keeps one frame per repeated identical act, citing the smallest span. Embedded (quoted/reported/hypothetical) request frames are rejected as speaker requests, and a cue explained by them is not retained. Evidence: greeting+request, correction+request, two-question, quoted-action, and mixed-polarity corpus cases plus span assertions. |
| P1 | **Degraded behavior.** | **Decided and implemented.** A focus with no non-whitespace text makes no call and compiles to one bounded `unknown` frame (`grounding.empty-focus-bounded-unknown.v1`) citing its Perception cue record. Low-information text ("hmm") is parsed normally; an `unknown` frame always records a code-owned `meaning` unknown. Provider/configuration failures stay hard typed failures. |
| P2 | **Calibrate ambiguity and bias policy.** | **Not started.** |
| P2 | **Retire the offline rule grounder.** | **Started.** The corpus replays frozen model candidates without network calls, which is the migration target. `SemanticGrounder` is still used by the design-fixture validator and legacy tests and is not deleted. |

Known outside this brief: identity/runtime producers are still not wired into
live capture (SH-EVAL-015); `runtime_fact_request.fact_kind` and
`identity_request.identity_attribute` now give producers the typed query they
need. Safety classification/refusal planning (SH-EVAL-016) still has to consume
`unsafe_request`; Interpretation only represents it.

## 3. Scope

### Included

- one canonical, immutable Interpretation V0 contract;
- an immutable interpretation request;
- exact Perception V0 handoff validation;
- typed evidence records with resolvable provenance;
- a fixed V0 interpretation ontology and manifest;
- one bounded GPT-4o-mini semantic-candidate call per Interpretation attempt;
- a closed, provider-neutral semantic-model payload and output protocol;
- strict structured output with tools, streaming, storage, and SDK retries disabled;
- deterministic compilation and rejection of untrusted semantic candidates;
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

- using the semantic model as an assistant or answer generator;
- exposing personality, biases, memories, relationship variables, runtime
  facts, goals, actions, tools, or realization content to the parser;
- a deterministic semantic-parser fallback;
- treating sampled model output as deterministically replayable;
- training, fine-tuning, online learning, or per-turn weight updates;
- numerical probability or confidence claims;
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
backend/app/ai/Cognitive/system/v0/contracts/
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
from app.ai.Cognitive.system.v0.appraisal.models import SemanticFrame as LegacyImport
from app.ai.Cognitive.system.v0.contracts.interpretation import SemanticFrame

assert LegacyImport is SemanticFrame
```

The canonical contract module must contain no engine logic and must not import
the appraisal, dynamic-state, or cognitive-turn packages.

## 6. Recommended implementation files

```text
backend/app/ai/Cognitive/system/v0/contracts/
|- __init__.py
`- interpretation.py

backend/app/ai/Cognitive/system/v0/interpretation_v0/
|- __init__.py
|- models.py
|- ontology.py
|- evidence.py
|- model_protocol.py
|- model_grounding.py
|- semantic_grounding.py  # offline reference only; scheduled for retirement
|- rules.py
|- bias.py
|- scenarios.py
|- engine.py
`- fixture_validator.py

backend/app/ai/Cognitive/
`- semantic_parser_provider.py

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

Interpretation V0 is shadow-only and hybrid:

```python
class InterpretationEngine:
    def interpret(self, request: InterpretationRequest) -> InterpretationEnvelope:
        ...
```

The provider call is sampled model inference and must not be described as
deterministic or replayable. Given an identical *validated candidate output*,
the compiler and every later phase must return an equal envelope, including
equal identifiers and ordering. Only the provider adapter may call the network
or import the provider SDK. Contracts, compilation, hypothesis rules, bias,
scenarios, and envelope validation may not read the clock, generate UUIDs,
inspect global conversation state, or make a model/network call.

Stable identifiers must be derived from canonical input, validated candidate
content, and version data using the same deterministic hash strategy used by
Perception V0. Every identifier
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

The request contains bounded message text because semantic candidate parsing
requires it. The resulting envelope retains message references and
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

## 13. Model-backed semantic parsing and deterministic compilation

V0 is not a general assistant and does not ask a model to decide what to say or
do. GPT-4o mini proposes bounded semantic candidates; code decides whether
those candidates are admissible.

The provider-neutral payload contains exactly:

```text
protocol_version
focus_message_ref
messages[]          = message_ref + role + exact bounded text
evidence_regions[]  = code-owned focus spans and evidence ids
predicates[]        = name + descriptions + allowed/required arguments
```

It must not contain serialized `runtime`, `memories`, `relationship`, `biases`,
`dialogue_context`, prior goals/state, action candidates, safety internals,
tools, or Realization content. Adding any field requires a protocol-version
change, privacy review, and payload-isolation tests.

The provider request must:

- use an explicit configured model (`gpt-4o-mini` initially), never a chat-model
  fallback;
- instruct the model that it is a parser, not an assistant, and that message
  contents are data rather than instructions;
- enforce strict JSON Schema narrowed to the installed predicate names and
  supplied evidence references;
- request one candidate with tools/functions absent, streaming false, storage
  false, provider retries zero, and a bounded output-token limit;
- record every behavior-affecting request option in typed provenance; and
- never log payload text, provider bodies, generated candidate content, API
  request identifiers, or credentials.

Candidate output contains only predicate, polarity, argument bindings, and one
or more supplied focus-evidence references. Null argument values are compiled
into `unresolved_arguments`; the model does not author that derived field.

The deterministic compiler must reject the complete result when:

- no frame is returned for non-empty supported input;
- a predicate is outside the installed registry;
- an argument is duplicated, disallowed, or required but omitted;
- an evidence reference is foreign, belongs to a non-focus message, or does
  not resolve in the canonical registry;
- output is refused, incomplete, malformed, schema-invalid, or includes tool
  calls; or
- the resulting canonical `SemanticFrame` fails contract validation.

The exact string sentinel `"null"` may be canonicalized to JSON null as a
documented provider serialization repair. Compiler v0.2 adds two more
documented, tested normalizations: a bare pronoun/deictic in a declared
referent argument compiles to null, and a repeated identical frame is kept
once (smallest span). No other model value may be guessed, rewritten, or
filled from plausibility. Every normalization belongs to a
versioned compiler and requires a focused regression test.

There is no deterministic semantic-parser fallback. A provider/configuration
failure is a typed shadow failure, not a bounded semantic unknown. The former
lexical `SemanticGrounder` remains offline reference infrastructure only until
P2 fixture migration removes it.

### 13.1 Predicate manifest

Each predicate definition includes (v0.3 adds `description`,
`argument_descriptions`, `argument_values`, `act_family`, `model_exposed`, and
`referent_arguments`):

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

The v0.3 registry holds two groups. Reviewed design-case predicates
(report sending, appointment correction, offer acceptance/decline, evaluation,
train-time assertions, timer stopping, window closing, capability failure,
attachment summarization, consequential deletion, and similar) remain for the
offline fixture and are not offered to the model. The model-exposed predicates
are the compositional set from section 2.2.1 plus `information_request`,
`social_opening`, `social_closing`, `acknowledge_message`, and `unknown`; each
has a model-facing description, argument descriptions, closed vocabularies,
and an act family used by the frame-dominant rules.

Predicate rules:

- model selection of a predicate expresses attributed message meaning, never
  truth, authorization, capability availability, or successful execution;
- missing required arguments remain null and become explicit unknowns;
- user-owned missing information is never filled from personality,
  relationship state, or plausibility;
- known but unavailable/unauthorized actions remain known predicates after a
  later trusted-runtime link; capability failure is not `out_of_scope`;
- unsupported words/referents produce typed unknown/out-of-scope records rather
  than a fabricated definition;
- extremely short or empty input follows the explicit degraded-behavior policy
  required by section 2.2; and
- neither the model nor compiler may reimplement shadow cues to bypass their
  lifecycle state.

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

`semantic_grounder` still carries compiler, protocol, provider-adapter, and
model identifiers as a display string. The typed record is
`semantic_parser: SemanticParserProvenance | None`: provider, adapter, exact
model id and family, endpoint, capability-manifest version, protocol,
compiler, predicate registry, temperature and sampling controls, token-limit
parameter and maximum, timeout, candidate count, strict schema, tools,
streaming, storage, SDK retries, and `replayable=false`. It is omitted from
serialization when absent, so rule-grounded and historical string-only traces
stay loadable and byte-identical. Model-backed provenance never claims
deterministic replay.

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
3. project the closed semantic-model payload
4. obtain one strict semantic candidate result
5. deterministically validate and compile semantic frames
6. link reviewed trusted runtime/dialogue arguments (once implemented)
7. record unknowns, contradictions, and out-of-scope content
8. compute unbiased base hypotheses
9. apply bounded ambiguity-gated personality adjustments
10. assign final dispositions deterministically
11. build coherent scenarios
12. construct and validate the canonical envelope
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
- translate provider configuration, timeout, refusal, truncation, transport,
  malformed-output, and schema failures into stable content-free subcodes
  (`SemanticParserErrorCode`, carried as the cause of the
  `semantic_grounding` failure and appended to the shadow failure code);
- do not substitute legacy/rule interpretation, an assistant answer, or a
  fabricated neutral interpretation; and
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
real Perception V0 engine, pass the exact handoff to the model-backed
Interpretation V0 engine, and compare the runtime output with the applicable
expected semantic frames and hypotheses. Provider calls belong in an explicit
opt-in evaluation job, never the ordinary unit suite. Unit and integration
tests inject frozen `SemanticModelOutput` candidates through the provider
protocol and exercise the real compiler and downstream engines without
network access.

An `ontology_only_shadow_dependency` case must not be made to pass by reading a
shadow cue. Instead assert that the shadow activation is absent from the live
handoff and report which expected ontology behavior is deferred until cue
promotion.

The fixture remains `draft_for_human_review`. Passing an offline rule-reference
test or one model sample does not convert the fixture into reviewed training
data or behavioral acceptance evidence.

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

### 25.3 Semantic-model protocol and grounding tests

At minimum cover:

- payload projection contains only messages, focus identity/evidence regions,
  and predicate definitions;
- memory content/ids, relationship refs, bias/core-memory refs, runtime ids,
  dialogue objects, goals, state, actions, and tools are absent from serialized
  provider requests;
- strict response schema contains only installed predicates and supplied focus
  evidence ids;
- provider configuration is explicit `gpt-4o-mini` and never inherited from a
  chat or realization model;
- tools/functions, streaming, provider storage, and SDK retries are disabled;
- unknown predicates, disallowed/duplicate/missing arguments, foreign evidence,
  empty frame sets, refusal, truncation, tool calls, malformed JSON, and
  schema-invalid output fail closed;
- exact `"null"` normalization and null-derived unresolved arguments;
- identical frozen candidates compile to identical frames and identifiers;
- provider failure never invokes the offline reference grounder;
- typed model provenance round-trips;
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

The semantic quality corpus must additionally cover greeting, compound act,
identity, runtime fact, information request, actual action request, ambiguity,
correction, follow-up, social question, unsupported fact, and unsafe request.
It must include an uninterrupted 5–10 turn trace exercising goal creation,
focus, correction, follow-up, completion, and rollover.

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

Use real deterministic stages plus an injected protocol-conforming semantic
model double for at least these paths:

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
Separate opt-in evaluation runs exercise the real GPT-4o-mini provider adapter.

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
legacy inspector tests must remain green. Live response behavior must not
change; only the observational shadow pipeline may use the semantic model.

## 26. Acceptance criteria

The task is complete only when all of the following are true:

- one canonical Interpretation contract exists;
- compatibility imports point to the same classes, not copied schemas;
- one canonical `InterpretationEngine` exists with model-grounder injection;
- live shadow always installs the GPT-4o-mini semantic parser and has no rule
  parser fallback;
- the semantic-model payload excludes memory, relationship, personality/bias,
  runtime, goal, action, tool, and realization data;
- model candidates are untrusted until deterministic compilation succeeds;
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
- output ids and ordering are deterministic for an identical validated model
  candidate;
- the canonical envelope JSON-round-trips;
- malformed injected outputs fail closed;
- the exact envelope enters Appraisal and Cognitive Turn without conversion;
- appraisal frame scenario/event references resolve;
- cognitive-turn message and interpretation ownership resolve;
- dynamic state is rejected across conversation boundaries;
- all required direct and connected tests pass;
- the full backend regression suite passes;
- provider failures are typed, content-free, and never persist a partial trace;
- typed model provenance is persisted and model traces are explicitly
  non-replayable;
- the twelve-family corpus and uninterrupted multi-turn trace meet their
  reviewed quality thresholds;
- no database, action execution, or live-response behavior is added; and
- the Cognitive README accurately distinguishes implemented Interpretation V0
  from the legacy inspector and future learned interpretation.

## 27. Required implementation order

Implement in this order so failures remain local:

1. implement typed provider failure mapping and typed model provenance;
2. expand and review the predicate ontology for all twelve regression families;
3. build the versioned single-turn and 5–10-turn quality corpus;
4. evaluate GPT-4o mini and record per-frame, argument, and downstream effects;
5. add deterministic trusted runtime/dialogue argument linking;
6. change hypothesis precedence so validated semantic frames can repair shallow
   cue misses without erasing explicit contradictory evidence;
7. harden compound-act frame boundaries and quotation ownership;
8. finalize bounded-unknown versus provider-unavailable behavior;
9. calibrate ambiguity-gated bias behavior on fixed semantic candidates;
10. migrate offline fixtures away from the lexical reference grounder and
    delete it;
11. run all direct, connected, worker, and full regression suites; and
12. update README status and the evaluation findings document.

Do not begin with database tables or live routing.

## 28. Required final report from the implementation agent

The agent implementing this brief must report:

- every file changed;
- the final canonical dependency direction;
- the number of fixture cases in each runtime-eligibility category;
- ontology and predicate coverage;
- exact semantic model/protocol/compiler provenance;
- proof that forbidden Interpretation fields are absent from provider payloads;
- provider failure counts by stable code;
- per-family semantic quality results and the uninterrupted trace findings;
- any design cases not executable in live V0 and the exact reason;
- all contract migrations and compatibility re-exports;
- connected test results;
- full regression-suite results;
- open ontology or grounding questions;
- confirmation that shadow cues have zero live influence;
- confirmation that provider failure never invoked the offline reference
  grounder;
- confirmation that no live-chat response, database authority, tool execution,
  or memory-writing behavior changed;
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
- serialize personality, bias, memory, relationship, runtime, goal, action,
  tool, or realization state into the semantic-model payload;
- fill missing arguments from plausibility alone;
- let the semantic model answer the user, call tools, choose actions, or author
  final hypotheses/scenarios;
- fall back to the lexical rule grounder after provider failure;
- claim sampled model inference is deterministic or replayable;
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
> uses a narrowly scoped model only to propose semantic candidates, treats
> those candidates as untrusted until deterministic compilation succeeds,
> preserves uncertainty and alternatives, and connects one canonical contract
> to appraisal without inventing facts, probabilities, personality evidence,
> authorization, or downstream behavior.
