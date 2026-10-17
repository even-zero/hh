"""Checkpointed shard executor (ARCHITECTURE §4, §9).

Runs shards as subprocesses against a streaming adapter. Per-test ledger rows are
the checkpoints: a killed shard loses nothing already recorded. Escalation ladder
replaces "double the limit and restart":
  1. extend shard budget x1.3 (p95-ish), resume from ledger
  2. adaptive re-budget from the observed per-test rate
  3. quarantine the remainder one test per process with a hard cutoff
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _done_keys(ledger: str) -> set[str]:
    done = set()
    if not os.path.exists(ledger):
        return done
    with open(ledger, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") == "end":
                done.add(row["key"])
    return done


def _open_starts(ledger: str) -> dict[str, dict]:
    starts: dict[str, dict] = {}
    if not os.path.exists(ledger):
        return starts
    with open(ledger, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("type") == "start":
                starts[row["key"]] = row
            elif row.get("type") == "end":
                starts.pop(row["key"], None)
    return starts


def _append(ledger: str, row: dict) -> None:
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()


def _mark_victim_censored(ledger: str, remaining: list[dict], kill_ts: float) -> bool:
    """The in-flight test (unmatched start row) is right-censored at the kill."""
    starts = _open_starts(ledger)
    for t in remaining:
        s = starts.get(t["key"])
        if s:
            _append(ledger, {
                "type": "end", "key": t["key"], "raw_id": t["raw_id"],
                "status": "timeout", "censored": True,
                "duration_ms": round((kill_ts - s["ts"]) * 1000.0, 3),
                "ts": time.time(),
            })
            return True
    return False


def _cmd(adapter: str, root: str, ledger: str, tests: list[str]) -> list[str]:
    return [sys.executable, "-m", adapter, "--root", root, "--ledger", ledger,
            "--tests", *tests]


def _run(cmd: list[str], timeout: float, env: dict) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True, env=env)
        return p.returncode == 0, time.perf_counter() - t0, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - t0, "TIMEOUT"


def _done_map(ledger: str) -> dict:
    """key -> duration_ms for every end-row in the ledger (incl. resumed work)."""
    m: dict[str, float] = {}
    if not os.path.exists(ledger):
        return m
    with open(ledger, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "end" and row.get("key"):
                m[row["key"]] = float(row.get("duration_ms", 0.0) or 0.0)
    return m


def execute(plan: dict, root: str, adapter: str = "hh.adapters.unittest_stream",
            log=print) -> dict:
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    os.makedirs(plan["checkpoint_dir"], exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    cutoff_default = float(plan.get("per_test_cutoff_s", 12.0))
    hang_cutoff = float(plan.get("hang_cutoff_s", 2.0))

    stats = {"attempts": 0, "wall_s": 0.0, "compute_s": 0.0,
             "censored": 0, "shard_failures": 0,
             # time-saved instrument (hh/savings.py)
             "resumed": 0, "resumed_ms": 0.0,
             "preserved_tests": 0, "preserved_ms": 0.0}
    import threading
    lock = threading.Lock()

    def bump(k: str, v: float = 1.0) -> None:
        with lock:
            stats[k] += v

    def run_shard(si: int, shard: dict) -> None:
        done0 = _done_map(ledger)
        tests = [t for t in shard["tests"] if t["key"] not in done0]
        skipped = [t for t in shard["tests"] if t["key"] in done0]
        if skipped:                      # resume credit: paid once, in this job
            bump("resumed", len(skipped))
            bump("resumed_ms", sum(done0[t["key"]] for t in skipped))
        if not tests:
            return
        credited: set[str] = set()
        budget = float(shard["budget_s"])
        quarantine = bool(shard.get("quarantine"))
        ext = 0
        while tests:
            bump("attempts")
            ok, wall, out = _run(
                _cmd(adapter, root, ledger, [t["raw_id"] for t in tests]),
                budget, env,
            )
            bump("wall_s", wall)
            bump("compute_s", wall)
            done = _done_keys(ledger)
            rem = [t for t in tests if t["key"] not in done]
            if not rem:
                log(f"  shard {si}: {len(tests)} tests done in {wall:.1f}s")
                break
            if not ok and wall < budget * 0.999:
                # process exited early (crash/import error): record remainder as errors
                for t in rem:
                    _append(ledger, {
                        "type": "end", "key": t["key"], "raw_id": t["raw_id"],
                        "status": "error", "censored": False,
                        "duration_ms": 0.0, "ts": time.time(),
                    })
                bump("shard_failures")
                log(f"  shard {si}: adapter exited early, {len(rem)} marked error")
                break
            # timed out mid-shard: censor the in-flight test
            kill_ts = time.time()
            if _mark_victim_censored(ledger, rem, kill_ts):
                bump("censored")
            done = _done_keys(ledger)
            rem = [t for t in rem if t["key"] not in done]
            if not rem:
                break
            log(f"  shard {si}: timeout@{budget:.1f}s, {len(rem)} left", )
            if not quarantine:
                # continuation credit: work this shard already finished (and that
                # the extension will therefore NOT re-run). Credited incrementally
                # so repeated extensions never double-count.
                done1 = _done_map(ledger)
                fresh = [t for t in tests
                         if t["key"] in done1 and t["key"] not in credited]
                if fresh:
                    bump("preserved_tests", len(fresh))
                    bump("preserved_ms", sum(done1[t["key"]] for t in fresh))
                    credited.update(t["key"] for t in fresh)
            if quarantine:
                break  # quarantine shards never escalate (fail-fast probe)
            if ext == 0:
                budget = max(budget * 1.3, 5.0)
                ext = 1
                log(f"    escalate: extend budget → {budget:.1f}s, resume")
            elif ext == 1:
                rate = max(wall / max(len(tests) - len(rem), 1), 0.001)
                budget = min(rate * len(rem) * 1.4 + 2.0, 600.0)
                ext = 2
                log(f"    escalate: adaptive re-budget → {budget:.1f}s")
            else:
                log(f"    escalate: quarantine {len(rem)} tests")
                for t in rem:
                    bump("attempts")
                    q_budget = hang_cutoff if t.get("hang_tier", 0) >= 2 else cutoff_default
                    ok2, wall2, _ = _run(
                        _cmd(adapter, root, ledger, [t["raw_id"]]), q_budget, env)
                    bump("wall_s", wall2)
                    bump("compute_s", wall2)
                    if t["key"] not in _done_keys(ledger):
                        _append(ledger, {
                            "type": "end", "key": t["key"], "raw_id": t["raw_id"],
                            "status": "timeout", "censored": True,
                            "duration_ms": round(wall2 * 1000, 3), "ts": time.time(),
                        })
                        bump("censored")
                break

    # All shards are independent by construction (disjoint test sets): hang
    # probes and normal shards alike run on W workers in parallel (§7.3
    # parallelism / S_bal). Ledger appends touch disjoint keys per shard;
    # single-line O_APPEND writes do not interleave.
    all_shards = list(enumerate(plan["shards"]))
    if all_shards:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(all_shards),
                                               int(plan.get("workers_hint") or 2))) as ex:
            list(ex.map(lambda t: run_shard(*t), all_shards))
    return stats
