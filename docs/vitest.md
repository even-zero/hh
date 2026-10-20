# Vitest (`--runner vitest`)

```bash
hh plan --root . --runner vitest --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test file**. Shared execution model, chunking and
environment: [javascript.md](javascript.md).

## What hh needs

- `vitest` installed in the project; hh runs `node_modules/vitest/vitest.mjs`,
  found from the suite root upwards.
- a suite Vitest itself would run — the project's `vitest.config.*` is what
  decides `include`, environment and setup files.

## How it works

- **Discovery** runs `vitest list --json` (Vitest ≥ 1.5) and keeps the distinct
  files. Older versions, or a `list` that fails, fall back to Vitest's default
  include shape (`*.test.*`, `*.spec.*`).
- **Execution** runs `vitest run --reporter=json --outputFile=<tmp>` with the
  selected files as positional arguments, in chunks. One verdict per file;
  `duration_ms` is the sum of the assertions in it.

## Identity

`vitest::src/store/cart.test.ts` — path relative to the root.

## Resume granularity: the file

Vitest can *list* individual tests, but not run an arbitrary subset of them by
id: `-t` is a single global name pattern. Running "these three tests in this
file" would mean one process per file anyway, so hh measures and resumes by
file and reports it that way.

## Gotchas

- **Never pass `--silent` before file paths.** Vitest's CLI consumes the next
  argument as the option's value (`Unexpected value "--silent=src/a.test.js"`),
  which is why hh uses `--passWithNoTests` and no `--silent`.
- **`--passWithNoTests` is deliberate.** An empty selection (everything already
  done) must be a successful no-op, not an exit code 1 that hh reports as a
  crash.
- **Workspace / projects.** `vitest list` reports every test in every project,
  and hh keeps the distinct files that fall under the root. A workspace whose
  projects live outside the root is therefore partially visible — point
  `--root` at the workspace root to time all of it.
- **`--reporter=json` writes one document** even with several files in the
  chunk; the human reporters' output is not parsed.
- **Pool workers** (threads/forks) are Vitest's business. hh times the
  files; it does not cap the concurrency Vitest chose.
