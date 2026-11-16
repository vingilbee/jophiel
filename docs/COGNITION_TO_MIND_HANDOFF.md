# Remove cognition and complete the persistent mind architecture

Date: 2026-11-13
Status: implementation handoff; the main runtime replacement is still outstanding.

## Task and authorization

Implement the architecture agreed with the user. Remove the separate cognition system completely,
transfer its remaining responsibilities to the mind, and clean the codebase and system of retired
cognition infrastructure and artifacts. This is an implementation task, not another proposal.

The user explicitly authorized removing cognition logs, reports, Markdown documents, training data,
and related retired material. Preserve unrelated work and data. Do not stop at disabling cognition,
renaming Student 2 to mind, or leaving an old decision service behind a new wrapper.

Read the current `CLAUDE.md`, applicable repository instructions, and `docs/MIND_ARCHITECTURE.md`.
Inspect the working tree before editing: this checkout already contains substantial uncommitted desktop,
security, mind, and personality changes, as well as thousands of intentional cognition artifact deletions.
Do not reset, overwrite, restore, or commit unrelated changes. File names below are inspection starting
points, not a complete dependency inventory.

## Agreed decisions

1. Remove Student 1 and its separate message-interpretation stage entirely.
2. Remove appraisal and the mood system entirely, including the observational after-reply pipeline.
   Do not recreate these under a different name or keep numerical mood/personality scores.
3. Transfer all other cognition decisions to one mind: speaking, asking, acknowledging, partial answers,
   silence, further thinking, recall, starting/changing/stopping work, activity choices, and responding
   to completed work or proposals from learning and other components.
4. The mind owns conversation continuity: what to note, unresolved questions, promises, intentions,
   what to revisit, what to close, and what she is waiting for. Memory persists those choices.
5. The mind owns scheduling choices: when or under what condition to reconsider an intention.
   Scheduling infrastructure stores and triggers that choice; it does not choose social behavior.
6. Behavioral choices belong to the mind's prompt and reasoning. Do not implement a deterministic
   social policy, fixed question counts, canned quiet-time topics, mandatory acknowledgments, or
   hardcoded rules deciding whether she should speak, think, recall, or work.
7. Keep mechanical execution guarantees: authentication/ownership, valid tool arguments, transactions,
   resource bounds, leases, cancellation, freshness/version checks, and prevention of duplicate effects.
   These report facts and enforce execution validity; they must not impersonate a mind decision.
8. Immediate memory-tool results return directly to the same mind LLM tool loop. No Student 1,
   Student 2, separate cognition pass, or mouth decision gate sits between recall and the mind.
9. Personality is text-only in the database and loaded once per backend process into an immutable
   cache. Personality adaptation and hot reload are outside scope.
10. The mind remains stateful and can act without a new user message. A reply is one possible action;
    it is not the required result of every evaluation.

## What is already implemented

- `CoreMemory` in `backend/app/db/models.py` now defines `profile: Text` in place of the numeric/grouped
  personality columns. The table name remains `core_memory` to preserve existing references.
- `backend/alembic/versions/a6d2f8c4e9b1_text_personality.py` is an unapplied migration. It follows
  `d8b3e6f1a4c7`, seeds the prose character plus interests/preferences, preserves existing textual
  character content, and drops the legacy groups. Its downgrade recreates empty groups; it cannot
  recover discarded scores. Verify migration behavior against the actual migration graph and a
  disposable PostgreSQL database before deployment.
- `backend/app/services/personality.py` supplies `current()`, a process-cached immutable snapshot
  containing name, version, and text. It requires exactly one active, nonempty database profile.
- Existing personality consumers were redirected to this service, including quick thinking, hard
  thinking, planning, the old character-page compatibility accessor, and shared-state projections.
  Remove obsolete compatibility accessors as their consumers migrate.
- Approximately 3,751 offline cognition files were removed: reports, evaluation outputs, logs,
  training output data, dedicated documents, and obsolete scripts/design references.
- `backend/scripts/student_model.py` was retained because it also serves the existing live model.
  Remove it after the runtime replacement.
- Historical migrations were deliberately preserved.
- Prior targeted validation: 71 tests passed, 45 skipped; 1,340 backend tests collected. These are
  historical checks for the partial change, not acceptance evidence for the completed refactor.
- The database migration has not been applied. The live cognition runtime still exists and can still
  generate new logs. The unified mind loop, immediate memory continuation, and responsibility transfer
  are not implemented yet.

## Target runtime

```text
Message / observation / component proposal / work result / due intention
                               |
                          durable wake event
                               |
        assemble personality + current state + relevant context
                               |
                              MIND
                  decide / request tools / consume results
                     |                    ^
                     +-- immediate tools -+
                     |
                     +-- persist working state and intentions
                     +-- speak through delivery
                     +-- act through hands/tools
                     +-- start background work
                     +-- schedule reconsideration
                     +-- wait or remain silent

Background outcome / scheduled condition -> later wake -> fresh state -> MIND
```

One mind is a shared owner and evaluation interface, not one permanently running LLM request.
Different effort levels may remain implementation options; they must not become independent competing
owners of behavior or reintroduce a cognition decision stage.

### Immediate tool chains

A wake can start one LLM call. When it requests recall or another immediate tool, execute the tool,
append the tool call and matching result to that evaluation's conversation, and let the same mind
continue. Multiple independent tool requests can run together when safe. End the evaluation when
there are no more tool requests or the mind deliberately waits.

Preserve tool-call IDs and error results. A failed lookup is not an empty successful memory search.
Do not force an extra decision-model call after every tool, or an extra mouth-model call for text
that has already been worded. Keep wording/delivery ownership explicit if a separate mouth is retained.

Use bounded execution for operational reliability. If an evaluation must yield because of time or
resource limits, persist its continuation/status rather than dropping its intention or fabricating
silence as a deliberate choice.

### Later continuations

Long-running work and scheduled intentions must not keep an LLM call open. Persist what is being
pursued, what result or condition she is waiting for, and its context. End the current evaluation.
The outcome or due condition creates a durable wake event with fresh state.

A scheduler tick checks conditions; it does not automatically invoke the LLM each second. Preserve
relevant observation/activity sources, but avoid repeated evaluations for unchanged information.
Cancellation, changed intentions, missed wakeups, and restart recovery must be handled explicitly.

### State supplied on each wake

Assemble a bounded view containing:

- Wake reason, event identity, time, and scope.
- Cached personality text and revision.
- Current conversation and relevant observations/activities.
- Current attention, goals, approach, intentions, unresolved questions, and promises.
- Pending work, waiting conditions, and scheduled reconsiderations.
- Relevant prior decisions, actual action/delivery outcomes, and newly completed work.
- Available memories and usable capabilities; further recall is available through tools.
- State revisions needed to detect changes while the evaluation was running.

The database is authoritative between wakeups. Process-local variables and an ever-growing LLM
conversation are not the sole record of continuity. Reuse the immediate tool history within a chain;
load current persistent state for a later wake. Never treat an intention as proof an action happened.

Use the existing working-memory/intentions model where it fits. Avoid a second independent notebook
that duplicates memory. Define ownership and scope clearly: personality is agent-wide; memories and
working state must respect the user, conversation, and activity they belong to.

## Suggested implementation sequence

### 1. Inventory live dependencies and define the new boundary

Trace all callers, worker startup hooks, imports, route contracts, model servers, config, deployment
commands, tests, stored state, and frontend displays. Inspect especially:

- `backend/app/services/shared_state.py`
- `backend/app/services/cognitive_live.py`, `student_two.py`, `cognition_decisions.py`
- `backend/app/ai/state/student_two.py`, `decisions.py`, contracts and routing
- `backend/app/services/inner_state_live.py`
- `backend/app/ai/Cognitive/` and its prompts, student code, and character fixtures
- `backend/app/api/routes/chat.py`, `backend/app/workers/chat.py`
- `backend/app/main.py`, `backend/app/workers/runner.py`
- `backend/app/services/coordinator.py`, `coordinator_runtime.py`, `autonomous_speech.py`
- `backend/app/services/mind.py`, `mind_hard.py`, `mind_conversation.py`, `mind_security.py`,
  `mind_minecraft.py`, `mind_ticks.py`, and `game_planning.py`
- `backend/app/ai/mind/`, `backend/app/ai/tools/memory/`, memory/recall services
- Learning/component proposal producers and the paths that accept, decline, or defer their proposals.

Choose a common mind evaluator and its durable event/state interfaces. Reuse working infrastructure;
remove obsolete abstractions rather than adding layers solely to preserve the old pipeline.

### 2. Implement the mind and its tools

Create the shared evaluation/tool loop and prompt. Give it working-state/memory tools, recall,
work/activity controls, scheduling, and speech/delivery capabilities. Adapt existing producers to
send scoped events and outcomes to this interface. Structured tool arguments are appropriate;
mandatory appraisal labels or a fixed checklist of internal thought fields are not required.

Ensure the mind can record/update/close unresolved questions, promises, and intentions, including
ones created during spontaneous thinking rather than a user request. A deferred intention must be
persisted and reconsiderable; it must not silently collapse into unrecorded silence.

### 3. Rewire every live path

Route text and voice input, activity/observation events, security findings, background results,
learning proposals, due intentions, and quiet-time continuations through the mind.

Remove Student 2 as the authority for pending replies, autonomous speech, finished thoughts,
activity changes, and component proposals. Remove Student 1 from message preparation and all
appraisal/mood after-reply hooks. Replace cognition-owned notes and their consumers with mind-owned
working memory, preserving meaningful open state before deleting old storage.

Audit current gates/sweeps/cooldowns. Replace behavioral rules with mind decisions where they
currently decide social behavior or initiative. Retain necessary execution checks and input event
coalescing. For changed circumstances, invalidate unsafe/stale execution and supply the change to
reconsideration where appropriate; do not silently lose the underlying intention.

Preserve actual delivery receipts, cancellation, memory-forgetting fences, activity ownership,
voice targeting, and duplicate prevention. Ordinary conversation must not depend on a desktop or
game session. She must not require another user message to resume valid background intentions.

### 4. Remove retired runtime and artifacts

After rewiring consumers, delete the unused cognition/Student 1/Student 2/appraisal/mood modules,
model servers, model assets, prompts, fixtures, warmup hooks, config/env examples, scripts, dependencies,
and dedicated tests. Move genuinely shared utilities into their actual owning subsystem before
removing their old directory. Do not keep `Cognitive/` as a generic utility dumping ground.

Remove all retired log writers as well as existing log files so they cannot be regenerated.
Remove cognition reports, dedicated Markdown plans, training/evaluation data, caches and build outputs,
including ignored runtime artifacts where present. Inspect `backend/var/`, `docs/`, design references,
and deployment commands rather than assuming tracked files are the entire system.

Audit learning capture, training examples, system signals/status/parts, and shared-state traces.
Delete cognition-only paths and data. Retain useful mind/action/learning observability and general
learning functionality. Mixed tables and capture stores require selective cleanup; do not delete
memory evidence or records other systems still reference.

Use new forward migrations for retired database columns/tables and deliberate cleanup of obsolete
stored cognition state. Preserve historical Alembic migrations and the ability to migrate a fresh
installation. Avoid global database, Redis, or filesystem resets. Remove only identified retired
cognition records/keys and preserve user conversation, memories, personality, and unrelated work.

Update the frontend: remove the cognition system node, Student 1/2 panels, appraisal/mood displays,
types, endpoints, and trace assumptions. Show mind-owned decisions, continuations, and outcomes in
the existing system view. Update meaningful labels, signals, and error routing instead of merely
hiding the old panels. Remove broken references from active documentation, then update
`docs/MIND_ARCHITECTURE.md` and `CLAUDE.md` to describe the final implemented system.

### 5. Verify and finish

Use `uv` for backend commands and `bun` for frontend commands. Run meaningful tests around changed
behavior, relevant existing regressions, frontend checks/build, migration checks, and an end-to-end
smoke path where infrastructure is available. Clearly report skipped infrastructure-dependent checks.

Required acceptance scenarios:

- A normal text/voice conversation runs without either student, appraisal, or mood services/models.
- Recall requested by the mind returns directly into its immediate tool chain and informs the next
  decision; memory failure remains distinguishable from no matching memory.
- A question, promise, or intention survives the current evaluation and a worker restart; the mind
  can update or close it later.
- The mind schedules reconsideration, ends its evaluation, and wakes later without new user input.
- Background work completes or fails, wakes the mind, and is considered with the current situation.
- She can remain silent, act without speaking, or speak without starting work, as chosen by the mind.
- A component proposal can be accepted, declined, or deferred without cognition.
- A new message or changed activity during work does not cause stale or duplicate speech/actions.
- Duplicate events, retries, concurrent workers, cancellation, and restarts preserve continuity and
  do not repeat committed effects. Re-evaluations of a user's mind state are coordinated.
- Exactly one cached text personality snapshot is shared per process; no numeric personality or
  appraisal/mood path is required. Independent LLM calls receive the personality text they need.
- The UI and logs represent the final system; no retired cognition logs or training assets regenerate.
- Fresh database migration and upgrade from the previous schema work. User memories and unrelated
  learning/desktop/security functionality remain intact.

Search for residual references to cognition, Student 1/2, appraisal, mood, legacy configuration,
retired endpoints, and character-file loading. Inspect matches rather than blindly deleting words:
historical migrations and accurate migration/handoff documentation may legitimately mention removal.
No executable path may still depend on the retired system.

Finish with a concise account of the implemented architecture, deleted subsystems/artifacts, migrations
and deployment steps, verification performed, and any genuine remaining limitations. Do not call the
migration complete while old runtime services, behavior owners, model dependencies, or log writers remain.
