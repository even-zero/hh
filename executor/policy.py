"""Policy: an Estimate becomes an ExecutionPlan with exactly one budget.

There is one decision in the product — *what timeout do we hand the process
that runs this suite?* — so the plan is correspondingly small: one shard, one
list of tests, one number. Nothing here shards, parallelises, quarantines or
escalates; a run that overruns is resumed from its ledger (executor), which is
the only safety net the timeout needs.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from .estimator import Estimate


@dataclass
class Shard:
    budget_s: float
    tests: list[dict] = field(default_factory=list)   # [{"key","raw_id"}]
    full_suite: bool = False                          # record pass: no test list


@dataclass
class ExecutionPlan:
    total_budget_s: float
    shards: list[Shard]
    checkpoint_dir: str
    operating_level: str = "measured"      # "measured" | "record"
    predicted_wall_s: float | None = None  # None on a record plan: no prediction
    startup_s: float = 0.0                 # measured runner import+collection (s)
    manifest_size: int = 0
    measured_tests: int = 0
    safety: float = 1.25
    overhead_ms: float = 0.0
    est: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


RECORD_BUDGET_S = 30.0        # first rung of the ladder a naive caller uses


def record_plan(*, checkpoint_dir: str, budget_s: float = RECORD_BUDGET_S,
                startup_s: float = 0.0) -> ExecutionPlan:
    """No measurements yet: run the suite like the naive command, and record it.

    The shard carries ``full_suite`` instead of a test list, so the runner
    discovers and executes in the *same* process. The only addition to the
    naive invocation is the ledger. This plan makes no prediction — there is
    nothing to predict from — and says so (``predicted_wall_s = None``).
    """
    return ExecutionPlan(
        total_budget_s=round(budget_s, 3),
        shards=[Shard(budget_s=round(budget_s, 3), full_suite=True)],
        checkpoint_dir=checkpoint_dir,
        operating_level="record",
        predicted_wall_s=None,
        startup_s=round(startup_s, 3),
        est={"basis": "cold", "coverage": 0.0},
    )


def make_plan(est: Estimate, raw_of: dict[str, str], *, checkpoint_dir: str,
              startup_s: float = 0.0, safety: float = 1.25,
              min_budget_s: float = 3.0, max_budget_s: float = 7200.0) -> ExecutionPlan:
    """One shard, one budget: the p90 of the measured suite plus headroom.

    Floors and ceilings are there so a nearly-empty history cannot produce a
    useless budget (a 40 ms suite still gets seconds, not milliseconds) and a
    pathological tail cannot reserve a week.
    """
    mass_s = max(est.p50, 1.0) / 1000.0
    budget = max(est.budget_s, min_budget_s)
    budget = min(budget, max_budget_s, mass_s * 4.0 + 30.0)
    tests = [{"key": p.key, "raw_id": raw_of.get(p.key, p.key)} for p in est.preds]
    return ExecutionPlan(
        total_budget_s=round(budget, 3),
        shards=[Shard(budget_s=round(budget, 3), tests=tests)],
        checkpoint_dir=checkpoint_dir,
        operating_level="measured",
        predicted_wall_s=round(est.p50 / 1000.0 + startup_s, 3),
        startup_s=round(startup_s, 3),
        manifest_size=len(tests),
        safety=safety,
        est={"p50_s": round(est.p50 / 1000, 3), "p90_s": round(est.p90 / 1000, 3),
             "p99_s": round(est.p99 / 1000, 3), "coverage": round(est.coverage, 3),
             "budget_s": round(budget, 3), **est.calibration},
    )


def new_checkpoint_dir(store_root: str) -> str:
    """A fresh checkpoint dir per call (a shared ledger is a resume bug)."""
    base = os.path.join(store_root, ".hh", "runs", time.strftime("%Y%m%dT%H%M%S"))
    d, i = base, 1
    while os.path.exists(d):
        i += 1
        d = f"{base}-{i}"
    os.makedirs(d, exist_ok=True)
    return d


def latest_ledger_dir(store_root: str) -> str | None:
    """Newest run dir that already has a ledger (resume target)."""
    import glob
    cands = glob.glob(os.path.join(store_root, ".hh", "runs", "*", "ledger.jsonl"))
    return os.path.dirname(max(cands, key=os.path.getmtime)) if cands else None
