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

## 8. Follow-up 2026-11-06: whole stretches are read, not parked

Found in a live Minecraft run (session 9e42d19d, 14:59-15:37): both reviews her mind nominated for whole stretches
stopped at once with "the evidence is more than can be read at once" (378k and 71k characters against the 60k
limit), so only single-failure assessments concluded, and every lesson restated one action's outcome. The
decision in §3 of the design ("code may page and bound evidence transport; pages are not semantic boundaries") was
not met in practice. Changed, with the 60k limit kept:

- Compact game records (`services/learning_assessment.py`): a run is its call, how it ended, seconds, reason and
  what was observed changing (position, health, food, inventory diff); her hands' available actions and plan step
  only when they change; her mind's, planner's and Student 2's answers as their own fields, labelled as reasoning.
  The exact records stay behind the "inputs" look. Nothing is inferred.
- The records window no longer counts the hands decisions already shown as targets, which had pushed her mind's
  thoughts out of a long stretch.
- A condensed view (`ai/learning/assessment.py`, `render(condensed=True)`) when the full one is too long: one line
  per decision of her hands, citable as `hN` (a target ref already stands for its records), repeated identical
  records once with a count, conversation turns during play cut short. Her mind's thoughts stay whole. Only if
  that is still too long does it park as before. The digest is of the full view either way.
- Prompts `assessment-v5` and `lessons-v4`: look at the approach across the stretch (what she kept doing, how long,
  what finally worked) before single actions; describe the kind of situation, not the recorded instance; keep
  doubt in status, applies_when and alternatives rather than in the statement; a restated error report is no
  change. Examples in the prompts are deliberately not from this session.

All six assessments parked that day fit afterwards (largest condensed: 59k). Tests:
`tests/test_learning_compact_evidence.py`. Not yet run: a real-model reading of those stretches.

## 9. Follow-up 2026-11-06 (second run): look backs during long steps, lessons judged by what followed

Found in the second live run (session c2b6396f, 17:18-17:39, no iron in 21 minutes): the reviews now concluded and
9 lessons reached her hands (127 deliveries, 87 "applied"), but nothing changed how she went about it.

- **Her planner ran once.** The step "find iron" never ended, so the step-ended look back never came; and every hard
  thought (10, each judging progress "no", 8 naming perception as the cause) only reached her hands as a tip.
- **A lesson became a reason to keep failing.** "When a route fails, try another nearby passage" was applied over and
  over while the search went nowhere, and no review ever saw which lessons had been applied.

Changed:

- **A look back while a step keeps going** (`services/mind_minecraft.py` `step_running_long`): once
  `mind_step_look_back_seconds` (default 300; 0 turns it off) into a step, and again each time that passes, her
  mind gets a `review` problem like the step-ended one, asking whether the effort is getting anywhere and whether the
  approach, not only the next move, should change. Code picks only *when*; no time or count decides she is stuck
  (the 2026-11-04 rule stands).
- **Her judgment reaches her planner** (`services/mind_hard.py` `route`): a look back (trigger `review`) that judges
  progress "no" also sends `rethink` through the Minecraft body (`mind_minecraft.rethink` ->
  `game_planning.request`), with her conclusion and the cause she favours. The planner may keep the plan; a rethink
  still waits until the plan has stood `RETHINK_GAP_S`. Surprises never replan (2026-11-04: 44 revisions in 24
  minutes when every thought did). The hard-thinking prompt says that "no" on a look back reaches her planner.
- **Lessons judged by what followed their use** (`services/learning_assessment.py` `_lessons_used`): the lessons her
  parts were given for the decisions in a stretch are shown among her existing lessons with "Given in this stretch:
  her hands said applied on N decision(s) (h3, h7, ...)", and each decision says `applied L1` / `L2 not relevant`.
  Prompts `assessment-v6` (judge a lesson by what followed its use; a part saying it applied one is its own
  account) and `lessons-v5` (a lesson applied again and again while the effort didn't move is countered and
  narrowed or revised into what works instead, never left as a reason to keep doing what failed).
  `memory_lessons.for_learning_ids` shows them the same way as `for_learning`.

Tests: `tests/test_mind.py` (the look back during a step), `tests/test_mind_hard.py` (no progress on a look back
asks the planner; progress or a surprise doesn't), `tests/test_learning_compact_evidence.py` (lessons applied, by
decision). Not yet run: a live session with these, or a real-model review.

## 10. Live check of §9, 2026-11-06 (session c2b6396f resumed 18:59-19:17 UTC)

Workers restarted 18:57 UTC, so §8 and §9 were live. She started over with an empty inventory (another place:
around -60 -300, earlier -250 200), goal still a diamond pickaxe.

**What she got in 18 minutes:** wooden pickaxe at 19:01:36 (2.5 min), stone pickaxe at 19:07:57 (lost 1.5 min on a
crafting table she no longer had and "oak planks" from spruce), then 10 minutes looking for iron, none found.

**(a) works as wired.** 10 planner runs instead of 1: 4 from her own look backs ("your own look back found no
progress: ..."), the rest from cognition's rethinks and one from the user ("dig down"). 22 hard thoughts, 9 of them
look backs (`review`). Plan changes were spaced (8 revisions in 18 minutes, no churn of the 2026-11-04 kind).

**But the method her mind found never got carried out.** From 19:05 her mind concluded 7+ times "mine beneath my
feet one block at a time, checking each opening" (the right method). Her hands kept walking (`go_to`, mostly
`dig=no`) to the end. Two causes, both in the record:

1. **A missing move.** The planner said so twice: "I can't dig straight down with the moves I have right now"
   (19:05:14), "I don't currently have a move that can carry out that exact intention" (19:05:26). The only digging
   is `go_to(dig=yes)` toward a target and `mine(block, radius)`, which picks the nearest matching block, not the one
   below. The 4 `mine(stone, radius 4)` at 19:06 did take her down a little; nothing does it on purpose. Next step:
   a pack skill to dig down / tunnel at a chosen depth (e.g. `dig(direction=down, to_y=..)`, a staircase, a branch
   tunnel), in `game_packs/minecraft/src/skills`, listed in the manifest so the planner and hands know it.
2. **The planner turned "dig down" into "walk to lower ground".** After the 19:09:58 rethink ("dig down from here")
   the next three plans (19:12, 19:13, 19:14) were "move east toward the lower ground", "walk north...", "walk into
   the cave-like opening", while her mind's look backs at 19:13:33 and 19:13:53 still said "stop walking routes, mine
   beneath my feet". Part of this follows from cause 1 (it plans with the moves it has). Worth checking once the
   move exists: whether the planner weighs her mind's repeated conclusion over the nearest walkable route.

**(b) not exercised yet.** No review since the resume challenged a lesson (0 revisions with `challenges`), and
the "try another nearby passage" lessons from the first run weren't what her hands were given here (different
situation). Needs a session where an applied lesson keeps failing.

**Lessons are getting better.** New ones are partly methods, not logs: "When repeated searches for a resource
report that it is not in sight, I can stop repeating that search and gather a suitable resource that is actually
visible" (it worked: spruce instead of oak). There are still plain logs ("directional labels such as 'right' are
rejected"; "no oak log in sight ... fails without moving"), and "can" phrasing is still in them.

**Still open, in order:**
1. A dig-down / tunnel move in the pack (cause 1 above). Without it the right method can't happen.
2. Sight (§8 notes; the user plans a revamp): `sight.js` checks only the `LOOK_AT=128` nearest candidates, all buried
   inside a hill, so visible stone or ore reads "not in sight"; caves are detected only from open sky
   (`surroundings.js`). "no stone in sight within 64 blocks" at 19:01:48 and 19:03:15 while standing on a hill is
   probably this.
3. Check (b) live, and whether the lesson writer still writes logs under `lessons-v5`.
4. Old crafting-table habit: she lost the placed table and tried crafting with none near (19:06:25-19:07:29).
