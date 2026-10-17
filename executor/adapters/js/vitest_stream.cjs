#!/usr/bin/env node
/**
 * Vitest adapter: file-level discovery and execution.
 *
 *   node vitest_stream.cjs collect <root> [out.jsonl]
 *   node vitest_stream.cjs run <root> <ledger.jsonl> [selection.jsonl]
 *
 * Discovery prefers `vitest list --json` (Vitest >= 1.5), which reports every
 * test — hh keeps the distinct files. Older versions fall back to Vitest's own
 * include patterns.
 *
 * Granularity is the file. Vitest *can* list individual tests, but it cannot
 * run an arbitrary subset of them by id: `-t` is one global name pattern, so
 * selecting "these three tests in this file" turns into one process per file.
 * hh measures and resumes by file instead, which is honest about what a resume
 * re-pays.
 */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const common = require('./common.cjs');

const TEST_FILE = /\.(test|spec)\.[cm]?[jt]sx?$/;

function vitestBin(root) {
  return common.localBin(root, 'vitest', path.join('vitest', 'vitest.mjs'));
}

function collect(root) {
  const bin = vitestBin(root);
  if (bin) {
    const res = common.run(process.execPath, [bin, 'list', '--json'],
      { cwd: root, timeoutMs: 180000 });
    if (res.code === 0) {
      const start = res.stdout.indexOf('[');
      const end = res.stdout.lastIndexOf(']');
      if (start >= 0 && end > start) {
        try {
          const entries = JSON.parse(res.stdout.slice(start, end + 1));
          const files = new Set();
          for (const e of entries) {
            if (e && typeof e.file === 'string') files.add(path.resolve(root, e.file));
          }
          if (files.size) return [...files].sort();
        } catch {
          /* fall through to the glob */
        }
      }
    }
  }
  return common.walk(root, TEST_FILE);
}

function runChunk(root, files) {
  const bin = vitestBin(root);
  if (!bin) throw new Error('vitest is not installed in this project (node_modules/vitest)');
  const report = path.join(os.tmpdir(), `hh-vitest-${process.pid}-${Date.now()}.json`);
  const args = [bin, 'run', '--reporter=json', `--outputFile=${report}`,
    '--passWithNoTests', ...files.map(f => common.rel(root, f))];
  const res = common.run(process.execPath, args, { cwd: root, timeoutMs: 0 });
  const doc = common.readJSON(report);
  try {
    fs.unlinkSync(report);
  } catch {
    /* no report written */
  }
  if (!doc || !Array.isArray(doc.testResults)) {
    throw new Error('vitest produced no report: ' + (res.stderr || res.stdout).slice(-1500));
  }
  return doc.testResults.map(fileResult => ({
    raw_id: common.rel(root, fileResult.name || ''),
    status: fileResult.status === 'passed' ? 'passed' : 'failed',
    duration_ms: (fileResult.assertionResults || [])
      .reduce((sum, a) => sum + (a.duration || 0), 0),
  }));
}

process.exitCode = common.main('vitest', { collect, runChunk });
