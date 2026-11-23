# Affinity B2 implementation handoff

> **Status:** implemented (2026-11-22). `services/phone_relationships.py` (readings, shadow proposals, requests for
> space, authorizations, the learner's view, review), `services/phone_writer.py` (the writer boundary), migration
> `b5e8d2a7c4f1`, tests `tests/test_phone_relationships.py` and a regression in `tests/test_learning_lessons.py`.
> **Inspect shadow results:** `GET /api/memory/people/{id}/review` (owner only): the active record beside the shadow
> view, every proposal with its exchange, audience and event key, her readings, requests for space, authorizations,
> phone assessments and lessons (`live: false`). A correction: `POST /api/memory/people/{id}/evidence/{eid}/retract`.
> Choices made where this left room:
> - Her mind gets three tools only on turns someone else's words on the phone caused: `read_phone_conversation`
>   (the messages the phone shows, with `msg:` handles, her echoed replies, `reply_to` when the phone sends it,
>   unknowns said), `note_exchange` (who it was addressed to, a scoped meaning, the person's exact words, the
>   changed fields, `worth_learning`, `also_for`) and `note_request_for_space`. `authorize_detail` sits beside
>   `read_person` on the owner's reply turns. Prompt guidance lives in the tool descriptions and stays general.
> - Familiarity past "new" is refused unless she has sent where they are and they addressed her (now or before).
> - A personal lesson is routed only when she has a `sent` reply in that conversation and judged it addressed to
>   her. It goes through the real learner as a `phone` assessment (that conversation and that person's lessons
>   only, nothing of the owner's memory), and the lesson carries `phone_person_id` / `phone_conversation_id` /
>   `phone_applies_to` / `phone_live = false`. Every ordinary retrieval filters phone lessons out in the query;
>   only `memory_lessons.for_phone_audience` reads them, live ones only. Nothing in B2 sets `phone_live`.
> - `also_for` is honoured only for a conversation the person shares with the user whose title their words name.
> - The writer's intent check refuses a content word known only from outside the audience (the person's other
>   conversations, notes or requests said elsewhere, other audiences' authorizations, the phone's other
>   notifications, deferred material for elsewhere). It can over-refuse a common word; the refusal names it.
> - Phone messages gain an optional `reply_to` (the replied-to `event_id`); nothing infers it.
> - Not built: lifting a request for space from a tool (service `lift_boundary` only), owner corrections of
>   identity, retention.

**Status — agreed scope, ready for backend implementation.** Build on [B1](PHONE_PHASE_8_AFFINITY_B1.md) and the [overall affinity plan](PHONE_PHASE_8_AFFINITY.md). B2 connects phone exchanges to relationship assessment and the existing learner, and builds the audience boundary for the third-party reply writer. B3 owns autonomous sending, scheduled follow-ups, the owner's switch and dispatch-time checks. B2 requires no phone UI, draft preview or Phase 9 screen access.

## 1. Give Mind evidence it can interpret

Start with [phone ingestion and identity](../backend/app/services/phone_people.py), [notification context](../backend/app/services/phone_notifications.py) and [turn preparation](../backend/app/services/mind_turn.py). Reuse B1's owner-scoped person, conversation, source event and outbound exchange records. Assemble the recent messages actually available for the conversation. Preserve speaker attribution, confirmed Jophiel replies and reply-to information when available. Keep unknowns explicit. A DM on Vincent's account does not establish who a message addresses.

Code supplies facts and enforces audience boundaries. Mind judges who was addressed, what the exchange meant and whether anything changed. Keep prompt instructions general. Put the specific social scenarios below in tests.

## 2. Produce two independent shadow results

- **Relationship assessment.** Mind can propose changes to B1's familiarity, warmth, initiative and short audience-scoped relationship note. Record only changed fields, the supporting observation, source event, audience and time. Most exchanges warrant no change. An overheard message to Vincent can shape a first impression. Familiarity still reflects whether Jophiel and the person have spoken. Her own outgoing words cannot establish reciprocity.
- **Personal lesson.** Connect eligible exchanges to the [existing assessment](../backend/app/services/learning_assessment.py), [lesson proposal](../backend/app/services/learning_lessons.py) and [lesson memory](../backend/app/services/memory_lessons.py) paths. Reuse their judgment and evidence handling. If Jophiel did not participate or cannot tell whether the person addressed her, skip the personal lesson. One direct statement can support a proposal. An inference needs evidence that supports the interpretation, with no fixed message count.
- **Scope.** Bind proposals to the owner, person, original conversation and supporting event references. A preference applies in its original audience by default. Extend its behavioral application only when the person's wording explicitly covers another identifiable audience. The private explanation stays in its original conversation.
- **Shadow behavior.** Persist inspectable proposals and supporting observations for backend review. Keep them out of active relationship reads, ordinary lesson retrieval, prompts and scheduling. In particular, verify that routing a phone assessment through the learner cannot automatically activate its lesson. The owner's relationship question continues to use actual exchanges and active state.

## 3. Honor explicit requests for space immediately

Store a clearly expressed contact boundary separately from shadow proposals and the rewritable relationship note. Preserve the person's intended scope, including a topic or audience when specified. Make the active request available for B3 to check before sending an independent follow-up. A request to leave the audition alone allows ordinary conversation about other topics. A later relationship assessment or note rewrite must preserve the request.

## 4. Build the third-party writer boundary

Mind may decide to reply, wait, ignore or privately ask Vincent. For a third-party reply, pass an audience-safe intent to the writer. Construct its input from Jophiel's personality, active relationship context safe for that audience, recent messages from that conversation, and any specific detail Vincent authorized for that recipient and audience. Filter lessons and recall **before retrieval into the writer's context**. Apply the same boundary to the intent itself and any deferred material the writer can read. A DM authorization does not extend to a group. Warmth never grants access to Vincent's information.

Keep the writer off the owner's normal chat and voice paths. Exercise it without dispatch in automated tests. No app preview or new delivery path is needed.

## 5. Persistence and acceptance checks

Reuse B1 identity and label validation. Make each evidence update idempotent under notification replay and atomic with its derived shadow state. Protect concurrent updates from overwriting each other. Preserve evidence provenance for correction and remove relationship-derived records on deletion. A failed lookup must leave existing state intact. Keep incoming notifications ephemeral. Review records retain a short scoped observation and references, excluding unrelated notification text, codes and bank details.

Extend [phone tests](../backend/tests/test_phone_people.py) and [learning tests](../backend/tests/test_learning_lessons.py) where appropriate. Verify through the normal ingestion, learning and retrieval paths as well as the writer input boundary.

| Scenario | Expected result |
|---|---|
| Rina directly asks for one question at a time in a DM | One statement can support a DM lesson proposal. Warmth may remain unchanged. The proposal is absent from live retrieval. |
| A missed call, a message clearly addressed to Vincent, or an ambiguous addressee | A missed call alone supplies insufficient evidence of a preference. A message to Vincent can inform an impression. An ambiguous addressee produces no personal lesson. |
| Rina explicitly includes the band chat in her preference | The proposed behavior can cover the identified group. Its private explanation stays in the DM. Neither proposal influences ordinary replies during shadow mode. |
| Rina requests space about the audition | The scoped request becomes active immediately, survives a note rewrite and leaves unrelated topics available. |
| Same person in a DM and a group | Inspect the writer's actual input using fictional exchanges. The DM-only detail is absent from the group messages, recall, lessons and intent. The writer can use information Rina herself shared in the group. |
| Vincent authorizes a detail for Rina's DM | Only the authorized detail and audience qualify. A group with Rina in it gets no such authorization. |
| Replayed event, concurrent update, lookup failure or deletion | One effect per event, independent changes preserved, no reset to defaults on failure, and no retrievable deleted evidence or derived state. |
| Owner asks how Jophiel feels while a warmth change is proposed | Answer uses actual exchanges and active state. Shadow warmth is available for backend review only. No autonomous message or scheduled wake results. |

Deliver the backend changes, any required migration and focused regression tests. Report the tests run and the backend path used to inspect shadow results.
