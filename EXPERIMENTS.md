# hh Experiments — sandbox lab protocol

**Agent under test:** the Arena Agent (me), operating in this workspace via bash /
background-process tools. **Judge / timekeeper:** the user.
**Purpose:** prove, with numbers, that hh saves time and compute vs. the
estimate-and-restart failure mode — and that its own cost stays under the §8 bounds —
*before* any of it is trusted.

Environment facts (verified 2026-10-16): Python 3.13.16 with pytest importable,
Node 20+ (`node:test`, npm), Java (no build tools), 2 cores, x86_64. No go/cargo/dotnet/
ruby/php binaries — their adapters are tested on recorded fixtures. Workspace snapshots
exclude `.venv`/`node_modules`, so the core stays stdlib-only and fixtures avoid heavy
installs.

---

## 1. Roles & ground rules

- The naive failure mode maps onto my tooling exactly: a bash call's timeout is the
  harness limit; when it trips, output/partial progress is discarded; re-invoking with a
  bigger limit is the "restart from scratch". Scripted runs use `subprocess.run(timeout=…)`
  with identical semantics, so scripted and live numbers are directly comparable.
- Every attempt (naive or hh) goes through `python3 -m hh wrap -- <cmd> …` or
  `run_experiment.py`, which appends to `.hh/experiments/attempt_log.jsonl`:
  `{ts, policy, suite_id, seed, attempt, budget_s, outcome, wall_ms, wasted_ms, overhead_ms}`.
- I may not peek at fixture ground-truth duration files during live runs (they exist for
  scoring, not for the agent). Naive phases: I state my guess **before** the run in
  `.hh/experiments/guesses.md` (guess + one-line rationale), then run.

## 2. Fixture suites (`fixtures/`)

`fixtures/gen_suite.py` builds deterministic suites from seeds:

| Fixture | Shape | Purpose |
|---|---|---|
| `synth_a` | 300 tests, log-normal durations (median 20 ms, σ=1.0 on log scale), 2 session-fixture costs | baseline estimation accuracy |
| `synth_hang` | synth_a + 3 tests that sleep past any budget (hang suspects) | censoring, quarantine, restart-waste comparison |
| `synth_churn` | synth_a with 20 % of tests renamed/edited/new between runs (seed bump) | fuzzy tiers T1–T2, coverage |
| `synth_params` | parametrization explosion (5 tests × 40 params), mixed durations | param_hash identity |
| `synth_tiny` | 12 tests, total < 1 s | auto-degrade L2/L3, worst-case regret bound |
| `dual_runner` | `synth_py` (pytest) + `synth_js` (`node:test`), shared timing distribution | cross-runner composition (E5) |

Ground truth (`fixtures/.truth/<seed>.json`) is generated with the suite and is
**scoring-only**. Test bodies are `time.sleep`-based → fully deterministic, zero deps
for the unittest variant; a pytest variant exists since pytest is present here.

## 3. Policies under test

(see ARCHITECTURE.md Appendix B) `naive-doubling`, `naive-heuristic`, `hh-record-only`,
`hh-full`, `oracle`. In **live** phases, `naive-*` = *me* guessing (the real agent),
`hh-*` = me following `hh plan` output mechanically.

## 4. Scripted experiments (reproducible, run by `run_experiment.py`)

| ID | Claim tested | Method | Pass threshold |
|---|---|---|---|
| **E1** | overhead bound (§8.1) | same suite, hh off vs on, 3 reps, worst-case diff | `overhead ≤ max(5 ms, 0.02·R)`; meter self-report within 2× of measured diff |
| **E2** | estimator accuracy & calibration | synth_a/churn/params over ≥ 8 runs; MAPE by run-index; interval coverage | MAPE < 25 % @ run 3; p50/p90 coverage 85–95 % (scored as upper coverage: truth ≤ p90) |
| **E3** | restart waste eliminated | synth_hang under naive-doubling vs hh-full (equal starting guesses) | hh: `wasted_ms = 0`; wall-clock lower; per-run `overhead_ms` < 5 % of saved waste |
| **E4** | auto-degrade ladder | synth_tiny + stable synth_a (suite_sig unchanged ×3) | tiny → L2/L3; stable → L1; regret ≤ 50 ms on tiny |
| **E5** | cross-runner composition | dual_runner via composite manifest (unittest + node:test reporters in one store) | estimate error < 25 % vs summed ground truth; runner-affine shards |
| **E6** | shadow gate calibration | replay logged naive attempts through estimator (shadow mode) | savings-LCB ≥ 3× overhead computed from logs → gate flips to "enforcing allowed" |

**Status (2026-10-16):** E1–E6 run and green, live protocol L1 complete — see
`RESULTS.md` for numbers and the design fixes the experiments forced, and
`.hh/experiments/L1_summary.md` for the live A/B. E6 shadow gate: **ENFORCING
ALLOWED** (conservative savings LCB 14.0 s ≫ 3× overhead = 0.15 s).

**M4 done:** capture-layer conformance 13/13 recorded parsers + 13/13 live
(jest/mocha/node:test + Go `go test -json` streaming + JUnit Platform listener
and Surefire XML, all with SIGKILL checkpointing) — `experiments/conformance.py
--live-all`. Reference tool-handler `hh/tool.py` (schema + contract in
`TOOL_HANDLER.md`) shares the CLI's plan→execute→record code path; demo 11/11.

**Time-saved instrument done:** receipts per run + `hh savings` at job finish;
demo 6/6 (`experiments/time_saved_demo.py`, numbers in RESULTS.md, spec in
TIME_SAVED.md).

**E7 done — deadline-aware worker selection (§9 CI hook):** `choose_workers()`
wired into `hh plan --deadline` and the `run_tests` tool. On live_b_202 (warm):
predicted wall **26.53 / 13.25 / 6.62 s** vs measured **26.60 / 13.34 / 6.78 s**
at W=1/2/4 (≤ 2.4 % error); deadlines 40/15/12/9 s pick W=1/2/3/4 (smallest that
fits, run at W=3 for a 12 s deadline finished in 9.63 s); 6 s and 4 s are
reported **infeasible** instead of faked. Imbalance suite synth_a_41 (3 s pole):
rule stops at W=3, measured W=3→4 gain **1.1 %**. Verdict PASS (7/7 checks);
payload `.hh/experiments/results/e7.json`.

## 5. Live protocol L1 (user watches; Arena Agent is the agent)

**Counterbalanced A/B** so I can't cheat by learning a suite's timing:

- Pick 2 matched fixture families (same generator, different seeds): family α for
  naive phase, family β for hh phase (or alternate suites α/β/α/β across both).
- **Phase N (naive):** user says *"run suite X"*. I record guess + rationale in
  `guesses.md`, run with a bash timeout = my guess. On timeout I do what the failing
  agent does: raise the limit, restart from zero. All attempts through `hh wrap`.
- **Phase T (hh):** same instruction for the matched suite. I must call
  `hh plan` first and follow the returned budget/shards/escalation exactly
  (no restarts — resume from ledger).
- **Metrics per phase:** attempts count, total wall-clock, discarded compute (`wasted_ms`),
  agent turns spent, `overhead_ms` (self-metered), final correctness (all tests run).
- **Deliverable:** `.hh/experiments/L1_summary.md` with the side-by-side table and
  verdict lines for each §13 metric.

Suite budget for live runs: each suite ≤ ~2–3 min of true runtime so the A/B fits in a
handful of turns; hang tests ensure Phase N *will* produce at least one restart if my
guess is human (that's the point).

## 6. Commands cheat sheet

```bash
python3 fixtures/gen_suite.py --seed 41 --family synth_hang      # build suite + truth
python3 experiments/run_experiment.py --policy naive-doubling --suite fixtures/synth_hang_41
python3 experiments/run_experiment.py --policy hh-full      --suite fixtures/synth_hang_41
python3 experiments/run_experiment.py --suite all --rep 3          # full scripted matrix
python3 -m hh plan --selector fixtures/synth_hang_41 --workers 2 --runner unittest
python3 -m hh wrap -- python3 -m unittest discover -s fixtures/synth_hang_41
python3 -m hh report --suite-sig latest          # calibration + overhead health
```

## 7. What "win" looks like

1. **E1 green** ⇒ the §8.1 bound is real, not marketing. Claim becomes falsifiable fact.
2. **E3 + L1:** hh arm shows `wasted_ms = 0`, ≥ 2× fewer attempts, lower wall-clock,
   and every `overhead_ms` at least 10× smaller than the waste it avoided.
3. **E6:** shadow gate flips on logged data → we can honestly say "enforcement is
   justified on this workload", i.e. the *guarantee* in ARCHITECTURE §8.3.3 is satisfied
   empirically, not assumed.

If any gate fails, the milestone it belongs to does not ship — the cost guarantee is a
release criterion, not a footnote.

## 8. Log & artifact layout

```
.hh/
  observations.sqlite          # raw history (gitignored)
  aggregates.sqlite            # derived, committable
  runs/<ts>/ledger.jsonl       # per-test checkpoints
  experiments/
    attempt_log.jsonl          # every attempt, every policy
    guesses.md                 # live naive guesses + rationale (audit trail)
    L1_summary.md              # live A/B verdict table
    results/<exp-id>.json      # scripted experiment outputs
```
