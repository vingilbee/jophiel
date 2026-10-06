# Two-Call Cognitive Live Shadow Latency Evaluation

Date: 2026-10-03  
Run ID: `f31446212d674abf83ee6e38e1062421`  
Sample: 32 authenticated application-path turns, including one uninterrupted 10-turn conversation  
Mode: `COGNITIVE_SHADOW_ORCHESTRATION=two_call`  
Model: `gpt-6-luna`, `reasoning_effort=none`

## Executive result

The current two-call Cognitive route did not complete two model calls on any
evaluated turn.

- The visible live assistant completed 32/32 turns through its separate
  `gpt-4.1` path.
- Cognitive `call_one` ran 32 times.
- Cognitive `call_two` ran **zero** times.
- All 32 shadow traces failed after or during `call_one`.
- Zero turns reached captured Realization.
- Zero useful accepted shadow candidates were produced.
- Median `call_one` provider latency was **10.596 s**; p95 was **18.419 s**;
  maximum was **20.039 s**.
- Median shadow processing was **10.664 s**; p95 was **18.488 s**; maximum was
  **20.083 s**.
- Median capture-to-terminal latency, including the single-worker queue, was
  **181.970 s**; p95 was **305.874 s**; maximum was **313.667 s**.
- The continuous conversation accumulated **109.632 s** of queue delay by turn
  10.

This run measures the current two-call route as implemented, but it does not
measure successful two-call critical-path latency. The route behaved as a
one-call failure path because deterministic compilation rejected the first
call's output before the second request could be made.

## Application path and isolation

The identical 32-turn corpus from the earlier Phase 1 evaluations was sent
through the authenticated frontend WebSocket protocol:

```text
authenticated synthetic frontend-protocol client
    -> /api/chat/ws
    -> durable live chat worker
    -> visible live assistant response
    -> immutable shadow capture
    -> two-call Cognitive Shadow route
    -> durable PostgreSQL terminal trace
    -> trace_summary
```

The client did not call the Cognitive runner directly. Shadow isolation
remained enabled, sampling was 1.0 for the bounded test, and no shadow output
received live delivery authority.

## Preflight verification

- Focused two-call, trace-summary, and worker tests: **61 passed, 14 skipped**.
- Skips were database-dependent test cases outside the live disposable QA
  database run.
- A bounded synthetic live canary made one `call_one` request, took 14.068 s,
  and failed `appraisal_binding`; it therefore did not reach `call_two`.
- The full corpus was still run to determine whether this was input-dependent.

## Intended versus actual model stages

| Stage | Intended responsibility | Actual calls | Result |
| --- | --- | ---: | --- |
| Visible assistant | User-visible response; outside Cognitive Shadow | 32 | 32 `done` responses |
| Cognitive `call_one` | Combined semantic candidates, goal-outcome assessments, and Appraisal proposal | 32 | 30 Appraisal-binding rejections, one semantic vocabulary rejection, one timeout |
| Deterministic middle spine | Compile Interpretation, goals, Appraisal, and state | 31 attempted after returned output | Rejected every returned `call_one` result before `call_two` |
| Cognitive `call_two` | Content, behavior plan, and captured wording | 0 | Never requested |
| Captured Realization validation | Validate `call_two` wording without a third LLM call | 0 | `not_requested` on all turns |

The retained model-call count is exact for this run: **32**. Worker retries and
SDK retries were both zero.

## Aggregate latency from `trace_summary`

`trace_summary` reported `coverage=two_call`, measurement scope
`shadow_worker_attempt`, and latency schema `cognitive-latency-summary-v0.2`.

| Measure | N | Median | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Visible live first token | 32 | 0.660 s | 1.134 s | 1.299 s |
| Visible live completion | 32 | 1.834 s | 4.013 s | 4.271 s |
| Shadow queue wait | 32 | 171.165 s | 296.280 s | 303.851 s |
| Shadow processing after start | 32 | 10.664 s | 18.488 s | 20.083 s |
| Capture to terminal failure | 32 | 181.970 s | 305.874 s | 313.667 s |
| `call_one` provider attempt | 32 | 10.596 s | 18.419 s | 20.039 s |
| `call_two` provider attempt | 0 | — | — | — |
| Sum of provider work per turn | 32 | 10.596 s | 18.419 s | 20.039 s |
| Fully validated shadow response ready | 0 | — | — | — |

The visible assistant remained a different model path. Its latency cannot be
attributed to the two-call Cognitive implementation.

## Comparison with the prior staged Luna run

| Measure | Staged Luna | Current two-call route | Interpretation |
| --- | ---: | ---: | --- |
| Median shadow processing | 14.499 s | 10.664 s | Faster terminal failure, not faster successful output |
| Median measured provider call | 12.662 s Appraisal | 10.596 s `call_one` | Different request responsibilities; not a direct model benchmark |
| Median queue wait | 265.067 s | 171.165 s | Less work per failed turn and no retries |
| Calls retained | 63 | 32 | Two-call route stopped after its first request |
| Turns reaching final wording model work | 0 | 0 | No improvement |
| Accepted candidates | 0/32 | 0/32 | No improvement |

The lower processing and queue figures are real, but they cannot be credited as
a successful two-call latency improvement. The system avoided the second call
by failing before it.

## Usage

Usage was available for 31 returned `call_one` results; the timeout had no
completed usage record.

| Usage | Median | Range |
| --- | ---: | ---: |
| Input tokens | 7,267 | 7,194–11,390 |
| Output tokens | 1,445 | 725–2,369 |

The OpenAI latency guidance notes that generated output tokens are commonly the
largest latency factor and that sequential model requests add round-trip
latency. This run only measured the first intended request.

## Failures and retries

| Terminal failure | Count | Provider calls retained | Effect |
| --- | ---: | ---: | --- |
| `pipeline.cognitive_turn.appraisal.two_call.call_one.appraisal_binding.proposal_rejected` | 30 | 30 | `call_one` returned; deterministic Appraisal binding rejected it |
| `pipeline.interpretation.semantic_grounding.semantic_parser.argument_value_outside_vocabulary` | 1 | 1 | `call_one` returned; semantic compilation rejected a closed-vocabulary value |
| `pipeline.interpretation.semantic_grounding.two_call.call_one.timeout` | 1 | 1 | First provider request reached the 20-second timeout |

- SDK retries: 0.
- Worker retries: 0.
- Validation repair calls: 0.
- Provider error calls: 1 timeout.
- `call_two` attempts: 0.

Fast failure is not counted as a successful response.

## Uninterrupted 10-turn conversation

| Turn | Live completion | Queue wait | Shadow processing | `call_one` | `call_two` | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 greeting | 1.525 s | 0.028 s | 18.362 s | 18.315 s | — | Appraisal binding rejected |
| 2 proposal | 2.186 s | 16.947 s | 12.124 s | 12.079 s | — | Appraisal binding rejected |
| 3 selection | 2.466 s | 26.927 s | 13.693 s | 13.657 s | — | Appraisal binding rejected |
| 4 explanation | 1.824 s | 38.181 s | 10.574 s | 10.516 s | — | Appraisal binding rejected |
| 5 correction | 2.113 s | 47.029 s | 18.488 s | 18.419 s | — | Appraisal binding rejected |
| 6 follow-up | 1.843 s | 63.439 s | 13.597 s | 13.528 s | — | Appraisal binding rejected |
| 7 description | 1.763 s | 75.281 s | 20.083 s | 20.039 s | — | Timeout |
| 8 substantial | 3.888 s | 93.709 s | 10.643 s | 10.571 s | — | Appraisal binding rejected |
| 9 summary | 1.638 s | 100.490 s | 10.685 s | 10.621 s | — | Appraisal binding rejected |
| 10 reflection | 2.171 s | 109.632 s | 12.889 s | 12.817 s | — | Appraisal binding rejected |

The worker still could not keep pace with a normal conversation even while
performing only the first intended provider call.

## Per-turn model-call report

| Turn | Live done | Queue | Processing | `call_one` | `call_two` | Calls | Accepted? |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| continuous-01-greeting | 1.525 s | 0.028 s | 18.362 s | 18.315 s | — | 1 | No |
| continuous-02-proposal | 2.186 s | 16.947 s | 12.124 s | 12.079 s | — | 1 | No |
| continuous-03-selection | 2.466 s | 26.927 s | 13.693 s | 13.657 s | — | 1 | No |
| continuous-04-explanation | 1.824 s | 38.181 s | 10.574 s | 10.516 s | — | 1 | No |
| continuous-05-correction | 2.113 s | 47.029 s | 18.488 s | 18.419 s | — | 1 | No |
| continuous-06-follow-up | 1.843 s | 63.439 s | 13.597 s | 13.528 s | — | 1 | No |
| continuous-07-description | 1.763 s | 75.281 s | 20.083 s | 20.039 s | — | 1 | No |
| continuous-08-substantial | 3.888 s | 93.709 s | 10.643 s | 10.571 s | — | 1 | No |
| continuous-09-summary | 1.638 s | 100.490 s | 10.685 s | 10.621 s | — | 1 | No |
| continuous-10-reflection | 2.171 s | 109.632 s | 12.889 s | 12.817 s | — | 1 | No |
| greeting-01 | 0.790 s | 120.391 s | 6.175 s | 6.138 s | — | 1 | No |
| greeting-02 | 1.272 s | 125.850 s | 16.460 s | 16.416 s | — | 1 | No |
| greeting-03 | 1.075 s | 141.059 s | 12.927 s | 12.896 s | — | 1 | No |
| greeting-04 | 0.842 s | 152.978 s | 5.682 s | 5.653 s | — | 1 | No |
| simple-01-identity | 0.849 s | 157.839 s | 10.208 s | 10.165 s | — | 1 | No |
| simple-02-date | 0.743 s | 167.251 s | 8.523 s | 8.484 s | — | 1 | No |
| simple-03-protein | 0.919 s | 175.079 s | 13.087 s | 13.044 s | — | 1 | No |
| simple-04-fiber | 1.845 s | 187.302 s | 10.288 s | 10.255 s | — | 1 | No |
| simple-05-macros | 1.753 s | 195.841 s | 10.399 s | 10.359 s | — | 1 | No |
| simple-06-balanced | 2.134 s | 204.505 s | 9.903 s | 9.859 s | — | 1 | No |
| substantial-01-compare | 4.271 s | 212.337 s | 11.276 s | 11.230 s | — | 1 | No |
| substantial-02-options | 3.261 s | 219.418 s | 11.151 s | 11.109 s | — | 1 | No |
| substantial-03-explain | 2.970 s | 227.377 s | 12.713 s | 12.666 s | — | 1 | No |
| substantial-04-plan | 3.405 s | 237.143 s | 14.212 s | 14.168 s | — | 1 | No |
| substantial-05-evaluate | 4.013 s | 247.992 s | 8.997 s | 8.956 s | — | 1 | No |
| substantial-06-summarize | 2.119 s | 253.075 s | 9.887 s | 9.862 s | — | 1 | No |
| correction-01-setup | 1.058 s | 260.867 s | 9.993 s | 9.950 s | — | 1 | No |
| correction-01-change | 2.357 s | 269.851 s | 13.659 s | 13.621 s | — | 1 | No |
| followup-01-setup | 1.036 s | 281.246 s | 8.356 s | 8.326 s | — | 1 | No |
| followup-01-expand | 1.778 s | 288.592 s | 9.398 s | 9.360 s | — | 1 | No |
| followup-02-setup | 2.082 s | 296.280 s | 9.594 s | 9.265 s | — | 1 | No |
| followup-02-clarify | 1.714 s | 303.851 s | 9.816 s | 9.775 s | — | 1 | No |

## Slow-turn examples

### Continuous description turn

- Visible completion: 1.763 s.
- Queue wait: 75.281 s.
- `call_one`: 20.039 s.
- Terminal result: timeout.
- `call_two`: not requested.

### Continuous correction

- Visible completion: 2.113 s.
- `call_one`: 18.419 s.
- Returned output failed Appraisal binding.
- No candidate was produced.

### Greeting 2

- Visible completion: 1.272 s.
- `call_one`: 16.416 s.
- Returned output failed Appraisal binding.
- A greeting still incurred a long first request and never reached wording.

### Substantial plan

- Visible completion: 3.405 s.
- Queue wait: 237.143 s.
- `call_one`: 14.168 s.
- Returned output failed Appraisal binding.

## Conclusion

The architecture is wired and its two-call telemetry works, but the live
behavior is not yet a functioning two-call Cognitive system. The first combined
response does not compile into the existing Appraisal contract, so the second
model call and captured wording path remain untested end to end.

It is therefore premature to say that two calls are fast enough for
conversation. The only measured provider stage already has a 10.596-second
median, and a complete successful turn would add the unmeasured `call_two`
latency afterward.

## Reproduction artifacts

- Application-path corpus driver:
  [`backend/scripts/cognitive_shadow_latency_phase1_live.py`](../../../../../scripts/cognitive_shadow_latency_phase1_live.py)
- Two-call trace-summary analyzer:
  [`backend/scripts/cognitive_shadow_latency_two_call_analyze.py`](../../../../../scripts/cognitive_shadow_latency_two_call_analyze.py)
- Private live-turn artifact:
  `/private/tmp/cognitive-phase1-latency-live-turns.json`
- Private two-call analysis artifact:
  `/private/tmp/cognitive-two-call-latency-analysis.json`
- Summary schema: `cognitive-shadow-trace-summary-v0.6`
- Latency schema: `cognitive-latency-summary-v0.2`

Private artifacts contain local trace identifiers and are not intended for
commit. No shadow output was delivered to the user.
