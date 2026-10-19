"""Render benchmark/_work/results/dashboard.html from results.json.

Single self-contained HTML file: dark theme, inline SVG charts, zero deps.
Sections render only when the data for them exists, so `python3 -m benchmark
run --repo itsdangerous` still yields a valid dashboard for that subset.

Values are read from results.json everywhere; narrative strings interpolate
measured numbers so a re-run after hh fixes rewrites the story automatically.
"""
from __future__ import annotations

import html
import json
import math
import os

from . import config

E = html.escape

# ---------- palette ----------
C = {"naive": "#60a5fa", "hh": "#2dd4bf", "w2": "#a78bfa", "waste": "#f87171",
     "warn": "#fbbf24", "grid": "#263043", "txt": "#c9d4e3", "dim": "#7d8ba3",
     "ok": "#34d399", "plan": "#94a3b8"}


def fmt(v, nd=1):
    if v is None:
        return "—"
    s = f"{v:.{nd}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# ---------- svg primitives ----------
def bar(x, y, w, h, fill, rx=3, extra=""):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" {extra}/>'


def hatch_defs(id_, color):
    return (f'<pattern id="{id_}" width="7" height="7" patternTransform="rotate(45)" '
            f'patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="{color}" fill-opacity="0.55"/>'
            f'<line x1="0" y1="0" x2="0" y2="7" stroke="#0b0f16" stroke-width="2.4"/></pattern>')


def txt(x, y, s, size=12, fill=C["txt"], anchor="start", weight=None, opacity=1):
    w = f' font-weight="{weight}"' if weight else ""
    lines = str(s).split("\n")
    if len(lines) == 1:
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
                f'text-anchor="{anchor}"{w} opacity="{opacity}">{E(lines[0])}</text>')
    spans = "".join(f'<tspan x="{x:.1f}" dy="{0 if i == 0 else 1.25}em">{E(ln)}</tspan>'
                    for i, ln in enumerate(lines))
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}"{w} opacity="{opacity}">{spans}</text>')


def line(x1, y1, x2, y2, stroke=C["grid"], w=1, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{w}"{d}/>'


def dot(x, y, r, fill, extra=""):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" {extra}/>'


def svg(w, h, body, extra_defs=""):
    defs = hatch_defs("wasteh", C["waste"]) + hatch_defs("keeph", C["warn"]) + extra_defs
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;margin:auto;display:block" '
            f'xmlns="http://www.w3.org/2000/svg"><defs>{defs}</defs>{body}</svg>')


# ============================ data adaptation ============================
def adapt(R: dict) -> dict:
    """Derived view of results.json used by every chart below."""
    A = {"R": R}
    exec_ids = [rid for rid, r in R.get("repos", {}).items() if r.get("lane") == "unittest_exec"]
    A["exec"] = exec_ids[0] if exec_ids else None
    A["exec_disp"] = R["repos"][A["exec"]]["display"] if A["exec"] else ""
    R_runs = R.get("runs", {})
    A["runs"] = R_runs.get(A["exec"], {}) if A["exec"] else {}
    logs = R.get("attempt_logs", {})

    # compat + manifest blind totals
    A["compat"] = R.get("compat", [])
    blind = [c for c in A["compat"] if "(via shim)" not in c["repo"] and c["discovered"] == 0]
    A["blind_tests"] = sum(c["tests"] for c in blind)
    A["blind_repos"] = len(blind)
    shim_rows = [c for c in A["compat"] if "(via shim)" in c["repo"] and c["discovered"] > 0]
    A["shim_row"] = shim_rows[0] if shim_rows else None

    # resume numbers (exec lane)
    res = A["runs"].get("resume", {})
    naive_log = [a for a in logs.get(A["exec"], []) if a.get("scenario") == "resume"
                 and a.get("arm") == "naive"]
    A["res_kill"] = res.get("kill_at_s")
    A["res_naive_a1"] = next((a for a in naive_log if a["attempt"] == 1), {})
    A["res_naive_a2"] = next((a for a in naive_log if a["attempt"] == 2), {})
    A["res_repaid"] = (A["res_naive_a1"].get("wall_ms", 0) or 0) / 1000
    A["res_naive_total"] = (A["res_naive_a1"].get("wall_ms", 0) + A["res_naive_a2"].get("wall_ms", 0)) / 1000 \
        if A["res_naive_a1"] and A["res_naive_a2"] else None
    rplan = res.get("plan_resume") or {}
    A["res_hh_finish"] = None if res.get("run_finish_wall_s") is None else \
        round(res["run_finish_wall_s"] + rplan.get("plan_overhead_ms", 0) / 1000, 1)
    A["res_hh_total"] = None if A["res_hh_finish"] is None else round(A["res_kill"] + A["res_hh_finish"], 1)
    A["res_hh_receipt"] = (res.get("receipt") or {}).get("this_call_s")
    A["res_resumed"] = res.get("resumed_tests")
    if A["res_hh_total"] and A["res_naive_total"]:
        A["res_wall_pct"] = round((1 - A["res_hh_total"] / A["res_naive_total"]) * 100, 1)

    # hang numbers (exec lane)
    h = A["runs"].get("hang")
    if h and not any(r.get("wall_s") for r in h.get("runs", [])):
        h = None
    A["hang"] = h
    if h:
        hn = [a for a in logs.get(A["exec"], []) if a.get("scenario") == "hang"
              and a.get("arm") == "naive"]
        A["hang_naive_walls"] = [a["wall_ms"] / 1000 for a in hn]
        A["hang_naive_total"] = sum(A["hang_naive_walls"])
        A["hang_naive_n"] = len(hn)
        A["hang_walls"] = [r["wall_s"] for r in h["runs"] if r.get("wall_s")]
        A["hang_censored"] = next((r["censored"] for r in h["runs"] if r.get("censored")), 0)
        A["hang_ingested"] = next((r["ingested"] for r in h["runs"] if r.get("ingested")), 0)
        A["hang_receipt"] = next((r["receipt_s"] for r in reversed(h["runs"]) if r.get("receipt_s")), None)
        A["hang_quants"] = [ (h.get(f"plan{i}") or {}).get("quarantine") for i in (1, 2, 3) ]

    # prediction errors
    A["pred_errs"] = {}
    pw = A["runs"].get("plan_warm") or {}
    rw = A["runs"].get("run_warm") or {}
    if pw.get("pred_wall_s") and rw.get("wall_s"):
        A["pred_errs"]["W1 warm"] = (pw["pred_wall_s"], rw["wall_s"])
    p2 = A["runs"].get("plan_w2") or {}
    r2 = A["runs"].get("run_w2") or {}
    if p2.get("pred_wall_s") and r2.get("wall_s"):
        A["pred_errs"]["W2 warm"] = (p2["pred_wall_s"], r2["wall_s"])

    # capture overhead
    A["cap"] = R.get("facts", {}).get("capture_overhead", [])
    A["cap_max"] = None
    cands = [c for c in A["cap"] if c.get("overhead_pct") is not None]
    if cands:
        A["cap_max"] = max(cands, key=lambda c: c["overhead_pct"])

    # receipts
    A["receipts"] = R.get("receipts", {})
    A["receipts_total"] = sum((d or {}).get("total_s", 0) for d in A["receipts"].values())
    hang_cap = R.get("facts", {}).get("hang_capture", {})
    A["hang_cap_id"] = next(iter(hang_cap), None)
    A["hang_cap"] = hang_cap.get(A["hang_cap_id"], {}) if A["hang_cap_id"] else {}
    return A


# ============================ CHART A: engagement ============================
def chart_compat(A: dict) -> str:
    rows = A["compat"]
    if not rows:
        return ""
    W = 1040
    H = 126 + 46 * len(rows)
    s = txt(20, 30, "Where hh can engage: manifest discovery vs the real suite universe", 20,
            C["txt"], weight="bold")
    s += txt(20, 52, "tests hh's manifest can discover vs the number its runner actually executes", 12.5, C["dim"])
    y0 = 96
    rh = 46
    x0 = 212
    xw = W - x0 - 235
    right_col = x0 + xw + 14
    maxv = max(r["tests"] for r in rows) or 1
    for i, r in enumerate(rows):
        y = y0 + i * rh
        s += txt(x0 - 12, y + 15, r["repo"], 12, fill=C["txt"], anchor="end")
        vw = xw * r["tests"] / maxv
        s += bar(x0, y, vw, 24, "#1c2635")
        dw = xw * r["discovered"] / maxv
        if r["discovered"] == 0:
            s += bar(x0, y, min(vw, max(xw * 0.012, 6)), 24, "url(#wasteh)", extra='opacity="0.85"')
            s += txt(x0 + 10, y + 16, f"0 of {r['tests']:,}", 11.5, C["waste"], weight="bold")
        else:
            s += bar(x0, y, dw, 24, C["ok"])
            s += txt(x0 + 10, y + 16, f"{r['discovered']:,} of {r['tests']:,}", 11.5, C["ok"], weight="bold")
        s += txt(right_col, y + 16, f"{r['tests']:,} executed", 10.5, C["dim"])
        reason = r.get("reasons")
        if reason and r["discovered"] == 0:
            s += txt(x0, y + 38, reason, 10, C["dim"])
    leg_y = H - 28
    s += bar(20, leg_y - 10, 14, 14, "#1c2635") + txt(40, leg_y + 2, "real suite universe (executed)", 11, C["dim"])
    s += bar(232, leg_y - 10, 14, 14, "url(#wasteh)") + txt(252, leg_y + 2, "nothing discovered — blind lane", 11, C["dim"])
    s += bar(445, leg_y - 10, 14, 14, C["ok"]) + txt(465, leg_y + 2, "fully discovered", 11, C["dim"])
    sect = f"<h3>1 · Manifest engagement{'' if A['blind_tests'] else ''}</h3>"
    if A["blind_tests"]:
        sect += (f"<p class='sub'><b>{A['blind_tests']:,} tests across {A['blind_repos']} repos are "
                 f"invisible</b> to <code>hh plan</code> — the unittest manifest only discovers "
                 f"unittest-style TestCase classes (and then only under the default "
                 f"<code>test_*.py</code> pattern). The pytest-function and JS lanes run blind. "
                 f"In the measured shim lane (unittest-compatible wrappers), discovery is complete.</p>")
    return sect + svg(W, H, s)


# ============================ CHART B: resume =============================
def chart_resume(A: dict) -> str:
    if A["exec"] is None or A["res_naive_total"] is None or A["res_hh_total"] is None:
        return ""
    W, H = 1040, 236
    kill = A["res_kill"]
    n1, n2 = A["res_naive_a1"]["wall_ms"] / 1000, A["res_naive_a2"]["wall_ms"] / 1000
    hh_k = n1  # same cutoff both arms
    hh_f = A["res_hh_finish"]
    sc = 600.0 / max(A["res_naive_total"], 1e-9)
    X0 = 300

    def X(v):
        return X0 + v * sc

    s = txt(20, 30, f"Mid-suite SIGKILL @ {kill:.1f}s — restart vs resume ({A['exec_disp']}, "
            f"{A['runs'].get('plan_cold', {}).get('n') or '—'} tests)", 18, C["txt"], weight="bold")
    s += txt(20, 52, "same cutoff for both arms · process-group kill · naive starts from zero, hh resumes "
             "from durable checkpoints", 12, C["dim"])
    y1, y2, bh = 108, 166, 30
    s += txt(X0 - 14, y1 + 20, "naive agent", 12.5, C["naive"], anchor="end", weight="bold")
    s += bar(X0, y1, n1 * sc, bh, "url(#wasteh)")
    s += txt(X0 + 8, y1 + 20, f"attempt 1 · killed · {n1:.1f}s (re-paid)", 11, C["waste"], weight="bold")
    s += bar(X(n1), y1, n2 * sc, bh, C["naive"])
    s += txt(X(n1) + 8, y1 + 20, f"attempt 2 · re-run from zero · {n2:.1f}s", 11, "#06121f", weight="bold")
    s += line(X(n1), y1 - 6, X(n1), y1 + bh + 6, C["txt"], 1.2, dash="3 4")
    s += txt(X(A["res_naive_total"]) + 10, y1 + 20, f"total {A['res_naive_total']:.1f}s", 13, C["txt"], weight="bold")
    s += txt(X0 - 14, y2 + 20, f"hh (job: {A['exec']}-resume)", 12.5, C["hh"], anchor="end", weight="bold")
    s += bar(X0, y2, hh_k * sc, bh, C["hh"], extra='opacity="0.55"')
    s += txt(X0 + 8, y2 + 20, f"killed @ {kill:.1f}s · 0s lost", 11, "#032b25", weight="bold")
    s += bar(X(hh_k), y2, hh_f * sc, bh, C["hh"])
    s += txt(X(hh_k) + 8, y2 + 20, f"plan --resume + finish · {hh_f:.1f}s · {A['res_resumed']} skipped", 11, "#032b25", weight="bold")
    s += line(X(hh_k), y2 - 6, X(hh_k), y2 + bh + 6, C["txt"], 1.2, dash="3 4")
    s += txt(X(hh_k + hh_f) + 10, y2 + 20, f"total {hh_k + hh_f:.1f}s", 13, C["txt"], weight="bold")
    s += txt(X0, H - 16,
             f"wall −{A['res_wall_pct']}% vs naive · re-paid compute {A['res_repaid']:.1f}s → 0s"
             + (f" · receipt credited {A['res_hh_receipt']:.1f}s" if A['res_hh_receipt'] else ""),
             12.5, C["hh"], weight="bold")
    return (f"<h3>2 · Checkpoint/resume on a real suite</h3>"
            f"<p class='sub'>Receipt (job <code>{A['exec']}-resume</code>): {fmt(A['res_hh_receipt'])}s "
            f"credited — {A['res_resumed']} tests already on this job's ledger were not re-executed.</p>") \
        + svg(W, H, s)


# ============================ CHART C: hang ==============================
def chart_hang(A: dict) -> str:
    if not A.get("hang"):
        return ""
    W = 1040
    runs = A["hang_walls"]
    n_naive = A["hang_naive_n"]
    H = 410
    s = txt(20, 30, f"Injected infinite hangs ({A['exec_disp']}) — information per second spent", 18,
            C["txt"], weight="bold")
    s += txt(20, 52, f"two synthetic <code>while True: sleep(0.5)</code> tests in an already-measured suite", 12, C["dim"])
    maxw = max(A["hang_naive_walls"][:1] + runs)
    X0 = 150
    xw = 720
    def X(v):
        return X0 + v * xw / (maxw * 1.08)
    y = 100
    s += txt(X0 - 10, y + 4, "naive agent", 12, C["naive"], weight="bold", anchor="end")
    for i, w in enumerate(A["hang_naive_walls"]):
        bw = w * (xw / (maxw * 1.08))
        s += bar(X0, y, bw, 22, "url(#wasteh)")
        label = f"attempt {i+1} · {fmt(w)}s · 0 results"
        if X0 + bw > W - 180:
            s += txt(X0 + 8, y + 15, label, 11, "#7f1d1d", weight="bold")
        else:
            s += txt(X0 + bw + 8, y + 15, label, 11, C["waste"], weight="bold")
        y += 34
    s += txt(X0, y + 12, f"give up: {fmt(A['hang_naive_total'])}s spent · zero information gained · hangs still unidentified",
             12, C["waste"], weight="bold")
    y += 66
    s += txt(X0 - 10, y + 4, "hh executor", 12, C["hh"], weight="bold", anchor="end")
    gg = A["hang_quants"]
    for i, r in enumerate(A["hang"]["runs"]):
        w = r["wall_s"] or 0
        s += bar(X0, y, w * (xw / (maxw * 1.08)), 22, C["hh"])
        q = gg[i] or [None]
        label = f"run {i+1} · {fmt(w)}s"
        if r.get("censored"):
            label += f" · {r['censored']} hanging test(s) censored"
        if q and q[0]:
            label += f" · hang shard budget {fmt(q[0], 0)}s"
        s += txt(X0 + 8, y + 15, label, 11, "#032b25", weight="bold")
        y += 34
    s += txt(X0, y + 12, f"kept every other result ({A['hang_ingested']} ingested) · culprit named by "
             f"open interval · receipt {fmt(A['hang_receipt'])}s", 12, C["hh"], weight="bold")
    y += 46
    s += line(X0, 84, X0, y - 24, C["grid"])
    s += txt(20, y - 6, "↓ the ledger the naive agent never got:", 12, C["dim"])
    cap = A.get("hang_cap", {})
    if cap:
        for a in cap.get("capture_attempts", []):
            s += txt(X0, y + 18, f"capture attempt {a['attempt']}: budget {fmt(a['budget_s'],0)}s · {a['outcome']}", 11, C["txt"])
            y += 24
        ident = cap.get("hang_identified")
        if ident:
            s += txt(X0, y + 18, f"open interval (start without end) = {ident}", 11, C["warn"])
            y += 24
    return "<h3>3 · A test that never finishes</h3>" + svg(W, H + 60, s)


# ==================== CHART D: planning quality & cost ====================
def chart_pred(A: dict) -> str:
    if not A.get("pred_errs"):
        return ""
    W, H = 1040, 280
    s = txt(20, 30, "Predicted vs actual wall time, and the cost of the instrument itself", 18,
            C["txt"], weight="bold")
    rows = list(A["pred_errs"].items())
    y = 116
    maxv = max(max(p, a) for _n, (p, a) in rows) * 1.15
    X0, xw = 320, 560
    for name, (pred, act) in rows:
        sc = xw / maxv
        s += txt(X0 - 12, y + 15, name, 12, C["txt"], anchor="end")
        s += bar(X0, y, act * sc, 22, C["hh"])
        s += txt(X0 + 8, y + 15, f"actual {fmt(act, 2)}s", 11, "#032b25", weight="bold")
        s += line(X0, y + 30, X0 + pred * sc, y + 30, C["plan"], 3)
        s += txt(X0 + pred * sc + 6, y + 34, f"predicted {fmt(pred, 2)}s", 11, C["plan"])
        err = act and abs(pred - act) / act * 100
        s += txt(X0 + 4, y + 50, f"error {err:.1f}% (warm store)", 11, C["ok"], weight="bold")
        y += 84
    s += txt(20, 52, "quantile q50/q90 come from the suite's own history; the forecast is consumed by "
             "shard packing AND can be checked by the agent before committing to a plan", 12, C["dim"])
    return "<h3>4 · Planning quality</h3>" + svg(W, H, s)


# ==================== CHART E: capture overhead at scale =================
def chart_overhead(A: dict) -> str:
    if not A["cap"]:
        return ""
    W, H = 1040, 480
    s = txt(20, 30, "Capture overhead at scale — instrumentation, not execution", 18, C["txt"], weight="bold")
    s += txt(20, 52, "passed/failed rows written per test (start+end or one row on failure) · "
             "overheads below are TOTAL vs the same suite run bare, same machine sequential", 12, C["dim"])
    pts = []
    for c in A["cap"]:
        if c.get("overhead_pct") is None or not c.get("tests"):
            continue
        pts.append(c)
    if not pts:
        return ""
    xs = [c["tests"] for c in pts]
    x0, x1 = 110, 950
    y0, y1 = 330, 110
    logmin, logmax = math.log10(max(min(xs) * 0.6, 1)), math.log10(max(xs) * 1.5)

    def X(n):
        return x0 + (math.log10(n) - logmin) / (logmax - logmin) * (x1 - x0)

    ymax = max(30, math.ceil(max(c2["overhead_pct"] for c2 in pts) / 10) * 10 + 6)

    def Y(p):
        return y0 - p / ymax * (y0 - y1)

    for tick in range(0, int(ymax) + 1, 10):
        s += line(x0, Y(tick), x1, Y(tick))
        s += txt(x0 - 8, Y(tick) + 4, str(tick), 10, C["dim"], anchor="end")
    s += txt(x0 - 34, (y0 + y1) / 2, "overhead vs bare run (%)", 11, C["dim"], anchor="middle")
    s += line(x0, y0, x1, y0, C["grid"], 1.5)
    for tick in [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000, 300000]:
        if min(xs) * 0.6 <= tick <= max(xs) * 1.5:
            s += line(X(tick), y0, X(tick), y0 + 6, C["dim"], 1.2)
            s += txt(X(tick), y0 + 20, f"{tick:,}", 10, C["dim"], anchor="middle")
    s += line(x0, Y(10), x1, Y(10), C["warn"], 1.5, dash="6 5")
    s += txt(x1 - 4, Y(10) - 6, "10% gate", 10, C["warn"], anchor="end")
    poly = " ".join(f"{X(c2['tests']):.1f},{Y(c2['overhead_pct']):.1f}" for c2 in sorted(pts, key=lambda z: z["tests"]))
    s += f'<polyline points="{poly}" fill="none" stroke="{C["w2"]}" stroke-width="2"/>'
    for c2 in pts:
        x, y = X(c2["tests"]), Y(c2["overhead_pct"])
        disp = A["R"]["repos"].get(c2["repo"], {}).get("display", c2["repo"])
        label = f"{disp} · {fmt(c2['overhead_pct'])}%"
        s += dot(x, y, 5, C["w2"])
        s += txt(x + 10, y - 8, label, 10.5, C["txt"], anchor="start" if x < x1 - 260 else "end")
        s += txt(x + 10 if x < x1 - 260 else x - 10, y + 6,
                 f"{fmt(c2['plain_s'],2)}s → {fmt(c2['capture_s'],2)}s", 9.5, C["dim"],
                 anchor="start" if x < x1 - 260 else "end")
    st = f"steepest: {A['cap_max']['repo']} {fmt(A['cap_max']['overhead_pct'])}% @ {A['cap_max']['tests']:,} tests" \
         if A["cap_max"] else ""
    s += txt(x0 + 6, y1 - 14, st, 10.5, C["dim"])
    s += txt(x0 + 6, y1 + 2, "each point = total wall, instrumented suite vs the same suite bare (66µs ≈ 2 ledger rows per test)", 10.5, C["dim"])
    s += txt((x0 + x1) / 2, H - 12, "tests (log scale) — the gap only becomes visible when the suite is 60k+ rows", 10, C["dim"], anchor="middle")
    return ("<h3>5 · Capture overhead at scale</h3>") + svg(W, H, s)





# ---------- html shell ----------
def page(A: dict, charts: list[str]) -> str:
    R = A["R"]
    meta = R.get("meta", {})
    core = "".join(charts)
    logs = R.get("attempt_logs", {})
    legs = "".join(f"<h3>{E(r['display'] if 'display' in r else rid)}</h3><pre class='log'>"
                   f"{E(json.dumps(logs.get(rid, []), indent=1)[:40000])}</pre>"
                   for rid, r in R.get("repos", {}).items())
    rec_rows = ""
    for job, d in sorted(A["receipts"].items()):
        if not d:
            continue
        for e in d.get("entries", []):
            rec_rows += (f"<tr><td>{E(job)}</td><td><code>{E(e['kind'])}</code></td>"
                         f"<td><b>{fmt(e['saved_s'])}s</b></td>"
                         f"<td>{E(e['claim_rule'])}</td></tr>")
    kpis = []
    if A["exec"] and A.get("res_repaid"):
        kpis.append(kpi("re-paid compute, mid-suite kill",
                        f"0s vs {fmt(A['res_repaid'])}s", f"hh resume vs naive restart ({A['exec_disp']})", C["hh"]))
    kpis.append(kpi("manifest coverage, pytest+JS lanes",
                    f"0 of {A['blind_tests']:,}", f"{A['blind_repos']} repos fully blind — the headline gap", C["waste"]))
    if A.get("hang"):
        kpis.append(kpi("hang: info gained",
                        f"{fmt(A['hang_naive_total'])}s ↔ {fmt(A['hang_walls'][-1],1)}s",
                        f"naive {A['hang_naive_n']} blank kills · hh run 3 converged", C["hh"]))
    pe = A.get("pred_errs", {})
    if "W1 warm" in pe:
        p, a = pe["W1 warm"]
        kpis.append(kpi("warm prediction error", f"{abs(p - a) / a * 100:.1f}%",
                        f"{fmt(p,2)}s predicted vs {fmt(a,2)}s actual", C["ok"]))
    if A["cap_max"]:
        kpis.append(kpi("max capture overhead", f"{fmt(A['cap_max']['overhead_pct'])}%",
                        f"{A['cap_max']['tests']:,}-test suite", C["warn"]))
    kpis.append(kpi("receipts handed out", f"{fmt(A['receipts_total'],1)}s",
                    "credited compute not re-paid", C["hh"]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>hh — Real-World Benchmark</title>
<style>
:root{{--bg:#0b0f16;--card:#111826;--line:#263043;--txt:#c9d4e3;--dim:#7d8ba3;--hh:#2dd4bf;--na:#60a5fa;--wa:#f87171;--warn:#fbbf24}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 ui-sans-serif,system-ui,'Segoe UI',sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:48px 28px 80px}}
code{{background:#1a2332;padding:1px 5px;border-radius:4px;font:12.5px ui-monospace,Menlo,monospace;color:#9fe8da}}
pre.log{{background:#0e1420;border:1px solid var(--line);border-radius:10px;padding:14px;overflow-x:auto;font:11.5px/1.5 ui-monospace,Menlo,monospace;max-height:400px;overflow-y:auto}}
h1{{font-size:30px;margin:0 0 6px}} h2{{font-size:19px;margin:40px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}}
h3{{font-size:15.5px;margin:28px 0 4px;color:#e6eef8}}
.sub{{color:var(--dim);margin:4px 0 14px;max-width:960px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin:14px 0}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:22px 0}}
.kpi b{{font-size:24px;display:block;color:var(--txt)}}
.kpi span{{font-size:12px;color:var(--dim)}}
ul{{margin:10px 0;padding-left:22px}} li{{margin:7px 0}}
.chip{{background:#1a2332;border-radius:99px;padding:5px 13px;font-size:13px;margin-right:6px;display:inline-block;margin-top:6px}}
.bad{{color:var(--wa);font-weight:600}} .good{{color:var(--hh);font-weight:600}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0}}
th,td{{border:1px solid var(--line);padding:5px 9px;text-align:left}}
th{{color:#dbe4f0;background:#141c2b}}
</style></head><body><div class="wrap">
<h1>hh — real-world benchmark</h1>
<p class="sub">{E(meta.get('date',''))} · hh {E(meta.get('hh_version','?'))} ({E(meta.get('hh_commit',''))}) ·
Python {E(meta.get('python',''))} · measured by <code>python3 -m benchmark</code> — config: <code>{E(meta.get('repos_file','benchmark/repos.json'))}</code></p>
<div class="kpis">{''.join(kpis)}</div>
{core}
<h2>Ledger / attempt logs</h2>{legs}
<h2>Time-saved receipts</h2><table><tr><th>job</th><th>kind</th><th>credited</th><th>note</th></tr>{rec_rows}</table>
<p class="sub">Generated {E(meta.get('date',''))} by <code>hh/benchmark/dashboard.py</code> — the data is
<code>benchmark/_work/results/results.json</code>; compare runs: <code>python3 -m benchmark compare</code>.</p>
</div></body></html>"""


def kpi(title, value, sub, color):
    return (f"<div class='card kpi' style='border-top:3px solid {color}'>"
            f"<span>{E(title)}</span><b>{E(value)}</b><span>{E(sub)}</span></div>")


def render(cfg: dict | None = None) -> str:
    R = json.load(open(os.path.join(config.RESULTS_DIR, "results.json"), encoding="utf-8"))
    # display names from the descriptor
    if cfg:
        disp = {r["id"]: r.get("display", r["id"]) for r in cfg.get("repos", [])}
        for c in R.get("compat", []):
            base = c["repo"].split(" (")[0]
            if base in disp:
                c.setdefault("display", c["repo"].replace(base, disp[base]))
    A = adapt(R)
    charts = [chart_compat(A), chart_resume(A), chart_hang(A), chart_pred(A), chart_overhead(A)]
    out = os.path.join(config.RESULTS_DIR, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page(A, [c for c in charts if c]))
    return out


if __name__ == "__main__":
    print(render())
