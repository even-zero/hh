# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [1.0.0] — 2026-10-16

First public release, under the name **hh**. Naming history: the internal
research kit was *TDA (Test-Duration Advisor)*, then briefly *hyper8*, now *hh*.
The agent-facing contract is considered stable from here on.

### Added

- **`run_tests` tool** (`hh/tool.py`): plan → checkpointed execute → record,
  one call. Refuses up front (`budget_too_small`, `deadline_infeasible`) instead
  of spending a caller's deadline on a run it cannot finish.
- **Time-saved receipts** (`hh/savings.py`): per-run and per-job utility
  reporting with four ledger-backed credit kinds (`continuation`, `resume`,
  `hang_bound`, `refusal`); unbounded counterfactuals are noted, never summed.
  Surfaces: tool response, `hh run`, `hh savings`, `hh report`.
- **Deadline-aware worker selection** (`policy.choose_workers`): smallest worker
  count that fits a CI deadline; imbalance stop when the marginal LPT gain drops
  below 5 %; default cap = CPU count.
- **Capture layer** for unittest, pytest, Jest, Mocha, node:test, Go
  (`go test -json`, streaming + parser), JUnit Platform (ServiceLoader listener +
  Surefire XML), plus a JUnit-XML/TRX parser family (Surefire, TestNG, PHPUnit,
  RSpec, GoogleTest, Catch2, cargo-nextest, mocha-junit, pytest-xml, .NET,
  VSTest). 13 recorded fixtures + 13 live checks.
- **Resume** (`resume=true` / `--resume`): finish the last run's remainder;
  already-paid tests are not re-executed (offset-aware ingest, no double-counting).
- **Test suite** (`tests/`, 40 tests, stdlib only, < 1 s) and **CI**:
  tests on Python 3.10–3.13, wheel build + clean-venv smoke, live adapter job.
- **Release automation**: `v*` tag → verified version, tests, sdist + wheel,
  clean-venv smoke test, GitHub Release with artifacts; opt-in PyPI publish.

### Measured (sandbox lab, see RESULTS.md for method and caveats)

- Tool overhead 0.9–1.3 % of suite runtime (gate 2 %); 24.9 ms on tiny suites
  (auto-degrade to record-only).
- Estimator: cold 56 % error → 4.2 % MAPE after one run; upper coverage 100 %.
- Restart waste: naive 14.0 s / give-up on hangs (105.1 s) vs 0 for hh;
  3 × 600 s hangs handled with a 8.6 s steady-state run.
- Predicted vs actual wall within 2.8 % across worker counts; one avoided restart
  pays for ~270 runs of the tool.
- Live A/B with an agent in the loop: 41.2 s / 15.0 s discarded / 2 blind guesses
  (naive) vs 26.8 s / 0 / 0 (hh).
