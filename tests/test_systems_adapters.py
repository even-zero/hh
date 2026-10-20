"""The systems runners: go, nextest, rspec, phpunit, ctest.

Two layers, cheapest first:

  * parsers, against the output shapes the tools document (always run);
  * the whole adapter driven by a **fake** tool on PATH — discovery, execution,
    ledger rows, resume skipping — so the driver logic is exercised even where
    the real toolchain is not installed;
  * the real tool, when it is installed (ctest locally; all five in CI's
    `systems` job, which fails if one of these skips).

The fake tools are small executables written to a temp dir and selected through
the adapters' `HH_*` variables. They are not mocks of the adapter's own code:
the adapter runs them as real subprocesses.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from executor import keys  # noqa: E402
from executor.adapters import registry  # noqa: E402
from executor.adapters import (ctest_stream, go_stream, nextest_stream,  # noqa: E402
                               phpunit_stream, rspec_stream)

SYSTEMS = ["go", "nextest", "rspec", "phpunit", "ctest"]

# Real toolchains are opt-in. The plain test job runs on a runner image that
# happens to ship some of these tools (an old phpunit, for one), and sniffing
# PATH there made the suite depend on the image rather than on the code. CI sets
# HH_REAL_TOOLS=1 in the `systems` job, which installs all five and fails if any
# real-tool test skips.
REAL_TOOLS = os.environ.get("HH_REAL_TOOLS") == "1"


def real_tool(name: str):
    """The real binary, or None when this run is not testing real toolchains."""
    return shutil.which(name) if REAL_TOOLS else None


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_tool(path: str, body: str) -> str:
    write(path, "#!/usr/bin/env python3\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def call(runner: str, *args: str, env: dict | None = None):
    """Run one adapter's CLI in a child process, like hh does."""
    e = dict(os.environ)
    e["PYTHONPATH"] = os.pathsep.join([ROOT, e.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", registry.module_name(runner), *args],
                          capture_output=True, text=True, env=e, cwd=ROOT)


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ends(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["type"] == "end"]


class TestRegistry(unittest.TestCase):
    def test_the_systems_runners_are_registered(self):
        for runner in SYSTEMS:
            self.assertIn(runner, registry.RUNNERS)
            self.assertFalse(registry.is_node(runner))
            self.assertTrue(registry.module_name(runner).endswith("_stream"))

    def test_keys_stay_per_file(self):
        # The file an aggregate falls back to must be stable for each runner.
        self.assertEqual(go_stream.key_for("example.com/m/pkg", "TestAdd"),
                         "go::example.com/m/pkg/TestAdd")
        self.assertEqual(keys.file_of(go_stream.key_for("example.com/m/pkg", "TestAdd")),
                         "example.com/m/pkg")
        self.assertEqual(keys.file_of(nextest_stream.key_for("demo::tests", "test_add")),
                         "demo/tests")
        self.assertEqual(keys.file_of(rspec_stream.key_for("./spec/a_spec.rb[1:2]")),
                         "spec/a_spec.rb")
        self.assertEqual(keys.file_of(phpunit_stream.key_for("Tests\\CalcTest::testAdd")),
                         "Tests/CalcTest")
        self.assertEqual(keys.file_of(ctest_stream.key_for("ok_a")), "ok_a")


class TestGoParsers(unittest.TestCase):
    LIST = """TestAdd
TestSub
BenchmarkFast
ExampleThing
ok  \texample.com/m/pkg\t0.012s
?   \texample.com/m/empty\t[no test files]
TestOnly
ok  \texample.com/m/other\t0.004s
"""

    def test_list_output(self):
        self.assertEqual(go_stream.parse_list_output(self.LIST), [
            ("example.com/m/pkg", "TestAdd"),
            ("example.com/m/pkg", "TestSub"),
            ("example.com/m/pkg", "ExampleThing"),
            ("example.com/m/other", "TestOnly"),
        ])

    def test_list_output_refuses_a_build_failure(self):
        with self.assertRaises(RuntimeError):
            go_stream.parse_list_output("FAIL\texample.com/m/bad [build failed]\n")

    def test_list_output_refuses_unattributed_names(self):
        with self.assertRaises(RuntimeError):
            go_stream.parse_list_output("TestOrphan\n")

    def test_json_events_become_rows(self):
        events = [
            {"Action": "start", "Package": "p"},
            {"Action": "run", "Package": "p", "Test": "TestAdd"},
            {"Action": "output", "Package": "p", "Test": "TestAdd", "Output": "hi\n"},
            {"Action": "pass", "Package": "p", "Test": "TestAdd", "Elapsed": 0.25},
            {"Action": "run", "Package": "p", "Test": "TestSub"},
            {"Action": "run", "Package": "p", "Test": "TestSub/child"},
            {"Action": "pass", "Package": "p", "Test": "TestSub/child", "Elapsed": 0.01},
            {"Action": "fail", "Package": "p", "Test": "TestSub", "Elapsed": 0.5},
            {"Action": "skip", "Package": "p", "Test": "TestLater", "Elapsed": 0.0},
        ]
        tmp = tempfile.mkdtemp(prefix="hh-go-")
        self.addCleanup(shutil.rmtree, tmp, True)
        ledger = os.path.join(tmp, "ledger.jsonl")
        lines = [json.dumps(e) for e in events]
        written, stats = go_stream.consume_events(lines, ledger, 0, [])
        rows = ends(read_jsonl(ledger))
        self.assertEqual(written, 3)
        self.assertEqual([r["raw_id"] for r in rows],
                         ["p::TestAdd", "p::TestSub", "p::TestLater"])
        self.assertEqual([r["status"] for r in rows], ["passed", "failed", "skipped"])
        self.assertEqual(rows[0]["duration_ms"], 250.0)
        self.assertEqual(rows[1]["key"], "go::p/TestSub")
        self.assertEqual(stats["build_fail"], [])


class TestGoWithFakeTool(unittest.TestCase):
    """The full adapter, driven by a fake `go` that speaks the real formats."""

    FAKE = '''
import json, sys
args = sys.argv[1:]
if args[:2] == ["test", "-list"]:
    print("TestAdd")
    print("TestFail")
    print("ok  \\texample.com/m\\t0.01s")
    sys.exit(0)
pkg = "example.com/m"
for ev in (
    {"Action": "run", "Package": pkg, "Test": "TestAdd"},
    {"Action": "pass", "Package": pkg, "Test": "TestAdd", "Elapsed": 0.01},
    {"Action": "run", "Package": pkg, "Test": "TestFail"},
    {"Action": "fail", "Package": pkg, "Test": "TestFail", "Elapsed": 0.02},
    {"Action": "fail", "Package": pkg},
):
    print(json.dumps(ev))
sys.exit(1)
'''

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-gofake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        write(os.path.join(self.root, "go.mod"), "module example.com/m\n")
        write(os.path.join(self.root, "pkg", "x_test.go"), "package pkg\n")
        self.go = write_tool(os.path.join(self.tmp, "bin", "go"), self.FAKE)
        self.ids = os.path.join(self.tmp, "ids.jsonl")

    def test_collect(self):
        res = call("go", "--root", self.root, "--collect-out", self.ids,
                   env={"HH_GO": self.go})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(self.ids)
        self.assertEqual(rows, [
            {"raw_id": "example.com/m::TestAdd", "key": "go::example.com/m/TestAdd"},
            {"raw_id": "example.com/m::TestFail", "key": "go::example.com/m/TestFail"},
        ])

    def test_run_and_resume(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = call("go", "--root", self.root, "--ledger", ledger,
                   env={"HH_GO": self.go})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([r["status"] for r in ends(rows)], ["passed", "failed"])
        self.assertEqual(rows[-1], {"type": "suite_end", "tests": 2,
                                    "ts": rows[-1]["ts"]})
        # A second attempt on the same ledger must not re-run finished tests.
        res = call("go", "--root", self.root, "--ledger", ledger,
                   env={"HH_GO": self.go})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual(len(ends(rows)), 2)
        self.assertEqual(rows[-1]["tests"], 0)

    def test_selection_runs_only_what_is_missing(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        sel = os.path.join(self.tmp, "selection.jsonl")
        write(sel, json.dumps({"raw_id": "example.com/m::TestAdd",
                               "key": "go::example.com/m/TestAdd"}) + "\n")
        res = call("go", "--root", self.root, "--ledger", ledger,
                   "--tests-file", sel, env={"HH_GO": self.go})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([r["raw_id"] for r in ends(rows)], ["example.com/m::TestAdd"])


class TestNextestParsers(unittest.TestCase):
    # `cargo nextest list --message-format json`: nextest-metadata's
    # TestListSummary, keyed by binary id (RustBinaryId: the package name for a
    # lib, "package::target" for an integration test).
    DOC = {"test-count": 3, "rust-build-meta": {"target-directory": "target"},
           "rust-suites": {
               "demo": {"package-name": "demo", "binary-name": "demo",
                        "binary-id": "demo", "kind": "lib",
                        "testcases": {"tests::test_add": {"ignored": False},
                                      "tests::test_ignored": {"ignored": True}}},
               "demo::it": {"package-name": "demo", "binary-name": "it",
                            "binary-id": "demo::it", "kind": "test",
                            "testcases": {"works": {}}},
               "demo::bench/b": {"package-name": "demo", "binary-name": "b",
                                 "binary-id": "demo::bench/b", "kind": "bench",
                                 "status": "skipped", "testcases": {"x": {}}},
           }}

    def test_list_skips_ignored_and_skipped_suites(self):
        self.assertEqual(sorted(nextest_stream.parse_list(self.DOC)), [
            ("demo", "tests::test_add"), ("demo::it", "works")])

    def test_suite_metadata_is_kept_for_the_event_names(self):
        suites = {s["binary_id"]: s for s in nextest_stream.parse_suites(self.DOC)}
        self.assertEqual((suites["demo"]["package"], suites["demo"]["binary"]),
                         ("demo", "demo"))
        self.assertEqual(suites["demo::it"]["binary"], "it")

    def test_unknown_schema_says_what_it_saw(self):
        with self.assertRaises(RuntimeError) as ctx:
            nextest_stream.parse_list({"whatever": 1})
        self.assertIn("whatever", str(ctx.exception))

    def test_raw_and_key(self):
        raw = nextest_stream.raw_for("demo::it", "mod::works")
        self.assertEqual(nextest_stream.split_raw(raw), ("demo::it", "mod::works"))
        self.assertEqual(nextest_stream.key_for("demo::it", "mod::works"),
                         "nextest::demo/it/mod/works")
        # the module path is the nearest useful aggregate for a Rust test
        self.assertEqual(keys.file_of("nextest::demo/it/mod/works"), "demo/it/mod")

    def test_filter_expression_is_grouped(self):
        expr = nextest_stream._filter_expression([("demo::it", "a"), ("demo", "b")])
        self.assertEqual(expr, '(binary_id(="demo::it") & test(="a")) | '
                               '(binary_id(="demo") & test(="b"))')

    def test_large_resumes_are_split_into_several_runs(self):
        targets = [("demo", "tests::t%04d" % i) for i in range(4000)]
        groups = nextest_stream._groups(targets)
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(g) for g in groups), len(targets))
        for group in groups:
            self.assertLessEqual(len(nextest_stream._filter_expression(group)),
                                 nextest_stream.MAX_FILTER_CHARS + 100)

    def test_events_are_libtest_lines(self):
        # nextest emits libtest-compatible JSON for `--message-format json`:
        # {"type":"test","event":"ok","name":"<package>::<binary>$<test>"}
        self.assertEqual(nextest_stream.parse_event(
            {"type": "test", "event": "ok", "name": "a"}), ("ok", "a"))
        self.assertEqual(nextest_stream.parse_event(
            {"type": "test", "event": "started", "name": "a"}), ("started", "a"))
        self.assertIsNone(nextest_stream.parse_event({"type": "suite", "event": "ok"}))
        self.assertIsNone(nextest_stream.parse_event({"type": "test", "event": "?"}))

    def test_event_names_map_back_to_list_ids(self):
        identity = nextest_stream.Identity(nextest_stream.parse_suites(self.DOC))
        self.assertEqual(identity.resolve("demo::demo$tests::test_add"),
                         (("demo", "tests::test_add"), "tests::test_add"))
        self.assertEqual(identity.resolve("demo::it$works"),
                         (("demo::it", "works"), "works"))
        # stress and retry suffixes are decoration, not part of the name
        self.assertEqual(identity.resolve("demo::demo@stress-2$tests::test_add#3"),
                         (("demo", "tests::test_add"), "tests::test_add"))
        # older nextest printed the bare test name; unambiguous ones still map
        self.assertEqual(identity.resolve("works"), (("demo::it", "works"), "works"))
        self.assertIsNone(identity.resolve("tests::not_listed")[0])


class TestNextestWithFakeTool(unittest.TestCase):
    FAKE = '''
import json, sys
args = sys.argv[1:]
if "list" in args:
    print(json.dumps({"test-count": 3, "rust-suites": {
        "demo": {"package-name": "demo", "binary-name": "demo", "binary-id": "demo",
                 "kind": "lib",
                 "testcases": {"tests::test_add": {"ignored": False},
                               "tests::test_ignored": {"ignored": True},
                               "tests::test_fail": {"ignored": False}}}}}))
    sys.exit(0)
if "-E" in args:
    open(%(argv)r, "w").write(" ".join(args))
for ev in (
    {"type": "suite", "event": "started", "test_count": 2},
    {"type": "test", "event": "started", "name": "demo::demo$tests::test_add"},
    {"type": "test", "event": "ok", "name": "demo::demo$tests::test_add",
     "exec_time": 0.02},
    {"type": "test", "event": "started", "name": "demo::demo$tests::test_fail"},
    {"type": "test", "event": "failed", "name": "demo::demo$tests::test_fail",
     "exec_time": 0.03, "stdout": "boom"},
    {"type": "suite", "event": "failed", "passed": 1, "failed": 1, "ignored": 0,
     "measured": 0, "filtered_out": 0, "exec_time": 0.05},
):
    print(json.dumps(ev))
sys.exit(100)
'''

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-nxfake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        write(os.path.join(self.root, "Cargo.toml"), "[package]\nname='demo'\n")
        self.argv_log = os.path.join(self.tmp, "argv.txt")
        self.cargo = write_tool(os.path.join(self.tmp, "bin", "cargo"),
                                self.FAKE % {"argv": self.argv_log})
        self.ids = os.path.join(self.tmp, "ids.jsonl")

    def test_collect_skips_ignored(self):
        res = call("nextest", "--root", self.root, "--collect-out", self.ids,
                   env={"HH_NEXTEST": self.cargo})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["key"] for r in read_jsonl(self.ids)],
                         ["nextest::demo/tests/test_add",
                          "nextest::demo/tests/test_fail"])

    def test_run_records_verdicts_once(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = call("nextest", "--root", self.root, "--ledger", ledger,
                   env={"HH_NEXTEST": self.cargo})
        # exit 100 means "tests failed"; that is data, not an adapter error
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([(r["raw_id"], r["status"]) for r in ends(rows)],
                         [("demo||tests::test_add", "passed"),
                          ("demo||tests::test_fail", "failed")])
        self.assertEqual(rows[-1]["tests"], 2)

    def test_a_finished_ledger_runs_nothing(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        env = {"HH_NEXTEST": self.cargo}
        call("nextest", "--root", self.root, "--ledger", ledger, env=env)
        before = read_jsonl(ledger)
        res = call("nextest", "--root", self.root, "--ledger", ledger, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([r for r in rows if r["type"] == "end"],
                         [r for r in before if r["type"] == "end"])
        self.assertEqual(rows[-1]["type"], "suite_end")
        self.assertEqual(rows[-1]["tests"], 0)

    def test_a_selection_narrows_the_run(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        sel = os.path.join(self.tmp, "selection.jsonl")
        write(sel, json.dumps({"raw_id": "demo||tests::test_add",
                               "key": "nextest::demo/tests/test_add"}) + "\n")
        res = call("nextest", "--root", self.root, "--ledger", ledger,
                   "--tests-file", sel,
                   env={"HH_NEXTEST": self.cargo})
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(self.argv_log, encoding="utf-8") as f:
            argv = f.read()
        self.assertIn("-E", argv)
        self.assertIn('binary_id(="demo") & test(="tests::test_add")', argv)
        self.assertNotIn("test_fail", argv)
        rows = read_jsonl(ledger)
        self.assertEqual([r["key"] for r in ends(rows)],
                         ["nextest::demo/tests/test_add"])


class TestRspecParsers(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(rspec_stream.key_for("./spec/a_spec.rb[1:2]"),
                         "rspec::spec/a_spec.rb/1:2")
        self.assertEqual(rspec_stream.key_for("spec/b_spec.rb[2:1:4]"),
                         "rspec::spec/b_spec.rb/2:1:4")

    def test_the_ruby_formatter_uses_the_same_scheme(self):
        """The formatter computes keys itself; the two must not drift."""
        with open(rspec_stream.FORMATTER, encoding="utf-8") as f:
            ruby = f.read()
        self.assertIn("def key_for(id)", ruby)
        self.assertIn('"rspec::#{path}/#{suffix}"', ruby)
        self.assertIn("path.sub(%r{\\A\\./}, '')", ruby)


class TestRspecWithFakeTool(unittest.TestCase):
    FAKE = '''
import json, os, sys
args = sys.argv[1:]
if "--dry-run" in args:
    out = args[args.index("--out") + 1]
    doc = {"examples": [
        {"id": "./spec/a_spec.rb[1:1]", "full_description": "A adds"},
        {"id": "./spec/a_spec.rb[1:2]", "full_description": "A fails"},
    ]}
    open(out, "w").write(json.dumps(doc))
    sys.exit(0)
ledger = os.environ["HH_RSPEC_LEDGER"]
rows = [
    {"raw_id": "./spec/a_spec.rb[1:1]", "key": "rspec::spec/a_spec.rb/1:1",
     "status": "passed", "ms": 5.0},
    {"raw_id": "./spec/a_spec.rb[1:2]", "key": "rspec::spec/a_spec.rb/1:2",
     "status": "failed", "ms": 7.0},
]
targets = [a for a in args if a.startswith("./spec/")]
for row in rows:
    if row["raw_id"] in targets:
        with open(ledger, "a") as f:
            f.write(json.dumps({"type": "end", "key": row["key"],
                                "raw_id": row["raw_id"], "status": row["status"],
                                "duration_ms": row["ms"], "censored": False,
                                "ts": 1.0}) + "\\n")
sys.exit(1)
'''

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-rsfake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        write(os.path.join(self.root, "spec", "a_spec.rb"), "RSpec.describe('a') {}\n")
        self.rspec = write_tool(os.path.join(self.tmp, "bin", "rspec"), self.FAKE)
        self.ids = os.path.join(self.tmp, "ids.jsonl")

    def test_collect(self):
        res = call("rspec", "--root", self.root, "--collect-out", self.ids,
                   env={"HH_RSPEC": self.rspec})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["key"] for r in read_jsonl(self.ids)],
                         ["rspec::spec/a_spec.rb/1:1", "rspec::spec/a_spec.rb/1:2"])

    def test_run_counts_only_rows_the_ledger_received(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = call("rspec", "--root", self.root, "--ledger", ledger,
                   env={"HH_RSPEC": self.rspec})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([r["status"] for r in ends(rows)], ["passed", "failed"])
        self.assertEqual(rows[-1]["tests"], 2)

        sel = os.path.join(self.tmp, "sel.jsonl")
        write(sel, json.dumps({"raw_id": "./spec/a_spec.rb[1:2]",
                               "key": "rspec::spec/a_spec.rb/1:2"}) + "\n")
        ledger2 = os.path.join(self.tmp, "ledger2.jsonl")
        res = call("rspec", "--root", self.root, "--ledger", ledger2,
                   "--tests-file", sel, env={"HH_RSPEC": self.rspec})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["raw_id"] for r in ends(read_jsonl(ledger2))],
                         ["./spec/a_spec.rb[1:2]"])


class TestPhpunitParsers(unittest.TestCase):
    def test_teamcity_messages(self):
        line = ("##teamcity[testStarted name='testAdd' "
                "locationHint='php_qn:///app/tests/CalcTest.php::\\Tests\\CalcTest::testAdd' "
                "captureStandardOutput='true']")
        self.assertEqual(phpunit_stream.parse_teamcity(line),
                         ("testStarted", {"name": "testAdd",
                                          "locationHint": "php_qn:///app/tests/CalcTest.php::\\Tests\\CalcTest::testAdd",
                                          "captureStandardOutput": "true"}))
        self.assertIsNone(phpunit_stream.parse_teamcity("PHPUnit 11.5.0 by x"))

    def test_escaped_values(self):
        self.assertEqual(phpunit_stream.parse_teamcity(
            "##teamcity[testFailed name='it|'s' message='a|nb' duration='3']"),
            ("testFailed", {"name": "it's", "message": "a\nb", "duration": "3"}))

    def test_hint_becomes_the_test_id(self):
        hint = "php_qn:///app/tests/CalcTest.php::\\Tests\\CalcTest::testAdd"
        self.assertEqual(phpunit_stream.fqn_from_hint(hint),
                         "Tests\\CalcTest::testAdd")
        data_set = ("php_qn:///app/tests/CalcTest.php::\\Tests\\CalcTest::"
                    "testAdd with data set #0")
        self.assertEqual(phpunit_stream.fqn_from_hint(data_set),
                         "Tests\\CalcTest::testAdd")
        self.assertIsNone(phpunit_stream.fqn_from_hint("nope"))

    def test_keys_and_filter(self):
        self.assertEqual(phpunit_stream.key_for("\\Tests\\Unit\\CalcTest::testAdd"),
                         "phpunit::Tests/Unit/CalcTest/testAdd")
        pattern = phpunit_stream._filter_pattern(["Tests\\CalcTest::testAdd"])
        self.assertEqual(pattern, r"/^(?:Tests\\CalcTest::testAdd)(?![A-Za-z0-9_])/")


class TestPhpunitWithFakeTool(unittest.TestCase):
    FAKE = '''
import sys
args = sys.argv[1:]
if "--list-tests-xml" in args:
    path = args[args.index("--list-tests-xml") + 1]
    open(path, "w").write("""<?xml version="1.0"?>
<testSuite xmlns="https://xml.phpunit.de/testSuite">
 <tests>
  <testClass name="Tests\\\\CalcTest" file="/tmp/CalcTest.php">
   <testMethod id="Tests\\\\CalcTest::testAdd" name="testAdd"/>
   <testMethod id="Tests\\\\CalcTest::testFail" name="testFail"/>
  </testClass>
 </tests>
</testSuite>
""")
    sys.exit(0)
print("##teamcity[testCount count='2']")
print("##teamcity[testStarted name='testAdd' locationHint='php_qn:///tmp/CalcTest.php::\\\\Tests\\\\CalcTest::testAdd']")
print("##teamcity[testFinished name='testAdd' duration='5']")
print("##teamcity[testStarted name='testFail' locationHint='php_qn:///tmp/CalcTest.php::\\\\Tests\\\\CalcTest::testFail']")
print("##teamcity[testFailed name='testFail' message='no' duration='7']")
print("##teamcity[testFinished name='testFail' duration='7']")
sys.exit(1)
'''

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hh-phpfake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        write(os.path.join(self.root, "tests", "CalcTest.php"), "<?php\n")
        self.phpunit = write_tool(os.path.join(self.tmp, "bin", "phpunit"), self.FAKE)
        self.ids = os.path.join(self.tmp, "ids.jsonl")

    def test_collect_and_run(self):
        res = call("phpunit", "--root", self.root, "--collect-out", self.ids,
                   env={"HH_PHPUNIT": self.phpunit})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["key"] for r in read_jsonl(self.ids)],
                         ["phpunit::Tests/CalcTest/testAdd",
                          "phpunit::Tests/CalcTest/testFail"])

        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = call("phpunit", "--root", self.root, "--ledger", ledger,
                   env={"HH_PHPUNIT": self.phpunit})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual([(r["status"], r["duration_ms"]) for r in ends(rows)],
                         [("passed", 5.0), ("failed", 7.0)])
        self.assertEqual(rows[-1]["tests"], 2)


class TestCtestParsers(unittest.TestCase):
    def test_summary_lines(self):
        cases = [
            ("1/4 Test #1: ok_a .............................   Passed    0.00 sec",
             "ok_a", "passed"),
            ("1/2 Test #2: ok_b .............................***Failed    0.01 sec",
             "ok_b", "failed"),
            ("4/4 Test #4: disabled_d .......................***Not Run (Disabled)   0.00 sec",
             "disabled_d", "skipped"),
            ("3/3 Test #3: a test with spaces ..............***Timeout   5.00 sec",
             "a test with spaces", "failed"),
        ]
        for line, name, status in cases:
            m = ctest_stream.SUMMARY.match(line)
            self.assertIsNotNone(m, line)
            self.assertEqual(m.group("name").strip(), name)
            self.assertEqual(ctest_stream._row_status(m.group("status")), status)


class TestCtestReal(unittest.TestCase):
    """The one systems runner that can be exercised here without a toolchain install."""

    CMAKE = """cmake_minimum_required(VERSION 3.20)
project(probe C)
enable_testing()
foreach(t ok_a ok_b)
  add_executable(${t} src/${t}.c)
  add_test(NAME ${t} COMMAND ${t})
endforeach()
add_test(NAME disabled_d COMMAND ok_a)
set_tests_properties(disabled_d PROPERTIES DISABLED TRUE)
"""

    def setUp(self):
        if not (real_tool("cmake") and real_tool("ctest")):
            self.skipTest("needs cmake and ctest")
        self.tmp = tempfile.mkdtemp(prefix="hh-ctest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        write(os.path.join(self.tmp, "CMakeLists.txt"), self.CMAKE)
        write(os.path.join(self.tmp, "src", "ok_a.c"),
              "#include <stdio.h>\nint main(void){return 0;}\n")
        write(os.path.join(self.tmp, "src", "ok_b.c"),
              "#include <stdio.h>\nint main(void){return 0;}\n")
        build = os.path.join(self.tmp, "build")
        for cmd in (["cmake", "-S", self.tmp, "-B", build],
                    ["cmake", "--build", build]):
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                self.skipTest("could not configure/build the CTest fixture: "
                              + (res.stdout + res.stderr)[-500:])

    def test_collect_run_and_resume_through_hh(self):
        from executor.tool import build_plan, execute_plan

        store = os.path.join(self.tmp, "store")
        ids = os.path.join(self.tmp, "ids.jsonl")
        res = call("ctest", "--root", self.tmp, "--collect-out", ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        # The DISABLED test is not in the manifest: CTest never runs it.
        self.assertEqual([r["raw_id"] for r in read_jsonl(ids)], ["ok_a", "ok_b"])

        cold = build_plan(self.tmp, runner="ctest", store_root=store)
        self.assertEqual(cold["operating_level"], "record")
        res = execute_plan(cold, self.tmp, runner="ctest", store_root=store)
        self.assertTrue(res["stats"]["suite_complete"], res)
        self.assertEqual(res["ingested"], 2)

        warm = build_plan(self.tmp, runner="ctest", store_root=store)
        self.assertEqual(warm["manifest_size"], 2)
        self.assertEqual(warm["measured_tests"], 2)


class TestRealTools(unittest.TestCase):
    """Small real suites, when the toolchain is installed (CI installs them all)."""

    def test_go(self):
        go = real_tool("go")
        if not go:
            self.skipTest("needs the Go toolchain")
        tmp = tempfile.mkdtemp(prefix="hh-go-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, "go.mod"), "module example.com/m\n\ngo 1.20\n")
        write(os.path.join(tmp, "a_test.go"),
              "package m\n\nimport \"testing\"\n\n"
              "func TestAdd(t *testing.T) { if 1+1 != 2 { t.Fatal(\"no\") } }\n"
              "func TestFail(t *testing.T) { t.Fatal(\"on purpose\") }\n")
        ids = os.path.join(tmp, "ids.jsonl")
        res = call("go", "--root", tmp, "--collect-out", ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(sorted(r["key"] for r in read_jsonl(ids)),
                         ["go::example.com/m/TestAdd", "go::example.com/m/TestFail"])
        ledger = os.path.join(tmp, "ledger.jsonl")
        res = call("go", "--root", tmp, "--ledger", ledger)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = ends(read_jsonl(ledger))
        self.assertEqual(sorted((r["raw_id"], r["status"]) for r in rows),
                         [("example.com/m::TestAdd", "passed"),
                          ("example.com/m::TestFail", "failed")])

    def test_rspec(self):
        if not real_tool("rspec"):
            self.skipTest("needs rspec")
        tmp = tempfile.mkdtemp(prefix="hh-rspec-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, "spec", "a_spec.rb"),
              "RSpec.describe('a') do\n"
              "  it('adds') { expect(1 + 1).to eq(2) }\n"
              "  it('fails') { expect(1).to eq(2) }\n"
              "end\n")
        ids = os.path.join(tmp, "ids.jsonl")
        res = call("rspec", "--root", tmp, "--collect-out", ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        collected = read_jsonl(ids)
        self.assertEqual([r["key"] for r in collected],
                         ["rspec::spec/a_spec.rb/1:1", "rspec::spec/a_spec.rb/1:2"])
        ledger = os.path.join(tmp, "ledger.jsonl")
        res = call("rspec", "--root", tmp, "--ledger", ledger)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = ends(read_jsonl(ledger))
        # The Ruby formatter computes keys itself: compare with Python's.
        self.assertEqual([r["key"] for r in rows], [r["key"] for r in collected])
        self.assertEqual([r["status"] for r in rows], ["passed", "failed"])

    def test_phpunit(self):
        if not real_tool("phpunit"):
            self.skipTest("needs phpunit")
        tmp = tempfile.mkdtemp(prefix="hh-php-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        # PHPUnit 11 needs a configuration file to find a suite; that is what
        # a real project has, and it keeps the test about the adapter.
        write(os.path.join(tmp, "phpunit.xml"),
              "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
              "<phpunit xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"\n"
              "         xsi:noNamespaceSchemaLocation=\"phpunit.xsd\">\n"
              "  <testsuites>\n"
              "    <testsuite name=\"default\"><directory>tests</directory></testsuite>\n"
              "  </testsuites>\n"
              "</phpunit>\n")
        write(os.path.join(tmp, "tests", "CalcTest.php"),
              "<?php\nuse PHPUnit\\Framework\\TestCase;\n"
              "final class CalcTest extends TestCase {\n"
              "  public function testAdd(): void { $this->assertSame(2, 1 + 1); }\n"
              "  public function testFail(): void { $this->assertSame(1, 2); }\n"
              "}\n")
        ids = os.path.join(tmp, "ids.jsonl")
        res = call("phpunit", "--root", tmp, "--collect-out", ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["key"] for r in read_jsonl(ids)],
                         ["phpunit::CalcTest/testAdd", "phpunit::CalcTest/testFail"])
        ledger = os.path.join(tmp, "ledger.jsonl")
        res = call("phpunit", "--root", tmp, "--ledger", ledger)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["status"] for r in ends(read_jsonl(ledger))],
                         ["passed", "failed"])

    def test_nextest(self):
        if not (real_tool("cargo") and
                (real_tool("cargo-nextest") or self._has_nextest())):
            self.skipTest("needs cargo-nextest")
        tmp = tempfile.mkdtemp(prefix="hh-nextest-real-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, "Cargo.toml"),
              "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2021\"\n")
        write(os.path.join(tmp, "src", "lib.rs"),
              "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n"
              "#[cfg(test)]\nmod tests {\n    use super::*;\n"
              "    #[test]\n    fn test_add() { assert_eq!(add(1, 1), 2); }\n"
              "    #[test]\n    fn test_fail() { assert_eq!(add(1, 1), 3); }\n}\n")
        ids = os.path.join(tmp, "ids.jsonl")
        res = call("nextest", "--root", tmp, "--collect-out", ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["key"] for r in read_jsonl(ids)],
                         ["nextest::demo/tests/test_add",
                          "nextest::demo/tests/test_fail"])
        ledger = os.path.join(tmp, "ledger.jsonl")
        res = call("nextest", "--root", tmp, "--ledger", ledger)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["status"] for r in ends(read_jsonl(ledger))],
                         ["passed", "failed"])

    @staticmethod
    def _has_nextest() -> bool:
        cargo = shutil.which("cargo")
        if not cargo:
            return False
        res = subprocess.run([cargo, "nextest", "--version"], capture_output=True,
                             text=True)
        return res.returncode == 0


if __name__ == "__main__":
    unittest.main()
