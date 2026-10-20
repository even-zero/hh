"""The agent-facing tool: `run_tests` -> a run, and the timeout it needed.

One tool, one job. The tool runs a suite, records how long each test took, and
returns the budget (timeout) that fits this suite on this machine. On the first
call it has nothing to measure from, so it runs the suite exactly like the
naive command would and records it; every call after that uses the measured
budget.

The response always answers the two questions a caller has:

  * did the run finish, and how long did it take?  (`status`, `wall_s`)
  * what budget should I pass to the process that runs this next time?
    (`budget_s`, `basis`, `confidence`)
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import estimator, policy, store as store_mod
from .adapters import registry
from .keys import unique_key_map
from .meter import Meter

RUNNERS = registry.RUNNERS

TOOL_SPEC = {
    "name": "run_tests",
    "description": (
        "Run a test suite and return the timeout it needs. The first call runs "
        "the suite like the plain runner command and records per-test durations "
        "(no prediction); later calls set the timeout from those measurements, "
        "so the suite is not killed by a budget that was guessed too low. An "
        "overrun is resumed from its ledger, never restarted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "suite root directory"},
            "runner": {"type": "string", "enum": list(RUNNERS),
                       "default": "unittest"},
            "budget_s": {"type": "number",
                         "description": "caller's own timeout in seconds; when given, "
                                        "the tool runs under it and reports whether "
                                        "it was enough"},
            "resume": {"type": "boolean", "default": False,
                       "description": "finish what the previous run left unfinished"},
        },
        "required": ["root"],
    },
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", ".hh", "site-packages"}

# Module adapters that enumerate out of process (everything but unittest, which
# imports the tree, and pytest, whose ids are plain strings).
EXTERNAL_MODULE_RUNNERS = set(registry.MODULE_ADAPTERS) - {"unittest", "pytest"}

# What counts as "the suite changed" for the manifest cache.
TREE_EXTS = {
    "junit": (".java", ".kt", ".kts", ".class", ".xml"),
    "go": (".go", ".mod", ".sum"),
    "nextest": (".rs", ".toml", ".lock"),
    "rspec": (".rb", ".gemspec"),
    "phpunit": (".php", ".xml"),
    "ctest": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".cmake",
              ".txt"),
}


def _read_adapter_ids(runner: str, path: str) -> dict[str, str]:
    """Read `{raw_id, key}` discovery rows into key -> raw id."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise RuntimeError(f"{runner} collection wrote a malformed id: "
                                   + line[:200])
            if not isinstance(row, dict) or "key" not in row or "raw_id" not in row:
                raise RuntimeError(f"{runner} collection wrote an id without a "
                                   "key: " + line[:200])
            out[row["key"]] = row["raw_id"]
    return out


def _tree_fingerprint(root: str, runner: str) -> str:
    import hashlib
    h = hashlib.sha1()
    h.update(runner.encode())
    exts = ((".js", ".cjs", ".mjs", ".json", ".ts", ".tsx", ".jsx",
             ".mts", ".cts")
            if registry.is_node(runner) else TREE_EXTS.get(runner, (".py",)))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
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


def collect_manifest(root: str, runner: str, meter: Meter,
                     store_root: str | None = None) -> tuple[list[str], dict[str, str], float]:
    """Enumerate the suite's test ids (ms-level; cached by tree fingerprint).

    This is discovery, never execution: pytest runs `--collect-only`, unittest
    imports the tests, every JS adapter enumerates its files. A warm plan reuses
    the cached manifest instead of paying it again.
    """
    import subprocess
    import tempfile

    root_abs = os.path.abspath(root)
    cache_path = None
    if store_root:
        cache_path = os.path.join(store_root, ".hh", f"manifest-{runner}.json")
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if (cached.get("fingerprint") == _tree_fingerprint(root_abs, runner)
                    and cached.get("root") == root_abs):
                return (list(cached["raw_of"]), dict(cached["raw_of"]),
                        float(cached.get("collection_s", 0.0)))
        except (OSError, ValueError, KeyError):
            pass

    # The adapter runs in a child process; make sure it can import executor when
    # tool itself is being used from a source checkout rather than a wheel.
    child_env = dict(os.environ)
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child_env["PYTHONPATH"] = os.pathsep.join(
        [pkg_parent, child_env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    raw_ids: list[str] = []
    adapter_keys: dict[str, str] | None = None
    with meter.span("collect"):
        if runner == "pytest":
            with tempfile.TemporaryDirectory(prefix="hh-collect-") as tmp:
                out = os.path.join(tmp, "nodeids.jsonl")
                proc = subprocess.run(
                    [sys.executable, "-m", "executor.adapters.pytest_stream",
                     "--root", root_abs, "--collect-out", out],
                    capture_output=True, text=True, env=child_env)
                if proc.returncode not in (0, 5) or not os.path.exists(out):
                    raise RuntimeError("pytest collection failed: "
                                       + (proc.stdout + proc.stderr)[-2000:])
                with open(out, encoding="utf-8") as f:
                    raw_ids = [json.loads(line) for line in f]
        elif registry.is_node(runner):
            script = registry.node_script(runner)
            with tempfile.TemporaryDirectory(prefix="hh-collect-") as tmp:
                out = os.path.join(tmp, "ids.jsonl")
                proc = subprocess.run(["node", script, "collect", root_abs, out],
                                      capture_output=True, text=True)
                if proc.returncode != 0 or not os.path.exists(out):
                    raise RuntimeError(f"{runner} collection failed: "
                                       + (proc.stdout + proc.stderr)[-2000:])
                adapter_keys = _read_adapter_ids(runner, out)
        elif runner in EXTERNAL_MODULE_RUNNERS:
            # junit, go, nextest, rspec, phpunit, ctest: a Python module that
            # drives the runner and owns its identity scheme.
            with tempfile.TemporaryDirectory(prefix="hh-collect-") as tmp:
                out = os.path.join(tmp, "ids.jsonl")
                proc = subprocess.run(
                    [sys.executable, "-m", registry.module_name(runner),
                     "--root", root_abs, "--collect-out", out],
                    capture_output=True, text=True, env=child_env)
                if proc.returncode != 0 or not os.path.exists(out):
                    raise RuntimeError(f"{runner} collection failed: "
                                       + (proc.stdout + proc.stderr)[-2000:])
                adapter_keys = _read_adapter_ids(runner, out)
        else:
            # unittest: import the modules and enumerate (no execution)
            for name, mod in list(sys.modules.items()):
                f = getattr(mod, "__file__", None)
                if f and os.path.abspath(f).startswith(root_abs + os.sep):
                    sys.modules.pop(name, None)
            import importlib
            importlib.invalidate_caches()
            sys.path.insert(0, root_abs)
            from .adapters.unittest_stream import discover_ids
            raw_ids, errors = discover_ids(root_abs)
            if not raw_ids and errors:
                raise RuntimeError("unittest collection failed: " + "; ".join(errors[:5]))
    if adapter_keys is not None:
        # Those adapters own their identity scheme (Mocha's full titles, a
        # file path for the file-level JS runners, package::test for Go, …).
        # Re-normalising here would produce keys the ledger never writes.
        raw_of = adapter_keys
    else:
        raw_ids = list(dict.fromkeys(raw_ids))
        raw_of = {key: raw for raw, key in unique_key_map(raw_ids, runner).items()}
    collection_s = round(meter.spans_ms.get("collect", 0.0) / 1000.0, 3)
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"fingerprint": _tree_fingerprint(root_abs, runner),
                           "root": root_abs, "raw_of": raw_of,
                           "collection_s": collection_s}, f)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return list(raw_of), raw_of, collection_s


def _ingest_pending(store_root: str, runner: str) -> int:
    """Import ledger rows a killed run left behind (so a resume can plan)."""
    d = policy.latest_ledger_dir(store_root)
    if not d:
        return 0
    ledger = os.path.join(d, "ledger.jsonl")
    marker = os.path.join(d, "ledger.consumed")
    offset = int(open(marker).read() or 0) if os.path.exists(marker) else 0
    st = store_mod.Store(store_root)
    run_id = st.start_run(runner)
    n = st.ingest_ledger(ledger, run_id, st.ctx_id(), runner, offset=offset)
    st.refresh_aggregates()
    st.finish_run(run_id, total_ms=0.0, wall_ms=0.0)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(str(os.path.getsize(ledger)))
    return n


def build_plan(root: str, runner: str = "unittest", store_root: str = ".",
               resume: bool = False, budget_s: float | None = None) -> dict:
    """plan step: measure (or record) -> one budget."""
    meter = Meter()
    st = store_mod.Store(store_root)
    if resume:
        with meter.span("resume_ingest"):
            _ingest_pending(store_root, runner)
    # Measurements belong to a runner. A jest row says nothing about a pytest
    # suite, so a runner's first run must still be a record pass even when the
    # same repository has already been measured by another runner.
    prefix = f"{runner}::"
    measured = {k for k in st.agg_map("test") if k.startswith(prefix)}
    if not measured:
        plan = policy.record_plan(checkpoint_dir=policy.new_checkpoint_dir(store_root),
                                  budget_s=budget_s or policy.RECORD_BUDGET_S)
        plan.overhead_ms = meter.total_ms
        return json.loads(plan.to_json())

    keys, raw_of, collected_s = collect_manifest(root, runner, meter, store_root)
    est = estimator.estimate(keys, st)
    plan = policy.make_plan(est, raw_of,
                            checkpoint_dir=policy.new_checkpoint_dir(store_root),
                            startup_s=collected_s)
    if budget_s:
        # the caller's own timeout wins (it is what the surrounding process
        # will enforce); the plan records whether that was enough
        plan.total_budget_s = float(budget_s)
        plan.shards[0].budget_s = float(budget_s)
    plan.manifest_size = len(keys)
    plan.measured_tests = sum(1 for k in keys if k in measured)
    plan.overhead_ms = meter.total_ms
    return json.loads(plan.to_json())


def execute_plan(plan: dict, root: str, runner: str = "unittest",
                 store_root: str = ".", log=lambda *a: None) -> dict:
    """execute + record step."""
    from .runner import execute, done_map
    st = store_mod.Store(store_root)
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    before = done_map(ledger)
    stats = execute(plan, os.path.abspath(root), runner=runner, log=log)
    after = done_map(ledger)
    new = set(after) - set(before)

    run_id = st.start_run(runner)
    n = st.ingest_ledger(ledger, run_id, st.ctx_id(), runner)
    st.refresh_aggregates()
    st.finish_run(run_id, total_ms=sum(after[k] for k in new) + stats["startup_s"] * 1000,
                  wall_ms=stats["wall_s"] * 1000, attempts=stats["attempts"])
    return {"stats": stats, "ledger": ledger, "ingested": n,
            "wall_s": round(stats["wall_s"], 3),
            "budget_s": plan["total_budget_s"]}


def run_tests(root: str, runner: str = "unittest", budget_s: float | None = None,
              resume: bool = False, store_root: str = ".") -> dict:
    """One call = plan -> execute -> record -> the timeout answer."""
    t0 = time.perf_counter()
    plan = build_plan(root, runner=runner, store_root=store_root,
                      resume=resume, budget_s=budget_s)
    res = execute_plan(plan, root, runner=runner, store_root=store_root)
    stats = res["stats"]
    record_pass = plan["operating_level"] == "record"
    st = store_mod.Store(store_root)
    known = len(st.agg_map("test"))

    if stats["error"]:
        status = "shard_failed"
    elif stats["failed_tests"]:
        status = "incomplete"
    elif record_pass:
        status = "ok" if stats["suite_complete"] else "incomplete"
    else:
        status = "ok"

    if record_pass:
        answer = {"basis": "record", "budget_s": plan["total_budget_s"],
                  "measured_tests": 0, "confidence": 0.0}
    else:
        answer = {"basis": "measured", "budget_s": plan["total_budget_s"],
                  "p50_s": plan["est"].get("p50_s"),
                  "p90_s": plan["est"].get("p90_s"),
                  "coverage": plan["est"].get("coverage"),
                  "measured_tests": plan["measured_tests"],
                  "confidence": round(min(1.0, known / max(plan["manifest_size"], 1)), 3)}

    out = {
        "status": status,
        "runner": runner,
        "operating_level": plan["operating_level"],
        "budget": answer,
        "wall_s": res["wall_s"],
        "attempts": stats["attempts"],
        "timed_out": stats["timed_out"],
        "wasted_s": round(stats["wasted_s"], 3),
        "resumed_tests": stats["resumed"],
        "startup_s": round(stats["startup_s"], 3),
        "unresolved": stats["unresolved"],
        "predicted_wall_s": plan.get("predicted_wall_s"),
        "tests": len(plan["shards"][0]["tests"]) if not record_pass else None,
        "recorded": res["ingested"],
        "known_tests": known,
        "next_call_hint": ("Recorded this suite without predicting anything. "
                           "The next call's budget is measured."
                           if record_pass else
                           "Budget measured from this store; re-run to keep it current."),
        "total_tool_latency_s": round(time.perf_counter() - t0, 3),
    }
    if stats["error"]:
        out["error"] = stats["error"]
    return out


def handle_tool_call(name: str, arguments: dict | str) -> str:
    """What an agent framework's executor actually calls: name + JSON -> JSON."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments.strip() else {}
    if name != "run_tests":
        return json.dumps({"error": f"unknown tool {name!r}", "known": ["run_tests"]})
    try:
        out = run_tests(**arguments)
    except TypeError as e:
        return json.dumps({"error": f"bad arguments: {e}",
                           "spec": TOOL_SPEC["parameters"]["properties"]})
    return json.dumps(out, indent=1)


if __name__ == "__main__":                      # manual smoke
    print(handle_tool_call("run_tests", {"root": sys.argv[1]}))
