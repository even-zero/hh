# Rust / cargo-nextest (`--runner nextest`)

```bash
hh plan --root . --runner nextest --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test**. Resume granularity: one test, by binary and
name.

## What hh needs

- Rust with **cargo-nextest** installed (`cargo install cargo-nextest`, or
  `cargo binstall cargo-nextest`); `HH_NEXTEST` points at a `cargo-nextest`
  binary directly if that is how you have it.
- a crate (or workspace) at `--root`.

`cargo test` on its own is not supported: it has no listing command and no way
to name an exact set of tests, while nextest has both.

## How it works

- **Discovery** runs `cargo nextest list --message-format json` and reads the
  `rust-suites` map: every suite has a `binary-id` (`demo` for the library's
  unit tests, `demo::it` for `tests/it.rs`, `demo::bin/app` for a binary), the
  package and binary names, and a `testcases` map. Tests marked `ignored` are
  left out — nextest does not run them by default, so recording them would only
  create ids that never resolve — and so are suites whose `status` says they
  were not listed for this host.
- **Execution** runs `cargo nextest run --message-format json` and reads
  **libtest** JSON, one object per line:

  ```json
  {"type":"suite","event":"started","test_count":2}
  {"type":"test","event":"started","name":"demo::demo$tests::test_add"}
  {"type":"test","event":"ok","name":"demo::demo$tests::test_add","exec_time":0.0001}
  {"type":"test","event":"failed","name":"demo::demo$tests::test_fail","exec_time":0.0002}
  {"type":"suite","event":"failed","passed":1,"failed":1,"ignored":0,
   "measured":0,"filtered_out":0,"exec_time":0.002}
  ```

  nextest prefixes each test name with `<package>::<binary-name>$` so a
  workspace full of binaries stays unambiguous. hh builds the mapping from the
  listing, not by splitting the string: the `$` suffix is matched against the
  suites `list` reported, with the bare name as a fallback for older versions.
  `exec_time` is seconds.

Rows are written as events arrive, so a kill at the budget keeps every finished
test.

On a resume the missing ids become a filter expression, so only those tests run:

```bash
cargo nextest run -E '(binary_id(="demo") & test(="tests::test_add")) | …'
```

A very large resume (hundreds of ids) would blow past the argv limit, so hh
splits it into several `cargo nextest run` invocations, each with its own
filter. A cold pass (nothing measured) runs the whole suite with no filter.

## Identity

`nextest::demo/tests/test_add` — the binary id and the test name, both with
their `::` turned into `/`. The directory part (`nextest::demo`) is the package,
which is the nearest useful aggregate for a Rust suite.

## Gotchas

- **`cargo nextest list` must be run again on every execution**, even a resume:
  the events name tests, and the listing is what turns a name into an id. It is
  cheap once the crate is built.
- **`ignored` tests are not in the manifest** unless the project's nextest
  config runs them. If they appear as `ignored` events anyway they are recorded
  as `skipped`.
- **Retries**: only the final verdict is recorded. The libtest name carries a
  `#<n>` suffix when a test was retried, and a `@stress-<n>` marker under stress
  runs — both are stripped before matching.
- **Exit code 100 means "tests failed"**, which is data, not an error. Exit 4
  means "no tests ran", which on a filtered resume means the ids no longer match
  the suite, and that is reported as an error instead of a silent empty run.
- **Compilation is not the test.** `cargo nextest run` builds first; that time
  is part of the wall clock hh reports, but it is measured as startup, not as a
  test, so the budget is not dominated by it.
