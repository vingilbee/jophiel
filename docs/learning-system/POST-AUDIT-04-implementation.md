# Post-audit 4 implementation: broader learning products

Branch: `feature/broader-learning-products` (from `feature/broaden-experience-understanding`). Built on request on
2026-11-05. Design: [broader learning products](POST-AUDIT-04-broader-learning-products.md). Migration
`b4e8d2a6f1c3`.

**Status in one line:** the product mechanics are built and covered by scripted contract tests, including the
required paused-reassessment integrity test. The reserved real-model acceptance was run once and **did not pass** (section 6: 2 of 8 families). Full
acceptance stays **blocked** on the declared Student 1 dependency: interpretation has no lesson input, and every run
records it as uncovered.

## 1. Inventory before coding (sequence step 1)

| Found | Consequence |
| --- | --- |
| Lessons (`memory_lessons`) had no relation to any other memory; reconciliation was local to lesson comparison, thought-item reconciliation and follow-up identity. | Added links owned by memory (below). |
| The reviewer could relate findings only to lessons; memory records came back from requests but couldn't be related to, and a memory item resting on her own thought was citable as evidence. | Findings relate to memory records too; her own inferences are never citable (closing a C1 path). |
| Lessons were shown to the reviewer only when it asked. | Ordinary experience couldn't check an expectation it didn't think to ask about: up to 3 relevant lessons are now shown by relevance. |
| No dependency between products; a premise changing reached nothing. | Basis links + an unsettled state computed at read time. |
| Same-evidence reconsideration had no entry point (an unchanged wake never calls a model, correctly). | Critiques from her mind open a reconsideration of one lesson; repeats are deduped. |
| Prepared supplies (Student 2's `Live`) were delivered as gathered. | `memory_lessons.current` refreshes a supply right before delivery. |
| Student 1 has no lesson input (known gap). | Unchanged: recorded as uncovered; blocks full acceptance. |
| Dev database is at `f2a6c8e1b4d9`: neither step 3's `a3d7f1c9e2b6` nor this migration is applied. | Live operation of step 3 and 4 is not established; see cutover. |

## 2. What was built

| Product semantics | Where | Code guarantees (integrity, never meaning) |
| --- | --- | --- |
| **Links** between a lesson and another memory (`memory_lesson_links`): `depends_on`, `builds_on` (its basis), `same_as`, `distinct`, `supports`, `challenges`, `related` (context). | `lessons.check` (`links`), `memory_lessons._relate` | Only targets that were shown; one per target; never support; no basis cycle (a lesson can't rest on itself); made after every change of that reading. |
| **Carrying a hypothesis forward**: a lesson that says what one of her open hypotheses says can carry it (memory closes the item `unresolved`, pointing to the lesson). | `carries`, `memory_lessons._carry` | Only her own open hypotheses; memory's `resolve` with a revision fence; lessons lock then memory lock (nothing takes them the other way). Facts are never changed by learning. |
| **Derived possibilities and methods**: `details.untested`, `tried` (`untried` / `tried`), `needs` (including a move she lacks). | lesson `details`, finding `details` | `tried` needs a cited record of her doing something; an untested, untried lesson can't be established. |
| **Checkable expectations**: `would_support`, `would_challenge`; the reviewer's `conditions_differ` relation; the builder's `note` op. | `note` -> evidence role `noted` | A noted occasion counts for nothing and makes no revision; coverage is shown (supported / against / noted). |
| **Dependent understanding**: a lesson is UNSETTLED when a basis target's revision or status differs from what the link recorded, or the target is gone. A lesson whose premise lesson is itself unsettled is unsettled too (followed 3 deep). | `memory_lessons.premises`, `unsettled_lines` | Computed from the stored state at every read: visible the moment the premise change commits, before any job runs, after a restart. Every consumer path renders it (`decision_lines`, the reviewer, the builder, the trial proposer). |
| **Reassessment** of a lesson whose premise changed, and **reconsideration** after her mind's critique, on the evidence it already rests on. | `learning_lessons.sweep_premises`, `critique`, `process_reconsider`; `lessons.check_reconsider`; `memory_lessons.reconsidered` | No evidence added, nothing counted; certainty can't rise, scope can't widen, a stated preference can't be revised or withdrawn here; `keep` makes no revision and settles the premises read; revisions are marked `prompted_by` critique / premise. Once per premise state; once per (lesson, critique words). |
| **Her mind's critique**: a hard thought's `reconsider` field names a lesson it read (`L2`) and why. | `app/ai/mind/hard.py`, `mind_hard._critique` | Only a lesson in its own input; her thought prompts the reading, never evidence. |
| **Stale prepared supply**: refreshed before delivery; one no longer supplied is left out, said; when currency can't be confirmed, no lessons go out. | `memory_lessons.current`, `student_two._current_lessons` | |

Consumers: Student 2, conversation mind, Minecraft mind, planner, hands and the trial proposer all render the new
fields through `decision_lines` (or the trial proposer's own line). `INTERFACE_VERSION` `lessons-input-v4`, builder
`lessons-v3`, reviewer `assessment-v4`, reconsideration `lessons-reconsider-v1`. The System page shows details, links,
unsettled premises, noted occasions and what prompted a revision.

Not built, by the design: no new store, no keyword equivalence, no automatic merging, no fixed budget, no training,
no skill system; learning still never acts (follow-ups stay working items for cognition).

## 3. Cutover

1. Stop workers. 2. From `backend`: `uv run alembic upgrade head` (applies step 3's `a3d7f1c9e2b6` and this
`b4e8d2a6f1c3`; nothing rewritten: existing lessons have no links and empty details). 3. Restart workers. The first
wake of each parked assessment reads once more (the reviewer's prompt version changed).

## 4. Scripted contract tests

`tests/test_learning_products.py` (14): derived possibilities and their basis, `tried` integrity, notes and carrying,
her own inferences never citable, reconsideration limits, **the paused-reassessment integrity test** (a fact premise
changes and commits with the worker held: Student 2's real rendering, the conversation mind, the planner, the hands
and a supply prepared before the change all name the changed premise and say unsettled; again after a restart;
released, one job, the LLM's narrowing applied with no evidence added; the independent lesson untouched), a
withdrawn premise down a chain and `keep`, a closed hypothesis and a deleted fact as premises, a critique revising on
the same evidence with repeats and unchanged wakes calling no model, cross-owner reconciliation through the real
reviewer/builder services, coverage notes, the hard-thought field, links made after a same-reading revision.

Results 2026-11-05 (disposable PostgreSQL): new tests 14 passed; full backend suite 1369 passed, 2 failed, 10
skipped. The 2 failures predate this work and are recorded in step 3's report
(`test_game_pack::test_she_knows_what_she_cant_do_yet_and_how_her_moves_behave`;
`test_student_two::test_its_work_is_carried_out_on_the_record_its_ref_names`, no local Student 2 model folder).
Migration upgrade -> downgrade -> upgrade on an empty disposable database. Frontend build passes; lint shows only the
3 earlier Fast Refresh warnings. Two step 3 tests were adjusted: a renamed reviewer header, and two pure decision
tests that hand Student 2 lessons no database holds now stub the currency check (delivery refuses unconfirmed
lessons).

## 5. Behavioral acceptance: protocol

Frozen before coding, in `backend/evals/post_audit_04/`: `RUBRIC.md` (requirements per family, critical failures,
scoring) and `reserved/fixtures.json` (8 families x 3 variants). Runner: `scripts/broader_learning_eval.py` (each run
in its own schema and process; reviewer, builder, reconsideration, Student 2 teacher, hard thought, planner and hands
are the configured models through the app's code; a judging model scores "judge" requirements, code the rest and the
critical checks). Source records are controlled and labelled. The runner's mechanics were checked on a separate
`development/` corpus; no reserved result was used to change prompts or code before the run below.

Known limits of the runner: the planner's and hands' situation text is built from the fixture (their learned section
is the app's own rendering); Student 2 probes run without the memory recall section; the judge is the same model
family as the learners.

## 6. Behavioral acceptance: results

Run 2026-11-06 (`backend/var/broader_learning_eval/20261106T020030Z-reserved.json`), 48 runs, every one completed,
none discarded. **Not accepted.**

| Family | Passes | Per variant | Main cause of failures |
| --- | --- | --- | --- |
| U understanding without advice | 0/6 | none | Products too narrow or missing (U.v2: the reviewer found the light/sapling relationship, no lesson came of it; U.v3: the Leo pattern wasn't kept, the control stretch made a product); U.v1/U.v2 probes non-discriminating. |
| C untried combination | 0/6 | none | The builder never proposed a combination; stretches became `episode_only` records or a plain strategy. |
| X cross-owner reconciliation | 0/6 | none | The reviewer never asked memory (one reading each); X.v3 left the stretch with nothing kept. No support was duplicated (X3 held in every run). |
| R same-evidence reconsideration | **6/6** | all | Passed. |
| P changed premise | **5/6** | all | Passed (paused visibility held in all 6 runs; P.v2 run 2's reassessment was judged unjustified). |
| E expectation checked | 4/6 | E.v3 0/2 | E.v3: a weekend of quick replies wasn't related to the expectation. |
| G capability gap | 0/6 | none | Products kept the gap (G1 passed 3/6), but every hands/Student 2 probe was non-discriminating: the baselines didn't claim the move either. |
| T later use and transfer | 0/6 | none | Probes non-discriminating (T.v1, T.v3); T.v2 kept no product. |

Critical integrity failures: none in any run. Consumer coverage: Student 2, mind, planner, hands probed; Student 1
uncovered in every run (blocking).

What it shows: the mechanics this package added (unsettled dependents, reassessment, reconsideration without support,
no-change wakes) behave as required with real models; the product-forming behavior (what the reviewer and builder
choose to keep, whether they ask memory, whether they combine) does not yet meet the rubric, and several reserved
probes can't discriminate because the baseline consumers already behave well.

Per the protocol, these 24 variants were read to diagnose failures and are now development material. Any change made
against them needs fresh reserved variants under the same rubric before an acceptance run counts. Nothing was
tuned before this run; no prompt or code was changed after it.

## 7. Prompt rounds after the first run (2026-11-06, on request)

The user asked for the prompts to be changed toward the plan's goal. Each round's reserved set was written before
that round's changes; each set, once read to diagnose, became development material (`evals/post_audit_04/
development/v1-diagnosed.json`, `v2-diagnosed.json`). Tuning used only development material.

**Round 1** (before set v2 ran): the reviewer learns from what the user tells her about their life, people and pets
and from her own limits, not only from feedback; states findings as reusable expectations with their conditions;
checks, as its own step, whether an existing lesson or memory plus this experience suggests an untried idea; the
most relevant memories (facts, her own items) and lessons are shown without asking; the user's name and the weekday
are shown. The builder keeps understanding (never as one-episode records), turns a derived possibility into its own
tentative lesson, and leaves routine exchanges alone. Fixes: game records citable by the builder; a hypothesis
without source messages no longer hidden.

**Round 2** (before set v3 ran): one subject, one lesson (new evidence goes into the lesson it bears on; a new
lesson only when it means something different, linked distinct; no "in this exchange..." statements); a
contradicting observation is stated as supported and related as challenges/narrows; a declared premise can't be
dismissed as "not a premise after all" without showing the lesson holds without it; code: a reconsideration can't
move an inference to episode scope (a withdrawal that doesn't say so), a lesson can't carry a hypothesis it
challenges, a finding can't narrow or revise a user's fact (kept as challenges: memory's to settle); the consumers'
guide (`lessons.GUIDE`, read by Student 2, her mind, planner and hands): a lesson about someone in the user's life
bears whenever the situation affects them, even unnamed. Runner: per-probe product snapshots for the judge, weekday
placement of stretches.

| Family | v1 | v2 | v3 |
| --- | --- | --- | --- |
| U | 0/6 | 2/6 | 4/6 |
| C | 0/6 | 4/6 | **5/6** |
| X | 0/6 | 2/6 | 1/6 |
| R | **6/6** | **6/6** | **6/6** |
| P | **5/6** | 4/6 | **5/6** |
| E | 4/6 | 3/6 | 4/6 |
| G | 0/6 | 3/6 | 0/6 |
| T | 0/6 | 3/6 | 2/6 |
| Runs | 11/48 | 27/48 | 33/48 |

Bold: family passes the rule. Critical integrity failures: none in any run of any set. Student 1 uncovered
throughout. **Not accepted.** Report: `var/broader_learning_eval/20261106T040201Z-reserved.json`.

Set v3 failures, read to diagnose (set v3 is now development material too):

- Probes that can't show learning (7 runs): the hands and planner already handle these situations from their state
  (G.v1, G.v2), the baseline asked a clarifying question (T.v3). Products were right. Fixture design.
- Consumers not using a correct product (5 runs): the planner ignored the flooding expectation (U.v2), the hands
  ignored the ice-road lesson or its revision (T.v2), Student 2 gave one gift title against a "three for gifts"
  lesson (T.v3). Consumer weighing.
- Learning (6 runs): E.v1, a supporting Monday observation recorded as counterevidence (likely pushed by round 2's
  contradiction instruction: a regression to fix); X.v1 memory not surfaced by relevance (X1); X.v2 a second lesson
  beside the old one; X2 misses where the judge wanted the fact's separateness stated explicitly; T.v2 run 2 no
  change from the snow-road stretch. In X.v1 and X.v3 the old lesson was now revised in place.

## 8. Measurement repairs (2026-11-06, after the readiness review)

Following [the readiness review](REVIEW-2026-11-05-behavioral-test-readiness.md), handoff steps 1-3, before any
further prompt or model work. Evaluator version `broader-learning-eval-v2`; earlier reports keep their scores and are
not relabelled.

- **Gate** (`backend/evals/post_audit_04/gate.py`, pure): `family_thresholds` and `package` are separate answers.
  Families validate identities against the corpus (exactly three variants, runs 1 and 2 each, no strays). The package
  also needs an eligible corpus (declared `"eligibility": "reserved"`, its variant hash not in `retired.json`, every
  run from that hash, no duplicate or unfinished runs), behavioral coverage of all five consumers (a passing
  requirement tied to that consumer's probe; a requirement says which probe it judges with `"probe"`, otherwise it
  claims no coverage), and a passing integrity-suite result for the same code hash. Every missing condition is a
  reason. `tests/test_broader_learning_gate.py` (10, model-free): one variant run six times, duplicates, a missing
  family, 4/6, a never-passing variant, development / retired / undeclared corpora, runs from another corpus,
  unfinished runs, an uncovered consumer (none, or probed and never passing), a missing / failed / mismatched
  integrity suite, a critical failure; and a complete valid set passes.
- **Corpus lineage**: sets v1, v2, v3 are in `development/` and their hashes in `retired.json`; `reserved/` is empty
  until a fresh set is frozen. Reports record the corpus hash, its eligibility, the code hash (commit plus every
  uncommitted change under backend), and hashes of the rubric, corpus, gate, registry and runner.
- **Integrity oracles** (`backend/evals/post_audit_04/oracles.py`, pure), each with status `checked` /
  `not_exercised` and its scope: C1 provenance (records of this timeline, never her own reasoning; not semantic
  support), C2 owners of products, links and resolved link targets, forgotten sources, facts unchanged, C3 independent
  support against fixture-defined events (not the app's counting) and reconsiderations adding no evidence, C4 every
  dispatch boundary intercepted while learning runs (messages, desktop decisions and hands messages, session start,
  autonomous speech, skills saved, plans applied or requested, trials pursued; names imported elsewhere are not
  intercepted, said in its scope), C5 supplied revisions against the revision held at that moment, unsettled and
  tentative products marked in the input. `tests/test_learning_integrity_oracles.py` (6): each oracle catches an
  injected violation; one breaks the app's independence counting on purpose and the oracle catches it.
- **Scorer**: C2 and C3 must hold for one product; T3 freshness can't pass with an empty supply; evidence cut to fit
  the judge makes the verdict ambiguous, never a pass; `compares` can be declared per requirement.
- **Probe paths**: every probe records what it exercises (`PROBE_PATHS`): all current probes are component
  evaluations, not deployed paths (Student 2 on the forced teacher provider, the hands' action not executed...).
  The hands' full prompt and schema are kept in the record.

What earlier results mean now: "no critical failures" in sets v1-v3 means none detected by the earlier, narrower
checks. A consumer "covered" there was covered as a component, not on its deployed path.

Not yet done (review steps 4-6, and still open): the B1-B12 / U-T coverage matrix, deployed-path probes, forked
matched arms, judge calibration, a separate-process restart test, a fresh reserved set, and revisiting the prompt rules
that may narrow understanding ("one subject, one lesson", no episode scope for understanding) with contrastive cases.
