"""Tier A adapter: stdlib unittest with live ledger checkpoints.

Usage:
  python3 -m hh.adapters.unittest_stream --root DIR --ledger LEDGER \
      [--tests id1 id2 ...]        # run selected tests (skip ledgered ones)
                                  # default: discover test_*.py under root
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unittest

from .base import end_row, start_row
from ..keys import normalize

RUNNER = "unittest"


class LedgerResult(unittest.TestResult):
    def __init__(self, ledger: str, *a, **kw):
        super().__init__(*a, **kw)
        self.ledger = ledger
        self.t0 = 0.0

    def startTest(self, test):
        super().startTest(test)
        self.t0 = time.perf_counter()
        start_row(self.ledger, normalize(RUNNER, test.id()), test.id())

    def _end(self, test, status):
        end_row(self.ledger, normalize(RUNNER, test.id()), test.id(), status,
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--tests", nargs="*", default=None)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    sys.path.insert(0, root)
    loader = unittest.defaultTestLoader
    if args.tests:
        suite = unittest.TestSuite(loader.loadTestsFromName(t) for t in args.tests)
    else:
        suite = loader.discover(root, pattern="test_*.py", top_level_dir=root)

    from ..executor import _done_keys  # reuse ledger reader
    done = _done_keys(args.ledger)
    tests = [t for t in _flatten(suite) if normalize(RUNNER, t.id()) not in done]

    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"),
                                     resultclass=lambda *a, **k: LedgerResult(args.ledger, *a, **k))
    result = runner.run(unittest.TestSuite(tests))
    return 0 if result.wasSuccessful() or result.failures or result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
