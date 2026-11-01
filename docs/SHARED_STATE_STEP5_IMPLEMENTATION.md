# Step 5: autonomous speech routing

> **Superseded 2026-10-22** by the legacy cleanup (`LEGACY_CLEANUP_2026-10-22.md`): the shared-state pipeline is the only reply preparation, `SHARED_STATE_PIPELINE_ENABLED` and the old preparation branch are gone, and Cognitive V0, its shadow and dialogue memory are retired. The two producers left out below were migrated then: `game_correction` now proposes through `autonomous_speech.submit`, and the background `say`/`offer_to_learn` tools deliver through `autonomous_speech.deliver_worded` (common delivery arbitration, no second cognition pass). Kept as the record of what was built.

Implemented 2026-10-22. This changes wiring, not the trained cognition capabilities.

## Routes

Quick thoughts with proposed speech, completed hard thoughts/questions, and applied planner speech/questions now submit to `services/autonomous_speech.py`. Legacy pending hard questions also submit here. None of these producers calls `agent_say.deliver`.

The route is:

`producer → durable CoordinatorWork proposal → result cognition → CoordinatorOutput reservation + ChatJob → existing mouth → existing stored-message/announcement delivery`

The coordinator consumes the already completed result without invoking mind again. It passes event provenance, user, activity, target conversation and input versions to the step-4 result-cognition interface. The mouth receives an explicitly autonomous context, not a fabricated user request.

## Persistence and guards

No schema migration is needed. Existing coordinator work rows hold the event ID, source, activity, explicit target, conversation sequence, and Minecraft plan, execution and lifecycle revisions. Their existing unique idempotency key deduplicates the producer event. The existing unique output phase and deterministic follow-up turn prevent repeated dispatch. Accepted autonomous results without an output decision are recovered after worker restart.

Targets come from the activity's conversation binding (or the conversation activity itself), with ownership checked. No latest-conversation fallback exists. Missing targets and disabled pipelines are rejected and traced without delivery; these rejections cannot use coordinator tables because those require a conversation target.

Current delivery restraint, quiet mode, recent-user hush and chatter/serious-event guards are checked before dispatch and again at mouth start and persistence. Work expires at a bounded deadline. New user messages and changed activity versions keep results private. Ordinary chat and autonomous output use the same per-conversation job arbitration. Autonomous output queues behind an active reply; its announcement retains the existing urgency interruption flag. The final conversation check holds the conversation sequencing lock through persistence.

Producer logs distinguish proposals from delivered speech: saving a proposal never fills `MindThought.said`. The authoritative delivery receipt is `CoordinatorOutput.assistant_message_id/status`. Actual mouth delivery updates the existing Redis chatter/serious-event receipts.

## Explicit limitations

Production result cognition remains unsupported, as in step 4: the output records `blocked`, the missing capability, and `not_sent`. Scripted providers exercise respond/ask/defer/silent. There is no substitute model or silent heuristic authorization. Deferred/silent proposals are retained privately with that decision; this step does not introduce an autonomous reconsideration scheduler.

Turning off the shared-state pipeline rejects these proposals; it does not restore direct producer delivery. No feature flags were enabled by this change.

Hands commands, intentions, and planner execution remain unchanged. Observation/memory scheduling is unchanged. Existing `game_correction` delivery and the mouth's low-level desktop speech tools are outside the migrated producer set. Step 6 remains responsible for broader cross-channel/voice interruption and transport restart coverage; this change does not claim exactly-once websocket delivery.

## Validation

59 tests passed against a disposable PostgreSQL cluster: autonomous routing, step-4 coordinator regression, quick/hard mind, and game planning. Coverage includes all scripted cognition branches, explicit unsupported behavior, duplicate proposals, restart recovery, quiet/recent-user/deadline guards, cancellation before mouth and during generation, and unchanged hands routing. No live model calls or application database data were used.
