# Go (`--runner go`)

```bash
hh plan --root . --runner go --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one test**. Resume granularity: one test, by package.

## What hh needs

- the Go toolchain (`go` on `PATH`, or `HH_GO`).
- a module directory: run hh with `--root` at the directory holding `go.mod`.
- the packages' dependencies available (`go mod download`, or a vendor tree) —
  discovery builds the test binaries.

## How it works

- **Discovery** runs `go test -list '.*' ./...`, which builds each package's
  test binary and prints its test, example and fuzz function names without
  running them. Benchmarks are left out: `go test` does not run them either
  unless `-bench` is given.
- **Execution** runs `go test -json -count=1` and reads the event stream. Each
  `{"Action":"pass","Test":"…","Elapsed":…}` becomes a ledger row the moment it
  arrives, so a SIGKILL at the budget keeps everything already finished.
  `-count=1` matters: without it Go serves a cached result (`ok (cached)`, ~0 s)
  and the store would learn that the suite is instant.

On a resume, the ids still missing are grouped **by package** and each package
is run with `-run '^(TestA|TestB)$'`: `-run` is a per-invocation regex, so one
package cannot be told "these three tests" without also naming the package.

## Identity

`go::example.com/m/pkg/TestAdd` — the import path and the top-level test
function. Subtests (`TestFoo/case`) are not separate ids: `-run '^TestFoo$'`
runs the test and all of its subtests, so the parent is the smallest unit that
can be replayed on its own. Their durations are part of the parent's.

## Gotchas

- **A package that does not compile is an error, not an empty package.** `go
  test -list` reports `FAIL pkg [build failed]`, and hh fails the attempt with
  that message instead of recording a suite that shrank.
- **Examples and fuzz targets are tests.** `ExampleFoo` output is compared by
  `go test`, and a fuzz target's seed corpus runs, so both are in the manifest.
- **`-count=1` also disables the test cache for the budget**, which is the
  point: a cached run says nothing about how long the suite takes.
- **Package-level failures** (a panic in `TestMain`, a timeout) arrive as a
  `fail` event without a `Test` field; they are not rows, and the exit code
  marks the attempt as failed work.
- **Parallelism is Go's.** `go test ./...` runs packages in parallel by default
  (`-p`); hh times what Go chose and does not cap it.
