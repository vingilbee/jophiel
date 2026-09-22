# Perception V0 implementation brief

Status: ready for implementation review  
Scope: deterministic, evidence-linked perception for the new cognitive system  
Depends on: accepted perception ontology, reviewed-shaped design cases, and the
V0 cognitive-turn identity contract  
Non-goal: this task does not implement interpretation, emotion recognition, a
learned encoder, action selection, or reflection

## 1. Objective

Implement the new Perception V0 runtime described by the Cognitive system
design:

```text
current message
+ bounded role-labelled history
+ trusted runtime facts
+ bounded working-context references
             |
             v
deterministic observation and cue detectors
             |
             v
evidence and ontology validation
             |
             v
canonical PerceptionEvent V0
             |
             v
future deterministic Interpretation V0
```

Perception answers:

> What observable signals are present in the supplied evidence?

Perception must not answer:

> What does the user ultimately mean, feel, intend, or want Columbina to do?

The implementation must produce a versioned, immutable, auditable perception
event belonging to one `cognitive_turn_id`. Every present cue must retain exact
evidence. Missing or ambiguous evidence must remain unknown rather than becoming
false, neutral, or a guessed interpretation.

## 2. Architectural boundary

The defining boundary is:

```text
perception:     detectable wording, structure, repetition, and runtime facts
interpretation: possible meaning, intent, pragmatic force, and user state
```

Examples of valid perception outputs:

- an explicit question form is present;
- the speaker explicitly corrected a previous statement;
- a scoped negation marker appears;
- the request has appeared once previously in the bounded context;
- trusted runtime context contains an attachment;
- a trusted authorized tool event reports failure;
- positive and negative wording coexist.

Examples perception must not output as facts:

- the user is annoyed, angry, hostile, deceptive, or sarcastic;
- the user distrusts Columbina;
- the user's real intent is to insult, joke, or manipulate;
- a task is missing a required argument before that task is interpreted;
- a requested capability is unavailable before the requested action is
  interpreted and matched to the capability registry;
- an external action is authorized;
- a response should be sent, clarified, delegated, or refused.

These are downstream interpretation, goal, action-eligibility, authorization,
or routing questions.

## 3. Legacy diagnostic pipeline boundary

The repository already contains:

```text
system/perception.py
system/interpretation.py
system/schemas.py
system/pipeline.py
```

Those files implement the older Cognitive Inspector experiment. Their outputs
are flat `Perception`, `Interpretation`, and `CognitiveInput` objects. They are
not the Perception V0 and Interpretation V0 contracts defined by the current
cognitive-system architecture.

### Do not delete them in this task

The authenticated `/api/cognitive/inspect` endpoint, the Cognitive Test UI, and
existing regression tests still depend on the legacy pipeline. Deleting or
renaming those files now would mix a new cognitive-runtime implementation with
an unrelated user-facing migration.

### Do not build V0 on their output schemas

The new engine must not return, wrap, subclass, or treat these as canonical:

```text
legacy Perception.is_question
legacy Perception.is_request
legacy Perception.novelty
legacy Perception.ambiguity
legacy Interpretation.intent
legacy Interpretation.confidence
```

Compatible deterministic logic may be extracted or reimplemented behind the
new cue definitions, but the new runtime must emit only the new canonical
contracts with evidence, status, provenance, lifecycle, and version fields.

### Planned retirement sequence

```text
1. Implement and test Perception V0 separately.
2. Implement and test Interpretation V0 separately.
3. Connect message -> Perception V0 -> Interpretation V0 -> CognitiveTurnRunner.
4. Add a new inspector view or migrate the existing inspector deliberately.
5. Confirm no API, UI, test, or training code imports the legacy contracts.
6. Remove the legacy pipeline in a separate cleanup change.
```

Until step 5, label the existing pipeline as legacy/diagnostic in documentation
and avoid adding new cognitive behavior to it.

## 4. Source design and fixture

The implementation must follow:

- the perception architecture, ontology, learning boundary, and decision ledger
  in `system/README.md`;
- `data/perception_design_cases.json`;
- the perception/interpretation and evidence boundaries in the connected trace
  design;
- the cognitive-turn identity and provenance rules.

Current fixture facts:

```text
schema_version: 1
ontology_version: 1
status: draft_for_human_review
case count: 20
cue count: 13
```

The fixture is an executable design contract, not approved training data. Its
status must remain `draft_for_human_review` until a human explicitly changes it.
The production runtime must never load it. Only tests and the fixture validator
may read it.

If an expected case cannot be implemented without violating an ontology
boundary, do not weaken the boundary or silently edit the case. Report the
conflict as a design question.

## 5. V0 scope

### Included

- canonical immutable input and output contracts;
- a static versioned cue manifest containing all thirteen initial cues;
- deterministic detectors for the approved V0-active cues;
- deterministic shadow detectors needed to execute the reviewed-shaped fixture;
- explicit lifecycle filtering so shadow cues have zero interpretation effect;
- exact text-span, whole-message, message-sequence, and runtime-fact evidence;
- bounded context handling;
- trusted-runtime boundaries for attachments and tool failures;
- explicit present, absent, unknown, and not-applicable states;
- deterministic coverage uncertainty with an explicit V0 policy version;
- fixture validation and focused unit tests;
- serialization round-trip and determinism tests;
- a clean future handoff to Interpretation V0.

### Excluded

- generative LLM calls;
- neural embeddings or learned cue heads;
- BGE, MiniLM, SetFit, ONNX, or model downloads;
- trained confidence calibration;
- numerical personality-driven salience;
- raw core-personality input;
- inferred user emotion or mental state;
- sarcasm, hostility, deception, or playfulness detection;
- semantic intent classification;
- task-constraint or capability-eligibility inference;
- action selection, tool execution, or authorization decisions;
- online learning;
- training-dataset generation;
- production database tables or migrations;
- live-chat behavior changes;
- legacy inspector removal or migration.

## 6. Suggested files

Use a namespace that cannot be confused with `system/perception.py`:

```text
backend/app/ai/Cognitive/system/perception_v0/
|- __init__.py
|- models.py
|- manifest.py
|- engine.py
|- detectors.py
|- evidence.py
`- fixture_validator.py

backend/tests/
|- test_perception_v0.py
`- test_perception_v0_contract.py
```

Suggested dependency direction:

```text
perception_v0.models <--- manifest
        ^                  |
        |                  v
     evidence <-------- detectors
        ^                  |
        +------ engine <---+
```

The fixture validator may import the production engine. The production engine
must not import the fixture validator or fixture JSON.

## 7. Input contracts

### 7.1 Role-labelled message

Define an immutable input message:

```python
class PerceptionMessage(FrozenModel):
    message_id: str
    role: MessageRole
    content: str
```

At minimum, roles must distinguish `user`, `assistant`, `system`, and `tool`.
The focused message will normally be a user message, but the contract should not
infer ownership from list position.

Message ids must be non-empty and unique within a request. Message order is the
tuple order and must be preserved.

### 7.2 Runtime snapshot

Define an immutable, versioned runtime snapshot containing only trusted facts
that perception is allowed to observe. It should support the existing design
fixture and future adapters without accepting arbitrary unproven dictionaries.

Logical minimum:

```python
class PerceptionRuntimeContext(FrozenModel):
    snapshot_id: str
    schema_version: int = 1
    attachments: tuple[AttachmentFact, ...] = ()
    available_capabilities: tuple[str, ...] = ()
    last_tool_event: ToolEventFact | None = None
    conversation_mode: str | None = None
```

`available_capabilities` is retained as runtime context for later stages. It
must not produce `runtime.capability_unavailable` in perception because the
requested target has not yet been interpreted.

An attachment fact must have a stable reference and trusted runtime provenance.
A tool-event fact must distinguish at least:

- tool or invocation reference;
- status;
- whether the event came from the trusted tool runtime;
- whether the invocation was authorized;
- evidence/event reference.

Only a recognized trusted and authorized failure may activate
`runtime.tool_failure`.

### 7.3 Bounded working context

Perception may receive a small immutable working-context summary containing
references such as the current topic and unresolved conversational objects. It
must not receive raw core personality or autobiographical memory as perceptual
evidence.

```python
class PerceptionWorkingContext(FrozenModel):
    active_topic_ref: str | None = None
    unresolved_refs: tuple[str, ...] = ()
    source_working_memory_ref: str | None = None
```

Topic-shift detection is shadow-only. If the supplied context cannot support a
topic comparison, its result is `unknown`, not `false`.

### 7.4 Request

Recommended logical shape:

```python
class PerceptionRequest(FrozenModel):
    schema_version: int = 1
    cognitive_turn_id: str
    messages: tuple[PerceptionMessage, ...]
    focus_message_id: str
    runtime: PerceptionRuntimeContext
    working_context: PerceptionWorkingContext | None = None
    language_tag: str | None = "en"
    cue_manifest_version: str = "cue-manifest-v1"
```

Required validations:

1. cognitive-turn, message, runtime-snapshot, and manifest ids are non-empty;
2. message ids are unique;
3. `focus_message_id` resolves exactly once;
4. context messages precede or equal the focus position;
5. future messages are rejected rather than used as evidence;
6. input is bounded to at most 30 ordered messages in V0;
7. callers should supply the bounded window; if an adapter truncates a larger
   history, it keeps the newest 30 ending at the focus message and records that
   truncation as an observation;
8. unsupported languages make language-dependent lexical cues `unknown` while
   trusted runtime cues remain evaluable;
9. raw personality metrics, retrieved autobiographical memories, action plans,
   and untrusted user claims are not accepted as runtime facts.

The engine must never inspect messages that occur after the focus message. This
prevents label leakage and preserves temporal reproducibility.

## 8. Cue manifest contract

The manifest is static, code-owned, immutable, and versioned in V0. It is not a
database table.

Recommended definition:

```python
class CueDefinition(FrozenModel):
    cue_key: str
    definition_version: int
    name: str
    description: str
    category: CueCategory
    value_type: CueValueType
    lifecycle_status: CueLifecycleStatus
    detector_type: DetectorType
    detector_reference: str
    evidence_requirement: EvidenceRequirement
    value_constraints: Mapping[str, object]
    parent_cue_key: str | None = None
    conflicts_with: tuple[str, ...] = ()
    base_importance: float | None = None
    origin: str = "human"
    downstream_consumers: tuple[str, ...] = ()
```

The manifest validator must reject:

- duplicate cue keys;
- unsupported value types;
- missing or non-positive definition versions;
- active cues without a deterministic/runtime detector reference;
- detector references without implementations;
- invalid parent or conflict references;
- lifecycle changes that implicitly change a definition;
- active cues with no evidence requirement;
- unknown lifecycle values.

Changing a cue's operational meaning, range, or evidence rule requires a new
definition version and eventually a new manifest version.

## 9. Initial cue ontology

The V0 manifest contains exactly these thirteen definitions.

### V0-active cues

These may be passed to Interpretation V0 after validation:

| Cue | Type | Operational meaning |
| --- | --- | --- |
| `conversation.question_form` | Boolean | Explicit grammatical or punctuated question in the focus message |
| `conversation.explicit_action_request` | Boolean | Speaker directly asks Columbina to perform an action; factual questions are excluded |
| `conversation.explicit_correction` | Boolean | Speaker explicitly corrects a prior claim, understanding, or action |
| `conversation.repetition` | Integer | Count of prior materially matching occurrences in the bounded context; first occurrence is zero |
| `linguistic.negation` | Boolean | Scoped negation marker is present in current-speaker text |
| `linguistic.uncertainty_marker` | Boolean | Explicit uncertainty wording is present in current-speaker text |
| `linguistic.self_reference` | Integer | Count of first-person references owned by the current speaker, excluding quoted speech owned by another speaker |
| `runtime.attachment_present` | Boolean | Trusted runtime reports one or more attachments |
| `runtime.tool_failure` | Boolean | Trusted authorized tool runtime reports a recognized failure |

### Shadow cues

These may be emitted and evaluated but must have zero effect on live
interpretation:

| Cue | Type | Operational meaning |
| --- | --- | --- |
| `conversation.topic_shift` | Boolean | Current subject materially differs from the supplied active/recent topic |
| `linguistic.positive_wording` | Boolean | Positive evaluation/affect wording marker is present, without inferred emotion or intent |
| `linguistic.negative_wording` | Boolean | Negative evaluation/affect wording marker is present, without inferred emotion or intent |
| `task.urgency` | Boolean | Explicit time pressure or immediate deadline wording is present; emotional intensity alone is insufficient |

The engine may expose all thirteen results for inspection and fixture
validation. A separate, tested interpretation-handoff view must include only
`v0_active` cues. Filtering must use manifest lifecycle status, not a duplicated
hard-coded list.

## 10. Evidence contracts

Use a discriminated union. Evidence must be resolvable against the exact request
that produced the event.

### Text-span evidence

```python
class TextSpanEvidence(FrozenModel):
    evidence_type: Literal["text_span"]
    message_id: str
    start: int
    end: int
    quoted_text: str
```

Validation requirements:

- zero-based Python character offsets;
- exclusive `end`;
- `0 <= start < end <= len(content)`;
- `quoted_text == content[start:end]`;
- the message exists in the request;
- the referenced text is available no later than the focus message.

### Whole-message evidence

Use when an honest smaller span cannot represent the cue:

```python
class WholeMessageEvidence(FrozenModel):
    evidence_type: Literal["whole_message"]
    message_id: str
```

Do not fabricate precise spans for context-level or structural detections.

### Runtime-fact evidence

```python
class RuntimeFactEvidence(FrozenModel):
    evidence_type: Literal["runtime_fact"]
    runtime_snapshot_id: str
    json_pointer: str
    value: JsonValue
```

Validation requirements:

- JSON Pointer syntax follows RFC 6901;
- the pointer resolves against the exact runtime snapshot;
- the recorded value equals the resolved value;
- text claims such as "I attached it" cannot produce runtime evidence;
- hypothetical or quoted tool failures cannot produce runtime evidence.

### Evidence invariants

- Every `present` cue requires sufficient evidence under its manifest
  definition.
- `absent` normally has no positive span evidence, but it still records the
  detector/version and evaluated scope.
- `unknown` records missing information or evidence limitations.
- Model attention weights are never evidence.
- Evidence belongs to observations and activations; it is never reconstructed
  later from generated rationale text.

## 11. Observation contract

Observations are direct properties or runtime events, not cue meanings.

Recommended logical shape:

```python
class PerceptionObservation(FrozenModel):
    observation_id: str
    observation_key: str
    value_type: CueValueType
    value: JsonValue
    evidence: tuple[PerceptionEvidence, ...]
    source: str
    source_version: str
```

Examples:

- focus message has a terminal question mark;
- token span contains `not`;
- runtime attachment count is 1;
- last trusted tool event has status `failed`;
- one prior message matches the normalized request signature.

Observations may support several cues. They must not be named `user_angry`,
`sarcastic`, `hostile`, or other interpretation conclusions.

Observation ids must be deterministic within a fixed request and detector
version so tests and later learning can reproduce provenance.

## 12. Cue-state and activation contract

Define states:

```python
class CueState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
```

Recommended activation shape:

```python
class CueActivation(FrozenModel):
    cue_key: str
    definition_version: int
    value_type: CueValueType
    state: CueState
    value: bool | int | float | str | Mapping[str, float] | None

    raw_score: float | None = None
    confidence: float | None = None
    salience: float | None = None

    evidence: tuple[PerceptionEvidence, ...] = ()
    observation_refs: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()

    detector_source: DetectorSource
    detector_reference: str
    lifecycle_status: CueLifecycleStatus
```

Required invariants:

- manifest definition, value type, version, detector, and lifecycle match;
- Boolean present/absent values are `true`/`false` respectively;
- integer present values are positive; integer absent values are zero;
- unknown/not-applicable values are `None`;
- present activations have evidence;
- unknown activations have at least one missing-information reason;
- `raw_score` is `None` for deterministic V0 detectors;
- lexical rule outputs do not claim calibrated numerical confidence;
- trusted runtime facts may use confidence `1.0` only to mean exact runtime
  observation, not semantic certainty;
- `salience` remains `None` until an approved AttentionPolicy exists;
- lifecycle status comes from the manifest and cannot be changed per request.

V0 design cases do not supply numerical confidence or salience. Do not invent
those targets to make the runtime look more complete.

## 13. Perception event contract

Recommended stable envelope:

```python
class PerceptionVersions(FrozenModel):
    schema: str
    cue_manifest: str
    deterministic_detectors: str
    evidence_validator: str
    uncertainty_policy: str
    attention_policy: str


class PerceptionEvent(FrozenModel):
    schema_version: int = 1
    perception_event_id: str
    cognitive_turn_id: str
    input_message_ids: tuple[str, ...]
    focus_message_id: str

    observations: tuple[PerceptionObservation, ...]
    cue_activations: tuple[CueActivation, ...]
    attention: tuple[PerceptionAttention, ...] = ()

    overall_uncertainty: float
    missing_information: tuple[str, ...]
    context_truncated: bool = False
    versions: PerceptionVersions
```

Event invariants:

1. the event and request share the same cognitive turn, messages, and focus id;
2. exactly one activation exists for each of the thirteen manifest cues;
3. cue keys are unique and match the manifest version;
4. every observation reference resolves;
5. every evidence reference validates against the input snapshot;
6. shadow cues remain identifiable as shadow;
7. attention is empty and activation salience is `None` in V0 unless a separate
   reviewed attention policy is implemented;
8. versions are explicit and non-empty;
9. the event is deeply immutable and JSON round-trippable.

### V0 uncertainty policy

The design does not yet approve learned confidence, calibration, or
out-of-distribution scoring. V0 therefore reports only **coverage uncertainty**:

```text
overall_uncertainty =
    count(UNKNOWN among applicable v0_active cues)
    / count(applicable v0_active cues)
```

Rules:

- exclude shadow cues from the calculation;
- exclude `not_applicable` cues from the denominator;
- if no V0-active cue is applicable, report `1.0` and a missing-information
  reason;
- round deterministically and record an explicit policy version such as
  `perception-coverage-uncertainty-v0.1`;
- do not describe this number as probability, model confidence, emotional
  uncertainty, or interpretation uncertainty;
- Interpretation V0 must calculate its own hypothesis uncertainty later.

If the project does not accept this coverage-only definition, keep the field
structurally present but pause implementation for a design decision rather than
inventing another formula.

## 14. Detector requirements

Each detector must be pure, deterministic, separately testable, and explicitly
versioned. It returns observations and one cue result; it does not modify the
request or other detector outputs.

### 14.1 Question form

Detect explicit grammatical or punctuated questions in the focus message.

Requirements:

- factual questions are present;
- action requests phrased as questions are also question-form present;
- question form alone does not establish a request for action;
- quoted or hypothetical questions are still structural question forms when
  the focus message itself asks a question, but quotation ownership must remain
  visible in evidence;
- empty input is absent or not-applicable according to the manifest definition,
  not automatically ambiguous intent.

### 14.2 Explicit action request

Detect direct requests for Columbina to perform an action.

Requirements:

- imperatives and `can/could/would you <action>` patterns may qualify;
- informal forms such as reviewed `pls` examples are supported;
- factual questions such as opening hours are excluded;
- the detector identifies request structure only;
- it does not resolve missing recipients, objects, authorization, capability, or
  whether the action should be performed;
- unknown action predicates may still be explicit requests; interpretation later
  marks their semantics out of scope.

### 14.3 Explicit correction

Requires evidence that the current speaker replaces, rejects, or repairs a
prior claim, understanding, or action.

Requirements:

- `No, I said Tuesday, not Thursday` is a correction;
- `No thanks` is not automatically a correction;
- `I don't know` is uncertainty/negation unless context supplies correction;
- the prior target message must be in the bounded context;
- if the alleged correction target is outside the context or ambiguous, emit
  unknown rather than present;
- evidence normally includes the focus span and the corrected prior-message
  reference or whole-message evidence.

### 14.4 Repetition

Return the number of prior materially matching occurrences in the bounded
context. The first occurrence is zero/absent.

V0 matching must be transparent and deterministic. It may use normalized text,
speaker role, request markers, and conservative token overlap. It must not use a
neural embedding or claim semantic equivalence it cannot inspect.

Requirements:

- count prior occurrences, not total occurrences including focus;
- preserve message ids for every counted occurrence and the focus;
- distinguish repetition from a nearby assistant paraphrase;
- use only messages before the focus;
- if normalization cannot determine whether two materially different phrasings
  repeat the same act, return unknown rather than guessing;
- record the matching-policy version.

### 14.5 Negation

Detect scoped negation markers in current-speaker text.

Requirements:

- support common explicit forms such as `not`, `never`, `no`, `didn't`, and
  equivalent reviewed contractions;
- do not treat every substring containing `no` as negation;
- retain the marker span and, when deterministically available, its local scope;
- exclude quoted speech owned by another speaker from current-speaker negation;
- unresolved quote ownership produces unknown when it changes the result;
- negation is a cue, not an intent or sentiment label.

### 14.6 Uncertainty marker

Detect explicit uncertainty wording such as `maybe`, `I think`, `not sure`, or
equivalent reviewed English markers.

Requirements:

- marker presence concerns the speaker's expressed certainty;
- it does not set `appraisal.certainty` directly;
- quoted uncertainty owned by another speaker is excluded;
- a confident statement without a marker is absent, not proof that the claim is
  objectively certain.

### 14.7 Self-reference

Count first-person references owned by the focus-message speaker.

Requirements:

- count reviewed English pronouns and contractions consistently;
- do not count quoted first-person speech owned by another speaker;
- ambiguous quotation ownership produces unknown if it affects the count;
- record spans for counted references;
- this cue does not infer selfishness, emotion, preference, or identity.

### 14.8 Attachment present

Use only the trusted runtime snapshot.

Requirements:

- one or more runtime attachments means present/true;
- zero trusted attachments means absent/false;
- text saying `I attached it` cannot change the runtime cue;
- evidence uses runtime snapshot id and JSON Pointer;
- malformed or unavailable runtime attachment data produces unknown, not false.

### 14.9 Tool failure

Use only a recognized trusted authorized tool event.

Requirements:

- a trusted authorized failure event means present;
- a trusted success/non-failure event means absent;
- no tool event means `not_applicable` because no invocation is being observed;
- user descriptions, hypothetical language, and quoted failures do not activate
  the cue;
- untrusted, unauthorized, or malformed events produce unknown;
- evidence points to the exact runtime event field.

### 14.10 Shadow topic shift

Compare the focus subject conservatively against supplied recent/working topic
context. Without adequate topic context, emit unknown.

It must never influence Interpretation V0 while shadow. Avoid pretending that a
lexical-overlap heuristic is a fully reliable semantic topic model.

### 14.11 Shadow positive and negative wording

Detect lexical evaluation/affect markers only. Preserve positive and negative
as separate simultaneous cues.

Requirements:

- mixed wording may activate both;
- marker presence does not establish sentiment, sarcasm, emotion, or pragmatic
  meaning;
- quoted markers owned by another speaker are excluded or marked unknown when
  ownership matters;
- lexicons and rules are versioned;
- shadow status prevents interpretation influence.

### 14.12 Shadow urgency

Detect explicit time pressure, deadlines, or immediate handling language.

Requirements:

- an explicit deadline can activate the cue;
- capitalization, profanity, or emotional intensity alone cannot;
- urgency remains distinct from importance and activation state;
- shadow status prevents interpretation influence.

## 15. Quotation and ownership handling

Several ontology boundaries depend on whether wording belongs to the current
speaker. Implement one small deterministic quote-span utility rather than
different ad hoc quote logic in every detector.

V0 may support straight quotes, common curly quotes, and clearly introduced
quoted speech. It must expose uncertainty when ownership cannot be resolved.

The utility must not perform general coreference or infer hidden speakers. It
returns structural quote spans and conservative ownership annotations only.

Tests must include quoted self-reference, quoted failure, quoted affective
wording, and hypothetical language from the fixture.

## 16. Shadow-safety boundary

Provide a single canonical handoff method or selector, for example:

```python
event.activations_for_interpretation()
```

It must:

- select by manifest lifecycle status;
- include only `v0_active` cues;
- preserve present, absent, unknown, and not-applicable status;
- preserve evidence and versions;
- never promote a shadow cue because it has high lexical strength;
- never allow request input to override lifecycle status.

Tests must prove that topic shift, positive wording, negative wording, and
urgency are excluded from the live interpretation feature view while remaining
available in the full event for shadow evaluation.

## 17. Fixture validator

Implement a validator that runs the production V0 engine against
`data/perception_design_cases.json`.

It must:

1. validate file schema and retain `draft_for_human_review` status;
2. validate unique case and per-case message ids;
3. validate the focus message;
4. validate cue keys against the manifest;
5. validate expected state/value compatibility;
6. validate exact text spans and runtime JSON Pointers in the fixture;
7. execute the production engine;
8. compare only explicitly listed cue expectations;
9. never treat an omitted cue as expected absent;
10. compare expected state and typed value;
11. require expected present evidence to be represented by valid actual
    evidence;
12. produce a coverage summary for each cue;
13. list ontology questions and mismatches without editing the fixture;
14. verify shadow cues remain shadow in the manifest and live handoff;
15. return a typed report and non-zero CLI exit on errors.

Recommended report fields:

```python
class PerceptionFixtureReport(FrozenModel):
    fixture_path: str
    cases_run: int
    expectations_checked: int
    evidence_checks: int
    coverage_by_cue: Mapping[str, CueCoverage]
    ontology_questions: tuple[str, ...]
    errors: tuple[str, ...]
```

The fixture validator belongs to testing/review infrastructure. Production
runtime imports must not execute it.

## 18. Failure behavior

Perception must fail closed on contract corruption and remain conservative on
insufficient evidence.

Raise validation errors for:

- duplicate or unresolved message ids;
- focus message outside the request;
- future-message evidence;
- unsupported manifest version;
- duplicate or unknown cue definitions;
- invalid activation type/value combinations;
- invalid text offsets or quoted text;
- invalid runtime pointer/value pairs;
- duplicate activations;
- detector output that conflicts with its manifest definition.

Emit `unknown` rather than raise for ordinary epistemic limitations such as:

- unavailable contextual target for correction;
- unresolved quotation ownership;
- unsupported language for a lexical detector;
- missing trusted runtime fact;
- insufficient topic context;
- conservative repetition matching cannot decide.

Do not catch programming or validation failures and replace them with a neutral
event. The cognitive-turn runner should later report the failed stage
explicitly.

## 19. Determinism and performance

For a fixed request and version set, the semantic output must be identical:

- deterministic event, observation, and activation ids;
- deterministic activation order based on manifest order;
- deterministic evidence order;
- no wall-clock values inside semantic output;
- no random ids unless an injectable deterministic id factory is used;
- no filesystem artifact loading on each call;
- no network calls;
- no model loading or training during import.

V0 should remain inexpensive relative to one chat turn. Add a generous local
performance regression test after correctness tests, but do not optimize by
removing provenance or validation.

## 20. Required tests

Testing happens in the same implementation task, not after Interpretation V0.

### 20.1 Manifest tests

- exactly thirteen cue definitions exist;
- exactly nine are `v0_active` and four are `shadow`;
- cue keys, versions, types, detectors, and evidence requirements are unique and
  valid;
- active detector references resolve;
- manifest order is deterministic;
- invalid parents, conflicts, lifecycles, or detector references fail.

### 20.2 Contract tests

- cue state/value combinations validate;
- unknown is distinct from false/zero;
- present cues require evidence;
- invalid dimension/value types fail;
- text evidence validates exact offsets and quoted text;
- runtime evidence validates pointer and value;
- event cue keys are unique and complete;
- nested data is deeply immutable;
- JSON serialization round-trips.

### 20.3 Fixture execution

Run all 20 design cases and every explicitly listed expectation. Report exact
case/cue mismatches. Do not update the fixture from engine output.

### 20.4 Required minimal pairs

Test at least:

- factual question versus action-request question;
- correction versus `No thanks`;
- first request versus repeated unanswered request;
- uncertainty marker versus confident statement;
- negation versus affirmative wording;
- trusted attachment versus text-only attachment claim;
- trusted tool failure versus hypothetical/quoted failure;
- explicit urgency versus emotional intensity;
- positive and negative wording coexisting;
- capability unavailable as a post-interpretation fact, not a perception cue.

### 20.5 Trust-boundary tests

- user text cannot create runtime attachment presence;
- user text cannot create a tool failure;
- untrusted or unauthorized tool events remain unknown;
- available-capability lists do not directly create a perception conclusion;
- future messages cannot affect a result;
- raw personality cannot enter the request or alter evidence.

### 20.6 Quotation tests

- quoted first-person language is not current-speaker self-reference;
- quoted negative/positive wording does not become owned wording when ownership
  is clear;
- hypothetical tool failure does not become runtime failure;
- unresolved ownership becomes unknown when outcome-relevant.

### 20.7 Shadow isolation

- shadow cues appear in the full event when detected;
- shadow cues are absent from the interpretation handoff view;
- no caller flag can promote them;
- their numeric strength cannot bypass lifecycle filtering.

### 20.8 Determinism and immutability

- identical inputs produce identical semantic events;
- request and prior message objects remain unchanged;
- evidence ordering is stable;
- serialization produces stable JSON-compatible values.

### 20.9 Legacy isolation

- Perception V0 does not return legacy `Perception` or `CognitiveInput`;
- production V0 modules do not import the legacy `IntentInterpreter` or
  `CognitiveInputPipeline`;
- existing inspector tests continue to pass unchanged;
- no legacy API behavior changes in this task.

### 20.10 No-network/no-model test

The complete fixture suite must execute without API keys, network access, model
weights, encoder artifacts, or LLM calls.

## 21. Acceptance criteria

The task is complete only when:

- a separate `perception_v0` package exists;
- canonical immutable request, evidence, observation, activation, manifest, and
  event contracts exist;
- all thirteen cue definitions are present;
- V0-active and shadow lifecycle behavior is enforced centrally;
- all required deterministic detectors are implemented and versioned;
- every present activation has exact valid evidence;
- runtime cues use trusted runtime facts only;
- unknown is preserved instead of becoming false;
- no cue asserts intent, emotion, sarcasm, hostility, authorization, missing
  task constraints, or capability eligibility;
- coverage uncertainty is explicitly identified as V0 coverage uncertainty;
- confidence and salience are not fabricated;
- all 20 reviewed-shaped design cases execute through the production engine;
- the fixture report exposes mismatches and ontology questions;
- the event JSON round-trips;
- shadow cues have zero interpretation influence;
- existing legacy inspector behavior and tests remain intact;
- no LLM, network, database, training, or live-chat integration is introduced;
- the Cognitive README is updated to say Perception V0 is implemented while
  clearly distinguishing it from the legacy inspector and unimplemented
  Interpretation V0.

## 22. Deferred V1+ work

Do not include the following merely because the design mentions them:

- `BAAI/bge-small-en-v1.5` or MiniLM benchmarking;
- frozen encoder execution;
- learned multi-label heads;
- Platt, temperature, or isotonic calibration;
- ensembles or energy-based OOD detection;
- learned span extraction;
- personalized residual detectors;
- neural attention/salience;
- automatic cue proposal or promotion;
- perception training jobs;
- weak-label generation from an LLM.

V0 runtime traces may later supply candidate examples, but ordinary predictions
are not labels and the design fixture is not training ground truth.

## 23. Next milestone

After Perception V0 passes its own contracts and fixture tests, implement the
deterministic Interpretation V0 and scenario builder:

```text
PerceptionEvent.activations_for_interpretation()
+ bounded text/context
+ trusted runtime and retrieved-memory evidence
             |
             v
semantic frames
+ multidimensional hypotheses
+ unknown/out-of-scope records
+ coherent competing scenarios
             |
             v
canonical InterpretationEnvelope
             |
             v
CognitiveTurnRunner -> appraisal -> dynamic state
```

The first full forward integration test should then prove:

```text
message + runtime facts
-> Perception V0
-> Interpretation V0
-> appraisal
-> dynamic-state revision
```

## 24. Central implementation rule

> Perception records observable, evidence-linked signals. When the evidence does
> not support a signal, preserve unknown. Never improve apparent completeness by
> smuggling interpretation, emotion, authorization, or imagined runtime facts
> into perception.
