# Changelog

## 2.0.0 — 2026-10-20

The product is now one number: the timeout a suite needs. Everything that
existed to serve other metrics was removed.

**Removed**
- cold-start sampling and the prior table (`PRIORS_MS` and the sample plan);
- sharding, worker selection, deadline feasibility, hang quarantine, the
  escalation ladder, per-test cutoffs;
- time-saved receipts and the savings ledger;
- capture-only adapters (Go, JUnit-XML, TRX, Jest reporter) and their fixtures;
- the multi-scenario benchmark (probe/clean/workers/resume/hang/capture),
  `compare`, `dashboard`, `interleave`, `budget_probe` and the reports they
  produced;
- the `experiments/` tree and its 13 conformance fixtures, the JUnit listener,
  the standalone Mocha reporter, and the unused `attempts` table and logger.

**Changed**
- `ExecutionPlan` is one shard with one budget: `p90 x 1.25` (3 s floor,
  `4 x p50 + 30 s` cap).
- The executor runs one runner process per attempt and resumes from the ledger
  on overrun; it reports `attempts`, `timed_out`, `wasted_s`.
- `run_tests` returns `budget` (`basis`, `budget_s`, `p50_s`, `p90_s`,
  `confidence`) instead of an estimate object, a deadline decision and a
  savings block.
- CLI is `plan | run | report | tool-spec`.
- New benchmark: ten repositories, mixed runners, one metric (SIGKILL avoided
  and seconds saved when the naive timeout was too low).

**Fixed**
- Mocha adapter exits as soon as the run is over, so suites that leave a handle
  open cannot be killed at the caller's timeout after all tests have finished.
- A pytest fallback no longer reuses a poisoned session: when direct node ids
  fail to collect (sample scripts, stale ids) the adapter re-runs in a fresh
  process that collects the tree and deselects the rest.
- unittest loader placeholders (`_FailedTest`, `ModuleSkipped`) never enter the
  manifest; ids an old manifest has but the runner cannot resolve are reported
  as `unresolved` instead of failing the run.
- The benchmark honours a repo `root` subdirectory (docutils) and per-repo
  environment (`PYTHONPATH`, pytest args) for both arms.
- The adapter child process puts hh's package directory on `PYTHONPATH`, so the
  tool works from a source checkout whatever the caller's cwd is.
- CI is one page: tests on 3.10 and 3.13, CLI smoke, and a wheel installed into
  a clean venv; the release workflow is a single tag-driven job.

## 1.0.0 — 2026-10-20

Duration-aware budgets with checkpointed execution, capture lanes and
time-saved receipts. Superseded by 2.0.0.
