# Action selection: candidate construction and policy

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Code constructs typed candidates and evaluates evidence, capability,
authorization, safety and lifecycle gates. V1 Call 2 selects and orders supplied
eligible candidates; it cannot select an action absent from that inventory.
The compiled plan records bound targets, parameters and selection provenance.

The deterministic planner remains relevant to offline and older routes. Its
score ordering must not be mistaken for the V1 model's decision rule. Inspect
`two_call/v1.py` and the installed adapter for the current policy boundary.

The [registry](data/CONVERSATIONAL_ACTION_REGISTRY_V0.md) and
[deterministic design](ACTION_SELECTION_V0_DESIGN.md) contain lifecycle lists and
transition tables read by registry tests. They remain compatibility references;
their older planner prose does not replace V1 selection.

Evaluation must distinguish candidate coverage, valid selection and appropriate
cognitive choice. Passing a gate does not mean an action is the best response.

## Source contracts and implementation

- [contracts/actions.py](contracts/actions.py)
- [action_selection_v0/registry.py](action_selection_v0/registry.py)
- [action_selection_v0/construction.py](action_selection_v0/construction.py)
- [action_selection_v0/gates.py](action_selection_v0/gates.py)
- [action_selection_v0/engine.py](action_selection_v0/engine.py)
- [two_call/v1.py](two_call/v1.py)
- [two_call/orchestrator.py](two_call/orchestrator.py)

## Verification and limits

- [test_action_v0_registry.py](../../../../../tests/test_action_v0_registry.py)
- [test_action_v0_gates.py](../../../../../tests/test_action_v0_gates.py)
- [test_action_v0_construction.py](../../../../../tests/test_action_v0_construction.py)
- [test_cognitive_two_call_v1.py](../../../../../tests/test_cognitive_two_call_v1.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
