# hh — faster agentic testing

**Your agent guesses how long the tests take, sets that guess as a timeout, and
when the guess is low the harness kills the run — discarding everything — and the
next attempt starts again from zero.** hh replaces the guess with a
measurement, checkpoints every finished test so it is never re-paid, quarantines
hangs instead of letting them eat the whole budget, and reports the utility it
actually produced.

Stdlib-only core · zero sockets · zero data leaves the repo · works with
unittest, pytest, Jest, Mocha, node:test, Go, and the JUnit-XML family.

```
naive agent (measured, live A/B)          hh
────────────────────────────────          ──────────────────────────────────
guess 15 s → timeout → 15 s discarded      plan from history → 13.4 s wall
guess 30 s → pass at 26.2 s                warm run: 13.4 s, 0 discarded
41.2 s wall · 2 blind guesses             26.8 s wall · 0 guesses · 0 waste
```

---

## Contents

- [Quickstart for agents](#quickstart-for-agents)
- [The failure it removes](#the-failure-it-removes)
- [CLI reference](#cli-reference)
- [How it works](#how-it-works)
- [Guarantees and their honest extent](#guarantees-and-their-honest-extent)
- [Measured results](#measured-results)
- [Capture layer (runners)](#capture-layer-runners)
- [Time-saved receipts](#time-saved-receipts)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Releasing](#releasing)
- [License](#license)

---

## Quickstart for agents

There is no server and no daemon. hh is a library plus one CLI, so "using
it" means one of two things: registering the `run_tests` tool with your agent
framework, or shelling out to the CLI.

### 1. Install from a git URL

```bash
pip install "git+https://github.com/YOUR-ORG/hh.git@v1.0.0"
hh --version          # hh 1.0.0
```

Zero-install alternative (no pip, no venv, stdlib only):

```bash
git clone --depth 1 --branch v1.0.0 https://github.com/YOUR-ORG/hh.git
cd hh && python3 -m hh --version
```

### 2. Register the tool

```bash
hh tool-spec          # JSON schema to hand to your framework's tool registry
```

```python
import json
from hh.tool import TOOL_SPEC, handle_tool_call

# register TOOL_SPEC with your agent framework, then route calls:
result = json.loads(handle_tool_call("run_tests", {
    "root": "path/to/suite",   # suite root
    "workers": 2,
    "budget_s": 60,            # your deadline for this call
}))
print(result["status"], result["results"]["n_failed"], result["time_saved"]["job_total_s"])
```

The response is deliberately small (~340 tokens): status, measurements, failures
(capped at 20), the slowest tests, what it learned, and the time-saved receipt.

### 3. Or just use the CLI from any shell

```bash
export HH_JOB=my-agent-session        # groups receipts across runs

hh plan --root path/to/suite --out /tmp/plan.json   # estimate + shards + budget
hh run  --plan /tmp/plan.json --root path/to/suite  # checkpointed execution
hh savings --job my-agent-session                   # what it saved so far
```

---

## The failure it removes

An agent that has never run a suite has no idea how long it takes. Counts do not
tell you: in our live A/B, 108 unittest tests looked like "about 15 s" from the
test count and actually took **26.1 s**, because 8 of them were 3-second tests.
The agent guessed 15 s, the harness killed the run at 15 s, 15 seconds of
finished work were discarded, the guess doubled to 30 s, and the suite finally
passed — **41.2 s wall and 15.0 s of wasted compute for a 26 s suite**.

That failure mode has three costs, and hh attacks each one:

| cost                             | naive                                              | hh                                                                  |
| -------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------- |
| the guess is wrong               | count-based estimate, off by 74 %                  | budget from measured per-test history (4.2 % MAPE after one run)    |
| a timeout discards finished work | restart from zero, re-pay everything               | per-test checkpoints; an under-budget shard extends the _remainder_ |
| one hanging test eats the run    | suite blocks to the harness limit, returns nothing | hang probes censored at 2–12 s; the other tests still report        |

---

## CLI reference

| command                                                         | what it does                                                               |
| --------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `hh tool-spec`                                                  | print the `run_tests` JSON schema for an agent framework                   |
| `hh plan --root DIR [--deadline S] [--workers N] [--resume]`    | estimate the suite, pick budget/shards/workers, write a plan (pure JSON)   |
| `hh run --plan PLAN.json --root DIR [--suite-id ID]`            | execute the plan with checkpoints, record to the store, print a receipt    |
| `hh savings [--job J] [--json]`                                 | job-finish receipt: time saved by kind                                     |
| `hh report`                                                     | store summary: tests tracked, hangs known, recent runs, time saved         |
| `hh wrap --policy P --suite-id S --attempt N --budget B -- CMD` | attempt logger for _any_ command (used to measure naive policies honestly) |
| `hh record --ledger L --root DIR --runner R`                    | ingest an externally produced ledger                                       |

Common flags: `--store-root DIR` (default `.`), `--runner unittest|pytest|node`,
`--cutoff S` (per-test censor cutoff, default 12 s).

---

## How it works

```
        hh plan                    hh run                       store
   ┌───────────────────┐        ┌────────────────────────┐      ┌──────────────────┐
   │ manifest (in-proc)│        │ W shards, disjoint     │      │ observations     │
   │ estimate per test │  ───>  │ checkpoint-ledger each │ ───> │ aggregates       │
   │ LPT shards + W    │        │ hang probes (2 s)      │      │ attempts         │
   │ budget + bound    │        │ escalate: extend, not  │      │ savings receipts │
   └───────────────────┘        │ restart                │      └──────────────────┘
                                └────────────────────────┘                │
                                     next plan is a measurement <─────────┘
```

1. **Estimate, don't guess.** Per-test durations come from previous runs in a
   per-repo store (`.hh/`), fuzzy-matched through tiers: exact key → file →
   sibling → runner prior. Cold, it uses bundled priors and says so
   (`coverage: 0.0, n_unknown: 108`); after one run the same suite is 100 %
   covered and the budget is a measurement (4.2 % MAPE measured; prediction vs
   actual wall was within 2.8 % across worker counts).
2. **Budget = bound, not point.** The plan reserves `p90`-style safety, and
   clamps it (`≤ min(7200 s, test_mass × 6 + 30 s)`) so a censored tail can never
   reserve a week (a real bug we hit and fixed).
3. **Shards, sized to workers.** Longest-predicted-first bin packing; the plan
   publishes `predicted_wall_s` (the makespan point estimate) so the caller can
   check the promise afterwards. `--deadline` picks the _smallest_ worker count
   that fits — fewer workers means less CI contention.
4. **Execution is checkpointed.** Every finished test lands in an append-only
   ledger (flush per row, no fsync — survives SIGKILL at ~10 µs/row). When a
   shard exceeds its budget, the process is extended and resumes the remainder;
   finished tests are never re-run. `--resume` does the same across calls.
5. **Hangs are quarantined.** A test that censors twice is probed in isolation at
   a 2 s cutoff; its estimate is treated as a lower bound and it is reported, not
   swallowed.
6. **Auto-degrade.** Tiny suites (`< 1 s` of test mass) get the _record-only_
   path: one process, no sharding, no checkpoint machinery — measured overhead
   ~25 ms.
7. **Report the utility.** Every run writes a receipt; `hh savings` totals it.

Design docs: [ARCHITECTURE.md](ARCHITECTURE.md) (the spec, including the
measured failure modes that shaped it), [EXPERIMENTS.md](EXPERIMENTS.md) (the lab
protocol), [RESULTS.md](RESULTS.md) (all measured numbers),
[TOOL_HANDLER.md](TOOL_HANDLER.md) (the agent-facing contract),
[TIME_SAVED.md](TIME_SAVED.md) (what the savings number means — and what it does
not).

---

## Guarantees and their honest extent

**Stated:** the tool's own overhead is bounded by `max(5 ms, 2 % of the suite's
runtime)`, and one avoided restart pays for ~150–250 runs of the tool.

**Measured:** self-metered overhead was **0.9–1.4 % of R** across runs (gate:
2 %); on a hanging suite the whole accounting cost ~85 ms against a 8.6 s
steady-state run; one avoided restart (14.0 s) is ~270× the per-run overhead.

**Honest extent:**

- hh does not make tests faster. It removes _re-paid_ work and _unbounded_
  waits. A clean run that never times out earns a receipt of **0 s** — that is
  correct, not a bug.
- Tiny suites pay a fixed ~0.2–0.5 s (interpreter spawn) for the checkpointed
  path; the auto-degrade ladder keeps that at ~25 ms of tool overhead by not
  using checkpoints at all below 1 s of test mass.
- Savings receipts are conservative by construction: unbounded counterfactuals
  (a hang with no caller deadline) are _noted_, never summed.
- The guarantee is about the tool's cost versus the suite's runtime, not about
  wall-clock speedups from parallelism (that is a latency feature, reported
  separately).

---

## Measured results

All numbers are from the sandbox lab described in [RESULTS.md](RESULTS.md)
(Python 3.13, 2 cores, synthetic suites with known ground truth, plus live A/B
with a real agent). Every gate below was _executed_, not asserted.

| gate                           | result                                                                                                                                                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **E1** cost bound              | self-metered **0.9–1.4 %** of an 8.77 s suite across re-runs (gate 2 %)                                                                                                                                |
| **E2** estimator accuracy      | cold 56 % error → **4.2 % MAPE after one run**; upper coverage 100 %                                                                                                                                   |
| **E3a** restart-avoidance      | naive: 4 attempts, **14.0 s discarded**; hh: 0 discarded, 87 ms tool overhead                                                                                                                          |
| **E3b** true hangs (3 × 600 s) | naive: **gives up, 105.1 s wasted**; hh completes: 48.8 s → 24.1 s → **8.6 s steady state**, hangs censored and reported                                                                               |
| **E4** tiny suite              | degrades to record-only; tool overhead **24.9 ms**                                                                                                                                                     |
| **E5** cross-runner            | unittest + node:test in one store: composed mass 738 ms vs 729 ms truth (**1.2 %**)                                                                                                                    |
| **E6** shadow gate             | savings LCB **14.0 s** per avoided restart vs 3× overhead (0.15 s) → **ENFORCING ALLOWED**                                                                                                             |
| **E7** worker selection        | predicted vs actual wall at W=1/2/4: **26.49/26.66, 13.25/13.34, 6.63/6.82 s**; deadline rule picks the smallest W that fits; imbalance stop confirmed (1.1 % marginal gain at the last useful worker) |
| **L1** live A/B                | naive agent **41.2 s wall, 15.0 s discarded, 2 blind guesses** vs hh **26.8 s wall, 0 discarded, 0 guesses**                                                                                           |
| **conformance**                | **13/13 recorded parsers + 13/13 live checks** (Jest, Mocha, node:test, Go, JUnit Platform — each with SIGKILL checkpointing)                                                                          |
| **receipts**                   | demo job: **97.8 s credited** against 34.9 s of run wall, by kind: hang_bound 51.5 · resume 26.5 · continuation 17.9 · refusal 2.0                                                                     |

---

## Capture layer (runners)

One adapter protocol, three tiers. Tier A streams rows live (a SIGKILL keeps what
finished); Tier B parses post-hoc reports; Tier C is the generic fallback.

| runner                                                                                                                         | tier  | how                                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------ | ----- | ----------------------------------------------------------------------------------- |
| unittest                                                                                                                       | A     | `hh.adapters.unittest_stream` (streaming subprocess adapter)                        |
| pytest                                                                                                                         | A     | `-p hh.adapters.pytest_plugin` (`HH_LEDGER=…`)                                      |
| Jest                                                                                                                           | A     | `--reporters=hh/adapters/hh_jest_reporter.cjs`                                      |
| Mocha                                                                                                                          | A     | `--reporter hh/adapters/hh_mocha_reporter.cjs`                                      |
| node:test                                                                                                                      | A     | `--test-reporter=hh/adapters/node_reporter.mjs`                                     |
| Go                                                                                                                             | A + B | `python -m hh.adapters.go_stream -- hh go test -json ./…`, or parse recorded output |
| JUnit Platform (JVM)                                                                                                           | A + B | `HhJUnitListener.java` (ServiceLoader), and Surefire XML                            |
| Surefire · TestNG · PHPUnit · RSpec · GoogleTest · Catch2 · cargo-nextest · mocha-junit · pytest-xml · .NET JUnit · VSTest TRX | B     | one `junit_xml`/`trx` parser family, conformance-tested on recorded fixtures        |

Run the conformance matrix yourself:

```bash
python experiments/conformance.py             # 13 recorded fixtures (instant)
python experiments/conformance.py --live-all  # + real Jest/Mocha/node:test/Go/JUnit runs
```

---

## Time-saved receipts

Every run reports its own utility, to the agent first:

```json
"time_saved": {
  "this_call_s": 26.5, "job_id": "my-agent-session", "job_total_s": 97.8,
  "breakdown": [{"kind": "resume", "ms": 26500.0,
                 "basis": "108 test(s) already on this job's ledger were not re-executed"}],
  "how": "conservative: only work a restart-from-zero baseline provably re-pays is credited"
}
```

Four kinds, each ledger-backed: `continuation` (finished work kept across a
budget extension), `resume` (already-paid work recognised), `hang_bound` (a hang
censored, capped at the caller's own deadline), `refusal` (a deadline protected by
refusing a doomed run). Unbounded claims are noted, never summed. Spec and worked
example: [TIME_SAVED.md](TIME_SAVED.md).

---

## Repository layout

```
hh/                  the package (stdlib only)
  tool.py                the agent-facing run_tests tool + contract
  savings.py             time-saved receipts
  policy.py              budgets, shards, worker choice, degrade ladder
  estimator.py           tiered fuzzy estimation + quantiles
  executor.py            checkpointed shard execution + escalation
  store.py               per-repo SQLite store (observations/aggregates/attempts/savings)
  keys.py  meter.py      identity, self-overhead meter
  adapters/              unittest, pytest, jest, mocha, node:test, go, junit-xml, trx
tests/                   fast stdlib test suite (CI runs this on 3.10–3.13)
experiments/             the lab: E1–E7, live A/B, conformance, demos
fixtures/                deterministic synthetic suites + recorded runner outputs
.github/workflows/       ci.yml (tests, wheel, live adapters) · release.yml (tag → GitHub Release)
```

---

## Development

```bash
python3 -m unittest discover -s tests -v     # 40 tests, < 1 s, stdlib only
python experiments/conformance.py            # parser conformance, instant
python experiments/time_saved_demo.py        # receipts end to end (~35 s)
python experiments/run_experiment.py --exp e7   # worker-selection gate (~65 s)
```

The core has **no third-party dependencies** and CI asserts that: the `tests`
job installs nothing, so an accidental dependency breaks the build immediately.

---

## Releasing

Releases are tag-driven — pushing a tag is the entire procedure:

```bash
# 1. bump the version (single source of truth)
$EDITOR hh/__init__.py         # __version__ = "1.0.1"
$EDITOR CHANGELOG.md
# 2. push the tag
git commit -am "release v1.0.1" && git tag v1.0.1 && git push origin main --tags
```

`.github/workflows/release.yml` then: verifies the tag matches
`hh.__version__`, runs the test suite, builds sdist + wheel, installs the
wheel into a clean venv and smoke-tests it (`hh --version`, `hh tool-spec`,
`hh plan`), publishes a GitHub Release with the artifacts and generated notes.
Publishing to PyPI is opt-in: set repository variable `PUBLISH_TO_PYPI=true` and
secret `PYPI_API_TOKEN`.

Any agent can then pin exactly what it tested:

```bash
pip install "git+https://github.com/YOUR-ORG/hh.git@v1.0.1"
```

---

## License

MIT — see [LICENSE](LICENSE).
