# Live intention path and exchange cue: design for review (2026-10-19)

Status: **first live step built 2026-10-19, flag-gated and off by default** (see "Built: first live step"); the intention-first step and the exchange cue are not built. Requested 2026-10-19: write this before building. Depends on
the ablation in `docs/qa/intention-teacher-2026-10-19/` (section "Ablation"), which says how much of
the improvement comes from the intention handoff, the character page and her rhythm. Read with
`docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md`.

## The problem it solves

The pilot teacher compressed her thinking into move labels and four dials, then wrote the reply from
the labels. The offline experiments (rounds 1-4) spoke the reply from her own intention instead, and
behaviours that had failed got better together (topic changes, grief, talking about herself,
disagreeing). Which change did it is being measured, not assumed.

If the students then only learn the old labels, the live system rebuilds the original bottleneck: a
small model picks "answer → ask about it" and the reply model executes it. This design keeps the
intention in the live path and gives the students a job they're good at.

## Division of work

| Part | Who | Why |
|---|---|---|
| Notice, mood, notes, flags, her own rhythm | code | cheap, readable, shared with her game mind later |
| How she reads the message, how it lands, how she feels, **how the exchange is going** | **Student 1** (local, milliseconds) | the pilot students passed on reading (acts, request, tone); this is a perception job |
| What she wants to do (intention) and the words | **one live LLM turn** | the intention needs the real dialogue; a 20-way classifier couldn't hold it |
| Move labels | extracted afterwards, offline | description for analysis and training, never a decision |

Student 2 (the move chooser) is no longer on the live path. It can stay as an offline analysis model.

## The live turn

```
user message
  → Notice (code) + typed memory retrieval
  → Student 1: reading, exchange cue, appraisal, feelings, note updates          (~10 ms)
  → code: mood update, notes, flags, her rhythm
  → reply model: page + typed memories + notes + her state in words + conversation
       → private intention (1-3 sentences) → reply (streamed)
  → after: intention saved as conversation state; labels extracted offline (shadow)
```

### Where it plugs in

Today a chat turn is the Luna orchestrator (`app/ai/chat.py`, `prompts.build_system_prompt`):
base prompt, awareness, memory block, game state, specialists. The proposal keeps the orchestrator,
since it owns tools and specialists, and adds:

1. **A "her state" block** in the system prompt: Student 1's reading and feelings, her mood, the
   exchange cue, open notes and her rhythm, all in words from fixed templates (the same rendering
   the teacher uses).
2. **Typed memories** replacing the single memory block (below).
3. **Intention first.** Two options:
   - **(a) Two calls:** a short, non-streamed intention call (at most ~60 tokens), then the
     streamed reply call with the intention in context. Clear and easy to log, but adds roughly a
     second before the first word.
   - **(b) One call with a hidden prefix:** the model writes `<intention>…</intention>`, then the
     reply. The stream handler holds back the prefix and streams only the reply. No extra round
     trip; needs a robust parser and a fallback if the tag is missing.

   Recommendation: (b) for both typed chat and voice, since voice can't afford a second; (a) as the
   shadow-evaluation form, because it logs cleanly.
4. **The previous intention** is kept per conversation (next to dialogue working memory) and shown
   next turn: "What you meant to do last turn: …". It exists live because the model wrote it, so the
   student/teacher information rule holds.

### When something is missing
- Student 1 unavailable or slow: the state block is left out; the reply model still has the page,
  memories and conversation (today's behaviour).
- Intention tag missing: the reply is used as-is, and the turn is logged.

## The exchange cue

What the person is inviting right now: a reading of the exchange, not a rule about questions.

| Value | Means | Example |
|---|---|---|
| `exploring` | they're opening a subject and welcome curiosity | "i think i'm finally starting to feel at home here" |
| `reluctant` | answering minimally, closing a subject | "Not really." / "I dont really want to unpack it" |
| `asking_about_her` | they want to know her | "what did you do today?" |
| `wants_company` | presence, not questions or fixing | "can you just talk with me a bit?" |
| `wants_answer` | a question or task they want done | "how long do boiled eggs keep?" |
| `playing` | banter, a running joke | "pigeon mcflapface" |
| `closing` | wrapping up | "I'll leave it there tonight" |

- **Shape:** probabilities over the values; several can hold (playing + asking_about_her).
- **Who labels it:** the teacher, in the extraction step, from its MOMENT thought and the inputs.
  Student 1 learns it. It joins Understand as one more cue.
- **What it replaces:** the "after two or three questions" recipe. Her question count stays a
  supporting fact (rhythm) but no longer drives anything; the cue says *why* to ask or not ask.
- **No target rate.** Success is judged by reading conversations and by the measures below, not by
  how often she asks.

## Typed memories, live

Each kind comes from its own source, scoped to this user:

| Kind | Live source | Note |
|---|---|---|
| `about_them` | semantic memories (`semantic_memories`) | facts rewritten with their name as the subject, not "she/he" |
| `shared` | episodic memories of things done together (game session recaps, `EpisodicMemory`) | today's memory block calls game recaps "your own recollections"; they are shared, and are typed as such |
| `her_own` | her recorded experiences: her mind's thoughts and her actions | **new store needed**; only recorded events count; generated "days" are scenario fiction and never used live |
| `her_preference` | her character (`core_memory` v2 `identity.character`) plus a small interests list | versioned with core memory |

**Privacy scope:** her own experiences come only from her own thoughts and from experiences with
*this* user. Nothing from other users' conversations, not even anonymised. Thoughts her mind had
during a session with one user belong to that user's scope.

## Students

- **Student 1** gains the exchange cue output and reads her own rhythm as numbers (the rhythm is
  also a real input to how the exchange is going). Her world and the intention are not Student 1
  inputs: they don't change how a message reads.
- Training data comes from the intention teacher's extracted labels. The pilot's label-first data
  stays usable for the reading cues (acts, tone, request), whose meaning didn't change.
- Evaluated as before: teacher self-agreement is the ceiling, with family hold-outs and connected
  runs. Add the exchange cue to matched contrasts: the same message after "reluctant" vs.
  "exploring" history.

## Measuring it

Offline, never gating:
- **The rater** from the ablation: borrowed memory, invented self, ignored correction, ignored topic
  change, asked already answered, dodged question, repeated herself.
- **The simulated user's hidden reaction** to each reply.
- **Reading whole conversations side by side.**

Live, shadow first: run the path alongside real replies and compare, then typed chat only, then
voice.

## Open questions

1. (a) or (b) for intention-first, after a latency measurement.
2. Where the previous intention lives: dialogue working memory, or a small per-conversation field.
3. The `her_own` store: schema, what her mind writes there, retention.
4. Whether the move labels stay worth extracting live (shadow) at all, or only offline.
5. Ablation outcome: if the intention handoff turns out not to matter, the live path can stay
   single-call without an intention, and the page and typed memories carry the improvement.

## Ablation result and what it changes here

From `docs/qa/intention-teacher-2026-10-19/RESULTS.md` ("Ablation"); 10 scenarios, one run per arm:
- The **character page** (motives, listening, her world) drives most of the improvement: −30 points
  of questions, no interview runs, under either handoff.
- **Speaking from the intention** adds variety (−7 points of repetition) and slightly shorter
  replies; it doesn't fix the interview pattern by itself.
- **Her rhythm** matters mainly with the old page.
- **Typed memory ownership** coincides with borrowed memories dropping to about zero.

Consequences for this design:
1. **Ship order changes.** First the parts that did the work: the page's content and typed,
   scoped memories in the live prompt (both independent of the students). Then Student 1's state
   block and exchange cue. The intention-first step comes last, as an optional variety improvement.
2. **Intention-first should not cost latency.** Its benefit is real but small, so option (a), a
   second call, isn't justified for voice. Use (b), the hidden prefix, or skip it until shadow runs
   show it pays off live.
3. **The rhythm input stays,** as a supporting fact for the exchange cue rather than a rule.
4. **Open question 5 is answered in part:** the handoff isn't the main lever, but it isn't nothing.
   Keep it, measured, rather than build around it.

## Built: first live step (2026-10-19)

Requested 2026-10-19: page and typed memories in the reply model, Student 1's acts/request/tone,
notes from what actually happened, feelings experimental, Student 2 offline; then compare in real
conversations.

| Setting (backend `.env`) | Effect |
|---|---|
| `COGNITIVE_LIVE_CHARACTER=true` | Her page (`app/ai/Cognitive/simple/character/columbina.md`, a copy of page v0.6) and memories typed by whose they are replace the untyped memory block. Game recaps become "what the two of you have done together" (the old block called them "your own recollections"). "Your own experiences" is honestly empty until a store exists. |
| `COGNITIVE_LIVE_STUDENT=on` / `ab` | Student 1 reads each user message (~10 ms on CPU; loaded at startup). Its acts/request/tone go into the prompt as "a quick reading… it can be wrong; the message itself wins" (`on`: always; `ab`: in a random half of conversations, fixed per conversation). Notes run in both halves; feelings and appraisal are logged, never shown. |

- **Code.** `services/cognitive_live.py` (`prepare` before the reply, `after_reply` after it's
  delivered; state in `working_memories.cognitive_state["simple_live"]`, merged under its own key),
  `app/ai/Cognitive/simple/live.py` (Student 1 runtime), new `AgentRuntimeContext` fields and
  prompt sections in `prompts.py` (added only when set, so with both settings off the prompt is
  unchanged). Hooks in `workers/chat.py` and the local fallback in `api/routes/chat.py`.
- **Notes from what happened.** About them: from Student 1's reading of their message. About her:
  "asked" from her reply's text; answered/offered/committed from a small model reading the reply
  after delivery (`prompts/reply_events.txt`, `SUBAGENT_MODEL` by default, ~3 s, off the critical
  path); on failure only "asked" is recorded.
- **Log.** Every turn to `backend/var/cognitive_live.jsonl`: arm, the reading (with probabilities),
  experimental feelings, notes before and after, events, reply, Student 1 latency. Read it with
  `uv run python scripts/cognitive_live_report.py`.
- **Tests.** `tests/test_cognitive_live.py`.

**Known weakness before comparing.** On targeted probes Student 1 misreads exactly the cases that
matter: "ugh you are useless today" → nothing clear, neutral (should be complaining, cold); "can you
set an alarm for 6?" → something she can do by talking (should be outside her ability); "no, that is
not what i meant at all" → sharing (should be correcting her). Its averages pass because ordinary
messages dominate. The prompt tells the reply model the reading can be wrong, but an A/B with this
student may show no gain; the probe cases are a better first check than overall agreement.
