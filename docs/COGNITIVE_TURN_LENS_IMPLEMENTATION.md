# Cognitive Turn Lens — End-to-End Implementation Brief

> **Retired 2026-10-22, never built.** This brief exposes the Cognitive V0 live-shadow trace, which the legacy cleanup (`LEGACY_CLEANUP_2026-10-22.md`) removed along with every file it lists below. Kept only as history.

Status: ready for implementation planning. This brief exposes the existing
canonical cognitive live-shadow trace for local evaluation. It does not grant
the cognitive pipeline authority over the live response.

Visual target:
[`design-references/cognitive-ui-concepts/01-turn-lens.png`](../design-references/cognitive-ui-concepts/01-turn-lens.png)

## 1. Objective

Build a protected `/cognitive` workspace where one real chat turn can be
viewed beside its canonical cognitive run:

```text
user message
    ├── existing live chat runtime -> streamed Live agent reply
    └── existing cognitive worker  -> immutable observational run
                                      perception
                                      interpretation
                                      goals
                                      appraisal
                                      dynamic state
                                      action selection
```

The page must make the authority boundary obvious:

- the live agent produces the user-visible reply;
- the cognitive run is observational in the current release;
- the shadow action plan is not an alternative assistant response;
- no cognitive output may enter the live prompt, agent tools, reply stream,
  stored assistant message, or chat-job result.

The public UI and API vocabulary should use **cognitive run**, not **shadow
trace**, so the contract can later represent `shadow`, `advisory`, `live`, and
`replay` execution modes. The first provider is the existing durable shadow
implementation.

## 2. Existing system boundaries to preserve

Read these before implementation:

1. `backend/app/ai/Cognitive/system/v0/README.md`
2. `backend/app/ai/Cognitive/system/v0/DURABLE_LIVE_SHADOW_TRACE_V0_IMPLEMENTATION.md`
3. `backend/app/ai/Cognitive/system/v0/live_shadow/models.py`
4. `backend/app/ai/Cognitive/system/v0/cognitive_pipeline_v0/models.py`
5. `backend/app/services/cognitive_shadow.py`
6. `backend/app/workers/cognitive_shadow.py`
7. `backend/app/api/routes/chat.py`
8. `backend/app/services/chat.py`
9. `frontend/src/pages/ChatPage.tsx`
10. `frontend/src/hooks/useChatSocket.ts`
11. `frontend/src/components/CognitiveInspector.tsx`
12. `frontend/src/types/index.ts`

Preserve these invariants:

- `cognitive_shadow_jobs.capture_payload` remains immutable.
- `cognitive_shadow_traces` remains immutable except for the existing
  predecessor foreign-key clearing behavior.
- The observability API is read-only with respect to capture and trace rows.
- The cognitive worker remains separate from chat generation.
- The live chat WebSocket does not publish cognitive stage events.
- The existing `/api/cognitive/inspect` endpoint remains the legacy synthetic
  inspector until it is explicitly deprecated; it must not be presented as a
  canonical run.
- The frontend never promotes a run from `shadow` to `advisory` or `live`.
  Runtime authority is server-owned configuration.

## 3. Product surface

### 3.1 Route

Add a protected frontend route:

```text
/cognitive
```

Do not add a second chat WebSocket. The page reuses `/api/chat/ws` and the
existing conversation/message persistence.

Add “Cognitive” to the authenticated navigation. Keep it visually separate
from the normal “Chat” entry so users understand that it is an evaluation
workspace.

### 3.2 Desktop layout

Follow the Turn Lens visual target:

- Existing slim Columbina sidebar.
- Center conversation area, approximately 60–64% of the remaining width.
- Fixed right inspector, approximately 36–40% of the remaining width.
- Conversation stays visually primary.
- Inspector scrolls independently.
- Composer stays under the conversation, not under the inspector.

The inspector header must always show:

```text
Shadow mode · does not control the reply
```

When future modes exist, derive this notice from `execution_mode` and
`authority`; do not hardcode “shadow” throughout the component tree.

### 3.3 Responsive behavior

- At widths below the desktop breakpoint, switch from split view to two tabs:
  `Conversation` and `Cognition`.
- Selecting a traced message on mobile should reveal a small “View cognition”
  action on that turn.
- The composer remains available only in the Conversation tab.
- Preserve keyboard focus when switching tabs.
- Do not compress stage details into unreadable multi-column grids.

### 3.4 Turn selection

Every user message already carries a `turn_id`. A user turn and its following
assistant message should behave as one selectable turn.

- The newest submitted turn is selected automatically.
- Clicking either message in a turn selects that `turn_id`.
- Reloading a conversation selects its most recent user turn.
- Selection changes only the inspector; it must not mutate the conversation.
- Show a restrained run-state marker beside each user turn:
  `not captured`, `pending`, `processing`, `completed`, `skipped`, or `failed`.

## 4. Backend architecture

### 4.1 New modules

Add:

```text
backend/app/api/routes/cognitive_runs.py
backend/app/schemas/cognitive_runs.py
backend/app/services/cognitive_runs.py
backend/tests/test_cognitive_runs_api.py
backend/tests/test_cognitive_runs_projection.py
```

Mount the router from `backend/app/main.py` under `/api`.

Do not put query or projection logic in the route handler. The route owns HTTP,
authentication, and status mapping. The service owns authorization-aware
lookup and projection. Pydantic schemas own the public response contract.

### 4.2 Feature flag

Add a server-owned flag:

```env
COGNITIVE_OBSERVABILITY_ENABLED=false
```

Properties:

- Independent from `COGNITIVE_LIVE_SHADOW_ENABLED`.
- Enabling observation does not enable capture.
- Enabling capture does not automatically expose traces over HTTP.
- Default remains `false` in code and `.env.example`.
- The local developer profile may enable both explicitly.

When observability is disabled, return `404`, not an implementation-detail
error that confirms the surface exists.

### 4.3 Public API

Implement:

```text
GET /api/cognitive/status
GET /api/cognitive/conversations/{conversation_id}/runs
GET /api/cognitive/runs/{turn_id}
```

All endpoints require the existing authenticated session.

#### `GET /api/cognitive/status`

Return only client-safe capability information:

```json
{
  "observability_enabled": true,
  "capture_enabled": true,
  "execution_mode": "shadow",
  "authority": "observational",
  "provider": "durable_live_shadow_v0"
}
```

Do not return secrets, queue names, internal paths, worker lease configuration,
or environment values. It is acceptable to expose that capture is disabled so
the UI can explain why new turns are not producing runs.

#### `GET /api/cognitive/conversations/{conversation_id}/runs`

Return lightweight run summaries ordered by source-turn order. This endpoint
prevents one request per historical message.

```json
{
  "conversation_id": "uuid",
  "runs": [
    {
      "turn_id": "uuid",
      "state": "completed",
      "execution_mode": "shadow",
      "authority": "observational",
      "trace_id": "uuid",
      "captured_at": "ISO-8601",
      "completed_at": "ISO-8601",
      "total_ms": 14.8,
      "segment_id": "uuid",
      "segment_reset_reason": null
    }
  ]
}
```

The service must verify that the conversation belongs to the current user
before querying or returning runs.

#### `GET /api/cognitive/runs/{turn_id}`

Return one stable `CognitiveRunOut` envelope. First verify that the turn belongs
to a conversation owned by the current user. Only then query the cognitive job
and trace.

For an owned turn with no job, return a successful envelope with
`state="not_captured"`; do not use a global trace lookup to decide ownership.

```json
{
  "schema_version": "cognitive-run-api-v0.1",
  "turn_id": "uuid",
  "conversation_id": "uuid",
  "focus_message_id": "uuid",
  "execution_mode": "shadow",
  "authority": "observational",
  "provider": "durable_live_shadow_v0",
  "state": "completed",
  "captured_at": "ISO-8601",
  "completed_at": "ISO-8601",
  "lineage": {
    "segment_id": "uuid",
    "predecessor_trace_id": "uuid-or-null",
    "continuous": true,
    "reset_reason": null,
    "missing_predecessor_refs": []
  },
  "stages": {
    "perception": {},
    "interpretation": {},
    "goals": {},
    "appraisal": {},
    "dynamic_state": {},
    "action_selection": {}
  },
  "timings": {},
  "versions": {},
  "terminal": {
    "skip_reason": null,
    "failure_code": null,
    "failure_message": null
  }
}
```

Allowed run states:

```text
not_captured
pending
processing
completed
skipped
failed
```

### 4.4 Generic run semantics

Use closed public enums:

```text
execution_mode: shadow | advisory | live | replay
authority: observational | recommendation | response_control
```

Current mapping is always:

```text
execution_mode = shadow
authority = observational
```

Do not infer authority from whether a trace completed. Authority describes
what the runtime was permitted to control, not whether it succeeded.

### 4.5 Projection boundary

Never return the ORM JSON columns directly. Parse persisted payloads through
their canonical Pydantic contracts first, then map them into a deliberately
smaller API schema.

Input sources:

```text
CognitiveShadowJob.capture_payload
CognitiveShadowTrace.pipeline_trace_payload
CognitiveShadowTrace.failure_payload
CognitiveShadowTrace.timings
CognitiveShadowTrace.versions
```

Validation behavior:

- A payload that fails its canonical schema returns a safe `failed` projection
  with a stable server-side code.
- Log identifiers and stable codes only.
- Never log message text, evidence spans, personality values, raw payloads, or
  compiled requests.
- Do not silently return partially parsed canonical stages as completed.

The first API version must not expose:

- `capture_payload` as raw JSON;
- `compiled_request_payload` as raw JSON;
- OAuth tokens or scopes beyond already-projected capability keys;
- full CoreMemory source content;
- internal failure exceptions or stack traces;
- deleted or unauthorized evidence;
- hidden chain-of-thought or model reasoning.

Evidence shown in the UI must come from the system's explicit structured
evidence contracts, not private reasoning.

## 5. Stage response contracts

Keep the first version useful and compact. Preserve stable identifiers so a
later detail drawer can request or display more information without changing
the summary semantics.

### 5.1 Perception

Return:

```text
overall_uncertainty
context_truncated
missing_information[]
cues[]
```

Each cue contains:

```text
key
state
value
confidence
salience
evidence[]
missing_information[]
detector_source
lifecycle_status
```

Preserve `null` confidence for deterministic detectors. The UI must not invent
a probability when the canonical contract explicitly has no calibrated
confidence.

### 5.2 Interpretation

Return:

```text
leading_hypotheses[]
retained_alternatives[]
semantic_frames[]
unknown_inputs[]
contradictions[]
out_of_scope[]
bias_adjustments[]
evidence[]
```

Display canonical support bands such as `weak`, `moderate`, and `strong` when
that is what the contract provides. Do not convert them into made-up decimal
confidence values.

### 5.3 Goals

Return:

```text
focus_route
primary_goal
supporting_goals[]
non_focal_active_goals[]
proposals[]
transitions[]
requirement_assessments[]
conflicts[]
```

Show goal labels through a small presentation mapping keyed by canonical
definition key. Always retain the canonical key in the API response.

### 5.4 Appraisal

Return:

```text
frames[]
dominant_frame_ids[]
dimension_ranges
```

Each frame summary should contain its scenario reference, focal goal reference,
subject, and evaluated dimensions. Avoid displaying every internal field in
the default view.

### 5.5 Dynamic state

Return the current snapshot and a UI-ready delta against the previous snapshot:

```text
revision
values[]
deltas[]
attention[]
derived_controls
contributions[]
```

Compute deltas deterministically in the projection service from the canonical
previous and current state. Do not persist UI-specific deltas back into the
immutable trace.

### 5.6 Action selection

Return:

```text
selected_moves[]
candidate_count
eligible_count
rejected_candidates[]
gate_summary[]
safety_basis
stop_reason
```

The UI label is always **Shadow action plan** while authority is observational.
Do not use “reply,” “response,” or “what Columbina said” for this stage.

## 6. Frontend architecture

### 6.1 New files

Recommended structure:

```text
frontend/src/pages/CognitivePage.tsx
frontend/src/api/cognitive.ts
frontend/src/hooks/useCognitiveRun.ts
frontend/src/components/cognitive/CognitiveRunPanel.tsx
frontend/src/components/cognitive/CognitiveRunHeader.tsx
frontend/src/components/cognitive/CognitiveStageRail.tsx
frontend/src/components/cognitive/PerceptionStage.tsx
frontend/src/components/cognitive/InterpretationStage.tsx
frontend/src/components/cognitive/GoalsStage.tsx
frontend/src/components/cognitive/AppraisalStage.tsx
frontend/src/components/cognitive/DynamicStateStage.tsx
frontend/src/components/cognitive/ActionSelectionStage.tsx
frontend/src/components/cognitive/RunTerminalState.tsx
frontend/src/components/cognitive/RunTimingFooter.tsx
frontend/src/types/cognitive.ts
```

Refactor reusable chat presentation only where necessary. Prefer extracting
components from `ChatPage.tsx` over copying its message, attachment, sticker,
subagent, and composer behavior into a second implementation.

Suggested reusable boundary:

```text
ChatWorkspace
  conversationId
  messages
  selectedTurnId
  onSelectTurn
  composer
  streaming state
```

Do not destabilize normal `/chat` while extracting. Preserve its existing
voice separation, barge-in behavior, attachment uploads, nudge handling,
history bootstrap, reconnection, stickers, and subagent segments.

### 6.2 API types

Create dedicated cognitive types rather than extending the legacy
`CognitiveInspection` interfaces. The legacy inspector and canonical run have
different semantics.

Use discriminated unions for terminal and non-terminal states:

```ts
type CognitiveRun =
  | CognitiveRunNotCaptured
  | CognitiveRunPending
  | CognitiveRunProcessing
  | CognitiveRunCompleted
  | CognitiveRunSkipped
  | CognitiveRunFailed
```

Only `CognitiveRunCompleted` may expose `stages`, full timings, and versions.
This prevents the UI from assuming a trace exists during pending or skipped
states.

### 6.3 Fetching lifecycle

For a newly submitted turn:

1. Select the client-created `turn_id` immediately.
2. Start the live reply through the existing WebSocket.
3. Request `/api/cognitive/runs/{turn_id}`.
4. While state is `pending` or `processing`, poll with bounded backoff:
   `500ms`, `750ms`, `1000ms`, then `1500ms`.
5. Stop polling on `completed`, `skipped`, `failed`, component unmount, route
   change, logout, or turn-selection change.
6. After a reasonable local timeout, show “Still processing” with a manual
   refresh action. Do not label it failed unless the server does.

For loaded history:

1. Fetch conversation messages normally.
2. Fetch the conversation run-summary endpoint once.
3. Index summaries by `turn_id`.
4. Fetch full run detail only for the selected turn.

Do not poll every historical turn.

### 6.4 Inspector states

The right panel must explicitly handle:

- No turn selected.
- Observability disabled.
- Capture disabled.
- Turn not captured or not sampled.
- Pending capture.
- Processing.
- Completed.
- Skipped with typed reason.
- Failed with safe error information.
- Network error with retry.
- Unsupported API schema version.

Never display an empty white panel while waiting.

### 6.5 Stage navigation

Use the mockup's horizontal rail:

```text
Capture → Perception → Interpretation → Goals → Appraisal → State → Action
```

- The selected stage uses Crimson and a larger outlined marker.
- Completed stages use quiet blue markers.
- Skipped or unavailable stages use muted outlines, not alarming red.
- Stage buttons must be real `<button>` elements with accessible names.
- Left/right arrow keys move between stage buttons.
- The selected panel uses an appropriate tab/tabpanel relationship.

### 6.6 Visual tokens

Use the established palette:

```text
Moon White       #FFFFFF
Frost            #F8FBFF
Ice Blue         #EAF6FF
Columbina Blue   #B9DCF4
Sky              #8FC6E8
Feather Lavender #DDD8F3
Veil Pink        #F3D7E5
Crimson          #8B3158
Midnight Ink     #23314F
Muted Slate      #7183A3
```

Implementation rules:

- Space, alignment, typography, and subtle surface changes provide hierarchy.
- Use borders sparingly and keep shadows very soft.
- Use Crimson for the selected stage and primary send action only.
- Use monospace only for cue keys, identifiers, schema versions, and timing.
- Do not recreate the existing dark `CognitiveInspector` styling.
- Do not use red/green health semantics for cue presence; use labels plus
  neutral dots so state remains understandable without color.

## 7. Conversation isolation for evaluation

Recommended: add a conversation purpose so Cognitive sessions do not clutter
normal chat history.

Database field:

```text
conversations.purpose: normal | cognitive_evaluation
```

Requirements:

- Existing rows migrate to `normal`.
- `/chat` creates and lists `normal` conversations.
- `/cognitive` creates and lists `cognitive_evaluation` conversations.
- Both purposes still use the same chat runtime and cognitive capture path.
- A client cannot change the purpose of an existing conversation.
- Conversation ownership checks remain unchanged.
- WhatsApp conversations remain `normal` unless a later reviewed design says
  otherwise.

If this migration is deferred for the first slice, the Cognitive page must at
least create a fresh conversation and clearly document that history is shared.
Do not silently pretend the histories are separated.

## 8. Evaluation annotations and future metrics

Do not mutate the trace to store human judgment. Add evaluation storage only in
a later, separate migration:

```text
cognitive_run_evaluations
  id
  trace_id
  semantic_digest
  reviewer_user_id
  rubric_version
  perception_rating
  interpretation_rating
  goal_rating
  appraisal_rating
  state_rating
  action_rating
  notes
  created_at
```

This future table should reference the immutable run and the semantic digest so
ratings remain tied to exactly what was reviewed. Aggregate dashboards should
read durable traces and evaluation rows, not the current in-process
`ShadowMetrics` object.

The first Turn Lens release should reserve visual space for a future “Evaluate
run” action but should not show a nonfunctional control.

## 9. Security and privacy

- Require authentication on every endpoint.
- Scope every lookup through the current user's owned conversation or message.
- Return the same `404` for absent and unauthorized resources.
- Never accept `user_id` from the client.
- Keep observability disabled by default.
- Treat evidence snippets as sensitive conversation data.
- Preserve the configured shadow retention policy; the API does not extend
  retention.
- Conversation/account deletion must continue to cascade to runs.
- Add conservative response-size limits to projected evidence lists.
- Do not provide raw-payload download in V0.1.
- Do not expose a browser endpoint that triggers replay, resampling, purging,
  worker control, or mode promotion.

## 10. Accessibility

- Meet WCAG AA contrast for text and interactive controls.
- Do not encode cue state, run state, or selected stage by color alone.
- Maintain visible focus rings using Midnight Ink or Crimson with sufficient
  offset from pale-blue surfaces.
- Stage navigation must be keyboard-operable.
- Announce run-state transitions with a polite live region, but do not announce
  every polling request.
- Preserve a logical reading order: conversation first, cognition second.
- Use semantic headings for each stage.
- Evidence snippets must wrap; do not require horizontal scrolling for normal
  content.
- Respect reduced-motion preferences for pending indicators and stage changes.

## 11. Testing strategy

### 11.1 Backend projection tests

Cover:

- each public run state;
- a completed canonical trace for every stage;
- canonical payload parse failures;
- null deterministic cue confidence;
- skipped traces and every current skip reason;
- failed traces with safe error projection;
- continuous lineage and each reset reason;
- state delta projection;
- action gate and safety-basis projection;
- evidence-list bounds;
- schema and enum serialization.

### 11.2 Backend API tests

Cover:

- authentication required;
- observability-disabled behavior;
- owned conversation and turn access;
- cross-user conversation and turn denial;
- owned, unsampled turns return `not_captured`;
- conversation summaries are ordered and do not expose payloads;
- pending/processing/completed/skipped/failed mapping;
- account and conversation deletion behavior;
- no endpoint mutates jobs or traces;
- no endpoint starts or wakes a worker;
- response payloads contain no raw capture or compiled-request field.

Update the shadow isolation allowlist narrowly for the new observability modules.
The isolation test must continue proving that chat generation never reads
cognitive output. Do not weaken it into a broad package exemption.

Before feature work, repair the existing repeatable stop-path test fixture in
`test_cognitive_shadow_chat_isolation.py`: its mocked session currently does not
support the WebSocket history bootstrap's `list_conversations` query. Fix the
fixture, not the production isolation boundary.

### 11.3 Frontend tests

Cover:

- all inspector states;
- automatic selection of the newest turn;
- selecting an older turn;
- bounded polling and cleanup;
- no polling after a terminal state;
- one summary request per loaded conversation;
- stage keyboard navigation;
- responsive Conversation/Cognition tabs;
- live reply remains usable when cognitive API requests fail;
- normal Chat remains unchanged;
- raw JSON and private fields never render.

### 11.4 End-to-end tests

With PostgreSQL, API, normal workers, and 100% local sampling:

1. Open `/cognitive`.
2. Send a user message.
3. Confirm the live response streams normally.
4. Confirm the selected run moves through pending/processing to completed.
5. Confirm all canonical stages render.
6. Confirm the live reply is identical with observability enabled and disabled.
7. Stop a live response and verify the cognitive capture cannot prevent or
   delay the stop outcome.
8. Reload and verify the run remains associated with the correct turn.
9. Select an older turn and verify the correct trace loads.
10. Verify another user cannot access either run.
11. Delete the conversation and verify its cognitive rows disappear by the
    existing cascade.

## 12. Implementation sequence

Implement in this order:

### Phase A — Contract and read service

1. Define the public cognitive-run Pydantic schemas.
2. Implement ownership-first lookup.
3. Implement canonical trace parsing and projection.
4. Add projection unit tests.

### Phase B — Read-only API

1. Add the observability feature flag.
2. Add status, conversation-summary, and turn-detail endpoints.
3. Add authentication, privacy, and state-mapping tests.
4. Rerun all shadow isolation and persistence tests.

### Phase C — Shared chat extraction

1. Extract the minimum reusable conversation and composer components from
   `ChatPage.tsx`.
2. Keep `/chat` behavior and visual output unchanged.
3. Add regression coverage before introducing `/cognitive`.

### Phase D — Turn Lens UI

1. Add `/cognitive` and protected navigation.
2. Build the split layout.
3. Add turn selection and run-summary indexing.
4. Add bounded polling for the selected turn.
5. Build stage rail and the six canonical stage views.
6. Add responsive tabs and accessibility behavior.

### Phase E — Evaluation-session isolation

1. Add the conversation-purpose migration.
2. Thread purpose through conversation creation and listing.
3. Verify Chat and Cognitive history separation.

### Phase F — Verification and rollout

1. Run backend focused and complete suites.
2. Run frontend lint/build/tests.
3. Run PostgreSQL end-to-end tests.
4. Compare the rendered page with the selected mockup at `1440 x 1024`.
5. Test a narrow viewport.
6. Confirm retention and deletion behavior.
7. Keep the feature local-only until privacy, latency, failure-rate, and
   storage measurements have been reviewed for a broader environment.

## 13. Local activation

The local development profile requires:

```env
COGNITIVE_LIVE_SHADOW_ENABLED=true
COGNITIVE_LIVE_SHADOW_SAMPLE_RATE=1.0
COGNITIVE_LIVE_SHADOW_RETENTION_DAYS=14
COGNITIVE_LIVE_SHADOW_PERSONALITY_SOURCE=runtime_mapper
COGNITIVE_LIVE_SHADOW_RUN_IN_SHARED_WORKER=true
COGNITIVE_OBSERVABILITY_ENABLED=true
```

Then run:

```text
PostgreSQL
Alembic migrations
FastAPI application
normal worker process (including the cognitive-shadow worker)
Vite frontend
```

The UI status endpoint should distinguish:

- API available, capture disabled;
- capture enabled, worker has not processed the turn yet;
- completed, skipped, or failed run.

Do not infer worker health merely from the capture flag.

## 14. Acceptance criteria

The first Turn Lens release is complete only when:

- `/cognitive` uses the same real chat runtime as `/chat`;
- the page never describes a shadow action plan as the produced reply;
- every selected user turn has an explicit cognitive run state;
- completed traces expose all six canonical stage summaries;
- stage data is derived from validated canonical contracts;
- no raw capture or compiled-request payload is returned;
- unauthorized users cannot distinguish absent from foreign runs;
- observability failure cannot break, delay, or alter the live reply;
- normal `/chat` behavior remains unchanged;
- desktop and mobile layouts are keyboard accessible;
- the rendered desktop implementation matches the selected Turn Lens visual
  hierarchy and current Columbina palette;
- the repeatable WebSocket stop-path isolation test is repaired and passing;
- focused cognitive, API, frontend, and end-to-end tests pass.

## 15. Explicitly out of scope

- Cognitive language realization.
- Cognitive control of the live response.
- Cognitive tool execution.
- Browser controls for changing execution authority.
- Outcome evaluation or learning.
- Human-rating dashboards.
- Raw trace export.
- Production rollout approval.
- Replacing or migrating the legacy inspector in the same change.

