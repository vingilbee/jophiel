# Evaluation and autonomous promotion

Status: proposed plan, not implementation authorization. [Plan index](README.md).

## Outcome

Autonomous promotion based on measured behavior, with rollback.

Dependencies: Step 5; establish baseline scenarios during steps 1–3.

## Work

- Reuse compatible training/export/evaluation pipelines.
- Evaluate unseen target situations, exceptions, prior abilities, character behavior, calibration where relevant, and cross-user disclosure.
- Keep held-out evaluations protected from training and repeated candidate tuning.
- Version datasets, evaluators, candidates, promotion decisions, and rollback targets.
- Implement controlled activation and regression monitoring.

## Decisions to settle within this plan

Numeric pass gates; training cadence and compute budget; supported model deployment mechanism; rollback triggers; evaluation contamination controls.

## Completion evidence

Demonstrate a candidate accepted under agreed gates, a failing candidate rejected, and successful rollback. Averages cannot silently replace separately agreed critical checks.

## Boundary

Passing disclosure tests supplies evidence, not a guarantee that shared weights cannot reproduce private data.

