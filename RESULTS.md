# hh — measured results (sandbox, 2026-10-16)

Agent under test: Arena Agent. Harness: this workspace (Python 3.13, 2 cores).
Full protocol: `EXPERIMENTS.md`. Artifacts: `.hh/experiments/results/*.json`.
All experiments green. Reproduce with `python3 experiments/run_experiment.py --exp all`.

## Scoreboard

| Exp | Claim | Result | Verdict |
|---|---|---|---|
| **E1** | cost bound (§8.1) | self-metered **83.7 ms = 0.94 % of R** (R = 8.88 s suite); gate 2 % = 177.6 ms. Wall-diff **−4.2 s** (parallel shards beat the plain run) | **PASS** |
| **E2** | estimator accuracy | cold start 56.4 % err (bundled priors overestimate) → **4.2 % after 1 recorded run**, stays ~4 %; p90 upper coverage **100 %** (gate: < 25 % after 3 runs) | **PASS** |
| **E3a** | under-estimation + restart | naive-doubling (g0 = 2 s): **4 attempts, 22.9 s wall, 14.0 s wasted**. hh (1 history run + planned run): **13.5 s wall, 0 wasted**, overhead 87 ms. Saved 9.4 s wall / 14.0 s waste on a *13 s suite* | **PASS** |
| **E3b** | true hangs | naive (g0 = 15 s): **give-up after 3 attempts, 105.1 s pure waste**, no results (suite cannot complete at any budget). hh: completes every run with 3 hangs censored + reported; learning curve **48.8 s → 24.1 s → 8.6 s** steady state | **PASS** |
| **E4** | auto-degrade | tiny suite (138 ms truth): plan correctly drops to **record-only**; self-metered overhead 24.9 ms; wall regret 310 ms (two extra interpreter startups dominate at this scale) | **PASS** |
| **E5** | cross-runner (§10.5) | unittest + node:test in one store; composed test-mass **738 ms vs 729 ms truth = 1.2 % err** (shared overhead counted once) | **PASS** |
| **E6** | shadow gate (§8.3.3) | 3 naive↔hh pairs: restart-waste avoided **[14.0 s, 105.1 s, 15.0 s]** vs mean overhead **51.1 ms**; conservative LCB 14.0 s ≫ gate 3× overhead = 0.15 s | **ENFORCING ALLOWED** |
| **L1** | live A/B, agent in the loop | naive: **2 blind guesses, 41.2 s wall, 15.0 s discarded** (guess 15 s vs truth 26.1 s). hh: cold 13.4 s + warm 13.4 s, **0 waste, 0 guesses** | **PASS** |

## Headline answers

**"Does the tool always cost less than re-evaluating and restarting?"**
Measured: hh's own work is **< 1 % of suite time** (E1), and one avoided restart
paid for **~160 runs** of that overhead (E3a: 14 s waste vs 87 ms). With parallel
shards, total wall-clock went *down* even before counting avoided restarts.
Even the worst case (E4, 138 ms suite where nothing can go wrong) is bounded at
~300 ms — and the auto-degrade ladder is what makes that the *ceiling*, not average.

**"Where does the time actually go?"** Steady-state hh on the hang suite (E3b run 2):
8.6 s wall = ~8.8 s of real tests on 2 workers + 2 s fail-fast probes for 3 known
hangs. The naive agent spent 105 s and produced nothing.

## Findings that changed the design (empirically, not anecdotally)

1. **fsync-per-ledger-row is too expensive** (~300 ms per 150-test run). Flush-only
   survives SIGKILL — the failure mode that matters — at ~10 µs/row. (E1 first run)
2. **Parallel shards turned overhead negative.** LPT bins are disjoint by construction;
   running them on W workers cut an 8.9 s suite to 4.6 s. The `S_bal` term in the
   cost model is not a footnote.
3. **Guard the log-normal.** A censored/uncensored mix blew one sigma up so far the
   planner reserved **7 days for a 90 s suite**. Now: sigma ≤ 1.5, p90 ≤ 8×p50,
   budget ≤ 6× predicted mass + 30 s. Tails are handled by checkpoints, not budgets.
4. **A 25 s "hang" is a slow test — budgeting for it is correct.** True hangs must
   never complete; the fixture now sleeps 600 s. (This distinction is the whole point
   of the censoring/quarantine machinery.)
5. **Quarantine tier-1 probes must not inflate off a censored bound** (1.5× bound ⇒
   99 s spike). One censored bound ⇒ probe once at the normal per-test cutoff;
   repeated censoring ⇒ 2 s fail-fast probe. Probes run in parallel (disjoint keys).
6. **Synthetic suites need unique test identities** (module tags): same-named files
   across suites share file-level aggregates. Real repos are protected by path prefixes;
   fixtures now are too.
7. **Cold-start honesty:** bundled priors were 56 % high on this family. One recorded
   run fixes it (4 %). The cold run is the cost of admission; shadow mode measures it
   before enforcement (§8.3.3) — and here it is quantified.

## L1 — the live A/B (Arena Agent as the agent under test)

Full table in `.hh/experiments/L1_summary.md`; audit trail (guesses committed
before each attempt) in `.hh/experiments/guesses.md`. Matched `live_slow` suites,
seeds 101/202; truth withheld from both phases until scoring.

- **Phase N (me guessing):** count-based estimate 15 s for "108 unittest tests" —
  the reported failure mode verbatim. Timeout at 15 s ⇒ **15 s discarded** ⇒
  double to 30 s ⇒ pass at 26.2 s. Totals: **2 attempts, 41.2 s wall, 15 s waste.**
  My guess was **11.1 s below truth** — counts don't encode durations.
- **Phase T (me following `hh plan`):** cold plan 22.2 s from priors (30 ms to
  compute), 2 shards × 13.6 s, completed at 13.4 s wall just inside budget;
  warm plan p50 27.4 s vs 26.4 s truth (3.8 % err), clean run 13.4 s.
  **0 waste, 0 guesses.** (Wall is ~½ the test mass because shards run on 2 workers.)
- **E6 verdict on all logged data (E3a/E3b + live): ENFORCING ALLOWED** —
  conservative savings LCB 14.0 s per avoided restart vs 3× overhead = 0.15 s.

## M4 — capture-layer conformance (`python3 experiments/conformance.py --live-js`)

**13/13 recorded parser fixtures + 13/13 live checks PASS** (`--live-all`). Live: real Jest, Mocha
and node:test runs through the hh reporters (durations within expectation:
300 ms sleep → 301 ms measured, 400 ms → 401 ms; statuses incl. skip/fail
correct), plus SIGKILL mid-suite with fast tests already on disk (jest 2/2,
mocha 2/2 rows checkpointed). Recorded fixtures cover Surefire, PHPUnit, RSpec,
GoogleTest, Catch2, cargo-nextest, mocha-junit, pytest-junitxml, .NET JUnit
logger, JUnit Platform paren names, go test -json, VSTest TRX.
**Live runtimes beyond Python/JS** (installed on demand, offline-after-once):
Go 1.27.2 via the `go_stream` wrapper (Tier A streaming + `parse()` cross-check,
SIGKILL checkpointing: 4 rows on disk while the 20 s test still ran) and a real
JUnit Platform run on OpenJDK 11 — our `HhJUnitListener` (Tier A) and the
launcher's Surefire XML (Tier B) agreed on all 5 rows, with SIGKILL checkpointing
verified. Quirks the matrix fixed: .NET logger emits a fully-qualified `name`
(naive class-prepend doubled identity — 11/12 → 12/12), node:test reports skips
as `test:pass` with `data.skip=true`, JUnit Platform's XML writer emits
`name="fast()"` with parentheses (JUnit 4 tools don't).

## Reference tool-handler (`hh/tool.py`, see TOOL_HANDLER.md)

One agent-facing tool (`run_tests`) on the *same* code path as the CLI
(refactored so plan→execute→record cannot drift). Demo 9/9 checks: cold→warm
learning (coverage 0→1.0; warm budget 34.9 s vs p90 30.4 s, 13.4 s wall),
`budget_too_small` refusal in 0.05 s when the agent's deadline (20 s) is under
the honest bound, forced run after raising it discovers a newly added test.
The demo exposed and fixed a real agent-relevant bug: stale `sys.modules`
entries hid an edited test file on the second call in the same process.

## E7 — deadline-aware worker selection (`hh plan --deadline`, `run_tests(deadline_s=…)`)

`choose_workers()` answers the CI question "how many workers do I actually need?"
Two live-measured results (`e7.json`):

| W | predicted wall | measured wall | error | shard bound honoured |
|---|---|---|---|---|
| 1 | 26.53 s | 26.60 s | 0.3 % | ✓ (39.9 s bound) |
| 2 | 13.25 s | 13.34 s | 0.7 % | ✓ (20.9 s bound) |
| 4 | 6.62 s | 6.78 s | 2.4 % | ✓ (11.5 s bound) |

- **Deadline rule** (smallest W that fits, less CI contention): deadlines
  40/15/12/9 s → W=1/2/3/4 on the warm live suite; the 12 s-deadline run finished
  in **9.63 s** with the same checkpointing guarantees; 6 s and 4 s are reported
  `infeasible` — predicted best-effort wall 6.63 s — instead of being faked.
- **Imbalance stop** (the case where parallelism no longer pays): synth_a_41 has
  one 3 s long pole that cannot split. Predicted curve stops at W=3 (W=4 adds
  0.4 %); measured W=2→3 = 4.51→3.09 s (real 32 % gain) but W=3→4 = **1.1 %** —
  the rule stops exactly where the measurements say it should, and the
  recommended W lands within 1.1 % of the 4-worker wall.
- Default worker cap is `os.cpu_count()` (don't oversubscribe a CI box); the
  4-worker runs above pass `max_workers=4` explicitly because the suite is
  sleep-heavy. **7/7 checks PASS.**

## Time-saved instrument (`hh/savings.py`, spec in TIME_SAVED.md)

Every run emits a receipt; `hh savings --job J` totals it at job finish. Demo
(`experiments/time_saved_demo.py`, **6/6 checks**) on one store:

| call | wall | receipt | kind |
|---|---|---|---|
| cold clean run | 13.4 s | **0.0 s** | honest zero — nothing at risk |
| resume same job | 0.003 s | 26.5 s | `resume` (108 tests already paid for) |
| 9 s shard budgets (4 attempts, 2 censored) | 13.0 s | 17.9 s | `continuation` (6 slow tests kept); censored wait noted, not summed |
| 3 × 600 s hangs, caller budget 60 s | 8.5 s | 51.5 s | `hang_bound` (censored at 2 s probes) |
| 2 s CI deadline (best effort 6.6 s) | ~0 s | 2.0 s | `refusal` (deadline protected) |
| **job finish** | 34.9 s spent | **97.8 s saved** | 4 kinds, 2.8× — work *not re-paid*, not wall-clock compression |

Also fixed on the way: `ingest_ledger` now takes a byte offset (a resumed run
re-ingests nothing), and `synth_hang_41` holds **155** tests ("41" is the seed —
the old belief that it was 41 tests came from reading the name; the ledger
confirms 155 start + 155 end rows).

## Caveats (honest lab notes)

- Sleep-based synthetic tests overstate *fractional* framework overhead vs real tests
  with real bodies; process-startup overheads are relatively larger on tiny suites.
- E3b's naive arm is capped at 3 doubling attempts (it would never complete — attempts
  4+ would also time out against 3× 600 s hangs).
- The live L1 protocol (user watches me play naive vs hh-assisted in conversation)
  is ready to run next; scripted numbers above are the reproducible core.
