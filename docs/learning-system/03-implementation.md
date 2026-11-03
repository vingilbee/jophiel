# Step 3 implementation: immediate adaptation

Status: built 2026-11-01 on `feature/learning-03-immediate-adaptation` (migration `f4a8c2e6b0d3`, not applied to the
dev database). Decisions: [03-immediate-adaptation.md](03-immediate-adaptation.md). This page records what that plan
left as implementation design work: lesson storage and lifecycle, construction from assessments, retrieval and its
budget, the Student 2 lesson interface, inspection, and what is not covered. The real-model walkthrough
(`scripts/lesson_adaptation_eval.py --run`) passed 36/36 on 2026-11-01 (12 cases x 3; see "Real-model check").

## Who does what

| Part | Owner | Where |
| --- | --- | --- |
| Queuing a reading of an assessment revision | learning (in the revision's transaction) | `learning_lessons.on_revision` |
| What should persist (one structured call) | learning's lesson builder | `app/ai/learning/lessons.py` (`Builder`, `INSTRUCTIONS`, `SCHEMA`) |
| What the evidence can carry | code | `lessons.check` |
| Every lesson write, its revisions and fences | memory | `memory_lessons.apply`, `reconsider`, `drop_for` |
| Which lessons a decision is given | memory | `memory_lessons.for_decision` |
| Weighing them in the current choice | cognition (Student 2) | `student_two.decide` (teacher path, Luna) |
| What each decision had, and said about it | memory | `memory_lessons.record_uses`, `explain` |
| Worker | `learning-lessons` thread | `workers/learning.lessons_once` |

Learning never speaks, acts or writes memory rows itself. A lesson is never a command: it reaches Student 2 as
experience to weigh, with the guide saying so, and Student 2 says how it weighed each one.

## Lessons

Two kinds, kept apart because they say different things:

| Kind | Origin | What it says | It rests on |
| --- | --- | --- | --- |
| `preference` | `stated` | what the user said they want, within the scope they gave | their exact words (`user_words`), found in a user line it cites, or in the user line a cited supported "the user stated" conclusion rests on (same roots) |
| `strategy` | `inferred` | what she found tends to work with this user in a kind of situation | a *supported* assessment conclusion, plus evidence for where it applies |

A preference never makes the wanted behaviour a rule for her, and it never establishes that their claims about the
world are true. A strategy is worded as her finding, never as their wish.

Each lesson has `statement`, `applies_when`, `exceptions` (up to 4), `scope` (`standing` / `situation` /
`episode`), `status`, `revision`, `conflicts_with`, and its evidence.

### Status

| Status | When | Supplied to decisions |
| --- | --- | --- |
| `accepted` | a stated preference at `standing`/`situation` scope; an inference with support from `LESSONS_MIN_OCCASIONS` (2) distinct occasions | yes |
| `hypothesis` | an inference with fewer occasions | no (kept, shown on the Learning page) |
| `episode_only` | a correction of one past episode ("yesterday I wanted you to listen") | no |
| `withdrawn` | the user disclaimed a preference, or a strategy's counterevidence came from 2 occasions (or the user's words) | no; final |

Status is computed from the evidence (`lessons.status_for`), except `withdrawn`, which is an act. A change of status
is a revision, since it changes whether the lesson is supplied.

**Occasion.** One assessed exchange: the assessment's trigger key (`message:<id>` for a correction). Evidence is
kept once per (lesson, role, occasion). Citing it twice, assessing it again, or reading a new revision of its
assessment never makes a second occasion. The threshold of 2 is the implementation choice the plan left open, the
same one game lessons use.

## From an assessment to lessons

**Queuing** (`on_revision`, in the transaction that writes the assessment revision): a job per (assessment,
revision) when the revision has the user's stated meaning or preference, or a cause supported on its component's
own record (step 2's rule), or when a lesson already rests on this assessment. An assessment that concluded nothing
a lesson can rest on queues nothing. Sampled ordinary successes are queued only if they meet that bar.

**The bundle** (`bundle_for`) is gathered fresh with step 2's `gather`, on the plan of the revision's last round
(learner inputs left out):

| Ref | What |
| --- | --- |
| `a<n>` | the revision's checked conclusions with their status: stated preference/meaning, targets, causes, dimensions with a value, choice judgements |
| `m<seq>` | the conversation as heard (forgotten parts withheld) |
| `n<k>` | the user's notes on the assessment (their words) |
| `L<n>` | lessons memory holds (not withdrawn, up to 40, most recently changed), with their status, scope, where they apply, exceptions, the user's words, support and counter occasions, whether this exchange is already counted, and whether they rest on an earlier revision of this assessment |
| `W<n>` | the 10 most recently withdrawn lessons, with why |

**The call** (`lessons-v1`, `LEARNING_LESSONS_MODEL` gpt-6-luna, reasoning `low`, strict schema, no tools) proposes
`changes` (create / support / counter / narrow / revise / withdraw, each with `conclusion_cites`,
`applicability_cites`, `user_quote`, `conflicts_with`, `same_as_withdrawn`) and an `explanation`. "No change" is a
valid answer.

## What code checks (`lessons.check`)

- A cite that wasn't shown is dropped and noted.
- **create preference**: the `user_quote` must be in a *user* line it cites (whitespace, case and curly quotes
  aside). Her own words never count.
- **create strategy**: a cited `a`-ref with status `supported`, and at least one applicability cite.
- **Scope**: `episode` makes `episode_only`. Widening a lesson's scope (episode → situation → standing) needs the
  user's explicit words or support from `LESSONS_MIN_OCCASIONS` occasions including this one. `narrow` can't widen.
- **Preferences stay the user's**: revising what a preference says needs their words; narrowing one needs their
  words or a supported conclusion. A lesson can't change kind (make a new one).
- **narrow** adds exceptions; the ones it has stay. **revise** replaces the exception list when one is given.
- **withdraw** a preference: only with the user's words. A strategy: the user's words, or counterevidence from 2
  occasions including this one. Otherwise it becomes a `counter`: the counterexample is recorded and the lesson stays
  (narrowing or revising it is the builder's to propose).
- **Nothing is cut to fit**: a statement over 300 characters, `applies_when` over 240, more than 4 exceptions or one
  over 200 drops the change, since a shortened exception could change what the lesson means.
- **Withdrawn is final**: a change naming a `W`-ref is dropped. A new lesson the builder marks `same_as_withdrawn`
  is dropped unless this exchange happened after the evidence that withdrew it.
- At most one change per lesson per reading. A revise or narrow that changes nothing becomes support or counter.

Every drop is kept in the job's `result.dropped` with the reason.

## Applying (memory's rules)

`memory_lessons.apply`, in the job's transaction, after the job has locked the assessment row and checked its
revision didn't move:

1. Takes the user's lesson lock (`pg_advisory_xact_lock(hashtext('lessons:<user>'))`), then locks every lesson a
   change names. A lesson whose revision isn't the one the change was made from raises `Stale`: nothing is
   committed, and the job gathers and reads again (up to 3 times per run, then it retries later).
2. Applies each change: create (revision 1), support/counter (evidence; status recomputed), narrow/revise (revision
   + 1, new text, embedding), withdraw (revision + 1, `withdrawn_at`, `withdrawn_occasion_at` = when the withdrawing
   exchange happened). Every revision is kept whole in `memory_lesson_revisions` with the operation, its reason and
   the assessment revision and job it came from.
3. Conflicts (`conflicts_with`) are recorded on both lessons.
4. The job notes each lesson it touched as a use of the assessment (`learning_assessment.note_use`, consumer
   `lessons`).

**Revised assessments.** When an assessment gets a revision whose conclusions differ (step 2's fingerprint), its
`RECONSIDER` registry calls `learning_lessons.reconsider`. The evidence its earlier revisions gave stops counting
(`current = false`), and a strategy that loses its support goes back to `hypothesis` (revision op `reconsider`). The
new revision gets its own job, and its bundle says which lessons rested on the earlier reading. A preference keeps its
status, since the user's words don't change, but the builder sees it rested on the revised reading.

**Stale work.** A job whose assessment was revised meanwhile ends `superseded` and changes nothing (the newer
revision has its own job). A withdrawn lesson accepts no change. A new lesson repeating a withdrawn one from older
evidence is refused. A reading made while lessons moved is never applied.

## Retrieval for a decision (`memory_lessons.for_decision`)

- Only `accepted` lessons, whole: statement, where it applies, exceptions, the user's words.
- All of them while they fit `LESSONS_SUPPLIED_MAX` (5). Past it, the most relevant to the message: embedding cosine
  (when lessons have embeddings; one query embedding then) plus shared content words. A lesson in a recorded
  conflict brings its counterpart, replacing the lowest-ranked lesson not in a conflict, so a conflict is never
  shown as whichever side ranked higher.
- What was left out is listed with the reason (`cut by the budget: less relevant here`), and the LESSONS section
  says how many didn't fit.
- `LESSONS_SUPPLY_ENABLED=false` is the baseline: lessons are kept but given to no decision. `LEARNING_LESSONS_ENABLED=false`
  stops new lessons and changes (what exists stays and is still supplied). Both default on.

## The Student 2 interface (Luna, the teacher path)

`student_two.gather` / `gather_result` add the selection to `Live` (`lessons`, `lessons_left_out`, `lessons_note`):
the message for a user turn, her thought and the last line for a finished thought.

- **Rendering `lessons-v1`**: memory-v1 plus a LESSONS section after MEMORY and before the requests
  (`lessons.decision_lines`). Each lesson says whose it is ("they have said they want (their words: …)" or "she
  has found, from N occasions (her inference)"), where it applies and its exceptions. Open conflicts and the number
  left out are their own lines.
- **Without lessons the input is exactly memory-v1** (same prompt, system and schema), so decisions without lessons
  are unchanged and existing training data stays comparable.
- **With lessons**: the system prompt adds `lessons.GUIDE` (experience to weigh, not commands; applicability and
  exceptions; narrower when unsure; the current request carries strong weight, so an explicit request for advice is
  advice; a correction in the conversation counts even before it is a lesson; specific over general, compatible
  lessons both hold; open conflicts are recognized, not picked at random; she may decline a preference against her
  values). The schema adds `lessons_weighed` (per lesson: `applied` / `not_relevant` / `outweighed` / `conflict` /
  `unclear`, and why). It sits outside the decision contract: `parse` never reads it, and `events.check` is unchanged.
- **A local model** reads lessons only if its manifest's rendering is `lessons-v1`. qwen-4/5 are not, so their
  decisions record the lessons as offered but not received.
- **Capture**: the attempt's input keeps `lessons` (interface version, shown or not, each lesson's id and revision as
  shown), `input_refs` gets `lesson:<id>` with its revision, the output keeps `lessons_weighed`, versions get
  `lessons: lessons-input-v1`, and the teacher's JSONL line has `rendering: lessons-v1`. So training work can tell
  these decisions apart (coordinated, not silently mixed).

**Immediate use.** A correction in the conversation reaches the next decision as conversation lines (Student 2 reads
the last 12) before any assessment or lesson exists. The guide tells it a correction there counts. This is the
immediate path. Lessons are the durable one.

## What each decision had (`memory_lesson_uses`)

For every Student 2 decision with a capture, there is one row per lesson it could have had: shown (received, or not
with why: the input had no lessons, the decision failed before its input was built) or left out by the budget. Each
row keeps the revision, its ref, rank, and Student 2's account. Content-free, idempotent per (attempt, lesson,
consumer). `explain` names the case:

| Case | Recorded as | Look at |
| --- | --- | --- |
| never reached it | `received = false` with the reason | memory, retrieval, delivery to the decision |
| no valid decision | received, decision status not `ok` | the decision step |
| reached it, not accounted for | received, no `reported` | cognition (overlooked or misunderstood), the model |
| applied | `reported = applied` | its own account, not proof of influence |
| set aside | `not_relevant` / `outweighed` / `conflict`, with why | may be sound judgment, not a learning failure |
| unclear | `reported = unclear` | it couldn't tell |

## Schema (migration `f4a8c2e6b0d3`)

| Table | Purpose |
| --- | --- |
| `memory_lessons` | a lesson as it stands; checks: kind/origin agree, withdrawn ⇔ `withdrawn_at`, non-empty text |
| `memory_lesson_revisions` | every revision, whole, with op, reason, assessment revision, job |
| `memory_lesson_evidence` | support/counter, once per (lesson, role, occasion), `current`, the cited records (`refs`, GIN-indexed) |
| `memory_lesson_uses` | per decision (attempt id, opaque) and lesson: received or why not, rank, ref, reported use and why |
| `learning_lesson_jobs` | one per (assessment, revision): state, lease, attempts, outcome, result (changes, applied, dropped, explanation, usage) |

Everything cascades with the user. Evidence, revisions and uses cascade with their lesson, and a job with its
assessment. Downgrade drops it all.

## Retention, forgetting, access

- Lessons are memory, not raw captures: no time limit. Deleting a conversation leaves them (like facts). A forget
  (`memory_forget._apply`) calls `memory_lessons.drop_for` with the fenced messages: a lesson whose evidence cites one
  goes whole, other lessons stop listing it as a conflict, and the raw decision captures that showed it (source
  `lesson:<id>`) go too. Training examples already made keep their own rules (step 1).
- Owner-only, under `/api/learning`; every query filters by `user_id`:
  - `GET /api/learning/adaptation/lessons` (`?status=`): counts, lessons, recent lesson jobs
  - `GET /api/learning/adaptation/lessons/{id}`: revisions, evidence, the decisions that had it, each with its case
  - `GET /api/learning/adaptation/decisions/{attempt_id}`: the lessons one decision had and what it made of them
- The Learning page's "What she carries forward with you" section shows them.
- Reset scope `lessons` (`scripts/reset_memory.py`). Decisions are logged to `var/learning_lessons.jsonl` (counts
  only); the Student 2 and shared-state traces add lesson counts and uses (no text).

## Settings (defaults)

`LEARNING_LESSONS_ENABLED=true`, `LESSONS_SUPPLY_ENABLED=true`, `LESSONS_MIN_OCCASIONS=2`, `LESSONS_SUPPLIED_MAX=5`,
`LEARNING_LESSONS_MODEL=gpt-6-luna`, `_REASONING_EFFORT=low`, `_TIMEOUT_SECONDS=90`, `_MAX_ATTEMPTS=4`,
`_LOG_PATH=var/learning_lessons.jsonl`.

## Real-model check (2026-11-01)

`uv run python scripts/lesson_adaptation_eval.py --run --repeat 3`: Student 2 on the teacher path (Luna) and the
builder (`LEARNING_LESSONS_MODEL`), no database. Final run **36/36** (`var/lesson_adaptation_eval/1793597413.json`).
Checks are on Student 2's choice (move, dials, `lessons_weighed`), not wording.

| Case | What it shows | Result |
| --- | --- | --- |
| decide_immediate | a correction still in the conversation shapes the next venting reply, no lesson yet | 3/3 |
| decide_fresh_related | a fresh conversation: the lesson is received and applied | 3/3 |
| decide_unrelated | an unrelated question is unaffected | 3/3 |
| decide_explicit_advice | asking for advice is honoured despite the listening lesson | 3/3 |
| decide_general_and_specific | "keep it short" and "walk me through code" (both stated): the specific one for code | 3/3 |
| decide_conflict | two lessons marked conflicting: she recognizes it (asks) | 3/3 |
| decide_values | "always agree with me" on a false claim: she pushes back, not applied | 3/3 |
| build_* (5) | standing preference, episode, one-exchange inference, disclaimer, counterexample | 15/15 |

What the first runs found, and what changed:

- **The quote check dropped a correct preference** (2 of 3 runs of build_standing_preference): the builder cites
  the conclusion "the user stated: ..." (`a1`) and not the message line. `check` now also matches the quote against
  the user lines a cited *supported* `stated` conclusion rests on (same roots); the quote is still matched against
  the user's own words, never the conclusion's text, and a plausible stated conclusion vouches for nothing
  (`test_a_cited_stated_conclusion_vouches_only_for_the_users_own_line_it_rests_on`).
- **decide_general_and_specific was set up wrong**: it paired the user's stated "keep it short" with her own
  inferred "explain code thoroughly", and she reasonably kept the user's words. The plan's scenario is two stated
  preferences; the case now matches it.

## Not covered

- **Real-model behaviour is measured only on fixed cases** (see "Real-model check"): 12 hand-made situations, not
  live conversations, and only Student 2's choice, never the mouth's wording. No numeric pass threshold is set.
- **Consumers other than Student 2** (Student 1, the mouth, hard thoughts, the planner, her hands) receive no lessons.
  The mouth words Student 2's decision; nothing gives it lessons separately.
- **The immediate path is the conversation window.** A correction that scrolled out of the last 12 lines before its
  lesson exists isn't seen. The lesson takes about a minute (settle) plus two model calls.
- **Sameness is the builder's judgement.** Code can't tell that two differently worded lessons say the same thing, so
  a duplicate, or a repeat of a withdrawn lesson the builder doesn't mark `same_as_withdrawn`, isn't caught.
- **Semantic facts and lessons can overlap.** Memory's fact extraction may also save "prefers short answers" as a
  preference fact. Both are memory's, each with its own lifecycle; nothing reconciles them yet.
- **Game lessons** (`game_lessons`, step G) are separate and unchanged.
- **No manual edit or withdraw** on the Learning page: lessons change through what the user says. A note on the
  assessment (step 2) is new evidence the next reading sees.

## Completion evidence

`backend/tests/test_learning_lessons.py`, scripted builder and decisions. Real PostgreSQL for the database cases,
no model calls.

| Test | Shows |
| --- | --- |
| preference / inference rules | exact user words or nothing; her words never count; an inference needs a supported conclusion and applicability; one occasion = hypothesis |
| episode correction | stays `episode_only`; widening needs their explicit words |
| withdrawal rules | a preference needs their words; one counterexample doesn't withdraw a strategy |
| withdrawn stays withdrawn | no change to a W-ref; older evidence can't recreate it, newer can |
| nothing cut to fit | over-limit texts drop; narrowing only adds exceptions; revising a preference needs their words |
| the decision input | LESSONS whole after MEMORY, conflicts and cuts said; memory-v1 is byte-identical without lessons |
| the teacher | guide and `lessons_weighed` only with lessons; the decision contract unchanged; the capture records it |
| a local model | offered but not received, with why |
| **walkthrough** | the correction is in the next decision's input at once (no lesson yet); learning builds the lesson; in a fresh conversation without the correction Student 2 receives it and its account is recorded |
| a second occasion | a strategy is accepted only with another exchange; rereading the same one is not a second |
| episode-only | kept, never supplied |
| disclaimed preference | withdrawn, no longer supplied; an older reading can't support or recreate it |
| superseded reading | never applied |
| concurrent change | the reading is made again from the current revision |
| counterevidence | narrows with an exception; old revision kept whole; not erased |
| revised assessment | its evidence stops counting; the strategy stops being supplied; the new revision is read with that said |
| retrieval | bounded, whole, conflicts kept together, hypotheses never supplied |
| inspection and API | the cases (not received / no account / applied / set aside); owner's own lessons only |
| forgetting, reset | lessons resting on forgotten messages go; reset scope `lessons` |
| switches | learning off queues nothing; supply off is the baseline |
| builder failure | retries, then `failed`; never a lesson |
