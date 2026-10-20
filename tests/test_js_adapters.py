"""The JavaScript adapters: one CLI, five runners.

Three layers are tested here, cheapest first:

  * the registry (pure Python: every runner maps to a file that exists);
  * the shared adapter CLI (`common.cjs`), driven by a fake runner — chunking,
    selection, and the rule that a file with no verdict is recorded as failed
    rather than dropped;
  * the real runners. node:test ships with Node, so it runs wherever Node >= 20
    does. jest, vitest and tap are npm packages, so those tests need a probe
    directory with them installed (`HH_JS_PROBE`, set by the js CI job); without
    it they skip, exactly like the JUnit test skips without a JDK.

Every adapter is file-level: one `end` row per file, and a resume re-runs whole
files. That contract is what the assertions below pin.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from executor.adapters import registry  # noqa: E402

JS_DIR = os.path.join(ROOT, "executor", "adapters", "js")
PROBE = os.environ.get("HH_JS_PROBE", "")
NODE = shutil.which("node")


def node_major() -> int:
    if not NODE:
        return 0
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout
    m = re.match(r"v(\d+)", out.strip())
    return int(m.group(1)) if m else 0


def adapter(name: str, *args: str, cwd: str | None = None,
            env: dict | None = None) -> subprocess.CompletedProcess:
    script = registry.node_script(name)
    return subprocess.run([NODE, script, *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestRegistry(unittest.TestCase):
    def test_every_runner_has_exactly_one_adapter(self):
        self.assertEqual(
            list(registry.RUNNERS),
            ["unittest", "pytest", "junit", "go", "nextest", "rspec",
             "phpunit", "ctest", "node", "jest", "vitest", "node-test", "tap"])
        scripts = [registry.node_script(r) for r in registry.NODE_ADAPTERS]
        self.assertEqual(len(scripts), len(set(scripts)))
        for runner in registry.RUNNERS:
            if registry.is_node(runner):
                self.assertTrue(os.path.exists(registry.node_script(runner)), runner)
            else:
                self.assertTrue(registry.module_name(runner).startswith("executor."))
                with self.assertRaises(KeyError):
                    registry.node_script(runner)

    def test_file_level_runners_are_the_node_runners_that_are_not_mocha(self):
        from executor import keys
        self.assertEqual(keys.FILE_LEVEL_RUNNERS,
                         set(registry.NODE_ADAPTERS) - {"node"})
        # A file-level key is its own file aggregate; a per-test one is not.
        self.assertEqual(keys.file_of("jest::src/a.test.js"), "jest::src/a.test.js")
        # Per-test runners keep the old prefix-free file path; the file-level
        # ones keep the whole key (runner prefix included).
        self.assertEqual(keys.file_of("mocha::test/a.test/a_adds"), "test/a.test")
        self.assertEqual(keys.file_of("pytest::tests/a/test_x"), "tests/a")

    def test_every_node_adapter_is_syntactically_valid(self):
        if not NODE:
            self.skipTest("needs node")
        for name in sorted(os.listdir(JS_DIR)):
            if not name.endswith(".cjs"):
                continue
            res = subprocess.run([NODE, "--check", os.path.join(JS_DIR, name)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{name}: {res.stderr}")


class TestSharedAdapterCli(unittest.TestCase):
    """`common.cjs` without any runner: a fake impl with known verdicts."""

    def setUp(self):
        if not NODE:
            self.skipTest("needs node")
        self.tmp = tempfile.mkdtemp(prefix="hh-jsfake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = os.path.join(self.tmp, "fake.cjs")
        common = os.path.join(JS_DIR, "common.cjs")
        write(self.script, f"""
'use strict';
const common = require({json.dumps(common)});
const path = require('node:path');
const collect = root => ['a.test.js', 'b.test.js', 'c.test.js']
  .map(f => path.join(root, f));
const runChunk = (root, files) => files.map(f => {{
  const raw = common.rel(root, f);
  if (raw === 'b.test.js') return {{ raw_id: raw, status: 'failed', duration_ms: 2.5 }};
  return {{ raw_id: raw, status: 'passed', duration_ms: 1 }};
}}).filter(r => r.raw_id !== 'c.test.js');   // c gets no verdict at all
common.main('fake', {{ collect, runChunk }});
""")

    def run_fake(self, *args, **kw):
        env = dict(os.environ)
        env.update(kw.get("env") or {})
        return subprocess.run([NODE, self.script, *args], cwd=self.tmp,
                              env=env, capture_output=True, text=True)

    def test_collect_writes_one_id_per_file(self):
        out = os.path.join(self.tmp, "ids.jsonl")
        res = self.run_fake("collect", ".", out)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(out)
        self.assertEqual([r["raw_id"] for r in rows],
                         ["a.test.js", "b.test.js", "c.test.js"])
        self.assertEqual([r["key"] for r in rows],
                         ["fake::a.test.js", "fake::b.test.js", "fake::c.test.js"])

    def test_full_run_records_every_file_and_seals_the_suite(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = self.run_fake("run", ".", ledger, env={"HH_JS_CHUNK": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        ends = [r for r in rows if r["type"] == "end"]
        self.assertEqual([r["raw_id"] for r in ends],
                         ["a.test.js", "b.test.js", "c.test.js"])
        self.assertEqual([r["status"] for r in ends], ["passed", "failed", "failed"])
        # The runner said nothing about c.test.js; it is recorded as failed at
        # 0 ms rather than dropped, so a resume cannot think it passed.
        self.assertEqual(ends[2]["duration_ms"], 0.0)
        self.assertEqual(ends[1]["duration_ms"], 2.5)
        self.assertEqual(rows[-1], {"type": "suite_end", "tests": 3,
                                    "ts": rows[-1]["ts"]})

    def test_a_resumed_run_does_not_repeat_finished_units(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        self.assertEqual(self.run_fake("run", ".", ledger).returncode, 0)
        self.assertEqual(len([r for r in read_jsonl(ledger) if r["type"] == "end"]), 3)
        # The record pass can be killed too: the retry re-enters with the same
        # ledger and must not write a second row for a file that already has one
        # (the store would count it twice).
        self.assertEqual(self.run_fake("run", ".", ledger).returncode, 0)
        rows = read_jsonl(ledger)
        self.assertEqual(len([r for r in rows if r["type"] == "end"]), 3)
        self.assertEqual(rows[-1], {"type": "suite_end", "tests": 0,
                                    "ts": rows[-1]["ts"]})

    def test_a_crashing_runner_does_not_seal_the_suite(self):
        """A crash must not look like a completed (empty) record pass."""
        script = os.path.join(self.tmp, "boom.cjs")
        write(script, f"""
'use strict';
const common = require({json.dumps(os.path.join(JS_DIR, "common.cjs"))});
const path = require('node:path');
const collect = root => [path.join(root, 'a.test.js')];
const runChunk = () => {{ throw new Error('runner exploded'); }};
common.main('boom', {{ collect, runChunk }});
""")
        ledger = os.path.join(self.tmp, "boom.jsonl")
        res = subprocess.run([NODE, script, "run", ".", ledger],
                             cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("runner exploded", res.stderr)
        rows = read_jsonl(ledger) if os.path.exists(ledger) else []
        self.assertEqual([r for r in rows if r["type"] == "suite_end"], [])

    def test_selection_runs_only_the_listed_files(self):
        sel = os.path.join(self.tmp, "selection.jsonl")
        write(sel, json.dumps({"raw_id": "b.test.js", "key": "fake::b.test.js"}) + "\n")
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        res = self.run_fake("run", ".", ledger, sel)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        ends = [r for r in rows if r["type"] == "end"]
        self.assertEqual([r["raw_id"] for r in ends], ["b.test.js"])
        self.assertEqual(rows[-1]["tests"], 1)


class _ProbeSuite:
    """Help for the tests that need a real npm runner."""

    def make_project(self, under_probe: bool, files: dict[str, str]) -> str:
        parent = PROBE if under_probe and PROBE and os.path.isdir(PROBE) else None
        tmp = tempfile.mkdtemp(prefix="hh-js-", dir=parent)
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, body in files.items():
            write(os.path.join(tmp, name), body)
        return tmp

    def collect_and_run(self, runner: str, root: str):
        ids = os.path.join(root, "ids.jsonl")
        res = adapter(runner, "collect", root, ids)
        self.assertEqual(res.returncode, 0, res.stderr)
        collected = read_jsonl(ids)
        ledger = os.path.join(root, "ledger.jsonl")
        res = adapter(runner, "run", root, ledger)
        # A failing test is a verdict, not an adapter error: mocha propagates
        # its failure count as exit 1, the others exit 0. Either way the ledger
        # must be complete, which is what the assertions below check.
        self.assertIn(res.returncode, (0, 1), res.stderr)
        rows = read_jsonl(ledger)
        ends = [r for r in rows if r["type"] == "end"]
        self.assertEqual([r["raw_id"] for r in ends],
                         [c["raw_id"] for c in collected])
        self.assertEqual(rows[-1]["type"], "suite_end")
        self.assertEqual(rows[-1]["tests"], len(collected))
        return collected, {r["raw_id"]: r for r in ends}, root, ledger


@unittest.skipUnless(PROBE and NODE and os.path.isdir(PROBE),
                     "needs HH_JS_PROBE with mocha/jest/vitest/tap installed")
class TestMochaAdapter(_ProbeSuite, unittest.TestCase):
    """The original `node` runner, still per-test rather than per-file."""

    FILES = {
        "test/a.test.js": ("const assert = require('assert');\n"
                           "describe('a', () => { it('adds', () => "
                           "assert.equal(1 + 1, 2)); });\n"),
        "test/b.test.js": ("const assert = require('assert');\n"
                           "describe('b', () => { it('fails', () => "
                           "assert.equal(1, 2)); });\n"),
    }

    def test_collect_run_and_resume_subset(self):
        root = self.make_project(True, self.FILES)
        collected, ends, _root, _ledger = self.collect_and_run("node", root)
        self.assertEqual([c["key"] for c in collected],
                         ["mocha::test/a.test/a_adds",
                          "mocha::test/b.test/b_fails"])
        self.assertEqual(ends["mocha::test/a.test/a_adds"]["status"], "passed")
        self.assertEqual(ends["mocha::test/b.test/b_fails"]["status"], "failed")


@unittest.skipUnless(NODE and node_major() >= 20, "needs node >= 20")
class TestNodeTestAdapter(_ProbeSuite, unittest.TestCase):
    FILES = {
        "a.test.mjs": (
            "import test from 'node:test';\n"
            "import assert from 'node:assert';\n"
            "test('adds', () => { assert.equal(1 + 1, 2); });\n"),
        "sub/b.test.mjs": (
            "import test from 'node:test';\n"
            "import assert from 'node:assert';\n"
            "test('fails on purpose', () => { assert.equal(1, 2); });\n"),
    }

    def test_collect_run_and_resume_subset(self):
        root = self.make_project(False, self.FILES)
        collected, ends, _root, ledger = self.collect_and_run("node-test", root)
        self.assertEqual([c["key"] for c in collected],
                         ["node-test::a.test.mjs", "node-test::sub/b.test.mjs"])
        self.assertEqual(ends["a.test.mjs"]["status"], "passed")
        self.assertEqual(ends["sub/b.test.mjs"]["status"], "failed")
        self.assertGreater(ends["a.test.mjs"]["duration_ms"], 0)

        # A resume hands the adapter exactly the unfinished files.
        sel = os.path.join(root, "selection.jsonl")
        write(sel, json.dumps({"raw_id": "a.test.mjs", "key": "node-test::a.test.mjs"}) + "\n")
        ledger2 = os.path.join(root, "ledger2.jsonl")
        res = adapter("node-test", "run", root, ledger2, sel)
        self.assertEqual(res.returncode, 0, res.stderr)
        ends2 = [r for r in read_jsonl(ledger2) if r["type"] == "end"]
        self.assertEqual([r["raw_id"] for r in ends2], ["a.test.mjs"])

    def test_a_record_pass_retry_does_not_write_duplicate_rows(self):
        root = self.make_project(False, self.FILES)
        _collected, _ends, _root, ledger = self.collect_and_run("node-test", root)
        res = adapter("node-test", "run", root, ledger)   # what a resume does
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = read_jsonl(ledger)
        self.assertEqual(len([r for r in rows if r["type"] == "end"]), 2)
        self.assertEqual(rows[-1]["tests"], 0)


@unittest.skipUnless(PROBE and NODE and os.path.isdir(PROBE),
                     "needs HH_JS_PROBE with jest/vitest/tap installed")
class TestJestAdapter(_ProbeSuite, unittest.TestCase):
    FILES = {
        "src/a.test.js": "test('a', () => { expect(1 + 1).toBe(2); });\n",
        "src/b.test.js": "test('b', () => { expect(1).toBe(2); });\n",
    }

    def test_collect_run_and_resume_subset(self):
        root = self.make_project(True, self.FILES)
        collected, ends, _root, _ledger = self.collect_and_run("jest", root)
        self.assertEqual([c["key"] for c in collected],
                         ["jest::src/a.test.js", "jest::src/b.test.js"])
        self.assertEqual(ends["src/a.test.js"]["status"], "passed")
        self.assertEqual(ends["src/b.test.js"]["status"], "failed")


@unittest.skipUnless(PROBE and NODE and os.path.isdir(PROBE),
                     "needs HH_JS_PROBE with jest/vitest/tap installed")
class TestVitestAdapter(_ProbeSuite, unittest.TestCase):
    FILES = {
        "src/a.test.mjs": ("import { test, expect } from 'vitest';\n"
                           "test('a', () => { expect(1 + 1).toBe(2); });\n"),
        "src/b.test.mjs": ("import { test, expect } from 'vitest';\n"
                           "test('b', () => { expect(1).toBe(2); });\n"),
    }

    def test_collect_run_and_resume_subset(self):
        root = self.make_project(True, self.FILES)
        collected, ends, _root, _ledger = self.collect_and_run("vitest", root)
        self.assertEqual([c["key"] for c in collected],
                         ["vitest::src/a.test.mjs", "vitest::src/b.test.mjs"])
        self.assertEqual(ends["src/a.test.mjs"]["status"], "passed")
        self.assertEqual(ends["src/b.test.mjs"]["status"], "failed")


@unittest.skipUnless(PROBE and NODE and os.path.isdir(PROBE),
                     "needs HH_JS_PROBE with jest/vitest/tap installed")
class TestTapAdapter(_ProbeSuite, unittest.TestCase):
    FILES = {
        "test/a.test.js": ("const t = require('tap');\n"
                           "t.test('a', async t => { t.equal(1 + 1, 2); });\n"),
        "test/b.test.js": ("const t = require('tap');\n"
                           "t.test('b', async t => { t.equal(1, 2); });\n"),
    }

    def test_collect_run_and_resume_subset(self):
        root = self.make_project(True, self.FILES)
        collected, ends, _root, _ledger = self.collect_and_run("tap", root)
        self.assertEqual([c["key"] for c in collected],
                         ["tap::test/a.test.js", "tap::test/b.test.js"])
        self.assertEqual(ends["test/a.test.js"]["status"], "passed")
        self.assertEqual(ends["test/b.test.js"]["status"], "failed")


@unittest.skipUnless(NODE and node_major() >= 20, "needs node >= 20")
class TestJsRunnerThroughTheTool(unittest.TestCase):
    """The whole path: plan -> run -> plan again, through hh's own store."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hh-jstool-")
        self.store = tempfile.mkdtemp(prefix="hh-jstool-store-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        write(os.path.join(self.root, "a.test.mjs"),
              "import test from 'node:test';\n"
              "test('a', () => {});\n")
        write(os.path.join(self.root, "b.test.mjs"),
              "import test from 'node:test';\n"
              "test('b', () => {});\n")

    def test_cold_records_then_warm_measures(self):
        from executor.tool import build_plan, execute_plan

        cold = build_plan(self.root, runner="node-test", store_root=self.store)
        self.assertEqual(cold["operating_level"], "record")
        self.assertTrue(cold["shards"][0]["full_suite"])
        res = execute_plan(cold, self.root, runner="node-test", store_root=self.store)
        self.assertTrue(res["stats"]["suite_complete"], res)
        self.assertEqual(res["ingested"], 2)

        warm = build_plan(self.root, runner="node-test", store_root=self.store)
        self.assertEqual(warm["manifest_size"], 2)
        self.assertEqual(warm["measured_tests"], 2)
        self.assertEqual(warm["est"]["basis"], "measured")
        res = execute_plan(warm, self.root, runner="node-test", store_root=self.store)
        self.assertEqual(res["stats"]["timed_out"], 0, res)

    def test_another_runner_is_still_cold(self):
        """Rows belong to a runner: a measured pytest suite says nothing here."""
        from executor.tool import build_plan

        first = build_plan(self.root, runner="node-test", store_root=self.store)
        self.assertEqual(first["operating_level"], "record")
        write(os.path.join(self.root, "test_x.py"),
              "import unittest\n\n\nclass T(unittest.TestCase):\n"
              "    def test_x(self):\n        pass\n")
        # unittest has no rows through this store yet, and node-test's rows do
        # not count for it.
        other = build_plan(self.root, runner="unittest", store_root=self.store)
        self.assertEqual(other["operating_level"], "record")


if __name__ == "__main__":
    unittest.main()
