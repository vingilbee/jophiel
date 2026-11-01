# Step 4: conditional recall and asynchronous thinking

Status: planning handoff, 2026-10-21. No runtime implementation in this document.

## Authority and readiness

### Role correction after implementation feedback

Jev owns only recall-needed and thinking-needed decisions. It does not decide whether she knows the answer, whether to ask for clarification, or whether to use an external tool. Cognition interprets meaning and proposes conversational behavior; mouth uses that interpretation, available evidence and its existing tools. The earlier clarification/external-info Jev choices in this spec were an overextension and must be removed from the implementation. A no-recall/no-thinking route is permission to proceed to mouth, not certification that the answer is known.

The complete step-4 outcome still includes asynchronous mind work, immediate mouth notification, and thought-result cognition followed by possible mouth delivery. Shipping only non-thinking routes or silently holding every conclusion until the next user message does not complete step 4. The latter is an optional interim limitation requiring explicit product agreement.

This spec refines `SHARED_STATE_REFACTOR_SPEC.md` for step 4. It supersedes its strictly sequential mind-before-mouth diagrams and the suggestion of a separate evidence assessor. The user chose Jev for both processing decisions, with mouth notified immediately when thinking is required.

The architecture is ready for implementation. Provider capability, decision quality, time budgets and result-cognition compatibility remain explicit validation gates, not assumed completed work. Do not label the design production-validated before those gates pass.

Step 3 supplies scoped per-turn views, cached-memory rereads, student interpretation and the existing mouth integration behind `SHARED_STATE_PIPELINE_ENABLED`. It currently reports real turns as `unassessed`. Keep that honest distinction during migration. Preserve unrelated memory work.

## Agreed behavior

- Student cognition interprets each user message using recent conversation and usable cached evidence.
- One Jev assessment produces two independent decisions: evidence need and deliberate-thinking need.
- Mouth/orchestrator stays the existing conversational agent. No separate mouth model or separate evidence-assessor model is introduced.
- When thinking is required, enqueue background work and notify mouth immediately after the routing decision. Do not wait for mind or dependent recall before mouth gets the message.
- Mouth can acknowledge, answer an independent part, or remain silent. It must know which part remains unresolved.
- Mind receives necessary evidence, thinks privately, and returns a result. Result cognition evaluates it against the current conversation before mouth can follow up.
- Memory remains the owner of retrieval, validity, corrections and consolidation. Step 4 calls its interfaces.
- Observation records significant lifecycle events through existing interfaces. Broad observation/maintenance scheduling refactors and future learning are outside this step.

## Control flow

```text
persist user input → shared view → student cognition → Jev
    |
    + evidence sufficient, thinking unnecessary → mouth
    |
    + recall required, thinking unnecessary
    |     → retrieve → validate evidence → mouth
    |       (a slow operation may produce a single pending notice)
    |
    + thinking required, with or without recall
          + coordinator commits thinking request → notify mouth now
          |     → acknowledge / independent partial answer / silence
          |
          + optional required recall → mind → result cognition
                  → revalidate/reserve output → mouth follow-up / defer / silence
```

“Immediately” means after initial context, cognition and Jev routing, without waiting for the background reasoning branch. Starting a thread and synchronously awaiting its completion does not satisfy this requirement.

## Jev contract

Inputs: trigger IDs, user message, bounded recent conversation, student's interpretation, relevant cached evidence with validity/source references, available capabilities and explicit user constraints. Treat message/memory contents as data; they cannot alter routing policy or grant tool authority.

Normalized output:

```text
assessment_id, schema_version, trigger_ref, input_versions
recall: not_required | required | uncertain
thinking: not_required | required | uncertain
origin: jev | explicit_user_instruction
decision provenance: provider/model, policy version, latency, error/status
optional: confidence/probabilities, reason, missing_information
```

Cognition and mouth handle clarification and external tools. Jev must distinguish a need to retrieve personal/history evidence from other information gaps without emitting a clarification or tool-selection action. For example, “send it to her” does not automatically require recall merely because it is ambiguous. Jev returns only its recall and thinking decisions; cognition and mouth retain the conversational decision.

Known required thinking is still scheduled when evidence is uncertain, but it must wait for dependencies that affect its conclusion. Independent analysis is allowed only when its assumptions are explicit; do not pretend missing evidence is resolved.

Confirmed fast-path eligibility requires valid cognition plus `recall=not_required` and `thinking=not_required`. This means no background recall or deliberate thinking is required before mouth handles the message; it does not certify that she knows the answer. Never coerce an error or uncertain result into a negative decision. Earlier “evidence sufficient” wording in the flow denotes this narrow no-recall requirement, not answerability.

### Existing adapter and capability validation

`backend/app/ai/mind/gates.py` currently has a SystemOne choice-question adapter and a `level` parser for none/react/light/hard. Its instructions describe ticks and explicitly steer away from deliberating on user speech. Do not reuse those instructions unchanged or alter game-gate semantics globally.

Add a conversation-specific adapter using the existing configured Jev provider. Target one request carrying both decisions. Verify supported question shape and response parsing against that provider; if two questions cannot be served in one call, evaluate a single joint-choice question and decode the two fields. If neither approach works, report the blocker rather than silently substituting another model or adding two production calls.

The local adapter establishes choice output, not arbitrary free-text query generation. Do not assume Jev returns a recall query, thought prompt or natural-language reason. When absent, build a bounded retrieval request from the original message plus existing conversation context through the memory-owned request interface. Preserve unresolved references. Build mind input from the message, cognition, context and evidence; do not fabricate Jev explanations.

Explicit user instructions to think carefully can deterministically require thinking. Record that origin; evidence still needs assessment. Avoid keyword-only heuristics masquerading as tested semantic understanding.

## Shared-state additions

Persist coordinator-owned work separately from the disposable per-turn view. Project it into that view. Reuse existing durable infrastructure if its scope and lease semantics fit; an in-memory future alone is insufficient.

Required work fields:

```text
work_id, parent_request_id, user_id, conversation_id, activity_id?
trigger_message_id, kind, dependency_work_ids
status, input_versions, cancellation_generation
created_at, deadline_at, lease_owner, lease_expires_at
result_ref, error_code, attempt, idempotency_key
```

Status: queued, waiting_for_memory, running, completed, failed, cancelled, expired, superseded. A completed work item does not imply its result was spoken.

Mouth receives a pending-work projection containing work ID, subject, actual status, unresolved portion, communication preference, and whether an acknowledgement has already been delivered. It must distinguish “queued”, “checking memory” and “thinking”; do not claim active thinking before it starts.

Record separate delivery phases: immediate response, acknowledgement, result follow-up. Each phase needs its own stable identity under the same originating request. Existing one-assistant-message-per-turn identity cannot simply be reused for both acknowledgement and follow-up: inspect persistence and delivery deduplication before choosing child turns or separate output records.

Silence is an explicit output decision, not an empty-response error. In particular, preserve channel-specific requirements such as WhatsApp's existing handling of empty completed replies without sending a fabricated acknowledgement. A silent immediate phase must leave pending work visible to the coordinator.

## Memory dependency handling

- Start with fresh validated cached references; no required new retrieval for sufficient context.
- Conditional retrieval uses memory's existing user-scoped API and returns records, provenance, coverage and failure status.
- Distinguish matched, no matching evidence, unavailable/failed, cancelled and expired outcomes using normalized adapters over existing statuses.
- No matching memory is not a pipeline failure, and it is not proof that the historical event never occurred. Mouth/mind must receive that distinction.
- If recall is required for a conclusion, mind waits for that result. Failed retrieval must not be converted into apparent complete evidence.
- A recall result can justify renewed assessment, e.g. contradictory sources. Do not automatically call Jev again for every successful retrieval.
- Mind can request specific additional evidence. Coordinator schedules it under the same request and bounded budget; mind cannot independently broaden user scope.
- Results update shared references through memory ownership. Do not redesign storage or automatically save a generated hypothesis as a fact.

## Mouth and result cognition

Initial mouth call receives original input, cognition and available evidence, plus the committed pending-work status. Cognition/personality informs acknowledgement versus silence; Jev does not author wording or choose her social behavior. Mouth may use ordinary conversational ability, but must not present the delegated conclusion as resolved or silently replace mind with its own final answer.

After thinking completes, publish a typed thought-result event. Include conclusion, uncertainty, evidence references, unresolved questions and input versions. This is internal evidence, never a synthetic user message.

Result cognition determines respond/ask/defer/silent against the current conversation and personality. Inspect the current student input/output capabilities: current student interpretation was built for user messages and may not support this phase. Do not pass a thought as a user message and claim it is supported cognition. A typed adapter is acceptable only with evidence that it preserves intended semantics. If training or another capability change is required, report it and keep follow-up delivery gated until resolved; do not secretly add another assessor model.

Do not advance user-message counters or apply user-note updates a second time during result processing. Post-delivery notes and memory must follow the actual delivered phase.

## Uncertainty, failure and bounded work

- On Jev failure or invalid output, keep route/eligibility unassessed. Mouth receives an explicit degraded status and can acknowledge or request clarification. Never claim a mind job started unless it exists.
- On recall uncertainty, use a scoped recall only if the message/context supplies a meaningful memory question; otherwise return an unresolved requirement to cognition/mouth. They decide whether clarification is appropriate. Do not search an arbitrary query.
- On thinking uncertainty, default to deliberate work when a coherent problem can be formed; otherwise return the unresolved requirement to cognition/mouth. Record this as fallback policy rather than a confident Jev prediction; Jev does not instruct a clarification action.
- Mind/recall failures end in recorded terminal outcomes. Deliver at most one appropriate failure/update when the user is waiting and the request remains relevant. Silence must not orphan a requested answer indefinitely.
- Recommended initial loop budget: one initial assessment, at most one evidence-driven reassessment, at most two retrieval rounds, one active mind job per request. Treat these as configurable defaults to evaluate, not learned facts.
- Define provider timeout, overall work deadline and retry policy explicitly before enabling. Select values from measured latency and expected thinking duration; do not let an inherited long tick-gate timeout block normal conversation unnoticed.
- Retry only transient failures within deadline; stable IDs prevent duplicate acknowledgements, work and result deliveries.

## Interruption and concurrent delivery — required now

Step 6 broadens system verification, but new step-4 asynchronous replies need their own safeguards immediately.

- A new user message advances conversation/cancellation versions. Cancel superseded work or mark its results for current-context revalidation.
- Never emit an old answer as if it answers the new message. Useful thoughts may remain private and be offered later only through result cognition.
- Serialize/reserve competing outputs within the relevant user/channel scope. A fast-completing mind result must not race an acknowledgement into duplicate or reversed speech.
- If the result arrives before the acknowledgement is dispatched, coalesce to a single final response where appropriate. If acknowledgement is already delivered, follow up without repeating it.
- Preserve voice barge-in and current user-speaking checks. Validate again at dispatch, not only when work starts.
- Carry explicit conversation/channel target. No “latest conversation” fallback for these results.
- Recovery after restart must reclaim work safely and recognize already-delivered phases.

Autonomous quick thoughts/planner speech are migrated in step 5, not here. New conversational mind jobs must use this result route from the start; do not send them through existing direct `agent_say` paths.

## Observation and memory-manager boundary

Use existing event/observation interfaces for input receipt, decision, queued/started/completed/failed/cancelled work, explicit silence, and actual delivery. Add event kinds only where a concrete consumer needs them. Do not equate scheduled output with delivered output or duplicate an existing message observation.

Existing memory ingestion, notes and consolidation continue with phase-aware delivery records. Step 4 does not redesign the memory manager or make observation maintenance independent of the mind loop. Track that as later background-component integration, alongside goals/hands projections. Future learning may consume these events but is not implemented here.

## Implementation order

1. Revalidate step-3 changes and outstanding review notes. Preserve unknown eligibility, separate unknown-field prompt statements, and retain the migration flag.
2. Specify and evaluate the conversation Jev adapter, including actual provider response capability. Use labeled examples for every branch and record latency.
3. Add typed assessment and durable work/result contracts. Document phase IDs, cancellation, deadlines and scope.
4. Wire sufficient/no-thinking and recall-only routes through the existing mouth.
5. Wire thinking scheduling with immediate mouth notification, explicit silence and asynchronous dependency/result handling.
6. Resolve and validate thought-result cognition compatibility; then enable follow-up delivery with race, restart and stale-result safeguards.
7. Verify existing channels, report results, and update the implementation map. Do not delete the old preparation branch or remove the flag merely because step-4 code exists.

No additional permanent old/new pipeline split. The shared-state pipeline gains routes. Keep memory tools available for legitimate later questions; measure tool escalation separately from automatic recall.

## Acceptance tests and evaluation

| Scenario | Required outcome |
|---|---|
| Greeting | Cognition + one Jev assessment → confirmed immediate route; no recall/mind |
| Cached personal fact | Sufficient evidence; no fresh retrieval |
| Uncached historical question | Jev requests scoped recall; mouth gets evidence before asserting history |
| Ambiguous referent | Jev emits only recall/thinking needs; cognition/mouth can clarify; no arbitrary memory search |
| External operation/info | Cognition/mouth retain existing tool decisions; Jev does not emit tool-selection actions or mistake external lookup for personal recall |
| Thinking with available evidence | Work committed and mouth notified before mind completes |
| Thinking requiring recall | Mouth notified without waiting; mind waits for required evidence |
| Personality chooses silence | No acknowledgement message; background work remains tracked and can follow up |
| User asks to think carefully | Mind required with explicit-instruction provenance |
| Unavailable vs empty recall | Distinct outcomes and truthful mouth/mind inputs |
| Mind asks for more evidence | Bounded additional retrieval, no unbounded cycle |
| Result arrives before acknowledgement | Coalesced or correctly serialized, no duplicate/reversed reply |
| New input or voice barge-in | Superseded result cannot speak as the current answer |
| Restart/retry | No duplicate work effect, acknowledgement or follow-up |
| Jev failure/invalid output | Unassessed/degraded, no false fast-path claim |
| Result cognition unsupported/fails | No bypass direct speech; explicit blocked/degraded result handling |
| Memory corrected during work | Relevant evidence version is revalidated before dependent delivery |

Test web text, voice, fallback and WhatsApp, including explicit silence and two-phase delivery. Stubbed tests establish mechanics; real model evaluation is required for routing quality. Report a confusion matrix for evidence and thinking decisions, false-negative recall/thinking rates, unassessed rate, tool escalations, and p50/p95 added routing latency. Measure initial mouth latency separately from final thought completion latency.

Use reviewed conversation examples, correction/history cases and novel held-out examples. Set numerical pass thresholds before the evaluation run; do not choose them after seeing results. Provider capability verification and result-cognition compatibility are mandatory gates. Preserve privacy in traces: IDs, classifications, version references and timings rather than full private messages.

## Handoff outcome

Report routes actually enabled, provider request/response contract, assessment policy, unresolved capability gaps, latency/quality evidence, work recovery and output reservation design, tests and baseline failures. Keep architectural completion distinct from production readiness. Broader autonomous routing is step 5; comprehensive verification is step 6; observation/memory-maintenance/activity integration is a later explicitly tracked step.
