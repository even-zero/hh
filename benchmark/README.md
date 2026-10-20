# hh/benchmark — pinned, config-driven real-world benchmark

Replicates the published "hh in the wild" benchmark on command, and makes
adding a new repository a **config-only** change. Fix hh, re-run, `compare`
against a frozen baseline.

## Command

```bash
cd <hh repo root>
python3 -m benchmark all          # setup + run + collect + dashboard (~20 min)
python3 -m benchmark list         # what will be measured
python3 -m benchmark setup/run    # individual phases
python3 -m benchmark run -r tornado -s resume   # filters (repeatable)
python3 -m benchmark compare      # current results vs benchmark/baseline/
```

`--repos-file <path>` points the same commands at a different descriptor set,
e.g. the fresh 2026-10-19 repo set:

```bash
python3 -m benchmark --repos-file benchmark/repos.fresh.json all
```

Two fix rounds followed:

- `REPORT-2026-10-20-fixes.md` — recursive discovery, collection-excluded
  timing, net receipts (cold-start *sampling*, now removed).
- `REPORT-2026-10-20-cold-start.md` — the sampling/prior machinery is gone: a
  cold store is a **record pass** (naive invocation + ledger). Cold is judged on
  parity with the naive run of the same suite; warm lanes are unchanged.
- `REPORT-2026-10-20-warm-pass.md` — **read this one for the warm lanes**: what
  a warm pass costs stage by stage, an alternating A/B against the repo's own
  command (`benchmark/interleave.py`), and the budget probe
  (`benchmark/budget_probe.py`) that shows where a measured budget pays. Its
  numbers supersede the warm columns of the cold-start report.

`repos.fresh.json` is a self-contained five-repo set (markdown, click, express,
werkzeug, urllib3) used for the cross-repo comparison report
`REPORT-2026-10-19-fresh-repos.md`; the committed `repos.json` and frozen
baseline are unchanged. Do not use `compare` against the old baseline for this
set: its metric names embed the original repo ids and the suites are different.
The normalized comparison is `reports/2026-10-19-fresh-repos-compare.md`.

Outputs (all gitignored): `_work/results/results.json`,
`_work/results/dashboard.html` (open in a browser),
`_work/results/compare.md`, plus `_work/records.jsonl` (one row per executed
step), `_work/stores/*/.hh/` (attempt logs incl. `hh-full` rows and savings
receipts), and `_work/logs/*` (plans, run JSONs, ledgers, naive run logs).

## What it measures (scenarios, arms)

- **probe** — `hh discover` on the repo *as shipped*: manifest engagement vs the
  suite's real size (the compatibility gap), plus the cold plan's operating
  level (it must be `record`: no enumeration, no prediction).
- **clean** — naive default ladder (30s→60s→120s, escalate only on timeout)
  with a harness process-group SIGKILL, vs the hh lane: executor plan/run
  (cold + warm; unittest, pytest or Mocha suites) or a Tier-A capture run
  (pytest plugin / mocha reporter) audited through the ledger + `hh record`.
  The **cold** pass is a record plan — one full-suite process, no test list, no
  prediction — so its only honest success condition is parity with the naive
  run (`record.delta_pct ≈ 0`).
- **workers** — hh plan/run at W=2 against the warm store.
- **resume** — SIGKILL at `auto:0.5` (half the measured warm wall) then finish;
  naive re-runs from zero, hh `plan --resume`s from checkpoints.
- **hang** — synthetic, clearly-labelled infinite tests:
  unittest lane: shrug shim + naive doubling give-up vs hh censored runs on the
  primed store; capture lane: naive repeats vs ledger-preserving capture which
  names the hang (open interval: start without end), then a descoped green run.

The naive agent is scripted — no improvisation, budgets escalate only on
timeout (this is *the* default-policy reading of the attempt log), and its
runs are recorded into the same accounting stores (`HH_JOB=NaiveAgent`) the
scored meter reads, so waste is quantified the same way in both arms.

## Adding a repo

Copy an entry in `benchmark/repos.json` and edit it — no code changes.
`_template_*` is a disabled example with every field annotated.

Key fields:
- `clone.url` + `clone.ref` — pin the exact commit (replication means the
  exact same code measured).
- `lane`: `unittest_exec` (optional shim preserves the historical 1484-test
  Tornado execution universe; native `*_test.py` discovery works without it),
  `pytest_exec`, `mocha_exec`, or `capture` (`capture_kind`: `pytest_plugin` |
  `mocha_reporter`). Executor plans always select tests via a file, not argv.
- `suite_cmd` — the naive command (`{repo_dir}`, `{shim}` placeholders).
- `pythonpath` — dirs joined onto PYTHONPATH (hh root is always included).
- `setup` — extra shell cmds (`pip install`, `npm ci`, bundling`).
- `scenarios` — per-scenario config; omit to skip.
- `sitecustomize` — file in `benchmark/assets/` copied to the child env for
  ALL arms of this repo (used for tornado's own access-log silencing, mirroring
  `tornado/test/runtests.py:146`).

Universe sanity gate: `n_tests_expected` only warns when the measured universe
differs — every number in results.json is measured, never synthesized.

## Baselines & the fix→re-run loop

`benchmark/baseline/v1.0.0-2026-10-19.json` is the frozen v1.0.0 snapshot
(the original publication run). `python3 -m benchmark compare` flattens both
results, diffs ~20 curated metrics, and flags ✅ better / ⚠️ worse with
direction-of-goodness per metric (walls/hardware move ±20% silently; the
table marks ±5% as "same"). Store new baselines as new files under `baseline/`.

## Methodological rules baked in

1. Same ARM universe: naive and hh run the identical command/universe; the
   shim is methodology, not a suite patch.
2. Kills are harness process-group SIGKILLs (`os.killpg`) — mid-suite,
   disclosed cutoffs, no graceful shutdown.
3. Missing imports → empty shim module + recorded warning, never a silent
   skip that shrinks the universe.
4. Receipts are read from the SAME store that executed the run (receipts
   attribute by job id).
5. Invalidated measurements are deleted and re-measured, never patched.
6. Compare only like-for-like capture percentages: executor startups dominate
   subsecond suites and are reported separately, not as capture regressions.
   For a fresh all-scenario rerun, clear `_work/records.jsonl`, `logs/`,
   `stores/`, and `results/` first; the runner appends records by design.

## Layout

```
benchmark/
├── repos.json            # the whole experiment is this file
├── baseline/             # frozen results snapshots for compare
├── assets/               # per-repo env pieces (sitecustomize, …)
├── __main__.py           # CLI
├── config.py             # repos.json loading/validation/paths
├── shims.py              # shim suite + hang injection builders
├── harness.py            # kill_run (process-group SIGKILL), run+save, audits
├── scenarios.py          # probe / clean / workers / resume / hang
├── collect.py            # records+stores+receipts → results.json
├── dashboard.py          # results.json → dashboard.html
├── compare.py            # results vs baseline → compare.md
└── _work/ (gitignored)   # repos/, stores/, logs/, records.jsonl, results/
```
