# Cognitive system: current guide

Updated 2026-10-18. Start here for the goal, implemented architecture, evaluation
standard, and outstanding work. Implementation status below describes the working
checkout; it does not establish deployment or behavioral acceptance.

Use the [document index](DOCUMENTATION_INDEX.md) to distinguish current component
guides, compatibility contracts, proposals and dated evidence. Completed authoring
assignments have been replaced with focused references. The duplicate historical
README has been removed; runtime contract definitions live in the linked source
models and component guides.

## Goal and scope

Columbina is a general conversational agent with persistent identity, personality,
memory, and cognitive state. Ordinary social conversation, reflection, curiosity,
disagreement, and practical collaboration all belong in evaluation. Nutrition is
a specialist capability, not the default cognitive task.

The model-backed cognitive pipeline is a **teacher for a future fast local
cognitive model**. Its reviewed examples should connect the conversation and
Columbina's biases, character, dynamic state, and eventually relationship context
to Interpretation, Appraisal, and an Action plan. The training target ends at the
Action plan; Realization wording is separate.

The teacher is not yet a validated source of training labels. The current
training projector produces unreviewed candidates, not approved examples.

## Implemented pipeline

The current V1 implementation uses protocol `cognitive-two-call-v1` and argument
registry `argument-kind-registry-v1.13`.

```text
Message + authorized history, memory, runtime facts, personality, prior state
  -> deterministic Perception
  -> Call 1: Interpretation and Appraisal proposals
  -> deterministic compilation, state update, Goals and Requirements
  -> content/contribution generation when required
  -> Call 2: selection from code-prepared eligible Action candidates
  -> deterministic Action validation and plan compilation
  -> separate Realization
```

“Two-call” names the two cognitive decisions. Conditional contribution and
separate Realization can add model calls. Perception cues are currently produced
by deterministic detectors; they are not currently predicted by Call 1.

Deterministic interpretation has not disappeared: V1 installs
`UnderstandingReadingRules`, which retains structural interpretation rules while
incorporating model readings. Candidate construction and gates also constrain
what Call 2 can select. These are active dependencies, not merely legacy files.

| Responsibility | Implementation entry point |
| --- | --- |
| Protocol installation and stage orchestration | [two_call/orchestrator.py](two_call/orchestrator.py) |
| Call 1/Call 2 input compilation and validation | [two_call/v1.py](two_call/v1.py) |
| Protocol, schema and registry versions | [two_call/v1_contracts.py](two_call/v1_contracts.py) |
| Model readings and structural interpretation | [two_call/compact_runtime.py](two_call/compact_runtime.py) |
| Core-memory projection | [personality_runtime_v0/mapper.py](personality_runtime_v0/mapper.py), [manifest](personality_runtime_v0/manifest.py) |
| Shadow capture and service configuration | [cognitive_shadow.py](../../../../services/cognitive_shadow.py) |
| Candidate training projection | [two_call/v1_training.py](two_call/v1_training.py) |

V1 availability does not mean the live shadow worker is using it. The earlier
review reported local configuration using `context-conditioned-two-call-v0.1`.
Check the configured protocol and captured trace provenance before interpreting
new live results. This document does not switch protocols or authorize rollout.

## Current conditioning work

**Direction (2026-10-18):** Columbina's personality reaches the teacher as a
written **character page** ([docs/characters/](../../../../../../docs/characters/)),
not as bias numbers. Matched-input runs showed bias numbers barely change readings
(10 of 72 expectations), whether the model applies them or code does; two
different pages on the same messages gave clearly different, page-consistent
behaviour (11 of 13), with plain questions unchanged. Plan: fixed character page,
strong offline teacher, own real and synthetic data, a student model, then
learning from reactions. Evidence, invalidated runs and open gaps (her own
feelings are not represented; no repair move is ever chosen):
[conditioning report](../../../../../../docs/qa/cognitive-conditioning-2026-10-18/REPORT.md).

- **Character page (`character-page-v1`):** `install(..., character_page=text)`
  puts the page in both Call 1 (`context.character_page`) and Call 2
  (`character_page`), with no bias numbers; it replays from the payload.
- **Code-applied biases (guides v2/v3):** the model names which biases bear on
  each reading and gives likelihoods; [two_call/v1_bias.py](two_call/v1_bias.py)
  applies her values, draws the lead with a recorded seed and stores weights.
  Kept and replayable, no longer the main path; v3 is the default for mapper
  v0.2 profiles without a page.

Earlier work in this round:

- **Personality mapper v0.2:** consumes 14 numeric core fields, up from 10.
  Hostile attribution and social-cue sensitivity become explicit biases.
  Confirmation tendency is mapped to `1 - contradiction_sensitivity`.
  Emotional stability sets sensitivity, maximum step, recovery, and persistence
  bands for every state key. Baselines and interaction patience remain at their
  existing defaults; self-attribution remains a neutral constant.
- **Call 1 conditioning guide:** profiles carrying the new biases receive nine
  bias meanings and the `conditioning-guide-v1` instructions. Earlier profiles
  retain their original payload/prompt behavior. Stored projections retain their
  capture-time values.
- **Matched-input harness:**
  [cognitive_conditioning_eval.py](../../../../../scripts/cognitive_conditioning_eval.py)
  exercises biases, state, clear-message controls, and unchanged-input noise
  through the model-backed pipeline, excluding Realization (`--guide`,
  `--character`, `--reasoning`, two-page fixtures, `analyze --score lead`). Its fixtures and
  frozen core are in the
  [conditioning evaluation directory](../../../../../../docs/qa/cognitive-conditioning-2026-10-18/).
- **Relationship:** live capture still omits the relationship projection.
  Relationship/affinity work is deliberately deferred until biases and state
  have been evaluated.

The [conditioning expectations draft](CONDITIONING_EXPECTATIONS_V0_DRAFT.md)
records intended effects and unresolved decisions, with wiring status updated
for mapper v0.2. Its mappings and scoring thresholds remain subject to behavioral
review; the draft is not proof that those effects have been demonstrated.

## Evaluation standard

Judge cognition against the supplied evidence and Columbina's defined inputs.
Do not require an average person's preference, agreement with the user, or a
particular conversational style. Contract acceptance and coherent wording alone
are not evidence of correct cognition.

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| Scripted unit/integration tests | Contracts, mappings, gates, handoffs and replay work for supplied decisions | The real model independently makes those decisions |
| Real-model development conversations | Observed behavior and stage-local failure mechanisms | Generalization to unfamiliar conversations |
| Matched-input contrasts | Whether an input changes cognitive outputs as defined | That the input definition itself is appropriate |
| Held-out evaluation after a freeze | Transfer to cases not used for that implementation's tuning | Broad reliability from a small sample |

For conditioning evaluation:

1. Keep message and history fixed, vary one defined bias or state axis, and verify
   the actual values received at the provider boundary. A calm/strained bundle
   measures a combined effect, not each state axis independently.
2. Define expected directions before running. Inspect readings, uncertainty,
   appraisal, eligible candidates, and selected Actions. Distinguish a missing
   candidate from a poor selection.
3. Check appropriate stability as well as change. Clear source meaning stays
   grounded; state may still change how a clear message is appraised and how
   Columbina responds. Comfort with uncertainty must not invent missing facts.
4. Repeat unchanged and contrasted inputs to measure model variation. Three to
   five repetitions are exploratory evidence; the draft's 30/10 percentage-point
   thresholds are provisional, not a demonstrated reliability guarantee.
5. Include unfamiliar, ordinary conversations as well as explicit reasoning
   probes. A prompt that asks the model to distinguish revision from stronger
   support does not establish that it does so spontaneously.
6. Keep development and held-out cases separate. Once a held-out result informs
   a change, use fresh cases for the next held-out evaluation.

See the [general conversational test plan](../../../../../../docs/COLUMBINA_CONVERSATIONAL_TEST_PLAN.md)
for product coverage. Realization tests remain useful delivery tests, but their
wording scores are outside the cognitive training target.

## Evidence and unresolved issues

| Item | Current status / next evidence needed |
| --- | --- |
| Bias/state influence | Earlier long-conversation work did not establish recurring state-caused judgment differences. Evaluate the new conditioning work before claiming success. |
| Personality-driven state movement | Mapper v0.2 now changes control bands. Mapping tests verify the formula; appropriateness and multi-turn behavior still need evaluation. |
| Input fidelity | A previous “blunt” fixture set curiosity under the wrong core group. Audit delivered projections, not intended overrides. |
| Unused core fields | Some remain unused intentionally. Adding a field needs a defined effect and evidence, not merely a desire to wire every stored number. |
| Live protocol | Verify configuration and trace provenance before attributing live behavior to V1 fixes. |
| Training readiness | Projection is unreviewed and does not export/promote examples. Review both label quality and whether projected inputs preserve the context needed to learn their targets. |
| Generalization | Repeated development scenarios cannot establish broad cognition. Preserve regressions and add fresh evaluation rather than optimizing only familiar wording. |
| Expectations | Resolve remaining draft ambiguities and assess mapping assumptions; do not treat a test's expected direction as independently proven. |

Relevant evidence:

- [Long-state evaluation, October 13](../../../../../../docs/qa/cognitive-long-state-2026-10-13/REPORT.md):
  continuity, input audits, state trajectories, and limits of state-effect claims.
- [Root-cause findings, October 13](../../../../../../docs/qa/cognitive-root-cause-2026-10-13/FINDINGS.md):
  routing, source boundaries, and downstream consequences of early mistakes.
- [Premise-fix evaluation, October 13](../../../../../../docs/qa/cognitive-premise-fix-2026-10-13/REPORT.md):
  the reserved cases **were subsequently run** after a freeze, with 20/20 turns
  completing and one incorrect relation label. The earlier README's “never run”
  statement is superseded. Those cases are no longer an untouched reserve.
- [Long-conversation handoff repairs](../../../../../../docs/qa/cognitive-long-fix-2026-10-13/REPORT.md):
  registry v1.13 repairs and their verification limits.

Immediate work is to finish and review the current conditioning evaluation,
resolve its expectation issues, and assess transfer to unfamiliar inputs.
Relationship projection, training promotion, and live protocol activation remain
separate follow-up decisions. Avoid new scenario-specific wording fixes as a
substitute for evaluating the cognitive teacher.

## Documentation map

| Read when… | Document |
| --- | --- |
| Defining the teacher/student boundary and candidate labels | [Training schema proposal](SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md) — design reference; compare with the current projector |
| Reviewing the two cognitive calls | [Two-call boundaries](CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md) — current responsibilities and source definitions |
| Reviewing conditioning expectations | [Conditioning draft](CONDITIONING_EXPECTATIONS_V0_DRAFT.md) |
| Finding component contracts or historical evidence | [Document index](DOCUMENTATION_INDEX.md) |
| Working on the existing design fixtures | [data/](data/) — JSON cases are still consumed by tests; fixture guides explain their scope |
| Understanding an older report or protocol | Dated implementation/evaluation documents beside this README and in [docs/qa](../../../../../../docs/qa/) |

Historical numbered-section references identify earlier briefs. Use the component
guides and source contracts in the document index for current implementation details.

### Dialogue Working Memory V0 status

See the [current component guide](DIALOGUE_WORKING_MEMORY_V0_IMPLEMENTATION.md)
for snapshot/update/projection contracts and tests. This heading preserves the
existing inbound link.

Keep this README focused on current orientation. Record new experiments in dated
reports and link their conclusions here, distinguishing implementation, observed
behavior, and unresolved decisions. Documentation cleanup should not delete
active fixtures, runtime code, or replay evidence.
