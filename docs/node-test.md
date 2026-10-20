# Node's test runner (`--runner node-test`)

```bash
hh plan --root . --runner node-test --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test file**. Shared execution model, chunking and
environment: [javascript.md](javascript.md).

`node-test` drives `node --test`, the runner built into Node itself. Nothing to
install.

## What hh needs

- **Node ≥ 20.** `--test-reporter` (the hook hh uses to read results as they
  finish) stabilised there. On older Node the tests skip with a clear message
  instead of silently reporting an empty suite.

## How it works

- **Discovery** follows Node's own default patterns: `*.test.{js,mjs,cjs}`,
  `test-*.{js,mjs,cjs}`, and any `.js/.mjs/.cjs` under `test/` or `tests/`.
  Node has no listing command, so these shapes are the contract.
- **Execution** runs `node --test --test-reporter <generated module> <files>`
  with explicit file paths. The reporter is a small `.mjs` file hh writes to the
  OS temp directory for each process; it forwards one JSON line per finished
  test (`data.file`, `details.duration_ms`), and hh aggregates those into one
  row per file.
- Files are passed explicitly because on Node 22 `node --test <dir>` means
  "load that directory as a module" (`Cannot find module`), not "search it".

## Identity

`node-test::test/router.test.mjs` — path relative to the root.

## Resume granularity: the file

A resume re-runs files that never got a verdict. `node --test` can filter by
name pattern (`--test-name-pattern`), but there is no way to run an arbitrary
list of ids, so the file is the honest unit.

## Gotchas

- **A test that never emits an event** (the file crashed at load, or a `before`
  hook threw) still gets a `failed` row at 0 ms: the file was given to the
  runner and produced no passing verdict.
- **Subtests count separately** in Node's stream; hh sums their durations into
  the file. A failing subtest fails the file.
- **Coverage is not enabled.** `node --test` does not turn it on by default, and
  hh does not add it: hh is timing the tests.
- **The reporter is per process.** If two hh runs share one OS temp dir the
  file names still differ by pid, so they cannot collide.
- **TypeScript** needs Node 22.6+ with type stripping, or a loader passed
  through `NODE_OPTIONS`; hh does not add one.
