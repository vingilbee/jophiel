# Interpretation: current runtime and evidence boundary

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

V1 Call 1 receives authorized conversation/context, perception cues, personality
biases, prior state and available memory/relationship context. It proposes
semantic events, readings and Appraisal. The former parser-only instruction
excluding personality and context does not apply to this route.

`two_call.orchestrator.install` installs `ContextInterpretationEngine`,
`UnderstandingGrounder`, `UnderstandingReadingRules`, and `ModelBiasOwner` for V1.
The canonical envelope still carries semantic frames, hypotheses, scenarios,
evidence, unknowns, contradictions and versions. Appraisal remains a separate
canonical output, even though Call 1 predicts both stages.

Structural act/modality rules remain active. The adapter removes legacy pragmatic
and selected social-goal rules and merges model readings. `ModelBiasOwner` avoids
applying the old deterministic bias adjustment a second time. The offline default
`InterpretationEngine()` still uses deterministic grounding and bias policy;
it is not interchangeable with the model-backed V1 installation.

## Evidence, meaning and conditioning

- Source identity, role, bounds and attribution must survive compilation.
- Unknown information and competing readings stay explicit. A valid source
  handle proves addressability, not that a proposed reading follows from it.
- Personality and state condition judgments; they do not create source facts.
- Clear source meaning stays grounded while appraisal and Action can vary with
  state. Do not infer an unobserved private mental state as an observed fact.
- Preserve recorded versions and replay semantics when changing these contracts.

### 17.1 Ambiguity gate

This heading is retained for references to the old brief. The legacy one-step,
ambiguity-gated support adjustment is implemented in `interpretation_v0/bias.py`.
It is not proof that the V1 model obeys the same bound. Model-conditioning effects
and proposed limits need evaluation against the
[conditioning expectations](CONDITIONING_EXPECTATIONS_V0_DRAFT.md).

The design-case JSON is a regression/design fixture. Its expected labels are not
approved teacher-training targets; see its
[fixture guide](data/INTERPRETATION_DESIGN_CASES_README.md).

## Source contracts and implementation

- [contracts/interpretation.py](contracts/interpretation.py)
- [interpretation_v0/models.py](interpretation_v0/models.py)
- [interpretation_v0/ontology.py](interpretation_v0/ontology.py)
- [interpretation_v0/engine.py](interpretation_v0/engine.py)
- [interpretation_v0/bias.py](interpretation_v0/bias.py)
- [two_call/compact_runtime.py](two_call/compact_runtime.py)
- [two_call/v1.py](two_call/v1.py)
- [two_call/orchestrator.py](two_call/orchestrator.py)

## Verification and limits

- [test_cognitive_two_call_v1.py](../../../../../tests/test_cognitive_two_call_v1.py)
- [test_cognitive_scope_sources.py](../../../../../tests/test_cognitive_scope_sources.py)
- [test_cognitive_continuity_regression.py](../../../../../tests/test_cognitive_continuity_regression.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
