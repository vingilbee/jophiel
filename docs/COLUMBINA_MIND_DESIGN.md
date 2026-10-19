# Columbina's mind: brain, mouth and hands

Status: v1 being built on `feature/minecraft-awareness` (2026-10-14). Games first (Minecraft pack);
the same design is meant to grow into an always-on mind outside games.

## Why

In play she felt unresponsive: she only thought when a few events chosen in code woke her
(danger, stuck, goal done), and she couldn't see what the user was doing. Today she runs as two
parts, her **hands** (the skill driver) and her **mouth** (the chat/voice orchestrator). Neither of
them is awake between events. This adds the third part, a **brain** that is always awake: it looks
at every moment and decides for itself whether the moment deserves a thought, and how hard.

## The three parts

```
                 ┌──────────── BRAIN (mind) ───────────┐
 game state ───► │ every tick: gate → none/react/       │
 + what the user │ light/hard → a thought (model if     │
   does + talk   │ needed)                              │
                 └───┬───────────────┬─────────────────┘
                     │ "say this"    │ plan / modes / rules
                     ▼               ▼
 user speaks ──► MOUTH (talk)    HANDS (driver)
                 answers, reads   runs skills; reflexes in
                 the brain's      the game pack underneath
                 latest thoughts
```

| Part | What it is | Job |
|---|---|---|
| Hands | `app/ai/desktop/skill_driver.py` + the pack's reflexes and modes | do things, one skill at a time |
| Mouth | the chat/voice orchestrator (Luna) | the only part that talks to the user, in her voice |
| Brain | `app/ai/mind/` + `app/services/mind.py` (new) | always on; decides how hard to think, thinks, tells the other two |

All three are Columbina: one identity, one memory. **Nothing waits for the brain.** The hands keep
playing and the mouth answers at once; a thought only adjusts what is already happening.

## A tick

About once a second while a game session is live, the brain builds one tick
(`app/ai/mind/tick.py`):

- **Changed since last tick** (first, so the model reads it first): her health, food, air,
  inventory, things appearing/leaving, what the user did (`game_packs/minecraft/src/watching.js`),
  time of day, her hands' skill, plan and step changes, notices, new messages.
- **Pressure**, decided by code: `urgent` (a hostile within 8 m, she lost health, low air, a fight,
  an urgent notice, the user getting hit), `busy` (her hands run a skill), `calm`.
- Goal and current step, her hands' note, recent talk.
- **Her recent thoughts** and **what she's still thinking hard about**, so a problem that persists
  isn't new every second.
- The full game state text.

## The gate: how hard to think

Every tick goes to a gate that answers one of four levels:

| Level | Meaning |
|---|---|
| `none` | nothing here needs her beyond what the hands and plan already do (most ticks) |
| `react` | an instant reaction, no time to think: danger now, a moment to blurt out |
| `light` | a short thought or one line, no planning |
| `hard` | real thinking or planning, and there is time for it |

Code sets the ceiling, the gate chooses within it: **under urgent pressure `hard` becomes `react`**,
and the hard thinking is postponed until the pressure drops ("after the fight"). Humans work the
same way: under pressure we react from habit; deep thinking happens before and after.

**Which gate.** Compared on 1,269 real ticks from a Nether session, labelled by Luna with reasoning
(Luna agrees with itself on 78%, about the ceiling):

| Gate | Agreed with Luna | Danger caught | p50 / p99 | Cost per hour at 1 tick/s |
|---|---|---|---|---|
| OpenAI Decisions | 64% | 37/40 | 128 / 755 ms | ~$0.29 |
| **Jev** (TypeSafe), threshold 0.7 | 62-64% | 36/40 | **59 / 181 ms** | **~$0.16** |
| gpt-4.1-mini | 61% | 36/40 | 903 / 1,792 ms | ~$1.18 |
| gpt-4.1-nano | 57% | 28/40 | 708 / 1,156 ms | ~$0.29 |
| Strands Decider 2B (local, M1) | 50% | 9/29 | 2,674 / 4,364 ms | free |

Chosen: **Jev**, with OpenAI Decisions as the fallback when Jev errors. Jev returns probabilities;
`light`/`hard` are chosen only when their combined probability clears `MIND_THINK_THRESHOLD`
(default 0.7), which is the dial between "aware and chatty" and "calm" (0.7 thinks on about a
quarter of ticks). Tools: `scripts/mind_gate_eval.py` (replay / judge / report).

Known flaw of that test: the recorded ticks had no "recent thoughts", so a stuck situation looked
new every second (13 of Luna's 23 `hard` labels were one repeated situation). Ticks now carry
thoughts; re-run the comparison on new recordings.

## General mind, one source per situation

The gate and the thinking never name a game. A source (the Minecraft pack today, `app/ai/mind/tick.py`;
later conversation, idle time, the screen, tasks) supplies a `Situation` (`app/ai/mind/situation.py`):
one line saying what she is doing ("playing Minecraft as her own player next to Vincent"), its own
examples for each gate level, and how her spoken lines sound there. Everything else the source knows
goes into its tick text, and it decides news and pressure (`urgent`/`busy`/`calm`). A test pins
that the general prompts contain no game words.

## Thinking

A thought must **think, not describe the moment back** (first live test: most thoughts were
narration, "TearDrop12 is walking west, gotta keep up"). It picks one kind:

| Kind | |
|---|---|
| `why` | why it is happening, what it means |
| `next` | what will probably happen, what she should do |
| `you` | what the user is trying to do, or how they feel |
| `judgement` | what she thinks of it, a better way |
| `feeling` | how it makes her feel |
| `memory` | what it reminds her of, from what is in front of her (never an invented past) |

The schema is `{noticed, kind, thought, say}`: `noticed` takes the description (logged, not shown),
so the thought doesn't have to. The kind is saved with the thought and shown in the panel
("light · why"). Offline check: `scripts/mind_think_eval.py` replays the moments where she thought
live through prompt/model variants (each sees its own earlier thoughts), plus hand-written moments
outside games (`scripts/mind_think_moments.json`), and Luna grades each thought: thinking or
narration, grounded, repeats.

| Level | Who thinks | Can do |
|---|---|---|
| `react` | a fast model (`MIND_REACT_MODEL`, default gpt-4.1-mini) | a gut reaction; say a few words |
| `light` | Luna (`MIND_QUICK_MODEL`, default gpt-6-luna, reasoning off) | write a short thought; say one short line |
| `quiet` | Luna, like `light` | a thought about what's next, the user or herself, never the present; rarely a line |
| `hard` | Luna, as a background game turn (the existing `game_events` path) with her game tools | change the plan, modes or rules, tell her hands, `say`; her written reply becomes the thought |

Limits: one quick thought and one hard thought at a time per session; a quick thought at most every
`MIND_QUICK_GAP_SECONDS` (react skips it); a hard thought at most every `MIND_HARD_GAP_SECONDS`.
A gate answer of `hard` while a hard thought is already running becomes `light`.

**Quiet thinking.** Thinking only on news would leave her blank between events, so after
`MIND_QUIET_SECONDS` (25) with no thought of any kind, nothing urgent, no hard thought running and
the user not having just spoken (10 s: the mouth answers them), she has a `quiet` thought: ahead
(what comes after this), about the user, or about herself, never the present. It must pick a kind:
offered an empty option, models took it every time.

**No repeats** (after the first live test, 2026-10-14, where she thought "still no trees" 5 times in
20 s and reacted to one skeleton 12 times in 30 s):
- A tick with **no news** gets no thought, whatever the gate says (`tick.news`). Background isn't
  news: her own walking, the user switching between walking and running, things farther than 16 m
  coming and going, air still above 10, her own words. A hostile leaving is news.
- The **same danger** gets one reaction per 8 s unless it got worse (`tick.worse`): a new hostile
  within 8 m, one 2 m closer, health lost, or air down to 3.
- The quick model may return an empty thought when its recent thoughts already cover the moment.

**Interruption.** Ticks never cancel a thought. When a thought lands it is checked against now:
still fitting, it's used; if the session stopped meanwhile it's kept as `stale` (shown, never acted
on or said). A hard thought that can't run (the session ended) is saved `unfinished`, and the next
hard thought gets it as "Unfinished: ..." so it continues rather than starts over. A half-done
thought is never acted on.

## When the mouth talks

Only two ways:
1. **The user speaks.** The mouth answers at once, as before, and reads the brain's latest thoughts
   ("On your mind") in its game summary, so the answer fits what she has been noticing.
2. **The brain asks.** A thought's `say` goes through **one speaking gate**, `agent_say.deliver`:
   never over the user or her own reply, a minimum gap (shorter for react), the session's commentary
   mode, spoken aloud only when voice is live and idle. v1: quick thoughts word their own line (one
   short line, in her playing voice) because speed matters; hard thoughts are Luna, who also is the
   mouth's model.

**Honesty rule for the mouth** (the split-brain risk: a speaking part that explains what it didn't
decide, as with "I'll put the armor on" when no skill could): promise only what is in her plan, what
her hands are running, her modes, or her skills; otherwise say she'll try, or ask.

## What the brain replaces

Game-event wakeups for **commentary** (danger, warning, discovery, notable) are skipped while the
brain is on: the brain sees the same notices every tick and decides itself. Wakeups that **control
play** (plan low, goal done, stuck, struggling, needs the user) go to the planner when
`MIND_PLANNER_ENABLED` is on (below); otherwise they still wake a background turn.

## Planning

**Why.** Before the planner, every turn holding the plan tools wrote plans: replies to the user,
background turns on game events, hard thoughts (all Luna, reasoning off, the plan a side effect of
whatever the turn was busy with). In the 2026-10-19 session that was 16 plans in 11 minutes, and a
hard thought wrote "I still need a stone pickaxe to mine iron" while, in the same turn, replacing a
correct wood → stone → iron plan with "mine iron ore" (she held a wooden pickaxe).

**One author.** With `MIND_PLANNER_ENABLED`, only the planner writes the plan of her own Minecraft
player (game packs; the VM path is unchanged):

| Who | Before | With the planner |
|---|---|---|
| Mouth (reply to the user) | `set_game_plan` / `add_game_steps` | `set_game_goal(goal, target, rules)` (the planner writes the steps); `do_now` for a one-go command (follow me, come here), applied at once |
| Background turns, hard thoughts | `set_game_plan` / `add_game_steps` | `rethink_plan(why)` only |
| Hands | `needs_human` paused for the user | report `stuck` (with what they saw); asking is the planner's call |

**When it runs.** Code decides, never the gate: a goal from the mouth, her hands stuck or
struggling, the last step, a goal done, or a hard thought's `rethink_plan`. Each is a planning job
(`agent_events`, source `plan`, one per session, merged while waiting). Jev still gates *thinking*.

**The call** (`app/ai/mind/plan.py`): one structured call, reasoning on (`low`), no tools. Input,
most important first (`services/game_planning.build_input`): why now, the goal and the plan with
step ids, **what it takes** (the pack's `GET /needs/<item>`: what to mine, smelt and craft first,
in order, computed from the game's own data against her inventory), what is happening (her hands,
her player, lately, her thoughts), her skills, how steps are written, what she learned (plan *and*
skill lessons), who is who, the talk. Output: `keep` (the usual answer), `revise` (the open steps by
id, new ones where they belong, removals named: a step is only lost when dropped), `new_goal`,
`wait_for_user` (pauses and asks), `done`; plus `why`, an optional `ask` or `say`, `rules`, `target`.

**Applying it** (code): a decision about a plan that changed meanwhile, or while a newer goal from
the user waits, is dropped (the newer job plans again). `keep` lifts a pause her hands got stuck in.
A user's goal is recorded as theirs (`columbina_user_turn`); a revision as `columbina_mind`; a goal
she picked herself as `columbina_planning_turn` (counts toward her own-goal limit).

**Thinking and planning can't disagree:** the decision's `why` is saved as her thought (level hard,
kind `plan`). While a job waits or runs, the mouth's summary says she's working it out, so a reply
doesn't claim steps she doesn't have yet. Asking is a normal move, not a last resort (where is it,
do you have any, which way, can you help); a question goes through `agent_say` in any commentary
mode, like being stuck.

**Measured** with `scripts/mind_plan_eval.py` on the 2026-10-19 moments
(`scripts/mind_plan_moments.json`): bare hands at the iron → the full wood → wooden pickaxe → stone
→ stone pickaxe → iron chain 3/3; the plan on track with a failed step → stone pickaxe still before
iron 3/3; stuck on a water-blocked shore under the user's goal → a different approach, not waiting,
3/3. Not solved by planning: she never thinks of digging down for stone (an experience gap).

## Where thoughts live

A new table, `mind_thoughts`: user, game session, level, text, what was said, status
(`done`/`stale`/`unfinished`), topic (hard thoughts), created_at. Not `chat_messages` (history the
model reads stays what was actually said) and not the memory tables (thoughts aren't facts; later
reflection may use them as evidence). Kept 7 days. The chat page shows them in their own panel, dim
and italic with a level badge, loaded from `GET /api/mind/thoughts` and live over the chat socket
(`thought` frames, Redis channel `mind:thought:<user>`).

## How it compares to a person

Matches: reflexes (spinal reflexes), skills (motor habits), the gate (the brain's salience network),
react vs hard (System 1 / System 2), thinking while acting, lessons becoming habits.
Differences, and what they mean:
- People have no separate mouth mind; ours can confabulate. Hence the honesty rule.
- She sees in 1 s text snapshots, not continuously; the pack's reflexes cover the fastest moments.
- No feelings steer her attention yet (fear, curiosity); later, her character could tune the gate.
- Memory isn't woven into perception (recognising a place while looking at it); later.

## Settings

| Setting | Default | |
|---|---|---|
| `MIND_ENABLED` | false | the brain (needs `DESKTOP_AGENT_ENABLED`) |
| `MIND_GATE` | `jev` | `jev` (needs `TYPESAFE_API_KEY`) or `decisions` |
| `MIND_THINK_THRESHOLD` | 0.7 | how sure the gate must be before she thinks |
| `MIND_QUICK_MODEL` | gpt-6-luna | light thoughts |
| `MIND_REACT_MODEL` | gpt-4.1-mini | react thoughts |
| `MIND_QUIET_SECONDS` | 25 | a quiet thought after this long with no thought (0 = off) |
| `MIND_QUICK_GAP_SECONDS` | 4 | between quick thoughts (react ignores it) |
| `MIND_HARD_GAP_SECONDS` | 20 | between hard thoughts |
| `MIND_TICK_RECORDING_ENABLED` | false | also write every tick to `var/mind_ticks.jsonl` |
| `MIND_PLANNER_ENABLED` | false | the planner writes the plan (needs `MIND_ENABLED`) |
| `MIND_PLAN_MODEL` | gpt-6-luna | the planning call |
| `MIND_PLAN_REASONING_EFFORT` | low | its reasoning effort |

Every gate decision, thought and planning decision is also logged to `var/mind.jsonl` (level,
latency, why; planning: decision, outcome, steps), for tuning.

## Not yet

- The mouth speaking from the mind's record (thoughts, plan and why, what the hands did) instead of
  a summary, so it never claims what she didn't do (next, after planning).
- The mouth wording the brain's light lines (v1 lets the quick model word them).
- The brain outside games (conversation, idle, tasks), replacing the nudge rules in `salience.py`.
- Mood/character tuning the gate; recognising places from memory while playing.
