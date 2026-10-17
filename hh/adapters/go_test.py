"""Tier A/B adapter: `go test -json` event stream (ARCHITECTURE §10.1).

parse() ingests a recorded NDJSON stream; iter_events() reads a live stream
(one JSON object per line) so a wrapper can checkpoint as tests finish.
"""
from __future__ import annotations

import json

from ..keys import normalize

RUNNER = "go"


def terminal_row(line: str) -> dict | None:
    """One line of `go test -json` → an end-row dict, or None if not terminal.

    Shared by parse() (recorded output) and go_stream (live streaming), so the
    two paths cannot drift.
    """
    line = line.strip()
    if not line:
        return None
    ev = json.loads(line)
    test = ev.get("Test")
    if not test or ev.get("Action") not in ("pass", "fail", "skip"):
        return None
    pkg = ev.get("Package", "")
    raw_id = f"{pkg}::{test}"
    return {
        "key": normalize(RUNNER, raw_id), "raw_id": raw_id,
        "status": {"pass": "passed", "fail": "failed", "skip": "skipped"}[ev["Action"]],
        "duration_ms": float(ev.get("Elapsed", 0.0)) * 1000.0,
        "censored": False,
    }


def parse(raw: bytes) -> list[dict]:
    """Group run/pass/fail/skip events into per-test observations.

    Elapsed (seconds) is attached to the terminal action. Subtests keep their
    'Parent/child' names — identity is the whole Test path.
    """
    ends: dict[str, dict] = {}
    for line in raw.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        test = ev.get("Test")
        if not test:            # package-level events carry no Test
            continue
        pkg = ev.get("Package", "")
        raw_id = f"{pkg}::{test}"
        action = ev.get("Action")
        if action in ("pass", "fail", "skip"):
            ends[raw_id] = {
                "key": normalize(RUNNER, raw_id), "raw_id": raw_id,
                "status": {"pass": "passed", "fail": "failed",
                           "skip": "skipped"}[action],
                "duration_ms": float(ev.get("Elapsed", 0.0)) * 1000.0,
                "censored": False,
            }
    return list(ends.values())


def iter_events(line_iter):
    """Live mode: yield observations as terminal events arrive."""
    for line in line_iter:
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if ev.get("Test") and ev.get("Action") in ("pass", "fail", "skip"):
            yield parse((line + "\n").encode())[0]
