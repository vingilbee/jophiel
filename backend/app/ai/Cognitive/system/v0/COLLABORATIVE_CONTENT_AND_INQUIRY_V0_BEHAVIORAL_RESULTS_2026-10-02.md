# Collaborative Content and Inquiry V0 — Behavioral Results (2026-10-02)

Scope: offline, hermetic evaluation. These results are **not** a live-provider
evaluation and **not** a real-application (Gate 8) evaluation. They use frozen,
reviewed candidates (semantic frames from the regression corpus and one
reviewed structured collaboration candidate per operation). Live-provider
variation has not been measured. `completed` and `accepted_for_shadow_review`
are not used as proxies for human-like behavior.

Reproduce with `uv run python -m pytest tests/test_collaborative_simulated_authority.py`.
The runner is `tests/simulated_authority_support.py`.

## 1. The uninterrupted vertical slice (simulated authority)

Each turn runs capture → live-shadow compiler → lineage → `CognitivePipelineV0`
→ Realization compilation. Between turns, the explicit harness injects a
**marked simulated delivery** of exactly the moves Action selected, followed
by an outcome-time goal revision. Columns: the primary goal; the selected
plan; the collaboration outcome (with the proposed revision number); the head
revision at turn start; the reconciliation of the predecessor's proposal; the
open-inquiry state; and the primary goal after the simulated delivery.

| # | User | Primary goal | Plan | Proposal | Head | Reconciled | Inquiry | Goal after delivery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Hi Columbina. I'd like to talk through dinner ideas. | respond_to_social_contact | acknowledge, greet | — | — | — | — | active (closed by rollover) |
| 2 | What vegetarian dinner would work for six people in under 30 minutes? | fulfill_conversational_deliverable | deliver_artifact | create → rev 1 | — | — | — | satisfied |
| 3 | Why does the chickpea stew fit those constraints? | fulfill_conversational_deliverable | deliver_artifact | explain (rev 1) | 1 | delivered_and_satisfied | — | satisfied |
| 4 | Revise our idea: make it dairy-free and add an inexpensive side. | fulfill_conversational_deliverable | deliver_artifact, **inquire** | revise → rev 2 | 1 | delivered_and_satisfied | — | satisfied |
| 5 | Describe the revised meal so it sounds inviting. | fulfill_conversational_deliverable | deliver_artifact | describe (rev 2) | 2 | delivered_waiting_for_answer | open | satisfied |
| 6 | Summarize what we've decided so far. | fulfill_conversational_deliverable | deliver_artifact | summarize (rev 2) | 2 | delivered_and_satisfied | open | satisfied |
| 7 | The dairy-free requirement is because one guest has an allergy. | incorporate_inquiry_answer | deliver_artifact | revise → rev 3 | 2 | delivered_and_satisfied | **answered** | satisfied |
| 8 | Given that, is there anything we should change? | fulfill_conversational_deliverable | deliver_artifact | evaluate (rev 3) | 3 | delivered_and_satisfied | answered | satisfied |

Outcomes against Section 19 of the instructions:

- One artifact lineage: rev 1 → rev 2 → rev 3, each parented on the previous
  head, with no other revisions. The final snapshot holds two `superseded`
  revisions and one `delivered` revision.
- Turn 4 produces a revision, not an acknowledgement or a decline. Vegetarian,
  6 servings, and 30 minutes are preserved, dairy is removed from the main
  dish, and rice is added.
- Turn 4 asks the preference-or-allergy question *after* the revision
  (`after_primary`, quality-improving, health sensitivity). The delivered
  outcome opens it, it stays open through turns 5–6, and turn 7 binds the
  answer (`option: allergy`) to that exact inquiry.
- Turn 7 changes only the dairy-free constraint's reason, from `unspecified`
  to `allergy`. Dishes, servings, the time limit, and the vegetarian
  constraint are byte-equal.
- Explain, describe, summarize, and evaluate each cite the head at their turn
  (rev 1, 2, 2, 3).
- Turn 8's safety guidance is hedged (`uncertain_suggestion`, general
  practice) and tied to the allergy constraint.
- No turn requires a capability, authorization, consent, safety, or execution
  prerequisite. No turn has a planning condition, an acknowledgement, or a
  decline.
- No older goal captures focus. Every primary goal from turn 2 onward was
  created in its own turn.
- Every primary goal is `active` in its own trace and becomes `satisfied` only
  from the delivered outcome.

## 2. Graded axes

| Axis | Result | Notes |
| --- | --- | --- |
| Operational completion | 8/8 | Every turn completes and compiles a Realization request |
| Semantic routing | 8/8 | Turn 1 social; turns 2–6 and 8 deliverables; turn 7 inquiry answer |
| Artifact continuity | Pass | One lineage, compare-and-set, correct presentation targets |
| Correction application | Pass | The turn 4 revision and turn 7 reason update preserve unrelated constraints |
| Inquiry relevance and timing | Pass (1 inquiry) | Asked once, after the contribution, about a safety-relevant reason; never repeated |
| Goal/outcome closure | Pass for deliverables | The social goal of turn 1 closes by rollover, not by a delivered-response event (§4) |
| Realization fidelity | 9 reviewed wordings accepted, 4 unfaithful rejected | §3 |
| Task usefulness | Reviewed by construction only | Content is the reviewed double's; a live provider is unmeasured |
| Naturalness | Partial | Two over-strict validator outcomes (§3) |

## 3. Realization validator over the real compiled requests

Accepted (frozen in tests):

- "A chickpea and spinach stew serves 6 / serves six and takes about 25 minutes."
- Explain with "so it fits…" causal language (licensed by the explanation role).
- "I've made the chickpea and spinach stew dairy-free, and steamed rice is an
  inexpensive side. Is dairy-free a preference or an allergy for you?"
- "It's a fragrant chickpea and spinach stew, served with fluffy steamed rice."
- "So far: a vegetarian, dairy-free … for 6, under 30 minutes."
- "You said one guest has an allergy, so I've recorded…" and "As you
  mentioned, …"
- Turn 8, with "It may help to check packaged ingredients…"

Rejected (correctly):

- A changed number ("serves 8"): `numeric_value_changed`.
- An invented question ("Do you also want dessert?"):
  `content_alignment_missing`.
- An unattributed user claim with an unplanned "Thanks.": `attribution_dropped`
  and `unplanned_move_detected`.
- Dropped hedging ("You must check…"): `required_uncertainty_dropped`.

Over-strict (recorded, not fixed):

- "Imagine a fragrant chickpea and spinach stew…" on a describe is rejected as
  an unsupported claim.
- "Thanks." on the answer turn is an unplanned move, because Action plans no
  receipt for an answer.

## 4. Failures and partial results found during evaluation (all fixed unless noted)

1. **Paraphrase of Columbina's own proposal.** Collaboration content
   propositions matched the paraphrase binder, so turns 2–4 planned a
   paraphrase that Realization could not word (`required_content_missing`).
   Fixed in construction `v0.2`: artifact basis content is never paraphrased.
2. **Selected inquiry never planned.** The deliverable's need was consumed
   by delivery, so the inquiry scored no need and the plan stopped. Fixed in
   features `v0.4`: a selected inquiry is its own open uncertainty, with
   relevance by criticality.
3. **Failed delivery reconciled as `abandoned`.** The harness cited nothing on
   a failed delivery. Fixed: a failed outcome cites the attempted content
   (never a revision or an inquiry) and reconciles as `delivery_failed`.
4. **Unrecordable failures.** `ShadowFailure.stage` and the trace summary did
   not know `pipeline.workspace_resolution` or
   `pipeline.collaborative_content`, so a failure there could not be
   persisted or summarized. Fixed.
5. **Reviewer error, not a system error.** My first turn 8 candidate gave
   unhedged safety guidance. The compiler rejected it
   (`sensitive_content_unauthorized`) and Action declined truthfully. This is
   kept as a negative test.
6. **Open:** the social goal is not closed by delivery evidence; the
   validator is over-strict (§3); and live-shadow continuity is impossible by
   design (see the implementation report §6).

## 5. Failed-delivery and no-provider variants

- **Turn 2 delivery failed.** The proposal reconciles as `delivery_failed`, no
  head exists, and the goal stays `active`. Turns 3–4 find no artifact
  (`not_found`), are blocked on ARTIFACT_TARGET, and plan only
  `seek_information.clarify`. Pending requirements open and expire after one
  user turn. Nothing is invented.
- **No provider configured** (live-shadow path). The deliverable fails with
  `provider_not_configured`, and the plan begins with
  `commit_or_coordinate.decline_request` and contains no `deliver_artifact`.
- **Observational shadow** (live-shadow path). The next turn reconciles the
  create as `shadow_only_not_delivered`, no revision is committed, and
  "explain" finds no shadow-owned artifact.

## 6. Structured contrast corpus

`data/semantic_parser_regression_corpus_v0.json` (`v0.2`) contains 62 turns,
including 6 contrast pairs that separate conversational artifacts from
external effects. The pairs cover polite, indirect, interrogative,
hypothetical, quoted, negated, corrected, compound, ambiguous, and
after-greeting forms, plus the 8-turn dinner trace. All 62 pass offline with
frozen candidates (`tests/test_collaborative_interpretation.py`,
`tests/test_semantic_parser_hardening.py`). The live semantic parser has not
been re-run against this corpus, so its variation is unmeasured.
