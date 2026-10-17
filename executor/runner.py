"""The executor: run one shard under its budget, resume instead of restarting.

A timeout is a promise about how long the suite takes. When the promise is
wrong the run is *not* thrown away: every finished test is a row in the ledger,
so the retry only pays for what is left. That property is what makes a wrong
estimate cost one budget rather than the whole suite.

The executor reports the facts the timeout metric needs:

  attempts       how many runner processes were started
  timed_out      how many were killed at the budget (SIGKILL)
  wall_s         what the caller waited
  wasted_s       time spent in attempts that were killed and superseded
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from .adapters import registry

MAX_ATTEMPTS = 3          # a resume ladder, not an infinite retry loop


# -- ledger helpers -------------------------------------------------------

def _rows(ledger: str):
    """Yield ledger rows, skipping a partial last line (a kill can split one)."""
    if not os.path.exists(ledger):
        return
    with open(ledger, encoding="utf-8", errors="replace") as f:
        yield from _rows_of(f)


def _rows_of(f):
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def done_map(ledger: str) -> dict[str, float]:
    """key -> duration_ms for every finished test in the ledger."""
    return {r["key"]: float(r.get("duration_ms") or 0.0)
            for r in _rows(ledger) if r.get("type") == "end" and r.get("key")}


def has_suite_end(ledger: str, offset: int = 0) -> bool:
    """Did a runner process reach the end of its suite?

    Only a process that finished writes this marker; a killed one does not.
    That is what lets a record pass trust its ledger as the test manifest.
    """
    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            return any(r.get("type") == "suite_end" for r in _rows_of(f))
    except OSError:
        return False


def _append(ledger: str, row: dict) -> None:
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()


def _first_row_ts(ledger: str, offset: int) -> float | None:
    """Timestamp of the first ledger row written after byte ``offset``."""
    try:
        with open(ledger, encoding="utf-8") as f:
            f.seek(offset)
            for r in _rows_of(f):
                ts = r.get("ts")
                if isinstance(ts, (int, float)):
                    return float(ts)
    except OSError:
        return None
    return None


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _selection_file(checkpoint: str, tests: list[dict]) -> str:
    path = os.path.join(checkpoint, "selection.jsonl")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for t in tests:
            f.write(json.dumps({"raw_id": t["raw_id"], "key": t["key"]}) + "\n")
    os.replace(tmp, path)
    return path


def _cmd(runner: str, root: str, ledger: str, tests_file: str | None) -> list[str]:
    """The child command for one attempt: `node <js adapter>` or `python -m ...`."""
    if registry.is_node(runner):
        cmd = ["node", registry.node_script(runner), "run", root, ledger]
        return cmd + ([tests_file] if tests_file else [])
    cmd = [sys.executable, "-m", registry.module_name(runner),
           "--root", root, "--ledger", ledger]
    return cmd + (["--tests-file", tests_file] if tests_file else [])


def _run(cmd: list[str], timeout: float, env: dict) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()                       # the SIGKILL this tool exists to avoid
        p.communicate()
        return False, time.perf_counter() - t0, "TIMEOUT"
    return p.returncode == 0, time.perf_counter() - t0, (out + err)[-2000:]


def _mark_victim(ledger: str, remaining: list[dict], kill_ts: float) -> float | None:
    """Record the in-flight test as right-censored at the kill."""
    starts: dict[str, dict] = {}
    for r in _rows(ledger):
        if r.get("type") == "start":
            starts[r["key"]] = r
        elif r.get("type") == "end":
            starts.pop(r.get("key"), None)
    for t in remaining:
        s = starts.get(t["key"])
        if s:
            dur = round(max(0.0, (kill_ts - s["ts"]) * 1000.0), 3)
            _append(ledger, {"type": "end", "key": t["key"], "raw_id": t["raw_id"],
                             "status": "timeout", "censored": True,
                             "duration_ms": dur, "ts": time.time()})
            return dur / 1000.0
    return None


# -- the executor ---------------------------------------------------------

def execute(plan: dict, root: str, runner: str = "unittest",
            log=lambda *a: None) -> dict:
    """Run the plan's single shard under its budget; resume on overrun."""
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    os.makedirs(plan["checkpoint_dir"], exist_ok=True)
    env = dict(os.environ)
    # The adapter runs in a child process out of its own cwd, so hh has to be on
    # the child's path even when it is not installed (a source checkout): the
    # package directory is the parent of this file's directory.
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join(
        [root, pkg_parent, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    shard = plan["shards"][0]
    full = bool(shard.get("full_suite"))
    startup_s = float(plan.get("startup_s") or 0.0)
    budget = float(shard["budget_s"])

    stats = {"attempts": 0, "timed_out": 0, "wall_s": 0.0, "wasted_s": 0.0,
             "startup_s": 0.0, "resumed": 0, "resumed_ms": 0.0,
             "unresolved": 0, "suite_complete": False, "failed_tests": 0,
             "error": None}

    if full:
        tests: list[dict] = []
    else:
        done = done_map(ledger)
        tests = [t for t in shard["tests"] if t["key"] not in done]
        stats["resumed"] = len(shard["tests"]) - len(tests)
        stats["resumed_ms"] = sum(done.get(t["key"], 0.0) for t in shard["tests"])

    while True:
        stats["attempts"] += 1
        off = _size(ledger)
        t0 = time.time()
        sel = None if full else _selection_file(plan["checkpoint_dir"], tests)
        ok, wall, out = _run(_cmd(runner, root, ledger, sel), budget + startup_s, env)
        stats["wall_s"] += wall
        first = _first_row_ts(ledger, off)
        if first is not None:
            stats["startup_s"] = max(stats["startup_s"],
                                     min(max(0.0, first - t0), wall))
        if full:
            if has_suite_end(ledger, off):
                stats["suite_complete"] = True
                log(f"  recorded the full suite in {wall:.1f}s")
                break
        else:
            done = done_map(ledger)
            remaining = [t for t in tests if t["key"] not in done]
            if not remaining:
                log(f"  {len(tests)} tests done in {wall:.1f}s")
                break
            if ok:
                # The runner finished cleanly but did not run some ids it was
                # given: those ids do not exist any more (a stale manifest).
                # Record them as unresolved instead of inventing a failure, and
                # do not claim the run covered them.
                for t in remaining:
                    _append(ledger, {"type": "end", "key": t["key"],
                                     "raw_id": t["raw_id"], "status": "unresolved",
                                     "censored": False, "duration_ms": 0.0,
                                     "ts": time.time()})
                stats["unresolved"] = len(remaining)
                log(f"  {len(remaining)} ids no longer resolve (recorded as unresolved)")
                break
        if wall < (budget + startup_s) * 0.999:   # died on its own (import error, crash)
            # The useful line is the thrown message, not the stack's last line
            # ("Node.js v22.22.3"); fall back to the tail when nothing says
            # "Error".
            lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
            errs = [ln for ln in lines if "Error" in ln]
            stats["error"] = (errs[:3] or lines[-3:]) or ["adapter exited early"]
            log("  adapter exited before finishing the suite")
            break
        stats["timed_out"] += 1
        if stats["attempts"] >= MAX_ATTEMPTS:
            stats["failed_tests"] = len(tests) if full else len(remaining)
            log("  budget exceeded again; stopping after %d attempts" % stats["attempts"])
            break
        # The budget was too low. Resume: finished rows are kept, the in-flight
        # test is censored at the kill, and the next budget is scaled by what
        # this attempt actually achieved.
        stats["wasted_s"] += wall
        if full:
            budget = budget * 2.0
            log(f"    killed at {budget / 2:.0f}s; resuming with {budget:.0f}s")
            continue
        _mark_victim(ledger, remaining, time.time())
        done = done_map(ledger)
        remaining = [t for t in tests if t["key"] not in done]
        if not remaining:
            break
        rate = max((wall - stats["startup_s"]) / max(len(tests) - len(remaining), 1), 0.001)
        budget = max(rate * len(remaining) * 1.3 + 1.0, budget * 1.3)
        log(f"    killed at budget; {len(remaining)} tests left, resuming with {budget:.1f}s")
        tests = remaining
    return stats
