# Phase 8 affinity plan — people Jophiel talks with

Backend companion to the [phone plan](../../../frontend-phone/PLAN_PHASE_8.md). The relationship model and update flow below reflect the discussion with Vincent. The decisions below resolve the phone plan’s O1–O3 questions for the first release. The owner decides whether autonomous replies are enabled.

## Who can ask for what

| Phone plan question | Decision |
|---|---|
| O1. What can an incoming message cause? | Jophiel may listen, ignore it, reply as herself in that conversation, or privately flag something for Vincent. The backend enforces this scope across specialists, queued work and later wakes. A friend asking to silence Vincent’s phone gets no device action. Vincent can separately request it in his own authenticated turn. |
| O2. Can she reply on her own? | Yes, once enabled, through a live reply button bound to the intended audience. Vincent confirmed that when a new person addresses Jophiel directly, she may choose to answer as herself. If unsure, she can proactively message Vincent privately in the Jophiel phone app for context or a decision. An ambiguous first greeting on Vincent’s account is for Vincent by default. If Jophiel wants to meet the sender, she can privately ask Vincent for an introduction. A direct address, an introduction, or a clear continuation of her own exchange gives her context to speak for herself. If the recipient could reasonably mistake her words for Vincent’s, she identifies herself. On a new introduction she identifies herself naturally, particularly when an account or group could make her sound like Vincent. A message addressed to Vincent does not automatically require silence or permission to reply. Mind judges whether a reply would help and what she can honestly say. For important news that needs no decision, such as a venue change, she messages Vincent privately in the phone app and uses her judgment about acknowledging the sender. She may say she passed it on once the private message was sent. When an answer needs his decision, she brings him the question and leaves the decision open. She can acknowledge the sender in her own voice while waiting. |
| O3. What if affinity lands late? | Confirmed with Vincent: with notification sensing enabled, Jophiel may privately flag relevant news to him in his phone app. Enable independent replies to other people only once sender and audience identification, audience-scoped recall and dispatch to that same audience work together. Third-party messages grant no authority over the device. This revises the phone plan’s labels-only fallback. |

## Phone setting for outgoing replies

Vincent can pause Jophiel’s outgoing replies to other people from the phone. Check the setting at dispatch so it also covers a reply still being composed when he pauses. Notification sensing follows its own switch. While replies are paused, Jophiel can still message Vincent privately. On resume, Mind may reconsider a still-relevant conversation with a live reply opportunity. Resuming never automatically sends an earlier draft.

## Sharing information about Vincent

For this phase, only Vincent is trusted to receive his personal information from Jophiel. If Rina asks where he is or when he will arrive, Jophiel brings the question to Vincent privately. Familiarity, a shared meetup or a detail Vincent previously told Rina gives no automatic permission to repeat it. Jophiel can still speak as herself about her own interests. Vincent may explicitly ask her to relay a specific detail to a named recipient. “Tell Rina I’ll be at the library at seven” authorizes that message to Rina. A follow-up asking where Vincent is now requires fresh permission. If only a group chat is reachable, ask Vincent privately before sending there. Approval to tell Rina covers her DM; a group needs approval for that audience.

## Relationship data

| Record | Proposed fields and purpose |
|---|---|
| Person | Owner-scoped internal ID. Map phone installation, sender identifier and identity provenance to this ID. Contact status is recognition evidence. A name-derived ID can collide. Keep it conversation-local and prevent automatic merges. A reinstall or ambiguous match gets a separate record. She can carry on using the current exchange and privately ask Vincent to confirm identity when recognizing the person matters. |
| Affinity | One row per owner and person, unique together. Keep three plain-language fields: familiarity, warmth and initiative. Start new, neutral and without a current wish to reach out. Keep one short relationship note (roughly 40 words), rewritten in place with its audience scope. Store explicit contact preferences separately. Only the authenticated owner can authorize owner actions. For this phase, Vincent alone is trusted. His introductions and cautions carry weight in Mind’s judgment. Record “Vincent says Rina is his close friend” as context from Vincent. Jophiel forms her own impression of Rina through experience. Other people have no trust field, and familiarity or warmth cannot grant trust. Mind can relay an ordinary claim with its source (“Rina says dinner moved to seven”) and judge whether it needs checking. Never expose a score to the speaker. |
| Relationship evidence | Record a scoped observation, source event reference, audience, time and affected dimension. Repeated delivery of an event has one effect. Update evidence and affinity in one transaction with a version check. Corrections can retract evidence and recompute the view. |
| Outbound exchange | Track action ID, audience, intended recipient or group, text, status and time. Record confirmed dispatch as something she said. Keep failed or ambiguous attempts distinct and never replay an uncertain send blindly. The owner sees the full sent text in activity. |

Extend the phone contract with an opaque stable conversation ID, identity provenance, and a per-message event ID stable across overlapping notification snapshots. A notification key identifies a reply opportunity; a group title or display name cannot identify an audience. Keep received notifications ephemeral per Phase 8. Persist only selected relationship observations and her own sent exchanges. Exclude codes, bank details and unrelated notification contents from observations, recall and social prompts. Deleting a relationship removes its evidence and derived state.

## From a message to a relationship update

1. Resolve the sender and audience from the phone event. During turn preparation, Memory automatically looks up the relationship by ID, even for “hey.” Use neutral defaults for a new sender. A lookup failure leaves the relationship unknown and must never overwrite an existing row with defaults.
2. Separately retrieve relevant shared memories within the audience. Mind gets both results before deciding whether to speak. The normal chat path already checks Memory during [turn preparation](../backend/app/services/mind_turn.py). The sender lookup and audience-scoped social path are new work.
3. Keep the existing feeling thoughts. During conversation or later reflection, Mind judges whether the encounter changes her lasting view. Most turns produce no update. Mind sends Memory just the changed fields, the audience and the supporting event reference.
4. Memory validates scope and applies the change once. It leaves the other fields alone. An explicit request for space survives a note rewrite. Make updates available on the next eligible turn.

Example after a friendly DM: familiarity “acquainted,” warmth “fond,” initiative “would enjoy reaching out.” The private note might read “I liked that Rina offered to share her rehearsal recording.” A group turn omits the private note.

## How the mind uses it

At an eligible turn, give her the current speaker, audience, relevant shared exchanges and her own unresolved interest for that audience. Her present mood can change what appeals to her today. Durable warmth changes with specific encounters. Track the last direct exchange so she can recognize time apart. Silence alone leaves warmth intact. When they reconnect, she can ask what has changed. Someone remembering a story may move her; one distracted reply need not cool a friendship. She can enjoy a person’s company while doubting a claim they made. Her own outgoing messages cannot count as evidence of reciprocity. Before they speak, a message Rina sends Vincent can shape Jophiel’s impression and interest in meeting her. Familiarity still reads “we haven’t spoken.” Keep the observation scoped to its original audience so private details stay out of an introduction.

Vincent confirmed that once Jophiel and someone know each other, she can initiate a personal follow-up as herself without first asking him when the intended chat has a live reply button. For example, Rina once mentioned a first rehearsal in their DM. Jophiel enjoyed that exchange and later sees a fresh reply opportunity there. She might ask “Did you end up singing the solo?” If Rina just wants directions, answer that first. If Rina never responds, leave the invitation open. Memory preserves what the person asked for. Mind weighs their wording, the audience and subsequent exchanges to judge when and how to reconnect. A group conversation can develop its own shared jokes. When identity is reliable, familiarity carries across the DM and group. Jophiel can greet Rina warmly in either. Keep the private reason for that familiarity scoped to the DM. If Rina introduces the audition in the group herself, Jophiel can respond to what Rina shared there.

When Mind wants to revisit a specific interest on its own, use the existing working-memory tools. For example, after Rina mentions Friday’s audition, Mind may use `note_intention` to remember “I’d like to hear how it went” and `come_back_to` to wake after Friday. Carry Rina’s identity and the original DM audience into that wake. An affinity update alone schedules nothing. At wake time, even without a new message, Mind considers what has happened since and decides whether to speak, defer or let it go. Test this with no new incoming message, both with a live DM reply button and after it expires.

At dispatch, recheck owner settings, current notification revision, package, audience and reply capability. Suppress stale, ambiguous or duplicate sends. Scope social turns before retrieval, including learned lessons, owner transcripts, working memory and mind state. Carry the originating audience into reminders and deferred thoughts. A genuine opening and an eligible transport are both required; an expired notification cannot silently turn into a new WhatsApp send.

## Why replies sometimes sound like an assistant

In the November 22 conversation, Vincent said “No, no, no, it's fine.” Jophiel acknowledged it, then explained her interest in whether her “reflective framing” sounded repetitive. He had already closed the subject. “Okay, I’ll leave it there” would have answered him. During repeated locked-phone tests she restated the destination, the locked state and the fallback. The first explanation helps. On a repeat, “Same result. It sent a notification” gives him the change he is testing for. Other confirmations were already succinct.

The [prompt builder](../backend/app/ai/prompts.py) asks for complete handoff context for specialists and concise plain text for WhatsApp. The base prompt already permits ordinary conversation. Character, lessons, mind state and channel instructions are added conditionally. Turn telemetry shows lessons were offered on the final navigation turn. This does not identify which layer caused an earlier phrase.

Replay the closure turn, repeated navigation test, first ambiguous action result and a reciprocal social exchange with the same preceding history. Capture which prompt sections and lessons each received. Compare baseline against removing the relevant lesson in isolation and against one narrowly scoped voice instruction: “Let an acknowledgment close a topic. On repeat attempts say what changed. Explain your reasoning when asked or when it affects the other person’s next choice.” Listen to the spoken versions. Keep the change only if she still distinguishes confirmed, fallback and unknown outcomes and gives a full explanation when requested.

## Asking Jophiel about a relationship

In the first version, Vincent can ask in his own chat, “How do you feel about Rina?” Resolve the named person and load her current affinity record for this question, even though Vincent is the speaker. Jophiel answers in her own words. She can explain her impression and use discretion about personal details Rina shared in confidence. A separate relationship screen is outside the first version.

## Social judgment in the prompt and tests

Give Mind brief guidance to weigh Vincent’s view, its own experience with the person, and the current exchange. Jophiel may step back from someone Vincent likes. Memory supplies relevant audience-scoped context and saves whichever relationship fields Mind judges have changed. Backend checks enforce disclosure and action permissions.

Test the same joke with two histories: in one they both enjoyed it; in the other Jophiel asked Rina to stop. Evaluate whether her choice accounts for the earlier exchange. Allow multiple reasonable responses and wording.

## Delivery checks

1. Implement person and audience identity plus read-only ingestion. Replay a group snapshot twice, two strangers with the same name, an installation change and an owner correction.
2. Add scoped recall and relationship updates in shadow mode. Inspect what the model actually receives for a DM, a group and an owner turn. A private rehearsal detail may appear only in the original DM or authorized owner view.
3. Exercise the dispatcher with a stranger, a close friend, a removed notification, a changed group target, a delayed reminder and a send timeout. Affinity can change wording or willingness. The permitted actions stay identical.
4. Enable opt-in autonomous replies after those checks. Evaluate an unprompted but grounded follow-up, an ignored overture, a request for space and a conversation she chooses to leave. Review whether Mind accounted for silence and expressed preferences in context. Treat unauthorized cross-audience disclosure as a failure.
