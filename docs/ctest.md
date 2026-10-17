# CTest (`--runner ctest`)

```bash
hh plan --root build --runner ctest --out plan.json
hh run  --plan plan.json --root build
```

Unit of measurement: **one test**. Works for plain `add_test` targets, GoogleTest
via `gtest_discover_tests`, Catch2 via `catch_discover_tests` — CTest is the
runner, the framework underneath does not matter.

## What hh needs

- CMake **3.14+** for `ctest --show-only=json-v1`, and a configured build tree
  (`cmake -S . -B build`). `HH_CTEST` overrides the binary.
- `--root` may be the build directory (the one with `CTestTestfile.cmake`) or
  the source directory: hh looks for exactly one build tree below it (`build/`,
  `cmake-build-debug/`, …) and refuses to guess when there are several.

## How it works

- **Discovery** runs `ctest --show-only=json-v1` and keeps the test names,
  leaving out tests with the `DISABLED` property — CTest never runs those.
- **Execution** runs `ctest -R '^(name1|name2)$' --output-on-failure
  --no-tests=ignore` in the build directory and reads the numbered summary line
  CTest prints as each test finishes:

  ```
  1/4 Test #1: ok_a .............................   Passed    0.00 sec
  ```

  That line streams, so a SIGKILL keeps every finished test. `***Failed`,
  `***Timeout`, `***Exception` and `***Not Run` are all recorded as `failed`;
  `Skipped` and `Not Run (Disabled)` as `skipped`.

## Identity

`ctest::ok_a` — the test's name as CTest knows it.

## Resume granularity: the test

CTest runs what `-R` selects, and the regex is built from the ids that are still
missing, so a resume runs exactly those tests.

## Gotchas

- **CTest has its own regex engine** (KWSys), not PCRE: no `(?:…)`, no `\d`. hh
  builds a plain `^(a|b)$` group with the names escaped, and treats
  `RegularExpression::compile()` in the output as an error — with
  `--no-tests=ignore` CTest otherwise exits 0 for an invalid filter, which would
  look like "nothing left to do".
- **`--no-tests=ignore`** keeps an empty selection (everything already done) a
  success; without it CTest exits 8 and hh would report a failed attempt.
- **Several build trees** under one source directory is an error, not a coin
  flip: pass `--root` with the build tree you want timed.
- **Test parallelism is CTest's.** hh does not pass `-j`; set `CTEST_PARALLEL_LEVEL`
  in the environment if the project relies on it.
- **Fixtures and setup tests** (`FIXTURES_SETUP`) appear as tests and are timed
  like any other; a fixture that fails is recorded as a failure, and the tests
  that depend on it usually fail too.
