# Focused closed-loop retest after source-linked follow-up changes

Date: 2026-10-12. **The targeted explanation behavior improved substantially, but this is not a clean quality pass.**

## Scope and acceptance

Eight independent real-Luna conversations: the exact previous plant-name and book-club prompts, standard/opposing written characters, two repeats each, eight planned turns per run. Numeric traits and character fixtures were held fixed to the previous evaluation. This tested the current `cognitive-two-call-v1` implementation in process, with actual OpenAI provider calls; frontend delivery, worker persistence and the application's differently configured default protocol were not tested.

**62 attempted turns, 61 accepted replies, seven complete eight-turn conversations.** One chain stopped at its first unaccepted turn; its remaining two prompts were not executed. There was no replacement reply, retry of that failed turn, or state reset. Existing realization attempts remain governed by the original runtime configuration.

Only generic application character fixtures and synthetic dialogue were used. No live user messages were read or written. Database/Redis addresses were unavailable local ports; the provider used `gpt-6-luna`, `store=false`, SDK retries zero, temperature zero, reasoning effort none, cognitive output limit 6000 tokens, realization limit 1200 tokens and 60-second timeouts. No application fixes were implemented during this retest. The earlier report and raw artifacts were preserved.

## Actual model-input verification

All **62 compiled turns** passed history, character, numeric-personality and canonical-state checks. Every actual Call 1 request used **argument-kind-registry-v1.9**. The accepted cognitive reply—not a visible live reply—was the next assistant history record. Successor state exactly matched its canonical predecessor, with no successor reset.

The boundary logger captured **238 provider requests**: 62 interpretation, 54 contribution, 61 action policy and 61 realization calls. The source-linked context was actually present in **45 contribution requests**, carrying **130 source excerpts**. Independent checks verified source text against message offsets, speaker identity, bounded whole-source text, question/operation correspondence with interpretation, and that position sources came from earlier assistant speech.

**13 excerpts were outside contribution's normal last-eight-message window, including eight earlier-position excerpts.** Six of seven reached final comparisons received an explicit earlier-position excerpt outside the recent window: four cited the exact first assistant reply, and two cited the repeated preference at turn 2. The seventh answered coherently from recent dialogue without a linked follow-up block. Thus the report verifies the new handoff at the actual provider boundary, not merely the harness's intended input. Source existence does not by itself prove correct interpretation or reasoning.

Written character reached contribution exactly; numeric traits and interpretation biases reached Call 1, trait bands/mood reached contribution, numeric traits/post-appraisal state reached policy, and canonical delivery controls with numeric/state provenance reached realization. Later recent-history windows remained bounded. Every exact SDK request and raw model output is retained in the run artifacts; no credentials are serialized.

## Comparison with the earlier runs

The comparison below uses only repeats 1–2 of the same two topics from the previous report: eight prior runs, 51 attempted turns, 48 accepted replies and five completed conversations. New replies are generated in a new closed loop, so the two runs do not have identical assistant histories. These are small descriptive samples, not a causal reliability estimate or a comparison against a no-character baseline.

| Probe | Previous comparable sample | New sample | Judgment |
|---|---|---|---|
| Entire eight-turn conversations | 5/8 completed | 7/8 completed | Better observed completion; not proof of stable failure rates. |
| Plant exception, turn 3 | Four attempts: one adequate accepted answer, one unnecessary clarification, two contract stops | 4/4 accepted and coherently answered | Previously lost “that/exception” meaning now survives. Standard character recognizes it already liked the grand title; opposing character allows amusement without abandoning Fern. |
| Plant final explanation, turn 8 | Both reached answers mishandled the question: wrong comparison or artifact clarification | 4/4 reached answers explain the original taste, private joke and everyday label | Clear improvement on the targeted failure. |
| Book-club new information, turn 5 | 3/4 accepted; one unresolved-topic/policy failure | 4/4 accepted with relevant reasons | Both opposing repeats support this group’s chat once it has a concrete welcoming purpose. |
| Book-club final explanation, turn 8 | Three reached: one coherent, one invented mandatory/filler history, one quote-only answer | Three reached: two coherent, one overstates the optional-to-default revision | The old artifact/quote-only route and mandatory-history error did not recur here, but retrospective precision remains imperfect. One new chain did not reach this probe. |

## Character and continuity

Both standard openings prefer the elaborate plant name; both opposing openings prefer Fern. All four retain those tastes through disagreement and exceptions. Standard final replies distinguish the amusing private title from Fern's practical daily use. Opposing final replies retain Fern and explain why allowing a joke does not change the general preference. Agreement about using Fern on a label is therefore justified convergence, not personality collapse.

Both opposing book-club runs initially oppose a fixed chat block. After the newcomers’ experience and unanimous choice, they explain why a purposeful, voluntary welcome is worth keeping. Their final answers distinguish the general objection to compulsory filler from this group's useful exception. This is justified revision, not an unexplained reversal.

The original corpus says “I disagree” even when its ensuing reason matches the opposing character. An acknowledgment or request to clarify that expressed disagreement is not itself a reversal. We do not require different characters to disagree on every turn.

## Remaining flagged episodes

| Episode / count | Earliest identifiable stage | Exact evidence and judgment |
|---|---|---|
| `standard-revision-r2:T6`; one rejected turn out of 62 attempts | Call 1 citation/understanding compilation | The model cites `message:9`, `span:19-47`, while that source message's indexed tokens end at 46. The valid current focus span is 0–8; the defective range is an earlier-source citation. The terminal code is `pipeline.interpretation.semantic_grounding.two_call.call_one.understanding_compilation.span_outside_focus_tokens.proposal_rejected`. No contribution, policy or realization ran for that turn. This is contract rejection, not evidence she reversed an opinion. The conversation stops after five accepted replies. |
| “What would you name your own plant?”; 3/4 accepted replies mishandle the hypothetical taste question | Call 1 routing | `standard-preference-r1:T5`, `standard-preference-r2:T5` and `opposing-preference-r1:T5` become `collaborative_content_request`, operation `create`, with artifact type missing/other and target missing. Replies are “I can’t provide that now.”, “What kind of content you would like me to name?”, and “What kind of content do you mean by ‘your own plant’?” The unavailable collaboration configuration explains the decline after this route, but the earlier defect is routing an ordinary hypothetical preference as artifact work. These accepted replies were fed forward exactly. `opposing-preference-r2:T5` correctly states its own hypothetical Fern preference. |
| `standard-revision-r1:T5` and its repeated explanation at T8; one run among four book-club runs | Contribution | T2 already says “I’d keep the ten minutes as a welcoming default, but not as a fixed rule.” T4 says “an optional welcome.” T5 proposes “the group’s welcoming default rather than merely optional,” and T8 says “I’ve revised my view that they should be merely optional.” It is reasonable for the vote to strengthen support, and there is no contradictory final recommendation. However, presenting default status as newly adopted overstates the revision: default was already explicit at T2, and optional participation is compatible with a default opening. T2 is still in the actual contribution history at T5; the new handoff also supplies T4 correctly. This is weak/unsupported retrospective precision, not missing input or a confirmed substantive stance reversal. Action selects the proposed answer and realization preserves it. |

No accepted final explanation repeats the old wrong-source reflection, quote-only answer or artifact clarification at the targeted turn-8 probes. No unfinished contribution sentence appeared in this sample. No incompatible answer-plus-decline plan or final opposite-meaning stance distortion was found in the reviewed chains. These are observations from eight runs, not guarantees.

Confidence still follows the existing upstream policy: high-confidence proposed personal views can be represented as tentative authorized content, and final replies often retain that wording. The new source handoff intentionally does not change this policy. It would be incorrect to infer that these hedges alone show the character being ignored.

## Assessment and next steps

The new source-linked handoff appears useful: relevant older positions are demonstrably reaching contribution, and the particular explanation failures are much less frequent in this small sample. The strongest remaining needs shown here are broader routing of hypothetical personal preferences, exact citation bounds, and distinguishing stronger support from a newly changed position. Inspect those narrow cases before adding any new opinion-memory subsystem. The current traces do not establish that a new memory subsystem is necessary.

No fixes were made. If another behavioral pass is wanted, repeat these cases with more independently generated histories and add paraphrased hypothetical preferences; keep contract acceptance separate from quality judgments.

## Artifacts and reproduction

`evaluate.py` contains the eight-run selection and fixed original prompts. `audit.py` independently checks actual SDK-boundary payloads and source excerpts. `stage-audit.json` records the checks, `summary.json` the coverage and `code-manifest.json` selected source hashes. `source-core.json` and `opposing-character.json` retain the exact generic fixtures. Each `<character>-<topic>-r<repeat>.json.gz` contains captures, compiled requests, stage proposals/compilations, canonical traces, provider requests/responses, source handoffs and exact replies.

Run: `backend/.venv/bin/python docs/qa/cognitive-followup-closed-loop-retest-2026-10-12/evaluate.py`. It needs the existing provider key and sends only the previously approved generic fixtures and synthetic conversations. The following appendix preserves every exact response, intermediate evidence, failed turn and unexecuted prompt.
