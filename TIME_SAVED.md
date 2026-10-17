# TIME_SAVED.md — what the "time saved" number means

The tool reports its own utility after every run and at job finish, to the agent
first (every `run_tests` response carries a `time_saved` block) and to the end
user via `hh savings`. **The number is conservative by construction** — this
page is the accounting spec, so nobody has to trust a marketing figure.

Implementation: `hh/savings.py` · spec: ARCHITECTURE §11.6 · demo:
`python3 experiments/time_saved_demo.py` (6/6 checks).

## The honesty rule

> Credit a line item only when the avoided cost is **structurally implied by
> recorded events** — "a restart-from-zero baseline would have re-paid exactly
> this, and here is the ledger row that proves it."

Everything credited is re-derivable from the run ledger + executor stats. Nothing
is extrapolated from a model, a benchmark, or a "typical" suite.

## The four kinds

| kind | credited when | amount | why it's not an over-claim |
|---|---|---|---|
| `continuation` | a shard exceeded its budget and the executor **extended the remainder** instead of restarting | Σ `duration_ms` of tests that finished before the extension (credited once per test, incrementally across extensions) | the naive failure mode is precisely "timeout ⇒ discard ⇒ restart from zero", so those tests were re-payable by construction |
| `resume` | a call re-used this job's ledger (`resume=true`) and skipped finished tests | Σ `duration_ms` of the skipped rows | the ledger rows are the receipt; they were paid once, in this job |
| `hang_bound` | test(s) were censored at the cutoff **and** the caller gave a deadline | `caller_deadline − wall` (floored at 0) | the blocking baseline can burn at most the caller's own deadline before giving up with nothing |
| `refusal` | the tool returned `budget_too_small` / `deadline_infeasible` | the caller deadline it protected | a naive attempt at a run that cannot finish in the deadline burns exactly that deadline |

## What is deliberately **not** counted

- **Unbounded waits.** A suite blocked by a 600 s hang with no caller deadline:
  the avoided wait is unbounded, so it is *noted* in the receipt and contributes
  **0**. With a caller deadline it is credited, but capped at that deadline.
- **Wall-clock compression by parallelism.** A run at 6 workers is fast, not
  "saving" seconds against itself. Speed from `W` is a latency feature; the
  receipt is about cost re-paid.
- **Better estimates that merely avoided over-waiting.** If nothing ever timed
  out, nothing was at risk, and the receipt says **0**. A clean cold run earning
  zero is a *correct* receipt, not a bug.
- **Agent-turn savings.** Fewer retries obviously save agent turns, but turns are
  not seconds of compute; they are reported by the L1 protocol (attempts, blind
  guesses), not folded in here.

## Worked example (from the demo run)

| call | receipt | kind |
|---|---|---|
| 1 cold clean run (13.4 s wall) | **0.0 s** | nothing at risk — honest zero |
| 2 resume (0.003 s wall) | 26.5 s | `resume` — 108 tests already paid for in this job |
| 3 tight 9 s shard budgets (13.0 s wall, 4 attempts, 2 censored) | 17.9 s | `continuation` — 6 slow tests kept across extensions; censored wait *noted*, not summed |
| 4 suite with 3 × 600 s hangs, caller budget 60 s (8.5 s wall, 155 tests) | 51.5 s | `hang_bound` — censored at 2 s probes; a blocking baseline burns the 60 s |
| 5 CI deadline 2 s (best effort 6.6 s) | 2.0 s | `refusal` — deadline protected |
| **job finish** | **97.8 s across 4 kinds, 3 suites** | 2.8× the 34.9 s of run wall actually spent |

## Reporting surfaces

- **Per run**: `run_tests` → `time_saved` block:
  `this_call_ms`, `job_id`, `job_store`, `job_total_s`, `breakdown[]` (each line
  carries its `basis` sentence), `notes[]` (unbounded items), `how`.
  CLI `hh run` prints the same block; `hh plan` costs nothing to report.
- **Job finish**: `hh savings [--job J] [--json]` — totals by kind, suites, and
  the latest basis sentence. Also embedded in `hh report`.
- **Raw receipts**: `<store>/.hh/experiments/savings.jsonl` (append-only, one
  line per credited item) + the `savings` table in `observations.sqlite`.

## Scope and blast radius

Receipts live in the **repo's store** (decision #1: per-repo, local-only), so a
job that spans two repos has two receipt books — `HH_JOB=<id>` groups receipts
*within* a store. That is deliberate: a global store would leak timing data
across repos, which the privacy decision forbids.
