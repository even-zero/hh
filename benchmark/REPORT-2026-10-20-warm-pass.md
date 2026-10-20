# Round 3 — a warm pass is the naive command plus a ledger

**Date:** 2026-10-20 · **Branch:** `arena/04e269e0-hh` → PR #2 ·
**hh:** 1.0.0 (`4d6a0d2`) · **Repos:** `benchmark/repos.fresh.json`

Round 2 removed cold-start prediction: a cold store gets a *record pass* (the
naive invocation, plus ledger rows). Round 3 answers the follow-up question —
**why is the warm (post-record) pass still not faster than naive, and is it
still being compared against cold-start priors?**

Short answer: it is not compared against anything cold. There are no priors left
in the tree (`PRIORS_MS` is gone, `OPERATING_LEVEL=cold` never samples). A warm
pass pays everything the naive command pays **and** the recording that makes the
next budget exact — so measuring the *test work* cannot show a win. What moved
is the **budget** and the **second-and-later wall clock**: on a suite longer than
the naive ladder's first rung, warm finishes in 36.2 s where naive pays 66.1 s.

Everything below is measured on this sandbox (2 vCPU), with the same repo
revisions as round 2, at `hh 1.0.0 (4d6a0d2)`.

## 1. What a warm pass actually does

Agent-facing tool call (`run_tests`), warm store:

| stage | markdown | click | express | what it is |
|---|---:|---:|---:|---|
| interpreter + `import hh.tool` | 19 ms | 19 ms | 20 ms | the price of being a Python program |
| plan (metered) | 15 ms | 15–38 ms | 12–27 ms | read the store, estimate, shape shards — cache hit, no collection |
| run (child process) | 0.97 s | 5.9–6.8 s | 3.4–3.5 s | the tests themselves, by measured node id |
| ingest + aggregates | 30–80 ms | 57–115 ms | 37–70 ms | ledger → SQLite, rebuild aggregates |

The naive arm pays stage 1 (25 ms, `python3 -m <runner>`) and stage 3. It does
not pay stages 2 and 4. That is the whole of the warm delta:

- **markdown, 1,129 tests / 0.86 s:** +0.22 s (recording 55 ms in the child,
  plan 15 ms, ingest 30–50 ms, imports 34 ms, ledger reads).
- **click, 2,287 tests / 6.67 s:** −0.37 s. Warm planning means the executor
  never re-collects the 33 k parametrizations just to deselect 31 k of them, and
  the child runs by node id. That saving (≈0.75 s) is larger than recording
  (≈0.4 s), so warm is *faster* than naive on this suite.
- **express, 1,261 tests:** indistinguishable from naive (−0.1 to +0.1 s in
  median; individual passes on this box drift 3.2 s → 6.2 s).

## 2. The fair test: alternating A/B

`scenario_clean` measures naive → cold → warm back to back. On a box whose
speed drifts, ordering is a bias. `benchmark/interleave.py` alternates the arms
every pass (naive first on even passes, hh first on odd ones) and adds a third
arm — **the capture adapter alone**, i.e. the same runner and the same tests with
only the ledger added. That splits "recording inside the child" from "hh around
it".

`python3 -m benchmark.interleave --repos-file benchmark/repos.fresh.json --reps 7 --mode all`

W=1 (hh warm = plan + tool call; adapter = child only):

| repo | naive | adapter alone | hh warm | Δ vs naive | paired median (hh − naive) |
|---|---:|---:|---:|---:|---:|
| markdown | 0.89 s | 0.95 s | 1.10 s | **+24.0 %** | +226 ms |
| click | 6.67 s | 7.02 s | 6.15 s | **−7.7 %** | −366 ms (6 of 7 passes) |
| express | 4.37 s | 3.51 s | 4.29 s | **−1.6 %** | +96 ms (noise) |

W=2 (hh's measured plan uses the second core; naive has no such option):

| repo | naive | hh warm | Δ |
|---|---:|---:|---:|
| markdown | 0.91 s | 1.11 s | +23.0 % |
| click | 6.66 s | 4.07 s | **−38.9 %** |
| express | 5.00 s | 2.71 s | **−45.8 %** |

Read the paired medians, not the percentages: markdown's +0.23 s is the same
absolute cost on every pass (it is 26 % only because the suite is 0.86 s). Click
wins because the suite is big enough for the collection it avoids to dominate
the recording it pays. Express is inside the noise band of this sandbox.

`scenario_clean`, medians of the collected lanes (same run, `results.json`):

| repo | naive | cold record pass | Δ | warm pass | Δ |
|---|---:|---:|---:|---:|---:|
| markdown | 0.967 s | 1.228 s | +26.9 % | 1.002 s | +3.5 % |
| click | 7.386 s | 7.765 s | +5.1 % | 7.308 s | −1.1 % |
| express | 4.057 s | 4.277 s | +5.4 % | 4.555 s | +12.3 % |

These are single passes of a noisy box (markdown's cold lane was 1.006 s in
round 2, 1.228 s here); the interleaved table above is the measurement to trust.

## 3. Where the value is: the budget, not the test work

Every suite in `repos.fresh.json` finishes inside the naive ladder's first rung
(30 s), so the clean scenario *cannot* show value in a correct budget: naive pays
the suite once and so does hh. `benchmark/budget_probe.py` builds a suite that
does not fit — 18 tests × 2 s = 36 s, `--workers 1` so the budget cannot be split.

| arm | budget | wall | attempts | vs naive |
|---|---|---:|---:|---:|
| naive, ladder 30 → 60 s | 30 s timout, then 60 s | 66.1 s | 2 | — |
| hh cold (record plan, same ladder) | 30 s, then retry | 38.2 s | 2 | −42.2 % |
| **hh warm** | **measured 46.291 s** | **36.2 s** | **1** | **−45.3 %** |
| hh warm, second run | same | 36.2 s | 1 | −45.3 % |

The warm budget is derived from rows only:

```
manifest_size 18 · coverage 1.0 · basis "measured"
mass 36,005.7 ms for 18 tests (2,000.3 ms/test, 18 suite rows)
p50 37.348 s · p90 40.253 s · budget = p90 × safety = 46.291 s
predicted wall 36.006 s → actual 36.155 s (+0.4 %)
```

Nothing here is a prior, a sample, or a cold estimate: it is the sum of recorded
durations plus the recorder's own measured overhead (`runner_overhead_ms`,
0.0 ms on this suite because all attempts were single-shot). The cold plan in the
same run has `basis: "cold"`, `coverage: 0.0`, `predicted_wall_s: None` — it
refuses to predict rather than predicting from a guess.

## 4. What this round removed to make warm cheaper

| change | effect |
|---|---|
| pytest executes by measured node id (`status`, `rc==4` falls back) | no collection of 33 k params followed by deselecting 31 k; stale ids still fall back safely |
| `execute_plan` reads the ledger before a run only if one exists | −20 ms per warm pass on 1,100 tests; a fresh plan has a fresh checkpoint dir |
| `runs.attempts`, `runner_overhead_ms` counts `attempts = 1` only | killed/resumed runs no longer leak 111 ms/test of phantom overhead into every later budget |
| `startup_max_s` rather than summed shard startup | `wall − exec` no longer reports 3.85 s of "startup" on a 4.0 s wall |
| `no_tests` instead of `ok` when the runner sees nothing | a wrong runner (`unittest` on a pytest tree) can no longer look green |

Measured, not assumed: the node-id path was 6.21 s vs naive 6.72 s interleaved on
click, and the ledger-before-run read was a visible 20 ms on markdown.

## 5. Honest limits

- **Recording is not free and cannot be.** ~35 µs/test (ledger rows + ingest +
  aggregates). On a sub-second suite that is a visible percentage; on a real CI
  suite it is noise. Anyone promising "zero overhead for the second run" is
  either not recording or not counting ingest.
- **The box is noisy.** Run-to-run drift on express and markdown reaches ±20 %;
  single-pass percentages in `results.json` are not comparable across days. The
  interleaved probe exists because of this, and its paired medians are the number
  to quote.
- **Resume on a 3.6 s suite still loses** (round 2 result, unchanged): the kill
  and restart cost more than the remaining work. hh reports that instead of
  hiding it.
- **Manifest drift is detected, not prevented.** A stale node id yields `rc=4`
  and the adapter falls back to collect-and-deselect; if a stale manifest names
  *fewer* tests, the run is not sealed (`suite_end` is only written on exit 0/1)
  but the missing tests are not yet diffed against a fresh collection.

## 6. Reproduce

```bash
python3 -m benchmark --repos-file benchmark/repos.fresh.json run     # all scenarios
python3 -m benchmark --repos-file benchmark/repos.fresh.json collect
python3 -m benchmark --repos-file benchmark/repos.fresh.json dashboard
python3 -m benchmark.interleave --repos-file benchmark/repos.fresh.json --reps 7 --mode all
python3 -m benchmark.interleave --repos-file benchmark/repos.fresh.json --reps 5 --mode all --workers 2
python3 -m benchmark.budget_probe
python3 -m unittest discover -s tests -q                               # 52 tests
```

Artifacts: `reports/2026-10-20-warm-results.json`,
`reports/2026-10-20-warm-dashboard.html`,
`reports/2026-10-20-warm-interleaved.json`,
`reports/2026-10-20-warm-budget-probe.json`. No `compare.md` this round: the
frozen baseline's metric names embed the original repo ids, so a diff against it
is not meaningful for this repo set (see `benchmark/README.md`).
