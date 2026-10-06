# General-conversation two-call Cognitive evaluation — 2026-10-05

## Scope

This evaluation intentionally excludes meal planning, nutrition, and food-image
tasks. It measures the Cognitive system as a general conversational agent from
simple dialogue through moderately complex reasoning.

The 13 synthetic turns covered:

- greeting and casual conversation;
- vague emotional disclosure and continued reflection;
- a request for help thinking through tomorrow;
- a correction rejecting schedule/task mode;
- a simple explanatory question and plain-language follow-up;
- correction of the intended meaning of a question;
- creative naming and revision;
- an ambiguous request requiring clarification;
- nuanced social interpretation and communication advice.

The execution path was:

```text
chat WebSocket
  -> backend persistence
  -> fresh isolated QA worker
  -> two-call Cognitive shadow pipeline
  -> deterministic compilation and realization validation
```

Every continued turn waited for its predecessor shadow trace to finish. Shadow
isolation remained enabled throughout.

## Result summary

| Measure | Result |
|---|---:|
| General-conversation turns | 13 |
| Provider calls | 22 |
| Turns reaching Call 2 | 9 |
| Core completed | 6 |
| Core failed | 7 |
| Accepted shadow realization | 4 |
| Rejected realization | 2 |
| Realization not requested | 7 |
| Worker retries | 0 |
| Proposed structured artifacts | 2 |

The functional success rate on general conversation was substantially worse
than the earlier food-oriented corpus. Only 4 of 13 turns produced an accepted
shadow candidate. Of those four, one was clearly useful, two were minimally
responsive acknowledgments, and one was an irrelevant clarification.

## Latency

Milliseconds are reported from durable trace spans. With 13 samples,
nearest-rank p95 equals the maximum observation.

| Measure | n | Median | p95 / maximum |
|---|---:|---:|---:|
| Visible first token | 13 | 780.22 | 2,072.89 |
| Visible response complete | 13 | 2,319.77 | 10,710.97 |
| Call 1 provider | 13 | 4,871.36 | 7,193.28 |
| Call 2 provider | 9 | 2,321.39 | 12,023.47 |
| Shadow processing | 13 | 7,498.63 | 17,356.39 |
| Capture to shadow terminal | 13 | 7,532.96 | 17,381.77 |
| Accepted shadow processing | 4 | 7,217.86 | 11,344.59 |

The 7.50-second overall median is misleading because four turns failed after
Call 1 and never paid the cost of Call 2. The most substantial request took
17.36 seconds and still failed. The one clearly useful accepted reflective
answer took 11.34 seconds.

### Fresh versus continued

| Scope | Turns | Call 1 median | Call 2 median | Shadow median |
|---|---:|---:|---:|---:|
| Fresh conversations | 5 | 5,196.55 | 7,172.43 (n=2) | 5,225.01 |
| Continued conversations | 8 | 4,684.47 | 2,309.96 (n=7) | 7,578.61 |

Fresh-turn latency looks lower only because three of five fresh turns failed
after Call 1. It is not evidence that fresh conversations are intrinsically
faster. Cached input appeared on 5 of 13 turns: two Call 1 hits around 1.85k
tokens and three Call 2 hits around 2.32k tokens. No turn had cache hits on both
calls.

## Per-turn latency and outcome

Seconds are rounded to two decimals. A dash means Call 2 did not run.

| Turn | Visible first / complete | Call 1 | Call 2 | Shadow total | Result |
|---|---:|---:|---:|---:|---|
| A1 casual greeting | 2.07 / 3.03 | 7.19 | 2.32 | 9.75 | realization rejected |
| A2 vague feeling | 0.74 / 1.41 | 5.63 | 1.84 | 7.66 | accepted |
| A3 follow-up context | 0.94 / 1.58 | 4.29 | 2.31 | 6.78 | accepted |
| A4 reflective help | 1.00 / 3.05 | 3.34 | 7.75 | 11.34 | accepted |
| A5 correction: no schedule | 0.86 / 2.01 | 5.91 | 1.77 | 7.87 | core failed |
| A6 substantial reflection | 1.05 / 1.93 | 4.87 | — | 4.91 | Call 1 rejected |
| B1 simple explanatory question | 0.75 / 3.21 | 3.37 | — | 3.40 | Call 1 rejected |
| B2 plain-language follow-up | 0.84 / 2.34 | 5.66 | 6.67 | 12.60 | realization rejected |
| B3 meaning correction | 0.78 / 3.48 | 4.50 | 2.88 | 7.50 | core failed |
| C1 creative brief | 0.67 / 2.32 | 5.20 | — | 5.23 | Call 1 rejected |
| C2 creative revision | 0.58 / 1.57 | 3.65 | 1.81 | 5.84 | accepted but irrelevant |
| D1 ambiguous request | 0.67 / 1.72 | 3.96 | — | 3.98 | Call 1 rejected |
| E1 complex social reasoning | 0.78 / 10.71 | 5.23 | 12.02 | 17.36 | core failed |

## Turn-by-turn wording and quality review

### A — six-turn reflective conversation

#### A1 — casual greeting

User: `Hi Columbina, nice to see you. How has your day been?`

Shadow wording:

> Hi! It’s lovely to see you too. My day has been peaceful, thank you for asking. How has yours been?

The wording is natural, but realization rejected it with
`unplanned_move_detected`, two `unplanned_question` findings, and
`output_too_long`. Incidental greeting/acknowledgment findings were warnings,
but the reciprocal social question remained a hard gate. No artifact was
created.

#### A2 — vague emotional disclosure

User: `I've been feeling scattered lately.`

Shadow wording:

> I hear you—you’ve been feeling scattered lately.

Accepted with no artifact. It is appropriate and grounded, but it only mirrors
the disclosure and does not invite or deepen the conversation.

#### A3 — follow-up context

User: `Mostly because I have too many small things on my mind.`

Shadow wording:

> You said you have too many small things on your mind.

Accepted with no artifact. This is a literal paraphrase rather than a
human-like conversational response. It demonstrates continuity at the message
level but little interpretation or engagement.

#### A4 — reflective help

User: `Can you help me think about how to make tomorrow feel less overwhelming?`

Shadow wording:

> I hear you: you’ve been feeling scattered, with lots of small things on your mind. Here’s a gentle shape for tomorrow: try choosing one small, useful thing to focus on first, writing down the small tasks competing for your attention, and leaving breathing room between tasks.

This was the strongest accepted candidate: it used earlier context, stayed
gentle, and gave practical advice. It also created a structured artifact titled
`A gentler shape for tomorrow` with sections `Start small` and `Make room`.
The artifact was coherent but unnecessary for an ordinary conversation.

Most importantly, the stored content item classified this general life advice
as:

```text
claim_kind: culinary_suggestion
```

That is direct evidence of food-domain vocabulary leaking into general
conversation.

#### A5 — correction rejecting schedule mode

User: `Actually, I don't want a schedule. I just want to talk through what matters most.`

No shadow wording or artifact was produced. Both calls completed, but action
selection rejected Call 2 with:

```text
behavior_proposal_ineligible.goal_or_target_mismatch.step_0
```

The visible assistant handled the correction appropriately; the Cognitive
pipeline could not represent or select the revised conversational stance.

#### A6 — substantial reflection

User: `The important part is making time to call my sister and finish one difficult email. What do you think?`

Call 1 completed, then deterministic interpretation rejected its proposal with
`semantic_parser.disallowed_argument`. Call 2 and realization never ran. This
is a normal reflective statement plus a request for an opinion, not an unusual
or unsafe input.

### B — explanatory question with follow-ups

#### B1 — simple explanatory question

User: `Why do people sometimes remember embarrassing moments more vividly than ordinary ones?`

Call 1 completed in 3.37 seconds but was rejected with
`semantic_parser.disallowed_argument`. No shadow wording was produced. The
visible assistant answered normally. This shows that the interpretation
contract cannot reliably represent ordinary general-knowledge questions.

#### B2 — plain-language follow-up

User: `Could you explain that without psychology jargon and give one everyday example?`

Shadow wording:

> What are you referring to? Strong feelings and replaying a moment can make it stand out in memory. For example, if you stumble over your words while introducing yourself, you might replay that awkward moment later, while an ordinary chat fades into the background.

The explanation itself is clear and useful, but `What are you referring to?`
is unnecessary because the prior user turn is explicit. Realization rejected
the candidate for `content_alignment_missing`.

The pipeline also created a structured artifact titled
`Why embarrassing moments can stick`. Its content was sensible, but its stored
claim kind was again `culinary_suggestion` rather than `general_practice` or
another general explanatory class. The artifact therefore passed structural
compilation while carrying semantically wrong domain metadata.

#### B3 — correction of intended meaning

User: `Small correction: I'm asking why the memory feels vivid, not whether it is accurate.`

Both calls completed, but action selection rejected the proposal with the same
`goal_or_target_mismatch.step_0` family seen on A5. No wording or artifact was
produced.

### C — creative naming and revision

#### C1 — creative brief

User: `Help me brainstorm a name for a quiet neighborhood book club. It should feel warm and curious, not pretentious.`

Call 1 completed, but reference compilation rejected it with
`mind_reference_compilation.proposal_rejected`. No brainstorming candidate was
produced.

#### C2 — creative revision

User: `I like ‘The Open Chapter,’ but make it a little more playful without sounding childish.`

Shadow wording:

> Which plan or draft you mean?

This was accepted despite being irrelevant and grammatically incomplete. The
previous creative turn had failed, so no accepted artifact or response existed
for the revision to target. The collaboration operation was skipped as
`goal_blocked`. Contract acceptance did not correspond to useful conversation.

### D — ambiguous request

User: `What should I do next?`

This should naturally produce a brief clarifying question. Instead, Call 1 was
rejected with `semantic_parser.disallowed_argument`; Call 2 never ran. The
visible assistant correctly asked what area of life or work the user meant.

### E — moderately complex social reasoning

User: `A friend keeps canceling plans but apologizes sincerely. Help me interpret a few possibilities without assuming bad intent, then suggest how I could talk to them.`

Both model calls ran. Call 2 alone took 12.02 seconds and emitted 1,322 output
tokens, but content compilation rejected it with
`compiler.content_invalid`. No final shadow wording or artifact survived. Total
shadow processing was 17.36 seconds. The visible assistant produced a nuanced,
useful response in 10.71 seconds.

## Failure distribution

The seven core failures were:

- `semantic_parser.disallowed_argument`: 3 turns — substantial reflection,
  simple explanation, and an ambiguous request;
- `goal_or_target_mismatch.step_0`: 2 turns — both corrections;
- `mind_reference_compilation.proposal_rejected`: 1 creative brief;
- `content_compilation.compiler.content_invalid`: 1 complex social-reasoning
  response.

The two completed-but-rejected realizations were:

- casual greeting: plan/text mismatch, unplanned reciprocal questions, and
  length;
- plain-language explanation: missing content alignment.

## Main findings

### 1. General conversation is not functionally reliable

Only 4 of 13 turns produced accepted wording. Basic explanatory, creative,
ambiguous, corrective, and nuanced social turns failed at different contract
boundaries. The system currently works better for a narrow subset of
acknowledgment and structured collaboration than for ordinary conversation.

### 2. Call 1 is a major general-domain bottleneck

Four turns stopped after Call 1. Three were rejected for a disallowed semantic
argument, including a simple `why` question and an ordinary clarification case.
The semantic ontology/compiler boundary is too narrow or the prompt/schema does
not constrain the model tightly enough for common general conversation.

### 3. Food-domain metadata leaks into non-food content

Both compiled general artifacts used `claim_kind: culinary_suggestion`. The
collaboration vocabulary exposes culinary claims even when the artifact and
subject are unrelated to food. The system is therefore structurally capable of
general artifacts but does not enforce domain-appropriate claim vocabularies.

### 4. The system overuses artifacts

The useful reflective response and psychology explanation were both converted
into structured artifacts even though the user asked for conversation and an
explanation. This adds complexity, latency, and new validation surfaces without
improving the interaction.

### 5. Corrections remain fragile

Both correction turns reached Call 2 and then failed with a goal/target
mismatch. The system did not smoothly revise its stance after `I don't want a
schedule` or refine the meaning of the memory question.

### 6. Acceptance still does not measure conversational quality

The accepted candidates included a redundant paraphrase and `Which plan or
draft you mean?`, while a natural greeting and a useful plain-language
explanation were rejected. Current gates measure structural alignment more
strongly than relevance, naturalness, or conversational usefulness.

### 7. Complex successful work is still around ten seconds or more

Early failures make the overall median look lower. The useful reflective turn
took 11.34 seconds; the complex social turn took 17.36 seconds and failed. The
sequential chain is not yet consistently fast enough for natural real-time
conversation.

## Architectural and training-data concerns for redesign

This section records concerns raised by the measured behavior rather than
prescribing a replacement architecture. The distinction matters: the test
does not show that explicit cognition, goals, evidence, or validation are
unnecessary. It shows that the present representation and enforcement burden
can dominate the cognitive and conversational task they are intended to
support.

### The same maximum-complexity path is applied to very different turns

A greeting, a vague emotional disclosure, an explanatory question, a creative
request, and a nuanced social-reasoning task all enter substantially the same
two-call structured pipeline. Even harmless conversational turns must survive
semantic frames, readings, eight-dimensional appraisal, evidence/context
bindings, goal formation, action selection, content bindings, realization
compilation, and boundary validation.

This makes the cost and failure surface poorly matched to the cognitive demand
of the turn. A simple greeting was rejected for an unplanned reciprocal
question, while the complex social-reasoning request failed much later after
17.36 seconds. The pipeline therefore pays high coordination cost on simple
turns without demonstrating reliable depth on difficult ones.

### Representational plumbing is entangled with cognitive correctness

The model outputs contain local observation, reading, evidence, context, goal,
choice, template, segment, content, and target handles. Correctness depends on
those references resolving across stages. Failures such as
`mind_reference_compilation` and `goal_or_target_mismatch` can reject an
otherwise plausible interpretation or response because two components
represented the target differently.

The resulting metric does not cleanly distinguish a bad cognitive judgment
from a bookkeeping error. This is a serious evaluation concern: the observed
failure rate cannot be read simply as “the model reasoned incorrectly,” because
some failures mean “the model understood the turn but did not encode that
understanding in exactly the expected reference structure.”

### The Call 1 target mixes cognition with protocol-specific representation

Call 1 predicts observations, predefined predicates and arguments, alternative
readings, evidence/context handles, eight appraisal values in positional order,
existing-goal impacts, unknowns, and conflicts. Some explanatory text is free
form, but the accepted meaning is substantially determined by the ontology and
its reference rules.

This creates ambiguity about what a future model trained on the traces would
learn. It may learn durable cognitive distinctions such as uncertainty and
conflict, but it may instead learn V0-specific predicate selection, positional
arrays, handle management, and validator-compatible phrasing. Those protocol
details are implementation artifacts rather than demonstrated cognitive
capabilities.

### Validation is currently a stronger signal than conversational quality

The test produced both false-looking rejections and false-looking acceptances:

- a natural greeting and a useful plain-language explanation were rejected;
- a literal restatement with little conversational value was accepted;
- the irrelevant and grammatically incomplete `Which plan or draft you mean?`
  was accepted;
- ordinary conversational additions such as a reciprocal question were treated
  as unauthorized moves.

Consequently, `accepted_shadow` currently means that the output satisfied the
implemented contracts. It does not establish that the response was relevant,
natural, appropriately curious, emotionally perceptive, or useful.

### Accepted-only training data would inherit rejection bias

If only accepted traces are retained as positive training examples, the corpus
will prefer responses that are easy for the current validators to prove. In
this run, that preference favored shallow acknowledgments and an irrelevant
clarification while excluding natural or useful wording. Training on that
selection could make a later model more contract-compliant while making it
less conversationally capable.

Rejected traces also lack a single consistent negative meaning. A rejection
may indicate poor reasoning, a disallowed argument, an unresolved internal
reference, a content-schema mismatch, or a final wording/alignment issue.
Treating all rejected traces as cognitively negative examples would combine
several different failure classes into a noisy supervision signal.

### Model-generated labels are not established cognitive ground truth

The structured interpretations and appraisals are proposed by a model and then
checked primarily for structural and referential validity. Passing those checks
does not demonstrate that the proposed reading, appraisal, goal impact, or
response strategy is the best psychological or conversational interpretation.

The corpus therefore risks becoming self-referential: V0 defines the allowed
ontology, a model generates labels inside that ontology, V0 accepts labels that
fit its rules, and those accepted labels may later train another model to
reproduce V0. Without an independent quality or outcome signal, architectural
assumptions and mistakes can be preserved as training targets.

### The appraisal representation is difficult to inspect and supervise

Call 1 emits eight appraisal values as a positional array. The meaning of each
position is supplied outside the output. This is compact, but the record itself
is not self-describing and is vulnerable to silent semantic confusion if
dimension order or scale interpretation changes.

For training and evaluation, this makes it harder to determine whether an
appraisal error reflects an incorrect value, incorrect dimension alignment,
weak evidence, or a changed schema version. The large set of references around
the values further obscures the actual cognitive judgment being supervised.

### Domain leakage contaminates otherwise valid-looking examples

Two non-food artifacts were accepted by content compilation with
`claim_kind: culinary_suggestion`: one for managing an overwhelming day and one
for explaining vivid embarrassing memories. These records are structurally
valid but semantically mislabeled.

This is especially concerning for training use because the error is not merely
cosmetic. A learner trained on these examples would receive an incorrect domain
label as a successful target. The test demonstrates that schema validity alone
cannot be used as a proxy for label quality.

### Artifact generation is conflated with ordinary conversational help

The system converted general advice and a short explanation into persistent
structured artifacts even though the users asked to talk, think, or understand.
That choice expanded Call 2 into titles, sections, operations, content items,
anchors, claim classes, subject identifiers, and source bindings.

The additional structure increased the opportunity for compilation failure and
introduced the observed domain-label error. It also makes the resulting trace
describe document construction more strongly than the conversational judgment
that motivated the answer.

### The current traces do not contain a reliable outcome signal

The durable record can show that a proposal compiled, passed a gate, or was
accepted by realization validation. It does not by itself show whether the user
felt understood, whether the answer resolved the request, whether a correction
was incorporated successfully, or whether the conversation improved.

Without that distinction, the data primarily supervises process conformance.
It is weaker evidence for response utility, human-likeness, or effective
reasoning. The visible assistant outperforming the shadow on several failed
turns reinforces the gap between internal validity and conversational outcome.

### Complexity makes causal diagnosis difficult

The current path has enough interacting stages that a failed turn can implicate
the ontology, prompt, model output, reference compiler, goal lifecycle,
collaboration compiler, action gate, realization validator, or persistence
boundary. Repairing one rejection can move the same turn to a different later
rejection without demonstrating better cognition.

This makes iterative evaluation expensive and can create misleading progress:
a higher completion rate may result from relaxing a boundary rather than from a
better interpretation or response. The system needs quality review of actual
wording and decisions in addition to terminal-status counts for precisely this
reason.

### Overall concern

V0 currently records a detailed account of how its software protocol processed
a turn. It does not yet consistently record an independently credible account
of the best cognitive judgment for that turn. Until those two concepts are
better separated, greater structural detail can produce more trace data without
producing better cognition data.

## Bottom line

The corrected general-conversation evaluation reverses the impression created
by the meal-oriented test. The two-call infrastructure runs, but the Cognitive
system is not yet broadly functional as a general conversational agent. Its
most immediate limitations are narrow interpretation contracts, correction
goal/target mismatches, overuse of structured artifacts, domain-inappropriate
claim classification, and final validators that reject natural conversational
forms while accepting irrelevant clarifications.
