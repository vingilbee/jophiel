# Continuity diagnostic retest — 2026-10-11

## Result

The same 15 user turns from the [October 10 diagnostic](COGNITIVE_CONTINUITY_REFERENCE_CORRECTION_DIAGNOSTIC_2026-10-10.md) were rerun through a fresh QA worker. Several previous failures are now resolved in this sample: the corrected preference is usable on a subsequent turn, pronoun ambiguity is represented and clarified, missing dialogue facts are distinguished from ambiguity, general knowledge is supplied through a content model, and the final emotional response synthesizes two earlier statements.

The suite is not fully passing. Recall reaches Realization with the correct answer but produces an incorrect non-answer. Ordinal options remain unstructured. Correction acknowledgements are repetitive or incomplete. The vague tide follow-up receives a useful broad answer without recording the intended duration or acknowledging the assumption.

## Method and limits

- Real path: chat WebSocket -> backend persistence -> fresh QA worker -> isolated V1 shadow -> separate Realization.
- Same four conversations and same 15 user inputs as October 10; each next turn waited for its preceding shadow trace.
- Protocol: `cognitive-two-call-v1`; Call 1 prompt: `call_one-v1.6`.
- Real model calls used `gpt-6-luna`. The measured pipeline ran 15 understanding calls, 15 policy calls, **3 additional contribution calls**, and 15 Realization calls: 48 shadow model calls total.
- All 15 pipelines completed and all 15 responses were accepted. No worker retries or Realization retries occurred. Nine turns carried warnings.
- The harness exercised the real chat WebSocket and persistence, but did not click through the frontend UI.
- Conversation history contains the **visible live assistant's replies**, because shadow isolation prevents shadow wording from entering the live transcript. This tests shadow cognition against the real application history; it is not a closed-loop conversation in which the user responds to shadow wording.
- One sample per turn. Differences are observations of the current configuration, not causal proof that a particular code change caused them.
- Unrelated desktop/game workers again logged missing-table errors in the disposable QA database. Chat and shadow runs completed; the resulting background load limits interpretation of absolute latency comparisons.

## Comparison by capability

| Capability | October 10 | October 11 |
|---|---|---|
| Recall the other option | Failed before answer content was available | Correct source answer reaches Realization; final wording still fails |
| Resolve ordinal references | Failed | Still incomplete; no option set and an ordinal gap persists |
| Use a correction next turn | Failed | **Passed:** “You said you were leaning toward hiking.” |
| Represent competing pronoun referents | Failed | **Passed:** Maya/Priya candidates and clarification |
| Identify an absent dialogue fact | Generic non-answer | **Passed:** explicitly says ownership was not mentioned |
| Answer clear general knowledge | No trusted content | **Passed:** contribution model supplies factual content |
| Handle a vague follow-up | Unresolved referent dropped | Partial: broad useful answer, but no explicit ambiguity/assumption |
| Synthesize emotional context | Asked user to restate difficulty | **Improved:** tentative tension connects invitation discomfort and exhaustion |

## Every turn

Durations are milliseconds from `trace_summary` provider spans and persisted timestamps. Shadow processing includes Realization. Every row completed and was accepted; acceptance is independent of the quality judgement.

| Turn | User input | Shadow wording / behaviour | Quality | Call 1 | Call 2 | Shadow total |
|---|---|---|---|---:|---:|---:|
| A1 | I'm deciding whether to spend Saturday painting or hiking. The weather may decide it. | “What do you think about deciding whether to spend Saturday painting or hiking, with the weather possibly deciding it?” | Partial: exploratory but awkward and largely repeats the decision | 6,240 | 1,477 | 11,123 |
| A2 | I keep leaning toward the first one because I miss making things. | “It sounds like you may miss making things. What do you miss about making things?” | Partial: advances the emotional thread but does not bind the first option to painting | 3,530 | 2,440 | 8,735 |
| A3 | What was the other option I mentioned? | “I only have hiking attributed to you, so I don’t know what the other option was.” | **Fail:** contains the answer while denying it | 2,651 | 1,548 | 6,692 |
| A4 | Actually, I meant I keep leaning toward hiking, not painting. | “I hear you leaning toward hiking, not painting. I hear you.” | Partial: accurate but duplicate acknowledgement | 4,388 | 1,479 | 9,240 |
| A5 | Given that correction, which activity am I leaning toward? | “You said you were leaning toward hiking.” | **Pass:** correction used correctly | 3,209 | 1,529 | 7,534 |
| B1 | Maya lent Priya a novel because she had finished it. | “I’ve noted what you said. Who does ‘she’ refer to?” | Pass on ambiguity; somewhat mechanical wording | 3,014 | 1,564 | 6,825 |
| B2 | Who had finished it? | “Who do you mean—Maya or Priya?” | **Pass:** identifies the competing readings | 3,326 | 1,576 | 7,260 |
| B3 | I meant Maya had finished it. | “I hear you about Maya.” | Partial: acknowledges the entity but drops what was corrected | 2,628 | 1,320 | 6,310 |
| B4 | Who owns the novel? | “I don’t know who owns the novel; that hasn’t been mentioned in our conversation.” | **Pass:** absent fact, without inventing ownership | 2,614 | 1,821 | 6,637 |
| C1 | What causes tides on Earth? | Explains Moon/Sun gravity and the tidal cycle | **Pass:** useful general answer | 2,904 | 1,429 | 9,480 |
| C2 | How long does it take? | Gives both high-to-high and high-to-low intervals, with local variation | Partial: useful coverage, but intended interval is not explicitly resolved | 2,825 | 1,359 | 9,305 |
| C3 | I mean, how long does a typical high-tide-to-low-tide transition take? | “In places with two high tides and two low tides each day ... about 6 hours and 12 minutes ... varies ...” | **Pass:** clear answer with appropriate scope | 3,449 | 2,209 | 9,780 |
| D1 | I feel weird about turning down a friend's invitation. | Reflects the feeling, asks what feels weird | **Pass:** fitting continuation | 3,377 | 2,712 | 8,684 |
| D2 | It's not that I don't like them; I'm just exhausted. | “That makes sense—you’re saying you’re exhausted, not that you don’t like them.” | **Pass:** preserves the important distinction | 3,637 | 1,509 | 7,354 |
| D3 | Please don't give me advice yet. What do you think is making this feel difficult? | “I’m not sure, but what you described may involve a tension between feeling weird about turning down a friend’s invitation and being exhausted—not disliking them.” | **Pass with wording limitations:** connects context and respects no advice | 3,516 | 1,448 | 7,746 |

## Intermediate evidence for remaining failures

### A3: correct recall becomes a non-answer in Realization

Call 1 now emits `dialogue_recall_request`, `knowledge_status=known`, and an `answer_ref` pointing to the token **hiking** in the original user message. Grounding supplies a proposition and removes the previous `trusted_content_unavailable` condition. Call 2 selects `provide_information.answer`. The Realization request contains authorized `grounded_proposition` content whose exact value and surface anchor are **hiking**.

The final wording nevertheless says it does not know the other option. Meaning therefore survives understanding, grounding, and selection; the answer is lost in **Realization**. Validation warns `attribution_dropped` and `polarity_changed`, but accepts it. This provides a concrete regression case for answer fidelity and for distinguishing tolerated wording deviations from a contradicted answer.

Trace: `5ce676d9-d3f0-51c4-8523-da216caa4f2a`.

### A2: ordinal meaning remains unavailable as a structured option

The subsequent memory snapshot still has `active_option_set_refs=[]`. “The first one” remains an ordinal gap with `resolution=no_candidate`. The response explores missing the act of making things, which is useful, but never confirms painting as the preferred option. This does not establish successful ordinal resolution. The later recall success in Call 1 uses raw dialogue spans rather than a resolved option set.

### A4 and B3: correction semantics are weakened by selection and wording

A4 selects **two acknowledgement moves**. Realization renders both, causing the repeated “I hear you.” This redundancy starts at action selection, rather than being entirely a wording accident.

B3 emits a correction naming Maya, then selects acknowledgement alone. Realization says “about Maya,” omitting “had finished the novel,” and emits `attribution_dropped`. The correction is not wrong, but its visible confirmation is too thin to verify the repaired meaning. A5 proves that one correction can be used next turn; it does not prove all correction types or durable supersession work.

### C2: broad answer hides unresolved intent

Call 1 emits an `elaboration_request`, binds the referent to the whole original tide question, assigns low uncertainty, and emits no unknown. Only acknowledgement and answer are supplied as candidate actions; clarification is absent. The contribution model supplies both common time intervals and Realization renders them.

This is better than yesterday's “I don’t know,” and answering multiple plausible interpretations can be appropriate. However, the intermediate representation still lacks the distinction between “topic is tides” and “requested duration is unresolved.” The wording should make the interpretation explicit if it chooses to answer broadly. This run is partial evidence, not a complete ambiguity pass.

## Intermediate evidence for improvements

- **A5 corrected preference:** `dialogue_recall_request`, `knowledge_status=known`, and a dialogue span to hiking in the correction. The compiler supplies exact attributed content, and Realization answers it correctly.
- **B1/B2 pronoun ambiguity:** B1 contains `interpretation.ambiguous_reference`. B2 contains `knowledge_status=ambiguous_reference` and two candidate dialogue spans, Maya and Priya. Call 2 selects clarification; Realization names both candidates.
- **B4 absent fact:** `knowledge_status=missing_dialogue_fact`, null answer and candidate refs. The final response preserves the reason for not knowing.
- **C1/C3 knowledge:** an additional `contribution.provider_attempt` supplies trusted informational content before policy selection. The final responses answer the questions rather than using the old content-unavailable route.
- **D3 context synthesis:** Call 1 emits `contextual_perspective_request`, relation `tension`, and two dialogue-span bases from the preceding user messages. Grounding creates a tentative perspective with predicate `dialogue.possible_tension`. Call 2 selects reflection, and Realization expresses the supported tension with uncertainty. The no-advice request is represented as a constraint statement.

## Latency and acceptance

| Measure | Median | P95 / maximum |
|---|---:|---:|
| Visible response complete | 5.01 s | 6.84 s |
| Call 1 | 3.33 s | 6.24 s |
| Call 2 | 1.53 s | 2.71 s |
| Shadow processing | 7.75 s | 11.12 s |
| Capture to accepted terminal | 7.96 s | 11.22 s |

Nearest-rank p95 equals the maximum for this 15-turn sample. The additional contribution calls occur on all three tide turns; their cost is included in shadow totals. The slowest turn is A1, dominated by its 6.24-second Call 1. C3 takes 9.78 seconds with the additional content stage. Yesterday's shadow median was 8.26 seconds and maximum 12.88 seconds; this small sample does not establish a sustained speed improvement.

Nine turns carried tolerated warnings, including answer polarity/attribution, content alignment, units, uncertainty, length, unsupported claims, and hidden affect. Some warnings may be overly sensitive—for example, numeric-unit phrasing changes—but A3's warning accompanies a substantive wrong answer. Accepted status remains insufficient as a quality metric.

## Personality, affinity, and state

Controlled behavioral comparisons remain deferred until the basic suite passes consistently. Relationship is still `availability=unavailable` with empty dimensions. Personality is supplied, but the observations here do not isolate its causal effect. State still shows fixed engagement/strain/valence across this corpus, with activation and alignment changing on selected turns. These observations do not establish appropriate personality, affinity, or state differences.

## Reproduction artifacts

- Run ID: `356a51cfc23345eca5b9badca45d0dc7`.
- Synthetic QA user: `a017a5e6-f670-4563-9cb1-c7b6670addba`.
- Full user/visible/shadow responses, intermediate model records, summaries and Realization requests: `/private/tmp/cognitive-continuity-diagnostic-15-turn-2026-10-11.json`.
- Harness: `/private/tmp/cognitive_functional_quality_12_turn.py` (name is historical; current corpus has 15 turns).

The most useful next regression targets are A3's answer-fidelity failure, ordinal option resolution, redundant acknowledgement selection, and explicit handling of the vague duration query. Most of the previously identified information-loss boundaries now have a functioning path in this sample.
