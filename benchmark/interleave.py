"""Alternating A/B: the naive suite command vs `hh plan` + `hh run`.

`scenario_clean` runs its arms back to back (naive, then the hh cold pass, then
the hh warm pass). On a machine whose speed drifts over a few minutes that
ordering is a bias: later arms inherit a slower box. This probe alternates the
order every pass and reports the paired difference, which is what "is hh
overhead?" actually asks.

The hh arm is a *warm* pass: a record pass runs first, off the clock, so the
comparison is "measured plan + run" against "the repo's own command".

Two shapes of the hh arm: `cli` pays `hh plan` and `hh run` (two interpreter
starts), `tool` pays one `run_tests` call — the agent-facing surface, which
plans and executes in a single process.

Run:  python3 -m benchmark.interleave [--reps 5] [--repo click ...]
Writes `_work/results/interleaved.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics

from . import config, harness, scenarios

HH = ["python3", "-m", "hh"]


def arms_for(mode: str, i: int) -> list[str]:
    # alternate: even passes pay naive first, odd passes pay hh first
    arms = ["naive", "hh"] + (["adapter"] if mode == "all" else [])
    return arms if i % 2 == 0 else arms[::-1]


def adapter_cmd(repo: dict, root: str, ledger: str) -> list[str]:
    """The capture adapter alone: same runner, same tests, ledger rows only.

    Isolates "what does recording cost inside the child" from "what does hh's
    planning and ingestion cost around it"."""
    if repo["runner"] == "pytest":
        return ["python3", "-m", "hh.adapters.pytest_stream", "--root", root,
                "--ledger", ledger]
    if repo["runner"] == "unittest":
        return ["python3", "-m", "hh.adapters.unittest_stream", "--root", root,
                "--ledger", ledger]
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "hh", "adapters", "mocha_stream.cjs")
    return ["node", os.path.abspath(script), "run", root, ledger]


def warm_up(repo: dict, store: str, ld: str, env: dict) -> dict:
    """One cold record pass, so every measured pass is a *warm* plan+run."""
    root = repo.get("_root") or config.repo_dir(repo)
    plan = os.path.join(ld, "interleave_record_plan.json")
    harness.run_and_save(HH + ["--store-root", store, "plan", "--root", root,
                               "--runner", repo["runner"], "--workers", "1",
                               "--out", plan],
                         cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                         scenario="interleave", arm="hh", attempt=0,
                         kind="record-plan", budget_s=300)
    parsed, wall, _ = harness.run_and_save(
        HH + ["--store-root", store, "run", "--plan", plan, "--root", root,
              "--runner", repo["runner"], "--suite-id", f"{repo['id']}-record"],
        cwd=config.repo_dir(repo), env=env, repo=repo["id"], scenario="interleave",
        arm="hh", attempt=0, kind="record-run", budget_s=900)
    return {"wall_s": round(wall, 3),
            "ingested": (parsed or {}).get("ingested")}


TOOL_CALL = (
    "import json,sys;"
    "from hh.tool import handle_tool_call;"
    "r=json.loads(handle_tool_call('run_tests', json.loads(sys.argv[1])));"
    "print(json.dumps(r));"
    "sys.exit(0 if r.get('status') in ('ok','tests_failed','up_to_date','incomplete') else 1)"
)


def measure(repo: dict, reps: int, workers: int, mode: str = "cli") -> dict:
    rid = repo["id"]
    ld = config.logdir(rid)
    store = config.store(rid, "interleave")
    shutil.rmtree(store, ignore_errors=True)
    env = config.env_for(repo, store_env_job=f"{rid}-interleave")
    root = repo.get("_root") or config.repo_dir(repo)
    cmd = scenarios.repo_cmd_subst(repo["suite_cmd"], repo)

    record = warm_up(repo, store, ld, env)
    naive, hh, adapter = [], [], []
    for i in range(reps):
        for arm in arms_for(mode, i):
            if arm == "adapter":
                ledger = os.path.join(ld, f"interleave_ledger_{i}.jsonl")
                open(ledger, "w").close()
                _, wall, _ = harness.run_and_save(
                    adapter_cmd(repo, root, ledger), cwd=config.repo_dir(repo),
                    env=env, repo=rid, scenario="interleave", arm="adapter",
                    attempt=i + 1, kind="adapter-run", budget_s=900)
                adapter.append(wall)
            elif arm == "naive":
                _, wall, _ = harness.run_and_save(
                    cmd, cwd=config.repo_dir(repo), env=env, repo=rid,
                    scenario="interleave", arm="naive", attempt=i + 1,
                    kind="naive-run", budget_s=900)
                naive.append(wall)
            elif mode in ("tool", "all"):
                spec = json.dumps({"root": root, "runner": repo["runner"],
                                   "workers": workers, "store_root": store})
                parsed, wall, _ = harness.run_and_save(
                    ["python3", "-c", TOOL_CALL, spec], cwd=config.repo_dir(repo),
                    env=env, repo=rid, scenario="interleave", arm="hh",
                    attempt=i + 1, kind="tool-run", budget_s=900)
                if not (parsed or {}).get("executed", {}).get("attempts"):
                    raise SystemExit(f"{rid}: tool mode executed nothing — wrong runner?")
                hh.append(wall)
            else:
                plan = os.path.join(ld, f"interleave_plan_{i}.json")
                ok, _, _ = harness.run_and_save(
                    HH + ["--store-root", store, "plan", "--root", root,
                          "--runner", repo["runner"], "--workers", str(workers),
                          "--out", plan],
                    cwd=config.repo_dir(repo), env=env, repo=rid,
                    scenario="interleave", arm="hh", attempt=i + 1,
                    kind="plan", budget_s=300)
                parsed, wall, _ = harness.run_and_save(
                    HH + ["--store-root", store, "run", "--plan", plan,
                          "--root", root, "--runner", repo["runner"],
                          "--suite-id", f"{rid}-interleave-{i}"],
                    cwd=config.repo_dir(repo), env=env, repo=rid,
                    scenario="interleave", arm="hh", attempt=i + 1,
                    kind="hh-run", budget_s=900)
                hh.append(wall)
    out = {"repo": rid, "runner": repo["runner"], "reps": reps,
           "workers": workers, "mode": mode, "record": record,
           "adapter_s": [round(x, 3) for x in adapter],
           "adapter_med_s": (round(statistics.median(adapter), 3) if adapter else None),
           "naive_s": [round(x, 3) for x in naive],
           "hh_s": [round(x, 3) for x in hh],
           "naive_med_s": round(statistics.median(naive), 3),
           "hh_med_s": round(statistics.median(hh), 3),
           "delta_pct": round((statistics.median(hh) / statistics.median(naive) - 1) * 100, 1)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m benchmark.interleave")
    ap.add_argument("--repos-file", default=None)
    ap.add_argument("--repo", "-r", action="append")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--mode", choices=("cli", "tool", "all"), default="cli",
                    help="cli: hh plan + hh run (two processes); tool: one run_tests "
                         "call; all: naive + tool + the capture adapter alone")
    args = ap.parse_args(argv)
    cfg = config.load(args.repos_file)
    config.work_env()
    rows = []
    for repo in cfg["repos_active"]:
        if args.repo and repo["id"] not in args.repo:
            continue
        if not repo["lane"].endswith("_exec"):
            continue
        if not os.path.isdir(config.repo_dir(repo)):
            continue
        row = measure(repo, args.reps, args.workers, args.mode)
        rows.append(row)
        print(f"{row['repo']:<10}"
              f"{'  adapter ' + format(row['adapter_med_s'], '.2f') + 's' if row['adapter_med_s'] else ''}"
              f"  naive {row['naive_med_s']:>6.2f}s  "
              f"hh {row['hh_med_s']:>6.2f}s  {row['delta_pct']:+.1f}%   "
              f"(W={row['workers']}, {row['reps']} alternating passes, {row['mode']})")
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    path = os.path.join(config.RESULTS_DIR, "interleaved.json")
    # merge: a per-repo re-run must not delete the other repos' rows
    by_repo = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                by_repo = {r["repo"]: r for r in json.load(f).get("rows", [])}
        except (OSError, ValueError):
            by_repo = {}
    for row in rows:
        by_repo[f"{row['repo']}-w{row['workers']}-{row['mode']}"] = row
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rows": [by_repo[k] for k in sorted(by_repo)]}, f, indent=1)
    print(f"wrote {os.path.join(config.RESULTS_DIR, 'interleaved.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
