"""hh CLI — the whole surface: plan | run | report | tool-spec."""
from __future__ import annotations

import argparse
import json
import sys

from . import store as store_mod
from .adapters import registry


def cmd_plan(args):
    from .tool import build_plan
    plan = build_plan(args.root, runner=args.runner, store_root=args.store_root,
                      resume=args.resume, budget_s=args.budget)
    out = json.dumps(plan, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    print(out)
    return 0


def cmd_run(args):
    from .tool import execute_plan
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    planned = plan.get("runner")
    if args.runner and planned and args.runner != planned:
        print(f"hh: this plan was built for --runner {planned}, "
              f"not {args.runner}", file=sys.stderr)
        return 2
    res = execute_plan(plan, args.root, runner=args.runner or planned,
                       store_root=args.store_root,
                       log=lambda *a: print(*a, file=sys.stderr))
    print(json.dumps(res))
    return 0 if not res["stats"]["error"] else 3


def cmd_report(args):
    st = store_mod.Store(args.store_root)
    agg = st.agg_map("test")
    runs = st.db.execute(
        "SELECT id, runner, total_ms, wall_ms, attempts, started_at FROM runs"
        " ORDER BY started_at DESC LIMIT 10").fetchall()
    print(json.dumps({
        "store": st.path,
        "tests_tracked": len(agg),
        "recent_runs": [dict(zip(["id", "runner", "total_ms", "wall_ms",
                                  "attempts", "ts"], r)) for r in runs],
    }, indent=2, default=str))
    return 0


def cmd_tool_spec(args):
    from .tool import TOOL_SPEC
    print(json.dumps(TOOL_SPEC, indent=2))
    return 0


def main(argv=None) -> int:
    from . import __version__
    ap = argparse.ArgumentParser(prog="hh")
    ap.add_argument("--version", action="version", version=f"hh {__version__}")
    ap.add_argument("--store-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="measure the suite and emit its budget")
    p.add_argument("--root", required=True)
    p.add_argument("--runner", default="unittest", choices=registry.RUNNERS)
    p.add_argument("--budget", type=float, default=None,
                   help="record pass only: first budget to try (default 30 s)")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("run", help="execute a plan and record it")
    p.add_argument("--plan", required=True)
    p.add_argument("--root", required=True)
    # optional: the plan records its runner, and a plan must not be executed by
    # a different adapter than the one that enumerated it
    p.add_argument("--runner", default=None, choices=registry.RUNNERS)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="what this store has measured")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("tool-spec")
    p.set_defaults(fn=cmd_tool_spec)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
