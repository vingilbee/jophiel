# Student 2 cognition revamp — implementation handoff

2026-10-23. Design and implementation brief, not a claim of implemented behavior.

## Objective and authorization

Implement the complete cognition revamp and coordination of existing components. Student 2 becomes the learned decision authority for deliberate behavior: responding to users, handling pending work and private thoughts, pursuing goals, and accepting or declining component proposals. Start with **ModernBERT-base**. Preserve the teacher–student approach and Student 1's user-message interpretation role.

The user explicitly wants behavior learned by the student, not a runtime prompt or deterministic social policy telling her when to speak, acknowledge, ask, or remain silent. Teacher instructions describe the judgment to learn; they must not reduce it to fixed situation-to-action mappings.

Implement in stages, but do not redefine the full task as a speech-only prototype. Validate model suitability early before migrating all production paths. This document specifies the intended destination; the implementation agent must report remaining gaps honestly.

Future learning is **not** part of this implementation. Provide integration boundaries for later belief/opinion learning, game learning, conversation learning, and learning new executable skills. Do not build those learners, automatic skill creation, online weight updates, or a universal planner now. Memory/observation quality repairs remain a separate workstream; adapt their interfaces without claiming the cognition revamp fixes retention or retrieval.

## Agreed decisions versus implementation proposals

Agreed in this conversation:

- Try ModernBERT-base for Student 2; it selects decisions, not reply text or free-text plans.
- Inputs include trigger type, latest conversation message, recent ordered conversation, time, work status, and private thought content.
- Goals/intentions remain free text. No separate conversation-goal subsystem or Minecraft-specific output labels.
- A general `pursue_goal` action selects a referenced proposal/goal.
- Goal execution and communication are independent: act only, speak only, both, or neither.
- Components need a common way to submit proposals; submitting is not authorization.
- Future features should integrate through shared proposal/capability interfaces.
- Separate evidence fields were excluded from the initial learned input design. Preserve provenance and execution validation in infrastructure; do not silently add an evidence-scoring task to Student 2.

The exact schemas and action names below are recommended implementation defaults, **not all previously approved vocabulary**. Refine them against actual callers and label ambiguities before bulk data generation. Additional inputs such as mood, memory, notes, and Student 1's probabilities were discussed but not finalized; document their inclusion and evaluate their value rather than assuming every legacy feature must remain.

## Current implementation and concrete gaps

Paths are relative to the repository root. Recheck current source before editing; historical reports are not deployment verification.

| Area | Current implementation | Required change |
|---|---|---|
| Student 2 | `backend/app/ai/Cognitive/simple/student/model.py`: projected per-text slots, concatenation, MLP; main move, optional second move, four dials | Replace/extend with jointly contextualized text and decision heads; retain baseline for comparison |
| Text encoder | `student/encode.py`: frozen `intfloat/multilingual-e5-small`, 384-dimensional vectors, per-text 256-token truncation | Define ModernBERT input serialization, bounds, and truncation explicitly; preserve role/source distinctions |
| Live cognition | `backend/app/services/cognitive_live.py`: Student 1 live; Student 2 offline | Add loading, inference, versioning, failure handling and real decision consumption |
| Pending/result decisions | `backend/app/ai/state/decisions.py`, `backend/app/services/cognition_decisions.py`: production providers return unsupported | Implement trained adapters for real events |
| Pending work | `services/coordinator.py`: unsupported immediate decision defaults to `respond` | Replace normal fallback behavior with student inference; keep outages explicitly labeled |
| Finished thoughts | Coordinator blocks unsupported decisions; `silent` and `defer` both record not-sent and return | Implement private retention versus durable deferred reconsideration |
| Recall/thinking selection | `ai/state/assessment.py`, `ai/state/routing.py`, `services/shared_state.py`: separate assessor and routing logic | Inventory and transfer behavioral selection ownership explicitly; do not leave competing selectors |
| Teacher | `simple/intention.py`, `simple/frozen.py`, prompts: intention → spoken reply → extracted labels | Support no-reply, work-only, proposal and asynchronous-event examples; separate decision from realization |
| Social masks | `simple/turn.py` masks greetings, apologies, commitments and farewells | Remove/rework heuristic social restrictions; retain genuine capability/authorization validity checks |
| Autonomous speech | `services/autonomous_speech.py`: proposal path plus already-worded delivery path and restraint rules | Ensure every deliberate initiation has a traceable cognition decision; audit social cooldowns versus transport protection |
| Hard mind | `services/mind_hard.py:route`: sends tips directly to body and creates intentions, while speech is proposed | Route deliberate action/goal proposals through cognition; retain private thought recording |
| Planner | `services/game_planning.py`, `ai/mind/plan.py`: selects keep/revise/new_goal/wait/done and applies plan before speech proposal | Separate goal authorization from detailed planning; route changes exceeding authorized scope back to cognition |
| Goals | `services/memory_manager.py`, `db/models.py:WorkingItem`: free-text goal, owner/context/status/revision, optional parent and done condition; session links to goal | Reuse goal store/lifecycle, not a second competing goal database |

The coordinator currently rejects some results after newer user messages before cognition sees them. Preserve freshness checks, but distinguish an invalid executable result from a still-meaningful thought that can be resubmitted for a fresh decision. Never replay a stale authorization.

## Responsibilities

| Component | Owns |
|---|---|
| Student 1 | Interpreting user messages; no requirement to pretend internal events are user messages |
| Mind | Private conclusions/questions/intentions and proposals; no unilateral deliberate external action |
| Student 2 | Contextual behavior and proposal/goal selection |
| Coordinator | Scheduling, scopes, leases, dependencies, cancellation, idempotency, expiry, result validation, output reservations |
| Planner | Detailed steps using available capabilities within an authorized goal; proposal when changing that goal or exceeding authority |
| Mouth | Wording and delivery of selected communicative intent; no independent extra goal or question |
| Hands/executors | Execution within authorized scope, local bounded control, progress/outcome reporting |
| Observation/memory | Their owned processing and storage; relevant results may trigger cognition |

Do not put a model call on every heartbeat, memory write, or motor step. Independent background maintenance continues independently. User cancellation, ownership checks, unavailable capabilities, and stale execution rejection remain deterministic. Safety and execution constraints are not personality policy.

## Input contract

Build one versioned, bounded decision snapshot. Keep model-visible content separate from routing metadata. Optional fields have explicit absence; absence must not look like a negative fact.

| Input | Meaning |
|---|---|
| Trigger | User message, pending-work event, thought result, component proposal, relevant work outcome, or deferred reconsideration |
| Current message | Latest available conversation message with speaker; may be absent for a non-conversation event |
| Conversation history | Ordered speaker-tagged messages including actual delivered replies; avoid duplicating current message |
| Time | Event age, time since user message/delivered reply, work age as relevant; time of day optional |
| Work status | Relevant work type, subject, actual state and unresolved part; queued differs from running; bounded list |
| Private thought | Conclusion, question or intention in its own section; distinguish it from user statements and teacher rationale |
| Activity context | Current goal, relevant situation, current plan summary and capabilities needed to evaluate a proposal |
| Proposals | Bounded candidates with content, purpose, target and relationship to current goal |

Activity context and proposal content are necessary extensions for the agreed goal-selection scope. Define their adapters without embedding game-specific fields into the universal contract. Domain descriptions may contain game facts; the generic schema and action vocabulary must not depend on Minecraft.

Owner IDs, activity IDs, model versions, causal references and source revisions travel with the snapshot for validation and logging. Avoid feeding arbitrary UUIDs as semantic features. Candidate IDs are mapped from model-selected positions back to validated records.

If retaining Student 1 features, preserve probabilities and evaluate using actual predictions, not only perfect teacher labels. Thought input is the runtime thought available to the decision, not the teacher's private explanation of its target decision. Do not leak future outcomes or hidden conversation history into student training inputs.

## Output contract and action coverage

Use related heads/decisions instead of one flat list mixing social style, goal management and scheduling. One forward pass can produce multiple outputs. The exact factorization should follow evaluation; a giant Cartesian-product label is not required.

### Communication

Recommended choices: `respond`, `acknowledge`, `partial`, `ask`, `silent`, `defer`.

- `respond`: communicate the selected content or intention now; does not certify correctness or full knowledge.
- `acknowledge`: acknowledge the actual request/work state without inventing progress.
- `partial`: communicate available content while an identified part remains unresolved.
- `ask`: initiate a question, including clarification, curiosity or coordination.
- `silent`: choose no communication for this event; private retention is independent of speaking.
- `defer`: retain a candidate for a later decision, with a durable reconsideration mechanism and bounded expiry.

Specify the distinction between `ask` and existing ask-style conversational moves. For example, communication chooses to ask and the move identifies curiosity versus clarification; avoid contradictory duplicate labels. `silent` and `defer` have no spoken move or style target.

Preserve the existing conversational vocabulary when applicable:

`greet`, `say_bye`, `react_briefly`, `answer_explain`, `ask_about_it`, `check_meaning`, `show_understanding`, `comfort`, `celebrate_thank`, `play`, `share_view`, `agree`, `push_back`, `apologise_fix`, `say_yes_commit`, `say_no_limit`, `suggest_offer`, `change_topic`, `share_about_herself`, `say_how_she_feels`.

Retain optional secondary move and length/warmth/playfulness/directness controls. They must not force a spoken response. An acknowledgment delivered earlier is observable history, not merely an intended/predicted move.

### Work and goals

Recommended coverage, with final enum/factorization to be settled before labeling:

| Decision | Meaning |
|---|---|
| No change | Do not initiate/change work; existing authorized work may continue |
| Request recall | Retrieve for a referenced unresolved need |
| Request thinking | Think about a referenced question/problem |
| Recall then thinking | Explicit dependency, not two unrelated competing jobs |
| Pursue goal | Adopt a referenced proposed/existing goal; use valid existing plan or request planning |
| Continue goal | Explicitly retain current goal when alternatives are considered |
| Pause / resume | Change an identified work/goal lifecycle where supported |
| Cancel | End identified work/goal; never erase the record |
| Request replanning | Keep the goal, ask the planner for another approach |
| Decline proposal | Do not pursue this candidate; distinct from silence about it |
| Defer proposal | Keep candidate pending for reconsideration; independent of communication deferral |

These are generic operations, not `mine_iron`, `explore_minecraft`, or future feature labels. Proposal targeting is required: `pursue_goal` without a selected goal/proposal is incomplete. Pausing, cancellation and replanning similarly require a target.

When adopting a conflicting new goal, explicitly retain, pause, or replace the old one. Never mark an abandoned goal completed. Code validates transitions; the student selects behavioral intent. Do not make the student guess completion from its own earlier promise: completion comes from actual outcomes.

An initial implementation may decide on one event/candidate at a time with bounded alternatives. Document the selection policy and ensure it cannot quietly hardcode the behavioral priority that cognition should learn.

## Generic proposal and capability interfaces

Reuse existing durable event/work infrastructure where possible. A proposal is not an executing goal until accepted.

Recommended proposal fields:

```text
schema_version, proposal_id, producer, cause/event_reference
owner_scope, conversation/activity_scope
goal_or_action_text, purpose, target_reference
current_goal_relationship, capability/planner_reference
input_versions, created_at, expires_at, dedupe_key
status, supersedes_reference
```

Keep free-text meaning and structured routing together. Planners validate whether a goal is achievable with their actual capabilities. Student 2 must not generate arbitrary executor IDs, commands, or plan text. Authorization is scoped to a validated proposal and does not grant unrestricted tool access.

Example: mind proposes “Explore beyond the mountain,” with the current activity and planner reference. Student 2 selects `pursue_goal` and independently `silent` or a communicative choice. Planner produces valid steps. Hands execute. Significant outcomes return as events; ordinary movement does not require fresh cognition.

Capabilities should expose stable identifier/version, plain-language description, accepted inputs, applicability/availability, planner/executor routing, and outcome schema. Reserve optional experimental/established status for later learned skills without implementing skill learning now.

A new feature should ordinarily require an adapter, capabilities and executor/planner integration—not core cognition branches or new action labels. Unfamiliar domains may still require additional training examples. Do not promise automatic generalization.

## Runtime semantics

1. A meaningful event creates a scoped snapshot and candidate set.
2. Student 2 selects communication and work/goal behavior.
3. Coordinator validates scope, target availability, relevant versions, cancellation and lifecycle compatibility.
4. Accepted intents are durably dispatched to mouth/planner/executor through existing infrastructure.
5. Delivery/execution results are recorded separately from authorization and can trigger a new decision.

Required properties:

- Idempotent acceptance and dispatch under duplicate events, retries and worker restart.
- No DB locks held across inference/network calls.
- New user input can interrupt output and invalidate authorization; fresh reasoning may reuse valid thought content.
- Deferred items have a wake condition or bounded next review time, expiration, and a retry/reconsideration limit. Waking means reassess, never automatically speak.
- Stale plans/results cannot mutate current state. Unrelated state changes should not invalidate everything.
- Never treat model failure as an intentional `silent` decision. Trace unavailable/invalid decisions and use an explicit operational fallback.
- Preserve user-authorized execution and cancellation semantics. Do not silently replace functioning paths with always-blocked stubs and call the migration complete.
- Audit ordinary chat, background `say`/`offer_to_learn`, planner speech, mind questions, correction speech, nudges, tool delegation and deliberate hands commands. Each active path needs a documented authority chain. Already-authorized execution need not receive duplicate approval from cognition.

## Teacher and training redesign

Main teacher page updated in place 2026-10-23: `docs/characters/columbina-v0.6.md`; event prompt asset:
`backend/app/ai/Cognitive/simple/prompts/decide_events_v1.txt`. Read
`docs/STUDENT2_TEACHER_PROMPT_REVIEW.md` for integration requirements, coverage gaps and open
personality questions. The main page and intention prompts are wired to the existing teacher; the event prompt needs the new runner. Neither revision has been behaviorally evaluated. The old runner still always generates speech.

Keep the existing character-centered teacher approach, but support event-centered decision episodes, not only alternating user/reply turns. A teacher can use prompts; the student's runtime behavior must come from trained inference.

Teacher guidance should allow acting, speaking, both or neither. It should consider her intentions and actual situation without rules such as “new goal → announce” or “thinking → acknowledge.” An intention like “know the user better” can lead to a question, self-disclosure, or letting a moment settle. It is not an automatic label mapping.

Generate a structured decision first; realize speech only if selected. Silent/work-only samples remain valid training examples. Preserve free-text teacher explanation for audit, separate from model-visible inputs. Teacher and student must share the same available situation; exclude privileged/full history unless also represented in the student input.

Required example coverage:

- Ordinary characterful conversation and all existing moves, not just task assistance.
- Pending versus running versus completed/failed work, partial knowledge, and already-delivered acknowledgment.
- Useful/irrelevant/repeated thoughts; questions; uncertainty expressed in thought content; user topic changes.
- Same thought or goal with different timing/history and different appropriate behavior.
- Action-only, speech-only, both, neither; silence and deferral are distinct.
- Conflicting/current/new goals, continuing a goal, pausing/resuming, cancellation and replanning.
- Domain-varied proposals; reserve at least one activity/domain for generalization evaluation.
- Failure, invalid/unavailable capabilities, interruption and resumed/deferred decisions.

Audit teacher quality on a small representative batch before generating at scale. Do not assume teacher labels are ground truth. Preserve disagreements/acceptable alternatives where behavior is legitimately ambiguous. Inspect class balance and missed vocabulary through explicit “does not fit” diagnostics; do not force every new situation into an old spoken move.

Split by episode/scenario family, keeping near-duplicate and counterfactual variants in the same split. Evaluate complete episodes as well as isolated decisions. Retain the current MLP as an experimental baseline; ModernBERT is the selected first implementation candidate, not a proven winner. Stop and report if it cannot meet quality/resource requirements rather than silently substituting an LLM.

## ModernBERT implementation

Use `answerdotai/ModernBERT-base` with task-specific classification/regression and candidate-selection heads. Fine-tuning, input layout, numeric feature fusion, pooling and loss masking are implementation choices to validate. Do not train a language model from scratch or require generation of plans/replies.

Official references:

- https://huggingface.co/answerdotai/ModernBERT-base
- https://huggingface.co/docs/transformers/model_doc/modernbert

The model card describes a 149M-parameter English/code encoder with an 8,192-token native context. Do not automatically use the maximum context. Bound input length and measure latency/memory on intended hardware. Existing encoding is multilingual; switching to an English-oriented model is a material coverage change to document and evaluate if other languages are required.

Record checkpoint revision, tokenizer, schema, label maps, preprocessing, feature normalization and teacher/data versions. Model/schema mismatches must fail visibly. Keep Student 1 loading independent. Report training and inference resource usage separately; weights-only estimates are not process memory measurements.

## Implementation stages and deliverables

1. **Inventory and contracts:** enumerate active callers and ownership; finalize input/output schemas, proposal lifecycle, routing and teacher label semantics. Mark unsupported capabilities explicitly.
2. **Teacher pilot and student feasibility:** create reviewed event-based examples; train/evaluate ModernBERT with the proposed heads; compare baseline; measure memory/latency. Agree measurable release criteria from this evidence before bulk generation or activation.
3. **Core runtime:** wire live Student 2, pending/result decisions, durable deferral and actual delivery outcomes. Preserve normal conversational quality.
4. **Goals/proposals:** integrate shared proposals and candidate selection; transfer recall/thinking ownership; connect existing planner and executor paths. Migrate deliberate-action bypasses.
5. **Full validation and cleanup:** multi-component flows, restart/race tests, live model behavioral evaluation, inspection traces, rollout/rollback documentation. Remove obsolete social masks and competing authorities once replacements work.

All stages belong to this task. The early milestone is a risk check, not permission to deliver a speech-only implementation as the completed revamp.

## Acceptance and completion checklist

### Behavioral evaluation with real trained inference

- User request requiring thought: student selects immediate behavior, then separately handles the finished thought.
- A thought can be shared, turned into a question, retained privately, or deferred and later reconsidered.
- Same content under changed conversation/timing yields context-sensitive decisions; action choice is not a hardcoded trigger mapping.
- A goal can be pursued without speech and with speech; speech need not initiate external work.
- Current goal, alternative proposal and cancellation are handled without accidental goal completion or forgotten active obligations.
- Domain-neutral interfaces work for an existing executable activity and a distinct test adapter; no game-specific cognition enum or branch.
- Existing conversational range, personality and multi-turn continuity remain evaluated.

### Operational verification

- Supported decisions reach real runtime effects, not only test provider stubs.
- Every active deliberate-action path has an identified cognition authorization or a documented bounded continuation under prior authority.
- Duplicate events/retries cannot duplicate delivery or execution; restart preserves work and deferrals.
- Interrupted/stale/expired results cannot speak or change the wrong activity or conversation.
- Runtime outage and unsupported capability are distinguishable from intentional silence/decline.
- Traces preserve input snapshot/version, candidate set, model decision, validation outcome and actual effect. Avoid logging secrets; use appropriate retention for conversation content.
- Teacher/student input parity, target leakage checks, held-out episode splits and candidate-order sensitivity tests pass.
- Report confusion/per-action metrics, unnecessary speech, missed appropriate speech, incorrect proposal selection, invalid authorization rate, latency and peak memory. Set thresholds explicitly; do not invent passing numbers.

Scripted tests establish coordination, not intelligence. Model agreement with its teacher alone does not establish naturalness; include reviewed multi-turn behavioral examples. Preserve memory/history regression fixtures where integration affects supplied context, without claiming unrelated memory defects are repaired.

### Future learning readiness, without implementing learners

- A synthetic future component can submit a goal such as “Practice navigating narrow paths” using the same proposal interface.
- Its accepted work can route to a registered test executor and return a normal outcome; no core cognition domain branch is necessary.
- Decision/outcome records distinguish chosen, accepted, attempted, completed, failed and cancelled.
- No automatic belief writes, online model retraining or new executable skill generation is introduced by this test.

## Handoff completion report

Report what changed, migrated callers, training/data/model versions, teacher audit findings, measured behavioral and resource results, remaining limitations, migrations/configuration and rollout/rollback steps. Separate code completed, tests passed, model quality validated and deployed state. Do not mark unsupported providers, scripted decisions or unused abstractions as completion.
