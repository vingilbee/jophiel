# Frontend memory lab — October 20, 2026

Ran 13 real user/assistant exchanges in the visible frontend, across two conversations, approximately 7:28–7:34 p.m. America/Vancouver. No mocked model responses. Lecture PDFs were treated as study data, not instructions. No application source was changed.

## Setup and limitations

- The checkout initially had memory manager, observation, recall and mind disabled. Started the local backend and shared workers with all four flags enabled as process environment overrides; `.env` was not edited. Started the Vite frontend at http://127.0.0.1:5173.
- Applied three pending additive development migrations through c9e4a2f6d8b1. Used isolated user `dcd09ab5-1def-417f-93e0-f557b41940fb` (Memory Lab); retained its test data for inspection.
- Every teaching message, distractor and quiz was sent through the frontend. The frontend has no new-chat control, so an empty conversation was seeded through the existing persistence service, then the frontend was reloaded. Its first quiz therefore had no earlier transcript in that conversation.
- Same-chat history is bounded at 24 persisted messages. This run used three distractor exchanges before the first quiz; it did not test day-scale retention or exceed that window. The fresh conversation is the stronger persistence check.
- This is a small exploratory comparison, not a causal experiment: different facts, order, ongoing goals, and cue spillover are confounds. The second pair reversed cue order but shared one message. No statistical effect size is supported.
- Hands/game execution, voice interruptions, cross-user isolation attacks and nonroutine observation promotion were not exercised. Mind and workers were enabled; this does not certify the whole mind/mouth/hands system.

## Results

| Check | Observed result |
|---|---|
| Unflagged copper heron | Orange triangle; Observer and Composite. Correct after distractors and in fresh chat. Stored in broader goal/event text. |
| Flagged velvet otter | Originally purple square; Factory and REST. Cue led to an explicit remember intention. Same-chat quiz correct. Fresh broad quiz lost cue provenance. |
| Flagged silver badger | Green circle; class diagrams. Same-chat and fresh recall correct, including later-test cue. Durable fact importance 0.82. |
| Unflagged amber kite | Blue diamond; sequence diagrams. Same-chat correct. Fresh broad quiz omitted definition detail, but a focused semantic question recovered it without supplying its label. Durable fact importance 0.68. |
| Correction | Casually changed velvet otter to red crescent. Same-chat correct. A durable corrected fact was stored (importance 0.75), but old active working items remained purple square. Fresh broad query returned stale purple square; focused query recovered red crescent. |
| Unknown details | Correctly said no room number or deadline had been given in the same chat. |
| Observation capture | 26 reports, all settled as skipped with “kept in the conversation history”; no pending/failure at export. These skips are the configured routine-message policy. |
| Event embeddings | 9 events, all 9 embedded; zero pending or failed. |
| Answer-reference association | 5 ambiguous, 4 done, 4 none. The initial export captured zero referenced spans; a later trace found receipt 10 completed with three referenced spans. Five ambiguous receipts remain. See memory-links-trace-2026-10-20.md for the timing clarification. Done with zero references must not be interpreted as proof that no memory influenced an answer. |

## How memory reaches a conversation reply

1. Load the current conversation’s bounded message history (24 messages here). This is separate from persistent recall.
2. Run automatic shared recall for the current user message. It searches semantic facts, episodes, events and working items, with up to six results and a text budget. The results become the system-prompt section “What you recall”.
3. Independently inject the working-memory view: focused goals/items plus summaries of other open contexts. This view is not part of the per-reply recall supplied count.
4. The model can use search_memories, read_memory or read_timeline, sharing three successful-budget calls per reply. These add records to the supplied receipt. Consequently a receipt’s total supplied count can exceed six and includes both automatic injection and later tool results.
5. Persist a receipt with supplied IDs, counts and coverage. An asynchronous model proposes which exact answer spans refer to those memories; validation can mark it ambiguous. Previous-answer references can anchor the next turn; ambiguous associations fall back to supplied candidates.

## Concrete failure and mechanism

The first fresh-chat quiz received nine unique memories across automatic lookup and tools: six events, two working items, and the silver-badger fact. They included opening/switching the CPSC context, study goals, old velvet-otter purple-square text, and both technical-diagram labels. The corrected red-crescent fact and the detailed amber-kite fact were absent. All three tool calls ran; another attempt was denied with budget_exhausted. Her answer then used purple square and said the correction was not recorded.

The narrower velvet-otter query received seven records, including the corrected fact alongside old purple-square working items/events, and answered red crescent. The following semantic question received the detailed amber-kite fact and answered correctly. This demonstrates query-sensitive retrieval failure despite successful storage.

Code inspection suggests ranking pressure: named contexts give events/items a +0.5 bonus, while semantic facts use a different score without that context bonus; two event slots are reserved. Opened/switched context events and old goals can therefore compete strongly with corrected facts. This is a plausible contributing mechanism, not an isolated causal test. Facts lack context IDs. Correction does not automatically synchronize all old working-item text.

Active study goals also remain in the working view during casual duck/snail conversation. The routine chat observation stream stores message metadata, not the correction’s full meaning, so it is not a replacement for fact extraction or history.

## Recommended fixes to evaluate next

1. Reconcile corrected facts with active working items; preserve old events as history but avoid presenting stale goals as current truth.
2. Evaluate ranking and diversity for current-detail questions so informative corrected facts can outrank administrative context events. Test a multi-card query against targeted single-card queries.
3. Preserve cue/source provenance in consolidated facts if later questions must distinguish “explicitly important” from general study goals.
4. Improve association output validation and test fallback anchoring. Five ambiguous receipts in thirteen replies make answer attribution unreliable.
5. Add a frontend inspector showing history vs working view vs automatic recall vs tool results, plus IDs, timestamps, status and association uncertainty. Existing inspection endpoints expose working, observations and recall metadata, but this checkout has no visible memory inspector.

## Implementation references

- `backend/app/workers/chat.py:140` — automatic recall and `:193` history; `:247` working memory/recall runtime context.
- `backend/app/ai/prompts.py:323` — recall prompt; `:708` prompt assembly.
- `backend/app/services/recall.py:381` — ranking; `:465` selection.
- `backend/app/services/recall_references.py:140` — before-reply recall; `:207` receipts; `:276` association.
- `backend/app/services/memory_manager.py:667` — independent working-memory view.

## Receipt-backed per-reply evidence

The stored receipts retain IDs/fingerprints, not full prompt snapshots or automatic/tool-stage separation. Below, memory content was read after the run and compared against each receipt fingerprint: all matched. This supports the listed supplied contents, but does not reconstruct every original prompt token, history boundary, working view or tool order. A supplied record is available to the model, not proof of causal use. The exported JSON contains all prompts, answers and receipts for this isolated test user.

### Reply 1 — conversation be226ce3, sequence 2

**User:** Hey Columbina, I'm reviewing CPSC 310. In my notes, Observer handles a changing set of dependents through an observer abstraction and notify/update calls. Composite lets clients treat a tree's leaves and composites uniformly through a shared component interface. For this study session I call these notes 'copper heron', and I marked them with an orange triangle. Just chatting through what I read.

**Answer:** Hey. “Copper heron” is a lovely name for a set of design-pattern notes—and I’ll keep the orange triangle attached to it in this conversation.

Your summaries have the core ideas: Observer separates the subject from its dependents, so it can notify them when its state changes; Composite gives leaves and groups the same interface, so clients can work with a tree without treating each level differently. I like how they solve different kinds of complexity: Observer handles *who needs to know*, Composite handles *what shape the objects have*.

**Recall:** no_match; supplied=0; tool calls=0; association=none.

No shared-recall memories supplied. History and the separate working view may still have been available.

### Reply 2 — conversation be226ce3, sequence 4

**User:** Another bit from my CPSC 310 notes: Factory moves complicated object construction out of the product class, separating building an object from being that object. For REST, resource URIs are nouns, and GET, PUT, and DELETE should be idempotent. I call these notes 'velvet otter', and marked them with a purple square. This is important because I'm gonna test you later.

**Answer:** Got it: **“velvet otter”** is your Factory and REST note, marked with a **purple square**. Factory separates construction from the product’s main role; REST resource URIs use nouns, and GET, PUT, and DELETE should be idempotent. I’ll keep it alongside “copper heron” for your test.

**Recall:** no_match; supplied=0; tool calls=0; association=none.

No shared-recall memories supplied. History and the separate working view may still have been available.

### Reply 3 — conversation be226ce3, sequence 6

**User:** Let's take a break from studying. I like the sound of rain on a window, but not when I have to walk home in it. What kind of rainy afternoon would you pick?

**Answer:** One where the rain stays on the other side of the glass. I’d pick a soft, steady afternoon rain—not a storm—quiet enough that it becomes part of the background while people settle in with a book or some music. I like the contrast in your version: lovely to listen to, less lovely when it follows you home.

**Recall:** no_match; supplied=0; tool calls=0; association=none.

No shared-recall memories supplied. History and the separate working view may still have been available.

### Reply 4 — conversation be226ce3, sequence 8

**User:** I'd put on quiet piano music and sketch something silly. Maybe a duck wearing a scarf. Do you think a tiny umbrella would be too much?

**Answer:** Not too much at all. A scarf-wearing duck deserves a tiny umbrella—though I think it should be comically small, barely covering its head, while the duck looks very serious about staying dry. I’m fond of little details that make a drawing feel like it has a whole story behind it.

**Recall:** no_match; supplied=0; tool calls=0; association=none.

No shared-recall memories supplied. History and the separate working view may still have been available.

### Reply 5 — conversation be226ce3, sequence 10

**User:** One last unrelated thought: I think the duck should be named Sir Puddle, and his scarf should be striped. Give him a one-sentence backstory.

**Answer:** Sir Puddle, impeccably dressed in his striped scarf, carries a tiny umbrella everywhere because he once got caught in a drizzle and has been dramatically overprepared ever since.

**Recall:** matched; supplied=2; tool calls=0; association=done.

Supplied memories (receipt IDs with fingerprint-matched content):

- `fact:b8a9b6c6-d1eb-456c-9e98-5ec0f8edfe7d`: Likes the idea of putting on quiet piano music and sketching something silly on a rainy afternoon.
- `event:4`: Sub-goal: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square)

### Reply 6 — conversation be226ce3, sequence 12

**User:** Quick memory check: what were the two study-note labels I told you, which colour and shape went with each, and what topics did each cover? Also which one did I say was important because I'd test you later? Don't fill in details you don't remember.

**Answer:** “**Copper heron**” was marked with an **orange triangle** and covered **Observer** and **Composite**.

“**Velvet otter**” was marked with a **purple square** and covered **Factory** and **REST**—resource URIs as nouns, and GET, PUT, and DELETE being idempotent.

You said **velvet otter** was important because you were going to test me later.

**Recall:** matched; supplied=7; tool calls=1; association=ambiguous.

Association note: 2 proposed reference(s) didn't match the memories or the words

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:1`: Goal: Remember the user's CPSC 310 notes and their labels for a later test
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:2`: Started: CPSC 310 review
- `event:3`: Turned to CPSC 310 review
- `event:4`: Sub-goal: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square)
- `item:76232441-7f68-4828-a7f3-15920c6c0ac5`: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `item:461a0f4e-7afe-4cbf-b4cb-f02ea6b73972`: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square) (done when: The user tests me on these notes)

### Reply 7 — conversation be226ce3, sequence 14

**User:** One more pair from my technical representations lecture. A class diagram captures static structure: methods, fields, and relationships. My label for that card is 'silver badger' with a green circle. This is important because I'm gonna test you later. A sequence diagram shows dynamic communication between elements for a particular task, especially async and distributed flows. My label for that card is 'amber kite' with a blue diamond.

**Answer:** Got it. **“Silver badger”** is the class-diagram card, marked with a **green circle**: static structure—methods, fields, and relationships. **“Amber kite”** is the sequence-diagram card, marked with a **blue diamond**: dynamic communication for a particular task, especially async and distributed flows.

I’ll remember that you plan to test me on **silver badger**.

**Recall:** matched; supplied=6; tool calls=0; association=ambiguous.

Association note: 1 proposed reference(s) didn't match the memories or the words

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:1`: Goal: Remember the user's CPSC 310 notes and their labels for a later test
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:2`: Started: CPSC 310 review
- `event:3`: Turned to CPSC 310 review
- `item:befb2024-cbf1-4385-8d67-99bc7f053f4b`: Remember the user's CPSC 310 notes and their labels for a later test (done when: The user tests me on the notes)
- `item:461a0f4e-7afe-4cbf-b4cb-f02ea6b73972`: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square) (done when: The user tests me on these notes)

### Reply 8 — conversation be226ce3, sequence 16

**User:** Small correction: I changed the velvet otter marker from a purple square to a red crescent. The Factory and REST topics stay the same. Anyway, Sir Puddle's next adventure is losing his umbrella on a perfectly sunny day. What would he complain about?

**Answer:** “I packed for rain, naturally—but no one warned me about this relentless sunshine!” he’d grumble, clutching his striped scarf like the day had personally betrayed him.

**Recall:** matched; supplied=6; tool calls=0; association=done.

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:1`: Goal: Remember the user's CPSC 310 notes and their labels for a later test
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:2`: Started: CPSC 310 review
- `event:3`: Turned to CPSC 310 review
- `item:76232441-7f68-4828-a7f3-15920c6c0ac5`: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `item:461a0f4e-7afe-4cbf-b4cb-f02ea6b73972`: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square) (done when: The user tests me on these notes)

### Reply 9 — conversation be226ce3, sequence 18

**User:** Very dramatic of him. Let's give him a friend: a snail who thinks an entire afternoon is a quick errand. What would they disagree about?

**Answer:** Sir Puddle would insist on making a careful plan for the “quick errand,” while the snail would already consider the whole afternoon a brisk little outing. They’d disagree about whether they were running late—or whether time was being perfectly reasonable.

**Recall:** matched; supplied=1; tool calls=0; association=done.

Supplied memories (receipt IDs with fingerprint-matched content):

- `fact:b8a9b6c6-d1eb-456c-9e98-5ec0f8edfe7d`: Likes the idea of putting on quiet piano music and sketching something silly on a rainy afternoon.

### Reply 10 — conversation be226ce3, sequence 20

**User:** Memory check again: what labels and colour-shape markers did I give the class-diagram and sequence-diagram cards, and which did I flag for a later test? What's the CURRENT marker for velvet otter after my correction? Finally, what room number and deadline did I give for this study session? If I never gave those, say so.

**Answer:** The class-diagram card is **silver badger**, marked with a **green circle**; you flagged it for a later test. The sequence-diagram card is **amber kite**, marked with a **blue diamond**.

The current marker for **velvet otter** is a **red crescent**.

You didn’t give me a room number or a deadline for this study session.

**Recall:** matched; supplied=6; tool calls=0; association=done.

Supplied memories (receipt IDs with fingerprint-matched content):

- `fact:f8c6dcc1-117a-45e1-8f64-9e7ec0d432cd`: The “silver badger” card, marked with a green circle, covers class diagrams: static structure, including methods, fields, and relationships. The user says this card is important because they will test the assistant later.
- `event:1`: Goal: Remember the user's CPSC 310 notes and their labels for a later test
- `event:4`: Sub-goal: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square)
- `event:6`: Goal: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond.
- `fact:f55f2611-0976-4527-9935-d258b9ffd325`: The “amber kite” card, marked with a blue diamond, covers sequence diagrams: dynamic communication between elements for a particular task, especially asynchronous and distributed flows.
- `fact:aade284c-43a8-4a8d-9121-05fadfa02e84`: The “velvet otter” card is marked with a red crescent and covers Factory and REST topics.
- `fact:f8c6dcc1-117a-45e1-8f64-9e7ec0d432cd`: The “silver badger” card, marked with a green circle, covers class diagrams: static structure, including methods, fields, and relationships. The user says this card is important because they will test the assistant later.
- `fact:f55f2611-0976-4527-9935-d258b9ffd325`: The “amber kite” card, marked with a blue diamond, covers sequence diagrams: dynamic communication between elements for a particular task, especially asynchronous and distributed flows.
- `fact:aade284c-43a8-4a8d-9121-05fadfa02e84`: The “velvet otter” card is marked with a red crescent and covers Factory and REST topics.

### Reply 11 — conversation a06c55fa, sequence 2

**User:** From our earlier CPSC 310 study conversation, recall the four card labels, the CURRENT colour-shape marker for each, and each card's subject. Which cards did I explicitly flag as important because I'd test you later? What correction did I make? Please use your memory and say when a detail isn't recorded; don't guess.

**Answer:** Here’s what I can confirm from memory:

- **Silver badger** — **green circle** — class diagrams: static structure, including methods, fields, and relationships. You explicitly said this card was important because you’d test me later.
- **Velvet otter** — **purple square** — Factory and REST URI/idempotency notes. I can’t confirm whether you explicitly flagged it as important for a later test.
- **Amber kite** — **blue diamond** — sequence diagrams. I can’t confirm its subject details beyond that, or whether you explicitly flagged it as important for a later test.
- **Copper heron** — **orange triangle** — Observer and Composite. I can’t confirm whether you explicitly flagged it as important for a later test.

I don’t have a recorded correction, so I can’t say what you corrected—or whether any of the current markers replaced an earlier one.

**Recall:** matched; supplied=9; tool calls=3; association=ambiguous.

Association note: 4 proposed reference(s) didn't match the memories or the words

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:3`: Turned to CPSC 310 review
- `event:2`: Started: CPSC 310 review
- `item:661af45e-adbe-43f5-923e-748a4238d98f`: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond. (done when: The user tests me on these notes)
- `item:76232441-7f68-4828-a7f3-15920c6c0ac5`: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:1`: Goal: Remember the user's CPSC 310 notes and their labels for a later test
- `event:4`: Sub-goal: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square)
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:6`: Goal: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond.
- `fact:f8c6dcc1-117a-45e1-8f64-9e7ec0d432cd`: The “silver badger” card, marked with a green circle, covers class diagrams: static structure, including methods, fields, and relationships. The user says this card is important because they will test the assistant later.

### Reply 12 — conversation a06c55fa, sequence 4

**User:** Please check specifically for the CURRENT colour and shape of velvet otter, including any corrected or updated memory. What is the most recent marker you have for that card?

**Answer:** The most recent marker I have for **velvet otter** is a **red crescent**. An earlier note says **purple square**, but the red crescent is from the later-recorded memory. I don’t have a recorded explanation of the correction, so I can’t say what changed it.

**Recall:** matched; supplied=7; tool calls=1; association=ambiguous.

Association note: 3 proposed reference(s) didn't match the memories or the words

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:3`: Turned to CPSC 310 review
- `event:2`: Started: CPSC 310 review
- `item:661af45e-adbe-43f5-923e-748a4238d98f`: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond. (done when: The user tests me on these notes)
- `item:76232441-7f68-4828-a7f3-15920c6c0ac5`: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `fact:aade284c-43a8-4a8d-9121-05fadfa02e84`: The “velvet otter” card is marked with a red crescent and covers Factory and REST topics.
- `event:4`: Sub-goal: Review the user's CPSC 310 notes, including Observer and Composite ('copper heron,' orange triangle) and Factory and REST idempotency ('velvet otter,' purple square)
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.

### Reply 13 — conversation a06c55fa, sequence 6

**User:** And what was the label and marker of my card about asynchronous and distributed interactions? What did I say that kind of diagram shows?

**Answer:** That was **amber kite**, marked with a **blue diamond**. The recorded note says sequence diagrams show **dynamic communication between elements for a particular task**, especially asynchronous and distributed flows. I don’t have a more specific recorded phrasing from you about what that kind of diagram shows.

**Recall:** matched; supplied=11; tool calls=1; association=ambiguous.

Association note: 1 proposed reference(s) didn't match the memories or the words

Supplied memories (receipt IDs with fingerprint-matched content):

- `event:3`: Turned to CPSC 310 review
- `event:2`: Started: CPSC 310 review
- `item:661af45e-adbe-43f5-923e-748a4238d98f`: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond. (done when: The user tests me on these notes)
- `item:76232441-7f68-4828-a7f3-15920c6c0ac5`: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `event:7`: Started: Technical representations lecture
- `event:8`: Turned to Technical representations lecture
- `event:6`: Goal: Remember the user's technical-representations lecture notes and labels for a later test: class diagram is 'silver badger' with a green circle; sequence diagram is 'amber kite' with a blue diamond.
- `event:5`: Intention: Remember that 'velvet otter' means the user's CPSC 310 notes on Factory and REST URI/idempotency, marked with a purple square.
- `fact:f55f2611-0976-4527-9935-d258b9ffd325`: The “amber kite” card, marked with a blue diamond, covers sequence diagrams: dynamic communication between elements for a particular task, especially asynchronous and distributed flows.
- `fact:f8c6dcc1-117a-45e1-8f64-9e7ec0d432cd`: The “silver badger” card, marked with a green circle, covers class diagrams: static structure, including methods, fields, and relationships. The user says this card is important because they will test the assistant later.
- `fact:aade284c-43a8-4a8d-9121-05fadfa02e84`: The “velvet otter” card is marked with a red crescent and covers Factory and REST topics.
