"""Tier B adapter: JUnit-XML parser — the lingua franca (ARCHITECTURE §10.2).

Covers Surefire/Gradle/TestNG/PHPUnit/Pest/RSpec-junit/mocha-junit/Robot/
gtest/Catch2/nextest/.NET-JUnitLogger/pytest-junitxml through one parser with
per-runner quirks: namespace-stripping, `class` vs `classname`, nested
testsuites, time-in-seconds vs missing times.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..keys import normalize


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(xml_bytes: bytes, runner: str = "junit") -> list[dict]:
    """Return observation dicts: key, raw_id, status, duration_ms, censored."""
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        if _local(el.tag) != "testcase":
            continue
        name = el.get("name", "") or ""
        if not name:
            continue
        # JUnit Platform's XmlReportWriter quirk: bare method names carry ()
        # (name="fast()"), unlike JUnit4/Maven Surefire ("fast"). Strip only the
        # exact bare-identifier shape so parameterized names survive intact.
        m = re.fullmatch(r"([A-Za-z_$][\w$]*)\(\)", name)
        if m:
            name = m.group(1)
        cls = el.get("classname") or el.get("class") or ""   # PHPUnit quirk
        # .NET JUnitLogger quirk: name is already fully qualified (starts with
        # classname) — never prepend in that case or identity doubles
        raw = name if (cls and name.startswith(cls)) else (
            f"{cls}.{name}" if cls else name)
        status = "passed"
        for child in el:
            t = _local(child.tag)
            if t in ("failure", "error"):
                status = "failed"
                break
            if t == "skipped":
                status = "skipped"
        t = float(el.get("time", 0) or 0)
        ms = t * 1000.0 if t < 600_000 else t   # seconds convention; ms fallback
        out.append({
            "key": normalize(runner, raw), "raw_id": raw,
            "status": status, "duration_ms": ms, "censored": False,
        })
    return out
