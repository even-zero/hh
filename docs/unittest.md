# unittest

```bash
hh plan --root . --runner unittest --out plan.json
hh run  --plan plan.json --root .
```

## What hh needs

Nothing — this is the stdlib runner and the adapter is stdlib too.

Discovery is **recursive and layout-agnostic**: hh first asks
`unittest.TestLoader().discover` for the standard suite (so packages,
`top_level_dir` and `__init__.py` semantics are the real ones), then walks the
tree for test files the standard discovery missed — `*_test.py`,
`test_*.py`, `tests/`, arbitrary nesting — and imports those directly.

## How it works

- **Discovery** imports the test modules and enumerates the cases; it never
  runs them.
- **Execution** builds a `TestSuite` from the selected ids and runs it with a
  result class that writes ledger rows as each test finishes.
- Loader placeholders — `_FailedTest` for a module that would not import,
  `ModuleSkipped` for one that skips at import — are reported as collection
  errors, **never** as tests. They cannot be run, and putting them in the
  manifest would make a later run report them as `unresolved` forever.

## Identity

`unittest::tests/test_s001/test_t000` — path and method, class dropped (a class
rename should not invalidate measurements). Two classes in one file with the
same method name collide by construction; both stay addressable through a
deterministic collision suffix derived from the raw id, computed by the same
function discovery and execution use, so they cannot disagree.

## Gotchas

- **Module-level side effects run at discovery.** Anything executed at import
  time in a test module happens once more per manifest refresh (cached by tree
  fingerprint, so not once per call).
- **`sys.modules` is cleared** for modules under the suite root before
  discovery, so an edited file is seen; a module imported from outside the root
  is left alone.
- **Installed copies shadow the checkout.** If the package under test is also
  installed globally, `sys.path` order decides which one the tests import. Put
  the checkout first, or uninstall the global copy.
