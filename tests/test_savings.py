"""The utility receipt: conservative credits only, unbounded waits noted."""
import os
import tempfile
import unittest

from hh import savings


class TestCreditLines(unittest.TestCase):
    def test_nothing_at_risk_earns_zero(self):
        lines, notes = savings.credit_lines({}, wall_s=1.0, caller_limit_s=60, censored=0)
        self.assertEqual(lines, [])
        self.assertEqual(notes, [])

    def test_resume_credited_from_stats(self):
        lines, _ = savings.credit_lines({"resumed": 108, "resumed_ms": 26500.0},
                                        wall_s=0.003, caller_limit_s=None, censored=0)
        self.assertEqual([l["kind"] for l in lines], ["resume"])
        self.assertEqual(lines[0]["ms"], 26500.0)

    def test_continuation_credited_from_stats(self):
        lines, _ = savings.credit_lines({"preserved_tests": 6, "preserved_ms": 17800.0},
                                        wall_s=13.0, caller_limit_s=None, censored=0)
        self.assertEqual([l["kind"] for l in lines], ["continuation"])

    def test_hang_credited_only_up_to_the_caller_deadline(self):
        lines, notes = savings.credit_lines({"censored": 3}, wall_s=8.0,
                                            caller_limit_s=60, censored=3)
        self.assertEqual(lines[0]["kind"], "hang_bound")
        self.assertEqual(lines[0]["ms"], (60 - 8.0) * 1000.0)
        self.assertLessEqual(lines[0]["ms"], 60_000)
        self.assertEqual(notes, [])

    def test_hang_without_caller_deadline_credits_bounded_escape(self):
        # No deadline: the unbounded wait is never summed, but the *measured*
        # escape (cutoff − wait actually spent) is credited as a lower bound.
        lines, notes = savings.credit_lines({}, wall_s=8.0, caller_limit_s=None,
                                            censored=3, censored_wait_s=6.0,
                                            cutoff_s=12.0)
        self.assertEqual([l["kind"] for l in lines], ["hang_escape"])
        self.assertEqual(lines[0]["ms"], (3 * 12.0 - 6.0) * 1000.0)
        self.assertTrue(any("UNBOUNDED" in n for n in notes))

    def test_hang_escape_never_negative(self):
        lines, _ = savings.credit_lines({}, wall_s=8.0, caller_limit_s=None,
                                        censored=1, censored_wait_s=30.0,
                                        cutoff_s=12.0)
        self.assertEqual([l for l in lines if l["kind"] == "hang_escape"], [])

    def test_block_reports_net_of_overhead(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="h8-net-")
        try:
            b = savings.block(tmp, "j", [{"kind": "resume", "ms": 5000.0, "basis": "b"}],
                              [], wall_s=1.0, overhead_s=0.5)
            self.assertEqual(b["overhead_s"], 0.5)
            self.assertEqual(b["net_s"], 4.5)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refusal_lines(self):
        lines = savings.refusal_lines("declared bound 35s > caller budget 20s", 20)
        self.assertEqual(lines[0]["kind"], "refusal")
        self.assertEqual(lines[0]["ms"], 20_000.0)
        self.assertEqual(savings.refusal_lines("x", None), [])


class TestRecordAndTotals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h8-savings-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_totals_and_jsonl_mirror(self):
        lines = [{"kind": "resume", "ms": 1000.0, "basis": "b1"},
                 {"kind": "hang_bound", "ms": 2500.0, "basis": "b2"}]
        savings.record(self.tmp, job="jobA", suite_id="suite1", lines=lines)
        t = savings.totals(self.tmp, "jobA")
        self.assertAlmostEqual(t["total_ms"], 3500.0)
        self.assertAlmostEqual(t["total_s"], 3.5)
        self.assertEqual(t["suites"], 1)
        self.assertEqual({k["kind"] for k in t["by_kind"]}, {"resume", "hang_bound"})
        mirror = os.path.join(self.tmp, ".hh", "experiments", "savings.jsonl")
        self.assertTrue(os.path.exists(mirror))
        self.assertEqual(len(open(mirror).read().strip().splitlines()), 2)

    def test_jobs_are_isolated(self):
        savings.record(self.tmp, job="jobA",
                       suite_id="s", lines=[{"kind": "resume", "ms": 1.0, "basis": "x"}])
        savings.record(self.tmp, job="jobB",
                       suite_id="s", lines=[{"kind": "resume", "ms": 2.0, "basis": "x"}])
        self.assertEqual(savings.totals(self.tmp, "jobA")["total_ms"], 1.0)
        self.assertEqual(savings.totals(self.tmp, "jobB")["total_ms"], 2.0)

    def test_block_shape(self):
        savings.record(self.tmp, job="j", suite_id="s",
                       lines=[{"kind": "continuation", "ms": 500.0, "basis": "b"}])
        b = savings.block(self.tmp, "j", [{"kind": "continuation", "ms": 500.0,
                                           "basis": "b"}], [], wall_s=1.0)
        for key in ("this_call_ms", "job_total_s", "job_store", "breakdown", "how"):
            self.assertIn(key, b)
        self.assertEqual(b["this_call_s"], 0.5)
        self.assertIn("conservative", b["how"])

    def test_zero_lines_records_nothing(self):
        savings.record(self.tmp, job="empty", suite_id="s", lines=[])
        self.assertEqual(savings.totals(self.tmp, "empty")["total_ms"], 0.0)
        mirror = os.path.join(self.tmp, ".hh", "experiments", "savings.jsonl")
        self.assertFalse(os.path.exists(mirror))


if __name__ == "__main__":
    unittest.main()
