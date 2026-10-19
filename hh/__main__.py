"""hh CLI — the agent's entire vocabulary (ARCHITECTURE §11.3):
  hh plan | record | report | wrap | run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from . import estimator, policy, store as store_mod
from .keys import normalize, suite_signature
from .meter import Meter


def _collect(root: str, runner: str, meter: Meter):
    from .tool import collect_manifest
    return collect_manifest(root, runner, meter)


def cmd_plan(args):
    from .tool import build_plan
    plan = build_plan(args.root, runner=args.runner, workers=args.workers,
                      cutoff_s=args.cutoff, store_root=args.store_root,
                      deadline_s=args.deadline, max_workers=args.max_workers,
                      resume=args.resume)
    out = plan.to_json()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    print(out)
    return 0


def cmd_record(args):
    st = store_mod.Store(args.store_root)
    run_id = st.start_run(args.runner, suite_sig=args.suite_sig)
    ctx = st.ctx_id(workers=args.workers)
    n = st.ingest_ledger(args.ledger, run_id, ctx, args.runner)
    st.refresh_aggregates()
    st.finish_run(run_id, total_ms=args.total_ms or 0.0, wall_ms=args.wall_ms or 0.0)
    print(json.dumps({"run_id": run_id, "ingested": n}))
    return 0


def cmd_tool_spec(args):
    """Emit the tool schema an agent framework registers (JSON)."""
    from .tool import TOOL_SPEC
    print(json.dumps(TOOL_SPEC, indent=2))
    return 0


def cmd_savings(args):
    from . import savings
    t = savings.totals(args.store_root, args.job)
    if args.json:
        print(json.dumps(t, indent=2))
    else:
        print(savings.human(t))
        if t.get("latest_basis"):
            print(f"  latest: {t['latest_basis']}")
    return 0


def cmd_report(args):
    from . import savings as savings_mod
    st = store_mod.Store(args.store_root)
    agg = st.agg_map("test")
    files = st.agg_map("file")
    n_cens = sum(1 for a in agg.values() if a["n_censored"])
    runs = st.db.execute(
        "SELECT id, runner, total_ms, wall_ms, started_at FROM runs"
        " ORDER BY started_at DESC LIMIT 10").fetchall()
    attempts = st.db.execute(
        "SELECT policy, suite_id, attempt, budget_s, outcome, wall_ms, wasted_ms"
        " FROM attempts ORDER BY ts DESC LIMIT 15").fetchall()
    print(json.dumps({
        "store": st.path,
        "time_saved": savings_mod.totals(args.store_root, None),
        "tests_tracked": len(agg), "files_tracked": len(files),
        "tests_with_censoring": n_cens,
        "hang_keys": st.censored_keys(2),
        "recent_runs": [dict(zip(["id", "runner", "total_ms", "wall_ms", "ts"], r)) for r in runs],
        "recent_attempts": [dict(zip(
            ["policy", "suite", "attempt", "budget_s", "outcome", "wall_ms", "wasted_ms"], r))
            for r in attempts],
    }, indent=2, default=str))
    return 0


def cmd_wrap(args):
    """Attempt logger for any command (naive arms included)."""
    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    if not args.cmd:
        print("wrap: no command given"); return 2
    t0 = time.perf_counter()
    timed_out = False
    try:
        p = subprocess.run(args.cmd, timeout=args.budget if args.budget > 0 else None,
                           capture_output=True, text=True)
        rc, wall = p.returncode, time.perf_counter() - t0
        tail = (p.stdout + p.stderr)[-1500:]
        outcome = "ok" if rc == 0 else "failed"
    except subprocess.TimeoutExpired:
        rc, wall = -9, time.perf_counter() - t0
        tail, outcome, timed_out = "TIMEOUT", "timeout", True
    rec = {
        "ts": time.time(), "policy": args.policy, "suite_id": args.suite_id,
        "attempt": args.attempt, "budget_s": args.budget,
        "outcome": outcome, "wall_ms": round(wall * 1000, 1),
        "wasted_ms": round(wall * 1000, 1) if timed_out else 0.0,
        "overhead_ms": 0.0,
    }
    store_mod.Store(args.store_root).log_attempt(**rec)
    os.makedirs(os.path.join(args.store_root, ".hh", "experiments"), exist_ok=True)
    with open(os.path.join(args.store_root, ".hh", "experiments", "attempt_log.jsonl"),
              "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
    if not args.quiet:
        print(tail)
    return 0 if outcome == "ok" else 1


def cmd_run(args):
    """Execute a plan via the checkpointed executor, then record."""
    from .tool import execute_plan
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    from . import savings
    res = execute_plan(plan, args.root, runner=args.runner, adapter=args.adapter,
                       store_root=args.store_root, suite_id=args.suite_id,
                       log=lambda *a: print(*a, file=sys.stderr))
    print(json.dumps({
        "stats": res["stats"], "ingested": res["ingested"],
        "wall_s": res["wall_s"], "exec_wall_s": res.get("exec_wall_s"),
        "startup_s": res.get("startup_s"), "phases": res.get("phases"),
        "overhead_ms": res["overhead_ms"],
        "predicted_exec_wall_s": (res.get("plan_used") or {}).get("predicted_exec_wall_s"),
        "time_saved": savings.block(args.store_root, None,
                                    res["savings"]["lines"],
                                    res["savings"]["notes"], res["wall_s"],
                                    overhead_s=res["overhead_ms"] / 1000.0),
    }))
    if res["stats"].get("shard_failures"):
        # never exit 0 when an adapter died before running its tests
        for e in res["stats"].get("errors", []):
            print(f"hh: shard {e['shard']} failed: {e['log'][-1] if e['log'] else ''}",
                  file=sys.stderr)
        return 3
    return 0


def main(argv=None):
    from . import __version__
    ap = argparse.ArgumentParser(prog="hh")
    ap.add_argument("--version", action="version", version=f"hh {__version__}")
    ap.add_argument("--store-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--root", required=True)
    p.add_argument("--runner", default="unittest")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--cutoff", type=float, default=12.0)
    p.add_argument("--deadline", type=float, default=None,
                   help="CI deadline (s): pick the smallest worker count that fits")
    p.add_argument("--max-workers", type=int, default=None,
                   help="cap for deadline-driven worker choice (default: CPU count)")
    p.add_argument("--resume", action="store_true",
                   help="finish the last run's remainder (paid work is not re-run)")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("record")
    p.add_argument("--ledger", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--runner", default="unittest")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--suite-sig")
    p.add_argument("--total-ms", type=float)
    p.add_argument("--wall-ms", type=float)
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("report")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("tool-spec")
    p.set_defaults(fn=cmd_tool_spec)

    p = sub.add_parser("savings")
    p.add_argument("--job", default=None, help="job id (default: $HH_JOB or 'default')")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_savings)

    p = sub.add_parser("wrap")
    p.add_argument("--policy", default="manual")
    p.add_argument("--suite-id", default="unknown")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--budget", type=float, default=0.0)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_wrap)
    # note: cmd_wrap strips a leading '--' from REMAINDER

    p = sub.add_parser("run")
    p.add_argument("--plan", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--runner", default="unittest")
    p.add_argument("--adapter", default=None)
    p.add_argument("--suite-id", default="run")
    p.set_defaults(fn=cmd_run)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
