"""Reference tool-handler: the agent-facing contract (ARCHITECTURE §11.3).

An agent framework registers ONE tool — `run_tests` — and calls
`handle_tool_call("run_tests", {...})`. Under the hood this is exactly the
plan → checkpointed-execute → record path the lab exercises (`hh plan`,
`hh run`): one code path, no second implementation to drift.

Contract guarantees the response makes explicit (the cost guarantee at the
tool boundary):
  * the tool never restarts a run from zero — an under-budget run extends the
    remainder of the same process, so no finished test is ever re-paid;
  * if the caller's own deadline (`budget_s`) is below the plan's declared
    bound, the tool refuses up front (`status="budget_too_small"`) instead of
    spending the caller's budget on a run it cannot finish;
  * the response carries `planned_bound_s` and `cost_breakdown_ms` so the
    caller can always check "what I paid" against "what it cost".
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import estimator, policy, savings, store as store_mod
from .keys import normalize
from .meter import Meter

TOOL_SPEC = {
    "name": "run_tests",
    "description": (
        "Run a test suite with duration-aware budgeting. Learns per-test "
        "durations from previous runs, so the budget is a measurement, not a "
        "guess; never restarts from zero (finished tests are checkpointed), "
        "and isolates hanging tests instead of killing the whole run. "
        "Returns pass/fail plus the measured budget for the next call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "suite root directory"},
            "runner": {"type": "string", "enum": ["unittest", "pytest", "node"],
                       "default": "unittest"},
            "workers": {"type": "integer", "default": 2},
            "budget_s": {"type": "number",
                         "description": "caller deadline; tool refuses if the "
                                        "plan's declared bound exceeds it"},
            "force": {"type": "boolean", "default": False,
                      "description": "run even if budget_s < planned bound"},
            "deadline_s": {"type": "number",
                           "description": "CI wall-clock deadline for the suite; "
                                          "the tool picks the smallest worker count "
                                          "that fits it, and refuses if infeasible"},
            "resume": {"type": "boolean", "default": False,
                       "description": "finish what the last run in this store left "
                                      "unfinished; tests already paid for in this "
                                      "job are not re-executed"},
            "max_workers": {"type": "integer",
                            "description": "cap for deadline-driven worker choice "
                                           "(default: CPU count; raise for "
                                           "sleep-heavy suites)"},
        },
        "required": ["root"],
    },
}


def collect_manifest(root: str, runner: str, meter: Meter) -> tuple[list[str], dict[str, str]]:
    """Pre-flight: enumerate keys via in-process collection (ms-level, metered).
    This is NEVER a second suite execution (Law 1)."""
    import importlib
    import unittest
    root_abs = os.path.abspath(root)
    if runner == "pytest":
        import subprocess
        import tempfile
        with meter.span("collect"):
            with tempfile.TemporaryDirectory(prefix="hh-collect-") as tmp:
                out = os.path.join(tmp, "nodeids.jsonl")
                proc = subprocess.run(
                    [sys.executable, "-m", "hh.adapters.pytest_stream", "--root", root_abs,
                     "--collect-out", out], capture_output=True, text=True)
                if proc.returncode not in (0, 5) or not os.path.exists(out):
                    raise RuntimeError(f"pytest collection failed: {(proc.stdout + proc.stderr)[-3000:]}")
                with open(out, encoding="utf-8") as f:
                    raw_ids = [json.loads(line) for line in f]
    elif runner == "node":
        import subprocess
        with meter.span("collect"):
            script = os.path.join(os.path.dirname(__file__), "adapters", "mocha_stream.cjs")
            proc = subprocess.run(["node", script, "collect", root_abs],
                                  capture_output=True, text=True)
            if proc.returncode:
                raise RuntimeError(f"Mocha collection failed: {(proc.stdout + proc.stderr)[-3000:]}")
            entries = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        return [e["key"] for e in entries], {e["key"]: e["raw_id"] for e in entries}
    elif runner == "unittest":
        with meter.span("collect"):
            # Long-lived agent processes must not reuse imported stale test modules.
            for name, mod in list(sys.modules.items()):
                f = getattr(mod, "__file__", None)
                if f and os.path.abspath(f).startswith(root_abs + os.sep):
                    sys.modules.pop(name, None)
            importlib.invalidate_caches()
            sys.path.insert(0, root_abs)
            raw_ids = []
            for pattern in ("test_*.py", "*_test.py"):
                loader = unittest.TestLoader()
                suite = loader.discover(root_abs, pattern=pattern, top_level_dir=root_abs)
                if loader.errors:
                    raise RuntimeError("unittest collection failed: " + "; ".join(loader.errors))

                def flatten(s):
                    for test in s:
                        if isinstance(test, unittest.TestSuite):
                            yield from flatten(test)
                        else:
                            yield test.id()

                raw_ids.extend(flatten(suite))
    else:
        raise ValueError(f"unsupported executable runner: {runner}")
    # The historical class-agnostic key can collide for inherited test methods.
    # Keep existing store identities for unambiguous tests, but disambiguate
    # collisions deterministically so no runnable test silently disappears.
    from collections import Counter
    import hashlib
    raw_ids = list(dict.fromkeys(raw_ids))
    counts = Counter(normalize(runner, raw) for raw in raw_ids)
    raw_of = {}
    for raw in raw_ids:
        key = normalize(runner, raw)
        if counts[key] > 1:
            key += " :: collision-" + hashlib.sha1(raw.encode()).hexdigest()[:12]
        raw_of[key] = raw
    return list(raw_of), raw_of


def build_plan(root: str, runner: str = "unittest", workers: int = 2,
               cutoff_s: float = 12.0, store_root: str = ".",
               deadline_s: float | None = None,
               max_workers: int | None = None, resume: bool = False) -> dict:
    """plan step: estimate from the store, pick budgets/shards, hand back a plan."""
    meter = Meter()
    st = store_mod.Store(store_root)
    keys, raw_of = collect_manifest(root, runner, meter)
    with meter.span("estimate"):
        est = estimator.estimate(keys, st, runner=runner, workers=workers)
        hangs = set(st.censored_keys(min_count=2))
        suspects = set(st.censored_keys(min_count=1))
        for p in est.preds:
            if p.key in hangs:
                p.hang_tier = 2
            elif p.key in suspects:
                p.hang_tier = 1
    with meter.span("policy"):
        ckpt = (policy.latest_ledger_dir(store_root) if resume else None) \
            or policy.new_checkpoint_dir(store_root)
        plan = policy.make_plan(est, raw_of, workers=workers, checkpoint_dir=ckpt,
                                per_test_cutoff_s=cutoff_s, deadline_s=deadline_s,
                                max_workers=max_workers)
    plan.overhead_ms = meter.total_ms
    return plan


def execute_plan(plan: dict, root: str, runner: str = "unittest",
                 adapter: str | None = None,
                 store_root: str = ".", suite_id: str = "run",
                 log=lambda *a: None, caller_limit_s: float | None = None,
                 job: str | None = None) -> dict:
    """execute + record step: checkpointed executor, then ingest into the store."""
    from .executor import execute
    meter = Meter()
    st = store_mod.Store(store_root)
    t0 = time.perf_counter()
    adapter = adapter or {"unittest": "hh.adapters.unittest_stream",
                          "pytest": "hh.adapters.pytest_stream",
                          "node": "hh.adapters.mocha_stream"}[runner]
    stats = execute(plan, os.path.abspath(root), adapter=adapter, log=log)
    wall = time.perf_counter() - t0
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    run_id = st.start_run(runner, suite_sig=None)
    ctx = st.ctx_id(workers=plan.get("workers_hint") or 1)
    marker = os.path.join(plan["checkpoint_dir"], "ledger.consumed")
    offset = 0
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            offset = int(f.read() or 0)
    with meter.span("ingest"):
        n = st.ingest_ledger(ledger, run_id, ctx, runner, offset=offset)
        st.refresh_aggregates()
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(os.path.getsize(ledger)))
    st.finish_run(run_id, total_ms=stats["wall_s"] * 1000, wall_ms=wall * 1000)
    oh = round(meter.total_ms + plan.get("overhead_ms", 0.0), 3)
    rec = {"ts": time.time(), "policy": "hh-full", "suite_id": suite_id,
           "attempt": 1, "budget_s": plan.get("total_budget_s"), "outcome": "ok",
           "wall_ms": round(wall * 1000, 1), "wasted_ms": 0.0, "overhead_ms": oh}
    st.log_attempt(**rec)
    exp = os.path.join(store_root, ".hh", "experiments")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "attempt_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    lines, notes = savings.credit_lines(
        stats, wall, caller_limit_s, int(stats.get("censored", 0)))
    savings.record(store_root, job=job, suite_id=suite_id, lines=lines)
    return {"stats": stats, "ingested": n, "wall_s": round(wall, 3),
            "overhead_ms": oh, "run_id": run_id, "ledger": ledger,
            "savings": {"lines": lines, "notes": notes}}


def run_tests(root: str, runner: str = "unittest", workers: int = 2,
              budget_s: float | None = None, force: bool = False,
              store_root: str = ".", cutoff_s: float = 12.0,
              verbose: bool = False, deadline_s: float | None = None,
              max_workers: int | None = None, resume: bool = False,
              job: str | None = None) -> dict:
    """The agent-facing tool. One call = plan → execute → record → summary."""
    t_start = time.perf_counter()
    plan_obj = build_plan(root, runner=runner, workers=workers,
                          cutoff_s=cutoff_s, store_root=store_root,
                          deadline_s=deadline_s, max_workers=max_workers,
                          resume=resume)
    plan = json.loads(plan_obj.to_json())       # same shape the CLI hands the executor
    est = plan["est"]
    planned = plan["total_budget_s"]
    cov_before = est["coverage"]

    suite_name = os.path.basename(os.path.abspath(root))
    dec = plan.get("deadline_decision")
    if dec is not None and not dec["feasible"] and not force:
        rl = savings.refusal_lines(
            f"best-effort wall {dec['predicted_wall_s']}s > deadline {deadline_s}s",
            deadline_s)
        savings.record(store_root, job=job, suite_id=suite_name, lines=rl)
        return {
            "time_saved": savings.block(store_root, job, rl, [], None),
            "status": "deadline_infeasible",
            "deadline_s": deadline_s, "planned_bound_s": round(planned, 2),
            "estimate": est,
            "deadline_decision": dec,
            "hint": (f"{dec['rationale']}. Options: raise deadline_s, raise "
                     f"max_workers (sleep-heavy suites scale past CPU count), "
                     f"lower cutoff_s, or pass force=true to run the best-effort "
                     f"plan anyway (finished tests still checkpoint)."),
            "cost_breakdown_ms": {"plan": round(plan["overhead_ms"], 1)},
        }

    if budget_s is not None and deadline_s is None and planned > budget_s and not force:
        n_tests = sum(len(s["tests"]) for s in plan["shards"])
        rl = savings.refusal_lines(
            f"declared bound {planned:.1f}s > caller budget {budget_s}s", budget_s)
        savings.record(store_root, job=job, suite_id=suite_name, lines=rl)
        return {
            "time_saved": savings.block(store_root, job, rl, [], None),
            "status": "budget_too_small",
            "planned_bound_s": round(planned, 2), "caller_budget_s": budget_s,
            "estimate": est, "known_tests": n_tests - est.get("n_unknown", 0),
            "hint": (f"The plan's declared bound ({planned:.1f}s) exceeds your "
                     f"deadline ({budget_s}s). Running would spend your budget "
                     f"on a run the tool cannot promise to finish. Raise "
                     f"budget_s, cut workers, or pass force=true to run anyway "
                     f"(checkpointing still saves finished tests)."),
            "cost_breakdown_ms": {"plan": round(plan["overhead_ms"] + 0.0, 1)},
        }

    caller_limit_s = deadline_s if deadline_s is not None else budget_s
    res = execute_plan(plan, root, runner=runner, store_root=store_root,
                       suite_id=suite_name,
                       log=(lambda *a: print(*a, file=sys.stderr)) if verbose else (lambda *a: None),
                       caller_limit_s=caller_limit_s, job=job)

    # read outcomes for the summary (never dump every row to the caller)
    fails, slowest, skipped = [], [], 0
    for line in open(res["ledger"], encoding="utf-8"):
        r = json.loads(line)
        if r.get("type") != "end":
            continue
        if r["status"] == "failed":
            fails.append({"key": r["key"], "ms": r["duration_ms"]})
        elif r["status"] == "skipped":
            skipped += 1
        slowest.append((r["duration_ms"], r["key"]))
    slowest.sort(reverse=True)

    # coverage after this run = manifest keys now present in the store
    st = store_mod.Store(store_root)
    agg = st.agg_map("test")
    n_manifest = len({t["key"] for s in plan["shards"] for t in s["tests"]})
    known_after = sum(1 for s in plan["shards"] for t in s["tests"] if t["key"] in agg)

    st_stats = res["stats"]
    if st_stats.get("attempts", 0) == 0 and st_stats.get("resumed", 0) > 0:
        status = "up_to_date"          # resume found everything already paid for
    elif fails:
        status = "tests_failed"
    else:
        status = "ok"
    return {
        "status": status,
        "time_saved": savings.block(store_root, job, res["savings"]["lines"],
                                    res["savings"]["notes"], res["wall_s"]),
        "resumed_tests": int(st_stats.get("resumed", 0)),
        "wall_s": res["wall_s"],
        "planned_bound_s": round(planned, 2),
        "estimate": est,
        "workers": plan.get("workers_hint"),
        "workers_rationale": plan.get("workers_rationale"),
        "deadline_decision": dec,
        "predicted_wall_s": plan.get("predicted_wall_s"),
        "shards": [{"budget_s": round(s["budget_s"], 2), "tests": len(s["tests"]),
                    "quarantine": bool(s.get("quarantine"))} for s in plan["shards"]],
        "executed": {"attempts": res["stats"]["attempts"],
                     "censored": res["stats"]["censored"],
                     "shard_failures": res["stats"]["shard_failures"]},
        "results": {"ingested": res["ingested"], "failed": fails[:20],
                    "n_failed": len(fails), "n_skipped": skipped,
                    "slowest": [{"key": k, "ms": ms} for ms, k in slowest[:5]]},
        "learning": {"coverage_before": cov_before,
                     "coverage_after": round(known_after / n_manifest, 3) if n_manifest else 0.0,
                     "known_tests": len(agg)},
        "cost_breakdown_ms": {"plan_overhead": round(plan["overhead_ms"], 1),
                              "ingest_overhead": round(res["overhead_ms"] - plan["overhead_ms"], 1),
                              "total_overhead": res["overhead_ms"]},
        "next_call_hint": ("Durations are now measured for "
                           f"{known_after}/{n_manifest} tests — the next call's "
                           "budget will be a measurement."),
        "total_tool_latency_s": round(time.perf_counter() - t_start, 3),
    }


def handle_tool_call(name: str, arguments: dict | str) -> str:
    """What an agent framework's executor actually calls: name + JSON args -> JSON."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    if name != "run_tests":
        return json.dumps({"error": f"unknown tool {name!r}", "known": ["run_tests"]})
    try:
        out = run_tests(**arguments)
    except TypeError as e:                       # bad args from the model
        return json.dumps({"error": f"bad arguments: {e}",
                           "spec": TOOL_SPEC["parameters"]["properties"]})
    return json.dumps(out, indent=1)


if __name__ == "__main__":                       # manual smoke: python3 -m hh.tool <root>
    print(handle_tool_call("run_tests", {"root": sys.argv[1], "workers": 2}))
