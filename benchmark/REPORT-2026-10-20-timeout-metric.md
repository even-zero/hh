# The only metric: does the timeout kill the suite?

**Date:** 2026-10-20 · **hh:** 2.0.0 (a207052) · **Repos:** 10 (node, pytest, unittest) · **Configurations:** 60 (10 repos x 6 guesses)

A caller must give a test suite a timeout. If the timeout is too low the
suite is SIGKILLed, the work is lost, and it has to run again. hh exists to
make that not happen. So the benchmark measures exactly two things:

1. **Kill-free.** When the caller's guess is too low, does hh's budget avoid
   the SIGKILL?
2. **Payoff.** When the guess was too low, how many seconds does hh save?

`naive` = the repo's own suite command under a guessed timeout G; on a kill
it doubles G and restarts from zero. `hh` = the `run_tests` tool: one record
pass (the naive command plus a ledger), then a measured budget that does not
depend on G at all. G is swept over 1 s, 2 s, 5 s, 10 s, 20 s, 30 s.

## 1. The metric

- **hh was SIGKILLed in 0 of 60 configurations** (0 kills in total).
- Naive was SIGKILLed in **20 of 60** (38 kills in total).
- Of those, naive **recovered** in 18: it spent **186.6 s** where hh spent **100.1 s** — **86.5 s saved** (median 4.55 s per configuration).
- In the other 2 it **never finished**: four guesses in a row were too low, so it burned 45.1 s and returned no result. hh finished those same configurations in 44.4 s. There is no saving to quote there — one arm produced a suite result and the other did not.
- One-time price: hh's first call is one more full run of the suite — **44.5 s of test time across the 10 repos** (per repo in section 5). It is paid once per repository, never per call.
- The remaining 40 configurations are ones where naive's guess was already high enough; there is nothing to save, and hh's ledger adds a little overhead instead — stated, not hidden.

## 2. Per repository

The quoted naive column is the guess where naive lost the most time in that
repository (the one where hh's saving is largest); section 4 lists every killed
configuration.

| repo | runner | tests recorded | reference | hh budget | hh kills | naive kills | naive wall @killed G | hh wall @same G | saved |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| click | pytest | 2287 | 5.36 s | 3.20 s | 0 | 3/6 | 12.53 s | 4.99 s | **+7.54 s** |
| docutils | unittest | 460 | 2.42 s | 3.23 s | 0 | 2/6 | 5.36 s | 2.76 s | **+2.60 s** |
| express | node | 1261 | 2.64 s | 3.32 s | 0 | 2/6 | 6.02 s | 3.03 s | **+2.99 s** |
| itsdangerous | pytest | 297 | 0.89 s | 3.00 s | 0 | 0/6 | — | — | — |
| jinja | pytest | 911 | 2.23 s | 3.00 s | 0 | 2/6 | 5.22 s | 2.52 s | **+2.70 s** |
| markdown | unittest | 1129 | 0.61 s | 3.00 s | 0 | 0/6 | — | — | — |
| nock | node | 678 | 6.59 s | 5.78 s | 0 | 3/6 | 13.43 s | 5.18 s | **+8.25 s** |
| pygments | pytest | 5345 | 17.78 s | 20.20 s | 0 | 4/6 | 33.37 s | 22.20 s | **+11.17 s** |
| pyyaml | pytest | 1287 | 3.42 s | 3.87 s | 0 | 2/6 | 6.61 s | 3.72 s | **+2.89 s** |
| supertest | node | 137 | 2.52 s | 3.00 s | 0 | 2/6 | 5.56 s | 0.61 s | **+4.95 s** |

## 3. The sweep (seconds; ✗ marks a SIGKILL)

Each cell is the wall-clock cost of getting the suite to finish. A naive cell
that was killed contains the wasted attempt(s) *and* the restart. Four attempts
with no result mean naive gave up on that guess — section 4 says so instead of
quoting a saving.

| repo | G=1s | G=2s | G=5s | G=10s | G=20s | G=30s | hh |
|---|---|---|---|---|---|---|---|
| click | 12.5 ✗ | 11.3 ✗ | 10.7 ✗ | 5.7 | 6.8 | 5.6 | **5.0** |
| docutils | 5.4 ✗ | 4.3 ✗ | 2.3 | 2.3 | 2.3 | 2.3 | **2.8** |
| express | 6.0 ✗ | 5.4 ✗ | 3.3 | 3.3 | 3.3 | 3.5 | **3.0** |
| itsdangerous | 0.9 | 0.9 | 0.9 | 0.9 | 0.9 | 0.9 | **0.9** |
| jinja | 5.2 ✗ | 4.1 ✗ | 2.1 | 2.1 | 2.1 | 2.1 | **2.5** |
| markdown | 0.6 | 0.7 | 0.6 | 0.6 | 0.6 | 0.6 | **0.6** |
| nock | 13.4 ✗ | 12.5 ✗ | 11.5 ✗ | 6.5 | 6.5 | 6.6 | **5.2** |
| pygments | 15.0 ✗ | 30.0 ✗ | 33.4 ✗ | 28.4 ✗ | 18.2 | 17.8 | **22.2** |
| pyyaml | 6.6 ✗ | 5.5 ✗ | 3.5 | 3.4 | 3.4 | 3.5 | **3.7** |
| supertest | 5.6 ✗ | 4.8 ✗ | 2.8 | 2.6 | 2.8 | 2.9 | **0.6** |

## 4. Every killed configuration, side by side

| repo | G | naive attempts | naive wall | hh attempts | hh wall | saved |
|---|---:|---:|---:|---:|---:|---:|
| click | 1 s | 4 | 12.53 s | 1 | 4.99 s | +7.54 s |
| click | 2 s | 3 | 11.34 s | 1 | 4.99 s | +6.35 s |
| click | 5 s | 2 | 10.67 s | 1 | 4.99 s | +5.68 s |
| docutils | 1 s | 3 | 5.36 s | 1 | 2.76 s | +2.60 s |
| docutils | 2 s | 2 | 4.35 s | 1 | 2.76 s | +1.59 s |
| express | 1 s | 3 | 6.02 s | 1 | 3.03 s | +2.99 s |
| express | 2 s | 2 | 5.42 s | 1 | 3.03 s | +2.40 s |
| jinja | 1 s | 3 | 5.22 s | 1 | 2.52 s | +2.70 s |
| jinja | 2 s | 2 | 4.13 s | 1 | 2.52 s | +1.62 s |
| nock | 1 s | 4 | 13.43 s | 1 | 5.18 s | +8.25 s |
| nock | 2 s | 3 | 12.49 s | 1 | 5.18 s | +7.31 s |
| nock | 5 s | 2 | 11.48 s | 1 | 5.18 s | +6.31 s |
| pygments | 1 s | 4 | 15.03 s | 1 | 22.20 s | naive gave up |
| pygments | 2 s | 4 | 30.04 s | 1 | 22.20 s | naive gave up |
| pygments | 5 s | 3 | 33.37 s | 1 | 22.20 s | +11.17 s |
| pygments | 10 s | 2 | 28.40 s | 1 | 22.20 s | +6.20 s |
| pyyaml | 1 s | 3 | 6.61 s | 1 | 3.72 s | +2.89 s |
| pyyaml | 2 s | 2 | 5.48 s | 1 | 3.72 s | +1.76 s |
| supertest | 1 s | 3 | 5.56 s | 1 | 0.61 s | +4.95 s |
| supertest | 2 s | 2 | 4.76 s | 1 | 0.61 s | +4.15 s |

## 5. What hh measured, and what it chose

`p50`/`p90` describe the suite's **total test time**: the sum over tests, each
with its own measured distribution from the record pass. `budget` is the
per-attempt kill threshold hh picks from them: `p90 x 1.25`, floored at 3 s. It
is not a prediction of the wall clock — it only has to outlast the slowest test
the runner may be inside when the budget runs out, so that a kill never lands on
unfinished work. `warm wall` is what the caller actually waited, collection and
ledger included.

The budget is compared against the run *between two ledger writes*, not against
the suite's wall clock: the executor adds the measured startup (interpreter +
collection) to it, so a 3.2 s budget next to a 5.4 s suite is not a contradiction
- it is 3.2 s of test execution after ~2 s of collection. Small suites hit the
3 s floor, which is why their budgets are all 3.00 s.

| repo | reference (naive command) | record pass | p50 | p90 | budget | hh warm wall |
|---|---:|---:|---:|---:|---:|---:|
| click | 5.36 s | 5.75 s | 2.486 s | 2.560 s | 3.20 s | 4.99 s |
| docutils | 2.42 s | 2.68 s | 2.467 s | 2.587 s | 3.23 s | 2.76 s |
| express | 2.64 s | 3.06 s | 2.579 s | 2.656 s | 3.32 s | 3.03 s |
| itsdangerous | 0.89 s | 0.91 s | 0.515 s | 0.530 s | 3.00 s | 0.95 s |
| jinja | 2.23 s | 2.37 s | 1.560 s | 1.626 s | 3.00 s | 2.52 s |
| markdown | 0.61 s | 0.68 s | 0.500 s | 0.511 s | 3.00 s | 0.65 s |
| nock | 6.59 s | 5.15 s | 4.366 s | 4.627 s | 5.78 s | 5.18 s |
| pygments | 17.78 s | 18.72 s | 15.419 s | 16.158 s | 20.20 s | 22.20 s |
| pyyaml | 3.42 s | 3.54 s | 2.867 s | 3.095 s | 3.87 s | 3.72 s |
| supertest | 2.52 s | 0.63 s | 0.310 s | 0.322 s | 3.00 s | 0.61 s |

## 6. How to read this honestly

- **hh does not make the tests faster.** Where G was already high enough, both
  arms run the suite once. On the Python repos hh's warm wall lands within
  -0.4 s to +0.4 s of the plain command. Click is slightly *faster*: the pytest
  adapter hands node ids to pytest, which then collects only the files that will
  run instead of the whole tree. The exception is pygments, at +4.4 s: 5,345
  ids are over the adapter's command-line cap, so its warm pass collects the
  tree and deselects. It is still 11.2 s and 6.2 s ahead of naive at G=5 and
  G=10, but the overhead is a cost, not something to hide.
- **These suites are short** (0.5-18 s), so only the low guesses kill. On a
  slower machine or a larger suite every number scales with the suite: a guess
  below `D` kills, and hh's budget is derived from `D` itself. The sweep is the
  same experiment at six values of G.
- **The record pass is the honest price.** hh's first call is the naive command
  plus a ledger, so it costs one extra full run of the suite per repository
  (section 1) — that is what it takes to have measurements at all.
- **The node repos compare two launchers, not just two timeouts.** For express,
  nock and supertest the naive arm runs the repository's `npm test`, while hh
  drives the mocha binary directly. npm's own startup (1.5-2 s, visible as the
  gap between `reference` and `record pass` in section 5) lands entirely in the
  naive column. On supertest it is larger than the suite itself, which is why
  hh wins even in configurations where naive was not killed. Strip it out and
  the timeout story is the same as for the Python repos.
- **hh resumes rather than restarts** when its own budget is exceeded, so even
  a wrong hh budget loses one budget, not the suite. No hh configuration in
  this run needed it; the test suite pins that behaviour (see
  `tests/test_tool_e2e.py`).

## 7. Reproducing

```
python3 -m benchmark setup     # clone + install the repos in repos.json
python3 -m benchmark run       # measure; writes _work/results/timeout-metric.json
python3 -m benchmark report    # writes this file
```

The commit hh reports is the tree the run measured — the commit just before
the one that carries this file.

| repo | commit |
|---|---|
| click | `2247b35ea1` |
| docutils | `f2cf464d21` |
| express | `0dee2c52c7` |
| itsdangerous | `672971d66a` |
| jinja | `5ef70112a1` |
| markdown | `b41e0aaf11` |
| nock | `fc32d74b04` |
| pygments | `156d85fdd1` |
| pyyaml | `f5e4504a92` |
| supertest | `3634bddc24` |

