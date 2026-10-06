# Dialogue Working Memory V0 implementation brief

## 1. Status and purpose

This document is the authoritative implementation handoff for **Dialogue
Working Memory V0**, the short-term, conversation-scoped memory required for
coherent multi-turn cognitive processing and grounded Realization.

Status at authoring: **designed and ready for implementation; not implemented,
accepted, behaviorally calibrated, or live-response integrated**.

Current status (2026-09-27): implemented and tested for shadow V0, not yet
implementation-accepted (independent audit pending). The canonical record of
versions, coverage, test counts, known omissions, and remaining blockers is the
[README's Dialogue Working Memory V0 status](README.md#dialogue-working-memory-v0-status).

The system must let a later turn understand and safely refer to what actually
happened earlier in the same conversation. It is not long-term memory,
relationship memory, learning memory, or an LLM transcript window.

The governing rules are:

```text
ChatMessage rows are the authoritative record of conversation events.
Dialogue Working Memory is an immutable, reproducible semantic projection.
A cache is only a disposable acceleration layer.
Only content actually exposed to the user may be remembered as assistant speech.
Shadow candidates are never remembered as delivered speech.
```

V0 remains shadow-only. It may change what the shadow pipeline observes and
what its Realization stage can safely word, but it must not change the live
assistant response, tool execution, long-term memory, or learning behavior.

## 2. Why this is needed

The current live-shadow capture already contains at most 24 bounded messages,
and `CognitivePipelineRequest` already supplies at most 30 messages to
Perception and Interpretation. That is transcript context, not a durable
semantic dialogue state.

The current Realization live producer, `project_dialogue_structure`, supplies
only generic references such as “the user's contribution,” “this
conversation,” and “an unresolved element.” It deliberately supplies no
propositions. Consequently, most Action plans correctly terminate as
`skipped_missing_content`.

Dialogue Working Memory V0 must support cases such as:

- “What did I just tell you?”
- “Use the second option.”
- “Yes, continue.”
- “No, I meant Charlie, not Max.”
- “What did you say before?”
- pronouns and demonstratives such as “it,” “that,” “those,” and “they”;
- returning to an earlier topic;
- carrying an unresolved question or request into the next turn; and
- preserving what part of a cancelled response the user can actually see.

It must support those cases without sending the raw conversation transcript to
the Realization model or upgrading user statements into verified facts.

## 3. Relationship to other memory systems

Keep these systems separate:

| System | Meaning | Lifetime | V0 relationship |
| --- | --- | --- | --- |
| Chat transcript | Exact persisted user and assistant messages | Conversation lifetime | Source of truth |
| Dialogue Working Memory | Derived shared conversational state | Conversation/session scoped | Implement here |
| Dynamic State | Columbina's internal activation and control state | Across cognitive turns | Separate input; never merged |
| CoreMemory | Stable identity/personality source | Versioned durable state | Already used by the personality mapper |
| SemanticMemory | Durable generalized user facts/beliefs | Cross-conversation | Explicitly out of scope |
| EpisodicMemory | Durable remembered experiences | Cross-conversation | Explicitly out of scope |
| Relationship memory | Durable relationship state | Cross-conversation | Not yet designed; out of scope |
| LearningMemory | Reviewed training/evaluation material | Offline learning lifecycle | Must never receive V0 output |

The existing `working_memories` table is older mutable scaffolding used by the
legacy architecture. Do not make its free-form `cognitive_state`,
`active_goals`, `unresolved_questions`, or `retrieved_memories` columns the
canonical V0 contract. Do not silently change legacy agent behavior.

Implement new immutable cognitive dialogue snapshot storage. A later explicit
migration may retire or adapt the legacy table after both paths are understood.

## 4. Canonical topology

### 4.1 Start-of-turn read path

```text
persisted ChatMessage rows
        +
latest committed DialogueMemorySnapshot
        |
        v
Dialogue Context Loader
  - validates ownership and message order
  - catches up missing committed messages
  - stops before the current focus message
        |
        v
Committed DialogueMemorySnapshot
        +
current persisted user message
        |
        v
turn-local DialogueContextView
        |
        +--> Perception working-context projection
        +--> Interpretation dialogue-context projection
        +--> Goal/Action grounded references
        +--> after Action: authorized Realization content projection
```

The committed snapshot describes the conversation immediately before the
current user message. The turn-local view additionally contains the current
user message and its current-turn Interpretation. This separation prevents
future evidence from entering a shadow observation.

### 4.2 End-of-turn write path

```text
current user message
        +
actual persisted assistant outcome
        +
transport-specific exposure status
        |
        v
durable DialogueMemoryUpdateJob (same DB transaction as terminal chat state)
        |
        v
conversation-serialized updater
        |
        v
new immutable DialogueMemorySnapshot
        |
        +--> durable head pointer
        +--> optional cache publication after commit
```

Do not “inject the whole conversation” at the end of each turn. Append only
new eligible events after the predecessor snapshot's source watermark.

### 4.3 Crash-recovery path

```text
snapshot watermark < latest eligible message position
        |
        v
discard any stale cache
load missing ChatMessage events from PostgreSQL
replay the deterministic updater in canonical order
persist the same snapshot the eager worker would have produced
```

Eager end-of-turn updates provide low latency. Start-of-turn catch-up provides
correctness after a crash, lost wake-up, expired lease, or cache eviction.

## 5. Non-negotiable invariants

1. PostgreSQL conversation messages and explicit delivery/exposure records are
   the source of truth. Redis and process memory are never authoritative.
2. Every snapshot is immutable, versioned, content-addressed, and owned by one
   user and one conversation.
3. Snapshot transition is deterministic: predecessor plus the same ordered
   source events produces the same payload, id, and digest.
4. A current-turn pipeline cannot consume any message after its focus user
   message.
5. A shadow candidate is not a delivered assistant message and may never enter
   Dialogue Working Memory.
6. `accepted_shadow` means validator acceptance for review, not user exposure.
7. User statements retain `user` attribution. They are never promoted to
   trusted facts merely because they were repeated or survived several turns.
8. Delivered legacy-agent statements retain `columbina` attribution but are
   not automatically trusted factual propositions.
9. Partial assistant output remains marked partial. It cannot satisfy a
   completion, promise, tool-result, or full-answer claim.
10. Corrections supersede earlier items without deleting their provenance.
11. Missing, stale, cross-user, cross-conversation, deleted, or unsupported
    source data fails closed with a typed outcome.
12. The Realization model receives only selected authorized content items,
    never the snapshot, cache record, raw transcript, or database objects.
13. No V0 operation writes SemanticMemory, EpisodicMemory, RelationshipMemory,
    CoreMemory, or LearningMemory.
14. Conversation/account deletion removes snapshots, heads, pending update
    jobs, and cache keys.
15. Enabling Dialogue Working Memory cannot enable live shadow, Realization,
    or live response delivery by itself.
16. Existing v0.2 capture/pipeline traces remain replayable under their exact
    historical versions.

## 6. Version ownership

Use exact V0 version identifiers:

```text
DIALOGUE_MEMORY_CONTRACT_VERSION       = "dialogue-working-memory-contract-v0.1"
DIALOGUE_MEMORY_ITEM_VERSION           = "dialogue-working-memory-item-v0.1"
DIALOGUE_MESSAGE_ORDER_VERSION         = "dialogue-message-order-v0.1"
DIALOGUE_MESSAGE_ELIGIBILITY_VERSION   = "dialogue-message-eligibility-v0.1"
DIALOGUE_MEMORY_UPDATER_VERSION        = "dialogue-working-memory-updater-v0.1"
DIALOGUE_MEMORY_PROJECTION_VERSION     = "dialogue-working-memory-projection-v0.1"
DIALOGUE_MEMORY_CACHE_VERSION          = "dialogue-working-memory-cache-v0.1"
DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION = "dialogue-working-memory-digest-v0.1"
```

Adding dialogue-state fields to a captured or pipeline request changes its
semantic projection. Do not reinterpret stored v0.2 records. Introduce:

```text
cognitive-shadow-capture-v0.3
cognitive-pipeline-schema-v0.3
cognitive-pipeline-runner-v0.3
```

The old v0.2 runner/replay route must remain available for stored v0.2 traces,
or replay must return a typed `version_unavailable`; it may not silently run
v0.3 semantics over v0.2 payloads.

Dialogue-derived Realization content requires source kinds that distinguish
actual delivered dialogue from cognitive shadow output. Add under a new
content-contract version rather than overloading
`accepted_prior_realization`:

```text
delivered_user_utterance
delivered_assistant_utterance
```

This requires a reviewed `realization-content-v0.2` and corresponding request,
validator, semantic-digest, and replay compatibility decision. A user message
from an earlier turn is not accurately described as `current_user_statement`.
An existing-agent response is not an `accepted_prior_realization` because it
was not produced and accepted through the cognitive Realization protocol.

## 7. Package and ownership layout

Create:

```text
backend/app/ai/Cognitive/system/v0/contracts/dialogue_memory.py

backend/app/ai/Cognitive/system/v0/dialogue_memory_v0/
  __init__.py
  errors.py
  models.py
  ordering.py
  eligibility.py
  bootstrap.py
  updater.py
  projector.py
  content_projector.py
  digests.py
  cache_protocol.py

backend/app/services/dialogue_memory.py
backend/app/workers/dialogue_memory.py
```

Ownership:

- `contracts/dialogue_memory.py`: immutable cross-boundary contracts only.
- `ordering.py`: canonical ordering and source-position validation.
- `eligibility.py`: message visibility/exposure policy.
- `bootstrap.py`: bounded first-use reconstruction.
- `updater.py`: pure deterministic predecessor-plus-events transition.
- `projector.py`: stage-specific projections for Perception, Interpretation,
  Goal, Action, and turn-local context.
- `content_projector.py`: authorized Realization inventory production.
- `digests.py`: canonical semantic and source digests.
- `cache_protocol.py`: provider-neutral optional cache interface.
- `services/dialogue_memory.py`: PostgreSQL ownership, locking, persistence,
  deletion, job scheduling, and cache publication.
- `workers/dialogue_memory.py`: durable job claiming and retry behavior.

The pure package must not import SQLAlchemy, Redis, provider SDKs, the chatbot
agent, or a wall clock.

## 8. Source-event ordering

### 8.1 Do not use timestamps alone

`chat_messages` currently uses UUID primary keys and timestamps. Timestamp plus
UUID sorting is not a sufficient conversation ledger when two messages are
created concurrently or timestamps have equal precision.

Add a monotonically increasing `conversation_sequence` to `chat_messages`:

```text
conversation_sequence BIGINT NOT NULL
UNIQUE (conversation_id, conversation_sequence)
CHECK (conversation_sequence >= 1)
```

Every new message append must:

1. lock the owned conversation row;
2. allocate the next sequence within that conversation;
3. insert the message with that sequence; and
4. commit the message and any associated job/outbox rows atomically.

Do not calculate `max(sequence)+1` without holding the conversation lock.

### 8.2 Existing-row backfill

Backfill existing messages deterministically within each conversation using:

1. `coalesce(ChatJob.created_at, ChatMessage.created_at)`;
2. turn grouping when `turn_id` is present;
3. user before assistant within one turn;
4. `ChatMessage.created_at`;
5. `ChatMessage.id` as the final stable tie-break.

Before setting `NOT NULL`, test that every conversation has a unique,
contiguous sequence from 1 through N. Record the migration's ordering version.

Unprompted assistant messages with no `ChatJob` still receive their own
sequence through the same locked append path.

### 8.3 Source watermark

Every snapshot records:

```text
through_conversation_sequence
through_message_id
source_event_count
source_prefix_digest
```

`through_message_id` is a provenance pointer. The numeric sequence is the
incremental cursor. `source_prefix_digest` detects deletion, mutation, or
reordering of the already-consumed prefix.

## 9. Message eligibility and exposure policy

Implement a closed `DialogueExposureState` enum:

```text
durably_visible
partially_visible
not_visible
delivery_pending
delivery_failed
visibility_unknown
```

The policy must operate on persisted facts, never an assumption that model
generation equals delivery.

| Source | Condition | State | Memory behavior |
| --- | --- | --- | --- |
| User message | Persisted and owned by conversation | `durably_visible` | Include with user attribution |
| Web assistant | `completed`, non-empty, committed to history | `durably_visible` | Include as delivered assistant utterance |
| Web assistant | `partial`, non-empty | `partially_visible` | Include exact partial text and incomplete marker |
| Assistant | `cancelled`, empty | `not_visible` | Record processed event metadata; create no content item |
| Assistant | `failed`, empty | `not_visible` | Record terminal failure only; create no utterance item |
| Assistant | `failed`, non-empty and shown in durable history | `partially_visible` | Reference/quote only; never completion evidence |
| WhatsApp assistant | delivery row pending | `delivery_pending` | Do not commit utterance yet |
| WhatsApp assistant | provider accepted/sent/delivered under reviewed mapping | `durably_visible` | Include after delivery transition |
| WhatsApp assistant | terminal delivery failure | `delivery_failed` | Exclude utterance; preserve failure event |
| Shadow Realization candidate | Any shadow status | `not_visible` | Always exclude |

If the current schema cannot prove transport exposure, add an explicit durable
message-exposure record instead of inferring from loosely related fields.
Define one reviewed mapping per source channel. Unknown channels fail closed as
`visibility_unknown`.

Do not block a future turn indefinitely on an old `delivery_pending` event.
The snapshot may advance over the source sequence while recording a pending
exposure gap. If delivery later succeeds, produce a new snapshot that adds the
utterance with its original message provenance and delivery-event evidence.

## 10. Core contracts

All contracts use immutable `extra="forbid"` Pydantic models and canonical
JSON-safe values.

### 10.1 Dialogue source event

```text
DialogueSourceEvent
  event_id
  conversation_ref
  user_ref
  conversation_sequence
  message_ref
  turn_ref
  role: user | assistant
  status: completed | partial | cancelled | failed
  source_channel
  exposure_state
  content_digest
  content
  attachment_refs
  created_at
  exposure_evidence_refs
```

`content` exists only inside the trusted updater boundary. Raw content must not
be copied wholesale into the semantic snapshot, logs, metrics, cache keys, or
Realization payload.

### 10.2 Dialogue memory item

Use a discriminated union with at least:

```text
DialogueEntity
DialogueProposition
DialogueQuestion
DialogueRequest
DialogueOptionSet
DialogueTopic
DialoguePlanReference
DialogueCommitmentReference
DialogueUtteranceReference
DialogueCorrection
DialogueGap
```

Every item carries:

```text
item_id
item_version
conversation_ref
introduced_turn_ref
last_supported_turn_ref
source_message_refs
source_span_refs
speaker: user | columbina | runtime
epistemic_status
status: current | superseded | resolved | withdrawn | inaccessible
sensitivity_class
surface_anchors
represents_refs
```

Item ids are deterministic UUID5/content-addressed identities from the item
version, conversation, normalized semantic identity, and source lineage. Never
use UUID4 or wall time in the pure updater.

### 10.3 Dialogue memory snapshot

```text
DialogueMemorySnapshot
  schema_version
  snapshot_id
  user_ref
  conversation_ref
  revision
  predecessor_snapshot_ref
  segment_id
  reset_reason
  through_conversation_sequence
  through_message_id
  source_event_count
  source_prefix_digest
  active_topic_refs
  focus_entity_refs
  unresolved_reference_refs
  open_question_refs
  active_request_refs
  active_option_set_refs
  active_plan_refs
  active_commitment_refs
  current_item_refs
  items
  omitted_history
  versions
  semantic_digest
```

Arrays use deterministic ordering. Maps are canonicalized. Exact message text
is not a generic snapshot field.

### 10.4 Omitted-history record

An omission is explicit:

```text
DialogueHistoryOmission
  reason
  sequence_start
  sequence_end
  message_refs
  recoverable
```

Reasons include:

```text
outside_bootstrap_window
unsupported_historical_schema
visibility_unproven
source_deleted
projection_unavailable
historical_trace_unavailable
```

Absence is not evidence that an event never occurred.

### 10.5 Turn-local context view

```text
DialogueTurnContext
  context_id
  cognitive_turn_id
  conversation_ref
  committed_snapshot_ref
  committed_snapshot_digest
  focus_message_ref
  focus_conversation_sequence
  prior_context_projection
  current_user_event
  current_interpretation_projection
  future_boundary_sequence
  versions
```

`future_boundary_sequence` equals the focus user's sequence. Any source event
above it is future evidence and makes compilation fail.

## 11. Semantic and epistemic policy

### 11.1 User statements

A user statement becomes an attributed conversational proposition:

```text
speaker = user
epistemic_status = attributed
source_kind = delivered_user_utterance
```

It may support “you said your dog is named Max.” It does not by itself support
“your dog is named Max” as verified external truth.

### 11.2 Assistant statements

An assistant message produced by the existing agent may support:

- exact quotation of what Columbina previously said;
- reference to a prior response, option, or question;
- dialogue continuity; and
- correction of Columbina's own earlier statement.

It does not become trusted system knowledge merely because it was delivered.
Until the live cognitive path preserves original proposition provenance,
legacy assistant text is represented as an attributed utterance reference or
authorized quote, not a grounded factual proposition.

### 11.3 Corrections

Correction is append-only:

```text
old item: status = superseded
new item: status = current
correction item: cites old, new, correcting message, and evidence spans
```

Do not delete the old item from the snapshot lineage. Realization receives the
old item only when a selected correction/repair move explicitly requires it,
and the validator must prevent reassertion.

### 11.4 Resolution and withdrawal

Questions, requests, plans, and commitments have independent lifecycle state.
A later response may resolve or withdraw them only when the source evidence
supports that transition. Topic change does not automatically resolve an open
request.

### 11.5 Partial output

Partial assistant text can be remembered only as an incomplete utterance. It
cannot establish:

- that an answer was completed;
- that a tool ran;
- that a plan finished;
- that a promise was fulfilled; or
- that the user received an omitted suffix.

## 12. Bootstrap behavior

### 12.1 First use

When no cognitive snapshot exists:

1. lock the owned conversation;
2. determine the requested future boundary;
3. load the most recent bounded eligible messages at or before that boundary;
4. preserve their canonical conversation sequence;
5. project only semantics supported by deterministic rules or stored canonical
   cognitive traces;
6. represent legacy assistant messages as utterance references/quotes only;
7. record excluded older ranges in `omitted_history`;
8. create revision 1 atomically; and
9. publish the cache only after commit.

Default bootstrap limits must be configuration, bounded, and tested. Suggested
conservative defaults:

```text
COGNITIVE_DIALOGUE_MEMORY_ENABLED=false
COGNITIVE_DIALOGUE_MEMORY_BOOTSTRAP_MESSAGES=30
COGNITIVE_DIALOGUE_MEMORY_MAX_ITEMS=200
COGNITIVE_DIALOGUE_MEMORY_CACHE_ENABLED=false
COGNITIVE_DIALOGUE_MEMORY_CACHE_TTL_SECONDS=3600
```

The message limit must not be represented as semantic completeness.

### 12.2 Historical turns without cognitive traces

Do not run an LLM to invent structured memory during bootstrap. For historical
messages without compatible canonical Interpretation traces, V0 may create:

- user/assistant utterance references;
- authorized exact quotes when policy permits;
- structural topic/request/question markers supported deterministically; and
- explicit `historical_trace_unavailable` omissions.

A future reviewed backfill version may replay historical user messages through
a pinned deterministic pipeline. V0 must not perform an invisible lossy
summarization and call it ground truth.

## 13. Incremental updater

`DialogueMemoryUpdaterV0.apply(predecessor, events)` is pure. It has no model,
database, cache, clock, network, retrieval, or provider import.

Algorithm:

1. Validate snapshot and event ownership.
2. Require events to begin at `predecessor.sequence + 1` with no duplicate or
   unexplained gap.
3. Validate message id, role, turn, exposure state, and content digest.
4. Record excluded/non-visible events without producing utterance content.
5. Project eligible message structure and compatible stored cognitive
   semantics.
6. Resolve referents only when evidence is unique enough; otherwise create an
   unresolved reference.
7. Apply corrections and lifecycle transitions append-only.
8. Update active topics, open questions, requests, option sets, plans, and
   commitments.
9. Enforce item-count bounds using explicit compaction/omission policy.
10. Calculate source-prefix and semantic digests.
11. Derive the new snapshot id deterministically.
12. JSON-round-trip and require value equality.

There is no partial snapshot. A deterministic contract failure yields a typed
failure and leaves the predecessor head unchanged.

## 14. Database schema and persistence

### 14.1 Immutable snapshots

Add `cognitive_dialogue_memory_snapshots`:

```text
id UUID PRIMARY KEY
user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
revision BIGINT NOT NULL
predecessor_snapshot_id UUID NULL
segment_id UUID NOT NULL
reset_reason VARCHAR NULL
through_conversation_sequence BIGINT NOT NULL
through_message_id UUID NULL REFERENCES chat_messages(id) ON DELETE SET NULL
source_event_count BIGINT NOT NULL
source_prefix_digest CHAR(64) NOT NULL
snapshot_payload JSONB NOT NULL
semantic_digest CHAR(64) NOT NULL
versions JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Constraints/indexes:

```text
UNIQUE (conversation_id, revision)
UNIQUE (conversation_id, through_conversation_sequence, semantic_digest)
CHECK revision >= 1
CHECK through_conversation_sequence >= 0
CHECK digests are lowercase SHA-256
```

Create a trigger that makes snapshot rows immutable. Deletion by conversation
cascade remains allowed.

### 14.2 Head pointer

Add `cognitive_dialogue_memory_heads`:

```text
conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE
user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
snapshot_id UUID NOT NULL REFERENCES cognitive_dialogue_memory_snapshots(id)
revision BIGINT NOT NULL
through_conversation_sequence BIGINT NOT NULL
semantic_digest CHAR(64) NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

The head is the only mutable record. Update it with predecessor compare-and-set
inside the same transaction that inserts the immutable successor snapshot.

### 14.3 Durable update jobs

Add `cognitive_dialogue_memory_jobs` using the same lease/claim discipline as
the cognitive-shadow queue:

```text
id UUID PRIMARY KEY
user_id
conversation_id
trigger_message_id
target_conversation_sequence
status: pending | processing | completed | failed | skipped
attempts
claim_token
lease_expires_at
last_error_code
last_error_safe
created_at
processed_at
```

Require uniqueness for the trigger message/target sequence. A retry must never
create a different semantic successor.

The job row must be inserted in the same transaction that commits the terminal
assistant message or its delivery transition. Redis may wake the worker, but
PostgreSQL polling is the source of truth.

## 15. End-of-turn integration

### 15.1 Chat worker

In `app/workers/chat.py`, after the assistant outcome has been persisted and
before the transaction commits:

1. determine the channel-specific exposure state;
2. insert the Dialogue Memory update job with `commit=False`;
3. complete the ChatJob;
4. commit message, ChatJob, delivery/outbox, and dialogue-memory job atomically;
5. wake the dialogue-memory worker after commit.

Do not update memory before the assistant message transaction succeeds.

### 15.2 Web/local fallback

All response-producing paths must use the same scheduling service. Do not add
working-memory writes to only the durable worker while leaving the WebSocket
local fallback inconsistent.

### 15.3 WhatsApp and other asynchronous channels

For channels where message persistence precedes confirmed provider delivery,
the generation transaction may schedule a pending exposure event but must not
commit the assistant utterance into memory. The delivery worker schedules the
final memory update when the reviewed delivery state is reached.

### 15.4 User-only terminal turns

If no assistant utterance becomes visible, the updater still advances over the
persisted user message and terminal event. The user statement is not lost
because assistant generation failed.

## 16. Start-of-turn and live-shadow integration

The current cognitive shadow capture occurs before assistant generation and is
immutable. Preserve that property.

At capture time:

1. load/catch up the committed snapshot only through the message immediately
   before the focus user message;
2. compile a turn-local view using the focus user message;
3. copy the exact snapshot id, digest, revision, and bounded projections into
   capture v0.3;
4. record explicit omissions; and
5. never re-read a newer snapshot during job retry.

This prevents a shadow worker that runs late from seeing the actual assistant
response to the same turn. That response is future evidence for the shadow
alternative.

Capture payloads must be self-contained for replay. A replay cannot read the
current mutable head or Redis because either may have advanced.

## 17. Stage projections

### 17.1 Perception

Extend the versioned working-context projection with:

```text
active_topic_refs
focus_entity_refs
unresolved_refs
open_question_refs
source_dialogue_snapshot_ref
source_dialogue_snapshot_digest
```

Only refs and state needed for cue interpretation enter Perception. Raw prior
message text remains in the separately bounded message window already owned by
the request.

### 17.2 Interpretation

Do not disguise Dialogue Working Memory as long-term
`InterpretationMemory`. Add a typed `InterpretationDialogueContext` input with
attributed propositions, entities, referents, option sets, active questions,
and exact provenance.

Interpretation must preserve ambiguity. If “it” has two viable antecedents,
produce competing hypotheses or an unresolved argument; never pick the newest
item merely because it is convenient.

### 17.3 Goal and Action

Project open requests/questions and active plans as typed Goal evidence and
Action grounded objects/target descriptors. Every projection cites the
dialogue item and its source message evidence.

Do not convert topic continuity into an active goal without the Goal policy's
normal evidence and lifecycle rules.

### 17.4 Realization content inventory

After Action Selection, `DialogueRealizationContentProjectorV0` selects only
items referenced by selected move targets, evidence, and reference parameters.
It never exports the entire snapshot.

Mappings include:

```text
attributed dialogue proposition -> GroundedProposition(attribution=user|columbina)
open question specification     -> GroundedQuestion
unresolved/failed lookup         -> KnowledgeGap
topic/request/message reference -> DialogueReference
exact permitted prior wording   -> AuthorizedQuote
```

Every content item records:

```text
source message refs
dialogue snapshot ref and digest
dialogue item ref
attribution
epistemic status
sensitivity
authorization decision
surface anchors
```

An actual delivered assistant utterance may be quoted or referenced. It does
not become a grounded external fact. A shadow candidate is ineligible even if
its status is `accepted_shadow`.

## 18. Cache design

Caching is optional and must be implemented behind `DialogueMemoryCache`:

```python
class DialogueMemoryCache(Protocol):
    def get(self, conversation_ref: str, revision: int, digest: str) -> bytes | None: ...
    def put(self, snapshot: DialogueMemorySnapshot, ttl_seconds: int) -> None: ...
    def delete_conversation(self, conversation_ref: str) -> None: ...
```

Suggested Redis keys:

```text
cognitive:dialogue-memory:v0.1:snapshot:{conversation_id}:{revision}:{digest}
cognitive:dialogue-memory:v0.1:head:{conversation_id}
```

Rules:

- cache only the canonical snapshot payload, not ORM objects;
- verify user, conversation, revision, id, and digest after every read;
- a miss performs the PostgreSQL path with no semantic change;
- a mismatch deletes the cache entry and performs the PostgreSQL path;
- publish after database commit only;
- head-cache failure never rolls back a valid database update;
- do not log payloads or message text;
- deletion must remove both head and immutable snapshot keys; and
- tests must prove enabled/disabled/evicted cache produces identical results.

Do not add caching until the PostgreSQL-only implementation passes. V0
correctness must never depend on Redis availability.

## 19. Bounds and compaction

Bound memory by semantic relevance and explicit lifecycle, not by silently
dropping the oldest item.

Suggested hard caps:

```text
maximum snapshot items = 200
maximum active topics = 8
maximum unresolved references = 16
maximum open questions = 16
maximum active requests = 16
maximum exact quotes = 8
maximum quote characters each = 500
```

When a cap is reached:

1. remove resolved/withdrawn items from the active indexes while retaining
   their lineage in prior immutable snapshots;
2. prefer current corrections over superseded content;
3. retain items cited by active goals, requests, plans, or commitments;
4. record an omission/compaction entry; and
5. fail closed if the required current-turn referent would be dropped.

V0 must not call an LLM to summarize old dialogue. A later summarizer requires
its own contract, provenance, validation, and version.

## 20. Concurrency and idempotency

The app already serializes active ChatJobs per conversation, but Dialogue
Working Memory must defend itself independently.

- Lock the conversation/head before sequence allocation or head transition.
- Claim update jobs with `FOR UPDATE SKIP LOCKED` and leases.
- Compare-and-set the predecessor snapshot id and revision.
- If another worker already produced the exact target successor, treat the job
  as idempotently completed.
- If the head advanced beyond the target, verify the source event is included
  in its source-prefix digest before completing the stale job.
- If lineage is discontinuous, begin an explicit new segment or emit a typed
  failure; never splice silently.
- A retry reuses the captured job target and source boundary.
- Two conversations may update concurrently; two revisions of one conversation
  may not.

## 21. Failure model

Define closed phases:

```text
source_loading
source_ordering
exposure_resolution
bootstrap
transition
projection
persistence
cache
pipeline_handoff
realization_content_projection
replay
```

Stable failure/skip codes include:

```text
conversation_not_owned
source_message_missing
source_sequence_gap
source_prefix_digest_mismatch
message_order_invalid
exposure_unknown
delivery_pending
snapshot_not_found
snapshot_lineage_mismatch
snapshot_contract_invalid
predecessor_conflict
projection_version_unsupported
historical_trace_unavailable
ambiguous_reference_unresolved
required_dialogue_content_missing
future_evidence_detected
cache_digest_mismatch
cache_unavailable
persistence_conflict
```

Cache failures are recoverable and do not fail the cognitive turn when the
database path succeeds. Ownership, future-evidence, digest, or source-order
failures are hard failures for the affected shadow observation.

Do not include message text, snapshot payloads, quotes, model candidates,
provider bodies, or personal content in logs or metric labels.

## 22. Replay and deterministic evidence

Store enough in capture v0.3 to replay without PostgreSQL conversation reads:

- committed snapshot payload or exact bounded projection;
- snapshot id, revision, and semantic digest;
- focus user source event;
- turn-local projection;
- all component versions; and
- omission records.

Replay must:

1. validate stored capture and snapshot digests;
2. reconstruct the stage projections;
3. rerun the deterministic pipeline;
4. rebuild the Dialogue-derived Realization inventory;
5. recompile and revalidate stored Realization without a provider call; and
6. compare semantic digests.

Changing the current working-memory head must not alter replay of an older
turn.

## 23. Privacy, retention, and deletion

Dialogue Working Memory is derived conversation data:

- account deletion cascades every snapshot, head, job, and cache key;
- conversation deletion does the same;
- message deletion must invalidate/rebuild affected descendant snapshots or
  begin a typed reset segment;
- snapshot access is always scoped by both `user_id` and `conversation_id`;
- no cross-user cache key lookup is accepted without post-read ownership
  verification;
- do not duplicate full transcripts into snapshot JSON;
- exact quotes are bounded and provenance-linked;
- metrics contain counts/statuses only; and
- shadow captures containing projections retain the existing local-canary
  14-day policy even if the conversation itself lives longer.

The production retention decision remains separate. Do not infer that local
14-day shadow retention approves production retention of dialogue snapshots.

## 24. Required tests

### 24.1 Contract tests

- immutable, `extra="forbid"`, canonical JSON round trip;
- invalid ownership, blank ids, duplicate refs, bad digests, invalid lifecycle;
- exact version bundle and deterministic ids;
- no raw ORM/model/provider objects accepted; and
- hash-seed invariance.

### 24.2 Ordering and migration tests

- existing rows backfill to unique contiguous conversation sequences;
- same-timestamp rows use the documented tie-break;
- user precedes assistant within one turn;
- unprompted assistant messages receive a valid sequence;
- concurrent append allocation cannot duplicate a sequence;
- migration upgrade/downgrade on real PostgreSQL; and
- old v0.2 trace replay remains stable.

### 24.3 Eligibility tests

Cover every row of the policy table:

- completed web response;
- non-empty partial response;
- empty cancellation;
- visible failed response;
- invisible failed response;
- WhatsApp pending/success/failure;
- unknown channel;
- accepted/rejected/provider-failed shadow candidates; and
- user messages across supported channels.

### 24.4 Updater tests

- bootstrap equals incremental replay for the same bounded source prefix;
- duplicate event is idempotent;
- sequence gaps fail closed;
- user claims stay attributed;
- assistant claims stay attributed;
- correction supersedes without erasure;
- partial output never creates completion evidence;
- question/request resolution requires evidence;
- ambiguous pronouns remain unresolved;
- caps/compaction preserve active dependencies; and
- predecessor plus events is pure and deterministic.

### 24.5 End-of-turn persistence tests

Using real PostgreSQL:

- message, terminal ChatJob, and update job commit atomically;
- rollback writes none of them partially;
- retry does not duplicate snapshots;
- conversation serialization prevents lost updates;
- stale worker token writes nothing;
- user-only terminal turn advances correctly;
- partial and failed outcomes use exact policy;
- account/conversation deletion cascades;
- immutable snapshot trigger rejects mutation; and
- head compare-and-set rejects a wrong predecessor.

### 24.6 Cache tests

- hit, miss, eviction, corruption, unavailable Redis, and disabled cache;
- corrupt digest is discarded, never consumed;
- cache publication happens only after commit;
- deletion removes keys;
- cache keys contain no user content; and
- all cache modes produce identical semantic snapshots.

### 24.7 Pipeline multi-turn cases

Create reviewed domain-general cases including:

1. pronoun with one antecedent;
2. pronoun with two plausible antecedents;
3. “the second option” with a current option set;
4. “continue” with an active plan;
5. “continue” without an active plan;
6. user corrects a personal statement;
7. user withdraws a request;
8. return to prior topic;
9. current question refers to prior user claim;
10. user asks what they previously said;
11. user asks what Columbina previously said;
12. prior assistant output was partial;
13. prior assistant generation failed invisibly;
14. WhatsApp delivery is still pending;
15. unprompted assistant message;
16. history outside the bootstrap bound;
17. deleted source message;
18. stale cache;
19. shadow candidate differs from actual delivered response; and
20. two users with superficially identical conversations.

The shadow candidate in case 19 must never appear in the next snapshot. The
actual delivered response may appear with correct attribution.

### 24.8 Realization integration tests

- Dialogue item projects to the correct content type/source/attribution;
- only selected Action refs enter the inventory;
- prior user claim remains attributed;
- legacy assistant claim is quote/reference only;
- correction supplies old and replacement sides without reasserting old text;
- missing required item yields `skipped_missing_content`;
- no raw snapshot or transcript reaches the model payload;
- Realization candidate never writes back into working memory; and
- replay rebuilds the inventory without a provider call.

### 24.9 Live isolation and regression

- enabling working memory does not change actual chat tokens, tools, jobs, or
  long-term memory;
- updater failure cannot fail a completed live chat turn;
- no message/snapshot content appears in logs or metrics;
- focused Perception, Interpretation, Goal, Action, Pipeline, shadow,
  Realization, chat, and memory suites pass; and
- the complete backend suite passes with and without real PostgreSQL tests.

## 25. Acceptance criteria

Dialogue Working Memory V0 is implementation-accepted for shadow use only
when:

1. message order has a durable, concurrency-safe per-conversation sequence;
2. immutable snapshots and a compare-and-set head are persisted;
3. eager end-of-turn update and lazy start-of-turn recovery are equivalent;
4. eligibility is based on explicit persisted exposure facts;
5. user and assistant attribution is never upgraded;
6. partial, failed, cancelled, pending, and unknown delivery states are handled
   exactly as specified;
7. accepted shadow candidates are proven absent from future dialogue memory;
8. current-turn capture cannot observe its actual assistant outcome;
9. bootstrap and incremental transition are deterministic and replayable;
10. corrections preserve provenance and prevent stale reassertion;
11. Perception and Interpretation receive typed bounded projections;
12. Goal and Action receive only evidence-linked dialogue objects;
13. Realization receives only selected authorized content, never raw history;
14. cache-disabled, cache-hit, and cache-miss paths are semantically identical;
15. crash recovery and worker retry cannot lose or duplicate a turn;
16. deletion, retention, ownership, and privacy tests pass;
17. existing v0.2 stored traces retain defined replay behavior;
18. real-PostgreSQL focused tests and the complete backend suite pass;
19. an independent audit finds no implementation blocker; and
20. the canonical README records exact versions, coverage, test counts, known
   omissions, and the continuing lack of live approval.

Passing these criteria does not establish correct long-term memory,
relationship understanding, semantic summarization, behavioral calibration,
or readiness to replace the live chatbot.

## 26. Implementation order

Implement in this order:

1. immutable contracts, versions, canonical ids, and digests;
2. message ordering column and real-PostgreSQL migration;
3. channel-specific exposure/eligibility policy;
4. immutable snapshots, heads, jobs, and DB constraints;
5. pure bootstrap and updater;
6. PostgreSQL-only persistence and catch-up path;
7. end-of-turn job scheduling in every response path;
8. capture v0.3 and pipeline v0.3 turn-local handoff;
9. Perception and Interpretation projections;
10. Goal/Action grounded-object projection;
11. Realization content v0.2 projection and replay compatibility;
12. multi-turn design cases and adversarial tests;
13. optional cache protocol and implementation;
14. deletion, retention, concurrency, and isolation tests;
15. complete regression suite;
16. independent audit; and
17. canonical README update.

Do not begin with Redis or by passing the raw transcript to GPT-6 Luna. Begin
with source order, eligibility, immutable PostgreSQL state, and deterministic
reconstruction.

## 27. Prohibited shortcuts

Do not:

- make Redis or an in-process dictionary the source of truth;
- overwrite one mutable JSON blob without immutable predecessors;
- reuse the legacy `working_memories.cognitive_state` blob as an unversioned
  canonical contract;
- order a conversation only by timestamp;
- re-read the latest conversation state during retry of an older capture;
- include the current turn's assistant response in its own shadow input;
- treat generated text as delivered text;
- write an accepted shadow candidate into dialogue memory;
- treat a legacy-agent assistant assertion as trusted knowledge;
- treat a user statement as verified fact;
- erase corrected content or provenance;
- pass raw transcript, snapshot JSON, ORM objects, or cache entries to the
  Realization model;
- use GPT-6 Luna or another LLM to invent or summarize bootstrap memory;
- conflate short-term state with SemanticMemory, EpisodicMemory,
  RelationshipMemory, or LearningMemory;
- update only one chat transport or execution path;
- swallow sequence/digest/ownership mismatches;
- place message text in logs, metric labels, ids, or cache keys;
- change historical v0.2 replay semantics in place;
- enable live response routing; or
- claim conversational completeness because a narrow shadow case succeeds.

## 28. Handoff result

When implementation is complete, report:

- files and migrations changed;
- exact contract/updater/projection/cache/capture/pipeline/content versions;
- how existing message rows were ordered and backfilled;
- the channel-specific exposure mapping;
- bootstrap bounds and compaction behavior;
- database schema, constraints, immutability trigger, and head CAS behavior;
- end-of-turn scheduling points for every chat path;
- lazy catch-up and crash-recovery evidence;
- cache-disabled and cache-failure equivalence results;
- proof that shadow candidates never enter memory;
- proof that the current shadow turn never sees its assistant outcome;
- focused and complete test pass/skip counts;
- real-PostgreSQL migration, concurrency, persistence, replay, retention, and
  deletion results;
- known semantic/reference-resolution limitations; and
- remaining blockers before broader shadow rollout or any live-response
  experiment.
