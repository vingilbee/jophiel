# Effort awareness and getting unstuck

Status: **design, not built** (2026-11-04, branch `feature/effort-and-stuck`). Revised the same day after review.
Wait for an explicit "ok to implement".

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

## 3. The stuck signal (general, code)

Raised when an effort has spent **too much active time without new observations** or keeps **repeating approaches
that already failed or told her nothing**. Thresholds start as **configurable defaults per source** (a game moves in
minutes, an email search in seconds).

It wakes one hard thought of a new kind, `stuck`. While it runs, **repetitive small surprises on the same effort are
aggregated** ("cobblestone +5 over 3 min"), while important changes and interruptions (danger, death, the user
speaking, a correction) still come through as their own moments.

Being stuck is **evidence available to cognition**, never an automatic announcement. She decides whether saying
"I've been at this 20 minutes and I'm not getting anywhere" would help.

## 4. The stuck thought (mind)

A hard thought with the four layers, the observed facts, the attempts and what came of her conclusions, the
source's senses and the current coverage. It weighs four causes:

1. **My ability:** the skill or tool can't do this (a search radius too small; go_to only finds blocks already seen).
2. **My perception:** what I need may be there, but my observation doesn't cover it.
3. **The situation:** it may not be here.
4. **My understanding:** I have the outcome, approach or a constraint wrong.

It concludes as today (decision / guess / question / intention). A **guess** names the cause it favours and a check,
stating in advance **what each result would support and what it would leave unknown**:

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

**Measure, on a real session compared with 2026-11-04:**

- repeated conclusions per effort, and how many were never acted on;
- corrections: did the right layer change (action cancelled, outcome kept)?
- planner: does the outcome slot still hold an action?
- "which way / can you see it" questions: does she answer from coverage instead of guessing?
- active time to the first new observation after a failure.

### Stage 2: the stuck signal and the stuck thought

### Stage 3: trials per cause, results recorded as supported / not supported / unknown

### Stage 4: lessons to the planner and hands

Each stage gets a short report from a real session before the next.

## Decided (2026-11-04 review)

| Question | Decision |
|---|---|
| Stuck thresholds | Configurable defaults per source; active time counted separately from paused time |
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
