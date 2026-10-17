"""Live JS conformance: real Jest + Mocha runs through the hh reporters.

Opt-in (`conformance.py --live-js`): installs jest+mocha into a scratch dir
outside the repo (node_modules never pollutes the workspace), runs small
suites with known sleeps, and asserts the reporter's rows match reality.
Also re-checks SIGKILL checkpointing: fast tests must be on disk while a
later test is still hanging.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADIR = os.path.join(ROOT, "hh", "adapters")
SCRATCH = "/tmp/hh_jsconform"

JEST_TESTS = {
    "tests/sum.test.js": """const assert = require('assert');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
describe('arithmetic', () => {
  test('adds fast', () => { assert.strictEqual(1 + 1, 2); });
  test('adds slowly', async () => { await sleep(300); assert.strictEqual(2 + 2, 4); });
  test.skip('later', () => {});
});
""",
    "tests/strings.spec.js": """const assert = require('assert');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
describe('strings', () => {
  it('trims', async () => { await sleep(120); assert.strictEqual(' a '.trim(), 'a'); });
  it('fails on purpose', () => { assert.strictEqual('a', 'b'); });
});
""",
    "tests/mixed.test.js": """const assert = require('assert');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
describe('mixed', () => {
  test('fast one', () => { assert.ok(true); });
  test('medium two', async () => { await sleep(200); assert.ok(true); });
  test('hangs at the end', async () => { await sleep(20000); }, 30000);
});
""",
}
MOCHA_TESTS = {
    "tests/mocha_tests.js": """const assert = require('assert');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
describe('mocha arithmetic', () => {
  it('adds fast', () => { assert.strictEqual(1 + 1, 2); });
  it('adds slowly', async function () { this.timeout(5000); await sleep(400); assert.ok(true); });
  it.skip('later', () => {});
});
describe('mocha nesting', () => {
  describe('deep', () => {
    it('works nested', async () => { await sleep(80); assert.ok(true); });
  });
});
""",
    "tests/mocha_mixed.js": """const assert = require('assert');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
describe('mocha mixed', () => {
  it('fast one', () => { assert.ok(true); });
  it('medium two', async () => { await sleep(200); assert.ok(true); });
  it('hangs at the end', async function () { this.timeout(25000); await sleep(20000); });
});
""",
}

# suffix-key -> (status, lo_ms, hi_ms)
JEST_EXPECT = {
    "arithmetic_adds_fast": ("passed", 0, 60),
    "arithmetic_adds_slowly": ("passed", 230, 450),
    "arithmetic_later": ("skipped", 0, 5),
    "strings_trims": ("passed", 90, 260),
    "strings_fails_on_purpose": ("failed", 0, 60),
}
NODE_TESTS = {
    "tests/node_tests.mjs": """import { test } from 'node:test';
import assert from 'node:assert';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
test('node fast', () => { assert.ok(true); });
test('node medium', async () => { await sleep(150); assert.ok(true); });
test('node skip', { skip: true }, () => {});
""",
}
NODE_EXPECT = {
    "node_fast": ("passed", 0, 60),
    "node_medium": ("passed", 100, 300),
    "node_skip": ("skipped", 0, 5),
}
MOCHA_EXPECT = {
    "mocha_arithmetic_adds_fast": ("passed", 0, 60),
    "mocha_arithmetic_adds_slowly": ("passed", 320, 600),
    "mocha_arithmetic_later": ("skipped", 0, 5),
    "mocha_nesting_deep_works_nested": ("passed", 40, 220),
}


def _setup() -> None:
    os.makedirs(SCRATCH, exist_ok=True)
    if not os.path.exists(os.path.join(SCRATCH, "node_modules", ".bin", "jest")):
        subprocess.run(["npm", "init", "-y"], cwd=SCRATCH, capture_output=True)
        print("  installing jest+mocha in scratch dir (one-time, ~25s)...")
        subprocess.run(["npm", "install", "--no-audit", "--no-fund", "jest", "mocha"],
                       cwd=SCRATCH, capture_output=True, check=True)
    for fname, src in {**JEST_TESTS, **MOCHA_TESTS, **NODE_TESTS}.items():
        p = os.path.join(SCRATCH, fname)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(src)


def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def _check(rows, expect: dict) -> list[str]:
    bad = []
    by_suffix = {r["key"].split("/")[-1]: r for r in rows}
    for suffix, (status, lo, hi) in expect.items():
        r = by_suffix.get(suffix)
        if r is None:
            bad.append(f"missing row {suffix}")
        elif r["status"] != status:
            bad.append(f"{suffix}: status {r['status']} != {status}")
        elif not (lo <= r["duration_ms"] <= hi):
            bad.append(f"{suffix}: {r['duration_ms']}ms outside [{lo},{hi}]")
    return bad


def live_js_matrix() -> int:
    _setup()
    env = dict(os.environ)
    jest_reporter = os.path.join(ADIR, "hh_jest_reporter.cjs")
    mocha_reporter = os.path.join(ADIR, "hh_mocha_reporter.cjs")
    fails = 0

    # 1. plain live runs
    for name, cmd, expect in (
        ("jest", ["npx", "jest", "tests/sum.test.js", "tests/strings.spec.js",
                  f"--reporters=default", f"--reporters={jest_reporter}", "--silent"],
         JEST_EXPECT),
        ("mocha", ["node", "node_modules/.bin/mocha", f"--reporter", mocha_reporter,
                   "tests/mocha_tests.js"], MOCHA_EXPECT),
        ("node", ["node", "--test", "--test-reporter",
                  os.path.join(ADIR, "node_reporter.mjs"), "tests/node_tests.mjs"],
         NODE_EXPECT),
    ):
        ledger = os.path.join(SCRATCH, f"live_{name}.jsonl")
        if os.path.exists(ledger):
            os.remove(ledger)
        env["HH_LEDGER"] = ledger
        subprocess.run(cmd, cwd=SCRATCH, env=env, capture_output=True)
        rows = _rows(ledger)
        bad = _check(rows, expect)
        print(f"  live {name:5} {len(rows)} rows, {len(expect)} expected  "
              f"{'PASS' if not bad else 'FAIL ' + '; '.join(bad)}")
        fails += bool(bad)

    # 2. SIGKILL checkpointing: fast tests on disk while a 20s test is running
    for name, cmd, exp_prefix, kill_after in (
        ("jest", ["npx", "jest", "tests/mixed.test.js", "--reporters=default",
                  f"--reporters={jest_reporter}", "--silent"], "mixed_fast_one", 7),
        ("mocha", ["node", "node_modules/.bin/mocha", "--reporter", mocha_reporter,
                   "tests/mocha_mixed.js"], "mocha_mixed_fast_one", 4),
    ):
        ledger = os.path.join(SCRATCH, f"kill_{name}.jsonl")
        if os.path.exists(ledger):
            os.remove(ledger)
        env["HH_LEDGER"] = ledger
        proc = subprocess.Popen(cmd, cwd=SCRATCH, env=env,
                                start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(kill_after)
        try:
            os.killpg(proc.pid, 9)          # SIGKILL the whole group
        except ProcessLookupError:
            pass
        time.sleep(1.0)
        rows = _rows(ledger)
        suffs = [r["key"].split("/")[-1] for r in rows]
        ok = any(s.endswith(exp_prefix) for s in suffs) and \
            all(r["duration_ms"] < 19000 for r in rows)   # the 20s test never landed
        print(f"  kill {name:5} {len(rows)} rows checkpointed pre-SIGKILL "
              f"({'PASS' if ok else 'FAIL: ' + str(suffs)})")
        fails += (not ok)
    return fails


if __name__ == "__main__":
    sys.exit(1 if live_js_matrix() else 0)
