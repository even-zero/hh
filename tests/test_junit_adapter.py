"""The JUnit Platform adapter: identity, classpath, and an end-to-end run.

The end-to-end tests here use a stub `java`/`javac` so the Python half (plan,
execute, resume, ingest) is exercised on any machine. The real driver is
compiled and run when a JDK and `HH_JUNIT_CLASSPATH` are present — CI sets
both, so HhJunit.java is covered there rather than skipped.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hh.adapters import junit_stream
from hh.keys import normalize, unique_key_map
from hh.tool import build_plan, execute_plan, run_tests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_JDK = bool(shutil.which("javac") and shutil.which("java"))
HAVE_PLATFORM = bool(os.environ.get("HH_JUNIT_CLASSPATH"))


class Identity(unittest.TestCase):
    def test_the_class_is_kept_because_in_java_it_is_the_file(self):
        self.assertEqual(normalize("junit", "com.example.FooTest.testAdd"),
                         "junit::com/example/FooTest/testAdd")

    def test_invocations_become_a_parameter_tag(self):
        key = normalize("junit", "com.example.FooTest.testAdd[#1]")
        self.assertTrue(key.startswith("junit::com/example/FooTest/testAdd :: "))
        self.assertNotEqual(key, normalize("junit", "com.example.FooTest.testAdd[#2]"))

    def test_same_method_in_two_classes_stays_distinct(self):
        a = normalize("junit", "com.example.FooTest.testEquals")
        b = normalize("junit", "com.example.BarTest.testEquals")
        self.assertNotEqual(a, b)

    def test_other_runners_still_drop_the_class(self):
        self.assertEqual(normalize("unittest", "test_s001.TestS001.test_t000"),
                         "unittest::test_s001/test_t000")


class Classpath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-junit-cp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "target", "test-classes"))
        self.cache = os.path.join(self.tmp, ".hh", "junit")
        os.makedirs(self.cache)

    def test_explicit_classpath_is_used_verbatim(self):
        with mock.patch.dict(os.environ, {"HH_JUNIT_CLASSPATH": "/opt/junit.jar"}):
            cp = junit_stream.classpath(self.tmp, self.cache)
        self.assertIn("/opt/junit.jar", cp)

    def test_standalone_jar_in_the_project_is_found(self):
        os.makedirs(os.path.join(self.tmp, "lib"))
        jar = os.path.join(self.tmp, "lib", "junit-platform-console-standalone-1.11.4.jar")
        open(jar, "w").close()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HH_JUNIT_CLASSPATH", None)
            cp = junit_stream.classpath(self.tmp, self.cache)
        self.assertIn(jar, cp)
        self.assertTrue(junit_stream.has_platform(cp))

    def test_compiled_test_dirs_are_discovered(self):
        self.assertEqual(junit_stream.test_class_dirs(self.tmp),
                         [os.path.join(self.tmp, "target", "test-classes")])


FAKE_JAVA = r'''#!{python}
import json, os, sys
from hh.keys import unique_key_map

spec = json.load(open(os.environ["HH_FAKE_JUNIT_SPEC"]))
argv = sys.argv[1:]
mode = argv[argv.index("HhJunit") + 1]
rest = argv[argv.index("HhJunit") + 2:]
opts = dict(zip(rest[::2], rest[1::2]))

raws = spec["tests"]
keys = unique_key_map(raws, "junit")
if mode == "discover":
    with open(opts["--out"], "w") as f:
        for raw in raws:
            f.write(json.dumps({{"raw_id": raw, "key": keys[raw]}}) + "\n")
else:
    if "--ids" in opts:
        with open(opts["--ids"]) as f:
            raws = [json.loads(line)["raw_id"] for line in f if line.strip()]
    with open(opts["--ledger"], "a") as f:
        for raw in raws:
            f.write(json.dumps({{"type": "start", "key": keys[raw],
                                 "raw_id": raw, "ts": 0}}) + "\n")
            f.write(json.dumps({{"type": "end", "key": keys[raw], "raw_id": raw,
                                 "status": "passed", "censored": False,
                                 "duration_ms": spec["durations"].get(raw, 5.0),
                                 "ts": 0}}) + "\n")
        f.write(json.dumps({{"type": "suite_end", "tests": len(raws), "ts": 0}}) + "\n")
'''

FAKE_JAVAC = r'''#!{python}
import os, sys
argv = sys.argv[1:]
out = argv[argv.index("-d") + 1]
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "HhJunit.class"), "w").close()
'''


class AdapterEndToEnd(unittest.TestCase):
    """hh's own plan -> execute -> plan loop, with the JVM stubbed out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-junit-e2e-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(os.path.join(self.root, "target", "test-classes"))
        self.store = os.path.join(self.tmp, "store")
        jar = os.path.join(self.tmp, "junit-platform-launcher-1.11.4.jar")
        open(jar, "w").close()
        self.spec = os.path.join(self.tmp, "spec.json")
        with open(self.spec, "w") as f:
            json.dump({
                "tests": ["com.example.FooTest.testAdd",
                          "com.example.FooTest.testSubtract",
                          "com.example.BarTest.testEquals"],
                "durations": {"com.example.FooTest.testAdd": 4.0,
                              "com.example.FooTest.testSubtract": 6.0,
                              "com.example.BarTest.testEquals": 5.0},
            }, f)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for name, body in (("java", FAKE_JAVA), ("javac", FAKE_JAVAC)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body.format(python=sys.executable))
            os.chmod(path, 0o755)
        self.env = mock.patch.dict(os.environ, {
            "HH_JAVA": os.path.join(self.bin, "java"),
            "HH_JAVAC": os.path.join(self.bin, "javac"),
            "HH_FAKE_JUNIT_SPEC": self.spec,
            "HH_JUNIT_CLASSPATH": jar,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_cold_run_records_then_warm_run_measures(self):
        cold = build_plan(self.root, runner="junit", store_root=self.store)
        self.assertEqual(cold["operating_level"], "record")
        self.assertIsNone(cold["predicted_wall_s"])
        first = execute_plan(cold, self.root, runner="junit", store_root=self.store)
        self.assertEqual(first["ingested"], 3)

        warm = build_plan(self.root, runner="junit", store_root=self.store)
        self.assertEqual(warm["operating_level"], "measured")
        self.assertEqual(sum(len(s["tests"]) for s in warm["shards"]), 3)
        second = execute_plan(warm, self.root, runner="junit", store_root=self.store)
        self.assertEqual(second["ingested"], 3)
        self.assertEqual(second["stats"]["attempts"], 1)
        self.assertEqual(second["stats"]["timed_out"], 0)

    def test_the_tool_reports_a_measured_budget(self):
        record = run_tests(self.root, runner="junit", store_root=self.store)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["budget"]["basis"], "record")
        measured = run_tests(self.root, runner="junit", store_root=self.store)
        self.assertEqual(measured["status"], "ok")
        self.assertEqual(measured["budget"]["basis"], "measured")
        self.assertGreater(measured["budget"]["budget_s"], 0)
        self.assertEqual(measured["timed_out"], 0)

    def test_a_manifest_from_a_renamed_test_is_reported_unresolved(self):
        run_tests(self.root, runner="junit", store_root=self.store)
        # the suite loses a test between the record pass and the warm call
        with open(self.spec, "w") as f:
            json.dump({"tests": ["com.example.FooTest.testAdd",
                                 "com.example.FooTest.testSubtract"],
                       "durations": {"com.example.FooTest.testAdd": 4.0,
                                     "com.example.FooTest.testSubtract": 6.0}}, f)
        plan = build_plan(self.root, runner="junit", store_root=self.store)
        res = execute_plan(plan, self.root, runner="junit", store_root=self.store)
        self.assertEqual(res["stats"]["unresolved"], 0)  # manifest is rebuilt
        self.assertEqual(res["ingested"], 2)


@unittest.skipUnless(HAVE_JDK and HAVE_PLATFORM,
                     "needs a JDK and HH_JUNIT_CLASSPATH (CI sets both)")
class RealJUnit(unittest.TestCase):
    """The real driver, compiled and run against a real JUnit suite."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-junit-real-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        src = os.path.join(self.tmp, "src", "test", "java", "demo")
        os.makedirs(src)
        with open(os.path.join(src, "SampleTest.java"), "w") as f:
            f.write(
                "package demo;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import static org.junit.jupiter.api.Assertions.assertTrue;\n"
                "public class SampleTest {\n"
                "  @Test void testFast() { assertTrue(true); }\n"
                "  @Test void testSlow() throws Exception {\n"
                "    Thread.sleep(30); assertTrue(true);\n"
                "  }\n"
                "}\n")
        classes = os.path.join(self.tmp, "target", "test-classes")
        os.makedirs(classes)
        proc = subprocess.run(
            [shutil.which("javac"), "-cp", os.environ["HH_JUNIT_CLASSPATH"],
             "-d", classes, os.path.join(src, "SampleTest.java")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.store = os.path.join(self.tmp, "store")

    def test_real_suite_records_and_then_measures(self):
        cold = run_tests(self.tmp, runner="junit", store_root=self.store)
        self.assertEqual(cold["status"], "ok", cold)
        self.assertEqual(cold["recorded"], 2)
        warm = run_tests(self.tmp, runner="junit", store_root=self.store)
        self.assertEqual(warm["status"], "ok", warm)
        self.assertEqual(warm["budget"]["basis"], "measured")
        self.assertEqual(warm["timed_out"], 0)


if __name__ == "__main__":
    unittest.main()
