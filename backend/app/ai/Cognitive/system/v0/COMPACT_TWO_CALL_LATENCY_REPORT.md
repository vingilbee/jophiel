# Compact two-call latency and size record

2026-10-04. **No live provider latency benchmark was run.** p50/p95/max latency,
live acceptance rate, real input/output/cache/reasoning token counts and first-token
timing are unavailable. The user's previously reported 25.68 seconds is historical
context, not a measurement in this implementation session.

Local configuration was checked with an allowlist: shadow already enabled,
`two_call` now selected, compact protocol, `gpt-6-luna`, reasoning `none`, per-call
timeout 20 seconds, output limit 6,000, zero SDK retries, nonstreaming,
`store=false`. A successful normal turn has exactly two sequential calls; failed
first-call compilation prevents the second. Captured final wording has no third
provider call. A worker process must reload cached settings to use the change.

The offline script compares canonical Unicode character counts with synthetic
fixtures. Counts below exclude instructions and response schema; those costs are
separately retained in `COMPACT_TWO_CALL_OFFLINE_MEASUREMENTS.json`.

| Fixture/call | Legacy payload | Compact payload (approx.) | Legacy output | Compact output |
| --- | ---: | ---: | ---: | ---: |
| Social / Call 1 | 25,939 | 15,726 | 4,718 | 895 |
| Social / Call 2 | 31,125 | 4,483 | 642 | 265 |
| Artifact / Call 1 | 26,141 | 15,795 | 29,657 | 836 |
| Artifact / Call 2 | 62,158 | 11,675 | 2,540 | 1,990 |

Both protocols' fixtures complete core processing and accepted shadow Realization
and pass core replay. These four fixture outcomes are not a real-model acceptance
distribution. Offline-double timing is not a provider speed benchmark.

The largest remaining Call 1 sections are predicate descriptions (~5.3k characters)
and shared argument definitions (~7.3k); its schema is ~6.6k and instructions ~2.8k.
Call 2 social schema is ~4.3k, artifact schema ~9.3k, and instructions ~2.9k.
Artifact future templates remain ~6.2k characters after removing repeated
descriptions and code-owned parameter schemas. Task sources/revisions are retained.

The rough characters/4 estimate, including schema and instructions, puts Call 1
around 6.3k input tokens, social Call 2 around 2.9k, and artifact Call 2 around 6k.
This is not measured tokenization. The brief's 2–4k Call 1 input target has not
been established; vocabulary/schema remains a cost to evaluate. Generated-output
and packet-size reductions alone do not prove the 5-second p50/8-second p95 target.

Telemetry now records actual provider usage and measured provider/parsing spans
when supplied. Numeric payload/output section attribution includes instructions
and schema and is exposed through compact trace summaries. First-token, queue
wait and delivery stay unavailable until actually measured; nested local spans
must not be summed as full-path latency. Durable persistence and external delivery
are not timed by the offline report.

Reproduce without external requests:

```sh
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run --no-sync python scripts/cognitive_compact_offline_report.py
```

Pending: real configured provider runs grouped by social/information/artifact
class, successful and failed-attempt distributions, cache usage, full worker-path
wall/queue/persistence timing, and candidate acceptance. No rollout is inferred
from the structural reductions.
