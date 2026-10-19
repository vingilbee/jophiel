# Goal system: lifecycle and evidence reference

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

The Goal engine maintains typed definitions, records, requirements, lifecycle
transitions, priority and focus. Existing goals and prior snapshots are explicit
inputs; adoption, formation and satisfaction use the registered policies and
source evidence. Goal priority is not itself authority to execute an action.

V1 Call 1 can propose impacts on existing goals. Deterministic compilation and
Goal processing own canonical identities and lifecycle; Call 2 sees the resulting
active goals and readiness. Preserve conversation scope and temporal provenance
when carrying goals between turns.

The [goal fixture guide](data/GOAL_DESIGN_CASES_README.md) distinguishes authored
cases from behavioral evidence.

## Source contracts and implementation

- [contracts/goals.py](contracts/goals.py)
- [goal_v0/models.py](goal_v0/models.py)
- [goal_v0/ontology.py](goal_v0/ontology.py)
- [goal_v0/engine.py](goal_v0/engine.py)
- [goal_v0/lifecycle.py](goal_v0/lifecycle.py)
- [goal_v0/formation_policy.py](goal_v0/formation_policy.py)
- [goal_v0/adoption_policy.py](goal_v0/adoption_policy.py)

## Verification and limits

- [test_goal_v0_design_cases_runtime.py](../../../../../tests/test_goal_v0_design_cases_runtime.py)
- [test_goal_v0_lifecycle.py](../../../../../tests/test_goal_v0_lifecycle.py)
- [test_goal_v0_pipeline_integration.py](../../../../../tests/test_goal_v0_pipeline_integration.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.

## 26. Remediation v0.3 (SH-EVAL-013 / SH-EVAL-014)

This historical anchor is retained for the September evaluation reports. The
September 29 remediation addressed missing reconciliation evidence, old goals
retaining primary focus, overly strict adoption support, and contamination
between frames in compound acts. The implementation is represented by
[the evidence producer](goal_v0/evidence_producer.py),
[adoption policy](goal_v0/adoption_policy.py),
[formation policy](goal_v0/formation_policy.py), and
[Goal engine](goal_v0/engine.py).

The [September 29 evaluation](SHADOW_PIPELINE_END_TO_END_EVALUATION_2026-09-29.md)
records historical behavior and limits. That result does not establish current
V1 quality; use the current contracts and corresponding tests for maintenance.
