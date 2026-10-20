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
  produced.

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

## 1.0.0 — 2026-10-20

Duration-aware budgets with checkpointed execution, capture lanes and
time-saved receipts. Superseded by 2.0.0.
