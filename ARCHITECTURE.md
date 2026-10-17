# hh — architecture

One product, one number: **the timeout a test suite needs**.

## 1. The failure it removes

A caller (agent, CI job, shell script) runs `pytest` under a timeout it guessed.
If the guess is below the suite's runtime the process is SIGKILLed, every
finished test is discarded, and the caller guesses again and starts over. hh
measures the suite once and then supplies a budget derived from that
measurement, so the guess disappears from the second run onwards.

## 2. Data flow

```
                ┌──────────────┐      ┌───────────────┐     ┌──────────────┐
run_tests() ──► │ collect      │ ───► │ estimate      │ ──► │ execute      │
                │ (discovery)  │      │ (store rows)  │     │ (1 process)  │
                └──────────────┘      └───────────────┘     └──────┬───────┘
                       ▲                                          │
                       │                                    ledger rows
                       │                                          ▼
                  manifest cache                          ┌──────────────┐
                  (.hh/manifest-*.json)                   │ store        │
                                                          │ (SQLite)     │
                                                          └──────────────┘
```

A **record pass** (empty store) skips discovery and estimation: one shard with
`full_suite: true`, so the runner discovers and executes in a single process —
the naive command, plus ledger rows.

## 3. The three numbers

| number | source | used for |
|---|---|---|
| `p50` | sum of per-test point estimates | `predicted_wall_s` (warm) |
| `p90` | lognormal convolution of the same | **the budget** |
| `p99` | same, for reporting | — |

Point estimate for a test, in priority order:

1. its own rows — `0.5*median + 0.5*EWMA`, sigma from MAD of log-ratios;
2. its file's mean — sigma 0.6 (an extrapolation);
3. the suite mean — sigma 0.6;
4. nothing — the store is empty, so there is no plan to make (record instead).

`budget = max(3 s, p90 x 1.25)`, capped at `4 x p50 + 30 s`.

## 4. Execution and the only safety net

One shard, one test list (or `full_suite`), one budget. The executor starts one
runner process under `budget + measured_startup`, where startup is the gap
between process start and the first ledger row.

If that process is killed at the budget:

- the in-flight test is written as a right-censored row (a lower bound);
- the next budget is scaled from the observed per-test rate;
- only the unfinished tests are re-run, in a new process, from the same ledger.

At most 3 attempts. This is what makes a wrong estimate cost one budget rather
than the whole suite — and it is the only recovery mechanism in the design.
There is no sharding, no worker pool, no hang quarantine, no escalation ladder.

## 5. Identity

A test's key is `runner::file::class.name` (see `executor/keys.py`), with a
collision suffix for inherited/parametrised methods. For `junit` the class
segment is kept, because in Java the class is the file; for the Python runners
it is dropped so a class rename cannot break identity. The same normalisation is
used by discovery, the ledger plugin and the store, so a recorded row, a plan
entry and a re-run target all name the same test.

## 6. The store

`runs` (one row per pass), `observations` (one row per finished test),
`aggregates` (per-test / per-file / global p50, p90, mean, MAD, EWMA). Aggregates
are derived and therefore rebuildable; observations are append-only.

Censored rows (killed at a budget) are kept and counted as lower bounds.

## 7. Adapters

Which adapter serves which runner is one table:
`executor/adapters/registry.py`.

| runner | discovery | ledger |
|---|---|---|
| pytest | `--collect-only` in a child process | `pytest_plugin` (start/end rows, watchdog start rows) |
| unittest | import + enumerate (`unittest.defaultTestLoader`) | `unittest_stream.LedgerResult` |
| Mocha (`node`) | load files in a child, print `{key, raw_id}` | `js/mocha_stream.cjs` (run mode), one row per test |
| Jest, Vitest, `node:test`, tap | the runner's own file list, or its file shapes | `js/common.cjs`: one row per **file**, chunked |
| JUnit | JUnit Platform launcher over the compiled test classes | `HhJunit.java` (compiled once with `javac`, run per attempt) |

`docs/README.md` documents each runner in full, including what is deliberately
not supported and why; `docs/javascript.md` has the model the JS adapters share.

Every adapter skips units already present in the ledger, so a resumed run —
record pass or measured — never re-pays finished work. The unit differs: a test
for pytest/unittest/Mocha/JUnit, a whole file for the other four JS runners.

The JUnit adapter needs the project's test classpath and a JDK, and it uses the
same three fallbacks documented in `docs/junit.md` (`HH_JUNIT_CLASSPATH`, Maven,
a console-standalone jar). Its ledger rows and its key derivation are pinned to
`executor/keys.py` by a check in the adapter: if the Java driver and Python disagree
about a single id, discovery fails with that id in the message instead of
letting the next run silently fail to find its tests.

## 8. Non-goals (removed on purpose)

- **Cold prediction.** A store with no rows produces a record plan, never an
  estimate. There are no bundled priors and no sampling pass.
- **Parallelism.** The metric is "does the timeout kill the suite", not
  "how fast can it finish". One process, one budget.
- **Hang isolation.** A hang is a suite that exceeds its budget; the run is
  resumed and the censored row is recorded. Naming individual hanging tests is
  a different product.
- **A savings scoreboard.** The benchmark measures kills avoided and seconds
  saved in those configurations only; there is no self-reported utility.
