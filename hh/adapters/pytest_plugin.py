"""Tier A adapter: pytest plugin (live ledger checkpoints).

Use:  HH_LEDGER=path/ledger.jsonl python3 -m pytest -p hh.adapters.pytest_plugin ...
Durations are free on the test report (Law: never add a pass over the suite).
"""
from __future__ import annotations

import os
import time

from .base import end_row, start_row
from ..keys import normalize

RUNNER = "pytest"
_T0: dict[str, float] = {}


def _ledger() -> str | None:
    return os.environ.get("HH_LEDGER")


def pytest_runtest_logstart(nodeid, location):
    led = _ledger()
    if led:
        _T0[nodeid] = time.perf_counter()
        start_row(led, normalize(RUNNER, nodeid), nodeid)


def pytest_runtest_logreport(report):
    led = _ledger()
    if not led or report.when != "call":
        return
    t0 = _T0.pop(report.nodeid, time.perf_counter() - report.duration)
    status = ("skipped" if report.skipped
              else "passed" if report.passed
              else "failed" if report.outcome == "failed" else "error")
    end_row(led, normalize(RUNNER, report.nodeid), report.nodeid, status,
            (time.perf_counter() - t0) * 1000.0)
