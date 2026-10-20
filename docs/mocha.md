# Mocha (`--runner node`)

```bash
hh plan --root . --runner node --out plan.json
hh run  --plan plan.json --root .
```

The runner value is `node` because Node is the runtime; the framework it drives
is Mocha. Other JavaScript runners get their own values (`jest`, `vitest`,
`node-test`, `tap`) in the next batch.

## What hh needs

- `mocha` resolvable from the project (a normal `npm install`).
- your test files under `test/` or `tests/`, any depth — discovery is
  recursive, so `test/acceptance/**` and `test/unit/**` are part of the suite.

hh drives the **mocha binary directly**, not `npm test`. That is deliberate:
npm adds 1.5–2 s of startup that has nothing to do with the tests, and it hides
the exit code. When a project's `npm test` does more than mocha (a linter, a
babel hook), reproduce what matters with the environment variables below.

## Environment

| variable | effect |
|---|---|
| `HH_MOCHA_REQUIRE` | comma-separated modules to `require` before loading the suite — the same preload a repo's own `npm test` does (express needs `test/support/env`) |
| `HH_LEDGER` | not used by this adapter; kept for parity with the other runners |

A `.mocharc.json` with a `file` array (nock's `tests/setup.js`) is honoured:
those files are added **inside** the mocha context with `mocha.addFile`, not
`require`d, because they register hooks (`afterEach`) at load time.

## How it works

- **Discovery** loads the files in a child process and prints one
  `{key, raw_id}` per test. Loading is not running: hooks that mutate global
  state do run, which is true of every Mocha-based tool.
- **Execution** loads the same files, keeps only the selected tests, and runs
  them under a reporter that appends a ledger row as each test passes, fails or
  is skipped.
- The process **exits as soon as the run is over**. Suites that leave a handle
  open (supertest starts a server) would otherwise keep node alive until the
  caller's timeout kills a process that has no work left.

## Identity

`mocha::test/router/GET_/` → `mocha::<path under the root><full title>`, with
whitespace in titles replaced by `_`, plus an index suffix when two tests in one
file share a full title. The path is relative to the root, so
`test/Router.js` and `test/acceptance/Router.js` do not collide.

hh does **not** re-normalise these keys: the adapter owns its identity scheme,
and re-deriving it in Python would produce keys the ledger never writes.

## Gotchas

- **Mocha 6 and older** have no `loadFilesAsync`; the adapter falls back to the
  callback loader.
- **Container-style suites** (`--opts`, `mocha.opts`) are not read beyond
  `.mocharc.json`; if a project depends on `mocha.opts`, pass what it needs via
  `HH_MOCHA_REQUIRE`.
- **Timeouts inside Mocha** (`this.timeout(ms)`) are the framework's own and are
  independent of hh's budget. A test that trips them is recorded as `failed`,
  not as a timeout of the suite.
