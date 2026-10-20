# Columbina's general mind and inner state

Status (2026-10-19): **steps 1-4 built** on `feature/general-mind`. Replaying 8,226 recorded ticks
through the old and new runtime with a scripted gate gives byte-identical decisions, and
`tests/test_mind_runtime.py` holds the lifecycle checks. Step 5 (inner state) is in design: it starts
with two offline tests (5.0, 5.1) before anything is built. Step 6 follows.
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
- **Choosing the next one:** the mouth's hand-off first. After that, trigger priority (failing,
  surprise, big/open, quiet), and a waiting problem gains one level per `AGING_SECONDS` (60 s), so a
  source that keeps finding urgent-looking problems can't starve another. Ties go to the source served
  least recently.
- **Event work wakes itself.** A source no tick comes back for (event rhythm, live or shadow) starts
  its waiting problem after the gap on a timer, and again when the running thought lands. Every change
  to attention goes through one lock, because ticks, events, timers and landing thoughts run on
  different threads.
- **The mind runs without a game.** `MIND_ENABLED` alone starts the mind worker. Each source has its
  own switch (Minecraft also needs desktop mode, `mind_minecraft.enabled()`).
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

Status (2026-10-19): **5.0 and 5.1 done (offline); 5.2 not built.** Results in
`docs/qa/inner-state-2026-10-19/RESULTS.md`. In short: interest changes her quick thoughts (with the
guided wording); tension changes nothing; the proposed state behaves; the teacher's reading leans
positive and echoes the mood it's given, so it has to be fixed before mood drives anything visible.

### Where mood stands today

| Place | Mood | Why |
|---|---|---|
| Teacher runs (`Cognitive/simple/episode.py`, `intention.py`, `ablation.py`, `real.py`) | **Dynamic**: starts from a named mood (`START_MOODS`: calm, tense, tired, excited, low) and moves each turn (`mood.update`, from `columbina.mood.json`) | To generate varied training data |
| Teacher mood replays | The same turn with the mood swapped (`REPLAY_SWAP`, e.g. calm ↔ tense) | For tests only: does the result differ when only the mood differs? |
| Live chat (`services/cognitive_live.py`, Student 1's input) | **Frozen** at `START_MOODS['calm']` on every turn | Student 1's readings and notes already affect chat in the live A/B. A moving mood input would change that experiment mid-run |
| Her mind (quick thoughts, hard thoughts, planner) | **None** | Not built |

Frozen is a temporary choice, not the design. Mood becomes dynamic in live use once the checks below pass.

### Evidence so far (why we test before building)

- Tension probe (identical inputs, only tension changed): the teacher's output did not change, even on blunt messages.
- The students did not learn mood replays (about chance).
- The teacher's emotion labels followed energy more than tension.

If prompts ignore the mood line, a mood engine feeds nothing. So the order is test first, build second.

### Order

| # | What | Builds | Decides |
|---|---|---|---|
| 5.0 | **Mood-line probe** on the mind prompts (quick, hard, planner): the same moments, only the mood line changed (`scripts/mind_mood_probe.py`). The reply prompt was already probed by the teacher tension probe | A script, offline | **Done.** Interest moves quick thoughts (guided wording +0.46/+0.53); tension and the hard/plan stages show nothing |
| 5.1 | **Offline replay**: the teacher's reading over teacher runs and stored conversations, three mood tracks (`scripts/mood_replay.py`) | A script, offline | **Done.** `time_targets` never pins and settles over gaps; the reading leans positive and echoes its mood input. Half-lives can't be pinned from this data |
| 5.1b | **Where the reading breaks** (checks A-D: observer, context, thought vs labels, connected conversations; `scripts/reading_boundaries.py`) | Script, offline | **Done.** Recognition and personality mostly work; the labels describe a state, not a change; extraction loses "unsure" and copies "tired" from the mood input; fair and unfair complaints read the same; the simulated users were rarely actually cold |
| 5.1c | **Labels `appraisal-v1`** (what they did / what she thinks they meant / her part / what changed in her, causal and quoted / what she shows; "now" only as a diagnostic) and the pure **inner-state updater** (`app/ai/mind/inner_state.py`), re-checked with A-D and 1-vs-10 neutral messages (`scripts/appraisal_v1_eval.py`) | New label version + pure module, offline | **Done.** Lingering hurt is "continuing" and adds nothing; an apology takes back its own incident; tension is the same after 1 or 10 neutral messages; her part separates her failure from an unfair complaint, and apologies follow it; "tired" no longer copied. Open: positive feelings pile up (calibration), "unsure" still lost |
| 5.1d | **What counts as a change, then its size.** Parts 1-2 done: labels `appraisal-v2` (events vs incidents; opened / escalated / eased / sustained / lingering; shift size, may be unclear; uneasy vs uncertain), the updater following them, and evaluation both ways (shift grader, support judge, 5 development scenarios + held-out sets; `scripts/appraisal_v2_eval.py`) | Labels + updater, offline | Pile-up gone (happy adds 202 → 64 of 540; pinned mood 25% → 6%), dismissal builds, repair eases. **Overcorrected**: incidents too broad, so new reasons become "sustained" (status supported 51%). Then `appraisal-v3` (incident identity apart from the change: shift up/down/none, cause active/over/recalled; code derives the status; `scripts/appraisal_v3_eval.py`): shift decision agrees with the support judge 83% (misses more than it invents); negatives, lingering and repair work; incident matching 58% right (repair depends on it); sizes over-claimed as "clear"; positive accumulation remains (21% pinned): real small shifts added in full. Then step 3: labels v3.1 (sizes only when shown: unsupported sizes 40% → 23%; incident matching still 58%), the layered model `app/ai/mind/mood_layers.py` (reaction / incident / mood, each incident with its own reaction; soft limit; larger retained share for negative feelings, a chosen character parameter), calibrated on development data (`scripts/mood_calibrate.py`: flat 2/8, layered 6/8 after two correctness fixes) and frozen in `docs/characters/columbina.inner_state.json`; fresh evaluation `scripts/mood_fresh_eval.py`: **the bar is met on fresh conversations** (ordinary stays stable, events matter, apology eases, repeats add nothing; never at the limit). Gaps: no "sad" feeling, size uncertainty ±0.1-0.15, repair depends on incident matching. Next (approved): commit; live wiring with persistence and a debug view, no influence; then limited influence on quick thoughts or tone behind its own switch |
| 5.2 | **The live state service**, behind `MIND_INNER_STATE_ENABLED`, off by default, **no influence** | `services/inner_state_live.py` (background reading after each reply, Redis per user with a WATCH transaction, incidents per conversation under a lock, `var/inner_state.jsonl`), `GET /api/mind/state`, `MoodStrip` in the chat page | **Built (2026-10-20).** Verified end to end on a throwaway Redis with real model calls. Next: watch real sessions; then limited influence on quick thoughts or tone behind its own switch |
| 5.3 | **Student 1 shadow comparison**: Student 1 run a second time with her real mood; only the difference is logged | Logging | How much mood changes its reading. Its live input stays frozen |
| 5.4 | **Second source adapter** (Minecraft), through the same impulse contract | Adapter | That the state is general, not built around one source |

### Design for 5.2 (if approved)

Changed by 5.0/5.1/5.1b: the impulse is what a message **changed** in her, judged from her thought, not
her resulting state (which would count lingering feelings again) and never copied from the mood input.
Her current mood may still shape how she reads things. No value is dropped: interest is the first one shown to prompts (guided wording); the others stay
unshown until each has a defined meaning and evidence that changes it (tension's null was on "sounds
tense", not on attention or choices; energy needs a real source); closeness becomes a per-user
relationship value, but friendly exchanges alone mustn't count as growth.

**Conversation is the first source.** It is her main experience. Minecraft is only one later adapter;
the state itself knows nothing about any source.

| Input | Moves the state? | Notes |
|---|---|---|
| Conversation: after each reply, the teacher's Understand + Feel reading of the exchange | Yes | One call, off the reply path. It names only the values the exchange touched; the evidence is the message id |
| Student 1 on the same message | No, logged beside the teacher's reading | Free validation data: how often does Student 1 agree with the teacher on real messages? Its weight stays 0 until it passes the probes |
| Her own feeling thoughts | **No (read-only)** | Thoughts express or interpret her mood; a cap limits a spiral but adds no new evidence. Later: reappraisal tied to an existing cause ("we sorted it out, so it matters less now"), which allows recovery as well as escalation |
| Time | Yes, each value fades toward her baseline | Closed form, so the result is the same at any polling rate |
| Other sources (later) | Through their own adapter | The adapter judges its own events in context (dying on purpose ≠ losing valuable gear) and sends an impulse plus evidence. Source-specific interpretation stays in the adapter |

**Impulse contract:** `event_id, source, activity_id, occurred_at, targets, weight, cause, evidence_ref`.
- `targets` holds only the values the event affects. `feeling_pull()` gives all five values, so it is not reused wholesale: being hurt must not also change closeness or interest.
- An ongoing condition (a long silence, sustained danger) is sent once, with an incident id, not once per tick. Overlapping signals about one incident (hurt, near death, death) share the incident id, and only the strongest counts.

**Updates are atomic across processes.** One Redis Lua script does it all in one step: check the
dedupe set, fade the state to now, apply the impulse, bump the version. A Python lock is not enough
(the chat worker and the mind worker are separate threads or processes).
- **Late events:** an impulse is weakened by its age, the same way the state fades, and dropped (and logged) past about two half-lives.
- **Traceable:** every thinking call records the state version it read. Every change is logged to `var/inner_state.jsonl`.

**Reaches (5.2 only):** one plain line in the quick-thought, hard-thought and planner prompts ("How you
are right now: a bit tense; interested in what they said"). No chat prompt changes directly. Chat is
still affected **indirectly**, because chat reads her thoughts.

**Visible:** a small strip in the thoughts panel (`ThoughtStream`) with the values in words, the last
few causes and when each happened, served by `GET /api/mind/state`.

### Tests (5.2)

- A duplicate event applies once.
- Values an event doesn't target only fade as usual.
- Concurrent updates are both kept.
- Fading gives the same result at any polling rate.
- Without new supporting events, every value returns to baseline. Sustained support (ongoing danger) may keep a value high; that's allowed.
- A shadow source can't change the state.
- The calm vs. tense contrast from 5.0, repeated live: the same moment shifts her thoughts the expected way.

### Doubts to settle before 5.2

1. **Mood reaches little live outside a game.** Thinking outside a game is shadow only, and 5.2 changes
   no chat prompt. With conversation as the only source, mood shows in the panel and logs, not yet in
   what she does. Live thinking in conversation is needed before mood can mean much.
2. **Closeness is a relationship, not a mood.** It changes over days and belongs to one user. Mood,
   tension, energy and interest change over minutes and belong to her. That depends on the open
   question below: is Columbina one person across users, or one per user?
3. **Energy is shaky for her.** Nothing in a conversation really tires her, so "tired" would mostly be a
   teacher guess. Either derive it from facts (time of day, how long you've been talking) or leave it out.
4. **Interest is probably the most useful value**: it maps to existing behaviour (how often she thinks,
   following or dropping a topic). If we start small: interest first, then tension.
5. Atomic updates are needed but cheap: about one update per message, so the Lua script stays small.

### What changes later (not in step 5)

| Later | What changes | Gate |
|---|---|---|
| Live chat mood unfrozen | Student 1 (and the reply) read her real mood instead of "calm" | The `reading` vs `page_only` A/B has an answer, and 5.3 shows how much mood moves the reading |
| Student 1 gets weight | Its feelings move her mood live, at reduced weight first | It agrees with the teacher on real messages and passes the probes (today it misreads "useless", alarm and corrections) |
| Reappraisal | A thought may lower or raise an existing feeling, but only by citing its cause | Thoughts are read-only in step 5 |
| Code knobs, one at a time, each with its own setting | Interest raises how often she thinks; low energy means more quiet; tension turns on careful behaviour where a body supports it; closeness sets how chatty she is | The calm vs. tense contrast works for that prompt |
| Live thinking outside a game | The conversation source leaves shadow, so mood reaches what she does in chat | Separate decision (not in this refactor) |
| Mood in chat replies (step 6) | The reply prompt gets the mood line | Step 6 |
| Persistence beyond Redis | A Postgres table, so mood survives a Redis wipe and spans days | If the experiment is kept |
| Opinions from reactions (L4 below) | Her taste shifts with what she lived | Inner state exists and is trusted |

**How we'll know overall:** the logged state moves with events and returns to baseline. Matched contrasts
work: the same moment, calm vs tense, shifts her thoughts that way. No value stays pinned at an
extreme without continued support. Live, she feels like the same person in the game and in chat.

## Learning from experience: beliefs, opinions, experiences, how she talks

Status: **design for discussion, not approved.** It doesn't touch core memory: her character (values,
principles, traits) changes only through a reviewed core version, as it does today.

### What changes her today

| You do | Today | Changes her? |
|---|---|---|
| Say "he's not a bad guy" | The fact-saver (`long_term_memory._EXTRACTION_PROMPT`) may store **your** view as a fact about you, or nothing (it doesn't infer relationships). If it's stored, it's recalled when he comes up | No: she has no view of him herself |
| Share a conversation log or a novel | She can read it within one turn (Drive). Nothing stays except facts from your own messages | No |
| Play Minecraft | Experience, then a reflection, then lessons with evidence (two sessions to confirm), learned moves, and shared recaps | Yes, but only her game skill |

The live prompt already has a slot for this, and it's empty: "Your own experiences: none recorded yet"
(`cognitive_live.typed_memory_text`).

### Four things she can learn, kept apart

| Kind | What it is | Example | Comes from |
|---|---|---|---|
| **Belief** | What she thinks is true, about a person, thing or the world | "Sam is kinder than he looks" | What she's told (testimony), what she sees, what she reads |
| **Opinion** | How she feels about something: likes, dislikes, takes | "I like slow-burn stories" | Her own reaction to an experience |
| **Experience** | Something that happened **to her**, from her point of view | "I read chapter 3; the betrayal annoyed me" | Her mind's thoughts while she does or reads something |
| **Way of talking** | Phrases, rhythm and humour she picks up | Shorter sentences, a running joke | Examples from conversations and reading |

They're kept apart because each answers to evidence differently. A belief can be wrong and be corrected. An
opinion isn't wrong; it just shifts. An experience is a fixed record of what happened. A way of talking is
only style.

### Beliefs and opinions: held, sourced, revisable

- **A belief is her own record**, not your fact. Each one stores what it's about (a person, thing or
  topic), the claim, how sure she is, and its **evidence**: each piece with its source (you said, she saw,
  she read), when, and a link to the message, experience or passage.
- **Your claim is testimony.** "He's not a bad guy" moves her belief about him partway, weighted by how
  much she trusts you on this, and it's recorded as "you told me". If she later sees him lie, that's
  evidence too, and both stay visible. She can say honestly: "you said he's fine, but what happened
  last time made me unsure."
- **Revision follows the same rule as game lessons:** a model's guess never becomes truth without
  evidence. One mention makes a tentative belief. Evidence from separate occasions makes it firm.
  Enough contradiction turns it into "I'm not sure any more". It never silently flips.
- **Opinions** come from her reactions (feeling thoughts, and her inner state once it exists). They shift a
  little with each experience. Her values in core memory still shape how she reacts, but nothing writes
  back to core.
- **Who writes them:** her slow thinking (hard thoughts). A conclusion can be "I now think…", citing
  what it rests on, and code checks that the cited evidence exists, as reflection checks `e`/`u` refs.
  The fact-saver stays about you and never writes her beliefs.

### Experiences: reading as a source of her mind

A document or novel becomes one more **source** in the general mind (`contracts.Source`, event
rhythm). The material is an activity, and she goes through it in pieces (chapters, conversation chunks):
- each piece is a moment; what surprises her or keeps coming up is a problem;
- hard thoughts conclude feelings, guesses and opinions about it;
- the end of a piece writes **her own** episodic memory ("I read…; it made me think…"), kept separate from
  shared memories with you and from facts about you.

That's what fills the "Your own experiences" section. It needs a field on episodic memory that says
whose experience it is: hers, shared, or yours.

**Fiction is labelled as fiction.** A belief from a novel is about the story ("in the book, Ana lies to
protect her brother"), never about the real world, unless she reasons her way there and says so.

### How she talks: examples, not rewriting

- From conversations and reading, keep short **examples** of ways of talking she liked, each with
  where it came from and what it suits (teasing, comforting, telling a story). Her mouth gets a few
  that match the moment, as "ways you've liked saying things", the same way memories are retrieved.
- Nothing is copied wholesale. Her character page and voice rules stay the frame; examples only colour it.
- **Later, offline:** if a style should become stable, the examples are data for the teacher and
  students. That's a reviewed retraining, never something that happens live.

### Safety and control

- She lists what she believes about someone, and why, on request, and on a page like Learning.
- You can correct or delete any belief, opinion, experience or style example.
- Beliefs about real third parties stay private to that user's memories and are never shared across
  users.
- One source can't dominate: there's a cap on how much a single conversation, document or person can move
  a belief, so one persuasive novel or one insistent message can't rewrite her view.

### Order, if approved

| Step | What | Shows that |
|---|---|---|
| L1 | Her own experiences: an owner field on episodic memory; hard-thought conclusions write experiences; the empty prompt section fills | She remembers things as hers |
| L2 | Beliefs with evidence: a table, hard thoughts propose, code checks evidence, firm/unsure rules, shown in her prompt when the subject comes up | "You said…, but I saw…" |
| L3 | Reading as a source: a document or novel in pieces through the general mind, writing experiences and beliefs labelled as fiction | She has read something and has thoughts about it |
| L4 | Opinions from reactions (with inner state, step 5) | Her taste moves with what she lived |
| L5 | Ways of talking: kept examples, retrieved for her mouth | Style that grows, inside her character |

### Measuring

- **Matched contrasts:** the same question about a person, before and after one piece of testimony,
  after contradicting evidence, and with no evidence. Her answer should move in that direction and say why.
- **Source honesty:** every belief in a reply can be traced to stored evidence, and nothing appears
  with none.
- **Fiction boundary:** after reading a novel, questions about the real world are unchanged.
- **Stability:** one message or one document moves a belief only up to the cap.

### Open decisions

- What trust weights your testimony gets, and whether trust is per topic.
- What counts as "separate occasions" outside games (different days? different conversations?).
- How long-lived opinions are: do they fade without new experience?
- Where you review and correct these: the Learning page or a new one.

## Rules kept

- No new response gates or character-page patches.
- Everything new is off by default. A failure never breaks a turn or a tick.
- Fixes are general, not tuned to a scenario.

## Open decisions

- Drift speeds per value, in minutes. Proposed starting points: tension ~10 min, mood ~30 min, energy and
  interest ~60 min, closeness ~1 day; 5.1 replaces them with measured ones.
- ~~How much Student 1's experimental feelings weigh before they're validated.~~ Agreed: 0 (log only)
  until it passes the probes.
- ~~Whether thoughts that come from her mood may push her mood at all.~~ Agreed: no, read-only in step 5;
  reappraisal tied to a cause later.
- Is Columbina one person across all users (mood shared, closeness per user) or one per user (all values
  per user)?
- Which values to keep at first: all five, or interest and tension only.
- Minecraft adapter in step 5 (5.4) or after.
