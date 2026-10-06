# Phased Context-Conditioned Cognition and Latency — Implementation Handoff

Date: 2026-10-04
Status: revised design and implementation instructions; not implemented or benchmarked by this edit.
Scope: Cognitive V0 shadow only. No new live delivery or tool-execution authority.

This file replaces the previous phased plan in full. It operationalizes the
[context-conditioned two-call redesign](CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md).
Do not apply the old requirement to preserve every deterministic input unchanged.
Change interfaces with explicit versions where necessary, while retaining state,
permission, reference, revision and outcome integrity. Keep old captures replayable.

Interpretation, model Appraisal and two-call orchestration already exist.
Inspect and reuse implemented code; do not restart from the earlier deterministic
baseline or overwrite another agent's work. The phases below migrate the actual
two-call implementation, not six successive LLM stages.

## 1. Objective and execution order

Use the LLM temporarily for contextual prediction and gather candidate training
data for future specialized models. The agent's interpretation, appraisal and
behavior must reflect her actual personality, biases, affinity/relationship,
dynamic state, values and relevant memories—not merely generic assistant
judgment dressed in a personality voice.

Pretraining priors cannot be removed by instruction. Demonstrate context
conditioning with paired examples and ablations, not a model's assertion that
it used the context. Do not force every context change to alter every prediction.

Keep two sequential calls on the normal conversational path:

```text
authorized utterance + allowed context + previous snapshots + actual outcomes
  → capture/reconcile + Perception + context projection
  → TurnContext
  → CALL 1: expressed meaning + situated reading + event appraisal
  → MindProposal
  → semantic/reference compiler
  → AcceptedUnderstanding + AcceptedEventAppraisal
  → goals/workspace reconciliation + typed requirements + appraisal binding
  → GoalSnapshot + WorkspaceSnapshot + Readiness + AppraisalResult
  → deterministic Dynamic State update
  → StateAfter
  → decision-packet compiler
  → DecisionContext
  → CALL 2: behavior + content + wording
  → ResponseProposal
  → content compiler → action construction/gates/transitions → text binding
  → AcceptedResponseBundle OR typed rejection
  → shadow persistence
  → delivery/execution only under separately approved authority
  → real observed outcomes feed the next turn
```

Phase 0 establishes the migration inventory and measurements.
Phases 1–4 build connected handoffs.
Phase 5 verifies behavior and tunes measured latency.
Do not mark a phase complete merely because its model call returned JSON.

### 1.1 Ownership rules

The model proposes meaning, subjective readings, significance, content,
behavior choices and wording. Code owns:

- Authorization/exposure, identity, authoritative context snapshots and references.
- State accumulation, decay, bounds, scope and persistence.
- Goal lifecycle transitions, focus accounting and required outcome evidence.
- Requirement readiness, actual capabilities, consent and safety restrictions.
- Artifact patch application, revisions and concurrency control.
- Action eligibility and per-step transitions.
- Acceptance, delivery and tool outcome records.

Deterministic code must not remain a second semantic judge that overrides valid
model readings solely because legacy heuristics or scores disagree. An unknown
psychological assessment is not a broken schema or fabricated certainty.
Personality may influence uncertain social interpretation, not rewrite explicit
facts, negation, authorization or safety boundaries.

### 1.2 Input/output contracts

The type names below are proposed new contracts, not claims they already exist.

| Producer | Input | Output → consumer |
| --- | --- | --- |
| Capture/context projector | Validated utterance, authorized dialogue/memories, versioned snapshots/outcomes | TurnContext → Call 1; ReferenceTable → compilers |
| Call 1 | TurnContext | MindProposal → understanding compiler |
| Understanding compiler | Proposal, ontology, source/reference table | AcceptedUnderstanding → goals/workspace; AcceptedEventAppraisal → binder |
| Goal/workspace/requirements | Understanding, prior snapshots, actual runtime facts/outcomes | GoalSnapshot, WorkspaceSnapshot, Readiness and formation links → binder/decision compiler |
| Appraisal binder | Event appraisal, actual formation links/readiness | AppraisalResult → StateUpdater |
| StateUpdater | Previous state, appraisal, state personality, elapsed time/outcomes | StateAfter → decision compiler |
| Decision compiler | Accepted understanding, focused goals/readiness, workspace slice, StateAfter/context | DecisionContext → Call 2 |
| Call 2 | DecisionContext | ResponseProposal → content/action/text compilers |
| Acceptance | Compiled content, gated sequence, text/span map, current revisions | AcceptedResponseBundle → persistence/delivery policy |
| Outcome reconciliation | Actual delivery/tool results and later correction | Updated lifecycle/workspace evidence → next cycle |

Each boundary needs a versioned type, explicit reference scope and failure
semantics. Never pass raw provider output where an accepted contract is required.
A consumer must not silently reconstruct a different interpretation.

## 2. Phase 0 — inventory, protocol versions and latency attribution

### Input → output

Current code, configuration and stored traces →
migration inventory, explicit protocol flag and instrumented current call graph.

### Implementation

Inspect these existing anchors relative to this file's directory:

- `two_call/contracts.py`, `orchestrator.py`, `provider.py`, prompts and `realization.py`.
- `interpretation_v0/engine.py`, `bias.py`, scenarios, model grounding and contracts.
- `appraisal/context.py`, model contracts/compiler, adapters and aggregation.
- `goal_v0/`, `dynamic_state/`, requirement/workspace/collaboration modules.
- `action_selection_v0/`, realization compiler/validator and captured response conversion.
- `cognitive_pipeline_v0/runner.py`, `cognitive_turn/runner.py`, `live_shadow/`,
  shadow service/worker, latency spans and trace-summary producers.

Identify exact existing schema versions, refs, sources, unknown handling,
state order, lifecycle/outcome dependencies and replay rules. Verify enabled
configuration rather than inferring it from an old document.

The current Call 1 payload includes all goal definitions and rich per-goal
appraisal. Call 2 includes overlapping context, goals/workspace/requirements,
content requests, action metadata and preview plans/realizations. Attribute
their token costs; do not assume each field contributes equally to latency.

Introduce an explicit new compact protocol/version under shadow settings.
Retain old decoding/replay and frozen provider doubles. Record model, prompt,
schema, context projection, supported sampling/reasoning settings, timeout and
attempt policy. Do not silently change the configured model.

Extend `trace_summary` and existing spans to expose:

- Full-path wall time, queue wait, core completion, final acceptance and delivery.
- Per-call payload compilation, provider wall time, parsing and compilation.
- First-token time only if actually measured; otherwise unavailable.
- Input/output tokens, cached-input and reasoning tokens if reported.
- Payload-section/output-section attribution including instructions and schema;
  record tokenizer/version or label estimates.
- Local goal/requirement/state/content/action/realization/persistence time.
- Attempts, retries, overhead, timeout/truncation, rejection and supersession.
- Protocol/context/prompt/model versions and projection completeness.

### Completion gate

Existing two-call captures still replay; instrumentation does not change behavior.
Produce a current call graph and latency record, not a deterministic-versus-model
benchmark. The user's previously reported 25.68 seconds and token counts are
context, not measurements made by the implementing agent.

## 3. Phase 1 — shared context and compact context-conditioned Call 1

### Input → output

Authorized capture + Perception + previous snapshots →
TurnContext → MindProposal → AcceptedUnderstanding + AcceptedEventAppraisal.

### Shared projection

Create one validated immutable context source with stage-specific views.
Build it after authorization/exposure and snapshot checks, not before them.

```text
TurnContext:
  current_utterance + speaker/message handle
  bounded_dialogue + relevant correction/clarification history
  relevant_previous_goals/workspace + open inquiries
  persona: sourced traits, voice constraints, preferences, values
  biases: actual interpretation fields and canonical scales
  relationship: availability, scoped exposed affinity/trust/familiarity
  state_before: relevant active scores/bands and availability
  relevant_memories: authorized excerpts, source/freshness/epistemic status
  capabilities/permissions + observed outcomes relevant to feasibility
  perception_cues: fallible evidence, including unknown status
```

Keep a code-only ReferenceTable from local handles to canonical IDs, revisions,
source types, exact spans and visibility. Give the model short local handles
and the content needed to use them, not invented UUID/hash obligations.

Static prompt definitions explain variable scales, scope, high/low behavior and
permitted influence once. Runtime sends actual values and availability.
Unavailable affinity/trait/state is not zero or neutral. Synthetic experiment
profiles must be labeled synthetic. Memories are not automatically world truth.

Both calls use the same persona/bias/relationship snapshot; Call 1 gets
state_before and Call 2 gets state_after. A bounded dialogue projection must
retain decisive corrections. Report omissions; avoid repeating raw history in
every nested request.

### Compact MindProposal

```text
observations[]:
  handle, act, predicate/arguments, polarity, source_span, epistemic_status
readings[]:
  handle, event_handles, candidate_meaning, support, uncertainty,
  evidence_handles, context_handles
event_appraisals[]:
  event_handles, reading_handle, compact_dimensions,
  evidence_handles, context_handles
existing_goal_impacts[]:
  goal_handle, event_handles, congruence, relevance, uncertainty
unknowns[] / conflicts[]:
  subject_handle, missing_or_conflicting_information
```

Observations express what was said. User claims remain user claims unless verified.
Readings express this agent's uncertain interpretation, permitting alternatives.
Appraisal expresses perceived significance and subjective norm compatibility,
not an objective verdict or authorization.

Use a fixed compact eight-dimension vector per materially distinct event/reading,
with shared evidence/context bundles and sparse dimension exceptions. Preserve
dimension scales and assessed/unknown/not-applicable distinctions. Missing values
must not be filled with neutral numbers. Do not require eight long evidence
summaries per guessed future goal or free-form reasoning transcripts.

### Prompt instruction

> Predict this agent's reading and appraisal from the supplied context. Separate
> expressed content from inferred intent. Use personality, biases, relationship,
> state and relevant memories as tendencies when evidence permits alternatives.
> Do not substitute an ideal helpful assistant's reaction or universal politeness
> judgment. Preserve explicit meaning, negation and boundaries. Do not claim to
> know another person's intent. Return compact structured predictions and
> supporting handles; unavailable context stays unavailable. Do not create goals,
> state, permissions or external outcomes.

Use a few contrasting examples: ambiguous teasing under different familiarity/
playfulness/trust; explicit denial of consent invariant under those same changes.
Context definitions must explain behavioral meaning, not just inject bare numbers.

### Connect Interpretation and prevent double bias

Compile observations into accepted semantic frames. Replace deterministic
pragmatic/social hypothesis prediction with accepted model readings for the new
protocol. Wire readings into scenarios, Goal evidence, appraisal and Call 2;
a trace-only sidecar is insufficient.

Bypass `BiasPolicy.apply` for already context-conditioned readings and record
the model as bias-application owner. Retain structural/grounding validation.
Legacy protocol retains its old rules. Do not merely add a model reading while
leaving a conflicting legacy hypothesis as the downstream authority.

### Completion gate

Paired context tests alter ambiguous readings coherently; explicit facts,
negation/permission and safety remain invariant. Unknown context stays unknown.
Downstream scenario/goal consumers demonstrably receive accepted readings.
No double bias, fabricated source refs or extra provider call.

## 4. Phase 2 — event-to-goal binding, typed readiness and state continuity

### Input → output

AcceptedUnderstanding + AcceptedEventAppraisal + prior snapshots/runtime →
actual goals/workspace/readiness + AppraisalResult → StateAfter.

### Remove prediction of future goals

Call 1 must not guess all future goal definitions or IDs. Existing goals can be
referenced. Goal formation emits canonical links from accepted event/frame
handles to actually adopted goals.

Bind event assessments through those links. Event affect remains event-level;
goal-specific congruence/relevance needs a justified mapping. Do not duplicate
an entire vector across every formed goal or multiply one emotional event by
the number of its goal links. Aggregate unique event influence explicitly.

Unmatched assessments and unassessed adopted goals yield typed unknowns.
Invalid/foreign refs remain errors. If a dimension requires newly resolved
readiness, preserve unknown or apply a narrowly defined runtime-derived rule
with that provenance, not invented psychological assessment or another LLM call.

### Lifecycle and readiness

Reconcile observed prior outcomes before updating goals. Apply current
corrections/reference resolution after understanding and before Call 2.
Keep supersession, terminal transitions and current-request focus. A generated
promise or proposed artifact does not establish delivery/execution success.

Expose compact typed readiness per goal:

```text
goal_handle, readiness_kind, missing_fields, known_values,
capability_status, permission_status, grounding_status,
permitted_next_behaviors, blocked_reason
```

Map existing records to explicit ready, needs_clarification,
unavailable_capability, needs_authorization, unsafe, insufficient_grounding
and failed_dependency states or equivalent versioned enums.
Unavailable capability is not an unsafe-request refusal. Missing fields in one
compound request must not block ready independent parts.

Ask a blocking question when the answer materially affects correctness, consent
or consequences. Permit explicit low-risk assumptions where supported.
Optional curiosity may accompany/follow progress; it is not a mandatory "why?"
and must not interrupt every conversation.

StateUpdater applies accepted appraisal with existing state-personality mapping,
elapsed time, observed outcomes, bounds/decay and scoped accumulation.
Preserve continuity; do not let the model rewrite snapshots or reset affinity.
StateAfter feeds Call 2 only, not this turn's Call 1.

### Completion gate

New/old goals link to the correct events, unknown appraisals do not corrupt the
pipeline, aggregate affect is not double-counted, corrections and topic shifts
change focus appropriately, and real outcomes drive lifecycle.
Typed readiness produces clarification/capability/authorization/refusal paths
without suppressing useful independent responses.

## 5. Phase 3 — compact DecisionContext and single-source Call 2 output

### Input → output

Accepted understanding + actual goals/readiness + workspace slice + StateAfter +
shared context → DecisionContext → ResponseProposal.

### Decision packet

Project once: current utterance/bounded dialogue; accepted observations/readings;
decision-relevant appraisal; StateAfter; sourced persona/bias/relationship/memory;
focused goals/readiness; relevant artifact content/base revision/constraints;
grounded behavioral choices and permitted construction templates.

Include answer epistemic permissions: verified runtime facts, provided content,
exposed memory, permitted general knowledge, uncertain inference or unavailable.
Do not present an unsupported model assertion as trusted runtime grounding.

Keep full internal traces/registries/state/goals/workspace/evidence in code.
Remove repeated messages, per-request history copies, full deterministic preview
plans/realizations and duplicate content inventories from the provider packet.
A needed artifact excerpt cannot be replaced by an opaque handle alone.

Build current choices directly through candidate construction/gating, not a
deterministic full-response preview. New content may require future targets:
provide compact typed construction templates, not fabricated eligible candidates.
Reconstruct/gate actual choices after content acceptance at each sequence step.

### ResponseProposal

```text
content_ops[]:
  request_handle, operation, base_revision_handle, patch_or_content, source_handles
moves[]:
  behavior_handle_or_template, goal_handles, target_handles,
  typed_parameters, response_segment_handles
segments[]:
  handle, text, content_handles, claim_bindings
inquiries[]:
  handle, target_handle, asked_about, blocking_or_optional
```

Generate user-visible prose once, in ordered segments. Compiler concatenates it
and derives exact spans; moves reference segments. One segment may serve
compatible moves. No separate full response plus duplicate move excerpts and
alignment text. Do not force artifact patches for ordinary social conversation.

Artifacts may contain more than speech; require appropriate delivery and crucial
content coverage, not verbatim speech of every stored item. Claim bindings support
provenance but do not by themselves prove semantic truth or entailment.

### Prompt instruction

> Choose this agent's next behavior and words from accepted understanding,
> goals/readiness and updated state. Let actual traits and relationship influence
> initiative, curiosity, disagreement and tone, not just decoration. Respond to
> the person and utterance; do not default to routine acknowledgments, unsolicited
> plans, service-agent introductions or mandatory questions. Those forms remain
> allowed when appropriate. Do not invent affection, hostility, memories,
> certainty, capabilities or completed actions. Ask a blocking question only
> when the answer matters; optional curiosity need not delay safe progress.
> Return one coherent response using permitted choices and valid handles.

Keep context instructions trusted and quoted dialogue/retrieved material
untrusted. Do not enforce caricature or hostility as evidence of personality.
New decisive ambiguity discovered in construction becomes typed clarification/
uncertainty, not a hidden reinterpretation or third provider call.

### Completion gate

Context remains present in Call 2; updated state is used. Chosen behavior does
not need to match deterministic ranking but must be grounded and eligible.
Social/info/artifact modes work without mandatory artificial task scaffolding.
Prose is generated once and sources/targets resolve correctly.

## 6. Phase 4 — connected acceptance, captured realization and outcome feedback

### Input → output

ResponseProposal + authoritative references/revisions/runtime →
compiled content + gated action sequence + text/span map →
AcceptedResponseBundle or typed rejection → persistence/outcome reconciliation.

### Implementation

Compile content first and resolve local targets into accepted objects.
Construct and gate the actual action sequence against each partial state.
No reranking to a different meaning while reusing the original sentence.
Convert captured Call 2 segments into realization output; this is not a third
model call. Keep content, actions, final text and spans linked in one bundle.

Hard checks: schema, refs/scope, actual sources, base revision, capability,
consent/safety, required content coverage and consistency with observed outcomes.
Soft diagnostics: assistant-like style, social nuance, optional curiosity and
subjective phrasing. Soft diagnostics do not reject good wording or trigger
automatic regeneration. Preserve paraphrase tolerance while checking substantive
claims/requirements. Document remaining semantic validation limitations.

Optional semantic-review models run offline/background only. No hidden third
normal-path call. Typed failures must include stage and cause; do not silently
pretend deterministic fallback was model success.

Commit with snapshot/revision compare-and-set. A stale worker or superseded turn
cannot overwrite current focus/revisions or deliver obsolete output.
Persist shadow proposals without treating them as live outcomes.
Actual delivery/tool results distinguish proposal, authorization, attempt,
success, failure and supersession as applicable. Feed outcomes to next-turn
goal/workspace reconciliation.

Two calls describes one normal cognitive cycle, not an entire multistep tool task.
Future actual tool outcomes may require later reasoning cycles under explicit
authority. This migration does not add execution permissions.

### Candidate training records

Capture authorized compact inputs, raw/accepted predictions, reference maps,
context/prompt/model/schema versions, validation diagnostics, timings and actual
outcomes. Retain rejected examples and later corrections.
Apply privacy/exposure/retention policy; model labels are candidate training data,
not human ground truth. Structural acceptance does not establish behavioral quality.

### Completion gate

Full shadow path yields an accepted candidate or precise typed failure.
Legacy replay remains stable. No third realization call, hidden retries,
invented success, stale commits or stylistic veto.
Action/content/text agree and actual outcomes reach the next cycle.

## 7. Phase 5 — behavioral verification and measured latency tuning

### Input → output

Connected new-protocol shadow path →
multi-turn behavioral findings, context-conditioning evidence, latency distribution
and explicitly labeled candidate training corpus.

### Latency priorities

Reduce generated output bureaucracy first, especially the reported Call 1
2,931-token output. Reduce Call 2's reported 22,854-token input through projection,
without deleting personality/context or task content needed to reason.
Call 2 depends on StateAfter; parallel execution with stale state is not equivalent.

Starting experimental short-turn budgets:

| Component | Target range |
| --- | --- |
| Call 1 input, including instructions/schema | 2,000–4,000 tokens |
| Call 1 output | 300–700 tokens |
| Call 2 input, including instructions/schema | 2,000–5,000 tokens |
| Call 2 output | 200–700 tokens |

Artifact/complex turns may exceed these. Report omissions; never silently discard
a decisive correction, neutralize unknowns or truncate JSON to satisfy a budget.
Keep compact schemas and stable definitions before varying context to support
prefix caching where available. Measure real cache usage.

Do not cache judgments across changed state/bias/relationship. Transport streaming
can expose first-token timing but cannot bypass acceptance/delivery policy or be
reported as reduced total latency by itself. Keep provider attempts/timeouts bounded
and disclose repair calls if explicitly enabled; do not rely on repairs for the
normal two-call path.

Provisional conversational target: p50 ≤ 5 seconds, p95 ≤ 8 seconds.
This is a design target, not a benchmark result or guarantee. Report measured
p50/p95/max separately for social, information and artifact turns, with acceptance
rates and failed-attempt latency. No deterministic comparison is required.

### Required evaluation

- Uninterrupted 8–10-turn conversations covering social opening, compound request,
  missing field, inquiry answer, correction, topic switch, follow-up and closure.
  Inspect response quality, focus, terminal transitions and content revisions.
- Regression families: greeting, compound act, identity, runtime fact, information
  request, action request, ambiguity, correction, follow-up, social question,
  unsupported fact and unsafe request.
- Paired ambiguous utterances with independently changed traits, bias, state,
  familiarity or affinity. Check coherent decision differences where warranted.
- Invariance cases under the same changes: facts, negation, consent and safety.
- Context ablations: hold message constant and omit/swap a component; inspect
  reading, appraisal, behavior and words. Self-reported influence is insufficient.
- Failure cases: unavailable context, ambiguous/foreign refs, unmatched appraisal,
  stale revision/worker, timeout/truncation, blocked independent subrequest,
  failed compilation and final realization rejection.
- Full configured two-call latency and candidate acceptance by turn class.
  Human review of subjective quality; do not claim human likeness from schema tests.

### Completion gate

Actual end-to-end behavior is context-conditioned without permission/fact drift;
lifecycle remains stable, responses are useful/natural and latency is measured.
If speed remains outside target, report which calls/tokens/settings dominate
and which further tradeoffs need approval. Do not claim success because a
single run was fast or because the model says it feels an emotion.

## 8. Cross-file implementation map

| Component | Required change |
| --- | --- |
| Shared context module + appraisal/context | One sourced context snapshot; compact stage-specific views |
| two_call/contracts/provider/prompts | New versioned packets/proposals and compact response schemas |
| two_call/orchestrator | Replace duplicated payloads, future-goal guessing and preview-based instructions |
| Interpretation engine/bias/scenarios/contracts | Accepted situated readings consumed downstream; single bias owner |
| Goal engine/evidence | Actual event formation links, correction/focus and outcome transitions |
| Appraisal compiler/adapter/aggregation | Event assessments bound to actual goals, explicit unknown and deduplication |
| Pipeline/turn runners | Explicit connected types and state-before/state-after ordering |
| Requirement/workspace/collaboration | Compact readiness and relevant revision/content projections |
| Action construction/gates | Grounded candidates/templates and per-step validation, not scorer imitation |
| Captured realization/compiler/validator | One wording source, compiled spans and tolerant language boundary |
| Shadow worker/service/live_shadow | Protocol capture/replay, isolated persistence, stale-work protection |
| Latency/trace_summary | Attribution, attempts, full-path distributions and failure timings |

Preserve related collaborative-content/inquiry implementation already present.
Modify overlapping code carefully; no unrelated changes, destructive rollback,
automatic live enablement or replacement of newer work with historical examples.

## 9. Required implementing-agent deliverables

- Implementation report listing each phase's actual changes, schema/config versions
  and producer-output → consumer-input connections.
- Updated README showing actual normal call graph, context influence and authority.
- Tests for compact protocols, legacy replay, reference compilation, readiness,
  single bias ownership, per-step gates, spans and stale-worker protection.
- Behavioral report with complete uninterrupted traces and structured regressions,
  including failures and human-review limitations.
- Latency report with configured model/settings, token counts, section attribution,
  full-path p50/p95/max, accepted/failed rates and actual call counts.
- Candidate dataset format/provenance and privacy rules; no claim that LLM labels
  alone provide correct future training targets.

Report incomplete phases honestly. No rollout authority is implied by passing tests.

## 10. Official latency references

[OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)
supports shorter generated outputs and compact structured output; input reduction
alone may produce a comparatively small latency improvement.

[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
explains reuse of stable prefixes on supported models. Cache reuse concerns input
processing, not eliminating output generation time. Verify actual usage.
