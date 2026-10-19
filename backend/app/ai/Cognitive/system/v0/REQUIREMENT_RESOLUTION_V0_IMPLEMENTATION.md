# Requirement resolution: readiness and content

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Requirements distinguish missing user information, unavailable capability,
content gaps and satisfied prerequisites. Typed resolutions feed candidate
construction, policy planning and Realization content. They must not silently
turn unknown information into a fact or promise an unavailable operation.

V1 installs `ContinuityRequirementEngine` around the base resolver and can use
conditional content/contribution generation. A missing referent and missing
knowledge are different conditions; preserve the accepted interpretation's
uncertainty and source linkage.

Review readiness and candidate availability when diagnosing an Action failure.
A valid acknowledgment after all answer candidates were removed may expose an
earlier routing or requirement problem.

## Source contracts and implementation

- [contracts/requirement_resolution.py](contracts/requirement_resolution.py)
- [requirement_resolution_v0/engine.py](requirement_resolution_v0/engine.py)
- [requirement_resolution_v0/manifest.py](requirement_resolution_v0/manifest.py)
- [producers_v0/](producers_v0/)
- [two_call/continuity.py](two_call/continuity.py)
- [two_call/contribution.py](two_call/contribution.py)

## Verification and limits

- [test_requirement_resolution_contract.py](../../../../../tests/test_requirement_resolution_contract.py)
- [test_requirement_resolution_engine.py](../../../../../tests/test_requirement_resolution_engine.py)
- [test_cognitive_continuity_regression.py](../../../../../tests/test_cognitive_continuity_regression.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
