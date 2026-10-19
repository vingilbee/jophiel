# Conversational Workspace and Collaborative Content V0 — Implementation Report

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Status (2026-10-02): **implemented for shadow; Gates 1–6 complete; Gate 7
(durable persistence) and Gate 8 (real-application evaluation) not done.**
Not independently audited, not behaviorally accepted, not live-approved.
Nothing here grants a shadow result live authority.

This implements
[`COLLABORATIVE_CONTENT_AND_INQUIRY_V0_IMPLEMENTATION_INSTRUCTIONS.md`](COLLABORATIVE_CONTENT_AND_INQUIRY_V0_IMPLEMENTATION_INSTRUCTIONS.md).
Behavioral evidence is in
[`COLLABORATIVE_CONTENT_AND_INQUIRY_V0_BEHAVIORAL_RESULTS_2026-10-02.md`](COLLABORATIVE_CONTENT_AND_INQUIRY_V0_BEHAVIORAL_RESULTS_2026-10-02.md).

## 1. What changed and why

The pipeline previously knew two outcomes for a request: answer it from
trusted content, or treat it as an external action that needs a capability.
"Plan a vegetarian dinner for six" was neither, so it was declined as an
unregistered action. V0 adds a third outcome, a **conversational artifact**.
This is shared work that is produced, revised, explained, and summarized
inside the conversation. It has typed state, delivery-bound outcomes, and
inquiries that ask after the contribution rather than instead of it.

Pipeline (runner `x.y.3`):

```text
Perception -> Interpretation -> Goal -> Workspace Resolution -> Requirement Resolution
  -> Collaborative Content -> Cognitive Turn (Appraisal, Dynamic State, Action) -> [worker] Realization
```

## 2. Stage by stage

| Stage | Change | Versions |
| --- | --- | --- |
| Interpretation | `RequestedOutcome` (information_answer / conversational_artifact / external_effect / social_exchange) on every predicate. New predicates `collaborative_content_request` (operation, artifact_type, target_ref, constraints, requested_form, outcome_certainty, embedding) and `inquiry_answer` (offered only when open inquiries exist). `action_request` is restricted to effects outside the conversation. Answers must cite an offered inquiry (`FOREIGN_INQUIRY`). Embedded (quoted/hypothetical/reported) content frames are rejected | predicates `v0.4`, model protocol / compiler `v0.3`, rules `v0.4`, regression scorer `v0.2`; corpus `v0.2` (62 turns, 6 contrast pairs, the 8-turn dinner trace) |
| Goal | `interaction.fulfill_conversational_deliverable` (target `request_ref`; params operation, artifact_type) and `interaction.incorporate_inquiry_answer` (target `answer_ref`). Strict frame-family eligibility, so the rule grounder's `unknown` frame never forms them. `ArtifactDeliveredEvent` satisfies only the exact requested operation at the exact target from the predecessor's focal episode | ontology `v0.3`, formation `v0.5`, priority / satisfaction `v0.4`, adoption / evidence `v0.3`, engine `v0.5` |
| Workspace | `contracts/workspace.py` + `conversation_workspace_v0/`: 11 content classes, typed `MealPlanPayload` / `StructuredArtifactPayload`, 15 typed patch ops, content-addressed immutable revisions, compare-and-set heads, open-inquiry and pending-requirement records, and three authority modes. `resolve_workspace` binds each goal to an exact target (create / unique artifact / dialogue / ambiguous / not found) and binds answers to exact inquiries | `workspace-resolution-v0.1`, `workspace-reconciliation-v0.1`, patch engine `v0.1` |
| Requirement Resolution | `RequirementCriticality`: only *blocking* unsatisfied requirements gate readiness. New kinds ARTIFACT_TARGET, REQUESTED_CHANGE, REQUESTED_OUTCOME, INQUIRY_LINK, and DOMAIN_CONTEXT (quality-improving). A deliverable never requires capability, authorization, consent, or safety prerequisites | contract / engine / manifest / projection `v0.2` |
| Collaborative Content | `collaborative_content_v0/`: a separate bounded provider protocol (structured output, never prose), a deterministic compiler, an inquiry timing and personality policy, and a projection into Action objects and Realization content. The compiler rejects ignored user constraints, foreign refs, restricted claims (nutrients, prices, medical, legal, tool outcomes), fabricated changes, unsupported numbers, unhedged safety guidance, and blocking inquiries. There is no fallback: no provider means a typed `provider_not_configured` failure, which Action can only decline | contract / compiler / protocol `v0.1`, `inquiry-timing-policy-v0.1`, `collaboration-action-projection-v0.1` |
| Action | `collaborate.deliver_artifact` and `seek_information.inquire` (77 definitions). Inquiries are constructed only after the primary deliverable is planned. A policy-selected inquiry is its own open uncertainty with relevance by criticality (quality_improving 0.50, optional_curiosity 0.30). A proposition that is basis content of an artifact revision is never paraphrased back to the user. A failed proposal becomes `CONTENT_UNAVAILABLE`, addressed only by a truthful decline | contract `v0.4`, registry `conversational-actions-v0.2-shadow`, construction `v0.2`, features / planner `v0.4` |
| Realization | `ArtifactPresentation` (meaning, integer arguments with units, and flags `requires_hedge`, `licenses_causal`, `licenses_suggestion`, `reports_change`) and `InquiryPrompt`. The validator licenses causal and advice language only inside the moves that carry it, requires hedging where the content does, checks argument values, and checks the question form | contract / content / compiler / model protocol / validator `v0.3`, action manifest `v0.2` |
| Pipeline / trace | Stages `workspace_resolution` and `collaborative_content`, with timings. `PipelineCollaboration` on the trace is validated by recomputing the projection from the recorded outcomes. Historical runners `v0.2.2` / `v0.3.2` stay parseable | runner `v0.2.3` / `v0.3.3` |
| Live shadow | The compiler carries the predecessor's workspace and turn record **observationally**, only when that stage recorded something, so a conversation without collaborative work compiles byte-identically. A predecessor workspace in any other mode is refused. Replay re-runs the compiler on the recorded candidates and provider failures (`recorded_model_for`) and never calls the provider. `ShadowFailure` registers the two new stages. The trace summary adds value-free `workspace` and `collaboration` stages | summary `v0.5`; accepted registry `v0.2-shadow` |
| Provider | `collaboration_provider.py` (OpenAI Chat Completions, strict JSON schema, one tool-free call, `store=False`, no SDK retries). It is validated against the shared capability manifest before any client exists. Settings `COGNITIVE_SHADOW_COLLABORATION_ENABLED` (default **false**), `…_MODEL` (explicit, no fallback), and `…_TIMEOUT_SECONDS` | adapter `openai-collaborative-content-v0.1` |
| Simulated authority | `conversation_workspace_v0/simulation.py`: the explicit offline harness. It turns the moves Action selected into a marked `simulated` delivery, the `ArtifactDeliveredEvent`s that delivery supports, and the next turn's simulated-authority workspace inputs. A failed delivery cites what it attempted, so it reconciles as `delivery_failed` | `workspace-simulated-authority-harness-v0.1` |

## 3. Invariants (each pinned by a test)

1. Observational shadow never commits a shadow proposal. Every carried
   proposal reconciles as `shadow_only_not_delivered`, no head advances, and an
   outcome citing a proposal in observational mode is an integrity failure.
2. Only a validated `DeliveredResponseOutcome` satisfies a deliverable, opens
   an inquiry, or advances a head, and only in simulated authority. Live
   authority fails closed (`authority_mode_not_approved`).
3. Heads advance by compare-and-set. A delivered revision whose base moved
   reconciles as `artifact_conflict` and never overwrites the head.
4. No goal is satisfied by planning or generation alone. In every turn of the
   dinner trace the primary goal is `active` in the trace and `satisfied` only
   after the outcome-time revision.
5. Non-blocking curiosity never precedes or prevents the primary
   contribution: the timing is `after_primary`, and blocking questions belong
   only to Requirement Resolution.
6. Generated suggestions cannot masquerade as verified facts. Restricted claim
   kinds are rejected, safety guidance must be hedged and tied to a
   safety-relevant constraint, and numbers must already be in the artifact.
7. There is no silent fallback. A missing or misconfigured provider is a
   typed failure, Action declines it truthfully, and replay never calls a
   provider.
8. Genuine external requests are unchanged. `action_request` still requires
   capability, authorization, and safety, and all pre-existing Action design
   cases produce identical plans (see §5).

## 4. Tests

| File | What it covers | Count |
| --- | --- | --- |
| `tests/test_collaborative_interpretation.py` | outcome contrasts, conditional `inquiry_answer`, foreign inquiry, embedded frames, corpus | 35 |
| `tests/test_conversation_workspace.py` | create/revise, no-op, conflicts, stale base, constraint preservation, digests, reconciliation modes, CAS conflict | 21 |
| `tests/test_collaborative_content_compiler.py` | protocol schema, every compiler rejection code, inquiry policy | 19 |
| `tests/test_collaborative_simulated_authority.py` | Gate 6 dinner trace end-to-end, failed delivery, unhedged-safety decline, harness isolation, evidence exactness, criticality, 13 frozen validator wordings | 27 |
| `tests/test_collaborative_live_shadow.py` | observational carry-forward, provider-free replay, disabled-provider decline, settings wiring, OpenAI adapter against a fake client, value-free summary | 11 |

Full backend suite on 2026-10-02 without a database: 1,737 passed, 116 skipped
(the final count after these docs is recorded in the README). Real-PostgreSQL
tests were **not** run in this session (no `TEST_DATABASE_URL`).

## 5. Regeneration proof

Goal, Action, Requirement Resolution, and Realization version strings changed,
so the Action design results and the Realization golden changed. Restoring
exactly the previous version strings reproduced all 30 Action design cases and
the Realization golden (`21532f63-…`) byte for byte. After restoring the new
versions, every case's `matched_plan` and projection is identical except for
the `versions` block. `data/conversational_action_design_results_v0.json` was
regenerated with `build_expected_results()`. The new golden is recorded in
`tests/test_realization_v0_compiler.py`.

## 6. Known limitations (honest)

- **Gate 7 not implemented.** There are no migrations, repositories, or
  workers for artifacts, heads, inquiries, or outcomes. In the only approved
  mode (observational shadow) nothing may be committed, simulated authority
  is offline and in-process, and live authority is not approved. A durable
  store would therefore have no legitimate writer yet. Add it together with
  an approved authority mode, not before.
- **Gate 8 not performed.** No browser, WebSocket, chat-worker, or database
  run of collaborative turns has been done. The OpenAI collaboration adapter
  has been exercised only against a fake client, never the real API.
- **Live shadow cannot continue its own proposals.** By design an
  observational shadow never delivers, so "why does the stew fit?" after a
  shadow-only create finds no artifact. Continuity is evaluable only under
  simulated authority until a shadow can bind to what the *live* response
  actually delivered, which is future work requiring a delivered-response
  projection.
- **Validator strictness.** On a describe, "Imagine a fragrant stew…" is
  rejected as an unsupported claim while "It's a fragrant stew…" passes. On
  the answer turn, a leading "Thanks." is an unplanned move, because Action
  plans no receipt there. Both are recorded, not fixed.
- **Social goals after a delivery.** The harness emits artifact events only,
  so a greeting turn's social goal closes by turn rollover rather than by a
  delivered-response event.
- **Uncalibrated constants.** The inquiry relevance (0.50 / 0.30), the inquiry
  policy score, and the response costs are reviewed defaults, not
  calibrated.
- **One typed payload.** Only `meal_plan` has a typed payload; other types use
  the structured section/item payload. Evaluation covers one vertical slice
  and claims nothing about general human-like behavior.
- **Database tests.** Acceptance criterion 13 (PostgreSQL) is unverified in
  this session.

## 7. Forbidden shortcuts (none taken)

- No phrase-specific rules: routing comes from typed frames, goals,
  resolutions, and the registry.
- No fallback model and no deterministic substitute for the provider.
- No weakening of external-action authorization, safety gates, or the
  validator. One validator change only licenses language the content already
  licenses.
- No historical trace rewritten. Old runners, registries, and projections
  remain parseable and are never reinterpreted.
- No artifacts in Core Memory, and no shadow output in prompts, memory,
  `WorkingMemory`, or `LearningMemory`.
