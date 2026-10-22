# Shared-state coordination refactor — implementation handoff

Status: planning specification, 2026-10-21. No runtime implementation accompanies this document.

## Purpose and agreed decisions

Make component responsibilities explicit and coordinate conversation, reasoning and activity through a versioned shared view. Preserve the current student/teacher cognition architecture. V0 shadow is legacy and is not part of this refactor.

- Mouth and orchestrator are the same conversational component. Do not introduce a separate mouth model.
- Cognition interprets every normal user message before mouth chooses its response.
- Mouth can respond without mind. Mind owns deliberate/critical thinking, not every reply.
- Jev decides whether/how much deliberate thinking is needed. It is not a classifier at every branch.
- Recall is conditional. Recent conversation and usable cached memories may be sufficient.
- Mind cannot deliver speech directly. Thoughts intended for conversation go through cognition, then mouth.
- Mouth may acknowledge pending thinking or remain silent, according to cognition/personality and circumstances.
- Observation records events and provenance. Interpretation and future learning derive meaning separately.
- Memory owns storage, corrections, organization, retrieval and summarization. Other components may request memory operations through its interfaces.
- A tick checks eligibility and deadlines. It does not invoke every model or wait for every component.
- This session produces a spec only. The implementation agent must inspect current code and concurrent memory changes before modifying it.

## Current implementation and gaps

See `COMPONENT_COORDINATION_REVIEW.md` for the source-backed inventory. Where that earlier proposal differs, this spec takes precedence: mouth/orchestrator stays combined, and current cognition means student/teacher.

Current normal chat flow, established by inspection:

1. WebSocket input or transcribed voice is persisted and a chat job is queued.
2. `workers/chat.py` loads history/setup and starts memory retrieval; it awaits retrieval before student preparation.
3. `services/cognitive_live.py` prepares optional student reading and notes. Student receives recent conversation, existing notes, up to five retrieved “about them” entries, notices and additional fixed/default inputs. Reading is exposed to mouth in the reading arm.
4. The worker assembles `AgentRuntimeContext`; `ai/chat.py` runs the tool-capable mouth/orchestrator.
5. Text/audio streams; the assistant result is persisted. Successful completion schedules consolidation and processes conversation notes; dialogue updates have their own scheduling. Optional inner-state processing is observational.

Missing: common versioned view, enforced per-message cognition/routing output, cache-sufficient recall bypass, normal-message Jev-to-mind handoff, mind-result-to-cognition-to-mouth route, and shared stale-result/output arbitration.

Existing paths to migrate: quick mind directly calls `agent_say.deliver`; hard questions and planner speech can also call delivery directly. Hard mind sends textual tips through the Minecraft Body adapter; this is not direct low-level movement. Preserve existing safeguards until replacements work.

## Component ownership

| Owner | Responsibility | Output |
|---|---|---|
| Coordinator (code) | Event ingestion, scoped revisions, eligibility, leases, cancellation, dependencies and output reservations | Scheduled work, accepted/rejected results |
| Cognition | Interpret user inputs; evaluate completed thoughts against current context/personality | Interpretation and proposed conversational action |
| Jev | Decide whether deliberate thinking is required and its depth | Thinking decision with reason |
| Mouth/orchestrator | Converse, phrase responses, delegate specialist operations | Reply or tool request |
| Mind | Deliberate thinking using supplied context and evidence | Conclusion, uncertainty, missing evidence, proposed communication |
| Memory | Own memory records and retrieval/cache validity | Evidence references and bounded projections |
| Observation | Record what occurred with identity, time and provenance | Immutable source reports |
| Inner state | Own mood/emotional projections | Versioned dynamic-state projection |
| Planner / hands | Existing game planning and execution | Plan/progress/result projections |

The coordinator is infrastructure, not another LLM. Owners publish typed changes; components do not mutate each other's internals or a shared Python dictionary. Mouth's existing tools can remain delegation interfaces. Do not move Gmail/document tools into Minecraft hands in this refactor.

## Shared-state contract

One logical AgentState per user; conversation/activity state is separately scoped. It is a bounded view over authoritative stores, not a replacement for those stores. Never mix users. Cross-conversation reuse must be explicit and authorized by the owning service.

All projections carry `owner`, `version`, `updated_at`, `source_refs`, and `status` (`available`, `pending`, `stale`, `unavailable`, or `disabled`). Absence is not evidence of a negative result. References must resolve under the same user's access scope.

| Section | Required content | Authority |
|---|---|---|
| Envelope | `schema_version`, `user_id`, `revision`, `captured_at`, `last_event_id` | Coordinator |
| Personality | Profile reference and version | Existing character configuration |
| Conversation | ID, conversation revision, latest message ID, bounded recent messages, summary reference/coverage, topic, open questions and commitments with provenance | Chat plus existing note/summary owners |
| Memory context | Cache entries, query/context signature, source record/version references, retrieval time, validity status, bounded rendered evidence, recall coverage/status | Memory |
| Cognition | Trigger reference, input versions, interpretation, proposed conversational action, missing evidence, communication preference | Cognition |
| Thinking decision | Trigger reference, `required`, depth, reason, relevant input versions | Jev or explicit deterministic user instruction |
| Dynamic inner state | Mood, immediate reactions, causes, timestamps, current focus; unknown/disabled distinguished from neutral | Inner-state owner; focus from attention owner |
| Activities | Activity ID/type, status, goal reference/version, priority, plan reference/version, blockers, progress | Existing activity/planner owners |
| Situation | Observation references, bounded environment summary, urgent changes, source timestamps/freshness | Source/observation owners |
| Mind | Active request references, topic, completed result references, uncertainty and input dependencies | Mind results; coordinator work status |
| Output | Channel/conversation target, active reply, output reservation, user speaking status, acknowledgement state, interruption generation | Coordinator/delivery; source voice telemetry |
| Work | Work IDs/types, scope, status, dependencies, lease, deadline, cancellation generation and result references | Coordinator |
| Health | Component mode (`live`, `shadow`, `disabled`), availability, last success and bounded error summary | Runtime component reporting |

Conversation structure must distinguish raw user messages from derived notes. Do not duplicate ownership of commitments: project the existing owner's records with references. A summary must declare which messages it covers, so newer messages remain visible and are not counted twice.

Cache validity is owned by memory. Never treat an old entry as current after its source is corrected/deleted. Initial integration may expose a version token or validation callback; implementation must agree this boundary with the ongoing memory work, not redefine memory internals.

Dynamic state is included in the contract, but enabling behavioral influence is a separate explicit change. Current observational mood must not silently start steering replies. No invented emotion update on every tick.

Minimum implementation slice: envelope, conversation, memory-context references/status, cognition, thinking decision, work and output. Other sections can be explicitly disabled/unavailable until their adapters are wired.

## Input and result envelopes

Every event/work/result includes:

```text
schema_version, id, user_id
conversation_id?, activity_id?, trigger_message_id?
producer, causation_id, correlation_id, dedupe_key
created_at, deadline_at?
input_versions (only actual dependencies)
cancellation_generation
payload or payload_ref
```

User-message requests require conversation/message identity. Autonomous work requires its activity/event identity and an explicit conversational target before speech is authorized. Never silently deliver to the latest conversation simply because it is latest.

Cognition output additionally contains:

```text
phase: user_input | thought_result | autonomous_event
interpretation
conversational_action: respond | acknowledge | ask | defer | silent
missing_evidence: scoped questions with reasons
communication_preference: speak | silent | defer, with reason
evidence_refs
```

These are conceptual fields; implementation may adapt naming to established student schemas. The student must actually support the routing information: inspect its existing output before mapping. Do not pretend a keyword heuristic is an evaluated student capability. Any extra model/training requirement must be reported explicitly.

Mind results include conclusion, uncertainty, evidence references, unresolved questions, suggested communication and relevant dependency versions. A thought result is not authorization to speak or execute a tool.

## Lifecycle A: user message

1. Persist input and publish its event transactionally or through a recoverable outbox. Advance that conversation's revision; cancel/revalidate conflicting older work.
2. Assemble a stable scoped view from recent conversation and existing usable memory cache. No required external recall.
3. Cognition interprets the input. Determine missing evidence independently from thinking requirements.
4. Jev evaluates whether deliberate thinking is needed, using interpretation and available context. Explicit requests to think carefully can require mind deterministically. Jev is not automatically called for every subsequent branch; a safe trivial-path bypass must be explicit and tested.
5. Coordinator routes:

| Missing evidence? | Mind needed? | Route |
|---|---|---|
| No | No | Cognition → mouth |
| Yes | No | Recall → mouth with interpretation and evidence |
| No | Yes | Mind → cognition on result → mouth/silence |
| Yes | Yes | Recall → mind with required evidence → cognition on result → mouth/silence |

6. For pending work, cognition's communication preference allows mouth to acknowledge or remain silent. Record acknowledgement per request; retries cannot repeat it. If the user is waiting for an answer, silence is a pending state, not successful completion. Failures/timeouts need a terminal outcome and appropriate user-facing handling.
7. Recalled evidence can invalidate the initial route (for example, contradiction requiring thought). Re-evaluate only for a recorded reason; bound repeated routing cycles. Mind can request additional specific evidence. Do not let “mind → cognition” automatically restart the original request indefinitely.
8. Mouth uses the authorized context to respond and may delegate its existing tools. Reserve output before beginning delivery. Record partial, cancelled and completed delivery distinctly.
9. Observation records actual delivery/action outcomes. Memory receives eligible events and performs updates/consolidation asynchronously under its own policy. Generated-but-undelivered words must not be recorded as delivered speech.

Fast path means no required recall or mind call; it does not mean skipping cognition. Background retrieval may continue only when useful; mouth waits if its answer depends on that evidence. An unavailable retrieval is not equivalent to “no matching memory.”

## Lifecycle B: autonomous activity

Goal/deadline/environment event → shared view and observation → cheap eligibility/urgency checks → mind if needed → cognition evaluates current significance/personality → mouth speaks, asks, defers or remains silent.

A completed ongoing thought enters directly at thought-result cognition. Routine reflections can be deferred; goal-blocking questions and urgent events have explicit priority. Personality guides initiative and wording together with user preferences, urgency and current conversation state.

Migrate quick thoughts, hard questions and planner speech through the same output authorization boundary. Do not remove existing delivery guards before the replacement handles their cases. Independent Minecraft execution continues within its existing authorized plan; this refactor must not make each movement wait for an LLM or shared tick.

## Scheduling, consistency and interruptions

- Tick/event handling runs cheap eligibility checks. New user input, cancellation and urgent events can wake processing immediately.
- Each component declares triggers, relevant input versions, minimum cadence, timeout and concurrency scope. Unchanged input skips expensive work unless a documented reflection/deadline policy is due.
- Snapshot revision identifies a view, not a globally frozen world. Domain updates retain their source versions; independently assembled projections must expose freshness rather than claim transactional consistency.
- Validate dependency versions, scope, lease and cancellation before accepting results and before dispatching effects. An unrelated mood revision must not invalidate a valid memory lookup automatically.
- A new user message supersedes conflicting reply work. Useful older thoughts can be retained, but must pass current-context cognition before future speech. Never attach them to a new message automatically.
- Use durable scoped leases/output reservations where multiple processes can race. In-process locks alone are insufficient.
- Assume at-least-once jobs; deduplicate effects and record receipts. Do not claim exactly-once external tool execution. Follow existing tool authorization rules.
- Preserve voice interruption fencing, user-speaking checks, urgent policies, and output channel targeting. Ordinary replies and background speech share arbitration.
- Bound histories, caches, queues and results. Coalesce replaceable sensor changes; preserve user input, cancellation and execution receipts.
- Observation recovery/maintenance must operate independently of mind enablement; coordinate any memory-facing scheduler changes with memory work.

## Failure behavior

Do not silently bypass required cognition or required evidence and report normal success. Define a visible degraded outcome: retry within a bounded budget, or provide a constrained acknowledgement/failure response using existing application error handling. Optional projections can be unavailable without failing a turn. Expired mind results are retained as private outcomes if useful, never delivered blindly.

Use existing durable infrastructure where practical. Exact state persistence layout, queue choice and cadence are implementation decisions; the implementer must document how they satisfy restart, scope and cancellation guarantees before wiring execution.

## Migration sequence

1. Revalidate current code and effective flags; memory work may have changed interfaces since inspection. Preserve unrelated edits.
2. Add typed contracts and adapters over current stores. Document owners and version sources; do not introduce an unused coordinator and call it complete.
3. Implement and verify user message → scoped shared view → cognition → mouth using available context. Preserve tools/streaming; support explicit degraded cognition behavior. Keep recall/mind branches out of this first test slice rather than silently answering questions that require them.
4. Wire conditional recall and Jev/mind requests/results, acknowledgement/silence, and bounded rerouting.
5. Wire autonomous thought-result cognition and common speech arbitration; remove direct mind/planner delivery bypasses only after equivalent safeguards pass.
6. Complete interruption, restart, duplicate-delivery and stale-result tests. Update the architecture map to match shipped behavior.

Existing student A/B behavior cannot silently defeat the new requirement that cognition interprets each message. Decide and document how routing is always supplied while any wording-conditioning experiment remains isolated. Do not activate legacy V0 to fill this gap.

## Acceptance scenarios

| Scenario | Expected behavior |
|---|---|
| Greeting, adequate recent context | Cognition → mouth; zero recall and mind calls |
| Question answered by valid cached memory | Use cache with source references; no fresh recall required |
| Personal-history question, absent evidence | Recall requested; distinguish no results from unavailable retrieval |
| Reasoning request with sufficient evidence | Jev/explicit instruction requires mind; optional single acknowledgement; result passes cognition |
| Reasoning request needing evidence | Required recall completes before dependent reasoning; mind can request more evidence |
| New message during thinking | Old result cannot become an answer to new input; cancellation/revalidation recorded |
| Background thought during normal reply | Shared arbitration prevents unintended competing speech; defer/drop decision traceable |
| User asks for silence | Autonomous communication respects preference and explicitly defined urgency exceptions |
| Goal-blocking question | Mind result → cognition → targeted mouth question; no direct mind delivery |
| Duplicate job or worker restart | No duplicate acknowledgement/command; durable ownership and recoverable state |
| Unchanged tick | No student/recall/mind call without an explicit due policy |
| Memory correction after cache creation | Superseded evidence is invalidated/revalidated before dependent use |
| Mind disabled | Observation maintenance still runs; required thinking cannot be misreported as completed |
| Partial/interrupted speech | Observation/memory distinguish delivered portion/status from full generated text |
| Cognition or recall failure | Bounded, explicit degraded outcome; no fabricated interpretation/evidence |

Each trace must connect input event → state dependencies → cognition → routing reason → work/result → acceptance/refusal → actual delivery/execution receipt. Capture skip reasons and latency without dumping secrets or full private context into generic logs.

## Scope exclusions

No memory-schema redesign, future learning implementation, V0 revival, separate mouth model, migration of all tools into hands, or silent activation of mood influence. Mind-to-hands tips require a separately explicit action-authorization decision; do not mistake a tip for motor execution or silently expand its authority while fixing speech.

## Handoff completion report

The implementation agent should report the routes actually migrated, remaining bypasses/disabled sections, schema and storage decisions, tests run, behavior/latency changes and unresolved dependencies. Keep this specification distinct from a claim that any route has already been implemented.
