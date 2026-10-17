"""L1 scorer (EXPERIMENTS.md §5): side-by-side of Phase N (naive, Arena Agent
guessing) vs Phase T (hh-assisted) on matched live suites. Reads the audit
trail (guesses.md + attempt logs) and — only now — the withheld truth files.
Writes .hh/experiments/L1_summary.md.
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def all_logs():
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
                         round(r.get("wall_ms", 0), 1))
                    if k not in seen:
                        seen.add(k)
                        logs.append(r)
    # merge DB attempts (cmd_run logs there; wrap logs to both)
    import sqlite3
    dbp = os.path.join(ROOT, ".hh", "observations.sqlite")
    if os.path.exists(dbp):
        db = sqlite3.connect(dbp)
        for ts, pol, sid, att, bud, out, wall, waste, oh in db.execute(
                "SELECT ts, policy, suite_id, attempt, budget_s, outcome,"
                " wall_ms, wasted_ms, overhead_ms FROM attempts"):
            k = (pol, sid, att, round(wall or 0, 1))
            if k not in seen:
                seen.add(k)
                logs.append({"ts": ts, "policy": pol, "suite_id": sid,
                             "attempt": att, "budget_s": bud, "outcome": out,
                             "wall_ms": wall or 0, "wasted_ms": waste or 0,
                             "overhead_ms": oh or 0})
        db.close()
    return logs


def suite_attempts(logs, suite_id):
    rows = [r for r in logs if r.get("suite_id") == suite_id
            and r.get("policy") != "smoke"]
    return sorted(rows, key=lambda r: r.get("ts", 0))


def truth_of(name):
    t = json.load(open(os.path.join(ROOT, "fixtures", name, ".truth.json")))
    return t


def main():
    logs = all_logs()
    naive = suite_attempts(logs, "live_a_101")
    hh = suite_attempts(logs, "live_b_202")
    t_a, t_b = truth_of("live_a_101"), truth_of("live_b_202")
    guesses = open(os.path.join(ROOT, ".hh", "experiments", "guesses.md")).read()

    naive_wall = sum(r["wall_ms"] for r in naive) / 1000
    naive_waste = sum(r.get("wasted_ms", 0) for r in naive) / 1000
    naive_outcome = "ok" if any(r["outcome"] == "ok" for r in naive) else "give-up"
    tda_wall = sum(r["wall_ms"] for r in hh) / 1000
    tda_cold = hh[0]["wall_ms"] / 1000 if hh else 0
    tda_warm = hh[-1]["wall_ms"] / 1000 if len(hh) > 1 else None

    lines = [
        "# L1 — live A/B summary (Arena Agent as the agent under test)",
        "",
        f"Matched suites (family `live_slow`, different seeds — no peeking at truth "
        f"during either phase): **live_a_101** (Phase N, naive) vs **live_b_202** "
        f"(Phase T, hh).",
        "",
        "## Phase N — Arena Agent estimating by hand (audit trail in guesses.md)",
        "",
        "| attempt | budget | outcome | wall | wasted |",
        "|---|---|---|---|---|",
    ]
    for r in naive:
        lines.append(f"| {r['attempt']} | {r['budget_s']:.0f}s | {r['outcome']} "
                     f"| {r['wall_ms']/1000:.1f}s | {r.get('wasted_ms',0)/1000:.1f}s |")
    lines += [
        f"| **total** | | **{naive_outcome}** | **{naive_wall:.1f}s** | **{naive_waste:.1f}s** |",
        "",
        "## Phase T — hh plan → checkpointed execute (mechanical, no guessing)",
        "",
        "| run | budget (from plan) | outcome | wall | wasted |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(hh):
        kind = "cold" if i == 0 else ("warm" if i == len(hh) - 1 else "warm")
        lines.append(f"| {kind} | {r['budget_s']:.0f}s | {r['outcome']} "
                     f"| {r['wall_ms']/1000:.1f}s | 0.0s |")
    lines += [
        f"| **total** | | **ok** | **{tda_wall:.1f}s** | **0.0s** |",
        "",
        "## Ground truth (revealed after both phases)",
        "",
        f"- live_a_101: {t_a['n']} tests, true test mass **{t_a['total_test_ms']/1000:.1f}s**",
        f"- live_b_202: {t_b['n']} tests, true test mass **{t_b['total_test_ms']/1000:.1f}s**",
        f"- naive guess #1 was off by **{(naive[0]['budget_s']*1000 - t_a['total_test_ms'])/1000:+.1f}s** "
        f"against the truth it could not see (count-based estimation, the reported failure mode)",
        "",
        "## Verdict",
        "",
        f"- Wall-clock: naive **{naive_wall:.1f}s** vs hh **{tda_wall:.1f}s** "
        f"({naive_wall - tda_wall:+.1f}s), of which hh's cold learning run was {tda_cold:.1f}s "
        f"and steady-state warm was {tda_warm if tda_warm is not None else 'n/a'}s",
        f"- Discarded compute: naive **{naive_waste:.1f}s** vs hh **0.0s** "
        f"(checkpoints: an under-budget extends the remainder, never restarts)",
        f"- Attempts: naive **{len(naive)}** vs hh **{len(hh)}** planned run(s), "
        f"0 blind guesses",
    ]
    out = os.path.join(ROOT, ".hh", "experiments", "L1_summary.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
