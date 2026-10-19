# General-conversation action-exploration retest — 2026-10-06

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

## Scope

This evaluation tested the current `cognitive-two-call-v1` shadow pipeline after
adding optional exploration of an explicitly stated user experience. It used 15
turns across five conversations and the real application path:

```text
chat WebSocket -> backend persistence -> fresh isolated QA worker
  -> Call 1 understanding -> deterministic middle -> Call 2 action policy
  -> deterministic plan validation -> separate Realization
```

Shadow isolation remained enabled. Call 1, Call 2, and Realization used real
`gpt-6-luna` calls. The visible assistant response and shadow response were
measured and reviewed separately. Each continued turn waited for the preceding
shadow trace, so conversation history and dialogue memory could be used.

The five conversations covered:

1. casual contact followed by personal and emotional disclosure (4 turns);
2. a conceptual question, correction, and follow-up (3 turns);
3. disagreement followed by vulnerability (3 turns);
4. alternatives, a reference, and a correction (3 turns);
5. social ambiguity and an explicit no-advice boundary (2 turns).

## Functional result

| Measure | Result |
|---|---:|
| Turns | 15 |
| Call 1 model calls | 15 |
| Call 2 model calls | 15 |
| Cognitive pipeline completed | 15 / 15 |
| Accepted shadow realizations | 15 / 15 |
| Terminal failures | 0 |
| Worker retries | 0 |
| Turns with validation warnings | 5 |
| Total validation warnings | 6 |

Warnings were three `unsupported_claim_detected`, one
`content_alignment_missing`, one `unplanned_move_detected`, and one
`attribution_dropped`. These were tolerated by the current conversational shadow
policy and therefore did not prevent acceptance.

## Latency

Values below use persisted shadow traces and the repository `trace_summary`.
P95 uses nearest-rank; with 15 samples it is the maximum.

| Measure | Median | P95 / maximum |
|---|---:|---:|
| Visible first token | 567 ms | 1,106 ms |
| Visible response complete | 1,295 ms | 1,916 ms |
| Call 1 provider | 4,065 ms | 5,849 ms |
| Call 2 provider | 1,794 ms | 2,488 ms |
| Separate Realization | 1,801 ms | 4,466 ms |
| Shadow processing | 8,229 ms | 11,190 ms |
| Capture to shadow terminal | 8,259 ms | 11,208 ms |
| Queue wait | 27 ms | 43 ms |

Fresh-conversation shadow median was 7,281 ms (five turns). Continued-turn
median was 8,731 ms (ten turns). Call 1 remained the dominant model call, with a
median 10,863 input tokens; Call 2 had a median 2,542 input tokens.

The visible response was ready much earlier than the shadow candidate. The
visible latency cannot be presented as cognitive-pipeline latency.

## Action-selection result

The optional `seek_information.explore_experience` candidate was offered on 7
of 15 turns and selected on 2. This is a useful sign: the new action is reachable
without becoming a mandatory question on every disclosure.

| Selected action | Count |
|---|---:|
| `give_feedback.reflect` | 7 |
| `give_feedback.acknowledge` | 4 |
| `provide_information.answer` | 4 |
| `seek_information.explore_experience` | 2 |
| `support_socially.validate_experience` | 2 |
| `manage_dialogue.greet` | 1 |

Eleven turns selected one move, three selected two moves, and one selected three
moves. The three-move plan—reflect, validate, and explore a disappointing social
experience—passed the focal-primary gate that failed in the preceding one-turn
run.

## Turn-by-turn quality review

This review concerns the shadow wording. Acceptance only establishes that the
candidate passed the configured contracts.

| Turn | Selected action(s) | Shadow response | Assessment |
|---|---|---|---|
| A1 casual greeting | greet | “Hey, good to see you too.” | Natural and proportionate. An `unplanned_move_detected` warning suggests the realization/action alignment is still overly sensitive. |
| A2 conversation without task | reflect | “It sounds like you just felt like talking.” | Relevant but mechanically repeats the user and does not help the conversation start. |
| A3 personal observation | reflect | “It sounds like rainy evenings make everything feel slower to you.” | Accurate but purely paraphrastic; an exploratory move was not chosen. |
| A4 loneliness disclosure | reflect | “It sounds like tonight it feels lonely for you.” | Emotionally appropriate but thin. It does not offer presence or invite continuation. |
| B1 conceptual question | answer | “I don’t know why that feels different at night.” | Honest under the closed-world contract, but not useful conversation and much weaker than the visible response. |
| B2 correction | acknowledge | “I hear you—you mean emotionally, not the audio quality.” | Correctly accepts the correction, but does not continue the explanation. |
| B3 memory follow-up | answer, answer | “I don’t know whether memories are part of it. I’m not sure what ‘that’ refers to.” | Clear continuity failure. “That” is resolved by the immediately preceding discussion. The duplicate answer moves did not improve the result. |
| C1 opinion/disagreement | answer | “I don’t know whether every hobby eventually needs to make money.” | It avoids automatic agreement, which is good, but does not express a meaningful independent perspective. |
| C2 meta-follow-up | acknowledge | “I hear you.” | Fails to engage with “You sound unconvinced”; it should own or clarify its stance. |
| C3 guilt/vulnerability | reflect, explore | “It sounds like you may feel guilty doing things just for fun. What comes up for you when you think about that?” | The new exploration behavior works and naturally continues the discussion. “May feel” unnecessarily weakens an explicit statement. |
| D1 alternatives | acknowledge | “I hear you’re considering Saturday at a museum or by the river.” | Grounded but only restates the alternatives. |
| D2 reference | reflect | “You said the second one feels better today.” | Does not surface that “the second one” means walking by the river, so reference understanding remains hidden and conversationally weak. |
| D3 correction | acknowledge | “I see your correction about the museum.” | Tracks the correction to the museum correctly. Still sounds like protocol bookkeeping rather than ordinary dialogue. |
| E1 social ambiguity | reflect, validate, explore | “You seem unsure what to make of it. It’s understandable to feel disappointed and unsure. What are you unsure what to make of?” | The richer three-action plan executes, but the final question is redundant: the friend’s repeated cancellation is already the stated uncertainty. |
| E2 no-advice boundary | reflect, validate | “It sounds like you’re trying to make sense of what bothers you. I can see why you’d want to talk about why it bothers you.” | Respects the no-advice boundary. It is supportive but stops short of actually exploring why it hurts. |

An informal manual quality classification gives approximately 5 good turns, 8
partial turns, and 2 poor turns. The two clearest failures are B1 (a non-answer)
and B3 (loss of an obvious conversational referent). This rating is qualitative,
not a contract metric.

## Conclusions

The action-exploration change works mechanically. Optional exploration is now a
real model choice, multi-action plans can pass validation, and the pipeline
completed all 15 turns without retry or rejection. It also did not over-question:
the model chose exploration on only two of seven opportunities.

The shadow is nevertheless not yet consistently good general conversation.
The dominant remaining problems are:

- reflection often becomes a near-verbatim restatement rather than a response;
- the closed-world answer action produces “I don’t know” for ordinary conceptual
  questions;
- dialogue continuity can lose an obvious antecedent on a follow-up;
- acknowledgements of corrections and meta-comments sound like protocol status;
- richer action plans can realize as redundant clauses or redundant questions;
- warnings are tolerated even when they correspond to noticeable wording flaws.

The new action space is a meaningful improvement over the earlier one-action
behavior, but action availability was only one bottleneck. Call 2 now has more
room to choose; grounded informational content, multi-turn reference resolution,
and Realization quality are the next constraints on human-like behavior.

