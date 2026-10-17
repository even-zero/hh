/** Tier A adapter: node:test custom reporter → hh ledger.
 *  Use: HH_LEDGER=path/ledger.jsonl node --test --test-reporter=./node_reporter.mjs tests/
 */
import fs from 'node:fs';

const keyOf = (file, name) => {
  const stem = (file || 'unknown').replace(/\.mjs?$/, '').replace(/.*\//, '');
  return `node::${stem}/${name.replace(/\s+/g, '_')}`;
};

export default async function* reporter(source) {
  const ledger = process.env.HH_LEDGER;
  for await (const ev of source) {
    if (['test:pass', 'test:fail', 'test:skip'].includes(ev.type)) {
      const d = ev.data || {};
      // node:test reports skips as test:pass with data.skip=true (node ≥20)
      const status = (ev.type === 'test:skip' || d.skip) ? 'skipped'
        : ev.type === 'test:pass' ? 'passed' : 'failed';
      const row = {
        type: 'end', key: keyOf(d.file, d.name), raw_id: d.name, status,
        duration_ms: d.details?.duration_ms ?? 0, censored: false,
        ts: Date.now() / 1000,
      };
      if (ledger) fs.appendFileSync(ledger, JSON.stringify(row) + '\n');
    }
    yield '';
  }
}
