/** Native Mocha collection and checkpointed execution for local test trees.

 * Discovery is *recursive* over test/: nested suites (test/acceptance/**,
 * test/unit/**) are part of the suite and must never be silently dropped. Set
 * HH_MOCHA_REQUIRE=path/a.js,path/b.js to preload the same require hooks the
 * repo's own npm test uses (e.g. express's test/support/env).
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

function files(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return files(p);
    return e.isFile() && /\.(js|cjs|mjs)$/.test(e.name) ? [p] : [];
  });
}
function baseKey(test) {
  // Path relative to the root, so nested suites with the same file stem do not
  // collide (test/Router.js vs test/acceptance/Router.js).
  const rel = path.relative(process.cwd(), test.file || 'unknown')
    .replace(/\.(m|c)?jsx?$/, '').split(path.sep).join('/');
  return `mocha::${rel}/${test.fullTitle().replace(/\s+/g, '_')}`;
}
function tests(suite) {
  return [...suite.tests, ...suite.suites.flatMap(tests)];
}
async function main() {
  const [mode, root, ledger, selection] = process.argv.slice(2);
  if (!['collect', 'run'].includes(mode)) throw Error('expected collect or run');
  process.chdir(root);
  const req = createRequire(path.join(root, 'package.json'));
  for (const mod of (process.env.HH_MOCHA_REQUIRE || '').split(',').map(s => s.trim()).filter(Boolean)) {
    req(path.resolve(root, mod));
  }
  const mochaModule = req('mocha');
  const Mocha = mochaModule.default || mochaModule.Mocha || mochaModule;
  const mocha = new Mocha({ reporter: 'dot', color: false });
  // Recursive discovery: every .js/.cjs/.mjs under test/, at any depth.
  const testDir = path.join(root, 'test');
  for (const file of files(testDir)) mocha.addFile(file);
  await mocha.loadFilesAsync();
  const all = tests(mocha.suite);
  const counts = new Map();
  for (const test of all) counts.set(baseKey(test), (counts.get(baseKey(test)) || 0) + 1);
  const indices = new Map();
  const keys = new WeakMap();
  for (const test of all) {
    const base = baseKey(test);
    const index = indices.get(base) || 0;
    indices.set(base, index + 1);
    keys.set(test, counts.get(base) > 1 ? `${base} :: collision-${index}` : base);
  }
  const key = test => keys.get(test);
  if (mode === 'collect') {
    for (const test of all)
      console.log(JSON.stringify({ key: key(test), raw_id: key(test) }));
    return;
  }
  // No selection file => record the whole suite in this one process, exactly
  // like `mocha test/` would (cold record pass).
  if (selection) {
    const selected = new Set(fs.readFileSync(selection, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line).raw_id));
    const filter = suite => {
      suite.tests = suite.tests.filter(t => selected.has(key(t)));
      for (const child of suite.suites) filter(child);
      suite.suites = suite.suites.filter(s => s.tests.length || s.suites.length);
    };
    filter(mocha.suite);
  }
  // Resume support: never re-pay a test this ledger already finished.
  if (fs.existsSync(ledger)) {
    const done = new Set();
    for (const line of fs.readFileSync(ledger, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      try { const row = JSON.parse(line); if (row.type === 'end') done.add(row.key); }
      catch (e) { /* partial last row from a kill */ }
    }
    if (done.size) {
      const drop = suite => {
        suite.tests = suite.tests.filter(t => !done.has(key(t)));
        for (const child of suite.suites) drop(child);
        suite.suites = suite.suites.filter(s => s.tests.length || s.suites.length);
      };
      drop(mocha.suite);
    }
  }
  const fd = fs.openSync(ledger, 'a');
  const write = row => fs.writeSync(fd, JSON.stringify(row) + '\n');
  const runner = mocha.run(failures => { fs.closeSync(fd); process.exitCode = failures ? 1 : 0; });
  // Completeness marker (see base.suite_end_row): emitted only when the run
  // loop reaches its end, so a killed record pass cannot seal a manifest.
  runner.on('end', () => write({ type: 'suite_end',
    tests: runner.stats ? runner.stats.tests : 0, ts: Date.now() / 1000 }));
  const started = new Map();
  runner.on('test', test => {
    started.set(test, process.hrtime.bigint());
    write({ type: 'start', key: key(test), raw_id: key(test), ts: Date.now() / 1000 });
  });
  for (const event of ['pass', 'fail', 'pending']) runner.on(event, test => {
    const t0 = started.get(test);
    // Pending tests may not receive a test event; they still have an end row.
    write({ type: 'end', key: key(test), raw_id: key(test),
      status: event === 'pass' ? 'passed' : event === 'fail' ? 'failed' : 'skipped',
      duration_ms: t0 ? Number(process.hrtime.bigint() - t0) / 1e6 : 0,
      censored: false, ts: Date.now() / 1000 });
  });
}
main().catch(e => { console.error(e); process.exitCode = 2; });
