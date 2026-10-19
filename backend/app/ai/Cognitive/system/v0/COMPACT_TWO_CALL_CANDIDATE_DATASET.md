# Candidate record format and provenance

> **Document status — 2026-10-18:** Historical compact-protocol proposal (October 4), not the current V1 training schema. See [v1_training.py](two_call/v1_training.py) and the [current guide](README.md). No dataset export or promotion is implied.

2026-10-04. This document specifies an offline candidate export format. It does
not enable new collection, training, retention or external export.

Each permitted record should contain:

- Dataset schema/version and record ID; protocol/context/prompt/schema/compiler
  versions; immutable authorized capture/request/trace digests and source scope.
- Explicit synthetic/real-capture designation and the actual personality projection
  source. Synthetic doubles and simulated outcomes must remain identifiable.
- Exposure-authorized TurnContext and DecisionContext projections, their omissions
  and unavailable fields; local reference table stored separately with restricted
  access to canonical message/user/memory/revision identities.
- Raw MindProposal/ResponseProposal, canonical compiled proposals and exact source
  digests, accepted/rejected status, typed failure stage/code and failed-attempt
  timing where available. A completed compact `CallCapture` already binds the raw
  proposal to its compiled handoff by recompilation. Current failed records do not
  guarantee retention of every raw provider output; an export must not invent one.
- Accepted semantic hypotheses/scenarios and event allocations, unknown/conflict
  records, actual goal links and readiness, StateBefore/StateAfter, content/revision
  proposals, gated action sequence and computed response spans.
- Final validation findings, advisory style diagnostics, accepted candidate digest,
  independent human-review status/provenance, and observed delivery/tool outcomes.
  Proposed wording never establishes delivery, task execution or user acceptance.
- Actual provider token/cache/reasoning counts and latency spans; estimates labeled
  with method/unit and tokenizer unavailable where appropriate. No synthetic
  latency or subjective quality result may be presented as measured model quality.

Raw captures, context, excerpts, generated content and reference tables are sensitive
conversation data. Reuse the existing exposure, ownership, retention and deletion
rules; no automatic expansion of authorized memory or cross-user access. Keep raw
data out of ordinary logs and content-minimized summaries. Export only under an
explicitly approved dataset purpose/destination/retention policy, with redaction,
access control and deletion lineage. This implementation adds no export worker.

LLM readings/appraisals are predictions, not verified human intent, objective truth
or ground-truth training labels. Deterministic contract acceptance means the record
is structurally valid and grounded within its authority scope. It does not mean
the subjective reading or response is good. Separate human review/adjudication and
sampling quality checks are needed before using these candidates as training targets.
