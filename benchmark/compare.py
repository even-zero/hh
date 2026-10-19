"""Compare a fresh results.json against a frozen baseline (benchmark/baseline/).

The workflow: ship a bug in hh one release at a time, re-run
`python3 -m benchmark run && collect`, then `compare` — the delta table shows
exactly which metric moved (and in which direction) without re-measuring the
old code.
"""
from __future__ import annotations

import json
import math
import os

from . import config

DEFAULT_BASELINE = os.path.join(config.HERE, "baseline", "v1.0.0-2026-10-19.json")

# metric paths -> (display name, direction, unit)
# direction: -1 lower is better, +1 higher is better, 0 informational
METRICS = [
    ("compat.discovered_total", "tests discovered (total)", +1, "n"),
    ("compat.blind_tests", "tests invisible to manifest", -1, "n"),
    ("exec.plain_s", "clean suite wall (naive pass)", 0, "s"),
    ("exec.hh_warm_wall", "hh warm run wall", -1, "s"),
    ("exec.hh_warm_exec_wall", "hh warm run wall (collection excluded)", -1, "s"),
    ("exec.exec_overhead_pct", "hh executor overhead, collection excluded", -1, "%"),
    ("exec.hh_warm_overhead_ms", "hh warm self-metered overhead", -1, "ms"),
    ("exec.pred_err_warm_pct", "W1 warm prediction error", -1, "%"),
    ("exec.pred_err_w2_pct", "W2 warm prediction error", -1, "%"),
    ("exec.resume_naive_total_s", "resume scenario: naive total", -1, "s"),
    ("exec.resume_hh_total_s", "resume scenario: hh total", -1, "s"),
    ("exec.resume_repaid_s", "resume scenario: compute re-paid (naive)", -1, "s"),
    ("exec.resume_receipt_s", "resume receipt credited", +1, "s"),
    ("exec.hang_naive_total_s", "hang scenario: naive total (all kills)", -1, "s"),
    ("exec.hang_run3_s", "hang scenario: hh steady-state wall", -1, "s"),
    ("exec.hang_receipt_s", "hang continuation receipt", +1, "s"),
    ("cap.*.overhead_pct", "capture overhead @N tests", -1, "%"),
    ("cap.*.overhead_pct_exec", "capture overhead, collection excluded", -1, "%"),
    ("cap.*.tests", "capture suite size", 0, "n"),
    ("receipts.total_s", "receipts handed out (gross)", +1, "s"),
    ("receipts.net_s", "receipts net of tool overhead", +1, "s"),
]


def flatten(results: dict) -> dict:
    if "repos" not in results and results.get("runs"):
        return flatten_legacy(results)   # e.g. the frozen v1.0.0 publication snapshot
    f = {}
    comp = results.get("compat", [])
    blind = [c for c in comp if "(via shim)" not in c["repo"] and c["discovered"] == 0]
    f["compat.blind_tests"] = sum(c["tests"] for c in blind)
    f["compat.discovered_total"] = sum(c["discovered"] for c in comp if "(via shim)" not in c["repo"])
    exec_ids = [rid for rid, r in results.get("repos", {}).items() if r.get("lane") == "unittest_exec"]
    if exec_ids:
        rr = results.get("runs", {}).get(exec_ids[0], {})
        logs = results.get("attempt_logs", {}).get(exec_ids[0], [])
        plain = next((a for a in logs if a.get("policy") == "naive-default"
                      and a.get("scenario") == "clean" and a["outcome"] == "ok"), None)
        f["exec.plain_s"] = (plain or {}).get("wall_ms", 0) / 1000 or None
        rw = rr.get("run_warm") or {}
        f["exec.hh_warm_wall"] = rw.get("wall_s")
        f["exec.hh_warm_exec_wall"] = rw.get("exec_wall_s")
        f["exec.hh_warm_overhead_ms"] = rw.get("overhead_ms")
        pw = rr.get("plan_warm") or {}
        if pw.get("pred_wall_s") and rw.get("wall_s"):
            f["exec.pred_err_warm_pct"] = round(abs(pw["pred_wall_s"] - rw["wall_s"]) / rw["wall_s"] * 100, 2)
        p2, r2 = rr.get("plan_w2") or {}, rr.get("run_w2") or {}
        if p2.get("pred_wall_s") and r2.get("wall_s"):
            f["exec.pred_err_w2_pct"] = round(abs(p2["pred_wall_s"] - r2["wall_s"]) / r2["wall_s"] * 100, 2)
        res = rr.get("resume") or {}
        nl = [a for a in logs if a.get("scenario") == "resume" and a.get("arm") == "naive"]
        if len(nl) >= 2:
            tot = (nl[0]["wall_ms"] + nl[1]["wall_ms"]) / 1000
            f["exec.resume_naive_total_s"] = round(tot, 1)
            f["exec.resume_repaid_s"] = round(nl[0]["wall_ms"] / 1000, 1)
            rp = res.get("plan_resume") or {}
            if res.get("run_finish_wall_s") is not None:
                f["exec.resume_hh_total_s"] = round((nl[0]["wall_ms"] / 1000)
                                                    + res["run_finish_wall_s"]
                                                    + rp.get("plan_overhead_ms", 0) / 1000, 1)
        f["exec.resume_receipt_s"] = (res.get("receipt") or {}).get("this_call_s")
        h = rr.get("hang") or {}
        hn = [a for a in logs if a.get("scenario") == "hang" and a.get("arm") == "naive"]
        if hn:
            f["exec.hang_naive_total_s"] = round(sum(a["wall_ms"] for a in hn) / 1000, 1)
        if h.get("runs"):
            walls = [x["wall_s"] for x in h["runs"] if x.get("wall_s")]
            if walls:
                f["exec.hang_run3_s"] = walls[-1]
            rec = next((x["receipt_s"] for x in reversed(h["runs"]) if x.get("receipt_s")), None)
            f["exec.hang_receipt_s"] = rec
    for c in results.get("facts", {}).get("capture_overhead", []):
        # A full executor includes collection/process startup; do not present
        # it as a like-for-like capture-only overhead regression.
        if c.get("tests") and not c.get("label"):
            f[f"cap.{c['repo']}.tests"] = c["tests"]
            f[f"cap.{c['repo']}.overhead_pct"] = c.get("overhead_pct")
            if c.get("overhead_pct_exec") is not None:
                f[f"cap.{c['repo']}.overhead_pct_exec"] = c["overhead_pct_exec"]
        elif c.get("label") and c.get("overhead_pct_exec") is not None and exec_ids:
            if c["repo"] == exec_ids[0]:
                f["exec.exec_overhead_pct"] = c["overhead_pct_exec"]
    tot = sum((d or {}).get("total_s", 0) for d in results.get("receipts", {}).values())
    f["receipts.total_s"] = tot or None
    net = 0.0
    seen = False
    for rr in results.get("runs", {}).values():
        for phase in ("cold", "warm"):
            n = (rr.get(f"run_{phase}") or {}).get("net_s")
            if n is not None:
                net += n
                seen = True
    f["receipts.net_s"] = round(net, 1) if seen else None
    return f


def flatten_legacy(R: dict) -> dict:
    """Adapter for the original publication results.json (before benchmark/ existed).

    Maps the first-run schema (runs.tornado.*, attempt_logs keyed by store name,
    facts.<repo>_capture dicts) onto the same flattened metric namespace.
    The file at benchmark/baseline/v1.0.0-2026-10-19.json is that schema.
    """
    f = {}
    comp = R.get("compat", [])
    blind = [c for c in comp if "(via shim)" not in c["repo"] and c["discovered"] == 0]
    f["compat.blind_tests"] = sum(c["tests"] for c in blind)
    f["compat.discovered_total"] = sum(c["discovered"] for c in comp if "(via shim)" not in c["repo"])
    logs = R.get("attempt_logs", {})
    t = R.get("runs", {}).get("tornado", {})
    naive_clean = [a for a in logs.get("tornado-naive", [])
                   if a["policy"].startswith("naive") and a["outcome"] == "ok"]
    if naive_clean:
        f["exec.plain_s"] = round(naive_clean[0]["wall_ms"] / 1000, 2)
    rw, pw = t.get("run_warm", {}), t.get("plan_warm", {})
    f["exec.hh_warm_wall"] = rw.get("wall_s")
    f["exec.hh_warm_overhead_ms"] = rw.get("overhead_ms")
    if pw.get("pred_wall_s") and rw.get("wall_s"):
        f["exec.pred_err_warm_pct"] = round(abs(pw["pred_wall_s"] - rw["wall_s"]) / rw["wall_s"] * 100, 2)
    p2, r2 = t.get("plan_w2", {}), t.get("run_w2", {})
    if p2.get("pred_wall_s") and r2.get("wall_s"):
        f["exec.pred_err_w2_pct"] = round(abs(p2["pred_wall_s"] - r2["wall_s"]) / r2["wall_s"] * 100, 2)
    rn = logs.get("tornado-resume-naive", [])
    if rn:
        f["exec.resume_naive_total_s"] = round(sum(a["wall_ms"] for a in rn) / 1000, 1)
        f["exec.resume_repaid_s"] = round(rn[0]["wall_ms"] / 1000, 1)
        res = t.get("resume", {})
        rp = res.get("plan_resume2", {})
        if res.get("run_finish_wall_s"):
            f["exec.resume_hh_total_s"] = round(rn[0]["wall_ms"] / 1000
                                                + res["run_finish_wall_s"]
                                                + rp.get("plan_overhead_ms", 0) / 1000, 1)
    recs = R.get("receipts", {})
    f["exec.resume_receipt_s"] = (recs.get("tornado-resume") or {}).get("total_s")
    hn = logs.get("tornado-hang-naive", [])
    if hn:
        f["exec.hang_naive_total_s"] = round(sum(a["wall_ms"] for a in hn) / 1000, 1)
    h = t.get("hang", {})
    if h.get("run3"):
        f["exec.hang_run3_s"] = h["run3"].get("wall_s")
    f["exec.hang_receipt_s"] = (recs.get("tornado-hang") or {}).get("total_s")
    facts = R.get("facts", {})
    for rid, fname in (("packaging", "packaging_capture"), ("itsdangerous", "itsdangerous_capture"),
                       ("markupsafe", "markupsafe_capture"), ("chai", "chai_capture")):
        d = facts.get(fname) or {}
        if rid == "packaging":
            pct, tests = d.get("overhead_pct_of_plain"), d.get("store_tests_tracked")
        else:
            plain, cap_s = d.get("plain_s"), d.get("capture_s")
            pct = (cap_s / plain - 1) * 100 if plain and cap_s else None
            pct = round(pct, 1) if pct is not None else None
            tests = d.get("tests")
        f[f"cap.{rid}.overhead_pct"] = pct
        f[f"cap.{rid}.tests"] = tests
    to = facts.get("tornado_overhead") or {}
    if f.get("exec.plain_s") and rw.get("wall_s"):
        f["cap.tornado.tests"] = pw.get("n") or (t.get("plan_cold", {}) or {}).get("n")
        f["cap.tornado.overhead_pct"] = round(
            ((rw["wall_s"] + pw.get("plan_overhead_ms", 0) / 1000) / f["exec.plain_s"] - 1) * 100, 2)
    tot = sum((d or {}).get("total_s", 0) for d in recs.values())
    f["receipts.total_s"] = round(tot, 1) or None
    return f


def compare(baseline_path: str = DEFAULT_BASELINE,
            current_path: str | None = None, out_md: str | None = None) -> str:
    base = flatten(json.load(open(baseline_path)))
    cur = flatten(json.load(open(current_path or os.path.join(config.RESULTS_DIR, "results.json"))))
    keys = sorted(set(base) | set(cur))
    rows = [f"# benchmark delta — {os.path.basename(baseline_path)} vs current run", "",
            "| metric | baseline | current | Δ | verdict |", "|---|---:|---:|---:|---|"]
    better = worse = 0
    direction_of = {path: direction for path, _, direction, _ in METRICS
                    if "*" not in path}
    for k in keys:
        b, c = base.get(k), cur.get(k)
        d = (-1 if k.startswith("cap.") and k.endswith(".overhead_pct")
             else 0 if k.startswith("cap.") else direction_of.get(k, 0))
        if b is None or c is None:
            verdict = "—"
        elif not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            verdict = "—"
        else:
            delta = c - b
            if abs(delta) < 0.05 * max(abs(b), 1e-9):
                verdict = "· same"
            elif d == 0:
                verdict = "~ moved"
            elif (delta < 0) == (d < 0):
                verdict = "✅ better"
                better += 1
            else:
                verdict = "⚠️ worse"
                worse += 1
        rows.append(f"| {k} | {_f(b)} | {_f(c)} | {_d(b, c)} | {verdict} |")
    rows += ["", f"**{better} improved, {worse} regressed, "
             f"{len(keys) - better - worse} unchanged/unknown.**", "",
             "_Generated by `python3 -m benchmark compare`._"]
    md = "\n".join(rows)
    with open(out_md or os.path.join(config.RESULTS_DIR, "compare.md"), "w") as f:
        f.write(md)
    return md


def _f(v):
    return "—" if v is None else (f"{v:,.1f}" if isinstance(v, float) and abs(v) < 100000
                                  else f"{v:,}")


def _d(b, c):
    if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
        return "—"
    d = c - b
    return ("+" if d >= 0 else "") + f"{d:,.1f}"


if __name__ == "__main__":
    print(compare())
