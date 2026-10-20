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

from hh.tool import TOOL_SPEC, build_plan, execute_plan, handle_tool_call  # noqa: E402

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

    def test_cold_run_is_a_record_pass_not_a_prediction(self):
        d = self.call()
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["results"]["n_failed"], 0)
        self.assertGreaterEqual(d["results"]["ingested"], 12)
        self.assertEqual(d["operating_level"], "record")          # naive-equivalent
        self.assertTrue(d["record_pass"])
        self.assertIsNone(d["predicted_wall_s"])                  # no guess offered
        self.assertEqual(d["estimate"]["coverage"], 0.0)          # cold
        self.assertGreater(d["learning"]["coverage_after"], 0.99)  # measured now
        self.assertIn("time_saved", d)

    def test_record_pass_seals_the_manifest_for_the_next_plan(self):
        first = self.call()
        cache = os.path.join(self.store, ".hh", "manifest-cache-unittest.json")
        self.assertTrue(os.path.exists(cache), "a finished record pass caches its manifest")
        warm = json.loads(build_plan(FIXTURE, workers=2, store_root=self.store).to_json())
        self.assertNotEqual(warm["operating_level"], "record")
        self.assertEqual(warm["manifest_size"], first["results"]["ingested"])
        self.assertEqual(sum(len(s["tests"]) for s in warm["shards"]),
                         first["results"]["ingested"])

    def test_partial_record_pass_never_seals_a_manifest(self):
        # An adapter that dies without running the suite (no suite_end marker)
        # must not leave a partial manifest behind for the next plan to trust.
        fake = tempfile.mkdtemp(prefix="h8-dead-")
        try:
            with open(os.path.join(fake, "hh_dead_adapter.py"), "w") as f:
                f.write("import sys\nsys.exit(0)\n")
            plan = json.loads(build_plan(FIXTURE, workers=1,
                                         store_root=self.store).to_json())
            self.assertEqual(plan["operating_level"], "record")
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = fake + (os.pathsep + old if old else "")
            try:
                res = execute_plan(plan, FIXTURE, store_root=self.store,
                                   adapter="hh_dead_adapter")
            finally:
                if old is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old
            self.assertFalse(res["complete"])
            self.assertFalse(res["stats"]["suite_complete"])
            self.assertEqual(res["stats"]["shard_failures"], 1)
            self.assertFalse(os.path.exists(
                os.path.join(self.store, ".hh", "manifest-cache-unittest.json")))
            again = json.loads(build_plan(FIXTURE, workers=1,
                                          store_root=self.store).to_json())
            self.assertEqual(again["operating_level"], "record")   # still cold
        finally:
            shutil.rmtree(fake, ignore_errors=True)

    def test_record_pass_timeout_follows_the_naive_ladder(self):
        tmp = tempfile.mkdtemp(prefix="h8-slow-")
        try:
            with open(os.path.join(tmp, "test_slow.py"), "w") as f:
                f.write("import time, unittest\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_slow(self):\n"
                        "        time.sleep(1.2)\n")
            plan = json.loads(build_plan(tmp, workers=1, store_root=self.store,
                                         record_budget_s=0.5).to_json())
            self.assertEqual(plan["operating_level"], "record")
            self.assertEqual(plan["shards"][0]["budget_s"], 0.5)
            res = execute_plan(plan, tmp, store_root=self.store)
            # killed at the first rung, then doubled exactly like naive; the
            # retry re-runs the suite (no suite_end on the killed attempt)
            self.assertEqual(res["stats"]["attempts"], 2)
            self.assertTrue(res["complete"])
            self.assertFalse(res["stats"]["incomplete"])
            self.assertEqual(res["ingested"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

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
        self.call()                                   # a deadline needs measurements
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
    def test_plan_is_json_and_predicts_only_after_measuring(self):
        tmp = tempfile.mkdtemp(prefix="h8-cli-")
        try:
            def plan():
                p = subprocess.run(
                    [sys.executable, "-m", "hh", "--store-root", tmp, "plan",
                     "--root", FIXTURE, "--workers", "2"],
                    capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(p.returncode, 0, p.stderr)
                return json.loads(p.stdout)          # stdout must be pure JSON

            cold = plan()
            self.assertEqual(cold["operating_level"], "record")
            self.assertIsNone(cold["predicted_wall_s"])     # no cold guess
            self.assertEqual(cold["measured_tests"], 0)

            # record the suite, then plan again: now it predicts from rows
            p = subprocess.run(
                [sys.executable, "-m", "hh", "--store-root", tmp, "run",
                 "--plan", os.path.join(tmp, "plan.json"), "--root", FIXTURE],
                capture_output=True, text=True, cwd=ROOT, env=dict(os.environ))
            self.assertNotEqual(p.returncode, 0)            # plan file absent
            plan_path = os.path.join(tmp, "cold_plan.json")
            subprocess.run([sys.executable, "-m", "hh", "--store-root", tmp,
                            "plan", "--root", FIXTURE, "--workers", "2",
                            "--out", plan_path],
                           capture_output=True, text=True, cwd=ROOT, check=True)
            subprocess.run([sys.executable, "-m", "hh", "--store-root", tmp,
                            "run", "--plan", plan_path, "--root", FIXTURE],
                           capture_output=True, text=True, cwd=ROOT, check=True)
            warm = plan()
            self.assertGreater(warm["measured_tests"], 0)
            self.assertGreater(warm["predicted_wall_s"] or 0, 0)
            self.assertIn("deadline_decision", warm)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
