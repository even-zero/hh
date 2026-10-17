# Jest (`--runner jest`)

```bash
hh plan --root . --runner jest --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test file**. Shared execution model, chunking and
environment: [javascript.md](javascript.md).

## What hh needs

- `jest` installed in the project (`npm install -D jest`); hh runs
  `node_modules/jest/bin/jest.js`, found from the suite root upwards.
- a suite Jest itself would run: hh asks Jest for the file list, so the
  project's `testMatch`, `roots` and config decide what is in it.

No config of hh's does anything to Jest: no `--config`, no `--testPathPattern`
of its own. If `npx jest` runs it, hh sees it.

## How it works

- **Discovery** runs `jest --listTests --json` and keeps the paths under the
  root. If Jest is missing or refuses to list, hh falls back to Jest's default
  file shapes (`*.test.*`, `*.spec.*`, anything under `__tests__/`) so a plan
  can still be made.
- **Execution** runs the selected files with `--runTestsByPath` and
  `--json --outputFile=<tmp> --ci --silent`, in chunks. The JSON report gives a
  verdict per file; `duration_ms` is the sum of the assertions in that file, not
  the worker's wall clock.

Exit status of Jest is ignored on purpose: a failing test is a result to record,
not a reason to stop the run. A missing report *is* an error and fails the
attempt.

## Identity

`jest::src/auth/login.test.js` — the path relative to the root, exactly as Jest
prints it. No re-normalisation in Python.

## Resume granularity: the file

A resume re-runs every file that never got a verdict. If a file held ten tests
and nine finished before the kill, all ten run again: Jest cannot be handed
"the ninth test". hh does not pretend otherwise — the report says what was
re-paid.

## Gotchas

- **Jest's root can be above your root.** Jest picks its `rootDir` from the
  nearest config/`package.json`, which in a monorepo is the workspace root. hh
  keeps only files under the root you gave it, so `--root packages/web` times
  `packages/web` and nothing else.
- **`--listTests` starts Jest.** It loads the config and the haste map, and it
  costs a few hundred ms. That is measured as collection time and reused
  through the manifest cache until the tree changes.
- **Watching is never used.** `--ci` is passed so Jest cannot end up in watch
  mode in a non-TTY.
- **Jest 27 and newer** all produce the same `testResults` shape hh parses.
  Reporters configured in the project still run; hh reads Jest's own JSON file,
  not their output.
- **Failing suites still exit quietly for hh.** Jest exits 1 when tests fail;
  the adapter reads the report and exits 0, so `hh run` records failures
  normally.
