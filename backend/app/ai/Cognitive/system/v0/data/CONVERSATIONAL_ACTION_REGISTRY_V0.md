# Conversational action registry V0

## Status and purpose

This document is the accepted registry design for Columbina's shadow-V0
conversational operators. It is not a production registry, database migration,
model artifact, live-selection authorization, or approved source of training
truth. `../ACTION_SELECTION_V0_DESIGN.md` owns the final integration and
selection boundary.

```text
registry_version: conversational-actions-v0.1-shadow
schema_version: 2
effect_contract_version: 1
status: accepted_for_shadow_implementation
action_families: 9
provisional_action_keys: 75
case_covered_keys: 51
shadow_selectable_keys: 39
boundary_only_keys: 12
uncovered_keys: 24
```

The planner audit accepts 39 keys with at least one positive design example for
construction and selection inside the isolated shadow runtime. Twelve keys that
appear only as disfavored or hard-invalid counterexamples and 24 uncovered keys
remain non-selectable candidates. `case-covered` therefore means only that a key
appears somewhere in the fixture; it is not the promotion criterion. No key is
approved for live behavior, behavioral calibration, or training truth.

## Registry model

The registry hierarchy is:

```text
family template
    -> versioned action definition
        -> grounded runtime candidate
            -> selected move in an ordered plan
                -> language realization
```

Each action definition ultimately compiles to this contract:

```json
{
  "action_key": "family.function",
  "definition_version": 1,
  "parent_action_key": null,
  "family": "family",
  "function": "function",
  "description": "Testable semantic boundary",
  "target_schema": {
    "accepted_types": [],
    "minimum_targets": 0,
    "maximum_targets": 1
  },
  "parameter_schema": {},
  "evidence_requirements": [],
  "intended_effects": [
    {
      "effect_key": "namespace.effect",
      "effect_version": 1,
      "transition_domain": "target_effect",
      "binding_mode": "each_target",
      "target_roles": {"primary": []},
      "planned_state": "representation_planned",
      "effect_strength": 1.0,
      "actual_outcome_claim": false
    }
  ],
  "structural_requirements": [],
  "hard_inhibitors": [],
  "policy_features": [],
  "outcome_metrics": [],
  "origin": "human",
  "status": "candidate"
}
```

Family templates supply defaults. Per-action rows below define semantic
boundaries and overrides. Literal response content does not belong in the
registry. Runtime candidates bind versioned references to cognitive objects.

## Shared types

### Target types

```text
message
user_act
assistant_act
dialogue_act
semantic_frame
semantic_argument
proposition
question
topic
option
option_set
interpretation_hypothesis
competing_hypothesis_set
unknown
contradiction
goal
plan
plan_step
request
suggestion
commitment
experience
concern
claimed_affect
appraisal_field
dynamic_state_field
partial_plan
prior_move
prior_action
prior_realization
observed_outcome
evidence_record
content_source
capability
boundary_scope
social_target
dialogue_scope
```

Targets are typed references, not copied free text. A retrieved memory may
support a proposition or hypothesis through provenance, but raw memory content
is not an unrestricted target type.

### Shared semantic parameters

| Parameter | Type | Meaning |
| --- | --- | --- |
| `question_form` | enum | `open_set`, `yes_no`, `choice`, `check`, `scale`, or `free_reflection` |
| `candidate_answer_refs` | reference array | Grounded alternatives; required for `choice` |
| `scale_min`, `scale_max` | number | Bounds; required for `scale` |
| `scope_ref` | reference | Semantic or dialogue scope of the move |
| `basis_refs` | reference array | Evidence or comparison basis explicitly carried by the move |
| `option_refs` | reference array | Grounded options being proposed, compared, or recommended |
| `reason_refs` | reference array | Grounded reasons; never a fabricated rationale string |
| `content_scope` | enum | `minimal`, `focused`, `broad`, or `requested` |
| `conditionality` | enum | `unconditional`, `conditional`, or `hypothetical` |
| `commitment_level` | enum | `tentative`, `bounded`, or `firm` within actual capability |
| `repair_scope` | enum | `interpretation`, `action`, `realization`, `memory`, or `mixed` |
| `comparison_dimension_refs` | reference array | Dimensions used for an evaluation or comparison |
| `requested_evidence_type` | enum | Evidence class being requested |

Personality, appraisal, dynamic state, confidence, previous actions, model
scores, and learned utility are policy inputs, not action parameters. Tone,
verbosity, vocabulary, and sentence form are realization controls unless they
change semantic force or commitment.

### Shared construction and hard-gate boundaries

A missing required target, parameter, or evidence binding normally makes an
action `uninstantiable`; it is not learned inhibition and is not mislabeled as a
hard-invariant violation. If a malformed grounded candidate nevertheless reaches
the gate, schema validation blocks it as an implementation fault.

All families inherit the six narrow hard-gate categories from the canonical
cognitive-system design:

- registry and lifecycle;
- schema and reference integrity;
- truth and evidence;
- capability;
- authorization, consent, and privacy; and
- versioned application safety policy.

A candidate is blocked when its required realization necessarily violates one
of these categories. When a truthful bounded realization remains possible, the
gate may pass the abstract action with mandatory realization constraints such as
`preserve_uncertainty` or `forbid_unsupported_guarantee`.

Ordinary awkwardness, repetition, seriousness, relationship mismatch,
verbosity, insufficient warmth, unwanted humor, or low expected utility are not
hard inhibitors. They remain evidence-driven policy questions.

### Shared policy features

Every eligible action may be scored using:

```text
interpretation and target fit
hypothesis support and uncertainty
goal relevance and expected progress
unknown or contradiction reduction
qualitative appraisal and available dynamic state
bounded core action affinities
prior-turn actions and observed outcomes
current partial-plan moves and repetition
expected response cost, risk, and helpfulness
reviewed contextual outcome history
```

Relationship features remain absent or unknown until the relationship subsystem
has an accepted contract.

### Shared outcome dimensions

The current observable vocabulary is:

```text
goal_resolution
clarity
explicit_user_correction
engagement
social_alignment
unnecessary_repetition
confusion
realization_fidelity
```

Actions list relevant dimensions but do not invent outcome values.

## Family templates and action definitions

In the tables below:

- `1` means exactly one target;
- `1+` means one or more targets;
- `0..1` means an optional target;
- `covered` means present in the current pending-review fixture;
- `uncovered` means another positive/boundary/counterexample case is required;
- every lifecycle value remains `candidate`.

### `provide_information`

Family purpose: add supported content to shared information state.

Family target defaults: `question`, `proposition`, `semantic_frame`, `topic`, or
`evidence_record`; cardinality `1` unless overridden.

Family evidence defaults: content provenance and epistemic status are required.
Unsupported certainty, fabricated evidence, and contradictions with stronger
runtime evidence inherit the shared hard inhibitors.

Family outcome defaults: `goal_resolution`, `clarity`, `confusion`, and
`realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `provide_information.answer` | Supply content responsive to a represented question or requested proposition; target `question` or `proposition`, `1` | optional `content_scope` | Requires supported answer content; effect: satisfy information request | covered |
| `provide_information.inform` | Add relevant information not structurally framed as a direct answer; target `proposition` or `semantic_frame`, `1+` | optional `content_scope`, `conditionality` | Requires provenance; effect: add supported belief candidate | covered |
| `provide_information.explain` | Make causes, mechanisms, or relationships understandable; target `proposition`, `semantic_frame`, or `topic`, `1` | optional `scope_ref`, `basis_refs` | Requires explanation basis; effect: increase understanding | covered |
| `provide_information.elaborate` | Add relevant detail to content already introduced; target `prior_move`, `proposition`, or `topic`, `1` | optional `content_scope` | Requires prior content and non-duplicate detail; effect: increase detail | covered |
| `provide_information.summarize` | Compress a bounded source while preserving its important content; target `semantic_frame`, `topic`, `prior_action`, or authorized `content_source`, `1+` | optional `content_scope` | Requires accessible source; effect: plan a summary without claiming that the source itself changed | covered |
| `provide_information.exemplify` | Supply a grounded example of an abstract claim or explanation; target `proposition` or `topic`, `1` | optional `scope_ref` | Example must not be presented as source fact unless supported; effect: improve comprehension | covered |
| `provide_information.justify` | Provide reasons or evidence supporting Columbina's claim, stance, recommendation, or action; target `prior_move`, `proposition`, or `commitment`, `1` | required `basis_refs` | Requires actual supporting basis; effect: expose support | uncovered |
| `provide_information.correct` | Replace or qualify a proposition using stronger contradictory evidence; target `proposition`, `semantic_frame`, or `prior_realization`, `1` | required `basis_refs`; optional `scope_ref` | Requires correction content and contradiction evidence; effect: repair shared belief | covered |
| `provide_information.disclose_uncertainty` | Qualify information Columbina is providing as uncertain; target `proposition`, `semantic_frame`, or `interpretation_hypothesis`, `1+` | optional `conditionality`, `basis_refs` | Requires represented uncertainty; effect: preserve epistemic status | covered |
| `provide_information.warn` | Communicate supported risk or adverse consequence, whether or not behavior change is requested; target `proposition`, `concern`, or `observed_outcome`, `1+` | optional `basis_refs`, `scope_ref` | Requires supported risk; effect: make risk known | uncovered |

Boundary decisions:

- `answer` responds to a represented information request; `inform` need not.
- `explain` adds causal or relational understanding; `elaborate` adds detail;
  `exemplify` supplies an instance; `justify` exposes support.
- `provide_information.warn` communicates risk. `advise_or_influence.warn`
  uses that risk to influence a choice or behavior.
- `disclose_uncertainty` qualifies supplied content.
  `express_stance.express_uncertainty` takes an uncertain position toward a
  proposition.

### `seek_information`

Family purpose: request information that can reduce a represented unknown,
resolve alternatives, evaluate an assumption, or improve a downstream decision.

Family target defaults: `unknown`, `semantic_argument`, `proposition`,
`interpretation_hypothesis`, `goal`, `experience`, or `evidence_record`;
cardinality `1` unless overridden.

Family parameters: `question_form` is required. `candidate_answer_refs` is
required when form is `choice`; `scale_min` and `scale_max` are required when
form is `scale`.

Family outcome defaults: `clarity`, `goal_resolution`, `engagement`,
`explicit_user_correction`, and `confusion`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `seek_information.clarify` | Resolve a missing argument, unfamiliar term, underspecified scope, or unclear content; target `unknown` or `semantic_argument`, `1` | family question schema | Requires represented unresolved content; effect: fill or refine it | covered |
| `seek_information.disambiguate` | Choose among represented competing referents or meanings; target `competing_hypothesis_set`, `unknown`, or ambiguous `semantic_argument`, `1` | family schema; grounded alternatives for `choice` | Requires at least two supported candidates; effect: select or reject alternatives | covered |
| `seek_information.verify` | Test whether a tentative proposition or assumption is correct; target `proposition` or `interpretation_hypothesis`, `1` | normally `yes_no` or `check`; optional `basis_refs` | Requires tentative status; effect: confirm, correct, or preserve uncertainty | covered |
| `seek_information.explore_goal` | Learn what outcome the user wants; target `goal` or goal-shaped `unknown`, `1` | family question schema | Requires unresolved or underspecified goal; effect: improve goal representation | covered |
| `seek_information.explore_preference` | Learn a choice, ranking, or degree preferred by the user; target preference `unknown` or `option_set`, `1` | family schema; scale bounds or alternatives when applicable | Must not invent preference; effect: populate preference evidence | covered |
| `seek_information.request_evidence` | Ask for support for a claim or interpretation; target `proposition`, `interpretation_hypothesis`, or `evidence_record`, `1` | family schema; optional `requested_evidence_type` | Requires a claim whose support matters; effect: obtain evidence | covered |
| `seek_information.explore_reason` | Invite reasons or reasoning behind a stance, choice, or claim; target `proposition`, `goal`, `option`, or `prior_action`, `1` | usually `open_set` or `free_reflection` | Requires a represented stance/choice; effect: expose reasoning | covered |
| `seek_information.explore_experience` | Invite description of an event or lived experience without asserting hidden affect; target `experience`, experience-scoped `interpretation_hypothesis`, or unresolved `semantic_argument`, `1` | usually `free_reflection` | Requires an evidenced experience or unresolved experience description; effect: plan experiential elicitation | covered |
| `seek_information.check_understanding` | Ask the user to confirm or correct Columbina's tentative interpretation; target `interpretation_hypothesis` or `proposition`, `1` | `question_form=check` | Requires explicit tentative interpretation; effect: validate shared understanding | covered |
| `seek_information.request_feedback` | Ask the user to evaluate Columbina's response, plan, or interaction; target `prior_move`, `prior_action`, `prior_realization`, or `plan`, `1` | family question schema | Requires an actual object to evaluate; effect: obtain outcome evidence | covered |
| `seek_information.inquire` | Ask a typed inquiry opportunity with a declared purpose, expected use, criticality, and timing (Collaborative Content V0, registry v0.2); target `inquiry`, `1` | `question_form` (`open_set`, `yes_no`, `choice`); `inquiry_timing`; `inquiry_criticality`; `candidate_answer_refs` for `choice` (no scale form) | Requires a validated inquiry of a collaborative proposal; a non-blocking inquiry follows its delivered primary contribution; effect: plan the elicitation | v0.2 addition |

Boundary decisions:

- `clarify` fills or sharpens one unresolved representation;
  `disambiguate` chooses among explicit alternatives.
- `verify` tests a proposition; `check_understanding` tests Columbina's own
  interpretation; `request_feedback` evaluates her output or interaction.
- Question form is a parameter, not a new action key.
- `inquire` asks a question whose purpose, expected use, criticality, and
  timing were decided upstream (collaborative content production and its
  deterministic inquiry policy); it never invents a question or a blocker.

### `give_feedback`

Family purpose: communicate Columbina's uptake of the user's contribution or the
state of shared understanding without necessarily adding a stance.

Family target defaults: `user_act`, `proposition`, `interpretation_hypothesis`,
`experience`, or `prior_move`; cardinality `1`.

Family outcome defaults: `clarity`, `social_alignment`, `engagement`,
`explicit_user_correction`, and `realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `give_feedback.acknowledge` | Signal receipt or attention without agreement, correctness, or full understanding; target `user_act`, `message`, or `request`, `1` | none | Requires observed contribution; effect: signal receipt | covered |
| `give_feedback.confirm_understanding` | State a specific understanding as sufficiently supported; target `interpretation_hypothesis` or `proposition`, `1` | optional `scope_ref` | Contradicted or weak interpretations cannot be confirmed; effect: establish shared understanding candidate | covered |
| `give_feedback.signal_misunderstanding` | State that Columbina does not currently understand some content; target `unknown`, `semantic_frame`, or `interpretation_hypothesis`, `1` | optional `scope_ref` | Requires actual unresolved understanding; effect: expose comprehension failure | covered |
| `give_feedback.paraphrase` | Restate understood content for alignment without adding new claims; target `user_act`, `proposition`, or `interpretation_hypothesis`, `1+` | optional `scope_ref`, `conditionality` | Must preserve epistemic status; effect: externalize current understanding | covered |
| `give_feedback.reflect` | Return an observation or tentative pattern about expressed experience; target `experience`, `claimed_affect`, or `user_act`, `1` | optional `conditionality` | Hidden affect must remain tentative; effect: invite recognition or correction | covered |
| `give_feedback.validate` | Recognize that a contribution, concern, or question is understandable or relevant without asserting factual correctness; target `user_act`, `concern`, or `experience`, `1` | optional `scope_ref` | Must not endorse unsupported claim; effect: validate conversational standing | covered |
| `give_feedback.indicate_attention` | Signal continued attention without claiming comprehension or evaluation; target `message`, `user_act`, ongoing `experience`, `topic`, attention `dynamic_state_field`, or active `dialogue_scope`, `1` | none | Requires an ongoing contribution or salience-driven continuation state; effect: plan an attention or continuity signal | uncovered |

Boundary decisions:

- `acknowledge` marks receipt; `indicate_attention` marks ongoing attention;
  `confirm_understanding` commits to a specific interpretation.
- `paraphrase` restates content; `reflect` returns an observation about expressed
  experience; `validate` recognizes conversational legitimacy.
- `give_feedback.validate` does not assert truth and differs from the more social
  `support_socially.validate_experience`.

### `express_stance`

Family purpose: represent Columbina's epistemic, evaluative, or preference
position toward grounded content.

Family target defaults: `proposition`, `option`, `option_set`, `plan`, or
`prior_move`; cardinality `1` unless comparison requires `1+`.

Family outcome defaults: `clarity`, `social_alignment`, `engagement`, and
`realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `express_stance.agree` | Endorse a proposition or stance as supported; target `proposition` or `prior_move`, `1` | optional `scope_ref`, `basis_refs` | Agreement must not imply unsupported facts; effect: align stance | covered |
| `express_stance.disagree` | Oppose a proposition or stance without necessarily supplying a correction; target `proposition` or `prior_move`, `1` | optional `scope_ref`, `basis_refs` | Requires identifiable point of disagreement; effect: mark non-alignment | covered |
| `express_stance.partially_agree` | Endorse one bounded part while withholding or opposing another; target `proposition` or `semantic_frame`, `1+` | required `scope_ref`; optional `basis_refs` | Requires typed references to separable supported and unsupported portions; effect: plan scoped alignment | uncovered |
| `express_stance.challenge` | Request reconsideration of a proposition, assumption, or reasoning process; target `proposition`, `interpretation_hypothesis`, or reasoning `prior_move`, `1` | optional `basis_refs`, `scope_ref` | Must address content, not personal worth; effect: reopen evaluation | covered |
| `express_stance.evaluate` | Assign a grounded assessment along explicit dimensions; target `option`, `plan`, `proposition`, or `prior_action`, `1+` | required `comparison_dimension_refs`; optional `basis_refs` | Requires evaluation basis; effect: add bounded assessment | uncovered |
| `express_stance.express_preference` | State Columbina's bounded preference among acceptable options; target `option` or `option_set`, `1+` | optional `basis_refs`, `scope_ref` | Must be represented as Columbina's preference, not user fact; effect: disclose preference | uncovered |
| `express_stance.express_uncertainty` | Take an uncertain stance toward a proposition or choice; target `proposition`, `option`, or `interpretation_hypothesis`, `1` | optional `conditionality`, `basis_refs` | Requires represented uncertainty; effect: avoid false commitment | uncovered |
| `express_stance.withhold_judgment` | Explicitly decline to form a stance because evidence or authority is insufficient; target `proposition`, `option_set`, or `prior_move`, `1` | optional `reason_refs`, `scope_ref` | Requires unresolved basis; effect: preserve non-commitment | uncovered |

Boundary decisions:

- `correct` replaces or qualifies content using stronger evidence; `disagree`
  only marks opposition; `challenge` invites reconsideration.
- `evaluate` applies dimensions; `express_preference` reports a preference.
- `withhold_judgment` is intentional non-evaluation, not uncertainty disguised
  as a conclusion.

### `support_socially`

Family purpose: support the user's expressed experience or social need without
inventing affect, responsibility, certainty, or future outcomes.

Family target defaults: `experience`, `concern`, `claimed_affect`, `user_act`,
`goal`, or `observed_outcome`; cardinality `1`.

Family outcome defaults: `social_alignment`, `engagement`, `clarity`, and
`realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `support_socially.reassure` | Reduce concern while preserving uncertainty and avoiding unsupported promises; target `concern`, `experience`, or uncertain `proposition`, `1` | optional `basis_refs`, `conditionality` | Requires a supported basis for any reassuring claim; effect: reduce concern | covered |
| `support_socially.encourage` | Support continued effort, expression, or a feasible next step; target `goal`, `plan_step`, `experience`, or `prior_action`, `1` | optional `basis_refs`, `scope_ref` | Must not guarantee success; effect: support agency or persistence | uncovered |
| `support_socially.comfort` | Offer presence or consolation in response to supported distress or hardship; target `experience`, `concern`, or `claimed_affect`, `1` | none | Hidden affect cannot be asserted; effect: provide social comfort | covered |
| `support_socially.validate_experience` | Recognize an expressed experience or reaction as understandable without establishing external facts; target `experience`, `claimed_affect`, or `concern`, `1` | optional `scope_ref` | Requires expressed or carefully qualified experience; effect: social validation | covered |
| `support_socially.congratulate` | Mark a supported achievement or positive milestone; target `observed_outcome`, `experience`, or `goal`, `1` | optional `scope_ref` | Requires supported positive event; effect: celebrate outcome | covered |
| `support_socially.appreciate` | Express valuation of a contribution, effort, disclosure, or quality; target `user_act`, `prior_action`, or `experience`, `1` | optional `scope_ref` | Requires actual contribution or quality; effect: communicate appreciation | uncovered |
| `support_socially.thank` | Express gratitude for a contribution or cooperation received; target `user_act`, `prior_action`, or `message`, `1` | optional `scope_ref` | Requires identifiable benefit or cooperation; effect: communicate gratitude | uncovered |
| `support_socially.apologize` | Express regret for Columbina's action, impact, or interaction failure without inventing responsibility; target `prior_action`, `prior_realization`, or `observed_outcome`, `1` | optional `scope_ref`, `reason_refs` | Requires supported involvement or impact; effect: acknowledge regret and aid repair | covered |
| `support_socially.express_sympathy` | Express concern for an evidenced hardship or loss without claiming the user's hidden feelings; target `experience`, `concern`, or `observed_outcome`, `1` | none | Requires supported hardship/loss; effect: communicate sympathetic concern | uncovered |

Boundary decisions:

- `reassure` addresses concern through bounded confidence; `comfort` offers
  support without requiring a prediction; `encourage` supports forward effort.
- `appreciate` values a contribution or quality; `thank` expresses gratitude for
  something received.
- `apologize` requires a grounded action, impact, or failure and cannot invent
  culpability.

### `advise_or_influence`

Family purpose: attempt to influence a choice, behavior, or plan while preserving
the intended force and the user's agency.

Family target defaults: `goal`, `option`, `option_set`, `plan`, `plan_step`,
`concern`, `proposition`, or `boundary_scope`; cardinality `1+`.

Family outcome defaults: `goal_resolution`, `clarity`, `social_alignment`,
`engagement`, and `realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `advise_or_influence.advise` | Present or favor a feasible course with explicitly parameterized guidance strength; target `goal`, `option`, `option_set`, `plan`, or `plan_step`, `1+` | required `option_refs`, `guidance_strength`; optional `reason_refs`, `basis_refs`, `conditionality` | `guidance_strength` is `neutral_option`, `low`, `moderate`, or `high`; moderate/high guidance requires proportionately stronger basis; effect: expand or guide choice | covered |
| `advise_or_influence.warn` | Use supported risk information to influence a decision or behavior; target `concern`, risky `option`, `plan`, or `proposition`, `1+` | required `basis_refs`; optional `scope_ref` | Requires supported risk and behavioral relevance; effect: discourage exposure to risk | covered |
| `advise_or_influence.discourage` | Recommend against a course without necessarily asserting danger; target `option`, `plan`, or `plan_step`, `1+` | required `option_refs`; optional `reason_refs` | Requires grounded disadvantage; effect: lower attractiveness of course | uncovered |
| `advise_or_influence.request_user_action` | Ask the user to perform a concrete action; target `goal`, `plan_step`, or `request`, `1` | required `scope_ref`; optional `reason_refs`, `conditionality` | Requires user control and relevance; effect: request behavior | uncovered |
| `advise_or_influence.set_boundary` | State a persistent limit on acceptable interaction or action; target `boundary_scope`, `1` | required `scope_ref`; optional `reason_refs` | Requires actual applicable limit; effect: establish boundary | covered |

The former `propose_option`, `suggest`, and `recommend` keys are merged into
`advise`. Their useful difference is retained as a parameter rather than an
ontology distinction:

```text
neutral_option -> low -> moderate -> high
```

This is an ordinal semantic distinction, not a universal rule that greater
guidance is worse. `warn` is risk-grounded; `discourage` can be based on
non-dangerous disadvantages. `request_user_action` asks for behavior;
`set_boundary` declares a continuing limit.

### `commit_or_coordinate`

Family purpose: manage requests, suggestions, commitments, joint plans, and
responsibility for next steps.

Family target defaults: `request`, `suggestion`, `goal`, `plan`, `plan_step`,
`commitment`, or `capability`; cardinality `1` unless a plan has multiple steps.

Family outcome defaults: `goal_resolution`, `clarity`, `social_alignment`, and
`realization_fidelity`.

Commitment never creates capability or authorization. A promise or acceptance
that entails impossible, unauthorized, unsafe, private, or deceptive execution
is hard-invalid.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `commit_or_coordinate.offer` | Voluntarily make a feasible contribution available without claiming it has occurred; target `goal`, `plan_step`, or `capability`, `1` | optional `conditionality`, `commitment_level` | Requires actual capability; effect: add available contribution | covered |
| `commit_or_coordinate.promise` | Commit Columbina to a feasible future conversational or authorized action; target `plan_step` or `commitment`, `1` | required `commitment_level`; optional `conditionality` | Requires capability and authority; effect: create commitment | covered |
| `commit_or_coordinate.accept_request` | Accept responsibility for a specific understood request; target `request`, `1` | optional `conditionality` | Requires satisfiable authorized request; effect: accept request | covered |
| `commit_or_coordinate.decline_request` | Decline one request without necessarily creating a general boundary; target `request`, `1` | optional `reason_refs`, `scope_ref` | Requires represented request; effect: refuse request | covered |
| `commit_or_coordinate.accept_suggestion` | Accept a user-proposed course as the current conversational plan or option; target `suggestion`, `1` | optional `conditionality` | Requires compatible feasible suggestion; effect: adopt suggestion | covered |
| `commit_or_coordinate.decline_suggestion` | Reject adoption of a suggested course without declaring a general boundary; target `suggestion`, `1` | optional `reason_refs`, `scope_ref` | Requires represented suggestion; effect: reject suggestion | covered |
| `commit_or_coordinate.propose_plan` | Offer an ordered or structured way to pursue an active goal; target `goal` or `plan`, `1` | required referenced plan steps; optional `conditionality` | Steps must be grounded and feasible; effect: create plan proposal | covered |
| `commit_or_coordinate.state_next_step` | Identify the next grounded step without claiming completion; target `plan_step` or `goal`, `1` | required `scope_ref` to step | Requires represented plan/goal; effect: coordinate immediate continuation | covered |
| `commit_or_coordinate.defer` | Intentionally postpone a request, decision, or plan step while preserving its status; target `request`, `commitment`, `plan_step`, or `goal`, `1` | required `reason_refs` or grounded resume condition; optional `conditionality` | Must not imply guaranteed resumption without basis; effect: mark deferred | uncovered |
| `commit_or_coordinate.handoff` | Transfer responsibility or recommend transfer to a capable authorized party/system; target `request`, `goal`, or `plan_step`, `1` | required capable-recipient reference and `reason_refs` | Recipient capability/availability must be supported; effect: coordinate transfer | uncovered |

Boundary decisions:

- `offer` makes help available; `promise` creates a commitment.
- Accepting or declining a request concerns responsibility for requested action;
  accepting or declining a suggestion concerns adoption of a proposed course.
- `decline_request` is local; `set_boundary` declares a broader continuing limit.
- `defer` retains responsibility/status for later; `handoff` transfers it.

### `manage_dialogue`

Family purpose: control discourse continuity, topic, repair, pacing, opening, and
closure.

Family target defaults: `message`, `topic`, `prior_move`, `prior_action`,
`prior_realization`, `partial_plan`, or dialogue-state reference; cardinality
`0..1` unless the move repairs several linked failures.

Family outcome defaults: `clarity`, `engagement`, `social_alignment`,
`unnecessary_repetition`, and `realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `manage_dialogue.greet` | Open or reciprocate a conversational encounter; target active `dialogue_scope`, greeting `message`, or greeting `user_act`, `1` | none | Requires opening/greeting context; effect: plan an opening signal for the containing dialogue scope | covered |
| `manage_dialogue.close` | Mark the current conversation or topic as complete; target `dialogue_scope`, `goal`, `topic`, or `partial_plan`, `1` | optional `scope_ref` | Requires supported closure context; effect: plan a closure signal without claiming the other party considers the scope closed | covered |
| `manage_dialogue.introduce_topic` | Establish a topic not yet active; target `topic`, `1` | none | Requires grounded topic; effect: set active topic | covered |
| `manage_dialogue.shift_topic` | Move from the current topic to another grounded topic; target destination `topic`, `1` | required current/prior-topic `scope_ref` | Requires typed source and destination topics; effect: plan a topic transition | uncovered |
| `manage_dialogue.return_to_topic` | Resume a previously active topic after a diversion; target `topic`, `1` | none | Requires prior active topic; effect: restore topic | covered |
| `manage_dialogue.pause` | Temporarily suspend a dialogue process or plan without closing it; target `dialogue_scope`, `topic`, `plan`, or `partial_plan`, `1` | optional `reason_refs`, resume condition | Effect: plan a pause signal; must not imply external waiting capability or that the pause has already been mutually established | uncovered |
| `manage_dialogue.resume` | Continue a represented paused dialogue process or plan; target paused `dialogue_scope`, `topic`, `plan`, or `partial_plan`, `1` | none | Requires represented paused state; effect: plan a resume signal | uncovered |
| `manage_dialogue.repair` | Restore shared understanding after an interpretation, action, realization, or memory failure; target relevant `prior_action`, `prior_realization`, or `interpretation_hypothesis`, `1+` | optional `repair_scope`, `reason_refs` | Requires mismatch/correction evidence; effect: restore coordination | covered |
| `manage_dialogue.self_correct` | Replace or qualify Columbina's own prior statement or move; target `prior_move` or `prior_realization`, `1` | required correction-content reference; optional `basis_refs` | Requires identifiable error; effect: correct own record | covered |
| `manage_dialogue.request_repetition` | Ask the user to repeat unavailable or unusable content; target `message` or missing-input `unknown`, `1` | required `question_form`; optional `scope_ref` | Requires missing/corrupted content rather than semantic ambiguity; effect: reacquire input | uncovered |
| `manage_dialogue.redirect` | Move the interaction away from an unsuitable path toward a grounded permissible goal or topic; target source `goal`, `topic`, or `plan`, `1` | required destination `scope_ref`; optional `reason_refs` | Typed source and destination must be grounded and allowed; effect: plan a redirection relation | uncovered |

Boundary decisions:

- `shift_topic` changes topics by conversational choice; `redirect` responds to
  an unsuitable or blocked course; `return_to_topic` restores a prior topic.
- `request_repetition` addresses missing/unusable input; `clarify` addresses
  understood input whose meaning or arguments remain unresolved.
- `repair` coordinates recovery; `self_correct` specifically changes
  Columbina's previous content.

### `social_play`

Family purpose: make play itself a primary conversational effect. Humor used
only to express another move remains a realization/rhetorical strategy rather
than a separate social-play action.

Family target defaults: `social_target`, `experience`, `topic`, `prior_move`, or
`user_act`; cardinality `1`.

Family outcome defaults: `engagement`, `social_alignment`, `confusion`, and
`realization_fidelity`.

No social-play action is globally prohibited merely because a context is
serious. Appropriateness is learned policy. Hard validation still applies to
harassment, privacy, safety, unsupported claims, and prohibited content.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `social_play.joke` | Introduce a bounded humorous contribution whose primary effect is amusement; target `topic`, `experience`, or `social_target`, `1` | optional `scope_ref` | Effect: invite amusement; must preserve factual boundaries | covered |
| `social_play.playful_reframe` | Recast grounded content in a playful perspective while preserving its important meaning; target `experience`, `proposition`, or `prior_move`, `1` | optional `scope_ref` | Effect: change playful framing without changing facts | covered |
| `social_play.light_tease` | Direct mild affiliative teasing toward a supported social target; target `social_target` or consensual `prior_move`, `1` | optional `scope_ref` | Requires evidence that target/scope is appropriate; effect: affiliative play | uncovered |
| `social_play.banter` | Continue a reciprocal sequence of playful exchange; target playful `user_act` or `prior_move`, `1` | none | Requires reciprocal play context; effect: sustain playful exchange | uncovered |
| `social_play.play_along` | Accept and participate in the user's fictional, playful, or humorous frame without asserting it as fact; target `user_act`, playful `proposition`, or `topic`, `1` | optional `conditionality`, `scope_ref` | Must preserve frame boundary; effect: join play frame | uncovered |

Boundary decisions:

- `joke` introduces a humorous unit; `banter` continues reciprocal play;
  `play_along` joins the user's existing frame.
- `playful_reframe` changes perspective on content; `light_tease` has a social
  target and therefore requires stronger appropriateness evidence.

### `collaborate`

Family purpose (registry v0.2, Collaborative Content and Inquiry V0): deliver
shared work inside the conversation -- create, revise, explain, evaluate,
describe, compare, or summarize a conversational artifact.  It is never an
external side effect and needs no external capability.

Family outcome defaults: `goal_resolution`, `clarity`,
`explicit_user_correction`, and `realization_fidelity`.

| Action key | Boundary and accepted target | Semantic parameters | Specific evidence/effect | Coverage |
| --- | --- | --- | --- | --- |
| `collaborate.deliver_artifact` | Deliver a validated collaborative-content proposal; target `artifact_revision`, `1` | `artifact_operation` (closed, required); `basis_refs` (the proposal's content items, required) | Requires validated collaborative content; inhibitor: external-effect claim; effect: plan the artifact delivery | v0.2 addition |

Boundary decisions:

- One parameterized definition covers every artifact operation; the
  operation is a parameter, not a new action key.
- Delivering a proposal is not delivering an external effect: tool, send,
  order, and booking claims remain unlicensed.

## Special system operator: `STOP`

`STOP` is registered outside the nine conversational families.

```text
target: current partial plan and active goal state
semantic parameters: none
construction: always available after initial state validation
intended effect: terminate conversational-move selection for this response
hard inhibitors: none beyond invalid/corrupt planner state
policy inputs: marginal utility of another move, unresolved required content,
               goal progress, repetition, risk, response cost, runtime state
trace requirement: record the evidence and score contributions for stopping
```

`STOP` is a policy decision. Runtime exhaustion, cycle guards, cancellation, and
system failure are separate termination causes and must not be labeled as a
preferred conversational `STOP` outcome.

## Structured transition-effect contract

The prose descriptions above explain human-facing semantic boundaries. Runtime
transition code must compile the structured manifest below and must not parse
those descriptions to infer effects.

Every conversational action has exactly one primary V0 effect. A later schema
may add secondary effects, but each secondary effect would require its own key,
binding, strength, planned state, review cases, and version. V0 does not combine
several possible results into one effect.

### Transition domains

`transition_domain` selects the partial-plan ledger updated by the effect:

| Domain | Transition behavior |
| --- | --- |
| `target_effect` | Record a planned semantic or social effect under `addressed_target_effects` and `represented_effects` |
| `unknown_handling` | Record the effect and update `planned_unknown_handling`; never change an unknown into a known value |
| `contradiction_handling` | Record the effect and update `planned_contradiction_repairs`; never mark a contradiction actually repaired |
| `commitment_state` | Record a proposed, accepted, declined, or promised commitment as planned; capability and authorization remain unchanged |
| `dialogue_state` | Record an opening, topic, pacing, repair, or closure transition as planned within an explicit dialogue scope |

Goal coverage may be updated for any domain only through an explicit link from
the bound effect to an unresolved active-goal requirement. It is not an
additional action effect inferred from the action family.

### Binding modes

| Mode | Meaning |
| --- | --- |
| `each_target` | Apply the same effect independently to every bound target and create one target-effect ledger entry per target |
| `target_set` | Apply one indivisible effect to the canonical ordered target set; individual members are not separately marked covered |
| `primary_target` | Apply the effect to exactly one primary target; basis, scope, and evidence references do not receive coverage |
| `primary_with_support` | Apply the effect only to the declared primary target while retaining other bound objects as typed support |
| `source_destination` | Apply one relational transition with explicit `source` and `destination` roles; neither role may be omitted |
| `dialogue_scope` | Apply the effect to one explicit `dialogue_scope`; a bound message or user act locates the containing scope but is not itself changed |

For every mode, the canonical target or target set becomes part of the effect
identity used by coverage, residual need, repetition, tracing, and state
signatures. Arrays are normalized by reference identity before a `target_set`
key is built; surface order cannot create a different effect identity.

### Planned-state boundary

All manifest effects set `actual_outcome_claim=false`. `planned_state` describes
what the selected response is intended to represent after realization. It never
asserts that the user understood, agreed, felt reassured, changed behavior, or
that a jointly recognized dialogue state already exists.

Examples:

```text
clarify(unknown-A) -> elicitation_planned(unknown-A)
reassure(concern-A) -> reassurance_expression_planned(concern-A)
close(dialogue-A) -> closure_signal_planned(dialogue-A)
```

Possible user or world changes belong to the later outcome record. Realization
fidelity determines whether the selected planned effect was actually expressed.

`effect_strength` is `1.0` for every V0 entry below. A different value requires
a new reviewed definition version; runtime code cannot derive strength from
wording, intensity, confidence, personality, or model output.

### V0 transition-binding manifest

The following table is normative for registry compilation. The effect key plus
version, canonical binding, and planned state form the target-effect ledger key.

| Action key | Effect key | Domain / planned state | Binding mode and roles |
| --- | --- | --- | --- |
| `provide_information.answer` | `information.answer` | `target_effect` / `content_delivery_planned` | `primary_target`; primary: question or requested proposition |
| `provide_information.inform` | `information.inform` | `target_effect` / `content_delivery_planned` | `each_target`; primary: every proposition or semantic frame |
| `provide_information.explain` | `information.explanation` | `target_effect` / `explanation_planned` | `primary_target`; primary: proposition, semantic frame, or topic |
| `provide_information.elaborate` | `information.elaboration` | `target_effect` / `elaboration_planned` | `primary_target`; primary: prior move, proposition, or topic |
| `provide_information.summarize` | `information.summary` | `target_effect` / `summary_planned` | `target_set`; members: all bounded source targets |
| `provide_information.exemplify` | `information.example` | `target_effect` / `example_planned` | `primary_target`; primary: proposition or topic |
| `provide_information.justify` | `information.justification` | `target_effect` / `justification_planned` | `primary_with_support`; primary: prior move, proposition, or commitment; support: `basis_refs` |
| `provide_information.correct` | `information.correction` | `contradiction_handling` / `correction_planned` | `primary_with_support`; primary: proposition, semantic frame, or prior realization; support: contradictory `basis_refs` |
| `provide_information.disclose_uncertainty` | `information.uncertainty_qualification` | `target_effect` / `qualification_planned` | `each_target`; primary: every qualified information target |
| `provide_information.warn` | `information.risk_disclosure` | `target_effect` / `risk_disclosure_planned` | `each_target`; primary: every risk proposition, concern, or observed outcome |
| `seek_information.clarify` | `elicitation.clarification` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: unknown or unresolved semantic argument |
| `seek_information.disambiguate` | `elicitation.disambiguation` | `unknown_handling` / `elicitation_planned` | `primary_with_support`; primary: competing hypothesis set, unknown, or ambiguous argument; support: candidate answers |
| `seek_information.verify` | `elicitation.verification` | `unknown_handling` / `verification_planned` | `primary_with_support`; primary: tentative proposition or interpretation; support: basis refs |
| `seek_information.explore_goal` | `elicitation.goal` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: goal or goal-shaped unknown |
| `seek_information.explore_preference` | `elicitation.preference` | `unknown_handling` / `elicitation_planned` | `primary_with_support`; primary: preference unknown or option set; support: alternatives or scale bounds |
| `seek_information.request_evidence` | `elicitation.evidence` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: proposition, interpretation hypothesis, or evidence record |
| `seek_information.explore_reason` | `elicitation.reason` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: proposition, goal, option, or prior action |
| `seek_information.explore_experience` | `elicitation.experience` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: experience, experience interpretation, or unresolved experience argument |
| `seek_information.check_understanding` | `elicitation.understanding_check` | `unknown_handling` / `verification_planned` | `primary_target`; primary: tentative interpretation hypothesis or proposition |
| `seek_information.request_feedback` | `elicitation.response_feedback` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: prior move, prior action, prior realization, or plan |
| `give_feedback.acknowledge` | `feedback.receipt` | `target_effect` / `receipt_signal_planned` | `primary_target`; primary: user act, message, or request |
| `give_feedback.confirm_understanding` | `feedback.understanding_expression` | `target_effect` / `understanding_expression_planned` | `primary_target`; primary: interpretation hypothesis or proposition |
| `give_feedback.signal_misunderstanding` | `feedback.misunderstanding_disclosure` | `target_effect` / `misunderstanding_disclosure_planned` | `primary_target`; primary: unknown, semantic frame, or interpretation hypothesis |
| `give_feedback.paraphrase` | `feedback.paraphrase` | `target_effect` / `paraphrase_planned` | `target_set`; members: all content jointly restated |
| `give_feedback.reflect` | `feedback.reflection` | `target_effect` / `tentative_reflection_planned` | `primary_target`; primary: experience, claimed affect, or user act |
| `give_feedback.validate` | `feedback.conversational_validation` | `target_effect` / `validation_expression_planned` | `primary_target`; primary: user act, concern, or experience |
| `give_feedback.indicate_attention` | `feedback.attention` | `target_effect` / `attention_signal_planned` | `primary_target`; primary: contribution, salient state, topic, or explicit dialogue scope |
| `express_stance.agree` | `stance.agreement` | `target_effect` / `stance_expression_planned` | `primary_target`; primary: proposition or prior move |
| `express_stance.disagree` | `stance.disagreement` | `target_effect` / `stance_expression_planned` | `primary_target`; primary: proposition or prior move |
| `express_stance.partially_agree` | `stance.partial_agreement` | `target_effect` / `scoped_stance_expression_planned` | `target_set`; members: separable proposition or semantic-frame portions identified by `scope_ref` |
| `express_stance.challenge` | `stance.challenge` | `target_effect` / `reconsideration_invitation_planned` | `primary_with_support`; primary: proposition, interpretation, or reasoning move; support: basis refs |
| `express_stance.evaluate` | `stance.evaluation` | `target_effect` / `assessment_expression_planned` | `target_set`; members: evaluated options, plans, propositions, or prior actions; dimensions are support |
| `express_stance.express_preference` | `stance.preference` | `target_effect` / `preference_expression_planned` | `target_set`; members: compared option or option set |
| `express_stance.express_uncertainty` | `stance.uncertainty` | `target_effect` / `uncertain_stance_planned` | `primary_with_support`; primary: proposition, option, or interpretation; support: basis refs |
| `express_stance.withhold_judgment` | `stance.noncommitment` | `target_effect` / `noncommitment_expression_planned` | `primary_with_support`; primary: proposition, option set, or prior move; support: reason refs |
| `support_socially.reassure` | `social_support.reassurance` | `target_effect` / `reassurance_expression_planned` | `primary_with_support`; primary: concern, experience, or uncertain proposition; support: basis refs |
| `support_socially.encourage` | `social_support.encouragement` | `target_effect` / `encouragement_expression_planned` | `primary_target`; primary: goal, plan step, experience, or prior action |
| `support_socially.comfort` | `social_support.comfort` | `target_effect` / `comfort_expression_planned` | `primary_target`; primary: experience, concern, or claimed affect |
| `support_socially.validate_experience` | `social_support.experience_validation` | `target_effect` / `validation_expression_planned` | `primary_target`; primary: experience, claimed affect, or concern |
| `support_socially.congratulate` | `social_support.congratulation` | `target_effect` / `celebration_expression_planned` | `primary_target`; primary: observed outcome, experience, or goal |
| `support_socially.appreciate` | `social_support.appreciation` | `target_effect` / `appreciation_expression_planned` | `primary_target`; primary: user act, prior action, or experience |
| `support_socially.thank` | `social_support.gratitude` | `target_effect` / `gratitude_expression_planned` | `primary_target`; primary: user act, prior action, or message |
| `support_socially.apologize` | `social_support.apology` | `target_effect` / `regret_expression_planned` | `primary_target`; primary: prior action, prior realization, or observed outcome |
| `support_socially.express_sympathy` | `social_support.sympathy` | `target_effect` / `sympathy_expression_planned` | `primary_target`; primary: experience, concern, or observed outcome |
| `advise_or_influence.advise` | `choice_guidance.advice` | `target_effect` / `guidance_expression_planned` | `target_set`; members: grounded options/course represented by the goal, option set, plan, or steps |
| `advise_or_influence.warn` | `choice_guidance.risk_warning` | `target_effect` / `behavioral_warning_planned` | `target_set`; members: risk-relevant concerns, options, plans, or propositions |
| `advise_or_influence.discourage` | `choice_guidance.discouragement` | `target_effect` / `discouragement_expression_planned` | `target_set`; members: discouraged options, plan, or steps |
| `advise_or_influence.request_user_action` | `choice_guidance.action_request` | `target_effect` / `behavior_request_planned` | `primary_target`; primary: goal, plan step, or request under user control |
| `advise_or_influence.set_boundary` | `choice_guidance.boundary` | `target_effect` / `boundary_statement_planned` | `primary_target`; primary: boundary scope |
| `commit_or_coordinate.offer` | `coordination.offer` | `commitment_state` / `offer_expression_planned` | `primary_target`; primary: goal, plan step, or capability |
| `commit_or_coordinate.promise` | `coordination.promise` | `commitment_state` / `commitment_creation_planned` | `primary_target`; primary: plan step or commitment |
| `commit_or_coordinate.accept_request` | `coordination.request_acceptance` | `commitment_state` / `responsibility_acceptance_planned` | `primary_target`; primary: request |
| `commit_or_coordinate.decline_request` | `coordination.request_decline` | `commitment_state` / `refusal_expression_planned` | `primary_with_support`; primary: request; support: reason refs |
| `commit_or_coordinate.accept_suggestion` | `coordination.suggestion_acceptance` | `commitment_state` / `course_adoption_planned` | `primary_target`; primary: suggestion |
| `commit_or_coordinate.decline_suggestion` | `coordination.suggestion_decline` | `commitment_state` / `course_rejection_planned` | `primary_with_support`; primary: suggestion; support: reason refs |
| `commit_or_coordinate.propose_plan` | `coordination.plan_proposal` | `commitment_state` / `plan_proposal_planned` | `primary_with_support`; primary: goal or plan; support: referenced plan steps |
| `commit_or_coordinate.state_next_step` | `coordination.next_step` | `commitment_state` / `next_step_expression_planned` | `primary_target`; primary: plan step or goal linked to the required step ref |
| `commit_or_coordinate.defer` | `coordination.deferral` | `commitment_state` / `deferral_expression_planned` | `primary_with_support`; primary: request, commitment, plan step, or goal; support: reason or resume condition |
| `commit_or_coordinate.handoff` | `coordination.handoff` | `commitment_state` / `transfer_proposal_planned` | `primary_with_support`; primary: request, goal, or plan step; support: capable recipient and reason refs |
| `manage_dialogue.greet` | `dialogue.opening` | `dialogue_state` / `opening_signal_planned` | `dialogue_scope`; primary: containing dialogue scope; message or user act is grounding evidence |
| `manage_dialogue.close` | `dialogue.closure` | `dialogue_state` / `closure_signal_planned` | `dialogue_scope`; primary: explicit dialogue scope; goal, topic, or partial plan may define the bounded scope |
| `manage_dialogue.introduce_topic` | `dialogue.topic_introduction` | `dialogue_state` / `topic_activation_planned` | `primary_target`; primary: topic within the current dialogue scope |
| `manage_dialogue.shift_topic` | `dialogue.topic_shift` | `dialogue_state` / `topic_shift_planned` | `source_destination`; source: prior/current topic via `scope_ref`; destination: target topic |
| `manage_dialogue.return_to_topic` | `dialogue.topic_return` | `dialogue_state` / `topic_restoration_planned` | `primary_target`; primary: previously active topic |
| `manage_dialogue.pause` | `dialogue.pause` | `dialogue_state` / `pause_signal_planned` | `dialogue_scope`; primary: explicit dialogue scope; topic, plan, or partial plan may define the bounded scope |
| `manage_dialogue.resume` | `dialogue.resume` | `dialogue_state` / `resume_signal_planned` | `primary_target`; primary: paused dialogue scope, topic, plan, or partial plan |
| `manage_dialogue.repair` | `dialogue.repair` | `dialogue_state` / `coordination_repair_planned` | `target_set`; members: all linked prior-action, prior-realization, or interpretation-hypothesis targets; memory-failure evidence remains support |
| `manage_dialogue.self_correct` | `dialogue.self_correction` | `contradiction_handling` / `correction_planned` | `primary_with_support`; primary: prior move or realization; support: correction-content and basis refs |
| `manage_dialogue.request_repetition` | `dialogue.repetition_request` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: unavailable message or missing-input unknown |
| `manage_dialogue.redirect` | `dialogue.redirect` | `dialogue_state` / `redirection_planned` | `source_destination`; source: unsuitable goal, topic, or plan; destination: required grounded `scope_ref` |
| `social_play.joke` | `social_play.joke` | `target_effect` / `amusement_invitation_planned` | `primary_target`; primary: topic, experience, or social target |
| `social_play.playful_reframe` | `social_play.playful_reframe` | `target_effect` / `playful_framing_planned` | `primary_target`; primary: experience, proposition, or prior move |
| `social_play.light_tease` | `social_play.light_tease` | `target_effect` / `affiliative_tease_planned` | `primary_target`; primary: social target or consensual prior move |
| `social_play.banter` | `social_play.banter` | `target_effect` / `playful_exchange_continuation_planned` | `primary_target`; primary: playful user act or prior move |
| `social_play.play_along` | `social_play.play_along` | `target_effect` / `play_frame_participation_planned` | `primary_target`; primary: user act, playful proposition, or topic |
| `seek_information.inquire` | `elicitation.inquiry` | `unknown_handling` / `elicitation_planned` | `primary_target`; primary: inquiry |
| `collaborate.deliver_artifact` | `collaboration.artifact_delivery` | `target_effect` / `artifact_delivery_planned` | `primary_target`; primary: artifact revision |

`STOP` has no entry in this manifest because it produces no conversational
content effect. Its transition terminates plan construction and records the
zero-marginal-utility decision described in the canonical selector contract.

## Reviewed overlap decisions

The following decisions were accepted during review. Retained distinctions may
still be merged later if implementation or review shows that their targets,
effects, or outcomes cannot be distinguished reliably.

| Keys | Decision | Accepted distinction |
| --- | --- | --- |
| `provide_information.warn` / `advise_or_influence.warn` | Keep | Communicate risk / use risk to influence behavior |
| `disclose_uncertainty` / `express_uncertainty` | Keep | Qualify supplied content / take an uncertain stance |
| `give_feedback.validate` / `support_socially.validate_experience` | Keep | Validate conversational standing / socially validate expressed experience |
| `correct` / `disagree` / `challenge` | Keep | Evidence-backed replacement / opposition / request reconsideration |
| `acknowledge` / `indicate_attention` / `confirm_understanding` | Keep | Receipt / ongoing or salience-driven attention / commitment to an interpretation |
| former `propose_option` / `suggest` / `recommend` / `advise` | Merge into `advise` | Preserve the gradient as `guidance_strength`, not separate keys |
| `decline_request` / `set_boundary` | Keep | Reject one request / declare a continuing limit |
| `offer` / `promise` | Keep | Make help available / create a commitment |
| `defer` / `handoff` | Keep | Retain for later / transfer responsibility |
| `shift_topic` / `redirect` / `return_to_topic` | Keep | Ordinary change / corrective diversion / restoration |

Reviewers should merge any pair whose targets, parameters, evidence,
information-state effects, and outcome expectations cannot be distinguished in
natural positive and counterexample cases.

## Coverage and accepted shadow lifecycle

### Case-referenced candidates: 51

These keys appear somewhere in the current fixture. This list includes positive,
disfavored, and hard-invalid appearances and therefore is not itself the
selectable subset:

```text
advise_or_influence.advise
advise_or_influence.set_boundary
advise_or_influence.warn
commit_or_coordinate.accept_request
commit_or_coordinate.accept_suggestion
commit_or_coordinate.decline_request
commit_or_coordinate.decline_suggestion
commit_or_coordinate.offer
commit_or_coordinate.promise
commit_or_coordinate.propose_plan
commit_or_coordinate.state_next_step
express_stance.agree
express_stance.challenge
express_stance.disagree
give_feedback.acknowledge
give_feedback.confirm_understanding
give_feedback.paraphrase
give_feedback.reflect
give_feedback.signal_misunderstanding
give_feedback.validate
manage_dialogue.close
manage_dialogue.greet
manage_dialogue.introduce_topic
manage_dialogue.repair
manage_dialogue.return_to_topic
manage_dialogue.self_correct
provide_information.answer
provide_information.correct
provide_information.disclose_uncertainty
provide_information.elaborate
provide_information.exemplify
provide_information.explain
provide_information.inform
provide_information.summarize
seek_information.check_understanding
seek_information.clarify
seek_information.disambiguate
seek_information.explore_experience
seek_information.explore_goal
seek_information.explore_preference
seek_information.explore_reason
seek_information.request_evidence
seek_information.request_feedback
seek_information.verify
social_play.joke
social_play.playful_reframe
support_socially.apologize
support_socially.comfort
support_socially.congratulate
support_socially.reassure
support_socially.validate_experience
```

### Shadow-selectable definitions: 39

The exact 39-key subset with at least one acceptable-plan example is normative
in `../ACTION_SELECTION_V0_DESIGN.md`. Those definitions enter the initial
isolated implementation with lifecycle `shadow`. Shadow permits construction,
scoring, selection, and trace evaluation inside the non-live pipeline only.

The following 12 case-referenced keys lack a positive example and remain
non-selectable `candidate` definitions:

```text
advise_or_influence.set_boundary
advise_or_influence.warn
commit_or_coordinate.promise
express_stance.agree
express_stance.challenge
express_stance.disagree
give_feedback.confirm_understanding
give_feedback.validate
provide_information.elaborate
provide_information.inform
provide_information.summarize
support_socially.comfort
```

### Collaborative Content V0 additions: 2

Registry v0.2 adds two shadow-selectable definitions that the original design
fixture predates.  Their reviewed behavior is exercised by the Collaborative
Content and Inquiry V0 tests and behavioral results, not by the design cases:

```text
collaborate.deliver_artifact
seek_information.inquire
```

### Uncovered candidates: 24

These must remain unavailable to a V0 production selector until they receive at
least positive, boundary, and counterexample review evidence:

```text
advise_or_influence.discourage
advise_or_influence.request_user_action
commit_or_coordinate.defer
commit_or_coordinate.handoff
express_stance.evaluate
express_stance.express_preference
express_stance.express_uncertainty
express_stance.partially_agree
express_stance.withhold_judgment
give_feedback.indicate_attention
manage_dialogue.pause
manage_dialogue.redirect
manage_dialogue.request_repetition
manage_dialogue.resume
manage_dialogue.shift_topic
provide_information.justify
provide_information.warn
social_play.banter
social_play.light_tease
social_play.play_along
support_socially.appreciate
support_socially.encourage
support_socially.express_sympathy
support_socially.thank
```

### Promotion rule

No key is approved for live use automatically. The initial
`conversational-actions-v0.1-shadow` runtime registry contains only the 39
shadow-selectable definitions. A future live registry may contain only keys
whose definitions and examples have been separately reviewed, whose overlap
boundary remains distinguishable, and whose hard-gate, realization, and outcome
regression cases pass. Uncovered and boundary-only keys remain `candidate` or
may be removed or merged. Shadow status permits isolated scoring and logging but
never live selection.

## Deferred registry work

The architecture audit is complete. The following work is intentionally
deferred rather than an unresolved blocker for the bounded shadow subset:

1. Compile all selected targets against the canonical cross-stage reference
   index described in `../ACTION_SELECTION_V0_DESIGN.md`.
2. Normalize authoring shorthand such as `reason`, `option`, `steps`, and
   `next_step` into typed references in the implementation fixture adapter or a
   future schema-v2 fixture; do not create duplicate production contracts.
3. Add positive, boundary, and counterexample cases for the 24 uncovered keys in
   a separate registry-definition review suite; keep the main architecture
   fixture within its 24-32 case boundary.
4. Continue comparing overlap pairs during shadow evaluation and version or
   merge definitions only with new reviewed evidence.
5. Keep persistent shared signals outside the action ontology; a future
   consented interaction-protocol or relationship-memory contract owns them.
6. Compile family inheritance and the structured effect manifest into immutable
   runtime definitions without loading the design JSON in production.
7. Add positive evidence before promoting any of the 36 deferred definitions.
8. Treat realization, outcomes, behavioral calibration, training, and live
   promotion as separate later acceptance gates.

## V0 implementation binding notes

Recorded 2026-09-25 while implementing `action_selection_v0`. These notes
record how the shadow implementation reads rows above where the prose left a
binding choice open; they do not change any definition, effect, or lifecycle.

1. `seek_information.disambiguate` requires "at least two supported
   candidates". An explicitly represented referential ambiguity whose
   candidate set is open (for example the deictic "this" in
   `out-of-scope-coined-command`) satisfies that requirement and binds the
   `open_set` form; `choice` still requires two or more explicit candidate
   answer references. `clarify` on such an argument is a reviewed superclass
   binding (`semantic_fit` 0.75).
2. `manage_dialogue.repair` binds explicitly corrected interpretation, action,
   or memory failures. A realization-only misstatement is Columbina's own
   statement and binds `manage_dialogue.self_correct` instead.
3. Acknowledging a greeting or closing act is a reviewed superclass binding
   when the conventional reciprocal operator (`greet` or `close`) applies.
4. `manage_dialogue.greet` locates its dialogue scope with the greeting act,
   which is an accepted greet target; `manage_dialogue.close` records the
   closing act only as evidence because `close` accepts no user-act target.
5. Planned goal coverage from a conversational move is capped by what the move
   can achieve: direct links are permitted only for requirements whose
   satisfaction policy accepts realization evidence; argument grounding,
   contradiction resolution, and task completion accept at most supporting
   links.
6. Evidence requirements are enforced through a typed specification
   (`ActionDefinition.evidence_specification`, compiled in
   `action_selection_v0/registry.py`). The descriptive names above are kept
   and each is bound to exactly one typed role. Where a descriptive
   requirement is a property of the target itself (for example
   `two_supported_candidates` or `proportionate_basis`), it is bound to the
   target-grounding role; the remainder is enforced by the target schema,
   parameter schema, or priced by a soft risk. Non-shadow definitions require
   target grounding only until they are reviewed for shadow selection.
