# Audit: can she learn from experience? Gap map for steps 2–4

> **2026-11-13:** Student 1, Student 2, the shared-state pipeline, appraisal and mood were retired; one mind now decides every turn and wake. Where this document describes them, it records the system as it was. Current design: [MIND_ARCHITECTURE.md](../MIND_ARCHITECTURE.md).

Status: audit, 2026-11-04. Read from the code, not the step docs, and checked against the game session of
2026-11-05 00:10–01:04 UTC ("make an iron armor set", the user as examiner, memory reset before it). Nothing here is
authorized to build. [Plan index](README.md).

## What she should be able to do (the user's expectation, 2026-11-04)

Learning builds experience and adapts the way a person does:

- **Game:** asked to dig down, she digs straight down and falls into lava. She keeps that experience and next time
  digs down a better way. That is her hands' decision.
- **Conversation:** she notices the user likes to talk, so she talks more with him. That is cognition's decision.
- Good and bad outcomes decide what she repeats and what she avoids.

Broken into what each part must do:

| # | Requirement |
|---|---|
| R1 | **What starts learning**: experiences that mattered (a death, a loss, a look back saying "not getting anywhere", a success after a struggle, a pattern in how the user responds), not only single failures |
| R2 | **The unit**: a stretch of experience (what she meant to do, what she did, what happened, what she thought), not one action |
| R3 | **How good or bad it was**: scored on the six dimensions, measured by code where code can count it, good outcomes included |
| R4 | **What it concludes**: a better way to do it next time ("to dig down, dig in steps"), not only whose fault it was |
| R5 | **How much one experience counts**: a costly one can be used straight away, still marked as being tried |
| R6 | **Who uses it**: the part that makes that decision (hands, planner, mind, Student 2) |
| R7 | **When it comes back**: recalled when a similar moment comes, by the part deciding |
| R8 | **Checked when used**: what happens after she uses it supports, narrows or withdraws it |
| R9 | **Testing a guess on purpose**: a trial shaped to the cause she suspects, or to the new method |
| R10 | **One place for lessons**, with one set of evidence rules |

## How it works today, in one picture

There are **two separate learning systems**:

| | Learning system (steps 1–4) | Game reflection (older, `services/game_learning.py`) |
|---|---|---|
| Starts on | a correction or complaint (Student 1's reading), 10 % of other messages, a cut-off or failed reply, a failed effect, **a failed skill run** (`learning_assessment._triage_one`, `sweep`) | a game session leaving play (paused long enough, offline, ended) |
| Looks at | one failed attempt and its record (`_game`: only `skill_outcome` rows and her hands' decisions) | the stretch: skill outcomes, notices (death, damage, night), plan events, the user's words (`build_timeline`) |
| Concludes | targets, causes (which part of her), six dimensions, choice vs outcome (`ai/learning/assessment.py`) → lessons of kind `preference` / `strategy` (`ai/learning/lessons.py`) | an episode, lessons `skill` / `plan` as "situation → advice", proposals for code changes |
| Goes to | **Student 2 only** (`memory_lessons.for_decision`, called only from `student_two.py`) | hands (`lessons_for_run`), planner and hard thoughts (`desktop_game._learned_lines`), conversation (the episode) |
| Trust | `strategy` needs 2 distinct occasions; until then a hypothesis, not supplied | starts `trial` and **is supplied at once**; confirmed by support from 2 *different sessions* |
| Trials (step 4) | started by her hands struggling (the 4th / 10th failed try, still counted in `skill_driver.py:795`), a step stuck, an open hypothesis; one experiment: `climb_obstacle` | results feed back as game lessons (`note_trial_lesson`) |

Last night: 20 assessments (all failed skill runs; goal progress "poor" ×81, cost "poor" ×6), 6 lessons for Student 2
(all about her hands' choices, e.g. "check a go_to target uses an accepted format"), 4 game lessons, 1 proposal,
2 trials (one deferred, one rejected). Nothing about the death, the 11-minute loop, or her own diagnosis at 00:55
that digging down "was my assumption, not Vincent's".

## Gap map

| # | Today | Gap | Changes in |
|---|---|---|---|
| R1 What starts it | Failed single runs, corrections, a 10 % sample. A death is recorded (`game_experiences` notice `died`, with what she dropped) but is **not a trigger**. Look backs (`mind_thoughts.assessment`, effort stage 2) reach no part of learning | No trigger for what mattered: death, a big loss, a look back with `progress: no`, a goal or step finished after a struggle, her own self-correction, the end of a conversation | **Step 2** |
| R2 The unit | Game: one key per `skill:<session>:<step>:<skill>`; evidence is skill outcomes only (no notices, no plan, no thoughts, no health or height). Conversation: a 12-message window around one message | No experience-sized unit. The effort record (`services/effort.py`) already builds exactly this stretch (outcome, approach, actions, her conclusions, the user's words, time); learning doesn't read it | **Step 2** (reuse `effort.py`) |
| R3 Good or bad | Six dimensions exist and are filled by the assessor model, only for what triggered an assessment. Code-countable costs (died, items lost, time, health) are never given to it as measured facts | Successes and the death are never scored; code measurements aren't used; scores don't weight anything | **Step 2** (measured facts in), **step 3** (scores weight lessons) |
| R4 What it concludes | Assessment: whose fault. Lessons: `preference` (what the user said) or `strategy` (what works *with this user*); the builder's prompt is written for conversation exchanges only ("one assessed exchange with a user") | No "next time, do it this way" for a task. Game lessons from the assessor came out as checks ("check a mining target is in sight") | **Step 2** (contract asks for it), **step 3** (lesson kind `method`) |
| R5 One costly experience | Strategy: 2 occasions, or not supplied. Game lessons: supplied at once at `trial`, with weak evidence (last night: "shelter before night", not shown by the record) | The two systems disagree, and neither ties weight to cost. A death must count; a weak guess must not reach her hands as if it were known | **Step 3** (status rule; needs the user's decision) |
| R6 Who uses it | Memory lessons: Student 2 only (`03-implementation.md`: "Consumers other than Student 2 … receive no lessons"). `memory_lesson_uses.consumer` already exists. Game lessons reach hands, planner and hard thoughts | The tested, evidence-checked system can't reach the parts that act in games; the one that can has weaker rules | **Step 3** (consumers: hands, planner, mind) |
| R7 Comes back when similar | Student 2: lessons ranked by embedding + words against the decision (`for_decision`). Game: all game lessons up to 12 every time, plus **only the last session's** recap (`latest_episode`) | No recall of a similar past experience at the moment of a game decision ("last time I dug straight down here…") | **Step 3** (`for_decision` for each consumer, query = the step's intention + situation) |
| R8 Checked when used | Student 2 says how it weighed each lesson (`lessons_weighed`), recorded per decision. Nobody connects what happened *after* to the lesson. Game lessons only move between sessions | No "I used it, it worked / didn't" loop for any consumer | **Step 3** (outcome after a decision with a lesson → support / counter); **step 4** for a deliberate test |
| R9 Testing on purpose | One scenario (`climb_obstacle`). Look backs give `cause` + `check`, but only a thought of kind `guess` becomes a hypothesis (`thought_memory.KIND`); last night every look back was a `decision`, so nothing reached trials. Trials still start on a try count (4th / 10th failure) | No trial per cause (ability, perception, situation, understanding), no "try the new method" trial, and the trigger is a count, against the rule that her mind judges | **Step 4** |
| R10 One place | Two lesson stores (`memory_lessons`, `game_lessons`), two trust rules, two prompts | Fold game lessons into memory lessons; keep the reflection's episode and proposals | **Step 3** |

## Exact changes per step

### Step 2: assessment

1. **New triggers** (code, in `learning_assessment.triage` / `sweep`; each a `kind` + key):
   - game: `died` and a large loss (from notices); a step ending done, given up or replaced after her hands tried it
     (the same moments effort stage 2 uses); a look back with `progress: no`; a goal finished or abandoned.
   - her own: a hard thought whose conclusion corrects an earlier one of hers (it names `follows` and says why).
   - conversation: the end of a conversation stretch (gone quiet, or the user leaves), so a whole exchange can be
     appraised, good or bad, not just a sampled message.
   - Keep the existing triggers. A failed skill run joins the experience it belongs to instead of standing alone.
2. **The experience as the unit**: key `experience:<goal or conversation>:<window>`; evidence from `effort.py` (the
   effort's timeline, her conclusions with `follows`, look backs) plus the notices `_game` leaves out (died, hurt,
   lava, height, health).
3. **Measured facts in**: code passes what it counted (deaths, items gained and lost, time active, health lowest, the
   effort's observed facts) as facts the six dimensions must start from. The model fills only what code can't count
   (understanding, relationship, character fit).
4. **The contract asks for a method**: besides targets and causes, `next_time`: for this kind of situation, what
   she'd do differently (or "keep doing this"), where it applies, and what the evidence leaves unknown. Keeps the
   rule "a guess is never truth": it is a proposed method with its evidence, not a fact.

### Step 3: lessons

1. **Lesson kind `method`**: "to do X in situation Y, do Z", with `for` = the part that decides it (`hands`,
   `planner`, `mind`, `student_2`), `applies_when`, exceptions.
2. **Consumers beyond Student 2**: `for_decision` called by her hands (`skill_driver`), her planner (`plan.py`) and
   her hard thoughts (`mind_hard`), each recording uses in `memory_lesson_uses` (the `consumer` column exists).
   Changes the rule in `03-implementation.md`.
3. **Trust tied to cost** (the user decides the rule): e.g. a method from a costly experience (a death) is supplied
   at once as `trying`; other inferences keep the 2-occasion rule. A lesson the record doesn't show (the "night"
   lesson) is never supplied.
4. **Checked on use**: after a decision that received a method lesson, its outcome (from capture) queues a lesson job:
   support, counter, narrow. Occasions are experiences, not sessions.
5. **Fold game lessons in**: the reflection's lessons become memory lessons (`method`, `for` hands or planner); its
   episode and proposals stay. Retire `game_lessons` and the "different session" rule.
6. **Builder prompt**: written for a task as well as a conversation (today it only knows "an exchange with a user").

### Step 4: trials

1. **From her look backs**: a look back with a `cause` and a `check` opens a hypothesis regardless of its kind
   (today only `guess` does). This was the effort plan's stage 3.
2. **Trial shapes per cause**: ability (same target, other skill or setting), perception (change the vantage, look
   again), situation (go somewhere untried, look once), understanding (ask the user), plus **trying a new method**:
   a method lesson's first uses carry a prediction and are assessed like a trial.
3. **No count trigger**: replace the 4th / 10th failure trigger with the look back (the user's decision of
   2026-11-04: no code-defined stuck).
4. **A missing move goes to her learner**: a method that needs a move she doesn't have ("dig in steps") hands the task
   to practice (stage H1) instead of failing as a trial.

### Outside steps 2–4, needed for them

| Change | Step | Why |
|---|---|---|
| Capture the planner and hard thoughts as learners | 1 | Today "the planner's decisions aren't captured", so no assessment can establish a planner cause. The old-rules bug (rules from earlier tests kept in a new goal's plan, `game_planning.apply`) is invisible to learning |
| Record what hurt her (damage cause: lava, fall, a mob, drowning) | pack | The death notice says what she dropped and which hostiles were near (none), not what killed her. Without it she can't learn "digging down opened into lava" |

## Already built: her effort and her look backs

These came from the effort plan (`docs/COLUMBINA_EFFORT_AND_STUCK_PLAN.md`, removed 2026-11-04; its last version is
in commit `d7ff4d2`). Its stages 3-4 (trials per cause, lessons to the planner and hands) are steps 4 and 3 above.

| Piece | What it does | Where |
|---|---|---|
| **Effort record** | One per outcome (the effort id is the goal item): the outcome, the approach (plan goal), actions, the user's rules (`constraint` items) and places (`reference` links), active and paused time, observed facts counted side by side, and one timeline of her conclusions, plans, hand runs and the user's words. Code counts and orders; it never says what it means | `app/ai/mind/effort.py` (pure), `services/effort.py`; migration `c5f1a7d3e9b2` |
| **`follows`** | When her planner or mind acts, it names the earlier conclusion it carries out; code stores what the model said, never infers the link | `ai/mind/plan.py`, `ai/mind/hard.py`, `mind_thoughts.follows` |
| **Senses and coverage** | What she perceives in Minecraft, checked against the code, and what the current moment doesn't cover; the shape of the place and the open space reach her mind | `services/mind_minecraft.py` (`SENSES`, `coverage`, `SHAPE_KEPT`) |
| **No code-defined stuck** | No time limit or try count decides she's stuck (the user's decision). A look back (trigger `review`) starts when a plan step ends: done, her hands gave up, or the plan moved on after her hands tried it | `services/mind_minecraft.py` (`step_ended`) |
| **Her judgment** | Every hard thought with an effort says `progress` (yes / no / unclear); when not yes, a `cause` (ability, perception, situation, understanding), a `check`, `if_supported`, `leaves_unknown`. Shown on her timeline afterwards | `ai/mind/hard.py`, `mind_thoughts.assessment` (migration `d8a2c6f4b1e7`), `services/effort.py` |
| **Fewer small thoughts** | Small surprises add up into a waiting look back; a rethink within 60 s of a plan change waits; going in or out of a cave raises one thought per 2 min at most | `services/mind_hard.py`, `services/game_planning.py` |
| **Planner looks first** | It writes `sees`, `cannot_see`, `progress` before deciding; steps are intentions in plain words that keep the user's method; her hands carry them out as worded | `ai/mind/plan.py`, `ai/desktop/skill_driver.py` |
| **Open space and height** | The air connected to where she stands (size, floor, water/lava at its edge, passages and whether they go down); her height now, a minute and five minutes ago | `game_packs/minecraft/src/surroundings.js`, `src/state.js` |

Known problems from the live test of 2026-11-05 00:10 UTC: rules from earlier tests carried into a new goal's plan
(`game_planning.apply` keeps the old plan's rules on a revision, capped at 5, old first); a look back's "no" changes
only her hands' tips, never the plan; surprise thoughts were still 37 of 55.

## Found while auditing

- **The game session never ends.** `desktop_sessions` row `9e42d19d…` has been the live session since 2026-10-20.
  Game lessons only gain trust from *different* sessions (`_seen_in`), so all 4 sit at `trial` and none can be
  confirmed. The plan's rules persist across tests for the same reason, and the memory reset keeps game sessions by
  design.
- **Correction to my earlier report:** I said she burned to death in lava. The record doesn't show that. She was hurt
  (16 → 5 health) and died within about 3 seconds while digging down at height 66, at night, with no hostile within
  8 blocks. Her hands called it "critical health beside lava". Lava had been seen about 8 blocks away at 00:20. The
  cause isn't recorded.
- **Lessons are given to the part that can't use them:** last night's 6 memory lessons are about her hands' choices
  but are only supplied to Student 2.
- **Hurt notices are rate-limited** (one per 15 s), so the fall from 16 to 5 health and the death show as one
  "8 of 10 hearts left".

## Decisions for the user

1. How much one costly experience counts (R5): used at once as `trying`, or wait for a second time?
2. Fold game lessons into memory lessons (R10)? Recommended.
3. Conversation patterns: should she infer them herself (long messages, follow-ups) or only from what the user says,
   with what the user says weighing more? Recommended: both.
4. Should a game session end at a natural point (a new goal from the user after a long gap, or a reset), so "a
   session" means something again? Needed for any rule that counts sessions, and for the old-rules bug.

## Order, if accepted

1. Fix the old-rules bug and give a look back's "no" a way to change the plan (from the effort session).
2. Step 2 triggers + experience unit + measured facts (no new consumers yet; check the conclusions on real sessions).
3. Step 3 `method` lessons, consumers, trust and checking on use; fold game lessons in.
4. Step 4 trials from look backs and per cause.
5. The effort plan's stages 3–4 are covered by 3 and 4 (see "Already built").
