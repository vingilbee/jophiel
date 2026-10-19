# Collaborative content and inquiry: component reference

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Structured collaboration handles conversational artifacts, their revisions and
inquiries. Typed proposals compile against supplied workspace targets and
requirements. The workspace distinguishes shadow/simulated authority from actual
application persistence and execution.

The original dinner example was a development fixture, not Columbina's default
purpose. Ordinary conversation must not be forced into artifact creation. A
personal view, a request to generate a deliverable, and an external operation
have different paths and authority requirements.

Use the current contracts and adapters below for fields and eligibility. The
[October 2 implementation report](CONVERSATIONAL_WORKSPACE_AND_COLLABORATIVE_CONTENT_V0_IMPLEMENTATION.md)
records that implementation's completed and deferred gates; it is not a current
claim that every workspace capability is available.

## Source contracts and implementation

- [contracts/collaboration.py](contracts/collaboration.py)
- [contracts/workspace.py](contracts/workspace.py)
- [collaborative_content_v0/engine.py](collaborative_content_v0/engine.py)
- [collaborative_content_v0/compiler.py](collaborative_content_v0/compiler.py)
- [collaborative_content_v0/inquiry_policy.py](collaborative_content_v0/inquiry_policy.py)
- [conversation_workspace_v0/](conversation_workspace_v0/)

## Verification and limits

- [test_conversation_workspace.py](../../../../../tests/test_conversation_workspace.py)
- [test_collaborative_content_compiler.py](../../../../../tests/test_collaborative_content_compiler.py)
- [test_collaborative_live_shadow.py](../../../../../tests/test_collaborative_live_shadow.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
