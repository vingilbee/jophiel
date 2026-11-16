# Post-audit 2 implementation

Branch: `codex/consolidate-learning-path`. Implemented and verified on 2026-11-05.

## Capability inventory before cutover

| Previous producer, dependency or consumer | Replacement / disposition |
| --- | --- |
| Hands outcomes and pack notices, `game_learning.record_experience` | Retain factual `GameExperience` domain records and existing observation adapter in `game_experience`; no second observation producer. |
| Session transitions → reflection jobs → old reflector | Transitions open bounded shared assessments over the unassessed stretch, including successful and ordinary outcomes, notices, goals and user messages. Failure intake remains available immediately. No general per-tick learning is added. |
| Detailed evidence | Existing attempts, executions, trajectory reader and gap reporting feed shared assessment; claims, execution reports and observed state stay separate. |
| `GameLesson`, separate trust counts and trial lesson callback | Shared assessment → lesson builder → `MemoryLesson`, revisions and evidence. Repeated/overlapping source events must not count as independent support. |
| Hands, planner, Minecraft mind | Shared retrieval with scoped hypotheses explicitly labelled tentative; capture the exact revisions supplied. Retrieval is separate from reported influence and actual resulting decisions. |
| Reflection recap → `EpisodicMemory` | Optional evidence-linked episode from shared assessment, written under memory ownership and revised/withdrawn with its assessment. |
| `GameProposal` code suggestions and known gaps | Retire separate proposal store/UI; useful limitations can be scoped shared lessons/questions. These proposals never wrote executable code. |
| `game_practice`, `game_skills`, `game_challenges`, trial execution/measurement | Preserve writer, critic, improvement, practice, saved programs, measurements and domain profile lookup. Reconnect trial evidence to shared assessment. |
| Old worker, config, reflection schema, API and UI | Remove after replacement tests. Historical migrations and inert table models remain for reset of test data, not compatibility delivery. |
| Play measurement report | Retain factual outcome metrics through the domain adapter; use shared lesson-use records. |

## Reconciliation and operational boundary

Use an explicit test-learning reset rather than promote old test products. The dedicated consolidation reset clears shared assessments/jobs/lessons/uses, old game lessons/proposals/jobs, game-derived episodes and pending learning receipts, and establishes a capture cutoff so pre-reset experiences cannot be reassessed. It preserves raw captures, experience evidence, generated skill code, practice history, accounts, conversations and unrelated memories. Stop workers during reset and restart them afterwards so in-flight prompts and hands caches cannot cross attempts. No development memory reset is executed by implementation tests.

## Required implementation checks

- A single supported cost or progress observation may produce a scoped hypothesis without establishing an unknown cause. Shared retrieval must actually supply it, labelled with uncertainty and applicability.
- A meaningful successful or ordinary stretch must still be assessable. “No broader observational learning” prevents a new general observation learner; it does not remove the previous stretch interpretation capability.
- Verify replacement learning, later decisions, revision, retry identity, conversation continuity and skill development before deleting superseded runtime machinery.

## Remaining runtime path

`game_experience` is a domain evidence adapter, not a second learner. It preserves the existing observation producer and play metrics. Stable attempt, pack-run, notice and trial-revision identities make callback retries one event and one observation. The shared assessment and lesson workers retain their existing leases, stale-reading fences and revision rules.

`game_assessment` opens shared assessments for bounded completed stretches. It retains the previous minimum of five outcomes at end/offline and fifteen at a pause, now configured by `LEARNING_GAME_MIN_OUTCOMES` and `LEARNING_GAME_PAUSE_OUTCOMES`. Ordinary and successful outcomes count. Large stretches are partitioned without discarding their middle. Existing critical-notice salience also starts assessment without waiting for a failed skill or a completed stretch. Intake records a reason to assess; it assigns no cause or action.

Shared assessment can read notices, outcome reports, available skill names, captured inputs and schemas, plans and applied rules, thoughts, execution reports, observed state, session events and the existing trajectory reader. Bounded excerpts say when they are truncated. Missing or expired decision/execution captures and trajectory holes remain explicit. Pack and backend clocks stay separate. Routine trajectory ticks still create no observation or model call.

Supported game cost/progress/knowledge findings can reach the shared lesson builder without a supported causal attribution. Memory owns the resulting lesson and its evidence/revisions. `memory_lessons.for_decision(..., include_tentative=True)` is the explicit retrieval extension; `for_game` requests it. Only situation hypotheses with live supporting evidence qualify, with status, reason, applicability, exceptions and source refs intact. Conversational retrieval retains its existing accepted-only policy.

Hands refresh shared retrieval between decisions, render uncertainty, and can report `lessons_weighed` through their strict schema. Planner and Minecraft mind use the same retrieval and retain the exact supply beside the rendered input, rather than independently re-querying after the prompt was assembled. Each records supplied revisions separately from any consumer account. A use without an account remains unaccounted for. Optional evidence-linked episodes go to the existing episodic memory store; a newer shared assessment revises or supersedes them. Overlapping source roots, including a trial read again or an execution and its own attempt, cannot manufacture independent support.

Trials retain investigation, cognition's selection, execution bounds, measurement, memory hypothesis resolution and successful-program saving. Their checked measurements and attributed interpretations now enter a stable shared assessment. Later unresolved readings also revisit previous learning. There is no direct trial-to-game-lesson writer.

Deleted: the old game reflection service and worker, independent lesson/proposal writers and retrieval, old worker health entry, `GAME_LEARNING_*` settings, reflection output schema, legacy lesson/proposal/reflection API routes and their frontend controls. Skill/practice/challenge schemas and routes remain. Historical migrations and legacy table models remain solely so test data can be explicitly cleared; they have no active learning writer, scheduler or delivery fallback.

## Cutover

The implementation does not reset the development database. Before starting the replacement against an existing test deployment:

1. Stop backend workers and any playing session so in-flight decisions and cached prompts cannot cross test attempts.
2. From `backend`, run `uv run alembic upgrade head` (new revision `f2a6c8e1b4d9`). It adds the per-user learning cutoff and the unique factual event identity; it does not erase source evidence.
3. Preview `uv run python scripts/reset_memory.py --user YOUR_EMAIL --scope consolidation --dry-run`, then run the same command without `--dry-run` for the chosen test user. This is the agreed reset instead of a legacy-product migration.
4. Remove obsolete `GAME_LEARNING_*` environment overrides, configure shared learning as usual, and restart workers and play.

The `consolidation` reset removes shared assessments, lesson jobs/products/revisions/uses, old game lessons/proposals/reflection jobs, derived trial records and pending investigation triggers, and game-derived episodes. Pending learning receipts are settled and a durable per-user cutoff prevents old observation recovery or game evidence from recreating prior learning. Raw decision captures, trajectory, factual game experiences, saved skill code, practice history, conversations and unrelated state remain. Historical log files are diagnostic records, not retrieval stores. Other reset scopes retain their documented behavior; `game` is broader and also deletes generated skills, so it is not the consolidation reset.

## Verification

Replacement behavior was tested before removal of the old runtime. Tests use disposable PostgreSQL schemas and scripted model/pack responses, with development database access disabled. They establish lifecycle, prompt delivery and resulting actions; they do not claim a live model evaluation or a real-server play trial.

| Contract | Evidence |
| --- | --- |
| Ordinary/successful stretches survive, without old jobs or products | `test_successful_and_ordinary_stretches_reach_shared_assessment_with_retry_safe_boundaries` |
| A costly event with unknown cause becomes a retrievable scoped hypothesis; later conflict invalidates it and supersedes the episode | `test_one_cost_with_unknown_cause_becomes_a_scoped_hypothesis_and_changes_later_hands_action` |
| Later action after original context is absent | The preceding test runs a fresh driver with and without the stored lesson: it mines without it and waits to inspect with it. `test_shared_tentative_lesson_changes_a_later_plan_and_records_the_exact_revision` applies a changed plan. |
| Mind delivery and exact revision accounting | `test_mind_reads_the_same_shared_hypothesis_and_captures_its_revision` |
| Single critical experience can be assessed immediately | `test_critical_notice_intake_does_not_need_a_failed_skill_or_a_full_stretch` |
| Claims, execution, observation and available actions remain distinct | `test_game_assessment_can_read_claim_execution_observed_state_and_available_actions_separately` |
| Same evidence does not become a second supporting occasion | `test_overlapping_game_sources_do_not_manufacture_independent_support`, plus shared revision/retry tests |
| Callback retries do not duplicate observations | `test_callback_retries_keep_one_event_and_one_observation` |
| Reset fences prior learning while preserving evidence and generated programs | `test_consolidation_reset_preserves_evidence_and_programs_and_fences_old_intake` |
| No legacy API delivery fallback | `test_old_routes_and_workers_are_absent`, worker/health regressions and runtime reference inspection |
| Conversation, recall, capture, skill writing/improvement/practice, trials and challenges continue to work | Existing regression suites retained and run alongside the new tests |

The full migration chain was applied to an empty disposable database, and the new migration was downgraded and upgraded successfully. Frontend TypeScript/production build and lint passed; lint reports three existing Fast Refresh warnings in unrelated hooks. Broader experience understanding remains the next package.

Final results: 325 tests passed in the combined backend regression suite. After adding lesson-source links to game decision captures, the targeted capture/consumer suite passed all 45 tests, including the new ownership regression. Frontend build/lint and migration upgrade/downgrade/upgrade checks passed. No development migration, reset or live-model evaluation was run.
