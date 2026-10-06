# Compact two-call reliability fix (decision-context-v0.2)

Date: 2026-10-05. Scope: observational Cognitive V0 shadow only. No delivery, tool or
memory authority changed. Follows `COMPACT_TWO_CALL_IMPLEMENTATION_REPORT.md` and
`PHASED_MODEL_BACKED_COGNITION_AND_LATENCY_IMPLEMENTATION.md`.

## Problem

After the earlier patch_rejected fix, live QA still failed on one turn with
`...action_selection.two_call.call_two.behavior_proposal_ineligible.proposal_rejected`.
A local reproduction (`scripts/cognitive_two_call_canary.py`, same dinner-brainstorm
turn) failed 3 times out of 3, each at a different validator. Different model outputs
were reaching different downstream checks, and every behavior failure had the same
opaque code.

## Root causes (from captured proposals, replayed offline)

| # | Where | Cause | Kind |
|---|---|---|---|
| 1 | Call 2 schema | `collaborative_content_v0.model_protocol.response_json_schema` shared one `string_list` dict between `anchors`, `subject_ids` and `answer_options`. Narrowing anchors (`minItems: 1, maxItems: 6`) leaked into the other two, so **every inquiry had to have ≥1 answer option**. A valid `open_set`/`yes_no` inquiry (needs 0) could not be produced. | Code bug |
| 2 | Call 2 schema | Inquiry `answer_options` must be slugs (`^[a-z0-9][a-z0-9_\-]*$`) and a `dietary` constraint value must be a `DietaryValue`. The schema allowed any string and never said so. | Contract gap |
| 3 | Call 2 prompt | Nothing told the model that a compiled artifact **must** be delivered. It sometimes chose only `acknowledge`, and the behavior gate then rejected the STOP (`model STOP omitted required content delivery`). This is the QA failure. | Prompt gap |
| 4 | Call 2 prompt | "Keep crucial statement anchors in speech" was vague. The model put element IDs (`quick_dinner`) in `anchors`, which must be substrings of the statement. | Prompt gap |
| 5 | Realization validator | The proper-name heuristic flagged sentence-initial imperatives ("Try …", "Cook the rice …", "Here's …") as unsupported names because its starter list is closed. | Validator false positive |
| 6 | Call 2 prompt | The model gave general-knowledge facts on knowledge-gap turns, echoed user numbers in unbound acknowledgements, added apologies or affect claims, and put a trailing acknowledgement after delivery (the planner scores that negative). | Prompt gap |
| 7 | Call 1 → Call 2 | The same user requirement can reach Call 2 under two refs with identical text (a request-frame constraint and a standalone frame). Citing one copy left the other "ignored" (`user_constraint_ignored`). | Representation |

## Changes

All new behavior is versioned. Old captures keep their packet version, prompt and
validation policy, so frozen replay is unchanged.

- **`decision-context-v0.2`** (`two_call/compact_contracts.py`, `compact.py`): each
  content request carries `delivery_move`, the exact template, goal and target to copy.
  The packet version selects `call_two_compact_v0_2.txt`
  (`provider.compact_prompt_version`). Provenance records the actual context and
  prompt versions.
- **`call_two_compact_v0_2.txt`**: precise rules for anchors, inquiry shape, mandatory
  delivery and move order, knowledge gaps (worded to match `GAP_STATEMENT`), numbers
  (including number words; only `meal_plan` artifacts can hold numbers), constraint
  vocabularies, and neutral acknowledgements.
- **Lossless normalization, v0.2 packets only** (`compact.normalize_response`):
  - An element-ID anchor becomes that element's label, but only when the statement
    contains it. A foreign anchor is dropped only if a real anchor remains. Anchors are
    never invented.
  - Answer options become slugs, and `question_kind` is reconciled with the options
    actually supplied.
  - A dietary constraint phrase becomes its closed value ("without dairy" →
    `dairy_free`), but only when that value exists.
  - Citing one user requirement also cites its identical-text siblings.
- **Schema** (`model_protocol.py`, `compact_provider.py`): list fields are built by
  factories, so per-field constraints no longer leak (fixes #1 for legacy and compact).
  v0.2 adds the slug pattern to `answer_options`.
- **`segmented_response-v0.2` validation policy** (`realization_v0/validator.py`,
  contracts): adds a closed, reviewable `SUGGESTION_STARTERS` list of sentence-initial
  imperatives. Capitalized words in the middle of a sentence are still checked as names.
  New compact runs use v0.2. Replays keep their captured policy.
- **Diagnostics** (`two_call/orchestrator.py`): `behavior_proposal_ineligible` now
  carries a closed reason, one of:
  - `stop_omitted_delivery`
  - `stop_leaves_planning_condition`
  - `ambiguous_candidate`
  - `skips_blocking_condition`
  - `action_not_eligible_at_step`
  - `goal_or_target_mismatch`
  - `move_without_positive_utility`
  - `target_unresolved`
  - …plus `.step_N`.

  The code never contains model text.
- **Tests:** `tests/test_compact_two_call_v0_2.py` (9 cases). Full backend suite:
  2008 passed, 120 skipped.

## Live results (canary, `gpt-6-luna`, in-process shadow, no chat delivery)

| Batch | Turns | Fully accepted (`completed` + `accepted_shadow`) |
|---|---|---|
| Before (dinner brainstorm) | 3 + QA's 1 | 0 |
| After causes 1–5 | 10 | 7 |
| After cause 6 prompt rules | 10 | 10 |
| Repeat | 10 | 9 (trailing acknowledgement, since addressed) |
| Extended (13 prompts incl. new meal-plan phrasings) | 13 | 11, then 12 after cause 7 |

Each turn made 2 provider calls, with roughly 5–10 s of shadow time per turn.

## Known remaining issue

"Suggest a gluten-free breakfast for 2 people" succeeds about 1 time in 4. Call 1
classifies it as artifact type `other`. A non-`meal_plan` artifact can hold no numbers
(`compiler._numbers`), but the model keeps repeating the user's own "2 people".
Prompting does not fix this reliably. A real fix is a design decision:

1. Have Call 1 or requirement resolution type meal suggestions as `meal_plan`, so
   `set_servings` exists.
2. Or let content and speech echo numbers that appear in the cited **user-supplied**
   requirement text. This would be a compiler and validator version bump.

Not independently audited.
