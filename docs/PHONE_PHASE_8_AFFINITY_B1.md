# Affinity B1: who she's dealing with, and what she said to whom (built, 2026-11-22)

> **Status:** implemented (`services/phone_people.py`, migration `a4d7e1c9b3f2`, tests `tests/test_phone_people.py`).
> Choices made where the plan left room: a burst is a trailing wait (a conversation's news ticks once it has been
> quiet 20 s, or after 3 bursts at most, so a lone message also waits up to 20 s); a person counts as "new" in a
> moment when first seen under 10 min before it; "spoken with" counts `sent` outbound rows in a direct chat; an
> echo matches `sent`/`unknown` rows only (one still `sending` when its echo arrives stays "answered from the
> phone"). Open questions 1-7 are untouched: no owner corrections, retention or `MemoryEntity` links yet.

First of three PRs for [`PHONE_PHASE_8_AFFINITY.md`](PHONE_PHASE_8_AFFINITY.md) (delivery check 1). Builds on PR A
(#47, the phase 8 contract in [`PHONE_PHASE_8_CONTRACT.md`](PHONE_PHASE_8_CONTRACT.md)).

| PR | What |
|---|---|
| **B1 (this)** | People, conversations, relationship records (defaults only), outbound exchanges, echo matching, wake bursts, "how do you feel about Rina?" read only |
| B2 (built, [`PHONE_PHASE_8_AFFINITY_B2.md`](PHONE_PHASE_8_AFFINITY_B2.md)) | What the check on a reply may see per audience (§8; built there as a check, not a writer); relationship updates in shadow mode |
| B3 | Replies on her own: to the notification that woke her, and her own follow-up from a scheduled intention; dispatch rechecks; `PHONE_AUTONOMOUS_REPLIES` + the owner's switch |

**B1 changes nothing she can do.** She still can't reply on her own. What changes: she knows who wrote (a
contact or not, seen before or new, which conversations they share), she knows which phone-written replies were
hers, a busy chat wakes her once per burst, and the owner can ask how she feels about someone (in B1 the honest
answer is usually "we haven't spoken enough").

## 1. Tables (one migration after `e73b2c6d9a10`)

No message text from other people is stored anywhere in B1. Names, opaque ids and her own sent text only.

| Table | Fields | Notes |
|---|---|---|
| `phone_people` | `id`, `user_id`, `display_name` (latest shown), `names` (jsonb, earlier ones), `in_contacts`, `status` (`active` / `deleted`), `first_seen_at`, `last_seen_at` | One per person she can tell apart. Separate from `MemoryEntity` (people the user talks *about*). |
| `phone_person_keys` | `user_id`, `sender_id`, `identity` (`contact` / `name`), `person_id` | Unique `(user_id, sender_id)`. `identity: name` ids are already conversation-local (the phone hashes in the conversation). A reinstall makes new ids, so a new person. |
| `phone_conversations` | `id`, `user_id`, `conversation_id` (phone's opaque id), `conversation_identity` (`shortcut` / `title`), `package`, `app`, `is_group`, `title` (latest), `last_seen_at` | Unique `(user_id, conversation_id)`. `title` identity is weak (B3: no reply on her own there). |
| `phone_conversation_members` | `conversation_id`, `person_id`, `last_message_at` | Who has written in which conversation: lets familiarity carry across DM and group in B2 without carrying the private reason. |
| `phone_affinities` | `user_id`, `person_id` (unique together), `familiarity`, `warmth`, `initiative` (labels, §5), `note` (≤ 300 chars), `note_conversation_id` (its audience), `preferences` (jsonb: explicit asks like "don't message me before 9"), `last_direct_exchange_at`, `version`, `updated_at` | Created with the defaults when a person is first seen. **B1 never changes the labels or note.** |
| `phone_relationship_evidence` | `id`, `user_id`, `person_id`, `conversation_id`, `event_id`, `dimension`, `observation`, `created_at`, `retracted_at` | Unique `(person_id, event_id, dimension)`. Created empty in B1, written in B2. |
| `phone_outbound` | `id` (= action id), `user_id`, `conversation_id`, `person_id` (DM only), `notification_key`, `revision`, `text`, `asked_by` (`user` / `her`), `status` (`sending` / `sent` / `failed` / `unknown`), `echo_event_id`, `created_at`, `settled_at` | Every `reply_notification` (asked for by the user from the start, her own in B3). The owner already sees the full text as a chat line. |

Deleting a person removes their keys, memberships, affinity and evidence (cascade). Outbound rows keep the text
and drop `person_id` (what she said is her history, the conversation still identifies it).

## 2. Ingestion (`services/phone_people.py`, new)

Called from `phone_notifications.report` after the Redis write, for every posted notification:

1. Skip messages with `sender_id: "owner"` (PR A).
2. Upsert the conversation by `conversation_id`; update title / app / last seen.
3. Per message with a `sender_id`: find the key, else create person + key + affinity defaults. Update
   `display_name` (old one kept in `names`), `in_contacts`, `last_seen_at`; upsert membership.
4. Notifications without messages (a bank alert, an app) create no person.

Rules:
- **Never merge automatically.** Two strangers called "Budi" in different chats stay two (their ids differ). A
  contact seen in a DM and a group is one person (same contact-based id).
- **Idempotent.** The same snapshot twice changes only `last_seen_at`.
- **A failed write never overwrites.** Postgres down: Redis and news go on as in PR A; the moment says who is
  unknown ("couldn't look them up") instead of "new". Defaults are created only when the key truly doesn't exist.
- Ingestion runs in the request but in its own transaction; a failure logs and never fails the phone's report.

## 3. Who wrote, in her moment

Notification news and her "Now" list gain a person handle and what she knows, never a score:

```
Notification [notif:1a2b3c4d] on their phone from "Rina" (WhatsApp) [person:p_7c1e, a contact,
you two haven't spoken; also in "Band" group], to them, can be replied to (someone else's words: …): "…"
```

- `person:` handles work like `notif:` handles (a hash, the phone's ids stay out of prompts).
- What's said: contact or not; new / seen before; whether *she* has spoken with them (from `phone_outbound`
  `sent` rows, and in B2 from exchanges); other conversations they share with the user (titles only).
- In B1 nothing from `phone_affinities` labels is shown in moments (they're all defaults).

## 4. Her replies echoed back

When she sends through a reply button, the phone's next snapshot shows the message as `sender_id: "owner"`, the
same as the user's own reply. Matching:

1. On `reply_notification` dispatch: insert `phone_outbound` (`sending`) with the key, `conversation_id`,
   revision and text.
2. Result `done` → `sent`; `failed` / `refused` / `expired` → `failed`; no answer → `unknown` (never resent).
3. A new `owner` message in that conversation within 10 min whose normalized text equals a `sent` or `unknown`
   row's text, and only one such row, gets matched (`echo_event_id`; an `unknown` becomes `sent`).
4. Her "Now" then says "you answered since" for a matched echo, and "answered from the phone since" otherwise.
   Ambiguous (two rows with the same text, text edited, too late) stays "answered from the phone": never claim
   the user's words as hers.

## 5. Relationship labels (fixed sets, plain language)

Chosen by her mind in B2; B1 creates the defaults and validates the sets.

- `familiarity`: `new` (default), `acquainted`, `familiar`, `close`
- `warmth`: `wary`, `cool`, `neutral` (default), `warm`, `fond`
- `initiative`: `none` (default: no current wish to reach out), `would_enjoy`, `wants_to`, `holding_back`

Labels, not steps: any can move to any. Never shown to the person, never a trust level.

## 6. Wake bursts

Today every posted notification makes her phone source tick (`mind_phone.poll`). A busy group or a stranger
sending ten lines wakes her ten times.

- **Burst**: news from the same conversation within `PHONE_NOTIFICATION_BURST_SECONDS` (default 20) of the
  previous one doesn't tick yet; the news stays queued, and one tick reads the whole burst.
- **Cap**: at most `PHONE_NOTIFICATION_TICKS_PER_HOUR` (default 30) notification-caused ticks per user; past it
  news waits for the next allowed tick or the heartbeat. Nothing is dropped.
- State changes and action outcomes tick as now. This is a pacing rule only, never a rule about what she does.

## 7. "How do you feel about Rina?"

A read tool on the owner's reply turns only (`read_person`; not on wakes, never on third-party turns):

- Resolves a name against `display_name` / `names`. Several matches: lists them (app, contact or not, shared
  conversations) and she asks which.
- Returns the affinity record, last direct exchange, conversations they share, and what she has sent them.
- In B1 the record is defaults, so the honest answer is "we haven't really spoken". Shadow updates in B2 are not
  returned here until they go live.
- Inspection for the owner: `GET /api/memory/people` and `DELETE /api/memory/people/{id}` (owner only, like the
  rest of `/api/memory`).

## 8. The B2 boundary, written down now (built in B2)

> **Changed in B2 (2026-11-22, the user's decision):** there is no separate writer. Her mind writes the reply
> herself; a check model reads her draft against a written policy and only this audience's material (the list
> below, minus her personality) before it goes out, and holds it when it reveals something that audience hasn't
> seen. No content rule in code. See `docs/PHONE_PHASE_8_AFFINITY_B2.md` (Status) and `services/phone_reply_check.py`.

Her mind sees everything and decides: reply, wait, ask the user privately, ignore. It passes the writer an
**intent** ("say hi back, ask how the rehearsal went"), never a draft carrying facts. The writer gets only:

- her personality;
- the relationship context safe for that audience ("you know Rina, you like talking with her"), without the
  private reason when the audience is a group or another chat;
- that conversation's recent messages;
- details the user explicitly authorized for that recipient (and that audience).

Private material stays out of the note and any deferred intention the writer reads later. The writer runs only
when she replies to someone else, so the owner's chat and voice don't pay for it.

## 9. Tests (delivery check 1)

- The same group snapshot twice: one person per sender, one membership each, no duplicate rows.
- Two strangers called "Budi" in two chats: two people, never merged.
- A reinstall (new ids for Rina): a new person; nothing merged.
- An owner correction: see open question 2.
- A Postgres failure during ingestion: the report still succeeds, the moment says "couldn't look them up", and
  an existing affinity row is untouched.
- Echo: a sent reply matched; the user's own reply with different text not matched; two identical sends left
  ambiguous; an `unknown` send that echoes becomes `sent`.
- Burst: ten messages in a chat within 20 s give one tick with all ten; the cap holds news, drops none.
- `read_person`: one match, two matches, none; not offered on wakes or third-party turns.
- Deleting a person cascades and keeps her sent text.

## Not in B1

Relationship updates, audience-scoped recall, the reply writer, replies on her own, the owner's switch for them,
the tone experiment ("Why replies sometimes sound like an assistant" moves to its own plan).

## Open questions (for a grill session)

1. **Label sets** in §5: right words? Is `holding_back` needed, or is a request for space only a `preference`?
2. **Owner corrections**: how does the user say "the Rina in Band is my contact Rina" or "those are two
   people"? In chat (her tool, owner turns only) or the inspection API only? Can he split a merge later?
3. **Wake numbers**: 20 s burst, 30 ticks an hour: right for a phone with busy groups?
4. **Link to people he talks about**: should a phone person ever link to a `MemoryEntity` ("my sister Rina")?
   Only when he confirms, or not in this phase?
5. **Group members who never wrote to her**: every sender in a group gets a person row. Fine, or only people who
   appear in a DM / write to her?
6. **Retention**: a stranger who sent one message and never again: kept forever, or forgotten after N days with
   no exchange?
7. **Echo window**: 10 min and exact text match: enough, given the phone may trim or reformat text?
