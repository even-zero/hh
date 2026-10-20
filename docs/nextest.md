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

`cargo test` on its own is not supported: its JSON output is unstable and it has
no listing command, while nextest has both, plus a filter language that can name
an exact set of tests.

## How it works

- **Discovery** runs `cargo nextest list --message-format json` and keeps every
  test case, skipping the ones marked `"ignored": true` — nextest does not run
  them by default, so recording them would only create ids that never resolve.
- **Execution** runs `cargo nextest run --message-format json` and reads the
  event line by line: `test-started`, `test-passed`, `test-failed`,
  `test-skipped`, with `running` (seconds) as the duration. Rows are written as
  events arrive.

On a resume the missing ids become a filter expression, so only those tests run:

```bash
cargo nextest run -E 'binary_id(="mycrate::tests") & test(="test_add") | …'
```

## Identity

`nextest::mycrate/tests/test_add` — the binary id and the test name. The binary
id's own `::` is turned into a path separator so the key keeps the
`runner::file/name` shape the store aggregates on.

## Gotchas

- **Two JSON schemas.** nextest's listing changed from a dictionary of suites to
  a list of suites in the 0.9.9x line, and its events changed from
  `{"type":"test","event":"ok"}` to `{"type":"test-passed"}` in 0.9.38. Both
  forms are accepted; the CI job runs whatever is current.
- **`ignored` tests are not in the manifest** unless you run hh with a nextest
  config that runs them. If you do run them (`--run-ignored`), they will show up
  as `skipped` rows only if nextest reports them.
- **Retries**: if a profile configures retries, only the final verdict is
  recorded — the intermediate `test-retry-*` events are ignored on purpose.
- **A filter expression can be long** on a resume with many missing tests;
  nextest accepts it as one argument. A cold pass (nothing measured) runs the
  whole suite with no filter at all.
- **Compilation is not the test.** `cargo nextest run` builds first; that time
  is part of the wall clock hh reports, but it is measured as startup, not as a
  test, so the budget is not dominated by it.
