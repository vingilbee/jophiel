# Shadow Pipeline Evaluation Findings

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Date: 2026-09-28  
Status: evaluation handoff; findings are not implementation acceptance  
Scope: local live-shadow traces from the canonical Cognitive V0.3 pipeline

## Purpose

This document records general defects and capability gaps exposed during the
first local behavioral review. It is written as an implementation handoff, not
as a set of patches for the individual utterances that happened to reveal the
problems.

Implementations must generalize across conversational forms, sources, turns,
and supported knowledge domains. Do not add exact-string branches for the
review messages, inject expected actions, let Realization repair an incorrect
plan, or weaken evidence and safety gates to make the examples pass.

The durable trace mechanism itself remained observational. Nothing reviewed
here authorizes a shadow result to affect the live response.

## Evidence set

The following local traces exposed the findings:

| Trace | Useful evidence |
| --- | --- |
| `d27a1112-d500-5ae6-88f3-06e759a0a729` | Compound social opening became an unknown interpretation; acknowledgement selected; provider request unsupported |
| `be031404-5218-5fc3-9856-092dd889c6db` | Direct test-database deletion invalidated a dialogue-memory source prefix; compilation failed closed and later reset correctly |
| `ddce526b-826e-5456-96a1-8e938043bf20` | Question form recognized, subject remained unknown, answer goal rejected for missing grounding, acknowledgement realized |
| `fcff0e1a-6fc6-5bc7-82ae-525d5f099a28` | Same grounding/fallback limitation in another knowledge domain; prior turn obligation remained active; focus evidence cited the older obligation |
| `eb1ba05e-5ef1-5d11-887b-99f6585e479a` | Compound frames were preserved, but a global action-request cue overrode an information-request frame; valid social contact was rejected; acknowledgement again won |

Trace identifiers are diagnostic references, not fixtures or expected-output
inputs. Do not read these rows from production code or make behavior depend on
their contents.

## Priority summary

| ID | Priority | Classification | Summary |
| --- | --- | --- | --- |
| SH-EVAL-001 | P0 | Confirmed integration defect | Turn-scoped response obligations accumulate as active goals across turns |
| SH-EVAL-002 | P0 | Confirmed provenance defect | Focus evidence can cite an older response obligation instead of the current/focal one |
| SH-EVAL-003 | P1 | Confirmed semantic-coverage defect | Semantic grounding is mutually exclusive and exact-form-oriented, so compound acts collapse to unknown |
| SH-EVAL-004 | P1 | Confirmed perception-coverage defect | Embedded or unpunctuated interrogatives are missed by question-form detection |
| SH-EVAL-005 | P1 | Missing trusted-producer boundary | Common answerable questions lack authorized identity, runtime-fact, and reviewed-knowledge projections |
| SH-EVAL-006 | P1 | Behavioral policy defect | Missing grounding causes a generic acknowledgement instead of a response that addresses the blocking unknown |
| SH-EVAL-007 | P1 | Provider compatibility defect | The provider adapter does not own or record model-specific request compatibility |
| SH-EVAL-008 | P2 | Behavioral evaluation gap | Realization validation can accept faithful but unnatural or contextually useless wording |
| SH-EVAL-009 | P2 | Evaluation observability defect | Failed trace summaries hide the safe failure envelope and render empty stage sections |
| SH-EVAL-010 | P2 | Evaluation observability defect | Summaries hide target and goal lineage needed to distinguish legitimate multiplicity from duplication |
| SH-EVAL-011 | P2 | Status-communication defect | `accepted_shadow` is easy to misread as behavioral approval |
| SH-EVAL-012 | P0 | Confirmed interpretation-routing defect | A surface action-request cue overrides incompatible semantic-frame evidence and misclassifies information requests as external actions |
| SH-EVAL-013 | P0 | Confirmed Goal adoption defect (remediation implemented 2026-09-29, audit pending) | Candidate support is rejected when any linked hypothesis is below strong, even when the ontology intentionally uses moderate support |

## SH-EVAL-001: stale turn-scoped goals remain active

### Observed behavior

A later goal snapshot contained two active
`interaction.discharge_response_obligation` records. Each had a different
turn scope and `created_turn_ref`; the older turn's obligation remained active
and was emitted as a non-focal active goal.

Rejected goals from prior turns may remain as immutable historical records.
The defect is specifically that a completed earlier response episode leaves a
turn-scoped goal active.

### General impact

- active-goal count grows with conversation length;
- stale goals participate in focus ordering, conflict checks, projections, and
  downstream scoring;
- behavior can depend on generated goal identifiers rather than the current
  conversational episode;
- long conversations can hit bounds or fail validation for reasons unrelated
  to the current turn; and
- evaluation results become contaminated by integration bookkeeping.

### Required invariant

At the start of a new response episode, no active or suspended goal scoped to a
different completed turn may remain active. Scope closure is not success. A
prior unresolved turn goal must become `expired` unless separately authorized
evidence supports another legal terminal transition.

Historical predecessor snapshots and terminal records must remain immutable.

### General implementation direction

Add an explicit turn-rollover reconciliation boundary before the new turn's
goals are ranked. That boundary must receive server-owned proof that the prior
response episode ended and must target every non-terminal goal whose
`scope="turn"` and `scope_ref` differs from the new cognitive turn.

The reconciliation may be implemented as a dedicated trusted-scope-closure
update or as a versioned extension of response-turn reconciliation. In either
case, it must use ordinary Goal transitions and produce `expire` transitions;
it must not edit the predecessor snapshot, delete old records, or manufacture
satisfaction evidence. The new response-turn update should then create its new
obligation against the reconciled snapshot.

Likely owners:

- `goal_v0/engine.py` and `contracts/goals.py` for lifecycle semantics;
- `cognitive_pipeline_v0/runner.py` for ordering the closure and response
  update, if both occur inside one canonical pipeline execution; and
- `live_shadow/compiler.py` for projecting only trusted, captured closure
  facts rather than inferring closure from arbitrary conversation text.

Because this changes cross-turn semantics, bump the affected Goal policy and
pipeline runner versions. Preserve replay support for stored traces under the
old versions or return a typed version-unavailable result.

### Acceptance coverage

- Run at least five consecutive response turns with no outcome producer.
- After every update, exactly the current turn's required response obligation
  is active.
- Every earlier unresolved turn obligation is expired, not satisfied.
- Rejected and expired records remain in canonical record order for audit.
- Conversation- and task-scoped goals are not expired merely because a new
  turn began.
- Replays and hash-seed variants produce identical transitions.

## SH-EVAL-002: focus evidence uses the wrong response obligation

### Observed behavior

The focus decision named the current response obligation as its primary goal,
but `evidence_refs` cited the older turn's obligation. The current engine finds
the first response-obligation record in record order rather than resolving the
obligation owned by the current/focal turn.

### General impact

This breaks provenance even when the selected primary goal is correct. Review,
replay, later learning, and error attribution can associate a decision with the
wrong source turn.

### Required invariant

Every focus evidence reference must justify the recorded focus decision. When
the response obligation is the primary or supporting focus, its evidence must
come from that exact goal. No lookup may silently select an arbitrary record
because definitions match.

### General implementation direction

Remove definition-only lookup from focus provenance. Resolve evidence from the
exact `GoalRecord` referenced by `primary_goal_ref` and each
`supporting_goal_ref`. If a separate current-turn response obligation must be
cited as the coordination floor, resolve it by all of:

- active status;
- `scope="turn"`;
- `scope_ref == request.cognitive_turn_id`; and
- the expected response-obligation definition.

Fail validation when there are zero or multiple matches. Extend Goal snapshot
validation so every focus evidence reference belongs to a focal goal or to an
explicitly typed coordination-floor goal. This prevents a future refactor from
reintroducing record-order dependence.

Primary owner: `goal_v0/engine.py`, with contract-level ownership checks in
`contracts/goals.py` and adversarial coverage in the Goal focus tests.

### Acceptance coverage

- Use adversarial record orders with several historical response obligations.
- Vary generated goal identifiers so stable-id ordering cannot mask the issue.
- Assert exact goal-to-evidence ownership for primary, supporting, and
  non-focal projections.
- Include snapshots containing historical rejected, expired, satisfied, and
  active records with the same definition key.

## SH-EVAL-003: semantic grounding does not compose conversational acts

### Observed behavior

The V0 grounder primarily uses one mutually exclusive `if`/`elif` chain. Some
social forms require a full-string match. An utterance containing more than one
ordinary act can therefore fail every narrow form and become one generic
`unknown` frame.

### General impact

- greeting plus question, acknowledgement plus request, correction plus
  request, and other compound contributions lose one or all acts;
- harmless addressees, discourse markers, spelling variation, and punctuation
  variation can erase an otherwise recognizable act;
- Goal, Appraisal, Action, and Realization then fail coherently on a bad
  upstream representation; and
- downstream weight tuning would hide rather than repair the defect.

### Required invariant

Grounding must support multiple compatible frames from one contribution and
must preserve clause/span evidence for each frame. Recognition of one act must
not prevent recognition of another compatible act. Lexical variation must be
handled by reviewed general rules, not by enumerating evaluation sentences.

Unknowns should be local to unresolved content. A recognized act plus an
unresolved argument must not collapse the entire message to an unknown act.

### General implementation direction

Replace the single exclusive grounding cascade with a versioned collection of
grounding rules. Each rule should declare:

- which clause/span it consumes;
- the predicate and typed arguments it can emit;
- evidence and quote-ownership requirements;
- whether it is compatible with other matched rules;
- its precedence only when two rules compete for the same semantic span; and
- the unknowns it emits when only an argument is unresolved.

Run all compatible rules, then perform deterministic conflict resolution and
canonical deduplication. Preserve separate frames for separate acts. A generic
unknown frame should be added only for an unresolved semantic region, not as a
replacement for already grounded regions.

Do not build a broad unreviewed natural-language parser in this step. Keep the
V0 approach deterministic and inspectable, but make the rule architecture
compositional. Update the predicate manifest, scenario compatibility rules,
and interpretation design-case validator together. A semantic-grounder version
bump is required.

Primary owners: `interpretation_v0/semantic_grounding.py`,
`interpretation_v0/ontology.py`, `interpretation_v0/rules.py`, and the reviewed
interpretation design cases.

### Acceptance coverage

Create contrasting design cases for:

- one act alone versus the same act with an addressee;
- two compatible acts in either order;
- punctuation-present and punctuation-omitted forms;
- benign spelling and casing variation;
- quoted acts that must not be attributed to the user; and
- genuinely ambiguous forms that must remain unresolved.

Assert exact evidence spans and confirm personality cannot manufacture a
missing act.

## SH-EVAL-004: question detection is position- and punctuation-dependent

### Observed behavior

Question-form detection currently relies on an owned question mark or a
question expression at the beginning of the whole message. An embedded
interrogative without terminal punctuation is missed when preceded by a
greeting, addressee, acknowledgement, or discourse marker.

### Required invariant

Question structure should be detected at the clause level while retaining the
existing quote-ownership protections. Punctuation may support detection but
must not be the only evidence. Embedded quoted questions must not become user
questions.

### General implementation direction

Introduce a deterministic, quote-aware clause scan shared by question-form
detection and semantic grounding. It should locate candidate interrogative
clauses after permitted discourse prefixes and addressees, while distinguishing
direct user speech from quoted or malformed quotation spans.

The detector should return evidence for the actual question clause rather than
the entire message when possible. Multiple clauses may produce one Boolean cue
with multiple evidence spans; detailed question objects belong downstream, not
inside the Boolean Perception cue.

Keep false-positive controls for relative clauses and non-interrogative uses of
question words. Bump the deterministic detector bundle and update the cue
manifest provenance without changing the meaning of historical events.

Primary owners: `perception_v0/detectors.py`, its evidence helpers, and the
Perception design-case fixture.

### Acceptance coverage

- leading, embedded, and trailing question clauses;
- punctuation present and absent;
- direct versus quoted questions;
- question words used non-interrogatively;
- multi-question contributions; and
- combinations with other conversational acts.

## SH-EVAL-005: trusted answer-content producers are missing

### Observed behavior

The system can detect an information request and propose a high-priority answer
goal, but rejects that goal when the subject and answer content have no trusted
typed producer. The live-shadow capture intentionally omits many runtime,
identity, reviewed-knowledge, tool-result, and long-term-memory inputs.

The rejection is correct under the current contract. The missing producer
coverage is the rollout blocker.

### General impact

Common, safely answerable questions become unknown. Action Selection receives
no grounded question/answer pair, and Realization is correctly forbidden from
using model knowledge to fill the gap.

### Required invariant

Every supported answer domain needs:

1. a typed semantic query or proposition;
2. an authorized, versioned producer;
3. turn-time provenance and freshness semantics;
4. an explicit unavailable/unknown representation;
5. a projection into Action grounded objects; and
6. a closed-world Realization content item.

Do not expose raw CoreMemory JSON, mutable post-capture state, provider tokens,
or unreviewed model knowledge. Dynamic facts must be captured at a defined
turn-time boundary. Identity facts must come from a reviewed identity contract,
not from personality metrics or a persona prompt.

### General implementation direction

Define small producer protocols by authority class rather than one generic
dictionary:

- immutable application identity facts;
- captured runtime facts with observation time and freshness;
- authorized reviewed knowledge;
- delivered dialogue claims with attribution;
- authorized tool results; and
- explicit knowledge gaps.

Each producer should return closed, versioned contracts with owner, source,
authorization, revision, sensitivity, and temporal provenance. Capture the
minimum required source at turn time, then compile it into the existing typed
Action reference index and closed-world Realization inventory. Producers must
be independently testable and must not import the model provider.

Add a query-to-producer capability manifest so unsupported semantic queries
remain explicit. Do not treat an empty producer result as evidence that a fact
does not exist. Do not pass full database objects, raw CoreMemory, or entire
transcripts into Realization.

Likely owners include new pure producer modules plus versioned projections in
`live_shadow/compiler.py`, Action grounded-object construction, and Realization
content projection. This work requires privacy and authorization review before
adding any sensitive source class.

### Acceptance coverage

For each producer class, test authorized-known, unavailable, stale,
unauthorized, conflicting, corrected, and cross-user inputs. Known content must
permit an answer; unavailable content must yield a grounded knowledge gap or a
targeted clarification; unauthorized content must remain inaccessible.

## SH-EVAL-006: fallback action does not address the blocking unknown

### Observed behavior

When an information-request goal is rejected for missing grounding, the generic
system response obligation becomes focal. Acknowledgement then scores above
clarification and is realized as a receipt statement that does not address the
request.

### General impact

The system can be safe and internally consistent while still producing a
non-responsive answer. The user receives neither the requested information nor
an explanation or question that could unblock it.

### Required invariant

When the leading current-turn act is a request and its user goal is blocked by
a typed missing input, the primary plan must address that blocker. Depending on
the typed state, the valid behavior is a targeted clarification, a bounded
knowledge-gap disclosure, an authorization request, or another registered
repair action. Generic receipt acknowledgement alone must not count as direct
coverage of the blocked user goal.

Do not solve this by giving Realization permission to answer outside the plan.

### General implementation direction

Carry the reason a user goal was rejected or blocked into the Action handoff as
a typed, evidence-linked planning condition. The Action constructor should be
able to propose a move whose target is the exact missing argument, unavailable
source, authorization requirement, or knowledge gap.

Define direct-goal-coverage semantics so a generic acknowledgement covers only
receipt/attention goals. It must not receive full progress credit for an
unanswered request merely because a response is required. Add or activate only
registered actions whose effects truthfully describe clarification, limitation
disclosure, or repair.

Apply hard gates before tuning scores. Once the correct candidates and coverage
features exist, calibrate their relative utility on reviewed cases. Do not fix
this solely by lowering the acknowledgement weight, because that would damage
legitimate acknowledgement contexts without representing the blocker.

Primary owners: Goal-to-Action projection, `action_selection_v0/construction.py`,
`features.py`, the action registry/manifest, and reviewed action design cases.

### Acceptance coverage

- Missing argument, missing trusted fact, missing authorization, conflicting
  fact, and unsupported-domain cases.
- Verify that the chosen move targets the exact blocker.
- Verify that an answer remains preferred when all required grounding exists.
- Verify that acknowledgement can still be selected for genuine receipt acts.
- Test the policy across different personality profiles without changing hard
  eligibility.

## SH-EVAL-007: provider request compatibility is not modeled

### Observed behavior

The adapter sent a request parameter combination rejected by one configured
model. The failure was safely typed as `provider_unsupported`, but the
configuration passed startup and failed only after a live shadow request.

The local evaluator switched to a compatible model configuration. The adapter
still lacks a general capability contract for provider/model request features.

### General impact

- a syntactically valid model id can fail every Realization attempt;
- changing the configured model can silently change supported parameters;
- provider-affecting settings may be absent from persisted provenance; and
- `provider_unsupported` is too broad to distinguish configuration
  incompatibility from output-shape failures during evaluation.

### Required invariant

The configured model and endpoint must be validated against the request
features the adapter will use before shadow evaluation begins. Every
behavior-affecting provider option must be explicit and persisted in model
provenance. Unsupported configurations must fail closed with a stable,
content-free configuration code.

Do not add an implicit model fallback.

### General implementation direction

Create a versioned provider-capability description used when constructing the
model adapter. It should declare support for the selected endpoint, strict
structured output, custom sampling parameters, reasoning controls, token-limit
parameter names, storage controls, and any other option used by the protocol.

Validate the configured model against that description during adapter creation
or worker startup. Construct request parameters from the validated capability
set instead of sending one universal parameter bundle to every model. Record
the resolved endpoint and every behavior-affecting option in
`RealizationModelProvenance`.

If a provider introduces semantics not representable by
`realization-model-protocol-v0.1`, define a new protocol version rather than
silently changing historical behavior. Keep provider error bodies out of
traces and ordinary logs; add a stable configuration-incompatible code for
failures detected before the API call.

Primary owners: `realization_provider.py`,
`contracts/realization.py`, provider protocol tests, and configuration
validation.

### Acceptance coverage

- capability-supported and capability-rejected model configurations;
- exact persisted provider provenance;
- no API call for a locally detectable incompatible configuration;
- typed timeout, transport, refusal, truncation, and schema failures; and
- historical replay reports version/configuration unavailable rather than
  silently using different provider semantics.

## SH-EVAL-008: Realization acceptance does not measure conversational quality

### Observed behavior

The deterministic validator accepted wording that covered the selected move and
violated no closed-world constraint, but the wording was mechanical and, due to
the upstream plan, contextually useless.

This is not a validator escape: the candidate faithfully realized the wrong
plan. It does reveal that `accepted_for_shadow_review` is not a naturalness,
helpfulness, or task-success result.

### Required invariant

Keep deterministic safety and fidelity validation separate from behavioral
quality evaluation. Do not weaken the existing validator to improve fluency.
Add a reviewed evaluation layer capable of labeling at least:

- action-to-user-intent appropriateness;
- plan coverage;
- naturalness and conversational register;
- unnecessary meta-language;
- concision;
- task success or useful recovery; and
- evaluator uncertainty and disagreement.

Human review remains the strongest evidence. An AI quality evaluator must keep
its model/version provenance and cannot promote a candidate by itself.

### General implementation direction

Build an offline/local review record that references the immutable trace and
candidate digest without rewriting either. Store rubric dimensions separately
from validator violations. Reviewer identity, review time, rubric version,
labels, confidence, and adjudication state must be explicit.

Use reviewed results to revise action selection, style controls, protocol field
descriptions, or model choice according to the attributed failure. Do not put a
free-form quality judge in the live generation path and do not let the same
generation call grade itself.

For wording-specific calibration, compare multiple candidates for the same
authorized plan offline. For plan-appropriateness failures, fix the upstream
stage rather than teaching the realizer to deviate from the plan.

### Acceptance coverage

Use held-out paraphrases and several domains. Include candidates that are safe
but stiff, fluent but plan-inconsistent, helpful but unsupported, concise and
correct, and appropriately uncertain. Report fidelity and quality separately.

## SH-EVAL-009: failed summaries hide the failure envelope

### Observed behavior

For a terminal `failed` trace, `trace_summary` returned empty Perception,
Interpretation, Goal, Appraisal, State, Action, and timing sections. It did not
show the stored safe failure stage or code, making the trace appear as if every
stage produced empty output.

### Required invariant

Every failed summary must expose the content-free terminal failure fields:

- stage;
- stable code;
- retryable flag;
- attempt count;
- nested stage/phase when present; and
- safe message, if it is part of the approved terminal envelope.

The summary must clearly distinguish `not_run` from a stage that ran and
returned an empty result. It must not print raw exceptions or payload content.

### General implementation direction

Branch `summarize_trace()` first on `terminal_status`:

- `failed`: emit the safe failure projection and mark downstream sections
  `not_run`;
- `skipped`: emit the skip reason and capture/version metadata;
- `completed`: summarize the validated pipeline and optional Realization; and
- invalid status/payload combinations: fail the summary command rather than
  inventing empty values.

Keep the extractor pure and accept both ORM rows and mappings, as it does now.
Add a closed summary schema or snapshot tests so fields cannot disappear
silently. Default output must remain content-minimized.

Primary owners: `trace_summary.py` and
`tests/test_cognitive_shadow_trace_summary.py`.

### Acceptance coverage

Cover capture, compile, each pipeline stage, persistence, skipped, and completed
terminals. Assert that no conversation text, evidence text, model prompt, or
candidate wording appears in default output.

## SH-EVAL-010: summaries omit decision identity and lineage

### Observed behavior

Two clarification candidates with different unknown targets appeared as
duplicate action/score rows. Historical and current goal records with the same
definition appeared duplicated because the summary omitted goal identity,
scope ownership, and creation turn.

The stored trace was correct; the summary removed information necessary for
behavioral diagnosis.

### Required invariant

The content-minimized summary should retain non-content decision structure:

- candidate id or stable short reference;
- target types, roles, and safe target keys;
- selected-versus-unselected state;
- goal id, scope, status, creation turn, and current/historical classification;
- focal, supporting, and non-focal designation; and
- rejection/transition rule codes.

Default output should continue to exclude candidate text. A separate explicit
local-only option may reveal final candidate wording for human review, but it
must never become a public endpoint or ordinary log field.

### General implementation direction

Extend summary projections with safe structural identifiers and provenance,
not payload excerpts. Candidate rows should include a stable short id and
target descriptors sufficient to distinguish two bindings of the same action.
Goal rows should expose record identity, scope ownership, created turn, focal
role, and whether the record is current or historical.

Keep candidate wording behind an explicit command-line flag with a prominent
local-sensitive-data warning. The flag should print only the final candidate
for the selected trace, never captured messages, prompts, rejected candidates,
or evidence text. Do not add HTTP serialization.

### Acceptance coverage

Test distinct candidates sharing an action key and score, repeated goal
definitions across turns, historical terminal records, and several active
goals. A reviewer must be able to distinguish multiplicity from duplication
without loading raw payloads.

## SH-EVAL-011: shadow acceptance terminology is ambiguous in summaries

### Observed behavior

Reviewers interpreted `accepted_shadow` / `accepted_for_shadow_review` as a
quality result. In the current contract it means that the candidate passed
schema, ownership, move coverage/order, grounding, epistemic fidelity,
constraint, style-bound, and final-integrity validation.

### Required invariant

Evaluation output must state separately:

```text
constraint/fidelity validation: passed | failed | not_run
behavioral quality review: passed | failed | not_reviewed
live approval: never_granted_by_shadow
```

Do not rename historical stored statuses in place. Clarify their meaning in
summary projections and review tooling.

### General implementation direction

Add derived review fields to the summary rather than changing persisted status
enums. Map terminal and validator state into clearly named axes such as
`fidelity_validation`, `behavioral_review`, and `live_authority`. The final axis
must always report no live authority for shadow traces.

Documentation and review UI/CLI labels should use the same vocabulary. Tests
should prove that an accepted candidate with no human review reports
`behavioral_review=not_reviewed`.

## SH-EVAL-012: surface request cues override semantic request type

### Observed behavior

The compositional grounder correctly emitted separate social-opening and
information-request frames. Perception also emitted the broad
`conversation.explicit_action_request` cue because the utterance used a modal
request construction. Interpretation then treated the cue as globally
authoritative: it emitted a strong `request_action`, rejected
`request_information`, and produced a `cause_action` speaker goal even though
the request's semantic object was information.

The selected acknowledgement targeted the misclassified action-request act.
This is not a missing-content-producer problem; it is a disagreement between
surface syntax and grounded semantics that is resolved in the wrong direction.

### General impact

- requests expressed with verbs such as tell, explain, show, check, find, or
  summarize can be routed as external actions even when their object is
  information;
- a broad Perception cue can erase more specific Interpretation evidence;
- capability, authorization, Goal, and Action policies receive the wrong
  request category;
- compound-message support appears implemented while downstream behavior still
  follows a global single-label shortcut; and
- adding more producer coverage will not fix the route because the information
  act is already rejected.

### Required invariant

Perception cues describe observable form; they do not unilaterally determine
semantic act type. Interpretation must resolve request type from the cue plus
the grounded predicate, semantic object/effect, and clause-local evidence.

An information-request frame cannot be rejected merely because the same clause
uses polite imperative or modal syntax. An external action request requires a
grounded non-communicative effect, task, operation, or other registered action
object. If both readings remain plausible, retain explicit alternatives rather
than forcing the information reading to unsupported.

### General implementation direction

Make request classification frame-local. Replace the global `if action: ...
reject request_information` branch with a versioned decision table over:

- clause-owned request-form cues;
- grounded predicate class;
- semantic target kind;
- requested effect or information topic;
- available capability/task context; and
- unresolved arguments.

Bind each hypothesis to only the semantic frames and cue evidence that support
that clause. Define predicate classes such as informational, operational, and
ambiguous in the ontology manifest rather than maintaining overlapping verb
lists in several stages. A surface verb may appear in more than one class; its
typed object and frame decide the route.

Do not weaken genuine action-request detection. Requests for an external side
effect must continue to require capability and authorization downstream.

Primary owners: `perception_v0/detectors.py` for cue semantics,
`interpretation_v0/ontology.py` for predicate classes, and
`interpretation_v0/rules.py` for frame-local hypothesis construction. Bump the
feature/rule manifest versions.

### Acceptance coverage

- Same surface verb with an informational object and with an operational
  object.
- Modal, imperative, and indirect request forms.
- One utterance containing independent information and action requests.
- Ambiguous cases that retain both hypotheses.
- Quoted and hypothetical requests.
- Assertions containing an action verb that are not requests.
- Exact frame and cue ownership for every emitted hypothesis.

Downstream assertions must prove that information cases can form answer goals,
action cases can form task goals, and ambiguous cases cannot silently gain
authorization.

## SH-EVAL-013: Goal adoption treats moderate support as insufficient globally

### Observed behavior

The Goal engine correctly proposed `interaction.respond_to_social_contact` from
a strongly supported greeting act, a moderately supported
maintain-social-connection goal, and a grounded dialogue scope. It then rejected
the candidate under `goal-reject.insufficient-interpretation-support.v1`.

The adoption implementation marks a candidate weak when *any* relevant linked
hypothesis is not `strong`. The accepted Interpretation ontology deliberately
uses `moderate` support for ordinary social-connection goals, so an explicit
greeting can satisfy the documented formation rule yet be systematically
rejected during adoption.

### General impact

- valid candidates become impossible when their reviewed hypothesis profile
  contains moderate support;
- ontology support semantics and Goal adoption semantics contradict one
  another;
- greeting, affiliative, ambiguous-but-actionable, and other bounded social
  behavior is suppressed in favor of the generic response obligation; and
- downstream scoring cannot recover because the intended goal is terminally
  rejected before Action Selection.

### Required invariant

Goal adoption thresholds must be definition- and role-specific. Required
hypotheses, accepted support bands, allowed dispositions, grounding
requirements, and contradiction conditions must be declared by the formation
policy for that goal definition.

`moderate` is not globally synonymous with insufficient. A candidate is weak
only when a required input falls below the reviewed threshold for that
candidate. Optional or corroborating hypotheses cannot veto a strongly
grounded candidate unless the policy explicitly declares them gating.

### General implementation direction

Replace the global `any(h.support != "strong")` adoption test with a versioned
support-requirement manifest keyed by goal definition and required hypothesis
role. Each formation rule should declare, at minimum:

- required communicative-act hypotheses and minimum support;
- required speaker-goal hypotheses and minimum support;
- allowed leading/alternative dispositions;
- required semantic frames and resolved arguments;
- disqualifying modality or contradiction evidence; and
- whether missing optional evidence reduces priority or blocks adoption.

The engine should evaluate the candidate's own linked frames and hypotheses
against that manifest and persist the failed requirement when rejecting. Do
not special-case a single social action in procedural code; other goal
definitions need auditable thresholds too.

Primary owners: Goal formation/adoption policy contracts,
`goal_v0/engine.py`, `goal_v0/rules.py`, and reviewed Goal design cases. Bump
the Goal formation/lifecycle policy versions if their semantics change.

### Acceptance coverage

- Minimum accepted support and one-band-below rejection for every goal
  definition.
- Mixed strong/moderate hypothesis profiles with required and optional roles.
- Retained alternatives that are allowed for reversible, low-risk goals.
- Quoted, hypothetical, contradicted, and unresolved cases that must reject.
- Compound acts whose unrelated moderate hypothesis cannot reject another
  act's candidate.
- Invariance under hypothesis and record reordering.

### Remediation status (2026-09-29)

**Implemented; independent audit pending.** The global test is gone.
`goal_v0/adoption_policy.py` (`goal-adoption-policy-v0.1-draft`) declares a
closed, validated policy for each of the ten definitions, evaluated over the
candidate's own act and frame-linked hypotheses only; optional requirements
never veto. `interaction.respond_to_social_contact` requires `act.greet` ≥
strong and `goal.maintain_social_connection` ≥ moderate, so the observed
greeting is adopted. Rejections persist a structured `rejection_detail`
(`goal-contract-v0.3-draft`). Coverage: `tests/test_goal_v0_adoption_policy.py`
exercises minimum and one-band-below support for every required role of every
definition, required versus optional roles, dispositions, quoted/hypothetical,
contradiction, unresolved arguments, authorization, safety, parent adoption,
compound acts, and ordering invariance. Deliberate v0.1 choice: every required
role accepts only `leading` hypotheses (retained alternatives count only for
optional corroboration); the manifest supports per-role dispositions if a
reviewed policy later allows alternatives for low-risk goals. See
[`GOAL_V0_IMPLEMENTATION.md` §26](GOAL_V0_IMPLEMENTATION.md#26-remediation-v03-sh-eval-013--sh-eval-014).

## Watch item: transient provider-internal retry

Trace `eb1ba05e-5ef1-5d11-887b-99f6585e479a` recorded a first-attempt
`provider_internal_error` followed by a successful second attempt. The retry
boundary worked and no raw provider detail entered the trace. One occurrence is
not enough to classify the cause.

Track aggregate frequency by provider/model/adapter version. If it recurs,
identify the protected exception class and map expected SDK/provider failures
to an existing stable code or a reviewed new code. Do not log response bodies,
generated text, request identifiers, or credentials. A high internal-error
rate is a provider-adapter defect even when retries eventually succeed.

## Correct behavior observed; do not "fix"

The next implementation agent must preserve these behaviors:

1. **Direct database deletion invalidated dialogue memory.** The source-prefix
   digest mismatch was correct. Capture failed closed, and a later dialogue
   update created an explicit `source_prefix_changed` reset. Application code
   does not expose individual message deletion in the reviewed flow.
2. **Conversation sequence numbers were not reused.** Gaps caused by direct
   deletion remained gaps. Do not renumber surviving messages or lower the
   conversation sequence counter.
3. **A failed predecessor caused a visible shadow predecessor gap.** The next
   completed trace began a new shadow segment instead of pretending continuity.
4. **An answer goal without grounding was rejected.** Preserve that gate. The
   fix is to add trusted producers and useful fallback behavior, not to adopt
   an unsupported answer goal.
5. **Application safety remained unavailable and commitments were restricted.**
   Do not synthesize `allow` to improve coverage.
6. **Realization did not invent an answer outside the selected plan.** Preserve
   the closed-world compiler and independent validator.
7. **Shadow output remained isolated.** No finding authorizes candidate text to
   reach chat, tools, memory, delivery, or live-response control.

## Suggested implementation order

1. Fix turn-scope closure and focus provenance, then add adversarial multi-turn
   tests. Behavioral evaluation of long chains is unreliable until these P0
   defects are resolved.
2. Fix frame-local request classification and definition-specific Goal support
   thresholds. These P0 defects currently discard correctly grounded meaning
   before Action Selection.
3. Finish compositional Perception and semantic-grounding coverage with
   reviewed contrasting cases rather than example-specific patterns.
4. Define the typed trusted-producer boundary and propagate blocker reasons to
   Action Selection.
5. Correct fallback policy for blocked user goals.
6. Make provider compatibility explicit and auditable.
7. Improve content-minimized evaluation summaries.
8. Add behavioral Realization review without weakening deterministic fidelity
   validation.

## Required agent report

For each addressed finding, report:

- finding IDs addressed;
- contract and version changes;
- files and migrations changed;
- why the behavior generalizes beyond the evidence utterances;
- focused tests, adversarial variants, and full-suite counts;
- replay and backward-compatibility impact;
- confirmation that shadow isolation and safe defaults remain unchanged; and
- any finding intentionally deferred, with the exact remaining blocker.
