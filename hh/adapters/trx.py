"""Tier B adapter: Visual Studio TRX (.NET / VSTest / NUnit / xUnit).

TRX is namespace-heavy and durations are .NET TimeSpans (hh:mm:ss.fffffff).
TestDefinitions maps testId → (className, methodName) for real identity.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..keys import normalize

RUNNER = "trx"
NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _timespan_ms(s: str) -> float:
    if not s:
        return 0.0
    h, m, sec = s.split(":")
    return (int(h) * 3600 + int(m) * 60 + float(sec)) * 1000.0


def parse(trx_bytes: bytes) -> list[dict]:
    root = ET.fromstring(trx_bytes)
    defs = {}
    for el in root.iter():
        if _local(el.tag) == "UnitTest":
            tid = el.get("id")
            for child in el.iter():
                if _local(child.tag) == "TestMethod":
                    defs[tid] = (child.get("className", ""), el.get("name", ""))
    out = []
    for el in root.iter():
        if _local(el.tag) != "UnitTestResult":
            continue
        tid = el.get("testId")
        cls, name = defs.get(tid, ("", el.get("testName", "")))
        raw = f"{cls}.{name}" if cls else el.get("testName", "")
        outcome = el.get("outcome", "Passed")
        status = ("passed" if outcome == "Passed"
                  else "skipped" if outcome in ("NotExecuted", "Inconclusive")
                  else "failed")
        out.append({
            "key": normalize(RUNNER, raw), "raw_id": raw, "status": status,
            "duration_ms": _timespan_ms(el.get("duration", "0")), "censored": False,
        })
    return out
