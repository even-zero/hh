"""CLI: python3 -m benchmark <cmd> [--repo ...] [--scenario ...]"""
from __future__ import annotations

import argparse
import sys

from . import (compare as compare_mod, config, dashboard as dashboard_mod,
               collect as collect_mod, scenarios)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m benchmark",
                                description="Pinned, config-driven benchmark for hh "
                                            "(see benchmark/README.md).")
    p.add_argument("cmd", choices=["list", "setup", "run", "collect", "dashboard",
                                   "compare", "all"])
    p.add_argument("--repo", "-r", action="append",
                   help="only this repo id (repeatable): see `list`")
    p.add_argument("--scenario", "-s", action="append",
                   help="only this scenario (repeatable): probe, clean, workers, resume, hang")
    p.add_argument("--baseline", default=compare_mod.DEFAULT_BASELINE,
                   help="compare: baseline results.json")
    p.add_argument("--repos-file", default=None,
                   help="alternate repo descriptor JSON (default: benchmark/repos.json); "
                        "use e.g. benchmark/repos.fresh.json for a different repo set")
    args = p.parse_args(argv)

    try:
        cfg = config.load(args.repos_file)
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "list":
        print(f"config: {args.repos_file or 'benchmark/repos.json'}   work dir: benchmark/_work/\n")
        for r in cfg["repos_active"]:
            raw_stars = r.get("stars")
            star = f"{raw_stars/1000:.1f}k★" if isinstance(raw_stars, (int, float)) else str(raw_stars or "")
            print(f"  {r['id']:<16} {star:<8} {r['runner']:<8} lane={r['lane']:<14}"
                  f" scenarios={[k for k, v in r['scenarios'].items() if v]}")
        for r in cfg.get("repos", []):
            if r.get("disabled"):
                print(f"  {r['id']:<16} (disabled — duplicate of repos.json to extend)")
        return 0

    config.work_env()
    if args.cmd in ("all", "setup"):
        for r in cfg["repos_active"]:
            if args.repo and r["id"] not in args.repo:
                continue
            print(f"[setup] {r['id']}")
            scenarios.setup_repo(r)
    if args.cmd in ("all", "run"):
        scenarios.run_scenarios(cfg, args.repo, args.scenario)
    if args.cmd in ("all", "collect"):
        res = collect_mod.collect(cfg)
        print(f"[collect] wrote {config.RESULTS_DIR}/results.json "
              f"({len(res['compat'])} compat rows, "
              f"{sum(len(v) for v in res['attempt_logs'].values())} attempt rows)")
    if args.cmd in ("all", "dashboard"):
        out = dashboard_mod.render(cfg)
        print(f"[dashboard] wrote {out}")
    if args.cmd == "compare":
        md = compare_mod.compare(baseline_path=args.baseline)
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
