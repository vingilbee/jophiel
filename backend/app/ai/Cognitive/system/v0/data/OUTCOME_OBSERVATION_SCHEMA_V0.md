# Outcome Observation Schema V0.1 Draft

> **Document status — 2026-10-18:** Design proposal, not a claim that the full observation/episode/learning system exists. The implemented state input is `OutcomeObservation` in [dynamic_state/models.py](../dynamic_state/models.py). Do not equate that subset with every proposed channel event or promotion mechanism below.

Status: V0.1 design accepted; case validation is required before database or
runtime implementation. Timer values and fixed confidence tiers remain
provisional calibration parameters.

This document defines how chat, voice, tools, and runtime events become evidence
about the outcome of Columbina's conversational actions. It does not define the
value/reward function or final credit-assignment algorithm.

## 1. Separation of responsibilities

Outcome processing uses three records with different meanings:

```text
immutable observation event
    what a channel, user, runtime component, or tool directly reported
                 |
                 v
outcome episode
    which delivered realization or action plan is awaiting evaluation
                 |
                 v
derived outcome evidence
    what observations support about one named outcome dimension
```

An observation never claims that an action succeeded. An episode never rewrites
its observations. Derived evidence may be corrected or disputed, but each
revision is appended rather than silently overwritten.

For voice, microphone activity is an observation, an ASR transcript is a
model-produced representation of audio, and `confusion = present` is a later
inference. They must retain separate provenance.

## 2. Stable observation-event envelope

`payload` is a discriminated union selected by `event_type` and
`event_version`:

```json
{
  "schema_version": 1,
  "observation_id": "obs-uuid",
  "event_type": "user.semantic_event.finalized",
  "event_version": 1,

  "conversation_id": "conversation-uuid",
  "session_id": "session-uuid",
  "cognitive_turn_id": "turn-uuid-or-null",
  "source_event_id": "message-or-audio-event-id-or-null",

  "actor": "user",
  "channel": "voice",
  "producer": {
    "component": "voice-ingestion",
    "component_version": "voice-ingestion-v1",
    "instance_id": null
  },

  "timing": {
    "client_session_clock_id": "browser-session-uuid",
    "client_monotonic_start_ms": 4812.4,
    "client_monotonic_end_ms": 6298.1,
    "client_wall_time": null,
    "server_received_at": "2026-09-18T18:03:12.415Z",
    "server_recorded_at": "2026-09-18T18:03:12.421Z",
    "clock_quality": "client_monotonic_only"
  },

  "correlation": {
    "responds_to_turn_id": "assistant-turn-uuid-or-null",
    "realization_id": "realization-uuid-or-null",
    "plan_id": "plan-uuid-or-null",
    "audio_stream_id": "audio-stream-uuid-or-null",
    "tool_call_id": null
  },

  "epistemic": {
    "kind": "observed",
    "finality": "final",
    "confidence": null
  },

  "payload": {},
  "privacy": {
    "content_class": "conversation_content",
    "raw_audio_retained": false,
    "retention_policy": "conversation-default-v1"
  },

  "supersedes_observation_id": null,
  "correction_reason": null,
  "idempotency_key": "unique-producer-key"
}
```

### 2.1 Field semantics

| Field | Rule |
| --- | --- |
| `observation_id` | Immutable, globally unique event identity |
| `event_type` | Stable namespaced key from the observation registry |
| `event_version` | Immutable payload-contract version for that type |
| `cognitive_turn_id` | Turn associated with the event, if known; nullable for session events |
| `source_event_id` | Native message, audio, tool, or runtime event identity |
| `actor` | `user`, `assistant`, `tool`, `system`, or `external` |
| `channel` | `chat`, `voice`, `tool`, `system`, or future registered channel |
| `producer` | Component that created the observation, not the actor described by it |
| `timing` | Source-relative timing plus authoritative server receipt/record times |
| `correlation` | Explicit links to the action or delivery that the event concerns |
| `epistemic.kind` | `observed`, `reported`, or `derived` |
| `epistemic.finality` | `partial`, `final`, `corrected`, or `retracted` |
| `privacy` | Retention classification; not a substitute for authorization enforcement |
| `supersedes_observation_id` | Previous event corrected by this append-only event |
| `idempotency_key` | Prevents producer retry from duplicating an event |

### 2.2 Invariants

- Client monotonic values are comparable only inside one
  `client_session_clock_id`; they are never synchronized server timestamps.
- `server_received_at` provides durable cross-component ordering. Audio duration
  and playback offset use the client/media clock.
- A correction appends an event and references the old one. Historical input is
  not mutated.
- Inferred claims use `epistemic.kind = derived`, never `observed`.
- Missing confidence is `null`, not an invented number.
- Raw content and hashes follow privacy/retention policy. A hash is provenance,
  not permission to retain the source media.

## 3. Initial observation registry

| Event type | Producer | Semantic role |
| --- | --- | --- |
| `user.text.submitted` | Chat frontend/API | Direct user-authored text |
| `user.voice.capture_started` | Voice frontend | Microphone/VAD boundary; not semantic evidence by itself |
| `user.voice.capture_completed` | Voice frontend | Final capture metadata and duration |
| `user.voice.asr_finalized` | ASR service | Final transcript plus ASR provenance |
| `user.semantic_event.finalized` | Ingestion service | Normalized user content accepted for cognition |
| `assistant.delivery.started` | Chat transport or voice frontend | Output became observable to the user |
| `assistant.delivery.completed` | Chat transport or voice frontend | Complete realization was delivered |
| `assistant.delivery.interrupted` | Voice frontend/runtime | Partial or unknown exposure |
| `user.feedback.explicit` | User/API | Explicit approval, rejection, correction, or rating |
| `tool.execution.finalized` | Tool runtime | Authorized external action succeeded, failed, or was cancelled |
| `conversation.topic_changed` | Cognitive/runtime detector | Derived boundary proposal, not a raw user fact |
| `conversation.session_closed` | Session runtime | Interaction ended |
| `outcome.window_elapsed` | Scheduler | Waiting interval elapsed; not proof of an outcome |

Raw PCM chunks, generated-token chunks, and partial ASR text may remain transient
transport data. They should not each become durable cognitive observations.

## 4. Chat semantic payload

```json
{
  "message_id": "message-uuid",
  "text": "That still doesn't answer my question.",
  "content_format": "plain_text",
  "submission_kind": "user_authored",
  "reply_to_message_id": "assistant-message-uuid-or-null",
  "attachments": [],
  "normalization_version": "text-normalization-v1"
}
```

The submitted message may directly produce `user.semantic_event.finalized`.
Normalization must preserve a reference to the source observation.

## 5. Voice capture and ASR provenance

The frontend owns microphone and VAD timing because it observes capture
directly. The capture-completed payload is:

```json
{
  "capture_id": "capture-uuid",
  "endpointing_source": "frontend_vad",
  "vad_policy_version": "vad-v1",
  "duration_ms": 1485.7,
  "sample_rate_hz": 16000,
  "channels": 1,
  "sample_width_bits": 16,
  "encoded_format": "wav",
  "byte_length": 47600,
  "audio_sha256": "optional-hash",
  "termination": "speech_end"
}
```

Allowed initial `termination` values are `speech_end`, `manual_stop`,
`barge_in`, `disconnect`, `timeout`, and `capture_error`.

The final ASR payload preserves how the text was produced:

```json
{
  "asr_record_id": "asr-uuid",
  "capture_observation_id": "obs-capture-completed",
  "provider": "openai",
  "model": "whisper-1",
  "request_configuration_version": "asr-openai-v1",
  "language_requested": "en",
  "language_detected": null,
  "response_format": "json",
  "transcript_text": "Could you explain that again?",
  "transcript_finality": "final",
  "segments": [],
  "words": [],
  "alternatives": [],
  "confidence": null,
  "provider_request_id": null,
  "request_started_at": "2026-09-18T18:03:11.700Z",
  "request_completed_at": "2026-09-18T18:03:12.410Z",
  "latency_ms": 710,
  "normalization_version": "transcript-normalization-v1",
  "status": "succeeded"
}
```

Empty `segments`, `words`, or `alternatives` mean the provider did not return
them. They do not imply agreement. `confidence` stays `null` unless the selected
model and response provide a defensible basis for it. The normalized semantic
event refers to the ASR observation instead of loosely copying its provenance.

ASR failure creates a final ASR observation with a typed failure status and no
semantic event. Failed or partial transcript text must not silently enter normal
perception as a final user statement.

## 6. Assistant delivery and voice exposure

Backend TTS-stream start means bytes began leaving the server. It does not prove
that the user heard them. Voice exposure comes from the frontend player and its
media clock:

```json
{
  "delivery_id": "delivery-uuid",
  "audio_stream_id": "audio-stream-uuid",
  "realization_id": "realization-uuid",
  "delivery_scope": "realization",
  "sample_rate_hz": 24000,
  "total_audio_ms": 4820,
  "played_audio_ms": 1930,
  "exposure_ratio": 0.4004,
  "completion": "interrupted",
  "interruption_reason": "user_barge_in",
  "interrupting_observation_id": "obs-user-capture-start",
  "move_exposures": []
}
```

Initial completion values are `not_started`, `partial`, `complete`, `cancelled`,
and `unknown`.

V0 uses realization-level exposure because the current audio protocol does not
map plan moves to audio sample ranges. An interrupted realization therefore is
not evidence that every planned move was heard. Move-level credit remains
unknown until realization/TTS supplies stable `move_id` ranges:

```json
{
  "move_id": "move-1",
  "audio_start_ms": 0,
  "audio_end_ms": 1275,
  "played_ms": 1275,
  "exposure": "complete"
}
```

Chat transport may report complete delivery, but this does not prove that the
user read the message.

## 7. Outcome-episode schema

An episode opens when Columbina commits a realization for delivery. It is an
evaluation container, not an outcome conclusion:

```json
{
  "schema_version": 1,
  "episode_id": "outcome-episode-uuid",
  "conversation_id": "conversation-uuid",
  "subject": {
    "cognitive_turn_id": "assistant-turn-uuid",
    "decision_trace_id": "decision-trace-uuid",
    "plan_id": "plan-uuid",
    "realization_id": "realization-uuid",
    "move_ids": ["move-1", "move-2"]
  },
  "channel": "voice",
  "status": "waiting_for_evidence",
  "opened_at": "2026-09-18T18:03:05.000Z",
  "settle_after": null,
  "observation_window": {
    "policy_version": "outcome-window-v1",
    "soft_deadline_at": "2026-09-18T18:03:35.000Z",
    "hard_close_at": null
  },
  "delivery_state": "partial",
  "delivery_observation_ids": ["obs-delivery-start", "obs-delivery-interrupt"],
  "response_observation_ids": ["obs-asr-final", "obs-semantic-final"],
  "evidence_ids": [],
  "revision": 3,
  "last_observation_at": "2026-09-18T18:03:12.421Z",
  "reflection_status": "not_eligible",
  "reflected_revision": null
}
```

Episode lifecycle:

```text
waiting_for_delivery
    -> waiting_for_evidence
    -> settling
    -> ready_for_reflection
    -> reflected
    -> reopened -> settling ...
    -> closed
```

Delayed explicit feedback can reopen an episode. Reflection always processes a
specific `revision`; later evidence increments it rather than invalidating an
earlier run silently.

A user event links to the most plausible open episode using explicit
`responds_to_turn_id` first, then same-conversation order and bounded temporal
rules. Ambiguous links remain candidates with confidence and cannot become clean
training examples. One observation may concern several earlier actions, and an
episode may receive several observations.

## 8. Derived outcome-evidence schema

Each outcome dimension is an independently supported claim:

```json
{
  "schema_version": 1,
  "evidence_id": "outcome-evidence-uuid",
  "episode_id": "outcome-episode-uuid",
  "episode_revision": 3,
  "dimension": "confusion",
  "assessment": {
    "status": "present",
    "value_type": "scalar",
    "value": 0.78,
    "confidence": 0.72
  },
  "direction": "negative",
  "scope": {
    "level": "realization",
    "move_ids": []
  },
  "source_observation_ids": ["obs-semantic-final"],
  "source_interpretation_ids": ["interpretation-uuid"],
  "derivation": {
    "method": "outcome-rules-v0",
    "version": "outcome-rules-v0.1",
    "review_status": "unreviewed"
  },
  "attribution_candidates": [
    {"component": "realization", "confidence": 0.45},
    {"component": "interpretation", "confidence": 0.25},
    {"component": "action_selection", "confidence": 0.20}
  ],
  "supersedes_evidence_id": null,
  "created_at": "2026-09-18T18:03:12.800Z"
}
```

Allowed assessment statuses are:

- `present`
- `absent`
- `unknown`
- `disputed`
- `not_applicable`

`unknown` is the initial status for every dimension, including
`explicit_user_correction`. Lack of correction is not immediately an observed
`false`. A value is required only when the dimension and status support one.
Evidence about realization fidelity remains distinct from evidence about action
appropriateness.

The initial dimensions remain:

```text
goal_resolution
clarity
explicit_user_correction
engagement
social_alignment
unnecessary_repetition
confusion
realization_fidelity
```

This document does not yet decide how these dimensions become a scalar reward,
preference comparison, vector objective, or no learning signal.

## 9. Personality-aware waiting policy

“Waiting” covers four different mechanisms that must not share one timer:

| Mechanism | Purpose | May personality affect it? |
| --- | --- | --- |
| Voice endpointing/VAD | Decide when the user finished an utterance | No in V0; use audio evidence and versioned channel configuration |
| Evidence settling/debounce | Wait for burst messages or dependent events to arrive | No; this protects record completeness |
| Outcome observation window | Decide when an episode can close without current evidence | No; personality must not redefine what was observed |
| Autonomous initiative delay | Decide when Columbina may speak again without a new user request | Yes, within bounded interaction rules |

Core identity may therefore include a slow-changing `interaction_patience`
metric in `[0, 1]`. It is distinct from ambiguity tolerance, persistence,
curiosity, empathy, or passivity:

```text
interaction_patience:
    willingness to leave conversational space before taking another voluntary
    conversational action
```

The deterministic personality runtime mapper compiles it rather than passing the
raw number to an LLM:

```text
patience_bias = 2 * interaction_patience - 1

personality_wait_multiplier = clamp(
    1 + 0.30 * patience_bias,
    0.70,
    1.30
)

initiative_delay = clamp(
    base_channel_delay
    * personality_wait_multiplier
    * situation_multiplier,
    channel_minimum,
    channel_maximum
)
```

Provisional V0 values, chosen for evaluation rather than claimed as natural
human constants, are:

| Timer | Base | Bounds | Personality influence |
| --- | ---: | ---: | --- |
| Voice autonomous initiative | 10 seconds | 5–20 seconds | `0.70–1.30` multiplier |
| Chat autonomous initiative | 90 seconds | 30–300 seconds | `0.70–1.30` multiplier |
| Voice settling after final semantic event | 0.75 seconds | fixed | None |
| Chat burst-message settling | 1.5 seconds | fixed | None |
| Voice no-response soft close | 30 seconds | fixed | None |
| Chat no-response soft close | 15 minutes or session boundary | fixed | None |

These values belong to `timing-policy-v0.1-draft` and must be calibrated through
interaction tests. A soft close does not prevent delayed feedback from reopening
an episode.

The initial situation multipliers and inhibitors are:

| Condition | Effect |
| --- | --- |
| Explicit user “wait”, user currently speaking, unfinished capture, or pending ASR | Hard inhibit autonomous speech |
| Required consent or authorization is pending | Hard inhibit action execution; a reminder still follows its own policy |
| Tool operation is still running and no status update is due | Wait for tool lifecycle policy, not personality |
| Explicit urgency with a safe unresolved goal | Multiply initiative delay by `0.75` |
| High estimated cognitive load or user requested more time | Multiply delay by `1.25`, unless explicitly inhibited |
| No unresolved goal, question, or high-salience continuation | Do not initiate merely because the timer elapsed |

The timer makes an eligible initiative candidate available; it does not force an
utterance. Normal candidate construction, action scoring, `STOP`, salience,
safety, consent, and anti-harassment/rate limits still apply. High patience can
make silence longer, but silence never becomes evidence of satisfaction. Low
patience cannot interrupt captured speech or bypass the minimum bound.

Later relationship learning may estimate a user-specific turn-taking preference,
but it should be an independent input with its own provenance. It must not mutate
Columbina's core patience or learn from silence as though silence were approval.

## 10. Deterministic V0 outcome derivation

`outcome-rules-v0.1` derives conservative, inspectable evidence after all
required source events are final. It performs no LLM call and does not update a
policy online.

### 10.1 Shared derivation rules

Evidence priority is:

```text
explicit user correction or outcome statement
    > objective tool/runtime result tied to the goal
    > grounded structured conversational behavior
    > weak behavioral proxy
    > elapsed time or silence
```

V0 applies these rules before any dimension-specific rule:

1. Use only final semantic user events and finalized tool/delivery events.
2. Require an explicit episode link or episode-link confidence of at least
   `0.80`. Otherwise retain evidence as unassigned or review-required.
3. Require the relevant source span, cue activation, semantic frame, tool result,
   or realization-validator result. Sentiment alone is insufficient.
4. If similarly credible evidence supports opposite assessments, emit
   `disputed`; do not average the conflict away.
5. If delivery was partial, evaluate only known exposed scope. Do not punish
   unheard moves.
6. Never turn silence, session closure, politeness, “thanks,” continued use, or a
   fast response by itself into success.
7. One source may support several dimensions only when every derived claim has a
   separate valid rule. Do not cascade `confusion` automatically into failed
   goal resolution or poor social alignment.
8. Initial numerical confidence values below are fixed provenance tiers, not
   calibrated probabilities. Calibration requires reviewed data.

Provisional evidence-confidence tiers are:

| Evidence class | Confidence |
| --- | ---: |
| Explicit, unambiguous user statement with grounded episode reference | `0.95` |
| Objective finalized runtime/tool fact exactly matching the target effect | `0.90` |
| Deterministic structural comparison or validated paraphrase | `0.85` |
| Bounded interpretation-dependent conversational behavior | `0.70` |
| Weak proxy, ungrounded sentiment, or timing alone | No live conclusion; shadow only |

### 10.2 `goal_resolution`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User explicitly states that the linked goal is solved/completed, or an objective finalized tool result exactly establishes the requested terminal state |
| `present`, value `0.5` | User explicitly confirms a named subgoal while the plan trace shows other required subgoals remain |
| `absent`, value `0.0` | User explicitly says the linked goal remains unresolved, or the required tool/runtime result definitively failed without an accepted alternative |
| `disputed` | Explicit user report conflicts with an objective result, or linked responses conflict |
| `unknown` | Everything else, including silence, gratitude, topic continuation, or tool success that does not itself equal the user goal |

A repeated request is not automatically failure: it may be refinement, a new
constraint, or a request for a different representation. `absent` requires the
interpretation to link it to the same unresolved goal.

### 10.3 `clarity`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User explicitly reports understanding, or gives a validated correct paraphrase of the relevant explanation |
| `absent`, value `0.0` | User explicitly calls the response unclear, unintelligible, or asks for re-explanation because the previous explanation was not understood |
| `disputed` | User both confirms understanding and identifies an unresolved clarity failure in the same assessed scope |
| `unknown` | Generic follow-up question, disagreement, short acknowledgement, silence, or positive sentiment without understanding evidence |

A deeper follow-up question may indicate engagement while clarity remains
unknown. It is not automatically evidence of confusion.

### 10.4 `explicit_user_correction`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | The accepted `conversation.explicit_correction` cue has a grounded user-text span and the interpretation links the corrected proposition/action to this episode |
| `absent`, value `0.0` | The episode window closes after at least one fully processed, substantively linked user response and no accepted explicit-correction cue occurred in that evaluated window |
| `disputed` | Correction scope cannot be resolved between this episode and another claim/turn |
| `unknown` | No semantic response, incomplete ASR, uncertain episode link, or still-open window |

Here, `absent` means only “no explicit correction was observed in the evaluated
window.” It is not evidence that Columbina was correct.

### 10.5 `engagement`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User supplies a substantive linked answer, requested detail, voluntary elaboration, or continues working on the active goal |
| `present`, value `0.5` | User gives a minimal but semantically linked acknowledgement that advances a required turn boundary |
| `absent`, value `0.0` | User explicitly asks to stop, declines further discussion, or explicitly disengages from the active goal |
| `unknown` | Silence, disconnect, session expiration, unrelated interruption, or an empty/failed voice capture |

Engagement is descriptive, not inherently positive. A correction, complaint, or
disagreement can show strong engagement.

### 10.6 `social_alignment`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User explicitly approves the interpersonal approach, accepts offered support, or expresses episode-linked rapport/appreciation with unambiguous scope |
| `absent`, value `0.0` | User explicitly reports offense, unwanted tone, boundary violation, inappropriate humor, pressure, or interpersonal mismatch tied to the episode |
| `disputed` | Mixed explicit social feedback applies to the same realization scope |
| `unknown` | Generic politeness, raw sentiment, emojis without resolved meaning, task success, or continued conversation |

Relationship state may later affect the expected importance of social outcomes,
but it cannot change the observed assessment.

### 10.7 `unnecessary_repetition`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User explicitly complains that exposed content was already stated, or a structural validator finds the same effect/target/content unit was fully exposed in the bounded recent context with no repeat request, repair need, or changed constraint |
| `absent`, value `0.0` | Structural comparison completed and found no repeated effect/target/content unit in the evaluated realization |
| `disputed` | Content repeats, but interpretation cannot resolve whether repetition was requested or required for repair |
| `not_applicable` | No prior exposed content exists for comparison |
| `unknown` | Exposure scope, target identity, or comparison context is unavailable |

Lexical overlap alone is insufficient because necessary restatement and semantic
duplication are different. V0 structural checks should use action effects,
target references, and realization content-unit identifiers.

### 10.8 `confusion`

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | User explicitly states non-understanding/confusion tied to the realization, or requests clarification of a reference/connection that the prior realization was required to make clear |
| `absent`, value `0.0` | User explicitly reports understanding or produces a validated correct paraphrase of the relevant content |
| `disputed` | Evidence of understanding and confusion applies to the same scope without resolvable ordering |
| `unknown` | Generic question, disagreement, correction, silence, negative sentiment, ASR uncertainty, or unlinked confusion |

`clarity` and `confusion` are related but remain separate evidence records. A
response may be clear in one respect while the user remains confused about
another.

### 10.9 `realization_fidelity`

This dimension compares generated/delivered content with `RealizationSpec`; it
does not judge whether the chosen action was wise.

| Output | Deterministic V0 condition |
| --- | --- |
| `present`, value `1.0` | All required moves/content are covered; no forbidden content, force change, unsupported claim, or commitment-strength violation is detected |
| `present`, value in `(0, 1)` | The contract marks omitted content dispensable under fallback and the deterministic coverage ratio is known |
| `absent`, value `0.0` | A required semantic move is omitted, contradicted, force-changed, or forbidden content is added |
| `disputed` | Deterministic checks and the independent semantic verifier conflict |
| `unknown` | Realization trace, validator output, or delivered-text identity is unavailable |

Audio interruption changes exposure, not generation fidelity. Outcome and action
learning must use only the exposed scope even when the complete generated text
was faithful.

### 10.10 Rule execution and trace

For every evaluated dimension, the rule engine records:

```json
{
  "rule_set_version": "outcome-rules-v0.1",
  "rule_id": "clarity.explicit_non_understanding.v1",
  "matched": true,
  "required_inputs": ["semantic-event", "interpretation", "episode-link"],
  "source_refs": ["obs-semantic-final", "interpretation-uuid"],
  "missing_inputs": [],
  "result_evidence_id": "outcome-evidence-uuid"
}
```

No match normally yields `unknown`, not `absent`. The rule engine emits evidence
only; it does not change memories, personality, relationship state, or action
weights. Slow reflection and later review decide whether an evidence set is fit
for a learning record.

## 11. Worker-trigger contract

The worker trigger is not the response-capture layer. Capture producers append
observations. A fast event-driven outcome observer links them to episodes and
tests whether a new revision is ready for slow processing:

```text
capture or runtime producer
    -> append immutable observation
    -> normalize/link to open episode(s)
    -> update episode revision
    -> evaluate readiness policy
    -> enqueue idempotent reflection job
```

The fast outcome observer reacts to:

- final chat submission or final ASR-backed semantic event;
- assistant delivery completion or interruption;
- explicit feedback or correction;
- finalized tool execution;
- topic or session boundary; and
- elapsed outcome window.

It does not run semantic evaluation from raw audio chunks, VAD start alone,
partial ASR text, or token chunks. Barge-in records partial delivery immediately,
but semantic evaluation normally waits for the interrupting utterance's final
ASR, perception, and interpretation.

The readiness policy returns:

| State | Meaning |
| --- | --- |
| `waiting_for_delivery` | Exposure is not yet known |
| `waiting_for_semantic_response` | A response began but final normalized content is unavailable |
| `settling` | Evidence exists but a channel-specific debounce window remains |
| `ready_for_reflection` | Stable evidence supports slow evaluation |
| `closed_without_semantic_evidence` | Window closed; outcomes remain unknown rather than negative |

Reflection jobs use this idempotency key:

```text
episode_id:episode_revision:reflection-policy-version
```

A job reads immutable source records, writes derived evidence and learning
candidates, and marks only that revision processed. Late evidence may reopen the
episode and enqueue a new revision safely.

No-response timing follows the versioned waiting policy above. Personality may
adjust when an autonomous conversational action becomes eligible, but it does
not alter evidence settling or outcome-window closure. An elapsed timer only
changes lifecycle state; it never proves disengagement, confusion, satisfaction,
or failure.

## 12. Illustrative voice sequence

```text
assistant realization committed
  -> outcome episode opens
backend begins TTS stream
  -> transport event only
frontend begins audible playback
  -> assistant.delivery.started
user begins speaking after 1.93 seconds
  -> user.voice.capture_started
  -> assistant.delivery.interrupted (played_audio_ms = 1930)
frontend VAD closes utterance
  -> user.voice.capture_completed
ASR completes
  -> user.voice.asr_finalized
normalized text is persisted
  -> user.semantic_event.finalized
perception and interpretation complete
  -> episode revision enters settling
readiness policy fires
  -> reflection job for that exact revision
worker derives evidence
  -> outcome evidence, attribution candidates, and learning candidates
```

The barge-in alone proves interruption and partial exposure. Whether it indicates
confusion, eagerness, disagreement, or an unrelated event depends on the final
semantic response and context.

## 13. Implementation boundary and review questions

This design does not yet authorize database tables. Before implementation:

1. Validate the envelope and payloads with chat, normal voice response, barge-in,
   ASR failure, disconnect, tool use, correction, and delayed-feedback cases.
2. Validate the provisional V0 timing values and bounded personality mapping;
   confirm that patience affects initiative only.
3. Validate every deterministic outcome rule against positive, negative,
   ambiguous, conflicting, partial-exposure, and missing-evidence cases.
4. Decide ambiguous episode-link handling and manual review behavior.
5. Design credit assignment separately; do not embed it into ingestion.
6. Decide retention and privacy policy for audio hashes, raw audio, transcripts,
   client/device metadata, and timing.
7. Only then map the logical records to PostgreSQL tables and indexes.
