# The benchmark — one metric, ten repositories

```
python3 -m benchmark setup     # clone + install the repos in repos.json
python3 -m benchmark run       # measure (writes _work/results/timeout-metric.json)
python3 -m benchmark report    # write REPORT-<date>-timeout-metric.md
```

## The metric

A caller must give a test suite a timeout. If the timeout is too low the suite
is SIGKILLed, the work is lost, and it runs again. hh's only job is to produce a
timeout that is not too low. So there are two numbers, and nothing else:

1. **kill-free** — when the caller's guess is too low, does hh's budget avoid
   the SIGKILL?
2. **payoff** — in the configurations where the guess was too low, how many
   seconds did hh save?

## The arms

| arm | what it does |
|---|---|
| `naive` | the repo's own suite command under a guessed timeout `G`. On a kill it doubles `G` and **restarts from zero** — what a shell wrapper or an agent does without an estimate. |
| `hh` | the `run_tests` tool. First call: record pass (the naive command plus a ledger, no prediction). Later calls: the measured budget. |

`G` is swept over 1, 2, 5, 10, 20, 30 s, because `G` is the only variable that
matters: a suite of duration `D` is killed by naive whenever `G < D`, while hh's
budget does not depend on `G` at all.

Each repository is measured with its **real suite and its real runner**
(`pytest`, `unittest`, Mocha), not a synthetic fixture.

## Reading a result

- A naive cell with a `✗` contains the wasted attempt(s) *and* the restart.
- hh's warm wall is the same number in every column of a row — that is the
  point: the measurement replaces the guess.
- The record pass is reported separately; it is the one-time price.
- Where `G` was already high enough, hh saves nothing and pays its recording
  overhead. The report says so instead of averaging it away.
