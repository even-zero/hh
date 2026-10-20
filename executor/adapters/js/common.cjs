/**
 * Shared plumbing for the JavaScript adapters.
 *
 * Five runners (Mocha, Jest, Vitest, node:test, tap) disagree about almost
 * everything except the two things hh needs:
 *
 *   1. a list of test files, discoverable without running them;
 *   2. a per-file verdict and duration, after a run.
 *
 * So every JS adapter is a thin shell around this module:
 *
 *   node <adapter>.cjs collect <root> [out.jsonl]
 *   node <adapter>.cjs run <root> <ledger.jsonl> [selection.jsonl]
 *
 * Granularity is the *file*. None of these runners can enumerate individual
 * tests without executing them (Vitest's `list` is the exception, and it still
 * cannot run an arbitrary subset by id), so a file is the unit hh measures and
 * the unit a resume leaves out. That is stated in docs/<runner>.md rather than
 * hidden.
 *
 * Execution is chunked: files are run in groups of HH_JS_CHUNK (default 25),
 * and the ledger is flushed after each chunk. A SIGKILL at hh's budget
 * therefore loses at most the chunk in flight, and the resume only re-runs the
 * files that never got a row.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const SKIP_DIRS = new Set(['node_modules', '.git', '.hh', 'build', 'dist',
  'coverage', '.next', '.nuxt', '.output', '.cache']);

/** Files under `root` whose name matches `re`, skipping vendor/build trees. */
function walk(root, re) {
  const out = [];
  const visit = dir => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name) && !e.name.startsWith('.')) visit(p);
      } else if (e.isFile() && re.test(e.name)) {
        out.push(p);
      }
    }
  };
  visit(root);
  return out;
}

function rel(root, file) {
  return path.relative(root, file).split(path.sep).join('/');
}

/**
 * Is `file` inside the suite root?
 *
 * A project runner resolves *its* root, not hh's: jest and tap walk up to the
 * nearest package.json, so asking them about `packages/web` can answer with
 * files in `packages`. Those are not this suite, and a `../` key would poison
 * the store, so discovery keeps only what is under the root hh was given.
 */
function inside(root, file) {
  const r = path.relative(root, path.resolve(file));
  return r !== '' && r !== '..' && !r.startsWith('..' + path.sep)
    && !path.isAbsolute(r);
}

/** The key and the raw id are the same thing: the file, relative to the root. */
function keyFor(runner, raw) {
  return `${runner}::${raw}`;
}

function emit(out, entry) {
  out.write(JSON.stringify(entry) + '\n');
}

/** JSONL selection file -> array of raw ids (one per line, `{raw_id, key}`). */
function loadSelection(file) {
  const rows = [];
  const text = fs.readFileSync(file, 'utf8');
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row && row.raw_id) rows.push(row.raw_id);
    } catch {
      // a partially written line from a killed run: ignore it
    }
  }
  return rows;
}

function chunkSize() {
  const n = parseInt(process.env.HH_JS_CHUNK || '', 10);
  return Number.isFinite(n) && n > 0 ? n : 25;
}

function chunks(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

/** raw ids that already have a verdict in this ledger (a resume's starting point). */
function finished(file) {
  const done = new Set();
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return done;                       // no ledger yet: nothing is finished
  }
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row && row.type === 'end' && row.raw_id) done.add(row.raw_id);
    } catch {
      // a partially written last line from a kill: ignore it
    }
  }
  return done;
}

function openLedger(file) {
  const fd = fs.openSync(file, 'a');
  return {
    write: row => fs.writeSync(fd, JSON.stringify(row) + '\n'),
    close: () => {
      try {
        fs.closeSync(fd);
      } catch {
        /* already closed */
      }
    },
  };
}

/** Run a child process and capture it. Returns {code, stdout, stderr}. */
function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, {
    cwd: opts.cwd,
    env: { ...process.env, ...(opts.env || {}) },
    encoding: 'utf8',
    maxBuffer: opts.maxBuffer || 64 * 1024 * 1024,
    timeout: opts.timeoutMs || 0,
    shell: false,
  });
  return {
    code: res.status === null ? 124 : res.status,
    stdout: res.stdout || '',
    stderr: res.stderr || '',
    error: res.error ? String(res.error.message || res.error) : '',
  };
}

/**
 * The project's own binary, searching upwards like Node's module resolution.
 *
 * `root` is the suite directory, which in a monorepo (or in hh's own tests) can
 * sit under the package that installed the runner: `packages/web` has no
 * node_modules of its own, `packages/../node_modules` does.
 */
function localBin(root, name, relPath) {
  const tail = relPath || path.join('.bin', name);
  let dir = root;
  for (;;) {
    const local = path.join(dir, 'node_modules', tail);
    if (fs.existsSync(local)) return local;
    const parent = path.dirname(dir);
    if (parent === dir) return null;   // filesystem root
    dir = parent;
  }
}

function readJSON(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

/** Write one row per file: the shared end-of-chunk bookkeeping. */
function writeFileRows(ledger, runner, results) {
  for (const r of results) {
    ledger.write({
      type: 'end',
      key: keyFor(runner, r.raw_id),
      raw_id: r.raw_id,
      status: r.status,
      duration_ms: Math.round((r.duration_ms || 0) * 1000) / 1000,
      censored: false,
      ts: Date.now() / 1000,
    });
  }
}

function seal(ledger, count) {
  ledger.write({ type: 'suite_end', tests: count, ts: Date.now() / 1000 });
}

/**
 * The CLI every JS adapter shares.
 *
 * `impl.collect(root)` returns absolute paths; `impl.runChunk(root, files)`
 * returns `[{raw_id, status, duration_ms}]` for exactly those files. A runner
 * that cannot report a verdict for a file it was given gets a `failed` row
 * with 0 ms — recorded, not silently dropped.
 */
function main(runner, impl) {
  const [mode, rootArg, third, fourth] = process.argv.slice(2);
  const root = path.resolve(rootArg || '.');

  /** impl.collect, restricted to the suite root and de-duplicated. */
  const discover = () => [...new Set(impl.collect(root).filter(f => inside(root, f)))];

  if (mode === 'collect') {
    const files = discover();
    const out = third ? fs.createWriteStream(third) : process.stdout;
    for (const f of files) {
      const raw = rel(root, f);
      emit(out, { raw_id: raw, key: keyFor(runner, raw) });
    }
    if (third) {
      out.end();
      process.stderr.write(`hh: ${files.length} test files\n`);
    }
    return 0;
  }

  if (mode !== 'run') {
    process.stderr.write('hh: expected `collect` or `run`\n');
    return 2;
  }
  if (!third) {
    process.stderr.write('hh: run needs a ledger path\n');
    return 2;
  }

  // Resume: a unit that already has a verdict in this ledger is not re-run.
  // The record pass gets killed too, and re-running the whole suite on the
  // retry would both waste the time and write a second row for every finished
  // unit — the store would then count the same file twice.
  const done = finished(third);
  const selected = (fourth
    ? loadSelection(fourth).map(raw => path.join(root, raw))
    : discover()).filter(f => !done.has(rel(root, f)));
  const ledger = openLedger(third);
  let written = 0;
  try {
    for (const group of chunks(selected, chunkSize())) {
      const rows = impl.runChunk(root, group);
      const seen = new Map(rows.map(r => [r.raw_id, r]));
      for (const file of group) {
        const raw = rel(root, file);
        // A file the runner was given but said nothing about did not pass.
        writeFileRows(ledger, runner, [seen.get(raw) || { raw_id: raw, status: 'failed', duration_ms: 0 }]);
        written++;
      }
    }
  } catch (e) {
    // Do NOT seal: `suite_end` is hh's evidence that a process reached the end
    // of the suite. Sealing here would turn a crashed runner into a complete
    // run of zero tests.
    ledger.close();
    throw e;
  }
  seal(ledger, written);
  ledger.close();
  return 0;
}

module.exports = {
  walk, rel, inside, keyFor, emit, loadSelection, chunkSize, chunks, finished,
  openLedger, run, localBin, readJSON, writeFileRows, seal, main, SKIP_DIRS,
};
