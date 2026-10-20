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


def _mark_victim_censored(ledger: str, remaining: list[dict], kill_ts: float) -> float | None:
    """The in-flight test (unmatched start row) is right-censored at the kill.

    Returns how long that test had been running (s), or None when no victim was
    found. The duration is the *escaped* wait baseline: it is what the tool
    stopped paying at the cutoff.
    """
    starts = _open_starts(ledger)
    for t in remaining:
        s = starts.get(t["key"])
        if s:
            dur_ms = round((kill_ts - s["ts"]) * 1000.0, 3)
            _append(ledger, {
                "type": "end", "key": t["key"], "raw_id": t["raw_id"],
                "status": "timeout", "censored": True,
                "duration_ms": dur_ms,
                "ts": time.time(),
            })
            return dur_ms / 1000.0
    return None


def _cmd(adapter: str, root: str, ledger: str, tests_file: str | None) -> list[str]:
    """Build the adapter command; ``tests_file=None`` runs the whole suite.

    A cold record pass uses that form: the adapter discovers and executes in a
    single process, exactly like the repo's naive command, and the only addition
    is the ledger.
    """
    if adapter == "hh.adapters.mocha_stream":
        script = os.path.join(os.path.dirname(__file__), "adapters", "mocha_stream.cjs")
        cmd = ["node", script, "run", root, ledger]
        return cmd + ([tests_file] if tests_file else [])
    cmd = [sys.executable, "-m", adapter, "--root", root, "--ledger", ledger]
    return cmd + (["--tests-file", tests_file] if tests_file else [])


def _selection_file(checkpoint: str, si: int, tests: list[dict]) -> str:
    # A path per shard (not a 60k-ID argv); atomically replace for retries.
    path = os.path.join(checkpoint, f"shard-{si}.jsonl")
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        for t in tests:
            f.write(json.dumps({"raw_id": t["raw_id"], "key": t["key"]}) + "\n")
    os.replace(temp, path)
    return path


def _run(cmd: list[str], timeout: float, env: dict) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)  # share caller group: external SIGKILL kills both
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return False, time.perf_counter() - t0, "TIMEOUT"
    return p.returncode == 0, time.perf_counter() - t0, (out + err)[-2000:]


def _first_row_ts(ledger: str, offset: int) -> float | None:
    """Timestamp of the first ledger row written after byte `offset`."""
    try:
        with open(ledger, "r", encoding="utf-8") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts")
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


def _has_suite_end(ledger: str, offset: int = 0) -> bool:
    """True when a runner process appended its completeness marker.

    Only a process that reached the end of its suite writes `suite_end`; a
    killed or crashed one does not. That is what lets a cold record pass say
    "the ledger is the manifest" without trusting a half-finished run.
    """
    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                if suite_end_marker(line):
                    return True
    except OSError:
        return False
    return False


def suite_end_marker(line: str) -> bool:
    line = line.strip()
    if not line or "suite_end" not in line:
        return False
    try:
        return json.loads(line).get("type") == "suite_end"
    except ValueError:
        return False


def execute(plan: dict, root: str, adapter: str = "hh.adapters.unittest_stream",
            log=print) -> dict:
    ledger = os.path.join(plan["checkpoint_dir"], "ledger.jsonl")
    os.makedirs(plan["checkpoint_dir"], exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    cutoff_default = float(plan.get("per_test_cutoff_s", 12.0))
    hang_cutoff = float(plan.get("hang_cutoff_s", 2.0))
    # Collection/startup is paid per runner process, not by the tests. Shard
    # budgets are execution budgets, so every attempt's clock gets startup added
    # on top and the ladder never charges collection to the test budget.
    startup_s = float(plan.get("startup_s", 0.0) or 0.0)
    stats = {"attempts": 0, "wall_s": 0.0, "compute_s": 0.0,
             "censored": 0, "shard_failures": 0, "incomplete": 0, "errors": [],
             # time-saved instrument (hh/savings.py)
             "resumed": 0, "resumed_ms": 0.0,
             "preserved_tests": 0, "preserved_ms": 0.0,
             # accounting: runner startup (collection) vs test execution
             "startup_s": 0.0, "startup_attempts": 0,
             "censored_wait_s": 0.0}
    import threading
    lock = threading.Lock()
    st_full = {"suite_complete": False}   # set by a record shard that finished

    def bump(k: str, v: float = 1.0) -> None:
        with lock:
            stats[k] += v

    def run_shard(si: int, shard: dict) -> None:
        full = bool(shard.get("full_suite"))
        done0 = _done_map(ledger)
        tests = [] if full else [t for t in shard["tests"] if t["key"] not in done0]
        skipped = [] if full else [t for t in shard["tests"] if t["key"] in done0]
        if skipped:                      # resume credit: paid once, in this job
            bump("resumed", len(skipped))
            bump("resumed_ms", sum(done0[t["key"]] for t in skipped))
        if not tests and not full:
            return
        credited: set[str] = set()
        budget = float(shard["budget_s"])
        quarantine = bool(shard.get("quarantine"))
        ext = 0
        while tests or full:
            bump("attempts")
            # budget is exec-only; the runner also pays startup/collection
            off = _size(ledger)
            t0 = time.time()
            sel = None if full else _selection_file(plan["checkpoint_dir"], si, tests)
            ok, wall, out = _run(_cmd(adapter, root, ledger, sel),
                                 budget + startup_s, env)
            bump("wall_s", wall)
            bump("compute_s", wall)
            # Measure the runner's real startup/collection: it is the gap
            # between process start and the first test's ledger row. Reusing the
            # plan's constant charged a full-suite collection to a 40-row
            # sample that never collected the suite (and inflated exec time).
            first_ts = _first_row_ts(ledger, off)
            startup_meas = 0.0 if first_ts is None else min(max(0.0, first_ts - t0), wall)
            if startup_meas:
                bump("startup_s", startup_meas)
                bump("startup_attempts")
            # A full-suite shard runs under a wall-clock budget (naive
            # semantics: discovery included); an enumerated shard gets its exec
            # budget plus the runner startup the executor added to the clock.
            limit = budget if full else budget + startup_meas
            timed_out = (not ok) and wall >= limit * 0.999
            if full:
                if _has_suite_end(ledger, off):
                    st_full["suite_complete"] = True
                    log(f"  shard {si}: recorded the full suite in {wall:.1f}s")
                    break
                # A record pass has no predictions to escalate from: mirror the
                # naive ladder (30 → 60 → 120 s) and then stop, like naive.
                if timed_out:
                    ext += 1
                    if ext <= 2:
                        budget = min(max(budget * 2.0, 60.0), 1200.0)
                        log(f"    record: naive ladder → {budget:.0f}s, retry")
                        continue
                    bump("incomplete")
                    log(f"  shard {si}: record pass unfinished "
                        f"(naive would give up here)")
                    break
                # No marker and no timeout: the runner died on its own (import
                # error, crash). Never let that pass for a finished suite.
                bump("shard_failures")
                tail = (out or "").strip().splitlines()[-6:]
                with lock:
                    stats["errors"].append({"shard": si, "wall_s": round(wall, 3),
                                            "log": tail})
                log(f"  shard {si}: adapter exited before finishing the suite: "
                    + (tail[-1] if tail else "no output"))
                break
            done = _done_keys(ledger)
            rem = [t for t in tests if t["key"] not in done]
            if not rem:
                log(f"  shard {si}: {len(tests)} tests done in {wall:.1f}s")
                break
            if not ok and wall < (budget + startup_meas) * 0.999:
                # process exited early (crash/import error): record remainder as errors
                for t in rem:
                    _append(ledger, {
                        "type": "end", "key": t["key"], "raw_id": t["raw_id"],
                        "status": "error", "censored": False,
                        "duration_ms": 0.0, "ts": time.time(),
                    })
                bump("shard_failures")
                tail = (out or "").strip().splitlines()[-6:]
                with lock:
                    stats["errors"].append({"shard": si, "wall_s": round(wall, 3),
                                            "log": tail})
                log(f"  shard {si}: adapter exited early, {len(rem)} marked error: "
                    + (tail[-1] if tail else "no output"))
                break
            # timed out mid-shard: censor the in-flight test
            kill_ts = time.time()
            victim_wait = _mark_victim_censored(ledger, rem, kill_ts)
            if victim_wait is not None:
                bump("censored")
                bump("censored_wait_s", victim_wait)
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
                rate = max((wall - startup_meas) / max(len(tests) - len(rem), 1), 0.001)
                budget = min(rate * len(rem) * 1.4 + 2.0, 600.0)
                ext = 2
                log(f"    escalate: adaptive re-budget → {budget:.1f}s")
            else:
                log(f"    escalate: quarantine {len(rem)} tests")
                for t in rem:
                    bump("attempts")
                    q_budget = hang_cutoff if t.get("hang_tier", 0) >= 2 else cutoff_default
                    off2 = _size(ledger)
                    t2 = time.time()
                    ok2, wall2, _ = _run(
                        _cmd(adapter, root, ledger, _selection_file(plan["checkpoint_dir"], si, [t])),
                        q_budget + startup_s, env)
                    bump("wall_s", wall2)
                    bump("compute_s", wall2)
                    f2 = _first_row_ts(ledger, off2)
                    st2 = 0.0 if f2 is None else min(max(0.0, f2 - t2), wall2)
                    if st2:
                        bump("startup_s", st2)
                        bump("startup_attempts")
                    bump("censored_wait_s", max(0.0, wall2 - st2))
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
    stats["suite_complete"] = bool(st_full["suite_complete"])
    return stats
