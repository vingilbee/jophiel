# Realization: wording after the cognitive plan

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Realization expresses a selected Action plan using its available content and
conversation context. The compiler builds a typed request, a provider proposes
wording, and the validator records acceptance or failure under the selected
versioned policy. Wording is excluded from the cognitive training target.

Preserve selected meaning, source attribution, uncertainty and execution status.
A planned action is not evidence that a tool ran or a memory was written. Content
availability depends on the route: current V1 can carry model general knowledge
and character-conditioned contributions; the original closed-world-only brief
is not a universal rule for V1.

Older strict and newer conversational policies coexist for replay. Wording
acceptance is not proof of semantic entailment or appropriate cognition. False
rejections need evaluation too; do not optimize only validator acceptance.

The [fixture guide](data/REALIZATION_DESIGN_CASES_README.md) explains the authored
compiler/validator cases and their limits.

## Source contracts and implementation

- [contracts/realization.py](contracts/realization.py)
- [realization_v0/compiler.py](realization_v0/compiler.py)
- [realization_v0/engine.py](realization_v0/engine.py)
- [realization_v0/validator.py](realization_v0/validator.py)
- [realization_v0/model_protocol.py](realization_v0/model_protocol.py)
- [live_shadow/realization.py](live_shadow/realization.py)
- [two_call/contribution.py](two_call/contribution.py)

## Verification and limits

- [test_realization_v0_design_cases.py](../../../../../tests/test_realization_v0_design_cases.py)
- [test_realization_v0_validator.py](../../../../../tests/test_realization_v0_validator.py)
- [test_realization_answer_fidelity.py](../../../../../tests/test_realization_answer_fidelity.py)
- [test_realization_conversation_context.py](../../../../../tests/test_realization_conversation_context.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
