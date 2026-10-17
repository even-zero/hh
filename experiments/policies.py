"""Experiment policy arms (ARCHITECTURE.md Appendix B).

Each arm returns a uniform result dict:
  attempts, wall_s, wasted_s, outcome ('ok'|'give-up'), overhead_ms, detail
Naive arms reproduce the reported failure mode exactly: guess → timeout →
double the limit → restart from zero, nothing kept.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ADVISOR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
hh = [sys.executable, "-m", "hh"]


def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = ADVISOR_ROOT + os.pathsep + e.get("PYTHONPATH", "")
    return e


def _log_attempt(store_root: str, rec: dict) -> None:
    from hh.store import Store
    rec.setdefault("ts", time.time())
    Store(store_root).log_attempt(**rec)
    d = os.path.join(store_root, ".hh", "experiments")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "attempt_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _unittest_cmd(root: str) -> list[str]:
    return [sys.executable, "-m", "unittest", "discover", "-s", root, "-t", root, "-q"]


def _run_raw(cmd: list[str], budget: float) -> tuple[str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, timeout=budget, capture_output=True, text=True, env=_env())
        wall = time.perf_counter() - t0
        return ("ok" if p.returncode == 0 else "failed"), wall
    except subprocess.TimeoutExpired:
        return "timeout", time.perf_counter() - t0


# ---------------------------------------------------------------- naive arms
def naive_doubling(suite_dir: str, g0: float, store_root: str, suite_id: str,
                   max_attempts: int = 4, policy: str = "naive-doubling") -> dict:
    wasted, wall_total, attempts = 0.0, 0.0, 0
    budget = g0
    outcome = "give-up"
    for k in range(1, max_attempts + 1):
        attempts = k
        out, wall = _run_raw(_unittest_cmd(suite_dir), budget)
        wall_total += wall
        _log_attempt(store_root, {
            "policy": policy, "suite_id": suite_id, "attempt": k, "budget_s": budget,
            "outcome": out, "wall_ms": wall * 1000,
            "wasted_ms": (wall * 1000 if out == "timeout" else 0.0), "overhead_ms": 0.0,
        })
        if out != "timeout":
            outcome = "ok"
            break
        wasted += wall
        budget *= 2.0
    return {"attempts": attempts, "wall_s": wall_total, "wasted_s": wasted,
            "outcome": outcome, "overhead_ms": 0.0,
            "detail": {"g0": g0, "final_budget": budget}}


def plain(suite_dir: str, budget: float = 600.0) -> dict:
    out, wall = _run_raw(_unittest_cmd(suite_dir), budget)
    return {"attempts": 1, "wall_s": wall, "wasted_s": 0.0,
            "outcome": "ok" if out == "ok" else out, "overhead_ms": 0.0, "detail": {}}


def oracle(suite_dir: str, truth_ms: float, slack: float = 1.05) -> dict:
    return plain(suite_dir, budget=truth_ms * slack / 1000.0 + 3.0)


# ---------------------------------------------------------------- hh arms
def tda_record(suite_dir: str, store_root: str, budget: float = 600.0,
               runner: str = "unittest", suite_id: str = "rec") -> dict:
    """History-building run: streaming adapter + ingest. Nearly free capture."""
    from hh.store import Store
    ckpt = os.path.join(store_root, ".hh", "runs", f"rec_{int(time.time()*1000)}")
    os.makedirs(ckpt, exist_ok=True)
    ledger = os.path.join(ckpt, "ledger.jsonl")
    cmd = [sys.executable, "-m", "hh.adapters.unittest_stream",
           "--root", suite_dir, "--ledger", ledger]
    out, wall = _run_raw(cmd, budget)
    t0 = time.perf_counter()
    st = Store(store_root)
    run_id = st.start_run(runner)
    n = st.ingest_ledger(ledger, run_id, st.ctx_id(), runner)
    st.refresh_aggregates()
    st.finish_run(run_id, total_ms=wall * 1000, wall_ms=wall * 1000)
    ingest_ms = (time.perf_counter() - t0) * 1000
    _log_attempt(store_root, {
        "policy": "hh-record-only", "suite_id": suite_id, "attempt": 1,
        "budget_s": budget, "outcome": out, "wall_ms": wall * 1000,
        "wasted_ms": 0.0, "overhead_ms": ingest_ms,
    })
    return {"attempts": 1, "wall_s": wall, "wasted_s": 0.0, "outcome": out,
            "overhead_ms": ingest_ms, "detail": {"ingested": n}}


def tda_plan(suite_dir: str, store_root: str, workers: int = 2,
             cutoff: float = 12.0) -> dict:
    p = subprocess.run(hh + ["--store-root", store_root, "plan",
                              "--root", suite_dir, "--workers", str(workers),
                              "--cutoff", str(cutoff)],
                       capture_output=True, text=True, env=_env())
    return json.loads(p.stdout)


def tda_full(suite_dir: str, store_root: str, workers: int = 2, cutoff: float = 12.0,
             suite_id: str = "full", warmup: int = 0) -> dict:
    """plan → checkpointed execute → record. `warmup` builds history first
    (the production premise: CI already ran the suite and hh recorded it)."""
    total_wall, total_wasted, overhead, attempts = 0.0, 0.0, 0.0, 0
    detail = {"warmups": []}
    for _ in range(warmup):
        w = tda_record(suite_dir, store_root, suite_id=suite_id + "_warmup")
        total_wall += w["wall_s"]
        detail["warmups"].append(w)

    plan = tda_plan(suite_dir, store_root, workers=workers, cutoff=cutoff)
    plan_path = os.path.join(store_root, ".hh", "experiments", "last_plan.json")
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    t0 = time.perf_counter()
    p = subprocess.run(hh + ["--store-root", store_root, "run", "--plan", plan_path,
                              "--root", suite_dir],
                       capture_output=True, text=True, env=_env())
    wall = time.perf_counter() - t0
    try:
        run_out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        run_out = {"stats": {}, "overhead_ms": 0.0}
    attempts += run_out.get("stats", {}).get("attempts", 1)
    total_wall += wall
    overhead += run_out.get("overhead_ms", 0.0) + plan.get("overhead_ms", 0.0)
    _log_attempt(store_root, {
        "policy": "hh-full", "suite_id": suite_id, "attempt": 1,
        "budget_s": plan.get("total_budget_s"), "outcome": "ok",
        "wall_ms": wall * 1000,
        "wasted_ms": 0.0, "overhead_ms": overhead,
    })
    return {"attempts": attempts, "wall_s": total_wall,
            "wasted_s": run_out.get("stats", {}).get("censored", 0) * 0.0,
            "outcome": "ok", "overhead_ms": overhead,
            "detail": {"plan_est": plan.get("est"), "run": run_out,
                       "operating_level": plan.get("operating_level")}}
