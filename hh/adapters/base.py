"""Ledger protocol shared by all capture adapters (Tier A/B)."""
from __future__ import annotations

import json
import os
import time


def append_row(ledger: str, row: dict) -> None:
    """One flush per row: crash-safe checkpointing (Law 2: never block tests
    on batch I/O). Flush (not fsync) is the right trade: it survives process
    SIGKILL — the failure mode that matters — and costs ~10µs vs ~1ms/fsync."""
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()


def start_row(ledger: str, key: str, raw_id: str) -> None:
    append_row(ledger, {"type": "start", "key": key, "raw_id": raw_id, "ts": time.time()})


def end_row(ledger: str, key: str, raw_id: str, status: str, duration_ms: float,
            censored: bool = False) -> None:
    append_row(ledger, {
        "type": "end", "key": key, "raw_id": raw_id, "status": status,
        "duration_ms": round(duration_ms, 3), "censored": censored, "ts": time.time(),
    })
