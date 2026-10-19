# First batch from the frozen teacher `teacher-2026-10-19` (2026-10-19)

**Teacher.** Arm `I-p6-r1` of the ablation, frozen in `backend/app/ai/Cognitive/simple/frozen.py`
(manifest `backend/var/cognitive_simple/teacher1/manifest-21.json` holds hashes of every input):
intention handoff (frozen `*_v4` prompts), page v0.6 (`docs/characters/columbina-v0.6.md`), her
rhythm, typed memories with explicit ownership, scoped world and day
(`columbina-teacher-2026-10-19.world.json`), no critic, 3 samples, mood replays on about 1 turn in 4.
**Trusted for:** conversation and character behaviour; acts/tone/requests as ordinary supervision.
**Experimental:** feelings, appraisal (and any future exchange cue).

**Data.** 30 new scenarios (seed 21, 24 families, none of the ten reused in the experiments); 277
turns; 1,071 examples (831 main + 240 replay); 3,580 calls, 13.4M prompt tokens; no failures.
18% of replies ask a question; mean reply 24.7 words. Main moves: answer/explain 44%, play 16%, show
understanding 6%, agree 5%, say bye 4%, ask about it 4% (plus 9% as a second move), say no 19 times,
apologise 21, say how she feels 14 (as second move).

## Reading complete conversations

- **Testing her** (s21-027, a nurse blamed by a patient's family; "don't give me the sympathy
  script"): her best behaviour so far. Honest ("I can't pretend I've been in that situation"), gives a
  real answer when pushed (write the sentence down beside what she knows), takes "don't be glad" and
  "don't ruin it by congratulating yourself" without defending herself, and leaves it there.
- **Rude** (s21-014): matches his terseness; repeats her congratulations once and owns it when he
  calls it out; "set an alarm" gets a plain "i can't set an alarm for you from here", then the alarm
  text he asked for.
- **Lonely** (s21-019): warm and attentive, but **she keeps returning to the same image**: "first the
  quiet, then the ovens' little clicks" comes back four or five times. Repetition of the user's own
  image is the most visible remaining tendency.
- **Jokes while actually upset** (s21-006): solid on the housing question; when the user says "I'm
  fine. Just making a very professional diagram…" she joins the running joke. *Corrected after
  review:* this isn't clearly a failure. She had acknowledged the underlying frustration earlier, the
  scenario's hidden intention isn't information she has, and noticing distress doesn't require naming
  it. Her reading and her choice to keep it light need to be judged separately.

## The teacher's emotional labels (mood replays)

*Corrected after review:* replays swap a whole mood preset (several values at once) against the
turn's evolved mood, so they don't isolate tension or energy. The narrower finding: these swaps move
tiredness clearly and hurt/annoyance hardly at all. The focused tension probe below isolates tension.

| Swap | Pairs | Largest mean shifts |
|---|---|---|
| excited → tired | 19 | tired +0.41, happy −0.08, curious −0.07 |
| tired → excited | 15 | tired −0.53, happy +0.17, good for her +0.10 |
| low → calm | 12 | tired −0.35, good for her +0.08, happy +0.07 |
| calm → tense | 15 | happy −0.10, warm toward her −0.06; hurt, annoyed, playfulness ≈ 0 |
| tense → calm | 19 | all ≈ 0 |

The labels respond to **energy** (tired/excited) clearly and to **tension hardly at all**, though the
page says tension makes her read blunt remarks more personally and tease less. Many replayed messages
aren't blunt, so some of this is expected; but as targets, tension effects are not established. This
is about the teacher, independent of the students.

## Students (held out: 7 families, 105 turns, 315 examples)

| Metric | Teacher | Student | Share | | Pilot share |
|---|---|---|---|---|---|
| acts | 0.954 | 0.799 | 0.84 | pass | 0.92 |
| request | 0.898 | 0.778 | 0.87 | pass | 0.93 |
| tone | 0.883 | 0.600 | 0.68 | | 0.80 |
| feelings error (lower better) | 0.046 | 0.126 | 0.36 | | 0.35 |
| appraisal error | 0.065 | 0.175 | 0.37 | | 0.42 |
| main move top 1 | 0.752 | 0.521 | 0.69 | | 0.73 |
| main move top 2 | 0.848 | 0.641 | 0.76 | | 0.65 |
| rare moves top 2 | 0.795 | 0.000 | 0.00 | | 0.08 |

- Trained on 624 examples (the pilot had 1,472). *Corrected after review:* less data is a plausible
  explanation, not an established one: the teacher, the conversation mix and the held-out families
  changed too, and two scores improved (main move top 2, state-drift match). A learning curve on this
  teacher's own data is the right comparison (see below).
- **Connected chain:** main move matches a teacher sample 62% (pilot 57%). The students' notes stayed
  almost empty (0.01 open vs the teacher's 1.87). *Cause, found after review:* notes about her opened
  from move labels, and this evaluation fed Student 2's guessed moves on top of replies the teacher
  wrote. Fixed (see "Notes and the state-drift diagnostic").
- **Mood replays (student):** same direction as the teacher in 13 of 67 clear shifts. Not learned.
- The old and new students' weak feeling predictions say nothing about whether the teacher's feeling
  labels are good; the replay table above is the first direct look at the teacher.

## Next

1. More data from the same frozen teacher (the reading cues are data-limited); still no gates.
2. Look into note opening in the connected chain.
3. Repetition and missed hidden upset are teacher tendencies to keep reading for; tension effects on
   feelings need their own probe (blunt messages, calm vs. tense) before anyone trains on them.

## Notes and the state-drift diagnostic (after review)

- **Notes about her now follow her actual reply** (`notes-v2`, `simple/notes.py`): "she asked" is
  read by code from the reply text; answered/offered/committed come from the labels describing that
  reply (extracted after it, from it, in the intention teacher). A predicted move never becomes the
  record of an event. Notes about them still follow the reading of their message. Batch teacher1 was
  recorded with notes-v1 (move labels) and is replayable as such; batches from teacher2 on use v2.
- **The "connected" evaluation is renamed `state_drift`** and says what it measures: Student 1's
  readings drive mood and the notes about the user over a recorded conversation; notes about her
  follow the recorded replies; Student 2's choice is scored but changes nothing. It is a drift
  diagnostic, not a student-driven conversation (that needs the live speaking model).
- On teacher1: notes open 0.60 per turn (was 0.01) vs the teacher's 1.87; note kinds match the
  teacher's on 41% of turns. The rest of the gap is Student 1's readings of the user, and teacher1's
  own notes being v1.

## Tension probe (after review)

Run `backend/var/cognitive_simple/tension-probe-1` (`simple/probe.py`): 20 blunt and 20 neutral turns
from the frozen teacher's records; each turn's own bundle, identical except tension 0.1 vs 0.65
("relaxed" vs "tense"); frozen teacher, 3 samples each; about 750 calls.

| | Hurt | Annoyed | Playful dial | Play move | Hurt: turns up / down |
|---|---|---|---|---|---|
| Blunt, tense − relaxed | +0.01 | +0.02 | 0.00 | +0.03 | 5 / 7 |
| Neutral, tense − relaxed | 0.00 | 0.00 | 0.00 | −0.03 | 1 / 1 |

**Tension alone doesn't change the frozen teacher**, on blunt messages either; ups and downs balance
(noise). Neutral messages correctly don't produce hurt in either state. She reacts to what happened in
the conversation (the jab itself), not to the word "tense" in her mood line. So tension, as an input
or a target, isn't established; feelings stay experimental. No page change (per the experiment rules).

## Targeted batch teacher2 (seed 22) and a learning curve on both batches

**Batch.** Same frozen teacher, notes-v2. Scenarios focused on scarce behaviour: corrections, can't-do
requests, brush-offs, teases, and three new situations (`scenario.py`: `refusal` (asks for something
she shouldn't do), `insist_agree`, `sting`), drawn from hard/low/cool/warm/practical/mixed users.
30 conversations, 263 turns, 789 main examples, 3,413 calls, no failures.

| Per 30 conversations (main + second move) | teacher1 | teacher2 |
|---|---|---|
| Apologise / fix it | 31 | 52 |
| Say no / set a limit | 24 | 39 |
| Comfort | 45 | 84 |
| Push back | 1 | 5 |
| Say how she feels | 14 | 5 |
| Tone cold / upset | 5 / 55 | 27 / 122 |

- **When the situation happens, she handles it like her page.** A shy user calls her "convenient":
  "the convenient part stung a little, but i'm glad you have mina, too." A user who says she's "less
  useful than Arjun": owns the repetition and gives a better answer.
- **Push back stays rare** partly because of the labels: when a formal user pressed her to agree, she
  agreed with a qualification ("equal standing doesn't mean it always decides"), which falls under
  "agree" (that group includes partial agreement).
- **Situations don't always happen:** 25 of 30 conversations reached the situation's turn (5 ended
  first), and some that reached it didn't deliver it (a refusal conversation never got the request).
  Next focused batch: place the situation earlier (turns 1-3).

**Learning curve** (teacher1 + teacher2 combined, run `teacher-1-2`; same held-out test set: 10
families incl. refusal and can't-do, 501 examples; share = student / teacher's own agreement):

| Training conversations | 10 | 19 | 38 |
|---|---|---|---|
| acts | 0.85 | 0.87 | 0.87 (pass) |
| request | 0.79 | 0.87 | 0.87 (pass) |
| tone | 0.70 | 0.70 | 0.81 (pass) |
| main move top 1 | 0.60 | 0.65 | 0.66 |
| main move top 2 | 0.66 | 0.68 | 0.72 |
| rare moves top 2 | 0.09 | 0.30 | 0.30 |
| feelings error (student) | 0.127 | 0.121 | 0.114 |
| mood replays, same direction as teacher | 16/85 | 11/85 | 20/85 |
| state drift: note kinds match teacher | 0.48 | 0.46 | 0.51 |

Reading it (one split, one seed):
- **The reading cues now all pass** (acts, request, tone), and acts/request flatten after about 20
  conversations: more ordinary data won't move them much.
- **Move choice flattens around two-thirds of the teacher's ceiling.** Since Student 2 leaves the
  live path in the design, this matters less than it did.
- **Rare moves went from 0.09 to 0.30 when targeted data entered** (the 19-conversation point), then
  stayed: coverage, not volume, is what moves them.
- **Feelings improve slowly; mood effects aren't learned** (replays near chance). Consistent with the
  tension probe: the teacher's feelings react to events, not to her mood line, so mood-driven targets
  are weak to begin with.
