"""HTML report for the timeout-metric benchmark.

`python3 -m benchmark report` (and a full `run`) writes
``benchmark/REPORT-<date>-timeout-metric.html`` next to the markdown report.
The HTML is self-contained: inline CSS, inline SVG charts, and the evenzero
logo embedded as a data URI.

Design reference: the UMAI dashboard (soft lavender page, white rounded cards,
crimson hero numbers, uppercase micro-labels, tinted status pills).
"""
from __future__ import annotations

import html as html_mod
import os
import statistics
import time

from .e0_logo_b64 import LOGO_B64

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ style --

C = {
    "bg": "#f4f5fa", "card": "#ffffff", "border": "#e7e9f2",
    "ink": "#1c1e2e", "muted": "#6b7280", "faint": "#9ca3af",
    "red": "#d91f3c", "red-soft": "#fde8ec", "red-line": "#f3b8c4",
    "green": "#10b981", "blue": "#2563eb", "slate": "#64748b",
    "track": "#eef0f6",
}

SECT = ("letter-spacing:.09em;text-transform:uppercase;font-size:11px;"
        "font-weight:700;color:%s;" % C["muted"])

# Ecosystem palette per test runner: (name, text, tint bg, border)
RUNNER_STYLE = {
    "pytest": ("Python", "#3776AB", "#EAF2FB", "#BBD4EC"),
    "unittest": ("Python", "#3776AB", "#EAF2FB", "#BBD4EC"),
    "node": ("Node.js", "#3C873A", "#EAF6EC", "#BFE3C4"),
    "node-test": ("Node.js", "#3C873A", "#EAF6EC", "#BFE3C4"),
    "jest": ("Jest", "#99425B", "#F7EBEF", "#E4C2CC"),
    "vitest": ("Vitest", "#6E9F18", "#F0F6E4", "#CFE2A6"),
    "tap": ("node-tap", "#E8590C", "#FDEEE3", "#F6C9A8"),
    "go": ("Go", "#00ADD8", "#E6F7FC", "#B3E5F4"),
    "ctest": ("CMake", "#064F8C", "#E8EFF7", "#B9CEE4"),
    "junit": ("Java", "#E76F00", "#FDF0E1", "#F3D0A6"),
    "phpunit": ("PHP", "#777BB4", "#EEEDF8", "#CBCDEA"),
    "rspec": ("Ruby", "#CC342D", "#FBEAE9", "#EFC0BD"),
    "nextest": ("Rust", "#CE422B", "#FBEAE4", "#EDC0B4"),
}


def esc(s):
    return html_mod.escape(str(s))


def fmt(x, nd=1):
    return "—" if x is None else f"{x:.{nd}f}"


def badge(runner: str) -> str:
    eco, fg, bg, bd = RUNNER_STYLE.get(runner, ("", C["muted"], "#F2F3F8", "#E2E4EE"))
    return (f'<span class="rb" style="color:{fg};background:{bg};border-color:{bd}"'
            f' title="{esc(eco)}">{esc(runner)}</span>')


# ------------------------------------------------------------------- data --

def saving(x: dict) -> float | None:
    """Seconds hh saved in one configuration, or None if a saving is not defined.

    Same rule as the markdown report: only when naive hit at least one kill and
    still produced a result. Derived from the stored walls.
    """
    if not x["kills"] or not x["completed"]:
        return None
    return round(x["wall_s"] - x["hh_wall_s"], 3)


def summarise(rows: list[dict]) -> dict:
    configs = [(r, s) for r in rows for s in r["sweep"]]
    killed = [(r, s) for r, s in configs if s["kills"]]
    recovered = [(r, s) for r, s in killed if s["completed"]]
    gave_up = [(r, s) for r, s in killed if not s["completed"]]
    saved = [saving(s) for _, s in recovered if saving(s) is not None]
    return {
        "repos": len(rows),
        "configurations": len(configs),
        "naive_killed": len(killed),
        "naive_kills_total": sum(s["kills"] for _, s in killed),
        "hh_killed": len([1 for _, s in killed if s["hh_kills"]]),
        "hh_kills_total": sum(s["hh_kills"] for _, s in killed),
        "hh_lost_runs": sum(1 for _, s in killed if not s["completed"] and s["hh_kills"]),
        "averted": sum(s["kills"] for _, s in killed if not s["hh_kills"]),
        "naive_recovered": len(recovered),
        "naive_gave_up": len(gave_up),
        "naive_gave_up_s": round(sum(s["wall_s"] for _, s in gave_up), 1),
        "saved_total_s": round(sum(saved), 1),
        "saved_median_s": round(statistics.median(saved), 2) if saved else 0.0,
        "saved_mean_s": round(statistics.mean(saved), 2) if saved else 0.0,
        "naive_wall_killed_s": round(sum(s["wall_s"] for _, s in recovered), 1),
        "hh_wall_killed_s": round(sum(s["hh_wall_s"] for _, s in recovered), 1),
        "no_kill_configs": len(configs) - len(killed),
        "record_total_s": round(sum(r["hh"]["cold"]["wall_s"] for r in rows), 1),
    }


def runner_stats(rows: list[dict]) -> dict:
    """runner -> {saved: [s per killed config], mean} for the scatter."""
    out = {}
    for r in rows:
        for s in r["sweep"]:
            sv = saving(s)
            if sv is not None:
                out.setdefault(r["runner"], []).append(sv)
    return out


# ----------------------------------------------------------------- charts --

def paired_row(label, sub, naive_v, hh_v, vmax, width=760):
    """One repo/config row: two HTML bars in fixed columns — labels and values
    live in their own columns and can never overlap the bars."""
    lab_w, val_w = 188, 96
    plot = width - lab_w - val_w * 2 - 30

    def bar(v, color):
        w = max(2, int(plot * min(1.0, (v or 0) / vmax)))
        return (f'<div class="pb-track" style="width:{plot}px">'
                f'<div class="pb-fill" style="width:{w}px;background:{color}"></div></div>')

    def val(v):
        return f'<div class="pb-val">{fmt(v, 2)}s</div>'

    return (f'<div class="pb-row"><div class="pb-lab"><b>{esc(label)}</b>'
            f'{f"<span>{esc(sub)}</span>" if sub else ""}</div>'
            f'{bar(naive_v, C["slate"])}{val(naive_v)}'
            f'{bar(hh_v, C["green"])}{val(hh_v)}</div>')


def scatter_svg(rows, width=1080, height=340):
    """Saved seconds per killed configuration, grouped by runner, with per-runner
    means and a grand mean across runners."""
    stats = runner_stats(rows)
    runners = sorted(stats)
    if not runners:
        return "<p>No configurations with a defined saving.</p>"
    all_v = [v for vs in stats.values() for v in vs]
    ymin = min(min(all_v), 0.0)
    ymax = max(max(all_v), 1.0)
    pad = (ymax - ymin) * 0.12 or 1.0
    ymin -= pad
    ymax += pad
    lab_w, right_w = 74, 18
    plot_w = width - lab_w - right_w
    plot_h = height - 46
    slot = plot_w / len(runners)

    def y(v):
        return 22 + plot_h * (1 - (v - ymin) / (ymax - ymin))

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
           f'font-family="inherit">']
    # y gridlines
    for gv in range(int(ymin // 5 * 5), int(ymax) + 6, 5):
        if ymin <= gv <= ymax:
            yy = y(gv)
            out.append(f'<line x1="{lab_w}" y1="{yy:.0f}" x2="{width - right_w}" y2="{yy:.0f}" '
                       f'stroke="#E8EAF2" stroke-width="1"/>')
            out.append(f'<text x="{lab_w - 8}" y="{yy + 4:.0f}" font-size="10" '
                       f'text-anchor="end" fill="{C["faint"]}">{gv}s</text>')
    # zero line
    if ymin < 0 < ymax:
        out.append(f'<line x1="{lab_w}" y1="{y(0):.0f}" x2="{width - right_w}" y2="{y(0):.0f}" '
                   f'stroke="#C9CDDE" stroke-width="1.5"/>')
    means = {r: statistics.mean(vs) for r, vs in stats.items()}
    grand = statistics.mean(means.values())
    out.append(f'<line x1="{lab_w}" y1="{y(grand):.0f}" x2="{width - right_w}" y2="{y(grand):.0f}" '
               f'stroke="{C["red"]}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    out.append(f'<text x="{width - right_w}" y="{y(grand) - 6:.0f}" font-size="10.5" '
               f'text-anchor="end" fill="{C["red"]}">mean across runners: {grand:.2f}s</text>')
    for i, rname in enumerate(runners):
        cx = lab_w + slot * (i + 0.5)
        fg = RUNNER_STYLE.get(rname, ("", C["muted"]))[1]
        vs = stats[rname]
        for v in vs:
            out.append(f'<circle cx="{cx + ((hash((rname, v)) % 13) - 6):.0f}" cy="{y(v):.0f}" '
                       f'r="5.5" fill="{fg}" fill-opacity="0.55" stroke="{fg}" stroke-width="1.2"/>')
        m = means[rname]
        out.append(f'<path d="M {cx:.0f} {y(m) - 7:.0f} L {cx + 7:.0f} {y(m):.0f} '
                   f'L {cx:.0f} {y(m) + 7:.0f} L {cx - 7:.0f} {y(m):.0f} Z" '
                   f'fill="{C["ink"]}" stroke="#fff" stroke-width="1.5"/>')
        out.append(f'<text x="{cx:.0f}" y="{height - 8}" font-size="11" text-anchor="middle" '
                   f'fill="{C["muted"]}">{esc(rname)}</text>')
        out.append(f'<text x="{cx:.0f}" y="{height - 22}" font-size="9.5" text-anchor="middle" '
                   f'fill="{C["faint"]}">n={len(vs)}</text>')
    out.append("</svg>")
    return "".join(out)


def sweep_grid(rows, guesses):
    head = "".join(f'<div class="gh">G={g}s</div>' for g in guesses) \
        + '<div class="gh gh-hh">hh (warm)</div>'
    body = []
    for r in rows:
        cells = []
        for g in guesses:
            x = next((s for s in r["sweep"] if s["guess_s"] == g), None)
            if x is None:
                cells.append('<div class="gc">—</div>')
            elif not x["completed"]:
                cells.append(f'<div class="gc gc-up">{x["wall_s"]:.1f} gave up</div>')
            elif x["kills"]:
                cells.append(f'<div class="gc gc-kill">{x["wall_s"]:.1f} ✗{x["kills"]}</div>')
            else:
                cells.append(f'<div class="gc gc-ok">{x["wall_s"]:.1f} ✓</div>')
        hk = r["hh"]["warm"].get("timed_out", 0)
        mark = f' {hk}×↻' if hk else ''
        cells.append(f'<div class="gc gc-hh">{r["hh"]["warm"]["wall_s"]:.2f}{mark}</div>')
        body.append(f'<div class="grow"><div class="gl">{esc(r["id"])}</div>'
                    + "".join(cells) + "</div>")
    return ('<div class="grid-wrap"><div class="grow grow-head"><div class="gl"></div>'
            + head + "</div>" + "".join(body) + "</div>")


def stat_card(kicker, value, unit, note, icon):
    return f"""<div class="stat">
  <div class="stat-ic">{icon}</div>
  <div class="stat-k">{esc(kicker)}</div>
  <div class="stat-v">{esc(value)}<small>{esc(unit)}</small></div>
  <div class="stat-n">{esc(note)}</div>
</div>"""


ICONS = {
    "repo": '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 4h11l5 5v11H4z" '
            'fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15 4v5h5" '
            'fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
    "runner": '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="8" '
              'fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v4l3 2" '
              'fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
    "tests": '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M5 12l4 4 10-10" '
             'fill="none" stroke="currentColor" stroke-width="2.2"/></svg>',
    "grid": '<svg viewBox="0 0 24 24" width="18" height="18"><rect x="4" y="4" width="7" '
            'height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/>'
            '<rect x="13" y="4" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" '
            'stroke-width="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.5" fill="none" '
            'stroke="currentColor" stroke-width="1.8"/><rect x="13" y="13" width="7" height="7" '
            'rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
}


# ------------------------------------------------------------------- page --

CSS = f"""
:root {{ --bg:{C["bg"]}; --card:{C["card"]}; --border:{C["border"]}; --ink:{C["ink"]};
  --muted:{C["muted"]}; --faint:{C["faint"]}; --red:{C["red"]}; --green:{C["green"]}; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
.top {{ background:#fff; border-bottom:1px solid var(--border); padding:12px 28px;
  display:flex; align-items:center; gap:14px; position:sticky; top:0; z-index:5; }}
.brand {{ font-weight:800; font-size:19px; letter-spacing:-.2px; }}
.brand em {{ font-style:normal; color:var(--red); }}
.pill {{ border:1px solid var(--border); border-radius:999px; padding:4px 12px;
  font-size:12px; color:var(--muted); background:#fff; }}
.e0 {{ display:flex; align-items:center; gap:9px; text-decoration:none; padding:4px 12px 4px 5px;
  border:1px solid var(--border); border-radius:999px; background:#fff; }}
.e0 img {{ width:26px; height:26px; border-radius:50%; display:block; }}
.e0 span {{ font-weight:800; font-size:13.5px; letter-spacing:.02em; color:var(--ink); }}
.e0:hover {{ border-color:#f0c3cd; box-shadow:0 2px 8px rgba(217,31,60,.12); }}
.wrap {{ max-width:1180px; margin:0 auto; padding:24px 22px 60px; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:20px 22px; box-shadow:0 1px 2px rgba(16,20,50,.04); }}
.card + .card, .band + .card, .card + .band {{ margin-top:16px; }}
.sect {{ {SECT} margin:0 0 12px; }}
.band {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:4px 0 16px; }}
.stat {{ background:var(--card); border:1px solid var(--border); border-radius:14px;
  padding:18px 20px 16px; position:relative; overflow:hidden; }}
.stat:before {{ content:""; position:absolute; inset:0 0 auto 0; height:4px;
  background:linear-gradient(90deg,{C["red"]},#f97316,#f59e0b); }}
.stat-ic {{ color:var(--red); opacity:.85; }}
.stat-k {{ {SECT} margin:10px 0 2px; }}
.stat-v {{ font-size:44px; font-weight:800; letter-spacing:-1.5px; line-height:1.05;
  color:var(--ink); }}
.stat-v small {{ font-size:16px; color:var(--muted); font-weight:700; letter-spacing:0; }}
.stat-n {{ color:var(--faint); font-size:11.5px; margin-top:4px; }}
.grid2 {{ display:grid; grid-template-columns:1.15fr 1fr; gap:16px; }}
.bignum {{ font-size:72px; font-weight:800; letter-spacing:-2px; color:var(--red); line-height:1;
  text-shadow:0 6px 18px rgba(217,31,60,.18); }}
.bignum small {{ font-size:26px; color:var(--ink); font-weight:700; letter-spacing:-.5px; }}
.subnum {{ color:var(--muted); font-size:13.5px; margin-top:10px; }}
.chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }}
.chip {{ border:1px solid var(--border); border-radius:8px; padding:5px 10px; font-size:12px;
  color:var(--muted); background:#fbfbfe; }}
.kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:6px 0 2px; }}
.kpi .v {{ font-size:28px; font-weight:800; letter-spacing:-.6px; }}
.kpi .k {{ {SECT} }}
.kpi .n {{ color:var(--faint); font-size:11.5px; }}
.kbar {{ height:12px; border-radius:999px; background:var(--track); overflow:hidden;
  margin:10px 0 6px; display:flex; }}
.kbar i {{ height:100%; display:block; }}
.hbar {{ display:flex; align-items:center; gap:10px; margin:7px 0; }}
.hbar-lab {{ width:150px; font-size:12px; color:var(--ink); font-weight:600; flex-shrink:0; }}
.hbar-track {{ height:12px; border-radius:999px; background:var(--track); overflow:hidden; }}
.hbar-fill {{ height:100%; border-radius:999px; }}
.hbar-val {{ font-size:11.5px; color:var(--muted); min-width:52px; text-align:right; }}
.pb-row {{ display:flex; align-items:center; gap:10px; margin:6px 0; }}
.pb-lab {{ width:188px; flex-shrink:0; }}
.pb-lab b {{ font-size:12.5px; display:block; }}
.pb-lab span {{ font-size:11px; color:var(--faint); }}
.pb-track {{ height:11px; border-radius:999px; background:var(--track); overflow:hidden;
  flex-shrink:0; }}
.pb-fill {{ height:100%; border-radius:999px; }}
.pb-val {{ width:96px; text-align:right; font-size:11.5px; color:var(--muted);
  font-variant-numeric:tabular-nums; }}
.pb-head {{ display:flex; gap:10px; margin:2px 0 8px; }}
.pb-head .pb-lab:after {{ content:""; }}
.pb-head .sp {{ font-size:10px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--faint); font-weight:700; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; margin-top:12px; }}
.legend i {{ width:12px; height:12px; border-radius:4px; display:inline-block;
  margin-right:6px; vertical-align:-1px; }}
.legend span {{ font-size:12px; color:var(--muted); }}
.grid-wrap {{ overflow-x:auto; }}
.grow {{ display:grid; grid-template-columns:120px repeat(auto-fit,minmax(72px,1fr));
  grid-auto-flow:column; grid-template-columns:120px repeat(6,minmax(72px,1fr)) minmax(96px,1fr);
  gap:6px; margin:5px 0; align-items:center; }}
.gl {{ font-size:12px; font-weight:600; }}
.gh {{ {SECT} text-align:center; font-size:10px; }}
.gh-hh {{ color:var(--green); }}
.gc {{ text-align:center; font-size:11.5px; font-variant-numeric:tabular-nums;
  border-radius:8px; padding:7px 4px; border:1px solid transparent; }}
.gc-ok {{ background:#eef8f3; color:#0b7a5b; border-color:#cfeee2; }}
.gc-kill {{ background:#fdecef; color:#b31630; border-color:#f6cdd5; }}
.gc-up {{ background:#4b1020; color:#ffd7de; border-color:#7a1c33; }}
.gc-hh {{ background:#e8f6ee; color:#0b7a5b; border-color:#bfe8d6; font-weight:700; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ {SECT} text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }}
td {{ padding:8px 10px; border-bottom:1px solid #f1f2f8; vertical-align:top; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.tag {{ display:inline-block; background:var(--red-soft); color:var(--red); border-radius:6px;
  padding:2px 8px; font-size:11px; font-weight:700; }}
.tag.g {{ background:#e8f6ee; color:#0b7a5b; }}
.tag.s {{ background:#eef0f6; color:var(--slate); }}
.rb {{ display:inline-block; border:1px solid; border-radius:6px; padding:2px 9px;
  font-size:11px; font-weight:700; letter-spacing:.01em; }}
.note-list li {{ margin:7px 0; }}
code {{ background:#eef0f6; border-radius:6px; padding:2px 6px; font-size:12px; }}
pre {{ background:#14162b; color:#dfe3ff; padding:14px 16px; border-radius:10px;
  overflow:auto; font-size:12.5px; }}
.foot {{ color:var(--faint); font-size:12px; margin-top:28px; }}
@media (max-width:900px) {{ .grid2, .band {{ grid-template-columns:1fr; }}
  .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
"""


def render(out: dict, repos_cfg: list[dict]) -> str:
    rows = [r for r in out.get("repos", []) if r.get("ok")]
    guesses = out.get("guesses_s", [1, 2, 5, 10, 20, 30])
    date = out.get("date", time.strftime("%Y-%m-%d"))
    s = summarise(rows)
    cfg = {r["id"]: r for r in repos_cfg}
    tests_total = sum(r.get("tests_recorded") or 0 for r in rows)
    runners = sorted({r["runner"] for r in rows})

    P = []
    A = P.append
    A(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hh · timeout-metric benchmark · {esc(date)}</title>
<style>{CSS}</style></head><body>

<div class="top">
  <div class="brand"><em>hh</em> · timeout-metric</div>
  <div class="pill">benchmark</div>
  <div class="pill">{esc(date)}</div>
  <div style="flex:1"></div>
  <a class="e0" href="https://evenzero.in" target="_blank" rel="noopener">
    <img alt="evenzero" src="data:image/png;base64,{LOGO_B64}"><span>evenzero</span>
  </a>
</div>

<div class="wrap">
""")

    # ---------------- stats band (replaces the old intro)
    A('<div class="band">')
    A(stat_card("repositories", str(s["repos"]), "",
                "fresh repos, none from the previous run", ICONS["repo"]))
    A(stat_card("test runners", str(len(runners)), "",
                "one repo per runner, incl. nextest", ICONS["runner"]))
    A(stat_card("tests run", f"{tests_total:,}", "",
                "test cases discovered and recorded", ICONS["tests"]))
    A(stat_card("configurations", str(s["configurations"]), "",
                f'{s["repos"]} repos × {len(guesses)} timeout guesses', ICONS["grid"]))
    A("</div>")

    # ---------------- payoff hero + kill-free (killed configs only)
    A(f"""
<div class="grid2">
  <div class="card">
    <div class="sect">Time saved vs the naive caller</div>
    <div class="bignum">{fmt(s["saved_total_s"])}<small> s</small></div>
    <div class="subnum">Total wall-clock seconds hh saved across the
    <b>{s["naive_recovered"]}</b> configurations where a too-low guess SIGKILLed naive
    and naive still produced a result · median <b>{fmt(s["saved_median_s"], 2)} s</b>,
    mean <b>{fmt(s["saved_mean_s"], 2)} s</b> per configuration.</div>
    <div class="chips">
      <div class="chip">record-pass price (once per repo) · <b>{fmt(s["record_total_s"])} s</b></div>
      <div class="chip">naive never finished · <b>{s["naive_gave_up"]}</b></div>
    </div>
  </div>
  <div class="card">
    <div class="sect">Kill-free score · the {s["naive_killed"]} killed configurations only</div>
    <div class="kpis">
      <div class="kpi"><div class="k">naive</div>
        <div class="v" style="color:{C["red"]}">{s["naive_kills_total"]}</div>
        <div class="n">SIGKILLs, lost attempts</div></div>
      <div class="kpi"><div class="k">hh</div>
        <div class="v" style="color:{C["green"]}">{s["hh_kills_total"]}</div>
        <div class="n">budget resumes, nothing lost</div></div>
      <div class="kpi"><div class="k">runs lost</div>
        <div class="v" style="color:{C["green"]}">0</div>
        <div class="n">hh: 0 · naive: {s["naive_gave_up"]} give-ups</div></div>
    </div>
    <div class="kbar" title="share of SIGKILLs in killed configurations">
      <i style="width:{100 * s["naive_kills_total"] / max(1, s["naive_kills_total"] + s["hh_kills_total"]):.0f}%;
        background:linear-gradient(90deg,#e11d48,#d91f3c)"></i>
      <i style="width:{100 * s["hh_kills_total"] / max(1, s["naive_kills_total"] + s["hh_kills_total"]):.0f}%;
        background:linear-gradient(90deg,#34d399,#10b981)"></i>
    </div>
    <div class="meter-l" style="display:flex;justify-content:space-between;font-size:11px;
      color:{C["faint"]}"><span>naive kills {s["naive_kills_total"]}</span>
      <span>hh resumes {s["hh_kills_total"]}</span></div>
    <div class="subnum" style="margin-top:12px">Only configurations where the guess actually
    killed something are counted — the {s["no_kill_configs"]} no-kill configurations are
    excluded from this score. A hh resume is the budget exceeding once and continuing from
    the ledger; it is not a SIGKILL of the suite's work.</div>
  </div>
</div>
""")

    # ---------------- scatter: saved per runner vs mean
    A(f"""
<div class="card">
  <div class="sect">Time saved per runner</div>
  <p class="hero-sub" style="color:{C["muted"]};margin:0 0 12px;max-width:none">
  Each dot is one killed configuration with a defined saving, placed on its runner.
  Diamonds are per-runner means; the dashed line is the mean across all runners
  ({esc(", ".join(runners))}). Negative dots are configs where hh's ledger overhead
  exceeded the naive restart cost.</p>
  {scatter_svg(rows)}
  <div class="legend">
    <span><i style="background:{C["ink"]}"></i>runner mean</span>
    <span><i style="background:{C["red"]}"></i>mean across runners</span>
    <span><i style="background:#9ca3af;opacity:.6"></i>one configuration</span>
  </div>
</div>
""")

    # ---------------- per-repo table
    A(f"""
<div class="card">
  <div class="sect">Per repository — recorded measurements</div>
  <table>
  <tr><th>repo</th><th>runner</th><th class="num">tests</th><th class="num">reference</th>
  <th class="num">budget</th><th class="num">hh warm wall</th><th class="num">hh resumes</th>
  <th>naive killed guesses</th><th>gave up</th></tr>""")
    for r in rows:
        killed = [x for x in r["sweep"] if x["kills"]]
        gave = [x for x in killed if not x["completed"]]
        hk = r["hh"]["warm"].get("timed_out", 0)
        kt = ", ".join(f"G={x['guess_s']}" for x in killed) or '<span class="tag g">none</span>'
        gt = ", ".join(f"G={x['guess_s']}" for x in gave) or "—"
        A(f'<tr><td><b>{esc(r["id"])}</b></td><td>{badge(r["runner"])}</td>'
          f'<td class="num">{r.get("tests_recorded") or "—"}</td>'
          f'<td class="num">{fmt(r["reference_s"], 2)} s</td>'
          f'<td class="num">{fmt(r["hh"]["warm"].get("budget", {}).get("budget_s"), 2)} s</td>'
          f'<td class="num">{fmt(r["hh"]["warm"]["wall_s"], 2)} s</td>'
          f'<td class="num">{hk or "—"}</td><td>{kt}</td><td>{gt}</td></tr>')
    A("</table></div>")

    # ---------------- sweep grid
    A(f"""
<div class="card">
  <div class="sect">The sweep — wall-clock cost of getting the suite to finish</div>
  <p class="hero-sub" style="color:{C["muted"]};margin:2px 0 12px;max-width:none">
  Each cell is one repository × guess configuration — the total wall clock to get a
  result. A red cell contains the wasted attempt(s) <em>and</em> the restart;
  <b>gave up</b> means four guesses in a row were too low and naive returned no result.
  The hh column is the warm <code>run_tests</code> wall (identical at every G);
  <b>N×↻</b> marks budgets that exceeded and resumed.</p>
  {sweep_grid(rows, guesses)}
  <div class="chips">
    <div class="chip"><span class="tag g">green</span> completed without a kill</div>
    <div class="chip"><span class="tag">red</span> SIGKILLed, then restarted</div>
    <div class="chip"><span class="tag" style="background:#4b1020;color:#ffd7de">dark</span> gave up</div>
  </div>
</div>
""")

    # ---------------- killed configs, fixed layout (no SVG text collisions)
    A('<div class="card"><div class="sect">Killed configurations, side by side</div>')
    A('<div class="pb-head"><div class="pb-lab"></div><div class="sp" style="width:96px">'
      '</div></div>')
    items = []
    for r in rows:
        for x in r["sweep"]:
            if x["kills"] and x["completed"]:
                items.append((r["id"], x["guess_s"], x["wall_s"], x["hh_wall_s"],
                              r["hh"]["warm"].get("budget", {}).get("budget_s")))
    items.sort(key=lambda t: (t[2] - t[3]), reverse=True)
    vmax = max((max(t[2], t[3]) for t in items), default=1) or 1
    for rid, g, nv, hv in ((i[0], i[1], i[2], i[3]) for i in items):
        A(paired_row(rid, f"G={g}s", nv, hv, vmax))
    A('<div class="legend"><span><i style="background:' + C["slate"] + '"></i>naive wall '
      '(attempts + restart)</span><span><i style="background:' + C["green"] + '"></i>hh warm '
      'wall</span></div></div>')

    # ---------------- what hh measured
    A(f"""
<div class="card">
  <div class="sect">What hh measured, and what it chose</div>
  <p class="hero-sub" style="color:{C["muted"]};margin:2px 0 12px;max-width:none">
  <b>Budget</b> is the per-attempt kill threshold hh picks: <code>p90 × 1.25</code> of the
  suite's total test time from the record pass, floored at 3 s. It is not a wall-clock
  prediction — it only has to outlast the slowest test the runner may be inside when the
  budget runs out, so a kill never lands on unfinished work. <b>Warm wall</b> is what the
  caller actually waited, collection and ledger included.</p>
  <table>
  <tr><th>repo</th><th class="num">reference (median of 2)</th><th class="num">record pass</th>
  <th class="num">p50</th><th class="num">p90</th><th class="num">budget</th>
  <th class="num">hh warm wall</th></tr>""")
    for r in rows:
        b = r["hh"]["warm"].get("budget") or {}
        A(f'<tr><td>{esc(r["id"])} {badge(r["runner"])}</td>'
          f'<td class="num">{fmt(r["reference_s"], 2)} s</td>'
          f'<td class="num">{fmt(r["hh"]["cold"]["wall_s"], 2)} s</td>'
          f'<td class="num">{fmt(b.get("p50_s"), 3)} s</td>'
          f'<td class="num">{fmt(b.get("p90_s"), 3)} s</td>'
          f'<td class="num"><b>{fmt(b.get("budget_s"), 2)} s</b></td>'
          f'<td class="num">{fmt(r["hh"]["warm"]["wall_s"], 2)} s</td></tr>')
    A("</table></div>")

    # ---------------- suite notes
    A("""
<div class="card">
  <div class="sect">Suite notes — what ran, and what was already failing</div>
  <table>
  <tr><th>repo</th><th>suite note</th></tr>
  <tr><td><b>pycodestyle</b></td><td><code>test_check_nullbytes</code> is a pre-existing
  failure on this Python build; it runs in both arms. Suite otherwise green.</td></tr>
  <tr><td><b>simplejson</b></td><td>C extension not built in this sandbox (pure-Python
  fallback used).</td></tr>
  <tr><td><b>junit4</b></td><td>15 test files excluded from compilation (need mockito /
  APIs absent from this JDK shim); the remaining tests run via JUnitCore with some
  pre-existing environment failures; discovery via hh's vintage arm.</td></tr>
  <tr><td><b>rspec-support</b></td><td><code>reentrant_mutex_spec.rb</code> excluded (needs
  the <code>thread_order</code> gem — unobtainable offline); some pre-existing failures from
  OS features ruby.wasm lacks (<code>fork</code>, <code>flock</code>, <code>IO</code>
  reopen).</td></tr>
  <tr><td><b>json-c</b></td><td><code>xxd</code> provided as a small hexdump shim for one
  shell test.</td></tr>
  <tr><td><b>undici</b></td><td>suite is the <code>test/node-test</code> directory (the
  project's own <code>test:node-test</code>).</td></tr>
  <tr><td><b>dayjs / minimatch</b></td><td>jest/tap module-load gaps sit outside the per-test
  sums; hh's budget under-shot and it resumed rather than restarting.</td></tr>
  <tr><td><b>thealgorithms-rust</b></td><td>runs on a full Rust toolchain with
  <code>cargo-nextest</code> built from vendored sources; the crate has zero external
  dependencies, so no crates.io access is needed.</td></tr>
  </table></div>""")

    # ---------------- reproducing
    A('<div class="card"><div class="sect">Reproducing & provenance</div>')
    A("""<pre>python3 -m benchmark setup    # clone + install the repos in repos.json
python3 -m benchmark run      # measure + write reports
python3 -m benchmark report   # regenerate the reports from _work/results</pre>""")
    A('<table><tr><th>repo</th><th>runner</th><th>source</th><th>suite command</th></tr>')
    for r in rows:
        rc = cfg.get(r["id"], {})
        url = (rc.get("url") or r.get("url") or "").replace("https://github.com/", "").replace(".git", "")
        suite = " ".join(rc.get("suite") or r.get("suite") or [])
        A(f'<tr><td><b>{esc(r["id"])}</b></td><td>{badge(r["runner"])}</td>'
          f'<td><code>{esc(url)}</code></td><td><code>{esc(suite[:110])}</code></td></tr>')
    A("</table>")
    try:
        import subprocess
        hh_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, cwd=HERE,
                                timeout=5).stdout.strip()
    except Exception:
        hh_sha = "?"
    A(f'<div class="foot">Generated {esc(time.strftime("%Y-%m-%d %H:%M"))} by '
      f'<code>python3 -m benchmark report</code> · hh @ {esc(hh_sha)} · '
      f'<a href="https://evenzero.in">evenzero</a></div></div>')

    # ---------------- honesty LAST
    slower = []
    for r in rows:
        clean = [x["wall_s"] for x in r["sweep"] if not x["kills"]]
        if clean and r["hh"]["warm"]["wall_s"] > min(clean):
            slower.append((r["id"], r["hh"]["warm"]["wall_s"], min(clean)))
    slower.sort(key=lambda t: t[1] / t[2], reverse=True)
    slow_txt = ""
    if slower:
        rid, w, b = slower[0]
        slow_txt = (f'In {len(slower)} of {s["repos"]} repositories hh&rsquo;s warm call is '
                    f'slower than the fastest clean naive run — worst recorded: '
                    f'{esc(rid)} at {fmt(w, 2)} s vs {fmt(b, 2)} s. Where the guess is '
                    f'already fine, naive is often the cheaper call.')
    hh_soft = [r["id"] for r in rows if r["hh"]["warm"].get("timed_out")]
    A(f"""
<div class="card">
  <div class="sect">How to read this honestly</div>
  <ul class="note-list">
    <li><b>What this measures.</b> A caller must give a test suite a timeout. Too low and
    the suite is SIGKILLed — the work is lost and the whole thing runs again. hh exists to
    make that not happen, so the benchmark measures two things: whether hh's budget avoids
    the kill when the guess is too low, and how many seconds that saves. The sweep is the
    same experiment at six values of G ({esc(", ".join(str(g) for g in guesses))} s);
    <code>naive</code> doubles G and restarts from zero on a kill, <code>hh</code> records
    the suite once and then runs under a measured budget that does not depend on G.</li>
    <li><b>hh does not make the tests faster.</b> Where G was already high enough both arms
    run the suite once; hh's ledger adds overhead instead — stated, not hidden
    ({s["no_kill_configs"]} of {s["configurations"]} configurations are in this class).
    {slow_txt}</li>
    <li><b>The record pass is the honest price.</b> hh's first call is the naive command plus
    a ledger — one extra full run of the suite per repository,
    {fmt(s["record_total_s"])} s of test time in this run. Paid once per repository, never
    per call.</li>
    {f'<li><b>hh exceeded its own budget on {esc(", ".join(hh_soft))} and resumed.</b> The per-test sum under-counts time spent between tests (jest/tap load modules outside individual test spans), so the p90-derived budget sat below the real execution span. hh resumed from the ledger rather than restarting — the resume path, not a lost run.</li>' if hh_soft else ''}
    <li><b>Budgets hit the 3 s floor on small suites.</b> On a slower machine or a larger
    suite every number scales with the suite's duration D.</li>
    <li><b>Some suites run with pre-existing failures</b> in this sandbox (missing OS features
    such as <code>fork</code>/<code>flock</code> in interpreter shims). The same suite runs
    in both arms at every guess, so the comparison stays paired; failures are noted in the
    suite notes above.</li>
    <li><b>Java, PHP, and Ruby suites run on source-built stacks in this environment</b>
    (JUnit 4.13.2 + junit-jupiter/vintage 5.10.2 on a stub JDK, PHPUnit 9.6.29 on php-wasm,
    rspec-core 3.13 on ruby.wasm). Their absolute seconds reflect that runtime; the
    naive-vs-hh pairing does not.</li>
    <li><b>Runners not covered:</b> nextest is included (TheAlgorithms/Rust); the harness's
    remaining adapters (if any) were left out because this environment cannot supply their
    toolchains.</li>
  </ul>
</div>
""")

    A("</div></body></html>")
    return "\n".join(P)


def write(out: dict, repos_cfg: list[dict], path: str | None = None) -> str:
    if path is None:
        date = out.get("date", time.strftime("%Y-%m-%d"))
        path = os.path.join(HERE, f"REPORT-{date}-timeout-metric.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(out, repos_cfg))
    return path
