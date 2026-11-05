# Columbina learning system: staged plans

Status: planning, 2026-10-31. Step 1 was built on request on 2026-11-01 ([implementation](01-implementation.md)); Step 2 design decisions were accepted on 2026-11-01 and it was built on request the same day ([implementation](02-implementation.md)); Step 3 likewise ([implementation](03-implementation.md)). Step 4's design decisions were accepted on 2026-11-03 and it was built on request the same day ([implementation](04-implementation.md)). No other step is authorized by this split.

Architecture: [original design](../COLUMBINA_LEARNING_SYSTEM_DESIGN.md). This index organizes that design into smaller plans. Step 1 records capture decisions accepted 2026-11-01 and supersedes earlier conflicting capture and retention proposals.

Audit 2026-11-04: [can she learn from experience? Gap map for steps 2–4](AUDIT-2026-11-04-learning-from-experience.md) (not authorized to build).

## Decisions already made

- [Step 4](04-predictions-and-trials.md) records prediction and game-trial decisions accepted 2026-11-03:
  autonomous proposals, cognition's final selection, persistent deferral, planner-owned preparation, linked
  execution and assessment, bounded climbing trials, and changed-condition evaluation. This remains design
  only. Conversational experiments require a separate discussion on the shared foundation.

- [Step 2](02-evidence-and-attribution.md) records accepted evidence and attribution decisions: learning-owned
  retrospective assessment, separate target and cause judgments, qualitative uncertainty, six assessment dimensions,
  automatic assessment without routine human review, evidence-based revision, and stopping on resolution or lack of
  a useful next step rather than a fixed investigation budget. Adaptation and training acceptance remain separate.

- Three loops: immediate adaptation, deliberate learning, and consolidation.
- Cognition handles the current request; learning decides what should persist.
- One shared Columbina model serves multiple users. Under the 2026-11-01 capture decision, personal captures and training examples remain private to their originating user and are not automatically available for other users’ training. This supersedes the earlier blanket permission for cross-user training. Step 5 must reconcile shared-model training with this boundary; UUID filtering alone does not enforce privacy inside model weights.
- Promotion is autonomous after behavioral evaluation, with rollback; routine human approval is not required.
- Core values remain fixed. Beliefs, strategies, and communication preferences can adapt.
- Experiments must follow core values. The user permits interpersonal experiments in principle; their V1 scope and operational limits remain unresolved.
- Generalizations are conditional and preserve exceptions. Experiments preserve competing explanations.
- Affinity and mood are excluded from V1.

Unanswered interview recommendations are not accepted decisions. In particular, exact lesson acceptance rules, data transformations, evaluation thresholds, and experiment boundaries remain open.

## Ordered plans

| Step | Plan | Depends on | Result |
| --- | --- | --- | --- |
| 1 | [Capture and ownership](01-capture-and-ownership.md) | None | Reconstruct what was known, chosen, and executed |
| 2 | [Evidence and attribution](02-evidence-and-attribution.md) | 1 | Attach feedback to the appropriate attempt without inventing certainty |
| 3 | [Immediate adaptation](03-immediate-adaptation.md) | 2 | A scoped correction improves a later decision without training |
| 4 | [Predictions and game trials](04-predictions-and-trials.md) | 2; reuse 3 for lessons | A bounded experiment checks expectations and revises a hypothesis |
| 5 | [Shared training data](05-shared-training-data.md) | 3; 4 for trial-derived examples | Auditable learner-specific datasets from eligible experiences |
| 6 | [Evaluation and autonomous promotion](06-evaluation-and-promotion.md) | 5 | A candidate improves behavior, passes gates, and can be rolled back |
| 7 | [V1 integration and operations](07-v1-integration.md) | 1–6 | Both loops work end to end and recover from failures |
| Later | [Broader learning](08-later-expansion.md) | Validated V1 | Reading, broader exploration, and richer prediction |

Each plan should be finalized and reviewed on its own before implementation. Work in sequence by default. Design the evaluation scenarios early, before generating training data; do not wait until step 6 to establish a baseline. Step 4 can proceed after step 2 if its memory interfaces are coordinated with step 3.

## Scope discipline

Steps 1–3 form the first useful conversational milestone. Step 4 completes the deliberate-learning milestone. Steps 5–7 add and validate autonomous consolidation. This decomposition proposes delivery order, not new authorization or settled implementation details.

Do not build a general world model, affinity system, or mood-learning system as a prerequisite. Keep broader interpersonal exploration outside the initial game-trial implementation until its separate policy is decided.
