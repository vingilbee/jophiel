# Post-audit 1: fill missing experience capture

Status: design agreed through the user discussion on 2026-11-04. Built 2026-11-04: see [POST-AUDIT-01-implementation.md](POST-AUDIT-01-implementation.md) (both implementation checks resolved there). This is the first work package in the new post-audit order, not a replacement or restart of historical steps 1–4.

## Purpose

Learning must be able to inspect what happened without inventing missing facts. Preserve detailed experience that can also support future hands-model training, without turning every game tick into a lasting memory or treating a model's output as a correct training target.

Foundation: [audit](AUDIT-2026-11-04-learning-from-experience.md), [existing capture decisions](01-capture-and-ownership.md), and [existing implementation](01-implementation.md). The general learning design reviewed for this discussion is currently in the sibling checkout at `../app-cognitive/docs/learning-system/GENERAL_EXPERIENCE_LEARNING_DESIGN.md` relative to the repository root. Its one-learning-path consolidation belongs to a later post-audit package, not this capture package.

## Accepted decisions

- Capture planner decisions, hard thoughts, and hands execution. Preserve existing conversational capture. This package does not add mouth wording or the separate VM driver as new captured learners.
- Record ordinary activity, exploration, successes, and failures, including activity without an assigned goal.
- Keep action detail as complete as available: commands, inputs, results, tick states, position, health, inventory, interruptions, retries, and recovery.
- Capture both what she proposed and what the application actually applied or executed.
- Record available damage and death causes from the game interface. Extend the Minecraft pack as needed. A cause the interface does not expose stays unknown; a model's explanation stays a separate claim.
- Keep later events in order, even after an action reports completion. Temporal order alone does not establish cause.
- Keep three outcome reports separate: what she says she achieved, what the action reports, and what observed game state establishes.
- Continue acting if saving fails. Save asynchronously with bounded retries and expose detected missing records. Existing crash-loss limitations remain explicit.
- Keep the existing 30-day raw-capture retention policy. No continuous video capture is requested.

## Observation, capture, and memory

Reuse the existing services and source records rather than introducing another learning controller.

1. The component making a decision or executing an action supplies its actual inputs, outputs, and execution events to the shared capture service. Exact decision inputs must be frozen at decision time; observation processing cannot reconstruct them afterward.
2. Observations report events and link to their detailed source records. Memory and learning can consume observations; detailed evidence need not pass through a memory summary before learning can inspect it.
3. Memory owns useful retained experiences and learned products. A tick or command must not automatically become its own lasting memory or prompt an LLM call.

The raw trajectory is detailed evidence, not a second lesson store. This package adds capture and links; it does not redesign learning intake, lesson acceptance, retrieval, or the memory lifecycle.

## Decision records

Extend the existing attempt identities and capture contracts to planner and hard-thought calls. Preserve:

- Exact supplied instructions, prompts, schemas, state, goal, constraints, and available observation or media references.
- Actual model/provider identity, settings, schema and code versions, timestamps, and source ownership.
- Retrieved memory or lesson content/revisions as supplied, using immutable references only where exact content remains retrievable.
- Actual raw and parsed output, validation failures, retries, and any concise rationale, prediction, alternatives, or `follows` link actually supplied. Do not manufacture missing fields or require hidden reasoning.
- Upstream attempt, goal, effort, plan revision, step, and execution links where known. Activity without a goal remains valid; do not invent a goal to fit the schema.

For the planner, retain the proposal separately from the applied plan revision, including rules and constraints actually carried forward by application code. Record the link between them. This exposes problems such as stale rules without silently fixing planning behavior as part of capture.

## Commands and tick states

Capture each client-observed game tick while connected and active, including ordinary activity outside skill runs. “Every tick” means the ticks exposed by the installed client, not guaranteed access to every server simulation state.

Preserve ordered commands and lower-level control inputs exposed by the execution stack, with arguments, dispatch/start/end times, results, partial failures, cancellation, interruption, and recovery. Preserve generated programs and their actual execution sequence where present. Distinguish proposed commands from commands dispatched and results received.

Record available position, orientation, movement/control state, health, food, inventory/equipment, and relevant observed surroundings. Record freshness and coverage: cached surroundings are not a new perception on every tick, and unavailable state is not an empty world or zero health.

Implementation may use compressed chunks, initial snapshots, exact state changes, and periodic checkpoints to avoid storing identical data repeatedly. This is an implementation proposal, not permission to silently downsample. The recorded per-tick sequence must remain reconstructable, including unchanged ticks, within declared coverage. Keep bases/checkpoints available for as long as retained chunks depend on them.

Use stream/run identities, sequence numbers, and timing sufficient to identify order, duplicates, reconnects, and detected gaps. A restart or disconnection must not appear to be continuous observation. Cross-process clock uncertainty must not be hidden by invented ordering.

Do not run expensive full surroundings scans or database writes synchronously on every tick. Batch persistence and preserve what the client actually knows. Measure storage and runtime overhead before claiming this is affordable. If the requested detail cannot be sustained, report the limitation rather than silently substituting a lower recording rate.

## Damage and outcomes

Capture available damage/death events independently of the rate limit on notices shown to cognition. Keep health changes, event times, reported cause, and the source of that cause. Preserve relevant nearby observations as context, without treating proximity to lava or a mob as proof.

Before implementation, verify the installed Mineflayer and Minecraft protocol versions and which events or packets expose damage causes. The user's expectation that Mineflayer provides the information is not yet a verified technical guarantee. Where no cause is available, record that explicitly.

Keep these reports distinct even when they disagree:

| Report | Example |
| --- | --- |
| Her claim | “I finished the armor set.” |
| Action result | Crafting boots returned success. |
| Observed outcome | Inventory contains boots but no helmet. |

Attach measured state and its observation time/coverage. Do not infer full goal completion from a successful command, or overwrite her earlier belief with a later observation. A generic proof of arbitrary goal completion is not required: unsupported completion remains unknown.

Continue recording subsequent activity so delayed consequences can be inspected. Preserve known execution links; where only timing is known, preserve timing without asserting causation. Semantic experience boundaries and causal assessment belong to later work.

## Retention and failure behavior

Raw decisions, executions, and new trajectory records follow the accepted 30-day raw-capture policy and existing user/source deletion boundaries. Retention must also cover trajectory chunks and referenced payloads, not just database rows. Apply existing ownership and access restrictions throughout.

Observation retention is separate: current code defaults to 14 days for routine observations, with unfinished processing and memory-evidence references protecting observations from routine pruning. This package does not change that rule. A surviving observation or memory link does not extend the detailed raw capture's lifetime; indicate when the linked detail has expired.

Memories and already-created training examples do not expire merely because raw capture expires. Existing correction, training deletion, and user eligibility rules remain unchanged. This package does not automatically create positive hands-training examples, promise that all raw experience survives until training, or authorize cross-user training.

Use the existing asynchronous capture service and retry-safe identities. High-volume trajectories may need bounded batching and queue settings appropriate to their volume. If queues overflow or retries exhaust, expose detected loss and continue acting. Unknown execution results remain unknown. A sudden crash can lose buffered data without an exact count of what was lost; do not claim complete recovery.

## Completion evidence

Demonstrate the following against the implemented path, with automated checks where appropriate:

1. Reconstruct a planner decision, its applied revision, a hard thought, and the associated hands sequence from actual captured inputs through results. Later observations never appear in earlier decision inputs.
2. Reconstruct ordinary activity with no assigned goal, successful actions, failed actions, interrupted actions, and recovery. Missing links remain visible.
3. Reconstruct the available per-tick state and command/input order from stored chunks, including unchanged ticks and checkpoint boundaries. Verify against the source stream, not only against the encoder's own output.
4. Show reported damage causes where supported, unknown causes where unavailable, and rapid damage/death events that are not lost merely because cognition's notices are rate-limited.
5. Show disagreement between her completion claim, a command result, and observed inventory without collapsing them into one success label. Keep a later consequence visible without inventing its cause.
6. Show that storage outages, queue pressure, reconnects, and restart do not block action or fabricate complete trajectories. Retries do not duplicate evidence.
7. Verify raw retention, source deletion, access boundaries, expired references, and chunk/checkpoint cleanup. Verify that routine tick capture does not create one memory or model call per tick.
8. Report measured bytes per minute/hour, estimated 30-day storage at the observed activity rate, queue behavior, and action/tick overhead from representative activity. Exact storage settings must follow this measurement; no numeric performance guarantee has been agreed.

## Scope boundary

Do not restart historical steps 1–4, consolidate the two learning paths here, change lesson trust, add new learning consumers, train a model, or fix unrelated planner behavior. Preserve evidence needed for those later changes. The implementation is complete when learning can inspect this richer record honestly, including its gaps; demonstrating improved learned behavior is a later milestone.
