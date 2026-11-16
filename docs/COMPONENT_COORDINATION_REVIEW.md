# Component coordination: implementation review and proposed contract

> **2026-11-13:** Student 1, Student 2, the shared-state pipeline, appraisal and mood were retired; one mind now decides every turn and wake. Where this document describes them, it records the system as it was. Current design: [MIND_ARCHITECTURE.md](MIND_ARCHITECTURE.md).

Reviewed 2026-10-21. This is a source-code review, not verification of deployed flags or a runtime concurrency test. The user confirms student/teacher cognition is current and V0 shadow is no longer used. V0 is legacy and excluded from the target design. Memory internals are being changed separately; this review defines integration boundaries only. No runtime behavior is changed by this document.

## Assessment

The system has meaningful local boundaries, but no single coordination authority. Complexity is concentrated in cross-component control, output delivery, and independently assembled context. It does not need a rewrite of every component. It needs explicit ownership and a common protocol for scheduling work and accepting results.

Existing strengths include Source/Body protocols, scoped activities, mind gating and cooldowns, stale-thought checks, planner revision checks, durable job queues, and observation consumer receipts. Preserve these.

## What exists today

Paths below are relative to `backend/app`.

| Component | Actual responsibility / trigger | Evidence |
|---|---|---|
| Chat orchestrator | Assembles tools, delegates, and generates user-facing text on a chat job. Decision-making and wording are combined. | `ai/agent.py:64`, `ai/chat.py:231`, `workers/chat.py` |
| Mouth | A conceptual role spread across chat streaming, background `agent_say`, socket/audio handling and TTS; not one exclusive delivery authority. | `ai/chat.py:231`, `services/agent_say.py:135`, `services/tts.py` |
| Student/teacher cognition | Live preparation supplies character, typed memory, student reading and conversation notes according to flags. After-reply processing updates notes. Teacher-related tooling also exists for training/evaluation; that does not mean the complete teacher workflow runs on every live turn. | `services/cognitive_live.py:172`, `:184`, `:256`, `ai/Cognitive/simple/` |
| Quick mind | Polls sources, builds moments, gates reaction/light/quiet work, saves thoughts. Can directly deliver a generated spoken line. | `services/mind.py:226`, `:289`, `:368` |
| Hard mind | Schedules problems with per-user attention, gathers context, runs tool-free thinking, then routes results to hands, intentions or questions. Can deliver a pending question directly. | `services/mind_hard.py:361`, `:408`, `:538` |
| Conversation mind source | Event-driven, currently explicitly shadow-only. Offered an open question after eligible student note updates; not a generic live conversation mind running each second. | `services/mind_conversation.py:56`, `:126`; `services/cognitive_live.py:277` |
| Planner | Dedicated game-plan author when enabled; applies decisions against a read revision. Also directly delivers planner speech. | `services/game_planning.py:271`, `:335` |
| Hands | Independent execution loop, selecting/executing skills under session/plan state and reporting outcomes. Not merely a function called once per mind tick. | `ai/desktop/skill_driver.py:342`, `:371`, `workers/desktop.py` |
| Observation | Normalizes source records into immutable reports, deduplicates and tracks processing receipts. Current consumer is memory. Does not call a model. | `services/observation.py`, `ai/observe/registry.py` |
| Recall / memory | Recall exposes retrieval to chat/tools; memory owns durable records and projections. They are collaborators, not the runtime coordinator. | `workers/chat.py:213`, `ai/chat.py`, `services/recall.py` |
| Inner state | Post-reply background appraisal and mood state for inspection. Explicitly has no influence on replies, thoughts, plans or actions in this implementation. | `services/inner_state_live.py:1` |

The student/teacher architecture and conversation-mind shadow flag are distinct. Retiring V0 does not by itself make the conversation mind source live.

## Concrete coordination problems

1. **Mind is not private thinking today.** Quick thoughts call `agent_say.deliver`; hard thinking can call `Body.command` and later deliver questions. Planner speech also bypasses the chat orchestrator. The desired rule “mind thinks, orchestrator decides, mouth speaks” is not enforced.
2. **Output restraint is distributed.** `agent_say` has cooldowns and a 20-second mind hush, while hard questions separately check pending chat jobs. The code comments explicitly describe earlier duplicate/conflicting replies. These mitigations are useful but are not a shared output reservation protocol.
3. **There is no common snapshot.** Chat constructs context from history, recall, student state, working memory and game state through separate reads. Mind maintains process-local activity/attention state plus Redis and database records. Hands owns another live execution view. These views can legitimately represent different moments without a single common freshness contract.
4. **Infrastructure work depends on mind enablement.** The only discovered application call to `observation.tick` is in `mind.run_once`, after the mind-enabled check. That loop also runs memory due checks. Observation recovery and maintenance should have a lifecycle independent of whether thinking is enabled. Coordinate the eventual change with the memory work.
5. **Scheduling abstractions are incomplete.** `Rhythm.every` exists, but `mind.run_once` does not use it to choose when each polled source is due. It loops over enabled poll sources on the worker cadence. The gate is evaluated before the no-fresh-news suppression in `tick`, so suppressed thinking does not necessarily mean zero gate cost.
6. **Some guarantees are process-local.** Attention locks, activity dictionaries and thread pools coordinate within one process. They do not establish cross-process ownership. Existing durable job leases should be extended or reused where coordination must survive restart or multiple workers.
7. **Source adapters carry too many concerns.** The Source protocol includes sensing, problem discovery, context gathering, speech settings, body access and handoff lookup. Adding a source therefore requires knowledge of several unrelated responsibilities.

## Target ownership contract (proposal, not implemented)

| Component | Owns | Must not own |
|---|---|---|
| Runtime coordinator | Scheduling, work leases, cancellation, state revisions, accepting/rejecting results, output/action reservations | Conversational reasoning or expensive model calls in its scheduling loop |
| Conversational orchestrator | Chooses conversational response, tool delegation, and whether a thought merits speech/action; creates authorized intents | Direct writes to another component's internal state |
| Student cognition | Interpretation/conditioning of relevant conversation input; typed reading and note-update proposals | Delivery or execution authority |
| Mind | Private thoughts, questions, hypotheses and suggested actions with evidence/freshness metadata | Direct speech, hands commands, or plan mutation |
| Planner | Detailed plan proposals and plan-domain validation; one owner for plan revisions | Direct speech or bypassing command authorization |
| Mouth | Wording/rendering and delivery of authorized speech, streaming, interruption, delivery receipts | Selecting new goals or independently deciding to speak |
| Hands | Executing authorized plans/commands, local control and bounded reflexes; progress/failure receipts | Changing high-level goals or speaking to the user |
| Observation | Capture, normalization, source identity and provenance | Deciding what to say or do |
| Memory | Durable memory and memory-owned projections | Scheduling the whole agent |
| Recall | Evidence retrieval for a scoped request | Independently initiating behavior |
| Inner state | Owned mood/appraisal projection, if enabled | Implicit control influence before that influence has an explicit contract |

The runtime coordinator is deterministic infrastructure, distinct from the existing LLM chat orchestrator. Do not add an LLM hop to every tick. Initially, keep orchestrator wording generation in place and extract the exclusive delivery boundary first. A separate mouth model is optional, not a prerequisite.

Hands may continue local movement/reflexes under an already-authorized plan without consulting the orchestrator for each motor action. This exception must be bounded by session, plan revision, allowed controls and cancellation.

## One state, multiple rates

Use one logical, versioned agent view per user, with conversation/activity subscopes. Do not use one global mutable dictionary or copy all memories and raw sensor frames into it. Domain stores remain authoritative; the shared view carries small owned projections, references, source versions and freshness.

Suggested view fields:

```text
AgentView
  user_id, revision, captured_at
  conversations[id]: latest_message_id, summary_version, cognition_version
  activities[id]: source, status, goal_version, plan_version, observation_cursor
  attention: running_work, queued_work
  output: reply_owner, speech_reservation, interruption_generation
  components[name]: enabled, mode, status, last_input_version, last_success, error
  projections: memory_refs, thought_refs, inner_state_ref
```

A snapshot is consistent with recorded input versions, not a promise that external reality stops changing. Mark missing/stale projections explicitly. Preserve original user messages as references so summaries do not erase recent corrections or intent.

On a tick, the coordinator drains durable events, updates relevant projections, runs cheap eligibility checks and schedules due work. Workers receive a stable scoped view or reference to it. They return asynchronously. The next tick never waits for every component to finish.

| Component | Wake condition | Normal idle behavior |
|---|---|---|
| Observation | Source event or recovery deadline | No model call |
| Student | New relevant conversation input/version | Reuse previous reading when still applicable |
| Conversation summary/notes | New completed exchange or explicit invalidation | No reprocessing of unchanged history |
| Mind | Relevant change, explicit problem, permitted reflection deadline | Skip; do not regenerate identical summaries |
| Planner | Goal change, failure, planning request | Keep current valid plan |
| Hands | Active command, controller deadline, environment change | Continue local authorized execution or idle |
| Mouth | Authorized speech intent and available output slot | Silent |
| Recall | Explicit retrieval request | Idle |
| Memory | Owned ingestion/consolidation schedule | Independent of mind toggle |

Deliver immediate user/cancellation/danger events without waiting for the next periodic heartbeat. Coalesce replaceable sensor updates; never silently discard user input, cancellation or delivery receipts. Bound queues and allow at most one conflicting work lease per scope/component.

## Result acceptance and synchronization

Every work/result envelope should include a schema version, event/work ID, user and activity/conversation scope, producer, causation ID, dedupe key, input versions, creation/deadline times and cancellation generation. Thought proposals additionally distinguish evidence, uncertainty, suggested speech/action and expiry.

For example, a mind result based on plan 8 must not steer hands now executing plan 9 without revalidation. An unrelated mood update should not automatically invalidate a valid plan result: compare relevant dependency versions, not only the top-level view revision.

The coordinator validates scope, lease, dependencies, cancellation and expiry before accepting an intent. Accepting an intent is not the same as successful execution. Track accepted, running, delivered/completed, refused, failed and cancelled outcomes separately. Use transactional state/job publication where required; assume at-least-once delivery and make consumers idempotent rather than claiming exactly-once external effects.

All speech paths must use the same output reservation and interruption policy, including normal replies, quick thoughts, planner questions and background game turns. Preserve channel/conversation targets explicitly instead of implicitly choosing the user's latest conversation.

## Incremental cleanup sequence

1. **Contracts and inventory.** Adopt the role matrix; expose effective enabled/live/shadow status and each component's trigger. Record current behavior before changing routes. Keep legacy V0 out of the active architecture.
2. **Exclusive speech boundary.** Route mind/planner speech as proposals through common arbitration. Keep the existing hush/urgency rules during migration. Include ordinary chat in the reservation protocol; wrapping background speech alone is insufficient.
3. **Exclusive action boundary.** Route mind-to-hands suggestions through authorized command proposals, preserving planner revision checks and local hands autonomy.
4. **Versioned view and result envelopes.** Adapt existing stores rather than moving every record. Start with conversation, activity, plan and output versions. Add correlation IDs to existing logs.
5. **Independent scheduling.** Separate observation/maintenance lifecycle from mind; honor per-component cadence and changed-input eligibility. Coordinate memory-facing edits with the agents already working there.
6. **Remove bypasses.** Once adapters are wired and verified, remove direct deliveries/commands and make boundary violations fail architectural checks. Split Source into sensing and optional problem/context capabilities as concrete adapters migrate.

Do not ship an unused coordinator abstraction alongside all old paths and call the cleanup complete. Each step must migrate a real caller and establish a measurable invariant.

## Required verification for implementation

- A mind thought cannot deliver speech or command hands directly.
- A normal reply and a competing background thought cannot both acquire the same exclusive output slot; permitted interruptions are explicit.
- Old results after cancellation, conversation switch or plan revision are rejected/revalidated, with a recorded reason.
- An unchanged heartbeat triggers no student/summary/model work unless an explicit timed policy is due.
- Slow or failed mind work cannot block chat or local hands execution.
- Duplicate events/retries do not duplicate a command or delivered message.
- Observation recovery continues with mind disabled.
- Restart/multiple-worker tests establish lease ownership beyond process-local locks.
- Every decision can be traced: input event → input versions → eligibility/skip reason → work → proposal → acceptance/refusal → execution receipt.

This review adds design documentation only. Runtime tests and migrations belong to the implementation steps above; no live behavior or memory files were modified.
