# Codex latency diagnostics (2026-07-11)

## Scope

This report records the first P0 diagnostic pass for `gpt-5.6-luna`. It does
not propose or implement a tool-system refactor. The benchmark used the
current ChatGPT account, `codex-cli 0.144.1`, the local App Server, the fixed
prompt `Reply with exactly: PONG`, no warm-up, and two measured runs per group:

```bash
.venv/bin/python scripts/dev/bench_codex_latency.py \
  --warmup 0 --runs 2 --timeout 300 \
  --output docs/development/codex_latency_results.jsonl
```

The complete machine-readable run rows and summaries are in
[`codex_latency_results.jsonl`](./codex_latency_results.jsonl).

## Raw results

All values are milliseconds. `TTFT` is measured from `call_enter` to the first
text delta. No run emitted a reasoning delta. Every run succeeded and started
exactly one Codex Turn.

| Group | Run | Effort | Tools | thread/start | turn/start | TTFT | Total | Cleanup | Turns |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| A | 1 | low | 0 | 1075.303 | 3.835 | 14898.634 | 15064.690 | 2.315 | 1 |
| A | 2 | low | 0 | 930.880 | 1.882 | 11206.642 | 11363.355 | 1.683 | 1 |
| B | 1 | medium | 0 | 943.838 | 1.781 | 26184.876 | 26451.819 | 2.955 | 1 |
| B | 2 | medium | 0 | 917.834 | 1.599 | 118142.823 | 118288.604 | 1.928 | 1 |
| C | 1 | low | 25 | 902.120 | 1.237 | 113881.216 | 114077.701 | 1.911 | 1 |
| C | 2 | low | 25 | 936.377 | 2.732 | 114155.376 | 114606.382 | 3.652 | 1 |
| D | 1 | medium | 25 | 1028.129 | 2.157 | 113899.390 | 114094.705 | 1.794 | 1 |
| D | 2 | medium | 25 | 946.524 | 0.975 | 114625.488 | 114890.573 | 3.946 | 1 |

## Medians

| Group | thread/start | turn/start | TTFT | Total | Cleanup |
|---|---:|---:|---:|---:|---:|
| A | 1003.091 | 2.858 | 13052.638 | 13214.022 | 1.999 |
| B | 930.836 | 1.690 | 72163.849 | 72370.212 | 2.442 |
| C | 919.248 | 1.985 | 114018.296 | 114342.041 | 2.782 |
| D | 987.326 | 1.566 | 114262.439 | 114492.639 | 2.870 |

Median total deltas were B−A `+59156.190`, C−A `+101128.019`, D−B
`+42122.427`, and D−C `+150.598` ms.

## Findings

- App Server preparation is not the dominant delay. `thread/start` stayed
  between 0.902 and 1.075 seconds; `turn/start` ack stayed between 0.975 and
  3.835 ms; cleanup stayed between 1.683 and 3.946 ms.
- The long tail is inside the active Codex Turn before the first text delta.
  The 114-second runs spent more than 99% of total time waiting for TTFT.
- The 25-tool C and D samples both reproduced the 113–116 second field
  symptom. Their medians differ by only 151 ms, so reasoning effort did not
  materially change that plateau in this small sample.
- Tools correlate with the plateau in this measured eight-run set, but the
  evidence is not sufficient for a causal tool-system redesign. The no-tool B
  group also contains a 118-second outlier, while A completed in 11–15
  seconds. This demonstrates substantial server-side/run-to-run variance.
- The duplicate-turn hypothesis is now independently guarded: each measured
  request recorded `turn_count=1`, and a second concurrent turn for the same
  `(session_id, model_id)` fails before `thread/start`/`turn/start`.

The next diagnostic step should be a larger randomized/interleaved sample
using this script, not a speculative refactor. The new `off`/`all` mode makes
that experiment possible without changing any other provider.
