/** Tier A adapter: Jest custom reporter → hh ledger (streaming).
 *  Use:
 *    HH_LEDGER=path/ledger.jsonl npx jest --reporters=default --reporters=/abs/path/hh_jest_reporter.cjs
 *  Emits one ledger row per test as it finishes (Jest ≥26 calls
 *  onTestCaseResult incrementally → true checkpointing, Law 2).
 */
'use strict';
const fs = require('fs');
const path = require('path');

const keyOf = (file, fullName) => {
  const rel = (file || 'unknown').replace(/\\/g, '/').replace(/.*\/node_modules\//, '');
  const stem = rel.replace(/^.*?\/?(?=[^/]+$)/, '').replace(/\.(test|spec)\.(m|c)?jsx?$|\.(m|c)?jsx?$/, '');
  return `jest::${stem}/${String(fullName).replace(/\s+/g, '_')}`;
};

class TdaJestReporter {
  constructor(globalConfig) {
    this.globalConfig = globalConfig;
    this.ledger = process.env.HH_LEDGER;
    this.emitted = new Set();
  }
  _row(result) {
    const status = result.status === 'passed' ? 'passed'
      : (result.status === 'pending' || result.status === 'todo') ? 'skipped' : 'failed';
    const full = (result.ancestorTitles || []).concat(result.title).join(' ')
      || result.fullName || result.title || '';
    const row = {
      type: 'end',
      key: keyOf(result.testFilePath || result.filePath, full),
      raw_id: full,
      status,
      duration_ms: typeof result.duration === 'number' ? result.duration : 0,
      censored: false,
      ts: Date.now() / 1000,
    };
    if (this.emitted.has(row.key)) return;      // dedupe tc-result vs test-result
    this.emitted.add(row.key);
    if (this.ledger) fs.appendFileSync(this.ledger, JSON.stringify(row) + '\n');
  }
  // Jest ≥26: fires per test, as it completes (streaming path)
  onTestCaseResult(test, testCaseResult) {
    this._row({
      testFilePath: testCaseResult.testFilePath || test.path,
      ancestorTitles: testCaseResult.ancestorTitles,
      title: testCaseResult.title, fullName: testCaseResult.fullName,
      status: testCaseResult.status, duration: testCaseResult.duration,
    });
  }
  // Fallback / older Jest: fires per file after the file completes
  onTestResult(test, testResult) {
    for (const r of testResult.testResults || []) {
      this._row({
        testFilePath: testResult.testFilePath, ancestorTitles: r.ancestorTitles,
        title: r.title, fullName: r.fullName, status: r.status, duration: r.duration,
      });
    }
  }
}
module.exports = TdaJestReporter;
