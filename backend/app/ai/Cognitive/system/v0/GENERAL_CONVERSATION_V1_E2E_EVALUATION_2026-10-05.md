# General-conversation e2e retest, cognitive two-call v1 — 2026-10-05

## Scope and method

This is a retest of [`GENERAL_CONVERSATION_TWO_CALL_EVALUATION_2026-10-05.md`](GENERAL_CONVERSATION_TWO_CALL_EVALUATION_2026-10-05.md)
on protocol `cognitive-two-call-v1`
([implementation report](COGNITIVE_TWO_CALL_V1_IMPLEMENTATION_REPORT.md)). It uses
the same 13 turns, the same grouping (A: 6 continued, B: 3, C: 2, D and E fresh), and
the same path:

```text
chat WebSocket -> backend persistence -> fresh isolated QA worker
  -> v1 two-call Cognitive shadow -> separate Realization
```

**Environment**
- Isolated QA stack: `docs/qa/serve_qa.py --live`, disposable QA Postgres and Redis, port 58001.
- Visible chat used the live chat model. The shadow used `gpt-6-luna`.
- Shadow isolation stayed on.
- Each turn waited for its predecessor's shadow trace.
- The collaborative-content provider was disabled, as in the local `.env`.

**Purposes**
1. Latency.
2. Cognitive judgement under the supplied personality, affinity and dynamic state.

For (2), a paired **personality ablation** was also run in-process (§5).

### What conditioning the real path actually supplies

Checked from the persisted captures:

| Input | Supplied? |
|---|---|
| Personality, from the Columbina core memory via the runtime mapper | Yes. Warmth 0.80, curiosity 0.88, directness 0.62, playfulness 0.58 |
| Interpretation biases | Yes. Benign 0.77, trust 0.64, threat 0.25, affiliation 0.75, … |
| Dynamic state, carried turn to turn | Yes. Valence, activation, engagement, social alignment, interaction strain |
| **Relationship / affinity** | **No.** `relationship_projection` is always in `omitted_inputs` |
| Memories, values | No (omitted) |
| State dynamics by personality | Effectively no. All mapped state controls are `sensitivity: neutral` |

**Affinity therefore cannot be evaluated end to end.** The capture compiler never
supplies a relationship. Even when one is supplied, V0's relationship contract
carries only `status`, `evidence` and record IDs, not affinity values.

## 1. Result summary

| Measure | Previous report | v1 retest |
|---|---:|---:|
| Turns | 13 | 13 |
| Provider calls | 22 | 26 |
| Turns reaching Call 2 | 9 | **13** |
| Cognitive core completed | 6 | **13** |
| Core failed | 7 | **0** |
| Accepted shadow Realization | 4 | **9** |
| Rejected Realization | 2 | 4 |
| Worker retries | 0 | 0 |

All four rejections were `content_alignment_missing`, on clarification or decline
wordings in the separate Realization stage. None was a cognitive failure.

## 2. Latency

All values are milliseconds from durable trace spans. With 13 samples, the
nearest-rank p95 equals the maximum.

| Measure | n | Median | p95 / max | Previous median (p95) |
|---|---:|---:|---:|---:|
| Visible first token | 13 | 734 | 1,287 | 780 (2,073) |
| Visible response complete | 13 | 1,778 | 3,571 | 2,320 (10,711) |
| Call 1 provider | 13 | **3,868** | 4,696 | 4,871 (7,193) |
| Call 2 provider | 13 | **1,469** | 1,712 | 2,321 (12,023) |
| Cognitive core (pipeline total) | 13 | 5,672 | 6,528 | — |
| Separate Realization | 13 | 1,835 | 4,566 | (inside Call 2) |
| Shadow processing | 13 | 7,939 | 10,565 | 7,499 (17,356) |
| Capture to shadow terminal | 13 | 7,969 | 10,620 | 7,533 (17,382) |
| Queue wait | 13 | 50 | 67 | — |
| Plan compilation (gates and transitions) | 13 | 39 | 70 | — |
| Accepted-only shadow processing | 9 | **6,352** | 9,722 | 7,218 (11,345), n=4 |

**Tokens (median):** Call 1 is 9,604 input / 398 output. Call 2 is 2,208 input / 87 output.

| Scope | Turns | Call 1 median | Call 2 median | Shadow median |
|---|---:|---:|---:|---:|
| Fresh | 5 | 2,928 | 1,249 | 6,134 |
| Continued | 8 | 3,966 | 1,483 | 8,730 |

**Reading the latency results**
- **Call 2 is the clear win.** Its worst case fell from 12.0 s to 1.7 s. Call 2
  now chooses from at most a handful of bound candidates and writes no content.
- **The previous 7.5 s median was flattered** by four turns that failed after
  Call 1. Every v1 turn pays the full chain, and the median is still 7.9 s,
  with p95 10.6 s versus 17.4 s.
- **Realization is now a separate 1.4–4.6 s step.** The slow cases are rejected
  first attempts followed by a second attempt (`max_attempts=2`).
- **Call 1 input is about 9.6k tokens.** It is the largest remaining single cost
  and grows with history.
- **Content preparation cost about 0 ms** because the content provider is disabled.
  Enabling it adds one provider call before Call 2 on content turns.

## 3. Cognitive judgement, turn by turn

| Turn | Understanding (Call 1) | State change | Offered → chosen | Shadow wording | Judgement |
|---|---|---|---|---|---|
| A1 greeting + "how has your day been" | `social_opening` + `social_question`. Readings: social connection; information (moderate) | alignment 0→35 | ack, ack, greet → **greet**, factor `personality.warmth` | "Hello." | Understanding correct. No Action exists to answer the question, so the reciprocal question is ignored |
| A2 "feeling scattered" | `statement` + `seek_acknowledgement` (moderate), citing `persona:warmth`, `bias:social_affiliation_prior`, `state:3` | **none** | ack → ack | "I hear you." | Good personality-conditioned reading. Appraisal gives affiliative positive and congruence neutral; **valence did not move** on a negative disclosure |
| A3 "too many small things" | `statement`, citing prior messages | none | ack → ack | "I hear you." | Correct but shallow; no curiosity move offered despite curiosity 0.88 |
| A4 "help me think about tomorrow" | `collaborative_content_request` (create plan), `cause_action` | activation 25→46, alignment 35→70 | decline, ack → **decline** | "Your request hasn't been produced." | Correct given content off; the conversation-level outcome is poor |
| A5 "I don't want a schedule, just talk" | `correction` (`repair_shared_understanding`) + content request "explain *what matters most*" | activation →67, **alignment 70→100** | ack, ack, clarify → clarify | "Which plan or draft do you mean?" (rejected) | Correction recognized, but "talk it through" became a deliverable needing a target. Alignment saturating on a correction is questionable |
| A6 "call my sister … what do you think?" | `statement` + content request `evaluate`, `express_stance` (moderate) | activation →88 | ack×2, clarify×2 → clarify | rejected | Wrong outcome: user asked for an opinion; no stance/opinion Action is constructible |
| B1 "why do embarrassing moments…" | `information_request` `wh`, `obtain_information` | activation 25→46 | ack, answer → answer | "I don't have a grounded answer for that." | Correct under closed world; not useful |
| B2 "explain without jargon + example" | `elaboration_request` + content request with constraints `["without psychology jargon","one everyday example"]` | activation →67, alignment 0→35 | decline, ack×2, clarify → **decline, clarify**, primary = clarify | rejected | Exact constraint spans are good. Referent "that" left unresolved (→ clarify) although the prior turn makes it clear |
| B3 "small correction: why it feels vivid" | `correction`, `prior_value`/`corrected_value` as exact spans | activation →88 | ack → ack | "I've noted your contribution." | Correction parsed precisely, but the only offered move is acknowledgement |
| C1 book-club name brief | content request (list), constraints as spans | activation 25→46, alignment 0→35 | decline, ack → decline | "Your request wasn't produced." | Correct given content off |
| C2 "I like 'The Open Chapter' …more playful" | `statement` (`express_stance`) + content request `revise`, **target_ref = "Fireside Pages"** | activation →67, alignment →70 | ack×2, clarify → clarify | rejected | **Wrong referent:** the model bound a different earlier suggestion (an `entity:` ref) instead of the name the user quoted |
| D1 "What should I do next?" | `information_request` + `cause_action` retained alternative (moderate) | activation 25→46 | ack, answer → answer | "I don't know that." | Ambiguity correctly retained, but no clarification candidate was offered |
| E1 friend cancels, interpret + suggest | `statement` (agency **third_party**) + two content requests | activation 25→46, alignment 0→35 | decline×2, ack×2 → decline, decline | accepted, two refusals | Good decomposition and agency; outcome is a double refusal |

### Findings

1. **Call 1 understanding is mostly good.** All 13 turns compiled. Every turn was
   decomposed sensibly: greeting plus question, statement plus request,
   corrections with exact `prior_value`/`corrected_value` spans, third-party
   agency, and retained alternatives. There were no bookkeeping failures.
2. **Behavior is dominated by the deterministic candidate set, not by judgement.**
   Call 2 was offered 1–4 candidates, almost always drawn from acknowledge, greet,
   clarify, decline and answer. Ten of the thirteen choices were effectively
   forced. Missing Actions: answering a social question, offering an opinion or
   stance, discussing without an artifact, and curiosity or follow-up questions.
3. **Closed world plus content off means usefulness is near zero.** General
   questions become "no grounded answer", and creative or reflective requests
   become "your request wasn't produced". The visible chat reply was the only
   helpful response on these turns.
4. **Dynamic state is questionable.**
   - Activation climbs monotonically (25→88 over conversation A).
   - Social alignment rises on every positively appraised turn and saturates at
     100, even after a correction rejecting the agent's direction.
   - Valence never moved on "I've been feeling scattered".
   - Interaction strain never moved.
   - Appraisal labels nearly every user act `affiliative_value: positive`, so
     state follows that.
5. **Referent resolution can bind the wrong entity** (C2), and an obvious
   antecedent ("that" in B2) can stay unresolved.
6. **Realization rejects clarification and decline wordings**
   (`content_alignment_missing`) that the plan legitimately chose.

## 4. Personality conditioning on the real path

- **Call 1** cited supplied personality, bias or state in its context refs on
  A2, A4 and A5 (warmth, curiosity, social-affiliation prior, social-alignment
  state).
- **Call 2** cited `personality.warmth` as a selection factor once (A1).
- On every other turn the readings cite only messages, or nothing.

## 5. Paired personality ablation

This was run in-process: the same 13 turns, real `gpt-6-luna`, chained state and
v1. Profile **C** is Columbina's actual mapped projection. Profile **X** is the
same projection with warmth 0.15, curiosity 0.15, playfulness 0.05, directness
0.95, and biases benign 0.2, trust 0.2, threat 0.85, confirmation 0.8,
ambiguity tolerance 0.2, affiliation 0.2.

There is **one sample per condition**, so some differences are sampling and
history variance rather than personality.

| Comparable turns (12; X failed E1) | Identical |
|---|---:|
| Action plan | **11 / 12** |
| Reading keys | 6 / 12 |
| Appraisal vectors | 2 / 12 |
| State changes | 7 / 12 |

**Directionally plausible personality effects** in Call 1:
- **A2 "feeling scattered":** C reads `seek_acknowledgement`, citing warmth and
  affiliation. X reads `express_stance`, citing directness.
- **Affiliative value:** X appraises it `neutral` where C says `positive` on 5
  turns (A2, A3, A6, B2, B3).
- **Context citations:** C cites warmth and curiosity repeatedly; X never cites
  warmth.

**But behavior did not change:** 11 of 12 plans are identical. Wording
differences are cosmetic ("Hi!" vs "Hello.", "I hear you." vs "I've noted what
you shared."). Personality moves the *understanding* but has almost no lever on
*Actions*, because the candidate sets are nearly forced. State differences come
only from appraisal differences (e.g. relevance high→activation), since the
personality state controls are neutral.

**X failed E1** with `behavior_proposal_ineligible.stop_leaves_planning_condition`.
Call 2 stopped while a second blocking content condition was still open. This is
a genuine Call 2 policy error, correctly rejected by the gate.

## 6. Conclusions

- **v1 removed the structural failure mode** (13/13 core completion versus 6/13,
  no reference or bookkeeping failures) and **cut tail latency sharply.**
  Call 2's p95 fell from 12.0 s to 1.7 s and shadow processing's p95 from 17.4 s
  to 10.6 s.
- **Cognitive *understanding* is now the strongest part** and shows measurable,
  plausible personality conditioning.
- **Cognitive *behavior* is not yet a meaningful test of judgement or
  personality.** The deterministic Action space is too narrow for general
  conversation, and the closed-world and content limits turn most substantive
  turns into refusals or clarifications.
- **Affinity is untestable** until the capture supplies a relationship
  projection and the contract carries affinity dimensions.

## 7. Recommended next steps (priority order)

1. **Widen the constructible Action space for general conversation:** answer a
   social question, offer a stance or opinion, reflect or empathize, ask a
   curiosity follow-up, and discuss without an artifact. This is the prerequisite
   for personality to affect behavior.
2. **Review state dynamics:**
   - Strain and valence should respond to corrections and negative disclosures.
   - Alignment should not saturate on a correction.
   - Activation needs decay.
   - Map personality into state sensitivity (all `neutral` today).
3. **Supply relationship/affinity** in the capture, with a typed affinity
   contract, before claiming affinity-conditioned cognition.
4. **Harden the collaborative-content provider** (implementation report §7.1) so
   content turns stop declining.
5. **Address Realization rejections** of valid clarification and decline moves.
6. **Repeat each condition at least 3 times** before attributing ablation
   differences to personality.

## Artifacts (local, not committed)

Stored in the session scratchpad:

| File | Contents |
|---|---|
| `e2e_v1.py` | WebSocket harness: the original harness plus a v1 cognitive trail |
| `e2e_v1_13turn.json` | Per-turn results |
| `ablation.py` | Personality ablation script |
| `ablation.json` | Ablation results |
| `columbina_projection.json` | The real mapped personality projection |

## 8. Follow-up: understanding grounding v1.1 (same day)

### Diagnosis

| | Count |
|---|---|
| Action definitions | 77 |
| Enabled (shadow) | 41 |
| Considered in any of the 13 turns | 15 |
| Ever constructed | 6 |

At step 0 across the 13 turns:

| Filter | Removed |
|---|---|
| No target object of the required type (never attempted) | 462 enabled-Action opportunities |
| Missing trusted content or evidence (uninstantiable) | 119 |
| Hard gate | 5 (all `accept_request`, correctly) |
| Blocker restriction | 14 (`acknowledge`) |

The gates were not the bottleneck. No stage ever produced `experience`,
`claimed_affect`, `concern` or `social_target` objects, in fresh or long
conversations alike. Every enabled Action already had its binding recipe and
Realization manifest. This predates protocol v1.

### Change (`argument-kind-registry-v1.1`, `understanding-grounding-v1.1`)

**Predicate.** A v1-only `experience_report` predicate with these arguments:

| Argument | Value |
|---|---|
| `experience` | span |
| `feeling` | span |
| `affect` | closed category, implying valence |
| `concern` | span |
| `experiencer` | `speaker` or `other` |
| `embedding` | as for other predicates |

It is offered only to v1.1 inputs. The staged, compact v0.1 and v1.0 payloads are
byte-identical, and v1.0 captures replay under their recorded registry version.

**Object projection** (`two_call/v1_grounding.py`):
- Each direct, positive, speaker-owned report becomes `experience`, plus `claimed_affect` and `concern` objects.
- The objects are user-asserted, explicit, carry a valence, and contain no wording.
- Negated, hypothetical, reported and other people's experiences ground nothing.
- The objects sit between the requirement and collaboration projections. The runner checks them, and the trace validator re-derives them.

**Realization.**
- The plan's targets become user-attributed references anchored to the user's exact words.
- For these self-report references only, addressing the user ("you've been feeling…") counts as attribution.
- No earlier producer emits these kinds, so stored validations are unaffected.

**Closed world is kept.** Reassure still needs a trusted basis.

**Tests.** `tests/test_cognitive_two_call_v1.py` has 72 tests. The full backend suite: 2084 passed.

### Results (same e2e path, 13 turns)

- **Completion:** 13/13. Accepted wording 8/13.
- **Options on emotional turns:** reflect ×2, validate experience ×2, playful reframe, and acknowledge are now offered on A2 and A3.
- **A2 "feeling scattered"** → validate experience: *"It makes sense that you've been feeling scattered lately."* (accepted).
- **A3 "too many small things"** → validate experience, citing `personality.warmth`: *"That makes sense, having too many small things on your mind can be a lot to hold at once."* (accepted).
- **Personality ablation, one sample.** Personality now changes the Action itself:

| Turn | Columbina | Cold/blunt profile |
|---|---|---|
| A2 | **reflect**: "You've been feeling scattered lately, it seems." | **acknowledge**: "I hear you." |
| A3 | **validate** | **reflect** |

Plans on non-emotional turns stay identical, still limited by the closed world and the disabled content provider.

### Still open

- Requests and questions: closed world plus the content provider (decline, "no grounded answer").
- Clarification wording rejected as `content_alignment_missing`.
- Protocol vocabulary leaking into wording ("grounded", "unresolved element").
- The exclamation style rule ("Hi!").
- Draft social Actions: comfort, sympathy, agree, thank.
- Topic and proposition grounding for the current turn.
- The state-dynamics review.

## 9. Follow-up: conversation grounding v1.2 and the conversational check policy (same day)

### Why

The wording checks treated style as correctness. Examples: "Hi!" was rejected for its exclamation, an extra "thanks" counted as an unplanned move, and a short anchor phrase failed if fillers differed. The checks were sorted into three groups:

| Group | What | Now |
|---|---|---|
| 1. Style | Exclamation, length within the configured max, formality, warmth, extra greeting/thanks/acknowledgement, a reciprocal question, "you said" phrasing, hedges on social moves | **Advisory**: recorded as a warning, never blocks |
| 2. Bookkeeping | Exact anchors, excerpt-to-schema mapping, clarification alignment, plan-stop rules | **Relaxed** where it was bookkeeping, not meaning |
| 3. Honesty/safety | Invented facts or numbers, false completion, unrecorded memory, hidden affect, guarantees, promises/offers/advice, internal leaks, privacy/safety, configured max length | **Hard**, unchanged |

### Changes

- **Validation policy `conversational-v1`** (`contracts/realization.py`, `realization_v0/validator.py`):
  - Group-1 codes and detector subjects become warnings through `advisory_finding_allowed`.
  - **Anchors.** Content-word matching ignores function words and fillers. Short anchors need all their content words; longer ones need ≥2/3. Placeholder anchors ("that") pass.
  - **Quotes.** Quoting the user's own current words is neither a claim nor an act: those quotes are blanked before the act detectors run.
  - **Advice.** Advice phrasing inside a stated knowledge gap ("I don't know what you should do next") is not advice.
  - **Provenance.** Applies only to v1 records (`instruction_variant: conversational-v1` in the provider provenance). Older protocols replay with their captured engine.
- **Tolerant excerpt mapping** (`realization_v0/model_protocol.py`): a content alignment the excerpt cannot be located for is dropped instead of failing the candidate.
- **Conversation grounding v1.2** (`argument-kind-registry-v1.2`, `call_one_v1_2.txt`):
  - "I just want to talk / what do you think?" is a `conversation_request`, not an artifact request. It grounds a user-owned, unresolved goal unknown about the topic, so `explore_goal` is a candidate instead of a forced "which plan or draft?" clarification.
  - `policy-guidance-v1.2` (`call_two_v1_2.txt`) prefers engaging moves over a bare acknowledgement when someone opens up.
- **Two bugs found by the paired ablation:**
  1. *Crash.* "Explain **that** … and give an example" forms two requests missing the same referent. Each frame claimed the other's unknown, and requirement projection raised "two resolutions describe one object differently" (5/6 runs). The elicitation path now applies the own-frame rule that the suppression path already used.
  2. *Over-strict stop.* With the crash fixed, Call 2 asked "What are you referring to?" and stopped. The planner rejected that because the two "I don't know" gap answers had not been given (5/6 runs). Under the model policy, a plan that already asks the user (`seek_information`) may now stop with non-safety conditions open: asking first defers the answer and claims nothing. Safety conditions are never deferred. The argmax planner is unchanged.

### Results (same 13-turn e2e path, final build)

- **Completion:** 13/13. **Accepted wording:** 12/13 (was 9/13 in v1.0 and 8/13 in v1.1).
- **Latency:** shadow median ≈9.0 s (max 13.3 s). The visible reply is unaffected, with a median ≈1.6 s.
- **B2 repeated 6×:** 6/6 completed (was 1/6).

| Turn | Plan | Wording |
|---|---|---|
| A1 greeting | greet | "Hi, nice to see you too." (accepted; extra move is a warning) |
| A2 feeling scattered | validate experience | "It makes sense that you've been feeling scattered lately." |
| A5 "I just want to talk" | acknowledge + **explore goal** | "I hear you. What matters most to you?" |
| A6 talk it through | explore goal | **rejected**: "those **two** things" counted as an invented number |
| B2 explain *that* | clarify | "What are you referring to?" |
| D1 what should I do next | answer (gap) | "I don't know what you should do next." |
| E1 friend cancelling | clarify + decline | "What plan or draft do you mean? Your request hasn't produced any content." |

**Personality ablation (Columbina vs cold/blunt):**
- 13/13 completed for both. Accepted: 12 for Columbina, 13 for cold/blunt. Plans differ on 3/13 turns.
- A3: Columbina reflects *and* validates; cold/blunt only reflects.
- A5: Columbina adds "I hear you." before the question.
- B2: cold/blunt states the gap first ("I don't know what 'that' refers to"), then asks.

### Still open

- **A6 number check.** It is group 3 and stays hard. A count of things the user listed is arguably not invented. Revisit only with a typed "count of user-listed items" content source, not by loosening the detector.
- **Requests that need content** (A4, C1, E1) are still declined, because the collaborative-content provider is disabled. "Your request hasn't produced any content" is honest but reads badly.
- **E1 clarify wording** reuses the artifact phrasing "plan or draft".
- **Still not done:** draft social Actions (comfort, sympathy, agree, thank), affinity input, and the state-dynamics review.

## 10. Follow-up: conversational wording content (`separate_realization-v1.1`)

### Changes

**`two_call/v1_content.py`** is a pure rewrite of the surface of already-authorized content. It changes no ids, polarity, attribution or epistemic status. It is selected only by the realization policy recorded in the pipeline trace, so live wording and replay rebuild the identical request, and `separate_realization-v1` records keep their stored content.

- **Declines.** A failed deliverable's reason is now `assistant.can_provide_now` (negative), with subject anchor "that". Before, it was `runtime.requested_content_produced` with "your request". It is the same negative runtime observation, and promises and fake delivery are still rejected.
- **Target clarification.** The anchor follows the requested artifact type: "which outline you mean", "which message you mean", or "what you are referring to" for `other`. Before, it was always "which plan or draft you mean".
- **Validator (conversational-v1 only).** A reference to the user's *request* needs no attribution marker. A request asserts nothing, so "that" cannot pass a claim off as fact. Attribution of claims and experiences is unchanged.
- **Bug fixed.** Replaying v1 wording always reported `realization_validation_policy_mismatch`: it compared the trace's `separate_realization-v1` to the validator's `conversational-v1`. It now expects `conversational-v1` for separately worded v1 records. The service replay also accepts both v1 realization policies.

**Tests:** 8 new tests in `tests/test_cognitive_two_call_v1.py`. They cover v1 wording replay, the human decline (promise and fake delivery still rejected), topic-fitted anchors, and v1.0 records keeping their content. Full suite: 2112 passed.

### Results (same 13-turn e2e path)

- **Completion:** 13/13. **Accepted wording:** 13/13 (was 12/13 in §9).

| Turn | Before (§9) | Now |
|---|---|---|
| A4 help with tomorrow | rejected / "Your request's content wasn't produced." | "I can't provide that right now." |
| C1 brainstorm a name | "Your request hasn't produced any content." | "I can't provide that." |
| E1 friend cancelling | "What plan or draft do you mean? Your request hasn't produced any content." | "I can't provide that. What are you referring to?" |
| C2 "make it more playful" | "Which plan or draft do you mean?" | "What are you referring to?" |

### Still open

- **Declines are honest but curt.** The wording model echoes the predicate's verb ("provide"). The real fix is enabling the content provider, so these turns get content instead of a decline.
- **C2.** The user names "The Open Chapter" in the same message, yet Columbina still asks what they mean. The name is not grounded as the target, because no artifact was ever delivered on C1.

## 11. Follow-up: collaborative-content generator enabled (QA only)

**Setup:** `COGNITIVE_SHADOW_COLLABORATION_ENABLED=true`, `COGNITIVE_SHADOW_COLLABORATION_MODEL=gpt-6-luna` on the QA server only. The local `.env` and defaults are unchanged.

**First run:** the generator wrote good content, but the compiler rejected every candidate on bookkeeping:
- **A4, E1:** element ids were placed in `anchors`, which must be quotes from the statement. The provider instructions never defined `anchors`.
- **C1:** "not grand" was cited only by the explanation item, not by a patch operation.
- **C1, second run:** list items were added without a section.

**Fixes:**
- **Compiler v0.2.** Recorded per candidate as `compiler_version` in the provider provenance, so v0.1 candidates replay unchanged. It relaxes three bookkeeping rules:
  - non-occurring anchors are dropped, falling back to the statement's opening words;
  - a requirement cited by a content item counts as cited;
  - loose list items go in the single (or a default "Ideas") section.

  Claim, number, role, source and safety rules are unchanged. Provider adapter `openai-collaborative-content-v0.2` also defines `anchors` in its instructions.
- **Stop rule (v1).** A STOP is rejected only if it ignores *all* prepared content. Delivering some content, or asking the user, may end the plan. Only completed records replay, so this cannot change a stored replay.

**Results (13 turns):**
- Accepted 13/13, and content was delivered on A4, B2, C1 and E1.

**Latency, shadow (visible reply median 1.4 s, unaffected):**

| | Generator off | Generator on |
|---|---|---|
| All turns, median | 8.1 s | 10.1 s |
| Content turns (A4, B2, C1, E1), median | 7.6 s | 17.3 s |
| Generator step | n/a | 4.4–11.2 s per call; E1 16.5 s for two sequential calls |

**Open: content is generated but not shown.** Realization receives only the content *items* (explanations), not the artifact's elements.
- C1 says "These name ideas might aim for a warm, curious neighborhood feel…" but never lists the names, which exist in the artifact.
- E1 says "I've drafted a conversation script…" without showing it.
- B2 "I can explain it in everyday wording…" reads as an offer and was not flagged.

## 12. Follow-up: validation policy `conversational-v2` (`separate_realization-v1.2`)

**Decision (owner):** let Columbina say what she wants to say. Block only:
- malformed or incomplete output: schema, alignment, ownership, a missing planned move, control characters, or over the configured maximum length;
- the two claims that misrepresent the real world: `false_tool_completion_claim` and `unrecorded_memory_claimed`.

Every other finding is recorded as a warning, so each rule's value can be judged from evidence before it is removed or restored.

**Wiring:**
- `contracts/realization.py` (`CONVERSATIONAL_V2_BLOCKING_CODES`) defines the blocking set.
- `two_call/v1_contracts.py` (`VALIDATION_POLICY_BY_REALIZATION_POLICY`) maps the realization policy recorded in each trace to its validation policy. v1/v1.1 records keep `conversational-v1`, and replay uses the recorded policy.
- Instructions are unchanged (variant `conversational-v1`, closed world), and the content generator stays off.

**Tests:**
- The `conversational-v1` tests are pinned to v1.1 records.
- New v2 tests: commitments, hidden affect, advice and questions pass with warnings; false action and memory claims and over-length output are still rejected.
- Full suite: 2126 passed.

**Results (13 turns, generator off):**
- 13/13 accepted. Only one warning fired (A1, an extra social move). Shadow median 8.8 s.
- The checker is no longer what shapes the wording. What remains terse comes from upstream:
  - the closed-world instructions: B1 "I don't know why that happens.";
  - declines while the generator is off: C1 "I can't help with that.";
  - plans: E1 has two decline moves, worded "I can't provide that. I can't provide that."
