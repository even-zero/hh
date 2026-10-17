/** Tier A adapter: Mocha custom reporter → hh ledger (streaming).
 *  Use:
 *    HH_LEDGER=path/ledger.jsonl npx mocha --reporter /abs/path/hh_mocha_reporter.cjs tests/
 *  Emits one row per test at 'test end' — mocha reports duration there, so
 *  a SIGKILL mid-suite still leaves every finished test checkpointed.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const keyOf = (file, fullTitle) => {
  const rel = (file || 'unknown').replace(/\\/g, '/').replace(/.*\/node_modules\//, '');
  const stem = rel.replace(/^.*?\/?(?=[^/]+$)/, '').replace(/\.(m|c)?jsx?$/, '');
  return `mocha::${stem}/${String(fullTitle).replace(/\s+/g, '_')}`;
};

module.exports = function tdaMochaReporter(runner) {
  const ledger = process.env.HH_LEDGER;
  if (!ledger) return;
  runner.on('test end', (test) => {
    const status = test.pending ? 'skipped'
      : test.state === 'passed' ? 'passed' : 'failed';
    const full = (typeof test.fullTitle === 'function' ? test.fullTitle() : test.title) || test.title || '';
    const row = {
      type: 'end', key: keyOf(test.file, full), raw_id: full, status,
      duration_ms: typeof test.duration === 'number' ? test.duration : 0,
      censored: false, ts: Date.now() / 1000,
    };
    fs.appendFileSync(ledger, JSON.stringify(row) + '\n');
  });
};
