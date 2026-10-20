#!/usr/bin/env node
/**
 * node:test adapter (Node's built-in runner): file-level discovery and
 * execution.
 *
 *   node nodetest_stream.cjs collect <root> [out.jsonl]
 *   node nodetest_stream.cjs run <root> <ledger.jsonl> [selection.jsonl]
 *
 * Discovery follows Node's own default patterns: `*.test.{js,mjs,cjs}`,
 * `test-*.{js,mjs,cjs}`, anything under `test/` or `tests/`.
 *
 * Execution passes explicit file paths — a directory argument means "require
 * this file" to Node 22, not "search it" — and uses a small custom
 * `--test-reporter` that emits one JSON line per finished test with the file
 * and the duration. hh aggregates those into one row per file.
 *
 * Requires Node >= 20 (the version that stabilised `--test-reporter`).
 */
'use strict';
const path = require('node:path');
const common = require('./common.cjs');

const TEST_FILE = /\.test\.[cm]?js$|^test-.*\.[cm]?js$/;
const TEST_DIR = /[\\/]tests?[\\/].*\.[cm]?js$/;

const REPORTER = `
export default async function* (source) {
  for await (const ev of source) {
    if (ev.type !== 'test:pass' && ev.type !== 'test:fail') continue;
    const d = ev.data || {};
    if (!d.file) continue;              // a suite node, not a test
    const details = d.details || {};
    const ms = details.duration_ms !== undefined
      ? details.duration_ms
      : (details.duration_ms_total || 0);
    yield JSON.stringify({ file: d.file, status: ev.type, ms }) + '\\n';
  }
}
`;

function collect(root) {
  const found = common.walk(root, TEST_FILE);
  for (const f of common.walk(root, TEST_DIR)) {
    if (!found.includes(f)) found.push(f);
  }
  return found.sort();
}

let reporterPath = null;
function reporter() {
  if (!reporterPath) {
    const os = require('node:os');
    const fs = require('node:fs');
    reporterPath = path.join(os.tmpdir(), `hh-nodetest-reporter-${process.pid}.mjs`);
    fs.writeFileSync(reporterPath, REPORTER);
  }
  return reporterPath;
}

function runChunk(root, files) {
  const args = ['--test', '--test-reporter', reporter(),
    ...files.map(f => common.rel(root, f))];
  const res = common.run(process.execPath, args, { cwd: root, timeoutMs: 0 });
  const perFile = new Map();
  for (const line of res.stdout.split('\n')) {
    if (!line.trim().startsWith('{')) continue;
    let ev;
    try {
      ev = JSON.parse(line);
    } catch {
      continue;
    }
    const raw = common.rel(root, ev.file);
    const cur = perFile.get(raw) || { status: 'passed', duration_ms: 0 };
    if (ev.status === 'test:fail') cur.status = 'failed';
    cur.duration_ms += Number(ev.ms) || 0;
    perFile.set(raw, cur);
  }
  return [...perFile.entries()].map(([raw, v]) => ({ raw_id: raw, ...v }));
}

process.exitCode = common.main('node-test', { collect, runChunk });
