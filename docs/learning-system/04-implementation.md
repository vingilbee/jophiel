# Step 4 implementation: predictions and bounded game trials

Status: built 2026-11-03 on `feature/learning-04-predictions-trials` (migration `a9c3e7f1d5b8`, not applied to the
dev database). Decisions: [04-predictions-and-trials.md](04-predictions-and-trials.md). This page records what that
plan left as implementation work: schemas, model use, candidate validation, how cognition reads a proposal, worker
wiring, recovery, and measurement predicates. Not yet played live against the real pack; the real-model check of the
proposer and assessor (`scripts/trial_eval.py --run`) has not been run.

## Who does what

| Part | Owner | Where |
| --- | --- | --- |
| What to investigate | learning (code) | `learning_trials.consider` (her hands' `struggling` / `stuck` on a step of the goal the session plays for; a trial's changed approach), `sweep` (her open hypotheses in a live game) |
| A proposal | learning (one structured call) | `app/ai/learning/trials.py` (`PROPOSAL_INSTRUCTIONS`, `PROPOSAL_SCHEMA`, `check`) |
| Persisting it, before anyone sees it | learning | `learning_trials._write_proposal` -> `learning_trial_revisions` |
| Her hypothesis | memory (working item, kind `hypothesis`) | `_hypothesis` (the triggering one, a linked trial's, or a new one via `memory_manager.add_item`) |
| Whether, when and which way | cognition (Student 2) | `learning_trials.present_once` -> `student_two.gather_trial` / `decide` / `apply` |
| A deferral's reminder | memory (working item, kind `intention`) | `_remind`, `_settle_reminder` |
| Arranging what's missing | her planner | `game_planning.request` (reason `trial: ...`), `learning_trials.planning_lines` in the planner's input |
| Running it | her body (game practice, `origin="trial"`) | `_start` -> `game_practice.run_once` (`PracticeRun(trial=TrialSpec)`) -> `on_execution_finished` |
| Bounds | code | trial clock (`_advance`, `_watch`), attempts and time (`PracticeRun`), blocks (the pack's sandbox) |
| What happened | code | `trials.measure`, `trials.evaluate`, `trials.allowed` |
| What it supports | learning (one structured call, checked) | `trials.ASSESSMENT_*`, `check_assessment` |
| What persists | memory (hypothesis), game lessons, game skills | `learning_trials._feed` |
| Worker | `learning-trials` thread (desktop mode) | `workers/learning.trials_once` -> `learning_trials.run_once` |

Only a Student 2 decision starts a trial. Hard thoughts can still send her hands tips, and the planner still writes
plans, but neither can select, start or resume an experiment: the selection carries the capture id of the decision
that made it (`learning_trial_events.decision_attempt_id` on `selected`). A proposal arriving interrupts nothing.

## The record

| Table | What | Rules |
| --- | --- | --- |
| `learning_trials` | one experiment: problem, status, current revision, cognition's `selection`, `deferral` + every `deferrals`, `limits`, counted `costs`, current `attempt_id`, `ending`, `stop_reason`, lease and next check | one pending proposal per `(user, problem_key)`; one live trial per session; taken up => selection set (checks) |
| `learning_trial_revisions` | every proposal revision, whole, as checked, with what was dropped and the evidence it rests on | immutable (trigger); no insert once the trial was selected (trigger) |
| `learning_trial_events` | proposed, revised, presented, undecided, deferred, reconsidered, rejected, expired, selected, preparation, readiness, execution_started, execution_ended (with its tries), paused, resumed, stop_requested, stopped, limit, ended_before_test, observed, assessed, reopened | append-only (trigger; only a foreign key's SET NULL may touch a row) |
| `learning_trial_assessments` | each reading: hypothesis status, whether the test started, measurements with sources, prediction checks, missing evidence, the checked interpretation, what was dropped | one `current`; later readings keep earlier ones |
| `learning_trial_triggers` | investigations waiting: kind, problem, evidence (up to 10, each with the latest skill outcome's experience id) | one pending per problem: repeats add evidence |

`game_skill_attempts.origin` gains `trial`. Everything cascades with the user; a trial survives its session, attempt
or working items (SET NULL). Reset scope `trials`. Downgrade refuses while trial attempts exist.

### Lifecycle

```
proposed -> presented -> selected -> preparing <-> (executing <-> paused) -> observing -> assessing -> assessed
                     \-> deferred -> (condition) -> proposed
                     \-> rejected | expired
```

Every selected trial ends `assessed`, including one that never started (prepared too long, the session ended,
stopped before the test, the last-moment readiness check failed for good).

## Proposals

**Triggers (code).** Her hands' 4th/10th failed try on a step (`desktop_sessions.driver_noticed`, kind
`struggling`) and a step reported stuck (`desktop.stuck`), only while the session plays for a goal and has an active
step; her open hypotheses (owner `columbina`) in the session's game context, once per item revision, unless a trial
already tests them (`sweep`, every 30 s); a trial whose body wanted another way (`approach_changed`). Problem keys:
`step:<goal>:<step words>`, `hypothesis:<item>`, `approach:<trial>`.

**The call** (`trial-proposal-v1`, `LEARNING_TRIALS_MODEL` gpt-6-luna, reasoning `low`, strict schema, no tools, no
temperature change: alternatives are asked for explicitly). It reads the scenario and limits, the problem, the
evidence (`x*`), the pending proposal for the same problem (`p1`, with its deferral reasons), earlier assessed trials
in this game and what they found (`t*`, up to 6), her game lessons (`g*`, up to 8), her position and state text from
the pack, and her skills.

**What code keeps (`trials.check`).** Never repaired; each drop is kept with why.

- `decision: none` is a valid answer (nothing worth testing, or not this scenario).
- It must cite something it was shown among `x*`, `t*`, `p1`.
- The destination must be a small obstacle near her: 1 to 5 blocks above her feet, within `TRIAL_NEAR_M` (24 m).
  Unknown position: no proposal.
- 2 to 4 alternatives, each with an action and approach (over 200 / 500 characters: dropped, not cut), a
  prediction of `reached`, valid predictions only (`reached` true/false; `climbed_m`, `blocks_placed`,
  `attempts_used`, `test_seconds`, `health_lost` as `at_most` / `at_least` / `between`), an uncertainty
  (low/medium/high), prerequisites as items with counts (1-64, at most 4). An alternative expecting more blocks than
  the limit is kept and marked.
- The recommendation must be a kept alternative.
- Limits can only come down from the configured defaults (`TRIAL_MAX_ATTEMPTS` 3, `TRIAL_MAX_SECONDS` 300,
  `TRIAL_MAX_BLOCKS` 8; at least 1 attempt and 30 s). `reached` is always required evidence. The observation window
  is at most `TRIAL_WINDOW_SECONDS` (20).
- The proposal keeps her start position: what the predictions are about.

**Revising, not duplicating.** A pending proposal (`proposed` / `presented` / `deferred`) for the same problem gets a
new revision with the new evidence; a deferred one goes back to `proposed`, its reminder closed, its deferral reasons
kept. `materially_different: true` makes a new trial linked to it (`linked_trial_id`), testing the same hypothesis
item.

## Cognition's choice

`present_once` hands one `proposed` trial to Student 2 with trigger `component_proposal`
(`student_two.gather_trial`): the game's conversation, goals, work and activity as on any decision, and each
alternative as one proposal `[P*]` (producer `curriculum`) in the trained snapshot shape, unchanged. Its purpose
says the hypothesis, the limits, the predicted result and predictions, uncertainty, costs and prerequisites,
risks, and which one learning recommends and why, plus earlier deferral reasons. Each `[P*]` conflicts with the goal
the game plays for, so `events.check` makes a pursue say what happens to that goal.

| Student 2's work | Effect (`student_two.apply` -> `learning_trials`) |
| --- | --- |
| `pursue [Pk]` | `on_pursue`: selected, alternative k of the revision it was shown; limits fixed; reminder closed. Refused if the revision moved, it was decided meanwhile, or another trial is live in the session. |
| `decline_proposal [Pk]` | `on_decline`: rejected, with its intention as the reason. |
| `defer_proposal [Pk]` | `on_defer`: deferred (below). |
| anything else, or no decision | deferred with the reason "cognition made no decision about it" |
| invalid or unavailable | retried with backoff; after 4, deferred with why |
| `pause` / `resume` / `cancel` on the trial's `[W*]` (any later decision) | `pause`, `resume`, `stop` |

A live trial shows to every later Student 2 decision as work `experiment: <action>` (execution: pause, resume,
cancel). Its `other_goal_handling` is recorded; the goal is never suspended for a trial (suspending a game's goal
pauses its session, which the trial needs). A speaking decision's words go through `autonomous_speech.submit`, never
straight out. The decision is captured (`learning_capture`, attempt id `uuid5(trial, "student_2:trial:<rev>:<n>")`).

Choosing an alternative learning didn't recommend is allowed: every alternative already has its own prediction, so
the choice never needs one written after the fact. A changed approach in the body is different (below).

### Deferral

The deferral keeps cognition's reason, a reconsideration condition, and an expiry (Student 2's `defer_expiry`, else
`TRIAL_DEFER_HOURS` 2). Working memory holds a reminder through its own owner: an intention of hers ("Reconsider the
experiment ... when ..."), due at the expiry. The condition is cognition's when it gave one that code can evaluate
(`time_reached`, converted from her local clock; `work_finished`; `user_returns`, which also stands for
`user_raises_it`), else learning's default: any of the goal it competes with closing (or the game playing for
another), the recommended alternative's prerequisites in hand, or (with neither) ten minutes. `reconsider_once`
checks deferred trials every 15 s; a condition that came true sends it back to cognition once (`proposed`); expiry
or a session ending closes it (`expired`). It is never presented on every tick or executed by itself.

## Preparation and execution

**Readiness** (`readiness`, read off the pack): the session running, the pack reachable, the chosen alternative's
prerequisites carried, her within `TRIAL_NEAR_M` of the destination, and no other lesson holding her body. Not
ready: `preparing`, and when something is missing or she's far, a planning job (`trial: <why>`; once per reason);
the planner's input gets "An experiment to get ready for" (what it needs, what's missing, time left, and not to run
the test itself). Her hands carry out the planner's steps as usual.

**Starting.** Ready: a `game_skill_attempts` row with origin `trial` and its task ("get up to x y z by <action>").
Her hands hold while it runs (existing). The game-practice worker builds a `TrialSpec` (`learning_trials.spec_for`):
the approach, destination, radius, what's left of the attempts, seconds and blocks, and the readiness check.

**The run** (`app/ai/desktop/practice.py`, `PracticeRun(trial=...)`):

- Right before the first try, readiness is read again from the game; not ready means the test never started, and
  the trial goes back to `preparing` (failed preparation, never evidence against the technique).
- The writer gets the trial's rules: keep to the chosen approach, fix details only; `keeps_approach: false` with
  `other_approach` ends the run without running anything (`approach_changed`).
- Each program runs with `limits: {place: <blocks left>}`: the pack's sandbox refuses the block past the budget and
  ends the program with status `limit` (`game_packs/minecraft/src/sandbox.js`; it also reports `placed` and
  `actions.done`). Program seconds are capped by the time left.
- Success is counted by code: she stands within the radius of the destination with her feet at or above its height
  (`trials.at_destination`). The critic isn't asked.
- Getting hurt (the existing safety control) ends it (`stopped`); a lost game ends it (`interrupted`).
- Endings: `completed` (got there, or used its attempts), `limit` (time or blocks), `stopped`, `interrupted`,
  `not_started`, `approach_changed`, `failed` (the writer couldn't write).

**Costs.** Preparation time (active stretches from selection to start), blocks placed while preparing (place skills
that ended done), test time, attempts, test blocks: kept apart, counted against the same limits. Paused time doesn't
count. Exhausting the time while preparing ends it as never started (`limit`).

**After the run** (`on_execution_finished`, in game practice's transaction): an `execution_ended` event with every
try, the start state and what was placed; then `observing` for the proposal's window, then the state after the
window (`observed`), then `assessing`. A paused trial keeps the segment and stays paused; `resume` continues with
what's left of the budget, readiness first. Reaching a limit ends the trial: another needs a fresh proposal.

**Changed approach.** The run ends (`approach_changed`); the trial is assessed on what it saw; an
`approach_changed` investigation proposes a new trial, linked, with a fresh prediction for the new approach, which
cognition must select. Nothing already predicted is rewritten.

**Recovery.** A restart cuts off a running attempt (game practice marks it failed after 15 minutes without an
update). The trial's watcher finds the attempt ended without an `execution_ended` and collects it from the tries it
saved, as `interrupted` (`recovered: true`). Nothing is run again; what wasn't saved stays unknown. A trial past its
time with an attempt still running is asked to stop (45 s grace).

## Measurement and assessment

**Measured by code** (`trials.measure`), each with its source: `reached` (any try's end position, or the state after
the window, at the destination), `climbed_m` (highest counted position minus the start), `blocks_placed` (the pack's
count per program), `attempts_used`, `test_seconds`, `health_lost` (start minus the lowest counted). A value nothing
counted is `None`, never a guess.

**Prediction checks** (`trials.evaluate`): `met`, `not_met` or `unknown`. A run that didn't finish (stopped,
interrupted) can confirm what only grows past a bound (`reached` true, `at_least`) and contradict what is already
past one (`at_most`), but never calls something not met for what it didn't get to see. A missing measurement is
`unknown`.

**What may be concluded** (`trials.allowed`): `unresolved` always; `supported` only when every prediction of the tried
alternative is met and every required measurement exists; `contradicted` only when one is measured as not met; a
test that never started is `unresolved` only.

**The reading** (`trial-assessment-v1`, same model; skipped for a test that never started, which code records). It
reads the hypothesis as written before, cognition's choice and reason, the costs, the ending, measurements (`m.*`),
checks (`c*`), tries (`a*`) and events (`e*`), and the allowed values. `check_assessment`: a status outside what's
allowed becomes `unresolved`; an explanation called supported or contradicted without a measured citation becomes
plausible / unresolved; competing explanations are kept; `failure_of` is `preparation` when the test never started
and not `technique` for an interruption; a lesson from an unresolved trial is dropped.

**What persists** (`_feed`), each owner under its own rules, nothing from an unresolved trial:

| Owner | What |
| --- | --- |
| game experience | the trial's outcome, one `skill_outcome` (`skill: trial:climb_obstacle`, status the hypothesis's): what reflection, observation and the lessons cite |
| memory | her hypothesis `supported` / `disproved`, with that experience as its source |
| game lessons | `note_trial_lesson`: new lessons start `trial` (tentative); one session counts once, so a second trial in the same session never confirms it; trust from distinct sessions; retired stays retired |
| game skills | a supported trial whose program reached the destination becomes one of her moves (`save_learned`); trial attempts are never kept as moves before their assessment |

Later evidence reads again (`reassess`: a new reading, the earlier one kept). The next proposal reads assessed trials
as `t*`: a supported finding reaches later decisions there, with its applicability and exceptions.

## Inspection

`GET /api/learning/trials` (counts, recent trials, recent investigations) and `GET /api/learning/trials/{id}` (every
revision, the events, every reading), owner only. Learning page "Experiments she ran". Her game summary (the mouth)
says the experiment under way and the latest finding. Every investigation, presentation and assessment is logged to
`var/learning_trials.jsonl` (`LEARNING_TRIALS_LOG_PATH`).

## Settings

`LEARNING_TRIALS_ENABLED` (new investigations and proposals; live trials finish and are assessed regardless),
`LEARNING_TRIALS_MODEL`, `LEARNING_TRIALS_REASONING_EFFORT`, `LEARNING_TRIALS_TIMEOUT_SECONDS`,
`LEARNING_TRIALS_MAX_ATTEMPTS` (investigation retries), `TRIAL_MAX_ATTEMPTS` (3), `TRIAL_MAX_SECONDS` (300),
`TRIAL_MAX_BLOCKS` (8), `TRIAL_WINDOW_SECONDS` (20), `TRIAL_DEFER_HOURS` (2), `TRIAL_NEAR_M` (24),
`LEARNING_TRIALS_LOG_PATH`. The limits are the climbing scenario's starting defaults, not universal budgets. Desktop
mode only.

## Tests and checks

- `tests/test_learning_trials.py`: pure (proposal validity, measurement, prediction checks, what may be concluded,
  the checked reading, the trial run in practice, Student 2's proposal view) and real PostgreSQL lifecycles with
  scripted models: failure -> proposal -> a non-recommended choice -> no revision after selection -> execution ->
  assessment -> hypothesis, tentative lesson, move -> a later proposal reading the finding from a changed position
  with a fresh prediction, the original kept; preparation through the planner and the last-moment readiness check;
  an interrupted run recovered, never repeated, concluding nothing; deferral, reminder, revision with new evidence,
  reconsideration, decline; preparation time running out (no model call); cognition stopping a running trial; one
  investigation per problem.
- `game_packs/minecraft/test/sandbox.test.js`: the block limit refuses the block past it.
- `scripts/trial_eval.py --run`: real proposer and assessor calls on fixed situations (not run yet).

### Completion evidence, live (not done yet)

The plan's evidence needs the real game: a trial whose supported finding changes a later relevant decision, then an
evaluation from a changed obstacle or start with a fresh prediction, plus an inconclusive case with no lesson. With
the pack running and a two-block ledge near her: give her a goal whose step needs getting up it, watch for a
proposal (`GET /api/learning/trials`), let Student 2 decide, and follow it through; then a step needing a different
ledge. The tests above run that sequence with scripted models.

## Not built

- Conversational experiments (separate design discussion, per the plan's boundary).
- Scenarios beyond climbing a small obstacle; a trained world model; exploration budgets across trials.
- Student 2's trained models (qwen-4/5) never saw a live `component_proposal`; the teacher path (Luna, the default)
  decides from the same snapshot shape. Their quality on it is unmeasured.
- Proposals presented on user messages: cognition sees pending proposals only when learning presents them (or a
  deferral comes back), and live trials as work.
- Measuring blocks placed while preparing counts place skills only, not her own programs during preparation.
