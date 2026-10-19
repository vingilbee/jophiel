# Dialogue working memory: current component reference

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Dialogue memory is conversation-scoped state used to preserve entities, topics,
questions and source-linked context across turns. Typed snapshots, eligibility,
ordering, updates and projections live in `dialogue_memory_v0`; the application
service owns persistence and catch-up.

Keep speaker attribution, source references and uncertainty. A later user claim
about what Columbina said is not automatically an earlier assistant position.
Memory projection does not guarantee that a model attends to a relevant source.
V1 source-linked follow-ups and passage selection add separate handoffs in
`two_call/continuity.py` and `two_call/source_passages.py`.

Use stored versions and predecessor identities for replay. Do not replace a
missing source with an invented summary or silently reset a conversation to
make a continuity test pass.

## Source contracts and implementation

- [dialogue_memory_v0/models.py](dialogue_memory_v0/models.py)
- [dialogue_memory_v0/updater.py](dialogue_memory_v0/updater.py)
- [dialogue_memory_v0/projector.py](dialogue_memory_v0/projector.py)
- [dialogue_memory_v0/interpreted.py](dialogue_memory_v0/interpreted.py)
- [../../../../services/dialogue_memory.py](../../../../services/dialogue_memory.py)
- [two_call/continuity.py](two_call/continuity.py)
- [two_call/source_passages.py](two_call/source_passages.py)

## Verification and limits

- [test_dialogue_memory_contract.py](../../../../../tests/test_dialogue_memory_contract.py)
- [test_dialogue_memory_updater.py](../../../../../tests/test_dialogue_memory_updater.py)
- [test_dialogue_memory_pipeline.py](../../../../../tests/test_dialogue_memory_pipeline.py)
- [test_cognitive_followup_handoff.py](../../../../../tests/test_cognitive_followup_handoff.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
