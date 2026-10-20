# Changelog

## 2.3.0 — 2026-10-20

**Added**
- `jest`, `vitest`, `node-test` and `tap` runners, so the five common JavaScript
  suites are all supported: `node` (Mocha), `jest`, `vitest`, `node-test`, `tap`.
  Each has a page under `docs/`, and the model they share — file granularity for
  four of them, chunked runs, one ledger row per file — is in
  `docs/javascript.md`.
- `executor/adapters/registry.py`: one table that says which adapter serves
  which runner. The CLI, the tool spec and the executor all read it, so adding a
  runner is one line plus its docs page.
- `tests/test_js_adapters.py`: the shared adapter CLI driven by a fake runner
  (chunking, selection, "no verdict means failed"), plus real end-to-end runs of
  `node-test` where Node is installed and of jest/vitest/tap/mocha when a probe
  project is available. A `js (node 22)` CI job installs those runners so they
  are proved against real output, not a fixture written to match the parser.
- `HH_JS_CHUNK` (default 25): files per runner process. The ledger is flushed
  after every chunk, so a SIGKILL loses at most the chunk in flight.

**Fixed**
- Almost-finished work is now resumed per runner: a repository already measured
  with one runner no longer makes another runner's first run a predicted one.
  Each runner's first run is still a record pass.
- File-level JS keys (`jest::src/a.test.js`) are their own file aggregate.
  Before, the last path segment was stripped, folding a whole directory into one
  "file" and feeding the estimator a meaningless fallback tier.
- A killed **record pass** now resumes: the JS adapters skip units that already
  have a verdict in the ledger, so the retry neither re-runs nor re-records
  finished files. Before, the whole suite ran again and every finished file got
  a second row, which the store counted twice.
- A crashing adapter no longer seals its ledger. `suite_end` is written only
  when a run really reaches the end of the suite, so a runner that dies (missing
  package, config error) is reported as "adapter exited before finishing the
  suite" instead of passing as a complete run of zero tests, and the reported
  error is the thrown message rather than the last line of the stack.
- Mocha adapter: accepts a relative `--root`, and its `collect` mode can write
  ids to a file like every other adapter.
- JS adapters ignore files outside the suite root. Jest and tap resolve their
  own project root, which can be above the root hh was given; a `../` key would
  have corrupted the store.

## 2.2.0 — 2026-10-20

**Changed**
- The Python package is now `executor/` (it was `hh/`). The **command is still
  `hh`** and the distribution is still `hh`, so nothing changes for a user of
  the CLI or of the `run_tests` tool; only `import hh` becomes `import executor`
  and `python -m hh` becomes `python -m executor`.
- `executor/executor.py` became `executor/runner.py`, so the module is not
  `executor.executor`.
- Runner documentation moved out of `README.md` into `docs/`: one page per
  runner, plus `docs/README.md` with the support matrix, the resume
  granularity of each adapter, the runners that are deliberately not supported
  and why, and the contract for adding one. The README keeps a table and links.
- The on-disk store is unchanged at `.hh/`, so stores recorded before the
  rename keep working with no migration.

## 2.1.0 — 2026-10-20

**Added**
- `junit` runner: Java and Kotlin suites on the JUnit Platform (JUnit 5, JUnit 4
  via Vintage) now get a record pass, a measured budget and resume-on-overrun
  like every other runner. A small driver (`executor/adapters/HhJunit.java`) is
  compiled once with `javac`; the project's test classpath is taken from
  `HH_JUNIT_CLASSPATH`, Maven, or a console-standalone jar.

**Fixed**
- Skipped tests and ids that no longer resolve (both 0 ms rows) are kept out of
  the duration aggregates. They used to drag the p90 down until a measured
  budget killed the suite — the failure the tool exists to prevent.
- Discovery child processes get hh's package directory on `PYTHONPATH`, so a
  source checkout works whatever the caller's cwd is.

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
