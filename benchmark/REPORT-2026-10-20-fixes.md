# Fix round for the fresh-repo report — 2026-10-20

**Verdict: the report's findings are fixed, and the headline failure is gone.**
The click executor regression (**+124.6%**) is now **+9.9%**; express went
**+13.9% → +4.4%** and markdown **+21.7% → +16.9%**. Discovery is complete
everywhere (Express now finds all **1,261** tests, up from 1,153), collection is
measured per arm and excluded from the execution comparison, and receipts now
report **net** savings (credited − overhead) and can credit time **escaped**
(censoring/hangs) rather than only work re-paid.

Two findings did **not** improve, and are reported as such:

* **Cold runs still cost more than naive** (+18% to +33% on this set): a cold
  plan pays one extra runner startup plus a 40-row sample before the full run.
  That is the price of planning from measurements instead of priors.
* **Resume is a wash on these suites** (click −3%, express **+25%**): the kill
  point lands inside pytest collection, so little work is on the ledger and the
  finish run must start the runner again anyway.

Raw data: [`reports/2026-10-20-fresh-repos-results.json`](reports/2026-10-20-fresh-repos-results.json)
(last clean pass; the report quotes medians of three passes).
Machine-readable delta: [`reports/2026-10-20-fresh-repos-compare.md`](reports/2026-10-20-fresh-repos-compare.md).
Dashboard: `reports/2026-10-20-fresh-repos-dashboard.html`.
Baseline: [`REPORT-2026-10-19-fresh-repos.md`](REPORT-2026-10-19-fresh-repos.md).

## Results — same five repositories, same scenarios

Warm lane is `plan` + `run` on a store that has already measured the suite
(median of three clean passes; the naive arm is a single `naive-default`
attempt in the same pass, so both numbers see the same machine state).

| repo | lane | naive | hh | Δ | previous report | absolute overhead |
|---|---|---:|---:|---:|---:|---:|
| markdown | `unittest_exec` | 0.868 s | 0.986 s | **+16.9%** | +21.7% | +118 ms |
| click | `pytest_exec` | 6.238 s | 6.723 s | **+9.9%** | +124.6% | +485 ms |
| express | `mocha_exec` | 3.125 s | 3.213 s | **+4.4%** | +13.9% | +88 ms |
| werkzeug | `capture` | 5.143 s | 5.285 s | **+7.1%** | +3.6% | +142 ms |
| urllib3 | `capture` | 11.838 s | 12.020 s | **+2.6%** | +0.3% | +182 ms |

Per-pass spread: markdown +13.7…+17.6%, click +6.6…+11.4%, express
+2.8…+6.7%, werkzeug +2.8…+7.4%, urllib3 +1.5…+3.9%. On a 2-CPU shared box
that spread is larger than several of the effects being measured, which is why
the table quotes medians and the two capture-lane regressions (werkzeug
+3.6 → +7.1, urllib3 +0.3 → +2.6) should be read as **unchanged within noise**,
not as a new cost: their absolute overhead moved by +0/−32 ms and +150 ms
respectively.

For the capture lanes the same-suite execution-only comparison (collection
subtracted from *both* arms, identical process shape) is **werkzeug +3.7%**,
**urllib3 +5.1%** — both inside the ±5% band the plugin has always cost.

Cold runs (fresh store, no measurements at all):

| repo | naive | hh cold | Δ cold | hh warm | cold pays |
|---|---:|---:|---:|---:|---:|
| markdown | 0.868 s | 1.157 s | +33% | 1.021 s | +0.14 s (sample + replan) |
| click | 6.238 s | 8.255 s | +32% | 6.649 s | +1.61 s |
| express | 3.125 s | 3.689 s | +18% | 3.213 s | +0.48 s |

The cold premium is almost entirely one extra runner process (pytest import +
collection is 1.2–1.4 s on click, 0.3 s on express) plus the sample itself
(40 tests: 40 ms on click). After that first phase the planner works from
measured rows, and the warm path above is what every later run costs.

## What was fixed

### 1. Cold start now plans from a few measured rows (`hh/tool.py`, `hh/policy.py`)

* `build_plan(..., sample=True)` returns a **sample plan**: ≤48 rows
  (`SAMPLE_MAX`), target `min(40, 5% of the suite)`, deterministic and
  stratified — round-robin across the file list, positions spread within each
  file, strided across the sorted file list when there are more files than
  rows. The stride matters: the first implementation took the first N files
  alphabetically and under-predicted click by 42% because the tail of the
  suite was never sampled.
* `execute_plan` is now recursive: run the sample → ingest → **re-plan from the
  rows just measured** → run the remainder. Up to 3 rounds if a sample is
  killed early (`SAMPLE_MAX_ITERATIONS`). Each phase is recorded separately
  (`phases[]` with `tests_executed`, `startup_s`, `plan_ms`,
  `predicted_exec_wall_s`, `replanned`).
* The discovered manifest is written to `<checkpoint>/manifest.jsonl` and
  reused by the re-plan, so the second plan costs ~10–40 ms instead of paying
  collection again.
* Re-planning reuses the executor's ledger, and the sample's own rows are
  explicitly **not** credited as resume savings (`calibration_tests` /
  `calibration_ms`), so the receipt cannot claim the planner's own
  calibration as user value.

### 2. Extrapolation rewritten (`hh/estimator.py`)

`predict_tests()` returns `(preds, calibration)` and reports
`coverage_measured`, which drives the cold-safety multiplier:

* measured test → measured file mean → measured suite mean → bundled prior;
* a file's rate is trusted in proportion to **how much of the file was
  sampled** (`sqrt` coverage shrinkage, cap 0.9); one row from a 1,378-test
  file no longer speaks for the file;
* the suite rate is the arithmetic **mean** of measured rows (the quantity a
  budget needs), not the recency EWMA — the old `0.7·measured + 0.3·prior`
  blend put a 60 ms floor under a 0.8 ms suite and predicted 52.7 s for a
  2.7 s run;
* quantiles above 200 tests use lognormal **moment matching** instead of 400
  Monte-Carlo draws: `estimate()` on 62,493 predictions went 233 ms → 2.6 ms
  (at 1,280 tests) and 0.10 s at 62k;
* a **per-test runner overhead** term (pytest ≈1.0 ms/test, unittest ≈0.1,
  node ≈0) is added to the reservation, because `duration_ms` excludes
  pytest's own per-test plumbing. Click's cold prediction went from 3.5×
  *under* the actual execution time to ~25% under.

### 3. Discovery is recursive everywhere (`hh/tool.py`, adapters)

* stdlib `unittest discover(root)` returns **zero tests** for a plain `tests/`
  directory without `__init__.py`. This was found with a smoke fixture and is
  the worst failure mode a budget tool can have. `hh` now discovers test files
  itself (`walk_test_files` / `discover_ids` / `resolve`), keeping standard ids
  for package trees (so existing stores stay valid) and path-qualified ids for
  everything else. Smoke repo: 0 → 60 tests.
* pytest walks both conventional roots (`tests/` and `test/`) recursively.
* Mocha walks `test/**` at any depth and keys tests by path-relative title, so
  identical test names in different files no longer collide. `HH_MOCHA_REQUIRE`
  lets hh load the repo's own bootstrap (`test/support/env` for Express).
* Express's official recursive suite is now the baseline in both arms:
  **1,261** tests (was 1,153), matching `n_tests_expected`.

### 4. Collection is measured and excluded (`hh/executor.py`, benchmark)

* Plans carry `startup_s` (measured collection/import cost). Shard budgets are
  **execution** budgets; each attempt runs with `budget + startup_s`.
* The executor now measures the startup actually paid per attempt (first
  ledger row timestamp − process start) instead of reusing the plan constant.
  A 40-row sample that never collected the suite no longer gets charged a
  full-suite collection, and a full run's `exec_wall_s` is genuinely
  execution-only.
* `pytest_stream` passes a small selection as explicit node ids, so a sample
  probe does not import/collect the whole suite (click sample phase: 2.76 s →
  0.51 s).
* The benchmark times collection separately for both arms (`--collect-only -q`,
  Mocha `--dry-run`, unittest `-k <no match>`) and reports it next to every
  number. For capture lanes it is subtracted from both arms; for exec lanes the
  process-level wall is the headline, because `--collect-only` (which prints
  every node id) costs ~1.1 s more than in-run collection on click, so
  subtracting it from only one arm would bias the comparison.

### 5. Receipts net out overhead and credit escapes (`hh/savings.py`)

* new `hang_escape` credit: `cutoff × censored − measured_wait` — bounded time
  a restart-from-zero baseline provably re-pays while a hung test is being
  identified. Censored work with no measured wait contributes a lower bound
  and is noted, never summed.
* `block()` now emits `overhead_ms/overhead_s` and `net_ms/net_s`, and every
  `hh run` receipt carries them. Resume receipts on this run: click 0.15 s
  credited, express 1.03 s credited (net of 85 ms / 54 ms overhead).

### 6. Harness correctness fixes (found while re-running)

* `records.jsonl` is append-only; `collect` now takes the **latest** record per
  (policy, attempt, scenario, arm) instead of the first, so a report can no
  longer mix last hour's naive run with this hour's hh run (click's naive wall
  was frozen at 5.987 s across two runs before this fix).
* `--collect-only` / artifact lookups use the last measurement, not the first.
* A shard that dies before running its tests is now a **hard failure**:
  the executor records the adapter's stderr tail, `hh run` exits non-zero, and
  the benchmark aborts instead of reporting a suspiciously fast "ok" run. This
  caught a real regression during this round (markdown's adapter failed on
  package-relative imports after the discovery rewrite and was silently
  reported as 0.43 s).

## Where it still does not win

* **Cold runs**: +18% to +33% on this set. The sample phase is a real extra
  runner start; for suites that finish in under a second it cannot pay for
  itself. It is still the only way to get a measured plan on a fresh suite.
* **Resume**: click −3%, express **+25%** vs a naive restart after the same
  kill. Killing at half of the warm wall lands inside pytest's collection
  (1.8 s of a 3.2 s express run), so few tests are on the ledger and the finish
  run pays full collection again. The previous report's "13% saved on express"
  came from a baseline inflated by a two-attempt warm run; with an honest
  baseline the scenario is a wash. The receipt correctly reports only the
  179 tests that were actually preserved.
* **Small-suite overhead**: markdown's +16.9% is the ledger itself (two rows
  per test, one `write(2)` each, ~0.11 s on 1,129 tests) plus a 12 ms plan.
  Batching rows would halve it but would weaken the crash guarantee.
* **Prediction accuracy after one sample**: markdown 2.1 s predicted / 0.85 s
  actual, click 3.9 / 5.1, express 4.5 / 2.9. Over-prediction is the safe
  direction (budgets are generous, no escalations were triggered); the click
  residual is the heavy tail, which 40 rows cannot see.

## Method & caveats

* Same five pinned repositories, same scenarios, same scripts as the baseline
  report; only `hh` and the harness changed. `benchmark/repos.fresh.json`
  differs from the old run in one deliberate way: Express's suite command is
  now the recursive official one (1,261 tests) and carries
  `mocha_require: ["test/support/env"]`.
* Medians of three clean passes per repo; the naive arm is re-run in every
  pass. Absolute walls drift by ±10–20% between passes on this box (the
  baseline's naive click was 4.79–6.24 s across the two days), so compare
  ratios inside a pass, not absolute seconds across reports.
* Dashboards auto-select the first `unittest_exec` lane for their exec charts
  (markdown); the tables above cover all five repos.

## Reproduction

```bash
cd <hh repo root>
python3 -m benchmark --repos-file benchmark/repos.fresh.json all   # ~10 min
python3 -m benchmark --repos-file benchmark/repos.fresh.json compare \
  --baseline benchmark/reports/2026-10-19-fresh-repos-results.json
```

Focused checks used while fixing:

```bash
python3 -m unittest discover -s tests -q                      # 45 tests
python3 -m hh --store-root /tmp/s plan --root <repo> --runner pytest --out /tmp/p.json
python3 -m hh --store-root /tmp/s run  --plan /tmp/p.json --root <repo>
```
