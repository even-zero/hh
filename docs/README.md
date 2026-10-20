# Runners

hh runs a test suite under a budget, so it needs four things from a runner:

1. **enumerate** the tests without executing them (the manifest);
2. **execute** a named subset (so a resume never re-pays a finished test);
3. **report** per-test results while the suite is running, so a SIGKILL keeps
   everything that finished;
4. a **stable id** per test, so the store can recognise it next time.

That is the whole adapter contract. Anything that can do all four is
supportable; anything that cannot is listed under "not supported" with the
reason, rather than pretended.

## Supported

| `--runner` | language | frameworks | doc |
|---|---|---|---|
| `pytest` | Python | pytest | [pytest.md](pytest.md) |
| `unittest` | Python | stdlib `unittest`, `TestCase` trees | [unittest.md](unittest.md) |
| `node` | JavaScript | Mocha | [mocha.md](mocha.md) |
| `junit` | Java, Kotlin | anything on the JUnit Platform: JUnit 5 (Jupiter), JUnit 4 via Vintage, Kotlin suites | [junit.md](junit.md) |

More runners are landing in the next two batches:

| batch | runners |
|---|---|
| JavaScript | `jest`, `vitest`, `node-test`, `tap` |
| systems | `go`, `nextest` (Rust), `dotnet`, `rspec` (Ruby), `phpunit`, `ctest` (C/C++ / GoogleTest) |

## What each runner gives you

| runner | discovery | resume granularity | notes |
|---|---|---|---|
| `pytest` | `--collect-only` in a child process | **test** | direct node ids; falls back to collect-and-deselect when the id list is too large or stale |
| `unittest` | import + enumerate (`unittest.defaultTestLoader`) | **test** | recursive; loader placeholders are kept out of the manifest |
| `node` | Mocha loads the files in a child process | **test** | drives the mocha binary, not `npm test` |
| `junit` | JUnit Platform launcher over the compiled test classes | **test** | needs a JDK and the test classpath |

"Resume granularity" is the finest unit hh can leave out of a retry. `test`
means a re-run pays only for tests that had not finished.

## Not supported, and why

| runner | why not |
|---|---|
| Playwright, Cypress | browser orchestration, not a suite with a stable test list hh can budget; their own retry/parallel machinery owns the process |
| Swift XCTest, Elixir ExUnit | no per-test machine-readable stream without a project-side dependency hh would have to install |
| Bazel | every test is a sandboxed action scheduled by Bazel; there is no single process for hh to time, and no resume model |
| JUnit-platform frameworks behind Maven Surefire only | Surefire forks its own JVM and reports by file, so hh cannot stream per-test rows; the `junit` runner bypasses it by driving the Platform directly |

## Adding a runner

An adapter is one module in `executor/adapters/` that answers four questions:

- `--collect-out FILE`: write `{"raw_id", "key"}` per test, never executing.
- run mode with `--ledger FILE [--tests-file FILE]`: execute the selected ids
  (or all of them) and append ledger rows as they finish.
- rows are the protocol in `executor/adapters/base.py`: `start`, `end`
  (`key`, `raw_id`, `status`, `duration_ms`, `censored`) and one `suite_end`
  after the loop returns. A killed process writes no `suite_end`, which is how
  hh knows a record pass did not seal a half-finished manifest.
- `key` comes from `executor/keys.unique_key_map(raw_ids, "<runner>")`, or from
  the adapter itself when the runner owns its identity scheme (Mocha, JUnit do
  this) — in that case tests must pin that the two agree.

Then register the module name in `executor/tool.py` (`RUNNERS`, the adapter map)
and add its page here.
