# Effort awareness and getting unstuck

Status: **stage 1 built and tested live** (2026-11-04, branch `feature/effort-and-stuck`, migration `c5f1a7d3e9b2`).
**Stage 2 built** (2026-11-04, migration `d8a2c6f4b1e7`) with **2b** (the planner looks first; open space and height
in her state), not yet tried live. Stages 3-4 are design only.

## The goal in one line

She keeps a reliable account of what she is trying to achieve, how she is going about it, what she has observed and
whether her own earlier conclusions were acted on. When an effort isn't getting anywhere, she can ask *why*
(her skill, her perception, the situation, or her understanding of what was asked), check carefully, and keep only
what the evidence supports. This works in any source, not only Minecraft.

## Why: what the 2026-11-04 morning session showed

Session 15:44–16:14 UTC, outcome "a full set of iron armor", Vincent guiding her in a cave.

| What happened | Evidence |
|---|---|
| She wandered, repeating searches that could not work | about 33 `no iron_ore / stone in sight within 64 blocks`, 6 refused go_to targets |
| Her mind reached the same conclusion three times | "stop retrying the ore target and explore the cave" at 15:49, 15:56, 16:09 (`var/mind_hard.jsonl`); nothing shows whether she acted on it |
| Her thoughts were mostly small reactions | 25 of 35 hard thoughts were `surprise` ("you got cobblestone x1"), 4 were `failing` |
| No sense of time or what had been observed | the input lists failures one by one, never "30 min on this, these places checked" |
| Outcome, approach and action were mixed up | her input said "Your goal: follow tearDrop12": following was an *action*, the cave an *approach*, iron armor the *outcome*. "Don't follow me" should have cancelled only the action |
| She couldn't say what her observation didn't cover | Vincent asked "which branch goes deeper?"; her view said "stone 2 m above your head" |
| The learning loop learned, but about the wrong thing | 7 lessons, nearly all about her hands' choices, given only to Student 2 (hands receive none) |

## Who owns what

Learning proposes, the mind decides. The loop crosses that boundary once: **the mind guesses, learning tests and remembers.**

| Part | Its job |
|---|---|
| Code (general mind + each source) | Keeps the effort record: counts observable facts, time, attempts, and what happened after each conclusion. Never interprets them. |
| Mind (hard thought) and planner | Interpret: is this progress, which layer does a correction change, why is it stuck, what to try |
| Learning (trials, assessment, lessons) | Turns a guess into a check, records what the check supports and what stays unknown, keeps a lesson only with evidence |

A guess never becomes truth without evidence (existing rule).

## 1. The effort record (general, code)

One per **effort**, with a stable effort id, scoped to one activity (`Scope`). Persisted in **working memory**, with a
compact live summary in the mind's per-activity state. Code fills it; code never decides what it means.

### Four layers, kept separate

| Layer | Meaning | 2026-11-04 example |
|---|---|---|
| **Outcome** | what the user ultimately wants | a full set of iron armor |
| **Approach** | how she is trying to reach it right now (can change many times per outcome) | search the cave for exposed iron |
| **Action** | what she is doing this moment | follow tearDrop12; go_to block:iron_ore |
| **Constraints and references** | user corrections and rules; places, objects and people that matter | "this is your task, don't follow me"; "go to the side that goes down"; the cave Vincent showed her (position) |

A user correction is **recorded immediately** with its words and time, unattached. Cognition (mind or planner) decides
whether it changes the outcome, the approach, the action, or adds a constraint. "Stop following me" then cancels an
action and keeps the outcome and the cave as a reference.

These layers feed **both the mind and the planner**, so the planner stops writing an action ("follow X") into the
slot where the outcome belongs.

### How working memory stores them today, and the change

Checked against the 2026-11-04 session (`working_items`, `memory_events`, `desktop_sessions`).

| Layer | Today | What went wrong | Change |
|---|---|---|---|
| **Outcome** | a `goal` item, owner user, reported: "Find a full set of iron armor" (15:44:56), pointed at by `desktop_sessions.goal_item_id` | the mind never reads it: `mind_minecraft.gather` passes the **plan's** `goal` text, which the planner had set to "follow tearDrop12" | the effort id **is** the goal item id. Mind and planner read the outcome from the goal item; the plan's text is shown as the current approach |
| **Approach** | her `decision` items (23 that half hour), flat, not attached to the goal; one replaces another only when a later thought is judged to restate it | 15+ active at once, some contradicting ("follow tearDrop12" and "don't follow me" both in force); no way to see them as one effort | `decision`, `intention`, `hypothesis` and `question` items may point at the goal they serve (`parent_id`, today allowed only goal → sub-goal). Shown under the outcome, oldest first, with status |
| **Action** | the plan's steps and her hands' runs; `action_failed` / `item_added` memory events | the 16 `action_failed` events weren't linked to the goal (`memory_events.item_id` linked only items' own add/change events) | actions stay **out of working memory** (too fast); their events get `item_id` = the goal, so they appear on the effort's timeline |
| **Constraints** | game plans have `rules`, none were set; nothing in memory | "No, don't follow me. This is your task." was stored nowhere | new item kind **`constraint`**: owner user, reported, `user_words` required, optional `parent_id` to the goal, statuses `active` / `lifted` |
| **References** | `memory_entities` (kinds include `place`, `thing`, `person`) with `memory_entity_links` to items, used in conversation | the cave Vincent showed her was never kept | reuse them: a `place` entity (coordinates in its metadata) linked to the goal; add link role `reference` |
| **Correction** | only the chat message | n/a | a memory event `user_correction` linked to the goal at once, with the message as evidence; cognition then lifts/creates a constraint, changes a decision, or (rarely) the goal |

New schema is small: one item kind (`constraint`), one event kind, one entity link role, and a looser `parent_id` rule.
No new tables.

### Observed facts, not a progress number

Code records **observable facts** relevant to the outcome and counts them. It does not claim every goal has one
progress number.

- Changes the source can count: items gained and lost, with how (mined, smelted, crafted, dropped, died); tools
  made; places reached; things seen for the first time.
- A source may name facts as relevant to an outcome (raw iron, iron ingots, iron armor, a stone pickaxe for iron),
  but it reports them side by side. Raw iron going down because it was smelted is shown as "smelted 3 raw iron →
  3 iron ingots", not as a loss.
- Whether that adds up to progress is for cognition to judge.

### Time

Active time and paused time are counted separately (game paused, user away, she was waiting on an answer). Stuck
thresholds use active time only.

### Attempts and what came of her conclusions

Repeating a conclusion can mean very different things: she never acted on it, she acted and failed, she acted and
learned nothing, or it worked. **The model judges which, not code.** Code gives her the structure to see it:

- **One timeline per effort**, oldest first: her conclusions (with ids, e.g. `D3`), plan changes, actions with their
  results, observations, user words and corrections, each with its time. Code orders, groups and counts
  ("go_to block:iron_ore failed ×4: no iron_ore in sight within 64"); it never says which action came from which
  conclusion.
- **The model names its own basis when it acts.** When the planner changes the plan or her mind decides, it says
  which earlier conclusion it is carrying out (`follows: D3`), or none. Code stores what the model said and shows it
  on the timeline. Student 2 already does this with its `basis` field.
- She reads the timeline and answers for herself: "I said I'd explore the cave at 15:49 and 15:56, but the plan
  kept the surface search; I never actually went in."

## 2. What each source adds (extend the `Source` contract)

| Contract piece | Meaning |
|---|---|
| `observations(effort)` | the countable facts above, with how each changed |
| `senses()` | how she perceives here, fixed per source, **checked against the code** |
| `coverage(moment)` | what the current observation does and doesn't cover, from the moment's own data |
| `group(actions)` | how to collapse identical repeated actions and failures on the timeline (same skill, same target, same result); not a judgment about approaches |

### Senses, as the Minecraft code actually works (verified 2026-11-04)

- **Blocks** (`src/sight.js`): she knows a block when a ray from her eyes reaches an open face of it, or when she saw
  it earlier (up to 4000 remembered positions). Solid blocks stop the ray. There is no light check, so darkness
  doesn't hide anything. Searches have a radius set by the skill (go_to: 64, mine: its `radius`) and check at most
  128 candidates, so many hidden ones nearby can crowd out a visible one further off.
- **Ways** (`src/surroundings.js`): in 8 directions, walks the ground up to 24 blocks (step up 1, down at most 3)
  and reports what stops her and how far it rises or falls. It stops at the first wall.
- **Openings**: cave mouths and tunnels are detected **only while she is under open sky**, along 16 directions.
  Underground, nothing reports passages or which one goes down beyond what the 24-block ground walk shows.
- **Overhead**: open sky, trees, roof, or underground with how far up the rock starts.

**Found 2026-11-04: her mind never gets the shape of the place.** `_brief` in `app/services/mind_minecraft.py`
cuts her state at the first "Ways", "Land", "Highest ground", "Surface", "Way out" or "Openings" line. None of the 30
Minecraft hard thoughts that morning saw the ways, so when Vincent asked which branch goes deeper, the rise or fall
per direction was in her hands' state but not in her mind's. Stage 1 gives the mind a short form of it (and the
coverage line below), rather than the full block.

### Coverage, per moment

Fixed capabilities aren't enough. Each moment also states what this particular observation leaves open, e.g.
"underground: ways cover 24 blocks along the ground; openings aren't detected here; nothing here says which passage
descends". That lets her answer "which branch goes deeper?" with "I can't tell from here" for the right reason.

## 3. Looking back: when she judges whether it's getting anywhere

**Revised 2026-11-04 (the user's decision): no code-defined stuck signal.** No time limit or count of tries
decides that she's stuck; that is her mind's judgment, made from the whole record. Code only decides *when* she
looks back, at moments where looking back makes sense, and never what she concludes.

- **Every hard thought that has an effort judges it**: is the effort getting anywhere (yes / no / unclear),
  from the whole record, not the moment alone.
- **A look back** (hard-thought trigger `review`) starts when a part of the effort ends: a step of her plan is
  done; her hands give up on it (stuck); or the plan moves on while the step was unfinished *after her hands
  had tried it* (a step replaced before anything was tried isn't a part that ended).
- The old "step keeps failing" prompt (her hands' 4th and 10th failed try) is gone from Minecraft: it was a
  count, and it saw one step, not the effort (in the morning session the plan changed ~40 times, resetting it).
- While a look back waits, small surprises on the same activity are **added up into it** ("surprise: you got
  cobblestone +1 (×4)") instead of each taking a thought, and a surprise never pushes a waiting look back out.
  Two waiting surprises are grouped the same way. Danger, the user speaking, a hand-off and a goal change come
  through as before.

Being stuck is **hers to know**, never an automatic announcement. She decides whether saying "I've been at
this a while and I'm not getting anywhere" would help.

## 4. Why it isn't working (mind)

Any hard thought with an effort (a look back above all) reads the four layers, the observed facts, the attempts and what came of her conclusions, the
source's senses and the current coverage. It weighs four causes:

1. **My ability:** the skill or tool can't do this (a search radius too small; go_to only finds blocks already seen).
2. **My perception:** what I need may be there, but my observation doesn't cover it.
3. **The situation:** it may not be here.
4. **My understanding:** I have the outcome, approach or a constraint wrong.

It concludes as today (decision / guess / question / intention), and besides the conclusion gives her judgment
(`mind_thoughts.assessment`): `progress`, and when it isn't "yes", the `cause` she favours, a `check`, what it
would support (`if_supported`) and what it would leave unknown (`leaves_unknown`), stated in advance:

> Guess: perception. Check: go to the nearest high point and read the openings.
> A cave in view supports perception (it was there, I couldn't see it).
> No cave in view supports only "no cave mouth visible from this point"; it does **not** show the area has none.

Asking the user is a valid check, and usually the right one for cause 4.

## 5. Testing the guess (learning)

The guess becomes an open hypothesis in working memory, which already feeds trials
(`learning_trials.consider(kind="hypothesis")`). Trials today know one experiment, `climb_obstacle`; they need shapes
per cause:

| Cause | Experiment shape | Minecraft example |
|---|---|---|
| ability | same target, other skill or setting | mine with a larger radius; dig down instead of walking |
| perception | change the vantage, observe again | go to `peak`, read openings |
| situation | move to an untried area, observe once | walk 100 m in a direction not yet covered |
| understanding | ask | "do you want me to follow you or find it myself?" |

Every trial result records **supported / not supported / unknown** for the guess, plus the scope it holds in (this
place, this kind of place). The assessor (step 02) checks this. A lesson is made only from what was supported, in the
scope it was supported in. "No cave seen from the peak" never becomes "this area has no caves" or "peaks don't help".

## 6. Keeping the answer where it acts (learning → consumers)

A supported answer becomes a lesson that reaches **the planner and the hands**, not only Student 2. Today only Student 2
receives lessons (`learning-system/03-implementation.md`: "Consumers other than Student 2 … receive no lessons"); this
changes that rule.

## Order of work

### Stage 1: a reliable account (no new behaviour)

Give the mind and the planner, for each effort:

- the outcome, approach, action, and constraints/references, kept separate, with user corrections recorded as they come;
- active and paused time;
- observed facts, counted, side by side;
- approaches attempted, with their failures grouped;
- her earlier conclusions on one timeline with what followed, and the planner/mind naming which conclusion it is carrying out (`follows`);
- the outcome read from the goal item, not the plan's text; the constraint kind and correction events stored;
- senses and current coverage, including a short form of the ways the mind is cut off from today (Minecraft first; the conversation source gets time, layers and conclusions).

**Promise:** a more reliable account. Not a fix for wandering or the wrong goal on its own.

**As built (2026-11-04):**

| Piece | Where |
|---|---|
| The record's shape, collapsing repeats, active/paused time, rendering | `app/ai/mind/effort.py` (pure) |
| Building it for a game session from the existing records (plan events, skill runs, her thoughts, the conversation, pauses, rules, places) | `app/services/effort.py` |
| Her hard thoughts read it in place of the plan's goal; the shape of the place is back in her view; she names `follows` | `app/ai/mind/hard.py`, `services/mind_minecraft.py` (`SENSES`, `coverage`, `_shape`), `services/mind_hard.py` |
| Her planner reads it, names `follows`, and names the user's rules (`user_rules`, `lift_rules`) and places | `app/ai/mind/plan.py`, `services/game_planning.py` (`remember`) |
| Schema: `constraint` items, items under the goal they serve, `reference` entity links, `mind_thoughts.follows` | migration `c5f1a7d3e9b2`, `services/memory_manager.py` |
| Her concluded thoughts and the game's failure events attach to the session's goal | `services/thought_memory.py`, `services/observation_memory.py` |
| Tests | `tests/test_effort.py` |

Choices made while building:

- The timeline lists the last 30 min of active play. Her conclusions, plans, the user's words and stuck notes are
  always listed; when it runs past about 100 lines, the oldest stretches of hand runs, replies and pauses between
  them become one summary line each ("hands: 14 runs, 5 failed (no iron_ore in sight ×3)"). On the 2026-11-04
  session that is about 20,000 characters.
- A rule is kept as the user's only when the planner's quoted words are found in a user message since the outcome
  was set (that message becomes its evidence); otherwise it's logged as not kept.
- Hand runs that were decided but discarded before running (`not_executed`) are left out: nothing happened.
- The conversation source gets no effort yet (no outcome to hang it on); its hard thoughts read as before.

**Measure, on a real session compared with 2026-11-04:**

- repeated conclusions per effort, and how many were never acted on;
- corrections: did the right layer change (action cancelled, outcome kept)?
- planner: does the outcome slot still hold an action?
- "which way / can you see it" questions: does she answer from coverage instead of guessing?
- active time to the first new observation after a failure.

**Live test 1 (2026-11-04, 20:35–20:59, new map, same outcome): still bad.** The effort reached 23 of 31 hard
thoughts; she kept the user's rule (stored from "No, don't follow me. This is your task."), named `follows`, and
noticed her loops. Her control didn't change, for three reasons, fixed after:

| Found | Fix |
|---|---|
| A revision never replaced the plan's goal: "follow tearDrop12" outlived ~40 revisions that each said to stop following; her hands kept running `follow_player` | a revision may change the plan's goal (her approach); the user's rules in force go into every plan's rules for that outcome (`game_planning.apply`) |
| Every surprise thought went to Student 2, which asked for a rethink: 44 plan revisions in 24 min; going in and out of a cave started a new thought each time; the coverage line ("cave mouths aren't detected from here") read as advice to go back out | a rethink within 60 s of the last plan change waits (`RETHINK_GAP_S`; goals, stuck and other news never wait); going in/out raises at most one hard thought per 2 min (`PLACE_SURPRISE_GAP_S`); coverage worded as facts only |
| A quiet-time decision suspended the goal; "go ahead and resume" was refused three turns running | resuming for the user makes a suspended goal active again (`desktop_game._reactivate_for_play`); a closed world or a finished goal still blocks |
| On a new map she read the morning's history as if it were now | no code rule for "a new attempt" (the user's decision): the timeline shows the time now, marks stretches of 10+ min with nothing recorded, and says parts may belong to an earlier session or place, judged from times, gaps and what the user said |

### Stage 2: looking back, and why it isn't working

**Promise:** she judges, at natural moments, whether her effort is getting anywhere and, when it isn't, why and
how she'd check. Nothing in code decides she's stuck. Not yet: testing the check (stage 3).

**As built (2026-11-04):**

| Piece | Where |
|---|---|
| Trigger `review`; `progress`, `cause`, `check`, `if_supported`, `leaves_unknown` in her answer (asked only when she has an effort; a cause only with progress not "yes", a check only with a cause) | `app/ai/mind/hard.py` (`CAUSES`, `EFFORT_RULES`, `assessment`) |
| A look back when a step ends (done, hands gave up, plan moved on after her hands tried it); the 4th/10th-failure prompt removed | `services/mind_minecraft.py` (`step_ended`) |
| Surprises added up into a waiting look back (and into each other); a look back holds while its outcome does | `services/mind_hard.py` (`_better`, `with_meanwhile`), `mind_minecraft.dropped` |
| Her judgment stored and handed to memory | migration `d8a2c6f4b1e7` (`mind_thoughts.assessment`), `ThoughtConcluded.assessment` |
| Her earlier judgments on her timeline ("[getting anywhere: no; cause: perception; check: …]") | `services/effort.py` (`judged`) |
| Tests | `tests/test_effort.py`, `tests/test_mind.py` |

**Measure, on a real session:**

| Try | Expect |
|---|---|
| Send her after something that isn't nearby | Look backs as steps end; once it isn't working, `progress: no` with a cause and a check |
| Read a look back | The cause fits the record; `leaves_unknown` says what a negative check wouldn't show; no earlier conclusion repeated |
| After it | Her next plan or tip carries it out (`follows` names it), or she asks you (cause: understanding) |
| Count surprise thoughts per 30 min | Clearly fewer than 25 of 35 (2026-11-04 morning) |

### Stage 2b: her planner looks first, and she sees the space she's in

**Why (session 2026-11-04 21:18–21:29):** her mind understood "dig the block beneath you until you find a cave",
but her planner wrote `mine(grass_block, count=1)` → `mine(stone, count=1)` → `mine(iron_ore, count=23, radius=64)`.
Every step had to be one skill call with a counted finish line, so the user's method shrank to "gain 1 dirt";
she dug about 5 blocks (the `mine` skill breaks the *nearest* stone, not the one beneath) and her hands went
back to the surface for the iron search. The planner already saw her full state; what it lacked was the order a
person plans in, and the hands lacked a measure of the space around them.

The user's decisions: no new skills (she learns them herself from now on); the planner plans like a person,
from what she perceives; perception reports measurements, never what the place *is*.

**As built (2026-11-04):**

| Piece | Where |
|---|---|
| The planner writes `sees`, `cannot_see` and `progress` before deciding; plans from where she is ("What it takes" is the ingredients, not the plan); steps are intentions in plain words, keeping the user's method; `done_when` a count or a situation her hands can see, never a count standing in for an intention; an intention no skill can do stays, so her hands name the missing move and she can teach herself it | `app/ai/mind/plan.py`, `services/desktop_game._pack_line`, `services/game_planning.py` (logged; `progress` on the plan's thought) |
| Her hands carry out an intention the way it says ("straight down" = under her feet) until the situation shows in their state, and say what shows it | `app/ai/desktop/skill_driver.py` |
| **Open space**: the air connected to where she stands, filled through air only, out to 12 blocks sideways, 4 above her head, 8 below her feet: size, floor, water/lava at its edge, and the passages where it goes on (direction, level or going down) | `game_packs/minecraft/src/surroundings.js` (`space`, `describeSpace`) |
| **Height** now, a minute ago, five minutes ago | `game_packs/minecraft/src/state.js` (`heightTrail`), `src/describe.js` |
| Her mind reads both; her senses and coverage say what they do and don't cover | `services/mind_minecraft.py` (`SHAPE_KEPT`, `SENSES`, `coverage`) |
| Tests | `game_packs/minecraft/test/surroundings.test.js`, `tests/test_effort.py` |

What gets harder: a situation finish line ("in an open space that goes on") is judged by her hands from what
they see, so it can be wrong (a hole she dug taken for a cave, or a small cave missed). Her look back (stage 2)
and the timeline (her height, what she mined) are where a wrong "done" shows.

**Measure:** give the same outcome; does the plan keep the user's method as an intention with its end; does a
"dig down" step take her down (height) until the open space changes; does "no skill for: …" appear when an
intention needs a move she lacks, and does her learner pick it up.

**Later (the user's idea, not started):** perception from the screen (a vision model such as YOLO) instead of
Mineflayer, so it generalises past Minecraft. The open-space and height lines are measurements of what's
around her, not Mineflayer's own terms, so a vision source could fill the same lines.

### Stage 3: trials per cause, results recorded as supported / not supported / unknown

### Stage 4: lessons to the planner and hands

Each stage gets a short report from a real session before the next.

## Decided (2026-11-04 review)

| Question | Decision |
|---|---|
| Stuck thresholds | ~~Configurable defaults per source~~ Revised 2026-11-04: none. Whether she's stuck is her mind's judgment from the record; code only picks when she looks back (a step ending) |
| Say it aloud? | The evidence is available to cognition; she decides. No automatic announcement |
| Where does effort live? | Working memory, under a stable effort id; a compact live summary in the mind's state |
| User corrections | Recorded immediately; cognition decides whether they change outcome, approach, action or a constraint |
| Which action came from which conclusion | The model judges, from a timeline code builds; the model names its own basis (`follows`) when it acts. Code never infers the link |
| Memory shape | Outcome = goal item (effort id); approaches = her items pointing at the goal; actions as events linked to it; new `constraint` kind; places as entities |
| Small surprises | Repetitive ones aggregated; important changes and interruptions kept |

## Still open

- How an effort starts and ends in a conversation source (a promise? a question she's working on?).
- How long an effort's timeline is shown in full before older parts are summarised (and who summarises: code
  counts, or a model writes a recap).
- Conversation decisions that already exist (tea, Cedar, Sam that morning) have no goal to attach to: leave them
  unattached, or does a conversation get an implicit outcome?

## Out of scope here

- Making her hands smarter by themselves (they get lessons in stage 4, nothing else).
- Inner state influencing any of this (still observe-only).
- A new model or training; this is context, signals and contracts.
