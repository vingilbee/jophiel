# Continuity v1.4 end-to-end shadow evaluation — 2026-10-10

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Follow-up to `COGNITIVE_CONTINUITY_REFERENCE_CORRECTION_DIAGNOSTIC_2026-10-10.md`.
It evaluates the uncommitted `argument-kind-registry-v1.4` work: extractive
dialogue citations (`two_call/continuity.py`), `dialogue_recall_request`,
`contextual_perspective_request` and `ContinuityRequirementEngine`.

## Bottom line

v1.4 fixes the **recall** half of the earlier diagnostic. That holds on new wording too, not just the original fixture.

- Recalling a topic, an option or a corrected value now works: 7/7 recall questions answered correctly, including recall after a digression and after two successive corrections.
- Ambiguous questions and questions about facts the user never stated are now typed differently inside the pipeline.

The response still mostly fails to **advance the conversation**:

- 23 of 56 baseline-core turns (41%) produced an acknowledge-only plan, which renders as "I hear you."
- Reference resolution outside a recall question, confirming a correction, answering a follow-up whose referent is obvious, giving a take, and accepting an invitation to talk all fail.

The causes are structural, not wording noise:

- Plain statements only offer `acknowledge`.
- An unresolved `it`/`that` always becomes a clarification.
- The knowledge gap type is lost at Realization.
- No action exists for "give a stance" or "go ahead, I'm listening".

On personality, affinity and state:

- **Personality** produces real, appropriate differences in *action selection* (reserved vs. playful), but almost none in wording, and playfulness never activates.
- **Affinity** has no live input. When injected in-process it reaches both calls but barely changes behavior.
- **Dynamic state** does not track the user's emotional situation at all, and its derived controls never vary, so state cannot produce behavioral differences yet.

## Method

The real application path ran against an isolated database. There were no dev DB writes and no live delivery.

```text
WebSocket /api/chat/ws (port 8011) -> chat worker -> visible reply
  -> shadow capture v0.3 (dialogue memory on) -> shadow worker
  -> Call 1 (gpt-6-luna) -> deterministic middle (v1.4 continuity engine)
  -> Call 2 -> plan validation -> Realization (gpt-6-luna) -> persisted trace
```

- DB: fresh `columbina_shadow_eval_1010` in the `nutrition-qa-postgres` container, migrated to head. Redis: QA container, db 7.
- Overrides: `COGNITIVE_SHADOW_TWO_CALL_PROTOCOL=cognitive-two-call-v1` (the local `.env` still selects `context-conditioned-two-call-v0.1`, **so v1.4 is not what the dev stack runs today**), `DESKTOP_AGENT_ENABLED=false`.
- Core memory: identical to the dev DB's `columbina` v1 (checksum-verified). Two variant cores were added to the QA DB only for the personality test.
- Each turn waited for its shadow trace before the next, so the predecessor chain is intact. The visible assistant ran normally and serves as the control.
- **Overfitting guard:** the v1.4 predicate descriptions use phrases from the morning fixtures ("what did I mention", "given that correction", "what makes this difficult"). The 4 fixture conversations (D_*) were therefore rerun **plus** 6 new conversations (H_*) that probe the same skills with different wording.
- Each turn ran once. Call 1 is nondeterministic: H_refs3 failed in two *different* ways in a smoke run and in the main run (see §2). Treat single results as indicative.

Totals: 40 basics turns, 9 state turns, 21 personality turns (3 cores × 7), 12 in-process affinity turns.

| Measure (40 basics + 9 state turns) | Result |
|---|---:|
| Pipeline completed | 49 / 49 |
| Realization accepted | 48 / 49 (1 `output_schema_invalid` ×2 attempts) |
| Shadow pipeline wall, median / p95 | 4.8 s / 6.6 s |
| Realization, median / p95 | 1.5 s / 2.8 s |
| Shadow total, median / p95 / max | 6.4 s / 9.4 s / 10.1 s |

The shadow total is faster than the morning run (8.3 s median). That is not a like-for-like latency comparison.

## 1. Scorecard

| Capability | Morning diagnostic | Now | Notes |
|---|---|---|---|
| Remember the topic | Fail | **Pass** (2/2) | Option recall; topic return after a cello digression |
| Recall after correction | Fail | **Pass** (3/3) | Including a double correction (Thu → Fri → Mon 10) |
| Resolve references outside recall questions | Fail | **Fail** (0/7) | "the first one", "the loud one", "the other one", "it/that" follow-ups |
| Two-hop reference ("which cat did that?") | — | **Fail** | Cited "loud one", not "Tofu" |
| Confirm a correction on the correction turn | Partial | **Fail** (1/4) | Three of four were "I hear you." |
| Detect ambiguity vs. a missing fact (internal) | Fail | **Pass** (5/6) | H_ambig4 misclassified as ambiguous |
| Express that difference to the user | Fail | **Fail** | Missing facts say "I don't know X", the same as external knowledge; ambiguity clarifications are garbled |
| Clear follow-up with an obvious referent | Fail | **Fail** (0/3) | Always "What are you referring to?" |
| External knowledge questions | Fail (by policy) | Fail (by policy) | No trusted knowledge producer exists |
| Advance the conversation | Weak | **Weak** | 41% acknowledge-only. Experience reports advance (reflect + explore); statements, invitations and opinion requests don't |

## 2. Per-failure localization

Evidence comes from the persisted `pipeline_trace_payload` and `realization_trace_payload`. Turn ids such as `H_refs3` refer to the corpus below.

### F1. Plain statements only offer `acknowledge` (biggest advancement failure)

Every turn where Call 1 emitted only `statement` events had exactly one eligible candidate: `give_feedback.acknowledge`. Examples: "My sister just moved to Lisbon…", "I have two cats, Miso and Tofu…", "My dentist appointment is on Thursday at 3", "I finally finished the book".

- The visible assistant engaged on each of these.
- The shadow said "I hear you." or a restatement.
- **Where meaning is lost:** action construction. No recipe binds a `statement` frame to an engaging move such as comment, react, follow-up question or connect to earlier content. Call 2 cannot pick what it isn't offered.
- This also hides correct understanding: in the smoke run, Call 1 resolved "the other one" to `entity:Miso`, and it still produced "I hear you."

### F2. Correction understood, not confirmed

D_options4, H_correct2 and D_ambig3:

- Call 1 emitted a correct `correction` frame (`prior_value=Thursday`, `corrected_value=Friday`) plus a `claim_dialogue_conflict`.
- The eligible list was two `acknowledge` candidates, one per frame. The final text was "I hear you."
- **Lost at:** action construction. There is no "acknowledge the repair, restating the new value" move, and the realization content carried only a `user_contribution` reference, not the corrected value.
- The correction *is* honored on later recall, because Call 1 cites the latest passage. This depends on the model choosing the newest span each time: there is still no supersession in dialogue memory.

### F3. Reference resolution only exists inside recall questions

- **H_refs3, "Which cat did that?"** The answer needs two hops ("loud one" → Tofu).
  - Main run: Call 1 returned `known` and cited `"loud one"`. Content = `["loud one"]`. Output: "You said 'loud one,' but I don't know which cat did that." **Lost at:** the Call 1 citation. A single extractive span cannot express a chained identity, and nothing checks that the cited span actually answers a "which X" question.
  - Smoke run (same turn): Call 1 again returned `known`, but bound `referent_phrase` to the deictic "that". The compiled value was null, and the deterministic `that` cue found two candidates, so `referent_ambiguous` blocked the turn and the output was "What are you referring to?". **Lost at:** the requirement engine. `ContinuityRequirementEngine` only special-cases `ambiguous_reference`, so for a `known` recall the inherited cue logic can still block a question the model already answered.
- **"The first one" / "the loud one" / "the other one" in statements:** never resolved into anything the response uses. That follows from F1, since an acknowledge needs no resolution.

### F4. Follow-ups with an obvious referent always clarify

Turns: D_know2 "How long does it take?", H_know2 "Is that also why sunsets look orange?", H_know3 "What about it at night though?".

- Call 1 emitted `elaboration_request` with `referent_phrase=null`, plus an `ambiguous_reference` unknown.
- `preserve_accepted_uncertainty` turns that into a blocking `referent_unresolved`, and the plan becomes `clarify`.
- The visible assistant resolved all three from the previous turn without trouble.
- **Lost at:** Call 1. `elaboration_request` gets no `dialogue_span` option for its referent, unlike recall, so the model can only say null. The v1.4 rule "accepted null wins" then makes this a guaranteed clarification.
- Separately, **the clarify→answer loop dead-ends** for knowledge questions. After the user clarified ("from high tide to low tide"), Call 1 bound the referent to "causes tides on Earth" from message 0, the query went to `dialogue.delivered_claim`, and the result was unavailable: "I don't know…".

### F5. The missing-fact vs. external-knowledge distinction is lost at Realization

| Turn | Typed content | Wording |
|---|---|---|
| H_ambig3 "What company was the job at?" | `knowledge_gap / no_record` | "I don't know which company the job was at." |
| D_ambig4 "Who owns the novel?" | `knowledge_gap / no_record` | "I don't know who owns the novel." |
| H_know1 "Why is the sky blue?" | `knowledge_gap / no_grounded_answer` | "I don't know why the sky is blue." |

- `v1_content.conversational_content` correctly rewrites the gap kind to `no_record`.
- The realized text is still the same template. The user can't tell "you didn't say" from "I can't know".
- **Lost at:** the Realization content projection and wording. `no_record` has no attributed anchor ("you didn't mention…").

### F6. Ambiguity clarifications are typed correctly but worded badly

- D_ambig2: candidates `["Maya","Priya"]` → "Who do you mean by Maya or Priya?"
- H_ambig2: → "Who do you mean by Tom or Alex?"
- H_ambig4: candidates were whole clauses (`"he got the job"`, `"Tom told Alex"`) → "Who do you mean by "he got the job" or "Tom told Alex"?"

The candidates reach Realization as bare surface anchors with no role ("candidate referents of 'he'"), so the model writes a malformed question. H_ambig4 is also a Call 1 error: "how does Alex feel" is a missing fact, not an ambiguity, but the earlier ambiguity was carried forward.

### F7. Elicitation anchor quoted back as if the user said it

- H_know3: "What are you referring to when you say "what you are referring to"?"
- Small-talk turn: "What are you referring to when you say "small talk is pointless"?"

`clarification_content` puts `elicitation_anchor(name)` into `surface_anchors` of a `DialogueReference` with `attribution=user` and `source_kind=current_user_statement`. Realization then treats the anchor as the user's words. **Lost at:** `v1_grounding.clarification_content`.

### F8. No action for opinions, invitations to talk, or disagreement

| Turn | Call 1 | Plan | Output |
|---|---|---|---|
| H_advance4 "What's your take?" | `conversation_request mode=perspective` | acknowledge | "I see you've shared something and would like my take." |
| S_warm3 / S_tense3 "Can I tell you something that's been bugging me?" | `conversation_request talk_through` | acknowledge | "I hear you." |
| "Honestly I think small talk is pointless. Change my mind." (all 3 cores) | statement | clarify | "What are you referring to?" |

`contextual_perspective_request` covers only "link two of the user's passages". It worked on its fixture: D_persp3 gave a grounded tension between "uncomfortable declining" and "exhausted…". It cannot express a stance of Columbina's own, and none of the held-out turns triggered it.

### F9. Realization schema failure, raw output not kept

D_persp1 (reflect + validate_experience): both attempts returned `output_schema_invalid` with `candidate=null`. The raw provider output isn't retained, so the cause can't be diagnosed from the trace.

### What works well

- **Recall:** 7/7 correct with exact citations (`You said Lisbon.`, `You said Monday at 10.`).
- **Ambiguous vs. missing typing:** right on all fixture turns, and on 3 of 4 held-out ambiguity turns.
- **Experience reports** (lonely, wrecked by an ending, frustrated) get reflect + explore and read as engaged, if formulaic ("It sounds like… What has that been like for you?").
- **Citation integrity:** no forged or assistant-sourced citations appeared.

## 3. Personality, affinity, state

The basics above still fail, so the morning's sequencing advice applies. These results show what *can* vary today, not the quality of mature behavior.

### Personality (end to end, core memory swapped and workers restarted)

The same 7 turns ran under `columbina` (warmth .80 / play .58 / directness .62), `columbina_reserved` (.20 / .08 / .92) and `columbina_playful` (.97 / .97 / .30).

| Turn | columbina | reserved | playful |
|---|---|---|---|
| "The water was freezing but I swam anyway." | reflect | **acknowledge** ("I hear you.") | reflect + **explore** |
| "I've been feeling kind of lonely since I moved." | reflect + explore | **reflect only** | reflect + explore |
| "I don't really know anyone here yet." | acknowledge | reflect | reflect + explore |
| "Sir Leafington the Third. Thoughts?" | ack + explore_goal | ack | ack |
| "Small talk is pointless. Change my mind." | clarify | clarify | clarify |

- **Appropriate differences at the action level.** The reserved core never explores and cites no personality factors. The playful core explores most and cites curiosity and warmth. The delivery profile bands follow each core.
- **Little difference in wording.** Greetings were "Hello. Welcome back from the coast." / "Hello." / "Hello!". The rest is the same reflect template.
- **Playfulness never activates.** `playfulness_intensity` was `none` on every turn under every core, because `style_mapper` grants the playfulness permission only when a `social_play` move is planned. `social_play.playful_reframe` was eligible on experience turns but never selected. A plant named "Sir Leafington the Third" got no play even from the 0.97-playfulness core.
- Run-to-run variance is high: the baseline gave a single acknowledge where reserved gave a reflect on "I don't know anyone here". One run per cell can't separate personality from noise.

### Affinity (in-process only)

Live capture always lists `relationship_projection` as omitted, so affinity cannot be tested through the real path. `affinity.py` injected `CognitivePipelineRelationshipContext` (none / `close_friend` with records / `new_acquaintance`), with Columbina's real projection and the v1 protocol.

- **Plumbing works.** Both calls receive the relationship, and Call 2 cites `relationship.context`.
- **Behavior barely changes.** With close_friend, "bombed my driving test" got reflect + explore; with new_acquaintance, reflect only. That is plausible, but it is one turn.
- **Contradiction:** the new_acquaintance greeting rendered "Hello again." Realization doesn't receive the relationship, so the wording contradicts it.
- **"Roast me a little, I can take it"** became `decline_request` in all three conditions, with internal wording leaking: "Your request isn't a capability the runtime has registered." A close relationship can't unlock teasing because the request is routed as an unregistered capability.
- The close_friend run failed once with `pipeline.trace_validation` (turn 3). Only the code is retained.

### Dynamic state (end to end)

| Conversation | activation | alignment | valence | strain | derived controls |
|---|---|---|---|---|---|
| Warm (thanks, "fun to talk to") | 25 → 25 → 25 | 0 → 0 → 0 | 0 | 25 | unchanged |
| Tense ("not what I asked", "not listening… frustrating") | 46 → 67 → 88 | -35 → -70 → -35 | 0 → -35 → 0 | **25 throughout** | unchanged |
| Grief ("my grandmother died", "she raised me") | 25 | 0 | **0** | 25 | unchanged |

- **Same probe, same behavior.** "Can I tell you something that's been bugging me?" got an acknowledge-only "I hear you." after both the warm and the tense history. The delivery profile and derived controls were identical in all 9 turns.
- **Strain can't move in v1.** Strain rules are keyed on legacy predicates (`correct_interpretation`, `reject_humor`, `request_private_medical_records`, `export_failed` in `dynamic_state/construction.py`). v1 emits `correction`, so even two consecutive complaints left strain at 25.
- **Valence is Columbina's own goal congruence**, so the user's grief, loneliness or exhaustion never moves it. That may be the intended semantics, but then nothing in state represents the user's affect. In the grief turns, Call 1 also left `affect=null` although `sad` is in the vocabulary.
- **Gratitude and compliments don't raise alignment.** `express_gratitude` was parsed, but alignment stayed 0.

## 4. Grounded fix order

1. **Offer engaging moves for statements** (F1, F2): a comment/follow-up move bound to `statement` frames, and a "confirm corrected value" move bound to `correction` frames. Supply the corrected value as content.
2. **Let follow-up referents cite dialogue** (F4, F3): offer `dialogue_span`/`entity_ref` for `elaboration_request.referent_phrase`. Don't let the deterministic cue block a `known` recall. Allow a citation chain, or require that the cited span be the asked-for kind ("which cat" → a cat name).
3. **Verbalize the gap kind** (F5): an attributed `no_record` anchor such as "you didn't mention which company".
4. **Fix the clarification content** (F6, F7): give ambiguity candidates their role, and never put elicitation anchors in user-attributed `surface_anchors`.
5. **Add stance and invitation moves** (F8): "go ahead, I'm listening" for `conversation_request talk_through`, and a bounded own-stance move for `mode=perspective`.
6. **Keep raw Realization output on schema failure** (F9), and keep the cause on `trace_validation` failures.
7. **State:** map v1 predicates into the strain/alignment rules, add a user-affect dimension (or document that none exists), and make derived controls reachable. Only then repeat the state ablation.
8. **Personality/affinity:** let playfulness activate without a `social_play` move, feed the relationship to Realization, add a live relationship projection, then rerun the paired ablations with ≥3 runs per cell.

## Reproduction

The scripts are in the session scratchpad (not committed):

- `drive.py`: WebSocket driver. It creates users directly in the QA DB (signup is limited to 3/hour) and waits for each trace by `turn_id`.
- `inspect_turns.py`: stage-by-stage view of each turn.
- `affinity.py`: in-process relationship ablation.
- Corpora: `basics.json`, `state.json`, `persona.json`.
- The QA DB `columbina_shadow_eval_1010` keeps every trace for re-inspection.

## Update: registry v1.5 fixes, same day

The closed-world fixes from §4 (items 1–6) are implemented as
`argument-kind-registry-v1.5` (README section "V1 conversational grounding").
Opinions and general knowledge were deliberately left out: they need a new
content source (a separate design decision). The same real path was rerun.
The rerun included the basics and state corpora, plus a **fresh corpus** (N_*: neighbors and tomatoes, a
flight-time correction, bees, a camping trip, a rescue dog). No v1.5 prompt
or test uses its wording, nor that of the earlier corpora.

| Same turns (H_topic/refs/correct/advance + state) | v1.4 | v1.5 |
|---|---:|---:|
| Turns | 27 | 27 |
| Acknowledge-only plans | 16 | 6 |
| Bare receipts ("I hear you.") | 11 | 4 |
| Realization accepted | 27 | 27 |

Across all 40 basics turns, acknowledge-only plans went from 15 to 4. On the
fresh corpus, after the guidance tightening below, 2/15 were acknowledge-only
and 15/15 were accepted.

**Fixed (examples from the rerun):**

- Statements get a follow-up: "What does your sister think of her new job at the design studio in Lisbon?", "What did you think of the book you finished?", "What happened after the loud one knocked your plant off the shelf this morning?". `playful_reframe` now also appears ("quite the dramatic entrance").
- Corrections name the value: "Noted—Friday.", "I hear you: you said 9."
- Invitations to talk: "What's been bugging you?" after both the warm and the tense history (previously "I hear you." in both).
- Follow-ups with an obvious referent no longer clarify. "Is that also why sunsets look orange?" now gets an honest knowledge gap, where it used to get "What are you referring to? I don't know what 'that' refers to."
- Ambiguity is a well-formed choice: "Do you mean Tom or Alex?", "Who do you mean—Maya or Priya?".
- No more quoted placeholder ("…when you say 'what you are referring to'").
- One unstated-fact answer worded correctly: "I don't know; the company hasn't been mentioned."

**One regression caught and fixed:** the first v1.5 run produced formulaic
"I hear you. What would you like to share about…?" pairs. It also produced one
intrusive question after a grandmother's death ("What makes you say, 'I don't even know why I'm telling you this'?").
`call_two_v1_5.txt` now says a follow-up stands alone, and rules out follow-ups
after loss or distress and on remarks about the conversation itself. It also
rules out playful moves in response to distress. On the rerun, the grief turn
got a plain acknowledgment.

**Still failing (model compliance, not pipeline structure):**

- **Two-hop reference** (H_refs3, N_refs3): Call 1 now cites the identity statement ("Ana is the gardener", "Tofu is the loud one") instead of the bare name. Realization then refuses to infer: "I don't know who gave you the tomatoes. You said Ana is the gardener." The prompt asks for the smallest answer span, but compliance is inconsistent. D_options3 also regressed once to citing "painting or hiking".
- **Not-mentioned wording:** the gap is typed `not_mentioned_in_conversation`, but Realization usually still writes "I don't know…". Its fixed instruction text says to answer unknowns with "I don't know". Changing that needs a new instruction variant, which is a contract and provenance change. Also, "Where did they camp?" was parsed as an external `information_request`, so it never reached the dialogue gap.
- **Follow-up wording** is sometimes generic ("What would you like to share about that?") or echoes the statement first ("You mentioned that…").
- **"How does Alex feel about it?"** is still classified as ambiguous instead of missing.
- **"What's your take?"** is unchanged; it needs the deferred opinion/knowledge content source.
- **One live failure in 64 v1.5 turns** (N_share3, first run): Call 2 skipped a blocking condition. It didn't reproduce in-process (2/2 completed), and the failure payload keeps no Call 2 proposal, which makes it hard to diagnose.

Personality, affinity and state (§3 and fix order item 7–8) were not changed in this pass.

## Update: registry v1.6, open world with personality-selected opinions

Product decision (2026-10-10): open the world for general knowledge and for
Columbina's own views; keep facts about the user closed-world. Opinions are
personality-based. Design: README section "V1 open world, registry v1.6".
In short, a recorded `contribution` call proposes labelled claims or two to
three distinct takes, and code picks her take by scoring it against her traits.

Same real path, 49 turns:
- the knowledge turns from the earlier corpora
- a new open-world corpus: an opinion with a stated view, a preference, factual questions with follow-ups, a question about the user, current events
- the personality corpus plus two opinion prompts under all three cores

| Measure | Result |
|---|---:|
| Completed / Realization accepted | 49 / 49 |
| Turns that used the contribution call | 26 |
| Contribution call, median / max | 2.2 s / 4.1 s |

**Knowledge.** Every factual question that got "I don't know" before is now
answered correctly: tides (cause, period, high-to-low), sky colour, sunsets,
the night sky, bees, and vaccines. Follow-ups chain naturally ("Do they do
that in winter too?"). Medium-confidence claims are hedged ("I'm not entirely
sure, but…"). The boundaries held. "What's my favorite color?" stayed a
dialogue recall ("I don't know your favorite color"). "Who won the football
match last night?" was classified as `current_events` and declined.

**Opinions and personality.** "What's your take?" on *Klara and the Sun* now
gets a real, nuanced view. Across cores, the same prompts produce different stances:

| Prompt | Baseline | Reserved (directness .92) | Playful (warmth .97, play .97) |
|---|---|---|---|
| "Small talk is pointless. Change my mind." | partly agrees: "you're right… but a simple opening can be a bridge" | disagrees: "small talk isn't pointless; it's a low-stakes way to show goodwill" | partly agrees |
| "The Inception ending is overrated." | partly agrees | disagrees: "the ending earns its reputation" | partly agrees |
| Sea or mountains? | sea | mountains | sea |

In the first two rows every core was offered both a disagreeing and a
partly agreeing take; only the high-directness core chose to disagree. That
difference is the selection formula, not generation noise.

**Still to do:**
- The playful core picks playful takes, but Realization tones them down. Its playfulness permission only opens with a `social_play` move, and even `playful_reframe` was worded flatly ("You describe him as a fern, very dramatic about water.").
- Baseline and playful often agree on stance. They differ mainly in tone, and that tone difference doesn't survive wording yet.
- Stances travel inside answer content rather than as separate `express_stance.*` moves, so Call 2 can't yet choose *whether* to push back, only whether to answer.
- Each cell ran once; the takes offered also vary between runs.
- State and affinity are unchanged since the first report.
