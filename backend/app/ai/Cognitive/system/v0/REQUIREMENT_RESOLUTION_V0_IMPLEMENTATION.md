# Typed Requirement Resolution V0 — Implementation

Status: **implemented** (production code and focused tests); not yet
independently audited; shadow-only, never live-integrated.  
Date: 2026-09-30  
Findings addressed: SH-EVAL-020, SH-EVAL-022, SH-EVAL-023, and the content
side of SH-EVAL-024 from
[`SHADOW_PIPELINE_END_TO_END_REEVALUATION_2026-09-29.md`](SHADOW_PIPELINE_END_TO_END_REEVALUATION_2026-09-29.md).
SH-EVAL-021 (live Goal outcome reconciliation) is deliberately **not**
addressed here (see §11).

This document is normative for the requirement-resolution stage and for the
Goal, Action, pipeline, live-shadow, Realization, and summary changes it
required. The Cognitive README status section is canonical for overall status.

## 1. Position in the pipeline

```text
Interpretation
  -> Goal formation/adoption            (what the user wants handled)
  -> typed requirement resolution       (is fulfilment ready, blocked, unavailable, or prohibited?)
  -> goal readiness / blocking projection
  -> existing Action candidate construction, gates, and policy
                                        (which existing move truthfully addresses that state?)
  -> Realization                        (closed-world wording of the selected plan)
```

Requirement resolution is deterministic and closed-world. It makes no model
call, parses no text, reads no clock, and has no I/O. It never chooses a move:
it populates fields Action already understands (grounded objects, target
descriptors) and records typed blockers that Action turns into planning
conditions. No new answer, clarification, or refusal action was added.

## 2. Versions

New:

| Component | Version |
| --- | --- |
| Contract (`contracts/requirement_resolution.py`) | `requirement-resolution-contract-v0.1` |
| Engine (`requirement_resolution_v0/engine.py`) | `requirement-resolution-engine-v0.1` |
| Manifest (`requirement_resolution_v0/manifest.py`) | `requirement-resolution-manifest-v0.1` |
| Capability map | `requirement-capability-map-v0.1` |
| Action projection (`requirement_resolution_v0/projection.py`) | `requirement-action-projection-v0.1` |
| Realization content producer | `requirement-resolution-content-projection-v0.1` |

Changed (every change is recorded in its owning version; stored traces from
earlier versions load for audit and replay as `version_unavailable`):

| Component | From | To | Why |
| --- | --- | --- | --- |
| Goal ontology | `goal-ontology-v0.1-draft` | `goal-ontology-v0.2-draft` | adds `interaction.handle_action_request` |
| Goal formation | `goal-formation-v0.3-draft` | `goal-formation-v0.4-draft` | forms the handler for an action request without a trusted task |
| Goal adoption policy | `goal-adoption-policy-v0.1-draft` | `goal-adoption-policy-v0.2-draft` | handler policy; answer-goal `deferred_arguments` |
| Goal priority | `goal-priority-v0.2-draft` | `goal-priority-v0.3-draft` | handler ranks with current explicit requests |
| Goal satisfaction | `goal-satisfaction-v0.2-draft` | `goal-satisfaction-v0.3-draft` | `goal-satisfaction.action-request-handled.v1` |
| Goal evidence policy | `goal-evidence-policy-v0.1-draft` | `goal-evidence-policy-v0.2-draft` | handler authority entry |
| Goal engine | `goal-engine-v0.3` | `goal-engine-v0.4` | candidate/focus ordering includes the handler |
| Action contract | `action-contract-v0.2` | `action-contract-v0.3` | request `requirement_handoff`; resolution-sourced planning conditions; `capability_status`; new condition kinds |
| Action features | `action-features-v0.2` | `action-features-v0.3` | handler goal links; current-turn request goals and their frames earn goal credit |
| Action planner | `action-planner-v0.2` | `action-planner-v0.3` | resolution-derived conditions and kind-specific addressing |
| Pipeline runner | `v0.2.1` / `v0.3.1` | `v0.2.2` / `v0.3.2` | resolution stage between Goal and the cognitive turn |
| Trace summary | `cognitive-shadow-trace-summary-v0.2` | `cognitive-shadow-trace-summary-v0.3` | `requirements` stage section |

Pipeline schema versions are unchanged: the request only gains the optional
`requirement_inputs` field, omitted when absent, exactly as earlier additive
fields were introduced.

## 3. Contracts (`contracts/requirement_resolution.py`)

A neutral module: it imports only `contracts.interpretation`, so Goal never
imports Action and Action never imports a producer implementation (the Action
contract imports this module).

- `RequirementKind`: `required_argument`, `dialogue_referent`,
  `trusted_content`, `capability`, `authorization`, `consent`, `safety`,
  `tool_outcome`.
- `RequirementResolutionStatus`: `satisfied`, `missing_user_input`,
  `missing_grounding`, `unavailable`, `unauthorized`, `prohibited`,
  `temporary_failure`, `conflicting`.
- `RequirementReason`: closed reason codes. `ALLOWED_OUTCOMES` fixes the only
  `(status, reason)` pairs each kind may record (e.g. a capability is never
  "missing user input"; an argument is never "prohibited").
- `RequirementResolution`: one declared prerequisite of one goal and frame —
  `goal_ref`, `goal_requirement_ref`, `frame_ref`, `kind`, `key`,
  `argument_name`, `status`, `blocking`, `tier`, `reason_code`, canonical
  `subject_refs`/`value_refs`/`evidence_refs`/`policy_refs`/`capability_refs`,
  `resolver_id`, `resolver_version`. A satisfied prerequisite never blocks; a
  prohibited one cites the trusted safety decision; a capability cites what it
  evaluated. **No wording and no produced value is ever carried.**
- `RequirementContentProvenance`: value-free producer provenance (query id and
  kind, result id, producer status/reason/class/id/version, manifest version,
  fact ids, withheld/conflicting ids, gap id). `ContentProducerStatus` mirrors
  `producers_v0.ProducerStatus` (parity is tested) so this module needs no
  producer import.
- `GoalReadiness`: the readiness projection of each goal (`ready`/`blocked`,
  blocking tier and resolutions). It is never a terminal Goal state:
  `GoalRequirementAssessment` is untouched.
- `RequirementResolutionSnapshot`: canonical ordering, one resolution per
  declared key per (goal, frame), readiness consistent with blocking, content
  provenance consistent with status, and a SHA-256 `digest` over every other
  field (`sealed()` computes it).
- `RequirementActionBinding` / `RequirementActionHandoff`: which Action objects,
  descriptors, and Realization content each resolution was projected into, and
  the exact blocker refs a move must target. Only a blocking resolution names
  blockers.
- `RequirementResolutionInputs`: trusted turn-boundary facts — the captured
  server `turn_time` (zoned), an optional IANA `time_zone` (validated, never
  guessed), and the `user_ref` that scopes user-owned records.
- `validate_requirement_snapshot`: lineage (turn, conversation,
  interpretation, goal snapshot, runtime) and reference integrity. Every
  reference must be a current-turn Interpretation identity (frames, every
  frame argument, unknowns, evidence, hypotheses, contradictions), a current
  Goal record or requirement, a trusted runtime reference of this snapshot, an
  authorized dialogue reference of this turn, or the snapshot's own producer
  identities. Raw text is therefore unrepresentable, and a snapshot may only
  cover active user/shared goals formed this turn.

## 4. Goal changes

### 4.1 `interaction.handle_action_request`

The non-committing handler of an explicit action request. Its terminal
condition (`action_request_handled`, satisfied only by a validated delivered
response via `goal-satisfaction.action-request-handled.v1`) is that the
request was appropriately handled — fulfilled, clarified, or truthfully
declined — never that the external effect occurred. `task.complete_authorized_request`
keeps its hard authorization and safety gates unchanged.

- Formation: act `request_action`, speaker goal `cause_action`, frame family
  `REQUEST_ACTION`, one candidate per positive request frame (negative frames
  scope it), target `request_ref` = the frame, parameter `requested_effect`,
  scope `turn`, owner `user`. It forms only when **no trusted task** exists
  (`requires_absent_task_ref`); with a trusted task the committing task goal
  represents the request, so the two never compete. In live shadow no task is
  ever captured, so every action request forms the handler.
- Adoption: act and speaker goal at `moderate` or better (an unresolved
  referent or an unsafe reading weakens the act to moderate), quoted or
  hypothetical modality disqualifies, an `unknown` frame is ungrounded, and
  **no** authorization, safety, or unresolved-argument gate: those are
  readiness, resolved downstream. Clear intent is never erased by missing
  feasibility evidence.
- Focus: created this turn, so it ranks in the current-request tier.

### 4.2 Deferred referent arguments

`GoalAdoptionPolicy.deferred_arguments` names referent arguments whose
unresolved value is readiness rather than a rejection ground; no epistemic
subgoal forms for them. The answer goal defers `referent_phrase`, so "expand
on that" keeps its answer goal and the referent is resolved (from Dialogue
Working Memory) or asked for by requirement resolution. `topic_ref` keeps its
reviewed rejection path.

### 4.3 Shared binding helper

`formation_policy.frame_target_refs` is the one function that binds an entry's
requirement targets from a frame. Formation uses it, and resolution uses it to
recover exactly the frame a goal was formed from (`goal_frames`).

All 36 reviewed Goal design cases (25 updates, 15 failure and 2 invariance
variants) pass unchanged apart from the recorded version strings.

## 5. Manifest (`requirement_resolution_v0/manifest.py`)

Covered goals: `interaction.answer_information_request` and
`interaction.handle_action_request` (active, current-turn, user/shared).

| Goal | Predicate | Declared prerequisites (tier) |
| --- | --- | --- |
| answer | any request-information predicate | each required referent argument (1) → trusted content (5) |
| handler | `action_request` and other request predicates | each required user-suppliable argument (1; referent arguments as `dialogue_referent`) → capability (2) → safety (3) |
| handler | registered-capability predicates | + authorization (3) and, for irreversible effects, consent (4) |
| handler | `unsafe_request` | safety (**0**) → capability (2); no argument is ever elicited |

Tiers: 0 safety, 1 user input, 2 capability, 3 execution (authorization,
safety for ordinary requests, tool outcome), 4 consent, 5 content. A trusted
`prohibited` decision always evaluates in tier 0. **Only a goal's lowest
unsatisfied tier blocks**; later tiers are still resolved and traced but do
not decide the next action. So a prohibited request is refused before any
detail is asked for, and a request missing its object is clarified before a
capability limitation is disclosed. `authorization` is never a conversational
argument.

Capability map: a predicate's typed `capability_key` is a registered
capability; it is available exactly when the trusted runtime lists it, needs a
trusted authorization record, and needs explicit consent when the predicate is
`irreversible`. A generic `action_request` carries its requested effect as
free text, which is never mapped to a capability: its effect is
**unregistered** — a limitation to disclose, never user ambiguity.

Content query compilation uses closed-vocabulary frame arguments only:

| Frame | Query kind |
| --- | --- |
| `identity_request` (name, assistant) | `application_identity.assistant_name` (installed) |
| `identity_request` (other attributes) | `application_identity.<attribute>` / `user_profile.<attribute>` (unregistered → explicit gap) |
| `runtime_fact_request` (current_date) | `runtime.current_date` (installed; captured turn time) |
| `runtime_fact_request` (other facts) | `runtime.<fact>` (unregistered → explicit gap) |
| `elaboration_request` | `dialogue.delivered_claim` with the exact resolved dialogue item as `claim_ref` (requires review → explicit gap in live shadow) |
| `information_request` | `reviewed_knowledge.open_question` (unregistered → explicit gap) |

Query anchors are reviewed wording, never the user's text or a parser
paraphrase: "my name", "Today's date in UTC" (or "Today's date" with a
captured zone), the exact dialogue cue span for a resolved follow-up, and
otherwise the generic "that" (so an explicit gap reads "I don't have a
grounded answer to that.").

Nothing falls back to model knowledge. `reviewed_knowledge.nutrient_amount`
needs typed `food` and `nutrient` arguments; no Interpretation predicate
carries them yet (§11).

## 6. Engine (`requirement_resolution_v0/engine.py`)

`RequirementResolutionEngine.resolve(RequirementResolutionRequest)` takes typed
snapshots only: the `InterpretationEnvelope`, the `GoalSnapshot`, the one
trusted `ActionRuntimeContext` projection, the authorized
`InterpretationDialogueContext` and dialogue Action refs of this turn, and the
captured `RequirementResolutionInputs`. It returns the sealed snapshot plus the
producer records behind each content resolution.

Resolver families:

- **Required argument**: present, or `missing_user_input` citing the exact
  argument ref and its typed unknowns.
- **Dialogue referent**: a stated value; else a referent cue of the authorized
  dialogue context whose span lies inside the frame's own evidence span —
  `resolved` satisfies it with the exact dialogue item refs, `ambiguous` or
  `no_candidate` is `missing_user_input`. No history is ever inspected.
- **Trusted content**: one `SemanticQuery` per goal frame, routed through
  `ProducerRegistry` (the one producer protocol); `known` → satisfied;
  `unavailable`/`stale`/`unsupported` → `missing_grounding` with the reviewed
  `KnowledgeGapRecord`; `unauthorized` / `conflicting` project no content.
  Content is not declared while its referent is missing.
- **Capability**: available / registered-but-unavailable / unregistered.
- **Authorization and consent**: captured runtime records only; a missing
  authorization is `unauthorized`, missing consent is a consent-specific
  `missing_user_input`, never an assumed permission.
- **Safety**: the closed application decision through `safety_restriction`:
  `allow` satisfies; `block_commitments`/`block_response` are `prohibited` with
  the decision, producer, and trusted policy records; a missing, unknown,
  unavailable, or unsupported-version decision is `unavailable` and fails
  closed.

Producers: the reviewed application identity and, when a turn time was
captured, one captured runtime record — the date at the turn boundary in the
captured user zone or, absent one, explicitly in UTC (observed exactly at the
turn time). Review-gated classes (reviewed knowledge, delivered claims, tool
results) stay unavailable; `allow_review_classes` exists only for offline
evaluation and is never set by the pipeline.

## 7. Projection (`requirement_resolution_v0/projection.py`)

- **Answers**: `producers_v0.project_action_objects` and
  `project_realization_content`, unchanged — a factual QUESTION whose
  `answer_content_refs` are the produced fact(s) or the explicit gap record.
- **Clarification**: the exact unresolved semantic argument gets a
  `(owner=user, unknown_kind=missing_value, about_refs=[goal])` descriptor;
  every other representation of the same missing value (its typed unknowns) is
  `represents_ref`-linked to it, so it is asked exactly once, there.
- **Requests**: one current-turn REQUEST per operational or unsafe request
  (`focus_contribution`, `fulfillment_mode="task"`) with an explicit
  `acceptance_status` derived from the blocking tier (`prohibited` >
  `unauthorized` > `unavailable` > `pending` > `acceptable`), its registered
  capabilities, authorization and consent needs, trusted safety policy records
  when prohibited, and `decline_reason_refs` to typed reason objects: a
  CAPABILITY object with the new closed `capability_status`
  (`unregistered`/`unavailable`), or an EVIDENCE_RECORD citing the safety
  decision or runtime authorization record. No meaning is encoded in an id.
- **Withheld or conflicting answers**: the question becomes a scoped
  conversational REQUEST (`unauthorized` / `unavailable`) declinable with a
  typed reason; the protected or competing values are never projected.
- **Suppression**: an unresolved element of a request frame that is not a
  blocking user-input prerequisite is represented by the request (or answer
  question), so the details of a prohibited or answered request are never
  elicited.
- **Realization content** (SH-EVAL-024 alignment; the validator is
  unchanged): answers and gaps from the producer projection; requests as
  anchored `DialogueReference`s with the reviewed anchor `"your request"`
  (it attributes the request to the user, satisfying the validator's user
  attribution rule); each decline reason as a runtime-attributed
  `GroundedProposition` about the request whose predicate names a *positive*
  state recorded with `polarity=negative`, so a natural refusal ("I can't
  help with your request.") preserves the recorded polarity:
  `runtime.requested_capability_registered`,
  `runtime.requested_capability_available`, `runtime.request_authorized`,
  `application_safety.request_permitted`,
  `application_safety.request_cleared`,
  `runtime.requested_operation_completed`,
  `runtime.requested_operation_available`,
  `runtime.requested_content_shareable`,
  `runtime.requested_content_consistent`. Each clarification target gets an
  anchored `unresolved_element` reference whose reviewed anchor names the gap
  in Columbina's understanding (`manifest.ELICITATION_ANCHORS`, e.g.
  `subject_ref` → "what you are referring to", default "what you mean"), so the
  validator rejects a question that echoes or reverses the user's request
  (e.g. repeating "Can you check it for me?"). Realization never infers a reason
  from the utterance or the action key.

## 8. Action changes (contract / features / planner v0.3)

- `ActionSelectionRequest.requirement_handoff` (omitted when absent) is
  validated for lineage; the reference index requires every binding's blocker,
  object, and descriptor refs to resolve.
- `PlanningConditionKind` adds `missing_capability`, `capability_unavailable`,
  `missing_consent`, `safety_prohibited`, `safety_unavailable`,
  `trusted_content_unavailable`, `temporary_failure`.
  `ActionPlanningCondition` derives from exactly one source:
  `rejection_rule_id` (a rejected goal, unchanged) or `resolution_refs`
  (blocking resolutions of an adopted current-turn request goal; one condition
  per goal and kind). An unresolved `topic_ref` of a rejected information goal
  is now `missing_argument` (user input), no longer conflated with
  `missing_trusted_fact`, which now means a known topic whose trusted fact is
  absent or stale; `trusted_content_unavailable` means no producer may answer.
- Addressing: rejection-derived conditions keep their reviewed rule. A
  resolution-derived condition is addressed only by the move kind that
  truthfully resolves it, with its principal target **exactly** a blocker:
  elicitation for missing input or consent, an answer carrying the explicit gap
  for missing content, verification/contradiction handling or a scoped decline
  for conflicting content, and a scoped decline for every execution blocker.
  `feedback.receipt` appears in no addressing set (checked at import), so
  acknowledgement can never discharge a blocker. The v0.2 blocker-first
  restriction and supporting-only credit for generic moves then apply
  unchanged.
- Features: the handler requirement is realization-satisfiable; a scoped
  decline links to it directly, clarification and acceptance only support it.
  Every current-turn request goal the requirement snapshot covers earns goal
  credit (not only the focal ones — Goal focus admits one focal goal per
  definition, so a compound turn's second question could otherwise never be
  answered), and a candidate relates to such a goal through the exact frame
  the goal was formed from (an answer goal's topic target may be literal user
  wording that no Action object can cite). Historical goals gain nothing.
- `ActionGroundedObject.capability_status` (closed; CAPABILITY objects only;
  omitted when absent, so earlier payloads keep their bytes).

Invariants and where they hold: acknowledgement is never the primary move of a
blocked request goal (it cannot address a blocker, and the restriction admits
only addressing candidates); supported answers use `provide_information.answer`
with exact content refs; missing user input is clarified at the exact argument;
known questions without a trusted answer answer with the explicit gap and never
elicit; unavailable capability and prohibited or unauthorized requests are
declined with grounded reasons; offers, promises, acceptance, and next steps
stay hard-gated unless capability, authorization, consent, and a trusted
`allow` are all present.

The 29 reviewed Action design cases and the Realization golden reproduce
byte-for-byte when the previous version strings are restored; the reviewed
expectations were then regenerated with `build_expected_results()` and every
plan and step is identical.

## 9. Pipeline, live shadow, Realization, and summary

- `CognitivePipelineV0` runs `requirement_resolution` after Goal (new failure
  stage `requirement_resolution` / `pipeline.requirement_resolution`), appends
  the projected objects and descriptors **after** the request-supplied ones
  (identity-checked), and hands the handoff to the cognitive turn
  (`CognitiveTurnRequest.requirement_handoff`, omitted when absent), which
  passes it to Action unchanged. Timings add `requirement_resolution_ms`.
- The trace records `requirement_resolution` (versions, handoff with the
  sealed snapshot and bindings, producer records, the dialogue refs it could
  cite, projected objects, descriptors, and Realization content) and
  `versions.requirement_resolution`. Trace validation requires it exactly for
  current-runner traces, re-validates the snapshot's lineage and references,
  checks the turn consumed exactly this handoff and these trailing inputs, and
  **recomputes the projection** from the recorded resolutions, so no object,
  reason, blocker, or content can be edited independently of them.
- The live-shadow compiler adds `requirement_inputs` from the capture
  (`captured_at`, no zone, the captured user); cross-record validation checks
  them and the dialogue refs against the capture. No capture schema change.
- `live_shadow.realization.realization_inventory` merges the projected content
  (producer `requirement-resolution-content-projection-v0.1`); a trace without
  it produces exactly the historical inventory.
- `trace_summary` v0.3 adds the `requirements` stage: per request goal its
  readiness, blocking tier, blocker kinds, and resolution kinds/statuses/reasons
  and value-free content provenance; for the primary goal whether an answer,
  clarification, decline, or other move was constructible, the selected
  primary action, and why acknowledgement did not address the blocker
  (`receipt_never_addresses_a_blocker`, `outscored_by_goal_directed_move`,
  `selected_without_a_blocker`, `not_eligible`). No values, anchors, query
  arguments, or text are emitted.

## 10. Verification

Focused suites (all pass): `test_requirement_resolution_contract.py`,
`test_requirement_resolution_engine.py`, `test_requirement_resolution_projection.py`,
`test_requirement_resolution_action.py`, and `test_requirement_resolution_pipeline.py`
(the exact reevaluation corpus replayed through the real live-shadow path with
its reviewed frozen candidates, the uninterrupted eight-turn trace with
lineage, the durable worker against real PostgreSQL, replay, Realization
compilation, summary, determinism, and isolation, plus SH-EVAL-024 wording
checks: faithful sentences over the projected content are accepted by the
unchanged validator, and echoed, reversed, or contradicting ones are rejected).
Full backend suite: 1525 passed / 114 skipped without a database; 1639 passed
against PostgreSQL with the live-shadow worker pinned to the offline rule
grounder (see the behavioral results for why that pin is needed with a local
`.env` that enables the real providers). Behavioral results are in
[`REQUIREMENT_RESOLUTION_V0_BEHAVIORAL_RESULTS_2026-09-30.md`](REQUIREMENT_RESOLUTION_V0_BEHAVIORAL_RESULTS_2026-09-30.md).

## 11. Known limitations (explicit, not silently deferred)

1. **Untyped action requests are declined.** Conversational requests such as
   "plan a vegetarian dinner" are `action_request`s whose free-text effect has
   no registered capability, so the truthful shadow move is a scoped decline.
   Fixing this needs a typed effect or capability argument from Interpretation
   (or a reviewed composition producer), not text matching.
2. **No typed nutrient query.** `reviewed_knowledge.nutrient_amount` needs
   typed `food`/`nutrient` arguments no predicate provides; nutrient questions
   compile to the explicit unsupported open-question query and answer with a
   grounded gap. Reviewed knowledge also still requires privacy review.
3. **Review-gated producers stay off.** Delivered claims and tool results
   remain `requires_review`; follow-ups resolved from Dialogue Working Memory
   answer with an explicit gap. Dialogue Working Memory V0 resolves pronouns
   only to user-named entities and marks "that/this" only sentence-finally,
   and never exposes delivered Columbina speech as a referent, so "expand on
   that with an example" after an assistant answer is resolved only when the
   parser states the referent; otherwise Columbina asks what to expand.
4. **No trusted user zone is captured.** The runtime date is reported
   explicitly in UTC (anchor "Today's date in UTC", capitalized so it stays
   valid when it opens the sentence; anchor matching is case-insensitive).
5. **No application safety policy producer exists.** Live safety is always
   `unavailable`, so unsafe requests are declined as `unauthorized` with a
   safety-unavailable reason; `prohibited` (with trusted policy records) occurs
   only with a trusted blocking decision. Safety is never treated as allow.
6. **No action elicits consent.** A missing consent is a typed blocker whose
   binding has no addressable target; acceptance stays hard-gated.
7. **Repair goals are not declared.** A correction turn's repair goal has no
   requirement declarations and no repair-capable action is constructible from
   live inputs, so acknowledgement remains its primary move; enforcing
   invariant 1 there would only produce `no_viable_plan` failures.
8. **`tool_outcome` / `temporary_failure`** exist in the contract and mapping
   but the V0 manifest declares no tool-outcome prerequisite.
9. **SH-EVAL-021** is unchanged: the live capture has no delivered-response or
   tool-outcome binding, so no goal is reconciled or closed from outcomes.
10. **Realization wording (SH-EVAL-024)** now binds grounded content for every
    answer, clarification, and decline; validator strictness is unchanged.
    With the real provider most remaining rejections are model wording
    (details copied from the user message, exclamations, schema-invalid
    output); see the behavioral results. The validator does not detect an
    echo that *includes* the reviewed anchor ("If I asked you to cancel what
    you are referring to, would you do it?"); detecting it would need a
    reviewed, non-phrase echo rule in Realization, which is out of scope here.

## 12. Forbidden shortcuts (still enforced)

No model decides a requirement status, capability, authority, safety, or
truth; no phrase-specific rule; no weakening of truth, capability,
authorization, privacy, or safety gates; Realization never chooses a different
action; the user utterance never becomes trusted content; no second answer,
clarification, or refusal registry; an unavailable producer is never evidence
of falsity; no goal is satisfied from a planned action; all behavior stays
shadow-only.
