"""Controlled probe: what a *measured* budget is worth when the naive rung is wrong.

Every suite in `repos.fresh.json` finishes inside the naive ladder's first rung
(30 s), so the `clean` scenario cannot show value in a correct budget: naive
pays the suite once and so does hh. Real CI suites are usually longer than the
first rung, and there the naive agent pays the rung it guessed wrong *before* it
pays the suite — the restart it cannot avoid because it has no estimate.

This probe makes that cost measurable on a synthetic suite whose duration is
known, using the same machinery as the benchmark (`harness.kill_run`, `hh plan`,
`hh run`):

  naive    the default ladder 30 -> 60 -> 120 s; kill on timeout, restart
  hh cold  the same ladder: a record plan *is* the naive run (parity by design)
  hh warm  one run with the budget measured by the cold pass

Run:  python3 -m benchmark.budget_probe
Writes `_work/results/budget-probe.json`; prints one table.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from . import config, harness

REPO_ID = "budget-probe"
N_TESTS = 18
SLEEP_S = 2.0          # 36 s of test time: > the naive first rung (30 s)
LADDER = [30, 60, 120]


def make_repo() -> tuple[dict, str]:
    config.work_env()
    d = config.repo_dir({"id": REPO_ID})
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "tests"))
    body = ["import time", "import unittest", "", "",
            "class Slow(unittest.TestCase):"]
    for i in range(N_TESTS):
        body += ["    def test_slow_%02d(self):" % i,
                 f"        time.sleep({SLEEP_S})"]
    with open(os.path.join(d, "tests", "test_slow.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    repo = {"id": REPO_ID, "runner": "unittest", "lane": "unittest_exec",
            "pythonpath": ["."], "display": "synthetic 36 s suite",
            "clone": {"url": "-", "ref": None}, "scenarios": {"clean": True},
            "suite_cmd": ["python3", "-m", "unittest", "discover", "-s", "tests",
                          "-p", "test_*.py"]}
    return repo, d


def run_naive(repo: dict, d: str, env: dict, store: str) -> dict:
    cmd = repo["suite_cmd"]
    runs, total = [], 0.0
    for i, budget in enumerate(LADDER, 1):
        rec = harness.kill_run(cmd, budget, cwd=d, env=env, repo=REPO_ID,
                               scenario="budget", arm="naive", attempt=i,
                               policy="naive-default", store_root=store,
                               log_name=f"budget_naive_a{i}.log")
        runs.append({"budget_s": budget, "outcome": rec["outcome"],
                     "wall_s": round(rec["wall_ms"] / 1000, 3)})
        total += rec["wall_ms"] / 1000
        if rec["outcome"] != "timeout":
            break
    return {"runs": runs, "total_s": round(total, 3),
            "wasted_s": round(sum(r["wall_s"] for r in runs if r["outcome"] == "timeout"), 3)}


def plan(d: str, env: dict, store: str, plan_path: str, kind: str) -> None:
    # --workers 1: this probe is about the *budget*, not about sharding. Two
    # shards would halve the wall clock and muddle the comparison.
    harness.run_and_save([sys.executable, "-m", "hh", "--store-root", store,
                          "plan", "--root", d, "--workers", "1",
                          "--out", plan_path],
                         cwd=d, env=env, repo=REPO_ID, scenario="budget",
                         arm="hh", attempt=1, kind=kind, budget_s=300)


def run_hh(d: str, env: dict, store: str, phase: str, plan_path: str) -> dict:
    HH = [sys.executable, "-m", "hh"]
    parsed, wall, _ = harness.run_and_save(
        HH + ["--store-root", store, "run", "--plan", plan_path, "--root", d,
              "--suite-id", f"budget-{phase}"],
        cwd=d, env=env, repo=REPO_ID, scenario="budget", arm="hh", attempt=1,
        kind=f"run-{phase}", budget_s=600, log_name=f"budget_{phase}.log")
    stats = (parsed or {}).get("stats") or {}
    return {"wall_s": round(wall, 3),
            "attempts": stats.get("attempts"),
            "complete": (parsed or {}).get("complete"),
            "ingested": (parsed or {}).get("ingested"),
            "predicted_wall_s": None if not parsed else parsed.get("predicted_exec_wall_s")}


def main() -> int:
    repo, d = make_repo()
    env = config.env_for(repo, repo_dir_override=d)
    store_n = config.store(REPO_ID, "naive")
    store_h = config.store(REPO_ID, "hh")
    for s in (store_n, store_h):
        shutil.rmtree(s, ignore_errors=True)
    ld = config.logdir(REPO_ID)

    naive = run_naive(repo, d, env, store_n)
    plan_cold = os.path.join(ld, "budget_plan_cold.json")
    plan(d, env, store_h, plan_cold, "plan-cold")
    cold = run_hh(d, env, store_h, "cold", plan_cold)
    # re-plan from the rows the record pass left: this is the measured budget
    plan_warm = os.path.join(ld, "budget_plan_warm.json")
    plan(d, env, store_h, plan_warm, "plan-warm")
    plan_doc = json.load(open(plan_warm, encoding="utf-8"))
    warm = run_hh(d, env, store_h, "warm", plan_warm)
    # a second measured pass: each run needs its own checkpoint dir, exactly as
    # a fresh `hh plan` per call gives it in production
    plan_warm2 = os.path.join(ld, "budget_plan_warm2.json")
    plan(d, env, store_h, plan_warm2, "plan-warm2")
    warm2 = run_hh(d, env, store_h, "warm", plan_warm2)

    out = {
        "suite": {"tests": N_TESTS, "declared_s": round(N_TESTS * SLEEP_S, 1)},
        "naive": naive, "hh_cold": cold, "hh_warm": warm, "hh_warm_2": warm2,
        "warm_plan": {"level": plan_doc.get("operating_level"),
                      "budget_s": plan_doc.get("total_budget_s"),
                      "predicted_wall_s": plan_doc.get("predicted_wall_s"),
                      "measured_tests": plan_doc.get("measured_tests")},
    }
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    with open(os.path.join(config.RESULTS_DIR, "budget-probe.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    print(f"synthetic suite: {N_TESTS} tests x {SLEEP_S}s = {N_TESTS * SLEEP_S:.0f}s, "
          f"W=1 (naive first rung is {LADDER[0]}s)\n")
    print("| arm | ladder / budget | wall | vs naive |")
    print("|---|---|---:|---:|")
    nv = naive["total_s"]
    def pct(x):
        return f"{(x / nv - 1) * 100:+.1f}%"
    print(f"| naive | 30 s rung times out, then 60 s "
          f"| {nv:.1f}s | — |")
    print(f"| hh cold (record plan, same ladder) | 30 s rung times out, retry "
          f"| {cold['wall_s']:.1f}s | {pct(cold['wall_s'])} |")
    print(f"| hh warm | measured budget {plan_doc.get('total_budget_s')}s, "
          f"{warm['attempts']} attempt | {warm['wall_s']:.1f}s | {pct(warm['wall_s'])} |")
    print(f"| hh warm, second run | same | {warm2['wall_s']:.1f}s "
          f"| {pct(warm2['wall_s'])} |")
    print(f"\nwrote {os.path.join(config.RESULTS_DIR, 'budget-probe.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
