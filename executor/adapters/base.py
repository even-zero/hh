"""Ledger protocol shared by the adapters."""
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


# -- helpers for adapters that drive an external runner --------------------

def done_ids(ledger: str) -> set[str]:
    """Raw ids that already have a verdict in this ledger.

    A record pass can be killed too. The retry re-enters with the same ledger,
    so it must not run (or record) a unit that already finished: the store
    would count it twice and the resume would re-pay for it.
    """
    done: set[str] = set()
    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue                      # partial last line from a kill
                if row.get("type") == "end" and row.get("raw_id"):
                    done.add(row["raw_id"])
    except OSError:
        pass
    return done


def selection_ids(tests_file: str | None) -> list[str] | None:
    """Raw ids from hh's selection file, or None meaning 'the whole suite'."""
    if not tests_file:
        return None
    ids: list[str] = []
    with open(tests_file, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("raw_id"):
                ids.append(row["raw_id"])
    return ids


def write_ids(path: str, rows) -> int:
    """Write `{"raw_id", "key"}` rows for discovery; returns how many."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for raw_id, key in rows:
            f.write(json.dumps({"raw_id": raw_id, "key": key}) + "\n")
            n += 1
    return n
