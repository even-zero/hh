# Fresh-repo benchmark rerun — 2026-10-19

**Verdict: no marked improvement over the existing report.** Five repositories
that are *not* in `benchmark/repos.json` reproduce the same profile: full
discovery for flat suites, small-suite overhead far above the 2% gate, resume
wins only when the suite is long enough to amortize planning, and hang
identification that trades wall time for knowledge. There is **one material
win** (urllib3 capture: 0.3%, below the 2% gate) and **one material
regression** (click's pytest executor: 8.21 s warm vs 4.79 s naive; 10.75 s
plan+run, +124.6%).

Raw data: [`reports/2026-10-19-fresh-repos-results.json`](reports/2026-10-19-fresh-repos-results.json).
Normalized old-vs-new table: [`reports/2026-10-19-fresh-repos-compare.md`](reports/2026-10-19-fresh-repos-compare.md).
Dashboard: `reports/2026-10-19-fresh-repos-dashboard.html` (its exec charts
still select the first `unittest_exec` lane, so they show markdown only).

## What ran

Five new descriptors, pinned to exact commits, in
[`benchmark/repos.fresh.json`](repos.fresh.json):

| id | lane | pinned commit | measured universe | suite |
|---|---|---|---:|---|
| markdown | `unittest_exec` | `934c53e43774af0a24c566f4b484e4f46d2d2f0d` | 1,129 | `python -m unittest discover -s tests` |
| click | `pytest_exec` | `2247b35ea1c47c727d7a06e51fa280e12a863ff6` | 2,287 | `pytest tests` (upstream `-m 'not stress'` deselects 31,000) |
| express | `mocha_exec` | `0dee2c52c7c873498f8ed7d8ee4ddd0a04d3ada8` | 1,153 | Mocha `test/` (108 `test/acceptance/` tests excluded from both arms) |
| werkzeug | `capture` (pytest plugin) | `665e760ca6325a348ce03e5cf780b17e4be01848` | 994 | `pytest tests -m 'not dev_server'` |
| urllib3 | `capture` (pytest plugin) | `a37bc93b65d6bcf00d3dc43d966bab6bd130ecd8` | 2,048 | `pytest test --ignore=test/with_dummyserver` |

Environment: Python 3.11.2, Node 22.22.3, npm 10.9.8, 2 CPUs, Linux. hh 1.0.0,
current HEAD `0d33703`. The five original repositories were **not** re-run; the
committed 2026-10-19 report is the baseline.

Commands:

```bash
cd /home/user/hh
python3 -m benchmark --repos-file benchmark/repos.fresh.json setup
python3 -m benchmark --repos-file benchmark/repos.fresh.json run
python3 -m benchmark --repos-file benchmark/repos.fresh.json collect
python3 -m benchmark --repos-file benchmark/repos.fresh.json dashboard
```

`--repos-file` is a small addition to the benchmark CLI; the committed
`repos.json` and frozen baseline are untouched.

## Results

### Executor lanes

`plan+run` is the benchmark's existing exec-lane accounting: the separate
`hh plan` overhead plus the warm run wall, compared with the naive clean run.
`pred err` is `|predicted_wall_s − actual| / actual` (the field `compare.py`
uses); the planner's p50 is shown in parentheses where it differs materially.

| repo | tests | naive clean | hh cold | hh warm | warm self-overhead | plan+run | Δ vs naive | warm pred err | W2 wall (pred err) | resume naive → hh |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| markdown | 1,129 | 0.746 s | 0.885 s | 0.748 s | 189.5 ms | 0.907 s | **+21.7%** | 17.5% (p50 5.0%) | 0.730 s (19.3%) | n/a (suite < 2 s) |
| click | 2,287 | 4.786 s | 4.813 s | 8.210 s | 2,613 ms | 10.750 s | **+124.6%** | 74.9% (p50 72.3%) | 6.383 s (83.6%) | 8.64 → **8.73 s** (+1.0%) |
| express | 1,153 | 2.787 s | 2.744 s | 2.788 s | 421.6 ms | 3.175 s | **+13.9%** | 16.3% (p50 8.4%) | 1.939 s (36.5%) | 4.75 → **4.12 s** (−13.2%) |

`warm self-overhead` is hh's own meter. `execute_plan` adds the plan's measured
collection cost to it by construction, so click's 2,613 ms is mostly the
2,540 ms of pytest collection paid by the separate `hh plan` step, not work
inside the warm run. The wall columns are the wall-clock metrics.

- **markdown** warm wall is parity with naive (0.748 vs 0.746 s). The planner
  correctly auto-degrades the 0.75 s suite to `record-only`; the 21.7% is the
  separate plan step, not the run. W2 gives no meaningful speedup (0.730 s).
- **express** warm wall is also parity (2.788 vs 2.787 s); the plan step is the
  13.9%. W2 is a genuine parallel win (1.939 s, 30% faster than naive), though
  the W2 prediction is 36.5% low.
- **click** is the regression. The cold run is parity (4.813 s vs 4.786 s); the
  cold plan+run is +56% once the 2.67 s plan step is included. The warm run is
  8.21 s because the 3.15 s shard budget does not account for the ~2.4 s of
  pytest collection inside the executor subprocess; the separate plan step adds
  another 2.54 s. The executor kills a shard that has already completed 1,579
  tests, then pays for a second collection to finish the remaining 708.
  `plan+run` rises to 10.75 s (+124.6%). W2 still needs four attempts (6.38 s,
  +33% vs naive). Receipts credit 2.9 s of continuation work, but the net wall
  is worse.

### Capture lanes

| repo | tests | naive clean | capture | overhead | ledger rows | 3-run median |
|---|---:|---:|---:|---:|---:|---:|
| werkzeug | 994 | 4.200 s | 4.352 s | **+3.6%** | 1,003 | +7.8% (4.104 → 4.425 s) |
| urllib3 | 2,048 | 11.387 s | 11.420 s | **+0.3%** | 2,074 | +0.42% (10.810 → 10.856 s) |

- **urllib3 passes the 2% gate.** The single benchmark run is +0.3%; three
  alternating runs give +0.42%; against the immediately preceding naive
  reference run (11.23 s) it is +1.7%, still below 2%. That is the one clear
  positive result in this rerun. It is **not** evidence of a code improvement:
  packaging's 13.0% was 62,493 tests at ~62 µs of ledger cost per test;
  urllib3 is 2,048 tests at ~16 µs per test.
- **werkzeug misses the gate** at +3.6% on the benchmark's first-naive
  comparison, +11.3% against the reference run, and +7.8% by median. It is a
  4.2 s suite with 994 tests, so fixed plugin/startup cost dominates. The
  small-suite caveat in the existing report still applies.

### Hang (werkzeug capture)

| arm | attempts | total wall | result |
|---|---|---:|---|
| naive doubling | 30 s + 60 s | 90.0 s | two blank timeouts, no culprit, no results |
| hh capture | 30 s + 60 s + 3.84 s descope | 93.9 s | `tests/test_zzhang.py::test_never_finishes` named; descope run green |

The first 30 s capture attempt already had 994 end rows and one open interval
naming the hang; the 60 s attempt confirmed it; the `-k "not never_finishes"`
run finished in 3.84 s. The capture arm is **4.3% slower than naive in wall
time**, but it produces the answer and a green run while naive produces
nothing. A policy that descopes as soon as an open interval is named could
finish in ~34 s (30 + 3.84), well below naive's 90 s; the current fixed
30/60/120 ladder does not take that shortcut.

## Findings

1. **The 2% gate is achievable but not general.** urllib3 capture (0.3–0.4%)
   passes; werkzeug capture (3.6–7.8%) fails. The difference is suite duration
   and per-test ledger cost, not a new capture implementation. Do not claim the
   E1 gate is now met across repos.
2. **Click exposes a real pytest budgeting bug.** The warm plan's p50 is 2.275 s
   and shard budget 3.15 s, but the executor's pytest subprocess also pays
   ~2.4–2.7 s of collection for 33k collected / 31k deselected items. The
   budget therefore expires while tests are still running, and the resume pays
   for collection again. Result: 8.21 s warm vs 4.79 s naive; 10.75 s plan+run;
   +124.6%. This is the most actionable finding. Fix directions: budget
   collection/startup separately, cache the collected set for execution, or
   auto-degrade to record-only when collection is a large fraction of the
   suite wall.
3. **Resume does not automatically pay off on small/collection-heavy suites.**
   Express saves 13.2% (4.12 vs 4.75 s); click is 1.0% *worse* (8.73 vs 8.64 s)
   because the resume plan is computed from cold priors (`coverage 0.0`; the
   killed run was not ingested) and pays 2.33 s of collection. Tornado's 27.7%
   win in the existing report remains the best case, not the norm.
4. **Warm prediction is worse on the new set.** `predicted_wall_s` errors are
   17.5% (markdown), 16.3% (express), and 74.9% (click); W2 errors are
   19.3%, 36.5%, and 83.6%. Using the planner's p50 narrows markdown to 5.0%
   and express to 8.4%, but click remains 72.3% off because of the escalation.
   The existing report's 1.0% warm / 5.5% W2 tornado numbers were the
   long-suite best case; the old tiny suites were already 36–80% off.
5. **Discovery is complete only for flat layouts.** markdown, click, and the
   measured express universe are 100%. Express's official suite includes 108
   tests under `test/acceptance/`; hh's Mocha adapter only loads files directly
   in `test/`, so those tests are invisible. They were removed from the naive
   arm too, keeping the measured comparison fair, but the "discovery gap
   closed" claim does not generalize to nested Node suites. For the capture
   repos, probe counts (1,019 and 2,808) exceed clean counts (994 and 2,048)
   because of `-m`/`--ignore` selections, not missed discovery.
6. **Receipts are not net savings.** Click's clean job credits 2.9 s of
   continuation work while its full cost is +124.6% vs naive; click's resume
   credits 2.1 s while total wall is 1.0% worse. Express's resume receipt
   (1.6 s) overstates its 0.63 s net win because planning/startup is not
   subtracted. Read receipts as ledger-backed work not re-paid, not as
   end-to-end time saved.
7. **Hang identification generalizes.** Werkzeug's injected hang is named from
   the ledger's open interval, same as packaging; the new result additionally
   shows it is named on the first 30 s attempt. The wall cost of the current
   ladder (93.9 s vs naive 90 s) is not a win on a 3.8 s suite; the information
   is.

## Caveats

- **Not a controlled A/B.** Different repositories have different suite sizes,
  test durations, and framework overheads. Percentages are descriptive; the
  only fair like-for-like statements are within a repo across arms.
- **Single benchmark run per arm.** The capture medians above come from three
  additional alternating naive/capture runs and are the better estimate of
  noise. The benchmark's naive arm runs first, so filesystem-cache warmth
  favors the hh arm; the alternating medians mitigate this.
- **Selection choices.** Werkzeug requires `-m 'not dev_server'` (25 tests
  deselected) because the full suite blocks in this sandbox; urllib3 requires
  `--ignore=test/with_dummyserver` (760 tests, 52 TLS/proxy failures otherwise);
  click's 31,000 stress tests are upstream-deselected via `addopts`; express
  `test/acceptance/` is excluded from both arms because hh cannot see it. These
  choices are recorded in each descriptor.
- **Install isolation.** Werkzeug is installed non-editable (markupsafe + test
  deps) so its development tree does not shadow the released Werkzeug that
  Quart/Flask need in the urllib3 capture environment. A full `run` initially
  aborted at the urllib3 probe for that reason; the descriptor now avoids it.
- **Baseline commit.** The existing report's meta records hh `287970c`; this
  run is on `0d33703`. The checkout is shallow, so a source diff between the
  two is not available here. The comparison assumes benchmark-relevant
  behavior is unchanged.

## Reproduction

```bash
cd /home/user/hh
# clean state (optional; the runner appends records by design)
rm -rf benchmark/_work
python3 -m benchmark --repos-file benchmark/repos.fresh.json setup
python3 -m benchmark --repos-file benchmark/repos.fresh.json run
python3 -m benchmark --repos-file benchmark/repos.fresh.json collect
python3 -m benchmark --repos-file benchmark/repos.fresh.json dashboard
```

The full run takes roughly 12 minutes on 2 CPUs: setup ~1 min, exec scenarios
~3 min, capture ~1 min, werkzeug hang ~5 min, urllib3 capture ~30 s.

_Generated 2026-10-19. Raw results: `reports/2026-10-19-fresh-repos-results.json`.
Old-vs-new normalized table: `reports/2026-10-19-fresh-repos-compare.md`._
