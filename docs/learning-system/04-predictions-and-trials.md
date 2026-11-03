# Predictions and bounded game trials

Status: design decisions accepted 2026-11-03; built on request the same day ([implementation](04-implementation.md)). [Plan index](README.md).

## Outcome

An experiment compares a pre-action prediction with observed effects.

Dependencies: Step 2; coordinate lesson interfaces with step 3.

## Work

- Reuse existing game practice and skill evaluation.
- Record hypothesis, alternatives, predicted observations, uncertainty, observation window, costs, and stop conditions before execution.
- Cognition selects; bodies execute; learning assesses and revises through existing owners.
- Keep simulated outcomes separate and absent observations unresolved.

## Accepted decisions

### Proposals and selection

Learning may autonomously investigate execution failures, missing skills, and unresolved hypotheses relevant
to an active game goal, spending tokens without asking the user each time. These triggers start investigation
and proposal generation, not automatic physical execution.

Initially an LLM uses current game state, available skills, and relevant experience to produce a structured
proposal: hypothesis, a small set of valid alternatives, predicted observations, qualitative uncertainty,
prerequisites, observation window, costs, limits, and a recommendation. A trained world model is not required.
Predictor expectations remain distinct from cognition's selection; untried alternatives have predicted results,
not observed ones. Increasing sampling temperature is not the exploration mechanism.

Cognition receives the alternatives, predictions, costs, and recommendation and decides whether and when to
pursue, defer, or reject. It can consider a proposal while hands are busy. Arrival alone does not interrupt
execution; cognition explicitly schedules any switch.

### Persistence and deferral

Persist the complete proposal when learning creates it, before cognition reviews it. Learning owns the
authoritative experiment lifecycle. Link existing hypotheses/questions through their memory/thought owners.
When deferred, working memory holds a linked reminder through its existing owner, with cognition's reason
and a reconsideration condition. Relevant changes such as finishing a goal or obtaining materials bring it
back to cognition; do not present it on every tick or automatically execute it.

New evidence about the same unresolved problem updates its pending proposal instead of creating duplicates.
Preserve revisions and earlier deferral reasons. A materially different test may be a separate proposal linked
to the same question.

### Preparation, execution, and bounds

After cognition selects a trial, the existing game planner arranges missing prerequisites and bodies execute
preparation. Learning specifies requirements; mind/planning reasons about how to satisfy them; cognition
retains authority over pursuing the experiment. Execution checks actual readiness immediately before the
test. Failed preparation is distinguishable from evidence that the technique failed.

Link proposal, cognition selection, preparation, actual execution, observations, and assessment, including
failures and interruptions. Changed approaches need fresh predictions before execution. If cognition changes
the proposed action, preserve the original and obtain an applicable prediction before treating the new action
as the experiment. Never rewrite expectations after observing results.

The first scenario is climbing a small obstacle using existing movement and block-placement capabilities.
Configurable starting limits per trial are three execution attempts, five minutes total including preparation,
and eight blocks placed, whichever is reached first. Preparation costs count toward the overall limits while
remaining distinguishable from test costs. Exhausting preparation budget can mean the test never started.

Cognition may stop, pause, or abandon a trial as circumstances change. Danger, missing conditions, and
competing goals are considerations, not new blanket instructions requiring automatic stopping. Existing game
safety controls still apply. Reaching an agreed limit ends the trial; further experimentation requires a fresh
proposal selected by cognition. Preserve partial observations and stopping reasons.

After restart or loss of contact, recover the record and check what actually happened before resuming. Do not
blindly repeat actions that may already have executed. Unrecoverable outcomes remain unknown and cognition
decides the next action.

### Assessment and revision

Each trial defines an observation window and required evidence. Execution completion, interruption, or window
expiry triggers assessment of available evidence, explicitly recording missing coverage. Later evidence may
revise it. Code measurements establish concrete effects such as reaching the destination, inventory changes,
time, and damage; learning interprets what they support and preserves alternative explanations. Interruption
or absent observation alone does not contradict a prediction.

Every trial produces an assessment, not necessarily a lesson. Supported results feed existing hypothesis,
lesson, and skill owners under their acceptance rules. One success does not automatically establish a reusable
strategy. Preserve applicability, evidence, and exceptions; simulated outcomes remain separate.

## Existing foundations and integration gaps

Inspection during the discussion found autonomous game practice, a prerequisite-aware planner, and independent
mind and hands loops. Student 2 does not run on every Minecraft state tick. Existing hard thoughts can send
hand tips directly, so the new cognition-selection boundary must be implemented explicitly.

Current practice queues a task description without this structured proposal, preparation, selection,
prediction, and assessment handshake. Existing `GameProposal` records suggest code changes for human review;
they are not executable trial proposals. Recheck contracts before implementation and reuse existing owners.

## Implementation choices still open

Concrete schemas, exact model selection, candidate validation, cognition input compatibility, event/worker
wiring, retry/recovery mechanics, and measurement predicates remain implementation work. The accepted limits
are initial scenario defaults, not universal budgets. This plan does not authorize coding.

## Completion evidence

Demonstrate a supported hypothesis or strategy revision changing a later relevant decision, then evaluate from
a changed obstacle or starting position with a fresh pre-action prediction. Preserve the original trial.
Revising again after evaluation is another learning attempt, not proof the earlier strategy transferred.
Include an inconclusive case that produces no unsupported lesson. Records alone or an isolated successful
action do not establish learning. Preserve competing explanations.

## Boundary

Use one shared foundation, with executable game experiments first. Conversational experiments, including
continuation across multiple messages, require a separate design discussion and are not enabled here.
Existing conversational correction and adaptation work is not postponed by this boundary.

