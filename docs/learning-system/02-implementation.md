# Step 2 implementation: evidence and attribution

Status: built 2026-11-01 on `feature/learning-02-evidence-attribution` (migration `e3f9b5d1a7c4`, not applied to the
dev database). Decisions: [02-evidence-and-attribution.md](02-evidence-and-attribution.md). This page records the
implementation details that plan left open (triggers and sampling, the evaluator and storage contracts, what code
checks, stopping, revision and dependents) and what is not covered. The real evaluator has not been run on the
fixed cases yet (`scripts/learning_assessment_eval.py --run`).

## Who does what

| Part | Owner | Where |
| --- | --- | --- |
| Triggers from observations (code only) | the `learning` observation consumer | `learning_assessment.triage` |
| Triggers that aren't observations, late captures | the sweep, every 30 s | `learning_assessment.sweep` |
| Gathering the evidence | code | `learning_assessment.gather` |
| Meaning and proposed causes | the evaluator (one structured call per round) | `app/ai/learning/assessment.py` |
| Checking what came back | code | `assessment.check` |
| Revisions, links, dependents | code | `learning_assessment._write` |
| Acting on a conclusion | each owner, through its own rule | today: `training_examples.flag_for_review` / `reconsider` |
| Worker | `learning-assessment` thread | `workers/learning.assess_once` |

Learning never speaks, acts or writes memory. A proposed clarification question is stored on the assessment and
shown on the Learning page; it is **not** handed to cognition yet (see "Not covered").

## Triggers

| Trigger | Kind | Key (one assessment per key) | Source |
| --- | --- | --- | --- |
| A user message Student 1 read as `correcting_her` | `correction` | `message:<id>` | observation `conversation.user_message` |
| … read as `complaining` | `complaint` | `message:<id>` | same |
| Any other user message, sampled | `sample` | `message:<id>` | same; `sha256(message id) < LEARNING_ASSESSMENT_SAMPLE_RATE` |
| A spoken reply cut off | `execution` | `heard:<message>` | observation `conversation.reply_interrupted` |
| A failed outside-channel delivery | `execution` | `delivery:<message>` | observation `conversation.delivery_status` |
| A failed skill run (repeats on the same step join it) | `execution` | `skill:<session>:<step>:<skill>` | observation `minecraft_pack.skill_outcome` |
| A failed or cut-off reply | `execution` | `reply:<turn>` | sweep over `decision_executions` |
| A failed effect | `execution` | `effect:<execution>` | sweep over `decision_executions` |
| Memory read a message as correcting an earlier statement (`kind=mistaken`) | `correction` | `message:<id>` (joins it) | sweep over `memory_corrections` |

- A message is skipped when nothing of hers comes before it in the conversation.
- **Waiting for Student 1.** If Student 1's capture of the message hasn't been saved, the receipt waits (15 s
  retries, not counted as failures) up to `LEARNING_ASSESSMENT_TRIAGE_WAIT_SECONDS` (120). After that the message
  can only be sampled, and the trigger says Student 1's reading wasn't captured.
- **A stronger trigger upgrades the kind**: a sampled message that memory later reads as a correction becomes a
  `correction` (`trigger.was` keeps the earlier kind).
- **Settle.** A new or reopened assessment waits `LEARNING_ASSESSMENT_SETTLE_SECONDS` (60) before it runs, so a
  burst of failures is one run and the reply to a correction can be part of it.
- **Start.** The sweep never imports records older than the moment assessment began (an `observation_adapters`
  row named `learning_assessment`), nor older than raw-capture retention. Observation receipts exist only for
  reports recorded after this change.
- **Sampling** is 10 % by default. It is the implementation decision the plan left open; ordinary successes are
  in it so learning isn't restricted to problems.

## The evidence bundle

Everything is rendered for the evaluator with refs it must cite (`app/ai/learning/assessment.render`):

| Ref | What | Checked by code |
| --- | --- | --- |
| `m<seq>` | a conversation line as heard (`memory_forget.memory_view`: interrupted replies cut, forgotten spans withheld) | speaker, time, stored status, heard characters, "this started it" |
| `t<seq>` | a turn of hers (a target), labelled with Student 2's intention | — |
| `t<seq>.s1` / `.s2` | Student 1's reading / Student 2's decision, or *not captured* with why (lost in capture with the gap reason, expired, never saved) | the latest try; earlier tries noted |
| `c<n>` | facts: delivery result, what was heard, channel status, the mouth speaking despite silence, Student 2's fallback, a failed effect, a capture gap, a quote of her words, a memory correction | each says what it **establishes** and what it **does not establish** (the domain measurement and its limit) |
| `h<n>`, `h<n>.d` | her hands' decision behind a failed skill run (a target), and its record | — |
| `g<id>` | a game outcome: skill, params, status, the game's reason, what was counted while it ran | — |
| `n<k>` | the user's notes on the assessment | their exact words |
| `p<k>` | other unresolved assessments in the conversation (not evidence: only for `reopens`) | — |

The initial window is `LEARNING_ASSESSMENT_WINDOW_MESSAGES` (12) lines up to the trigger. Possible looks are
listed as "What more could be looked at": `earlier` (the window doubles), `later` (up to 6 later messages),
`inputs` (the exact input each captured learner was given, 4000 characters each), `game` (12 more earlier game
outcomes per step). Later messages attached as evidence are always shown.

## The evaluator's contract (`assessment-v1`)

One strict JSON-schema call (`LEARNING_ASSESSMENT_MODEL`, default gpt-6-luna, reasoning `low`, no tools):
`feedback` (yes/no/unclear), `targets` (ref, `also` for a sequence, status, cites, why), `causes` (target,
component, status, cites, why), `user_stated` (intended meaning or preference, exact quote), `dimensions` (the six,
value good/poor/mixed/neutral/unknown, status), `choice_vs_outcome` (choice sound/poor/unclear apart from outcome
good/bad/mixed/unknown), `missing`, `clarification`, `reopens`, `next` (conclude / look_further with looks /
need_missing) and a 2-5 sentence `explanation`.

Components: `student_1` (interpretation), `student_2` (choice), `mouth` (wording), `delivery`, `timing`, `hands`,
`skill`, `planner`, `memory`, `environment`, `user_side` (nothing on her side), `unclear`. Statuses:
`supported`, `plausible`, `unresolved`, `contradicted`; no percentages.

## What code checks (`assessment.check`)

- A ref that wasn't shown is dropped; an item left with no evidence becomes `unresolved` (or `unknown` for a
  dimension or outcome). Everything set aside is kept in the revision's `dropped` with the reason.
- **Target**: `supported` needs a cite of something of the target itself (its reply, its records, its facts);
  otherwise `plausible`. Several candidate targets are all kept.
- **Cause**: `supported` needs the component's **own record**, captured and cited:
  Student 1 / Student 2 / hands: their attempt; mouth: her reply *and* Student 2's captured decision (else wording
  can't be told from choice); timing: her reply; delivery: a delivery fact; skill: a skill outcome; environment: a
  game observation; user_side: something the user said. Planner and memory are never captured for assessment, so
  a cause on them is at most `plausible`/`unresolved`.
  - Own record missing → `unresolved`, `basis: missing_capture`, and what's missing is added. This is missing
    capture, kept apart from `contradicted` (conflicting evidence).
  - Own record captured but not cited → `plausible`, `basis: not_its_own_record`. So a user's correction alone,
    or a delivery failure alone, never establishes a Student 1 or Student 2 error.
  - `unclear` is never `supported`.
- **The user's words**: a `user_stated` item is kept only when its quote is exactly in a user line or note it
  cites (whitespace, case and curly quotes aside), and is then `supported`: the user is the authority on what they
  meant and prefer. Her own words never count.
- **Choice quality** needs the decision record (Student 2's, or her hands'), captured and cited, else `unclear`.
- **Independence**: each citable item names its occurrence (`event`: a message, a turn of hers, a skill run, a
  note). A conclusion's `independent` count is distinct events: citing a message three times, the memory
  correction read from it and a quote found in it is one event.
- **Fingerprint**: conclusions compared by the records they're about and their statuses, never by ref labels or
  wording.

The assessment's headline keeps the two judgments apart: `target_status` and `cause_status` (the best status
among each), plus `outcome`: `concluded` (at least one supported conclusion: a target, a cause, a stated
meaning or preference, a dimension), `no_feedback`, or `unresolved`.

## Stopping (no round budget)

Each round reads the bundle as gathered now. It stops when:

| Stop | When |
| --- | --- |
| `concluded` | the evaluator says `conclude` |
| `missing_evidence` | it says `need_missing`, or the first bundle is more than `LEARNING_ASSESSMENT_MAX_CHARS` (60000) |
| `stuck` | what it asked to look at adds nothing new (the rendered evidence is identical), it asks for nothing available, or looking further would be more than can be read at once |

Every look only adds, and the conversation and session are finite, so this always ends. A failed call isn't a
conclusion: the assessment retries with backoff (30 s × 2ⁿ, at most 1 h) and is `failed` after
`LEARNING_ASSESSMENT_MAX_ATTEMPTS` (4). A run holds a 10-minute lease; a worker that dies leaves it to be taken again.

## Revisions and dependents

- **Revision** = an evaluation that read different evidence. The same evidence read again (same digest, same
  prompt version) is *not* a revision: reflecting again is not evidence. The previous revision is kept, with
  `superseded_at` / `superseded_by`; its reason names the new evidence (`new evidence: attempt:<id>`).
- **New evidence reopens** (`epoch` + 1; a run already in flight is followed by another):
  a missing capture that arrives (`awaiting` lists the attempt ids still expected; the sweep checks them),
  a memory correction of the message, a later message another assessment named in `reopens`, the user's note.
- **Dependents.** `learning_assessment_uses` records conclusions an owner acted on. Today the only one: a
  supported cause on Student 2 for a turn whose decision became a training example flags that example
  `needs_review` and ineligible (the training owner's rule, like a memory correction; it is kept). When a later
  revision no longer supports it, the use becomes `reconsider` and the owner is told (`training_examples.reconsider`
  adds a note; the example stays under review: review decides). Owners register in `RECONSIDER`.
- Raw records are never rewritten.

## Schema (migration `e3f9b5d1a7c4`)

| Table | Purpose |
| --- | --- |
| `learning_assessments` | trigger kind/key/detail, conversation, game session, trigger message, job state and lease, epoch, current revision and its headline, `awaiting` |
| `learning_assessment_evidence` | evidence attached beyond the window, once per ref (corrections, messages, late captures, notes) |
| `learning_assessment_revisions` | every evaluation: evaluator and prompt version, reason, input digest, fingerprint, checked result (with target refs and labels), what code dropped, each round, outcome, stop, independent events, explanation, tokens, superseded |
| `learning_assessment_links` | per revision: targets (all their stored refs, with status), causes (the component's own attempt, with component and status), everything cited; opaque refs, indexed for lookup |
| `learning_assessment_uses` | conclusions an owner acted on, `current` / `reconsider` |

Everything cascades with the user; assessments also with their conversation, game session and trigger message.

## Retention, forgetting, access

- Assessments are learning's derivatives, not raw captures: they have no time limit. They go with their
  conversation, session, trigger message or user.
- A forget (`memory_forget._apply`) calls `learning_assessment.drop_for` with the fenced messages: assessments
  that cite, attach or start from one go, and so does every assessment in that conversation from that message on
  (a window may have shown it uncited), and every earlier one there that looked at later messages. Later bundles
  show forgotten text as withheld.
- Owner-only, like the rest of `/api/learning`; every query filters by `user_id`.
  - `GET /api/learning/assessments` (`?outcome=`): counts and recent ones
  - `GET /api/learning/assessments/{id}`: evidence, every revision, the current links, uses
  - `POST /api/learning/assessments/{id}/notes`: the user's correction, as new evidence; assessed again at once
- The Learning page's "How it went" section shows them, unresolved ones filterable, with evidence, history,
  what code set aside, and a note box.
- Reset scope `assessment` (`scripts/reset_memory.py`). Decisions are logged to `var/learning_assessment.jsonl`
  (counts only).

## Settings (defaults)

`LEARNING_ASSESSMENT_MODEL=gpt-6-luna`, `_REASONING_EFFORT=low`, `_TIMEOUT_SECONDS=90`, `_SAMPLE_RATE=0.1`,
`_SETTLE_SECONDS=60`, `_TRIAGE_WAIT_SECONDS=120`, `_WINDOW_MESSAGES=12`, `_MAX_CHARS=60000`, `_MAX_ATTEMPTS=4`,
`_LOG_PATH=var/learning_assessment.jsonl`.

## Not covered

- **The real evaluator's quality is unmeasured.** `scripts/learning_assessment_eval.py --run` reads 10 fixed
  cases (bad wording, delivery failure, unwanted advice, misread sarcasm, delayed feedback, ambiguous target,
  missing decision, ordinary success, lucky success, game failure) and scores missed attribution, unsupported
  blame and allowed outcomes; it makes real model calls and has not been run. No numeric pass threshold is set
  (the plan leaves it open).
- **Clarifications aren't handed to cognition.** They are stored and shown only; giving cognition a way to
  decide whether to ask needs its own design (it would make her speak on learning's behalf).
- **Triggers rely on Student 1.** A correction Student 1 doesn't read as `correcting_her`/`complaining` is only
  assessed if sampled or if memory reads it as a correction.
- **Not captured as learners**: the planner, the mouth's wording (only its words are seen), hard thoughts and
  the VM key-pressing driver; causes on them can't be established.
- **No other dependents yet.** Steps 3 and 5 own lessons and training data from assessments; they register as
  owners when built.
- **Interpretations of one event** are counted as one, but conclusions across *different* assessments of the same
  event are not compared.

## Completion evidence

`backend/tests/test_learning_assessment.py` (real PostgreSQL for the database cases, scripted evaluator, no model
calls):

| Test | Shows |
| --- | --- |
| unknown refs, a correction alone | unshown refs dropped; Student 1 blamed on the user's words alone stays plausible; Student 2 with its record stays supported |
| missing capture | missing Student 2 record: its cause and the mouth's are unresolved with what's missing; contradicted kept distinct; the stated preference is still supported |
| quotes, delivery record | invented or her own words are not the user's; delivery blame needs a delivery record |
| dimensions, lucky success | unknown unless cited; choice apart from outcome; no decision record, no choice judgement |
| independence, rewording | three cites of one message plus its correction count once; reworded conclusions have the same fingerprint |
| bad wording | mouth supported, Student 1 contradicted; links to the turn and attempts; facts say what they establish |
| delivery failure | found by the sweep once; delivery supported, Student 1/2 only plausible |
| unwanted advice | Student 2 supported; exact quote kept, invented dropped; its training example flagged; a note revises it, the old revision is kept and superseded, the flag is told to reconsider |
| delayed and sequence feedback | the quoted earlier turn, not the last reply; a sequence links its other turns |
| ambiguity | two plausible targets, unresolved, missing evidence, a proposed question |
| incomplete capture | attribution withheld; the capture arrives late, the sweep reopens it, revision 2 supports it |
| repeated reflection, reprocessing | same evidence: no revision; evidence attached once; independent count unchanged |
| stuck | looks earlier until the conversation's start, then stops; asking for nothing new stops at once |
| evaluator failure | retries, then `failed`; never a revision |
| reopening | an unresolved assessment reopened by a later message another assessment found relevant |
| triggers | correction and complaint from Student 1; waiting for its capture without failing; sampling; a memory correction upgrades and joins the message's assessment |
| game | a failed skill run targets her hands; repeats join one assessment; the planner can't be established |
| forgetting, deletion | forget and conversation deletion remove assessments |
| API | owner's own records only; a note reopens |
| eval scoring | the real-model eval catches blame, misses, and always answering unresolved |
