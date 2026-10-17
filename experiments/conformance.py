"""Adapter conformance matrix (M4): every Tier B parser against recorded
fixture outputs from real runners, checked against golden expectations.

  python3 experiments/conformance.py

Covers: Surefire(JVM) · Gradle(JUnit shape) · TestNG · PHPUnit · RSpec(Ruby) ·
gtest(C++) · Catch2 · cargo-nextest(Rust) · mocha-junit(JS) · pytest-junitxml ·
.NET JUnitLogger · Go -json · VSTest TRX. Live adapters (jest/mocha/node:test
reporters) are exercised separately when the toolchain is present.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from hh.adapters import go_test, junit_xml, trx  # noqa: E402

PARSERS = {
    "junit_surefire.xml": ("junit", junit_xml.parse),      # Maven Surefire
    "junit_phpunit.xml": ("junit", junit_xml.parse),       # PHP (class-attr quirk)
    "junit_rspec.xml": ("junit", junit_xml.parse),         # Ruby RSpec
    "junit_gtest.xml": ("junit", junit_xml.parse),         # GoogleTest
    "junit_pytest.xml": ("junit", junit_xml.parse),        # pytest --junitxml
    "junit_testng.xml": ("junit", junit_xml.parse),        # TestNG
    "junit_nextest.xml": ("junit", junit_xml.parse),       # cargo-nextest
    "junit_mocha.xml": ("junit", junit_xml.parse),         # mocha-junit-reporter
    "junit_catch2.xml": ("junit", junit_xml.parse),        # Catch2
    "junit_dotnet.xml": ("junit", junit_xml.parse),        # .NET JUnit logger
    "junit_jupiter_parens.xml": ("junit", junit_xml.parse),  # JUnit Platform "fake()" quirk
    "go_test.jsonl": ("go", go_test.parse),                # go test -json
    "trx_sample.trx": ("trx", trx.parse),                  # VSTest TRX
}
TOL_MS = 0.01


def run(live_js: bool = False, live_go: bool = False,
        live_jvm: bool = False) -> int:
    rec = os.path.join(ROOT, "fixtures", "recorded")
    expected = json.load(open(os.path.join(rec, "expected.json")))
    rows, failed = [], 0
    for fname, (runner, fn) in PARSERS.items():
        got = fn(open(os.path.join(rec, fname), "rb").read())
        exp = expected[fname]
        ok_n = len(got) == len(exp)
        ok_rows, ok_keys = True, len({g["key"] for g in got}) == len(got)
        for g, (raw, status, ms) in zip(got, exp):
            if (abs(g["duration_ms"] - ms) > TOL_MS or g["status"] != status
                    or g["raw_id"] != raw.replace("\\\\", "\\")):
                ok_rows = False
        verdict = "PASS" if ok_n and ok_rows and ok_keys else "FAIL"
        failed += verdict == "FAIL"
        rows.append((fname, runner, len(got), ok_n, ok_rows, ok_keys, verdict))
        if verdict == "FAIL":
            print(f"  !! {fname}: got {[ (g['raw_id'], g['status'], g['duration_ms']) for g in got ]}")
    w = max(len(r[0]) for r in rows) + 2
    print(f"{'fixture':{w}} {'runner':7} {'rows':>4} {'n':>3} {'rows✓':>6} {'keys✓':>6}  verdict")
    for fname, runner, n, ok_n, ok_rows, ok_keys, verdict in rows:
        print(f"{fname:{w}} {runner:7} {n:>4} {'✓' if ok_n else '✗':>3}"
              f" {'✓' if ok_rows else '✗':>6} {'✓' if ok_keys else '✗':>6}  {verdict}")
    print(f"\nconformance: {len(rows) - failed}/{len(rows)} parser fixtures PASS")
    if live_js:
        print("\nlive JS (real jest + mocha runs through hh reporters):")
        import js_live_conformance
        failed += js_live_conformance.live_js_matrix()
    if live_go:
        print("\nlive Go (go test -json through the streaming wrapper):")
        import live_other_toolchains
        failed += live_other_toolchains.live_go_matrix()
    if live_jvm:
        print("\nlive JVM (real JUnit Platform run, listener + Surefire XML):")
        import live_other_toolchains
        failed += live_other_toolchains.live_jvm_matrix()
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(run(live_js="--live-js" in args or "--live-all" in args,
                 live_go="--live-go" in args or "--live-all" in args,
                 live_jvm="--live-jvm" in args or "--live-all" in args))
