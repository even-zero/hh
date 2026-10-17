# hh — Agent Timeout Estimator: Architecture

*Naming history: this design began as the internal research kit **TDA
(Test-Duration Advisor)**, was briefly called **hyper8**, and is now **hh**.
The lab logs keep the old suite ids (`e3a_tda`, …) because they are recorded
data, not names.*

**Status:** proposal v2 (revised: cost-guarantee model, full capture layer, resolved
decisions, sandbox-lab plan with the Arena Agent as the agent under test)
**Problem:** an agent guesses a suite runtime, allocates that as a limit, and on timeout
raises the limit and *restarts from scratch* — burning compute and wall-clock every miss.
**Proposal:** a harness-pluggable utility that keeps a running record of per-test durations,
fuzzy-extrapolates suite cost from that history, and hands the agent a *budget + execution
plan* instead of letting it guess — **with a structurally bounded overhead that is
guaranteed cheaper than the restart path it replaces**.

---

## 0. Assumptions & environment

- The "agent" is an LLM/automation loop that shells out to a test runner with a tool-call
  timeout. The estimator sits *below* the agent's decision layer, not in its prompt.
- **Primary test environment = this sandbox**, with the **Arena Agent as the agent under
  test** (see §14 and `EXPERIMENTS.md`). Verified toolchain: Python 3.13 + pytest
  (importable), Node 20+ (`node:test`, npm available), Java present w/o build tools,
  2 cores, x86_64. Go/Rust/.NET/Ruby/PHP adapters ship as parsers tested on recorded
  fixture outputs.
- **Core is stdlib-only** (sqlite3, json, statistics, re, argparse, subprocess): zero
  install cost, survives workspace snapshots (venv/node_modules are snapshot-excluded),
  runs anywhere the agent runs.
- Store is **repo-local** `.hh/` (resolved decision, §15).
- Reference implementation Python-first; protocol language-neutral (CLI/JSON).

---

## 1. Goals / Non-goals

**Goals**

1. Predict suite runtime *before execution*, with a calibrated uncertainty interval.
2. Recommend a timeout budget and a sharded, checkpointed execution plan.
3. Learn continuously from every run (including failed/timed-out ones).
4. Plug into any test harness through a small adapter interface (matrix in §10).
5. Make "raise the limit and rerun everything" structurally unnecessary.
6. **Cost guarantee:** the tool's own overhead is structurally bounded and provably
   cheaper than the restart path it replaces (§8) — measured, self-metered, falsifiable.

**Non-goals**

- Universal *per-run zero overhead* — impossible for any instrumentation; we replace it
  with a hard overhead bound, a relative cap, and bounded regret (§8.3).
- Profiling *why* tests are slow; flaky detection (except hang quarantine); replacing the
  runner's own parallelism.

---

## 2. Feasibility & prior art

- Test durations are **locally stable**; suites evolve incrementally; heavy-tailed but
  stationary enough for quantile estimation.
- Runners already emit timing (`pytest --durations`, Jest perfStats, `go test -json`
  Elapsed, JUnit XML everywhere) — **capture can be nearly free** (§3).
- Timing-aware scheduling is solved territory: Bazel per-test timing history, `pytest-split`
  knapsack splitting, Develocity / CI test-analytics timing DBs.
- Missing piece — and what this design adds: the **closed loop into an agent's tool
  budget** (estimate → budget → checkpointed execution → record → recalibrate) with
  tiered fuzzy matching, censoring-aware statistics, and a cost model that guarantees
  the loop pays for itself.

---

## 3. Design laws (the cost guarantee is enforced by construction)

These are hard constraints, not aspirations. They are what make the §8 bound provable:

1. **Never add a pass over the suite.** No separate collect-only run inside the execution
   path. Two-phase planning (§11.5): pre-flight budget from the *cached* manifest +
   filesystem diff; shard plans finalized *during* the run's own collection phase, for
   free (plugin hook), before the first test executes.
2. **Never block test execution on I/O.** Observations go to an in-memory queue; a
   write-behind buffer flushes in batches (or at run end). If the store is unavailable,
   spill to a JSONL sidecar and import later. Losing the last few rows on SIGKILL is an
   acceptable trade; stalling a test is not.
3. **All per-test work is O(1) with a µs-class constant** (queue append + one buffered
   JSONL ledger line ≈ ≤ 100 µs/test including flush). Fixed cost per run ≈ ≤ 20 ms.
   Anything superlinear (fuzzy matching) gets a **time budget** (50 ms) and then degrades
   a tier.
4. **Every plan self-reports `overhead_ms`.** The advisor meters itself (wall + CPU around
   its own calls) and stamps it into every plan/report. Overhead is a regression-tested
   invariant (§8.5), not a hope.
5. **Auto-degrade over hard-fail.** If measured overhead or expected benefit goes the
   wrong way, the advisor drops to a cheaper operating level (§8.4) instead of continuing
   to cost money. There is a level whose cost is provably trivial.
6. **Core makes no network calls and spawns no test processes of its own.** It advises;
   the agent's tool handler (or CI step) executes.

---

## 4. The core insight most designs miss

> A better guess reduces timeouts. **Checkpointed sharding eliminates the cost of
> the timeouts that still happen.** You need both.

The estimator alone will still miss (new tests, machine noise, hangs). If a miss still
means "kill and restart from zero", you have only reduced waste's frequency. The
execution plan runs **resumable shards with per-test checkpoints**, so a budget extension
only ever runs the *remainder*. This converts estimation errors from **catastrophic**
(full restart) to **bounded** (one capped shard extension) — which is exactly what makes
the cost guarantee in §8 hold even when predictions are wrong.

Second insight: **predict quantiles, not points.** The agent needs "p50 = 3m40s,
p90 = 5m10s → allocate 6m in 4 shards", not "3m42s".

---

## 5. System architecture

```
              ┌──────────────────────────────────────────────────┐
              │        Agent / CI loop (Arena Agent in lab)      │
              │        "run tests" tool  /  pipeline step        │
              └───────┬──────────────────────────────▲───────────┘
                      │  ExecutionPlan (timeout,     │  actuals, per-test
                      │  shards, checkpoint dir)     │  events (streamed)
              ┌───────▼──────────────────────────────┴───────────┐
              │  ADVISOR (policy + API/CLI) + OVERHEAD METER     │
              │  plan() → Estimate + ExecutionPlan + overhead_ms │
              │  finalize() → record actuals, calibration health │
              └───────┬──────────────────────────────▲───────────┘
                      │  SuiteManifest (2-phase)      │  Observations
      ┌───────────────▼───────────┐      ┌───────────┴──────────────────────┐
      │   ESTIMATOR (pure core)   │      │   CAPTURE LAYER (adapters, §10)  │
      │  · key normalization     │      │  Tier A native stream: pytest,   │
      │  · fuzzy match tiers     │      │   unittest, jest, vitest, mocha, │
      │  · hierarchical priors   │      │   go test, node:test, ExUnit …   │
      │  · overhead + scaling    │      │  Tier B parsers: JUnit XML, TRX, │
      │  · quantile estimation   │      │   gtest, catch2, nextest, xcode… │
      └───────────────▲───────────┘      │  Tier C console regex fallback   │
                      │ aggregates       └───────────┬──────────────────────┘
              ┌───────┴──────────────────────────────▼───────────┐
              │   DURATION STORE (repo-local .hh/ SQLite)       │
              │   raw observations (append-only, gitignored) +   │
              │   aggregates (materialized, committable)         │
              └──────────────────────────────────────────────────┘
```

---

## 6. Data model

Raw observations stay append-only; aggregates are materialized. You will re-tune the
estimator later and cannot recompute quantiles/censoring from averages.

```sql
CREATE TABLE observations (
  id           INTEGER PRIMARY KEY,
  run_id       INTEGER NOT NULL REFERENCES runs(id),
  runner       TEXT NOT NULL,          -- composite key = runner || '::' || test_key
  test_key     TEXT NOT NULL,          -- normalized key (§7.1)
  raw_id       TEXT NOT NULL,
  file_path    TEXT,
  name         TEXT,
  param_hash   TEXT,
  content_hash TEXT,
  status       TEXT NOT NULL,          -- passed|failed|error|timeout|skipped
  duration_ms  REAL NOT NULL,
  censored     INTEGER NOT NULL DEFAULT 0,  -- 1 ⇒ duration is a LOWER BOUND
  ctx_id       INTEGER NOT NULL REFERENCES contexts(id),
  ts           REAL NOT NULL
);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, runner TEXT, git_sha TEXT, suite_sig TEXT,
  collection_ms REAL, total_ms REAL, wall_ms REAL, started_at REAL
);
CREATE TABLE contexts (
  id INTEGER PRIMARY KEY, machine_fp TEXT, workers INTEGER, runner_ver TEXT, extras JSON
);
CREATE TABLE aggregates (           -- materialized, rebuilt incrementally
  level TEXT, level_key TEXT,       -- 'test'|'file'|'dir'|'cohort'|'global' (+runner prefix)
  n INTEGER, n_censored INTEGER,
  p50 REAL, p90 REAL, mean_ewma REAL, mad REAL, updated_at REAL,
  PRIMARY KEY (level, level_key)
);
```

- `censored=1` rows are **right-censored** (`duration ≥ limit`) — used as lower bounds (§7.6).
- `content_hash` decays confidence on edits without rename.
- `machine_fp` is a coarse class fingerprint (cpu class/cores/mem), not hostname.
- `suite_sig` = hash(sorted test keys) → trivial fast path for unchanged suites.
- **Layout:** `.hh/observations.sqlite` (raw, **gitignored**), `.hh/aggregates.sqlite`
  (derived, optionally committed for team-shared history), `.hh/runs/<ts>/ledger.jsonl`
  (checkpoints). Static priors are bundled constants in the tool — no global store.

---

## 7. Estimation algorithm

### 7.1 Identity: normalization before fuzziness

```
raw:    tests/api/test_users.py::TestCreate::test_post[user_admin-True]
norm:   pytest::tests/api/test_users/test_post :: user_admin|True
```

Drop runner line-noise, canonicalize separators, keep a stable hash of param *values*.
Composite key includes the runner so cross-runner suites never collide.

### 7.2 Fuzzy matching, tiered with confidence weights

| Tier | Match rule | w | Typical use |
|---|---|---|---|
| T0 | exact key + recent `content_hash` | 1.00 | steady state |
| T1 | exact key, content changed | 0.70 | edited test |
| T2 | fuzzy name (unique) + file-stem similarity ≥ θ | 0.60 | moved/renamed file |
| T3 | same file → file-level per-test aggregate | 0.40 | new test in known file |
| T4 | same cohort (dir / markers / param-count bucket) | 0.25 | new area |
| T5 | global prior (per runner, bundled) | 0.10 | cold start |

`ŷ(t) = w · d_matched + (1 − w) · prior(t)`, with `d_matched` a robust location
(EWMA-mean ⊕ median) of the matched history. This is the "fuzzy extrapolation":
*fuzzy identity* (T1–T2), *fuzzy cohort inheritance* (T3–T4), *fuzzy suite-level reuse*
(suite_sig / duration-mass overlap: "92% of last run's suite by duration mass ⇒ scale
the residual"). T2 is only attempted for unknowns, only against stem-indexed candidates,
and only inside a 50 ms time budget before degrading to T3 (Law 3).

### 7.3 Suite model

```
T_serial = c0 + c1·N + Σ_t ŷ(t)            # c0/c1 measured (collection, per-test overhead)
T_wall   ≈ T_serial / W · (1 + λ·CV_shard)  # W = workers; CV_shard from plan's own balance
```

Collection/import/session-fixture cost is real (10–30% of fast suites) — `c0` is
measured per run (`collection_ms`) and modeled, never ignored.

### 7.4 Quantiles

Per-cohort empirical or log-normal fit; suite quantiles via Monte-Carlo convolution of
per-test draws. Outputs `p50/p90/p99` + `coverage` (duration mass matched at T0–T1).
Measured guards (RESULTS.md #3): `sigma_log ≤ 1.5`, `p90 ≤ 8×p50`, `p99 ≤ 20×p50`,
and the policy clamps budget to `min(6× predicted mass + 30 s, max_budget)` — tails
are handled by checkpoints and the escalation ladder, never by absurd reservations.

### 7.5 Budget rule (policy layer)

`recommended_timeout = clamp(p90 · safety(ctx) + overhead_margin, min, max)`
with `safety` raised on noisy machine fingerprints or poor recent calibration. Because
sharding + checkpoints exist, an under-budget is no longer catastrophic — bias to p90.

### 7.6 Censoring & hang suspects

Timeout at limit `L` ⇒ `duration=L, censored=1`, true value `≥ L`. Censored rows pull
aggregates up via Kaplan–Meier-style handling per cohort (or conservative `L·penalty`,
flagged). Isolation tiers (measured refinement, RESULTS.md #5):
- **tier 1** (one censored bound): own shard, probed once at the normal per-test cutoff;
- **tier 2** (repeatedly censored, still failing): own shard, 2 s fail-fast probe
  (catches fixes cheaply), reported as a first-class failure — never silently skipped.
"Suite timed out, restart" becomes "that one test was cut; everything else is done".
Probes are disjoint by construction ⇒ run in parallel with normal shards.

### 7.7 Recency, drift, invalidation

EWMA (α ≈ 0.3); `content_hash` change ⇒ immediate drop to T1; incremental aggregate
refresh after each run (raw rows kept for re-tuning); winsorize / MAD filter so one run
on a loaded box can't double estimates.

### 7.8 Cold start

Bundled per-runner priors (order-of-magnitude constants); first runs are
conservative-budget + record-everything; after ~3 runs per file T0–T3 dominate.

---

## 8. Cost model & guarantees

> **Question:** can we *always* guarantee the tool costs less than re-evaluating and
> restarting the suite? **Short answer:** per-run *vs. the restart path* — yes, hard,
> by 2–3 orders of magnitude. Per-run *vs. a hypothetical world where nothing would
> have gone wrong* — zero overhead is logically unattainable for any instrumentation,
> but the delta is bounded by a self-enforced cap (~2% of run time, floor ≈ ms) and
> amortized net-positive is guaranteed above a tiny restart rate, verified in shadow
> mode *before* enforcement. Full accounting below.

### 8.1 The cost of hh (upper bound)

Every component is O(N) with µs-class constants (enforced by the laws in §3):

| Item | Bound | Notes |
|---|---|---|
| Pre-flight `plan` (fast path) | ≤ 5 ms | cached manifest + fs diff + dict lookups |
| Pre-flight `plan` (cold, 10k keys) | ≤ 50 ms | one indexed batch query; fuzzy capped at 50 ms |
| In-run capture | ≤ 100 µs/test | queue append + batched write-behind INSERT |
| Checkpoint ledger | (incl. above) | one buffered JSONL line + flush per test |
| Shard planning at collection | ≤ 10 ms | LPT sort, O(N log N), during free collection hook |
| Aggregation refresh | ≤ 20 ms | incremental, at finalize |

```
C_tda ≈ 20 ms + 100 µs · N          (per run, worst-case target)
```

Examples: 200 tests → ~40 ms. 2 000 tests → ~220 ms. 10 000 tests → ~1.0 s.
Worst-case overhead *ratio* on realistic suites (where 10k tests ≈ minutes of runtime):
**≤ 0.1–1 %**. And hh **never** costs a second suite pass (Law 1) — the expensive
things (imports, execution) are counted exactly once.

### 8.2 The cost of the failure path (what we compare against)

Naive loop with guess `G`, doubling on failure: each miss costs a **discarded attempt**
(work done up to the kill ≈ `G`, kept by nothing) **plus** the eventual full re-run.
Per restart event: `C_restart ≈ G_wasted ≥ min(G, partial progress) ≫ C_tda`, and
`G ≥ R` on the iteration that finally succeeds, so `C_restart ≥` one full attempt of
the suite.

### 8.3 The guarantee, stated precisely (four tiers)

1. **Structural guarantee (hard, per-run, vs. the restart path):**
   `C_tda ≈ 0.02 + 0.0001·N` seconds vs `C_restart ≥` one full attempt `≥ R`.
   For any suite where a restart is *the alternative being compared against*, hh is
   2–3 orders of magnitude cheaper — provable from §3 + §8.1 alone, no statistics needed.
2. **Relative-overhead cap (hard, enforced at runtime):** the advisor meters itself and
   auto-degrades (§8.4) until `overhead ≤ θ·R`, θ = 2 % default. This is an invariant,
   not an expectation.
3. **Amortized guarantee (statistical, gated):** net savings per run =
   `p_re·W_re + S_ov + S_bal − C_tda` (`p_re` = restart probability of the naive policy,
   `W_re` = discarded work, `S_ov` = over-allocation holding cost, `S_bal` = sharding
   balance gain). Shadow mode measures `p̂_re, Ŵ_re` **before** enforcement; we switch
   to enforcing only when the lower confidence bound of savings ≥ 3× `C_tda`.
   Break-even restart rate is tiny: for a 60 s suite with N = 2 000 (`C_tda ≈ 0.22 s`),
   `r/k > 0.22/60 ≈ 0.4 %`. The problem statement's premise (the agent *does* restart)
   sits orders of magnitude above that line.
4. **Bounded regret (hard, even when the estimator is wrong):** checkpoints + the
   escalation ladder cap a mis-estimate at one shard extension ≤ `max_budget`, never a
   full restart. Worst-case per-run loss vs. oracle ≤ `C_tda + capped extension`.

**What we cannot guarantee (honesty clause):** (a) per-run, that the naive agent *would*
have restarted — that counterfactual is unknowable per run (it is measurable in aggregate,
and shadow mode measures it); (b) strictly positive savings on micro-suites run exactly
once with no restart risk — there the worst case is a regret of *tens of milliseconds*
(2 s suite: C_tda ≈ 22 ms ≈ 1 %), which the auto-degrade ladder reduces further. No
instrumentation that does anything can promise literal zero; what we promise is a
**provable upper bound, a self-enforced relative cap, bounded regret, and a measurable,
gated net-positive guarantee** — falsifiable numbers, not vibes.

### 8.4 Auto-degrade ladder (the "always" backstop)

| Level | Active machinery | Cost floor | When |
|---|---|---|---|
| L0 full | plan + shards + ledger + capture | `C_tda` as above | unknowns>0, high variance, large suite |
| L1 light | cached plan, no sharding, buffered capture | ~10 µs/test + 5 ms | stable suite_sig, fits budget comfortably |
| L2 record-only | capture only (rows for the future) | ~20 µs/test | benefit/overhead ratio unfavorable |
| L3 off | nothing | 0 | micro-suites (< 1 s), `--no-hh`, or ratio still bad at L2 |

The ladder only ever moves *toward* cheaper. L2 is the net-positive floor: it preserves
learning at trivial cost; L3 exists for the pathological tail so the tool can never be
net-negative by more than the metered, capped amount.

### 8.5 Falsifiability & regression tests

- Every plan/report carries `overhead_ms` (self-metered) — the claim is checkable in
  production data, not just in docs.
- `EXPERIMENTS.md` E1 is a standing regression: same fixture suite, hh off vs on ×3,
  assert `overhead ≤ max(5 ms, 0.02·R)` and `overhead < savings` on hang-laden fixtures.
- The shadow gate (savings LCB ≥ 3× overhead) is a *release criterion* for turning
  enforcement on — the guarantee is proven on the target workload before it is trusted.

---

## 9. Policy layer

```python
@dataclass(frozen=True)
class ExecutionPlan:
    total_budget_s: float
    shards: list[Shard]            # ordered test list + per-shard budget
    checkpoint_dir: str            # per-test JSONL ledger
    per_test_cutoff_s: float | None
    workers_hint: int | None       # (decision #2) advisor MAY suggest reducing W
    predicted_wall_s: float        # LPT point estimate: max shard load / makespan
    deadline_decision: dict | None # set when a CI deadline drove the W choice
    workers_rationale: str | None  # e.g. "CV_shard 0.31 → 2 workers optimal"
    escalation: EscalationLadder
    operating_level: str           # "full"|"light"|"record-only" (§8.4)
    overhead_ms: float             # self-metered, Law 4
```

- **Sharding:** LPT (longest predicted first) bin-packing across `W` slots — stragglers
  first so the tail can't serialize at the end.
- **Checkpointing:** append to `checkpoint_dir/ledger.jsonl` + flush *before* the next
  test starts; re-entry skips ledgered tests. Retries become cheap by construction.
- **Escalation ladder** (replaces "double the limit and rerun"):
  1. shard over budget ⇒ extend *that shard only* to p95, resume from ledger;
  2. per-test cutoff hit ⇒ mark censored, move on, report hang suspect;
  3. unknown-heavy suite ⇒ more, smaller shards + higher safety factor.
- **Within-shard order:** predicted-descending (slowest first ⇒ earliest bad news).

---

## 10. Capture layer — full adapter matrix

Two adapter flavors:

- **Tier A — native streaming** (best fidelity: live events ⇒ checkpointing + censoring
  of its own timeouts; collects manifest during the run's own collection).
- **Tier B — structured-output parsers** (post-run ingest, record-only but full-fidelity
  durations; covers runners via their standard machine formats).
- **Tier C — console regex fallback** (any command; low-confidence aggregates; last resort).

### 10.1 Tier A — native streaming adapters

| Runner / tool | Language | Mechanism | ckpt | Notes |
|---|---|---|---|---|
| **pytest** (+ plugins) | Python | plugin: `pytest_collection_finish` + `pytest_runtest_logreport` | ✔ | durations free on report; content hash from item source |
| **pytest-xdist** | Python | worker→master report hooks, aggregated at master | ✔ | parallel-aware (workers recorded in ctx) |
| **unittest / testtools** | Python | custom `TestResult` / `load_tests` (stdlib) | ✔ | zero-dep; the lab's first fixture runner |
| **nose2** | Python | plugin API | ✔ | |
| **Jest** | JS/TS | custom reporter (`testResult.perfStats`) | ✔ | also `--json` ingest (Tier B twin) |
| **Vitest** | JS/TS | custom reporter, JSON event stream | ✔ | |
| **Mocha** | JS/TS | custom reporter (runner `pass/fail` events + duration) | ✔ | |
| **Jasmine** | JS/TS | reporter API | ◐ | durations sometimes coarse |
| **node:test** | JS | `--test-reporter` custom / `--test-reporter=junit` | ◐ | zero-dep Node runner (lab's JS fixture) |
| **Playwright Test** | JS/TS | custom reporter | ✔ | e2e-heavy; per-spec durations |
| **Go** (`go test`) | Go | `-json` event stream (`Elapsed`, subtests) | ✔ | textbo§ok-perfect stream; no deps |
| **Dart / Flutter test** | Dart | `--machine` JSON protocol | ✔ | |
| **ExUnit** | Elixir | formatter behaviour | ✔ | |
| **CTest** | C/C++ | `--output-junit` + verbose stream | ◐ | CMake projects |

### 10.2 Tier B — structured-output parsers (JUnit XML is the lingua franca)

One `junit-xml` parser (with a per-runner quirks table for name formats, param encoding,
time units, skipped/xfail) covers the majority of the remaining ecosystem:

| Format | Emitted by |
|---|---|
| **JUnit XML** | JUnit 4/5 (via build tools), **TestNG**, **Maven Surefire/Failsafe**, **Gradle**, **sbt/ScalaTest**, **PHPUnit** (`--log-junit`), **Pest**, **RSpec** (junit/json formatter), **Mocha** (mocha-junit-reporter), **Robot Framework**, **Kotlin** test tasks |
| **VSTest / TRX** | `dotnet test` (NUnit, xUnit, MSTest) — TRX→JUnit converter |
| **gtest XML** | GoogleTest `--gtest_output=xml` |
| **Catch2 JUnit** | `--reporter junit` |
| **cargo-nextest JUnit** | Rust CI profiles (also libtest `--report json` where available) |
| **xcresulttool** | XCTest via `xcodebuild -resultBundlePath` |
| **Bazel BEP JSON** | `--build_event_json_file` (test summaries w/ timing — ingest as history) |

### 10.3 Tier C — console regex fallback

A per-ecosystem library of duration regexes (e.g. `ok 1.23s`, `PASS 0.05s`) applied to
raw console output of *any* command. Explicitly degraded: no param identity, aggregate-only
history, low confidence weight (≤ T4). Exists so that "unknown runner" is never "no data".

### 10.4 Meta-runners (orchestrators)

Not adapters themselves — the adapter attaches to the **underlying** runner; the
orchestrator contributes overhead to `c0` and grouping to the manifest:

| Meta-runner | Attach to | Extra accounting |
|---|---|---|
| tox / nox | per-env underlying runner | env startup → `c0` per env |
| npm scripts / nx / turbo / make | per-script underlying runner | orchestration overhead → `c0` |
| Bazel | native (BEP ingest) + per-test runner | remote-cache hits recorded in ctx |
| CI matrix shards | each job = its own run + ctx | cross-job aggregates merge by key |

### 10.5 Capability legend & cross-runner composition

Capabilities per adapter: `collect()` (manifest w/o running), `stream()` (live events),
`ckpt()` (checkpoint-capable), `censor()` (can mark its own timeouts as censored),
`par()` (parallelism-aware).

**Cross-runner suites** (decision #5): `SuiteManifest = {subs: [Manifest(runner, adapter)]}`.
The estimator sums per-sub estimates + one shared overhead model (§7.3); the planner runs
LPT over a global shard pool **with runner affinity** (a runner's own parallelism config
is respected inside its shards, decision #2 lets it suggest W changes). Store keys are
`runner::norm_key`, so a monorepo's pytest + jest + go history coexists in one DB and
cross-runner cohort priors stay runner-scoped.

**Lab coverage:** pytest + unittest + node:test live in this sandbox; Go/JVM/PHP/Ruby/.NET
adapters validated against **recorded fixture outputs** (checked-in sample XML/JSON in
`fixtures/recorded/`) so the matrix is tested even without those toolchains.

---

## 11. Integration surface

### 11.1 Pure core

```python
class Estimator:
    def estimate(self, manifest: SuiteManifest, ctx: RunContext) -> Estimate: ...

@dataclass(frozen=True)
class Estimate:
    p50: float; p90: float; p99: float
    point: float
    recommended_timeout: float
    breakdown: dict[str, float]
    coverage: float
    unknowns: list[TestKey]
```

### 11.2 Adapter protocol (the only runner-specific surface)

```python
class HarnessAdapter(Protocol):
    def collect(self, sel: SuiteSelector) -> SuiteManifest: ...
    def run(self, plan: ExecutionPlan, on_event: Callable[[TestEvent], None]) -> RunResult: ...
    def parse(self, raw: bytes) -> list[Observation]: ...   # Tier B/C ingest
```

### 11.3 CLI (the agent's entire vocabulary)

```
hh plan    --selector "tests/" --workers 2 --runner pytest   # → JSON ExecutionPlan
hh record  --run-id ...                                       # ingest actuals
hh report  --suite-sig ...                                    # calibration + overhead report
hh wrap    -- <cmd>                                           # Tier C fallback runner/attempt logger
```

JSON contract keeps the agent's tool handler dumb on purpose:

```json
{
  "recommended_timeout_s": 372,
  "est": {"p50": 221, "p90": 340, "p99": 505, "coverage": 0.94},
  "shards": [{"budget_s": 96, "tests": ["…"]}],
  "checkpoint_dir": ".hh/runs/2026-10-16T14-02/ledger.jsonl",
  "per_test_cutoff_s": 120,
  "workers_hint": 2, "workers_rationale": "CV_shard 0.31 → 2 workers optimal",
  "escalation": {"shard_over_budget": "extend_to_p95_resume", "test_cutoff": "censor_and_continue"},
  "operating_level": "full",
  "overhead_ms": 38
}
```

### 11.4 Where it sits in the agent loop

```
agent wants to run tests
  → tool handler calls hh plan (never the LLM's guess)
  → handler executes shards, streaming events to the adapter
  → adapter queues observations; ledger flushed per test
  → on completion/timeout: hh record; return results
  → next hh plan is smarter; overhead_ms accumulates for the meter
```

Prompt-side estimation is *deleted*, not improved.

### 11.5 Two-phase planning (Law 1 concretized)

1. **Pre-flight (`hh plan`, before the runner starts):** budget + provisional shards from
   the *cached* manifest reconciled with a filesystem diff of test dirs (ms-level; new or
   deleted files adjust counts and tier-down to T3–T4 for the delta). No imports, no
   collection pass.
2. **In-run finalize (collection hook, free):** the adapter sees the exact manifest during
   the run's own collection phase and rewrites the shard assignment *before test one
   executes*. Exactness at zero marginal cost; the pre-flight budget is refined upward/
   downward (never triggers a restart — shards checkpoint regardless).

---

### 11.6 Utility receipt — reported time saved (implemented: `hh/savings.py`)

Every run writes a receipt; `hh savings [--job J]` (or the `time_saved` block on
every tool response) reports it. Credit is **conservative and ledger-backed**:
only work that a restart-from-zero baseline *provably re-pays* counts.

| kind | credited when | amount |
|---|---|---|
| `continuation` | a shard exceeded its budget and the extension resumed the remainder instead of restarting | Σ durations of tests finished before the extension (credited once per test) |
| `resume` | a call re-used this job's ledger (`resume=true`) | Σ durations of tests already paid for in this job |
| `hang_bound` | a hang was censored at the cutoff *and* the caller supplied a deadline | `deadline − wall` (the most a blocking baseline could have burned) |
| `refusal` | `budget_too_small` / `deadline_infeasible` | the caller deadline the refusal protected |

Rules that keep the number honest:
- unbounded counterfactuals (a suite blocked forever by a hang with no caller
  deadline) are **noted, never summed**;
- a fast clean run earns a receipt of **0** — "no savings to claim" is valid;
- savings are work *not re-paid*, not wall-clock compression: a 6-worker run is
  fast, not "saving" seconds against itself;
- receipts live in the repo's store (decision #1), so a job spanning two repos
  has two receipt books; `HH_JOB` groups receipts *within* a store.

## 12. Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| Renamed/moved tests | tiered fuzzy T2; optional git-rename awareness later |
| Edited tests | `content_hash` ⇒ T1, blend with prior |
| Hangs / infinite loops | censored observations; hang-suspect quarantine; per-test cutoff |
| Parametrization explosion | `param_hash` identity; per-param history w/ cohort fallback |
| Noisy shared machine | `machine_fp` buckets; median/MAD; winsorize |
| First run / new repo | bundled priors; conservative factor; shadow mode |
| One monster slow test | isolation shard + cutoff; own line item |
| Suite grows 40% overnight | suite_sig fast-path drops; coverage falls; safety widens |
| History poisoning | append-only + outlier filter + `hh report` residuals |
| Different parallelism than history | `workers` in ctx; scale model; re-bucket (decision #2) |
| hh itself slows things down | Law 4 meter + auto-degrade ladder + E1 regression (§8) |
| Store corruption/lock | write-behind + spill-to-JSONL; store is never on the test hot path |

---

## 13. Evaluation plan

Modes: **shadow** (estimate beside the agent's guesses) → **advisory** (agent uses
budget, keeps restart behavior) → **enforcing** (budget + shards + checkpoints;
restart disabled). Metrics, with cost as a **first-class metric from day zero**:

1. **hh overhead ratio** = `overhead_ms / R` (bound: ≤ 2 %, target ≤ 1 %).
2. **Over-allocation ratio** = allocated / actual (target 1.1–1.25).
3. **Timeout events per 100 runs** (target ≈ 0 after warmup).
4. **Restart waste** = wall-clock spent on later-discarded work (target 0 with ckpt).
5. **Calibration** = coverage of [p50, p90] (want ≈ 90 %).
6. **Cold-start MAPE** by run index (< 40 % at run 2, < 15 % at run 5).

**Shadow gate (§8.3.3):** enforcing is enabled only when savings-LCB ≥ 3× overhead on
the target workload — the cost guarantee is *proven before it is relied upon*.

---

## 14. Milestones — revised for this sandbox, with the Arena Agent as the agent under test

All paths relative to the workspace. Core is stdlib-only; nothing depends on
snapshot-excluded dirs (`.venv`, `node_modules`). Long runs use the background-process
tools; short ones plain bash. `EXPERIMENTS.md` holds the full protocol.

| Milestone | Scope (concrete) | Acceptance gate |
|---|---|---|
| **M0 — lab kit + free capture** | `hh/` core package (store, schema, meter), `fixtures/gen_suite.py` (deterministic synthetic suites: log-normal durations, hang test, params, session fixture), adapters: `unittest_stream`, `pytest_stream`; `experiments/run_experiment.py --policy naive|hh`; naive policies (doubling, heuristic); attempt logger (`hh wrap`) | E1 passes: overhead ≤ max(5 ms, 2 %·R); observations land with correct censoring |
| **M1 — estimator + plan** | tiers T0/T3/T5 + overhead model + `hh plan` (two-phase: cached-manifest pre-flight); bundled priors; `overhead_ms` in every plan | E2: MAPE < 25 % after 3 runs on synthetic ground truth; plan ≤ 50 ms @ 10k keys; overhead gate holds |
| **M2 — fuzzy + quantiles + planner + degrade** | T1–T2 fuzzy w/ 50 ms budget; censoring/KM; MC quantiles; LPT planner; auto-degrade ladder; `workers_hint` | E2: 90 % interval coverage 85–95 %; E4: ladder transitions correct on tiny/stable suites |
| **M3 — checkpointed execution + live A/B** | ledger + shard executor (per-test cutoff) + escalation ladder; JSON contract; **live protocol L1: Arena Agent plays naive (estimates + bash timeouts) vs hh-assisted runs on matched suites** | E3 scripted: restart waste 0 in hh arm, wall-clock lower, overhead < bound. L1: user observes fewer attempts, less wall-clock, fewer agent turns |
| **M4 — portability + cross-runner + optional ML** | `node:test` + Jest/Mocha adapters (Node present), JUnit-XML parser + recorded fixtures for Go/JVM/PHP/Ruby/.NET; cross-runner composition (E5); revisit learned model **only if** T0–T2 coverage high yet MAPE > 15 % (decision #4) | E5 passes on a two-runner fixture (pytest + node:test); parser conformance on `fixtures/recorded/` |
| **M4.5 — live Go/JVM + CI hook** | Go streaming wrapper (`go_stream`, Tier A) + `go test -json` parser live; JUnit Platform run live two ways: `TestExecutionListener` (Tier A streaming) **and** Surefire XML (Tier B) cross-checked; deadline-aware `choose_workers` (§9) wired into `hh plan --deadline` and the `run_tests` tool | Conformance 13/13 recorded + 13/13 live; SIGKILL checkpointing on Go/JVM/JS; E7: predicted-vs-actual wall ≤ 2.4 % error at W∈{1,2,4}, deadline rule picks smallest fitting W, imbalance stop confirmed (1.1 % marginal gain at last useful W) |

Sandbox mapping of the naive failure mode: the bash tool's per-call timeout plays the
harness limit; a timed-out call discards output/partial progress exactly like the real
failure path; re-invoking with a bigger timeout = the restart. `run_experiment.py`
reproduces the same semantics with `subprocess.run(timeout=…)` for scripted runs, so
scripted numbers and live numbers are comparable.

---

## 15. Resolved decisions (were open questions in v1)

1. **Store scope — per repo.** `.hh/` in the repo: raw observations + run ledgers
   gitignored; `aggregates.sqlite` small and optionally committed for team-shared
   history. Static priors bundled with the tool. **No user-global store.**
2. **Advisor may reduce `W` — yes; and reduce it *to a deadline*.** `workers_hint` +
   `workers_rationale` in the plan; the executor applies it as a hint (never silently).
   Evidence: historical `CV_shard`, plus `choose_workers()`: given a CI deadline it picks
   the **smallest** worker count whose predicted makespan fits (fewer workers = less CI
   contention); with no deadline it stops adding workers once the marginal LPT gain
   drops below 5 % — the long-pole case where parallelism stops paying (E7: 3 s pole ⇒
   W=3 last useful, W=3→4 measured gain 1.1 %). Default cap = CPU count; raise
   `max_workers` for sleep-heavy suites. Infeasible deadlines are reported, never faked.
3. **Privacy — local-only.** Core has zero sockets (Law 6); store is local; committing
   aggregates is explicit opt-in. Timing data never leaves the repo.
4. **Learned models — revisit at M4,** gated: only if tiered heuristic's residual error
   is demonstrably the bottleneck (T0–T2 coverage high, MAPE > 15 %). Default is no ML.
5. **Cross-runner suites — supported.** Composite manifests, per-runner estimation summed
   with shared overhead, runner-affine sharding, `runner::key` store identity (§10.5).

## 16. Remaining open questions

1. pytest-xdist worker aggregation edge cases (worker crash ⇒ partial manifest).
2. Whether committed `aggregates.sqlite` needs a merge/rebase story for concurrent CI
   writers (v1: last-writer-wins + raw rows never committed, so re-aggregation is lossless).
3. How aggressive `suite_sig` fast-path should be in the live A/B (measurable in E4).

---

## Appendix A — why "fuzzy" needs guardrails

Loose fuzzy matching is worse than no matching: confidently attaching 200 ms of history
to a 200 s test poisons the budget in the dangerous direction. Rules: fuzzy matches are
*blended with priors*, never adopted whole; ambiguity ⇒ drop a tier, don't guess;
`Estimate.coverage` is reported so policy auto-widens safety when coverage is low.

## Appendix B — naive baseline policies (for honest comparison)

Implemented in `experiments/policies.py` so the claim "saves time and compute" is tested
against the actual described failure mode, not a strawman:

- `naive-doubling`: guess `G₀` (e.g. 30 s); on timeout set `G←2G`, **restart from zero**.
  This is the exact reported behavior.
- `naive-heuristic`: guess from crude signals (e.g. `2 s × file count`) with the same
  restart behavior — models a mildly smarter agent.
- `hh-record-only` and `hh-full`: the two relevant operating levels.
- `oracle`: budget = actual (lower bound on achievable cost; measures estimator headroom).
