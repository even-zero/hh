"""Execution harness: runs commands, enforces budgets with process-group
SIGKILL (grandchildren die too), saves every machine-readable artifact, and
writes one records.jsonl row per executed step so collect.py never has to
re-derive what happened.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

from . import config


def now_record(**kw) -> None:
    config.work_env()
    kw.setdefault("ts", time.time())
    with open(config.RECORDS, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw) + "\n")


def kill_run(cmd: list[str], budget_s: float, *, cwd: str, env: dict,
             repo: str, scenario: str, arm: str, attempt: int, policy: str,
             store_root: str | None, log_name: str | None = None) -> dict:
    """Run cmd; SIGKILL the whole process group at budget_s (harness kill).

    Writes a wrap-compatible attempt row into store_root/.hh/experiments/
    attempt_log.jsonl (same instrument the naive policy is scored with) and a
    records.jsonl row. stdout/stderr are captured to a log file.
    """
    log_path = None
    out_f = subprocess.DEVNULL
    if log_name:
        log_path = os.path.join(config.logdir(repo), log_name)
        out_f = open(log_path, "w", encoding="utf-8")   # audit only; arms may not read it
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True,
                         stdout=out_f, stderr=subprocess.STDOUT)
    timed_out = False
    try:
        rc = p.wait(timeout=budget_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(p.pid, signal.SIGKILL)
        rc = p.wait()
    finally:
        if log_path:
            out_f.close()
    wall = time.perf_counter() - t0
    rec = {"policy": policy, "suite_id": repo, "attempt": attempt,
           "budget_s": float(budget_s),
           "outcome": "timeout" if timed_out else ("ok" if rc == 0 else "failed"),
           "wall_ms": round(wall * 1000, 1),
           "wasted_ms": round(wall * 1000, 1) if timed_out else 0.0,
           "overhead_ms": 0.0}
    if store_root:
        d = os.path.join(store_root, ".hh", "experiments")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "attempt_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **rec}) + "\n")
    row = {"repo": repo, "scenario": scenario, "arm": arm, "attempt": attempt,
           "kind": "kill_run", "cmd": cmd, "log": log_path, **rec}
    now_record(**row)
    return rec


def run_and_save(cmd: list[str], *, cwd: str, env: dict, repo: str, scenario: str,
                 arm: str, attempt: int, kind: str, budget_s: float | None = None,
                 log_name: str | None = None, keep_json: bool = True) -> tuple[dict | None, float, str]:
    """Run cmd (no kill), time it, save stdout (JSON) into the repo's log dir,
    append a records.jsonl row. Returns (parsed_json|None, wall_s, raw_tail)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=budget_s)
        wall = time.perf_counter() - t0
        timed_out, rc = False, p.returncode
        raw = p.stdout
        tail = (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        wall = time.perf_counter() - t0
        timed_out, rc, raw, tail = True, -9, "", "TIMEOUT"
    parsed = None
    if keep_json:
        try:
            parsed = json.loads(raw.strip().splitlines()[-1])
        except Exception:
            parsed = None
    log_path = None
    if log_name:
        log_path = os.path.join(config.logdir(repo), log_name)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(raw if raw else tail)
    now_record(repo=repo, scenario=scenario, arm=arm, attempt=attempt, kind=kind,
               budget_s=budget_s, outcome=("timeout" if timed_out else
                                            ("ok" if rc == 0 else "failed")),
               wall_ms=round(wall * 1000, 1), json_ok=parsed is not None,
               log=log_path, cmd=cmd)
    return parsed, wall, tail


def ledger_rows(path: str) -> dict:
    """Count row types and find open intervals (start without end = hang signature)."""
    import collections
    starts, ends = {}, collections.Counter()
    types = collections.Counter()
    if not os.path.exists(path):
        return {"rows": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("type")
            types[t] += 1
            if t == "start":
                starts[r["key"]] = r.get("raw_id", r["key"])
            elif t == "end":
                ends[r["key"]] += 1
    open_ = [v for k, v in starts.items() if ends.get(k, 0) < 1]
    return {"rows": sum(types.values()), "start": types.get("start", 0),
            "end": types.get("end", 0), "open_intervals": open_,
            "status": dict(collections.Counter())}


def read_attempt_log(store_root: str) -> list[dict]:
    p = os.path.join(store_root, ".hh", "experiments", "attempt_log.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
