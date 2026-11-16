# Post-audit 2: consolidate into one learning path

Status: implemented on request on 2026-11-05; see the [implementation record](POST-AUDIT-02-implementation.md). Scope and behavioral decisions were agreed through the user discussion on 2026-11-05. This is the second work package in the new post-audit order, not a restart of historical steps 1–4.

## Purpose and foundation

Retain what is useful from the old game-learning system, move those capabilities into the new learning system, and delete the superseded runtime path in this same package. Completion requires the replacement to work and its consumers to use it; connecting two independently operating learning systems is insufficient.

Build on completed [post-audit step 1](POST-AUDIT-01-fill-missing-experience-capture.md) and its [implementation record](POST-AUDIT-01-implementation.md), on branch `feature/experience-capture`. Use the [audit](AUDIT-2026-11-04-learning-from-experience.md) as the gap map, not as an instruction to restart its historical steps. Its assignment of consolidation and game-consumer delivery to historical step 3 is superseded by this package.

The general learning design reviewed in this discussion is in the sibling checkout at `../app-cognitive/docs/learning-system/GENERAL_EXPERIENCE_LEARNING_DESIGN.md`, relative to the repository root. Its accepted one-learning-path requirement applies here. Broader general-learning ambitions do not expand this package's scope.

## Accepted decisions

- This package only retains useful old capabilities through the new system. It does not introduce the full general observational-learning system or new categories merely because the general design describes them. The old ability to interpret successful and ordinary stretches of play must survive.
- Where observation capabilities overlap, keep the new implementation and discard the old implementation. Reuse completed step 1 capture; do not rebuild it or create duplicate observation producers.
- Deliver retained learning to the actual decision makers, including hands, planner, and mind. Preserve existing conversational adaptation.
- Preserve LLM skill-code creation and improvement, including useful practice and measurement capabilities. Trace their actual dependencies before removing old services.
- Old reflection is not valuable merely because it exists. Retain useful capabilities, such as interpreting a meaningful stretch of experience or remembering a useful episode, within the shared lifecycle. Discard redundant reflection machinery and outputs that add no value.
- A single costly experience may inform cautious action immediately as a tentative, scoped lesson. Unknown causes remain questions for her reasoning; uncertainty does not require her to ignore the experience.
- Do not encode situation-specific behavioral restrictions or hardcoded causal conclusions. Preserve observations, attributed claims, and uncertainty so the model can reason about them. Cognition retains action authority.
- Current stored data is test data, and the user intends to reset memories between test attempts. An explicit reset is an acceptable reconciliation of existing test memories; a migration solely to preserve those memories is unnecessary. This document does not execute a reset.
- The replacement and removal of the old path belong to the same package. Temporary coexistence is allowed only during the transition, without duplicate learning writes.

## Capability inventory and disposition

Before changing code, trace the old producers, jobs, stores, consumers, trial callbacks, and skill-development dependencies against the current branch. Record where each retained capability will live and which old entry points will be removed. File names alone do not establish ownership: a service may contain both obsolete orchestration and reusable domain functions.

| Existing capability | Required disposition |
| --- | --- |
| Game observations and detailed execution evidence | Prefer the new observation/capture implementation wherever it overlaps. Keep a domain adapter only for a useful capability the new path still needs. |
| Reflection over a meaningful stretch of play | Preserve useful interpretation through shared assessment, using existing effort and capture evidence where appropriate. Remove the independent old reflection lifecycle. |
| Skill and plan lessons | Represent useful learning through shared memory ownership and evidence rules, with scope, uncertainty, and source links. Retire the independent game-lesson lifecycle. |
| Delivery to hands, planner, and mind | Replace old delivery calls with shared retrieval and record the product revisions actually supplied. |
| Episodic recollection | Preserve useful episodes through existing memory ownership; do not retain an old recap mechanism solely for compatibility. |
| Skill generation, improvement, practice, and measurement | Preserve useful working capabilities and reconnect dependencies to the shared learning path. |
| Trial results feeding old game lessons | Route retained trial results into the shared lifecycle; remove writes to the superseded lesson path. |
| Old workers, schedules, prompts, settings, and compatibility routes | Remove those made obsolete after replacement verification in this package. |

Code inspection distinguishes old `GameProposal` records from executable skill writing: `services/game_learning.py` describes proposals as suggestions for game-code changes that the application does not itself apply. Preserving LLM skill writing does not imply that this proposal store must survive unchanged or that arbitrary application-code changes become automatic. Trace the skill-development path separately and retain useful proposal information only where it serves the replacement.

## Evidence, uncertainty, and later use

Use the richer step 1 record without treating every tick as a memory or model call. A retained assessment must be able to inspect the relevant experience, including available actions, outcomes, notices, conclusions, and measured facts. Missing or expired evidence remains explicit.

For example, death while descending supports reconsidering the descent. Without evidence of the damage cause, it does not establish that lava killed her. She may ask herself what happened, consider explanations, seek information, and choose a cautious alternative. This example illustrates reasoning expectations, not a new hardcoded death handler or strategy.

Use one shared evidence and revision lifecycle. Migration or repeated reflection on the same event must not manufacture independent support. Do not carry forward the old different-session trust rule as a second policy. Exact storage fields and integration changes should follow the existing shared implementation; this discussion did not select numerical confidence thresholds or a new universal promotion formula.

Supply relevant retained products to each consumer with applicability and uncertainty intact. If shared retrieval currently supplies only accepted products, extend it explicitly so supported, scoped tentative lessons actually reach hands, planner and mind. Record what was supplied separately from evidence that it influenced a decision. Connect later outcomes to support, contradiction, narrowing, or withdrawal where the evidence supports that connection; delivery alone does not demonstrate successful learning.

## Test data and reset boundary

Document the chosen reconciliation before cutover. For this test deployment, reset may replace migration of old learned products. Inventory the affected shared and game-specific memories, lesson records, derived caches, and pending jobs so an old job cannot repopulate cleared learning after reset. Verify that the next test does not retrieve learning from the preceding attempt.

The user's permission to discard test memories is not a requirement to erase all raw capture, source evidence, generated skill code, or unrelated application state. Specify the reset boundary explicitly. Preserve existing ownership, retention, and deletion rules for evidence outside that boundary. If any records are carried forward, retain provenance and uncertainty; do not promote guesses merely by moving them.

## Implementation sequence

1. Inventory old capabilities and dependencies against completed step 1 and the shared learning system; record retain, replace, or remove decisions.
2. Implement missing shared-path support required to retain those capabilities, including game experience assessment and consumer delivery. Reuse useful domain adapters and skill-development functions.
3. Route producers, jobs, trial results, and consumers to the replacement with retry-safe identities and no duplicate learning writes. Apply the documented test-data reconciliation at cutover.
4. Verify learning, later use, revision, conversational continuity, and preserved skill-development behavior through the replacement.
5. Remove superseded runtime code, schedules, settings, and compatibility paths in this same package. Update operational documentation and tests to describe the single remaining path.

## Completion evidence

- A capability inventory accounts for old producers, workers, stores, consumers, and skill-development dependencies, with no useful capability lost accidentally.
- A meaningful game experience produces evidence-linked, scoped learning through the shared path. Unknown causes remain unknown, while a tentative lesson is actually retrieved and supplied to inform cautious judgment. Successful and ordinary stretches remain assessable through the replacement.
- Hands, planner, and mind receive relevant products from that path. At least one later decision after the original context is gone demonstrates use; retrieval logs alone are insufficient.
- Later supporting or conflicting evidence can revise a retained product without counting the same observation twice.
- Existing conversational learning still works, and useful LLM skill creation/improvement and practice functions remain operational.
- Overlapping observations are emitted by the new implementation only. Retries and temporary transition do not create duplicate learning writes.
- A test-memory reset prevents prior learned products or pending old jobs from contaminating the next attempt, while respecting its documented deletion boundary.
- The superseded runtime path is removed: no active old reflection worker, schedule, lesson writer, consumer route, or compatibility fallback remains. Necessary domain adapters serve the shared lifecycle.

## Scope boundary

Do not restart completed capture work, add broader ordinary-observation learning, introduce new experiments, train models, or fix unrelated planner behavior as part of this package. Make only the shared-path changes needed to preserve useful old capabilities and complete their replacement. Do not defer deletion of the superseded runtime path to another milestone.
