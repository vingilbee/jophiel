# Cognitive input system

This package is the successor to the experimental
`Cognitive/cognitive_nodes` code. It contains two deliberately separate paths.
The original flat-schema rules/LLM pipeline remains the legacy diagnostic path
used by the Cognitive Test inspector. The new `perception_v0` and
`interpretation_v0` packages implement the canonical, evidence-linked,
deterministic front of the target cognitive runtime:

```text
message + bounded context + trusted runtime -> Perception V0 -> Interpretation V0
    -> Appraisal -> Dynamic State (only through the explicit shadow runner)
```

Perception V0 and Interpretation V0 are implemented. Interpretation consumes
the exact active-cue handoff returned by `PerceptionEvent.interpretation_handoff()`;
shadow cues never enter its evidence or rules. The deterministic
`CognitiveTurnRunner` connects an explicitly supplied interpretation envelope
to Appraisal and Dynamic State for tests and future integration. None of these
packages is connected to live chat, persistence, routing, action selection, or
language generation. The legacy single-call experiment extends its own flat
contracts with proposed action selection; it is not an interpretation of the
canonical Perception/Interpretation V0 path.

`system/cognitive_pipeline_v0/` is the canonical shadow composition boundary.
It derives Perception, Interpretation, and Appraisal views from one trusted
runtime snapshot, preserves exact stage outputs at each handoff, and returns a
single validated `CognitivePipelineTrace`. It intentionally requires already
compiled personality inputs, retrieved context, active goals, and a previous
dynamic-state snapshot; it does not fabricate or retrieve them.

The authenticated `POST /api/cognitive/inspect` endpoint and the Chat page's
**Cognitive test** panel still run the legacy diagnostic path without creating a
conversation or invoking the chat agent. They use `system/perception.py`,
`system/interpretation.py`, and `system/pipeline.py`; they do not invoke
`cognitive_pipeline_v0` and must not be treated as the canonical cognitive V0
runtime.

## Perception V0 runtime

`system/perception_v0/` provides immutable request, runtime-fact, evidence,
observation, cue-activation, manifest, and event contracts. Its static manifest
contains nine `v0_active` cues and four shadow cues. All thirteen detectors are
pure, local, deterministic, and versioned; present activations retain exact
text, message, sequence, or trusted-runtime evidence. Unsupported language,
unresolved quote ownership, and insufficient context remain `unknown` rather
than becoming false. Shadow cues are visible for evaluation but the canonical
`interpretation_handoff()` view excludes them by manifest lifecycle while
preserving the event and detector version envelope.

The runtime makes no model, network, database, training, or live-chat call.
`perception_v0/fixture_validator.py` is review infrastructure and is the only
production-adjacent module allowed to read `data/perception_design_cases.json`.
The fixture remains `draft_for_human_review` and is not training data. Its
validator currently reports two ontology conflicts without changing the
fixture: no tool event is `not_applicable` under the runtime contract rather
than the fixture's `absent`, and a quoted-self-reference case also contains an
unquoted first-person “we” despite expecting a zero count.

## Interpretation V0 runtime

`system/contracts/interpretation.py` is the single canonical contract shared
by Interpretation and Appraisal. `appraisal.models` re-exports those exact
classes for compatibility; downstream packages do not import the
Interpretation engine. The dependency direction is:

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

`system/interpretation_v0/` validates ownership and trusted runtime facts,
builds a typed evidence registry, grounds a bounded predicate vocabulary,
evaluates separate communicative-act, speaker-goal, modality, and pragmatic
hypotheses, applies at most one ambiguity-gated personality support step, and
emits deterministic retained scenarios from explicit compatibility-linked
hypothesis groups. Conflicting meanings cannot share a branch, and compatible
alternative hypotheses cannot be split into incoherent partial branches. The
runtime is local and replayable: it reads no
clock or UUID source and makes no network, database, model, action, routing, or
persistence call. Unknown inputs, contradictions, unavailable predicates, and
out-of-scope actions remain explicit rather than being guessed.

The 23-case interpretation design fixture remains
`draft_for_human_review`. Its validator classifies 22 cases as live V0 eligible
and executes them through the real Perception and Interpretation engines. The
`positive-negative-mismatch` case is ontology-only because its expected tone
mismatch depends on shadow positive/negative wording cues. One review question
remains explicit: the fixture uses semantic polarity `mixed` although the
canonical contract permits only `positive`, `negative`, or `unknown`.

Appraisal now validates interpretation ownership and all emitted scenario,
event, goal, value, relationship, capability, dimension, conflict, and
aggregate references. Cognitive-turn traces retain focus-message and complete
interpretation version provenance. Dynamic-state snapshots retain
`conversation_ref` and reject cross-conversation or personality-mapper
mismatches. This is still a shadow forward path, not a claim that action
selection, metacognitive routing, learning, or the complete cognitive system is
implemented.

Interpretation V0 acceptance hardening is complete for the shadow scope:
unauthorized context is rejected before content inspection, evidence registries
are reconstructed and compared exactly, hypothesis contributions are
consolidated, scenario composition is compatibility-checked, semantic arguments
are registry-validated, unsupported languages fail explicitly, every execution
phase has typed-failure coverage, and the required contrasting cases traverse
the connected downstream pipeline. The accurate status is **implemented and
acceptance-hardened shadow V0**. It is suitable for isolated deterministic
development and testing, but it is still not connected to live cognitive routing.

## Single-call experiment

The inspector now offers **LLM** and **Rules** modes. LLM mode predicts one
`CognitiveState` defined in `llm.py`: explicit perception, contextual
interpretation, and a proposed `respond`, `clarify`, or `delegate` action.
It uses one structured-output provider request, with retries disabled and a
30-second timeout. Set `COGNITIVE_MODEL` to override the existing `CHAT_MODEL`;
the configured `OPENAI_API_KEY` is shared. No model call occurs during import.

Send `mode: "llm"` to `POST /api/cognitive/inspect`. This mode returns `state`,
runtime attachment counts, and metrics; omitted mode retains the original rules
response. `recent_messages` should contain ordered, role-labelled user and
assistant messages. The UI supplies both roles. Counts are supplied by code;
attachment contents are not sent to the model. Optional `available_capabilities`
are hypothetical diagnostic names, not authorization. The UI supplies no
capabilities yet. Validation rejects delegation to names absent from that list,
inconsistent actions, and incomplete outputs. Failures return HTTP 502 rather
than silently substituting a rule prediction.

From `backend`, compare the reviewed cases:

```bash
.venv/bin/python -m app.ai.Cognitive.system.training.evaluate_llm
.venv/bin/python -m app.ai.Cognitive.system.training.evaluate_llm --llm --output /tmp/cognitive-comparison.json
```

The second command makes one model request per case. Scores cover intent,
clarification for rules, and action/capability selection for the LLM. Review
constraint preservation and resolved goals in the full output manually. These
cases are a regression sample, not a general accuracy estimate. Compare actual
agent outcomes and added latency against direct-agent behavior before integration.

The agent is not connected yet. A future handoff must preserve the original
message/history alongside this advisory state and use runtime capabilities and
authorization for execution.

## Persistent memory foundation

The PostgreSQL schema separates memory by cognitive purpose:

- `chat_messages` remains the source of the recent short-term transcript.
- `working_memories` stores one derived cognitive snapshot per conversation,
  including mode, active goals, unresolved questions, and retrieved-memory ids.
- `episodic_memories` stores time-bound experiences with source-message
  provenance, importance, emotional significance, and consolidation state.
- `semantic_memories` is the former `long_term_memories` fact store, renamed
  without discarding data and extended with supporting/contradicting episode ids.
- `learning_memories` stores perception, interpretation, action, outcome, and
  feedback separately so a prediction cannot silently become ground truth.
- `core_memory` stores versioned identity, traits, perception priors,
  interpretation biases, drives, action style, values, and learning policy.

The migration seeds a conservative `columbina` core. Automatic core updates
are disabled; future learning must preserve feedback provenance and honor the
locked safety/value paths.

## Target architecture and current status

Columbina is intended to support two explicit runtime modes:

- **Agent mode** keeps the existing task-oriented agent, including its prompt,
  tools, delegation, and responsibility to complete a user request.
- **Cognitive mode** gives Columbina a persistent identity whose behavior is
  produced from structured cognitive state, retrieved memories, learned
  policies, and the current situation. It should not depend on a large persona
  prompt or on the language model consistently interpreting raw personality
  numbers. Narrow model instructions and schemas may still be used to perform
  a specific cognitive operation or to realize language.

The mode switch and cognitive-mode integration are not implemented yet. The
current cognitive inspector remains an isolated experiment and does not alter
live chat behavior.

### Processing timescales

The target system has four related paths. Slow cognition and learning are
separate processes:

```text
incoming turn
    |
    v
runtime context + memory retrieval + mapped core identity
    |
    v
perception -> interpretation -> goal/action selection -> language realization
    |                    |
    |                    +-- metacognitive controller may invoke slow cognition
    |                        for uncertainty, contradiction, novelty, risk,
    |                        irreversibility, or an explicit reasoning request
    v
post-turn reflection -> candidate memories and learning evidence
    |
    v
periodic consolidation/training -> reviewed, versioned memory and policy updates
```

1. **Fast cognition** runs during ordinary, low-risk conversation. It moves
   from perception to interpretation to an approved action and response with a
   small latency budget.
2. **Slow cognition** runs during a difficult turn. It checks evidence,
   alternatives, uncertainty, counterfactuals, and likely consequences, and it
   may clarify or abstain.
3. **Post-turn reflection and learning** converts the trace and its observed
   outcome into candidate episodic, semantic, and learning records. It must not
   treat a model inference as ground truth.
4. **Periodic consolidation and training** reviews accumulated evidence,
   summarizes episodes, updates learned policies, and proposes slow identity
   changes using versioned offline jobs rather than per-message gradient updates.

### Cognitive state and identity

Core memory is the slow-changing identity baseline. Its numeric metrics cover
traits, perception priors, interpretation biases, drives, action style, values,
and learning policy. Runtime state is faster and may include valence, arousal,
curiosity, confidence, attention, active goals, unresolved questions, and
relationship context.

Raw metrics should not be passed to a language model with the expectation that
their meaning will remain stable across calls. A deterministic
`PersonalityRuntimeMapper` should turn versioned core metrics into operational
effects:

- attention and salience weights for perception;
- priors and hypothesis preferences for interpretation;
- affinities and inhibitors for action selection; and
- bounded autonomous-initiative timing, including compiled interaction patience;
  and
- bounded language-style controls for response realization.

Personality changes what Columbina notices, how she weighs possible meanings,
and which safe actions she prefers. It must not rewrite observations or convert
uncertain interpretations into facts. Effective behavior may combine the core
baseline, user-specific adjustment, and temporary situation state, with fixed
bounds and locked values.

`interaction_patience` may change how long Columbina leaves conversational space
before proposing an autonomous continuation. It must not change VAD endpointing,
evidence-settling time, outcome-window closure, tool timeouts, or what silence
means. Those are observation and runtime policies rather than personality.

### Memory roles and evidence

- The recent `chat_messages` window is the short-term transcript.
- `working_memories` is the derived active workspace for the conversation.
- `episodic_memories` records situated experiences and their provenance.
- `semantic_memories` records generalized facts and beliefs supported or
  contradicted by episodes.
- `learning_memories` records cognitive traces: cues, predicted interpretations,
  selected actions, outcomes, feedback, reward, confidence, and model versions.
- `core_memory` records the versioned identity baseline and its learning limits.

An LLM may transform conversation into structured **candidate** learning data,
including evidence spans, competing hypotheses, selected action, outcome, and
proposed memories. Deterministic validation must retain source-message ids,
distinguish observed/explicit/inferred/corrected/confirmed/contradicted/unknown
claims, and enforce ownership and safety constraints. Explicit human correction
has the strongest authority. AI evaluation is weaker evidence and must retain
its provenance. Ambiguity, such as unresolved sarcasm, should remain a
distribution over hypotheses rather than being forced into a false certainty.

### Action direction

Action selection is separate from language generation. In the current cognitive
design, an action primarily means an abstract **conversational move** chosen by
Columbina, while the final text is one realization of that move. External tool
actions may later participate in the same plan, but they remain a separate
execution category with capability and authorization gates.

The accepted boundary is:

```text
user's conversational act       -> interpretation
Columbina's intended response   -> conversational action
actual response wording         -> language realization
external side effect            -> separately authorized tool execution
```

#### Compositional action model

The action vocabulary is hierarchical and compositional rather than a flat list
of every possible behavior. Each conversational move combines independently
versioned dimensions:

```text
conversational move
├── family: broad class of behavior
├── function: intended conversational effect
├── target: proposition, uncertainty, goal, or state being addressed
├── form: structural form, when relevant
├── stance: certainty, agreement, or commitment
├── rhetorical strategies: example, analogy, contrast, humor, and similar devices
├── social strategies: warmth, validation, reassurance, directness, and tact
├── structural requirements and hard system invariants
└── priority: primary or supporting move
```

This prevents a vocabulary explosion. `clarify_missing_recipient` and
`clarify_ambiguous_reference`, for example, are parameterizations of a broader
information-seeking function rather than unrelated hard-coded actions.

One response may contain an ordered action plan with one primary move and zero
or more compatible supporting moves:

```text
acknowledge -> explain -> ask a comprehension check
```

The action plan contains no final prose. The language realizer must express the
selected moves without silently adding, removing, or replacing their intended
effects.

The proposed stable plan envelope is:

```json
{
  "schema_version": 1,
  "cognitive_turn_id": "turn-123",
  "action_plan_id": "action-plan-123",
  "primary_move_id": "move-2",
  "moves": [
    {
      "move_id": "move-1",
      "action_key": "give_feedback.acknowledge",
      "family": "give_feedback",
      "function": "acknowledge",
      "target": {"interpretation_ref": "hypothesis-4"},
      "parameters": {},
      "priority": "supporting",
      "rationale_evidence": [],
      "expected_effects": ["signal_attention"]
    },
    {
      "move_id": "move-2",
      "action_key": "seek_information.clarify",
      "family": "seek_information",
      "function": "clarify_missing_argument",
      "target": {"semantic_argument": "send_report.recipient"},
      "parameters": {"question_form": "open_set"},
      "priority": "primary",
      "rationale_evidence": [],
      "expected_effects": ["resolve_required_argument"]
    }
  ],
  "hard_invariants": {
    "required_evidence_refs": [],
    "forbidden_claims": [],
    "authorized_capabilities": []
  },
  "termination": {
    "mechanism": "stop_action",
    "runtime_budget_ref": "chat-runtime-budget-v1"
  },
  "versions": {
    "action_registry": "conversational-actions-v1",
    "selection_policy": "action-policy-v0",
    "compatibility_policy": "action-compatibility-v1"
  }
}
```

#### Provisional initial action families

The family structure is accepted as the starting direction. The exact V0
function list remains provisional until contrasting action-design cases are
reviewed.

| Family | Initial candidate functions |
| --- | --- |
| `provide_information` | answer, inform, explain, elaborate, summarize, exemplify, justify, correct, disclose uncertainty, warn |
| `seek_information` | clarify, disambiguate, verify, explore goal, explore preference, request evidence, explore reason, check understanding, request feedback |
| `give_feedback` | acknowledge, confirm understanding, signal misunderstanding, paraphrase, reflect, validate, indicate attention |
| `express_stance` | agree, disagree, partially agree, challenge, evaluate, express preference, express uncertainty, withhold judgment |
| `support_socially` | reassure, encourage, comfort, validate experience, congratulate, appreciate, thank, apologize, express sympathy |
| `advise_or_influence` | advise (parameterized from neutral option through strong guidance), warn, discourage, request user action, set boundary |
| `commit_or_coordinate` | offer, promise, accept or decline a request, accept or decline a suggestion, propose plan, state next step, defer, hand off |
| `manage_dialogue` | greet, close, introduce or shift topic, return to topic, pause, resume, repair, self-correct, request repetition, redirect |
| `social_play` | joke, playful reframe, light tease, banter, play along |

Several functions require strict semantic boundaries. Acknowledgement is not
agreement; validation does not assert that a claim is correct; reflection does
not turn inferred affect into fact; reassurance must not promise an unsupported
outcome; and a challenge must address content rather than attack a person.

The structure follows the multidimensional and hierarchical treatment of
communicative functions in the
[DIT++ taxonomy](https://dit.uvt.nl/dit4/) and the separation of forward- and
backward-looking dialogue functions in
[DAMSL](https://www.cs.rochester.edu/research/speech/damsl/RevisedManual/).

#### Question actions

`ask_question` is a form, not a sufficient action definition.
Information-seeking moves separate the reason for asking from the question form.

Initial purposes are:

- clarify a missing semantic or task argument;
- clarify a referent or unfamiliar meaning;
- disambiguate competing interpretations;
- verify an assumption;
- explore a goal, preference, reason, or experience;
- request evidence;
- check the user's understanding; and
- request feedback or satisfy bounded curiosity.

Initial forms are:

| Form | Intended answer shape |
| --- | --- |
| `open_set` | One or more unconstrained values, such as who, what, where, or why |
| `yes_no` | Truth of a proposition |
| `choice` | One option from explicit alternatives |
| `check` | Confirmation or correction of Columbina's tentative understanding |
| `scale` | A bounded rating or degree |
| `free_reflection` | An unconstrained description of experience or reasoning |

The first four correspond to set, propositional, alternatives, and check
questions in DIT++. A clarification action therefore composes a purpose and a
form:

```json
{
  "family": "seek_information",
  "function": "clarify_referent",
  "target": "museum",
  "parameters": {
    "question_form": "choice",
    "candidate_answers": ["museum-a", "museum-b"]
  }
}
```

Question selection should optimize expected information gain and downstream
decision value, not curiosity alone. Research on proactive clarification shows
that selecting a context-relevant clarification can materially improve the
quality of information retrieval
([Aliannejadi et al.](https://arxiv.org/abs/1907.06554)). Missing-information
and ambiguity features may bootstrap early examples, but the long-term policy
learns when to ask, which purpose and form to use, and when another move has
higher expected value.

#### Humor and rhetorical strategies

Humor is usually a rhetorical strategy applied to another move:

```text
answer + light humor
reassure + playful reframe
```

`social_play.joke` is reserved for cases where play itself is the primary
conversational goal. Ordinary appropriateness—including grief, correction,
relationship support, prior rejection of humor, and conversational seriousness—
is learned from reviewed behavior and outcomes rather than encoded as a permanent
compatibility table. Only violations of system-level safety, privacy, truth, or
authorization invariants are hard-blocked. Personality participates as a bounded
policy input; it does not override those invariants.

#### Action definition contract

Every selectable action belongs to a versioned registry. A definition requires:

| Field | Meaning |
| --- | --- |
| `action_key` | Stable namespaced identity |
| `definition_version` | Immutable operational version |
| `parent_action_key` | Optional broader action from which constraints are inherited |
| `family` and `function` | Broad class and intended conversational effect |
| `description` | Testable semantic boundary |
| `parameter_schema` | Typed variable parts of an action instance |
| `intended_effects` | Outcomes the move is designed to influence |
| `structural_requirements` | Evidence references and parameter types required for a valid instance |
| `hard_inhibitors` | Safety, privacy, truth, authorization, or capability conditions that make the action invalid |
| `policy_features` | Learnable context features relevant to selection, ordering, compatibility, and stopping |
| `outcome_metrics` | Observable dimensions used for later evaluation |
| `origin` | Human, system, or reflected proposal |
| `status` | Candidate, shadow, approved, deprecated, or retired |

Definitions are historical contracts. Changing an action's meaning or safety
boundary creates a new version rather than rewriting previous traces.

#### Candidate construction and parameter binding

Candidate construction turns the current structured cognitive state into
grounded conversational-operator instances. It proposes possibilities; it does
not select the winner, learn utilities, or realize final wording.

The design combines three established patterns:

- [ISO 24617-2](https://www.iso.org/standard/76443.html) and
  [DIT++](https://dit.uvt.nl/) separate communicative function, semantic
  content, dialogue dimensions, relations, and qualifiers. DIT++ defines a
  dialogue act semantically as a communicative function applied to content to
  update information state.
- Google's
  [Schema-Guided Dialogue](https://research.google/pubs/towards-scalable-multi-domain-conversational-agents-the-schema-guided-dialogue-dataset/)
  represents changing functions through descriptions, required and optional
  typed slots, defaults, and result fields. This supports a shared model over a
  dynamic registry instead of one fixed output head per action.
- [Soar operator proposals](https://soar.eecs.umich.edu/tutorials/soar_tutorial/01/)
  bind parameterized operator schemas to objects in working memory before the
  decision procedure compares and selects them.

The corresponding Columbina boundary is:

```text
action key             = communicative function
target references      = semantic or cognitive content being addressed
action parameters      = typed variables that change the action instance
intended effects       = desired information-state or dialogue-state update
policy features        = context that changes preference, not action identity
realization controls   = wording and presentation after selection
```

##### Inputs, targets, parameters, and realization controls

These concepts must remain distinct.

**Cognitive inputs** are the complete versioned state available to construction
and scoring: perception cues, interpretation hypotheses, semantic frames,
unknowns, contradictions, appraisal, available dynamic state, active goals,
bounded personality features, partial-plan moves, previous-turn actions and
outcomes, and authorized retrieved-memory references.

**Targets** identify what a move addresses. Initial targetable object types are:

```text
semantic frame or semantic argument
proposition
interpretation hypothesis or competing-hypothesis set
unknown or unresolved argument
contradiction
active goal
appraisal field
available dynamic-state field
previous move in the current partial plan
previous-turn action, realization, or observed outcome
```

Not every input is a target. Personality normally changes a preference score;
it is not the content of an action. Retrieved memory should normally support a
target proposition through a versioned reference rather than become unstructured
content copied into an action.

**Parameters** are typed values that create a semantically different instance
of the same action. Initial reusable parameter categories include:

```text
question form and referenced candidate answers
scope and target cardinality
comparison or evaluation basis
commitment level or conditionality
requested evidence type
response-content scope or requested detail level
repair scope
```

A value belongs in `parameter_schema` only when changing it changes the
semantic structure of the move. Confidence, arousal, personality metrics,
previous actions, interpretation probability, and learned utility are policy
inputs. Sentence length, vocabulary, contractions, and friendly tone are
normally realization controls. Qualifiers such as uncertainty or conditionality
may be semantic parameters when they change Columbina's epistemic or commitment
stance; purely stylistic intensity remains in realization.

##### Registry schema

Each versioned action definition extends the common action contract with the
following construction fields:

```json
{
  "action_key": "seek_information.clarify",
  "definition_version": 1,
  "family": "seek_information",
  "function": "clarify",
  "description": "Request information needed to resolve a represented unknown.",
  "target_schema": {
    "accepted_types": [
      "unknown",
      "unresolved_semantic_argument",
      "ambiguous_reference",
      "competing_interpretations"
    ],
    "minimum_targets": 1,
    "maximum_targets": 1
  },
  "parameter_schema": {
    "question_form": {
      "type": "enum",
      "required": true,
      "values": [
        "open_set",
        "yes_no",
        "choice",
        "check",
        "scale",
        "free_reflection"
      ]
    },
    "candidate_answer_refs": {
      "type": "reference_array",
      "required_when": {"question_form": "choice"}
    }
  },
  "evidence_requirements": [
    "target resolves to a current cognitive object",
    "unknown or ambiguity remains unresolved"
  ],
  "intended_effects": [
    "reduce_target_uncertainty",
    "obtain_missing_information"
  ],
  "hard_inhibitors": [
    "target reference does not exist",
    "question requires prohibited disclosure"
  ]
}
```

`required_when` expresses conditional structure rather than a learned social
preference. For example, `candidate_answer_refs` is structurally required for a
choice question, while the decision to use a choice question instead of an open
question is learned policy.

Definitions should inherit reusable family schemas and declare only meaningful
overrides:

| Family | Shared construction schema |
| --- | --- |
| `provide_information` | Supported content targets, epistemic qualification, content scope, and evidence provenance |
| `seek_information` | Unknown, hypothesis, goal, or proposition targets plus the shared question-form schema |
| `give_feedback` | Current or prior dialogue-act targets and references needed to distinguish acknowledgement, agreement, reflection, and validation |
| `express_stance` | Proposition or option targets plus epistemic, agreement, comparison, or judgment qualifiers |
| `support_socially` | Supported experience, concern, outcome, or user-act targets without inventing hidden affect |
| `advise_or_influence` | Goal, option, consequence, and requested-user-action targets with intended-force semantics |
| `commit_or_coordinate` | Request, suggestion, plan, capability, and commitment targets with authorization and feasibility evidence |
| `manage_dialogue` | Discourse state, active topic, prior move, failure, repair, pause, resume, and closing targets |
| `social_play` | Social or rhetorical target and scope without bypassing ordinary policy scoring or hard invariants |

Examples of action-specific overrides are:

```text
seek_information.clarify
  accepts: represented unknown, unresolved argument, or ambiguity
  requires: question_form
  conditionally requires: candidate answers for question_form=choice

provide_information.answer
  accepts: question, requested proposition, or requested semantic frame
  requires: supported content reference
  optionally parameterizes: response-content scope

provide_information.correct
  accepts: proposition, semantic-frame assertion, or prior Columbina claim
  requires: correction content and contradicting evidence references

give_feedback.acknowledge
  accepts: user act, statement, request, or correction
  requires: no semantic parameter beyond a valid target

manage_dialogue.repair
  accepts: prior interpretation, action, or realization failure
  requires: prior trace and correction or mismatch evidence
  optionally parameterizes: repair scope

STOP
  accepts: current partial-plan and goal state
  requires: no semantic parameter
  records: stopping basis in the decision trace, not as action content
```

##### Deterministic V0 construction

The V0 constructor enumerates approved definitions plus `STOP`, binds their
target and parameter schemas against the current state, attaches evidence, and
preserves every result category:

```text
approved definition
        |
        v
attempt target and parameter binding
        |
        +-- uninstantiable: required target or parameter cannot be bound
        |
        v
instantiated candidate
        |
        v
hard-invariant validation
        |
        +-- hard_invalid: schema, evidence, capability, authorization,
        |                 safety, privacy, or truth invariant fails
        |
        v
eligible candidate
        |
        v
policy scoring
        |
        +-- eligible_low_scoring: valid but contextually unattractive
        |
        +-- selected or unselected eligible candidate
```

The constructor favors recall over preference: an awkward joke may be a valid
instantiated candidate and then lose during scoring. It must not disguise an
ordinary contextual disadvantage as failed construction or a hard inhibitor.
If one action schema binds to two targets, it creates two grounded candidates,
for example `clarify(reference-A)` and `clarify(reference-B)`. Exact duplicates
with the same definition version, ordered target references, and normalized
parameters are canonicalized while retaining all proposal and evidence sources.

Every candidate instance records at least:

```json
{
  "candidate_id": "candidate-123",
  "action_key": "seek_information.clarify",
  "definition_version": 1,
  "target_refs": ["unknown-reference-a"],
  "parameters": {"question_form": "choice"},
  "supporting_evidence_refs": ["message-12", "frame-4"],
  "proposal_reasons": ["unresolved_reference"],
  "construction_status": "instantiated",
  "registry_version": "conversational-actions-v0"
}
```

Candidate construction is therefore accepted at the architectural level. The
remaining work is to approve reusable family schemas and the action-specific
target, parameter, evidence, cardinality, and effect overrides for the initial
registry. This is registry authoring, not another learned cognitive subsystem
and not necessarily a separate PostgreSQL table.

The current reviewable family templates, all 75 provisional key overrides,
overlap decisions, and coverage-based lifecycle recommendations are recorded in
[`data/CONVERSATIONAL_ACTION_REGISTRY_V0.md`](data/CONVERSATIONAL_ACTION_REGISTRY_V0.md).

#### Small deterministic hard gate

Columbina learns ordinary conversational eligibility, inhibition, ordering, and
preference from experience. A deliberately small deterministic gate protects
conditions that must not be traded against learned utility. It answers only:

> Is this grounded action structurally and systemically permissible?

It does not answer whether the action is socially appropriate or useful.

The gate runs after construction and before learned scoring:

```text
action definition
        |
        v
target and parameter binding
        |
        +-- uninstantiable
        |
        v
grounded candidate
        |
        v
small deterministic hard gate
        |
        +-- blocked
        +-- shadow_only
        |
        v
passed candidate -> learned contextual scoring
```

The accepted V0 gate contains six categories:

| Gate | Deterministic responsibility |
| --- | --- |
| Registry and lifecycle | Require a known action key and immutable definition version. Candidate actions cannot affect live behavior; shadow actions may be scored and logged but not selected; only approved actions may be selected. Deprecated or retired versions cannot enter new plans. |
| Schema and reference integrity | Validate target types and cardinality, parameter types and conditional requirements, evidence references, cognitive-turn ownership, authorization to access references, and whether referenced objects remain current rather than deleted or superseded. |
| Truth and evidence | Block a candidate only when its required realization necessarily asserts a known false, unsupported, or more-strongly-contradicted factual claim. Preserve represented uncertainty rather than blocking it. |
| Capability | Block a commitment, acceptance, completion claim, or executable step that necessarily requires an unavailable capability. Merely discussing or explaining a capability does not require possessing it. |
| Authorization, consent, and privacy | Block commitments, executions, retrievals, or disclosures that lack required authority, user confirmation, data-owner consent, or permitted audience. Silence and continued conversation are not consent. |
| System safety | Consume a versioned application safety decision. A safety block cannot be overcome by utility, personality, relationship state, model confidence, or previous positive outcomes. |

The gate distinguishes failure to construct from violation after construction.
For example, `correct` with no correction target is `uninstantiable`; a grounded
correction whose replacement contradicts stronger verified evidence is
`blocked`. A malformed candidate that somehow passes construction is blocked by
schema validation as an implementation fault.

An abstract action should pass when a truthful bounded realization remains
possible. The gate may attach mandatory realization constraints instead of
blocking it. For example, reassurance may pass with
`forbid_unsupported_guarantee`, and uncertain information may pass with
`preserve_uncertainty`. The realizer must not silently replace the selected
strategy in order to satisfy those constraints.

The result vocabulary is:

```text
uninstantiable
  Required semantic structure could not be bound by construction.

blocked
  At least one deterministic hard invariant failed.

shadow_only
  Structurally valid, but the definition is not approved for live selection.

passed
  Eligible for learned contextual scoring.
```

The gate returns every applicable violation and constraint rather than stopping
after the first failure:

```json
{
  "candidate_id": "candidate-123",
  "gate_version": "action-hard-gate-v0",
  "status": "blocked",
  "violations": [
    {
      "category": "capability",
      "code": "capability_unavailable",
      "subject_ref": "send_email",
      "evidence_refs": ["runtime-capabilities-1"]
    },
    {
      "category": "authorization",
      "code": "authorization_missing",
      "subject_ref": "recipient-list",
      "evidence_refs": ["authorization-snapshot-1"]
    }
  ],
  "realization_constraints": [],
  "versions": {
    "action_definition": 1,
    "registry": "conversational-actions-v0",
    "safety_policy": "safety-policy-version"
  }
}
```

A passing result may instead carry constraints:

```json
{
  "candidate_id": "candidate-456",
  "gate_version": "action-hard-gate-v0",
  "status": "passed",
  "violations": [],
  "realization_constraints": [
    {
      "code": "preserve_uncertainty",
      "subject_ref": "possible-launch-date"
    },
    {
      "code": "forbid_unsupported_guarantee",
      "subject_ref": "project-outcome"
    }
  ]
}
```

The hard gate must not contain rules such as:

```text
never joke in serious conversation
never ask consecutive questions
never disagree with the user
always acknowledge before correcting
never advise unless explicitly requested
never continue when the user is silent
high arousal forbids elaboration
low relationship familiarity forbids playfulness
```

Those are contextual policy questions. They remain eligible and receive learned
positive or negative preference with uncertainty and evidence.

Experience never mutates a hard gate directly. Repeated experience may propose
a candidate invariant, but promotion requires explicit review, regression cases,
and a new versioned gate or system policy. Normal experience updates the learned
action policy instead.

#### Learned sequential selection

Conversational eligibility, inhibition, ordering, compatibility, plan length,
and stopping are policy decisions to be learned rather than permanently encoded
as conversational rules. The selector produces a variable-length sequence one
move at a time and terminates by selecting `STOP`:

```text
interpretation hypotheses + unknowns and contradictions
        +
current goals + qualitative appraisal + available dynamic state
        +
core action affinities + relationship context
        |
        v
sequential policy state
        |
        +-- choose next approved conversational move
        |        |
        |        v
        |   append move and update policy state
        |        |
        |        +-- repeat
        |
        +-- choose STOP
                 |
                 v
validate hard invariants -> language realization
```

The policy learns which moves are useful, how many are needed, which combinations
work, their order, and when to stop. There is no semantic maximum plan length or
fixed question count. A runtime token, latency, and compute budget remains a
system termination boundary, not a theory of good conversation.

Policy features include goal fit, interpretation fit, missing-information
reduction, expected helpfulness, appraisal, dynamic state, relationship history,
bounded core identity, previous moves in the current plan, repetition, risk, and
response cost. Unavailable appraisal or dynamic-state fields remain explicitly
unknown; neither the policy nor the realizer may invent them.

Only approved action definitions may be selected in production. The selection
mask enforces schema validity, referenced-evidence existence, capability and
authorization state, and system-level safety, privacy, and truth constraints.
Ordinary social appropriateness and move compatibility are not hard masks.

Core personality changes bounded learned affinities after hard validity is
established.
For example, curiosity may favor exploratory questions, warmth may favor
acknowledgement, playfulness may favor humor, and directness may favor concise
answers. No trait may bypass a system invariant or factual boundary.

#### Bootstrap and training stages

There is no reviewed action dataset yet, so a learned production policy does not
exist. Development proceeds in stages:

1. **Teacher bootstrap:** an LLM proposes plans using only approved action keys.
   These proposals are weak labels with AI provenance, not ground truth.
2. **Reviewed imitation:** reviewers accept, remove, add, or reorder moves and
   identify the preferred `STOP` position. A compact sequential policy learns
   from these reviewed plans.
3. **Shadow prediction:** the learned policy proposes full plans with zero live
   influence while its choices and predicted outcomes are compared with the plan
   actually used.
4. **Guarded contextual learning:** after sufficient outcome evidence, update
   action affinities and the sequential policy from reviewed traces.
5. **Offline reinforcement learning:** only after reliable multi-turn outcomes
   exist, evaluate whether offline RL improves delayed conversational results
   without violating invariants or degrading calibrated behavior.

The production system does not perform unrestricted per-turn online gradient
updates. Exploration is bounded, reviewed, reversible, and deployed through
versioned shadow and promotion stages.

#### Action-policy model roadmap

The action subsystem selects registered operators over structured cognitive
state. Its runtime selector is separate from the offline system that later
learns policy updates from decision traces, outcomes, and review. Neither is the
language model that writes the final response. Hard schema, evidence, capability,
authorization, safety, privacy, and truth invariants remain deterministic gates
at every model version.

The selector uses one shared context-action scoring interface rather than one
classifier per action:

```text
policy_score(
  perception cues,
  interpretation hypotheses and uncertainty,
  qualitative appraisal and available dynamic state,
  active goals,
  bounded core action affinities,
  retrieved outcome history,
  moves already selected in this plan,
  registered action definition and parameters
) -> action utility and uncertainty
```

Relationship state is not yet designed. Until that subsystem has its own state,
evidence, update, and confidence contracts, the action policy treats relationship
input as absent or unknown. It must not infer a relationship profile merely to
fill a policy feature. A future version may consume a versioned relationship
snapshot without moving ownership of relationship learning into the action
policy.

##### Cognitive-architecture basis

The accepted runtime structure combines ideas from ACT-R and Soar without
claiming that Columbina is an implementation of either architecture.

ACT-R's procedural system separates symbolic production matching, conflict
resolution, utility learning, and production compilation. Productions whose
conditions match the current buffer state form a conflict set, and the matching
production with the greatest utility is selected. Utility learning changes
preferences from reward, while production compilation learns procedural
shortcuts. The current reference is the
[ACT-R 7 reference manual](https://act-r.psy.cmu.edu/actr7.x/reference-manual.pdf).

Soar represents deliberate goal-directed behavior as repeated selection and
application of operators to working-memory state. Rules propose operators and
create preferences; the decision procedure selects an operator; application
changes state; and the cycle repeats until the goal is achieved or an impasse
requires resolution. Soar reinforcement learning can treat preference rules as
features in a linear state-operator value function:

```text
Q(state, operator) = sum(weight_i * feature_i(state, operator))
```

The relevant primary references are the
[Soar architecture manual](https://soar.eecs.umich.edu/soar_manual/02_TheSoarArchitecture/)
and
[Soar reinforcement-learning manual](https://soar.eecs.umich.edu/soar_manual/05_ReinforcementLearning/).

Columbina therefore uses this conceptual cycle:

```text
structured cognitive state
        |
        v
propose applicable registered conversational operators
        |
        v
apply deterministic hard-invariant validation
        |
        v
compute contextual preferences or utilities
        |
        v
resolve the conflict by selecting one operator
        |
        v
apply it to the partial-plan state
        |
        +-- repeat
        |
        +-- select STOP
```

The architectural analogy has limits. ACT-R selects production rules and Soar
selects operators in bounded modeled tasks; neither directly supplies
Columbina's open-domain interpretation ontology, conversational outcomes,
identity, or language realization. Their research supports the separation of
state, applicability, preference, selection, tracing, and learning--not a claim
that the complete Columbina system reproduces human cognition.

##### V0: deterministic shared scorer

V0 requires no trained model. After the hard gate removes invalid candidates, a
versioned deterministic scorer ranks each `(context, action)` pair using explicit
terms such as interpretation fit, goal usefulness, missing-information reduction,
appraisal and state compatibility, bounded personality-action affinity,
repetition, response cost, and reviewed contextual disadvantages. Missing inputs
remain unknown and contribute only their documented neutral fallback.

The selector chooses one move, appends it to the partial plan, updates its policy
state, and scores the remaining approved moves plus `STOP` again. This gives V0
variable-length ordered plans without a semantic maximum plan length. A runtime
cycle guard may prevent a software failure, but it is not a learned theory of
good conversation. Stable deterministic tie-breaking is required so identical
versioned inputs reproduce the same plan.

V0 exists to test the ontology, feature contract, attribution boundaries, and
evaluation harness. The initial manual fixture is evaluation and design data,
not automatically approved training truth.

###### V0 selector contract

The initial contract is `action-policy-v0.1-draft`. Its architecture is accepted
for V0; its numerical weights are bootstrap calibration values and may change
only through a new version after the design cases are evaluated. The scorer does
not claim that these numbers are psychological constants.

The conversation and the within-response planner operate at different levels:

```text
turn-level belief state
        |
        v
choose one structured action plan (the turn-level macro-action)
        |
        v
realize one assistant message and observe the next user event

inside the action planner:
empty partial plan -> append move -> update state -> ... -> STOP
```

The turn-level process is POMDP-like because user intention, affect, and future
reaction are latent. V0 does not attempt to learn that POMDP. Its internal plan
builder is a deterministic finite-horizon state transition and marginal-utility
scorer. A completed plan is one turn-level action; individual plan moves do not
pretend to receive separate observations from the user.

###### Immutable turn context and mutable plan state

Planning begins from a versioned, immutable `TurnPolicyContext`. It contains
references to the structured cognitive objects available before response
planning:

```text
TurnPolicyContext
  cognitive_turn_id
  source_event_refs
  perception_snapshot_ref
  interpretation_snapshot_ref
  appraisal_snapshot_ref or explicit unknown
  dynamic_state_snapshot_ref or explicit unknown
  active_goal_refs
  unknown_refs
  contradiction_refs
  retrieved_memory_refs with provenance
  bounded_core_action_affinities
  previous_turn_action and outcome refs
  relationship_snapshot_ref or explicit unknown
  response_obligation
  registry, feature, hard-gate, and policy versions
```

The planner must not rewrite interpretation, appraisal, dynamic state, core
identity, relationship, or memory while imagining a response. It creates and
updates a separate `PartialPlanState`:

```text
PartialPlanState
  selected_moves[]
  represented_effects[]
  addressed_target_effects{}
  residual_target_needs{}
  planned_goal_coverage{}
  planned_unknown_handling{}
  planned_contradiction_repairs{}
  action_counts{}
  family_counts{}
  previous_move_ref or null
  accumulated_response_cost
  accumulated_soft_risk
  step_index
  state_signature
```

The word `planned` is mandatory. Adding `clarify(unknown-A)` records that the
message will request information; it does not mark `unknown-A` as answered.
Adding `reassure(person-A)` records represented reassurance; it does not assert
that the person was reassured. Actual changes require later outcome evidence.

`addressed_target_effects` is keyed by both target and intended effect. This
prevents one move from incorrectly satisfying every need associated with the
same target. For example, acknowledging a concern may cover
`recognized(concern-A)` without covering `explained(cause-A)`.

###### Deterministic transition

For a selected grounded move `a`, V0 computes:

```text
P_next = transition_v0(turn_context, P_current, a, registry_version)
```

The transition has the following fixed order:

1. Verify that the selected candidate and all references are the exact versions
   that were scored.
2. Append the complete grounded action instance to `selected_moves` once.
3. Copy the action definition's declared effects into `represented_effects`,
   preserving target, evidence, parameters, and provenance.
4. Update only the matching target-effect coverage and its residual need.
5. Update planned goal coverage for goal requirements linked to those effects.
6. Mark relevant unknowns as `elicitation_planned`,
   `qualification_planned`, or `no_longer_needed_for_this_plan`; never as known.
7. Mark contradictions as `exposure_planned`, `correction_planned`, or
   `reconciliation_planned`; never as actually repaired.
8. Increment exact-action and family counts and set `previous_move_ref`.
9. Add marginal response cost and soft risk to the accumulated values.
10. Reconstruct any candidates whose bindings depend on partial-plan moves,
    re-run the small hard gate where applicable, and recompute every feature.
11. Produce the next canonical `state_signature` from versioned references and
    normalized state fields.

The transition is bookkeeping over declared action effects, not a simulation of
the user's mind. Given identical versioned inputs, it must produce byte-equivalent
normalized output.

For exact target-effect coverage, V0 uses:

```text
coverage_next = max(coverage_current, declared_effect_strength)
residual_need_next = max(0, initial_need - coverage_next)
```

The default strength for an action's primary declared effect is `1.0`. A lower
strength is permitted only when it is part of the reviewed, versioned action
definition; the runtime scorer cannot invent it. Related but non-identical
effects do not automatically share coverage. This conservative rule avoids an
unreviewed semantic-overlap matrix in V0.

Repetition evidence is deterministic:

```text
1.00  same action key, target set, and material parameters in this plan
0.75  same intended effect on the same target in this plan
0.50  same family on the same target with materially overlapping effect
0.35  exact action-target repetition from the immediately previous turn
0.00  no represented repetition
```

When more than one condition applies, V0 uses the maximum rather than adding
them. Repetition is a soft cost, not a hard prohibition.

###### Shared feature manifest

All eligible actions, including newly approved actions, use the same manifest.
There is no V0 classifier per action, action-key bias, or unrestricted pairwise
interaction table. Action-specific behavior comes from typed targets,
parameters, evidence, declared effects, and residual needs.

| Feature | Range | V0 meaning | Missing or inapplicable value |
| --- | --- | --- | --- |
| `semantic_fit` | `0..1` | Strength of the typed match among the action meaning, bound target, and requested effect after construction | Construction normally supplies it; otherwise `0` |
| `target_priority` | `0..1` | Current priority of the target multiplied by the matching residual target-effect need | `0` |
| `goal_fit` | `0..1` | Strength of the explicit link from the action's effect to an unresolved requirement of an active goal | `0` when no supported residual goal link exists |
| `expected_goal_progress` | `0..1` | Marginal residual goal requirement expected to be represented by adding this move | `0` |
| `uncertainty_reduction` | `0..1` | Decision-relevant uncertainty for which this move plans elicitation, qualification, or bounded resolution | `0` |
| `contradiction_repair` | `0..1` | Residual contradiction for which the move plans exposure, correction, or reconciliation | `0` |
| `evidence_support` | `-1..1` | Grounding strength relative to the assertion or conversational effect; `0` means neutral or not evidence-bearing | `0` |
| `personality_affinity` | `-1..1` | Bounded match between compiled core action affinities and this action; it cannot change facts or eligibility | `0` when core input is unavailable |
| `state_compatibility` | `-1..1` | Match with available appraisal and dynamic state without inventing missing state | `0` when state is unavailable |
| `plan_contribution` | `-1..1` | Marginal complementarity, local coherence, and non-interference with already selected moves | `0` for an empty plan or unsupported relation |
| `repetition_cost` | `0..1` | Maximum applicable repetition value from the deterministic schedule above | `0` |
| `response_cost` | `0..1` | Marginal expected length, complexity, cognitive load, and user effort added by this move | conservative action-definition default |
| `soft_risk_cost` | `0..1` | Permissible but contextually risky downside, such as humor under unresolved seriousness | `0` without positive risk evidence |

All inputs retain their original `null` or unknown status in the trace even when
the numerical scoring fallback is `0`. A neutral contribution must never be
reported as observed evidence. Feature calculators consume normalized upstream
values and typed registry relations; a generative model does not fill missing
features at runtime.

For candidates with multiple targets, V0 computes target-dependent features per
target and takes a residual-priority-weighted mean. If every residual priority is
zero, the aggregate is zero. This prevents an action from obtaining an automatic
score advantage merely by binding more targets. Goal progress remains capped at
`1.0` for the entire move.

The boundaries between related features are:

```text
semantic_fit           can this operator meaningfully apply to this object?
target_priority        how important is the still-unaddressed target-effect?
goal_fit               is the effect relevant to an active goal?
expected_goal_progress how much new goal coverage would adding it provide?
plan_contribution      does it complement the moves already selected?
```

These values must be calculated separately. A single salience or goal score may
not be copied into several fields, which would count the same evidence multiple
times.

###### Marginal utility and initial weights

V0 scores the value of adding one candidate to the current partial plan, not the
candidate's general desirability in isolation. It first calculates remaining
need and then lets grounding, personality, state, and sequence context modulate
that need:

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

score_v0(a | C, P) = positive_marginal_value
  - 1.80 * repetition_cost
  - 0.80 * response_cost
  - 1.20 * soft_risk_cost
  - 0.30
```

The final `-0.30` is a shared move overhead. It represents the burden of adding
any extra conversational move and helps reject harmless but unnecessary
continuations. It is not a maximum response length.

The multiplicative structure is intentional. `semantic_fit` cannot make a move
useful when there is no residual need. Evidence, personality, dynamic state, and
sequence coherence may strengthen or weaken a useful move, but none can create
a reason to add irrelevant content by itself. In particular, high curiosity
cannot manufacture an unnecessary question after the relevant uncertainty has
already been handled.

The initial priorities are intentional:

- marginal goal progress is the strongest benefit;
- repetition is the strongest ordinary cost;
- personality has a small bounded influence;
- response cost discourages unnecessary complexity without forcing brevity;
- soft risk discourages questionable choices without converting ordinary social
  judgment into a hard gate.

The raw score is an ordinal deterministic utility used for comparison. It is not
a probability, confidence, predicted reward, or claim about human cognition.
Every base term, multiplier term, clamp result, and cost contribution is stored
separately. V0 has no additional hidden interaction terms or per-action bias.
Interactions enter transparently through this formula, residual needs, and
`plan_contribution` calculated from the updated partial state.

###### `STOP` and plan termination

V0 uses `STOP` as the zero-marginal-addition baseline:

```text
score_v0(STOP | C, P) = 0
```

At each step:

```text
eligible = construct_and_gate(C, P)
scored = score_each(eligible, C, P)
best = deterministic_argmax(scored)

if best.score > 0:
    P = transition_v0(C, P, best)
else:
    select STOP
```

Consequently, the planner continues only while another move has positive
marginal value. As coverage grows, goal-progress and target-priority terms fall,
while repetition and marginal response cost tend to rise. `STOP` can therefore
win without a semantic move limit or fixed question count.

An empty plan is allowed only when `response_obligation` permits no output. If a
response is required but no eligible action has positive utility, the result is
`no_viable_plan`, an operational planning failure handled by a separately
specified fallback. It must not be mislabeled as a preferred `STOP` decision.

Runtime cancellation, timeout, compute exhaustion, repeated `state_signature`,
or a defensive cycle boundary produces an operational termination code. Such a
boundary may protect the software but is not conversational evidence and must
not become a positive `STOP` training label.

###### Deterministic conflict resolution

Scores are compared using their unrounded decimal representation. Values within
the implementation's versioned numerical tolerance are tied. Ties are resolved
in this order:

1. greater `expected_goal_progress`;
2. greater `target_priority`;
3. lower `soft_risk_cost`;
4. lower `response_cost`;
5. stable ascending `(action_key, target_refs, normalized_parameters)` order.

The final lexical rule exists only for reproducibility and is recorded whenever
used. It is not preference evidence and must not be learned as though the chosen
action was semantically superior.

###### Required transition and scoring trace

Every planning step emits factual data produced by the actual computation:

```text
plan_step_id
turn_context_ref and immutable context digest
partial_state_before and state_signature
constructed candidate ids
uninstantiable and hard-gated candidates with typed reasons
for every eligible candidate:
  normalized feature values
  missing-value flags
  feature provenance refs
  weight version
  signed contribution per feature
  shared move overhead
  final raw score
STOP score
selected candidate or STOP
tie set and tie-break field, when applicable
declared effects applied
partial_state_delta
partial_state_after and state_signature
termination kind
```

The trace records computation, not a generated rationale. Later review and
outcome records join to it without mutating it.

###### V0 calibration and acceptance

The architecture, field meanings, and separation of planned effects from actual
outcomes should remain stable during V0 calibration. The bootstrap weights may
be adjusted as a single versioned vector after running the reviewed design cases.
Calibration must explicitly inspect:

- premature and unnecessarily late `STOP`;
- repeated acknowledgement, explanation, or questioning;
- useful multi-move composition and ordering;
- ambiguity and contradiction handling;
- serious contexts in which humor remains eligible but unattractive;
- cases with missing appraisal, dynamic state, personality, or relationship;
- personality influence changing style preference without defeating goal fit;
- empty-plan behavior and operational termination separation.

If failures can be corrected by changing weights, calibration may proceed. If
review repeatedly demands action-key exceptions or special ordering rules, the
team must first test whether a feature boundary, registry effect, target binding,
or cognitive input is missing. V0 must not grow an opaque collection of
case-specific bonuses.

##### V0.5: fitted transparent weights

After reviewed decisions accumulate, regularized logistic or pairwise-linear
learning may fit bounded corrections to the V0 weights. The feature vocabulary,
hard gates, sequential planning loop, and fallback remain unchanged. This stage
tests whether recurring reviewer corrections can be learned while keeping every
interaction inspectable, for example whether increasing ambiguity consistently
raises the relative value of clarification.

An initial experiment may begin around 300-1,000 reviewed decision contexts. A
production promotion is evidence-based rather than count-based and requires
held-out improvement, calibration, hard-invariant regression tests, and stable
behavior on rare or missing-input slices.

##### V1: supervised context-action ranker

V1 is a shared pairwise learning-to-rank model. Each reviewed context contains a
preferred action or partial plan and one or more acceptable, disfavored, or
invalid alternatives. The learner estimates relative utility:

```text
score(context, preferred action) > score(context, disfavored action)
```

A compact gradient-boosted ranker is the initial recommendation because the
inputs are structured, interactions may be nonlinear, and the early dataset will
be too small to justify a transformer policy. The model receives action-dependent
features from the registry, so it can share evidence across related actions and
score shadow candidates without adding a classifier head for each key. V0 remains
the fallback for unsupported contexts and insufficient-confidence decisions.

V1 experimentation is reasonable around 1,000-2,000 reviewed contexts; a more
credible promotion target is roughly 2,000-5,000 contexts with family, ordering,
`STOP`, ambiguity, and robustness coverage. These are planning ranges, not proof
that a sample count alone makes a model safe. Evaluation splits must separate
whole conversations and, when available, users and time periods to reduce trace
leakage. XGBoost documents pairwise learning-to-rank objectives at
<https://xgboost.readthedocs.io/en/stable/tutorials/learning_to_rank.html>.

##### V2: guarded contextual outcome learning

V2 may use an action-dependent contextual bandit for narrow action choices after
real outcome evidence exists. It observes a context, chooses among the currently
eligible registered actions, and updates expected outcomes for the chosen action.
This matches immediate contextual action learning but does not by itself solve
delayed multi-turn credit assignment.

Shadow experiments may begin around 5,000-10,000 logged eligible decisions. Live
promotion normally needs substantially more useful evidence--potentially
20,000-100,000 decisions depending on outcome noise, action imbalance, effect
size, and context diversity. Effective coverage matters more than the raw total.
Every trace must retain the available actions, selected action, selection
probability, policy version, context features, observed outcome evidence, and
attribution confidence. Without selection probabilities, historical outcomes
cannot support reliable counterfactual policy evaluation.

Exploration is limited to similarly safe eligible alternatives, begins in shadow,
and never bypasses a hard gate. Vowpal Wabbit's action-dependent contextual-bandit
and offline-policy-evaluation documentation describes the relevant learning and
logging pattern:
<https://vowpalwabbit.org/docs/vowpal_wabbit/python/9.6.0/tutorials/python_Contextual_bandits_and_Vowpal_Wabbit.html>
and
<https://vowpalwabbit.org/docs/vowpal_wabbit/python/latest/tutorials/off_policy_evaluation.html>.

##### Future: delayed and ordered-plan learning

When reliable reviewed trajectories and delayed outcomes exist, a sequential or
slate policy may learn move choice, move order, and `STOP` jointly. It should
remain autoregressive and inspectable at the action level:

```text
context -> next move
context + partial plan -> next move
context + completed-enough plan -> STOP
```

Offline reinforcement learning is justified only if immediate action outcomes
cannot represent important later effects and the stored trajectories support
credible credit assignment. A neural policy is a later capacity choice, not an
automatic maturity milestone; it may never outperform a compact ranker enough to
justify its opacity and data cost. A first neural/sequential investigation would
normally require on the order of 100,000 high-quality plan decisions, with much
larger datasets possible for sparse actions and noisy delayed feedback.

These models are established methods for contextual decision and dialogue-policy
learning; they do not establish consciousness, subjective understanding, or a
human-equivalent cognitive process. Columbina's complete composition remains an
experimental cognitive-inspired architecture. Promotion therefore depends on
measured behavioral validity: action preference, calibration, outcome
improvement, controlled personality influence, generalization, stability, and
traceable failure attribution.

##### Decision trace and action-learning record

At runtime the selector emits a factual, machine-readable `decision_trace`. It
must not ask a language model to invent a post-hoc explanation of why an action
was selected. A trace records the actual inputs and operations of selection:

```text
cognitive-turn and source-message references
versioned structured policy state
proposed operators and their matched applicability evidence
hard-rejected operators and exact invariant reasons
eligible operators and score or preference contributions
selected operator or STOP
tie-break mechanism, if used
partial plan before and after application
policy, registry, feature-contract, and hard-gate versions
```

A human-readable explanation may be rendered from this trace for inspection,
but that rendering is not evidence and never becomes training truth.

The separate learning record joins the immutable decision trace with later
evidence:

```text
reviewed preferred, acceptable, and disfavored alternatives
selection score, uncertainty, probability, and policy version when applicable
realization reference and realization-fidelity evidence
multidimensional observed outcomes with evidence and provenance
possible error sources and attribution confidence
review status and later correction history
```

The offline learning system uses this joined record to decide whether evidence
supports an update to operator utility, feature weights, ordering, `STOP`,
realization, or an upstream cognitive component. A negative outcome does not
automatically update every stage.

One context may yield several pairwise comparisons, but generated comparisons do
not become independent evidence and must remain grouped by their originating
conversation during evaluation.

##### Action decision ledger

The model family and staged learning direction are accepted as a design. The
following decisions remain before an action runtime or training job should be
implemented:

| Topic | Status | Decision still required |
| --- | --- | --- |
| Action ontology | Provisional and case-tested | Human-review the pending cases, resolve ontology questions, and approve the initial definitions and versions |
| Candidate construction | V0 registry binding manifest drafted; review pending | Validate the 75 structured action-effect mappings, binding modes, target roles, typed parameters, evidence, and cardinality before approving a production subset |
| V0 feature contract | Accepted V0.1 draft; calibration pending | Validate normalized shared features, missing-value behavior, provenance, and multi-target aggregation against the reviewed cases |
| V0 preference/utility | Accepted V0.1 draft; calibration pending | Evaluate the versioned marginal-utility weights, shared move overhead, zero-baseline `STOP`, and deterministic tie-breaks; change numbers only through a new policy version |
| Personality mapping | Open | Compile core metrics into bounded action-policy features without changing facts or hard eligibility |
| Relationship input | Deferred | Design the independent relationship subsystem before giving its values action-policy semantics |
| Sequential state | Accepted V0.1 draft; implementation open | Implement immutable turn context, mutable partial-plan ledgers, planned-effect semantics, deterministic transition order, and state signatures |
| `STOP` | Accepted V0.1 draft; calibration pending | Use zero marginal utility as the V0 baseline; validate early, appropriate, and late stopping and keep operational termination separate |
| Ordinary compatibility and ordering | Learning direction accepted | Represent contextual disadvantages in V0 and reviewed labels without turning them into universal hard masks |
| Hard gate compiler | Accepted direction, implementation open | Implement the six narrow deterministic gates, typed violation codes, realization constraints, lifecycle handling, and versioned regression cases |
| Realization contract | Accepted V0.1 draft; implementation and evaluation open | Compile the selected plan into a semantic kernel and bounded linguistic envelope, generate through a stable protocol, validate independently, retry once when repairable, and use a safe fallback when fidelity cannot be established |
| Outcome evidence | V0.1 logical design accepted; case validation pending | Validate `data/OUTCOME_OBSERVATION_SCHEMA_V0.md` across observation sources, episode windows, corrections, provenance, partial exposure, and worker-trigger cases before designing tables |
| Value and tradeoffs | Open | Decide when multidimensional outcomes may become a scalar, vector, preference comparison, or no learning signal |
| Credit assignment | Open | Attribute an outcome to action selection, move order, `STOP`, realization, or an upstream cognitive stage without punishing all components |
| Review workflow | Open | Define reviewer labels, disagreement handling, adjudication, dataset versions, and promotion from design data to training truth |
| Evaluation and promotion | Open | Define held-out splits, metrics, robustness slices, calibration, uncertainty fallback, statistical comparison, rollback, and version promotion |
| Candidate action lifecycle | Direction accepted, schema open | Resolve candidate parent/family representation and implement candidate, reviewed, shadow, approved, deprecated, and retired transitions |
| Runtime budgets | Open implementation boundary | Set latency, token, compute, and cycle guards without treating them as learned conversational preferences |
| Decision trace | V0 selector schema accepted; persistence open | Persist actual proposals, gates, normalized features and provenance, contributions, conflict resolution, selected move, and state delta without a generated rationale |
| Learning-system boundary | Accepted direction, implementation open | Join immutable decision traces to later outcome and review evidence, then produce offline versioned policy updates |

The V0 selector contract now defines the versioned scoring feature vector,
deterministic marginal preferences, sequential state, `STOP`, conflict
resolution, and factual decision-trace fields. The action registry includes a
structured V0 effect and binding manifest for all 75 action definitions, and the
realization contract below defines how a completed plan becomes bounded language.
The registry and realization contract still require review and executable tests
before any production subset is approved. The immediate next design dependency
is outcome evidence and credit assignment because V0 cannot be evaluated or
later learned from without them.

#### Realization contract

The initial contract is `realization-contract-v0.1-draft`. Realization is a
typed boundary between cognitive decision-making and generative language:

```text
selected completed action plan
        |
        v
deterministic realization compiler
        |
        v
RealizationSpec
        |
        v
generative language model
        |
        v
candidate message + untrusted alignment report
        |
        v
independent fidelity validation
        +-- pass -> deliver
        +-- repairable -> one constrained regeneration
        +-- hard failure or repeated failure -> safe fallback
```

The language model chooses wording, syntax, discourse markers, and permitted
surface variation. It does not choose goals, reinterpret the user, add actions,
change semantic force, create evidence, expand authority, or decide what it is
allowed to claim.

The realization instruction is a protocol prompt, not Columbina's cognitive
identity or an invitation to reason about the strategy. Cognitive mode may have
no conventional agent/persona prompt while still supplying this machine-created
semantic contract to a text generator.

##### Prompting is not the enforcement boundary

A stable prompt telling the model to follow the contract is useful but not
sufficient. V0 combines four controls:

1. deterministic compilation of a closed, grounded input specification;
2. a short stable generation protocol with positive obligations;
3. structural and semantic validation of the generated message; and
4. bounded regeneration or a safe deterministic fallback.

Research on controlled dialogue generation supports conditioning generation on
explicit dialogue acts and semantic attributes and checking semantic accuracy,
rather than relying on unconstrained conversational prompting. Relevant
references include
[Ramirez et al. (2023)](https://aclanthology.org/2023.sigdial-1.32/),
[Wagner and Ultes (2024)](https://aclanthology.org/2024.sigdial-1.19/), and the
hybrid constrained-generation system of
[Fang et al. (2023)](https://aclanthology.org/2023.findings-acl.351/).

Structured-output decoding may guarantee the JSON envelope, field types, and
required fields. It does not prove that free text inside `message_text`
faithfully expresses those fields. The model's alignment report is useful trace
data but is not independent evidence.

##### Semantic kernel and linguistic envelope

`RealizationSpec` contains two deliberately separate components:

```text
semantic kernel      what must, may, and must not be communicated
linguistic envelope  how permitted content may be expressed
```

The semantic kernel is authoritative. A style preference cannot override it.
The linguistic envelope is bounded flexibility, not another action policy.

##### `RealizationSpec` V0 schema

The compiler emits the following logical schema. The persisted representation
may use normalized relational records plus JSON, but the meaning must remain the
same:

```json
{
  "contract_version": "realization-contract-v0.1-draft",
  "realization_id": "realization-uuid",
  "cognitive_turn_id": "turn-uuid",
  "plan_id": "plan-uuid",
  "mode": "cognitive",
  "registry_version": "conversational-actions-v0-draft",
  "policy_version": "action-policy-v0.1-draft",

  "channel": {
    "kind": "chat",
    "language": "en",
    "output_format": "plain_text",
    "response_obligation": "required"
  },

  "context": {
    "current_user_event": {
      "event_ref": "message-17",
      "content": "Why did the estimate change?"
    },
    "relevant_prior_events": [],
    "resolved_referents": [],
    "selected_memory_evidence": []
  },

  "semantic_kernel": {
    "ordered_moves": [
      {
        "move_id": "move-1",
        "position": 0,
        "action_key": "provide_information.explain",
        "action_definition_version": 1,
        "effect_key": "information.explanation",
        "effect_version": 1,
        "priority": "primary",
        "target_bindings": [
          {
            "role": "primary",
            "type": "proposition",
            "ref": "proposition-9",
            "resolved_content": "The estimate changed because the represented portion changed from one serving to two."
          }
        ],
        "semantic_parameters": {
          "content_scope": "focused"
        },
        "content_units": [
          {
            "content_ref": "claim-4",
            "content": "The represented portion changed from one serving to two.",
            "epistemic_status": "supported",
            "evidence_refs": ["measurement-4"],
            "use": "required"
          }
        ],
        "realization_constraints": []
      },
      {
        "move_id": "move-2",
        "position": 1,
        "action_key": "seek_information.clarify",
        "action_definition_version": 1,
        "effect_key": "elicitation.clarification",
        "effect_version": 1,
        "priority": "supporting",
        "target_bindings": [
          {
            "role": "primary",
            "type": "unknown",
            "ref": "unknown-3",
            "resolved_content": "Which portion size the user expected"
          }
        ],
        "semantic_parameters": {
          "question_form": "open_set"
        },
        "content_units": [],
        "realization_constraints": []
      }
    ],

    "required_move_ids": ["move-1", "move-2"],
    "required_content_refs": ["claim-4", "unknown-3"],
    "allowed_claim_refs": ["claim-4"],
    "forbidden_claims": [
      "The user made an error.",
      "The estimate is medically guaranteed."
    ],
    "epistemic_constraints": {
      "preserve_status_for": ["claim-4"],
      "must_express_unknowns": ["unknown-3"],
      "forbid_unsupported_causal_claims": true
    },
    "capability_constraints": {
      "permitted_commitment_refs": [],
      "forbid_unselected_external_action_claims": true
    },
    "ordering": {
      "mode": "preserve",
      "required_precedence": [["move-1", "move-2"]]
    }
  },

  "linguistic_envelope": {
    "register": "conversational",
    "warmth_band": "moderate",
    "directness_band": "high",
    "playfulness_band": "none",
    "verbosity_band": "focused",
    "format_constraints": [],
    "lexical_constraints": []
  },

  "generation_protocol": {
    "use_only_allowed_claims": true,
    "do_not_add_conversational_moves": true,
    "do_not_describe_internal_planning": true,
    "return_alignment_report": true
  }
}
```

The example values are illustrative; the schema and enumerations are the
contract. All references must resolve to immutable objects from the same
cognitive turn or to explicitly authorized historical evidence.

##### Deterministic realization compiler

The compiler is not an LLM. Given a completed selected plan, it:

1. verifies plan, registry, action-definition, effect, and hard-gate versions;
2. includes only selected moves in their selected order;
3. resolves target identifiers into minimal human-readable semantic content;
4. joins only the evidence authorized for those moves;
5. separates claims into required, permitted, forbidden, disputed, and unknown;
6. derives epistemic and commitment constraints from structured state;
7. selects the minimum prior conversation needed for reference resolution,
   repair, coherence, or explicit action semantics;
8. includes a memory only when another cognitive component selected it as
   relevant evidence or context and preserves its provenance and status;
9. converts numeric core traits into versioned categorical style controls; and
10. fails closed with `uncompilable_realization_spec` when a required target,
    claim, evidence, scope, or authorization reference cannot be resolved.

Opaque identifiers alone are insufficient. Each target or content reference
given to the generator also needs a bounded `resolved_content` representation.
The compiler must never ask the generator to infer what `proposition-9` or
`unknown-3` means.

The compiler does not automatically include a fixed number of prior turns.
Normally it includes the current user event and only those earlier events needed
for pronouns, ellipsis, repair, repetition, quotation ownership, or a selected
action. A source event may be included as context without becoming an allowed
claim.

##### Personality and dynamic-state compilation

Raw personality metrics are not placed directly in the prompt. Different model
versions may interpret a number such as `warmth=0.63` inconsistently. A
versioned deterministic compiler maps bounded core metrics and available state
into categorical realization controls and concrete boundaries:

```text
core warmth = 0.63
        |
        v  personality-style-compiler-v1
warmth_band = moderate
permitted: brief acknowledgement, non-clinical wording
forbidden: invented emotional labels, unsupported reassurance
```

Personality may affect wording within the selected actions. It cannot add humor,
questions, advice, disagreement, promises, or any other action that the planner
did not select. Appraisal and dynamic state follow the same rule: they may
compile into bounded expression controls when known, while unavailable values
remain explicit unknowns and contribute no invented style instruction.

Relationship style remains absent until the independent relationship subsystem
has a reviewed contract. The realizer must not infer a relationship merely to
make the response sound personalized.

##### Inputs excluded from realization

The generator does not normally receive:

- unselected, rejected, hard-gated, or shadow action candidates;
- raw action scores, policy weights, cue logits, or tie-break internals;
- generated rationales or private chain-of-thought;
- the complete memory store or memories not selected for this response;
- interpretation alternatives irrelevant to the selected wording;
- uncompiled core-personality numbers;
- private or unauthorized evidence;
- future outcome labels; or
- instructions to reconsider the selected strategy.

This reduces both accidental strategy changes and unnecessary disclosure. The
decision trace preserves policy internals separately for authorized debugging.

##### Stable generation protocol

The generation prompt contains a small stable protocol plus the serialized
`RealizationSpec`. It states positively that the model must:

```text
produce one user-facing message
realize every required move and required content unit
use only permitted grounded claims
preserve epistemic and commitment strength
obey required move precedence
stay within the linguistic envelope
return the message and an alignment report
```

It also prohibits adding unselected actions, fabricating evidence, claiming
planned outcomes have already occurred, exposing internal planning, and
executing tools. The protocol should not contain an open-ended request to
"improve the strategy" or "do whatever is most helpful."

The preferred internal model response envelope is:

```json
{
  "message_text": "The estimate changed because the represented portion changed from one serving to two. Which portion size were you expecting?",
  "self_reported_alignment": [
    {"move_id": "move-1", "start": 0, "end": 85},
    {"move_id": "move-2", "start": 86, "end": 124}
  ]
}
```

The alignment is untrusted model output. It helps locate possible spans but
cannot establish that a move was correctly realized.

##### Fidelity validation

Validation compares the candidate message with the immutable
`RealizationSpec`, not with a post-hoc explanation from the generator. It has
the following layers:

| Layer | Required V0 checks |
| --- | --- |
| Envelope | Valid output schema, one message, valid move ids and span bounds, no empty required response |
| Coverage | Every required move and content unit has supporting text; optional content is not reported as required |
| Action semantics | The expressed dialogue act matches the selected action; no additional material action is introduced |
| Grounding | Names, quantities, causes, evidence, memories, and factual propositions are licensed by allowed content |
| Epistemic force | Supported, tentative, disputed, contradicted, and unknown content is not strengthened or collapsed |
| Commitment force | Offers, advice, requests, acceptance, promises, and completion claims preserve selected force and authority |
| Ordering | Required precedence is preserved; freely reorderable moves need not match surface order exactly |
| Capability and consent | No claim of unauthorized tool use, external effect, access, consent, or completed execution is added |
| Style boundary | Hard style prohibitions hold; softer preferred bands are scored separately rather than treated as factual failure |

Validation emits typed, evidence-bearing results:

```json
{
  "status": "pass | repairable | hard_fail | indeterminate",
  "violations": [
    {
      "code": "epistemic_strength_increased",
      "move_id": "move-1",
      "message_span": [0, 42],
      "contract_refs": ["claim-4"],
      "evidence": "Generated certainty exceeded the supported claim status."
    }
  ],
  "validated_move_ids": ["move-2"],
  "validator_versions": [],
  "provenance": []
}
```

The evidence string is a bounded observation attached to typed comparisons, not
a free-form rationale or chain-of-thought.

Deterministic validators check schema, references, required lexical anchors when
defined, forbidden exact values, numbers, named entities, question count and
form where structurally testable, tool/capability claims, and ordering anchors.
Semantic equivalence, dialogue-act fidelity, unsupported paraphrases, and
epistemic entailment may require a separately versioned classifier, natural
language inference model, or verifier call. NLI-based semantic-accuracy checking
is a researched option, for example
[Dušek and Kasner (2020)](https://aclanthology.org/2020.inlg-1.19/), but no
verifier output is treated as infallible ground truth.

##### Repair and fallback policy

V0 permits at most one semantic regeneration for a repairable failure. The
second generation receives the same immutable semantic kernel plus typed
violation codes and the minimum necessary correction instruction. It does not
receive a new strategy.

```text
first candidate passes
    -> deliver

first candidate is repairable
    -> regenerate once against the same plan
       +-- pass -> deliver
       +-- fail or indeterminate -> fallback

first candidate hard-fails
    -> do not deliver; fallback immediately
```

Fallbacks are selected by risk and representability:

- reviewed deterministic or slot-filled templates for simple clarification,
  uncertainty disclosure, consent, decline, boundary, and capability messages;
- a minimal realization containing only the primary move when the contract
  explicitly permits that reduction; or
- a typed `no_safe_realization` failure routed to the runtime error policy.

Fallback never selects a new conversational action. Omitting a required move is
allowed only if the original contract marks it dispensable under fallback.
High-consequence commitments, consent, privacy, medical, legal, financial, and
external-action claims should prefer templates or stronger verification until
the learned realizer has sufficient reviewed evidence.

##### Realization trace and learning boundary

The immutable realization trace records:

```text
RealizationSpec and compiler version
generation model and protocol versions
candidate outputs and generation attempt numbers
deterministic check results
semantic verifier inputs, outputs, confidence, and versions
typed repair instructions
selected delivered output or fallback
final fidelity status
```

Later user outcomes join to this trace without rewriting it. A faithful message
with a poor outcome is evidence about action choice, ordering, interpretation,
or circumstances rather than automatically a realization failure. An unfaithful
message must not be used as clean evidence that the selected action plan itself
was poor.

##### V0 acceptance criteria

Before live use, the realization layer must demonstrate on held-out reviewed
cases:

- required-move and required-content coverage;
- detection of added, omitted, and force-changed actions;
- unsupported-claim and invented-entity detection;
- epistemic and commitment-strength preservation;
- capability, consent, and forbidden-claim regression safety;
- correct separation of generation failure from planning failure;
- reproducible fallback behavior; and
- trace completeness across pass, retry, and fallback paths.

Exact promotion thresholds remain part of the evaluation-and-promotion design.
V0 is not accepted merely because its generated text sounds fluent.

#### Outcome representation

Action outcomes remain multidimensional rather than immediately collapsing into
one reward:

```json
{
  "goal_resolution": null,
  "clarity": null,
  "explicit_user_correction": null,
  "engagement": null,
  "social_alignment": null,
  "unnecessary_repetition": null,
  "confusion": null,
  "realization_fidelity": null
}
```

Every value retains its evidence and provenance and may remain unknown. Continued
conversation or silence is not success. A later policy may learn a value vector
or context-dependent tradeoff across these dimensions; core identity may shape
bounded preferences among acceptable outcomes, while safety remains outside the
reward tradeoff.

The accepted V0.1 logical design for immutable channel observations, voice/ASR
provenance, assistant-delivery exposure, outcome episodes, derived evidence,
corrections, and reflection-worker readiness is recorded in
[`data/OUTCOME_OBSERVATION_SCHEMA_V0.md`](data/OUTCOME_OBSERVATION_SCHEMA_V0.md).
It deliberately separates response capture from outcome inference and worker
scheduling. Acceptance approves the logical direction, not database tables or
runtime behavior; representative design cases must validate the rules first.

#### Controlled action expansion

Action expansion has three levels, evaluated in this order:

1. **Parameterization:** express the behavior using new parameter values for an
   existing approved action.
2. **Composition:** express it as a new ordered plan of existing approved moves.
3. **Definition discovery:** propose a new action only when it has a distinct
   conversational effect, eligibility boundary, risk profile, or outcome target
   that the existing registry cannot represent.

Candidate discovery happens during slow reflection or offline review, not by
mutating the production ontology during a live turn. Proposal triggers include
repeated action failure, recurring useful out-of-plan realization behavior,
repeated human requests for a missing strategy, stable repeated move sequences,
or cases where no existing approved action can pursue the selected goal.

An LLM may propose a candidate action containing its parent, definition,
parameters, intended effects, structural requirements, hard inhibitors, examples,
counterexamples, and distinction from existing actions. A deterministic action
compiler then checks schema validity, registry collisions, representability by
existing parameters or composition, effect observability, and safety. The
candidate lifecycle is:

```text
candidate -> reviewed -> shadow -> approved -> deprecated -> retired
```

Shadow actions may be scored and logged but have zero influence on the realized
response. Promotion requires reviewed examples and counterexamples, acceptable
false-eligibility behavior, outcome evidence, and regression checks against
existing actions.

#### Three separate learning problems

The system must not conflate:

```text
repertoire learning: what distinct conversational actions exist?
policy learning:     when should each approved action be selected?
realization learning: how should the selected action be expressed?
```

Outcome history normally updates contextual policy statistics for an approved
action, not the action definition itself. For example, feedback may reduce the
affinity of humor in serious correction contexts without creating a new joke
action or changing what `social_play.joke` means.

The longer-term proposal remains:

- versioned action definitions with preconditions, hard inhibitors, effects,
  risk, origin, lifecycle status, and parameters;
- context policies that learn an action's affinity, expected reward, uncertainty,
  and evidence count for a situation, core identity, and optionally a user;
- action evaluations that preserve predicted and observed outcomes plus a
  multi-dimensional reward vector; and
- a guarded lifecycle for newly proposed actions: candidate, shadow, approved,
  deprecated, and retired.

Safety and authorization are hard gates, not learnable reward tradeoffs. A
contextual bandit may later serve narrow single-move decisions, but ordered plans
require a sequential or slate policy conditioned on previously selected moves.
Delayed multi-turn consequences may justify offline reinforcement learning only
after reliable reviewed trajectories exist.

The exact initial function vocabulary, sequential policy representation,
teacher/reviewer protocol, outcome evidence rules, reward/value learning,
promotion thresholds, appraisal/state inputs, and action-design fixture remain
open for the next review. The manual authoring and validation contract for that
fixture is in `data/CONVERSATIONAL_ACTION_DESIGN_CASES_README.md`; its delegated
output is `data/conversational_action_design_cases.json`.

### What is implemented

- The diagnostic rules and single-call LLM cognitive pipelines.
- Deterministic Perception V0 with immutable evidence-linked observations,
  versioned active/shadow cue lifecycles, and an active-only interpretation
  handoff.
- Deterministic Interpretation V0 with a shared canonical envelope, bounded
  semantic grounding, multidimensional ordinal hypotheses, explicit
  unknown/contradiction/out-of-scope records, bounded bias adjustment, and
  retained scenarios. Its acceptance-hardened shadow path is connected and
  tested; live cognitive routing remains unimplemented.
- Deterministic appraisal and dynamic-state engines with shared canonical
  appraisal, aggregate, and semantic-frame contracts.
- A shadow-only `CognitiveTurnRunner` that connects an existing interpretation
  through real appraisal and dynamic-state update, retaining an immutable,
  versioned, JSON-round-trippable trace with stage timings and typed failures.
- Acceptance-hardened `CognitivePipelineV0` orchestration that derives all
  stage runtime views from one trusted snapshot and connects exact Perception,
  Interpretation, Cognitive Turn, Appraisal, and Dynamic State outputs into one
  validated shadow trace. It is not connected to live chat.
- Persistent working, episodic, semantic, learning, and core memory schemas.
- Migration of the former long-term memory store to semantic memory.
- A conservative, versioned Columbina core identity with automatic core updates
  disabled.
- Provenance-aware storage fields for later reflection and learning.

### What remains to be designed or implemented

- agent/cognitive mode routing and integration with live chat;
- the deterministic personality runtime mapper;
- Perception V0 fixture review, calibration, attention policy, learned-model
  evaluation, and learning jobs;
- Interpretation V0 fixture review, calibration, and future learning jobs;
- dynamic-state persistence, calibration, decay scheduling, and action integration;
- Goal V0 formation/adoption policy, engine, fixtures, and integration; canonical
  contract ownership, ontology, and lifecycle are specified below but remain
  unimplemented;
- the action tables, candidate lifecycle, policy, and evaluator;
- the metacognitive controller and slow-reasoning procedure;
- the post-turn reflection annotator and evidence validator;
- consolidation, training, evaluation, and shadow-deployment jobs; and
- language realization from a selected action and bounded style controls.

The perception-to-interpretation contract, initial hypothesis ontology, bounded
personality influence, and deterministic V0 runtime are specified below and are
implemented as an isolated shadow path. Their design fixtures remain pending
human review, and the learned-model direction remains provisional.

## Connected cognitive trace design

This section describes the complete target trace. Its forward shadow portion is
implemented through Perception V0, Interpretation V0, Appraisal, Dynamic State,
and `CognitiveTurnRunner`; live orchestration, actions, realization, outcomes,
reflection, learning, and durable trace persistence remain design work. No
cognitive component should be implemented as an isolated table or model. Every
result for a conversation turn must ultimately belong to one trace so later
learning can connect what Columbina perceived, what she thought it meant, what
she did, and what happened afterward.

### End-to-end data flow

```text
conversation turn
       |
       v
runtime context + retrieved memories + compiled personality
       |
       v
perception event
  observations + cue activations
       |
       v
interpretation event
  competing hypotheses + probabilities
       |
       v
goal update
  candidates + lifecycle transitions + active commitments
       |
       v
appraisal + dynamic-state update
  scenario-relative meaning + bounded motivational state
       |
       v
action decision
  eligible actions + selected action
       |
       v
language realization and/or authorized tool execution
       |
       v
observed outcome + explicit or implicit feedback
       |
       v
learning trace
       |
       v
offline evaluation, consolidation, and versioned model/policy updates
```

Runtime cognition moves forward through this graph. Learning examines the same
trace backward from the outcome to determine which stage most likely needs to
change.

### Root cognitive turn

A future `cognitive_turns` record should be the root of the trace. The exact
database schema remains subject to review, but its identity and relationships
are required:

```text
cognitive_turn
├── runtime-context snapshot and retrieved-memory references
├── core-memory and PersonalityRuntimeMapper versions
├── perception event
│   └── cue activations
├── interpretation event
│   └── interpretation hypotheses
├── goal snapshot
│   ├── ordered lifecycle transitions
│   └── active and focal goal references
├── appraisal result and dynamic-state update
├── action decision
│   └── action candidates
├── response realization or tool execution
├── observed outcomes and feedback
└── learning records and reviewed labels
```

The trace should reference the input and response messages, conversation,
working memory, runtime mode, fast/slow processing path, timestamps, and latency.
It should not duplicate the authoritative message contents unnecessarily.

### Component contracts

Each stage has a distinct question and must preserve the boundary:

| Component | Question | Produces | Must not do |
| --- | --- | --- | --- |
| Perception | What signals are observable? | Observations, cue activations, confidence, evidence | Declare an inferred meaning as fact |
| Interpretation | What might the signals mean? | Competing hypotheses, probabilities, supporting and contradicting evidence | Select or execute an action |
| Goal formation | What outcome is Columbina currently pursuing? | Ranked, bounded goals and conflicts | Treat a transient goal as core identity |
| Action selection | What should Columbina do under these hypotheses and goals? | Eligible actions, scores, selected action, inhibitors | Generate arbitrary unregistered behavior |
| Realization | How should the selected action be expressed or executed? | Text or an authorized tool request | Quietly replace the selected action |
| Outcome evaluation | What happened after the action? | Observations, feedback, reward dimensions, uncertainty | Assume silence means success |
| Learning | Which component is supported or contradicted by the outcome? | Reviewed labels and update proposals | Rewrite historical predictions |

Later stages may reference earlier outputs, but earlier stages must not be
silently rewritten to make an outcome look consistent.

## Goal System V0 canonical design

Goal V0 uses a BDI/GOAL-inspired separation between a possible desired state,
an adopted commitment, a current intention, a plan, and an action. It does not
copy an existing cognitive architecture wholesale. The contracts below adapt
that separation to this repository's immutable, versioned, evidence-linked
pipeline.

This section fixes three decisions before goal-formation rules are designed:

1. canonical contract ownership;
2. the V0 goal ontology; and
3. the legal goal lifecycle.

The existing `appraisal.models.ActiveGoal` is a migration placeholder, not the
future authority. It currently permits unconstrained owner, type, priority, and
status strings plus an arbitrary `desired_state` mapping. Implementation of
Goal V0 must replace that authority rather than add another competing goal
model.

### Canonical contract ownership

`system/contracts/goals.py` must be the only owner of goal contracts shared by
multiple cognitive stages. It must contain the following public contracts and
enums:

```text
GoalDefinition
GoalDefinitionRef
GoalRequirement
GoalRecord
GoalTransition
GoalSnapshot
ActiveGoalView

GoalOwner
GoalDomain
GoalMode
GoalScope
GoalPriorityBand
GoalStatus
GoalTransitionKind
GoalRequirementStatus
GoalAdoptionBasis
```

The dependency direction is:

```text
contracts/goals.py
        ^
     goal_v0
        ^
cognitive_pipeline_v0
        |
        +----> appraisal
        +----> action selection
        +----> trace and persistence
```

The shared contract module must import no goal engine and no downstream stage.
`goal_v0` produces instances of the canonical contracts but does not redefine
them. Appraisal consumes `ActiveGoalView`; the cognitive pipeline owns the exact
projection from a validated `GoalSnapshot`; and action selection references
requirements from that same snapshot.

Interpretation speaker-goal hypotheses remain owned by
`contracts.interpretation`. They are evidence about what the speaker may want,
not canonical goals and not commitments made by Columbina. Safety,
authorization, consent, values, personality, and dynamic state also remain
separate contracts. None may be encoded as an ordinary goal merely to influence
priority.

During migration, `appraisal.ActiveGoal` may remain as an exact alias of
`contracts.goals.ActiveGoalView`. It must not be a duplicate class or subclass.
Tests must assert exact class identity, as the Interpretation V0 shared-contract
tests already do.

The implementation package has the following ownership:

```text
system/contracts/goals.py  shared immutable schemas and enums
system/goal_v0/ontology.py approved GoalDefinition registry
system/goal_v0/lifecycle.py legal transition table and validation
system/goal_v0/rules.py    deterministic proposal, adoption, and update rules
system/goal_v0/engine.py   application of rules and next-snapshot production
system/goal_v0/models.py   engine-only request, diagnostics, and failure models
```

### Ontology boundaries

The following terms are not interchangeable:

```text
speaker-goal hypothesis != goal candidate != active goal
goal != intention != plan != action != outcome
```

A **speaker-goal hypothesis** is an Interpretation result such as
`interpretation.goal.obtain_information`. It means that evidence supports a
possible attribution to the speaker. It does not authorize adoption.

A **goal candidate** is a structured proposal referencing an approved goal
definition. It must cite interpretation, trusted runtime, or parent-goal
evidence and the deterministic proposal rule that created it. Unstructured LLM
output alone cannot create a candidate.

An **active goal** is an approved commitment that Columbina is currently
eligible to pursue. Active does not mean selected, highest priority, guaranteed
achievable, planned, or completed.

A **focal goal** is an active goal selected as relevant for a particular
appraisal or action-planning pass. Focus is a per-turn relation, not a lifecycle
state. Appraisal frames continue to reference exactly one focal active goal.

The remaining distinctions are:

```text
goal       declarative desired state
intention  currently selected commitment or focus
plan       proposed sequence for pursuing the intention
action     one selected conversational or tool operation
outcome    evidence about what actually happened
```

Plans record only planned coverage. They cannot change goal lifecycle state or
claim that the desired world state has occurred.

### Goal classification

The free-form `goal_type` field must be replaced by independent typed
dimensions. Conversation-versus-task scope must not be confused with the
semantic kind of goal.

#### Owner

`GoalOwner` is one of:

```text
user
columbina
shared
system
```

Owner means whose interest or desired state the record represents; it does not
identify the executor or grant authority.

- `user` represents a user outcome that Columbina has explicitly adopted for
  support. A merely inferred user desire stays an Interpretation hypothesis.
- `columbina` represents an agent-side conversational objective.
- `shared` requires evidence that both parties committed to the outcome.
  Friendly cooperation or an unacknowledged inference is insufficient.
- `system` is restricted to narrow operational obligations derived from trusted
  runtime state. It is not a way to express safety policy as a tradable goal.

An active user-owned record means Columbina has adopted a commitment to support
that user's outcome. It does not mean the system can speak or act with the
user's authority.

#### Domain

`GoalDomain` is limited in V0 to:

| Domain | Meaning |
| --- | --- |
| `task` | Produce or change an external or task result |
| `epistemic` | Resolve a decision-relevant unknown or contradiction |
| `interaction` | Establish a conversational condition such as answering, repairing understanding, or discharging a response obligation |

Relationship-development and self-development goals are deferred beyond V0.
Dynamic-state regulation is not a goal domain.

#### Mode

`GoalMode` is one of:

| Mode | Meaning |
| --- | --- |
| `achieve` | Make a specified condition true |
| `maintain` | Preserve a condition throughout the declared scope |
| `avoid` | Prevent a specified condition during the declared scope |

Ordinary user commitments such as "do not send this yet" may form an avoidance
goal. Safety prohibitions remain hard constraints even when they resemble an
avoidance goal.

#### Scope

`GoalScope` is one of:

```text
turn
conversation
task
```

Scope states how long the commitment may remain valid. Cross-conversation and
indefinite goals are excluded until memory, consent, and persistence policy are
implemented.

#### Priority

`GoalPriorityBand` is one of `low`, `moderate`, or `high`. Priority is not
confidence, urgency, appraisal intensity, or safety authority. A hard
constraint always overrides a goal regardless of priority. The goal system,
not appraisal, assigns and revises the band.

### Definitions and requirements

Goal semantics come from a small, reviewed, versioned registry. Runtime code
must not invent definition keys. A definition has this conceptual shape:

```text
GoalDefinition
  definition_key
  definition_version
  domain
  mode
  allowed_owners[]
  allowed_scopes[]
  requirement_schema
  satisfaction_policy_key
```

The initial registry should be bounded to definitions such as:

```text
interaction.discharge_response_obligation
interaction.answer_information_request
interaction.repair_shared_understanding
epistemic.resolve_required_unknown
epistemic.resolve_material_contradiction
task.complete_authorized_request
task.explain_unavailable_or_failed_operation
```

An implementation brief must review the exact initial registry and examples
before coding it. These names establish the intended granularity but do not
authorize silently adding new runtime definitions.

The arbitrary `desired_state` mapping is replaced by typed
`GoalRequirement` records:

```text
requirement_id
requirement_key
target_refs[]
parameters
satisfaction_policy_key
```

The referenced definition validates every requirement key, parameter, and
target role. Example requirement meanings include:

```text
supported_answer_provided(topic-ref)
required_argument_resolved(argument-ref)
authorized_task_completed(task-ref)
misunderstanding_repaired(proposition-ref)
response_obligation_discharged(turn-ref)
```

This gives action selection an explicit, auditable connection:

```text
registered action effect
        |
        v
matching unresolved GoalRequirement
        |
        v
planned_goal_coverage
```

During migration, `ActiveGoalView` may expose a derived `desired_state`
projection for existing appraisal rules. That projection is compatibility data,
not a second source of truth, and must be removed once appraisal consumes typed
requirements.

### Goal lifecycle

`GoalStatus` is limited to:

```text
candidate
active
suspended

satisfied
failed
abandoned
superseded
rejected
expired
```

The final six statuses are terminal. There is no separate `adopted` status:
adoption is the transition from `candidate` to `active`.

The only legal V0 transitions are:

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

All other transitions are invalid in V0. Standing trusted obligations that are
created and activated during one update still emit ordered `propose` and
`adopt` transitions; they do not bypass the lifecycle.

The statuses mean:

- `candidate`: proposed but not accepted as Columbina's commitment;
- `active`: adopted and eligible for appraisal, focus, and planning;
- `suspended`: retained but temporarily ineligible for pursuit;
- `satisfied`: verified evidence establishes every required terminal
  condition;
- `failed`: reliable evidence establishes that the goal cannot be achieved
  within its declared scope;
- `abandoned`: pursuit was deliberately stopped without satisfaction or proven
  impossibility;
- `superseded`: an identified replacement goal now expresses the commitment;
- `rejected`: a candidate was evaluated and not adopted; and
- `expired`: its scope ended without another terminal result.

`blocked` is not a lifecycle status. A temporary blocker either suspends the
goal or appears as an unresolved capability or requirement condition. A failed
action also does not fail its goal; another plan may remain possible.

### Transition evidence and invariants

Every `GoalTransition` contains:

```text
transition_id
goal_id
sequence
from_status
to_status
transition_kind
rule_id
cognitive_turn_id
source_refs[]
evidence_refs[]
outcome_refs[]
replacement_goal_ref or null
reason_code
```

Lifecycle state is reconstructed from ordered transitions. Runtime code must
not silently replace a record's status. These invariants apply:

1. Goal identifiers never change.
2. Definition key and version never change after proposal.
3. A material desired-state change creates a replacement goal and supersedes
   the old goal.
4. Terminal goals cannot reopen in V0. A later recurrence creates a new goal
   with provenance linking it to the earlier record.
5. Conflicting completion evidence produces a disputed requirement assessment,
   not `satisfied`.
6. Appraisal cannot perform lifecycle transitions.
7. Action selection cannot mark a requirement satisfied.
8. A planned response records planned coverage only.
9. Silence, gratitude, sentiment, or continued conversation cannot satisfy a
   goal.
10. Satisfaction uses reviewed outcome-evidence rules, including objective
    finalized runtime facts where they exactly establish the terminal state.
11. Every snapshot references its predecessor and carries its ordered
    transition set.

### Canonical downstream view and snapshot

Only active goals are projected into `ActiveGoalView`:

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

Candidates, suspended goals, and terminal goals are not supplied to appraisal
as focal goals. The complete state remains available through `GoalSnapshot`:

```text
snapshot_id
revision
supersedes_snapshot_id
conversation_ref
records[]
transitions[]
active_goal_refs[]
focal_goal_refs[]
ontology_version
lifecycle_policy_version
```

`focal_goal_refs` are recomputed for each turn. Changing focus does not change
goal lifecycle status.

### Empty-goal behavior

V0 keeps the existing invariant that a response-producing cognitive turn has at
least one active goal. It does not make appraisal, dynamic state, and action
planning nullable merely to represent passive events.

`CognitivePipelineV0` is therefore scoped to response-producing turns. Its
trusted runtime input must contain an explicit required response obligation.
For every accepted invocation, Goal V0 deterministically proposes and adopts:

```text
interaction.discharge_response_obligation
```

This goal is system-owned, turn-scoped, and tied to the exact cognitive turn.
It is satisfied only by evidence that the selected response was validly
realized and delivered. It is not a fabricated user goal, a permanent "help the
user" goal, a personality preference, or a safety rule.

The coordination goal normally coexists with the task, epistemic, or
interaction goals that explain what the response should accomplish. It creates
the obligation to produce a response but does not gain semantic progress merely
because an arbitrary response exists.

Passive observations, background events, and explicit no-response cases must
not invoke this response pipeline. They belong to a future observer/update path
that may update perception, interpretation, outcomes, or memory without running
appraisal and response planning. Passing a no-response obligation to
`CognitivePipelineV0` is an input-validation failure, not an empty successful
turn and not a conversational `STOP` decision.

### Priority, conflicts, and bounded focus

Goal V0 uses ordinal priority bands, explicit conflict records, and bounded
focus. It does not calculate an opaque global utility for goals.

Priority is assigned in this order:

1. Load the versioned definition's default `GoalPriorityBand`.
2. Apply deterministic policy rules supported by current evidence.
3. Emit a `GoalPriorityChange` whenever the band differs from the previous
   snapshot, preserving old band, new band, rule, and evidence.
4. Select focus only after every active goal has its final band for the turn.

Allowed V0 priority evidence is limited to:

- a current explicit user request or correction linked to the goal;
- an accepted shared commitment;
- a trusted required response obligation;
- a task deadline or urgency fact supplied by trusted runtime;
- an unresolved requirement that blocks every plan for a higher goal;
- an explicit deferral, withdrawal, or resumption; and
- a parent goal whose active requirement created the subgoal.

Appraisal intensity, current emotion, personality traits, action scores, and LLM
preference text cannot assign or change goal priority. The previous dynamic
state may affect a reviewed interruption or attention rule, but it cannot
rewrite ownership, adoption evidence, priority, or terminal truth. Personality
influences bounded action affinity only after goal eligibility and focus are
fixed.

Standing interaction goals remain active when compatible, but a current
explicit task goal takes focus over a lower-priority standing goal. The
turn-scoped response obligation is a coordination floor: it requires a response
but does not by itself outrank the semantic goal the response exists to serve.

Every pair of active goals is classified as one of:

```text
compatible
resource_competition
mutually_exclusive
unknown
```

The classification emits a `GoalConflict` with both goal references, rule and
evidence references, materiality, reversibility, and resolution. Conflict
handling follows this order:

1. Apply safety, authorization, consent, and capability gates outside goal
   ranking. A prohibited goal is suspended or rejected with the gate evidence;
   it is never merely assigned a low score.
2. Honor an explicit withdrawal or correction as superseding the earlier
   incompatible commitment when identity and scope are grounded.
3. Prefer the higher priority band.
4. At the same band, prefer the more specific applicable scope:
   `turn`, then `task`, then `conversation`.
5. At the same scope, preserve the older adopted commitment to prevent
   oscillation, unless newer evidence explicitly corrects or supersedes it.
6. If the remaining choice is material, irreversible, or changes whose
   interests are served, do not use a lexical tie-break. Emit
   `unresolved_material_conflict` and route to clarification or slow cognition.
7. Use stable ascending goal identity only to order equally safe, reversible,
   semantically equivalent goals. Record when this reproducibility rule is
   used; it is not preference evidence.

Owner alone is not a universal priority hierarchy. In particular, a
Columbina-owned style or rapport objective cannot override an explicit adopted
user task, but a user-owned goal cannot override truthfulness, authorization,
consent, or safety because those are gates rather than Columbina-owned goals.
A shared commitment is not silently discarded; explicit withdrawal,
supersession, or conflict resolution is required.

Focus is bounded to one primary goal and at most two supporting goals per turn.
The primary goal is the highest goal remaining after the rules above.
Supporting goals must be compatible with the primary and have a concrete
requirement that the current response can advance. Other active goals remain in
the snapshot but are non-focal and do not enter appraisal or action scoring for
that turn.

Interruption and resumption are lifecycle operations, not deletion:

- an active goal becomes `suspended` when a higher-priority incompatible goal,
  missing authorization, unavailable prerequisite, or explicit deferral
  temporarily prevents pursuit;
- the transition records the blocker and the exact resumption condition;
- `resume` is legal only when that condition has verified evidence; and
- a suspended goal retains its original adoption time and therefore does not
  gain artificial precedence by resuming.

Priority changes and focus changes are audited but are not lifecycle status
changes. `GoalPriorityChange` and `GoalFocusDecision` are separate canonical
records rather than fake `active -> active` lifecycle transitions.

### Persistence and scope semantics

Every non-terminal record has exactly one scope and non-empty `scope_ref`:

| Scope | Required reference | Persistence and closure |
| --- | --- | --- |
| `turn` | `cognitive_turn_id` | Exists for one response episode; closes after realization/outcome reconciliation |
| `conversation` | `conversation_ref` | May survive multiple turns in that conversation; closes on explicit completion, abandonment, supersession, or trusted conversation closure |
| `task` | trusted `task_ref` | May survive multiple turns while the referenced task and conversation remain valid; closes on task terminal evidence or containing-conversation closure |

V0 has no cross-conversation or durable scope. A task-scoped goal does not cross
a conversation boundary merely because the same topic later appears. At trusted
conversation closure, unresolved conversation- and task-scoped goals become
`expired` unless verified evidence supports another terminal transition.

Goals are persisted as immutable snapshots and events, not mutable rows whose
history is overwritten. Loading a new response turn requires the exact previous
`GoalSnapshot` for the conversation. The next revision contains the complete
current view plus only the new ordered decisions and transitions.

Scope ending is not success. An unresolved goal becomes `expired`, while a
goal with explicit withdrawal becomes `abandoned`. `failed` is reserved for
verified impossibility within the declared scope.

A current turn's pre-action goal snapshot may remain active until realization
or outcome evidence exists. Post-action outcome reconciliation creates a later
goal-snapshot revision; it never rewrites the pre-action snapshot embedded in
the cognitive trace. That reconciliation may occur after realization in the
same external turn or at the start of the next response turn.

### Requirement assessment and satisfaction evidence

Each goal definition names a reviewed satisfaction policy. Each requirement is
assessed independently as:

```text
open
satisfied
impossible
disputed
```

Partial progress is represented by separate satisfied subrequirements. V0 does
not invent a continuous completion percentage. A goal becomes `satisfied` only
when every required requirement is satisfied. Any disputed required condition
blocks terminal satisfaction. An impossible required condition permits `failed`
only when no approved alternative requirement or plan can still achieve the
goal.

Goal satisfaction may consume only target-linked, allowlisted evidence:

| Evidence kind | Valid use |
| --- | --- |
| `realization_validation` | The selected information or conversational effect was actually present in the delivered response |
| `semantic_grounding` | A required argument was explicitly supplied and grounded to the correct goal requirement |
| `explicit_user_confirmation` | The user explicitly confirmed the linked subjective or conversational outcome |
| `finalized_tool_result` | An authorized tool produced the exact required terminal result |
| `trusted_runtime_fact` | Trusted state directly establishes the required condition or scope closure |
| `reviewed_outcome` | A reviewed outcome rule establishes the condition from valid evidence |

Examples are definition-specific:

- `supported_answer_provided` can be satisfied by realization validation that
  the supported answer was delivered; it does not claim that the user
  understood it.
- `misunderstanding_repaired` requires explicit confirmation, a validated
  correct paraphrase, or another reviewed repair outcome; sending an
  explanation alone records planned or realized repair effort.
- `required_argument_resolved` requires grounded user or trusted-runtime data,
  not a guessed value.
- `authorized_task_completed` requires a finalized successful tool/runtime
  result matching the target, parameters, authorization, and task reference.
- `response_obligation_discharged` requires successful realization and delivery
  of the selected response.

The following are never satisfaction evidence by themselves:

```text
action selection
planned goal coverage
an assistant claim of success
positive appraisal or dynamic state
positive sentiment
politeness or gratitude
continued use
silence or session closure
an unfinalized tool event
```

Explicit user confirmation may establish a subjective outcome but does not
override a contradictory objective result. Comparable opposing evidence makes
the requirement `disputed`.

For `maintain` and `avoid` goals, absence of a known violation is not enough.
Satisfaction requires trusted scope closure plus adequate monitoring evidence
for the declared scope. Incomplete monitoring produces `expired`, not
`satisfied`.

### Goal engine placement and temporal boundary

The response pipeline order is fixed as:

```text
Perception
    -> Interpretation
    -> Goal V0 update
       inputs:
         previous GoalSnapshot
         current InterpretationEnvelope
         trusted runtime and response obligation
         previous Dynamic State snapshot
         newly available prior realization/outcome evidence
    -> focal ActiveGoalView projection
    -> Appraisal
    -> new Dynamic State
    -> Action selection and realization (future)
```

The goal engine never consumes appraisal or dynamic state produced later in the
same forward pass. The new dynamic state becomes eligible input only for a later
goal update. This prevents:

```text
goal -> appraisal -> dynamic state -> same goal
```

Within a goal update, processing order is deterministic:

1. validate ownership, versions, predecessor revision, and bounded inputs;
2. reconcile new evidence against existing requirements;
3. apply satisfaction, failure, withdrawal, supersession, and scope closure;
4. reevaluate suspension and resumption conditions;
5. propose candidates from current interpretation and trusted runtime;
6. adopt or reject candidates using registered rules;
7. assign or revise priority;
8. classify and resolve conflicts;
9. select bounded focus;
10. emit and revalidate the next immutable snapshot.

`CognitivePipelineRequest.active_goals` must be replaced by
`previous_goal_snapshot` plus the trusted response-obligation input. Callers may
not inject active commitments directly. The pipeline constructs the exact focal
`ActiveGoalView` tuple from the emitted snapshot and preserves object identity
when building the Cognitive Turn request.

### Goal trace, version, and failure contracts

The canonical `GoalSnapshot` contains at least:

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
transitions[]
priority_changes[]
conflicts[]
focus_decision
active_goal_refs[]
focal_goal_refs[]
versions
```

`versions` contains the goal-contract schema, ontology registry, lifecycle
policy, formation/adoption rules, priority/conflict policy, satisfaction policy,
and engine versions. Every snapshot increments the predecessor revision by one
and names the predecessor snapshot exactly. The initial snapshot uses revision
one and a null predecessor. A snapshot belongs to one conversation and one
update turn; records retain their own original creation turn and scope.

`GoalTransition` retains the fields defined above and additionally cites the
previous snapshot and resulting snapshot. Requirement assessments, priority
changes, conflicts, and focus decisions are immutable typed records with their
own identifiers, rules, and evidence. They are not placed in an untyped
diagnostics mapping.

`CognitivePipelineTrace` must add the exact emitted `GoalSnapshot` between
Interpretation and Cognitive Turn. Its cross-stage validator enforces:

1. turn, conversation, runtime, and interpretation ownership match;
2. snapshot predecessor and revision are valid;
3. active and focal references resolve to unique records with legal statuses;
4. every supplied `ActiveGoalView` is the canonical projection of a focal goal;
5. every Appraisal focal-goal reference is in `focal_goal_refs`;
6. no non-focal or non-active goal reaches Appraisal;
7. Cognitive Turn contains the same goal snapshot reference and projection;
8. pipeline versions contain the exact goal versions; and
9. pipeline timing includes `goal_update_ms` in its checked total.

Goal execution uses a content-safe `GoalExecutionError` that preserves the
original exception as its chained cause. `GoalFailurePhase` is limited to:

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

The outer pipeline adds `goal_update` and `goal_handoff_validation` to its
failure stages and records the nested goal phase. A goal failure is fail-closed:
the previous snapshot remains authoritative, no partial next snapshot is
returned, caller-supplied fallback goals are forbidden, and Appraisal and
Dynamic State do not run. Any user-facing operational fallback occurs outside
the cognitive trace and must not be mislabeled as a successful cognitive turn.

### Remaining Goal V0 design work

Canonical ownership, ontology, lifecycle, empty-turn behavior, priority and
conflict semantics, persistence, satisfaction evidence, engine placement, and
trace/failure boundaries are now decided. Goal V0 is not ready for
implementation until the following details are specified:

The manual fixture-authoring contract for resolving and testing these details
is `data/GOAL_DESIGN_CASES_README.md`. Its delegated output is
`data/goal_design_cases.json`, which must remain draft pending human review.

- exact initial definition and requirement registry;
- the deterministic candidate-formation and adoption rule table for every
  supported interpretation/runtime pattern;
- the exact response-obligation input contract and passive-event routing
  boundary;
- the reviewed conflict-compatibility table for the initial registry;
- concrete Pydantic field shapes and validators matching this design;
- fixtures covering ambiguous, conflicting, empty-goal, partial-progress, and
  terminal-evidence cases; and
- migration and acceptance tests for appraisal, Cognitive Turn, and Cognitive
  Pipeline V0.

## Perception architecture

The deterministic V0 portion of this architecture is implemented in
`system/perception_v0`. The learned V1 portion remains a design proposal. The
repository does **not** contain the proposed fast learned perception classifier,
calibrators, or attention policy; V0 intentionally leaves raw score, calibrated
confidence, and salience unset.

The central boundary is:

```text
perception:     what detectable signals are present?
interpretation: what might those signals mean?
```

`negative wording`, `an explicit correction`, and `a mismatch between positive
words and a negative event` are perceptual cues. `The user is annoyed` and `the
user is being sarcastic` are interpretation hypotheses. A cue definition must
not hide an interpretation inside a perceptual label.

### Recommended V1 runtime

Fast perception should make no generative LLM call. It should use one shared
small text encoder, lightweight multi-label prediction heads, deterministic
detectors, and deterministic post-processing:

```text
current message + bounded recent context + runtime facts
                         |
                         v
        deterministic observation/cue detectors
                         +
             one frozen text encoder pass
                         |
                         v
            multi-label cue prediction heads
                         |
                         v
              per-cue confidence calibration
                         |
                         v
       evidence validation + attention/salience policy
                         |
                         v
                  perception event
```

"One head per cue" is a logical training distinction, not a separate model
service or encoder execution. If the encoder produces embedding `e`, all Boolean
cue scores can be computed together:

```text
raw cue scores = sigmoid(W_perception * e + b_perception)
```

Each row of `W_perception` represents one approved learned cue. Categorical or
scalar cues may use small type-specific output rows while sharing the same
encoder and execution. Deterministic cues bypass the neural head and enter the
same validated output contract.

The exact encoder family, artifact, size, context length, and latency target are
not finalized. The leading V1 shape is a frozen sentence-transformer-style
encoder with trainable linear heads. SetFit or a fine-tuned compact encoder is a
future option after enough reviewed labels exist.

### Runtime inputs

Fast perception may receive only bounded, explicitly versioned inputs:

- the current message and its role;
- a bounded recent-message context with roles and ordering;
- deterministic runtime facts such as attachment presence, tool result status,
  elapsed time, speaker change, and conversation mode;
- a working-memory summary containing active topic and unresolved references;
- the approved cue manifest and detector versions; and
- mapped attention modifiers from identity and current goals.

Raw core-personality JSON is not an input to the cue detector. Personality must
not change what external evidence says. It enters only through the later
attention/salience calculation. Retrieved autobiographical memories generally
belong to interpretation rather than perception; a memory may affect what a cue
means, but it must not manufacture a cue in the current message.

### Stable perception envelope

The outer schema is fixed and versioned even though registered cues can grow:

```json
{
  "schema_version": 1,
  "cognitive_turn_id": "turn-123",
  "input_message_ids": ["message-123"],
  "observations": [],
  "cue_activations": [],
  "attention": [],
  "overall_uncertainty": 0.31,
  "missing_information": [],
  "versions": {
    "cue_manifest": "cue-manifest-v1",
    "encoder": "encoder-v1",
    "detector": "perception-heads-v1",
    "calibrator": "cue-calibration-v1",
    "attention_policy": "attention-v1"
  }
}
```

- `observations` contains direct properties or runtime events with provenance.
- `cue_activations` contains recognized patterns derived from observations and
  message content.
- `attention` records which cue activations became salient to Columbina.
- `overall_uncertainty` summarizes coverage, detector uncertainty, disagreement,
  and out-of-distribution signals; it is not a simple average.
- `missing_information` records evidence that could materially change perception
  or downstream interpretation.
- `versions` makes the result reproducible and auditable.

### Cue-definition contract

Every cue belongs to a versioned registry. A future cue definition needs at
least the following logical fields; this does not yet approve a database table:

| Field | Meaning |
| --- | --- |
| `cue_key` | Stable namespaced identity, such as `conversation.explicit_correction` |
| `definition_version` | Immutable version of the operational meaning |
| `name` and `description` | Human-readable name and testable definition |
| `category` | Linguistic, conversation, social, task, environment, or runtime |
| `value_type` | Boolean, scalar, integer, category, or distribution |
| `value_constraints` | Range, allowed categories, units, and null rules |
| `detector_type` | Deterministic, learned, composite, or runtime-supplied |
| `detector_reference` | Versioned rule or model-head identifier |
| `evidence_requirement` | Exact span, message reference, runtime event, or whole-message scope |
| `parent_cue_key` | Optional approved fallback category |
| `conflicts_with` | Cues that require conflict reporting or validation |
| `base_importance` | Personality-independent starting attention weight |
| `origin` | Human, system, or learned proposal |
| `status` | Candidate, shadow, approved, deprecated, or retired |

Definitions are immutable by version. Changing meaning, range, or evidence
requirements creates a new version rather than silently changing historical
data.

### Cue value types

The activation format is a tagged union. `value_type` comes from the definition,
and exactly one matching value is present:

```json
{
  "cue_key": "conversation.explicit_correction",
  "definition_version": 1,
  "value_type": "boolean",
  "value": true,
  "raw_score": 0.94,
  "confidence": 0.91,
  "salience": 0.83,
  "evidence": [],
  "detector_source": "learned",
  "activation_status": "predicted"
}
```

Supported primitive meanings are:

| Type | Example | Learning target |
| --- | --- | --- |
| Boolean | Explicit correction is present | Present, absent, or unknown |
| Scalar | Urgency intensity in `[0, 1]` | Reviewed continuous value or interval |
| Integer | Repetition count | Reviewed count |
| Category | Response form is `question` | One approved category or unknown |
| Distribution | Language detection over several languages | Reviewed target distribution when available |

`unknown` is not the same as `false` or zero. Missing and unreviewed labels must
not be converted into negative training examples.

### Value, raw score, confidence, and salience

These fields must stay separate:

- `value` describes the detected property according to its declared type.
- `raw_score` is the uncalibrated detector output, when a learned detector exists.
- `confidence` estimates the reliability of the stored value after calibration
  and out-of-distribution checks.
- `salience` describes how much attention Columbina should give the activation
  in the current cognitive state.

For a Boolean cue, `value: true` with `confidence: 0.70` means the system selects
"present" but estimates only 70% reliability. For a scalar cue, `value: 0.80`
with `confidence: 0.55` means the estimated magnitude is high but uncertain.

The learned detector supplies a raw score, not authoritative confidence.
Calibration should initially use held-out reviewed data with temperature or
Platt scaling, and may later use isotonic calibration, ensembles, or explicit
out-of-distribution detection. Calibrators are small parameterized artifacts,
not additional language models.

Salience is initially deterministic:

```text
salience = clamp(
    base cue importance
  * current-goal relevance
  * risk relevance
  * novelty modifier
  * unresolved-question relevance
  * compiled personality attention modifier,
  0,
  1
)
```

The exact combination rule and coefficients are still open. It must be versioned,
bounded, and inspectable. The same detected cue and confidence may receive
different salience in casual conversation and a safety-critical discussion.

### Evidence and provenance

Every activation must point back to its evidence. Depending on the cue, evidence
may be an exact character span, a whole-message reference, a sequence of message
ids, or a runtime event. Model attention weights are not acceptable evidence.

```json
{
  "message_id": "message-123",
  "start": 0,
  "end": 18,
  "quoted_text": "Great, another bug",
  "evidence_type": "text_span"
}
```

V1 should prefer deterministic spans and source references. A dedicated span
extractor is optional future work, not another required fast-path model. If a
semantic cue cannot be localized honestly, its evidence scope should be the
whole message or specified context window rather than a fabricated exact span.

### Initial cue ontology V1

The following small ontology is accepted as the first design version. Inclusion
in the ontology does not mean every cue can influence live behavior immediately.
Each cue has a runtime status based on the evidence currently available:

- `v0_active`: deterministic and allowed to feed interpretation in V0;
- `shadow`: detected or annotated for evaluation but has zero behavioral effect;
- `defined`: its contract exists, but no production detector is approved.

| Cue key | Type | Initial status | Operational meaning |
| --- | --- | --- | --- |
| `conversation.question_form` | Boolean | `v0_active` | Current message contains an explicit grammatical or punctuated question |
| `conversation.explicit_action_request` | Boolean | `v0_active` | Speaker directly asks Columbina to perform an action; factual information questions are excluded |
| `conversation.explicit_correction` | Boolean | `v0_active` | Speaker explicitly corrects a prior claim, understanding, or action |
| `conversation.repetition` | Integer | `v0_active` | Number of prior materially matching occurrences in the supplied bounded context; first occurrence is `0` |
| `conversation.topic_shift` | Boolean | `shadow` | Current subject differs materially from the active conversational topic |
| `linguistic.negation` | Boolean | `v0_active` | Text contains a scoped negation marker such as `not`, `never`, or `didn't` |
| `linguistic.uncertainty_marker` | Boolean | `v0_active` | Speaker uses explicit uncertainty language such as `maybe`, `I think`, or `not sure` |
| `linguistic.positive_wording` | Boolean | `shadow` | Lexical wording contains a positive evaluation or affect marker, independent of inferred intent |
| `linguistic.negative_wording` | Boolean | `shadow` | Lexical wording contains a negative evaluation or affect marker, independent of inferred intent |
| `linguistic.self_reference` | Integer | `v0_active` | Count of first-person references owned by the current speaker, excluding quoted speech owned by another speaker |
| `task.urgency` | Boolean | `shadow` | Explicit wording indicates time pressure or a need for immediate handling; intensity alone is insufficient |
| `runtime.attachment_present` | Boolean | `v0_active` | Trusted runtime context reports one or more attachments |
| `runtime.tool_failure` | Boolean | `v0_active` | An authorized tool invocation returned a recognized failure state |

The ontology deliberately excludes `sarcasm`, `annoyance`, `hostility`, and
similar meanings. They are interpretation hypotheses, not V1 perceptual facts.
Future observable cues such as tone/situation mismatch or playfulness markers may
support those hypotheses after their definitions and detectors are reviewed.

#### V1 cue-definition requirements

Before a `v0_active` detector is implemented, its definition must include:

- positive examples and counterexamples;
- exact value constraints and missing-value behavior;
- required text spans, message ids, or trusted runtime evidence;
- precedence and conflict rules;
- language limitations;
- detector and definition versions; and
- at least one named downstream consumer or evaluation purpose.

For example, `conversation.explicit_correction` includes `No, I said Tuesday,
not Thursday` but not every message containing `no`. `No thanks` is a refusal,
and `I don't know` is an uncertainty statement unless additional context makes
it a correction.

The V1 Boolean wording cues record marker presence, not emotional magnitude. A
future ontology version may add separately named and calibrated strength cues.
Quoted first-person language does not count as current-speaker self-reference;
when quote ownership cannot be resolved, the activation is `unknown`.

`task.missing_constraint` and `runtime.capability_unavailable` are deliberately
excluded from perception. Both require an interpreted task/capability target.
After interpretation identifies that target, goal/action planning compares it
with a trusted task contract and capability set and records derived situation or
action-eligibility facts. These facts may trigger clarification or inhibit an
action, but they must not be presented as raw perceptual cues.

`conversation.topic_shift`, affective wording, and urgency start in shadow
because their context behavior requires reviewed examples.

### Manual cue-design case protocol

Before implementing Perception V0, create a manually reviewed design fixture at:

```text
backend/app/ai/Cognitive/system/data/perception_design_cases.json
```

This fixture validates the ontology contract; it does not measure a model. The
author must not implement detectors, run an LLM to decide expected answers, add
database tables, or change production code as part of this task. An LLM may help
draft natural wording, but a human-readable rationale and the ontology definition
must determine the expected result.

#### File-level schema

Use one reviewable JSON document rather than JSONL:

```json
{
  "schema_version": 1,
  "ontology_version": 1,
  "status": "draft_for_human_review",
  "description": "Manual cases for validating Perception Ontology V1",
  "cases": []
}
```

The file is design data, not approved training data. Its status must remain
`draft_for_human_review` until a human explicitly reviews it. It must not be read
by a production runtime or training job.

#### Case schema

Each case must be self-contained:

```json
{
  "case_id": "explicit-correction-minimal-pair-a",
  "title": "Explicit correction of a date",
  "purpose": "Distinguish a correction from an ordinary use of no",
  "source": "synthetic_manual",
  "messages": [
    {
      "message_id": "m1",
      "role": "assistant",
      "content": "Your appointment is Thursday."
    },
    {
      "message_id": "m2",
      "role": "user",
      "content": "No, I said Tuesday, not Thursday."
    }
  ],
  "focus_message_id": "m2",
  "runtime_context": {
    "attachments": [],
    "available_capabilities": [],
    "last_tool_event": null
  },
  "cue_expectations": [
    {
      "cue_key": "conversation.explicit_correction",
      "expected_state": "present",
      "expected_value": true,
      "evidence": [
        {
          "evidence_type": "text_span",
          "message_id": "m2",
          "start": 0,
          "end": 33,
          "quoted_text": "No, I said Tuesday, not Thursday."
        }
      ],
      "rationale": "The user directly replaces Thursday with Tuesday."
    },
    {
      "cue_key": "conversation.question_form",
      "expected_state": "absent",
      "expected_value": false,
      "evidence": [],
      "rationale": "The focus message contains no question."
    }
  ],
  "forbidden_inferences": [
    "The user is angry.",
    "The user distrusts Columbina."
  ],
  "interpretation_handoff": {
    "information_available": [
      "The user is correcting previously stated information."
    ],
    "information_missing": [
      "The user's emotional state cannot be established from this message."
    ],
    "candidate_hypotheses_for_later_review": [
      "correcting_previous_information"
    ]
  },
  "routing_expectation": {
    "path": "fast",
    "rationale": "The correction is explicit and low risk."
  },
  "post_interpretation_expectations": [],
  "ontology_questions": [],
  "review": {
    "status": "pending",
    "reviewer": null,
    "notes": []
  }
}
```

Text offsets use zero-based Python character indexes and an exclusive `end`.
`quoted_text` must exactly equal `content[start:end]`. Evidence spanning several
messages uses one evidence object per message. Runtime cues use an
`evidence_type` of `runtime_fact` and a standards-based JSON Pointer such as
`/attachments/0` or `/last_tool_event` in a `json_pointer` field. The referenced
`value` must equal the value at that pointer in `runtime_context`.

`post_interpretation_expectations` is optional design scaffolding for derived
facts such as a missing required task parameter or unavailable requested
capability. Each entry must say which interpretation is required before the fact
can be computed. These are not perception cue expectations or approved
interpretation labels.

#### Expected-state semantics

Use only these states:

| State | Meaning |
| --- | --- |
| `present` | The case provides sufficient evidence that the cue is active |
| `absent` | The case provides sufficient evidence that the cue is not active |
| `unknown` | Available evidence cannot resolve the cue |
| `not_applicable` | The cue does not apply to this input or runtime situation |

An ontology cue omitted from a case's `cue_expectations` was not reviewed in that
case; omission never implies `absent`. Present expectations require evidence.
Absent expectations normally have an empty evidence list and a rationale showing
why absence is known rather than merely undetected.

Do not use `absent` merely because a simple rule would fail to find the cue.
Do not assign numeric detector confidence or salience in manual design cases;
neither a calibrated detector nor an AttentionPolicy exists yet. A reviewer may
record qualitative ambiguity in the rationale and use `unknown`.

#### Required case coverage

Create 12–20 cases. Reuse cases across cues where natural, but the complete file
must include for every ontology cue:

- at least one clear positive or applicable case;
- at least one clear negative, counterexample, or not-applicable case;
- explicit evidence and a short rationale; and
- a note when the cue cannot be resolved under its current definition.

Every `v0_active` language cue should have at least one minimal pair: two cases
that differ in one meaningful feature and therefore produce a different cue
state. Required contrasts include:

1. a factual question versus an action request phrased as a question;
2. an explicit correction versus a refusal such as `No thanks`;
3. first mention versus repetition after an unanswered request;
4. explicit uncertainty versus a confident statement;
5. negation versus a superficially similar word sequence with different scope;
6. a real attachment versus text that merely says an attachment exists;
7. a trusted tool failure versus a user only describing a hypothetical failure;
8. a capability truly unavailable versus a user assuming it is unavailable,
   represented as a post-interpretation/action-eligibility distinction;
9. positive/negative wording used literally versus a possible sarcastic mismatch;
10. a clear topic shift versus elaboration within the existing topic;
11. explicit urgency versus emotional intensity without time pressure; and
12. a task with a declared required parameter missing versus a task with no such
    parameter contract.

Also include at least one case from each robustness slice:

- typo or informal spelling;
- quoted speech where the speaker does not own the quoted cue;
- hypothetical or fictional language;
- mixed positive and negative wording;
- multi-turn reference resolution;
- empty or extremely short input;
- more than one simultaneously valid cue; and
- ambiguity that should remain `unknown` and route to slow cognition or
  clarification.

#### Ontology-boundary rules

The case author must enforce these boundaries:

- Perception records wording, structure, repetition, and trusted runtime facts;
  it does not declare sarcasm, annoyance, hostility, deception, or intent as fact.
- A cue may appear as `present` in `cue_expectations` only if its current
  definition and evidence requirements justify it.
- `forbidden_inferences` should name tempting interpretations that perception
  must not emit.
- `candidate_hypotheses_for_later_review` is qualitative scaffolding for the
  future interpretation schema, not an approved label or probability target.
- `routing_expectation` is a design expectation only. Use `fast`, `slow`,
  `clarify`, or `abstain_or_escalate`; explain why uncertainty, novelty,
  contradiction, or risk requires a non-fast path.
- Do not change an ontology definition silently to make a case fit. Record the
  problem in `ontology_questions` with a proposed clarification.

#### Review procedure

Review the finished file in this order:

1. Validate JSON syntax, unique case ids, unique message ids within each case,
   known cue keys, legal expected states, and exact evidence spans.
2. Build a coverage summary showing positive, negative/not-applicable, and
   unknown counts for every cue.
3. Examine minimal pairs side by side and confirm that only the intended cue
   distinction changes.
4. Check every `forbidden_inferences` entry against the cue expectations to
   ensure no interpretation was smuggled into perception.
5. List all non-empty `ontology_questions`; do not resolve them by silently
   editing expected results.
6. Walk at least three cases through the qualitative interpretation handoff and
   routing expectation to ensure the cues have a downstream use.
7. Leave all review statuses pending and present the coverage summary and open
   questions for human approval.

#### Completion criteria for the delegated task

The delegated case-authoring task is complete only when it provides:

- `perception_design_cases.json` with 12–20 valid, natural cases;
- coverage of all thirteen V1 cues and all required contrast/robustness slices;
- exact evidence for every expected present activation;
- no numeric confidence, salience, interpretation probability, or model output;
- a coverage table in the task report;
- a list of ontology ambiguities, missing definitions, and suggested changes;
- confirmation that no production code, database schema, or training artifact was
  changed; and
- confirmation that the fixture remains pending human review and is not treated
  as training ground truth.

### Example V1 perception

For `Great, another bug. Exactly what I needed.`, a valid result might be:

```json
{
  "cue_activations": [
    {
      "cue_key": "linguistic.positive_wording",
      "value_type": "boolean",
      "value": true,
      "raw_score": 0.98,
      "confidence": 0.96,
      "salience": 0.55,
      "evidence": [{"text": "Great", "start": 0, "end": 5}]
    },
    {
      "cue_key": "task.negative_event",
      "value_type": "boolean",
      "value": true,
      "raw_score": 0.95,
      "confidence": 0.93,
      "salience": 0.78,
      "evidence": [{"text": "another bug", "start": 7, "end": 18}]
    },
    {
      "cue_key": "social.tone_situation_mismatch",
      "value_type": "boolean",
      "value": true,
      "raw_score": 0.80,
      "confidence": 0.71,
      "salience": 0.88,
      "evidence": [{"message_scope": "message-123"}]
    }
  ]
}
```

This does not assert sarcasm, annoyance, intent, or desired action. Those are
downstream hypotheses.

### Cue expansion lifecycle

New cues can be proposed by a human, offline analysis, or slow/reflection LLM.
An LLM proposal is candidate data, never a production definition:

```text
repeated unexplained pattern
          |
          v
candidate definition with examples and counterexamples
          |
          v
deduplicate against existing cues and parent ontology
          |
          v
collect reviewed positive, negative, and unknown examples
          |
          v
train detector head and calibrator
          |
          v
shadow detection with zero behavioral influence
          |
          v
evaluate detectability, calibration, utility, and failure modes
          |
          v
approve, revise, merge, reject, or retire
```

Approval requires evidence that the cue is distinct, detectable, repeatable,
calibrated, and useful downstream. Approval adds it to a new cue manifest and a
new detector artifact. It does not change an existing model version or grant an
interpretation weight automatically.

During shadow status, activations are stored for evaluation but excluded from
the live interpretation feature set. An unknown approved child cue may fall back
to an explicitly configured parent cue; otherwise it is ignored safely.

### Perception-learning record

Perception trains from reviewed labels, not directly from ordinary transcripts.
A logical training example should contain:

```json
{
  "training_example_id": "example-123",
  "source_cognitive_turn_id": "turn-123",
  "input_message_ids": ["message-123"],
  "context_message_ids": ["message-120", "message-121", "message-122"],
  "input_snapshot_hash": "...",
  "cue_labels": [
    {
      "cue_key": "social.tone_situation_mismatch",
      "definition_version": 1,
      "label_state": "present",
      "target_value": true,
      "label_confidence": 1.0,
      "evidence": [{"message_id": "message-123", "scope": "whole_message"}],
      "provenance": "explicit_human_correction"
    }
  ],
  "review_status": "approved",
  "reviewer_type": "human",
  "created_at": "..."
}
```

Required label states are `present`, `absent`, `unknown`, `disputed`, and
`not_applicable`. Training rules are:

- `present` and `absent` may become supervised Boolean targets.
- `unknown` and `not_applicable` are masked out, not treated as negatives.
- `disputed` is retained for analysis and excluded unless adjudicated.
- Scalar and categorical targets must satisfy the definition's constraints.
- Corrections create new labels; they never overwrite the original prediction.

Provenance should distinguish at least explicit human statement, human review,
later observed evidence, deterministic derivation, AI annotation, and weak
outcome inference. Explicit human correction is strongest. AI annotations may
bootstrap candidate datasets but remain weak labels until reviewed or supported.
Silence and continued conversation are not confirmation.

The training view may resolve message references to authorized text at training
time, but the durable record should prefer provenance and source ids over
unnecessary duplicate transcript storage. Dataset creation must honor deletion,
privacy, and retention policies.

### Perception-learning pipeline

Perception learning is offline and versioned:

```text
immutable runtime predictions
          +
reviewed cue labels with provenance
          |
          v
dataset validation and conflict checks
          |
          v
conversation/user-grouped train, validation, and test splits
          |
          v
train cue heads while encoder is initially frozen
          |
          v
fit confidence calibration on held-out validation data
          |
          v
evaluate per cue, by context slice, and for regressions
          |
          v
shadow deployment
          |
          v
promote or reject version
```

Splits must be grouped so messages from the same conversation, near-duplicate
episode, or user-specific pattern cannot leak across train and test sets. Rare
cues require minimum positive and negative evidence before production training.
Class imbalance must be handled explicitly rather than optimized away by always
predicting absence.

Evaluation should include per-cue precision, recall, F1 or PR-AUC, calibration
error and Brier score, false-positive cost for sensitive cues, evidence validity,
latency, out-of-distribution behavior, and downstream interpretation utility.
Aggregate accuracy alone is insufficient.

## Interpretation design

Interpretation infers what the perceived message may mean, what conversational
move the speaker is making, and what outcome the speaker may be seeking. It does
not re-detect perception cues and it does not select or execute an action.

The accepted boundary is:

```text
PERCEPTION
observable cues, confidence, salience, evidence, and provenance
        |
        v
SEMANTIC GROUNDING
propositions, predicates, arguments, references, polarity, and modality
        |
        v
INTERPRETATION
communicative-act, speaker-goal, modality, and pragmatic hypotheses
        |
        v
APPRAISAL
what each possible meaning implies for Columbina's goals and values
        |
        +--------> METACOGNITIVE ROUTER
        |          ambiguity, conflict, novelty, and consequence
        v
DYNAMIC-STATE UPDATE
bounded changes to valence, arousal, trust, curiosity, and control
        |
        v
ACTION SELECTION
```

Perception cues alone are insufficient because a cue such as
`conversation.explicit_action_request=true` does not identify whether the user
asked to send a report or delete a database. Interpretation therefore consumes:

- the stable perception envelope;
- the focus message and bounded recent context authorized by the trace;
- a semantic frame grounded to source spans;
- trusted runtime facts and post-interpretation task/capability contracts;
- relevant retrieved memories with provenance and retrieval scores;
- the compiled, bounded core-personality bias vector; and
- the current relationship and working state.

Candidate and shadow perception cues contribute exactly zero to live
interpretation. A newly approved cue may receive interpretation mappings only in
a new, separately reviewed feature-manifest version.

### Initial interpretation hypothesis ontology

Interpretation outputs hypothesis keys, not additional perception cue keys. The
initial ontology is intentionally multidimensional because one utterance may
perform more than one communicative function.

#### Communicative-act hypotheses

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

#### Speaker-goal hypotheses

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

`interpretation.goal.unknown` is a valid result. A statement does not justify
inventing a hidden goal merely because a known goal label exists.

#### Modality hypotheses

```text
interpretation.modality.asserted
interpretation.modality.tentative
interpretation.modality.questioned
interpretation.modality.requested
interpretation.modality.hypothetical
interpretation.modality.quoted
```

Modality is multi-label: quoted content may occur inside a hypothetical question.

#### Pragmatic-meaning hypotheses

```text
interpretation.pragmatic.literal
interpretation.pragmatic.indirect
interpretation.pragmatic.tone_situation_mismatch
interpretation.pragmatic.unknown
```

V0 does not contain an accepted `sarcastic` fact or intent. A positive/negative
mismatch may support `tone_situation_mismatch`; slow cognition may compare
sarcastic, playful, sincere, idiomatic, and other explanations without silently
promoting any of them into perception.

Hypotheses are multi-label within compatible dimensions and mutually exclusive
only where their definitions explicitly require it. Every hypothesis record must
include status, support, supporting and contradicting evidence, source cue ids,
model/rule version, and correction history.

### Semantic frame

The message-specific content is an extensible frame rather than an ever-growing
intent ontology:

```json
{
  "predicate": "send_report",
  "arguments": {
    "object": "report",
    "recipient": null
  },
  "polarity": "positive",
  "source_spans": [],
  "unresolved_arguments": ["recipient"]
}
```

After the interpretation resolves `send_report`, action planning may compare its
arguments with the trusted task contract and derive
`action.required_parameter_missing=true`. That derived fact is not a perception
cue or an intention hypothesis.

### Perception-to-interpretation mapping

The initial feature mapping is:

| Perception cue | Interpretation use |
| --- | --- |
| `conversation.question_form` | Supports questioned modality or an information-request act; does not override an explicit action request |
| `conversation.explicit_action_request` | Supports `act.request_action`, `goal.cause_action`, and requested modality |
| `conversation.explicit_correction` | Supports `act.correct` and `goal.repair_shared_understanding` |
| `conversation.repetition` | Supports an unresolved repeated act and increases repair/recovery relevance |
| `conversation.topic_shift` | Opens or proposes a new discourse segment |
| `linguistic.negation` | Changes semantic-frame polarity or scoped arguments; it is not an intention by itself |
| `linguistic.uncertainty_marker` | Supports tentative modality for the speaker's proposition, not model uncertainty |
| `linguistic.positive_wording` | Contributes lexical evidence to evaluation and pragmatic hypotheses |
| `linguistic.negative_wording` | Contributes lexical evidence to evaluation and pragmatic hypotheses |
| `linguistic.self_reference` | Helps resolve semantic roles and possible agency |
| `task.urgency` | Adds a time constraint and contributes to goal relevance/arousal appraisal |
| `runtime.attachment_present` | Grounds attachment arguments and makes attachment-dependent actions eligible |
| `runtime.tool_failure` | Grounds a failure event and supports recovery context |

No single cue normally determines a hidden intention. Rules combine cues,
semantic content, context, and contradictions while preserving alternatives.

For V0, positive and negative wording in the same utterance is sufficient lexical
evidence to retain `interpretation.pragmatic.tone_situation_mismatch`; a matching
runtime event is not required. Wording alone does not prove sarcasm, user affect,
or that the described negative event actually occurred. Those remain separate
hypotheses or unverified content.

The absence of `conversation.explicit_action_request` does not prohibit an
indirect conversational-action hypothesis. When the current and recent context
ground a salient situation and a conventional helpful response, interpretation
may retain `act.request_action`, `goal.cause_action`, or
`pragmatic.indirect` with at most moderate V0 support while preserving the
literal interpretation. This is evidence for choosing a conversational move
such as suggesting, acknowledging, or asking; it is not evidence that an
explicit request occurred and does not itself authorize external tool execution.

### Stable interpretation envelope

The proposed stable outer contract is:

```json
{
  "schema_version": 1,
  "cognitive_turn_id": "turn-123",
  "perception_trace_id": "perception-123",
  "semantic_frames": [],
  "hypotheses": {
    "communicative_acts": [],
    "speaker_goals": [],
    "modalities": [],
    "pragmatic_meanings": []
  },
  "unknowns": [],
  "contradictions": [],
  "out_of_scope": false,
  "appraisals": [],
  "versions": {
    "ontology": "interpretation-ontology-v1",
    "feature_manifest": "interpretation-features-v1",
    "rules_or_model": "interpretation-v0",
    "bias_compiler": "personality-bias-compiler-v1"
  }
}
```

V0 hypothesis support is ordinal: `unsupported`, `weak`, `moderate`, or `strong`.
It must not emit numerical probabilities without calibration. V1+ may emit a
calibrated distribution while retaining the same alternatives and evidence.

### Interpretation design-fixture simplification

The manual interpretation fixture validates ontology meaning and connections; it
is not a production-runtime evaluation. Keep it simple:

- the file has one top-level `evaluation_scope: "ontology_design"`;
- cases may use `shadow` perception cues to test their intended future meaning;
- a shadow cue in this fixture remains zero-influence in the live runtime until
  the separate perception manifest promotes it;
- do not add live/shadow fields to every hypothesis or duplicate every routing
  result;
- `routing_expectation` expresses intended behavior assuming the listed input
  cues are approved and available; runtime eligibility is tested later against a
  particular cue-manifest version; and
- bias scenarios test bounded changes to interpretation support and invariants;
  profile-specific appraisal/state matrices are deferred to the dynamic-state
  design rather than duplicated in every interpretation case.

Each case includes `expected_out_of_scope: true|false`. Appraisal expectations
use exactly three shapes:

```text
goal_relevance, novelty, certainty, controllability
    -> expected_band: low | moderate | high | unknown

goal_congruence, norm_compatibility, affiliative_value
    -> expected_tone: negative | neutral | positive | unknown

agency
    -> expected_category: user | columbina | third_party | environment | unknown
```

`appraisal.certainty` means Columbina's certainty about the interpreted
situation and its implications. A speaker uncertainty marker establishes
`modality.tentative`; it may inform appraisal certainty but must not determine it
automatically or imply that the interpretation model is uncertain.

### Core-memory interpretation biases

Raw core-memory JSON never directly enters a hypothesis formula. A deterministic,
versioned personality compiler maps it into a bounded vector such as:

```json
{
  "benign_prior": 0.65,
  "trust_prior": 0.60,
  "threat_sensitivity": 0.30,
  "confirmation_tendency": 0.20,
  "self_attribution_tendency": 0.40,
  "ambiguity_tolerance": 0.75,
  "social_affiliation_prior": 0.70
}
```

Values use `[0, 1]`, with `0.5` neutral unless a field contract says otherwise.
The fields mean:

| Bias | Permitted influence |
| --- | --- |
| `benign_prior` | Prefer a benign explanation when current evidence genuinely underdetermines pragmatic meaning |
| `trust_prior` | Weakly prefer cooperative/sincere explanations over deception hypotheses |
| `threat_sensitivity` | Increase attention and support for criticism/harm hypotheses when negative evidence exists |
| `confirmation_tendency` | Weakly favor meanings consistent with a trusted existing user model; never suppress corrections |
| `self_attribution_tendency` | Influence appraisal of whether failure is attributable to Columbina, another agent, or circumstances |
| `ambiguity_tolerance` | Preserve alternatives longer and alter slow/clarify pressure rather than forcing an answer |
| `social_affiliation_prior` | Weakly favor bonding, reassurance, or support goals when compatible evidence exists |

The permission boundary is mandatory:

| Interpretation component | Maximum personality influence |
| --- | --- |
| Trusted runtime facts, negation scope, and quotation ownership | None |
| Explicit request/correction and grounded semantic arguments | None, except weak reference-resolution priors |
| Communicative act | Weak and only when indirect or ambiguous |
| Speaker goal | Weak to moderate while alternatives remain visible |
| Pragmatic meaning | Moderate but bounded |
| Appraisal and dynamic-state sensitivity | Stronger, bounded by the state contract |
| Action style | Strong, after action eligibility and safety gates |

The base interpretation is computed before personality adjustment:

```text
base support(h) =
    cue/rule evidence
  + semantic-frame compatibility
  + bounded conversation-context evidence
  + retrieved-memory evidence
  - contradiction penalties

final support(h) =
    base support(h)
  + ambiguity_gate * bounded_personality_adjustment(h)
```

`ambiguity_gate` is zero for explicit or runtime-grounded meanings and increases
only when evidence supports multiple meanings. Bias must never manufacture cue
evidence, remove competing hypotheses, override a current correction, or turn an
explicit negative proposition into a positive one.

Core memory and user/relationship memory remain distinct:

```text
core memory: how Columbina generally weighs ambiguity
relationship memory: evidence about what this user meant in similar situations
```

### Appraisal and dynamic-state connection

The accepted V0.1 appraisal direction is documented in
[`data/APPRAISAL_SCHEMA_V0.md`](data/APPRAISAL_SCHEMA_V0.md). Columbina borrows
Soar's incremental, data-dependent appraisal framing and EMA's continuous
reappraisal of the current interpretation, without adopting either architecture
wholesale. V0 remains deterministic and inspectable.

The implemented V0.1 dynamic-state registry, scoped snapshot and contribution
envelopes, appraisal mapping, decay and personality boundaries, derived control
signals, and model roadmap are documented in
[`data/DYNAMIC_STATE_SCHEMA_V0.md`](data/DYNAMIC_STATE_SCHEMA_V0.md). The
deterministic updater and its reviewed design-case validator are implemented.
The manual authoring and validation contract for those cases is in
[`data/DYNAMIC_STATE_DESIGN_CASES_README.md`](data/DYNAMIC_STATE_DESIGN_CASES_README.md).

The `cognitive_turn` package now provides a deterministic, shadow-only V0 spine
from an already-built `InterpretationEnvelope` through appraisal and dynamic
state. It records exact authoritative inputs and outputs without changing live
chat behavior. Real Perception-to-Interpretation integration is implemented and
covered by isolated connected tests, but assembling those stages into one live
runtime service remains outside this runner. Routing, action selection,
realization, outcome handling, reflection, and learning also remain outside it.

Interpretation first groups compatible hypotheses into coherent scenarios.
Appraisal evaluates each scenario—not isolated cue labels or an accidental
combination of contradictory hypotheses—using:

```text
appraisal.goal_relevance       [0, 1]
appraisal.goal_congruence      [-1, 1]
appraisal.novelty              [0, 1]
appraisal.certainty            [0, 1]
appraisal.controllability      [0, 1]
appraisal.norm_compatibility   [-1, 1]
appraisal.affiliative_value    [-1, 1]
appraisal.agency               user | columbina | third_party | environment | unknown
```

Interpretation does not directly overwrite dynamic state. The updater consumes
per-scenario, per-goal appraisal frames and preserves branches when credible
alternatives imply different directions:

```text
previous state
    -> deterministic decay toward compiled baseline
    -> appraisal contributions per scenario and focal goal
    -> unknown/disputed branch preservation
    -> bounded personality sensitivity
    -> compatible contribution combination
    -> clamped immutable state revision
    -> derived control signals for routing and action
```

The initial registry contains scoped valence, activation, epistemic confidence,
curiosity, engagement, goal pressure, social alignment, interaction strain, and
a weighted attention map. Core traits set bounded baselines, sensitivities,
persistence, recovery, and thresholds; they do not alter literal meaning,
appraisal evidence, contribution direction, safety, or authorization.

Unknown appraisal values create no midpoint contribution. Disputed alternatives
retain branch-specific proposed updates and trigger slow cognition when they
would materially change state or action. High-consequence alternatives are
never averaged into a benign state.

For V0 aggregation only, ordinal support may be mapped deterministically to
`unsupported=0`, `weak=1`, `moderate=2`, and `strong=3`, then normalized across
compatible retained alternatives. These aggregation weights are not claimed to
be probabilities and must be recorded with their mapping version.

### Interpretation V0

V0 is a deterministic, inspectable hypothesis scorer. It uses no generative LLM,
no untrained probability estimates, and no online weight updates:

```text
validated perception envelope
        +
bounded text/context and semantic frames
        +
trusted runtime and retrieved-memory evidence
        |
        v
versioned cue/frame-to-hypothesis rules
        |
        v
unbiased ordinal base support and contradictions
        |
        v
bounded personality adjustment through ambiguity gate
        |
        v
ranked multi-dimensional hypotheses + unknown/out-of-scope
        |
        v
appraisal event -> router -> state/action stages
```

Initial high-level rules include:

- question form without an action request supports `request_information`;
- an explicit action request supports `request_action` even when phrased as a
  grammatical question;
- explicit correction supports `repair_shared_understanding`;
- an uncertainty marker makes the speaker proposition tentative, not the model;
- negation modifies its semantic scope and does not independently identify intent;
- positive and negative wording together preserves literal and
  tone/situation-mismatch alternatives;
- wording alone may support tone/situation mismatch, but cannot establish
  sarcasm, affect, or runtime event truth;
- grounded discourse context may support a moderate indirect conversational
  action hypothesis without an explicit action-request cue, while the literal
  reading remains available;
- an unresolved semantic argument becomes an unknown, not a guessed value; and
- an unknown user-defined predicate or referent sets `out_of_scope` and normally
  routes to `clarify` because only the user can explain it;
- a known but unavailable, unsafe, or unauthorized action routes to
  `abstain_or_escalate`; and
- a conflict that can be investigated from existing evidence routes to slow
  cognition instead of forcing a known label.

V0 semantic grounding is deliberately limited to versioned lexical/structural
patterns, known action and entity registries, negation/quotation scope rules, and
explicit context references. Anything it cannot ground becomes an unresolved
argument, unknown predicate, or out-of-scope record; V0 does not fabricate a
general semantic parse.

The old experimental strategy of multiplying intent confidence by global
optimism/trust/negativity and mapping the winning intent directly to a user
emotion must not be reused. It conflates intention, emotion, appraisal, and
Columbina's state and can let personality overpower evidence.

### Future learned interpretation model

After sufficient reviewed interpretation records exist, V1+ may use one compact
bounded-context encoder shared with or benchmarked against the perception encoder.
The preferred provisional shape is:

```text
context embedding
+ approved perception-cue vector
+ semantic-frame features
+ provenance-aware memory/relationship features
        |
        v
multi-task interpretation model
        +-- communicative-act head
        +-- speaker-goal head
        +-- modality head
        +-- pragmatic-hypothesis head
        +-- appraisal regression heads
        +-- unknown/out-of-scope head
        |
        v
symbolic constraint and bounded-bias fusion layer
        |
        v
calibrated hypotheses with evidence links
```

Begin with frozen embeddings plus sparse linear/logistic heads so cue and context
contributions remain inspectable. Benchmark a small MLP only if interactions
demonstrably improve held-out results. Fine-tuning, set-based interpretation,
ensembles, or Bayesian approximations are later options, not V1 requirements.

Training is offline, reviewed, versioned, grouped by conversation/user, and
evaluated for multi-label precision/recall, calibration, unknown detection,
contradiction handling, bias sensitivity, subgroup regressions, downstream
routing utility, and state-update stability. Personality and relationship priors
must be ablated during evaluation to prove they improve ambiguous cases without
degrading explicit ones. Learned artifacts deploy in shadow with zero behavioral
influence before promotion.

The delegated manual-case authoring and validation contract is in
`data/INTERPRETATION_DESIGN_CASES_README.md`. Its output is
`data/interpretation_design_cases.json`; the fixture remains pending design data
until human review.

Research basis: the interpretation dimensions follow the multifunctional view of
dialogue acts in [ISO 24617-2](https://aclanthology.org/2020.lrec-1.69/);
probabilistic pragmatic inference motivates preserving contextual alternatives
([Goodman and Frank](https://pubmed.ncbi.nlm.nih.gov/27692852/)); the separation
between inference, appraisal, and affect dynamics follows the computational
[EMA model](https://www.ccs.neu.edu/~marsella/publications/pdf/MarsellaCSR09.pdf);
and the explicit unknown/out-of-scope path responds to evidence that closed-set
intent classifiers struggle when every input is forced into a known class
([Larson et al.](https://aclanthology.org/D19-1131/)).

### Fast, slow, and reflection responsibilities

| Path | Cognitive responsibility |
| --- | --- |
| Fast | Deterministic perception and interpretation rules; later approved compact learned heads |
| Slow | Examine unresolved evidence, competing meanings, memories, and consequences when routed by uncertainty/risk |
| Reflection | Propose missed cues, candidate definitions, labels, and counterexamples |
| Offline learning | Train, calibrate, evaluate, shadow, and version detector artifacts |

The slow/reflection LLM may suggest cues or reviewed hypotheses, but it cannot
silently add a cue to the production manifest. Final response generation may use
an LLM after action selection; that does not make perception LLM-based.

### Slow-cognition routing policy

The router is a metacognitive controller, not another perception cue. It decides
whether the current fast result is reliable enough for the consequence of being
wrong. It returns one of four routes:

| Route | Meaning |
| --- | --- |
| `fast` | Continue with the fast interpretation and action policy |
| `slow` | Spend more computation comparing hypotheses, evidence, memories, and possible actions |
| `clarify` | Ask the user for information that internal reasoning cannot recover |
| `abstain_or_escalate` | Do not proceed autonomously because a hard constraint cannot be resolved safely |

The cheap perception pass and fast interpretation pass normally run before the
final routing decision. This gives the router both grounded cues and an
interpretation distribution. An earlier pre-route may still stop processing when
trusted runtime context already exposes a hard prohibition. The final pre-action
route additionally sees the candidate action and its consequence metadata.

Routing uses hard gates before soft metrics. A weighted average must never hide a
hard safety or authorization failure.

#### Hard gates

The following conditions bypass the ordinary score:

| Condition | Default route |
| --- | --- |
| Material safety, privacy, legal, or harm risk | `slow`, then `abstain_or_escalate` if unresolved |
| Irreversible or externally consequential action | `slow` before execution |
| Missing or uncertain authorization | `abstain_or_escalate`, or `clarify` when the user can grant/confirm it |
| A required user-owned parameter is missing | `clarify` |
| Trusted runtime facts conflict with the proposed action | `slow` or `abstain_or_escalate` |
| Policy or core-value constraints conflict | `slow` |
| Repeated failed action or explicit correction of the system | `slow` |
| User explicitly asks for careful analysis or deliberation | `slow` |

#### Soft routing metrics

Each soft metric is normalized to `[0, 1]`, accompanied by provenance and a
metric-version identifier. No metric should be invented when its required model
output is unavailable.

| Metric | Meaning | Initial source |
| --- | --- | --- |
| Interpretation entropy | How spread the interpretation distribution is: `-sum(p_i log p_i) / log(K)` | V1 hypothesis probabilities |
| Top-two ambiguity | How close the two leading hypotheses are: `1 - (p_1 - p_2)` | V1 hypothesis probabilities |
| Calibrated error probability | Estimated chance that the selected fast interpretation is wrong | Held-out calibration; unavailable in data-free V0 |
| Cue conflict | Degree to which reliable cues support incompatible hypotheses | Rule conflicts in V0; learned contributions later |
| Evidence insufficiency | Important evidence is unknown, disputed, low-confidence, or uncovered | Perception envelope and task requirements |
| Novelty / out-of-distribution | Current input differs from reviewed training support | V0 lexical/schema fallback; embedding or energy score later |
| Memory contradiction | Retrieved trusted memories disagree with the current interpretation or each other | Memory retrieval and provenance |
| Failure / correction pressure | Recent failed actions, unresolved repetition, or explicit user corrections | Conversation and runtime event history |
| Consequence severity | Expected cost, external effect, irreversibility, and blast radius if the action is wrong | Action registry and runtime policy |
| Expected slow-path benefit | Estimated chance that deliberation can materially improve the decision | Outcome history, once enough reviewed data exists |

An uncertainty marker in user language is only evidence about the user's claim;
it is not by itself evidence that Columbina's interpretation is uncertain. Likewise,
sarcasm ambiguity can justify slow interpretation, while a missing recipient should
usually produce `clarify`, not repeated internal thought.

V0 has no calibrated hypothesis probabilities, so it must not pretend to compute
entropy or error likelihood. The following deterministic router is the accepted
V0 triggering policy:

```text
if unsafe, unauthorized, or prohibited:
    abstain_or_escalate
else if required information can only come from the user:
    clarify
else if the candidate action is consequential or difficult to reverse:
    slow
else if plausible interpretations conflict and choosing incorrectly matters:
    slow
else if trusted runtime facts or relevant memories conflict with the fast result:
    slow
else if the system has been explicitly corrected or the attempted action repeatedly failed:
    slow
else if at least two independent minor warning signals are present:
    slow
else:
    fast
```

The controlling distinction is:

> Use slow cognition when additional reasoning over existing evidence could
> prevent a meaningful mistake. Use clarification when the missing evidence must
> come from the user.

Slow cognition must not merely repeat the fast pass. It must enumerate plausible
interpretations, compare supporting and conflicting evidence, retrieve relevant
memories, inspect assumptions, compare eligible actions and consequences, and
then select an interpretation/action or return `clarify` or
`abstain_or_escalate`.

The “two minor signals” rule is a bootstrap heuristic, not a learned law. A single
hard gate, materially consequential action, or strong decision-changing conflict
is sufficient on its own. Every routing record must retain the active flags,
available metric values, missing metrics, selected route, policy version,
latency, and eventual outcome so the threshold can later be calibrated.

Once reviewed outcomes exist, routing should approximate expected value of
computation:

```text
EVC = P(fast decision is wrong)
    * consequence cost
    * expected improvement from slow cognition
    - cost of slow cognition
```

Choose `slow` when `EVC` is positive, subject to hard gates. This follows the
rational-metareasoning view that computation itself is an action with costs and
benefits. A selective-prediction evaluation should then plot **fast coverage**
against **fast-path error** instead of optimizing raw accuracy alone. Also track
false-fast rate, unnecessary-slow rate, clarification success, slow-path recovery,
latency, and compute cost.

Research basis: selective prediction formalizes the risk/coverage tradeoff
([SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html)); deep ensembles
provide a practical uncertainty baseline
([Lakshminarayanan et al.](https://proceedings.neurips.cc/paper_files/paper/2017/hash/9ef2ed4b7fd2c810847ffa5fa85bce38-Abstract.html));
energy scores are a later option for out-of-distribution detection
([Liu et al.](https://proceedings.neurips.cc/paper/2020/hash/f5496252609c43eb8a3d147ab9b9c006-Abstract.html));
and expected value of computation supplies the controller's long-term objective
([Rational metareasoning](https://pmc.ncbi.nlm.nih.gov/articles/PMC5937797/)).

### Future perception evolution

V1 favors inspectability and limited data requirements. Possible later upgrades
are conditional on measured shortcomings:

1. Fine-tune the shared compact encoder when frozen embeddings plateau and the
   reviewed dataset is large enough.
2. Add multi-task or hierarchical heads that share evidence across related cue
   families without merging their definitions.
3. Add dedicated span extraction only when whole-message evidence is inadequate.
4. Add ensemble or Bayesian uncertainty when simple calibration fails on novel
   inputs.
5. Add personalized residual detectors only after global behavior is stable and
   privacy, minimum-evidence, and overfitting protections are defined.
6. Consider a set-based interpretation network when linear cue-to-hypothesis
   interactions demonstrably fail; do not introduce it only for complexity.

### Perception decision ledger

The design is **not fully finalized**. Current decision status is:

| Topic | Status | Current decision or remaining question |
| --- | --- | --- |
| Perception V0 runtime | Implemented, isolated | Immutable evidence-linked event, 13-cue static manifest, deterministic detectors, coverage uncertainty, fixture validator, and lifecycle-safe interpretation handoff; no live-chat integration |
| Perception/interpretation boundary | Implemented for V0 | Cues are detectable signals; meanings remain hypotheses |
| Fast-path LLM use | Implemented for V0 | No generative LLM in fast perception or fast interpretation |
| Perception envelope | Implemented for V0 | Fixed, typed, versioned envelope with extensible registered cues |
| Dynamic cue safety | Implemented for V0 | Candidate and shadow cues have zero live behavioral influence |
| V0 model shape | Implemented | Rules/runtime facts only; no untrained neural cue predictions |
| V1 model shape | Provisional | Frozen `BAAI/bge-small-en-v1.5` plus multi-label heads and deterministic detectors |
| Confidence | Accepted direction | Detector score is calibrated on held-out reviewed data |
| Salience | Accepted direction | Separate bounded attention calculation; exact formula remains open |
| Evidence | Accepted direction | Source references/spans, never model attention as explanation |
| Learning timing | Accepted direction | Offline, reviewed, versioned training; no per-turn weight updates |
| Learning record semantics | Accepted direction | Present/absent/unknown/disputed/not-applicable with provenance |
| Initial cue list | Implemented V0 manifest | Thirteen namespaced cues with `v0_active` or `shadow` status |
| Encoder artifact | Preferred candidate | Benchmark BGE-small against MiniLM before promotion |
| Context window | Implemented for V0 | At most 30 ordered messages ending at focus; the bounded adapter keeps the newest 30 and records truncation |
| Calibration method | Provisional | Start with temperature or Platt scaling; validate per cue |
| Approval thresholds | Open | Minimum examples, calibration bounds, utility, and safety criteria |
| Salience coefficients | Open | Operational mapping from goals, risk, novelty, and personality |
| Interpretation schema | Implemented and acceptance-hardened for shadow V0 | Multidimensional hypotheses, semantic frames, alternatives, evidence, contradictions, unknowns, and version provenance |
| Initial interpretation ontology | Implemented V0 manifest | Ten acts, nine speaker goals, six modalities, and four pragmatic hypotheses; appraisal remains downstream |
| Interpretation scenarios | Implemented and acceptance-hardened for shadow V0 | Deterministic retained scenarios feed appraisal; hypothesis consolidation and compatibility/conflict validation are enforced |
| Appraisal architecture | Accepted V0.1 direction | Versioned per-scenario appraisal frames; deterministic V0; incremental reappraisal when interpretation, memory, runtime state, or action simulation adds evidence |
| Appraisal expansion | Accepted direction | Fixed outer envelope plus versioned dimension registry; candidate dimensions remain shadow until reviewed evidence demonstrates distinct downstream value |
| Interpretation design fixture | Implemented validator; human review pending | Twenty-three design cases are structurally validated; 22 run through live Perception/Interpretation and one shadow-dependent case is deferred |
| Core-memory bias boundary | Accepted direction | No effect on observed facts; ambiguity-gated bounded priors; stronger influence on appraisal and state sensitivity |
| Interpretation V0 | Implemented and acceptance-hardened shadow V0 | Canonical evidence-linked envelope, bounded grounding, ordinal hypotheses, alternatives, and unknown/out-of-scope handling; not live-integrated |
| Goal System V0 | Architecture accepted; rule tables, fixtures, and implementation open | Canonical ownership, typed ontology, lifecycle, response-turn fallback goal, priority/conflict/focus policy, V0 scope persistence, satisfaction evidence, engine placement, and trace/failure boundaries are fixed |
| Cognitive Pipeline V0 | Implemented and acceptance-hardened shadow V0 | One canonical request/runtime snapshot connects exact Perception, Interpretation, Cognitive Turn, Appraisal, and Dynamic State outputs into a validated trace; not live-integrated |
| Interpretation learner V1+ | Provisional | Compact context encoder with multi-task heads and a symbolic constraint/bias-fusion layer |
| Slow-cognition router V0 | Accepted direction | Deterministic hard gates and warning rules; distinguish internally resolvable uncertainty from information that requires user clarification |
| Learned router V1+ | Provisional | Calibrate uncertainty, consequence, and expected-value metrics from reviewed routing outcomes |
| Database schema | Open | Design only after perception and interpretation contracts are reviewed |

### Data-free V0 and dataset bootstrap

There is currently no reviewed dataset for the new cue ontology. Therefore the
learned V1 perception model must not be treated as available yet. A pretrained
encoder can create embeddings, but its cue heads cannot learn Columbina's cue
definitions without labeled examples.

V0 uses only deterministic signals whose operational meaning can be checked:

```text
message + recent structural context + runtime facts
                         |
                         v
questions, explicit action requests/corrections, repetition, negation,
message structure, attachment state, tool status, and similar rules
                         |
                         v
validated V0 cue activations
```

V0 must not claim reliable detection of sarcasm, hidden hostility, annoyance,
deception, playfulness, or internal emotion. Those remain slow-path hypotheses
until there is reviewed evidence for fast detectors.

The bootstrap loop is:

```text
V0 runtime traces and authorized conversations
                         |
                         v
slow/reflection LLM proposes cue labels against fixed definitions
                         |
                         v
pending weak labels with evidence spans and AI provenance
                         |
                         v
human accepts, corrects, rejects, or marks unknown/disputed
                         |
                         v
reviewed perception-training examples
                         |
                         v
train and calibrate V1 cue heads offline
                         |
                         v
shadow deployment with zero interpretation influence
                         |
                         v
promotion only after held-out evaluation
```

Synthetic examples and public datasets may improve coverage, but neither may be
the sole production evaluation set. Silence is not a label. An LLM annotation is
not ground truth. Once an early classifier exists, review should prioritize
uncertain, disagreeing, novel, and out-of-distribution examples through active
learning instead of labeling every ordinary turn.

### Selected V1 encoder direction

The preferred V1 candidate is
[`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5):

- approximately 33.4 million parameters;
- 384-dimensional sentence embeddings;
- a maximum sequence length of 512 tokens;
- MIT licensing; and
- a practical size for local CPU/MPS inference and frozen-encoder training.

This is a preferred candidate, not a promoted production artifact. BGE produces
an embedding, not cues by itself:

```text
bounded role-labelled conversation context
                         |
                         v
              frozen BGE-small encoder
                         |
                         v
              384-dimensional embedding
                         |
                         v
          trained multi-label cue-head matrix
                         |
                         v
                   raw cue scores
```

The initial training policy is:

```text
BGE encoder weights: frozen
cue-head weights:    trained from reviewed cue labels
calibrators:         fitted on held-out validation records
```

The first benchmark should compare BGE-small with
[`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
MiniLM is smaller and may be faster, but its default 256-word-piece truncation is
more restrictive. If multilingual behavior is a V1 requirement, benchmark
[`paraphrase-multilingual-MiniLM-L12-v2`](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
instead of assuming an English encoder will generalize.

Selection uses Columbina's reviewed holdout data, not embedding leaderboard rank
alone. Required comparisons include per-cue PR-AUC/precision/recall, calibration,
sensitive-cue false positives, out-of-distribution behavior, downstream
interpretation utility, p50/p95 latency, and resident memory on target hardware.
ONNX/INT8 optimization is considered only after an accurate artifact exists and
must be regression-tested against its unquantized version.

SetFit or partial/full encoder fine-tuning is a later option, not the starting
point. It requires enough diverse reviewed data to demonstrate an improvement
over frozen embeddings without unacceptable overfitting or calibration loss.

### Legacy cognitive-nodes reuse decision

`Cognitive/cognitive_nodes` came from a separate experiment. Its dependencies,
paths, schemas, and serialized artifacts are not part of this application's
runtime contract. The code will not be transplanted directly. Its conceptual
pattern and data may still help bootstrap the new design.

The useful legacy pattern is:

```text
specialized signal detectors -> shared cue representation
                              -> transparent weighted interpretation
```

The new architecture preserves that pattern while correcting its boundaries:

| Legacy concept | New treatment |
| --- | --- |
| Emotion classifier | Optional linguistic emotion-expression cue provider, not true user emotion |
| Politeness classifier | External pretrained shadow cue provider until locally evaluated |
| Toxicity classifier | Optional specialized safety cue provider, not general social perception |
| VAD lexicons | Candidate deterministic lexical cues after source/license and implementation review |
| Intent classifier | Interpretation component; intent is removed from perception |
| Hard-coded cue/trait matrix | Replace with versioned, evaluated cue-to-hypothesis weights |
| Existing user impression | Bounded prior; it cannot overwrite or amplify current evidence without limit |
| Positive/negative/neutral collapse | Preserve distinct hypotheses and uncertainty |

The legacy files provide approximately 170 candidate perception annotations and
159 intent labels over the beginning of a larger conversation corpus. They are
weak legacy examples, not a reviewed dataset: their lengths and label contracts
do not match, their provenance is incomplete, rare intent classes are extremely
sparse, and old salience values are not ground truth.

They may enter the new system only through an explicit conversion and review
queue:

```text
legacy conversation + legacy annotation
                         |
                         v
map directly observable fields to candidate registered cues
                         |
                         v
discard or quarantine inferred salience/mental-state claims
                         |
                         v
human review under the new cue definition and evidence rules
                         |
                         v
new reviewed training record with legacy provenance
```

Initial conversion candidates are:

| Legacy field | Candidate new cue |
| --- | --- |
| `questions` | `conversation.question_form` |
| `directives` | `conversation.directive_marker` |
| `self_references` | `linguistic.self_reference` |
| `politeness_markers` | `linguistic.politeness_marker` |
| `emotion_words` | `linguistic.affective_wording` |
| `speech_act_type` | Candidate label requiring review |
| `ambiguity_flag` | Candidate label requiring review |
| `salience_score` | Do not import as truth |

Third-party legacy detectors may be run offline or in shadow to propose weak
labels, but their scores must retain detector/model provenance and contribute
zero to live interpretation until evaluated and calibrated on Columbina data.
The old serialized joblib artifact and missing local transformer checkpoint are
not production dependencies.

### Complete perception-to-learning connection

The following is the canonical connection that later implementation must
preserve. Each arrow represents a versioned contract, and every object belongs
to the same `cognitive_turn` trace:

```text
INPUT
current message + bounded history + runtime facts
                         |
                         v
PERCEPTION
deterministic detectors + approved learned cue heads
                         |
                         v
typed cue activations
(value, calibrated confidence, salience, evidence, provenance)
                         |
                         v
INTERPRETATION
cue-to-hypothesis weights + context + retrieved memories
+ bounded personality and relationship priors
                         |
                         v
multiple meaning hypotheses with ordinal support in V0 or calibrated probabilities in V1+
                         |
                         v
METACOGNITIVE ROUTER
low uncertainty/risk -> fast path
high uncertainty/risk/novelty/conflict -> slow path or clarification
                         |
                         v
GOAL AND ACTION
current goals + eligible registered actions + hard safety gates
+ learned action policy
                         |
                         v
selected abstract action and bounded language style
                         |
                         v
REALIZATION
generative LLM expresses the selected action; authorized tools execute separately
                         |
                         v
OUTCOME
user response + explicit feedback + tool/runtime outcome
                         |
                         v
REFLECTION
candidate cue labels, interpretation labels, action evaluation, and memories
                         |
                         v
VALIDATION AND REVIEW
provenance, evidence, ownership, safety, corrections, and uncertainty
                         |
                         v
OFFLINE LEARNING
targeted detector, calibrator, interpretation-weight, retrieval, or action-policy update
                         |
                         v
SHADOW EVALUATION AND VERSIONED PROMOTION
```

The connection has four invariants:

1. Perception never converts a meaning hypothesis into an observed fact.
2. Personality changes salience and bounded priors, never source evidence.
3. Response generation cannot silently replace the selected abstract action.
4. Feedback updates only the component supported by error attribution; it does
   not punish or mutate the entire chain automatically.

The next review should walk a small set of contrasting conversations through
the accepted cue and interpretation ontologies, validate the personality-bias
boundary, and revise the contracts before implementation.

## Connected learning and error attribution

Perception learning and interpretation learning use the same trace but solve
different prediction problems:

```text
perception learning:
conversation + context -> reviewed cue labels

interpretation learning:
cue activations + context + memories + priors -> reviewed meaning hypotheses

action learning:
cognitive state + goals + eligible actions -> observed outcome
```

Outcome analysis proceeds backward through the trace:

```text
outcome or feedback
       |
       v
was the selected action appropriate?
       |
       v
was the chosen interpretation supported?
       |
       v
were the relevant observations and cues detected correctly?
```

This supports targeted attribution:

| Observed failure | Primary update target |
| --- | --- |
| Relevant cue was missed or measured incorrectly | Perception detector |
| Cues were accurate but the inferred meaning was wrong | Interpretation model or prior |
| Meaning was reasonable but the strategy was poor | Action policy |
| Strategy was appropriate but wording contradicted it | Language realization |
| Retrieved memory was false, stale, or irrelevant | Memory record or retrieval policy |

Negative feedback must not automatically punish every component. When causal
attribution remains uncertain, the trace should retain multiple possible error
sources for review rather than manufacture a definitive label.

Historical predictions are immutable. Human correction, later evidence, or an
AI evaluator creates a separate reviewed label with provenance and confidence.
Explicit human correction receives the greatest evidential weight. Training
jobs compare original predictions with reviewed labels and create new versioned
artifacts; they do not update model weights after every conversation.

Every trace should record enough version information to reproduce its behavior:

- cue-definition and perception-detector versions;
- perception and interpretation schema versions;
- cue-manifest, interpretation feature-mapping, and interpretation-model versions;
- core-memory and PersonalityRuntimeMapper versions;
- memory-retrieval policy and retrieved record ids;
- action-registry and action-policy versions; and
- response-model, tool, and evaluator versions.

This version graph is the connection between cognition and learning: it makes it
possible to determine whether a later improvement came from perception,
interpretation, memory, identity, action selection, or response realization.

## Design review gates before implementation

Before creating additional tables or runtime code, review this design in order:

1. Confirm the root cognitive-turn trace and stage boundaries.
2. Finalize the stable perception envelope and primitive observation types.
3. Validate the initial core cue ontology and candidate-cue approval criteria
   against contrasting conversations.
4. Create and review contrasting interpretation design cases against the accepted
   hypothesis schema, bias boundary, and uncertainty representation.
5. Validate the cue-to-interpretation feature mapping and version manifest with
   those cases.
6. Walk representative conversations through the complete forward trace.
7. Walk corrections and outcomes backward through error attribution.
8. Only then design database tables, runtime services, and training jobs.

Structured output follows the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Rules runtime API

`CognitiveInputPipeline.process()` returns a validated `CognitiveInput` with:

- `Perception`: stable question/request/direction, novelty, and ambiguity fields
  plus an expandable map of typed `PerceptionSignal` values.
- `Interpretation`: one of `question`, `request`, `inform`, `clarify`, `feedback`,
  `greeting`, or `unknown`, its confidence, alternatives, and whether more
  information is needed.

Rules processing performs no network or LLM calls. Interpretation lazily loads
an optional local classifier artifact and safely falls back to deterministic
rules when no valid artifact is configured.

## Training

Training data uses reviewed JSONL records:

```json
{"text": "Can you analyze this image?", "intent": "request"}
```

From `backend`, train and evaluate with:

```bash
python -m app.ai.Cognitive.system.training.train_intent DATA.jsonl ARTIFACT.json
python -m app.ai.Cognitive.system.training.evaluate_intent DATA.jsonl
```

The legacy corpus is not migrated automatically. Its three source files have
different lengths, its label set does not match the new contract, and several
minority classes contain too few examples for meaningful evaluation. Review and
relabel examples before creating a deployable artifact.

## Extension rules

- Keep the stable `Perception` fields small.
- Add new observations through `signals`, with a value, confidence, and source.
- Do not overwrite reserved signals produced by the perception engine.
- Keep model training offline and artifact loading lazy.
- Keep action selection separate from perception and interpretation, as a sibling state.
