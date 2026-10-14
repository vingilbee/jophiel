# Independent premise/relation assessment — 2026-10-13

## Change

The prior contract already had separate fields; the defect was semantic coupling
in model instructions. `status` evaluates what the user asserts or presupposes.
`relation` independently compares the actual recorded/current positions, including
a comparison of two past recommendations. Absence of a user assertion does not
make that comparison inapplicable.

Implemented as registry v1.12 with `recorded-position-premise-v2`. Contribution
input v1.4 and proposal v3 retain their shape. A short versioned instruction and
field descriptions clarify independent applicability, evidence-limited uncertainty,
and truly unrelated requests. The schema still permits all evidence-supported
relations after either a false or neutral premise; code does not force a judgment.
Character/state inputs, source binding, confidence policy, action selection and
realization are unchanged. No extra model call, opinion memory, tool authority,
or production protocol switch was added.

Files: `two_call/contribution_premise_v2.txt`, `premise.py`, `contribution.py`,
`provider.py`, registry/provenance membership, and targeted premise tests.
Earlier v1.11 captures still select their original instructions/schema; four
saved v1.11 development traces replayed with matching semantic digests.

Automated regression result: **397 passed, 1 skipped**, including premise-axis
independence, old/new prompt/schema selection, source binding, character,
continuity, orchestration and requirement-resolution coverage. `git diff --check`
passed.

OpenAI Docs guidance informed the use of explicit field descriptions rather than
additional forced schema coupling; see the official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat).
Schema compliance does not guarantee semantic correctness.

## Real-model development verification

`backend/scripts/cognitive_relation_axes_eval.py` runs seven **new development**
scenarios under two written characters using the existing isolated real-provider
pipeline harness. One repeat each; controlled histories, not new long closed
loops. The reserved corpus was not opened or rerun. No live replies, database
writes or worker changes were made. Original reports/evidence remain intact.

**14/14 turns completed and were accepted.** In all 12 relevant comparisons,
relation was applicable (including `uncertain` for insufficient evidence). Both
unrelated preferences had `relation=not_applicable`. The specific
“no asserted change → comparison not applicable” defect did not recur.

| Scenario | Standard | Opposing |
| --- | --- | --- |
| Neutral packaging comparison | no_asserted_change / revised | no_asserted_change / exception |
| Neutral repeated sketch choice | no_asserted_change / unchanged | no_asserted_change / unchanged |
| Neutral strengthened cover recommendation | no_asserted_change / stronger_support | no_asserted_change / stronger_support |
| False heading-preference premise | contradicted / uncertain | contradicted / uncertain |
| Explicit wrapping-paper exception | no_asserted_change / exception | no_asserted_change / exception |
| Missing lamp preferences | no_asserted_change / uncertain | contradicted / uncertain |
| Unrelated cloud preference | not_applicable / not_applicable | not_applicable / not_applicable |

## Limits and remaining variation

- Packaging: the opposing character describes metal tins as a shipment-specific
  exception rather than a revision. Its reply preserves both recommendations and
  the transport constraint. This is a remaining scope/label distinction, not the
  original applicability confusion. No further tuning was done from this result.
- False-premise fixtures initially expected `unchanged`; both models instead used
  `uncertain` because the alleged older long-heading preference was absent. The
  replies correctly refuse to invent a switch. This is defensible uncertainty,
  not a requirement to force the initially expected label.
- Missing-position fixtures initially expected `insufficient_evidence`; the two
  profiles differ over whether asking to compare an unestablished preference
  presupposes its existence. Both correctly keep relation uncertain and fabricate
  no preference. Exact fixture expectations are retained rather than rewritten
  after seeing outputs. This is not a claim of 14/14 exact-label agreement.
- Existing repetitive hedging remains. These tests do not establish recurrence
  rates, long-run conversational coherence, or full dynamic-state conditioning.

Exact provider payloads, proposals, terminal records and replies are in
`evidence/*.json.gz`; fixtures and summary accompany them. The saved artifacts
are development evidence, not an independent held-out score or uniformly clean
training labels.
