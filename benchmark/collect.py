"""Assemble benchmark/_work/results/results.json from the raw artifacts:

- records.jsonl (every harness step: kills, runs, audits, plans)
- per-arm hh stores (attempt logs incl. the executor's own hh-full rows, receipts)
- saved plan/run JSONs (logs/<repo>/)

Output schema is the one dashboard.py renders and compare.py diffs.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time

from . import config, harness

RESULTS = os.path.join(config.RESULTS_DIR, "results.json")


def records() -> list[dict]:
    if not os.path.exists(config.RECORDS):
        return []
    return [json.loads(x) for x in open(config.RECORDS, encoding="utf-8") if x.strip()]


def rec_find(recs, repo, kind, scenario=None, attempt=None):
    for r in recs:
        if r.get("repo") != repo or r.get("kind") != kind:
            continue
        if scenario and r.get("scenario") != scenario:
            continue
        if attempt is not None and r.get("attempt") != attempt:
            continue
        return r
    return None


def est_of(plan_path: str) -> dict | None:
    if not plan_path or not os.path.exists(plan_path):
        return None
    d = json.load(open(plan_path))
    e = d.get("est", {})
    b = e.get("breakdown", {})
    return {"n": b.get("n_tests"), "coverage": e.get("coverage"),
            "n_unknown": e.get("n_unknown"), "pred_wall_s": d.get("predicted_wall_s"),
            "p50_s": e.get("p50_s"), "p90_s": e.get("p90_s"),
            "budget_s": d.get("total_budget_s"),
            "plan_overhead_ms": round(d.get("overhead_ms", 0.0), 1),
            "level": d.get("operating_level"), "shards": len(d.get("shards", [])),
            "quarantine": [s["budget_s"] for s in d.get("shards", []) if s.get("quarantine")]}


def savings(store: str, job: str) -> dict | None:
    env = dict(os.environ)
    env["PYTHONPATH"] = config.REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run(["python3", "-m", "hh", "--store-root", store,
                        "savings", "--job", job, "--json"],
                       capture_output=True, text=True, env=env)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def hh_version() -> tuple[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = config.REPO_ROOT
    v = subprocess.run(["python3", "-m", "hh", "--version"],
                       capture_output=True, text=True, env=env).stdout.strip()
    head = subprocess.run(["git", "-C", config.REPO_ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    return v, head


def collect(cfg: dict) -> dict:
    recs = records()
    out = {"schema": 1, "meta": {}, "compat": [], "attempt_logs": {},
           "runs": {}, "receipts": {}, "facts": {}, "repos": {}}

    # ---- per-repo info + compat probe ----
    for repo in cfg["repos_active"]:
        rid = repo["id"]
        out["repos"][rid] = {"display": repo.get("display", rid), "stars": repo.get("stars"),
                             "clone": repo["clone"], "lane": repo["lane"],
                             "runner": repo["runner"], "notes": repo.get("notes"),
                             "n_tests_expected": repo.get("n_tests_expected")}
        pr = rec_find(recs, rid, "probe-result")
        if pr:
            # measured universe: exec lane -> clean cold plan; capture -> ledger ends
            n_tests = repo.get("n_tests_expected") or 0
            cr = rec_find(recs, rid, "capture-audit")
            cp_plan = os.path.join(config.logdir(rid), "clean_cold_plan.json")
            if repo["lane"] == "capture" and cr and cr.get("audit", {}).get("end"):
                n_tests = cr["audit"]["end"]
            elif repo["lane"] == "unittest_exec" and os.path.exists(cp_plan):
                n_tests = sum(len(s["tests"]) for s in json.load(open(cp_plan))["shards"])
            reasons = None
            if pr["discovered"] == 0:
                reasons = ("unittest manifest can't see pytest functions"
                           if repo["runner"] == "pytest"
                           else "unittest manifest can't see JS tests at all"
                           if repo["runner"] == "node"
                           else f"its own naming ({repo['shim']['glob']}) is invisible "
                                f"(pattern is test_*.py)" if repo.get("shim") else
                                "suite shape invisible to the unittest manifest")
            out["compat"].append({"repo": rid, "runner": repo["runner"], "tests": n_tests,
                                  "discovered": pr["discovered"],
                                  "level": pr["operating_level"],
                                  "reasons": reasons})
            if repo["lane"] == "unittest_exec" and os.path.exists(cp_plan):
                out["compat"].append({"repo": f"{rid} (via shim)", "runner": repo["runner"],
                                      "tests": n_tests, "discovered": n_tests,
                                      "level": "full"})

    # ---- attempt logs: harness kills + executor hh-full rows ----
    for repo in cfg["repos_active"]:
        rid = repo["id"]
        rows = []
        for r in recs:
            if r.get("repo") == rid and r.get("kind") == "kill_run":
                rows.append({"policy": r["policy"], "suite_id": r["suite_id"],
                             "attempt": r["attempt"], "budget_s": r["budget_s"],
                             "outcome": r["outcome"], "wall_ms": r["wall_ms"],
                             "wasted_ms": r["wasted_ms"], "overhead_ms": 0.0,
                             "scenario": r["scenario"], "arm": r.get("arm")})
        for suffix in ("clean-naive", "clean-hh", "resume-naive", "resume-hh",
                       "hang-naive", "hang-cap", "capture", "packaging_hang-capture"):
            st = config.store(rid, suffix)
            for a in harness.read_attempt_log(st):
                if a.get("policy", "").startswith("hh-full"):
                    a = dict(a, scenario="(hh-full)")
                    rows.append(a)
        if rows:
            out["attempt_logs"][rid] = rows

    # ---- exec-lane scenario blocks ----
    for repo in cfg["repos_active"]:
        if repo["lane"] != "unittest_exec":
            continue
        rid = repo["id"]
        ld = config.logdir(rid)
        blk = {}
        for phase in ("cold", "warm"):
            blk[f"plan_{phase}"] = est_of(os.path.join(ld, f"clean_{phase}_plan.json"))
            st = rec_find(recs, rid, f"run-{phase}-stats")
            if st:
                blk[f"run_{phase}"] = {"wall_s": st.get("wall_s"),
                                       "overhead_ms": st.get("overhead_ms"),
                                       "attempts": st.get("stats", {}).get("attempts"),
                                       "ingested": st.get("ingested"),
                                       "receipt_s": st.get("receipt")}
        # workers
        for w in repo["scenarios"].get("workers", []):
            if w == 1:
                continue
            blk["plan_w2"] = est_of(os.path.join(ld, f"workers_w{w}_plan.json"))
            rr = rec_find(recs, rid, f"run-w{w}")
            if rr:
                runj = _last_json(os.path.join(ld, f"workers_w{w}_run.log"))
                blk["run_w2"] = {"wall_s": (runj or {}).get("wall_s"),
                                 "overhead_ms": (runj or {}).get("overhead_ms"),
                                 "ingested": (runj or {}).get("ingested")}
        # resume
        rp = rec_find(recs, rid, "resume-params")
        rs = rec_find(recs, rid, "resume-stats")
        blk["resume"] = {
            "kill_at_s": (rp or {}).get("kill_at_s"),
            "baseline_wall_s": (rp or {}).get("baseline_wall_s"),
            "plan_resume": est_of(os.path.join(ld, "resume_plan2.json")),
            "run_finish_wall_s": (rs or {}).get("wall_s"),
            "resumed_tests": (rs or {}).get("resumed_tests"),
            "resumed_s": (rs or {}).get("resumed_s"),
            "overhead_ms": (rs or {}).get("overhead_ms"),
            "receipt": (rs or {}).get("receipt")}
        # hang
        if repo["scenarios"].get("hang"):
            h = {"runs": []}
            for i in range(1, repo["scenarios"]["hang"].get("runs", 3) + 1):
                st = rec_find(recs, rid, f"run-h{i}-stats")
                h[f"plan{i}"] = est_of(os.path.join(ld, f"hang_plan{i}.json"))
                h["runs"].append({"wall_s": (st or {}).get("wall_s"),
                                  "overhead_ms": (st or {}).get("overhead_ms"),
                                  "attempts": (st or {}).get("attempts"),
                                  "censored": (st or {}).get("censored"),
                                  "ingested": (st or {}).get("ingested"),
                                  "receipt_s": (st or {}).get("receipt")})
            blk["hang"] = h
        out["runs"][rid] = blk

    # ---- receipts (exec lanes) ----
    for repo in cfg["repos_active"]:
        if repo["lane"] != "unittest_exec":
            continue
        rid = repo["id"]
        for job, store_suf in ((f"{rid}-hang", "clean-hh"), (f"{rid}-resume", "resume-hh"),
                               (f"{rid}-clean", "clean-hh")):
            d = savings(config.store(rid, store_suf), job)
            if d is not None:
                d["entries"] = _receipt_entries(config.store(rid, store_suf), job)
            out["receipts"][job] = d

    # ---- capture facts (overhead at scale, hang-capture audits) ----
    facts = {"capture_overhead": []}
    for repo in cfg["repos_active"]:
        rid = repo["id"]
        logs = out["attempt_logs"].get(rid, [])
        if repo["lane"] == "capture":
            plain = next((a for a in logs if a.get("policy") == "naive-default"
                          and a.get("scenario") == "clean" and a["outcome"] == "ok"), None)
            plain_w = (plain or {}).get("wall_ms")
            ca = rec_find(recs, rid, "capture-audit")
            if ca:
                cap_w = ca.get("wall_s", 0) * 1000
                n = ca["audit"].get("end", 0)
                facts["capture_overhead"].append({
                    "repo": rid, "tests": n,
                    "plain_s": (plain_w or 0) / 1000.0,
                    "capture_s": cap_w / 1000.0,
                    "overhead_ms": None if not plain_w else round(cap_w - plain_w, 1),
                    "overhead_pct": None if not plain_w else round((cap_w / plain_w - 1) * 100, 1),
                    "ledger_rows": ca["audit"].get("rows")})
        else:
            # exec lane: plan+run wall vs naive single-attempt wall
            plain = next((a for a in logs if a.get("policy") == "naive-default"
                          and a.get("scenario") == "clean" and a["outcome"] == "ok"), None)
            rw = rec_find(recs, rid, "run-warm-stats")
            pw = est_of(os.path.join(config.logdir(rid), "clean_warm_plan.json"))
            if plain and rw and pw:
                hh_tot = rw["wall_s"] + pw["plan_overhead_ms"] / 1000
                facts["capture_overhead"].append({
                    "repo": rid, "tests": (est_of(os.path.join(config.logdir(rid), "clean_cold_plan.json")) or {}).get("n"),
                    "plain_s": plain["wall_ms"] / 1000,
                    "capture_s": round(hh_tot, 3),
                    "overhead_ms": round((hh_tot - plain["wall_ms"] / 1000) * 1000, 1),
                    "overhead_pct": round((hh_tot / (plain["wall_ms"] / 1000) - 1) * 100, 2),
                    "self_metered_ms": rw.get("overhead_ms"),
                    "label": "full executor (plan+run)"})
        # hang capture audits
        audits = [r["audit"] for r in recs
                  if r.get("repo") == rid and r.get("kind") == "capture-ledger-audit"]
        if audits:
            open_ = next((a for a in audits if a.get("open_intervals")), {})
            naives = [a for a in logs if a.get("scenario") == "hang"
                      and a.get("policy") == "naive-doubling"]
            caps = [a for a in logs if a.get("scenario") == "hang"
                    and a.get("policy") == "hh-capture"]
            facts.setdefault("hang_capture", {})[rid] = {
                "naive_attempts": naives, "capture_attempts": caps,
                "audits": audits,
                "hang_identified": (open_.get("open_intervals") or [None])[0]}
    out["facts"] = facts
    v, head = hh_version()
    out["meta"] = {"date": time.strftime("%Y-%m-%d"), "hh_version": v.replace("hh ", ""),
                   "hh_commit": head, "python": platform.python_version(),
                   "platform": platform.platform(), "cores": os.cpu_count(),
                   "repos": {r["id"]: f"{r['clone']['url']}@{r['clone'].get('ref', 'HEAD')[:10]}"
                             for r in cfg["repos_active"]}}
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    with open(RESULTS, "w") as f:
        json.dump(out, f, indent=1)
    return out


def _last_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    txt = open(path).read().strip().splitlines()
    try:
        return json.loads(txt[-1])
    except Exception:
        return None


def _receipt_entries(store: str, job: str) -> list[dict]:
    p = os.path.join(store, ".hh", "experiments", "savings.jsonl")
    out = []
    if os.path.exists(p):
        for line_ in open(p, encoding="utf-8"):
            try:
                e = json.loads(line_)
            except json.JSONDecodeError:
                continue
            if e.get("job_id") == job:
                out.append({"kind": e.get("kind"), "saved_s": round(e.get("amount_ms", 0) / 1000, 1),
                            "claim_rule": e.get("basis", "")})
    return out


if __name__ == "__main__":
    c = collect(config.load())
    print(f"wrote {RESULTS}: {len(c['compat'])} compat rows, "
          f"{sum(len(v) for v in c['attempt_logs'].values())} attempt rows")
