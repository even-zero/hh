# The JavaScript adapters

hh talks to five JavaScript runners:

| `--runner` | framework | how a unit is identified |
|---|---|---|
| `node` | Mocha | one **test** (file + full title) |
| `jest` | Jest | one **file** |
| `vitest` | Vitest | one **file** |
| `node-test` | Node's built-in `node:test` | one **file** |
| `tap` | node-tap | one **file** |

Each has its own page: [mocha.md](mocha.md), [jest.md](jest.md),
[vitest.md](vitest.md), [node-test.md](node-test.md), [tap.md](tap.md).
This page explains what all five share.

## Why four of them are file-level

hh wants to measure a unit, leave it out of a resume, and re-run only what is
left. That needs an id the runner can be *given back*.

- **Jest** can list test files without running them, but its CLI has no way to
  run "these three tests, by id".
- **Vitest** can list individual tests (`vitest list`), but `-t` filters by one
  global name pattern; running an arbitrary set of ids means one process per
  file anyway.
- **`node:test`** and **tap** have no listing command at all.

So hh measures the file for those four and says so. A resume re-runs the files
that never got a verdict — not fewer. Mocha is the exception: it can enumerate
tests by loading the files and can be told to run a subset from the loaded
suite, so its unit is the test.

## The shared command line

Every JS adapter is one `.cjs` file in `executor/adapters/js/` and answers the
same two commands:

```bash
node <adapter>.cjs collect <root> [out.jsonl]        # enumerate, never execute
node <adapter>.cjs run <root> <ledger.jsonl> [selection.jsonl]
```

`collect` writes `{"raw_id", "key"}` per unit; `run` appends ledger rows
(`end` with `status`, `duration_ms`, then one `suite_end`). A process killed at
the budget writes no `suite_end`, which is how hh knows a record pass did not
seal a half-finished manifest. The protocol is described in
[README.md](README.md#adding-a-runner).

`selection.jsonl` is the resume list: rows `{"raw_id", "key"}`, one per line.
Without it the adapter runs the whole suite, which is the cold record pass.

## Chunked execution

Files are run in chunks of `HH_JS_CHUNK` (default **25**), and the ledger is
flushed after each chunk. A SIGKILL therefore loses at most the chunk in
flight, and the resume re-runs only the files that never got a row.

```bash
HH_JS_CHUNK=5 hh run --plan plan.json --root .
```

Each adapter reads the ledger it is handed and skips units that already have a
verdict there, so a killed **record** pass resumes instead of restarting, and a
retry cannot write a second row for a file that already finished.

A file the runner was given but said nothing about gets a `failed` row at 0 ms.
It is recorded as not-passed rather than dropped, so a resume cannot mistake it
for finished work.

## Finding the runner

hh runs the project's **own** installation: `node_modules/jest/bin/jest.js`,
`node_modules/vitest/vitest.mjs`, `node_modules/tap/dist/esm/run.mjs`, and
`mocha` resolved from `<root>/package.json`. The search walks up from the suite
root like Node's module resolution, so a suite inside a monorepo package finds
the runner installed at the workspace root.

`node` itself comes from `PATH`; the adapters never shell out through `npm test`
or `npx`. npm adds 1.5–2 s that is not test work, and it hides which binary
actually ran.

## Discovery loads the project

Enumerating a JavaScript suite means letting the project's own configuration
load: Jest reads `jest.config.*`, Vitest reads `vitest.config.*`, Mocha reads
`.mocharc.json`. That is still discovery, not execution — no test body runs —
but a config file with side effects does. hh measures that time in the plan's
`startup_s` and pays it again on every attempt, because it is part of the
suite's real cost.

Files outside the root hh was given are dropped. Jest and tap resolve their own
project root, which can be above the suite directory; a `../` key would corrupt
the store.

## Environment

| variable | effect |
|---|---|
| `HH_JS_CHUNK` | files per process (default 25) |
| `HH_MOCHA_REQUIRE` | Mocha only: comma-separated modules to require before loading the suite |
| `HH_JUNIT_*` | not used here; see [junit.md](junit.md) |

Exit codes: a failing test is a verdict, not an adapter error. The adapters
return 0 when they produced a complete ledger (`node`/Mocha propagates the
failure count as exit 1, which hh also accepts). A crash exits non-zero without
a `suite_end`, and hh reports it as "adapter exited before finishing the suite"
instead of pretending the missing tests passed.
