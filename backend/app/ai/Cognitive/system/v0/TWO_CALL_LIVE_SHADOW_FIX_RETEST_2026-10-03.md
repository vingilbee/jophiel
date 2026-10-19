# Two-Call Cognitive Live Shadow Fix Retest

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Date: 2026-10-03  
Run ID: `e121c7c1b5f141f89202d7aa1f607a65`  
Sample: 32 authenticated application-path turns, including one uninterrupted 10-turn conversation  
Mode: `COGNITIVE_SHADOW_ORCHESTRATION=two_call`  
Model: `gpt-6-luna`, `reasoning_effort=none`  
Shadow isolation: enabled

## Executive result

The attempted fix does **not** restore the two-call Cognitive pipeline end to
end.

- The visible live assistant completed 32/32 responses through its separate
  production chat path.
- Cognitive `call_one` completed 32/32 real provider requests.
- Cognitive `call_two` ran **0/32** times.
- The shadow worker ended in failure on **32/32** turns.
- Fully validated shadow responses ready: **0/32**.
- Useful accepted shadow candidates: **0/32**.
- Realization status was `not_requested` on all 32 turns.
- Worker retries, SDK retries, and provider errors were all zero.

The earlier generic `appraisal_binding` rejection has been removed, so that
part of the fix is observable. It exposed the next incompatible boundary:
31/32 turns now fail
`pipeline.cognitive_turn.appraisal.two_call.call_one.accepted_event_missing.proposal_rejected`.
One turn passed that point but failed `pipeline.requirement_resolution` before
`call_two` could begin.

This remains a one-provider-call failure path, not a functioning two-call
conversation path. Its latency cannot be interpreted as successful two-call
latency, and fast failure is not counted as a successful response.

## What was tested

The same 32-turn corpus used in the preceding live evaluations was sent through
the authenticated application WebSocket protocol:

```text
authenticated synthetic frontend-protocol client
    -> /api/chat/ws
    -> durable live chat worker
    -> visible live assistant response
    -> immutable isolated shadow capture
    -> two-call Cognitive Shadow route
    -> durable PostgreSQL terminal trace
    -> trace_summary
```

This was not a direct invocation of the Cognitive runner. The live assistant
and the shadow were deliberately measured separately. Shadow output retained
no authority to alter the visible response, user state, tools, or delivery.

The corpus covered greetings, simple questions, corrections, follow-ups,
substantial conversational tasks, and an uninterrupted 10-turn exchange.

## Preflight and canary

- Focused orchestration, trace-summary, and worker tests: **71 passed, 14
  skipped**.
- The updated goal binding reports `goal-formation-binding-v0.2`.
- A real-provider canary completed `call_one` in 14.362 s and then failed
  `accepted_event_missing`; `call_two` was not requested.
- The full 32-turn corpus was still executed to rule out a canary-specific or
  input-specific failure.

The contrast between the passing focused tests and the 0/32 live result means
the automated suite does not presently exercise the real model output through
this boundary strongly enough to establish end-to-end viability.

## Model-backed stages that actually ran

| Stage | Intended responsibility | Actual calls | Observed result |
| --- | --- | ---: | --- |
| Visible live assistant | User-visible response outside Cognitive Shadow | 32 | 32 `done` responses |
| Cognitive `call_one` | Semantic candidates, goal-outcome assessments, and Appraisal proposal | 32 | All provider requests completed |
| Deterministic middle spine | Bind and validate Interpretation, goals, Appraisal, state, and requirements | 32 attempted | Rejected every turn before `call_two` |
| Cognitive `call_two` | Content, behavior plan, and captured conversational wording | 0 | Never requested |
| Captured Realization validation | Validate `call_two` wording without another model call | 0 | `not_requested` on all turns |

The retained provider-call count is exactly 32. Although the configuration is
called “two-call,” no evaluated turn executed a second model-backed stage.

## Aggregate latency from `trace_summary`

`trace_summary` provided the provider-attempt durations and total worker
timings used below.

| Measure | N | Median | P95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Visible live first token | 32 | 0.655 s | 1.317 s | 1.517 s |
| Visible live completion | 32 | 1.682 s | 3.551 s | 5.290 s |
| Shadow queue wait | 32 | 153.931 s | 266.916 s | 275.332 s |
| Shadow processing after worker start | 32 | 10.398 s | 16.695 s | 18.055 s |
| Capture to terminal failure | 32 | 163.226 s | 277.707 s | 286.877 s |
| `call_one` provider attempt | 32 | 10.349 s | 16.619 s | 18.004 s |
| `call_two` provider attempt | 0 | — | — | — |
| Sum of provider work per turn | 32 | 10.349 s | 16.619 s | 18.004 s |
| Fully validated shadow response ready | 0 | — | — | — |

There is no “time until a fully validated shadow response is ready” statistic:
no such response existed in the run. Capture-to-terminal measures time until a
failure was known, not response readiness.

The single serialized shadow worker also cannot keep up with normal foreground
conversation at roughly 10.4 seconds of processing per captured turn. Queue
delay reached 96.845 seconds by the tenth uninterrupted turn and 275.332
seconds on the final corpus turn.

## Comparison with the previous two-call run

| Measure | Previous run | Fix retest | Change and interpretation |
| --- | ---: | ---: | --- |
| `call_one` median | 10.596 s | 10.349 s | 0.247 s faster; too small to change conversational viability |
| `call_one` p95 | 18.419 s | 16.619 s | Lower, but still only the first request |
| Shadow processing median | 10.664 s | 10.398 s | Faster terminal failure, not a successful response |
| Queue wait median | 171.165 s | 153.931 s | Lower because only one call still runs and this run's first-call distribution was lower |
| `call_one` calls | 32 | 32 | Unchanged |
| `call_two` calls | 0 | 0 | No end-to-end progress |
| Accepted candidates | 0/32 | 0/32 | No improvement |
| Main deterministic rejection | `appraisal_binding` (30) | `accepted_event_missing` (31) | Old generic symptom removed; next binding invariant now rejects output |

The latency improvement is not evidence that the repaired system is fast
enough for conversation. The system still avoids the intended second request
by failing before it.

## Failure interpretation

| Terminal failure | Count | Provider calls retained | Meaning |
| --- | ---: | ---: | --- |
| `pipeline.cognitive_turn.appraisal.two_call.call_one.accepted_event_missing.proposal_rejected` | 31 | 31 | A known Appraisal dimension could not be tied to an accepted semantic-frame event under the deterministic evidence-binding invariant |
| `pipeline.requirement_resolution` | 1 | 1 | This turn advanced beyond Appraisal but failed requirement resolution before `call_two` |

The dominant rejection occurs after a valid provider response has been
returned. In the current middle-spine code, every known dimension must find an
Interpretation semantic frame in the selected scenario whose evidence
intersects that dimension's evidence references. The real `call_one` outputs
did not satisfy that relationship on 31 turns. This is a cross-representation
contract mismatch: the provider completed successfully, but the deterministic
compiler could not accept the model's bindings.

The requirement-resolution failure is also important. It shows that simply
passing Appraisal once is insufficient to reach `call_two`; another downstream
contract can still terminate the route.

## Usage

Usage was retained for all 32 completed `call_one` requests.

| Usage | Median | Range |
| --- | ---: | ---: |
| Input tokens | 7,716.5 | 7,555–12,200 |
| Output tokens | 1,551 | 733–2,628 |

The large fixed schema/context request explains why even greetings frequently
take several seconds. Longer continuous-context turns increased the first-call
input to more than 12,000 tokens and produced several of the slowest results.

## Uninterrupted 10-turn conversation

| Turn | Live completion | Queue wait | Shadow processing | `call_one` | `call_two` | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 greeting | 1.490 s | 0.037 s | 12.609 s | 12.570 s | — | Accepted event missing |
| 2 proposal | 2.475 s | 11.249 s | 11.486 s | 11.451 s | — | Accepted event missing |
| 3 selection | 2.337 s | 20.295 s | 18.055 s | 18.004 s | — | Accepted event missing |
| 4 explanation | 1.942 s | 36.095 s | 12.239 s | 12.190 s | — | Accepted event missing |
| 5 correction | 2.545 s | 46.424 s | 16.408 s | 16.362 s | — | Accepted event missing |
| 6 follow-up | 1.849 s | 60.319 s | 11.259 s | 11.209 s | — | Accepted event missing |
| 7 description | 1.401 s | 69.782 s | 10.226 s | 10.175 s | — | Accepted event missing |
| 8 substantial | 2.954 s | 78.675 s | 11.117 s | 11.070 s | — | Accepted event missing |
| 9 summary | 1.577 s | 86.893 s | 11.428 s | 11.351 s | — | Accepted event missing |
| 10 reflection | 1.818 s | 96.845 s | 16.695 s | 16.619 s | — | Requirement resolution |

The visible assistant stayed responsive because it is independent of the
shadow. The shadow itself fell increasingly far behind despite performing only
half of its intended model sequence.

## Slow-turn examples

| Turn | `call_one` | Input / output tokens | Queue wait | Terminal result |
| --- | ---: | ---: | ---: | --- |
| continuous-03-selection | 18.004 s | 9,062 / 2,580 | 20.295 s | Accepted event missing |
| continuous-10-reflection | 16.619 s | 12,200 / 2,628 | 96.845 s | Requirement resolution |
| continuous-05-correction | 16.362 s | 10,135 / 2,572 | 46.424 s | Accepted event missing |
| substantial-05-evaluate | 14.033 s | 7,590 / 1,516 | 213.792 s | Accepted event missing |
| greeting-02 | 13.152 s | 7,841 / 2,152 | 115.985 s | Accepted event missing |
| continuous-01-greeting | 12.570 s | 7,838 / 2,169 | 0.037 s | Accepted event missing |

These examples reinforce that task complexity is not the only latency driver:
even short greetings carry a large structured Cognitive payload and may produce
long structured outputs.

## Complete per-turn report

| Turn | Live done | Queue | Processing | `call_one` | `call_two` | Calls | Accepted? | Terminal result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| continuous-01-greeting | 1.490 s | 0.037 s | 12.609 s | 12.570 s | — | 1 | No | Accepted event missing |
| continuous-02-proposal | 2.475 s | 11.249 s | 11.486 s | 11.451 s | — | 1 | No | Accepted event missing |
| continuous-03-selection | 2.337 s | 20.295 s | 18.055 s | 18.004 s | — | 1 | No | Accepted event missing |
| continuous-04-explanation | 1.942 s | 36.095 s | 12.239 s | 12.190 s | — | 1 | No | Accepted event missing |
| continuous-05-correction | 2.545 s | 46.424 s | 16.408 s | 16.362 s | — | 1 | No | Accepted event missing |
| continuous-06-follow-up | 1.849 s | 60.319 s | 11.259 s | 11.209 s | — | 1 | No | Accepted event missing |
| continuous-07-description | 1.401 s | 69.782 s | 10.226 s | 10.175 s | — | 1 | No | Accepted event missing |
| continuous-08-substantial | 2.954 s | 78.675 s | 11.117 s | 11.070 s | — | 1 | No | Accepted event missing |
| continuous-09-summary | 1.577 s | 86.893 s | 11.428 s | 11.351 s | — | 1 | No | Accepted event missing |
| continuous-10-reflection | 1.818 s | 96.845 s | 16.695 s | 16.619 s | — | 1 | No | Requirement resolution |
| greeting-01 | 1.045 s | 111.780 s | 5.197 s | 5.157 s | — | 1 | No | Accepted event missing |
| greeting-02 | 0.953 s | 115.985 s | 13.197 s | 13.152 s | — | 1 | No | Accepted event missing |
| greeting-03 | 0.760 s | 128.274 s | 9.207 s | 9.172 s | — | 1 | No | Accepted event missing |
| greeting-04 | 0.762 s | 136.769 s | 5.510 s | 5.472 s | — | 1 | No | Accepted event missing |
| simple-01-identity | 0.748 s | 141.575 s | 8.420 s | 8.378 s | — | 1 | No | Accepted event missing |
| simple-02-date | 0.688 s | 149.288 s | 9.850 s | 9.791 s | — | 1 | No | Accepted event missing |
| simple-03-protein | 0.800 s | 158.574 s | 8.740 s | 8.710 s | — | 1 | No | Accepted event missing |
| simple-04-fiber | 1.690 s | 166.533 s | 9.044 s | 9.010 s | — | 1 | No | Accepted event missing |
| simple-05-macros | 1.673 s | 173.951 s | 9.459 s | 9.426 s | — | 1 | No | Accepted event missing |
| simple-06-balanced | 2.868 s | 181.798 s | 10.145 s | 10.104 s | — | 1 | No | Accepted event missing |
| substantial-01-compare | 5.290 s | 189.133 s | 9.358 s | 9.189 s | — | 1 | No | Accepted event missing |
| substantial-02-options | 2.776 s | 193.278 s | 9.531 s | 9.494 s | — | 1 | No | Accepted event missing |
| substantial-03-explain | 3.506 s | 200.068 s | 9.588 s | 9.553 s | — | 1 | No | Accepted event missing |
| substantial-04-plan | 3.193 s | 206.216 s | 10.735 s | 10.697 s | — | 1 | No | Accepted event missing |
| substantial-05-evaluate | 3.551 s | 213.792 s | 14.070 s | 14.033 s | — | 1 | No | Accepted event missing |
| substantial-06-summarize | 2.277 s | 224.370 s | 8.529 s | 8.490 s | — | 1 | No | Accepted event missing |
| correction-01-setup | 1.230 s | 230.664 s | 9.409 s | 9.382 s | — | 1 | No | Accepted event missing |
| correction-01-change | 1.604 s | 238.902 s | 10.569 s | 10.522 s | — | 1 | No | Accepted event missing |
| followup-01-setup | 1.240 s | 247.915 s | 10.061 s | 10.021 s | — | 1 | No | Accepted event missing |
| followup-01-expand | 1.341 s | 256.792 s | 11.418 s | 11.375 s | — | 1 | No | Accepted event missing |
| followup-02-setup | 2.409 s | 266.916 s | 10.791 s | 10.752 s | — | 1 | No | Accepted event missing |
| followup-02-clarify | 1.292 s | 275.332 s | 11.545 s | 11.501 s | — | 1 | No | Accepted event missing |

## Conclusion

The narrow `appraisal_binding` fix is present, but the live two-call Cognitive
pipeline is still not operational. The dominant failure moved one invariant
deeper—from generic Appraisal binding to missing accepted-event evidence—but
the externally meaningful outcome did not change: no second call, no captured
wording, and no accepted candidate.

Latency also remains unsuitable for an inline conversational response. Even if
the deterministic boundary were repaired without adding time, the current
first request alone has a 10.349-second median. The actual two-call latency is
still unknown because no live turn reached `call_two`; it must be measured
again once both calls and final validation complete successfully.

This document reports the current chain only. Any subsequent contract repair,
parallelism, prompt reduction, caching, or additional phase enablement requires
a new isolated live evaluation rather than extrapolation from this failure
path.
