# Simple cognition + teacher environment: plan for review (2026-10-18)

Status: **built 2026-10-18; pilot data generated; not audited.** See
[Implementation status](#implementation-status-2026-10-18). The open questions at the end use the
proposed defaults until changed. The current v0/v1 cognitive pipeline
(`backend/app/ai/Cognitive/system/v0`) stays as it is; this is built next to it. (Update 2026-10-22: v0/v1 is
retired and removed, see `LEGACY_CLEANUP_2026-10-22.md`; this chain is the only cognition.)

A review on 2026-10-18 raised nine implementation concerns. Their resolutions are folded into
the sections below and summarised in [Review concerns and resolutions](#review-concerns-and-resolutions-2026-10-18).

Goal, in priority order:
1. **Human-like behaviour, with Columbina as the personality.**
2. Fast enough to run on every message: small local **students** learn from a slow **teacher**.
3. Simple enough that a person can judge every label by eye.

## Why

Findings from the 2026-10-18 conditioning tests (`docs/qa/cognitive-conditioning-2026-10-18/REPORT.md`,
about 2,000 real-model runs):

- **Personality numbers don't steer an LLM.** Bias values changed Call 1's readings in 10 of 72
  expectations, whether the model or code applied them.
- **A written character page does.** Warm vs. guarded pages on the same messages gave different,
  page-consistent behaviour (11 of 13); plain questions were unchanged.
- **Mood reaches both calls as numbers, with almost no meaning.** One general sentence in Call 1,
  nothing in Call 2. Strained mood collapsed every choice to "acknowledge".
- **Nothing records how a message makes her feel**, so being brushed off never registers.
- **Repair and apology can never be offered.** They need records of her earlier turn and of a bad
  outcome, and live capture always omits `prior_action_records`, `prior_realization_records`
  and `action_observed_outcomes` (`live_shadow/models.py`).
- **The chain is too fine to learn.** About 36,000 lines; 53 action candidates, of which 10 were
  ever chosen (acknowledge 1,372 times, apology 0). Rare labels never get enough examples, and
  labels like `explore_reason` vs. `request_evidence` can't be judged by eye.

## The simple chain

```
message ─► 1. Notice (code) ─► 2. Understand ─► 3. Feel ─► 4. Mood (code)
        ─► 5. What this turn needs (code) ─► 6. Decide ─► 7. Say it (LLM)

inputs to every stage: her character (page for the teacher, weights for the student),
conversation, memories, time of day, affinity (reserved, always neutral for now)
```

| # | Stage | The human question | Who | Output |
|---|---|---|---|---|
| 1 | Notice | What did they literally do? | code | cheap facts (below) |
| 2 | Understand | What do they mean? | **student 1** | acts, tone, about her, how sure, note updates |
| 3 | Feel | How does it land for her? | **student 1** | 4 appraisal numbers + 8 feelings |
| 4 | Mood | How is she overall right now? | code | 5 mood values |
| 5 | What this turn needs | What's expected of her? | code | a few flags |
| 6 | Decide | What does she do, and how? | **student 2** | 1 or 2 of 20 groups + 4 dials |
| 7 | Say it | The words | LLM | her reply |

### 1. Notice (code)

- question? request? mentions her / addressed to her?
- laughing, emoji, exclamation
- message length; seconds since her last message; time of day (user's zone)

Reuses perception's question/request detectors where they fit. Meaning comes from the text
encoder, not from rules.

### 2. Understand (student 1)

| Output | Values |
|---|---|
| **What they're doing** (each yes/no; several can hold) | asking, sharing, requesting, teasing, complaining, correcting her, small talk, ending |
| **Their tone** (exactly one) | friendly, neutral, playful, cold, upset |
| **About her?** | 0-1 |
| **How sure she is** of her reading | 0-1 |
| **Note updates** (per open note) | this message answers it / accepts it / completes it / drops it / none (see conversation notes) |

Acts overlap ("haha you're useless" is teasing *and* complaining), so each is its own yes/no.
Tone is a single choice.

### 3. Feel (student 1)

| Output | Values |
|---|---|
| **How it lands** (4 numbers) | good or bad for her (-1..1), warm or cold toward her (-1..1), surprising (0..1), important (0..1) |
| **Her feelings** (strength 0-1 each) | happy, amused, curious, touched, unsure, hurt, annoyed, tired |

The feeling slot is new: it's what lets a brush-off actually register.

### 4. Mood (code)

Five values: **mood** (bad..good), **energy**, **interest**, **tension**, **closeness in this chat**.

- Each turn, mood moves toward what she felt in stage 3, then drifts back to her baseline over time.
- Her character sets the baseline and how fast she moves (a few numbers next to her page).
- Plain code, so you can read it, and the game mind can later share the same mood.
- Starting point: the existing `dynamic_state` updater, reduced. Dropped from today's 8: goal
  pressure, epistemic confidence (curiosity and engagement merge into interest).

### 5. What this turn needs (code)

A few flags instead of the goal engine and requirement tiers:

| Flag | Set when |
|---|---|
| `must_respond` | they asked her something |
| `yes_no_needed` | they asked her to do something |
| `something_went_wrong` | they corrected her or said she got something wrong (needs her last turn recorded) |
| `her_open_question` | she asked something earlier that hasn't been answered |
| `cant_do` | the request needs something she doesn't have (capability, permission, safety) |

Her own longer-running wants (intentions from her mind's thinking) come later.

### 6. Decide (student 2)

A main move from these groups, plus optionally a second (no second move is valid; zero moves is
not, because in chat she always replies):

| # | Group | Covers today's actions |
|---|---|---|
| 1 | greet | greet |
| 2 | say bye | close |
| 3 | react briefly | acknowledge, indicate_attention |
| 4 | answer / explain | answer, explain, inform, elaborate, exemplify, summarize, justify |
| 5 | ask about it | explore_experience, explore_reason, explore_preference, explore_goal, inquire |
| 6 | check what they meant | clarify, disambiguate, check_understanding, verify |
| 7 | show she understands | reflect, validate, paraphrase, confirm_understanding |
| 8 | comfort / reassure | comfort, reassure, express_sympathy, encourage |
| 9 | celebrate / thank | congratulate, appreciate, thank |
| 10 | play | joke, banter, light_tease, play_along, playful_reframe |
| 11 | share her view | express_preference, evaluate, express_uncertainty |
| 12 | agree | agree, partially_agree |
| 13 | push back | disagree, challenge |
| 14 | apologise / fix it | apologize, repair, self_correct |
| 15 | say yes / commit | accept_request, accept_suggestion, promise |
| 16 | say no / set a limit | decline_request, decline_suggestion, set_boundary |
| 17 | suggest / offer | offer, propose_plan, advise |
| 18 | change topic | shift_topic, return_to_topic, introduce_topic |
| 19 | **share about herself** (new) | none today |
| 20 | **say how she feels** (new) | none today |

**Dials (0-1):** length, warmth, playfulness, directness.

- **A group is a decision; the choice inside it is style.** Joke vs. tease, comfort vs. encourage
  are left to the wording, guided by the dials; the critic checks the reply matches the group.
  Groups that would hide opposite decisions are split (greet / say bye, agree / push back).
- **"Say yes / commit" means she agreed, never that something happened.** Whether a thing was
  actually done is a fact from tools or the game, not a move, and is never a label.
- Code removes impossible groups before the student scores them (`cant_do` → no "say yes";
  nothing went wrong → no "apologise"). The student never decides safety.

### 7. Say it (LLM)

The wording model gets the decision in plain words ("she's a little hurt; main move: ask about
it; then share how she feels; short, warm, not playful"), plus the conversation, her page and
memories.

## The teacher environment

Offline, slow is fine. It produces the training data for both students.

```
Scenario maker ──► conversation episode (8-12 turns)
                    ┌──────────────────────────────────────┐
                    │ simulated user ──message──► teacher  │
                    │       ▲                      │       │
                    │       └──── her reply ◄──────┘       │
                    └──────────────────────────────────────┘
                                   │ every turn
                                   ▼
                           recorder → dataset
```

### Scenario maker (once per conversation)

Spread out on purpose, so every combination appears:

| Parameter | Values |
|---|---|
| User type | a written card each, mixed and varied (see below) |
| Situation to include | greeting, small talk, a question, a tease, a compliment, a brush-off, a correction, a request she can't do, a topic change, something ambiguous; placed at a random turn |
| Her starting mood | bands of the 5 mood values: calm, tense, tired, excited, low |
| Affinity | reserved: "you don't know them well yet" |
| Memories | none, or a few facts about the user from a small pool, stored so live retrieval finds them; some scenarios are built so a memory should change the reading ("I'm fine" after a known loss) |
| Time of day | morning, afternoon, late night |

**User types.** Each card is a person, not just a mood: a name, age range, how they write
(long or short, typos, emoji, slang, all lowercase), what they want from talking to her, and
their mood today. Kinds to cover:

| Group | Types |
|---|---|
| Warm | friendly regular, playful teaser, flirty, excited sharer, grateful, affectionate old friend |
| Cool | curt / busy, bored, distracted (replies late, half-reads), shy, formal stranger, sceptic who doubts she's "real" |
| Hard | rude, sarcastic, passive-aggressive, testing her ("prove you have feelings"), trying to break her (contradictions, trick questions), boundary-pusher, someone who keeps changing the topic |
| Low | sad, lonely, stressed, venting (wants to be heard, not fixed), grieving, anxious overthinker |
| Thinking | curious about her, debater who disagrees, philosophical, confused (unclear messages), forgetful (repeats themselves) |
| Practical | wants help with something, asks for things she can't do, impatient asker |
| Mixed | starts friendly then gets annoyed, starts cold then warms up, jokes while actually upset |

The "mixed" cards matter most: they make mood and reading change *within* one conversation.

### Simulated user (1 call per turn)

- Plays its card and reacts to what she actually said.
- Brings in the scenario's situation at its turn.
- Writes a hidden note, **"how did that land for me"** (-1..1 plus a line). It isn't shown to her;
  it's the reaction score for later learning.

### Teacher, per turn

| Step | Who | What |
|---|---|---|
| 1. Prepare | code | Notice; mood and affinity into words ("she's a bit tense and tired") |
| 2. **Think as her** | LLM, reasoning on | Free inner thought: what she notices, what they might mean, how it lands, how she feels, what she wants to do and why. Reads her page, the turn's input bundle rendered as text, and the **full conversation** so far (see "Same information for teacher and student") |
| 3. Label stages 2-3 | LLM | Turns the thought into Understand + Feel outputs |
| 4. Code stages | code | Mood update, turn flags, impossible groups removed |
| 5. Label stage 6 | LLM | Groups and dials that match what the thought wanted, plus **"wanted but not available"**, plus **"used older messages"**: which messages before the student's 6-message window shaped these labels, and what from them ("message 3: she promised to remind him") |
| 6. Critic | separate LLM | Reviews **every sample**: is this her, per the page? Would a person do this here? Do the labels follow from the inputs? A rejected sample is **regenerated whole** from step 2 (at most twice), never patched part-way |
| 7. Say it | LLM | Her reply from the decision; goes to the simulated user |

- **Think and label are separate calls.** The thinking is in plain words, the way a person thinks;
  labelling only translates. One call that thinks and fills a form is what made her stiff before.
- **The critic checks "in character" and "human-like" only, never "nice".** Example: to "ugh you're
  useless today", her page says she gets a little hurt and assumes a bad day, so "hmm… that stung a
  little. Bad day?" passes. A critic that also judged politeness would reject it until she said
  "I'm sorry, how can I help?", and every student trained on that data would come out a bland
  assistant. Hurt, teasing, disagreeing and short replies are allowed whenever they fit her page.
- **The critic judges labels against the inputs, not against the thought.** It reads the same
  input bundle (conversation notes, memories, state), the full conversation and her page, and
  checks that "used older messages" is complete. The thought
  text is a generated explanation to inspect, not ground truth: labels that match the thought but
  not the inputs fail, and so does a thought that claims something the inputs don't contain.
- **Five samples, each a complete chain.** Steps 2-6 run **5 times** on the same input. A sample
  is one whole chain: its thought, its Understand/Feel labels, the mood and flags code computed
  from them, and its decision. **Each accepted sample is its own training example; nothing is
  averaged across samples**, so a decision always stays with the understanding and mood that
  produced it. Training on the separate samples still teaches probabilities: if "teasing" holds
  in 3 of 5, the student learns about 0.6 by itself.
- **Rejections:** rejected attempts are kept, marked rejected (not trained on for now). A sample
  still rejected after two retries is dropped; a turn with fewer than 3 accepted samples is
  flagged noisy.
- **Continuing the conversation:** one **accepted** sample, picked at random, continues it. Its
  mood, notes and labels become the next turn's state and previous-turn labels; its reply goes to
  the simulated user. Episodes branch naturally instead of always taking her most likely path.

### What the teacher outputs, and how it becomes numbers

| For | Cues | Size |
|---|---|---|
| Student 1 | acts (8 yes/no), tone (1 of 5), about her, how sure, how it lands (4), feelings (8), note updates (per open note, 1 of 5) | 27 + up to 6 note slots |
| Student 2 | main move (1 of 20), second move (1 of 20 or none), dials (4) | 45 |
| Checking only | free thought, wanted but not available, used older messages, critic verdict | not trained on |

The teacher **never writes decimals** (LLMs are poorly calibrated with raw numbers). Three
different quantities are kept apart:
- **Intensity** (how hurt, how warm a reply): a fixed scale, *none / a little / somewhat /
  strong / very* → 0 / 0.25 / 0.5 / 0.75 / 1. **Signed** values (good/bad, warm/cold): *very bad /
  bad / neutral / good / very good* → -1..1. Each scale has written anchors per step, per cue, in
  the teacher prompt, e.g. hurt "a little" = it stings and passes; "strong" = it stays with her
  for the next turns; length "a little" = a few words, "very" = several sentences.
- **Her certainty** ("how sure she is" of her reading): its own cue on the same scale, with anchors
  ("somewhat" = she'd check if it mattered).
- **Frequency** across samples: not a label. It comes from training on the 5 separate samples,
  and the dataset never averages intensity into frequency or the reverse.

Example sample, "ugh you're useless today" from a usually friendly user: complaining · cold ·
about her strong · sure somewhat · bad for her · a bit cold toward her · hurt somewhat, unsure a
little · main "say how she feels", second "ask about it" · short, warm, not playful, somewhat
direct.

### Mood replays

For some turns, re-run the teacher on the **same message and history** with only the mood
changed (calm vs. tense, tired vs. excited). These are matched-input contrasts: they show the
student directly what mood does, which ordinary conversations rarely isolate. Affinity gets the
same treatment once it exists.

### Real conversations

Also run the teacher on real chats from the shadow traces, so the data isn't only simulated.
**Shadow only:** the teacher reads stored copies offline, after the fact. It never runs during a
live chat and never changes a reply.

### What the recorder stores, per turn

| | Contents |
|---|---|
| Student inputs | the turn's **input bundle** (the teacher saw the same, plus the full conversation): message, 6-message window, conversation notes, retrieved memory **text** (in order), Notice facts, mood **numbers**, affinity (neutral), time of day, previous turn's labels |
| Per sample (each a training example) | its sample id and turn; Student 1 targets (acts, tone, about-her, sureness, 4 appraisal values, 8 feelings, note updates); the mood, flags and allowed-move mask code computed from them; Student 2 targets (main and second group, dials); critic verdict and attempt number |
| Per turn | which accepted sample continued the conversation |
| For review and learning | the thought text, rejected attempts, "wanted but not available", "used older messages" + whether notes covered it, the bounded-teacher comparison where run, the user's hidden reaction |

The teacher sees text and the student gets numbers, both from the same state.

### Checking the dataset

- **Consistency:** in mood replays, do labels move the way her page says?
- **Agreement:** turns where the 5 samples disagree a lot are marked noisy and weighted down.
- **Gaps:** count "wanted but not available"; that's the to-do list for the groups.
- **History gap:** the bounded-teacher comparison (see "Same information for teacher and student").
- **You:** a small review page to thumbs-up or thumbs-down a random sample of turns.

**Splits.** Train/evaluation splits are by **whole conversation**, with its mood replays and
variants kept on the same side; never by single turns, which would put near-identical histories
on both sides. Some **scenario families** (a user type together with a situation) are held out
entirely, to test turns of a kind the student never saw.

**Pass thresholds come from the teacher itself.** The teacher's agreement with itself across
samples is the ceiling no student can beat. Measured on held-out conversations, before any claim
that a student is usable:

| Metric | What |
|---|---|
| Student 1 labels | act/tone agreement, intensity error, against the teacher's samples |
| Student 2 decisions | main-move agreement (top 1 and top 2), dial error |
| Rare moves | recall on apologise, say no, push back, comfort |
| Memory and history | the same metrics on turns built to depend on a memory or an older message |
| Connected chain | the students driving mood and notes themselves (see "The students") |

A student passes at a set share of the teacher's own agreement (proposed 80%, fixed before the
pilot runs). Data size, training time and generation cost stay estimates until measured.

## Before generating data

1. **Her page:** a fuller Mood section written as situations ("tense and someone complains, she…"),
   and the reserved affinity line.
2. **Feeling slot** and the 18 groups as the teacher's label format.
3. **Record her last turn** (what she decided and said) so `something_went_wrong` and
   "apologise / fix it" can exist.

## Same information for teacher and student

**Rule: whatever the teacher reads, the student gets too, with two exceptions:** her character
page (constant across examples, so it lives in the weights) and the **full conversation** (the
teacher reads all of it; the student gets the last 6 messages plus conversation notes).
Otherwise the student gets labels it can't explain: a teacher that remembers "his dog died last
week" reads "I'm fine" as sad, and a student without that memory learns "I'm fine" means sad.

**How it's enforced: one input bundle per turn.** Code builds the student's input record first.
The teacher's text is a **code-rendered view of that same record** (fixed templates), plus her
page and the full conversation. A test checks that every rendered line comes from a field of the
record.

**Why the teacher reads the full conversation:** its labels are the behaviour we want. A teacher
limited to the student's view would teach the student to be exactly as forgetful as itself, and
we'd never learn what it misses.

**The conversation gap is measured, not hidden** (training with privileged information: the
teacher knows more, the student learns its best guess from what it has):
1. **Self-report, as a hint:** the teacher's labels include **"used older messages"**: anything
   before the student's window that shaped them, and what. A model can't reliably report
   everything that influenced it, so this is diagnostic only.
2. **Coverage check:** for each reported use, an LLM judge decides whether the conversation notes
   carry the same meaning (not a text match). If they do, the example is normal; if not, it's
   marked **"student can't see this"** and weighted down. Weighting down limits the noise; it
   doesn't give the student the missing information.
3. **The real measurement: a bounded teacher.** On a sample of turns (proposed 1 in 5), a second
   teacher run sees **only the student's bundle**. Where its labels agree with the full teacher's,
   older messages didn't matter; where they differ, the student is missing something. That
   disagreement rate, by kind, is the evidence for new note kinds or a longer window.
4. **Growing the notes:** a kind of dependency that keeps showing up in the bounded-teacher
   disagreements becomes a new note kind. If the pilot's disagreement rate is high, notes or the
   window grow before the full run.

| In the bundle | Student gets it as | Teacher sees it as |
|---|---|---|
| current message | encoder slot | the message |
| last 6 messages (speaker, turns ago) | 6 encoder slots | the **full conversation** (the 6 are its end) |
| conversation notes (below) | up to 6 encoder slots + counts by kind | the same notes, as a list |
| retrieved memories (below) | up to 5 encoder slots + count + "none" flag | the same memories, same order |
| mood, affinity, time, Notice facts | numbers | words from fixed bands ("a bit tense") |
| previous turn's labels | numbers | words from fixed templates ("last turn she felt a bit hurt and asked about it") |
| the thought it writes this turn | nothing | its own working, not an input |

- **Memories:** a bounded set, **at most 5**, from the live retrieval
  (`long_term_memory.retrieve_for_prompt`), in its ranked order, each in its own slot (not pooled,
  so the student can tell them apart). The teacher gets exactly those 5 texts in that order, never
  more. The recorder stores the text itself, not ids.
- **Outside the 6-message window: conversation notes.** Decision-relevant meaning from earlier in
  the conversation must arrive before deciding, even when the words have scrolled out ("I'll
  remind you tomorrow" 12 messages ago). Code keeps a bounded list of open notes for the
  conversation, at most 6:

  | Kind | Opened when | Target | Closed when |
  |---|---|---|---|
  | her commitment | she chose "say yes / commit", or the user **accepts** an offer note | the request or offer | a later message's note update says completed or dropped |
  | her offer | she chose "suggest / offer" | her message | accepted (becomes a commitment), or dropped |
  | her open question | she chose "ask about it" / "check what they meant" | her message | a note update says answered |
  | their open question | their act was "asking" and her decision had no answer move | their message | she answers (her decision includes "answer / explain" on a later turn) |
  | correction in force | their act was "correcting her" | what it corrects: by default her previous turn | a newer correction **of the same target** |
  | something they shared that matters | their act was "sharing" with importance strong or very | their message | end of conversation (memory takes over) |

  - **Opening is code**, from the turn's labels. **Closing needs to know which note a message
    refers to**, which broad labels can't tell, so it uses the per-note **note updates** from
    Understand: for each open note, does this message answer it, accept it, complete it, drop it,
    or none? The teacher labels them; Student 1 learns them per note slot; code applies them.
  - **An offer is not a commitment** until the user accepts it.
  - **When all 6 slots are full**, the lowest-priority, oldest note is dropped: commitment >
    correction > their open question > her open question > her offer > something shared.
  - Each note stores the original message text it came from. Notes are never written freely by a
    model, so student and teacher see identical notes. The `her_open_question` and
    `something_went_wrong` flags read from them. The exact wording of an old promise can still be
    left to Realization; the fact that it exists can't.
- **No free-text previous thoughts.** Live there are none per turn, and the previous turn's labels
  are not equivalent to free thought, so the teacher reads only the templated labels, the same
  information the student has. When her mind is connected, its latest conclusions become one more
  bundle slot, encoded for the student and shown to the teacher.
- **Who uses memory for what:** the student only needs a memory's *effect* ("this touches
  something sad, be gentle"); the wording model also reads the memories and handles the details
  ("ask about the dog").
- **Checks:** scenarios where a memory should change the reading; an ablation (student with vs.
  without the memory slot, compared on those turns); a test that every teacher input appears in
  the student's input record.

## The students

- **Text encoder:** a small pretrained model, frozen, not trained and not a chat model. It
  encodes the bundle's text as **separate slots**: the current message, each of the last 6
  messages (speaker-tagged), each conversation note, each retrieved memory. Separate slots so a
  small encoder doesn't mix them up or cut them off. Empty slots are zeros plus a flag.
- **Student 1:** small network (MLP): all bundle slots + Notice facts + mood + affinity + time +
  previous turn's labels → Understand + Feel.
- **Student 2:** small network. Inputs: **the same context slots as Student 1** (message, 6
  messages, notes, memories), previous turn's labels, Student 1's outputs, mood after this
  message, turn flags, and the allowed-move mask → main group, second group (or none), dials.
- **Training the chain, not just the parts.** Student 2 trained only on the teacher's clean
  Understand/Feel would see imperfect Student 1 predictions at runtime, and mood and notes would
  build on those errors. So once Student 1 is trained, Student 2's training inputs are rebuilt
  partly from **Student 1's predictions** (mixed with the teacher's), with mood and notes
  recomputed from them. Evaluation runs the **connected chain** over whole held-out
  conversations, with the students driving mood and notes. To find which part failed, swap in the
  teacher's values one stage at a time (Student 1, then mood/notes, then Student 2).
- **How the conversation reaches them.** An MLP has no sense of order, so the conversation comes
  in two ways:
  1. **Recent messages:** the last ~6, encoded one by one (speaker + turns ago) into fixed slots.
     Enough for the flow: tone changing, a joke continuing, someone getting colder.
  2. **Carried state, which does most of the work:** mood, the previous turn's labels, the
     conversation notes and the turn flags hold the gist of what happened, the way a person does.
     Earlier conversations arrive through memories, not transcripts.

  Exact references ("the second one", "why that?") and details from far back are left to the
  wording model, which reads the full conversation: the student decides *how she reacts*, the
  wording handles *what exactly she refers to*.
- **Training cost and data size are estimates to test, not established.** Expected: small (a few
  hundred thousand weights, encodings computed once and cached), so quick on a laptop; actual
  time depends on hardware and caching. Data needed depends on coverage and held-out
  performance; a guess of a few thousand turns for a usable student, to be measured from the
  pilot's learning curve.
- **If the conversation isn't getting through** (measured: message only vs. with conversation, on
  turns where history matters), upgrade in order: (1) a small attention layer over the slots;
  (2) lightly fine-tune the encoder (~100M parameters, not an LLM). Neither can recover
  information that isn't in the bundle: if a needed fact is missing, the fix is a bundle input
  (a note kind, a memory), not a bigger model.
- **One pair per character.** Her personality lives in the weights; the next character gets a
  new pair trained from their own page with the same environment.

## Pilot

- About 50 conversations × 8 turns ≈ 400 turns, plus mood replays.
- About 22-25 calls per turn (5 samples × think, two labels, critic = 20; plus retries, wording,
  user), plus the bounded-teacher runs on 1 turn in 5: roughly 10,000 calls, running for hours.
  An estimate until measured.
- Pass if: (1) the data is varied and consistent with her page (mood replays, sample agreement,
  your spot checks), and (2) small students reach the agreed share of the teacher's own agreement
  on held-out conversations, separately and connected (see "Checking the dataset").

## Later (not in the pilot)

- **Affinity:** the input slot exists from the start, always neutral. To add it: write it into the
  page and teacher text, generate data where it varies, retrain (or grow the trained network with
  zero-initialised weights plus mixed old/new data).
- **Learning from reactions:** student 2 samples moves, a two-part score (in character? how did it
  land?) nudges it; anchored to the base, per-user changes kept apart from her personality.
- **Personality over time:** one experience log feeds both learned text notes (read by the LLM
  parts) and student updates. Confirmed notes are folded into a new page version you approve,
  then the teacher regenerates data and a new student is trained.
- **Connecting to her mind (game thinking):** one shared mood; her latest thoughts become inputs;
  a turn that lands badly can start a hard thought. Decide (stage 6) chooses *whether* to do a
  request; the planner (Luna with game tools) chooses *how*.
- **Going live:** first in shadow next to the chat, then as guidance in Luna's prompt.

## What this gives up

- Exact pointers into the user's words (span refs) and digest-checked replay. Recall ("what did I
  say my favourite was?") is left to memory and the wording model.
- Collaborative content / artifacts (outlines, drafts): task work, handled by the orchestrator.
- Typed audit trails; simple logs of inputs, labels and the teacher's thought instead.

## Decisions (2026-10-18)

| Question | Decision |
|---|---|
| Think and label as separate calls? | **yes** |
| What the critic checks | **in character and human-like only, never "nice"** |
| User types | **as broad as possible** (the table above, all in the pilot) |
| Real conversations | **yes, shadow only** |
| Replace the goal engine with turn flags? | **yes**. It's careful code, but too heavy and too fine for this; the flags may change as we learn |
| The 5 mood values | **agreed**: mood, energy, interest, tension, closeness in this chat |
| Teacher reads the full conversation? | **yes**, with the gap measured by a bounded-teacher comparison |
| Split mixed groups? | **yes**: greet / say bye and agree / push back, 18 → 20 groups; "say yes / commit" stays one group |

## Open questions (defaults in use)

| Question | Default |
|---|---|
| Recall handled outside the student? | yes |
| The 8 feelings | happy, amused, curious, touched, unsure, hurt, annoyed, tired |
| The 20 groups | as listed |
| Bounded-teacher sample rate | 1 turn in 5 |
| Student pass threshold | 80% of the teacher's own agreement |
| Which text encoder | to choose before the pilot: small, multilingual if you chat in more than English |

## Review concerns and resolutions (2026-10-18)

A review raised nine places where the implementation could otherwise make an unstated decision.
The intended base model is one Columbina, developing later through experience; none of this
trains across personalities. Each resolution is folded into the section named.

| # | Concern | Resolution | Where |
|---|---|---|---|
| 1 | Five samples may differ upstream, so averaging could create combinations no sample produced | Each accepted sample is one complete chain and its own training example; nothing is averaged across samples; one accepted sample continues the conversation | Teacher, per turn |
| 2 | "A little hurt" to a number was undefined; intensity, certainty and frequency were mixed | Intensity and certainty use anchored word scales; frequency comes only from training on separate samples; acts are independent yes/no, tone is one choice | What the teacher outputs; 2. Understand |
| 3 | Student 2's context inputs were vague | Student 2 gets the same context slots as Student 1, plus previous-turn labels, Student 1 outputs, mood after, flags and the allowed-move mask | The students |
| 4 | Broad labels can't open/close specific notes; offers aren't commitments; corrections need a target; overflow undefined | Per-note "note updates" (answers / accepts / completes / drops / none) labelled by the teacher and learned per slot; offers become commitments only when accepted; corrections carry a target; priority order on overflow | Conversation notes |
| 5 | Full-history teacher vs. bounded student; self-report can't be trusted; downweighting adds no information | Full history kept (decision). Self-report is a hint only; coverage judged by an LLM, not text match; the real measure is a bounded-teacher run on 1 turn in 5, whose disagreement rate justifies new notes or a longer window | Same information for teacher and student |
| 6 | Some groups merged opposite choices; a label might imply something happened | Split greet / say bye and agree / push back (20 groups); "say yes / commit" means agreement only, never completion; within-group choice is style for the wording, checked by the critic; second move optional, zero moves invalid | 6. Decide |
| 7 | Critic coverage, retries and committed state unclear | The critic reviews every sample; a rejection regenerates the whole sample; rejected attempts kept; dropped after two retries; turns with fewer than 3 accepted samples flagged; continuation only from an accepted sample | Teacher, per turn |
| 8 | Student 2 trained on clean labels but run on Student 1 predictions; errors accumulate | Student 2 retrained partly on Student 1 predictions with mood/notes recomputed; connected-chain evaluation over whole conversations; stage-swap attribution | The students |
| 9 | Turn-level splits leak; pass criteria not measurable | Split by whole conversation with its replays; hold out scenario families; metrics per stage, rare moves, memory/history turns and connected chain; pass at a set share of the teacher's own agreement | Checking the dataset |

## Implementation status (2026-10-18)

Code: `backend/app/ai/Cognitive/simple/` (offline and shadow only; nothing on a live chat path).
CLI: `backend/scripts/simple_cognition.py` (`generate`, `real`, `report`, `review`, `import-review`,
`train`, `evaluate`). Tests: `backend/tests/test_simple_cognition.py`. Output: `backend/var/cognitive_simple/<run>/`.

| Part | Where |
|---|---|
| Vocabulary, scales, records | `contracts.py` |
| Code stages: Notice, Mood, flags + allowed moves, conversation notes | `notice.py`, `mood.py`, `turn.py`, `notes.py`; her mood numbers in `docs/characters/columbina.mood.json` |
| The teacher's text view of the bundle | `render.py` |
| Teacher (think → label → code → decide → critic, whole-sample retries, wording, coverage judge) | `teacher.py`, `prompts/` |
| Scenario maker, simulated user, episode loop (5 samples, continuation, mood replays, bounded teacher) | `scenario.py`, `simuser.py`, `episode.py` |
| Memory recall with the live scoring over in-memory scenario facts | `retrieval.py` |
| Real conversations, read-only | `real.py` |
| Students (frozen `intfloat/multilingual-e5-small` encoder, two small networks), training, evaluation | `student/encode.py`, `data.py`, `model.py`, `train.py` |
| Dataset report, spot-check page | `student/report.py`, `student/review.py` |

**Where the code differs from the plan above:**
- **A `request` cue in Understand** (no request / talk only / outside her ability). The `cant_do`
  flag needs it, and code can't tell from words what a request needs.
- **Memory recall** uses the live scoring and thresholds (imported from `long_term_memory`), but
  over an in-memory pool of the scenario's facts: synthetic users have no database rows. Limit 5
  (live uses 6). Episodes and recency don't apply.
- **Thinking uses `reasoning_effort=low`**; labelling and critic run at temperature 0 without
  reasoning; the simulated user and wording at temperature 1.
- **Her page** gained a situational Mood section and a "How well she knows this person" line
  (always "you don't know them well yet").
- **Simulated users, batch 2:** the first batch's user prompt let hard types soften (a "rude" user
  who was rude only about their landlord). From seed 12 on, the type must show toward her, and each
  scenario also gets a `want` (chat, vent, company, a task, test her...). Batches are kept apart
  by scenario id prefix and per-batch manifests.

### Pilot results (run `pilot`, 2026-10-18)

**Data.** 50 conversations (batch s11: 30, batch s12: 20), 416 turns, 34 user types, 36 families.
2,080 samples, 2,029 accepted (89.5% on the first try, 51 dropped after retries, 4 noisy turns),
so 2,328 training examples (2,029 main + 299 mood replays). 12,726 model calls, 35.3M prompt
tokens, 1.5M completion tokens, no failed calls; about 20 minutes per batch at 10 conversations
at once. Replies average 28.5 words (44 before the length rule).

- **Critic rejections** (443): older messages not listed 272, labels not following the inputs 106,
  out of character 26, not human-like 17. The "request" label was the main follows-inputs failure
  until a question was defined as talk-only.
- **Main moves:** answer/explain 714, play 341, show understanding 261, celebrate/thank 95,
  comfort 84, ask about it 78, share view 71, suggest/offer 70, apologise/fix 65, say bye 50,
  agree 49, say yes 43, share about herself 33, react briefly 27, push back 22, say no 16,
  check meaning 7, say how she feels 3. "Ask about it" is mostly a second move (353 of 2,029).
- **She almost never feels hurt or annoyed.** Batch s11's simulated users softened every hard type
  (hurt 0.001, cold-toward-her readings 0). Batch s12 fixed the user prompt: 38 cold-toward-her
  readings, hurt 0.031, push back 22, and her replies to real jabs fit the page ("that sounded like
  a jab at you, and it stung."). Still rare: most hard users aim their anger at someone else, and
  her page says she assumes good intent first.
- **Wanted but not available:** 1 (searching restaurants). The 20 groups covered what the teacher wanted.
- **History gap.** 216 turns had history beyond the student's window. 277 samples reported using
  older messages; for 147 the notes didn't carry it (judge). Bounded vs. full teacher on the same
  turns: main-move agreement 0.757 against the full teacher's own 0.769 (turns with older history)
  and 0.681 against 0.725 (all in window). **The student's view loses little**: the gap is about
  as big as the teacher's disagreement with itself.

**Students** (held out: 10 families, 132 turns, 599 examples; teacher leave-one-out agreement is
the ceiling; pass = 80% of it):

| Metric | Teacher | Student | Share | |
|---|---|---|---|---|
| acts | 0.970 | 0.896 | 0.92 | pass |
| request | 0.966 | 0.893 | 0.93 | pass |
| tone | 0.937 | 0.751 | 0.80 | pass |
| feelings error (lower is better) | 0.050 | 0.142 | 0.35 | |
| appraisal error | 0.051 | 0.122 | 0.42 | |
| main move, top 1 | 0.738 | 0.536 | 0.73 | |
| main move, top 2 | 0.873 | 0.571 | 0.65 | |
| second move | 0.579 | 0.457 | 0.79 | |
| dials error | 0.029 | 0.083 | 0.35 | |
| rare moves (apologise, say no, push back, comfort), top 2 | 0.895 | 0.070 | 0.08 | |

- **Student 2 is the bottleneck, not Student 1:** given the teacher's own Understand/Feel it scores
  0.549, the same as on Student 1's predictions (0.536).
- **Connected** (students driving mood and notes over whole conversations): her main move matches
  one of the teacher's samples 57% of the time, the teacher's most common move 44%. The students'
  notes stay emptier than the teacher's (0.65 open vs 1.34). The predicted mask blocked a teacher
  move 4.9% of the time.
- **Mood replays:** same direction as the teacher in 35 of 67 clear shifts (52%). Tired (14/18)
  and playfulness (3/3) are learned; most of the rest is near chance. 299 replay examples are not
  enough.
- **Learning curve** (main move top 1): 8 conversations 0.447, 16 → 0.499, 31 → 0.536. Still rising
  by about 4-5 points per doubling, so more data helps; feelings error stops improving (0.155 →
  0.142 → 0.142).
- **Real conversations** (`real-shadow`, 8 turns of the one stored chat, a Minecraft session): the
  teacher labels in-game requests ("follow me", "make a sword") as outside her ability, so "say
  yes" is blocked. The bundle needs a "what she can do here" input before real game chats are used.

**Reading it.** The teacher environment works and the data looks like her. The students learn
what a message *is* (acts, request, tone pass) but not yet what she *does* or *feels*: the move
choice is at 73% of the teacher's ceiling and climbing with data, and rare moves, feelings and mood
effects are far off. Next, in order of expected value: (1) more data, weighted toward hard users,
corrections and can't-do requests (rare moves have about 60 examples in total); (2) far more mood
replays; (3) a "what she can do here" input; (4) only then model changes (attention over slots,
class weighting for rare moves).
