# V1.10: speech scope, source selection and dialogue reasoning

Implemented October 13, 2026, from the development findings in
[the root-cause report](../../../../../../docs/qa/cognitive-root-cause-2026-10-13/REPORT.md).
The ten reserved unfamiliar cases were neither opened nor executed.

## Objective and non-goals

Improve general pipeline connectivity, not force a particular opinion or an
ideal helpful-assistant personality. Character values, principles, beliefs,
personality bands and dynamic-state bands continue to enter contribution. A
different character may reach a different judgment; supporting dialogue must
still be attributed correctly. No topic-specific routing rules, mandatory
agreement, permanent opinion ledger or new execution permissions were added.

This applies to new `cognitive-two-call-v1` captures. The application's configured
protocol was not switched, and no worker was restarted. Legacy registries retain
their old schema/prompt/compilation behavior. No-character contribution remains
on its existing legacy path; this work does not replace that selector.

## Connected input/output changes

1. **Understanding input → semantic events.** Registry v1.10 adds `act_scope`
   (`current`, `quoted`, `reported`, `imagined`) and `subject_mode` (`actual`,
   `hypothetical`, `unspecified`). A current request about an imagined object is
   still a current request. Predicate descriptions distinguish personal judgment,
   actual identity, produced deliverable and external action by requested outcome.
2. **Semantic events → interpretation/goals.** The compiler derives the existing
   `embedding` argument and epistemic status from `act_scope`. Both semantic-frame
   compilation and accepted-observation compilation use the same mapping. Existing
   downstream goal/action contracts therefore receive the corrected meaning.
   `subject_mode` is retained in capture/training; it grants no authority.
3. **Dialogue → checked references.** Code constructs a linear-size menu of exact
   token passages: bounded complete messages/sentences and overlapping windows
   for long text. Each is at most 600 characters. Strict provider schema links
   each message to its own legal spans; prior-position sources admit assistant
   messages only. Shared schema definitions avoid duplicating menus. Bounds,
   speaker and other compiler checks remain independently enforced.
4. **Checked sources → contribution.** Existing source-linked follow-up handoff
   passes exact excerpts, speaker, offsets and resolved question. V1.10 additionally
   passes original message length and omitted-prefix/suffix flags, so a final
   excerpt from a long message is distinguishable from an interior extract.
   Complete source text is included only within the existing 2,000-character
   budget. A recent-history omission does not erase a separately supplied source.
5. **Contribution → authorized answer content.** Character-conditioned input
   v1.3 permits `dialogue_reasoning`: an account of supplied public statements,
   rather than general-world knowledge or an unknown private user fact. It is
   compiled through the existing own-view authority as `dialogue.reasoned_account`,
   not as independently verified fact. Existing answer selection and realization
   consume that content; neither is bypassed.
6. **Comparison semantics.** Contribution instructions distinguish recommendation,
   participation requirement, scope/conditions and strength of support. Default
   and optional are compatible; stronger support need not change a recommendation.
   Genuine reversals and contextual exceptions remain allowed. Source text takes
   precedence over a fallible role label or resolved-question summary.

## Important boundary details

- Empty follow-up sources are allowed in v1.10 when no relevant prior statement
  exists. They are not permission to invent a past opinion. Absence of a source
  labelled `earlier_position` is not proof that no prior statement exists.
- Fine-grained `candidate_refs`, `dialogue_updates`, and current-turn evidence
  retain exact token selectors. Replacing two distinct referents with the same
  sentence would break ambiguity and option/correction handling. This change
  therefore does **not** eliminate every possible numeric-selector failure.
- Runtime compilation still checks bounds/speaker even if an adapter bypasses
  strict output. It does not silently clamp, substitute or drop invalid citations.
- Provider schemas require scope fields on new events; model contracts keep them
  optional for old captures/scripted adapters. Old registries reject new fields.
- Dialogue reasoning is model-produced interpretation of evidence, not a semantic
  entailment proof. No validator can guarantee it has compared the right meanings.
- Source coverage flags indicate text location, not the content of omitted text.
- Opinion confidence/hedging policy is unchanged. Repeated “tentatively” remains
  a visible quality limitation, not evidence of personality-specific uncertainty.

## Files

- `two_call/scope.py`, `call_one_v1_10.txt`: scope mapping and semantic guidance.
- `two_call/source_passages.py`, `v1_provider.py`: source-linked strict schemas.
- `two_call/v1.py`, `v1_contracts.py`, `v1_training.py`: compilation/versioning.
- `two_call/followup.py`: bounded source handoff and source-coverage metadata.
- `two_call/contribution.py`, `contribution_v1_10.txt`: dialogue reasoning.
- `two_call/provider.py`, `orchestrator.py`: versioned prompts and provenance.
- `tests/test_cognitive_scope_sources.py`: general scope/source/replay regressions.
- `scripts/cognitive_scope_development_eval.py`: isolated real-model development
  evaluation; reuses development fixtures without accessing reserved cases.

Strict schema construction was checked against the official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
for nested unions, enums and shared definitions; no model migration was made.

## Verification

The final broad automated regression run passed **376 tests, 1 skipped**, including
the added long-source coverage regression. Scope isolation, source bounds/speaker
guards, contribution authority, old-registry behavior and deterministic replay
are covered. `git diff --check` passed.

Real-model evaluation used isolated in-process shadow runs with actual model
calls, not frontend/WebSocket/worker persistence. No visible replies, database
writes or worker configuration changes were made. Exact inputs/outputs and
terminal traces are retained in the QA directory linked below.

- Initial smoke: **4/4** completed and accepted.
- Expanded development run: **44/44** accepted; **4/4 six-turn closed loops**
  completed under standard and opposing character profiles (one repeat each).
- Hypothetical personal choices reached opinion content; genuine generation and
  external actions retained their respective routes. Capability-limited replies
  on those controls are not proof that artifact/tool functionality was added.
- Both profiles distinguished strengthened support from a genuine reversal.
  Notebook conversations expressed distinct tastes and coherent contextual
  exceptions. Volunteer conversations retained optional participation and
  explained the urgent-meeting exception.
- Acceptance was not treated as correctness: one long-source response falsely
  denied an earlier view. A coverage-instruction retest fixed that response but
  exposed uncertainty about whether a cited ending was the final qualification.
  This led to explicit source-coverage metadata and a final targeted retest.
- Final-revision check: **28/28** accepted, including **4/4 six-turn loops**.
  Both long-source cases preserved the final qualification, and both absent-view
  cases avoided fabricating the alleged earlier belief.
- The bare-reference control still produced the unhelpful question “What would
  you call it?” rather than clearly asking what the referent is. Its clarification
  route remained available; this wording/content issue is not fixed here.
- External-action replies still expose internal “runtime” terminology, and
  generation controls lack a suitable content capability in this harness.
- The final standard-character notebook run still accepted the user's premise
  of an “exception” on turn 3 despite already preferring that title. Contribution
  itself generated this wording. It also correctly said its general preference
  had not changed, and subsequent turns preserved that preference. This is a
  remaining semantic precision defect, not missing history or proof the whole
  opinion-consistency problem is solved. Do not train on that turn as a clean
  positive example.

Exact development artifacts and final verification are indexed in
[the implementation QA report](../../../../../../docs/qa/cognitive-scope-source-fix-2026-10-13/REPORT.md).

These development results are not a matched statistical improvement estimate
against the earlier 120-turn run. They do not establish general human-likeness,
full dynamic-state effects, or frontend continuity. Independent held-out testing
is the next gate, using the reserved cases without tuning against them.
