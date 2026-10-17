"""Tier A pytest ledger plugin: checkpoint ends, name tests that stall.

Fast tests emit only an end row. A daemon watchdog emits an open start row for
any test still active after the grace period (20ms by default); this retains
hang attribution without doubling ledger traffic on enormous fast suites.
Finished tests always emit an end row before pytest moves to the next test.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .base import end_row, start_row
from ..keys import normalize

RUNNER = "pytest"
_GRACE_NS = max(1_000_000, int(float(os.environ.get("HH_START_GRACE_MS", "20")) * 1_000_000))
_ACTIVE = None  # (nodeid, perf_start, key, ledger) for the current test
_LOCK = threading.Lock()
_WATCHER = None
_KEYS: dict[str, str] | None = None
_DONE: set[str] | None = None


def _key(nodeid):
    global _KEYS
    if _KEYS is None:
        path = os.environ.get("HH_KEYS_FILE")
        if path:
            with open(path, encoding="utf-8") as f:
                _KEYS = {entry["raw_id"]: entry["key"] for line in f
                         for entry in [json.loads(line)]}
        else:
            _KEYS = {}
    key = _KEYS.get(nodeid)
    # `dict.get(k, normalize(...))` would rebuild the key on *every* call, even
    # on a hit: ~0.2 s on a 2k-test suite, i.e. more than the ledger I/O itself.
    return key if key is not None else normalize(RUNNER, nodeid)


def _done() -> set[str]:
    """Keys already recorded in this ledger (a resumed run must not re-pay)."""
    global _DONE
    if _DONE is None:
        _DONE = set()
        led = os.environ.get("HH_LEDGER")
        if led and os.path.exists(led):
            with open(led, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("type") == "end" and row.get("key"):
                        _DONE.add(row["key"])
    return _DONE


def pytest_collection_modifyitems(items):
    """Resume support: drop tests this ledger already finished."""
    led = os.environ.get("HH_LEDGER")
    if not led:
        return
    done = _done()
    if done:
        items[:] = [i for i in items if _key(i.nodeid) not in done]


def _watch():
    global _ACTIVE
    while True:
        time.sleep(_GRACE_NS / 2_000_000_000)
        entry = _ACTIVE
        if entry is not None and len(entry) == 4 and time.clock_gettime_ns(time.CLOCK_MONOTONIC) - entry[1] >= _GRACE_NS:
            with _LOCK:
                if _ACTIVE is entry and len(entry) == 4:
                    start_row(entry[3], entry[2], entry[0])
                    # Leave entry intact; a start exists for this test now.
                    _ACTIVE = (entry[0], entry[1], entry[2], entry[3], True)


def pytest_runtest_logstart(nodeid, location):
    global _WATCHER, _ACTIVE
    led = os.environ.get("HH_LEDGER")
    if not led:
        return
    if _WATCHER is None:
        _WATCHER = threading.Thread(target=_watch, daemon=True, name="hh-ledger-watch")
        _WATCHER.start()
    _ACTIVE = (nodeid, time.clock_gettime_ns(time.CLOCK_MONOTONIC), _key(nodeid), led)


def pytest_runtest_logreport(report):
    global _ACTIVE
    if report.when != "call" and not (report.when == "setup" and
                                      (report.skipped or report.failed)):
        return
    entry = _ACTIVE
    if entry is None or entry[0] != report.nodeid:
        return
    # The watcher can only race if the test lasted past the grace period.
    if time.clock_gettime_ns(time.CLOCK_MONOTONIC) - entry[1] >= _GRACE_NS:
        with _LOCK:
            entry = _ACTIVE
            _ACTIVE = None
    else:
        _ACTIVE = None
    if entry is None:
        return
    nodeid, t0, key, led = entry[:4]
    status = ("skipped" if report.skipped
              else "passed" if report.passed
              else "failed" if report.failed else "error")
    end_row(led, key, nodeid, status,
            (time.clock_gettime_ns(time.CLOCK_MONOTONIC) - t0) / 1_000_000)
