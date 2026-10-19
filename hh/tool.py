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
                          raw_of: dict[str, str], meter: Meter) -> None:
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": _tree_fingerprint(root_abs, runner),
                       "root": root_abs, "raw_of": raw_of,
                       "collection_s": round(meter.spans_ms.get("collect", 0.0) / 1000.0, 3)}, f)
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
            entries = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        # already normalized+deduped by the adapter; keep raw_id for execution
        raw_of = {e["key"]: e["raw_id"] for e in entries}
        if cache_path:
            _write_manifest_cache(cache_path, root_abs, runner, raw_of, meter)
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
    if cache_path:
        _write_manifest_cache(cache_path, root_abs, runner, raw_of, meter)
    return list(raw_of), raw_of, round(meter.spans_ms.get("collect", 0.0) / 1000.0, 3)


SAMPLE_MIN_TESTS = 40          # below this, a full run is cheaper than sampling
SAMPLE_MAX = 48                # hard cap on rows measured per sampling round
SAMPLE_FRACTION = 0.02         # rows per round, as a share of the suite
SAMPLE_TARGET_FRACTION = 0.05  # stop sampling once this share is measured
SAMPLE_MAX_ITERATIONS = 3      # recursive rounds before the plan is final


def _sample_keys(keys: list[str], *, max_n: int = SAMPLE_MAX) -> list[str]:
    """Deterministic stratified sample: first/middle/last of each file.

    Round-robin over files (sorted) so the sample spreads across the suite
    rather than clustering in whichever file sorts first. No randomness: the
    same manifest always yields the same sample, so runs are comparable.
    """
    from .keys import file_of
    by_file: dict[str, list[str]] = {}
    for k in keys:
        by_file.setdefault(file_of(k), []).append(k)
    # Round-robin across files: with a 29-row budget and 40 files, a per-file
    # block would sample only the first ~10 files — and their (often slower)
    # tests were then extrapolated to the whole suite. One row per file per
    # round, at evenly spaced positions, keeps the sample representative.
    order = sorted(by_file)
    if len(order) > max_n:
        # Spread the sample across the *whole* sorted file list. Taking the
        # first `max_n` files was a silent bias: on click that picked the
        # alphabetically early (cheap) files and under-predicted the suite by
        # 42%, because the tail of the suite was never looked at.
        stride = max(1, len(order) // max_n)
        order = order[::stride][:max_n]
    used: dict[str, set[int]] = {f: set() for f in order}
    picks: list[str] = []
    round_i = 0
    while len(picks) < max_n:
        added = False
        for f in order:
            ks = by_file[f]
            L = len(ks)
            if len(used[f]) >= L:
                continue
            npts = min(3, L)
            pos = 0 if npts == 1 else round((round_i % npts) * (L - 1) / (npts - 1))
            for step in range(L):
                cand = (pos + step) % L
                if cand not in used[f]:
                    used[f].add(cand)
                    picks.append(ks[cand])
                    added = True
                    break
            if len(picks) >= max_n:
                break
        if not added:
            break
        round_i += 1
    return picks[:max_n]


def _ingest_pending_ledger(store_root: str, runner: str) -> dict:
    """Import ledger rows a killed/unrecorded run left behind (resume inputs).

    Without this, `--resume` estimates from bundled priors even though the
    killed run already measured most of the suite — the failure mode that made
    a warm pytest resume plan 183 s for a 4.8 s suite.
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


def build_plan(root: str, runner: str = "unittest", workers: int = 2,
               cutoff_s: float = 12.0, store_root: str = ".",
               deadline_s: float | None = None,
               max_workers: int | None = None, resume: bool = False,
               sample: bool = True, manifest_file: str | None = None,
               startup_s: float | None = None) -> dict:
    """plan step: estimate from the store, pick budgets/shards, hand back a plan.

    Cold-start protocol: when no durations are measured yet and the suite is
    large enough to bother, the plan is a *sample* plan — a few dozen stratified
    rows — which `execute_plan` runs, records, and then iterates on with a
    second, measured plan. A warm store never takes this path.
    """
    meter = Meter()
    st = store_mod.Store(store_root)
    if manifest_file and os.path.exists(manifest_file):
        # Re-plan inside a run: reuse the manifest the first plan discovered.
        # Collection is the expensive part of planning a large suite, and the
        # cold-start loop must not pay it once per iteration.
        keys, raw_of = [], {}
        with open(manifest_file, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                keys.append(row["key"])
                raw_of[row["key"]] = row["raw_id"]
    else:
        keys, raw_of, collected_s = collect_manifest(root, runner, meter,
                                                     store_root=store_root)
        manifest_file = None
    # collection/startup cost paid once per runner process (pytest/Mocha do the
    # import + collection before executing anything). Budgets exclude it.
    startup_s = (collected_s if startup_s is None else float(startup_s))
    resume_info = {"ingested": 0, "ledger": None}
    if resume:
        with meter.span("resume_ingest"):
            resume_info = _ingest_pending_ledger(store_root, runner)
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
        measured = set(st.agg_map("test"))
        n_sampled = len(measured)
        target_rows = min(SAMPLE_MIN_TESTS, max(8, int(round(len(keys) * SAMPLE_TARGET_FRACTION))))
        remaining = [k for k in keys if k not in measured]
        want_sample = (sample and len(keys) >= SAMPLE_MIN_TESTS
                       and n_sampled < target_rows and remaining)
        if want_sample:
            # One round, sized to the target: sampling in two smaller rounds
            # paid a second runner startup + re-plan for no extra information
            # (and on file-heavy suites round 1 could only take one row/file,
            # so it always fell short and always recurred).
            target = max(8, min(SAMPLE_MAX, target_rows))
            sample_keys = _sample_keys(remaining, max_n=target)
            plan = policy.make_sample_plan(
                est, sample_keys, raw_of, checkpoint_dir=ckpt,
                per_test_cutoff_s=cutoff_s, startup_s=startup_s,
                iteration=1)
            plan.manifest_size = len(keys)
            plan.sample_phase["target_rows"] = target_rows
        else:
            plan = policy.make_plan(est, raw_of, workers=workers, checkpoint_dir=ckpt,
                                    per_test_cutoff_s=cutoff_s, deadline_s=deadline_s,
                                    max_workers=max_workers, startup_s=startup_s)
            plan.manifest_size = len(keys)
    plan.overhead_ms = meter.total_ms
    plan.resume_ingest = resume_info
    if plan.sample_phase:
        # Persist the discovered manifest so the post-sample re-plan is cheap:
        # it reads this file instead of re-running collection.
        path = os.path.join(plan.checkpoint_dir, "manifest.jsonl")
        os.makedirs(plan.checkpoint_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for k in keys:
                f.write(json.dumps({"key": k, "raw_id": raw_of.get(k, k)}) + "\n")
        os.replace(tmp, path)
        plan.sample_phase["manifest_file"] = path
        plan.sample_phase["startup_s"] = startup_s
        plan.sample_phase["calibration"] = est.calibration
    return plan


_SUM_KEYS = ("attempts", "wall_s", "compute_s", "censored", "shard_failures",
             "resumed", "resumed_ms", "preserved_tests", "preserved_ms",
             "startup_s", "startup_attempts", "censored_wait_s",
             "calibration_tests", "calibration_ms")


def _merge_stats(dst: dict | None, src: dict) -> dict:
    out = dict(dst or {})
    for k in _SUM_KEYS:
        if k in src or k in out:
            out[k] = float(out.get(k, 0.0)) + float(src.get(k, 0.0) or 0.0)
    for k, v in src.items():
        if k not in _SUM_KEYS:
            out[k] = v
    return out


def execute_plan(plan: dict, root: str, runner: str = "unittest",
                 adapter: str | None = None,
                 store_root: str = ".", suite_id: str = "run",
                 log=lambda *a: None, caller_limit_s: float | None = None,
                 job: str | None = None, max_sample_iterations: int = 2) -> dict:
    """execute + record step: checkpointed executor, then ingest into the store.

    Cold-start recursion: a plan carrying ``sample_phase`` is executed, its
    ledger is ingested, and a *new* plan is built from those measured rows
    before the remainder runs. One sample (<=48 rows) turns bundled priors into
    measured per-file rates; a second iteration is allowed if the store still
    has no rows (e.g. the sample was killed early), then the loop stops.

    Rows measured by this call's own sample are calibration *cost*, not resume
    savings: they are removed from the resume credit and reported separately.
    """
    from .executor import execute
    meter = Meter()
    st = store_mod.Store(store_root)
    adapter = adapter or {"unittest": "hh.adapters.unittest_stream",
                          "pytest": "hh.adapters.pytest_stream",
                          "node": "hh.adapters.mocha_stream"}[runner]
    root_abs = os.path.abspath(root)
    t0 = time.perf_counter()
    phases: list[dict] = []
    stats: dict | None = None
    call_keys: set[str] = set()
    plan_ms_total = float(plan.get("overhead_ms", 0.0) or 0.0)
    cur = plan
    it = 0
    ledger = os.path.join(cur["checkpoint_dir"], "ledger.jsonl")
    while True:
        it += 1
        before = _done_map_path(ledger)
        phase_stats = execute(cur, root_abs, adapter=adapter, log=log)
        after = _done_map_path(ledger)
        new_here = set(after) - set(before)
        # resume credit earned earlier *in this same call* is calibration cost
        own = set(before) & call_keys
        if own:
            own_ms = sum(before[k] for k in own)
            phase_stats["resumed"] = max(0, int(phase_stats.get("resumed", 0)) - len(own))
            phase_stats["resumed_ms"] = max(0.0, float(phase_stats.get("resumed_ms", 0.0)) - own_ms)
            phase_stats["calibration_tests"] = len(own)
            phase_stats["calibration_ms"] = own_ms
        call_keys |= new_here
        run_id = st.start_run(runner, suite_sig=None)
        ctx = st.ctx_id(workers=cur.get("workers_hint") or 1)
        marker = os.path.join(cur["checkpoint_dir"], "ledger.consumed")
        offset = 0
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as f:
                offset = int(f.read() or 0)
        with meter.span("ingest"):
            n = st.ingest_ledger(ledger, run_id, ctx, runner, offset=offset)
            st.refresh_aggregates()
        with open(marker, "w", encoding="utf-8") as f:
            f.write(str(os.path.getsize(ledger)))
        st.finish_run(run_id, total_ms=phase_stats["wall_s"] * 1000,
                      wall_ms=phase_stats["wall_s"] * 1000,
                      collection_ms=float(phase_stats.get("startup_s", 0.0) or 0.0) * 1000.0)
        stats = _merge_stats(stats, phase_stats)
        phases.append({"phase": ("sample" if cur.get("sample_phase") else "run"),
                       "iteration": it, "tests_in_plan": _n_tests(cur),
                       "tests_executed": len(new_here),
                       "wall_s": round(phase_stats["wall_s"], 3),
                       "startup_s": round(float(phase_stats.get("startup_s", 0.0) or 0.0), 3),
                       "ingested": n, "attempts": phase_stats.get("attempts", 0),
                       "censored": phase_stats.get("censored", 0),
                       "plan_ms": round(float(cur.get("overhead_ms", 0.0) or 0.0), 1),
                       "operating_level": cur.get("operating_level"),
                       "predicted_exec_wall_s": cur.get("predicted_exec_wall_s")})
        if not cur.get("sample_phase"):
            break
        n_sampled = len(call_keys)
        sample_target = int((cur.get("sample_phase") or {}).get("target_rows")
                            or SAMPLE_MIN_TESTS)
        if n_sampled < sample_target and it < max_sample_iterations:
            log(f"  cold sample: only {n_sampled} rows measured — sampling again")
            nxt = build_plan(root, runner=runner,
                             workers=max(1, int(cur.get("workers_hint") or 2)),
                             cutoff_s=cur.get("per_test_cutoff_s", 12.0),
                             store_root=store_root, resume=True, sample=True,
                             manifest_file=_manifest_of(cur),
                             startup_s=cur.get("startup_s"))
            nxt = json.loads(nxt.to_json())
            plan_ms_total += float(nxt.get("overhead_ms", 0.0) or 0.0)
            if nxt.get("sample_phase"):
                nxt["sample_phase"]["iteration"] = it + 1
                phases[-1]["replanned"] = "resample"
            cur = nxt
            continue
        log(f"  cold sample: {n_sampled} rows measured — re-planning from them")
        nxt = build_plan(root, runner=runner,
                         workers=max(1, int(cur.get("workers_hint") or 2)),
                         cutoff_s=cur.get("per_test_cutoff_s", 12.0),
                         store_root=store_root, resume=True, sample=False,
                         manifest_file=_manifest_of(cur),
                         startup_s=cur.get("startup_s"))
        nxt = json.loads(nxt.to_json())
        plan_ms_total += float(nxt.get("overhead_ms", 0.0) or 0.0)
        phases[-1]["replanned"] = {
            "coverage_measured": (nxt.get("est") or {}).get("coverage_measured"),
            "calibration": (nxt.get("est") or {}).get("calibration"),
            "predicted_exec_wall_s": nxt.get("predicted_exec_wall_s"),
            "predicted_wall_s": nxt.get("predicted_wall_s"),
            "operating_level": nxt.get("operating_level"),
            "startup_s": nxt.get("startup_s"),
        }
        cur = nxt

    wall = time.perf_counter() - t0
    stats = dict(stats or {})
    stats["phases"] = phases
    stats["startup_s"] = round(float(stats.get("startup_s", 0.0) or 0.0), 3)
    stats["exec_wall_s"] = round(max(0.0, wall - stats["startup_s"]), 3)
    oh = round(meter.total_ms + plan_ms_total, 3)
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
    return {"stats": stats, "ingested": len(call_keys), "wall_s": round(wall, 3),
            "exec_wall_s": stats["exec_wall_s"], "startup_s": stats["startup_s"],
            "overhead_ms": oh, "run_id": run_id, "ledger": ledger,
            "phases": phases, "plan_used": cur,
            "savings": {"lines": lines, "notes": notes}}


def _manifest_of(plan: dict) -> str | None:
    return (plan.get("sample_phase") or {}).get("manifest_file")


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
        "sampled_tests": int(st_stats.get("calibration_tests", 0) or 0),
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
