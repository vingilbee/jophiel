# Evidence and attribution

Status: design decisions accepted 2026-11-01; implementation requested and built 2026-11-01 (uncommitted, branch
`feature/learning-02-evidence-attribution`): see [Step 2 implementation](02-implementation.md) for the contracts,
settings and what isn't covered. [Plan index](README.md).
This plan is authoritative for the accepted evidence and attribution decisions below.

## Outcome and ownership

Feedback reaches the relevant attempt or sequence, and evidence determines whether any component needs correction.
Identifying the target of feedback and explaining its cause are separate judgments.

Dependencies: Step 1. Its accepted capture, ownership, privacy, retention, and deletion rules remain applicable.

Learning owns retrospective assessment: understanding what happened and what the evidence supports. Thought reasons
about the current situation and may propose explanations or questions. They may share a reasoning model and utilities,
but a thought is not automatically a confirmed assessment. Learning does not independently speak or act.

Assessment is not training-data conversion. Step 3 decides how supported assessments become scoped adaptations;
Step 5 decides whether they justify learner-specific training examples using decision-time inputs. An assessment may
produce neither a lesson nor training data. Memory retains ownership of memory writes.

## Evaluator responsibilities

- Code gathers linked records and checks concrete facts, including delivery status and defined domain measurements.
- An LLM examines meaning and proposes causes grounded in those records. It may conclude that the cause is unclear.
- A source has authority only over what it can establish. The user is authoritative about their intended meaning and
  expressed preferences; their external factual claims remain attributed claims. A user correction does not by itself
  establish which internal component caused the mismatch.
- Code-counted results establish the measurement they make, not overall strategy quality. Model explanations remain
  revisable judgments; evaluator agreement alone does not establish truth.

## Linking feedback and assigning responsibility

Keep the feedback target separate from the proposed explanation. Knowing which reply annoyed the user does not establish
whether interpretation, choice, wording, timing, or delivery caused the problem. Participation links are not causal proof.

For delayed feedback, begin with the current conversation and explicitly referenced records. Examine relevant earlier
attempts or sequences rather than automatically assigning feedback to the latest reply. Preserve multiple candidate
links when the target is ambiguous.

Retain competing explanations and distinguish choice quality given available information from eventual outcome.
Negative feedback can remain recorded without a learner-specific correction. Learning may propose a clarification to
cognition, which decides whether asking is appropriate.

Incomplete records do not prevent every assessment. Preserve conclusions supported by the available evidence, identify
missing information, and withhold conclusions that depend on it. For example, a stated preference can be established even
when a missing decision record prevents component attribution.

## Uncertainty and explanations

V1 uses evidence-backed qualitative judgments rather than LLM-generated confidence percentages. The accepted starting
categories are `supported`, `plausible`, `unresolved`, and `contradicted`; these are not numerical probabilities.
Represent uncertainty separately for the feedback target and proposed cause. Distinguish missing capture from conflicting
evidence. Numerical confidence would require a defined interpretation and evaluation before adoption.

Every assessment includes a short, inspectable explanation of the relevant evidence, conclusion, uncertainty, and other
plausible explanations, including why responsibility was left unresolved.

## Outcome assessment and personality

Assess the relevant dimensions separately; unsupported dimensions remain unknown, not zero. V1 does not require numeric
scores or one overall good/bad score.

| Dimension | Question |
| --- | --- |
| Goal progress | Did this advance the current intention? |
| Understanding | Did the interpretation fit the evidence? |
| Relationship | Did it respect expressed preferences and commitments? |
| Knowledge gain | Did it resolve a useful uncertainty? |
| Cost | What time, effort, resources, interruption, damage, or recovery did it require? |
| Character fit | Did the behavior fit her values and personality? |

Keep observations, interpretations, and personal appraisal distinguishable. Personality affects how she values outcomes,
not what the evidence establishes. Core values remain fixed; preferences and strategies can adapt. Affinity and mood
remain outside V1. Domain-specific measurements and any later numeric weighting are not settled by these dimensions.

A current instruction governs the current interaction. If an earlier preference differs, first consider contextual
variation rather than automatically replacing it. Revise the broader preference when the user explicitly changes it or
further evidence supports the revision.

## Triggers, stopping, and human review

Explicit corrections and execution failures trigger assessment automatically. Sample ordinary interactions, including
successes, so learning is not restricted to problems. The sampling policy remains an implementation decision.

Clear, supported assessments proceed without routine human approval. Ambiguous cases remain unresolved; they do not
create a mandatory human review queue. Users can inspect and correct assessments. Downstream adaptation and training
retain their own acceptance rules.

There is no fixed investigation budget. Start with directly relevant evidence and continue only while there is a useful
next step. Stop when:

- The evidence supports a conclusion.
- Resolution requires missing evidence: record the unresolved issue and what evidence is needed.
- Reasoning is stuck: explanations repeat without new evidence or a distinct way to test them.

Another model call or reworded explanation is not progress. Relevant new evidence can reopen an unresolved assessment.
This stopping policy concerns retrospective investigation, not authorization for unlimited external actions or trials.

## Revision and evidence independence

New evidence can supersede an assessment's current conclusion while preserving its history, subject to the accepted
retention and deletion rules. Flag dependent conclusions for reconsideration through their owners. Do not overwrite raw
observations. Preserve evaluator/version and derivation lineage so the reason for a revision is inspectable.

Repeated reflection, multiple evaluator calls, synthetic variants, and repeated citations of the same experience do not
create independent evidence. Several interpretations of one event remain interpretations of one event.

## Implementation work remaining

- Add a retryable learning consumer using existing observation receipts where compatible.
- Define concrete evaluator and storage contracts for target links, causal explanations, evidence, uncertainty,
  dimension assessments, versions, and supersession.
- Implement delayed linking, revision propagation, ordinary-interaction sampling, and detection of stalled reasoning.
- Define domain-specific measurements without treating them as universal success signals.
- Expose evidence, attribution, assessment history, and unresolved cases in a minimal inspection view.

These details require implementation planning; the accepted design does not authorize implementation or settle downstream
lesson acceptance, training-label review, numerical weights, or model promotion rules.

## Completion evidence

Use a fixed set of cases with expected conclusions covering:

- Correct interpretation with bad wording, and a wording or delivery failure that does not automatically label
  interpretation or action choice as wrong.
- Clear corrections that reach the supported component, including unwanted advice despite correct understanding.
- Delayed feedback about an earlier action, feedback about a sequence, and ambiguous target links.
- Lucky success after a poor choice, with outcome separated from choice quality.
- Incomplete capture, missing evidence, conflicting evidence, and justified abstention.
- New evidence superseding an assessment and flagging dependent conclusions for reconsideration.
- Repeated reflection not increasing independent evidence, and stalled reasoning stopping without forced conclusions.
- Reprocessing without duplicate evidence.

Require no unsupported blame in the essential failure cases and correct attribution for clear corrections. Evaluate both
incorrect blame and missed supported attribution: always answering “unresolved” fails. Broader numeric evaluation
thresholds remain open; assessment correctness alone does not demonstrate adaptation or training improvement.
