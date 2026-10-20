"""End-to-end: the agent-facing tool against the tiny fixture.

Pins the contract the timeout metric depends on:

  * the first call records and predicts nothing;
  * the second call's budget is measured (p90 x safety) and it finishes inside it;
  * a budget that is too low costs one budget and a resume, not the whole suite;
  * a stale/absent suffix cannot masquerade as a complete suite.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hh.tool import TOOL_SPEC, build_plan, handle_tool_call  # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "synth_tiny_7")


@unittest.skipUnless(os.path.isdir(FIXTURE), "fixtures not shipped in this build")
class TestToolEndToEnd(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="hh-e2e-")

    def tearDown(self):
        shutil.rmtree(self.store, ignore_errors=True)

    def call(self, **kw):
        args = {"root": FIXTURE, "store_root": self.store}
        args.update(kw)
        d = json.loads(handle_tool_call("run_tests", args))
        self.assertNotIn("error", d, d)
        return d

    def test_first_call_records_and_predicts_nothing(self):
        d = self.call()
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["operating_level"], "record")
        self.assertEqual(d["budget"]["basis"], "record")
        self.assertIsNone(d["predicted_wall_s"])        # no guess is offered
        self.assertEqual(d["budget"]["measured_tests"], 0)
        self.assertGreaterEqual(d["recorded"], 12)
        self.assertEqual(d["attempts"], 1)
        self.assertEqual(d["timed_out"], 0)

    def test_second_call_uses_a_measured_budget(self):
        self.call()                                     # the record pass
        warm = self.call()
        self.assertEqual(warm["operating_level"], "measured")
        self.assertEqual(warm["budget"]["basis"], "measured")
        self.assertEqual(warm["status"], "ok")
        self.assertEqual(warm["timed_out"], 0)          # no SIGKILL on a measured budget
        self.assertGreater(warm["budget"]["budget_s"], 0)
        self.assertGreater(warm["budget"]["coverage"], 0.99)
        # the budget is the p90 plus headroom, not the mean
        self.assertGreaterEqual(warm["budget"]["budget_s"],
                                warm["budget"]["p90_s"])
        self.assertLessEqual(warm["wall_s"] + 0.2, warm["budget"]["budget_s"])

    def test_budget_covers_the_suite_over_repeats(self):
        """The point of the metric: the measured budget keeps not killing it."""
        self.call()
        for _ in range(3):
            d = self.call()
            self.assertEqual(d["timed_out"], 0, d)
            self.assertLessEqual(d["wall_s"], d["budget"]["budget_s"])

    def test_a_budget_that_is_too_low_resumes_instead_of_restarting(self):
        self.call()                                     # record
        # force a budget far below the recorded suite duration
        plan = build_plan(FIXTURE, store_root=self.store)
        plan["shards"][0]["budget_s"] = 0.1
        plan["total_budget_s"] = 0.1
        from hh.tool import execute_plan
        res = execute_plan(plan, FIXTURE, store_root=self.store)
        self.assertGreaterEqual(res["stats"]["timed_out"], 1)   # it was killed
        self.assertEqual(res["stats"]["attempts"], 2)           # one resume, not a restart
        self.assertEqual(res["ingested"], 12)                   # nothing was lost
        self.assertGreater(res["stats"]["wasted_s"], 0)

    def test_unknown_tool_and_bad_args_return_errors_not_crashes(self):
        self.assertIn("error", json.loads(handle_tool_call("nope", {})))
        self.assertIn("error", json.loads(handle_tool_call("run_tests", {"workers": 2})))

    def test_spec_is_registerable(self):
        self.assertEqual(TOOL_SPEC["name"], "run_tests")
        props = TOOL_SPEC["parameters"]["properties"]
        self.assertEqual(TOOL_SPEC["parameters"]["required"], ["root"])
        for p in ("root", "runner", "budget_s", "resume"):
            self.assertIn(p, props)
        self.assertEqual(props["runner"]["enum"], ["unittest", "pytest", "node"])


@unittest.skipUnless(os.path.isdir(FIXTURE), "fixtures not shipped in this build")
class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT
        return subprocess.run([sys.executable, "-m", "hh", *args],
                              capture_output=True, text=True, env=env)

    def test_version(self):
        from hh import __version__
        p = self.run_cli("--version")
        self.assertEqual(p.stdout.strip(), f"hh {__version__}")

    def test_tool_spec_is_json_with_the_right_name(self):
        p = self.run_cli("tool-spec")
        self.assertEqual(json.loads(p.stdout)["name"], "run_tests")

    def test_plan_is_json_and_predicts_only_after_measuring(self):
        store = tempfile.mkdtemp(prefix="hh-cli-")
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = ROOT
            import subprocess
            p = subprocess.run([sys.executable, "-m", "hh", "--store-root", store,
                                "plan", "--root", FIXTURE],
                               capture_output=True, text=True, env=env)
            cold = json.loads(p.stdout)
            self.assertEqual(cold["operating_level"], "record")
            self.assertIsNone(cold["predicted_wall_s"])
            self.assertEqual(len(cold["shards"]), 1)
            self.assertTrue(cold["shards"][0]["full_suite"])
        finally:
            shutil.rmtree(store, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
