# Post-audit 4 behavioral acceptance: reserved corpus

Frozen 2026-11-05, before any step 4 code was written
(docs/learning-system/POST-AUDIT-04-broader-learning-products.md, "Product-focused acceptance").

- `RUBRIC.md`: the frozen rubric. Each family's observable requirements, the critical integrity failures, how a run
  is scored, and the accepted pass rule (three reserved variants per family, two isolated runs per variant, at
  least five of six passes per family, every variant passing at least once, no critical integrity failure, all
  eight families).
- `reserved/fixtures.json`: the 24 reserved variants (8 families x 3). **Evaluation data only.** Do not read them
  while writing or tuning prompts, checks or implementation branches, and never train on them. A variant used to
  diagnose a failure becomes development material; replace it with a fresh reserved variant under the same
  rubric before it counts again.

The public examples in the design documents are specifications, not reserved data. The runner is
`scripts/broader_learning_eval.py`; it reads this corpus and nothing else from here.

## Readiness review and corpus eligibility

See [the current test-readiness handoff](../../../docs/learning-system/REVIEW-2026-11-05-behavioral-test-readiness.md) before the next acceptance run. The implementation report records the latest reserved set as diagnosed development material; the directory name alone does not establish eligibility. Preserve historical results and the frozen rubric. Repair and version the scorer, establish fresh reserved-fixture lineage, and close the documented gate/coverage defects before claiming acceptance. This note does not relabel old runs or change their scores.

## Gate, oracles, lineage (evaluator v2, 2026-11-06)

- `gate.py`: what a set of runs may claim (family thresholds and package acceptance, separately), failing closed.
- `oracles.py`: the critical checks, independent of the code under test, each saying whether it was exercised.
- `retired.json`: corpus hashes that can never support acceptance again (diagnosed or development sets).
- A requirement names the probe it judges with `"probe": "<label>"` (that is what consumer coverage counts) and may
  declare `"compares": true|false` (whether a non-discriminating baseline fails it).
- A reserved set declares `"eligibility": "reserved"`.

## Fixture format

Each variant has a timeline (`setup`), the later probes (`probes`), and its requirements (`requirements`).
Times are relative to the run's start.

Setup steps:

| Step | What the runner does |
| --- | --- |
| `conversation` | Stores a conversation (`messages`: `[speaker, words]`, speaker `user` or `her`), `days_ago` old. With `learn: true` the real reviewer and lesson builder read it, as every completed exchange is read live. |
| `game` | Stores a Minecraft session and its recorded experience (`events`: skill outcomes and notices, the shapes the pack records). With `learn: true` the real reviewer and builder read the stretch. |
| `fact` | Memory's record of something the user said (`content`), sourced to a message of the named conversation. A controlled record, labelled as such. |
| `hypothesis` | One of her own open hypotheses as thought memory keeps it (`text`, `reason`). Controlled. |
| `seed_lesson` | An earlier product as memory holds it (kind, statement, applies, certainty...), resting on the named stretch's messages or experience. Controlled: used where the case is about what happens to an existing product. `depends_on` names premises (a fact, a hypothesis or another seeded lesson). |
| `critique` | Her mind's critique of a product she was shown (`of`: words identifying it, `text`). Controlled: the input of a reconsideration. |
| `change_fact` / `withdraw_fact` / `close_hypothesis` / `withdraw_lesson` | A premise changes through its owner's own path. |
| `pause_reassessment` / `resume_reassessment` | Holds or releases the lessons worker's reconsideration jobs. |
| `restart` | Drops every in-process cache and opens new sessions, as a worker restart would. |
| `wake_unchanged` | Wakes the named stretch's assessment again with nothing new (`times`). |

Probes run each consumer twice in a fresh context (the original conversation is not in its input): with the
learned products available and with them withheld (`lessons_supply_enabled` off), everything else equal. A
consumer with no lesson path (Student 1) is recorded as uncovered, never as a pass.
