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
  // Keep the path relative to cwd so nested suites with the same stem do not
  // collide (mirrors adapters/mocha_stream.cjs, the executor's key format).
  const rel = path.relative(process.cwd(), file || 'unknown')
    .split(path.sep).join('/').replace(/\.(m|c)?jsx?$/, '');
  return `mocha::${rel}/${String(fullTitle).replace(/\s+/g, '_')}`;
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
