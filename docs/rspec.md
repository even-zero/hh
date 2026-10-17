# RSpec (`--runner rspec`)

```bash
hh plan --root . --runner rspec --out plan.json
hh run  --plan plan.json --root .
```

Unit of measurement: **one example**. Resume granularity: one example, by id.

## What hh needs

- RSpec (`gem install rspec`), or the project's own `bin/rspec` / `bundle exec
  rspec` when a `Gemfile` is present. `HH_RSPEC` overrides the binary.
- `--root` at the project directory (the one with `spec/`, or whatever
  `.rspec`/`--default-path` says).

## How it works

- **Discovery** runs `rspec --dry-run --format json --out <file>`. RSpec loads
  the spec files and reports every example with its id (`./spec/a_spec.rb[1:2]`)
  without running an example body.
- **Execution** runs `rspec --require <hh's formatter> --format HhRspecFormatter
  <ids…>`. The formatter (`executor/adapters/rspec_formatter.rb`) writes a
  ledger row in `example_passed`, `example_failed` and `example_pending`, so
  rows land as examples finish. RSpec's own JSON report is written when the
  process ends, which is why it is not used here: a suite killed at the budget
  would report nothing.

A resume passes the missing example ids on the command line; RSpec accepts
those natively, so a resume runs exactly the examples that never finished.

## Identity

`rspec::spec/a_spec.rb/1:2` — the spec file and the example's position inside
it. The file stays a path segment, so the store's file tier aggregates per spec
file.

The formatter computes that key in Ruby and `rspec_stream.py` computes it in
Python for discovery; the integration test runs a real suite and asserts the two
agree, because a drift would make every resume re-run finished examples.

## Gotchas

- **`--dry-run` still loads the suite**, so a spec file with a syntax error
  fails discovery — loudly, with RSpec's message, instead of as an empty suite.
- **Data-driven examples** (`it` inside a loop over cases) are ordinary
  examples with their own ids; they resume individually.
- **`--require` adds hh's formatter; the project's formatters still run.** A
  `.rspec` with `--format documentation` keeps its output on stdout, which hh
  reads only for error messages.
- **Exit code 1 means "examples failed"** (rows were written). RSpec also exits
  1 when a spec file cannot be loaded — with no rows at all — and that case is
  reported as an adapter error, not as a suite that vanished.
- **`bundle exec` is used when a `Gemfile` exists** and there is no `bin/rspec`,
  so a project's pinned RSpec version is the one that runs.
