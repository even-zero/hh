"""Regression tests for native discovery, selection and kill-safe checkpoints."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from executor.tool import build_plan, execute_plan


class NativeAdapters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-native-")
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self.store = os.path.join(self.tmp, "store")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def put(self, name, content):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(content)

    def test_unittest_native_suffix_and_inherited_collisions(self):
        self.put("example_test.py", """import unittest
class Base(unittest.TestCase):
    def test_shared(self): pass
class Derived(Base):
    pass
""")
        cold = json.loads(json.dumps(build_plan(self.repo, store_root=self.store)))
        self.assertEqual(cold["operating_level"], "record")
        self.assertIsNone(cold["predicted_wall_s"])
        first = execute_plan(cold, self.repo, store_root=self.store)
        self.assertEqual(first["ingested"], 2)          # recorded, not predicted
        self.assertIsNone(first["stats"]["error"])
        # the pass seals a completeness marker: its ledger can be trusted as
        # the manifest for the next plan
        self.assertTrue(first["stats"]["suite_complete"])
        self.assertTrue(first["stats"]["suite_complete"])
        # second plan: measured, and it lists exactly the recorded tests
        p = json.loads(json.dumps(build_plan(self.repo, store_root=self.store)))
        self.assertNotEqual(p["operating_level"], "record")
        tests = [t for s in p["shards"] for t in s["tests"]]
        self.assertEqual(len(tests), 2)
        self.assertEqual(len({t["key"] for t in tests}), 2)
        result = execute_plan(p, self.repo, store_root=self.store)
        self.assertEqual(result["ingested"], 2)
        self.assertIsNone(result["stats"]["error"])

    @unittest.skipUnless(__import__("importlib").util.find_spec("pytest"), "pytest not installed")
    def test_pytest_respects_opt_in_markers_and_setup_skips(self):
        self.put("pyproject.toml", """[tool.pytest.ini_options]
addopts = ["-m", "not optin"]
markers = ["optin: only when explicitly requested"]
""")
        os.mkdir(os.path.join(self.repo, "tests"))
        self.put("tests/test_native.py", """import pytest
@pytest.mark.optin
def test_optin(): assert False
def test_ok(): assert True
@pytest.mark.skip(reason='optional')
def test_skip(): pass
""")
        cold = build_plan(self.repo, runner="pytest", store_root=self.store)
        self.assertEqual(cold["operating_level"], "record")
        first = execute_plan(cold, self.repo, runner="pytest", store_root=self.store)
        self.assertEqual(first["ingested"], 2)          # marker + skip honored
        self.assertEqual(first["stats"]["attempts"], 1)
        self.assertTrue(first["stats"]["suite_complete"])
        with open(first["ledger"]) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual({r["status"] for r in rows if r["type"] == "end"},
                         {"passed", "skipped"})
        p = build_plan(self.repo, runner="pytest", store_root=self.store)
        self.assertEqual(sum(len(s["tests"]) for s in p["shards"]), 2)
        res = execute_plan(p, self.repo, runner="pytest", store_root=self.store)
        self.assertEqual(res["ingested"], 2)
        self.assertEqual(res["stats"]["attempts"], 1)

    @unittest.skipUnless(__import__("importlib").util.find_spec("pytest"), "pytest not installed")
    def test_pytest_hard_kill_names_hanging_test(self):
        self.put("test_hang.py", "def test_never_finishes():\n    while True: pass\n")
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        env = dict(os.environ, HH_LEDGER=ledger, HH_START_GRACE_MS="5")
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__)) + os.pathsep + env.get("PYTHONPATH", "")
        p = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p",
                              "executor.adapters.pytest_plugin", "-p", "no:cacheprovider"],
                             cwd=self.repo, env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            limit = time.monotonic() + 4
            while time.monotonic() < limit:
                if os.path.exists(ledger) and os.path.getsize(ledger) > 0:
                    break
                time.sleep(.02)
            self.assertTrue(os.path.exists(ledger) and os.path.getsize(ledger) > 0,
                            "watchdog did not checkpoint the hang")
        finally:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        with open(ledger) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual([r["type"] for r in rows], ["start"])
        self.assertIn("never_finishes", rows[0]["raw_id"])


if __name__ == "__main__":
    unittest.main()
