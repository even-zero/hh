"""Tier A adapter: stdlib unittest with live ledger checkpoints.

Usage:
  python3 -m hh.adapters.unittest_stream --root DIR --ledger LEDGER \
      [--tests id1 id2 ...]        # run selected tests (skip ledgered ones)
                                  # default: discover test_*.py under root
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import os
import sys
import time
import unittest

from .base import end_row, start_row
from ..keys import normalize

RUNNER = "unittest"
PATTERNS = ("test_*.py", "*_test.py")
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
             "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
             "site-packages", ".eggs"}


class LedgerResult(unittest.TestResult):
    def __init__(self, ledger: str, keys: dict[str, str] | None = None, *a, **kw):
        super().__init__(*a, **kw)
        self.ledger = ledger
        self.keys = keys or {}
        self.t0 = 0.0

    def _key(self, test) -> str:
        """Memoized key lookup.

        `dict.get(k, normalize(...))` evaluates normalize on *every* call even
        when the key is present; at two rows per test that was ~0.2 s on a
        1.1k-test suite — more than the ledger I/O itself.
        """
        tid = test.id()
        k = self.keys.get(tid)
        if k is None:
            k = normalize(RUNNER, tid)
            self.keys[tid] = k
        return k

    def startTest(self, test):
        super().startTest(test)
        self.t0 = time.perf_counter()
        start_row(self.ledger, self._key(test), test.id())

    def _end(self, test, status):
        end_row(self.ledger, self._key(test), test.id(), status,
                (time.perf_counter() - self.t0) * 1000.0)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._end(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._end(test, "failed")

    def addError(self, test, err):
        super().addError(test, err)
        self._end(test, "error")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._end(test, "skipped")


def _flatten(suite):
    out = []
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            out.extend(_flatten(t))
        else:
            out.append(t)
    return out


# ---------------------------------------------------------------- discovery
# `unittest discover` only descends into directories that are importable
# packages. A repo with a plain `tests/` (no __init__.py) silently yields zero
# tests — the worst possible failure for a budget tool. Discovery below covers
# every test file recursively: package trees keep their normal ids (so existing
# stores stay valid) and non-package files get a path-qualified id.

def _rel_noext(root: str, path: str) -> str:
    rel = os.path.splitext(os.path.relpath(path, root))[0]
    return rel.replace(os.sep, "/")


def walk_test_files(root: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        for pat in PATTERNS:
            found.extend(os.path.join(dirpath, f)
                         for f in sorted(fnmatch.filter(filenames, pat)))
    # a file matching both patterns must appear once
    return list(dict.fromkeys(found))


def _standard_suite(root: str, errors: list[str]) -> list:
    tests = []
    for pat in PATTERNS:
        loader = unittest.TestLoader()
        try:
            suite = loader.discover(root, pattern=pat, top_level_dir=root)
        except Exception as e:                       # pragma: no cover - defensive
            errors.append(f"{pat}: {e!r}")
            continue
        tests.extend(_flatten(suite))
        errors.extend(loader.errors)
    return tests


def _covered_files(root: str, ids: list[str]) -> set[str]:
    """Which files the standard (package) discovery already saw."""
    covered = set()
    for tid in ids:
        parts = tid.split(".")
        for n in range(len(parts) - 2, 0, -1):
            cand = os.path.join(root, *parts[:n]) + ".py"
            if os.path.exists(cand):
                covered.add(os.path.abspath(cand))
                break
    return covered


def _load_file_module(path: str):
    name = "_hhfile_" + hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:12]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _qualified_id(root: str, path: str, test) -> str | None:
    method = getattr(test, "_testMethodName", None)
    if not method:
        return None
    return f"{_rel_noext(root, path)}.{type(test).__name__}.{method}"


def _fully_packaged(root: str, path: str) -> bool:
    """True when every directory from `path` up to `root` is a package.

    Such a file is already reachable by standard discovery; loading it
    standalone would only re-import it out of its package (relative imports
    fail) or import a helper module that happens to be named test_*.py.
    """
    d = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(root)
    while os.path.abspath(d) != root:
        if not os.path.exists(os.path.join(d, "__init__.py")):
            return False
        nd = os.path.dirname(d)
        if nd == d:
            return False
        d = nd
    return True


def discover_ids(root: str) -> tuple[list[str], list[str]]:
    """Recursive, every-layout test discovery. Returns (raw_ids, errors)."""
    root = os.path.abspath(root)
    ids: list[str] = []
    errors: list[str] = []
    standard = _standard_suite(root, errors)
    ids.extend(t.id() for t in standard)
    covered = _covered_files(root, ids)
    for path in walk_test_files(root):
        if os.path.abspath(path) in covered:
            continue
        if _fully_packaged(root, path):
            continue
        try:
            mod = _load_file_module(path)
        except Exception as e:
            errors.append(f"{_rel_noext(root, path)}: {e!r}")
            continue
        loader = unittest.TestLoader()
        try:
            suite = loader.loadTestsFromModule(mod)
        except Exception as e:                       # pragma: no cover - defensive
            errors.append(f"{_rel_noext(root, path)}: {e!r}")
            continue
        for test in _flatten(suite):
            qid = _qualified_id(root, path, test)
            if qid:
                ids.append(qid)
    return list(dict.fromkeys(ids)), errors


def resolve(root: str, raw_id: str):
    """Load one raw id produced by discover_ids (standard or path-qualified).

    Dotted ids are real module paths and must be imported as modules: loading
    such a file standalone breaks package-relative imports
    (``from ..blocks import x``). Only the path-qualified ids invented for
    non-package test trees are loaded from a file path.
    """
    root = os.path.abspath(root)
    sys.path.insert(0, root)
    if "/" in raw_id:
        parts = raw_id.split(".")
        for n in range(len(parts) - 2, 0, -1):
            rel = os.path.join(*parts[:n])
            path = os.path.join(root, rel + ".py")
            if not os.path.exists(path):
                continue
            cls_name, method = parts[n], ".".join(parts[n + 1:])
            mod = _load_file_module(path)
            cls = getattr(mod, cls_name)
            test = cls(method)
            _qualify(test, raw_id)
            return unittest.TestSuite([test])
        raise ImportError(f"cannot resolve {raw_id!r} under {root}")
    return unittest.defaultTestLoader.loadTestsFromName(raw_id)


def resolve_many(root: str, raw_ids: list[str]) -> list:
    """Resolve ids in bulk: dotted ids are one loader call, path ids one each."""
    root = os.path.abspath(root)
    sys.path.insert(0, root)
    dotted = [r for r in raw_ids if "/" not in r]
    pathy = [r for r in raw_ids if "/" in r]
    suites: list = []
    if dotted:
        # Resolve one *class* at a time, not one test at a time: the loader
        # walks module attributes on every call, and 1.1k single-test calls
        # cost 0.16 s (a fifth of the whole suite). Grouping by class cuts that
        # to a few dozen calls; anything that fails falls back per id.
        by_class: dict[str, list[str]] = {}
        for r in dotted:
            head, _, method = r.rpartition(".")
            (by_class.setdefault(head, []).append(method) if head and method
             else suites.append(resolve(root, r)))
        for head, methods in by_class.items():
            want = set(methods)
            picked: dict[str, unittest.TestCase] = {}
            try:
                loaded = _flatten(unittest.defaultTestLoader.loadTestsFromName(head))
            except Exception:
                loaded = []
            for t in loaded:
                m = getattr(t, "_testMethodName", None)
                if m in want:
                    _qualify(t, f"{head}.{m}")
                    picked.setdefault(m, t)
            if len(picked) == len(want):
                suites.append(unittest.TestSuite(picked[m] for m in methods))
            else:
                suites.extend(resolve(root, f"{head}.{m}") for m in methods)
    suites.extend(resolve(root, r) for r in pathy)
    return suites


def _qualify(test, qid: str) -> None:
    test.id = (lambda q: (lambda: q))(qid)          # instance-level override


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--tests", nargs="*", default=None)
    ap.add_argument("--tests-file")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    sys.path.insert(0, root)
    loader = unittest.defaultTestLoader
    if args.tests_file:
        import json
        with open(args.tests_file, encoding="utf-8") as f:
            selected = [json.loads(line) for line in f]
        keys = {t["raw_id"]: t["key"] for t in selected}
        args.tests = list(keys)
    if args.tests:
        suite = unittest.TestSuite(resolve_many(root, args.tests))
    else:
        # default (no selection file): the full recursive universe
        ids, _errors = discover_ids(root)
        suite = unittest.TestSuite(resolve_many(root, ids))

    from ..executor import _done_keys  # reuse ledger reader
    done = _done_keys(args.ledger)
    key_of = (lambda t: keys.get(t.id(), normalize(RUNNER, t.id()))) if args.tests_file else (lambda t: normalize(RUNNER, t.id()))
    tests = [t for t in _flatten(suite) if key_of(t) not in done]

    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"),
                                     resultclass=lambda *a, **k: LedgerResult(args.ledger, keys if args.tests_file else None, *a, **k))
    result = runner.run(unittest.TestSuite(tests))
    return 0 if result.wasSuccessful() or result.failures or result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
