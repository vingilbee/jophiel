# Source-linked follow-up handoff — 2026-10-12

## Scope

Implements steps 1–3 requested after the closed-loop evaluation: preserve the
meaning of conversational follow-ups, distinguish explanation from quotation
and artifact work, and forward relevant earlier statements to contribution.
No permanent opinion-memory subsystem, state/personality changes, confidence
mapping changes, new model calls, or production configuration changes.

## Connected input/output

1. Call 1 still sees the authorized captured history. Registry v1.9 adds a
   nullable `followup` on opinion and elaboration events. It has an operation
   (`continue_opinion`, `explain_position`, `compare_positions`), a bounded
   `resolved_question`, and up to six source-labelled dialogue citations.
2. The compiler checks message membership, focus-time bounds, token ranges,
   excerpt length, duplicate sources, and speaker attribution. Position sources
   must be earlier assistant speech. No source can come from another capture or
   a future message. Other event types cannot carry this handoff.
3. The resolved question becomes the canonical topic/referent in BOTH semantic
   frames and accepted observations. Goals, requirements and policy therefore
   receive the same interpreted meaning. This is model interpretation, not
   independently verified answer content. Genuine ambiguity keeps the existing
   null/unknown/clarification path.
4. Contribution receives `followup_context` for the matching predicate and
   resolved question. It includes exact source excerpts, local message/token
   references, offsets, speaker, and the whole source message when <=2,000
   characters. Whole messages are never silently truncated. The normal recent
   history window remains unchanged. Older relevant positions survive through
   this explicit handoff. No unrelated event receives the context; duplicate
   matching events do not trigger arbitrary source selection.
5. Contribution instructions distinguish continued judgment, explanation, and
   comparison. They require claims about previous speech to follow actual text,
   not a fallible interpretation label or an assumed change in the user's
   question. Reasoning may maintain, qualify or revise a position; no stance or
   generic helpful-assistant style is prescribed. The existing action selection
   and realization stages consume the resulting content normally.

## Routing and authority

The model chooses the conversational route. There is no keyword router and no
post-hoc conversion of every `explain` request into an opinion. Literal recall
and actual artifact work keep their existing paths. A bounded question
restatement is a deliberate relaxation of the former extractive-only semantic
format; it is not a generated answer, personal fact, or durable belief.

Citations prove that text exists, NOT that a proposed interpretation follows
from it. The source-message context and instructions help preserve contrast and
negation, but semantic correctness still requires behavioral evaluation.

## Compatibility and traces

- Registry v1.8 and earlier retain old schemas, prompts and replay semantics.
- Call 1 prompt is `call_one-v1.9`; contribution keeps its character-dependent
  input schema and adds marker `source-linked-followup-v1` and prompt suffix
  `+followup-v1`. Captures retain the new context for inspection/replay.
- Training projection retains the resolved question and locally referenced
  source excerpts. Acceptance does not promote examples into reviewed data.
- Existing unrelated worktree edits were preserved. The older configured
  `context-conditioned-two-call-v0.1` path is not switched by this change.

## Verification

`tests/test_cognitive_followup_handoff.py` covers old-context forwarding,
canonical argument/observation agreement, strict schema shape, replay,
ambiguous follow-ups, invalid/foreign/future citations, speaker checks, event
isolation, and training provenance. Existing v1, character, continuity,
orchestration and requirement tests are included in the regression run.

Final automated run: **367 passed, 1 skipped** across follow-up handoff,
character, v1, continuity, orchestration, compact orchestration, and requirement
resolution tests. These are contract/regression checks, not personality scores.

`scripts/cognitive_followup_retest.py` reruns three exact recorded inputs with
their original predecessors and real providers. It is an isolated in-process
regression, not a new closed-loop run or frontend/worker test. It makes no DB
writes, does not deliver replies, preserves prior evaluation artifacts, and
records raw request/response bodies without credentials.

### Real-model targeted results

Final run: **3/3 completed and accepted**, using the recorded synthetic histories
and original predecessors with `gpt-6-luna`. This is one sample per case, not a
reliability estimate or proof of general closed-loop improvement.

- `standard-preference-r3:T3`: correctly says she **already** leans toward the
  grand title and that the user's amusement reinforces that preference, instead
  of asking what exception or inventing a prior simple-name preference.
- `opposing-preference-r1:T8`: explains that Fern matches her original preference
  and that an amusing private joke does not change the broader preference.
- `standard-revision-r1:T8`: retains flexible, non-barrier chat, while explaining
  stronger support for this particular group given the vote, newcomers' needs,
  optional participation, and unchanged book-discussion time.

Final raw input/output and terminal artifacts:
`/private/tmp/cognitive-followup-v1-9-final-retest.json`.

Development runs were not uniformly successful and are retained separately:
the sandboxed attempt failed transport; the first network attempt passed two
cases but exposed a schema mismatch (`unavailable` versus JSON null) on the
compound exception turn. After correcting that, the exception answered but
invented a prior simple-name preference. The final prompt removes the
over-specific example, marks the interpretation as fallible, and includes
bounded full source messages to retain contrasting clauses. The final three
cases were rerun after those changes. These observations do not justify hiding
earlier failures or treating contract acceptance alone as a quality pass.

Official [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
informed the nullable strict schema. Schema conformance is separate from
semantic-quality validation.
