# Phase 1 GPT-6 Luna Live Shadow Latency Retest

Date: 2026-10-02  
Run ID: `21c0a000b3c146e4b62516dde0d810e6`  
Sample: 32 authenticated application-path turns, including one uninterrupted 10-turn conversation  
Comparison baseline: `gpt-4o-mini` Phase 1 run `86da20b6ef9a43a98888823576614c2c`

## Executive result

Changing the Cognitive model-backed stages to `gpt-6-luna` with
`reasoning_effort=none` did **not** make the current chain faster or produce a
usable shadow response.

- The visible live assistant completed 32/32 turns. It remained a separate
  `gpt-4.1` path and was not changed to Luna.
- All 32 Cognitive Shadow traces failed.
- Zero turns produced an accepted candidate.
- Zero turns reached Realization, even though the configured Realization model
  was Luna.
- Median shadow processing after the terminal worker attempt began was
  **14.499 s**, versus **13.105 s** in the earlier `gpt-4o-mini` run.
- Median Appraisal provider latency was **12.662 s**, versus **11.947 s** with
  `gpt-4o-mini`.
- Appraisal p95 was **19.769 s**, versus **18.043 s** in the baseline.
- The uninterrupted conversation accumulated **183.8 s** of queue/retry delay
  by turn 10, versus **120.0 s** in the baseline.
- Thirty turns ended in deterministic Appraisal proposal rejection, one in an
  Appraisal timeout after three worker attempts, and one in an Interpretation
  vocabulary rejection.

This is not evidence that Luna is universally slower than `gpt-4o-mini`. It is
evidence that Luna, with the current payload, schema, prompt, adapter, and
single-worker execution model, did not improve this Cognitive pipeline.

## Isolation and application path

The retest used the same corpus driver and authenticated WebSocket path as the
baseline:

```text
authenticated synthetic frontend-protocol client
    -> /api/chat/ws
    -> durable live chat worker
    -> visible live assistant response
    -> immutable shadow capture
    -> separate Cognitive Shadow execution
    -> durable PostgreSQL trace
    -> trace_summary
```

The client did not call a Cognitive runner directly. Shadow isolation remained
enabled throughout:

- `COGNITIVE_LIVE_SHADOW_ENABLED=true`;
- sample rate `1.0` for this bounded local evaluation;
- shadow candidates had no live authority;
- no shadow candidate was sent to the user; and
- shadow failures did not delay the visible assistant.

## Configuration verified before the run

The focused adapter and contract suite passed: **176 tests**.

The three Cognitive model adapters were configured with:

- model: `gpt-6-luna`;
- reasoning effort: `none`;
- strict structured output: enabled;
- temperature: `0`;
- streaming: disabled;
- provider-side storage: disabled; and
- SDK retries: `0`.

Model-backed Appraisal and Realization were enabled in the local shadow
configuration. Collaborative Content remained disabled.

## Model-backed stages that actually ran

| Stage | Configured model | Retained terminal-path calls | Outcome |
| --- | --- | ---: | --- |
| Visible assistant | `gpt-4.1` | 32 | 32 visible `done` responses; outside the shadow chain |
| Interpretation | `gpt-6-luna`, reasoning `none` | 32 | 31 advanced to Appraisal; one deterministic vocabulary rejection |
| Appraisal | `gpt-6-luna`, reasoning `none` | 31 | 30 completed provider outputs rejected by the compiler; one terminal timeout |
| Collaborative Content | disabled | 0 | Did not run |
| Realization | `gpt-6-luna`, reasoning `none` | 0 | `not_requested` on all 32 turns |

The terminal traces expose **63 retained-path model calls**. Four earlier
worker attempts are not retained in detail, so the exact provider-call total
across all attempts is not reconstructable. The bounded total is 63–71 if both
eligible model stages ran in every unretained attempt.

## Aggregate latency

All figures are generated from `trace_summary` plus durable capture, job, and
trace timestamps. Percentiles use nearest rank.

| Measure | N | Luna median | Luna p95 | Luna maximum | Baseline median | Baseline p95 | Baseline maximum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Visible first token | 32 | 0.635 s | 0.931 s | 1.940 s | 0.688 s | 1.123 s | 2.527 s |
| Visible completion | 32 | 2.212 s | 4.236 s | 5.958 s | 2.257 s | 4.675 s | 5.299 s |
| Shadow queue/retry wait | 32 | 265.067 s | 449.976 s | 461.478 s | 251.386 s | 398.169 s | 402.553 s |
| Shadow processing after terminal-attempt start | 32 | 14.499 s | 21.523 s | 22.644 s | 13.105 s | 21.550 s | 24.301 s |
| Capture to terminal failure | 32 | 278.765 s | 463.863 s | 471.745 s | 267.653 s | 405.774 s | 411.217 s |
| Fully validated shadow response ready | 0 | — | — | — | — | — | — |
| Exact Appraisal provider span | 31 | 12.662 s | 19.769 s | 20.024 s | 11.947 s | 18.043 s | 19.851 s |
| Exact Realization provider span | 0 | — | — | — | — | — | — |

The live assistant timing is included only to show what the user experienced.
Its small run-to-run change cannot be attributed to Luna because the visible
assistant remained `gpt-4.1`.

Relative to the baseline, Luna's median Appraisal call was approximately 6.0%
slower, and its p95 was approximately 9.6% slower. Median total shadow
processing increased approximately 10.6%. These are measurements of two small
live samples, not an API-wide model benchmark.

## Token observations

Thirty completed Luna Appraisal calls exposed usage data:

| Usage | Luna | Earlier `gpt-4o-mini` baseline |
| --- | ---: | ---: |
| Median input tokens | 4,500.5 | 4,310 |
| Input-token range | 3,076–6,353 | 3,111–10,734 |
| Median output tokens | 1,720 | 706 |
| Output-token range | 785–2,568 | 662–2,010 |

Luna generated substantially more structured output tokens under the same
Appraisal contract. That correlates with the lack of latency improvement, but
this run does not prove that output length is the only cause.

## Calls, retries, and failures

### Retry accounting

- SDK retries: **0** by configuration.
- Realization validation retries: **0**, because Realization never ran.
- Worker retries: **4** across three turns.
- `continuous-02-proposal`: two worker attempts, one retry.
- `continuous-06-follow-up`: three worker attempts, two retries, terminal
  Appraisal timeout.
- `substantial-03-explain`: two worker attempts, one retry.

Earlier worker-attempt spans are not retained in the terminal trace. Their
durations and exact model-call counts are therefore unavailable rather than
estimated as exact values.

### Terminal failures

| Failure | Count | Meaning |
| --- | ---: | --- |
| `pipeline.cognitive_turn.appraisal.model_appraisal.proposal_rejected` | 30 | Luna returned structured output; deterministic Appraisal compilation rejected it |
| `pipeline.cognitive_turn.appraisal.model_appraisal.timeout` | 1 | The retained Appraisal call reached the 20-second timeout |
| `pipeline.interpretation.semantic_grounding.semantic_parser.argument_value_outside_vocabulary` | 1 | Luna Interpretation returned an argument value outside the closed vocabulary |

Fast failure is not counted as success. The accepted-candidate rate remained
**0/32**.

## Uninterrupted 10-turn conversation

| Turn | Visible completion | Queue/retry wait | Shadow processing | Appraisal provider | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 greeting | 2.230 s | 0.041 s | 22.644 s | 19.769 s | proposal rejected |
| 2 proposal | 1.808 s | 42.870 s | 9.636 s | 7.854 s | proposal rejected after one worker retry |
| 3 selection | 2.328 s | 50.748 s | 17.624 s | 14.911 s | proposal rejected |
| 4 explanation | 1.804 s | 66.095 s | 8.384 s | 6.653 s | proposal rejected |
| 5 correction | 2.928 s | 72.764 s | 14.993 s | 12.886 s | proposal rejected |
| 6 follow-up | 2.353 s | 128.069 s | 21.523 s | 20.024 s | timeout after two worker retries |
| 7 description | 1.449 s | 147.314 s | 15.890 s | 14.211 s | proposal rejected |
| 8 substantial | 3.700 s | 161.812 s | 14.217 s | 12.342 s | proposal rejected |
| 9 summary | 2.243 s | 172.394 s | 13.542 s | 11.505 s | proposal rejected |
| 10 reflection | 2.329 s | 183.778 s | 16.312 s | 12.980 s | proposal rejected |

The terminal-attempt queue measure includes time consumed by earlier worker
attempts when a job retries. Even with that caveat, the conversational capacity
result is unambiguous: the live conversation produced turns much faster than
the single shadow worker could terminate them.

## Slow-turn examples

### Continuous greeting

- Visible assistant completed in **2.230 s**.
- Shadow processing took **22.644 s**, the run maximum.
- Appraisal alone took **19.769 s**.
- The provider returned 2,527 output tokens.
- Deterministic Appraisal compilation rejected the proposal.
- No Realization call occurred.

### Continuous follow-up with timeout

- Visible assistant completed in **2.353 s**.
- Terminal-attempt queue/retry wait was **128.069 s**.
- The retained Appraisal attempt took **20.024 s** and timed out.
- The job used three worker attempts.
- No accepted candidate existed.

### Greeting 2

- Visible assistant completed in **1.531 s**.
- Shadow processing took **19.302 s**.
- Appraisal took **17.346 s**.
- The completed provider output was rejected.

### Substantial options discussion

- Visible assistant completed in **3.296 s**.
- Shadow processing took **18.026 s**.
- Appraisal took **15.504 s**.
- The completed provider output was rejected.

### Correction follow-up

- Visible assistant completed in **2.520 s**.
- Queue wait was **412.511 s**.
- Shadow processing took **17.015 s**.
- Appraisal took **14.906 s**.
- The completed provider output was rejected.

## Per-turn result table

Every row has `realization_status=not_requested` and no accepted candidate.

| Turn | Live done | Queue/retry wait | Shadow processing | Appraisal | Calls retained | Worker retries | Terminal result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| continuous-01-greeting | 2.230 s | 0.041 s | 22.644 s | 19.769 s | 2 | 0 | proposal rejected |
| continuous-02-proposal | 1.808 s | 42.870 s | 9.636 s | 7.854 s | 2 | 1 | proposal rejected |
| continuous-03-selection | 2.328 s | 50.748 s | 17.624 s | 14.911 s | 2 | 0 | proposal rejected |
| continuous-04-explanation | 1.804 s | 66.095 s | 8.384 s | 6.653 s | 2 | 0 | proposal rejected |
| continuous-05-correction | 2.928 s | 72.764 s | 14.993 s | 12.886 s | 2 | 0 | proposal rejected |
| continuous-06-follow-up | 2.353 s | 128.069 s | 21.523 s | 20.024 s | 2 | 2 | Appraisal timeout |
| continuous-07-description | 1.449 s | 147.314 s | 15.890 s | 14.211 s | 2 | 0 | proposal rejected |
| continuous-08-substantial | 3.700 s | 161.812 s | 14.217 s | 12.342 s | 2 | 0 | proposal rejected |
| continuous-09-summary | 2.243 s | 172.394 s | 13.542 s | 11.505 s | 2 | 0 | proposal rejected |
| continuous-10-reflection | 2.329 s | 183.778 s | 16.312 s | 12.980 s | 2 | 0 | proposal rejected |
| greeting-01 | 0.923 s | 197.793 s | 14.785 s | 13.321 s | 2 | 0 | proposal rejected |
| greeting-02 | 1.531 s | 211.748 s | 19.302 s | 17.346 s | 2 | 0 | proposal rejected |
| greeting-03 | 1.007 s | 229.566 s | 9.250 s | 7.458 s | 2 | 0 | proposal rejected |
| greeting-04 | 0.770 s | 237.896 s | 7.482 s | 6.061 s | 2 | 0 | proposal rejected |
| simple-01-identity | 0.658 s | 244.710 s | 14.380 s | 12.182 s | 2 | 0 | proposal rejected |
| simple-02-date | 0.674 s | 258.516 s | 13.730 s | 12.207 s | 2 | 0 | proposal rejected |
| simple-03-protein | 0.881 s | 271.618 s | 13.665 s | 11.946 s | 2 | 0 | proposal rejected |
| simple-04-fiber | 1.802 s | 284.463 s | 14.617 s | 12.911 s | 2 | 0 | proposal rejected |
| simple-05-macros | 2.195 s | 297.342 s | 15.057 s | 13.461 s | 2 | 0 | proposal rejected |
| simple-06-balanced | 2.493 s | 310.272 s | 14.883 s | 12.662 s | 2 | 0 | proposal rejected |
| substantial-01-compare | 4.236 s | 322.711 s | 14.013 s | 12.309 s | 2 | 0 | proposal rejected |
| substantial-02-options | 3.296 s | 332.551 s | 18.026 s | 15.504 s | 2 | 0 | proposal rejected |
| substantial-03-explain | 3.789 s | 369.677 s | 16.285 s | 14.414 s | 2 | 1 | proposal rejected |
| substantial-04-plan | 5.958 s | 382.251 s | 2.732 s | — | 1 | 0 | Interpretation vocabulary rejection |
| substantial-05-evaluate | 3.688 s | 379.070 s | 14.231 s | 12.541 s | 2 | 0 | proposal rejected |
| substantial-06-summarize | 1.540 s | 389.684 s | 16.360 s | 14.052 s | 2 | 0 | proposal rejected |
| correction-01-setup | 2.280 s | 404.582 s | 10.194 s | 7.807 s | 2 | 0 | proposal rejected |
| correction-01-change | 2.520 s | 412.511 s | 17.015 s | 14.906 s | 2 | 0 | proposal rejected |
| followup-01-setup | 0.938 s | 427.097 s | 15.873 s | 13.982 s | 2 | 0 | proposal rejected |
| followup-01-expand | 1.354 s | 442.089 s | 9.184 s | 7.719 s | 2 | 0 | proposal rejected |
| followup-02-setup | 2.445 s | 449.976 s | 13.888 s | 12.487 s | 2 | 0 | proposal rejected |
| followup-02-clarify | 1.641 s | 461.478 s | 10.267 s | 8.754 s | 2 | 0 | proposal rejected |

## Interpretation

The model replacement did not address the primary blocker. Luna successfully
returned strict structured Appraisal JSON on 30 retained calls, but the
deterministic semantic compiler rejected every one. This confirms that the
problem is not merely provider transport or insufficient output length.

The run also shows that `reasoning_effort=none` is not sufficient by itself to
make the present Appraisal protocol conversationally fast. The payload remains
large, the required structured output remains verbose, and calls remain
sequential.

## Telemetry limitations

- `trace_summary` latency coverage is still `appraisal_only`.
- Failed traces do not retain exact Interpretation provider spans.
- Earlier worker-attempt provider spans are not retained.
- No Realization timing exists because no turn reached Realization.
- Queue time for retried jobs includes time consumed before the terminal
  attempt represented by the trace.
- This is a bounded local live-provider comparison, not a production SLA or a
  general benchmark of either model.

## Reproduction artifacts

- Corpus driver:
  [`backend/scripts/cognitive_shadow_latency_phase1_live.py`](../../../../../scripts/cognitive_shadow_latency_phase1_live.py)
- Trace-summary analyzer:
  [`backend/scripts/cognitive_shadow_latency_phase1_analyze.py`](../../../../../scripts/cognitive_shadow_latency_phase1_analyze.py)
- Private live-turn artifact:
  `/private/tmp/cognitive-phase1-latency-live-turns.json`
- Private analysis artifact:
  `/private/tmp/cognitive-phase1-latency-analysis.json`
- Summary schema: `cognitive-shadow-trace-summary-v0.6`
- Latency schema: `cognitive-latency-summary-v0.1`

The private artifacts contain local identifiers and are not intended for
commit. Shadow payloads and generated candidate content are not included in
this report.
