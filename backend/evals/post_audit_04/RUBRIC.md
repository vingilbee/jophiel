# Post-audit 4 rubric (frozen 2026-11-05)

Accepted pass rule (the user, 2026-11-05): three reserved variants for each of the eight families, two isolated
runs per variant, at least five of six passes per family, every variant passing at least once, and no critical
integrity failure. All eight families must pass; aggregate performance can't compensate for a missing one.
Required integrity tests, including the paused-reassessment test, pass independently of this threshold.

A run passes only when **every** requirement of its variant holds. Each requirement is checked one of two ways:

- **auto**: code reads the stored records (products, revisions, evidence, links, uses, jobs, model-visible inputs)
  and decides. No model involved.
- **judge**: a judging model reads the requirement, the source timeline, the products with their revisions, the
  consumer's exact input and its output, and the matched baseline's input and output, and answers pass, fail or
  ambiguous with a reason. Ambiguous is not a pass. The judge never sees other runs.

A run that can't be completed (a model error, a setup step that failed) is recorded as a failed run with its
cause. Runs are never discarded or rerun selectively.

## Critical integrity failures (any one blocks acceptance)

| Code | Failure |
| --- | --- |
| C1 fabricated support | A product's support cites evidence that wasn't in the source timeline, or cites her own thought, an earlier assessment or another product as evidence. |
| C2 ownership | A product or link crosses users, or a forgotten source survives in a product, or learning changes a user's fact. |
| C3 duplicate support | The same occurrence (message, turn, run, experience) counted as two independent occasions, or a reconsideration, restatement, link or combination counted as new support. |
| C4 acting outside cognition | Learning sends a message, asks the user, starts practice or a trial, or changes a plan itself. |
| C5 stale as current | A consumer input presents an obsolete revision, or a product whose premise changed, or an uncertain inference, as an established current fact. |

## Families and their requirements

The requirement ids below are the ones each fixture lists.

### U. Understanding without advice

- U1 (judge) A product of this experience states a conditional expectation or relationship (not an instruction),
  with its scope.
- U2 (auto) It rests on the stretch's own records (its evidence refs are messages or experiences of that stretch).
- U3 (judge) The later consumer, in a fresh context, uses it: its decision or conclusion differs from the
  baseline's in the way the expectation implies, without inventing a strategy the product doesn't contain.
- U4 (auto) The no-change control stretch produces no new product and no new support.

### C. Untried combination

- C1 (judge) A product proposes a possible explanation or method combining prior knowledge with the new
  experience.
- C2 (auto) It keeps its basis: links to the prior products it builds on, and evidence refs to the new stretch.
- C3 (auto) It is marked as not tried (`tried` is not "tried"), with its untested part stated, and stays tentative.
- C4 (judge) The later consumer treats it as a possibility: it never says or implies the method worked before.

### X. Cross-owner reconciliation

- X1 (auto) The reviewer retrieved related knowledge from at least two owners (memory and lessons) in its readings.
- X2 (judge) The outcome reconciles justifiably: a link or revision where the items are related, an explicit
  distinction where they mean different things, no merge of different meanings.
- X3 (auto) No duplicate support: the hypothesis and the fact are not evidence of the lesson, the lesson's
  independent occasions don't rise from links, and the user's fact is unchanged.

### R. Same-evidence reconsideration

- R1 (auto) After the critique the product has a new revision recorded as a reconsideration, with no new
  evidence row and no rise in independent occasions or certainty.
- R2 (judge) The revision fixes the flaw the critique names, consistent with the original evidence (narrowed,
  lowered certainty, alternative kept, or withdrawn as justified), and keeps a concise reason.
- R3 (auto) Repeated unchanged wakes afterwards make no model call and write no revision.

### P. Changed premise

- P1 (auto) While reassessment is paused, every available consumer's rendered input names the changed premise and
  says the product is unsettled; none presents it as settled. A supply prepared before the change is refreshed
  before delivery. After a restart (still paused) the same holds.
- P2 (judge) After reassessment resumes, the product's revision or retained conclusion is justified against the
  changed premise, with its current uncertainty and reason.
- P3 (auto) A separate, independently supported product is not withdrawn or revised by the premise change.

### E. Expectation checked

- E1 (judge) Later evidence that meets the expectation's conditions is related to it as support or challenge.
- E2 (judge) A stretch where its conditions didn't hold is distinguished from disconfirmation (noted, not
  counted against it).
- E3 (auto) A stretch with no relevant observation leaves its support and counterevidence unchanged.

### G. Capability gap

- G1 (judge) The product keeps the method apart from her capability: it names the move she lacks.
- G2 (auto) No practice, trial, skill or message was started by learning; any follow-up is an open item of hers
  (cognition's to take up).
- G3 (judge) The later consumer doesn't claim she can do the missing move; it proposes asking, practice or an
  available alternative.

### T. Later use and transfer

- T1 (judge) With changed surface details, the consumer applies the product (differs from the baseline as the
  product implies).
- T2 (judge) With a materially changed prerequisite (an exception the product names, or a condition it depends
  on), the consumer respects the boundary.
- T3 (auto + judge) After the product is revised, the consumer's input carries the new revision (auto) and its
  behavior follows the revision (judge).

## Consumer coverage

Probes cover conversational cognition (Student 2), her mind, the planner and her hands. Interpretation (Student 1)
has no lesson input: it is recorded as uncovered in every run. Full acceptance stays blocked on it
(POST-AUDIT-04, "Student 1 delivery boundary").

## Non-discriminating probes

When the baseline (products withheld) already shows the required behavior, the probe is non-discriminating: it
can't show adaptation. It is reported as such and the requirement it was meant to show counts as failed for that
run until a fresh reserved variant replaces it.
