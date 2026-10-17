# pytest

```bash
hh plan --root . --runner pytest --out plan.json
hh run  --plan plan.json --root .
```

## What hh needs

Nothing beyond `pytest` itself. Discovery runs `pytest --collect-only` in a
child process, so **collection-time errors and conftest imports behave exactly
as they do when you run pytest yourself**. If collection needs a virtualenv,
activate it (or point `HH_PYTEST_ARGS` at the right flags) — hh inherits the
environment it is started in.

Suite-wide pytest options can be passed through `HH_PYTEST_ARGS`:

```bash
HH_PYTEST_ARGS="--ignore=tests/contrast" hh plan --root . --runner pytest
```

They are appended to hh's own options for both discovery and execution, so the
manifest and the run always see the same set of tests.

## How it works

- **Discovery** — `--collect-only` in a child process, one node id per line.
- **Execution** — a warm run passes the node ids it is going to run *directly*
  to pytest, so pytest collects only those items instead of the whole tree.
  On click that skips 31k deselected parametrizations, which is why a measured
  run can be slightly faster than the plain command it replaces.
- Two caps (8000 ids, 600 kB of argv) keep the command line usable. Beyond
  them, and whenever direct ids fail to collect, the adapter falls back to
  collecting the tree and deselecting everything else — in a **fresh process**,
  because a pytest session that already failed collection cannot be reused.

## Identity

`pytest::tests/a/test_b/test_x :: <param hash>` — the file path, then the test
name, with the class segment dropped so a class rename does not break identity.
Parametrisations get an 8-hex suffix of their id.

## Gotchas

- **Sample scripts that pytest happily imports.** pygments ships `.py` files
  under `tests/examplefiles/` that are *examples*, not tests, but pytest
  collects them as modules and they fail at import. That is why the fallback
  exists: the ids are real, the modules just cannot be imported on their own.
- **`-p no:cacheprovider`** is what the benchmark uses to keep `.pytest_cache`
  out of the way; hh does not add it for you.
- **xdist (`-n auto`)** fork or spawn workers, which makes per-test ledger rows
  unreliable. hh runs one process; do not pass `-n`.
