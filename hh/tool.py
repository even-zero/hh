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
        "and isolates hanging tests instead of killing the whole run. A store "
        "with no measurements records the suite like a plain run and makes no "
        "prediction; the deadline/worker checks therefore apply from the "
        "second call on. Returns pass/fail plus the measured budget for the "
        "next call."
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


def _tree_fingerprint(root: str, runner: str) -> str:
    """Cheap change detector for the test tree (paths + size + mtime)."""
    import hashlib
    h = hashlib.sha1()
    h.update(runner.encode())
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
            ".tox", ".mypy_cache", ".pytest_cache", ".hh", "site-packages"}
    exts = (".py",) if runner != "node" else (".js", ".cjs", ".mjs", ".json")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        for f in sorted(filenames):
            if not f.endswith(exts):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{os.path.relpath(p, root)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


def _write_manifest_cache(cache_path: str, root_abs: str, runner: str,
                          raw_of: dict[str, str], collection_s: float) -> None:
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": _tree_fingerprint(root_abs, runner),
                       "root": root_abs, "raw_of": raw_of,
                       "collection_s": round(collection_s, 3)}, f)
        os.replace(tmp, cache_path)
    except OSError:
        pass


def collect_manifest(root: str, runner: str, meter: Meter,
                     store_root: str | None = None
                     ) -> tuple[list[str], dict[str, str], float]:
    """Pre-flight: enumerate keys via in-process collection (ms-level, metered).

    This is NEVER a second suite execution (Law 1). The manifest is cached
    beside the store and keyed by a tree fingerprint: a warm plan (nothing
    changed since the last plan) reuses it instead of paying pytest/Mocha
    collection again. Collection is the single largest fixed cost of planning a
    large suite, and it has nothing to do with test execution.

    Returns (keys, raw_of, collection_s) where collection_s is the *measured*
    runner collection/startup cost (cached across calls).
    """
    import importlib
    import unittest
    root_abs = os.path.abspath(root)
    cache_path = None
    if store_root:
        cache_dir = os.path.join(store_root, ".hh")
        cache_path = os.path.join(cache_dir, f"manifest-cache-{runner}.json")
        fp = _tree_fingerprint(root_abs, runner)
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("fingerprint") == fp and cached.get("root") == root_abs:
                # reuse the *measured* collection cost so the executor's
                # startup accounting survives the cache hit
                return (list(cached["raw_of"]), dict(cached["raw_of"]),
                        float(cached.get("collection_s", 0.0)))
        except (OSError, ValueError, KeyError):
            pass
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
            # The child's stdout is the protocol, but a test tree that prints
            # while loading (express logs its routes) would otherwise make
            # discovery crash. Keep every JSON entry; ignore non-protocol noise,
            # and fail loudly if the noise was all we got.
            entries, noise = [], 0
            for line in proc.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    noise += 1
                    continue
                if not isinstance(row, dict) or "key" not in row:
                    noise += 1
                    continue
                entries.append(row)
            if not entries and noise:
                raise RuntimeError(f"Mocha collection produced no test ids "
                                   f"({noise} non-protocol lines): "
                                   f"{proc.stdout[-1500:]}")
        # already normalized+deduped by the adapter; keep raw_id for execution
        raw_of = {e["key"]: e["raw_id"] for e in entries}
        if cache_path:
            _write_manifest_cache(cache_path, root_abs, runner, raw_of,
                                  meter.spans_ms.get("collect", 0.0) / 1000.0)
        return list(raw_of), raw_of, round(meter.spans_ms.get("collect", 0.0) / 1000.0, 3)
    elif runner == "unittest":
        with meter.span("collect"):
            # Long-lived agent processes must not reuse imported stale test modules.
            for name, mod in list(sys.modules.items()):
                f = getattr(mod, "__file__", None)
                if f and os.path.abspath(f).startswith(root_abs + os.sep):
                    sys.modules.pop(name, None)
            importlib.invalidate_caches()
            sys.path.insert(0, root_abs)
            from .adapters.unittest_stream import discover_ids
            raw_ids, errors = discover_ids(root_abs)
            if not raw_ids and errors:
                raise RuntimeError("unittest collection failed: " + "; ".join(errors[:5]))
    else:
        raise ValueError(f"unsupported executable runner: {runner}")
    # The historical class-agnostic key can collide for inherited test methods.
    # Keep existing store identities for unambiguous tests, but disambiguate
    # collisions deterministically so no runnable test silently disappears.
    from .keys import unique_key_map
    raw_ids = list(dict.fromkeys(raw_ids))
    raw_of = {key: raw for raw, key in unique_key_map(raw_ids, runner).items()}
    if cache_path:
        _write_manifest_cache(cache_path, root_abs, runner, raw_of,
                              meter.spans_ms.get("collect", 0.0) / 1000.0)
    return list(raw_of), raw_of, round(meter.spans_ms.get("collect", 0.0) / 1000.0, 3)


def _ingest_pending_ledger(store_root: str, runner: str) -> dict:
    """Import ledger rows a killed/unrecorded run left behind (resume inputs).

    Without this, `--resume` would plan as if nothing had been measured even
    though the killed run already recorded most of the suite — the failure mode
    that made a warm pytest resume plan 183 s for a 4.8 s suite.
    """
    d = policy.latest_ledger_dir(store_root)
    if not d:
        return {"ingested": 0, "ledger": None}
    ledger = os.path.join(d, "ledger.jsonl")
    if not os.path.exists(ledger):
        return {"ingested": 0, "ledger": ledger}
    marker = os.path.join(d, "ledger.consumed")
    offset = 0
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            offset = int(f.read() or 0)
    st = store_mod.Store(store_root)
    run_id = st.start_run(runner, suite_sig=None)
    ctx = st.ctx_id(workers=1)
    n = st.ingest_ledger(ledger, run_id, ctx, runner, offset=offset)
    st.refresh_aggregates()
    st.finish_run(run_id, total_ms=0.0, wall_ms=0.0)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(os.path.getsize(ledger)))
    return {"ingested": n, "ledger": ledger}


COLD_RECORD_BUDGET_S = 30.0     # first rung of the default naive ladder


def build_plan(root: str, runner: str = "unittest", workers: int = 2,
               cutoff_s: float = 12.0, store_root: str = ".",
               deadline_s: float | None = None,
               max_workers: int | None = None, resume: bool = False,
               startup_s: float | None = None,
               record_budget_s: float = COLD_RECORD_BUDGET_S) -> dict:
    """plan step: estimate from *measured* rows, pick budgets/shards.

    Cold-store policy: when this store holds no duration yet, hh does not
    predict and does not even enumerate the suite. It returns a **record
    plan** — one full-suite shard that the runner executes exactly like its
    naive invocation (same single process, same discovery) while the ledger
    records every duration. The next plan is then a measurement. This is the
    tool's scope: remove the guesswork from the second run onwards; do not try
    to solve the first.
    """
    meter = Meter()
    st = store_mod.Store(store_root)
    resume_info = {"ingested": 0, "ledger": None}
    if resume:
        with meter.span("resume_ingest"):
            resume_info = _ingest_pending_ledger(store_root, runner)
    measured = set(st.agg_map("test"))
    if not measured:
        with meter.span("policy"):
            ckpt = policy.new_checkpoint_dir(store_root)
            plan = policy.make_record_plan(
                checkpoint_dir=ckpt, per_test_cutoff_s=cutoff_s,
                startup_s=float(startup_s or 0.0), budget_s=record_budget_s)
            if deadline_s is not None:
                # No measurements ⇒ no feasibility claim. Say so and record.
                plan.deadline_decision = {
                    "workers": 1, "feasible": None, "deadline_s": deadline_s,
                    "predicted_wall_s": None, "max_workers": 1,
                    "rationale": ("cold store: no measurements yet, so the "
                                  f"{deadline_s}s deadline cannot be verified. "
                                  "Recording the suite first; the next call can "
                                  "plan against the deadline."),
                }
        plan.measured_tests = 0
        plan.overhead_ms = meter.total_ms
        plan.resume_ingest = resume_info
        return plan

    keys, raw_of, collected_s = collect_manifest(root, runner, meter,
                                                 store_root=store_root)
    # collection/startup cost paid once per runner process (pytest/Mocha do the
    # import + collection before executing anything). Budgets exclude it.
    startup_s = (collected_s if startup_s is None else float(startup_s))
    with meter.span("policy"):
        ckpt = (policy.latest_ledger_dir(store_root) if resume else None) \
            or policy.new_checkpoint_dir(store_root)
        overhead = st.runner_overhead_ms(runner)
        with meter.span("estimate"):
            est = estimator.estimate(keys, st, runner=runner, workers=workers,
                                     runner_overhead_ms=overhead)
            hangs = set(st.censored_keys(min_count=2))
            suspects = set(st.censored_keys(min_count=1))
            for p in est.preds:
                if p.key in hangs:
                    p.hang_tier = 2
                elif p.key in suspects:
                    p.hang_tier = 1
        plan = policy.make_plan(est, raw_of, workers=workers, checkpoint_dir=ckpt,
                                per_test_cutoff_s=cutoff_s, deadline_s=deadline_s,
                                max_workers=max_workers, startup_s=startup_s)
        plan.manifest_size = len(keys)
    plan.measured_tests = sum(1 for k in keys if k in measured)
    plan.overhead_ms = meter.total_ms
    plan.resume_ingest = resume_info
    return plan


def execute_plan(plan: dict, root: str, runner: str = "unittest",
                 adapter: str | None = None,
                 store_root: str = ".", suite_id: str = "run",
                 log=lambda *a: None, caller_limit_s: float | None = None,
                 job: str | None = None) -> dict:
    """execute + record step: checkpointed executor, then ingest into the store.

    One plan, one pass. A cold plan (``operating_level == "record"``) runs the
    whole suite under naive-style budgets and records it; a measured plan runs
    the shards hh computed. Either way every finished test lands in the store,
    which is what the next plan reads.
    """
    from .executor import execute
    meter = Meter()
    st = store_mod.Store(store_root)
    adapter = adapter or {"unittest": "hh.adapters.unittest_stream",
                          "pytest": "hh.adapters.pytest_stream",
                          "node": "hh.adapters.mocha_stream"}[runner]
    root_abs = os.path.abspath(root)
    t0 = time.perf_counter()
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    before = _done_map_path(ledger)
    phase_stats = execute(plan, root_abs, adapter=adapter, log=log)
    after = _done_map_path(ledger)
    new_here = set(after) - set(before)
    new_ms = sum(after[k] for k in new_here)

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
    if os.path.exists(ledger):
        with open(marker, "w", encoding="utf-8") as f:
            f.write(str(os.path.getsize(ledger)))
    complete = _run_complete(phase_stats, plan.get("operating_level") == "record")
    if (plan.get("operating_level") == "record" and complete):
        # The ledger of a completed record pass *is* the manifest for this tree:
        # cache it (fingerprint-checked) so the next plan skips discovery
        # entirely instead of paying pytest/Mocha collection a second time.
        raw_of: dict[str, str] = {}
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") == "end" and row.get("key"):
                    raw_of[row["key"]] = row.get("raw_id", row["key"])
        if raw_of:
            _write_manifest_cache(
                os.path.join(store_root, ".hh", f"manifest-cache-{runner}.json"),
                root_abs, runner, raw_of,
                float(phase_stats.get("startup_s", 0.0) or 0.0))
    # total_ms is Σ executed test durations; wall_ms is the whole attempt. The
    # difference (minus collection) is the runner bookkeeping the next plan
    # budgets for (`Store.runner_overhead_ms`).
    st.finish_run(run_id, total_ms=new_ms,
                  wall_ms=phase_stats["wall_s"] * 1000,
                  collection_ms=float(phase_stats.get("startup_s", 0.0) or 0.0) * 1000.0)

    wall = time.perf_counter() - t0
    stats = dict(phase_stats)
    phase = "record" if plan.get("operating_level") == "record" else "run"
    phases = [{"phase": phase, "iteration": 1, "tests_in_plan": _n_tests(plan),
               "tests_executed": len(new_here),
               "wall_s": round(stats["wall_s"], 3),
               "startup_s": round(float(stats.get("startup_s", 0.0) or 0.0), 3),
               "ingested": n, "attempts": stats.get("attempts", 0),
               "censored": stats.get("censored", 0),
               "plan_ms": round(float(plan.get("overhead_ms", 0.0) or 0.0), 1),
               "operating_level": plan.get("operating_level"),
               "predicted_exec_wall_s": plan.get("predicted_exec_wall_s")}]
    stats["phases"] = phases
    stats["startup_s"] = round(float(stats.get("startup_s", 0.0) or 0.0), 3)
    stats["exec_wall_s"] = round(max(0.0, wall - stats["startup_s"]), 3)
    oh = round(meter.total_ms + float(plan.get("overhead_ms", 0.0) or 0.0), 3)
    rec = {"ts": time.time(), "policy": "hh-full", "suite_id": suite_id,
           "attempt": 1, "budget_s": plan.get("total_budget_s"), "outcome": "ok",
           "wall_ms": round(wall * 1000, 1), "wasted_ms": 0.0, "overhead_ms": oh}
    st.log_attempt(**rec)
    exp = os.path.join(store_root, ".hh", "experiments")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "attempt_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    lines, notes = savings.credit_lines(
        stats, wall, caller_limit_s, int(stats.get("censored", 0)),
        censored_wait_s=float(stats.get("censored_wait_s", 0.0) or 0.0),
        cutoff_s=float(plan.get("per_test_cutoff_s", 12.0)))
    savings.record(store_root, job=job, suite_id=suite_id, lines=lines)
    return {"stats": stats, "complete": complete,
            "ingested": len(new_here), "wall_s": round(wall, 3),
            "exec_wall_s": stats["exec_wall_s"], "startup_s": stats["startup_s"],
            "overhead_ms": oh, "run_id": run_id, "ledger": ledger,
            "phases": phases, "plan_used": plan,
            "savings": {"lines": lines, "notes": notes}}


def _run_complete(stats: dict, record_pass: bool) -> bool:
    """Did this pass finish the work it was given?

    A record pass must have written its suite-completion marker; any pass with
    a dead shard or an unfinished record ladder is *not* complete — no manifest
    cache is sealed from it, and the payload says so instead of pretending.
    """
    if stats.get("shard_failures") or stats.get("incomplete"):
        return False
    if record_pass:
        return bool(stats.get("suite_complete"))
    return True


def _n_tests(plan: dict) -> int:
    return sum(len(s["tests"]) for s in plan.get("shards", []))


def _done_map_path(ledger: str) -> dict:
    from .executor import _done_map
    return _done_map(ledger)


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
    record_pass = plan.get("operating_level") == "record"
    # A cold store has no measurements, so hh cannot refuse or accept a
    # deadline on evidence. It records the suite (naive behaviour) and the
    # caller gets an honest note instead of a fabricated feasibility answer.
    deadline_note = None
    if record_pass and dec is not None:
        deadline_note = dec.get("rationale")
    if dec is not None and dec.get("feasible") is False and not force:
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

    if (budget_s is not None and deadline_s is None and planned > budget_s
            and not force and not record_pass):
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
    if record_pass:
        # A record plan has no test list: the ledger it just produced is the
        # manifest, and every key on it is now measured.
        run_keys = set(_done_map_path(res["ledger"]))
        n_manifest = len(run_keys)
        known_after = sum(1 for k in run_keys if k in agg)
    else:
        n_manifest = len({t["key"] for s in plan["shards"] for t in s["tests"]})
        known_after = sum(1 for s in plan["shards"] for t in s["tests"] if t["key"] in agg)

    st_stats = res["stats"]
    if st_stats.get("attempts", 0) == 0 and st_stats.get("resumed", 0) > 0:
        status = "up_to_date"          # resume found everything already paid for
    elif st_stats.get("incomplete"):
        # cold record pass hit the naive ladder's ceiling: partial data, and
        # the caller must be told so (a naive run would simply have given up)
        status = "incomplete"
    elif st_stats.get("shard_failures"):
        # an adapter that dies before running its tests must never look like a
        # green run: the missing tests were force-marked "error", not executed
        status = "shard_failed"
    elif fails:
        status = "tests_failed"
    else:
        status = "ok"
    return {
        "status": status,
        "time_saved": savings.block(store_root, job, res["savings"]["lines"],
                                    res["savings"]["notes"], res["wall_s"],
                                    overhead_s=res["overhead_ms"] / 1000.0),
        "resumed_tests": int(st_stats.get("resumed", 0)),
        "operating_level": plan.get("operating_level"),
        "record_pass": record_pass,
        "deadline_note": deadline_note,
        "wall_s": res["wall_s"],
        "exec_wall_s": res.get("exec_wall_s"),
        "startup_s": res.get("startup_s"),
        "phases": res.get("phases"),
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
                     "incomplete": res["stats"].get("incomplete", 0),
                     "shard_failures": res["stats"]["shard_failures"]},
        "shard_errors": res["stats"].get("errors", []),
        "results": {"ingested": res["ingested"], "failed": fails[:20],
                    "n_failed": len(fails), "n_skipped": skipped,
                    "slowest": [{"key": k, "ms": ms} for ms, k in slowest[:5]]},
        "learning": {"coverage_before": cov_before,
                     "coverage_after": round(known_after / n_manifest, 3) if n_manifest else 0.0,
                     "calibration": est.get("calibration"),
                     "known_tests": len(agg)},
        "cost_breakdown_ms": {"plan_overhead": round(plan["overhead_ms"], 1),
                              "ingest_overhead": round(res["overhead_ms"] - plan["overhead_ms"], 1),
                              "total_overhead": res["overhead_ms"]},
        "next_call_hint": (
            (f"Cold store: this pass recorded {known_after}/{n_manifest} tests "
             "without predicting anything. The next call plans from these "
             "measurements.")
            if record_pass or cov_before == 0.0 else
            ("Durations are now measured for "
             f"{known_after}/{n_manifest} tests — the next call's budget will "
             "be a measurement.")),
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
