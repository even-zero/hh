"""Recorded-output conformance for the Tier-B parsers (one per runner family).

Fixtures are real-shaped outputs from Surefire, PHPUnit, RSpec, GoogleTest,
Catch2, cargo-nextest, mocha-junit, pytest-xml, .NET's JUnit logger, TestNG,
JUnit Platform (paren quirk), `go test -json` and VSTest TRX.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
REC = os.path.join(ROOT, "fixtures", "recorded")

TOL_MS = 0.01


@unittest.skipUnless(os.path.isdir(REC), "recorded fixtures not shipped")
class TestRecordedParsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from experiments.conformance import PARSERS          # parser registry
        cls.parsers = PARSERS
        cls.expected = json.load(open(os.path.join(REC, "expected.json")))

    def _check(self, fname):
        runner, fn = self.parsers[fname]
        rows = fn(open(os.path.join(REC, fname), "rb").read())
        exp = self.expected[fname]
        self.assertEqual(len(rows), len(exp), f"{fname}: row count")
        self.assertEqual(len({r['key'] for r in rows}), len(rows), f"{fname}: keys unique")
        for got, (raw, status, ms) in zip(rows, exp):
            self.assertEqual(got["raw_id"], raw.replace("\\\\", "\\"), f"{fname}: raw_id")
            self.assertEqual(got["status"], status, f"{fname}: status for {raw}")
            self.assertAlmostEqual(got["duration_ms"], ms, delta=TOL_MS,
                                   msg=f"{fname}: duration for {raw}")

    def test_all_recorded_fixtures(self):
        self.assertTrue(self.parsers, "parser registry is empty")
        for fname in self.parsers:
            with self.subTest(fixture=fname):
                self._check(fname)

    def test_registry_covers_all_fixtures(self):
        on_disk = {f for f in os.listdir(REC) if f != "expected.json"}
        self.assertEqual(on_disk, set(self.parsers),
                         "every recorded fixture must be claimed by a parser")


if __name__ == "__main__":
    unittest.main()
