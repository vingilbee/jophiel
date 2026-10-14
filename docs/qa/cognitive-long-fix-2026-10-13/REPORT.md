# Long-conversation handoff repairs

Implemented from the [long-state diagnostic](../cognitive-long-state-2026-10-13/REPORT.md).
No personality preference, value, affinity or dynamic-state rule was changed.
The objective is coherent meaning and reliable delivery, not a prescribed opinion.

## Changes

New V1 runs use argument registry v1.13 and realization policy v1.6. Earlier
registry/policy paths remain available for frozen replay.

1. **Routing:** Call 1 explicitly distinguishes reevaluating a judgment from
   modifying a deliverable or performing an external action. Imperative grammar
   alone no longer implies the action path in its instructions. Real action
   permissions/blockers are not bypassed.
2. **Referent preservation:** contribution receives an explicit accepted-question
   contract alongside the existing exact sources/history. Its instructions prevent
   silently replacing that referent with another literal meaning and then claiming
   history is absent. This remains semantic model reasoning, not a hard-coded
   topic classifier or a guarantee against all history errors.
3. **Compatible answer selection:** Call 2 now receives the actual authorized
   content associated with each candidate, following question→answer-content refs.
   Previously candidate targets alone were insufficient to compare meanings.
   It is instructed to avoid duplicate or conflicting answers about the same
   recorded position, without suppressing legitimate present revisions or answers
   to different questions. Code does not claim to solve semantic contradiction.
4. **Recall construction:** the proposition's subject identifies the speaker's
   recorded statement, not the whole current user utterance. Exact recalled values,
   attribution, source links and question context remain available. The question
   and any criticism it contains are no longer promoted to an assertable subject.
5. **Display boundary:** model-authored contribution values/anchors share a
   single-line normalized display surface, removing Unicode format characters and
   collapsing whitespace. Raw proposals remain captured. Source quotations are
   not rewritten; other invalid controls still fail. This fixes the observed
   invisible-format-character projection failure without accepting arbitrary
   malformed text.
6. **Realization alignment:** new instruction variant v4 requires nonempty
   contiguous excerpts copied from final text. Omitted moves have no alignment
   entry rather than an empty one. Missing required moves and invalid alignments
   remain failures; there is no fabricated whole-response alignment fallback.
7. **Negation scope:** validation v4 recognizes contrasts such as “candor, not a
   kind lie,” retaining positive candor while rejecting “I did not advocate
   candor” and “candor was not my answer.” Old v3 validation is unchanged. This
   is a bounded English contrast heuristic, not complete semantic entailment.
8. **Evidence-schema agreement:** a new development run exposed an unchanged
   relation with zero evidence refs. The compiler correctly rejected it. The
   v1.13 provider schema now mirrors that existing requirement using evidence-bound
   and unbound alternatives: asserted relations need a reference; uncertainty or
   inapplicability can remain unbound. It does not choose an opinion or repair
   missing evidence by inventing it.

No additional model call, deterministic opinion freeze, permanent opinion memory,
production protocol switch or worker restart was introduced. The OpenAI Docs skill
guided the schema clarification using the official
[Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat).

## Development evaluation

`backend/scripts/cognitive_long_fix_eval.py` reuses the original and separately
recorded conversational wording, under standard/base/strained and opposing/blunt/
strained profiles. Real model calls run in isolation with DB/Redis unavailable.
Only accepted cognitive responses feed forward; a failure stops its chain, with
no substitute response or silent reset. Reserved cases were not used.

The first four-chain run completed **three ten-turn chains**. Both original
“reconsider” chains and the standard conversational chain completed. The opposing
conversational chain stopped on turn 3 because contribution declared an unchanged
relation but omitted evidence references. This failed run is retained, not counted
as complete. Totals: **32/33 attempted turns accepted**, of 40 originally planned.

On the three reached criticism turns, replies retained the established
meeting-format preference and explained grounds for change. The original wording
was routed to opinion reasoning rather than an unavailable external action. The
checked replies did not deny the earlier preference or echo the whole question.

The new evidence-schema constraint was followed by a fresh run of the stopped
condition. Results and final regression/replay checks are recorded below.

### Final verification

- Fresh opposing/blunt/strained conversational rerun: **10/10 accepted**, one
  complete ten-turn chain. Exact inputs, model boundaries and replies are in
  `evidence-rerun/`; the initial four-chain run remains in `initial/`.
- Turn 7 retained its earlier loose-plan preference despite criticism. Turns
  8–9 changed the club-specific recommendation in response to participation
  evidence and moderator safeguards. Turn 10 correctly distinguished those
  recommendations from general taste and recalled opposing a kind lie.
- Across both development runs: **42/43 attempted turns accepted**, four
  completed chains and one stopped attempt. This is not 40/40 on one run.
- Targeted regression suite: **589 passed, 1 skipped**. Coverage includes frozen
  replay assertions, premise assessment, source/routing handoffs, character
  opinions, two-call orchestration, requirement resolution and realization.
- Implementation diff whitespace checks passed. Historical QA artifacts retain
  their original CSV line endings and transcript whitespace; staging those
  artifacts raises whitespace warnings. No additional reserved cases were executed.

The rerun still repeatedly says “tentatively.” Successful delivery and accurate
recall here do not establish natural conversational quality or reliable dynamic
state effects across unseen conversations.

## Limits

These are development regressions, not a matched statistical comparison against
the original 24 conversations. A schema or prompt repair does not guarantee all
future routing, referent or alignment judgments. The 400-character contribution
budget and repetitive hedging remain; state-caused substantive judgment differences
remain unproven. No state mechanism was changed based on that unproven hypothesis.
Inspect replies and intermediate evidence before treating accepted traces as
clean training examples.
