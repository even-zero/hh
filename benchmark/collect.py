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
import statistics
import subprocess
import time

from . import config, harness

RESULTS = os.path.join(config.RESULTS_DIR, "results.json")


def records() -> list[dict]:
    if not os.path.exists(config.RECORDS):
        return []
    return [json.loads(x) for x in open(config.RECORDS, encoding="utf-8") if x.strip()]


def rec_find(recs, repo, kind, scenario=None, attempt=None):
    # records.jsonl is append-only across runs; the last match is the one this
    # results set was produced from. Taking the first one silently mixed a
    # previous run's numbers into a new report.
    for r in reversed(recs):
        if r.get("repo") != repo or r.get("kind") != kind:
            continue
        if scenario and r.get("scenario") != scenario:
            continue
        if attempt is not None and r.get("attempt") != attempt:
            continue
        return r
    return None


def rec_last_n(recs, n, **match):
    """The newest `n` rows matching every flat key=value in `match`.

    A single clean pass is noisy (a 5.4 s cold pass for a 3.5 s suite reads as
    +50 %). Reports use medians over the last few passes; the rows come from
    records.jsonl, which is append-only across runs.
    """
    out = []
    for r in reversed(recs):
        if all(r.get(k) == v for k, v in match.items()):
            out.append(r)
            if len(out) >= n:
                break
    return list(reversed(out))


def med(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return round(statistics.median(vals), 3) if vals else None


def est_of(plan_path: str) -> dict | None:
    if not plan_path or not os.path.exists(plan_path):
        return None
    d = json.load(open(plan_path))
    e = d.get("est", {})
    b = e.get("breakdown", {})
    return {"n": b.get("n_tests"), "coverage": e.get("coverage"),
            "measured_tests": d.get("measured_tests"),
            "calibration": e.get("calibration"),
            "manifest": d.get("manifest_size"),
            "startup_s": d.get("startup_s"),
            "n_unknown": e.get("n_unknown"), "pred_wall_s": d.get("predicted_wall_s"),
            "pred_exec_wall_s": d.get("predicted_exec_wall_s"),
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
            elif repo["lane"].endswith("_exec"):
                # a cold plan no longer enumerates: the probe's `hh discover`
                # manifest is the measured universe
                n_tests = pr.get("discovered") or n_tests
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
            if repo["lane"] == "unittest_exec" and repo.get("shim") and os.path.exists(cp_plan):
                shim_n = sum(len(s["tests"]) for s in json.load(open(cp_plan))["shards"])
                out["compat"][-1]["tests"] = pr["discovered"]  # native universe differs
                out["compat"].append({"repo": f"{rid} (via shim)", "runner": repo["runner"],
                                      "tests": shim_n, "discovered": shim_n,
                                      "level": "full"})

    # ---- attempt logs: harness kills + executor hh-full rows ----
    for repo in cfg["repos_active"]:
        rid = repo["id"]
        # records.jsonl is append-only across benchmark runs: keep only the
        # newest row per (policy, attempt, scenario, arm), otherwise a report
        # mixes an old naive run with a fresh hh run.
        latest: dict[tuple, dict] = {}
        for r in recs:
            if r.get("repo") == rid and r.get("kind") == "kill_run":
                key = (r["policy"], r["attempt"], r.get("scenario"), r.get("arm"))
                latest[key] = {"policy": r["policy"], "suite_id": r["suite_id"],
                             "attempt": r["attempt"], "budget_s": r["budget_s"],
                             "outcome": r["outcome"], "wall_ms": r["wall_ms"],
                             "wasted_ms": r["wasted_ms"], "overhead_ms": 0.0,
                             "scenario": r["scenario"], "arm": r.get("arm")}
        rows = list(latest.values())
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
        if not repo["lane"].endswith("_exec"):
            continue
        rid = repo["id"]
        ld = config.logdir(rid)
        blk = {}
        for phase in ("cold", "warm"):
            blk[f"plan_{phase}"] = est_of(os.path.join(ld, f"clean_{phase}_plan.json"))
            runs = rec_last_n(recs, 5, repo=rid, kind=f"run-{phase}-stats",
                              scenario="clean")
            if runs:
                last = runs[-1]
                blk[f"run_{phase}"] = {
                    "wall_s": med(runs, "wall_s"),
                    "exec_wall_s": med(runs, "exec_wall_s"),
                    "startup_s": med(runs, "startup_s"),
                    "overhead_ms": med(runs, "overhead_ms"),
                    "attempts": last.get("stats", {}).get("attempts"),
                    "ingested": last.get("ingested"),
                    "phases": last.get("phases"),
                    "pred_exec_wall_s": last.get("predicted_exec_wall_s"),
                    "receipt_s": last.get("receipt"),
                    "net_s": last.get("net_s"),
                    "complete": all(r.get("complete") is not False for r in runs),
                    "n_runs": len(runs)}
        # workers
        for w in repo["scenarios"].get("workers", []):
            if w == 1:
                continue
            blk["plan_w2"] = est_of(os.path.join(ld, f"workers_w{w}_plan.json"))
            rr = rec_find(recs, rid, f"run-w{w}")
            if rr:
                runj = _last_json(os.path.join(ld, f"workers_w{w}_run.log"))
                blk["run_w2"] = {"wall_s": (runj or {}).get("wall_s"),
                                 "exec_wall_s": (runj or {}).get("exec_wall_s"),
                                 "startup_s": (runj or {}).get("startup_s"),
                                 "overhead_ms": (runj or {}).get("overhead_ms"),
                                 "ingested": (runj or {}).get("ingested")}
        # cold pass = record-only (no prediction): compare it with the naive run
        # of the *same* suite. Equal numbers are the success condition here;
        # hh is not supposed to be faster on a suite it has never measured.
        plains = rec_last_n(recs, 5, repo=rid, kind="kill_run", scenario="clean",
                            policy="naive-default", attempt=1, outcome="ok")
        cold = blk.get("run_cold") or {}
        if plains and cold.get("wall_s"):
            nv = med(plains, "wall_ms") / 1000.0
            blk["record"] = {
                "naive_s": round(nv, 3),
                "hh_s": cold.get("wall_s"),
                "delta_pct": round((cold["wall_s"] / nv - 1) * 100, 2),
                "naive_tests": None,
                "measured_after": (blk.get("plan_warm") or {}).get("measured_tests"),
                "level": (blk.get("plan_cold") or {}).get("level"),
                "n_runs": min(len(plains), cold.get("n_runs") or 0),
            }
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
        if not repo["lane"].endswith("_exec"):
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
        # collection is not execution: subtract the measured collection probe
        # from each arm before comparing (pytest/Mocha load test files first)
        coll_naive = next((r.get("collection_s") for r in reversed(recs)
                           if r.get("repo") == rid and r.get("kind") == "collection-result"
                           and r.get("arm") == "naive"), None)
        coll_hh = next((r.get("collection_s") for r in reversed(recs)
                        if r.get("repo") == rid and r.get("kind") == "collection-result"
                        and r.get("arm") == "hh"), None)
        if repo["lane"] == "capture":
            plain = next((a for a in logs if a.get("policy") == "naive-default"
                          and a.get("scenario") == "clean" and a["outcome"] == "ok"), None)
            plain_w = (plain or {}).get("wall_ms")
            ca = rec_find(recs, rid, "capture-audit")
            if ca:
                cap_w = ca.get("wall_s", 0) * 1000
                n = ca["audit"].get("end", 0)
                plain_s = (plain_w or 0) / 1000.0
                plain_exec = plain_s - (coll_naive or 0.0) if plain_w else None
                cap_exec = cap_w / 1000.0 - (coll_hh or 0.0) if plain_w else None
                facts["capture_overhead"].append({
                    "repo": rid, "tests": n,
                    "plain_s": plain_s, "capture_s": cap_w / 1000.0,
                    "collection_naive_s": coll_naive, "collection_hh_s": coll_hh,
                    "plain_exec_s": None if plain_exec is None else round(plain_exec, 3),
                    "capture_exec_s": None if cap_exec is None else round(cap_exec, 3),
                    "overhead_exec_ms": (None if plain_exec is None else
                                         round((cap_exec - plain_exec) * 1000, 1)),
                    "overhead_pct_exec": (None if not plain_exec else
                                          round((cap_exec / plain_exec - 1) * 100, 1)),
                    "overhead_ms": None if not plain_w else round(cap_w - plain_w, 1),
                    "overhead_pct": None if not plain_w else round((cap_w / plain_w - 1) * 100, 1),
                    "ledger_rows": ca["audit"].get("rows")})
        else:
            # exec lane: plan+run wall vs naive single-attempt wall
            plains = rec_last_n(recs, 5, repo=rid, kind="kill_run", scenario="clean",
                                policy="naive-default", attempt=1, outcome="ok")
            blk = out["runs"].get(rid) or {}
            rw = blk.get("run_warm")
            pw = blk.get("plan_warm")
            if plains and rw and pw:
                hh_tot = rw["wall_s"] + (pw.get("plan_overhead_ms") or 0) / 1000
                plain_s = med(plains, "wall_ms") / 1000
                # subtract the *same* measured collection cost from both arms:
                # comparing "naive wall − collect-only" against "hh exec-only"
                # mixed two different startup estimates and made hh look worse
                # than it is (collect-only prints every node id and costs more
                # than in-run collection).
                hh_exec = (rw.get("exec_wall_s") if rw.get("exec_wall_s") is not None
                           else rw["wall_s"] - (rw.get("startup_s") or 0.0))
                rec = (out["runs"].get(rid) or {}).get("record") or {}
                facts["capture_overhead"].append({
                    "repo": rid, "tests": (pw or {}).get("n"),
                    "record_s": rec.get("hh_s"), "record_pct": rec.get("delta_pct"),
                    "plain_s": plain_s,
                    "capture_s": round(hh_tot, 3),
                    # Collection is measured, not guessed: `--collect-only`
                    # costs more than in-run collection (it materializes and
                    # prints every node id), so it cannot be subtracted from
                    # one arm and not the other. Exec lanes therefore report
                    # the process-level (end-to-end) comparison plus hh's own
                    # measured exec/startup split; capture lanes below have an
                    # identical process shape in both arms and do subtract it.
                    "collection_naive_s": coll_naive,
                    "hh_startup_s": rw.get("startup_s"),
                    "hh_exec_s": round(hh_exec, 3),
                    "hh_plan_ms": pw["plan_overhead_ms"],
                    "plain_exec_s": None,
                    "capture_exec_s": None,
                    "overhead_exec_ms": None,
                    "overhead_pct_exec": None,
                    "overhead_ms": round((hh_tot - plain_s) * 1000, 1),
                    "overhead_pct": round((hh_tot / plain_s - 1) * 100, 2),
                    "self_metered_ms": rw.get("overhead_ms"),
                    "label": "full executor (plan+run); exec split measured"})
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
    try:
        repos_file = os.path.relpath(cfg.get("_path", os.path.join(config.HERE, "repos.json")),
                                     config.REPO_ROOT)
    except ValueError:
        repos_file = str(cfg.get("_path", "benchmark/repos.json"))
    out["meta"] = {"date": time.strftime("%Y-%m-%d"), "hh_version": v.replace("hh ", ""),
                   "hh_commit": head, "python": platform.python_version(),
                   "platform": platform.platform(), "cores": os.cpu_count(),
                   "repos_file": repos_file,
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
