# Personality runtime mapper: current projection

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Mapper v0.2 consumes fourteen numeric core fields. `SOURCE_PATHS` defines their
actual locations; fields stored elsewhere in core memory are not automatically
consumed. Written character content is a separate projection.

The mapper compiles Interpretation biases, Dynamic State personality controls,
and Action personality from one immutable source. The manifest owns transforms,
bounds and rounding. Capture stores the projection; workers must not silently
remap an old capture using newer core memory.

Current additions are hostile attribution, social-cue sensitivity, confirmation
tendency derived as `1 - contradiction_sensitivity`, and emotional-stability
control bands. The latter apply to every state key's sensitivity, maximum step,
recovery and persistence. State baselines and interaction patience retain their
existing defaults. Self-attribution stays a neutral constant.

These mappings are implemented assumptions, not established behavioral effects.
The [conditioning draft](CONDITIONING_EXPECTATIONS_V0_DRAFT.md) and real-model
contrasts assess their consequences. Earlier stored profiles retain their
captured values; new optional bias fields are absent from legacy JSON.

This filename is retained because runtime projections use it as `review_ref`.
It no longer instructs an agent to implement mapper v0.1.

## Source contracts and implementation

- [personality_runtime_v0/models.py](personality_runtime_v0/models.py)
- [personality_runtime_v0/manifest.py](personality_runtime_v0/manifest.py)
- [personality_runtime_v0/mapper.py](personality_runtime_v0/mapper.py)
- [../../../../services/cognitive_shadow.py](../../../../services/cognitive_shadow.py)

## Verification and limits

- [test_personality_runtime_v0_mapper.py](../../../../../tests/test_personality_runtime_v0_mapper.py)
- [test_personality_runtime_v0_contract.py](../../../../../tests/test_personality_runtime_v0_contract.py)
- [test_personality_runtime_v0_shadow_integration.py](../../../../../tests/test_personality_runtime_v0_shadow_integration.py)
- [test_cognitive_conditioning_guide.py](../../../../../tests/test_cognitive_conditioning_guide.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
