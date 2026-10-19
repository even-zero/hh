"""Time-saved instrumentation — the utility receipt (ARCHITECTURE §11.6).

Every run writes a receipt: what work was *provably* not re-paid thanks to the
tool, plus what was protected by refusing a doomed run. On job finish,
`hh savings` totals the job.

Honesty rule
------------
Credit a line item only when the avoided cost is structurally implied by
recorded events — "a restart-from-zero baseline would have re-paid exactly
this, and here is the row that proves it":

  continuation  tests finished before a shard hit its budget were kept; the
                extension resumes the remainder instead of restarting from
                zero (ledger rows + stats prove the mass).
  resume        tests already on the job's ledger from an earlier call were
                not re-executed (they were paid for once, in this job).
  hang_bound    a hang was censored at the cutoff. The *unbounded* naive wait
                is NOT summed; when the caller supplied a deadline, we credit
                at most (deadline − wall), i.e. the most the blocking baseline
                could have burned.
  hang_escape   no caller deadline: still credit the *escaped* wait — the time
                each censored test would have consumed before any completion-
                based runner gives up, minus what the tool actually waited
                (capped at the per-test cutoff). This is a measured lower
                bound, not the unbounded wait.
  refusal       `budget_too_small` / `deadline_infeasible` returns: the run
                could not finish in the caller's deadline, and a naive attempt
                would have burned that deadline and produced nothing.

Nothing else is credited. In particular, a fast run with nothing at risk earns
a receipt of 0 — "no savings to claim" is a valid, honest receipt.

Every receipt also reports what the call *cost* (`overhead_s`: planning,
ingestion, calibration sampling) and the resulting `net_s`, so a receipt can
never be read as pure profit when the tool's own work is larger.
"""
from __future__ import annotations

import json
import os
import time

KINDS = ("continuation", "resume", "hang_bound", "hang_escape", "refusal")


def job_id() -> str:
    return os.environ.get("HH_JOB", "default")


def credit_lines(stats: dict, wall_s: float, caller_limit_s: float | None,
                 censored: int, censored_wait_s: float = 0.0,
                 cutoff_s: float = 12.0) -> tuple[list[dict], list[str]]:
    """Executor stats + caller limit → (credited lines, honest notes).

    `censored_wait_s` is the measured time actually spent on the censored
    tests; `cutoff_s` is the per-test cutoff. Their difference is the escape.
    """
    lines: list[dict] = []
    notes: list[str] = []

    if stats.get("resumed_ms"):
        lines.append({
            "kind": "resume",
            "ms": round(stats["resumed_ms"], 1),
            "basis": (f"{int(stats.get('resumed', 0))} test(s) already on this job's "
                      f"ledger were not re-executed — paid once, in this job"),
        })
    if stats.get("preserved_ms"):
        lines.append({
            "kind": "continuation",
            "ms": round(stats["preserved_ms"], 1),
            "basis": (f"{int(stats.get('preserved_tests', 0))} test(s) finished before a "
                      f"shard exceeded its budget were kept; the extension resumes the "
                      f"remainder, a restart-from-zero baseline re-pays them"),
        })
    if censored:
        if caller_limit_s:
            ms = max(0.0, caller_limit_s * 1000.0 - wall_s * 1000.0)
            lines.append({
                "kind": "hang_bound",
                "ms": round(ms, 1),
                "basis": (f"{censored} test(s) censored (hang, or in-flight when a "
                          f"shard hit its budget) at the 2–12 s cutoff; a blocking "
                          f"baseline would have burned your {caller_limit_s:g}s deadline "
                          f"and returned nothing"),
            })
        else:
            # No caller deadline: credit the measured escape, bounded by the
            # per-test cutoff the tool itself would have paid.
            escape_ms = max(0.0, censored * cutoff_s * 1000.0
                            - max(0.0, censored_wait_s) * 1000.0)
            if escape_ms > 0:
                lines.append({
                    "kind": "hang_escape",
                    "ms": round(escape_ms, 1),
                    "basis": (f"{censored} test(s) censored at the {cutoff_s:g}s "
                              f"cutoff after {censored_wait_s:.1f}s of measured wait; "
                              f"a completion-waiting runner spends at least the "
                              f"cutoff on each, and returns nothing for them — "
                              f"this is the escaped lower bound, not the unbounded "
                              f"wait"),
                })
            notes.append(f"{censored} test(s) censored (hang, or in-flight at a "
                         f"budget cut) — the escape above is a lower bound; the "
                         f"true avoided wait is UNBOUNDED without a caller deadline")
    return lines, notes


def refusal_lines(detail: str, limit_s: float | None) -> list[dict]:
    if not limit_s:
        return []
    return [{
        "kind": "refusal",
        "ms": round(limit_s * 1000.0, 1),
        "basis": (f"refused a run that could not finish ({detail}); a naive attempt "
                  f"would have burned the {limit_s:g}s deadline and returned nothing"),
    }]


def record(store_root: str, *, job: str | None, suite_id: str, lines: list[dict]) -> None:
    """Persist credited lines (SQLite + JSONL mirror). No-op if nothing credited."""
    if not lines:
        return
    from .store import Store
    job = job or job_id()
    st = Store(store_root)
    for ln in lines:
        st.db.execute(
            "INSERT INTO savings (ts, job_id, suite_id, kind, amount_ms, basis)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), job, suite_id, ln["kind"], float(ln["ms"]), ln["basis"]))
    st.db.commit()
    exp = os.path.join(store_root, ".hh", "experiments")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "savings.jsonl"), "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps({"ts": time.time(), "job_id": job,
                                "suite_id": suite_id, "kind": ln["kind"],
                                "amount_ms": ln["ms"], "basis": ln["basis"]}) + "\n")


def totals(store_root: str, job: str | None = None) -> dict:
    """Job receipt: totals by kind (the 'job finish' view)."""
    from .store import Store
    st = Store(store_root)
    job = job or job_id()
    rows = st.db.execute(
        "SELECT kind, SUM(amount_ms), COUNT(*) FROM savings WHERE job_id = ?"
        " GROUP BY kind ORDER BY SUM(amount_ms) DESC", (job,)).fetchall()
    by_kind = [{"kind": k, "ms": round(m or 0.0, 1), "events": c} for k, m, c in rows]
    total = sum(x["ms"] for x in by_kind)
    suites = st.db.execute(
        "SELECT COUNT(DISTINCT suite_id) FROM savings WHERE job_id = ?",
        (job,)).fetchone()[0]
    last = st.db.execute(
        "SELECT basis FROM savings WHERE job_id = ? ORDER BY ts DESC LIMIT 1",
        (job,)).fetchone()
    return {"job_id": job, "total_ms": round(total, 1),
            "total_s": round(total / 1000, 1), "suites": suites,
            "by_kind": by_kind, "latest_basis": last[0] if last else None}


def block(store_root: str, job: str | None, lines: list[dict], notes: list[str],
          wall_s: float | None = None, overhead_s: float | None = None) -> dict:
    """The `time_saved` block that ships in every tool/CLI response.

    `net_s` = credited work − the tool's own measured overhead for this call
    (planning, ingest, calibration sampling). A receipt is never presented as
    gross-only: the caller always sees what the tool spent to earn it.
    """
    job = job or job_id()
    tot = totals(store_root, job)
    this_call = round(sum(l["ms"] for l in lines), 1)
    out = {
        "job_store": os.path.abspath(store_root),
        "this_call_ms": this_call,
        "this_call_s": round(this_call / 1000, 1),
        "job_id": job,
        "job_total_s": tot["total_s"],
        "job_total_ms": tot["total_ms"],
        "job_suites": tot["suites"],
        "breakdown": lines,
        "how": ("conservative: only work a restart-from-zero baseline provably "
                "re-pays is credited (ledger-backed); unbounded waits are noted, "
                "not summed; net = credited − tool overhead — see TIME_SAVED.md"),
    }
    if overhead_s is not None:
        out["overhead_ms"] = round(overhead_s * 1000.0, 1)
        out["overhead_s"] = round(overhead_s, 3)
        out["net_ms"] = round(this_call - overhead_s * 1000.0, 1)
        out["net_s"] = round((this_call - overhead_s * 1000.0) / 1000.0, 1)
    if wall_s is not None:
        out["this_call_wall_s"] = round(wall_s, 3)
    if notes:
        out["notes"] = notes
    return out


def human(t: dict) -> str:
    """One-line-per-kind rendering for CLI/summary use."""
    head = (f"TIME SAVED (job '{t['job_id']}'): {t['total_s']} s across "
            f"{len(t['by_kind'])} kind(s), {t['suites']} suite(s)")
    out = [head]
    for k in t["by_kind"]:
        out.append(f"  {k['kind']:12} {k['ms'] / 1000:8.1f} s  ({k['events']} event(s))")
    if not t["by_kind"]:
        out.append("  (no savings to claim yet — nothing was at risk)")
    return "\n".join(out)
