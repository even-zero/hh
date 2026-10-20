#!/usr/bin/env node
/**
 * node-tap adapter: file-level discovery and execution.
 *
 *   node tap_stream.cjs collect <root> [out.jsonl]
 *   node tap_stream.cjs run <root> <ledger.jsonl> [selection.jsonl]
 *
 * tap has no "list the tests" command, so discovery uses the same file shapes
 * tap itself includes by default (`*.test.{js,cjs,mjs}` and anything under
 * `test/`).
 *
 * Execution runs `tap run <files>` with the TAP reporter and parses the
 * *top-level* test points — one per file, each with `# time=...` — which is
 * exactly the granularity hh records here. Coverage is disabled: it costs more
 * than the tests and hh is timing the tests.
 */
'use strict';
const path = require('node:path');
const common = require('./common.cjs');

const TEST_FILE = /\.test\.[cm]?js$/;
const TEST_DIR = /[\\/]tests?[\\/].*\.[cm]?js$/;

function collect(root) {
  const found = common.walk(root, TEST_FILE);
  for (const f of common.walk(root, TEST_DIR)) {
    if (!found.includes(f)) found.push(f);
  }
  return found.sort();
}

function tapBin(root) {
  return common.localBin(root, 'tap', path.join('tap', 'dist', 'esm', 'run.mjs'))
    || common.localBin(root, 'tap');
}

/** Top-level TAP points: `ok 1 - path/to/file.js # time=12.3ms` */
function parseTopLevel(text) {
  const rows = new Map();
  for (const line of text.split('\n')) {
    const m = line.match(/^(ok|not ok)\s+\d+\s+-\s+(.+?)(?:\s+#\s+(.*))?$/);
    if (!m) continue;                      // nested points are indented
    if (/^#/.test(line)) continue;
    const [, verdict, rawName, directive] = m;
    const raw = rawName.trim();
    const t = directive && directive.match(/time=([\d.]+)ms/);
    const prev = rows.get(raw) || { status: 'passed', duration_ms: 0 };
    if (verdict === 'not ok') prev.status = 'failed';
    if (t) prev.duration_ms += parseFloat(t[1]) || 0;
    rows.set(raw, prev);
  }
  return rows;
}

function runChunk(root, files) {
  const bin = tapBin(root);
  if (!bin) throw new Error('tap is not installed in this project (node_modules/tap)');
  const args = [bin, 'run', '--disable-coverage', '--no-color', '--reporter=tap',
    ...files.map(f => common.rel(root, f))];
  const res = common.run(process.execPath, args, { cwd: root, timeoutMs: 0 });
  const parsed = parseTopLevel(res.stdout);
  const names = [...parsed.keys()];
  const out = [];
  for (const file of files) {
    const raw = common.rel(root, file);
    // tap names a point relative to *its* project root (the nearest
    // package.json), which can be above the suite root: match the longest
    // common tail instead of requiring the two strings to be equal.
    const hit = names.find(n => n === raw || n.endsWith('/' + raw));
    out.push(hit ? { raw_id: raw, ...parsed.get(hit) }
      : { raw_id: raw, status: 'failed', duration_ms: 0 });
  }
  return out;
}

process.exitCode = common.main('tap', { collect, runChunk });
