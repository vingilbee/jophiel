# Cognitive documentation index

Reviewed 2026-10-18. Start with [README.md](README.md). This index covers all
Markdown under `v0/`; the system-root README describes the older prototype.

Current guides describe the working implementation and link its source contracts.
Compatibility documents retain exact tables used by tests. Proposals express
intended designs and open decisions. Dated evidence reports only its own revision
and sample; its old next-step instructions are not the current backlog.

The completed implementation/fixture assignments were shortened in place so
existing filenames and runtime review references remain resolvable. The duplicate
`README_HISTORY.md` was removed. Historical numeric section references may refer
to the retired briefs; no runtime schema, fixture or captured trace was removed.

## Current orientation and component guides

- [Action selection: candidate construction and policy](ACTION_SELECTION_V0_IMPLEMENTATION.md)
- [Cognitive pipeline: orchestration reference](COGNITIVE_PIPELINE_V0_IMPLEMENTATION.md)
- [Cognitive turn: canonical stage handoffs](COGNITIVE_TURN_V0_IMPLEMENTATION.md)
- [Collaborative content and inquiry: component reference](COLLABORATIVE_CONTENT_AND_INQUIRY_V0_IMPLEMENTATION_INSTRUCTIONS.md)
- [Context-conditioned two-call pipeline: current boundary reference](CONTEXT_CONDITIONED_TWO_CALL_PIPELINE_REDESIGN.md)
- [Dialogue working memory: current component reference](DIALOGUE_WORKING_MEMORY_V0_IMPLEMENTATION.md)
- [Durable shadow capture, observation and replay](DURABLE_LIVE_SHADOW_TRACE_V0_IMPLEMENTATION.md)
- [Goal system: lifecycle and evidence reference](GOAL_V0_IMPLEMENTATION.md)
- [Interpretation: current runtime and evidence boundary](INTERPRETATION_V0_IMPLEMENTATION.md)
- [Local shadow verification reference](LOCAL_LIVE_SHADOW_CANARY_V0_IMPLEMENTATION.md)
- [Perception: current deterministic cue extraction](PERCEPTION_V0_IMPLEMENTATION.md)
- [Personality runtime mapper: current projection](PERSONALITY_RUNTIME_MAPPER_V0_IMPLEMENTATION.md)
- [Cognitive system: current guide](README.md)
- [Realization: wording after the cognitive plan](REALIZATION_V0_IMPLEMENTATION.md)
- [Requirement resolution: readiness and content](REQUIREMENT_RESOLUTION_V0_IMPLEMENTATION.md)

## Current conditioning work (expectations still under review)

- [Conditioning expectations V0 (draft for review)](CONDITIONING_EXPECTATIONS_V0_DRAFT.md)

## Fixture guides

- [Appraisal fixture guide](data/APPRAISAL_DESIGN_CASES_README.md)
- [Conversational Action fixture guide](data/CONVERSATIONAL_ACTION_DESIGN_CASES_README.md)
- [Dynamic State fixture guide](data/DYNAMIC_STATE_DESIGN_CASES_README.md)
- [Goal fixture guide](data/GOAL_DESIGN_CASES_README.md)
- [Interpretation fixture guide](data/INTERPRETATION_DESIGN_CASES_README.md)
- [Cognitive V0 fixtures and schema references](data/README.md)
- [Realization fixture guide](data/REALIZATION_DESIGN_CASES_README.md)

## Compatibility contracts and schema/design references

- [Action Selection V0 accepted design](ACTION_SELECTION_V0_DESIGN.md)
- [Candidate record format and provenance](COMPACT_TWO_CALL_CANDIDATE_DATASET.md)
- [Simplified two-call cognitive training schema proposal](SIMPLIFIED_TWO_CALL_COGNITIVE_TRAINING_SCHEMA_PROPOSAL.md)
- [Appraisal Schema V0.1](data/APPRAISAL_SCHEMA_V0.md)
- [Conversational action registry V0](data/CONVERSATIONAL_ACTION_REGISTRY_V0.md)
- [Dynamic State Schema V0.1](data/DYNAMIC_STATE_SCHEMA_V0.md)
- [Outcome Observation Schema V0.1 Draft](data/OUTCOME_OBSERVATION_SCHEMA_V0.md)

## Superseded roadmap

- [Earlier phased cognition experiment: reference](PHASED_MODEL_BACKED_COGNITION_AND_LATENCY_IMPLEMENTATION.md)

## Dated implementation and evaluation evidence

- [Cognitive continuity, reference, correction, and ambiguity diagnostic — 2026-10-10](COGNITIVE_CONTINUITY_REFERENCE_CORRECTION_DIAGNOSTIC_2026-10-10.md)
- [Continuity diagnostic retest — 2026-10-11](COGNITIVE_CONTINUITY_RETEST_2026-10-11.md)
- [Continuity v1.4 end-to-end shadow evaluation — 2026-10-10](COGNITIVE_CONTINUITY_V1_4_E2E_EVALUATION_2026-10-10.md)
- [Cognitive two-call protocol v1 — implementation report](COGNITIVE_TWO_CALL_V1_IMPLEMENTATION_REPORT.md)
- [Collaborative Content and Inquiry V0 — Behavioral Results (2026-10-02)](COLLABORATIVE_CONTENT_AND_INQUIRY_V0_BEHAVIORAL_RESULTS_2026-10-02.md)
- [Compact protocol behavioral verification status](COMPACT_TWO_CALL_BEHAVIORAL_REPORT.md)
- [Compact context-conditioned two-call implementation](COMPACT_TWO_CALL_IMPLEMENTATION_REPORT.md)
- [Compact two-call latency and size record](COMPACT_TWO_CALL_LATENCY_REPORT.md)
- [Conversational Workspace and Collaborative Content V0 — Implementation Report](CONVERSATIONAL_WORKSPACE_AND_COLLABORATIVE_CONTENT_V0_IMPLEMENTATION.md)
- [Source-linked follow-up handoff — 2026-10-12](FOLLOWUP_HANDOFF_V1_9_IMPLEMENTATION.md)
- [General-conversation action-exploration retest — 2026-10-06](GENERAL_CONVERSATION_ACTION_EXPLORATION_15_TURN_RETEST_2026-10-06.md)
- [General-conversation two-call Cognitive evaluation — 2026-10-05](GENERAL_CONVERSATION_TWO_CALL_EVALUATION_2026-10-05.md)
- [General-conversation e2e retest, cognitive two-call v1 — 2026-10-05](GENERAL_CONVERSATION_V1_E2E_EVALUATION_2026-10-05.md)
- [Phase 1 Live Cognitive Shadow Latency Evaluation](PHASE_1_LIVE_SHADOW_LATENCY_EVALUATION_2026-10-02.md)
- [Phase 1 GPT-6 Luna Live Shadow Latency Retest](PHASE_1_LUNA_LIVE_SHADOW_LATENCY_RETEST_2026-10-02.md)
- [Phase 1 — model-backed appraisal implementation report](PHASE_1_MODEL_APPRAISAL_IMPLEMENTATION_REPORT.md)
- [Typed Requirement Resolution V0 — Behavioral Results (2026-09-30)](REQUIREMENT_RESOLUTION_V0_BEHAVIORAL_RESULTS_2026-09-30.md)
- [V1.10: speech scope, source selection and dialogue reasoning](SCOPE_SOURCE_V1_10_IMPLEMENTATION.md)
- [Cognitive Shadow V0 End-to-End Evaluation](SHADOW_PIPELINE_END_TO_END_EVALUATION_2026-09-29.md)
- [Cognitive Shadow V0 End-to-End Reevaluation](SHADOW_PIPELINE_END_TO_END_REEVALUATION_2026-09-29.md)
- [Shadow Pipeline Evaluation Findings](SHADOW_PIPELINE_EVALUATION_FINDINGS_2026-09-28.md)
- [Two-Call Cognitive Live Shadow Fix Retest](TWO_CALL_LIVE_SHADOW_FIX_RETEST_2026-10-03.md)
- [Two-Call Cognitive Live Shadow Latency Evaluation](TWO_CALL_LIVE_SHADOW_LATENCY_EVALUATION_2026-10-03.md)
- [Two-call orchestration implementation report](TWO_CALL_ORCHESTRATION_IMPLEMENTATION_REPORT.md)
- [Compact two-call reliability fix (decision-context-v0.2)](TWO_CALL_V0_2_RELIABILITY_FIX_2026-10-05.md)

## Related evaluation evidence

[docs/qa](../../../../../../docs/qa/) retains dated traces, reports and scripts.
Read each report's scope, protocol and limitations before interpreting a result.
The [October 13 premise report](../../../../../../docs/qa/cognitive-premise-fix-2026-10-13/REPORT.md)
supersedes earlier statements that the reserved cases had never run. The
[October 18 conditioning directory](../../../../../../docs/qa/cognitive-conditioning-2026-10-18/)
belongs to the ongoing mapping/evaluation work.
