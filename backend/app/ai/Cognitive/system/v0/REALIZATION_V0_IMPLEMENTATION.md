# Realization V0 implementation brief

## 1. Status and purpose

This document is the authoritative implementation handoff for **Realization
V0**, the final language-producing stage of the canonical cognitive path.

Status at authoring: **designed and ready for implementation; not implemented,
behaviorally calibrated, live-approved, or connected to the user-facing
response path**.

Realization V0 converts an already selected `ActionPlan` plus an explicit,
authorized content inventory into a natural-language candidate. It does not
interpret the user, select goals, choose actions, retrieve facts, call tools,
write memory, or decide what is true.

The governing rule is:

```text
The cognitive system decides what may be communicated.
The realization model decides only how that authorized meaning is worded.
```

The initial implementation is shadow-only. Its candidates are persisted for
inspection and comparison but never shown to the user and never substituted
for the existing chat response.

## 2. Canonical topology

The conceptual cognitive path becomes:

```text
Perception
  -> Interpretation
  -> Goal
  -> Appraisal
  -> Dynamic State
  -> Action Selection
  -> Realization V0
  -> shadow language candidate
```

Realization V0 contains three owned phases:

```text
ActionSelectionResult + authorized content + personality/state
  -> deterministic Realization Compiler
  -> bounded LLM Surface Realizer
  -> deterministic/independent Realization Validator
  -> accepted-shadow | rejected | skipped | provider-failed outcome
```

The LLM is not an assistant or an agent. It is a single-call transformation:

```text
realize(authorized meaning, resolved delivery controls) -> candidate wording
```

## 3. Non-negotiable boundaries

Realization V0 must preserve all of these:

1. Action Selection remains the sole owner of which conversational moves occur.
2. Only an authorized content item may become a factual or autobiographical
   assertion.
3. LLM pretrained knowledge is never a content source or evidence source.
4. Personality changes delivery, never facts, safety, authorization, selected
   moves, or epistemic status.
5. Dynamic State changes bounded delivery controls only; it never authorizes a
   move or claim.
6. Realization receives no tools, network capability, database session, memory
   writer, chat-agent loop, or recursive planning capability.
7. The model receives no raw conversation history and no open-ended instruction
   to “answer the user.”
8. The model cannot add questions, offers, apologies, jokes, commitments, or
   recommendations absent from the selected Action plan.
9. A model-produced alignment is diagnostic evidence, not self-validating
   proof.
10. A failed or rejected realization cannot affect the live response.
11. The accepted deterministic Perception-through-Action trace and semantic
    replay remain intact.
12. No candidate is described as live-approved merely because structural tests
    pass.

## 4. Package and ownership layout

Add these canonical contracts and implementation modules:

```text
backend/app/ai/Cognitive/system/v0/contracts/realization.py

backend/app/ai/Cognitive/system/v0/realization_v0/
  __init__.py
  errors.py
  models.py
  content.py
  style_manifest.py
  style_mapper.py
  action_manifest.py
  compiler.py
  model_protocol.py
  validator.py
  engine.py
```

Responsibilities:

- `contracts/realization.py`: immutable cross-stage request/result contracts.
- `content.py`: authorized content-index construction and resolution only.
- `style_manifest.py`: fixed numeric bands and observable delivery controls.
- `style_mapper.py`: pure personality/state-to-delivery compilation.
- `action_manifest.py`: reviewed realization requirements for every Action V0
  shadow definition.
- `compiler.py`: pure Action-to-Realization request compilation.
- `model_protocol.py`: provider-neutral one-call interface and the exact model
  payload projection.
- `validator.py`: candidate alignment, grounding, constraint, and integrity
  validation.
- `engine.py`: bounded generation attempts and terminal outcome assembly.
- `errors.py`: typed phase/code ownership with safe messages.

Provider-specific SDK code belongs outside the pure contract/compiler modules,
for example:

```text
backend/app/ai/Cognitive/system/v0/realization_provider.py
```

The provider adapter must implement the protocol defined by
`realization_v0/model_protocol.py`. Do not import a provider SDK from shared
contracts, the compiler, style mapper, or validator.

## 5. Versions

Define and persist independent versions:

```text
REALIZATION_CONTRACT_VERSION       = "realization-contract-v0.2"
REALIZATION_CONTENT_VERSION        = "realization-content-v0.1"
REALIZATION_ACTION_MANIFEST        = "realization-action-manifest-v0.1"
REALIZATION_STYLE_MANIFEST         = "realization-style-manifest-v0.1"
REALIZATION_COMPILER_VERSION       = "realization-compiler-v0.2"
REALIZATION_MODEL_PROTOCOL_VERSION = "realization-model-protocol-v0.2"
REALIZATION_VALIDATOR_VERSION      = "realization-validator-v0.1"
REALIZATION_ENGINE_VERSION         = "realization-engine-v0.1"
```

Every request, candidate, validation result, and trace must retain all relevant
versions. Changing numeric bands, action coverage, model payload shape,
validation behavior, or retry semantics requires the owning version to change.

The configured provider/model identifier is provenance, not a policy version.

## 6. Knowledge boundary and content inventory

### 6.1 Closed-world assertion rule

The compiler works under a closed-world realization policy:

```text
If a meaning is not present as an authorized content item, the model may not
assert it.
```

Absence of a memory is not proof that an event did not happen. For example, no
episodic record of visiting Japan authorizes “I do not have a memory of visiting
Japan,” not “I have never visited Japan.”

### 6.2 Allowed source kinds

Use a closed enum such as:

```text
current_user_statement
authorized_semantic_memory
authorized_episodic_memory
authorized_relationship_memory
trusted_runtime_observation
finalized_tool_result
reviewed_system_knowledge
accepted_prior_realization
retrieval_result
retrieval_no_result
```

There is deliberately no `model_knowledge`, `common_knowledge`, or
`llm_pretraining` source.

### 6.3 Content item types

Define an immutable discriminated union with at least:

```text
GroundedProposition
GroundedQuestion
KnowledgeGap
DialogueReference
AuthorizedQuote
```

All content items carry:

```text
content_id
cognitive_turn_id
kind
source_kind
source_refs
authorized_for_realization
sensitivity_class
epistemic_status
attribution
surface_anchors
```

`authorized_for_realization` must be exactly `true` before the item enters a
request. Unauthorized items fail compilation; they are never silently omitted
if a selected move requires them.

### 6.4 Grounded proposition

A general proposition must not be nutrition-specific. Use typed semantic terms:

```json
{
  "content_id": "claim:example",
  "kind": "grounded_proposition",
  "subject": {"ref": "entity:subject", "surface_anchor": "the subject"},
  "predicate": "registered.predicate.key",
  "arguments": [
    {
      "role": "value",
      "value_type": "decimal",
      "value": "12.600000",
      "unit": "gram"
    }
  ],
  "polarity": "positive",
  "modality": "asserted",
  "epistemic_status": "grounded",
  "certainty": "approximate",
  "attribution": "trusted_source",
  "source_kind": "reviewed_system_knowledge",
  "source_refs": ["source:123"],
  "authorized_for_realization": true,
  "sensitivity_class": "ordinary"
}
```

Numeric values use canonical decimal strings. Exact names, dates, identifiers,
units, negation, attribution, and uncertainty must remain machine-readable and
must not exist only inside prose.

### 6.5 Knowledge gap

Represent an actual failed retrieval separately:

```json
{
  "content_id": "gap:capital-question",
  "kind": "knowledge_gap",
  "topic_ref": "topic:capital-of-france",
  "surface_anchor": "the capital of France",
  "gap_kind": "no_grounded_answer",
  "source_kind": "retrieval_no_result",
  "source_refs": ["retrieval:turn-42"],
  "authorized_for_realization": true
}
```

This may support “I do not have a grounded answer.” It never supports an answer
from model weights.

### 6.6 User assertions

A user statement may be repeated or referenced only with retained attribution.
`current_user_statement` does not become `verified_fact` merely because the
realizer can phrase it fluently.

### 6.7 No raw memory handoff

The realizer does not receive the memory database, retrieved memory objects, or
unfiltered conversation history. Trusted producers project selected material
into the content inventory first. That projection is where authorization,
sensitivity, status, attribution, and provenance are fixed.

## 7. Realization request contract

Define `RealizationRequestV0` as an immutable `extra="forbid"` Pydantic model.
It contains:

```text
schema_version
request_id
cognitive_turn_id
conversation_ref
language_tag
action_result_ref
action_plan
content_items
move_bindings
discourse_controls
delivery_profile
constraints
provenance
request_digest
```

### 7.1 Action plan projection

Copy the exact selected move identity, order, targets, parameters,
`realization_constraints`, primary/supporting designation, and termination from
`ActionSelectionResult`. Do not ask the LLM to inspect the full Action trace or
choose among candidates.

Every move receives a `RealizationMoveBinding`:

```text
move_ref
action_key
required = true
content_refs
question_refs
target_surface_refs
constraint_codes
```

All selected moves are required in V0. Optionality belongs to Action Selection;
the realizer cannot silently omit a selected supporting move.

### 7.2 Discourse controls

Use explicit controls rather than raw conversational history:

```text
move_order
maximum_sentences
maximum_characters
response_mode
allowed_reference_forms
already_expressed_content_refs
```

V0 must not send the focus message or recent messages to the model. If a quote
or exact wording is necessary, supply an `AuthorizedQuote` content item with an
exact source span and permission.

### 7.3 Constraint union

Combine, without weakening:

- every `GroundedActionMove.realization_constraints` item;
- application-safety restrictions;
- content-item epistemic/attribution restrictions;
- global realization prohibitions; and
- action-manifest requirements.

Global prohibitions include:

```text
introduce_unsupported_claim
answer_from_model_knowledge
change_numeric_value_or_unit
drop_required_uncertainty
change_attribution
claim_unobserved_tool_execution
claim_unrecorded_memory
add_unplanned_question
add_unplanned_offer
add_unplanned_commitment
add_unplanned_advice
assert_hidden_affect
expose_internal_cognitive_state
reassert_corrected_content
```

## 8. Action-to-realization manifest

Create a static reviewed manifest with exactly one entry for every definition in
`action_selection_v0.registry.SHADOW_KEYS`.

Each entry declares:

```text
action_key
accepted_definition_version
allowed_content_kinds
minimum_content_items
maximum_content_items
question_required
assertion_permission
required_alignment_kind
style_permissions
additional_constraint_codes
```

Required family behavior:

| Action family | Realization requirement |
| --- | --- |
| `provide_information` | Requires authorized proposition/example/explanation content. Never obtains content from model weights. |
| `seek_information` | Requires a represented unknown, question, option set, or permission target. It may phrase only the selected question form. |
| `give_feedback` | May acknowledge an observed contribution without endorsing it. Paraphrase/reflect require authorized content and preserved epistemic status. |
| `support_socially` | Requires the represented experience/event/involvement. It cannot invent feelings, outcomes, or shared experience. |
| `advise_or_influence` | Requires a grounded feasible option and any basis demanded by the selected move. |
| `commit_or_coordinate` | Requires the exact capability/authorization basis accepted by Action gates. No completion claim is permitted. |
| `manage_dialogue` | Greeting/closing may be content-light; repair, return, self-correction, and topic moves require their represented targets. |
| `social_play` | Requires the reviewed play frame and must preserve the factual-frame boundary. |

### 8.1 Normative shadow-action profiles

The implementation agent must not decide the manifest policy while coding it.
Use the profiles and action assignments below as the V0 source of truth. The
static manifest expands every profile into the fields listed above; profile
names do not appear in the model payload.

Content-kind shorthand:

```text
P = GroundedProposition
Q = GroundedQuestion
G = KnowledgeGap
D = DialogueReference
A = AuthorizedQuote
* = no finite maximum after global request limits are applied
```

`assertion_permission` is a closed enum:

```text
none                 no declarative claim beyond the named dialogue/social act
grounded_only        only propositions explicitly bound to the move
attributed_only      only user/prior-source content with retained attribution
self_status_only     only the represented capability, uncertainty, or dialogue status
```

`style_permissions` are also closed sets. `neutral` permits only compression,
transition, and clause-shape controls. `warm` additionally permits one bounded
warmth marker. `question` permits question-tone control. `play` permits the
reviewed playfulness control. No profile permits style to add a move.

| Profile | Allowed kinds | Min | Max | Question | Assertion permission | Alignment kind | Style permissions | Mandatory constraints |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| `fact` | P, G | 1 | * | no | grounded_only | assertion | neutral | preserve_epistemic_status, preserve_attribution |
| `correction` | P, D, A | 2 | * | no | grounded_only | correction | neutral | replacement_required, basis_required, do_not_reassert_corrected_content |
| `uncertainty` | P, G | 1 | * | no | self_status_only | uncertainty | neutral | uncertainty_marker_required, no_gap_answer |
| `question` | Q, G, P, D | 1 | * | yes | attributed_only | question | question | one_selected_question_form, no_embedded_answer |
| `receipt` | D, A | 1 | 1 | no | none | receipt | warm | no_agreement, no_correctness_claim, no_full_understanding_claim |
| `misunderstanding` | G, D | 1 | 2 | no | self_status_only | misunderstanding | warm | scope_required, no_user_fault_claim |
| `paraphrase` | P, D, A | 1 | * | no | attributed_only | paraphrase | neutral | preserve_meaning, preserve_epistemic_status, no_new_claim |
| `reflection` | P, D, A | 1 | 2 | no | attributed_only | reflection | warm | preserve_tentativeness, no_hidden_affect |
| `social_support` | P, D, A | 1 | 2 | no | attributed_only | social_act | warm | no_external_fact_upgrade, no_hidden_affect |
| `reassurance` | P, D | 2 | * | no | grounded_only | reassurance | warm | basis_required, preserve_uncertainty, no_promise, no_guarantee |
| `apology` | D, P | 1 | 2 | no | self_status_only | apology | warm | involvement_or_impact_required, no_invented_culpability |
| `advice` | P, D | 1 | * | no | grounded_only | advice | neutral | feasible_option_required, preserve_guidance_strength, no_guarantee |
| `coordination` | P, D | 1 | * | no | self_status_only | coordination_act | neutral | preserve_commitment_level, no_completion_claim |
| `dialogue` | D, P, A | 1 | 2 | no | self_status_only | dialogue_act | neutral | preserve_dialogue_scope |
| `opening` | D | 0 | 1 | no | none | opening | warm | no_unplanned_question, no_unplanned_offer |
| `closing` | D, P | 0 | 1 | no | none | closing | warm | no_completion_claim_without_content, no_unplanned_offer |
| `repair` | D, P, A, G | 1 | * | no | self_status_only | repair | warm | represented_failure_required, preserve_repair_scope |
| `play` | P, D, A | 1 | 2 | no | attributed_only | play_frame | play | preserve_factual_frame, no_new_factual_claim |

The 39 V0 assignments are normative:

| Shadow action | Profile | Additional required binding or constraint |
| --- | --- | --- |
| `provide_information.answer` | fact | Bind the represented information request and its authorized answer; a gap may yield only a no-grounded-answer statement. |
| `provide_information.explain` | fact | Bind the explained target and explanation basis; causal language must already be represented. |
| `provide_information.exemplify` | fact | Bind both the externalized claim and a grounded example; do not invent an illustrative case. |
| `provide_information.correct` | correction | Bind prior/corrected content, replacement content, and contradictory basis. |
| `provide_information.disclose_uncertainty` | uncertainty | Bind every qualified target; do not introduce uncertainty for an unbound target. |
| `seek_information.clarify` | question | Bind the represented unresolved argument, term, or scope. |
| `seek_information.disambiguate` | question | Bind at least two supported candidate meanings and preserve their labels. |
| `seek_information.verify` | question | Bind the tentative proposition/interpretation and preserve tentative status. |
| `seek_information.explore_goal` | question | Bind the represented goal gap; do not suggest a goal. |
| `seek_information.explore_preference` | question | Bind the preference gap and any selected options; do not infer a preference. |
| `seek_information.request_evidence` | question | Preserve `requested_evidence_type`; do not imply that evidence already exists. |
| `seek_information.explore_reason` | question | Bind the represented stance, choice, claim, or prior action. |
| `seek_information.explore_experience` | question | Bind the evidenced experience; never assert hidden affect. |
| `seek_information.check_understanding` | question | Use only `question_form=check`; externalize the exact tentative interpretation. |
| `seek_information.request_feedback` | question | Bind the actual response, plan, action, or interaction being evaluated. |
| `give_feedback.acknowledge` | receipt | Acknowledgement means receipt/attention only. |
| `give_feedback.signal_misunderstanding` | misunderstanding | Bind the unresolved content and disclose Columbina's status, not a user defect. |
| `give_feedback.paraphrase` | paraphrase | All target content is attributed; no compression may change scope or modality. |
| `give_feedback.reflect` | reflection | Bind expressed experience/affect or a tentative observed pattern only. |
| `support_socially.reassure` | reassurance | Bind concern/experience plus the supporting basis; unsupported outcome prediction is forbidden. |
| `support_socially.validate_experience` | social_support | Validate understandability of the experience/reaction, not external truth. |
| `support_socially.congratulate` | social_support | Bind a supported achievement or positive milestone. |
| `support_socially.apologize` | apology | Bind Columbina's actual action, realization, involvement, or observed impact. |
| `advise_or_influence.advise` | advice | Bind every selected option/step and preserve `guidance_strength` and conditionality. |
| `commit_or_coordinate.offer` | coordination | Bind an actual capability and keep the contribution prospective. |
| `commit_or_coordinate.accept_request` | coordination | Bind the exact satisfiable authorized request; acceptance creates no completion claim. |
| `commit_or_coordinate.decline_request` | coordination | Bind the exact request and all selected reasons; do not create a general boundary. |
| `commit_or_coordinate.accept_suggestion` | coordination | Bind the exact compatible feasible suggestion and its conditionality. |
| `commit_or_coordinate.decline_suggestion` | coordination | Bind the exact suggestion and all selected reasons; do not create a general boundary. |
| `commit_or_coordinate.propose_plan` | coordination | Bind the goal/plan and at least two ordered grounded steps. |
| `commit_or_coordinate.state_next_step` | coordination | Bind exactly the represented next step; explicitly forbid completion language. |
| `manage_dialogue.greet` | opening | Zero content is allowed only with reviewed opening-context evidence. |
| `manage_dialogue.close` | closing | Zero content is allowed only for conversational closure; goal completion requires bound content. |
| `manage_dialogue.introduce_topic` | dialogue | Bind the grounded inactive topic. |
| `manage_dialogue.repair` | repair | Bind mismatch/correction evidence and preserve the selected `repair_scope`. |
| `manage_dialogue.return_to_topic` | dialogue | Bind the previously active topic; do not claim prior content not represented. |
| `manage_dialogue.self_correct` | correction | Bind Columbina's prior move/realization, correction content, and basis. |
| `social_play.joke` | play | Bind the reviewed topic/experience/target and keep invented joke material visibly non-factual. |
| `social_play.playful_reframe` | play | Bind the original content and preserve its proposition, polarity, attribution, and epistemic status. |

The manifest test must compare this exact key set with `SHADOW_KEYS`, not merely
assert a count of 39. If Action changes lifecycle or definition version, import
must fail until this table and a new manifest version are reviewed.

Manifest import must fail if:

- any shadow action is absent;
- any unknown action is present;
- an action definition version differs;
- an assertion-capable action permits zero authorized content without a
  reviewed exception; or
- a question action does not require a question/unknown binding.

Candidate, boundary-only, deprecated, or future action definitions are not
realizable until separately added to a new manifest version.

## 9. Personality and style mapping

### 9.1 Never send unexplained numbers

The model must not receive `warmth=0.8` and be expected to invent its meaning.
Retain numeric values for provenance, then deterministically compile them into
named bands and concrete delivery controls.

Use canonical six-place Decimal values and this exact V0 partition:

```text
[0.000000, 0.200000) -> very_low
[0.200000, 0.400000) -> low
[0.400000, 0.600000) -> moderate
[0.600000, 0.800000) -> high
[0.800000, 1.000000] -> very_high
```

Therefore `0.800000` is unambiguously `very_high`.

### 9.2 Resolved delivery profile

Define a `ResolvedDeliveryProfile` that contains source provenance plus
concrete controls. The model payload receives the resolved controls, not the
raw numbers alone.

Required controls include:

```text
warmth_band
directness_band
playfulness_band
curiosity_band
transition_style
correction_style
follow_up_style
question_tone
content_lead_style
compression_preference
style_marker_budget
playfulness_intensity
endearment_policy
emotional_claim_policy
exclamation_policy
```

V0 invariants:

- Endearments are always forbidden.
- Unprovided emotional-state claims are always forbidden.
- Exclamation is `avoid` unless a selected social action explicitly permits it.
- Warmth may soften an already selected move but cannot add acknowledgement,
  apology, reassurance, offer, or follow-up moves.
- Directness affects clause shape within a move; it cannot reorder selected
  moves or remove required uncertainty.
- Playfulness has no effect unless a selected `social_play.*` move or reviewed
  playful-reframe permission exists.
- Curiosity affects only the phrasing of an already selected
  `seek_information.*` move. It cannot create a question.
- Style-marker budget is at most one in V0.

The exact mapping from bands to controls belongs in
`style_manifest.py`, not prose, fixtures, or a provider prompt.

### 9.3 Dynamic State

Do not send raw state scores to the model. Consume only the current snapshot's
reviewed `derived_controls` through a versioned mapping:

- `response_energy` may set `compression_preference`.
- `repair_pressure` may alter explicitness only when a repair/self-correction
  move is selected.
- `clarification_pressure` may alter question directness only when a
  seek-information move is selected.
- `exploration_readiness` may alter openness only within the selected question
  form.
- `initiative_readiness` and `slow_cognition_pressure` have no Realization V0
  effect and must be recorded as intentionally unused.

Epistemic confidence never independently changes hedging. Uncertainty wording
is controlled by content epistemic status and Action realization constraints.

### 9.4 Meaning invariance tests

Across every personality/style extreme, authorized proposition ids, values,
units, polarity, attribution, uncertainty, selected moves, and move order must
remain identical. Only permitted surface wording may differ.

## 10. Deterministic compiler

`RealizationCompilerV0.compile(...)` is pure: no model, database, clock, random
value, network, memory retrieval, or provider import.

Inputs:

```text
ActionSelectionResult
RealizationContentInventory
ActionPersonalityProfile
current StateSnapshot
language tag
application safety basis
```

Algorithm:

1. Validate ownership and version lineage.
2. Require Action result `shadow_only=true` for this shadow implementation.
3. Resolve every selected action through the realization manifest.
4. Resolve move target/evidence/content refs through the authorized content
   index.
5. Reject unauthorized, stale, superseded, deleted, cross-turn, or unresolvable
   content.
6. Preserve selected move order exactly.
7. Compile numeric personality values to bands and concrete controls.
8. Apply only the reviewed Dynamic State control mappings.
9. Union Action, content, safety, and global constraints.
10. Build one immutable request atomically.
11. Canonically serialize and hash it.
12. Reparse it and require value equality before returning.

There is no partial request. A missing required content item yields a typed
compiler outcome; it never becomes neutral text or a request for the LLM to
fill the gap.

Deterministic ids use content-addressed or UUID5-style derivation from versioned
lineage. Do not use UUID4, process hash order, or wall-clock time.

## 11. Model protocol: structured transducer, not assistant

Define a provider-neutral protocol:

```python
class RealizationModel(Protocol):
    def generate(self, payload: RealizationModelPayload) -> RealizationModelOutput: ...
```

The canonical model payload is a strict projection of the validated request:

```text
protocol version
language
exact current user utterance, typed as non-authoritative linguistic context
ordered move bindings
authorized content items
resolved delivery controls
discourse controls
constraints
prior typed validation codes (retry only)
```

The utterance may be used only for natural wording and local reference
resolution. It cannot supply a fact, add an action, grant authorization, or
override a safety decision; those remain controlled by the selected moves and
authorized content inventory.

It excludes:

- prior chat messages and any untyped transcript;
- system/assistant/user role history;
- full Perception, Interpretation, Goal, Appraisal, or Dynamic State traces;
- rejected Action candidates and scores;
- memories not projected into authorized content;
- tools or tool schemas;
- database identifiers unnecessary for alignment;
- free-form “be helpful” or persona instructions; and
- hidden chain-of-thought requests.

### 11.1 No dynamic prose prompting

The canonical implementation must not assemble a natural-language prompt from
templates. If a hosted SDK accepts only text input, serialize the canonical JSON
payload mechanically under the fixed protocol version and require a structured
JSON-schema response. The adapter may not concatenate conversational history,
developer instructions, an assistant persona, or per-case prose.

If a provider cannot enforce the required structured output and tool-free
single-call behavior, it is unsupported and the stage fails closed.

### 11.2 Model call restrictions

- One provider call per attempt.
- No streaming into chat.
- No tools or function calls.
- No retrieval.
- No provider-managed conversation/session memory.
- No recursive agent loop.
- No fallback to `chat_model` or another model.
- Explicit model id; missing configuration means unavailable.
- Bounded timeout and output length.
- At most two generation attempts.
- Record model id, protocol version, decoding parameters, attempt count, and
  latency; do not store provider chain-of-thought.

Temperature zero may reduce variation but must not be represented as
determinism.

## 12. Candidate contract

Require structured output:

```text
schema_version
candidate_id
request_ref
text
move_alignments
content_alignments
omitted_move_refs
model_provenance
```

Each alignment contains a referenced id and UTF-8-safe character span into
`text`. Spans must be ordered, non-negative, within bounds, and must slice a
non-empty substring.

`candidate_id` is derived after generation from the request digest, attempt
number, and canonical candidate payload. It is not supplied as trusted provider
identity.

No reasoning field is accepted. Extra output fields fail validation.

## 13. Validation

The model's text and self-reported alignments are untrusted input.

Define closed phases:

```text
schema
ownership
move_coverage
move_order
content_grounding
epistemic_fidelity
constraint_enforcement
style_bounds
final_integrity
```

At minimum validate:

1. Output schema and request ownership.
2. Every selected move is aligned exactly once unless its manifest explicitly
   permits shared realization with another move.
3. No unselected move is claimed or expressed by a registered structural
   detector.
4. Required move order is preserved.
5. Every asserted number, unit, proper name, date, identifier, and quoted span
   matches an authorized content item.
6. Negation, modality, attribution, polarity, and uncertainty are preserved.
7. User assertions remain attributed where required.
8. A knowledge gap is not transformed into a factual answer.
9. No tool completion, memory, promise, guarantee, advice, question, offer,
   apology, reassurance, or affect claim appears without the matching selected
   move/content authorization.
10. Every inherited `RealizationConstraint` is enforced.
11. Corrected or known-false content is not reasserted.
12. Social play preserves fictional/playful framing.
13. Style marker count and hard delivery controls are respected.
14. Candidate text is non-empty, within limits, valid Unicode, and contains no
    control-channel or internal-state disclosure.

Use deterministic detectors for exact anchors, numeric/unit/date integrity,
move punctuation/form, forbidden claims, and required markers. A separate
semantic verifier may be added, but it cannot replace deterministic checks and
must never be the same call or self-verdict as the generator.

Because unrestricted semantic entailment cannot be proven perfectly, a V0
candidate that passes is called **accepted for shadow review**, never
truth-guaranteed or live-approved.

### 13.1 Typed violations

Include stable codes such as:

```text
output_schema_invalid
request_ownership_mismatch
required_move_missing
unplanned_move_detected
move_order_changed
content_alignment_missing
unsupported_claim_detected
numeric_value_changed
unit_changed
polarity_changed
attribution_dropped
required_uncertainty_dropped
knowledge_gap_answered
false_tool_completion_claim
unplanned_question
unplanned_offer
unplanned_commitment
unplanned_advice
hidden_affect_asserted
corrected_content_reasserted
internal_state_disclosed
style_marker_budget_exceeded
output_too_long
```

Validation errors sent into the optional second attempt are codes and refs
only, never free-form coaching.

## 14. Terminal realization outcome

Define a `RealizationTraceV0` with one closed status:

```text
accepted_shadow
rejected
skipped_missing_content
skipped_unsupported_action
provider_unavailable
provider_failed
```

It records:

```text
request and request digest
attempt records
final candidate, if any
validation result
terminal status
versions
provider/model provenance
timings
```

Rejected candidates may be retained inside the protected shadow trace for the
same 14-day local retention period, but must never be written to ordinary logs,
chat messages, learning memory, or a public API.

No candidate text is a success or quality label. Human review remains separate.

## 15. Failure ownership

Use `RealizationExecutionError` only for deterministic implementation/contract
failures. Provider transport errors become typed provider outcomes or bounded
worker retries according to the durable shadow policy.

Stable phases:

```text
request_validation
action_manifest_resolution
content_resolution
style_mapping
request_compilation
model_protocol
provider_call
candidate_validation
trace_validation
```

Safe messages contain no user text, model output, memory content, or provider
error body.

Missing authorized content is not an invitation for the model to improvise. It
produces `skipped_missing_content` unless the selected action is explicitly
content-light under the manifest.

## 16. Shadow integration and persistence

### 16.1 Preserve the deterministic core

Do not put an online model call inside the pure `CognitivePipelineV0.run()` or
change the meaning of its existing semantic digest. The accepted
Perception-through-Action trace remains independently replayable.

After that trace completes, the cognitive-shadow worker may run Realization V0
as a downstream extension:

```text
stored capture
  -> deterministic CognitivePipelineV0
  -> deterministic RealizationRequestV0 compilation
  -> provider call
  -> validation
  -> one atomic terminal persistence containing both trace components
```

### 16.2 Persistence extension

Add nullable/versioned realization fields to the shadow terminal record and
database row rather than embedding nondeterministic text into the existing core
semantic digest:

```text
realization_status
realization_request_payload
realization_request_digest
realization_trace_payload
realization_trace_digest
realization_failure_payload
realization_timings
```

Add a migration and database checks enforcing valid combinations. Existing
rows migrate to `realization_status=not_requested` with null realization
payloads.

Core `terminal_status` continues to describe the deterministic cognitive
observation. Realization has its own terminal status so a provider failure does
not erase a valid Action trace or mislabel a rejected candidate as a completed
utterance.

Insert the core and realization artifacts atomically in the existing terminal
write. Do not update immutable terminal rows later.

### 16.3 Digests and replay

- Keep the existing core semantic digest unchanged.
- Add a separate realization semantic digest over the canonical request,
  candidate, validation result, status, and realization policy versions.
- Exclude wall-clock timings and provider request ids from semantic equality.
- Replay never calls the LLM.
- Replay recompiles the request under matching versions, compares its digest,
  revalidates the stored candidate, and compares the realization semantic
  digest.
- Model/protocol/version differences return `version_unavailable`; never replay
  under a different realization policy.

### 16.4 Retention and deletion

Realization payloads inherit the exact shadow job's retention, conversation
deletion, and account deletion behavior. No separate retention class or orphan
table is allowed.

## 17. Current live-shadow omissions

The current local capture explicitly omits Action grounded objects, target
descriptors, prior realizations, observed outcomes, and most memory/runtime
producers. Add a new explicit omission for the authorized realization content
inventory until a producer is connected.

Expected early behavior:

- content-light greetings, acknowledgements, closings, and generic
  clarification moves may produce candidates;
- factual answers, explanations, corrections, advice, commitments, and social
  claims normally skip unless all required authorized content is present; and
- the model must never compensate for missing producers with pretrained
  knowledge.

This limited output is correct coverage evidence, not a reason to weaken
grounding.

## 18. Configuration

Add conservative settings:

```text
cognitive_shadow_realization_enabled = false
cognitive_shadow_realization_model = None
cognitive_shadow_realization_timeout_seconds = 20
cognitive_shadow_realization_max_attempts = 2
cognitive_shadow_realization_max_characters = 1200
```

Requirements:

- Disabled by default.
- Enabling requires an explicit model id; there is no fallback to `chat_model`,
  `cognitive_model`, or a subagent model.
- Realization cannot enable live shadow by itself.
- Disabling realization leaves the deterministic shadow pipeline unchanged.
- The local 14-day retention policy applies automatically when realization is
  enabled inside the local shadow canary.
- No setting permits candidate delivery to chat in V0.

## 19. Required tests

### 19.1 Contract tests

- `extra="forbid"`, immutability, JSON round trips, enums, uniqueness, ordering,
  character spans, numeric canonicalization, ownership, and digest integrity;
- invalid/unauthorized/cross-turn content fails closed;
- LLM/model knowledge is not an allowed source kind;
- no reasoning or unknown output fields are accepted; and
- all version fields are retained.

### 19.2 Action-manifest tests

- exact parity with all 39 current `SHADOW_KEYS`;
- no candidate/boundary/uncovered action is accepted accidentally;
- every action definition version matches;
- assertion actions require authorized content;
- question actions require represented question/unknown content; and
- all Action `realization_constraints` survive compilation.

### 19.3 Style tests

- exact boundary tests at `0`, `0.199999`, `0.2`, `0.399999`, `0.4`,
  `0.599999`, `0.6`, `0.799999`, `0.8`, and `1`;
- specifically assert `0.800000 -> very_high`;
- one personality source changes only its declared delivery controls;
- playfulness has no effect without a selected play action;
- curiosity cannot create a question;
- warmth cannot create an acknowledgement/apology/offer;
- directness cannot reorder moves or remove uncertainty;
- Dynamic State consumes only reviewed derived controls; and
- meaning/content/action invariance across all style extremes.

### 19.4 Compiler tests

- golden request with deterministic id/digest and byte-equivalent JSON;
- repeated process/hash-seed determinism;
- every selected move binds exactly once in order;
- missing content yields a typed skip, not fabricated neutral content;
- knowledge-gap content cannot bind as an answer proposition;
- user assertions retain attribution;
- numbers, units, dates, polarity, modality, and uncertainty survive exactly;
- unauthorized/sensitive content is rejected according to policy; and
- no input object is mutated.

### 19.5 Model-protocol isolation tests

Use a recording fake provider and prove the payload carries the exact current
utterance with the non-authoritative usage policy and excludes:

- prior messages and history;
- memories not projected into content;
- tools and tool schemas;
- rejected action candidates;
- raw appraisal/state traces;
- chat-agent/system prompts; and
- provider conversation/session ids.

Prove one call per attempt, no streaming, no tools, bounded timeout/output, no
model fallback, and at most two attempts.

### 19.6 Adversarial validator tests

Mutate an otherwise valid candidate to introduce each violation code, including:

- unsupported facts;
- a famous fact known to the base model but absent from content;
- changed numeric value/unit/date/name;
- reversed negation;
- dropped uncertainty or attribution;
- a knowledge-gap answer;
- an invented personal memory;
- false tool completion;
- an extra question, offer, recommendation, promise, apology, reassurance, or
  joke;
- hidden affect assertion;
- reordered or missing moves;
- reasserted corrected content;
- internal cognitive terminology; and
- excessive style markers or length.

Every mutation must reject, retain typed codes, and leave the live response
untouched.

### 19.7 Domain-general design cases

Create a reviewed fixture and authoring README covering at least:

1. greeting with no factual content;
2. acknowledgement without factual endorsement;
3. clarification of a missing referent;
4. factual question with no memory/tool content -> knowledge-gap disclosure;
5. factual question with an authorized semantic-memory proposition;
6. user-stated relationship fact with attribution;
7. no episodic record -> “no memory of,” not “never happened”;
8. tentative claim preserving uncertainty;
9. explicit correction and self-correction;
10. explanation with multiple grounded propositions;
11. advice with a grounded option and basis;
12. commitment blocked by unavailable application safety;
13. tool result available versus tool result missing;
14. social validation without hidden-affect assertion;
15. apology with supported involvement only;
16. playful framing that does not turn fiction into fact;
17. multi-move order and `STOP`;
18. identical meaning under personality extremes; and
19. non-nutrition topics across personal, practical, social, and factual turns.

Fixtures are bootstrap/regression material, not learning data or proof of
human-like conversation quality.

### 19.8 Shadow persistence tests

Against real PostgreSQL:

- realization disabled leaves existing trace behavior byte/semantically
  unchanged;
- accepted, rejected, skipped, and provider-failed realization outcomes persist
  with valid combinations;
- no partial realization payload is persisted;
- provider failure preserves the completed deterministic Action trace;
- retries never alter the captured input or core trace;
- replay recompiles/revalidates without a provider call;
- realization version change returns version-unavailable or starts the required
  lineage boundary without corrupting prior records;
- account/conversation deletion and 14-day retention remove realization data;
- candidate text never appears in logs; and
- shadow success/failure/rejection cannot alter chat tokens, assistant message,
  ChatJob status, tools, or memory.

### 19.9 Regression

Run focused Realization, Action, Pipeline, live-shadow, replay, retention, and
chat-isolation suites, then the complete backend suite and `git diff --check`.

## 20. Acceptance criteria

Realization V0 is implementation-accepted for shadow review only when:

1. every selected shadow Action has a reviewed realization-manifest entry;
2. one immutable authorized content inventory is the only source of claims;
3. no provider/model knowledge is represented as Columbina's knowledge;
4. missing content skips or rejects instead of being invented;
5. personality numbers are deterministically mapped to bands and concrete
   controls, including exact `0.800000 -> very_high` behavior;
6. personality and state cannot change meaning, actions, evidence, safety, or
   authorization;
7. the provider receives a strict JSON projection, no conversation-agent
   prompt/history/tools;
8. generation is single-call, bounded, non-recursive, and model-explicit;
9. all candidate output is treated as untrusted and independently validated;
10. typed adversarial mutations fail closed;
11. the deterministic Perception-through-Action trace and replay remain intact;
12. realization has an independent digest and replay-without-generation path;
13. every terminal realization outcome is durable and internally consistent;
14. retention/deletion/privacy behavior remains at least as strict as the
    source shadow trace;
15. no candidate affects live chat, tools, or memory;
16. focused real-PostgreSQL and complete backend suites pass;
17. an independent audit finds no implementation blocker; and
18. the canonical README records exact versions, scope, test counts, and the
    continuing lack of live approval.

Passing these criteria does not establish naturalness, semantic-verifier recall,
behavioral calibration, or readiness to replace the existing response path.

## 21. Implementation order

Implement in this order:

1. shared content/request/candidate/validation/trace contracts;
2. versioned action-realization manifest;
3. versioned numeric style manifest and mapper;
4. authorized content index;
5. pure deterministic compiler;
6. adversarial compiler/style/manifest tests;
7. provider-neutral structured model protocol with recording fake;
8. candidate validator and mutation suite;
9. bounded engine and terminal outcomes;
10. provider adapter behind disabled configuration;
11. separate shadow persistence fields, migration, digests, and replay;
12. local shadow integration and isolation tests;
13. domain-general design fixture;
14. focused and complete regression suites;
15. independent audit; and
16. canonical README status update.

Do not begin by wiring model text into the chat response.

## 22. Prohibited shortcuts

Do not:

- reuse the existing chatbot agent or its system prompt;
- pass the chat transcript to the realization model;
- ask the model to choose actions or decide what is true;
- treat model weights as SemanticMemory;
- let the model call tools, retrieve memory, or browse;
- fall back to another configured model;
- pass raw personality numbers without the versioned style mapping;
- allow style to add conversational moves;
- treat self-reported content refs as validation;
- use the same generation call as the sole semantic verifier;
- silently drop required Action moves;
- convert missing content into generic factual prose;
- log prompts, content inventory, candidate text, or provider error bodies;
- include model output in the existing deterministic semantic digest;
- regenerate during replay;
- write realization output to LearningMemory;
- expose candidates through a public endpoint in this task;
- enable realization by default; or
- make realization authoritative for a user-facing response.

## 23. Handoff result

When implementation is complete, report:

- files and migrations changed;
- exact contract/manifest/compiler/protocol/validator/engine versions;
- exact shadow Action manifest coverage;
- model/provider configuration and confirmation that no fallback exists;
- focused and complete pass/skip counts;
- real-PostgreSQL persistence/replay/retention results;
- confirmation that no raw conversation history or agent prompt reaches the
  model;
- confirmation that no realization candidate affects chat, tools, or memory;
- known validator limitations and behavioral-review needs; and
- remaining blockers before any live-response experiment.

## 24. Addendum: requirement-resolution content (2026-09-30)

Normative text: [`REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md`](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md) §7 and §9. `live_shadow.realization.realization_inventory`
merges the trace's requirement content (producer
`requirement-resolution-content-projection-v0.1`) with the dialogue
inventory. A trace without it produces exactly the historical inventory, and a
content-id collision fails closed as a content-resolution error. The content
uses reviewed anchors only: "your request", "that", "Today's date in UTC", "my
name", and the clarification-gap anchors from `ELICITATION_ANCHORS`. Decline reasons are
negative-polarity propositions over positive states. **The validator and the
model protocol are unchanged.** Faithful wording is accepted as written, and a
clarification that echoes the user's request is rejected because it does not
bind the reviewed anchor. Known gap: an echo that includes the anchor is still
accepted (see the behavioral results).
