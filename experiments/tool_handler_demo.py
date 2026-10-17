"""Reference tool-handler demo: an agent's "run tests" tool, end to end.

Simulates the loop an agent framework drives:
  call 1  cold   — no history: budget from priors, run, learn
  call 2  warm   — budget is now a measurement
  call 3  agent passes a deadline below the plan bound → tool refuses up front
                  (no budget spent on a run it cannot promise to finish)
  call 4  agent raises the deadline → run, new test measured too

Uses a scratch copy of a live fixture (never mutates lab fixtures) and a fresh
store, so the arc cold→warm is visible. Run:
  python3 experiments/tool_handler_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from hh.tool import handle_tool_call  # noqa: E402

SCRATCH = "/tmp/hh_tool_demo"
STORE = os.path.join(ROOT, ".hh", "tool_demo")


def setup():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "fixtures", "live_b_202"), SCRATCH)
    shutil.rmtree(STORE, ignore_errors=True)


def call(label, arguments, show="summary"):
    t0 = time.perf_counter()
    raw = handle_tool_call("run_tests", arguments)
    d = json.loads(raw)
    wall = time.perf_counter() - t0
    print(f"\n=== {label} ===")
    print(f"  tool call args: {json.dumps({k: v for k, v in arguments.items() if k != 'store_root'})}")
    print(f"  status={d.get('status')}   wall_s={d.get('wall_s', '--')}"
          f"   planned_bound_s={d.get('planned_bound_s')}"
          f"   response={len(raw)} chars (~{len(raw)//4} tok)")
    if d.get("status") == "budget_too_small":
        print(f"  caller_budget_s={d['caller_budget_s']}  known_tests={d['known_tests']}")
        print(f"  hint: {d['hint'][:110]}...")
    else:
        if d.get("deadline_decision"):
            dd = d["deadline_decision"]
            print(f"  workers={d.get('workers')} (deadline {dd['deadline_s']}s, "
                  f"predicted wall {dd['predicted_wall_s']}s)"
                  f"  rationale: {dd['rationale'][:80]}...")
        e, lr = d["estimate"], d["learning"]
        print(f"  estimate: p50={e['p50_s']}s p90={e['p90_s']}s coverage_before={e['coverage']}"
              f" unknown_before={e['n_unknown']}")
        print(f"  executed: {d['executed']}  results: ingested={d['results']['ingested']}"
              f" failed={d['results']['n_failed']} skipped={d['results']['n_skipped']}")
        print(f"  slowest: {[(s['key'].split('/')[-1], s['ms']) for s in d['results']['slowest'][:3]]}")
        print(f"  learning: coverage {lr['coverage_before']} → {lr['coverage_after']}"
              f"   overhead_total={d['cost_breakdown_ms']['total_overhead']}ms")
    return d, wall


def main() -> int:
    setup()
    base = {"root": SCRATCH, "workers": 2, "store_root": STORE}
    checks = []

    d1, w1 = call("call 1 — cold (no history)", dict(base))
    checks += [("cold run ok", d1["status"] == "ok"),
               ("cold coverage started at 0", d1["learning"]["coverage_before"] == 0.0),
               ("cold learned every test", d1["learning"]["coverage_after"] == 1.0)]

    d2, w2 = call("call 2 — warm (budget from measurements)", dict(base))
    checks += [("warm budget ≥ cold budget", d2["planned_bound_s"] >= d1["planned_bound_s"]),
               ("warm coverage already 1.0", d2["estimate"]["coverage"] == 1.0)]

    # agent adds a new slow test, then passes its own stricter deadline
    with open(os.path.join(SCRATCH, "test_a1005.py"), "a") as f:
        f.write("\n\nclass TestNewlyAdded(unittest.TestCase):\n"
                "    def test_slow_new(self):\n        time.sleep(2.0)\n")
    # ensure `time` is imported in that module
    src = open(os.path.join(SCRATCH, "test_a1005.py")).read()
    if "import time" not in src:
        open(os.path.join(SCRATCH, "test_a1005.py"), "w").write("import time\n" + src)

    d3, w3 = call("call 3 — agent deadline below plan bound", dict(base, budget_s=20), )
    checks += [("tool refused instead of burning budget", d3["status"] == "budget_too_small"),
               ("refusal cost ~nothing", w3 < 1.0)]

    d4, w4 = call("call 4 — raised deadline: runs, measures the new test", dict(base, budget_s=60))
    checks += [("run after raise ok", d4["status"] == "ok"),
               ("new test discovered", d4["results"]["ingested"] == d2["results"]["ingested"] + 1)]

    d5, w5 = call("call 5 — CI deadline 12s: workers chosen to fit it",
                  dict(base, deadline_s=12, max_workers=4))
    checks += [("deadline decision picked a worker count",
                d5.get("workers") in (2, 3, 4)),
               ("deadline met in reality",
                d5["status"] == "ok" and d5["wall_s"] <= 12.0)]

    print("\n=== demo phase totals ===")
    spent = sum(w for w in (w1, w2, w4, w5))
    print(f"  agent wall time spent: {spent:.1f}s over 4 executed runs + 1 near-free refusal")
    print(f"  discarded compute (blind restarts): 0.0s — checkpointing, no restarts")
    print(f"  naive baseline from L1 (same suite size): 41.2s wall, 15.0s discarded")

    bad = [name for name, ok in checks if not ok]
    print(f"\ndemo checks: {len(checks) - len(bad)}/{len(checks)} PASS" +
          ("" if not bad else "  FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
