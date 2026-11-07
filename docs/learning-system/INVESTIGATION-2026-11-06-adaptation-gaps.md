# Investigation: why learning does not reliably become adaptation

Reviewed 2026-11-06. Scope: current working-tree reviewer, builder, evidence gathering, retention and delivery code; latest implementation reports; saved, already-diagnosed v3 behavioral results and live summary logs. No production changes, database writes, new model calls, or live game run.

Follow-up on user request: the supported-status retention veto described in section 1 was subsequently removed
from finding admission and inferred-product creation/update/withdrawal. Source references remain required.
`lessons-v6` aligns the builder instructions with this change; the broader prompt revisions below remain for
discussion. The reproduction script now shows both statuses admitted and retained. This does not remove every
semantic rule elsewhere in learning: scope/certainty restrictions on repeated evidence and reconsideration,
and the untested-certainty rule, remain and require a separate explicit account in further changes.

## Conclusion

Prompt instructions contribute materially, but the available evidence cannot establish that they cause a majority of failures. There is a reproducible code barrier to retaining some tentative findings, several prompt rules that work against the intended breadth of learning, and separate failures in evidence access and execution. More assertive lesson wording alone will not close these gaps.

The intended outcome is evolving understanding from ordinary experience, changing later attention, expectations, possible strategies and decisions, with appropriate transfer and revision. The user's examples are illustrations, not domain rules to implement.

## 1. Confirmed code barrier: a plausible finding can disappear before retention

`backend/app/services/learning_lessons.py:worth_reading` schedules a builder when a finding is supported, contradicted, or related to existing understanding. A novel plausible finding, with no relation and no other qualifying conclusions, does not qualify.

`backend/app/ai/learning/lessons.py:check` independently requires a supported assessment conclusion to create any strategy or understanding, even with certainty tentative. Citing only a plausible finding fails this check.

Model-free reproduction using the actual functions:

| Otherwise identical finding | Builder admission | Tentative creation |
| --- | --- | --- |
| plausible | false | rejected: inferred lesson needs a supported conclusion |
| supported | true | retained |

Reproduce with `python3 docs/learning-system/investigate_tentative_retention.py` from the repository root. This establishes the mechanical restriction, not that every plausible finding deserves retention or that this caused most recorded failures.

The reviewer explicitly requests plausible, untested possibilities. The builder can retain these when it cites additional supported ingredients; the saved v3 combination cases demonstrate that this works sometimes. The gap is the path for evidence-grounded tentative understanding that lacks a separately formulated supported conclusion. It creates pressure to restate an observation as supported before preserving the possible meaning of that observation.

Recommended repair: distinguish the observed basis from the inferred proposition and its uncertainty. Permit justified tentative products without requiring the proposition itself to be supported. Preserve provenance, ownership and uncertainty checks. This is a contract and code change as well as a prompt change; simply accepting every plausible statement would be insufficient.

## 2. Prompt rules can suppress ordinary social learning

The reviewer says most ordinary activity teaches nothing new and lists small talk as a reason to leave a stretch. The builder also lists small talk among material not to carry forward. Other paragraphs correctly ask for patterns in how a person talks.

Those instructions pull in different directions. Casual interaction can supply evidence of humor, conversational expectations, inconsistencies or changing circumstances. Its conversational category alone cannot determine learning value.

This is a confirmed instruction conflict and a plausible source of under-learning. Its measured contribution is unknown; no controlled social-learning replay was run in this investigation. Replace category exclusions with a judgment about whether evidence adds to or changes understanding, allowing no change when appropriate.

## 3. Prompt reconciliation is too broad

The builder says “One subject, one lesson” and then instructs it to put experience about the same person, place or situation into that lesson. Later instructions allow a separate lesson for different meaning, and compatible scopes to coexist.

A subject can support several independent beliefs. Grouping by subject can force unrelated understanding into one product; grouping by superficial wording can also create duplicates. Saved X-family reconciliation results were 1/6 under the historical evaluator. That supports concern about reconciliation but does not isolate this sentence as its cause.

Compare propositions, conditions and evidence, with subject identity as context. Preserve multiple distinct or competing beliefs when warranted.

## 4. New feedback prompts overstate what outcomes establish

The reviewer correctly says a component reporting “applied” is not proof that a lesson shaped its choice. But it also directs support when use is followed by success, and challenge when repeated use accompanies no progress. The builder ends with “A lesson applied and followed by progress is supported by that experience.”

Outcome is relevant evidence, but it may concern perception, execution, a missing prerequisite, another concurrent action or luck. A method may never actually have been carried out. The latest live report itself shows this distinction: the mind proposed descending while the body kept walking.

Retain the whole sequence, including opportunity, actual choice, execution and outcome. Judge which claim the outcome bears on before changing belief. Do not turn reported application plus success/failure into an automatic causal verdict.

## 5. Evidence transport and retrieval remain separate limits

The live report documents whole stretches previously parked unread because they exceeded the evidence limit. Condensed rendering now lets those reported stretches fit. Current `_investigate` still parks when neither full nor condensed evidence fits; it does not implement a general progressive reading of arbitrary-size stretches.

Automatic lesson lookup uses the last 4,000 characters of selected evidence; memory lookup uses the last 2,000. Extra requests can recover context, but the initial comparison may miss an earlier relevant event or prior belief. The builder clears retrieval requests when gathering its bundle and relies on the reviewer's conclusions to carry their support. These are actual information bottlenecks; the frequency and effect of resulting omissions remain unmeasured.

## 6. Useful products exist, but production and use remain inconsistent

The already-diagnosed saved report `backend/var/broader_learning_eval/20261106T040201Z-reserved.json` contains both reusable tentative understanding and statements tied to a single attempt. It also contains successful untried combinations. Thus the system is not mechanically limited to logs.

Historical family results include understanding 4/6, combinations 5/6, reconciliation 1/6, expectation checks 4/6 and transfer 2/6. These use an evaluator subsequently found deficient; they are diagnostic records, not current acceptance evidence or reliable estimates of failure prevalence. The report predates current assessment-v6 / lessons-v5 prompts.

The latest live report establishes delivery without reliable behavioral improvement and later reasoning without execution of the proposed method. Student 1 still lacks lesson input. None of those gaps can be repaired solely by rewriting the reviewer prompt.

## Recommended next work

1. Repair the tentative-retention contract, with evidence-backed guesses, unsupported guesses, repeated evidence and established-claim controls.
2. Resolve conflicting instructions about ordinary interaction, proposition identity and outcome attribution. Keep learning broad; do not add handlers for the user's examples.
3. Trace controlled cases from raw ordinary experience through review, retention, retrieval, decision, actual execution and subsequent revision. Record the first failed stage.
4. Compare matched prompt versions against the same development experiences before attributing improvement to wording. Separately evaluate code repairs. Use fresh reserved cases only after development, with the repaired acceptance gate.
5. Follow the user's agreed mind/planner/hands responsibilities when repairing execution coordination. A new approach should be executed or returned with its reason for being blocked; silent substitution defeats both action and learning evaluation.

The investigation identifies concrete blockers and instruction conflicts. It does not establish their relative prevalence, verify the latest live database state, or claim human-like learning has been achieved.
