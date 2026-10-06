# Cognitive two-call protocol v1 — implementation report

Date: 2026-10-05.
Scope: Cognitive V0 shadow only. Shadow isolation is unchanged: no live response,
tool, memory-write or training authority.
Implements [`CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md`](CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md)
together with [`SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md`](SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md).
Not independently audited. Behavior has not been reviewed.

## 1. What changed

New protocol `cognitive-two-call-v1`, selected with
`COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1`. The code default and the
local `.env` still select `context-conditioned-two-call-v0.1`. Both earlier protocols
remain installed and replay unchanged.

```text
deterministic Perception
  -> Call 1  UnderstandingInputV1 -> UnderstandingProposalV1   (typed events, readings, named appraisal)
  -> compiler -> canonical Interpretation + Appraisal (existing contracts)
  -> deterministic Dynamic State, Goals, Workspace, Requirements
  -> configured collaborative-content provider (separate subsystem; may fail closed)
  -> code-constructed, hard-gated, fully bound eligible candidates
  -> Call 2  ActionPolicyInputV1 -> ActionPolicyProposalV1     (candidate refs, primary, stop, factors)
  -> plan compilation under authoritative gates (planner action-planner-model-policy-v1)
  -> separate Realization (configured Realization provider), outside the cognitive target
```

| Surface | File |
|---|---|
| Contracts, registries, versions | `two_call/v1_contracts.py` |
| Input builders, compilers, routers, adapters | `two_call/v1.py` |
| Strict narrowed JSON Schemas | `two_call/v1_provider.py` |
| Prompts | `two_call/call_one_v1.txt`, `two_call/call_two_v1.txt` |
| Typed unknown/conflict grounder; reading rules and conflict policy | `two_call/compact_runtime.py` (`UnderstandingGrounder`, `UnderstandingReadingRules`) |
| Orchestration wiring | `two_call/orchestrator.py` (`_prepare_v1`, `select_policy_choice`, `select_policy_primary`, `validate_policy_plan`) |
| Candidate training projection | `two_call/v1_training.py` |
| Planner and move contract | `contracts/actions.py`, `action_selection_v0/engine.py`, `action_selection_v0/transition.py` |
| Trace and replay validation | `two_call/contracts.py`, `cognitive_pipeline_v0/models.py`, `live_shadow/realization.py`, `live_shadow/service.py`, `app/services/cognitive_shadow.py` |
| Latency | `latency.py` (span schema v0.3), `trace_summary.py` (summary v0.4) |
| Tools | `scripts/cognitive_two_call_canary.py`, `scripts/cognitive_two_call_corpus_eval.py` |
| Tests | `tests/test_cognitive_two_call_v1.py` (63 tests) |

## 2. Contracts as implemented

**Call 1 output.** `events[]` each carry:

- `predicate_key`, `polarity`;
- `arguments` (typed value union);
- `evidence_span_refs`;
- nested `readings[]`, each with `hypothesis_key`, `uncertainty`, `context_refs`, and a named `appraisal`.

Alongside the events: `existing_goal_impacts[]`, `unknowns[]` (registered key plus a
typed subject pointer), and `conflicts[]` (registered key plus two supplied refs).

There are:

- no handles;
- no `candidate_meaning`;
- no positional appraisal vectors;
- no free-text unknowns or conflicts.

**Call 2 output.** `selected_candidate_refs[]`, `primary_index`,
`stop_after_selected` and `selection_factors[]`. Each factor has registered keys and
supplied source refs. There are:

- no action keys, goals, targets or parameters;
- no content, segments or wording;
- no rationale.

Every string in both strict schemas is either an enum of supplied refs or registered
keys, or matches the span grammar. A test enforces this.

## 3. Decisions taken for open or underspecified points

Each decision is versioned and recorded on the trace.

1. **Span addressing (`focus-token-span-v1`).** The model sees
   `focus.indexed_tokens` and cites `span:i` or `span:i-j` (inclusive) over
   code-owned word tokens of the focus message.
   - Values compile to the exact source text.
   - Evidence maps each token to the smallest containing Perception region, and a
     span outside the supplied regions is rejected.
   - Clause-level `authorized_span_refs` are supplied as ready-made ranges.
   - Referents in earlier messages use `entity:*`, which compiles to the dialogue
     item's code-owned surface anchor, or `null`.
2. **Arguments are an object keyed by registered argument name**, rather than the
   handoff's `ArgumentBinding[]`. The meaning is the same, but the strict schema can
   then enforce exact names and presence, which an array cannot.
3. **Nullability (`argument-kind-registry-v1`).** `null` on a required or referent
   argument is an unresolved value and becomes an explicit unknown. On any other
   optional argument it means "not stated" and the argument is omitted. Without
   this, every absent `embedding` or `addressee` became a spurious unknown and a
   clarification candidate.
4. **Derived, not predicted.** Hypothesis support comes from uncertainty
   (`reading-support-from-uncertainty-v1`: low→strong, moderate→moderate,
   high→weak). Epistemic status comes from predicate, embedding and polarity
   (`epistemic-status-derivation-v1`).
5. **Appraisal.** Named dimensions, with one uncertainty per reading. A value given
   under high uncertainty is compiled as unassessed. Readings of one event that
   disagree on a dimension combine to unknown, as before.
6. **Goal impacts** are one per (goal, event). Readings of the same event merge:
   agreeing values are kept and disagreement becomes unknown. Only exact
   duplicates are rejected.
7. **Reading conflicts (`reading-conflict-policy-v1`).** Different events can
   legitimately carry conflicting readings, e.g. "don't make a schedule" and "talk
   it through". The canonical leading scenario cannot hold both. A model-only
   reading that conflicts with a structural reading, or with a better-supported
   model reading, is kept as a *retained alternative*, never dropped, and the rule
   ID records this.
8. **Registries.** New registries for unknown keys (6), conflict keys (4) and
   selection factors (19). They are in `v1_contracts.py` and must be reviewed
   before data collection (handoff §20.6).
9. **Policy mode** is a deterministic router input (`policy-mode-router-v1`), not
   a model output:
   - **deliberative:** active conflicts, blocking planning conditions, or an unsafe request.
   - **contextual:** several events, unresolved required arguments or registered unknowns, retained alternatives, or content outcomes.
   - **direct:** otherwise.

   `max_moves` is 2, 3 and 4 respectively.
10. **Whole-plan selection** (§20.4). Candidates are constructed at step 0 and
    each selected ref binds by lexical identity to the gated candidate at its
    step. `stop_after_selected=false` is rejected because no iterative contract
    is offered.
11. **Primary move.** The model chooses it, and the move must carry the plan's
    greatest direct focal-goal coverage
    (`action-primary.model-selected-focal-coverage.v1`).
12. **Utility is not a gate** under planner `action-planner-model-policy-v1`. The
    deterministic score is still computed and recorded on every move
    (`selection_policy: model_policy`). Positive utility remains a gate for the
    historical argmax planner. Choosing among safe plans is a preference, not a
    structural failure (§15).
13. **Content (§20.5).** Artifact content is produced by the configured
    collaborative-content provider *before* Call 2. A proposed artifact becomes a
    bound `collaborate.deliver_artifact` candidate, listed in
    `goal_readiness.delivery_candidate_refs`. Omitting it fails as
    `behavior_proposal_ineligible.stop_omitted_delivery`. A disabled or failing
    provider becomes a typed `content_unavailable` planning condition.
14. **Input extensions** beyond the handoff types:
    - Call 1: `agent_values`, `indexed_tokens`.
    - Call 2: `grounded_objects`, `addressed_condition_refs`, `delivery_candidate_refs`, `selection_factor_registry`, `iterative_planning`, `policy_mode_router`.

    The safety view drops every `*_ref` and `*_id` field.

## 4. Shared fixes found during implementation

- **Requirement projection.** Two request frames sharing one clause region could
  both claim one argument unknown. Projection then raised "two resolutions
  describe one object differently". This affected every protocol. On that exact
  conflict, the unknown's own frame (identified by its exact compiler ID) now
  wins. Every outcome that previously succeeded is unchanged.
- **Collaboration provider.** It never forwarded the capability manifest's
  `reasoning_effort`, so `gpt-6-luna` rejected its temperature with HTTP 400. It
  now forwards it. The provenance field is omitted when unset, so earlier records
  serialize unchanged.

## 5. Phase status

| Phase | Status |
|---|---|
| A. Baseline | v0.1 stays replayable. A same-harness baseline for the 13-turn corpus was measured (§6). |
| B. Versioned contracts | Done. Four contracts with narrowed strict schemas. Old behavior is untouched. |
| C. Adapters | Done. Call 1 compiles into canonical Interpretation/Appraisal through the existing compilers, with a parity test against v0.1. Call 2 compiles into Action plans under the authoritative gates. |
| D. Realization separation | Done. v1 never uses captured wording; live runs and replay use the configured Realization provider. |
| E. Training projection | Done (`project_training_example`). Nothing is exported, persisted or promoted. All review statuses are `unreviewed`, and failures carry a typed category. |
| F. Real shadow evaluation | **Partial.** Run with the in-process harness (real provider, chained predecessor state). **Not** run through the WebSocket → persistence → fresh-worker path. No independent behavioral review. |

## 6. Evaluation (in-process harness, `gpt-6-luna`, same 13 turns)

The corpus is from `GENERAL_CONVERSATION_TWO_CALL_EVALUATION_2026-10-05.md`, grouped
into the same five conversations. Each turn chains the predecessor shadow state. The
assistant history is the shadow's own accepted wording, or "Okay.". The content
provider is off, as in the local `.env`.

| | v0.1 compact | v1 |
|---|---:|---:|
| Cognitive completion | 7/13 | **13/13** |
| Reached Call 2 | 9 | 13 |
| Accepted Realization | 6 | 8 |
| Call 1 provider median / max (ms) | 4,298 / 6,361 | **3,378 / 5,029** |
| Call 2 provider median / max (ms) | 1,985 / 8,803 | **1,412 / 3,597** |
| Cognitive core median / max (ms) | 6,651 / 13,174 | **5,125 / 8,129** |
| Call 1 input / output tokens (median) | 5,689 / 410 | 9,267 / 348 |
| Call 2 input / output tokens (median) | 4,233 / 138 | **2,168 / 87** |
| Separate Realization median (ms) | — (inside Call 2) | 1,791 |
| Plan compilation median (ms) | — | 39 |

The five rejected Realizations are all in the separate Realization stage
(`content_alignment_missing`, mostly on clarification moves). None is a cognitive
failure.

**Action quality is not improved by this change alone.** v0.1 produced richer
answers, such as book-club names and a plan for tomorrow, because its Call 2 wrote
content. v1 removes that by design, and with the content provider off the plans are
grounded but thin: acknowledge, decline content, or clarify. Turning the content
provider on currently does not help either; see §7.1.

## 7. Known issues and recommended next steps

1. **Collaborative-content provider output is brittle.** Model output is rejected
   by its compiler as `content_invalid` (anchors) or
   `patch_rejected.add_item.section_id.missing`. These are the failure classes
   fixed for the compact v0.2 Call 2 (narrowed patch-operation schema and lossless
   normalization), but this separate provider never received them. Until it does,
   enabling `COGNITIVE_SHADOW_COLLABORATION_ENABLED` mostly yields
   `content_unavailable` → `decline_request`.
2. **The Realization validator rejects many clarification wordings**
   (`content_alignment_missing`). This is a Realization concern, outside the
   cognitive target.
3. **The registry has no Action for answering a social question** ("How has your
   day been?"), so only a greeting is possible.
4. **Closed world.** General-knowledge questions are knowledge gaps by design, so
   the answer is "I don't have a grounded answer".
5. **Call 1 input is about 9.3k tokens**, mostly the ontology and the per-predicate
   schema. Moving `value_kinds`/`enum_values` out of the payload, or shortening
   predicate descriptions, would reduce it.
6. **Persistence duration is not in the trace.** It happens after sealing.
   Summary v0.4 reports it as `not_recorded_in_trace`.
7. **Phase F on the real path, and independent review of labels, Actions,
   personality fidelity and outcomes**, are still required before acceptance
   criteria 9–10 can be claimed.

## 8. Acceptance criteria (handoff §19)

| # | Criterion | Status |
|---|---|---|
| 1 | No unrestricted generated prose in either output | Met (schema test) |
| 2 | No model-authored durable IDs or target-binding graphs | Met |
| 3 | Named Appraisal replaces positional output | Met |
| 4 | Deterministic State, Goals, Requirements and gates stay authoritative | Met (identical middle under different Call 2 choices) |
| 5 | Call 2 returns only selection/order/primary/stop plus registered factors | Met |
| 6 | Realization separate and independently measured | Met |
| 7 | Old traces remain replayable | Met (full suite, including v0.1 and legacy replay) |
| 8 | Summaries separate every duration | Partial: provider, compile, content, plan, middle and Realization are separated; persistence is not recorded in the trace |
| 9 | Corpus improves completion and Action quality without more unsafe or ungrounded behavior | Completion improved (7→13/13). Action quality is not shown and has not been reviewed |
| 10 | Training candidates stay unreviewed until promotion | Met |

## 9. How to run

```bash
# One turn
COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1 \
  uv run python scripts/cognitive_two_call_canary.py --text "Hi" --output /tmp/v1.json

# The 13-turn corpus (writes proposals of failed turns for local diagnosis)
COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1 \
  uv run python scripts/cognitive_two_call_corpus_eval.py --output /tmp/v1-corpus.json

# Tests
PYTHONPATH=. uv run pytest tests/test_cognitive_two_call_v1.py
```

To use v1 in the shadow worker, set `COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1`
in `backend/.env` and restart the worker. Realization then needs
`COGNITIVE_SHADOW_REALIZATION_ENABLED=true` with a configured model, which the local
`.env` already has.
