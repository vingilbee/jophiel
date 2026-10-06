# Collaborative Content and Inquiry V0 — Implementation Instructions

> Evaluation scope clarification (2026-10-05): The dinner-planning slice below is a targeted artifact regression scenario, not Columbina's primary product or conversational benchmark. Columbina is a general conversational agent with identity and personality. Future overall quality evaluations must include ordinary multi-turn social, personal, practical, and factual conversation, with no requirement to create an artifact. See [the conversational test plan](../../../../../../docs/COLUMBINA_CONVERSATIONAL_TEST_PLAN.md).

Date: 2026-10-02  
Status: implementation handoff; shadow-only until separately reviewed and approved  
Scope: Cognitive V0 pipeline, live-shadow capture, trace, replay, and behavioral evaluation

## 1. Objective

Implement the missing constructive conversational path in Cognitive V0 without
replacing its existing deterministic control spine.

The completed system must be able to:

1. distinguish a response that exists inside the conversation from an external
   side effect;
2. create, inspect, explain, revise, evaluate, describe, compare, and summarize
   a shared conversational artifact;
3. preserve that artifact as typed, revisioned conversation state;
4. use user constraints, authorized dialogue, and explicitly classified
   generated proposals without representing them as verified external facts;
5. distinguish information that blocks useful progress from information that
   would merely improve a result;
6. fulfill a safe request first and optionally ask a relevant, non-blocking
   question afterward;
7. bind a later user answer to the exact open inquiry or requirement that
   elicited it;
8. commit artifact changes and satisfy goals only from validated delivered
   outcomes, never from a planned or shadow-only candidate; and
9. produce natural language while preserving semantic, attribution, safety,
   authorization, and outcome fidelity.

The initial vertical slice is the reviewed dinner-planning conversation in
Section 19. The design must remain generic enough to support plans, drafts,
outlines, comparisons, summaries, explanations, and other conversational work
without introducing a separate pipeline for each domain.

## 2. Problem being corrected

The current system has a strong conversational control plane:

```text
detect meaning
  -> form and focus a goal
  -> resolve typed prerequisites
  -> select an eligible move
  -> constrain wording
  -> persist an auditable trace
```

It does not yet have a dependable conversational work plane:

```text
identify the shared work product
  -> create useful content
  -> retain a typed revision
  -> apply corrections and constraints
  -> explain or evaluate the latest revision
  -> ask useful questions without unnecessarily blocking
  -> observe delivery and reconcile the goal
```

This produces two sequential failure modes:

1. Imperative content requests such as "revise the dinner idea" may be typed as
   external `action_request`s, encounter `missing_capability`, and be declined.
2. When conversational phrasing correctly reaches an answer goal, the goal is
   often blocked by `trusted_content_unavailable` because no producer can
   construct ordinary suggestions or bind the conversation's own delivered
   content into the answer inventory.

Realization cannot repair either upstream failure. It can only verbalize the
selected plan using the authorized inventory it receives.

## 3. Architectural boundary

Preserve the existing stage responsibilities:

```text
Perception
  -> Interpretation
  -> Goal
  -> Requirement Resolution
  -> Cognitive Turn (Appraisal, Dynamic State, Action Selection)
  -> Realization
```

Extend the pipeline to:

```text
Perception
  -> Interpretation
  -> Goal
  -> Conversational Workspace Resolution
  -> Requirement Resolution
  -> Collaborative Content Production
  -> Cognitive Turn / Action Selection
  -> Realization
  -> Delivered Outcome Reconciliation
  -> Workspace and Goal Update
```

The implementation may combine workspace resolution and collaborative content
production into one pipeline package internally, but their contracts and trace
outputs must remain distinguishable. Resolving existing state and proposing
new content have different authority and persistence rules.

## 4. Mandatory invariants

The implementation must preserve all of the following.

1. Shadow output never affects the live response, tool execution, canonical
   memory, or canonical workspace.
2. A proposed artifact revision is not a delivered artifact revision.
3. A selected action is not evidence that the action was delivered or worked.
4. User statements remain attributed user content; they do not become verified
   world facts.
5. Generated suggestions are representable, but they must not be mislabeled as
   verified facts or observed tool outcomes.
6. External side effects retain capability, authorization, consent,
   confirmation, safety, execution, and verification gates.
7. Conversational artifact work must not require an external capability merely
   because the user's wording uses an imperative verb.
8. A question must cite its purpose, target, timing, and expected use.
9. Optional curiosity never blocks the primary safe contribution.
10. A non-blocking inquiry goal never captures focus from a new explicit user
    request.
11. Previous-turn requirements are resolved only through typed linkage and
    current evidence, never through unconstrained text matching.
12. Realization never changes the selected operation, artifact revision,
    values, attribution, uncertainty, or outcome status.
13. No stage may silently fall back from a failed model provider to a different
    semantic implementation.
14. Every new contract, trace shape, snapshot, and persistence schema is
    versioned and replay-safe.

## 5. Requested-outcome semantics in Interpretation

### 5.1 Add a typed outcome distinction

Extend the code-owned Interpretation ontology and model protocol with a closed
requested-outcome classification:

```text
information_answer
conversational_artifact
external_effect
social_exchange
```

Do not infer this from Goal or Action after the fact. It is part of the
pragmatic meaning of the current user contribution.

For conversational artifact requests, validate these fields:

```yaml
outcome_kind: conversational_artifact
operation: create | revise | explain | evaluate | describe | compare | summarize
artifact_type: <registered artifact type>
target_ref: <optional dialogue/workspace reference>
constraints: <typed argument references>
requested_form: <optional registered presentation form>
```

For external effects, retain or compile the existing typed action semantics:

```yaml
outcome_kind: external_effect
requested_effect: <typed effect or bounded unresolved effect>
target_ref: <optional target>
parameters: <typed arguments>
```

### 5.2 Prefer a semantic frame compatible with the current registry

Introduce a reviewed semantic predicate such as
`collaborative_content_request`, or the equivalent name that best fits the
existing ontology. Its required argument is `operation`; artifact type and
target may be deferred only where the operation can safely continue without
them.

Do not add phrase rules for words such as "plan", "make", "revise", or
"update". The model-backed semantic parser must decide the pragmatic outcome
from the bounded conversation and code-owned schema. The deterministic
compiler must reject unknown operations, foreign evidence, invalid targets,
and malformed arguments.

### 5.3 Add reviewed contrast cases

The semantic regression corpus must include at least these contrasts:

| Conversational artifact | External effect |
| --- | --- |
| Plan a vegetarian dinner in this chat. | Place an order for dinner. |
| Draft an email to Sam. | Send the email to Sam. |
| Revise our shared dinner idea. | Edit the restaurant's online order. |
| Make a reminder checklist for me to copy. | Create a reminder in my reminders app. |
| Summarize our conversation. | Export the conversation to a file. |
| Suggest calendar times. | Create the calendar events. |

Also test indirect, polite, imperative, interrogative, hypothetical, quoted,
negated, corrected, and compound forms. The user must not need to say "do not
use a tool" for the correct distinction to be made.

### 5.4 Preserve evidence and uncertainty

The requested outcome and every artifact argument must cite current-turn
Interpretation evidence. If the outcome is genuinely ambiguous between a
conversational artifact and an external effect, retain the bounded ambiguity
and create a targeted requirement. Do not guess an external effect and decline
solely because its capability is absent.

## 6. Goal-system changes

### 6.1 Add a conversational-deliverable goal

Add a goal definition such as:

```text
interaction.fulfill_conversational_deliverable
```

Its parameters must include:

```yaml
operation: create | revise | explain | evaluate | describe | compare | summarize
artifact_type: <registered type>
artifact_ref: <optional until resolved>
source_frame_ref: <exact semantic frame>
```

This goal represents producing useful content inside the conversation. It must
not share the external capability requirement of
`interaction.handle_action_request` or `task.complete_authorized_request`.

### 6.2 Formation and adoption

Form the goal from a positive, leading collaborative-content frame. Preserve
compound acts as separate goal candidates where appropriate. Quoted,
hypothetical, negated, and weak alternative frames must follow the existing
evidence and adoption rules.

Missing artifact references that can be resolved from the workspace should be
deferred to requirement resolution rather than rejecting the goal. Missing
information that makes the requested operation meaningless must remain a
rejection or blocking condition according to the existing Goal contract.

### 6.3 Focus

A new explicit conversational deliverable is a current-turn user request and
must compete in the same current-request focus tier as other explicit requests.
An older repair, inquiry, or artifact goal must not permanently outrank it.

### 6.4 Terminal evidence

The goal is satisfied only when a validated delivered outcome proves that the
requested artifact operation was communicated to the user. A proposed patch,
selected move, generated candidate, or accepted shadow candidate is not
terminal evidence.

For example:

```text
create/revise -> delivered artifact revision with matching operation and refs
explain       -> delivered explanation bound to the requested revision
summarize     -> delivered summary bound to the requested revision
evaluate      -> delivered evaluation with preserved epistemic status
```

### 6.5 Inquiry subgoals

When the system actually asks a question whose answer is expected on a later
turn, create a linked goal or equivalent typed open-inquiry record:

```yaml
definition: interaction.understand_relevant_context
parent_goal_ref: <deliverable goal>
blocking: false
focus_eligibility: subordinate
expires_when:
  - answered
  - parent completed
  - user rejects the inquiry
  - user changes subject beyond the reviewed carry-forward policy
```

Do not create a persistent curiosity goal for rhetorical or purely social
questions.

## 7. Conversational Workspace V0

### 7.1 Responsibility

Add a `conversation_workspace_v0` package and neutral contracts. Dialogue
Working Memory records what was said. Conversational Workspace records the
versioned work product currently being developed.

Do not store the full artifact inside Dynamic State, Core Memory, relationship
state, or long-term memory. Those systems may retain refs or reviewed summaries
only according to their own policies.

### 7.2 Artifact envelope

Define an immutable artifact envelope similar to:

```yaml
artifact_id: conversation-scoped stable identity
conversation_ref: owning conversation
artifact_type: registered type
revision_id: content-addressed revision identity
revision_number: monotonic integer
parent_revision_ref: previous revision or null
status: proposed | observed | delivered | superseded | abandoned
title: optional bounded label
payload: typed payload union
source_refs: authorized interpretation, dialogue, content, and outcome refs
created_turn_ref: turn that proposed or observed the revision
delivered_outcome_ref: required only for delivered shadow-owned revisions
producer_ref: component that produced the payload
epistemic_summary: value-free content-class counts
digest: digest of every authoritative field
```

### 7.3 Typed payloads

Do not use unrestricted JSON as the authoritative payload. Use a registry of
versioned payload schemas. Implement at least:

1. a generic structured conversational artifact with sections, items,
   constraints, and annotations; and
2. a meal-plan payload sufficient for the vertical slice.

The meal-plan payload should represent at least:

```yaml
servings: integer or unresolved
dietary_constraints: typed set
allergy_constraints: typed set
time_limit_minutes: integer or unresolved
main_dish: proposal item or unresolved
side_dishes: ordered proposal items
notes: bounded attributed annotations
```

Payload fields must retain source refs and epistemic class where relevant.

### 7.4 Artifact patches

Content production must propose a typed patch against an exact base revision:

```yaml
artifact_ref: meal-plan-1
base_revision_ref: meal-plan-1#revision-2
operation: revise
set_fields: ...
remove_fields: ...
append_items: ...
source_refs: ...
```

Validate:

- exact base revision;
- allowed fields for the payload type;
- value types and closed enums;
- no loss of unrelated constraints;
- conflict detection;
- provenance for changed values;
- deterministic patch application; and
- content digest after application.

### 7.5 Persistence and concurrency

Use immutable revision rows plus a compare-and-set conversation/artifact head,
following the existing Dialogue Working Memory durability pattern. Add database
migrations, ownership constraints, retention behavior, replay support, and
stale-worker protection.

No worker may overwrite a head if the expected base revision has changed. A
conflict must yield a typed reconciliation/retry result, never last-write-wins.

### 7.6 Shadow authority modes

Implement three explicit modes:

1. `observational_shadow`: may read captured delivered conversation content;
   shadow proposals remain trace-only and never advance the canonical head.
2. `simulated_authority`: offline evaluation may feed a reviewed shadow output
   back as delivered input in an isolated simulated conversation.
3. `live_authority`: reserved for a separately approved rollout; only a real
   delivered outcome may advance the head.

Never treat an `accepted_shadow` candidate as delivered in observational mode.

## 8. Content classes and epistemic policy

### 8.1 Replace the trusted-fact-or-gap binary for creative work

Add explicit authorized content classes:

```text
verified_fact
trusted_runtime_observation
user_supplied_constraint
attributed_user_claim
delivered_dialogue_content
artifact_content
generated_proposal
reasoned_inference
uncertain_suggestion
tool_result
knowledge_gap
```

Each class must define:

- who or what produced it;
- whether it may support factual assertion;
- whether attribution is mandatory;
- whether it may update an artifact;
- whether it may be presented as a suggestion;
- whether external verification is required;
- sensitivity and authorization scope; and
- permitted Realization modalities.

### 8.2 Generated proposals are not verified facts

Permit statements such as:

> Rice could work as an inexpensive side.

as a generated proposal or uncertain suggestion. Do not treat the statement as
a verified current-price claim.

Claims such as exact nutrient amounts, current prices, legal conclusions,
medical advice, tool outcomes, or external state changes retain their relevant
trusted-source requirements.

### 8.3 User constraints

The user's instruction "make it dairy-free" may directly constrain the shared
artifact while remaining attributed user input. It does not require a producer
to prove that the preference is true. If the user claims an allergy, preserve
it as an attributed user safety constraint; do not infer diagnosis or severity.

## 9. Collaborative Content Production V0

### 9.1 Stage placement

Add `collaborative_content_v0` after Goal and workspace/requirement resolution,
before Action Selection receives its grounded content inventory.

The stage is constructive reasoning, not final wording and not external tool
execution.

### 9.2 Input contract

The request should contain only bounded, authorized inputs:

```yaml
cognitive_turn_id
conversation_ref
goal_ref
requested_operation
semantic_frame_refs
current_artifact_snapshot
resolved_requirements
authorized_dialogue_items
user_constraints
trusted_content_records
personality deliberation profile
relationship projection, if separately authorized
language tag
```

Raw messages may be included only as bounded, attributed dialogue evidence
under the same privacy and exposure policy used by live shadow. They are
instructions/context, not automatically trusted factual content.

### 9.3 Output contract

Return strict structured output:

```yaml
content_proposal_id
operation
artifact_patch or artifact_snapshot
content_items
supporting_explanations
inquiry_opportunities
source_refs
epistemic_classes
uncertainties
provider_provenance
```

No final user-facing prose belongs in this output.

### 9.4 Provider boundary

Use a separate provider adapter and protocol from semantic Interpretation and
Realization. Record model, protocol, compiler, schema, sampling, storage,
timeout, and retry provenance.

The provider may propose content, but a deterministic compiler must reject:

- unknown artifact types or operations;
- foreign source refs;
- malformed patches;
- unsupported verified claims;
- fabricated tool or delivery outcomes;
- changes inconsistent with explicit user constraints;
- silent removal of prior constraints;
- unauthorized sensitive content; and
- inquiries without a typed purpose and expected use.

Provider failure must become a typed content-production failure or gap. It must
not silently fall back to Realization reasoning.

## 10. Requirement Resolution changes

### 10.1 Requirement criticality

Extend requirement records with a closed criticality:

```text
blocking
quality_improving
optional_curiosity
```

Only `blocking` requirements make the primary deliverable goal unready.

### 10.2 Conversational deliverable manifest

Declare prerequisites by operation. Examples:

```text
create:
  artifact type
  minimally sufficient user objective
  required safety constraints for the domain

revise:
  exact artifact/base revision
  typed requested change

explain/evaluate/describe/summarize:
  exact artifact revision or authorized delivered content target
```

Do not declare an external capability prerequisite for these operations.
Content-provider availability is not the same thing as external execution
capability.

### 10.3 Cross-turn requirement records

The current implementation restricts resolution to goals created in the
current turn. Replace that limitation with explicit persistent pending records,
not by simply removing the turn check.

Persist or carry forward:

```yaml
pending_requirement_id
goal_ref
originating_frame_ref
argument_or_question_target
elicitation_move_ref
elicitation_delivery_ref
status
created_turn_ref
expiry_policy
```

Interpretation or Dialogue Working Memory must link a later answer to the exact
open inquiry/requirement. Requirement Resolution then recomputes the parent
goal's readiness using current typed evidence.

Do not satisfy old requirements through similarity search over arbitrary raw
text.

### 10.4 Content query repair

Fix delivered-dialogue content queries so a unique resolved dialogue or
artifact referent supplies the required query argument. A summary or
description request referring to the current artifact must not produce
`missing_query_argument` when the workspace contains one unambiguous active
revision.

## 11. Inquiry opportunities and timing

### 11.1 Contract

Define `InquiryOpportunity` with at least:

```yaml
inquiry_id
parent_goal_ref
target_kind
target_ref or semantic slot
question_kind
purpose
expected_use
criticality
timing: before_primary | after_primary | checkpoint
sensitivity
repeat_policy
expiry_policy
source_refs
```

Supported V0 question purposes should include:

```text
resolve_required_meaning
resolve_safety_relevant_context
discover_preference
discover_success_criterion
choose_between_tradeoffs
improve_personalization
understand_rationale
confirm_consequential_effect
```

### 11.2 Deterministic timing policy

Ask before the primary contribution when the answer materially affects safety,
correctness, authorization, or an irreversible boundary.

Ask after the primary contribution when a safe useful draft can be produced
and the answer would improve a later revision.

Ask at a checkpoint when preparatory work may proceed but a consequential
boundary must wait.

Suppress an inquiry when it is unrelated, overly sensitive for its expected
value, already answered, recently repeated, or unlikely to change behavior.

### 11.3 Personality influence

Personality may alter the score or expression of an already valid optional
inquiry. It may influence directness, warmth, curiosity threshold, assumption
disclosure, and number of optional questions.

Personality must not:

- invent a blocker;
- ask for sensitive context without a task-relevant purpose;
- override safety or privacy policy;
- turn every instruction into an interrogation; or
- create an inquiry type absent from the reviewed registry.

## 12. Action Selection integration

Reuse the current multi-move plan rather than creating a separate action
engine.

Add the smallest stable conversational action vocabulary that can carry
content proposals. Prefer a parameterized definition such as:

```text
collaborate.deliver_artifact
```

with an operation parameter over duplicating nearly identical action
definitions, unless the existing registry's evidence or safety requirements
require separate definitions.

Add or extend:

```text
seek_information.inquire
```

so it can represent blocking and non-blocking questions with explicit timing.

Example plan:

```yaml
moves:
  - action: collaborate.deliver_artifact
    role: primary
    operation: revise
    artifact_revision_ref: meal-plan-1#revision-3

  - action: seek_information.inquire
    role: supporting
    timing: after_primary
    inquiry_ref: inquiry-dairy-free-reason
```

Action eligibility must verify that every artifact revision and inquiry is
present in the authorized projection from Collaborative Content Production.

An acknowledgement never counts as performing an artifact operation. A
decline is appropriate only when the actual conversational operation is
prohibited or genuinely unavailable, not because an unrelated external
capability is absent.

## 13. Realization integration

### 13.1 Content inventory

Add closed Realization content items for:

- artifact snapshot or patch presentation;
- generated proposal;
- explanation or evaluation;
- attributed user constraint;
- uncertainty and alternatives;
- inquiry prompt and its timing; and
- delivery/outcome status.

The current user message may remain part of the bounded Realization context,
but it is not trusted content and cannot independently authorize claims.

### 13.2 Generator responsibility

Realization chooses wording only. It may combine the primary contribution and
supporting inquiry naturally, but it may not create a new artifact field,
question, commitment, factual claim, or operation.

Example authorized meaning:

```text
revision: chickpea stew is dairy-free; rice added as a suggested side
inquiry: preference versus allergy, after the revision
```

Acceptable wording:

> I've made the chickpea stew dairy-free and added rice as an inexpensive
> side. Is dairy-free a preference or an allergy? If it's an allergy, I can
> adjust the guidance for cross-contamination.

### 13.3 Validator redesign

Preserve hard semantic checks:

- required move coverage and order where order is meaningful;
- exact protected values;
- attribution;
- epistemic modality;
- no fabricated external outcome;
- no new commitment;
- no prohibited content; and
- no question absent from the plan.

Remove or recalibrate surface restrictions that reject harmless punctuation,
ordinary connective language, safe paraphrase, or proportional politeness.

Validator success remains a fidelity result, not a human-likeness score. Add a
separate behavioral rubric for usefulness, relevance, continuity, initiative,
and naturalness.

## 14. Delivered outcomes and reconciliation

### 14.1 Delivery-bound evidence

Record an immutable delivered-response outcome containing:

```yaml
outcome_id
conversation_ref
turn_ref
selected_action_ref
realization_ref
delivery_channel
delivered_content_refs
artifact_revision_refs
inquiry_refs
delivery_status
observed_at
```

Only this outcome can:

- mark the deliverable goal satisfied;
- activate an open inquiry for later answer binding; or
- advance a shadow-owned artifact head in an authority mode that permits it.

### 14.2 Reconciliation states

Support at least:

```text
delivered_and_satisfied
delivered_waiting_for_answer
delivery_failed
content_rejected
artifact_conflict
superseded
abandoned
```

The next user turn may provide evidence that the result was accepted,
corrected, rejected, or insufficient. Preserve that evidence and update the
goal/workspace rather than assuming silence means success.

### 14.3 Shadow isolation

In the current observational shadow, store proposed content and predicted
outcomes in the trace only. Do not pretend the shadow candidate was delivered.
When evaluating a continuous hypothetical shadow conversation, use the
explicit `simulated_authority` harness and mark every injected delivery.

## 15. Pipeline, capture, trace, and replay

Update the compiled request with authorized workspace inputs and exact version
refs. Add trace stages for:

```text
workspace_resolution
collaborative_content
outcome_reconciliation (when available)
```

Each stage must record:

- input lineage;
- versions;
- timings;
- value-bearing data only where permitted by the trace privacy policy;
- value-free summaries for operational review;
- deterministic digests for replayable components; and
- provider provenance and non-replayability for model-generated components.

Replay must distinguish:

- exact deterministic replay;
- semantic comparison of model-backed proposals;
- unavailable provider replay; and
- historical traces from schemas without these stages.

Historical trace parsing must remain supported. Do not reinterpret an old
trace using the new ontology or content classes.

## 16. Persistence and migrations

Add migrations only after the in-process contracts and pipeline tests pass.
Expected durable entities include:

- artifact identity;
- immutable artifact revision;
- artifact head;
- pending requirement/open inquiry;
- delivered content/outcome binding; and
- optional content-proposal trace payload if not already contained in the
  cognitive trace.

Enforce conversation/user ownership, immutable revisions, unique revision
numbers, digest integrity, compare-and-set heads, foreign-key lineage, and
retention/deletion behavior consistent with the owning conversation.

Workers must be idempotent. Retries must not duplicate artifact revisions,
open inquiries, or delivered outcomes. Reclaimed/stale workers must not commit
after losing their lease.

## 17. Versioning and documentation

Bump every changed contract, engine, manifest, compiler, runner, trace summary,
and schema version. Register historical versions for parsing and replay.

Update at minimum:

- Cognitive system `README.md` status table and architecture sections;
- Interpretation implementation documentation;
- Goal implementation documentation;
- Requirement Resolution implementation documentation;
- Action Selection implementation documentation;
- Realization implementation documentation;
- Cognitive Pipeline implementation documentation;
- Dialogue Working Memory documentation where delivered-content projection
  changes; and
- a new implementation document for Conversational Workspace and
  Collaborative Content.

Status language must distinguish implemented, tested, independently audited,
behaviorally accepted, live-integrated, and live-approved.

## 18. Implementation sequence

Implement in reviewable gates.

### Gate 1: semantic outcome distinction

- Add requested-outcome contracts and ontology.
- Add contrast corpus cases.
- Prove conversational artifact requests no longer require external
  capabilities.
- Preserve genuine external action behavior.

### Gate 2: in-process workspace contracts

- Add artifact envelope, typed payloads, patches, revisions, and digests.
- Implement deterministic patch application and conflict detection.
- Keep state in-process/test-only initially.

### Gate 3: collaborative-content provider and compiler

- Add separate structured provider protocol.
- Compile proposals into authorized content and proposed artifact revisions.
- Enforce epistemic and provenance policies.

### Gate 4: Goal and Requirement integration

- Add the conversational-deliverable goal.
- Add operation-specific requirements and criticality.
- Resolve exact workspace targets.
- Add typed carry-forward requirements and inquiry-answer binding.

### Gate 5: Action and Realization integration

- Add artifact-delivery and inquiry moves.
- Support primary contribution plus supporting inquiry.
- Add content inventory types.
- Replace surface-fragile checks with semantic fidelity checks while retaining
  hard safety and truth invariants.

### Gate 6: uninterrupted simulated-authority evaluation

- Run the dinner conversation with each accepted shadow delivery explicitly
  fed into the next turn.
- Verify artifact continuity, revision, inquiry binding, goal transitions, and
  natural output.

### Gate 7: durable workspace and outcome loop

- Add migrations, repositories, workers, retries, leases, isolation, and
  retention.
- Commit only from real delivered outcomes in an authorized mode.

### Gate 8: real-application observational shadow evaluation

- Exercise browser, WebSocket, chat worker, database, shadow worker, trace,
  content proposal, Action, and Realization.
- Confirm the shadow remains observational.
- Compare shadow proposals with the actual delivered conversation without
  conflating them.

## 19. Required vertical-slice conversation

Use an uninterrupted conversation equivalent to:

1. "Hi Columbina. I'd like to talk through dinner ideas."
2. "What vegetarian dinner would work for six people in under 30 minutes?"
3. "Why does the chickpea stew fit those constraints?"
4. "Revise our idea: make it dairy-free and add an inexpensive side."
5. "Describe the revised meal so it sounds inviting."
6. "Summarize what we've decided so far."
7. "The dairy-free requirement is because one guest has an allergy."
8. "Given that, is there anything we should change?"

The user should not need to repeat "this is only conversation" or explicitly
forbid tool use.

Expected behavior:

- turn 2 creates one meal-plan artifact;
- turn 3 explains the current revision;
- turn 4 creates a new revision rather than an acknowledgement or decline;
- turn 4 may ask after the revision whether dairy-free is an allergy or
  preference;
- turn 5 describes the latest revision;
- turn 6 summarizes the artifact rather than reconstructing arbitrary chat
  text;
- turn 7 binds to the open inquiry and updates the artifact's attributed
  allergy constraint;
- turn 8 evaluates the current artifact with appropriately cautious safety
  guidance;
- no turn requires an external execution capability;
- no older inquiry or repair goal captures focus from a new request; and
- every delivered response reconciles its corresponding goal and revision.

## 20. Test requirements

### 20.1 Contract tests

Test frozen models, closed enums, canonical ordering, uniqueness, reference
integrity, digest sealing, tamper rejection, serializer compatibility, and
historical schema parsing.

### 20.2 Interpretation tests

Test all semantic contrasts in Section 5.3, including compounds, corrections,
pronouns, quoted instructions, hypothetical language, negation, and context
leakage from prior greetings.

### 20.3 Workspace tests

Test create, revise, no-op patch, conflicting patch, stale base, preservation of
unrelated constraints, deterministic digest, concurrent writers, ownership,
retention, and deletion.

### 20.4 Requirement tests

Test blocking versus quality-improving requirements, exact artifact target
resolution, ambiguous targets, delivered-dialogue query binding, old inquiry
answer binding, expiry, rejection, and topic changes.

### 20.5 Content-production tests

Use reviewed structured model doubles. Test valid proposals, unsupported
verified claims, constraint removal, foreign refs, provider timeout, malformed
schema, unsafe content, uncertainty preservation, and no silent fallback.

### 20.6 Goal tests

Test formation, adoption, focus, parent/child inquiry relationships,
carry-forward, expiry, correction, supersession, delivered satisfaction, and
the prohibition on satisfying from a plan or candidate.

### 20.7 Action tests

Test create/revise/explain/evaluate/describe/summarize routes, primary plus
supporting inquiry, inquiry timing, acknowledgement ineligibility, and genuine
external-action behavior remaining unchanged.

### 20.8 Realization tests

Test semantic paraphrases, punctuation, politeness, artifact value fidelity,
attribution, uncertainty, move coverage, optional inquiry wording, invented
questions, fabricated outcomes, factual precision, and multi-move output.

### 20.9 Outcome and worker tests

Test delivery success/failure, idempotency, retry, reclaimed jobs, stale worker
write rejection, exact action/realization/artifact binding, simulated authority,
observational isolation, and goal/workspace reconciliation.

### 20.10 Behavioral evaluation

Grade separately:

- operational completion;
- semantic routing;
- artifact continuity;
- task usefulness;
- correction application;
- inquiry relevance and timing;
- realization fidelity;
- naturalness; and
- goal/outcome closure.

Do not use `completed` or `accepted_shadow` as a proxy for human-like behavior.
Use frozen deterministic candidates for authoritative regression and report
live-provider variation separately.

Tests must override provider configuration hermetically. They must not depend
on the developer's `.env`. Runtime-date assertions must derive the expected
date from the captured turn time and time zone rather than hard-code the day on
which the test was written.

## 21. Acceptance criteria

The implementation is ready for independent shadow audit only when:

1. all semantic contrast cases distinguish conversational artifacts from
   external effects without phrase-specific rules;
2. the required vertical slice produces one traceable artifact lineage;
3. every correction changes the intended revision and preserves unrelated
   constraints;
4. explanations, descriptions, evaluations, and summaries cite the latest
   authorized revision;
5. non-blocking curiosity never prevents the safe primary contribution;
6. answers bind to exact open inquiries and update the original parent context;
7. no goal is satisfied from planning or generation alone;
8. observational shadow never mutates canonical conversation state;
9. generated suggestions cannot masquerade as verified facts or tool results;
10. genuine external requests still fail closed when capability, authority,
    confirmation, safety, or outcome evidence is absent;
11. reviewed faithful realizations survive the validator without requiring
    unnatural punctuation or exact-template wording;
12. full focused tests pass in a hermetic offline environment;
13. database tests pass against the supported PostgreSQL environment; and
14. the behavioral report includes failures and partial results rather than
    reporting only operational completion.

## 22. Explicit non-goals

This implementation must not:

- integrate MCP or desktop execution;
- grant the shadow live-response authority;
- weaken external action authorization or safety gates;
- turn Realization into an unconstrained reasoning agent;
- treat raw user text as verified world knowledge;
- use personality as a substitute for a justified inquiry opportunity;
- store conversational artifacts in Core Memory by default;
- silently rewrite historical traces;
- add phrase-specific fixes for the evaluation corpus; or
- claim general human-like behavior from the single vertical slice.

## 23. Implementation deliverables

The implementing agent must provide:

1. versioned contracts and manifests;
2. pipeline and provider integration;
3. workspace resolution and artifact patching;
4. Goal, Requirement, Action, and Realization changes;
5. outcome reconciliation appropriate to shadow authority;
6. migrations and durable services when Gate 7 is reached;
7. focused unit, integration, persistence, replay, worker, isolation, and
   behavioral tests;
8. updated architecture and status documentation;
9. an implementation report listing exact versions, files, tests, known
   limitations, and forbidden shortcuts; and
10. a behavioral results report containing the uninterrupted trace and a
    structured contrast corpus.

Do not describe the work as behaviorally accepted or live-ready until a
separate audit verifies the acceptance criteria above.
