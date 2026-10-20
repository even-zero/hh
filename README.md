# hh — the timeout your test suite actually needs

A caller that runs a test suite has to give it a timeout. If the timeout is too
low the suite is **SIGKILLed**, the work is thrown away, and the caller runs it
again from zero. hh replaces that guess with a measurement: it records how long
each test takes, and from the second run on it hands out a budget derived from
those measurements.

It does one thing. There is no sampling, no prior, no parallelism, no scoring.

```
first call    run the suite like the naive command, and record it   (no prediction)
later calls   budget = p90(measured suite) x 1.25                   (a measurement)
on overrun    resume from the ledger; finished tests are never re-paid
```

Stdlib-only core · zero install · no network · SQLite store inside the repo.

## Quickstart

As a tool (the agent-facing surface):

```python
from hh.tool import handle_tool_call

result = handle_tool_call("run_tests", {"root": "/path/to/repo", "runner": "pytest"})
# -> {"status": "ok",
#     "operating_level": "measured",
#     "budget": {"basis": "measured", "budget_s": 24.7, "p50_s": 17.5,
#                "p90_s": 19.8, "coverage": 1.0, "confidence": 1.0},
#     "wall_s": 17.9, "attempts": 1, "timed_out": 0, ...}
```

As a CLI:

```bash
hh plan --root . --runner pytest --out plan.json   # measure, emit the budget
hh run  --plan plan.json --root .                  # execute + record
hh report                                          # what the store has measured
```

`--runner` is one of `unittest`, `pytest`, `node` (Mocha). The store lives in
`./.hh/` inside the suite root (gitignore it, or point `--store-root` elsewhere).

## What each call returns

| field | meaning |
|---|---|
| `status` | `ok`, `incomplete` (work left), `shard_failed` (runner died) |
| `budget.basis` | `record` (no measurements yet) or `measured` |
| `budget.budget_s` | the timeout to give this suite next time |
| `budget.p50_s`, `budget.p90_s` | the measured distribution the budget comes from |
| `budget.confidence` | share of the suite that has its own measurements |
| `attempts` / `timed_out` | runner processes started / killed at the budget |
| `wall_s` | what the caller waited |
| `resumed_tests` | tests recognised as already paid for |

## How the budget is computed

1. The suite is enumerated (pytest `--collect-only`, unittest import, Mocha load),
   never executed, and the manifest is cached by tree fingerprint.
2. Every test gets a point estimate: its own measured rows if it has any
   (median/EWMA blend), else its file's mean, else the suite mean.
3. The suite total is a p50/p90/p99 over those distributions — Monte-Carlo below
   200 tests, closed-form lognormal moment matching above.
4. `budget = p90 x 1.25`, floored at 3 s and capped at `4 x p50 + 30 s`.

A cold store has no rows, so step 2 has nothing to work with: the tool says so
(`basis: "record"`, `predicted_wall_s: null`) and records instead of guessing.

## Guarantees, and their honest extent

- **No guess is ever presented as an estimate.** A record pass reports
  `operating_level: "record"` and a null prediction.
- **A wrong budget costs one budget, not the suite.** Finished tests are in the
  ledger; an overrun resumes from the last unfinished test.
- **hh cannot beat a correct guess.** If the suite already fits your timeout,
  hh costs its recording (~35 µs per test) and nothing else. It is insurance
  against a too-low timeout, not a speed-up.
- **Small suites are dominated by fixed costs.** Below ~1 s of test time the
  tool's own process start and SQLite writes are a visible percentage.

## Repository layout

```
hh/
  tool.py        the run_tests tool: plan -> execute -> record -> answer
  policy.py      one shard, one budget (p90 x safety)
  estimator.py   measured durations -> p50/p90/p99
  executor.py    run under the budget; resume on overrun
  store.py       SQLite: runs, observations, aggregates
  keys.py        stable test identities
  adapters/      unittest, pytest, Mocha (live ledger + discovery)
benchmark/       ten repos, one metric (see benchmark/README.md)
tests/           unit + end-to-end tests
```

## Development

```bash
python3 -m unittest discover -s tests -q
python3 -m benchmark setup && python3 -m benchmark run     # the benchmark
```

## License

MIT — see `LICENSE`.
