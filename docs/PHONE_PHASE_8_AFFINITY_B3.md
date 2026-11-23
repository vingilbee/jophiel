# Affinity B3 implementation handoff

> **Status:** implemented in the backend (2026-11-22). No backend flag (the user's decision, 2026-11-22):
> the phone's Messages switch is the one gate (unknown = paused). Handset verification not done yet. `services/phone_replies.py` (targets, follow-ups, the checked send), the
> `reply_notification` tool in `app/ai/tools/phone.py` `build_reply_tools`, migration `c6f9e3b8d5a2`
> (`phone_outbound.attempt_key`), tests `tests/test_phone_affinity_b3.py`. Choices made where this left room:
> - **Targets** are bound at the tick (`mind_phone` moment `reply_targets` -> the wake's input), one per
>   notification, to its newest new message: key, revision, conversation id, event id, sender id. Eligible only
>   with a reply button, `conversation_identity: shortcut`, an event id and a non-owner sender id; in a group the
>   sender must be `identity: contact` (a name-derived id can be two people). Read only on the notification wake
>   itself (`Evaluation.reply_targets`), never by thinking it starts or wakes it schedules.
> - **The tool** `reply_notification(notification, text)` sits only on those wakes and on a due follow-up; device actions stay off there. The own-turn `reply_notification` behind the flag was removed
>   (an own turn alone doesn't say which exchange she answers).
> - **The send** (`phone_replies.send`): whitespace-normalised once, held (never cut) past 2000 characters, the
>   B2 check run on exactly that text with its material now carrying key / revision / conversation / recipient
>   (sender id and person), which must match the target. After the model call: Messages switch reported
>   on (unknown = paused), the notification live with the same revision and newest message, the same phone
>   session, and an unchanged fingerprint of what the user authorized and the requests for space. Then a claim
>   (`phone_people.claim_outbound`, unique `attempt_key`) before `phone_actions.act(..., action_id, claimed)`.
>   Attempt keys: `reply:<conversation>:<event>` (one per triggering exchange) and `followup:<wake id>` (one per
>   due occurrence). A claimed attempt is never retried, whatever its result (`unknown` stays unknown until a late
>   answer settles the same row).
> - **Follow-ups:** `come_back_to(..., phone="notif:...")` keeps `{person_id, conversation_id, is_group,
>   sender_id, event_id}` (no draft, no notification key) on the wake, through reschedules, and makes it
>   `third_party`. When due, "Now" says who and whether a live reply button exists; she may write only to a live
>   notification of that conversation whose newest message is that same person's. A changed speaker, an answer
>   from the phone or a removed notification ends the attempt; a later notification can prompt a fresh decision.
> - The installation is bound through the phone session (`target_for`) and the per-install sender id.

Status — proposed implementation plan, 2026-11-22. Build on merged B2. Deliver immediate autonomous replies first, then scheduled follow-ups. Both send through an Android notification's live Reply action. Opening WhatsApp and tapping Send on screen belongs to phone Phase 9.

## Starting points

- [Phone tool](../backend/app/ai/tools/phone.py) already offers reply_notification on own turns behind PHONE_AUTONOMOUS_REPLIES, resolves a notification handle, checks messages_on, and labels the action asked_by="her". An owner-directed reply uses the same transport. Own-turn status alone is too broad to identify the exchange she is answering.
- [B2 check](../backend/app/services/phone_reply_check.py) evaluates her draft with evidence for that recipient and audience. Mind writes the reply in her own voice. Keep content judgments, including disclosure and requests for space, in the existing policy. The B2 relationship proposals and phone lessons remain in shadow.
- [Dispatcher](../backend/app/services/phone_actions.py) records outbound attempts and handles socket or push delivery, timeouts and late results. In the sibling frontend-phone repo, JophielNotificationListener.reply already checks the local Messages switch, conversation and revision at execution. Reuse the existing switch and Android Reply action.
- Read the [B2 implementation decisions](PHONE_PHASE_8_AFFINITY_B2.md) and [overall delivery checks](PHONE_PHASE_8_AFFINITY.md) before editing.

## 1. Reply to a notification that woke Mind

1. Trace notification wake evidence through services/mind_phone.py and the Mind turn into build_phone_tools. Supply the eligible targets from that wake as backend-owned context. For a batched wake, bind each eligible handle to its own triggering exchange. Require strong conversation identity and an identified recipient. A display title, ambiguous speaker, or unrelated active notification cannot establish the target. Third-party words grant no authority for other device actions.
2. Mind may ignore the message, wait, ask Vincent privately, or choose reply_notification and draft her reply. Run B2 check_reply on that draft before dispatch. The current phone tool truncates text in _text and the checker normalizes whitespace. Validate the full input before any truncation, then send exactly the text evaluated. A hold, unavailable check or oversized draft ends the attempt without sending.
3. Bind the check to the actual evidence it evaluated. Carry stable target identifiers through the check, including phone installation/session, conversation, notification key and revision, and the recipient identity within that audience. Revalidate at the last backend send point after the model call. Hold if the target, recipient, relevant authorization or boundary changed, the Reply action disappeared, or the switch or feature flag no longer permits sending. Treat unknown opt-in state as paused for autonomous sends. Keep the handset's final local check for changes during delivery.
4. Use the existing outbound record and owner-visible receipt. Claim a stable attempt for the triggering exchange before publishing. Concurrent wakes and retries must not publish the same reply twice. Preserve unknown delivery on timeout and reconcile late results against the same attempt. Verify owner-requested replies, sensing and private messages to Vincent still work under their existing rules. Resuming allows fresh consideration and never flushes a held draft.

## 2. Follow up when an intention comes due

Extend the existing Mind intention lifecycle. Persist why she might follow up, when to reconsider, and stable identifiers for the original person, conversation, audience and supporting exchange. Avoid storing a ready-to-send draft or using a notification handle as a durable identity. Carry only audience-appropriate material into deferred phone context.

At the due turn, resolve a current strong target for that same person and audience and reread the exchange. Mind can drop the intention if the person already answered, asked for space or disengaged. If she still wants to reply, compose fresh text and use the same checked dispatcher. A missing Reply action, changed group recipient or disabled switch produces no send. Consume or settle the due occurrence through the existing intention lifecycle so a worker retry cannot send twice. A later notification can prompt a fresh decision.

## Acceptance and rollout

Keep autonomous replies disabled by default while implementing. Extend the existing phone and Mind tests with the following observable outcomes.

| Exercise | Expected result |
| --- | --- |
| Stranger and close friend, each with an eligible notification | Mind can answer or decline. Both receive identical action permissions. A passing send records the exact approved text once with asked_by="her". |
| Batched DM and group notifications, duplicate names, title-only identity | Only a strongly identified triggering target is eligible. A DM authorization cannot be used in the group. |
| Checker holds, errors, or receives an oversized draft | No frame reaches the phone. No shortened draft is sent. |
| Pause during composition, remove the notification, or change revision or group speaker during the check | The stale attempt sends nothing. Resume alone sends nothing. |
| Replay a wake, run two due workers, or deliver a late success after timeout | At most one publication per attempt. The receipt reflects the eventual known result. |
| Due follow-up with a live target, an answered question, a request for space, or an expired notification | A grounded fresh reply can pass. Mind can leave an exchange alone. A missing target produces no send. |

Before enabling opt-in use, exercise a real phone with a test conversation: receive a notification, observe one autonomous reply, pause during another attempt, and remove a notification before a follow-up. Record what the receiving chat and the owner receipt each show. Report automated and handset verification separately.
