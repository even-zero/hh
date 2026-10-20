"""Policy layer (ARCHITECTURE §9): Estimate → ExecutionPlan (budget, shards, ladder)."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from .estimator import Estimate, TestPred


@dataclass
class Shard:
    budget_s: float
    tests: list[dict]          # [{"key", "raw_id", "pred_ms"}]
    quarantine: bool = False   # single-test hard-cutoff shard (hang suspect)
    full_suite: bool = False   # cold record shard: run everything, no test list


@dataclass
class ExecutionPlan:
    total_budget_s: float
    shards: list[Shard]
    checkpoint_dir: str
    per_test_cutoff_s: float
    hang_cutoff_s: float = 2.0
    workers_hint: int | None = None
    workers_rationale: str | None = None
    escalation: dict = field(default_factory=lambda: {
        "shard_over_budget": "extend_x1.3_then_adaptive_then_quarantine",
        "test_cutoff": "censor_and_continue",
    })
    operating_level: str = "full"
    overhead_ms: float = 0.0
    # None on a cold (record) plan: hh does not predict suites it has never
    # measured. Measured plans carry an LPT point estimate (max shard load).
    predicted_wall_s: float | None = None
    # collection/startup paid once per shard attempt; budgets are *execution*
    # budgets, so the executor's clock adds this instead of charging it to tests
    startup_s: float = 0.0
    predicted_exec_wall_s: float | None = None   # same, collection excluded
    manifest_size: int = 0                # tests discovered in this plan
    measured_tests: int = 0               # tests with their own rows in the store
    resume_ingest: dict | None = None     # rows imported from a killed run before planning
    deadline_decision: dict | None = None  # choose_workers() output when a deadline was given
    est: dict = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)


def _lpt_shard(items: list[TestPred], raw_of: dict[str, str], workers: int,
               total_budget_s: float) -> tuple[list[Shard], float]:
    """LPT bin-packing (§9): longest predicted first into W bins."""
    bins: list[list[TestPred]] = [[] for _ in range(max(1, workers))]
    loads = [0.0] * max(1, workers)
    for p in sorted(items, key=lambda p: -p.median):
        i = loads.index(min(loads))
        bins[i].append(p)
        loads[i] += p.median
    scale = total_budget_s * 1000.0 / max(sum(loads), 1.0)
    shards = []
    for i, b in enumerate(bins):
        if not b:
            continue
        shards.append(Shard(
            budget_s=round(loads[i] * scale / 1000.0 * 1.15 + 2.0, 3),
            tests=[{"key": p.key, "raw_id": raw_of.get(p.key, p.key),
                    "pred_ms": p.median, "hang_tier": p.hang_tier} for p in b],
        ))
    # CV of shard loads → workers hint (decision #2)
    mean = sum(loads) / len(loads) if loads else 0.0
    cv = (sum((x - mean) ** 2 for x in loads) / len(loads)) ** 0.5 / mean if mean else 0.0
    return shards, cv


def makespan_ms(preds, w: int) -> float:
    """LPT simulation: the max bin load is the predicted wall for a W-shard run."""
    if not preds:
        return 0.0
    w = max(1, min(w, len(preds)))
    bins = [0.0] * w
    for p in sorted(preds, key=lambda p: -p.median):
        bins[bins.index(min(bins))] += p.median
    return max(bins)


def choose_workers(est, *, deadline_s: float | None = None,
                   max_workers: int | None = None,
                   min_gain: float = 0.05) -> dict:
    """Deadline-aware worker count (§9, CI hook).

    * deadline given  → smallest W whose predicted wall fits it (fewer workers
      is a better CI citizen); if even the cap cannot fit, say so honestly
      (`feasible=False`) instead of pretending.
    * no deadline     → grow W only while the marginal makespan gain stays
      ≥ min_gain. A dominant long test cannot split, so the curve flattens and
      the recommendation stops early ("parallelism unhelpful" case).

    Default cap is the CPU count (don't oversubscribe); raise `max_workers`
    explicitly for sleep-heavy suites where W > cores still halves the wall.
    """
    cap = max_workers or max(1, os.cpu_count() or 1)
    cap = max(1, min(cap, len(est.preds) or 1))
    curve, prev = [], None
    for w in range(1, cap + 1):
        m = makespan_ms(est.preds, w)
        gain = None if not prev else round((prev - m) / prev * 100, 1)
        curve.append({"w": w, "predicted_wall_s": round(m / 1000, 3), "gain_pct": gain})
        prev = m

    if deadline_s is not None:
        fitting = [c for c in curve if c["predicted_wall_s"] <= deadline_s]
        if fitting:
            chosen, feasible = fitting[0], True
            rationale = (f"deadline {deadline_s}s: smallest fitting W is {chosen['w']}"
                         f" (predicted {chosen['predicted_wall_s']}s; W=1 needs "
                         f"{curve[0]['predicted_wall_s']}s) — fewer workers, less CI contention")
        else:
            chosen, feasible = curve[-1], False
            rationale = (f"deadline {deadline_s}s INFEASIBLE: even W={chosen['w']} "
                         f"predicts {chosen['predicted_wall_s']}s. Raise deadline, lower "
                         f"per_test_cutoff, or split the suite")
    else:
        chosen = curve[0]
        for c in curve[1:]:
            if c["gain_pct"] is not None and c["gain_pct"] >= min_gain * 100:
                chosen = c
            else:
                break
        nxt = curve[chosen["w"]] if chosen["w"] < len(curve) else None
        rationale = (f"imbalance stop at W={chosen['w']}"
                     + (f": W={nxt['w']} would add only {nxt['gain_pct']}% (< "
                        f"{min_gain * 100:.0f}%) — a dominant test cannot split"
                        if nxt is not None else ": all marginal gains above threshold"))
        feasible = True
    return {"workers": chosen["w"], "feasible": feasible, "deadline_s": deadline_s,
            "predicted_wall_s": chosen["predicted_wall_s"], "curve": curve,
            "max_workers": cap, "rationale": rationale}


def make_plan(est: Estimate, raw_of: dict[str, str], *, workers: int = 2,
              checkpoint_dir: str, per_test_cutoff_s: float = 12.0,
              tiny_threshold_ms: float = 1000.0,
              max_budget_s: float = 7200.0,
              deadline_s: float | None = None,
              max_workers: int | None = None,
              startup_s: float = 0.0) -> ExecutionPlan:
    """Build an ExecutionPlan. Operating levels per §8.4 auto-degrade ladder.

    Only call this with a *measured* estimate: cold suites go through
    make_record_plan() instead. With `deadline_s` set, the number of workers is
    chosen by choose_workers() and shards are built for that parallelism
    (CI-deadline hook, §9).

    `startup_s` is the measured collection/import cost of one runner process;
    it is added to each shard attempt's clock but never to the tests' budget.
    """
    decision = None
    if deadline_s is not None:
        decision = choose_workers(est, deadline_s=deadline_s,
                                  max_workers=max_workers)
        workers = decision["workers"]
    total_s = est.recommended_timeout
    # Hard clamps: the budget is a *reservation* — tails beyond it are handled by
    # the escalation ladder and checkpoints, never by absurd reservations
    # (E3b finding: sigma blow-up once reserved 7 days for a 90s suite).
    mass_s = (est.breakdown.get("tests_ms", est.point) + est.runner_overhead_ms) / 1000.0
    total_s = min(total_s, max_budget_s, mass_s * 6.0 + 30.0)
    # tiny suite ⇒ do not spend on sharding/checkpoints (L2/L3)
    if est.point < tiny_threshold_ms:
        return ExecutionPlan(
            total_budget_s=max(3.0, total_s * 2),
            shards=[Shard(budget_s=max(3.0, total_s * 2),
                          tests=[{"key": p.key, "raw_id": raw_of.get(p.key, p.key),
                                  "pred_ms": p.median} for p in est.preds])],
            checkpoint_dir=checkpoint_dir, per_test_cutoff_s=per_test_cutoff_s,
            operating_level="tiny",
            workers_hint=1, workers_rationale="tiny suite: serial, no sharding",
            predicted_wall_s=round(sum(p.median for p in est.preds) / 1000
                                   + est.runner_overhead_ms / 1000 + startup_s, 3),
            startup_s=round(startup_s, 3),
            predicted_exec_wall_s=round(sum(p.median for p in est.preds) / 1000
                                        + est.runner_overhead_ms / 1000, 3),
            deadline_decision=decision,
            est=_est_summary(est),
        )

    hang_cutoff = 2.0
    hangs = [p for p in est.preds if p.hang_tier]
    normal = [p for p in est.preds if not p.hang_tier]

    shards, cv = _lpt_shard(normal, raw_of, workers, max(total_s - 2.0, 1.0))
    for h in hangs:  # isolation shards first (slowest-first ordering, §9)
        # tier 2 = repeated censoring ⇒ fail-fast probe; tier 1 = one censored
        # bound ⇒ probe once at the normal per-test cutoff (not 1.5x its bound —
        # E3b finding: inflating off a censored bound burns 3x cutoffs serially)
        q_budget = hang_cutoff if h.hang_tier >= 2 else per_test_cutoff_s
        shards.insert(0, Shard(
            budget_s=q_budget, quarantine=True,
            tests=[{"key": h.key, "raw_id": raw_of.get(h.key, h.key),
                    "pred_ms": h.median, "hang_tier": h.hang_tier}],
        ))

    hint = workers
    rationale = f"CV_shard {cv:.2f} → {workers} workers"
    if cv > 0.35 and workers > 1:
        hint = 2 if workers > 2 else workers
        rationale = f"CV_shard {cv:.2f} high → cap at {hint} workers (decision #2)"

    if decision is not None:                 # deadline given: the decision owns W
        hint, rationale = decision["workers"], decision["rationale"]
    norm_loads = [sum(t["pred_ms"] for t in s.tests) for s in shards
                  if not s.quarantine]
    q_loads = [s.budget_s * 1000.0 for s in shards if s.quarantine]
    exec_wall = max(norm_loads + q_loads + [0.0]) / 1000 + est.runner_overhead_ms / 1000
    plan = ExecutionPlan(
        total_budget_s=round(total_s, 3), shards=shards,
        checkpoint_dir=checkpoint_dir, per_test_cutoff_s=per_test_cutoff_s,
        hang_cutoff_s=hang_cutoff, workers_hint=hint, workers_rationale=rationale,
        predicted_wall_s=round(exec_wall + startup_s, 3),
        startup_s=round(startup_s, 3),
        predicted_exec_wall_s=round(exec_wall, 3),
        deadline_decision=decision,
        est=_est_summary(est),
    )
    return plan


def make_record_plan(*, checkpoint_dir: str, per_test_cutoff_s: float = 12.0,
                     startup_s: float = 0.0, budget_s: float = 30.0) -> ExecutionPlan:
    """Cold store: do not predict, do not even enumerate. Just record.

    The shard carries ``full_suite`` instead of a test list, so the runner
    discovers and executes in the *same* process — the naive invocation plus a
    ledger. Budget starts at the first rung of the default ladder (30 s) and
    doubles on timeout, exactly like the naive agent's policy; then it stops.
    Nothing here is derived from priors; its purpose is to leave measurements
    behind for the next plan.
    """
    return ExecutionPlan(
        total_budget_s=round(budget_s, 3),
        shards=[Shard(budget_s=round(budget_s, 3), tests=[], full_suite=True)],
        checkpoint_dir=checkpoint_dir, per_test_cutoff_s=per_test_cutoff_s,
        operating_level="record",
        workers_hint=1,
        workers_rationale=("cold store: no measurements yet — recording the suite "
                           "like a naive run, then planning from the rows"),
        escalation={"shard_over_budget": "naive_double_then_stop",
                    "test_cutoff": "censor_and_continue"},
        startup_s=round(startup_s, 3),
        est={"basis": "cold", "coverage": 0.0,
             "calibration": {"basis": "cold"}},
    )


def _est_summary(est: Estimate) -> dict:
    return {
        "p50_s": round(est.p50 / 1000, 3), "p90_s": round(est.p90 / 1000, 3),
        "runner_overhead_ms": est.runner_overhead_ms,
        "p99_s": round(est.p99 / 1000, 3), "coverage": round(est.coverage, 3),
        "calibration": est.calibration,
        "measured_tests": len(est.preds) - len(est.unknowns),
        "n_unknown": len(est.unknowns), "breakdown": est.breakdown,
    }


def latest_ledger_dir(root: str) -> str | None:
    """Newest checkpoint dir that already has a ledger (resume target)."""
    import glob
    cands = glob.glob(os.path.join(root, ".hh", "runs", "*", "ledger.jsonl"))
    if not cands:
        return None
    return os.path.dirname(max(cands, key=os.path.getmtime))


def new_checkpoint_dir(root: str) -> str:
    """Fresh checkpoint dir per call; never share a ledger by accident.

    Timestamps have 1 s resolution, and two runs of a small suite can easily
    land in the same second — the second run then "resumed" the first one's
    ledger and executed nothing.
    """
    base = os.path.join(root, ".hh", "runs", time.strftime("%Y%m%dT%H%M%S"))
    d, i = base, 1
    while os.path.exists(d):
        i += 1
        d = f"{base}-{i}"
    os.makedirs(d, exist_ok=True)
    return d
