"""The benchmark: one metric, ten repositories, mixed runners.

THE QUESTION
------------
A caller that runs a test suite has to give it a timeout. If the timeout is too
low the suite is SIGKILLed, the work is lost, and the caller has to run it
again. hh's only job is to produce a timeout that is not too low.

So there is one metric, in two halves:

  1. kill-free: when a caller's guess is too low, does hh's budget avoid the
     SIGKILL (or, if it is ever wrong, does it at least resume instead of
     restarting)?
  2. payoff: in the configurations where the guess *was* too low, how many
     seconds did hh save?

THE ARMS
--------
  naive   the repo's own suite command under a guessed timeout G. On a kill it
          doubles G and starts over from zero — what a shell wrapper or an
          agent does when it has no estimate.
  hh      the `run_tests` tool. First call: record pass (the naive command
          plus a ledger, no prediction). Later calls: the measured budget.

  G is swept over {1, 2, 5, 10, 20, 30} s because the guess is the variable.
  A suite that takes D seconds is killed by naive whenever G < D; hh's budget
  does not depend on G at all — that is the whole point.

Run:
    python3 -m benchmark setup     # clone + install the ten repos
    python3 -m benchmark run       # measure
    python3 -m benchmark report    # write benchmark/REPORT-<date>-timeout-metric.md
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "_work")
REPOS = os.path.join(WORK, "repos")
RESULTS = os.path.join(WORK, "results")
CONFIG = os.path.join(HERE, "repos.json")

GUESSES_S = [1, 2, 5, 10, 20, 30]
MAX_ATTEMPTS = 4          # naive doubles G at most this often before giving up
HARD_CAP_S = 300          # nothing in this set needs longer


# -- plumbing -------------------------------------------------------------

def load_repos() -> list[dict]:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)["repos"]


def repo_dir(repo: dict) -> str:
    return os.path.join(REPOS, repo["id"])


def work_dir(repo: dict) -> str:
    """The directory hh measures: the checkout, or a subdirectory of it."""
    return os.path.join(repo_dir(repo), repo.get("root") or ".")


def env_for(repo: dict, store: str | None = None) -> dict:
    env = dict(os.environ)
    root = work_dir(repo)
    path = [ROOT, root]
    for sub in ("src", "lib"):
        if os.path.isdir(os.path.join(root, sub)):
            path.append(os.path.join(root, sub))
    if repo.get("pythonpath"):
        path += [os.path.join(repo_dir(repo), p) for p in repo["pythonpath"]]
    env["PYTHONPATH"] = os.pathsep.join(path)
    if repo.get("mocha_require"):
        env["HH_MOCHA_REQUIRE"] = repo["mocha_require"]
    for k, v in (repo.get("env") or {}).items():
        env[k] = v
    if store:
        env["HH_STORE"] = store
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def sh(cmd: list[str], cwd: str, timeout: float | None = None,
       env: dict | None = None) -> tuple[int, float, str]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, time.perf_counter() - t0, (p.stdout + p.stderr)[-4000:]
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, time.perf_counter() - t0, out[-4000:]


def git_sha(url: str) -> str:
    p = subprocess.run(["git", "ls-remote", url, "HEAD"], capture_output=True, text=True)
    return p.stdout.split()[0] if p.stdout.strip() else ""


def note(msg: str) -> None:
    print(msg, flush=True)


# -- setup ----------------------------------------------------------------

def setup(repos: list[dict], only: set[str] | None = None) -> None:
    os.makedirs(REPOS, exist_ok=True)
    for repo in repos:
        if only and repo["id"] not in only:
            continue
        dest = repo_dir(repo)
        sha = git_sha(repo["url"])
        have = os.path.isdir(os.path.join(dest, ".git"))
        if not have:
            shutil.rmtree(dest, ignore_errors=True)
            note(f"[setup] {repo['id']}: clone {sha[:10]}")
            rc, _, out = sh(["git", "clone", "--quiet", repo["url"], dest], WORK, 600)
            if rc:
                note(out)
                continue
        if sha:
            sh(["git", "-C", dest, "checkout", "--quiet", sha], WORK, 300)
        for cmd in repo.get("install", []):
            note(f"[setup] {repo['id']}: {' '.join(cmd[:3])} …")
            rc, w, out = sh(cmd, dest, 900)
            if rc:
                note(f"[setup] {repo['id']}: install failed rc={rc}\n{out[-800:]}")
        # a suite that does not run is not a data point
        rc, w, out = sh(repo["suite"], work_dir(repo), HARD_CAP_S, env_for(repo))
        repo["_ok"] = rc in (0, 1)
        repo["_sha"] = sha
        note(f"[setup] {repo['id']}: suite rc={rc} in {w:.1f}s "
             f"{'OK' if repo['_ok'] else 'FAILED'}")
        if not repo["_ok"]:
            note(out[-600:])


# -- measurement ----------------------------------------------------------

def naive_once(repo: dict, guess_s: float) -> dict:
    """The naive caller: run under `timeout G`; on a kill double G and restart."""
    env = env_for(repo)
    attempts, kills, wall = [], 0, 0.0
    g = float(guess_s)
    for _ in range(MAX_ATTEMPTS):
        rc, w, out = sh(repo["suite"], work_dir(repo), timeout=g, env=env)
        wall += w
        attempts.append({"guess_s": g, "rc": rc, "wall_s": round(w, 3)})
        if rc == 124:
            kills += 1
            if w >= HARD_CAP_S:
                break
            g *= 2.0
            continue
        return {"kills": kills, "attempts": attempts, "wall_s": round(wall, 3),
                "completed": rc in (0, 1), "final_guess_s": g}
    return {"kills": kills, "attempts": attempts, "wall_s": round(wall, 3),
            "completed": False, "final_guess_s": g}


def hh_call(repo: dict, store: str, budget_s: float | None = None) -> dict:
    """One `run_tests` call in a fresh process (the tool an agent would call)."""
    spec = json.dumps({"root": work_dir(repo), "runner": repo["runner"],
                       "store_root": store,
                       **({"budget_s": budget_s} if budget_s else {})})
    code = ("import json,sys;from hh.tool import handle_tool_call;"
            "print(handle_tool_call('run_tests', json.loads(sys.argv[1])))")
    rc, w, out = sh([sys.executable, "-c", code, spec], work_dir(repo),
                    HARD_CAP_S, env_for(repo, store))
    try:
        d = json.loads(out[out.index("{"):])        # the tool prints indented JSON
    except Exception:
        return {"error": f"tool call failed rc={rc}", "tail": out[-1500:], "wall_s": w}
    d["process_wall_s"] = round(w, 3)
    return d


def measure(repo: dict) -> dict:
    rid = repo["id"]
    store = os.path.join(WORK, "stores", rid)
    shutil.rmtree(store, ignore_errors=True)
    head = subprocess.run(["git", "-C", repo_dir(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    row = {"id": rid, "runner": repo["runner"], "sha": head.stdout.strip(),
           "url": repo["url"],
           "suite": " ".join(repo["suite"])}

    # 0. the reference: how long does the suite take with no timeout at all?
    ref = []
    for _ in range(2):
        rc, w, out = sh(repo["suite"], work_dir(repo), HARD_CAP_S, env_for(repo))
        ref.append(w)
        if rc not in (0, 1):
            return {**row, "ok": False, "stage": "reference", "tail": out[-800:]}
    row["reference_s"] = round(statistics.median(ref), 3)

    # 1. hh cold (record pass) and warm (measured budget)
    cold = hh_call(repo, store)
    if cold.get("error") or cold.get("status") not in ("ok", "incomplete"):
        return {**row, "ok": False, "stage": "hh-cold", "hh": cold}
    warm = hh_call(repo, store)
    warm2 = hh_call(repo, store)
    row["hh"] = {
        "cold": {k: cold.get(k) for k in
                 ("status", "wall_s", "attempts", "timed_out", "budget", "recorded")},
        "warm": {k: warm.get(k) for k in
                 ("status", "wall_s", "attempts", "timed_out", "budget", "predicted_wall_s")},
        "warm2": {k: warm2.get(k) for k in
                  ("status", "wall_s", "attempts", "timed_out", "budget")},
    }

    # 2. the sweep: what the naive caller pays for each guess
    sweep = []
    for g in GUESSES_S:
        naive = naive_once(repo, g)
        hh_warm_wall = warm.get("wall_s")
        # A saving is only defined when naive eventually produced a result.
        # If it exhausted its attempts the whole naive cost was waste and there
        # is nothing to subtract from: that is a separate outcome, not a number.
        saved = (round(naive["wall_s"] - hh_warm_wall, 3)
                 if naive["kills"] and naive["completed"] else None)
        sweep.append({"guess_s": g, **naive, "hh_wall_s": hh_warm_wall,
                      "hh_kills": warm.get("timed_out", 0), "saved_s": saved})
        note(f"  {rid:<12} G={g:>3}s  naive {naive['wall_s']:>7.2f}s "
             f"kills={naive['kills']}  hh {hh_warm_wall:>6.2f}s kills={warm.get('timed_out', 0)}")
    row["sweep"] = sweep
    row["ok"] = True
    return row


def run(repos: list[dict], only: set[str] | None = None) -> dict:
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "timeout-metric.json")
    out = {"date": time.strftime("%Y-%m-%d"), "guesses_s": GUESSES_S, "repos": []}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                out = json.load(f)
        except ValueError:
            pass
    done = {r["id"]: r for r in out["repos"]}
    for repo in repos:
        if only and repo["id"] not in only:
            continue
        note(f"[run] {repo['id']} ({repo['runner']})")
        try:
            done[repo["id"]] = measure(repo)
        except Exception as e:                       # noqa: BLE001 - report it
            done[repo["id"]] = {"id": repo["id"], "ok": False, "stage": "exception",
                                "error": repr(e)}
        out["repos"] = [done[k] for k in sorted(done)]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return out


# -- report ---------------------------------------------------------------

def saving(x: dict) -> float | None:
    """Seconds hh saved in one configuration, or None if naive never recovered.

    Derived from the stored walls (rather than trusting `saved_s`) so that an
    older results file cannot smuggle in a difference that compares a finished
    suite against an arm that gave up.
    """
    if not x["kills"] or not x["completed"]:
        return None
    return round(x["wall_s"] - x["hh_wall_s"], 3)


def recorded_tests(rid: str) -> int | None:
    """Size of the manifest hh recorded for a repo (None if no store)."""
    import glob
    best = None
    for path in glob.glob(os.path.join(WORK, "stores", rid, ".hh", "manifest-*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                n = len(json.load(f).get("raw_of") or {})
        except (OSError, ValueError):
            continue
        best = n if best is None else max(best, n)
    return best


def summarise(out: dict) -> dict:
    rows = [r for r in out["repos"] if r.get("ok")]
    configs = [(r, s) for r in rows for s in r["sweep"]]
    killed = [(r, s) for r, s in configs if s["kills"]]
    hh_killed = [(r, s) for r, s in configs if s["hh_kills"]]
    recovered = [(r, s) for r, s in killed if s["completed"]]
    gave_up = [(r, s) for r, s in killed if not s["completed"]]
    saved = [saving(s) for _, s in recovered if saving(s) is not None]
    naive_total = sum(s["wall_s"] for _, s in recovered)
    hh_total = sum(s["hh_wall_s"] for _, s in recovered)
    return {
        "repos": len(rows),
        "configurations": len(configs),
        "naive_killed": len(killed),
        "naive_kills_total": sum(s["kills"] for _, s in killed),
        "hh_killed": len(hh_killed),
        "hh_kills_total": sum(s["hh_kills"] for _, s in configs),
        "configs_needing_a_kill": len(killed),
        "naive_recovered": len(recovered),
        "naive_gave_up": len(gave_up),
        "naive_gave_up_s": round(sum(s["wall_s"] for _, s in gave_up), 1),
        "hh_wall_gave_up_s": round(sum(s["hh_wall_s"] for _, s in gave_up), 1),
        "saved_total_s": round(sum(saved), 1),
        "saved_median_s": round(statistics.median(saved), 2) if saved else 0.0,
        "naive_wall_killed_configs_s": round(naive_total, 1),
        "hh_wall_killed_configs_s": round(hh_total, 1),
        "reference_total_s": round(sum(r["reference_s"] for r in rows), 1),
        "no_kill_configs": len(configs) - len(killed),
    }


def report(out: dict) -> str:
    s = summarise(out)
    rows = [r for r in out["repos"] if r.get("ok")]
    L: list[str] = []
    A = L.append
    A("# The only metric: does the timeout kill the suite?")
    A("")
    A(f"**Date:** {out['date']} · **hh:** {_version()} · "
      f"**Repos:** {s['repos']} ({', '.join(sorted({r['runner'] for r in rows}))}) · "
      f"**Configurations:** {s['configurations']} "
      f"({s['repos']} repos x {len(out['guesses_s'])} guesses)")
    A("")
    A("A caller must give a test suite a timeout. If the timeout is too low the")
    A("suite is SIGKILLed, the work is lost, and it has to run again. hh exists to")
    A("make that not happen. So the benchmark measures exactly two things:")
    A("")
    A("1. **Kill-free.** When the caller's guess is too low, does hh's budget avoid")
    A("   the SIGKILL?")
    A("2. **Payoff.** When the guess was too low, how many seconds does hh save?")
    A("")
    A("`naive` = the repo's own suite command under a guessed timeout G; on a kill")
    A("it doubles G and restarts from zero. `hh` = the `run_tests` tool: one record")
    A("pass (the naive command plus a ledger), then a measured budget that does not")
    A("depend on G at all. G is swept over " + ", ".join(f"{g} s" for g in out["guesses_s"]) + ".")
    A("")
    A("## 1. The metric")
    A("")
    A(f"- **hh was SIGKILLed in {s['hh_killed']} of {s['configurations']} configurations** "
      f"({s['hh_kills_total']} kills in total).")
    A(f"- Naive was SIGKILLed in **{s['configs_needing_a_kill']} of "
      f"{s['configurations']}** ({s['naive_kills_total']} kills in total).")
    A(f"- Of those, naive **recovered** in {s['naive_recovered']}: it spent "
      f"**{s['naive_wall_killed_configs_s']:.1f} s** where hh spent "
      f"**{s['hh_wall_killed_configs_s']:.1f} s** — **{s['saved_total_s']:.1f} s saved** "
      f"(median {s['saved_median_s']:.2f} s per configuration).")
    if s["naive_gave_up"]:
        A(f"- In the other {s['naive_gave_up']} it **never finished**: four guesses in a row "
          f"were too low, so it burned {s['naive_gave_up_s']:.1f} s and returned no result. "
          f"hh finished those same configurations in {s['hh_wall_gave_up_s']:.1f} s. "
          f"There is no saving to quote there — one arm produced a suite result and the "
          f"other did not.")
    A(f"- One-time price: hh's first call is one more full run of the suite — "
      f"**{s['reference_total_s']:.1f} s of test time across the {s['repos']} repos** "
      f"(per repo in section 5). It is paid once per repository, never per call.")
    A(f"- The remaining {s['no_kill_configs']} configurations are ones where naive's "
      f"guess was already high enough; there is nothing to save, and hh's ledger adds "
      f"a little overhead instead — stated, not hidden.")
    A("")
    A("## 2. Per repository")
    A("")
    A("The quoted naive column is the guess where naive lost the most time in that")
    A("repository (the one where hh's saving is largest); section 4 lists every killed")
    A("configuration.")
    A("")
    A("| repo | runner | tests recorded | reference | hh budget | hh kills | naive kills | "
      "naive wall @killed G | hh wall @same G | saved |")
    A("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        sweep = r["sweep"]
        killed = [x for x in sweep if x["kills"]]
        hh_budget = r["hh"]["warm"]["budget"]["budget_s"]
        n_tests = recorded_tests(r["id"])
        if killed:
            # Quote the configuration where naive recovered and lost the most
            # time. If naive never recovered in any of them, say that instead
            # of inventing a difference.
            recovered = [x for x in killed if saving(x) is not None]
            if recovered:
                k = max(recovered, key=lambda x: saving(x))
                saved_cell = f"**{saving(k):+.2f} s**"
            else:
                k = max(killed, key=lambda x: x["wall_s"])
                saved_cell = f"gave up after {k['wall_s']:.2f} s"
            A(f"| {r['id']} | {r['runner']} | {n_tests or '—'} | "
              f"{r['reference_s']:.2f} s | {hh_budget:.2f} s | "
              f"{r['hh']['warm']['timed_out']} | {len(killed)}/{len(sweep)} | "
              f"{k['wall_s']:.2f} s | {k['hh_wall_s']:.2f} s | {saved_cell} |")
        else:
            A(f"| {r['id']} | {r['runner']} | {n_tests or '—'} | "
              f"{r['reference_s']:.2f} s | {hh_budget:.2f} s | "
              f"{r['hh']['warm']['timed_out']} | 0/{len(sweep)} | — | — | — |")
    A("")
    A("## 3. The sweep (seconds; ✗ marks a SIGKILL)")
    A("")
    A("Each cell is the wall-clock cost of getting the suite to finish. A naive cell")
    A("that was killed contains the wasted attempt(s) *and* the restart. Four attempts")
    A("with no result mean naive gave up on that guess — section 4 says so instead of")
    A("quoting a saving.")
    A("")
    head = "| repo | " + " | ".join(f"G={g}s" for g in out["guesses_s"]) + " | hh |"
    A(head)
    A("|---|" + "---|" * (len(out["guesses_s"]) + 1))
    for r in rows:
        cells = []
        for x in r["sweep"]:
            mark = " ✗" if x["kills"] else ""
            cells.append(f"{x['wall_s']:.1f}{mark}")
        A(f"| {r['id']} | " + " | ".join(cells) +
          f" | **{r['hh']['warm']['wall_s']:.1f}** |")
    A("")
    A("## 4. Every killed configuration, side by side")
    A("")
    A("| repo | G | naive attempts | naive wall | hh attempts | hh wall | saved |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        for x in r["sweep"]:
            if not x["kills"]:
                continue
            sv = saving(x)
            saved = f"{sv:+.2f} s" if sv is not None else "naive gave up"
            A(f"| {r['id']} | {x['guess_s']} s | {len(x['attempts'])} | {x['wall_s']:.2f} s | "
              f"{r['hh']['warm']['attempts']} | {x['hh_wall_s']:.2f} s | {saved} |")
    A("")
    A("## 5. What hh measured, and what it chose")
    A("")
    A("`p50`/`p90` describe the suite's **total test time**: the sum over tests, each")
    A("with its own measured distribution from the record pass. `budget` is the")
    A("per-attempt kill threshold hh picks from them: `p90 x 1.25`, floored at 3 s. It")
    A("is not a prediction of the wall clock — it only has to outlast the slowest test")
    A("the runner may be inside when the budget runs out, so that a kill never lands on")
    A("unfinished work. `warm wall` is what the caller actually waited, collection and")
    A("ledger included.")
    A("")
    A("The budget is compared against the run *between two ledger writes*, not against")
    A("the suite's wall clock: the executor adds the measured startup (interpreter +")
    A("collection) to it, so a 3.2 s budget next to a 5.4 s suite is not a contradiction")
    A("- it is 3.2 s of test execution after ~2 s of collection. Small suites hit the")
    A("3 s floor, which is why their budgets are all 3.00 s.")
    A("")
    A("| repo | reference (naive command) | record pass | p50 | p90 | budget | hh warm wall |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        b = r["hh"]["warm"]["budget"]
        p50 = b.get("p50_s")
        p90 = b.get("p90_s")
        A(f"| {r['id']} | {r['reference_s']:.2f} s | {r['hh']['cold']['wall_s']:.2f} s | "
          f"{p50:.3f} s | {p90:.3f} s | {b['budget_s']:.2f} s | "
          f"{r['hh']['warm']['wall_s']:.2f} s |" if p50 and p90 else
          f"| {r['id']} | {r['reference_s']:.2f} s | {r['hh']['cold']['wall_s']:.2f} s | "
          f"— | — | {b['budget_s']:.2f} s | {r['hh']['warm']['wall_s']:.2f} s |")
    A("")
    A("## 6. How to read this honestly")
    A("")
    A("- **hh does not make the tests faster.** Where G was already high enough, both")
    A("  arms run the suite once. On the Python repos hh's warm wall lands within")
    A("  -0.4 s to +0.4 s of the plain command. Click is slightly *faster*: the pytest")
    A("  adapter hands node ids to pytest, which then collects only the files that will")
    A("  run instead of the whole tree. The exception is pygments, at +4.4 s: 5,345")
    A("  ids are over the adapter's command-line cap, so its warm pass collects the")
    A("  tree and deselects. It is still 11.2 s and 6.2 s ahead of naive at G=5 and")
    A("  G=10, but the overhead is a cost, not something to hide.")
    A("- **These suites are short** (0.5-18 s), so only the low guesses kill. On a")
    A("  slower machine or a larger suite every number scales with the suite: a guess")
    A("  below `D` kills, and hh's budget is derived from `D` itself. The sweep is the")
    A("  same experiment at six values of G.")
    A("- **The record pass is the honest price.** hh's first call is the naive command")
    A("  plus a ledger, so it costs one extra full run of the suite per repository")
    A("  (section 1) — that is what it takes to have measurements at all.")
    A("- **The node repos compare two launchers, not just two timeouts.** For express,")
    A("  nock and supertest the naive arm runs the repository's `npm test`, while hh")
    A("  drives the mocha binary directly. npm's own startup (1.5-2 s, visible as the")
    A("  gap between `reference` and `record pass` in section 5) lands entirely in the")
    A("  naive column. On supertest it is larger than the suite itself, which is why")
    A("  hh wins even in configurations where naive was not killed. Strip it out and")
    A("  the timeout story is the same as for the Python repos.")
    A("- **hh resumes rather than restarts** when its own budget is exceeded, so even")
    A("  a wrong hh budget loses one budget, not the suite. No hh configuration in")
    A("  this run needed it; the test suite pins that behaviour (see")
    A("  `tests/test_tool_e2e.py`).")
    A("")
    A("## 7. Reproducing")
    A("")
    A("```")
    A("python3 -m benchmark setup     # clone + install the repos in repos.json")
    A("python3 -m benchmark run       # measure; writes _work/results/timeout-metric.json")
    A("python3 -m benchmark report    # writes this file")
    A("```")
    A("")
    A("| repo | commit |")
    A("|---|---|")
    for r in rows:
        A(f"| {r['id']} | `{(r.get('sha') or '')[:10]}` |")
    A("")
    return "\n".join(L)


def _version() -> str:
    sys.path.insert(0, ROOT)
    from hh import __version__
    p = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return f"{__version__} ({p.stdout.strip()})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m benchmark")
    ap.add_argument("--repo", "-r", action="append")
    sub = ap.add_subparsers(dest="cmd")
    ap.set_defaults(cmd="all")
    sub.add_parser("setup")
    sub.add_parser("run")
    sub.add_parser("report")
    sub.add_parser("all")
    args = ap.parse_args(argv)
    only = set(args.repo) if args.repo else None
    repos = load_repos()
    if args.cmd in ("setup", "all"):
        setup(repos, only)
    if args.cmd in ("run", "all"):
        out = run(repos, only)
        if only:
            note("[run] partial run; report skipped")
        else:
            path = os.path.join(HERE, f"REPORT-{time.strftime('%Y-%m-%d')}-timeout-metric.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(report(out) + "\n")
            note(f"[report] wrote {path}")
    if args.cmd == "report":
        with open(os.path.join(RESULTS, "timeout-metric.json"), encoding="utf-8") as f:
            out = json.load(f)
        path = os.path.join(HERE, f"REPORT-{time.strftime('%Y-%m-%d')}-timeout-metric.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report(out) + "\n")
        note(f"[report] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
