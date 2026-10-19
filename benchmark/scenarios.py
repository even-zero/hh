"""Benchmark scenarios. Each scenario is a procedure over a repo descriptor:

- probe   : `hh plan` on the repo *as shipped* — the discovery/coverage audit
- clean   : naive-default ladder vs hh lane arm (executor plan/run or Tier-A capture)
- workers : hh plan/run at several worker counts (executor lane)
- resume  : mid-suite process-group SIGKILL, then finish — naive restarts, hh resumes
- hang    : injected never-finishing tests; naive escalating give-up vs hh censored runs
            (executor lane) or knowledge-preserving capture (capture lane)

Policies are scripted, never improvised: ladder budgets come from the descriptor,
escalation happens only on timeout, give-up counts are fixed. hh's own runs are
recorded through the same accounting stores as the naive arms (hh-full rows).
"""
from __future__ import annotations

import json
import os
import shutil

from . import config, harness, shims

HH = ["python3", "-m", "hh"]


def repo_cmd_subst(cmd: list[str], repo: dict, **subs) -> list[str]:
    out = []
    mapping = {
        "{repo_dir}": config.repo_dir(repo),
        "{shim}": shims.shim_dir(repo) if "shim" in repo else "",
    }
    mapping.update(subs)
    for c in cmd:
        for k, v in mapping.items():
            if k in c:
                c = c.replace(k, v)
        out.append(c)
    return out


# ---------------------------------------------------------------- probe

def scenario_probe(repo: dict) -> dict:
    sc = repo["scenarios"].get("probe", {})
    if sc is False or sc is None:
        return {}
    if sc is True:
        sc = {}
    root_rel = sc.get("root", ".")
    root = os.path.join(config.repo_dir(repo), root_rel)
    runner = sc.get("runner_override", repo["runner"]) if isinstance(sc, dict) else repo["runner"]
    store = config.store(repo["id"], "probe")
    shutil.rmtree(store, ignore_errors=True)
    plan_path = os.path.join(config.logdir(repo["id"]), f"{repo['id']}_probe_plan.json")
    env = config.env_for(repo)
    cmd = HH + ["--store-root", store, "plan", "--root", root,
                "--runner", runner, "--workers", "1", "--out", plan_path]
    harness.run_and_save(cmd, cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                         scenario="probe", arm="hh", attempt=1, kind="plan-probe",
                         budget_s=180, log_name=f"{repo['id']}_probe_stdout.log")
    d = json.load(open(plan_path))
    discovered = sum(len(s["tests"]) for s in d["shards"])
    harness.now_record(repo=repo["id"], scenario="probe", arm="hh", attempt=1,
                       kind="probe-result", runner=runner, root=root,
                       discovered=discovered, operating_level=d["operating_level"],
                       coverage=d.get("est", {}).get("coverage"),
                       plan_overhead_ms=d.get("overhead_ms"),
                       plan=plan_path)
    return {"discovered": discovered, "operating_level": d["operating_level"]}


# ---------------------------------------------------------------- clean

def scenario_clean(repo: dict, defaults: dict) -> None:
    ladder = defaults.get("naive_ladder_s", [30, 60, 120])
    store_n = config.store(repo["id"], "clean-naive")
    shutil.rmtree(store_n, ignore_errors=True)
    cmd = repo_cmd_subst(repo["suite_cmd"], repo)
    rdir = config.repo_dir(repo)
    env = config.env_for(repo)
    # naive default-policy ladder
    for i, budget in enumerate(ladder, 1):
        rec = harness.kill_run(cmd, budget, cwd=rdir, env=env, repo=repo["id"],
                               scenario="clean", arm="naive", attempt=i,
                               policy="naive-default", store_root=store_n,
                               log_name=f"clean_naive_a{i}.log")
        if rec["outcome"] == "timeout":
            continue
        break
    # reference run with full output (suite verdict), generous budget
    harness.kill_run(cmd, max(ladder), cwd=rdir, env=env, repo=repo["id"],
                     scenario="clean", arm="naive", attempt=99,
                     policy="naive-reference", store_root=store_n,
                     log_name="clean_reference.log")
    # hh arm
    if repo["lane"].endswith("_exec"):
        _clean_exec(repo)
    else:
        _clean_capture(repo, budget=max(ladder))


def _clean_exec(repo: dict) -> None:
    store = config.store(repo["id"], "clean-hh")
    shutil.rmtree(store, ignore_errors=True)
    env = config.env_for(repo, store_env_job=f"{repo['id']}-clean")
    ld = config.logdir(repo["id"])
    root = shims.shim_dir(repo) if "shim" in repo else config.repo_dir(repo)
    for phase in ("cold", "warm"):
        plan_path = os.path.join(ld, f"clean_{phase}_plan.json")
        harness.run_and_save(HH + ["--store-root", store, "plan", "--root", root,
                                   "--runner", repo["runner"], "--workers", "1",
                                   "--out", plan_path],
                             cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                             scenario="clean", arm="hh", attempt=1, kind=f"plan-{phase}",
                             budget_s=300)
        run, wall, _ = harness.run_and_save(
            HH + ["--store-root", store, "run", "--plan", plan_path, "--root", root,
                  "--runner", repo["runner"], "--suite-id", f"{repo['id']}-clean-{phase}"],
            cwd=config.repo_dir(repo), env=env, repo=repo["id"], scenario="clean",
            arm="hh", attempt=1, kind=f"run-{phase}", budget_s=900,
            log_name=f"clean_{phase}_run.log", keep_json=True)
        if run:
            harness.now_record(repo=repo["id"], scenario="clean", arm="hh", attempt=1,
                               kind=f"run-{phase}-stats", wall_s=run.get("wall_s"),
                               overhead_ms=run.get("overhead_ms"),
                               stats=run.get("stats"), ingested=run.get("ingested"),
                               receipt=run.get("time_saved", {}).get("this_call_s"))


def _clean_capture(repo: dict, budget: float) -> None:
    store = config.store(repo["id"], "capture")
    shutil.rmtree(store, ignore_errors=True)
    ledger = os.path.join(config.logdir(repo["id"]), "clean_capture_ledger.jsonl")
    if os.path.exists(ledger):
        os.remove(ledger)
    cmd = _capture_cmd(repo, ledger, None)
    env = config.env_for(repo, extra=_capture_env_extra(ledger))
    rec = harness.kill_run(cmd, budget, cwd=config.repo_dir(repo), env=env,
                           repo=repo["id"], scenario="clean", arm="hh-capture",
                           attempt=1, policy="hh-capture", store_root=store,
                           log_name="clean_capture.log")
    audit = harness.ledger_rows(ledger)
    rec2 = _record_ledger(repo, store, ledger)
    harness.now_record(repo=repo["id"], scenario="clean", arm="hh-capture", attempt=1,
                       kind="capture-audit", ledger=ledger, wall_s=rec["wall_ms"] / 1000,
                       audit=audit, record=rec2)


def _capture_cmd(repo: dict, ledger: str, deselect: str | None) -> list[str]:
    base = repo_cmd_subst(repo["suite_cmd"], repo)
    if repo["capture_kind"] == "pytest_plugin":
        cmd = base + ["-p", "hh.adapters.pytest_plugin"]
        if deselect:
            cmd += ["-k", deselect]
        return cmd
    if repo["capture_kind"] == "mocha_reporter":
        reporter = os.path.join(config.REPO_ROOT, "hh", "adapters", "hh_mocha_reporter.cjs")
        return [base[0], "--reporter", reporter] + base[1:]
    raise ValueError(repo["capture_kind"])


def _capture_env_extra(ledger: str) -> dict:
    return {"HH_LEDGER": ledger}


def _record_ledger(repo: dict, store: str, ledger: str) -> dict | None:
    parsed, _, tail = harness.run_and_save(
        HH + ["--store-root", store, "record", "--ledger", ledger,
              "--root", config.repo_dir(repo), "--runner", repo["runner"]],
        cwd=config.repo_dir(repo), env=config.env_for(repo), repo=repo["id"],
        scenario="record", arm="hh-capture", attempt=1, kind="record", budget_s=600)
    return parsed


# ---------------------------------------------------------------- workers

def scenario_workers(repo: dict) -> None:
    store = config.store(repo["id"], "clean-hh")  # warm store from clean
    env = config.env_for(repo, store_env_job=f"{repo['id']}-clean")
    ld = config.logdir(repo["id"])
    root = shims.shim_dir(repo) if "shim" in repo else config.repo_dir(repo)
    for w in repo["scenarios"]["workers"]:
        if w == 1:
            continue  # already covered by clean warm
        plan_path = os.path.join(ld, f"workers_w{w}_plan.json")
        harness.run_and_save(HH + ["--store-root", store, "plan", "--root", root,
                                   "--runner", repo["runner"], "--workers", str(w),
                                   "--out", plan_path],
                             cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                             scenario="workers", arm="hh", attempt=w, kind=f"plan-w{w}",
                             budget_s=300)
        run, _, _ = harness.run_and_save(
            HH + ["--store-root", store, "run", "--plan", plan_path, "--root", root,
                  "--runner", repo["runner"], "--suite-id", f"{repo['id']}-w{w}"],
            cwd=config.repo_dir(repo), env=env, repo=repo["id"], scenario="workers",
            arm="hh", attempt=w, kind=f"run-w{w}", budget_s=900,
            log_name=f"workers_w{w}_run.log", keep_json=True)


# ---------------------------------------------------------------- resume

def scenario_resume(repo: dict, defaults: dict) -> None:
    rspec = defaults.get("resume", {})
    rerun_budget = rspec.get("rerun_budget_s", 120)
    baseline = _warm_baseline(repo) or 28.5
    kill = rspec.get("kill_at", "auto:0.5")
    cut = round(max(2.0, baseline * float(kill.split(":")[-1])), 1) if str(kill).startswith("auto") else float(kill)
    harness.now_record(repo=repo["id"], scenario="resume", arm="meta", attempt=0,
                       kind="resume-params", baseline_wall_s=baseline, kill_at_s=cut)
    cmd = repo_cmd_subst(repo["suite_cmd"], repo)
    env = config.env_for(repo)
    # naive: kill then full rerun from zero
    store_n = config.store(repo["id"], "resume-naive")
    shutil.rmtree(store_n, ignore_errors=True)
    harness.kill_run(cmd, cut, cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                     scenario="resume", arm="naive", attempt=1, policy="naive-default",
                     store_root=store_n, log_name="resume_naive_a1.log")
    harness.kill_run(cmd, rerun_budget, cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                     scenario="resume", arm="naive", attempt=2, policy="naive-default",
                     store_root=store_n, log_name="resume_naive_a2.log")
    # hh: plan, kill mid-run, plan --resume, finish
    store_h = config.store(repo["id"], "resume-hh")
    shutil.rmtree(store_h, ignore_errors=True)
    env_h = config.env_for(repo, store_env_job=f"{repo['id']}-resume")
    ld = config.logdir(repo["id"])
    root = shims.shim_dir(repo) if "shim" in repo else config.repo_dir(repo)
    p1 = os.path.join(ld, "resume_plan1.json")
    harness.run_and_save(HH + ["--store-root", store_h, "plan", "--root", root,
                               "--runner", repo["runner"], "--workers", "1", "--out", p1],
                         cwd=config.repo_dir(repo), env=env_h, repo=repo["id"],
                         scenario="resume", arm="hh", attempt=1, kind="plan-p1", budget_s=300)
    harness.kill_run(HH + ["--store-root", store_h, "run", "--plan", p1, "--root", root,
                           "--runner", repo["runner"], "--suite-id", f"{repo['id']}-resume-killed"],
                     cut, cwd=config.repo_dir(repo), env=env_h, repo=repo["id"],
                     scenario="resume", arm="hh", attempt=1, policy="hh-checkpointed",
                     store_root=store_h, log_name="resume_hh_killed.log")
    p2 = os.path.join(ld, "resume_plan2.json")
    harness.run_and_save(HH + ["--store-root", store_h, "plan", "--root", root,
                               "--runner", repo["runner"], "--workers", "1", "--resume",
                               "--out", p2],
                         cwd=config.repo_dir(repo), env=env_h, repo=repo["id"],
                         scenario="resume", arm="hh", attempt=2, kind="plan-resume", budget_s=300)
    run, _, _ = harness.run_and_save(
        HH + ["--store-root", store_h, "run", "--plan", p2, "--root", root,
              "--runner", repo["runner"], "--suite-id", f"{repo['id']}-resume-finish"],
        cwd=config.repo_dir(repo), env=env_h, repo=repo["id"], scenario="resume",
        arm="hh", attempt=2, kind="run-finish", budget_s=600,
        log_name="resume_hh_finish.log", keep_json=True)
    if run:
        s = run.get("stats", {})
        harness.now_record(repo=repo["id"], scenario="resume", arm="hh", attempt=2,
                           kind="resume-stats", wall_s=run.get("wall_s"),
                           overhead_ms=run.get("overhead_ms"),
                           resumed_tests=s.get("resumed"), resumed_s=(s.get("resumed_ms") or 0) / 1000,
                           receipt=run.get("time_saved", {}))


def _warm_baseline(repo: dict) -> float | None:
    if not os.path.exists(config.RECORDS):
        return None
    best = None
    for line in open(config.RECORDS):
        r = json.loads(line)
        if r.get("repo") == repo["id"] and r.get("kind") == "run-warm-stats" and r.get("wall_s"):
            best = r["wall_s"]
    return best


# ---------------------------------------------------------------- hang

def scenario_hang(repo: dict, defaults: dict) -> None:
    if repo["lane"].endswith("_exec"):
        _hang_exec(repo)
    else:
        _hang_capture(repo, defaults)


def _hang_exec(repo: dict) -> None:
    spec = repo["scenarios"]["hang"]
    naive_n = spec.get("naive_give_up_attempts", 3)
    ladder = [30, 60, 120][:naive_n]
    hang_shim = shims.shim_dir(repo, hang=True)
    cmd = repo_cmd_subst(repo["suite_cmd"], repo, **{"{shim}": hang_shim})
    store_n = config.store(repo["id"], "hang-naive")
    shutil.rmtree(store_n, ignore_errors=True)
    env = config.env_for(repo)
    for i, budget in enumerate(ladder, 1):
        harness.kill_run(cmd, budget, cwd=config.repo_dir(repo), env=env, repo=repo["id"],
                         scenario="hang", arm="naive", attempt=i, policy="naive-doubling",
                         store_root=store_n, log_name=f"hang_naive_a{i}.log")
    # hh: reuse the clean-hh store (the suite is already measured; 2 new tests hang)
    store = config.store(repo["id"], "clean-hh")
    env_h = config.env_for(repo, store_env_job=f"{repo['id']}-hang")
    ld = config.logdir(repo["id"])
    for i in range(1, spec.get("runs", 3) + 1):
        plan_path = os.path.join(ld, f"hang_plan{i}.json")
        harness.run_and_save(HH + ["--store-root", store, "plan", "--root", hang_shim,
                                   "--runner", repo["runner"], "--workers", "1",
                                   "--out", plan_path],
                             cwd=config.repo_dir(repo), env=env_h, repo=repo["id"],
                             scenario="hang", arm="hh", attempt=i, kind=f"plan-h{i}",
                             budget_s=300)
        run, _, _ = harness.run_and_save(
            HH + ["--store-root", store, "run", "--plan", plan_path, "--root", hang_shim,
                  "--runner", repo["runner"], "--suite-id", f"{repo['id']}-hang"],
            cwd=config.repo_dir(repo), env=env_h, repo=repo["id"], scenario="hang",
            arm="hh", attempt=i, kind=f"run-h{i}", budget_s=900,
            log_name=f"hang_run{i}.log", keep_json=True)
        if run:
            s = run.get("stats", {})
            harness.now_record(repo=repo["id"], scenario="hang", arm="hh", attempt=i,
                               kind=f"run-h{i}-stats", wall_s=run.get("wall_s"),
                               overhead_ms=run.get("overhead_ms"),
                               attempts=s.get("attempts"), censored=s.get("censored"),
                               ingested=run.get("ingested"),
                               receipt=run.get("time_saved", {}).get("this_call_s"))


def _hang_capture(repo: dict, defaults: dict) -> None:
    spec = repo["scenarios"]["hang"]
    variant_dir = os.path.join(config.REPOS_DIR, spec["dest"])
    harness.now_record(repo=repo["id"], scenario="hang", arm="meta", attempt=0,
                       kind="hang-variant", variant_dir=variant_dir)
    # naive
    store_n = config.store(repo["id"], "hang-naive")
    shutil.rmtree(store_n, ignore_errors=True)
    env_v = config.env_for(repo, repo_dir_override=variant_dir)
    for i, budget in enumerate(spec["naive_attempts"], 1):
        harness.kill_run(repo["suite_cmd"], budget, cwd=variant_dir, env=env_v,
                         repo=repo["id"], scenario="hang", arm="naive", attempt=i,
                         policy="naive-doubling", store_root=store_n,
                         log_name=f"hang_naive_a{i}.log")
    # capture arm
    store_c = config.store(repo["id"], "hang-cap")
    shutil.rmtree(store_c, ignore_errors=True)
    audits = []
    for i, step in enumerate(spec["capture_attempts"], 1):
        ledger = os.path.join(config.logdir(repo["id"]), f"hang_capture_ledger{i}.jsonl")
        if os.path.exists(ledger):
            os.remove(ledger)
        cmd = _capture_cmd(repo, ledger, step.get("deselect"))
        env_c = config.env_for(repo, extra=_capture_env_extra(ledger),
                               repo_dir_override=variant_dir)
        harness.kill_run(cmd, step["budget"], cwd=variant_dir, env=env_c, repo=repo["id"],
                         scenario="hang", arm="hh-capture", attempt=i,
                         policy="hh-capture", store_root=store_c,
                         log_name=f"hang_capture_a{i}.log")
        audit = harness.ledger_rows(ledger)
        audit["attempt"] = i
        audits.append(audit)
        harness.now_record(repo=repo["id"], scenario="hang", arm="hh-capture", attempt=i,
                           kind="capture-ledger-audit", ledger=ledger, audit=audit)
    # record the ledger that contains the full picture (attempt 2 today)
    if len(spec["capture_attempts"]) >= 2:
        ledger = os.path.join(config.logdir(repo["id"]), "hang_capture_ledger2.jsonl")
        _record_ledger(repo, store_c, ledger)


# ---------------------------------------------------------------- orchestration

SCENARIOS = {"probe": scenario_probe, "clean": scenario_clean, "workers": scenario_workers,
             "resume": scenario_resume, "hang": scenario_hang}


def setup_repo(repo: dict) -> None:
    """Clone (pinned), install deps, copy sitecustomize, build shims, build hang variants."""
    config.work_env()
    dest = config.repo_dir(repo)
    if not os.path.exists(dest):
        url, ref = repo["clone"]["url"], repo["clone"].get("ref")
        subprocess_clone(url, dest, ref, repo["clone"].get("depth"))
    rdir = dest
    for cmd in repo.get("setup", []):
        harness.run_and_save(cmd, cwd=rdir, env=config.env_for(repo), repo=repo["id"],
                             scenario="setup", arm="meta", attempt=1, kind="setup-cmd",
                             budget_s=1800, log_name=f"setup_{abs(hash(tuple(cmd))) % 99999}.log")
    if repo.get("sitecustomize"):
        src = os.path.join(config.HERE, "assets", repo["sitecustomize"])
        shutil.copy(src, os.path.join(config.ENV_DIR, "sitecustomize.py"))
    if repo["lane"] == "unittest_exec" and "shim" in repo:
        out = shims.build_shim(repo)
        harness.now_record(repo=repo["id"], scenario="setup", arm="meta", attempt=1,
                           kind="shim-built", shim=out)
    hang = repo["scenarios"].get("hang")
    if repo["lane"] == "capture" and isinstance(hang, dict) and hang.get("variant") == "copy":
        vdir = os.path.join(config.REPOS_DIR, hang["dest"])
        if not os.path.exists(vdir):
            shutil.copytree(rdir, vdir)
        shims.inject_hangs(vdir, hang["inject"], shims.CODE_KEYS)


def subprocess_clone(url: str, dest: str, ref: str | None, depth) -> None:
    import subprocess as sp
    if depth and (not ref or len(ref) != 40):
        sp.run(["git", "clone", "--depth", str(depth), "--branch", ref or "main", url, dest],
               check=True, capture_output=True)
        return
    sp.run(["git", "clone", url, dest], check=True, capture_output=True)
    if ref:
        sp.run(["git", "-C", dest, "checkout", "-q", ref], check=True, capture_output=True)


def run_scenarios(cfg: dict, repo_ids: list[str] | None, sc_names: list[str] | None) -> None:
    defaults = cfg.get("defaults", {})
    for repo in cfg["repos_active"]:
        if repo_ids and repo["id"] not in repo_ids:
            continue
        for name in ("probe", "clean", "workers", "resume", "hang"):
            if sc_names and name not in sc_names:
                continue
            if not repo["scenarios"].get(name):
                continue
            fn = SCENARIOS[name]
            if name in ("probe", "workers"):
                fn(repo)
            elif name == "resume":
                if repo["lane"].endswith("_exec"):
                    fn(repo, defaults)
                else:
                    harness.now_record(repo=repo["id"], scenario="resume", arm="meta",
                                       attempt=0, kind="resume-skipped",
                                       reason="executor lane only")
            else:
                fn(repo, defaults)


SCENARIO_ORDER = ["setup", "probe", "clean", "workers", "resume", "hang"]
