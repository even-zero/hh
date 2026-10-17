"""Time-saved instrument demo: receipts after each run + a job-finish total.

Five scenarios on scratch stores (nothing in the lab's own store is touched):
  1 cold clean run            → honest zero receipt (nothing was at risk)
  2 resume (same job)         → credits work already paid for in this job
  3 tight CI shard budgets    → credits work preserved across a shard extension
  4 suite with real hangs     → credits the caller deadline a blocking run burns
  5 infeasible deadline       → credits the deadline the refusal protected

Run:  python3 experiments/time_saved_demo.py
Receipts also land in the store, so `hh savings --job savings_demo` reproduces
the same totals at job finish (and after the process is gone).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ["HH_JOB"] = "savings_demo"
from hh import savings                              # noqa: E402
from hh.tool import build_plan, execute_plan, run_tests  # noqa: E402

SCRATCH = "/tmp/hh_savings_demo"
STORE = os.path.join(SCRATCH, "store")   # one store = one repo = one receipt book
LIVE = os.path.join(SCRATCH, "live")


def setup():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "fixtures", "live_b_202"), LIVE)
    # One store for the whole demo (receipts are per-repo by design, decision #1).
    # Seeded from the E3b experiment store so the hang suite already has tier-2
    # history (2 s probes) and the demo stays quick — the real store is untouched.
    shutil.copytree(os.path.join(ROOT, ".exp_store", "e3b"), STORE)


def receipt(label: str, block: dict, extra: str = "") -> None:
    b = block["breakdown"]
    print(f"\n=== {label} ===")
    if b:
        for ln in b:
            print(f"  + {ln['ms'] / 1000:6.1f} s  {ln['kind']:12} {ln['basis'][:88]}")
    else:
        print("  +    0.0 s  (no savings to claim — nothing at risk in this call)")
    for n in block.get("notes", []):
        print(f"  !  note: {n}")
    print(f"  this call: {block['this_call_s']:6.1f} s saved"
          f"   |  job total: {block['job_total_s']:.1f} s"
          f"   |  wall {block.get('this_call_wall_s', '--')} s {extra}")


def main() -> int:
    setup()
    walls = {}
    t0 = time.perf_counter()

    # 1 — cold clean run: correct performance, nothing to credit
    r1 = run_tests(LIVE, workers=2, store_root=STORE)
    walls["cold"] = r1["wall_s"]
    receipt("call 1 — cold clean run", r1["time_saved"], f"status={r1['status']}")

    # 2 — same job, resume: the suite already ran, so nothing is re-paid
    r2 = run_tests(LIVE, workers=2, store_root=STORE, resume=True)
    walls["resume"] = r2["wall_s"]
    receipt("call 2 — resume (agent re-checks; suite already paid for)",
            r2["time_saved"], f"status={r2['status']} resumed={r2['resumed_tests']}")

    # 3 — tight CI shard budgets force a mid-shard extension on purpose
    plan = json.loads(build_plan(LIVE, workers=2, store_root=STORE).to_json())
    for s in plan["shards"]:
        s["budget_s"] = 9.0
    res3 = execute_plan(plan, LIVE, store_root=STORE, suite_id="live_tight_budget",
                        log=lambda *a: None)
    walls["extension"] = res3["wall_s"]
    receipt("call 3 — shard budgets too tight (9 s): extensions keep finished work",
            savings.block(STORE, None, res3["savings"]["lines"],
                          res3["savings"]["notes"], res3["wall_s"]),
            f"attempts={res3['stats']['attempts']:.0f} censored={res3['stats']['censored']:.0f}")

    # 4 — a suite with three real 600 s hangs; caller supplies a plausible budget
    plan4 = build_plan(os.path.join(ROOT, "fixtures", "synth_hang_41"),
                       workers=2, store_root=STORE)
    limit = max(15.0, math.ceil(plan4.total_budget_s))
    r4 = run_tests(os.path.join(ROOT, "fixtures", "synth_hang_41"), workers=2,
                   budget_s=limit, store_root=STORE)
    walls["hangs"] = r4["wall_s"]
    receipt(f"call 4 — 3 × 600 s hangs, caller budget {limit:.0f}s", r4["time_saved"],
            f"status={r4['status']} ingested={r4['results']['ingested']}")

    # 5 — deadline the plan cannot meet: refuse, protect the deadline
    r5 = run_tests(LIVE, deadline_s=2, max_workers=4, store_root=STORE)
    walls["refusal"] = 0.0
    receipt("call 5 — CI deadline 2 s (predicted best effort 6.6 s)",
            r5["time_saved"], f"status={r5['status']}")

    # ---- job finish: totals, from the store (what the agent would ask for) ----
    out = subprocess.run(
        [sys.executable, "-m", "hh", "--store-root", STORE, "savings",
         "--job", "savings_demo"], cwd=ROOT, capture_output=True, text=True)
    print("\n" + out.stdout.strip())

    spent = sum(walls.values())
    tot = savings.totals(STORE, "savings_demo")
    print(f"\njob finish: {tot['total_s']} s saved vs {spent:.1f} s of run wall spent"
          f" → ratio {tot['total_s'] / max(spent, 0.01):.1f}×"
          f" (savings are work *not re-paid*, not wall-clock compression)")

    summary = {"job": "savings_demo", "wall_spent_s": round(spent, 2),
               "walls": walls, "totals": tot,
               "receipts": {"cold": r1["time_saved"], "resume": r2["time_saved"],
                            "extension": res3["savings"],
                            "hangs": r4["time_saved"], "refusal": r5["time_saved"]}}
    res_dir = os.path.join(ROOT, ".hh", "experiments", "results")
    os.makedirs(res_dir, exist_ok=True)
    with open(os.path.join(res_dir, "savings_demo.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"demo wall: {time.perf_counter() - t0:.0f}s total")

    # checks
    checks = [
        ("cold run earned an honest zero", not r1["time_saved"]["breakdown"]),
        ("resume credited already-paid work",
         any(l["kind"] == "resume" for l in r2["time_saved"]["breakdown"])
         and r2["status"] == "up_to_date"),
        ("extension credited preserved work",
         any(l["kind"] == "continuation" for l in res3["savings"]["lines"])),
        ("hangs credited up to the caller deadline",
         any(l["kind"] == "hang_bound" for l in r4["time_saved"]["breakdown"])
         and r4["time_saved"]["this_call_ms"] <= limit * 1000),
        ("refusal credited the protected deadline",
         r5["status"] == "deadline_infeasible"
         and any(l["kind"] == "refusal" for l in r5["time_saved"]["breakdown"])),
        ("job totals reproduce from the store", tot["total_s"] > 0
         and abs(tot["total_ms"] - sum(l["ms"] for r in
                 (r1, r2, r4, r5) for l in r["time_saved"]["breakdown"])
                 - sum(l["ms"] for l in res3["savings"]["lines"])) < 1.0),
    ]
    bad = [n for n, ok in checks if not ok]
    print(f"\ndemo checks: {len(checks) - len(bad)}/{len(checks)} PASS" +
          ("" if not bad else "  FAILED: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
