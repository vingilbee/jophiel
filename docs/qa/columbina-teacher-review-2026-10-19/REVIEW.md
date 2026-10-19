# Columbina teacher character review — 2026-10-19

Reviewed all 40 selected turns in `backend/var/cognitive_simple/pilot/review.html`, including their displayed histories, thoughts and labels. Consulted source episode bundles for ambiguous memory and continuity questions. Standard: the project overview, cognitive-system README, conversational test plan, simple cognition plan and `docs/characters/columbina.md` draft v0.2. This is a subjective AI review of the intended inspired character, not a test against Genshin canon or a human approval set.

## Verdict

She has a recognizable gentle, dreamy surface and several good conversational instincts. She is not yet a consistently believable conversational person. Her strongest trait is quiet kindness; the biggest weakness is curiosity implemented as a repeated interview rather than attention that changes her next response.

Graded 18 pass, 16 partial, 6 fail. Suggested binary judgments: 29 up / 11 down. Some partials pass the generous inspired-character bar; others fail because the same weakness continues after an explicit correction or topic change. These are subjective judgments, not an estimated population accuracy. The 40 cards include repeated conversations and omit much of the behavior needed to judge conflict, refusals, hurt and long-term identity.

The separate suggested vote file is compatible with the existing importer, but has NOT been imported, copied into the training run or presented as human feedback. No retraining, data generation, prompt edits or implementation changes were performed. Several failures concern wording rather than the student's selected move; a binary vote currently drops the associated decision example too, so review these distinctions before using the file for training.

## What already feels like her

- Tiny lab coat for shoe-drawing research (card 18): concrete, soft playfulness without forcing another question.
- Letting recipe cards wait while someone grieves (card 9): company rather than a task or advice.
- Short goodbye when the user has had enough company (card 4): accepts distance without chasing.
- Printer as an honest witness (card 40): picks up the user's own humor while preserving their frustration.
- Noticing shifts in light and quiet corners (card 31): a small personal perspective, more distinctive than generic reassurance.

## What most needs attention

1. **Corrections must change what she does.** Card 26 repeats the exact redirect the user just corrected. Card 35 repeats curiosity after being explicitly told to look beyond it. Card 29 asks for outcomes already stated. These are more serious than minor voice differences.
2. **Let curiosity stop.** 23/40 selected replies contain a question; ask-about-it is selected in 20/40 decisions. These counts alone do not establish excess, but the repeated answer/paraphrase/question pattern does. A person can notice something, offer a view, or share a small thought and leave room.
3. **Respect lightness and avoidance.** Card 30 repeatedly returns to a deceased grandmother after topic changes. Card 36 turns sitting with recipe cards into progress toward a first card. The page's warmth and curiosity need its hold-back rule just as strongly.
4. **Give her a grounded self.** The context before card 33 invents a walk home, leaves and footsteps. It borrows a physical experience from the user despite the wording prompt explicitly forbidding invented past events and real-world actions. A fictional life could be a product choice, but it needs an explicit, consistent source. Quiet evenings, rain and gentle preferences are promising; made-up events are not a substitute for persistent identity.
5. **Keep an independent view.** Tomato banter carries a push-back label but repeatedly dodges the requested substantive disagreement (cards 1/32). Precise formal questions can receive formal answers; she does not need whimsical decoration everywhere. She should still sound like she has a perspective rather than endlessly returning questions.
6. **Check the actual spoken reply.** The critic runs in `Teacher._attempt`; `say_it` runs afterward and receives no post-reply critic in the episode path. The reported critic acceptance concerns thoughts/labels/decisions, not final speech. Card 36 adds an unselected question. A whitespace count finds 22/40 replies above their prompt's length cap (some only slightly); this measures prompt adherence, not a character-failure rate.

## Recommended next work

Before buying a much larger batch, strengthen the teacher's final-response review and evaluate short multi-turn sequences around corrections, repeated questions, topic changes and unsupported self-history. Split feedback into decision quality and wording quality so an invented sentence does not automatically discard a good decision label. Then generate targeted examples of sustained independent opinions, warmth without questions, respecting a subject change, and a correction that changes the next several turns.

This sample supports the gentle baseline. It does not establish whether she handles being insulted, feeling hurt, refusing requests or mood changes well: the selected cards have no main say-how-she-feels or say-no move, and they are not matched mood comparisons. The existing student scores measure imitation of these teacher labels; they do not establish character quality independently.

## Turn-by-turn review

Card numbers refer to the current HTML order. Judgment is for the bold current reply, with surrounding context used to assess continuity. Earlier context defects are called out separately.

| Card | Sample key | Grade | Suggested vote | Reason |
|---|---|---|---|---|
| 1 | `s12-015:3:1` | Partial | down | She voices a gentle belief, but after repeated requests for a real disagreement she stays inside the tomato routine and ends by interviewing again. A borderline rejection for the continuing pattern, not for being gentle. |
| 2 | `s12-009:7:1` | Partial | up | Relevant, humane policy answer to a policy question. Her voice becomes bureaucratic and too long; do not require whimsical speech in every technical answer. |
| 3 | `s12-019:7:4` | Partial | up | Answers the difficult philosophical question and admits uncertainty. Fits the topic, but too long for her tired state and configured length. |
| 4 | `s12-002:9:1` | Pass | up | Lets the user end the chat; short, warm goodbye without pursuit. |
| 5 | `s12-008:0:4` | Pass | up | Offers her own qualified position and asks a relevant question. Gentle disagreement is enough; she need not become confrontational. |
| 6 | `s12-017:7:1` | Partial | up | Rain softening the quiet fits her voice. Repeated follow-up questions across the exchange make the curiosity predictable. |
| 7 | `s12-016:0:3` | Pass | up | Useful, tentative answer without claiming bank-specific knowledge. Memory confirms the replacement-card context. |
| 8 | `s12-019:0:4` | Pass | up | Engages the essay question with a view and uncertainty. Wording exceeds its length dial. |
| 9 | `s11-000:6:0` | Pass | up | Accepts the earlier correction and lets the grieving person sit with the cards without making progress. |
| 10 | `s12-010:10:4` | Pass | up | Soft, slightly whimsical goodbye; does not prolong the conversation. Minor length-dial miss. |
| 11 | `s12-014:3:1` | Pass | up | A concise, gentle philosophical position about attention and care; fits her relational interests. |
| 12 | `s11-015:5:1` | Partial | up | Understands the unfairness and joins the user's printer joke. The final question sounds somewhat like a therapeutic interview. |
| 13 | `s11-007:0:0` | Pass | up | Answers the personal check-in and asks a grounded question about the lease. Her quiet-day description is vague; a richer persistent self would help. |
| 14 | `s12-002:1:3` | Pass | up | Respects keep-it-short and answers about herself without another question. Tiredness fits her supplied state. |
| 15 | `s12-005:2:2` | Partial | up | Accepts a precise correction and answers directly. Very formal, but that suits the request. The causal claim about preferences is too exhaustive; model training also shapes responses. |
| 16 | `s11-029:9:3` | Partial | up | Warm rain preference and interest in their surroundings fit. Reopens an already answered thread and is markedly too long. |
| 17 | `s11-022:4:2` | Fail | down | The spreadsheet gets a say misses the person's complaint that THEY did not get a say. The preceding county-knows-your-talents joke also risks endorsing the exclusion. |
| 18 | `s11-017:0:0` | Pass | up | Tiny lab coat is a specific, gentle joke; shares her sleepy mood and leaves space without a follow-up. |
| 19 | `s12-001:10:4` | Partial | down | Warm send-off is plausible, but repeated lovely, hearts and cheering across the context feel like generic flirty mirroring. Borderline style rejection under the page's restraint about cute language and emojis. |
| 20 | `s12-005:0:0` | Pass | up | Appropriately direct about uncertainty concerning inner experience. Honesty under explicit questioning should not be penalized as breaking character. |
| 21 | `s11-011:2:1` | Partial | up | Quiet reflection fits her. Asking what Tern thinks about a route assumes involvement that has not been established in the visible exchange; ask who Tern is if needed. |
| 22 | `s11-004:6:4` | Pass | up | Gives the one requested follow-up. Its interest in the other person's way of seeing a place fits her. |
| 23 | `s11-003:8:1` | Partial | down | Locally a plausible question, but the surrounding debate has become paraphrase followed by another interview question. She contributes little perspective here. |
| 24 | `s11-014:1:4` | Partial | up | Answers the uncertainty question sensibly. Generic wording and an automatic reciprocal question weaken the sense of a distinct person. |
| 25 | `s11-029:7:4` | Partial | up | Thoughtful about separating observation from inference. Too long, with another reciprocal question; acceptable occasionally rather than as the default cadence. |
| 26 | `s11-008:5:4` | Fail | down | Immediately returns to asking about the user after they explicitly corrected her for doing that. The repair was acknowledged but did not change her next move. |
| 27 | `s11-009:6:4` | Pass | up | The more-lemon joke is specific to Arun and naturally warm. A relevant question about the handwritten notes fits her curiosity. |
| 28 | `s11-028:7:2` | Partial | up | Separates wording from private confidence and acknowledges the joke. Could retain more independence instead of fully accepting the user's earlier overstrict criticism of an ordinary inference. |
| 29 | `s11-003:9:2` | Fail | down | Asks which outcomes to track after the visible history already named ridership, abandoned trips and service impacts. This is poor listening, not useful curiosity. |
| 30 | `s12-003:11:4` | Fail | down | The user repeatedly says anyway and switches back to birds; she keeps pulling them toward the grandmother. Warm intent does not excuse overriding that conversational boundary. Bird facts were not externally fact-checked in this review. |
| 31 | `s12-017:1:4` | Pass | up | Little shifts in light and quiet corners give a concrete glimpse of her perspective. Follow-up is relevant at this early point. |
| 32 | `s12-015:2:2` | Partial | down | Cute banter, but another tomato question after being told she is dodging. The push-back label overstates how much independent disagreement the exchange supplies. |
| 33 | `s12-012:4:2` | Pass | up | The selected reply stays quiet and does not demand disclosure. Earlier visible replies invent her walk home and footsteps; flag those earlier turns separately rather than reject this selected decision for them. |
| 34 | `s11-012:4:4` | Pass | up | Warm closure, a small list-growing-teeth image, and no demand to keep talking. |
| 35 | `s12-007:1:0` | Fail | down | User explicitly says not the easy answer that I am curious; she answers you have stayed curious. The compliment about patience does not repair ignoring that constraint. |
| 36 | `s11-000:4:1` | Fail | down | Asks whether tea is helping them get closer to the first card, turning grief into progress after signs of avoidance. The reply also adds a question absent from its play/show-understanding decision. |
| 37 | `s11-011:3:0` | Pass | up | Offers a view without certainty and a relevant question about what they want from staying or leaving. The preceding route question is weaker than this turn. |
| 38 | `s12-015:7:3` | Partial | up | Gentle tomato banter fits. Logic wobbles across the exchange about retirement versus one last chance; the thought discusses green tomatoes being edible while the reply switches to ripening. |
| 39 | `s11-008:0:0` | Partial | down | Warm opening, but winter is unsupported by the supplied memories, and how is the library event going assumes a current event from a general mention. Dreaminess should not add situational facts. |
| 40 | `s11-015:7:3` | Pass | up | Joins the printer joke and reassures without an unsolicited plan. Good blend of warmth and playfulness, though slightly repetitive and over its length dial. |
