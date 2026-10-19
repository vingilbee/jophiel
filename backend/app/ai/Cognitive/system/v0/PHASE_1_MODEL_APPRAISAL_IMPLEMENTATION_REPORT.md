# Phase 1 — model-backed appraisal implementation report

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Date: 2026-10-02 (America/Vancouver).

**Decision: retain the deterministic default.** Phase 1 is implemented as an opt-in shadow experiment. The behavioral acceptance gate is pending; no live authority or Phase 2 rollout is granted. The separate Phase 0 whole-pipeline/provider baseline has not been completed by this change. Appraisal-only instrumentation is available now rather than deferring measurement of the new provider.

## Changed components and versions

Paths below are relative to `backend/`.

| Component | Change / version |
| --- | --- |
| `app/ai/Cognitive/system/v0/appraisal/model_contracts.py` | Immutable provider-neutral proposal, evidence basis, context, trace, and typed errors; `appraisal-model-protocol-v0.1`, `appraisal-model-compiler-v0.1`, `cognitive-model-context-appraisal-v0.1`. |
| `app/ai/Cognitive/system/v0/appraisal/context.py` | Appraisal-specific personality/state/relationship projection and bounded attributed dialogue window. Does not expand the existing Action mapper's authority. |
| `app/ai/Cognitive/system/v0/appraisal/prompt_v0_1.txt` | Source-controlled Phase 1 prompt, `appraisal-prompt-v0.1`; exact dimension definitions/scales are appended programmatically. |
| `app/ai/Cognitive/system/v0/appraisal/model_engine.py` | Payload compiler, proposal compiler, stage spans, immutable accepted model trace, and frozen-output model. |
| `app/ai/Cognitive/system/v0/appraisal_provider.py` | `openai-appraisal-v0.1`, strict `appraisal-proposal-schema-v0.1`; explicit configured model, capability-aware temperature, timeout, no streaming/storage/tools or SDK retry. |
| `app/ai/Cognitive/system/v0/appraisal/engine.py` | Reuses frame identity/revision/subject/aggregate machinery with supplied compiled dimensions, without executing baseline appraisal rules. Extends authorized evidence validation for accepted context references. |
| `app/ai/Cognitive/system/v0/appraisal/models.py` | Optional typed model trace, omitted from deterministic serialization to preserve legacy payloads. Existing eight-dimension appraisal schema remains in use. |
| `app/ai/Cognitive/system/v0/cognitive_turn/models.py`, `runner.py` | Optional validated pre-appraisal context and diagnostic spans; invoke selected engine before the unchanged deterministic state updater and Action stage. Absent additions are omitted from legacy serialization. |
| `app/ai/Cognitive/system/v0/cognitive_pipeline_v0/runner.py` | Supplies authorized bounded dialogue to model appraisal while preserving exact Interpretation object identity. |
| `app/ai/Cognitive/system/v0/latency.py` | Content-free monotonic telemetry, `cognitive-latency-span-v0.1`. |
| `app/ai/Cognitive/system/v0/live_shadow/models.py`, `service.py` | Persist typed model errors and failed spans in existing JSON traces; replay recorded appraisal proposals instead of calling the provider. Existing collaboration replay changes were preserved. |
| `app/ai/Cognitive/system/v0/trace_summary.py` | Adds `cognitive-latency-summary-v0.1`, marks appraisal implementation/replay semantics, preserves current workspace/collaboration sections; summary `cognitive-shadow-trace-summary-v0.6`. |
| `app/core/config.py`, `.env.example`, `app/services/cognitive_shadow.py` | Independent shadow selector and explicit model/timeout. Configuration errors fail with typed codes; no deterministic or alternate-model fallback. |
| `scripts/appraisal_comparison.py` | Paired baseline/trial runner on identical canonical turn snapshots, `appraisal-comparison-v0.1`; live and frozen modes, optional local frozen replay material. |
| `tests/test_model_appraisal.py` | Hermetic compiler, context, adapter, state-spine, failure, selector, latency, and replay checks. |
| `app/ai/Cognitive/system/v0/PHASE_1_APPRAISAL_COMPARISON.json` | Measured eight-case synthetic/frozen comparison. Contains no messages, prompts or trait values. |

The original phased handoff is preserved. Existing concurrent collaboration/workspace edits were retained. No database migration is necessary: new trace details use existing JSON payload fields.

## Prompt and projection

The privileged prompt uses the handoff's Phase 1 template: appraise the focus event, distinguish correction from hostility, distinguish failed goals from rejection, keep uncertainty explicit, and never draft a response or mutate state. Dynamic data is a separate structured user message. Quoted and historical instructions remain evidence/context. Concise evidence summaries are bounded to 240 characters; hidden chain-of-thought is neither requested nor stored.

The model receives retained scenario/active-goal pairs, their semantic events and evidence, scoped unknowns/contradictions, explicit runtime constraints, value records, and supplemental outcomes. Each pair must appear exactly once with all eight dimensions. The compiler rejects invalid scales, foreign/cross-scope evidence, extra mutation fields, unresolved pair references, duplicate dimensions, known assessments lacking current event and goal citations, and highly uncertain known assessments. It distinguishes observed/inferred assessments from insufficient evidence. Unknown is null with missing-input provenance, never a substituted neutral value.

Context mapping:

- Identity: the supplied CoreMemory lineage reference; no invented identity content.
- Personality: the existing mapped curiosity, warmth, playfulness, and directness values, preserving their canonical unit scale and snapshot references. This is a separately versioned appraisal projection; the existing Action score boundary stays unchanged.
- Assertiveness: unavailable, with no value or invented scale. No absent source is filled with an observed-looking constant.
- Explicit policy constants: supported as a distinct source kind and tested; they are not represented as observed traits. Neutral state controls are not projected as observed personality.
- State: only active values from the exact previous snapshot, with existing signed/unsigned 100-point score scales, scope, status, and band. Timing is always `pre_appraisal`.
- Relationship: the supplied authorized appraisal relationship snapshot; absence is explicit. No reciprocal attachment, diagnosis or consent is inferred.
- Dialogue: current focus text plus up to eight recent authorized messages; oversized historical messages are omitted with reference diagnostics. Every item has speaker, reference and current-turn attribution. Historical messages have context-only authority.
- Workspace: Phase 1 does not introduce a new workspace projection; later phases own that extension.

Requests are bounded to eight scenario/goal pairs and 48,000 serialized characters. The adapter allows one attempt with a configured timeout and 4,000 output tokens. This bounds the new appraisal provider call, not the entire multi-provider turn; a global turn deadline remains part of the outstanding cross-stage work.

## State and replay boundaries

Compiled frames carry model-compiler provenance rather than claiming the old deterministic rules ran. Accepted appraisal evidence goes to the existing state updater; bounds, decay, contribution application, persistence, Action gates and outcome authority stay deterministic. Tests verify state supersession, frame evidence, numeric bounds, and exact downstream replay.

Model traces record prompt version/hash, protocol, compiler, context version, provider/model and behavior-affecting request settings, plus the immutable payload and proposal. These full internal traces contain sensitive evidence and should be treated like existing raw shadow traces. The summary does not reveal their content.

Replay is explicitly `frozen_output_only`: it reuses the captured proposal, checks the payload digest and installed prompt/compiler versions, and recompiles the downstream spine. It does not claim the provider would reproduce the same output. Frozen replay provider spans are marked skipped and do not count as actual model calls. Legacy timing fields and semantic digest exclusions remain intact.

## Validation

Final relevant regression command, run from `backend/`:

```sh
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --no-sync python -m pytest -q \
  tests/test_model_appraisal.py tests/test_appraisal*.py tests/test_dynamic_state*.py \
  tests/test_cognitive_turn*.py tests/test_cognitive_pipeline_v0*.py \
  tests/test_cognitive_shadow*.py tests/test_personality_runtime*.py \
  tests/test_semantic_model_grounding.py tests/test_semantic_parser_hardening.py \
  tests/test_realization_provider_capabilities.py \
  -k 'not test_local_fallback_is_unchanged_by_capture_success_or_failure'
```

Result: **462 passed, 56 skipped, 2 deselected**, with existing dependency deprecation warnings. `git diff --check` passed.

The full run before excluding that parametrized WebSocket test had **461 passed, 56 skipped, 1 failed**. Its cancellation variant raises `WebSocketDisconnect` while the chat history/active-job mocks return non-iterable `Mock` objects. This is outside the appraisal path and was not repaired here. Both variants of that test were deselected in the final run; the successful variant had passed in the full run. Subsequent checks added durable frozen replay and malformed timing coverage.

PostgreSQL persistence/worker/migration checks were collected but skipped because `TEST_DATABASE_URL` was not configured. Their durable behavior remains unverified against PostgreSQL. No developer database was altered.

Tests include paired input projections (one trait changed, relationship history changed, strain changed), facts/intent/permission invariance, missing sources, explicit constants, prompt separation, malformed proposals, typed failures, cancellation timing, legacy traces, token-null handling, skipped replay calls, serializer round trips, and provider capability handling. These tests establish contracts and input sensitivity; they do **not** establish the appropriateness of live model output differences.

## Paired findings and latency

See `PHASE_1_APPRAISAL_COMPARISON.json` for exact measurements. Inputs are eight **synthetic contract fixtures**, with scripted frozen proposals: correction, gratitude, disagreement, urgency, uncertainty, harmless refinement, failed operation, and topic change. They intentionally exercise events outside the deterministic registry.

All eight compile successfully with authorized evidence. Baseline: 0 known dimensions per fixture. Scripted proposal: 6 known and 2 unknown dimensions per fixture. Novelty and norm compatibility retain unknown status. This demonstrates the new path can represent wider coverage while retaining uncertainty; it is **not evidence of better contextual judgment**, since a test double supplied the judgments.

Measured appraisal-only times (8 samples each):

| Mode | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| Deterministic compiler/engine | 0.169 ms | 0.185 ms | 0.185 ms |
| Frozen proposal replay/compiler | 0.777 ms | 1.002 ms | 1.002 ms |
| Live provider | unavailable | unavailable | unavailable |

These small local measurements exclude network inference, queues, Realization, delivery and the rest of the pipeline. They are neither a live latency estimate nor an SLA. Live sample size: **0**; actual live appraisal calls: **0**; SDK retries: **0**. No appraisal model was configured in the settings inspected. Cache-warm/cold effects and worker retries were not measured.

`trace_summary.py` exposes measured appraisal payload/provider/response spans, failure status, usage when reported, actual call counts and provider work separately from pipeline wall time. Older traces report unavailable counts and null values. Queue wait, response readiness, delivery and retry wait are null when unmeasured. Coverage is explicitly `appraisal_only`; it must not be interpreted as full Phase 0 instrumentation. Summaries validate telemetry vocabulary/numeric fields so arbitrary prompt, message, identifier or trait text cannot escape through latency fields.

## Configuration and comparison runner

Baseline (default):

```dotenv
COGNITIVE_SHADOW_APPRAISAL_IMPLEMENTATION=deterministic
```

Opt-in shadow trial after arranging evaluation:

```dotenv
COGNITIVE_SHADOW_APPRAISAL_IMPLEMENTATION=model
COGNITIVE_SHADOW_APPRAISAL_MODEL=<explicit-supported-model-id>
COGNITIVE_SHADOW_APPRAISAL_TIMEOUT_SECONDS=20
```

The existing OpenAI API-key setting supplies credentials. Missing credentials/model or unsupported model settings produce a typed failure, never a silent baseline/alternate model. The shared repository capability manifest is reused; it is not expanded speculatively.

`appraisal_comparison.py` accepts JSONL objects with a canonical `CognitiveTurnRequest` under `request`, and (for frozen mode) an `AppraisalModelTrace` under `model_trace`:

```sh
uv run --no-sync python scripts/appraisal_comparison.py \
  --input captured-turns.jsonl --output comparison.json --live \
  --model <explicit-supported-model-id> --frozen-output local-replay.jsonl

uv run --no-sync python scripts/appraisal_comparison.py \
  --input local-replay.jsonl --output replay-comparison.json --frozen
```

The optional frozen-output file contains sensitive internal snapshots and is for local replay; the comparison report excludes that evidence. Counts reflect actual attempts, including failed calls, rather than assuming one call per input. Appropriateness remains a separate pending human review.

## Remaining acceptance gates

1. Complete the Phase 0 whole-call-graph baseline/instrumentation and representative live conversation sample; existing stage timings are not a substitute for that requirement.
2. Configure an explicit appraisal model and capture paired live baseline/trial runs; collect at least the handoff's 30 representative live turns for exploratory latency reporting.
3. Independently review evidence quality, unsupported confidence, correction/disagreement handling, paired personality/relationship/strain differences, and invariance of facts and permissions. Repeat representative stochastic cases.
4. Run PostgreSQL persistence and worker tests with a disposable test database.
5. Resolve the reported WebSocket test failure in its owning workstream.

Keep the deterministic selector until those results support a proceed decision. Later model migration phases remain outside this implementation.

API schema requirements were checked against [official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs); all object properties are required and additional properties are forbidden. Repository measurements and independent review, rather than that API guidance, determine phase acceptance.

## Requested model configuration update

Following the user's request, Interpretation's semantic parser, Appraisal, and Realization now default to `gpt-6-luna`. Their adapters explicitly send `reasoning_effort="none"`, record that setting in provenance, and use capability manifest `realization-openai-capabilities-v0.2`. The local `backend/.env` selects that model for all three stages and enables model-backed appraisal in shadow mode. The general appraisal selector default remains deterministic and Realization remains disabled by default for fresh installations. Existing local Realization enablement was preserved.

Migration validation: **366 passed, 23 skipped** across adapter, appraisal, semantic-parser, Realization and replay tests. The effective local model/effort configuration was verified without emitting credentials or making provider calls. Backend/shadow-worker processes need to reload this configuration. The original frozen comparison remains historical; this configuration change does not complete the live behavioral/latency acceptance gate.

Zero reasoning is supported as `none` according to [official GPT-6 Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).
