# node-tap (`--runner tap`)

```bash
hh plan --root . --runner tap --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test file**. Shared execution model, chunking and
environment: [javascript.md](javascript.md).

## What hh needs

- `tap` installed in the project (`npm install -D tap`); hh runs
  `node_modules/tap/dist/esm/run.mjs`, found from the suite root upwards.
- test files in tap's default shapes: `*.test.{js,cjs,mjs}` and anything under
  `test/` or `tests/`.

## How it works

- **Discovery** walks those shapes; tap has no "list the tests" command.
- **Execution** runs `tap run --disable-coverage --no-color --reporter=tap
  <files>` in chunks, and parses the **top-level** TAP points — one per file:

  ```
  ok 1 - test/router.test.js # time=14.362ms
  not ok 2 - test/broken.test.js # time=3.1ms
  ```

  Nested assertions are indented and are skipped: the file is the unit, and the
  top-level point is the only line that names it.

- `# time=` is the file's wall clock inside tap. A file can pass while a
  subtest is skipped; tap reports it as `ok` with `# SKIP`, and hh records the
  file as `passed`.

## Identity

`tap::test/router.test.js` — path relative to the root.

## Resume granularity: the file

tap can run a list of files but has no "run these test ids" option, and no
listing command to name ids in the first place.

## Gotchas

- **tap names points relative to its project root**, which it finds by walking
  up to the nearest `package.json` — often above the suite root. The adapter
  matches a point to a requested file by the longest common path tail, so
  `packages/web/test/a.test.js` still lands on `test/a.test.js`.
- **Coverage is disabled on purpose** (`--disable-coverage`): tap turns it on by
  default and it costs more than most suites hh is timing.
- **tap's own timeouts** (`--timeout`, default 30 s per file) are tap's, not
  hh's budget. A file that trips one is `not ok`, recorded as `failed`.
- **No colour** (`--no-color`) so the parser sees the same bytes on every
  machine.
- **`tap` writes its report on stdout**, including the `TAP version 14` header
  and a trailing `# time=` summary line. The parser only accepts unindented
  `ok`/`not ok` points, so the summary cannot be mistaken for a test.
