#!/usr/bin/env node
/**
 * Jest adapter: file-level discovery and execution.
 *
 *   node jest_stream.cjs collect <root> [out.jsonl]
 *   node jest_stream.cjs run <root> <ledger.jsonl> [selection.jsonl]
 *
 * Discovery asks Jest itself (`--listTests --json`), so the project's own
 * `testMatch`, roots and config decide what the suite is — not a glob hh made
 * up. Execution runs selected files with `--runTestsByPath`, in chunks, and
 * converts Jest's JSON report into one ledger row per file.
 *
 * Granularity is the file: Jest can list test *files* without running them but
 * not individual test names, so a file is the unit hh measures here.
 */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const common = require('./common.cjs');

const TEST_FILE = /\.(test|spec)\.[cm]?[jt]sx?$|__tests__[\\/].*\.[cm]?[jt]sx?$/;

function jestBin(root) {
  return common.localBin(root, 'jest', path.join('jest', 'bin', 'jest.js'));
}

function collect(root) {
  const bin = jestBin(root);
  if (bin) {
    const res = common.run(process.execPath, [bin, '--listTests', '--json'],
      { cwd: root, timeoutMs: 120000 });
    if (res.code === 0) {
      try {
        const files = JSON.parse(res.stdout.trim().split('\n').pop());
        if (Array.isArray(files)) {
          return files.filter(f => typeof f === 'string').sort();
        }
      } catch {
        /* fall through to the glob */
      }
    }
  }
  // No local Jest (or it refused to list): fall back to Jest's default
  // testMatch shape so a plan can still be made.
  return common.walk(root, TEST_FILE);
}

function runChunk(root, files) {
  const bin = jestBin(root);
  if (!bin) throw new Error('jest is not installed in this project (node_modules/jest)');
  const report = path.join(os.tmpdir(), `hh-jest-${process.pid}-${Date.now()}.json`);
  const args = [bin, '--ci', '--runTestsByPath', ...files,
    '--json', `--outputFile=${report}`, '--silent'];
  const res = common.run(process.execPath, args, { cwd: root });
  const doc = common.readJSON(report);
  try {
    fs.unlinkSync(report);
  } catch {
    /* the runner may not have written one */
  }
  if (!doc || !Array.isArray(doc.testResults)) {
    throw new Error('jest produced no report: ' + (res.stderr || res.stdout).slice(-1500));
  }
  return doc.testResults.map(fileResult => ({
    raw_id: common.rel(root, fileResult.name || fileResult.testFilePath || ''),
    status: fileResult.status === 'passed' ? 'passed' : 'failed',
    // Test time, not wall clock: the sum of the assertions in the file.
    duration_ms: (fileResult.assertionResults || [])
      .reduce((sum, a) => sum + (a.duration || 0), 0),
  }));
}

process.exitCode = common.main('jest', { collect, runChunk });
