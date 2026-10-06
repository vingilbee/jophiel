# Realization V0 design cases

`realization_design_cases.json` is the reviewed-draft bootstrap and regression
fixture for Realization V0 (`REALIZATION_V0_IMPLEMENTATION.md` section 19.7).
Status: **`draft_for_human_review`**. It is not learning data, not a quality
benchmark, and not evidence of natural or human-like conversation. A passing
case means only that the deterministic compiler and validator behave as
specified on that input.

Production code never reads this file. `tests/realization_v0_support.py` and
`tests/test_realization_v0_design_cases.py` are the only consumers.

## What a case contains

| Field | Meaning |
| --- | --- |
| `case_id` | Stable identifier |
| `coverage` | Required section 19.7 items (1-19) the case exercises |
| `domain` | `personal`, `practical`, `social`, or `factual` (no case depends on nutrition) |
| `plan_source` | Either `{"action_case": id}`, an accepted Action V0 design case run through the real Action engine, or `{"live_message": text}`, a message run through the real `CognitivePipelineV0` with the real Personality Runtime Mapper projection |
| `content_items` | The authored, authorized content inventory. Each item is JSON exactly as a trusted producer would project it. `represents_refs` names the upstream Action target, evidence, or parameter ref that the item represents. Generic dialogue-structure references from `project_dialogue_structure` are always added |
| `candidate` | One scripted candidate: `text`, plus optional `move_excerpts` (1-based move order to exact substring) and `content_excerpts`. Without excerpts, every move and content item is aligned to the whole text. `null` means the case only asserts the compile outcome |
| `expected` | `compile` (`request` or a typed skip status), the validation `status`, optionally the exact selected `action_keys`, and constraint codes that must appear in the request |
| `mutations` | Adversarial rewrites of the candidate. Each must be **rejected**, and its `expected_codes` must be a subset of the typed violation codes |
| `personality_variants` | Optional. The request is recompiled under each variant. Content, bindings, moves, move order, and constraints must stay byte-identical; only delivery controls may differ |

## Authoring rules

1. Author content as a trusted producer would. Set authorization,
   attribution, epistemic status, certainty, polarity, and provenance
   explicitly. Never put a meaning in candidate text that no content item
   authorizes.
2. Reference upstream Action refs exactly as the plan source emits them
   (inspect them with `tests/action_v0_support.result_for`).
3. Every move that binds an item must name it by at least one authorized
   anchor. Every assertable proposition must be fully expressed (all values)
   in at least one binding move.
4. Write mutations that isolate one failure where possible. Assert the codes
   that failure must produce. Do not assert codes that only incidental
   wording produces.
5. A new required behavior needs a new case, not a relaxed validator. False
   rejections are acceptable in V0; false acceptances are not.

## Coverage map

| Item | Cases |
| --- | --- |
| 1 | `greeting-without-factual-content` |
| 2 | `acknowledgement-without-endorsement` |
| 3 | `clarify-missing-referent` |
| 4 | `factual-question-without-content-discloses-gap`, `tool-result-absent-skips`, `famous-fact-without-content-is-rejected` |
| 5 | `factual-question-with-semantic-memory` |
| 6 | `user-stated-relationship-fact-keeps-attribution` |
| 7 | `no-episodic-record-is-not-never` |
| 8 | `tentative-claim-preserves-uncertainty` |
| 9 | `explicit-correction`, `self-correction-after-realization-failure` |
| 10 | `explanation-multimove-order-and-stop` |
| 11 | `advice-with-grounded-option-and-basis` |
| 12 | `commitment-blocked-by-unavailable-safety` |
| 13 | `tool-result-available`, `tool-result-missing-cannot-claim-execution`, `tool-result-absent-skips` |
| 14 | `social-validation-without-hidden-affect` |
| 15 | `apology-with-supported-involvement-only` |
| 16 | `playful-frame-stays-nonfactual` |
| 17 | `explanation-multimove-order-and-stop` |
| 18 | `identical-meaning-under-personality-extremes` |
| 19 | Every case is tagged with a non-nutrition domain; `museum-choice-disambiguation` and `accept-request-without-completion` add factual and practical coverage |

## Known limits the cases make visible

- The detectors are English and lexical. Some legitimate wording is rejected.
  For example, "thanks" is gratitude, which is not a selected move, so it is
  rejected even inside an acknowledgement.
- Semantic entailment is not proven. A candidate that passes is **accepted for
  shadow review**, never truth-guaranteed or live-approved.
