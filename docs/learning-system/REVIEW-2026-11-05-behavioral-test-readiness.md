# Review: behavioral test readiness for post-audit 3 and 4

Reviewed 2026-11-05 (America/Vancouver). Sources: the user's original learning conversation, the sibling checkout's general experience learning design, post-audit 3/4 designs and implementation records, current scripted tests, evaluation runner, public rubric, development fixtures, and saved result summaries. This is an implementation-agent handoff, not an acceptance result or authorization to rebuild the learning system.

## Conclusion

The families are useful but the current evaluator is not yet a sound completion gate for the user's goal. Retain the families, fix measurement defects before further prompt tuning, and add missing process evidence. A model successfully restating a supplied lesson does not establish autonomous discovery, transfer, or improvement through experience.

Keep the boundary: step 3 supplies the learning process; step 4 completes how retained understanding relates, changes, and remains usable. Tests should check their connection without creating another learner or domain-specific lesson rules.

The latest inspected report, `backend/var/broader_learning_eval/20261106T040201Z-reserved.json`, contains 48 runs: U 4/6, C 5/6, X 1/6, R 6/6, P 5/6, E 4/6, G 0/6, T 2/6. Three families pass numerically; full acceptance is false. Student 1 remains uncovered. Filenames use UTC; this run occurred on November 5 in the user's timezone. No new model evaluations or database tests were run in this review.

## What to preserve

- Distinguish scripted lifecycle tests from real-model behavioral cases. The existing scripted tests exercise meaningful reference, revision, same-evidence, and paused-reassessment contracts.
- Retain tentative products, dependencies, missing-evidence reporting, no-change outcomes, and cognition-owned actions.
- Retain the accepted step 4 rule: three variants, two isolated runs each, at least five passes per family, every variant passing once, and no critical integrity failure. Step 3's rule still has its separately documented agreement status.
- Retain the declared Student 1 prerequisite and the distinction between product mechanics and complete interpretation coverage.
- Preserve all historical results. A test repair must get a new evaluator/rubric version; old scores do not become passes retroactively.

## Findings that block trusting an acceptance claim

### 1. Aggregate acceptance omits required gates (P1)

`backend/scripts/broader_learning_eval.py`, `families()` and report construction in `main()`:

- `accepted` depends on family scores and recorded critical failures, even while the same report states that Student 1 is uncovered. It does not gate on actual consumer coverage or required integrity-suite results.
- Family validation checks six total runs, not exactly three variant identities with two distinct run identities each. A local, model-free reproduction using the actual `families()` function accepts one variant run six times in every family.
- Development corpus runs are not structurally excluded from acceptance, and duplicate run identities are not rejected.

Required repair: separate `family_thresholds_passed` from `package_accepted`. Validate family/variant/run identities, completeness, reserved-corpus eligibility, prerequisite coverage, and integrity-suite artifacts. Test missing consumers, missing families, duplicate runs, one variant repeated six times, development-only runs, and absent integrity evidence. Each must fail closed with a reason. Do not hardcode a permanently false gate; derive completion from verified prerequisites when they become available.

### 2. Critical checks do not implement the full rubric (P1)

`integrity()` provides narrower checks than its C1–C5 descriptions:

- C3 compares counts produced by the application under test against the number of distinct occasion keys. Two occasion keys sharing the same underlying event can both count and still satisfy that upper bound. The oracle also calls the same `_counts` implementation whose correctness it should test.
- C2 checks product/link owners but does not resolve every link target's owner; the fixtures create only one user. It does not establish forgotten-source exclusion through a real forget lifecycle.
- C4 checks additional chat rows and trial existence, not all plan changes, skill/practice starts, dispatches, or external delivery paths.
- C5 searches a narrow rendering phrase for an absent guess label. It does not generally validate revision currency, unsettled premises, or whether the model asserts uncertain information as fact. T3's freshness check can pass vacuously if the after-revision supply is empty.
- C1 checks certain reference prefixes, not semantic support of a claim by the cited source. A real source ID can still be used to justify an invented fact.

Required repair: use independent fixture-defined event identities and expected state transitions; seed cross-user targets and forgotten evidence; intercept actual dispatch boundaries; validate exact supplied revisions and semantic assertions. Add mutation tests that deliberately introduce each violation and demonstrate detection. Report `not exercised` separately from `checked and absent`; historical “zero critical failures” means zero detected by the old checks, not comprehensive integrity validation.

### 3. Direct model probes are not deployed-consumer coverage (P1)

`_prepare_process()` forces Student 2's teacher provider and disables capture. `_probe_planner()` builds its own situation; `_probe_hands()` calls the driver directly and does not execute its action. `_probe_mind()` builds a minimal hard input. The hands report omits the full system prompt and schema. These are valuable component evaluations, but do not demonstrate the complete deployed paths, local cognition, captured decisions, or measured execution outcomes.

Required repair: label component probes as such. Add production-path integration probes with controlled sources and actual configured input contracts. Record selected versus deployed providers, exact prompts/schemas, retrieved revisions, and authoritative resulting state. For action claims, execute in a controlled environment with independently defined outcome measurement; a sensible proposed action is not evidence of successful execution. Keep local-model incompatibility as a dependency rather than substituting teacher success for it.

### 4. Scoring can reward the wrong product or incomplete evidence (P1)

`auto()` checks C2 and C3 with separate `any(...)` evaluations over the judge's chosen products. One product can satisfy basis links while a different product satisfies tentative/untried status; no single product need satisfy both. U2 permits arbitrary `fact:` roots through a self-derived allowlist rather than resolving their provenance. X1 looks for section headers, which proves neither the relevant records were present nor that the reviewer used them. `judge()` silently slices serialized evidence and probes to character limits.

Required repair: bind all requirements for a candidate to the same product identity and appropriate revision, verify evidence against a fixture-defined source graph, and inspect retrieved record identities and subsequent use. Preserve full audit artifacts; provide bounded, explicitly covered judge views. Missing or truncated evidence must lead to incomplete/ambiguous scoring, not an inferred pass. Test unknown product IDs, split satisfaction across two products, empty retrieval sections, irrelevant returned memories, and omitted decisive evidence.

### 5. Step 4 results do not cover the step 3 process (P1)

The current eight-family runner manually seeds stretches and calls `learning_review.conversation/game`; game sessions are created ended. It does not establish live trigger admission, observation-only discovery, multiple-step success attribution, self-directed evidence expansion, relevant cross-session resumption, ongoing activity, or cognition accepting/defering a proposal end to end. Those capabilities have partial scripted coverage in `test_learning_broaden.py`, not a completed B1–B12 real-model acceptance.

Required repair: maintain a traceable B1–B12 and U–T coverage matrix, with links to actual run artifacts and explicit gaps. A shared case may satisfy both only when its execution exercises both contracts. Do not mark step 3 complete because a seeded step 4 product later reaches a consumer. At least some discovery cases must begin with ordinary source activity, no seeded answer, no evaluator-provided critique, and no instruction identifying the desired lesson.

### 6. Baselines and reserved-data handling need stronger controls (P2)

`probe()` runs with-products then without-products sequentially against one database. Conversation probes write messages in both arms. The arms are not forked from an identical snapshot, and provider stochasticity is not controlled or independently assessed. The requirement's need for a behavioral difference is inferred from the word `baseline` in prose rather than structured metadata. Non-discriminating probes were frequent in saved runs.

The implementation report says the latest reserved set was read for diagnosis and is now development material, but the runner still loads it through the `reserved` alias. Reports do not record fixture/rubric/code content hashes. Do not silently promote a diagnosed fixture because its directory still says reserved.

Required repair: fork matched arm state, restore settings, prevent cross-arm memory writes, record order/settings, and define comparison requirements explicitly. Track split and exposure lineage with immutable hashes and declared eligibility. Any diagnosed set remains useful regression material but is ineligible for a new acceptance claim after tuning. Freeze new fixtures before changes, and keep evaluation reports tied to exact artifacts.

### 7. Runtime prompts risk narrowing the goal to the tests (P2)

The current builder prompt says “One subject, one lesson,” broadly routes evidence about the same person/place into an existing lesson, and categorically excludes some routine successes/small talk. It also prohibits episode scope for understanding. These instructions may encourage apparent deduplication or reuse while erasing distinct claims, exceptions, or meaningful ordinary observations. They are not equivalent to the user's LLM-judged relevance and semantic reconciliation requirement.

Required repair: add contrastive tests before changing prompts. Two distinct relationships about the same subject may both be valid; two differently worded claims may be redundant; a routine interaction can contain novel information or no information. Score the justified meaning and retained evidence, not a preferred number of records or a specific wording. Change prompts based on those general distinctions, not individual failing fixture answers. Do not replace semantic judgment with keyword rules.

## Family guidance for the next implementation agent

Keep eight step 4 families. The following makes their existing intent testable; it does not change the accepted numerical threshold.

| Family | Preserve | Strengthen or avoid |
| --- | --- | --- |
| U understanding | Observation can yield understanding before advice. | Use contingent relationships learnable from the fixture, not facts the base model already knows. Pair meaningful ordinary success with genuinely uninformative activity; do not prohibit all success-derived learning. |
| C combination | Novel combinations retain basis and uncertainty. | Require one identified product to satisfy all criteria. Include a control where combining is unjustified. Do not demand exactly the evaluator's imagined strategy when another evidence-grounded possibility works. |
| X reconciliation | Compare across existing memory owners. | Separate same subject/different meaning from different wording/same meaning. Verify specific retrieved evidence and its use; include a case needing an additional request beyond initial supplied memories. |
| R reconsideration | Correct an inference without manufacturing observations. | Include a valid critique and a mistaken critique: she may justify retaining a sound belief. A controlled critique proves response to critique, not autonomous discovery of a reasoning flaw; measure those separately. |
| P premise changes | Consumers see changed premises before reassessment. | Test every actual supply path, stale prepared input, missing/withdrawn premise, multiple/deep dependencies, unrelated revisions, and a fresh-process restart. Do not assume one-hop success establishes all dependency behavior. |
| E expectations | Judge support, contradiction, changed conditions, and missing coverage. | Include counterbalanced supporting and conflicting sequences and noisy observations. Do not reward inventing causality or treating every new observation as a counterexample. |
| G capability gap | Separate a useful possible method from actual ability. | A baseline that also avoids an unavailable move can pass the integrity requirement; it simply cannot prove adaptation. Use a separate contingent learned opportunity or prerequisite to demonstrate changed choice. |
| T transfer | Fresh context, surface change, meaningful boundary, later revision. | Hold out relational structure or task families, not only names. Hide the contingent answer from the current state. Require measured decision quality, not merely any difference from baseline. |

All families need acceptable alternatives defined by evidence and goal, not one canonical sentence. An honest “unclear” can be correct when evidence is insufficient; it must not excuse failure on a deliberately answerable learning case. Score learning opportunity quality separately from product formation, retrieval, use, and outcome so a failure points to the right subsystem.

## Designing a discriminating test without teaching the answer

Use controlled, contingent relationships whose answer is available in prior experience but absent from general model knowledge and the later task. Counterbalance which relationship holds across fixtures. Present ordinary observations through the existing adapters, including distractors and relevant conditions; do not include an explicit lesson statement or instruct the reviewer which relationship to infer.

After learning, clear original context, introduce a later task whose correct handling depends on that relationship, and compare matched arms. Then change an irrelevant surface detail, change a necessary condition, and finally supply contrary evidence. Measure appropriate use, appropriate non-use, and revision. This tests learning rather than general competence or obedience to the latest prompt.

Fixtures should vary domains, evidence patterns, missingness, and outcomes. Three renamed versions of one story are not strong evidence of generality. Keep seeded-product cases for lifecycle tests, but label which capability they bypass. No new domain adapter is required merely to diversify semantic scenarios within existing observation/conversation/game inputs.

The original goal also includes learning related tasks faster. Existing transfer families do not yet measure this directly. Add an explicitly labelled exploratory measurement: compare observations/actions needed to reach the same criterion on a related task with versus without earlier relevant experience, including an unrelated-prior control. Do not silently add it as a new mandatory numerical gate; agree on promotion to an acceptance requirement separately.

## Paused reassessment: coverage is good but not complete

The scripted test in `test_learning_products.py` verifies changed premise text and UNSETTLED status before the worker runs, current supply for prepared Student 2 input, reassessment, and an unaffected product. Preserve this test.

Its “restart” reimports an already-loaded module and rereads using the same process; that does not demonstrate process restart. The behavioral runner recreates its engine/session but also stays in process. Add a separate-process test with no retained Python globals. The current helpers render consumer sections; extend selected checks to actual delivery boundaries, especially cached/prepared mind, planner, and hands inputs. Student 1 remains uncovered, not vacuously passed.

## Implementation handoff order

1. Repair aggregate acceptance and write model-free tests proving malformed or incomplete runs cannot pass.
2. Strengthen independent integrity oracles and scorer mutation tests; keep recorded critical coverage explicit.
3. Version the evaluator and rubric, retire exposed reserved sets, and freeze a fresh eligible corpus with auditable split lineage.
4. Build the B1–B12/U–T coverage matrix and distinguish component probes from deployed-path tests. Preserve outstanding Student 1 and step 3 gates.
5. Improve fixtures and matched baselines; add the paired controls above. Calibrate the judge using labelled pass/fail/ambiguous examples, blind arm labels where practical, and inspect disagreements. Keep a single judge's agreement distinct from ground truth.
6. Only then change learning prompts or implementation against development cases. Fix the general failure mode, not a fixture-specific behavior. Run required integrity tests and fresh behavioral evaluation, retaining every result and limitation.

## Verification performed for this review

- Read the plans, current tests and runner, public rubric, and implementation records; checked saved summary results against the report.
- Executed the real aggregation function in isolation (extracted through Python AST, no database or model imports). Confirmed that one variant with six runs incorrectly passes a family.
- Did not read the current reserved fixture content, tune prompts, alter frozen test data, change production code, call paid models, migrate the development database, or rerun previously reported suites. Static findings are labelled by their actual checks and required remedies above.
