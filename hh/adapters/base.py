"""Ledger protocol shared by all capture adapters (Tier A/B)."""
from __future__ import annotations

import json
import os
import time


# A single O_APPEND descriptor per ledger and process: every row is still one
# write(2), so a SIGKILL keeps all complete rows, without open/close per test.
# Discard inherited descriptors on fork (pytest plugins may fork workers).
_FDS: dict[str, int] = {}
_PID = os.getpid()


def append_row(ledger: str, row: dict) -> None:
    global _PID
    if _PID != os.getpid():
        for fd in _FDS.values():
            os.close(fd)
        _FDS.clear()
        _PID = os.getpid()
    fd = _FDS.get(ledger)
    if fd is None:
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        _FDS[ledger] = fd
    data = (json.dumps(row) + "\n").encode("utf-8")
    # Single append syscall per row (also safe when multiple shards write).
    if os.write(fd, data) != len(data):
        raise OSError("short ledger write")


def start_row(ledger: str, key: str, raw_id: str) -> None:
    append_row(ledger, {"type": "start", "key": key, "raw_id": raw_id, "ts": time.time()})


def suite_end_row(ledger: str, tests: int) -> None:
    """Marker: this process finished the suite it was asked to run.

    Written after the runner loop returns (including with failing tests) and
    never on a kill/crash. A cold record pass seals the manifest cache only if
    this marker is present, so a half-finished suite can never become the
    "complete" test list that the next plan trusts.
    """
    append_row(ledger, {"type": "suite_end", "tests": int(tests), "ts": time.time()})


def end_row(ledger: str, key: str, raw_id: str, status: str, duration_ms: float,
            censored: bool = False) -> None:
    append_row(ledger, {
        "type": "end", "key": key, "raw_id": raw_id, "status": status,
        "duration_ms": round(duration_ms, 3), "censored": censored, "ts": time.time(),
    })
