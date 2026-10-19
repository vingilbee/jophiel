# Context-conditioned two-call pipeline: current boundary reference

Updated 2026-10-18. The original October 5 implementation handoff has been
implemented and subsequently revised. This file summarizes current responsibilities;
it is not a pending redesign assignment. See the [current guide](README.md).

## Call 1

The input compiler supplies authorized dialogue, perception cues, biases, prior
state, available memory/relationship context, existing goals, runtime facts and
versioned ontology choices. The model proposes Interpretation and Appraisal,
including explicit unknowns and source-linked readings. Code validates and
compiles canonical records. A valid citation does not prove semantic correctness.

## Middle stages and Call 2

Code owns Dynamic State transitions, Goal lifecycle and Requirement resolution.
Content/contribution generation can supply answer material before policy selection.
Code constructs and gates Action candidates; Call 2 selects and orders eligible
candidates. It does not author final wording or grant execution authority.

## Realization and training

Realization is separate from the cognitive target. “Two-call” refers to the two
cognitive decisions, not a universal limit of two provider requests. Candidate
training records remain unreviewed until separately evaluated and promoted.

## Current definitions and verification

- [Input/compiler and candidate views](two_call/v1.py)
- [Versioned contracts](two_call/v1_contracts.py)
- [Protocol installation](two_call/orchestrator.py)
- [Training projector](two_call/v1_training.py)
- [Scripted contract tests](../../../../../tests/test_cognitive_two_call_v1.py)
- [Conditioning expectations](CONDITIONING_EXPECTATIONS_V0_DRAFT.md)
- [Original implementation report](COGNITIVE_TWO_CALL_V1_IMPLEMENTATION_REPORT.md)

The [training schema proposal](SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md)
retains the original design rationale. Numbered references such as “handoff §18”
in older reports/tests identify the original brief, not this current summary.
