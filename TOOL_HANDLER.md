# `run_tests` — the reference tool-handler

One tool an agent registers; the whole duration-aware stack behind it.
Implementation: `hh/tool.py`. Demo: `python3 experiments/tool_handler_demo.py`
(9/9 checks PASS). The CLI (`hh plan`, `hh run`) and this tool share one code
path — `collect_manifest → build_plan → execute_plan` — so the agent-facing
contract can never drift from what the lab measured.

## Schema

```json
{"name": "run_tests",
 "parameters": {"root": "…", "runner": "unittest|pytest|node",
                "workers": 2, "budget_s": null, "force": false,
                "deadline_s": null, "max_workers": null, "resume": false}}
```

An agent framework calls `handle_tool_call("run_tests", args) → JSON string`.

## What the tool promises (and the response proves)

| guarantee | how it shows up in the response |
|---|---|
| Never restarts from zero (Law 1) | an under-budget shard extends the remainder of the same process; finished tests are checkpointed to the ledger and skipped on resume. `discarded compute = 0` is structural, not measured luck |
| Budget is a measurement, not a guess | `estimate` carries `p50/p90/p99`, `coverage`, `n_unknown`; after one call `coverage` = 1.0 |
| The caller's deadline is respected *before* spending | if `budget_s < planned_bound_s` the tool returns `status="budget_too_small"` in ~0 s instead of burning the budget |
| Hangs don't poison the run | hang-suspect tests are quarantined (`shards[].quarantine`, 2 s cutoff); they are censored and reported, never allowed to blow the budget |
| Token discipline | one line summary + failures (≤20) + slowest 5; never 108 rows. ~340 tokens per call |
| Long-lived processes see edits | stale modules under the suite root are evicted from `sys.modules` before collection (bug found by the demo: an appended test was invisible on the second call) |

## Demo arc (live fixture copy, fresh store, 2 workers)

| call | agent's view | planned bound | wall | discarded |
|---|---|---|---|---|
| 1 cold | `ok`, budget from priors | 22.2 s | 13.4 s | 0 |
| 2 warm | `ok`, budget from measurements (p50 27.4 s vs 26.4 s truth) | 34.9 s | 13.4 s | 0 |
| 3 deadline 20 s | `budget_too_small` + hint | — | ~0.05 s | 0 |
| 4 new slow test added, deadline 60 s | `ok`, 109 tests, new test measured (15.3 s) | 35.1 s | 15.3 s | 0 |
| 5 CI deadline 12 s | `ok`, **W=3 chosen** (predicted 9.5 s), ran 9.63 s | 37.6 s | 9.6 s | 0 |

Total agent wall time: **52 s over 4 executed runs + a near-free refusal** (11/11
demo checks; receipts for that flow, incl. resume and hang cases, are in
TIME_SAVED.md), zero discarded compute — against the naive arm's **41.2 s wall with
15.0 s discarded and 2 blind guesses** for the same suite size (L1). Call 5 shows
the CI hook: same suite, a 12 s deadline, and the tool takes 3 workers (9.63 s)
instead of 2 (13.3 s) or 4 (6.8 s — wasted contention on a real CI box).

## Failure modes it deliberately refuses to hide

- `budget_too_small` is the tool saying "your deadline is smaller than the
  honest bound" — the alternative (run anyway) is the blind-restart tax in a
  nicer costume.
- `force=true` runs anyway; checkpointing still protects finished tests, and the
  response reports the real wall so the caller sees the overrun.
- `executed.censored` > 0 means some tests exceeded their cutoff and only a
  lower bound is known — the store remembers them (`hang_keys` in `hh report`),
  so the next plan quarantines them at 2 s.
