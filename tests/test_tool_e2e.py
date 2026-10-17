"""End-to-end: the agent-facing tool against the tiny fixture.

Pins the contract an agent depends on: run → ok + learned coverage; resume →
already-paid work recognised; impossible deadline → refusal with a receipt, not
a burnt budget.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hh.tool import TOOL_SPEC, handle_tool_call  # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "synth_tiny_7")


@unittest.skipUnless(os.path.isdir(FIXTURE), "fixtures not shipped in this build")
class TestToolEndToEnd(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="h8-e2e-")

    def tearDown(self):
        shutil.rmtree(self.store, ignore_errors=True)

    def call(self, **kw):
        args = {"root": FIXTURE, "workers": 2, "store_root": self.store}
        args.update(kw)
        d = json.loads(handle_tool_call("run_tests", args))
        self.assertNotIn("error", d, d)
        return d

    def test_cold_run_is_ok_and_learns_everything(self):
        d = self.call()
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["results"]["n_failed"], 0)
        self.assertGreaterEqual(d["results"]["ingested"], 12)
        self.assertEqual(d["estimate"]["coverage"], 0.0)          # cold
        self.assertGreater(d["learning"]["coverage_after"], 0.99)  # measured now
        self.assertIn("time_saved", d)

    def test_second_call_is_warm(self):
        self.call()
        d = self.call()
        self.assertEqual(d["estimate"]["coverage"], 1.0)

    def test_resume_does_not_re_pay_and_reports_it(self):
        self.call()
        d = self.call(resume=True)
        self.assertEqual(d["status"], "up_to_date")
        self.assertGreaterEqual(d["resumed_tests"], 12)
        kinds = [l["kind"] for l in d["time_saved"]["breakdown"]]
        self.assertIn("resume", kinds)
        self.assertGreater(d["time_saved"]["this_call_ms"], 0)

    def test_impossible_deadline_refuses_with_a_receipt(self):
        d = self.call(deadline_s=0.05, max_workers=2)
        self.assertEqual(d["status"], "deadline_infeasible")
        self.assertIn("deadline_decision", d)
        self.assertFalse(d["deadline_decision"]["feasible"])
        self.assertEqual([l["kind"] for l in d["time_saved"]["breakdown"]], ["refusal"])
        self.assertEqual(d["time_saved"]["this_call_ms"], 50.0)

    def test_unknown_tool_and_bad_args_return_errors_not_crashes(self):
        self.assertIn("error", json.loads(handle_tool_call("nope", {})))
        self.assertIn("error", json.loads(handle_tool_call("run_tests", {"bogus": 1})))

    def test_spec_is_registerable(self):
        self.assertEqual(TOOL_SPEC["name"], "run_tests")
        self.assertIn("root", TOOL_SPEC["parameters"]["properties"])
        self.assertIn("deadline_s", TOOL_SPEC["parameters"]["properties"])


class TestCli(unittest.TestCase):
    def test_version(self):
        out = subprocess.run([sys.executable, "-m", "hh", "--version"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0)
        self.assertRegex(out.stdout.strip(), r"^hh \d+\.\d+\.\d+$")

    def test_tool_spec_is_json_with_the_right_name(self):
        out = subprocess.run([sys.executable, "-m", "hh", "tool-spec"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(json.loads(out.stdout)["name"], "run_tests")

    @unittest.skipUnless(os.path.isdir(FIXTURE), "fixtures not shipped in this build")
    def test_plan_emits_pure_json_with_a_predicted_wall(self):
        tmp = tempfile.mkdtemp(prefix="h8-cli-")
        try:
            p = subprocess.run(
                [sys.executable, "-m", "hh", "--store-root", tmp, "plan",
                 "--root", FIXTURE, "--workers", "2"],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(p.returncode, 0, p.stderr)
            plan = json.loads(p.stdout)              # stdout must be pure JSON
            self.assertGreater(plan["predicted_wall_s"], 0)
            self.assertIn("deadline_decision", plan)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
