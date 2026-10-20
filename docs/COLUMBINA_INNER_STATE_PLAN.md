# Columbina's general mind and inner state

Status (2026-10-19): **steps 1-4 built** on `feature/general-mind`. Replaying 8,226 recorded ticks
through the old and new runtime with a scripted gate gives byte-identical decisions, and
`tests/test_mind_runtime.py` holds the lifecycle checks. Steps 5-6 are next.
This plan replaces the first draft of the same day. It took in an outside review:
- scoped state instead of one key per user;
- explicit domain planners;
- capabilities that say whether they accepted a command;
- scheduling and validity rules;
- a second source early, in shadow;
- lifecycle tests on top of replay.

Related: `docs/COLUMBINA_MIND_DESIGN.md` (thinking, mouth, hands), `docs/COLUMBINA_HARD_THINKING_PLAN.md`,
`docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md` (teacher/student, mood), `services/cognitive_live.py`.

## Why

There are two problems, and this plan solves both.

1. **Her mind only exists while Minecraft runs.** The runtime that gives her ongoing thinking is
   built around a live game session:
   - `services/mind.py` loops over live pack sessions and reads `PackClient` before thinking;
   - `services/mind_ticks.live_sessions` keeps only the game-pack profile;
   - `services/mind_hard.py` gathers game state and sends its conclusions through `desktop_commands`.

   When the game stops, the thinking stops too.
2. **No part of her carries a state from one moment to the next.** There's no mood, feeling or
   attention that persists. Every LLM call starts fresh.

The refactor gives her a mind that works across activities, and the inner state gives that mind
continuity across experiences. Build the narrow shared foundation first. Minecraft's skills, recipes,
reflexes and execution stay in its own integration.

## Coupling today

| Layer | Files | Coupling |
|---|---|---|
| Thinking prompts / LLM calls | `ai/mind/gates.py`, `think.py`, `hard.py` | Loose: they never name a game; Minecraft arrives as a `Situation` and as text |
| Planner prompt | `ai/mind/plan.py` | **Game-shaped**: its prompt talks about game-confirmed steps, item/block ids, playing together. It's Minecraft's domain planner, not a general one |
| Minecraft source | `ai/mind/tick.py`, `services/mind_ticks.py` | Minecraft by design |
| Mind runtime | `services/mind.py`, `services/mind_hard.py` | **Tight**: loops over `DesktopSession`s, reads the pack, game-only triggers, conclusions sent via `desktop_commands` |
| Hands | `ai/desktop/skill_driver.py`, `game_packs/minecraft` | Fully Minecraft |
| Mouth (reply) | `ai/chat.py`, `ai/prompts.py` | Loose; it reads her mind through the game summary (`desktop_game._mind_lines`) |
| Mouth (speaking first) | `services/agent_say.py` | Medium: general rules, but uses the game session's commentary mode |

## The five boundaries

| Boundary | What belongs there | Not there |
|---|---|---|
| **General mind** | Attention (what to think about, when, how hard), appraisal, mood, choosing problems, conclusions, coordinating memory | Recipes, skills, block ids |
| **Source** | Observations, changes, evidence, timestamps, context-specific urgency (pressure), its own problems and whether they still hold | Deciding how hard to think |
| **Body** | The controls it supports, current activity, accepting or refusing a command, execution, outcomes | Deciding what is worth doing |
| **Domain planner** | How to reach a goal with that body's real capabilities (Minecraft: `ai/mind/plan.py` + `services/game_planning.py`) | Mood, attention |
| **Communication** (mouth) | Replies and spontaneous speech, under the user's preferences | Deciding what she thinks |

The mind understands "my attempt failed and the goal matters to me". Minecraft supplies "mining failed
because I have the wrong pickaxe", plus the knowledge to fix it.

### Tools, bodies, sources

| Kind | Behaviour | Examples |
|---|---|---|
| Tool | One call, one answer, inside a mouth turn | Gmail, Drive, MacroVision sub-agents, `look_at_screen` |
| Body | Runs continuously with its own loop; it is **steered**, not called; reports back as events | Minecraft skill driver, VM key driver |
| Source | Produces moments and problems for the mind | Minecraft (`tick.py`); conversation (shadow) |

Her hands are not at the same level as the Google or MacroVision tools. The orchestrator-and-tools
design stays: the orchestrator *is* the mouth, and one-shot tools stay its tools. The rule to hold
is that talking with tools belongs to the mouth, acting continuously belongs to bodies, and deciding
belongs to thinking and the domain planner. No tool runs a body, and the mouth never writes the plan
itself.

## Contracts (`app/ai/mind/contracts.py`)

These are shaped by two real sources, Minecraft and conversation, and nothing speculative. Fields
are added only when one of those two needs them.

- **`Scope(user_id, source, activity)`**: what a piece of thinking is about. `activity` is the
  source's own id (a game session id, a conversation id).
- **Scoped state.** Mood and attention budget are shared per **user**. Thoughts, intentions, the
  open question, the mouth's hand-off and the hands' tips stay per **activity** and carry their
  anchor (goal, step, time). A thought about an old Minecraft goal must never steer a new session.
- **`Rhythm`**: `poll` (Minecraft: every second) or `event` (conversation: when a message
  arrives). The runtime serves both and never assumes a 1 s tick.
- **`Moment`**: what a source's tick hands the mind:
  - the text the gate and quick thoughts read;
  - pressure (`urgent`/`busy`/`calm`, decided by the source's code);
  - fresh lines (news);
  - whether its body can act;
  - its danger, which the source compares itself.
- **`Problem`** (from `ai/mind/hard.py`): what a hard thought is about, with an anchor. A problem
  may carry an `event` id so a duplicate event is ignored.
- **Validity**: every source answers "does this still hold?" for a problem, both while it waits
  and when the thought lands (`dropped`). Minecraft's answer is the game ended, the step is over,
  or the goal changed.
- **`Body`**: `controls()` lists what it supports (Minecraft: `tip`), and
  `command(scope, Command) -> CommandResult` returns `accepted`, `refused` or `unsupported`.
  "Careful" isn't required of every body, because it means different things in different
  activities. Routing records each result, so "I intend to", "it started" and "it finished" stay
  distinct.

## Scheduling (general runtime)

- **One hard thought at a time per user**, across all sources.
- **One waiting problem per activity.** A newer problem replaces that activity's waiting one, and the
  mouth's hand-off always wins. A noisy source can only replace its own slot, never queue more,
  so it can't take all her attention.
- **Choosing the next one:** by priority (hand-off, failing, surprise, big, open, quiet), then the
  source served least recently.
- **Pressure is per user.** If any of her activities is urgent, no hard thought starts. She doesn't
  ponder a chat question while a zombie hits her.
- **Gap:** `MIND_HARD_GAP_SECONDS` between hard thoughts (a hand-off skips it). Quick thoughts keep
  their per-activity gaps.
- **Shadow sources** get their own attention, so they can never delay or replace a live thought.

## Routing a conclusion

| Conclusion | Goes to |
|---|---|
| decision / guess with a tip | `Body.command(tip)` for that activity's body (result logged) |
| intention | That activity's intentions; her body reads them (Minecraft: `hands_messages`), and so does her mouth |
| question | That activity's open question: asked at a pause, or taken by her next reply, never both |
| all | Saved as a thought and shown in the panel; her mouth reads it as "on your mind" |
| shadow source | Only `var/mind_shadow.jsonl`: never saved, said, or sent to a body |

## Steps

| # | Step | Behaviour change |
|---|---|---|
| 1 | Contracts: source, body, command result, moment, scope, rhythm | None |
| 2 | Wrap the Minecraft paths behind them (`services/mind_minecraft.py`) | None |
| 3 | A minimal **conversation source in shadow**: accept a message, find a problem worth revisiting (a question of theirs still open after her reply), produce a thought, with no `DesktopSession`. Flag `MIND_CONVERSATION_SHADOW_ENABLED`, off | None (shadow only, log only) |
| 4 | Scheduling and conclusion routing move into the general runtime (`services/mind.py`, `services/mind_hard.py`) | None for Minecraft |
| 5 | Shared inner state and appraisal in that runtime (below) | Behind a flag, mind prompts only |
| 6 | Connect mood to chat and actions, one piece at a time | After the live `reading` vs `page_only` A/B has an answer |

Not in this refactor: generic body tools for the mouth (the Minecraft game tools stay as they are),
live unsolicited thinking outside a game, and a migration that gives `mind_thoughts` a
source/activity column (today `session_id` is a foreign key to `desktop_sessions`).

## Checks

- **Behaviour:** the existing mind, hard-thinking, tick and planner tests keep their assertions.
  Replaying recorded ticks (`var/mind_ticks.jsonl`) gives the same text.
- **Lifecycle tests** (replay can't show these):
  - a source stops while a thought runs: it's dropped;
  - the goal changes mid-thought: the thought is cancelled, and the waiting one is discarded;
  - a duplicate event: one thought;
  - two sources compete for attention: priority first, then fairness, and the noisy one can't starve the other;
  - urgent pressure in one activity: hard thinking is held for all;
  - a shadow source never touches live attention, thoughts, mouth or body.

## Step 5: inner state

**One inner state per user, read by every part, updated by events.** Student 1 is one of the things
that updates it. We don't add its prediction to each LLM call separately.

| Field | Range |
|---|---|
| mood, closeness | -1..1 |
| energy, interest, tension | 0..1 |
| recent feelings (cause, time) | last ~5, fading |

- **Baseline and reactivity** come from `docs/characters/columbina.mood.json`, the same file the
  teacher uses.
- **Drift is by elapsed time**, not per turn: the mind ticks every second, and chat turns can be
  hours apart.
- **Updates:**
  - game events go through code rules first (hurt raises tension, death lowers mood and raises
    tension, goal done raises mood, a gift raises closeness);
  - user messages go through Student 1's reading, at reduced weight until its feelings are validated;
  - her own feeling thoughts give a small, capped push, so she can't spiral.
- **Reaches:** one plain line in the quick-thought, hard-thought and planner prompts ("How you are
  right now: a bit tense, we nearly died; feeling close to them"). Code knobs come one at a time,
  each with its own setting:
  - interest raises how often she thinks;
  - low energy means more quiet;
  - tension turns on careful mode where the body supports it;
  - closeness sets how chatty she is.

**How we'll know:** the logged state moves with events and returns to baseline. Matched contrasts
work: the same moment, calm vs tense, shifts her thoughts that way. No value stays pinned at an
extreme. Live, she feels like the same person in the game and in chat.

## Rules kept

- No new response gates or character-page patches.
- Everything new is off by default. A failure never breaks a turn or a tick.
- Fixes are general, not tuned to a scenario.

## Open decisions

- Drift speeds per value, in minutes.
- How much Student 1's experimental feelings weigh before they're validated.
- Whether thoughts that come from her mood may push her mood at all.
