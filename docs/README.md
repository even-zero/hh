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
| `jest` | JavaScript | Jest | [jest.md](jest.md) |
| `vitest` | JavaScript | Vitest | [vitest.md](vitest.md) |
| `node-test` | JavaScript | Node's built-in `node:test` | [node-test.md](node-test.md) |
| `tap` | JavaScript | node-tap | [tap.md](tap.md) |
| `junit` | Java, Kotlin | anything on the JUnit Platform: JUnit 5 (Jupiter), JUnit 4 via Vintage, Kotlin suites | [junit.md](junit.md) |
| `go` | Go | `go test` (tests, examples, fuzz seeds) | [go.md](go.md) |
| `nextest` | Rust | cargo-nextest | [nextest.md](nextest.md) |
| `rspec` | Ruby | RSpec | [rspec.md](rspec.md) |
| `phpunit` | PHP | PHPUnit 9.4+ | [phpunit.md](phpunit.md) |
| `ctest` | C, C++ | CTest: plain `add_test`, GoogleTest, Catch2 | [ctest.md](ctest.md) |

The five JavaScript adapters share one execution model (file granularity for
four of them, chunked runs, `HH_JS_CHUNK`): [javascript.md](javascript.md).

## What each runner gives you

| runner | discovery | resume granularity | notes |
|---|---|---|---|
| `pytest` | `--collect-only` in a child process | **test** | direct node ids; falls back to collect-and-deselect when the id list is too large or stale |
| `unittest` | import + enumerate (`unittest.defaultTestLoader`) | **test** | recursive; loader placeholders are kept out of the manifest |
| `node` | Mocha loads the files in a child process | **test** | drives the mocha binary, not `npm test` |
| `jest` | `jest --listTests --json`, then its own file shapes | **file** | run with `--runTestsByPath`; Jest cannot run a list of ids |
| `vitest` | `vitest list --json`, then Vitest's include shapes | **file** | `-t` is one global name pattern, so ids cannot be replayed |
| `node-test` | Node's default file shapes (`*.test.*`, `test-*`, `test/`) | **file** | `node --test` has no listing command; needs Node ≥ 20 |
| `tap` | tap's default file shapes | **file** | parses top-level TAP points, `# time=` per file |
| `junit` | JUnit Platform launcher over the compiled test classes | **test** | needs a JDK and the test classpath |
| `go` | `go test -list '.*' ./...` | **test** | `-count=1`, so cached results never look instant |
| `nextest` | `cargo nextest list --message-format json` | **test** | ignored tests are not in the manifest |
| `rspec` | `rspec --dry-run --format json` | **test** | a formatter streams rows; RSpec's JSON report is end-of-run only |
| `phpunit` | `phpunit --list-tests-xml` (>= 9.4) | **test** | TeamCity messages; data-provider sets are summed per method |
| `ctest` | `ctest --show-only=json-v1` | **test** | `DISABLED` tests are left out; `--root` may be the source or build tree |

"Resume granularity" is the finest unit hh can leave out of a retry. `test`
means a re-run pays only for tests that had not finished; `file` means a resume
re-runs every file that never got a verdict, even if some tests inside it did.

## Not supported, and why

| runner | why not |
|---|---|
| Playwright, Cypress | browser orchestration, not a suite with a stable test list hh can budget; their own retry/parallel machinery owns the process |
| Swift XCTest, Elixir ExUnit | no per-test machine-readable stream without a project-side dependency hh would have to install |
| Bazel | every test is a sandboxed action scheduled by Bazel; there is no single process for hh to time, and no resume model |
| JUnit-platform frameworks behind Maven Surefire only | Surefire forks its own JVM and reports by file, so hh cannot stream per-test rows; the `junit` runner bypasses it by driving the Platform directly |
| .NET (`dotnet test`, xUnit/NUnit/MSTest) | the only per-test machine-readable output is a TRX file, written when the run ends — a suite killed at the budget would report nothing, and TRX was deliberately removed from this tool. The console logger streams but reports display names, not the fully-qualified ids a resume needs. Adding it means re-adding TRX on purpose; not done silently. |
| Jest/Vitest/… test names instead of files | see [javascript.md](javascript.md): none of them can replay a list of ids |

## Adding a runner

An adapter is one entry in `executor/adapters/registry.py`:

```python
MODULE_ADAPTERS = {"pytest": "executor.adapters.pytest_stream", ...}
NODE_ADAPTERS = {"jest": "jest_stream.cjs", ...}      # executor/adapters/js/
```

`NODE_ADAPTERS` is for the JavaScript runners, which share the CLI and the
ledger plumbing in `executor/adapters/js/common.cjs`
([javascript.md](javascript.md)); `MODULE_ADAPTERS` is for a Python module that
implements the protocol below. `tool.py`, `runner.py`, the CLI and the
`run_tests` tool spec all read the registry — there is no second list to keep
in sync. Then add the runner's page here.

A module adapter answers four questions:

- `--collect-out FILE`: write `{"raw_id", "key"}` per test, never executing.
- run mode with `--ledger FILE [--tests-file FILE]`: execute the selected ids
  (or all of them) and append ledger rows as they finish.
- rows are the protocol in `executor/adapters/base.py`: `start`, `end`
  (`key`, `raw_id`, `status`, `duration_ms`, `censored`) and one `suite_end`
  after the loop returns. A killed process writes no `suite_end`, which is how
  hh knows a record pass did not seal a half-finished manifest.
- `key` comes from `executor/keys.unique_key_map(raw_ids, "<runner>")`, or from
  the adapter itself when the runner owns its identity scheme (Mocha, JUnit and
  the file-level JS adapters do this) — in that case tests must pin that the two
  agree, and `executor/keys.FILE_LEVEL_RUNNERS` must list the runner so the file
  aggregate stays the key itself.

JS adapters return the same rows through `common.main(runner, {collect,
runChunk})`; they own their discovery and their per-chunk report parsing, and
`common` owns chunking, the ledger, `suite_end` and the "no verdict means
failed" rule.

The systems adapters (`go`, `nextest`, `rspec`, `phpunit`, `ctest`) are module
adapters that drive an external tool. They share `executor/adapters/driver.py`
for the CLI and error reporting, and each one owns the translation from its
tool's output to ledger rows. They all read their own ledger back and skip ids
that already have a verdict, because a record pass can be killed too.
