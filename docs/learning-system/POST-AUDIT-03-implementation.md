# Post-audit 3 implementation: broaden experience understanding

Branch: `feature/broaden-experience-understanding` (from `feature/system-page-logs`). Built on request on 2026-11-05.
Design: [broaden experience understanding](POST-AUDIT-03-broaden-experience-understanding.md). Acceptance:
[behavioral acceptance](POST-AUDIT-03-behavioral-acceptance.md).

**Status in one line:** the infrastructure for every capability in the design is built and covered by scripted
contract tests. The behavioral acceptance (real models, reserved fixtures, the proposed pass rule) has **not** been
run, and its pass rule is still a proposal awaiting the user's agreement. This package is not complete until that
acceptance passes.

## 1. Trace: what was reused, what was extended

| Area | Before | Now |
| --- | --- | --- |
| Conversation intake | Student 1's `correcting_her` / `complaining` opened `correction` / `complaint` assessments; other messages were hash-sampled at `LEARNING_ASSESSMENT_SAMPLE_RATE`; triage waited for Student 1's capture. | Every completed exchange extends the conversation's **review stretch** (`trigger_kind="review"`, `services/learning_review.py`). Student 1's labels and memory's corrections are shown as **signals**, never reasons. Sampling and the triage wait are removed. A sweep adds the fallback: her last reply in a conversation quiet for `LEARNING_REVIEW_PAUSE_SECONDS`. |
| Execution facts | Interrupted reply, failed delivery, failed reply/effect, failed skill, critical notice. | Unchanged: they keep their own `execution` assessments (facts, not verdicts on her choice). Overlap with a review is reconciled by dependency-aware support counting (below). |
| Game stretches | Opened on session transitions only, gated by `LEARNING_GAME_MIN_OUTCOMES` (5) / `PAUSE_OUTCOMES` (15). | Transitions with any new experience open or extend a review stretch (no count gate). Running sessions get a review when new experience has waited `LEARNING_REVIEW_GAME_SECONDS`, so a pattern can be read before the session ends. An unread or parked stretch takes more play; a concluded one is never extended. Pages of 100 experiences are transport, not experience boundaries. |
| Mind | No way to point learning at anything. | Hard thoughts have a `learn` field; a non-empty one is a **nomination** (`learning_review.nominate`): a pointer attached to the activity's review and shown as "her mind pointed at this … not evidence". |
| Reviewer contract | `assessment-v2`: targets, causes, stated, dimensions, choice/outcome, missing, clarification, reopens, next (conclude / look_further / need_missing), episode. | `assessment-v3` adds `review` (examine / wait / leave), `findings` (statement, free-text form, status, cites, applies_when, alternatives, relates_to existing lessons), `requests`, `inquiry`, `follow_up` (replaces `clarification`), and `next.step="wait"`. |
| Iterative evidence | Looks: earlier, later, inputs, game. | Plus `requests` answered read-only by their owners: `memory` (`recall.search`, `explicit_search`, no access counting), `conversations` (`transcript_search`, forgotten passages withheld), `experiences` (game experience outside this session), `lessons` (`memory_lessons.for_learning`). Each answer states status and coverage; `no_match` is rendered as a gap, never as evidence of absence. Lessons render in a non-citable section; citing one is dropped. |
| Continuation | A new reading always started from a fresh plan; any reopen called the model again. | A reading resumes from the last revision's plan (looks and requests). A wake whose evidence digest equals the current revision's calls **no model** and writes no revision (`UNCHANGED`). Every stop except concluded/left parks a resumable inquiry (`learning_assessments.inquiry`: question, provisional reading, alternatives, unknowns, resume condition, last examined revision/digest, requests). Unrelated activity never touches it. Two expansions in a row that bring no new citable evidence park it as `stuck`. |
| Cross-context resume | Open assessments shown only within the same conversation or session. | The user's parked inquiries elsewhere are also shown (`p` refs, "elsewhere"). A `reopens` attaches the citing evidence to that inquiry; when it resumes, that evidence is rendered in "Found elsewhere and attached to this", with its prior inquiry. |
| Lesson products | `preference` / `strategy`; an inference became `accepted` only with `LESSONS_MIN_OCCASIONS` (2) occasions; withdrawal needed the same count of counterevidence. | Adds `understanding` (an expectation, relationship, explanation, capability, open question; with a descriptive `form` and kept `alternatives`). Established vs tentative is the **builder's judgment** (`certainty`), not a count. Code keeps integrity: an experience already counted for a lesson (same occasion, or sharing a message/turn/run/experience with its evidence) can't raise certainty or widen scope, checked both in `lessons.check` and again in `memory_lessons.apply`. A supported conclusion against an inference can withdraw it at once; a preference still needs the user's words. `LESSONS_MIN_OCCASIONS` is removed. |
| Independent support | Overlapping *game* roots merged across assessments; conversation occasions counted by assessment key. | Messages and turns merge too, so two reviews resting on the same message are one occasion. |
| Lesson comparison | The builder saw the 40 most recently changed lessons. | Past 40: the 10 most recent plus the most relevant to the revision (meaning, else shared words). Each lesson says whether this experience is already counted for it. |
| Supply | Student 2: accepted only. Game consumers: accepted + situation hypotheses with live support. Conversation mind: none. Student 1: none. | Student 2 and the conversation mind: accepted + tentative (`include_tentative=True`), labelled "a guess", alternatives shown (`lessons-input-v3`). Game consumers unchanged but receive `understanding` too. An inference is supplied only with live support, whatever its status. Student 1's trained input has no lessons slot: every decision records a `student_1` use with `received=false` and the delivery-gap reason. |
| Follow-up | `clarification` was stored and reached nobody. | `follow_up` (question / observation / practice) becomes an open working item of hers via memory (`learning_review.propose`): a `question`, or a `hypothesis` (in a live game the existing trial sweep can investigate it; cognition selects). One per open assessment. Learning never speaks or acts. |
| Forgetting / reset | | A review stretch that reaches a forgotten message goes, even when it only showed it. The `consolidation` reset also closes learning's open follow-up items. |

## 2. Where each accepted decision lives

| Decision | Code |
| --- | --- |
| Code supplies evidence; the LLM judges meaning | `learning_review` only schedules; `assessment.INSTRUCTIONS` asks for the review decision; `assessment.check` enforces refs, quotes, own-record causes; no code verdict anywhere in intake. |
| Different entry points, one path | Conversation, games, nominations and execution facts all open `LearningAssessment` rows in the same lifecycle (claim, lease, revisions, links, uses, lessons). |
| Ordinary activity | Every exchange and every stretch of play gets an opportunity; consequential failures keep immediate triggers but don't replace the ordinary ones. |
| Iterative inquiry | `Plan.requests`, `_requests` / `_ask`, `Evidence.requests` / `lessons`, the round loop in `_investigate`. |
| Broader products | `findings` → `learning_lessons.bundle_for` (conclusion kind `finding`) → `understanding` lessons. |
| Persistent questions, no-progress parking | `LearningAssessment.inquiry`, `_inquiry`, `UNCHANGED`, `_held` (barren expansions), cross-context `ev.open`. |
| Follow-up under cognition | `learning_review.propose` → working items. |
| Later use and verification | `memory_lessons.for_decision(include_tentative=True)`; `record_uses` per consumer including the Student 1 gap. |
| Resources | No cap, quota or round limit was added. Scheduling delays (settle, pause, game) are timing only. Usage per revision stays recorded (`usage`, `rounds[].latency_ms`). |

## 3. Removed

`LEARNING_ASSESSMENT_SAMPLE_RATE`, `LEARNING_ASSESSMENT_TRIAGE_WAIT_SECONDS`, `LESSONS_MIN_OCCASIONS`,
`LEARNING_GAME_MIN_OUTCOMES`, `LEARNING_GAME_PAUSE_OUTCOMES`, `learning_assessment.sampled`, the `clarification`
result field (older revisions keep it; the System page shows either).

Added settings: `LEARNING_REVIEW_PAUSE_SECONDS` (300), `LEARNING_REVIEW_GAME_SECONDS` (300),
`LEARNING_REVIEW_STRETCH_MESSAGES` (40).

## 4. Cutover

1. Stop workers.
2. From `backend`: `uv run alembic upgrade head` (revision `a3d7f1c9e2b6`: the new trigger/outcome/stop/kind
   values, `learning_assessments.inquiry`, `memory_lessons.form` / `alternatives` and their revision copies).
   Nothing is rewritten; existing assessments and lessons keep what they are.
3. Remove the retired environment overrides above if set. Restart workers.

Existing `accepted` strategies stay accepted; existing `hypothesis` strategies stay tentative (now supplied to
conversation as guesses while they have live support). If you prefer a clean test attempt, the existing
`consolidation` reset applies (`uv run python scripts/reset_memory.py --user YOU --scope consolidation --dry-run`
first); it now also closes learning's open follow-up items.

## 5. Verification

Scripted contract tests (disposable PostgreSQL schemas, scripted reviewer/builder/Student 2, no model calls).
They establish lifecycle and integrity, not that a real model learns well.

| Behavioral family | Contract covered by | Not covered |
| --- | --- | --- |
| B1/B2 ordinary observation, success across steps | ordinary/successful stretches reach review (`test_successful_and_ordinary_stretches…`, `test_ongoing_play…`) | whether a real reviewer derives the relationship |
| B3 semantic conversation | `test_every_completed_exchange_is_a_review_opportunity_and_labels_are_only_signals` (labels as signals, left stretch writes nothing) | real-model distinction of jokes, quotes, serious context |
| B4 iterative retrieval, missing variant | `test_the_reviewer_asks_another_owner…`, `test_a_request_that_finds_nothing_is_a_gap…` | |
| B5 existing lesson reconciliation | `test_existing_lessons_are_shown_to_compare_with_never_as_evidence`, `test_a_review_that_repeats_the_same_evidence_adds_no_support`, `test_overlapping_game_sources…` | real-model merge/narrow quality |
| B6 tentative understanding | `test_a_finding_becomes_a_tentative_understanding…` (Student 2 and the conversation mind receive it labelled a guess; Student 1 gap recorded) | matched no-learning baseline with a real model |
| B7 transfer with a boundary | none beyond retrieval | entirely a real-model evaluation |
| B8 contradiction and revision | `test_contrary_evidence_withdraws_an_understanding_at_once…`, `test_a_revised_assessment_stops_counting_until_read_again` | |
| B9 no useful progress | `test_a_parked_inquiry_resumes_with_its_state_and_unchanged_or_unrelated_wakes_call_no_model` | |
| B10 follow-up authority | `test_a_follow_up_is_a_proposal_kept_by_memory_never_an_act_of_learning`, the question follow-up in `test_ambiguous_targets…` | accepted handoff through a real Student 2 decision |
| B11 no-change control | left stretches, unchanged wakes, repeated evidence | real-model restraint |
| B12 ongoing and cross-session | `test_ongoing_play…`, `test_evidence_in_another_conversation_resumes_a_parked_inquiry…`, `test_a_left_stretch_takes_the_next_exchange…` | |
| Reliability | retry-safe intake, stale lesson readings, forgetting a shown message (`test_forgetting_a_message_removes_a_stretch…`), reset closes follow-ups | concurrent workers under load |

Consumer coverage: Student 2 and the conversation mind (new), hands / planner / Minecraft mind (existing, now with
`understanding`). Student 1 cannot receive lessons: a recorded delivery gap, not coverage.

Other checks: migration upgrade → downgrade → upgrade on an empty disposable database; frontend build passes.
Test results are in section 7.

## 6. Known limits

- The reviewer now runs on every completed exchange (batched by the settle delay). That is more background model
  calls than sampling at 10%. No cap was added, by the user's decision; usage and latency are recorded per revision
  for diagnosis.
- `scripts/learning_assessment_eval.py` and `scripts/lesson_adaptation_eval.py` still score the earlier contract's
  cases; they were not re-run. They are not the post-audit 3 acceptance.
- `experiences` requests match words in recorded payloads; they don't search by meaning.
- Student 1 can't use lessons until a model with a lessons input is trained (historical step 5/6).
- The behavioral acceptance's pass rule needs the user's agreement, then reserved fixtures, then real-model runs.

## 7. Test results

2026-11-05, against a disposable PostgreSQL database:

- Full backend suite: 1362 passed, 5 failed. The same 5 fail on the unchanged code (checked by stashing this work):
  `test_game_pack::test_she_knows_what_she_cant_do_yet_and_how_her_moves_behave`, three in `test_inner_state_live`,
  and `test_student_two::test_its_work_is_carried_out_on_the_record_its_ref_names` (no local Student 2 model folder
  on this machine). They are not caused by this package.
- Learning suites after the final edits (assessment, lessons, consolidation, broaden, trials, effort): 119 passed.
- Migration `a3d7f1c9e2b6`: upgrade, downgrade, upgrade on an empty database.
- Frontend: build passes; lint shows only the 3 earlier Fast Refresh warnings.
- Not run: any real-model evaluation, the behavioral acceptance, a live chat or game session. The dev database
  was not migrated.
