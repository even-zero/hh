# Round 2 — a cold start is a record pass, not a prediction

> **Superseded for warm numbers.** The warm columns below were measured before
> the node-id execution path and the `attempts` fix. Current warm/normalized
> numbers: `REPORT-2026-10-20-warm-pass.md`.

**Date:** 2026-10-20 · **Branch:** `arena/221c8a97-hh` (PR #2)
**Repos:** the five fresh repos of `benchmark/repos.fresh.json` (markdown, click,
express, werkzeug, urllib3) · **Runner:** `python3 -m benchmark --repos-file
benchmark/repos.fresh.json all`, with five clean passes for the three executor
lanes.

---

## 1. The decision

Round 1 tried to make the *first* run of a suite smart: run a stratified sample,
re-plan from those rows, back the rest with bundled priors, multiply by a
cold-safety factor. It worked, in the sense that it produced numbers — and it was
the wrong product. hh exists to remove the guesswork from the **second** run
onwards. A first run has nothing to know from, so hh must not pretend: it should
do what a naive agent does, and write down what happened.

This round deletes the whole cold-start apparatus and replaces it with that.

## 2. What was deleted

| Removed | Where it lived |
|---|---|
| `make_sample_plan()`, the `sample` operating level | `hh/policy.py` |
| the measure → re-plan loop (`sample_phase`, `_sample_keys`, `_merge_stats`, `_SUM_KEYS`) | `hh/tool.py` |
| `PRIORS_MS`, `PRIOR_SIGMA_LOG`, `RUNNER_OVERHEAD_MS`, the T5 "bundled prior" tier | `hh/estimator.py` |
| `coverage_measured`, `confidence`, `cold_safety` | estimator / plan payload |
| cold deadline refusal (a feasibility claim with no evidence) | `hh/tool.py` |

The estimator's fallbacks are now: exact key → that file's mean → the suite mean →
0.0 ms with a wide `UNMEASURED_SIGMA_LOG` (0.6). Every one of those is a *measured*
neighbourhood; none is a bundled constant. A test with no rows and no file mean is
simply unknown, and the budget treats it that way.

## 3. What a cold run is now

`hh plan` on an empty store returns a **record plan** (real output, markdown/1,129
tests):

```json
{
 "total_budget_s": 30.0,
 "shards": [{"budget_s": 30.0, "tests": [], "quarantine": false, "full_suite": true}],
 "operating_level": "record",
 "predicted_wall_s": null,
 "predicted_exec_wall_s": null,
 "manifest_size": 0,
 "measured_tests": 0,
 "overhead_ms": 0.24,
 "workers_rationale": "cold store: no measurements yet — recording the suite like
                       a naive run, then planning from the rows"
}
```

and `hh run` executes it as:

- **one process, the runner's own discovery.** No test list is passed, no second
  collection pass is paid: `python -m hh.adapters.<runner> --root R --ledger L`
  discovers and executes exactly like the naive invocation, writing a ledger row
  per test.
- **the naive budget ladder.** 30 s → 60 s → 120 s, then stop. Double on timeout,
  retry the whole suite; a give-up is reported as `status: "incomplete"`, never as
  success. Nonzero exit with a short wall (test failures) is not a timeout.
- **no claim.** `predicted_wall_s` is `null`; with `deadline_s` set the plan says
  `feasible: null` plus a note, instead of refusing or promising.
- **the ledger becomes the cache.** A finished pass (one that wrote its
  `suite_end` marker) seals `<store>/.hh/manifest-cache-<runner>.json`,
  fingerprint-checked, with the measured collection cost. The next plan therefore
  needs *zero* discovery: 0.2 ms to plan cold vs 27–34 ms warm, and on click the
  warm plan skips a 3.2 s pytest collection entirely.
- **incomplete passes seal nothing.** If the process is killed or dies (import
  error, crash), there is no `suite_end` row: the manifest cache is not written,
  the shard is reported as failed, and the next plan is still cold. A half-finished
  suite can never become the test list the next plan trusts.

## 4. Benchmark changes

- **probe** now runs a new `hh discover` command (manifest audit) instead of
  reading the plan: a cold plan no longer enumerates, so the plan can no longer
  answer "what can hh see?".
- **clean** asserts the cold plan's level is `record` and the warm plan's is not,
  and records both.
- **results.json** gains `runs.<id>.record` = `{naive_s, hh_s, delta_pct, level,
  measured_after, n_runs}`, and `compare` a metric `exec.record_delta_pct`
  ("cold record pass vs naive — should be ~0").
- **collect** takes medians over the last five clean passes per lane: one noisy
  pass (5.4 s for a 3.5 s suite) previously read as +50 %.
- **dashboard** headline is the median cold delta over the three executor lanes,
  plus "cold plans that predicted: 0 of 5".

The two capture lanes (werkzeug, urllib3) are untouched by any of this — they never
used the executor's cold path; their numbers are the round-1 method, re-measured.

## 5. Results

### 5.1 Cold parity — the success condition

Medians of five clean passes per lane; "cold" is `hh plan` + `hh run` on an empty
store (the run itself is one full-suite process), "naive" is the repo's own suite
command with no hh at all.

| repo | runner | tests | naive | cold record pass | Δ | warm pass | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| markdown | unittest | 1,129 | 0.969 s | 1.006 s | **+3.9 %** | 1.018 s | +5.1 % |
| click | pytest | 2,287 | 6.538 s | 7.174 s | **+9.7 %** | 6.994 s | +7.0 % |
| express | mocha | 1,261 | 3.587 s | 3.846 s | **+7.2 %** | 3.695 s | +3.0 % |

Median across lanes: **+7.2 %**. Nothing is predicted, no extra process, no second
discovery, no shard shaping — the delta is the recording itself.

For comparison, round 1's cold lane (sample → re-plan → priors) was **+33 % / +32 %
/ +18 %** on the same three suites, and it was still claiming a wall-time estimate.

### 5.2 Where the delta comes from

- **Fixed, ~0.1 s:** the hh CLI process itself (import, store open, receipts,
  JSON payload) and ingestion of two ledger rows per test.
- **Per test:** one `write(2)` per ledger row. Measured in isolation, the unittest
  adapter *alone* is not slower than naive (0.914 s vs 0.930 s, median of 3 on the
  1,129-test markdown suite) — 2,258 rows cost less than the run-to-run noise.
- **pytest/mocha hooks:** +0.03 ms/test (unittest) vs +0.21–0.28 ms/test (pytest,
  mocha). That is the dominant residual and the reason click (+0.64 s) and express
  (+0.26 s) are above markdown (+0.04 s). On click the same suite measured
  interleaved against naive shows 6.538 s → 7.131 s for the adapter alone, so the
  cost is in the child process, not in hh's bookkeeping. Writing rows to
  `/dev/null` removes none of it: it is hook dispatch, not I/O.
- **Noise:** ±3–6 % run to run on these suites (2 vCPU sandbox). The markdown
  delta is inside that band; the click delta is not.

### 5.3 Warm lanes — unchanged by this round

| repo | naive | warm hh | Δ | predicted wall | pred. error |
|---|---:|---:|---:|---:|---:|
| markdown | 0.969 s | 1.018 s | +5.1 % | 0.989 s | −2.9 % |
| click | 6.538 s | 6.994 s | +7.0 % | 7.074 s | +1.1 % |
| express | 3.587 s | 3.695 s | +3.0 % | 3.870 s | −4.5 % |

- Workers=2: click 5.23 s (from 6.99 s at W=1), express 2.95 s (from 3.70 s).
- Resume (click): killed at 3.5 s, 1,198 of 2,287 tests already paid for, finish
  4.99 s vs naive's 3.5 s + 6.4 s restart.
- Resume (express): still a **loss** — 2.0 s kill + 5.04 s finish vs a 3.63 s full
  run. Unchanged from round 1 (a 3.6 s suite is too short for resume to pay).
- Hang (werkzeug, injected `test_never_finishes`): naive burns 30 s + 60 s and
  gives up with nothing; capture names the culprit from the open interval after
  each kill (`tests/test_zzhang.py::test_never_finishes`), keeps all 994 finished
  rows, and finishes green in 5.9 s on the descoped third attempt.

### 5.4 Discovery

`hh discover` against each repo as shipped:

| repo | manifest | executed | expected |
|---|---:|---:|---:|
| markdown | 1,129 | 1,129 | 1,129 |
| click | 2,287 | 2,287 | 2,287 |
| express | 1,261 | 1,261 | 1,261 |
| werkzeug | 1,019 | 994 | 994 (`-m 'not dev_server'` deselects 25) |
| urllib3 | 2,808 | 2,048 | 2,048 (`--ignore=test/with_dummyserver`) |

No repo is blind any more (round 1's headline gap). The last two rows are *over*-
discovery: hh's manifest honors pytest `addopts` but not the CLI flags carried in
`suite_cmd` (`-m`, `--ignore`). Both are capture lanes, so nothing executes from
the surplus; an executor-lane repo filtered that way would over-execute.

### 5.5 Capture lanes (method unchanged)

| repo | tests | naive | hh capture | Δ |
|---|---:|---:|---:|---:|
| werkzeug | 994 | 5.651 s | 5.609 s | −0.7 % |
| urllib3 | 2,048 | 12.163 s | 12.308 s | +1.2 % |

## 6. Honest limits

1. **Cold is not free.** +4 % to +10 % on these suites, and it is bookkeeping, not
   work: nothing is executed twice, no extra process runs the suite. On a sub-second
   suite the fixed ~0.1 s dominates; on a 6.5 s suite the pytest hooks dominate.
2. **The ladder ceiling is naive's.** A suite that cannot finish in 120 s makes hh
   stop exactly where naive stops, with `status: "incomplete"` and partial rows
   kept. hh does not claim otherwise.
3. **Resume on a cold store pays one discovery.** If a *record* pass is killed, it
   never wrote its manifest, so `plan --resume` re-collects (click: 3.1 s). This is
   the price of not trusting a half-finished suite.
4. **`suite_cmd` CLI filters are invisible to discovery** (see 5.4).
5. **express at W=2 misses its prediction by 39 %** (2.11 s predicted vs 2.95 s
   wall): a plan predicts one startup, a two-shard run pays two. Pre-existing, warm
   lane only, not part of this round's claim.

## 7. Reproduce

```bash
python3 -m benchmark --repos-file benchmark/repos.fresh.json setup
python3 -m benchmark --repos-file benchmark/repos.fresh.json run     # probe/clean/workers/resume/hang
for i in 1 2 3 4; do
  python3 -m benchmark --repos-file benchmark/repos.fresh.json run \
      -s clean -r markdown -r click -r express
done
python3 -m benchmark --repos-file benchmark/repos.fresh.json collect
python3 -m benchmark --repos-file benchmark/repos.fresh.json dashboard
```

Artifacts: `reports/2026-10-20-cold-start-results.json`,
`reports/2026-10-20-cold-start-dashboard.html`, and
`reports/2026-10-20-cold-start-compare.md` — the last one is *not* meaningful for
this repo set: `compare` maps the frozen tornado-era baseline id by id, and these
five repos have no counterpart there. Judge this round from the tables above and
from the dashboard, not from that diff.
