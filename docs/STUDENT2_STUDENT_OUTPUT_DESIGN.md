# Student 2 — how the student produces a complete decision (design review, 2026-10-24)

Status: **approved with four corrections (2026-10-24), built and run once.** Results: "Outcome of the first build"
at the end, and `docs/qa/student2-event-pilot-2026-10-23/RESULTS_PILOT8_STUDENT.md`. It answers review items 1 and 2:
- every field of the agreed decision needs a supported way to be produced;
- the outputs must depend on each other;
- training must keep complete teacher decisions.

Evidence for why this is needed is in `docs/qa/student2-event-pilot-2026-10-23/RESULTS_PILOT8_STUDENT.md`, sections
"Three implementation checks" and "The no-change collapse".

## Principle

The decision contract (`events.check`) says which combinations mean something. The student may only choose among
those. Within them, every choice is hers, learned from the teacher. The masks below come from `check()` and the
snapshot's state, never from what she should prefer.

## Every field, and how it gets produced

Counts are over the 4,561 validator-passed teacher decisions in pilots 8 + 8b.

| Field | Needed in | How the student produces it |
|---|---|---|
| work operation + target | all | One choice over valid (operation, target) pairs; `no_change` is the single pair (no_change, none). |
| resolution | 158 (3%) | A choice between `achieved` and `given_up`, only after `resolve_goal`. |
| basis | 544 (12%) | A pointer to a reference, restricted to the kinds the operation allows (below). |
| other goal + handling | 321 (7%) | A pointer over the other open goals (or none), then a handling choice; only after `pursue` or `resume`. |
| communication | all | A choice among six, conditioned on the work choice. |
| move, second move, dials | speaking | Move and second move from the move groups. Dials are 4 small choices (length, warmth, playfulness, directness). |
| what to communicate | speaking | **Not written by the student.** See "Text fields". |
| defer item, condition, expiry | 11 (0.2%) | Item: a pointer. Expiry: a choice. Condition: see "Text fields". |
| think about | 63 (1.4%) | See "Text fields". |
| intention, explanation, proposed new goal, does not fit | — | Teacher diagnostics for people. Nothing at runtime reads them, so the student doesn't produce them. |

**Pointers instead of a fixed list.** The student will read references from the text. Every reference in the
rendered snapshot has a marker (`[G1]`, `[W2]`, `[E1]`…), and the student scores a reference from the encoder's state
at its marker. Today's student instead picks from a fixed vocabulary of 70 labels, where "G1" means nothing about the
goal it names. Reading the goal's own words should help with unseen situations, though that is unproven (see the
collapse section).

## Order and masks

The student decodes one complete decision in this order. Each step sees the earlier choices, and invalid options
are masked out.

1. **Operation and target, as one choice.** Each pair is scored from the operation and the target's marker state. A
   pair is offered only if `check()` would accept that operation on that target:
   - the target kind is allowed (`ALLOWED`);
   - its controls support it;
   - pause only applies to something running, resume only to something paused;
   - goals must be open;
   - restrictions are respected, unless a lifting basis is possible (step 2).

   So invalid pairs like `no_change` with a target, or `pursue` on a goal, can't be produced.
2. **Fields that operation requires:**

   | Operation | Then chooses |
   |---|---|
   | `resolve_goal` | resolution; basis = an assessment `[E*]` **of that goal** (required for achieved; optional, may be none, for given up) |
   | `revise_goal` | basis from `[M]`, `[U*]`, `[R*]` (required) |
   | `request_replanning` | basis from `[W*]`, `[M]`, `[U*]`, `[T*]`, `[E*]` (required) |
   | `pursue`, `resume` | basis: none, or a user message after a restriction in scope (required to start inside one); other goal from the other open goals, or none; handling, with `close_achieved` only if an assessment of that goal exists; pursuing a proposal that conflicts with a goal must name that goal |
   | `request_thinking`, `recall_then_thinking` | the thinking subject (see "Text fields") |
   | everything else | nothing more |
3. **Communication**, conditioned on the operation and target. Speaking options are offered only when a move exists.
4. **Speech fields**, only when speaking: move (never none), second move, dials. Defer fields only for defer.
   Silent has none.

The result always passes `check()`, by construction. A test will assert that on every held-out decision.

## Text fields

ModernBERT selects; it doesn't write. For each text field the choice is between selecting something that already
exists and handing it to a component that writes.

| Field | Proposal | What it costs |
|---|---|---|
| **think about** | The student selects the **subject** (the operation's target: `[T1]`, `[M]`, `[U*]` or `[W*]`). It also selects up to two **references to weigh against it** (a multi-pointer: `[U1]`, `[W1]`, `[G1]`…) and a **question form**: whether / what / compare / recall what was said. The thinking component (`app/ai/mind/hard.py`, which already reads the situation) turns that into its request. | The teacher's wording sometimes adds a caution ("don't assume it was residency dates"). That would have to come from the thinker reading the situation. Before adopting this, I'd regenerate the 63 teacher requests from the selections alone and have a grader compare them with the originals. |
| **reconsider when** (defer) | A closed set: `user_returns`, `user_raises_it`, `work_finishes` (the deferred `[W*]`), `after_recall`. Each of the 11 teacher conditions fits one of these. Four of them also name a time ("after 8:30", "tomorrow", "after the weekend"), and `defer_expiry` only says when to stop keeping the item. So the set also needs a `not_before` time (none / 2 hours / tomorrow / next week). | **This changes the contract for the teacher too.** But a wake-up condition that code has to evaluate should be a closed set anyway: free text can't be checked at wake time. |
| **what to communicate** | The mouth (`event_speak`, later the live mouth) writes the words. It gets the student's decision: move, dials, operation and target, plus a new **about** pointer (up to two references the speech is about, e.g. `[T1]`, `[E1]`, `[M]`). | The teacher contract gains an `about` field. Older pilots need it labelled after the fact from `what_to_communicate`: one cheap model pass, checked by code against the listed references. |

Nothing is removed. Each ability either stays a student choice or is handed to the component that formulates it,
with the student's selection as its input.

## Training on complete teacher decisions

- **The target is the teacher's samples as whole decisions.** For each sample, the loss is the log-probability of
  its complete path through the steps above, given its own earlier choices (teacher forcing). It's averaged over
  that snapshot's samples. Disagreement between samples is kept, and no field is voted separately. Per-field voting
  is how today's "teacher, voted per head" produced combinations the teacher never chose.
- **Decoding:** a beam over valid paths. The prediction is the most likely complete decision.
- **Metrics:**
  - complete-decision match with the teacher's majority decision, and with any of its samples;
  - per-step accuracy given earlier steps right, so errors can be located;
  - per-operation recall;
  - the same baselines as the collapse check: always the most common decision, and the family majority.

## What I'd build, in order (needs your ok)

1. **Data:** the `about` field and the closed defer conditions in the teacher contract. Label existing pilots after
   the fact (about, think-about selections). Do the regeneration check on the 63 think-about cases.
2. **Student:**
   - pointer targets over marker states;
   - the masked decoding above, with complete-decision training and a "decisions always pass `check()`" test;
   - beam decoding.
3. **Rerun on the same data and split.** Use the human verdict file. Which of the model reviewer's other exclusions
   stay is your call; see the results doc. Compare against today's student and both baselines, before generating more data. Pass mark to agree:
   held-out acting decisions at or above the family-majority baseline (49 of 129), and zero invalid decisions.

Not decided here: whether `intention` should become a student output for her mind's use. Today nothing reads it.

## Corrections from the review, as built

1. **A deferral keeps its actual time.** Snapshots now carry the user's local clock (`clock`, from the plan's seed,
   given to the situation writer too), and a deferral is structured: `reconsider_on` (user_returns,
   user_raises_it, work_finished, recalled, time_reached), `reconsider_ref` (e.g. the `[W1]` it waits for), `not_before`
   (an exact "YYYY-MM-DD HH:MM" after the clock) and `not_before_source` (the message the time comes from). A wake-up
   means reconsideration, never speech. Older runs have no clock, so their 5 named times ("after 8:30") are kept as
   words and can't become `not_before`; the student has no training data for it yet and leaves it empty.
2. **`about` names a topic, not her message.** The reconstruction checks below test whether the mouth keeps her
   message; they did not pass, so "nothing is removed" is **not** established.
3. **Passing the validator now means the permission is real.** Whether a user message lifted a restriction is part
   of the situation (`restrictions[].lifted_by`, shown as "lifted by [M]" or "in force"), written by the situation maker
   and, for older runs, labelled after the fact. A start inside a restriction is allowed only when it is lifted, and
   the only basis it may cite is that message; a later message that doesn't lift it lifts nothing. `achieved` and
   `close_achieved` need an assessment that reads the goal as fulfilled, not just one that exists.
4. **Provenance.** Overrides live in `review_overrides.json` with `by` (`human` only for a person's own verdict, else
   `ai`) and `source`. The disputed explicit_dismissal decision is recorded as the user's reviewing AI's verdict,
   with this agent's concurrence, `user_endorsed: false`.

The gates (zero invalid decisions; acting decisions at or above the family baseline) are experiment gates: they show
whether this implementation works and merits more training, not readiness for use.

## Outcome of the first build

- **Validity:** 0 invalid decisions in 1,534 predictions (train and held out). The structure works.
- **Generalisation:** held-out acting decisions 24 of 124, against 50 for the family baseline. **Gate not met.**
  The no-change collapse is unchanged by the output structure (details in the results doc).
- **Text handoffs:** speech rebuilt from the selection kept her stance 146/160 but her message only 38/160 (control
  with the full decision: 153/160). Thinking requests: 24/63 the same question (paraphrase control: 62/63). Adding
  up to three exact phrases of the situation as a further selection: 55/160 and 31/63. The selections the design
  proposed do not carry what she means; see the results doc for the options.
