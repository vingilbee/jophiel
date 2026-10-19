# Phase 1 Live Cognitive Shadow Latency Evaluation

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Date: 2026-10-02  
Status: exploratory local live-provider evaluation; not an SLA, rollout approval, or live-response authorization  
Run ID: `86da20b6ef9a43a98888823576614c2c`  
Sample: 32 authenticated live chat turns, including one uninterrupted 10-turn conversation

## Executive result

The current Phase 1 sequential model chain is **not fast enough or reliable
enough to act as the conversational response path** in the tested
configuration.

- The visible live assistant completed 32/32 turns.
- Median visible first-token latency was **688 ms**; p95 was **1.123 s**; the
  maximum was **2.527 s**.
- Median visible live completion latency was **2.257 s**; p95 was **4.675 s**;
  the maximum was **5.299 s**.
- The isolated shadow worker reached a terminal decision on 32/32 turns, but
  **all 32 terminal traces failed**.
- Median shadow processing time after a worker started the turn was
  **13.105 s**; p95 was **21.550 s**; the maximum was **24.301 s**.
- The model-backed Appraisal call alone had a median of **11.947 s**, p95 of
  **18.043 s**, and maximum of **19.851 s** across 29 recorded calls.
- **0/32 turns produced an accepted shadow candidate.** Consequently, there is
  no measured “fully validated shadow response ready” latency. A fast terminal
  failure is not counted as response readiness.
- Realization was `not_requested` on all 32 turns because the pipeline failed
  earlier. The configured Realization model made zero calls in the retained
  terminal attempts.
- 29 turns failed because the model Appraisal proposal was rejected by the
  deterministic compiler. Three failed during model-backed Interpretation
  because a semantic argument value was outside the registered vocabulary.
- The uninterrupted conversation overloaded the single sequential shadow
  worker. Queue wait grew from **34 ms** on turn 1 to **120.0 s** on turn 10.
- Across the whole burst, median queue wait was **251.4 s**, p95 was
  **398.2 s**, and maximum was **402.6 s**. These queue figures measure this
  deliberate 32-turn workload, not isolated provider latency.

The live assistant remained responsive because shadow isolation was preserved.
The live assistant's successful responses must not be attributed to the
Cognitive Shadow.

## Evaluation question

The evaluation asked whether the sequential LLM chain is fast enough for a
natural conversation when exercised through the actual application shadow
path, and whether the latency results in useful accepted shadow output.

The behavioral condition matters. A chain that terminates quickly but produces
no validated response is not successful. Latency and response usefulness are
therefore reported together throughout this document.

## Application path

Each turn used the authenticated WebSocket protocol used by the frontend:

```text
synthetic authenticated client
    -> /api/chat/ws
    -> durable live chat worker
    -> visible live assistant response
    -> immutable shadow capture
    -> separate Cognitive Shadow worker
    -> PostgreSQL terminal trace
    -> trace_summary
```

The client did not invoke the cognitive runner directly. Authentication,
conversation creation, message persistence, live generation, shadow sampling,
queueing, worker processing, durable tracing, and summary projection were all
active.

The local shadow settings retained:

- `COGNITIVE_LIVE_SHADOW_ENABLED=true`;
- sampling rate `1.0` for this deliberate local test;
- separate shadow worker execution;
- `live_authority=never_granted_by_shadow`; and
- no delivery of shadow candidates to the user.

The disposable QA PostgreSQL and Redis services were used. The repository
migrations were at head before the run.

## Corpus

The 32 turns covered:

| Family | Turns | Coverage |
| --- | ---: | --- |
| Continuous conversation | 10 | Greeting, proposal, selection, explanation, correction, follow-up, description, coordinated preparation, summary, reflection |
| Additional greetings/social turns | 4 | Greeting, conversational opening, meeting, gratitude |
| Simple questions | 6 | Identity, date, protein, fiber, macronutrients, balanced vegetarian meals |
| Substantial conversational tasks | 6 | Comparison, options with tradeoffs, explanation, meal outline, evaluation, summary |
| Corrections and follow-ups | 6 | One setup/correction pair and two setup/follow-up pairs |

The uninterrupted 10-turn conversation remained in one conversation lineage
and did not pause to wait for the shadow worker between visible replies. This
is intentional: it tests whether shadow throughput can keep pace with ordinary
back-and-forth conversation.

## Model-backed stages that actually ran

### Visible live assistant

The live assistant used the application's configured `gpt-4.1` chat path. It
is outside the Cognitive Shadow chain. Its timings describe what the user saw,
not when the shadow became ready.

### Shadow Interpretation

- Implementation: model-backed semantic parser.
- Model: `gpt-4o-mini`.
- Configured SDK retries: 0.
- Calls in retained terminal paths: 32.
- Outcome: 29 calls advanced to Appraisal; 3 produced
  `semantic_parser.argument_value_outside_vocabulary` and terminated the turn.

The current failed-trace contract does not persist a dedicated Interpretation
provider span or completed pipeline stage timings. Because every turn failed,
`trace_summary` reports no `interpretation_ms` values for this run. The exact
duration of each Interpretation provider call is therefore unavailable.

The report does not substitute total turn time and call it Interpretation
latency. For Appraisal-reaching turns, the difference between shadow processing
time and the Appraisal provider span includes Interpretation plus deterministic
compilation and stage overhead. That remainder had median **1.597 s**, p95
**4.855 s**, and maximum **6.258 s**, but it is not a pure provider-call
measurement.

### Shadow Phase 1 Appraisal

- Implementation: model-backed Appraisal experiment.
- Model: `gpt-4o-mini`.
- Maximum attempts per pipeline attempt: 1.
- Configured SDK retries: 0.
- Recorded terminal-attempt provider calls: 29.
- Provider call status: all 29 calls completed at the provider boundary.
- Compiler outcome: all 29 returned proposals were rejected.
- Median input size: 4,310 tokens; range 3,111–10,734.
- Median output size: 706 tokens; range 662–2,010.

`trace_summary` records exact content-free Appraisal spans for payload compile,
provider attempt, and response compile. The aggregate latency below uses only
the `provider_attempt` spans.

### Shadow Collaborative Content

Collaborative Content was explicitly disabled for this Phase 1 run. It made
zero provider calls. Later phases must be evaluated separately rather than
extrapolated from this result.

### Shadow Realization

- Configured model: `gpt-4o-mini`.
- Configured maximum attempts: 2.
- Actual status: `not_requested` on 32/32 turns.
- Provider calls: 0.
- Application-level retry attempts: 0.
- Accepted candidates: 0.

The pipeline never reached Realization because Interpretation or Appraisal
failed first. This run therefore does not measure the full intended
Interpretation → Appraisal → Realization critical path. It measures the current
chain as it actually behaved: it stopped before the final model stage.

## Measurement definitions

| Measure | Definition |
| --- | --- |
| Live first token | WebSocket send until the first visible live assistant token |
| Live completion | WebSocket send until the live `done` frame |
| Shadow queue wait | Durable capture time until the shadow terminal attempt's `started_at` |
| Shadow processing | Terminal attempt `started_at` until `completed_at` |
| Capture to terminal | Durable capture time until terminal trace completion; queue plus processing |
| Validated shadow ready | Capture until terminal completion only when `realization_status=accepted_shadow` |
| Appraisal provider call | Exact monotonic `appraisal.provider_attempt` span from `trace_summary` |
| Interpretation provider call | Not separately persisted for these failed traces |
| Realization provider call | Exact per-attempt latency when present; none ran in this evaluation |

Percentiles use the nearest-rank method. This is a small exploratory local
sample, not a production SLA.

## Aggregate latency

| Measure | N | Median | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Visible live first token | 32 | 0.688 s | 1.123 s | 2.527 s |
| Visible live completion | 32 | 2.257 s | 4.675 s | 5.299 s |
| Shadow queue wait | 32 | 251.386 s | 398.169 s | 402.553 s |
| Shadow processing after start | 32 | 13.105 s | 21.550 s | 24.301 s |
| Shadow capture to terminal failure | 32 | 267.653 s | 405.774 s | 411.217 s |
| Fully validated shadow response ready | 0 | — | — | — |
| Exact Appraisal provider call | 29 | 11.947 s | 18.043 s | 19.851 s |
| Exact Realization provider call | 0 | — | — | — |
| Interpretation + deterministic remainder on Appraisal-reaching turns | 29 | 1.597 s | 4.855 s | 6.258 s |

### Interpretation of the aggregate

Even without queueing, median shadow processing was almost six times the
median visible live completion time. P95 shadow processing exceeded 21 seconds
without reaching Realization or producing a candidate.

The Appraisal provider call dominated the measured processing path. At the
median it consumed approximately 91% of the 13.105-second shadow processing
time. This ratio is descriptive only because the median of one distribution
need not correspond to the same turn as the median of another.

The end-to-end capture-to-terminal figures are dominated by queue wait. They
show throughput under this workload rather than individual-call latency. Since
the worker processed one slow shadow turn at a time while the live assistant
completed new turns in roughly two seconds, backlog growth was expected and
was directly observed.

## Calls, retries, and failures

### Calls visible in terminal traces

| Stage | Trace-visible calls | Exact per-call duration available? | Provider-level failures |
| --- | ---: | --- | ---: |
| Interpretation | 32 | No; failed traces discarded core stage timings | 0 transport/time-out failures; 3 deterministic vocabulary rejections after output |
| Appraisal | 29 | Yes | 0 provider failures; 29 deterministic proposal rejections after output |
| Collaborative Content | 0 | Not applicable | 0 |
| Realization | 0 | Not applicable | 0 |
| **Total retained terminal-path calls** | **61** | Partial | **0 provider transport failures** |

Two jobs had more than one worker attempt:

- `greeting-03`: 3 worker attempts;
- `substantial-06-summarize`: 2 worker attempts.

That is **3 additional worker attempts** across two turns. The terminal trace
retains only the final attempt's detailed provider spans, so the prior attempts'
call count and duration cannot be reconstructed exactly. The actual total is
therefore at least 61 calls and no more than 67 if both configured model stages
ran on every unretained attempt. The report does not claim a fabricated exact
number.

Provider SDK retries were configured to zero. Appraisal used one provider
attempt per retained pipeline attempt. Realization made no attempts, so it
performed no validation-driven retry.

### Terminal failures

| Failure code | Count | Effect |
| --- | ---: | --- |
| `pipeline.cognitive_turn.appraisal.model_appraisal.proposal_rejected` | 29 | Interpretation and the Appraisal provider ran; the deterministic Appraisal response compiler rejected the proposal; Action and Realization did not complete |
| `pipeline.interpretation.semantic_grounding.semantic_parser.argument_value_outside_vocabulary` | 3 | Interpretation provider ran; semantic compilation rejected an argument value; later model stages did not run |

These are not model transport failures. The providers returned outputs, but the
current deterministic contracts rejected them. From a conversational readiness
perspective the distinction does not improve the outcome: the shadow still
produced no response.

## Per-turn results

`Interp.` means the Interpretation model call ran but its exact call duration
was not separately persisted. `Appraisal` is the exact provider-attempt span.
Queue and processing are measured independently. Every “Useful accepted?”
entry is “No” because no accepted candidate existed.

| Turn | Live done | Queue | Shadow process | Interp. | Appraisal call | Calls | Worker retries | Terminal result | Useful accepted? |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| continuous-01-greeting | 2.971 s | 0.034 s | 3.000 s | ran; exact unavailable | — | 1 | 0 | Interpretation vocabulary rejection | No |
| continuous-02-proposal | 2.661 s | 0.197 s | 19.780 s | ran; exact unavailable | 16.290 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-03-selection | 2.439 s | 17.382 s | 15.687 s | ran; exact unavailable | 13.096 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-04-explanation | 2.239 s | 30.685 s | 14.913 s | ran; exact unavailable | 11.947 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-05-correction | 2.387 s | 43.384 s | 11.763 s | ran; exact unavailable | 9.692 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-06-follow-up | 2.275 s | 52.824 s | 16.796 s | ran; exact unavailable | 11.940 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-07-description | 5.299 s | 67.399 s | 17.937 s | ran; exact unavailable | 14.198 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-08-substantial | 3.108 s | 80.128 s | 20.101 s | ran; exact unavailable | 17.011 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-09-summary | 1.585 s | 97.193 s | 24.301 s | ran; exact unavailable | 18.043 s | 2 | 0 | Appraisal proposal rejected | No |
| continuous-10-reflection | 2.521 s | 119.986 s | 14.261 s | ran; exact unavailable | 12.649 s | 2 | 0 | Appraisal proposal rejected | No |
| greeting-01 | 0.756 s | 131.787 s | 15.897 s | ran; exact unavailable | 14.695 s | 2 | 0 | Appraisal proposal rejected | No |
| greeting-02 | 1.087 s | 147.005 s | 18.867 s | ran; exact unavailable | 17.483 s | 2 | 0 | Appraisal proposal rejected | No |
| greeting-03 | 1.072 s | 208.072 s | 12.841 s | ran; exact unavailable | 11.244 s | 2 | 2 | Appraisal proposal rejected | No |
| greeting-04 | 0.761 s | 219.907 s | 13.250 s | ran; exact unavailable | 12.246 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-01-identity | 0.708 s | 232.449 s | 14.274 s | ran; exact unavailable | 13.196 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-02-date | 0.703 s | 246.195 s | 10.983 s | ran; exact unavailable | 10.023 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-03-protein | 0.975 s | 256.577 s | 21.550 s | ran; exact unavailable | 19.851 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-04-fiber | 1.580 s | 277.209 s | 12.899 s | ran; exact unavailable | 11.689 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-05-macros | 1.726 s | 288.589 s | 12.536 s | ran; exact unavailable | 10.202 s | 2 | 0 | Appraisal proposal rejected | No |
| simple-06-balanced | 2.651 s | 299.484 s | 13.915 s | ran; exact unavailable | 12.639 s | 2 | 0 | Appraisal proposal rejected | No |
| substantial-01-compare | 4.474 s | 310.773 s | 15.055 s | ran; exact unavailable | 13.455 s | 2 | 0 | Appraisal proposal rejected | No |
| substantial-02-options | 3.448 s | 321.420 s | 1.370 s | ran; exact unavailable | — | 1 | 0 | Interpretation vocabulary rejection | No |
| substantial-03-explain | 4.252 s | 319.394 s | 12.959 s | ran; exact unavailable | 11.732 s | 2 | 0 | Appraisal proposal rejected | No |
| substantial-04-plan | 4.675 s | 328.155 s | 1.224 s | ran; exact unavailable | — | 1 | 0 | Interpretation vocabulary rejection | No |
| substantial-05-evaluate | 4.258 s | 324.733 s | 12.081 s | ran; exact unavailable | 10.596 s | 2 | 0 | Appraisal proposal rejected | No |
| substantial-06-summarize | 2.605 s | 353.964 s | 18.857 s | ran; exact unavailable | 17.535 s | 2 | 1 | Appraisal proposal rejected | No |
| correction-01-setup | 1.930 s | 370.247 s | 9.128 s | ran; exact unavailable | 8.112 s | 2 | 0 | Appraisal proposal rejected | No |
| correction-01-change | 2.054 s | 377.527 s | 8.961 s | ran; exact unavailable | 7.166 s | 2 | 0 | Appraisal proposal rejected | No |
| followup-01-setup | 1.173 s | 384.491 s | 7.343 s | ran; exact unavailable | 6.264 s | 2 | 0 | Appraisal proposal rejected | No |
| followup-01-expand | 1.255 s | 390.721 s | 8.645 s | ran; exact unavailable | 7.325 s | 2 | 0 | Appraisal proposal rejected | No |
| followup-02-setup | 3.284 s | 398.169 s | 7.605 s | ran; exact unavailable | 6.447 s | 2 | 0 | Appraisal proposal rejected | No |
| followup-02-clarify | 1.587 s | 402.553 s | 8.664 s | ran; exact unavailable | 6.887 s | 2 | 0 | Appraisal proposal rejected | No |

## Slow-turn examples

### Continuous turn 9: conversation summary

- Visible live response completed in **1.585 s**.
- Shadow queue wait was **97.193 s**.
- Shadow processing was **24.301 s**, the run maximum.
- The exact Appraisal call took **18.043 s**.
- Appraisal used 5,293 input tokens, including 1,024 cached input tokens, and
  produced 701 output tokens.
- The provider call completed, but response compilation rejected the proposal.
- Realization did not run; no candidate was ready.

This example is especially important because it was a normal recap in the
continuous conversation, not a tool request or unusually difficult external
task.

### Simple protein question

- Visible live response completed in **0.975 s**.
- Shadow processing was **21.550 s**.
- The Appraisal call took **19.851 s**, the run's slowest Appraisal call.
- The proposal was rejected and Realization did not run.

A simple one-sentence conceptual question incurred more than twenty seconds of
shadow processing without producing a response candidate.

### Continuous turn 8: coordinated preparation discussion

- Visible live response completed in **3.108 s**.
- Queue wait was **80.128 s**.
- Shadow processing was **20.101 s**.
- Appraisal took **17.011 s**.
- The proposal was rejected before Realization.

### Continuous turn 2: dinner proposal

- Visible live response completed in **2.661 s**.
- Queue wait was only **0.197 s**, so this is a clean early-run example with
  almost no backlog.
- Shadow processing still took **19.780 s**.
- Appraisal took **16.290 s**.
- No candidate was produced.

This turn demonstrates that the long processing time is not merely a queueing
artifact.

### Greeting turn 2

- Visible live response completed in **1.087 s**.
- Shadow processing took **18.867 s**.
- Appraisal alone took **17.483 s**.
- No candidate was produced.

The latency is not confined to substantial tasks. A greeting can traverse the
same expensive Appraisal path.

## Continuous-conversation throughput

The 10-turn conversation is the clearest conversational-capacity signal:

| Turn | Live completion | Shadow processing | Queue before shadow start |
| ---: | ---: | ---: | ---: |
| 1 | 2.971 s | 3.000 s | 0.034 s |
| 2 | 2.661 s | 19.780 s | 0.197 s |
| 3 | 2.439 s | 15.687 s | 17.382 s |
| 4 | 2.239 s | 14.913 s | 30.685 s |
| 5 | 2.387 s | 11.763 s | 43.384 s |
| 6 | 2.275 s | 16.796 s | 52.824 s |
| 7 | 5.299 s | 17.937 s | 67.399 s |
| 8 | 3.108 s | 20.101 s | 80.128 s |
| 9 | 1.585 s | 24.301 s | 97.193 s |
| 10 | 2.521 s | 14.261 s | 119.986 s |

After the first two turns, the live conversation generated turns faster than
the shadow worker completed them. By turn 10, the shadow began processing
approximately two minutes after capture. No turn in the sequence yielded a
usable candidate, so the backlog was entirely spent producing terminal
failures.

## Visible live response versus shadow response

The live assistant was successful as a transport and conversational path:

- 32/32 WebSocket turns ended with `done`;
- responses streamed visibly;
- the 10-turn conversation remained coherent at the live boundary; and
- visible completion latency stayed within 0.703–5.299 seconds, with no
  transport failures.

The shadow result was completely different:

- 32/32 traces ended `failed`;
- 0/32 entered Realization;
- 0/32 produced a final candidate;
- 0/32 passed fidelity validation;
- 0/32 could be behaviorally reviewed as useful; and
- no shadow content influenced, delayed, or replaced the visible response.

The live timings must not be used as evidence that the shadow is fast. The
shadow timings must not be used as evidence that users experienced the delay,
because isolation kept it off the delivery path.

## Is the chain fast enough for conversation?

No, not in the tested Phase 1 configuration.

Three separate observations support that conclusion:

1. **Per-turn processing is too slow relative to the live conversational
   cadence.** Median shadow processing was 13.105 seconds before Realization,
   compared with 2.257 seconds for a complete visible live response.
2. **Throughput does not keep pace.** In the continuous conversation, queue wait
   grew to 120 seconds by turn 10 because one sequential worker serviced turns
   more slowly than the conversation produced them.
3. **The latency bought no usable behavior.** Every turn failed and no validated
   response candidate became ready. Even a lower latency terminal failure would
   not qualify as conversational success.

This result does not estimate what a corrected or optimized chain would do. It
measures the current chain exactly as configured and executed on this date.

## Telemetry limitations

The run exposed several limits that constrain the precision of the report:

- Failed pipeline traces do not preserve completed core-stage timings, so the
  exact Interpretation provider duration is unavailable.
- Appraisal has exact spans, but its summary coverage is explicitly
  `appraisal_only`.
- Realization per-attempt latency is supported by the trace contract, but no
  Realization attempt occurred in this run.
- Queue wait and response-ready fields in `trace_summary.latency` remain null;
  queue and terminal timing here were computed from durable job and trace
  timestamps.
- Two jobs retried at the worker level, but earlier attempt spans are not
  retained in the terminal trace, preventing an exact total provider-call
  count across retries.
- This local sample is too small for an SLA and does not model production
  concurrency, rate limits, geography, or sustained multi-user load.

These gaps are reported as unavailable rather than estimated.

## Phase boundary

This is a Phase 1 result for the current enabled chain:

```text
model Interpretation
    -> deterministic Goal / workspace / requirement processing
    -> model Appraisal
    -> deterministic compiler rejection in this run
    -> Realization not reached
```

Collaborative Content and later model-backed phases were not enabled. This
evaluation must be repeated when additional phases are enabled because each
new provider call changes both the critical path and throughput. Results from
this run must not be extrapolated to those future configurations.

## Reproduction artifacts

- Live corpus driver:
  [`backend/scripts/cognitive_shadow_latency_phase1_live.py`](../../../../../scripts/cognitive_shadow_latency_phase1_live.py)
- Trace-summary analyzer:
  [`backend/scripts/cognitive_shadow_latency_phase1_analyze.py`](../../../../../scripts/cognitive_shadow_latency_phase1_analyze.py)
- Private local live-turn artifact:
  `/private/tmp/cognitive-phase1-latency-live-turns.json`
- Private local analyzed artifact:
  `/private/tmp/cognitive-phase1-latency-analysis.json`
- Content-free summary schema: `cognitive-shadow-trace-summary-v0.6`
- Latency schema: `cognitive-latency-summary-v0.1`

The private artifacts contain local trace identifiers and are not committed.
The Markdown report contains only bounded synthetic prompts, content-free
timing data, stable failure codes, and aggregate results.
