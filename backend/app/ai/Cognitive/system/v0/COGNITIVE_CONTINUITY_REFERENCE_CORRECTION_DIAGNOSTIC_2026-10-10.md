# Cognitive continuity, reference, correction, and ambiguity diagnostic — 2026-10-10

## Executive result

The current shadow pipeline is structurally reliable but does **not** yet pass
the requested conversational foundations.

- All 15 turns completed Call 1, the deterministic middle, Call 2, plan
  validation, and separate Realization.
- All 15 realizations were accepted for shadow review.
- Nevertheless, the shadow failed both direct memory checks, failed to apply a
  correction on the following turn, did not reliably identify genuine
  ambiguity, and rendered clear knowledge gaps and ambiguous questions with the
  same generic “I don’t know” behavior.
- In the emotional conversation, only the first of three responses clearly
  advanced the discussion. The last response asked the user to restate the
  difficulty instead of using the two preceding turns.

This is an important distinction: **15/15 acceptance is not 15/15 conversational
success.** The hard contracts are currently much better at verifying structural
legality than continuity or usefulness.

Because these foundations did not pass, a controlled personality/affinity/state
behavioral comparison was not run. The report separately records whether those
signals were available and used, but does not attribute behavior differences to
them prematurely.

## Method

The evaluation used the real isolated application path:

```text
chat WebSocket -> backend persistence -> fresh QA worker
  -> dialogue-memory projection -> Call 1 understanding
  -> deterministic interpretation/state/goal/requirements/grounding
  -> Call 2 action policy -> deterministic plan validation
  -> separate Realization -> persisted trace_summary
```

Call 1, Call 2, and Realization used real `gpt-6-luna` calls. Shadow isolation
remained enabled. Continued turns waited for the preceding shadow trace. Visible
assistant responses and shadow responses were retained separately.

Environment note: newer unrelated desktop/game workers logged missing-table
errors in the disposable QA database. The chat endpoint, cognitive job tables,
shadow worker, trace persistence, and every tested turn completed normally; no
reported cognitive result depended on those failing workers.

The 15 turns formed four uninterrupted conversations:

1. establish painting versus hiking, use “the first one,” recall “the other
   option,” correct the preference, and query the corrected preference;
2. introduce a deliberately ambiguous pronoun, ask who it denotes, resolve it,
   then ask an unknowable ownership question;
3. ask a clear general-knowledge question, a genuinely vague follow-up, then a
   clear corrected version;
4. disclose discomfort about declining an invitation, add an important
   distinction, then prohibit advice and ask for reflective interpretation.

For failures, the review examined:

- Call 1 proposal, unknowns, conflicts, reference table, and dialogue context;
- durable dialogue-memory items, option/topic sets, gaps, and supersession;
- accepted understanding, grounded objects, planning conditions, and eligible
  actions supplied to Call 2;
- Call 2 selection and selection factors;
- the Realization request, constraints, validation, and final wording.

## Functional and latency summary

| Measure | Result |
|---|---:|
| Turns | 15 |
| Call 1 calls | 15 |
| Call 2 calls | 15 |
| Realization calls | 15 |
| Cognitive pipeline completed | 15 / 15 |
| Accepted shadow realizations | 15 / 15 |
| Worker retries | 0 |
| Turns carrying warnings | 7 / 15 |
| Warning instances | 8 |

Warnings were five `unsupported_claim_detected`, one
`content_alignment_missing`, one `attribution_dropped`, and one
`hidden_affect_asserted`.

Nearest-rank latency:

| Measure | Median | P95 / maximum |
|---|---:|---:|
| Visible response complete | 5,750 ms | 12,199 ms |
| Call 1 provider | 3,208 ms | 7,774 ms |
| Call 2 provider | 1,731 ms | 4,790 ms |
| Separate Realization | 1,750 ms | 2,134 ms |
| Shadow processing | 8,264 ms | 12,883 ms |
| Capture to terminal | 8,371 ms | 13,001 ms |

Median input size was 10,854 tokens for Call 1 and 2,376 tokens for Call 2.

## Result by requested capability

| Capability | Result | Evidence |
|---|---|---|
| Remember the topic | **Fail** | The shadow could not recall that hiking was the other option, despite the original turn being present in dialogue memory. |
| Resolve references | **Fail** | “The first one” became an unresolved gap; “the other option” was bound to a whole proposition rather than an option; ambiguous “she” was not represented as ambiguous. |
| Apply corrections | **Partial, then fail** | Call 1 correctly parsed “hiking, not painting,” and the same-turn response acknowledged it. The next turn still answered that it did not know the current preference. |
| Distinguish missing knowledge from ambiguity | **Fail at response level** | Genuine ambiguity, absent facts, clear external knowledge, and an unresolved follow-up all became variants of “I don’t know.” |
| Advance the conversation | **Weak** | One exploratory emotional response advanced the exchange. Later responses reduced the new distinction to a paraphrase and asked the user to restate an already established difficulty. |

## Detailed failure localization

### 1. Topic and option memory: meaning is lost before Call 1 can reason over it

The user first said:

> “I'm deciding whether to spend Saturday painting or hiking.”

The next turn referred to “the first one.” The visible assistant correctly
understood painting. The shadow instead reflected the unresolved phrase itself:

> “You seem to be leaning toward the first one...”

On the direct recall check—“What was the other option I mentioned?”—the shadow
answered:

> “I don’t know what the other option was.”

**Intermediate evidence**

- Dialogue memory contained the complete original user utterance and later
  visible utterances. The frontend/backend did not lose the transcript.
- The memory snapshot had `active_option_set_refs: []` and
  `active_topic_refs: []`.
- It did not create separate durable entities for painting and hiking. It kept
  “Saturday” as an entity and the whole sentence as a proposition.
- “the first one” became a `gap` with `cue_kind=ordinal_option`,
  `resolution=no_candidate`, and an empty candidate list.
- On the recall turn, Call 1 emitted `elaboration_request` and bound
  `referent_phrase` to the whole earlier proposition. It emitted no unknown and
  no conflict.
- The deterministic content boundary then produced
  `trusted_content_unavailable`; Call 2 could only select acknowledgement or
  `provide_information.answer`.
- Realization was forbidden from using unregistered model knowledge and produced
  the generic non-answer.

**Failure location:** dialogue-memory projection/option representation first;
Call 1 then receives an impoverished reference table; the trusted-content gate
and Realization faithfully preserve that loss.

### 2. Correction: understood now, not incorporated into durable belief state

The correction was:

> “Actually, I meant I keep leaning toward hiking, not painting.”

The same-turn shadow response was correct:

> “I hear you: you keep leaning toward hiking, not painting.”

But the immediate follow-up—“Given that correction, which activity am I leaning
toward?”—received:

> “I don’t know what activity you’re leaning toward.”

**Intermediate evidence**

- Call 1 correctly emitted a `correction` with `corrected_value=hiking`,
  `prior_value=painting`, plus a statement of the new preference.
- The next snapshot contained three current propositions: the original option
  sentence, the “first one” preference, and the correction.
- All three remained `status=current`. None had `superseded_by_ref(s)`.
- No normalized preference relation linked the user, “leaning toward,” and
  hiking.
- On the follow-up, Call 1 correctly recognized a clear `information_request`
  with low uncertainty, but it had no resolved proposition to answer from.
- The same `trusted_content_unavailable` path produced a generic non-answer.

**Failure location:** the Call 1 correction is good; the dialogue-memory updater
does not compile the correction into supersession or an updated canonical
proposition. The meaning is lost between the accepted correction and the next
turn.

### 3. Genuine ambiguity versus absent fact: the epistemic reason is not retained

The deliberately ambiguous sentence was:

> “Maya lent Priya a novel because she had finished it.”

Asked “Who had finished it?”, the shadow said:

> “I don’t know who had finished it.”

After the user resolved it to Maya, the unrelated ownership question—“Who owns
the novel?”—received:

> “I don’t know who owns the novel.”

Those responses are both cautious, but they do not distinguish two different
epistemic situations:

- ambiguous: two supported readings exist for “she”;
- missing: the dialogue contains no ownership fact.

**Intermediate evidence**

- On the ambiguous source sentence, Call 1 emitted only a generic `statement`.
  It emitted no unknown, conflict, alternative readings, or pronoun-resolution
  candidates.
- The memory reference table later retained Priya and the whole proposition, but
  not the competing Maya/Priya antecedents for “she.”
- On “Who had finished it?”, Call 1 emitted a normal low-uncertainty
  `information_request`, again with no unknown or conflict.
- On “Who owns the novel?”, Call 1 produced the same shape: a low-uncertainty
  information request with no explicit missing-fact record.
- Both turns received a question object plus
  `trusted_content_unavailable`; both selected `provide_information.answer`;
  Realization had no typed epistemic reason to express.

**Failure location:** Call 1/ontology representation for pronoun alternatives
and missing-fact status. Downstream stages cannot verbalize a distinction that
does not exist in the accepted understanding.

### 4. A genuine unresolved follow-up is detected, then dropped

After “What causes tides on Earth?”, the user asked:

> “How long does it take?”

Call 1 did better here: it emitted an `elaboration_request` with a null referent
and an unknown `referent_phrase`. The final response was still:

> “I don’t know how long it takes.”

**Intermediate evidence**

- The unresolved referent existed in Call 1's accepted output.
- It was not projected into the grounded objects supplied to action
  construction. Only a generic question object remained.
- No `seek_information.clarify` candidate was eligible.
- `trusted_content_unavailable` was treated as the blocking condition, and Call
  2 selected `provide_information.answer`.

**Failure location:** this meaning survives Call 1 but is lost in
understanding-to-action grounding/candidate construction.

### 5. Clear knowledge questions are understood but intentionally starved of content

For “What causes tides on Earth?” and the clarified high-to-low-tide duration,
Call 1 correctly recognized clear information requests with low uncertainty. The
shadow responses were:

> “I don’t know what causes that.”

> “I don’t know how long that takes.”

The visible assistant answered both questions correctly and specifically.

**Intermediate evidence**

- Call 1 did not confuse these questions with ambiguity.
- The content path supplied no trusted answer, producing
  `trusted_content_unavailable`.
- `provide_information.answer` remained selectable, but Realization's global
  constraints forbade `answer_from_model_knowledge` and unsupported claims.

**Failure location:** content/knowledge policy, not semantic parsing. The current
action name says “answer,” while its only authorized realization is effectively
an admission that no grounded answer exists.

### 6. Conversation advancement: action expansion helps, but context synthesis does not

On the first emotional turn, the shadow selected reflection plus optional
exploration and asked:

> “What feels weird about it to you?”

This advanced the conversation. The user then supplied the key distinction:

> “It's not that I don't like them; I'm just exhausted.”

The shadow only replied:

> “It sounds like you’re just exhausted.”

Finally, the user prohibited advice and asked what was making the situation
difficult. The shadow said:

> “I hear you. What about this feels difficult?”

That question asks the user to restate the question they just asked and does not
use the established tension between caring about the friend and being exhausted.

**Intermediate evidence**

- Call 1 correctly represented the first disclosure as experience plus concern.
  Call 2 had reflection, exploration, and validation candidates and selected a
  useful reflection/exploration pair.
- On the distinction turn, Call 1 captured `tired` and a
  `repair_shared_understanding` reading, but Call 2 selected reflection alone.
- On the final turn, Call 1 correctly represented both the no-advice action
  boundary and a conversation request for perspective.
- It nevertheless projected “this feel difficult” as an unresolved goal.
- Call 2 was offered acknowledgement, clarification, and goal exploration—not a
  grounded perspective/interpretation action using prior turns—and selected
  acknowledgement plus clarification.
- Realization followed that plan. Its weak response is downstream of the action
  candidate set rather than an isolated wording failure.

**Failure location:** context-to-goal grounding and the available conversational
action/content representation. Realization cannot synthesize a perspective that
the accepted plan does not authorize.

## Why the visible assistant performed better

The live visible assistant correctly answered “hiking” on both memory checks,
identified the Maya/Priya ambiguity, distinguished unknown ownership, answered
the tide questions, and synthesized plausible emotional tensions on the final
turn.

That comparison confirms that:

- the WebSocket conversation history is available;
- the user prompts are not intrinsically too ambiguous;
- the failures arise inside the shadow's typed memory, grounding, content, and
  action boundaries rather than at frontend capture.

The visible response is not evidence that the shadow succeeded; it is a useful
control showing what information was present in the application conversation.

## Separate personality, affinity, and state readiness check

The requested controlled behavioral test is deferred because the basic
continuity suite failed. Current trace evidence nevertheless shows:

### Personality

- Columbina's action profile was present on every tested turn: curiosity 0.88,
  warmth 0.80, directness 0.62, and playfulness 0.58.
- Call 2 cited warmth and curiosity as selection factors on the emotional turns.
- Realization received the personality-derived delivery profile.
- This proves wiring and use, **not** appropriate behavioral differentiation. A
  paired profile ablation would be meaningful only after both profiles receive
  the same correct understanding, references, and action opportunities.

### Affinity / relationship

- Call 2 reported `relationship.availability=unavailable` and
  `relationship.dimensions=[]`.
- No affinity-conditioned behavioral test is currently possible through this
  real path.

### Dynamic state

- State snapshots were supplied and used by Realization controls.
- Activation rose mechanically with several question/correction sequences (for
  example 25 -> 46 -> 67 -> 88), and social alignment rose after corrections.
- In the three-turn emotional sequence, activation, engagement, strain,
  alignment, and valence did not change at all. Valence remained neutral and
  strain remained low despite discomfort and exhaustion.
- The derived controls also stayed effectively fixed: exploration readiness was
  very low and slow-cognition pressure high.

This does not yet demonstrate appropriate state-conditioned behavior. It shows
that state is wired but that its updates are not tracking the salient emotional
changes in this corpus.

## Grounded priority order

The trace evidence supports this dependency order for future work:

1. represent option sets, alternative members, and ordinal references in
   dialogue memory;
2. compile corrections into canonical replacement/supersession that survives
   the next turn;
3. preserve alternative referents and typed epistemic reasons such as
   `ambiguous_reference`, `missing_dialogue_fact`, and
   `external_knowledge_required`;
4. project Call 1 unknowns into action-grounded objects so clarification can
   actually become eligible;
5. provide a trusted general-knowledge content path or rename/restrict the
   current answer behavior so it does not masquerade as an answer;
6. add a perspective/synthesis action grounded in prior propositions so a
   conversation can advance beyond reflection and clarification;
7. only then run controlled personality variants, add a real affinity
   projection, and test state-conditioned behavior separately.

## Bottom line

The pipeline now completes reliably and can choose richer social actions, but
its conversational substrate is still too lossy. It often has the transcript
without having a usable representation of what the transcript means across
turns. The main failures are therefore not random model wording errors: they are
localized to option/reference memory, correction persistence, epistemic typing,
understanding-to-action grounding, and trusted-content availability.

