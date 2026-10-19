# Intention teacher experiment (2026-10-19)

Question: does losing her intention between thinking and speaking cause the interview pattern,
corrections that don't stick and invented self-history found in
`docs/qa/columbina-teacher-review-2026-10-19/REVIEW.md`?

**Setup.** Run `backend/var/cognitive_simple/intention-v1`, variant `intention-v1`
(`backend/app/ai/Cognitive/simple/intention.py`): understand the moment → intention in plain words →
reply spoken from that thought → labels extracted afterwards. No critic, no reply check. Character
page v0.3 (motives instead of habits, a "Listening" section, "Her world"), plus a grounded world per
scenario (`docs/characters/columbina.world.json`: interests, preferences, thoughts, things noticed
in earlier conversations). Ten pilot scenarios rerun with their original user cards: the review's
failing cards (26, 29, 30, 33, 35, 36), the tomato disagreement, and three hard users. 3 samples
per turn; about 1,000 calls (the pilot used 12,700 for 50 conversations). The three s11 scenarios
(s11-000, s11-003, s11-008) used the stricter simulated-user prompt here but the softer one in the
pilot; the seven s12 scenarios used the same user prompt in both.

Changes were made together, so this tests the combination, not each change alone.

## Numbers (same 10 scenarios)

| | Pilot (v0.2, labels first) | intention-v1 |
|---|---|---|
| Replies | 95 | 99 |
| Replies with a question | 48% | **1%** |
| Decision includes "ask about it" | 43% | 3% |
| Longest run of question-replies per conversation | 4, 4, 6, 4, 10, 4, 1, 2, 0, 1 | 1, 0, 0, 0, 0, 0, 0, 0, 0, 0 |
| Mean words per reply | 25.3 | 23.0 |
| Invented body/place claims (pattern match) | 2 | 0 |
| Main moves (top) | answer 26, show understanding 22, play 15, ask 6, push back 6 | play 27, answer 23, show understanding 13, comfort 7, agree 7, apologise 5, share about herself 4 |
| "Did not fit the groups" | — | 1 (she chose silence) |

## What got better (reading the conversations)

- **Topic changes are followed.** The topic-changer (s12-003) says "anyway" a dozen times; she goes
  with every change. When the grandmother comes up she acknowledges it once and doesn't steer back
  (pilot: she pulled the user toward the grandmother; review card 30).
- **Grief is accompanied, not managed** (s11-000): she keeps company over the recipe cards, lets a
  card "stay by the kettle", never turns it into progress (pilot card 36 asked whether tea was
  "helping you get closer to the first card").
- **She talks about herself truthfully** (s12-007): "i keep returning to how many ways 'i'm fine' can
  sound", "i don't eat, but…", "I'd want you to remember that even short goodbyes feel a little sad
  to me." No walks home or telescope notebooks of her own.
- **She disagrees when asked** (s12-015): "i disagree that being careful means i'm hiding. sometimes
  it's how i leave room for someone else."
- **Corrections land** (s12-015): "you're right, i had it backwards."

## What got worse

- **Curiosity disappeared.** One question in 99 replies. In s11-008 the user says they feel "new
  somewhere but starting to belong"; she answers "that feeling … is lovely" and moves on, and the
  user has to correct her ("I was trying to say that's something I'm living through"). In s12-003 she
  riffs on every joke for 12 turns without once asking anything. She became a pleasant mirror.
  Cause: the v0.3 page and the intention prompt both argue against questions ("a question is only
  one way…", "have I been asking question after question?"), and nothing argues for them.
- **A lot of riffing.** Play is now the most common main move (27%): she often echoes the user's joke
  back with a small twist ("the app's alibi is impressively useless") instead of adding something.
- **A label slip.** The extraction labels sympathy ("i'm sorry it's messed this up again") as
  apologise/fix; that should be comfort or show-understanding (apology is for her own mistake).

## Reading it

The combined redesign (intention handoff, v0.3 page, her world) fixed topic changes, grief,
self-talk and disagreement in the same conversations where the label-first teacher failed. Which
change did it is not established: they were made together, so "the compression caused it" is a
hypothesis, to be tested by ablation (see "Review of rounds 1-4"). But the
fix overshot: the prompts now discourage questions instead of giving curiosity a purpose. The
interview problem was "questions without a reason", not "questions".

## Proposed next step (not done)

One small rebalance, same 10 scenarios:
1. Page and intention prompt: she asks when she genuinely wants to know something they haven't told
   her, especially when they share something about their life or feelings; she doesn't ask what
   they've already said, or as a reflex. Drop the anti-question line from the intention prompt; keep
   the listening rules.
2. Riffing: she adds something of her own (a thought, a feeling, a small detail she noticed) rather
   than only mirroring the joke.
3. Extraction: apologise/fix only for her own mistake; "sorry that happened" is comfort.

Then read the conversations again. Target by reading, not by a number, but for reference a curious
person asks in roughly a fifth to a third of replies.

Conversations side by side: `conversations.md` in this folder.

## Rebalance: intention-v2 (same 10 scenarios)

Run `backend/var/cognitive_simple/intention-v2`; page v0.4 (curiosity with a purpose: she asks about
what they haven't told her, especially their life and feelings; playing along must add something of
her own); intention prompt asks "what don't I know yet that I'd like to know?" and drops the
anti-question line; extraction: apology only for her own mistake. v0.3 and the v1 prompts are kept
(`columbina-v0.3.md`, `prompts/*_v1.txt`, `--variant intention-v1`). About 1,000 calls.

| | Pilot | intention-v1 | intention-v2 |
|---|---|---|---|
| Replies with a question | 48% | 1% | 45% |
| Decision includes "ask about it" | 43% | 3% | 47% |
| Longest question run per conversation | 4, 4, 6, 4, 10, 4, 1, 2, 0, 1 | 1, 0, 0, … | 1, 2, 1, 8, 8, 3, 3, 1, 8, 0 |
| Invented body/place claims | 2 | 0 | 0 |
| Mean words | 25.3 | 23.0 | 26.8 |

**Reading.** Her questions now have a reason: they're about things the user hasn't said ("who's
Daniel, if you don't mind me asking?", "what did your mom use before the blue ballpoint?"), not
things already answered, and she still follows topic changes and stays honest about herself. But in
grief they pile up: with the user who lost Lena she asks 8 in a row until the user says "I don't
really want to keep picking it apart tonight. Can you just talk with me a bit?" (then she adapts
well: "of course. we don't have to make it better."). The recipe-card conversation does the same
over 12 turns. New, smaller: she repeats one world thought three times in s11-008 ("whether people
remember sounds or feelings more").

**Why the pendulum.** v1 said "don't ask by habit" and she stopped asking; v2 said "ask when you're
curious" and every turn found a genuine curiosity. Each turn decides alone, and every single
question is reasonable; the problem only exists across turns. A person notices their own rhythm
("I've asked three things in a row; maybe they want company, not questions"). The teacher doesn't
see its own rhythm as a fact, only as a long transcript it reads per turn.

## Proposed next (not done)

Give her the rhythm as an input instead of another rule:
- A Notice-style fact about **her own** recent replies, computed by code: how many of her last
  replies were questions, and which world thoughts she has already shared in this conversation.
  It goes in the bundle, so the student gets it too. The page says what she does with it: after a few
  questions, especially with someone low or grieving, she offers company, a thought of her own, or
  quiet; she doesn't repeat a thought she's already shared.
- Rerun the same 10 scenarios and read them.

## Round 3: intention-v3, her own rhythm as an input (same 10 scenarios)

Run `backend/var/cognitive_simple/intention-v3`. Code counts her own recent replies (how many asked
a question, how many in a row) and which world thoughts she has already shared (`simple/rhythm.py`);
it goes in the bundle as "Her own recent replies". Page v0.5 says what she does with it: after two
or three questions she gives instead of asking (company, a thought of her own, quiet), sooner with
someone low or grieving; she doesn't repeat a shared thought. Round 2 kept as `columbina-v0.4.md`,
`prompts/*_v2.txt`, `--variant intention-v2`.

| | Pilot | v1 | v2 | v3 |
|---|---|---|---|---|
| Replies with a question | 48% | 1% | 45% | 9% |
| Longest question run per conversation | up to 10 | 1 | up to 8 | 1 |
| Invented body/place claims | 2 | 0 | 0 | 0 |
| Mean words | 25.3 | 23.0 | 26.8 | 22.9 |

**Better.** The interview is gone without losing the good parts. With the user grieving Lena she
keeps company ("we can just let this one be quiet together") instead of asking eight questions. The
astronomy teacher (s12-007) is the best conversation so far: one real question when she's curious
("what do you enjoy about teaching astronomy?"), real views ("a pause is a detail, not an
explanation"), and the user notices: "i like that you didn't claim it as your own memory".

**New main problem: she answers questions about herself evasively.** "What did you do today?" gets a
world thought ("i've been wondering whether people remember sounds or feelings more"), and the user
pushes back twice ("You didnt answer me", "dodging a very simple question"). The friendly user
(s11-008) also had to correct "i don't have a day like yours", which she says in several
conversations. Cause: her world has thoughts and interests, but nothing she *did* today, so a simple
question has no simple true answer, and the page lets her open with a disclaimer.

**Smaller.** She blurred "radio dramas" into "songs" (s12-012). One sample produced an empty reply.

**Scenario bug (affects every run so far):** the simulated user is never told the time of day, so
scenarios set to morning get "I'm home after work" and she answers "good morning!" to an evening.

## Proposed next (not done)

1. Her world gains what she did today, in true terms: how many conversations, what kinds of things
   came up (no private details), what she spent the in-between time thinking about. Live, this comes
   from her actual day.
2. Page: when asked about herself, she answers simply first ("pretty good, quiet. I talked with a few
   people…"), no "I don't have a day like yours" disclaimer unless they ask what her days are like.
3. Fix the scenario bug: tell the simulated user the time of day.

## Round 4: intention-v4, her own day as memories (same 10 scenarios)

Run `backend/var/cognitive_simple/intention-v4`. Each scenario first generates her memory of her day
so far (`prompts/her_day.txt`, `world.day_lines`): how many conversations, 3-5 ordinary things that
happened in them (no names or private details), what she thought about in between, a game if any,
and how the day felt. It's added to her world. Page v0.6: asked about herself, she answers plainly
first, from her day; no "I don't have a day like yours" unless asked what her days are like.
Scenario bug fixed: the simulated user is now told the time of day. Round 3 kept as
`columbina-v0.5.md`, `prompts/*_v3.txt`, `--variant intention-v3`.

| | Pilot | v1 | v2 | v3 | v4 |
|---|---|---|---|---|---|
| Replies with a question | 48% | 1% | 45% | 9% | 11% |
| Longest question run | up to 10 | 1 | up to 8 | 1 | 1 |
| "I don't have a day like yours"-type disclaimers | | | | 2 | 0 |
| Invented body/place claims | 2 | 0 | 0 | 0 | 0 |

**Better.** Questions about her day get plain, true answers: "my night's been quiet and thoughtful.
someone told me about their little tea-making ritual…"; "my morning's held two conversations so far".
No disclaimers. The rhythm from round 3 holds.

**Still wrong.**
- **She took the user's memory as her own** (s12-012). Asked "what kind of old dramas do you like?",
  the continued sample thought "I only remember listening to old radio dramas with Lena while we
  cooked" and said it. The other two samples got it right ("I don't have my own listening memories…
  I think I'd like mysteries, where a voice makes a whole world"). The continuation is picked at
  random, so a 1-in-3 slip became the conversation, and the user spent the rest of it pushing back
  ("That still sounds like a borrowed answer").
- **Thin preferences.** "What do you like listening to?" has no true answer in her world; she reached
  for "I like the sound of the word 'drizzle'".
- **Stock phrases.** The morning user asked about her four times; she answered "quiet and gently
  curious" each time (her day's one "how it felt" line).

## Proposed next (not done)

1. **Continue from the most typical sample, not a random one.** Pick the accepted sample whose
   labels agree most with the others. Not a quality check: nothing is judged or rejected, and all
   samples stay in the data as soft labels; it only stops a one-off slip from steering the rest of
   the conversation.
2. Intention prompt: in MOMENT, call them "they" and keep whose memory is whose; theirs are never hers.
3. When she doesn't have something (a listening habit), she says so in a few words and offers what she
   does have, rather than reaching for a substitute.
4. Her world: a few more concrete preferences (things people have played or read to her, games),
   and her day's "how it felt" as a feeling she can vary, not a phrase to repeat.

## Review of rounds 1-4 (2026-10-19)

A second review (shared in conversation) and a check of the records:
- **Not established which change helped.** Rounds changed several things at once. Next: ablate
  (intention handoff with the v0.2 page and no world; label-first teacher with the new page).
- **The question rate follows the latest instruction** (48% → 1% → 45% → 9% → 11%), so prompt rules,
  not character, are steering it. Replace the "after two or three questions" recipe with a reading
  of how the exchange is going (inviting exploration, answering reluctantly, asking to know her,
  wanting quiet company, wanting a task done); counts stay supporting facts; no target rate.
- **Memory ownership is a data bug, confirmed.** The memory retrieved on the Lena turn reads "She and
  Lena used to listen to old radio dramas…": user-card facts call the user "she", and her context
  calls Columbina "she". Fix: typed memories (about them / shared / her own / her preference) with
  explicit subjects, rendered in separate sections.
- **Most-typical-sample continuation withdrawn:** label agreement says nothing about whose memory a
  reply uses, and it would filter out unusual replies.
- **Student/live gap:** `student/data.py` reads none of world, rhythm or intention. Proposed live path:
  students give reading (plus the exchange cue), feelings and mood; one live call writes intention
  then reply; move labels become descriptive.
- **Her generated day is scenario fiction.** Live autobiographical memory must come from recorded
  events; a day built from other users' chats needs a privacy decision first.

## Ablation (2026-10-19): handoff × page × rhythm

Run `backend/var/cognitive_simple/ablation-1` (`simple/ablation.py`). A 2×2×2 design on the same 10
scenarios and user cards: handoff (labels-first, the pilot chain, vs. intention-first, frozen
`*_v4` prompts) × page (v0.2 vs. v0.6, both frozen files) × her rhythm (off/on). Held constant
in every arm: typed memories with explicit ownership and names (facts rewritten from "she" to the
person's name), her scoped world and day (her own experiences only, prepared once per scenario),
the simulated user (now told the time of day), 3 samples, **no critic in any arm**, the same seeded
continuation. Baseline arm `L-p2-r0` (the pilot design under these controls). About 8,000
generation calls and 760 rater calls.

| Arm | Questions | Longest question runs | Words | Reaction | Repeated herself | Dodged | Invented self | Borrowed memory |
|---|---|---|---|---|---|---|---|---|
| L-p2-r0 (baseline) | 53% | 4,5,3,2,11,3,9,3,2,1 | 25.8 | 0.31 | 22% | 3% | 3% | 0% |
| L-p2-r1 | 30% | ≤3 | 24.3 | 0.27 | 16% | 3% | 3% | 1% |
| L-p6-r0 | 7% | ≤1 | 27.2 | 0.34 | 25% | 5% | 0% | 0% |
| L-p6-r1 | 8% | ≤2 | 25.6 | 0.31 | 17% | 3% | 1% | 0% |
| I-p2-r0 | 52% | 3,8,6,1,7,7,1,3,3,0 | 21.9 | 0.27 | 15% | 8% | 3% | 0% |
| I-p2-r1 | 31% | ≤3 | 24.0 | 0.33 | 16% | 5% | 0% | 0% |
| I-p6-r0 | 13% | ≤2 | 23.1 | 0.36 | 9% | 3% | 0% | 0% |
| I-p6-r1 | 18% | ≤2 | 24.9 | 0.28 | 12% | 8% | 1% | 0% |

Main effects (average of the four arms at one level minus the four at the other):

| | Questions | Repeated herself | Dodged | Invented self | Words | Reaction |
|---|---|---|---|---|---|---|
| Intention vs. labels | +4 pts | −7 pts | +2.5 pts | −1 pt | −2.3 | 0.00 |
| Page v0.6 vs. v0.2 | **−30 pts** | −1 pt | 0 | −2 pts | +1.2 | +0.03 |
| Rhythm on vs. off | −9 pts | −3 pts | 0 | 0 | +0.2 | −0.02 |

**Rater calibration.** On pilot conversations with known review failures the rater flagged the
borrowed Lena memory and card 29's already-answered question, but missed cards 26, 30, 35 and 36
(correction not sticking, steering back to grief, ignoring "not the easy answer", grief turned into
progress). Its counts for ignored corrections and topic changes (near zero everywhere) are
therefore not evidence; those were judged by reading.

**What it shows (10 scenarios, one run per arm; differences of a few points are noise):**
- **The character page drives the question pattern**, not the handoff: v0.6 cuts questions by 30
  points under both handoffs, and removes the long interview runs. The intention handoff alone
  (`I-p2-r0`) still interviews (runs of 6-8).
- **Her rhythm matters with the old page** (53% → 30%) and adds little once the page is v0.6.
- **The intention handoff mainly brings variety:** less repetition (−7 points; e.g. `L-p6-r0`
  says "i'm here with you" five times to the grieving user, `I-p6-r0` doesn't) and slightly
  shorter replies. It slightly increases dodged questions.
- **Read side by side, `L-p6-r0` and `I-p6-r0` both** follow the topic-changer and keep the
  grieving user company without interrogating; neither steers back to the grandmother.
- **Borrowed memories:** 0-1% in every arm with typed memories, against 1.1% (intention-v4) and
  2.1% (pilot) under the same rater with untyped memories. Different runs, so suggestive rather
  than controlled, but consistent with typed ownership fixing the cause.
- **Simulated users' reactions** don't separate the arms (0.27-0.36).

So the compression hypothesis is mostly **not supported** as the main cause: the page change did
most of the work. Speaking from her intention is a smaller, real gain in variety.
