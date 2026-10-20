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
from executor.tool import handle_tool_call

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

The store lives in `./.hh/` inside the suite root (gitignore it, or point
`--store-root` elsewhere). The command keeps the name `hh`; the importable
package is `executor`.

## Supported runners

| `--runner` | language | frameworks | doc |
|---|---|---|---|
| `pytest` | Python | pytest | [docs/pytest.md](docs/pytest.md) |
| `unittest` | Python | stdlib `unittest` | [docs/unittest.md](docs/unittest.md) |
| `node` | JavaScript | Mocha | [docs/mocha.md](docs/mocha.md) |
| `jest` | JavaScript | Jest | [docs/jest.md](docs/jest.md) |
| `vitest` | JavaScript | Vitest | [docs/vitest.md](docs/vitest.md) |
| `node-test` | JavaScript | Node's built-in `node:test` | [docs/node-test.md](docs/node-test.md) |
| `tap` | JavaScript | node-tap | [docs/tap.md](docs/tap.md) |
| `junit` | Java, Kotlin | JUnit 5, JUnit 4 via Vintage, Kotlin suites | [docs/junit.md](docs/junit.md) |

`docs/README.md` has the full matrix — what each runner can discover, how
finely a run can be resumed, the runners that are deliberately not supported
and why, and the contract a new adapter has to satisfy. The JavaScript adapters
share one execution model: [docs/javascript.md](docs/javascript.md).

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
| `unresolved` | ids from an old manifest the runner no longer finds (re-record them; not counted as failures) |

## How the budget is computed

1. The suite is enumerated by its runner (see `docs/`), never executed, and the
   manifest is cached by tree fingerprint.
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
- **Only tests that ran count.** Skipped tests and ids that no longer resolve
  are recorded but kept out of the duration aggregates: a 0 ms row would drag
  the p90 down until the budget killed the suite.
- **hh cannot beat a correct guess** on test work. If the suite already fits
  your timeout, hh costs its ledger and nothing else. (It can still be faster
  overall when the naive command goes through a wrapper such as `npm test`:
  hh drives the runner directly. That is a launcher difference, not a timeout
  one.) It is insurance against a too-low timeout, not a speed-up.
- **Small suites are dominated by fixed costs.** Below ~1 s of test time the
  tool's own process start and SQLite writes are a visible percentage.

## Repository layout

```
executor/
  __main__.py    the `hh` CLI (plan | run | report | tool-spec)
  tool.py        the run_tests tool: plan -> execute -> record -> answer
  policy.py      one shard, one budget (p90 x safety)
  estimator.py   measured durations -> p50/p90/p99
  runner.py      run under the budget; resume on overrun
  store.py       SQLite: runs, observations, aggregates
  keys.py        stable test identities
  adapters/      one module per runner (see docs/)
docs/            one page per runner, plus the support matrix
benchmark/       ten repos, one metric (see benchmark/README.md)
tests/           unit + end-to-end tests
```

## Development

```bash
python3 -m pip install pytest        # the pytest adapter tests need it
python3 -m executor --version         # the CLI, from a source checkout
python3 -m unittest discover -s tests -q
python3 -m benchmark setup && python3 -m benchmark run     # the benchmark
```

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.10 and 3.13, smokes
the CLI on `fixtures/synth_tiny_7`, installs the built wheel into a clean venv,
and runs the JUnit adapter against a real JUnit 5 suite with a JDK. The
benchmark is not in CI: it clones ten repositories and takes minutes.

## License

MIT — see `LICENSE`.
