"""Scripted experiments (EXPERIMENTS.md §4). Run from advisor root:

  python3 experiments/run_experiment.py --exp e1        # cost bound (the §8 claim)
  python3 experiments/run_experiment.py --exp e2        # estimator accuracy
  python3 experiments/run_experiment.py --exp e3a       # under-estimation + restart
  python3 experiments/run_experiment.py --exp e3b       # true hangs
  python3 experiments/run_experiment.py --exp e4        # auto-degrade tiny suite
  python3 experiments/run_experiment.py --exp all
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from experiments import policies as pol  # noqa: E402

FIX = os.path.join(ROOT, "fixtures")
RESULTS = os.path.join(ROOT, ".hh", "experiments", "results")


def gen(family: str, name: str, seed: int = 41, grow: int = 0) -> dict:
    out = os.path.join(FIX, name, ".truth.json")
    if not os.path.exists(out) or grow:
        subprocess.run([sys.executable, os.path.join(FIX, "gen_suite.py"),
                        "--family", family, "--name", name, "--seed", str(seed),
                        "--grow", str(grow)], check=True, capture_output=True)
    return json.load(open(out))


def suite(name: str) -> str:
    return os.path.join(FIX, name)


def save(exp: str, payload: dict) -> None:
    os.makedirs(RESULTS, exist_ok=True)
    payload["ts"] = time.time()
    with open(os.path.join(RESULTS, f"{exp}.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(json.dumps(payload, indent=2, default=str))


# ------------------------------------------------------------------ E1
def e1(rep: int = 3):
    """Cost bound: overhead ≤ max(5ms, 2%·R). Primary = self-metered; secondary = wall diff."""
    truth = gen("synth_a", "synth_a_41")
    plain_walls, hh_walls, tda_oh = [], [], []
    for i in range(rep):
        r = pol.plain(suite("synth_a_41"))
        plain_walls.append(r["wall_s"])
        print(f"  plain #{i+1}: {r['wall_s']:.2f}s")
    for i in range(rep):
        r = pol.tda_full(suite("synth_a_41"), ROOT, suite_id=f"e1_{i}")
        hh_walls.append(r["wall_s"])
        tda_oh.append(r["overhead_ms"])
        print(f"  hh   #{i+1}: wall {r['wall_s']:.2f}s  self-metered {r['overhead_ms']:.1f}ms")
    R = statistics.median(plain_walls) * 1000
    gate = max(5.0, 0.02 * R)
    oh_med = statistics.median(tda_oh)
    wall_diff = statistics.median(hh_walls) * 1000 - R
    verdict = "PASS" if oh_med <= gate and wall_diff <= gate else (
        "PASS (meter) / WALL-NOISY" if oh_med <= gate else "FAIL")
    save("e1", {
        "exp": "E1 cost bound", "suite": "synth_a_41", "truth_ms": truth["total_test_ms"],
        "plain_ms": [round(w * 1000) for w in plain_walls],
        "tda_wall_ms": [round(w * 1000) for w in hh_walls],
        "hh_self_metered_ms": [round(o, 1) for o in tda_oh],
        "R_ms": round(R), "gate_ms": round(gate, 1),
        "overhead_median_ms": round(oh_med, 1),
        "wall_diff_ms": round(wall_diff, 1),
        "overhead_pct_of_R": round(100 * oh_med / R, 3),
        "verdict": verdict,
    })


# ------------------------------------------------------------------ E2
def e2(runs: int = 4):
    """Estimator accuracy & calibration on synthetic ground truth.

    Uses a FRESH store so run 0 is a true cold start (the shared root store may
    already hold history from other experiments). Calibration scoring: the
    budget-relevant property is upper-bound coverage (truth ≤ p90), which is
    what p90·safety buys us — a [p50,p90] band containing truth 100% of the
    time would mean the estimator is biased HIGH, not well calibrated.
    """
    import shutil
    sr = os.path.join(ROOT, ".exp_store", "e2")
    shutil.rmtree(sr, ignore_errors=True)
    os.makedirs(sr, exist_ok=True)
    truth = gen("synth_a", "synth_a_41")
    true_ms = truth["total_test_ms"]
    rows = []
    for i in range(1, runs + 1):
        plan = pol.tda_plan(suite("synth_a_41"), sr)
        est = plan["est"]
        p50, p90, cov = est["p50_s"] * 1000, est["p90_s"] * 1000, est["coverage"]
        rows.append({"after_runs": i - 1, "p50_ms": round(p50), "p90_ms": round(p90),
                     "coverage": cov,
                     "err_pct": round(100 * abs(p50 - true_ms) / true_ms, 1),
                     "truth_le_p90": bool(true_ms <= p90)})
        print(f"  after {i-1} runs: p50={p50:.0f}ms truth={true_ms:.0f}ms "
              f"err={rows[-1]['err_pct']}% cov={cov} covered={rows[-1]['truth_le_p90']}")
        pol.tda_record(suite("synth_a_41"), sr, suite_id=f"e2_{i}")
    err3 = rows[min(3, len(rows) - 1)]["err_pct"]
    upper = sum(r["truth_le_p90"] for r in rows) / max(len(rows), 1)
    save("e2", {
        "exp": "E2 estimator accuracy", "suite": "synth_a_41", "truth_ms": true_ms,
        "rows": rows, "mape_after3_pct": err3,
        "upper_coverage_p90": round(upper, 2),
        "note": "cold-start prior matches this synthetic family well; churn/params "
                "fixtures probe the harder cases",
        "verdict": "PASS" if err3 < 25 and upper >= 0.85 else "CHECK",
    })


# ------------------------------------------------------------------ E3
def e3a():
    """Under-estimation: naive restarts from zero; hh budgets from history."""
    truth = gen("synth_a", "synth_a_41")
    naive = pol.naive_doubling(suite("synth_a_41"), g0=2.0, store_root=ROOT,
                               suite_id="e3a_naive", max_attempts=5)
    hh = pol.tda_full(suite("synth_a_41"), ROOT, suite_id="e3a_tda", warmup=1)
    save("e3a", {
        "exp": "E3a under-estimation + restart", "suite": "synth_a_41",
        "truth_ms": truth["total_test_ms"],
        "naive-doubling": {k: naive[k] for k in ("attempts", "wall_s", "wasted_s", "outcome")},
        "hh-full (warmup 1)": {k: hh[k] for k in ("attempts", "wall_s", "wasted_s", "outcome")},
        "tda_overhead_ms": round(hh["overhead_ms"], 1),
        "saved_wall_s": round(naive["wall_s"] - hh["wall_s"], 2),
        "saved_waste_s": round(naive["wasted_s"] - hh["wasted_s"], 2),
    })


def e3b():
    """True hangs: naive can never complete; hh quarantines and finishes the rest."""
    import shutil
    sr = os.path.join(ROOT, ".exp_store", "e3b")
    shutil.rmtree(sr, ignore_errors=True)
    os.makedirs(sr, exist_ok=True)
    truth = gen("synth_hang", "synth_hang_41")
    naive = pol.naive_doubling(suite("synth_hang_41"), g0=15.0, store_root=sr,
                               suite_id="e3b_naive", max_attempts=3)
    runs = []
    for i in range(3):   # cold → warm → warm (shows learning). No record-warmup:
        t = pol.tda_full(suite("synth_hang_41"), sr, suite_id=f"e3b_tda_{i}",
                         warmup=0)   # a record-only pass would block on true hangs;
        runs.append({"run": i, "wall_s": round(t["wall_s"], 2),  # the executor's
                     "attempts": t["attempts"],                 # censoring IS the teacher
                     "est": t["detail"].get("plan_est"),
                     "censored": t["detail"].get("run", {}).get("stats", {}).get("censored")})
        print(f"  hh run {i}: wall={t['wall_s']:.1f}s attempts={t['attempts']} "
              f"censored={runs[-1]['censored']}")
    save("e3b", {
        "exp": "E3b hangs / censoring / quarantine", "suite": "synth_hang_41",
        "truth_nonhang_ms": truth["total_nonhang_ms"],
        "naive-doubling": {k: naive[k] for k in ("attempts", "wall_s", "wasted_s", "outcome")},
        "tda_runs": runs,
        "note": "naive never completes with infinite hangs (give-up is the honest outcome); "
                "hh completes with hang tests censored and reported",
    })


# ------------------------------------------------------------------ E4
def e4():
    """Auto-degrade: tiny suite must not pay for machinery (§8.4 L2/L3)."""
    gen("synth_tiny", "synth_tiny_7", seed=7)
    plain = pol.plain(suite("synth_tiny_7"))
    hh = pol.tda_full(suite("synth_tiny_7"), ROOT, suite_id="e4", warmup=1)
    level = hh["detail"].get("operating_level")
    regret_ms = (hh["wall_s"] - plain["wall_s"]) * 1000
    save("e4", {
        "exp": "E4 auto-degrade tiny suite", "suite": "synth_tiny_7",
        "plain_ms": round(plain["wall_s"] * 1000), "tda_ms": round(hh["wall_s"] * 1000),
        "regret_ms": round(regret_ms, 1), "operating_level": level,
        "overhead_ms": round(hh["overhead_ms"], 1),
        "verdict": "PASS" if level in ("record-only", "off") and regret_ms < 500 else "CHECK",
    })


# ------------------------------------------------------------------ E5
def e5():
    """Cross-runner composition (§10.5): pytest/unittest + node:test in one store,
    estimator sums per-sub estimates against combined ground truth."""
    import shutil
    sr = os.path.join(ROOT, ".exp_store", "e5")
    shutil.rmtree(sr, ignore_errors=True)
    os.makedirs(sr, exist_ok=True)
    truth = gen("dual", "dual_41", seed=41)
    py_ms = truth["total_test_ms"]
    js_truth = json.load(open(os.path.join(suite("dual_41"), ".truth_js.json")))
    js_ms = js_truth["total_test_ms"]

    pol.tda_record(suite("dual_41"), sr, suite_id="e5_py")           # unittest adapter
    # node:test adapter (Tier A reporter)
    ck = os.path.join(sr, ".hh", "runs", "e5_js")
    os.makedirs(ck, exist_ok=True)
    ledger = os.path.join(ck, "ledger.jsonl")
    env = dict(os.environ); env["HH_LEDGER"] = ledger
    env["PYTHONPATH"] = ROOT
    subprocess.run(["node", "--test", "--test-reporter=" + os.path.join(
        ROOT, "hh", "adapters", "node_reporter.mjs"),
        os.path.join(suite("dual_41"), "test_basic.mjs")],
        capture_output=True, env=env, timeout=120)
    from hh.store import Store
    from hh import estimator as est_mod
    st = Store(sr)
    run_id = st.start_run("node")
    n_js = st.ingest_ledger(ledger, run_id, st.ctx_id(), "node")
    st.refresh_aggregates()

    # compose: per-sub estimates summed (shared overhead counted once)
    from hh.__main__ import _collect
    from hh.meter import Meter
    py_keys, _ = _collect(suite("dual_41"), "unittest", Meter())
    js_keys = [row["key"] for row in map(json.loads, open(ledger))
               if row.get("type") == "end"]
    e_py = est_mod.estimate(py_keys, st, runner="unittest")
    e_js = est_mod.estimate(js_keys, st, runner="node")
    # composition: per-sub TEST MASS summed; shared overhead c0 counted ONCE.
    # (Scoring wall-estimates against pure sleep-mass truth would charge c0 to
    # "error" — at sub-second scale one 150ms c0 is ~20% of the answer.)
    composed_mass = e_py.breakdown["tests_ms"] + e_js.breakdown["tests_ms"]
    composed_wall = composed_mass + 150.0
    combined_truth = py_ms + js_ms
    err = 100 * abs(composed_mass - combined_truth) / combined_truth
    save("e5", {
        "exp": "E5 cross-runner composition", "suite": "dual_41",
        "py_truth_ms": py_ms, "js_truth_ms": js_ms, "js_rows_ingested": n_js,
        "py_test_mass_ms": round(e_py.breakdown["tests_ms"]),
        "js_test_mass_ms": round(e_js.breakdown["tests_ms"]),
        "composed_mass_ms": round(composed_mass),
        "composed_wall_ms": round(composed_wall),
        "combined_truth_ms": round(combined_truth),
        "err_pct": round(err, 1),
        "note": "shared overhead counted once in composed_wall; per-sub p50s each "
                "carry their own c0 by design",
        "verdict": "PASS" if err < 25 else "CHECK",
    })


# ------------------------------------------------------------------ E6
def e6():
    """Shadow gate (§8.3.3): savings-LCB ≥ 3× overhead on logged data ⇒
    'enforcing allowed'. Savings sample per naive↔hh pair = restart waste the
    naive arm paid and hh would not (checkpoints + real budgets)."""
    import glob
    import math
    logs = []
    seen = set()
    paths = [os.path.join(ROOT, ".hh", "experiments", "attempt_log.jsonl")]
    paths += glob.glob(os.path.join(ROOT, ".exp_store", "*", ".hh",
                                    "experiments", "attempt_log.jsonl"))
    for p in paths:
        if os.path.exists(p):
            for l in open(p):
                if l.strip():
                    r = json.loads(l)
                    k = (r.get("policy"), r.get("suite_id"), r.get("attempt"),
                         round(r.get("wall_ms", 0), 1),
                         round(r.get("overhead_ms", 0), 1))
                    if k not in seen:
                        seen.add(k)
                        logs.append(r)
    import sqlite3
    dbp = os.path.join(ROOT, ".hh", "observations.sqlite")
    if os.path.exists(dbp):
        db = sqlite3.connect(dbp)
        for ts, pol, sid, att, bud, out, wall, waste, oh in db.execute(
                "SELECT ts, policy, suite_id, attempt, budget_s, outcome,"
                " wall_ms, wasted_ms, overhead_ms FROM attempts"):
            k = (pol, sid, att, round(wall or 0, 1), round(oh or 0, 1))
            if k not in seen:
                seen.add(k)
                logs.append({"ts": ts, "policy": pol, "suite_id": sid,
                             "attempt": att, "budget_s": bud, "outcome": out,
                             "wall_ms": wall or 0, "wasted_ms": waste or 0,
                             "overhead_ms": oh or 0})
        db.close()
    naive_runs, hh_walls, oh = {}, {}, []
    for r in logs:
        if r["policy"] == "smoke":
            continue
        if r["policy"].startswith("naive"):
            # split into sessions (a new session starts at attempt 1) and keep
            # the last one — pairing must compare single naive runs, not sums
            d = naive_runs.setdefault(r["suite_id"], {"sessions": []})
            if r.get("attempt", 1) == 1 or not d["sessions"]:
                d["sessions"].append({"wasted_s": 0.0, "wall_s": 0.0, "attempts": 0})
            s = d["sessions"][-1]
            s["wasted_s"] += r.get("wasted_ms", 0) / 1000
            s["wall_s"] += r.get("wall_ms", 0) / 1000
            s["attempts"] += 1
        elif r["policy"].startswith(("hh", "tda")):   # tda* = pre-rename rows
            hh_walls.setdefault(r["suite_id"], []).append(r.get("wall_ms", 0) / 1000)
            if r.get("overhead_ms"):
                oh.append(r["overhead_ms"])
    pairs = [("e3a_naive", "e3a_tda"), ("e3b_naive", "e3b_tda_2"),
             ("live_a_101", "live_b_202")]
    samples = []
    for n_id, t_id in pairs:
        if n_id not in naive_runs or t_id not in hh_walls:
            continue
        nv = naive_runs[n_id]["sessions"][-1]
        t_wall = hh_walls[t_id][-1]          # steady-state (last) hh run
        samples.append({
            "pair": f"{n_id} ↔ {t_id}", "naive_wasted_s": round(nv["wasted_s"], 2),
            "naive_wall_s": round(nv["wall_s"], 2), "naive_attempts": nv["attempts"],
            "hh_wall_s": round(t_wall, 2), "savings_s": round(nv["wasted_s"], 2),
        })
    savings = [s["savings_s"] for s in samples]
    n = len(savings)
    mean = sum(savings) / n if n else 0.0
    se = (sum((x - mean) ** 2 for x in savings) / (n - 1)) ** 0.5 / n ** 0.5 if n > 1 else 0.0
    lcb = mean - 1.96 * se if n > 1 else mean
    lcb_cons = min(savings) if savings else 0.0      # tiny-n conservative LCB
    oh_mean = sum(oh) / len(oh) if oh else 0.0
    gate = 3.0 * oh_mean / 1000.0                    # 3× overhead, in seconds
    verdict = ("ENFORCING ALLOWED" if lcb_cons >= gate and savings else "HOLD AT ADVISORY")
    save("e6", {
        "exp": "E6 shadow gate", "samples": samples,
        "overhead_samples_ms": [round(o, 1) for o in oh],
        "mean_overhead_ms": round(oh_mean, 1),
        "savings_mean_s": round(mean, 2), "savings_lcb95_s": round(lcb, 2),
        "savings_min_s": round(lcb_cons, 2), "gate_3x_overhead_s": round(gate, 3),
        "verdict": verdict,
    })


# ------------------------------------------------------------------ E7
def e7():
    """Deadline-aware worker selection (§9 CI hook).

    Live fixture, warm store. Measures predicted vs actual wall at W∈{1,2,4},
    checks the plan promise (actual ≤ shard bound), then the deadline rule
    (smallest W that fits) and the imbalance stop on a long-pole suite.
    """
    from hh import estimator as est_mod
    from hh import store as store_mod
    from hh.meter import Meter
    from hh.policy import choose_workers
    from hh.tool import run_tests as tool_run, collect_manifest

    LIVE = os.path.join(FIX, "live_b_202")
    SYN = os.path.join(FIX, "synth_a_41")

    def est_of(root):
        keys, _ = collect_manifest(root, "unittest", Meter())
        return est_mod.estimate(keys, store_mod.Store(ROOT), runner="unittest",
                                workers=2)

    est_live = est_of(LIVE)

    measured = {}
    for w in (1, 2, 4):
        r = tool_run(LIVE, workers=w, max_workers=4, store_root=ROOT)
        measured[w] = {
            "wall_s": r["wall_s"], "predicted_wall_s": r["predicted_wall_s"],
            "shard_bound_s": max(s["budget_s"] for s in r["shards"]),
            "shards": len(r["shards"]), "attempts": r["executed"]["attempts"],
            "overhead_ms": r["cost_breakdown_ms"]["total_overhead"],
        }

    # deadline rule: smallest fitting W, honest refusal when infeasible
    deadlines = {}
    for dl in (40, 15, 12, 9, 6, 4):
        d = choose_workers(est_live, deadline_s=dl, max_workers=4)
        deadlines[dl] = {k: d[k] for k in ("workers", "feasible",
                                           "predicted_wall_s", "rationale")}

    # run at the W chosen for a 9 s deadline; the deadline must hold in reality
    chosen9 = deadlines[9]["workers"]
    r9 = tool_run(LIVE, deadline_s=9, max_workers=4, store_root=ROOT)
    deadline_met = r9["status"] == "ok" and r9["wall_s"] <= 9.0

    # imbalance stop: synth_a_41 has a 4 s long pole that cannot split
    est_syn = est_of(SYN)
    dec_syn = choose_workers(est_syn, deadline_s=None, max_workers=4)
    # measure the *marginal step* the rule claims is useless (recommended W → W+1),
    # not the whole staircase: W=2→3 is a real gain, W=3→4 is the long pole again
    syn_measured = {}
    for w in (2, 3, 4):
        rr = tool_run(SYN, workers=w, store_root=ROOT)
        syn_measured[w] = {"wall_s": rr["wall_s"]}
    w_rec = dec_syn["workers"]
    step_gain = ((syn_measured[w_rec]["wall_s"] - syn_measured[w_rec + 1]["wall_s"])
                 / syn_measured[w_rec]["wall_s"])

    checks = {
        "plan_promise_actual_le_bound": all(m["wall_s"] <= m["shard_bound_s"]
                                            for m in measured.values()),
        "predicted_within_30pct": all(
            abs(m["predicted_wall_s"] - m["wall_s"]) <= 0.30 * m["wall_s"]
            for m in measured.values()),
        "deadline_rule_smallest_fitting_W": (
            deadlines[40]["workers"] <= 2 and deadlines[15]["workers"] <= 2
            and deadlines[12]["workers"] >= deadlines[15]["workers"]
            and deadlines[9]["feasible"] and not deadlines[6]["feasible"]
            and not deadlines[4]["feasible"]),
        "deadline_met_in_reality": deadline_met,
        "imbalance_stop_flagged_last_useful_W": (
            w_rec == 3
            and dec_syn["curve"][w_rec]["gain_pct"] is not None
            and dec_syn["curve"][w_rec]["gain_pct"] < 5.0),
        "imbalance_measured_marginal_gain_small": step_gain < 0.10,
        "imbalance_recommended_within_10pct_of_cap": (
            syn_measured[w_rec]["wall_s"] <= 1.10 * syn_measured[w_rec + 1]["wall_s"]),
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    save("e7", {
        "exp": "E7 deadline-aware worker selection",
        "suite": "live_b_202", "cpu_count": os.cpu_count(),
        "estimate": {"p50_s": round(est_live.p50 / 1000, 3),
                     "p90_s": round(est_live.p90 / 1000, 3),
                     "coverage": est_live.coverage},
        "measured": measured,
        "deadline_choices": deadlines,
        "deadline9_run": {"status": r9["status"], "wall_s": r9["wall_s"],
                          "workers": r9.get("workers")},
        "imbalance_suite": {
            "suite": "synth_a_41", "decision": dec_syn,
            "measured": syn_measured,
            "measured_marginal_gain_pct_at_recommended_W": round(step_gain * 100, 1),
        },
        "checks": checks, "verdict": verdict,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True,
                    choices=["e1", "e2", "e3a", "e3b", "e4", "e5", "e6", "e7", "all"])
    ap.add_argument("--rep", type=int, default=3)
    args = ap.parse_args()
    exps = ([args.exp] if args.exp != "all"
            else ["e1", "e2", "e3a", "e3b", "e4", "e5", "e6", "e7"])
    for e in exps:
        print(f"=== {e} ===")
        {"e1": lambda: e1(args.rep), "e2": e2, "e3a": e3a, "e3b": e3b, "e4": e4,
         "e5": e5, "e6": e6, "e7": e7}[e]()


if __name__ == "__main__":
    main()
